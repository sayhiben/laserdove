from __future__ import annotations

import logging
from pathlib import Path
from typing import NamedTuple, Optional

from .ruida_jobs import RuidaJobMixin
from .ruida_sequence import RuidaSequenceMixin
from .ruida_status import RuidaStatusMixin
from .ruida_transport import RuidaUDPClient

log = logging.getLogger(__name__)


class RuidaLaser(RuidaStatusMixin, RuidaJobMixin, RuidaSequenceMixin):
    """
    UDP-based Ruida transport (port 50200) using swizzle magic 0x88.
    Uses the shared RuidaUDPClient for send/ACK handling.
    """

    MEM_MACHINE_STATUS = b"\x04\x00"
    MEM_CURRENT_X = b"\x04\x21"
    MEM_CURRENT_Y = b"\x04\x31"
    MEM_CURRENT_Z = b"\x04\x41"

    STATUS_BIT_MOVING = 0x01000000
    STATUS_BIT_PART_END = 0x00000002
    STATUS_BIT_JOB_RUNNING = 0x00000001

    # Consider "busy" only when moving or actively running; PART_END means finished.
    BUSY_MASK = STATUS_BIT_MOVING | STATUS_BIT_JOB_RUNNING

    class MachineState(NamedTuple):
        status_bits: int
        x_mm: Optional[float]
        y_mm: Optional[float]
        z_mm: Optional[float] = None

    def __init__(
        self,
        host: str,
        port: int = 50200,
        *,
        source_port: int = 40200,
        timeout_s: float = 3.0,
        dry_run: bool = False,
        magic: int = 0x88,
        movement_only: bool = False,
        save_rd_dir: Path | None = None,
        air_assist: bool = True,
        inline_fan_on: bool = False,
        pre_cut_warmup_s: float = 0.0,
        z_positive_moves_bed_up: bool = True,
        z_speed_mm_s: float = 5.0,
        socket_factory=None,
        min_stable_s: float = 0.0,
    ) -> None:
        """
        Create a Ruida UDP transport wrapper.

        Args:
            host: Controller hostname or IP.
            port: UDP port for RD uploads (default 50200).
            source_port: Local UDP source port to bind.
            timeout_s: Socket timeout for ACK/reply waits.
            dry_run: If True, log packets without sending.
            magic: Swizzle magic key (0x88 for 644xG).
            movement_only: Suppress power in generated jobs.
            save_rd_dir: Optional path to persist swizzled RD jobs.
            air_assist: Whether to enable air assist in RD jobs.
            inline_fan_on: Whether to enable the inline fan (Ruida BLOW output).
            pre_cut_warmup_s: Seconds to run air assist/inline fan before the first cut.
            z_positive_moves_bed_up: Interpret Z+ as bed-up (default).
            z_speed_mm_s: Speed to use for Z moves emitted in RD jobs.
            socket_factory: Optional socket factory for tests.
            min_stable_s: Minimum idle time before declaring ready.
        """
        self.host = host
        self.port = port
        self.source_port = source_port
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        self.magic = magic
        self.movement_only = movement_only
        socket_factory = socket_factory or __import__("socket").socket
        self._udp = RuidaUDPClient(
            host,
            port=port,
            source_port=source_port,
            timeout_s=timeout_s,
            magic=magic,
            dry_run=dry_run,
            socket_factory=socket_factory,
        )
        self._udp.MTU = 1470
        self.x = 0.0
        self.y = 0.0
        self.z = 0.0
        self._z_origin_mm: Optional[float] = None
        self.power = 0.0
        self._last_speed_ums: Optional[int] = None
        self._movement_only_power_sent = False
        self.save_rd_dir = Path(save_rd_dir) if save_rd_dir else None
        self._rd_job_counter = 0
        self.air_assist = air_assist
        self.inline_fan_on = inline_fan_on
        self.pre_cut_warmup_s = pre_cut_warmup_s
        self.z_positive_moves_bed_up = z_positive_moves_bed_up
        self.z_speed_mm_s = z_speed_mm_s
        self.min_stable_s = min_stable_s
        log.info(
            "RuidaLaser initialized for UDP host=%s port=%d dry_run=%s movement_only=%s",
            host,
            port,
            dry_run,
            movement_only,
        )

    def cleanup(self) -> None:
        """Release UDP socket if open."""
        if getattr(self._udp, "sock", None) is not None:
            try:
                self._udp.sock.close()
            except Exception:
                log.debug("Failed to close Ruida socket", exc_info=True)
            self._udp.sock = None
