from __future__ import annotations

from typing import Iterable, List

from laserdove.hardware.ruida_laser import RuidaLaser
from laserdove.model import Command, CommandType
from laserdove.hardware.rd_builder import RDMove


class FakeRotary:
    def __init__(self) -> None:
        self.angle = 0.0

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        self.angle = angle_deg


class CapturingRuida(RuidaLaser):
    """
    Test double that records RD jobs and simulates the controller moving
    back to a different position after each job.
    """

    def __init__(self, *, initial_x: float, initial_y: float, return_x: float, return_y: float) -> None:
        super().__init__(
            host="0.0.0.0",
            port=0,
            timeout_s=1.0,
            dry_run=True,
            movement_only=False,
            save_rd_dir=None,
        )
        self.x = initial_x
        self.y = initial_y
        self._sim_return = (return_x, return_y)
        self.sent_jobs: List[List[RDMove]] = []
        self._state_poll_count = 0

    def send_rd_job(
        self,
        moves: List[RDMove],
        job_z_mm: float | None = None,
        *,
        require_busy_transition: bool = True,
        start_z_mm: float | None = None,
    ) -> None:
        self.sent_jobs.append(moves)
        # Simulate controller auto-returning to some other coordinate.
        self.x, self.y = self._sim_return

    def _read_machine_state(self, *, read_positions: bool = True):
        # Return the currently tracked coords to mirror real polling.
        self._state_poll_count += 1
        return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y, z_mm=self.z)


def run_two_jobs(laser: CapturingRuida) -> None:
    # Two simple MOVE blocks separated by ROTATE to force two RD jobs.
    cmds: List[Command] = [
        Command(type=CommandType.MOVE, x=0.0, y=0.0, z=None, speed_mm_s=100.0),
        Command(type=CommandType.ROTATE, angle_deg=10.0, speed_mm_s=30.0),
        Command(type=CommandType.MOVE, x=5.0, y=5.0, z=None, speed_mm_s=100.0),
    ]
    laser.run_sequence_with_rotary(cmds, FakeRotary(), movement_only=False, edge_length_mm=100.0)


def test_repositions_to_job_origin_before_each_rd_job():
    """
    After the first RD job, simulate the controller moving to (0,0).
    The next job should trigger a reposition back to the captured origin (10,20)
    before uploading.
    """
    laser = CapturingRuida(initial_x=10.0, initial_y=20.0, return_x=0.0, return_y=0.0)
    repositioned: list[tuple[float, float]] = []

    orig_move = laser.move

    def spy_move(x=None, y=None, z=None, speed=None):
        if x is not None and y is not None:
            repositioned.append((x, y))
        return orig_move(x=x, y=y, z=z, speed=speed)

    laser.move = spy_move  # type: ignore[assignment]

    run_two_jobs(laser)

    # We expect multiple RD jobs (two blocks + final park); ensure a reposition to the captured origin occurred.
    assert len(laser.sent_jobs) >= 2
    assert (10.0, 20.0) in repositioned
