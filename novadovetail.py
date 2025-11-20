# novadovetail.py
from __future__ import annotations

from typing import List

from config import build_arg_parser, load_config_and_args
from geometry import compute_tail_layout
from planner import plan_tail_board, compute_pin_plan, plan_pin_board
from hardware import DummyLaser, DummyRotary, execute_commands
from logging_utils import setup_logging


def main() -> None:
    parser = build_arg_parser()
    args = parser.parse_args()

    setup_logging(args.log_level)

    joint, jig, machine, mode, dry_run = load_config_and_args(args)

    tail_layout = compute_tail_layout(joint)

    all_cmds: List = []

    if mode in ("tails", "both"):
        tail_cmds = plan_tail_board(joint, machine, tail_layout)
        all_cmds.extend(tail_cmds)

    if mode in ("pins", "both"):
        pin_plan = compute_pin_plan(joint, jig, tail_layout)
        pin_cmds = plan_pin_board(joint, jig, machine, pin_plan)
        all_cmds.extend(pin_cmds)

    if dry_run:
        for c in all_cmds:
            print(c)
        return

    # v1: always use dummy interfaces; swap in RuidaLaser/RealRotary later
    laser = DummyLaser()
    rotary = DummyRotary()
    execute_commands(all_cmds, laser, rotary)


if __name__ == "__main__":
    main()
