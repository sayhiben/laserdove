from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Allow importing the RD parser helper from tools/ for round-trip checks.
ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT / "tools"))

from laserdove.hardware.rd_builder import RDMove, build_rd_job  # noqa: E402
from tools.rd_parser import RuidaParser  # type: ignore # noqa: E402


def test_build_rd_job_preserves_negative_coords_and_z_offsets() -> None:
    moves = [
        RDMove(x_mm=0.0, y_mm=-10.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False),
        RDMove(x_mm=0.0, y_mm=-10.0, speed_mm_s=5.0, power_pct=0.0, is_cut=False, z_mm=2.5),
        RDMove(x_mm=5.0, y_mm=10.0, speed_mm_s=20.0, power_pct=50.0, is_cut=True),
        RDMove(x_mm=5.0, y_mm=10.0, speed_mm_s=5.0, power_pct=0.0, is_cut=False, z_mm=-1.0),
    ]

    payload = build_rd_job(moves, job_z_mm=None, air_assist=True)
    parser = RuidaParser(buf=payload)
    parser.decode(debug=False)

    assert parser._bbox == pytest.approx([0.0, -10.0, 5.0, 10.0], abs=1e-3)
    z_values = [round(val, 3) for _, val, _, _ in parser._z_offsets]
    assert 2.5 in z_values  # first move from 0 -> +2.5 mm
    assert -3.5 in z_values  # second move from +2.5 -> -1.0 mm
