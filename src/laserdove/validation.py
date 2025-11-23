# validation.py
from __future__ import annotations

from typing import List

from .model import JointParams, JigParams, MachineParams, TailLayout


def validate_joint_params(joint_params: JointParams) -> List[str]:
    errors: List[str] = []

    if joint_params.num_tails <= 0:
        errors.append("num_tails must be > 0")

    if joint_params.edge_length_mm <= 0:
        errors.append("edge_length_mm must be > 0")

    if joint_params.thickness_mm <= 0:
        errors.append("thickness_mm must be > 0")

    if joint_params.tail_outer_width_mm <= 0:
        errors.append("tail_outer_width_mm must be > 0")

    if joint_params.dovetail_angle_deg <= 0 or joint_params.dovetail_angle_deg >= 80:
        errors.append("dovetail_angle_deg should be in (0, 80) degrees")

    if joint_params.tail_depth_mm <= 0:
        errors.append("tail_depth_mm must be > 0")

    if joint_params.socket_depth_mm <= 0:
        errors.append("socket_depth_mm must be > 0")

    if joint_params.kerf_tail_mm <= 0 or joint_params.kerf_pin_mm <= 0:
        errors.append("kerf_tail_mm and kerf_pin_mm must be > 0")

    return errors


def validate_tail_layout(joint_params: JointParams, layout: TailLayout) -> List[str]:
    errors: List[str] = []

    if not layout.tail_centers_y:
        errors.append("tail_centers_y is empty")
        return errors

    y_min = layout.tail_centers_y[0] - layout.tail_outer_width / 2.0
    y_max = layout.tail_centers_y[-1] + layout.tail_outer_width / 2.0
    if y_min < -1e-6 or y_max > joint_params.edge_length_mm + 1e-6:
        errors.append(
            f"Tails extend outside edge [0, L]: y_min={y_min:.3f}, "
            f"y_max={y_max:.3f}, L={joint_params.edge_length_mm:.3f}"
        )

    if layout.pin_outer_width <= 0:
        errors.append("Computed pin_outer_width <= 0; adjust tail width or edge length")

    return errors


def validate_machine_limits(machine_params: MachineParams) -> List[str]:
    errors: List[str] = []

    if machine_params.cut_speed_tail_mm_s <= 0 or machine_params.cut_speed_pin_mm_s <= 0:
        errors.append("Cut speeds must be > 0")

    if machine_params.rapid_speed_mm_s <= 0:
        errors.append("rapid_speed_mm_s must be > 0")

    if machine_params.z_speed_mm_s <= 0:
        errors.append("z_speed_mm_s must be > 0")

    if not (0 <= machine_params.cut_power_tail_pct <= 100):
        errors.append("cut_power_tail_pct must be in [0,100]")

    if not (0 <= machine_params.cut_power_pin_pct <= 100):
        errors.append("cut_power_pin_pct must be in [0,100]")

    return errors


def validate_jig(jig_params: JigParams) -> List[str]:
    errors: List[str] = []
    if jig_params.axis_to_origin_mm <= 0:
        errors.append("axis_to_origin_mm must be > 0 (distance from axis to job origin)")
    return errors


def validate_all(
    joint_params: JointParams,
    jig_params: JigParams,
    machine_params: MachineParams,
    layout: TailLayout,
) -> List[str]:
    errors: List[str] = []
    errors += validate_joint_params(joint_params)
    errors += validate_tail_layout(joint_params, layout)
    errors += validate_machine_limits(machine_params)
    errors += validate_jig(jig_params)
    return errors
