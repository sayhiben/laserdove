# hardware/base.py
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Iterable, Callable, Dict, List

from ..model import Command, CommandType

log = logging.getLogger(__name__)


class LaserInterface(ABC):
    """Abstract laser motion/power interface."""

    @abstractmethod
    def move(self, x=None, y=None, z=None, speed=None) -> None:
        """Move the head to an absolute position."""

    @abstractmethod
    def cut_line(self, x, y, speed) -> None:
        """Perform a cutting move to the target coordinate."""

    @abstractmethod
    def set_laser_power(self, power_pct) -> None:
        """Set output power percentage."""


class RotaryInterface(ABC):
    """Abstract rotary axis interface."""

    @abstractmethod
    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        """Rotate to an absolute angle at the given speed (deg/sec)."""


class DummyLaser(LaserInterface):
    """In-memory laser stub that only logs state changes."""

    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        """
        Update cached XYZ position without real I/O.

        Args:
            x: Absolute X target (mm).
            y: Absolute Y target (mm).
            z: Absolute Z target (mm).
            speed: Optional travel speed.
        """
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        log.info("MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)

    def cut_line(self, x, y, speed) -> None:
        """
        Update XY position while logging a cutting move.

        Args:
            x: Absolute X target (mm).
            y: Absolute Y target (mm).
            speed: Cutting speed (mm/s).
        """
        self.x = x
        self.y = y
        log.info("CUT_LINE x=%.3f y=%.3f speed=%.3f", x, y, speed)

    def set_laser_power(self, power_pct) -> None:
        """
        Cache power percentage without sending to hardware.

        Args:
            power_pct: Requested power percentage.
        """
        self.power = power_pct
        log.info("SET_LASER_POWER %.1f%%", power_pct)


class DummyRotary(RotaryInterface):
    """In-memory rotary stub that tracks angle only."""

    def __init__(self) -> None:
        self.angle = 0.0

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        """
        Update cached rotary angle.

        Args:
            angle_deg: Absolute target angle in degrees.
            speed_dps: Rotation speed in degrees/sec.
        """
        log.info("ROTATE to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        self.angle = angle_deg
        time.sleep(0.0)


def execute_commands(
    commands: Iterable[Command],
    laser: LaserInterface,
    rotary: RotaryInterface,
) -> None:
    """
    Interpret Command objects and call the appropriate laser/rotary methods.

    Args:
        commands: Iterable of planned commands.
        laser: Laser backend implementing motion/power.
        rotary: Rotary backend implementing rotation.

    Returns:
        None.
    """
    # Track drivers with cleanup to release GPIO pins after the run.
    cleanup_funcs: List[Callable[[], None]] = []
    for dev in (laser, rotary):
        if hasattr(dev, "cleanup") and callable(getattr(dev, "cleanup")):
            cleanup_funcs.append(getattr(dev, "cleanup"))

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

    dispatch: Dict[CommandType, Callable[[Command], None]] = {
        CommandType.MOVE: handle_move,
        CommandType.CUT_LINE: handle_cut_line,
        CommandType.SET_LASER_POWER: handle_set_laser_power,
        CommandType.ROTATE: handle_rotate,
    }

    try:
        for command in commands:
            if command.comment:
                log.debug("# %s", command.comment)

            handler = dispatch.get(command.type)
            if handler is None:
                raise ValueError(f"Unsupported command type {command.type}")
            handler(command)
    finally:
        for cleanup in cleanup_funcs:
            try:
                cleanup()
            except Exception:
                log.debug("Cleanup failed", exc_info=True)
