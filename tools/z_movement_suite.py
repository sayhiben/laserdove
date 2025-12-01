"""
Z movement fidelity suite.

This script probes Z movement through multiple paths (panel jog optional/disabled by default):
  1) Direct UDP axis-Z command (0x80 0x01 + abscoord).
  2) Alternate direct opcode 0x80 0x08 (observed in some stacks).
  3) RD job with embedded job Z.
  4) Optional panel/interface port jog (UDP 50207) for comparison.

Focus/Home opcodes are skipped by default because they proved unsafe on real hardware.
"""

from __future__ import annotations

import argparse
import logging
import socket
import time
from typing import Optional

from laserdove.hardware import rd_builder
from laserdove.hardware.rd_builder import RDMove, build_rd_job, _RDJobBuilder
from laserdove.hardware.ruida_laser import RuidaLaser
from laserdove.hardware.ruida_panel import RuidaPanelInterface
from laserdove.hardware.ruida_common import encode_abscoord_mm, decode_abscoord_mm

log = logging.getLogger("z_movement_suite")


def _poll_raw_z(laser: RuidaLaser) -> Optional[float]:
    """Return absolute hardware Z (mm) by reading controller memory."""
    payload = laser._get_memory_value(laser.MEM_CURRENT_Z, expected_len=5)  # type: ignore[attr-defined]
    if payload is None:
        return None
    return decode_abscoord_mm(payload)


def _poll_z(
    laser: RuidaLaser, label: str, count: int = 3, delay: float = 0.2, *, update_xy: bool = False
) -> Optional[float]:
    """Poll and log Z a few times; return last logical Z. Optionally seed XY from the last poll."""
    last_z = None
    last_state = None
    for i in range(count):
        state = laser._read_machine_state()
        last_state = state or last_state
        raw_z = _poll_raw_z(laser)
        last_z = None if not state else state.z_mm
        log.info(
            "[%s] poll %d: logical_z=%s raw_z=%s x=%s y=%s status=0x%08X",
            label,
            i + 1,
            f"{last_z:.3f}" if last_z is not None else "None",
            f"{raw_z:.3f}" if raw_z is not None else "None",
            f"{state.x_mm:.3f}" if state and state.x_mm is not None else "None",
            f"{state.y_mm:.3f}" if state and state.y_mm is not None else "None",
            state.status_bits if state else -1,
        )
        if i + 1 < count:
            time.sleep(delay)
    if update_xy and last_state:
        if last_state.x_mm is not None:
            laser.x = last_state.x_mm
        if last_state.y_mm is not None:
            laser.y = last_state.y_mm
        log.info("Seeded XY from polls: x=%.3f y=%.3f", laser.x, laser.y)
    return last_z


def _hardware_target(laser: RuidaLaser, logical_z_mm: float) -> float:
    if laser._z_origin_mm is None:
        # Force origin capture via poll.
        _poll_raw_z(laser)
        if laser._z_origin_mm is None:
            laser._z_origin_mm = 0.0
    return laser._z_origin_mm + logical_z_mm


def direct_udp_axis_move(laser: RuidaLaser, logical_target_mm: float) -> None:
    hw_target = _hardware_target(laser, logical_target_mm)
    payload = b"\x80\x01" + encode_abscoord_mm(hw_target)
    log.info("Direct UDP axis Z move: logical=%.3f raw=%.3f", logical_target_mm, hw_target)
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "direct-udp", count=5, delay=0.1)


def direct_udp_axis_move_alt(laser: RuidaLaser, logical_target_mm: float) -> None:
    """Try alternate opcode 0x80 0x08 reported by some stacks."""
    hw_target = _hardware_target(laser, logical_target_mm)
    payload = b"\x80\x08" + encode_abscoord_mm(hw_target)
    log.info(
        "Direct UDP axis Z move (alt opcode 0x80 0x08): logical=%.3f raw=%.3f",
        logical_target_mm,
        hw_target,
    )
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "direct-udp-alt", count=5, delay=0.1)


def rd_job_move(laser: RuidaLaser, logical_target_mm: float) -> None:
    moves = [
        RDMove(
            x_mm=laser.x,
            y_mm=laser.y,
            speed_mm_s=laser.z_speed_mm_s,
            power_pct=0.0,
            is_cut=False,
        )
    ]
    payload = build_rd_job(moves, job_z_mm=logical_target_mm, air_assist=laser.air_assist)
    hw_target = _hardware_target(laser, logical_target_mm)
    log.info(
        "RD job Z move via 0x80 0x03 offset: logical=%.3f raw=%.3f", logical_target_mm, hw_target
    )
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "rd-job", count=10, delay=0.2)


def rd_job_move_alt_opcode(laser: RuidaLaser, logical_target_mm: float) -> None:
    """
    RD job with Z using alternate opcode 0x80 0x08 instead of 0x80 0x01.
    """
    hw_target = _hardware_target(laser, logical_target_mm)
    moves = [
        RDMove(
            x_mm=laser.x,
            y_mm=laser.y,
            speed_mm_s=laser.z_speed_mm_s,
            power_pct=0.0,
            is_cut=False,
        )
    ]
    builder = _RDJobBuilder()
    paths, bbox = rd_builder._moves_to_paths(moves)
    layer = rd_builder._Layer(
        paths=paths, bbox=bbox, speed=[laser.z_speed_mm_s, laser.z_speed_mm_s], power=[0.0, 0.0]
    )
    builder._globalbbox = bbox
    header = builder.header([layer], filename="ALTZ")
    body = builder.body([layer], job_z_mm=None, air_assist=laser.air_assist)
    # inject alt opcode: 0x80 0x08 + encoded Z
    body = body + bytes([0x80, 0x08]) + builder.encode_number(hw_target)
    trailer = builder.trailer((0.0, 0.0))
    payload = header + body + trailer
    log.info("RD job Z move (alt 0x80 0x08): logical=%.3f raw=%.3f", logical_target_mm, hw_target)
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "rd-job-alt", count=10, delay=0.2)


def rd_job_move_z_only(
    laser: RuidaLaser, logical_target_mm: float, *, rapid_options: int = 0x02
) -> None:
    """
    RD job containing only a rapid Z command (D9 02) with no XY paths to avoid unintended XY travel.
    """
    hw_target = _hardware_target(laser, logical_target_mm)
    layer = rd_builder._Layer(
        paths=[],
        bbox=[[laser.x, laser.y], [laser.x, laser.y]],
        speed=[laser.z_speed_mm_s, laser.z_speed_mm_s],
        power=[0.0, 0.0],
    )
    builder = _RDJobBuilder()
    builder._globalbbox = layer.bbox
    header = builder.header([layer], filename="ZONLY")
    body = builder.body([layer], job_z_mm=None, air_assist=laser.air_assist)
    # Inject rapid Z: D9 02 <options> <abscoord>
    body = body + bytes([0xD9, 0x02, rapid_options & 0xFF]) + builder.encode_number(hw_target)
    trailer = builder.trailer((0.0, 0.0))
    payload = header + body + trailer
    log.info(
        "RD job Z-only rapid: logical=%.3f raw=%.3f options=0x%02X",
        logical_target_mm,
        hw_target,
        rapid_options,
    )
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "rd-job-zonly", count=10, delay=0.2)


def encode_abscoord_mm_signed(value_mm: float) -> bytes:
    """
    Encode a signed coordinate (mm) into the 5x7-bit field used by Ruida.
    LightBurn writes 0x80 03 as a signed offset in microns, no origin bias.
    """
    microns = int(round(value_mm * 1000.0))
    if microns < 0:
        microns &= 0xFFFFFFFF  # two's complement
    res = []
    for _ in range(5):
        res.append(microns & 0x7F)
        microns >>= 7
    res.reverse()
    return bytes(res)


def direct_udp_axis_move_8003(laser: RuidaLaser, logical_target_mm: float) -> None:
    """
    Send 0x80 0x03 with signed absolute coordinate (LightBurn-style Z offset).
    """
    # Send as signed offset only (no origin bias) to mirror LightBurn behavior.
    payload = b"\x80\x03" + encode_abscoord_mm_signed(logical_target_mm)
    log.info("Direct UDP 0x80 0x03 Z (signed offset): logical=%.3f", logical_target_mm)
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "direct-udp-8003", count=5, delay=0.1)


def rd_job_move_8003(laser: RuidaLaser, logical_target_mm: float) -> None:
    """
    RD job containing only 0x80 0x03 signed absolute Z (LightBurn-style), no XY paths.
    """
    layer = rd_builder._Layer(
        paths=[],
        bbox=[[laser.x, laser.y], [laser.x, laser.y]],
        speed=[laser.z_speed_mm_s, laser.z_speed_mm_s],
        power=[0.0, 0.0],
    )
    builder = _RDJobBuilder()
    builder._globalbbox = layer.bbox
    header = builder.header([layer], filename="Z8003")
    body = builder.body([layer], job_z_mm=None, air_assist=laser.air_assist)
    # Send as signed offset only (no origin bias) to mirror LightBurn behavior.
    body = body + b"\x80\x03" + encode_abscoord_mm_signed(logical_target_mm)
    trailer = builder.trailer((0.0, 0.0))
    payload = header + body + trailer
    log.info("RD job 0x80 0x03 Z-only (signed offset): logical=%.3f", logical_target_mm)
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "rd-job-8003", count=10, delay=0.2)


def direct_udp_rapid_z(laser: RuidaLaser, logical_target_mm: float, *, options: int = 0x00) -> None:
    """
    Rapid/default Z opcode observed in other stacks: D9 02 <options> <abscoord>.
    options=0x02 observed in MeerK40t for "no light/no origin"; 0 uses controller default.
    """
    hw_target = _hardware_target(laser, logical_target_mm)
    payload = bytes([0xD9, 0x02, options & 0xFF]) + encode_abscoord_mm(hw_target)
    log.info(
        "Direct UDP rapid Z (0xD9 0x02 opt=0x%02X): logical=%.3f raw=%.3f",
        options,
        logical_target_mm,
        hw_target,
    )
    laser._send_packets(payload)  # type: ignore[attr-defined]
    _poll_z(laser, "direct-udp-rapid", count=5, delay=0.1)


def panel_jog(laser: RuidaLaser, logical_delta_mm: float, max_steps: int = 5) -> None:
    if logical_delta_mm == 0:
        log.info("Panel jog skipped (delta 0)")
        return
    iface = RuidaPanelInterface(laser.host, timeout_s=laser.timeout_s, dry_run=laser.dry_run)
    direction_up = logical_delta_mm > 0
    if not laser.z_positive_moves_bed_up:
        direction_up = not direction_up
    cmd = RuidaPanelInterface.CMD_Z_UP if direction_up else RuidaPanelInterface.CMD_Z_DOWN
    log.info(
        "Panel jog: delta=%.3f cmd=%s steps=%d",
        logical_delta_mm,
        "Z_UP" if direction_up else "Z_DOWN",
        max_steps,
    )
    _poll_z(laser, "panel-jog-before", count=3, delay=0.1)
    for idx in range(max_steps):
        iface.send_command(cmd)
        time.sleep(0.05)
        _poll_z(laser, f"panel-jog-step{idx + 1}", count=2, delay=0.05)


def panel_main_jog(laser: RuidaLaser, logical_delta_mm: float, *, hold_s: float = 0.3) -> None:
    """
    Simulate front-panel Z keydown/keyup on the main control port (50200).
    """
    if logical_delta_mm == 0:
        log.info("Panel-main jog skipped (delta 0)")
        return
    direction_up = logical_delta_mm > 0
    if not laser.z_positive_moves_bed_up:
        direction_up = not direction_up
    down_cmd = b"\xd8\x24" if direction_up else b"\xd8\x25"
    up_cmd = b"\xd8\x34" if direction_up else b"\xd8\x35"
    log.info(
        "Panel-main jog: delta=%.3f direction=%s hold=%.2fs (keydown %s / keyup %s)",
        logical_delta_mm,
        "Z_UP" if direction_up else "Z_DOWN",
        hold_s,
        down_cmd.hex(" "),
        up_cmd.hex(" "),
    )
    _poll_z(laser, "panel-main-before", count=3, delay=0.1)
    laser._send_packets(down_cmd)  # type: ignore[attr-defined]
    time.sleep(max(0.05, hold_s))
    laser._send_packets(up_cmd)  # type: ignore[attr-defined]
    _poll_z(laser, "panel-main-after", count=5, delay=0.1)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe Ruida Z movement fidelity via multiple paths."
    )
    parser.add_argument("--host", required=True, help="Ruida controller host/IP")
    parser.add_argument(
        "--mode",
        choices=[
            "rd",
            "rd-alt",
            "rd-zonly",
            "rd-8003",
            "udp",
            "udp-alt",
            "udp-rapid",
            "udp-8003",
            "panel",
            "panel-main",
        ],
        default="rd",
        help="Single test to run: rd=standard RD Z, rd-alt=RD with 0x80 0x08, rd-zonly=RD with only rapid D9 02 Z and no XY, rd-8003=RD with only 0x80 0x03 signed Z and no XY, udp=direct 0x80 0x01, udp-alt=direct 0x80 0x08, udp-rapid=D9 02 rapid Z, udp-8003=direct 0x80 0x03 signed Z, panel=interface jog, panel-main=keydown/keyup on control port",
    )
    parser.add_argument(
        "--target-z-mm", type=float, default=0.0, help="Logical Z target relative to startup (mm)"
    )
    parser.add_argument("--panel-steps", type=int, default=3, help="Max panel jog steps to issue")
    parser.add_argument(
        "--movement-only",
        action="store_true",
        default=True,
        help="Force power=0 in RD jobs (default)",
    )
    parser.add_argument("--no-movement-only", dest="movement_only", action="store_false")
    parser.add_argument(
        "--panel-main-hold-s",
        type=float,
        default=0.3,
        help="Hold time for panel-main keydown before keyup (s)",
    )
    parser.add_argument(
        "--rapid-options",
        type=lambda x: int(x, 0),
        default=0x02,
        help="Options byte for rapid Z (D9 02) command",
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="Socket timeout (s)")
    parser.add_argument("--dry-run", action="store_true", help="Log packets without sending")
    parser.add_argument(
        "--relative-delta-mm",
        type=float,
        help="Relative Z delta to test via read+absolute (clamped)",
    )
    parser.add_argument(
        "--max-travel-mm",
        type=float,
        default=50.0,
        help="Clamp absolute/relative target to +/- this travel (mm)",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    try:
        socket.getaddrinfo(args.host, None)
    except socket.gaierror as exc:
        log.error("Host %s not resolvable: %s", args.host, exc)
        raise SystemExit(2)

    laser = RuidaLaser(
        args.host,
        timeout_s=args.timeout,
        dry_run=args.dry_run,
        movement_only=args.movement_only,
    )

    _poll_z(laser, "startup", count=3, delay=0.1, update_xy=True)

    target = args.target_z_mm
    target_source = "target argument"
    if args.relative_delta_mm is not None:
        state = laser._read_machine_state()
        logical_now = state.z_mm if state and state.z_mm is not None else 0.0
        target = logical_now + args.relative_delta_mm
        target_source = "relative delta"

    max_travel = max(args.max_travel_mm, 0.0)
    unclamped = target
    target = max(-max_travel, min(max_travel, target))
    if target != unclamped:
        log.warning(
            "Clamped %s from %.3f to %.3f (max travel +/-%.1f mm)",
            target_source,
            unclamped,
            target,
            max_travel,
        )
    else:
        log.info("Using %s: %.3f mm (max travel +/-%.1f mm)", target_source, target, max_travel)

    try:
        if args.mode == "rd":
            rd_job_move(laser, target)
        elif args.mode == "rd-alt":
            rd_job_move_alt_opcode(laser, target)
        elif args.mode == "rd-zonly":
            rd_job_move_z_only(laser, target, rapid_options=args.rapid_options)
        elif args.mode == "rd-8003":
            rd_job_move_8003(laser, target)
        elif args.mode == "udp":
            direct_udp_axis_move(laser, target)
        elif args.mode == "udp-alt":
            direct_udp_axis_move_alt(laser, target)
        elif args.mode == "udp-rapid":
            direct_udp_rapid_z(laser, target, options=args.rapid_options)
        elif args.mode == "udp-8003":
            direct_udp_axis_move_8003(laser, target)
        elif args.mode == "panel":
            state = laser._read_machine_state()
            logical_now = state.z_mm if state and state.z_mm is not None else 0.0
            delta = target - logical_now
            panel_jog(laser, delta, max_steps=args.panel_steps)
        elif args.mode == "panel-main":
            state = laser._read_machine_state()
            logical_now = state.z_mm if state and state.z_mm is not None else 0.0
            delta = target - logical_now
            panel_main_jog(laser, delta, hold_s=args.panel_main_hold_s)
        else:
            raise ValueError(f"Unknown mode {args.mode}")
    except Exception:
        log.exception("Z test (%s) failed", args.mode)


if __name__ == "__main__":
    main()
