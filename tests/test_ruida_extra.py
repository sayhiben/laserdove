import socket

import pytest

from laserdove.hardware.ruida import RuidaLaser, RDMove
from laserdove.model import Command, CommandType


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


def test_send_rd_job_zeroes_power_when_movement_only():
    moves = [RDMove(x_mm=0, y_mm=0, speed_mm_s=10.0, power_pct=60.0, is_cut=True)]
    laser = RuidaLaser("127.0.0.1", movement_only=True, dry_run=True)
    sent = []
    laser._send_packets = lambda payload=None: sent.append(payload)  # type: ignore
    laser.send_rd_job(moves, job_z_mm=None)
    assert moves[0].power_pct == 0.0
    assert sent  # payload attempted


def test_send_packets_dry_run_returns_none():
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    assert laser._send_packets(b"\x00") is None
