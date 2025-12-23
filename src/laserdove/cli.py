# cli entrypoint
from __future__ import annotations

import logging
from typing import List, Tuple

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


def _build_reset_commands(run_config) -> List[Command]:
    """Build reset-only command sequence with laser off and parked axes."""
    return [
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


def plan_commands(run_config) -> List[Command]:
    """Generate motion commands for the selected mode."""
    if run_config.reset_only:
        return _build_reset_commands(run_config)

    tail_layout = compute_tail_layout(run_config.joint_params)
    validation_errors = validate_all(
        run_config.joint_params, run_config.jig_params, run_config.machine_params, tail_layout
    )
    if validation_errors:
        for error in validation_errors:
            print(f"ERROR: {error}")
        raise SystemExit("Validation failed; fix configuration before running.")

    commands: List[Command] = []

    if run_config.mode in ("tails", "both"):
        commands.extend(
            plan_tail_board(run_config.joint_params, run_config.machine_params, tail_layout)
        )

    if run_config.mode in ("pins", "both"):
        pin_plan = compute_pin_plan(run_config.joint_params, run_config.jig_params, tail_layout)
        commands.extend(
            plan_pin_board(
                run_config.joint_params, run_config.jig_params, run_config.machine_params, pin_plan
            )
        )

    return commands


def _build_real_backends(run_config) -> Tuple[object, object]:
    if run_config.backend.laser_backend == "dummy":
        laser = DummyLaser()
    elif run_config.backend.laser_backend == "ruida":
        ruida_dry_run = run_config.dry_run or run_config.backend.dry_run_rd
        laser = RuidaLaser(
            host=run_config.backend.ruida_host,
            port=run_config.backend.ruida_port,
            magic=run_config.backend.ruida_magic,
            timeout_s=run_config.backend.ruida_timeout_s,
            source_port=run_config.backend.ruida_source_port,
            dry_run=ruida_dry_run,
            movement_only=run_config.backend.movement_only,
            save_rd_dir=run_config.backend.save_rd_dir,
            air_assist=run_config.machine_params.air_assist,
            inline_fan_on=run_config.machine_params.inline_fan_on,
            pre_cut_warmup_s=run_config.machine_params.pre_cut_warmup_s,
            z_positive_moves_bed_up=run_config.machine_params.z_positive_moves_bed_up,
            z_speed_mm_s=run_config.machine_params.z_speed_mm_s,
            min_stable_s=5.0,
        )
    else:
        raise ValueError(f"Unsupported laser backend {run_config.backend.laser_backend}")

    if run_config.backend.rotary_backend == "dummy":
        rotary = DummyRotary()
    elif run_config.backend.rotary_backend == "real":
        driver = LoggingStepperDriver()
        if any(
            pin is not None for pin in (run_config.rotary.step_pin, run_config.rotary.step_pin_pos)
        ) and any(
            pin is not None for pin in (run_config.rotary.dir_pin, run_config.rotary.dir_pin_pos)
        ):
            try:
                driver = GPIOStepperDriver(
                    step_pin=run_config.rotary.step_pin,
                    dir_pin=run_config.rotary.dir_pin,
                    step_pin_pos=run_config.rotary.step_pin_pos,
                    dir_pin_pos=run_config.rotary.dir_pin_pos,
                    enable_pin=run_config.rotary.enable_pin,
                    alarm_pin=run_config.rotary.alarm_pin,
                    invert_dir=run_config.rotary.invert_dir,
                    pin_mode=run_config.rotary.pin_numbering.upper(),
                )
            except Exception as e:
                log.warning(
                    "Failed to initialize GPIO rotary driver; using logging driver instead: %s", e
                )
        else:
            log.warning(
                "Rotary backend 'real' selected but step/dir pins not configured; using logging driver."
            )

        rotary = RealRotary(
            steps_per_rev=run_config.rotary.steps_per_rev,
            driver=driver,
            max_step_rate_hz=run_config.rotary.max_step_rate_hz,
        )
    else:
        raise ValueError(f"Unsupported rotary backend {run_config.backend.rotary_backend}")
    return laser, rotary


def _prepend_rotate_zero(commands: List[Command], run_config) -> None:
    if run_config.simulation.enabled or run_config.reset_only:
        return
    commands.insert(
        0,
        Command(
            type=CommandType.ROTATE,
            angle_deg=run_config.jig_params.rotation_zero_deg,
            speed_mm_s=run_config.jig_params.rotation_speed_dps,
            comment="Prep: rotate jig to zero",
        ),
    )


def _execute(commands: List[Command], laser, rotary, run_config) -> None:
    try:
        if isinstance(laser, RuidaLaser):
            movement_only_flag = run_config.backend.movement_only or run_config.reset_only
            laser.run_sequence_with_rotary(
                commands,
                rotary,
                movement_only=movement_only_flag,
                edge_length_mm=run_config.joint_params.edge_length_mm,
            )
        else:
            execute_commands(commands, laser, rotary)

    finally:
        for dev in (laser, rotary):
            if hasattr(dev, "cleanup"):
                try:
                    dev.cleanup()  # type: ignore[attr-defined]
                except Exception:
                    log.debug("Cleanup failed", exc_info=True)


def main() -> None:
    """Entry point for the command-line planner/executor."""
    parser = build_arg_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)

    run_config = load_config_and_args(args)
    if run_config.reset_only:
        run_config.backend.movement_only = True

    commands = plan_commands(run_config)

    if run_config.simulation.screenshots_dir is not None:
        from .pygame_simulator import run_pygame_viewer

        run_pygame_viewer(
            commands,
            run_config,
            screenshot_dir=run_config.simulation.screenshots_dir,
            screenshot_every_s=run_config.simulation.screenshots_every_s,
            rd_dir=run_config.simulation.rd_dir,
        )
        return

    if run_config.simulation.enabled:
        try:
            from .pygame_simulator import run_pygame_viewer

            run_pygame_viewer(
                commands,
                run_config,
                time_scale=6.0,
                rd_dir=run_config.simulation.rd_dir,
            )
        except Exception:  # pragma: no cover - UI best-effort path
            log.error("Pygame viewer failed", exc_info=True)
        return

    if run_config.dry_run and run_config.backend.laser_backend != "ruida":
        for command in commands:
            print(command)
        return

    laser, rotary = _build_real_backends(run_config)
    _prepend_rotate_zero(commands, run_config)
    _execute(commands, laser, rotary, run_config)


if __name__ == "__main__":
    main()
