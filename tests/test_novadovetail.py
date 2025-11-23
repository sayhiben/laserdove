import pytest
import sys
from io import StringIO

from laserdove.novadovetail import main


def test_main_runs_dry_run_and_prints_commands(monkeypatch, capsys):
    monkeypatch.setenv("PYTHONWARNINGS", "ignore")  # silence any warnings
    argv = ["novadovetail.py", "--mode", "tails", "--dry-run"]
    monkeypatch.setattr(sys, "argv", argv)

    main()
    output = capsys.readouterr().out
    assert "CommandType.MOVE" in output or "Command(" in output
    assert "Tail:" in output


def test_main_executes_commands_without_dry_run(monkeypatch):
    executed = {}

    def fake_execute(commands, laser, rotary):
        executed["count"] = len(list(commands))

    argv = ["novadovetail.py", "--mode", "tails"]
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.setattr("laserdove.novadovetail.execute_commands", fake_execute)

    main()
    assert executed["count"] > 0


def test_main_exits_on_validation_error(monkeypatch):
    argv = ["novadovetail.py", "--mode", "tails"]
    monkeypatch.setattr(sys, "argv", argv)

    # Force a validation failure without breaking layout computation.
    monkeypatch.setattr(
        "laserdove.novadovetail.load_config_and_args",
        lambda args: (
            None, None, None, "tails", False, True, "host", 0, 0x88, 3.0, 40200, 200.0, None, False
        ),
    )
    monkeypatch.setattr(
        "laserdove.novadovetail.compute_tail_layout",
        lambda _: None,
    )
    monkeypatch.setattr(
        "laserdove.novadovetail.validate_all",
        lambda *_: ["fail"],
    )
    with pytest.raises(SystemExit):
        main()
