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
from typing import Dict, List

from laserdove.hardware.rd_commands import (
    DEFAULT_PROFILE_NAME,
    RuidaProfile,
    command_table_for,
    get_profile,
    merge_protocol_tables,
)

from .rd_parser_decode import RuidaParserDecodeMixin
from .rd_parser_handlers import RuidaParserHandlersMixin
from .rd_parser_helpers import RuidaParserHelpersMixin


class RuidaParser(RuidaParserHelpersMixin, RuidaParserHandlersMixin, RuidaParserDecodeMixin):
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
        """Initialize RuidaParser."""
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
        self._current_power_pct: float = 0.0
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


def main() -> None:
    """CLI entry point."""
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
        running = 0.0
        for idx, (pos, val, raw, prio) in enumerate(parser._z_offsets, 1):
            running += val
            print(
                f"  #{idx}: Δ{val:+.3f} mm -> {running:+.3f} mm raw={raw.hex(' ')} "
                f"layer(prio)={prio} at pos={pos}"
            )
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
                f"\nJob bbox: [{parser._bbox[0]:.3f}, {parser._bbox[1]:.3f}]–"
                f"[{parser._bbox[2]:.3f}, {parser._bbox[3]:.3f}] (w={width:.3f}mm h={height:.3f}mm)"
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
