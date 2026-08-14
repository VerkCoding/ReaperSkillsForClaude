# Controlling plugin parameters

Every plugin parameter is a normalised 0–1 float, and every plugin maps that range
differently. There are two ways to hit a real value, and knowing which to use saves a lot of
round trips.

## Contents

- [Find the parameter first](#find-the-parameter-first)
- [Binary search by formatted value](#binary-search-by-formatted-value)
- [FabFilter Pro-Q 4](#fabfilter-pro-q-4)
- [FabFilter Pro-C 3](#fabfilter-pro-c-3)
- [Known index traps](#known-index-traps)
- [Latency](#latency)
- [Sidechain routing](#sidechain-routing)

## Find the parameter first

Never assume an index. Dump names alongside values and read the layout:

```lua
for p = 0, math.min(reaper.TrackFX_GetNumParams(tr, fx), 40) - 1 do
  local _, n = reaper.TrackFX_GetParamName(tr, fx, p, "")
  local _, v = reaper.TrackFX_GetFormattedParamValue(tr, fx, p, "")
  out[#out+1] = ("%d %s = %s"):format(p, n, v)
end
```

Never locate an FX by plugin name either. Renamed FX are normal in an organised template, and
name-based lookup silently finds the wrong slot — or, with `TrackFX_AddByName`, adds a
duplicate. Enumerate the chain and match on position and expected role.

Big plugins pad their parameter list with a hundred MIDI CC entries. Cap your dump.

## Binary search by formatted value

The general solution: drive the normalised value until the plugin's own formatted readout
matches what you asked for. It works on any plugin without knowing its mapping.

```lua
local function pv(s)                      -- parse the formatted value INCLUDING units
  local n = tonumber(s:match("[-+]?%d*%.?%d+")) or 0
  if s:find("ms") then return n
  elseif s:find("%d%s*s") then return n * 1000
  elseif s:find("kHz") or s:find("k") then return n * 1000 end
  return n
end

local function setval(tr, fx, p, target)
  local function val(n)
    reaper.TrackFX_SetParam(tr, fx, p, n)
    local _, s = reaper.TrackFX_GetFormattedParamValue(tr, fx, p, "")
    return pv(s), s
  end
  local inc = val(1) > val(0)             -- some ranges run backwards
  local lo, hi = 0.0, 1.0
  for _ = 1, 26 do
    local mid = (lo + hi) / 2
    if (val(mid) < target) == inc then lo = mid else hi = mid end
  end
  local _, s = val((lo + hi) / 2)
  return s                                -- return it and print it: free verification
end
```

**Unit parsing is the part people skip and regret.** Pro-C 3's Release displays `ms` at the
bottom of its range and `s` at the top; a naive number match sees "2.00" for 2 seconds as
smaller than 110 and converges on 1000 ms. Any parameter whose display changes units needs
this.

The search sets the parameter to both extremes on its way, which is harmless for continuous
values but means you should not point it at a parameter that triggers something.

Always print what `setval` returned. A value that came back as `2:1` when you asked for a
threshold in dB tells you instantly that you had the wrong index.

## FabFilter Pro-Q 4

740 parameters, but the structure is clean. Bands are a flat array with a **stride of 23**, so
band *n* starts at `(n-1) * 23`:

| Offset | Parameter |
|---|---|
| +0 | Used — set 0 to remove the band |
| +1 | Enabled |
| +2 | Frequency |
| +3 | Gain |
| +4 | Q |
| +5 | Shape |
| +6 | Slope |

The mappings are exact — verified by round trip — so no search is needed:

```lua
local function fnorm(f) return math.log(f/10, 10) / 3.4771213 end   -- 10 Hz .. 30 kHz, log
local function gnorm(g) return (g + 30) / 60 end                    -- +-30 dB, linear
local function qnorm(q) return math.log(q/0.025, 10) / 3.20412 end  -- 0.025 .. 40, log
-- slope: norm = dB_per_oct / 60   (0.2 = 12 dB/oct, 0.4 = 24 dB/oct)
```

Shape is `index/9`:

`0` Bell · `1` Low Shelf · `2` Low Cut · `3` High Shelf · `4` High Cut · `5` Notch ·
`6` Band Pass · `7` Tilt Shelf · `8` Flat Tilt · `9` All Pass

**Reading a chain back is misleading.** All 24 bands report `Used` and `Enabled` whether or
not they are in use. An unused band is identifiable only by its parked defaults — 1000.0 Hz
at 0.00 dB. When you rewrite a curve, explicitly write `0` to offset +0 for every band past
the last one you set, or leftovers from the previous curve stay active.

Useful globals: `556` Output Level, `559` Bypass, `738` Wet.

## FabFilter Pro-C 3

Indices `0` Style, `1` Threshold, `2` Auto Threshold, `4` Ratio, `5` Knee, `6` Range,
`7` Attack, `8` Release, `10` Lookahead.

Style is `index/12`:

`0` Clean · `1` Versatile · `2` Smooth · `3` Punch · `4` Upward · `5` TTM · `6` Vari-Mu ·
`7` Classic · `8` Opto · `9` Vocal · `10` Mastering · `11` Bus · `12` Pumping

Threshold spans −60 to 0 dB. A threshold near 0 cannot compress, so if a chain has one sitting
at 0 with makeup gain applied, that plugin is a gain stage wearing a compressor's name — worth
flagging to the user rather than silently "fixing", since it may have been deliberate.

## Known index traps

Verified the hard way. Dump before you write, but these in particular are easy to get wrong:

**bx_townhouse Buss Compressor** — `0` Bank, `1` Comp In, `2` Key In, `3` AutoFade,
**`4` Thresh**, `5` Ratio, `6` Attack, `7` Release, `8` MakeUp, `10` Mix. Assuming Thresh is
at 5 means you drive Ratio instead and knock Release off `auto`. Release reads `auto` at
norm ≥ 0.90.

**SPL Transient Designer Plus** — `0` Attack, `1` Sustain, `2` Output, `3` Mix.

**bx_subsynth** — `7` 24–36 Hz, `8` 36–56 Hz, `9` 56–80 Hz, `10` Subharmonics, `11` Low End,
`14` Squeeze, `16` Drive. Genuinely generates a fundamental, so it is the tool of last resort
when a kick source has no low end of its own.

**soothe2** — `4` depth, `5` sharpness, `9`–`13` low cut group, then four bands of six
parameters from `14` (on / freq / sens / q / balance / mode), `38`–`42` high cut group,
`51` sidechain. The per-band `sens` biases where it works hardest; pointing it at a measured
resonance is far more musical than a static cut, because it only acts when the resonance is
actually ringing.

## Latency

**Bypassing a plugin does not release its PDC.** A bypassed metering or reference plugin can
still claim thousands of samples. Setting it **offline** takes it to zero.

```lua
local ok, lat = reaper.TrackFX_GetNamedConfigParm(tr, fx, "pdc")
reaper.TrackFX_SetOffline(tr, fx, true)
```

Sum `pdc` across a chain and report it in ms at the project rate. This is worth checking any
time the user mentions monitoring or tracking latency — and worth fixing before you spend
time on anything else, since it is usually one plugin.

## Sidechain routing

A send feeding a plugin's sidechain needs three things to line up, and it silently does
nothing if any one is missing:

1. Destination track channel count ≥ 4 (`I_NCHAN`)
2. The send's `I_DSTCHAN` set to 2 (channels 3/4)
3. The plugin's input pins 2 and 3 mapped to those channels

```lua
reaper.GetTrackSendInfo_Value(tr, 0, s, "I_DSTCHAN")   -- 2 means channels 3/4
reaper.TrackFX_GetPinMappings(tr, fx, 0, 2)            -- expect 0x4 for pin 2
reaper.TrackFX_GetPinMappings(tr, fx, 0, 3)            -- expect 0x8 for pin 3
```

Check all three before concluding a sidechain is working — pin mapping is the one people
forget, and the plugin will happily report its sidechain as enabled while listening to
silence.
