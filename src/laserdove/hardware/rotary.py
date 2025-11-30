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
        """
        Log a step request without hardware output.

        Args:
            steps: Number of steps to move (sign indicates direction).
            step_rate_hz: Step pulse rate in Hz.
        """
        log.info("[ROTARY DRV] steps=%d rate=%.1fHz", steps, step_rate_hz)


class GPIOStepperDriver:
    """
    Simple DIR/STEP pulse driver for external stepper drives (e.g., CL57T).

    Not initialized by default; only construct when running on hardware with
    appropriate GPIO library available.
    """

    def __init__(
        self,
        step_pin: Optional[int] = None,
        dir_pin: Optional[int] = None,
        step_pin_pos: Optional[int] = None,
        dir_pin_pos: Optional[int] = None,
        enable_pin: Optional[int] = None,
        alarm_pin: Optional[int] = None,
        step_high_s: float = 5e-6,
        step_low_s: float = 5e-6,
        invert_dir: bool = False,
        pin_mode: str = "BOARD",
    ) -> None:
        """
        Initialize GPIO pins for a STEP/DIR external driver.

        Args:
            step_pin: Negative/active step pin (if using differential pairs).
            dir_pin: Negative/active direction pin.
            step_pin_pos: Positive step pin (held static if step_pin used).
            dir_pin_pos: Positive direction pin (held static if dir_pin used).
            enable_pin: Optional enable pin (active low on many drivers).
            alarm_pin: Optional alarm input pin.
            step_high_s: Duration to hold step high.
            step_low_s: Duration to hold step low.
            invert_dir: Invert direction polarity.
            pin_mode: 'BCM' or 'BOARD' numbering.
        """
        try:
            import RPi.GPIO as GPIO  # type: ignore
        except Exception as e:  # pragma: no cover - hardware only
            raise RuntimeError("RPi.GPIO required for GPIOStepperDriver") from e
        self.GPIO = GPIO
        GPIO.setwarnings(False)  # avoid noisy reuse warnings; we'll clean up pins we touch
        if step_pin is None and step_pin_pos is None:
            raise ValueError("Provide at least one of step_pin or step_pin_pos")
        if dir_pin is None and dir_pin_pos is None:
            raise ValueError("Provide at least one of dir_pin or dir_pin_pos")

        # Active pins: whichever side we toggle. Static pins are held to bias the optos.
        self.step_pulse_pin = step_pin if step_pin is not None else step_pin_pos
        self.step_pulse_is_pos = step_pin is None
        self.step_static_pin = None if step_pin is None or step_pin_pos is None else (step_pin_pos if step_pin is not None else step_pin)
        self.step_static_level = GPIO.HIGH if not self.step_pulse_is_pos else GPIO.LOW

        self.dir_active_pin = dir_pin if dir_pin is not None else dir_pin_pos
        self.dir_active_is_pos = dir_pin is None
        self.dir_static_pin = None if dir_pin is None or dir_pin_pos is None else (dir_pin_pos if dir_pin is not None else dir_pin)
        self.dir_static_level = GPIO.HIGH if not self.dir_active_is_pos else GPIO.LOW

        self.enable_pin = enable_pin
        self.alarm_pin = alarm_pin
        self.step_high_s = step_high_s
        self.step_low_s = step_low_s
        self.invert_dir = invert_dir
        self.busy = False

        mode = GPIO.BCM if pin_mode.upper() == "BCM" else GPIO.BOARD
        GPIO.setmode(mode)
        GPIO.setup(self.step_pulse_pin, GPIO.OUT, initial=GPIO.LOW)
        if self.step_static_pin is not None:
            GPIO.setup(self.step_static_pin, GPIO.OUT, initial=self.step_static_level)
        GPIO.setup(self.dir_active_pin, GPIO.OUT, initial=GPIO.LOW)
        if self.dir_static_pin is not None:
            GPIO.setup(self.dir_static_pin, GPIO.OUT, initial=self.dir_static_level)
        if enable_pin is not None:
            GPIO.setup(enable_pin, GPIO.OUT, initial=GPIO.LOW)
        if alarm_pin is not None:
            GPIO.setup(alarm_pin, GPIO.IN)

    def move_steps(self, steps: int, step_rate_hz: float) -> None:
        """
        Emit step pulses at the requested rate.

        Args:
            steps: Number of steps to move (sign sets direction).
            step_rate_hz: Pulse rate in Hz; capped and defaulted if <= 0.

        Raises:
            RuntimeError: If the driver alarm input is active.
        """
        GPIO = self.GPIO  # local alias
        if steps == 0:
            return
        if step_rate_hz <= 0:
            step_rate_hz = 200.0  # fallback default
        delay = max(self.step_high_s + self.step_low_s, 1.0 / step_rate_hz)
        direction = GPIO.HIGH if (steps > 0) ^ self.invert_dir else GPIO.LOW
        GPIO.output(self.dir_active_pin, direction)
        if self.dir_static_pin is not None:
            GPIO.output(self.dir_static_pin, self.dir_static_level)
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
            GPIO.output(self.step_pulse_pin, GPIO.HIGH)
            time.sleep(self.step_high_s)
            GPIO.output(self.step_pulse_pin, GPIO.LOW)
            time.sleep(max(0.0, delay - self.step_high_s))
        # leave enable as-is to allow holding torque
        self.busy = False
        if alarm_active():
            raise RuntimeError("Rotary driver alarm active after move")

    def cleanup(self) -> None:
        """Release GPIO pins touched by this driver."""
        try:
            pins = [
                p for p in (
                    self.step_pulse_pin,
                    self.step_static_pin,
                    self.dir_active_pin,
                    self.dir_static_pin,
                    self.enable_pin,
                    self.alarm_pin,
                ) if p is not None
            ]
            if pins:
                self.GPIO.cleanup(pins)
        except Exception:
            pass


class RealRotary(RotaryInterface):
    """
    Skeleton implementation for the physical rotary on the Pi.

    v1: Logs requested angles. If a driver is provided, emit DIR/STEP pulses.
    CL57T + 23HS45 defaults: 200 steps/rev; microstep set by driver DIP.
    """

    def __init__(
        self,
        steps_per_rev: float | None = 4000.0,
        microsteps: int | None = None,
        driver: Optional[object] = None,
        max_step_rate_hz: float | None = 500.0,
    ) -> None:
        """
        Initialize the real rotary axis controller.

        Args:
            steps_per_rev: Full steps per revolution (pre-microstep).
            microsteps: Microstep setting of the driver.
            driver: Stepper driver object with move_steps.
            max_step_rate_hz: Optional cap on step rate.
        """
        self.angle = 0.0
        self.steps_per_rev = steps_per_rev
        self.microsteps = microsteps
        self.driver = driver or LoggingStepperDriver()
        self.max_step_rate_hz = max_step_rate_hz
        log.info(
            "RealRotary initialized (steps_per_rev=%s microsteps=%s max_step_rate_hz=%s)",
            steps_per_rev,
            microsteps,
            max_step_rate_hz,
        )

    def rotate_to(self, angle_deg: float, speed_dps: float) -> None:
        """
        Rotate to an absolute angle, issuing step pulses when possible.

        Args:
            angle_deg: Target angle in degrees.
            speed_dps: Desired rotation speed in degrees/sec.
        """
        prev_angle = self.angle
        delta = angle_deg - prev_angle
        log.info("[ROTARY] rotate_to θ=%.3f° (Δ=%.3f°) at %.1f dps", angle_deg, delta, speed_dps)
        if self.steps_per_rev:
            micro = self.microsteps or 1
            steps = int(round((delta / 360.0) * self.steps_per_rev * micro))
            duration_s = abs(delta) / speed_dps if speed_dps > 0 else 0.0
            step_rate_hz = (abs(steps) / duration_s) if duration_s > 0 else 0.0
            if self.max_step_rate_hz and step_rate_hz > self.max_step_rate_hz:
                log.info(
                    "[ROTARY] capping step rate from %.1f Hz to %.1f Hz",
                    step_rate_hz,
                    self.max_step_rate_hz,
                )
                step_rate_hz = self.max_step_rate_hz
            log.debug("Computed step target: %d steps (microsteps=%s, rate=%.1f Hz)", steps, micro, step_rate_hz)
            if hasattr(self.driver, "move_steps"):
                try:
                    self.driver.move_steps(steps, step_rate_hz)
                except Exception as e:
                    log.warning("Rotary driver move failed: %s", e)
        self.angle = angle_deg
        time.sleep(0.0)
