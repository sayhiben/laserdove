from __future__ import annotations

import logging
import math
import socket
import time
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional

from .rd_builder import build_rd_job, RDMove
from .ruida_common import (
    checksum,
    clamp_power,
    decode_abscoord_mm,
    decode_status_bits,
    encode_abscoord_mm,
    encode_power_pct,
    swizzle,
    unswizzle,
    should_force_speed,
)
from ..model import CommandType

log = logging.getLogger(__name__)


class RuidaLaser:
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

    @staticmethod
    def _swizzle_byte(b: int, magic: int = 0x88) -> int:
        # Thin wrappers kept for backward compatibility in tests.
        from .ruida_common import swizzle_byte

        return swizzle_byte(b, magic)

    @staticmethod
    def _swizzle(payload: bytes, magic: int = 0x88) -> bytes:
        return swizzle(payload, magic=magic)

    @staticmethod
    def _unswizzle_byte(b: int, magic: int = 0x88) -> int:
        from .ruida_common import unswizzle_byte

        return unswizzle_byte(b, magic)

    @staticmethod
    def _unswizzle(payload: bytes, magic: int = 0x88) -> bytes:
        return unswizzle(payload, magic=magic)

    @staticmethod
    def _encode_abscoord_mm(value_mm: float) -> bytes:
        return encode_abscoord_mm(value_mm)

    @staticmethod
    def _encode_power_pct(power_pct: float) -> bytes:
        return encode_power_pct(power_pct)

    def _checksum(self, data: bytes) -> bytes:
        return checksum(data)

    def _decode_abscoord_mm(self, payload: bytes) -> float:
        return decode_abscoord_mm(payload)

    def _decode_status_bits(self, payload: bytes) -> int:
        return decode_status_bits(payload)

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
        save_rd_dir: Path | None = None,
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
        self.save_rd_dir = Path(save_rd_dir) if save_rd_dir else None
        self._rd_job_counter = 0
        log.info(
            "RuidaLaser initialized for UDP host=%s port=%d dry_run=%s movement_only=%s",
            host,
            port,
            dry_run,
            movement_only,
        )

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
        swizzled = self._swizzle(payload, magic=self.magic)
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

        unswizzled = self._unswizzle(payload_only, magic=self.magic)
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
        speed_ums, changed = should_force_speed(self._last_speed_ums, speed_mm_s)
        if not changed:
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
        requested_power, should_update = clamp_power(power_pct, self.power)
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

        if not should_update:
            return

        self.power = requested_power
        payload = bytes([0xC7]) + self._encode_power_pct(requested_power)
        log.info("[RUDA UDP] SET_LASER_POWER %.1f%%", requested_power)
        self._send_packets(payload)

    # ---------------- RD job upload/run helpers ----------------
    def send_rd_job(self, moves: List[RDMove], job_z_mm: float | None = None) -> None:
        """
        Build a minimal RD job and send it over UDP 50200. Auto-runs on receipt.
        """
        if not moves:
            return
        if self.movement_only:
            for mv in moves:
                mv.power_pct = 0.0
        payload = build_rd_job(moves, job_z_mm=job_z_mm)
        if self.save_rd_dir:
            self.save_rd_dir.mkdir(parents=True, exist_ok=True)
            self._rd_job_counter += 1
            filename = f"job_{self._rd_job_counter:03d}"
            if job_z_mm is not None:
                filename += f"_z{job_z_mm:.3f}"
            path = self.save_rd_dir / f"{filename}.rd"
            swizzled = self._swizzle(payload, magic=self.magic)
            path.write_bytes(swizzled)
            log.info("[RUDA UDP] Saved RD job to %s", path)
        log.info("[RUDA UDP] Uploading RD job with %d moves%s",
                 len(moves), f" z={job_z_mm:.3f}" if job_z_mm is not None else "")
        if self.dry_run:
            log.debug("[RUDA UDP DRY RD] %s", payload.hex(" "))
        self._send_packets(payload)
        # Wait for completion
        self._wait_for_ready()

    def run_sequence_with_rotary(self, commands: Iterable, rotary) -> None:
        """
        Partition commands at ROTATE boundaries; send each laser block as an RD job;
        run rotary moves via provided rotary interface in between.
        """
        current_power = 0.0
        current_speed: float | None = None
        cursor_x = 0.0
        cursor_y = 0.0
        current_z: float | None = None

        def flush_block(block_moves: List[RDMove], block_z: float | None) -> None:
            if not block_moves:
                return
            self.send_rd_job(block_moves, job_z_mm=block_z)

        block: List[RDMove] = []
        block_z: float | None = None

        for cmd in commands:
            if cmd.type.name == "ROTATE":
                flush_block(block, block_z)
                block = []
                block_z = None
                rotary.rotate_to(cmd.angle_deg, cmd.speed_mm_s or 0.0)
                continue

            if cmd.type.name == "SET_LASER_POWER":
                if cmd.power_pct is not None:
                    current_power = cmd.power_pct
                continue

            if cmd.type.name == "MOVE":
                x = cursor_x if cmd.x is None else cmd.x
                y = cursor_y if cmd.y is None else cmd.y
                if cmd.z is not None:
                    if block_z is not None and not math.isclose(cmd.z, block_z, abs_tol=1e-6) and block:
                        flush_block(block, block_z)
                        block = []
                    current_z = cmd.z
                    block_z = current_z
                if cmd.speed_mm_s is not None:
                    current_speed = cmd.speed_mm_s
                if current_speed is None:
                    continue
                block.append(RDMove(x_mm=x, y_mm=y, speed_mm_s=current_speed,
                                    power_pct=current_power, is_cut=False))
                cursor_x, cursor_y = x, y
                continue

            if cmd.type.name == "CUT_LINE":
                x = cursor_x if cmd.x is None else cmd.x
                y = cursor_y if cmd.y is None else cmd.y
                if cmd.z is not None:
                    if block_z is not None and not math.isclose(cmd.z, block_z, abs_tol=1e-6) and block:
                        flush_block(block, block_z)
                        block = []
                    current_z = cmd.z
                    block_z = current_z
                if cmd.speed_mm_s is not None:
                    current_speed = cmd.speed_mm_s
                if current_speed is None:
                    continue
                block.append(RDMove(x_mm=x, y_mm=y, speed_mm_s=current_speed,
                                    power_pct=current_power, is_cut=True))
                cursor_x, cursor_y = x, y
                continue

        flush_block(block, block_z)

    def cleanup(self) -> None:
        """Release UDP socket if open."""
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                log.debug("Failed to close Ruida socket", exc_info=True)
            self.sock = None
