from __future__ import annotations

import math


def clamp_board_y(y_val: float, edge_length_mm: float) -> float:
    """Limit board-space Y to the material span to avoid overcutting past the ends."""
    return max(0.0, min(edge_length_mm, y_val))


def project_board_y(
    y_board: float,
    *,
    edge_length_mm: float,
    axis_to_origin_mm: float,
    rotation_deg: float,
    rotation_zero_deg: float,
) -> float:
    """
    Project board Y into machine Y for a rotated board about the rotary axis.
    """
    y_center = edge_length_mm / 2.0
    delta = rotation_deg - rotation_zero_deg
    angle_rad = math.radians(delta)
    cos_theta = math.cos(angle_rad)
    sin_theta = math.sin(angle_rad)
    return y_center + (y_board - y_center) * cos_theta - axis_to_origin_mm * sin_theta
