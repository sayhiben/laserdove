from laserdove.hardware import ruida_laser
from laserdove.hardware.ruida_laser import RuidaLaser


def test_apply_job_z_uses_hardware_state(monkeypatch):
    """
    Ensure Z moves are based on polled hardware position rather than the cached tracker.
    """
    laser = RuidaLaser("localhost", dry_run=True, panel_z_step_mm=1.0)
    laser.z = 2.0  # stale cached value; real hardware will report 0

    addresses = []

    def fake_get_memory_value(address, *, expected_len):
        addresses.append(address)
        if address == laser.MEM_MACHINE_STATUS:
            return b"\x00\x00\x00\x00"
        if address == laser.MEM_CURRENT_Z:
            return laser._encode_abscoord_mm(0.0)
        return None

    monkeypatch.setattr(laser, "_get_memory_value", fake_get_memory_value)
    monkeypatch.setattr(ruida_laser.time, "sleep", lambda *_: None)

    sent_commands = []

    class StubPanel:
        def send_command(self, cmd):
            sent_commands.append(cmd)

    laser._panel_iface = StubPanel()

    laser._apply_job_z(2.0)

    assert laser.MEM_CURRENT_Z in addresses
    assert sent_commands  # command(s) were issued because hardware Z differed
    # After calibration we should have updated z to the polled value and stopped.
    assert laser.z == 0.0
