# hardware.py
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from typing import Iterable, Callable, Dict

from model import Command, CommandType

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
