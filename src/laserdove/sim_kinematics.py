# sim_kinematics.py
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .model import BoardSide, Command, CommandType

log = logging.getLogger(__name__)


@dataclass
class PlaybackSegment:
    """
    One motion/rotation interval with start/end poses in board and world space.
    """

    start_board: tuple[float, float, float]
    end_board: tuple[float, float, float]
    start_world: tuple[float, float, float]
    end_world: tuple[float, float, float]
    start_rotation_deg: float
    end_rotation_deg: float
    start_z_offset_mm: float
    end_z_offset_mm: float
    board: BoardSide
    is_cut: bool
    duration: float
    power_pct: float
    air_assist: bool
    source: str = "plan"


def invert_projected_y(
    y_machine: float,
    rotation_deg: float,
    *,
    axis_to_origin_mm: float,
    y_center: float,
    rotation_zero_deg: float = 0.0,
) -> float:
    """
    Recover board-space Y from a projected machine-space Y at a given rotation.
    """
    delta = rotation_deg - rotation_zero_deg
    cos_t = math.cos(math.radians(delta))
    if abs(cos_t) < 1e-6:
        return y_center
    sin_t = math.sin(math.radians(delta))
    return y_center + (y_machine - y_center + axis_to_origin_mm * sin_t) / cos_t


def board_to_world_local(
    x_b: float,
    y_local: float,
    z_local: float,
    rotation_deg: float,
    *,
    axis_to_origin_mm: float,
    y_center: float,
    rotation_zero_deg: float = 0.0,
) -> tuple[float, float, float]:
    """
    Map board-local coordinates (centered at y=0, z=0 at the top surface)
    into world space for a given rotary angle.
    """
    delta = rotation_deg - rotation_zero_deg
    angle_rad = math.radians(delta)
    sin_t = math.sin(angle_rad)
    cos_t = math.cos(angle_rad)
    y_rot = y_local * cos_t - (axis_to_origin_mm + z_local) * sin_t
    z_rot = y_local * sin_t + (axis_to_origin_mm + z_local) * cos_t
    return (x_b, y_center + y_rot, z_rot)


def _current_z_reference(board: BoardSide, z_zero_tail_mm: float, z_zero_pin_mm: float) -> float:
    return z_zero_tail_mm if board == BoardSide.TAIL else z_zero_pin_mm


def capture_segments_from_commands(
    commands: Iterable[Command],
    *,
    edge_length_mm: float,
    axis_to_origin_mm: float,
    rotation_zero_deg: float,
    z_zero_tail_mm: float,
    z_zero_pin_mm: float,
    movement_only: bool = False,
    air_assist: bool = True,
    start_board: BoardSide = BoardSide.TAIL,
) -> List[PlaybackSegment]:
    """
    Expand planner Commands into time-annotated playback segments for visualization.
    """
    y_center = edge_length_mm / 2.0
    rotation = rotation_zero_deg
    board = start_board
    z_ref = _current_z_reference(board, z_zero_tail_mm, z_zero_pin_mm)
    x = 0.0
    y = 0.0
    z = z_ref
    power_pct = 0.0
    segments: List[PlaybackSegment] = []

    y_local = y - y_center
    z_offset = z - z_ref
    board_local = (x, y_local, 0.0)
    world_base = board_to_world_local(
        x,
        y_local,
        0.0,
        rotation,
        axis_to_origin_mm=axis_to_origin_mm,
        y_center=y_center,
        rotation_zero_deg=rotation_zero_deg,
    )
    world_pos = (world_base[0], world_base[1], world_base[2] + z_offset)

    for command in commands:
        if command.type == CommandType.SET_LASER_POWER:
            power_pct = 0.0 if movement_only else (command.power_pct or 0.0)
            continue

        if command.type == CommandType.ROTATE:
            # Rotary motion always targets the pin-board setup.
            if board != BoardSide.PIN:
                board = BoardSide.PIN
                z_ref = _current_z_reference(board, z_zero_tail_mm, z_zero_pin_mm)
            target_rotation = rotation if command.angle_deg is None else command.angle_deg
            delta_angle = abs(target_rotation - rotation)
            speed = command.speed_mm_s or 0.0
            duration = delta_angle / speed if speed > 0 else 0.0
            z_offset = z - z_ref

            board_y_abs = (
                invert_projected_y(
                    y,
                    rotation,
                    axis_to_origin_mm=axis_to_origin_mm,
                    y_center=y_center,
                    rotation_zero_deg=rotation_zero_deg,
                )
                if board == BoardSide.PIN
                and not math.isclose(rotation, rotation_zero_deg, abs_tol=1e-9)
                else y
            )
            y_local = board_y_abs - y_center
            board_local = (x, y_local, 0.0)
            world_base = board_to_world_local(
                x,
                y_local,
                0.0,
                rotation,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            world_pos = (world_base[0], world_base[1], world_base[2] + z_offset)

            segments.append(
                PlaybackSegment(
                    start_board=board_local,
                    end_board=board_local,
                    start_world=world_pos,
                    end_world=world_pos,
                    start_rotation_deg=rotation,
                    end_rotation_deg=target_rotation,
                    start_z_offset_mm=z_offset,
                    end_z_offset_mm=z_offset,
                    board=board,
                    is_cut=False,
                    duration=duration,
                    power_pct=power_pct,
                    air_assist=air_assist,
                    source="plan",
                )
            )
            rotation = target_rotation
            board_y_abs = (
                invert_projected_y(
                    y,
                    rotation,
                    axis_to_origin_mm=axis_to_origin_mm,
                    y_center=y_center,
                    rotation_zero_deg=rotation_zero_deg,
                )
                if board == BoardSide.PIN
                and not math.isclose(rotation, rotation_zero_deg, abs_tol=1e-9)
                else y
            )
            y_local = board_y_abs - y_center
            board_local = (x, y_local, 0.0)
            world_base = board_to_world_local(
                x,
                y_local,
                0.0,
                rotation,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            world_pos = (world_base[0], world_base[1], world_base[2] + z_offset)
            continue

        if command.type not in (CommandType.MOVE, CommandType.CUT_LINE):
            log.debug("Skipping unsupported command in simulator: %s", command)
            continue

        target_x = x if command.x is None else command.x
        target_y_machine = y if command.y is None else command.y
        target_z = z if command.z is None else command.z

        y_board_abs = (
            invert_projected_y(
                target_y_machine,
                rotation,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            if board == BoardSide.PIN
            and not math.isclose(rotation, rotation_zero_deg, abs_tol=1e-9)
            else target_y_machine
        )
        y_target_local = y_board_abs - y_center
        target_z_offset = target_z - z_ref

        world_end_base = board_to_world_local(
            target_x,
            y_target_local,
            0.0,
            rotation,
            axis_to_origin_mm=axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=rotation_zero_deg,
        )
        end_world = (world_end_base[0], world_end_base[1], world_end_base[2] + target_z_offset)

        dx = target_x - x
        dy = target_y_machine - y
        dz = target_z - z
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)
        if command.type == CommandType.MOVE and math.isclose(distance, 0.0, abs_tol=1e-9):
            distance = abs(dz)
        speed = command.speed_mm_s or 0.0
        duration = distance / speed if speed > 0 else 0.0

        is_cut = command.type == CommandType.CUT_LINE and (not movement_only) and power_pct > 0.0
        z_offset = z - z_ref
        segment = PlaybackSegment(
            start_board=board_local,
            end_board=(target_x, y_target_local, 0.0),
            start_world=world_pos,
            end_world=end_world,
            start_rotation_deg=rotation,
            end_rotation_deg=rotation,
            start_z_offset_mm=z_offset,
            end_z_offset_mm=target_z_offset,
            board=board,
            is_cut=is_cut,
            duration=duration,
            power_pct=power_pct,
            air_assist=air_assist,
            source="plan",
        )
        segments.append(segment)

        x, y, z = target_x, target_y_machine, target_z
        board_local = (target_x, y_target_local, 0.0)
        world_pos = end_world

    return segments


def overlay_segments_from_rd(
    rd_segments: Sequence[dict],
    rotation_deg: float,
    board: BoardSide,
    *,
    edge_length_mm: float,
    axis_to_origin_mm: float,
    rotation_zero_deg: float,
    z_zero_tail_mm: float,
    z_zero_pin_mm: float,
    z_base_mm: float | None = None,
    z_speed_mm_s: float | None = None,
) -> List[PlaybackSegment]:
    """
    Convert RD parser segments into board/world coordinates for overlay rendering.
    """
    y_center = edge_length_mm / 2.0
    z_ref = _current_z_reference(board, z_zero_tail_mm, z_zero_pin_mm)
    overlays: List[PlaybackSegment] = []
    z_base = 0.0 if z_base_mm is None else z_base_mm
    last_z_offset = z_base - z_ref

    for seg in rd_segments:
        x0 = float(seg["x0"])
        y0 = float(seg["y0"]) + y_center
        x1 = float(seg["x1"])
        y1 = float(seg["y1"]) + y_center
        z_seg = float(seg.get("z", seg.get("logical_z", 0.0)))
        y0_board = (
            invert_projected_y(
                y0,
                rotation_deg,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            if board == BoardSide.PIN
            and not math.isclose(rotation_deg, rotation_zero_deg, abs_tol=1e-9)
            else y0
        )
        y1_board = (
            invert_projected_y(
                y1,
                rotation_deg,
                axis_to_origin_mm=axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=rotation_zero_deg,
            )
            if board == BoardSide.PIN
            and not math.isclose(rotation_deg, rotation_zero_deg, abs_tol=1e-9)
            else y1
        )
        z_offset = (z_base + z_seg) - z_ref
        start_board = (x0, y0_board - y_center, 0.0)
        end_board = (x1, y1_board - y_center, 0.0)
        start_world_base = board_to_world_local(
            start_board[0],
            start_board[1],
            0.0,
            rotation_deg,
            axis_to_origin_mm=axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=rotation_zero_deg,
        )
        end_world_base = board_to_world_local(
            end_board[0],
            end_board[1],
            0.0,
            rotation_deg,
            axis_to_origin_mm=axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=rotation_zero_deg,
        )
        if not math.isclose(z_offset, last_z_offset, abs_tol=1e-9):
            z_duration = 0.0
            if z_speed_mm_s is not None and z_speed_mm_s > 0:
                z_duration = abs(z_offset - last_z_offset) / z_speed_mm_s
            overlays.append(
                PlaybackSegment(
                    start_board=start_board,
                    end_board=start_board,
                    start_world=(
                        start_world_base[0],
                        start_world_base[1],
                        start_world_base[2] + last_z_offset,
                    ),
                    end_world=(
                        start_world_base[0],
                        start_world_base[1],
                        start_world_base[2] + z_offset,
                    ),
                    start_rotation_deg=rotation_deg,
                    end_rotation_deg=rotation_deg,
                    start_z_offset_mm=last_z_offset,
                    end_z_offset_mm=z_offset,
                    board=board,
                    is_cut=False,
                    duration=z_duration,
                    power_pct=0.0,
                    air_assist=bool(seg.get("air_assist", True)),
                    source="rd",
                )
            )
            last_z_offset = z_offset
        overlays.append(
            PlaybackSegment(
                start_board=start_board,
                end_board=end_board,
                start_world=(
                    start_world_base[0],
                    start_world_base[1],
                    start_world_base[2] + z_offset,
                ),
                end_world=(end_world_base[0], end_world_base[1], end_world_base[2] + z_offset),
                start_rotation_deg=rotation_deg,
                end_rotation_deg=rotation_deg,
                start_z_offset_mm=z_offset,
                end_z_offset_mm=z_offset,
                board=board,
                is_cut=bool(seg.get("is_cut")),
                duration=0.0,
                power_pct=float(seg.get("power_pct", 0.0)),
                air_assist=bool(seg.get("air_assist", True)),
                source="rd",
            )
        )
        last_z_offset = z_offset

    return overlays
