# Rendering from the bridge

Rendering is required to measure a bus, the mix, or the master. The following information defines the rendering process.

For an ordinary render to a file, use the `render_project`, `render_time_selection` and `render_stems` tools instead. They save and restore every `RENDER_*` field they touch, validate the format and bit depth, and refuse an empty project. Render from the bridge when you need something those tools do not offer: a partial range around a specific bar, a chain disabled for the duration, or measurement inside the same command.

## Contents

- [The recipe](#the-recipe)
- [Choosing the output format](#choosing-the-output-format)
- [Why renders come out silent](#why-renders-come-out-silent)
- [Bounds flags](#bounds-flags)
- [Polling instead of sleeping](#polling-instead-of-sleeping)
- [Measuring the result](#measuring-the-result)
- [Rendering one bus in isolation](#rendering-one-bus-in-isolation)
- [Timing](#timing)
- [Diagnosing a silent render](#diagnosing-a-silent-render)

## The recipe

Save the user's settings, render, and restore. The `40101` call is required.

```lua
local DIR = [[C:\path\to\scratch]]
local RES = DIR .. [[\result.txt]]
local function wr(s) local f = io.open(RES, "w") if f then f:write(s) f:close() end end
wr("PENDING")

reaper.Main_OnCommand(40101, 0)

local sv = {}
for _, k in ipairs({"RENDER_SETTINGS","RENDER_BOUNDSFLAG","RENDER_TAILFLAG"}) do
  sv[k] = reaper.GetSetProjectInfo(0, k, 0, false)
end
local _, svF = reaper.GetSetProjectInfo_String(0, "RENDER_FILE", "", false)
local _, svP = reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", "", false)
local ts, te = reaper.GetSet_LoopTimeRange(false, false, 0, 0, false)

reaper.GetSet_LoopTimeRange(true, false, 99.6, 121.0, false)
reaper.GetSetProjectInfo(0, "RENDER_SETTINGS",   0, true)
reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 2, true)
reaper.GetSetProjectInfo(0, "RENDER_TAILFLAG",   0, true)
reaper.GetSetProjectInfo_String(0, "RENDER_FILE", DIR, true)
reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", "chunk", true)

reaper.Main_OnCommand(41824, 0)

reaper.GetSet_LoopTimeRange(true, false, ts, te, false)
for k, v in pairs(sv) do reaper.GetSetProjectInfo(0, k, v, true) end
reaper.GetSetProjectInfo_String(0, "RENDER_FILE", svF, true)
reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", svP, true)
wr("DONE")
```

`RENDER_FILE` takes a directory. Providing a file path creates a folder with that name containing the render.

The recipe above saves the fields it changes. Add any other field you touch to that list, and note which ones are strings: `RENDER_FORMAT`, `RENDER_FILE` and `RENDER_PATTERN` are read and written with `GetSetProjectInfo_String`, the rest with `GetSetProjectInfo`.

## Choosing the output format

`RENDER_FORMAT` is a **string** holding a base64 configuration blob. Writing it as a number does nothing and reads back as `0.0`, and so does `RENDER_FORMAT2` for bit depth. A render then silently comes out in whatever format the project already had: a request for FLAC produces `name.wav`, and every bit depth request produces the project's existing depth.

The blob is a reversed four character code followed by format-specific settings. Verified by rendering and reading the resulting file headers:

| Format | Blob | Result |
|---|---|---|
| WAV | `evaw` + `depth, 0, 1` | depth 8 → PCM_U8, 16 → PCM_16, 24 → PCM_24, 32 → 32-bit float |
| FLAC | `calf` | PCM_16 at default compression |
| FLAC | `calf` + `<uint32 depth><uint32 compression>` | depth 16 or 24, compression 0-8 |
| MP3 | `l3pm` | MPEG Layer III |
| OGG | `vggo` | Vorbis |

```lua
-- 16-bit WAV. string.char builds the blob; the client encodes it as base64.
local blob = "evaw" .. string.char(16, 0, 1)
reaper.GetSetProjectInfo_String(0, "RENDER_FORMAT", base64_encode(blob), true)
```

The default 24-bit WAV blob is `ZXZhdxgAAQ==`, which decodes to `evaw\24\0\1`. To learn any other format's blob, set it once in REAPER's render dialog and read `RENDER_FORMAT` back.

`RENDER_SRATE` and `RENDER_CHANNELS` are numeric and behave as expected.

## Why renders come out silent

REAPER's preference `offlineinact = 1` ("set media items offline when application is not active") takes media items offline when REAPER loses focus. When run from a background process, REAPER does not have focus. This causes the items to go offline, resulting in a silent file of the correct length.

Check the preference:

```lua
local ok, v = reaper.get_config_var_string("offlineinact")
```

Detect the item state:

```lua
local src = reaper.GetMediaItemTake_Source(reaper.GetActiveTake(reaper.GetMediaItem(0, 0)))
reaper.GetMediaSourceLength(src)
```

Actions to manage media online status:

| Action | Effect |
|---|---|
| `40100` | set all media offline |
| `40101` | set all media online |

Call `40101` before every render made through the bridge, as the recipe above does. The MCP render and analysis tools call it themselves since 1.3.1; 1.3.0 did not, and needed it as a separate bridge command right before the tool call. The procedure and its check are in [Renders through the bridge](../SKILL.md#renders-through-the-bridge). Leave `offlineinact` as the user set it; some users keep it on deliberately.

## Bounds flags

`RENDER_BOUNDSFLAG`:

| Value | Meaning | Works via API? |
|---|---|---|
| 0 | custom time range | No: ignores `RENDER_STARTPOS`/`RENDER_ENDPOS` |
| 1 | entire project | Yes |
| 2 | time selection | Yes: set it with `GetSet_LoopTimeRange` |

Use value 2 for section checks and 1 for final measurement. Save and restore the user's time selection.

`RENDER_SETTINGS = 0` specifies the master mix. Other values render stems and produce multiple numbered files.

## Polling instead of sleeping

A full-song render blocks REAPER. The bridge does not write `out.txt` until the script returns, so a fixed sleep will read the previous command's output.

Write progress to a separate file from Lua and poll that file:

```powershell
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 8
  $r = Get-Content "$scratch\result.txt" -Raw -ErrorAction SilentlyContinue
  if ($r -and $r -notmatch "PENDING") { $r; break }
}
```

`scripts/bridge.py` watches the `out.txt` timestamp and matches a request id, so it returns neither the previous command's output nor another client's. Increase the `--timeout` parameter for renders instead of manual polling, unless the render duration exceeds the maximum acceptable timeout. A timeout only means the client stopped waiting; REAPER carries on rendering, and the file still appears.

## Measuring the result

Measurement can be done inside the Lua command using REAPER's API:

```lua
local src = reaper.PCM_Source_CreateFromFile(path)
local len = reaper.GetMediaSourceLength(src)
-- Target is in dB. Passing 1.0 makes every reading exactly 1 dB low.
local function m(md) return -20 * math.log(reaper.CalculateNormalization(src, md, 0.0, 0, len), 10) end
local I, S, T = m(0), m(5), m(3)
reaper.PCM_Source_Destroy(src)
```

Mode 3 is a real oversampled true peak and reads higher than the sample peak on limited material. See [Loudness of a file](../../reaper-audio-engineer/references/audio-measurement.md#loudness-of-a-file).

If `GetMediaSourceLength` returns 0 on a newly written file, recreate the source in a subsequent command.

If implementing a WAV parser in PowerShell, note that `[byte] -bor [int]` returns a byte, causing 24-bit samples to truncate. Cast with `[int]` before operations.

## Rendering one bus in isolation

To check the effect of a specific chain, solo the group or bus track. REAPER's solo does not propagate along send chains in all routing topologies.

To measure a chain without the master chain effects, disable the FX chains:

```lua
reaper.SetMediaTrackInfo_Value(premaster, "I_FXEN", 0)
reaper.SetMediaTrackInfo_Value(reaper.GetMasterTrack(0), "I_FXEN", 0)
```

Return tracks can be muted similarly. Restore states within the same command.

## Timing

Example timings on a 62-track project with approximately 90 plugins at 44.1 kHz:

| Range | Wall time |
|---|---|
| 2 s | ~2 s |
| 8 s | ~5 s |
| 21 s | ~12-14 s |
| 195 s | ~100 s |

Performance is approximately 2× real time. Use partial renders for iteration and full renders for final verification.

### The audio device around a render: normal, not a fault

REAPER stops the audio device before an offline render and starts it again afterwards. How long that takes is up to the audio driver, and it adds to every render while the engine is running, however short the render is. Measured on REAPER 7.82 with a Focusrite USB ASIO driver (64 samples, 48 kHz), rendering 3 s of audio:

| Audio engine just before the render | Wall time | Where it goes |
|---|---|---|
| Closed and settled | 1.1-1.4 s | the render itself |
| Closed moments ago | ~9.6 s | waiting for the driver to finish closing |
| Running | 18.5-18.8 s | ~8 s stopping the driver, 0.3 s rendering, ~8.7 s starting it again |

Where the output goes (Temp or another drive) and whether the project holds media made no difference. REAPER counts the wait as render time: its window reported "Finished in 0:08 (0.4x realtime)" for 3 s of audio written in 0.3 s.

The engine is running while REAPER has focus, while the transport plays or records, and after anything opened it while REAPER was in the background (playback, `Audio_Init`): with "Close audio device when stopped and application is inactive" on, REAPER closes it only at the moment it loses focus. `reaper.Audio_IsRunning()` tells which case a render will meet. The same driver timing is why opening the engine with `Audio_Init` took 0.6 s once and about 9 s another time.

This is how REAPER and the driver behave; there is nothing for the tools to fix. Closing the engine before a render would not help, since a render straight after closing still waits about 10 s, and it would change the user's audio state. Treat the wait as a fixed price per render while the engine runs, and make each render count: one render for several measurements, not one render per measurement. Measure the overhead on a new machine or driver once before planning many renders.

## Diagnosing a silent render

First separate "silent" from "absent". An empty project writes **no file at all**, and does so without raising a dialog: REAPER simply renders nothing and the render error you may be looking for never appears. Check `GetProjectLength(0)` before blaming routing or a modal.

For a file that exists but is silent, check the following properties:

1. Check `I_SOLO` on every track.
2. Check `GetMediaSourceLength` on a take source. A value of 0.0 indicates offline media.
3. Check `B_MUTE` and `I_FXEN` on every track.
4. Verify routing to the master track. Inspect `D_VOL`, `B_MAINSEND`, and send parameters.
5. Render with all FX chains disabled. If silent, verify routing and media. If audio is present, bisect the chains.
6. Verify individual plugins.

A render measuring near −90 dBFS indicates analog noise floor from plugins with no input. This indicates issues with media or routing.
