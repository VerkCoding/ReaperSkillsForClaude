"""Batch edits on media items: move, resize, fade, mute, gain, split, delete.

A track lists its items in time order and REAPER re-sorts that list the moment
an item's position changes, so an index read before a move names a different
item afterwards. Every entry is therefore resolved to its item pointer before
anything changes, and the indices reported back are read after the last change.
"""

import logging

from reaper_mcp.connection import RPR, get_project, is_null, undo_step
from reaper_mcp.units import db_to_linear, linear_to_db

logger = logging.getLogger("reaper_mcp.item_tools")

_MAX_ENTRIES = 500
_EPS = 1e-9
_EDITS = {
    "position": "D_POSITION",
    "length": "D_LENGTH",
    "fade_in": "D_FADEINLEN",
    "fade_out": "D_FADEOUTLEN",
}


def _whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _seconds(entry: dict, key: str, positive: bool = False):
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0 or (positive and value == 0):
        raise ValueError(f"{key} must be seconds, {'above' if positive else 'at least'} 0, got {value!r}")
    return float(value)


def _describe(item) -> dict:
    """Read an item's place and settings back from REAPER."""
    value = lambda key: RPR.GetMediaItemInfo_Value(item, key)  # noqa: E731
    track = RPR.GetMediaItem_Track(item)
    return {
        "track_index": int(RPR.GetMediaTrackInfo_Value(track, "IP_TRACKNUMBER")) - 1,
        "item_index": int(value("IP_ITEMNUMBER")),
        "position": value("D_POSITION"),
        "length": value("D_LENGTH"),
        "fade_in": value("D_FADEINLEN"),
        "fade_out": value("D_FADEOUTLEN"),
        "volume_db": round(linear_to_db(value("D_VOL")), 2),
        "mute": bool(value("B_MUTE")),
    }


def _plan(entry: dict, n_tracks: int):
    """Validate one entry and return (track_index, item_index, action, args) or raise ValueError."""
    if not isinstance(entry, dict):
        raise ValueError("each entry must be an object")
    track_index, item_index = entry.get("track_index"), entry.get("item_index")
    if not _whole(track_index) or not 0 <= track_index < n_tracks:
        raise ValueError(f"track_index must be 0-{n_tracks - 1}, got {track_index!r}")
    if not _whole(item_index) or item_index < 0:
        raise ValueError(f"item_index must be 0 or greater, got {item_index!r}")

    fields = set(entry) - {"track_index", "item_index"}
    if entry.get("delete") is True:
        if fields != {"delete"}:
            raise ValueError("a delete entry takes only track_index, item_index and delete")
        return track_index, item_index, "delete", None
    if "split_at" in fields:
        if fields != {"split_at"}:
            raise ValueError("split_at cannot be combined with other changes in one entry")
        times = entry["split_at"]
        times = times if isinstance(times, list) else [times]
        if not times or not all(isinstance(t, (int, float)) and not isinstance(t, bool) for t in times):
            raise ValueError(f"split_at must be a time or a list of times in seconds, got {entry['split_at']!r}")
        return track_index, item_index, "split", sorted(float(t) for t in times)

    unknown = fields - set(_EDITS) - {"volume_db", "mute", "dest_track_index"}
    if unknown or not fields:
        raise ValueError(f"unknown or missing fields: {', '.join(sorted(unknown)) or 'nothing to change'}")
    edits = {key: _seconds(entry, key, positive=(key == "length")) for key in _EDITS if key in entry}
    if "volume_db" in entry:
        db = entry["volume_db"]
        if not isinstance(db, (int, float)) or isinstance(db, bool) or db > 24:
            raise ValueError(f"volume_db must be a number up to 24, got {db!r}")
        edits["volume_db"] = float(db)
    if "mute" in entry:
        if not isinstance(entry["mute"], bool):
            raise ValueError(f"mute must be true or false, got {entry['mute']!r}")
        edits["mute"] = entry["mute"]
    if "dest_track_index" in entry:
        dest = entry["dest_track_index"]
        if not _whole(dest) or not 0 <= dest < n_tracks:
            raise ValueError(f"dest_track_index must be 0-{n_tracks - 1}, got {dest!r}")
        edits["dest_track_index"] = dest
    return track_index, item_index, "edit", edits


def register_tools(mcp):

    @mcp.tool()
    def edit_items(entries: list[dict]) -> dict:
        """Move, resize, fade, mute, set gain on, split or delete media items in one call.

        entries: {"track_index": t, "item_index": i, ...} with any of position, length, fade_in, fade_out (seconds),
        volume_db, mute, dest_track_index (move to another track); or "split_at": [seconds, ...]; or "delete": true.
        Indices refer to the project before this call. Each item is read back; results give its new indices.
        """
        try:
            if not isinstance(entries, list) or not entries:
                return {"success": False, "error": "entries must be a non-empty list"}
            if len(entries) > _MAX_ENTRIES:
                return {"success": False, "error": f"at most {_MAX_ENTRIES} entries per call"}
            get_project()
            errors, planned = [], []
            with undo_step("edit_items"):
                n_tracks = RPR.CountTracks(0)
                for i, entry in enumerate(entries):
                    try:
                        track_index, item_index, action, args = _plan(entry, n_tracks)
                        track = RPR.GetTrack(0, track_index)
                        n_items = RPR.CountTrackMediaItems(track)
                        if item_index >= n_items:
                            raise ValueError(f"track {track_index} has {n_items} items, no item_index {item_index}")
                    except ValueError as e:
                        errors.append({"entry": i, "error": str(e)})
                        continue
                    planned.append((i, RPR.GetTrackMediaItem(track, item_index), action, args))

                # Deletes go last so no later entry acts on an item already gone.
                planned.sort(key=lambda p: p[2] == "delete")
                done = []
                deleted = set()
                for i, item, action, args in planned:
                    if item in deleted:
                        errors.append({"entry": i, "error": "the item was deleted by an earlier entry"})
                        continue
                    if action == "delete":
                        track = RPR.GetMediaItem_Track(item)
                        before = RPR.CountTrackMediaItems(track)
                        if not RPR.DeleteTrackMediaItem(track, item) or RPR.CountTrackMediaItems(track) != before - 1:
                            errors.append({"entry": i, "error": "REAPER did not delete the item"})
                            continue
                        deleted.add(item)
                        done.append((i, "delete", None, None))
                    elif action == "split":
                        pieces, piece = [item], item
                        for t in args:
                            start = RPR.GetMediaItemInfo_Value(piece, "D_POSITION")
                            if not start + _EPS < t < start + RPR.GetMediaItemInfo_Value(piece, "D_LENGTH") - _EPS:
                                errors.append({"entry": i, "error": f"{t}s is not inside the item"})
                                break
                            right = RPR.SplitMediaItem(piece, t)
                            if is_null(right):
                                errors.append({"entry": i, "error": f"REAPER did not split at {t}s"})
                                break
                            pieces.append(right)
                            piece = right
                        done.append((i, "split", pieces, args))
                    else:
                        for key, parm in _EDITS.items():
                            if key in args:
                                RPR.SetMediaItemInfo_Value(item, parm, args[key])
                        if "volume_db" in args:
                            RPR.SetMediaItemInfo_Value(item, "D_VOL", db_to_linear(args["volume_db"]))
                        if "mute" in args:
                            RPR.SetMediaItemInfo_Value(item, "B_MUTE", 1 if args["mute"] else 0)
                        if "dest_track_index" in args:
                            if not RPR.MoveMediaItemToTrack(item, RPR.GetTrack(0, args["dest_track_index"])):
                                errors.append({"entry": i, "error": "REAPER did not move the item to the track"})
                        done.append((i, "edit", item, args))

                RPR.UpdateArrange()
                # Read back once everything has moved, so the indices are final.
                results = []
                for i, action, target, args in done:
                    if action == "delete":
                        results.append({"entry": i, "deleted": True})
                        continue
                    if action == "split":
                        pieces = [_describe(p) for p in target if p not in deleted]
                        got = [p["position"] for p in pieces[1:]]
                        if len(got) == len(args) and any(abs(a - b) > 1e-6 for a, b in zip(got, args)):
                            errors.append({"entry": i, "error": f"pieces start at {got}, asked for {args}"})
                        results.append({"entry": i, "pieces": pieces})
                        continue
                    if target in deleted:
                        results.append({"entry": i, "deleted": True})
                        continue
                    got = _describe(target)
                    wrong = [k for k in _EDITS if k in args and abs(got[k] - args[k]) > 1e-6]
                    if "volume_db" in args and abs(got["volume_db"] - args["volume_db"]) > 0.01:
                        wrong.append("volume_db")
                    if "mute" in args and got["mute"] != args["mute"]:
                        wrong.append("mute")
                    if "dest_track_index" in args and got["track_index"] != args["dest_track_index"]:
                        wrong.append("track")
                    if wrong:
                        errors.append({"entry": i, "error": f"REAPER kept different {', '.join(wrong)}: {got}"})
                    results.append({"entry": i, **got})
            results.sort(key=lambda r: r["entry"])
            result = {"success": not errors, "results": results, "errors": errors}
            if errors:
                result["error"] = f"{len(errors)} of {len(entries)} entries failed; see errors"
            return result
        except Exception as e:
            logger.error(f"edit_items failed: {e}")
            return {"success": False, "error": str(e)}
