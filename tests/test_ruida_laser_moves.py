from __future__ import annotations

import math

from laserdove.hardware.rd_builder import RDMove
from laserdove.hardware.ruida_common import encode_abscoord_mm
from laserdove.hardware.ruida_laser import RuidaLaser


class FakeUDP:
    def __init__(self, replies: dict[bytes, bytes | list[bytes]] | None = None) -> None:
        self.replies = replies or {}
        self.sent: list[bytes] = []

    def send_packets(self, payload: bytes, *, expect_reply: bool = False):
        self.sent.append(payload)
        if not expect_reply:
            return None
        if payload.startswith(b"\xda\x00"):
            addr = payload[2:4]
            reply = self.replies.get(addr)
            if isinstance(reply, list):
                return reply.pop(0) if reply else None
            return reply
        return None


def test_get_memory_value_parses_reply():
    addr = RuidaLaser.MEM_MACHINE_STATUS
    data = b"\x01\x02\x03\x04"
    addr2 = RuidaLaser.MEM_CURRENT_X
    data2 = encode_abscoord_mm(12.0)
    addr3 = RuidaLaser.MEM_CURRENT_Y
    truncated = b"\xda\x01" + addr3 + b"\x01\x02"
    replies = {
        addr: b"\xda\x01" + addr + data,
        addr2: addr2 + data2,
        addr3: truncated,
    }
    laser = RuidaLaser(host="0.0.0.0", dry_run=True)
    laser._udp = FakeUDP(replies)

    assert laser._get_memory_value(addr, expected_len=4) == data
    assert laser._get_memory_value(addr2, expected_len=5) == data2
    assert laser._get_memory_value(addr3, expected_len=5) is None


def test_read_machine_state_captures_z_origin_and_sign():
    status_bytes = (0).to_bytes(4, byteorder="big")
    x_bytes = encode_abscoord_mm(1.0)
    y_bytes = encode_abscoord_mm(2.0)
    z_bytes_1 = encode_abscoord_mm(5.0)
    z_bytes_2 = encode_abscoord_mm(7.0)
    replies = {
        RuidaLaser.MEM_MACHINE_STATUS: [
            b"\xda\x01" + RuidaLaser.MEM_MACHINE_STATUS + status_bytes,
            b"\xda\x01" + RuidaLaser.MEM_MACHINE_STATUS + status_bytes,
        ],
        RuidaLaser.MEM_CURRENT_X: [
            b"\xda\x01" + RuidaLaser.MEM_CURRENT_X + x_bytes,
            b"\xda\x01" + RuidaLaser.MEM_CURRENT_X + x_bytes,
        ],
        RuidaLaser.MEM_CURRENT_Y: [
            b"\xda\x01" + RuidaLaser.MEM_CURRENT_Y + y_bytes,
            b"\xda\x01" + RuidaLaser.MEM_CURRENT_Y + y_bytes,
        ],
        RuidaLaser.MEM_CURRENT_Z: [
            b"\xda\x01" + RuidaLaser.MEM_CURRENT_Z + z_bytes_1,
            b"\xda\x01" + RuidaLaser.MEM_CURRENT_Z + z_bytes_2,
        ],
    }

    laser = RuidaLaser(host="0.0.0.0", dry_run=False, z_positive_moves_bed_up=False)
    laser._udp = FakeUDP(replies)

    state1 = laser._read_machine_state(read_positions=True)
    state2 = laser._read_machine_state(read_positions=True)

    assert state1 is not None
    assert state1.x_mm == 1.0
    assert state1.y_mm == 2.0
    assert math.isclose(state1.z_mm or 0.0, 0.0, abs_tol=1e-6)
    assert math.isclose(laser._z_origin_mm or 0.0, 5.0, abs_tol=1e-6)
    assert state2 is not None
    assert math.isclose(state2.z_mm or 0.0, -2.0, abs_tol=1e-6)


def test_move_emits_z_speed_and_xy():
    laser = RuidaLaser(host="0.0.0.0", dry_run=True)
    laser._udp = FakeUDP()

    laser.move(x=1.0, y=2.0, z=3.0, speed=10.0)

    payloads = laser._udp.sent
    assert payloads[0].startswith(b"\x80\x03")
    assert payloads[1].startswith(b"\xc9\x02")
    assert payloads[2].startswith(b"\x88")


def test_set_laser_power_movement_only_sends_once():
    laser = RuidaLaser(host="0.0.0.0", dry_run=True, movement_only=True)
    laser._udp = FakeUDP()

    laser.set_laser_power(50.0)
    laser.set_laser_power(10.0)

    assert len(laser._udp.sent) == 1
    assert laser._udp.sent[0].startswith(b"\xc7")
    assert math.isclose(laser.power, 0.0, abs_tol=1e-9)


def test_pre_cut_warmup_builds_travel_job():
    laser = RuidaLaser(
        host="0.0.0.0",
        dry_run=False,
        pre_cut_warmup_s=2.0,
        air_assist=True,
        inline_fan_on=True,
    )
    laser._udp = FakeUDP()
    laser._wait_for_ready = lambda **kwargs: None
    captured: dict[str, list[RDMove]] = {}

    def fake_send_rd_job(moves, **_kwargs):
        captured["moves"] = moves

    laser.send_rd_job = fake_send_rd_job  # type: ignore[assignment]

    laser._pre_cut_warmup(origin_x=0.0, origin_y=0.0)

    assert captured["moves"]
    assert all(not mv.is_cut for mv in captured["moves"])
    assert laser._udp.sent[0] == b"\xca\x01\x13"
    assert laser._udp.sent[1] == b"\xca\x13"


def test_send_rd_job_zeroes_power_in_movement_only():
    laser = RuidaLaser(host="0.0.0.0", dry_run=True, movement_only=True)
    laser._udp = FakeUDP()
    laser._read_machine_state = lambda read_positions=True: None

    moves = [
        RDMove(
            x_mm=0.0,
            y_mm=0.0,
            speed_mm_s=10.0,
            power_pct=60.0,
            is_cut=True,
            z_mm=1.0,
        )
    ]
    laser.send_rd_job(moves, job_z_mm=None, start_z_mm=0.0)

    assert all(math.isclose(mv.power_pct, 0.0, abs_tol=1e-9) for mv in moves)
    assert math.isclose(laser.z, 1.0, abs_tol=1e-9)
    assert len(laser._udp.sent) == 1
