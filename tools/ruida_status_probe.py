#!/usr/bin/env python3
"""
Ruida status probe and self-test helper.

This script polls MEM_MACHINE_STATUS via the same transport logic we use in RuidaLaser,
then runs a small suite of movement-only actions (no cutting) while logging status
before, during, and after each action. Use it to map status bits on your controller.

Actions performed (movement-only, power=0):
 - Baseline polling
 - Move along X
 - Move along Y
 - Z move (bed up/down depending on your config)
 - Small movement-only RD job (air assist ON)
 - Small movement-only RD job (air assist OFF)

Run with:
  python -m tools.ruida_status_probe --host 10.0.3.3
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Callable, List

try:
    from laserdove.hardware.rd_builder import RDMove, build_rd_job
    from laserdove.hardware.ruida_laser import RuidaLaser
    from laserdove.logging_utils import setup_logging
    from tools.rd_parser import RuidaParser
except ImportError:
    # Allow running directly from the repository without editable install.
    REPO_ROOT = Path(__file__).resolve().parent.parent
    SRC_ROOT = REPO_ROOT / "src"
    for path in (SRC_ROOT, REPO_ROOT):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    from laserdove.hardware.rd_builder import RDMove, build_rd_job
    from laserdove.hardware.ruida_laser import RuidaLaser
    from laserdove.logging_utils import setup_logging
    from tools.rd_parser import RuidaParser

LOG = logging.getLogger("ruida_status_probe")


def decode_bits(raw: int) -> str:
    parts = [
        f"raw=0x{raw:08X}",
        f"busy_mask={(raw & RuidaLaser.STATUS_BIT_MOVING) != 0 or (raw & RuidaLaser.STATUS_BIT_JOB_RUNNING) != 0}",
        f"part_end={(raw & RuidaLaser.STATUS_BIT_PART_END) != 0}",
        f"is_move_low={(raw & 0x10) != 0}",
        f"job_run_low={(raw & 0x01) != 0}",
        f"bit_2={(raw & 0x04) != 0}",
        f"bit_3={(raw & 0x08) != 0}",
        f"bit_10={(raw & 0x400) != 0}",
        f"bit_11={(raw & 0x800) != 0}",
    ]
    return " ".join(parts)


def poll_status_once(laser: RuidaLaser, label: str) -> bool:
    state = laser._read_machine_state()
    if state:
        LOG.info(
            "[%s] status %s x=%.3f y=%.3f",
            label,
            decode_bits(state.status_bits),
            state.x_mm,
            state.y_mm,
        )
        return True
    LOG.info("[%s] status: timeout/none", label)
    return False


def run_with_capture(
    poll_laser: RuidaLaser,
    label: str,
    action: Callable[[], None],
    *,
    interval: float,
    polls_after: int = 5,
) -> None:
    LOG.info("=== %s START ===", label)
    poll_status_once(poll_laser, f"{label}-before")

    stop = threading.Event()

    def capture():
        idx = 0
        while not stop.is_set():
            idx += 1
            poll_status_once(poll_laser, f"{label}-during#{idx}")
            time.sleep(interval)

    t = threading.Thread(target=capture, daemon=True)
    t.start()
    try:
        action()
    except Exception as exc:
        LOG.error("[%s] action raised: %s", label, exc)
    finally:
        stop.set()
        t.join(timeout=interval * 2)

    for i in range(polls_after):
        poll_status_once(poll_laser, f"{label}-after#{i + 1}")
        time.sleep(interval)
    LOG.info("=== %s END ===", label)


def log_rd_summary(tag: str, moves: List[RDMove], job_z_mm: float | None) -> None:
    if not moves:
        LOG.info("[%s] empty RD job", tag)
        return

    payload = build_rd_job(moves, job_z_mm=job_z_mm, filename=f"probe-{tag}", air_assist=True)
    parser = RuidaParser(buf=payload)
    # Populate parser internals without dumping tokens.
    parser.decode(debug=False)

    bbox = parser._bbox  # quick summary only; tooling-level script
    width = bbox[2] - bbox[0] if bbox[2] > -10e8 else 0.0
    height = bbox[3] - bbox[1] if bbox[3] > -10e8 else 0.0
    LOG.info(
        "[%s] RD job moves=%d bbox=[%.3f, %.3f]–[%.3f, %.3f] size=%.3fx%.3fmm z=%s",
        tag,
        len(moves),
        bbox[0],
        bbox[1],
        bbox[2],
        bbox[3],
        width,
        height,
        f"{job_z_mm:.3f}" if job_z_mm is not None else "n/a",
    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Poll Ruida status and run movement-only actions to map status bits."
    )
    ap.add_argument("--host", required=True, help="Ruida controller host/IP")
    ap.add_argument(
        "--port", type=int, default=50200, help="Ruida controller UDP port (default 50200)"
    )
    ap.add_argument(
        "--source-port", type=int, default=40200, help="Local UDP source port (default 40200)"
    )
    ap.add_argument(
        "--status-source-port",
        type=int,
        help="Local UDP source port for status polling (default source-port+1 when dual-socket)",
    )
    ap.add_argument(
        "--dual-socket",
        dest="dual_socket",
        action="store_true",
        help="Use a dedicated socket for status polling to avoid reply mix-ups",
    )
    ap.add_argument(
        "--single-socket",
        dest="dual_socket",
        action="store_false",
        help="Force reuse of a single socket for both actions and polling (default)",
    )
    ap.set_defaults(dual_socket=False)
    ap.add_argument(
        "--run-actions",
        action="store_true",
        help="Run the movement-only actions (travel at power=0). Omit to poll status only.",
    )
    ap.add_argument("--log-level", default="INFO", help="Log level (default INFO)")
    ap.add_argument("--timeout-s", type=float, default=3.0, help="UDP timeout seconds")
    ap.add_argument(
        "--interval", type=float, default=0.5, help="Seconds between status polls during actions"
    )
    ap.add_argument("--move-dist-mm", type=float, default=10.0, help="Distance for X/Y moves (mm)")
    ap.add_argument("--z-move-mm", type=float, default=1.0, help="Z move magnitude (mm)")
    ap.add_argument("--polls-after", type=int, default=5, help="Status polls after each action")
    ap.add_argument(
        "--baseline-polls", type=int, default=1, help="Status polls before running actions"
    )
    ap.add_argument(
        "--magic", type=lambda x: int(x, 0), default=0x88, help="Swizzle magic (default 0x88)"
    )
    args = ap.parse_args()

    setup_logging(args.log_level)
    use_dual = args.dual_socket

    status_source_port = (
        args.status_source_port
        if args.status_source_port is not None
        else (args.source_port + 1 if use_dual else args.source_port)
    )

    action_laser = RuidaLaser(
        host=args.host,
        port=args.port,
        source_port=args.source_port,
        timeout_s=args.timeout_s,
        dry_run=False,
        movement_only=True,  # keep power at 0
        magic=args.magic,
        air_assist=True,
    )

    poll_laser = action_laser
    if use_dual:
        poll_laser = RuidaLaser(
            host=args.host,
            port=args.port,
            source_port=status_source_port,
            timeout_s=args.timeout_s,
            dry_run=False,
            movement_only=True,
            magic=args.magic,
            air_assist=True,
        )
    LOG.info(
        "Probing host=%s port=%d source_port=%d status_port=%d dual_socket=%s",
        args.host,
        args.port,
        args.source_port,
        status_source_port,
        use_dual,
    )

    # Baseline polling only
    baseline_results = []
    for i in range(args.baseline_polls):
        baseline_results.append(poll_status_once(poll_laser, f"baseline#{i + 1}"))
        time.sleep(args.interval)

    if use_dual and baseline_results and not any(baseline_results):
        LOG.warning(
            "Dual-socket baseline polling timed out; falling back to single-socket on source_port=%d",
            args.source_port,
        )
        poll_laser = action_laser
        use_dual = False
        for i in range(args.baseline_polls):
            poll_status_once(poll_laser, f"baseline-single#{i + 1}")
            time.sleep(args.interval)

    if not args.run_actions:
        LOG.info("Actions skipped (pass --run-actions to enable movement-only probes).")
        return

    actions: List[tuple[str, Callable[[], None]]] = []

    # X move
    def move_x():
        mv = [
            RDMove(0.0, 0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False),
            RDMove(args.move_dist_mm, 0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False),
        ]
        log_rd_summary("move-x", mv, None)
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    actions.append(("move-x", move_x))

    # Y move
    def move_y():
        mv = [
            RDMove(0.0, 0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False),
            RDMove(0.0, args.move_dist_mm, speed_mm_s=50.0, power_pct=0.0, is_cut=False),
        ]
        log_rd_summary("move-y", mv, None)
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    actions.append(("move-y", move_y))

    # Z move
    def move_z():
        mv = [RDMove(0.0, 0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False)]
        log_rd_summary("move-z", mv, args.z_move_mm)
        action_laser.send_rd_job(mv, job_z_mm=args.z_move_mm, require_busy_transition=False)

    actions.append(("move-z", move_z))

    # Air assist ON small job
    def air_on_job():
        mv = [
            RDMove(0.0, 0.0, speed_mm_s=30.0, power_pct=0.0, is_cut=False),
            RDMove(args.move_dist_mm, 0.0, speed_mm_s=30.0, power_pct=0.0, is_cut=False),
        ]
        log_rd_summary("air-assist-on-job", mv, None)
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    actions.append(("air-assist-on-job", air_on_job))

    # Air assist OFF small job (omit CA 01 13)
    def air_off_job():
        mv = [
            RDMove(0.0, 0.0, speed_mm_s=30.0, power_pct=0.0, is_cut=False),
            RDMove(args.move_dist_mm, 0.0, speed_mm_s=30.0, power_pct=0.0, is_cut=False),
        ]
        log_rd_summary("air-assist-off-job", mv, None)
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    actions.append(("air-assist-off-job", air_off_job))

    # Jog-style moves (single-axis jog commands via send_rd_job travel)
    def jog_x_positive():
        mv = [RDMove(args.move_dist_mm, 0.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False)]
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    def jog_x_negative():
        mv = [RDMove(-args.move_dist_mm, 0.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False)]
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    def jog_y_positive():
        mv = [RDMove(0.0, args.move_dist_mm, speed_mm_s=100.0, power_pct=0.0, is_cut=False)]
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    def jog_y_negative():
        mv = [RDMove(0.0, -args.move_dist_mm, speed_mm_s=100.0, power_pct=0.0, is_cut=False)]
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    # Small square travel-only path to see if motion-only sequences toggle bits.
    def square_travel():
        d = args.move_dist_mm
        mv = [
            RDMove(0.0, 0.0, speed_mm_s=80.0, power_pct=0.0, is_cut=False),
            RDMove(d, 0.0, speed_mm_s=80.0, power_pct=0.0, is_cut=False),
            RDMove(d, d, speed_mm_s=80.0, power_pct=0.0, is_cut=False),
            RDMove(0.0, d, speed_mm_s=80.0, power_pct=0.0, is_cut=False),
            RDMove(0.0, 0.0, speed_mm_s=80.0, power_pct=0.0, is_cut=False),
        ]
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    actions.extend(
        [
            ("jog-x-positive", jog_x_positive),
            ("jog-x-negative", jog_x_negative),
            ("jog-y-positive", jog_y_positive),
            ("jog-y-negative", jog_y_negative),
            ("square-travel", square_travel),
        ]
    )

    # Home-like move back to origin (travel-only)
    def home_travel():
        mv = [RDMove(0.0, 0.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False)]
        action_laser.send_rd_job(mv, job_z_mm=0.0, require_busy_transition=False)

    actions.append(("home-travel", home_travel))

    # Upload-only RD (send file but don't run) by using RuidaLaser transport directly with no job run flag.
    # Since our send_rd_job auto-runs, we approximate "upload" as a tiny travel-only job.
    def upload_only():
        mv = [RDMove(0.0, 0.0, speed_mm_s=10.0, power_pct=0.0, is_cut=False)]
        action_laser.send_rd_job(mv, job_z_mm=None, require_busy_transition=False)

    actions.append(("upload-only-travel", upload_only))

    for label, fn in actions:
        run_with_capture(
            poll_laser, label, fn, interval=args.interval, polls_after=args.polls_after
        )

    print("Done. Review logs above for status transitions.")


if __name__ == "__main__":
    main()
