import os
import time
import logging
from pathlib import Path

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project

logger = logging.getLogger("reaper_mcp.project_tools")


def _read_time_signature() -> tuple:
    """Read the project time signature at the start of the timeline.

    Avoids reapy.Project.time_signature because it returns tempo and numerator (bpm, bpi) rather than numerator and denominator, and is read-only. TimeMap_GetTimeSigAtTime is used to access the correct parameters.
    """
    out = RPR.TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)
    return int(out[2]), int(out[3])


def _refuse_if_dirty(what: str) -> dict:
    """Prevent execution of commands that trigger a modal save prompt.

    Opening or replacing a dirty project triggers a blocking UI prompt in REAPER. This prompt halts deferred script execution and stops the reapy server from responding, causing tool calls to hang. Aborting execution prevents connection deadlock.
    """
    if not RPR.IsProjectDirty(0):
        return {}
    return {
        "success": False,
        "error": (
            f"The open project has unsaved changes. Executing {what} triggers a modal prompt. "
            "Tool execution is suspended until the prompt is resolved. "
            "Save the project or discard changes before proceeding."
        ),
    }


def _marker_at_zero() -> int:
    """Return the index of the tempo/time-signature marker at the start position, or -1.

    The return value is formatted to be compatible with SetTempoTimeSigMarker, which accepts -1 to indicate insertion of a new marker.
    """
    for i in range(RPR.CountTempoTimeSigMarkers(0)):
        marker = RPR.GetTempoTimeSigMarker(0, i, 0, 0, 0, 0, 0, 0, 0)
        if abs(marker[3]) < 1e-9:  # timepos
            return i
    return -1


def _write_tempo(bpm: float) -> float:
    """Set the project tempo.

    Modifies the tempo marker at position 0 if it exists. Reapy's Project.bpm assignment fails to update existing tempo markers and creates duplicate markers instead. This ensures consistency between tempo and time signature modifications.
    """
    existing = _marker_at_zero()
    if existing < 0:
        get_project().bpm = bpm
    else:
        marker = RPR.GetTempoTimeSigMarker(0, existing, 0, 0, 0, 0, 0, 0, 0)
        RPR.SetTempoTimeSigMarker(
            0, existing, 0.0, -1, -1, bpm, marker[7], marker[8], False
        )
        RPR.UpdateTimeline()
    return get_project().bpm


def _write_time_signature(numerator: int, denominator: int) -> tuple:
    """Set the time signature at the start of the project.

    Uses SetTempoTimeSigMarker because REAPER stores time signatures in tempo markers. The existing tempo is passed back into the function to prevent unintended tempo modification.
    """
    project = get_project()
    RPR.SetTempoTimeSigMarker(
        0, _marker_at_zero(), 0.0, -1, -1, project.bpm, numerator, denominator, False
    )
    RPR.UpdateTimeline()
    return _read_time_signature()


def register_tools(mcp):

    @mcp.tool()
    def create_project(tempo: float = 120.0, time_signature: str = "4/4", name: str = "") -> dict:
        """Create a new REAPER project with the given tempo and time signature."""
        try:
            refusal = _refuse_if_dirty("starting a new project")
            if refusal:
                return refusal
            RPR.Main_OnCommand(41929, 0)  # File: New project
            project = get_project()
            _write_tempo(tempo)
            if time_signature:
                num, denom = map(int, time_signature.split("/"))
                _write_time_signature(num, denom)
            num, denom = _read_time_signature()
            return {
                "success": True,
                "name": name or f"New Project {time.strftime('%Y-%m-%d %H-%M-%S')}",
                "tempo": project.bpm,
                "time_signature": f"{num}/{denom}",
            }
        except Exception as e:
            logger.error(f"create_project failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def save_project(project_path: str = "") -> dict:
        """Save the current project. If no path is given, saves to ~/Documents/REAPER Projects."""
        try:
            project = get_project()
            if not project_path:
                proj_name = project.name or f"Project {time.strftime('%Y-%m-%d %H-%M-%S')}"
                default_dir = Path.home() / "Documents" / "REAPER Projects"
                os.makedirs(default_dir, exist_ok=True)
                project_path = str(default_dir / f"{proj_name}.rpp")
            project_path = str(Path(project_path).expanduser().resolve())
            os.makedirs(os.path.dirname(project_path), exist_ok=True)

            # reapy's Project.save only accepts a boolean force_save_as parameter.
            # Main_SaveProjectEx is used to specify a file path.
            RPR.Main_SaveProjectEx(0, project_path, 0)

            if not os.path.isfile(project_path):
                return {
                    "success": False,
                    "error": f"REAPER reported no error but {project_path} was not written",
                }

            # Main_SaveProjectEx leaves the project dirty flag set.
            # Main_SaveProject clears the dirty flag to prevent unexpected save prompts.
            if RPR.IsProjectDirty(0):
                RPR.Main_SaveProject(0, False)

            return {
                "success": True,
                "project_path": project_path,
                "size_bytes": os.path.getsize(project_path),
                "unsaved_changes": bool(RPR.IsProjectDirty(0)),
            }
        except Exception as e:
            logger.error(f"save_project failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def load_project(project_path: str) -> dict:
        """Load a REAPER project (.rpp) from the given file path."""
        try:
            if not os.path.exists(project_path):
                return {"success": False, "error": f"File not found: {project_path}"}
            refusal = _refuse_if_dirty("opening another project")
            if refusal:
                return refusal
            RPR.Main_openProject(project_path)
            project = get_project()
            num, denom = _read_time_signature()
            return {
                "success": True,
                "name": project.name,
                "tempo": project.bpm,
                "time_signature": f"{num}/{denom}",
                "project_path": project_path,
            }
        except Exception as e:
            logger.error(f"load_project failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def get_project_info() -> dict:
        """Get information about the current project: name, path, tempo, tracks, length."""
        try:
            project = get_project()
            markers = []
            try:
                for i in range(project.n_markers):
                    m = project.markers[i]
                    markers.append({"index": i, "name": m.name, "position": m.position})
            except Exception:
                pass

            regions = []
            try:
                for i in range(project.n_regions):
                    r = project.regions[i]
                    regions.append({"index": i, "name": r.name, "start": r.start, "end": r.end})
            except Exception:
                pass

            num, denom = _read_time_signature()
            return {
                "success": True,
                "name": project.name,
                "path": project.path,
                "tempo": project.bpm,
                "time_signature": f"{num}/{denom}",
                "length": project.length,
                "track_count": project.n_tracks,
                "markers": markers,
                "regions": regions,
            }
        except Exception as e:
            logger.error(f"get_project_info failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_tempo(bpm: float) -> dict:
        """Set the project tempo in BPM."""
        try:
            if bpm <= 0:
                return {"success": False, "error": f"bpm must be positive, got {bpm}"}
            return {"success": True, "tempo": _write_tempo(bpm)}
        except Exception as e:
            logger.error(f"set_tempo failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_time_signature(numerator: int, denominator: int) -> dict:
        """Set the project time signature."""
        try:
            if numerator < 1 or denominator < 1:
                return {"success": False, "error": "numerator and denominator must be positive"}
            num, denom = _write_time_signature(numerator, denominator)
            return {"success": True, "time_signature": f"{num}/{denom}"}
        except Exception as e:
            logger.error(f"set_time_signature failed: {e}")
            return {"success": False, "error": str(e)}
