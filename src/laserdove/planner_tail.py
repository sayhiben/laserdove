from __future__ import annotations

import math
from typing import List

from .geometry import kerf_offset_boundary
from .model import Command, CommandType, JointParams, MachineParams, TailLayout
from .planner_math import clamp_board_y


def plan_tail_board(
    joint_params: JointParams,
    machine_params: MachineParams,
    tail_layout: TailLayout,
) -> List[Command]:
    """
    Plan tail cuts as trapezoids widened by the dovetail angle.

    Args:
        joint_params: Joint geometry (tails, kerf, clearances).
        machine_params: Machine/process parameters (speeds, powers, Z zeros).
        tail_layout: Tail spacing derived from geometry.

    Returns:
        Ordered Command list to cut all tails and return to origin.
    """
    commands: List[Command] = []

    edge_length_mm = joint_params.edge_length_mm
    num_tails = joint_params.num_tails
    tail_outer_width_mm = tail_layout.tail_outer_width
    pin_outer_width_mm = tail_layout.pin_outer_width
    half_pin_width = tail_layout.half_pin_width
    tail_depth_mm = joint_params.tail_depth_mm
    tail_angle_rad = math.radians(joint_params.dovetail_angle_deg)
    tail_widen_mm = tail_depth_mm * math.tan(tail_angle_rad)

    pockets: List[tuple[float, float]] = []

    # Left edge half-pin pocket
    pockets.append((0.0, half_pin_width))

    tail_pin_pitch = tail_outer_width_mm + pin_outer_width_mm
    for tail_index in range(num_tails - 1):
        y_start = half_pin_width + tail_outer_width_mm + tail_index * tail_pin_pitch
        y_end = y_start + pin_outer_width_mm
        pockets.append((y_start, y_end))

    # Right edge half-pin pocket
    pockets.append((edge_length_mm - half_pin_width, edge_length_mm))

    for pocket_start_y, pocket_end_y in pockets:
        # Tail board: keep is outside the pocket; waste is inside.
        y_left_top = kerf_offset_boundary(
            y_geo=pocket_start_y,
            kerf_mm=joint_params.kerf_tail_mm,
            clearance_mm=joint_params.clearance_mm,
            keep_on_positive_side=False,  # keep at Y < y0
            is_tail_board=True,
        )
        y_right_top = kerf_offset_boundary(
            y_geo=pocket_end_y,
            kerf_mm=joint_params.kerf_tail_mm,
            clearance_mm=joint_params.clearance_mm,
            keep_on_positive_side=True,  # keep at Y > y1
            is_tail_board=True,
        )
        y_left_bottom = y_left_top - tail_widen_mm
        y_right_bottom = y_right_top + tail_widen_mm
        y_left_top = clamp_board_y(y_left_top, edge_length_mm)
        y_right_top = clamp_board_y(y_right_top, edge_length_mm)
        y_left_bottom = clamp_board_y(y_left_bottom, edge_length_mm)
        y_right_bottom = clamp_board_y(y_right_bottom, edge_length_mm)

        commands.append(
            Command(
                type=CommandType.MOVE,
                x=0.0,
                y=y_left_top,
                z=machine_params.z_zero_tail_mm,
                speed_mm_s=machine_params.rapid_speed_mm_s,
                comment=f"Tail: move to pocket [{pocket_start_y:.3f}, {pocket_end_y:.3f}] left edge",
            )
        )
        commands.append(
            Command(
                type=CommandType.SET_LASER_POWER,
                power_pct=machine_params.cut_power_tail_pct,
                comment="Tail: laser on",
            )
        )
        commands.append(
            Command(
                type=CommandType.CUT_LINE,
                x=tail_depth_mm,
                y=y_left_bottom,
                speed_mm_s=machine_params.cut_speed_tail_mm_s,
                comment="Tail: left slope",
            )
        )
        commands.append(
            Command(
                type=CommandType.CUT_LINE,
                x=tail_depth_mm,
                y=y_right_bottom,
                speed_mm_s=machine_params.cut_speed_tail_mm_s,
                comment="Tail: bottom edge",
            )
        )
        commands.append(
            Command(
                type=CommandType.CUT_LINE,
                x=0.0,
                y=y_right_top,
                speed_mm_s=machine_params.cut_speed_tail_mm_s,
                comment="Tail: right slope",
            )
        )
        commands.append(
            Command(
                type=CommandType.CUT_LINE,
                x=0.0,
                y=y_left_top,
                speed_mm_s=machine_params.cut_speed_tail_mm_s,
                comment="Tail: close trapezoid",
            )
        )
        commands.append(
            Command(
                type=CommandType.SET_LASER_POWER,
                power_pct=machine_params.travel_power_pct,
                comment="Tail: laser off",
            )
        )

    # Return to a known origin after finishing tails.
    commands.append(
        Command(
            type=CommandType.MOVE,
            x=0.0,
            y=0.0,
            z=machine_params.z_zero_tail_mm,
            speed_mm_s=machine_params.rapid_speed_mm_s,
            comment="Tail: return to origin",
        )
    )

    return commands
