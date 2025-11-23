# tests/test_tail_trapezoid.py
from planner import plan_tail_board
from geometry import compute_tail_layout
from model import JointParams, MachineParams, TailLayout


def make_joint_and_machine() -> tuple[JointParams, MachineParams, TailLayout]:
    joint = JointParams(
        thickness_mm=6.35,
        edge_length_mm=100.0,
        dovetail_angle_deg=8.0,
        num_tails=2,
        tail_outer_width_mm=20.0,
        tail_depth_mm=6.35,
        socket_depth_mm=6.6,
        clearance_mm=0.05,
        kerf_tail_mm=0.15,
        kerf_pin_mm=0.15,
    )
    machine = MachineParams(
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
    layout = compute_tail_layout(joint)
    return joint, machine, layout


def test_tail_pocket_widens_with_angle():
    joint, machine, layout = make_joint_and_machine()
    commands = plan_tail_board(joint, machine, layout)

    # First pocket command sequence after MOVE and power on:
    # A (move) -> B -> C -> D -> A
    move_cmd = commands[0]          # A: (0, y_left_top)
    left_slope_cmd = commands[2]    # B: (tail_depth, y_left_bottom)
    bottom_edge_cmd = commands[3]   # C: (tail_depth, y_right_bottom)
    right_slope_cmd = commands[4]   # D: (0, y_right_top)

    top_width = right_slope_cmd.y - move_cmd.y
    bottom_width = bottom_edge_cmd.y - left_slope_cmd.y
    assert bottom_width > top_width  # trapezoid widens toward depth

    import math

    expected_delta = joint.tail_depth_mm * math.tan(math.radians(joint.dovetail_angle_deg))
    measured_delta_left = move_cmd.y - left_slope_cmd.y
    measured_delta_right = bottom_edge_cmd.y - right_slope_cmd.y
    tolerance = 1e-6
    assert abs(measured_delta_left - expected_delta) < tolerance
    assert abs(measured_delta_right - expected_delta) < tolerance
