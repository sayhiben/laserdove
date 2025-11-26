import types

import pytest

from laserdove.hardware.rotary import GPIOStepperDriver


class FakeGPIO:
    BCM = "BCM"
    BOARD = "BOARD"
    OUT = "OUT"
    IN = "IN"
    LOW = 0
    HIGH = 1

    def __init__(self):
        self.mode = None
        self.setup_calls = []
        self.outputs = []
        self.warnings_disabled = False

    def setmode(self, mode):
        self.mode = mode

    def setwarnings(self, flag):
        self.warnings_disabled = flag

    def setup(self, pin, mode, initial=None):
        self.setup_calls.append((pin, mode, initial))

    def output(self, pin, value):
        self.outputs.append((pin, value))

    def input(self, pin):
        return bool(self.alarm_values.pop(0)) if getattr(self, "alarm_values", None) else False

    def cleanup(self, pins):
        self.cleaned = pins


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
        step_pin=None,
        dir_pin=None,
        step_pin_pos=4,
        dir_pin_pos=5,
        enable_pin=None,
        alarm_pin=None,
        step_high_s=0.0,
        step_low_s=0.0,
        invert_dir=False,
        pin_mode="BCM",
    )
    driver.move_steps(2, step_rate_hz=1000.0)
    assert fake_gpio.outputs  # pulses recorded
    assert (4, fake_gpio.OUT, fake_gpio.LOW) in fake_gpio.setup_calls  # pulse pin init low
    assert (5, fake_gpio.OUT, fake_gpio.LOW) in fake_gpio.setup_calls


def test_gpio_stepper_alarm_and_cleanup(monkeypatch):
    fake_gpio = FakeGPIO()
    fake_gpio.alarm_values = [0, 1]  # quiet before move, alarm after move
    rpi_module = types.ModuleType("RPi")
    gpio_module = fake_gpio
    monkeypatch.setitem(__import__("sys").modules, "RPi", rpi_module)
    monkeypatch.setitem(__import__("sys").modules, "RPi.GPIO", gpio_module)
    monkeypatch.setattr("laserdove.hardware.rotary.time.sleep", lambda *_: None)
    driver = GPIOStepperDriver(
        step_pin=2,
        dir_pin=3,
        enable_pin=10,
        alarm_pin=11,
        step_high_s=0.0,
        step_low_s=0.0,
        invert_dir=True,
        pin_mode="BOARD",
    )
    with pytest.raises(RuntimeError):
        driver.move_steps(1, step_rate_hz=100.0)
    driver.cleanup()
    assert fake_gpio.cleaned  # cleanup called with pins
