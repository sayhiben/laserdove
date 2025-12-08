# pygame_simulator.py
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, List, Sequence

from .model import Command
from .panda3d_simulator import capture_segments_from_commands

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

    traces: List[BeamTrace] = []
    for seg in playback:
        z_ref = _z_ref(seg.board, machine_params.z_zero_tail_mm, machine_params.z_zero_pin_mm)
        start_machine_z = seg.start_board[2] + z_ref
        end_machine_z = seg.end_board[2] + z_ref
        y_center = joint_params.edge_length_mm / 2.0

        def surface_world(
            x_b: float, y_local: float, rotation_deg: float, z_local: float
        ) -> tuple[float, float, float]:
            """
            Map board-local coordinates to world space using the same math as board_to_world_local,
            but forcing z_local to the board surface so visual rotation does not wander with head Z.
            """
            delta = rotation_deg - jig_params.rotation_zero_deg
            angle_rad = math.radians(abs(delta))
            sin_t = math.sin(math.radians(delta))
            cos_t = math.cos(angle_rad)
            radius = jig_params.axis_to_origin_mm + z_local
            y_rot = y_local * cos_t - radius * sin_t
            z_rot = y_local * sin_t + radius * cos_t
            return (x_b, y_center + y_rot, z_rot)

        start_top = surface_world(
            seg.start_board[0], seg.start_board[1], seg.start_rotation_deg, 0.0
        )
        start_bottom = surface_world(
            seg.start_board[0],
            seg.start_board[1],
            seg.start_rotation_deg,
            -joint_params.thickness_mm,
        )
        end_top = surface_world(seg.end_board[0], seg.end_board[1], seg.end_rotation_deg, 0.0)
        end_bottom = surface_world(
            seg.end_board[0], seg.end_board[1], seg.end_rotation_deg, -joint_params.thickness_mm
        )

        is_rotation_only = (
            math.isclose(seg.start_world[0], seg.end_world[0], abs_tol=1e-9)
            and math.isclose(seg.start_world[1], seg.end_world[1], abs_tol=1e-9)
            and math.isclose(seg.start_world[2], seg.end_world[2], abs_tol=1e-9)
            and not math.isclose(seg.start_rotation_deg, seg.end_rotation_deg, abs_tol=1e-9)
        )

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
                rotation_deg=seg.start_rotation_deg,
                rotation_end_deg=seg.end_rotation_deg,
                duration=seg.duration,
                power_pct=seg.power_pct,
                air_assist=seg.air_assist,
                start_machine_z=start_machine_z,
                end_machine_z=end_machine_z,
                source=seg.source,
                is_rotation_only=is_rotation_only,
            )
        )

    return traces


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
        time_scale: float = 1.0,
    ) -> None:
        self.traces = list(traces)
        self.edge_length_mm = edge_length_mm
        self.thickness_mm = thickness_mm
        self.axis_to_origin_mm = axis_to_origin_mm
        self.rotation_zero_deg = rotation_zero_deg
        self.time_scale = time_scale
        self.total_duration = sum(max(t.duration, 0.0) for t in self.traces)
        self._cumulative: List[float] = []
        running = 0.0
        for t in self.traces:
            running += max(t.duration, 0.0)
            self._cumulative.append(running)

        self.pos_traces = [
            t for t in self.traces if t.rotation_end_deg >= self.rotation_zero_deg - 1e-6
        ]
        self.neg_traces = [
            t for t in self.traces if t.rotation_end_deg < self.rotation_zero_deg - 1e-6
        ]
        self._pos_cumulative = self._build_cumulative(self.pos_traces)
        self._neg_cumulative = self._build_cumulative(self.neg_traces)

        self.width = 1500
        self.height = 820
        self.padding = 18
        self.top_rect_pos = None
        self.top_rect_neg = None
        self.edge_rect = None
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
        self.board_fill = (36, 44, 58, 140)

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
            yz_min_y, yz_max_y = float("inf"), -float("inf")
            yz_min_z, yz_max_z = float("inf"), -float("inf")

            def add_top(pt: tuple[float, float, float]) -> None:
                nonlocal x_min, x_max, y_min, y_max
                x_min = min(x_min, pt[0])
                x_max = max(x_max, pt[0])
                y_min = min(y_min, pt[1])
                y_max = max(y_max, pt[1])

            def add_edge(pt: tuple[float, float, float]) -> None:
                nonlocal yz_min_y, yz_max_y, yz_min_z, yz_max_z
                yz_min_y = min(yz_min_y, pt[1])
                yz_max_y = max(yz_max_y, pt[1])
                yz_min_z = min(yz_min_z, pt[2])
                yz_max_z = max(yz_max_z, pt[2])

            for trace in traces:
                for pt in (trace.start_top, trace.end_top, trace.start_bottom, trace.end_bottom):
                    add_top(pt)
                    add_edge(pt)
                for rot in (trace.rotation_deg, trace.rotation_end_deg):
                    radius_top = self.axis_to_origin_mm
                    radius_bottom = max(radius_top - self.thickness_mm, 0.001)
                    for y_abs in (0.0, self.edge_length_mm):
                        y_t, z_t = self._project_yz_abs(y_abs, radius_top, rot)
                        y_b, z_b = self._project_yz_abs(y_abs, radius_bottom, rot)
                        add_edge((0.0, y_t, z_t))
                        add_edge((0.0, y_b, z_b))

            if x_min == float("inf"):
                x_min, x_max = 0.0, self.thickness_mm
                y_min, y_max = 0.0, self.edge_length_mm
                yz_min_y, yz_max_y = 0.0, self.edge_length_mm
                yz_min_z, yz_max_z = -self.axis_to_origin_mm * 0.2, self.axis_to_origin_mm * 1.2

            span_x = max(x_max - x_min, 1e-3)
            span_y = max(y_max - y_min, 1e-3)
            span_ey = max(yz_max_y - yz_min_y, 1e-3)
            span_ez = max(yz_max_z - yz_min_z, 1e-3)
            margin_x = span_x * 0.08
            margin_y = span_y * 0.08
            margin_ey = span_ey * 0.08
            margin_ez = span_ez * 0.08
            top_bounds = (
                x_min - margin_x,
                x_max + margin_x,
                y_min - margin_y,
                y_max + margin_y,
            )
            edge_bounds = (
                yz_min_y - margin_ey,
                yz_max_y + margin_ey,
                yz_min_z - margin_ez,
                yz_max_z + margin_ez,
            )
            return top_bounds, edge_bounds

        # Compute combined bounds across all traces, then enforce a target grid span (150% of edge length).
        top_bounds_all, edge_bounds_all = bounds_for(self.traces)
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
        self.edge_bounds = edge_bounds_all
        self.top_bounds_pos = self.top_bounds_common
        self.top_bounds_neg = self.top_bounds_common

    @property
    def y_center(self) -> float:
        return self.edge_length_mm / 2.0

    def _project_yz_abs(
        self, y_abs: float, radius: float, rotation_deg: float
    ) -> tuple[float, float]:
        y_local = y_abs - self.y_center
        delta = rotation_deg - self.rotation_zero_deg
        angle_rad = math.radians(abs(delta))
        sin_t = math.sin(math.radians(delta))
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
        return int(x), int(y)

    def _draw_grid_top(self, screen, rect, bounds) -> None:
        import pygame

        overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
        x_min, x_max, y_min, y_max = bounds
        x_span = x_max - x_min
        y_span = y_max - y_min
        x_step = self._nice_spacing(x_span)
        y_step = self._nice_spacing(y_span)

        # Vertical lines (X)
        x_start = math.floor(x_min / x_step) * x_step
        label_color = (120, 135, 150)
        grid_color = (50, 60, 75, 90)
        while x_start <= x_max + 1e-6:
            x0 = self._to_screen_top((x_start, y_min, 0.0), rect, bounds)[0]
            pygame.draw.line(overlay, grid_color, (x0, 0), (x0, rect.height), 1)
            label = self._font_small.render(f"{x_start:.0f}", True, label_color)
            overlay.blit(label, (x0 + 2, 2))
            x_start += x_step

        # Horizontal lines (Y) centered so board midpoint (y_center) labels as 0.
        y_offset = self.y_center
        y_start_local = math.floor((y_min - y_offset) / y_step) * y_step
        y_local = y_start_local
        while y_local <= (y_max - y_offset) + 1e-6:
            y_abs = y_local + y_offset
            y0 = self._to_screen_top((x_min, y_abs, 0.0), rect, bounds)[1]
            pygame.draw.line(overlay, grid_color, (0, y0), (rect.width, y0), 1)
            label = self._font_small.render(f"{y_local:.0f}", True, label_color)
            overlay.blit(label, (2, y0 + 2))
            y_local += y_step

        screen.blit(overlay, rect)

    def _to_screen_edge(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        scale, x_off, y_off, mins = self._scale_for_rect(self.edge_bounds, rect)
        y_min, z_min = mins
        x = x_off + (yz[0] - y_min) * scale
        y = rect.bottom - self.padding - (yz[1] - z_min) * scale
        return int(x), int(y)

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
        self, rotation_deg: float
    ) -> tuple[List[tuple[float, float, float]], List[tuple[float, float]]]:
        radius_top = self.axis_to_origin_mm
        radius_bottom = max(radius_top - self.thickness_mm, 0.001)
        y0_top, z0_top = self._project_yz_abs(0.0, radius_top, rotation_deg)
        y1_top, z1_top = self._project_yz_abs(self.edge_length_mm, radius_top, rotation_deg)
        y0_bot, z0_bot = self._project_yz_abs(0.0, radius_bottom, rotation_deg)
        y1_bot, z1_bot = self._project_yz_abs(self.edge_length_mm, radius_bottom, rotation_deg)

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

    def run(self) -> None:
        try:
            import pygame
        except Exception as exc:  # pragma: no cover - optional dependency
            log.error("pygame not available for simulation: %s", exc)
            return

        pygame.init()
        screen = pygame.display.set_mode((self.width, self.height))
        pygame.display.set_caption("Laserdove pygame simulation")
        col_w = (self.width - self.padding * 4) // 3
        self.top_rect_pos = pygame.Rect(
            self.padding, self.padding, col_w, self.height - 2 * self.padding
        )
        self.top_rect_neg = pygame.Rect(
            self.padding * 2 + col_w, self.padding, col_w, self.height - 2 * self.padding
        )
        self.edge_rect = pygame.Rect(
            self.padding * 3 + col_w * 2,
            self.padding,
            col_w,
            self.height - 2 * self.padding,
        )

        self._font_small = pygame.font.SysFont("Arial", 14)
        self._font_large = pygame.font.SysFont("Arial", 20, bold=True)

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
        # Panels
        pygame.draw.rect(screen, (32, 40, 54), self.top_rect_pos, width=1, border_radius=6)
        pygame.draw.rect(screen, (32, 40, 54), self.top_rect_neg, width=1, border_radius=6)
        pygame.draw.rect(screen, (32, 40, 54), self.edge_rect, width=1, border_radius=6)

        rot_pos_idx = self._current_index_for_group(
            play_time, self.pos_traces, self._pos_cumulative
        )
        rot_neg_idx = self._current_index_for_group(
            play_time, self.neg_traces, self._neg_cumulative
        )
        rot_pos = (
            self.pos_traces[rot_pos_idx].rotation_end_deg
            if rot_pos_idx >= 0
            else self.rotation_zero_deg
        )
        rot_neg = (
            self.neg_traces[rot_neg_idx].rotation_end_deg
            if rot_neg_idx >= 0
            else -self.rotation_zero_deg
        )

        def draw_board(rect, rot, bounds):
            overlay = pygame.Surface(rect.size, pygame.SRCALPHA)
            top_poly, _ = self._board_outline_points(rot)
            pygame.draw.polygon(
                overlay, self.board_fill, [self._to_screen_top(pt, rect, bounds) for pt in top_poly]
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
        self._draw_grid_top(screen, self.top_rect_pos, self.top_bounds_pos)
        self._draw_grid_top(screen, self.top_rect_neg, self.top_bounds_neg)

        draw_board(self.top_rect_pos, rot_pos, self.top_bounds_pos)
        draw_board(self.top_rect_neg, rot_neg, self.top_bounds_neg)

        top_poly, edge_poly = self._board_outline_points(rotation)
        edge_overlay = pygame.Surface(self.edge_rect.size, pygame.SRCALPHA)
        pygame.draw.polygon(
            edge_overlay,
            self.board_fill,
            [self._to_screen_edge(pt, self.edge_rect) for pt in edge_poly],
        )
        screen.blit(edge_overlay, self.edge_rect)
        pygame.draw.lines(
            screen,
            self.board_outline,
            True,
            [self._to_screen_edge(pt, self.edge_rect) for pt in edge_poly],
            1,
        )

        visible_traces = [
            t for i, t in enumerate(self.traces) if self._cumulative[i] <= play_time + 1e-9
        ]
        self._draw_traces(screen, visible_traces)

        head_top = trace.end_top
        head_edge = (trace.end_top[1], trace.end_machine_z)
        head_rect = (
            self.top_rect_pos
            if trace.rotation_end_deg >= self.rotation_zero_deg - 1e-6
            else self.top_rect_neg
        )
        head_bounds = self.top_bounds_pos if head_rect is self.top_rect_pos else self.top_bounds_neg
        pygame.draw.circle(
            screen, (255, 214, 102), self._to_screen_top(head_top, head_rect, head_bounds), 5
        )
        pygame.draw.circle(
            screen, (255, 214, 102), self._to_screen_edge(head_edge, self.edge_rect), 6
        )

        self._draw_hud(screen, trace, rotation, current_board, head_top)

    def _draw_traces(self, screen, traces: Sequence[BeamTrace]) -> None:
        import pygame

        top_overlay_pos = pygame.Surface(self.top_rect_pos.size, pygame.SRCALPHA)
        top_overlay_neg = pygame.Surface(self.top_rect_neg.size, pygame.SRCALPHA)
        edge_overlay = pygame.Surface(self.edge_rect.size, pygame.SRCALPHA)
        for trace in traces:
            color = self.cut_color if trace.is_cut else self.move_color
            top_rect = (
                self.top_rect_pos
                if trace.rotation_end_deg >= self.rotation_zero_deg - 1e-6
                else self.top_rect_neg
            )
            top_bounds = (
                self.top_bounds_pos if top_rect is self.top_rect_pos else self.top_bounds_neg
            )
            overlay_target = top_overlay_pos if top_rect is self.top_rect_pos else top_overlay_neg

            start_top_pt = self._to_screen_top(trace.start_top, top_rect, top_bounds)
            end_top_pt = self._to_screen_top(trace.end_top, top_rect, top_bounds)
            start_bottom_pt = self._to_screen_top(trace.start_bottom, top_rect, top_bounds)
            end_bottom_pt = self._to_screen_top(trace.end_bottom, top_rect, top_bounds)

            start_edge_top = self._to_screen_edge(
                (trace.start_top[1], trace.start_top[2]), self.edge_rect
            )
            end_edge_top = self._to_screen_edge(
                (trace.end_top[1], trace.end_top[2]), self.edge_rect
            )
            start_edge_bottom = self._to_screen_edge(
                (trace.start_bottom[1], trace.start_bottom[2]), self.edge_rect
            )
            end_edge_bottom = self._to_screen_edge(
                (trace.end_bottom[1], trace.end_bottom[2]), self.edge_rect
            )

            if trace.is_cut:
                pygame.draw.line(overlay_target, self.cut_fill, start_top_pt, end_top_pt, 6)
                pygame.draw.line(edge_overlay, self.cut_fill, start_edge_top, end_edge_top, 6)
            pygame.draw.line(screen, color, start_top_pt, end_top_pt, 2)
            pygame.draw.line(screen, color, start_bottom_pt, end_bottom_pt, 1)
            pygame.draw.line(screen, color, start_edge_top, end_edge_top, 2)
            pygame.draw.line(screen, color, start_edge_bottom, end_edge_bottom, 1)
            pygame.draw.line(screen, color, start_top_pt, start_bottom_pt, 1)
            pygame.draw.line(screen, color, end_top_pt, end_bottom_pt, 1)
        screen.blit(top_overlay_pos, self.top_rect_pos)
        screen.blit(top_overlay_neg, self.top_rect_neg)
        screen.blit(edge_overlay, self.edge_rect)

    def _draw_hud(
        self,
        screen,
        trace: BeamTrace,
        rotation: float,
        board: str,
        head_top: tuple[float, float, float],
    ) -> None:
        labels = [
            f"Board: {board}",
            f"Rotation: {rotation:.2f}°",
            f"Head XYZ: ({head_top[0]:.2f}, {head_top[1]:.2f}, {head_top[2]:.2f})",
            f"Machine Z: {trace.end_machine_z:.3f} mm",
            f"Power: {trace.power_pct:.1f}% {'CUT' if trace.is_cut else 'MOVE'}",
            "Space: pause/play | R: restart | Esc/Q: quit",
        ]
        for idx, text in enumerate(labels):
            surf = self._font_small.render(text, True, (230, 230, 230))
            screen.blit(surf, (self.padding, self.padding + idx * 18))

        title_top = self._font_large.render("Top +θ", True, (210, 210, 210))
        title_top_neg = self._font_large.render("Top -θ", True, (210, 210, 210))
        title_edge = self._font_large.render("Edge", True, (210, 210, 210))
        screen.blit(title_top, (self.top_rect_pos.left + 8, self.top_rect_pos.top - 4))
        screen.blit(title_top_neg, (self.top_rect_neg.left + 8, self.top_rect_neg.top - 4))
        screen.blit(title_edge, (self.edge_rect.left + 8, self.edge_rect.top - 4))


def run_pygame_viewer(commands: Iterable[Command], run_config, *, time_scale: float = 1.0) -> None:
    """
    Build beam traces from commands and launch the pygame viewer.
    """
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
        time_scale=time_scale,
    )
    viewer.run()
