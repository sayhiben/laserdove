from __future__ import annotations

import math
from typing import List, Tuple

from laserdove.hardware.ruida_common import unswizzle


class RuidaParserHelpersMixin:
    # ---------------- Segment helpers ----------------
    """Mixin implementing RD parser helpers."""

    def _emit_segment(self, x: float, y: float, *, is_cut: bool) -> None:
        """Emit segment."""
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
                "power_pct": self._current_power_pct if is_cut else 0.0,
                "z": self._current_z,
                "logical_z": self._current_z,
                "air_assist": self._air_assist,
            }
        )
        self._cursor = [x, y]

    # ---------------- Basic helpers ----------------
    def unscramble_bytes(self, data: bytes) -> bytes:
        """Return unscramble bytes."""
        return unswizzle(data, magic=0x88)

    def get_layer(self, n: int) -> dict:
        """Return layer."""
        if n not in self._layer:
            self._layer[n] = {"n": n, "bbox": [0, 0, 0, 0], "laser": {}}
        return self._layer[n]

    def get_laser(self, n: int, lay: int | None = None) -> dict:
        """Return laser."""
        if lay is not None:
            layer = self.get_layer(lay)
            if n not in layer["laser"]:
                layer["laser"][n] = {"n": n, "offset": [0, 0], "layer": lay}
            return layer["laser"][n]
        if n not in self._laser:
            self._laser[n] = {"n": n, "offset": [0, 0]}
        return self._laser[n]

    def new_path(self) -> List[List[float]]:
        """Return new path."""
        p = {"data": [], "n": len(self._paths), "layer": self._layer.get(self._prio, self._prio)}
        self._paths.append(p)
        return p["data"]

    def get_path(self) -> List[List[float]]:
        """Return path."""
        if not self._paths:
            self.new_path().append([0, 0])
        return self._paths[-1]["data"]

    def relative_xy(self, x: float = 0.0, y: float = 0.0) -> List[float]:
        """Return relative xy."""
        if not self._paths:
            self.new_path().append([0, 0])
        current = self._paths[-1]["data"][-1]
        return [current[0] + x, current[1] + y]

    # ---------------- Decoders ----------------
    def decode_number(self, x: bytes) -> float:
        """Decode number."""
        fak = 1
        res = 0
        for b in reversed(x):
            res += fak * b
            fak *= 0x80
        if res > 0x80000000:
            res = res - 0x100000000
        return res * 0.001

    def decode_relcoord(self, x: bytes) -> float:
        """Decode relcoord."""
        r = (x[0] << 7) + x[1]
        if r > 16383 or r < 0:
            raise ValueError("Not a rel coord: " + repr(x[0:2]))
        if r > 8191:
            return 0.001 * (r - 16384)
        return 0.001 * r

    def decode_percent_float(self, x: bytes) -> float:
        """Decode percent float."""
        return ((x[0] << 7) + x[1]) * 100 / 0x3FFF

    def arg_strz(self, off: int = 0) -> Tuple[int, str]:
        """Return arg strz."""
        string = ""
        while self._buf[off] != 0x00:
            string += "%c" % self._buf[off]
            off += 1
        return off + 1, string

    def arg_byte(self, off: int = 0) -> Tuple[int, int]:
        """Return arg byte."""
        return off + 1, self._buf[off]

    def arg_perc(self, off: int = 0) -> Tuple[int, int]:
        """Return arg perc."""
        buf = self._buf[off : off + 2]
        return off + 2, int(self.decode_percent_float(buf) + 0.5)

    def arg_abs(self, off: int = 0) -> Tuple[int, float]:
        """Return arg abs."""
        buf = self._buf[off : off + 5]
        return off + 5, self.decode_number(buf)

    def arg_rel(self, off: int = 0) -> Tuple[int, float]:
        """Return arg rel."""
        buf = self._buf[off : off + 2]
        return off + 2, self.decode_relcoord(buf)

    def arg_color(self, off: int = 0) -> Tuple[int, int]:
        """Return arg color."""
        buf = self._buf[off : off + 5]
        rgb = list(reversed(list(buf)))
        red = rgb[0] + ((rgb[1] & 0x01) << 7)
        green = ((rgb[1] & 0x7E) >> 1) + ((rgb[2] & 0x03) << 6)
        blue = ((rgb[2] & 0x7C) >> 2) + ((rgb[3] & 0x07) << 5)
        return off + 5, ((red << 16) + (green << 8) + blue)
