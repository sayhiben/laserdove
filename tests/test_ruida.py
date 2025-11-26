import socket

import pytest

from laserdove.hardware.ruida import RuidaLaser


class FakeSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout

    def bind(self, addr):
        return None

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, _):
        if not self.responses:
            raise socket.timeout()
        return self.responses.pop(0)


def _make_swizzled_response(laser: RuidaLaser, address: bytes, data: bytes) -> bytes:
    payload = b"\xDA\x01" + address + data
    swizzled = laser._swizzle(payload)
    return laser._checksum(swizzled) + swizzled


def test_swizzle_round_trip():
    laser = RuidaLaser("127.0.0.1", dry_run=True)
    payload = bytes(range(16))
    assert laser._unswizzle(laser._swizzle(payload)) == payload


def test_get_memory_value_reads_response_with_checksum():
    laser = RuidaLaser("127.0.0.1", socket_factory=lambda *_, **__: FakeSocket([]))
    status_payload = _make_swizzled_response(
        laser,
        RuidaLaser.MEM_MACHINE_STATUS,
        RuidaLaser.STATUS_BIT_JOB_RUNNING.to_bytes(4, "big"),
    )
    laser.sock = FakeSocket(
        [
            (bytes([RuidaLaser.ACK]), ("127.0.0.1", 0)),
            (status_payload, ("127.0.0.1", 0)),
        ]
    )

    value = laser._get_memory_value(RuidaLaser.MEM_MACHINE_STATUS, expected_len=4)
    assert value == RuidaLaser.STATUS_BIT_JOB_RUNNING.to_bytes(4, "big")


def test_wait_for_ready_polls_until_idle_and_updates_position():
    # First poll reports busy, second is clear. Positions are updated from the ready snapshot.
    def build_snapshot(laser: RuidaLaser, status_bits: int, x_mm: float, y_mm: float):
        return [
            (bytes([RuidaLaser.ACK]), (laser.host, laser.port)),
            (_make_swizzled_response(laser, RuidaLaser.MEM_MACHINE_STATUS, status_bits.to_bytes(4, "big")), (laser.host, laser.port)),
            (bytes([RuidaLaser.ACK]), (laser.host, laser.port)),
            (_make_swizzled_response(laser, RuidaLaser.MEM_CURRENT_X, laser._encode_abscoord_mm(x_mm)), (laser.host, laser.port)),
            (bytes([RuidaLaser.ACK]), (laser.host, laser.port)),
            (_make_swizzled_response(laser, RuidaLaser.MEM_CURRENT_Y, laser._encode_abscoord_mm(y_mm)), (laser.host, laser.port)),
        ]

    laser = RuidaLaser("127.0.0.1", socket_factory=lambda *_, **__: FakeSocket([]))
    busy_snapshot = build_snapshot(laser, RuidaLaser.STATUS_BIT_MOVING, 10.0, 20.0)
    ready_snapshot = build_snapshot(laser, 0, 11.0, 21.0)
    laser.sock = FakeSocket(busy_snapshot + ready_snapshot)

    state = laser._wait_for_ready(max_attempts=3, delay_s=0, require_busy_transition=True)

    assert state.status_bits == 0
    assert pytest.approx(laser.x) == 11.0
    assert pytest.approx(laser.y) == 21.0
