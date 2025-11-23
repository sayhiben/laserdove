# tests/test_geometry.py
from laserdove.geometry import compute_tail_layout, z_offset_for_angle, kerf_offset_boundary
from laserdove.model import JointParams


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


def test_kerf_offset_moves_boundary_toward_keep_side_with_clearance():
    y_geo = 0.0
    kerf_mm = 0.2
    clearance_mm = 0.1
    kerf_radius = kerf_mm / 2.0
    clearance_shift = clearance_mm / 2.0

    # Keep on positive side: cut should land so the kerf edge is at +clearance/2.
    y_cut_pos = kerf_offset_boundary(
        y_geo=y_geo,
        kerf_mm=kerf_mm,
        clearance_mm=clearance_mm,
        keep_on_positive_side=True,
        is_tail_board=True,
    )
    assert abs(y_cut_pos - (y_geo + clearance_shift - kerf_radius)) < 1e-9
    # Effective kept boundary after the cut (kerf edge on keep side).
    assert abs((y_cut_pos + kerf_radius) - (y_geo + clearance_shift)) < 1e-9

    # Keep on negative side: kerf edge should land at -clearance/2.
    y_geo_neg = 10.0
    y_cut_neg = kerf_offset_boundary(
        y_geo=y_geo_neg,
        kerf_mm=kerf_mm,
        clearance_mm=clearance_mm,
        keep_on_positive_side=False,
        is_tail_board=False,
    )
    assert abs(y_cut_neg - (y_geo_neg - clearance_shift + kerf_radius)) < 1e-9
    assert abs((y_cut_neg - kerf_radius) - (y_geo_neg - clearance_shift)) < 1e-9
