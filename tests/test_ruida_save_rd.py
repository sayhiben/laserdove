from laserdove.hardware.rd_builder import RDMove, build_rd_job
from laserdove.hardware.ruida import RuidaLaser
from laserdove.hardware.ruida_common import swizzle


def test_ruida_saves_rd_file(tmp_path):
    laser = RuidaLaser(
        host="0.0.0.0",
        dry_run=True,
        movement_only=False,
        save_rd_dir=tmp_path,
    )
    moves = [
        RDMove(0.0, 0.0, speed_mm_s=10.0, power_pct=0.0, is_cut=False),
        RDMove(5.0, 0.0, speed_mm_s=10.0, power_pct=50.0, is_cut=True),
    ]

    laser.send_rd_job(moves, job_z_mm=1.0)

    rd_files = list(tmp_path.glob("job_*.rd"))
    assert len(rd_files) == 1

    saved = rd_files[0].read_bytes()
    expected_swizzled = swizzle(build_rd_job(moves, job_z_mm=1.0))
    assert saved == expected_swizzled
