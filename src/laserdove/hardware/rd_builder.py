from __future__ import annotations

"""
Minimal RD job builder for straight-line moves/cuts.

This is intentionally tiny and only covers what the current planner emits:
  - Absolute XY moves and cuts
  - Single layer with speed/power
  - EOF terminator

Notes:
- This is NOT a full RD generator; it only builds enough to jog/run simple paths.
- Z moves are not encoded here; set Z separately before sending the job.
"""

from dataclasses import dataclass
from typing import List, Tuple


def _encode_abscoord_mm(value_mm: float) -> bytes:
    microns = int(round(value_mm * 1000.0))
    res = []
    for _ in range(5):
        res.append(microns & 0x7F)
        microns >>= 7
    res.reverse()
    return bytes(res)


def _encode_power_pct(power_pct: float) -> bytes:
    clamped = max(0.0, min(100.0, power_pct))
    raw = int(round(clamped * (0x3FFF / 100.0)))
    return bytes([(raw >> 7) & 0x7F, raw & 0x7F])


@dataclass
class RDMove:
    x_mm: float
    y_mm: float
    speed_mm_s: float
    power_pct: float
    is_cut: bool


def build_rd_job(moves: List[RDMove], job_z_mm: float | None = None) -> bytes:
    """
    Build a minimal RD job buffer for a sequence of moves/cuts.
    """
    if not moves:
        return b""

    # Header: very small job, single layer. This is a simplified header seen in practice.
    header = bytes([
        0x55, 0xAA,             # magic
        0x00, 0x00, 0x00, 0x00, # file length placeholder (filled later)
        0x00, 0x00, 0x00, 0x00, # layer count etc. minimal
    ])

    body = bytearray()

    # Optional Z move first (absolute)
    if job_z_mm is not None:
        body.extend(bytes([0x80, 0x01]))  # AXIS_Z_MOVE (abs)
        body.extend(_encode_abscoord_mm(job_z_mm))

    # Set speed/power once at start
    first = moves[0]
    body.extend(bytes([0xC9, 0x02]))  # set speed
    body.extend(_encode_abscoord_mm(first.speed_mm_s))
    body.extend(bytes([0xC7]))        # set power
    body.extend(_encode_power_pct(first.power_pct))

    for mv in moves:
        if mv.speed_mm_s != first.speed_mm_s:
            body.extend(bytes([0xC9, 0x02]))
            body.extend(_encode_abscoord_mm(mv.speed_mm_s))
            first.speed_mm_s = mv.speed_mm_s
        if mv.power_pct != first.power_pct:
            body.extend(bytes([0xC7]))
            body.extend(_encode_power_pct(mv.power_pct))
            first.power_pct = mv.power_pct

        op = 0xA8 if mv.is_cut else 0x88
        body.append(op)
        body.extend(_encode_abscoord_mm(mv.x_mm))
        body.extend(_encode_abscoord_mm(mv.y_mm))

    # EOF marker (observed as 0xF0 0x0F in some dumps; using a simple terminator)
    body.extend(bytes([0xF0, 0x0F]))

    # Fill header length
    total_len = len(header) + len(body)
    header = header[:2] + total_len.to_bytes(4, "big") + header[6:]

    return header + bytes(body)
