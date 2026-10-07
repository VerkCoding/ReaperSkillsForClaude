"""Automation envelopes on tracks and FX parameters.

Values cross the tool boundary in the units a mixer thinks in and are converted
to what REAPER stores. Each conversion below was established by writing a value
and reading Envelope_FormatValue, REAPER's own display of it:

* Volume, pre-FX volume and trim store fader-scaled gain. A raw linear 0.5
  displays as -inf dB; ScaleToEnvelopeMode(1, 0.5) = 592.4 displays as -6.02dB.
* Pan envelopes store pan inverted: 0.5 displays as 50%L, while a track's D_PAN
  of -0.5 is 50%L.
* Mute envelopes store 0 for MUTE and 1 for UNMUTE.
* FX parameter envelopes store the parameter's native value, not the normalised
  one: a point at -6 on JS Volume Adjustment (range -150 to 150) evaluates to -6.
"""

import logging
import math

from reaper_mcp.connection import RPR, get_project, held, is_null, records_undo, undo_step
from reaper_mcp.units import db_to_linear, linear_to_db

logger = logging.getLogger("reaper_mcp.envelope_tools")

# Track envelopes by the name REAPER gives them, with the action that shows them.
# Showing an envelope that does not exist yet creates it on the selected tracks.
TRACK_ENVELOPES = {
    "Volume": 40406,
    "Pan": 40407,
    "Volume (Pre-FX)": 40408,
    "Pan (Pre-FX)": 40409,
    "Mute": 40867,
    "Trim Volume": 42020,
    "Width": 41870,
    "Width (Pre-FX)": 41869,
}
_GAIN = {"Volume", "Volume (Pre-FX)", "Trim Volume"}
_PAN = {"Pan", "Pan (Pre-FX)"}
# Linear gain of +24 dB, the top of REAPER's widest volume envelope range.
_MAX_GAIN = 16.0
_MAX_POINTS = 2000
# InsertEnvelopePoint and DeleteEnvelopePointRange take times in seconds;
# points closer than this are treated as the same time.
_TIME_EPS = 1e-6


class _Envelope:
    """One resolved envelope and the conversion between tool and stored units."""

    def __init__(self, pointer, name, kind, low=0.0, high=1.0):
        self.pointer = pointer
        self.name = name
        self.kind = kind  # gain, pan, width, mute or fx
        self.low, self.high = low, high
        self.mode = RPR.GetEnvelopeScalingMode(pointer)

    @property
    def unit(self) -> str:
        return {
            "gain": "linear gain, 1.0 = 0 dB",
            "pan": "-1 left to 1 right",
            "width": "-1 to 1",
            "mute": "1 muted, 0 unmuted",
            "fx": "normalized 0-1",
        }[self.kind]

    def check(self, point: dict):
        """Return the value a point asks for in tool units, or raise ValueError."""
        if self.kind == "gain" and "db" in point:
            if "value" in point:
                raise ValueError("give value (linear gain) or db, not both")
            db = point["db"]
            if not isinstance(db, (int, float)) or isinstance(db, bool) or db > 24:
                raise ValueError(f"db must be a number up to 24, got {db!r}")
            return 0.0 if db <= -150 else db_to_linear(db)
        if "value" not in point:
            raise ValueError("each point needs time and value")
        value = point["value"]
        if not isinstance(value, (int, float)) or isinstance(value, bool) or math.isnan(value):
            raise ValueError(f"value must be a number, got {value!r}")
        if self.kind == "gain":
            if value < 0:
                raise ValueError(
                    f"{self.name} value is linear gain (1.0 = 0 dB) and cannot be negative, "
                    f"got {value}: that looks like dB. Use db instead, or 10 ** (dB / 20)."
                )
            if value > _MAX_GAIN:
                raise ValueError(f"{self.name} gain {value} is above +24 dB")
        elif self.kind in ("pan", "width") and not -1.0 <= value <= 1.0:
            raise ValueError(f"{self.name} value must be -1 to 1, got {value}")
        elif self.kind == "mute" and value not in (0, 1):
            raise ValueError(f"Mute value must be 1 (muted) or 0 (unmuted), got {value}")
        elif self.kind == "fx" and not 0.0 <= value <= 1.0:
            raise ValueError(f"FX parameter value must be normalized 0-1, got {value}")
        return float(value)

    def to_stored(self, value: float) -> float:
        if self.kind == "pan":
            value = -value
        elif self.kind == "mute":
            value = 1.0 - value
        elif self.kind == "fx":
            value = self.low + value * (self.high - self.low)
        return RPR.ScaleToEnvelopeMode(self.mode, value)

    def from_stored(self, stored: float) -> float:
        value = RPR.ScaleFromEnvelopeMode(self.mode, stored)
        if self.kind == "pan":
            return -value
        if self.kind == "mute":
            return 1.0 - value
        if self.kind == "fx":
            span = self.high - self.low
            return (value - self.low) / span if span else 0.0
        return value

    def describe(self, time: float, stored: float, shape: int, tension: float) -> dict:
        value = self.from_stored(stored)
        point = {"time": time, "value": round(value, 6) + 0.0}
        if self.kind == "gain":
            point["db"] = round(linear_to_db(value), 2)
        if shape:
            point["shape"] = shape
        if tension:
            point["tension"] = tension
        point["display"] = RPR.Envelope_FormatValue(self.pointer, stored, "", 64)[2]
        return point

    def points(self, start: float = 0.0, end=None) -> list:
        """Return (time, stored, shape, tension) for every point in [start, end]."""
        found = []
        for i in range(RPR.CountEnvelopePoints(self.pointer)):
            _, _, _, time, stored, shape, tension, _ = RPR.GetEnvelopePoint(
                self.pointer, i, 0, 0, 0, 0, 0
            )
            if time < start - _TIME_EPS or (end is not None and time > end + _TIME_EPS):
                continue
            found.append((time, stored, shape, tension))
        return found

    @property
    def active(self) -> bool:
        return RPR.GetSetEnvelopeInfo_String(self.pointer, "ACTIVE", "", False)[3] != "0"


def _existing_envelopes(track) -> list:
    names = []
    for i in range(RPR.CountTrackEnvelopes(track)):
        names.append(RPR.GetEnvelopeName(RPR.GetTrackEnvelope(track, i), "", 256)[2])
    return names


def _show_track_envelope(track, action: int) -> None:
    """Run an envelope-show action on one track and put the selection back."""
    selected = [RPR.GetSelectedTrack2(0, i, False) for i in range(RPR.CountSelectedTracks2(0, False))]
    RPR.SetOnlyTrackSelected(track)
    RPR.Main_OnCommandEx(action, 0, 0)
    RPR.SetTrackSelected(track, False)
    for other in selected:
        RPR.SetTrackSelected(other, True)


def _resolve(track_index, envelope, fx_index, param_index, create: bool):
    """Return (_Envelope, created) or raise LookupError / ValueError with the reason."""
    if track_index < 0:
        raise ValueError(f"track_index must be 0 or greater, got {track_index}")
    n_tracks = RPR.CountTracks(0)
    if track_index >= n_tracks:
        raise ValueError(f"track_index {track_index} out of range, project has {n_tracks} tracks")
    track = RPR.GetTrack(0, track_index)

    if fx_index is not None or param_index is not None:
        if fx_index is None or param_index is None:
            raise ValueError("an FX parameter envelope needs both fx_index and param_index")
        n_fx = RPR.TrackFX_GetCount(track)
        if not 0 <= fx_index < n_fx:
            raise ValueError(f"fx_index {fx_index} out of range, track has {n_fx} FX")
        n_params = RPR.TrackFX_GetNumParams(track, fx_index)
        if not 0 <= param_index < n_params:
            raise ValueError(f"param_index {param_index} out of range, FX has {n_params} parameters")
        existed = not is_null(RPR.GetFXEnvelope(track, fx_index, param_index, False))
        pointer = RPR.GetFXEnvelope(track, fx_index, param_index, create)
        if is_null(pointer):
            raise LookupError(f"FX {fx_index} parameter {param_index} has no envelope")
        _, _, _, _, low, high = RPR.TrackFX_GetParam(track, fx_index, param_index, 0, 0)
        name = RPR.GetEnvelopeName(pointer, "", 256)[2]
        return _Envelope(pointer, name, "fx", low, high), not existed

    if envelope not in TRACK_ENVELOPES:
        raise ValueError(f"envelope must be one of {', '.join(TRACK_ENVELOPES)}, got {envelope!r}")
    pointer = RPR.GetTrackEnvelopeByName(track, envelope)
    created = False
    if is_null(pointer) and create:
        _show_track_envelope(track, TRACK_ENVELOPES[envelope])
        pointer = RPR.GetTrackEnvelopeByName(track, envelope)
        created = True
    if is_null(pointer):
        existing = _existing_envelopes(track)
        raise LookupError(
            f"track {track_index} has no {envelope} envelope"
            + (f"; it has: {', '.join(existing)}" if existing else "")
        )
    if envelope in _GAIN:
        kind = "gain"
    elif envelope in _PAN:
        kind = "pan"
    elif envelope == "Mute":
        kind = "mute"
    else:
        kind = "width"
    return _Envelope(pointer, envelope, kind), created


def register_tools(mcp):

    @mcp.tool()
    def get_envelope_points(
        track_index: int,
        envelope: str = "Volume",
        fx_index: int | None = None,
        param_index: int | None = None,
        start: float = 0.0,
        end: float | None = None,
        max_points: int = 500,
    ) -> dict:
        """Read automation points from a track envelope or, with fx_index and param_index, an FX parameter envelope.

        envelope: Volume, Pan, Width, Mute, Trim Volume, or "Volume (Pre-FX)" etc.
        Values come back in the units add_envelope_points takes, with REAPER's display text.
        """
        try:
            if max_points < 1:
                return {"success": False, "error": f"max_points must be 1 or more, got {max_points}"}
            get_project()
            with held():
                env, _ = _resolve(track_index, envelope, fx_index, param_index, create=False)
                found = env.points(start, end)
                shown = [env.describe(*p) for p in found[:max_points]]
                return {
                    "success": True,
                    "track_index": track_index,
                    "envelope": env.name,
                    "unit": env.unit,
                    "active": env.active,
                    "point_count": RPR.CountEnvelopePoints(env.pointer),
                    "points": shown,
                    "truncated": len(found) > max_points,
                }
        except (LookupError, ValueError) as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"get_envelope_points failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo(own_step=True)
    def add_envelope_points(
        track_index: int,
        points: list[dict],
        envelope: str = "Volume",
        fx_index: int | None = None,
        param_index: int | None = None,
    ) -> dict:
        """Add automation points, creating and activating the envelope if needed. Existing points stay; clear a range first to replace it.

        points: [{"time": s, "value": v, "shape": 0, "tension": 0}]. shape 0 linear, 1 square, 2 slow, 3 fast start, 4 fast end, 5 bezier.
        value units: Volume/Trim = linear gain, 1.0 = 0 dB, never dB (or give "db" instead of value). Pan -1 left to 1 right.
        Width -1 to 1. Mute 1 muted, 0 unmuted. FX parameter normalized 0-1, as in set_fx_parameter.
        Points REAPER stores differently are reported in errors.
        """
        try:
            if not isinstance(points, list) or not points:
                return {"success": False, "error": "points must be a non-empty list"}
            if len(points) > _MAX_POINTS:
                return {"success": False, "error": f"at most {_MAX_POINTS} points per call, got {len(points)}"}
            get_project()
            with undo_step("add_envelope_points"):
                env, created = _resolve(track_index, envelope, fx_index, param_index, create=True)
                activated = False
                if not env.active:
                    RPR.GetSetEnvelopeInfo_String(env.pointer, "ACTIVE", "1", True)
                    activated = True

                errors, wanted = [], []
                for i, point in enumerate(points):
                    try:
                        if not isinstance(point, dict):
                            raise ValueError("each point must be an object with time and value")
                        time = point.get("time")
                        if not isinstance(time, (int, float)) or isinstance(time, bool) or time < 0:
                            raise ValueError(f"time must be seconds, 0 or more, got {time!r}")
                        value = env.check(point)
                        shape = point.get("shape", 0)
                        if shape not in range(6):
                            raise ValueError(f"shape must be 0-5, got {shape!r}")
                        tension = point.get("tension", 0.0)
                        if not isinstance(tension, (int, float)) or not -1.0 <= tension <= 1.0:
                            raise ValueError(f"tension must be -1 to 1, got {tension!r}")
                    except ValueError as e:
                        errors.append({"point": i, "error": str(e)})
                        continue
                    stored = env.to_stored(value)
                    if not RPR.InsertEnvelopePoint(env.pointer, float(time), stored, shape, float(tension), False, True)[0]:
                        errors.append({"point": i, "error": "REAPER refused the point"})
                        continue
                    wanted.append((i, float(time), value))
                RPR.Envelope_SortPoints(env.pointer)

                # Read every point back from REAPER and match each request by time and value.
                stored_points = env.points()
                added = []
                for i, time, value in wanted:
                    near = [p for p in stored_points if abs(p[0] - time) <= _TIME_EPS]
                    best = min(near, key=lambda p: abs(env.from_stored(p[1]) - value), default=None)
                    if best is None:
                        errors.append({"point": i, "error": f"no point at {time}s after inserting"})
                        continue
                    got = env.describe(*best)
                    if env.kind == "gain":
                        same = abs(linear_to_db(env.from_stored(best[1])) - linear_to_db(value)) <= 0.01
                    else:
                        same = abs(env.from_stored(best[1]) - value) <= 1e-4
                    if not same:
                        errors.append({"point": i, "error": f"REAPER stored {got['display']} ({got['value']}), asked for {value}"})
                    added.append(got)

                result = {
                    "success": not errors,
                    "track_index": track_index,
                    "envelope": env.name,
                    "unit": env.unit,
                    "created": created,
                    "activated": activated,
                    "added": added,
                    "errors": errors,
                    "point_count": len(stored_points),
                }
                if errors:
                    result["error"] = f"{len(errors)} of {len(points)} points failed; see errors"
                return result
        except (LookupError, ValueError) as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"add_envelope_points failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo(own_step=True)
    def clear_envelope_points(
        track_index: int,
        start: float,
        end: float,
        envelope: str = "Volume",
        fx_index: int | None = None,
        param_index: int | None = None,
    ) -> dict:
        """Delete the automation points from start to end seconds, both ends included. To remove automation, clear it; do not write points over it."""
        try:
            if start < 0 or end < start:
                return {"success": False, "error": f"need 0 <= start <= end, got {start} and {end}"}
            get_project()
            with undo_step("clear_envelope_points"):
                env, _ = _resolve(track_index, envelope, fx_index, param_index, create=False)
                before = RPR.CountEnvelopePoints(env.pointer)
                # DeleteEnvelopePointRange keeps a point lying exactly on the end time.
                RPR.DeleteEnvelopePointRange(env.pointer, start - _TIME_EPS, end + _TIME_EPS)
                left = env.points(start, end)
                after = RPR.CountEnvelopePoints(env.pointer)
                if left:
                    return {
                        "success": False,
                        "error": f"{len(left)} points remain between {start}s and {end}s",
                        "removed": before - after,
                    }
                return {
                    "success": True,
                    "track_index": track_index,
                    "envelope": env.name,
                    "removed": before - after,
                    "point_count": after,
                }
        except (LookupError, ValueError) as e:
            return {"success": False, "error": str(e)}
        except Exception as e:
            logger.error(f"clear_envelope_points failed: {e}")
            return {"success": False, "error": str(e)}
