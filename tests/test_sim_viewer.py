from laserdove.hardware.sim import SimulatedLaser


class DummyViewer:
    def __init__(self):
        self.opened = False
        self.updated = False
        self.mainloop_called = False

    def open(self):
        self.opened = True

    def render(self, segments, rotation_deg, **kwargs):
        self.updated = True

    def update(self, segments, rotation_deg, **kwargs):
        self.updated = True

    def mainloop(self, segments, rotation_deg, **kwargs):
        self.mainloop_called = True


def test_simulated_laser_setup_and_show(monkeypatch):
    dummy = DummyViewer()
    # Patch SimulationViewer class to return our dummy instance.
    monkeypatch.setattr("laserdove.hardware.sim.SimulationViewer", lambda: dummy)

    laser = SimulatedLaser()
    laser.setup_viewer()
    assert dummy.opened
    assert dummy.updated

    # Ensure show triggers mainloop when viewer exists.
    laser.show()
    assert dummy.mainloop_called
