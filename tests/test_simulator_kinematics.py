# tests/test_simulator_kinematics.py
import math

from laserdove.model import BoardSide, Command, CommandType
from laserdove.sim_kinematics import (
    board_to_world_local,
    capture_segments_from_commands,
    invert_projected_y,
)


def test_rotate_segment_targets_pin_board():
    commands = [
        Command(type=CommandType.MOVE, x=0.0, y=0.0, z=0.0, speed_mm_s=100.0),
        Command(type=CommandType.ROTATE, angle_deg=10.0, speed_mm_s=30.0),
    ]
    segments = capture_segments_from_commands(
        commands,
        edge_length_mm=100.0,
        axis_to_origin_mm=30.0,
        rotation_zero_deg=0.0,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
        start_board=BoardSide.TAIL,
    )
    rotate_segments = [
        seg for seg in segments if not math.isclose(seg.start_rotation_deg, seg.end_rotation_deg)
    ]
    assert rotate_segments, "Expected at least one rotation segment"
    assert rotate_segments[0].board == BoardSide.PIN


def test_z_move_does_not_shift_y_when_rotated():
    commands = [
        Command(type=CommandType.ROTATE, angle_deg=20.0, speed_mm_s=10.0),
        Command(type=CommandType.MOVE, z=5.0, speed_mm_s=5.0),
    ]
    segments = capture_segments_from_commands(
        commands,
        edge_length_mm=100.0,
        axis_to_origin_mm=30.0,
        rotation_zero_deg=0.0,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
        start_board=BoardSide.PIN,
    )
    move_segments = [seg for seg in segments if seg.start_rotation_deg == seg.end_rotation_deg]
    assert move_segments, "Expected at least one MOVE segment"
    seg = move_segments[-1]
    assert abs(seg.start_world[1] - seg.end_world[1]) < 1e-9
    assert abs((seg.end_world[2] - seg.start_world[2]) - 5.0) < 1e-9


def test_projection_round_trip_matches_board_y():
    edge_length = 100.0
    y_center = edge_length / 2.0
    axis_to_origin = 32.5
    rotation_deg = 20.0
    y_board_abs = 70.0

    angle_rad = math.radians(rotation_deg)
    y_machine = (
        y_center
        + (y_board_abs - y_center) * math.cos(angle_rad)
        - axis_to_origin * math.sin(angle_rad)
    )

    commands = [
        Command(type=CommandType.ROTATE, angle_deg=rotation_deg, speed_mm_s=30.0),
        Command(type=CommandType.MOVE, x=0.0, y=y_machine, z=0.0, speed_mm_s=100.0),
    ]
    segments = capture_segments_from_commands(
        commands,
        edge_length_mm=edge_length,
        axis_to_origin_mm=axis_to_origin,
        rotation_zero_deg=0.0,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
        start_board=BoardSide.PIN,
    )
    move_seg = segments[-1]
    assert abs(move_seg.end_world[1] - y_machine) < 1e-9
    assert abs(move_seg.end_board[1] - (y_board_abs - y_center)) < 1e-9


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
