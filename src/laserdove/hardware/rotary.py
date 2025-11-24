# hardware/rotary.py
from __future__ import annotations

import logging
import time
from typing import Optional

from .base import RotaryInterface

log = logging.getLogger(__name__)


class LoggingStepperDriver:
    """No-op driver that just logs step intents."""

    def move_steps(self, steps: int, step_rate_hz: float) -> None:
        log.info("[ROTARY DRV] steps=%d rate=%.1fHz", steps, step_rate_hz)


class GPIOStepperDriver:
    """
    Simple DIR/STEP pulse driver for external stepper drives (e.g., CL57T).

    Not initialized by default; only construct when running on hardware with
    appropriate GPIO library available.
    """

    def __init__(
        self,
        step_pin: int,
        dir_pin: int,
        enable_pin: Optional[int] = None,
        alarm_pin: Optional[int] = None,
        step_high_s: float = 5e-6,
        step_low_s: float = 5e-6,
        invert_dir: bool = False,
    ) -> None:
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except Exception as e:  # pragma: no cover - hardware only
            raise RuntimeError("RPi.GPIO required for GPIOStepperDriver") from e
        self.GPIO = GPIO
        self.step_pin = step_pin
        self.dir_pin = dir_pin
        self.enable_pin = enable_pin
        self.alarm_pin = alarm_pin
        self.step_high_s = step_high_s
        self.step_low_s = step_low_s
        self.invert_dir = invert_dir
        self.busy = False

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(step_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(dir_pin, GPIO.OUT, initial=GPIO.LOW)
        if enable_pin is not None:
            GPIO.setup(enable_pin, GPIO.OUT, initial=GPIO.LOW)
        if alarm_pin is not None:
            GPIO.setup(alarm_pin, GPIO.IN)

    def move_steps(self, steps: int, step_rate_hz: float) -> None:
        GPIO = self.GPIO  # local alias
        if steps == 0:
            return
        if step_rate_hz <= 0:
            step_rate_hz = 200.0  # fallback default
        delay = max(self.step_high_s + self.step_low_s, 1.0 / step_rate_hz)
        direction = GPIO.HIGH if (steps > 0) ^ self.invert_dir else GPIO.LOW
        GPIO.output(self.dir_pin, direction)
        if self.enable_pin is not None:
            GPIO.output(self.enable_pin, GPIO.LOW)  # active enable low on many drivers

        def alarm_active() -> bool:
            if self.alarm_pin is None:
                return False
            try:
                return bool(GPIO.input(self.alarm_pin))
            except Exception:
                return False

        if alarm_active():
            raise RuntimeError("Rotary driver alarm active before move")

        self.busy = True
        for _ in range(abs(steps)):
            GPIO.output(self.step_pin, GPIO.HIGH)
            time.sleep(self.step_high_s)
            GPIO.output(self.step_pin, GPIO.LOW)
            time.sleep(max(0.0, delay - self.step_high_s))
        # leave enable as-is to allow holding torque
        self.busy = False
        if alarm_active():
            raise RuntimeError("Rotary driver alarm active after move")


class RealRotary(RotaryInterface):
    """
    Skeleton implementation for the physical rotary on the Pi.

    v1: Logs requested angles. If a driver is provided, emit DIR/STEP pulses.
    CL57T + 23HS45 defaults: 200 steps/rev; microstep set by driver DIP.
    """

    def __init__(
        self,
        steps_per_rev: float | None = 200.0,
        microsteps: int | None = None,
        driver: Optional[object] = None,
    ) -> None:
        self.angle = 0.0
        self.steps_per_rev = steps_per_rev
        self.microsteps = microsteps
        self.driver = driver or LoggingStepperDriver()
        log.info(
            "RealRotary initialized (steps_per_rev=%s microsteps=%s)",
            steps_per_rev,
            microsteps,
        )

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        prev_angle = self.angle
        delta = angle_deg - prev_angle
        log.info("[ROTARY] rotate_to θ=%.3f° (Δ=%.3f°) at %.1f dps", angle_deg, delta, speed_dps)
        if self.steps_per_rev:
            micro = self.microsteps or 1
            steps = int(round((delta / 360.0) * self.steps_per_rev * micro))
            duration_s = abs(delta) / speed_dps if speed_dps > 0 else 0.0
            step_rate_hz = (abs(steps) / duration_s) if duration_s > 0 else 0.0
            log.debug("Computed step target: %d steps (microsteps=%s, rate=%.1f Hz)", steps, micro, step_rate_hz)
            if hasattr(self.driver, "move_steps"):
                try:
                    self.driver.move_steps(steps, step_rate_hz)
                except Exception as e:
                    log.warning("Rotary driver move failed: %s", e)
        self.angle = angle_deg
        time.sleep(0.0)
