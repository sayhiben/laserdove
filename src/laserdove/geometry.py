# geometry.py
from __future__ import annotations

import math
from typing import List

from .model import JointParams, TailLayout


def compute_tail_layout(joint_params: JointParams) -> TailLayout:
    """
    Compute a symmetric layout along Y for tails and pins on the tail board,
    with Y in [0, L].

    Pattern:   half-pin, (tail, full-pin)*, tail, half-pin

    User specifies tail_outer_width_mm; we compute full pin width such that:

        L = N * tail_outer_width + N * pin_outer_width
    """
    edge_length_mm = joint_params.edge_length_mm
    num_tails = joint_params.num_tails
    tail_outer_width_mm = joint_params.tail_outer_width_mm

    if num_tails <= 0:
        raise ValueError("num_tails must be positive")

    pin_outer_width_mm = (edge_length_mm - num_tails * tail_outer_width_mm) / num_tails
    if pin_outer_width_mm <= 0:
        raise ValueError(
            f"Invalid layout: edge_length {edge_length_mm} too small for "
            f"{num_tails} tails of width {tail_outer_width_mm}"
        )

    half_pin_width = pin_outer_width_mm / 2.0
    tail_pin_pitch = tail_outer_width_mm + pin_outer_width_mm

    tail_centers: List[float] = []
    for tail_index in range(num_tails):
        y_left = half_pin_width + tail_index * tail_pin_pitch
        y_right = y_left + tail_outer_width_mm
        tail_centers.append(0.5 * (y_left + y_right))

    return TailLayout(
        tail_centers_y=tail_centers,
        tail_outer_width=tail_outer_width_mm,
        pin_outer_width=pin_outer_width_mm,
        half_pin_width=half_pin_width,
    )


def kerf_offset_boundary(
    y_geo: float,
    kerf_mm: float,
    clearance_mm: float,
    keep_on_positive_side: bool,
    is_tail_board: bool,  # reserved for future biasing
) -> float:
    """
    Compute Y of the cut centerline for a straight boundary at Y=y_geo.

    - kerf_mm: full kerf width.
    - clearance_mm: total socket-minus-tail clearance at the face.
    - keep_on_positive_side:
        True  -> material to keep is at Y > y_geo
        False -> material to keep is at Y < y_geo
    - is_tail_board: not used in v1; allows future biasing.

    We split clearance evenly between tails and pins in v1 by shifting the
    *kept* boundary by +/- clearance/2 and then placing the cut so the kerf
    edge lands on that shifted boundary.
    """
    kerf_radius = kerf_mm / 2.0
    clearance_per_board = clearance_mm / 2.0

    keep_sign = 1.0 if keep_on_positive_side else -1.0
    # Shift the desired kept boundary toward the keep side by clearance/2.
    boundary_shift = keep_sign * clearance_per_board

    # Place the cut so that the kerf edge on the keep side sits at the shifted boundary.
    return y_geo + boundary_shift - keep_sign * kerf_radius


def z_offset_for_angle(y_b_mm: float, angle_deg: float, axis_to_origin_mm: float) -> float:
    """
    Compute Z offset (mm) required to keep the top surface point at board
    coordinate Y_b in focus, assuming:

      - We focused at Y_b = 0, θ = 0, with top-surface radius axis_to_origin_mm from axis.
      - Y_b is measured along the edge from the mid-edge (job origin), so
        Y_b = 0 corresponds to the mid-edge.

    Result is "bed move" relative to the 0° focus height.
    Sign convention:
      Positive means "move bed up by this amount" if machine Z+ is "bed up".
    """
    # Use the magnitude of the tilt; the sign of y_b already encodes which edge
    # is farther/closer to the head at a given rotation.
    angle_rad = math.radians(abs(angle_deg))
    z_physical = y_b_mm * math.sin(angle_rad) + axis_to_origin_mm * math.cos(angle_rad)
    z_physical_at_origin = axis_to_origin_mm
    delta_physical = z_physical - z_physical_at_origin
    return -delta_physical
