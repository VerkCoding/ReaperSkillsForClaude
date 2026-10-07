# Driving REAPER from Python

The MCP tools interface with REAPER through reapy, a wrapper for the ReaScript API. Some functions return incorrect results or fail silently without raising errors.

The behaviours below were established by executing the MCP tools against REAPER and then reading REAPER's own state back, either through `reascript_api`, through the Lua bridge, or by inspecting the `.rpp` and rendered audio files the operation produced. A tool's own return value was never accepted as evidence. The first pass, over the original 58 tools in nine modules, found 37 defects; three tools had never worked at all while reporting success on every call. The 13 tools added in 1.2.0 were built on the findings in [Undo Points and Holding REAPER](#undo-points-and-holding-reaper) and the sections after it, measured on REAPER 7.82.

Environment: REAPER 7.79/x64, reapy 0.10.0, Python 3.12.10, numpy 2.5.2, soundfile 0.14.0, librosa 1.0.0, scipy 1.18.0, pyloudnorm.

## Contents

- [API Usage Rules](#api-usage-rules)
- [Verified Reapy Traps](#verified-reapy-traps)
- [Unit Scaling](#unit-scaling)
- [Index Semantics](#index-semantics)
- [Pointer Returns](#pointer-returns)
- [Return Tuple Shapes](#return-tuple-shapes)
- [Modal Dialogs Freeze Everything](#modal-dialogs-freeze-everything)
- [Saving and Opening Projects](#saving-and-opening-projects)
- [Loop Bounding](#loop-bounding)
- [Tempo and Time Signature Markers](#tempo-and-time-signature-markers)
- [Plugin Parameters](#plugin-parameters)
- [Undo Points and Holding REAPER](#undo-points-and-holding-reaper)
- [Envelopes](#envelopes)
- [Markers and Regions](#markers-and-regions)
- [Items, Sends, FX Order and Pins](#items-sends-fx-order-and-pins)
- [Render Settings State](#render-settings-state)
- [Measuring Rendered Audio](#measuring-rendered-audio)
- [Bridge Protocol](#bridge-protocol)
- [What a Call Costs](#what-a-call-costs)
- [Benchmark Script](#benchmark-script)

## API Usage Rules

Write and read using `reascript_api`. Do not rely on reapy attribute assignments.

`FXParam` subclasses `float`. `Track` lacks an `armed` property, `Take` lacks `pitch` and `playback_rate`, and `Item` lacks `name`. Assigning to a name the class does not define creates an ordinary Python attribute on a throwaway wrapper object. No error is raised, REAPER is never told, and reading the same name back returns the value that was just assigned, so the write appears to have succeeded.

```python
fx.params[0].normalized_value = 0.6   # Assigns attribute to a temporary object. State is not updated.
track.armed = True                    # Assigns attribute to a temporary object. State is not updated.
take.pitch = -3.0                     # Assigns attribute to a temporary object. D_PITCH remains 0.0.
```

To update state, use the `reascript_api` functions and read the state back from REAPER, not from the wrapper:

```python
from reapy import reascript_api as RPR

RPR.TrackFX_SetParamNormalized(track.id, fx_index, param_index, value)
applied = RPR.TrackFX_GetParamNormalized(track.id, fx_index, param_index)
return {"value": applied, "requested": value}
```

Returning both values allows the caller to handle cases where a plugin quantizes parameters, resulting in an `applied` value that differs from the `requested` value.

### Confirming a write

A read-back is only evidence if the failure mode produces a different value. These calls report refusal rather than raising:

| Call | Refusal signal | Check |
|---|---|---|
| `TrackFX_GetParamNormalized` | returns `-1.0` | `if applied < 0.0: fail` |
| `TrackFX_Delete` | returns falsy | `if not RPR.TrackFX_Delete(...): fail` |
| `RemoveTrackSend` | returns falsy | `if not RPR.RemoveTrackSend(...): fail` |
| `SetTrackSendInfo_Value` | returns falsy for a send that does not exist | `if not RPR.SetTrackSendInfo_Value(...): fail` |
| `InsertEnvelopePoint` | no return value | compare `CountEnvelopePoints` before and after |
| `InsertMedia` | no usable return | diff the track's item ids before and after |
| `SplitMediaItem` | returns a null pointer outside the item or exactly at its start | `if _is_null(right): fail` |
| `DeleteTrackMediaItem` | returns falsy | check the track's item count dropped by one |
| `TrackFX_CopyToTrack` | no return; a destination past the end moves the FX to the end | validate the slot, then compare FX GUIDs in order |
| `GetRegionOrMarker(0, -1, guid)` | returns a null pointer for an unknown GUID | treat null as deleted |

Reporting `-1.0` as the applied value presents a refused write as a successful one. Treat a negative read-back as failure.

## Verified Reapy Traps

Confirmed by writing through reapy and reading the resulting state from REAPER.

| Input Syntax | Result | Recommended Usage |
|---|---|---|
| `idx = track.add_fx(name)` then `if idx < 0` | Returns an `FX` object, not an int. Comparing raises `'<' not supported between instances of 'FX' and 'int'`. | `fx = track.add_fx(name)`, then read `fx.index`. Missing plugins raise `ValueError`. |
| `param.normalized_value` / `param.formatted_value` | Raises `AttributeError` on read; silent no-op on write. | `param.normalized` / `param.formatted` to read; `RPR.TrackFX_SetParamNormalized` to write. |
| `fx.params[i].normalized = v` | reapy setter reads `parent_fx.id`, which `FX` does not have, raising `AttributeError`. | `RPR.TrackFX_SetParamNormalized(track.id, fx_index, i, v)`. |
| `track.armed = True` | No-op. `I_RECARM` remains 0. | `RPR.SetMediaTrackInfo_Value(track.id, "I_RECARM", 1)`. |
| `take.pitch = x` | **No property exists.** Creates a Python attribute; `D_PITCH` stays 0.0 while the attribute reads back as `x`. | `RPR.SetMediaItemTakeInfo_Value(take.id, "D_PITCH", x)`, then read `GetMediaItemTakeInfo_Value`. |
| `take.playback_rate = x` | **No property exists.** Same silent failure as `pitch`; `D_PLAYRATE` stays 1.0. | `RPR.SetMediaItemTakeInfo_Value(take.id, "D_PLAYRATE", x)`. |
| `item.name` | **No property exists.** Raises `AttributeError`, so any loop over a populated track dies. | `item.active_take.name`, guarded by `item.n_takes` because an item may hold no take. |
| `project.bpm` | Returns the denominator-scaled project *setting*, not quarter-note BPM. See [Unit Scaling](#unit-scaling). | `RPR.Master_GetTempo()`. |
| `project.path` | Returns the recording directory, not the project file. | `RPR.EnumProjects(-1, "", 4096)[2]`, which is `""` for an unsaved project. |
| `project.time_signature` | Returns `(bpm, bpi)`. | `RPR.TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)`. Numerator and denominator are at indices 2 and 3. |
| `project.time_signature = (n, d)` | Read-only property; raises `AttributeError`. | `RPR.SetTempoTimeSigMarker`. |
| `project.save(path)` | Argument is `force_save_as` (bool). Path input raises `TypeError`. | See [Saving and Opening Projects](#saving-and-opening-projects). |
| `project.bpm = x` | Modifies bpm only if no tempo marker exists at position 0. Otherwise, inserts duplicate marker. | Modify the marker directly. |
| `item.fade_in_length` / `item.fade_out_length` | No-op. The fade remains 0.0. | `RPR.SetMediaItemInfo_Value(item.id, "D_FADEINLEN" / "D_FADEOUTLEN", seconds)`. |
| `take.start_offset = x` | Read-only property; raises `AttributeError`. | `RPR.SetMediaItemTakeInfo_Value(take.id, "D_STARTOFFS", value)`. |
| `fx.preset_name = name` | No-op. Accepts non-existent preset names. | `RPR.TrackFX_SetPreset(track.id, fx, name)`, read `TrackFX_GetPreset(...)[3]`. |
| `item.position` / `item.length` / `fx.is_enabled` / `take.name` | Functions as documented. | Use these properties. |

Most issues relate to reapy implementing getters but omitting or implementing non-functional setters. The `pitch`, `playback_rate` and `name` cases are worse than a no-op setter: because no property exists at all, the getter returns the assigned attribute and confirms a change that never happened.

## Unit Scaling

Two values do not mean what their names suggest. Both produced silently wrong output because the write path was correct and only the read was skewed.

### Tempo is scaled by the time signature denominator

`project.bpm` reads `GetProjectTimeSignature2`, which returns the project BPM *setting*. REAPER scales that by the denominator:

```
reported = true_quarter_note_bpm * (denominator / 4)
```

Measured on a project whose `.rpp` records `TEMPO 120`:

| Signature | `project.bpm` | `Master_GetTempo()` | `.rpp` TEMPO |
|---|---|---|---|
| 4/4 | 120.0 | 120.0 | 120 |
| 7/8 | 240.0 | 120.0 | 120 |
| 3/2 | 60.0 | 120.0 | 120 |

`Master_GetTempo()`, `TimeMap_GetTimeSigAtTime(...)[4]` and `GetTempoTimeSigMarker(...)[6]` all return true quarter-note BPM. So do the setters `SetCurrentBPM` and `SetTempoTimeSigMarker`. Only `project.bpm` is scaled.

Feeding `project.bpm` back into a setter therefore rescales the project on every call. Writing a 7/8 signature three times took a 120 BPM project to 240, then 480. Any duration derived from `60.0 / project.bpm` is wrong by the same factor: MIDI written in 7/8 came out at double speed.

### Envelope points are stored in the envelope's own scaling

Volume envelopes default to fader scaling (`GetEnvelopeScalingMode` returns 1). Writing a raw linear gain stores a value REAPER interprets differently:

```python
# Wrong: -6 dB written as linear 0.501 evaluates to -192 dB, effectively a mute.
RPR.InsertEnvelopePoint(env, pos, 0.501, 0, 0, False, True)

# Right: convert into the envelope's scaling first.
mode = RPR.GetEnvelopeScalingMode(env)
RPR.InsertEnvelopePoint(env, pos, RPR.ScaleToEnvelopeMode(mode, gain), 0, 0, False, True)
```

For a 0.501 linear gain the correctly scaled value is 592.85. Pan envelopes report mode 0, where the conversion is the identity, so applying it unconditionally is safe. Read points back with `ScaleFromEnvelopeMode` before converting to dB.

## Index Semantics

### Negative indices are accepted by one layer and rejected by the other

reapy resolves a negative index the Python way, to the last element. The ReaScript calls reject it and do nothing. Code that looks a name up through reapy and then acts through `reascript_api` reports the last element's name alongside a write that never happened:

```python
fx_name = track.fxs[-1].name        # resolves to the LAST plugin
RPR.TrackFX_Delete(track.id, -1)    # does nothing, returns falsy
return {"success": True, "removed": fx_name}   # a lie
```

Observed: `remove_fx(-1)` reported removing a plugin that was still loaded; `set_fx_parameter(param_index=-1)` returned `value: -1.0` as though it were a parameter value.

The track list is the exception — `project.tracks[-1]` raises `IndexError("Track index out of range")`. FX lists and parameter lists accept negative indices. Validate explicitly rather than relying on either behaviour.

### Items are ordered by position, not by creation

`track.items` follows REAPER's ordering, which is chronological. A newly created item is last only when it starts after every existing item. `track.n_items - 1` therefore names the wrong item whenever something is inserted earlier in the timeline, and names a pre-existing item when nothing was inserted at all.

```python
before = {track.items[i].id for i in range(track.n_items)}
RPR.InsertMedia(file_path, 0)          # or track.add_midi_item(...)
track = project.tracks[track_index]    # re-read after the change
new = [i for i in range(track.n_items) if track.items[i].id not in before]
if not new:
    return {"success": False, "error": "nothing was inserted"}
```

### MIDI values wrap silently

`pitch`, `velocity` and `channel` are stored in seven bits. REAPER keeps the low bits of anything larger: pitch 200 was stored as 72, velocity 300 as 44. Validate 0-127 (0-15 for channel) before writing, because nothing downstream will report the truncation.

`take.add_note(start=..., end=...)` takes seconds relative to the item start.

## Pointer Returns

ReaScript functions return pointers as strings. A null pointer is returned as a string such as `'(TrackEnvelope*)0x0000000000000000'`, which evaluates to True in Python.

```python
envelope = RPR.GetTrackEnvelopeByName(track.id, "Volume")
if not envelope:        # Condition is never met.
```

Checks for null pointers must evaluate the string content.

```python
def _is_null(pointer) -> bool:
    return not pointer or "0x0000000000000000" in str(pointer)
```

Operations on null pointers return 0 and do not modify state.

Pointers are not always strings. `GetTrackSendInfo_Value(track, 0, i, "P_DESTTRACK")` returns a float address, while `Track.id` is a formatted pointer string. Compare them as integers:

```python
address = int(RPR.GetTrackSendInfo_Value(track.id, 0, i, "P_DESTTRACK"))
int(str(other.id).split("0x")[-1].rstrip(")"), 16) == address
```

## Return Tuple Shapes

Python ReaScript returns `(retval, *arguments)` with output parameters filled in. Functions returning `void` omit `retval`. Indices confirmed live:

| Call | Useful indices |
|---|---|
| `EnumProjects(-1, "", 4096)` | `[2]` active project filename |
| `TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)` | `[2]` numerator, `[3]` denominator, `[4]` true tempo |
| `GetTempoTimeSigMarker(0, i, 0,0,0,0,0,0,0)` | `[3]` time position, `[6]` BPM, `[7]` numerator, `[8]` denominator |
| `TrackFX_GetParam(track, fx, p, 0, 0)` | `[0]` value, `[4]` min, `[5]` max |
| `TrackFX_GetParamName(track, fx, p, "", 256)` | `[4]` name |
| `TrackFX_GetFormattedParamValue(track, fx, p, "", 256)` | `[4]` displayed text |
| `TrackFX_GetPreset(track, fx, "", 256)` | `[3]` preset name |
| `MIDI_CountEvts(take, 0, 0, 0)` | `[2]` note count |
| `MIDI_GetNote(take, i, 0,0,0,0,0,0,0)` | `[5]` start ppq, `[6]` end ppq, `[7]` channel, `[8]` pitch, `[9]` velocity |
| `Envelope_Evaluate(env, t, 0, 0, 0, 0, 0, 0)` | `[5]` value. Takes **eight** arguments; fewer raises `TypeError` |
| `GetSetProjectInfo_String(0, key, " " * 1024, False)` | `[3]` value |
| `GetEnvelopePoint(env, i, 0, 0, 0, 0, 0)` | `[3]` time, `[4]` stored value, `[5]` shape, `[6]` tension |
| `Envelope_FormatValue(env, value, "", 64)` | `[2]` display text, such as `-6.02dB` or `50%L` |
| `GetEnvelopeName(env, "", 256)` | `[2]` name |
| `GetSetEnvelopeInfo_String(env, "ACTIVE", "", False)` | `[3]` `"1"` or `"0"`; `VISIBLE` and `ARM` work the same way |
| `TrackFX_GetIOSize(track, fx, 0, 0)` | `[0]` plug-in type, -1 for no such FX; `[3]` input pins, `[4]` output pins |
| `TrackFX_GetPinMappings(track, fx, is_output, pin, 0)` | `[0]` channels 1-32 as bits, `[5]` channels 33-64 |
| `TrackFX_GetNamedConfigParm(track, fx, "in_pin_2", "", 256)` | `[4]` pin name, such as `Auxiliary Input L` |
| `GetSet_LoopTimeRange2(0, False, is_loop, 0, 0, False)` | `[3]` start, `[4]` end |
| `GetSetRegionOrMarkerInfo_String(0, marker, "P_NAME", "", False)` | `[4]` value |
| `CountProjectMarkers(0, 0, 0)` | `[0]` total, `[2]` markers, `[3]` regions |
| `ColorFromNative(color, 0, 0, 0)` | `[1]` red, `[2]` green, `[3]` blue |
| `guidToString(guid_pointer, "")` | `[1]` GUID text. Takes two arguments; three raise `TypeError` |

## Modal Dialogs Freeze Everything

Modal dialogs block REAPER from running deferred scripts, including `activate_reapy_server.py`.

While a modal dialog is active:
- The reapy server blocks MCP tool calls.
- The Lua bridge blocks.
- No error logs are generated.

| Dialog | Text | Trigger Condition | Requires User Input |
|---|---|---|---|
| `Render Error` | "Nothing to render!" | `RENDER_BOUNDSFLAG = 0` | No |
| `Record Warning` | "No tracks are armed for recording" | Transport start with no tracks armed | Yes |
| `REAPER Query` | "Save unsaved project before closing?" | `load_project` / `create_project` on a dirty project | Yes |
| `ReaScript task control` | "…is running in the background" | Server action re-run | Yes |

### Not every hang is a dialog

Two stalls were investigated by enumerating REAPER's top-level windows through the Win32 API. Neither had a dialog open and the process reported `Responding = True` in both cases:

- **Rendering an empty project** with `RENDER_BOUNDSFLAG = 1` writes no file and raises no dialog. The tool then reports its expected output as missing. Check `GetProjectLength(0) <= 0` and return "the project is empty" instead of blaming a modal.
- **Stopping a recording** blocked for roughly 70 seconds while REAPER finalised the take, long enough for the MCP call to time out and the bridge heartbeat to go stale. The stop had succeeded. Report `GetPlayState()` so a caller can distinguish a slow success from a failure.

Leftover non-modal windows are normal: a "Finished in 0:00" render progress window stays open and blocks nothing.

### Dialog Prevention

Check conditions prior to operations that may trigger dialogs.

```python
if RPR.IsProjectDirty(0):
    return {"success": False, "error": "Unsaved project changes detected. Save project to proceed."}
```

`IsProjectDirty` does not see every change. Adding a track sets it; **tempo and time signature writes do not**, so a guarded `create_project` discarded unsaved tempo work without prompting. Call `RPR.MarkProjectDirty(0)` after writing tempo or time signature so the guard can see it.

Validate track arming before recording, and keep `RENDER_BOUNDSFLAG` at 1 or 2.

### Dialog Detection

Dialogs can be detected by enumerating top-level windows owned by the REAPER process ID. The implementation in `scripts/benchmark_tools.py` identifies dialog presence and retrieves child text.

### Thread Execution

Execute REAPER calls on isolated threads with timeouts. Cancelling `asyncio` tasks will leave the event loop blocked if the execution is blocked inside REAPER.

## Saving and Opening Projects

`Main_SaveProjectEx(0, path, options)` writes the file but **does not rebind the open project to it and does not clear the dirty flag**. Options 0, 1 and 2 all behave this way; option 1 additionally produced a much smaller file (2.8 KB against 9.7 KB for the same project), so use 0.

Because the project stays untitled, a follow-up `Main_SaveProject(0, False)` intended to clear the dirty flag saves a *second* time under REAPER's own auto-name, depositing `save.rpp`, `save2.rpp`, … beside the media directory. The project's name then becomes `save2.rpp`, and a later default-path save built from that name produced `save2.rpp.rpp`.

Save to a path like this instead:

```python
current = RPR.EnumProjects(-1, "", 4096)[2]          # "" when never saved

if current and same_file(current, target):
    RPR.Main_SaveProject(0, False)                   # in place: one write, dirty cleared, undo kept
else:
    RPR.Main_SaveProjectEx(0, target, 0)             # writes the file
    RPR.Main_openProject("noprompt:" + target)       # rebinds the project and clears dirty
```

The `noprompt:` prefix suppresses the save-changes modal, which is otherwise the deadlock in the table above. It is the only scriptable way found to discard changes or rebind without user input, and it is equally useful for restoring a known project state between tests. The cost is that reopening resets the undo history, so prefer the in-place branch when the project is already bound to the target.

## Loop Bounding

Loops based on string pointers require null checks and limit bounds.

```python
while RPR.EnumProjects(i, "", 0)[0]:        # Loop does not terminate due to string pointer evaluation.
    i += 1
```

`EnumProjects` returns `'(ReaProject*)0x0000000000000000'` past the end of the list.

```python
def project_tab_count(limit=32):
    for i in range(limit):
        if "0x0000000000000000" in str(RPR.EnumProjects(i, "", 0)[0]):
            return i
    return limit
```

Bound loops that delete elements to prevent infinite execution if deletions fail.

```python
for _ in range(limit):
    count = RPR.CountTempoTimeSigMarkers(0)
    if count == 0:
        break
    RPR.DeleteTempoTimeSigMarker(0, count - 1)
    if RPR.CountTempoTimeSigMarkers(0) == count:
        break          # Deletion failed; exit loop.
```

Run diagnostic scripts with `python -u` or `print(..., flush=True)` to prevent stdout block-buffering during hangs.

## Tempo and Time Signature Markers

REAPER stores time signature data within tempo/time-signature markers.

- Setting a time signature creates a marker at position 0 if one does not exist.
- If a marker exists at position 0, `project.bpm = x` has no effect and creates duplicate markers.

Writes must use the marker API, preserve the other variable, and read the preserved value with `Master_GetTempo` rather than `project.bpm`. Passing `project.bpm` back in is what doubled the tempo of every x/8 project.

```python
def marker_at_zero() -> int:
    for i in range(RPR.CountTempoTimeSigMarkers(0)):
        if abs(RPR.GetTempoTimeSigMarker(0, i, 0, 0, 0, 0, 0, 0, 0)[3]) < 1e-9:
            return i
    return -1          # Value used to insert new marker

num, denom = RPR.TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)[2:4]

# Set tempo, preserve signature
RPR.SetTempoTimeSigMarker(0, marker_at_zero(), 0.0, -1, -1, bpm, num, denom, False)

# Set signature, preserve tempo -- Master_GetTempo, never project.bpm
RPR.SetTempoTimeSigMarker(0, marker_at_zero(), 0.0, -1, -1, RPR.Master_GetTempo(), num, denom, False)

RPR.UpdateTimeline()
RPR.MarkProjectDirty(0)
```

`GetTempoTimeSigMarker` returns the input arguments with modified outputs. Time position is index 3, BPM index 6, numerator index 7, denominator index 8.

Modifying the time signature creates a marker in the REAPER project UI.

## Plugin Parameters

Stock Cockos plugins report a **native range of 0.0 to 1.0** even when they display decibels or milliseconds. `TrackFX_GetParam(...)[4:6]` returns that 0-1 range, not the displayed units, so writing `-3.0` for a threshold with `TrackFX_SetParam` clamps to the minimum and silently sets the wrong value.

reapy 0.10.0 does not expose `TrackFX_SetParamFromString`. To set a parameter in the units a plugin displays, invert its mapping by bisecting on the displayed text:

```python
RPR.TrackFX_SetParamNormalized(t, fx, p, 0.0); at_low = displayed(t, fx, p)
RPR.TrackFX_SetParamNormalized(t, fx, p, 1.0); at_high = displayed(t, fx, p)
increasing = at_high > at_low
low, high = 0.0, 1.0
for _ in range(24):
    mid = (low + high) / 2
    RPR.TrackFX_SetParamNormalized(t, fx, p, mid)
    shown = displayed(t, fx, p)
    if abs(shown - target) <= tolerance:
        break
    if (shown < target) == increasing:
        low = mid
    else:
        high = mid
```

Mappings differ per parameter and are not always increasing. ReaLimit's Threshold is linear from -60 to +12 dB, while its Release runs *backwards* from `inf` down to 6 ms, so the displayed text must be parsed for non-numeric values such as `inf`. Budget roughly two ReaScript round trips per iteration, and return the value the plugin ended up displaying so an out-of-range request reports what was actually achieved.

Parameter counts are not stable: ReaEQ reported 19 parameters before loading a preset and 16 after, because its band count changes. Do not cache parameter indices across preset loads.

## Undo Points and Holding REAPER

### A call from outside REAPER waits for a defer cycle

The reapy server runs as a deferred script, so each call waits for REAPER's next defer cycle: 31 ms per call, measured over 20 calls of `CountTracks`. `reapy.inside_reaper()` makes REAPER serve only this client until the block exits: 0.38 ms per call over 200 calls. Reading 500 markers at six calls each takes about 90 seconds without it and about a second inside it. REAPER does nothing else while held, so a held block must not wait on anything outside REAPER.

### Calls from outside REAPER make no undo point

A write through reapy changes the project without adding an undo step. An undo block spread across separate calls fails differently:

```python
RPR.SetMediaTrackInfo_Value(tr, "D_VOL", 0.7)       # no undo point
RPR.Undo_BeginBlock2(0)
RPR.SetMediaTrackInfo_Value(tr, "D_VOL", 0.6)
RPR.Undo_EndBlock2(0, "MCP: probe block", -1)
# History shows "ReaScript: Run", not "MCP: probe block"; redo restores 0.7, not 0.6.
```

REAPER closed the open block at the end of the first defer cycle, under its own name, holding the state from before the write. Run the whole block inside `inside_reaper()` and the step is named as given and holds the right state. `connection.undo_step()` does this for the tools.

REAPER adds a step only when the project differs from the state saved at the last one. A block that makes no call adds none, and so does a block that writes a value the project already has: setting a volume to 0.3 twice left one step, not two.

### Undoing restores the previous step's whole state

`Undo_DoUndo2` does not reverse the last step alone: it restores the full state saved at the step before. A write that made no undo point in between is reverted with it. Measured: a volume written as 1.0 without an undo point, followed by a recorded step, came back as the 0.7 of the step before when that step was undone. Since 1.3.0 every tool that changes the project records a step (`connection.records_undo`), so this only bites raw calls: a test that sets up with raw calls and then undoes a tool must record its setup as a step first.

`Undo_CanUndo2` and `Undo_CanRedo2` raise inside Python ReaScript when there is nothing to undo or redo: the generated wrapper calls `.decode()` on a null result. Catch the error rather than expecting `None`.

### A block records every change except a new MIDI item

Measured from reapy on REAPER 7.82, each change followed by one Undo, passing when the project came back exactly and the step before it was untouched:

| Change | Block (`Undo_BeginBlock2` … `EndBlock2(-1)`) | `Undo_OnStateChange2` | `Undo_OnStateChangeEx2(-1)` |
| --- | --- | --- | --- |
| Track volume | pass | fail | pass |
| Insert track | pass | pass | pass |
| Add FX | pass | fail | fail |
| Add marker | pass | fail | pass |
| Envelope point | pass | pass | pass |
| New MIDI item (`CreateNewMIDIItemInProj`) | **fail** | pass | fail |
| Note into an existing item | pass | pass | pass |
| Move item | pass | pass | pass |
| Delete item | pass | pass | pass |

Inside a block, `CreateNewMIDIItemInProj` leaves no step at all, so the next Undo reverts the step before it and the item stays. Every other change, including splits, moves to another track, take pitch and rate, `InsertMedia` and preset loads, records correctly in a block. So `records_undo` uses a block by default and `Undo_OnStateChange2` (`items=True`) for the three tools that create MIDI items. `TrackFX_AddByName` outside a block records its own "Add FX" step, which the block absorbs. The Lua bridge does not have the MIDI gap: a chunk that creates a MIDI item is undone by one Undo.

`scripts/check_undo.py` repeats this for every tool: it saves the `.rpp` before a call, undoes once, and compares.

### The state change count is not a change detector

`GetProjectStateChangeCount` does not move when reapy writes outside a block, and moves by one at every `Undo_EndBlock2`, including an empty block. It cannot tell a write that landed from one that did not. Whether a step was added can: after a successful call, `records_undo` checks that `MCP: <tool>` is the top of the undo history and otherwise adds `unconfirmed` to the reply. A value that was already set is flagged as well, which is accurate: REAPER recorded no change. The check works one way only. REAPER compares the whole project with the last step, so a change left unrecorded since then, such as a selection or a raw call, gives even an empty call a step: `unconfirmed` proves nothing changed, while its absence does not prove the call's own change landed.

### Backups before the first change

`Main_SaveProjectEx` writing a copy without rebinding the project (see [Saving and Opening Projects](#saving-and-opening-projects)) is what makes a backup safe: on a saved, dirty project it took 32 ms, left the project bound to its file, dirty, with its undo history, and the copy held the unsaved change. `connection.backup_before_first_change` writes `<name>.mcp-backup-<time>.rpp` beside the project before the first change this server makes to it, keeps the newest five, and skips a project that was never saved.

## Envelopes

### Each envelope stores its own units

Established by inserting a point and reading REAPER's display of it with `Envelope_FormatValue`:

| Envelope | Stored value | Display |
| --- | --- | --- |
| Volume, Volume (Pre-FX), Trim Volume | fader-scaled gain: `ScaleToEnvelopeMode(1, 0.5)` = 592.4 | `-6.02dB`; a raw 0.5 shows `-inf dB` |
| Pan, Pan (Pre-FX) | pan inverted: 0.5 | `50%L`, while a track `D_PAN` of 0.5 is 50% right |
| Mute | 0 or 1 | 0 is `MUTE`, 1 is `UNMUTE` |
| Width | -1 to 1 unchanged | 0.5 is `50.0%` |
| FX parameter | the parameter's native value, not the normalised one | JS Volume Adjustment, range -150 to 150: a point at -6 evaluates to -6.0 |

For an FX parameter, convert a normalised value with `low + value * (high - low)`, where `low` and `high` are `TrackFX_GetParam(...)[4:6]`. For stock VST plugins the range is 0 to 1 and the conversion is the identity, which hides the mistake until a JSFX is automated.

`add_pan_automation` wrote pan without the inversion until 1.2.0, so every point landed on the opposite side. Its benchmark counted points and could not see it; it now also reads REAPER's display of the point.

### Creating, finding and clearing

- `GetTrackEnvelopeByName` returns an envelope that exists even when it is hidden and inactive. It returns null only when the envelope was never created, which is when to create it.
- There is no API call that creates a track envelope. Showing it does: run the action on the track alone, then put the selection back. Volume 40406, Pan 40407, Volume (Pre-FX) 40408, Pan (Pre-FX) 40409, Mute 40867, Trim Volume 42020. Width and Width (Pre-FX) have no "show" action; "Track: Select width envelope" (41870) and "Track: Select pre-FX width envelope" (41869) create them. Creating a volume envelope adds a point at 0 s holding the current level.
- `GetFXEnvelope(track, fx, param, True)` creates an FX parameter envelope, and un-bypasses one that exists.
- `GetSetEnvelopeInfo_String(env, "ACTIVE", "0", True)` bypasses an envelope; its points then do nothing.
- `DeleteEnvelopePointRange(env, 1.0, 2.0)` deleted the point at 1.0 and kept the one at 2.0: the end is excluded. Widen the range by a small margin to include it.
- `InsertEnvelopePoint` at a time that already holds a point adds a second point, and accepts negative times.

## Markers and Regions

`EnumProjectMarkers3` cannot return a marker's name in Python: the name is a `char**` output, and the generated wrapper returns the argument it was given. Every marker read that way is unnamed. REAPER 7's ProjectMarker API works: `GetNumRegionsOrMarkers`, `GetRegionOrMarker(0, index, "")`, `GetRegionOrMarkerInfo_Value` with `D_STARTPOS`, `D_ENDPOS`, `I_INDEX`, `I_NUMBER`, `B_ISREGION`, `I_CUSTOMCOLOR`, and `GetSetRegionOrMarkerInfo_String` with `P_NAME` and `GUID`. Setting `P_NAME` to `""` clears the name.

- A marker's index is its place in time order. Moving one renumbers the others, so address a batch by GUID: `GetRegionOrMarker(0, -1, guid)`.
- Markers and regions are numbered separately, and two markers may share a displayed number. The number does not identify a marker.
- Setting a region's `D_STARTPOS` past its end swaps the bounds: start 9 on a 1-2 region gave 2-9. Set the bound that moves away from the other one first.
- `AddRegionOrMarker` returns the new `ProjectMarker*`, so its GUID can be read at once.
- The time selection and the loop points are linked by default: setting either through `GetSet_LoopTimeRange2` set both.

## Items, Sends, FX Order and Pins

### Items re-sort as soon as they move

Setting `D_POSITION` re-sorts the track's item list at once, before `UpdateArrange`: moving the first of two items past the second swapped their indices. A batch that reads indices as it goes edits the wrong items after the first move. Resolve every index to its `MediaItem*` before changing anything, and read `IP_ITEMNUMBER` afterwards for the new indices.

### Send channels

| Field | Encoding |
| --- | --- |
| `I_SRCCHAN` | -1 no audio. Low 10 bits: first channel, 0-based. Bits above: 0 stereo pair, 1 mono, n for 2n channels |
| `I_DSTCHAN` | Low 10 bits: first channel, 0-based. Bit 1024 mixes the source to one channel |
| `I_SENDMODE` | 0 post-fader, 1 pre-FX, 3 pre-fader post-FX. 2 is a legacy value still accepted |

A send into channels 3/4 does not raise the destination track's channel count: `I_DSTCHAN` = 2 left `I_NCHAN` at 2, so the sidechain carried nothing. Raise `I_NCHAN` as well. The destination track's state chunk shows the same fields, `AUXRECV <source> <mode> <volume> <pan> <mute> <mono> <phase> <srcchan> <dstchan>`, which makes it an independent witness for a send write.

### FX order

`TrackFX_CopyToTrack(track, src, track, dest, True)` leaves the FX in slot `dest`: from EQ, Comp, Gate, Delay, moving 0 to 2 gave Comp, Gate, EQ, Delay. A destination past the end moved the FX to the end without complaint. `TrackFX_GetFXGUID` returns a `GUID*` pointer; convert it with `guidToString(pointer, "")` to compare chains.

### Pins

`TrackFX_GetPinMappings` returns a bitmask per pin, bit n for track channel n + 1. ReaComp reports four input pins mapped 1, 2, 4, 8 (channels 1 to 4) by default, named `Main Input L/R` and `Auxiliary Input L/R` through `TrackFX_GetNamedConfigParm(track, fx, "in_pin_N")`. A pin index past the end reads 0.

### reapy refuses pointer strings in ValidatePtr2

`RPR.ValidatePtr2(project, item, "MediaItem*")` raises `TypeError: 'str' object cannot be interpreted as an integer` inside reapy's own wrapper. Track deletions with a count instead, and never read from a pointer that may have been deleted.

## Render Settings State

Render fields are stored in project state, and any tool that renders modifies them. Save and restore them with a context manager. Note which fields are strings:

```python
_SAVED_STRINGS = ("RENDER_FILE", "RENDER_PATTERN", "RENDER_FORMAT")
_SAVED_NUMBERS = ("RENDER_SRATE", "RENDER_CHANNELS", "RENDER_BOUNDSFLAG")

value = RPR.GetSetProjectInfo_String(0, "RENDER_FILE", " " * 1024, False)[3]
```

### Format is a base64 blob, not a number

`RENDER_FORMAT` is a **string** setting holding a base64 configuration blob. Writing `RENDER_FORMAT` or `RENDER_FORMAT2` numerically does nothing and reads back as `0.0`. Every render then comes out in whatever format the project already had — requesting FLAC produced `name.wav` while the tool reported the `.flac` it expected as missing, and every bit depth request produced 24-bit.

The blob is a reversed four character code plus format-specific settings. Verified by rendering and reading the resulting file headers:

| Format | Blob | Result |
|---|---|---|
| WAV | `b"evaw" + bytes([depth, 0, 1])` | depth 8 → PCM_U8, 16 → PCM_16, 24 → PCM_24, 32 → 32-bit FLOAT |
| FLAC | `b"calf"` | PCM_16, default compression |
| FLAC | `b"calf" + struct.pack("<II", depth, compression)` | depth 16 or 24; compression 0-8 |
| MP3 | `b"l3pm"` | MPEG_LAYER_III |
| OGG | `b"vggo"` | Vorbis |

```python
RPR.GetSetProjectInfo_String(
    0, "RENDER_FORMAT", base64.b64encode(blob).decode("ascii"), True
)
```

The default WAV 24-bit cookie is `'ZXZhdxgAAQ=='`, which decodes to `b'evaw\x18\x00\x01'`. Reading the current cookie is the reliable way to learn a format's blob: configure it once in REAPER's render dialog, then read `RENDER_FORMAT`.

### Other render fields

- `RENDER_BOUNDSFLAG`: 1 = entire project, 2 = time selection. 0 = custom range and opens a modal dialog.
- `RENDER_FILE` is the target *directory*; `RENDER_PATTERN` is the filename stem. Setting `RENDER_FILE` to a full file path makes REAPER create a directory with that name.
- `RENDER_SRATE` and `RENDER_CHANNELS` are numeric and work as expected.
- Check output with `Path(target).is_file()`.

`Path.with_suffix()` is unsafe for building the output name: it replaces everything after the last dot, so `mix v1.2` becomes `mix v1.wav`. Append the extension unless the path already carries a known audio suffix.

## Measuring Rendered Audio

The analysis tools render the project and measure the file. Three measurement mistakes produced confident, wrong numbers.

**Mono summing hides out-of-phase content.** Collapsing to `mean(data, axis=1)` before measuring peak and RMS reported a -6 dBFS anti-phase mix as -120 dB silence. Measure across channels: peak from `max(abs(samples))`, RMS from `sqrt(mean(samples ** 2))` over every channel.

**Raw STFT magnitudes are not dBFS.** Averaging `abs(stft(y))` over the bins of a band placed a -6 dBFS tone at +31.9 dB, and made the reading depend on how many bins the band spans. Scale magnitudes to component amplitude, sum power across the band, and divide by the window's equivalent noise bandwidth:

```python
window = np.hanning(n_fft + 1)[:-1]
enbw = n_fft * np.sum(window ** 2) / (np.sum(window) ** 2)        # 1.5 for Hann
D = np.abs(librosa.stft(y, n_fft=n_fft)) * (2.0 / np.sum(window))
power = np.mean(np.sum((D[mask, :] / np.sqrt(2)) ** 2, axis=0) / enbw)
level_db = 10 * np.log10(power + 1e-12)
```

Summing every band of a signal then reproduces its overall RMS to within 0.02 dB, which is the check worth keeping.

**True peak is not sample peak.** BS.1770 measures the reconstructed waveform between samples. A render whose sample peak read -0.00 dBFS measured +0.04 dBTP once oversampled four times with `scipy.signal.resample_poly`. Report peaks to two decimals; at one decimal an overshoot of +0.04 rounds to `0.0` and a clipping warning ends up saying "0.0 dBTP, above full scale".

Two more traps in reported numbers:

- A dynamic-range score computed over 3-second windows finds **zero** windows in a shorter render. Returning `0.0` reads as "no dynamic range"; return the measurement over what exists and say which window was used.
- `np.corrcoef` returns `NaN` when a channel never changes, and `NaN` is not valid JSON. Guard on `np.std(channel) == 0`. Dividing by a guard constant has the same problem in reverse: `side_rms / (mid_rms + 1e-10)` returned a stereo width of 3,543,678,557 for fully out-of-phase audio. numpy scalars themselves serialise fine.

## Bridge Protocol

The Lua bridge is a file exchange in `<REAPER resource path>/claude_bridge`: the client writes `cmd.lua`, the listener consumes it, runs it, and writes `out.txt`. `status.txt` carries a heartbeat every 5 seconds and `log.txt` records each command, truncating past 2 MB.

Behaviours worth knowing before relying on it:

| Behaviour | Detail |
| --- | --- |
| Single result | `pcall` captures one value, so `return 7, 8, 9` yields `7`. Pack multiple values into a table. |
| Table rendering | One line per array element, then remaining keys as `k = v`, nested tables compact as `{1, 2}`, empty table as `{}`. Keys are sorted so output is stable. |
| Globals persist | Chunks share one Lua state. `_G.x` survives to the next call; locals do not. |
| Undo | Every command runs inside an undo block named "Claude bridge command". Read-only commands add no undo point. |
| Errors are contained | Parse errors, `error()`, nil indexing and unknown `reaper.*` calls return `PARSE_ERROR:`/`RUNTIME_ERROR:` with a line number and exit code 1. The listener stays alive. |
| Large payloads | A 100,000 character string and a 5,000 line table both round-trip intact. |
| Timeouts | The client stops waiting; REAPER keeps executing the chunk. A late result is not mistaken for the next command's output. |
| Line numbers | The client appends its request id as a trailing comment, so error line numbers match the code as written. |

### Request ids

A fresh `out.txt` timestamp does not prove the output belongs to the command just sent. With two clients running, the second reads whichever result lands first: measured directly, a caller that asked for `CALLER_A` received `CALLER_B`, and `CALLER_A`'s result was lost.

The client therefore appends `--@claude-bridge-id:<token>` to the chunk and the listener echoes it as a first line, `@id:<token>`. Results carrying a different id are skipped and waiting continues. A listener that sends no id line is still accepted, so a hand-written `cmd.lua` continues to work.

### One listener at a time

Running the startup action twice used to leave two listeners polling the same file, each consuming commands the other was waiting for. The script now claims a generation number at startup and any older instance retires on its next wake-up.

Both of these live in `claude_bridge.lua`, which REAPER loads at startup. **Editing that file has no effect on the running listener until REAPER restarts.**

### Interpreter selection

`scripts/reaper-bridge` must run a candidate interpreter before committing to it. On Windows, `python3` resolves to the Microsoft Store App Execution Alias: it satisfies `command -v`, then refuses to run and exits 49 with "Python was not found". Selecting on presence alone made the wrapper unusable unless `REAPER_MCP_PYTHON` was set by hand, even with a working Python on PATH.

## What a Call Costs

Execution timings per MCP tool call (measured on a Windows VM with an empty project).

| Call Type | Median Time |
|---|---|
| No round trip (`play_project`, `stop_transport`) | ~30 ms |
| Mutating calls (`set_track_volume`, `add_fx`) | ~150-190 ms |
| Project info read | ~310 ms |
| Track info read | ~375 ms |
| List tracks (3 tracks) | ~595 ms |
| Render (3-second project) | ~1.9 s |
| Analysis tool (render and measure) | ~1.9-3.5 s |
| Parameter solve by display bisection | ~1-3 s per parameter |
| A tool built on `inside_reaper` (`edit_items`, `get_envelope_points`, `list_markers`) | ~25-60 ms |

Tool calls execute `get_project()` and `n_tracks`. `list_tracks` performs four ReaScript reads per track. Batch reads using the Lua bridge to minimize round trips.

## Benchmark Script

`scripts/benchmark_tools.py` executes 71 tools against REAPER, asserts results, logs execution time, and restores state.

```bash
python scripts/benchmark_tools.py                    # Default: skips project-replacing tools
python scripts/benchmark_tools.py --include-destructive
python scripts/benchmark_tools.py --scratch          # Run in a new project tab
python scripts/benchmark_tools.py --json out.json
```

The script executes within the active project on tracks prefixed `__bench__`. It restores tempo, time signature, markers, master volume, master FX, and cursor position. The envelope, marker, selection, routing, undo and item groups run last, in `run_edit_plan`, on two tracks of their own; they remove their markers and restore the time selection, loop and repeat state. Their undo test leaves its steps at the top of the project's undo history.

The `--scratch` flag creates a new project tab for testing `save_project`, `load_project`, `create_project`, and `start_recording`. Execution is blocked if the current project has unsaved changes.

### State Verification

The `expect` parameter tests the payload returned by the tool. `b.confirm(probe, expected, label)` executes a state verification read via `reascript_api`.

```python
b.call("set_track_color", {"track_index": ix, "r": 200, "g": 60, "b": 60}, group=g)
b.confirm(lambda: (int(RPR.GetMediaTrackInfo_Value(_track_id(ix), "I_CUSTOMCOLOR")),
                   RPR.ColorToNative(200, 60, 60) | 0x1000000),
          lambda pair: pair[0] == pair[1], "I_CUSTOMCOLOR (stored, wanted)")
```

API reads are encapsulated within the probe function. `confirm` bypasses execution if the initial tool call returns a failure.

A test that only reads the tool's own response proves nothing about REAPER. Verify against an independent source: `reascript_api`, the Lua bridge, the saved `.rpp`, or the rendered audio. Several defects survived earlier passes because empty tracks never exercised the failing branch — `get_track_info` crashed on any track holding an item, and the analysis tools could not be judged at all without audio of known level, phase and spectrum to measure against.

The `--include-destructive` flag creates an additional project tab. This tab is left open after execution to avoid modal save prompts on closure.
