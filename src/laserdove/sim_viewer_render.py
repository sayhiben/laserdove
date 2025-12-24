from __future__ import annotations

import math
from typing import Sequence

from .model import BoardSide
from .sim_kinematics import invert_projected_y
from .sim_traces import BeamTrace


class ViewerRenderMixin:
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
        z_ref = self.z_zero_tail_mm if current_board == BoardSide.TAIL else self.z_zero_pin_mm
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
        spot_rect = top_rect_pos if current_board == BoardSide.TAIL else top_rect_neg
        spot_bounds = (
            self.top_bounds_pos if current_board == BoardSide.TAIL else self.top_bounds_neg
        )
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

            if trace.board == BoardSide.TAIL:
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
        edge_traces = [t for t in traces if t.board == BoardSide.PIN]
        for board, rot, poly in self._edge_cut_polygons(edge_traces):
            if board == BoardSide.TAIL:
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
        board: BoardSide,
        beam_top: tuple[float, float, float],
        *,
        machine_z: float,
        z_offset_cmd_mm: float,
        bed_offset_mm: float,
    ) -> None:
        import pygame

        bed_dir = "up" if self.z_positive_moves_bed_up else "down"
        labels = [
            f"Board: {board.value}",
            f"Rotation: {rotation:.2f}°",
            f"Spot XYZ: ({beam_top[0]:.2f}, {beam_top[1]:.2f}, {beam_top[2]:.2f})",
            (
                f"Machine Z: {machine_z:.3f} mm (Δcmd {z_offset_cmd_mm:+.3f})   "
                f"Bed Δ: {bed_offset_mm:+.3f} mm (Z+ bed {bed_dir})"
            ),
            f"Power: {trace.power_pct:.1f}% {'CUT' if trace.is_cut else 'MOVE'}",
            f"Playback: {self.time_scale:.2f}x ([ slows, ] speeds)",
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
