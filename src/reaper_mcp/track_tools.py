import logging

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project
from reaper_mcp.units import set_solo, set_volume_db, track_state

logger = logging.getLogger("reaper_mcp.track_tools")


def register_tools(mcp):

    @mcp.tool()
    def create_track(name: str, track_type: str = "audio") -> dict:
        """
        Create a track at the end of the project.
        track_type: audio, midi, instrument, folder
        """
        try:
            project = get_project()
            idx = project.n_tracks
            project.add_track(idx, name)
            track = project.tracks[idx]

            if track_type in ("midi", "instrument"):
                # I_RECINPUT value 4096 configures track to accept all MIDI inputs.
                RPR.SetMediaTrackInfo_Value(track.id, "I_RECINPUT", 4096)
            elif track_type == "folder":
                # I_FOLDERDEPTH value 1 configures track as a parent folder.
                RPR.SetMediaTrackInfo_Value(track.id, "I_FOLDERDEPTH", 1)

            return {
                "success": True,
                "track_index": idx,
                "name": track.name,
                "type": track_type,
            }
        except Exception as e:
            logger.error(f"create_track error: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def delete_track(track_index: int) -> dict:
        """Delete track by index."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            RPR.DeleteTrack(track.id)
            return {"success": True, "deleted_index": track_index}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def rename_track(track_index: int, name: str) -> dict:
        """Rename track."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            track.name = name
            return {"success": True, "track_index": track_index, "name": track.name}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_track_volume(track_index: int, volume_db: float) -> dict:
        """Set track volume in dB."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            return {
                "success": True,
                "track_index": track_index,
                "volume_db": set_volume_db(track, volume_db),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_track_pan(track_index: int, pan: float) -> dict:
        """Set track pan."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            RPR.SetMediaTrackInfo_Value(track.id, "D_PAN", pan)
            return {
                "success": True,
                "track_index": track_index,
                "pan": RPR.GetMediaTrackInfo_Value(track.id, "D_PAN"),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_track_mute(track_index: int, muted: bool) -> dict:
        """Set track mute state."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            RPR.SetMediaTrackInfo_Value(track.id, "B_MUTE", 1 if muted else 0)
            return {
                "success": True,
                "track_index": track_index,
                "muted": bool(RPR.GetMediaTrackInfo_Value(track.id, "B_MUTE")),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_track_solo(track_index: int, soloed: bool) -> dict:
        """Set track solo state."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            return {
                "success": True,
                "track_index": track_index,
                "soloed": set_solo(track, soloed),
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def get_track_info(track_index: int) -> dict:
        """Get track information."""
        try:
            project = get_project()
            track = project.tracks[track_index]

            fx_list = []
            for i in range(track.n_fxs):
                fx = track.fxs[i]
                fx_list.append({"index": i, "name": fx.name, "enabled": fx.is_enabled})

            items = []
            for i in range(track.n_items):
                item = track.items[i]
                # Reapy's Item exposes no name attribute. The name REAPER shows for an
                # item belongs to its active take, and an item can carry no takes at
                # all. Reading item.name raised AttributeError for every track holding
                # media, which made this tool unusable on any populated project.
                take = item.active_take if item.n_takes else None
                items.append({
                    "index": i,
                    "position": item.position,
                    "length": item.length,
                    "name": take.name if take is not None else "",
                })

            return {
                "success": True,
                "track_index": track_index,
                "name": track.name,
                **track_state(track),
                "fx_count": track.n_fxs,
                "fx": fx_list,
                "item_count": track.n_items,
                "items": items,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def list_tracks() -> dict:
        """List all tracks."""
        try:
            project = get_project()
            tracks = []
            for i in range(project.n_tracks):
                track = project.tracks[i]
                tracks.append({
                    "index": i,
                    "name": track.name,
                    **track_state(track),
                    "fx_count": track.n_fxs,
                    "item_count": track.n_items,
                })
            return {"success": True, "count": len(tracks), "tracks": tracks}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_track_color(track_index: int, r: int, g: int, b: int) -> dict:
        """Set track color."""
        try:
            project = get_project()
            track = project.tracks[track_index]
            # Custom colors require setting bit 24 (0x1000000).
            color = RPR.ColorToNative(r, g, b) | 0x1000000
            RPR.SetMediaTrackInfo_Value(track.id, "I_CUSTOMCOLOR", color)
            return {"success": True, "track_index": track_index, "r": r, "g": g, "b": b}
        except Exception as e:
            return {"success": False, "error": str(e)}
