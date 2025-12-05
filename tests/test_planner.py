# tests for planner.py
import math

import pytest

from laserdove.geometry import compute_tail_layout
from laserdove.model import JointParams, JigParams, Side
from laserdove.planner import compute_pin_plan


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
