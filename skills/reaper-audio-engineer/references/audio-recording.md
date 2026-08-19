# Recording and intake

## 1. Document sequence

Three documents are executed sequentially. Each requires an entry condition and a written output:

| Document | Input | Output |
|---|---|---|
| **audio-recording** (current) | Assessment of source material and mix requirements | Intake record and project brief |
| **[audio-mixing](./audio-mixing.md)** | Source material | Mixed audio file and headroom specification |
| **[audio-mastering](./audio-mastering.md)** | Mixed audio file | Mastered file and deliverable set |

This document omits transport layer functions. Instructions for executing REAPER functions, using Lua via the file bridge, reading sample data, and preventing silent renders are located in the `reaper-mcp` skill documentation. Refer to this document for measurement parameters and data interpretation.

### Evidence tiers

- **Tier 1**: Published specifications, industry standards, verifiable arithmetic, repeatable measurements.
- **Tier 2**: Analytical reasoning derived from Tier 1.
- **Tier 3**: Industry convention, reference values, subjective assessment.

Every numeric threshold below includes its associated tier. Tier 3 thresholds are baseline values, not definitive standards.

## 2. Operating principles

Source quality determines maximum possible mix quality. The following defects remain after mix processing: clipping recorded into the file, noise floor exceeding the minimum signal level, phase cancellation recorded at the source, and performance errors. Identifying these defects during intake requires less time than identifying them during mastering.

Subjective auditory assessment is not possible. The following constraints apply:

1. Record measured data, not perceived audio.
2. Base conclusions on specific measurements and the methodology used.
3. Verify user notes by locating the corresponding metric data.

Diagnostic measurement precedes processing. Do not apply processing plugins before identifying the target metric.

## 3. Measurement tool functionality

The `analyze_*` tools function by rendering the entire project and measuring the master output. They do not measure individual tracks directly.

- To measure a single source: Use `set_track_solo` on the target track, execute the measurement, and remove the solo state. Document original solo and mute states prior to modification, and restore them upon completion.
- Alternatively, use `render_stems` and measure the resulting files. This tool solos one track per render; the resulting stem includes track-level effects but excludes bus and master processing.
- Measurement values represent post-FX and post-fader signals. To measure raw audio, bypass track effects prior to measurement and restore them upon completion.

Tool constraints:

| Tool | Output | Functional Limit |
|---|---|---|
| `analyze_loudness` | LUFS-I and dBTP per ITU-R BS.1770 | Integrated value for the full programme. Does not identify specific high-amplitude sections. |
| `analyze_frequency_spectrum` | RMS across 7 fixed bands | Resolution is insufficient for identifying narrow resonances. Use for broad spectral balance assessment. |
| `analyze_dynamics` | RMS, peak, crest factor, dynamic range | Crest factor is dependent on the measurement window. Comparisons require identical window lengths. |
| `analyze_stereo_field` | mid/side balance, width, L/R correlation | Averaged over the programme duration. Brief out-of-phase segments may not affect the average significantly. |
| `analyze_transients` | Up to 100 onsets | Maximum limit of 100 onsets. For extended audio, measure using a specific time selection. |
| `detect_clipping` | Samples at or exceeding 0 dBFS | Detects sample peaks, not true peaks. Does not detect inter-sample peaks. |

For high-resolution frequency measurement, such as a 1/3 octave resonance sweep, utilize the Lua toolkit documented in `./audio-measurement.md`.

## 4. Intake record

Execute the following checks sequentially. Output a table with one row per source, including a status column with the values: **pass**, **warning**, or **blocker**.

### 4.1 Technical inventory

Execute `get_project_info` and `list_tracks`. Document the sample rate, bit depth, channel count, and length for each source file.

Sample rate, start point, and playback speed must match across the session. A sample rate mismatch converted silently by the DAW results in altered pitch. An incorrect start point results in asynchronous audio playback.

To identify a misread sample rate, calculate the length ratio. A 44.1 kHz file processed as 48 kHz will be approximately 8.8 percent shorter and 1.4 semitones higher in pitch (Tier 1).

### 4.2 Clipping

Execute `detect_clipping` with each source soloed, or on each rendered stem.

- **Zero samples at 0 dBFS**: Inconclusive. The signal may have clipped prior to digital conversion and subsequently been reduced in gain, resulting in flat-topped waveforms below 0 dBFS.
- **More than zero samples at 0 dBFS**: Digital clipping is present. Document the number of consecutive samples at 0 dBFS. Runs of tens of samples indicate unrecoverable flat-topped waveforms.

To identify gain-reduced clipping, compare the crest factor against the reference range for the source type (section 4.5). For example, a kick drum with a 6 dB crest factor indicates prior clipping (Tier 2).

### 4.3 Peak and true peak

`analyze_loudness` provides the dBTP value. For raw material, this value is used to calculate the headroom budget.

True peak values exceeding sample peak values are standard. A difference of several dB indicates high-frequency content requiring management at the final limiter stage (Tier 1, Tier 2).

### 4.4 Noise floor

Set a time selection over a section containing no primary signal, and measure the RMS value. This value represents the noise floor RMS.

The usable dynamic range equals the signal RMS minus the noise floor RMS. This value dictates allowable compression and upward automation limits, as increasing signal gain increases noise floor gain proportionally.

Reference thresholds (Tier 3):

- Above 60 dB: Noise floor impact is negligible.
- 40 to 60 dB: Usable dynamic range. Upward automation will increase noise floor audibility.
- Below 40 dB: Noise floor restricts processing. Notify the user prior to mixing.

Identify the noise type to determine treatment: broadband hiss, discrete harmonics (e.g., 50 or 60 Hz hum), or cross-talk (bleed). Execute `analyze_frequency_spectrum` on the silent section to identify low-frequency harmonic concentration.

### 4.5 Crest factor

Execute `analyze_dynamics` on each soloed source over a representative section.

The following ranges are reference values (Tier 3), dependent on hardware, environment, and performance.

| Source | Typical crest | Lower value indicates | Higher value indicates |
|---|---|---|---|
| Close-miked kick, snare | 15-20 dB | Prior compression or clipping | Distant microphone placement, aggressive gating |
| Overheads, room | 12-18 dB | Prior compression | Non-reflective room, close microphone placement |
| Sustained vocal | 10-15 dB | Prior compression | Wide dynamic range |
| Bass DI | 8-14 dB | Prior compression or limiting | Inconsistent amplitude |
| Amplified guitar | 8-14 dB | Amplifier compression | Direct injection recording |
| Unmastered mix | 12-18 dB | Master bus limiting | Sparse arrangement |

Document values outside these ranges and formulate a hypothesis regarding the cause.

### 4.6 Phase correlation between multiple microphones on a single source

This applies to sources recorded with multiple microphones.

Solo the relevant tracks, execute `analyze_stereo_field`, and document the L/R correlation:

- **Near +1**: Signals are highly correlated. Standard for multiple microphones on a single source; indicates a narrow stereo image for a stereo pair.
- **Near 0**: Uncorrelated signals. Standard for spaced overhead pairs.
- **Negative**: Signals are inversely correlated, causing phase cancellation. Sustained negative correlation in low frequencies results in bass frequency reduction when summed to mono.

The measurement is an average over the selection. Measure multiple short time selections to prevent averaging out brief negative correlation periods.

To verify, measure the combined RMS level of both tracks, and compare to the RMS of each track individually. Positively correlated signals sum toward +6 dB; negatively correlated signals sum to a level below the individual tracks (Tier 1).

### 4.7 Timing offset

Multiple microphones on a single source will have a timing offset equal to the distance difference divided by the speed of sound. At 20 degrees Celsius, sound velocity is approximately 343 m/s, yielding a 1 ms offset per 34.3 cm (Tier 1).

This offset causes comb filtering when summed. For delay time t, nulls occur at f = (2n+1) / (2t). A 1 ms offset produces a primary null at 500 Hz (Tier 1).

To measure timing offset: Execute `analyze_transients` on each soloed track and calculate the difference in onset time for a shared transient. For higher precision, utilize the cross-correlation function via the file bridge (refer to the Lua toolkit).

Document the offset value. Alignment procedures are detailed in [audio-mixing](./audio-mixing.md).

### 4.8 DC offset, bleed, head and tail silence

- **DC offset**: A sample mean non-zero value. It reduces available headroom and causes asymmetrical limiting. Measure using the file bridge.
- **Bleed**: Track RMS level during sections where the primary source is inactive. Limits gate functionality and ties EQ changes to the bleed signal.
- **Head and tail silence**: Document durations to determine start points and delivery fade lengths.

### 4.9 Record format

Output a table with one row per source. Include a status column indicating **pass**, **warning (with consequence)**, or **blocker (with required action)**.

Omit measurements that do not require an action or decision.

## 5. Correction limits

The following table categorizes defects by correctability (Tier 2).

| Measured condition | Correctable in mix | Required action |
|---|---|---|
| Isolated samples at 0 dBFS | Yes | Reduce clip gain, document, proceed |
| Consecutive flat-topped samples | No | Report defect, request rerecording |
| Low crest factor on close-miked source | Partial | Document prior compression, adjust processing expectations |
| Usable dynamic range below 40 dB | Partial | Report noise floor limits on upward automation |
| Hum at mains frequency and harmonics | Yes | Apply narrow notch EQ |
| Persistent negative correlation in bass frequencies | Usually | Invert polarity or adjust delay. If negative correlation persists, defect is uncorrectable. |
| Timing offset between microphones | Yes | Apply delay compensation based on section 4.7 data |
| Pitch or timing performance errors | N/A | Request user instruction |
| Source characteristics do not match label (e.g., high frequency transient labeled as drum body) | No | Report discrepancy and describe actual characteristics |

Report uncorrectable defects instead of attempting ineffective processing.

## 6. Gain staging in floating-point systems

REAPER utilizes a floating-point mix engine. Amplitude values exceeding 0 dBFS within the engine are retained; reducing the fader level restores the waveform. The 32-bit float range exceeds standard audio dynamic range limits (Tier 1).

Clipping occurs at specific points:

1. **Analog components and ADCs**: Preamplifiers and analog-to-digital converters have fixed maximum input levels. Clipping at this stage is permanently recorded.
2. **Fixed-point and analog-modeled plugins**: Certain plugins operate with a calibrated reference level (e.g., equivalent to 0 VU). Input signals exceeding this level will generate distortion.
3. **Integer format rendering**: Output rendering to integer formats will apply hard clipping at 0 dBFS.

Set input gain to optimize signal-to-noise ratio within the analog hardware's operating range. Calibration standards typically set the analog reference at -18 to -20 dBFS (Tier 1). Target peak levels of -18 to -12 dBFS are reference values (Tier 3) dependent on specific hardware calibration.

Measure peak levels during the highest amplitude section and set gain to prevent 0 dBFS exceedance at the ADC (Tier 1).

## 7. Recording procedure

If executing `start_recording`, verify the following conditions:

1. **Signal path**: Verify track input assignment and record-arm status using `get_track_info`.
2. **Format**: Verify project sample rate and bit depth match delivery specifications to avoid unnecessary conversion.
3. **Solo state**: Verify all tracks are unsoloed.
4. **Monitoring latency**: Delay exceeding 10 ms (Tier 3) may impede performance. Bypass or offline plugins causing latency. Bypassing retains delay compensation; offlining removes it.
5. **File nomenclature**: Assign track names prior to recording to set output file names.
6. **Test recording**: Record a brief test segment and execute checks 4.2 through 4.5.

Execute the full intake record on all newly recorded material.

## 8. Project brief

A defined objective is required for mixing. Without an objective, processing defaults to standard parameters.

Require the following parameters prior to mixing:

1. **Delivery format**: Determines loudness and true peak targets in [audio-mastering](./audio-mastering.md).
2. **Reference tracks**: Provides measurable spectral and dynamic reference points.
3. **Aesthetic parameters**: Defines spatial and tonal characteristics.
4. **Constraints**: Mandatory processing limits (e.g., no pitch correction, specific duration, mono compatibility).

If parameters are not provided, document assumed parameters and proceed. Include the assumption list in the output document.

Standard defaults (Tier 3): Streaming format, mono compatible, no performance correction, original length retained.

## 9. Handoff specification

Provide the following data to [audio-mixing](./audio-mixing.md):

1. **Intake table**: Includes all warnings and blockers.
2. **Source list**: Track classifications and multi-microphone groupings.
3. **Timing offsets**: Measured delays between grouped microphones, in samples and milliseconds.
4. **Usable dynamic range**: Value per source, and identification of sources with restrictive noise floors.
5. **Project brief**: Or the documented assumption list.
6. **Session state**: Initial track count, solo/mute states, and FX bypass states.

If a data point is missing, state its absence explicitly.

## 10. Common error states

| Symptom | Cause |
|---|---|
| Uniform loudness across sources | Project output measured instead of soloed track |
| Unresponsive solo state | Non-exclusive solo applied over existing solo |
| Silent render output | Media items offline |
| Inconsistent crest factor on same source | Differing measurement time windows |
| Positive correlation but mono bass loss | Averaged correlation value obscures section-specific negative correlation |
| Latency remains with bypassed plugin | Bypass maintains delay compensation; plugin must be offlined |
| RMS noise floor approaches zero | Measurement window overlaps digital silence |

## 11. Session exit protocol

- Restore all solo, mute, and bypass states to original configuration.
- Delete temporary analysis tracks. Verify track count against section 9 data.
- Restore time selection and render settings.
- **Do not save the project.** Saving may commit errors, such as failed plugin loads. Notify the user that the project was not saved.
