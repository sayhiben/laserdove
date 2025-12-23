from __future__ import annotations

import math
from pathlib import Path

from laserdove.hardware.rd_builder import RDMove, build_rd_job
from laserdove.hardware.ruida_common import swizzle
from laserdove.model import Command, CommandType, JigParams, JointParams, MachineParams
from laserdove.pygame_simulator import (
    BeamTrace,
    PygameSimulationViewer,
    _beam_traces_from_playback,
    _rd_job_contexts,
    build_beam_traces_from_rd_files,
)
from laserdove.sim_kinematics import PlaybackSegment


def _machine_params() -> MachineParams:
    return MachineParams(
        cut_speed_tail_mm_s=10.0,
        cut_speed_pin_mm_s=8.0,
        rapid_speed_mm_s=5.0,
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


def _joint_params(*, thickness_mm: float = 10.0, edge_length_mm: float = 100.0) -> JointParams:
    return JointParams(
        thickness_mm=thickness_mm,
        edge_length_mm=edge_length_mm,
        dovetail_angle_deg=8.0,
        num_tails=1,
        tail_outer_width_mm=10.0,
        tail_depth_mm=thickness_mm,
        socket_depth_mm=thickness_mm,
        clearance_mm=0.0,
        kerf_tail_mm=0.0,
        kerf_pin_mm=0.0,
    )


def _jig_params(*, axis_to_origin_mm: float = 30.0) -> JigParams:
    return JigParams(
        axis_to_origin_mm=axis_to_origin_mm, rotation_zero_deg=0.0, rotation_speed_dps=30.0
    )


def _write_rd(tmp_path: Path, moves: list[RDMove], name: str) -> Path:
    payload = build_rd_job(moves, job_z_mm=None, air_assist=True)
    path = tmp_path / f"{name}.rd"
    path.write_bytes(swizzle(payload))
    return path


def test_rd_job_contexts_detects_blocks():
    commands = [
        Command(type=CommandType.MOVE, x=0.0, y=0.0, speed_mm_s=10.0),
        Command(type=CommandType.ROTATE, angle_deg=15.0, speed_mm_s=30.0),
        Command(type=CommandType.MOVE, x=0.0, y=10.0, speed_mm_s=10.0),
    ]
    contexts = _rd_job_contexts(commands, rotation_zero_deg=0.0)
    assert contexts == [(0.0, "tail"), (15.0, "pin")]


def test_beam_traces_from_playback_computes_duration():
    joint = _joint_params()
    jig = _jig_params()
    machine = _machine_params()
    y_center = joint.edge_length_mm / 2.0
    axis = jig.axis_to_origin_mm

    playback = [
        PlaybackSegment(
            start_board=(0.0, 0.0, 0.0),
            end_board=(10.0, 0.0, 0.0),
            start_world=(0.0, y_center, axis),
            end_world=(10.0, y_center, axis),
            start_rotation_deg=0.0,
            end_rotation_deg=0.0,
            start_z_offset_mm=0.0,
            end_z_offset_mm=0.0,
            board="tail",
            is_cut=False,
            duration=0.0,
            power_pct=0.0,
            air_assist=True,
            source="plan",
        )
    ]

    traces = _beam_traces_from_playback(
        playback, joint_params=joint, jig_params=jig, machine_params=machine
    )
    assert math.isclose(traces[0].duration, 2.0, abs_tol=1e-6)
    assert math.isclose(
        traces[0].start_top[2] - traces[0].start_bottom[2], joint.thickness_mm, abs_tol=1e-6
    )


def test_viewer_rotation_and_metadata():
    joint = _joint_params()
    jig = _jig_params()
    machine = _machine_params()
    y_center = joint.edge_length_mm / 2.0
    axis = jig.axis_to_origin_mm

    playback = [
        PlaybackSegment(
            start_board=(0.0, 0.0, 0.0),
            end_board=(0.0, 0.0, 0.0),
            start_world=(0.0, y_center, axis),
            end_world=(0.0, y_center, axis),
            start_rotation_deg=0.0,
            end_rotation_deg=90.0,
            start_z_offset_mm=0.0,
            end_z_offset_mm=0.0,
            board="pin",
            is_cut=False,
            duration=2.0,
            power_pct=0.0,
            air_assist=True,
            source="plan",
        ),
        PlaybackSegment(
            start_board=(0.0, 0.0, 0.0),
            end_board=(0.0, 10.0, 0.0),
            start_world=(0.0, y_center, axis),
            end_world=(0.0, y_center + 10.0, axis),
            start_rotation_deg=90.0,
            end_rotation_deg=90.0,
            start_z_offset_mm=0.0,
            end_z_offset_mm=0.0,
            board="pin",
            is_cut=True,
            duration=1.0,
            power_pct=50.0,
            air_assist=True,
            source="plan",
        ),
    ]

    traces = _beam_traces_from_playback(
        playback, joint_params=joint, jig_params=jig, machine_params=machine
    )
    viewer = PygameSimulationViewer(
        traces,
        edge_length_mm=joint.edge_length_mm,
        thickness_mm=joint.thickness_mm,
        axis_to_origin_mm=jig.axis_to_origin_mm,
        rotation_zero_deg=jig.rotation_zero_deg,
        z_zero_tail_mm=machine.z_zero_tail_mm,
        z_zero_pin_mm=machine.z_zero_pin_mm,
        travel_time_scale=1.0,
    )

    assert math.isclose(viewer._rotation_at(1.0), 45.0, abs_tol=1e-6)
    assert viewer._current_trace_index(2.1) == 1

    meta = viewer._frame_metadata(0.5, file="frame.png")
    assert meta["board"] == "pin"
    assert math.isclose(meta["rotation_deg"], 22.5, abs_tol=1e-6)


def test_viewer_edge_polygons_and_beam_entry():
    joint = _joint_params()
    jig = _jig_params()
    machine = _machine_params()
    y_center = joint.edge_length_mm / 2.0
    axis = jig.axis_to_origin_mm

    def make_trace(y0: float, y1: float) -> BeamTrace:
        start_world = (0.0, y_center + y0, axis)
        end_world = (0.0, y_center + y1, axis)
        return BeamTrace(
            board="tail",
            is_cut=True,
            start_top=start_world,
            end_top=end_world,
            start_bottom=start_world,
            end_bottom=end_world,
            head_start=start_world,
            head_end=end_world,
            start_world=start_world,
            end_world=end_world,
            start_board_local=(0.0, y0, 0.0),
            end_board_local=(0.0, y1, 0.0),
            rotation_deg=0.0,
            rotation_end_deg=0.0,
            duration=1.0,
            power_pct=50.0,
            air_assist=True,
            start_machine_z=0.0,
            end_machine_z=0.0,
            source="test",
            is_rotation_only=False,
        )

    traces = [make_trace(-10.0, -5.0), make_trace(5.0, 10.0)]
    viewer = PygameSimulationViewer(
        traces,
        edge_length_mm=joint.edge_length_mm,
        thickness_mm=joint.thickness_mm,
        axis_to_origin_mm=jig.axis_to_origin_mm,
        rotation_zero_deg=jig.rotation_zero_deg,
        z_zero_tail_mm=machine.z_zero_tail_mm,
        z_zero_pin_mm=machine.z_zero_pin_mm,
    )

    polys = viewer._edge_cut_polygons(traces)
    assert len(polys) == 1
    _, _, poly = polys[0]
    assert poly[0] == (-10.0, 0.0)
    assert poly[1] == (10.0, 0.0)
    assert poly[2] == (10.0, -joint.thickness_mm)
    assert poly[3] == (-10.0, -joint.thickness_mm)

    assert viewer._edge_outline_local() == [
        (-y_center, 0.0),
        (y_center, 0.0),
        (y_center, -joint.thickness_mm),
        (-y_center, -joint.thickness_mm),
    ]

    top_world, bottom_world = viewer._beam_entry_exit(
        x_mm=0.0, y_mm=y_center, rotation_deg=0.0, z_offset_mm=0.0
    )
    assert math.isclose(top_world[2], axis, abs_tol=1e-6)
    assert math.isclose(bottom_world[2], axis - joint.thickness_mm, abs_tol=1e-6)
    assert viewer._nice_spacing(0.0) == 10.0


def test_build_beam_traces_from_rd_files_includes_extra_jobs(tmp_path: Path):
    joint = _joint_params()
    jig = _jig_params()
    machine = _machine_params()

    rd1 = _write_rd(
        tmp_path,
        [RDMove(x_mm=10.0, y_mm=0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False)],
        "job1",
    )
    rd2 = _write_rd(
        tmp_path,
        [RDMove(x_mm=20.0, y_mm=0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False)],
        "job2",
    )
    rd3 = _write_rd(
        tmp_path,
        [RDMove(x_mm=30.0, y_mm=0.0, speed_mm_s=50.0, power_pct=0.0, is_cut=False)],
        "job3",
    )

    commands = [
        Command(type=CommandType.MOVE, x=0.0, y=0.0, speed_mm_s=10.0),
        Command(type=CommandType.ROTATE, angle_deg=15.0, speed_mm_s=30.0),
        Command(type=CommandType.MOVE, x=0.0, y=10.0, speed_mm_s=10.0),
    ]

    traces = build_beam_traces_from_rd_files(
        [rd1, rd2, rd3],
        commands,
        joint_params=joint,
        jig_params=jig,
        machine_params=machine,
    )
    assert any(
        math.isclose(t.start_world[0], 30.0, abs_tol=1e-6)
        or math.isclose(t.end_world[0], 30.0, abs_tol=1e-6)
        for t in traces
    )
