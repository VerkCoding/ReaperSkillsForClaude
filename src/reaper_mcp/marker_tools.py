"""Markers, regions, the time selection and the loop.

Markers are addressed through REAPER 7's ProjectMarker API rather than
EnumProjectMarkers3: Python ReaScript cannot return the name that call writes
into its char** argument, so every marker read through it comes back unnamed.

A marker's index is its place in time order, so moving one renumbers the rest,
and REAPER lets two markers share a displayed number. Batch edits therefore
resolve every index to the marker's GUID before changing anything.
"""

import logging

from reaper_mcp.connection import RPR, get_project, held, is_null, records_undo, undo_step
from reaper_mcp.units import native_color, rgb_color

logger = logging.getLogger("reaper_mcp.marker_tools")

_MAX_ENTRIES = 500
_EPS = 1e-9
_ADD_FIELDS = {"position", "start", "end", "name", "color"}
_EDIT_FIELDS = _ADD_FIELDS | {"index", "delete"}


def _marker(guid: str):
    pointer = RPR.GetRegionOrMarker(0, -1, guid)
    return None if is_null(pointer) else pointer


def _guid(pointer) -> str:
    return RPR.GetSetRegionOrMarkerInfo_String(0, pointer, "GUID", "", False)[4]


def _set(guid: str, key: str, value) -> None:
    """Write one field, finding the marker by GUID again since a move re-sorts the list."""
    if key == "P_NAME":
        RPR.GetSetRegionOrMarkerInfo_String(0, _marker(guid), key, value, True)
    else:
        RPR.SetRegionOrMarkerInfo_Value(0, _marker(guid), key, value)


def _read(pointer) -> dict:
    """Describe one marker or region as the tools report it."""
    value = lambda key: RPR.GetRegionOrMarkerInfo_Value(0, pointer, key)  # noqa: E731
    entry = {
        "index": int(value("I_INDEX")),
        "number": int(value("I_NUMBER")),
        "name": RPR.GetSetRegionOrMarkerInfo_String(0, pointer, "P_NAME", "", False)[4],
    }
    if value("B_ISREGION"):
        entry["region"] = True
        entry["start"] = value("D_STARTPOS")
        entry["end"] = value("D_ENDPOS")
    else:
        entry["position"] = value("D_STARTPOS")
    color = rgb_color(value("I_CUSTOMCOLOR"))
    if color:
        entry["color"] = color
    return entry


def _number(entry: dict, key: str, minimum: float = 0.0):
    value = entry.get(key)
    if value is None:
        return None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"{key} must be seconds, {minimum} or more, got {value!r}")
    return float(value)


def _check_time_range(start: float, end: float) -> str:
    if start < 0 or end < start:
        return f"need 0 <= start <= end, got {start} and {end}"
    return ""


def _time_range(is_loop: bool) -> tuple:
    out = RPR.GetSet_LoopTimeRange2(0, False, is_loop, 0.0, 0.0, False)
    return out[3], out[4]


def register_tools(mcp):

    @mcp.tool()
    def list_markers(max_results: int = 500) -> dict:
        """List markers and regions in time order. index is what edit_markers takes; number is the ID REAPER displays."""
        try:
            if max_results < 1:
                return {"success": False, "error": f"max_results must be 1 or more, got {max_results}"}
            get_project()
            with held():
                count = RPR.GetNumRegionsOrMarkers(0)
                entries = [_read(RPR.GetRegionOrMarker(0, i, "")) for i in range(min(count, max_results))]
            return {"success": True, "count": count, "markers": entries, "truncated": count > max_results}
        except Exception as e:
            logger.error(f"list_markers failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo(own_step=True)
    def add_markers(entries: list[dict]) -> dict:
        """Add markers and regions in one call.

        entries: marker {"position": s, "name": "", "color": [r, g, b]}; region {"start": s, "end": s, "name": "", "color": [r, g, b]}.
        Returns each new marker's index and number, read back from REAPER.
        """
        try:
            if not isinstance(entries, list) or not entries:
                return {"success": False, "error": "entries must be a non-empty list"}
            if len(entries) > _MAX_ENTRIES:
                return {"success": False, "error": f"at most {_MAX_ENTRIES} entries per call"}
            get_project()
            errors, made = [], []
            with undo_step("add_markers"):
                for i, entry in enumerate(entries):
                    try:
                        if not isinstance(entry, dict):
                            raise ValueError("each entry must be an object")
                        if set(entry) - _ADD_FIELDS:
                            raise ValueError(f"unknown fields: {', '.join(sorted(set(entry) - _ADD_FIELDS))}")
                        position, start, end = (_number(entry, k) for k in ("position", "start", "end"))
                        if position is not None and (start is not None or end is not None):
                            raise ValueError("give position for a marker, or start and end for a region")
                        if position is None:
                            if start is None or end is None:
                                raise ValueError("a marker needs position; a region needs start and end")
                            if end <= start:
                                raise ValueError(f"region end {end} must be after start {start}")
                        name = entry.get("name", "")
                        if not isinstance(name, str):
                            raise ValueError(f"name must be text, got {name!r}")
                        color = native_color(entry["color"]) if entry.get("color") is not None else 0
                    except ValueError as e:
                        errors.append({"entry": i, "error": str(e)})
                        continue
                    region = position is None
                    pointer = RPR.AddRegionOrMarker(
                        0, region, start if region else position, end if region else position, name, -1, color
                    )
                    if is_null(pointer):
                        errors.append({"entry": i, "error": "REAPER refused to add it"})
                        continue
                    made.append((i, _guid(pointer), entry, region, color))

                # Indices are final only once every entry is in, so read back afterwards.
                results = []
                for i, guid, entry, region, color in made:
                    pointer = _marker(guid)
                    if pointer is None:
                        errors.append({"entry": i, "error": "added, but REAPER no longer finds it"})
                        continue
                    got = _read(pointer)
                    want = {"start": entry.get("start"), "end": entry.get("end")} if region else {"position": entry.get("position")}
                    want["name"] = entry.get("name", "")
                    wrong = [k for k, v in want.items() if (abs(got.get(k, -1) - v) > _EPS if k != "name" else got[k] != v)]
                    if color and got.get("color") != entry["color"]:
                        wrong.append("color")
                    if wrong:
                        errors.append({"entry": i, "error": f"REAPER stored different {', '.join(wrong)}: {got}"})
                    results.append({"entry": i, **got})
            result = {"success": not errors, "added": results, "errors": errors, "count": RPR.GetNumRegionsOrMarkers(0)}
            if errors:
                result["error"] = f"{len(errors)} of {len(entries)} entries failed; see errors"
            return result
        except Exception as e:
            logger.error(f"add_markers failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo(own_step=True)
    def edit_markers(entries: list[dict]) -> dict:
        """Edit or delete markers and regions in one call.

        entries: {"index": i, "name": "", "position": s} for a marker, {"index": i, "start": s, "end": s} for a region,
        "color": [r, g, b] or null to clear, or {"index": i, "delete": true}. Only index is required.
        Indices refer to list_markers before this call; moves and deletes within the batch do not shift them.
        """
        try:
            if not isinstance(entries, list) or not entries:
                return {"success": False, "error": "entries must be a non-empty list"}
            if len(entries) > _MAX_ENTRIES:
                return {"success": False, "error": f"at most {_MAX_ENTRIES} entries per call"}
            get_project()
            errors, edited, deletes = [], [], []
            with undo_step("edit_markers"):
                count = RPR.GetNumRegionsOrMarkers(0)
                guids = [_guid(RPR.GetRegionOrMarker(0, i, "")) for i in range(count)]

                for i, entry in enumerate(entries):
                    try:
                        if not isinstance(entry, dict):
                            raise ValueError("each entry must be an object")
                        if set(entry) - _EDIT_FIELDS:
                            raise ValueError(f"unknown fields: {', '.join(sorted(set(entry) - _EDIT_FIELDS))}")
                        index = entry.get("index")
                        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
                            raise ValueError(f"index must be 0-{count - 1}, got {index!r}")
                        if entry.get("delete") is True:
                            if set(entry) - {"index", "delete"}:
                                raise ValueError("a delete entry takes only index and delete")
                            deletes.append((i, guids[index]))
                            continue
                        pointer = _marker(guids[index])
                        if pointer is None:
                            raise ValueError(f"marker {index} no longer exists")
                        region = bool(RPR.GetRegionOrMarkerInfo_Value(0, pointer, "B_ISREGION"))
                        position, start, end = (_number(entry, k) for k in ("position", "start", "end"))
                        if region and position is not None:
                            raise ValueError(f"marker {index} is a region: use start and end, not position")
                        if not region and (start is not None or end is not None):
                            raise ValueError(f"marker {index} is not a region: use position, not start or end")
                        name = entry.get("name")
                        if name is not None and not isinstance(name, str):
                            raise ValueError(f"name must be text, got {name!r}")
                        color = None
                        if "color" in entry:
                            color = native_color(entry["color"]) if entry["color"] is not None else 0
                        if region:
                            old_start = RPR.GetRegionOrMarkerInfo_Value(0, pointer, "D_STARTPOS")
                            old_end = RPR.GetRegionOrMarkerInfo_Value(0, pointer, "D_ENDPOS")
                            new_start = old_start if start is None else start
                            new_end = old_end if end is None else end
                            if new_end <= new_start:
                                raise ValueError(f"region end {new_end} must be after start {new_start}")
                    except ValueError as e:
                        errors.append({"entry": i, "error": str(e)})
                        continue

                    guid = guids[index]
                    if name is not None:
                        _set(guid, "P_NAME", name)
                    if color is not None:
                        _set(guid, "I_CUSTOMCOLOR", color)
                    if position is not None:
                        _set(guid, "D_STARTPOS", position)
                        _set(guid, "D_ENDPOS", position)
                    if region and (start is not None or end is not None):
                        # A start set past the current end swaps the two, so the
                        # bound that moves away from the other one goes first.
                        order = (("D_ENDPOS", new_end), ("D_STARTPOS", new_start))
                        if new_start < old_end:
                            order = order[::-1]
                        for key, value in order:
                            _set(guid, key, value)
                    want = {}
                    if name is not None:
                        want["name"] = name
                    if position is not None:
                        want["position"] = position
                    if region and (start is not None or end is not None):
                        want["start"], want["end"] = new_start, new_end
                    edited.append((i, guid, want, entry.get("color", ...)))

                removed = 0
                for i, guid in deletes:
                    pointer = _marker(guid)
                    if pointer is None:
                        errors.append({"entry": i, "error": "already deleted earlier in this call"})
                        continue
                    index = int(RPR.GetRegionOrMarkerInfo_Value(0, pointer, "I_INDEX"))
                    if not RPR.DeleteProjectMarkerByIndex(0, index) or _marker(guid) is not None:
                        errors.append({"entry": i, "error": "REAPER did not delete it"})
                        continue
                    removed += 1

                results = []
                deleted_guids = {guid for _, guid in deletes}
                for i, guid, want, color in edited:
                    pointer = _marker(guid)
                    if pointer is None:
                        if guid not in deleted_guids:
                            errors.append({"entry": i, "error": "edited, but REAPER no longer finds it"})
                        continue
                    got = _read(pointer)
                    wrong = [k for k, v in want.items() if (got.get(k) != v if k == "name" else abs(got.get(k, -1) - v) > _EPS)]
                    if color is not ... and got.get("color") != color:
                        wrong.append("color")
                    if wrong:
                        errors.append({"entry": i, "error": f"REAPER stored different {', '.join(wrong)}: {got}"})
                    results.append({"entry": i, **got})
                result = {
                    "success": not errors,
                    "edited": results,
                    "deleted": removed,
                    "errors": errors,
                    "count": RPR.GetNumRegionsOrMarkers(0),
                }
                if errors:
                    result["error"] = f"{len(errors)} of {len(entries)} entries failed; see errors"
                return result
        except Exception as e:
            logger.error(f"edit_markers failed: {e}")
            return {"success": False, "error": str(e)}

    @mcp.tool()
    def get_time_selection() -> dict:
        """Read the time selection, loop points, repeat state and edit cursor (seconds; start == end means none)."""
        try:
            get_project()
            with held():
                start, end = _time_range(False)
                loop_start, loop_end = _time_range(True)
                return {
                    "success": True,
                    "start": start,
                    "end": end,
                    "loop_start": loop_start,
                    "loop_end": loop_end,
                    "repeat": bool(RPR.GetSetRepeatEx(0, -1)),
                    "cursor": RPR.GetCursorPositionEx(0),
                }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @mcp.tool()
    @records_undo(own_step=True)
    def set_time_selection(start: float, end: float, loop: bool = False, repeat: bool | None = None) -> dict:
        """Set the time selection, or the loop points with loop=true; start == end clears it. repeat turns loop playback on or off.

        REAPER links the two by default, so the reply shows both as REAPER now has them.
        """
        try:
            problem = _check_time_range(start, end)
            if problem:
                return {"success": False, "error": problem}
            if end == start:
                start = end = 0.0
            get_project()
            with undo_step("set_time_selection"):
                RPR.GetSet_LoopTimeRange2(0, True, loop, float(start), float(end), False)
                if repeat is not None:
                    RPR.GetSetRepeatEx(0, 1 if repeat else 0)
                got_start, got_end = _time_range(loop)
                other = _time_range(not loop)
                now_repeat = bool(RPR.GetSetRepeatEx(0, -1))
            if abs(got_start - start) > _EPS or abs(got_end - end) > _EPS:
                return {"success": False, "error": f"REAPER holds {got_start}-{got_end}, asked for {start}-{end}"}
            if repeat is not None and now_repeat != repeat:
                return {"success": False, "error": f"repeat stayed {now_repeat}"}
            selection, loop_points = (other, (got_start, got_end)) if loop else ((got_start, got_end), other)
            return {
                "success": True,
                "start": selection[0],
                "end": selection[1],
                "loop_start": loop_points[0],
                "loop_end": loop_points[1],
                "repeat": now_repeat,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
