import types

from laserdove.hardware.ruida import RuidaPanelInterface


class DummySock:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.sent = []

    def settimeout(self, t):
        self.timeout = t

    def bind(self, addr):
        self.bound = addr

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def recvfrom(self, n):
        if not self.responses:
            raise TimeoutError()
        resp = self.responses.pop(0)
        return resp, ("host", 0)


def test_panel_interface_sends_and_accepts_ack(monkeypatch):
    dummy = DummySock(responses=[bytes([RuidaPanelInterface.ACK])])
    iface = RuidaPanelInterface(
        host="127.0.0.1",
        port=50207,
        socket_factory=lambda *args, **kwargs: dummy,
        dry_run=False,
    )
    iface.send_command(RuidaPanelInterface.CMD_STOP)
    assert dummy.sent, "Should have sent a panel command"

