from __future__ import annotations

import logging
import math
import time
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional

from .rd_builder import build_rd_job, RDMove
from .ruida_transport import RuidaUDPClient
from .ruida_panel import RuidaPanelInterface
from .ruida_common import (
    clamp_power,
    decode_abscoord_mm,
    decode_status_bits,
    encode_abscoord_mm,
    encode_abscoord_mm_signed,
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
    Uses the shared RuidaUDPClient for send/ACK handling.
    """
    # Reuse transport ACK/NACK tables so legacy send wrapper keeps working.
    ACK_VALUES = RuidaUDPClient.ACK_VALUES
    NACK_VALUES = RuidaUDPClient.NACK_VALUES

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
        from .ruida_common import checksum

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
        socket_factory=None,
        panel_z_step_mm: float = 0.05,
        panel_z_max_step_mm: float = 0.5,
        min_stable_s: float = 0.0,
    ) -> None:
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
        # Legacy socket handle for tests/backward compatibility.
        self.sock: Optional[object] = None
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self._z_origin_mm: Optional[float] = None
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
        self.panel_z_step_mm = panel_z_step_mm
        self.panel_z_max_step_mm = panel_z_max_step_mm
        self._panel_iface: RuidaPanelInterface | None = None
        log.info(
            "RuidaLaser initialized for UDP host=%s port=%d dry_run=%s movement_only=%s",
            host,
            port,
            dry_run,
            movement_only,
        )

    def _send_packets(self, payload: bytes, *, expect_reply: bool = False) -> Optional[bytes]:
        """
        Legacy send wrapper (uses RuidaUDPClient socket but retains ACK/NACK behavior for tests).
        """
        swizzled = self._swizzle(payload, magic=self.magic)
        if self.dry_run:
            log.info("[RUIDA UDP DRY] %s", swizzled.hex(" "))
            return None
        self._udp.dry_run = self.dry_run
        self._udp._ensure_socket()
        self.sock = self._udp.sock
        if self.sock is None:
            log.info("[RUIDA UDP DRY] %s", swizzled.hex(" "))
            return None

        # Chunk
        chunks: List[bytes] = []
        start = 0
        mtu = getattr(self._udp, "MTU", 1470)
        while start < len(swizzled):
            end = min(start + mtu, len(swizzled))
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
                except Exception:
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
            reply, _ = self.sock.recvfrom(mtu)
        except Exception:
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
            z_payload = self._get_memory_value(self.MEM_CURRENT_Z, expected_len=5) if read_positions else None
        except RuntimeError as exc:
            log.warning("[RUIDA UDP] Failed to poll machine state: %s", exc)
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y, z_mm=self.z) if self.dry_run else None

        if status_payload is None:
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y, z_mm=self.z) if self.dry_run else None

        status_bits = self._decode_status_bits(status_payload)
        x_mm = self._decode_abscoord_mm(x_payload) if x_payload else None
        y_mm = self._decode_abscoord_mm(y_payload) if y_payload else None
        raw_z_mm = self._decode_abscoord_mm(z_payload) if z_payload else None
        if raw_z_mm is not None and self._z_origin_mm is None:
            self._z_origin_mm = raw_z_mm
        z_rel = raw_z_mm - self._z_origin_mm if raw_z_mm is not None and self._z_origin_mm is not None else None
        if z_rel is not None and not self.z_positive_moves_bed_up:
            z_rel = -z_rel
        z_mm = z_rel
        return self.MachineState(status_bits=status_bits, x_mm=x_mm, y_mm=y_mm, z_mm=z_mm)

    def _hardware_z_from_logical(self, z_mm: float | None) -> float | None:
        if z_mm is None:
            return None
        origin = self._z_origin_mm or 0.0
        logical = z_mm if self.z_positive_moves_bed_up else -z_mm
        return origin + logical

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
            return self.MachineState(status_bits=0, x_mm=self.x, y_mm=self.y, z_mm=self.z)

        effective_min_stable_s = min_stable_s
        if self.movement_only:
            effective_min_stable_s = min(min_stable_s, 1.0)

        last_state: Optional[RuidaLaser.MachineState] = None
        baseline_bits: Optional[int] = None
        baseline_pos: dict[str, float] = {}
        stable_counter = 0
        saw_activity = False
        last_pos: dict[str, float] = {}
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
                last_state = state
                part_end = bool(state.status_bits & self.STATUS_BIT_PART_END)
                move_low = bool(state.status_bits & 0x10)  # lower-byte IsMove per EduTech wiki
                run_low = bool(state.status_bits & 0x01)  # lower-byte JobRunning bit on some firmwares
                if attempt <= 2 or attempt % 10 == 1:
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
                positions = {
                    "x": state.x_mm,
                    "y": state.y_mm,
                    "z": state.z_mm,
                }

                for axis, value in positions.items():
                    if value is not None and axis not in baseline_pos:
                        baseline_pos[axis] = value

                prev_pos = last_pos
                last_pos = {axis: value for axis, value in positions.items() if value is not None}

                for axis, value in last_pos.items():
                    prev_val = prev_pos.get(axis)
                    if prev_val is not None and abs(value - prev_val) > pos_tol_mm:
                        saw_activity = True
                for axis, base_val in baseline_pos.items():
                    value = last_pos.get(axis)
                    if value is not None and abs(value - base_val) > pos_tol_mm:
                        saw_activity = True

                stable_match = baseline_bits is None or state.status_bits == baseline_bits
                pos_stable = True
                for axis, prev_val in prev_pos.items():
                    value = last_pos.get(axis)
                    if value is None:
                        continue
                    if abs(value - prev_val) > pos_tol_mm:
                        pos_stable = False
                        break
                if stable_match and pos_stable:
                    if stable_counter == 0:
                        stable_start = time.monotonic()
                    stable_counter += 1
                else:
                    stable_counter = 0
                    stable_start = None

                stable_elapsed = 0.0 if stable_start is None else time.monotonic() - stable_start

                if (
                    require_busy_transition
                    and stable_counter >= stable_target
                    and stable_elapsed >= effective_min_stable_s
                    and not (state.status_bits & self.BUSY_MASK)
                ):
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    if state.z_mm is not None and not self.movement_only:
                        self.z = state.z_mm
                    log.debug(
                        "[RUIDA UDP] Ready via busy transition on attempt %d: status=0x%08X x=%.3f y=%.3f z=%.3f stable_counter=%d stable_elapsed=%.2fs",
                        attempt,
                        state.status_bits,
                        self.x,
                        self.y,
                        self.z,
                        stable_counter,
                        stable_elapsed,
                    )
                    return state

                if not require_busy_transition and stable_counter >= stable_polls and stable_elapsed >= effective_min_stable_s:
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    if state.z_mm is not None and not self.movement_only:
                        self.z = state.z_mm
                    log.debug(
                        "[RUIDA UDP] Ready via idle stability on attempt %d: status=0x%08X x=%.3f y=%.3f z=%.3f stable_counter=%d stable_elapsed=%.2fs",
                        attempt,
                        state.status_bits,
                        self.x,
                        self.y,
                        self.z,
                        stable_counter,
                        stable_elapsed,
                    )
                    return state

                if not require_busy_transition and stable_counter >= stable_target and stable_elapsed >= effective_min_stable_s:
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    if state.z_mm is not None and not self.movement_only:
                        self.z = state.z_mm
                    log.debug(
                        "[RUIDA UDP] Ready via idle stability on attempt %d: status=0x%08X x=%.3f y=%.3f z=%.3f stable_counter=%d stable_elapsed=%.2fs",
                        attempt,
                        state.status_bits,
                        self.x,
                        self.y,
                        self.z,
                        stable_counter,
                        stable_elapsed,
                    )
                    return state

                if (
                    stable_counter >= stable_target
                    and (saw_activity or move_low or run_low or part_end)
                    and stable_elapsed >= effective_min_stable_s
                ):
                    if state.x_mm is not None:
                        self.x = state.x_mm
                    if state.y_mm is not None:
                        self.y = state.y_mm
                    if state.z_mm is not None:
                        self.z = state.z_mm
                    log.debug(
                        "[RUIDA UDP] Ready via stability on attempt %d: status=0x%08X x=%.3f y=%.3f z=%.3f activity_seen=%s stable_counter=%d stable_elapsed=%.2fs",
                        attempt,
                        state.status_bits,
                        self.x,
                        self.y,
                        self.z,
                        saw_activity,
                        stable_counter,
                        stable_elapsed,
                    )
                    return state
                if stable_counter >= stable_target and stable_elapsed < effective_min_stable_s:
                    if attempt <= 2 or attempt % 10 == 1:
                        log.debug(
                            "[RUIDA UDP] Stable but waiting for %.2fs (elapsed %.2fs)",
                            effective_min_stable_s,
                            stable_elapsed,
                        )
            else:
                if last_state and not require_busy_transition:
                    if last_state.x_mm is not None:
                        self.x = last_state.x_mm
                    if last_state.y_mm is not None:
                        self.y = last_state.y_mm
                    if last_state.z_mm is not None:
                        self.z = last_state.z_mm
                    log.debug("[RUIDA UDP] Returning last known state after missing poll (no busy transition required)")
                    return last_state
                if last_state and require_busy_transition and not (last_state.status_bits & self.BUSY_MASK):
                    if last_state.x_mm is not None:
                        self.x = last_state.x_mm
                    if last_state.y_mm is not None:
                        self.y = last_state.y_mm
                    if last_state.z_mm is not None:
                        self.z = last_state.z_mm
                    log.debug("[RUIDA UDP] Returning last known non-busy state after missing poll (busy transition required)")
                    return last_state
            if attempt <= 2 or attempt % 10 == 1:
                log.debug("[RUIDA UDP] Waiting (attempt %d/%d); sleeping %.2fs", attempt, max_attempts, delay_s)
            time.sleep(delay_s)

        raise RuntimeError(f"Ruida controller not ready after {max_attempts} attempts (last={last_state})")

    def _apply_job_z(self, job_z_mm: float | None) -> None:
        """
        Optionally move Z via the panel/jog port before running an RD job.
        """
        if job_z_mm is None:
            return
        state = self._read_machine_state()
        if state and state.z_mm is not None:
            self.z = state.z_mm
        delta = job_z_mm - self.z
        if abs(delta) < 1e-6:
            log.debug("[RUIDA UDP] Z already at target %.3f; skipping panel jog", job_z_mm)
            return
        if not (self.panel_z_step_mm and self.panel_z_step_mm > 0):
            log.debug("[RUIDA UDP] Panel Z jog disabled (panel_z_step_mm=%.3f); leaving Z unchanged", self.panel_z_step_mm)
            self.z = job_z_mm
            return
        steps = int(round(delta / self.panel_z_step_mm))
        if steps == 0:
            log.debug(
                "[RUIDA UDP] Z delta %.3f below step size %.3f; updating tracked Z only",
                delta,
                self.panel_z_step_mm,
            )
            self.z = job_z_mm
            return
        # Panel jog direction: "up" raises the bed (reduces clearance) on most machines.
        direction_up = steps > 0
        if not self.z_positive_moves_bed_up:
            direction_up = not direction_up
        cmd = RuidaPanelInterface.CMD_Z_UP if direction_up else RuidaPanelInterface.CMD_Z_DOWN
        if self._panel_iface is None:
            self._panel_iface = RuidaPanelInterface(
                self.host,
                timeout_s=self.timeout_s,
                dry_run=self.dry_run,
            )
        log.info(
            "[RUIDA UDP] Jogging Z via panel: target=%.3f (hw %.3f) current=%.3f (hw %.3f) step=%.3f count=%d cmd=%s",
            job_z_mm,
            self._hardware_z_from_logical(job_z_mm) or 0.0,
            self.z,
            self._hardware_z_from_logical(self.z) or 0.0,
            self.panel_z_step_mm,
            abs(steps),
            "Z_UP" if direction_up else "Z_DOWN",
        )

        # Calibrate step size on the first move to avoid large over-travel if the configured step is wrong.
        if not self.dry_run and state and state.z_mm is not None:
            try:
                self._panel_iface.send_command(cmd)
                time.sleep(0.05)
                post_state = self._read_machine_state()
            except Exception:
                post_state = None

            if post_state and post_state.z_mm is not None:
                moved = post_state.z_mm - state.z_mm
                if abs(moved) < 1e-6:
                    log.warning("[RUIDA UDP] Panel Z jog produced no movement; aborting further Z jogs")
                    self.z = post_state.z_mm
                    return
                if abs(moved) > self.panel_z_max_step_mm:
                    log.warning(
                        "[RUIDA UDP] Panel Z jog step %.3f exceeds max %.3f; aborting panel jog and relying on RD Z only",
                        moved,
                        self.panel_z_max_step_mm,
                    )
                    self.z = post_state.z_mm
                    return
                if (moved > 0) != (delta > 0):
                    log.warning(
                        "[RUIDA UDP] Panel Z jog moved opposite direction (moved=%.3f delta=%.3f); aborting to avoid collision",
                        moved,
                        delta,
                    )
                    self.z = post_state.z_mm
                    return
                calibrated_step_mm = moved
                remaining_delta = job_z_mm - post_state.z_mm
                adjusted_steps = int(round(remaining_delta / calibrated_step_mm))
                self.z = post_state.z_mm
                if adjusted_steps == 0:
                    log.info("[RUIDA UDP] Z jog completed after calibration step (no further steps needed)")
                    return
                direction_up = adjusted_steps > 0
                if not self.z_positive_moves_bed_up:
                    direction_up = not direction_up
                cmd = RuidaPanelInterface.CMD_Z_UP if direction_up else RuidaPanelInterface.CMD_Z_DOWN
                log.info(
                    "[RUIDA UDP] Calibrated panel Z step %.3fmm; remaining delta %.3f -> steps=%d cmd=%s",
                    calibrated_step_mm,
                    remaining_delta,
                    abs(adjusted_steps),
                    "Z_UP" if direction_up else "Z_DOWN",
                )
                for _ in range(abs(adjusted_steps)):
                    self._panel_iface.send_command(cmd)
                    time.sleep(0.05)
                    poll = self._read_machine_state()
                    if poll and poll.z_mm is not None:
                        self.z = poll.z_mm
                        log.debug("[RUIDA UDP] Panel Z poll during jog: %.3f", self.z)
                        remaining_delta = job_z_mm - self.z
                        if abs(remaining_delta) < self.panel_z_step_mm:
                            log.info("[RUIDA UDP] Z jog reached target within step tolerance; stopping early")
                            break
                post_final = self._read_machine_state()
                if post_final and post_final.z_mm is not None:
                    self.z = post_final.z_mm
                    log.info("[RUIDA UDP] Z after jog (polled): %.3f", self.z)
                return

        for _ in range(abs(steps)):
            self._panel_iface.send_command(cmd)
            time.sleep(0.05)
            poll = self._read_machine_state()
            if poll and poll.z_mm is not None:
                self.z = poll.z_mm
                log.debug("[RUIDA UDP] Panel Z poll during jog: %.3f", self.z)
                remaining_delta = job_z_mm - self.z
                if abs(remaining_delta) < self.panel_z_step_mm:
                    log.info("[RUIDA UDP] Z jog reached target within step tolerance; stopping early")
                    break
        if not self.dry_run:
            post_final = self._read_machine_state()
            if post_final and post_final.z_mm is not None:
                self.z = post_final.z_mm
                log.info("[RUIDA UDP] Z after jog (polled): %.3f", self.z)

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
                self._send_packets(payload)
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
        job_has_power = any(mv.is_cut and mv.power_pct > 0.0 for mv in moves)
        require_busy_transition = require_busy_transition and job_has_power
        job_z_offset_mm = None
        if job_z_mm is not None:
            job_z_offset_mm = job_z_mm if self.z_positive_moves_bed_up else -job_z_mm
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
        payload = build_rd_job(moves, job_z_mm=job_z_offset_mm, air_assist=self.air_assist)
        if self.save_rd_dir:
            self.save_rd_dir.mkdir(parents=True, exist_ok=True)
            self._rd_job_counter += 1
            filename = f"job_{self._rd_job_counter:03d}"
            if job_z_offset_mm is not None:
                filename += f"_z{job_z_offset_mm:+.3f}"
            path = self.save_rd_dir / f"{filename}.rd"
            swizzled = self._swizzle(payload, magic=self.magic)
            path.write_bytes(swizzled)
            log.info("[RUIDA UDP] Saved RD job to %s", path)
        log.info("[RUIDA UDP] Uploading RD job with %d moves%s",
                 len(moves), f" z={job_z_offset_mm:.3f}" if job_z_offset_mm is not None else "")
        if self.dry_run:
            log.debug("[RUIDA UDP DRY RD] %s", payload.hex(" "))
        self._send_packets(payload)
        # Wait for completion; treat PART_END as done.
        self._wait_for_ready(
            require_busy_transition=require_busy_transition,
            min_stable_s=self.min_stable_s,
        )
        if job_z_mm is not None:
            self.z = job_z_mm

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
        job_origin_x = initial_state.x_mm if initial_state and initial_state.x_mm is not None else 0.0
        job_origin_y = initial_state.y_mm if initial_state and initial_state.y_mm is not None else 0.0
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
        origin_z: float | None = job_origin_z
        origin_z_from_command = False
        origin_speed: float | None = None
        park_z: float | None = job_origin_z

        def park_head_before_rotary() -> None:
            if movement_only_mode:
                return
            nonlocal cursor_x, cursor_y
            move_speed = origin_speed or current_speed
            need_xy = not math.isclose(cursor_x, origin_x, abs_tol=1e-9) or not math.isclose(cursor_y, origin_y, abs_tol=1e-9)

            if not need_xy:
                return
            # Keep head at origin in XY; Z adjustments are emitted via RD job payloads.
            if need_xy:
                self.move(x=origin_x, y=origin_y, speed=move_speed)
                cursor_x, cursor_y = origin_x, origin_y

        def flush_block(block_moves: List[RDMove], block_z: float | None) -> None:
            if not block_moves:
                return
            job_z = block_z if block_z is not None else last_set_z
            self.send_rd_job(block_moves, job_z_mm=job_z, require_busy_transition=True)

        block: List[RDMove] = []
        block_z: float | None = None

        try:
            for cmd in commands:
                if cmd.type.name == "ROTATE":
                    if park_speed is None and cmd.speed_mm_s is not None:
                        park_speed = cmd.speed_mm_s
                    flush_block(block, block_z)
                    block = []
                    block_z = None
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
                        if block_z is not None and not math.isclose(cmd.z, block_z, abs_tol=1e-6) and block:
                            flush_block(block, block_z)
                            block = []
                        current_z = cmd.z
                        if not origin_z_from_command:
                            origin_z = current_z
                            origin_z_from_command = True
                        last_set_z = current_z
                        self.z = current_z
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
                        self.z = current_z
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
                        is_cut=not movement_only_mode,
                    ))
                    cursor_x, cursor_y = x, y
                    continue

            flush_block(block, block_z)
        finally:
            if park_z is not None:
                try:
                    needs_park = last_set_z is None or not math.isclose(last_set_z, park_z, abs_tol=1e-6)
                    if needs_park:
                        park_moves = [
                            RDMove(
                                x_mm=cursor_x,
                                y_mm=cursor_y,
                                speed_mm_s=self.z_speed_mm_s,
                                power_pct=0.0,
                                is_cut=False,
                            )
                        ]
                        self.send_rd_job(park_moves, job_z_mm=park_z, require_busy_transition=True)
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
                origin_x = initial_state.x_mm if initial_state and initial_state.x_mm is not None else job_origin_x
                origin_y = initial_state.y_mm if initial_state and initial_state.y_mm is not None else job_origin_y
                if origin_x is not None and origin_y is not None:
                    if not (math.isclose(self.x, origin_x, abs_tol=1e-6) and math.isclose(self.y, origin_y, abs_tol=1e-6)):
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
            self.sock = None

    # Legacy helpers for tests/backward compatibility.
    def _ensure_socket(self) -> None:
        self._udp.dry_run = self.dry_run
        self._udp._ensure_socket()
        self.sock = self._udp.sock
