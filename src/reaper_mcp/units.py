"""Conversions between REAPER native units and MCP tool units.

REAPER stores track volume as a linear gain factor. The tools use dB.
Reapy 0.10 lacks volume, pan, mute, and solo properties on Track objects.
Low-level ReaScript accessors handle these properties.
"""

import math

from reaper_mcp.connection import RPR

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


# I_PANMODE values. -1 follows the project; 0 is the deprecated 3.x balance mode.
PAN_MODE_NAMES = {0: "classic", 3: "balance", 5: "stereo", 6: "dual"}
PAN_MODE_VALUES = {"project": -1, "balance": 3, "stereo": 5, "dual": 6}


def _config_number(name: str):
    ok, _, text, _ = RPR.get_config_var_string(name, "", 64)
    try:
        return float(text) if ok else None
    except ValueError:
        return None


def project_pan_defaults() -> tuple:
    """Read the active project's pan mode (I_PANMODE value) and pan law (gain).

    No API returns them, but REAPER keeps the active project's settings in the
    config variables "panmode" and "panlaw": loading a project saved with
    PANMODE 5 and PANLAW 0.708 changed both. Either is None when unreadable.
    """
    mode = _config_number("panmode")
    return (int(mode) if mode is not None else None), _config_number("panlaw")


def pan_mode_name(value) -> str:
    return PAN_MODE_NAMES.get(value, f"mode {value}") if value is not None else "project"


def pan_state(track, project_pan: tuple) -> dict:
    """Read a track's pan, pan mode, width and pan law, resolving "follow the project".

    pan_law_db is 20*log10 of the law's gain: 0.0 for the 0 dB law, -3.0 for -3 dB.
    REAPER stores its "with gain compensation" laws above 1, so they read positive.
    In dual pan mode REAPER ignores D_PAN and D_WIDTH (measured), so the two dual
    pans are added.
    """
    mode = int(RPR.GetMediaTrackInfo_Value(track.id, "I_PANMODE"))
    law = RPR.GetMediaTrackInfo_Value(track.id, "D_PANLAW")
    mode_from_project, law_from_project = mode < 0, law < 0
    if mode_from_project:
        mode = project_pan[0]
    if law_from_project:
        law = project_pan[1]
    state = {
        "pan": RPR.GetMediaTrackInfo_Value(track.id, "D_PAN"),
        "pan_mode": pan_mode_name(mode),
        "pan_mode_from_project": mode_from_project,
        "width": RPR.GetMediaTrackInfo_Value(track.id, "D_WIDTH"),
        "pan_law_db": round(linear_to_db(law), 2) if law is not None else None,
        "pan_law_from_project": law_from_project,
    }
    if mode == 6:
        state["dual_pan"] = [RPR.GetMediaTrackInfo_Value(track.id, "D_DUALPANL"),
                             RPR.GetMediaTrackInfo_Value(track.id, "D_DUALPANR")]
    return state


def track_state(track, project_pan: tuple | None = None) -> dict:
    """Read track volume, pan, pan mode, width, pan law, mute, and solo states.

    Pass project_pan_defaults() when reading many tracks, to read it once.
    """
    return {
        "volume_db": get_volume_db(track),
        **pan_state(track, project_pan or project_pan_defaults()),
        "muted": bool(RPR.GetMediaTrackInfo_Value(track.id, "B_MUTE")),
        "soloed": bool(RPR.GetMediaTrackInfo_Value(track.id, "I_SOLO")),
    }


def parse_channels(text: str) -> tuple:
    """Parse "3/4" (pair), "3" (one channel) or "1-4" (a range) into (first, count).

    Channels are 1-based, as REAPER's routing window shows them. Raises ValueError.
    """
    text = str(text).strip()
    try:
        if "/" in text:
            first, second = (int(part) for part in text.split("/"))
            if second != first + 1:
                raise ValueError
            count = 2
        elif "-" in text:
            first, last = (int(part) for part in text.split("-"))
            count = last - first + 1
            if count < 2:
                raise ValueError
        else:
            first, count = int(text), 1
    except ValueError:
        raise ValueError(f"channels must look like '3/4', '3' or '1-4', got {text!r}") from None
    if first < 1 or first + count - 1 > 128:
        raise ValueError(f"channels must lie within 1-128, got {text!r}")
    return first, count


def format_channels(first: int, count: int) -> str:
    if count == 1:
        return str(first)
    if count == 2:
        return f"{first}/{first + 1}"
    return f"{first}-{first + count - 1}"


def send_source_channels(value: float) -> tuple:
    """Decode a send's I_SRCCHAN into (first, count), or (0, 0) for no audio.

    The low 10 bits hold the first channel; the bits above hold the width:
    0 for a stereo pair, 1 for mono, and n for 2n channels.
    """
    value = int(value)
    if value < 0:
        return 0, 0
    width = value >> 10
    return (value & 1023) + 1, {0: 2, 1: 1}.get(width, 2 * width)


def send_source_value(first: int, count: int) -> int:
    width = {2: 0, 1: 1}.get(count, count // 2)
    return (width << 10) | (first - 1)


def send_dest_channels(value: float, source_count: int) -> tuple:
    """Decode a send's I_DSTCHAN into (first, count) at the destination.

    The low 10 bits hold the first channel; bit 1024 mixes the source down to
    one channel.
    """
    value = int(value)
    count = 1 if value & 1024 or source_count == 1 else source_count
    return (value & 1023) + 1, count


def native_color(rgb) -> int:
    """Convert [r, g, b] to a REAPER custom colour. Raises ValueError."""
    if not isinstance(rgb, (list, tuple)) or len(rgb) != 3 or not all(
        isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in rgb
    ):
        raise ValueError(f"color must be [r, g, b] with each 0-255, got {rgb!r}")
    # Bit 24 marks the colour as set; without it REAPER shows the default colour.
    return RPR.ColorToNative(*rgb) | 0x1000000


def rgb_color(native: float):
    """Convert a REAPER custom colour to [r, g, b], or None when none is set."""
    native = int(native)
    if not native & 0x1000000:
        return None
    return list(RPR.ColorFromNative(native & 0xFFFFFF, 0, 0, 0)[-3:])


def project_tempo() -> float:
    """Read the project tempo in quarter-note BPM.

    Reapy's Project.bpm reads GetProjectTimeSignature2, which returns the project
    BPM *setting*. REAPER scales that value by the time signature denominator, so a
    120 BPM project reports 240 in 7/8 and 60 in 3/2. Master_GetTempo returns
    quarter-note BPM regardless of denominator, matching the unit that
    SetTempoTimeSigMarker, SetCurrentBPM, and the .rpp TEMPO field all use.
    """
    return float(RPR.Master_GetTempo())
