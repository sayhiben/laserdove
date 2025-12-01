"""
Shared RD opcode definitions and controller profiles.

This module is the single source of truth for RD command labels so tools and runtime
code do not drift. Parser attaches handlers; runtime uses labels for logging/metadata.
Profiles allow small per-controller tweaks (e.g., magic key, opcode variants) without
forking tables.

Notes:
- CA 41 appears to encode a layer mode flag: 0x00 covers lines and some fills
  (e.g., CROSS1–6 in the calibration RD), 0x02 covers fills/images/LPI/etc. Keep “?”.
- C6 11 is labeled “Time” in EduTech; meaning remains uncertain.
- E5 05 decodes to a float spacing (0.6419mm in calibration RD) and appears once near
  the trailer; likely a job-level spacing/DPI summary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict

RD_COMMANDS: Dict[int, Any] = {
    0x80: {
        0x00: "AXIS_X_MOVE",
        0x03: "AXIS_Z_OFFSET",
        0x08: "AXIS_Y_MOVE",
    },
    0x88: "MOVE_ABS_XY",
    0x89: "MOVE_REL_XY",
    0x8A: "MOVE_REL_X",
    0x8B: "MOVE_REL_Y",
    0xA8: "CUT_ABS_XY",
    0xA9: "CUT_REL_XY",
    0xAA: "CUT_REL_X",
    0xAB: "CUT_REL_Y",
    0xC0: "UNKNOWN_C0",
    0xC1: "UNKNOWN_C1",
    0xC2: "UNKNOWN_C2",
    0xC3: "UNKNOWN_C3",
    0xC4: "UNKNOWN_C4",
    0xC5: "UNKNOWN_C5",
    0xC6: "POWER_TABLE",
    0xC7: "IMD_POWER_1",
    0xC8: "END_POWER_1",
    0xC9: {
        0x02: "SPEED_LASER_1",
        0x03: "SPEED_AXIS",
        0x04: "LAYER_SPEED",
        0x05: "FORCE_ENG_SPEED",
    },
    0xCA: {
        0x01: {
            0x00: "LAYER_END",
            0x01: "WORK_MODE_1",
            0x02: "WORK_MODE_2",
            0x03: "WORK_MODE_3",
            0x04: "WORK_MODE_4",
            0x05: "WORK_MODE_6",
            0x10: "LASER_DEVICE_0",
            0x11: "LASER_DEVICE_1",
            0x12: "AIR_ASSIST_OFF",
            0x13: "AIR_ASSIST_ON",
            0x14: "DB_HEAD",
            0x30: "ENABLE_LASER2_OFFSET0",
            0x31: "ENABLE_LASER2_OFFSET1",
            0x55: "WORK_MODE_5",
        },
        0x02: "LAYER_NUMBER_PART",
        0x03: "UNKNOWN_CA_03",
        0x06: "LAYER_COLOR",
        0x10: "UNKNOWN_CA_10",
        0x12: "BLOW_OFF",
        0x13: "BLOW_ON",
        0x22: "LAYER_COUNT",
        0x41: "LAYER_MODE?",  # 0x00 vs 0x02 (line vs fill/image/LPI)
    },
    0xCC: "ACK_CC",
    0xD7: "EOF",
    0xD8: {
        0x00: "START_PROCESS",
        0x01: "STOP_PROCESS",
        0x02: "PAUSE_PROCESS",
        0x03: "RESTORE_PROCESS",
        0x10: "UNKNOWN_D8_10",
        0x11: "UNKNOWN_D8_11",
        0x12: "UPLOAD_FOLLOWS",
    },
    0xD9: {
        0x00: "RAPID_MOVE_X",
        0x01: "RAPID_MOVE_Y",
        0x02: "RAPID_MOVE_Z",
        0x03: "DIRECT_MOVE_U_REL",
        0x10: "RAPID_MOVE_XY",
    },
    0xDA: {
        0x00: "WORK_INTERVAL_QUERY",
        0x01: "WORK_INTERVAL_RESP",
    },
    0xE5: {
        0x05: "WORK_SPACING?",  # decodes as float spacing; seen once near trailer
    },
    0xE6: {
        0x01: "SET_ABSOLUTE",
    },
    0xE7: {
        0x00: "STOP",
        0x01: "SET_FILENAME",
        0x03: "BOUNDING_BOX_TOP_LEFT",
        0x04: "LAYER_BBOX_RESET?",
        0x05: "LAYER_BBOX_FLUSH?",
        0x06: "FEEDING",
        0x07: "BOUNDING_BOX_BOTTOM_RIGHT",
        0x08: "LAYER_BBOX_BOTTOM_RIGHT?",
        0x13: "LAYOUT_ORIGIN?",
        0x17: "LAYOUT_BOTTOM_RIGHT?",
        0x23: "LAYOUT_ORIGIN_ALT?",
        0x24: "LAYOUT_FLAGS?",
        0x37: "LAYOUT_BBOX_ALT?",
        0x38: "JOB_UNITS?",
        0x50: "BOUNDING_BOX_TOP_LEFT",
        0x51: "BOUNDING_BOX_BOTTOM_RIGHT",
        0x52: "LAYER_TOP_LEFT",
        0x53: "LAYER_BOTTOM_RIGHT",
        0x54: "PEN_DRAW_Y",
        0x55: "LASER_Y_OFFSET",
        0x60: "UNKNOWN_E7_60",
        0x61: "LAYER_TOP_LEFT_ALT",
        0x62: "LAYER_BOTTOM_RIGHT_ALT",
    },
    0xE8: {
        0x01: "FILESTORE_E8_01",
        0x02: "PREP_FILENAME_E8_02",
    },
    0xEA: "UNKNOWN_EA",
    0xEB: "FINISH",
    0xF0: "MAGIC_88",
    0xF1: {
        0x00: "START0",
        0x01: "START1",
        0x02: "START2",
        0x03: "LASER2_OFFSET",
        0x04: "ENABLE_FEEDING",
    },
    0xF2: {
        0x00: "RASTER_PARAMS_00",
        0x01: "RASTER_PARAMS_01",
        0x02: "JOB_SCALE?",
        0x03: "JOB_TOP_LEFT?",
        0x04: "JOB_BOTTOM_RIGHT?",
        0x05: "JOB_SIZE?",
        0x06: "JOB_OFFSETS?",
        0x07: "JOB_FLAGS?",
    },
}


@dataclass(frozen=True)
class RuidaProfile:
    """
    Encapsulates controller-specific quirks for protocol decoding/building.

    Fields allow per-model overrides while keeping the base opcode labels shared.
    """

    name: str
    swizzle_magic: int = 0x88
    command_overrides: Dict[int, Any] = field(default_factory=dict)
    decoder_overrides: Dict[int, Any] = field(default_factory=dict)


DEFAULT_PROFILE_NAME = "rdc6442g"

PROFILES: Dict[str, RuidaProfile] = {
    DEFAULT_PROFILE_NAME: RuidaProfile(name=DEFAULT_PROFILE_NAME, swizzle_magic=0x88),
}


def _deep_merge(base: Dict[int, Any], overrides: Dict[int, Any]) -> Dict[int, Any]:
    """Recursively merge protocol tables (supports nested dicts)."""
    merged: Dict[int, Any] = {}
    for key, val in base.items():
        merged[key] = val
    for key, val in overrides.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def merge_protocol_tables(*tables: Dict[int, Any]) -> Dict[int, Any]:
    """Merge multiple protocol tables left-to-right with deep dict merging."""
    merged: Dict[int, Any] = {}
    for table in tables:
        merged = _deep_merge(merged, table)
    return merged


def get_profile(profile: str | RuidaProfile | None = None) -> RuidaProfile:
    """Resolve a profile name/object to a RuidaProfile, defaulting to RDC6442G."""
    if isinstance(profile, RuidaProfile):
        return profile
    if profile is None:
        return PROFILES[DEFAULT_PROFILE_NAME]
    key = profile.lower()
    if key not in PROFILES:
        raise ValueError(f"Unknown Ruida profile '{profile}'")
    return PROFILES[key]


def command_table_for(profile: str | RuidaProfile | None = None) -> Dict[int, Any]:
    """
    Return a merged command label table for the requested profile.

    This is the entry point consumers should use to avoid duplicating opcode labels.
    """
    prof = get_profile(profile)
    return merge_protocol_tables(RD_COMMANDS, prof.command_overrides)


__all__ = [
    "RD_COMMANDS",
    "RuidaProfile",
    "DEFAULT_PROFILE_NAME",
    "PROFILES",
    "command_table_for",
    "get_profile",
    "merge_protocol_tables",
]
