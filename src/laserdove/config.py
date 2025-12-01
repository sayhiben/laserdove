# config.py
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

import logging

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore

from .model import JointParams, JigParams, MachineParams

log = logging.getLogger(__name__)


@dataclass
class RunConfig:
    joint_params: JointParams
    jig_params: JigParams
    machine_params: MachineParams
    mode: str
    dry_run: bool
    dry_run_rd: bool
    backend_use_dummy: bool
    backend_host: str
    backend_port: int
    ruida_magic: int
    ruida_timeout_s: float
    ruida_source_port: int
    rotary_steps_per_rev: float
    rotary_microsteps: Optional[int]
    rotary_step_pin: Optional[int]
    rotary_dir_pin: Optional[int]
    rotary_step_pin_pos: Optional[int]
    rotary_dir_pin_pos: Optional[int]
    rotary_enable_pin: Optional[int]
    rotary_alarm_pin: Optional[int]
    rotary_invert_dir: bool
    rotary_max_step_rate_hz: Optional[float]
    rotary_pin_numbering: str
    simulate: bool
    laser_backend: str
    rotary_backend: str
    movement_only: bool
    save_rd_dir: Optional[Path]
    reset_only: bool


def build_arg_parser() -> argparse.ArgumentParser:
    """
    Build the CLI argument parser for planning and execution flags.

    Returns:
        Configured argparse.ArgumentParser instance.
    """
    p = argparse.ArgumentParser(
        description="Dovetail joint planner and driver for rotary jig",
    )
    p.add_argument(
        "--config",
        type=Path,
        help="TOML config file (defaults to config.toml if present)",
    )
    p.add_argument(
        "--mode", choices=["tails", "pins", "both"], default="both", help="Which board to plan"
    )
    p.add_argument(
        "--dry-run", action="store_true", help="Do not talk to hardware; just print plan"
    )
    p.add_argument(
        "--reset",
        action="store_true",
        help="Skip planning and just zero rotary/head with laser off",
    )
    p.add_argument(
        "--simulate",
        action="store_true",
        help="Run commands against the simulated backend with a Tkinter visualization",
    )
    p.add_argument(
        "--movement-only",
        action="store_true",
        help="Clamp laser power to 0 while still issuing motion to hardware",
    )
    p.add_argument(
        "--air-assist",
        dest="air_assist",
        action="store_true",
        help="Enable air assist (default)",
    )
    p.add_argument(
        "--no-air-assist",
        dest="air_assist",
        action="store_false",
        help="Disable air assist in RD jobs",
    )
    p.set_defaults(air_assist=None)
    p.add_argument(
        "--z-positive-bed-up",
        dest="z_positive_moves_bed_up",
        action="store_true",
        help="Interpret Z+ as moving the bed up (closer to the head; default)",
    )
    p.add_argument(
        "--z-positive-bed-down",
        dest="z_positive_moves_bed_up",
        action="store_false",
        help="Interpret Z+ as moving the bed down (away from the head)",
    )
    p.set_defaults(z_positive_moves_bed_up=None)

    # Common overrides
    p.add_argument("--edge-length-mm", type=float)
    p.add_argument("--thickness-mm", type=float)
    p.add_argument("--num-tails", type=int)
    p.add_argument("--dovetail-angle-deg", type=float)
    p.add_argument("--tail-width-mm", type=float)
    p.add_argument("--clearance-mm", type=float)
    p.add_argument("--kerf-tail-mm", type=float)
    p.add_argument("--kerf-pin-mm", type=float)
    p.add_argument("--axis-offset-mm", type=float)
    # Ruida UDP tuning
    p.add_argument("--ruida-timeout-s", type=float, help="UDP ACK timeout seconds for Ruida")
    p.add_argument("--ruida-source-port", type=int, help="Local UDP source port (default 40200)")
    # Rotary tuning
    p.add_argument(
        "--rotary-steps-per-rev", type=float, help="Full steps per revolution (default 200)"
    )
    p.add_argument("--rotary-microsteps", type=int, help="Microsteps per full step (driver DIP)")
    p.add_argument("--rotary-step-pin", type=int, help="BCM pin for STEP (real rotary only)")
    p.add_argument("--rotary-dir-pin", type=int, help="BCM pin for DIR (real rotary only)")
    p.add_argument(
        "--rotary-step-pin-pos",
        type=int,
        help="BCM pin for STEP + (optional; defaults to held high)",
    )
    p.add_argument(
        "--rotary-dir-pin-pos", type=int, help="BCM pin for DIR + (optional; defaults to held high)"
    )
    p.add_argument(
        "--rotary-enable-pin", type=int, help="BCM pin for ENABLE (optional, active low)"
    )
    p.add_argument("--rotary-alarm-pin", type=int, help="BCM pin for ALARM input (optional)")
    p.add_argument(
        "--rotary-invert-dir", action="store_true", help="Invert DIR output (real rotary)"
    )
    p.add_argument(
        "--rotary-pin-numbering",
        choices=["bcm", "board"],
        default="board",
        help="Pin numbering scheme for rotary GPIO (BCM vs physical)",
    )
    p.add_argument("--rotary-max-step-rate-hz", type=float, help="Cap rotary step pulse rate (Hz)")
    p.add_argument(
        "--dry-run-rd",
        action="store_true",
        help="Build RD jobs and log them without talking to Ruida (overrides --dry-run behavior)",
    )
    p.add_argument(
        "--save-rd-dir",
        type=Path,
        help="Directory to save generated (scrambled) RD jobs for inspection",
    )
    # Backend selection
    p.add_argument("--laser-backend", choices=["dummy", "ruida"], help="Laser backend to use")
    p.add_argument("--rotary-backend", choices=["dummy", "real"], help="Rotary backend to use")

    p.add_argument("--log-level", default="INFO")
    return p


def _load_toml(path: Path) -> dict:
    """
    Load a TOML config file.

    Args:
        path: Path to the TOML file.

    Returns:
        Parsed dictionary.

    Raises:
        FileNotFoundError: If the file is missing.
        tomllib/TOMLDecodeError: On parse errors.
    """
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def _dict_get_nested(data: dict, key: str, default=None):
    """
    Fetch a dotted-path value from a nested dict.

    Args:
        data: Mapping to search.
        key: Dot-separated key path.
        default: Fallback if key is absent.

    Returns:
        Retrieved value or default.
    """
    parts = key.split(".")
    current_level = data
    for part in parts[:-1]:
        current_level = current_level.get(part, {})
    return current_level.get(parts[-1], default)


def load_backend_config(cfg_data: dict) -> tuple[bool, str, int, int]:
    """
    Return (use_dummy, ruida_host, ruida_port, ruida_magic)

    Args:
        cfg_data: Parsed config dictionary.

    Returns:
        Tuple of (use_dummy_backend, ruida_host, ruida_port, swizzle_magic).
    """
    use_dummy = _dict_get_nested(cfg_data, "backend.use_dummy", True)
    host = _dict_get_nested(cfg_data, "backend.ruida_host", "192.168.1.100")
    port = _dict_get_nested(cfg_data, "backend.ruida_port", 50200)
    magic = _dict_get_nested(cfg_data, "backend.ruida_magic", 0x88)
    return use_dummy, host, port, magic


def load_config_and_args(args: argparse.Namespace) -> RunConfig:
    """
    Merge CLI args with TOML config into a RunConfig.

    Args:
        args: Parsed argparse namespace.

    Returns:
        RunConfig containing joint/jig/machine settings and backend choices.

    Raises:
        SystemExit: On missing/invalid config when explicitly requested.
    """
    cfg_data: dict = {}
    cfg_path: Path | None = args.config
    used_default = False

    # Default: try config.toml if no explicit --config was provided
    if cfg_path is None:
        default_path = Path("config.toml")
        if default_path.exists():
            cfg_path = default_path
            used_default = True

    if cfg_path is not None:
        try:
            cfg_data = _load_toml(cfg_path)
        except FileNotFoundError:
            # Explicit --config must exist; default config.toml may be absent
            if not used_default:
                raise SystemExit(f"Config file not found: {cfg_path}")
        except Exception as e:  # TOML parse errors, permission issues, etc.
            raise SystemExit(f"Failed to load config file {cfg_path}: {e}") from e

    joint_params = JointParams(
        thickness_mm=_dict_get_nested(cfg_data, "joint.thickness_mm", 6.35),
        edge_length_mm=_dict_get_nested(cfg_data, "joint.edge_length_mm", 100.0),
        dovetail_angle_deg=_dict_get_nested(cfg_data, "joint.dovetail_angle_deg", 8.0),
        num_tails=_dict_get_nested(cfg_data, "joint.num_tails", 3),
        tail_outer_width_mm=_dict_get_nested(cfg_data, "joint.tail_outer_width_mm", 20.0),
        tail_depth_mm=_dict_get_nested(cfg_data, "joint.tail_depth_mm", 6.35),
        socket_depth_mm=_dict_get_nested(cfg_data, "joint.socket_depth_mm", 6.6),
        clearance_mm=_dict_get_nested(cfg_data, "joint.clearance_mm", 0.05),
        kerf_tail_mm=_dict_get_nested(cfg_data, "joint.kerf_tail_mm", 0.15),
        kerf_pin_mm=_dict_get_nested(cfg_data, "joint.kerf_pin_mm", 0.15),
    )

    jig_params = JigParams(
        axis_to_origin_mm=_dict_get_nested(cfg_data, "jig.axis_to_origin_mm", 30.0),
        rotation_zero_deg=_dict_get_nested(cfg_data, "jig.rotation_zero_deg", 0.0),
        rotation_speed_dps=_dict_get_nested(cfg_data, "jig.rotation_speed_dps", 30.0),
    )

    machine_params = MachineParams(
        cut_speed_tail_mm_s=_dict_get_nested(cfg_data, "machine.cut_speed_tail_mm_s", 10.0),
        cut_speed_pin_mm_s=_dict_get_nested(cfg_data, "machine.cut_speed_pin_mm_s", 8.0),
        rapid_speed_mm_s=_dict_get_nested(cfg_data, "machine.rapid_speed_mm_s", 200.0),
        z_speed_mm_s=_dict_get_nested(cfg_data, "machine.z_speed_mm_s", 5.0),
        cut_power_tail_pct=_dict_get_nested(cfg_data, "machine.cut_power_tail_pct", 60.0),
        cut_power_pin_pct=_dict_get_nested(cfg_data, "machine.cut_power_pin_pct", 65.0),
        travel_power_pct=_dict_get_nested(cfg_data, "machine.travel_power_pct", 0.0),
        air_assist=bool(_dict_get_nested(cfg_data, "machine.air_assist", True)),
        z_positive_moves_bed_up=bool(
            _dict_get_nested(cfg_data, "machine.z_positive_moves_bed_up", True)
        ),
        z_zero_tail_mm=_dict_get_nested(cfg_data, "machine.z_zero_tail_mm", 0.0),
        z_zero_pin_mm=_dict_get_nested(cfg_data, "machine.z_zero_pin_mm", 0.0),
    )

    # CLI overrides
    if args.edge_length_mm is not None:
        joint_params.edge_length_mm = args.edge_length_mm
    if args.thickness_mm is not None:
        joint_params.thickness_mm = args.thickness_mm
        joint_params.tail_depth_mm = joint_params.thickness_mm
    if args.num_tails is not None:
        joint_params.num_tails = args.num_tails
    if args.dovetail_angle_deg is not None:
        joint_params.dovetail_angle_deg = args.dovetail_angle_deg
    if args.tail_width_mm is not None:
        joint_params.tail_outer_width_mm = args.tail_width_mm
    if args.clearance_mm is not None:
        joint_params.clearance_mm = args.clearance_mm
    if args.kerf_tail_mm is not None:
        joint_params.kerf_tail_mm = args.kerf_tail_mm
    if args.kerf_pin_mm is not None:
        joint_params.kerf_pin_mm = args.kerf_pin_mm
    if args.axis_offset_mm is not None:
        jig_params.axis_to_origin_mm = args.axis_offset_mm
    if getattr(args, "air_assist", None) is not None:
        machine_params.air_assist = bool(args.air_assist)
    if getattr(args, "z_positive_moves_bed_up", None) is not None:
        machine_params.z_positive_moves_bed_up = bool(args.z_positive_moves_bed_up)

    backend_use_dummy, backend_host, backend_port, ruida_magic = load_backend_config(cfg_data)
    ruida_timeout_s = _dict_get_nested(cfg_data, "backend.ruida_timeout_s", 3.0)
    ruida_source_port = _dict_get_nested(cfg_data, "backend.ruida_source_port", 40200)
    rotary_steps_per_rev = _dict_get_nested(cfg_data, "backend.rotary_steps_per_rev", 4000.0)
    rotary_microsteps = _dict_get_nested(cfg_data, "backend.rotary_microsteps", None)
    # Default pins match the known working script (BOARD/physical numbers): pulse PUL+/DIR+, PUL-/DIR- tied to GND.
    rotary_pin_numbering = _dict_get_nested(
        cfg_data, "backend.rotary_pin_numbering", "board"
    ).lower()
    rotary_step_pin = _dict_get_nested(cfg_data, "backend.rotary_step_pin", None)  # PUL-
    rotary_dir_pin = _dict_get_nested(cfg_data, "backend.rotary_dir_pin", None)  # DIR-
    rotary_step_pin_pos = _dict_get_nested(
        cfg_data, "backend.rotary_step_pin_pos", 11
    )  # PUL+ (physical pin 11)
    rotary_dir_pin_pos = _dict_get_nested(
        cfg_data, "backend.rotary_dir_pin_pos", 13
    )  # DIR+ (physical pin 13)
    rotary_enable_pin = _dict_get_nested(cfg_data, "backend.rotary_enable_pin", None)
    rotary_alarm_pin = _dict_get_nested(cfg_data, "backend.rotary_alarm_pin", None)
    rotary_invert_dir = bool(_dict_get_nested(cfg_data, "backend.rotary_invert_dir", False))
    rotary_max_step_rate_hz = _dict_get_nested(cfg_data, "backend.rotary_max_step_rate_hz", 500.0)
    save_rd_dir = _dict_get_nested(cfg_data, "backend.save_rd_dir", None)
    laser_backend = _dict_get_nested(cfg_data, "backend.laser_backend", None)
    rotary_backend = _dict_get_nested(cfg_data, "backend.rotary_backend", None)
    movement_only = bool(_dict_get_nested(cfg_data, "backend.movement_only", False))
    if args.ruida_timeout_s is not None:
        ruida_timeout_s = args.ruida_timeout_s
    if args.ruida_source_port is not None:
        ruida_source_port = args.ruida_source_port
    if args.rotary_steps_per_rev is not None:
        rotary_steps_per_rev = args.rotary_steps_per_rev
    if args.rotary_microsteps is not None:
        rotary_microsteps = args.rotary_microsteps
    if args.rotary_step_pin is not None:
        rotary_step_pin = args.rotary_step_pin
    if args.rotary_dir_pin is not None:
        rotary_dir_pin = args.rotary_dir_pin
    if args.rotary_step_pin_pos is not None:
        rotary_step_pin_pos = args.rotary_step_pin_pos
    if args.rotary_dir_pin_pos is not None:
        rotary_dir_pin_pos = args.rotary_dir_pin_pos
    if args.rotary_enable_pin is not None:
        rotary_enable_pin = args.rotary_enable_pin
    if args.rotary_alarm_pin is not None:
        rotary_alarm_pin = args.rotary_alarm_pin
    if args.rotary_invert_dir:
        rotary_invert_dir = True
    if args.rotary_pin_numbering is not None:
        rotary_pin_numbering = args.rotary_pin_numbering.lower()
    if args.rotary_max_step_rate_hz is not None:
        rotary_max_step_rate_hz = args.rotary_max_step_rate_hz
    if getattr(args, "save_rd_dir", None) is not None:
        save_rd_dir = args.save_rd_dir
    if args.laser_backend is not None:
        laser_backend = args.laser_backend
    if args.rotary_backend is not None:
        rotary_backend = args.rotary_backend
    movement_only = movement_only or args.movement_only
    if save_rd_dir is not None and not isinstance(save_rd_dir, Path):
        save_rd_dir = Path(save_rd_dir)

    # Default backend selection preserves legacy use_dummy behavior.
    if laser_backend is None:
        laser_backend = "dummy" if backend_use_dummy else "ruida"
    if rotary_backend is None:
        rotary_backend = "dummy" if backend_use_dummy else "real"

    valid_laser_backends = {"dummy", "ruida"}
    valid_rotary_backends = {"dummy", "real"}
    if laser_backend not in valid_laser_backends:
        raise SystemExit(
            f"Invalid laser backend '{laser_backend}'; expected one of {sorted(valid_laser_backends)}"
        )
    if rotary_backend not in valid_rotary_backends:
        raise SystemExit(
            f"Invalid rotary backend '{rotary_backend}'; expected one of {sorted(valid_rotary_backends)}"
        )
    if rotary_pin_numbering not in ("bcm", "board"):
        raise SystemExit("rotary_pin_numbering must be 'bcm' or 'board'")

    dry_run_rd = bool(getattr(args, "dry_run_rd", False))
    reset_only = bool(getattr(args, "reset", False))

    log.debug("JointParams: %s", asdict(joint_params))
    log.debug("JigParams: %s", asdict(jig_params))
    log.debug("MachineParams: %s", asdict(machine_params))

    return RunConfig(
        joint_params=joint_params,
        jig_params=jig_params,
        machine_params=machine_params,
        mode=args.mode,
        dry_run=args.dry_run,
        dry_run_rd=dry_run_rd,
        backend_use_dummy=backend_use_dummy,
        backend_host=backend_host,
        backend_port=backend_port,
        ruida_magic=ruida_magic,
        ruida_timeout_s=ruida_timeout_s,
        ruida_source_port=ruida_source_port,
        rotary_steps_per_rev=rotary_steps_per_rev,
        rotary_microsteps=rotary_microsteps,
        rotary_step_pin=rotary_step_pin,
        rotary_dir_pin=rotary_dir_pin,
        rotary_step_pin_pos=rotary_step_pin_pos,
        rotary_dir_pin_pos=rotary_dir_pin_pos,
        rotary_enable_pin=rotary_enable_pin,
        rotary_alarm_pin=rotary_alarm_pin,
        rotary_invert_dir=rotary_invert_dir,
        rotary_max_step_rate_hz=rotary_max_step_rate_hz,
        rotary_pin_numbering=rotary_pin_numbering,
        simulate=args.simulate,
        laser_backend=laser_backend,
        rotary_backend=rotary_backend,
        movement_only=movement_only,
        save_rd_dir=save_rd_dir,
        reset_only=reset_only,
    )
