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
| Per-bar structure, band balance, resonance hunting, the levels behind an automation ride | The bridge. No tool covers these. The ride itself is written with `add_envelope_points`. |

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

A track audio accessor returns the items **before the track's FX, fader and pan**, but after everything at item level: item volume, take pan, take FX and playrate all showed in its output (a −6 dB item with take pan +0.5 read −24.08/−18.06; a +6 dB take FX read −6.02; playrate 2 turned 1 kHz into 2 kHz). On a MIDI track with a virtual instrument it returns nothing (`GetAudioAccessorSamples` = 0), since the sound only exists after the track FX. Measured on REAPER 7.82 (2026-10-10) with a −12.04 dBFS sine on four tracks: pan +0.5, fader −6 dB, stereo pan mode at −0.702 width 0.3, and a JS volume plugin at +6 dB. All four read −12.04 dBFS on both channels. Track accessors also read the audio while `offlineinact` has the media offline, as take accessors do: three sources reporting length 0 and one channel still returned their audio. An FFT spectrum read this way misses the track's EQ. Against post-FX stems of a 25-track mix, the 5-band spectral centroid of the raw files was off by a median of 0.11 octave, p90 0.59, and up to 1.4 octaves on an EQ'd ride and 2.7 on a shaker.

Cost on the main thread grows with the window: about 1 ms per track for 2,048 stereo frames, including deinterleaving in Lua, a Hann window (`array.multiply`), two `fft_real` calls and a per-bin loop. It was 0.5 ms at 1,024 frames and 1.9 ms at 4,096. Spread the tracks over several defer cycles rather than reading every track on each cycle.

Measured on REAPER 7.82 while building a live Y axis from track accessors (ReaperForVisual step 7, 2026-10-13 and 14):

- **One complex FFT carries both channels.** Read the interleaved stereo buffer as complex samples z = L + iR, window it with an interleaved Hann (`buf.multiply(win)`) and call `buf.fft(N, true)`; no deinterleaving. With a = Z(k) and b = Z(N−k), bin k's left power is (|a|²+|b|²+2Re(ab))/4, its right power (|a|²+|b|²−2Re(ab))/4 and its cross power Im(ab)/2, whatever the FFT's sign convention. A window then costs about 0.2 ms on 48 kHz files: the FFT itself 0.017 ms, the rest reading and the per-bin loop.
- **Read the FFT with partial tables.** `buf.table()` of 4,096 values took 0.13 ms, indexing `buf[i]` 0.31 ms; two partial tables (`buf.table(offset, size)`, offset 1-based), the bins up to 20 kHz and their mirrors, 0.115 ms.
- **Ask for the file's own sample rate.** On 44.1 kHz files, windows read at 48 kHz cost 0.43 ms and at 44.1 kHz 0.29 ms (REAPER resamples otherwise). `GetMediaSourceSampleRate(GetMediaItemTake_Source(take))` gives it.
- **The first read on a new accessor costs more**: 0.65 ms alone, 0.45 ms each in fives, 0.40 ms on one kept; making and destroying one costs about 4 µs.
- **An accessor is a snapshot.** After an item moved, one made before kept reading the old layout until `AudioAccessorUpdate`; a new one read the new layout. `AudioAccessorStateChanged` turned true only a few defer cycles after the edit, never inside the call that made it, and only for some edits: a move, an item muted or unmuted, an item added, a take FX added or bypassed (and a track FX added, which changes nothing it reads). It stayed false for item and take volume, take pan, item length, take start offset, playrate, fades and a take FX parameter. To follow every edit, fingerprint the items when `GetProjectStateChangeCount` moves, or make accessors fresh. Name a take's source in the fingerprint by `GetMediaSourceType` and `GetMediaSourceFileName`, never by `tostring()` of the source: that is a pointer, and opening the project again gave every take a new one (`...1DD0` became `...2140`), so a fingerprint kept across sessions read every item as changed (ReaperForVisual step 9b, 2026-10-16). Not by `GetMediaSourceLength` either: with the media set offline (action 40100, or REAPER's "set media offline when inactive" whenever REAPER goes to the background) it reads 0, and the type and file name stay; setting media offline and online also moves the state counter, so a watcher re-fingerprints right then (one user's map was read again 50 times in 33 minutes, step 10b). Saving a copy with `Main_SaveProjectEx` and setting media offline and online kept the pointers.
- **Reading changes nothing.** 37 accessors made and 12,000 windows read moved neither the dirty flag, nor undo, nor the state counter; nor did a 600 s run of a defer script that made, read and destroyed accessors each cycle, with a change of the user's pending all along (it stayed pending).
- **A spectral centroid needs dense windows.** Against 2,048-point Welch sections of the same stems, one window a second put the centroid 0.18 octave off in median (p90 1.0); five a second 0.025 per section, ten 0.015. A centroid from 1/3-octave band powers adds about 0.05 octave; keeping per channel the power and the power-weighted log2 f instead is exact, and both add up across windows and tracks with any gain.
- **From FFT power to mean square.** With a periodic Hann window of N points, the sum of |X(k)|² over the positive bins of one channel is 3N²/16 times its mean square (N/2 bins times the window's Σw² = 3N/8). Divided by that and by the number of windows, a 1 kHz sine of peak 0.25 on both channels read -12.04 dB, as it should; on a band's tracks without FX, files read this way through each track's fader and pan were 0.18 dB from their rendered stems in median per section (ReaperForVisual step 9, 2026-10-15). Tracks with FX are off by what their FX add: 3.0 dB in median (p90 7.4) on the same band, 0.47 dB once each track's ratio of meter to files was learned on other sections.
- **Short windows miss drum hits.** Power read from 2,048-sample windows every 0.1 s (43 % of the time) against every sample, per 2 s of a real multitrack at 120 BPM: snare close mic 2.2 dB off in median, kick 1.9, hi-hat 1.2, bass, piano and vocals 0.1–0.3; with only 3 of the 5 windows read, the snare 3.7 (p90 14.7), with 1 of 5, 8.0. Random offsets within the slots barely helped. For a part's level, prefer the track meter (a level-from-meters fit was 0.9 dB per section) or read every sample; a gain learned as meter over a sparse reading carries the reading's error wherever it is applied (ReaperForVisual step 9, 2026-10-15).
- **Average levels as powers.** Averaged in dB, a part that comes and goes reads low: backing vocals 4 dB under their rendered level over a section. Ratios (gains) may average in dB.
- **Count the silence.** A level over a range is the power over every window read there, silent windows and stretches with no item included. Leaving out the blocks that hold no sound puts a sine that plays in a quarter of a 20 s range at its level where it plays, 6.02 dB over its average there; with them counted, it read 0.08 dB off that average (ReaperForVisual step 9b review, 2026-10-15). Keep the count of windows read as well: it tells a stretch read and silent from one not read yet.
- **Before the track's FX means before its EQ**: on a mix played live, centroids read this way were 0.09 octave off for tracks without FX and 0.22 for tracks with FX, with an 8–9 dB high shelf on a hi-hat and a ride putting them 2–3 octaves low.

## Live meters

`reaper.Track_GetPeakInfo(track, 0)` and `(track, 1)` return the left and right peak of the track's output **after FX, fader and pan**, as the track meter shows them. They are amplitudes, where 1.0 is 0 dBFS. With the same sine tracks, they read exactly what the pan law predicts:

| Track | L | R |
|---|---|---|
| pan +0.5 | −18.06 | −12.04 |
| fader −6 dB | −18.06 | −18.06 |
| stereo mode −0.702, width 0.3 | −12.04 | −22.56 |
| JS volume +6 dB | −6.02 | −6.02 |

- The meters move only while the audio engine runs. Starting playback from a script opens it even with REAPER in the background and `audiocloseinactive` on; the first readings arrived 0.45 s after `OnPlayButtonEx`. When playback is stopped, the meters fall to silence, except for what still sounds while the engine runs: with "Run FX when stopped" on (`get_config_var_string("runallonstop")` read 1) and the engine opened with action 40101, a JS Tone Generator at -40 dB kept its track's meter at -40.1 dB while stopped, and a track armed with monitoring showed its input, the interface's noise floor at -82.7 dB (REAPER 7.82, 2026-10-15). A stopped meter therefore shows dither, FX noise and live input, never the song at the edit cursor: to see a mix while stopped, read the files (below) and apply the faders and pans.
- The value is the meter as drawn, with its decay, not the raw peak of the last block. Two threads write it: the audio thread raises it at once whenever audio peaks above it, and REAPER's UI lowers it one step at its meter ticks unless a peak since the last tick holds it up. Inside one script call that held the UI for 0.4 s, a noise track's meter rose twice and a decaying tone's never fell. The ticks come from the meter update setting (`get_config_var_string("vuupdfreq")` read 30 Hz, 33.3 ms) rounded up by Windows' 15.6 ms timer to about 47 ms here (it wobbles between ~46 and ~47 ms); the audio buffer was 128 samples, 2.7 ms, and played no part. After a tone stopped the meter fell 1.4148 dB per tick, 30 dB/s, the `vudecay` setting (`get_config_var_string("vudecay")` read 30), all the way down to -155 dB; stopped, with an FX making the tone, it fell the same way. Read every 31 ms, one reading in three repeats the previous one, which once made the decay look like 43–46 dB/s; on noise, which rises between ticks, nearly every reading differs. The decay is the same on both channels, so the left/right ratio holds.
- Three things break a naive reading of it. The step follows the refresh time, so it is 1.38 dB now and then: test for a fall of about one step or more, not an exact multiple. REAPER stalls now and then, and one reading then falls several steps. And REAPER renders audio in bursts, so a refresh can see no audio: a steady tone's meter dips exactly one step and comes straight back. A rise, or a fall of less than a step, is the exact new peak; anything else only bounds it from above.
- `Track_GetPeakHoldDB(track, ch, true)` returns the hold in dB × 0.01 and clears it. Read without clearing, then clear, and the next read gives the largest peak since: the raw peak per refresh, or -150 dB (-1.5) when no refresh came in between. The API calls it the meter's hold state, so the hold readout on screen is very likely the same one; that was not yet seen on screen.
- Meters work on every kind of track, including a MIDI track with a virtual instrument and folder or send buses, where an audio accessor reads nothing.
- A **record-armed** track with input monitoring meters its **input**, before FX, fader and pan: with the fader at -inf and pan hard left, a mono input read -76.4 dB on both channels (REAPER 7.82). Its own meter says nothing about where the monitored signal sits in the mix; read the bus it feeds, or apply its pan and fader to the input level.
- MIDI activity does not show: tracks playing MIDI items into an offline, bypassed or missing instrument read silence throughout playback.
- Mute and solo, read on the meters (REAPER 7.82, master muted so its meter showed the sum before its mute):
  - A muted track's meter falls to silence, and so does a track whose Mute envelope sits at 0. The mute button mutes even where the Mute envelope says 1, and with all automation bypassed.
  - A folder muted while playing leaves its child's meter showing the child's signal; a folder muted before playback starts leaves its child unprocessed, its meter silent.
  - Under solo, the meters of the tracks it silences keep showing their signal; only the master's sum leaves them out. The exception is the sibling of a soloed child: it is cut on its way into the folder, so the folder's meter shows only the soloed child.
  - What reaches the master: a soloed track; everything that feeds a soloed bus, through folders and sends alike; a soloed child, carried by its folder parents. "Solo in place" (`I_SOLO` 2) also passes the sends on its way; plain solo (1) stops at a send to a bus that is not soloed (a track with its master send off, soloed with 1, was not heard at all). Solo defeat (`B_SOLO_DEFEAT`) passes always, even inside a folder that is not soloed.
- The master's own meter shows the mix before the master's mute: with the master muted, in-phase sines whose sum peaked at 1.0 read 0.0 dB on it.
- Channels 1024 and 1025 (loudness) read 0 unless the track's meter is set to show loudness, and changing that setting changes the project.
- Level from meters alone (ReaperForVisual, revised 2026-10-12): rebuild the raw peaks from the fixed decay (a reading on the decay line counted as 0.4 of itself, a lone dip dropped), then over 2 s take 10·log10(mean S) − (4.480 + 0.467·10·log10(max S / mean S)), S = L² + R². The coefficients were fitted on REAPER's own readings recorded while a 37-track band played (an emulation of the meter gave 4.546 and 0.502). Live, against rendered truth: 0.93 dB median per section on the band (p90 2.3), 0.83 dB per 2 s window and 0.80 per 20 s on three recordings the fit never saw (p90 2.1 and 1.9). A pure tone reads about 1.5 dB low (its crest is 3 dB, music's more) and white noise about 5 dB high (its crest is 11 dB, yet its peaks are as steady as dense music's). With other meter settings the coefficients change: emulated, a 33 ms or 70 ms refresh, or a 15 dB/s decay, cost 1.2 to 1.7 dB with the coefficients above and 1.0 to 1.2 dB with coefficients looked up by the measured step and refresh. On a second full song the fit never saw (36 tracks, 96 s, 2026-10-16): 1.47 to 1.57 dB median per section, where the band read 0.93. A kick trigger, short clicks in digital silence, read 15 dB loud, and refitting the two coefficients on both songs only moved the error between them (emulated: the second song 1.79 to 1.47 dB on its own, the trigger still about 12 dB loud): no crest model built from peaks places such a track. Where a track has no FX, its files give its level exactly (an audio accessor through its fader and pan) and need no model.
- A peak meter overstates level against RMS by the source's crest factor. On 25 raw multitrack sources, the mean of the squared 31 ms peaks was 3.7 to 13.5 dB above RMS (median 8.6). Bass and backing vocals sat near 4–6 dB, cymbals and snares near 11–12. With the meter's decay included, the gap grew to a median of 11.1 dB. Correct for crest factor before ranking parts by peak level. Judged leave-one-track-out against rendered stems of a 37-track band (after FX, 166 track-sections), with the meter emulated as measured (46 ms refresh, 30 dB/s): a fit on how much the meter jumps predicted the level to a median of 3.0 dB (p90 5.4) from the readings as they are, 1.2-1.3 dB (p90 2.8) after rebuilding the raw peaks from the fixed decay (a reading on the decay line taken as 0.3-0.5 of it), and 0.9 dB (p90 2.8) from raw peaks read from a cleared peak hold. Over 2 s windows the rebuilt peaks gave 1.5 dB (p90 3.7), the raw ones 1.3 dB (p90 3.2); over 0.5 s, 2.5 and 1.3 dB.
- The cost is negligible: reading both channels of 224 tracks took 0.057 ms per cycle.

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

Write the ride with one `add_envelope_points` call, one `{"time": t, "db": d}` per point. The tool creates the envelope when it is missing and converts into its scaling, then reads every point back. Check the result with `get_envelope_points`.

Volume envelopes store scaled values rather than linear amplitude. When reading envelope points through the bridge, apply `ScaleFromEnvelopeMode` before interpreting the data as decibels to avoid incorrect scaling assumptions.

Automation placement depends on what the ride is for:

- **A levelling ride** evens out a source before its dynamics. Put it on the source track, preceding the channel compressor: that is the `Volume (Pre-FX)` envelope, since `Volume` acts after the FX chain. This feeds a normalized signal into the dynamics processor. If the routing is post-fader, the volume envelope applies to the send.
- **A balance ride** sets one element against the rest of the mix, for example lifting a buried vocal line. Any compressor downstream undoes part of it, including one on a parent bus. Measured: a +3.0 dB `Trim Volume` ride on a lead-vocal sub-bus, feeding a vocal-group SSL G Channel at 8:1, raised the lead by +1.5 dB. Put a balance ride after the last compressor in the element's path, which is usually `Trim Volume` on the group bus. `Trim Volume` adds to the fader instead of replacing it.

Whichever you write, measure the result where it is heard: render the group stem, not the child, and compare phrase by phrase with the prediction. If it fell short, find the compressor that ate it before writing a larger ride.

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
