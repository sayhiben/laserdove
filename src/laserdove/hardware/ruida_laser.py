from __future__ import annotations

import logging
import math
import socket
import time
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional

from .rd_builder import build_rd_job, RDMove
from .ruida_common import (
    checksum,
    clamp_power,
    decode_abscoord_mm,
    decode_status_bits,
    encode_abscoord_mm,
    encode_power_pct,
    swizzle,
    unswizzle,
    should_force_speed,
)
from ..model import CommandType

log = logging.getLogger(__name__)


class RuidaLaser:
    """
    UDP-based Ruida transport (port 50200) using swizzle magic 0x88.
    Sends swizzled payloads with 16-bit checksum and waits for ACK (0xC6)
    per packet. Packets are chunked to <= MTU.
    """

    # Some references report ACK/NACK as 0xCC/0xCF; others as 0xC6/0x46.
    ACK = 0xC6
    NACK = 0x46
    ACK_VALUES = {ACK, 0xCC}
    NACK_VALUES = {NACK, 0xCF}
    MTU = 1470
    MEM_MACHINE_STATUS = b"\x04\x00"
    MEM_CURRENT_X = b"\x04\x21"
    MEM_CURRENT_Y = b"\x04\x31"

    STATUS_BIT_MOVING = 0x01000000
    STATUS_BIT_PART_END = 0x00000002
    STATUS_BIT_JOB_RUNNING = 0x00000001

    # Consider "busy" only when moving or actively running; PART_END means finished.
    BUSY_MASK = STATUS_BIT_MOVING | STATUS_BIT_JOB_RUNNING

    class MachineState(NamedTuple):
        status_bits: int
        x_mm: Optional[float]
        y_mm: Optional[float]

    @staticmethod
    def _swizzle_byte(b: int, magic: int = 0x88) -> int:
        # Thin wrappers kept for backward compatibility in tests.
        from .ruida_common import swizzle_byte

        return swizzle_byte(b, magic)

    @staticmethod
    def _swizzle(payload: bytes, magic: int = 0x88) -> bytes:
        return swizzle(payload, magic=magic)

    @staticmethod
    def _unswizzle_byte(b: int, magic: int = 0x88) -> int:
        from .ruida_common import unswizzle_byte

        return unswizzle_byte(b, magic)

    @staticmethod
    def _unswizzle(payload: bytes, magic: int = 0x88) -> bytes:
        return unswizzle(payload, magic=magic)

    @staticmethod
    def _encode_abscoord_mm(value_mm: float) -> bytes:
        return encode_abscoord_mm(value_mm)

    @staticmethod
    def _encode_power_pct(power_pct: float) -> bytes:
        return encode_power_pct(power_pct)

    def _checksum(self, data: bytes) -> bytes:
        return checksum(data)

    def _decode_abscoord_mm(self, payload: bytes) -> float:
        return decode_abscoord_mm(payload)

    def _decode_status_bits(self, payload: bytes) -> int:
        return decode_status_bits(payload)

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
        socket_factory=socket.socket,
        min_stable_s: float = 0.0,
    ) -> None:
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.magic = magic
        self.movement_only = movement_only
        self.sock: Optional[socket.socket] = None
        self._socket_factory = socket_factory
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self.power = 0.0
        self._last_speed_ums: Optional[int] = None
        self._movement_only_power_sent = False
        self._last_requested_power = 0.0
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

    def _ensure_socket(self) -> None:
        if self.dry_run:
            return
        if self.sock is not None:
            return
        if self._socket_factory is socket.socket:
            sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        else:
            sock = self._socket_factory()
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock = sock
        if hasattr(self.sock, "settimeout"):
            try:
                self.sock.settimeout(self.timeout_s)
            except Exception:
                pass
        try:
            self.sock.bind(("", self.source_port))
        except Exception:
            log.warning("Falling back to ephemeral source port for Ruida UDP (bind failed)")
            try:
                self.sock.bind(("", 0))
            except Exception:
                log.error("Unable to bind UDP socket; switching to dry_run for safety")
                self.dry_run = True
                self.sock = None

    def _send_packets(self, payload: bytes, *, expect_reply: bool = False) -> Optional[bytes]:
        """
        Swizzle, chunk, prepend checksum, and send with ACK wait. Optionally collect a follow-on reply packet
        (e.g., GET_SETTING responses) and return its unswizzled payload.
        """
        swizzled = self._swizzle(payload, magic=self.magic)
        if self.dry_run:
            log.info("[RUIDA UDP DRY] %s", swizzled.hex(" "))
            return None
        self._ensure_socket()
        if self.sock is None:
            log.info("[RUIDA UDP DRY] %s", swizzled.hex(" "))
            return None

        # Chunk
        chunks: List[bytes] = []
        start = 0
        while start < len(swizzled):
            end = min(start + self.MTU, len(swizzled))
            chunk = swizzled[start:end]
            chunk = self._checksum(chunk) + chunk
            chunks.append(chunk)
            start = end

        for idx, chunk in enumerate(chunks):
            retry = 0
            while True:
                self.sock.sendto(chunk, (self.host, self.port))
                try:
                    data, _ = self.sock.recvfrom(8)
                except socket.timeout:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP ACK timeout")
                    continue
                if not data:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP empty response")
                    continue
                if data[0] in self.ACK_VALUES:
                    break
                if data[0] in self.NACK_VALUES and idx == 0:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP NACK on first packet")
                    continue
                raise RuntimeError(f"UDP unexpected response {data.hex()}")

        if not expect_reply:
            return None

        try:
            reply, _ = self.sock.recvfrom(self.MTU)
        except socket.timeout:
            raise RuntimeError("UDP reply timeout")

        payload_only = reply
        if len(reply) > 2:
            maybe_checksum = reply[:2]
            maybe_payload = reply[2:]
            if maybe_checksum == self._checksum(maybe_payload):
                payload_only = maybe_payload

        unswizzled = self._unswizzle(payload_only, magic=self.magic)
        if not unswizzled and reply:
            unswizzled = reply
        return unswizzled

    def _get_memory_value(self, address: bytes, *, expected_len: int) -> Optional[bytes]:
        payload = bytes([0xDA, 0x00]) + address
        reply = self._send_packets(payload, expect_reply=True)
        if reply is None:
            return None

        if reply.startswith(b"\xDA\x01" + address):
            data = reply[4:]
        elif reply.startswith(address):
            data = reply[2:]
        else:
            log.warning("[RUIDA UDP] Unexpected reply %s for address %s", reply.hex(" "), address.hex(" "))
            return None

        if len(data) < expected_len:
            log.warning("[RUIDA UDP] Truncated reply for %s: %s", address.hex(" "), data.hex(" "))
            return None
        return data[:expected_len]

    def _read_machine_state(self, *, read_positions: bool = True) -> Optional[MachineState]:
        try:
            status_payload = self._get_memory_value(self.MEM_MACHINE_STATUS, expected_len=4)
            x_payload = self._get_memory_value(self.MEM_CURRENT_X, expected_len=5) if read_positions else None
            y_payload = self._get_memory_value(self.MEM_CURRENT_Y, expected_len=5) if read_positions else None
        except RuntimeError as exc:
            log.warning("[RUIDA UDP] Failed to poll machine state: %s", exc)
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y) if self.dry_run else None

        if status_payload is None:
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y) if self.dry_run else None

        status_bits = self._decode_status_bits(status_payload)
        x_mm = self._decode_abscoord_mm(x_payload) if x_payload else None
        y_mm = self._decode_abscoord_mm(y_payload) if y_payload else None
        return self.MachineState(status_bits=status_bits, x_mm=x_mm, y_mm=y_mm)

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
        if self.dry_run:
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y)

        last_state: Optional[RuidaLaser.MachineState] = None
        baseline_bits: Optional[int] = None
        baseline_pos: Optional[tuple[float, float]] = None
        stable_counter = 0
        saw_activity = False
        last_pos: Optional[tuple[float, float]] = None
        stable_target = 1
        stable_start: Optional[float] = None
        for attempt in range(1, max_attempts + 1):
            try:
                state = self._read_machine_state(read_positions=read_positions)
            except TypeError:
                # For monkeypatched test doubles without the keyword argument
                state = self._read_machine_state()
            except RuntimeError as exc:
                log.warning("[RUIDA UDP] Failed to poll machine state: %s", exc)
                if last_state and not require_busy_transition:
                    log.debug("[RUIDA UDP] Returning last known state after poll failure (no busy transition required)")
                    return last_state
                state = None
            if state:
                if baseline_bits is None:
                    baseline_bits = state.status_bits
                elif state.status_bits != baseline_bits:
                    saw_activity = True
                    baseline_bits = state.status_bits
                    stable_counter = 0
                if baseline_pos is None and state.x_mm is not None and state.y_mm is not None:
                    baseline_pos = (state.x_mm, state.y_mm)
                last_state = state
                part_end = bool(state.status_bits & self.STATUS_BIT_PART_END)
                move_low = bool(state.status_bits & 0x10)  # lower-byte IsMove per EduTech wiki
                run_low = bool(state.status_bits & 0x01)  # lower-byte JobRunning bit on some firmwares
                log.debug(
                    "[RUIDA UDP] Status poll %d/%d: raw=0x%08X low_move=%s low_run=%s part_end=%s stable_counter=%d saw_activity=%s",
                    attempt,
                    max_attempts,
                    state.status_bits,
                    move_low,
                    run_low,
                    part_end,
                    stable_counter,
                    saw_activity,
                )
                if baseline_bits is not None and state.status_bits != baseline_bits:
                    saw_activity = True
                if last_pos and state.x_mm is not None and state.y_mm is not None:
                    dx = abs(state.x_mm - last_pos[0])
                    dy = abs(state.y_mm - last_pos[1])
                    if dx > pos_tol_mm or dy > pos_tol_mm:
                        saw_activity = True
                if baseline_pos and state.x_mm is not None and state.y_mm is not None:
                    if abs(state.x_mm - baseline_pos[0]) > pos_tol_mm or abs(state.y_mm - baseline_pos[1]) > pos_tol_mm:
                        saw_activity = True
                if state.x_mm is not None and state.y_mm is not None:
                    last_pos = (state.x_mm, state.y_mm)

                stable_match = baseline_bits is None or state.status_bits == baseline_bits
                pos_stable = True
                if last_pos and state.x_mm is not None and state.y_mm is not None:
                    pos_stable = abs(state.x_mm - last_pos[0]) <= pos_tol_mm and abs(state.y_mm - last_pos[1]) <= pos_tol_mm
                if stable_match and pos_stable:
                    if stable_counter == 0:
                        stable_start = time.monotonic()
                    stable_counter += 1
                else:
                    stable_counter = 0
                    stable_start = None

                stable_elapsed = 0.0 if stable_start is None else time.monotonic() - stable_start

                if not require_busy_transition and stable_counter >= stable_polls and stable_elapsed >= min_stable_s:
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    log.debug(
                        "[RUIDA UDP] Ready via idle stability on attempt %d: status=0x%08X x=%.3f y=%.3f stable_counter=%d stable_elapsed=%.2fs",
                        attempt,
                        state.status_bits,
                        self.x,
                        self.y,
                        stable_counter,
                        stable_elapsed,
                    )
                    return state

                if not require_busy_transition and stable_counter >= stable_target and stable_elapsed >= min_stable_s:
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    log.debug(
                        "[RUIDA UDP] Ready via idle stability on attempt %d: status=0x%08X x=%.3f y=%.3f stable_counter=%d stable_elapsed=%.2fs",
                        attempt,
                        state.status_bits,
                        self.x,
                        self.y,
                        stable_counter,
                        stable_elapsed,
                    )
                    return state

                if (
                    stable_counter >= stable_target
                    and (saw_activity or move_low or run_low or part_end)
                    and stable_elapsed >= min_stable_s
                ):
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    log.debug(
                        "[RUIDA UDP] Ready via stability on attempt %d: status=0x%08X x=%.3f y=%.3f activity_seen=%s stable_counter=%d stable_elapsed=%.2fs",
                        attempt,
                        state.status_bits,
                        self.x,
                        self.y,
                        saw_activity,
                        stable_counter,
                        stable_elapsed,
                    )
                    return state
                if stable_counter >= stable_target and stable_elapsed < min_stable_s:
                    log.debug(
                        "[RUIDA UDP] Stable but waiting for %.2fs (elapsed %.2fs)",
                        min_stable_s,
                        stable_elapsed,
                    )
            else:
                if last_state and not require_busy_transition:
                    log.debug("[RUIDA UDP] Returning last known state after missing poll (no busy transition required)")
                    return last_state
            log.debug("[RUIDA UDP] Waiting (attempt %d/%d); sleeping %.2fs", attempt, max_attempts, delay_s)
            time.sleep(delay_s)

        raise RuntimeError(f"Ruida controller not ready after {max_attempts} attempts (last={last_state})")

    def _set_speed(self, speed_mm_s: float) -> None:
        speed_ums, changed = should_force_speed(self._last_speed_ums, speed_mm_s)
        if not changed:
            return
        self._last_speed_ums = speed_ums
        payload = bytes([0xC9, 0x02]) + self._encode_abscoord_mm(speed_mm_s)
        log.info("[RUIDA UDP] SET_SPEED %.3f mm/s", speed_mm_s)
        self._send_packets(payload)

    def move(self, x=None, y=None, z=None, speed=None) -> None:
        self._wait_for_ready()
        if x is not None:
            self.x = x
        if y is not None:
            self.y = y
        if z is not None:
            self.z = z
        if self.power != 0.0:
            self.set_laser_power(0.0)
        if speed is not None:
            self._set_speed(speed)
        if x is None and y is None:
            return
        x_mm = self.x if x is None else x
        y_mm = self.y if y is None else y
        payload = bytes([0x88]) + self._encode_abscoord_mm(x_mm) + self._encode_abscoord_mm(y_mm)
        log.info("[RUIDA UDP] MOVE x=%.3f y=%.3f z=%.3f speed=%s", self.x, self.y, self.z, speed)
        self._send_packets(payload)

    def cut_line(self, x, y, speed) -> None:
        self._wait_for_ready()
        self.x = x
        self.y = y
        if speed is not None:
            self._set_speed(speed)
        payload = bytes([0xA8]) + self._encode_abscoord_mm(x) + self._encode_abscoord_mm(y)
        log.info("[RUIDA UDP] CUT_LINE x=%.3f y=%.3f speed=%.3f power=%.1f%%",
                 x, y, speed, self.power)
        self._send_packets(payload)

    def set_laser_power(self, power_pct) -> None:
        self._wait_for_ready()
        requested_power, should_update = clamp_power(power_pct, self.power)
        self._last_requested_power = requested_power

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
            payload = bytes([0xC7]) + self._encode_power_pct(0.0)
            self._send_packets(payload)
            return

        if not should_update:
            return

        self.power = requested_power
        payload = bytes([0xC7]) + self._encode_power_pct(requested_power)
        log.info("[RUIDA UDP] SET_LASER_POWER %.1f%%", requested_power)
        self._send_packets(payload)

    # ---------------- RD job upload/run helpers ----------------
    def send_rd_job(self, moves: List[RDMove], job_z_mm: float | None = None, *, require_busy_transition: bool = True) -> None:
        """
        Build a minimal RD job and send it over UDP 50200. Auto-runs on receipt.
        """
        if not moves:
            return
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
        payload = build_rd_job(moves, job_z_mm=job_z_mm, air_assist=self.air_assist)
        if self.save_rd_dir:
            self.save_rd_dir.mkdir(parents=True, exist_ok=True)
            self._rd_job_counter += 1
            filename = f"job_{self._rd_job_counter:03d}"
            if job_z_mm is not None:
                filename += f"_z{job_z_mm:.3f}"
            path = self.save_rd_dir / f"{filename}.rd"
            swizzled = self._swizzle(payload, magic=self.magic)
            path.write_bytes(swizzled)
            log.info("[RUIDA UDP] Saved RD job to %s", path)
        log.info("[RUIDA UDP] Uploading RD job with %d moves%s",
                 len(moves), f" z={job_z_mm:.3f}" if job_z_mm is not None else "")
        if self.dry_run:
            log.debug("[RUIDA UDP DRY RD] %s", payload.hex(" "))
        self._send_packets(payload)
        # Wait for completion; treat PART_END as done.
        self._wait_for_ready(
            require_busy_transition=require_busy_transition,
            min_stable_s=self.min_stable_s,
        )

    def run_sequence_with_rotary(self, commands: Iterable, rotary, *, travel_only: bool = False, edge_length_mm: float | None = None) -> None:
        """
        Partition commands at ROTATE boundaries; send each laser block as an RD job;
        run rotary moves via provided rotary interface in between.
        """
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
        job_origin_x = initial_state.x_mm if initial_state and initial_state.x_mm is not None else 0.0
        job_origin_y = initial_state.y_mm if initial_state and initial_state.y_mm is not None else 0.0
        y_center = (edge_length_mm / 2.0) if edge_length_mm is not None else 0.0

        travel_only = travel_only or self.movement_only
        current_power = 0.0
        current_speed: float | None = None
        cursor_x = job_origin_x
        cursor_y = job_origin_y
        current_z: float | None = None
        last_set_z: float | None = None
        origin_x = job_origin_x
        origin_y = job_origin_y
        origin_z: float | None = None
        origin_speed: float | None = None

        def park_head_before_rotary() -> None:
            nonlocal cursor_x, cursor_y, current_z, last_set_z, block_z
            target_z = origin_z if origin_z is not None else last_set_z
            move_speed = origin_speed or current_speed
            need_xy = not math.isclose(cursor_x, origin_x, abs_tol=1e-9) or not math.isclose(cursor_y, origin_y, abs_tol=1e-9)
            need_z = target_z is not None and (last_set_z is None or not math.isclose(last_set_z or 0.0, target_z, abs_tol=1e-6))

            if not need_xy and not need_z:
                return

            current_z_for_order = current_z if current_z is not None else last_set_z

            # If we know the target Z and current Z, order moves to avoid collisions.
            if need_xy and need_z and current_z_for_order is not None and target_z is not None:
                closer = target_z > current_z_for_order if self.z_positive_moves_bed_up else target_z < current_z_for_order
                if closer:
                    # Move closer (bed up) first, then translate.
                    self.move(z=target_z, speed=move_speed)
                    current_z = target_z
                    last_set_z = target_z
                    block_z = target_z
                    self.move(x=origin_x, y=origin_y, speed=move_speed)
                    cursor_x, cursor_y = origin_x, origin_y
                    return
                else:
                    # Translate first, then move bed away.
                    self.move(x=origin_x, y=origin_y, speed=move_speed)
                    cursor_x, cursor_y = origin_x, origin_y
                    self.move(z=target_z, speed=move_speed)
                    current_z = target_z
                    last_set_z = target_z
                    block_z = target_z
                    return

            # If we have a target Z but no current Z (e.g., never set), do a combined move.
            if need_z and target_z is not None:
                self.move(x=origin_x if need_xy else None, y=origin_y if need_xy else None, z=target_z, speed=move_speed)
                if need_xy:
                    cursor_x, cursor_y = origin_x, origin_y
                current_z = target_z
                last_set_z = target_z
                block_z = target_z
                return

            # Only XY to park; include Z if known to keep at origin height.
            if need_xy:
                self.move(
                    x=origin_x,
                    y=origin_y,
                    z=target_z if target_z is not None else None,
                    speed=move_speed,
                )
                cursor_x, cursor_y = origin_x, origin_y
                if target_z is not None:
                    current_z = target_z
                    last_set_z = target_z
                    block_z = target_z

        def flush_block(block_moves: List[RDMove], block_z: float | None) -> None:
            if not block_moves:
                return
            job_z = block_z if block_z is not None else last_set_z
            if job_z is not None and not self.dry_run:
                try:
                    self.move(z=job_z, speed=self.z_speed_mm_s)
                except Exception:
                    log.debug("Pre-RD Z move failed; continuing to embed Z in RD", exc_info=True)
            self.send_rd_job(block_moves, job_z_mm=job_z, require_busy_transition=True)

        block: List[RDMove] = []
        block_z: float | None = None

        for cmd in commands:
            if cmd.type.name == "ROTATE":
                flush_block(block, block_z)
                block = []
                block_z = None
                park_head_before_rotary()
                # After parking, cursor/last_set_z reflect parked position.
                current_z = last_set_z
                rotary.rotate_to(cmd.angle_deg, cmd.speed_mm_s or 0.0)
                continue

            if cmd.type.name == "SET_LASER_POWER":
                if travel_only:
                    current_power = 0.0
                elif cmd.power_pct is not None:
                    current_power = cmd.power_pct
                continue

            if cmd.type.name == "MOVE":
                x = cursor_x if cmd.x is None else job_origin_x + cmd.x
                y = cursor_y if cmd.y is None else job_origin_y + (cmd.y - y_center)
                if cmd.z is not None:
                    if block_z is not None and not math.isclose(cmd.z, block_z, abs_tol=1e-6) and block:
                        flush_block(block, block_z)
                        block = []
                    current_z = cmd.z
                    if origin_z is None:
                        origin_z = current_z
                    last_set_z = current_z
                    block_z = current_z
                if cmd.speed_mm_s is not None:
                    current_speed = cmd.speed_mm_s
                    if origin_speed is None:
                        origin_speed = current_speed
                if current_speed is None:
                    continue
                block.append(RDMove(x_mm=x, y_mm=y, speed_mm_s=current_speed,
                                    power_pct=current_power, is_cut=False))
                cursor_x, cursor_y = x, y
                continue

            if cmd.type.name == "CUT_LINE":
                x = cursor_x if cmd.x is None else job_origin_x + cmd.x
                y = cursor_y if cmd.y is None else job_origin_y + (cmd.y - y_center)
                if cmd.z is not None:
                    if block_z is not None and not math.isclose(cmd.z, block_z, abs_tol=1e-6) and block:
                        flush_block(block, block_z)
                        block = []
                    current_z = cmd.z
                    last_set_z = current_z
                    block_z = current_z
                if cmd.speed_mm_s is not None:
                    current_speed = cmd.speed_mm_s
                if current_speed is None:
                    continue
                block.append(RDMove(
                    x_mm=x,
                    y_mm=y,
                    speed_mm_s=current_speed,
                    power_pct=current_power,
                    is_cut=not travel_only,
                ))
                cursor_x, cursor_y = x, y
                continue

        flush_block(block, block_z)

    def cleanup(self) -> None:
        """Release UDP socket if open."""
        if self.sock is not None:
            try:
                self.sock.close()
            except Exception:
                log.debug("Failed to close Ruida socket", exc_info=True)
            self.sock = None
