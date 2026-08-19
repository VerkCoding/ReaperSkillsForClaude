import os
import logging

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project
from reaper_mcp.units import get_volume_db, set_volume_db

logger = logging.getLogger("reaper_mcp.mastering_tools")

MASTERING_PRESETS = {
    "default": ["ReaEQ", "ReaComp", "ReaLimit"],
    "loud":    ["ReaEQ", "ReaComp", "ReaComp", "ReaLimit"],
    "gentle":  ["ReaEQ", "ReaComp", "ReaLimit"],
}


def _add_fx(track, fx_name: str):
    """Add a plugin and return its FX object, or None if there is no such plugin.

    reapy 0.10 raises ValueError for unknown names rather than returning -1.
    Catching ValueError prevents TypeError crashes in calling code.
    """
    try:
        return track.add_fx(fx_name)
    except ValueError:
        return None


def register_tools(mcp):

    @mcp.tool()
    def add_master_fx(fx_name: str) -> dict:
        """Add an FX plugin to the master track."""
        try:
            project = get_project()
            master = project.master_track
            fx = _add_fx(master, fx_name)
            if fx is None:
                return {"success": False, "error": f"Plugin not found: '{fx_name}'"}
            return {"success": True, "fx_index": fx.index, "name": fx.name, "n_params": fx.n_params}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def list_master_fx() -> dict:
        """List all FX plugins on the master track."""
        try:
            project = get_project()
            master = project.master_track
            fx_list = []
            for i in range(master.n_fxs):
                fx = master.fxs[i]
                fx_list.append({"index": i, "name": fx.name, "enabled": fx.is_enabled, "n_params": fx.n_params})
            return {"success": True, "fx": fx_list}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_master_fx_parameter(fx_index: int, param_index: int, value: float) -> dict:
        """Set a normalized parameter (0.0-1.0) on a master track FX plugin."""
        try:
            if not 0.0 <= value <= 1.0:
                return {"success": False, "error": f"Value must be 0.0-1.0, got {value}"}
            project = get_project()
            master = project.master_track
            fx = master.fxs[fx_index]
            param_name = fx.params[param_index].name

            # Direct attribute assignment targets a throwaway float subclass.
            # RPR.TrackFX_SetParamNormalized ensures the assignment is passed to the REAPER API.
            RPR.TrackFX_SetParamNormalized(master.id, fx_index, param_index, value)
            applied = RPR.TrackFX_GetParamNormalized(master.id, fx_index, param_index)

            return {
                "success": True,
                "fx_index": fx_index,
                "param_index": param_index,
                "param_name": param_name,
                "value": applied,
                "requested": value,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_master_volume(volume_db: float) -> dict:
        """Set the master track output volume in dB."""
        try:
            project = get_project()
            master = project.master_track
            return {"success": True, "volume_db": set_volume_db(master, volume_db)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def apply_mastering_chain(preset: str = "default") -> dict:
        """Add a predefined FX chain to the master track."""
        try:
            if preset not in MASTERING_PRESETS:
                return {
                    "success": False,
                    "error": f"Unknown preset '{preset}'. Available: {list(MASTERING_PRESETS.keys())}",
                }
            project = get_project()
            master = project.master_track
            added, missing = [], []
            for fx_name in MASTERING_PRESETS[preset]:
                fx = _add_fx(master, fx_name)
                if fx is None:
                    missing.append(fx_name)
                else:
                    added.append({"fx_index": fx.index, "name": fx.name})
            if missing:
                return {
                    "success": False,
                    "error": f"Plugins not installed: {', '.join(missing)}",
                    "preset": preset,
                    "fx_chain": added,
                }
            return {"success": True, "preset": preset, "fx_chain": added}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def apply_limiter(threshold_db: float = -0.5, release_ms: float = 50.0) -> dict:
        """Add ReaLimit to the master track."""
        try:
            project = get_project()
            master = project.master_track
            fx = _add_fx(master, "ReaLimit")
            if fx is None:
                return {"success": False, "error": "ReaLimit plugin not found."}
            return {
                "success": True,
                "fx_index": fx.index,
                "name": fx.name,
                "instruction": f"ReaLimit added at index {fx.index}. Retrieve parameter indices via get_fx_parameters. Set parameters via set_master_fx_parameter.",
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def analyze_loudness() -> dict:
        """Render the project to a temporary file and measure integrated loudness (LUFS) and true peak (dBTP) based on ITU-R BS.1770."""
        try:
            import soundfile as sf
            import pyloudnorm as pyln
            import numpy as np
            from reaper_mcp.render_tools import render_to_temp_file

            tmp = render_to_temp_file()
            try:
                data, rate = sf.read(tmp)
                meter = pyln.Meter(rate)
                integrated = meter.integrated_loudness(data)
                peak_linear = float(np.max(np.abs(data)))
                peak_db = float(20 * np.log10(peak_linear)) if peak_linear > 0 else -120.0
                return {
                    "success": True,
                    "integrated_lufs": round(integrated, 1),
                    "true_peak_dbtp": round(peak_db, 1),
                    "sample_rate": rate,
                }
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)
        except Exception as e:
            logger.error(f"analyze_loudness error: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def normalize_project(target_lufs: float = -14.0) -> dict:
        """Measure the project integrated loudness and adjust the master volume to achieve the target LUFS."""
        try:
            import soundfile as sf
            import pyloudnorm as pyln
            from reaper_mcp.render_tools import render_to_temp_file

            tmp = render_to_temp_file()
            try:
                data, rate = sf.read(tmp)
                meter = pyln.Meter(rate)
                current_lufs = meter.integrated_loudness(data)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            if current_lufs == float("-inf"):
                return {"success": False, "error": "Project audio is silent."}

            gain_db = target_lufs - current_lufs
            project = get_project()
            master = project.master_track
            new_vol_db = set_volume_db(master, get_volume_db(master) + gain_db)

            return {
                "success": True,
                "original_lufs": round(current_lufs, 1),
                "target_lufs": target_lufs,
                "gain_applied_db": round(gain_db, 1),
                "new_master_volume_db": round(new_vol_db, 1),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
