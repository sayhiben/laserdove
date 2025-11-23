# validation.py
from __future__ import annotations

from typing import List

from model import JointParams, JigParams, MachineParams, TailLayout


def validate_joint_params(jp: JointParams) -> List[str]:
    errors: List[str] = []

    if jp.num_tails <= 0:
        errors.append("num_tails must be > 0")

    if jp.edge_length_mm <= 0:
        errors.append("edge_length_mm must be > 0")

    if jp.thickness_mm <= 0:
        errors.append("thickness_mm must be > 0")

    if jp.tail_outer_width_mm <= 0:
        errors.append("tail_outer_width_mm must be > 0")

    if jp.dovetail_angle_deg <= 0 or jp.dovetail_angle_deg >= 80:
        errors.append("dovetail_angle_deg should be in (0, 80) degrees")

    if jp.tail_depth_mm <= 0:
        errors.append("tail_depth_mm must be > 0")

    if jp.socket_depth_mm <= 0:
        errors.append("socket_depth_mm must be > 0")

    if jp.kerf_tail_mm <= 0 or jp.kerf_pin_mm <= 0:
        errors.append("kerf_tail_mm and kerf_pin_mm must be > 0")

    return errors


def validate_tail_layout(jp: JointParams, layout: TailLayout) -> List[str]:
    errors: List[str] = []

    if not layout.tail_centers_y:
        errors.append("tail_centers_y is empty")
        return errors

    y_min = layout.tail_centers_y[0] - layout.tail_outer_width / 2.0
    y_max = layout.tail_centers_y[-1] + layout.tail_outer_width / 2.0
    if y_min < -1e-6 or y_max > jp.edge_length_mm + 1e-6:
        errors.append(
            f"Tails extend outside edge [0, L]: y_min={y_min:.3f}, "
            f"y_max={y_max:.3f}, L={jp.edge_length_mm:.3f}"
        )

    if layout.pin_outer_width <= 0:
        errors.append("Computed pin_outer_width <= 0; adjust tail width or edge length")

    return errors


def validate_machine_limits(mp: MachineParams) -> List[str]:
    errors: List[str] = []

    if mp.cut_speed_tail_mm_s <= 0 or mp.cut_speed_pin_mm_s <= 0:
        errors.append("Cut speeds must be > 0")

    if mp.rapid_speed_mm_s <= 0:
        errors.append("rapid_speed_mm_s must be > 0")

    if mp.z_speed_mm_s <= 0:
        errors.append("z_speed_mm_s must be > 0")

    if not (0 <= mp.cut_power_tail_pct <= 100):
        errors.append("cut_power_tail_pct must be in [0,100]")

    if not (0 <= mp.cut_power_pin_pct <= 100):
        errors.append("cut_power_pin_pct must be in [0,100]")

    return errors


def validate_jig(jg: JigParams) -> List[str]:
    errors: List[str] = []
    if jg.axis_to_origin_mm <= 0:
        errors.append("axis_to_origin_mm must be > 0 (distance from axis to job origin)")
    return errors


def validate_all(
    jp: JointParams,
    jg: JigParams,
    mp: MachineParams,
    layout: TailLayout,
) -> List[str]:
    errors: List[str] = []
    errors += validate_joint_params(jp)
    errors += validate_tail_layout(jp, layout)
    errors += validate_machine_limits(mp)
    errors += validate_jig(jg)
    return errors