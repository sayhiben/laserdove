from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Sequence

from .command_compiler import compile_command_plan
from .model import BoardSide, Command
from .rd_parser import RuidaParser
from .sim_kinematics import (
    PlaybackSegment,
    board_to_world_local,
    capture_segments_from_commands,
    invert_projected_y,
    overlay_segments_from_rd,
)

log = logging.getLogger(__name__)


@dataclass
class BeamTrace:
    """
    Beam path projected through the stock at one motion interval.
    """

    board: BoardSide
    is_cut: bool
    start_top: tuple[float, float, float]
    end_top: tuple[float, float, float]
    start_bottom: tuple[float, float, float]
    end_bottom: tuple[float, float, float]
    head_start: tuple[float, float, float]
    head_end: tuple[float, float, float]
    start_world: tuple[float, float, float]
    end_world: tuple[float, float, float]
    start_board_local: tuple[float, float, float]
    end_board_local: tuple[float, float, float]
    rotation_deg: float
    rotation_end_deg: float
    duration: float
    power_pct: float
    air_assist: bool
    start_machine_z: float
    end_machine_z: float
    source: str
    is_rotation_only: bool


def _z_ref(board: BoardSide, z_zero_tail_mm: float, z_zero_pin_mm: float) -> float:
    return z_zero_tail_mm if board == BoardSide.TAIL else z_zero_pin_mm


def _beam_traces_from_playback(
    playback: Sequence[PlaybackSegment],
    *,
    joint_params,
    jig_params,
    machine_params,
) -> List[BeamTrace]:
    """
    Convert playback segments into per-segment beam traces with top/bottom projections.
    """

    traces: List[BeamTrace] = []
    for seg in playback:
        z_ref = _z_ref(seg.board, machine_params.z_zero_tail_mm, machine_params.z_zero_pin_mm)
        start_machine_z = seg.start_z_offset_mm + z_ref
        end_machine_z = seg.end_z_offset_mm + z_ref
        y_center = joint_params.edge_length_mm / 2.0

        # Beam projection: in world space the beam is vertical, so the entry/exit
        # points have the same (x, y) and differ only in z. Recover board-space Y
        # separately for the top and bottom surfaces so cuts shear correctly in the
        # edge view (thickness * tan(theta)).
        radius_bottom = max(jig_params.axis_to_origin_mm - joint_params.thickness_mm, 0.001)

        start_top = seg.start_world
        end_top = seg.end_world

        y_start_machine = seg.start_world[1]
        y_end_machine = seg.end_world[1]

        y_start_bottom_abs = invert_projected_y(
            y_start_machine,
            seg.start_rotation_deg,
            axis_to_origin_mm=radius_bottom,
            y_center=y_center,
            rotation_zero_deg=jig_params.rotation_zero_deg,
        )
        y_start_bottom_local = y_start_bottom_abs - y_center
        start_bottom_base = board_to_world_local(
            seg.start_board[0],
            y_start_bottom_local,
            -joint_params.thickness_mm,
            seg.start_rotation_deg,
            axis_to_origin_mm=jig_params.axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=jig_params.rotation_zero_deg,
        )
        start_bottom = (
            start_bottom_base[0],
            start_bottom_base[1],
            start_bottom_base[2] + seg.start_z_offset_mm,
        )

        y_end_bottom_abs = invert_projected_y(
            y_end_machine,
            seg.end_rotation_deg,
            axis_to_origin_mm=radius_bottom,
            y_center=y_center,
            rotation_zero_deg=jig_params.rotation_zero_deg,
        )
        y_end_bottom_local = y_end_bottom_abs - y_center
        end_bottom_base = board_to_world_local(
            seg.end_board[0],
            y_end_bottom_local,
            -joint_params.thickness_mm,
            seg.end_rotation_deg,
            axis_to_origin_mm=jig_params.axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=jig_params.rotation_zero_deg,
        )
        end_bottom = (
            end_bottom_base[0],
            end_bottom_base[1],
            end_bottom_base[2] + seg.end_z_offset_mm,
        )

        is_rotation_only = (
            math.isclose(seg.start_world[0], seg.end_world[0], abs_tol=1e-9)
            and math.isclose(seg.start_world[1], seg.end_world[1], abs_tol=1e-9)
            and math.isclose(seg.start_world[2], seg.end_world[2], abs_tol=1e-9)
            and not math.isclose(seg.start_rotation_deg, seg.end_rotation_deg, abs_tol=1e-9)
        )
        duration = seg.duration
        if duration <= 0.0 and not is_rotation_only:
            dx = seg.end_world[0] - seg.start_world[0]
            dy = seg.end_world[1] - seg.start_world[1]
            dz = seg.end_world[2] - seg.start_world[2]
            distance = math.sqrt(dx * dx + dy * dy + dz * dz)
            if seg.is_cut:
                speed = (
                    machine_params.cut_speed_pin_mm_s
                    if seg.board == BoardSide.PIN
                    else machine_params.cut_speed_tail_mm_s
                )
            else:
                speed = machine_params.rapid_speed_mm_s
            duration = distance / speed if speed > 0 else 0.0

        traces.append(
            BeamTrace(
                board=seg.board,
                is_cut=seg.is_cut,
                start_top=start_top,
                end_top=end_top,
                start_bottom=start_bottom,
                end_bottom=end_bottom,
                head_start=seg.start_world,
                head_end=seg.end_world,
                start_world=seg.start_world,
                end_world=seg.end_world,
                start_board_local=seg.start_board,
                end_board_local=seg.end_board,
                rotation_deg=seg.start_rotation_deg,
                rotation_end_deg=seg.end_rotation_deg,
                duration=duration,
                power_pct=seg.power_pct,
                air_assist=seg.air_assist,
                start_machine_z=start_machine_z,
                end_machine_z=end_machine_z,
                source=seg.source,
                is_rotation_only=is_rotation_only,
            )
        )

    return traces


def build_beam_traces(
    commands: Iterable[Command],
    *,
    joint_params,
    jig_params,
    machine_params,
    movement_only: bool = False,
    air_assist: bool = True,
    start_board: BoardSide = BoardSide.TAIL,
) -> List[BeamTrace]:
    """
    Expand planner commands into per-segment beam traces with top/bottom projections.
    """
    playback = capture_segments_from_commands(
        commands,
        edge_length_mm=joint_params.edge_length_mm,
        axis_to_origin_mm=jig_params.axis_to_origin_mm,
        rotation_zero_deg=jig_params.rotation_zero_deg,
        z_zero_tail_mm=machine_params.z_zero_tail_mm,
        z_zero_pin_mm=machine_params.z_zero_pin_mm,
        movement_only=movement_only,
        air_assist=air_assist,
        start_board=start_board,
    )
    return _beam_traces_from_playback(
        playback,
        joint_params=joint_params,
        jig_params=jig_params,
        machine_params=machine_params,
    )


def _rd_job_contexts(
    commands: Iterable[Command],
    *,
    rotation_zero_deg: float,
) -> List[tuple[float, BoardSide]]:
    plan = compile_command_plan(
        list(commands),
        origin_x=0.0,
        origin_y=0.0,
        start_z=0.0,
        edge_length_mm=0.0,
        z_speed_mm_s=1.0,
        movement_only=False,
        rotation_zero_deg=rotation_zero_deg,
    )
    return [(block.rotation_deg, block.board) for block in plan.blocks()]


def _parse_rd_segments(rd_path: Path) -> List[dict]:
    parser = RuidaParser(file=str(rd_path))
    parser.decode(debug=False)
    return list(parser._segments)


def build_beam_traces_from_rd_segments(
    rd_segments: Sequence[dict],
    *,
    rotation_deg: float,
    board: BoardSide,
    joint_params,
    jig_params,
    machine_params,
) -> List[BeamTrace]:
    playback = overlay_segments_from_rd(
        rd_segments,
        rotation_deg,
        board,
        edge_length_mm=joint_params.edge_length_mm,
        axis_to_origin_mm=jig_params.axis_to_origin_mm,
        rotation_zero_deg=jig_params.rotation_zero_deg,
        z_zero_tail_mm=machine_params.z_zero_tail_mm,
        z_zero_pin_mm=machine_params.z_zero_pin_mm,
        z_speed_mm_s=machine_params.z_speed_mm_s,
    )
    return _beam_traces_from_playback(
        playback,
        joint_params=joint_params,
        jig_params=jig_params,
        machine_params=machine_params,
    )


def build_beam_traces_from_rd_files(
    rd_paths: Sequence[Path],
    commands: Iterable[Command],
    *,
    joint_params,
    jig_params,
    machine_params,
) -> List[BeamTrace]:
    contexts = _rd_job_contexts(commands, rotation_zero_deg=jig_params.rotation_zero_deg)
    if not contexts:
        contexts = [(jig_params.rotation_zero_deg, BoardSide.TAIL)]
    rd_segments_list = [_parse_rd_segments(rd_path) for rd_path in rd_paths]
    if len(rd_paths) != len(contexts):
        if len(rd_paths) > len(contexts):
            extra_segments = rd_segments_list[len(contexts) :]
            # Extra RD jobs are often post-run travel-only parks; treat them as the last context.
            extra_travel_only = all(
                not any(segment.get("is_cut") for segment in segments)
                for segments in extra_segments
            )
            if extra_travel_only:
                contexts.extend([contexts[-1]] * len(extra_segments))
            else:
                log.warning(
                    "RD job count (%d) does not match rotation blocks (%d); reusing nearest context",
                    len(rd_paths),
                    len(contexts),
                )
        else:
            log.warning(
                "RD job count (%d) does not match rotation blocks (%d); reusing nearest context",
                len(rd_paths),
                len(contexts),
            )

    def _z_ref_for_board(board: BoardSide) -> float:
        return _z_ref(board, machine_params.z_zero_tail_mm, machine_params.z_zero_pin_mm)

    def _board_local_from_machine(
        x_machine: float, y_machine: float, rotation_deg: float, board: BoardSide
    ) -> tuple[float, float, float]:
        y_center = joint_params.edge_length_mm / 2.0
        if board == BoardSide.PIN and not math.isclose(
            rotation_deg, jig_params.rotation_zero_deg, abs_tol=1e-9
        ):
            y_abs = invert_projected_y(
                y_machine,
                rotation_deg,
                axis_to_origin_mm=jig_params.axis_to_origin_mm,
                y_center=y_center,
                rotation_zero_deg=jig_params.rotation_zero_deg,
            )
        else:
            y_abs = y_machine
        return (x_machine, y_abs - y_center, 0.0)

    def _world_from_machine(
        x_machine: float,
        y_machine: float,
        rotation_deg: float,
        board: BoardSide,
        z_offset: float,
    ) -> tuple[float, float, float]:
        y_center = joint_params.edge_length_mm / 2.0
        board_local = _board_local_from_machine(x_machine, y_machine, rotation_deg, board)
        base = board_to_world_local(
            board_local[0],
            board_local[1],
            0.0,
            rotation_deg,
            axis_to_origin_mm=jig_params.axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=jig_params.rotation_zero_deg,
        )
        return (base[0], base[1], base[2] + z_offset)

    def _travel_segment_between(
        last_seg: PlaybackSegment,
        next_seg: PlaybackSegment,
        rotation_deg: float,
    ) -> PlaybackSegment | None:
        start_x, start_y = last_seg.end_world[0], last_seg.end_world[1]
        end_x, end_y = next_seg.start_world[0], next_seg.start_world[1]
        if math.isclose(start_x, end_x, abs_tol=1e-9) and math.isclose(
            start_y, end_y, abs_tol=1e-9
        ):
            return None
        board = last_seg.board
        z_offset = last_seg.end_z_offset_mm
        start_board = _board_local_from_machine(start_x, start_y, rotation_deg, board)
        end_board = _board_local_from_machine(end_x, end_y, rotation_deg, board)
        end_world = _world_from_machine(end_x, end_y, rotation_deg, board, z_offset)
        return PlaybackSegment(
            start_board=start_board,
            end_board=end_board,
            start_world=last_seg.end_world,
            end_world=end_world,
            start_rotation_deg=rotation_deg,
            end_rotation_deg=rotation_deg,
            start_z_offset_mm=z_offset,
            end_z_offset_mm=z_offset,
            board=board,
            is_cut=False,
            duration=0.0,
            power_pct=0.0,
            air_assist=last_seg.air_assist,
            source="rd",
        )

    def rotation_segment_between(
        last_seg: PlaybackSegment | None,
        prev_rotation: float,
        next_rotation: float,
        next_board: BoardSide,
    ) -> PlaybackSegment | None:
        if last_seg is None or math.isclose(prev_rotation, next_rotation, abs_tol=1e-9):
            return None
        duration = (
            abs(next_rotation - prev_rotation) / jig_params.rotation_speed_dps
            if jig_params.rotation_speed_dps > 0
            else 0.0
        )
        x_machine, y_machine, z_machine = last_seg.end_world
        y_center = joint_params.edge_length_mm / 2.0
        y_start_abs = invert_projected_y(
            y_machine,
            prev_rotation,
            axis_to_origin_mm=jig_params.axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=jig_params.rotation_zero_deg,
        )
        y_end_abs = invert_projected_y(
            y_machine,
            next_rotation,
            axis_to_origin_mm=jig_params.axis_to_origin_mm,
            y_center=y_center,
            rotation_zero_deg=jig_params.rotation_zero_deg,
        )
        start_board = (x_machine, y_start_abs - y_center, 0.0)
        end_board = (x_machine, y_end_abs - y_center, 0.0)
        prev_z_ref = _z_ref(
            last_seg.board,
            machine_params.z_zero_tail_mm,
            machine_params.z_zero_pin_mm,
        )
        machine_z = last_seg.end_z_offset_mm + prev_z_ref
        next_z_ref = _z_ref(
            next_board,
            machine_params.z_zero_tail_mm,
            machine_params.z_zero_pin_mm,
        )
        z_offset = machine_z - next_z_ref
        return PlaybackSegment(
            start_board=start_board,
            end_board=end_board,
            start_world=(x_machine, y_machine, z_machine),
            end_world=(x_machine, y_machine, z_machine),
            start_rotation_deg=prev_rotation,
            end_rotation_deg=next_rotation,
            start_z_offset_mm=z_offset,
            end_z_offset_mm=z_offset,
            board=next_board,
            is_cut=False,
            duration=duration,
            power_pct=0.0,
            air_assist=last_seg.air_assist,
            source="rd",
        )

    playback: List[PlaybackSegment] = []
    last_segment: PlaybackSegment | None = None
    last_machine_z: float | None = None
    for idx, rd_path in enumerate(rd_paths):
        rotation_deg, board = contexts[min(idx, len(contexts) - 1)]
        z_base_mm = last_machine_z if last_machine_z is not None else _z_ref_for_board(board)
        overlays = overlay_segments_from_rd(
            rd_segments_list[idx],
            rotation_deg,
            board,
            edge_length_mm=joint_params.edge_length_mm,
            axis_to_origin_mm=jig_params.axis_to_origin_mm,
            rotation_zero_deg=jig_params.rotation_zero_deg,
            z_zero_tail_mm=machine_params.z_zero_tail_mm,
            z_zero_pin_mm=machine_params.z_zero_pin_mm,
            z_base_mm=z_base_mm,
            z_speed_mm_s=machine_params.z_speed_mm_s,
        )
        if overlays and last_segment is not None:
            prev_rotation, _prev_board = contexts[min(idx - 1, len(contexts) - 1)]
            travel_segment = _travel_segment_between(last_segment, overlays[0], prev_rotation)
            if travel_segment is not None:
                playback.append(travel_segment)
                last_segment = travel_segment
        if idx > 0:
            prev_rotation, _prev_board = contexts[min(idx - 1, len(contexts) - 1)]
            rotation_segment = rotation_segment_between(
                last_segment,
                prev_rotation,
                rotation_deg,
                board,
            )
            if rotation_segment is not None:
                playback.append(rotation_segment)
                last_segment = rotation_segment
        playback.extend(overlays)
        if overlays:
            last_segment = overlays[-1]
            last_machine_z = last_segment.end_z_offset_mm + _z_ref_for_board(last_segment.board)

    return _beam_traces_from_playback(
        playback,
        joint_params=joint_params,
        jig_params=jig_params,
        machine_params=machine_params,
    )
