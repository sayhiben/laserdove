import math

from laserdove.geometry import compute_tail_layout
from laserdove.model import JointParams, JigParams, MachineParams
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

    y_far = cut_lines[1].y  # pocket span leg
    expected_half_gap = joint_params.tail_outer_width_mm / 2.0

    assert y_far > y_start
    assert math.isclose(abs(y_far - y_start), expected_half_gap, abs_tol=1e-6)
