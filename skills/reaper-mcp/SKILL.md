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

### MCP server

Use the MCP server for structured tasks: projects, tempo, time signature, tracks, naming, volume, pan, solo, mute, colour, FX, MIDI items, chords, drum patterns, sends, buses, rendering, and stems.

### File bridge

Use the bridge for unsupported API functions:

- **Offline DSP measurement**: Reading samples, band analysis, arrangement maps.
- **Dynamic parameter search**: Setting a parameter by its formatted value (`"110 ms"`, `"-12 dB"`) using binary search when no tool maps it.
- **Batching**: Combining multiple operations into one bridge call to reduce overhead. Refer to [what a call costs](./references/python-reaper-tools.md#what-a-call-costs).

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
- Increase `--timeout` for renders. Renders block REAPER. The client monitors `out.txt` for changes rather than sleeping. A timeout indicates the process is slow, not necessarily failed.
- `PARSE_ERROR` and `RUNTIME_ERROR` set a non-zero exit code. Treat these as failures and read the error message.

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
