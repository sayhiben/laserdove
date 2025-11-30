from __future__ import annotations

import logging
import socket
from typing import Optional, List

from .ruida_common import swizzle, unswizzle, checksum

log = logging.getLogger(__name__)


class RuidaUDPClient:
    """
    Low-level UDP transport for Ruida controllers.
    Handles swizzle/checksum/chunking/ACK and optional reply collection.
    """

    ACK = 0xC6
    NACK = 0x46
    ACK_VALUES = {ACK, 0xCC}
    NACK_VALUES = {NACK, 0xCF}
    MTU = 1470

    def __init__(
        self,
        host: str,
        port: int = 50200,
        *,
        source_port: int = 40200,
        timeout_s: float = 3.0,
        magic: int = 0x88,
        dry_run: bool = False,
        socket_factory=socket.socket,
    ) -> None:
        """
        Initialize a UDP client for a Ruida controller.

        Args:
            host: Controller hostname or IP.
            port: UDP port for actions (default 50200).
            source_port: Local UDP source port to bind.
            timeout_s: Socket timeout for ACK/reply waits.
            magic: Swizzle magic key.
            dry_run: If True, log packets instead of sending.
            socket_factory: Optional socket factory for testing.
        """
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self.magic = magic
        self.dry_run = dry_run
        self._socket_factory = socket_factory
        self.sock: Optional[socket.socket] = None

    def _ensure_socket(self) -> None:
        """
        Create and bind the UDP socket if not already present.

        Returns:
            None. Sets ``self.sock`` unless dry-run or binding fails.
        """
        if self.dry_run:
            return
        if self.sock is not None:
            return
        if self._socket_factory is socket.socket:
            sock = self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM)
        else:
            sock = self._socket_factory()
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock = sock
        if hasattr(self.sock, "settimeout"):
            try:
                self.sock.settimeout(self.timeout_s)
            except Exception:
                pass
        try:
            self.sock.bind(("", self.source_port))
        except Exception:
            log.warning("Falling back to ephemeral source port for Ruida UDP (bind failed)")
            try:
                self.sock.bind(("", 0))
            except Exception:
                log.error("Unable to bind UDP socket; switching to dry_run for safety")
                self.dry_run = True
                self.sock = None

    def send_packets(self, payload: bytes, *, expect_reply: bool = False) -> Optional[bytes]:
        """
        Swizzle, chunk, prepend checksum, and send with ACK wait. Optionally collect a follow-on reply packet
        (e.g., GET_SETTING responses) and return its unswizzled payload.

        Args:
            payload: Raw payload to transmit (unswizzled, without checksum).
            expect_reply: Whether to read an additional reply packet after ACKs.

        Returns:
            None for dry-run/no reply, otherwise the unswizzled reply bytes.
        """
        self._ensure_socket()
        swizzled = swizzle(payload, magic=self.magic)
        if self.dry_run:
            log.info("[RUIDA UDP DRY] %s", swizzled.hex(" "))
            return None
        if self.sock is None:
            log.info("[RUIDA UDP DRY] %s", swizzled.hex(" "))
            return None

        # Chunk
        chunks: List[bytes] = []
        start = 0
        while start < len(swizzled):
            end = min(start + self.MTU, len(swizzled))
            chunk = swizzled[start:end]
            chunk = checksum(chunk) + chunk
            chunks.append(chunk)
            start = end

        reply = b""
        payload_only = b""
        for idx, chunk in enumerate(chunks):
            retry = 0
            while True:
                self.sock.sendto(chunk, (self.host, self.port))
                try:
                    data, _ = self.sock.recvfrom(8)
                except socket.timeout:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP ACK timeout")
                    continue
                if not data:
                    retry += 1
                    if retry > 3:
                        raise RuntimeError("UDP empty response")
                    continue
                if data[0] in self.ACK_VALUES:
                    break
                if data[0] in self.NACK_VALUES and idx == 0:
                    raise RuntimeError("UDP NACK received")
                if data[0] not in self.ACK_VALUES and expect_reply:
                    reply = data
                    break
                if data[0] not in self.ACK_VALUES:
                    # Unexpected reply; keep retrying
                    retry += 1
                    if retry > 3:
                        raise RuntimeError(f"Unexpected UDP response {data.hex(' ')}")
                    continue
            # Collect follow-on reply if requested and not already captured
            if expect_reply and not reply:
                try:
                    maybe_payload, _ = self.sock.recvfrom(1024)
                    if maybe_payload:
                        payload_only = maybe_payload
                except socket.timeout:
                    pass

        unswizzled = unswizzle(payload_only, magic=self.magic)
        if not unswizzled and reply:
            unswizzled = reply
        return unswizzled
