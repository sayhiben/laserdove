# tests/test_simulation_backend.py
from laserdove.hardware import SimulatedLaser, SimulatedRotary, execute_commands
from laserdove.model import Command, CommandType


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


def test_simulated_laser_show_and_viewer_updates(monkeypatch):
    class DummyViewer:
        def __init__(self):
            self.updated = 0
            self.mainloop_called = False

        def open(self):  # pragma: no cover - trivial
            pass

        def render(self, segments, rotation_deg):
            self.updated += 1

        def update(self, segments, rotation_deg):
            self.updated += 1

        def mainloop(self, segments, rotation_deg):
            self.mainloop_called = True

    laser = SimulatedLaser()
    laser.viewer = DummyViewer()
    laser.move(x=1.0, y=0.0, speed=10.0)
    laser.set_rotation(15.0)
    laser.show()
    assert laser.viewer.updated >= 2
    assert laser.viewer.mainloop_called is True


def test_simulated_rotary_updates_visualizer(monkeypatch):
    laser = SimulatedLaser()
    viewer_updates = {}

    class DummyViewer:
        def update(self, segments, rotation_deg):
            viewer_updates["rotation"] = rotation_deg

    laser.viewer = DummyViewer()
    rotary = SimulatedRotary(laser, real_time=True, time_scale=1.0)
    monkeypatch.setattr("laserdove.hardware.sim.time.sleep", lambda *_: None)
    rotary.rotate_to(30.0, speed_dps=60.0)
    assert viewer_updates["rotation"] == 30.0


def test_simulated_laser_real_time_sleep(monkeypatch):
    slept = {}
    monkeypatch.setattr(
        "laserdove.hardware.sim.time.sleep", lambda duration: slept.setdefault("duration", duration)
    )
    laser = SimulatedLaser(real_time=True, time_scale=1.0)
    laser._sleep_for_motion(distance_mm=10.0, speed=5.0)
    assert slept["duration"] == 2.0


def test_simulated_laser_show_without_segments(monkeypatch):
    laser = SimulatedLaser()
    # No segments and no viewer -> returns early without error
    laser.show()
