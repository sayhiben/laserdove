# hardware/ruida.py
from __future__ import annotations

import logging
import math
import os
from typing import Optional

from .base import LaserInterface

log = logging.getLogger(__name__)


class RuidaLaser(LaserInterface):
    """
    Minimal USB-serial Ruida transport using swizzle magic 0x88 (644xG).
    Supports immediate power, speed, absolute move, and absolute cut.
    USB has no ACK; enable dry_run while validating on scrap.
    """

    def __init__(
        self,
        host: str,
        port: int = 50200,
        *,
        serial_port: Optional[str] = None,
        baud: int = 19200,
        timeout_s: float = 0.25,
        dry_run: bool = False,
    ) -> None:
        self.host = host
        self.port = port  # kept for compatibility; unused for USB
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0
        self._last_speed_ums: Optional[int] = None
        self._serial = None
        self._baud = baud
        self._timeout_s = timeout_s
        env_port = os.getenv("RUIDA_SERIAL_PORT")
        self._serial_port = (
            serial_port
            or env_port
            or (host if host.startswith("/") else "/dev/ttyUSB0")
        )
        self._dry_run = dry_run
        log.info(
            "RuidaLaser initialized for USB serial=%s baud=%d dry_run=%s",
            self._serial_port,
            baud,
            dry_run,
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
        return bytes([self._swizzle_byte(b) for b in payload])

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

    def _ensure_serial(self) -> None:
        if self._dry_run:
            return
        if self._serial is not None:
            return
        if not os.path.exists(self._serial_port):
            log.warning("Ruida serial port %s not found; switching to dry_run", self._serial_port)
            self._dry_run = True
            return
        try:
            import serial  # type: ignore
            from serial.serialutil import SerialException  # type: ignore
        except ImportError as e:
            raise RuntimeError("pyserial is required for RuidaLaser over USB") from e
        try:
            self._serial = serial.Serial(
                self._serial_port,
                self._baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self._timeout_s,
                rtscts=True,
                dsrdtr=True,
            )
        except Exception as e:
            raise RuntimeError(f"Failed to open Ruida serial port {self._serial_port}") from e

    def _send(self, payload: bytes) -> None:
        swizzled = self._swizzle(payload)
        if self._dry_run:
            log.info("[RUDA USB DRY] %s", swizzled.hex(" "))
            return
        self._ensure_serial()
        if self._dry_run:
            log.info("[RUDA USB DRY] %s", swizzled.hex(" "))
            return
        if self._serial is None:
            raise RuntimeError("Serial interface not available")
        self._serial.write(swizzled)

    def _set_speed(self, speed_mm_s: float) -> None:
        speed_ums = int(round(speed_mm_s * 1000.0))
        if self._last_speed_ums == speed_ums:
            return
        self._last_speed_ums = speed_ums
        payload = bytes([0xC9, 0x02]) + self._encode_abscoord_mm(speed_mm_s)
        log.info("[RUDA USB] SET_SPEED %.3f mm/s", speed_mm_s)
        self._send(payload)

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        # Safety: travel with laser off.
        if self.power != 0.0:
            self.set_laser_power(0.0)
        if speed is not None:
            self._set_speed(speed)
        if x is None and y is None:
            return
        x_mm = self.x if x is None else x
        y_mm = self.y if y is None else y
        payload = bytes([0x88]) + self._encode_abscoord_mm(x_mm) + self._encode_abscoord_mm(y_mm)
        log.info("[RUDA USB] MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)
        self._send(payload)

    def cut_line(self, x, y, speed) -> None:
        self.x = x
        self.y = y
        if speed is not None:
            self._set_speed(speed)
        payload = bytes([0xA8]) + self._encode_abscoord_mm(x) + self._encode_abscoord_mm(y)
        log.info("[RUDA USB] CUT_LINE x=%.3f y=%.3f speed=%.3f power=%.1f%%",
                 x, y, speed, self.power)
        self._send(payload)

    def set_laser_power(self, power_pct) -> None:
        if math.isclose(self.power, power_pct, abs_tol=1e-6):
            return
        self.power = power_pct
        payload = bytes([0xC7]) + self._encode_power_pct(power_pct)
        log.info("[RUDA USB] SET_LASER_POWER %.1f%%", power_pct)
        self._send(payload)
