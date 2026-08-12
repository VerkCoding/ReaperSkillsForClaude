"""Conversions between REAPER's native units and the units the tools expose.

REAPER stores track and master volume as a linear gain factor (1.0 == 0 dB),
while the MCP tools speak dB. reapy 0.10 exposes no volume/pan/mute/solo
properties on Track, so callers reach these values through the low-level
ReaScript accessors (D_VOL, D_PAN, B_MUTE, I_SOLO) and convert here.
"""

import math

from reapy import reascript_api as RPR

# REAPER's practical floor; a silent track reads a linear gain of 0.0, which
# has no finite dB value.
DB_FLOOR = -150.0


def db_to_linear(db: float) -> float:
    return 10.0 ** (db / 20.0)


def linear_to_db(gain: float) -> float:
    if gain <= 0.0:
        return DB_FLOOR
    return 20.0 * math.log10(gain)


def get_volume_db(track) -> float:
    """Read a track's volume in dB. Works for regular and master tracks."""
    return linear_to_db(RPR.GetMediaTrackInfo_Value(track.id, "D_VOL"))


def set_volume_db(track, db: float) -> float:
    """Set a track's volume in dB and return the value REAPER settled on."""
    RPR.SetMediaTrackInfo_Value(track.id, "D_VOL", db_to_linear(db))
    return get_volume_db(track)


def set_solo(track, soloed: bool) -> bool:
    RPR.SetMediaTrackInfo_Value(track.id, "I_SOLO", 1 if soloed else 0)
    return bool(RPR.GetMediaTrackInfo_Value(track.id, "I_SOLO"))


def track_state(track) -> dict:
    """Read volume/pan/mute/solo for a track via ReaScript."""
    return {
        "volume_db": get_volume_db(track),
        "pan": RPR.GetMediaTrackInfo_Value(track.id, "D_PAN"),
        "muted": bool(RPR.GetMediaTrackInfo_Value(track.id, "B_MUTE")),
        "soloed": bool(RPR.GetMediaTrackInfo_Value(track.id, "I_SOLO")),
    }
