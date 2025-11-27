# cli entrypoint
from __future__ import annotations

import inspect
import logging
from typing import List

from .config import build_arg_parser, load_config_and_args
from .geometry import compute_tail_layout
from .planner import plan_tail_board, compute_pin_plan, plan_pin_board
from .model import Command, CommandType
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

    run_config = load_config_and_args(args)
    if run_config.reset_only:
        run_config.movement_only = True

    # Early exit: reset-only mode just zeros rotary/head with laser off.
    if run_config.reset_only:
        all_commands: List[Command] = [
            Command(
                type=CommandType.SET_LASER_POWER,
                power_pct=0.0,
                comment="Reset: ensure laser off",
            ),
            Command(
                type=CommandType.ROTATE,
                angle_deg=run_config.jig_params.rotation_zero_deg,
                speed_mm_s=run_config.jig_params.rotation_speed_dps,
                comment="Reset: rotate jig to zero",
            ),
            Command(
                type=CommandType.MOVE,
                x=0.0,
                y=0.0,
                z=run_config.machine_params.z_zero_pin_mm,
                speed_mm_s=run_config.machine_params.rapid_speed_mm_s,
                comment="Reset: move head to origin at pin Z0",
            ),
        ]
    else:
        # Compute shared layout once (pins and tails must agree)
        tail_layout = compute_tail_layout(run_config.joint_params)

        # Validate geometry + machine/jig config
        validation_errors = validate_all(run_config.joint_params, run_config.jig_params, run_config.machine_params, tail_layout)
        if validation_errors:
            for error in validation_errors:
                print(f"ERROR: {error}")
            raise SystemExit("Validation failed; fix configuration before running.")

        all_commands: List[Command] = []

        if run_config.mode in ("tails", "both"):
            tail_commands = plan_tail_board(run_config.joint_params, run_config.machine_params, tail_layout)
            all_commands.extend(tail_commands)

        if run_config.mode in ("pins", "both"):
            pin_plan = compute_pin_plan(run_config.joint_params, run_config.jig_params, tail_layout)
            pin_commands = plan_pin_board(run_config.joint_params, run_config.jig_params, run_config.machine_params, pin_plan)
            all_commands.extend(pin_commands)

    if run_config.dry_run and not run_config.simulate and run_config.laser_backend != "ruida":
        for command in all_commands:
            print(command)
        return

    # Backend selection
    if run_config.simulate:
        from .hardware import SimulatedLaser, SimulatedRotary  # local import to keep tk optional

        sim_kwargs = {"real_time": True}
        sim_opts = {
            "origin_x": 0.0,
            "origin_y": 0.0,
            "edge_length_mm": run_config.joint_params.edge_length_mm,
            "movement_only": run_config.movement_only or run_config.reset_only,
            "z_positive_moves_bed_up": run_config.machine_params.z_positive_moves_bed_up,
            "air_assist": run_config.machine_params.air_assist,
        }
        for name, val in sim_opts.items():
            if name in inspect.signature(SimulatedLaser).parameters:
                sim_kwargs[name] = val

        laser = SimulatedLaser(**sim_kwargs)
        rotary = SimulatedRotary(laser, real_time=True)
        laser.setup_viewer()  # Open the window before execution to show progress.
    else:
        if run_config.laser_backend == "dummy":
            laser = DummyLaser()
        elif run_config.laser_backend == "ruida":
            ruida_dry_run = run_config.dry_run or run_config.dry_run_rd
            laser = RuidaLaser(
                host=run_config.backend_host,
                port=run_config.backend_port,
                magic=run_config.ruida_magic,
                timeout_s=run_config.ruida_timeout_s,
                source_port=run_config.ruida_source_port,
                dry_run=ruida_dry_run,
                movement_only=run_config.movement_only,
                save_rd_dir=run_config.save_rd_dir,
                air_assist=run_config.machine_params.air_assist,
                z_positive_moves_bed_up=run_config.machine_params.z_positive_moves_bed_up,
                z_speed_mm_s=run_config.machine_params.z_speed_mm_s,
            )
        else:
            raise ValueError(f"Unsupported laser backend {run_config.laser_backend}")

        if run_config.rotary_backend == "dummy":
            rotary = DummyRotary()
        elif run_config.rotary_backend == "real":
            driver = LoggingStepperDriver()
            if any(pin is not None for pin in (run_config.rotary_step_pin, run_config.rotary_step_pin_pos)) and any(pin is not None for pin in (run_config.rotary_dir_pin, run_config.rotary_dir_pin_pos)):
                try:
                    driver = GPIOStepperDriver(
                        step_pin=run_config.rotary_step_pin,
                        dir_pin=run_config.rotary_dir_pin,
                        step_pin_pos=run_config.rotary_step_pin_pos,
                        dir_pin_pos=run_config.rotary_dir_pin_pos,
                        enable_pin=run_config.rotary_enable_pin,
                        alarm_pin=run_config.rotary_alarm_pin,
                        invert_dir=run_config.rotary_invert_dir,
                        pin_mode=run_config.rotary_pin_numbering.upper(),
                    )
                except Exception as e:
                    log.warning("Failed to initialize GPIO rotary driver; using logging driver instead: %s", e)
            else:
                log.warning("Rotary backend 'real' selected but step/dir pins not configured; using logging driver.")

            rotary = RealRotary(
                steps_per_rev=run_config.rotary_steps_per_rev,
                microsteps=run_config.rotary_microsteps,
                driver=driver,
                max_step_rate_hz=run_config.rotary_max_step_rate_hz,
            )
        else:
            raise ValueError(f"Unsupported rotary backend {run_config.rotary_backend}")

    # If not simulating, ensure we start from known rotation zero.
    if not run_config.simulate and not run_config.reset_only:
        all_commands.insert(0, Command(
            type=CommandType.ROTATE,
            angle_deg=run_config.jig_params.rotation_zero_deg,
            speed_mm_s=run_config.jig_params.rotation_speed_dps,
            comment="Prep: rotate jig to zero",
        ))

    try:
        if isinstance(laser, RuidaLaser):
            run_kwargs = {}
            sig = inspect.signature(laser.run_sequence_with_rotary)
            if "travel_only" in sig.parameters:
                run_kwargs["travel_only"] = run_config.movement_only or run_config.reset_only
            if "edge_length_mm" in sig.parameters:
                run_kwargs["edge_length_mm"] = run_config.joint_params.edge_length_mm
            laser.run_sequence_with_rotary(all_commands, rotary, **run_kwargs)
        else:
            execute_commands(all_commands, laser, rotary)

        if run_config.simulate and hasattr(laser, "show"):
            laser.show()
    finally:
        for dev in (laser, rotary):
            if hasattr(dev, "cleanup"):
                try:
                    dev.cleanup()  # type: ignore[attr-defined]
                except Exception:
                    log.debug("Cleanup failed", exc_info=True)


if __name__ == "__main__":
    main()
