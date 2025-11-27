from laserdove.hardware.rd_builder import (
    RDMove,
    _RDJobBuilder,
    _compute_odometer,
    build_rd_job,
)


def test_empty_moves_returns_empty_payload():
    assert build_rd_job([]) == b""


def test_odometer_splits_travel_and_cut():
    moves = [
        RDMove(0.0, 0.0, speed_mm_s=10.0, power_pct=0.0, is_cut=False),
        RDMove(0.0, 3.0, speed_mm_s=10.0, power_pct=50.0, is_cut=False),
        RDMove(4.0, 3.0, speed_mm_s=10.0, power_pct=50.0, is_cut=True),
    ]
    cut, travel = _compute_odometer(moves)
    assert cut == 4.0
    assert travel == 3.0


def test_build_rd_job_contains_z_speed_and_power_blocks():
    moves = [
        RDMove(0.0, 0.0, speed_mm_s=10.0, power_pct=0.0, is_cut=False),
        RDMove(10.0, 0.0, speed_mm_s=20.0, power_pct=50.0, is_cut=True),
    ]
    payload = build_rd_job(moves, job_z_mm=5.0, filename="TESTFILE")

    # Filename block (E7 01) is intentionally omitted.
    assert b"\xE7\x01" not in payload

    # Z move block (AXIS_Z_MOVE) should be present.
    z_bytes = _RDJobBuilder.encode_number(5.0)
    assert b"\x80\x01" + z_bytes in payload

    # Speed block uses C9 02 with encoded speed; last speed set should be 20 mm/s.
    speed_bytes = _RDJobBuilder.encode_number(20.0)
    assert b"\xC9\x02" + speed_bytes in payload

    # Power block uses C6 01/C6 02 min/max; both carry the same encoded percent.
    power_bytes = _RDJobBuilder.encode_percent(50.0)
    assert b"\xC6\x01" in payload
    assert power_bytes in payload

    # Trailer ends with D7 terminator.
    assert payload.endswith(b"\xD7")


def test_build_rd_job_air_assist_toggle():
    moves = [
        RDMove(0.0, 0.0, speed_mm_s=10.0, power_pct=0.0, is_cut=False),
        RDMove(10.0, 0.0, speed_mm_s=20.0, power_pct=50.0, is_cut=True),
    ]

    payload_on = build_rd_job(moves, air_assist=True)
    payload_off = build_rd_job(moves, air_assist=False)

    assert b"\xCA\x01\x13" in payload_on
    assert b"\xCA\x01\x13" not in payload_off
