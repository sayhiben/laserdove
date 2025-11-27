import socket

from laserdove.hardware.ruida import RuidaLaser


class FakeSocket:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []
        self.bound = None
        self.timeout = None

    def settimeout(self, t):
        self.timeout = t

    def bind(self, addr):
        self.bound = addr

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, n):
        if not self.responses:
            raise socket.timeout()
        resp = self.responses.pop(0)
        return resp, ("host", 12345)

    def close(self):
        pass


def fake_socket_factory(fake):
    def factory(*args, **kwargs):
        return fake
    return factory


def _ready_responses(laser: RuidaLaser):
    def make_payload(address: bytes, data: bytes) -> bytes:
        payload = b"\xDA\x01" + address + data
        swizzled = laser._swizzle(payload)
        return laser._checksum(swizzled) + swizzled

    return [
        bytes([RuidaLaser.ACK]),
        make_payload(RuidaLaser.MEM_MACHINE_STATUS, (0).to_bytes(4, "big")),
        bytes([RuidaLaser.ACK]),
        make_payload(RuidaLaser.MEM_CURRENT_X, laser._encode_abscoord_mm(laser.x)),
        bytes([RuidaLaser.ACK]),
        make_payload(RuidaLaser.MEM_CURRENT_Y, laser._encode_abscoord_mm(laser.y)),
        bytes([RuidaLaser.ACK]),
        make_payload(RuidaLaser.MEM_CURRENT_Z, laser._encode_abscoord_mm(laser.z)),
    ]


def test_udp_ack_and_retry(monkeypatch):
    # First packet NACK then ACK; second packet ACK.
    fake = FakeSocket([])
    laser = RuidaLaser(
        host="127.0.0.1",
        port=50200,
        dry_run=False,
        timeout_s=0.1,
        socket_factory=fake_socket_factory(fake),
    )
    ready = _ready_responses(laser)
    fake.responses = ready + [bytes([0x46]), bytes([0xC6]), bytes([0xC6])]
    laser.MTU = 4  # force multiple chunks
    laser.set_laser_power(10.0)
    assert fake.sent  # packets emitted
    assert fake.bound is not None


def test_udp_timeout_raises(monkeypatch):
    fake = FakeSocket([])
    laser = RuidaLaser(
        host="127.0.0.1",
        port=50200,
        dry_run=False,
        timeout_s=0.05,
        socket_factory=fake_socket_factory(fake),
    )
    fake.responses = _ready_responses(laser)
    try:
        laser.set_laser_power(5.0)
    except RuntimeError:
        return
    assert False, "Expected timeout runtime error"
