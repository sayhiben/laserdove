from __future__ import annotations

from typing import Sequence

from .model import BoardSide
from .sim_traces import BeamTrace


class ViewerPlaybackMixin:
    """Mixin for playback timing helpers."""

    def _rotation_at(self, play_time: float) -> float:
        """Internal helper to rotation at."""
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
        """Return current trace index."""
        for idx, cumulative in enumerate(self._cumulative):
            if play_time <= cumulative + 1e-9:
                return idx
        return len(self.traces) - 1

    @staticmethod
    def _current_index_for_group(
        play_time: float, traces: Sequence[BeamTrace], cumulative: Sequence[float]
    ) -> int:
        """Return current index for group."""
        if not traces:
            return -1
        for idx, cum_val in enumerate(cumulative):
            if play_time <= cum_val + 1e-9:
                return idx
        return len(traces) - 1

    def _frame_metadata(self, play_time: float, *, file: str) -> dict:
        """Internal helper to frame metadata."""
        idx = self._current_trace_index(play_time)
        trace = self.traces[idx]
        rotation = self._rotation_at(play_time)
        prior = 0.0 if idx == 0 else self._cumulative[idx - 1]
        duration = max(trace.duration, 1e-9)
        progress = max(min((play_time - prior) / duration, 1.0), 0.0) if trace.duration > 0 else 1.0
        machine_z = trace.start_machine_z + (trace.end_machine_z - trace.start_machine_z) * progress
        z_ref = self.z_zero_tail_mm if trace.board == BoardSide.TAIL else self.z_zero_pin_mm
        return {
            "file": file,
            "t": play_time,
            "trace_index": idx,
            "board": trace.board.value,
            "rotation_deg": rotation,
            "machine_z": machine_z,
            "z_offset_mm": machine_z - z_ref,
            "is_cut": trace.is_cut,
            "power_pct": trace.power_pct,
            "duration_s": trace.duration,
            "source": trace.source,
        }
