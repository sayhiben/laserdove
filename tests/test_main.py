import sys

import pytest

from laserdove.cli import main
from laserdove.config import RunConfig
from laserdove.model import (
    Command,
    CommandType,
    JointParams,
    JigParams,
    MachineParams,
    PinPlan,
)


def make_run_config(**overrides) -> RunConfig:
    defaults = dict(
        joint_params=JointParams(
            thickness_mm=6.35,
            edge_length_mm=100.0,
            dovetail_angle_deg=8.0,
            num_tails=3,
            tail_outer_width_mm=20.0,
            tail_depth_mm=6.35,
            socket_depth_mm=6.6,
            clearance_mm=0.05,
            kerf_tail_mm=0.15,
            kerf_pin_mm=0.15,
        ),
        jig_params=JigParams(
            axis_to_origin_mm=30.0,
            rotation_zero_deg=0.0,
            rotation_speed_dps=30.0,
        ),
        machine_params=MachineParams(
            cut_speed_tail_mm_s=10.0,
            cut_speed_pin_mm_s=8.0,
            rapid_speed_mm_s=200.0,
            z_speed_mm_s=5.0,
            cut_power_tail_pct=60.0,
            cut_power_pin_pct=65.0,
            travel_power_pct=0.0,
            cut_overtravel_mm=0.5,
            z_zero_tail_mm=0.0,
            z_zero_pin_mm=0.0,
        ),
        mode="both",
        dry_run=False,
        dry_run_rd=False,
        backend_use_dummy=True,
        backend_host="host",
        backend_port=50200,
        ruida_magic=0x88,
        ruida_timeout_s=3.0,
        ruida_source_port=40200,
        rotary_steps_per_rev=4000.0,
        rotary_microsteps=None,
        rotary_step_pin=None,
        rotary_dir_pin=None,
        rotary_step_pin_pos=11,
        rotary_dir_pin_pos=13,
        rotary_enable_pin=None,
        rotary_alarm_pin=None,
        rotary_invert_dir=False,
        rotary_max_step_rate_hz=500.0,
        rotary_pin_numbering="board",
        simulate=False,
        laser_backend="dummy",
        rotary_backend="dummy",
        movement_only=False,
        save_rd_dir=None,
        reset_only=False,
    )
    defaults.update(overrides)
    return RunConfig(**defaults)


def test_main_runs_dry_run_and_prints_commands(monkeypatch, capsys):
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")  # silence any warnings
    argv = ["main.py", "--mode", "tails", "--dry-run"]
    monkeypatch.setattr(sys, "argv", argv)

    main()
    output = capsys.readouterr().out
    assert "CommandType.MOVE" in output or "Command(" in output
    assert "Tail:" in output


def test_main_executes_commands_without_dry_run(monkeypatch):
    executed = {}

    def fake_execute(commands, laser, rotary):
        executed["count"] = len(list(commands))

    argv = ["main.py", "--mode", "tails"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr("laserdove.cli.execute_commands", fake_execute)

    main()
    assert executed["count"] > 0


def test_main_exits_on_validation_error(monkeypatch):
    argv = ["main.py", "--mode", "tails"]
    monkeypatch.setattr(sys, "argv", argv)

    # Force a validation failure without breaking layout computation.
    dummy_config = RunConfig(
        joint_params=None,
        jig_params=None,
        machine_params=None,
        mode="tails",
        dry_run=False,
        dry_run_rd=False,
        backend_use_dummy=True,
        backend_host="host",
        backend_port=0,
        ruida_magic=0x88,
        ruida_timeout_s=3.0,
        ruida_source_port=40200,
        rotary_steps_per_rev=200.0,
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
        simulate=False,
        laser_backend="dummy",
        rotary_backend="dummy",
        movement_only=False,
        save_rd_dir=None,
        reset_only=False,
    )
    monkeypatch.setattr(
        "laserdove.cli.load_config_and_args",
        lambda args: dummy_config,
    )
    monkeypatch.setattr(
        "laserdove.cli.compute_tail_layout",
        lambda _: None,
    )
    monkeypatch.setattr(
        "laserdove.cli.validate_all",
        lambda *_: ["fail"],
    )
    with pytest.raises(SystemExit):
        main()


def test_main_runs_both_boards_and_executes(monkeypatch):
    rc = make_run_config(mode="both")
    captured = {}
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr(
        "laserdove.cli.plan_tail_board",
        lambda jp, mp, tl: [Command(type=CommandType.MOVE, x=0, y=0)],
    )
    monkeypatch.setattr(
        "laserdove.cli.compute_pin_plan",
        lambda jp, jg, tl: PinPlan(sides=[], pin_outer_width=1.0, half_pin_width=0.5),
    )
    monkeypatch.setattr(
        "laserdove.cli.plan_pin_board",
        lambda jp, jg, mp, pin_plan: [Command(type=CommandType.CUT_LINE, x=1, y=1, speed_mm_s=1)],
    )
    monkeypatch.setattr(
        "laserdove.cli.execute_commands",
        lambda cmds, laser, rotary: captured.setdefault("commands", list(cmds)),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "both"])

    main()
    types = [cmd.type for cmd in captured["commands"]]
    assert types[0] == CommandType.ROTATE  # prep rotate
    assert CommandType.MOVE in types
    assert CommandType.CUT_LINE in types


def test_main_simulate_uses_simulated_backends_and_cleans_up(monkeypatch):
    created = {}

    class FakeLaser:
        def __init__(self, real_time=True):
            self.setup_called = False
            self.cleanup_called = False
            created["laser"] = self

        def setup_viewer(self):
            self.setup_called = True

        def cleanup(self):
            self.cleanup_called = True

    class FakeRotary:
        def __init__(self, _laser=None, real_time=True):
            self.cleanup_called = False
            created["rotary"] = self

        def cleanup(self):
            self.cleanup_called = True

    rc = make_run_config(mode="tails", simulate=True)
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr("laserdove.hardware.SimulatedLaser", FakeLaser)
    monkeypatch.setattr("laserdove.hardware.SimulatedRotary", FakeRotary)
    monkeypatch.setattr(
        "laserdove.cli.plan_tail_board",
        lambda jp, mp, tl: [Command(type=CommandType.MOVE, x=0, y=0, speed_mm_s=1.0)],
    )
    monkeypatch.setattr("laserdove.cli.execute_commands", lambda cmds, laser, rotary: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "tails", "--simulate"])

    main()
    assert created["laser"].setup_called is True
    assert created["laser"].cleanup_called is True
    assert created["rotary"].cleanup_called is True


def test_main_ruida_respects_dry_run_rd_and_cleans(monkeypatch):
    init_kwargs = {}
    run_called = {}
    created = {}

    class FakeRuida:
        def __init__(self, **kwargs):
            init_kwargs.update(kwargs)
            self.cleaned = False
            created["ruida"] = self

        def run_sequence_with_rotary(self, commands, rotary):
            run_called["count"] = len(list(commands))

        def cleanup(self):
            self.cleaned = True

    rc = make_run_config(
        mode="tails",
        laser_backend="ruida",
        dry_run=False,
        dry_run_rd=True,
    )
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr("laserdove.cli.RuidaLaser", FakeRuida)
    monkeypatch.setattr(
        "laserdove.cli.plan_tail_board",
        lambda jp, mp, tl: [Command(type=CommandType.MOVE, x=0, y=0, speed_mm_s=1.0)],
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "tails"])

    main()
    assert init_kwargs["dry_run"] is True
    assert run_called["count"] >= 1
    assert created["ruida"].cleaned is True


def test_main_pins_only_executes_pin_branch(monkeypatch):
    rc = make_run_config(mode="pins")
    called = {}
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr(
        "laserdove.cli.compute_pin_plan",
        lambda jp, jg, tl: PinPlan(sides=[], pin_outer_width=1.0, half_pin_width=0.5),
    )
    monkeypatch.setattr(
        "laserdove.cli.plan_pin_board",
        lambda jp, jg, mp, pin_plan: [Command(type=CommandType.MOVE, x=0, y=0, speed_mm_s=1.0)],
    )
    monkeypatch.setattr(
        "laserdove.cli.execute_commands",
        lambda cmds, laser, rotary: called.setdefault("cmds", list(cmds)),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "pins"])
    main()
    types = [cmd.type for cmd in called["cmds"]]
    assert types[0] == CommandType.ROTATE
    assert CommandType.MOVE in types


def test_main_real_rotary_without_pins(monkeypatch):
    rc = make_run_config(mode="tails", rotary_backend="real")
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr(
        "laserdove.cli.plan_tail_board",
        lambda jp, mp, tl: [Command(type=CommandType.MOVE, x=0, y=0, speed_mm_s=1.0)],
    )
    monkeypatch.setattr("laserdove.cli.execute_commands", lambda cmds, laser, rotary: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "tails"])
    main()  # Should not raise even with missing step/dir pins


def test_main_simulate_calls_show(monkeypatch):
    created = {}

    class FakeLaser:
        def __init__(self, real_time=True):
            created["laser"] = self
            self.shown = False
            self.cleanup_called = False

        def setup_viewer(self):
            pass

        def show(self):
            self.shown = True

        def cleanup(self):
            self.cleanup_called = True

    class FakeRotary:
        def __init__(self, laser=None, real_time=True):
            self.cleanup_called = False

        def cleanup(self):
            self.cleanup_called = True

    rc = make_run_config(mode="tails", simulate=True)
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr("laserdove.hardware.SimulatedLaser", FakeLaser)
    monkeypatch.setattr("laserdove.hardware.SimulatedRotary", FakeRotary)
    monkeypatch.setattr(
        "laserdove.cli.plan_tail_board",
        lambda jp, mp, tl: [Command(type=CommandType.MOVE, x=0, y=0, speed_mm_s=1.0)],
    )
    monkeypatch.setattr("laserdove.cli.execute_commands", lambda cmds, laser, rotary: None)
    monkeypatch.setattr(sys, "argv", ["main.py", "--mode", "tails", "--simulate"])
    main()
    assert created["laser"].shown is True


def test_main_reset_only(monkeypatch):
    rc = make_run_config(mode="tails", reset_only=True)
    captured = {}
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr(
        "laserdove.cli.execute_commands",
        lambda cmds, laser, rotary: captured.setdefault("cmds", list(cmds)),
    )
    monkeypatch.setattr(sys, "argv", ["main.py", "--reset"])
    main()
    assert [cmd.type for cmd in captured["cmds"]] == [
        CommandType.SET_LASER_POWER,
        CommandType.ROTATE,
        CommandType.MOVE,
    ]


def test_main_invalid_backend_raises(monkeypatch):
    rc = make_run_config(laser_backend="unsupported")
    monkeypatch.setattr("laserdove.cli.load_config_and_args", lambda args: rc)
    monkeypatch.setattr(sys, "argv", ["main.py"])
    with pytest.raises(ValueError):
        main()
