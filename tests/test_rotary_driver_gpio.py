import types

from laserdove.hardware.rotary import GPIOStepperDriver


class FakeGPIO:
    BCM = "BCM"
    OUT = "OUT"
    LOW = 0
    HIGH = 1

    def __init__(self):
        self.mode = None
        self.setup_calls = []
        self.outputs = []

    def setmode(self, mode):
        self.mode = mode

    def setup(self, pin, mode, initial=None):
        self.setup_calls.append((pin, mode, initial))

    def output(self, pin, value):
        self.outputs.append((pin, value))


def test_gpio_stepper_driver(monkeypatch):
    fake_gpio = FakeGPIO()
    # Mock RPi and RPi.GPIO modules
    rpi_module = types.ModuleType("RPi")
    gpio_module = fake_gpio
    monkeypatch.setitem(__import__("sys").modules, "RPi", rpi_module)
    monkeypatch.setitem(__import__("sys").modules, "RPi.GPIO", gpio_module)
    # speed sleeps; patch time.sleep to no-op
    monkeypatch.setattr("laserdove.hardware.rotary.time.sleep", lambda _: None)
    driver = GPIOStepperDriver(
        step_pin=1,
        dir_pin=2,
        enable_pin=3,
        alarm_pin=None,
        step_high_s=0.0,
        step_low_s=0.0,
        invert_dir=False,
    )
    driver.move_steps(2, step_rate_hz=1000.0)
    assert fake_gpio.outputs  # pulses recorded
