from laserdove.hardware.rotary import RealRotary


class CaptureDriver:
    def __init__(self):
        self.calls = []

    def move_steps(self, steps: int, step_rate_hz: float) -> None:
        self.calls.append((steps, step_rate_hz))


def test_real_rotary_computes_steps_and_calls_driver():
    driver = CaptureDriver()
    rotary = RealRotary(steps_per_rev=200.0, microsteps=8, driver=driver)
    rotary.rotate_to(90.0, speed_dps=180.0)  # quarter turn
    assert driver.calls, "Driver should be invoked"
    steps, rate = driver.calls[0]
    assert steps == 400  # 200 * 0.25 * microsteps(8) = 400
    assert rate > 0


def test_real_rotary_skips_when_no_steps_per_rev():
    driver = CaptureDriver()
    rotary = RealRotary(steps_per_rev=None, driver=driver)
    rotary.rotate_to(45.0, speed_dps=90.0)
    assert driver.calls == []
