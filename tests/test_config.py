import argparse
from pathlib import Path

import pytest

from laserdove.config import load_config_and_args
from laserdove.model import JointParams, JigParams, MachineParams


def make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        config=None,
        mode="both",
        dry_run=False,
        simulate=False,
        movement_only=False,
        dry_run_rd=False,
        reset=False,
        edge_length_mm=None,
        thickness_mm=None,
        num_tails=None,
        dovetail_angle_deg=None,
        tail_width_mm=None,
        clearance_mm=None,
        kerf_tail_mm=None,
        kerf_pin_mm=None,
        axis_offset_mm=None,
        log_level="INFO",
        ruida_timeout_s=None,
        ruida_source_port=None,
        rotary_steps_per_rev=None,
        rotary_microsteps=None,
        rotary_step_pin=None,
        rotary_dir_pin=None,
        rotary_step_pin_pos=None,
        rotary_dir_pin_pos=None,
        rotary_enable_pin=None,
        rotary_alarm_pin=None,
        rotary_invert_dir=False,
        rotary_max_step_rate_hz=None,
        rotary_pin_numbering="board",
        laser_backend=None,
        rotary_backend=None,
        save_rd_dir=None,
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_load_config_defaults_without_file(tmp_path, monkeypatch):
    # Ensure we don't accidentally pick up a real config.toml.
    monkeypatch.chdir(tmp_path)
    args = make_args()
    rc = load_config_and_args(args)

    assert isinstance(rc.joint_params, JointParams)
    assert isinstance(rc.jig_params, JigParams)
    assert isinstance(rc.machine_params, MachineParams)
    assert rc.mode == "both"
    assert rc.dry_run is False
    assert rc.simulate is False
    assert rc.backend_use_dummy is True
    assert rc.backend_host == "192.168.1.100"
    assert rc.backend_port == 50200
    assert rc.laser_backend == "dummy"
    assert rc.rotary_backend == "dummy"
    assert rc.rotary_step_pin is None
    assert rc.rotary_dir_pin is None
    assert rc.rotary_step_pin_pos == 11
    assert rc.rotary_dir_pin_pos == 13
    assert rc.rotary_enable_pin is None
    assert rc.rotary_alarm_pin is None
    assert rc.rotary_invert_dir is False
    assert rc.rotary_max_step_rate_hz == 500.0
    assert rc.rotary_pin_numbering == "board"
    assert rc.movement_only is False
    assert rc.save_rd_dir is None


def test_load_config_uses_default_config_file(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
        [joint]
        edge_length_mm = 42.0
        [backend]
        laser_backend = "ruida"
        rotary_backend = "real"
        """
    )
    monkeypatch.chdir(tmp_path)
    args = make_args(config=None)

    rc = load_config_and_args(args)
    # Default config.toml picked up and applied
    assert rc.joint_params.edge_length_mm == 42.0
    assert rc.laser_backend == "ruida"
    assert rc.rotary_backend == "real"


def test_load_config_missing_explicit_path_raises(tmp_path):
    missing_path = tmp_path / "nope.toml"
    args = make_args(config=missing_path)
    with pytest.raises(SystemExit):
        load_config_and_args(args)


def test_load_config_reads_toml_and_applies_overrides(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
        [joint]
        thickness_mm = 5.0
        edge_length_mm = 50.0
        num_tails = 2
        [jig]
        axis_to_origin_mm = 40.0
        [machine]
        cut_speed_tail_mm_s = 12.0
        """
    )
    args = make_args(
        config=cfg_path,
        thickness_mm=7.0,  # override thickness and tail depth
        clearance_mm=0.2,
        axis_offset_mm=55.0,
        mode="pins",
        dry_run=True,
        simulate=True,
        laser_backend="dummy",
        rotary_backend="dummy",
    )

    rc = load_config_and_args(args)

    assert rc.joint_params.thickness_mm == 7.0
    assert rc.joint_params.tail_depth_mm == 7.0  # thickness override also updates tail depth
    assert rc.joint_params.edge_length_mm == 50.0
    assert rc.joint_params.clearance_mm == 0.2
    assert rc.joint_params.num_tails == 2

    assert rc.jig_params.axis_to_origin_mm == 55.0  # CLI override wins over file
    assert rc.machine_params.cut_speed_tail_mm_s == 12.0

    assert rc.mode == "pins"
    assert rc.dry_run is True
    assert rc.simulate is True
    assert rc.backend_use_dummy is True
    assert rc.backend_host == "192.168.1.100"
    assert rc.backend_port == 50200
    assert rc.laser_backend == "dummy"
    assert rc.rotary_backend == "dummy"
    assert rc.movement_only is False


def test_backend_overrides_and_movement_only(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        """
        [backend]
        use_dummy = false
        laser_backend = "ruida"
        rotary_backend = "real"
        movement_only = true
        ruida_host = "10.0.0.5"
        ruida_port = 60000
        rotary_step_pin = 23
        rotary_dir_pin = 24
        rotary_enable_pin = 25
        rotary_alarm_pin = 18
        rotary_invert_dir = true
        """
    )
    args = make_args(
        config=cfg_path,
        # Override rotary backend to dummy via CLI to test precedence.
        rotary_backend="dummy",
    )

    rc = load_config_and_args(args)

    assert rc.backend_use_dummy is False
    assert rc.backend_host == "10.0.0.5"
    assert rc.backend_port == 60000
    assert rc.laser_backend == "ruida"
    assert rc.rotary_backend == "dummy"  # CLI override wins
    assert rc.movement_only is True
    assert rc.rotary_step_pin == 23
    assert rc.rotary_dir_pin == 24
    assert rc.rotary_step_pin_pos == 11  # defaults remain unless overridden
    assert rc.rotary_dir_pin_pos == 13
    assert rc.rotary_enable_pin == 25
    assert rc.rotary_alarm_pin == 18
    assert rc.rotary_invert_dir is True
    assert rc.rotary_max_step_rate_hz == 500.0
    assert rc.rotary_pin_numbering == "board"


def test_invalid_backends_raise(monkeypatch):
    args = make_args(laser_backend="bogus")
    with pytest.raises(SystemExit):
        load_config_and_args(args)

    args = make_args(rotary_backend="nope")
    with pytest.raises(SystemExit):
        load_config_and_args(args)

    args = make_args(rotary_pin_numbering="weird")
    with pytest.raises(SystemExit):
        load_config_and_args(args)


def test_cli_overrides_apply_to_optional_fields(tmp_path):
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text("[backend]\nrotary_steps_per_rev = 1000.0\n")
    args = make_args(
        config=cfg_path,
        edge_length_mm=10.0,
        num_tails=5,
        dovetail_angle_deg=10.0,
        tail_width_mm=12.0,
        kerf_tail_mm=0.2,
        kerf_pin_mm=0.25,
        rotary_steps_per_rev=1234.0,
        rotary_microsteps=8,
        rotary_enable_pin=7,
        rotary_alarm_pin=8,
        rotary_max_step_rate_hz=900.0,
        ruida_timeout_s=1.5,
        ruida_source_port=41000,
        rotary_step_pin=9,
        rotary_dir_pin=10,
        save_rd_dir=tmp_path / "rd",
        dry_run_rd=True,
    )
    rc = load_config_and_args(args)
    assert rc.joint_params.edge_length_mm == 10.0
    assert rc.joint_params.num_tails == 5
    assert rc.joint_params.dovetail_angle_deg == 10.0
    assert rc.joint_params.tail_outer_width_mm == 12.0
    assert rc.joint_params.kerf_tail_mm == 0.2
    assert rc.joint_params.kerf_pin_mm == 0.25
    assert rc.rotary_steps_per_rev == 1234.0
    assert rc.rotary_microsteps == 8
    assert rc.rotary_enable_pin == 7
    assert rc.rotary_alarm_pin == 8
    assert rc.rotary_max_step_rate_hz == 900.0
    assert rc.ruida_timeout_s == 1.5
    assert rc.ruida_source_port == 41000
    assert rc.rotary_step_pin == 9
    assert rc.rotary_dir_pin == 10
    assert rc.save_rd_dir == tmp_path / "rd"
    assert rc.dry_run_rd is True
