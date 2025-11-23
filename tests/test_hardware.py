import pytest

from hardware import DummyLaser, DummyRotary, execute_commands
from model import Command, CommandType
from hardware import SimulatedLaser, SimulatedRotary, RuidaLaser, RealRotary


def test_execute_commands_updates_state():
    laser = DummyLaser()
    rotary = DummyRotary()
    commands = [
        Command(type=CommandType.SET_LASER_POWER, power_pct=50.0),
        Command(type=CommandType.MOVE, x=1.0, y=2.0, z=3.0, speed_mm_s=100.0),
        Command(type=CommandType.CUT_LINE, x=5.0, y=2.0, speed_mm_s=10.0),
        Command(type=CommandType.ROTATE, angle_deg=15.0, speed_mm_s=5.0),
        Command(type=CommandType.DWELL, dwell_ms=1),
    ]

    execute_commands(commands, laser, rotary)

    assert laser.power == 50.0
    assert (laser.x, laser.y, laser.z) == (5.0, 2.0, 3.0)
    assert rotary.angle == 15.0


@pytest.mark.parametrize(
    "command",
    [
        Command(type=CommandType.CUT_LINE, x=1, y=1, speed_mm_s=None),
        Command(type=CommandType.SET_LASER_POWER, power_pct=None),
        Command(type=CommandType.ROTATE, angle_deg=None, speed_mm_s=1.0),
    ],
)
def test_execute_commands_missing_required_fields_raise(command):
    laser = DummyLaser()
    rotary = DummyRotary()
    with pytest.raises(ValueError):
        execute_commands([command], laser, rotary)


def test_simulated_laser_records_segments_and_rotation(monkeypatch):
    laser = SimulatedLaser(real_time=False)
    laser.set_laser_power(10)
    laser.move(x=1, y=0, speed=10)
    laser.cut_line(x=2, y=0, speed=10)
    laser.set_rotation(5.0)
    laser.move(x=2, y=1, speed=10)

    assert laser.segments  # segments recorded
    assert laser.rotation_deg == 5.0
    assert laser.current_board == "pin"


def test_simulated_rotary_respects_real_time(monkeypatch):
    laser = SimulatedLaser(real_time=True, time_scale=1000.0)
    rotary = SimulatedRotary(visualizer=laser, real_time=True, time_scale=1000.0)
    rotary.rotate_to(10.0, speed_dps=1000.0)
    assert abs(rotary.angle - 10.0) < 1e-9
    assert abs(laser.rotation_deg - 10.0) < 1e-9


def test_skeleton_backends_accept_calls():
    ruida = RuidaLaser(host="127.0.0.1", port=50200)
    ruida.set_laser_power(20)
    ruida.move(x=1, y=2, z=3, speed=100)
    ruida.cut_line(x=4, y=5, speed=10)

    rotary = RealRotary()
    rotary.rotate_to(30.0, speed_dps=5.0)
