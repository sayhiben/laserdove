import sys

import tools.ruida_status_probe as probe


def test_status_probe_skips_actions_by_default(monkeypatch, capsys):
    calls = {"init": 0, "read": 0, "send": 0}

    class FakeState:
        status_bits = 0
        x_mm = 0.0
        y_mm = 0.0

    class FakeLaser:
        STATUS_BIT_MOVING = 0x01
        STATUS_BIT_JOB_RUNNING = 0x02
        STATUS_BIT_PART_END = 0x04

        def __init__(self, *args, **kwargs):
            calls["init"] += 1

        def _read_machine_state(self):
            calls["read"] += 1
            return FakeState()

        def send_rd_job(self, *args, **kwargs):
            calls["send"] += 1

    # Avoid waiting during the test.
    monkeypatch.setattr(probe.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(probe, "RuidaLaser", FakeLaser)

    monkeypatch.setenv("PYTHONUNBUFFERED", "1")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ruida_status_probe",
            "--host",
            "1.2.3.4",
            "--baseline-polls",
            "2",
            "--polls-after",
            "1",
        ],
    )

    probe.main()

    out = capsys.readouterr().out
    assert "Actions skipped" in out
    assert calls["send"] == 0  # no jobs should be sent without --run-actions
    assert calls["read"] == 2  # two baseline polls were issued
