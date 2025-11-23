from laserdove.hardware.ruida import RuidaLaser


def test_ruida_swizzle_magic_override():
    # Known swizzle for byte 0x00 with magic 0x88 vs 0x11 should differ.
    default_swizzled = RuidaLaser._swizzle_byte(0x00, magic=0x88)
    alt_swizzled = RuidaLaser._swizzle_byte(0x00, magic=0x11)
    assert default_swizzled != alt_swizzled


def test_ruida_dry_run_sends_without_socket(monkeypatch):
    laser = RuidaLaser(host="127.0.0.1", port=50200, dry_run=True)
    # Exercise power/speed/move/cut paths; should not raise when dry_run.
    laser.set_laser_power(25.0)
    laser.move(x=1.0, y=2.0, speed=100.0)
    laser.cut_line(x=3.0, y=4.0, speed=50.0)
    # Re-setting same power should be a no-op
    laser.set_laser_power(25.0)
    # Speed cache should avoid re-send
    laser.move(x=5.0, y=6.0, speed=100.0)
