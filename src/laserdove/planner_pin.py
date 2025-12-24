from __future__ import annotations

import math
from typing import Dict, List

from .geometry import kerf_offset_boundary, z_offset_for_angle
from .model import (
    Command,
    CommandType,
    JigParams,
    JointParams,
    MachineParams,
    PinPlan,
    PinSide,
    Side,
    TailLayout,
)
from .planner_math import clamp_board_y, project_board_y


def compute_pin_plan(
    joint_params: JointParams,
    jig_params: JigParams,
    tail_layout: TailLayout,
) -> PinPlan:
    """
    Compute pin flank rotations, Z offsets, and boundaries.

    Pins are the gaps between tails (plus half-pins at ends). Each pin has two
    sides: LEFT at rotation_zero_deg + β and RIGHT at rotation_zero_deg - β
    so the pin widens toward the bottom of the mounted board.

    Args:
        joint_params: Joint geometry and kerf/clearance parameters.
        jig_params: Rotary geometry and speed hints.
        tail_layout: Previously computed tail spacing.

    Returns:
        PinPlan enumerating all pin flanks with rotations and Z offsets.
    """
    num_tails = joint_params.num_tails
    pin_outer_width = tail_layout.pin_outer_width
    half_pin_width = tail_layout.half_pin_width
    edge_length_mm = joint_params.edge_length_mm

    pin_centers_y: List[float] = []

    # Half-left pin center
    pin_centers_y.append(half_pin_width / 2.0)

    tail_pin_pitch = joint_params.tail_outer_width_mm + pin_outer_width
    for pin_index in range(1, num_tails):
        y_left = (
            half_pin_width + joint_params.tail_outer_width_mm + (pin_index - 1) * tail_pin_pitch
        )
        y_right = y_left + pin_outer_width
        pin_centers_y.append(0.5 * (y_left + y_right))

    # Half-right pin center
    pin_centers_y.append(edge_length_mm - half_pin_width / 2.0)

    dovetail_angle_deg = joint_params.dovetail_angle_deg

    sides: List[PinSide] = []

    rotation_for_side: Dict[Side, float] = {
        Side.LEFT: jig_params.rotation_zero_deg + dovetail_angle_deg,
        Side.RIGHT: jig_params.rotation_zero_deg - dovetail_angle_deg,
    }

    for pin_index, center_y in enumerate(pin_centers_y):
        width = half_pin_width if pin_index in (0, len(pin_centers_y) - 1) else pin_outer_width
        y_left = center_y - width / 2.0
        y_right = center_y + width / 2.0

        # Convert outer-face Y to centered board coordinate Y_b (0 at mid-edge)
        y_center = edge_length_mm / 2.0
        y_b_left_centered = y_left - y_center
        y_b_right_centered = y_right - y_center

        y_for_side_centered: Dict[Side, float] = {
            Side.LEFT: y_b_left_centered,
            Side.RIGHT: y_b_right_centered,
        }
        y_boundary_raw: Dict[Side, float] = {
            Side.LEFT: y_left,
            Side.RIGHT: y_right,
        }

        for side in (Side.LEFT, Side.RIGHT):
            rotation_deg = rotation_for_side[side]
            delta_angle_signed = rotation_deg - jig_params.rotation_zero_deg
            y_b_centered = y_for_side_centered[side]
            z_offset = z_offset_for_angle(
                y_b_mm=y_b_centered,
                angle_deg=delta_angle_signed,
                axis_to_origin_mm=jig_params.axis_to_origin_mm,
            )
            sides.append(
                PinSide(
                    pin_index=pin_index,
                    side=side,
                    y_boundary_mm=y_boundary_raw[side],
                    rotation_deg=rotation_deg,
                    z_offset_mm=z_offset,
                    x_depth_mm=joint_params.socket_depth_mm,
                )
            )

    return PinPlan(
        sides=sides,
        pin_outer_width=pin_outer_width,
        half_pin_width=half_pin_width,
    )


def plan_pin_board(
    joint_params: JointParams,
    jig_params: JigParams,
    machine_params: MachineParams,
    pin_plan: PinPlan,
) -> List[Command]:
    """
    Plan pin cuts (rotary flanks) as rectangles on each pin boundary.

    Args:
        joint_params: Joint geometry and kerf/clearance parameters.
        jig_params: Rotary geometry and speed hints.
        machine_params: Machine/process parameters (speeds, powers, Z zeros).
        pin_plan: PinPlan with sides/rotations.

    Returns:
        Ordered Command list to cut all pins and return to origin/zero.
    """
    commands: List[Command] = []

    # Group pin sides by rotation angle.
    sides_by_angle: Dict[float, List[PinSide]] = {}
    for side in pin_plan.sides:
        sides_by_angle.setdefault(side.rotation_deg, []).append(side)

    # Pre-compute half-span to the neighboring boundary in the waste direction.
    # Each flank will clear a rectangular pocket of this half-gap width.
    unique_boundaries = sorted({side.y_boundary_mm for side in pin_plan.sides})
    half_pin_width = pin_plan.half_pin_width

    half_gap_by_side: Dict[tuple[int, Side], float] = {}
    for side in pin_plan.sides:
        idx = unique_boundaries.index(side.y_boundary_mm)
        if side.side == Side.LEFT:
            # Waste toward negative Y; neighbor is previous boundary or edge half-pin.
            if idx > 0:
                gap = side.y_boundary_mm - unique_boundaries[idx - 1]
            else:
                gap = half_pin_width
        else:
            # Waste toward positive Y; neighbor is next boundary or edge half-pin.
            if idx + 1 < len(unique_boundaries):
                gap = unique_boundaries[idx + 1] - side.y_boundary_mm
            else:
                gap = half_pin_width
        half_gap_by_side[(side.pin_index, side.side)] = gap / 2.0

    keep_on_positive_side: Dict[Side, bool] = {
        Side.LEFT: True,  # pin material at Y > boundary; keep positive side
        Side.RIGHT: False,  # pin material at Y < boundary; keep negative side
    }

    edge_length = joint_params.edge_length_mm

    def is_edge_boundary(y_val: float) -> bool:
        return math.isclose(y_val, 0.0, abs_tol=1e-9) or math.isclose(
            y_val, edge_length, abs_tol=1e-9
        )

    for rotation_deg, sides in sides_by_angle.items():
        # Skip board-edge boundaries so half-pins are bounded by the stock edge.
        sides = [side for side in sides if not is_edge_boundary(side.y_boundary_mm)]
        if not sides:
            continue
        delta_angle_signed = rotation_deg - jig_params.rotation_zero_deg
        angle_rad = math.radians(delta_angle_signed)
        shear_overlap = abs(joint_params.thickness_mm * math.tan(angle_rad)) if angle_rad else 0.0

        # Start each rotation block with the maximum clearance position.
        # Lower Z offsets mean the bed is farther from the head (safer).
        def z_order(side: PinSide) -> float:
            return side.z_offset_mm

        ordered_sides = sorted(
            sides,
            key=lambda side: (
                z_order(side),
                project_board_y(
                    side.y_boundary_mm,
                    edge_length_mm=edge_length,
                    axis_to_origin_mm=jig_params.axis_to_origin_mm,
                    rotation_deg=rotation_deg,
                    rotation_zero_deg=jig_params.rotation_zero_deg,
                ),
            ),
        )

        commands.append(
            Command(
                type=CommandType.ROTATE,
                angle_deg=rotation_deg,
                speed_mm_s=jig_params.rotation_speed_dps,
                comment=f"Rotate jig to θ={rotation_deg:.3f}°",
            )
        )

        for side in ordered_sides:
            z_offset = side.z_offset_mm
            if not machine_params.z_positive_moves_bed_up:
                z_offset = -z_offset
            target_z = machine_params.z_zero_pin_mm + z_offset
            commands.append(
                Command(
                    type=CommandType.MOVE,
                    z=target_z,
                    speed_mm_s=machine_params.z_speed_mm_s,
                    comment=f"Set Z for pin {side.pin_index} {side.side.name}",
                )
            )

            y_cut = kerf_offset_boundary(
                y_geo=side.y_boundary_mm,
                kerf_mm=joint_params.kerf_pin_mm,
                clearance_mm=joint_params.clearance_mm,
                keep_on_positive_side=keep_on_positive_side[side.side],
                is_tail_board=False,
            )
            y_cut = clamp_board_y(y_cut, edge_length)
            y_cut_projected = project_board_y(
                y_cut,
                edge_length_mm=edge_length,
                axis_to_origin_mm=jig_params.axis_to_origin_mm,
                rotation_deg=rotation_deg,
                rotation_zero_deg=jig_params.rotation_zero_deg,
            )

            commands.append(
                Command(
                    type=CommandType.MOVE,
                    x=0.0,
                    y=y_cut_projected,
                    speed_mm_s=machine_params.rapid_speed_mm_s,
                    comment=f"Move to pin {side.pin_index} {side.side.name} at edge",
                )
            )

            commands.append(
                Command(
                    type=CommandType.SET_LASER_POWER,
                    power_pct=machine_params.cut_power_pin_pct,
                    comment="Pin: laser on",
                )
            )

            cut_depth = side.x_depth_mm
            half_gap_base = half_gap_by_side[(side.pin_index, side.side)]
            # Expand half-gap so opposing rotations overlap through the stock thickness.
            gap_mm = half_gap_base * 2.0
            half_gap = min(half_gap_base + shear_overlap, gap_mm)
            boundary_y = side.y_boundary_mm
            at_left_edge = math.isclose(boundary_y, 0.0, abs_tol=1e-9)
            at_right_edge = math.isclose(boundary_y, edge_length, abs_tol=1e-9)
            waste_sign = -1.0 if keep_on_positive_side[side.side] else 1.0
            # Edge half-pins should pocket toward the material, not off the board.
            if at_left_edge:
                waste_sign = 1.0
            elif at_right_edge:
                waste_sign = -1.0

            y_far = clamp_board_y(y_cut + waste_sign * half_gap, edge_length)
            if math.isclose(y_far, y_cut, abs_tol=1e-9):
                # If clamping collapsed the span, nudge toward the interior so the pocket exists.
                epsilon = min(half_gap, edge_length * 0.01)
                y_far = clamp_board_y(y_cut + waste_sign * epsilon, edge_length)
            y_far_projected = project_board_y(
                y_far,
                edge_length_mm=edge_length,
                axis_to_origin_mm=jig_params.axis_to_origin_mm,
                rotation_deg=rotation_deg,
                rotation_zero_deg=jig_params.rotation_zero_deg,
            )

            commands.append(
                Command(
                    type=CommandType.CUT_LINE,
                    x=cut_depth + machine_params.cut_overtravel_mm,
                    y=y_cut_projected,
                    speed_mm_s=machine_params.cut_speed_pin_mm_s,
                    comment="Pin: plunge to depth (with overtravel)",
                )
            )
            commands.append(
                Command(
                    type=CommandType.CUT_LINE,
                    x=cut_depth + machine_params.cut_overtravel_mm,
                    y=y_far_projected,
                    speed_mm_s=machine_params.cut_speed_pin_mm_s,
                    comment="Pin: pocket span (with overtravel)",
                )
            )
            commands.append(
                Command(
                    type=CommandType.CUT_LINE,
                    x=0.0,
                    y=y_far_projected,
                    speed_mm_s=machine_params.cut_speed_pin_mm_s,
                    comment="Pin: retract X",
                )
            )
            commands.append(
                Command(
                    type=CommandType.CUT_LINE,
                    x=0.0,
                    y=y_cut_projected,
                    speed_mm_s=machine_params.cut_speed_pin_mm_s,
                    comment="Pin: close rectangle",
                )
            )

            commands.append(
                Command(
                    type=CommandType.SET_LASER_POWER,
                    power_pct=machine_params.travel_power_pct,
                    comment="Pin: laser off",
                )
            )

    # Return rotary and head to zeroed positions after pins.
    commands.append(
        Command(
            type=CommandType.ROTATE,
            angle_deg=jig_params.rotation_zero_deg,
            speed_mm_s=jig_params.rotation_speed_dps,
            comment="Rotate jig back to zero",
        )
    )
    commands.append(
        Command(
            type=CommandType.MOVE,
            x=0.0,
            y=0.0,
            z=machine_params.z_zero_pin_mm,
            speed_mm_s=machine_params.rapid_speed_mm_s,
            comment="Pin: return to origin",
        )
    )

    return commands
