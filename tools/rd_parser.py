#!/usr/bin/env python3
"""
RD parser utility (CLI) for inspecting exported .rd files.

Adapted from the reference/ruidaparser.py decoder so we can quickly inspect
layer settings, Z offsets (e.g., 0x80 0x03), and motion commands locally.
"""

from __future__ import annotations

import argparse
import sys
import math
from typing import List, Tuple

from laserdove.hardware.ruida_common import unswizzle


class RuidaParser:
    """
    Minimal RD decoder adapted from reference/ruidaparser.py.
    """

    def __init__(self, buf: bytes | None = None, file: str | None = None) -> None:
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
            l = self.get_layer(lay)
            if n not in l["laser"]:
                l["laser"][n] = {"n": n, "offset": [0, 0], "layer": lay}
            return l["laser"][n]
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
        l = self._buf[0]
        self._prio = l
        return 1, f"t_layer_priority({l})"

    def t_layer_color(self, n: int, desc=None):
        l = self.get_layer(self._buf[0])
        off, c = self.arg_color(1)
        l["color"] = "#%06x" % c
        return off, f"t_layer_color({l['n']}, {l['color']})"

    def t_laser_min_pow(self, n: int, desc=None):
        las = self.get_laser(desc[0])
        off, v = self.arg_perc()
        las[f"pmin{desc[0]}"] = v
        return off, f"t_laser_min_pow({las['n']}, {v}%)"

    def t_laser_max_pow(self, n: int, desc=None):
        las = self.get_laser(desc[0])
        off, v = self.arg_perc()
        las[f"pmax{desc[0]}"] = v
        return off, f"t_laser_max_pow({las['n']}, {v}%)"

    def t_laser_min_pow_lay(self, n: int, desc=None):
        las = self.get_laser(desc[0], desc[1])
        off, v = self.arg_perc(1)
        las[f"pmin{desc[0]}"] = v
        return off, f"t_laser_min_pow_lay({las['n']}, {desc[1]}, {v}%)"

    def t_laser_max_pow_lay(self, n: int, desc=None):
        las = self.get_laser(desc[0], desc[1])
        off, v = self.arg_perc(1)
        las[f"pmax{desc[0]}"] = v
        return off, f"t_laser_max_pow_lay({las['n']}, {desc[1]}, {v}%)"

    def t_cut_through_pow(self, n: int, desc=None):
        off, v = self.arg_perc()
        return off, f"t_cut_through_pow({desc[0]}, {v}%)"

    def t_layer_speed(self, n: int, desc=None):
        off, s = self.arg_abs(1)
        l = self.get_layer(desc[0])
        l["speed"] = s
        return off, f"t_layer_speed({l['n']}, {s}mm)"

    def t_laser_freq(self, n: int, desc=None):
        las = self.get_laser(desc[0])
        off, freq = self.arg_abs(2)
        las["freq"] = freq
        return off, f"t_laser_freq({las['n']}, {freq}kHz)"

    def t_speed_axis(self, n: int, desc=None):
        off, s = self.arg_abs()
        return off, f"t_speed_axis({s}mm/s)"

    def t_job_units_hint(self, n: int, desc=None):
        val = self._buf[0]
        return 1, f"job_units_flag=0x{val:02X} (00=mm?, 01=in?)"

    def t_laser_offset(self, n: int, desc=None):
        las = self.get_laser(desc[1])
        off, x = self.arg_abs()
        off, y = self.arg_abs(off)
        las["offset"][0] = x
        las["offset"][1] = y
        return off, f"t_laser_offset({las['n']}, {x:.8g}mm, {y:.8g}mm)"

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
        l = self.get_layer(self._buf[0])
        off, x = self.arg_abs(1)
        off, y = self.arg_abs(off)
        self._bbox[0] = min(self._bbox[0], x)
        self._bbox[1] = min(self._bbox[1], y)
        l["bbox"][0] = x
        l["bbox"][1] = y
        return off, f"t_lay_top_left({l['n']}, {x:.8g}mm, {y:.8g}mm)"

    def t_lay_bot_right(self, n: int, desc=None):
        l = self.get_layer(self._buf[0])
        off, x = self.arg_abs(1)
        off, y = self.arg_abs(off)
        self._bbox[2] = max(self._bbox[2], x)
        self._bbox[3] = max(self._bbox[3], y)
        l["bbox"][2] = x
        l["bbox"][3] = y
        return off, f"t_lay_bot_right({l['n']}, {x:.8g}mm, {y:.8g}mm)"

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

    # ---------------- Decoder table ----------------
    rd_decoder_table = {
        0x80: {
            0x01: ["Axis_X_Move (80 01)", t_skip_bytes, 5],  # observed as X on 6442
            0x03: ["Axis_Z_Offset (80 03)", t_z_offset_8003, 0, [0]],  # signed abscoord
            0x08: ["Axis_Y_Move (80 08)", t_skip_bytes, 5],  # observed as Y on 6442
        },
        0x88: ["Mov_Abs", t_move_abs, 5 + 5, ":abs, :abs"],
        0x89: ["Mov_Rel", t_move_rel, 2 + 2, ":rel, :rel"],
        0x8A: ["Mov_Horiz", t_move_horiz, 2, ":rel"],
        0x8B: ["Mov_Vert", t_move_vert, 2, ":rel"],
        0xA8: ["Cut_Abs", t_cut_abs, 5 + 5, ":abs, :abs"],
        0xA9: ["Cut_Rel", t_cut_rel, 2 + 2, ":rel, :rel"],
        0xAA: ["Cut_Horiz", t_cut_horiz, 2, ":rel"],
        0xAB: ["Cut_Vert", t_cut_vert, 2, ":rel"],
        0xC0: ["Unknown_C0", t_skip_bytes, 2],
        0xC1: ["Unknown_C1", t_skip_bytes, 2],
        0xC2: ["Unknown_C2", t_skip_bytes, 2],
        0xC3: ["Unknown_C3", t_skip_bytes, 2],
        0xC4: ["Unknown_C4", t_skip_bytes, 2],
        0xC5: ["Unknown_C5", t_skip_bytes, 2],
        0xC6: {
            0x01: ["Laser_1_Min_Pow_C6_01", t_laser_min_pow, 2, ":power", 1],
            0x02: ["Laser_1_Max_Pow_C6_02", t_laser_max_pow, 2, ":power", 1],
            0x05: ["Laser_3_Min_Pow_C6_05", t_laser_min_pow, 2, ":power", 3],
            0x06: ["Laser_3_Max_Pow_C6_06", t_laser_max_pow, 2, ":power", 3],
            0x07: ["Laser_4_Min_Pow_C6_07", t_laser_min_pow, 2, ":power", 4],
            0x08: ["Laser_4_Max_Pow_C6_08", t_laser_max_pow, 2, ":power", 4],
            0x10: ["Dot_time_C6_10", t_skip_bytes, 5, ":sec", arg_abs],
            0x11: ["Unknown_C6_11", t_skip_bytes, 5, ":abs", arg_abs],
            0x12: ["Cut_Open_delay_12", t_skip_bytes, 5, ":ms", arg_abs],
            0x13: ["Cut_Close_delay_13", t_skip_bytes, 5, ":ms", arg_abs],
            0x15: ["Cut_Open_delay_15", t_skip_bytes, 5, ":ms", arg_abs],
            0x16: ["Cut_Close_delay_16", t_skip_bytes, 5, ":ms", arg_abs],
            0x21: ["Laser_2_Min_Pow_C6_21", t_laser_min_pow, 2, ":power", 2],
            0x22: ["Laser_2_Max_Pow_C6_22", t_laser_max_pow, 2, ":power", 2],
            0x31: ["Laser_1_Min_Pow_C6_31", t_laser_min_pow_lay, 1 + 2, ":layer, :power", 1],
            0x32: ["Laser_1_Max_Pow_C6_32", t_laser_max_pow_lay, 1 + 2, ":layer, :power", 1],
            0x35: ["Laser_3_Min_Pow_C6_35", t_laser_min_pow_lay, 1 + 2, ":layer, :power", 3],
            0x36: ["Laser_3_Max_Pow_C6_36", t_laser_max_pow_lay, 1 + 2, ":layer, :power", 3],
            0x37: ["Laser_4_Min_Pow_C6_37", t_laser_min_pow_lay, 1 + 2, ":layer, :power", 4],
            0x38: ["Laser_4_Max_Pow_C6_38", t_laser_max_pow_lay, 1 + 2, ":layer, :power", 4],
            0x41: ["Laser_2_Min_Pow_C6_41", t_laser_min_pow_lay, 1 + 2, ":layer, :power", 2],
            0x42: ["Laser_2_Max_Pow_C6_42", t_laser_max_pow_lay, 1 + 2, ":layer, :power", 2],
            0x50: ["Cut_through_power1", t_cut_through_pow, 2, ":power", 1],
            0x51: ["Cut_through_power2", t_cut_through_pow, 2, ":power", 2],
            0x55: ["Cut_through_power3", t_cut_through_pow, 2, ":power", 3],
            0x56: ["Cut_through_power4", t_cut_through_pow, 2, ":power", 4],
            0x60: ["Laser_Freq", t_laser_freq, 1 + 1 + 5, ":laser, 0x00, :freq"],
        },
        0xC7: ["Unknown_C7", t_skip_bytes, 2],
        0xC8: ["Unknown_C8", t_skip_bytes, 2],
        0xC9: {
            0x02: ["Speed_Laser1 (C9 02)", t_skip_bytes, 5, ":speed", arg_abs],
            0x03: ["Speed_Axis (C9 03)", t_speed_axis, 5, ":speed"],
            0x04: ["Layer_Speed", t_layer_speed, 1 + 5, ":layer, :speed"],
        },
        0xCA: {
            0x12: ["Blow_off", t_skip_bytes, 0],
            0x13: ["Blow_on", t_skip_bytes, 0],
            0x01: ["Flags_CA_01", t_skip_bytes, 1, "flags"],
            0x02: ["Prio", t_layer_priority, 1, ":priority"],
            0x03: ["Unkown_CA_03", t_skip_bytes, 1],
            0x06: ["Layer_Color", t_layer_color, 1 + 5, ":layer, :color"],
            0x10: ["Unkown_CA_10", t_skip_bytes, 1],
            0x22: ["Layer_Count", t_skip_bytes, 1],
            0x41: ["Layer_CA_41", t_skip_bytes, 2, ":layer, -1"],
        },
        0xCC: ["Ack_CC", t_skip_bytes, 0],
        0xD7: ["EOF"],
        0xD8: {0x00: ["Light_RED"], 0x10: ["Unknown_D8_10", t_skip_bytes, 0], 0x11: ["Unknown_D8_11", t_skip_bytes, 0], 0x12: ["UploadFollows_D8_12", t_skip_bytes, 0]},
        0xD9: {
            0x00: ["Direct_Move_X_rel", t_skip_bytes, 1 + 5, ":mm", 1, arg_abs],
            0x01: ["Direct_Move_Y_rel", t_skip_bytes, 1 + 5, ":mm", 1, arg_abs],
            0x02: ["Direct_Move_Z_rel", t_skip_bytes, 1 + 5, ":mm", 1, arg_abs],
        },
        0xDA: {0x00: ["Work_Interval query", t_skip_bytes, 2], 0x01: ["Work_Interval resp1", t_skip_bytes, 2 + 5, "??", 2, arg_abs, arg_abs]},
        0xE5: {0x05: ["Work_Spacing? (E5 05)", t_skip_bytes, 5, "??", arg_abs]},
        0xE6: {0x01: ["Job_Header? (E6 01)"]},
        0xE7: {
            0x00: ["Stop"],
            0x01: ["SetFilename", t_skip_bytes, 0, ":strz", arg_strz],
            0x03: ["Bounding_Box_Top_Left", t_bb_top_left, 5 + 5, ":abs, :abs"],
            0x04: ["Layer_Bbox_Reset? (E7 04)", t_skip_bytes, 4 + 5 + 5, ":abs, :abs", 4, arg_abs, arg_abs],
            0x05: ["Layer_Bbox_Flush? (E7 05)", t_skip_bytes, 1],
            0x06: ["Feeding", t_feeding, 5 + 5, ":abs, :abs"],
            0x07: ["Bounding_Box_Bottom_Right", t_bb_bot_right, 5 + 5, ":abs, :abs"],
            0x08: ["Layer_Bbox_Bottom_Right? (E7 08)", t_skip_bytes, 4 + 5 + 5, ":abs, :abs", 4, arg_abs, arg_abs],
            0x13: ["Layout_Origin? (E7 13)", t_skip_bytes, 5 + 5, ":abs, :abs", arg_abs, arg_abs],
            0x17: ["Layout_Bottom_Right? (E7 17)", t_skip_bytes, 5 + 5, ":abs, :abs", arg_abs, arg_abs],
            0x23: ["Layout_Origin_Alt? (E7 23)", t_skip_bytes, 5 + 5, ":abs, :abs", arg_abs, arg_abs],
            0x24: ["Layout_Flags? (E7 24)", t_skip_bytes, 1],
            0x37: ["Layout_Bbox_Alt? (E7 37)", t_skip_bytes, 5 + 5, "??", arg_abs, arg_abs],
            0x38: ["Job_Units? (E7 38)", t_job_units_hint, 1],
            0x50: ["Bounding_Box_Top_Left", t_bb_top_left, 5 + 5, ":abs, :abs"],
            0x51: ["Bounding_Box_Bottom_Right", t_bb_bot_right, 5 + 5, ":abs, :abs"],
            0x52: ["Layer_Top_Left_E7_52", t_lay_top_left, 1 + 5 + 5, ":layer, :abs, :abs"],
            0x53: ["Layer_Bottom_Right_E7_53", t_lay_bot_right, 1 + 5 + 5, ":layer, :abs, :abs"],
            0x54: ["Pen_Draw_Y", t_skip_bytes, 1 + 5, ":layer, :abs", arg_byte, arg_abs],
            0x55: ["Laser_Y_Offset", t_skip_bytes, 1 + 5, ":layer, :abs", arg_byte, arg_abs],
            0x60: ["Unkown_E7_60", t_skip_bytes, 1],
            0x61: ["Layer_Top_Left_E7_61", t_lay_top_left, 1 + 5 + 5, ":layer, :abs, :abs"],
            0x62: ["Layer_Bottom_Right_E7_62", t_lay_bot_right, 1 + 5 + 5, ":layer, :abs, :abs"],
        },
        0xE8: {0x01: ["FileStore_E8_01", t_skip_bytes, 2, ":number, :string"], 0x02: ["PrepFilename_E8_02", t_skip_bytes, 0]},
        0xEA: ["Unkown_EA", t_skip_bytes, 1],
        0xEB: ["Finish"],
        0xF0: ["Magic88"],
        0xF1: {0x00: ["Start0", t_skip_bytes, 1], 0x01: ["Start1", t_skip_bytes, 1], 0x02: ["Start2", t_skip_bytes, 1], 0x03: ["Laser2_Offset", t_laser_offset, 5 + 5, ":abs, :abs", 2], 0x04: ["Enable_Feeding_F1_04", t_skip_bytes, 1]},
        0xF2: {
            0x00: ["Raster_Params? F2_00", t_skip_bytes, 1],
            0x01: ["Raster_Params? F2_01", t_skip_bytes, 1],
            0x02: ["Job_Scale? F2_02 (maybe DPI/scale)", t_skip_bytes, 10, "??", arg_abs, arg_abs],
            0x03: ["Job_Top_Left? F2_03", t_skip_bytes, 5 + 5, ":abs, :abs", arg_abs, arg_abs],
            0x04: ["Job_Bottom_Right? F2_04", t_skip_bytes, 5 + 5, ":abs, :abs", arg_abs, arg_abs],
            0x05: ["Job_Size? F2_05", t_skip_bytes, 4 + 5 + 5, "4, :abs, :abs", 4, arg_abs, arg_abs],
            0x06: ["Job_Offsets? F2_06", t_skip_bytes, 5 + 5, ":abs, :abs", arg_abs, arg_abs],
            0x07: ["Job_Flags? F2_07", t_skip_bytes, 1],
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
                        out = f"{pos:5d}: {b0:02x} {b1:02x} {c[0]}"
                        consumed, msg = self.token_method(c)
                        if msg is not None:
                            out += " " + msg
                        if debug:
                            print(out, file=debugfile)
                        self._buf = self._buf[consumed:]
                        pos += consumed
                    else:
                        if debug:
                            print(f"{pos:5d}: {b0:02x} {self._buf[0]:02x} second byte not defined in rd_dec", file=debugfile)
                else:
                    out = f"{pos:5d}: {b0:02x} {tok[0]}"
                    consumed, msg = self.token_method(tok)
                    if msg is not None:
                        out += " " + msg
                    if debug:
                        print(out, file=debugfile)
                    self._buf = self._buf[consumed:]
                    pos += consumed
            else:
                if debug:
                    print(f"{pos:5d}: {b0:02x} ERROR: ----------- token not found in rd_dec", file=debugfile)


def main() -> None:
    ap = argparse.ArgumentParser(description="Decode and dump Ruida RD files (unswizzle + token decode).")
    ap.add_argument("rd_file", help=".rd file to decode")
    ap.add_argument("--no-summary", action="store_true", help="Skip summary of Z offsets at end")
    args = ap.parse_args()

    parser = RuidaParser(file=args.rd_file)
    parser.decode(debug=True)
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
            bbox_str = f"[{bbox[0]:.1f}, {bbox[1]:.1f}]–[{bbox[2]:.1f}, {bbox[3]:.1f}]" if bbox else "n/a"
            speed_str = f"{speed} mm/s" if speed is not None else "n/a"
            print(f"  Layer {ln}: speed={speed_str} bbox={bbox_str} color={color or 'n/a'}")
    if not args.no_summary:
        # Crude units hint: compare bbox size vs inch->mm thresholds
        if parser._bbox[2] > -10e8 and parser._bbox[3] > -10e8:
            width = parser._bbox[2] - parser._bbox[0]
            height = parser._bbox[3] - parser._bbox[1]
            print(f"\nJob bbox: [{parser._bbox[0]:.3f}, {parser._bbox[1]:.3f}]–[{parser._bbox[2]:.3f}, {parser._bbox[3]:.3f}] (w={width:.3f}mm h={height:.3f}mm)")
            if max(width, height) > 0:
                # If width/25.4 is close to a round number, guess inches
                w_in = width / 25.4
                h_in = height / 25.4
                print(f"   Approx size in inches: {w_in:.3f}in x {h_in:.3f}in")


if __name__ == "__main__":
    main()
