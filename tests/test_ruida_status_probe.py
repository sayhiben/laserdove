import types

import tools.ruida_status_probe as probe


class DummyLaser:
    STATUS_BIT_MOVING = 0x08
    STATUS_BIT_JOB_RUNNING = 0x02
    STATUS_BIT_PART_END = 0x100

    class MachineState:
        def __init__(self, status_bits=0, x_mm=None, y_mm=None):
            self.status_bits = status_bits
            self.x_mm = x_mm
            self.y_mm = y_mm
            self.z_mm = None

    def __init__(self, states):
        self.states = list(states)
        self.sent_jobs = []

    def _read_machine_state(self, read_positions=True):
        if not self.states:
            return None
        return self.states.pop(0)

    def send_rd_job(self, moves, job_z_mm=None, require_busy_transition=True):
        self.sent_jobs.append((moves, job_z_mm, require_busy_transition))


def test_decode_bits_formats_status():
    dummy = DummyLaser([])
    # Set both moving and job-running bits plus part_end.
    status = dummy.STATUS_BIT_MOVING | dummy.STATUS_BIT_JOB_RUNNING | dummy.STATUS_BIT_PART_END
    # For decode_bits, simulate RuidaLaser constants on the probe module.
    probe.RuidaLaser = DummyLaser  # type: ignore[attr-defined]
    text = probe.decode_bits(status)
    assert "busy_mask=True" in text
    assert "part_end=True" in text


def test_poll_status_once_logs_present_state(caplog):
    state = DummyLaser.MachineState(status_bits=0x01, x_mm=1.0, y_mm=2.0)
    laser = DummyLaser([state])
    with caplog.at_level("INFO"):
        ok = probe.poll_status_once(laser, "label")
    assert ok is True
    assert any("label" in rec.message for rec in caplog.records)


def test_run_with_capture_runs_action_and_polls(monkeypatch, caplog):
    laser = DummyLaser([DummyLaser.MachineState(status_bits=0x01, x_mm=0, y_mm=0)] * 5)

    calls = {"action": 0, "polls": 0}

    def fake_poll(lzr, label):
        calls["polls"] += 1
        return True

    monkeypatch.setattr(probe, "poll_status_once", fake_poll)
    with caplog.at_level("INFO"):
        probe.run_with_capture(
            laser, "test", lambda: calls.__setitem__("action", 1), interval=0.01, polls_after=2
        )

    assert calls["action"] == 1
    assert calls["polls"] >= 3  # before + after polls


def test_log_rd_summary_handles_empty(caplog):
    with caplog.at_level("INFO"):
        probe.log_rd_summary("tag", [], None)
    assert any("empty RD job" in rec.message for rec in caplog.records)


def test_log_rd_summary_decodes_bbox(monkeypatch, caplog):
    moves = [probe.RDMove(0.0, 0.0, 10.0, 0.0, False), probe.RDMove(5.0, 5.0, 10.0, 0.0, False)]

    class DummyParser:
        def __init__(self, buf=None, file=None, profile=None):
            self._bbox = [0.0, 0.0, 5.0, 5.0]

        def decode(self, debug=True):
            return None

    monkeypatch.setattr(probe, "RuidaParser", DummyParser)
    monkeypatch.setattr(
        probe, "build_rd_job", lambda moves, job_z_mm=None, filename="", air_assist=True: b"buf"
    )

    with caplog.at_level("INFO"):
        probe.log_rd_summary("tag", moves, 1.23)
    assert any("bbox=[0.000, 0.000]–[5.000, 5.000]" in rec.message for rec in caplog.records)


def test_main_smoke_without_actions(monkeypatch, caplog):
    args = types.SimpleNamespace(
        host="127.0.0.1",
        port=50200,
        source_port=40200,
        status_source_port=None,
        dual_socket=False,
        run_actions=False,
        log_level="INFO",
        timeout_s=0.1,
        interval=0.01,
        move_dist_mm=1.0,
        z_move_mm=0.5,
        polls_after=1,
        baseline_polls=1,
        magic=0x88,
    )

    # Minimal RuidaLaser stand-in that accepts any kwargs and returns a stable state.
    class FakeRuidaLaser(DummyLaser):
        def __init__(self, **kwargs):
            super().__init__([DummyLaser.MachineState(status_bits=0, x_mm=0, y_mm=0)])

    monkeypatch.setattr(probe, "setup_logging", lambda *_: None)
    monkeypatch.setattr(probe, "RuidaLaser", FakeRuidaLaser)
    monkeypatch.setattr(
        probe, "argparse", types.SimpleNamespace(ArgumentParser=lambda **_: DummyParser(args))
    )

    with caplog.at_level("INFO"):
        probe.main()


class DummyParser:
    def __init__(self, args):
        self.args = args

    def add_argument(self, *args, **kwargs):
        return None

    def set_defaults(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self.args, k, v)

    def parse_args(self):
        return self.args
