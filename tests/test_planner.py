import math
import pytest

from laserdove.geometry import compute_tail_layout, kerf_offset_boundary
from laserdove.model import JointParams, JigParams, MachineParams, Side
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
    return JigParams(
        axis_to_origin_mm=30.0,
        rotation_zero_deg=0.0,
        rotation_speed_dps=30.0,
    )


def make_machine() -> MachineParams:
    return MachineParams(
        cut_speed_tail_mm_s=10.0,
        cut_speed_pin_mm_s=8.0,
        rapid_speed_mm_s=200.0,
        z_speed_mm_s=5.0,
        cut_power_tail_pct=60.0,
        cut_power_pin_pct=65.0,
        travel_power_pct=0.0,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
    )


def test_pin_cut_spans_half_gap():
    joint_params = make_joint()
    layout = compute_tail_layout(joint_params)
    jig_params = make_jig()
    machine_params = make_machine()

    pin_plan = compute_pin_plan(joint_params, jig_params, layout)
    commands = plan_pin_board(joint_params, jig_params, machine_params, pin_plan)

    move_index = next(
        index
        for index, command in enumerate(commands)
        if command.comment.startswith("Move to pin 0 RIGHT")
    )
    y_start = commands[move_index].y

    # Four CUT_LINE commands follow after laser-on.
    cut_lines = commands[move_index + 2 : move_index + 6]
    assert len(cut_lines) == 4

    y_far = cut_lines[1].y  # pocket span leg (already projected)
    cos_theta = math.cos(
        math.radians(jig_params.rotation_zero_deg + joint_params.dovetail_angle_deg)
    )
    expected_half_gap = (joint_params.tail_outer_width_mm / 2.0) * cos_theta

    assert y_far > y_start
    assert math.isclose(abs(y_far - y_start), expected_half_gap, abs_tol=1e-6)


def test_pin_y_positions_are_projected_for_rotation():
    joint_params = make_joint()
    # Increase angle to magnify projection effects.
    joint_params.dovetail_angle_deg = 30.0
    layout = compute_tail_layout(joint_params)
    jig_params = make_jig()
    machine_params = make_machine()

    pin_plan = compute_pin_plan(joint_params, jig_params, layout)
    commands = plan_pin_board(joint_params, jig_params, machine_params, pin_plan)

    # Build half-gap lookup matching planner logic.
    unique_boundaries = sorted({side.y_boundary_mm for side in pin_plan.sides})
    half_gap_by_side = {}
    for side in pin_plan.sides:
        idx = unique_boundaries.index(side.y_boundary_mm)
        if side.side == Side.LEFT:
            gap = (
                side.y_boundary_mm - unique_boundaries[idx - 1]
                if idx > 0
                else pin_plan.half_pin_width
            )
        else:
            gap = (
                unique_boundaries[idx + 1] - side.y_boundary_mm
                if idx + 1 < len(unique_boundaries)
                else pin_plan.half_pin_width
            )
        half_gap_by_side[(side.pin_index, side.side)] = gap / 2.0

    y_center = joint_params.edge_length_mm / 2.0
    rotation_deg = next(s.rotation_deg for s in pin_plan.sides if s.side == Side.RIGHT)
    delta_angle = rotation_deg - jig_params.rotation_zero_deg
    cos_theta = math.cos(math.radians(abs(delta_angle)))
    sin_theta = math.sin(math.radians(delta_angle))

    # Pick a right side to verify projection math.
    side = next(s for s in pin_plan.sides if s.side == Side.RIGHT)

    y_cut_board = kerf_offset_boundary(
        y_geo=side.y_boundary_mm,
        kerf_mm=joint_params.kerf_pin_mm,
        clearance_mm=joint_params.clearance_mm,
        keep_on_positive_side=False,
        is_tail_board=False,
    )
    half_gap = half_gap_by_side[(side.pin_index, side.side)]
    y_far_board = y_cut_board + half_gap

    def project_y(y_board: float) -> float:
        return (
            y_center + (y_board - y_center) * cos_theta - jig_params.axis_to_origin_mm * sin_theta
        )

    expected_y_move = project_y(y_cut_board)
    expected_y_span = project_y(y_far_board)

    move_cmd_index = next(
        idx
        for idx, cmd in enumerate(commands)
        if cmd.type.name == "MOVE"
        and cmd.y is not None
        and f"pin {side.pin_index} {side.side.name}" in (cmd.comment or "")
    )
    move_cmd = commands[move_cmd_index]
    span_cmd = next(
        cmd
        for cmd in commands[move_cmd_index:]
        if cmd.type.name == "CUT_LINE" and (cmd.comment or "").startswith("Pin: pocket span")
    )

    assert math.isclose(move_cmd.y, expected_y_move, abs_tol=1e-9)
    assert math.isclose(span_cmd.y, expected_y_span, abs_tol=1e-9)


@pytest.mark.parametrize("angle_deg", [0.0, 15.0, -15.0, 45.0])
def test_pin_projection_varies_with_angle(angle_deg: float):
    joint_params = make_joint()
    joint_params.dovetail_angle_deg = abs(angle_deg)
    layout = compute_tail_layout(joint_params)
    jig_params = make_jig()
    # Shift zero so negative angles still exercise delta logic.
    jig_params.rotation_zero_deg = -5.0
    machine_params = make_machine()

    pin_plan = compute_pin_plan(joint_params, jig_params, layout)
    commands = plan_pin_board(joint_params, jig_params, machine_params, pin_plan)

    y_center = joint_params.edge_length_mm / 2.0
    sides = [s for s in pin_plan.sides if s.side in (Side.LEFT, Side.RIGHT)]
    half_gap_by_side = {}
    unique_boundaries = sorted({side.y_boundary_mm for side in pin_plan.sides})
    for side in pin_plan.sides:
        idx = unique_boundaries.index(side.y_boundary_mm)
        if side.side == Side.LEFT:
            gap = (
                side.y_boundary_mm - unique_boundaries[idx - 1]
                if idx > 0
                else pin_plan.half_pin_width
            )
        else:
            gap = (
                unique_boundaries[idx + 1] - side.y_boundary_mm
                if idx + 1 < len(unique_boundaries)
                else pin_plan.half_pin_width
            )
        half_gap_by_side[(side.pin_index, side.side)] = gap / 2.0

    for side in sides:
        if side.side == Side.RIGHT and side.pin_index == 0:
            continue  # skip half-pin right to keep symmetry simple
        y_cut_board = kerf_offset_boundary(
            y_geo=side.y_boundary_mm,
            kerf_mm=joint_params.kerf_pin_mm,
            clearance_mm=joint_params.clearance_mm,
            keep_on_positive_side=side.side == Side.LEFT,
            is_tail_board=False,
        )
        half_gap = half_gap_by_side[(side.pin_index, side.side)]
        waste_sign = -1.0 if side.side == Side.LEFT else 1.0
        y_far_board = y_cut_board + waste_sign * half_gap

        delta_angle = side.rotation_deg - jig_params.rotation_zero_deg
        cos_theta = math.cos(math.radians(abs(delta_angle)))
        sin_theta = math.sin(math.radians(delta_angle))

        def project_y(y_board: float) -> float:
            return (
                y_center
                + (y_board - y_center) * cos_theta
                - jig_params.axis_to_origin_mm * sin_theta
            )

        expected_move = project_y(y_cut_board)
        expected_span = project_y(y_far_board)

        move_cmd_index = next(
            idx
            for idx, cmd in enumerate(commands)
            if cmd.type.name == "MOVE"
            and f"pin {side.pin_index} {side.side.name}" in (cmd.comment or "")
            and cmd.z is None
        )
        move_cmd = commands[move_cmd_index]
        span_cmd = next(
            cmd
            for cmd in commands[move_cmd_index:]
            if cmd.type.name == "CUT_LINE" and (cmd.comment or "").startswith("Pin: pocket span")
        )

        assert math.isclose(move_cmd.y, expected_move, abs_tol=1e-9)
        assert math.isclose(span_cmd.y, expected_span, abs_tol=1e-9)
