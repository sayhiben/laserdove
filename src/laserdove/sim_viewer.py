from __future__ import annotations

import json
import logging
import math
import os
from pathlib import Path
from typing import Iterable, List, Sequence

from .model import BoardSide, Command
from .sim_traces import BeamTrace, build_beam_traces, build_beam_traces_from_rd_files
from .sim_viewer_layout import ViewerLayoutMixin
from .sim_viewer_math import ViewerMathMixin
from .sim_viewer_playback import ViewerPlaybackMixin
from .sim_viewer_render import ViewerRenderMixin

log = logging.getLogger(__name__)


class PygameSimulationViewer(
    ViewerLayoutMixin, ViewerMathMixin, ViewerPlaybackMixin, ViewerRenderMixin
):
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
        travel_time_scale: float = 3.0,
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
        self.travel_time_scale = max(travel_time_scale, 0.1)
        for trace in self.traces:
            if not trace.is_cut:
                trace.duration *= self.travel_time_scale
        self.total_duration = sum(max(t.duration, 0.0) for t in self.traces)
        self._cumulative = self._build_cumulative(self.traces)

        self.tail_traces = [t for t in self.traces if t.board == BoardSide.TAIL]
        self.pin_traces = [t for t in self.traces if t.board == BoardSide.PIN]

        self.width = 1500
        self.height = 820
        self.padding = 18
        self.hud_line_count = 7
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
        min_scale = 0.1
        max_scale = 20.0
        scale_step = 1.25
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
                    if event.key == pygame.K_LEFTBRACKET:
                        self.time_scale = max(self.time_scale / scale_step, min_scale)
                    if event.key == pygame.K_RIGHTBRACKET:
                        self.time_scale = min(self.time_scale * scale_step, max_scale)

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
            movement_only=run_config.backend.movement_only or run_config.reset_only,
            air_assist=run_config.machine_params.air_assist,
            start_board=BoardSide.TAIL,
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
