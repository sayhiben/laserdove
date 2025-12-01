#!/usr/bin/env python3
"""
Visualize one or more RD files using the Tk simulation viewer.

This replays XY/Z moves decoded from the RD body (0x88/0x8a/0x8b moves,
0xA8/0xAA/0xAB cuts, 0x80 0x03 Z offsets) and renders the path with the
same canvas used by the simulator so we can compare RD output against the
planned command stream.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List

# Add project root so we can import laserdove modules when run from tools/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from laserdove.simulation_viewer import SimulationViewer  # noqa: E402
from tools.rd_parser import RuidaParser  # noqa: E402


def rd_segments(
    rd_file: Path,
    *,
    edge_length_mm: float,
    rotation_deg: float,
    board: str,
) -> List[dict]:
    parser = RuidaParser(file=str(rd_file))
    parser.decode(debug=False)
    y_center = edge_length_mm / 2.0

    segments: List[dict] = []
    for seg in parser._segments:
        segments.append(
            {
                "x0": seg["x0"],
                "y0": seg["y0"] + y_center,
                "x1": seg["x1"],
                "y1": seg["y1"] + y_center,
                "z": seg["z"],
                "logical_z": seg["logical_z"],
                "is_cut": seg["is_cut"],
                "board": board,
                "rotation_deg": rotation_deg,
                "air_assist": True,
            }
        )
    return segments


def main() -> None:
    ap = argparse.ArgumentParser(description="Render RD files in the Tk simulation viewer.")
    ap.add_argument(
        "rd_files",
        nargs="+",
        help="RD files to visualize (swizzled .rd as saved from Ruida dry runs)",
    )
    ap.add_argument(
        "--edge-length-mm",
        type=float,
        default=100.0,
        help="Board edge length used to recover centered Y",
    )
    ap.add_argument(
        "--rotation-deg", type=float, default=0.0, help="Rotation to annotate in the viewer"
    )
    ap.add_argument(
        "--board", choices=["tail", "pin"], default="pin", help="Label to color segments under"
    )
    args = ap.parse_args()

    all_segments: List[dict] = []
    for path_str in args.rd_files:
        path = Path(path_str)
        if not path.exists():
            ap.error(f"RD file not found: {path}")
        all_segments.extend(
            rd_segments(
                path,
                edge_length_mm=args.edge_length_mm,
                rotation_deg=args.rotation_deg,
                board=args.board,
            )
        )

    viewer = SimulationViewer()
    viewer.open()
    viewer.render(
        all_segments,
        rotation_deg=args.rotation_deg,
        origin=(0.0, 0.0),
        y_center=args.edge_length_mm / 2.0,
    )
    viewer.mainloop(
        all_segments,
        rotation_deg=args.rotation_deg,
        origin=(0.0, 0.0),
        y_center=args.edge_length_mm / 2.0,
    )


if __name__ == "__main__":
    main()
