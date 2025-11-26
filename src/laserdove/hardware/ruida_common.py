from __future__ import annotations

import math
import struct


def swizzle_byte(b: int, magic: int = 0x88) -> int:
    b ^= (b >> 7) & 0xFF
    b ^= (b << 7) & 0xFF
    b ^= (b >> 7) & 0xFF
    b ^= magic
    b = (b + 1) & 0xFF
    return b


def unswizzle_byte(b: int, magic: int = 0x88) -> int:
    b = (b - 1) & 0xFF
    b ^= magic
    fb = b & 0x80
    lb = b & 0x01
    b = (b - fb - lb) & 0xFF
    b |= (lb << 7)
    b |= (fb >> 7)
    return b


def swizzle(payload: bytes, magic: int = 0x88) -> bytes:
    return bytes([swizzle_byte(b, magic) for b in payload])


def unswizzle(payload: bytes, magic: int = 0x88) -> bytes:
    return bytes([unswizzle_byte(b, magic) for b in payload])


def encode_abscoord_mm(value_mm: float) -> bytes:
    microns = int(round(value_mm * 1000.0))
    res = []
    for _ in range(5):
        res.append(microns & 0x7F)
        microns >>= 7
    res.reverse()
    return bytes(res)


def encode_power_pct(power_pct: float) -> bytes:
    clamped = max(0.0, min(100.0, power_pct))
    raw = int(round(clamped * (0x3FFF / 100.0)))
    return bytes([(raw >> 7) & 0x7F, raw & 0x7F])


def checksum(data: bytes) -> bytes:
    cs = sum(data) & 0xFFFF
    return struct.pack(">H", cs)


def decode_abscoord_mm(payload: bytes) -> float:
    microns = 0
    for b in payload:
        microns = (microns << 7) | b
    return microns / 1000.0


def decode_status_bits(payload: bytes) -> int:
    return int.from_bytes(payload[:4], byteorder="big", signed=False)


def should_force_speed(last_speed_ums: int | None, speed_mm_s: float) -> tuple[int, bool]:
    """Return (ums, changed?) to avoid redundant speed packets."""
    speed_ums = int(round(speed_mm_s * 1000.0))
    if last_speed_ums == speed_ums:
        return speed_ums, False
    return speed_ums, True


def clamp_power(power_pct: float | None, current_power: float) -> tuple[float, bool]:
    requested = 0.0 if power_pct is None else power_pct
    return requested, not math.isclose(current_power, requested, abs_tol=1e-6)
