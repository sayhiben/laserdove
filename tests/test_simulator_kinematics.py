# tests/test_simulator_kinematics.py
import math

from laserdove.model import Command, CommandType
from laserdove.panda3d_simulator import capture_segments_from_commands


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
        start_board="tail",
    )
    rotate_segments = [
        seg for seg in segments if not math.isclose(seg.start_rotation_deg, seg.end_rotation_deg)
    ]
    assert rotate_segments, "Expected at least one rotation segment"
    assert rotate_segments[0].board == "pin"


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
        start_board="pin",
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
        start_board="pin",
    )
    move_seg = segments[-1]
    assert abs(move_seg.end_world[1] - y_machine) < 1e-9
    assert abs(move_seg.end_board[1] - (y_board_abs - y_center)) < 1e-9
