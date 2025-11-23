# hardware.py
from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from typing import Iterable, Callable, Dict, List, Optional, Tuple

from model import Command, CommandType

log = logging.getLogger(__name__)


class LaserInterface(ABC):
    @abstractmethod
    def move(self, x=None, y=None, z=None, speed=None) -> None:
        ...

    @abstractmethod
    def cut_line(self, x, y, speed) -> None:
        ...

    @abstractmethod
    def set_laser_power(self, power_pct) -> None:
        ...


class RotaryInterface(ABC):
    @abstractmethod
    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        ...


class DummyLaser(LaserInterface):
    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        log.info("MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)

    def cut_line(self, x, y, speed) -> None:
        self.x = x
        self.y = y
        log.info("CUT_LINE x=%.3f y=%.3f speed=%.3f", x, y, speed)

    def set_laser_power(self, power_pct) -> None:
        self.power = power_pct
        log.info("SET_LASER_POWER %.1f%%", power_pct)


class DummyRotary(RotaryInterface):
    def __init__(self) -> None:
        self.angle = 0.0

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        log.info("ROTATE to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        self.angle = angle_deg
        time.sleep(0.0)


class SimulatedLaser(LaserInterface):
    """
    Collects executed moves/cuts and can render them on a Tkinter canvas.
    """

    def __init__(self, real_time: bool = False, time_scale: float = 1.0) -> None:
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power_pct = 0.0
        self.rotation_deg = 0.0
        self.current_board = "tail"
        self.segments: List[Dict[str, float | bool]] = []
        self.real_time = real_time
        self.time_scale = time_scale
        # Tk viewer state
        self._root: Optional["tkinter.Tk"] = None  # type: ignore[name-defined]
        self._canvas: Optional["tkinter.Canvas"] = None  # type: ignore[name-defined]
        self._rotation_colors: Dict[float, str] = {}
        self._palette = ["#e53935", "#1e88e5", "#8e24aa", "#43a047", "#fb8c00", "#3949ab"]
        self._width, self._height, self._padding = 960, 540, 20

    def set_rotation(self, rotation_deg: float) -> None:
        self.rotation_deg = rotation_deg
        self.current_board = "pin"

    def _record_segment(self, new_x: float, new_y: float, is_cut: bool) -> None:
        if new_x == self.x and new_y == self.y:
            return
        self.segments.append(
            {
                "x0": self.x,
                "y0": self.y,
                "x1": new_x,
                "y1": new_y,
                "is_cut": is_cut,
                "rotation_deg": self.rotation_deg,
                "z": self.z,
                "board": self.current_board,
            }
        )
        if self._canvas is not None:
            self._redraw_all()
            try:
                self._root.update_idletasks()  # type: ignore[union-attr]
                self._root.update()  # type: ignore[union-attr]
            except Exception:
                pass

    def _sleep_for_motion(self, distance_mm: float, speed: float | None) -> None:
        if not self.real_time:
            return
        if speed is None or speed <= 0 or self.time_scale <= 0:
            return
        duration = distance_mm / speed / self.time_scale
        if duration > 0:
            time.sleep(duration)

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        new_x = self.x if x is None else x
        new_y = self.y if y is None else y
        if z is not None:
            self.z = z
        if new_x != self.x or new_y != self.y:
            self._record_segment(new_x, new_y, is_cut=False)
            distance = math.hypot(new_x - self.x, new_y - self.y)
            self._sleep_for_motion(distance, speed)
        self.x = new_x
        self.y = new_y
        log.info("MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)

    def cut_line(self, x, y, speed) -> None:
        is_cut = self.power_pct > 0.0
        distance = math.hypot(x - self.x, y - self.y)
        self._record_segment(x, y, is_cut=is_cut)
        self._sleep_for_motion(distance, speed)
        self.x = x
        self.y = y
        log.info("CUT_LINE x=%.3f y=%.3f speed=%.3f", x, y, speed)

    def set_laser_power(self, power_pct) -> None:
        self.power_pct = power_pct
        log.info("SET_LASER_POWER %.1f%%", power_pct)

    def _reset_default_root(self, tk_mod) -> None:
        try:
            if getattr(tk_mod, "_default_root", None) is not None:
                tk_mod._default_root.destroy()  # type: ignore[attr-defined]
                tk_mod._default_root = None  # type: ignore[attr-defined]
        except Exception:
            pass

    def setup_viewer(self) -> None:
        """Prepare the Tk canvas without blocking main thread."""
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
        self._canvas = tk.Canvas(self._root, width=self._width, height=self._height, bg="white")
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
        self._redraw_all()
        try:
            self._root.update_idletasks()
            self._root.update()
        except Exception:
            pass

    def _compute_transform(
        self,
        segments: List[Dict[str, float | bool]],
        viewport: Tuple[float, float, float, float],
    ) -> tuple[float, float, float]:
        if not segments:
            return 1.0, 0.0, 0.0
        min_x = min(min(seg["x0"], seg["x1"]) for seg in segments)
        max_x = max(max(seg["x0"], seg["x1"]) for seg in segments)
        min_y = min(min(seg["y0"], seg["y1"]) for seg in segments)
        max_y = max(max(seg["y0"], seg["y1"]) for seg in segments)
        span_x = max(max_x - min_x, 1e-6)
        span_y = max(max_y - min_y, 1e-6)
        vx0, vy0, vx1, vy1 = viewport
        vw = max(vx1 - vx0, 1.0)
        vh = max(vy1 - vy0, 1.0)
        scale = min(
            (vw - 2 * self._padding) / span_x,
            (vh - 2 * self._padding) / span_y,
        )
        return scale, min_x, min_y

    def _to_canvas(
        self,
        x_val: float,
        y_val: float,
        scale: float,
        min_x: float,
        min_y: float,
        viewport: Tuple[float, float, float, float],
    ) -> tuple[float, float]:
        vx0, vy0, _, vy1 = viewport
        cx = vx0 + self._padding + (x_val - min_x) * scale
        cy = vy1 - self._padding - (y_val - min_y) * scale
        return cx, cy

    def _color_for_rotation(self, rotation: float) -> str:
        if rotation not in self._rotation_colors:
            self._rotation_colors[rotation] = self._palette[len(self._rotation_colors) % len(self._palette)]
        return self._rotation_colors[rotation]

    def _color_for_z(self, z_val: float, z_min: float, z_max: float) -> str:
        if z_max - z_min < 1e-6:
            return "#1e88e5"
        t = (z_val - z_min) / (z_max - z_min)
        # Blue -> Magenta -> Red gradient
        r = int(30 + 200 * t)
        g = int(80 * (1 - t))
        b = int(160 + 50 * (1 - t))
        return f"#{r:02x}{g:02x}{b:02x}"

    def _draw_segments(
        self,
        segments: List[Dict[str, float | bool]],
        viewport: Tuple[float, float, float, float],
        use_z_color: bool,
    ) -> None:
        if self._canvas is None or not segments:
            return
        scale, min_x, min_y = self._compute_transform(segments, viewport)
        z_values = [seg["z"] for seg in segments if seg["is_cut"]]
        z_min, z_max = (min(z_values), max(z_values)) if z_values else (0.0, 0.0)

        for seg in segments:
            x0, y0 = self._to_canvas(seg["x0"], seg["y0"], scale, min_x, min_y, viewport)
            x1, y1 = self._to_canvas(seg["x1"], seg["y1"], scale, min_x, min_y, viewport)
            if seg["is_cut"]:
                color = self._color_for_z(seg["z"], z_min, z_max) if use_z_color else "#1e88e5"
                width_px = 2
            else:
                color = "#90a4ae"
                width_px = 1
            self._canvas.create_line(x0, y0, x1, y1, fill=color, width=width_px)

    def _draw_rotary_indicator(self, viewport: Tuple[float, float, float, float]) -> None:
        if self._canvas is None:
            return
        vx0, vy0, vx1, vy1 = viewport
        cx = vx0 + (vx1 - vx0) * 0.85
        cy = vy0 + (vy1 - vy0) * 0.15
        radius = min(vx1 - vx0, vy1 - vy0) * 0.1
        self._canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, outline="#546e7a")
        angle_rad = math.radians(self.rotation_deg)
        dx = radius * math.cos(angle_rad)
        dy = radius * math.sin(angle_rad)
        self._canvas.create_line(cx - dx, cy - dy, cx + dx, cy + dy, fill="#e53935", width=2)
        self._canvas.create_text(cx, cy + radius + 12, text=f"θ={self.rotation_deg:.1f}°", font=("Arial", 9))

    def _redraw_all(self) -> None:
        if self._canvas is None:
            return
        self._canvas.delete("all")

        mid_x = self._width / 2
        tail_viewport = (self._padding, self._padding, mid_x - self._padding, self._height - self._padding)
        pin_viewport = (mid_x + self._padding, self._padding, self._width - self._padding, self._height - self._padding)

        tail_segments = [seg for seg in self.segments if seg.get("board") == "tail"]
        pin_segments = [seg for seg in self.segments if seg.get("board") == "pin"]

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
            text="Pin board (rotation colored by Z)",
            font=("Arial", 10),
        )

        self._draw_segments(tail_segments, tail_viewport, use_z_color=False)
        self._draw_segments(pin_segments, pin_viewport, use_z_color=True)
        self._draw_rotary_indicator(pin_viewport)

        legend_y = self._padding / 2
        for rotation, color in self._rotation_colors.items():
            self._canvas.create_rectangle(self._padding, legend_y, self._padding + 20, legend_y + 10, fill=color, outline=color)
            self._canvas.create_text(self._padding + 30, legend_y + 5, anchor="w", text=f"θ={rotation:.1f}°", font=("Arial", 9))
            legend_y += 18

    def show(self) -> None:
        if not self.segments and self._root is None:
            log.info("No segments to visualize.")
            return
        self.setup_viewer()
        if self._root is None:
            return
        try:
            self._root.mainloop()
        finally:
            try:
                self._root.destroy()
            except Exception:
                pass
            self._root = None
            self._canvas = None


class SimulatedRotary(RotaryInterface):
    def __init__(
        self,
        visualizer: SimulatedLaser | None = None,
        real_time: bool = False,
        time_scale: float = 1.0,
    ) -> None:
        self.angle = 0.0
        self.visualizer = visualizer
        self.real_time = real_time
        self.time_scale = time_scale

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        log.info("[SIM ROTARY] rotate_to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        delta_angle = abs(angle_deg - self.angle)
        self.angle = angle_deg
        if self.visualizer is not None:
            self.visualizer.set_rotation(angle_deg)
        if self.real_time and speed_dps > 0 and self.time_scale > 0:
            duration = delta_angle / speed_dps / self.time_scale
            if duration > 0:
                time.sleep(duration)


class RuidaLaser(LaserInterface):
    """
    Skeleton implementation intended to wrap your existing Ruida tooling
    (RuidaProxy, udpsendruida, ruida.py).

    v1: Only logs; replace internals with UDP / RD-file logic when ready.
    """

    def __init__(self, host: str, port: int = 50200) -> None:
        self.host = host
        self.port = port
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0
        log.info("RuidaLaser initialized for host=%s port=%d", host, port)
        # TODO: initialize UDP socket or RuidaProxy connection here.

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        log.info("[RUDA] MOVE x=%.3f y=%.3f z=%.3f speed=%s",
                 self.x, self.y, self.z, speed)
        # TODO: send rapid move / path segment to Ruida.

    def cut_line(self, x, y, speed) -> None:
        self.x = x
        self.y = y
        log.info("[RUDA] CUT_LINE x=%.3f y=%.3f speed=%.3f power=%.1f%%",
                 x, y, speed, self.power)
        # TODO: send cut vector to Ruida at current power.

    def set_laser_power(self, power_pct) -> None:
        self.power = power_pct
        log.info("[RUDA] SET_LASER_POWER %.1f%%", power_pct)
        # TODO: encode power into RD job or runtime power override if supported.


class RealRotary(RotaryInterface):
    """
    Skeleton implementation for the physical rotary on the Pi.

    v1: Logs requested angles. Replace method body with calls into
    your stepper driver / GPIO code.
    """

    def __init__(self, steps_per_rev: float | None = None, microsteps: int | None = None) -> None:
        self.angle = 0.0
        self.steps_per_rev = steps_per_rev
        self.microsteps = microsteps
        log.info("RealRotary initialized (steps_per_rev=%s microsteps=%s)",
                 steps_per_rev, microsteps)
        # TODO: initialize GPIO / driver here.

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        log.info("[ROTARY] rotate_to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        self.angle = angle_deg
        # TODO: convert angle to steps and drive motor.
        time.sleep(0.0)


def execute_commands(
    commands: Iterable[Command],
    laser: LaserInterface,
    rotary: RotaryInterface,
) -> None:
    """
    Interpret Command objects and call the appropriate laser/rotary methods.

    Dispatch is table-driven for clarity.
    """

    def handle_move(command: Command) -> None:
        laser.move(x=command.x, y=command.y, z=command.z, speed=command.speed_mm_s)

    def handle_cut_line(command: Command) -> None:
        if command.speed_mm_s is None:
            raise ValueError("CUT_LINE without speed_mm_s")
        laser.cut_line(x=command.x, y=command.y, speed=command.speed_mm_s)

    def handle_set_laser_power(command: Command) -> None:
        if command.power_pct is None:
            raise ValueError("SET_LASER_POWER without power_pct")
        laser.set_laser_power(command.power_pct)

    def handle_rotate(command: Command) -> None:
        if command.angle_deg is None:
            raise ValueError("ROTATE without angle_deg")
        rotary.rotate_to(command.angle_deg, command.speed_mm_s or 0.0)

    def handle_dwell(command: Command) -> None:
        if command.dwell_ms is None:
            return
        time.sleep(command.dwell_ms / 1000.0)

    dispatch: Dict[CommandType, Callable[[Command], None]] = {
        CommandType.MOVE: handle_move,
        CommandType.CUT_LINE: handle_cut_line,
        CommandType.SET_LASER_POWER: handle_set_laser_power,
        CommandType.ROTATE: handle_rotate,
        CommandType.DWELL: handle_dwell,
    }

    for command in commands:
        if command.comment:
            log.debug("# %s", command.comment)

        handler = dispatch.get(command.type)
        if handler is None:
            raise ValueError(f"Unsupported command type {command.type}")
        handler(command)
