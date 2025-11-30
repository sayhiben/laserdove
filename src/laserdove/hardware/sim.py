# hardware/sim.py
from __future__ import annotations

import logging
import math
import time
from typing import Dict, List, Optional

from ..simulation_viewer import SimulationViewer
from .base import LaserInterface, RotaryInterface

log = logging.getLogger(__name__)


class SimulatedLaser(LaserInterface):
    """
    Collects executed moves/cuts and can render them on a Tkinter canvas.
    Rendering is delegated to SimulationViewer to keep this class focused on state.
    """

    def __init__(
        self,
        real_time: bool = False,
        time_scale: float = 1.0,
        *,
        origin_x: float = 0.0,
        origin_y: float = 0.0,
        edge_length_mm: float | None = None,
        movement_only: bool = False,
        z_positive_moves_bed_up: bool = True,
        air_assist: bool = True,
    ) -> None:
        """
        Initialize the simulated laser backend.

        Args:
            real_time: If True, sleep to approximate motion durations.
            time_scale: Scale factor applied to real-time sleeps.
            origin_x: Machine-space origin X (mm).
            origin_y: Machine-space origin Y (mm).
            edge_length_mm: Board edge length for centering Y coordinates.
            movement_only: Force power to 0 when recording cuts.
            z_positive_moves_bed_up: Sign convention for Z (for metadata).
            air_assist: Whether air assist is considered on for segments.
        """
        # Absolute machine-space coordinates (after mapping from board coords).
        self.x = origin_x
        self.y = origin_y
        self.z = 0.0
        self.power_pct = 0.0
        self.origin_x = origin_x
        self.origin_y = origin_y
        self.y_center = (edge_length_mm / 2.0) if edge_length_mm is not None else 0.0
        self.rotation_deg = 0.0
        self.current_board = "tail"
        self.segments: List[Dict[str, float | bool]] = []
        self.real_time = real_time
        self.time_scale = time_scale
        self.movement_only = movement_only
        self.z_positive_moves_bed_up = z_positive_moves_bed_up
        self.air_assist = air_assist
        self.viewer: Optional[SimulationViewer] = None

    def set_rotation(self, rotation_deg: float) -> None:
        """
        Update the simulated rotary angle and refresh the viewer.

        Args:
            rotation_deg: New absolute angle (degrees).
        """
        self.rotation_deg = rotation_deg
        self.current_board = "pin"
        if self.viewer is not None:
            try:
                self.viewer.update(self.segments, self.rotation_deg, origin=(self.origin_x, self.origin_y), y_center=self.origin_y)
            except TypeError:
                self.viewer.update(self.segments, self.rotation_deg)

    def _map_coords(self, x: float, y: float) -> tuple[float, float]:
        """
        Map board-frame coordinates to machine-frame coordinates.

        Args:
            x: Board-frame X (mm).
            y: Board-frame Y (mm), 0 at mid-edge.

        Returns:
            Tuple of (machine X, machine Y).
        """
        return self.origin_x + x, self.origin_y + (y - self.y_center)

    def _record_segment(self, new_x: float, new_y: float, is_cut: bool) -> None:
        """
        Append a segment to the simulated path and refresh the viewer.

        Args:
            new_x: Target machine X (mm).
            new_y: Target machine Y (mm).
            is_cut: True if this segment represents cutting motion.
        """
        if new_x == self.x and new_y == self.y:
            return
        logical_z = self.z
        self.segments.append(
            {
                "x0": self.x,
                "y0": self.y,
                "x1": new_x,
                "y1": new_y,
                "is_cut": is_cut,
                "rotation_deg": self.rotation_deg,
                "z": logical_z,
                "logical_z": logical_z,
                "board": self.current_board,
                "air_assist": self.air_assist,
            }
        )
        if self.viewer is not None:
            try:
                self.viewer.update(self.segments, self.rotation_deg, origin=(self.origin_x, self.origin_y), y_center=self.origin_y)
            except TypeError:
                self.viewer.update(self.segments, self.rotation_deg)

    def _sleep_for_motion(self, distance_mm: float, speed: float | None) -> None:
        """
        Sleep to simulate motion time when real_time is enabled.

        Args:
            distance_mm: Distance traveled (mm).
            speed: Motion speed (mm/s).
        """
        if not self.real_time:
            return
        if speed is None or speed <= 0 or self.time_scale <= 0:
            return
        duration = distance_mm / speed / self.time_scale
        if duration > 0:
            time.sleep(duration)

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        """
        Simulate a travel move in board coordinates.

        Args:
            x: Target board X (mm), None to keep current.
            y: Target board Y (mm), None to keep current.
            z: Optional logical Z.
            speed: Travel speed (mm/s).
        """
        new_x = self.x if x is None else self.origin_x + x
        new_y = self.y if y is None else self.origin_y + (y - self.y_center)
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
        """
        Simulate a cutting move in board coordinates.

        Args:
            x: Target board X (mm).
            y: Target board Y (mm).
            speed: Cutting speed (mm/s).
        """
        target_x, target_y = self._map_coords(x, y)
        is_cut = (not self.movement_only) and self.power_pct > 0.0
        distance = math.hypot(target_x - self.x, target_y - self.y)
        self._record_segment(target_x, target_y, is_cut=is_cut)
        self._sleep_for_motion(distance, speed)
        self.x = target_x
        self.y = target_y
        log.info("CUT_LINE x=%.3f y=%.3f speed=%.3f", x, y, speed)

    def set_laser_power(self, power_pct) -> None:
        """
        Cache a power value for simulated cuts.

        Args:
            power_pct: Requested power percentage.
        """
        self.power_pct = 0.0 if self.movement_only else power_pct
        log.info("SET_LASER_POWER %.1f%% (movement_only=%s)", power_pct, self.movement_only)

    def setup_viewer(self) -> None:
        """Prepare the Tk canvas without blocking main thread."""
        if self.viewer is None:
            self.viewer = SimulationViewer()
        self.viewer.open()
        try:
            self.viewer.render(self.segments, self.rotation_deg, origin=(self.origin_x, self.origin_y), y_center=self.origin_y)
            self.viewer.update(self.segments, self.rotation_deg, origin=(self.origin_x, self.origin_y), y_center=self.origin_y)
        except TypeError:
            # Fallback for legacy dummy viewers in tests
            self.viewer.render(self.segments, self.rotation_deg)
            self.viewer.update(self.segments, self.rotation_deg)

    def show(self) -> None:
        """Open the viewer and render the accumulated segments."""
        if not self.segments and self.viewer is None:
            log.info("No segments to visualize.")
            return
        self.setup_viewer()
        if self.viewer is None:
            return
        try:
            self.viewer.mainloop(self.segments, self.rotation_deg, origin=(self.origin_x, self.origin_y), y_center=self.origin_y)
        except TypeError:
            self.viewer.mainloop(self.segments, self.rotation_deg)


class SimulatedRotary(RotaryInterface):
    """Simulated rotary axis that optionally drives a viewer."""

    def __init__(
        self,
        visualizer: SimulatedLaser | None = None,
        real_time: bool = False,
        time_scale: float = 1.0,
    ) -> None:
        """
        Initialize the simulated rotary axis.

        Args:
            visualizer: Optional SimulatedLaser to sync rotation with.
            real_time: If True, sleep to approximate rotation duration.
            time_scale: Scale factor applied to real-time sleeps.
        """
        self.angle = 0.0
        self.visualizer = visualizer
        self.real_time = real_time
        self.time_scale = time_scale

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        """
        Rotate to an absolute angle and update the viewer.

        Args:
            angle_deg: Target angle (degrees).
            speed_dps: Rotation speed (deg/sec).
        """
        log.info("[SIM ROTARY] rotate_to θ=%.3f° at %.1f dps", angle_deg, speed_dps)
        delta_angle = abs(angle_deg - self.angle)
        self.angle = angle_deg
        if self.visualizer is not None:
            self.visualizer.set_rotation(angle_deg)
            if self.visualizer.viewer is not None:
                try:
                    self.visualizer.viewer.update(self.visualizer.segments, angle_deg, origin=(self.visualizer.origin_x, self.visualizer.origin_y), y_center=self.visualizer.origin_y)
                except TypeError:
                    self.visualizer.viewer.update(self.visualizer.segments, angle_deg)
        if self.real_time and speed_dps > 0 and self.time_scale > 0:
            duration = delta_angle / speed_dps / self.time_scale
            if duration > 0:
                time.sleep(duration)
