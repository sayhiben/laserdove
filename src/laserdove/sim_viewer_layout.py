from __future__ import annotations

import math
from typing import List, Sequence

from .model import BoardSide
from .sim_traces import BeamTrace


class ViewerLayoutMixin:
    """Mixin for viewer layout and coordinate mapping."""

    def _hud_line_height_px(self) -> int:
        """Internal helper to hud line height px."""
        if self._font_small is None:
            return 18
        return int(self._font_small.get_linesize() + 2)

    def _hud_total_height_px(self) -> int:
        # Global HUD starts at y=self.padding and ends with an extra padding.
        """Internal helper to hud total height px."""
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
        """Internal helper to panel content rect."""
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
        """Build cumulative."""
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
            """Helper to bounds for."""
            x_min, x_max = float("inf"), -float("inf")
            y_min, y_max = float("inf"), -float("inf")

            def add_top(pt: tuple[float, float, float]) -> None:
                """Helper to add top."""
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
            if t.board != BoardSide.PIN:
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
        """Return y center."""
        return self.edge_length_mm / 2.0

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
        """Convert to screen top coordinates."""
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

    def _to_screen_edge(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        """Convert to screen edge coordinates."""
        scale, x_off, y_off, mins = self._scale_for_rect(self.edge_bounds, rect)
        y_min, z_min = mins
        x = x_off + (yz[0] - y_min) * scale
        y = rect.bottom - self.padding - (yz[1] - z_min) * scale
        return int(round(x)), int(round(y))

    def _to_panel_edge(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        """Convert to panel edge coordinates."""
        x, y = self._to_screen_edge(yz, rect)
        return x - rect.left, y - rect.top

    def _to_screen_pose(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        """Convert to screen pose coordinates."""
        bounds = self.pose_bounds or self.edge_bounds
        scale, x_off, y_off, mins = self._scale_for_rect(bounds, rect)
        y_min, z_min = mins
        x = x_off + (yz[0] - y_min) * scale
        y = rect.bottom - self.padding - (yz[1] - z_min) * scale
        return int(round(x)), int(round(y))

    def _to_panel_pose(self, yz: tuple[float, float], rect) -> tuple[int, int]:
        """Convert to panel pose coordinates."""
        x, y = self._to_screen_pose(yz, rect)
        return x - rect.left, y - rect.top

    def _nice_spacing(self, span: float) -> float:
        """Internal helper to nice spacing."""
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

    def _edge_outline_local(self) -> List[tuple[float, float]]:
        """
        Board-local (Y,Z) outline for the edge view: a simple rectangle.
        """
        yc = self.y_center
        return [(-yc, 0.0), (yc, 0.0), (yc, -self.thickness_mm), (-yc, -self.thickness_mm)]

    def _ensure_layout(self, pygame, *, use_default_font: bool = False) -> None:
        """Ensure layout."""
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
