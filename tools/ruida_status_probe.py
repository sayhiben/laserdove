#!/usr/bin/env python3
"""
Ruida status probe and self-test helper.

This script polls MEM_MACHINE_STATUS and optional X/Y positions, runs a small
movement-only RD job (optional), and logs every distinct status word observed.

Usage examples:
  python -m tools.ruida_status_probe --host 10.0.3.3 --polls-before 5 --send-job --polls-after 20
  python -m tools.ruida_status_probe --host 10.0.3.3 --no-send-job --polls-before 20
"""

from __future__ import annotations

import argparse
import socket
import time
from typing import Dict, List, Set, Tuple

from laserdove.hardware.rd_builder import RDMove, build_rd_job
from laserdove.hardware.ruida_common import decode_status_bits, swizzle


MEM_MACHINE_STATUS = b"\x04\x00"
MEM_CURRENT_X = b"\x04\x21"
MEM_CURRENT_Y = b"\x04\x31"


def checksum(data: bytes) -> bytes:
    cs = sum(data) & 0xFFFF
    return cs.to_bytes(2, byteorder="big")


def _chunk_and_send(sock: socket.socket, addr: Tuple[str, int], payload: bytes, timeout_s: float, magic: int) -> None:
    swizzled = swizzle(payload, magic=magic)
    mtu = 1470
    chunks: List[bytes] = []
    start = 0
    while start < len(swizzled):
        end = min(start + mtu, len(swizzled))
        chunk = swizzled[start:end]
        chunk = checksum(chunk) + chunk
        chunks.append(chunk)
        start = end

    for idx, chunk in enumerate(chunks):
        retry = 0
        while True:
            sock.sendto(chunk, addr)
            sock.settimeout(timeout_s)
            try:
                data, _ = sock.recvfrom(8)
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
            if data[0] in {0xC6, 0xCC}:
                break
            if data[0] in {0x46, 0xCF} and idx == 0:
                retry += 1
                if retry > 3:
                    raise RuntimeError("UDP NACK on first packet")
                continue
            raise RuntimeError(f"UDP unexpected response {data.hex()}")


def get_setting(sock: socket.socket, addr: Tuple[str, int], mem_addr: bytes, timeout_s: float, magic: int) -> bytes | None:
    payload = bytes([0xDA, 0x00]) + mem_addr
    pkt = checksum(swizzle(payload, magic=magic)) + swizzle(payload, magic=magic)
    sock.settimeout(timeout_s)
    try:
        sock.sendto(pkt, addr)
        data, _ = sock.recvfrom(1024)
    except socket.timeout:
        return None
    if not data:
        return None
    if len(data) > 2 and data[:2] == checksum(data[2:]):
        data = data[2:]
    return data


def decode_status(raw: int) -> Dict[str, bool | int]:
    return {
        "raw_hex": f"0x{raw:08X}",
        "busy_mask": bool(raw & 0x01000001),
        "part_end": bool(raw & 0x00000002),
        "is_move_low": bool(raw & 0x10),
        "job_run_low": bool(raw & 0x01),
        "bit_2": bool(raw & 0x04),
        "bit_3": bool(raw & 0x08),
        "bit_10": bool(raw & 0x400),
        "bit_11": bool(raw & 0x800),
    }


def poll_status(sock: socket.socket, addr: Tuple[str, int], count: int, interval: float, timeout_s: float, magic: int, label: str, seen: Set[int], counts: Dict[int, int]) -> None:
    print(f"--- Polling status ({label}) x{count} ---")
    for i in range(1, count + 1):
        status_reply = get_setting(sock, addr, MEM_MACHINE_STATUS, timeout_s, magic)
        x_reply = get_setting(sock, addr, MEM_CURRENT_X, timeout_s, magic)
        y_reply = get_setting(sock, addr, MEM_CURRENT_Y, timeout_s, magic)

        if status_reply is None:
            print(f"[{label} #{i}] status: timeout")
        else:
            if status_reply.startswith(b"\xDA\x01" + MEM_MACHINE_STATUS):
                payload = status_reply[4:]
            elif status_reply.startswith(MEM_MACHINE_STATUS):
                payload = status_reply[2:]
            else:
                payload = status_reply
            raw = decode_status_bits(payload)
            counts[raw] = counts.get(raw, 0) + 1
            bits = decode_status(raw)
            new_marker = " NEW" if raw not in seen else ""
            seen.add(raw)
            print(f"[{label} #{i}] status {bits['raw_hex']}{new_marker}: {bits}")

        if x_reply and y_reply:
            try:
                x_mm = int.from_bytes(x_reply[-5:], "big") / 1000.0
                y_mm = int.from_bytes(y_reply[-5:], "big") / 1000.0
                print(f"    pos x={x_mm:.3f} y={y_mm:.3f}")
            except Exception:
                pass

        time.sleep(interval)


def send_test_job(sock: socket.socket, addr: Tuple[str, int], timeout_s: float, magic: int, job_len_mm: float, job_power_pct: float, job_z_mm: float, movement_only: bool) -> None:
    # Build a tiny two-segment job.
    moves = [
        RDMove(0.0, 0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False),
        RDMove(job_len_mm, 0.0, speed_mm_s=20.0, power_pct=job_power_pct, is_cut=True),
    ]
    payload = build_rd_job(moves, job_z_mm=job_z_mm, air_assist=True)
    if movement_only:
        # Ensure power is 0 in movement-only mode; RD builder already suppresses zero-power cuts.
        pass
    print(f"Sending test RD job (len={job_len_mm}mm, power={job_power_pct}%, z={job_z_mm})")
    _chunk_and_send(sock, addr, payload, timeout_s, magic)


def main() -> None:
    ap = argparse.ArgumentParser(description="Poll Ruida machine status and optionally run a small test RD job.")
    ap.add_argument("--host", required=True, help="Ruida controller host/IP")
    ap.add_argument("--port", type=int, default=50200, help="Ruida controller UDP port (default 50200)")
    ap.add_argument("--magic", type=lambda x: int(x, 0), default=0x88, help="Swizzle magic (default 0x88)")
    ap.add_argument("--interval", type=float, default=0.5, help="Seconds between polls")
    ap.add_argument("--polls-before", type=int, default=5, help="Polls before sending test job")
    ap.add_argument("--polls-after", type=int, default=20, help="Polls after sending test job")
    ap.add_argument("--send-job", dest="send_job", action="store_true", help="Send a small test RD job")
    ap.add_argument("--no-send-job", dest="send_job", action="store_false", help="Skip sending a test RD job")
    ap.add_argument("--job-len-mm", type=float, default=10.0, help="Test job length in mm")
    ap.add_argument("--job-power-pct", type=float, default=0.0, help="Test job power percent (0 to stay movement-only)")
    ap.add_argument("--job-z-mm", type=float, default=0.0, help="Test job Z height (mm)")
    ap.add_argument("--timeout-s", type=float, default=3.0, help="UDP timeout seconds")
    ap.set_defaults(send_job=True)
    args = ap.parse_args()

    addr = (args.host, args.port)
    seen: Set[int] = set()
    counts: Dict[int, int] = {}

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        poll_status(sock, addr, args.polls_before, args.interval, args.timeout_s, args.magic, "baseline", seen, counts)

        if args.send_job:
            send_test_job(sock, addr, args.timeout_s, args.magic, args.job_len_mm, args.job_power_pct, args.job_z_mm, movement_only=(args.job_power_pct <= 0.0))
            poll_status(sock, addr, args.polls_after, args.interval, args.timeout_s, args.magic, "post-job", seen, counts)

    print("Summary of observed status values:")
    for raw, c in counts.items():
        bits = decode_status(raw)
        print(f"  {bits['raw_hex']} seen {c} times -> {bits}")


if __name__ == "__main__":
    main()
