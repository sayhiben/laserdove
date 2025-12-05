from __future__ import annotations

import math

from laserdove.panda3d_simulator import board_to_world_local, invert_projected_y


def test_invert_projected_y_round_trip() -> None:
    y_center = 50.0
    axis = 30.0
    rotation = 8.0
    board_y = 42.0
    y_machine = (
        y_center
        + (board_y - y_center) * math.cos(math.radians(rotation))
        - axis * math.sin(math.radians(rotation))
    )
    recovered = invert_projected_y(
        y_machine,
        rotation,
        axis_to_origin_mm=axis,
        y_center=y_center,
        rotation_zero_deg=0.0,
    )
    assert math.isclose(recovered, board_y, abs_tol=1e-9)


def test_board_to_world_matches_projection() -> None:
    axis = 25.0
    rotation = -6.0
    y_center = 40.0
    y_board = 65.0
    y_local = y_board - y_center
    pos = board_to_world_local(
        5.0,
        y_local,
        0.0,
        rotation,
        axis_to_origin_mm=axis,
        y_center=y_center,
        rotation_zero_deg=0.0,
    )
    angle_rad = math.radians(abs(rotation))
    sin_t = math.sin(math.radians(rotation))
    cos_t = math.cos(angle_rad)
    expected_y = y_center + y_local * cos_t - axis * sin_t
    expected_z = y_local * sin_t + axis * cos_t
    assert math.isclose(pos[1], expected_y, abs_tol=1e-9)
    assert math.isclose(pos[2], expected_z, abs_tol=1e-9)
