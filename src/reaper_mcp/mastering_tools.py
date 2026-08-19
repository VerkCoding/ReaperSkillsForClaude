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


def _negative_index(**values) -> str:
    """Return a message naming the first negative index, or "".

    The ReaScript parameter calls reject a negative index and do nothing, while the
    reapy lookup beside them resolves it to the last element. The pair reported a
    plugin name together with a write that never happened.
    """
    for name, value in values.items():
        if value < 0:
            return "%s must be 0 or greater, got %s" % (name, value)
    return ""


def _nothing_to_measure():
    """Return an error dict when the project holds no audio, otherwise None."""
    if RPR.GetProjectLength(0) <= 0:
        return {
            "success": False,
            "error": "The project is empty. There is nothing to render or measure.",
        }
    return None


def _param_index(track_id, fx_index: int, param_name: str):
    """Return the index of a named plugin parameter, or None."""
    wanted = param_name.strip().lower()
    for p in range(int(RPR.TrackFX_GetNumParams(track_id, fx_index))):
        name = RPR.TrackFX_GetParamName(track_id, fx_index, p, "", 256)[4]
        if str(name).strip().lower() == wanted:
            return p
    return None


def _displayed_number(track_id, fx_index: int, param_index: int) -> float:
    """Return the number a plugin currently displays for one parameter.

    ReaLimit shows a threshold as "-3.01" and a release as "15.0", and reports an
    unreachable release as "inf".
    """
    text = str(RPR.TrackFX_GetFormattedParamValue(track_id, fx_index, param_index, "", 256)[4]).strip()
    lowered = text.lower().lstrip("+")
    if lowered.startswith("inf"):
        return float("inf")
    if lowered.startswith("-inf"):
        return float("-inf")
    digits = ""
    for character in text:
        if character.isdigit() or character in "+-.":
            digits += character
        elif digits:
            break
    try:
        return float(digits)
    except ValueError:
        return float("nan")


def _set_param_to_display(track_id, fx_index: int, param_name: str, target: float,
                          iterations: int = 24):
    """Set a parameter to a value expressed in the units the plugin displays.

    Plugins accept only a normalised 0-1 value, and each maps that range its own way:
    ReaLimit's threshold runs linearly from -60 to +12 dB while its release runs
    backwards from inf down to 6 ms. Both report a native range of 0-1, so writing a
    decibel or millisecond figure straight into the parameter simply clamped to an
    end of the range. reapy's binding exposes no TrackFX_SetParamFromString, so the
    plugin's own mapping is inverted by bisecting on the value it displays.

    Returns the value the plugin ended up displaying, which is the achievable value
    nearest the request, or None when there is no parameter of that name.
    """
    p = _param_index(track_id, fx_index, param_name)
    if p is None:
        return None

    RPR.TrackFX_SetParamNormalized(track_id, fx_index, p, 0.0)
    at_low = _displayed_number(track_id, fx_index, p)
    RPR.TrackFX_SetParamNormalized(track_id, fx_index, p, 1.0)
    at_high = _displayed_number(track_id, fx_index, p)
    increasing = at_high > at_low

    tolerance = max(0.01, abs(target) * 0.001)
    low, high = 0.0, 1.0
    for _ in range(iterations):
        middle = (low + high) / 2.0
        RPR.TrackFX_SetParamNormalized(track_id, fx_index, p, middle)
        shown = _displayed_number(track_id, fx_index, p)
        if abs(shown - target) <= tolerance:
            return float(shown)
        if (shown < target) == increasing:
            low = middle
        else:
            high = middle

    RPR.TrackFX_SetParamNormalized(track_id, fx_index, p, (low + high) / 2.0)
    return float(_displayed_number(track_id, fx_index, p))


def _true_peak_db(data, sample_rate: int) -> float:
    """Return the true peak in dBTP, measured on a four times oversampled signal.

    BS.1770 measures the peak of the reconstructed waveform, which rises between
    samples: a render whose sample peak reads -0.00 dBFS measured +0.05 dBTP once
    oversampled. Reporting the sample peak under a dBTP label understates exactly the
    overshoot the measurement exists to catch. Falls back to the sample peak when
    scipy is unavailable.
    """
    import numpy as np

    peak = float(np.max(np.abs(data)))
    try:
        from scipy.signal import resample_poly

        oversampled = resample_poly(data, 4, 1, axis=0)
        peak = max(peak, float(np.max(np.abs(oversampled))))
    except Exception as e:
        logger.warning(f"true peak falling back to sample peak: {e}")
    return float(20 * np.log10(peak)) if peak > 0 else -120.0


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
            invalid = _negative_index(fx_index=fx_index, param_index=param_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            master = project.master_track
            fx = master.fxs[fx_index]
            param_name = fx.params[param_index].name

            # Direct attribute assignment targets a throwaway float subclass.
            # RPR.TrackFX_SetParamNormalized ensures the assignment is passed to the REAPER API.
            RPR.TrackFX_SetParamNormalized(master.id, fx_index, param_index, value)
            applied = RPR.TrackFX_GetParamNormalized(master.id, fx_index, param_index)

            # REAPER returns -1 from the readback when it refused the write, which was
            # reported as though it were the value now in effect.
            if applied < 0.0:
                return {
                    "success": False,
                    "error": (
                        f"REAPER refused the write to param {param_index} "
                        f"of master fx {fx_index}"
                    ),
                }

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
                # The plugins added before the missing one are removed again. Leaving
                # them behind returned a failure while the master track kept half a
                # mastering chain.
                for entry in reversed(added):
                    RPR.TrackFX_Delete(master.id, entry["fx_index"])
                return {
                    "success": False,
                    "error": f"Plugins not installed: {', '.join(missing)}",
                    "preset": preset,
                    "fx_chain": [],
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

            # Both arguments were previously accepted and discarded: the plugin was
            # added at its defaults and the caller was told to set it up themselves.
            applied_threshold = _set_param_to_display(master.id, fx.index, "Threshold", threshold_db)
            applied_release = _set_param_to_display(master.id, fx.index, "Release", release_ms)
            unset = [
                name for name, applied in
                (("Threshold", applied_threshold), ("Release", applied_release))
                if applied is None
            ]

            return {
                "success": True,
                "fx_index": fx.index,
                "name": fx.name,
                "threshold_db": applied_threshold,
                "release_ms": applied_release,
                "requested": {"threshold_db": threshold_db, "release_ms": release_ms},
                "note": (
                    f"This build of ReaLimit exposes no {', '.join(unset)} parameter; "
                    "set it with set_master_fx_parameter."
                ) if unset else None,
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

            empty = _nothing_to_measure()
            if empty:
                return empty

            tmp = render_to_temp_file()
            try:
                data, rate = sf.read(tmp)
                meter = pyln.Meter(rate)
                integrated = float(meter.integrated_loudness(data))
                peak_linear = float(np.max(np.abs(data)))
                sample_peak_db = float(20 * np.log10(peak_linear)) if peak_linear > 0 else -120.0
                return {
                    "success": True,
                    "integrated_lufs": round(integrated, 1),
                    # Two decimals: an overshoot of +0.04 dBTP rounds away at one, and
                    # whether a master sits above or below full scale is the point of
                    # the measurement.
                    "true_peak_dbtp": round(_true_peak_db(data, rate), 2),
                    "sample_peak_dbfs": round(sample_peak_db, 2),
                    "sample_rate": int(rate),
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

            empty = _nothing_to_measure()
            if empty:
                return empty

            tmp = render_to_temp_file()
            try:
                data, rate = sf.read(tmp)
                meter = pyln.Meter(rate)
                current_lufs = float(meter.integrated_loudness(data))
                current_peak_db = _true_peak_db(data, rate)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

            if current_lufs == float("-inf"):
                return {"success": False, "error": "Project audio is silent."}

            gain_db = target_lufs - current_lufs
            project = get_project()
            master = project.master_track
            new_vol_db = set_volume_db(master, get_volume_db(master) + gain_db)

            # The same gain moves the peak. A target that pushes it past full scale is
            # reported rather than left for the next render to reveal.
            projected_peak_db = current_peak_db + gain_db

            return {
                "success": True,
                "original_lufs": round(current_lufs, 1),
                "target_lufs": target_lufs,
                "gain_applied_db": round(gain_db, 1),
                "new_master_volume_db": round(float(new_vol_db), 1),
                "projected_true_peak_dbtp": round(projected_peak_db, 2),
                "warning": (
                    f"The projected true peak is {projected_peak_db:.2f} dBTP, above full "
                    "scale. Add a limiter or choose a lower target."
                ) if projected_peak_db > 0 else None,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
