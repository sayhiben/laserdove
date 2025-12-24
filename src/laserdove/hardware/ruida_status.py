from __future__ import annotations

import logging
import time
from typing import Optional, TYPE_CHECKING

from .ruida_common import decode_abscoord_mm, decode_status_bits

if TYPE_CHECKING:
    from .ruida_laser import RuidaLaser

log = logging.getLogger("laserdove.hardware.ruida_laser")


class RuidaStatusMixin:
    """Mixin providing status polling helpers."""

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

    def _read_machine_state(
        self, *, read_positions: bool = True
    ) -> Optional["RuidaLaser.MachineState"]:
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
            log.info(
                "[RUIDA UDP] Captured Z origin from controller: raw=%.3fmm (z+ moves bed %s)",
                raw_z_mm,
                "up" if self.z_positive_moves_bed_up else "down",
            )
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
    ) -> "RuidaLaser.MachineState":
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

        last_state: Optional["RuidaLaser.MachineState"] = None
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
