# config.py
from __future__ import annotations

import argparse
from dataclasses import asdict
from pathlib import Path
from typing import Tuple

import logging

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore

from model import JointParams, JigParams, MachineParams

log = logging.getLogger(__name__)


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Dovetail joint planner for Thunder Nova + rotary jig",
    )
    p.add_argument(
        "--config",
        type=Path,
        help="TOML config file (defaults to config.toml if present)",
    )
    p.add_argument("--mode", choices=["tails", "pins", "both"],
                   default="both", help="Which board to plan")
    p.add_argument("--dry-run", action="store_true",
                   help="Do not talk to hardware; just print plan")

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

    p.add_argument("--log-level", default="INFO")
    return p


def _load_toml(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")
    with path.open("rb") as f:
        return tomllib.load(f)


def _dict_get_nested(data: dict, key: str, default=None):
    parts = key.split(".")
    current_level = data
    for part in parts[:-1]:
        current_level = current_level.get(part, {})
    return current_level.get(parts[-1], default)


def load_backend_config(cfg_data: dict) -> tuple[bool, str, int]:
    """
    Return (use_dummy, ruida_host, ruida_port)
    """
    use_dummy = _dict_get_nested(cfg_data, "backend.use_dummy", True)
    host = _dict_get_nested(cfg_data, "backend.ruida_host", "192.168.1.100")
    port = _dict_get_nested(cfg_data, "backend.ruida_port", 50200)
    return use_dummy, host, port


def load_config_and_args(
    args: argparse.Namespace,
) -> Tuple[JointParams, JigParams, MachineParams, str, bool, bool, str, int]:
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

    backend_use_dummy, backend_host, backend_port = load_backend_config(cfg_data)

    log.debug("JointParams: %s", asdict(joint_params))
    log.debug("JigParams: %s", asdict(jig_params))
    log.debug("MachineParams: %s", asdict(machine_params))

    return (
        joint_params,
        jig_params,
        machine_params,
        args.mode,
        args.dry_run,
        backend_use_dummy,
        backend_host,
        backend_port,
    )
