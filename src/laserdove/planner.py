# planner.py
from __future__ import annotations

import math
from typing import List, Dict

from .model import (
    JointParams,
    JigParams,
    MachineParams,
    TailLayout,
    PinPlan,
    PinSide,
    Side,
    Command,
    CommandType,
)
from .geometry import (
    kerf_offset_boundary,
    z_offset_for_angle,
)


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
            keep_on_positive_side=True,  # keep at Y > y0
            is_tail_board=True,
        )
        y_right_top = kerf_offset_boundary(
            y_geo=pocket_end_y,
            kerf_mm=joint_params.kerf_tail_mm,
            clearance_mm=joint_params.clearance_mm,
            keep_on_positive_side=False,  # keep at Y < y1
            is_tail_board=True,
        )
        y_left_bottom = y_left_top - tail_widen_mm
        y_right_bottom = y_right_top + tail_widen_mm

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


def compute_pin_plan(
    joint_params: JointParams,
    jig_params: JigParams,
    tail_layout: TailLayout,
) -> PinPlan:
    """
    Compute pin flank rotations, Z offsets, and boundaries.

    Pins are the gaps between tails (plus half-pins at ends). Each pin has two
    sides: LEFT at rotation_zero_deg - β and RIGHT at rotation_zero_deg + β.

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

    sides: List[PinSide] = []
    dovetail_angle_deg = joint_params.dovetail_angle_deg

    rotation_for_side: Dict[Side, float] = {
        Side.LEFT: jig_params.rotation_zero_deg - dovetail_angle_deg,
        Side.RIGHT: jig_params.rotation_zero_deg + dovetail_angle_deg,
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
            # Flip Y across 0° so Z offsets keep a consistent sign convention per tilt.
            y_b_centered = y_for_side_centered[side]
            if rotation_deg > 0:
                y_b_centered = -y_b_centered
            z_offset = z_offset_for_angle(
                y_b_mm=y_b_centered,
                angle_deg=rotation_deg,
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
    Plan pin cuts:

    - Group sides by rotation angle.
    - For each angle:
        rotate once
        for each side in center-outward order:
            set Z
            move XY to kerf-adjusted Y at X=0
            cut a closed rectangle spanning half the gap to the neighboring pin.

    Args:
        joint_params: Joint geometry and kerf/clearance parameters.
        jig_params: Rotary geometry and speed hints.
        machine_params: Machine/process parameters (speeds, powers, Z zeros).
        pin_plan: Pin flank plan from compute_pin_plan.

    Returns:
        Ordered Command list for cutting all pins and returning to origin.
    """
    commands: List[Command] = []

    # Group sides by angle
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

    current_y = 0.0  # track last projected Y to order cuts and reduce long travel moves
    y_center = joint_params.edge_length_mm / 2.0

    for rotation_deg, sides in sides_by_angle.items():
        # Project board Y coordinates into machine Y with foreshortening at this angle.
        delta_angle_deg = abs(rotation_deg - jig_params.rotation_zero_deg)
        cos_theta = math.cos(math.radians(delta_angle_deg))

        def project_y(y_board: float) -> float:
            """Apply orthographic foreshortening about the board midline."""
            return y_center + (y_board - y_center) * cos_theta

        # Nearest-neighbor ordering from current_y to reduce travel swings.
        remaining = sides[:]
        ordered_sides: List[PinSide] = []
        cursor = current_y
        while remaining:
            next_index = min(
                range(len(remaining)),
                key=lambda i: abs(project_y(remaining[i].y_boundary_mm) - cursor),
            )
            next_side = remaining.pop(next_index)
            ordered_sides.append(next_side)
            cursor = project_y(next_side.y_boundary_mm)

        commands.append(
            Command(
                type=CommandType.ROTATE,
                angle_deg=rotation_deg,
                speed_mm_s=jig_params.rotation_speed_dps,
                comment=f"Rotate jig to θ={rotation_deg:.3f}°",
            )
        )

        for side in ordered_sides:
            target_z = machine_params.z_zero_pin_mm + side.z_offset_mm
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
            y_cut_projected = project_y(y_cut)

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
            half_gap = half_gap_by_side[(side.pin_index, side.side)]
            waste_sign = -1.0 if keep_on_positive_side[side.side] else 1.0
            y_far = y_cut + waste_sign * half_gap
            y_far_projected = project_y(y_far)

            commands.append(
                Command(
                    type=CommandType.CUT_LINE,
                    x=cut_depth,
                    y=y_cut_projected,
                    speed_mm_s=machine_params.cut_speed_pin_mm_s,
                    comment="Pin: plunge to depth",
                )
            )
            commands.append(
                Command(
                    type=CommandType.CUT_LINE,
                    x=cut_depth,
                    y=y_far_projected,
                    speed_mm_s=machine_params.cut_speed_pin_mm_s,
                    comment="Pin: pocket span",
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
            current_y = y_cut_projected

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
