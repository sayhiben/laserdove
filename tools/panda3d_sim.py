#!/usr/bin/env python3
"""
Panda3D-based 3D simulator for laserdove plans.

This replays planner output in 3D and can optionally overlay decoded RD files
to compare planned vs. emitted toolpaths.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

# Add project root so we can import laserdove modules when run from tools/.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from laserdove.cli import plan_commands  # noqa: E402
from laserdove.config import build_arg_parser, load_config_and_args  # noqa: E402
from laserdove.logging_utils import setup_logging  # noqa: E402
from laserdove.panda3d_simulator import (  # noqa: E402
    Panda3DViewer,
    capture_segments_from_commands,
    overlay_segments_from_rd,
)
from tools.rd_parser import RuidaParser  # noqa: E402


def _parse_rd_arg(raw: str) -> tuple[Path, str, float]:
    """
    Parse path[:board[:rotation_deg]] into components.
    """
    parts = raw.split(":")
    if not parts or not parts[0]:
        raise argparse.ArgumentTypeError("RD argument must include a file path")
    path = Path(parts[0])
    board = parts[1] if len(parts) > 1 and parts[1] else "pin"
    rotation = float(parts[2]) if len(parts) > 2 and parts[2] else 0.0
    return path, board, rotation


def _rd_segments_from_file(rd_path: Path) -> List[dict]:
    parser = RuidaParser(file=str(rd_path))
    parser.decode(debug=False)
    segments: List[dict] = []
    for seg in parser._segments:
        segments.append(
            {
                "x0": seg["x0"],
                "y0": seg["y0"],
                "x1": seg["x1"],
                "y1": seg["y1"],
                "z": seg.get("z", 0.0),
                "logical_z": seg.get("logical_z", 0.0),
                "is_cut": seg.get("is_cut", False),
                "air_assist": seg.get("air_assist", True),
                "power_pct": seg.get("power_pct", 0.0),
            }
        )
    return segments


def _build_parser() -> argparse.ArgumentParser:
    parser = build_arg_parser()
    parser.description = "Render planner output in 3D with Panda3D (optional RD overlays)."
    parser.add_argument(
        "--rd",
        action="append",
        metavar="PATH[:BOARD[:ROT]]",
        help="Overlay a swizzled RD file; BOARD defaults to pin, ROT defaults to 0°.",
    )
    parser.add_argument(
        "--time-scale",
        type=float,
        default=1.0,
        help="Scale playback speed (1.0 = real-time based on commanded feed).",
    )
    parser.add_argument(
        "--skip-plan",
        action="store_true",
        help="Skip planner execution and only render RD overlays.",
    )
    parser.add_argument(
        "--board-thickness-mm",
        type=float,
        help="Override board thickness used for visualization (defaults to joint.thickness_mm).",
    )
    parser.add_argument(
        "--window-size",
        metavar="WIDTHxHEIGHT",
        help="Window size for the Panda3D viewer (e.g. 1600x1200). Defaults to 1600x1200.",
    )
    return parser


def _parse_window_size(raw: str | None) -> tuple[int, int] | None:
    if not raw:
        return None
    sep = "x" if "x" in raw else ","
    parts = raw.lower().split(sep)
    if len(parts) != 2:
        raise argparse.ArgumentTypeError("Window size must be WIDTHxHEIGHT")
    try:
        w = int(parts[0])
        h = int(parts[1])
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Window size must be WIDTHxHEIGHT") from exc
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("Window size must be positive")
    return w, h


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    setup_logging(args.log_level)
    window_size = _parse_window_size(args.window_size)

    run_config = load_config_and_args(args)
    joint = run_config.joint_params
    jig = run_config.jig_params
    machine = run_config.machine_params
    board_thickness = args.board_thickness_mm or joint.thickness_mm

    plan_segments: List = []
    if not args.skip_plan:
        commands = plan_commands(run_config)
        start_board = "tail" if run_config.mode in ("tails", "both") else "pin"
        plan_segments = capture_segments_from_commands(
            commands,
            edge_length_mm=joint.edge_length_mm,
            axis_to_origin_mm=jig.axis_to_origin_mm,
            rotation_zero_deg=jig.rotation_zero_deg,
            z_zero_tail_mm=machine.z_zero_tail_mm,
            z_zero_pin_mm=machine.z_zero_pin_mm,
            movement_only=run_config.movement_only or run_config.reset_only,
            air_assist=machine.air_assist,
            start_board=start_board,
        )

    overlay_segments: List = []
    rd_args: List[Tuple[Path, str, float]] = []
    if args.rd:
        for raw in args.rd:
            try:
                rd_args.append(_parse_rd_arg(raw))
            except argparse.ArgumentTypeError as exc:
                parser.error(str(exc))

    for rd_path, board, rotation in rd_args:
        if not rd_path.exists():
            parser.error(f"RD file not found: {rd_path}")
        rd_segments = _rd_segments_from_file(rd_path)
        overlay_segments.extend(
            overlay_segments_from_rd(
                rd_segments,
                rotation,
                board,
                edge_length_mm=joint.edge_length_mm,
                axis_to_origin_mm=jig.axis_to_origin_mm,
                rotation_zero_deg=jig.rotation_zero_deg,
                z_zero_tail_mm=machine.z_zero_tail_mm,
                z_zero_pin_mm=machine.z_zero_pin_mm,
            )
        )

    if not plan_segments and not overlay_segments:
        parser.error("Nothing to render: enable planning or supply --rd overlays.")

    try:
        viewer = Panda3DViewer(
            plan_segments,
            overlay_segments,
            axis_to_origin_mm=jig.axis_to_origin_mm,
            edge_length_mm=joint.edge_length_mm,
            board_thickness_mm=board_thickness,
            rotation_zero_deg=jig.rotation_zero_deg,
            time_scale=args.time_scale,
            window_size=window_size,
        )
    except RuntimeError as exc:
        parser.error(str(exc))
    viewer.run()


if __name__ == "__main__":
    main()
