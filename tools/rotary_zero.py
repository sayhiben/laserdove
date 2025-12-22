#!/usr/bin/env python3
"""
Rotary zero/level helper.

Rotate the rotary axis by a specified number of degrees clockwise or counter-clockwise
so you can level the jig with a digital angle gauge before running jobs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import logging
from pathlib import Path
import sys
from typing import Any

try:
    from laserdove.hardware import RealRotary, LoggingStepperDriver, GPIOStepperDriver
    from laserdove.logging_utils import setup_logging
except ImportError:
    # Allow running directly from the repository without editable install.
    REPO_ROOT = Path(__file__).resolve().parent.parent
    SRC_ROOT = REPO_ROOT / "src"
    for path in (SRC_ROOT, REPO_ROOT):
        path_str = str(path)
        if path_str not in sys.path:
            sys.path.insert(0, path_str)
    from laserdove.hardware import RealRotary, LoggingStepperDriver, GPIOStepperDriver
    from laserdove.logging_utils import setup_logging

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover - fallback for older Pythons
    import tomli as tomllib  # type: ignore

LOG = logging.getLogger("rotary_zero")


@dataclass
class RotarySettings:
    cfg_path: Path | None
    rotary_backend: str
    steps_per_rev: float | None
    microsteps: int | None
    step_pin: int | None
    dir_pin: int | None
    step_pin_pos: int | None
    dir_pin_pos: int | None
    enable_pin: int | None
    alarm_pin: int | None
    invert_dir: bool
    pin_numbering: str
    max_step_rate_hz: float | None
    speed_dps: float
    zero_deg: float
    dry_run: bool


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


def _build_arg_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Rotate the rotary axis by a specified number of degrees (CW/CCW) to level it."
    )
    ap.add_argument(
        "--config",
        type=Path,
        help="Optional TOML config file (defaults to config.toml if present).",
    )
    ap.add_argument(
        "--interactive",
        action="store_true",
        help="Launch the interactive zeroing UI.",
    )
    ap.add_argument(
        "--degrees",
        type=float,
        help="Degrees to rotate (positive magnitude, requires --cw or --ccw).",
    )
    direction = ap.add_mutually_exclusive_group(required=False)
    direction.add_argument("--cw", action="store_true", help="Rotate clockwise by --degrees.")
    direction.add_argument(
        "--ccw",
        action="store_true",
        help="Rotate counter-clockwise by --degrees.",
    )
    ap.add_argument(
        "--speed-dps",
        type=float,
        help="Rotation speed in degrees/sec (defaults to jig.rotation_speed_dps or 30).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Log the requested move without driving GPIO.",
    )
    ap.add_argument("--log-level", default="INFO", help="Log level (default INFO).")

    ap.add_argument("--rotary-backend", choices=["dummy", "real"], help="Rotary backend to use.")
    ap.add_argument(
        "--rotary-steps-per-rev",
        type=float,
        help="Pulses per revolution from driver (same as backend.rotary_steps_per_rev).",
    )
    ap.add_argument("--rotary-microsteps", type=int, help="Microsteps per full step.")
    ap.add_argument("--rotary-step-pin", type=int, help="GPIO pin for STEP (negative).")
    ap.add_argument("--rotary-dir-pin", type=int, help="GPIO pin for DIR (negative).")
    ap.add_argument("--rotary-step-pin-pos", type=int, help="GPIO pin for STEP+ (optional).")
    ap.add_argument("--rotary-dir-pin-pos", type=int, help="GPIO pin for DIR+ (optional).")
    ap.add_argument("--rotary-enable-pin", type=int, help="GPIO pin for ENABLE (optional).")
    ap.add_argument("--rotary-alarm-pin", type=int, help="GPIO pin for ALARM input (optional).")
    ap.add_argument("--rotary-invert-dir", action="store_true", help="Invert DIR output.")
    ap.add_argument(
        "--rotary-pin-numbering",
        choices=["bcm", "board"],
        help="Pin numbering scheme for rotary GPIO (BCM vs physical).",
    )
    ap.add_argument(
        "--rotary-max-step-rate-hz",
        type=float,
        help="Cap rotary step pulse rate (Hz).",
    )
    return ap


def _resolve_rotary_backend(cfg_data: dict[str, Any]) -> str:
    use_dummy = bool(_dict_get_nested(cfg_data, "backend.use_dummy", True))
    rotary_backend = _dict_get_nested(cfg_data, "backend.rotary_backend", None)
    if rotary_backend is None:
        rotary_backend = "dummy" if use_dummy else "real"
    return rotary_backend


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


def _resolve_settings(
    args: argparse.Namespace, cfg_data: dict[str, Any], cfg_path: Path | None
) -> RotarySettings:
    rotary_backend = _resolve_rotary_backend(cfg_data)
    if args.rotary_backend is not None:
        rotary_backend = args.rotary_backend

    steps_per_rev = _dict_get_nested(cfg_data, "backend.rotary_steps_per_rev", 4000.0)
    microsteps = _dict_get_nested(cfg_data, "backend.rotary_microsteps", None)
    step_pin = _dict_get_nested(cfg_data, "backend.rotary_step_pin", None)
    dir_pin = _dict_get_nested(cfg_data, "backend.rotary_dir_pin", None)
    step_pin_pos = _dict_get_nested(cfg_data, "backend.rotary_step_pin_pos", 11)
    dir_pin_pos = _dict_get_nested(cfg_data, "backend.rotary_dir_pin_pos", 13)
    enable_pin = _dict_get_nested(cfg_data, "backend.rotary_enable_pin", None)
    alarm_pin = _dict_get_nested(cfg_data, "backend.rotary_alarm_pin", None)
    invert_dir = bool(_dict_get_nested(cfg_data, "backend.rotary_invert_dir", False))
    pin_numbering = _dict_get_nested(cfg_data, "backend.rotary_pin_numbering", "board")
    max_step_rate_hz = _dict_get_nested(cfg_data, "backend.rotary_max_step_rate_hz", 500.0)
    speed_dps = _dict_get_nested(cfg_data, "jig.rotation_speed_dps", 30.0)
    zero_deg = float(_dict_get_nested(cfg_data, "jig.rotation_zero_deg", 0.0))

    if args.rotary_steps_per_rev is not None:
        steps_per_rev = args.rotary_steps_per_rev
    if args.rotary_microsteps is not None:
        microsteps = args.rotary_microsteps
    if args.rotary_step_pin is not None:
        step_pin = args.rotary_step_pin
    if args.rotary_dir_pin is not None:
        dir_pin = args.rotary_dir_pin
    if args.rotary_step_pin_pos is not None:
        step_pin_pos = args.rotary_step_pin_pos
    if args.rotary_dir_pin_pos is not None:
        dir_pin_pos = args.rotary_dir_pin_pos
    if args.rotary_enable_pin is not None:
        enable_pin = args.rotary_enable_pin
    if args.rotary_alarm_pin is not None:
        alarm_pin = args.rotary_alarm_pin
    if args.rotary_invert_dir:
        invert_dir = True
    if args.rotary_pin_numbering is not None:
        pin_numbering = args.rotary_pin_numbering
    if args.rotary_max_step_rate_hz is not None:
        max_step_rate_hz = args.rotary_max_step_rate_hz
    if args.speed_dps is not None:
        speed_dps = args.speed_dps

    if pin_numbering is None:
        pin_numbering = "board"
    if speed_dps is None:
        speed_dps = 30.0

    if speed_dps <= 0:
        raise SystemExit("--speed-dps must be > 0.")
    if rotary_backend not in ("dummy", "real"):
        raise SystemExit(f"Invalid rotary backend '{rotary_backend}'; expected 'dummy' or 'real'.")
    if str(pin_numbering).lower() not in ("bcm", "board"):
        raise SystemExit("rotary_pin_numbering must be 'bcm' or 'board'.")

    return RotarySettings(
        cfg_path=cfg_path,
        rotary_backend=rotary_backend,
        steps_per_rev=steps_per_rev,
        microsteps=microsteps,
        step_pin=step_pin,
        dir_pin=dir_pin,
        step_pin_pos=step_pin_pos,
        dir_pin_pos=dir_pin_pos,
        enable_pin=enable_pin,
        alarm_pin=alarm_pin,
        invert_dir=invert_dir,
        pin_numbering=str(pin_numbering).lower(),
        max_step_rate_hz=max_step_rate_hz,
        speed_dps=speed_dps,
        zero_deg=zero_deg,
        dry_run=bool(getattr(args, "dry_run", False)),
    )


def _build_rotary(settings: RotarySettings) -> tuple[RealRotary, object]:
    driver: object = LoggingStepperDriver()
    if not settings.dry_run and settings.rotary_backend == "real":
        have_step = any(pin is not None for pin in (settings.step_pin, settings.step_pin_pos))
        have_dir = any(pin is not None for pin in (settings.dir_pin, settings.dir_pin_pos))
        if have_step and have_dir:
            try:
                driver = GPIOStepperDriver(
                    step_pin=settings.step_pin,
                    dir_pin=settings.dir_pin,
                    step_pin_pos=settings.step_pin_pos,
                    dir_pin_pos=settings.dir_pin_pos,
                    enable_pin=settings.enable_pin,
                    alarm_pin=settings.alarm_pin,
                    invert_dir=settings.invert_dir,
                    pin_mode=str(settings.pin_numbering).upper(),
                )
            except Exception as exc:
                LOG.warning(
                    "Failed to initialize GPIO rotary driver; using logging driver instead: %s",
                    exc,
                )
        else:
            LOG.warning(
                "Rotary backend 'real' selected but step/dir pins not configured; "
                "using logging driver."
            )
    elif settings.rotary_backend != "real":
        LOG.info("Rotary backend '%s' selected; using logging driver.", settings.rotary_backend)

    if not settings.steps_per_rev:
        LOG.warning(
            "rotary_steps_per_rev is %s; no step pulses will be emitted.",
            settings.steps_per_rev,
        )

    rotary = RealRotary(
        steps_per_rev=settings.steps_per_rev,
        microsteps=settings.microsteps,
        driver=driver,
        max_step_rate_hz=settings.max_step_rate_hz,
    )
    return rotary, driver


def _compute_step_deg(steps_per_rev: float | None, microsteps: int | None) -> float:
    if steps_per_rev and steps_per_rev > 0:
        micro = microsteps or 1
        if micro <= 0:
            micro = 1
        return 360.0 / (steps_per_rev * micro)
    return 0.1


def _interactive_ui(rotary: RealRotary, settings: RotarySettings) -> None:
    try:
        import curses
    except Exception as exc:
        raise SystemExit(f"Interactive mode requires curses: {exc}") from exc

    step_deg = _compute_step_deg(settings.steps_per_rev, settings.microsteps)
    zero_deg = settings.zero_deg
    status = "Ready."

    def rotate_to(target: float) -> bool:
        nonlocal status
        try:
            rotary.rotate_to(target, settings.speed_dps)
            return True
        except Exception as exc:
            status = f"Rotate failed: {exc}"
            return False

    def rotate_by(delta: float) -> None:
        nonlocal status
        target = rotary.angle + delta
        if rotate_to(target):
            status = f"Moved {delta:+.4f} deg -> {rotary.angle:.4f} deg"

    def store_zero() -> None:
        nonlocal zero_deg, status
        zero_deg = rotary.angle
        if settings.cfg_path is None:
            status = "Zero stored in session only (no config file loaded)."
            return
        try:
            _update_toml_value(settings.cfg_path, "jig", "rotation_zero_deg", zero_deg)
            status = f"Zero saved to {settings.cfg_path}"
        except Exception as exc:
            status = f"Failed to save zero: {exc}"

    def go_zero() -> None:
        nonlocal status
        if rotate_to(zero_deg):
            status = f"Moved to zero {zero_deg:.4f} deg"

    def render(stdscr: Any, input_buffer: str) -> None:
        stdscr.clear()
        max_y, max_x = stdscr.getmaxyx()
        cfg_label = str(settings.cfg_path) if settings.cfg_path is not None else "none"
        lines = [
            "Rotary zeroing (interactive)",
            f"Config: {cfg_label}  Backend: {settings.rotary_backend}  Dry-run: {settings.dry_run}",
            ("Speed: {:.2f} dps  Steps/rev: {}  Microsteps: {}  Step: {:.6f} deg").format(
                settings.speed_dps,
                settings.steps_per_rev,
                settings.microsteps or 1,
                step_deg,
            ),
            f"Current angle: {rotary.angle:.4f} deg  Stored zero: {zero_deg:.4f} deg",
            "",
            "Delta degrees (Enter to rotate): " + input_buffer,
            "Keys: [ ] step, { } x5 step, z go zero, s save zero, q quit",
            "Status: " + status,
        ]
        for idx, line in enumerate(lines):
            if idx >= max_y - 1:
                break
            stdscr.addstr(idx, 0, line[: max_x - 1])
        input_row = 5
        cursor_x = len("Delta degrees (Enter to rotate): ") + len(input_buffer)
        if input_row < max_y:
            stdscr.move(input_row, min(cursor_x, max_x - 1))
        stdscr.refresh()

    def loop(stdscr: Any) -> None:
        nonlocal status
        try:
            curses.curs_set(1)
        except Exception:
            pass
        stdscr.nodelay(False)
        stdscr.keypad(True)
        input_buffer = ""

        while True:
            render(stdscr, input_buffer)
            key = stdscr.getch()
            if key in (ord("q"), ord("Q"), 27):
                break
            if key in (curses.KEY_ENTER, 10, 13):
                text = input_buffer.strip()
                if not text:
                    input_buffer = ""
                    continue
                try:
                    delta = float(text)
                except ValueError:
                    status = f"Invalid number: {text}"
                else:
                    rotate_by(delta)
                input_buffer = ""
                continue
            if key in (curses.KEY_BACKSPACE, 127, 8):
                input_buffer = input_buffer[:-1]
                continue
            if key == ord("["):
                rotate_by(-step_deg)
                continue
            if key == ord("]"):
                rotate_by(step_deg)
                continue
            if key == ord("{"):
                rotate_by(-step_deg * 5.0)
                continue
            if key == ord("}"):
                rotate_by(step_deg * 5.0)
                continue
            if key in (ord("s"), ord("S")):
                store_zero()
                continue
            if key in (ord("z"), ord("Z")):
                go_zero()
                continue
            if key == curses.KEY_RESIZE:
                continue
            if 0 <= key <= 255:
                ch = chr(key)
                if ch.isdigit() or ch in ".-+eE":
                    input_buffer += ch

    curses.wrapper(loop)


def _should_run_interactive(args: argparse.Namespace) -> bool:
    if getattr(args, "interactive", False):
        return True
    if args.degrees is None and not args.cw and not args.ccw:
        return True
    return False


def main() -> None:
    args = _build_arg_parser().parse_args()
    interactive = _should_run_interactive(args)
    setup_logging(args.log_level, stream=sys.stderr if interactive else None)

    cfg_data, cfg_path = _load_config(args.config)
    settings = _resolve_settings(args, cfg_data, cfg_path)

    LOG.info(
        "Config=%s backend=%s dry_run=%s steps_per_rev=%s microsteps=%s max_rate_hz=%s",
        str(settings.cfg_path) if settings.cfg_path is not None else "none",
        settings.rotary_backend,
        settings.dry_run,
        settings.steps_per_rev,
        settings.microsteps,
        settings.max_step_rate_hz,
    )

    rotary, driver = _build_rotary(settings)
    try:
        if interactive:
            _interactive_ui(rotary, settings)
        else:
            if args.degrees is None or not (args.cw or args.ccw):
                raise SystemExit("--degrees and --cw/--ccw are required unless --interactive.")
            if args.degrees <= 0:
                raise SystemExit("--degrees must be > 0 (use --ccw for negative rotation).")
            direction = 1.0 if args.cw else -1.0
            delta = direction * args.degrees
            target = rotary.angle + delta
            dir_label = "CW" if args.cw else "CCW"
            LOG.info(
                "Requesting %s rotation: delta=%.3f deg target=%.3f deg speed=%.1f dps",
                dir_label,
                delta,
                target,
                settings.speed_dps,
            )
            rotary.rotate_to(target, settings.speed_dps)
    finally:
        if hasattr(driver, "cleanup") and callable(getattr(driver, "cleanup")):
            try:
                driver.cleanup()  # type: ignore[attr-defined]
            except Exception:
                LOG.debug("Rotary driver cleanup failed", exc_info=True)


if __name__ == "__main__":
    main()
