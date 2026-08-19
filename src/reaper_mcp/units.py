"""Conversions between REAPER native units and MCP tool units.

REAPER stores track volume as a linear gain factor. The tools use dB.
Reapy 0.10 lacks volume, pan, mute, and solo properties on Track objects.
Low-level ReaScript accessors handle these properties.
"""

import math

from reapy import reascript_api as RPR

# Linear gain of 0.0 corresponds to a silent track and has no finite dB value.
DB_FLOOR = -150.0


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def linear_to_db(gain: float) -> float:
    if gain <= 0.0:
        return DB_FLOOR
    return 20.0 * math.log10(gain)


def get_volume_db(track) -> float:
    """Read track volume in dB."""
    return linear_to_db(RPR.GetMediaTrackInfo_Value(track.id, "D_VOL"))


def set_volume_db(track, db: float) -> float:
    """Set track volume in dB. Returns the applied value."""
    RPR.SetMediaTrackInfo_Value(track.id, "D_VOL", db_to_linear(db))
    return get_volume_db(track)


def set_solo(track, soloed: bool) -> bool:
    RPR.SetMediaTrackInfo_Value(track.id, "I_SOLO", 1 if soloed else 0)
    return bool(RPR.GetMediaTrackInfo_Value(track.id, "I_SOLO"))


def track_state(track) -> dict:
    """Read track volume, pan, mute, and solo states."""
    return {
        "volume_db": get_volume_db(track),
        "pan": RPR.GetMediaTrackInfo_Value(track.id, "D_PAN"),
        "muted": bool(RPR.GetMediaTrackInfo_Value(track.id, "B_MUTE")),
        "soloed": bool(RPR.GetMediaTrackInfo_Value(track.id, "I_SOLO")),
    }
