# hardware/sim.py
from __future__ import annotations

import logging
import math
import time
from typing import Dict, List, Optional

from simulation_viewer import SimulationViewer
from .base import LaserInterface, RotaryInterface

log = logging.getLogger(__name__)


class SimulatedLaser(LaserInterface):
    """
    Collects executed moves/cuts and can render them on a Tkinter canvas.
    Rendering is delegated to SimulationViewer to keep this class focused on state.
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
        self.viewer: Optional[SimulationViewer] = None

    def set_rotation(self, rotation_deg: float) -> None:
        self.rotation_deg = rotation_deg
        self.current_board = "pin"
        if self.viewer is not None:
            self.viewer.update(self.segments, self.rotation_deg)

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
        if self.viewer is not None:
            self.viewer.update(self.segments, self.rotation_deg)

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

    def setup_viewer(self) -> None:
        """Prepare the Tk canvas without blocking main thread."""
        if self.viewer is None:
            self.viewer = SimulationViewer()
        self.viewer.open()
        self.viewer.render(self.segments, self.rotation_deg)
        self.viewer.update(self.segments, self.rotation_deg)

    def show(self) -> None:
        if not self.segments and self.viewer is None:
            log.info("No segments to visualize.")
            return
        self.setup_viewer()
        if self.viewer is None:
            return
        self.viewer.mainloop(self.segments, self.rotation_deg)


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
            if self.visualizer.viewer is not None:
                self.visualizer.viewer.update(self.visualizer.segments, angle_deg)
        if self.real_time and speed_dps > 0 and self.time_scale > 0:
            duration = delta_angle / speed_dps / self.time_scale
            if duration > 0:
                time.sleep(duration)
