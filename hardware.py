# hardware.py
from __future__ import annotations

import logging
import math
import time
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
    Skeleton implementation intended to wrap your existing Ruida tooling
    (RuidaProxy, udpsendruida, ruida.py).

    v1: Only logs; replace internals with UDP / RD-file logic when ready.
    """

    def __init__(self, host: str, port: int = 50200) -> None:
        self.host = host
        self.port = port
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0
        log.info("RuidaLaser initialized for host=%s port=%d", host, port)
        # TODO: initialize UDP socket or RuidaProxy connection here.

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        log.info("[RUDA] MOVE x=%.3f y=%.3f z=%.3f speed=%s",
                 self.x, self.y, self.z, speed)
        # TODO: send rapid move / path segment to Ruida.

    def cut_line(self, x, y, speed) -> None:
        self.x = x
        self.y = y
        log.info("[RUDA] CUT_LINE x=%.3f y=%.3f speed=%.3f power=%.1f%%",
                 x, y, speed, self.power)
        # TODO: send cut vector to Ruida at current power.

    def set_laser_power(self, power_pct) -> None:
        self.power = power_pct
        log.info("[RUDA] SET_LASER_POWER %.1f%%", power_pct)
        # TODO: encode power into RD job or runtime power override if supported.


class RealRotary(RotaryInterface):
    """
    Skeleton implementation for the physical rotary on the Pi.

    v1: Logs requested angles. Replace method body with calls into
    your stepper driver / GPIO code.
    """

    def __init__(self, steps_per_rev: float | None = None, microsteps: int | None = None) -> None:
        self.angle = 0.0
        self.steps_per_rev = steps_per_rev
        self.microsteps = microsteps
        log.info("RealRotary initialized (steps_per_rev=%s microsteps=%s)",
                 steps_per_rev, microsteps)
        # TODO: initialize GPIO / driver here.

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        log.info("[ROTARY] rotate_to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        self.angle = angle_deg
        # TODO: convert angle to steps and drive motor.
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
