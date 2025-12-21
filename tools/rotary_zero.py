#!/usr/bin/env python3
"""
Rotary zero/level helper.

Rotate the rotary axis by a specified number of degrees clockwise or counter-clockwise
so you can level the jig with a digital angle gauge before running jobs.
"""

from __future__ import annotations

import argparse
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
        "--degrees",
        type=float,
        required=True,
        help="Degrees to rotate (positive magnitude).",
    )
    direction = ap.add_mutually_exclusive_group(required=True)
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


def main() -> None:
    args = _build_arg_parser().parse_args()
    setup_logging(args.log_level)

    cfg_data, cfg_path = _load_config(args.config)

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

    if args.degrees <= 0:
        raise SystemExit("--degrees must be > 0 (use --ccw for negative rotation).")
    if speed_dps <= 0:
        raise SystemExit("--speed-dps must be > 0.")
    if rotary_backend not in ("dummy", "real"):
        raise SystemExit(
            f"Invalid rotary backend '{rotary_backend}'; expected 'dummy' or 'real'."
        )
    if str(pin_numbering).lower() not in ("bcm", "board"):
        raise SystemExit("rotary_pin_numbering must be 'bcm' or 'board'.")

    LOG.info(
        "Config=%s backend=%s dry_run=%s steps_per_rev=%s microsteps=%s max_rate_hz=%s",
        str(cfg_path) if cfg_path is not None else "none",
        rotary_backend,
        args.dry_run,
        steps_per_rev,
        microsteps,
        max_step_rate_hz,
    )

    driver = LoggingStepperDriver()
    if not args.dry_run and rotary_backend == "real":
        have_step = any(pin is not None for pin in (step_pin, step_pin_pos))
        have_dir = any(pin is not None for pin in (dir_pin, dir_pin_pos))
        if have_step and have_dir:
            try:
                driver = GPIOStepperDriver(
                    step_pin=step_pin,
                    dir_pin=dir_pin,
                    step_pin_pos=step_pin_pos,
                    dir_pin_pos=dir_pin_pos,
                    enable_pin=enable_pin,
                    alarm_pin=alarm_pin,
                    invert_dir=invert_dir,
                    pin_mode=str(pin_numbering).upper(),
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
    elif rotary_backend != "real":
        LOG.info("Rotary backend '%s' selected; using logging driver.", rotary_backend)

    if not steps_per_rev:
        LOG.warning("rotary_steps_per_rev is %s; no step pulses will be emitted.", steps_per_rev)

    rotary = RealRotary(
        steps_per_rev=steps_per_rev,
        microsteps=microsteps,
        driver=driver,
        max_step_rate_hz=max_step_rate_hz,
    )

    direction = 1.0 if args.cw else -1.0
    delta = direction * args.degrees
    target = rotary.angle + delta
    dir_label = "CW" if args.cw else "CCW"
    LOG.info(
        "Requesting %s rotation: delta=%.3f deg target=%.3f deg speed=%.1f dps",
        dir_label,
        delta,
        target,
        speed_dps,
    )
    rotary.rotate_to(target, speed_dps)

    if hasattr(driver, "cleanup") and callable(getattr(driver, "cleanup")):
        try:
            driver.cleanup()  # type: ignore[attr-defined]
        except Exception:
            LOG.debug("Rotary driver cleanup failed", exc_info=True)


if __name__ == "__main__":
    main()
