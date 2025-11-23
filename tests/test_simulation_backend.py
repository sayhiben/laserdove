# tests/test_simulation_backend.py
from hardware import SimulatedLaser, SimulatedRotary, execute_commands
from model import Command, CommandType


def test_simulated_laser_records_move_and_cut():
    laser = SimulatedLaser()
    rotary = SimulatedRotary(laser)

    commands = [
        Command(type=CommandType.MOVE, x=0.0, y=0.0, z=0.0, speed_mm_s=100.0),
        Command(type=CommandType.SET_LASER_POWER, power_pct=50.0),
        Command(type=CommandType.CUT_LINE, x=10.0, y=0.0, speed_mm_s=5.0),
        Command(type=CommandType.SET_LASER_POWER, power_pct=0.0),
        Command(type=CommandType.MOVE, x=10.0, y=20.0, speed_mm_s=50.0),
    ]

    execute_commands(commands, laser, rotary)

    assert len(laser.segments) == 2
    assert laser.segments[0]["is_cut"] is True
    assert laser.segments[1]["is_cut"] is False
