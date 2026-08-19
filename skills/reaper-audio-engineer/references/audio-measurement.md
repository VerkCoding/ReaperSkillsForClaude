# Offline measurement toolkit

These methods execute without playback and bypass the audio device, providing data for mixing decisions. Processing speed for reading 22050 samples via a take accessor averages 5 ms. Stem analysis completes in sub-second intervals.

## Contents

- [Loudness of a file](#loudness-of-a-file)
- [Reading samples](#reading-samples)
- [Band analysis](#band-analysis)
- [Finding a resonance](#finding-a-resonance)
- [Mapping the arrangement](#mapping-the-arrangement)
- [Writing a measured automation ride](#writing-a-measured-automation-ride)
- [Auditing the project](#auditing-the-project)

## Loudness of a file

`CalculateNormalization` outputs the required gain to reach a specified target. When target is 1.0, the measurement is computed as `-20*log10(gain)`.

```lua
local src = reaper.PCM_Source_CreateFromFile(path)
local len = reaper.GetMediaSourceLength(src)
local function m(md) return -20 * math.log(reaper.CalculateNormalization(src, md, 1.0, 0, len), 10) end
-- Support for various measurement formats: 0 = LUFS-I, 1 = RMS, 2 = peak, 3 = true peak, 5 = LUFS-S max
reaper.PCM_Source_Destroy(src)
```

Provide an accurate length parameter. Supplying `0, 0` yields exactly 1.0, resulting in an output of `-0.00 dB` for all sources.

To measure a project stem, instantiate the source using the take's filename. Avoid using the take's source object to prevent offline state errors.

```lua
local fn = reaper.GetMediaSourceFileName(reaper.GetMediaItemTake_Source(take), "")
local src = reaper.PCM_Source_CreateFromFile(fn)
```

`GetMediaSourceFileName` outputs the filename directly. Using `select(2, ...)` yields nil.

Crest factor (true peak − LUFS-I) identifies signal types. Typical acoustic kick drums measure 18-22 dB. Readings near 35 dB paired with low integrated levels indicate sparse impulse trains or trigger clicks. This indicates source characteristics that equalization does not alter.

## Reading samples

Take audio accessors function offline and read the source file regardless of the project's offline status.

```lua
local acc = reaper.CreateTakeAudioAccessor(take)
local en  = reaper.GetAudioAccessorEndTime(acc)
local SR, N = 22050, 22050              -- Request rate triggers REAPER internal resampling
local buf = reaper.new_array(N)
reaper.GetAudioAccessorSamples(acc, SR, 1, startTime, N, buf)
-- buf contains floats
reaper.DestroyAudioAccessor(acc)
```

Request a lower sample rate for low-frequency content analysis or RMS envelope generation. Using 4000 Hz reduces processing time for arrangement mapping.

Track audio accessors read only track items, making them incompatible with bus measurement. On FX-only buses, `GetAudioAccessorStartTime` and `EndTime` equal 0. Bus measurement requires rendering.

## Band analysis

An RBJ bandpass filter bank provides tonal balance data. Normalizing each band against the signal's broadband energy yields relative balance rather than absolute level.

```lua
local function mkbp(f0, Q, SR)
  local w0 = 2*math.pi*f0/SR
  local al = math.sin(w0)/(2*Q)
  local a0 = 1+al
  return {b0=al/a0, b2=-al/a0, a1=(-2*math.cos(w0))/a0, a2=(1-al)/a0}
end
-- Calculate band energy block
local x1,x2,y1,y2,acc2 = 0,0,0,0,0
for i = 1, N do
  local x = buf[i]
  local y = f.b0*x + f.b2*x2 - f.a1*y1 - f.a2*y2
  x2=x1; x1=x; y2=y1; y1=y
  acc2 = acc2 + y*y
end
```

Calculate Q using `Q = f0/(f2-f1)` with band edges for octave-scale analysis. Standard ranges are: sub 30-60, low 60-120, low-mid 120-300, mid 300-800, upper-mid 800-2500, presence 2500-6000, air 6000-10000.

Gate signals prior to averaging. Exclude windows with RMS values below the noise floor to prevent silence from skewing the mean. Process windows with mean square values exceeding `1e-9`. For sparse material, analyze the loudest windows instead of evenly spaced intervals.

Data interpretation requires considering relative values. Because measurements are relative to the signal mean, reducing energy in multiple bands mathematically increases the relative value of unmodified bands. To isolate the effect of a specific adjustment, apply a single change and repeat the measurement.

## Finding a resonance

Broadband analysis indicates general spectral tilt. 1/3-octave analysis isolates specific frequency concentrations. Apply `Q = 4.318` at ISO center frequencies and normalize to the mean of the scanned range to isolate peaks:

```
200 250 315 400 500 630 800 1000 1250 1600 2000 2500 3150 4000
```

Analyze the overall frequency response shape rather than isolated values. A measurement of +5 at 1 kHz combined with −4 to −6 from 2-4 kHz indicates a dominant 1 kHz region relative to the 2-4 kHz region. Adjust the ratio by reducing the 1 kHz peak and increasing the 2-4 kHz region.

Avoid indiscriminate flattening of peaks. Voices possess formants; stable frequency concentrations characterize the instrument. Verify the source of the peak by bypassing the mastering chain, muting reverb returns, and confirming channel strip EQ is flat.

## Mapping the arrangement

Per-bar RMS calculation at a low sample rate, output as text characters, provides structural data for the session. Obtain this data prior to generating automation or evaluating balance.

```lua
local bar = 4 * 60 / reaper.Master_GetTempo()
local SR = 4000
local NB = math.floor(bar * SR)
-- Calculate RMS per bar block and map dB-below-max to a character
local glyph = {" ", ".", ":", "-", "=", "+", "*", "#", "@"}
```

Output one row per source. This displays entries, drop-outs, and section boundaries. Convert bar numbers to seconds using the computed `bar` constant.

## Writing a measured automation ride

Corrective level automation normalizes signal variance using mathematical measurement.

Method: Segment the take into ~0.5 s windows. Gate the signal to isolate active audio. Calculate the median of the active blocks to establish a target level, as medians resist outlier skewing. Invert the difference, clamp the values, apply smoothing over phrase lengths, and write automation points at approximately 1 s intervals.

```lua
local gate = maxBlockLevel - 18          -- Isolate active audio; loose gates include noise floor
local target = sortedActive[#sortedActive // 2]
local d = target - level                 -- Clamp output difference bounds
```

Technical constraints:

Volume envelopes store scaled values rather than linear amplitude:

```lua
local env = reaper.GetTrackEnvelopeByName(tr, "Volume")
if not env then
  reaper.SetOnlyTrackSelected(tr)
  reaper.Main_OnCommand(40406, 0)        -- Initialize volume envelope visibility
  env = reaper.GetTrackEnvelopeByName(tr, "Volume")
end
local mode = reaper.GetEnvelopeScalingMode(env)
reaper.InsertEnvelopePoint(env, t, reaper.ScaleToEnvelopeMode(mode, 10^(dB/20)), 0, 0, false, true)
-- Sort points required after bulk insert
```

Reading envelope points returns scaled values. Apply `ScaleFromEnvelopeMode` before interpreting the data as decibels to avoid incorrect scaling assumptions.

Automation placement: Apply the automation on the source track, preceding the channel compressor. This feeds a normalized signal into the dynamics processor. If the routing is post-fader, the volume envelope applies to the send.

Output the gate threshold, target level, and resulting range. If the automation remains clamped at maximum or minimum values for extended periods, the gate threshold is too low and includes non-target audio. Adjust the threshold and reprocess.

## Auditing the project

Prior to completion, scan the project and generate warnings rather than raw values. A zero-warning state confirms compliance.

Audit checklist:

- Track count matches the initial state to verify no temporary analysis tracks remain.
- All tracks unmuted and unsoloed, `I_FXEN != 0`, and master FX chain enabled.
- Modified plugins match expected IDs in their designated slots.
- Compressor thresholds engage the signal. Compressors applying makeup gain without gain reduction function solely as static gain stages.
- Envelope point counts and ranges remain within standard limits.
- Sends are unmuted; send levels match explicitly set values.
- Render settings, render paths, and time selections match the initial user state.
- Expected item fades exist on targeted items.

```lua
local warn = {}
-- Output collected warnings block
return body .. "\n\n=== WARNINGS (" .. #warn .. ") ===\n" ..
  (#warn > 0 and table.concat(warn, "\n") or "none")
```
