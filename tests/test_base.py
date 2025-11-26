import types
from types import SimpleNamespace

import pytest

from laserdove.hardware.base import (
    DummyLaser,
    DummyRotary,
    LaserInterface,
    RotaryInterface,
    execute_commands,
)
from laserdove.model import Command, CommandType


class TrackingLaser(LaserInterface):
    def __init__(self) -> None:
        self.calls = []
        self.cleaned = False

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        self.calls.append(("move", x, y, z, speed))
        super().move(x=x, y=y, z=z, speed=speed)  # exercise abstract pass

    def cut_line(self, x, y, speed) -> None:
        self.calls.append(("cut", x, y, speed))
        super().cut_line(x, y, speed)

    def set_laser_power(self, power_pct) -> None:
        self.calls.append(("power", power_pct))
        super().set_laser_power(power_pct)

    def cleanup(self) -> None:
        self.cleaned = True


class TrackingRotary(RotaryInterface):
    def __init__(self) -> None:
        self.calls = []
        self.cleaned = False

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        self.calls.append(("rotate", angle_deg, speed_dps))
        super().rotate_to(angle_deg, speed_dps)  # exercise abstract pass

    def cleanup(self) -> None:
        self.cleaned = True


def test_dummy_laser_updates_state_and_logs():
    laser = DummyLaser()
    laser.move(x=1.0, y=2.0, z=3.0, speed=10.0)
    assert (laser.x, laser.y, laser.z) == (1.0, 2.0, 3.0)
    # Call again with only one axis to exercise conditional branches.
    laser.move(x=5.0, speed=20.0)
    assert laser.x == 5.0 and laser.y == 2.0 and laser.z == 3.0
    laser.move(y=7.0)  # x None path
    assert laser.x == 5.0 and laser.y == 7.0
    laser.cut_line(4.0, 5.0, speed=20.0)
    assert (laser.x, laser.y) == (4.0, 5.0)
    laser.set_laser_power(55.0)
    assert laser.power == 55.0


def test_execute_commands_runs_all_handlers_and_cleans(monkeypatch):
    laser = TrackingLaser()
    rotary = TrackingRotary()
    slept = {}
    monkeypatch.setattr("laserdove.hardware.base.time.sleep", lambda t: slept.setdefault("t", t))

    commands = [
        Command(type=CommandType.MOVE, x=0.0, y=0.0, z=0.0, speed_mm_s=100.0, comment="start"),
        Command(type=CommandType.SET_LASER_POWER, power_pct=10.0),
        Command(type=CommandType.CUT_LINE, x=1.0, y=1.0, speed_mm_s=5.0),
        Command(type=CommandType.ROTATE, angle_deg=45.0, speed_mm_s=30.0),
        Command(type=CommandType.DWELL, dwell_ms=50),
        Command(type=CommandType.DWELL, dwell_ms=None),  # cover early return
    ]
    execute_commands(commands, laser, rotary)

    assert ("move", 0.0, 0.0, 0.0, 100.0) in laser.calls
    assert ("power", 10.0) in laser.calls
    assert ("cut", 1.0, 1.0, 5.0) in laser.calls
    assert ("rotate", 45.0, 30.0) in rotary.calls
    assert slept["t"] == 0.05  # dwell_ms converted to seconds
    assert laser.cleaned is True
    assert rotary.cleaned is True


def test_execute_commands_raises_on_missing_fields_and_still_cleans(monkeypatch):
    laser = TrackingLaser()
    rotary = TrackingRotary()
    commands = [
        Command(type=CommandType.CUT_LINE, x=1.0, y=1.0, speed_mm_s=None),
    ]
    with pytest.raises(ValueError):
        execute_commands(commands, laser, rotary)
    assert laser.cleaned is True
    assert rotary.cleaned is True


def test_execute_commands_unsupported_type_triggers_cleanup(monkeypatch):
    cleanup_called = {}

    class WithCleanup:
        def cleanup(self):
            cleanup_called["called"] = True

    bad_command = SimpleNamespace(type="bogus", comment="bad")
    with pytest.raises(ValueError):
        execute_commands([bad_command], WithCleanup(), WithCleanup())
    assert cleanup_called["called"] is True


def test_execute_commands_cleanup_exceptions_are_swallowed(monkeypatch):
    class CleanupRaises(DummyLaser):
        def cleanup(self):
            raise RuntimeError("boom")

    class CleanupRotary(DummyRotary):
        def __init__(self):
            super().__init__()
            self.cleaned = False

        def cleanup(self):
            self.cleaned = True

    monkeypatch.setattr("laserdove.hardware.base.time.sleep", lambda *_: None)
    laser = CleanupRaises()
    rotary = CleanupRotary()
    cmds = [Command(type=CommandType.MOVE, x=0.0, y=0.0, speed_mm_s=1.0)]
    execute_commands(cmds, laser, rotary)
    assert rotary.cleaned is True
