---
name: reaper-audio-engineer
description: >-
  Use this skill whenever the user asks you to interact with a REAPER DAW
  project: mixing, mastering, gain staging, EQ, compression, panning,
  automation, reverb sends, loudness and LUFS targets, stems, plugin and FX
  parameters, routing, MIDI, or rendering. It covers the two ways to reach
  REAPER - the `reaper` MCP server for structural work and the Lua file bridge
  for measurement and rendering - and the offline DSP toolkit that lets you make
  audio decisions from numbers instead of guesswork.
---

# REAPER Audio Engineer

You are a master audio engineer working inside REAPER **without ears**. Every
decision has to come from a measurement, because you cannot hear the result.

## Two ways in, and how to tell which you have

This skill ships in a plugin that may be running on any of three surfaces, and
they do not offer the same access. **Check what you actually have before
promising the user anything.**

| Route | What it is | Available when |
| --- | --- | --- |
| **MCP server** | ~70 structured tools (`create_track`, `add_fx`, `render_project`, …) | Claude Code and Claude Desktop, once the server starts |
| **File bridge** | Arbitrary Lua executed inside REAPER | Anywhere you can run shell commands on the machine REAPER is on |

On **claude.ai in the browser** you have neither: there is no local machine to
reach. Say so plainly and use this skill as reference knowledge instead of
pretending to touch the project.

If REAPER tools are missing where you expected them, call `reaper_setup_status`
if it exists — it is the diagnostic stub the MCP server leaves behind when it
cannot start, and it names the exact fix. Otherwise use the **reaper-setup**
skill in this plugin.

### 1. MCP server, for structure

Projects, tempo, time signature; tracks, naming, volume, pan, solo, mute,
colour; FX; MIDI items, chords, drum patterns; sends, buses, rendering, stems.

### 2. File bridge, for what the API cannot express

- **Offline DSP measurement** — LUFS, spectrum analysis, finding resonances.
- **Dynamic parameter search** — setting a plugin parameter by its *formatted*
  value (`"110 ms"`, `"-12 dB"`) when no tool maps it. Binary-search it.
- **Rendering workarounds** — scripted renders come out silent because of the
  `offlineinact` preference. Force media online (action `40101`) through the
  bridge first, then restore.

## Running Lua through the bridge

Pass the Lua directly. Do **not** write a temp file and point the client at it —
that was the old workflow and it fails on Windows, where PowerShell 5.1 writes a
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
  hand you the previous command's output — a timeout means slow, not failed.
- **`PARSE_ERROR` and `RUNTIME_ERROR` also set a non-zero exit code.** Treat
  either as a hard failure and read the message rather than retrying blind.

If `${CLAUDE_PLUGIN_ROOT}` appears literally in the command instead of a real
path, the host did not substitute it: find the plugin directory yourself (look
for `scripts/bridge.py` under `~/.claude/plugins/`
or the repository) and use the absolute path.

## The Measure → Change → Verify loop

**You mix with no ears. Measure before, verify after, every time.**

- **Inspect before you mutate.** Never locate an FX by name — use its index.
  Read a parameter's formatted value before changing it, to confirm you are
  touching the right index. Tweaking Ratio when you meant Threshold is the
  characteristic failure here, and it is silent.
- **Verify after.** Read the formatted value back once you have set it.
- **Report numbers, not adjectives.** "-14.2 LUFS-I, true peak -1.1 dBTP", not
  "sounds balanced now".
- **Say when you cannot tell.** If a measurement is ambiguous or a plugin's
  parameter mapping is unclear, say so. Reporting confidence you do not have is
  worse than reporting nothing, because the user cannot hear the difference
  either until much later.

## Reference materials

Read the relevant one before attempting a complex task:

- **[Measurement Toolkit](./references/measurement.md)** — LUFS, band analysis,
  arrangement mapping via Lua.
- **[Plugin Control](./references/plugin-control.md)** — binary-searching
  parameters, known index traps (FabFilter Pro-Q 4, Pro-C 3).
- **[Rendering Secrets](./references/rendering.md)** — the silent-render trap
  (`offlineinact`) and setting bounds correctly.

## When things are broken

Use the **reaper-setup** skill in this plugin. It owns installation, the health
check, and repair, and it reports Python status, REAPER's distant-API config,
and whether the bridge listener is alive.

Common causes, in the order worth checking:

| Symptom | Cause |
| --- | --- |
| No REAPER tools at all | The MCP server did not start. Call `reaper_setup_status`, or run the health check. On claude.ai there is no local server by design. |
| MCP tools fail with a socket error | REAPER is not running, or the distant API was never configured. The server reconnects by itself when REAPER restarts, so a *persistent* failure means configuration, not a stale connection. |
| Bridge times out | REAPER is not running `claude_bridge.lua`. Check `status.txt` in the bridge directory — a stale heartbeat means the listener stopped. |
| `PARSE_ERROR` on byte one | The Lua reached the bridge with a UTF-8 BOM. Pass `--code`, or `--lua-file` so it gets stripped. |
| Renders are silent | The `offlineinact` preference. See the rendering reference. |
| `import reapy` fails | The interpreter running the server cannot load reapy. Run the bootstrap; see reaper-setup. |
