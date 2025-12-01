from __future__ import annotations

import types

from laserdove.hardware.ruida_common import encode_abscoord_mm_signed
from laserdove.hardware.ruida_laser import RuidaLaser


def test_encode_abscoord_mm_signed_positive():
    assert encode_abscoord_mm_signed(0.0) == bytes.fromhex("00 00 00 00 00")
    # 2.0 mm -> 2000 um -> 0x7d0 -> base-128 packed: 00 00 00 0f 50
    assert encode_abscoord_mm_signed(2.0) == bytes.fromhex("00 00 00 0f 50")
    # 0.5 mm -> 500 um -> 0x1f4 -> base-128 packed: 00 00 00 03 74
    assert encode_abscoord_mm_signed(0.5) == bytes.fromhex("00 00 00 03 74")


def test_encode_abscoord_mm_signed_negative_twos_complement():
    # -2.0 mm -> two's complement of 2000 um over 32 bits -> base-128 packed: 0f 7f 7f 70 30
    assert encode_abscoord_mm_signed(-2.0) == bytes.fromhex("0f 7f 7f 70 30")
    # -0.5 mm -> two's complement of 500 um -> base-128 packed: 0f 7f 7f 7c 0c
    assert encode_abscoord_mm_signed(-0.5) == bytes.fromhex("0f 7f 7f 7c 0c")


def test_ruida_move_emits_signed_offset_only():
    sent_packets = []

    def fake_send(pkt, *_, **__):
        sent_packets.append(pkt)
        return None

    laser = RuidaLaser("dummy-host", dry_run=False)
    laser._udp.send_packets = fake_send  # type: ignore[assignment]
    laser._wait_for_ready = types.MethodType(lambda self: None, laser)  # type: ignore[attr-defined]
    # Start at z=10.0 logical
    laser.z = 10.0

    # Move Z down to 8.0 (relative -2.0): expect 0x80 0x03 + signed delta (-2.0 mm)
    laser.move(z=8.0)
    assert laser.z == 8.0
    assert sent_packets[0].startswith(b"\x80\x03")
    assert sent_packets[0][2:] == encode_abscoord_mm_signed(-2.0)

    # XY-only move should not emit another 0x80 0x03 (but will emit an XY move packet)
    sent_packets.clear()
    laser.move(x=1.0, y=2.0)
    assert sent_packets
    assert not any(pkt.startswith(b"\x80\x03") for pkt in sent_packets)

    # Small delta below tolerance should skip Z packet
    sent_packets.clear()
    laser.move(z=8.0000001)
    assert not sent_packets
