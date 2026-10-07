import logging

from reaper_mcp.connection import RPR, get_project, held, reapy, records_undo, undo_step

logger = logging.getLogger("reaper_mcp.fx_tools")


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
    @records_undo()
    def set_fx_parameter(
        track_index: int, fx_index: int, param_index: int, value: float
    ) -> dict:
        """
        Set a normalized parameter value on an FX plugin.
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
            RPR.TrackFX_SetParamNormalized(track.id, fx_index, param_index, value)
            applied = RPR.TrackFX_GetParamNormalized(track.id, fx_index, param_index)

            # REAPER returns -1 from the readback when it refused the write. Reporting
            # that as the applied value presented a failed write as a successful one.
            if applied < 0.0:
                return {
                    "success": False,
                    "error": (
                        f"REAPER refused the write to param {param_index} "
                        f"of fx {fx_index} on track {track_index}"
                    ),
                }

            return {
                "success": True,
                "track_index": track_index,
                "fx_index": fx_index,
                "param_index": param_index,
                "param_name": param_name,
                "value": applied,
                "requested": value,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def get_fx_parameters(track_index: int, fx_index: int) -> dict:
        """Get parameters for an FX plugin."""
        try:
            invalid = _negative_index(track_index=track_index, fx_index=fx_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            fx = track.fxs[fx_index]
            params = []
            for i in range(fx.n_params):
                # Use normalized and formatted properties to avoid exceptions from non-existent fields.
                param = fx.params[i]
                params.append({
                    "index": i,
                    "name": param.name,
                    "normalized_value": param.normalized,
                    "formatted_value": param.formatted,
                })
            return {
                "success": True,
                "track_index": track_index,
                "fx_index": fx_index,
                "fx_name": fx.name,
                "parameters": params,
            }
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
