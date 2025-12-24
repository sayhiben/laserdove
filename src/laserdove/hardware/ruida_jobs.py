from __future__ import annotations

import logging
import math
from typing import List

from .rd_builder import RDMove, build_rd_job
from .ruida_common import (
    clamp_power,
    encode_abscoord_mm,
    encode_abscoord_mm_signed,
    encode_power_pct,
    should_force_speed,
    swizzle,
)

log = logging.getLogger("laserdove.hardware.ruida_laser")


class RuidaJobMixin:
    """Mixin providing RD job and motion helpers."""

    def _set_speed(self, speed_mm_s: float) -> None:
        """
        Issue a SET_SPEED command if the requested speed differs from last send.

        Args:
            speed_mm_s: Speed in mm/sec.
        """
        speed_ums, changed = should_force_speed(self._last_speed_ums, speed_mm_s)
        if not changed:
            return
        self._last_speed_ums = speed_ums
        payload = bytes([0xC9, 0x02]) + encode_abscoord_mm(speed_mm_s)
        log.info("[RUIDA UDP] SET_SPEED %.3f mm/s", speed_mm_s)
        self._udp.send_packets(payload)

    def _set_aux_outputs(
        self, *, air_assist: bool | None = None, blow_on: bool | None = None
    ) -> None:
        """Set aux outputs."""
        if air_assist is not None:
            payload = b"\xca\x01\x13" if air_assist else b"\xca\x01\x12"
            log.info("[RUIDA UDP] AIR_ASSIST %s", "ON" if air_assist else "OFF")
            self._udp.send_packets(payload)
        if blow_on is not None:
            payload = b"\xca\x13" if blow_on else b"\xca\x12"
            log.info("[RUIDA UDP] BLOW %s", "ON" if blow_on else "OFF")
            self._udp.send_packets(payload)

    def _pre_cut_warmup(self, *, origin_x: float, origin_y: float) -> None:
        """Internal helper to pre cut warmup."""
        if self.pre_cut_warmup_s <= 0:
            return
        outputs = []
        if self.air_assist:
            outputs.append("air_assist")
        if self.inline_fan_on:
            outputs.append("inline_fan")
        if not outputs:
            log.info(
                "[RUIDA UDP] Pre-cut warmup configured (%.1fs) but no outputs enabled",
                self.pre_cut_warmup_s,
            )
            return
        log.info(
            "[RUIDA UDP] Pre-cut warmup: outputs=%s duration=%.1fs",
            ", ".join(outputs),
            self.pre_cut_warmup_s,
        )
        self._wait_for_ready()
        self._set_aux_outputs(
            air_assist=True if self.air_assist else None,
            blow_on=True if self.inline_fan_on else None,
        )
        warmup_speed = max(min(self.z_speed_mm_s, 10.0), 1.0)
        warmup_span = 1.0
        leg_time = warmup_span / warmup_speed if warmup_speed > 0 else 0.0
        if leg_time <= 0.0:
            log.info("[RUIDA UDP] Warmup skipped: invalid speed/span")
            return
        legs = max(1, math.ceil(self.pre_cut_warmup_s / leg_time))
        moves: List[RDMove] = [
            RDMove(
                x_mm=origin_x,
                y_mm=origin_y,
                speed_mm_s=warmup_speed,
                power_pct=0.0,
                is_cut=False,
            )
        ]
        for idx in range(legs):
            x_target = origin_x + warmup_span if idx % 2 == 0 else origin_x
            moves.append(
                RDMove(
                    x_mm=x_target,
                    y_mm=origin_y,
                    speed_mm_s=warmup_speed,
                    power_pct=0.0,
                    is_cut=False,
                )
            )
        if not math.isclose(moves[-1].x_mm, origin_x, abs_tol=1e-9):
            moves.append(
                RDMove(
                    x_mm=origin_x,
                    y_mm=origin_y,
                    speed_mm_s=warmup_speed,
                    power_pct=0.0,
                    is_cut=False,
                )
            )
        log.info(
            "[RUIDA UDP] Warmup travel: span=%.1fmm speed=%.2fmm/s legs=%d",
            warmup_span,
            warmup_speed,
            legs,
        )
        if self.dry_run:
            log.info("[RUIDA UDP] Dry-run: skipping warmup job")
            return
        self.send_rd_job(
            moves,
            job_z_mm=None,
            require_busy_transition=True,
            start_z_mm=self.z,
        )

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        """
        Move the head to an absolute XY position and optionally adjust Z.

        Args:
            x: Target X (mm), leaves unchanged if None.
            y: Target Y (mm), leaves unchanged if None.
            z: Target logical Z (mm), emits 0x80 0x03 relative to cached Z.
            speed: Travel speed in mm/sec.
        """
        self._wait_for_ready()
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            delta_z = z - self.z
            if not math.isclose(delta_z, 0.0, abs_tol=1e-6):
                hardware_delta = delta_z if self.z_positive_moves_bed_up else -delta_z
                payload = b"\x80\x03" + encode_abscoord_mm_signed(hardware_delta)
                log.info(
                    "[RUIDA UDP] MOVE_Z via 0x80 0x03: target=%.3f delta=%.3f (hw_delta=%.3f)",
                    z,
                    delta_z,
                    hardware_delta,
                )
                self._udp.send_packets(payload)
                self.z = z
        if self.power != 0.0:
            self.set_laser_power(0.0)
        if speed is not None:
            self._set_speed(speed)
        if x is None and y is None:
            return
        x_mm = self.x if x is None else x
        y_mm = self.y if y is None else y
        payload = bytes([0x88]) + encode_abscoord_mm(x_mm) + encode_abscoord_mm(y_mm)
        log.info("[RUIDA UDP] MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)
        self._udp.send_packets(payload)

    def cut_line(self, x, y, speed) -> None:
        """
        Execute a cutting move to an absolute coordinate.

        Args:
            x: Target X (mm).
            y: Target Y (mm).
            speed: Cutting speed (mm/sec).
        """
        self._wait_for_ready()
        self.x = x
        self.y = y
        if speed is not None:
            self._set_speed(speed)
        payload = bytes([0xA8]) + encode_abscoord_mm(x) + encode_abscoord_mm(y)
        log.info(
            "[RUIDA UDP] CUT_LINE x=%.3f y=%.3f speed=%.3f power=%.1f%%", x, y, speed, self.power
        )
        self._udp.send_packets(payload)

    def set_laser_power(self, power_pct) -> None:
        """
        Set laser output power, honoring movement-only suppression.

        Args:
            power_pct: Requested power percentage.
        """
        self._wait_for_ready()
        requested_power, should_update = clamp_power(power_pct, self.power)

        if self.movement_only:
            log.info(
                "[RUIDA UDP] movement-only: requested laser power %.1f%% (suppressed)",
                requested_power,
            )
            if self._movement_only_power_sent:
                log.debug("[RUIDA UDP] movement-only: suppressing laser power change")
                return
            log.info("[RUIDA UDP] movement-only: sending single laser-off command")
            self.power = 0.0
            self._movement_only_power_sent = True
            payload = bytes([0xC7]) + encode_power_pct(0.0)
            self._udp.send_packets(payload)
            return

        if not should_update:
            return

        self.power = requested_power
        payload = bytes([0xC7]) + encode_power_pct(requested_power)
        log.info("[RUIDA UDP] SET_LASER_POWER %.1f%%", requested_power)
        self._udp.send_packets(payload)

    def send_rd_job(
        self,
        moves: List[RDMove],
        job_z_mm: float | None = None,
        *,
        require_busy_transition: bool = True,
        start_z_mm: float | None = None,
    ) -> None:
        """
        Build a minimal RD job and send it over UDP 50200. Auto-runs on receipt.

        Args:
            moves: Sequence of RDMove objects describing travel/cuts.
            job_z_mm: Optional logical Z offset for the job header.
            require_busy_transition: If True, wait for busy->idle before returning.
            start_z_mm: Logical Z at job start; used to compute relative 0x80 03 offsets.
        """
        if not moves:
            return
        job_has_power = any(mv.is_cut and mv.power_pct > 0.0 for mv in moves)
        require_busy_transition = require_busy_transition and job_has_power
        job_z_offset_mm = None
        if job_z_mm is not None:
            job_z_offset_mm = job_z_mm if self.z_positive_moves_bed_up else -job_z_mm
        start_z = start_z_mm if start_z_mm is not None else self.z
        # Log current status before building/sending.
        pre_state = self._read_machine_state()
        if pre_state:
            log.debug(
                "[RUIDA UDP] Status before RD send: 0x%08X busy=%s low_move=%s low_run=%s part_end=%s",
                pre_state.status_bits,
                bool(pre_state.status_bits & self.BUSY_MASK),
                bool(pre_state.status_bits & 0x10),
                bool(pre_state.status_bits & 0x01),
                bool(pre_state.status_bits & self.STATUS_BIT_PART_END),
            )
        if self.movement_only:
            for mv in moves:
                mv.power_pct = 0.0
        payload = build_rd_job(
            moves,
            job_z_mm=job_z_offset_mm,
            initial_z_mm=start_z,
            air_assist=self.air_assist,
            blow_on=self.inline_fan_on,
        )
        z_moves = [f"#{idx}:{mv.z_mm:+.3f}" for idx, mv in enumerate(moves) if mv.z_mm is not None]
        log.info(
            "[RUIDA UDP] RD Z context: start_z=%s header_z=%s z_moves=%s",
            f"{start_z:.3f}" if start_z is not None else "unset",
            f"{job_z_offset_mm:+.3f}" if job_z_offset_mm is not None else "none",
            ", ".join(z_moves) if z_moves else "none",
        )
        if self.save_rd_dir:
            self.save_rd_dir.mkdir(parents=True, exist_ok=True)
            self._rd_job_counter += 1
            filename = f"job_{self._rd_job_counter:03d}"
            if job_z_offset_mm is not None:
                filename += f"_z{job_z_offset_mm:+.3f}"
            path = self.save_rd_dir / f"{filename}.rd"
            swizzled = swizzle(payload, magic=self.magic)
            path.write_bytes(swizzled)
            log.info("[RUIDA UDP] Saved RD job to %s", path)
        log.info(
            "[RUIDA UDP] Uploading RD job with %d moves%s",
            len(moves),
            f" z={job_z_offset_mm:.3f}" if job_z_offset_mm is not None else "",
        )
        if self.dry_run:
            log.debug("[RUIDA UDP DRY RD] %s", payload.hex(" "))
        self._udp.send_packets(payload)
        # Wait for completion; treat PART_END as done.
        self._wait_for_ready(
            require_busy_transition=require_busy_transition,
            min_stable_s=self.min_stable_s,
        )
        # Update logical Z to reflect the last commanded target.
        final_z = start_z + (job_z_offset_mm or 0.0)
        for mv in moves:
            if mv.z_mm is not None:
                final_z = mv.z_mm
        self.z = final_z
