import logging

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project

logger = logging.getLogger("reaper_mcp.fx_tools")


def register_tools(mcp):

    @mcp.tool()
    def add_fx(track_index: int, fx_name: str) -> dict:
        """
        Add an FX plugin to a track.
        """
        try:
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
    def remove_fx(track_index: int, fx_index: int) -> dict:
        """Remove an FX plugin from a track by its index."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            fx_name = track.fxs[fx_index].name
            RPR.TrackFX_Delete(track.id, fx_index)
            return {"success": True, "track_index": track_index, "removed": fx_name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_fx_parameter(
        track_index: int, fx_index: int, param_index: int, value: float
    ) -> dict:
        """
        Set a normalized parameter value on an FX plugin.
        """
        try:
            if not 0.0 <= value <= 1.0:
                return {"success": False, "error": f"value must be 0.0-1.0, got {value}"}
            project = get_project()
            track = project.tracks[track_index]
            fx = track.fxs[fx_index]
            param_name = fx.params[param_index].name

            # ReaScript is used directly because reapy's FXParam attribute assignment does not persist to REAPER.
            RPR.TrackFX_SetParamNormalized(track.id, fx_index, param_index, value)
            applied = RPR.TrackFX_GetParamNormalized(track.id, fx_index, param_index)

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
    def bypass_fx(track_index: int, fx_index: int, bypassed: bool) -> dict:
        """Enable or disable an FX plugin on a track."""
        try:
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
    def load_fx_preset(track_index: int, fx_index: int, preset_name: str) -> dict:
        """Load a saved preset by name for an FX plugin."""
        try:
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
