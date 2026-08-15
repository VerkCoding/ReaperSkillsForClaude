# Driving REAPER from Python

The MCP tools reach REAPER through **reapy**, and reapy is a thin, uneven wrapper
over the ReaScript API. It is convenient where it works and quietly wrong where
it does not, and the failures do not look like failures — they look like success.

Everything below was established by running all 58 MCP tools against REAPER 7.78
with reapy 0.10.0 under Python 3.12 and checking what REAPER actually held
afterwards. That found nine distinct defects: 15 of the 58 tools failed outright,
and several more *passed* a success-only check while doing nothing at all. The
second group is the reason this document exists.

## Contents

- [The one rule](#the-one-rule)
- [Verified reapy traps](#verified-reapy-traps)
- [Modal dialogs freeze everything](#modal-dialogs-freeze-everything)
- [Never loop unbounded against REAPER](#never-loop-unbounded-against-reaper)
- [Tempo and time signature share one marker](#tempo-and-time-signature-share-one-marker)
- [Render settings are project state](#render-settings-are-project-state)
- [What a call costs](#what-a-call-costs)
- [The bench](#the-bench)

## The one rule

**Write through `reascript_api`, read back through `reascript_api`, and never
trust a reapy attribute assignment.**

`FXParam` subclasses `float`. `Track` has no `armed` property. Python lets you
assign any attribute to either, so this succeeds, returns no error, and never
reaches REAPER:

```python
fx.params[0].normalized_value = 0.6   # sets a junk attribute on a temporary object
track.armed = True                    # same
```

Both were live bugs in this plugin. `set_fx_parameter` reported the value it had
been handed rather than the value REAPER held, so it looked correct in every
transcript. The version that works writes and then re-reads:

```python
from reapy import reascript_api as RPR

RPR.TrackFX_SetParamNormalized(track.id, fx_index, param_index, value)
applied = RPR.TrackFX_GetParamNormalized(track.id, fx_index, param_index)
return {"value": applied, "requested": value}
```

Returning both is deliberate. A plugin that quantises its parameters will report
an `applied` that differs from `requested`, and that is information the caller
needs rather than a discrepancy to hide.

This is the same discipline as the **Measure → Change → Verify** loop in the main
skill, applied one layer down: you cannot hear the mix, and you also cannot see
whether the write landed.

## Verified reapy traps

Each row was confirmed by writing through reapy and then reading REAPER's own
state back.

| What looks right | What actually happens | Use instead |
|---|---|---|
| `idx = track.add_fx(name)` then `if idx < 0` | Returns an **`FX` object**, not an int. Comparing raises `'<' not supported between instances of 'FX' and 'int'` | `fx = track.add_fx(name)`, then `fx.index`. A missing plugin raises `ValueError`, it never returns -1 — catch it |
| `param.normalized_value` / `param.formatted_value` | `AttributeError` on read; **silent no-op** on write | `param.normalized` / `param.formatted` to read; `RPR.TrackFX_SetParamNormalized` to write |
| `fx.params[i].normalized = v` | reapy's own setter reads `parent_fx.id`, which `FX` does not have → `AttributeError` | `RPR.TrackFX_SetParamNormalized(track.id, fx_index, i, v)` |
| `track.armed = True` | Silent no-op. `I_RECARM` stays 0, so recording starts with nothing armed and REAPER opens a modal warning | `RPR.SetMediaTrackInfo_Value(track.id, "I_RECARM", 1)`, then read it back before starting the transport |
| `project.time_signature` | Returns **`(bpm, bpi)`** — tempo and numerator. Formatting it as `n/d` reports a 120 BPM 4/4 project as `"120.0/4.0"` | `RPR.TimeMap_GetTimeSigAtTime(0, 0.0, 0, 0, 0)`; numerator and denominator are at indices 2 and 3 |
| `project.time_signature = (n, d)` | Read-only property → `AttributeError` | `RPR.SetTempoTimeSigMarker` — see below |
| `project.save(path)` | Its only argument is `force_save_as`, a **bool**. A path lands where an int is expected → `'str' object cannot be interpreted as an integer` | `RPR.Main_SaveProjectEx(0, path, 0)`. It writes the file but leaves the project dirty; follow with `RPR.Main_SaveProject(0, False)` to clear the flag |
| `project.bpm = x` | Correct **only while no tempo marker sits at position 0**. With one there it changes nothing and inserts a duplicate marker | Rewrite the marker (see below) |
| `item.fade_in_length` / `item.fade_out_length` | **Silent no-op.** The fade stays 0.0 and the call reports success — and the payload returns position and length, so nothing in the reply could contradict it | `RPR.SetMediaItemInfo_Value(item.id, "D_FADEINLEN" / "D_FADEOUTLEN", seconds)` |
| `take.start_offset = x` | Read-only property → `AttributeError`, which took the whole `start_trim` branch with it | `RPR.SetMediaItemTakeInfo_Value(take.id, "D_STARTOFFS", value)` |
| `fx.preset_name = name` | **Silent no-op**, and it accepts a name no plugin has — so "preset loaded" was reported for presets that do not exist | `RPR.TrackFX_SetPreset(track.id, fx, name)`, then read `TrackFX_GetPreset(...)[3]` back and compare |
| `item.position` / `item.length` / `fx.is_enabled` | **These do work.** Listed so the next sweep does not "fix" them on suspicion of matching the pattern | Leave them alone |

## Null pointers are strings, and strings are true

The trap under [Never loop unbounded](#never-loop-unbounded-against-reaper) is
not only about loops, and `EnumProjects` is not the only call that springs it.
**Every** ReaScript function returning a pointer returns it as a *string*, so a
null one arrives as `'(TrackEnvelope*)0x0000000000000000'` — twenty-eight
non-empty characters, and therefore true.

```python
envelope = RPR.GetTrackEnvelopeByName(track.id, "Volume")
if not envelope:        # NEVER fires, whatever REAPER returned
```

Both automation tools were written with that guard, so the "show the envelope
first" error they carried could not be reached. They inserted a point into a
null envelope — which does nothing, and returns 0 — and reported success. The
guard has to look at the value:

```python
def _is_null(pointer) -> bool:
    return not pointer or "0x0000000000000000" in str(pointer)
```

Then check the work landed, because a bad pointer is not the only way an insert
fails: `CountEnvelopePoints` before and after is the claim worth making.

The pattern behind most of these: reapy exposes a property for the *getter* and
either omits or breaks the *setter*, and Python's permissiveness turns the
missing setter into a no-op rather than an error.

## Modal dialogs freeze everything

This is the failure mode worth internalising, because nothing in Python reports
it and the symptom is indistinguishable from a hang.

`activate_reapy_server.py` is a **deferred** ReaScript. A modal dialog stops
REAPER running deferred scripts — all of them. So while a dialog waits:

- the reapy server stops answering, and every MCP tool call blocks forever;
- the Lua bridge stops answering too, so your fallback route is gone as well;
- nothing appears in any log, because nothing failed. It is still waiting.

`connection.py` documents this chain for the ReaScript-task-control dialog. It
applies to every modal REAPER can raise, and four turned up in ordinary use:

| Dialog | Text | Provoked by | Safe to dismiss? |
|---|---|---|---|
| `Render Error` | "Nothing to render!" | `RENDER_BOUNDSFLAG = 0` — the custom range is empty | **Yes**, OK is the only button |
| `Record Warning` | "No tracks are armed for recording" | Starting the transport with nothing armed | Cancel aborts the record; Continue starts a recording nobody asked for |
| `REAPER Query` | "Save unsaved project before closing?" | `load_project` / `create_project` on a dirty project | **No** — three buttons, and one discards the user's work |
| `ReaScript task control` | "…is running in the background" | reapy re-running the server action | **No** — "Terminate instances" kills the bridge too |

### Do not provoke them

Prevention beats detection, and each of these has a cheap guard:

```python
if RPR.IsProjectDirty(0):
    return {"success": False, "error": "save first — otherwise REAPER opens a modal prompt "
                                       "and no tool can reach it until someone clicks it"}
```

Refusing with a sentence the user can act on is strictly better than starting an
operation that freezes every subsequent call. The same shape applies to arming
before recording, and to setting `RENDER_BOUNDSFLAG` to 1 or 2 but never 0.

### Detect them when it is too late

Enumerate REAPER's windows by process id — matching on titles you already know
means the one dialog you have never seen is the one you cannot report:

```python
# Find REAPER's pid from its main window ("REAPER v..." in the title), then list
# every other visible top-level window owned by that process, with child text.
```

`scripts/benchmark_tools.py` implements exactly this and is worth copying from. A run that
reports *"REAPER is showing 'Record Warning': No tracks are armed for
recording"* has told you the answer; one that reports *"no dialog found"* has
told you nothing, which is what the first version of that code did.

### Survive them

Never call REAPER on a thread you cannot abandon. Run the call on its own thread
with a timeout, and if it does not return, report what is on screen rather than
waiting. Cancelling an `asyncio` task does not help — the block is inside REAPER,
not inside the event loop, so the loop stays wedged for every call after it.

## Never loop unbounded against REAPER

Two loops that look obviously correct will spin forever and pin a CPU core:

```python
while RPR.EnumProjects(i, "", 0)[0]:        # NEVER terminates
    i += 1
```

`EnumProjects` returns a pointer *string* past the end of the list —
`'(ReaProject*)0x0000000000000000'` — which is truthy. Compare against the null
pointer and bound the loop:

```python
def project_tab_count(limit=32):
    for i in range(limit):
        if "0x0000000000000000" in str(RPR.EnumProjects(i, "", 0)[0]):
            return i
    return limit
```

Likewise `while RPR.CountTempoTimeSigMarkers(0) > 0: RPR.DeleteTempoTimeSigMarker(0, 0)`
spins if a delete is ever refused. Bound every such loop and check that the count
actually moved:

```python
for _ in range(limit):
    count = RPR.CountTempoTimeSigMarkers(0)
    if count == 0:
        break
    RPR.DeleteTempoTimeSigMarker(0, count - 1)
    if RPR.CountTempoTimeSigMarkers(0) == count:
        break          # refused; looping again will not help
```

When you write a throwaway diagnostic script, run it with `python -u` or
`print(..., flush=True)`. Python block-buffers stdout when it is redirected, so a
script that hangs writes an **empty** file and tells you nothing about where it
got to.

## Tempo and time signature share one marker

REAPER stores the time signature in a tempo/time-signature marker, not in a
project field. That makes the two settings interact, and the interaction is
silent in both directions:

- Setting a time signature **creates a marker at position 0** if there is not one.
- Once that marker exists, `project.bpm = x` stops working and adds duplicates.

So both writes have to go through the marker, each preserving the other's half:

```python
def marker_at_zero() -> int:
    for i in range(RPR.CountTempoTimeSigMarkers(0)):
        if abs(RPR.GetTempoTimeSigMarker(0, i, 0, 0, 0, 0, 0, 0, 0)[3]) < 1e-9:
            return i
    return -1          # also the "insert a new one" argument

# tempo, keeping the signature
m = RPR.GetTempoTimeSigMarker(0, idx, 0, 0, 0, 0, 0, 0, 0)
RPR.SetTempoTimeSigMarker(0, idx, 0.0, -1, -1, bpm, m[7], m[8], False)

# signature, keeping the tempo
RPR.SetTempoTimeSigMarker(0, marker_at_zero(), 0.0, -1, -1, project.bpm, num, denom, False)

RPR.UpdateTimeline()
```

`GetTempoTimeSigMarker` returns the argument list with outputs filled in:
timepos at 3, numerator at 7, denominator at 8.

Setting a time signature therefore leaves a visible marker in the user's project.
That is how REAPER represents it — it is not litter — but say so rather than
letting them find it.

## Render settings are project state

`RENDER_FILE`, `RENDER_PATTERN`, `RENDER_BOUNDSFLAG` and the format fields belong
to the user's project. Every analysis tool renders, so a `analyze_loudness` call
that leaves those pointing at a temp file has quietly reconfigured their export.

**Save them, set them, put them back** — a context manager is the natural shape.
Read strings with a buffer big enough for the answer, since the string you pass
in is the buffer REAPER writes into:

```python
value = RPR.GetSetProjectInfo_String(0, "RENDER_FILE", " " * 1024, False)[3]
```

The two facts that cost the most time, both already correct in
[rendering.md](./rendering.md) for the Lua route and both wrong in the Python
tools until this was checked:

- `RENDER_BOUNDSFLAG`: **1** = entire project, **2** = time selection. **0** is a
  custom range that starts out empty and opens the modal box.
- `RENDER_FILE` is a **directory**; `RENDER_PATTERN` is the filename stem. Passing
  a full path to `RENDER_FILE` makes REAPER create a *directory* named
  `mixdown.wav` and write the pattern-named file inside it. `os.path.exists`
  returns true for that directory and `os.path.getsize` returns 0 — which is how
  a render tool reports success with a file size of zero.

Check `Path(target).is_file()`, never `os.path.exists`.

## What a call costs

Median wall time per MCP tool call, measured on a 4 GB Windows Sandbox VM with an
empty project. Treat the absolute numbers as machine-specific and the **ratios**
as the useful part.

| Call | Median |
|---|---|
| `play_project`, `stop_transport` (no round trip) | ~30 ms |
| most mutating calls (`set_track_volume`, `add_fx`) | ~150–190 ms |
| `get_project_info` | ~310 ms |
| `get_track_info` | ~375 ms |
| `list_tracks` (3 tracks) | ~595 ms |
| a render of a 3-second project | ~1.9 s |
| an analysis tool (renders, then measures) | ~1.9–3.5 s |

Every tool pays one `get_project()` round trip plus an `n_tracks` probe that
forces it to be real. `list_tracks` then pays four more ReaScript reads per
track, which is why it is the slowest read by a wide margin. **Batch through the
Lua bridge when you need many values at once** — one bridge round trip that
returns a table beats forty MCP calls.

## The bench

`scripts/benchmark_tools.py` calls all 58 tools against a live REAPER, asserts on what
comes back, times everything, and cleans up after itself.

```bash
python scripts/benchmark_tools.py                    # default: skips project-replacing tools
python scripts/benchmark_tools.py --include-destructive
python scripts/benchmark_tools.py --scratch          # run it all in a fresh project tab
python scripts/benchmark_tools.py --json out.json
```

Run it after touching any tool module. It works inside the project that is
already open, on tracks prefixed `__bench__`, and restores tempo, time signature,
markers, master volume, master FX and the cursor.

`--scratch` opens a new project tab and runs everything there, which is what
makes `save_project`, `load_project`, `create_project` and `start_recording`
safe to exercise. It **refuses to start** when the open project has unsaved
changes rather than letting REAPER raise the save prompt: a modal stops every
deferred script, so provoking one costs the whole session. Save or discard
first.

### Asserting on the right thing

`expect=` is handed **the tool's own payload** — the tool's word for what it
did. `b.confirm(probe, expected, label)` is handed REAPER's, read straight
through `reascript_api`, and demotes the call just recorded when the two
disagree:

```python
b.call("set_track_color", {"track_index": ix, "r": 200, "g": 60, "b": 60}, group=g)
b.confirm(lambda: (int(RPR.GetMediaTrackInfo_Value(_track_id(ix), "I_CUSTOMCOLOR")),
                   RPR.ColorToNative(200, 60, 60) | 0x1000000),
          lambda pair: pair[0] == pair[1], "I_CUSTOMCOLOR (stored, wanted)")
```

Both reads go inside the probe, because a REAPER call made outside it is one
more thing that can hang. `confirm` stays silent when the recorded call already
failed — a tool that failed changed nothing, and a second row would say the same
thing twice.

Twenty-one tools once had no `expect=` at all and passed on "did not throw".
Adding `confirm` to them found four defects in one run, three of which reported
`success: true`. So: **a new case without an independent read-back is not a
test.** And a probe that has never been seen to fail is not one either — invert
it once and watch it go red before trusting it.

`--include-destructive` leaves an extra project tab behind, because REAPER's New
Project opens a tab rather than replacing the current one. The bench reports it
instead of closing it — closing a tab with unsaved changes asks a question this
skill is not entitled to answer.
