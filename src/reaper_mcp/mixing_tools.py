import logging

from reaper_mcp.connection import RPR, get_project, reapy, records_undo, undo_step
from reaper_mcp.units import (
    format_channels,
    linear_to_db,
    parse_channels,
    send_dest_channels,
    send_source_channels,
    send_source_value,
)

logger = logging.getLogger("reaper_mcp.mixing_tools")

# I_SENDMODE values. 2 is a legacy post-FX value REAPER still reads as pre-fader.
_SEND_MODES = {"post-fader": 0, "pre-fx": 1, "pre-fader": 3}
_MODE_NAMES = {0: "post-fader", 1: "pre-fx", 2: "pre-fader", 3: "pre-fader"}


def _send_routing(track_id, send_index: int) -> dict:
    """Read a send's mode and channels in the form set_send_routing takes."""
    value = lambda key: RPR.GetTrackSendInfo_Value(track_id, 0, send_index, key)  # noqa: E731
    first, count = send_source_channels(value("I_SRCCHAN"))
    routing = {
        "mode": _MODE_NAMES.get(int(value("I_SENDMODE")), str(int(value("I_SENDMODE")))),
        "src_channels": format_channels(first, count) if count else "none",
    }
    if count:
        routing["dest_channels"] = format_channels(*send_dest_channels(value("I_DSTCHAN"), count))
    return routing


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
    @records_undo()
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
    @records_undo()
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
            # Pan envelopes store pan inverted: a point of 0.5 displays as 50%L, where a
            # track's D_PAN of 0.5 is 50%R. Writing pan unchanged put it on the other side.
            RPR.InsertEnvelopePoint(envelope, position, _scaled_for(envelope, -pan), 0, 0, False, True)
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
    @records_undo()
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
                    **_send_routing(track.id, i),
                })
            return {"success": True, "track_index": track_index, "sends": sends}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo()
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
    @records_undo()
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
    @records_undo(own_step=True)
    def set_send_routing(
        source_track_index: int,
        send_index: int,
        mode: str | None = None,
        src_channels: str | None = None,
        dest_channels: str | None = None,
        mute: bool | None = None,
        pan: float | None = None,
    ) -> dict:
        """Set a send's mode, channels, mute or pan; omitted settings stay as they are.

        mode: post-fader, pre-fader (post-FX) or pre-fx.
        src_channels: "1/2" pair, "3" mono, "1-4" multichannel, or "none" for no audio.
        dest_channels: "3/4", or one channel such as "3" (a stereo source is mixed to mono).
        The destination track gets more channels when needed, e.g. a sidechain into 3/4. Check the FX side with get_fx_pins.
        """
        try:
            invalid = _negative_index(source_track_index=source_track_index, send_index=send_index)
            if invalid:
                return {"success": False, "error": invalid}
            if mode is not None and mode not in _SEND_MODES:
                return {"success": False, "error": f"mode must be one of {', '.join(_SEND_MODES)}, got {mode!r}"}
            if pan is not None and not -1.0 <= pan <= 1.0:
                return {"success": False, "error": f"pan must be -1.0 to 1.0, got {pan}"}
            try:
                source = None
                if src_channels is not None and src_channels.strip().lower() != "none":
                    source = parse_channels(src_channels)
                    if source[1] > 2 and source[1] % 2:
                        raise ValueError(f"a multichannel send carries an even number of channels, got {src_channels!r}")
                dest = parse_channels(dest_channels) if dest_channels is not None else None
            except ValueError as e:
                return {"success": False, "error": str(e)}
            project = get_project()
            with undo_step("set_send_routing"):
                n_tracks = RPR.CountTracks(0)
                if source_track_index >= n_tracks:
                    return {"success": False, "error": f"source_track_index {source_track_index} out of range, project has {n_tracks} tracks"}
                track = RPR.GetTrack(0, source_track_index)
                n = RPR.GetTrackNumSends(track, 0)
                if send_index >= n:
                    return {"success": False, "error": f"track {source_track_index} has only {n} sends"}
                dest_index = _track_index_of(
                    project, RPR.GetTrackSendInfo_Value(track, 0, send_index, "P_DESTTRACK")
                )
                if dest_index < 0:
                    return {"success": False, "error": f"send {send_index} has no destination track"}
                dest_id = RPR.GetTrack(0, dest_index)

                if src_channels is not None and source is None:
                    source_value = -1
                    source_count = 0
                elif source is not None:
                    track_channels = int(RPR.GetMediaTrackInfo_Value(track, "I_NCHAN"))
                    if source[0] + source[1] - 1 > track_channels:
                        return {
                            "success": False,
                            "error": f"track {source_track_index} has {track_channels} channels, so it cannot send {src_channels}",
                        }
                    source_value = send_source_value(*source)
                    source_count = source[1]
                else:
                    source_value = None
                    source_count = send_source_channels(RPR.GetTrackSendInfo_Value(track, 0, send_index, "I_SRCCHAN"))[1]

                dest_value = None
                if dest is not None:
                    if source_count == 0:
                        return {"success": False, "error": "this send carries no audio; set src_channels as well"}
                    if dest[1] == 1:
                        # One destination channel: a mono source lands on it; a wider source is mixed down.
                        dest_value = (dest[0] - 1) | (1024 if source_count > 1 else 0)
                    elif dest[1] == source_count:
                        dest_value = dest[0] - 1
                    else:
                        return {
                            "success": False,
                            "error": f"the source carries {source_count} channels, so dest_channels must be {source_count} channels or one",
                        }

                raised_from = None
                last_dest = None
                if dest_value is not None:
                    last_dest = dest[0] + dest[1] - 1
                elif source_value is not None and source_count:
                    first_dest, dest_count = send_dest_channels(
                        RPR.GetTrackSendInfo_Value(track, 0, send_index, "I_DSTCHAN"), source_count
                    )
                    last_dest = first_dest + dest_count - 1
                dest_channels_now = int(RPR.GetMediaTrackInfo_Value(dest_id, "I_NCHAN"))
                if last_dest and last_dest > dest_channels_now:
                    raised_from = dest_channels_now
                    RPR.SetMediaTrackInfo_Value(dest_id, "I_NCHAN", last_dest + last_dest % 2)

                for key, value in (
                    ("I_SENDMODE", _SEND_MODES.get(mode)),
                    ("I_SRCCHAN", source_value),
                    ("I_DSTCHAN", dest_value),
                    ("B_MUTE", None if mute is None else int(mute)),
                    ("D_PAN", pan),
                ):
                    if value is not None and not RPR.SetTrackSendInfo_Value(track, 0, send_index, key, value):
                        return {"success": False, "error": f"REAPER refused {key} = {value}"}

                got = _send_routing(track, send_index)
                got["mute"] = bool(RPR.GetTrackSendInfo_Value(track, 0, send_index, "B_MUTE"))
                got["pan"] = RPR.GetTrackSendInfo_Value(track, 0, send_index, "D_PAN")
                dest_channels_after = int(RPR.GetMediaTrackInfo_Value(dest_id, "I_NCHAN"))

            wrong = []
            if mode is not None and got["mode"] != mode:
                wrong.append("mode")
            if src_channels is not None and got["src_channels"] != (format_channels(*source) if source else "none"):
                wrong.append("src_channels")
            if dest is not None and got.get("dest_channels") != format_channels(*dest):
                wrong.append("dest_channels")
            if mute is not None and got["mute"] != mute:
                wrong.append("mute")
            if pan is not None and abs(got["pan"] - pan) > 1e-6:
                wrong.append("pan")
            if last_dest and dest_channels_after < last_dest:
                wrong.append("destination track channels")
            result = {
                "success": not wrong,
                "source_track_index": source_track_index,
                "send_index": send_index,
                "dest_track_index": dest_index,
                **got,
                "dest_track_channels": dest_channels_after,
            }
            if raised_from is not None:
                result["dest_track_channels_raised_from"] = raised_from
            if wrong:
                result["error"] = f"REAPER kept different {', '.join(wrong)}"
            return result
        except Exception as e:
            logger.error(f"set_send_routing failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo()
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
