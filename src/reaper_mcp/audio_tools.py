import os
import logging

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project

logger = logging.getLogger("reaper_mcp.audio_tools")


def register_tools(mcp):

    @mcp.tool()
    def import_audio_file(file_path: str, track_index: int, position: float = 0.0) -> dict:
        """
        Import an audio file onto a track at the given position (seconds).
        Supports all formats REAPER can read: wav, aiff, mp3, flac, ogg, etc.
        """
        try:
            if not os.path.exists(file_path):
                return {"success": False, "error": f"File not found: {file_path}"}
            project = get_project()
            track = project.tracks[track_index]
            # Select only this track, set cursor, then insert media at cursor
            RPR.SetOnlyTrackSelected(track.id)
            project.cursor_position = position
            RPR.InsertMedia(file_path, 0)
            # Retrieve the item that was just created (last item on the track)
            track_refreshed = project.tracks[track_index]
            if track_refreshed.n_items == 0:
                return {"success": False, "error": "Insert succeeded but no item found on track"}
            item = track_refreshed.items[track_refreshed.n_items - 1]
            return {
                "success": True,
                "track_index": track_index,
                "item_index": track_refreshed.n_items - 1,
                "position": item.position,
                "length": item.length,
                "file_path": file_path,
            }
        except Exception as e:
            logger.error(f"import_audio_file failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def start_recording(track_index: int) -> dict:
        """Arm a track and start recording. Call stop_transport when done."""
        try:
            project = get_project()
            track = project.tracks[track_index]

            # reapy's Track has no `armed` property, so `track.armed = True`
            # only set an attribute on a throwaway Python object - REAPER never
            # heard about it. Recording then started with nothing armed, which
            # REAPER answers with a modal "No tracks are armed for recording"
            # warning; a modal dialog stops every background script, so the
            # tool that was meant to start a recording instead froze the
            # connection until somebody clicked the box.
            RPR.SetMediaTrackInfo_Value(track.id, "I_RECARM", 1)
            if not RPR.GetMediaTrackInfo_Value(track.id, "I_RECARM"):
                return {
                    "success": False,
                    "error": (
                        f"Could not arm track {track_index}. Not starting the "
                        "transport: recording with nothing armed opens a modal "
                        "warning in REAPER that blocks every other tool."
                    ),
                }

            RPR.Main_OnCommand(1013, 0)  # Transport: Record
            return {
                "success": True,
                "track_index": track_index,
                "armed": True,
                "message": "Recording started. Call stop_transport to stop.",
            }
        except Exception as e:
            logger.error(f"start_recording failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def stop_transport() -> dict:
        """Stop playback or recording."""
        try:
            RPR.Main_OnCommand(1016, 0)  # Transport: Stop
            return {"success": True, "message": "Transport stopped"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def play_project() -> dict:
        """Start project playback from the current cursor position."""
        try:
            RPR.Main_OnCommand(1007, 0)  # Transport: Play
            return {"success": True, "message": "Playback started"}
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
        Trim an audio item and/or add fades. All values in seconds.
        start_trim: seconds to remove from the beginning.
        end_trim: seconds to remove from the end.
        fade_in/fade_out: fade length in seconds.
        """
        try:
            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]

            # Written through reascript_api and read back below.
            #
            # `item.position` and `item.length` do reach REAPER, but the other
            # three reapy properties in this block did not: fade_in_length and
            # fade_out_length assign an attribute and go nowhere, so the fades
            # were silently dropped while this returned success, and
            # take.start_offset has no setter at all - it raised AttributeError
            # and took the whole trim with it.
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

            # Reported from REAPER rather than from the arguments, so a value it
            # clamped or refused shows up here instead of being echoed back.
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
        """Adjust the pitch of an audio item by semitones (can be fractional)."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]
            take = item.active_take
            take.pitch = semitones
            return {
                "success": True,
                "track_index": track_index,
                "item_index": item_index,
                "pitch_semitones": take.pitch,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def adjust_playback_rate(track_index: int, item_index: int, rate: float) -> dict:
        """Adjust playback rate of an audio item. 1.0 = normal speed, 0.5 = half speed."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]
            take = item.active_take
            take.playback_rate = rate
            return {
                "success": True,
                "track_index": track_index,
                "item_index": item_index,
                "playback_rate": take.playback_rate,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
