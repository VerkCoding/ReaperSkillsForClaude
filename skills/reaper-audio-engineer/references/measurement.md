# Offline measurement toolkit

Everything here runs without playback and without the audio device. It is what lets you make
mixing decisions from data. All of it is fast: reading 22050 samples through a take accessor
costs about 5 ms, so a full band analysis of a stem is a fraction of a second.

## Contents

- [Loudness of a file](#loudness-of-a-file)
- [Reading samples](#reading-samples)
- [Band analysis](#band-analysis)
- [Finding a resonance](#finding-a-resonance)
- [Mapping the arrangement](#mapping-the-arrangement)
- [Writing a measured automation ride](#writing-a-measured-automation-ride)
- [Auditing the project](#auditing-the-project)

## Loudness of a file

`CalculateNormalization` returns the gain that would hit the target, so the measurement is
`-20*log10(gain)` when you pass a target of 1.0.

```lua
local src = reaper.PCM_Source_CreateFromFile(path)
local len = reaper.GetMediaSourceLength(src)
local function m(md) return -20 * math.log(reaper.CalculateNormalization(src, md, 1.0, 0, len), 10) end
-- md: 0 = LUFS-I, 1 = RMS, 2 = peak, 3 = true peak, 5 = LUFS-S max
reaper.PCM_Source_Destroy(src)
```

Pass a real length. With `0, 0` it returns exactly 1.0, which decodes to a clean-looking
`-0.00 dB` and is indistinguishable from a real measurement until you notice every source
reads the same.

To measure a **stem in the project**, build the source from the take's filename rather than
using the take's own source object, which may be offline:

```lua
local fn = reaper.GetMediaSourceFileName(reaper.GetMediaItemTake_Source(take), "")
local src = reaper.PCM_Source_CreateFromFile(fn)
```

`GetMediaSourceFileName` returns the filename directly as its only return value — a
`select(2, ...)` around it yields nil.

**Crest factor (true peak − LUFS-I) is diagnostic.** A real kick drum sits around 18–22 dB. A
value near 35 dB with a very low integrated level means a sparse impulse train — a trigger
click, not a drum. That is a source problem no EQ will solve, and worth saying out loud.

## Reading samples

Take audio accessors work offline and read the file even when the project's copy is offline.

```lua
local acc = reaper.CreateTakeAudioAccessor(take)
local en  = reaper.GetAudioAccessorEndTime(acc)
local SR, N = 22050, 22050              -- request any rate; REAPER resamples
local buf = reaper.new_array(N)
reaper.GetAudioAccessorSamples(acc, SR, 1, startTime, N, buf)
-- buf[1..N] are floats
reaper.DestroyAudioAccessor(acc)
```

Request a **lower samplerate** when you only need low-frequency content or an RMS envelope —
4000 Hz for an arrangement map cuts the work by 11×.

**Track audio accessors are not a way to measure a bus.** They only cover items on that
track, so on an FX-only bus `GetAudioAccessorStartTime` and `EndTime` are both 0 and every
read returns nothing. To measure a bus, render it.

## Band analysis

An RBJ bandpass bank over the sample buffer gives you tonal balance. Normalise each band
against the signal's own broadband energy so you read *balance*, not level.

```lua
local function mkbp(f0, Q, SR)
  local w0 = 2*math.pi*f0/SR
  local al = math.sin(w0)/(2*Q)
  local a0 = 1+al
  return {b0=al/a0, b2=-al/a0, a1=(-2*math.cos(w0))/a0, a2=(1-al)/a0}
end
-- per band, one pass over buf:
local x1,x2,y1,y2,acc2 = 0,0,0,0,0
for i = 1, N do
  local x = buf[i]
  local y = f.b0*x + f.b2*x2 - f.a1*y1 - f.a2*y2
  x2=x1; x1=x; y2=y1; y1=y
  acc2 = acc2 + y*y
end
```

For an octave-ish overview use `Q = f0/(f2-f1)` with band edges; a serviceable set is
sub 30–60, low 60–120, lomid 120–300, mid 300–800, upmid 800–2500, pres 2500–6000,
air 6000–10000.

**Gate before you average.** Skip any window whose RMS is below a floor, or a long silence
between phrases will drag the result. A window is worth using if its mean square exceeds
about `1e-9`; for sparse material, take the loudest windows rather than evenly spaced ones.

**Sanity check on interpretation.** These numbers are relative to the signal's own mean, so a
band moving does not always mean you changed that band — cutting several other bands raises
everything else by comparison. When you want to know what a specific move did, change one
thing and re-measure.

## Finding a resonance

Broad bands tell you the tilt; 1/3-octave tells you where the problem actually is. Use
`Q = 4.318` at ISO centres and normalise to the mean of the scanned range so peaks stand out:

```
200 250 315 400 500 630 800 1000 1250 1600 2000 2500 3150 4000
```

Read the *shape*, not single numbers. A vocal that reads +5 at 1 kHz with −4 to −6 through
2–4 kHz is the classic nasal signature: too much of the honk band relative to the
intelligibility band. The ratio between those regions matters far more than either alone, so
fix it from both ends — cut the peak and widen the presence boost to fill the hole.

Not every peak should be flattened. Voices have formants; a residual bump that survives a
proper cut and does not move when you increase it is the instrument's own character. Prove it
is not something else — bypass the mastering chain, mute the reverb returns, check the channel
strip EQ is really at zero — and then leave it alone.

## Mapping the arrangement

Per-bar RMS at low samplerate, rendered as characters, gives you song structure in one
command. You need this before writing automation or judging a balance.

```lua
local bar = 4 * 60 / reaper.Master_GetTempo()
local SR = 4000
local NB = math.floor(bar * SR)
-- for each bar: read NB samples, compute RMS, map dB-below-max to a glyph
local glyph = {" ", ".", ":", "-", "=", "+", "*", "#", "@"}
```

Print one row per source. Entries, drop-outs and section boundaries become obvious, and you
can convert bar numbers to seconds with the same `bar` value.

## Writing a measured automation ride

A corrective vocal ride is one of the highest-value things you can do without ears, because
it is pure measurement: even out what is uneven.

Method: block the take into ~0.5 s windows, gate to the parts that are actually sung, take the
**median** of the active blocks as the target (means get dragged by outliers), invert, clamp,
smooth over about a phrase, and write points at ~1 s spacing.

```lua
local gate = maxBlockLevel - 18          -- singing only; a looser gate boosts breaths
local target = sortedActive[#sortedActive // 2]
local d = target - level                 -- then clamp to about -5 .. +4 dB
```

Two things that will bite:

**Envelope scaling.** Volume envelopes store a scaled value, not linear amplitude:

```lua
local env = reaper.GetTrackEnvelopeByName(tr, "Volume")
if not env then
  reaper.SetOnlyTrackSelected(tr)
  reaper.Main_OnCommand(40406, 0)        -- Track: toggle volume envelope visible
  env = reaper.GetTrackEnvelopeByName(tr, "Volume")
end
local mode = reaper.GetEnvelopeScalingMode(env)
reaper.InsertEnvelopePoint(env, t, reaper.ScaleToEnvelopeMode(mode, 10^(dB/20)), 0, 0, false, true)
-- ... then reaper.Envelope_SortPoints(env)
```

Reading back gives the scaled value; convert with `ScaleFromEnvelopeMode` before interpreting
it as dB, or you will see numbers like 716 and think something is broken.

**Where the ride sits in the chain.** Put it on the source track, upstream of the strip's
compressor, so the compressor receives an already-even signal and does less work. If the
send to the next tier is post-fader, the envelope applies to it.

Report the gate, target and resulting range. If the ride is pinned at its clamp for long
stretches, the gate is too loose and you are lifting breaths — tighten it and rerun.

## Auditing the project

Before handing back, walk the project and report **warnings, not values**. Zero warnings is a
much stronger statement than pages of numbers, and it catches your own slips.

Worth asserting:

- Track count matches the start (a temporary analysis track left behind is easy to miss)
- No track soloed or muted, no `I_FXEN == 0`, master FX chain enabled
- The expected plugin really is in the slot you edited, on every strip you touched
- No compressor with a threshold too high to ever engage — a "compressor" doing nothing while
  its makeup gain is up is just a gain stage, and it is a very common way for a chain to look
  right and do nothing
- Envelope point count and range are sane
- No send muted; send levels are what you set
- Render settings, render path and time selection restored to the user's originals
- Item fades present where you added them

```lua
local warn = {}
-- ... accumulate strings ...
return body .. "\n\n=== WARNINGS (" .. #warn .. ") ===\n" ..
  (#warn > 0 and table.concat(warn, "\n") or "none")
```
