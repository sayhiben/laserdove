# model.py
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional


class Side(Enum):
    LEFT = auto()
    RIGHT = auto()


@dataclass
class JointParams:
    """Geometry + fit + process parameters for one dovetail joint."""

    thickness_mm: float  # t
    edge_length_mm: float  # L
    dovetail_angle_deg: float  # β (used by both pins and tails in v1)
    num_tails: int  # N
    tail_outer_width_mm: float  # W_tail at outer face (X=0)
    tail_depth_mm: float  # D; depth into tail board
    socket_depth_mm: float  # D_pin; depth into pin board
    clearance_mm: float  # C; socket-face minus tail-face width
    kerf_tail_mm: float  # k_tail
    kerf_pin_mm: float  # k_pin


@dataclass
class JigParams:
    """Physical and kinematic properties of the rotary jig."""

    axis_to_origin_mm: float  # h; axis -> mid-edge top surface at θ=0
    rotation_zero_deg: float  # θ corresponding to "flat" board
    rotation_speed_dps: float  # deg/sec; coarse planning hint


@dataclass
class MachineParams:
    """Machine motion + cut params; Ruida specifics live elsewhere."""

    cut_speed_tail_mm_s: float
    cut_speed_pin_mm_s: float
    rapid_speed_mm_s: float
    z_speed_mm_s: float
    cut_power_tail_pct: float
    cut_power_pin_pct: float
    travel_power_pct: float
    cut_overtravel_mm: float

    # Z locations when the user focuses and zeros for each board
    z_zero_tail_mm: float  # focus at top of tail board
    z_zero_pin_mm: float  # focus at mid-thickness of pin board

    # Aux outputs
    air_assist: bool = True
    z_positive_moves_bed_up: bool = True

    # Optional soft limits for validation
    x_min_mm: float = 0.0
    y_min_mm: float = 0.0
    z_min_mm: float = 0.0
    x_max_mm: float = 9999.0
    y_max_mm: float = 9999.0
    z_max_mm: float = 9999.0


@dataclass
class TailLayout:
    """Logical layout of tails and pins along Y (0..L) on the tail board."""

    tail_centers_y: List[float]  # length N
    tail_outer_width: float
    pin_outer_width: float
    half_pin_width: float


@dataclass
class PinSide:
    """One flank of one pin, in board Y coordinates (0..L)."""

    pin_index: int
    side: Side  # LEFT or RIGHT
    y_boundary_mm: float  # Y at outer face where this flank lives
    rotation_deg: float  # absolute θ for this flank
    z_offset_mm: float  # delta Z relative to pin-board Z0
    x_depth_mm: float  # X depth of cut


@dataclass
class PinPlan:
    sides: List[PinSide]
    pin_outer_width: float
    half_pin_width: float


class CommandType(Enum):
    MOVE = auto()
    CUT_LINE = auto()
    SET_LASER_POWER = auto()
    ROTATE = auto()


@dataclass
class Command:
    """Abstract motion / laser / rotary command."""

    type: CommandType
    x: Optional[float] = None
    y: Optional[float] = None
    z: Optional[float] = None
    angle_deg: Optional[float] = None
    speed_mm_s: Optional[float] = None
    power_pct: Optional[float] = None
    comment: str = ""
