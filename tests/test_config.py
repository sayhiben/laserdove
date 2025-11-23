import argparse
from pathlib import Path

import pytest

from config import load_config_and_args
from model import JointParams, JigParams, MachineParams


def make_args(**overrides) -> argparse.Namespace:
    defaults = dict(
        config=None,
        mode="both",
        dry_run=False,
        simulate=False,
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
    )
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def test_load_config_defaults_without_file(tmp_path, monkeypatch):
    # Ensure we don't accidentally pick up a real config.toml.
    monkeypatch.chdir(tmp_path)
    args = make_args()
    joint, jig, machine, mode, dry_run, use_dummy, host, port, simulate = load_config_and_args(args)

    assert isinstance(joint, JointParams)
    assert isinstance(jig, JigParams)
    assert isinstance(machine, MachineParams)
    assert mode == "both"
    assert dry_run is False
    assert simulate is False
    assert use_dummy is True
    assert host == "192.168.1.100"
    assert port == 50200


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
    )

    joint, jig, machine, mode, dry_run, use_dummy, host, port, simulate = load_config_and_args(args)

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
