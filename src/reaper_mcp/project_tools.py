import os
import time
import logging
from pathlib import Path

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project

logger = logging.getLogger("reaper_mcp.project_tools")


def _read_time_signature() -> tuple:
    """The project's time signature at the start of the timeline, as (num, denom).

    Deliberately not ``reapy.Project.time_signature``. That property is
    documented as returning the time signature but its two values are ``(bpm,
    bpi)`` - the tempo and the numerator - so formatting them as "n/d" reports a
    120 BPM project in 4/4 as "120.0/4.0". It is also read-only, which is why
    setting it raised.

    ``TimeMap_GetTimeSigAtTime`` answers the question that was being asked. It
    fills its output arguments in place and reapy hands back the argument list,
    so the numerator and denominator are at 2 and 3.
    """
    out = RPR.TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)
    return int(out[2]), int(out[3])


def _refuse_if_dirty(what: str) -> dict:
    """Guard the two commands that make REAPER ask a modal question.

    Opening or replacing a project with unsaved changes makes REAPER put up
    "Save unsaved project before closing?" - three buttons, and no way to answer
    it from here. While it waits REAPER runs no deferred scripts, so the reapy
    server stops answering and every tool call hangs until somebody clicks it.

    Refusing is strictly better than that: the caller gets a sentence telling
    them what to do, instead of a connection that has silently died. Answering
    the question automatically is not on the table - one of those buttons throws
    away the user's work.
    """
    if not RPR.IsProjectDirty(0):
        return {}
    return {
        "success": False,
        "error": (
            f"The open project has unsaved changes, so {what} would make REAPER "
            "open a modal 'Save unsaved project before closing?' prompt. While "
            "that prompt is waiting, no REAPER tool can respond at all - so this "
            "stopped rather than starting it.\n"
            "Save first with save_project, or discard the changes in REAPER, "
            "then try again."
        ),
    }


def _marker_at_zero() -> int:
    """Index of the tempo/time-signature marker at the start, or -1 for none.

    ``SetTempoTimeSigMarker`` takes -1 to mean "insert a new one", so this
    doubles as the argument to pass: edit the marker that is there, or make one.
    """
    for i in range(RPR.CountTempoTimeSigMarkers(0)):
        marker = RPR.GetTempoTimeSigMarker(0, i, 0, 0, 0, 0, 0, 0, 0)
        if abs(marker[3]) < 1e-9:  # timepos
            return i
    return -1


def _write_tempo(bpm: float) -> float:
    """Set the project tempo, through the marker at 0 when there is one.

    ``reapy.Project.bpm`` is only correct while the project has no tempo marker
    at the start. Once one exists - and ``_write_time_signature`` creates one,
    because that is where REAPER keeps the time signature - the marker wins:
    assigning to ``bpm`` leaves the tempo where it was AND inserts a second
    marker at the same position, so the project quietly accumulates markers
    while appearing not to respond.

    So the two tools have to agree about where the tempo lives. Setting a time
    signature and then a tempo is an ordinary thing to ask for, and it used to
    half-work in a way nothing reported.
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
    """Set the time signature at the start of the project, leaving the tempo alone.

    Named apart from the tool that calls it on purpose: the tool is bound in
    ``register_tools``'s scope under the obvious name, so a module-level helper
    sharing it would be shadowed by the closure and the tool would call itself.

    REAPER keeps the time signature in tempo/time-signature markers rather than
    in a project field, so this edits the marker at position 0 - creating it
    only if there is not one already, since inserting a second marker at the
    same spot on every call would litter the timeline.

    The current BPM is passed straight back in because SetTempoTimeSigMarker
    writes tempo and signature together; omitting it would silently retempo the
    project.
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

            # Main_SaveProjectEx, because reapy's Project.save takes no path -
            # its only argument is `force_save_as`, a bool. Passing a filename
            # to it put a string where REAPER's binding wanted an int, so every
            # call with a path died on "'str' object cannot be interpreted as
            # an integer" before saving anything.
            RPR.Main_SaveProjectEx(0, project_path, 0)

            if not os.path.isfile(project_path):
                return {
                    "success": False,
                    "error": f"REAPER reported no error but {project_path} was not written",
                }

            # SaveProjectEx writes the file but leaves the project marked dirty,
            # so the next load_project or create_project would still trip
            # REAPER's save prompt - having just saved. A plain save afterwards
            # clears the flag, and cannot prompt for a filename because the call
            # above has already given the project one.
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
        """Set the project time signature, e.g. 4/4, 3/4, 6/8. The tempo is unchanged."""
        try:
            if numerator < 1 or denominator < 1:
                return {"success": False, "error": "numerator and denominator must be positive"}
            num, denom = _write_time_signature(numerator, denominator)
            return {"success": True, "time_signature": f"{num}/{denom}"}
        except Exception as e:
            logger.error(f"set_time_signature failed: {e}")
            return {"success": False, "error": str(e)}
