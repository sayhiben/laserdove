from laserdove.geometry import compute_tail_layout
from laserdove.model import JointParams, JigParams, MachineParams
from laserdove.planner import compute_pin_plan, plan_pin_board


def test_pin_plan_stays_within_board_edges_when_zero_rotation():
    joint = JointParams(
        thickness_mm=6.0,
        edge_length_mm=76.0,
        dovetail_angle_deg=0.0,  # simplify projection so machine Y matches board Y
        num_tails=3,
        tail_outer_width_mm=20.0,
        tail_depth_mm=6.0,
        socket_depth_mm=6.1,
        clearance_mm=0.05,
        kerf_tail_mm=0.15,
        kerf_pin_mm=0.15,
    )
    jig = JigParams(axis_to_origin_mm=0.0, rotation_zero_deg=0.0, rotation_speed_dps=30.0)
    machine = MachineParams(
        cut_speed_tail_mm_s=8.0,
        cut_speed_pin_mm_s=8.0,
        rapid_speed_mm_s=200.0,
        z_speed_mm_s=5.0,
        cut_power_tail_pct=70.0,
        cut_power_pin_pct=70.0,
        travel_power_pct=0.0,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
        air_assist=True,
        z_positive_moves_bed_up=True,
    )

    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    ys = [cmd.y for cmd in commands if cmd.y is not None]
    assert ys, "Expected pin planner to emit Y-coordinates"
    assert min(ys) >= -1e-9
    assert max(ys) <= joint.edge_length_mm + 1e-9
