from __future__ import annotations

import math

from laserdove.hardware.rd_builder import _RDJobBuilder
from laserdove.hardware.ruida_common import (
    encode_abscoord_mm,
    encode_abscoord_mm_signed,
    encode_power_pct,
)
from laserdove.rd_parser import RuidaParser


def test_decode_number_and_relcoord() -> None:
    parser = RuidaParser(buf=b"")
    encoded = encode_abscoord_mm(12.345)
    assert math.isclose(parser.decode_number(encoded), 12.345, abs_tol=1e-3)

    signed = encode_abscoord_mm_signed(-2.0)
    assert math.isclose(parser.decode_number(signed), -2.0, abs_tol=1e-3)

    builder = _RDJobBuilder()
    rel_pos = builder.encode_relcoord(1.5)
    rel_neg = builder.encode_relcoord(-1.0)
    assert math.isclose(parser.decode_relcoord(rel_pos), 1.5, abs_tol=1e-3)
    assert math.isclose(parser.decode_relcoord(rel_neg), -1.0, abs_tol=1e-3)


def test_layer_color_and_speed() -> None:
    builder = _RDJobBuilder()
    color_bytes = builder.encode_color((1, 2, 3))
    parser = RuidaParser(buf=bytes([2]) + color_bytes)
    parser._buf = bytes([2]) + color_bytes
    off, _ = parser.t_layer_color(0)
    assert off == 6
    assert parser.get_layer(2)["color"] == "#010203"

    parser._buf = bytes([2]) + encode_abscoord_mm(120.0)
    parser.t_layer_speed(0)
    assert math.isclose(parser.get_layer(2)["speed"], 120.0, abs_tol=1e-3)


def test_move_and_cut_segments() -> None:
    parser = RuidaParser(buf=b"")
    parser._buf = encode_abscoord_mm(10.0) + encode_abscoord_mm(5.0)
    parser.t_move_abs(0)
    assert parser._segments[-1]["is_cut"] is False

    builder = _RDJobBuilder()
    parser._buf = builder.encode_relcoord(-2.0) + builder.encode_relcoord(3.0)
    parser.t_cut_rel(0)
    seg = parser._segments[-1]
    assert seg["is_cut"] is True
    assert math.isclose(seg["x1"], 8.0, abs_tol=1e-3)
    assert math.isclose(seg["y1"], 8.0, abs_tol=1e-3)


def test_z_offset_and_air_assist_flags() -> None:
    parser = RuidaParser(buf=b"")
    parser._buf = encode_abscoord_mm_signed(1.25)
    parser.t_z_offset_8003(0)
    assert math.isclose(parser._current_z, 1.25, abs_tol=1e-3)
    assert parser._z_offsets

    parser.t_air_assist(0, True)
    assert parser._air_assist is True

    parser._buf = encode_abscoord_mm(1.0) + encode_abscoord_mm(2.0)
    parser.t_move_abs(0)
    assert parser._segments[-1]["air_assist"] is True


def test_laser_power_settings() -> None:
    parser = RuidaParser(buf=b"")
    parser._buf = encode_power_pct(12.0)
    parser.t_laser_min_pow(0, desc=[1])
    assert parser.get_laser(1)["pmin1"] == 12

    parser._buf = bytes([2]) + encode_power_pct(25.0)
    parser.t_laser_min_pow_lay(0, desc=[1])
    layer_laser = parser.get_layer(2)["laser"][1]
    assert layer_laser["pmin1"] == 25
