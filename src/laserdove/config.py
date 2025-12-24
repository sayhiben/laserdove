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
class BackendConfig:
    """Backend configuration values."""

    laser_backend: str
    rotary_backend: str
    ruida_host: str
    ruida_port: int
    ruida_magic: int
    ruida_timeout_s: float
    ruida_source_port: int
    movement_only: bool
    save_rd_dir: Optional[Path]
    dry_run_rd: bool


@dataclass
class RotaryConfig:
    """Rotary configuration values."""

    steps_per_rev: float
    step_pin: Optional[int]
    dir_pin: Optional[int]
    step_pin_pos: Optional[int]
    dir_pin_pos: Optional[int]
    enable_pin: Optional[int]
    alarm_pin: Optional[int]
    invert_dir: bool
    max_step_rate_hz: Optional[float]
    pin_numbering: str


@dataclass
class SimulationConfig:
    """Simulation configuration values."""

    enabled: bool
    screenshots_dir: Optional[Path]
    screenshots_every_s: float
    rd_dir: Optional[Path]


@dataclass
class RunConfig:
    """Combined runtime configuration values."""

    joint_params: JointParams
    jig_params: JigParams
    machine_params: MachineParams
    mode: str
    dry_run: bool
    reset_only: bool
    backend: BackendConfig
    rotary: RotaryConfig
    simulation: SimulationConfig


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
        help="Visualize the plan with the pygame simulator",
    )
    p.add_argument(
        "--simulate-screenshots-dir",
        type=Path,
        help="Write periodic PNG frames from the pygame viewer to this directory and exit",
    )
    p.add_argument(
        "--simulate-screenshots-every-s",
        type=float,
        default=2.0,
        help="Interval (seconds) between saved pygame frames (default: 2.0)",
    )
    p.add_argument(
        "--simulate-rd-dir",
        type=Path,
        help="Load .rd files from this directory instead of planner commands in the pygame viewer",
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
        "--inline-fan-on",
        dest="inline_fan_on",
        action="store_true",
        help="Enable inline fan output (Ruida BLOW)",
    )
    p.add_argument(
        "--inline-fan-off",
        dest="inline_fan_on",
        action="store_false",
        help="Disable inline fan output (Ruida BLOW)",
    )
    p.set_defaults(inline_fan_on=None)
    p.add_argument(
        "--pre-cut-warmup-s",
        type=float,
        help="Seconds to run air assist/inline fan before the first cut",
    )
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
    p.add_argument("--kerf-mm", type=float)
    p.add_argument("--kerf-tail-mm", type=float)
    p.add_argument("--kerf-pin-mm", type=float)
    p.add_argument(
        "--axis-to-fence-mm",
        type=float,
        help="Rotary axis center to fence top (adds material thickness)",
    )
    p.add_argument(
        "--cut-overtravel-mm",
        type=float,
        help="Extend X cuts past the edge by this amount (mm) for through/finger joints.",
    )
    # Ruida UDP tuning
    p.add_argument("--ruida-timeout-s", type=float, help="UDP ACK timeout seconds for Ruida")
    p.add_argument("--ruida-source-port", type=int, help="Local UDP source port (default 40200)")
    # Rotary tuning
    p.add_argument(
        "--rotary-steps-per-rev",
        type=float,
        help="Step pulses per revolution (includes microsteps; e.g. 4000)",
    )
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
        default=None,
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


def load_config_data(path: Path | None) -> tuple[dict, Path | None]:
    """
    Load configuration data from the given path or default config.toml.

    Args:
        path: Optional TOML config file path.

    Returns:
        Tuple of (config dict, resolved path or None).
    """
    cfg_data: dict = {}
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


def _section(cfg_data: dict, key: str) -> dict:
    """Internal helper to section."""
    value = cfg_data.get(key, {})
    return value if isinstance(value, dict) else {}


def _as_path(value: object | None) -> Path | None:
    """Internal helper to as path."""
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    return Path(str(value))


def build_joint_params(cfg_data: dict) -> JointParams:
    """Build joint params."""
    joint_cfg = _section(cfg_data, "joint")
    thickness_mm = float(joint_cfg.get("thickness_mm", 6.35))
    edge_length_mm = float(joint_cfg.get("edge_length_mm", 100.0))
    dovetail_angle_deg = float(joint_cfg.get("dovetail_angle_deg", 8.0))
    num_tails = int(joint_cfg.get("num_tails", 3))
    tail_outer_width_mm = float(joint_cfg.get("tail_outer_width_mm", 20.0))
    clearance_mm = float(joint_cfg.get("clearance_mm", 0.05))

    kerf_mm_cfg = joint_cfg.get("kerf_mm")
    kerf_mm = float(kerf_mm_cfg) if kerf_mm_cfg is not None else 0.15
    kerf_tail_cfg = joint_cfg.get("kerf_tail_mm")
    kerf_pin_cfg = joint_cfg.get("kerf_pin_mm")
    kerf_tail_mm = float(kerf_tail_cfg) if kerf_tail_cfg is not None else kerf_mm
    kerf_pin_mm = float(kerf_pin_cfg) if kerf_pin_cfg is not None else kerf_mm

    if "tail_depth_mm" in joint_cfg or "socket_depth_mm" in joint_cfg:
        log.warning(
            "joint.tail_depth_mm and joint.socket_depth_mm are derived from thickness_mm and ignored"
        )

    return JointParams(
        thickness_mm=thickness_mm,
        edge_length_mm=edge_length_mm,
        dovetail_angle_deg=dovetail_angle_deg,
        num_tails=num_tails,
        tail_outer_width_mm=tail_outer_width_mm,
        tail_depth_mm=thickness_mm,
        socket_depth_mm=thickness_mm,
        clearance_mm=clearance_mm,
        kerf_tail_mm=kerf_tail_mm,
        kerf_pin_mm=kerf_pin_mm,
    )


def apply_joint_overrides(args: argparse.Namespace, joint_params: JointParams) -> None:
    """Apply joint overrides."""
    if getattr(args, "edge_length_mm", None) is not None:
        joint_params.edge_length_mm = args.edge_length_mm
    if getattr(args, "thickness_mm", None) is not None:
        joint_params.thickness_mm = args.thickness_mm
        joint_params.tail_depth_mm = joint_params.thickness_mm
        joint_params.socket_depth_mm = joint_params.thickness_mm
    if getattr(args, "num_tails", None) is not None:
        joint_params.num_tails = args.num_tails
    if getattr(args, "dovetail_angle_deg", None) is not None:
        joint_params.dovetail_angle_deg = args.dovetail_angle_deg
    if getattr(args, "tail_width_mm", None) is not None:
        joint_params.tail_outer_width_mm = args.tail_width_mm
    if getattr(args, "clearance_mm", None) is not None:
        joint_params.clearance_mm = args.clearance_mm
    if getattr(args, "kerf_mm", None) is not None:
        joint_params.kerf_tail_mm = args.kerf_mm
        joint_params.kerf_pin_mm = args.kerf_mm
    if getattr(args, "kerf_tail_mm", None) is not None:
        joint_params.kerf_tail_mm = args.kerf_tail_mm
    if getattr(args, "kerf_pin_mm", None) is not None:
        joint_params.kerf_pin_mm = args.kerf_pin_mm


def build_jig_params(cfg_data: dict, joint_params: JointParams) -> JigParams:
    """Build jig params."""
    jig_cfg = _section(cfg_data, "jig")
    axis_to_fence_mm = jig_cfg.get("axis_to_fence_mm")
    if axis_to_fence_mm is not None:
        axis_to_origin_mm = float(axis_to_fence_mm) + joint_params.thickness_mm
    else:
        axis_to_origin_mm = 30.0
        log.warning("jig.axis_to_fence_mm not set; defaulting axis_to_origin_mm to 30.0")
    return JigParams(
        axis_to_origin_mm=axis_to_origin_mm,
        rotation_zero_deg=float(jig_cfg.get("rotation_zero_deg", 0.0)),
        rotation_speed_dps=float(jig_cfg.get("rotation_speed_dps", 30.0)),
    )


def apply_jig_overrides(
    args: argparse.Namespace, jig_params: JigParams, joint_params: JointParams
) -> None:
    """Apply jig overrides."""
    if getattr(args, "axis_to_fence_mm", None) is not None:
        jig_params.axis_to_origin_mm = args.axis_to_fence_mm + joint_params.thickness_mm


def build_machine_params(cfg_data: dict) -> MachineParams:
    """Build machine params."""
    machine_cfg = _section(cfg_data, "machine")
    return MachineParams(
        cut_speed_tail_mm_s=float(machine_cfg.get("cut_speed_tail_mm_s", 10.0)),
        cut_speed_pin_mm_s=float(machine_cfg.get("cut_speed_pin_mm_s", 8.0)),
        rapid_speed_mm_s=float(machine_cfg.get("rapid_speed_mm_s", 200.0)),
        z_speed_mm_s=float(machine_cfg.get("z_speed_mm_s", 5.0)),
        cut_power_tail_pct=float(machine_cfg.get("cut_power_tail_pct", 60.0)),
        cut_power_pin_pct=float(machine_cfg.get("cut_power_pin_pct", 65.0)),
        travel_power_pct=float(machine_cfg.get("travel_power_pct", 0.0)),
        cut_overtravel_mm=float(machine_cfg.get("cut_overtravel_mm", 0.5)),
        air_assist=bool(machine_cfg.get("air_assist", True)),
        z_positive_moves_bed_up=bool(machine_cfg.get("z_positive_moves_bed_up", True)),
        inline_fan_on=bool(machine_cfg.get("inline_fan_on", False)),
        pre_cut_warmup_s=float(machine_cfg.get("pre_cut_warmup_s", 0.0)),
        z_zero_tail_mm=float(machine_cfg.get("z_zero_tail_mm", 0.0)),
        z_zero_pin_mm=float(machine_cfg.get("z_zero_pin_mm", 0.0)),
    )


def apply_machine_overrides(args: argparse.Namespace, machine_params: MachineParams) -> None:
    """Apply machine overrides."""
    if getattr(args, "cut_overtravel_mm", None) is not None:
        machine_params.cut_overtravel_mm = args.cut_overtravel_mm
    if getattr(args, "air_assist", None) is not None:
        machine_params.air_assist = bool(args.air_assist)
    if getattr(args, "inline_fan_on", None) is not None:
        machine_params.inline_fan_on = bool(args.inline_fan_on)
    if getattr(args, "pre_cut_warmup_s", None) is not None:
        machine_params.pre_cut_warmup_s = float(args.pre_cut_warmup_s)
    if getattr(args, "z_positive_moves_bed_up", None) is not None:
        machine_params.z_positive_moves_bed_up = bool(args.z_positive_moves_bed_up)


def build_backend_config(cfg_data: dict, *, dry_run_rd: bool) -> BackendConfig:
    """Build backend config."""
    backend_cfg = _section(cfg_data, "backend")
    laser_backend = backend_cfg.get("laser_backend")
    rotary_backend = backend_cfg.get("rotary_backend")
    if laser_backend is None:
        laser_backend = "dummy"
    if rotary_backend is None:
        rotary_backend = "dummy"
    return BackendConfig(
        laser_backend=str(laser_backend).lower(),
        rotary_backend=str(rotary_backend).lower(),
        ruida_host=str(backend_cfg.get("ruida_host", "192.168.1.100")),
        ruida_port=int(backend_cfg.get("ruida_port", 50200)),
        ruida_magic=int(backend_cfg.get("ruida_magic", 0x88)),
        ruida_timeout_s=float(backend_cfg.get("ruida_timeout_s", 3.0)),
        ruida_source_port=int(backend_cfg.get("ruida_source_port", 40200)),
        movement_only=bool(backend_cfg.get("movement_only", False)),
        save_rd_dir=_as_path(backend_cfg.get("save_rd_dir")),
        dry_run_rd=dry_run_rd,
    )


def apply_backend_overrides(args: argparse.Namespace, backend: BackendConfig) -> None:
    """Apply backend overrides."""
    if getattr(args, "ruida_timeout_s", None) is not None:
        backend.ruida_timeout_s = args.ruida_timeout_s
    if getattr(args, "ruida_source_port", None) is not None:
        backend.ruida_source_port = args.ruida_source_port
    if getattr(args, "laser_backend", None) is not None:
        backend.laser_backend = args.laser_backend
    if getattr(args, "rotary_backend", None) is not None:
        backend.rotary_backend = args.rotary_backend
    if getattr(args, "save_rd_dir", None) is not None:
        backend.save_rd_dir = _as_path(args.save_rd_dir)
    backend.movement_only = backend.movement_only or bool(getattr(args, "movement_only", False))


def build_rotary_config(cfg_data: dict) -> RotaryConfig:
    """Build rotary config."""
    backend_cfg = _section(cfg_data, "backend")
    return RotaryConfig(
        steps_per_rev=float(backend_cfg.get("rotary_steps_per_rev", 4000.0)),
        step_pin=backend_cfg.get("rotary_step_pin"),
        dir_pin=backend_cfg.get("rotary_dir_pin"),
        step_pin_pos=backend_cfg.get("rotary_step_pin_pos", 11),
        dir_pin_pos=backend_cfg.get("rotary_dir_pin_pos", 13),
        enable_pin=backend_cfg.get("rotary_enable_pin"),
        alarm_pin=backend_cfg.get("rotary_alarm_pin"),
        invert_dir=bool(backend_cfg.get("rotary_invert_dir", False)),
        max_step_rate_hz=backend_cfg.get("rotary_max_step_rate_hz", 500.0),
        pin_numbering=str(backend_cfg.get("rotary_pin_numbering", "board")).lower(),
    )


def apply_rotary_overrides(args: argparse.Namespace, rotary: RotaryConfig) -> None:
    """Apply rotary overrides."""
    if getattr(args, "rotary_steps_per_rev", None) is not None:
        rotary.steps_per_rev = args.rotary_steps_per_rev
    if getattr(args, "rotary_step_pin", None) is not None:
        rotary.step_pin = args.rotary_step_pin
    if getattr(args, "rotary_dir_pin", None) is not None:
        rotary.dir_pin = args.rotary_dir_pin
    if getattr(args, "rotary_step_pin_pos", None) is not None:
        rotary.step_pin_pos = args.rotary_step_pin_pos
    if getattr(args, "rotary_dir_pin_pos", None) is not None:
        rotary.dir_pin_pos = args.rotary_dir_pin_pos
    if getattr(args, "rotary_enable_pin", None) is not None:
        rotary.enable_pin = args.rotary_enable_pin
    if getattr(args, "rotary_alarm_pin", None) is not None:
        rotary.alarm_pin = args.rotary_alarm_pin
    if getattr(args, "rotary_invert_dir", False):
        rotary.invert_dir = True
    if getattr(args, "rotary_pin_numbering", None) is not None:
        rotary.pin_numbering = args.rotary_pin_numbering.lower()
    if getattr(args, "rotary_max_step_rate_hz", None) is not None:
        rotary.max_step_rate_hz = args.rotary_max_step_rate_hz


def build_simulation_config(cfg_data: dict, args: argparse.Namespace) -> SimulationConfig:
    """Build simulation config."""
    backend_cfg = _section(cfg_data, "backend")
    rd_dir = _as_path(backend_cfg.get("simulate_rd_dir"))
    if getattr(args, "simulate_rd_dir", None) is not None:
        rd_dir = _as_path(args.simulate_rd_dir)
    return SimulationConfig(
        enabled=bool(getattr(args, "simulate", False)),
        screenshots_dir=getattr(args, "simulate_screenshots_dir", None),
        screenshots_every_s=float(getattr(args, "simulate_screenshots_every_s", 2.0)),
        rd_dir=rd_dir,
    )


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
    cfg_data, _cfg_path = load_config_data(args.config)

    joint_params = build_joint_params(cfg_data)
    apply_joint_overrides(args, joint_params)

    jig_params = build_jig_params(cfg_data, joint_params)
    apply_jig_overrides(args, jig_params, joint_params)

    machine_params = build_machine_params(cfg_data)
    apply_machine_overrides(args, machine_params)

    backend = build_backend_config(cfg_data, dry_run_rd=bool(getattr(args, "dry_run_rd", False)))
    apply_backend_overrides(args, backend)

    rotary = build_rotary_config(cfg_data)
    apply_rotary_overrides(args, rotary)

    simulation = build_simulation_config(cfg_data, args)

    valid_laser_backends = {"dummy", "ruida"}
    valid_rotary_backends = {"dummy", "real"}
    if backend.laser_backend not in valid_laser_backends:
        raise SystemExit(
            f"Invalid laser backend '{backend.laser_backend}'; expected one of {sorted(valid_laser_backends)}"
        )
    if backend.rotary_backend not in valid_rotary_backends:
        raise SystemExit(
            f"Invalid rotary backend '{backend.rotary_backend}'; expected one of {sorted(valid_rotary_backends)}"
        )
    if simulation.screenshots_every_s <= 0.0:
        raise SystemExit("--simulate-screenshots-every-s must be > 0")
    if rotary.pin_numbering not in ("bcm", "board"):
        raise SystemExit("rotary_pin_numbering must be 'bcm' or 'board'")

    log.debug("JointParams: %s", asdict(joint_params))
    log.debug("JigParams: %s", asdict(jig_params))
    log.debug("MachineParams: %s", asdict(machine_params))
    log.debug("BackendConfig: %s", asdict(backend))
    log.debug("RotaryConfig: %s", asdict(rotary))
    log.debug("SimulationConfig: %s", asdict(simulation))

    return RunConfig(
        joint_params=joint_params,
        jig_params=jig_params,
        machine_params=machine_params,
        mode=args.mode,
        dry_run=bool(getattr(args, "dry_run", False)),
        reset_only=bool(getattr(args, "reset", False)),
        backend=backend,
        rotary=rotary,
        simulation=simulation,
    )
