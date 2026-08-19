# Mastering

## 1. Scope

Mastering operates on one summed signal. Adjustments affect all elements simultaneously.

Decision rule:
> If the correction targets one specific element, it is a mix revision. Send the note back to the mix instead of treating it with mastering processing.

Classification (Tier 2):

| Measurement | Classification |
|---|---|
| Whole programme deviates from reference across a wide band | Mastering |
| Loudness and true peak do not meet delivery target | Mastering |
| Overall dynamic range deviates from genre norm | Mastering, within limits |
| Overall stereo width requires adjustment | Mastering |
| Narrow resonance on one instrument | Mix |
| Vocal balance | Mix |
| Bass and kick competition | Mix |
| Exposed noise floor in one section | Mix or source constraint |
| Mono summing loses low end | Mix |

When mono summing loses bass, the cause is phase cancellation between sources. Mono summing the low band at the master treats the symptom. Measure the phase cancellation and send the note to the mix.

The transport layer is located in `reaper-mcp`. The entry condition is the handoff from `audio-mixing.md`.

## 2. Entry checks

Execute these measurements prior to processing.

| Check | Measurement method | Threshold | Action on failure |
|---|---|---|---|
| Clipping | `detect_clipping` | 0 samples at 0 dBFS | Return to mix with sample count and positions |
| Headroom | `analyze_loudness` | True peak around -6 dBTP | Proceed; record actual headroom |
| Limiter status | Handoff record, `list_master_fx` | Absent or declared | Stop and request clarification if undeclared |
| Dynamic range | `analyze_dynamics` | Record crest and DR as baseline | Required for comparison post-processing |
| Mono summing | `analyze_stereo_field` | No sustained negative correlation | Return to mix |
| Spectral balance | `analyze_frequency_spectrum` | Deviation from reference inside tolerance | Narrow band deviation returns to mix |
| Format | `get_project_info` | Matches or exceeds delivery target | Document conversion plan |

If no reference track is provided, document the absence. State the baseline used for comparison.
Record all measurements before processing to quantify dynamic range changes.

## 3. Chain order

The Tier 3 reference order:

1. Corrective EQ. Based on measurement.
2. Bus compression. Macro dynamic control.
3. Tonal EQ. Low Q, traced to brief.
4. Stereo and mid/side processing. Placed after tonal EQ.
5. Saturation and harmonics. Must be measurable.
6. Limiter. Always last.
7. Dither. After limiter, applied once when reducing bit depth.

`apply_mastering_chain` provides preset configurations (`default`, `loud`, `gentle`).
Measure before applying processing. Select required blocks, use `list_master_fx` and `get_fx_parameters` for indices, then apply `set_master_fx_parameter`.
Read parameter indices, set values, and read the formatted value back to confirm.

## 4. Spectral balance comparison

Use `analyze_frequency_spectrum` for overall balance comparison.

Procedure (Tier 2):
1. Measure reference spectrum.
2. Measure mix spectrum.
3. Level match using LUFS-I before comparing.
4. Compare spectral shape.
5. Correct broad deviations with low-Q EQ.

Tolerances (Tier 3):
- < 1.5 dB: Normal variance.
- 1.5 to 3 dB: Potential correction if consistent across adjacent bands.
- > 3 dB: If narrow, return to mix.

The objective is identifying systematic deviation from the reference.
When using multiple references, use the variance between them as tolerance.

## 5. Loudness

### 5.1 Reference points

| Target | Level | Status |
|---|---|---|
| EBU R128 | -23 LUFS, +/-0.5 LU | Standard |
| ATSC A/85 | -24 LKFS | Standard |
| AES streaming | -16 to -20 LUFS | Recommendation |
| Spotify | ~ -14 LUFS | Platform specified |
| Apple Music | ~ -16 LUFS | Platform specified |
| YouTube | ~ -14 LUFS | Platform specified |
| Tidal, Amazon | ~ -14 LUFS | Platform specified |

Verify platform specifications at delivery time and document the date.

### 5.2 Normalisation

Platforms apply loudness normalisation on playback.
Consequence (Tier 2): A -6 LUFS master on a -14 LUFS platform is attenuated by 8 dB. Exceeding the target reduces dynamic range without increasing playback loudness.

Exceptions:
1. Non-normalised contexts (club playback, downloads, game audio).
2. Platform specific behavior (absence of upward normalisation).
3. Aesthetic requirement for heavy compression, as specified in the brief.

Identify delivery target from the brief, set target level, document reasoning, and measure.

`normalize_project(target_lufs)` measures the project, applies the difference to the master volume, and reports `projected_true_peak_dbtp` for the result. It warns when that projection exceeds full scale, which is the case a loudness target reaches before a limiter is in place. Take the warning as instruction to limit or lower the target, not as a reason to render and see.

### 5.3 True peak ceiling

Standard ceiling is -1.0 dBTP to accommodate lossy codecs (AAC, MP3, Opus).
For high loudness masters, a ceiling of -1.5 to -2 dBTP applies (Tier 3).

`detect_clipping` measures sample peak. True peak requires `analyze_loudness`, which returns `true_peak_dbtp` (four times oversampled) alongside `sample_peak_dbfs`, both to two decimals. The two differ by exactly the amount that matters here: a master whose sample peak read `-0.00 dBFS` measured `+0.04 dBTP`. Judge the ceiling on `true_peak_dbtp`.

The second decimal is load-bearing. At one decimal an overshoot of +0.04 dBTP prints as `0.0` and a ceiling check passes on a master that is over.

## 6. Limiter application

Procedure:
1. Set the ceiling (section 5.3).
2. Increase input gain until LUFS-I meets target. Measure with `analyze_loudness`.
3. Monitor gain reduction.
4. Execute final measurements (section 7).

`apply_limiter(threshold_db, release_ms)` inserts ReaLimit **and applies both values**, reporting what the plugin accepted. It does not set the ceiling, so step 1 is a separate write.

ReaLimit's parameters are linear in decibels but over different ranges, and using the wrong one silently misses the target by several dB:

| Parameter | Index | Range | Normalised value |
|---|---|---|---|
| Threshold | 0 | -60 to +12 dB | `(dB + 60) / 72` |
| Ceiling | 1 | -24 to 0 dB | `(dB + 24) / 24` |
| Release | 2 | `inf` down to 6 ms | inverted; solve by search |

A -1.0 dBTP ceiling is `set_master_fx_parameter(fx, 1, 0.9583)`. Applying the Threshold formula to the Ceiling instead lands on -4.33 dB. Read the formatted value back after either write.
Gain reduction exceeding 3-4 dB average indicates conflict between dynamic range and loudness target. Options:
- Apply bus compression.
- Return to mix for macro dynamic automation.
- Accept loudness below target.

Limiter release affects low frequencies. Verify by comparing level-matched spectra for harmonic distortion.

## 7. Post-processing measurement

Record a before-and-after table.

| Metric | Measurement tool | Indication |
|---|---|---|
| LUFS-I | `analyze_loudness` | Target achievement |
| True peak | `analyze_loudness` | Ceiling compliance |
| Crest factor and DR | `analyze_dynamics` | Dynamic range reduction |
| 7-band spectrum | `analyze_frequency_spectrum` | Tonal balance shift |
| Correlation and width | `analyze_stereo_field` | Mono compatibility status |
| Clipping | `detect_clipping` | Must remain zero |
| Onset count | `analyze_transients` | Limiter transient attenuation |

Document the crest factor change.

Reading these results:

- `true_peak_dbtp` is the ceiling figure; `sample_peak_dbfs` sits beside it and will read lower.
- `analyze_dynamics` reports `dr_measured_over`. On material shorter than one 3-second window the score covers the whole render instead, which is a weaker statement about sustained dynamics than a full-length measurement.
- Spectrum band levels are RMS in dBFS and sum to the overall signal RMS, so they can be compared between two masters directly rather than only against each other.
- `stereo_width_ratio` of `null` with a `width_note` means the mid channel is silent: the material is out of phase, not merely wide. `lr_correlation` of `null` means a channel never changes.
- All five analysis tools refuse an empty project rather than reporting silence, so an "empty project" error means the render had nothing in it, not that the mix is quiet.

## 8. Bit depth, sample rate, dither

Sample rate: Deliver at the mix sample rate when permitted. Execute conversion once at the final step if required.
Bit depth: Process in floating point. Deliver at target requirement.
Dither: Apply only when reducing bit depth.
1. Apply once, after the limiter.
2. Do not apply if bit depth is not reduced.
3. Noise shaping applies to 16-bit reduction.

## 9. Deliverables

Confirm required items prior to generation.

Main master file: Document sample rate, bit depth, LUFS-I, dBTP, and delivery target.
Alternate mixes:
- Instrumental: Vocals removed.
- TV track: Lead vocal removed, backing retained.
- Acapella: Vocals only.
- Clean: Specified lyrics removed.

Render all alternate mixes from the same session state.
Stems: `render_stems` exports individual tracks. Bus and master processing are not included. Document this limitation.
Archiving: Record session version, plugin list with versions, delivery target, measurements, and brief.

## 10. Final checks

Execute checks on the rendered file.
1. Measure LUFS-I, dBTP, and clipping.
2. Verify start point, final fade, and absence of artifacts at head/tail.
3. Verify duration against mix.
4. Verify mono summing.
5. Confirm no clipping.
6. Verify naming convention and metadata.
7. Repeat for alternate mixes.

Scripted renders can produce silent files if media items are offline. Measure the rendered file to confirm audio presence.

## 11. Reporting

Report structure:
1. Metrics: LUFS-I, dBTP, crest against target.
2. Changes: Dynamic range, spectral balance, transients.
3. Omissions: Items returned to mix, unaddressed constraints.
4. Verification: Distinguish measurement from convention.

Provide adjustment parameters:
> To alter compression, adjust limiter input by +/- 1 dB. LUFS-I will change accordingly. Specify the corresponding change in crest factor.

## 12. Common issues

| Symptom | Cause |
|---|---|
| Clipped delivery file | Sample peak measured instead of true peak |
| Silent delivery file | Offline media items |
| File exceeds ceiling | Limiter true peak mode disabled |
| Low perceived loudness at LUFS target | High dynamic range lowers whole-programme average |
| Master EQ ineffective | Narrow band issue requires mix revision |
| Mono bass loss after widening | Side content widened in low band |
| Transient attenuation | Fast limiter attack or heavy limiting |
| Inconsistent delivery set | Rendered from varied session states |
| Incorrect parameter value | Wrong parameter index specified |
| Playback alters master | Platform normalisation applied |

## 13. Session exit

- Restore render settings and time selection.
- Restore solo/mute states.
- Delete non-deliverable temporary files.
- Do not save the project. Document changes and defer saving to the user.
