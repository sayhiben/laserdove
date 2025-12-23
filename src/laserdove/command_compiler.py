from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from .hardware.rd_builder import RDMove
from .model import BoardSide, Command, CommandType


@dataclass
class CompileState:
    cursor_x: float
    cursor_y: float
    current_speed: float | None
    current_z: float | None
    last_set_z: float | None
    origin_speed: float | None


@dataclass
class LaserBlock:
    moves: List[RDMove]
    rotation_deg: float
    board: BoardSide
    start_z_mm: float | None
    end_state: CompileState


@dataclass
class RotationStep:
    angle_deg: float
    speed_dps: float | None


CompiledStep = LaserBlock | RotationStep


@dataclass
class CompiledPlan:
    steps: List[CompiledStep]
    origin_x: float
    origin_y: float
    origin_z: float | None
    initial_state: CompileState
    final_state: CompileState
    has_cut: bool

    def blocks(self) -> List[LaserBlock]:
        return [step for step in self.steps if isinstance(step, LaserBlock)]


def compile_command_plan(
    commands: Sequence[Command],
    *,
    origin_x: float,
    origin_y: float,
    start_z: float | None,
    edge_length_mm: float | None,
    z_speed_mm_s: float,
    movement_only: bool,
    rotation_zero_deg: float = 0.0,
) -> CompiledPlan:
    """
    Compile planner commands into ordered laser blocks and rotary steps.
    """
    y_center = (edge_length_mm / 2.0) if edge_length_mm is not None else 0.0
    cursor_x = origin_x
    cursor_y = origin_y
    current_speed: float | None = None
    current_z: float | None = start_z
    last_set_z: float | None = None
    origin_speed: float | None = None
    current_power = 0.0
    rotation = rotation_zero_deg
    board = BoardSide.TAIL
    steps: List[CompiledStep] = []
    block_moves: List[RDMove] = []
    block_start_z = current_z
    has_cut = False

    def flush_block() -> None:
        nonlocal block_moves, block_start_z
        if not block_moves:
            return
        end_state = CompileState(
            cursor_x=cursor_x,
            cursor_y=cursor_y,
            current_speed=current_speed,
            current_z=current_z,
            last_set_z=last_set_z,
            origin_speed=origin_speed,
        )
        steps.append(
            LaserBlock(
                moves=block_moves,
                rotation_deg=rotation,
                board=board,
                start_z_mm=block_start_z,
                end_state=end_state,
            )
        )
        block_moves = []
        block_start_z = current_z

    for cmd in commands:
        if cmd.type == CommandType.ROTATE:
            flush_block()
            angle = rotation if cmd.angle_deg is None else cmd.angle_deg
            rotation = angle
            board = BoardSide.PIN
            steps.append(RotationStep(angle_deg=angle, speed_dps=cmd.speed_mm_s))
            continue

        if cmd.type == CommandType.SET_LASER_POWER:
            if (cmd.power_pct or 0.0) > 0.0:
                has_cut = True
            current_power = 0.0 if movement_only else (cmd.power_pct or 0.0)
            continue

        if cmd.type == CommandType.MOVE:
            x = cursor_x if cmd.x is None else origin_x + cmd.x
            y = cursor_y if cmd.y is None else origin_y + (cmd.y - y_center)
            if cmd.z is not None:
                current_z = cmd.z
                last_set_z = current_z
                block_moves.append(
                    RDMove(
                        x_mm=x,
                        y_mm=y,
                        speed_mm_s=z_speed_mm_s,
                        power_pct=current_power,
                        is_cut=False,
                        z_mm=current_z,
                    )
                )
            if cmd.speed_mm_s is not None:
                current_speed = cmd.speed_mm_s
                if origin_speed is None:
                    origin_speed = current_speed
            if current_speed is None:
                continue
            block_moves.append(
                RDMove(
                    x_mm=x,
                    y_mm=y,
                    speed_mm_s=current_speed,
                    power_pct=current_power,
                    is_cut=False,
                )
            )
            cursor_x, cursor_y = x, y
            continue

        if cmd.type == CommandType.CUT_LINE:
            has_cut = True
            x = cursor_x if cmd.x is None else origin_x + cmd.x
            y = cursor_y if cmd.y is None else origin_y + (cmd.y - y_center)
            if cmd.z is not None:
                current_z = cmd.z
                last_set_z = current_z
                block_moves.append(
                    RDMove(
                        x_mm=x,
                        y_mm=y,
                        speed_mm_s=z_speed_mm_s,
                        power_pct=current_power,
                        is_cut=False,
                        z_mm=current_z,
                    )
                )
            if cmd.speed_mm_s is not None:
                current_speed = cmd.speed_mm_s
            if current_speed is None:
                continue
            block_moves.append(
                RDMove(
                    x_mm=x,
                    y_mm=y,
                    speed_mm_s=current_speed,
                    power_pct=current_power,
                    is_cut=not movement_only,
                )
            )
            cursor_x, cursor_y = x, y
            continue

    flush_block()

    initial_state = CompileState(
        cursor_x=origin_x,
        cursor_y=origin_y,
        current_speed=None,
        current_z=start_z,
        last_set_z=None,
        origin_speed=origin_speed,
    )
    final_state = CompileState(
        cursor_x=cursor_x,
        cursor_y=cursor_y,
        current_speed=current_speed,
        current_z=current_z,
        last_set_z=last_set_z,
        origin_speed=origin_speed,
    )
    return CompiledPlan(
        steps=steps,
        origin_x=origin_x,
        origin_y=origin_y,
        origin_z=start_z,
        initial_state=initial_state,
        final_state=final_state,
        has_cut=has_cut,
    )
