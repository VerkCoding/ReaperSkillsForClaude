# Rendering from the bridge

Rendering is the only way to measure a bus, the mix or the master, so you will need it. It is
also the part of the bridge most likely to waste an hour. Everything here was established by
elimination against a real project.

## Contents

- [The recipe](#the-recipe)
- [Why renders come out silent](#why-renders-come-out-silent)
- [Bounds flags](#bounds-flags)
- [Polling instead of sleeping](#polling-instead-of-sleeping)
- [Measuring the result](#measuring-the-result)
- [Rendering one bus in isolation](#rendering-one-bus-in-isolation)
- [Timing](#timing)
- [Diagnosing a silent render](#diagnosing-a-silent-render)

## The recipe

Save the user's settings, render, restore. The `40101` call is not optional.

```lua
local DIR = [[C:\path\to\scratch]]           -- a DIRECTORY, not a file path
local RES = DIR .. [[\result.txt]]
local function wr(s) local f = io.open(RES, "w") if f then f:write(s) f:close() end end
wr("PENDING")

reaper.Main_OnCommand(40101, 0)              -- Media: set all media ONLINE

local sv = {}
for _, k in ipairs({"RENDER_SETTINGS","RENDER_BOUNDSFLAG","RENDER_TAILFLAG"}) do
  sv[k] = reaper.GetSetProjectInfo(0, k, 0, false)
end
local _, svF = reaper.GetSetProjectInfo_String(0, "RENDER_FILE", "", false)
local _, svP = reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", "", false)
local ts, te = reaper.GetSet_LoopTimeRange(false, false, 0, 0, false)

reaper.GetSet_LoopTimeRange(true, false, 99.6, 121.0, false)   -- the range you want
reaper.GetSetProjectInfo(0, "RENDER_SETTINGS",   0, true)      -- master mix
reaper.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 2, true)      -- time selection
reaper.GetSetProjectInfo(0, "RENDER_TAILFLAG",   0, true)      -- no 4 s tail
reaper.GetSetProjectInfo_String(0, "RENDER_FILE", DIR, true)
reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", "chunk", true)

reaper.Main_OnCommand(41824, 0)              -- render, auto-close dialog

reaper.GetSet_LoopTimeRange(true, false, ts, te, false)
for k, v in pairs(sv) do reaper.GetSetProjectInfo(0, k, v, true) end
reaper.GetSetProjectInfo_String(0, "RENDER_FILE", svF, true)
reaper.GetSetProjectInfo_String(0, "RENDER_PATTERN", svP, true)
wr("DONE")
```

`RENDER_FILE` is a directory. Giving it a path with a filename stem creates a folder of that
name and puts the render inside — harmless but confusing when you go looking for the file.

## Why renders come out silent

REAPER's preference **`offlineinact = 1`** — "set media items offline when application is not
active" — takes every media item offline whenever REAPER loses focus. Driving it from a
background process means REAPER never has focus. Items contribute nothing, and you get a
file of exactly the right length containing silence.

Confirm the preference:

```lua
local ok, v = reaper.get_config_var_string("offlineinact")   -- "1" means the trap is armed
```

Detect the state at any moment — this is the reliable signal:

```lua
local src = reaper.GetMediaItemTake_Source(reaper.GetActiveTake(reaper.GetMediaItem(0, 0)))
reaper.GetMediaSourceLength(src)   -- 0.0 when offline, real length when online
```

Actions, verified by measuring source length either side of each:

| Action | Effect |
|---|---|
| `40100` | set all media **offline** |
| `40101` | set all media **online** |

Getting these backwards makes everything silent, so if you are unsure, call one and check the
source length rather than trusting memory.

Offer the user the permanent fix — turning `offlineinact` off in Preferences → Media — but
treat it as their setting to change, and keep calling `40101` regardless so the skill works on
an unmodified machine.

## Bounds flags

`RENDER_BOUNDSFLAG`:

| Value | Meaning | Works via API? |
|---|---|---|
| 0 | custom time range | **No** — silently ignores `RENDER_STARTPOS`/`RENDER_ENDPOS` |
| 1 | entire project | Yes |
| 2 | time selection | Yes — set it with `GetSet_LoopTimeRange` |

Use 2 for section checks and 1 for the final measurement. Save and restore the user's time
selection; they very likely have one they care about.

`RENDER_SETTINGS = 0` is the master mix. Other values render stems and produce multiple
numbered files, which is a good way to be confused about why there are two outputs.

## Polling instead of sleeping

A full-song render blocks REAPER for a minute or two. The bridge cannot write `out.txt` until
your script returns, so a fixed sleep will read the *previous* command's output and you will
draw conclusions from stale data.

Write progress to a side file from inside the Lua and poll that file:

```powershell
for ($i=0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 8
  $r = Get-Content "$scratch\result.txt" -Raw -ErrorAction SilentlyContinue
  if ($r -and $r -notmatch "PENDING") { $r; break }
}
```

`scripts/bridge.py` — the client behind `reaper-bridge` — does the equivalent for
ordinary commands by watching `out.txt`'s timestamp rather than sleeping, so it
never returns the previous command's output. Raise `--timeout` for a render
instead of polling by hand; reach for the side-file pattern above only when the
render outlives any timeout you are willing to set.

## Measuring the result

Do the measurement inside the same Lua command, using REAPER's own analyser:

```lua
local src = reaper.PCM_Source_CreateFromFile(path)
local len = reaper.GetMediaSourceLength(src)
local function m(md) return -20 * math.log(reaper.CalculateNormalization(src, md, 1.0, 0, len), 10) end
local I, S, T = m(0), m(5), m(3)     -- LUFS-I, LUFS-S max, true peak dBTP
reaper.PCM_Source_Destroy(src)
```

Occasionally `GetMediaSourceLength` returns 0 on a file REAPER has only just finished writing.
Re-create the source in a subsequent command rather than concluding the render failed.

Do **not** write your own WAV parser in PowerShell to double-check. In PowerShell,
`[byte] -bor [int]` returns a **byte**, so 24-bit samples truncate to 0–255 and every file —
including known-good ones — measures a peak of exactly −90.34 dBFS. If you ever see that
number on more than one file, the parser is lying, not the audio. Cast with `[int]` first if
you really need an independent check.

## Rendering one bus in isolation

Useful for checking what a chain actually did to a source. Solo the **group or bus** track,
not a tier that feeds downstream purely through explicit sends — REAPER's solo does not
propagate along send chains in every routing topology, and you will render silence.

To measure a chain without the mastering chain colouring the result, disable the FX chains
rather than bypassing plugins one by one:

```lua
reaper.SetMediaTrackInfo_Value(premaster, "I_FXEN", 0)
reaper.SetMediaTrackInfo_Value(reaper.GetMasterTrack(0), "I_FXEN", 0)
-- render, then restore both
```

Mute reverb/delay return tracks the same way if you need the dry signal. Restore everything in
the same command so a failure cannot leave the project in a strange state.

## Timing

Measured on a 62-track project with roughly 90 plugins, 44.1 kHz:

| Range | Wall time |
|---|---|
| 2 s | ~2 s |
| 8 s | ~5 s |
| 21 s | ~12–14 s |
| 195 s (full song) | ~100 s |

Roughly 2× real time. Budget for it: prefer one 20 s chorus render for iteration and reserve
full-song renders for final verification.

## Diagnosing a silent render

In order, because each step rules out a whole class of cause:

1. **Is a track soloed?** Walk every track's `I_SOLO`. A solo left on from a previous session
   silences everything except one element, which may not play in the range you rendered.
2. **Is media online?** Check `GetMediaSourceLength` on a take source. 0.0 means offline.
3. **Is anything muted, or is an FX chain disabled?** Check `B_MUTE` and `I_FXEN` per track.
4. **Does the routing reach the master?** Walk one source-to-master path printing `D_VOL`,
   `B_MAINSEND` and every send's destination and level.
5. **Render with all FX chains disabled.** If that is silent too, the problem is upstream of
   the plugins — routing or media. If it has audio, bisect the chains.
6. **Only then** suspect a plugin.

A render that measures around −90 dBFS rather than true digital silence is usually the analog
noise floor of the mix-bus plugins with no input reaching them — which points at media or
routing, not at the plugins themselves.
