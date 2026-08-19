# Driving REAPER from Python

The MCP tools interface with REAPER through reapy, a wrapper for the ReaScript API. Some functions return incorrect results or fail silently without raising errors.

The following behaviors were identified by executing 58 MCP tools against REAPER 7.78 with reapy 0.10.0 under Python 3.12, followed by verifying REAPER's state. Fifteen tools failed execution, and several returned success without modifying state. This document details the latter cases.

## Contents

- [API Usage Rules](#api-usage-rules)
- [Reapy Behaviors](#reapy-behaviors)
- [Pointer Returns](#pointer-returns)
- [Modal Dialog Execution Blocking](#modal-dialog-execution-blocking)
- [Loop Bounding](#loop-bounding)
- [Tempo and Time Signature Markers](#tempo-and-time-signature-markers)
- [Render Settings State](#render-settings-state)
- [Execution Timing](#execution-timing)
- [Benchmark Script](#benchmark-script)

## API Usage Rules

Write and read using `reascript_api`. Do not rely on reapy attribute assignments.

`FXParam` subclasses `float`. `Track` lacks an `armed` property. Assigning attributes to these objects does not raise errors and does not update REAPER state.

```python
fx.params[0].normalized_value = 0.6   # Assigns attribute to a temporary object. State is not updated.
track.armed = True                    # Assigns attribute to a temporary object. State is not updated.
```

To update state, use the `reascript_api` functions and read the state back to verify:

```python
from reapy import reascript_api as RPR

RPR.TrackFX_SetParamNormalized(track.id, fx_index, param_index, value)
applied = RPR.TrackFX_GetParamNormalized(track.id, fx_index, param_index)
return {"value": applied, "requested": value}
```

Returning both values allows the caller to handle cases where a plugin quantizes parameters, resulting in an `applied` value that differs from the `requested` value. Read operations are necessary to verify write operations.

## Reapy Behaviors

The behaviors in the table were confirmed by executing writes via reapy and reading the subsequent state in REAPER.

| Input Syntax | Result | Recommended Usage |
|---|---|---|
| `idx = track.add_fx(name)` then `if idx < 0` | Returns an `FX` object, not an int. Comparing raises `'<' not supported between instances of 'FX' and 'int'`. | `fx = track.add_fx(name)`, then read `fx.index`. Missing plugins raise `ValueError`. |
| `param.normalized_value` / `param.formatted_value` | Raises `AttributeError` on read; silent no-op on write. | `param.normalized` / `param.formatted` to read; `RPR.TrackFX_SetParamNormalized` to write. |
| `fx.params[i].normalized = v` | reapy setter reads `parent_fx.id`, which `FX` does not have, raising `AttributeError`. | `RPR.TrackFX_SetParamNormalized(track.id, fx_index, i, v)`. |
| `track.armed = True` | No-op. `I_RECARM` remains 0. | `RPR.SetMediaTrackInfo_Value(track.id, "I_RECARM", 1)`. |
| `project.time_signature` | Returns `(bpm, bpi)`. | `RPR.TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)`. Numerator and denominator are at indices 2 and 3. |
| `project.time_signature = (n, d)` | Read-only property; raises `AttributeError`. | `RPR.SetTempoTimeSigMarker`. |
| `project.save(path)` | Argument is `force_save_as` (bool). Path input raises `TypeError`. | `RPR.Main_SaveProjectEx(0, path, 0)`. Follow with `RPR.Main_SaveProject(0, False)` to clear dirty flag. |
| `project.bpm = x` | Modifies bpm only if no tempo marker exists at position 0. Otherwise, inserts duplicate marker. | Modify the marker directly. |
| `item.fade_in_length` / `item.fade_out_length` | No-op. The fade remains 0.0. | `RPR.SetMediaItemInfo_Value(item.id, "D_FADEINLEN" / "D_FADEOUTLEN", seconds)`. |
| `take.start_offset = x` | Read-only property; raises `AttributeError`. | `RPR.SetMediaItemTakeInfo_Value(take.id, "D_STARTOFFS", value)`. |
| `fx.preset_name = name` | No-op. Accepts non-existent preset names. | `RPR.TrackFX_SetPreset(track.id, fx, name)`, read `TrackFX_GetPreset(...)[3]`. |
| `item.position` / `item.length` / `fx.is_enabled` | Functions as documented. | Use these properties. |

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

Operations on null pointers return 0 and do not modify state. Verify state changes using operations like `CountEnvelopePoints` before and after.

Most issues relate to reapy implementing getters but omitting or implementing non-functional setters.

## Modal Dialog Execution Blocking

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

### Dialog Prevention

Check conditions prior to operations that may trigger dialogs.

```python
if RPR.IsProjectDirty(0):
    # Modal dialog prompt for save will block execution.
    return {"success": False, "error": "Unsaved project changes detected. Save project to proceed."}
```

Validate inputs for track arming before recording and ensure `RENDER_BOUNDSFLAG` is set to 1 or 2.

### Dialog Detection

Dialogs can be detected by enumerating top-level windows owned by the REAPER process ID.

```python
# Retrieve top-level windows for REAPER pid to identify active dialogs.
```

The implementation in `scripts/benchmark_tools.py` identifies dialog presence and retrieves child text.

### Thread Execution

Execute REAPER calls on isolated threads with timeouts. Cancelling `asyncio` tasks will leave the event loop blocked if the execution is blocked inside REAPER.

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

Writes for tempo and time signature must use the marker API and preserve the other variable.

```python
def marker_at_zero() -> int:
    for i in range(RPR.CountTempoTimeSigMarkers(0)):
        if abs(RPR.GetTempoTimeSigMarker(0, i, 0, 0, 0, 0, 0, 0, 0)[3]) < 1e-9:
            return i
    return -1          # Value used to insert new marker

# Set tempo, preserve signature
m = RPR.GetTempoTimeSigMarker(0, idx, 0, 0, 0, 0, 0, 0, 0)
RPR.SetTempoTimeSigMarker(0, idx, 0.0, -1, -1, bpm, m[7], m[8], False)

# Set signature, preserve tempo
RPR.SetTempoTimeSigMarker(0, marker_at_zero(), 0.0, -1, -1, project.bpm, num, denom, False)

RPR.UpdateTimeline()
```

`GetTempoTimeSigMarker` returns the input arguments with modified outputs. The time position is at index 3, numerator at index 7, and denominator at index 8.

Modifying the time signature creates a marker in the REAPER project UI.

## Render Settings State

Render fields (`RENDER_FILE`, `RENDER_PATTERN`, `RENDER_BOUNDSFLAG`, format fields) are stored in project state. Analysis tools that render files modify these fields.

Use a context manager to save and restore render fields. String reads require passing a buffer string.

```python
value = RPR.GetSetProjectInfo_String(0, "RENDER_FILE", " " * 1024, False)[3]
```

Render API details:
- `RENDER_BOUNDSFLAG`: 1 = entire project, 2 = time selection. 0 = custom range (opens modal dialog).
- `RENDER_FILE`: target directory. `RENDER_PATTERN`: filename stem. Setting `RENDER_FILE` to a full file path causes REAPER to create a directory with that path.

Use `Path(target).is_file()` to check output files.

## Execution Timing

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

Tool calls execute `get_project()` and `n_tracks`. `list_tracks` performs four ReaScript reads per track.
Batch reads using the Lua bridge for data retrieval to minimize round trips.

## Benchmark Script

`scripts/benchmark_tools.py` executes 58 tools against REAPER, asserts results, logs execution time, and restores state.

```bash
python scripts/benchmark_tools.py                    # Default: skips project-replacing tools
python scripts/benchmark_tools.py --include-destructive
python scripts/benchmark_tools.py --scratch          # Run in a new project tab
python scripts/benchmark_tools.py --json out.json
```

The script executes within the active project on tracks prefixed `__bench__`. It restores tempo, time signature, markers, master volume, master FX, and cursor position.

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

Test cases require independent read-backs to verify state mutations.

The `--include-destructive` flag creates an additional project tab. This tab is left open after execution to avoid modal save prompts on closure.
