"""
RD job builder for simple XY move/cut sequences.

This borrows the on-wire structure observed in public Ruida RD examples:
 - full header/body/trailer framing
 - single layer, absolute XY moves (0x88) and cuts (0xA8)
 - optional job Z emitted via 0x80 0x03 using signed mm offsets

It is intentionally small and only covers what our planner emits.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from typing import Iterable, List, Tuple

from .rd_job_builder import _Layer, _RDJobBuilder


@dataclass
class RDMove:
    """Single move/cut with optional target Z (absolute logical position, mm)."""

    x_mm: float
    y_mm: float
    speed_mm_s: float
    power_pct: float
    is_cut: bool
    z_mm: float | None = None


def _moves_to_paths(
    moves: Iterable[RDMove],
) -> Tuple[List[List[Tuple[float, float]]], List[List[float]]]:
    """
    Collapse sequential cut segments into paths for bbox computation.

    Args:
        moves: Iterable of RDMove entries.

    Returns:
        Tuple of (path list, bounding box [[xmin, ymin], [xmax, ymax]]).
    """
    paths: List[List[Tuple[float, float]]] = []
    cursor: Tuple[float, float] | None = None
    current_path: List[Tuple[float, float]] | None = None
    cutting = False

    for mv in moves:
        if mv.z_mm is not None:
            # Z-only move does not affect XY path construction.
            continue
        point = (mv.x_mm, mv.y_mm)
        if mv.is_cut:
            if cutting and current_path is not None:
                current_path.append(point)
            else:
                if current_path:
                    paths.append(current_path)
                start_point = cursor if cursor is not None else point
                current_path = [start_point, point] if start_point != point else [point]
                cutting = True
        else:
            if current_path:
                paths.append(current_path)
            current_path = [point]
            cutting = False
        cursor = point

    if current_path:
        paths.append(current_path)

    bbox = _RDJobBuilder.boundingbox(paths) if paths else [[0.0, 0.0], [0.0, 0.0]]
    return paths, bbox


def _compute_odometer(moves: List[RDMove]) -> Tuple[float, float]:
    """
    Compute cut and travel distances in mm (simple segment lengths).

    Args:
        moves: Sequence of RDMove objects.

    Returns:
        Tuple of (cut_distance_mm, travel_distance_mm).
    """
    if not moves:
        return (0.0, 0.0)
    cut = 0.0
    travel = 0.0
    prev_x = moves[0].x_mm
    prev_y = moves[0].y_mm
    for mv in moves[1:]:
        dist = math.hypot(mv.x_mm - prev_x, mv.y_mm - prev_y)
        if mv.is_cut:
            cut += dist
        else:
            travel += dist
        prev_x, prev_y = mv.x_mm, mv.y_mm
    return (cut, travel)


def build_rd_job(
    moves: List[RDMove],
    job_z_mm: float | None = None,
    initial_z_mm: float | None = None,
    *,
    filename: str = "LASERDOVE",
    air_assist: bool = True,
    blow_on: bool = False,
) -> bytes:
    """
    Build an unswizzled RD payload for a sequence of moves.
    The optional job_z_mm is a signed Z offset (mm) encoded once with opcode 0x80 0x03.
    Z moves embedded in RDMove.z_mm emit additional 0x80 0x03 commands inline.

    Args:
        moves: Sequence of RDMove entries.
        job_z_mm: Optional job-level Z offset in mm.
        initial_z_mm: Logical Z at job start; used to turn absolute targets into relative offsets.
        filename: Filename tag for the RD job.
        air_assist: Whether to enable air-assist flags.
        blow_on: Whether to enable the BLOW output (inline fan) flag.

    Returns:
        Unscrambled RD job payload bytes.
    """
    if not moves:
        return b""

    normalized_moves: List[RDMove] = []
    for mv in moves:
        # Treat zero-power cuts as travel to avoid controllers reusing default power.
        is_cut = mv.is_cut and mv.power_pct > 0.0
        power_pct = mv.power_pct if is_cut else 0.0
        normalized_moves.append(
            RDMove(
                x_mm=mv.x_mm,
                y_mm=mv.y_mm,
                speed_mm_s=mv.speed_mm_s,
                power_pct=power_pct,
                is_cut=is_cut,
                z_mm=mv.z_mm,
            )
        )

    # Convert absolute Z targets into relative 0x80 03 offsets.
    current_z = 0.0 if initial_z_mm is None else initial_z_mm
    if job_z_mm is not None:
        current_z += job_z_mm
    z_relative_moves: List[RDMove] = []
    for mv in normalized_moves:
        mv_copy = copy.copy(mv)
        if mv_copy.z_mm is not None:
            target_z = mv_copy.z_mm
            mv_copy.z_mm = target_z - current_z
            current_z = target_z
        z_relative_moves.append(mv_copy)

    paths, bbox = _moves_to_paths(normalized_moves)
    travel_speed = next(
        (mv.speed_mm_s for mv in normalized_moves if not mv.is_cut), normalized_moves[0].speed_mm_s
    )
    cut_speed = next((mv.speed_mm_s for mv in normalized_moves if mv.is_cut), travel_speed)
    power = next(
        (mv.power_pct for mv in normalized_moves if mv.is_cut),
        next((mv.power_pct for mv in normalized_moves), 0.0),
    )
    power = max(0.0, power)

    layer = _Layer(
        paths=paths,
        bbox=bbox,
        speed=[travel_speed, cut_speed],
        power=[power, power],
    )
    builder = _RDJobBuilder()
    builder._globalbbox = bbox
    cut_dist, travel_dist = _compute_odometer(normalized_moves)

    header = builder.header([layer], filename=filename)
    # Build a simplified body that preserves move order and injects inline Z offsets.
    data = bytearray()

    prolog_flags = "ca 01 30\nca 01 10\n"
    if air_assist:
        prolog_flags += "ca 01 13\n"
    if blow_on:
        prolog_flags += "ca 13\n"
    data.extend(builder.enc("-b-", ["ca 01 00\nca 02", 0, prolog_flags]))

    SPEED_SET = "c9 02"
    CUT_DELAY_ON = "c6 15 00 00 00 00 00"
    CUT_DELAY_OFF = "c6 16 00 00 00 00 00"
    L1_MIN = "c6 01"
    L1_MAX = "c6 02"
    L2_MIN = "c6 21"
    L2_MAX = "c6 22"
    L3_MIN = "c6 05"
    L3_MAX = "c6 06"
    L4_MIN = "c6 07"
    L4_MAX = "c6 08"
    ENABLE_LAYER = "ca 03 01"
    IO_FLAGS = "ca 10 00"

    power_vals = [power, power, power, power, power, power, power, power]
    speed_vals = [travel_speed, cut_speed]

    data.extend(
        builder.enc(
            "-n---p-p-p-p-p-p-p-p--",
            [
                SPEED_SET,
                speed_vals[1],
                CUT_DELAY_ON,
                CUT_DELAY_OFF,
                L1_MIN,
                power_vals[0],
                L1_MAX,
                power_vals[1],
                L2_MIN,
                power_vals[2],
                L2_MAX,
                power_vals[3],
                L3_MIN,
                power_vals[4],
                L3_MAX,
                power_vals[5],
                L4_MIN,
                power_vals[6],
                L4_MAX,
                power_vals[7],
                ENABLE_LAYER,
                IO_FLAGS,
            ],
        )
    )

    def emit_speed(speed: float) -> bytes:
        return builder.enc("-n", [SPEED_SET, speed])

    # Optionally start with a job-level Z offset.
    if job_z_mm is not None:
        data.extend(bytes([0x80, 0x03]))
        data.extend(builder.encode_z_offset(job_z_mm))

    last_speed = None
    for mv in z_relative_moves:
        if mv.z_mm is not None:
            data.extend(bytes([0x80, 0x03]))
            data.extend(builder.encode_z_offset(mv.z_mm))
            continue

        if mv.speed_mm_s is not None and (
            last_speed is None or abs(mv.speed_mm_s - last_speed) > 1e-6
        ):
            data.extend(emit_speed(mv.speed_mm_s))
            last_speed = mv.speed_mm_s

        opcode = "a8" if mv.is_cut else "88"
        data.extend(builder.enc("-nn", [opcode, mv.x_mm, mv.y_mm]))

    body = bytes(data)
    trailer = builder.trailer((cut_dist, travel_dist))
    return header + body + trailer
