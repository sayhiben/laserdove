from __future__ import annotations

import math
import struct



def swizzle_byte(b: int, magic: int = 0x88) -> int:
    """
    Scramble a single byte using the Ruida swizzle algorithm.

    Args:
        b: Byte to swizzle.
        magic: Controller-specific magic key.

    Returns:
        Swizzled byte value.
    """
    b ^= (b >> 7) & 0xFF
    b ^= (b << 7) & 0xFF
    b ^= (b >> 7) & 0xFF
    b ^= magic
    b = (b + 1) & 0xFF
    return b


def unswizzle_byte(b: int, magic: int = 0x88) -> int:
    """
    Reverse the Ruida swizzle on a single byte.

    Args:
        b: Swizzled byte value.
        magic: Controller-specific magic key.

    Returns:
        Original unscrambled byte.
    """
    b = (b - 1) & 0xFF
    b ^= magic
    fb = b & 0x80
    lb = b & 0x01
    b = (b - fb - lb) & 0xFF
    b |= (lb << 7)
    b |= (fb >> 7)
    return b


def swizzle(payload: bytes, magic: int = 0x88) -> bytes:
    """
    Swizzle a payload for Ruida transport.

    Args:
        payload: Raw bytes to scramble.
        magic: Controller-specific magic key.

    Returns:
        Swizzled payload.
    """
    return bytes([swizzle_byte(b, magic) for b in payload])


def unswizzle(payload: bytes, magic: int = 0x88) -> bytes:
    """
    Reverse swizzling on a payload.

    Args:
        payload: Swizzled bytes.
        magic: Controller-specific magic key.

    Returns:
        Unscrambled payload.
    """
    return bytes([unswizzle_byte(b, magic) for b in payload])


def encode_abscoord_mm(value_mm: float) -> bytes:
    """
    Encode an absolute coordinate in mm into Ruida's 5x7-bit format.

    Args:
        value_mm: Coordinate in millimeters.

    Returns:
        Encoded bytes representing microns in base-128.
    """
    microns = int(round(value_mm * 1000.0))
    res = []
    for _ in range(5):
        res.append(microns & 0x7F)
        microns >>= 7
    res.reverse()
    return bytes(res)


def encode_abscoord_mm_signed(value_mm: float) -> bytes:
    """
    Encode a signed coordinate (mm) into Ruida's 5x7-bit field (two's complement).
    Useful for 0x80 0x03 Z offsets observed in LightBurn RD files.

    Args:
        value_mm: Signed coordinate in millimeters.

    Returns:
        Encoded signed base-128 payload.
    """
    microns = int(round(value_mm * 1000.0))
    if microns < 0:
        microns &= 0xFFFFFFFF
    res = []
    for _ in range(5):
        res.append(microns & 0x7F)
        microns >>= 7
    res.reverse()
    return bytes(res)


def encode_power_pct(power_pct: float) -> bytes:
    """
    Encode a power percentage into Ruida's 14-bit power field.

    Args:
        power_pct: Requested power percentage (0-100).

    Returns:
        Two-byte encoding of the power level.
    """
    clamped = max(0.0, min(100.0, power_pct))
    raw = int(round(clamped * (0x3FFF / 100.0)))
    return bytes([(raw >> 7) & 0x7F, raw & 0x7F])


def checksum(data: bytes) -> bytes:
    """
    Compute a 16-bit big-endian checksum over the data bytes.

    Args:
        data: Payload to checksum.

    Returns:
        Two-byte checksum.
    """
    cs = sum(data) & 0xFFFF
    return struct.pack(">H", cs)


def decode_abscoord_mm(payload: bytes) -> float:
    """
    Decode a Ruida 5x7-bit absolute coordinate to millimeters.

    Args:
        payload: Encoded coordinate bytes.

    Returns:
        Coordinate value in millimeters.
    """
    microns = 0
    for b in payload:
        microns = (microns << 7) | b
    return microns / 1000.0


def decode_status_bits(payload: bytes) -> int:
    """
    Decode a 4-byte status payload into an integer bitfield.

    Args:
        payload: Raw status bytes from MEM_MACHINE_STATUS.

    Returns:
        Parsed status bits as an integer.
    """
    return int.from_bytes(payload[:4], byteorder="big", signed=False)


def should_force_speed(last_speed_ums: int | None, speed_mm_s: float) -> tuple[int, bool]:
    """
    Determine whether to emit a new speed command.

    Args:
        last_speed_ums: Last commanded speed in microns/sec, or None.
        speed_mm_s: Requested speed in mm/sec.

    Returns:
        Tuple of (speed in microns/sec, whether it differs from last send).
    """
    speed_ums = int(round(speed_mm_s * 1000.0))
    if last_speed_ums == speed_ums:
        return speed_ums, False
    return speed_ums, True


def clamp_power(power_pct: float | None, current_power: float) -> tuple[float, bool]:
    """
    Normalize a requested power value and decide whether to update hardware.

    Args:
        power_pct: Requested power percentage; None is treated as 0.
        current_power: Last commanded power percentage.

    Returns:
        Tuple of (clamped power percentage, should_send_update flag).
    """
    requested = 0.0 if power_pct is None else power_pct
    return requested, not math.isclose(current_power, requested, abs_tol=1e-6)

