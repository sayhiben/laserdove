# simulation_viewer.py
from __future__ import annotations

import logging
import math
from typing import Dict, List, Tuple, Optional

log = logging.getLogger(__name__)


class SimulationViewer:
    def __init__(self, width: int = 960, height: int = 540, padding: int = 20) -> None:
        self.width = width
        self.height = height
        self.padding = padding
        self._palette = ["#e53935", "#1e88e5", "#8e24aa", "#43a047", "#fb8c00", "#3949ab"]
        self._rotation_colors: Dict[float, str] = {}
        self._root = None
        self._canvas = None

    def _reset_default_root(self, tk_mod) -> None:
        try:
            if getattr(tk_mod, "_default_root", None) is not None:
                tk_mod._default_root.destroy()  # type: ignore[attr-defined]
                tk_mod._default_root = None  # type: ignore[attr-defined]
        except Exception:
            pass

    def open(self) -> None:
        try:
            import tkinter as tk
        except Exception as exc:  # pragma: no cover - UI import guard
            log.error("Tkinter unavailable for simulation: %s", exc)
            return

        if self._root is not None and self._canvas is not None:
            return

        self._reset_default_root(tk)
        self._root = tk.Tk()
        self._root.title("Nova dovetail simulation")
        self._canvas = tk.Canvas(self._root, width=self.width, height=self.height, bg="white")
        self._canvas.pack(fill="both", expand=True)

        def lift_window() -> None:
            try:
                self._root.update_idletasks()
                self._root.deiconify()
                self._root.lift()
                self._root.focus_force()
                self._root.attributes("-topmost", True)
                self._root.after(200, lambda: self._root.attributes("-topmost", False))
            except Exception as exc:  # pragma: no cover - UI best-effort
                log.debug("Could not lift simulation window: %s", exc)

        def on_close() -> None:
            try:
                self._root.quit()
                self._root.destroy()
            except Exception:
                pass
            self._root = None
            self._canvas = None

        self._root.after(50, lift_window)
        self._root.protocol("WM_DELETE_WINDOW", on_close)

    def close(self) -> None:
        try:
            if self._root is not None:
                self._root.destroy()
        except Exception:
            pass
        self._root = None
        self._canvas = None

    def _extents(self, segments: List[Dict[str, float | bool]]) -> Optional[Tuple[float, float, float, float]]:
        if not segments:
            return None
        min_x = min(min(seg["x0"], seg["x1"]) for seg in segments)
        max_x = max(max(seg["x0"], seg["x1"]) for seg in segments)
        min_y = min(min(seg["y0"], seg["y1"]) for seg in segments)
        max_y = max(max(seg["y0"], seg["y1"]) for seg in segments)
        return min_x, max_x, min_y, max_y

    def _scale_candidate(self, extents: Optional[Tuple[float, float, float, float]], viewport: Tuple[float, float, float, float]) -> Optional[float]:
        if extents is None:
            return None
        min_x, max_x, min_y, max_y = extents
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        vx0, vy0, vx1, vy1 = viewport
        vw = max(vx1 - vx0 - 2 * self.padding, 1.0)
        vh = max(vy1 - vy0 - 2 * self.padding, 1.0)
        return min(vw / span_x, vh / span_y)

    def _to_canvas(
        self,
        x_val: float,
        y_val: float,
        scale: float,
        extents: Tuple[float, float, float, float],
        viewport: Tuple[float, float, float, float],
    ) -> tuple[float, float]:
        min_x, max_x, min_y, max_y = extents
        vx0, vy0, vx1, vy1 = viewport
        avail_w = vx1 - vx0 - 2 * self.padding
        avail_h = vy1 - vy0 - 2 * self.padding
        used_w = (max_x - min_x) * scale
        used_h = (max_y - min_y) * scale
        extra_x = max((avail_w - used_w) / 2.0, 0.0)
        extra_y = max((avail_h - used_h) / 2.0, 0.0)
        cx = vx0 + self.padding + extra_x + (x_val - min_x) * scale
        cy = vy1 - self.padding - extra_y - (y_val - min_y) * scale
        return cx, cy

    def _color_for_z(self, z_val: float, z_min: float, z_max: float) -> str:
        if z_max - z_min < 1e-6:
            return "#1e88e5"
        t = (z_val - z_min) / (z_max - z_min)
        r = int(30 + 200 * t)
        g = int(80 * (1 - t))
        b = int(160 + 50 * (1 - t))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_z_gauge(
        self,
        pin_segments: List[Dict[str, float | bool]],
        viewport: Tuple[float, float, float, float],
        top_offset_px: Optional[float] = None,
    ) -> None:
        if self._canvas is None:
            return
        z_values = [seg["z"] for seg in pin_segments if seg["is_cut"]]
        if not z_values:
            return

        z_min_actual, z_max_actual = min(z_values), max(z_values)
        span = max(abs(z_min_actual), abs(z_max_actual), 1e-6)
        z_min, z_max = -span, span  # center gauge at Z=0

        _, vy0, vx1, vy1 = viewport
        gauge_height = 140
        gauge_width = 16
        # Center gauge under rotary indicator if provided; default to right edge.
        gx1 = vx1 - self.padding
        gx0 = gx1 - gauge_width
        gy0 = vy0 + (top_offset_px if top_offset_px is not None else self.padding)
        gy1 = gy0 + gauge_height
        if gy1 > vy1 - self.padding:
            gy1 = vy1 - self.padding
            gy0 = gy1 - gauge_height

        self._canvas.create_rectangle(gx0, gy0, gx1, gy1, outline="#90a4ae", fill="#eceff1")

        # Draw gradient steps
        steps = 12
        for i in range(steps):
            t0 = i / steps
            t1 = (i + 1) / steps
            y0 = gy1 - gauge_height * t0
            y1 = gy1 - gauge_height * t1
            z_sample = z_min + (z_max - z_min) * (t0 + t1) / 2
            color = self._color_for_z(z_sample, z_min, z_max)
            self._canvas.create_rectangle(gx0, y1, gx1, y0, outline=color, fill=color)

        latest_z = pin_segments[-1]["z"]
        ratio = (latest_z - z_min) / (z_max - z_min)
        indicator_y = gy1 - gauge_height * ratio
        self._canvas.create_line(gx0 - 4, indicator_y, gx1 + 4, indicator_y, fill="#e53935", width=2)

        text_x = gx0 - 6
        text_x_right = gx1 + 6
        self._canvas.create_text(text_x, gy0, anchor="e", text=f"Z max {z_max_actual:.2f}", font=("Arial", 9))
        self._canvas.create_text(text_x, gy1, anchor="e", text=f"Z min {z_min_actual:.2f}", font=("Arial", 9))
        self._canvas.create_text(text_x_right, indicator_y, anchor="w", text=f"Z now {latest_z:.2f}", font=("Arial", 9))

    def _draw_segments(
        self,
        segments: List[Dict[str, float | bool]],
        viewport: Tuple[float, float, float, float],
        use_z_color: bool,
        common_scale: float,
        extents: Optional[Tuple[float, float, float, float]],
    ) -> None:
        if self._canvas is None or not segments or extents is None:
            return
        z_values = [seg["z"] for seg in segments if seg["is_cut"]]
        z_min, z_max = (min(z_values), max(z_values)) if z_values else (0.0, 0.0)

        for seg in segments:
            x0, y0 = self._to_canvas(seg["x0"], seg["y0"], common_scale, extents, viewport)
            x1, y1 = self._to_canvas(seg["x1"], seg["y1"], common_scale, extents, viewport)
            if seg["is_cut"]:
                color = self._color_for_z(seg["z"], z_min, z_max) if use_z_color else "#1e88e5"
                width_px = 2
            else:
                color = "#90a4ae"
                width_px = 1
            self._canvas.create_line(x0, y0, x1, y1, fill=color, width=width_px)

    def _draw_rotary_indicator(self, viewport: Tuple[float, float, float, float], rotation_deg: float) -> None:
        if self._canvas is None:
            return
        vx0, vy0, vx1, vy1 = viewport
        cx = vx0 + (vx1 - vx0) * 0.85
        cy = vy0 + (vy1 - vy0) * 0.15
        radius = min(vx1 - vx0, vy1 - vy0) * 0.1
        self._canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="#546e7a")
        angle_rad = math.radians(rotation_deg)
        dx = radius * math.cos(angle_rad)
        dy = radius * math.sin(angle_rad)
        self._canvas.create_line(cx - dx, cy - dy, cx + dx, cy + dy, fill="#e53935", width=2)
        self._canvas.create_text(cx, cy + radius + 12, text=f"θ={rotation_deg:.1f}°", font=("Arial", 9))

    def _draw_legends(self) -> None:
        if self._canvas is None:
            return
        legend_y = self.padding / 2
        for rotation, color in self._rotation_colors.items():
            self._canvas.create_rectangle(self.padding, legend_y, self.padding + 20, legend_y + 10, fill=color, outline=color)
            self._canvas.create_text(self.padding + 30, legend_y + 5, anchor="w", text=f"θ={rotation:.1f}°", font=("Arial", 9))
            legend_y += 18

    def render(self, segments: List[Dict[str, float | bool]], rotation_deg: float) -> None:
        if self._root is None or self._canvas is None:
            return

        mid_x = self.width / 2
        tail_viewport = (self.padding, self.padding, mid_x - self.padding, self.height - self.padding)
        pin_viewport = (mid_x + self.padding, self.padding, self.width - self.padding, self.height - self.padding)

        tail_segments = [seg for seg in segments if seg.get("board") == "tail"]
        pin_segments = [seg for seg in segments if seg.get("board") == "pin"]
        tail_extents = self._extents(tail_segments)
        pin_extents = self._extents(pin_segments)

        scale_candidates = [
            self._scale_candidate(tail_extents, tail_viewport),
            self._scale_candidate(pin_extents, pin_viewport),
        ]
        scale_candidates = [s for s in scale_candidates if s is not None]
        common_scale = min(scale_candidates) if scale_candidates else 1.0

        self._canvas.delete("all")

        self._canvas.create_rectangle(*tail_viewport, outline="#cfd8dc")
        self._canvas.create_text(
            (tail_viewport[0] + tail_viewport[2]) / 2,
            tail_viewport[1] - 6,
            text="Tail board",
            font=("Arial", 10),
        )

        self._canvas.create_rectangle(*pin_viewport, outline="#cfd8dc")
        self._canvas.create_text(
            (pin_viewport[0] + pin_viewport[2]) / 2,
            pin_viewport[1] - 6,
            text="Pin board (Z-colored)",
            font=("Arial", 10),
        )

        self._draw_segments(tail_segments, tail_viewport, use_z_color=False, common_scale=common_scale, extents=tail_extents)
        self._draw_segments(pin_segments, pin_viewport, use_z_color=True, common_scale=common_scale, extents=pin_extents)
        rot_cx = pin_viewport[0] + (pin_viewport[2] - pin_viewport[0]) * 0.8
        rot_cy = pin_viewport[1] + (pin_viewport[3] - pin_viewport[1]) * 0.15
        rot_radius = min(pin_viewport[2] - pin_viewport[0], pin_viewport[3] - pin_viewport[1]) * 0.1

        self._draw_rotary_indicator(pin_viewport, rotation_deg)
        # Position Z gauge below the rotary indicator and its label, centered under the rotary column.
        gauge_top = (rot_cy + rot_radius + 24) - pin_viewport[1]
        # Shift gauge left toward the rotary column.
        gauge_viewport = (
            pin_viewport[0],
            pin_viewport[1],
            pin_viewport[2] - 40,  # leave right margin for min/max labels
            pin_viewport[3],
        )
        self._draw_z_gauge(pin_segments, gauge_viewport, top_offset_px=gauge_top)
        self._draw_legends()

    def update(self, segments: List[Dict[str, float | bool]], rotation_deg: float) -> None:
        if self._root is None:
            return
        self.render(segments, rotation_deg)
        try:
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            pass

    def mainloop(self, segments: List[Dict[str, float | bool]], rotation_deg: float) -> None:
        if self._root is None:
            return
        try:
            self.render(segments, rotation_deg)
            self._root.mainloop()
        finally:
            self.close()
