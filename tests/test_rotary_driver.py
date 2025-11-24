from laserdove.hardware.rotary import RealRotary


class CaptureDriver:
    def __init__(self):
        self.calls = []

    def move_steps(self, steps: int, step_rate_hz: float) -> None:
        self.calls.append((steps, step_rate_hz))


def test_real_rotary_computes_steps_and_calls_driver():
    driver = CaptureDriver()
    rotary = RealRotary(steps_per_rev=4000.0, microsteps=1, driver=driver, max_step_rate_hz=500.0)
    rotary.rotate_to(90.0, speed_dps=180.0)  # quarter turn => raw rate 2000 Hz, capped to 500
    assert driver.calls, "Driver should be invoked"
    steps, rate = driver.calls[0]
    assert steps == 1000  # 4000 pulses/rev * 0.25 turn
    assert rate == 500.0


def test_real_rotary_skips_when_no_steps_per_rev():
    driver = CaptureDriver()
    rotary = RealRotary(steps_per_rev=None, driver=driver)
    rotary.rotate_to(45.0, speed_dps=90.0)
    assert driver.calls == []
