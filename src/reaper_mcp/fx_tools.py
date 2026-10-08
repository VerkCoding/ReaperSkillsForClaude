import logging
import re
import time

from reaper_mcp.connection import RPR, UNDO_PREFIX, get_project, held, reapy, records_undo, undo_step

logger = logging.getLogger("reaper_mcp.fx_tools")

_NUMBER = re.compile(r"[-+]?\d*\.?\d+")

# How long a parameter write may take to show in the read-back. iZotope Ozone 12
# takes a host write in its audio callback, so the value moves on the next audio
# block, and never while REAPER's audio engine is closed; soothe2, Waves SSL G
# Channel, smart:chain and the stock plugins take it at once either way.
LATE_WRITE_WAIT_SEC = 1.0
_LATE_WRITE_POLL_SEC = 0.05
_SAME = 1e-6


def write_param(track_id, fx_index: int, param_index: int, value: float, tool: str) -> dict:
    """Write a normalised parameter and confirm it, recording one "MCP: <tool>" undo step.

    The write used to happen inside an undo block that held REAPER for one cycle, with
    the read-back in the same cycle. A plugin that takes writes in its audio callback,
    as Ozone 12 does, then reported the old value and, depending on timing,
    "unconfirmed" with no undo step. With REAPER in the background and "Close audio
    device when stopped and application is inactive" on, its audio engine is closed and
    such a write waited until the user next focused REAPER, then landed outside the undo
    history. Here a plugin that takes the write at once is read back and recorded in the
    same held cycle; otherwise the value is read again across cycles, and if it has not
    moved while the engine is closed, the engine is opened so the plugin can take the
    write, and closed again afterwards. The undo step is recorded once REAPER holds the
    new value, so undoing it restores the old one.

    Returns {"value", "before", "landed", "late_ms", "opened_audio"}, or {"error"} when
    REAPER refused the write. "landed" is False when the value never moved; the
    caller's records_undo then reports "unconfirmed".
    """
    started = time.monotonic()
    # Most plugins take the write at once, so read, write, check and record in one
    # held cycle; only a value that has not moved yet is followed across cycles.
    with held():
        before = RPR.TrackFX_GetParamNormalized(track_id, fx_index, param_index)
        # REAPER returns -1 from the readback when it refuses the target.
        if before < 0.0:
            return {"error": "refused"}
        if abs(before - value) <= _SAME:
            return {"value": before, "before": before, "landed": True, "late_ms": None,
                    "opened_audio": False, "unchanged": True}
        RPR.TrackFX_SetParamNormalized(track_id, fx_index, param_index, value)
        applied = RPR.TrackFX_GetParamNormalized(track_id, fx_index, param_index)
        if abs(applied - before) > _SAME and applied >= 0.0:
            # Calls from outside REAPER record no undo point of their own; this one
            # holds the state with the new value.
            RPR.Undo_OnStateChange2(0, UNDO_PREFIX + tool)
            return {"value": applied, "before": before, "landed": True, "late_ms": None,
                    "opened_audio": False}

    polls, opened_audio = 0, False
    try:
        while abs(applied - before) <= _SAME and time.monotonic() - started < LATE_WRITE_WAIT_SEC:
            if not opened_audio and polls >= 2 and not RPR.Audio_IsRunning():
                RPR.Audio_Init()
                opened_audio = True
            time.sleep(_LATE_WRITE_POLL_SEC)
            applied = RPR.TrackFX_GetParamNormalized(track_id, fx_index, param_index)
            polls += 1
        if applied < 0.0:
            return {"error": "refused"}

        landed = abs(applied - before) > _SAME
        if landed:
            # Calls from outside REAPER record no undo point of their own, so the write
            # above left the history alone; this step holds the state with the new value.
            with held():
                RPR.Undo_OnStateChange2(0, UNDO_PREFIX + tool)
    finally:
        # Put the engine back the way the user's preferences had it.
        if opened_audio:
            RPR.Audio_Quit()
    late_ms = round((time.monotonic() - started) * 1000) if polls and landed else None
    return {"value": applied, "before": before, "landed": landed, "late_ms": late_ms,
            "opened_audio": opened_audio}


def _param_reply(base: dict, written: dict, requested: float) -> dict:
    """Build a set_*_parameter reply from write_param's result."""
    reply = dict(base, success=True, value=written["value"], requested=requested)
    notes = []
    if written.get("opened_audio"):
        notes.append("REAPER's audio engine was closed (REAPER in the background with 'Close audio "
                     "device when stopped and application is inactive'), and this plugin takes host "
                     "writes only while audio runs, so the engine was opened for the write and "
                     "closed again.")
    if written["late_ms"] is not None:
        notes.append(f"The plugin applied the write {written['late_ms']} ms after it was sent; "
                     "the value shown is the one REAPER holds now.")
    if not written["landed"]:
        notes.append(f"The value stayed at {written['before']:.6f} for "
                     f"{LATE_WRITE_WAIT_SEC:.0f} s after the write: the plugin did not take it.")
    elif abs(written["value"] - requested) > 1e-4:
        notes.append(f"The plugin stored {written['value']:.6f}, not {requested}: "
                     "it snaps this parameter to its own steps.")
    if notes:
        reply["note"] = " ".join(notes)
    if written.get("unchanged"):
        # Said outright: records_undo infers it from the top of the undo history, which
        # an earlier step of the same name (after a redo, say) can satisfy.
        reply["unconfirmed"] = "The parameter already held this value, so nothing changed."
    return reply


def _steps(track_id, fx_index: int, param_index: int):
    """Return the number of steps of a stepped, non-toggle parameter, or None.

    The step size comes in the parameter's native units: a JS enum running 0-4 reports a
    step of 1, while a VST3 list reports 1/steps over its native 0-1. Dividing the native
    range by the step gives the count either way.
    """
    out = RPR.TrackFX_GetParameterStepSizes(track_id, fx_index, param_index, 0, 0, 0, False)
    if not out[0] or out[7] or out[4] <= 0:
        return None
    _, _, _, _, low, high = RPR.TrackFX_GetParam(track_id, fx_index, param_index, 0, 0)
    span = high - low
    if span <= 0:
        return None
    steps = round(span / out[4])
    if steps < 2 or abs(steps * out[4] - span) > 1e-3 * span:
        return None
    return steps


def step_probe(index: int, steps: int) -> float:
    """Return the normalized value at which every common mapping names entry `index`.

    Plugins turn a normalized value into a list entry three ways, measured across 17
    plugins: rounding (most VST3s), truncating (sonible smart:chain) and the VST3 SDK's
    floor(v * (steps + 1)) (Arturia). Just past index / steps all three agree; a quarter
    step past it the SDK mapping already names the next entry in the top quarter. The
    nudge clears float error in the stored value and stays below 1 / (steps * (steps + 1)).
    """
    nudge = min(1e-5, 0.5 / (steps * (steps + 1)))
    return min(1.0, index / steps + nudge)


def _step_info(track_id, fx_index: int, param_index: int, normalized: float, formatted: str) -> dict:
    """Return steps, step_index and, when it differs, step_label for a stepped list parameter.

    Some plugins truncate when they turn the stored value into a label, so a value stored
    a hair under an index is labelled with the entry below it: sonible smart:chain's
    Profile labelled "Vocals | High" as "Synth | Pad". The index from the number is the
    reliable one, and the plugin's own label for that index is read at step_probe.
    """
    steps = _steps(track_id, fx_index, param_index)
    if steps is None:
        return {}
    index = round(normalized * steps)
    info = {"steps": steps, "step_index": index}
    label = str(RPR.TrackFX_FormatParamValueNormalized(
        track_id, fx_index, param_index, step_probe(index, steps), "", 256)[5])
    if label and not _same_reading(label, formatted):
        info["step_label"] = label
    return info


def _leading_number(text: str):
    match = _NUMBER.search(text)
    return float(match.group()) if match else None


def _same_reading(a: str, b: str) -> bool:
    """True when two displays name the same setting.

    Labels are compared as text, and numbers as numbers: the probe sits a hair past the
    step, so a JS slider labels it "-11.999917" where the display reads "-12.0", and
    plugins add or drop units between the two paths ("450 Hz" against "450").
    """
    if a.strip() == b.strip():
        return True
    x, y = _leading_number(a), _leading_number(b)
    if x is None or y is None:
        return False
    return abs(x - y) <= max(1e-3, abs(y) * 1e-3)


def _fx_guid(track_id, fx_index: int) -> str:
    """Return an FX's GUID as text. TrackFX_GetFXGUID itself returns a GUID* pointer."""
    return str(RPR.guidToString(RPR.TrackFX_GetFXGUID(track_id, fx_index), "")[1])


def _pin_channels(track_id, fx_index: int, is_output: int, pin: int) -> list:
    """Return the 1-based track channels wired to one pin.

    TrackFX_GetPinMappings returns a bitmask of channels 1-32 and fills high32
    with channels 33-64; bit n set means channel n + 1.
    """
    out = RPR.TrackFX_GetPinMappings(track_id, fx_index, is_output, pin, 0)
    low, high = int(out[0]) & 0xFFFFFFFF, int(out[5]) & 0xFFFFFFFF
    return [bit + 1 for bit in range(32) if low >> bit & 1] + [bit + 33 for bit in range(32) if high >> bit & 1]


def _negative_index(**values) -> str:
    """Return a message naming the first negative index, or "".

    Reapy resolves a negative index the Python way, to the last element, while the
    ReaScript calls in this module reject it and do nothing. Tools that read a name
    through reapy and then act through ReaScript reported the last plugin's name
    while changing nothing at all.
    """
    for name, value in values.items():
        if value < 0:
            return "%s must be 0 or greater, got %s" % (name, value)
    return ""


def register_tools(mcp):

    @mcp.tool()
    @records_undo()
    def add_fx(track_index: int, fx_name: str) -> dict:
        """
        Add an FX plugin to a track.
        """
        try:
            invalid = _negative_index(track_index=track_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            # reapy returns an FX object instead of the index and raises ValueError if the name is not found.
            # We catch ValueError to handle missing plugins instead of checking for -1.
            try:
                fx = track.add_fx(fx_name)
            except ValueError:
                return {
                    "success": False,
                    "error": f"Plugin not found: '{fx_name}'.",
                }
            return {
                "success": True,
                "fx_index": fx.index,
                "name": fx.name,
                "n_params": fx.n_params,
                "track_index": track_index,
            }
        except Exception as e:
            logger.error(f"add_fx failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo()
    def remove_fx(track_index: int, fx_index: int) -> dict:
        """Remove an FX plugin from a track by its index."""
        try:
            invalid = _negative_index(track_index=track_index, fx_index=fx_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            fx_name = track.fxs[fx_index].name

            # TrackFX_Delete reports whether the plugin was actually removed. Without
            # this check a refused delete still returned the name as though it had been.
            if not RPR.TrackFX_Delete(track.id, fx_index):
                return {
                    "success": False,
                    "error": f"REAPER did not remove fx {fx_index} from track {track_index}",
                }
            return {"success": True, "track_index": track_index, "removed": fx_name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo(own_step=True)
    def set_fx_parameter(
        track_index: int, fx_index: int, param_index: int, value: float
    ) -> dict:
        """
        Set a normalized parameter value on an FX plugin.

        Some plugins (iZotope Ozone 12) take writes only in their audio callback, so the value is
        followed across REAPER cycles, and the audio engine is opened for the write when REAPER has
        closed it in the background; "value" is what REAPER holds once the write has landed.
        """
        try:
            if not 0.0 <= value <= 1.0:
                return {"success": False, "error": f"value must be 0.0-1.0, got {value}"}
            invalid = _negative_index(
                track_index=track_index, fx_index=fx_index, param_index=param_index
            )
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            fx = track.fxs[fx_index]
            param_name = fx.params[param_index].name

            # ReaScript is used directly because reapy's FXParam attribute assignment does not persist to REAPER.
            written = write_param(track.id, fx_index, param_index, value, "set_fx_parameter")
            if "error" in written:
                return {
                    "success": False,
                    "error": (
                        f"REAPER refused the write to param {param_index} "
                        f"of fx {fx_index} on track {track_index}"
                    ),
                }
            return _param_reply({
                "track_index": track_index,
                "fx_index": fx_index,
                "param_index": param_index,
                "param_name": param_name,
            }, written, value)
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def get_fx_parameters(
        track_index: int,
        fx_index: int,
        name_contains: str = "",
        start: int = 0,
        max_params: int = 200,
    ) -> dict:
        """Get parameters for an FX plugin: name, normalized and formatted value.

        name_contains keeps parameters whose name contains it (case-insensitive); start and
        max_params page through long lists (Ozone 12 has 876, Pro-Q 4 740).
        A stepped list parameter also gets steps and step_index = round(normalized * steps).
        step_label appears when the plugin's label for that index differs from formatted_value:
        some plugins truncate when labelling, so formatted_value can name the entry below the real one.
        """
        try:
            invalid = _negative_index(track_index=track_index, fx_index=fx_index, start=start)
            if invalid:
                return {"success": False, "error": invalid}
            if max_params < 1:
                return {"success": False, "error": f"max_params must be 1 or more, got {max_params}"}
            project = get_project()
            track = project.tracks[track_index]
            fx = track.fxs[fx_index]
            fx_name = fx.name
            wanted = name_contains.strip().lower()
            params, matched = [], 0
            # One held block: about 900 reads of a large plugin take a fraction of a
            # second instead of one REAPER cycle each.
            with held():
                n_params = int(RPR.TrackFX_GetNumParams(track.id, fx_index))
                for i in range(n_params):
                    name = str(RPR.TrackFX_GetParamName(track.id, fx_index, i, "", 256)[4])
                    if wanted and wanted not in name.lower():
                        continue
                    matched += 1
                    if matched <= start or len(params) >= max_params:
                        continue
                    normalized = RPR.TrackFX_GetParamNormalized(track.id, fx_index, i)
                    formatted = str(RPR.TrackFX_GetFormattedParamValue(track.id, fx_index, i, "", 256)[4])
                    entry = {
                        "index": i,
                        "name": name,
                        "normalized_value": normalized,
                        "formatted_value": formatted,
                    }
                    entry.update(_step_info(track.id, fx_index, i, normalized, formatted))
                    params.append(entry)
            result = {
                "success": True,
                "track_index": track_index,
                "fx_index": fx_index,
                "fx_name": fx_name,
                "n_params": n_params,
                "matched": matched,
                "parameters": params,
            }
            if start + len(params) < matched:
                result["truncated"] = True
                result["next_start"] = start + len(params)
            if any("step_label" in p for p in params):
                result["note"] = ("step_label differs from formatted_value: the plugin labels the stored "
                                  "value as the neighbouring entry. step_index and step_label are the real setting.")
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def list_track_fx(track_index: int) -> dict:
        """List FX plugins on a track."""
        try:
            invalid = _negative_index(track_index=track_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            fx_list = []
            for i in range(track.n_fxs):
                fx = track.fxs[i]
                fx_list.append({
                    "index": i,
                    "name": fx.name,
                    "enabled": fx.is_enabled,
                    "n_params": fx.n_params,
                })
            return {"success": True, "track_index": track_index, "fx": fx_list}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo()
    def bypass_fx(track_index: int, fx_index: int, bypassed: bool) -> dict:
        """Enable or disable an FX plugin on a track."""
        try:
            invalid = _negative_index(track_index=track_index, fx_index=fx_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            fx = track.fxs[fx_index]
            fx.is_enabled = not bypassed
            return {
                "success": True,
                "track_index": track_index,
                "fx_index": fx_index,
                "fx_name": fx.name,
                "bypassed": bypassed,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo()
    def load_fx_preset(track_index: int, fx_index: int, preset_name: str) -> dict:
        """Load a saved preset by name for an FX plugin."""
        try:
            invalid = _negative_index(track_index=track_index, fx_index=fx_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            fx = track.fxs[fx_index]

            # Use TrackFX_SetPreset because attribute assignment on fx.preset_name does not validate existence.
            # Reading the preset name back verifies whether the requested preset was loaded.
            RPR.TrackFX_SetPreset(track.id, fx_index, preset_name)
            loaded = RPR.TrackFX_GetPreset(track.id, fx_index, "", 256)[3]

            if str(loaded).strip().lower() != preset_name.strip().lower():
                return {
                    "success": False,
                    "error": f"Preset '{preset_name}' not found for {fx.name}. Current preset is '{loaded}'.",
                    "preset": loaded,
                }
            return {
                "success": True,
                "track_index": track_index,
                "fx_index": fx_index,
                "fx_name": fx.name,
                "preset": loaded,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def get_fx_pins(track_index: int, fx_index: int) -> dict:
        """Read the track channels wired to each FX input and output pin, with pin names.

        Verifies a sidechain: ReaComp's "Auxiliary Input L/R" pins should read channels [3] and [4].
        """
        try:
            invalid = _negative_index(track_index=track_index, fx_index=fx_index)
            if invalid:
                return {"success": False, "error": invalid}
            get_project()
            with held():
                n_tracks = RPR.CountTracks(0)
                if track_index >= n_tracks:
                    return {"success": False, "error": f"track_index {track_index} out of range, project has {n_tracks} tracks"}
                track = RPR.GetTrack(0, track_index)
                n_fx = RPR.TrackFX_GetCount(track)
                if fx_index >= n_fx:
                    return {"success": False, "error": f"fx_index {fx_index} out of range, track has {n_fx} FX"}
                _, _, _, n_in, n_out = RPR.TrackFX_GetIOSize(track, fx_index, 0, 0)
                pins = {}
                for kind, count, prefix in (("inputs", n_in, "in_pin_"), ("outputs", n_out, "out_pin_")):
                    pins[kind] = [
                        {
                            "pin": pin,
                            "name": RPR.TrackFX_GetNamedConfigParm(track, fx_index, f"{prefix}{pin}", "", 256)[4],
                            "channels": _pin_channels(track, fx_index, int(kind == "outputs"), pin),
                        }
                        for pin in range(max(count, 0))
                    ]
                return {
                    "success": True,
                    "track_index": track_index,
                    "fx_index": fx_index,
                    "fx_name": RPR.TrackFX_GetFXName(track, fx_index, "", 256)[3],
                    "track_channels": int(RPR.GetMediaTrackInfo_Value(track, "I_NCHAN")),
                    **pins,
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo(own_step=True)
    def move_fx(track_index: int, fx_index: int, to_index: int) -> dict:
        """Move an FX to another slot in the same track's chain; to_index is the slot it ends up in. Returns the new chain order."""
        try:
            invalid = _negative_index(track_index=track_index, fx_index=fx_index, to_index=to_index)
            if invalid:
                return {"success": False, "error": invalid}
            get_project()
            with undo_step("move_fx"):
                n_tracks = RPR.CountTracks(0)
                if track_index >= n_tracks:
                    return {"success": False, "error": f"track_index {track_index} out of range, project has {n_tracks} tracks"}
                track = RPR.GetTrack(0, track_index)
                n_fx = RPR.TrackFX_GetCount(track)
                # An out-of-range destination moves the FX to the end without complaint.
                if fx_index >= n_fx or to_index >= n_fx:
                    return {"success": False, "error": f"track {track_index} has {n_fx} FX, slots 0-{n_fx - 1}"}
                before = [_fx_guid(track, i) for i in range(n_fx)]
                expected = before[:fx_index] + before[fx_index + 1:]
                expected.insert(to_index, before[fx_index])
                RPR.TrackFX_CopyToTrack(track, fx_index, track, to_index, True)
                after = [_fx_guid(track, i) for i in range(RPR.TrackFX_GetCount(track))]
                chain = [
                    {"index": i, "name": RPR.TrackFX_GetFXName(track, i, "", 256)[3]}
                    for i in range(len(after))
                ]
            if after != expected:
                return {"success": False, "error": "REAPER's chain order is not the one asked for", "fx": chain}
            return {"success": True, "track_index": track_index, "moved_to": to_index, "fx": chain}
        except Exception as e:
            return {"success": False, "error": str(e)}
