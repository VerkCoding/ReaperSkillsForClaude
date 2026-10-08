import logging

from reaper_mcp.connection import RPR, get_project, held, reapy, records_undo
from reaper_mcp.units import (
    PAN_MODE_VALUES,
    pan_state,
    project_pan_defaults,
    set_solo,
    set_volume_db,
    track_state,
)

logger = logging.getLogger("reaper_mcp.track_tools")


def register_tools(mcp):

    @mcp.tool()
    @records_undo()
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
    @records_undo()
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
    @records_undo()
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
    @records_undo()
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
    @records_undo()
    def set_track_pan(
        track_index: int,
        pan: float | None = None,
        pan_mode: str | None = None,
        width: float | None = None,
    ) -> dict:
        """Set a track's pan, pan mode and width; omitted settings stay as they are.

        pan: -1.0 (left) to 1.0 (right). width: -1.0 to 1.0; 1.0 as recorded, 0 mono.
        pan_mode: "balance" (stereo balance / mono pan: on a stereo track, pan turns the
        other channel down and removes it at full pan), "stereo" (stereo pan: pan moves
        both channels), "dual" (dual pan, which ignores pan and width), or "project"
        (follow the project). To place a stereo track, pass pan_mode "stereo", width and
        pan in one call. Writes mode, then width, then pan, and reads every value back.
        """
        try:
            if pan is None and pan_mode is None and width is None:
                return {"success": False, "error": "nothing to set: pass pan, pan_mode or width"}
            if pan is not None and not -1.0 <= pan <= 1.0:
                return {"success": False, "error": f"pan must be -1.0 to 1.0, got {pan}"}
            if width is not None and not -1.0 <= width <= 1.0:
                return {"success": False, "error": f"width must be -1.0 to 1.0, got {width}"}
            if pan_mode is not None and pan_mode not in PAN_MODE_VALUES:
                return {"success": False, "error": f"pan_mode must be one of {', '.join(PAN_MODE_VALUES)}, got {pan_mode!r}"}
            project = get_project()
            track = project.tracks[track_index]
            project_pan = project_pan_defaults()
            mode = PAN_MODE_VALUES[pan_mode] if pan_mode is not None else int(
                RPR.GetMediaTrackInfo_Value(track.id, "I_PANMODE"))
            # Measured: in dual pan mode neither D_PAN nor D_WIDTH changes the output.
            if (pan is not None or width is not None) and (project_pan[0] if mode < 0 else mode) == 6:
                return {
                    "success": False,
                    "error": "the track would be in dual pan mode, where REAPER ignores pan and width: "
                             "pass pan_mode \"stereo\" or \"balance\", or set D_DUALPANL and D_DUALPANR",
                }

            for key, value in (
                ("I_PANMODE", PAN_MODE_VALUES.get(pan_mode)),
                ("D_WIDTH", width),
                ("D_PAN", pan),
            ):
                if value is not None:
                    RPR.SetMediaTrackInfo_Value(track.id, key, value)

            got = pan_state(track, project_pan)
            wrong = []
            if pan_mode is not None and int(RPR.GetMediaTrackInfo_Value(track.id, "I_PANMODE")) != mode:
                wrong.append("pan_mode")
            if width is not None and abs(got["width"] - width) > 1e-6:
                wrong.append("width")
            if pan is not None and abs(got["pan"] - pan) > 1e-6:
                wrong.append("pan")
            result = {"success": not wrong, "track_index": track_index, **got}
            if wrong:
                result["error"] = f"REAPER kept different {', '.join(wrong)}"
            return result
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo()
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
    @records_undo()
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
        """Get a track's volume, pan, pan mode, width, pan law, mute, solo, FX and items.

        pan_mode is balance, stereo, dual or classic, with "project" ones resolved;
        pan_mode_from_project and pan_law_from_project say the track follows the project.
        """
        try:
            project = get_project()
            # Held: each call outside it waits about 30 ms for REAPER's next defer cycle.
            with held():
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
        """List all tracks with volume, pan, pan mode, width, pan law, mute and solo.

        Fields as in get_track_info.
        """
        try:
            project = get_project()
            tracks = []
            with held():
                project_pan = project_pan_defaults()
                for i in range(project.n_tracks):
                    track = project.tracks[i]
                    tracks.append({
                        "index": i,
                        "name": track.name,
                        **track_state(track, project_pan),
                        "fx_count": track.n_fxs,
                        "item_count": track.n_items,
                    })
            return {"success": True, "count": len(tracks), "tracks": tracks}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo()
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
