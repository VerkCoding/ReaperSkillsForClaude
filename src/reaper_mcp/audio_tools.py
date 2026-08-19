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
        Supports formats readable by REAPER: wav, aiff, mp3, flac, ogg.
        """
        try:
            if not os.path.exists(file_path):
                return {"success": False, "error": f"File not found: {file_path}"}
            project = get_project()
            track = project.tracks[track_index]
            
            # REAPER uses track selection and cursor position to determine the insertion point.
            RPR.SetOnlyTrackSelected(track.id)
            project.cursor_position = position
            RPR.InsertMedia(file_path, 0)
            
            # Inserted media is appended as the last item on the track.
            track_refreshed = project.tracks[track_index]
            if track_refreshed.n_items == 0:
                return {"success": False, "error": "Insert operation completed but no item was found on track."}
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
        """Arm a track and start recording."""
        try:
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
            return {"success": True, "message": "Transport stopped."}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def play_project() -> dict:
        """Start project playback from the current cursor position."""
        try:
            RPR.Main_OnCommand(1007, 0)
            return {"success": True, "message": "Playback started."}
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
            project = get_project()
            track = project.tracks[track_index]
            item = track.items[item_index]

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
        """Adjust playback rate of an audio item."""
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
