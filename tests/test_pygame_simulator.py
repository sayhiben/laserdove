import math

from laserdove.hardware.rd_builder import RDMove, build_rd_job
from laserdove.model import Command, CommandType, JigParams, JointParams, MachineParams
from laserdove.pygame_simulator import (
    PygameSimulationViewer,
    build_beam_traces,
    build_beam_traces_from_rd_segments,
)
from laserdove.rd_parser import RuidaParser


def _machine_params() -> MachineParams:
    return MachineParams(
        cut_speed_tail_mm_s=10.0,
        cut_speed_pin_mm_s=10.0,
        rapid_speed_mm_s=200.0,
        z_speed_mm_s=5.0,
        cut_power_tail_pct=60.0,
        cut_power_pin_pct=65.0,
        travel_power_pct=0.0,
        cut_overtravel_mm=0.0,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
        air_assist=True,
        z_positive_moves_bed_up=True,
    )


def _joint_params(*, thickness_mm: float, edge_length_mm: float) -> JointParams:
    return JointParams(
        thickness_mm=thickness_mm,
        edge_length_mm=edge_length_mm,
        dovetail_angle_deg=10.0,
        num_tails=1,
        tail_outer_width_mm=10.0,
        tail_depth_mm=1.0,
        socket_depth_mm=1.0,
        clearance_mm=0.0,
        kerf_tail_mm=0.0,
        kerf_pin_mm=0.0,
    )


def test_build_beam_traces_vertical_beam_exit_matches_machine_y() -> None:
    thickness = 10.0
    edge_length = 100.0
    axis_to_origin = 30.0
    rotation = 15.0

    commands = [
        Command(type=CommandType.ROTATE, angle_deg=rotation, speed_mm_s=30.0),
        Command(type=CommandType.MOVE, x=0.0, y=70.0, z=0.0, speed_mm_s=100.0),
    ]

    traces = build_beam_traces(
        commands,
        joint_params=_joint_params(thickness_mm=thickness, edge_length_mm=edge_length),
        jig_params=JigParams(
            axis_to_origin_mm=axis_to_origin,
            rotation_zero_deg=0.0,
            rotation_speed_dps=30.0,
        ),
        machine_params=_machine_params(),
        movement_only=True,
        air_assist=True,
        start_board="tail",
    )

    move_trace = traces[-1]
    assert math.isclose(move_trace.start_top[1], move_trace.start_bottom[1], abs_tol=1e-9)
    assert math.isclose(move_trace.end_top[1], move_trace.end_bottom[1], abs_tol=1e-9)


def test_edge_cut_polygon_shears_by_thickness_tan() -> None:
    thickness = 10.0
    edge_length = 100.0
    y_center = edge_length / 2.0
    axis_to_origin = 30.0
    rotation = 10.0

    board_y_start = 40.0
    board_y_end = 60.0
    angle_rad = math.radians(rotation)
    y_machine_start = (
        y_center
        + (board_y_start - y_center) * math.cos(angle_rad)
        - axis_to_origin * math.sin(angle_rad)
    )
    y_machine_end = (
        y_center
        + (board_y_end - y_center) * math.cos(angle_rad)
        - axis_to_origin * math.sin(angle_rad)
    )

    commands = [
        Command(type=CommandType.ROTATE, angle_deg=rotation, speed_mm_s=30.0),
        Command(type=CommandType.MOVE, x=0.0, y=y_machine_start, z=0.0, speed_mm_s=100.0),
        Command(type=CommandType.SET_LASER_POWER, power_pct=50.0),
        Command(type=CommandType.CUT_LINE, x=0.0, y=y_machine_end, speed_mm_s=10.0),
        Command(type=CommandType.SET_LASER_POWER, power_pct=0.0),
    ]

    joint = _joint_params(thickness_mm=thickness, edge_length_mm=edge_length)
    jig = JigParams(
        axis_to_origin_mm=axis_to_origin, rotation_zero_deg=0.0, rotation_speed_dps=30.0
    )
    machine = _machine_params()

    traces = build_beam_traces(
        commands,
        joint_params=joint,
        jig_params=jig,
        machine_params=machine,
        movement_only=False,
        air_assist=True,
        start_board="tail",
    )
    viewer = PygameSimulationViewer(
        traces,
        edge_length_mm=edge_length,
        thickness_mm=thickness,
        axis_to_origin_mm=axis_to_origin,
        rotation_zero_deg=0.0,
        z_zero_tail_mm=0.0,
        z_zero_pin_mm=0.0,
    )
    polygons = viewer._edge_cut_polygons([t for t in traces if t.is_cut])
    assert len(polygons) == 1
    board, rot, poly = polygons[0]
    assert board == "pin"
    assert math.isclose(rot, rotation, abs_tol=1e-9)

    shear = thickness * math.tan(math.radians(rotation))
    y_top_min = poly[0][0]
    y_top_max = poly[1][0]
    y_bot_max = poly[2][0]
    y_bot_min = poly[3][0]
    assert math.isclose(y_top_min - y_bot_min, shear, abs_tol=1e-9)
    assert math.isclose(y_top_max - y_bot_max, shear, abs_tol=1e-9)


def test_rd_segments_round_trip_to_traces() -> None:
    moves = [
        RDMove(x_mm=0.0, y_mm=10.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False),
        RDMove(x_mm=0.0, y_mm=20.0, speed_mm_s=50.0, power_pct=60.0, is_cut=True),
    ]
    payload = build_rd_job(moves, job_z_mm=None, air_assist=True)
    parser = RuidaParser(buf=payload)
    parser.decode(debug=False)

    joint = _joint_params(thickness_mm=10.0, edge_length_mm=100.0)
    jig = JigParams(axis_to_origin_mm=30.0, rotation_zero_deg=0.0, rotation_speed_dps=30.0)
    machine = _machine_params()

    traces = build_beam_traces_from_rd_segments(
        parser._segments,
        rotation_deg=0.0,
        board="tail",
        joint_params=joint,
        jig_params=jig,
        machine_params=machine,
    )
    y_center = joint.edge_length_mm / 2.0
    assert len(traces) == len(parser._segments)
    for trace, seg in zip(traces, parser._segments):
        assert math.isclose(trace.start_world[1], seg["y0"] + y_center, abs_tol=1e-6)
        assert math.isclose(trace.end_world[1], seg["y1"] + y_center, abs_tol=1e-6)
    assert any(
        trace.is_cut and math.isclose(trace.power_pct, 60.0, abs_tol=1e-6) for trace in traces
    )


def test_rd_z_offsets_animate_between_segments() -> None:
    moves = [
        RDMove(x_mm=0.0, y_mm=0.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False, z_mm=2.0),
        RDMove(x_mm=0.0, y_mm=10.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False),
        RDMove(x_mm=0.0, y_mm=10.0, speed_mm_s=100.0, power_pct=0.0, is_cut=False, z_mm=0.5),
        RDMove(x_mm=0.0, y_mm=20.0, speed_mm_s=50.0, power_pct=60.0, is_cut=True),
    ]
    payload = build_rd_job(moves, job_z_mm=None, air_assist=True)
    parser = RuidaParser(buf=payload)
    parser.decode(debug=False)

    joint = _joint_params(thickness_mm=10.0, edge_length_mm=100.0)
    jig = JigParams(axis_to_origin_mm=30.0, rotation_zero_deg=0.0, rotation_speed_dps=30.0)
    machine = _machine_params()

    traces = build_beam_traces_from_rd_segments(
        parser._segments,
        rotation_deg=0.0,
        board="tail",
        joint_params=joint,
        jig_params=jig,
        machine_params=machine,
    )
    assert any(
        not math.isclose(trace.start_machine_z, trace.end_machine_z, abs_tol=1e-9)
        for trace in traces
    )
