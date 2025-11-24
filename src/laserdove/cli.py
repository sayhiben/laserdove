# cli entrypoint
from __future__ import annotations

import logging
from typing import List

from .config import build_arg_parser, load_config_and_args
from .geometry import compute_tail_layout
from .planner import plan_tail_board, compute_pin_plan, plan_pin_board
from .hardware import (
    DummyLaser,
    DummyRotary,
    RuidaLaser,
    RealRotary,
    LoggingStepperDriver,
    GPIOStepperDriver,
    execute_commands,
)
from .logging_utils import setup_logging
from .validation import validate_all

log = logging.getLogger(__name__)


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)

    (
        joint_params,
        jig_params,
        machine_params,
        mode,
        dry_run,
        _backend_use_dummy,
        backend_host,
        backend_port,
        ruida_magic,
        ruida_timeout_s,
        ruida_source_port,
        rotary_steps_per_rev,
        rotary_microsteps,
        rotary_step_pin,
        rotary_dir_pin,
        rotary_step_pin_pos,
        rotary_dir_pin_pos,
        rotary_enable_pin,
        rotary_alarm_pin,
        rotary_invert_dir,
        rotary_pin_numbering,
        simulate,
        laser_backend,
        rotary_backend,
        movement_only,
    ) = load_config_and_args(args)

    # Compute shared layout once (pins and tails must agree)
    tail_layout = compute_tail_layout(joint_params)

    # Validate geometry + machine/jig config
    validation_errors = validate_all(joint_params, jig_params, machine_params, tail_layout)
    if validation_errors:
        for error in validation_errors:
            print(f"ERROR: {error}")
        raise SystemExit("Validation failed; fix configuration before running.")

    all_commands: List = []

    if mode in ("tails", "both"):
        tail_commands = plan_tail_board(joint_params, machine_params, tail_layout)
        all_commands.extend(tail_commands)

    if mode in ("pins", "both"):
        pin_plan = compute_pin_plan(joint_params, jig_params, tail_layout)
        pin_commands = plan_pin_board(joint_params, jig_params, machine_params, pin_plan)
        all_commands.extend(pin_commands)

    if dry_run and not simulate:
        for command in all_commands:
            print(command)
        return

    # Backend selection
    if simulate:
        from .hardware import SimulatedLaser, SimulatedRotary  # local import to keep tk optional

        laser = SimulatedLaser(real_time=True)
        rotary = SimulatedRotary(laser, real_time=True)
        laser.setup_viewer()  # Open the window before execution to show progress.
    else:
        if laser_backend == "dummy":
            laser = DummyLaser()
        elif laser_backend == "ruida":
            laser = RuidaLaser(
                host=backend_host,
                port=backend_port,
                magic=ruida_magic,
                timeout_s=ruida_timeout_s,
                source_port=ruida_source_port,
                movement_only=movement_only,
            )
        else:
            raise ValueError(f"Unsupported laser backend {laser_backend}")

        if rotary_backend == "dummy":
            rotary = DummyRotary()
        elif rotary_backend == "real":
            driver = LoggingStepperDriver()
            if any(pin is not None for pin in (rotary_step_pin, rotary_step_pin_pos)) and any(pin is not None for pin in (rotary_dir_pin, rotary_dir_pin_pos)):
                try:
                    driver = GPIOStepperDriver(
                        step_pin=rotary_step_pin,
                        dir_pin=rotary_dir_pin,
                        step_pin_pos=rotary_step_pin_pos,
                        dir_pin_pos=rotary_dir_pin_pos,
                        enable_pin=rotary_enable_pin,
                        alarm_pin=rotary_alarm_pin,
                        invert_dir=rotary_invert_dir,
                        pin_mode=rotary_pin_numbering.upper(),
                    )
                except Exception as e:
                    log.warning("Failed to initialize GPIO rotary driver; using logging driver instead: %s", e)
            else:
                log.warning("Rotary backend 'real' selected but step/dir pins not configured; using logging driver.")

            rotary = RealRotary(
                steps_per_rev=rotary_steps_per_rev,
                microsteps=rotary_microsteps,
                driver=driver,
            )
        else:
            raise ValueError(f"Unsupported rotary backend {rotary_backend}")

    execute_commands(all_commands, laser, rotary)

    if simulate and hasattr(laser, "show"):
        laser.show()


if __name__ == "__main__":
    main()
