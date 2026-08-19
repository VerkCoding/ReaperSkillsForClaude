import logging

import reapy
from reapy import reascript_api as RPR

from reaper_mcp.connection import get_project
from reaper_mcp.units import linear_to_db

logger = logging.getLogger("reaper_mcp.mixing_tools")


def _db_to_linear(db: float) -> float:
    if db <= -150:
        return 0.0
    return 10 ** (db / 20.0)


def _negative_index(**values) -> str:
    """Return a message naming the first negative index, or "".

    The ReaScript send and envelope calls reject a negative index and do nothing.
    Without this check the tools reported success for work REAPER never performed.
    """
    for name, value in values.items():
        if value < 0:
            return "%s must be 0 or greater, got %s" % (name, value)
    return ""


def _track_index_of(project, pointer) -> int:
    """Map a MediaTrack pointer to its track index, or -1 when it matches none.

    GetTrackSendInfo_Value returns the destination track as a float address, while
    Track.id is a formatted pointer string, so both are compared as integers.
    """
    try:
        address = int(pointer)
    except (TypeError, ValueError):
        return -1
    for i in range(project.n_tracks):
        text = str(project.tracks[i].id)
        if "0x" not in text:
            continue
        try:
            if int(text.split("0x")[-1].rstrip(")"), 16) == address:
                return i
        except ValueError:
            continue
    return -1


def _scaled_for(envelope, value: float) -> float:
    """Convert a real value into the envelope's own storage scaling.

    REAPER stores envelope points in the scaling the envelope declares. A volume
    envelope defaults to fader scaling, where writing a raw linear gain lands far
    from the intended level: an unscaled -6 dB evaluated as -192 dB, silence.
    Pan envelopes report no scaling, for which this conversion is the identity.
    """
    return RPR.ScaleToEnvelopeMode(RPR.GetEnvelopeScalingMode(envelope), value)


def _is_null(pointer) -> bool:
    """Check if REAPER returned a null pointer.

    The ReaScript bridge returns pointers as strings. A null pointer is formatted
    as a string containing '0x0000000000000000', which evaluates to True in Python.
    This check prevents operations on null envelopes that would otherwise fail silently.
    """
    return not pointer or "0x0000000000000000" in str(pointer)


def _envelope_or_error(track, name: str, shown_as: str):
    """Retrieve a named track envelope or an error dictionary.

    REAPER instantiates a track envelope only when it is exposed in the user interface.
    As there is no API method to expose an envelope, the user must perform this action manually.
    """
    envelope = RPR.GetTrackEnvelopeByName(track.id, name)
    if _is_null(envelope):
        return None, {
            "success": False,
            "error": (
                f"{name} envelope not found. The envelope must be shown first: right-click the track "
                f"in REAPER and select '{shown_as}'."
            ),
        }
    return envelope, None


def register_tools(mcp):

    @mcp.tool()
    def add_volume_automation(track_index: int, position: float, value_db: float) -> dict:
        """Add a volume automation point on a track.
        
        The volume envelope must be visible in REAPER.
        position: time in seconds. value_db: volume level in dB.
        """
        try:
            invalid = _negative_index(track_index=track_index)
            if invalid:
                return {"success": False, "error": invalid}
            if position < 0:
                return {"success": False, "error": f"position must be 0 or greater, got {position}"}
            project = get_project()
            track = project.tracks[track_index]
            envelope, problem = _envelope_or_error(
                track, "Volume", "Show envelope for track volume"
            )
            if problem:
                return problem

            before = RPR.CountEnvelopePoints(envelope)
            value = _scaled_for(envelope, _db_to_linear(value_db))
            RPR.InsertEnvelopePoint(envelope, position, value, 0, 0, False, True)
            RPR.Envelope_SortPoints(envelope)
            after = RPR.CountEnvelopePoints(envelope)
            if after <= before:
                return {
                    "success": False,
                    "error": f"REAPER retained {after} envelope points. The point was not added.",
                }
            return {
                "success": True,
                "track_index": track_index,
                "position": position,
                "value_db": value_db,
                "envelope_points": after,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def add_pan_automation(track_index: int, position: float, pan: float) -> dict:
        """Add a pan automation point on a track.
        
        The pan envelope must be visible in REAPER.
        pan: -1.0 (full left) to 1.0 (full right).
        """
        try:
            invalid = _negative_index(track_index=track_index)
            if invalid:
                return {"success": False, "error": invalid}
            if position < 0:
                return {"success": False, "error": f"position must be 0 or greater, got {position}"}
            if not -1.0 <= pan <= 1.0:
                return {"success": False, "error": f"pan must be -1.0 to 1.0, got {pan}"}
            project = get_project()
            track = project.tracks[track_index]
            envelope, problem = _envelope_or_error(
                track, "Pan", "Show envelope for track pan"
            )
            if problem:
                return problem

            before = RPR.CountEnvelopePoints(envelope)
            RPR.InsertEnvelopePoint(envelope, position, _scaled_for(envelope, pan), 0, 0, False, True)
            RPR.Envelope_SortPoints(envelope)
            after = RPR.CountEnvelopePoints(envelope)
            if after <= before:
                return {
                    "success": False,
                    "error": f"REAPER retained {after} envelope points. The point was not added.",
                }
            return {
                "success": True,
                "track_index": track_index,
                "position": position,
                "pan": pan,
                "envelope_points": after,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def create_send(
        source_track_index: int, dest_track_index: int, volume_db: float = 0.0
    ) -> dict:
        """Create an aux send from one track to another."""
        try:
            invalid = _negative_index(
                source_track_index=source_track_index, dest_track_index=dest_track_index
            )
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            src = project.tracks[source_track_index]
            dst = project.tracks[dest_track_index]
            send_idx = RPR.CreateTrackSend(src.id, dst.id)
            if send_idx < 0:
                return {"success": False, "error": "Failed to create send."}
            RPR.SetTrackSendInfo_Value(src.id, 0, send_idx, "D_VOL", _db_to_linear(volume_db))
            return {
                "success": True,
                "source_track_index": source_track_index,
                "dest_track_index": dest_track_index,
                "send_index": send_idx,
                "volume_db": volume_db,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def list_sends(track_index: int) -> dict:
        """List all sends from a track."""
        try:
            invalid = _negative_index(track_index=track_index)
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[track_index]
            n = RPR.GetTrackNumSends(track.id, 0)
            sends = []
            for i in range(n):
                vol = RPR.GetTrackSendInfo_Value(track.id, 0, i, "D_VOL")
                pan = RPR.GetTrackSendInfo_Value(track.id, 0, i, "D_PAN")
                muted = bool(RPR.GetTrackSendInfo_Value(track.id, 0, i, "B_MUTE"))
                # The destination is what distinguishes one send from another. Without
                # it a list of sends cannot be told apart or acted on.
                dest = _track_index_of(
                    project, RPR.GetTrackSendInfo_Value(track.id, 0, i, "P_DESTTRACK")
                )
                sends.append({
                    "send_index": i,
                    "dest_track_index": dest,
                    "volume_db": linear_to_db(vol),
                    "volume_linear": vol,
                    "pan": pan,
                    "muted": muted,
                })
            return {"success": True, "track_index": track_index, "sends": sends}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def remove_send(source_track_index: int, send_index: int) -> dict:
        """Remove a send from a track by its index."""
        try:
            invalid = _negative_index(
                source_track_index=source_track_index, send_index=send_index
            )
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[source_track_index]

            # RemoveTrackSend reports whether the send was actually removed. Without
            # this check an out-of-range index still returned success.
            if not RPR.RemoveTrackSend(track.id, 0, send_index):
                return {
                    "success": False,
                    "error": f"REAPER did not remove send {send_index} from track {source_track_index}",
                }
            return {"success": True, "source_track_index": source_track_index, "send_index": send_index}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def set_send_volume(source_track_index: int, send_index: int, volume_db: float) -> dict:
        """Set the volume of a send in dB."""
        try:
            invalid = _negative_index(
                source_track_index=source_track_index, send_index=send_index
            )
            if invalid:
                return {"success": False, "error": invalid}
            project = get_project()
            track = project.tracks[source_track_index]

            # SetTrackSendInfo_Value reports nothing for an index that does not exist,
            # so the count is checked first rather than reporting a write that no send
            # ever received.
            n = RPR.GetTrackNumSends(track.id, 0)
            if send_index >= n:
                return {
                    "success": False,
                    "error": f"track {source_track_index} has only {n} sends",
                }

            RPR.SetTrackSendInfo_Value(track.id, 0, send_index, "D_VOL", _db_to_linear(volume_db))
            applied = RPR.GetTrackSendInfo_Value(track.id, 0, send_index, "D_VOL")
            return {
                "success": True,
                "source_track_index": source_track_index,
                "send_index": send_index,
                "volume_db": linear_to_db(applied),
                "requested_db": volume_db,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def create_bus(name: str, track_indices: list) -> dict:
        """Create a new bus track and route the specified tracks to it via sends.
        
        track_indices: list of track indices to route into the bus.
        """
        try:
            project = get_project()

            # Every source is checked before the bus exists. Validating inside the
            # routing loop left the new track, and any sends made before the bad index,
            # behind in the project on failure.
            if not track_indices:
                return {"success": False, "error": "track_indices must name at least one track"}
            for idx in track_indices:
                if not isinstance(idx, int) or isinstance(idx, bool):
                    return {"success": False, "error": f"track index must be a whole number, got {idx!r}"}
                if not 0 <= idx < project.n_tracks:
                    return {
                        "success": False,
                        "error": f"track index {idx} out of range, project has {project.n_tracks} tracks",
                    }

            bus_idx = project.n_tracks
            project.add_track(bus_idx, name)
            bus_track = project.tracks[bus_idx]
            sends = []
            for idx in track_indices:
                src = project.tracks[idx]
                send_i = RPR.CreateTrackSend(src.id, bus_track.id)
                if send_i < 0:
                    return {
                        "success": False,
                        "error": f"REAPER refused a send from track {idx} into '{name}'",
                        "bus_index": bus_idx,
                    }
                sends.append({"track_index": idx, "send_index": send_i})
            return {
                "success": True,
                "bus_index": bus_idx,
                "bus_name": name,
                "sends": sends,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
