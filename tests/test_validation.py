from laserdove.geometry import compute_tail_layout
from laserdove.model import JointParams, JigParams, MachineParams, TailLayout
from laserdove.validation import (
    validate_joint_params,
    validate_tail_layout,
    validate_machine_limits,
    validate_jig,
    validate_all,
)


def make_joint(**overrides) -> JointParams:
    params = dict(
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
    params.update(overrides)
    return JointParams(**params)


def make_jig(**overrides) -> JigParams:
    params = dict(
        axis_to_origin_mm=30.0,
        rotation_zero_deg=0.0,
        rotation_speed_dps=30.0,
    )
    params.update(overrides)
    return JigParams(**params)


def make_machine(**overrides) -> MachineParams:
    params = dict(
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
    params.update(overrides)
    return MachineParams(**params)


def test_validate_joint_params_flags_invalid_values():
    joint = make_joint(num_tails=0, edge_length_mm=0, dovetail_angle_deg=90, kerf_tail_mm=0)
    errors = validate_joint_params(joint)
    assert any("num_tails" in e for e in errors)
    assert any("edge_length_mm" in e for e in errors)
    assert any("dovetail_angle_deg" in e for e in errors)
    assert any("kerf_tail_mm" in e for e in errors)


def test_validate_tail_layout_bounds_and_pin_width():
    joint = make_joint(edge_length_mm=10.0, tail_outer_width_mm=8.0, num_tails=1)
    layout = TailLayout(
        tail_centers_y=[1.0], tail_outer_width=8.0, pin_outer_width=-1.0, half_pin_width=0.5
    )
    errors = validate_tail_layout(joint, layout)
    assert any("outside edge" in e for e in errors)
    assert any("pin_outer_width" in e for e in errors)


def test_validate_machine_limits_and_jig_limits():
    machine = make_machine(cut_speed_tail_mm_s=0, cut_power_pin_pct=200)
    jig = make_jig(axis_to_origin_mm=-1)
    machine_errors = validate_machine_limits(machine)
    jig_errors = validate_jig(jig)
    assert any("Cut speeds" in e for e in machine_errors)
    assert any("cut_power_pin_pct" in e for e in machine_errors)
    assert any("axis_to_origin_mm" in e for e in jig_errors)


def test_validate_all_collects_errors():
    joint = make_joint(num_tails=0)
    jig = make_jig(axis_to_origin_mm=0)
    machine = make_machine(cut_speed_tail_mm_s=0)
    layout = compute_tail_layout(make_joint())
    errors = validate_all(joint, jig, machine, layout)
    assert len(errors) >= 3


def test_validate_tail_layout_returns_early_for_empty_layout():
    joint = make_joint()
    empty_layout = TailLayout(
        tail_centers_y=[], tail_outer_width=1.0, pin_outer_width=1.0, half_pin_width=0.5
    )
    errors = validate_tail_layout(joint, empty_layout)
    assert errors and "tail_centers_y is empty" in errors[0]
