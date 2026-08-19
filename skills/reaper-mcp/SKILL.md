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
| **MCP server** | 58 structured tools (e.g., `create_track`, `add_fx`, `render_project`). | Claude Code and Claude Desktop, when the server is running. |
| **File bridge** | Arbitrary Lua executed inside REAPER. | Environments where shell commands can be executed on the host machine running REAPER. |

On claude.ai in the browser, neither route is available. State this limitation and use this document as a reference.

If REAPER tools are unavailable, call `reaper_setup_status` to identify the diagnostic status. Otherwise, use reaper-core-setup.

## Choosing a route

**Default to the MCP tools.** They validate their inputs, refuse impossible values, restore the settings they borrow, and return a structured result. Reach for the bridge when one of the conditions below holds, not as a matter of taste.

| Situation | Route | Why |
| --- | --- | --- |
| A tool covers the task | **MCP tool** | Validation, error messages and state restoration are already written and tested. Reimplementing them in Lua repeats debugged work. |
| Structured project work | **MCP tool** | Projects, tempo, time signature, tracks, naming, volume, pan, solo, mute, colour, FX, MIDI items, chords, drum patterns, sends, buses, rendering, stems. |
| Rendering to a file | **MCP tool** | `render_project`, `render_time_selection` and `render_stems` save and restore the `RENDER_*` project settings. Hand-written Lua leaves the user's render settings changed. |
| An operation that must be refused when wrong | **MCP tool** | The tools reject negative indices, out-of-range values and trims that would consume an item. Raw Lua applies whatever it is given. |
| More than about three operations in one step | **Bridge** | A tool call costs 150-600 ms; one bridge call costs roughly 300 ms however many API calls it contains. Ten reads is one bridge call, not ten tool calls. |
| Confirming what a tool reported | **Bridge** | The bridge reads REAPER directly, so it is the independent witness. A tool's response is a claim about its work, not evidence of it. |
| Reading state no tool returns | **Bridge** | Selection, play position, envelope scaling mode, take offsets, source lengths, render settings, item and marker layout. |
| Setting a parameter in display units | **Bridge** | Binary-searching a plugin's formatted value when no tool maps that parameter. See [Plugin Control](./references/plugin-control.md). |
| Offline DSP measurement | **Bridge** | Reading samples, band analysis, arrangement maps. |
| Anything reapy gets wrong | **Bridge** | The bridge runs native ReaScript inside REAPER and skips the reapy wrapper layer entirely, along with its silent no-ops. |

Two habits follow:

- **Batch through the bridge, act through the tools.** Gather what you need to know in one Lua call, decide, then make the change with the tool built for it.
- **Verify across routes.** After a tool reports success on something that matters, read the value back through the bridge. When both agree, the change is real. This is how the tool defects in [Driving REAPER from Python](./references/python-reaper-tools.md) were found: three tools reported success on every call while changing nothing.

## Running Lua through the bridge

Pass Lua code directly. Do not use a temporary file. Windows PowerShell 5.1 writes a UTF-8 BOM, resulting in a `PARSE_ERROR` from the bridge.

```bash
reaper-bridge --code 'return reaper.GetAppVersion()'
```

If `reaper-bridge` is not on the PATH, call the script directly:

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

## Verify writes

The `reapy` library can accept attribute assignments that do not affect REAPER. Operations like `fx.params[0].normalized_value = 0.6` and `track.armed = True` may succeed in Python without raising an error while having no effect.

A `success: true` response does not guarantee execution. Read the value back through `reascript_api` to confirm. Refer to [Driving REAPER from Python](./references/python-reaper-tools.md#verified-reapy-traps).

## System hangs

If MCP calls stop returning and the bridge times out without errors, a modal dialog is likely open in REAPER. This halts deferred scripts, including the reapy server and the Lua bridge.

Check the REAPER window. Refer to [Driving REAPER from Python](./references/python-reaper-tools.md#modal-dialogs-freeze-everything). To prevent hangs, check `IsProjectDirty` before replacing a project, arm tracks prior to starting transport, and avoid setting `RENDER_BOUNDSFLAG` to 0.

## Reference materials

- **[Driving REAPER from Python](./references/python-reaper-tools.md)**: reapy no-ops, modal dialogs, loop traps, latency, and benchmarks.
- **[Rendering Secrets](./references/rendering.md)**: The silent-render state (`offlineinact`), bounds flags, and measurement.
- **[Plugin Control](./references/plugin-control.md)**: Binary-searching parameters by formatted value, known index traps.

## Verifying tools

`scripts/benchmark_tools.py` executes all 58 tools against REAPER, asserts expected responses, records execution time, and performs cleanup.

```bash
python scripts/benchmark_tools.py
```

Run this script after modifying tool modules. Asserts must be based on independently read values, not on success reports.

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
| Renders are silent | The `offlineinact` preference is enabled. |
| Render produces 0 bytes | `RENDER_FILE` received a full path while `RENDER_PATTERN` held a filename. Check `is_file()`. |
| `import reapy` fails | The interpreter cannot load reapy. Run the bootstrap via reaper-core-setup. |
