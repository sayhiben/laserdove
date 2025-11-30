# Tests for Ruida movement-only behavior (no cut commands in RD jobs)
from laserdove.hardware.ruida_laser import RuidaLaser
from laserdove.model import Command, CommandType


class _CaptureRuida(RuidaLaser):
    def __init__(self, movement_only: bool = True) -> None:
        super().__init__("127.0.0.1", dry_run=True, movement_only=movement_only)
        self.jobs = []

    def send_rd_job(self, moves, job_z_mm=None, **kwargs):  # type: ignore[override]
        self.jobs.append((list(moves), job_z_mm))


class _CaptureRuidaWithMoves(_CaptureRuida):
    def __init__(self, movement_only: bool = True) -> None:
        super().__init__(movement_only=movement_only)
        self.move_calls = []

    def move(self, x=None, y=None, z=None, speed=None):  # type: ignore[override]
        self.move_calls.append((x, y, z, speed))
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z


class _StubRotary:
    def __init__(self) -> None:
        self.calls = []

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        self.calls.append((angle_deg, speed_dps))


def test_movement_only_strips_cut_moves_and_power() -> None:
    laser = _CaptureRuida(movement_only=True)
    rotary = _StubRotary()

    commands = [
        Command(type=CommandType.SET_LASER_POWER, power_pct=50.0),
        Command(type=CommandType.MOVE, x=0.0, y=0.0, speed_mm_s=200.0),
        Command(type=CommandType.CUT_LINE, x=10.0, y=0.0, speed_mm_s=50.0),
    ]

    laser.run_sequence_with_rotary(commands, rotary, travel_only=True)

    assert laser.jobs, "Expected at least one RD job"
    moves, job_z = laser.jobs[0]
    assert job_z is None
    assert all(not mv.is_cut for mv in moves)
    assert all(mv.power_pct == 0.0 for mv in moves)


def test_travel_only_mode_applies_even_without_movement_flag() -> None:
    # Simulate --reset: movement_only False, but travel_only True at callsite
    laser = _CaptureRuida(movement_only=False)
    rotary = _StubRotary()

    commands = [
        Command(type=CommandType.SET_LASER_POWER, power_pct=0.0),
        Command(type=CommandType.MOVE, x=0.0, y=0.0, speed_mm_s=150.0),
        Command(type=CommandType.CUT_LINE, x=5.0, y=0.0, speed_mm_s=50.0),
    ]

    laser.run_sequence_with_rotary(commands, rotary, travel_only=True)

    moves, _ = laser.jobs[0]
    assert all(not mv.is_cut for mv in moves)
    assert all(mv.power_pct == 0.0 for mv in moves)


def test_travel_only_still_applies_z_moves() -> None:
    target_z = 12.5
    laser = _CaptureRuidaWithMoves(movement_only=True)
    rotary = _StubRotary()

    commands = [
        Command(type=CommandType.MOVE, z=target_z, speed_mm_s=5.0),
        Command(type=CommandType.MOVE, x=0.0, y=0.0, speed_mm_s=150.0),
    ]

    laser.run_sequence_with_rotary(commands, rotary, travel_only=True)

    assert any(call[2] == target_z for call in laser.move_calls), "Z move should run in travel-only mode"
