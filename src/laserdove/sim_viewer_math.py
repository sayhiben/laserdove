from __future__ import annotations

import math
from typing import List, Sequence

from .model import BoardSide
from .sim_kinematics import board_to_world_local, invert_projected_y
from .sim_traces import BeamTrace


class ViewerMathMixin:
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

    def _edge_cut_polygons(
        self, traces: Sequence[BeamTrace]
    ) -> List[tuple[BoardSide, float, List[tuple[float, float]]]]:
        """
        Group visible cut traces into per-shape polygons in board-local (Y,Z).
        """
        polys: List[tuple[BoardSide, float, List[tuple[float, float]]]] = []
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
