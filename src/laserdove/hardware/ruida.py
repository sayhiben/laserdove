# hardware/ruida.py
from __future__ import annotations

import logging
import math
import socket
import struct
import time
from typing import Iterable, List, NamedTuple, Optional

from .base import LaserInterface

log = logging.getLogger(__name__)


class RuidaLaser(LaserInterface):
    """
    UDP-based Ruida transport (port 50200) using swizzle magic 0x88.
    Sends swizzled payloads with 16-bit checksum and waits for ACK (0xC6)
    per packet. Packets are chunked to <= MTU.
    """

    # Some references report ACK/NACK as 0xCC/0xCF; others as 0xC6/0x46.
    ACK = 0xC6
    NACK = 0x46
    ACK_VALUES = {ACK, 0xCC}
    NACK_VALUES = {NACK, 0xCF}
    MTU = 1470
    MEM_MACHINE_STATUS = b"\x04\x00"
    MEM_CURRENT_X = b"\x04\x21"
    MEM_CURRENT_Y = b"\x04\x31"

    STATUS_BIT_MOVING = 0x01000000
    STATUS_BIT_PART_END = 0x00000002
    STATUS_BIT_JOB_RUNNING = 0x00000001

    BUSY_MASK = STATUS_BIT_MOVING | STATUS_BIT_JOB_RUNNING | STATUS_BIT_PART_END

    class MachineState(NamedTuple):
        status_bits: int
        x_mm: Optional[float]
        y_mm: Optional[float]

    def __init__(
        self,
        host: str,
        port: int = 50200,
        *,
        source_port: int = 40200,
        timeout_s: float = 3.0,
        dry_run: bool = False,
        magic: int = 0x88,
        movement_only: bool = False,
        socket_factory=socket.socket,
    ) -> None:
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.magic = magic
        self.movement_only = movement_only
        self.sock: Optional[socket.socket] = None
        self._socket_factory = socket_factory
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0
        self._last_speed_ums: Optional[int] = None
        self._movement_only_power_sent = False
        self._last_requested_power = 0.0
        log.info(
            "RuidaLaser initialized for UDP host=%s port=%d dry_run=%s movement_only=%s",
            host,
            port,
            dry_run,
            movement_only,
        )

    # ---------------- Swizzle/encoding helpers ----------------
    @staticmethod
    def _swizzle_byte(b: int, magic: int = 0x88) -> int:
        b ^= (b >> 7) & 0xFF
        b ^= (b << 7) & 0xFF
        b ^= (b >> 7) & 0xFF
        b ^= magic
        b = (b + 1) & 0xFF
        return b

    def _swizzle(self, payload: bytes) -> bytes:
        return bytes([self._swizzle_byte(b, self.magic) for b in payload])

    @staticmethod
    def _unswizzle_byte(b: int, magic: int = 0x88) -> int:
        b = (b - 1) & 0xFF
        b ^= magic
        fb = b & 0x80
        lb = b & 0x01
        b = (b - fb - lb) & 0xFF
        b |= (lb << 7)
        b |= (fb >> 7)
        return b

    def _unswizzle(self, payload: bytes) -> bytes:
        return bytes([self._unswizzle_byte(b, self.magic) for b in payload])

    @staticmethod
    def _encode_abscoord_mm(value_mm: float) -> bytes:
        microns = int(round(value_mm * 1000.0))
        res = []
        for _ in range(5):
            res.append(microns & 0x7F)
            microns >>= 7
        res.reverse()
        return bytes(res)

    @staticmethod
    def _encode_power_pct(power_pct: float) -> bytes:
        clamped = max(0.0, min(100.0, power_pct))
        raw = int(round(clamped * (0x3FFF / 100.0)))
        return bytes([(raw >> 7) & 0x7F, raw & 0x7F])

    def _checksum(self, data: bytes) -> bytes:
        cs = sum(data) & 0xFFFF
        return struct.pack(">H", cs)

    def _decode_abscoord_mm(self, payload: bytes) -> float:
        microns = 0
        for b in payload:
            microns = (microns << 7) | b
        return microns / 1000.0

    def _decode_status_bits(self, payload: bytes) -> int:
        return int.from_bytes(payload[:4], byteorder="big", signed=False)

    def _ensure_socket(self) -> None:
        if self.dry_run:
            return
        if self.sock is not None:
            return
        self.sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout_s)
        try:
            self.sock.bind(("", self.source_port))
        except PermissionError:
            log.warning("Falling back to ephemeral source port for Ruida UDP (bind failed)")
            try:
                self.sock.bind(("", 0))
            except PermissionError:
                log.error("Unable to bind UDP socket; switching to dry_run for safety")
                self.dry_run = True
                self.sock = None

    def _send_packets(self, payload: bytes, *, expect_reply: bool = False) -> Optional[bytes]:
        """
        Swizzle, chunk, prepend checksum, and send with ACK wait. Optionally collect a follow-on reply packet
        (e.g., GET_SETTING responses) and return its unswizzled payload.
        """
        swizzled = self._swizzle(payload)
        if self.dry_run:
            log.info("[RUDA UDP DRY] %s", swizzled.hex(" "))
            return None
        self._ensure_socket()
        if self.sock is None:
            log.info("[RUDA UDP DRY] %s", swizzled.hex(" "))
            return None

        # Chunk
        chunks: List[bytes] = []
        start = 0
        while start < len(swizzled):
            end = min(start + self.MTU, len(swizzled))
            chunk = swizzled[start:end]
            chunk = self._checksum(chunk) + chunk
            chunks.append(chunk)
            start = end

        for idx, chunk in enumerate(chunks):
            retry = 0
            while True:
                self.sock.sendto(chunk, (self.host, self.port))
                try:
                    data, _ = self.sock.recvfrom(8)
                except socket.timeout:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP ACK timeout")
                    continue
                if not data:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP empty response")
                    continue
                if data[0] in self.ACK_VALUES:
                    break
                if data[0] in self.NACK_VALUES and idx == 0:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP NACK on first packet")
                    continue
                raise RuntimeError(f"UDP unexpected response {data.hex()}")

        if not expect_reply:
            return None

        try:
            reply, _ = self.sock.recvfrom(self.MTU)
        except socket.timeout:
            raise RuntimeError("UDP reply timeout")

        payload_only = reply
        if len(reply) > 2:
            maybe_checksum = reply[:2]
            maybe_payload = reply[2:]
            if maybe_checksum == self._checksum(maybe_payload):
                payload_only = maybe_payload

        unswizzled = self._unswizzle(payload_only)
        if not unswizzled and reply:
            unswizzled = reply
        return unswizzled

    def _get_memory_value(self, address: bytes, *, expected_len: int) -> Optional[bytes]:
        payload = bytes([0xDA, 0x00]) + address
        reply = self._send_packets(payload, expect_reply=True)
        if reply is None:
            return None

        if reply.startswith(b"\xDA\x01" + address):
            data = reply[4:]
        elif reply.startswith(address):
            data = reply[2:]
        else:
            log.warning("[RUDA UDP] Unexpected reply %s for address %s", reply.hex(" "), address.hex(" "))
            return None

        if len(data) < expected_len:
            log.warning("[RUDA UDP] Truncated reply for %s: %s", address.hex(" "), data.hex(" "))
            return None
        return data[:expected_len]

    def _read_machine_state(self) -> Optional[MachineState]:
        try:
            status_payload = self._get_memory_value(self.MEM_MACHINE_STATUS, expected_len=4)
            x_payload = self._get_memory_value(self.MEM_CURRENT_X, expected_len=5)
            y_payload = self._get_memory_value(self.MEM_CURRENT_Y, expected_len=5)
        except RuntimeError as exc:
            log.warning("[RUDA UDP] Failed to poll machine state: %s", exc)
            return None

        if status_payload is None:
            return None

        status_bits = self._decode_status_bits(status_payload)
        x_mm = self._decode_abscoord_mm(x_payload) if x_payload else None
        y_mm = self._decode_abscoord_mm(y_payload) if y_payload else None
        return self.MachineState(status_bits=status_bits, x_mm=x_mm, y_mm=y_mm)

    def _wait_for_ready(self, *, max_attempts: int = 5, delay_s: float = 0.5) -> MachineState:
        if self.dry_run:
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y)

        last_state: Optional[RuidaLaser.MachineState] = None
        for attempt in range(1, max_attempts + 1):
            state = self._read_machine_state()
            if state:
                last_state = state
                if not (state.status_bits & self.BUSY_MASK):
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    log.debug("[RUDA UDP] Ready on attempt %d: status=0x%08X x=%.3f y=%.3f", attempt, state.status_bits, self.x, self.y)
                    return state
            log.debug("[RUDA UDP] Busy state (attempt %d/%d); sleeping %.2fs", attempt, max_attempts, delay_s)
            time.sleep(delay_s)

        raise RuntimeError(f"Ruida controller not ready after {max_attempts} attempts (last={last_state})")

    def _set_speed(self, speed_mm_s: float) -> None:
        speed_ums = int(round(speed_mm_s * 1000.0))
        if self._last_speed_ums == speed_ums:
            return
        self._last_speed_ums = speed_ums
        payload = bytes([0xC9, 0x02]) + self._encode_abscoord_mm(speed_mm_s)
        log.info("[RUDA UDP] SET_SPEED %.3f mm/s", speed_mm_s)
        self._send_packets(payload)

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        self._wait_for_ready()
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        if self.power != 0.0:
            self.set_laser_power(0.0)
        if speed is not None:
            self._set_speed(speed)
        if x is None and y is None:
            return
        x_mm = self.x if x is None else x
        y_mm = self.y if y is None else y
        payload = bytes([0x88]) + self._encode_abscoord_mm(x_mm) + self._encode_abscoord_mm(y_mm)
        log.info("[RUDA UDP] MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)
        self._send_packets(payload)

    def cut_line(self, x, y, speed) -> None:
        self._wait_for_ready()
        self.x = x
        self.y = y
        if speed is not None:
            self._set_speed(speed)
        payload = bytes([0xA8]) + self._encode_abscoord_mm(x) + self._encode_abscoord_mm(y)
        log.info("[RUDA UDP] CUT_LINE x=%.3f y=%.3f speed=%.3f power=%.1f%%",
                 x, y, speed, self.power)
        self._send_packets(payload)

    def set_laser_power(self, power_pct) -> None:
        self._wait_for_ready()
        requested_power = 0.0 if power_pct is None else power_pct
        self._last_requested_power = requested_power

        if self.movement_only:
            log.info(
                "[RUDA UDP] movement-only: requested laser power %.1f%% (suppressed)",
                requested_power,
            )
            if self._movement_only_power_sent:
                log.debug("[RUDA UDP] movement-only: suppressing laser power change")
                return
            log.info("[RUDA UDP] movement-only: sending single laser-off command")
            self.power = 0.0
            self._movement_only_power_sent = True
            payload = bytes([0xC7]) + self._encode_power_pct(0.0)
            self._send_packets(payload)
            return

        if math.isclose(self.power, requested_power, abs_tol=1e-6):
            return

        self.power = requested_power
        payload = bytes([0xC7]) + self._encode_power_pct(requested_power)
        log.info("[RUDA UDP] SET_LASER_POWER %.1f%%", requested_power)
        self._send_packets(payload)


class RuidaPanelInterface:
    """
    Lightweight helper for the unswizzled panel/“interface” port (UDP 50207).
    Useful for sanity-check jogging without sending full RD commands.
    """

    ACK = 0xCC
    PORT = 50207
    SRC_PORT = 40207

    CMD_STOP = b"\xA5\x50\x09"
    CMD_ORIGIN = b"\xA5\x50\x08"
    CMD_FRAME = b"\xA5\x53\x00"
    CMD_Y_DOWN = b"\xA5\x50\x03"
    CMD_Y_UP = b"\xA5\x51\x03"
    CMD_Z_DOWN = b"\xA5\x50\x0A"
    CMD_Z_UP = b"\xA5\x51\x0A"

    def __init__(
        self,
        host: str,
        port: int = PORT,
        *,
        source_port: int = SRC_PORT,
        timeout_s: float = 2.0,
        socket_factory=socket.socket,
        dry_run: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self._socket_factory = socket_factory
        self.sock: Optional[socket.socket] = None
        self.dry_run = dry_run

    def _ensure_socket(self) -> None:
        if self.dry_run:
            return
        if self.sock is not None:
            return
        self.sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout_s)
        try:
            self.sock.bind(("", self.source_port))
        except Exception as e:
            log.warning("Panel UDP bind failed (%s); falling back to ephemeral port", e)
            try:
                self.sock.bind(("", 0))
            except Exception:
                self.dry_run = True
                self.sock = None

    def send_command(self, cmd: bytes) -> None:
        """
        Send an unswizzled panel command and expect a single-byte ACK (0xCC).
        """
        if self.dry_run:
            log.info("[RUDA PANEL DRY] %s", cmd.hex(" "))
            return
        self._ensure_socket()
        if self.sock is None:
            log.info("[RUDA PANEL DRY] %s", cmd.hex(" "))
            return
        payload = cmd
        self.sock.sendto(payload, (self.host, self.port))
        try:
            data, _ = self.sock.recvfrom(8)
            if data and data[0] != self.ACK:
                log.warning("[RUDA PANEL] Unexpected response %s", data.hex(" "))
        except socket.timeout:
            log.warning("[RUDA PANEL] ACK timeout for command %s", cmd.hex(" "))
