# planner.py
from __future__ import annotations

import math
from typing import List, Dict

from model import (
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
from geometry import (
    kerf_offset_boundary,
    z_offset_for_angle,
    center_outward_indices,
)


def plan_tail_board(
    jp: JointParams,
    mp: MachineParams,
    tail_layout: TailLayout,
) -> List[Command]:
    """
    v1 tail planner:
      - Treat each pin pocket as a rectangle from X=0..tail_depth_mm.
      - Cut its outline at kerf-offset Y positions.
      - Caller is responsible for board positioning and z_zero_tail_mm.
    """
    cmds: List[Command] = []

    L = jp.edge_length_mm
    N = jp.num_tails
    Wt = tail_layout.tail_outer_width
    Wp = tail_layout.pin_outer_width
    hp = tail_layout.half_pin_width
    D = jp.tail_depth_mm

    pockets: List[tuple[float, float]] = []

    # Left edge half-pin pocket
    pockets.append((0.0, hp))

    pitch = Wt + Wp
    for i in range(N - 1):
        y_start = hp + Wt + i * pitch
        y_end = y_start + Wp
        pockets.append((y_start, y_end))

    # Right edge half-pin pocket
    pockets.append((L - hp, L))

    for y0, y1 in pockets:
        # Tail board: keep is outside the pocket; waste is inside.
        y_left_cut = kerf_offset_boundary(
            y_geo=y0,
            kerf_mm=jp.kerf_tail_mm,
            clearance_mm=jp.clearance_mm,
            keep_on_positive_side=True,   # keep at Y > y0
            is_tail_board=True,
        )
        y_right_cut = kerf_offset_boundary(
            y_geo=y1,
            kerf_mm=jp.kerf_tail_mm,
            clearance_mm=jp.clearance_mm,
            keep_on_positive_side=False,  # keep at Y < y1
            is_tail_board=True,
        )

        cmds.append(Command(
            type=CommandType.MOVE,
            x=0.0,
            y=y_left_cut,
            z=mp.z_zero_tail_mm,
            speed_mm_s=mp.rapid_speed_mm_s,
            comment=f"Tail: move to pocket [{y0:.3f}, {y1:.3f}] left edge",
        ))
        cmds.append(Command(
            type=CommandType.SET_LASER_POWER,
            power_pct=mp.cut_power_tail_pct,
            comment="Tail: laser on",
        ))
        cmds.append(Command(
            type=CommandType.CUT_LINE,
            x=D,
            y=y_left_cut,
            speed_mm_s=mp.cut_speed_tail_mm_s,
            comment="Tail: left edge",
        ))
        cmds.append(Command(
            type=CommandType.CUT_LINE,
            x=D,
            y=y_right_cut,
            speed_mm_s=mp.cut_speed_tail_mm_s,
            comment="Tail: bottom edge",
        ))
        cmds.append(Command(
            type=CommandType.CUT_LINE,
            x=0.0,
            y=y_right_cut,
            speed_mm_s=mp.cut_speed_tail_mm_s,
            comment="Tail: right edge",
        ))
        cmds.append(Command(
            type=CommandType.CUT_LINE,
            x=0.0,
            y=y_left_cut,
            speed_mm_s=mp.cut_speed_tail_mm_s,
            comment="Tail: top close",
        ))
        cmds.append(Command(
            type=CommandType.SET_LASER_POWER,
            power_pct=mp.travel_power_pct,
            comment="Tail: laser off",
        ))

    return cmds


def compute_pin_plan(
    jp: JointParams,
    jg: JigParams,
    tail_layout: TailLayout,
) -> PinPlan:
    """
    Compute a simple pin plan:

    - Pins are the gaps between tails (plus half-pins at ends).
    - Each pin has two sides:
        LEFT  uses rotation_zero_deg - β
        RIGHT uses rotation_zero_deg + β
    - Z focus uses mid-thickness reference (delta-radius = 0 for v1).
    """
    N = jp.num_tails
    Wp = tail_layout.pin_outer_width
    hp = tail_layout.half_pin_width
    L = jp.edge_length_mm

    pin_centers_y: List[float] = []

    # Half-left pin center
    pin_centers_y.append(hp / 2.0)

    pitch = jp.tail_outer_width_mm + Wp
    for i in range(1, N):
        y_left = hp + jp.tail_outer_width_mm + (i - 1) * pitch
        y_right = y_left + Wp
        pin_centers_y.append(0.5 * (y_left + y_right))

    # Half-right pin center
    pin_centers_y.append(L - hp / 2.0)

    sides: List[PinSide] = []
    base_theta = jp.dovetail_angle_deg

    rotation_for_side: Dict[Side, float] = {
        Side.LEFT: jg.rotation_zero_deg - base_theta,
        Side.RIGHT: jg.rotation_zero_deg + base_theta,
    }

    for idx, center_y in enumerate(pin_centers_y):
        width = hp if idx in (0, len(pin_centers_y) - 1) else Wp
        y_left = center_y - width / 2.0
        y_right = center_y + width / 2.0

        # Convert outer-face Y to centered board coordinate Y_b (0 at mid-edge)
        y_center = L / 2.0
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
            theta = rotation_for_side[side]
            y_b_centered = y_for_side_centered[side]
            z_off = z_offset_for_angle(
                y_b_mm=y_b_centered,
                angle_deg=theta,
                h_mm=jg.axis_to_origin_mm,
            )
            sides.append(PinSide(
                pin_index=idx,
                side=side,
                y_boundary_mm=y_boundary_raw[side],
                rotation_deg=theta,
                z_offset_mm=z_off,
                x_depth_mm=jp.socket_depth_mm,
            ))

    return PinPlan(sides=sides)


def plan_pin_board(
    jp: JointParams,
    jg: JigParams,
    mp: MachineParams,
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
            perform an L-shaped cut (X-in, Y-ramp, X-out).
    """
    cmds: List[Command] = []

    # Group sides by angle
    sides_by_angle: Dict[float, List[PinSide]] = {}
    for s in pin_plan.sides:
        sides_by_angle.setdefault(s.rotation_deg, []).append(s)

    keep_on_positive_side: Dict[Side, bool] = {
        Side.LEFT: False,   # pin material at Y < boundary; keep negative side
        Side.RIGHT: True,   # pin material at Y > boundary; keep positive side
    }
    ramp_direction: Dict[Side, float] = {
        Side.LEFT: -1.0,
        Side.RIGHT: +1.0,
    }

    for theta_deg, sides in sides_by_angle.items():
        # Center-outward by Y
        idxs = center_outward_indices([s.y_boundary_mm for s in sides])
        ordered_sides = [sides[i] for i in idxs]

        cmds.append(Command(
            type=CommandType.ROTATE,
            angle_deg=theta_deg,
            speed_mm_s=jg.rotation_speed_dps,
            comment=f"Rotate jig to θ={theta_deg:.3f}°",
        ))

        for side in ordered_sides:
            target_z = mp.z_zero_pin_mm + side.z_offset_mm
            cmds.append(Command(
                type=CommandType.MOVE,
                z=target_z,
                speed_mm_s=mp.z_speed_mm_s,
                comment=f"Set Z for pin {side.pin_index} {side.side.name}",
            ))

            y_cut = kerf_offset_boundary(
                y_geo=side.y_boundary_mm,
                kerf_mm=jp.kerf_pin_mm,
                clearance_mm=jp.clearance_mm,
                keep_on_positive_side=keep_on_positive_side[side.side],
                is_tail_board=False,
            )

            cmds.append(Command(
                type=CommandType.MOVE,
                x=0.0,
                y=y_cut,
                speed_mm_s=mp.rapid_speed_mm_s,
                comment=f"Move to pin {side.pin_index} {side.side.name} at edge",
            ))

            cmds.append(Command(
                type=CommandType.SET_LASER_POWER,
                power_pct=mp.cut_power_pin_pct,
                comment="Pin: laser on",
            ))

            depth = side.x_depth_mm
            alpha = math.radians(jp.dovetail_angle_deg)
            sign = ramp_direction[side.side]
            delta_y = depth * math.tan(alpha) * sign
            y_ramp_end = y_cut + delta_y

            cmds.append(Command(
                type=CommandType.CUT_LINE,
                x=depth,
                y=y_cut,
                speed_mm_s=mp.cut_speed_pin_mm_s,
                comment="Pin: short X leg",
            ))
            cmds.append(Command(
                type=CommandType.CUT_LINE,
                x=depth,
                y=y_ramp_end,
                speed_mm_s=mp.cut_speed_pin_mm_s,
                comment="Pin: long Y leg",
            ))
            cmds.append(Command(
                type=CommandType.CUT_LINE,
                x=0.0,
                y=y_ramp_end,
                speed_mm_s=mp.cut_speed_pin_mm_s,
                comment="Pin: retract X",
            ))

            cmds.append(Command(
                type=CommandType.SET_LASER_POWER,
                power_pct=mp.travel_power_pct,
                comment="Pin: laser off",
            ))

    return cmds