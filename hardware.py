# hardware.py
from __future__ import annotations

import logging
import math
import time
import os
from abc import ABC, abstractmethod
from typing import Iterable, Callable, Dict, List, Optional

from model import Command, CommandType
from simulation_viewer import SimulationViewer

log = logging.getLogger(__name__)


class LaserInterface(ABC):
    @abstractmethod
    def move(self, x=None, y=None, z=None, speed=None) -> None:
        ...

    @abstractmethod
    def cut_line(self, x, y, speed) -> None:
        ...

    @abstractmethod
    def set_laser_power(self, power_pct) -> None:
        ...


class RotaryInterface(ABC):
    @abstractmethod
    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        ...


class DummyLaser(LaserInterface):
    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        log.info("MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)

    def cut_line(self, x, y, speed) -> None:
        self.x = x
        self.y = y
        log.info("CUT_LINE x=%.3f y=%.3f speed=%.3f", x, y, speed)

    def set_laser_power(self, power_pct) -> None:
        self.power = power_pct
        log.info("SET_LASER_POWER %.1f%%", power_pct)


class DummyRotary(RotaryInterface):
    def __init__(self) -> None:
        self.angle = 0.0

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        log.info("ROTATE to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        self.angle = angle_deg
        time.sleep(0.0)


class SimulatedLaser(LaserInterface):
    """
    Collects executed moves/cuts and can render them on a Tkinter canvas.
    Rendering is delegated to SimulationViewer to keep this class focused on state.
    """

    def __init__(self, real_time: bool = False, time_scale: float = 1.0) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power_pct = 0.0
        self.rotation_deg = 0.0
        self.current_board = "tail"
        self.segments: List[Dict[str, float | bool]] = []
        self.real_time = real_time
        self.time_scale = time_scale
        self.viewer: Optional[SimulationViewer] = None

    def set_rotation(self, rotation_deg: float) -> None:
        self.rotation_deg = rotation_deg
        self.current_board = "pin"
        if self.viewer is not None:
            self.viewer.update(self.segments, self.rotation_deg)

    def _record_segment(self, new_x: float, new_y: float, is_cut: bool) -> None:
        if new_x == self.x and new_y == self.y:
            return
        self.segments.append(
            {
                "x0": self.x,
                "y0": self.y,
                "x1": new_x,
                "y1": new_y,
                "is_cut": is_cut,
                "rotation_deg": self.rotation_deg,
                "z": self.z,
                "board": self.current_board,
            }
        )
        if self.viewer is not None:
            self.viewer.update(self.segments, self.rotation_deg)

    def _sleep_for_motion(self, distance_mm: float, speed: float | None) -> None:
        if not self.real_time:
            return
        if speed is None or speed <= 0 or self.time_scale <= 0:
            return
        duration = distance_mm / speed / self.time_scale
        if duration > 0:
            time.sleep(duration)

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        new_x = self.x if x is None else x
        new_y = self.y if y is None else y
        if z is not None:
            self.z = z
        if new_x != self.x or new_y != self.y:
            self._record_segment(new_x, new_y, is_cut=False)
            distance = math.hypot(new_x - self.x, new_y - self.y)
            self._sleep_for_motion(distance, speed)
        self.x = new_x
        self.y = new_y
        log.info("MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)

    def cut_line(self, x, y, speed) -> None:
        is_cut = self.power_pct > 0.0
        distance = math.hypot(x - self.x, y - self.y)
        self._record_segment(x, y, is_cut=is_cut)
        self._sleep_for_motion(distance, speed)
        self.x = x
        self.y = y
        log.info("CUT_LINE x=%.3f y=%.3f speed=%.3f", x, y, speed)

    def set_laser_power(self, power_pct) -> None:
        self.power_pct = power_pct
        log.info("SET_LASER_POWER %.1f%%", power_pct)

    def setup_viewer(self) -> None:
        """Prepare the Tk canvas without blocking main thread."""
        if self.viewer is None:
            self.viewer = SimulationViewer()
        self.viewer.open()
        self.viewer.render(self.segments, self.rotation_deg)
        self.viewer.update(self.segments, self.rotation_deg)

    def show(self) -> None:
        if not self.segments and self.viewer is None:
            log.info("No segments to visualize.")
            return
        self.setup_viewer()
        if self.viewer is None:
            return
        self.viewer.mainloop(self.segments, self.rotation_deg)


class SimulatedRotary(RotaryInterface):
    def __init__(
        self,
        visualizer: SimulatedLaser | None = None,
        real_time: bool = False,
        time_scale: float = 1.0,
    ) -> None:
        self.angle = 0.0
        self.visualizer = visualizer
        self.real_time = real_time
        self.time_scale = time_scale

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        log.info("[SIM ROTARY] rotate_to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        delta_angle = abs(angle_deg - self.angle)
        self.angle = angle_deg
        if self.visualizer is not None:
            self.visualizer.set_rotation(angle_deg)
            if self.visualizer.viewer is not None:
                self.visualizer.viewer.update(self.visualizer.segments, angle_deg)
        if self.real_time and speed_dps > 0 and self.time_scale > 0:
            duration = delta_angle / speed_dps / self.time_scale
            if duration > 0:
                time.sleep(duration)


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


class RealRotary(RotaryInterface):
    """
    Skeleton implementation for the physical rotary on the Pi.

    v1: Logs requested angles. Replace method body with calls into
    your stepper driver / GPIO code (e.g., CL57T + 23HS45: 200 steps/rev,
    microstep per driver DIP).
    """

    def __init__(self, steps_per_rev: float | None = 200.0, microsteps: int | None = None) -> None:
        # 23HS45 datasheet: 200 full steps/rev; CL57T driver sets microsteps via DIP.
        self.angle = 0.0
        self.steps_per_rev = steps_per_rev
        self.microsteps = microsteps
        log.info(
            "RealRotary initialized (steps_per_rev=%s microsteps=%s)",
            steps_per_rev,
            microsteps,
        )
        # Hook for real GPIO/driver wiring if provided by caller.

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        log.info("[ROTARY] rotate_to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        self.angle = angle_deg
        if self.steps_per_rev:
            steps = (angle_deg / 360.0) * self.steps_per_rev
            micro = self.microsteps or 1
            steps *= micro
            log.debug("Computed step target: %.2f steps (microsteps=%s)", steps, micro)
        # Real motor driving would be wired here (pulse DIR/STEP to CL57T or similar).
        time.sleep(0.0)


def execute_commands(
    commands: Iterable[Command],
    laser: LaserInterface,
    rotary: RotaryInterface,
) -> None:
    """
    Interpret Command objects and call the appropriate laser/rotary methods.

    Dispatch is table-driven for clarity.
    """

    def handle_move(command: Command) -> None:
        laser.move(x=command.x, y=command.y, z=command.z, speed=command.speed_mm_s)

    def handle_cut_line(command: Command) -> None:
        if command.speed_mm_s is None:
            raise ValueError("CUT_LINE without speed_mm_s")
        laser.cut_line(x=command.x, y=command.y, speed=command.speed_mm_s)

    def handle_set_laser_power(command: Command) -> None:
        if command.power_pct is None:
            raise ValueError("SET_LASER_POWER without power_pct")
        laser.set_laser_power(command.power_pct)

    def handle_rotate(command: Command) -> None:
        if command.angle_deg is None:
            raise ValueError("ROTATE without angle_deg")
        rotary.rotate_to(command.angle_deg, command.speed_mm_s or 0.0)

    def handle_dwell(command: Command) -> None:
        if command.dwell_ms is None:
            return
        time.sleep(command.dwell_ms / 1000.0)

    dispatch: Dict[CommandType, Callable[[Command], None]] = {
        CommandType.MOVE: handle_move,
        CommandType.CUT_LINE: handle_cut_line,
        CommandType.SET_LASER_POWER: handle_set_laser_power,
        CommandType.ROTATE: handle_rotate,
        CommandType.DWELL: handle_dwell,
    }

    for command in commands:
        if command.comment:
            log.debug("# %s", command.comment)

        handler = dispatch.get(command.type)
        if handler is None:
            raise ValueError(f"Unsupported command type {command.type}")
        handler(command)
