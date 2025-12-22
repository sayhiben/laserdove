# tests/test_planner_rotation.py
import math

from laserdove.geometry import compute_tail_layout
from laserdove.model import CommandType, JointParams, JigParams, MachineParams, Side
from laserdove.planner import compute_pin_plan, plan_pin_board


def _make_joint() -> JointParams:
    # Use zero kerf/clearance to isolate rotary projection math.
    return JointParams(
        thickness_mm=6.35,
        edge_length_mm=100.0,
        dovetail_angle_deg=20.0,
        num_tails=1,
        tail_outer_width_mm=20.0,
        tail_depth_mm=6.35,
        socket_depth_mm=6.6,
        clearance_mm=0.0,
        kerf_tail_mm=0.0,
        kerf_pin_mm=0.0,
    )


def _make_machine(*, z_positive_moves_bed_up: bool = True) -> MachineParams:
    return MachineParams(
        cut_speed_tail_mm_s=10.0,
        cut_speed_pin_mm_s=8.0,
        rapid_speed_mm_s=200.0,
        z_speed_mm_s=5.0,
        cut_power_tail_pct=60.0,
        cut_power_pin_pct=65.0,
        travel_power_pct=0.0,
        cut_overtravel_mm=0.5,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
        z_positive_moves_bed_up=z_positive_moves_bed_up,
    )


def _find_edge_move(commands, pin_index: int, side: Side) -> float:
    for cmd in commands:
        if (
            cmd.type == CommandType.MOVE
            and cmd.y is not None
            and cmd.comment.startswith(f"Move to pin {pin_index} {side.name} at edge")
        ):
            return cmd.y
    raise AssertionError(f"Edge move for pin {pin_index} {side.name} not found")


def _has_edge_move(commands, pin_index: int, side: Side) -> bool:
    target = f"Move to pin {pin_index} {side.name} at edge"
    return any(
        cmd.type == CommandType.MOVE and cmd.y is not None and cmd.comment.startswith(target)
        for cmd in commands
    )


def _expected_projected_y(
    y_board: float, jig: JigParams, rotation_deg: float, *, edge_length_mm: float
) -> float:
    delta_angle = rotation_deg - jig.rotation_zero_deg
    angle_rad = math.radians(delta_angle)
    cos_theta = math.cos(math.radians(abs(delta_angle)))
    sin_theta = math.sin(angle_rad)
    y_center = edge_length_mm / 2.0
    return y_center + (y_board - y_center) * cos_theta - jig.axis_to_origin_mm * sin_theta


def _first_pin_move_for_angle(commands, rotation_deg: float) -> tuple[int, Side]:
    saw_angle = False
    for cmd in commands:
        if (
            cmd.type == CommandType.ROTATE
            and cmd.angle_deg is not None
            and math.isclose(cmd.angle_deg, rotation_deg, abs_tol=1e-9)
        ):
            saw_angle = True
            continue
        if (
            saw_angle
            and cmd.type == CommandType.MOVE
            and cmd.comment
            and cmd.comment.startswith("Move to pin ")
        ):
            parts = cmd.comment.split()
            pin_index = int(parts[3])
            side = Side[parts[4]]
            return pin_index, side
        if saw_angle and cmd.type == CommandType.ROTATE:
            break
    raise AssertionError(f"No pin move found for rotation {rotation_deg}")


def _first_pin_z_for_angle(commands, rotation_deg: float) -> float:
    saw_angle = False
    for cmd in commands:
        if (
            cmd.type == CommandType.ROTATE
            and cmd.angle_deg is not None
            and math.isclose(cmd.angle_deg, rotation_deg, abs_tol=1e-9)
        ):
            saw_angle = True
            continue
        if saw_angle and cmd.type == CommandType.MOVE and cmd.z is not None:
            if cmd.comment.startswith("Set Z for pin"):
                return cmd.z
        if saw_angle and cmd.type == CommandType.ROTATE:
            break
    raise AssertionError(f"No pin Z move found for rotation {rotation_deg}")


def test_pin_edge_boundaries_are_skipped():
    joint = _make_joint()
    jig = JigParams(axis_to_origin_mm=30.0, rotation_zero_deg=0.0, rotation_speed_dps=30.0)
    machine = _make_machine()

    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    edge_sides = [
        (side.pin_index, side.side)
        for side in pin_plan.sides
        if math.isclose(side.y_boundary_mm, 0.0, abs_tol=1e-9)
        or math.isclose(side.y_boundary_mm, joint.edge_length_mm, abs_tol=1e-9)
    ]
    assert edge_sides, "Expected edge boundary sides for half-pins"

    for pin_index, side in edge_sides:
        assert not _has_edge_move(commands, pin_index=pin_index, side=side)

    inner_side = next(
        side
        for side in pin_plan.sides
        if 0.0 < side.y_boundary_mm < joint.edge_length_mm and side.side == Side.RIGHT
    )
    assert _has_edge_move(commands, pin_index=inner_side.pin_index, side=inner_side.side)


def test_pin_edge_projection_handles_negative_rotation():
    joint = _make_joint()
    jig = JigParams(axis_to_origin_mm=30.0, rotation_zero_deg=2.0, rotation_speed_dps=30.0)
    machine = _make_machine()

    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    # Right flank of the leading half-pin sits at Y=half_pin_width and uses the negative angle.
    right_side = min(
        (
            side
            for side in pin_plan.sides
            if side.side == Side.RIGHT
            and not math.isclose(side.y_boundary_mm, joint.edge_length_mm, abs_tol=1e-9)
        ),
        key=lambda side: side.y_boundary_mm,
    )
    expected = _expected_projected_y(
        y_board=right_side.y_boundary_mm,
        jig=jig,
        rotation_deg=right_side.rotation_deg,
        edge_length_mm=joint.edge_length_mm,
    )
    actual = _find_edge_move(commands, pin_index=right_side.pin_index, side=Side.RIGHT)
    assert math.isclose(actual, expected, abs_tol=1e-6)


def test_pin_rotation_starts_with_max_clearance_surface():
    joint = _make_joint()
    jig = JigParams(axis_to_origin_mm=30.0, rotation_zero_deg=0.0, rotation_speed_dps=30.0)
    machine = _make_machine()

    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    rotations = {
        jig.rotation_zero_deg + joint.dovetail_angle_deg,
        jig.rotation_zero_deg - joint.dovetail_angle_deg,
    }
    for rotation_deg in rotations:
        candidates = [
            side
            for side in pin_plan.sides
            if math.isclose(side.rotation_deg, rotation_deg, abs_tol=1e-9)
            and 0.0 < side.y_boundary_mm < joint.edge_length_mm
        ]
        assert candidates

        pin_index, side = _first_pin_move_for_angle(commands, rotation_deg)
        chosen = next(s for s in candidates if s.pin_index == pin_index and s.side == side)
        expected = min(s.z_offset_mm for s in candidates)
        assert math.isclose(chosen.z_offset_mm, expected, abs_tol=1e-9)


def test_pin_rotation_bed_down_inverts_z_offsets():
    joint = _make_joint()
    jig = JigParams(axis_to_origin_mm=30.0, rotation_zero_deg=0.0, rotation_speed_dps=30.0)
    machine = _make_machine(z_positive_moves_bed_up=False)

    layout = compute_tail_layout(joint)
    pin_plan = compute_pin_plan(joint, jig, layout)
    commands = plan_pin_board(joint, jig, machine, pin_plan)

    rotations = {
        jig.rotation_zero_deg + joint.dovetail_angle_deg,
        jig.rotation_zero_deg - joint.dovetail_angle_deg,
    }
    for rotation_deg in rotations:
        z_cmd = _first_pin_z_for_angle(commands, rotation_deg)
        candidates = [
            side
            for side in pin_plan.sides
            if math.isclose(side.rotation_deg, rotation_deg, abs_tol=1e-9)
            and 0.0 < side.y_boundary_mm < joint.edge_length_mm
        ]
        assert candidates

        pin_index, side = _first_pin_move_for_angle(commands, rotation_deg)
        chosen = next(s for s in candidates if s.pin_index == pin_index and s.side == side)
        expected = machine.z_zero_pin_mm - chosen.z_offset_mm
        assert math.isclose(z_cmd, expected, abs_tol=1e-9)
        assert math.isclose(
            chosen.z_offset_mm, min(s.z_offset_mm for s in candidates), abs_tol=1e-9
        )
