# geometry.py
from __future__ import annotations

import math
from typing import List

from model import JointParams, TailLayout


def compute_tail_layout(jp: JointParams) -> TailLayout:
    """
    Compute a symmetric layout along Y for tails and pins on the tail board,
    with Y in [0, L].

    Pattern:   half-pin, (tail, full-pin)*, tail, half-pin

    User specifies tail_outer_width_mm; we compute full pin width such that:

        L = N * tail_outer_width + N * pin_outer_width
    """
    L = jp.edge_length_mm
    N = jp.num_tails
    Wt = jp.tail_outer_width_mm

    if N <= 0:
        raise ValueError("num_tails must be positive")

    Wp = (L - N * Wt) / N
    if Wp <= 0:
        raise ValueError(
            f"Invalid layout: edge_length {L} too small for "
            f"{N} tails of width {Wt}"
        )

    half_pin = Wp / 2.0
    pitch = Wt + Wp

    tail_centers: List[float] = []
    for i in range(N):
        y_left = half_pin + i * pitch
        y_right = y_left + Wt
        tail_centers.append(0.5 * (y_left + y_right))

    return TailLayout(
        tail_centers_y=tail_centers,
        tail_outer_width=Wt,
        pin_outer_width=Wp,
        half_pin_width=half_pin,
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

    We split clearance evenly between tails and pins in v1.
    """
    base = kerf_mm / 2.0
    clearance_per_board = clearance_mm / 2.0
    offset_mag = base + clearance_per_board

    # Move into the waste side.
    if keep_on_positive_side:
        # Waste is negative side.
        direction = -1.0
    else:
        # Waste is positive side.
        direction = +1.0

    return y_geo + direction * offset_mag


def z_offset_for_angle(y_b_mm: float, angle_deg: float, h_mm: float) -> float:
    """
    Compute Z offset (mm) required to keep the top surface point at board
    coordinate Y_b in focus, assuming:

      - We focused at Y_b = 0, θ = 0, with top-surface radius h_mm from axis.
      - Y_b is measured along the edge from the mid-edge (job origin), so
        Y_b = 0 corresponds to the mid-edge.

    Result is "bed move" relative to the 0° focus height.
    Sign convention:
      Positive means "move bed up by this amount" if machine Z+ is "bed up".
    """
    theta = math.radians(angle_deg)
    z_physical = y_b_mm * math.sin(theta) + h_mm * math.cos(theta)
    z_physical_0 = h_mm
    delta_physical = z_physical - z_physical_0
    return -delta_physical


def center_outward_indices(y_values: List[float]) -> List[int]:
    """
    Return indices of y_values ordered center-outward.

    "Center" is taken as (min(y) + max(y)) / 2, which matches the joint
    mid-edge when y-values span the full 0..L range.
    """
    if not y_values:
        return []
    y_min = min(y_values)
    y_max = max(y_values)
    y_center = 0.5 * (y_min + y_max)

    indexed = list(enumerate(y_values))
    indexed.sort(key=lambda iv: abs(iv[1] - y_center))
    return [i for i, _ in indexed]