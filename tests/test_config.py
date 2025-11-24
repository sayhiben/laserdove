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
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_load_config_defaults_without_file(tmp_path, monkeypatch):
    # Ensure we don't accidentally pick up a real config.toml.
    monkeypatch.chdir(tmp_path)
    args = make_args()
    (
        joint,
        jig,
        machine,
        mode,
        dry_run,
        use_dummy,
        host,
        port,
        _,
        _,
        _,
        _,
        _,
        _step_pin,
        _dir_pin,
        _step_pin_pos,
        _dir_pin_pos,
        _enable_pin,
        _alarm_pin,
        _invert_dir,
        _max_step_rate,
        _pin_scheme,
        simulate,
        laser_backend,
        rotary_backend,
        movement_only,
    ) = load_config_and_args(args)

    assert isinstance(joint, JointParams)
    assert isinstance(jig, JigParams)
    assert isinstance(machine, MachineParams)
    assert mode == "both"
    assert dry_run is False
    assert simulate is False
    assert use_dummy is True
    assert host == "192.168.1.100"
    assert port == 50200
    assert simulate is False
    assert laser_backend == "dummy"
    assert rotary_backend == "dummy"
    assert _step_pin is None
    assert _dir_pin is None
    assert _step_pin_pos == 11
    assert _dir_pin_pos == 13
    assert _enable_pin is None
    assert _alarm_pin is None
    assert _invert_dir is False
    assert _max_step_rate == 1200.0
    assert _pin_scheme == "board"
    assert movement_only is False


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

    (
        joint,
        jig,
        machine,
        mode,
        dry_run,
        use_dummy,
        host,
        port,
        _magic,
        _timeout,
        _src_port,
        _steps_per_rev,
        _microsteps,
        _step_pin,
        _dir_pin,
        _step_pin_pos,
        _dir_pin_pos,
        _enable_pin,
        _alarm_pin,
        _invert_dir,
        _max_step_rate,
        _pin_scheme,
        simulate,
        laser_backend,
        rotary_backend,
        movement_only,
    ) = load_config_and_args(args)

    assert joint.thickness_mm == 7.0
    assert joint.tail_depth_mm == 7.0  # thickness override also updates tail depth
    assert joint.edge_length_mm == 50.0
    assert joint.clearance_mm == 0.2
    assert joint.num_tails == 2

    assert jig.axis_to_origin_mm == 55.0  # CLI override wins over file
    assert machine.cut_speed_tail_mm_s == 12.0

    assert mode == "pins"
    assert dry_run is True
    assert simulate is True
    assert use_dummy is True
    assert host == "192.168.1.100"
    assert port == 50200
    assert laser_backend == "dummy"
    assert rotary_backend == "dummy"
    assert movement_only is False


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

    (
        _joint,
        _jig,
        _machine,
        _mode,
        _dry_run,
        use_dummy,
        host,
        port,
        _magic,
        _timeout,
        _src_port,
        _steps_per_rev,
        _microsteps,
        _step_pin,
        _dir_pin,
        _step_pin_pos,
        _dir_pin_pos,
        _enable_pin,
        _alarm_pin,
        _invert,
        _max_step_rate,
        _pin_scheme,
        _simulate,
        laser_backend,
        rotary_backend,
        movement_only,
    ) = load_config_and_args(args)

    assert use_dummy is False
    assert host == "10.0.0.5"
    assert port == 60000
    assert laser_backend == "ruida"
    assert rotary_backend == "dummy"  # CLI override wins
    assert movement_only is True
    assert _step_pin == 23
    assert _dir_pin == 24
    assert _step_pin_pos == 11  # defaults remain unless overridden
    assert _dir_pin_pos == 13
    assert _enable_pin == 25
    assert _alarm_pin == 18
    assert _invert is True
    assert _max_step_rate == 1200.0
    assert _pin_scheme == "board"
