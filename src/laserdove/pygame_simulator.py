# pygame_simulator.py
from __future__ import annotations

import logging
import math
import os
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .model import Command, CommandType
from .sim_kinematics import (
    PlaybackSegment,
    board_to_world_local,
    capture_segments_from_commands,
    invert_projected_y,
    overlay_segments_from_rd,
)
from .rd_parser import RuidaParser

log = logging.getLogger(__name__)


@dataclass
class BeamTrace:
    """
    Beam path projected through the stock at one motion interval.
    """

    board: str
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


def _z_ref(board: str, z_zero_tail_mm: float, z_zero_pin_mm: float) -> float:
    return z_zero_tail_mm if board == "tail" else z_zero_pin_mm


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
                    if seg.board == "pin"
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
    start_board: str = "tail",
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
) -> List[tuple[float, str]]:
    rotation = rotation_zero_deg
    board = "tail"
    contexts: List[tuple[float, str]] = []
    block_has_motion = False

    for cmd in commands:
        if cmd.type == CommandType.ROTATE:
            if block_has_motion:
                contexts.append((rotation, board))
                block_has_motion = False
            if cmd.angle_deg is not None:
                rotation = cmd.angle_deg
            board = "pin"
            continue
        if cmd.type in (CommandType.MOVE, CommandType.CUT_LINE):
            block_has_motion = True

    if block_has_motion:
        contexts.append((rotation, board))

    return contexts


def _parse_rd_segments(rd_path: Path) -> List[dict]:
    parser = RuidaParser(file=str(rd_path))
    parser.decode(debug=False)
    return list(parser._segments)


def build_beam_traces_from_rd_segments(
    rd_segments: Sequence[dict],
    *,
    rotation_deg: float,
    board: str,
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
        contexts = [(jig_params.rotation_zero_deg, "tail")]
    if len(rd_paths) != len(contexts):
        log.warning(
            "RD job count (%d) does not match rotation blocks (%d); reusing nearest context",
            len(rd_paths),
            len(contexts),
        )

    playback: List[PlaybackSegment] = []
    for idx, rd_path in enumerate(rd_paths):
        rotation_deg, board = contexts[min(idx, len(contexts) - 1)]
        playback.extend(
            overlay_segments_from_rd(
                _parse_rd_segments(rd_path),
                rotation_deg,
                board,
                edge_length_mm=joint_params.edge_length_mm,
                axis_to_origin_mm=jig_params.axis_to_origin_mm,
                rotation_zero_deg=jig_params.rotation_zero_deg,
                z_zero_tail_mm=machine_params.z_zero_tail_mm,
                z_zero_pin_mm=machine_params.z_zero_pin_mm,
            )
        )

    return _beam_traces_from_playback(
        playback,
        joint_params=joint_params,
        jig_params=jig_params,
        machine_params=machine_params,
    )


class PygameSimulationViewer:
    """
    Dual orthographic (top + edge) viewer rendered with pygame.
    """

    def __init__(
        self,
        traces: Sequence[BeamTrace],
        *,
        edge_length_mm: float,
        thickness_mm: float,
        axis_to_origin_mm: float,
        rotation_zero_deg: float,
        z_zero_tail_mm: float,
        z_zero_pin_mm: float,
        z_positive_moves_bed_up: bool = True,
        time_scale: float = 1.0,
    ) -> None:
        self.traces = list(traces)
        self.edge_length_mm = edge_length_mm
        self.thickness_mm = thickness_mm
        self.axis_to_origin_mm = axis_to_origin_mm
        self.rotation_zero_deg = rotation_zero_deg
        self.z_zero_tail_mm = z_zero_tail_mm
        self.z_zero_pin_mm = z_zero_pin_mm
        self.z_positive_moves_bed_up = z_positive_moves_bed_up
        self.time_scale = time_scale
        self.total_duration = sum(max(t.duration, 0.0) for t in self.traces)
        self._cumulative: List[float] = []
        running = 0.0
        for t in self.traces:
            running += max(t.duration, 0.0)
            self._cumulative.append(running)

        self.tail_traces = [t for t in self.traces if t.board == "tail"]
        self.pin_traces = [t for t in self.traces if t.board == "pin"]

        self.width = 1500
        self.height = 820
        self.padding = 18
        self.hud_line_count = 6
        self.top_rect_pos = None
        self.top_rect_neg = None
        self.edge_rect = None
        self.pose_bounds = None
        self.pose_head_z = None
        self.top_bounds_pos = None
        self.top_bounds_neg = None
        self.top_bounds_common = None
        self.edge_bounds = None
        self._font_small = None
        self._font_large = None
        self._compute_bounds()

        self.move_color = (230, 64, 64)
        self.cut_color = (40, 140, 255)
        self.cut_fill = (40, 140, 255, 70)
        self.bg_color = (14, 17, 26)
        self.board_outline = (70, 82, 102)
        # Slightly more opaque so background grid doesn't "stripe" the stock.
        self.board_fill = (36, 44, 58, 200)
        self.tail_cut_color = (120, 150, 210)
        self.pin_pos_cut_color = (40, 140, 255)
        self.pin_neg_cut_color = (230, 64, 64)
        self.tail_fill = (120, 150, 210, 70)
        self.pin_pos_fill = (40, 140, 255, 70)
        self.pin_neg_fill = (230, 64, 64, 70)

    def _hud_line_height_px(self) -> int:
        if self._font_small is None:
            return 18
        return int(self._font_small.get_linesize() + 2)

    def _hud_total_height_px(self) -> int:
        # Global HUD starts at y=self.padding and ends with an extra padding.
        line_h = self._hud_line_height_px()
        return int(self.padding * 2 + self.hud_line_count * line_h)

    def _panel_header_height_px(self) -> int:
        """
        Height reserved at the top of each panel for the panel title/scale.
        """
        if self._font_large is None:
            return 28
        return max(28, int(self._font_large.get_linesize() + 10))

    def _panel_content_rect(self, rect):
        import pygame

        header_h = self._panel_header_height_px()
        return pygame.Rect(
            rect.left,
            rect.top + header_h,
            rect.width,
            max(1, rect.height - header_h),
        )

    @staticmethod
    def _build_cumulative(traces: Sequence[BeamTrace]) -> List[float]:
        cumulative: List[float] = []
        running = 0.0
        for t in traces:
            running += max(t.duration, 0.0)
            cumulative.append(running)
        return cumulative

    def _compute_bounds(self) -> None:
        """
        Pre-compute extents for all viewports so scaling stays stable.
        """

        def bounds_for(
            traces: Sequence[BeamTrace],
        ) -> tuple[tuple[float, float, float, float], tuple[float, float, float, float]]:
            x_min, x_max = float("inf"), -float("inf")
            y_min, y_max = float("inf"), -float("inf")

            def add_top(pt: tuple[float, float, float]) -> None:
                nonlocal x_min, x_max, y_min, y_max
                x_min = min(x_min, pt[0])
                x_max = max(x_max, pt[0])
                y_min = min(y_min, pt[1])
                y_max = max(y_max, pt[1])

            for trace in traces:
                for pt in (trace.start_board_local, trace.end_board_local):
                    add_top((pt[0], pt[1] + self.y_center, 0.0))

            if x_min == float("inf"):
                x_min, x_max = 0.0, self.thickness_mm
                y_min, y_max = 0.0, self.edge_length_mm

            span_x = max(x_max - x_min, 1e-3)
            span_y = max(y_max - y_min, 1e-3)
            margin_x = span_x * 0.08
            margin_y = span_y * 0.08
            top_bounds = (
                x_min - margin_x,
                x_max + margin_x,
                y_min - margin_y,
                y_max + margin_y,
            )
            return top_bounds, (0.0, 0.0, 0.0, 0.0)

        # Compute combined bounds across all traces, then enforce a target grid span (150% of edge length).
        top_bounds_all, _ = bounds_for(self.traces)
        y_span_target = self.edge_length_mm * 1.5
        y_min_target = self.y_center - y_span_target * 0.5
        y_max_target = self.y_center + y_span_target * 0.5
        x_span_target = max(self.thickness_mm * 1.5, (top_bounds_all[1] - top_bounds_all[0]))
        x_min_target = -self.thickness_mm * 0.25
        x_max_target = x_min_target + x_span_target

        self.top_bounds_common = (
            min(top_bounds_all[0], x_min_target),
            max(top_bounds_all[1], x_max_target),
            min(top_bounds_all[2], y_min_target),
            max(top_bounds_all[3], y_max_target),
        )
        # Edge view renders in board-local (Y vs thickness) coordinates so the stock
        # is a rectangle and cuts appear as sheared polygons (vertical beam projection).
        max_delta = 0.0
        for t in self.traces:
            max_delta = max(max_delta, abs(t.rotation_end_deg - self.rotation_zero_deg))
        max_shear = abs(self.thickness_mm * math.tan(math.radians(max_delta))) if max_delta else 0.0
        y_margin = max(self.edge_length_mm * 0.06, 6.0)
        z_margin = max(self.thickness_mm * 0.12, 3.0)
        self.edge_bounds = (
            -self.y_center - max_shear - y_margin,
            self.y_center + max_shear + y_margin,
            -self.thickness_mm - z_margin,
            0.0 + z_margin,
        )
        self.top_bounds_pos = self.top_bounds_common
        self.top_bounds_neg = self.top_bounds_common

        # Edge pose inset: machine-frame view (world Y vs world Z), showing the
        # rotary axis, rotated stock outline, and a fixed laser head + beam.
        z_sign = 1.0 if self.z_positive_moves_bed_up else -1.0
        pin_z_offsets: List[float] = []
        for t in self.traces:
            if t.board != "pin":
                continue
            pin_z_offsets.append(z_sign * (t.start_machine_z - self.z_zero_pin_mm))
            pin_z_offsets.append(z_sign * (t.end_machine_z - self.z_zero_pin_mm))
        if not pin_z_offsets:
            pin_z_offsets = [0.0]
        z_off_min = min(pin_z_offsets)
        z_off_max = max(pin_z_offsets)

        rotations: set[float] = {self.rotation_zero_deg}
        for t in self.traces:
            rotations.add(t.rotation_deg)
            rotations.add(t.rotation_end_deg)

        pose_y_min = float("inf")
        pose_y_max = -float("inf")
        pose_z_min = float("inf")
        pose_z_max = -float("inf")
        for rot in rotations:
            for z_off in (z_off_min, z_off_max):
                _, edge_poly = self._board_outline_points(rot, z_offset_mm=z_off)
                for y_val, z_val in edge_poly:
                    pose_y_min = min(pose_y_min, y_val)
                    pose_y_max = max(pose_y_max, y_val)
                    pose_z_min = min(pose_z_min, z_val)
                    pose_z_max = max(pose_z_max, z_val)

        # Include the rotary axis location (moves with bed Z).
        pose_y_min = min(pose_y_min, self.y_center)
        pose_y_max = max(pose_y_max, self.y_center)
        pose_z_min = min(pose_z_min, z_off_min, z_off_max)
        pose_z_max = max(pose_z_max, z_off_min, z_off_max)

        if pose_y_min == float("inf"):
            pose_y_min, pose_y_max = 0.0, self.edge_length_mm
            pose_z_min, pose_z_max = -self.thickness_mm, self.axis_to_origin_mm

        # Place a fixed "laser head" above the entire motion envelope.
        head_z = pose_z_max + max(self.thickness_mm * 0.6, 8.0)
        self.pose_head_z = head_z
        pose_z_max = head_z

        pose_y_margin = max((pose_y_max - pose_y_min) * 0.08, 6.0)
        pose_z_margin = max((pose_z_max - pose_z_min) * 0.08, 6.0)
        self.pose_bounds = (
            pose_y_min - pose_y_margin,
            pose_y_max + pose_y_margin,
            pose_z_min - pose_z_margin,
            pose_z_max + pose_z_margin,
        )

    @property
    def y_center(self) -> float:
        return self.edge_length_mm / 2.0

    def _project_yz_abs(
        self, y_abs: float, radius: float, rotation_deg: float
    ) -> tuple[float, float]:
        y_local = y_abs - self.y_center
        delta = rotation_deg - self.rotation_zero_deg
        angle_rad = math.radians(delta)
        sin_t = math.sin(angle_rad)
        cos_t = math.cos(angle_rad)
        y_rot = y_local * cos_t - radius * sin_t
        z_rot = y_local * sin_t + radius * cos_t
        return self.y_center + y_rot, z_rot

    def _rotation_at(self, play_time: float) -> float:
        if not self.traces:
            return self.rotation_zero_deg
        for idx, cumulative in enumerate(self._cumulative):
            if play_time <= cumulative + 1e-9:
                trace = self.traces[idx]
                if trace.is_rotation_only and trace.duration > 0:
                    prior = 0.0 if idx == 0 else self._cumulative[idx - 1]
                    t = max(min((play_time - prior) / trace.duration, 1.0), 0.0)
                    return trace.rotation_deg + (trace.rotation_end_deg - trace.rotation_deg) * t
                return trace.rotation_end_deg
        return self.traces[-1].rotation_end_deg

    def _current_trace_index(self, play_time: float) -> int:
        for idx, cumulative in enumerate(self._cumulative):
            if play_time <= cumulative + 1e-9:
                return idx
        return len(self.traces) - 1

    @staticmethod
    def _current_index_for_group(
        play_time: float, traces: Sequence[BeamTrace], cumulative: Sequence[float]
    ) -> int:
        if not traces:
            return -1
        for idx, cum_val in enumerate(cumulative):
            if play_time <= cum_val + 1e-9:
                return idx
        return len(traces) - 1

    def _scale_for_rect(
        self, bounds: tuple[float, float, float, float], rect
    ) -> tuple[float, float, float, float]:
        """
        Compute pixels-per-unit scale and offsets for mapping world coords to screen.
        """
        x_min, x_max, y_min, y_max = bounds
        span_x = max(x_max - x_min, 1e-6)
        span_y = max(y_max - y_min, 1e-6)
        scale = min(
            (rect.width - 2 * self.padding) / span_x,
            (rect.height - 2 * self.padding) / span_y,
        )
        x_off = rect.left + self.padding
        y_off = rect.top + self.padding
        return scale, x_off, y_off, (x_min, y_min)

    def _to_screen_top(self, pt: tuple[float, float, float], rect, bounds) -> tuple[int, int]:
        scale, x_off, y_off, mins = self._scale_for_rect(bounds, rect)
        x_min, y_min = mins
        x = x_off + (pt[0] - x_min) * scale
        y = rect.bottom - self.padding - (pt[1] - y_min) * scale
        return int(round(x)), int(round(y))

    def _to_panel_top(self, pt: tuple[float, float, float], rect, bounds) -> tuple[int, int]:
        """
        Like `_to_screen_top`, but returns coordinates relative to `rect.topleft`.

        This is required when drawing onto a per-panel overlay surface that later
        gets blitted at `rect`.
        """
        x, y = self._to_screen_top(pt, rect, bounds)
        return x - rect.left, y - rect.top

    def _grid_steps(self, bounds, rect) -> tuple[float, float, float]:
        """
        Returns (scale_px_per_mm, x_step_mm, y_step_mm) for the given rect/bounds.
        """
        x_min, x_max, y_min, y_max = bounds
        x_span = x_max - x_min
        y_span = y_max - y_min
        scale, _, _, _ = self._scale_for_rect(bounds, rect)
        target_px = 80.0
        # _nice_spacing expects a span, and returns a "nice" step for ~8 lines.
        # Feed it 8 * desired_step_mm to target a fixed pixel spacing.
        step_for_px = self._nice_spacing((target_px / max(scale, 1e-9)) * 8.0)
        x_step = max(step_for_px, self._nice_spacing(x_span))
        y_step = max(step_for_px, self._nice_spacing(y_span))
        return scale, x_step, y_step

    def _draw_grid_top(self, screen, rect, bounds) -> None:
        import pygame

        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        x_min, x_max, y_min, y_max = bounds
        scale, x_step, y_step = self._grid_steps(bounds, rect)

        # Vertical lines (X)
        x_start = math.floor(x_min / x_step) * x_step
        label_color = (120, 135, 150)
        grid_color = (50, 60, 75, 90)
        while x_start <= x_max + 1e-6:
            x0 = self._to_panel_top((x_start, y_min, 0.0), rect, bounds)[0]
            pygame.draw.line(overlay, grid_color, (x0, 0), (x0, rect.height), 1)
            if x_step * scale >= 60.0:
                label = self._font_small.render(f"{x_start:.0f}", True, label_color)
                overlay.blit(label, (x0 + 2, 2))
            x_start += x_step

        # Horizontal lines (Y) centered so board midpoint (y_center) labels as 0.
        y_offset = self.y_center
        y_start_local = math.floor((y_min - y_offset) / y_step) * y_step
        y_local = y_start_local
        while y_local <= (y_max - y_offset) + 1e-6:
            y_abs = y_local + y_offset
            y0 = self._to_panel_top((x_min, y_abs, 0.0), rect, bounds)[1]
            pygame.draw.line(overlay, grid_color, (0, y0), (rect.width, y0), 1)
            if y_step * scale >= 60.0:
                label = self._font_small.render(f"{y_local:.0f}", True, label_color)
                overlay.blit(label, (2, y0 + 2))
            y_local += y_step

        screen.blit(overlay, rect)

    def _to_screen_edge(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        scale, x_off, y_off, mins = self._scale_for_rect(self.edge_bounds, rect)
        y_min, z_min = mins
        x = x_off + (yz[0] - y_min) * scale
        y = rect.bottom - self.padding - (yz[1] - z_min) * scale
        return int(round(x)), int(round(y))

    def _to_panel_edge(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        x, y = self._to_screen_edge(yz, rect)
        return x - rect.left, y - rect.top

    def _to_screen_pose(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        bounds = self.pose_bounds or self.edge_bounds
        scale, x_off, y_off, mins = self._scale_for_rect(bounds, rect)
        y_min, z_min = mins
        x = x_off + (yz[0] - y_min) * scale
        y = rect.bottom - self.padding - (yz[1] - z_min) * scale
        return int(round(x)), int(round(y))

    def _to_panel_pose(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        x, y = self._to_screen_pose(yz, rect)
        return x - rect.left, y - rect.top

    def _nice_spacing(self, span: float) -> float:
        if span <= 0:
            return 10.0
        raw = span / 8.0
        power = 10 ** math.floor(math.log10(raw))
        norm = raw / power
        if norm < 1.5:
            step = 1.0
        elif norm < 3.5:
            step = 2.0
        elif norm < 7.5:
            step = 5.0
        else:
            step = 10.0
        return step * power

    def _board_outline_points(
        self, rotation_deg: float, *, z_offset_mm: float = 0.0
    ) -> tuple[List[tuple[float, float, float]], List[tuple[float, float]]]:
        radius_top = self.axis_to_origin_mm
        radius_bottom = max(radius_top - self.thickness_mm, 0.001)
        y0_top, z0_top = self._project_yz_abs(0.0, radius_top, rotation_deg)
        y1_top, z1_top = self._project_yz_abs(self.edge_length_mm, radius_top, rotation_deg)
        y0_bot, z0_bot = self._project_yz_abs(0.0, radius_bottom, rotation_deg)
        y1_bot, z1_bot = self._project_yz_abs(self.edge_length_mm, radius_bottom, rotation_deg)
        z0_top += z_offset_mm
        z1_top += z_offset_mm
        z0_bot += z_offset_mm
        z1_bot += z_offset_mm

        top_poly = [
            (0.0, y0_top, z0_top),
            (self.thickness_mm, y0_top, z0_top),
            (self.thickness_mm, y1_top, z1_top),
            (0.0, y1_top, z1_top),
        ]
        edge_poly = [
            (y0_top, z0_top),
            (y1_top, z1_top),
            (y1_bot, z1_bot),
            (y0_bot, z0_bot),
        ]
        return top_poly, edge_poly

    def _edge_outline_local(self) -> List[tuple[float, float]]:
        """
        Board-local (Y,Z) outline for the edge view: a simple rectangle.
        """
        yc = self.y_center
        return [(-yc, 0.0), (yc, 0.0), (yc, -self.thickness_mm), (-yc, -self.thickness_mm)]

    def _edge_cut_polygons(
        self, traces: Sequence[BeamTrace]
    ) -> List[tuple[str, float, List[tuple[float, float]]]]:
        """
        Group visible cut traces into per-shape polygons in board-local (Y,Z).
        """
        polys: List[tuple[str, float, List[tuple[float, float]]]] = []
        group: List[BeamTrace] = []

        def flush() -> None:
            nonlocal group
            if not group:
                return
            rotation_deg = group[-1].rotation_end_deg
            delta = rotation_deg - self.rotation_zero_deg
            shear = self.thickness_mm * math.tan(math.radians(delta)) if abs(delta) > 1e-9 else 0.0
            y_vals = []
            for t in group:
                y_vals.extend([t.start_board_local[1], t.end_board_local[1]])
            y_top_min = min(y_vals)
            y_top_max = max(y_vals)
            y_bot_min = y_top_min - shear
            y_bot_max = y_top_max - shear
            # Clamp to stock span so cuts stay anchored inside the material rectangle.
            y_lo = -self.y_center
            y_hi = self.y_center
            y_top_min = max(y_lo, min(y_hi, y_top_min))
            y_top_max = max(y_lo, min(y_hi, y_top_max))
            y_bot_min = max(y_lo, min(y_hi, y_bot_min))
            y_bot_max = max(y_lo, min(y_hi, y_bot_max))
            if (y_top_max - y_top_min) < 1e-6 and (y_bot_max - y_bot_min) < 1e-6:
                group = []
                return
            poly = [
                (y_top_min, 0.0),
                (y_top_max, 0.0),
                (y_bot_max, -self.thickness_mm),
                (y_bot_min, -self.thickness_mm),
            ]
            polys.append((group[0].board, rotation_deg, poly))
            group = []

        for t in traces:
            if not t.is_cut:
                flush()
                continue
            if not group:
                group = [t]
                continue
            same_board = t.board == group[-1].board
            same_rot = abs(t.rotation_end_deg - group[-1].rotation_end_deg) < 1e-6
            if same_board and same_rot:
                group.append(t)
            else:
                flush()
                group = [t]
        flush()
        return polys

    def _beam_entry_exit(
        self, x_mm: float, y_mm: float, rotation_deg: float, *, z_offset_mm: float
    ) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        """
        Return world-space entry (top surface) and exit (bottom surface) points for a
        vertical beam at (x_mm, y_mm).

        Uses inverse projection on the top and bottom surfaces separately so the beam
        stays vertical in world space while the stock is rotated.
        """
        y_abs_top = invert_projected_y(
            y_mm,
            rotation_deg,
            axis_to_origin_mm=self.axis_to_origin_mm,
            y_center=self.y_center,
            rotation_zero_deg=self.rotation_zero_deg,
        )
        y_local_top = y_abs_top - self.y_center
        top_base = board_to_world_local(
            x_mm,
            y_local_top,
            0.0,
            rotation_deg,
            axis_to_origin_mm=self.axis_to_origin_mm,
            y_center=self.y_center,
            rotation_zero_deg=self.rotation_zero_deg,
        )
        top_world = (top_base[0], top_base[1], top_base[2] + z_offset_mm)

        radius_bottom = max(self.axis_to_origin_mm - self.thickness_mm, 0.001)
        y_abs_bottom = invert_projected_y(
            y_mm,
            rotation_deg,
            axis_to_origin_mm=radius_bottom,
            y_center=self.y_center,
            rotation_zero_deg=self.rotation_zero_deg,
        )
        y_local_bottom = y_abs_bottom - self.y_center
        bottom_base = board_to_world_local(
            x_mm,
            y_local_bottom,
            -self.thickness_mm,
            rotation_deg,
            axis_to_origin_mm=self.axis_to_origin_mm,
            y_center=self.y_center,
            rotation_zero_deg=self.rotation_zero_deg,
        )
        bottom_world = (bottom_base[0], bottom_base[1], bottom_base[2] + z_offset_mm)
        return top_world, bottom_world

    def _draw_edge_pose_inset(
        self,
        screen,
        *,
        rect,
        rotation_deg: float,
        machine_z: float,
        z_offset_cmd_mm: float,
        bed_offset_mm: float,
        beam_top_world: tuple[float, float, float],
        beam_bottom_world: tuple[float, float, float],
    ) -> None:
        """
        Draw a compact machine-frame side view showing:
        - board rotation about the rotary axis (U),
        - bed Z offset as axis/board translation,
        - a fixed laser head and the vertical beam through the stock.
        """
        import pygame

        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        bg = (10, 12, 18, 160)
        border = (32, 40, 54, 220)
        pygame.draw.rect(overlay, bg, overlay.get_rect(), border_radius=6)
        pygame.draw.rect(overlay, border, overlay.get_rect(), width=1, border_radius=6)

        # Rotated stock outline in world (machine) Y/Z coordinates.
        _, edge_poly = self._board_outline_points(rotation_deg, z_offset_mm=bed_offset_mm)
        pts = [self._to_panel_pose(pt, rect) for pt in edge_poly]
        stock_fill = (80, 92, 112, 90)
        stock_outline = (150, 165, 190, 220)
        pygame.draw.polygon(overlay, stock_fill, pts)
        pygame.draw.polygon(overlay, stock_outline, pts, width=2)

        # Rotary axis (moves with the bed).
        axis_pt = self._to_panel_pose((self.y_center, bed_offset_mm), rect)
        pygame.draw.circle(overlay, (240, 240, 240), axis_pt, 4)
        pygame.draw.circle(overlay, (240, 240, 240, 160), axis_pt, 10, width=1)

        # Fixed laser head height + beam line.
        head_z = (
            self.pose_head_z
            if self.pose_head_z is not None
            else (max(z for _, z in edge_poly) + 10.0)
        )
        beam_y = beam_top_world[1]
        head_pt = self._to_panel_pose((beam_y, head_z), rect)
        head_y_px = head_pt[1]
        pygame.draw.line(overlay, (190, 190, 190, 220), (0, head_y_px), (rect.width, head_y_px), 1)
        pygame.draw.rect(
            overlay,
            (200, 200, 200),
            pygame.Rect(head_pt[0] - 10, head_pt[1] - 5, 20, 10),
            border_radius=3,
        )

        beam_color = (255, 214, 102)
        beam_start = head_pt
        beam_end = self._to_panel_pose((beam_y, beam_bottom_world[2]), rect)
        pygame.draw.line(overlay, beam_color, beam_start, beam_end, 2)
        pygame.draw.circle(
            overlay,
            beam_color,
            self._to_panel_pose((beam_y, beam_top_world[2]), rect),
            3,
        )

        # Z sign indicator (bed direction for +Z) so it's obvious if the config
        # needs flipping for a given controller.
        arrow_x = rect.width - 26
        arrow_top = 34
        arrow_len = 22
        if self.z_positive_moves_bed_up:
            tip = (arrow_x, arrow_top)
            base = (arrow_x, arrow_top + arrow_len)
            label = "+Z↑"
        else:
            tip = (arrow_x, arrow_top + arrow_len)
            base = (arrow_x, arrow_top)
            label = "+Z↓"
        pygame.draw.line(overlay, (190, 190, 190, 220), base, tip, 2)
        pygame.draw.polygon(
            overlay,
            (190, 190, 190, 220),
            [
                tip,
                (tip[0] - 4, tip[1] + (6 if tip[1] == arrow_top else -6)),
                (tip[0] + 4, tip[1] + (6 if tip[1] == arrow_top else -6)),
            ],
        )
        txt = self._font_small.render(label, True, (170, 180, 200))
        overlay.blit(txt, (arrow_x - txt.get_width() // 2, arrow_top + arrow_len + 4))

        # Labels.
        delta_deg = rotation_deg - self.rotation_zero_deg
        line_h = self._hud_line_height_px()
        txt_u = self._font_small.render(
            f"U={rotation_deg:.1f}° (Δ{delta_deg:+.1f}°)",
            True,
            (225, 225, 225),
        )
        txt_z = self._font_small.render(
            f"Zcmd={machine_z:.3f} (Δcmd {z_offset_cmd_mm:+.3f})   Bed Δ={bed_offset_mm:+.3f}",
            True,
            (180, 190, 210),
        )
        overlay.blit(txt_u, (8, 6))
        overlay.blit(txt_z, (8, 6 + line_h))

        screen.blit(overlay, rect)

    def run(self) -> None:
        try:
            import pygame
        except Exception as exc:  # pragma: no cover - optional dependency
            log.error("pygame not available for simulation: %s", exc)
            return

        pygame.init()
        screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Laserdove pygame simulation")
        self._ensure_layout(pygame)

        clock = pygame.time.Clock()
        play_time = 0.0
        paused = False
        running = True
        while running:
            dt = clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        running = False
                    if event.key == pygame.K_SPACE:
                        paused = not paused
                    if event.key == pygame.K_r:
                        play_time = 0.0
                        paused = False

            if not paused and self.total_duration > 0:
                play_time = min(play_time + dt * self.time_scale, self.total_duration)

            screen.fill(self.bg_color)
            self._draw_views(screen, play_time)
            pygame.display.flip()

        pygame.quit()

    def capture_screenshots(
        self,
        out_dir: Path | str,
        *,
        every_s: float = 2.0,
        include_last: bool = True,
        prefix: str = "frame",
    ) -> List[dict]:
        """
        Render a time series of frames to PNG files.

        The edge and top views are board-local (Y vs thickness on edge, X/Y on top).
        """
        try:
            import pygame
        except Exception as exc:  # pragma: no cover - optional dependency
            log.error("pygame not available for capture: %s", exc)
            return []

        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        # Headless-friendly: allow running without a window.
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

        pygame.init()
        screen = pygame.Surface((self.width, self.height))
        self._ensure_layout(pygame, use_default_font=True)

        times: List[float] = []
        if self.total_duration <= 0:
            times = [0.0]
        else:
            t = 0.0
            step = max(every_s, 1e-6)
            while t < self.total_duration - 1e-9:
                times.append(t)
                t += step
            if include_last:
                if not times or not math.isclose(times[-1], self.total_duration, abs_tol=1e-9):
                    times.append(self.total_duration)

        meta: List[dict] = []
        for idx, play_time in enumerate(times):
            screen.fill(self.bg_color)
            self._draw_views(screen, play_time)
            frame_name = f"{prefix}_{idx:04d}_t{play_time:06.2f}.png"
            frame_file = out_path / frame_name
            pygame.image.save(screen, str(frame_file))
            meta.append(self._frame_metadata(play_time, file=frame_name))

        (out_path / "index.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
        pygame.quit()
        log.info("Wrote %d frames to %s", len(meta), out_path)
        return meta

    def _ensure_layout(self, pygame, *, use_default_font: bool = False) -> None:
        if self._font_small is None or self._font_large is None:
            if use_default_font:
                self._font_small = pygame.font.Font(None, 16)
                self._font_large = pygame.font.Font(None, 24)
            else:
                try:
                    self._font_small = pygame.font.SysFont("Arial", 14)
                    self._font_large = pygame.font.SysFont("Arial", 20, bold=True)
                except Exception:
                    self._font_small = pygame.font.Font(None, 16)
                    self._font_large = pygame.font.Font(None, 24)

        if self.top_rect_pos is None or self.top_rect_neg is None or self.edge_rect is None:
            col_w = (self.width - self.padding * 4) // 3
            panel_top = self._hud_total_height_px()
            panel_h = max(1, self.height - panel_top - self.padding)
            self.top_rect_pos = pygame.Rect(self.padding, panel_top, col_w, panel_h)
            self.top_rect_neg = pygame.Rect(self.padding * 2 + col_w, panel_top, col_w, panel_h)
            self.edge_rect = pygame.Rect(self.padding * 3 + col_w * 2, panel_top, col_w, panel_h)

    def _frame_metadata(self, play_time: float, *, file: str) -> dict:
        idx = self._current_trace_index(play_time)
        trace = self.traces[idx]
        rotation = self._rotation_at(play_time)
        prior = 0.0 if idx == 0 else self._cumulative[idx - 1]
        duration = max(trace.duration, 1e-9)
        progress = max(min((play_time - prior) / duration, 1.0), 0.0) if trace.duration > 0 else 1.0
        machine_z = trace.start_machine_z + (trace.end_machine_z - trace.start_machine_z) * progress
        z_ref = self.z_zero_tail_mm if trace.board == "tail" else self.z_zero_pin_mm
        return {
            "file": file,
            "t": play_time,
            "trace_index": idx,
            "board": trace.board,
            "rotation_deg": rotation,
            "machine_z": machine_z,
            "z_offset_mm": machine_z - z_ref,
            "is_cut": trace.is_cut,
            "power_pct": trace.power_pct,
            "duration_s": trace.duration,
            "source": trace.source,
        }

    def _draw_views(self, screen, play_time: float) -> None:
        import pygame

        if not self.traces:
            msg = self._font_large.render("No segments to visualize", True, (240, 240, 240))
            screen.blit(msg, (self.padding, self.padding))
            return

        idx = self._current_trace_index(play_time)
        trace = self.traces[idx]
        rotation = self._rotation_at(play_time)
        current_board = trace.board
        prior = 0.0 if idx == 0 else self._cumulative[idx - 1]
        duration = max(trace.duration, 1e-9)
        progress = max(min((play_time - prior) / duration, 1.0), 0.0) if trace.duration > 0 else 1.0

        spot_world = tuple(
            trace.start_world[i] + (trace.end_world[i] - trace.start_world[i]) * progress
            for i in range(3)
        )
        machine_z = trace.start_machine_z + (trace.end_machine_z - trace.start_machine_z) * progress
        z_ref = self.z_zero_tail_mm if current_board == "tail" else self.z_zero_pin_mm
        z_offset_cmd = machine_z - z_ref
        bed_offset = z_offset_cmd if self.z_positive_moves_bed_up else -z_offset_cmd

        # Reserve a panel header strip so titles/scales don't overlap grids.
        top_rect_pos = self._panel_content_rect(self.top_rect_pos)
        top_rect_neg = self._panel_content_rect(self.top_rect_neg)
        edge_rect = self._panel_content_rect(self.edge_rect)

        # Panels
        pygame.draw.rect(screen, (32, 40, 54), self.top_rect_pos, width=1, border_radius=6)
        pygame.draw.rect(screen, (32, 40, 54), self.top_rect_neg, width=1, border_radius=6)
        pygame.draw.rect(screen, (32, 40, 54), self.edge_rect, width=1, border_radius=6)

        def draw_board(rect, rot, bounds):
            overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
            top_poly, _ = self._board_outline_points(rot, z_offset_mm=0.0)
            pygame.draw.polygon(
                overlay,
                self.board_fill,
                [self._to_panel_top(pt, rect, bounds) for pt in top_poly],
            )
            screen.blit(overlay, rect)
            pygame.draw.lines(
                screen,
                self.board_outline,
                True,
                [self._to_screen_top(pt, rect, bounds) for pt in top_poly],
                1,
            )

        # Grids behind boards
        self._draw_grid_top(screen, top_rect_pos, self.top_bounds_pos)
        self._draw_grid_top(screen, top_rect_neg, self.top_bounds_neg)

        # Top panels render in board-local coordinates (no rotary foreshortening).
        draw_board(top_rect_pos, self.rotation_zero_deg, self.top_bounds_pos)
        draw_board(top_rect_neg, self.rotation_zero_deg, self.top_bounds_neg)

        # Edge view: board-local rectangle (Y vs thickness) so cuts don't "float"
        # when machine Z changes.
        edge_overlay = pygame.Surface(edge_rect.size, pygame.SRCALPHA)
        edge_poly = self._edge_outline_local()
        pygame.draw.polygon(
            edge_overlay,
            self.board_fill,
            [self._to_panel_edge(pt, edge_rect) for pt in edge_poly],
        )
        screen.blit(edge_overlay, edge_rect)
        pygame.draw.lines(
            screen,
            self.board_outline,
            True,
            [self._to_screen_edge(pt, edge_rect) for pt in edge_poly],
            1,
        )

        visible_traces = [t for i, t in enumerate(self.traces) if i < idx]
        if trace.is_cut:
            visible_traces.append(trace)
        self._draw_traces(
            screen,
            visible_traces,
            top_rect_pos=top_rect_pos,
            top_rect_neg=top_rect_neg,
            edge_rect=edge_rect,
        )

        beam_top_world, beam_bottom_world = self._beam_entry_exit(
            spot_world[0], spot_world[1], rotation, z_offset_mm=bed_offset
        )

        spot_board = tuple(
            trace.start_board_local[i]
            + (trace.end_board_local[i] - trace.start_board_local[i]) * progress
            for i in range(3)
        )
        spot_top = (spot_board[0], spot_board[1] + self.y_center, 0.0)
        spot_rect = top_rect_pos if current_board == "tail" else top_rect_neg
        spot_bounds = self.top_bounds_pos if current_board == "tail" else self.top_bounds_neg
        beam_color = (255, 214, 102)

        pygame.draw.circle(
            screen, beam_color, self._to_screen_top(spot_top, spot_rect, spot_bounds), 5
        )

        # Draw the beam in board-local edge coordinates (shears with rotation).
        radius_bottom = max(self.axis_to_origin_mm - self.thickness_mm, 0.001)
        y_abs_top = invert_projected_y(
            spot_world[1],
            rotation,
            axis_to_origin_mm=self.axis_to_origin_mm,
            y_center=self.y_center,
            rotation_zero_deg=self.rotation_zero_deg,
        )
        y_abs_bottom = invert_projected_y(
            spot_world[1],
            rotation,
            axis_to_origin_mm=radius_bottom,
            y_center=self.y_center,
            rotation_zero_deg=self.rotation_zero_deg,
        )
        beam_top_edge = self._to_screen_edge((y_abs_top - self.y_center, 0.0), edge_rect)
        beam_bottom_edge = self._to_screen_edge(
            (y_abs_bottom - self.y_center, -self.thickness_mm), edge_rect
        )
        pygame.draw.line(screen, beam_color, beam_top_edge, beam_bottom_edge, 2)

        # Compact pose inset (machine frame) at the top of the edge panel:
        # shows U rotation + Z motion + a fixed head/beam.
        pose_top = edge_rect.top + 8
        pose_rect = pygame.Rect(
            edge_rect.left + 6,
            pose_top,
            edge_rect.width - 12,
            int(edge_rect.height * 0.32),
        )
        self._draw_edge_pose_inset(
            screen,
            rect=pose_rect,
            rotation_deg=rotation,
            machine_z=machine_z,
            z_offset_cmd_mm=z_offset_cmd,
            bed_offset_mm=bed_offset,
            beam_top_world=beam_top_world,
            beam_bottom_world=beam_bottom_world,
        )

        self._draw_hud(
            screen,
            trace,
            rotation,
            current_board,
            beam_top_world,
            machine_z=machine_z,
            z_offset_cmd_mm=z_offset_cmd,
            bed_offset_mm=bed_offset,
        )

    def _draw_traces(
        self,
        screen,
        traces: Sequence[BeamTrace],
        *,
        top_rect_pos,
        top_rect_neg,
        edge_rect,
    ) -> None:
        import pygame

        top_overlay_pos = pygame.Surface(top_rect_pos.size, pygame.SRCALPHA)
        top_overlay_neg = pygame.Surface(top_rect_neg.size, pygame.SRCALPHA)
        edge_overlay = pygame.Surface(edge_rect.size, pygame.SRCALPHA)
        current_key = None
        current_points: list[tuple[int, int]] = []
        y_center = self.y_center

        def to_panel_from_board(
            pt_local: tuple[float, float, float], rect, bounds
        ) -> tuple[int, int]:
            return self._to_panel_top((pt_local[0], pt_local[1] + y_center, 0.0), rect, bounds)

        def flush_top_path() -> None:
            nonlocal current_key, current_points
            if current_key is None or len(current_points) < 2:
                current_key = None
                current_points = []
                return
            overlay_target, line_color, fill_color = current_key

            # Snap-close if the final point is within 1px of the start.
            first = current_points[0]
            last = current_points[-1]
            if abs(first[0] - last[0]) <= 1 and abs(first[1] - last[1]) <= 1:
                current_points[-1] = first
            closed = current_points[0] == current_points[-1] and len(current_points) >= 3

            pygame.draw.lines(overlay_target, fill_color, closed, current_points, width=6)
            pygame.draw.lines(overlay_target, line_color, closed, current_points, width=2)
            # Reduce visible gaps at corners caused by per-segment rasterization.
            for pt in current_points:
                pygame.draw.circle(overlay_target, fill_color, pt, 3)
                pygame.draw.circle(overlay_target, line_color, pt, 2)

            current_key = None
            current_points = []

        for trace in traces:
            if not trace.is_cut:
                flush_top_path()
                continue

            if trace.board == "tail":
                top_rect = top_rect_pos
                top_bounds = self.top_bounds_pos
                overlay_target = top_overlay_pos
                line_color = self.tail_cut_color
                fill_color = self.tail_fill
            else:
                top_rect = top_rect_neg
                top_bounds = self.top_bounds_neg
                overlay_target = top_overlay_neg
                if trace.rotation_end_deg >= self.rotation_zero_deg - 1e-6:
                    line_color = self.pin_pos_cut_color
                    fill_color = self.pin_pos_fill
                else:
                    line_color = self.pin_neg_cut_color
                    fill_color = self.pin_neg_fill

            key = (overlay_target, line_color, fill_color)
            start_top_pt = to_panel_from_board(trace.start_board_local, top_rect, top_bounds)
            end_top_pt = to_panel_from_board(trace.end_board_local, top_rect, top_bounds)

            if current_key != key or not current_points:
                flush_top_path()
                current_key = key
                current_points = [start_top_pt, end_top_pt]
                continue

            last_pt = current_points[-1]
            if abs(start_top_pt[0] - last_pt[0]) <= 1 and abs(start_top_pt[1] - last_pt[1]) <= 1:
                # Snap to the prior endpoint so the polyline stays contiguous after rounding.
                current_points.append(end_top_pt)
            else:
                flush_top_path()
                current_key = key
                current_points = [start_top_pt, end_top_pt]

        flush_top_path()

        # The edge view is intended to communicate the *edge* cuts (pin board).
        # Tail-board operations are face cuts and are better represented in the
        # Top Tail panel, so filter them out here to avoid confusing overlays.
        edge_traces = [t for t in traces if t.board == "pin"]
        for board, rot, poly in self._edge_cut_polygons(edge_traces):
            if board == "tail":
                line_color = self.tail_cut_color
                fill_color = self.tail_fill
            elif rot >= self.rotation_zero_deg - 1e-6:
                line_color = self.pin_pos_cut_color
                fill_color = self.pin_pos_fill
            else:
                line_color = self.pin_neg_cut_color
                fill_color = self.pin_neg_fill
            pts = [self._to_panel_edge(p, edge_rect) for p in poly]
            # Use normal alpha blending so overlaps brighten/mix without immediately saturating.
            poly_fill = (fill_color[0], fill_color[1], fill_color[2], min(fill_color[3] * 2, 160))
            tmp = pygame.Surface(edge_rect.size, pygame.SRCALPHA)
            pygame.draw.polygon(tmp, poly_fill, pts)
            edge_overlay.blit(tmp, (0, 0))
            pygame.draw.polygon(edge_overlay, line_color, pts, width=2)
        screen.blit(top_overlay_pos, top_rect_pos)
        screen.blit(top_overlay_neg, top_rect_neg)
        screen.blit(edge_overlay, edge_rect)

    def _draw_hud(
        self,
        screen,
        trace: BeamTrace,
        rotation: float,
        board: str,
        beam_top: tuple[float, float, float],
        *,
        machine_z: float,
        z_offset_cmd_mm: float,
        bed_offset_mm: float,
    ) -> None:
        import pygame

        bed_dir = "up" if self.z_positive_moves_bed_up else "down"
        labels = [
            f"Board: {board}",
            f"Rotation: {rotation:.2f}°",
            f"Spot XYZ: ({beam_top[0]:.2f}, {beam_top[1]:.2f}, {beam_top[2]:.2f})",
            (
                f"Machine Z: {machine_z:.3f} mm (Δcmd {z_offset_cmd_mm:+.3f})   "
                f"Bed Δ: {bed_offset_mm:+.3f} mm (Z+ bed {bed_dir})"
            ),
            f"Power: {trace.power_pct:.1f}% {'CUT' if trace.is_cut else 'MOVE'}",
            "Space: pause/play | R: restart | Esc/Q: quit",
        ]
        line_h = self._hud_line_height_px()
        hud_rect = pygame.Rect(0, 0, self.width, self._hud_total_height_px())
        pygame.draw.rect(screen, (10, 12, 18), hud_rect)
        pygame.draw.line(
            screen,
            (32, 40, 54),
            (0, hud_rect.bottom - 1),
            (self.width, hud_rect.bottom - 1),
            1,
        )
        for idx, text in enumerate(labels):
            surf = self._font_small.render(text, True, (230, 230, 230))
            screen.blit(surf, (self.padding, self.padding + idx * line_h))

        header_h = self._panel_header_height_px()

        def draw_panel_header(rect, title: str, *, subtitle: str | None = None) -> None:
            header_rect = pygame.Rect(rect.left, rect.top, rect.width, header_h)
            pygame.draw.rect(screen, (12, 14, 22), header_rect)
            pygame.draw.line(
                screen,
                (32, 40, 54),
                (header_rect.left, header_rect.bottom - 1),
                (header_rect.right, header_rect.bottom - 1),
                1,
            )
            title_surf = self._font_large.render(title, True, (210, 210, 210))
            screen.blit(title_surf, (header_rect.left + 8, header_rect.top + 4))
            if subtitle:
                subtitle_surf = self._font_small.render(subtitle, True, (150, 165, 190))
                sw = subtitle_surf.get_width()
                screen.blit(
                    subtitle_surf,
                    (header_rect.right - sw - 8, header_rect.top + 6),
                )

        # Show a simple scale hint derived from the Y grid step.
        top_pos_content = self._panel_content_rect(self.top_rect_pos)
        top_neg_content = self._panel_content_rect(self.top_rect_neg)
        _, x_step_pos, y_step_pos = self._grid_steps(self.top_bounds_pos, top_pos_content)
        _, x_step_neg, y_step_neg = self._grid_steps(self.top_bounds_neg, top_neg_content)

        draw_panel_header(
            self.top_rect_pos,
            "Top Tail",
            subtitle=f"Grid {x_step_pos:.0f}×{y_step_pos:.0f} mm",
        )
        draw_panel_header(
            self.top_rect_neg,
            "Top Pin",
            subtitle=f"Grid {x_step_neg:.0f}×{y_step_neg:.0f} mm",
        )
        draw_panel_header(self.edge_rect, "Edge")


def run_pygame_viewer(
    commands: Iterable[Command],
    run_config,
    *,
    time_scale: float = 1.0,
    screenshot_dir: Path | None = None,
    screenshot_every_s: float = 2.0,
    rd_dir: Path | None = None,
) -> None:
    """
    Build beam traces from commands and launch the pygame viewer.
    """
    traces: List[BeamTrace] = []
    if rd_dir is not None:
        rd_path = Path(rd_dir)
        if not rd_path.exists():
            raise ValueError(f"RD directory not found: {rd_path}")
        rd_files = sorted(rd_path.glob("*.rd"))
        if not rd_files:
            raise ValueError(f"No .rd files found in {rd_path}")
        traces = build_beam_traces_from_rd_files(
            rd_files,
            commands,
            joint_params=run_config.joint_params,
            jig_params=run_config.jig_params,
            machine_params=run_config.machine_params,
        )
    else:
        traces = build_beam_traces(
            commands,
            joint_params=run_config.joint_params,
            jig_params=run_config.jig_params,
            machine_params=run_config.machine_params,
            movement_only=run_config.movement_only or run_config.reset_only,
            air_assist=run_config.machine_params.air_assist,
            start_board="tail",
        )
    if not traces:
        log.info("No segments to visualize in pygame viewer.")
        return
    viewer = PygameSimulationViewer(
        traces,
        edge_length_mm=run_config.joint_params.edge_length_mm,
        thickness_mm=run_config.joint_params.thickness_mm,
        axis_to_origin_mm=run_config.jig_params.axis_to_origin_mm,
        rotation_zero_deg=run_config.jig_params.rotation_zero_deg,
        z_zero_tail_mm=run_config.machine_params.z_zero_tail_mm,
        z_zero_pin_mm=run_config.machine_params.z_zero_pin_mm,
        z_positive_moves_bed_up=run_config.machine_params.z_positive_moves_bed_up,
        time_scale=time_scale,
    )
    if screenshot_dir is not None:
        viewer.capture_screenshots(screenshot_dir, every_s=screenshot_every_s)
        return
    viewer.run()
