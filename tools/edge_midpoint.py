#!/usr/bin/env python3
"""
Interactive helper to find the Y midpoint of a board edge on the rotary jig.

Workflow:
  1) Jog the laser head to the top corner of the board edge.
  2) Jog to the bottom corner of the board edge.
  3) The tool computes the midpoint and optionally jogs there.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
import math
from pathlib import Path
import sys
from typing import Any

try:
    from laserdove.hardware.ruida_laser import RuidaLaser
    from laserdove.logging_utils import setup_logging
except ImportError:
    # Allow running directly from the repository without editable install.
    REPO_ROOT = Path(__file__).resolve().parent.parent
    SRC_ROOT = REPO_ROOT / "src"
    for path in (SRC_ROOT, REPO_ROOT):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    from laserdove.hardware.ruida_laser import RuidaLaser
    from laserdove.logging_utils import setup_logging

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - fallback for older Pythons
    import tomli as tomllib  # type: ignore

LOG = logging.getLogger("edge_midpoint")


@dataclass
class MidpointSettings:
    cfg_path: Path | None
    laser_backend: str
    host: str
    port: int
    source_port: int
    timeout_s: float
    magic: int
    rapid_speed_mm_s: float
    dry_run: bool
    force: bool


def _dict_get_nested(data: dict[str, Any], key: str, default: Any = None) -> Any:
    current: Any = data
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def _load_config(path: Path | None) -> tuple[dict[str, Any], Path | None]:
    cfg_data: dict[str, Any] = {}
    cfg_path: Path | None = path
    used_default = False

    if cfg_path is None:
        default_path = Path("config.toml")
        if default_path.exists():
            cfg_path = default_path
            used_default = True

    if cfg_path is not None:
        try:
            cfg_data = _load_toml(cfg_path)
        except FileNotFoundError:
            if not used_default:
                raise SystemExit(f"Config file not found: {cfg_path}")
        except Exception as exc:
            raise SystemExit(f"Failed to load config file {cfg_path}: {exc}") from exc

    return cfg_data, cfg_path


def _format_float(value: float) -> str:
    text = f"{value:.6f}"
    text = text.rstrip("0").rstrip(".")
    return text or "0"


def _update_toml_value(path: Path, section: str, key: str, value: float) -> None:
    text = path.read_text()
    newline = "\n"
    if "\r\n" in text:
        newline = "\r\n"
    lines = text.splitlines(keepends=True)
    target_header = f"[{section}]"
    in_section = False
    section_found = False
    insert_idx: int | None = None
    value_str = _format_float(value)

    for idx, line in enumerate(lines):
        line_no_nl = line.rstrip("\r\n")
        stripped = line_no_nl.strip()
        if stripped.startswith("[") and stripped.endswith("]") and not stripped.startswith("[["):
            if stripped == target_header:
                in_section = True
                section_found = True
                insert_idx = idx + 1
            else:
                if in_section:
                    in_section = False
        if in_section:
            insert_idx = idx + 1
            pre = line_no_nl
            comment = ""
            if "#" in line_no_nl:
                pre, comment_text = line_no_nl.split("#", 1)
                comment = " #" + comment_text.strip()
            if "=" in pre:
                left, _ = pre.split("=", 1)
                if left.strip() == key:
                    indent = left[: len(left) - len(left.lstrip())]
                    updated = f"{indent}{key} = {value_str}{comment}"
                    if line.endswith("\r\n"):
                        updated += "\r\n"
                    elif line.endswith("\n"):
                        updated += "\n"
                    lines[idx] = updated
                    path.write_text("".join(lines))
                    return

    if not section_found:
        if lines and not lines[-1].endswith(("\n", "\r\n")):
            lines[-1] = lines[-1] + newline
        lines.append(f"{target_header}{newline}")
        lines.append(f"{key} = {value_str}{newline}")
    else:
        if insert_idx is None:
            insert_idx = len(lines)
        lines.insert(insert_idx, f"{key} = {value_str}{newline}")

    path.write_text("".join(lines))


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Find the Y midpoint of a board edge by jogging to its corners."
    )
    ap.add_argument(
        "--config",
        type=Path,
        help="Optional TOML config file (defaults to config.toml if present).",
    )
    ap.add_argument("--host", help="Ruida controller host/IP (overrides config).")
    ap.add_argument("--port", type=int, help="Ruida UDP port (default 50200).")
    ap.add_argument(
        "--source-port", type=int, help="Local UDP source port (default 40200)."
    )
    ap.add_argument("--timeout-s", type=float, help="UDP timeout seconds (default 3.0).")
    ap.add_argument(
        "--magic",
        type=lambda x: int(x, 0),
        help="Swizzle magic (default 0x88).",
    )
    ap.add_argument(
        "--rapid-speed-mm-s",
        type=float,
        help="Jog speed for midpoint move (default machine.rapid_speed_mm_s).",
    )
    ap.add_argument(
        "--save-edge-length",
        action="store_true",
        help="Write computed edge_length_mm into [joint] of the config file.",
    )
    ap.add_argument(
        "--edge-length-offset-mm",
        type=float,
        default=0.0,
        help="Offset to apply to computed edge length before saving (default 0).",
    )
    ap.add_argument(
        "--no-jog",
        action="store_true",
        help="Do not jog to the midpoint after measuring.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Log actions without sending UDP packets.",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Run even if backend.laser_backend is dummy in config.",
    )
    ap.add_argument("--log-level", default="INFO", help="Log level (default INFO).")
    return ap


def _resolve_settings(
    args: argparse.Namespace, cfg_data: dict[str, Any], cfg_path: Path | None
) -> MidpointSettings:
    laser_backend = _dict_get_nested(cfg_data, "backend.laser_backend", None)
    if laser_backend is None:
        use_dummy = _dict_get_nested(cfg_data, "backend.use_dummy", None)
        if use_dummy is not None:
            LOG.warning(
                "backend.use_dummy is deprecated; set backend.laser_backend instead"
            )
            laser_backend = "dummy" if bool(use_dummy) else "ruida"
        else:
            laser_backend = "dummy"
    host = _dict_get_nested(cfg_data, "backend.ruida_host", "192.168.1.100")
    port = int(_dict_get_nested(cfg_data, "backend.ruida_port", 50200))
    source_port = int(_dict_get_nested(cfg_data, "backend.ruida_source_port", 40200))
    timeout_s = float(_dict_get_nested(cfg_data, "backend.ruida_timeout_s", 3.0))
    magic = int(_dict_get_nested(cfg_data, "backend.ruida_magic", 0x88))
    rapid_speed = float(_dict_get_nested(cfg_data, "machine.rapid_speed_mm_s", 200.0))

    if args.host is not None:
        host = args.host
    if args.port is not None:
        port = args.port
    if args.source_port is not None:
        source_port = args.source_port
    if args.timeout_s is not None:
        timeout_s = args.timeout_s
    if args.magic is not None:
        magic = args.magic
    if args.rapid_speed_mm_s is not None:
        rapid_speed = args.rapid_speed_mm_s

    if rapid_speed <= 0:
        raise SystemExit("--rapid-speed-mm-s must be > 0.")

    return MidpointSettings(
        cfg_path=cfg_path,
        laser_backend=str(laser_backend).lower(),
        host=str(host),
        port=port,
        source_port=source_port,
        timeout_s=timeout_s,
        magic=magic,
        rapid_speed_mm_s=rapid_speed,
        dry_run=bool(args.dry_run),
        force=bool(args.force),
    )


def _prompt_position(laser: RuidaLaser, label: str) -> tuple[float, float]:
    input(f"Jog the head to the {label} corner, then press Enter...")
    state = laser._wait_for_ready(max_attempts=60, delay_s=0.25, min_stable_s=0.5)
    if state.x_mm is None or state.y_mm is None:
        raise SystemExit("Failed to read machine position from Ruida.")
    LOG.info("%s corner captured: x=%.3f y=%.3f", label.capitalize(), state.x_mm, state.y_mm)
    return state.x_mm, state.y_mm


def main() -> None:
    args = _build_arg_parser().parse_args()
    setup_logging(args.log_level)

    cfg_data, cfg_path = _load_config(args.config)
    settings = _resolve_settings(args, cfg_data, cfg_path)

    if settings.laser_backend == "dummy" and not settings.force:
        raise SystemExit(
            "backend.laser_backend is dummy; refusing to move the head. "
            "Set backend.laser_backend=ruida or pass --force."
        )

    laser = RuidaLaser(
        settings.host,
        port=settings.port,
        source_port=settings.source_port,
        timeout_s=settings.timeout_s,
        dry_run=settings.dry_run,
        magic=settings.magic,
        movement_only=True,
    )

    LOG.info(
        "Config=%s host=%s:%d dry_run=%s rapid_speed=%.1f",
        str(settings.cfg_path) if settings.cfg_path is not None else "none",
        settings.host,
        settings.port,
        settings.dry_run,
        settings.rapid_speed_mm_s,
    )
    if settings.dry_run:
        LOG.warning("Dry-run enabled; positions will not be read from hardware.")

    top_x, top_y = _prompt_position(laser, "top")
    bottom_x, bottom_y = _prompt_position(laser, "bottom")

    mid_x = 0.5 * (top_x + bottom_x)
    mid_y = 0.5 * (top_y + bottom_y)
    dx = bottom_x - top_x
    dy = bottom_y - top_y
    length_y = abs(dy)
    length_edge = math.hypot(dx, dy)
    skew_deg = math.degrees(math.atan2(dx, dy)) if length_edge > 0 else 0.0
    if length_edge <= 0:
        raise SystemExit("Corner positions are identical; cannot compute midpoint.")

    LOG.info("Board edge length (Y projection): %.3f mm", length_y)
    LOG.info("Board edge length (edge distance): %.3f mm", length_edge)
    LOG.info("Edge skew dX=%.3f mm across dY=%.3f mm (tilt %.3f deg)", dx, dy, skew_deg)
    if abs(skew_deg) > 0.25:
        LOG.warning("Edge skew is large; re-square the board for best results.")

    if not args.no_jog:
        LOG.info("Jogging to midpoint: x=%.3f y=%.3f", mid_x, mid_y)
        laser.move(x=mid_x, y=mid_y, speed=settings.rapid_speed_mm_s)

    if args.save_edge_length:
        if settings.cfg_path is None:
            raise SystemExit("--save-edge-length requires a config file.")
        edge_length = length_y + args.edge_length_offset_mm
        if edge_length <= 0:
            raise SystemExit("Computed edge length must be > 0 to save.")
        _update_toml_value(settings.cfg_path, "joint", "edge_length_mm", edge_length)
        LOG.info("Saved joint.edge_length_mm=%.3f to %s", edge_length, settings.cfg_path)


if __name__ == "__main__":
    main()
