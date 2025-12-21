# tests for planner.py
import math

import pytest

from laserdove.geometry import compute_tail_layout, kerf_offset_boundary
from laserdove.model import (
    CommandType,
    JigParams,
    JointParams,
    MachineParams,
    PinPlan,
    PinSide,
    Side,
)
from laserdove.planner import compute_pin_plan, plan_pin_board


def make_joint() -> JointParams:
    return JointParams(
        thickness_mm=6.35,
        edge_length_mm=100.0,
        dovetail_angle_deg=8.0,
        num_tails=3,
        tail_outer_width_mm=20.0,
        tail_depth_mm=6.35,
        socket_depth_mm=6.6,
        clearance_mm=0.05,
        kerf_tail_mm=0.15,
        kerf_pin_mm=0.15,
    )


def make_jig() -> JigParams:
    return JigParams(axis_to_origin_mm=32.5, rotation_zero_deg=0.0, rotation_speed_dps=30.0)


def make_machine() -> MachineParams:
    return MachineParams(
        cut_speed_tail_mm_s=10.0,
        cut_speed_pin_mm_s=8.0,
        rapid_speed_mm_s=200.0,
        z_speed_mm_s=5.0,
        cut_power_tail_pct=60.0,
        cut_power_pin_pct=65.0,
        travel_power_pct=0.0,
        cut_overtravel_mm=0.5,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
    )


def _projected_y(y_cut: float, rotation_deg: float, jig: JigParams, edge_length: float) -> float:
    delta_angle = rotation_deg - jig.rotation_zero_deg
    cos_theta = math.cos(math.radians(abs(delta_angle)))
    sin_theta = math.sin(math.radians(delta_angle))
    y_center = edge_length / 2.0
    return y_center + (y_cut - y_center) * cos_theta - jig.axis_to_origin_mm * sin_theta


def _find_move_y(commands, pin_index: int, side: Side) -> float:
    target_comment = f"Move to pin {pin_index} {side.name} at edge"
    for cmd in commands:
        if cmd.comment == target_comment and cmd.type == CommandType.MOVE and cmd.y is not None:
            return cmd.y
    raise AssertionError(f"Command with comment '{target_comment}' not found")


def _find_pocket_span_y(commands, pin_index: int, side: Side) -> float:
    target_comment = f"Move to pin {pin_index} {side.name} at edge"
    saw_target = False
    for cmd in commands:
        if cmd.comment == target_comment and cmd.type == CommandType.MOVE:
            saw_target = True
            continue
        if (
            saw_target
            and cmd.comment == "Pin: pocket span (with overtravel)"
            and cmd.type == CommandType.CUT_LINE
            and cmd.y is not None
        ):
            return cmd.y
    raise AssertionError(f"Pocket span for pin {pin_index} {side.name} not found")


def test_pin_rotation_orientation_widens_at_bottom():
    joint = make_joint()
    jig = make_jig()
    tail_layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, tail_layout)

    # Use a full-width interior pin to avoid half-pin edge cases.
    left_side = next(s for s in pin_plan.sides if s.pin_index == 1 and s.side == Side.LEFT)
    right_side = next(s for s in pin_plan.sides if s.pin_index == 1 and s.side == Side.RIGHT)

    assert left_side.rotation_deg == pytest.approx(jig.rotation_zero_deg + joint.dovetail_angle_deg)
    assert right_side.rotation_deg == pytest.approx(
        jig.rotation_zero_deg - joint.dovetail_angle_deg
    )

    # With left rotated +β and right rotated -β, the pin should be wider at the bottom surface.
    delta_left = math.tan(math.radians(left_side.rotation_deg - jig.rotation_zero_deg))
    delta_right = math.tan(math.radians(right_side.rotation_deg - jig.rotation_zero_deg))
    y_bottom_left = left_side.y_boundary_mm - joint.thickness_mm * delta_left
    y_bottom_right = right_side.y_boundary_mm - joint.thickness_mm * delta_right
    top_width = right_side.y_boundary_mm - left_side.y_boundary_mm
    bottom_width = y_bottom_right - y_bottom_left
    expected_delta = 2.0 * joint.thickness_mm * math.tan(math.radians(joint.dovetail_angle_deg))

    assert bottom_width > top_width
    assert bottom_width == pytest.approx(top_width + expected_delta)


def test_pin_projection_accounts_for_axis_translation_per_rotation():
    joint = make_joint()
    jig = make_jig()
    machine = make_machine()
    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    # Use an interior pin to avoid clamp-edge adjustments.
    target_pin_index = 2
    keep_positive = {Side.LEFT: True, Side.RIGHT: False}

    for side in (Side.LEFT, Side.RIGHT):
        pin_side = next(
            s for s in pin_plan.sides if s.pin_index == target_pin_index and s.side == side
        )
        y_cut = kerf_offset_boundary(
            y_geo=pin_side.y_boundary_mm,
            kerf_mm=joint.kerf_pin_mm,
            clearance_mm=joint.clearance_mm,
            keep_on_positive_side=keep_positive[side],
            is_tail_board=False,
        )
        y_cut_clamped = max(0.0, min(joint.edge_length_mm, y_cut))
        expected_y = _projected_y(
            y_cut=y_cut_clamped,
            rotation_deg=pin_side.rotation_deg,
            jig=jig,
            edge_length=joint.edge_length_mm,
        )
        move_y = _find_move_y(commands, pin_side.pin_index, side)
        assert move_y == pytest.approx(expected_y, rel=1e-9, abs=1e-9)


def test_pin_projection_foreshortens_spans_about_midline():
    joint = make_joint()
    jig = make_jig()
    machine = make_machine()
    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    left_sides = sorted(
        (s for s in pin_plan.sides if s.side == Side.LEFT),
        key=lambda s: s.y_boundary_mm,
    )
    inner_left_sides = left_sides[1:3]  # skip the edge half-pin to avoid clamp effects
    assert len(inner_left_sides) == 2

    y_cuts = []
    for pin_side in inner_left_sides:
        y_cut = kerf_offset_boundary(
            y_geo=pin_side.y_boundary_mm,
            kerf_mm=joint.kerf_pin_mm,
            clearance_mm=joint.clearance_mm,
            keep_on_positive_side=True,
            is_tail_board=False,
        )
        y_cuts.append(y_cut)

    board_delta = y_cuts[1] - y_cuts[0]
    theta = math.radians(abs(inner_left_sides[0].rotation_deg - jig.rotation_zero_deg))
    expected_machine_delta = board_delta * math.cos(theta)

    move_y_low = _find_move_y(commands, inner_left_sides[0].pin_index, Side.LEFT)
    move_y_high = _find_move_y(commands, inner_left_sides[1].pin_index, Side.LEFT)
    machine_delta = move_y_high - move_y_low

    assert machine_delta == pytest.approx(expected_machine_delta, rel=1e-9, abs=1e-9)


def test_pin_pocket_span_compensates_for_shear_overlap():
    joint = make_joint()
    jig = make_jig()
    machine = make_machine()
    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    right_side = next(s for s in pin_plan.sides if s.pin_index == 0 and s.side == Side.RIGHT)
    left_side = next(s for s in pin_plan.sides if s.pin_index == 1 and s.side == Side.LEFT)

    y_cut_right = kerf_offset_boundary(
        y_geo=right_side.y_boundary_mm,
        kerf_mm=joint.kerf_pin_mm,
        clearance_mm=joint.clearance_mm,
        keep_on_positive_side=False,
        is_tail_board=False,
    )
    y_cut_left = kerf_offset_boundary(
        y_geo=left_side.y_boundary_mm,
        kerf_mm=joint.kerf_pin_mm,
        clearance_mm=joint.clearance_mm,
        keep_on_positive_side=True,
        is_tail_board=False,
    )

    gap = left_side.y_boundary_mm - right_side.y_boundary_mm
    shear = joint.thickness_mm * math.tan(math.radians(joint.dovetail_angle_deg))
    half_gap = gap / 2.0 + shear

    y_far_right = y_cut_right + half_gap
    y_far_left = y_cut_left - half_gap

    expected_right = _projected_y(
        y_cut=y_far_right,
        rotation_deg=right_side.rotation_deg,
        jig=jig,
        edge_length=joint.edge_length_mm,
    )
    expected_left = _projected_y(
        y_cut=y_far_left,
        rotation_deg=left_side.rotation_deg,
        jig=jig,
        edge_length=joint.edge_length_mm,
    )

    actual_right = _find_pocket_span_y(commands, right_side.pin_index, Side.RIGHT)
    actual_left = _find_pocket_span_y(commands, left_side.pin_index, Side.LEFT)

    assert actual_right == pytest.approx(expected_right, rel=1e-9, abs=1e-9)
    assert actual_left == pytest.approx(expected_left, rel=1e-9, abs=1e-9)


@pytest.mark.parametrize(
    ("axis_to_origin_mm", "rotation_deg"),
    [
        (0.0, 0.0),
        (100.0, 0.0),
        (0.0, 45.0),
        (100.0, 45.0),
        (100.0, 90.0),
        (100.0, 33.0),
        (100.0, -33.0),
        (100.0, -45.0),
        (32.5, 33.0),
        (32.5, -33.0),  # verify translation sign flips with rotation direction
    ],
)
def test_pin_projection_varied_heights_and_angles(axis_to_origin_mm: float, rotation_deg: float):
    joint = make_joint()
    jig = JigParams(
        axis_to_origin_mm=axis_to_origin_mm, rotation_zero_deg=0.0, rotation_speed_dps=30.0
    )
    machine = make_machine()
    mid_y = joint.edge_length_mm / 2.0

    pin_plan = PinPlan(
        sides=[
            PinSide(
                pin_index=0,
                side=Side.LEFT,
                y_boundary_mm=mid_y,
                rotation_deg=rotation_deg,
                z_offset_mm=0.0,
                x_depth_mm=joint.socket_depth_mm,
            )
        ],
        pin_outer_width=joint.tail_outer_width_mm,
        half_pin_width=joint.tail_outer_width_mm / 2.0,
    )
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    move_y = _find_move_y(commands, 0, Side.LEFT)
    y_cut = kerf_offset_boundary(
        y_geo=mid_y,
        kerf_mm=joint.kerf_pin_mm,
        clearance_mm=joint.clearance_mm,
        keep_on_positive_side=True,
        is_tail_board=False,
    )
    expected_y = _projected_y(y_cut, rotation_deg, jig, joint.edge_length_mm)
    assert move_y == pytest.approx(expected_y, rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("rotation_deg", [0.0, 33.0, 45.0, 90.0])
def test_pin_projection_compression_at_multiple_angles(rotation_deg: float):
    joint = make_joint()
    jig = JigParams(axis_to_origin_mm=20.0, rotation_zero_deg=0.0, rotation_speed_dps=30.0)
    machine = make_machine()
    y_center = joint.edge_length_mm / 2.0
    offsets = (-15.0, 15.0)

    sides = [
        PinSide(
            pin_index=i,
            side=Side.LEFT,
            y_boundary_mm=y_center + offset,
            rotation_deg=rotation_deg,
            z_offset_mm=0.0,
            x_depth_mm=joint.socket_depth_mm,
        )
        for i, offset in enumerate(offsets)
    ]
    pin_plan = PinPlan(
        sides=sides,
        pin_outer_width=joint.tail_outer_width_mm,
        half_pin_width=joint.tail_outer_width_mm / 2.0,
    )
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    y_cuts = [
        kerf_offset_boundary(
            y_geo=sides[i].y_boundary_mm,
            kerf_mm=joint.kerf_pin_mm,
            clearance_mm=joint.clearance_mm,
            keep_on_positive_side=True,
            is_tail_board=False,
        )
        for i in range(len(sides))
    ]
    board_delta = y_cuts[1] - y_cuts[0]
    expected_delta = board_delta * math.cos(math.radians(abs(rotation_deg)))

    move_low = _find_move_y(commands, 0, Side.LEFT)
    move_high = _find_move_y(commands, 1, Side.LEFT)
    measured_delta = move_high - move_low

    assert measured_delta == pytest.approx(expected_delta, rel=1e-9, abs=1e-9)
