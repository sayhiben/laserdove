# novadovetail.py
from __future__ import annotations

from typing import List

from config import build_arg_parser, load_config_and_args
from geometry import compute_tail_layout
from planner import plan_tail_board, compute_pin_plan, plan_pin_board
from hardware import DummyLaser, DummyRotary, RuidaLaser, RealRotary, execute_commands
from logging_utils import setup_logging
from validation import validate_all


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
        backend_use_dummy,
        backend_host,
        backend_port,
        simulate,
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
        from hardware import SimulatedLaser, SimulatedRotary  # local import to keep tk optional

        laser = SimulatedLaser(real_time=True)
        rotary = SimulatedRotary(laser, real_time=True)
        laser.setup_viewer()  # Open the window before execution to show progress.
    elif backend_use_dummy:
        laser = DummyLaser()
        rotary = DummyRotary()
    else:
        laser = RuidaLaser(host=backend_host, port=backend_port)
        rotary = RealRotary()

    execute_commands(all_commands, laser, rotary)

    if simulate and hasattr(laser, "show"):
        laser.show()


if __name__ == "__main__":
    main()
