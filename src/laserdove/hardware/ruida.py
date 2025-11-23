# hardware/ruida.py
from __future__ import annotations

import logging
import math
import socket
import struct
import time
from typing import Optional, Iterable, List

from .base import LaserInterface

log = logging.getLogger(__name__)


class RuidaLaser(LaserInterface):
    """
    UDP-based Ruida transport (port 50200) using swizzle magic 0x88.
    Sends swizzled payloads with 16-bit checksum and waits for ACK (0xC6)
    per packet. Packets are chunked to <= MTU.
    """

    ACK = 0xC6
    NACK = 0x46
    MTU = 1470

    def __init__(
        self,
        host: str,
        port: int = 50200,
        *,
        source_port: int = 40200,
        timeout_s: float = 3.0,
        dry_run: bool = False,
        magic: int = 0x88,
        socket_factory=socket.socket,
    ) -> None:
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.magic = magic
        self.sock: Optional[socket.socket] = None
        self._socket_factory = socket_factory
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0
        self._last_speed_ums: Optional[int] = None
        log.info("RuidaLaser initialized for UDP host=%s port=%d dry_run=%s", host, port, dry_run)

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

    def _send_packets(self, payload: bytes) -> None:
        """
        Swizzle, chunk, prepend checksum, and send with ACK wait.
        """
        swizzled = self._swizzle(payload)
        if self.dry_run:
            log.info("[RUDA UDP DRY] %s", swizzled.hex(" "))
            return
        self._ensure_socket()
        if self.sock is None:
            log.info("[RUDA UDP DRY] %s", swizzled.hex(" "))
            return

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
                if data[0] == self.ACK:
                    break
                if data[0] == self.NACK and idx == 0:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP NACK on first packet")
                    continue
                raise RuntimeError(f"UDP unexpected response {data.hex()}")

    def _set_speed(self, speed_mm_s: float) -> None:
        speed_ums = int(round(speed_mm_s * 1000.0))
        if self._last_speed_ums == speed_ums:
            return
        self._last_speed_ums = speed_ums
        payload = bytes([0xC9, 0x02]) + self._encode_abscoord_mm(speed_mm_s)
        log.info("[RUDA UDP] SET_SPEED %.3f mm/s", speed_mm_s)
        self._send_packets(payload)

    def move(self, x=None, y=None, z=None, speed=None) -> None:
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
        self.x = x
        self.y = y
        if speed is not None:
            self._set_speed(speed)
        payload = bytes([0xA8]) + self._encode_abscoord_mm(x) + self._encode_abscoord_mm(y)
        log.info("[RUDA UDP] CUT_LINE x=%.3f y=%.3f speed=%.3f power=%.1f%%",
                 x, y, speed, self.power)
        self._send_packets(payload)

    def set_laser_power(self, power_pct) -> None:
        if math.isclose(self.power, power_pct, abs_tol=1e-6):
            return
        self.power = power_pct
        payload = bytes([0xC7]) + self._encode_power_pct(power_pct)
        log.info("[RUDA UDP] SET_LASER_POWER %.1f%%", power_pct)
        self._send_packets(payload)
