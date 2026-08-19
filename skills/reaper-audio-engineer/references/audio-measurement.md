# Offline measurement toolkit

These methods execute without playback and bypass the audio device, providing data for mixing decisions. Processing speed for reading 22050 samples via a take accessor averages 5 ms. Stem analysis completes in sub-second intervals.

## Contents

- [Choosing a measurement route](#choosing-a-measurement-route)
- [Traps that produce confident wrong numbers](#traps-that-produce-confident-wrong-numbers)
- [Loudness of a file](#loudness-of-a-file)
- [Reading samples](#reading-samples)
- [Band analysis](#band-analysis)
- [Finding a resonance](#finding-a-resonance)
- [Mapping the arrangement](#mapping-the-arrangement)
- [Writing a measured automation ride](#writing-a-measured-automation-ride)
- [Auditing the project](#auditing-the-project)

## Choosing a measurement route

| Need | Route |
|---|---|
| Loudness, true peak, clipping, dynamics, spectrum, stereo field of the whole project | `analyze_loudness`, `detect_clipping`, `analyze_dynamics`, `analyze_frequency_spectrum`, `analyze_stereo_field`. Each renders the project and measures the file. |
| The same for one file already on disk | `CalculateNormalization` through the bridge, below. No render required. |
| A bus, a section, or a chain in isolation | Render it, then measure. See [Rendering from the bridge](../../reaper-mcp/references/rendering.md). |
| Per-bar structure, band balance, resonance hunting, automation rides | The bridge. No tool covers these. |

The analysis tools return calibrated figures: band levels are RMS in dBFS that sum to the overall signal RMS, true peak is oversampled, and dynamics are measured across channels. They refuse an empty project rather than reporting silence. What they cannot do is measure part of a project, so anything narrower than "the whole mix" means rendering or reading samples yourself.

## Traps that produce confident wrong numbers

Each of these was found producing a plausible, wrong reading. They apply to any measurement code, including your own.

**Do not sum to mono before measuring level.** Averaging the channels cancels out-of-phase content: a -6 dBFS anti-phase mix measures as -120 dB silence. Take the peak as the maximum absolute sample across channels, and the RMS across all channels' samples.

**A sample peak is not a true peak.** The waveform rises between samples. Measure with an oversampled method (REAPER's mode 3, or four times upsampling) and report two decimals.

**A measurement window longer than the material measures nothing.** A dynamic-range score over 3-second windows finds zero windows in a 2-second render. Returning `0.0` then reads as "no dynamic range" rather than "not measured". Report the window actually used, and fall back to the whole signal when it is shorter.

**Raw FFT magnitudes are not levels.** Averaging `abs(stft)` across a band puts a -6 dBFS tone at +31 dB and makes the answer depend on how many bins the band spans. Scale to component amplitude, sum power across the band, and divide by the window's equivalent noise bandwidth (1.5 for Hann). The check that catches this: all bands summed must reproduce the signal's overall RMS.

**Guard the degenerate cases.** Correlation of a silent channel is `NaN`, which is not valid JSON; a stereo width ratio divided by a small guard constant returned 3,543,678,557 for fully out-of-phase audio. Report "silent" or "out of phase" instead of a number that looks measured.

## Loudness of a file

`CalculateNormalization` outputs the required gain to reach a specified target. The measurement is then `-20*log10(gain)`.

**The target argument is in dB, not linear.** Passing `1.0` asks for a +1 dB target and makes every reading exactly 1 dB low. Pass `0.0`.

```lua
local src = reaper.PCM_Source_CreateFromFile(path)
local len = reaper.GetMediaSourceLength(src)
local function m(md) return -20 * math.log(reaper.CalculateNormalization(src, md, 0.0, 0, len), 10) end
-- Support for various measurement formats: 0 = LUFS-I, 1 = RMS, 2 = peak, 3 = true peak, 5 = LUFS-S max
reaper.PCM_Source_Destroy(src)
```

Verified against files of known content: a 1 kHz sine written at -6.00 dBFS reads `peak = -6.00` and `truepeak = -5.99` with a target of `0.0`, and `-7.00` / `-6.99` with a target of `1.0`.

Mode 3 is a genuine oversampled true peak, not the sample peak relabelled. On a hard-clipped file whose sample peak reads `-0.00`, mode 3 returns `+0.04`, matching an independent four times oversampled measurement. That inter-sample overshoot is the point of the reading, so keep two decimals: at one decimal it rounds to `0.0` and a ceiling check passes when it should not.

REAPER's LUFS-I agrees with other implementations to within a few tenths of a dB, not exactly. The same sine measured `-6.37` here against `-6.04` under pyloudnorm. Do not treat a 0.3 dB difference between tools as a fault.

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
