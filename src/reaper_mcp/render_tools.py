import base64
import os
import logging
import struct
from contextlib import contextmanager
from pathlib import Path

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project
from reaper_mcp.units import set_solo

logger = logging.getLogger("reaper_mcp.render_tools")

# REAPER stores the render format as a base64 configuration blob under the string
# setting RENDER_FORMAT, not as a number. The blob opens with a reversed four
# character code naming the format, followed by settings belonging to that format.
# Writing RENDER_FORMAT and RENDER_FORMAT2 as numbers changed nothing: every render
# came out as a 24 bit WAV whatever was requested, and a request for flac or mp3
# produced a .wav file while the tool reported the file it expected as missing.
FORMAT_FOURCC = {
    "wav":  b"evaw",
    "flac": b"calf",
    "mp3":  b"l3pm",
    "ogg":  b"vggo",
}

# Bit depth travels inside the blob. For WAV, 32 selects 32 bit float, which is how
# REAPER represents that depth. FLAC carries its depth and compression level as two
# little endian integers.
WAV_BIT_DEPTHS = (8, 16, 24, 32)
FLAC_BIT_DEPTHS = (16, 24)
FLAC_COMPRESSION = 5

# Suffixes that may be replaced when resolving an output path. Anything else is left
# alone, because Path.with_suffix would truncate a name such as "mix v1.2".
_AUDIO_SUFFIXES = {".wav", ".flac", ".mp3", ".ogg", ".aif", ".aiff"}


def _render_format_cookie(format: str, bit_depth: int) -> str:
    """Build the RENDER_FORMAT blob for a format and bit depth."""
    fourcc = FORMAT_FOURCC[format.lower()]
    if fourcc == b"evaw":
        blob = fourcc + bytes([bit_depth, 0, 1])
    elif fourcc == b"calf":
        blob = fourcc + struct.pack("<II", bit_depth, FLAC_COMPRESSION)
    else:
        blob = fourcc
    return base64.b64encode(blob).decode("ascii")


def _unsupported(format: str, bit_depth: int) -> str:
    """Return a message describing an unrenderable request, or "".

    An unknown format silently fell back to WAV, so the caller received a file in a
    format they had not asked for, under a name suggesting they had.
    """
    key = format.lower()
    if key not in FORMAT_FOURCC:
        return "Unsupported format '%s'. Available: %s" % (
            format, ", ".join(sorted(FORMAT_FOURCC)))
    if key == "wav" and bit_depth not in WAV_BIT_DEPTHS:
        return "WAV bit depth must be one of %s, got %s" % (
            ", ".join(str(d) for d in WAV_BIT_DEPTHS), bit_depth)
    if key == "flac" and bit_depth not in FLAC_BIT_DEPTHS:
        return "FLAC bit depth must be 16 or 24, got %s" % bit_depth
    return ""


def _nothing_to_render() -> dict:
    """Return an error dict when the project holds no audio, otherwise None."""
    if RPR.GetProjectLength(0) <= 0:
        return {"success": False, "error": "The project is empty. There is nothing to render."}
    return None

# RENDER_BOUNDSFLAG values.
# Value 0 (custom time range) requires RENDER_STARTPOS and RENDER_ENDPOS to be set.
# If unset, REAPER displays a modal dialog which blocks background scripts and causes execution to halt.
# Explicit values are used to prevent modal dialogs.
BOUNDS_ENTIRE_PROJECT = 1
BOUNDS_TIME_SELECTION = 2

# Render settings are temporarily modified and restored.
# State preservation prevents automated render operations from modifying the user's project settings permanently.
_SAVED_STRINGS = ("RENDER_FILE", "RENDER_PATTERN", "RENDER_FORMAT")
_SAVED_NUMBERS = ("RENDER_SRATE", "RENDER_CHANNELS", "RENDER_BOUNDSFLAG")


def _get_string(key: str) -> str:
    """Read a string project setting.

    The buffer length must accommodate the maximum expected path length to prevent truncation.
    """
    return RPR.GetSetProjectInfo_String(0, key, " " * 1024, False)[3]


def _resolve_output(output_path: str, format: str) -> Path:
    """Determine final output file path.

    REAPER stores the directory in RENDER_FILE and the filename in RENDER_PATTERN.
    The extension is overridden by the selected format to ensure consistency.
    """
    target = Path(output_path).expanduser().resolve()
    extension = "." + format.lower()
    if target.suffix.lower() == extension:
        return target
    if target.suffix.lower() in _AUDIO_SUFFIXES:
        return target.with_suffix(extension)
    # with_suffix would drop everything after the last dot of a name like "mix v1.2".
    return target.with_name(target.name + extension)


@contextmanager
def _render_settings(
    output_path: str,
    format: str,
    sample_rate: int,
    bit_depth: int,
    channels: int,
    bounds: int,
):
    """Context manager to scope temporary render settings."""
    saved_strings = {key: _get_string(key) for key in _SAVED_STRINGS}
    saved_numbers = {
        key: RPR.GetSetProjectInfo(0, key, 0, False) for key in _SAVED_NUMBERS
    }

    target = _resolve_output(output_path, format)
    os.makedirs(target.parent, exist_ok=True)

    try:
        RPR.GetSetProjectInfo_String(0, "RENDER_FILE", str(target.parent), True)
        RPR.GetSetProjectInfo_String(0, "RENDER_PATTERN", target.stem, True)
        RPR.GetSetProjectInfo_String(
            0, "RENDER_FORMAT", _render_format_cookie(format, bit_depth), True
        )
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
    RPR.Main_OnCommand(41824, 0)  # Command 41824: File: Render project to disk (no dialog)


def _nothing_rendered(target: Path) -> str:
    """Generate error description when render output is missing.

    Command 41824 suppresses the start dialog but not the error dialog. Modal dialogs halt script execution.
    """
    if target.parent.is_dir() and not any(target.parent.iterdir()):
        where = "output directory empty"
    else:
        where = f"no file at {target}"
    return (
        f"Render completed with missing output ({where}). "
        "A modal 'Render Error' dialog may be blocking execution in REAPER. "
        "Verify project content and time selection length."
    )


def render_to_temp_file(sample_rate: int = 48000) -> str:
    """
    Render project to a temporary WAV file.
    Caller is responsible for file deletion.
    """
    import tempfile
    import uuid

    # Output is written directly to the system temporary directory.
    # Avoiding subdirectories prevents leftover directories after caller unlinks the file.
    target = Path(tempfile.gettempdir()) / f"reaper-mcp-{uuid.uuid4().hex}.wav"

    with _render_settings(str(target), "wav", sample_rate, 24, 2,
                          bounds=BOUNDS_ENTIRE_PROJECT) as resolved:
        _render_now()

    if not resolved.is_file():
        raise RuntimeError(
            "Render output missing. Close any open modal dialogs in REAPER to resume script execution."
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
            problem = _unsupported(format, bit_depth)
            if problem:
                return {"success": False, "error": problem}
            empty = _nothing_to_render()
            if empty:
                return empty
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
                return {"success": False, "error": f"Invalid range: end ({end}) must be greater than start ({start})."}
            problem = _unsupported(format, bit_depth)
            if problem:
                return {"success": False, "error": problem}
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
            problem = _unsupported(format, bit_depth)
            if problem:
                return {"success": False, "error": problem}
            empty = _nothing_to_render()
            if empty:
                return empty
            output_directory = str(Path(output_directory).expanduser().resolve())
            os.makedirs(output_directory, exist_ok=True)
            project = get_project()
            indices = track_indices if track_indices is not None else list(range(project.n_tracks))
            rendered = []

            for idx in indices:
                track = project.tracks[idx]
                track_name = track.name or f"Track_{idx}"
                # Exclusive solo ensures isolation of track audio.
                for j in range(project.n_tracks):
                    set_solo(project.tracks[j], j == idx)
                # Characters are restricted to prevent filesystem errors.
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

            # Restore project state.
            for j in range(project.n_tracks):
                set_solo(project.tracks[j], False)

            return {
                "success": True,
                "output_directory": output_directory,
                "stems": rendered,
            }
        except Exception as e:
            # State must be restored even if an error occurs.
            try:
                proj = get_project()
                for j in range(proj.n_tracks):
                    set_solo(proj.tracks[j], False)
            except Exception:
                pass
            logger.error(f"render_stems failed: {e}")
            return {"success": False, "error": str(e)}
