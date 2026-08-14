import os
import logging
from contextlib import contextmanager
from pathlib import Path

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project
from reaper_mcp.units import set_solo

logger = logging.getLogger("reaper_mcp.render_tools")

# REAPER RENDER_FORMAT codes
FORMAT_CODES = {
    "wav":  0,
    "mp3":  3,
    "ogg":  4,
    "flac": 5,
}

# REAPER RENDER_FORMAT2 codes for WAV bit depth
BIT_DEPTH_CODES = {
    16: 0,
    24: 2,
    32: 4,
}

# RENDER_BOUNDSFLAG values, which are worth spelling out because getting them
# wrong is silent and then modal. REAPER's set is:
#
#     0 = custom time range   1 = entire project      2 = time selection
#     3 = all project regions 4 = selected items      5 = selected regions
#
# 0 looks like the sensible default and is the one value that cannot work
# unnoticed: the custom range starts out empty (RENDER_STARTPOS == RENDER_ENDPOS
# == 0), so REAPER opens a modal "Nothing to render!" box. A modal box stops
# REAPER running deferred scripts, which stops the reapy server answering, so
# the render call never returns and the whole connection appears to hang. The
# error is nowhere in Python - it is a dialog waiting on screen.
BOUNDS_ENTIRE_PROJECT = 1
BOUNDS_TIME_SELECTION = 2

# Settings a render touches, saved and put back around every render. Rendering
# is meant to be a read of the project, not an edit of its preferences, and
# analysis tools render constantly - without this, asking for the loudness would
# silently repoint the user's render output at a temp file.
_SAVED_STRINGS = ("RENDER_FILE", "RENDER_PATTERN")
_SAVED_NUMBERS = (
    "RENDER_FORMAT", "RENDER_FORMAT2", "RENDER_SRATE",
    "RENDER_CHANNELS", "RENDER_BOUNDSFLAG",
)


def _get_string(key: str) -> str:
    """Read a string project setting.

    The buffer passed in is the buffer REAPER writes back into, so it has to be
    long enough for the answer - a short one truncates a render path. reapy
    returns the whole argument list with the outputs filled in; index 3 is the
    value.
    """
    return RPR.GetSetProjectInfo_String(0, key, " " * 1024, False)[3]


def _resolve_output(output_path: str, format: str) -> Path:
    """Where REAPER will actually put the file.

    RENDER_FILE is a *directory* and RENDER_PATTERN is the file name; handing
    the full path to RENDER_FILE alone makes REAPER create a directory called
    `mixdown.wav` and write the pattern-named file inside it. Since the caller
    names a file, split it here, and let the format decide the extension so
    asking for mp3 with a .wav name still lands somewhere predictable.
    """
    target = Path(output_path).expanduser().resolve()
    return target.with_suffix("." + format.lower())


@contextmanager
def _render_settings(
    output_path: str,
    format: str,
    sample_rate: int,
    bit_depth: int,
    channels: int,
    bounds: int,
):
    """Apply render settings for the duration of the block, then put them back."""
    saved_strings = {key: _get_string(key) for key in _SAVED_STRINGS}
    saved_numbers = {
        key: RPR.GetSetProjectInfo(0, key, 0, False) for key in _SAVED_NUMBERS
    }

    target = _resolve_output(output_path, format)
    os.makedirs(target.parent, exist_ok=True)

    try:
        RPR.GetSetProjectInfo_String(0, "RENDER_FILE", str(target.parent), True)
        RPR.GetSetProjectInfo_String(0, "RENDER_PATTERN", target.stem, True)
        RPR.GetSetProjectInfo(0, "RENDER_FORMAT", FORMAT_CODES.get(format.lower(), 0), True)
        RPR.GetSetProjectInfo(0, "RENDER_FORMAT2", BIT_DEPTH_CODES.get(bit_depth, 2), True)
        RPR.GetSetProjectInfo(0, "RENDER_SRATE", float(sample_rate), True)
        RPR.GetSetProjectInfo(0, "RENDER_CHANNELS", float(channels), True)
        RPR.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", float(bounds), True)
        yield target
    finally:
        for key, value in saved_strings.items():
            RPR.GetSetProjectInfo_String(0, key, value, True)
        for key, value in saved_numbers.items():
            RPR.GetSetProjectInfo(0, key, value, True)


def _render_now() -> None:
    RPR.Main_OnCommand(41824, 0)  # File: Render project to disk (no dialog)


def _nothing_rendered(target: Path) -> str:
    """Why no file appeared, in the two ways it actually happens.

    41824 is the no-dialog render, but that only means REAPER does not ask
    anything before starting - it still opens a modal box when the render turns
    out to be empty, and that box is invisible from here.
    """
    if target.parent.is_dir() and not any(target.parent.iterdir()):
        where = "the output directory is empty"
    else:
        where = f"nothing was written to {target}"
    return (
        f"Render finished but produced no file ({where}). Check REAPER's window: "
        "a 'Render Error' dialog blocks every background script until it is "
        "dismissed. An empty project, or a time selection of zero length, is the "
        "usual cause."
    )


def render_to_temp_file(sample_rate: int = 48000) -> str:
    """
    Render the current project to a temporary WAV file and return its path.
    Used by analysis and mastering tools. Caller is responsible for deleting the file.
    """
    import tempfile
    import uuid

    # A unique name straight in the temp directory rather than a temp directory
    # of its own: callers unlink the file they are given, so a wrapper directory
    # would be left behind by every analysis call.
    target = Path(tempfile.gettempdir()) / f"reaper-mcp-{uuid.uuid4().hex}.wav"

    with _render_settings(str(target), "wav", sample_rate, 24, 2,
                          bounds=BOUNDS_ENTIRE_PROJECT) as resolved:
        _render_now()

    if not resolved.is_file():
        raise RuntimeError(
            "REAPER rendered no audio. If a 'Render Error' dialog is open in "
            "REAPER, dismiss it - REAPER runs no background scripts while one is "
            "waiting, so nothing else can work until it is closed."
        )
    return str(resolved)


def register_tools(mcp):

    @mcp.tool()
    def render_project(
        output_path: str,
        format: str = "wav",
        sample_rate: int = 48000,
        bit_depth: int = 24,
        channels: int = 2,
    ) -> dict:
        """
        Render the entire project to a file.
        format: wav, flac, mp3 (requires LAME), ogg.
        sample_rate: e.g. 44100, 48000, 96000.
        bit_depth: 16, 24, or 32 (WAV only; ignored for mp3/ogg/flac).
        channels: 1 (mono) or 2 (stereo).
        """
        try:
            with _render_settings(output_path, format, sample_rate, bit_depth, channels,
                                  bounds=BOUNDS_ENTIRE_PROJECT) as target:
                _render_now()
            if not target.is_file():
                return {"success": False, "error": _nothing_rendered(target)}
            return {
                "success": True,
                "output_path": str(target),
                "format": format,
                "sample_rate": sample_rate,
                "bit_depth": bit_depth,
                "channels": channels,
                "file_size_bytes": target.stat().st_size,
            }
        except Exception as e:
            logger.error(f"render_project failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def render_time_selection(
        output_path: str,
        start: float,
        end: float,
        format: str = "wav",
        sample_rate: int = 48000,
        bit_depth: int = 24,
        channels: int = 2,
    ) -> dict:
        """Render a specific time range of the project to a file."""
        try:
            if end <= start:
                return {"success": False, "error": f"end ({end}) must be after start ({start})"}
            project = get_project()
            project.time_selection = (start, end)
            with _render_settings(output_path, format, sample_rate, bit_depth, channels,
                                  bounds=BOUNDS_TIME_SELECTION) as target:
                _render_now()
            if not target.is_file():
                return {"success": False, "error": _nothing_rendered(target)}
            return {
                "success": True,
                "output_path": str(target),
                "start": start,
                "end": end,
                "format": format,
                "file_size_bytes": target.stat().st_size,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def render_stems(
        output_directory: str,
        track_indices: list = None,
        format: str = "wav",
        sample_rate: int = 48000,
        bit_depth: int = 24,
    ) -> dict:
        """
        Render each track as a separate stem file by soloing each track individually.
        track_indices: list of track indices, or null to render all tracks.
        Files are named after the track names in the output directory.
        """
        try:
            output_directory = str(Path(output_directory).expanduser().resolve())
            os.makedirs(output_directory, exist_ok=True)
            project = get_project()
            indices = track_indices if track_indices is not None else list(range(project.n_tracks))
            rendered = []

            for idx in indices:
                track = project.tracks[idx]
                track_name = track.name or f"Track_{idx}"
                # Solo this track exclusively
                for j in range(project.n_tracks):
                    set_solo(project.tracks[j], j == idx)
                # Sanitize filename
                safe_name = "".join(c if c.isalnum() or c in " _-" else "_" for c in track_name)
                stem_path = os.path.join(output_directory, f"{safe_name}.{format}")
                with _render_settings(stem_path, format, sample_rate, bit_depth, 2,
                                      bounds=BOUNDS_ENTIRE_PROJECT) as target:
                    _render_now()
                rendered.append({
                    "track_index": idx,
                    "track_name": track_name,
                    "output_path": str(target),
                    "exists": target.is_file(),
                    "file_size_bytes": target.stat().st_size if target.is_file() else 0,
                })

            # Unsolo all tracks
            for j in range(project.n_tracks):
                set_solo(project.tracks[j], False)

            return {
                "success": True,
                "output_directory": output_directory,
                "stems": rendered,
            }
        except Exception as e:
            # Always unsolo on error
            try:
                proj = get_project()
                for j in range(proj.n_tracks):
                    set_solo(proj.tracks[j], False)
            except Exception:
                pass
            logger.error(f"render_stems failed: {e}")
            return {"success": False, "error": str(e)}
