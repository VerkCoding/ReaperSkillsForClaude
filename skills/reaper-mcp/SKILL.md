---
name: reaper-mcp
description: >-
  The `reaper` MCP tool surface and Lua file bridge for communication with REAPER.
  Use to call REAPER tools, handle tool failures, debug hangs, manage renders,
  set plugin parameters without mapped tools, or debug the MCP server.
  For decision-making and measurements, use reaper-audio-engineer.
---

# Reaching REAPER

This skill manages the transport layer for sending commands to REAPER and receiving responses. Decision logic is handled by reaper-audio-engineer.

## Communication routes

Verify available routes before execution.

| Route | Description | Availability |
| --- | --- | --- |
| **MCP server** | 71 structured tools (e.g., `create_track`, `add_fx`, `render_project`). | Claude Code and Claude Desktop, when the server is running. |
| **File bridge** | Arbitrary Lua executed inside REAPER. | Environments where shell commands can be executed on the host machine running REAPER. |

On claude.ai in the browser, neither route is available. State this limitation and use this document as a reference.

If REAPER tools are unavailable, call `reaper_setup_status` to identify the diagnostic status. Otherwise, use reaper-core-setup.

## REAPER in the background

Claude drives REAPER from another window, so REAPER is almost never the focused application. Two of REAPER's preferences act on exactly that. Some users keep both on deliberately: leave them as they are, and do not ask the user to change them.

| Preference | In the background it | Which breaks | Handled since 1.3.1 by | By hand |
|---|---|---|---|---|
| `offlineinact`, "Set media items offline when application is not active" | takes every media item offline | renders: silent at the right length | every tool that renders | [Renders through the bridge](#renders-through-the-bridge) |
| `audiocloseinactive`, "Close audio device when stopped and application is inactive" | closes the audio engine while stopped | writes to plugins that take them in their audio callback, such as Ozone 12: the write waits | `set_fx_parameter`, `set_master_fx_parameter` | [Plugin Control](./references/plugin-control.md#plugins-that-take-writes-in-their-audio-callback) |

`reaper.get_config_var_string("offlineinact")` and `("audiocloseinactive")` read the two preferences; `reaper.Audio_IsRunning()` says whether the engine is open now.

The engine state also sets the price of every render, normally: REAPER stops and restarts the audio device around a render, which took about 1 s in all with the engine closed and about 18 s with it running on a Focusrite ASIO driver. Not a fault; plan renders around it ([Rendering](./references/rendering.md#the-audio-device-around-a-render-normal-not-a-fault)).

### Renders through the bridge

Since 1.3.1, `render_project`, `render_time_selection`, `render_stems`, `normalize_project`, `detect_clipping` and the `analyze_*` tools run action 40101 (Item: Set all media online) just before rendering. The analysis tools and `normalize_project` refuse a silent render with `success: false` and `silent: true`, and the render tools add a `warning` to a silent file. Version 1.3.0 did none of this and reported a silent render as `integrated_lufs: -Infinity` with `success: true`.

With 1.3.0 or earlier, and for any render made through the bridge, bring the media online yourself immediately before the render:

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/reaper-bridge" --timeout 30 --code 'reaper.Main_OnCommand(40101, 0) local on, off = 0, 0 for i = 0, reaper.CountMediaItems(0) - 1 do local take = reaper.GetActiveTake(reaper.GetMediaItem(0, i)) if take then if reaper.GetMediaSourceLength(reaper.GetMediaItemTake_Source(take)) > 0 then on = on + 1 else off = off + 1 end end end return "online=" .. on .. " offline=" .. off'
```

- Render only when it returns `offline=0`.
- Repeat it before **each** render, not once per session. Media goes offline again every time the user clicks into REAPER and back out; in one session all 25 sources were offline again after the user selected an item.
- Action 40101 (Item: Set all media online) changes no project state: it adds no undo point and leaves the dirty flag alone. Tell the user once per session that renders go through this step; it needs no announcement after that.
- A reading of `-Infinity` LUFS, a true peak below about −80 dBTP, or a tool reply with `silent: true` means a silent render. Stop and follow [Diagnosing a silent render](./references/rendering.md#diagnosing-a-silent-render).
- In one session the first render after media came online read a sample peak 0.74 dB lower than every later render, with the same LUFS-I; a repeat test did not reproduce it. When a peak decides something, such as a ceiling check, confirm it with a second render.

## Talking to a plugin: the protocol

> **Every read or write of a plugin parameter follows the [Plugin protocol](./references/plugin-protocol.md)**, whatever the plugin. It is built on 17 measured plugins, so an unfamiliar plugin starts from what most plugins do, and the checks say when to leave that default.

| Step | Do | Price | Payoff / Stability |
|---|---|---|---|
| 0 Address | Track and FX index from `list_track_fx`; check the FX name right before writing | ~0.2 s | the right instance, even after the user reorders |
| 1 Find | `get_fx_parameters` with `name_contains`; skip the last three (REAPER's Bypass, Wet, Delta) and MIDI padding | 0.2-1.4 s | the index of this plugin version |
| 2 Classify | toggle / list (`steps`, `step_index`, `step_label`) / continuous, from the same reply | free | the true setting; `step_label` corrects a label that is off by one |
| 3 Translate | toggle 0/1; list `index/steps` + nudge; registry formula; else `find_norm` (read-only); else a scratch instance | 0 / ~0.2 s / ~1.3 s | one value, with nothing written to the user's instance while searching |
| 4 Write | `set_fx_parameter` once; never a bridge write when the tool can | ~0.35 s, up to +1 s late, ~+1 s to open the engine | REAPER holds it; one undo step; engine put back |
| 5 Verify | the reply always, the display by default, a render only for sound decisions | free / ~0.2 s / ~1-18 s + audio, by audio-engine state | the plugin shows the intended value |
| 6 Record | a registry row in [Plugin Control](./references/plugin-control.md#registry-of-measured-plugins) when the plugin left the base | once | the next session knows the plugin |

## Choosing a route

**Default to the MCP tools.** They validate their inputs, refuse impossible values, restore the settings they borrow, and return a structured result. Reach for the bridge when one of the conditions below holds, not as a matter of taste.

| Situation | Route | Why |
| --- | --- | --- |
| A tool covers the task | **MCP tool** | Validation, error messages and state restoration are already written and tested. Reimplementing them in Lua repeats debugged work. |
| Structured project work | **MCP tool** | Projects, tempo, time signature, tracks, naming, volume, pan, solo, mute, colour, FX and FX order, MIDI items, chords, drum patterns, sends with their mode and channels, buses, automation on track and FX-parameter envelopes, markers and regions, time selection and loop, item moves, splits and fades, undo, rendering, stems. |
| Rendering to a file | **MCP tool** | `render_project`, `render_time_selection` and `render_stems` save and restore the `RENDER_*` project settings. Hand-written Lua leaves the user's render settings changed. |
| An operation that must be refused when wrong | **MCP tool** | The tools reject negative indices, out-of-range values and trims that would consume an item. Raw Lua applies whatever it is given. |
| No tool covers the task | **Bridge** | The tools stop short of: folder structure, takes, polarity, actions by command ID, FX pin remapping (`get_fx_pins` only reads), send envelopes, automation items, MIDI channels on sends, and a track's channel count except as a send destination. Check `src/reaper_mcp/*_tools.py` before calling a tool missing: `edit_audio_item` trims and fades, `render_time_selection` takes its own start and end, `set_send_routing` raises the destination's channel count for a sidechain. |
| More than about three operations in one step | **Bridge** | A tool call costs 150-600 ms; one bridge call costs roughly 300 ms however many API calls it contains. Ten reads is one bridge call, not ten tool calls. Writes of one kind go in one call of a batch tool instead: `add_envelope_points`, `add_markers`, `edit_markers`, `edit_items`. Their indices refer to the project before the call, and they report each entry's indices after it. |
| Confirming what a tool reported | **Bridge** | The bridge reads REAPER directly, so it is the independent witness. A tool's response is a claim about its work, not evidence of it. |
| Reading state no tool returns | **Bridge** | Track and item selection, play position, take offsets, source lengths, render settings, item fades and gain before an edit (`get_track_info` lists only position and length). |
| Setting a parameter in display units | **Bridge, then tool** | Find the normalized value with the read-only `find_norm` in one bridge command (protocol step 3d), then write it once with `set_fx_parameter`. See [Plugin protocol](./references/plugin-protocol.md). |
| Offline DSP measurement | **Bridge** | Reading samples, band analysis, arrangement maps. |
| Anything reapy gets wrong | **Bridge** | The bridge runs native ReaScript inside REAPER and skips the reapy wrapper layer entirely, along with its silent no-ops. |

Two habits follow:

- **Batch through the bridge, act through the tools.** Gather what you need to know in one Lua call, decide, then make the change with the tool built for it.
- **Verify across routes.** After a tool reports success on something that matters, read the value back through the bridge. When both agree, the change is real. This is how the tool defects in [Driving REAPER from Python](./references/python-reaper-tools.md) were found: three tools reported success on every call while changing nothing.

### Using both routes in one task

- **Say when a change goes through the bridge.** Before changing the project with Lua, tell the user in one line and name what the tools lack, for example "No tool sets track polarity, so the flip goes through the bridge." Reads and read-backs need no announcement. When the same gap sends you to the bridge repeatedly, say so: it is a candidate for a new tool.
- **One route at a time.** Do not issue tool calls and bridge calls in parallel. Both act on the same project, and a read on one route can land before a write on the other. Verifying across routes is sequential: the tool returns, then the bridge reads.
- **Re-read indices after the bridge changes structure.** The tools address tracks, FX, sends and items by index. After a bridge chunk adds, deletes or moves any of them, call `list_tracks` (or `list_track_fx`, `list_sends`) before the next tool call that takes an index.
- **One change per bridge write.** Each bridge command already runs inside its own undo block, so a chunk that does one job is one Ctrl+Z for the user. Batch reads freely; keep writes separate.
- **Check the listener before the first bridge call.** Run `return reaper.GetAppVersion()` with `--timeout 5`. A timeout means `claude_bridge.lua` is not running: tell the user, carry on with the tools where they cover the task, and see Troubleshooting below.

## Running Lua through the bridge

Pass Lua code directly. Do not use a temporary file. Windows PowerShell 5.1 writes a UTF-8 BOM, resulting in a `PARSE_ERROR` from the bridge.

```bash
"${CLAUDE_PLUGIN_ROOT}/scripts/reaper-bridge" --code 'return reaper.GetAppVersion()'
```

If the wrapper cannot run (no `sh`, or it picks the wrong interpreter), call the script directly:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/bridge.py" --code 'return reaper.GetAppVersion()'
```

For multiline scripts, write the Lua code to a file using a file-writing tool and pass `--lua-file`. The client will strip the BOM.

Rules for Lua bridge execution:

- Always `return` a string from the chunk. A chunk returning nothing results in `OK`, indicating execution without data.
- **Only the first return value survives.** `return 7, 8, 9` yields `7`. Pack several values into a table, or build the string yourself.
- A returned table renders one line per array element, then any remaining keys as `k = v`, with nested tables shown compactly as `{1, 2}`. An empty table returns `{}`.
- **Globals persist between calls.** `_G.x` set in one chunk is readable in the next, which is useful for staging state across calls and worth clearing when finished.
- Each command runs inside an undo block named "Claude bridge command". A read-only command adds no undo point.
- Increase `--timeout` for renders. Renders block REAPER. The client monitors `out.txt` for changes rather than sleeping. A timeout indicates the process is slow, not necessarily failed; REAPER keeps executing the chunk after the client gives up.
- `PARSE_ERROR` and `RUNTIME_ERROR` set a non-zero exit code. Treat these as failures and read the error message. Line numbers refer to your code as written.

If `${CLAUDE_PLUGIN_ROOT}` is not substituted, locate the plugin directory manually (e.g., `scripts/bridge.py` under `~/.claude/plugins/` or the repository) and use the absolute path.

## Undo, backups and unconfirmed writes

Every tool that changes the project records one undo point per call, named `MCP: <tool>`; each bridge command records `Claude bridge command`. Transport, cursor, render, recording and project-file tools record none, since REAPER keeps none of that in undo history.

- `undo` steps back through REAPER's history and names each step it undid. It stops at a step it did not make, which is the user's own edit; pass `any_step` only when the user asked for that.
- Undoing restores the whole state saved at the previous undo point, so a change made with raw calls through reapy since then is reverted with it. Tools and bridge commands record their own changes, so this only concerns code outside both.
- **Backup.** Before the first change the server makes to a saved project, it writes a copy, unsaved changes included, as `<name>.mcp-backup-<time>.rpp` next to the project file, and keeps the newest five. The reply that made it carries `backup` with the path; tell the user once. A project never saved is not backed up. `REAPER_MCP_BACKUP=0` in the server's environment turns this off.
- **`unconfirmed`.** REAPER adds an undo point only when the project changed. A reply with `success: true` and `unconfirmed` left no `MCP: <tool>` step: the value was already set, or the write did not reach REAPER. Read the value back before reporting it as done.

## Verify writes

The `reapy` library can accept attribute assignments that do not affect REAPER. Operations like `fx.params[0].normalized_value = 0.6` and `track.armed = True` may succeed in Python without raising an error while having no effect.

A `success: true` response does not guarantee execution. An `unconfirmed` field in the reply means REAPER recorded no change; without it, some change was recorded, not necessarily the one asked for. Read the value back through `reascript_api` to confirm. Refer to [Driving REAPER from Python](./references/python-reaper-tools.md#verified-reapy-traps).

**Some plugins take a parameter write only in their audio callback**, iZotope Ozone 12 among them, so the value moves on the next audio block and never while REAPER's audio engine is closed ([REAPER in the background](#reaper-in-the-background)). Since 1.3.1 `set_fx_parameter` and `set_master_fx_parameter` follow such a write across cycles, open the engine for it when they must, and report the value REAPER holds; 1.3.0 read back at once and reported the old value, sometimes with `unconfirmed` and no undo step. Through the bridge, read back in a separate command and check `reaper.Audio_IsRunning()` before concluding that a write failed: a session that skipped this concluded that Ozone rejects host writes, and it does not. Labels mislead too: a list parameter's formatted value can name the entry next to the real one, which `get_fx_parameters` flags with `step_label`. Both traps and the read-only way around them are in [Plugin Control](./references/plugin-control.md#plugins-that-take-writes-in-their-audio-callback).

## System hangs

If MCP calls stop returning and the bridge times out without errors, a modal dialog is likely open in REAPER. This halts deferred scripts, including the reapy server and the Lua bridge.

Check the REAPER window. Refer to [Driving REAPER from Python](./references/python-reaper-tools.md#modal-dialogs-freeze-everything). To prevent hangs, check `IsProjectDirty` before replacing a project, arm tracks prior to starting transport, and avoid setting `RENDER_BOUNDSFLAG` to 0.

## Reference materials

- **[Driving REAPER from Python](./references/python-reaper-tools.md)**: reapy no-ops, modal dialogs, loop traps, latency, and benchmarks.
- **[Rendering Secrets](./references/rendering.md)**: The silent-render state (`offlineinact`), bounds flags, and measurement.
- **[Plugin protocol](./references/plugin-protocol.md)**: The procedure for every exchange with a plugin, its measured base, and the price of each step.
- **[Plugin Control](./references/plugin-control.md)**: The registry of measured plugins, read-only and scratch-instance searches, list parameters, known index traps.

## Verifying tools

`scripts/benchmark_tools.py` executes all 71 tools against REAPER, asserts expected responses, records execution time, and performs cleanup.

```bash
python scripts/benchmark_tools.py
```

Run this script after modifying tool modules. Asserts must be based on independently read values, not on success reports.

`scripts/check_undo.py` calls every tool that changes the project in a scratch tab, undoes once, and compares the saved project text before and after; it also checks the backup and the `unconfirmed` flag. A new tool that changes the project gets `@records_undo()` beneath `@mcp.tool()` (or `items=True` if it creates MIDI items) and an entry in that script's plan.

```bash
python scripts/check_undo.py
```

## Troubleshooting

Use reaper-core-setup for installation, health checks, and repairs.

| Symptom | Cause |
| --- | --- |
| Routes hang with no errors | A modal dialog is open in REAPER. |
| Tool reports success but nothing changed | A reapy attribute assignment did not reach REAPER. Read the value back. |
| No REAPER tools available | The MCP server did not start. Call `reaper_setup_status` or run the health check. No local server is available on claude.ai. |
| MCP tools fail with socket error | REAPER is not running, or the API is not configured. Persistent failures indicate configuration issues. |
| Bridge times out | REAPER is not running `claude_bridge.lua`. Check `status.txt` for heartbeat status. |
| `PARSE_ERROR` on byte one | Lua code contained a UTF-8 BOM. Use `--code` or `--lua-file`. |
| Renders are silent, an `analyze_*` tool replies `silent: true`, or (1.3.0) `-Infinity` LUFS with `success: true` | Media went offline (`offlineinact`), or tracks are muted or soloed away. Bridge renders need action 40101 first: [Renders through the bridge](#renders-through-the-bridge). |
| A plugin parameter write does not show, or shows only after the user clicks into REAPER | The plugin takes writes in its audio callback and the audio engine is closed (`audiocloseinactive`): [REAPER in the background](#reaper-in-the-background). |
| Render produces 0 bytes | `RENDER_FILE` received a full path while `RENDER_PATTERN` held a filename. Check `is_file()`. |
| `import reapy` fails | The interpreter cannot load reapy. Run the bootstrap via reaper-core-setup. |
