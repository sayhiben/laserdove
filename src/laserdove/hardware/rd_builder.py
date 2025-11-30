from __future__ import annotations

"""
RD job builder for simple XY move/cut sequences.

This borrows the on-wire structure observed in public Ruida RD examples:
 - full header/body/trailer framing
 - single layer, absolute XY moves (0x88) and cuts (0xA8)
 - optional job Z emitted via 0x80 0x03 using signed mm offsets

It is intentionally small and only covers what our planner emits.
"""

import copy
import math
import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from .ruida_common import encode_abscoord_mm_signed

@dataclass
class RDMove:
    x_mm: float
    y_mm: float
    speed_mm_s: float
    power_pct: float
    is_cut: bool
    z_mm: float | None = None


@dataclass
class _Layer:
    paths: List[List[Tuple[float, float]]]
    bbox: List[List[float]]
    speed: Sequence[float]
    power: Sequence[float]
    color: Sequence[int] = (255, 0, 0)


class _RDJobBuilder:
    """
    Minimal RD job builder (single layer) adapted from community protocol notes.
    Produces the unswizzled RD payload; transport is responsible for swizzling.
    """

    def __init__(self, *, forceabs: int = 100) -> None:
        self._globalbbox: List[List[float]] | None = None
        self._forceabs = forceabs

    # ---------------- Encoding helpers ----------------
    @staticmethod
    def encode_number(num: float, length: int = 5, scale: int = 1000) -> bytes:
        res = []
        nn = int(num * scale)
        while nn > 0:
            res.append(nn & 0x7F)
            nn >>= 7
        while len(res) < length:
            res.append(0)
        res.reverse()
        return bytes(res)

    @staticmethod
    def encode_percent(n: float) -> bytes:
        a = int(n * 0x3FFF * 0.01)
        return bytes([a >> 7, a & 0x7F])

    def encode_relcoord(self, n: float) -> bytes:
        nn = int(n * 1000)
        if nn > 8191 or nn < -8191:
            raise ValueError("relcoord out of range; use abscoords")
        if nn < 0:
            nn += 16384
        return self.encode_number(nn, length=2, scale=1)

    def encode_byte(self, n: int) -> bytes:
        return self.encode_number(n, length=1, scale=1)

    @staticmethod
    def encode_z_offset(offset_mm: float) -> bytes:
        """Encode signed Z offsets (mm) for opcode 0x80 0x03."""
        return encode_abscoord_mm_signed(offset_mm)

    @staticmethod
    def encode_color(color: Sequence[int]) -> bytes:
        cc = ((color[2] & 0xFF) << 16) + ((color[1] & 0xFF) << 8) + (color[0] & 0xFF)
        return _RDJobBuilder.encode_number(cc, scale=1)

    @staticmethod
    def encode_hex(str_val: str) -> bytes:
        str_val = re.sub(r"#.*$", "", str_val, flags=re.MULTILINE)
        return bytes(int(x, 16) for x in str_val.split())

    def enc(self, fmt: str, tupl: Sequence) -> bytes:
        if len(fmt) != len(tupl):
            raise ValueError("format length differs from tuple length")
        ret = b""
        for i, ch in enumerate(fmt):
            if ch == "-":
                ret += self.encode_hex(tupl[i])
            elif ch == "n":
                ret += self.encode_number(tupl[i])
            elif ch == "p":
                ret += self.encode_percent(tupl[i])
            elif ch == "r":
                ret += self.encode_relcoord(tupl[i])
            elif ch == "b":
                ret += self.encode_byte(tupl[i])
            elif ch == "c":
                ret += self.encode_color(tupl[i])
            else:
                raise ValueError(f"unknown format character {ch}")
        return ret

    @staticmethod
    def boundingbox(paths: List[List[Tuple[float, float]]]) -> List[List[float]]:
        xmin = xmax = paths[0][0][0]
        ymin = ymax = paths[0][0][1]
        for path in paths:
            for point in path:
                xmin = min(xmin, point[0])
                xmax = max(xmax, point[0])
                ymin = min(ymin, point[1])
                ymax = max(ymax, point[1])
        return [[xmin, ymin], [xmax, ymax]]

    @staticmethod
    def bbox_combine(bbox1: List[List[float]] | None, bbox2: List[List[float]] | None) -> List[List[float]] | None:
        if bbox1 is None:
            return bbox2
        if bbox2 is None:
            return bbox1
        x0 = min(bbox1[0][0], bbox2[0][0])
        y0 = min(bbox1[0][1], bbox2[0][1])
        x1 = max(bbox1[1][0], bbox2[1][0])
        y1 = max(bbox1[1][1], bbox2[1][1])
        return [[x0, y0], [x1, y1]]

    # ---------------- RD structure builders ----------------
    def header(self, layers: Sequence[_Layer], filename: str) -> bytes:
        bbox: List[List[float]] | None = self._globalbbox
        for layer in layers:
            bbox = self.bbox_combine(bbox, layer.bbox)
        if bbox is None:
            bbox = [[0.0, 0.0], [0.0, 0.0]]
        (xmin, ymin) = bbox[0]
        (xmax, ymax) = bbox[1]

        data = self.encode_hex(
            """
            d8 12           # Red Light on ?
            f0 f1 02 00     # file type ?
            d8 00           # Green Light off ?
            e6 01           # Set Absolute positioning
            """
        )
        data += self.enc("-nn", ["e7 06", 0, 0])  # Feeding
        data += self.enc("-nn", ["e7 03", xmin, ymin])  # Top_Left_E7_07
        data += self.enc("-nn", ["e7 07", xmax, ymax])  # Bottom_Right_E7_07
        data += self.enc("-nn", ["e7 50", xmin, ymin])  # Top_Left_E7_50
        data += self.enc("-nn", ["e7 51", xmax, ymax])  # Bottom_Right_E7_51
        data += self.enc("-nn", ["e7 04 00 01 00 01", 0, 0])  # E7 04 ???
        data += self.enc("-", ["e7 05 00"])  # E7 05 ???

        for lnum, layer in enumerate(layers):
            power = list(layer.power)
            if len(power) % 2:
                raise ValueError("Even number of elements needed in power[]")
            while len(power) < 8:
                power += power[-2:]

            speed = list(layer.speed)
            if isinstance(speed, (float, int)):
                speed = [1000, speed]  # travel, laser
            laserspeed = speed[1]

            data += self.enc("-bn", ["c9 04", lnum, laserspeed])

            data += self.enc(
                "-bp-bp-bp-bp",
                [
                    "c6 31",
                    lnum,
                    power[0],
                    "c6 32",
                    lnum,
                    power[1],
                    "c6 41",
                    lnum,
                    power[2],
                    "c6 42",
                    lnum,
                    power[3],
                ],
            )
            data += self.enc(
                "-bp-bp-bp-bp",
                [
                    "c6 35",
                    lnum,
                    power[4],
                    "c6 36",
                    lnum,
                    power[5],
                    "c6 37",
                    lnum,
                    power[6],
                    "c6 38",
                    lnum,
                    power[7],
                ],
            )

            data += self.enc(
                "-bc-bb-bnn-bnn-bnn-bnn",
                [
                    "ca 06",
                    lnum,
                    layer.color,
                    "ca 41",
                    lnum,
                    0,
                    "e7 52",
                    lnum,
                    layer.bbox[0][0],
                    layer.bbox[0][1],
                    "e7 53",
                    lnum,
                    layer.bbox[1][0],
                    layer.bbox[1][1],
                    "e7 61",
                    lnum,
                    layer.bbox[0][0],
                    layer.bbox[0][1],
                    "e7 62",
                    lnum,
                    layer.bbox[1][0],
                    layer.bbox[1][1],
                ],
            )

        data += self.enc("-b", ["ca 22", len(layers) - 1])
        # Trailing job metadata blocks (bbox copies, offsets, array defaults) mirrored
        # from reference RD captures; kept verbatim because the controller expects them.
        data += self.enc(
            "-----------nn-nn-nn--nn---nn-nn-nn--nn",
            [
                "e7 54 00 00 00 00 00 00",
                "e7 54 01 00 00 00 00 00",
                "e7 55 00 00 00 00 00 00",
                "e7 55 01 00 00 00 00 00",
                "f1 03 00 00 00 00 00 00 00 00 00 00",
                "f1 00 00",
                "f1 01 00",
                "f2 00 00",
                "f2 01 00",
                "f2 02 05 2a 39 1c 41 04 6a 15 08 20",
                "f2 03",
                xmin,
                ymin,
                "f2 04",
                xmax,
                ymax,
                "f2 06",
                xmin,
                ymin,
                "f2 07 00",
                "f2 05 00 01 00 01",
                xmax,
                ymax,
                "ea 00",
                "e7 60 00",
                "e7 13",
                xmin,
                ymin,
                "e7 17",
                xmax,
                ymax,
                "e7 23",
                xmin,
                ymin,
                "e7 24 00",
                "e7 08 00 01 00 01",
                xmax,
                ymax,
            ],
        )
        return data

    def body(self, layers: Sequence[_Layer], *, job_z_mm: float | None = None, air_assist: bool = True) -> bytes:
        def relok(last: Tuple[float, float] | None, point: Tuple[float, float]) -> bool:
            maxrel = 8.191
            if last is None:
                return False
            dx = abs(point[0] - last[0])
            dy = abs(point[1] - last[1])
            return max(dx, dy) <= maxrel

        data = bytearray()

        for lnum, layer in enumerate(layers):
            power = list(layer.power)
            if len(power) % 2:
                raise ValueError("Even number of elements needed in power[]")
            while len(power) < 8:
                power += power[-2:]

            speed = list(layer.speed)
            if isinstance(speed, (float, int)):
                speed = [1000, speed]
            laserspeed = speed[1]

            prolog_flags = """
                        ca 01 30
                        ca 01 10
            """
            if air_assist:
                prolog_flags += "                        ca 01 13\n"

            data.extend(
                self.enc(
                    "-b-",
                    [
                        """
                        ca 01 00
                        ca 02""",
                        lnum,
                        prolog_flags,
                    ],
                )
            )

            # Layer prolog: speed, cut delays, per-laser min/max power, layer flags.
            SPEED_SET = "c9 02"  # set laser speed
            CUT_DELAY_ON = "c6 15 00 00 00 00 00"
            CUT_DELAY_OFF = "c6 16 00 00 00 00 00"
            L1_MIN = "c6 01"
            L1_MAX = "c6 02"
            L2_MIN = "c6 21"
            L2_MAX = "c6 22"
            L3_MIN = "c6 05"
            L3_MAX = "c6 06"
            L4_MIN = "c6 07"
            L4_MAX = "c6 08"
            ENABLE_LAYER = "ca 03 01"
            IO_FLAGS = "ca 10 00"

            data.extend(
                self.enc(
                    "-n---p-p-p-p-p-p-p-p--",
                    [
                        SPEED_SET,
                        laserspeed,
                        CUT_DELAY_ON,
                        CUT_DELAY_OFF,
                        L1_MIN,
                        power[0],
                        L1_MAX,
                        power[1],
                        L2_MIN,
                        power[2],
                        L2_MAX,
                        power[3],
                        L3_MIN,
                        power[4],
                        L3_MAX,
                        power[5],
                        L4_MIN,
                        power[6],
                        L4_MAX,
                        power[7],
                        ENABLE_LAYER,
                        IO_FLAGS,
                    ],
                )
            )

            if job_z_mm is not None:
                data.extend(bytes([0x80, 0x03]))
                data.extend(self.encode_z_offset(job_z_mm))

            relcounter = 0
            last_point: Tuple[float, float] | None = None
            for path in layer.paths:
                travel = True
                for point in path:
                    if relok(last_point, point) and (self._forceabs == 0 or relcounter < self._forceabs):
                        if self._forceabs > 0:
                            relcounter += 1
                        MOVE_REL_X = "8a"
                        MOVE_REL_Y = "8b"
                        MOVE_REL_XY = "89"
                        CUT_REL_X = "aa"
                        CUT_REL_Y = "ab"
                        CUT_REL_XY = "a9"
                        if point[1] == last_point[1]:
                            data += self.enc("-r", [MOVE_REL_X if travel else CUT_REL_X, point[0] - last_point[0]])
                        elif point[0] == last_point[0]:
                            data += self.enc("-r", [MOVE_REL_Y if travel else CUT_REL_Y, point[1] - last_point[1]])
                        else:
                            data += self.enc("-rr", [MOVE_REL_XY if travel else CUT_REL_XY, point[0] - last_point[0], point[1] - last_point[1]])
                    else:
                        MOVE_ABS_XY = "88"
                        CUT_ABS_XY = "a8"
                        relcounter = 0
                        data += self.enc("-nn", [MOVE_ABS_XY if travel else CUT_ABS_XY, point[0], point[1]])
                    last_point = point
                    travel = False
        return bytes(data)

    def trailer(self, odo: Sequence[float] = (0.0, 0.0)) -> bytes:
        return self.enc(
            "-nn-",
            [
                """
                eb e7 00
                da 01 06 20""",
                odo[0] * 0.001,
                odo[0] * 0.001,
                """
                d7
                """,
            ],
        )


def _moves_to_paths(moves: Iterable[RDMove]) -> Tuple[List[List[Tuple[float, float]]], List[List[float]]]:
    paths: List[List[Tuple[float, float]]] = []
    cursor: Tuple[float, float] | None = None
    current_path: List[Tuple[float, float]] | None = None
    cutting = False

    for mv in moves:
        if mv.z_mm is not None:
            # Z-only move does not affect XY path construction.
            continue
        point = (mv.x_mm, mv.y_mm)
        if mv.is_cut:
            if cutting and current_path is not None:
                current_path.append(point)
            else:
                if current_path:
                    paths.append(current_path)
                start_point = cursor if cursor is not None else point
                current_path = [start_point, point] if start_point != point else [point]
                cutting = True
        else:
            if current_path:
                paths.append(current_path)
            current_path = [point]
            cutting = False
        cursor = point

    if current_path:
        paths.append(current_path)

    bbox = _RDJobBuilder.boundingbox(paths) if paths else [[0.0, 0.0], [0.0, 0.0]]
    return paths, bbox


def _compute_odometer(moves: List[RDMove]) -> Tuple[float, float]:
    """
    Compute cut and travel distances in mm (simple segment lengths).
    """
    if not moves:
        return (0.0, 0.0)
    cut = 0.0
    travel = 0.0
    prev_x = moves[0].x_mm
    prev_y = moves[0].y_mm
    for mv in moves[1:]:
        dist = math.hypot(mv.x_mm - prev_x, mv.y_mm - prev_y)
        if mv.is_cut:
            cut += dist
        else:
            travel += dist
        prev_x, prev_y = mv.x_mm, mv.y_mm
    return (cut, travel)


def build_rd_job(
    moves: List[RDMove],
    job_z_mm: float | None = None,
    *,
    filename: str = "LASERDOVE",
    air_assist: bool = True,
) -> bytes:
    """
    Build an unswizzled RD payload for a sequence of moves.
    The optional job_z_mm is a signed Z offset (mm) encoded once with opcode 0x80 0x03.
    Z moves embedded in RDMove.z_mm emit additional 0x80 0x03 commands inline.
    """
    if not moves:
        return b""

    normalized_moves: List[RDMove] = []
    for mv in moves:
        # Treat zero-power cuts as travel to avoid controllers reusing default power.
        is_cut = mv.is_cut and mv.power_pct > 0.0
        power_pct = mv.power_pct if is_cut else 0.0
        normalized_moves.append(
            RDMove(
                x_mm=mv.x_mm,
                y_mm=mv.y_mm,
                speed_mm_s=mv.speed_mm_s,
                power_pct=power_pct,
                is_cut=is_cut,
                z_mm=mv.z_mm,
            )
        )

    paths, bbox = _moves_to_paths(normalized_moves)
    travel_speed = next((mv.speed_mm_s for mv in normalized_moves if not mv.is_cut), normalized_moves[0].speed_mm_s)
    cut_speed = next((mv.speed_mm_s for mv in normalized_moves if mv.is_cut), travel_speed)
    power = next((mv.power_pct for mv in normalized_moves if mv.is_cut), next((mv.power_pct for mv in normalized_moves), 0.0))
    power = max(0.0, power)

    layer = _Layer(
        paths=paths,
        bbox=bbox,
        speed=[travel_speed, cut_speed],
        power=[power, power],
    )
    builder = _RDJobBuilder()
    builder._globalbbox = bbox
    cut_dist, travel_dist = _compute_odometer(normalized_moves)

    header = builder.header([layer], filename=filename)
    # Build a simplified body that preserves move order and injects inline Z offsets.
    data = bytearray()

    prolog_flags = "ca 01 30\nca 01 10\n"
    if air_assist:
        prolog_flags += "ca 01 13\n"
    data.extend(builder.enc("-b-", ["ca 01 00\nca 02", 0, prolog_flags]))

    SPEED_SET = "c9 02"
    CUT_DELAY_ON = "c6 15 00 00 00 00 00"
    CUT_DELAY_OFF = "c6 16 00 00 00 00 00"
    L1_MIN = "c6 01"
    L1_MAX = "c6 02"
    L2_MIN = "c6 21"
    L2_MAX = "c6 22"
    L3_MIN = "c6 05"
    L3_MAX = "c6 06"
    L4_MIN = "c6 07"
    L4_MAX = "c6 08"
    ENABLE_LAYER = "ca 03 01"
    IO_FLAGS = "ca 10 00"

    power_vals = [power, power, power, power, power, power, power, power]
    speed_vals = [travel_speed, cut_speed]

    data.extend(
        builder.enc(
            "-n---p-p-p-p-p-p-p-p--",
            [
                SPEED_SET,
                speed_vals[1],
                CUT_DELAY_ON,
                CUT_DELAY_OFF,
                L1_MIN,
                power_vals[0],
                L1_MAX,
                power_vals[1],
                L2_MIN,
                power_vals[2],
                L2_MAX,
                power_vals[3],
                L3_MIN,
                power_vals[4],
                L3_MAX,
                power_vals[5],
                L4_MIN,
                power_vals[6],
                L4_MAX,
                power_vals[7],
                ENABLE_LAYER,
                IO_FLAGS,
            ],
        )
    )

    def emit_speed(speed: float) -> bytes:
        return builder.enc("-n", [SPEED_SET, speed])

    # Optionally start with a job-level Z offset.
    if job_z_mm is not None:
        data.extend(bytes([0x80, 0x03]))
        data.extend(builder.encode_z_offset(job_z_mm))

    last_speed = None
    for mv in normalized_moves:
        if mv.z_mm is not None:
            data.extend(bytes([0x80, 0x03]))
            data.extend(builder.encode_z_offset(mv.z_mm))
            continue

        if mv.speed_mm_s is not None and (last_speed is None or abs(mv.speed_mm_s - last_speed) > 1e-6):
            data.extend(emit_speed(mv.speed_mm_s))
            last_speed = mv.speed_mm_s

        opcode = "a8" if mv.is_cut else "88"
        data.extend(builder.enc("-nn", [opcode, mv.x_mm, mv.y_mm]))

    body = bytes(data)
    trailer = builder.trailer((cut_dist, travel_dist))
    return header + body + trailer
