import socket

import pytest

from laserdove.hardware.ruida import RuidaLaser, RDMove
from laserdove.model import Command, CommandType
from laserdove.hardware.ruida_common import should_force_speed, clamp_power


class ScriptedSocket:
    def __init__(self, responses, *, bind_failures=0):
        self.responses = list(responses)
        self.sent = []
        self.timeout = None
        self.bind_calls = []
        self.bind_failures = bind_failures

    def settimeout(self, timeout):
        self.timeout = timeout

    def bind(self, addr):
        self.bind_calls.append(addr)
        if self.bind_failures > 0:
            self.bind_failures -= 1
            raise PermissionError("bind failed")
        return None

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, _):
        if not self.responses:
            raise socket.timeout()
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_ensure_socket_falls_back_to_ephemeral_port(monkeypatch):
    factory_socket = ScriptedSocket([], bind_failures=1)
    laser = RuidaLaser("host", source_port=40200, socket_factory=lambda *_, **__: factory_socket, dry_run=False)
    laser._ensure_socket()
    assert factory_socket.bind_calls[0][1] == 40200
    assert factory_socket.bind_calls[1][1] == 0
    assert laser.sock is factory_socket


def test_ensure_socket_switches_to_dry_run_after_bind_failures(monkeypatch):
    factory_socket = ScriptedSocket([], bind_failures=2)
    laser = RuidaLaser("host", source_port=40200, socket_factory=lambda *_, **__: factory_socket, dry_run=False)
    laser._ensure_socket()
    assert laser.dry_run is True
    assert laser.sock is None


def test_send_packets_handles_nack_and_returns_unswizzled_reply():
    def make_reply(lzr: RuidaLaser, payload: bytes) -> bytes:
        swizzled = lzr._swizzle(payload)
        return lzr._checksum(swizzled) + swizzled

    laser = RuidaLaser("127.0.0.1", socket_factory=lambda *_, **__: ScriptedSocket([]), dry_run=False)
    reply_payload = b"ok"
    sock = ScriptedSocket(
        [
            (bytes([RuidaLaser.NACK]), ("host", 0)),  # first attempt nack
            (bytes([RuidaLaser.ACK]), ("host", 0)),   # retry ack
            (make_reply(laser, reply_payload), ("host", 0)),  # reply
        ]
    )
    laser.sock = sock

    result = laser._send_packets(b"\x01\x02\x03", expect_reply=True)
    assert result == reply_payload
    assert len(sock.sent) == 2  # resent after NACK


def test_set_laser_power_movement_only_sends_single_off(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True, movement_only=True)
    calls = []
    monkeypatch.setattr(laser, "_wait_for_ready", lambda: None)
    monkeypatch.setattr(laser, "_send_packets", lambda payload=None, expect_reply=False: calls.append(payload))

    laser.set_laser_power(50.0)
    laser.set_laser_power(20.0)
    assert calls  # only one packet sent
    assert len(calls) == 1
    assert laser.power == 0.0
    assert laser._movement_only_power_sent is True
    assert laser._last_requested_power == 20.0


def test_set_laser_power_skips_when_same_value(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True, movement_only=False)
    monkeypatch.setattr(laser, "_wait_for_ready", lambda: None)
    sent = []
    monkeypatch.setattr(laser, "_send_packets", lambda payload=None: sent.append(payload))
    laser.set_laser_power(10.0)
    laser.set_laser_power(10.0)
    assert len(sent) == 1  # second call skipped


def test_run_sequence_with_rotary_flushes_blocks(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    blocks = []

    def fake_send(block_moves, block_z=None, **kwargs):
        job_z = kwargs.get("job_z_mm", block_z)
        blocks.append(([(mv.x_mm, mv.y_mm, mv.is_cut) for mv in block_moves], job_z))

    laser.send_rd_job = fake_send  # type: ignore

    class DummyRotary:
        def __init__(self):
            self.angles = []

        def rotate_to(self, angle_deg, speed_dps):
            self.angles.append(angle_deg)

    cmds = [
        Command(type=CommandType.SET_LASER_POWER, power_pct=50.0),
        Command(type=CommandType.MOVE, x=0.0, y=0.0, z=0.0, speed_mm_s=100.0),
        Command(type=CommandType.CUT_LINE, x=1.0, y=1.0, z=0.0, speed_mm_s=50.0),
        Command(type=CommandType.MOVE, x=0.0, y=2.0, z=1.0, speed_mm_s=100.0),  # z change -> flush
        Command(type=CommandType.ROTATE, angle_deg=90.0, speed_mm_s=10.0),        # flush before rotate
        Command(type=CommandType.CUT_LINE, x=1.0, y=2.0, z=1.0, speed_mm_s=50.0),
    ]
    laser.run_sequence_with_rotary(cmds, DummyRotary())

    assert len(blocks) == 3  # before z change, before rotate, after rotate
    assert blocks[0][1] == 0.0
    assert blocks[1][1] == 1.0
    assert blocks[2][1] == 1.0


def test_run_sequence_movement_only_forces_zero_power(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True, movement_only=True)
    sent_blocks = []

    def fake_send(moves, job_z_mm=None, **kwargs):
        sent_blocks.append(moves)

    laser.send_rd_job = fake_send  # type: ignore
    class DummyRotary:
        def rotate_to(self, angle_deg, speed_dps):
            pass

    cmds = [
        Command(type=CommandType.SET_LASER_POWER, power_pct=60.0),
        Command(type=CommandType.MOVE, x=0.0, y=0.0, speed_mm_s=100.0),
        Command(type=CommandType.CUT_LINE, x=1.0, y=0.0, speed_mm_s=50.0),
    ]
    laser.run_sequence_with_rotary(cmds, DummyRotary())
    assert sent_blocks
    for mv in sent_blocks[0]:
        assert mv.power_pct == 0.0


def test_run_sequence_parks_before_rotary(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    sends = []
    laser.send_rd_job = lambda moves, job_z_mm=None, **kwargs: sends.append((moves, job_z_mm))  # type: ignore
    moves_called = []
    def track_move(x=None, y=None, z=None, speed=None):
        moves_called.append((x, y, z, speed))
    monkeypatch.setattr(laser, "move", track_move)

    class DummyRotary:
        def __init__(self):
            self.rotations = []

        def rotate_to(self, angle_deg, speed_dps):
            self.rotations.append(angle_deg)

    cmds = [
        Command(type=CommandType.MOVE, x=5.0, y=5.0, z=1.0, speed_mm_s=100.0),
        Command(type=CommandType.ROTATE, angle_deg=90.0, speed_mm_s=10.0),
    ]

    laser.run_sequence_with_rotary(cmds, DummyRotary())

    assert moves_called, "Head should be parked before rotary move"
    assert moves_called[0][0:3] == (0.0, 0.0, 1.0)  # back to origin with initial Z


def test_rotary_parking_orders_z_first_if_below(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    moves_called = []
    monkeypatch.setattr(laser, "move", lambda x=None, y=None, z=None, speed=None: moves_called.append((x, y, z)))
    class DummyRotary:
        def rotate_to(self, angle_deg, speed_dps):
            pass
    cmds = [
        Command(type=CommandType.MOVE, x=5.0, y=5.0, z=1.0, speed_mm_s=100.0),  # origin z
        Command(type=CommandType.MOVE, x=6.0, y=6.0, z=0.0, speed_mm_s=50.0),   # below origin
        Command(type=CommandType.ROTATE, angle_deg=45.0, speed_mm_s=5.0),
    ]
    laser.run_sequence_with_rotary(cmds, DummyRotary())
    assert moves_called[-2:] == [(None, None, 1.0), (0.0, 0.0, None)]  # raise Z first, then XY


def test_rotary_parking_orders_z_last_if_above(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    moves_called = []
    monkeypatch.setattr(laser, "move", lambda x=None, y=None, z=None, speed=None: moves_called.append((x, y, z)))
    class DummyRotary:
        def rotate_to(self, angle_deg, speed_dps):
            pass
    cmds = [
        Command(type=CommandType.MOVE, x=5.0, y=5.0, z=1.0, speed_mm_s=100.0),  # origin z
        Command(type=CommandType.MOVE, x=6.0, y=6.0, z=2.0, speed_mm_s=50.0),   # above origin
        Command(type=CommandType.ROTATE, angle_deg=45.0, speed_mm_s=5.0),
    ]
    laser.run_sequence_with_rotary(cmds, DummyRotary())
    assert moves_called[-2:] == [(0.0, 0.0, None), (None, None, 1.0)]  # XY first, then lower Z


def test_send_rd_job_zeroes_power_when_movement_only():
    moves = [RDMove(x_mm=0, y_mm=0, speed_mm_s=10.0, power_pct=60.0, is_cut=True)]
    laser = RuidaLaser("127.0.0.1", movement_only=True, dry_run=True)
    sent = []

    def fake_send(payload=None, expect_reply=False):
        if expect_reply:
            return None
        sent.append(payload)

    laser._send_packets = fake_send  # type: ignore
    laser.send_rd_job(moves, job_z_mm=None)
    assert moves[0].power_pct == 0.0
    assert sent  # payload attempted


def test_send_packets_dry_run_returns_none():
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    assert laser._send_packets(b"\x00") is None


def test_wait_for_ready_dry_run_returns_state():
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    state = laser._wait_for_ready()
    assert state.status_bits == 0
    assert state.x_mm == 0.0


def test_wait_for_ready_raises_after_attempts(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=False)
    # Force busy state forever
    busy_state = RuidaLaser.MachineState(status_bits=RuidaLaser.STATUS_BIT_MOVING, x_mm=None, y_mm=None)
    monkeypatch.setattr(laser, "_read_machine_state", lambda: busy_state)
    monkeypatch.setattr("laserdove.hardware.ruida_laser.time.sleep", lambda *_: None)
    with pytest.raises(RuntimeError):
        laser._wait_for_ready(max_attempts=2, delay_s=0, require_busy_transition=True)


def test_get_memory_value_truncated(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    # Reply shorter than expected
    monkeypatch.setattr(laser, "_send_packets", lambda payload=None, expect_reply=True: b"\xDA\x01" + laser.MEM_MACHINE_STATUS + b"\x00")
    assert laser._get_memory_value(laser.MEM_MACHINE_STATUS, expected_len=4) is None


def test_move_skips_when_no_xy(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    laser.power = 5.0
    sends = []
    monkeypatch.setattr(laser, "_wait_for_ready", lambda: None)
    monkeypatch.setattr(laser, "_send_packets", lambda payload=None: sends.append(payload))
    monkeypatch.setattr(laser, "set_laser_power", lambda p: sends.append(("power", p)))
    laser.move(speed=10.0)
    assert sends[0] == ("power", 0.0)  # power reset
    assert len(sends) == 2  # speed set but no MOVE when x/y None


def test_set_speed_skips_same_value(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    laser._last_speed_ums = 1000
    monkeypatch.setattr(laser, "_send_packets", lambda payload=None: (_ for _ in ()).throw(AssertionError("should not send")))
    laser._set_speed(1.0)  # 1 mm/s -> 1000 ums


def test_cleanup_closes_socket():
    class CloseTracker:
        def __init__(self):
            self.closed = False

        def close(self):
            self.closed = True

    laser = RuidaLaser("127.0.0.1", dry_run=True)
    laser.sock = CloseTracker()  # type: ignore
    laser.cleanup()
    assert laser.sock is None
    assert laser.dry_run is True  # unchanged


def test_should_force_speed_and_clamp_power_helpers():
    assert should_force_speed(None, 1.0) == (1000, True)
    assert should_force_speed(1000, 1.0) == (1000, False)
    assert clamp_power(10.0, current_power=5.0) == (10.0, True)
    assert clamp_power(5.0, current_power=5.0) == (5.0, False)


def test_send_packets_raises_on_unexpected_response():
    sock = ScriptedSocket([(bytes([0x01]), ("host", 0))])
    laser = RuidaLaser("127.0.0.1", socket_factory=lambda *_, **__: sock, dry_run=False)
    with pytest.raises(RuntimeError):
        laser._send_packets(b"\x01")


def test_get_memory_value_unexpected_header_returns_none(monkeypatch):
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    # Reply does not start with expected header
    monkeypatch.setattr(laser, "_send_packets", lambda payload=None, expect_reply=True: b"\x00\x01\x02")
    assert laser._get_memory_value(laser.MEM_MACHINE_STATUS, expected_len=4) is None


def test_send_rd_job_writes_file(tmp_path, monkeypatch):
    moves = [RDMove(x_mm=0, y_mm=0, speed_mm_s=10.0, power_pct=60.0, is_cut=True)]
    laser = RuidaLaser("127.0.0.1", dry_run=True, save_rd_dir=tmp_path)
    monkeypatch.setattr(laser, "_send_packets", lambda payload=None, expect_reply=False: None)
    laser.send_rd_job(moves, job_z_mm=0.5)
    files = list(tmp_path.glob("job_*.rd"))
    assert files, "RD file should be written when save_rd_dir is set"
