#!/usr/bin/env python3
"""
RD parser utility (CLI) for inspecting exported .rd files.

Notes:
- CA 41 likely encodes layer mode; flag 0x00 vs 0x02 correlates with LightBurn fill/image layers (line layers mostly 0x00, fill/image/LPI/exported-as-fill often 0x02; layer IDs differ from the LB UI).
- C6 11 is labeled “Time” in EduTech; we print raw + decoded (~10000 in calibration.rd).
- E5 05 payload bytes[1:] decode to a float that looks like spacing/DPI (e.g., 0.6419mm).

Adapted from the reference/ruidaparser.py decoder so we can quickly inspect
layer settings, Z offsets (e.g., 0x80 0x03), and motion commands locally.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
from typing import Any, Dict, List, Tuple

from laserdove.hardware.rd_commands import (
    DEFAULT_PROFILE_NAME,
    RuidaProfile,
    command_table_for,
    get_profile,
    merge_protocol_tables,
)
from laserdove.hardware.ruida_common import unswizzle


class RuidaParser:
    """
    Minimal RD decoder adapted from reference/ruidaparser.py.
    """

    def __init__(
        self,
        buf: bytes | None = None,
        file: str | None = None,
        *,
        profile: str | RuidaProfile | None = None,
    ) -> None:
        self.profile = get_profile(profile)
        self._buf = buf
        self._file = file
        self._bbox = [10e9, 10e9, -10e9, -10e9]
        self._paths: List[dict] = []
        self._layer: dict = {}
        self._laser: dict = {}
        self._prio = 0
        self._z_offsets: List[tuple[int, float, bytes, int]] = []
        self._current_pos: int = -1
        self._segments: List[dict] = []
        self._cursor: List[float] = [0.0, 0.0]
        self._current_z: float = 0.0
        self._air_assist: bool | None = None
        self._opcode_counts: Dict[str, int] = {}
        self._unknown_counts: Dict[str, int] = {}
        self.rd_decoder_table = merge_protocol_tables(
            command_table_for(self.profile),
            self.profile.decoder_overrides,
            self.decoder_overrides(),
        )
        if file and buf is None:
            with open(file, "rb") as fd:
                raw = fd.read()
            self._buf = self.unscramble_bytes(raw)

    # ---------------- Segment helpers ----------------
    def _emit_segment(self, x: float, y: float, *, is_cut: bool) -> None:
        if not self._segments:
            last_x, last_y = self._cursor
        else:
            last = self._segments[-1]
            last_x, last_y = last["x1"], last["y1"]
        if math.isclose(last_x, x, abs_tol=1e-9) and math.isclose(last_y, y, abs_tol=1e-9):
            self._cursor = [x, y]
            return
        self._segments.append(
            {
                "x0": last_x,
                "y0": last_y,
                "x1": x,
                "y1": y,
                "is_cut": is_cut,
                "z": self._current_z,
                "logical_z": self._current_z,
                "air_assist": self._air_assist,
            }
        )
        self._cursor = [x, y]

    # ---------------- Basic helpers ----------------
    def unscramble_bytes(self, data: bytes) -> bytes:
        return unswizzle(data, magic=0x88)

    def get_layer(self, n: int) -> dict:
        if n not in self._layer:
            self._layer[n] = {"n": n, "bbox": [0, 0, 0, 0], "laser": {}}
        return self._layer[n]

    def get_laser(self, n: int, lay: int | None = None) -> dict:
        if lay is not None:
            layer = self.get_layer(lay)
            if n not in layer["laser"]:
                layer["laser"][n] = {"n": n, "offset": [0, 0], "layer": lay}
            return layer["laser"][n]
        if n not in self._laser:
            self._laser[n] = {"n": n, "offset": [0, 0]}
        return self._laser[n]

    def new_path(self) -> List[List[float]]:
        p = {"data": [], "n": len(self._paths), "layer": self._layer.get(self._prio, self._prio)}
        self._paths.append(p)
        return p["data"]

    def get_path(self) -> List[List[float]]:
        if not self._paths:
            self.new_path().append([0, 0])
        return self._paths[-1]["data"]

    def relative_xy(self, x: float = 0.0, y: float = 0.0) -> List[float]:
        if not self._paths:
            self.new_path().append([0, 0])
        current = self._paths[-1]["data"][-1]
        return [current[0] + x, current[1] + y]

    # ---------------- Decoders ----------------
    def decode_number(self, x: bytes) -> float:
        fak = 1
        res = 0
        for b in reversed(x):
            res += fak * b
            fak *= 0x80
        if res > 0x80000000:
            res = res - 0x100000000
        return res * 0.001

    def decode_relcoord(self, x: bytes) -> float:
        r = (x[0] << 7) + x[1]
        if r > 16383 or r < 0:
            raise ValueError("Not a rel coord: " + repr(x[0:2]))
        if r > 8191:
            return 0.001 * (r - 16384)
        return 0.001 * r

    def decode_percent_float(self, x: bytes) -> float:
        return ((x[0] << 7) + x[1]) * 100 / 0x3FFF

    def arg_strz(self, off: int = 0) -> Tuple[int, str]:
        string = ""
        while self._buf[off] != 0x00:
            string += "%c" % self._buf[off]
            off += 1
        return off + 1, string

    def arg_byte(self, off: int = 0) -> Tuple[int, int]:
        return off + 1, self._buf[off]

    def arg_perc(self, off: int = 0) -> Tuple[int, int]:
        buf = self._buf[off : off + 2]
        return off + 2, int(self.decode_percent_float(buf) + 0.5)

    def arg_abs(self, off: int = 0) -> Tuple[int, float]:
        buf = self._buf[off : off + 5]
        return off + 5, self.decode_number(buf)

    def arg_rel(self, off: int = 0) -> Tuple[int, float]:
        buf = self._buf[off : off + 2]
        return off + 2, self.decode_relcoord(buf)

    def arg_color(self, off: int = 0) -> Tuple[int, int]:
        buf = self._buf[off : off + 5]
        rgb = list(reversed(list(buf)))
        red = rgb[0] + ((rgb[1] & 0x01) << 7)
        green = ((rgb[1] & 0x7E) >> 1) + ((rgb[2] & 0x03) << 6)
        blue = ((rgb[2] & 0x7C) >> 2) + ((rgb[3] & 0x07) << 5)
        return off + 5, ((red << 16) + (green << 8) + blue)

    # ---------------- Token handlers (subset) ----------------
    def skip_msg(self, n: int, desc=None):
        buf = self._buf
        r = []
        if len(buf) < n:
            return "ERROR: len(buf)=%d < n=%d" % (len(buf), n)
        for i in range(n):
            r.append("%02x" % buf[i])
        if isinstance(desc, list):
            off = 0
            v = []
            for arg in desc[1:]:
                if isinstance(arg, int):
                    off += arg
                else:
                    try:
                        n2, val = arg(self, off)  # type: ignore
                    except Exception:
                        val = None
                        n2 = off
                    v.append(val)
                    off = n2
            if v:
                r.append("=>" + str(v))
            if len(desc) > 1:
                n = off
        return n, " ".join(r)

    def t_skip_bytes(self, n: int, desc=None):
        return self.skip_msg(n, desc)

    def t_layer_priority(self, n: int, desc=None):
        layer_id = self._buf[0]
        self._prio = layer_id
        return 1, f"t_layer_priority({layer_id})"

    def t_layer_color(self, n: int, desc=None):
        layer = self.get_layer(self._buf[0])
        off, c = self.arg_color(1)
        layer["color"] = "#%06x" % c
        return off, f"t_layer_color({layer['n']}, {layer['color']})"

    def _laser_from_desc(self, desc, default=1) -> int:
        if isinstance(desc, (list, tuple)):
            for item in desc:
                if isinstance(item, int):
                    return item
        if isinstance(desc, int):
            return desc
        return default

    def t_laser_min_pow(self, n: int, desc=None):
        laser_id = self._laser_from_desc(desc, default=1)
        las = self.get_laser(laser_id)
        off, v = self.arg_perc()
        las[f"pmin{laser_id}"] = v
        return off, f"t_laser_min_pow({las['n']}, {v}%)"

    def t_laser_max_pow(self, n: int, desc=None):
        laser_id = self._laser_from_desc(desc, default=1)
        las = self.get_laser(laser_id)
        off, v = self.arg_perc()
        las[f"pmax{laser_id}"] = v
        return off, f"t_laser_max_pow({las['n']}, {v}%)"

    def t_laser_min_pow_lay(self, n: int, desc=None):
        laser_id = self._laser_from_desc(desc, default=1)
        layer_id = self._buf[0] if self._buf else None
        las = self.get_laser(laser_id, layer_id)
        off, v = self.arg_perc(1)
        las[f"pmin{laser_id}"] = v
        return off, f"t_laser_min_pow_lay({las['n']}, {layer_id}, {v}%)"

    def t_laser_max_pow_lay(self, n: int, desc=None):
        laser_id = self._laser_from_desc(desc, default=1)
        layer_id = self._buf[0] if self._buf else None
        las = self.get_laser(laser_id, layer_id)
        off, v = self.arg_perc(1)
        las[f"pmax{laser_id}"] = v
        return off, f"t_laser_max_pow_lay({las['n']}, {layer_id}, {v}%)"

    def t_cut_through_pow(self, n: int, desc=None):
        off, v = self.arg_perc()
        return off, f"t_cut_through_pow({desc[0]}, {v}%)"

    def t_layer_speed(self, n: int, desc=None):
        layer_id = self._buf[0] if self._buf else None
        off, s = self.arg_abs(1)
        layer = self.get_layer(layer_id if layer_id is not None else 0)
        layer["speed"] = s
        return off, f"t_layer_speed({layer['n']}, {s}mm)"

    def t_laser_freq(self, n: int, desc=None):
        laser_id = self._laser_from_desc(desc, default=1)
        if self._buf:
            laser_id = self._buf[0]
        las = self.get_laser(laser_id)
        off, freq = self.arg_abs(2)
        las["freq"] = freq
        return off, f"t_laser_freq({las['n']}, {freq}kHz)"

    def t_speed_axis(self, n: int, desc=None):
        off, s = self.arg_abs()
        return off, f"t_speed_axis({s}mm/s)"

    def t_job_units_hint(self, n: int, desc=None):
        val = self._buf[0]
        hint = "mm" if val == 0x00 else "inch" if val == 0x01 else "unknown"
        return 1, f"job_units_flag=0x{val:02X} ({hint} guess; 00=mm?, 01=in?)"

    def t_laser_offset(self, n: int, desc=None):
        las = self.get_laser(desc[1])
        off, x = self.arg_abs()
        off, y = self.arg_abs(off)
        las["offset"][0] = x
        las["offset"][1] = y
        return off, f"t_laser_offset({las['n']}, {x:.8g}mm, {y:.8g}mm)"

    def t_layer_flag_ca41(self, n: int, desc=None):
        """
        CA 41 appears to be a per-layer mode/flag byte. Values seen: 0x00, 0x02.
        LightBurn layers (line vs fill) suggest 0x00=line? / 0x02=fill/raster? (uncertain, layer IDs differ from LB UI).
        """
        layer = self._buf[0]
        flag = self._buf[1]
        lay = self.get_layer(layer)
        lay["mode_flag"] = flag
        guess = "line?" if flag == 0x00 else "fill/raster?" if flag == 0x02 else "unknown"
        return 2, f"layer={layer} flag=0x{flag:02X} ({guess})"

    def t_c6_11_unknown(self, n: int, desc=None):
        """
        C6 11 payload is 5 bytes; EduTech wiki lists 0xC6 0x11 as 'Time.'.
        Observed as ~10000.0 in calibration.rd; keep as unknown/time? and show raw+decoded.
        """
        raw = self._buf[:5]
        _, val = self.arg_abs()
        return 5, f"Time_C6_11? raw={raw.hex(' ')} decoded≈{val:.3f}"

    def t_work_spacing(self, n: int, desc=None):
        """
        E5 05 payload observed as 5 bytes; bytes[1:] look like a float mm spacing.
        Seen once near trailer (after Finish/Stop/Work_Interval) in calibration.rd.
        """
        raw = self._buf[:5]
        spacing_mm = None
        if len(raw) >= 5:
            try:
                spacing_mm = struct.unpack("<f", raw[1:5])[0]
            except Exception:
                spacing_mm = None
        if spacing_mm is not None:
            return 5, f"work_spacing≈{spacing_mm:.4f}mm raw={raw.hex(' ')}"
        return 5, f"Work_Spacing_raw={raw.hex(' ')}"

    # ---------------- Counters ----------------
    def _count_label(self, label: str) -> None:
        self._opcode_counts[label] = self._opcode_counts.get(label, 0) + 1

    def _count_unknown(self, label: str) -> None:
        self._unknown_counts[label] = self._unknown_counts.get(label, 0) + 1

    def t_set_absolute(self, n: int, desc=None):
        return 0, "t_set_absolute()"

    def t_process_control(self, n: int, desc=None):
        label = desc or "process_control"
        return 0, f"t_{label}"

    def t_bb_top_left(self, n: int, desc=None):
        off, x = self.arg_abs()
        off, y = self.arg_abs(off)
        self._bbox[0] = min(self._bbox[0], x)
        self._bbox[1] = min(self._bbox[1], y)
        return off, f"t_bb_top_left({x:.8g}mm, {y:.8g}mm)"

    def t_bb_bot_right(self, n: int, desc=None):
        off, x = self.arg_abs()
        off, y = self.arg_abs(off)
        self._bbox[2] = max(self._bbox[2], x)
        self._bbox[3] = max(self._bbox[3], y)
        return off, f"t_bb_bot_right({x:.8g}mm, {y:.8g}mm)"

    def t_lay_top_left(self, n: int, desc=None):
        layer = self.get_layer(self._buf[0])
        off, x = self.arg_abs(1)
        off, y = self.arg_abs(off)
        self._bbox[0] = min(self._bbox[0], x)
        self._bbox[1] = min(self._bbox[1], y)
        layer["bbox"][0] = x
        layer["bbox"][1] = y
        return off, f"t_lay_top_left({layer['n']}, {x:.8g}mm, {y:.8g}mm)"

    def t_lay_bot_right(self, n: int, desc=None):
        layer = self.get_layer(self._buf[0])
        off, x = self.arg_abs(1)
        off, y = self.arg_abs(off)
        self._bbox[2] = max(self._bbox[2], x)
        self._bbox[3] = max(self._bbox[3], y)
        layer["bbox"][2] = x
        layer["bbox"][3] = y
        return off, f"t_lay_bot_right({layer['n']}, {x:.8g}mm, {y:.8g}mm)"

    def t_feeding(self, n: int, desc=None):
        off, x = self.arg_abs()
        off, y = self.arg_abs(off)
        return off, f"t_feeding({x}mm, {y}mm)"

    def t_move_abs(self, n: int, desc=None):
        off, x = self.arg_abs()
        off, y = self.arg_abs(off)
        self.new_path().append([x, y])
        self._emit_segment(x, y, is_cut=False)
        return off, f"t_move_abs({x:.3f}mm, {y:.3f}mm)"

    def t_move_rel(self, n: int, desc=None):
        off, dx = self.arg_rel()
        off, dy = self.arg_rel(off)
        xy = self.relative_xy(dx, dy)
        self.new_path().append(xy)
        self._emit_segment(xy[0], xy[1], is_cut=False)
        return off, f"t_move_rel({dx:.3f}mm, {dy:.3f}mm)"

    def t_move_horiz(self, n: int, desc=None):
        off, dx = self.arg_rel()
        xy = self.relative_xy(dx, 0)
        self.new_path().append(xy)
        self._emit_segment(xy[0], xy[1], is_cut=False)
        return off, f"t_move_horiz({dx:.3f}mm)"

    def t_move_vert(self, n: int, desc=None):
        off, dy = self.arg_rel()
        xy = self.relative_xy(0, dy)
        self.new_path().append(xy)
        self._emit_segment(xy[0], xy[1], is_cut=False)
        return off, f"t_move_vert({dy:.3f}mm)"

    def t_cut_abs(self, n: int, desc=None):
        off, x = self.arg_abs()
        off, y = self.arg_abs(off)
        self.get_path().append([x, y])
        self._emit_segment(x, y, is_cut=True)
        return off, f"t_cut_abs({x:.3f}mm, {y:.3f}mm)"

    def t_cut_rel(self, n: int, desc=None):
        off, dx = self.arg_rel()
        off, dy = self.arg_rel(off)
        xy = self.relative_xy(dx, dy)
        self.get_path().append(xy)
        self._emit_segment(xy[0], xy[1], is_cut=True)
        return off, f"t_cut_rel({dx:.3f}mm, {dy:.3f}mm)"

    def t_cut_horiz(self, n: int, desc=None):
        off, dx = self.arg_rel()
        xy = self.relative_xy(dx, 0)
        self.get_path().append(xy)
        self._emit_segment(xy[0], xy[1], is_cut=True)
        return off, f"t_cut_horiz({dx:.3f}mm)"

    def t_cut_vert(self, n: int, desc=None):
        off, dy = self.arg_rel()
        xy = self.relative_xy(0, dy)
        self.get_path().append(xy)
        self._emit_segment(xy[0], xy[1], is_cut=True)
        return off, f"t_cut_vert({dy:.3f}mm)"

    def t_z_offset_8003(self, n: int, desc=None):
        raw = self._buf[:5]
        val = self.decode_number(raw)
        self._z_offsets.append((self._current_pos, val, raw, self._prio))
        self._current_z = val
        return 5, f"Z_Offset_80_03({val:.3f}mm raw={raw.hex(' ')})"

    def t_air_assist(self, n: int, desc=None):
        self._air_assist = bool(desc)
        state = "ON" if self._air_assist else "OFF"
        return 1, f"t_air_assist({state})"

    def t_rapid_move_abs(self, n: int, desc=None):
        mode = self._buf[0]
        off, x = self.arg_abs(1)
        off, y = self.arg_abs(off)
        self.new_path().append([x, y])
        self._emit_segment(x, y, is_cut=False)
        return off, f"t_rapid_move_abs(mode=0x{mode:02X}, x={x:.3f}mm, y={y:.3f}mm)"

    def t_rapid_move_axis(self, n: int, desc=None):
        axis = desc or "axis"
        if isinstance(axis, (list, tuple)) and axis:
            axis = axis[0]
        mode = self._buf[0]
        off, coord = self.arg_abs(1)
        if axis.lower() in ("x", "y"):
            if axis.lower() == "x":
                self._cursor[0] = coord
            else:
                self._cursor[1] = coord
            self._emit_segment(self._cursor[0], self._cursor[1], is_cut=False)
        return off, f"t_rapid_move_{axis}(mode=0x{mode:02X}, coord={coord:.3f}mm)"

    # ---------------- Decoder table ----------------
    @classmethod
    def decoder_overrides(cls) -> Dict[int, Any]:
        """Parser-specific decode handlers layered on top of shared labels."""
        return {
            0x80: {
                0x01: ["Axis_X_Move (80 01)", cls.t_skip_bytes, 5],  # observed as X on 6442
                0x03: ["Axis_Z_Offset (80 03)", cls.t_z_offset_8003, 0, [0]],  # signed abscoord
                0x08: ["Axis_Y_Move (80 08)", cls.t_skip_bytes, 5],  # observed as Y on 6442
            },
            0x88: ["Mov_Abs", cls.t_move_abs, 5 + 5, ":abs, :abs"],
            0x89: ["Mov_Rel", cls.t_move_rel, 2 + 2, ":rel, :rel"],
            0x8A: ["Mov_Horiz", cls.t_move_horiz, 2, ":rel"],
            0x8B: ["Mov_Vert", cls.t_move_vert, 2, ":rel"],
            0xA8: ["Cut_Abs", cls.t_cut_abs, 5 + 5, ":abs, :abs"],
            0xA9: ["Cut_Rel", cls.t_cut_rel, 2 + 2, ":rel, :rel"],
            0xAA: ["Cut_Horiz", cls.t_cut_horiz, 2, ":rel"],
            0xAB: ["Cut_Vert", cls.t_cut_vert, 2, ":rel"],
            0xC0: ["Unknown_C0", cls.t_skip_bytes, 2],
            0xC1: ["Unknown_C1", cls.t_skip_bytes, 2],
            0xC2: ["Unknown_C2", cls.t_skip_bytes, 2],
            0xC3: ["Unknown_C3", cls.t_skip_bytes, 2],
            0xC4: ["Unknown_C4", cls.t_skip_bytes, 2],
            0xC5: ["Unknown_C5", cls.t_skip_bytes, 2],
            0xC6: {
                0x01: ["Laser_1_Min_Pow_C6_01", cls.t_laser_min_pow, 2, ":power", 1],
                0x02: ["Laser_1_Max_Pow_C6_02", cls.t_laser_max_pow, 2, ":power", 1],
                0x05: ["Laser_3_Min_Pow_C6_05", cls.t_laser_min_pow, 2, ":power", 3],
                0x06: ["Laser_3_Max_Pow_C6_06", cls.t_laser_max_pow, 2, ":power", 3],
                0x07: ["Laser_4_Min_Pow_C6_07", cls.t_laser_min_pow, 2, ":power", 4],
                0x08: ["Laser_4_Max_Pow_C6_08", cls.t_laser_max_pow, 2, ":power", 4],
                0x10: ["Dot_time_C6_10", cls.t_skip_bytes, 5, ":sec", cls.arg_abs],
                0x11: ["Time_C6_11?", cls.t_c6_11_unknown, 5],
                0x12: ["Cut_Open_delay_12", cls.t_skip_bytes, 5, ":ms", cls.arg_abs],
                0x13: ["Cut_Close_delay_13", cls.t_skip_bytes, 5, ":ms", cls.arg_abs],
                0x15: ["Cut_Open_delay_15", cls.t_skip_bytes, 5, ":ms", cls.arg_abs],
                0x16: ["Cut_Close_delay_16", cls.t_skip_bytes, 5, ":ms", cls.arg_abs],
                0x21: ["Laser_2_Min_Pow_C6_21", cls.t_laser_min_pow, 2, ":power", 2],
                0x22: ["Laser_2_Max_Pow_C6_22", cls.t_laser_max_pow, 2, ":power", 2],
                0x31: [
                    "Laser_1_Min_Pow_C6_31",
                    cls.t_laser_min_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    1,
                ],
                0x32: [
                    "Laser_1_Max_Pow_C6_32",
                    cls.t_laser_max_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    1,
                ],
                0x35: [
                    "Laser_3_Min_Pow_C6_35",
                    cls.t_laser_min_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    3,
                ],
                0x36: [
                    "Laser_3_Max_Pow_C6_36",
                    cls.t_laser_max_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    3,
                ],
                0x37: [
                    "Laser_4_Min_Pow_C6_37",
                    cls.t_laser_min_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    4,
                ],
                0x38: [
                    "Laser_4_Max_Pow_C6_38",
                    cls.t_laser_max_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    4,
                ],
                0x41: [
                    "Laser_2_Min_Pow_C6_41",
                    cls.t_laser_min_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    2,
                ],
                0x42: [
                    "Laser_2_Max_Pow_C6_42",
                    cls.t_laser_max_pow_lay,
                    1 + 2,
                    ":layer, :power",
                    2,
                ],
                0x50: ["Cut_through_power1", cls.t_cut_through_pow, 2, ":power", 1],
                0x51: ["Cut_through_power2", cls.t_cut_through_pow, 2, ":power", 2],
                0x55: ["Cut_through_power3", cls.t_cut_through_pow, 2, ":power", 3],
                0x56: ["Cut_through_power4", cls.t_cut_through_pow, 2, ":power", 4],
                0x60: ["Laser_Freq", cls.t_laser_freq, 1 + 1 + 5, ":laser, 0x00, :freq"],
            },
            0xC7: ["Unknown_C7", cls.t_skip_bytes, 2],
            0xC8: ["Unknown_C8", cls.t_skip_bytes, 2],
            0xC9: {
                0x02: ["Speed_Laser1 (C9 02)", cls.t_skip_bytes, 5, ":speed", cls.arg_abs],
                0x03: ["Speed_Axis (C9 03)", cls.t_speed_axis, 5, ":speed"],
                0x04: ["Layer_Speed", cls.t_layer_speed, 1 + 5, ":layer, :speed"],
                0x05: ["Force_Eng_Speed_C9_05", cls.t_speed_axis, 5, ":speed"],
            },
            0xCA: {
                0x01: {
                    0x00: ["Layer_End_CA_01_00", cls.t_skip_bytes, 1],
                    0x01: ["Work_Mode_1_CA_01_01", cls.t_skip_bytes, 1],
                    0x02: ["Work_Mode_2_CA_01_02", cls.t_skip_bytes, 1],
                    0x03: ["Work_Mode_3_CA_01_03", cls.t_skip_bytes, 1],
                    0x04: ["Work_Mode_4_CA_01_04", cls.t_skip_bytes, 1],
                    0x05: ["Work_Mode_6_CA_01_05", cls.t_skip_bytes, 1],
                    0x10: ["Laser_Device_0_CA_01_10", cls.t_skip_bytes, 1],
                    0x11: ["Laser_Device_1_CA_01_11", cls.t_skip_bytes, 1],
                    0x12: ["Air_Assist_OFF_CA_01_12", cls.t_air_assist, 1, 0],
                    0x13: ["Air_Assist_ON_CA_01_13", cls.t_air_assist, 1, 1],
                    0x14: ["DB_Head_CA_01_14", cls.t_skip_bytes, 1],
                    0x30: ["En_Laser_2_Offset0_CA_01_30", cls.t_skip_bytes, 1],
                    0x31: ["En_Laser_2_Offset1_CA_01_31", cls.t_skip_bytes, 1],
                    0x55: ["Work_Mode_5_CA_01_55", cls.t_skip_bytes, 1],
                },
                0x02: ["Layer_Number_Part_CA_02", cls.t_skip_bytes, 1],
                0x03: ["Unkown_CA_03", cls.t_skip_bytes, 1],
                0x06: ["Layer_Color", cls.t_layer_color, 1 + 5, ":layer, :color"],
                0x10: ["Unkown_CA_10", cls.t_skip_bytes, 1],
                0x12: ["Blow_off", cls.t_skip_bytes, 0],
                0x13: ["Blow_on", cls.t_skip_bytes, 0],
                0x22: ["Layer_Count", cls.t_skip_bytes, 1],
                0x41: ["Layer_Mode_CA_41?", cls.t_layer_flag_ca41, 2, ":layer, flag"],
            },
            0xCC: ["Ack_CC", cls.t_skip_bytes, 0],
            0xD7: ["EOF"],
            0xD8: {
                0x00: ["Start_Process_D8_00", cls.t_process_control, 0, "start_process"],
                0x01: ["Stop_Process_D8_01", cls.t_process_control, 0, "stop_process"],
                0x02: ["Pause_Process_D8_02", cls.t_process_control, 0, "pause_process"],
                0x03: ["Restore_Process_D8_03", cls.t_process_control, 0, "restore_process"],
                0x10: ["Unknown_D8_10", cls.t_skip_bytes, 0],
                0x11: ["Unknown_D8_11", cls.t_skip_bytes, 0],
                0x12: ["UploadFollows_D8_12", cls.t_skip_bytes, 0],
            },
            0xD9: {
                0x00: ["Rapid_Move_X_D9_00", cls.t_rapid_move_axis, 1 + 5, "x"],
                0x01: ["Rapid_Move_Y_D9_01", cls.t_rapid_move_axis, 1 + 5, "y"],
                0x02: ["Rapid_Move_Z_D9_02", cls.t_skip_bytes, 1 + 5, ":mm", 1, cls.arg_abs],
                0x03: ["Direct_Move_U_rel", cls.t_skip_bytes, 1 + 5, ":mm", 1, cls.arg_abs],
                0x10: ["Rapid_Move_XY_D9_10", cls.t_rapid_move_abs, 1 + 5 + 5],
            },
            0xDA: {
                0x00: ["Work_Interval query", cls.t_skip_bytes, 2],
                0x01: [
                    "Work_Interval resp1",
                    cls.t_skip_bytes,
                    2 + 5,
                    "??",
                    2,
                    cls.arg_abs,
                    cls.arg_abs,
                ],
            },
            0xE5: {0x05: ["Work_Spacing? (E5 05)", cls.t_work_spacing, 5]},
            0xE6: {0x01: ["Job_Header? (E6 01)", cls.t_set_absolute, 0]},
            0xE7: {
                0x00: ["Stop"],
                0x01: ["SetFilename", cls.t_skip_bytes, 0, ":strz", cls.arg_strz],
                0x03: ["Bounding_Box_Top_Left", cls.t_bb_top_left, 5 + 5, ":abs, :abs"],
                0x04: [
                    "Layer_Bbox_Reset? (E7 04)",
                    cls.t_skip_bytes,
                    4 + 5 + 5,
                    ":abs, :abs",
                    4,
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x05: ["Layer_Bbox_Flush? (E7 05)", cls.t_skip_bytes, 1],
                0x06: ["Feeding", cls.t_feeding, 5 + 5, ":abs, :abs"],
                0x07: ["Bounding_Box_Bottom_Right", cls.t_bb_bot_right, 5 + 5, ":abs, :abs"],
                0x08: [
                    "Layer_Bbox_Bottom_Right? (E7 08)",
                    cls.t_skip_bytes,
                    4 + 5 + 5,
                    ":abs, :abs",
                    4,
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x13: [
                    "Layout_Origin? (E7 13)",
                    cls.t_skip_bytes,
                    5 + 5,
                    ":abs, :abs",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x17: [
                    "Layout_Bottom_Right? (E7 17)",
                    cls.t_skip_bytes,
                    5 + 5,
                    ":abs, :abs",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x23: [
                    "Layout_Origin_Alt? (E7 23)",
                    cls.t_skip_bytes,
                    5 + 5,
                    ":abs, :abs",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x24: ["Layout_Flags? (E7 24)", cls.t_skip_bytes, 1],
                0x37: [
                    "Layout_Bbox_Alt? (E7 37)",
                    cls.t_skip_bytes,
                    5 + 5,
                    "??",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x38: ["Job_Units? (E7 38)", cls.t_job_units_hint, 1],
                0x50: ["Bounding_Box_Top_Left", cls.t_bb_top_left, 5 + 5, ":abs, :abs"],
                0x51: ["Bounding_Box_Bottom_Right", cls.t_bb_bot_right, 5 + 5, ":abs, :abs"],
                0x52: ["Layer_Top_Left_E7_52", cls.t_lay_top_left, 1 + 5 + 5, ":layer, :abs, :abs"],
                0x53: [
                    "Layer_Bottom_Right_E7_53",
                    cls.t_lay_bot_right,
                    1 + 5 + 5,
                    ":layer, :abs, :abs",
                ],
                0x54: [
                    "Pen_Draw_Y",
                    cls.t_skip_bytes,
                    1 + 5,
                    ":layer, :abs",
                    cls.arg_byte,
                    cls.arg_abs,
                ],
                0x55: [
                    "Laser_Y_Offset",
                    cls.t_skip_bytes,
                    1 + 5,
                    ":layer, :abs",
                    cls.arg_byte,
                    cls.arg_abs,
                ],
                0x60: ["Unkown_E7_60", cls.t_skip_bytes, 1],
                0x61: ["Layer_Top_Left_E7_61", cls.t_lay_top_left, 1 + 5 + 5, ":layer, :abs, :abs"],
                0x62: [
                    "Layer_Bottom_Right_E7_62",
                    cls.t_lay_bot_right,
                    1 + 5 + 5,
                    ":layer, :abs, :abs",
                ],
            },
            0xE8: {
                0x01: ["FileStore_E8_01", cls.t_skip_bytes, 2, ":number, :string"],
                0x02: ["PrepFilename_E8_02", cls.t_skip_bytes, 0],
            },
            0xEA: ["Unkown_EA", cls.t_skip_bytes, 1],
            0xEB: ["Finish"],
            0xF0: ["Magic88"],
            0xF1: {
                0x00: ["Start0", cls.t_skip_bytes, 1],
                0x01: ["Start1", cls.t_skip_bytes, 1],
                0x02: ["Start2", cls.t_skip_bytes, 1],
                0x03: ["Laser2_Offset", cls.t_laser_offset, 5 + 5, ":abs, :abs", 2],
                0x04: ["Enable_Feeding_F1_04", cls.t_skip_bytes, 1],
            },
            0xF2: {
                0x00: ["Raster_Params? F2_00", cls.t_skip_bytes, 1],
                0x01: ["Raster_Params? F2_01", cls.t_skip_bytes, 1],
                0x02: [
                    "Job_Scale? F2_02 (maybe DPI/scale)",
                    cls.t_skip_bytes,
                    10,
                    "??",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x03: [
                    "Job_Top_Left? F2_03",
                    cls.t_skip_bytes,
                    5 + 5,
                    ":abs, :abs",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x04: [
                    "Job_Bottom_Right? F2_04",
                    cls.t_skip_bytes,
                    5 + 5,
                    ":abs, :abs",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x05: [
                    "Job_Size? F2_05",
                    cls.t_skip_bytes,
                    4 + 5 + 5,
                    "4, :abs, :abs",
                    4,
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x06: [
                    "Job_Offsets? F2_06",
                    cls.t_skip_bytes,
                    5 + 5,
                    ":abs, :abs",
                    cls.arg_abs,
                    cls.arg_abs,
                ],
                0x07: ["Job_Flags? F2_07", cls.t_skip_bytes, 1],
            },
        }

    # ---------------- Decode loop ----------------
    def token_method(self, c):
        consumed, msg = 0, None
        if len(c) == 2:
            return c[1](self)
        if len(c) == 3:
            return c[1](self, c[2])
        if len(c) >= 4:
            consumed, msg = c[1](self, c[2], c[3:])
            if msg is None:
                label = c[3] if isinstance(c[3], str) else ""
                msg = "(" + label + ")" if label else ""
            else:
                label = c[3] if isinstance(c[3], str) else ""
                if label:
                    msg += " (" + label + ")"
        return consumed, msg

    def decode(self, buf: bytes | None = None, *, debug: bool = True) -> None:
        debugfile = sys.stderr
        if debug not in (True, False):
            debug = True
            debugfile = sys.stdout
        if buf is not None:
            self._buf = buf
        pos = -1
        while len(self._buf):
            b0 = self._buf[0]
            self._buf = self._buf[1:]
            pos += 1
            self._current_pos = pos
            tok = self.rd_decoder_table.get(b0)

            if tok:
                if isinstance(tok, dict):
                    if not self._buf:
                        if debug:
                            print(f"{pos:5d}: {b0:02x} ERROR: truncated", file=debugfile)
                        break
                    b1 = self._buf[0]
                    c = tok.get(b1)
                    if c:
                        self._buf = self._buf[1:]
                        pos += 1
                        if isinstance(c, dict):
                            if not self._buf:
                                if debug:
                                    print(
                                        f"{pos:5d}: {b0:02x} {b1:02x} ERROR: truncated",
                                        file=debugfile,
                                    )
                                break
                            b2 = self._buf[0]
                            c2 = c.get(b2)
                            if c2:
                                self._buf = self._buf[1:]
                                pos += 1
                                label = c2[0]
                                self._count_label(label)
                                out = f"{pos:5d}: {b0:02x} {b1:02x} {b2:02x} {label}"
                                consumed, msg = self.token_method(c2)
                                if msg is not None:
                                    out += " " + msg
                                if debug:
                                    print(out, file=debugfile)
                                self._buf = self._buf[consumed:]
                                pos += consumed
                            else:
                                if debug:
                                    print(
                                        f"{pos:5d}: {b0:02x} {b1:02x} {b2:02x} unknown nested token",
                                        file=debugfile,
                                    )
                                self._count_unknown(f"UNKNOWN_{b0:02X}_{b1:02X}_{b2:02X}")
                        else:
                            label = c[0]
                            self._count_label(label)
                            out = f"{pos:5d}: {b0:02x} {b1:02x} {label}"
                            consumed, msg = self.token_method(c)
                            if msg is not None:
                                out += " " + msg
                            if debug:
                                print(out, file=debugfile)
                            self._buf = self._buf[consumed:]
                            pos += consumed
                    else:
                        self._count_unknown(f"UNKNOWN_{b0:02X}_{self._buf[0]:02X}")
                        if debug:
                            print(
                                f"{pos:5d}: {b0:02x} {self._buf[0]:02x} second byte not defined in rd_dec",
                                file=debugfile,
                            )
                else:
                    label = tok[0]
                    self._count_label(label)
                    out = f"{pos:5d}: {b0:02x} {label}"
                    consumed, msg = self.token_method(tok)
                    if msg is not None:
                        out += " " + msg
                    if debug:
                        print(out, file=debugfile)
                    self._buf = self._buf[consumed:]
                    pos += consumed
            else:
                self._count_unknown(f"UNKNOWN_{b0:02X}")
                if debug:
                    print(
                        f"{pos:5d}: {b0:02x} ERROR: ----------- token not found in rd_dec",
                        file=debugfile,
                    )


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Decode and dump Ruida RD files (unswizzle + token decode)."
    )
    ap.add_argument("rd_file", help=".rd file to decode")
    ap.add_argument(
        "--model",
        default=DEFAULT_PROFILE_NAME,
        help=f"Ruida controller profile (default {DEFAULT_PROFILE_NAME})",
    )
    ap.add_argument("--no-summary", action="store_true", help="Skip summary of Z offsets at end")
    ap.add_argument("--summary", action="store_true", help="Add opcode/unknown counts after decode")
    ap.add_argument(
        "--summary-only",
        action="store_true",
        help="Only show opcode/unknown counts (skip per-token dump)",
    )
    args = ap.parse_args()

    parser = RuidaParser(file=args.rd_file, profile=args.model)
    parser.decode(debug=not args.summary_only)
    if not args.no_summary and parser._z_offsets:
        print("\nZ offsets (80 03 signed):")
        for idx, (pos, val, raw, prio) in enumerate(parser._z_offsets, 1):
            print(f"  #{idx}: {val:.3f} mm raw={raw.hex(' ')} layer(prio)={prio} at pos={pos}")
    if not args.no_summary and parser._layer:
        print("\nLayers:")
        for ln, info in sorted(parser._layer.items(), key=lambda kv: str(kv[0])):
            bbox = info.get("bbox", [])
            speed = info.get("speed", None)
            color = info.get("color", None)
            bbox_str = (
                f"[{bbox[0]:.1f}, {bbox[1]:.1f}]–[{bbox[2]:.1f}, {bbox[3]:.1f}]" if bbox else "n/a"
            )
            speed_str = f"{speed} mm/s" if speed is not None else "n/a"
            print(f"  Layer {ln}: speed={speed_str} bbox={bbox_str} color={color or 'n/a'}")
    if not args.no_summary:
        # Crude units hint: compare bbox size vs inch->mm thresholds
        if parser._bbox[2] > -10e8 and parser._bbox[3] > -10e8:
            width = parser._bbox[2] - parser._bbox[0]
            height = parser._bbox[3] - parser._bbox[1]
            print(
                f"\nJob bbox: [{parser._bbox[0]:.3f}, {parser._bbox[1]:.3f}]–[{parser._bbox[2]:.3f}, {parser._bbox[3]:.3f}] (w={width:.3f}mm h={height:.3f}mm)"
            )
            if max(width, height) > 0:
                # If width/25.4 is close to a round number, guess inches
                w_in = width / 25.4
                h_in = height / 25.4
                print(f"   Approx size in inches: {w_in:.3f}in x {h_in:.3f}in")
    if args.summary or args.summary_only:
        print("\nOpcode counts (top 30):")
        for label, count in sorted(parser._opcode_counts.items(), key=lambda kv: (-kv[1], kv[0]))[
            :30
        ]:
            print(f"  {label}: {count}")
        if parser._unknown_counts:
            print("\nUnknown tokens:")
            for label, count in sorted(
                parser._unknown_counts.items(), key=lambda kv: (-kv[1], kv[0])
            ):
                print(f"  {label}: {count}")


if __name__ == "__main__":
    main()
