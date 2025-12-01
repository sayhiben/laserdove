from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional

from .rd_builder import build_rd_job, RDMove
from .ruida_transport import RuidaUDPClient
from .ruida_common import (
    clamp_power,
    decode_abscoord_mm,
    decode_status_bits,
    encode_abscoord_mm,
    encode_abscoord_mm_signed,
    encode_power_pct,
    swizzle,
    should_force_speed,
)

log = logging.getLogger(__name__)


class RuidaLaser:
    """
    UDP-based Ruida transport (port 50200) using swizzle magic 0x88.
    Uses the shared RuidaUDPClient for send/ACK handling.
    """

    MEM_MACHINE_STATUS = b"\x04\x00"
    MEM_CURRENT_X = b"\x04\x21"
    MEM_CURRENT_Y = b"\x04\x31"
    MEM_CURRENT_Z = b"\x04\x41"

    STATUS_BIT_MOVING = 0x01000000
    STATUS_BIT_PART_END = 0x00000002
    STATUS_BIT_JOB_RUNNING = 0x00000001

    # Consider "busy" only when moving or actively running; PART_END means finished.
    BUSY_MASK = STATUS_BIT_MOVING | STATUS_BIT_JOB_RUNNING

    class MachineState(NamedTuple):
        status_bits: int
        x_mm: Optional[float]
        y_mm: Optional[float]
        z_mm: Optional[float] = None

    def __init__(
        self,
        host: str,
        port: int = 50200,
        *,
        source_port: int = 40200,
        timeout_s: float = 3.0,
        dry_run: bool = False,
        magic: int = 0x88,
        movement_only: bool = False,
        save_rd_dir: Path | None = None,
        air_assist: bool = True,
        z_positive_moves_bed_up: bool = True,
        z_speed_mm_s: float = 5.0,
        socket_factory=None,
        min_stable_s: float = 0.0,
    ) -> None:
        """
        Create a Ruida UDP transport wrapper.

        Args:
            host: Controller hostname or IP.
            port: UDP port for RD uploads (default 50200).
            source_port: Local UDP source port to bind.
            timeout_s: Socket timeout for ACK/reply waits.
            dry_run: If True, log packets without sending.
            magic: Swizzle magic key (0x88 for 644xG).
            movement_only: Suppress power in generated jobs.
            save_rd_dir: Optional path to persist swizzled RD jobs.
            air_assist: Whether to enable air assist in RD jobs.
            z_positive_moves_bed_up: Interpret Z+ as bed-up (default).
            z_speed_mm_s: Speed to use for Z moves emitted in RD jobs.
            socket_factory: Optional socket factory for tests.
            min_stable_s: Minimum idle time before declaring ready.
        """
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.magic = magic
        self.movement_only = movement_only
        socket_factory = socket_factory or __import__("socket").socket
        self._udp = RuidaUDPClient(
            host,
            port=port,
            source_port=source_port,
            timeout_s=timeout_s,
            magic=magic,
            dry_run=dry_run,
            socket_factory=socket_factory,
        )
        self._udp.MTU = 1470
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self._z_origin_mm: Optional[float] = None
        self.power = 0.0
        self._last_speed_ums: Optional[int] = None
        self._movement_only_power_sent = False
        self.save_rd_dir = Path(save_rd_dir) if save_rd_dir else None
        self._rd_job_counter = 0
        self.air_assist = air_assist
        self.z_positive_moves_bed_up = z_positive_moves_bed_up
        self.z_speed_mm_s = z_speed_mm_s
        self.min_stable_s = min_stable_s
        log.info(
            "RuidaLaser initialized for UDP host=%s port=%d dry_run=%s movement_only=%s",
            host,
            port,
            dry_run,
            movement_only,
        )

    def _get_memory_value(self, address: bytes, *, expected_len: int) -> Optional[bytes]:
        """
        Read a memory address via Ruida UDP GET_SETTING (0xDA 0x00).

        Args:
            address: Two-byte memory address.
            expected_len: Minimum number of data bytes expected.

        Returns:
            Raw data bytes, or None on failure/truncation/dry-run.
        """
        payload = bytes([0xDA, 0x00]) + address
        reply = self._udp.send_packets(payload, expect_reply=True)
        if reply is None:
            return None

        if reply.startswith(b"\xda\x01" + address):
            data = reply[4:]
        elif reply.startswith(address):
            data = reply[2:]
        else:
            log.warning(
                "[RUIDA UDP] Unexpected reply %s for address %s", reply.hex(" "), address.hex(" ")
            )
            return None

        if len(data) < expected_len:
            log.warning("[RUIDA UDP] Truncated reply for %s: %s", address.hex(" "), data.hex(" "))
            return None
        return data[:expected_len]

    def _read_machine_state(self, *, read_positions: bool = True) -> Optional[MachineState]:
        """
        Poll status and optionally axes from controller memory.

        Args:
            read_positions: If True, also request X/Y/Z addresses.

        Returns:
            MachineState with decoded bits and coordinates, or None on failure.
        """
        try:
            status_payload = self._get_memory_value(self.MEM_MACHINE_STATUS, expected_len=4)
            x_payload = (
                self._get_memory_value(self.MEM_CURRENT_X, expected_len=5)
                if read_positions
                else None
            )
            y_payload = (
                self._get_memory_value(self.MEM_CURRENT_Y, expected_len=5)
                if read_positions
                else None
            )
            z_payload = (
                self._get_memory_value(self.MEM_CURRENT_Z, expected_len=5)
                if read_positions
                else None
            )
        except RuntimeError as exc:
            log.warning("[RUIDA UDP] Failed to poll machine state: %s", exc)
            return (
                self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y, z_mm=self.z)
                if self.dry_run
                else None
            )

        if status_payload is None:
            return (
                self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y, z_mm=self.z)
                if self.dry_run
                else None
            )

        status_bits = decode_status_bits(status_payload)
        x_mm = decode_abscoord_mm(x_payload) if x_payload else None
        y_mm = decode_abscoord_mm(y_payload) if y_payload else None
        raw_z_mm = decode_abscoord_mm(z_payload) if z_payload else None
        if raw_z_mm is not None and self._z_origin_mm is None:
            self._z_origin_mm = raw_z_mm
        z_rel = (
            raw_z_mm - self._z_origin_mm
            if raw_z_mm is not None and self._z_origin_mm is not None
            else None
        )
        if z_rel is not None and not self.z_positive_moves_bed_up:
            z_rel = -z_rel
        z_mm = z_rel
        return self.MachineState(status_bits=status_bits, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)

    def _wait_for_ready(
        self,
        *,
        max_attempts: int = 400,
        delay_s: float = 0.5,
        require_busy_transition: bool = False,
        stable_polls: int = 3,
        pos_tol_mm: float = 1e-3,
        read_positions: bool = True,
        min_stable_s: float = 0.0,
    ) -> MachineState:
        """
        Poll until the controller appears idle and stable.

        Args:
            max_attempts: Maximum polls before giving up.
            delay_s: Delay between polls (seconds).
            require_busy_transition: If True, wait until at least one busy/motion state was observed.
            stable_polls: Number of consecutive stable polls required.
            pos_tol_mm: Position delta that counts as motion.
            read_positions: If False, skip reading X/Y/Z.
            min_stable_s: Minimum time in a stable state before returning.

        Returns:
            Final MachineState considered ready.

        Raises:
            RuntimeError: If readiness is not reached within max_attempts.
        """
        if self.dry_run:
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y, z_mm=self.z)

        effective_min_stable_s = min(min_stable_s, 1.0) if self.movement_only else min_stable_s

        last_state: Optional[RuidaLaser.MachineState] = None
        last_bits: Optional[int] = None
        last_pos: dict[str, float] = {}
        stable_counter = 0
        stable_start: Optional[float] = None
        saw_busy_or_motion = False

        for attempt in range(1, max_attempts + 1):
            try:
                state = self._read_machine_state(read_positions=read_positions)
            except RuntimeError as exc:
                log.warning("[RUIDA UDP] Failed to poll machine state: %s", exc)
                state = None

            if state is None:
                if attempt <= 2 or attempt % 10 == 1:
                    log.debug(
                        "[RUIDA UDP] Poll returned no state (attempt %d/%d)", attempt, max_attempts
                    )
                time.sleep(delay_s)
                continue

            busy = bool(state.status_bits & self.BUSY_MASK)
            part_end = bool(state.status_bits & self.STATUS_BIT_PART_END)
            if busy or part_end:
                saw_busy_or_motion = True

            positions = {"x": state.x_mm, "y": state.y_mm, "z": state.z_mm}
            movement = False
            for axis, value in positions.items():
                prev_val = last_pos.get(axis)
                if (
                    value is not None
                    and prev_val is not None
                    and abs(value - prev_val) > pos_tol_mm
                ):
                    movement = True
                    saw_busy_or_motion = True
            status_changed = last_bits is not None and state.status_bits != last_bits
            if status_changed:
                saw_busy_or_motion = True

            if movement or status_changed:
                stable_counter = 0
                stable_start = None
            else:
                if stable_counter == 0:
                    stable_start = time.monotonic()
                stable_counter += 1

            stable_elapsed = 0.0 if stable_start is None else time.monotonic() - stable_start

            last_state = state
            last_bits = state.status_bits
            last_pos = {axis: value for axis, value in positions.items() if value is not None}

            idle = not busy
            stable_enough = (
                stable_counter >= stable_polls and stable_elapsed >= effective_min_stable_s
            )
            ready = (
                idle
                and stable_enough
                and (not require_busy_transition or saw_busy_or_motion or part_end)
            )

            if ready:
                if state.x_mm is not None:
                    self.x = state.x_mm
                if state.y_mm is not None:
                    self.y = state.y_mm
                if state.z_mm is not None and not self.movement_only:
                    self.z = state.z_mm
                log.debug(
                    "[RUIDA UDP] Ready after %d polls: status=0x%08X busy=%s part_end=%s stable_counter=%d stable_elapsed=%.2fs saw_activity=%s",
                    attempt,
                    state.status_bits,
                    busy,
                    part_end,
                    stable_counter,
                    stable_elapsed,
                    saw_busy_or_motion,
                )
                return state

            if attempt <= 2 or attempt % 10 == 1:
                log.debug(
                    "[RUIDA UDP] Waiting: attempt %d/%d status=0x%08X busy=%s part_end=%s stable_counter=%d stable_elapsed=%.2fs saw_activity=%s",
                    attempt,
                    max_attempts,
                    state.status_bits,
                    busy,
                    part_end,
                    stable_counter,
                    stable_elapsed,
                    saw_busy_or_motion,
                )
            time.sleep(delay_s)

        raise RuntimeError(
            f"Ruida controller not ready after {max_attempts} attempts (last={last_state})"
        )

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

    # ---------------- RD job upload/run helpers ----------------
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

    def run_sequence_with_rotary(
        self,
        commands: Iterable,
        rotary,
        *,
        movement_only: bool | None = None,
        travel_only: bool | None = None,
        edge_length_mm: float | None = None,
    ) -> None:
        """
        Partition commands at ROTATE boundaries; send each laser block as an RD job;
        run rotary moves via provided rotary interface in between.

        Args:
            commands: Iterable of high-level commands (MOVE/CUT/SET_LASER_POWER/ROTATE).
            rotary: Rotary backend implementing rotate_to.
            movement_only: Force power=0 regardless of command (overrides instance flag).
            travel_only: Legacy alias for movement_only.
            edge_length_mm: Board edge length to compute Y midline for rotary centering.
        """
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
        job_origin_z: float | None = None
        if initial_state and initial_state.z_mm is not None:
            job_origin_z = initial_state.z_mm
        elif self._z_origin_mm is not None:
            job_origin_z = 0.0  # treat as logical zero if origin captured but no absolute read
        if job_origin_z is not None:
            self.z = job_origin_z
        y_center = (edge_length_mm / 2.0) if edge_length_mm is not None else 0.0

        # movement_only is the preferred name; travel_only is accepted for backward compatibility.
        movement_only_mode = any(flag for flag in (movement_only, travel_only, self.movement_only))
        current_power = 0.0
        current_speed: float | None = None
        cursor_x = job_origin_x
        cursor_y = job_origin_y
        current_z: float | None = job_origin_z
        last_set_z: float | None = None
        origin_x = job_origin_x
        origin_y = job_origin_y
        origin_speed: float | None = None
        park_z: float | None = job_origin_z

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
            if need_xy:
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

        def flush_block(block_moves: List[RDMove], block_index: int) -> None:
            if not block_moves:
                return
            nonlocal block_start_z
            _ensure_at_job_origin()
            needs_origin_move = block_index > 0
            origin_move_speed = origin_speed or current_speed or self.z_speed_mm_s
            payload_moves = (
                [
                    RDMove(
                        x_mm=origin_x,
                        y_mm=origin_y,
                        speed_mm_s=origin_move_speed,
                        power_pct=0.0,
                        is_cut=False,
                    )
                ]
                + block_moves
                if needs_origin_move
                else block_moves
            )
            self.send_rd_job(
                payload_moves,
                job_z_mm=None,
                require_busy_transition=True,
                start_z_mm=block_start_z,
            )
            block_start_z = current_z

        block: List[RDMove] = []
        block_start_z: float | None = current_z
        block_index = 0

        try:
            for cmd in commands:
                if cmd.type.name == "ROTATE":
                    if park_speed is None and cmd.speed_mm_s is not None:
                        park_speed = cmd.speed_mm_s
                    flush_block(block, block_index)
                    block = []
                    block_start_z = current_z
                    block_index += 1
                    park_head_before_rotary()
                    # After parking, cursor/last_set_z reflect parked position.
                    current_z = last_set_z
                    rotary.rotate_to(cmd.angle_deg, cmd.speed_mm_s or 0.0)
                    continue

                if cmd.type.name == "SET_LASER_POWER":
                    if movement_only_mode:
                        current_power = 0.0
                    elif cmd.power_pct is not None:
                        current_power = cmd.power_pct
                    continue

                if cmd.type.name == "MOVE":
                    x = cursor_x if cmd.x is None else job_origin_x + cmd.x
                    y = cursor_y if cmd.y is None else job_origin_y + (cmd.y - y_center)
                    if cmd.z is not None:
                        current_z = cmd.z
                        last_set_z = current_z
                        self.z = current_z
                        block.append(
                            RDMove(
                                x_mm=x,
                                y_mm=y,
                                speed_mm_s=self.z_speed_mm_s,
                                power_pct=current_power,
                                is_cut=False,
                                z_mm=current_z,
                            )
                        )
                    if cmd.speed_mm_s is not None:
                        current_speed = cmd.speed_mm_s
                        if origin_speed is None:
                            origin_speed = current_speed
                    if current_speed is None:
                        continue
                    block.append(
                        RDMove(
                            x_mm=x,
                            y_mm=y,
                            speed_mm_s=current_speed,
                            power_pct=current_power,
                            is_cut=False,
                        )
                    )
                    cursor_x, cursor_y = x, y
                    continue

                if cmd.type.name == "CUT_LINE":
                    x = cursor_x if cmd.x is None else job_origin_x + cmd.x
                    y = cursor_y if cmd.y is None else job_origin_y + (cmd.y - y_center)
                    if cmd.z is not None:
                        current_z = cmd.z
                        last_set_z = current_z
                        self.z = current_z
                        block.append(
                            RDMove(
                                x_mm=x,
                                y_mm=y,
                                speed_mm_s=self.z_speed_mm_s,
                                power_pct=current_power,
                                is_cut=False,
                                z_mm=current_z,
                            )
                        )
                    if cmd.speed_mm_s is not None:
                        current_speed = cmd.speed_mm_s
                    if current_speed is None:
                        continue
                    block.append(
                        RDMove(
                            x_mm=x,
                            y_mm=y,
                            speed_mm_s=current_speed,
                            power_pct=current_power,
                            is_cut=not movement_only_mode,
                        )
                    )
                    cursor_x, cursor_y = x, y
                    continue

            flush_block(block, block_index)
        finally:
            if park_z is not None:
                try:
                    needs_park = last_set_z is None or not math.isclose(
                        last_set_z, park_z, abs_tol=1e-6
                    )
                    if needs_park:
                        park_moves = [
                            RDMove(
                                x_mm=cursor_x,
                                y_mm=cursor_y,
                                speed_mm_s=self.z_speed_mm_s,
                                power_pct=0.0,
                                is_cut=False,
                                z_mm=park_z,
                            )
                        ]
                        self.send_rd_job(
                            park_moves,
                            job_z_mm=None,
                            require_busy_transition=True,
                            start_z_mm=current_z,
                        )
                        last_set_z = park_z
                        current_z = park_z
                except Exception:
                    log.debug("Z park via RD failed", exc_info=True)
            try:
                if park_angle is not None and hasattr(rotary, "rotate_to"):
                    target_speed = park_speed if park_speed is not None else 30.0
                    current_angle = getattr(rotary, "angle", park_angle)
                    if not math.isclose(current_angle, park_angle, abs_tol=1e-6):
                        rotary.rotate_to(park_angle, target_speed)
            except Exception:
                log.debug("Rotary park failed", exc_info=True)
            try:
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

    def cleanup(self) -> None:
        """Release UDP socket if open."""
        if getattr(self._udp, "sock", None) is not None:
            try:
                self._udp.sock.close()
            except Exception:
                log.debug("Failed to close Ruida socket", exc_info=True)
            self._udp.sock = None
