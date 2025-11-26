from __future__ import annotations

import logging
import socket
from typing import Optional

log = logging.getLogger(__name__)


class RuidaPanelInterface:
    """
    Lightweight helper for the unswizzled panel/“interface” port (UDP 50207).
    Useful for sanity-check jogging without sending full RD commands.
    """

    ACK = 0xCC
    PORT = 50207
    SRC_PORT = 40207

    CMD_STOP = b"\xA5\x50\x09"
    CMD_ORIGIN = b"\xA5\x50\x08"
    CMD_FRAME = b"\xA5\x53\x00"
    CMD_Y_DOWN = b"\xA5\x50\x03"
    CMD_Y_UP = b"\xA5\x51\x03"
    CMD_Z_DOWN = b"\xA5\x50\x0A"
    CMD_Z_UP = b"\xA5\x51\x0A"

    def __init__(
        self,
        host: str,
        port: int = PORT,
        *,
        source_port: int = SRC_PORT,
        timeout_s: float = 2.0,
        socket_factory=socket.socket,
        dry_run: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self._socket_factory = socket_factory
        self.sock: Optional[socket.socket] = None
        self.dry_run = dry_run

    def _ensure_socket(self) -> None:
        if self.dry_run:
            return
        if self.sock is not None:
            return
        self.sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(self.timeout_s)
        try:
            self.sock.bind(("", self.source_port))
        except Exception as e:
            log.warning("Panel UDP bind failed (%s); falling back to ephemeral port", e)
            try:
                self.sock.bind(("", 0))
            except Exception:
                self.dry_run = True
                self.sock = None
        # Best-effort handshake: send 0xCC to elicit 0xCC ACK on this port.
        if self.sock is not None:
            try:
                self.sock.sendto(bytes([self.ACK]), (self.host, self.port))
                data, _ = self.sock.recvfrom(8)
                if data and data[0] != self.ACK:
                    log.debug("[RUDA PANEL] Unexpected handshake response %s", data.hex(" "))
            except Exception:
                # Ignore handshake failures; some controllers stay silent until a real command.
                pass

    def send_command(self, cmd: bytes) -> None:
        """
        Send an unswizzled panel command and expect a single-byte ACK (0xCC).
        """
        if self.dry_run:
            log.info("[RUDA PANEL DRY] %s", cmd.hex(" "))
            return
        self._ensure_socket()
        if self.sock is None:
            log.info("[RUDA PANEL DRY] %s", cmd.hex(" "))
            return
        payload = cmd
        self.sock.sendto(payload, (self.host, self.port))
        try:
            data, _ = self.sock.recvfrom(8)
            if not data:
                log.warning("[RUDA PANEL] Empty response for command %s", cmd.hex(" "))
            elif data[0] != self.ACK:
                log.warning("[RUDA PANEL] Unexpected response %s", data.hex(" "))
        except socket.timeout:
            log.warning("[RUDA PANEL] ACK timeout for command %s", cmd.hex(" "))
