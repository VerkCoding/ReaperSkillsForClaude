---
name: reaper-mcp
description: >-
  How Claude actually reaches REAPER and makes it do things: the `reaper` MCP
  tool surface and the Lua file bridge. Use when calling REAPER tools, when a
  tool fails or returns something that looks wrong, when a call hangs, when a
  render comes out silent or empty, when setting a plugin parameter that no tool
  maps, or when writing, debugging or benchmarking the MCP server itself. This
  is the transport layer; for what to measure and which move to make, use
  reaper-audio-engineer.
---

# Reaching REAPER

This skill owns the **channel**: getting a command into REAPER and a trustworthy
answer back. It does not decide what the command should be. That is
**reaper-audio-engineer**.

## Two routes, and how to tell which you have

**Check what you actually have before promising the user anything.**

| Route | What it is | Available when |
| --- | --- | --- |
| **MCP server** | 58 structured tools (`create_track`, `add_fx`, `render_project`, …) | Claude Code and Claude Desktop, once the server starts |
| **File bridge** | Arbitrary Lua executed inside REAPER | Anywhere you can run shell commands on the machine REAPER is on |

On **claude.ai in the browser** you have neither: there is no local machine to
reach. Say so plainly and use these skills as reference knowledge instead of
pretending to touch the project.

If REAPER tools are missing where you expected them, call `reaper_setup_status`
if it exists. It is the diagnostic stub the MCP server leaves behind when it
cannot start, and it names the exact fix. Otherwise use **reaper-core-setup**.

### Use the MCP server for structure

Projects, tempo, time signature; tracks, naming, volume, pan, solo, mute,
colour; FX; MIDI items, chords, drum patterns; sends, buses, rendering, stems.

### Use the bridge for what the API cannot express

- **Offline DSP measurement**: reading samples, band analysis, arrangement maps.
- **Dynamic parameter search**: setting a parameter by its *formatted* value
  (`"110 ms"`, `"-12 dB"`) when no tool maps it. Binary-search it.
- **Batching**: one bridge round trip returning a table beats forty MCP calls.
  See [what a call costs](./references/python-reaper-tools.md#what-a-call-costs).

## Running Lua through the bridge

Pass the Lua directly. Do **not** write a temp file and point the client at it.
That was the old workflow and it fails on Windows, where PowerShell 5.1 writes a
UTF-8 BOM and the bridge answers `PARSE_ERROR` on byte one.

```bash
reaper-bridge --code 'return reaper.GetAppVersion()'
```

`reaper-bridge` is on the Bash tool's PATH whenever this plugin is enabled. If
it is not found, call the script directly:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/bridge.py" --code 'return reaper.GetAppVersion()'
```

For anything longer than a line, write the Lua to a file with a file-writing
tool and pass `--lua-file`; the client strips a BOM if one got in.

Rules that matter:

- **Always `return` a string** from your chunk. A chunk that returns nothing
  answers `OK`, which tells you it ran and nothing else.
- **Raise `--timeout` for renders.** They block REAPER for minutes. The client
  waits for `out.txt` to actually change rather than sleeping, so it will never
  hand you the previous command's output; a timeout means slow, not failed.
- **`PARSE_ERROR` and `RUNTIME_ERROR` also set a non-zero exit code.** Treat
  either as a hard failure and read the message rather than retrying blind.

If `${CLAUDE_PLUGIN_ROOT}` appears literally in the command instead of a real
path, the host did not substitute it: find the plugin directory yourself (look
for `scripts/bridge.py` under `~/.claude/plugins/` or the repository) and use the
absolute path.

## Never trust a write you have not read back

The single most expensive lesson in this codebase. reapy accepts writes that
never reach REAPER. `fx.params[0].normalized_value = 0.6` and `track.armed =
True` both succeed in Python, return no error, and change nothing, because the
attribute does not exist and Python is happy to invent it.

**A tool reporting `success: true` is not evidence that anything happened.**
Read the value back through `reascript_api` before believing it. The full list
of verified traps is in
[Driving REAPER from Python](./references/python-reaper-tools.md#verified-reapy-traps).

## When everything hangs at once

If MCP calls stop returning **and** the bridge times out, with no error
anywhere: **a modal dialog is open in REAPER**. It halts every deferred script,
which is both the reapy server and the Lua bridge. Nothing failed, so nothing is
logged. It is still waiting.

Look at REAPER's window. The four dialogs that come up in ordinary use, and
which are safe to dismiss, are in
[Driving REAPER from Python](./references/python-reaper-tools.md#modal-dialogs-freeze-everything).
Prevention is cheap: check `IsProjectDirty` before replacing a project, arm a
track before starting the transport, and never set `RENDER_BOUNDSFLAG` to 0.

## Reference materials

Read the relevant one before attempting a complex task:

- **[Driving REAPER from Python](./references/python-reaper-tools.md)**: reapy's
  silent no-ops, modal dialogs, unbounded-loop traps, per-call latency, and the
  tool benchmark.
- **[Rendering Secrets](./references/rendering.md)**: the silent-render trap
  (`offlineinact`), bounds flags, and measuring the result.
- **[Plugin Control](./references/plugin-control.md)**: binary-searching
  parameters by formatted value, known index traps (FabFilter Pro-Q 4, Pro-C 3).

## Verifying the tool surface

`scripts/benchmark_tools.py` calls all 58 tools against a live REAPER, asserts
on what comes back, times every call, and cleans up after itself.

```bash
python scripts/benchmark_tools.py
```

Run it after changing any tool module. When adding a case, assert on a value
read back **independently**, not on `success`, and not on the setter's own
report, since that is exactly how the silent no-ops survived.

## When things are broken

Use **reaper-core-setup**. It owns installation, the health check and repair.

| Symptom | Cause |
| --- | --- |
| Every route hangs at once, no error anywhere | A modal dialog is open in REAPER. See above. |
| A tool reports success but nothing changed | A reapy attribute assignment that never reached REAPER. Read it back. |
| No REAPER tools at all | The MCP server did not start. Call `reaper_setup_status`, or run the health check. On claude.ai there is no local server by design. |
| MCP tools fail with a socket error | REAPER is not running, or the distant API was never configured. The server reconnects by itself when REAPER restarts, so a *persistent* failure means configuration, not a stale connection. |
| Bridge times out | REAPER is not running `claude_bridge.lua`. Check `status.txt` in the bridge directory; a stale heartbeat means the listener stopped. |
| `PARSE_ERROR` on byte one | The Lua reached the bridge with a UTF-8 BOM. Pass `--code`, or `--lua-file` so it gets stripped. |
| Renders are silent | The `offlineinact` preference. See the rendering reference. |
| A render "succeeds" with 0 bytes | `RENDER_FILE` was given a full path while `RENDER_PATTERN` held a filename, so REAPER made a *directory* of that name. Check `is_file()`, never `exists()`. |
| `import reapy` fails | The interpreter running the server cannot load reapy. Run the bootstrap; see reaper-core-setup. |
