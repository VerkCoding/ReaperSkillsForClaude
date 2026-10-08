# Controlling plugin parameters

Every plugin parameter has a normalised 0-1 view, and every plugin maps that range differently. Two methods exist to find specific parameter values.

> **Follow the [Plugin protocol](./plugin-protocol.md) for every exchange with a plugin.** This file holds the techniques the protocol uses and the [registry of measured plugins](#registry-of-measured-plugins). A plugin that leaves the protocol's base gets a row there.

## Contents

- [Registry of measured plugins](#registry-of-measured-plugins)
- [Find the parameter first](#find-the-parameter-first)
- [Normalised versus native values](#normalised-versus-native-values)
- [Plugins that take writes in their audio callback](#plugins-that-take-writes-in-their-audio-callback)
- [Searching without writing](#searching-without-writing)
- [Searching on a scratch instance](#searching-on-a-scratch-instance)
- [Binary search by formatted value](#binary-search-by-formatted-value)
- [List parameters: trust the number, not the label](#list-parameters-trust-the-number-not-the-label)
- [Stock Cockos plugins](#stock-cockos-plugins)
- [FabFilter Pro-Q 4](#fabfilter-pro-q-4)
- [FabFilter Pro-C 3](#fabfilter-pro-c-3)
- [iZotope Ozone 12](#izotope-ozone-12)
- [Known index traps](#known-index-traps)
- [Latency](#latency)
- [Sidechain routing](#sidechain-routing)

## Find the parameter first

Do not assume an index. Dump names alongside values to read the layout:

```lua
for p = 0, math.min(reaper.TrackFX_GetNumParams(tr, fx), 40) - 1 do
  local _, n = reaper.TrackFX_GetParamName(tr, fx, p, "")
  local _, v = reaper.TrackFX_GetFormattedParamValue(tr, fx, p, "")
  out[#out+1] = ("%d %s = %s"):format(p, n, v)
end
```

Do not locate an FX by plugin name. Renamed FX occur in organised templates. Name-based lookup may find the wrong slot, or, with `TrackFX_AddByName`, add a duplicate. Enumerate the chain and match on position and expected role.

Large plugins pad parameter lists with MIDI CC entries. Limit the parameter dump: `get_fx_parameters` takes `name_contains`, `start` and `max_params`.

## Registry of measured plugins

One row per plugin, measured on REAPER 7.82, Windows, 2026-10-08. "Writes" is how a write through `TrackFX_SetParamNormalized` lands: **at once** (readable in the same Lua chunk), **callback** (only in the audio callback, so not while the audio engine is closed; `set_fx_parameter` 1.3.1 handles it), **snaps** (at once, moved to the plugin's own step), **ignored**. "Lists" is how a list parameter's normalized value becomes an entry: **round**, **floor** (truncate), **SDK** (floor(v × (steps + 1))). Parameter counts include REAPER's appended `Bypass`, `Wet`, `Delta`.

| Plugin as REAPER names it | Params | Padding | Lists | Writes | Load | Notes |
|---|---|---|---|---|---|---|
| `VST3: Ozone 12 (iZotope)` | 876 | none | round (111) | callback | 3.6 s | module prefixes; True Peak switch not exposed; [section](#izotope-ozone-12) |
| `VST3: Nectar 4 Delay (iZotope)` | 27 | none | round (3) | callback | 0.05 s | display path drops units |
| `CLAP: reVUe (Blenheim Sound)` | 4 | none | — | callback | <0.01 s | one control (Calibration) |
| `VST3: soothe2 (oeksound)` | 60 | none | round (10) | at once | 0.17 s | display path drops units; kilo notation "1k", "2k5"; [indices](#known-index-traps) |
| `VST3: SSLGChannel Stereo (Waves)` | 167 | 130 MIDI CC, empty display | — | at once | 0.02 s | CompThresh runs backwards; [indices](#known-index-traps) |
| `VST3: Scheps Omni Channel 2 Stereo (Waves)` | 1950 | 130 MIDI CC, empty display | round (27) | at once | 0.28 s | 1,629 `Insert` entries that format only their current value; **L and R are separate parameters**; [indices](#known-index-traps) |
| `VST3: GTR Amp Stereo (Waves)` | 155 | 130 MIDI CC, empty display | round (5) | at once | 0.53 s | |
| `VST3: Acon Digital DeNoise 2 (Acon Digital)` | 27 | none | round (2) | at once | 0.01 s | display doubles units ("5.0 dB dB") |
| `VST3: smartChain (sonible)` | 120 | none | **floor** (16) | at once | 0.62 s | Profile label off by one; `110` Output Trim linear ±24 dB, `norm = (dB + 24) / 48`, one per instance; [topic](../../reaper-audio-engineer/references/topics/plugin-sonible-smartchain.md) |
| `VST3: Pro-L 2 (FabFilter)` | 172 | MIDI entries 36-167 | round (5) | at once | — | `0` Gain linear 0 to +30 dB, `norm = dB / 30`; `18` Output Level −30 to 0 dBTP, −1.00 = 0.966517; `10` True Peak Limiting (on by default); `9` Oversampling, 4x = 0.4; `2` Lookahead linear 0-5 ms |
| `VST3: Rev SPRING-636 (Arturia)` | 2196 | 2,080 "MIDI CC helper", plus MPE and program-change entries | **SDK** (17) | at once | 0.05 s | |
| `VST3: kHs Reverb (Kilohearts)` | 2090 | 2,080 "MIDI", empty display | — | at once | 0.01 s | 7 real controls; U+202F before units; Decay runs "500 ms" to "30.0 s" |
| `VST3: UADx Pure Plate Reverb (Universal Audio (UADx))` | 2093 | 2,080 "MIDI CC #\|#" | round (1) | at once | 0.13 s | 10 real controls |
| `VST3: ValhallaFutureVerb (Valhalla DSP, LLC)` | 36 | none | — | at once | 0.60 s | **formats only its current value**: search on a [scratch instance](#searching-on-a-scratch-instance); Mix 30 % = 0.2995-0.30 |
| `VST3: SPAN Plus (Voxengo)` | 6 | none | round (1) | at once | 0.01 s | |
| `VST3: Youlean Loudness Meter 2 (Youlean)` | 5 | none | — | **ignored** (Preset) | 0.11 s | switches do not format |
| `JS: Loudness Meter Peak/RMS/LUFS (Cockos)` | 26 | none | floor, in native units (3) | snaps | 0.03 s | steps reported in native units |
| `VST: ReaEQ (Cockos)` | 19 | none | not measured | at once | — | [stock layouts](#stock-cockos-plugins) |
| `JS: RBJ Stereo Image Filter` | 11 | none | — | at once | — | `1` S - HP (Scale), 0-100 in 0.05 steps, normalized = scale / 100; the corner frequency is not linear in the scale: table in [reaper-panning](../../reaper-panning/SKILL.md#mono-low-end-with-stock-fx) |

## Normalised versus native values

Two setters exist and they do not take the same number.

- `TrackFX_SetParamNormalized(tr, fx, p, v)` takes 0-1 across the parameter's whole range.
- `TrackFX_SetParam(tr, fx, p, v)` takes the plugin's **native** scale, which `TrackFX_GetParam(tr, fx, p, 0, 0)` reports at indices 4 and 5.

The native scale is an internal number that bears no relation to the displayed units. ReaComp's Threshold displays decibels and reports a native range of 0 to 2; ReaComp's RMS size reports 0 to 10, and ReaEQ's Global Gain 0 to 4. Writing a decibel figure into `TrackFX_SetParam` therefore clamps to an end of the range and sets something else entirely.

Measured on ReaComp's Threshold:

| Call | Threshold displays |
|---|---|
| `SetParam(0.0)` | `-inf` |
| `SetParam(1.0)` | `+0.0` |
| `SetParam(2.0)` (native max) | `+6.0` |
| `SetParamNormalized(0.0)` | `-inf` |
| `SetParamNormalized(0.5)` | `+0.0` |
| `SetParamNormalized(1.0)` | `+6.0` |

A search that steps `SetParam` from 0 to 1 covers only the lower half of that parameter and can never reach a target above 0 dB. **Search with `SetParamNormalized`.**

Reading back is subject to the same split: `TrackFX_GetParamNormalized` returns `-1.0` when REAPER rejects the target, for instance a negative index. Treat a negative read-back as a failed write rather than a value.

## Plugins that take writes in their audio callback

Most plugins take a host write at once. Some take it only in their audio callback, so the value moves on the next audio block, and **not at all while REAPER's audio engine is closed**. REAPER closes the engine when it is stopped and in the background if Preferences > Audio > "Close audio device when stopped and application is inactive" (`audiocloseinactive`) is on, and Claude drives REAPER from another window, so REAPER is usually in the background. `reaper.Audio_IsRunning()` tells which state it is in.

Measured on REAPER 7.82 with 16 plugins, writing with the engine closed, reading in the same Lua chunk, in the next command, and after `Audio_Init()` (registry column "Writes"): 12 took the write at once, three only in the audio callback (iZotope Ozone 12, iZotope Nectar 4 Delay, the CLAP reVUe), and one ignored it (Youlean's preset selector). The callback plugins read the old value in the same chunk and in the next command, and the new one after `Audio_Init()`. With the engine already running, Ozone 12 read the new value by the next read; Nectar 4 and reVUe were not tested that way.

A plugin that reads back the new value in the same chunk takes writes without the audio thread, so the engine state does not matter to it.

A waiting write lands as soon as the engine opens: when the user focuses REAPER or opens the plugin window, when playback starts, or on `reaper.Audio_Init()`. In one session a write of Ozone's Maximizer Input Gain waited unseen until the user opened Ozone, which then showed the value as though the user had set it. REAPER also recorded its own "Edit FX parameter" undo step at some point while a write to a freshly loaded Ozone was waiting.

What this broke before 1.3.1:

- **`set_fx_parameter` and `set_master_fx_parameter` read back in the same cycle.** On Ozone 12 they reported the old `value`, sometimes with `unconfirmed` and no undo step, while the write landed later, outside the undo history.
- **A same-chunk read cannot prove a write failed.** A session concluded "Ozone rejects host writes" from such reads. The writes had not failed; they were waiting for the audio engine.
- **The write-based binary search below converges on nonsense** on such a plugin, because each formatted read sees an old value. Use [Searching without writing](#searching-without-writing).
- **A write and its restore in one chunk net out:** only the last value lands. A "retry" with a different value would land too, later.

Since 1.3.1, `set_fx_parameter` and `set_master_fx_parameter` handle this themselves. A plugin that takes the write at once is read back and recorded in one cycle. Otherwise the tool reads again across cycles for up to a second; if the value has not moved and the engine is closed, it calls `Audio_Init()`, waits for the value, records the `MCP: <tool>` undo step, then calls `Audio_Quit()` so the user's preference keeps its effect. The reply's `note` says when this happened. Opening the device can take from under a second to several seconds, depending on the driver.

Writing through the bridge, do the same by hand: read back in a **separate** command, and if the value has not moved, check `reaper.Audio_IsRunning()` before concluding anything. Add any new plugin you measure to the table above.

## Searching without writing

`TrackFX_FormatParamValueNormalized` asks the plugin to format a normalised value without setting it. A binary search on it finds the normalised value for a displayed target without touching the parameter, so it works on plugins that take writes in their audio callback and never sweeps a live parameter to its extremes. This is the protocol's route 3d.

Measured: 13 of the 15 plugins with continuous controls format any value. ValhallaFutureVerb returns its current display whatever value is asked for, and so do Scheps Omni Channel's 1,629 `Insert` entries; the function below detects that and says so (route 3e then applies).

```lua
local function num(s)                    -- a display to a number in base units: Hz, ms, dB, %
  if not s or s == "" then return nil end
  s = s:gsub("\226\128\175", " "):gsub("\194\160", " ")      -- U+202F and U+00A0 to spaces
  local a, b = s:match("(%d+)k(%d+)")                         -- "2k5" = 2500
  if a then return (tonumber(a) + tonumber("0." .. b)) * 1000 end
  local n = tonumber(s:match("[-+]?%d*%.?%d+"))
  if not n then return nil end
  if s:find("%d%s*kHz") or s:find("%dk%f[%A]") then return n * 1000 end
  if s:find("%d%s*ms") then return n end
  if s:find("%d%s*s%f[%A]") then return n * 1000 end           -- seconds to ms
  return n
end

local function find_norm(tr, fx, p, target)   -- read-only; nil and a reason when it cannot search
  local function f(v)
    local _, s = reaper.TrackFX_FormatParamValueNormalized(tr, fx, p, v, "")
    return num(s)
  end
  local a, b = f(0), f(1)
  if not a or not b then return nil, "no numeric display" end
  if a == b then return nil, "the plugin formats only its current value: use a scratch instance" end
  local inc = b > a
  local function edge(strict)                   -- first v whose display reaches (or passes) the target
    local lo, hi = 0.0, 1.0
    for _ = 1, 30 do
      local mid = (lo + hi) / 2
      local m = f(mid)
      local before = strict and (inc and m <= target or not inc and m >= target)
                            or not strict and (inc and m < target or not inc and m > target)
      if before then lo = mid else hi = mid end
    end
    return hi
  end
  local first, past = edge(false), edge(true)   -- the run of values that display the target
  local v = (past > first) and (first + past) / 2 or first
  local _, shown = reaper.TrackFX_FormatParamValueNormalized(tr, fx, p, v, "")
  return v, shown
end
```

Give the target in the base unit the parser returns: hertz, milliseconds, decibels, percent. The result is the middle of the run of values that display the target, so a coarse display such as "30.0 %" does not leave the value at the run's edge (29.95 %).

What the parser has to survive, all measured: units present on one display path and absent on the other ("450" against "450 Hz"), doubled units ("5.0 dB dB", DeNoise 2), kilo notation ("1k", "2k5", soothe2), a change of unit inside the range ("500 ms" to "30.0 s", kHs Reverb; ms to s on Pro-C 3's Release), and a narrow no-break space U+202F between number and unit (kHs Reverb), which Lua's `%s` does not match.

Verified on Ozone 12 (12.40 dB = 0.620000), soothe2 (450 Hz, 2k5), kHs Reverb (2.20 s), Nectar 4 (250 ms) and DeNoise 2 (5.0 dB = 0.666667). Then write the value once with `set_fx_parameter`, which confirms it.

## Searching on a scratch instance

For a plugin that formats only its current value (route 3e), run the same search by writing, on a fresh instance of the same plugin in a scratch tab, never on the user's instance: open a tab with action 40859, add the plugin with `TrackFX_AddByName`, find the parameter by name, bisect by writing it and reading `TrackFX_GetFormattedParamValue` with the parser above, then empty the tab, make it clean (save a copy, reopen it with `Main_openProject("noprompt:" .. path)`) and close it with action 40860, and select the user's tab again. The same plugin version maps values the same way, so the result carries over; write it once to the user's instance with `set_fx_parameter`.

Measured on ValhallaFutureVerb, Mix = 30 %: 33 writes on the scratch instance, 1.3 s for the whole round including loading the plugin, and the user's project untouched (same dirty flag, same undo history). It needs a plugin that takes writes at once; a plugin that takes them in its audio callback needs the audio engine running for the duration.

## Binary search by formatted value

The write-based search for route 3e, for a plugin that takes writes at once. **Run it on a scratch instance** ([above](#searching-on-a-scratch-instance)): on the user's instance it sweeps the parameter through both extremes, which the protocol forbids. Prefer [Searching without writing](#searching-without-writing) wherever the plugin formats any value.

```lua
local function setval(tr, fx, p, target)  -- num() as in "Searching without writing"
  local function val(n)                   -- Normalized, not SetParam: see the section above
    reaper.TrackFX_SetParamNormalized(tr, fx, p, n)
    local _, s = reaper.TrackFX_GetFormattedParamValue(tr, fx, p, "")
    return num(s), s
  end
  local inc = val(1) > val(0)             -- Handle ranges that invert direction
  local lo, hi = 0.0, 1.0
  for _ = 1, 26 do
    local mid = (lo + hi) / 2
    if (val(mid) < target) == inc then lo = mid else hi = mid end
  end
  local v = (lo + hi) / 2
  local _, s = val(v)
  return v, s                             -- the value, and the display to verify the parameter
end
```

The search sets the parameter to both extremes during execution. Do not use this method on a parameter that triggers state changes.

`TrackFX_SetParamFromString` is absent from both this REAPER build's Lua API and reapy 0.10.0, so the plugin's own mapping has to be inverted, by this search or the read-only one above. Around 24 iterations resolves a two-decimal readout; stop early once the displayed value is within tolerance.

## List parameters: trust the number, not the label

A parameter that picks from a list (a profile, a mode, a style) stores the choice as `index / steps`. Some plugins turn the stored number back into a label by truncating instead of rounding, so a value stored a hair under an integer, for example 15.9999995 instead of 16, is labelled with the entry **below** the real one. The plugin's own interface shows the right entry; only the label the host receives is wrong.

Measured on sonible smart:chain's Profile (parameter 116, `steps = 47`; 26 named profiles, the remaining indices read "n/a"): 8 of 25 instances in one project were labelled with the neighbouring profile. Lead vocal "Vocals | High" read as "Synth | Pad", snares "Drums | Snare" as "Drums | Kick", electric guitars as "Guitar | Acoustic", bass as "Universal". A session nearly "fixed" a correct setup on the strength of those labels.

Since 1.3.1, `get_fx_parameters` does this for you. A stepped parameter that is not a toggle carries `steps` and `step_index`, and `step_label` when the plugin's label for that index differs from `formatted_value`; the reply then also carries a `note`. Report `step_label`, say that the host label is off by one, and ask the user to confirm against the plugin window before anyone changes it.

Through the bridge, read a list parameter by its number:

```lua
local ok, step, _, _, toggle = reaper.TrackFX_GetParameterStepSizes(tr, fx, p)  -- use only when ok and not toggle
local _, lo, hi = reaper.TrackFX_GetParam(tr, fx, p)
local steps = math.floor((hi - lo) / step + 0.5)        -- step is in native units: 1/47 on a VST3, 1 on a JS enum 0-4
local k = math.floor(reaper.TrackFX_GetParamNormalized(tr, fx, p) * steps + 0.5)  -- the real index
local nudge = math.min(1e-5, 0.5 / (steps * (steps + 1)))
local _, name = reaper.TrackFX_FormatParamValueNormalized(tr, fx, p, math.min(1, k / steps + nudge), "")
```

Plugins turn a normalized value into an entry three ways, measured across 17 plugins (registry column "Lists"): rounding (most), truncating (smart:chain) and the VST3 SDK's floor(v × (steps + 1)) (Arturia). Just past `k / steps` all three name entry `k`, so read labels and write entries there (`fx_tools.step_probe`). A quarter step past it, the SDK mapping already names `k + 1` in the top quarter of the list: an earlier version of this note used `(k + 0.25) / steps` and mislabelled one of Arturia's 17 lists. Compare labels as numbers when they are numbers: a JS slider read just past its step shows "-11.999917" for "-12.0". Counting distinct labels does not give `steps` when several indices share a label such as "n/a".

## Stock Cockos plugins

These ship with REAPER and are what the MCP tools add by default, so their layouts are worth having to hand. Dumped from REAPER 7.79 at factory defaults.

**ReaLimit** (6 params). Threshold is linear from -60 to +12 dB, so `norm = (dB + 60) / 72`. Release runs **backwards**: normalised 0 reads `inf`, and 1.0 reads 6 ms, with 15 ms at the default 0.355. A release request outside 6 ms to `inf` clamps.

`0` Threshold · `1` Ceiling · `2` Release · `3` Bypass · `4` Wet · `5` Delta

**ReaComp** (24 params):

`0` Threshold · `1` Ratio · `2` Attack · `3` Release · `4` Pre-comp · `6` Lowpass · `7` Hipass ·
`8` SignIn · `9` AudIn · `10` Dry · `11` Wet · `13` RMS size · `14` Knee · `15` Auto Make Up Gain ·
`16` Auto Release · `21` Bypass · `23` Delta

**ReaGate** (24 params):

`0` Threshold · `1` Attack · `2` Release · `3` Pre-open · `4` Hold · `5` Lowpass · `6` Hipass ·
`9` Dry · `10` Wet · `11` Noise level · `12` Hysteresis · `14` RMS size · `21` Bypass · `23` Delta

**ReaEQ** (19 params at default). Bands are a flat array with a stride of 3 from index 0: Freq, Gain, BW. Band 1 is the Low Shelf, band 4 the High Shelf, band 5 the High Pass. `15` Global Gain, `16` Bypass, `17` Wet, `18` Delta.

**The ReaEQ parameter count is not fixed.** It reports 19 parameters at default and 16 after loading the `stock - Mud Free` preset, because the band count changes with the preset. Re-read `TrackFX_GetNumParams` and the names after any preset load rather than caching indices across one.

## FabFilter Pro-Q 4

Contains 740 parameters. Bands are a flat array with a stride of 23. Band *n* starts at `(n-1) * 23`:

| Offset | Parameter |
|---|---|
| +0 | Used (set 0 to remove the band) |
| +1 | Enabled |
| +2 | Frequency |
| +3 | Gain |
| +4 | Q |
| +5 | Shape |
| +6 | Slope |

The mappings are exact and do not require searching:

```lua
local function fnorm(f) return math.log(f/10, 10) / 3.4771213 end   -- Maps 10 Hz .. 30 kHz
local function gnorm(g) return (g + 30) / 60 end                    -- Maps +-30 dB
local function qnorm(q) return math.log(q/0.025, 10) / 3.20412 end  -- Maps 0.025 .. 40
-- Slope mapping: norm = dB_per_oct / 60 (0.2 = 12 dB/oct)
```

Shape is `index/9`:

`0` Bell · `1` Low Shelf · `2` Low Cut · `3` High Shelf · `4` High Cut · `5` Notch ·
`6` Band Pass · `7` Tilt Shelf · `8` Flat Tilt · `9` All Pass

All 24 bands report `Used` and `Enabled` regardless of state. Unused bands reset to parked defaults: 1000.0 Hz at 0.00 dB. When rewriting a curve, write `0` to offset +0 for every unused band to prevent previous curves from remaining active.

**Write `Used` and `Enabled` before a band's other parameters.** Writing Frequency, Gain or Q to a band that is not yet in use has no audible effect, and on Pro-Q 3 and Pro-C 2 it crashed REAPER (`STATUS_ACCESS_VIOLATION`; two minidumps at the same offset inside Pro-Q 3). This was found by the xDarkzX Reaper-MCP project (CHANGELOG 0.6.3), not here, and is untested on Pro-Q 4 and Pro-C 3; treat them the same. In practice, write a FabFilter plugin's parameters in ascending index order, which puts +0 and +1 first for every band. `set_fx_parameter` writes one parameter per call, so call it in that order too. A write that changes nothing is an ordering problem, not a sign that the plugin window must be open: headless writes work.

Useful globals: `556` Output Level, `559` Bypass, `738` Wet.

## FabFilter Pro-C 3

Indices: `0` Style, `1` Threshold, `2` Auto Threshold, `4` Ratio, `5` Knee, `6` Range,
`7` Attack, `8` Release, `10` Lookahead.

Style is `index/12`:

`0` Clean · `1` Versatile · `2` Smooth · `3` Punch · `4` Upward · `5` TTM · `6` Vari-Mu ·
`7` Classic · `8` Opto · `9` Vocal · `10` Mastering · `11` Bus · `12` Pumping

Threshold spans −60 to 0 dB. A threshold near 0 with makeup gain applied functions as a gain stage.

## iZotope Ozone 12

876 parameters. **It takes host writes only in its audio callback**, so nothing lands while REAPER's audio engine is closed ([Plugins that take writes in their audio callback](#plugins-that-take-writes-in-their-audio-callback)); `set_fx_parameter` 1.3.1 opens the engine for the write when it has to. Each module exposes `<MODULE>: Bypass`, and module enable flags read correctly while the plugin itself is bypassed in REAPER, so the chain a user built can be inspected before turning it on. Filter by name, `get_fx_parameters` with `name_contains` (`MAX:`, `VCOMP:`, `DYNEQ:`, `IMG:`, `EQ:`): 876 lines are too many to read.

Maximizer: `119` Bypass · `120` Input Gain (linear 0 to 20 dB, `norm = dB / 20`) · `121` Output Level (ceiling; −1.00 dB reads 0.95) · `122` Link Input Gain and Output Level · `123` Character. The True Peak switch is not exposed; measure true peak after a render instead of assuming it.

Vintage Compressor: `539` Bypass · `540` Threshold · `541` Ratio · `546` Auto Gain.

## Known index traps

**bx_townhouse Buss Compressor**: `0` Bank, `1` Comp In, `2` Key In, `3` AutoFade,
**`4` Thresh**, `5` Ratio, `6` Attack, `7` Release, `8` MakeUp, `10` Mix. Release reads `auto` at norm ≥ 0.90.

**SPL Transient Designer Plus**: `0` Attack, `1` Sustain, `2` Output, `3` Mix.

**bx_subsynth**: `7` 24-36 Hz, `8` 36-56 Hz, `9` 56-80 Hz, `10` Subharmonics, `11` Low End,
`14` Squeeze, `16` Drive. Generates a fundamental frequency.

**Scheps Omni Channel 2 Stereo**: `2` Input Gain, `5` Output Gain (both −144 to +12 dB, 0 dB = 0.764, not linear: use `find_norm`), `10` Limit:Threshold (−30 to 0 dB, linear), `11` Limit:On, `49` Gate:Threshold, `84` Comp:Threshold (−50 to 0 dB, linear), `114`/`124` DeEsser thresholds (−48 to 0 dB, linear). **Every per-channel control has a separate `... R` parameter** (`4` Input Gain R, `6` Output Gain R, `50`, `85`, `115`, `125`, ...), and the plugin's own Link and ST switches do not tie them together for host writes: writing `5` alone moved only the left channel, so a −6.6 dB write measured −2.2 dB on a stereo stem ((10^−0.66 + 1)/2) and pulled the image right. Write both, then compare every `X` with `X R` in one bridge read. **The limiter sits before Output Gain**: a −6.8 → −14.3 dB threshold change with Output Gain −7.5 left the output peak at −21.8 = −14.3 − 7.5. Move the limit threshold with the level inside the plugin, not with the output.

**SSLGChannel Stereo**: `33` InputLevel (−18 to +18 dB, linear), `30` Gain, the output fader (−24 to +12 dB, 0 dB = 0.6667, not linear), `2` CompThresh runs **backwards**, +10 dB at 0 to −20 dB at 1, `norm = (10 − dB) / 30`; `8` ExpThresh (−30 to +10 dB, not linear); `9` ExpRange (0 = expander does nothing); `13` DynamicBypass; `4` CompFast. Its compressor's make-up follows the threshold: lowering input and threshold together by 10.1 dB, which keeps the gain reduction, raised the output about 3 dB above the linear prediction. Measure the output after moving a threshold instead of computing it.

**soothe2**: `3` mode, `4` depth, `5` sharpness, `9`-`13` low cut group. Four bands of six parameters from `14` (on / freq / sens / q / balance / mode), `38`-`42` high cut group, `50` mix, `51` trim, `53` bypass, `54` sidechain, `55` input trim. The per-band `sens` biases detection to specific resonances. An earlier version of this note put sidechain at `51`; the current VST3 (read on REAPER 7.82) has it at `54`, so confirm the name before writing.

## Latency

Bypassing a plugin does not release its PDC. Setting it offline reduces PDC to zero.

```lua
local ok, lat = reaper.TrackFX_GetNamedConfigParm(tr, fx, "pdc")
reaper.TrackFX_SetOffline(tr, fx, true)
```

Sum `pdc` across a chain and report it in ms at the project rate.

## Sidechain routing

A send feeding a plugin's sidechain requires three conditions:

1. Destination track channel count ≥ 4 (`I_NCHAN`)
2. The send's `I_DSTCHAN` set to 2 (channels 3/4)
3. The plugin's input pins 2 and 3 mapped to those channels

```lua
reaper.GetTrackSendInfo_Value(tr, 0, s, "I_DSTCHAN")   -- 2 corresponds to channels 3/4
reaper.TrackFX_GetPinMappings(tr, fx, 0, 2)            -- 0x4 maps to pin 2
reaper.TrackFX_GetPinMappings(tr, fx, 0, 3)            -- 0x8 maps to pin 3
```

`set_send_routing` with `dest_channels` "3/4" sets the first two and raises `I_NCHAN` when it is too low; `get_fx_pins` reads the third, listing each pin's name and channels.

Check all three variables. Plugins may report sidechain as enabled while receiving no input.
