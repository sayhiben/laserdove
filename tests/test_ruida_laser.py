from __future__ import annotations

from laserdove.hardware.ruida_laser import RuidaLaser
from laserdove.model import Command, CommandType


class DummyRotary:
    def __init__(self) -> None:
        self.angle = 0.0

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        self.angle = angle_deg


def test_run_sequence_inserts_origin_move_between_blocks(monkeypatch):
    """Each RD block should start with a move back to the captured job origin."""

    # Capture initial machine state at some arbitrary origin.
    ruida = RuidaLaser(host="0.0.0.0", dry_run=True)
    ruida._read_machine_state = lambda read_positions=True: ruida.MachineState(  # type: ignore[attr-defined]
        status_bits=0, x_mm=100.0, y_mm=200.0, z_mm=0.0
    )

    recorded_blocks = []

    def fake_send_rd_job(moves, **kwargs):
        recorded_blocks.append(moves)
        # Simulate controller parking to a different XY between jobs.
        ruida.x = 5.0
        ruida.y = 6.0

    monkeypatch.setattr(ruida, "send_rd_job", fake_send_rd_job)

    commands = [
        Command(type=CommandType.MOVE, y=10.0, speed_mm_s=100.0),
        Command(type=CommandType.ROTATE, angle_deg=45.0, speed_mm_s=30.0),
        Command(type=CommandType.MOVE, y=20.0, speed_mm_s=100.0),
    ]

    ruida.run_sequence_with_rotary(commands, DummyRotary(), edge_length_mm=None)

    assert len(recorded_blocks) >= 2

    first_block = recorded_blocks[0]
    second_block = recorded_blocks[1]

    # Both blocks should start by returning to the original job origin (100, 200).
    assert first_block[0].x_mm == 100.0
    assert first_block[0].y_mm == 200.0
    assert second_block[0].x_mm == 100.0
    assert second_block[0].y_mm == 200.0
