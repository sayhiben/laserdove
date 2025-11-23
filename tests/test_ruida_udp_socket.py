import socket

from hardware.ruida import RuidaLaser


class DummySock:
    def __init__(self, responses):
        self.responses = list(responses)
        self.sent = []

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


def test_ruida_udp_send_with_ack(monkeypatch):
    # First packet gets NACK then ACK, second packet ACK.
    dummy = DummySock([bytes([0x46]), bytes([0xC6]), bytes([0xC6])])

    def fake_socket(*args, **kwargs):
        return dummy

    # Use socket_factory to avoid bind errors in sandbox.
    laser = RuidaLaser(
        host="127.0.0.1",
        port=50200,
        dry_run=False,
        timeout_s=0.1,
        socket_factory=lambda *args, **kwargs: dummy,
    )
    # Force small MTU to produce multiple chunks.
    laser.MTU = 4
    laser.set_laser_power(10.0)
    assert dummy.sent, "Should have sent UDP packets"
