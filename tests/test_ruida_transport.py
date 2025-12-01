import socket

import pytest

from laserdove.hardware import ruida_transport
from laserdove.hardware.ruida_transport import RuidaUDPClient


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

    def recvfrom(self, _):
        if not self.responses:
            raise socket.timeout()
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp, ("host", 0)


@pytest.fixture(autouse=True)
def patch_swizzle_checksum_unswizzle(monkeypatch):
    monkeypatch.setattr(ruida_transport, "swizzle", lambda data, magic=0x88: b"SW" + data)
    monkeypatch.setattr(ruida_transport, "checksum", lambda data: b"C")
    monkeypatch.setattr(ruida_transport, "unswizzle", lambda data, magic=0x88: b"UN" + data)
    yield


def test_send_packets_dry_run_skips_socket(monkeypatch):
    client = RuidaUDPClient("127.0.0.1", dry_run=True, socket_factory=lambda: None)
    assert client.send_packets(b"abc") is None
    assert client.sock is None


def test_send_packets_ack_and_reply(monkeypatch):
    fake = FakeSocket(responses=[b"\xc6", b"PAY"])
    client = RuidaUDPClient("host", socket_factory=lambda: fake, dry_run=False)
    reply = client.send_packets(b"abc", expect_reply=True)

    # swizzle + checksum applied once, ack then payload read
    assert fake.sent and fake.sent[0][0] == b"CSWabc"
    assert reply == b"UNPAY"


def test_send_packets_retries_on_timeout(monkeypatch):
    fake = FakeSocket(responses=[socket.timeout(), b"\xc6"])
    client = RuidaUDPClient("host", socket_factory=lambda: fake, dry_run=False)
    reply = client.send_packets(b"abc", expect_reply=False)

    assert reply == b"UN"  # unswizzle of empty payload
    assert len(fake.sent) == 2  # retried once after timeout


def test_send_packets_raises_on_nack(monkeypatch):
    fake = FakeSocket(responses=[b"\x46"])
    client = RuidaUDPClient("host", socket_factory=lambda: fake, dry_run=False)
    with pytest.raises(RuntimeError):
        client.send_packets(b"abc")
