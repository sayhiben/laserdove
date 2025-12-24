from __future__ import annotations

import logging
import math
from typing import Iterable, List

from .rd_builder import RDMove
from ..command_compiler import LaserBlock, RotationStep, compile_command_plan

log = logging.getLogger("laserdove.hardware.ruida_laser")


class RuidaSequenceMixin:
    def run_sequence_with_rotary(
        self,
        commands: Iterable,
        rotary,
        *,
        movement_only: bool | None = None,
        edge_length_mm: float | None = None,
    ) -> None:
        """
        Partition commands at ROTATE boundaries; send each laser block as an RD job;
        run rotary moves via provided rotary interface in between.

        Args:
            commands: Iterable of high-level commands (MOVE/CUT/SET_LASER_POWER/ROTATE).
            rotary: Rotary backend implementing rotate_to.
            movement_only: Force power=0 regardless of command (overrides instance flag).
            edge_length_mm: Board edge length to compute Y midline for rotary centering.
        """
        command_list = list(commands)
        park_angle = getattr(rotary, "angle", 0.0)
        park_speed: float | None = None
        # Log initial status before any commands.
        initial_state = self._read_machine_state()
        if initial_state:
            log.debug(
                "[RUIDA UDP] Initial status: 0x%08X busy=%s low_move=%s low_run=%s part_end=%s",
                initial_state.status_bits,
                bool(initial_state.status_bits & self.BUSY_MASK),
                bool(initial_state.status_bits & 0x10),
                bool(initial_state.status_bits & 0x01),
                bool(initial_state.status_bits & self.STATUS_BIT_PART_END),
            )
        job_origin_x = (
            initial_state.x_mm if initial_state and initial_state.x_mm is not None else 0.0
        )
        job_origin_y = (
            initial_state.y_mm if initial_state and initial_state.y_mm is not None else 0.0
        )

        def _state_z_to_command(z_mm: float | None) -> float | None:
            if z_mm is None:
                return None
            return z_mm if self.z_positive_moves_bed_up else -z_mm

        job_origin_z: float | None = None
        if initial_state and initial_state.z_mm is not None:
            job_origin_z = _state_z_to_command(initial_state.z_mm)
        elif self._z_origin_mm is not None:
            job_origin_z = 0.0  # treat as logical zero if origin captured but no absolute read
        if job_origin_z is not None:
            self.z = job_origin_z
        job_origin_z_str = f"{job_origin_z:.3f}" if job_origin_z is not None else "unknown"
        cached_z_origin_str = (
            f"{self._z_origin_mm:.3f}" if self._z_origin_mm is not None else "unset"
        )
        log.info(
            "[RUIDA UDP] Job origin snapshot x=%.3f y=%.3f z=%s (cached_z_origin=%s)",
            job_origin_x,
            job_origin_y,
            job_origin_z_str,
            cached_z_origin_str,
        )

        movement_only_mode = bool(movement_only) or self.movement_only
        plan = compile_command_plan(
            command_list,
            origin_x=job_origin_x,
            origin_y=job_origin_y,
            start_z=job_origin_z,
            edge_length_mm=edge_length_mm,
            z_speed_mm_s=self.z_speed_mm_s,
            movement_only=movement_only_mode,
        )
        if self.pre_cut_warmup_s > 0 and not movement_only_mode and plan.has_cut:
            self._pre_cut_warmup(origin_x=job_origin_x, origin_y=job_origin_y)
        cursor_x = plan.initial_state.cursor_x
        cursor_y = plan.initial_state.cursor_y
        current_speed: float | None = plan.initial_state.current_speed
        current_z: float | None = plan.initial_state.current_z
        last_set_z: float | None = plan.initial_state.last_set_z
        origin_speed: float | None = plan.initial_state.origin_speed
        origin_x = job_origin_x
        origin_y = job_origin_y
        park_z: float | None = plan.origin_z

        def park_head_before_rotary() -> None:
            if movement_only_mode:
                return
            nonlocal cursor_x, cursor_y
            move_speed = origin_speed or current_speed
            need_xy = not math.isclose(cursor_x, origin_x, abs_tol=1e-9) or not math.isclose(
                cursor_y, origin_y, abs_tol=1e-9
            )

            if not need_xy:
                return
            # Keep head at origin in XY; Z adjustments are emitted via RD job payloads.
            self.move(x=origin_x, y=origin_y, speed=move_speed)
            cursor_x, cursor_y = origin_x, origin_y

        def _ensure_at_job_origin() -> None:
            """
            If the controller auto-returns to a machine origin between RD jobs, reposition
            back to the captured job origin before uploading the next block. Polls state
            when possible to avoid drifting self.{x,y}.
            """
            nonlocal cursor_x, cursor_y
            state = None
            try:
                state = self._read_machine_state(read_positions=True)
            except TypeError:
                state = self._read_machine_state()
            if state:
                if state.x_mm is not None:
                    self.x = state.x_mm
                if state.y_mm is not None:
                    self.y = state.y_mm
            need_rehome = not math.isclose(self.x, origin_x, abs_tol=1e-6) or not math.isclose(
                self.y, origin_y, abs_tol=1e-6
            )
            if not need_rehome:
                return
            move_speed = origin_speed or current_speed or self.z_speed_mm_s
            self.move(x=origin_x, y=origin_y, speed=move_speed)
            cursor_x, cursor_y = origin_x, origin_y

        def send_block(block: LaserBlock, block_index: int) -> bool:
            if not block.moves:
                return False
            nonlocal cursor_x, cursor_y, current_speed, current_z, last_set_z, origin_speed
            _ensure_at_job_origin()
            # Refresh Z from the controller in case it changed between RD jobs.
            start_z_mm = block.start_z_mm
            try:
                state = self._read_machine_state(read_positions=True)
                if state and state.z_mm is not None:
                    state_z = _state_z_to_command(state.z_mm)
                    if state_z is not None:
                        start_z_mm = state_z
                        self.z = state_z
                    log.info(
                        "[RUIDA UDP] Updated RD block start Z from controller: %.3fmm",
                        start_z_mm,
                    )
            except Exception:
                pass
            needs_origin_move = block_index > 0
            origin_move_speed = origin_speed or current_speed or self.z_speed_mm_s
            payload_moves = block.moves
            if needs_origin_move:
                origin_move = RDMove(
                    x_mm=origin_x,
                    y_mm=origin_y,
                    speed_mm_s=origin_move_speed,
                    power_pct=0.0,
                    is_cut=False,
                )
                # If a block starts with Z-only moves, keep them first so Z clearance
                # happens before any XY travel.
                leading_z_count = 0
                for mv in payload_moves:
                    if mv.z_mm is None:
                        break
                    leading_z_count += 1
                if leading_z_count:
                    payload_moves = (
                        payload_moves[:leading_z_count]
                        + [origin_move]
                        + payload_moves[leading_z_count:]
                    )
                else:
                    payload_moves = [origin_move] + payload_moves
            start_z_display = f"{start_z_mm:.3f}" if start_z_mm is not None else "unknown"
            block_start_display = (
                f"{block.start_z_mm:.3f}" if block.start_z_mm is not None else "unknown"
            )
            log.info(
                "[RUIDA UDP] Flushing RD block %d: start_z=%s block_start_z=%s moves=%d",
                block_index,
                start_z_display,
                block_start_display,
                len(payload_moves),
            )
            self.send_rd_job(
                payload_moves,
                job_z_mm=None,
                require_busy_transition=True,
                start_z_mm=start_z_mm,
            )
            cursor_x = block.end_state.cursor_x
            cursor_y = block.end_state.cursor_y
            current_speed = block.end_state.current_speed
            current_z = block.end_state.current_z
            last_set_z = block.end_state.last_set_z
            if block.end_state.origin_speed is not None:
                origin_speed = block.end_state.origin_speed
            return True

        block_index = 0

        try:
            for step in plan.steps:
                if isinstance(step, LaserBlock):
                    sent = send_block(step, block_index)
                    if sent:
                        block_index += 1
                    continue
                if isinstance(step, RotationStep):
                    if park_speed is None and step.speed_dps is not None:
                        park_speed = step.speed_dps
                    park_head_before_rotary()
                    # After parking, cursor/last_set_z reflect parked position.
                    current_z = last_set_z
                    rotary.rotate_to(step.angle_deg, step.speed_dps or 0.0)
        finally:
            parked_via_rd = False
            try:
                needs_park_z = False
                if park_z is not None:
                    needs_park_z = last_set_z is None or not math.isclose(
                        last_set_z, park_z, abs_tol=1e-6
                    )
                needs_park_xy = not math.isclose(
                    cursor_x, origin_x, abs_tol=1e-6
                ) or not math.isclose(cursor_y, origin_y, abs_tol=1e-6)

                if needs_park_z or needs_park_xy:
                    park_moves: List[RDMove] = []
                    if needs_park_z and park_z is not None:
                        park_moves.append(
                            RDMove(
                                x_mm=cursor_x,
                                y_mm=cursor_y,
                                speed_mm_s=self.z_speed_mm_s,
                                power_pct=0.0,
                                is_cut=False,
                                z_mm=park_z,
                            )
                        )
                    if needs_park_xy:
                        move_speed = origin_speed or current_speed or self.z_speed_mm_s
                        park_moves.append(
                            RDMove(
                                x_mm=origin_x,
                                y_mm=origin_y,
                                speed_mm_s=move_speed,
                                power_pct=0.0,
                                is_cut=False,
                            )
                        )
                    if park_moves:
                        park_start_z = current_z
                        if needs_park_z and park_start_z is None:
                            try:
                                state = self._read_machine_state(read_positions=True)
                            except TypeError:
                                state = self._read_machine_state()
                            if state and state.z_mm is not None:
                                state_z = _state_z_to_command(state.z_mm)
                                if state_z is not None:
                                    park_start_z = state_z
                                    self.z = state_z
                        self.send_rd_job(
                            park_moves,
                            job_z_mm=None,
                            require_busy_transition=True,
                            start_z_mm=park_start_z,
                        )
                        parked_via_rd = True
                        if needs_park_z and park_z is not None:
                            last_set_z = park_z
                            current_z = park_z
                            self.z = park_z
                        if needs_park_xy:
                            cursor_x, cursor_y = origin_x, origin_y
                            self.x, self.y = origin_x, origin_y
            except Exception:
                log.debug("Final park via RD failed", exc_info=True)
            try:
                if park_angle is not None and hasattr(rotary, "rotate_to"):
                    target_speed = park_speed if park_speed is not None else 30.0
                    current_angle = getattr(rotary, "angle", park_angle)
                    if not math.isclose(current_angle, park_angle, abs_tol=1e-6):
                        rotary.rotate_to(park_angle, target_speed)
            except Exception:
                log.debug("Rotary park failed", exc_info=True)
            try:
                if not parked_via_rd:
                    origin_x = (
                        initial_state.x_mm
                        if initial_state and initial_state.x_mm is not None
                        else job_origin_x
                    )
                    origin_y = (
                        initial_state.y_mm
                        if initial_state and initial_state.y_mm is not None
                        else job_origin_y
                    )
                    if origin_x is not None and origin_y is not None:
                        if not (
                            math.isclose(self.x, origin_x, abs_tol=1e-6)
                            and math.isclose(self.y, origin_y, abs_tol=1e-6)
                        ):
                            self.move(x=origin_x, y=origin_y, speed=self.z_speed_mm_s)
            except Exception:
                log.debug("XY park failed", exc_info=True)
