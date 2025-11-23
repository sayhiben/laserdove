# tests/test_geometry.py
from geometry import compute_tail_layout, z_offset_for_angle
from model import JointParams


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


def test_tail_layout_basic():
    joint_params = make_joint()
    layout = compute_tail_layout(joint_params)
    assert len(layout.tail_centers_y) == joint_params.num_tails
    assert min(layout.tail_centers_y) > 0.0
    assert max(layout.tail_centers_y) < joint_params.edge_length_mm


def test_z_offset_zero_angle():
    axis_to_origin_mm = 30.0
    z_offset = z_offset_for_angle(
        y_b_mm=0.0,
        angle_deg=0.0,
        axis_to_origin_mm=axis_to_origin_mm,
    )
    assert abs(z_offset) < 1e-9
