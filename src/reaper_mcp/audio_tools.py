import os
import logging

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project

logger = logging.getLogger("reaper_mcp.audio_tools")


def _negative_index(**values) -> str:
    """Return a message naming the first negative index, or "".

    Reapy resolves a negative index to the last element, so a mistyped index
    silently edited a different item than the caller named.
    """
    for name, value in values.items():
        if value < 0:
            return "%s must be 0 or greater, got %s" % (name, value)
    return ""


def register_tools(mcp):

    @mcp.tool()
    def import_audio_file(file_path: str, track_index: int, position: float = 0.0) -> dict:
        """
        Import an audio file onto a track at the given position (seconds).
        Supports formats readable by REAPER: wav, aiff, mp3, flac, ogg.
        """
        try:
            invalid = _negative_index(track_index=track_index)
            if invalid:
                return {"success": False, "error": invalid}
            if position < 0:
                return {"success": False, "error": f"position must be 0 or greater, got {position}"}
            if not os.path.exists(file_path):
                return {"success": False, "error": f"File not found: {file_path}"}
            project = get_project()
            track = project.tracks[track_index]

            # Items are ordered by position, so the inserted one is identified by
            # comparing the track contents before and after. Reading the last item
            # instead described a different item whenever the insert landed earlier
            # in the timeline, and described a pre-existing item when nothing was
            # inserted at all.
            before = {track.items[i].id for i in range(track.n_items)}

            # REAPER uses track selection and cursor position to determine the insertion point.
            RPR.SetOnlyTrackSelected(track.id)
            project.cursor_position = position
            RPR.InsertMedia(file_path, 0)

            track_refreshed = project.tracks[track_index]
            inserted = [
                i for i in range(track_refreshed.n_items)
                if track_refreshed.items[i].id not in before
            ]
            if not inserted:
                return {
                    "success": False,
                    "error": f"REAPER read no media from {file_path}. No item was inserted.",
                }
            item_index = inserted[0]
            item = track_refreshed.items[item_index]
            return {
                "success": True,
                "track_index": track_index,
                "item_index": item_index,
                "position": item.position,
                "length": item.length,
                "file_path": file_path,
            }
        except Exception as e:
            logger.error(f"import_audio_file failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def start_recording(track_index: int) -> dict:
        """Arm a track and start recording.

        The track stays record-armed after the transport stops, matching REAPER's own
        behaviour. Stopping a recording can take REAPER several seconds to finalise
        the file, during which further calls wait.
        """
        try:
            invalid = _negative_index(track_index=track_index)
            if invalid:
                return {"success": False, "error": invalid}
            # Bit 4 of the play state marks an active recording. Issuing the record
            # command again while it is set stops the take rather than starting one.
            if int(RPR.GetPlayState()) & 4:
                return {"success": False, "error": "REAPER is already recording."}
            project = get_project()
            track = project.tracks[track_index]

            # The reapy.Track.armed property does not update REAPER's state. 
            # Initiating recording without an armed track triggers a modal dialog that halts script execution.
            RPR.SetMediaTrackInfo_Value(track.id, "I_RECARM", 1)
            if not RPR.GetMediaTrackInfo_Value(track.id, "I_RECARM"):
                return {
                    "success": False,
                    "error": f"Track {track_index} arming failed. Transport start aborted to prevent modal dialog block.",
                }

            RPR.Main_OnCommand(1013, 0)
            return {
                "success": True,
                "track_index": track_index,
                "armed": True,
                "message": "Recording started.",
            }
        except Exception as e:
            logger.error(f"start_recording failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def stop_transport() -> dict:
        """Stop playback or recording."""
        try:
            RPR.Main_OnCommand(1016, 0)
            return {
                "success": True,
                "message": "Transport stopped.",
                "play_state": int(RPR.GetPlayState()),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def play_project() -> dict:
        """Start project playback from the current cursor position."""
        try:
            RPR.Main_OnCommand(1007, 0)
            return {
                "success": True,
                "message": "Playback started.",
                "play_state": int(RPR.GetPlayState()),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_cursor_position(position: float) -> dict:
        """Move the edit cursor to a position in seconds."""
        try:
            project = get_project()
            project.cursor_position = position
            return {"success": True, "position": project.cursor_position}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def edit_audio_item(
        track_index: int,
        item_index: int,
        start_trim: float = 0.0,
        end_trim: float = 0.0,
        fade_in: float = 0.0,
        fade_out: float = 0.0,
    ) -> dict:
        """
        Trim an audio item and add fades.
        start_trim: seconds to remove from the beginning.
        end_trim: seconds to remove from the end.
        fade_in: fade in length in seconds.
        fade_out: fade out length in seconds.
        """
        try:
            invalid = _negative_index(track_index=track_index, item_index=item_index)
            if invalid:
                return {"success": False, "error": invalid}
            for name, value in (
                ("start_trim", start_trim), ("end_trim", end_trim),
                ("fade_in", fade_in), ("fade_out", fade_out),
            ):
                if value < 0:
                    return {"success": False, "error": f"{name} must be 0 or greater, got {value}"}

            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]

            # Trimming more than the item holds pushed the take offset past the end of
            # the source: the item kept a plausible length while playing nothing.
            length = RPR.GetMediaItemInfo_Value(item.id, "D_LENGTH")
            if start_trim + end_trim >= length:
                return {
                    "success": False,
                    "error": (
                        f"trimming {start_trim + end_trim}s from a {length}s item would "
                        f"leave nothing"
                    ),
                }

            # Direct RPR calls are required because reapy properties for fades and offsets either fail to propagate to REAPER or lack setter methods.
            if start_trim > 0:
                RPR.SetMediaItemInfo_Value(item.id, "D_POSITION", item.position + start_trim)
                RPR.SetMediaItemInfo_Value(item.id, "D_LENGTH", item.length - start_trim)
                take = item.active_take
                if take:
                    offset = RPR.GetMediaItemTakeInfo_Value(take.id, "D_STARTOFFS")
                    RPR.SetMediaItemTakeInfo_Value(take.id, "D_STARTOFFS", offset + start_trim)

            if end_trim > 0:
                RPR.SetMediaItemInfo_Value(item.id, "D_LENGTH", item.length - end_trim)

            if fade_in > 0:
                RPR.SetMediaItemInfo_Value(item.id, "D_FADEINLEN", fade_in)

            if fade_out > 0:
                RPR.SetMediaItemInfo_Value(item.id, "D_FADEOUTLEN", fade_out)

            # Querying REAPER directly ensures returned values reflect actual state, including constraints.
            return {
                "success": True,
                "track_index": track_index,
                "item_index": item_index,
                "position": RPR.GetMediaItemInfo_Value(item.id, "D_POSITION"),
                "length": RPR.GetMediaItemInfo_Value(item.id, "D_LENGTH"),
                "fade_in": RPR.GetMediaItemInfo_Value(item.id, "D_FADEINLEN"),
                "fade_out": RPR.GetMediaItemInfo_Value(item.id, "D_FADEOUTLEN"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def adjust_pitch(track_index: int, item_index: int, semitones: float) -> dict:
        """Adjust the pitch of an audio item by semitones."""
        try:
            invalid = _negative_index(track_index=track_index, item_index=item_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]
            if not item.n_takes:
                return {"success": False, "error": f"item {item_index} has no take to pitch"}
            take = item.active_take

            # Reapy's Take class defines no pitch property, so assigning to it only
            # created a Python attribute and read that attribute back: the tool
            # reported the requested value while REAPER kept the take unchanged.
            RPR.SetMediaItemTakeInfo_Value(take.id, "D_PITCH", semitones)
            return {
                "success": True,
                "track_index": track_index,
                "item_index": item_index,
                "pitch_semitones": RPR.GetMediaItemTakeInfo_Value(take.id, "D_PITCH"),
                "requested": semitones,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def adjust_playback_rate(track_index: int, item_index: int, rate: float) -> dict:
        """Adjust playback rate of an audio item."""
        try:
            invalid = _negative_index(track_index=track_index, item_index=item_index)
            if invalid:
                return {"success": False, "error": invalid}
            if rate <= 0:
                return {"success": False, "error": f"rate must be greater than 0, got {rate}"}
            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]
            if not item.n_takes:
                return {"success": False, "error": f"item {item_index} has no take to rate"}
            take = item.active_take

            # Reapy's Take class defines no playback_rate property, so assigning to it
            # only created a Python attribute and read that attribute back: the tool
            # reported the requested rate while REAPER kept the take unchanged.
            RPR.SetMediaItemTakeInfo_Value(take.id, "D_PLAYRATE", rate)
            return {
                "success": True,
                "track_index": track_index,
                "item_index": item_index,
                "playback_rate": RPR.GetMediaItemTakeInfo_Value(take.id, "D_PLAYRATE"),
                "requested": rate,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
