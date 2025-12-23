from __future__ import annotations

import math

from laserdove.hardware import ruida_common
from laserdove.rd_parser import RuidaParser


def test_swizzle_round_trip() -> None:
    payload = bytes([0x00, 0x01, 0x7F, 0x80, 0xFF])
    for magic in (0x88, 0x11):
        swizzled = ruida_common.swizzle(payload, magic=magic)
        assert ruida_common.unswizzle(swizzled, magic=magic) == payload


def test_encode_decode_abscoord_mm_round_trip() -> None:
    value = 12.345
    encoded = ruida_common.encode_abscoord_mm(value)
    decoded = ruida_common.decode_abscoord_mm(encoded)
    assert math.isclose(decoded, value, abs_tol=1e-3)


def test_encode_abscoord_mm_signed_negative_round_trip() -> None:
    value = -1.5
    encoded = ruida_common.encode_abscoord_mm_signed(value)
    parser = RuidaParser(buf=b"")
    decoded = parser.decode_number(encoded)
    assert math.isclose(decoded, value, abs_tol=1e-3)


def test_encode_power_pct_clamps() -> None:
    parser = RuidaParser(buf=b"")
    encoded = ruida_common.encode_power_pct(120.0)
    decoded = parser.decode_percent_float(encoded)
    assert math.isclose(decoded, 100.0, abs_tol=0.5)

    encoded = ruida_common.encode_power_pct(-10.0)
    decoded = parser.decode_percent_float(encoded)
    assert math.isclose(decoded, 0.0, abs_tol=0.5)


def test_decode_status_bits_and_speed_helpers() -> None:
    payload = bytes([0x12, 0x34, 0x56, 0x78])
    assert ruida_common.decode_status_bits(payload) == 0x12345678

    speed, changed = ruida_common.should_force_speed(None, 10.0)
    assert speed == 10000
    assert changed is True

    speed2, changed2 = ruida_common.should_force_speed(speed, 10.0)
    assert speed2 == 10000
    assert changed2 is False

    power, changed = ruida_common.clamp_power(None, 10.0)
    assert power == 0.0
    assert changed is True

    power2, changed2 = ruida_common.clamp_power(20.0, 20.0)
    assert power2 == 20.0
    assert changed2 is False
