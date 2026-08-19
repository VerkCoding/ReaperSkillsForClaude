# Controlling plugin parameters

Every plugin parameter is a normalised 0-1 float, and every plugin maps that range differently. Two methods exist to find specific parameter values.

## Contents

- [Find the parameter first](#find-the-parameter-first)
- [Binary search by formatted value](#binary-search-by-formatted-value)
- [FabFilter Pro-Q 4](#fabfilter-pro-q-4)
- [FabFilter Pro-C 3](#fabfilter-pro-c-3)
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

Large plugins pad parameter lists with MIDI CC entries. Limit the parameter dump.

## Binary search by formatted value

A general solution is to drive the normalised value until the plugin's formatted readout matches the requested value. This applies to any plugin without knowing its mapping.

```lua
local function pv(s)                      -- Parse units for parameters that change scale (e.g. ms to s)
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
  local inc = val(1) > val(0)             -- Handle ranges that invert direction
  local lo, hi = 0.0, 1.0
  for _ = 1, 26 do
    local mid = (lo + hi) / 2
    if (val(mid) < target) == inc then lo = mid else hi = mid end
  end
  local _, s = val((lo + hi) / 2)
  return s                                -- Return formatted string to verify parameter index
end
```

Unit parsing is necessary. Pro-C 3's Release displays `ms` at the bottom of its range and `s` at the top; a naive number match sees "2.00" for 2 seconds as smaller than 110 and converges on 1000 ms. Any parameter whose display changes units requires parsing.

The search sets the parameter to both extremes during execution. Do not use this method on a parameter that triggers state changes.

Print the returned value of `setval` to verify the index matches the expected parameter type.

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

Useful globals: `556` Output Level, `559` Bypass, `738` Wet.

## FabFilter Pro-C 3

Indices: `0` Style, `1` Threshold, `2` Auto Threshold, `4` Ratio, `5` Knee, `6` Range,
`7` Attack, `8` Release, `10` Lookahead.

Style is `index/12`:

`0` Clean · `1` Versatile · `2` Smooth · `3` Punch · `4` Upward · `5` TTM · `6` Vari-Mu ·
`7` Classic · `8` Opto · `9` Vocal · `10` Mastering · `11` Bus · `12` Pumping

Threshold spans −60 to 0 dB. A threshold near 0 with makeup gain applied functions as a gain stage.

## Known index traps

**bx_townhouse Buss Compressor**: `0` Bank, `1` Comp In, `2` Key In, `3` AutoFade,
**`4` Thresh**, `5` Ratio, `6` Attack, `7` Release, `8` MakeUp, `10` Mix. Release reads `auto` at norm ≥ 0.90.

**SPL Transient Designer Plus**: `0` Attack, `1` Sustain, `2` Output, `3` Mix.

**bx_subsynth**: `7` 24-36 Hz, `8` 36-56 Hz, `9` 56-80 Hz, `10` Subharmonics, `11` Low End,
`14` Squeeze, `16` Drive. Generates a fundamental frequency.

**soothe2**: `4` depth, `5` sharpness, `9`-`13` low cut group. Four bands of six parameters from `14` (on / freq / sens / q / balance / mode), `38`-`42` high cut group, `51` sidechain. The per-band `sens` biases detection to specific resonances.

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

Check all three variables. Plugins may report sidechain as enabled while receiving no input.
