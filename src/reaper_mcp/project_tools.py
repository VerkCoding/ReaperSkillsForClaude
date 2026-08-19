import os
import time
import logging
from pathlib import Path

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project
from reaper_mcp.units import project_tempo

logger = logging.getLogger("reaper_mcp.project_tools")


def _read_time_signature() -> tuple:
    """Read the project time signature at the start of the timeline.

    Avoids reapy.Project.time_signature because it returns tempo and numerator (bpm, bpi) rather than numerator and denominator, and is read-only. TimeMap_GetTimeSigAtTime is used to access the correct parameters.
    """
    out = RPR.TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)
    return int(out[2]), int(out[3])


def _project_file() -> str:
    """Return the path the open project is bound to, or "" when it is untitled.

    Reapy's Project.path returns the recording directory rather than the project
    file. EnumProjects with an index of -1 addresses the active project and returns
    its filename.
    """
    return RPR.EnumProjects(-1, "", 4096)[2]


def _same_file(a: str, b: str) -> bool:
    """Compare two project paths using platform casing rules."""
    if not a or not b:
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(os.path.normpath(b))


def _mark_dirty() -> None:
    """Flag the project as modified.

    Tempo and time signature writes do not set REAPER's dirty flag on their own.
    Without this, _refuse_if_dirty reads the project as clean, and a subsequent new
    or open command discards the change with no prompt and no warning.
    """
    RPR.MarkProjectDirty(0)


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
    _mark_dirty()
    return project_tempo()


def _write_time_signature(numerator: int, denominator: int) -> tuple:
    """Set the time signature at the start of the project.

    Uses SetTempoTimeSigMarker because REAPER stores time signatures in tempo markers. The existing tempo is passed back into the function to prevent unintended tempo modification.

    The tempo is re-read in quarter-note BPM. Reapy's Project.bpm reports the
    denominator-scaled project setting, and writing that reading back rescales the
    project on every call: in 7/8 a 120 BPM project became 240, then 480.
    """
    RPR.SetTempoTimeSigMarker(
        0, _marker_at_zero(), 0.0, -1, -1, project_tempo(), numerator, denominator, False
    )
    RPR.UpdateTimeline()
    _mark_dirty()
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
            _write_tempo(tempo)
            if time_signature:
                num, denom = map(int, time_signature.split("/"))
                _write_time_signature(num, denom)
            num, denom = _read_time_signature()
            return {
                "success": True,
                "name": name or f"New Project {time.strftime('%Y-%m-%d %H-%M-%S')}",
                "tempo": project_tempo(),
                "time_signature": f"{num}/{denom}",
            }
        except Exception as e:
            logger.error(f"create_project failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def save_project(project_path: str = "") -> dict:
        """Save the current project.

        Saving to a new path rebinds the open project to that file, matching the
        behaviour of File > Save As. With no path given, a project that already has a
        file is saved in place and an unsaved one goes to ~/Documents/REAPER Projects.
        """
        try:
            current = _project_file()
            if not project_path:
                if current:
                    project_path = current
                else:
                    stem = f"Project {time.strftime('%Y-%m-%d %H-%M-%S')}"
                    default_dir = Path.home() / "Documents" / "REAPER Projects"
                    os.makedirs(default_dir, exist_ok=True)
                    project_path = str(default_dir / f"{stem}.rpp")
            project_path = str(Path(project_path).expanduser().resolve())
            os.makedirs(os.path.dirname(project_path), exist_ok=True)

            if _same_file(current, project_path):
                # The project is already bound to this file. An in-place save writes
                # once, clears the dirty flag, and preserves the undo history.
                RPR.Main_SaveProject(0, False)
            else:
                # reapy's Project.save only accepts a boolean force_save_as parameter.
                # Main_SaveProjectEx is used to specify a file path.
                RPR.Main_SaveProjectEx(0, project_path, 0)

                if not os.path.isfile(project_path):
                    return {
                        "success": False,
                        "error": f"REAPER reported no error but {project_path} was not written",
                    }

                # Main_SaveProjectEx writes the file but leaves the project bound to its
                # previous name with the dirty flag set. Calling Main_SaveProject to
                # clear that flag saves a second time under REAPER's own auto-name,
                # depositing save.rpp, save2.rpp, ... next to the media directory.
                # Reopening the file just written rebinds the project to it and clears
                # the flag. The noprompt prefix suppresses the modal save dialog, which
                # would otherwise deadlock the connection.
                RPR.Main_openProject("noprompt:" + project_path)

            if not os.path.isfile(project_path):
                return {
                    "success": False,
                    "error": f"REAPER reported no error but {project_path} was not written",
                }

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
                "tempo": project_tempo(),
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
                "path": _project_file(),
                "media_path": project.path,
                "tempo": project_tempo(),
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
