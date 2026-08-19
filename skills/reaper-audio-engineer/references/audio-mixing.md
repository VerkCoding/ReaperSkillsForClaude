# Mixing

## 1. Entry condition and scope

This document assumes the handoff from [audio-recording](./audio-recording.md) exists: the intake record, the source list, measured timing offsets, usable dynamic range, the brief or its assumption list, and the initial session state.

If the handoff does not exist, execute the recording pass first. Processing unvetted material can lead to incorrect adjustments.

The transport layer, running Lua through the file bridge, and the render traps are in `reaper-mcp`. The offline measurement toolkit is in `./audio-measurement.md`. This document specifies what to measure, the sequence of operations, and the adjustments derived from measurements.

The three evidence tiers carry over from the recording pass: Tier 1 is specification and arithmetic, Tier 2 is usable reasoning, Tier 3 is trade convention. Every threshold is tagged.

## 2. The basic loop

Every operation in this document executes the following five steps.

1. **Hypothesis.** State the problem as a testable proposition. "Bass and kick are competing in 60-120 Hz" is a hypothesis. "The bass sounds muddy" is not, as auditory perception cannot be measured directly by the system.
2. **Predict the measurement.** Specify the expected numerical outcome before making an adjustment.
3. **Change one thing.** Execute one logical change at a time to maintain clear undo history.
4. **Measure again.** Use the identical method, time window, and solo state. Measurements taken under different conditions are not comparable.
5. **Keep or undo.** If the measurement does not match the prediction, revert the change and formulate a new hypothesis. Increasing parameter values without a correct prediction leads to errors.

## 3. The level-matching rule

Every before-and-after comparison must be level matched prior to analysis. 

Raising signal level alters perceived loudness and frequency sensitivity (Tier 1). Processing that increases level may appear improved solely due to the volume increase. Spectral and crest measurements vary with level; unmatched comparisons produce systematic errors.

Procedure:

1. Measure LUFS-I of state A.
2. Measure LUFS-I of state B.
3. Calculate the difference d = LUFS(B) - LUFS(A).
4. Apply -d to B, then compare spectrum, crest, and correlation.

Report the value of d when recording the adjustment, as a level increase caused by equalization constitutes a gain change.

## 4. Measuring one element: procedures

Every `analyze_*` executes a full project render and measures the master output.

**Single element measurement:** Record the current solo and mute state, execute `set_track_solo` on the target track, run the measurement, and restore the state. Each measurement requires a full project render.

**Multiple element measurement:** Execute `render_stems` once across the required tracks, then measure each file. 

Stem characteristics:
- A stem includes track FX but excludes bus or master processing.
- To measure unprocessed audio, bypass track FX before rendering and restore afterwards.
- A stem is a snapshot. It is invalidated after subsequent processing changes.

For measurements requiring more than seven bands of resolution, a 1/3 octave resonance sweep, or a cross-correlation to find sample offset, use the Lua toolkit in `./audio-measurement.md`.

Measurement consistency rules (Tier 2):
- Apply a level gate before analysis to exclude silence from the average.
- Normalize to the signal's mean when comparing spectra to evaluate tonal balance independent of overall level.

## 5. Order of operations

A mix executes sequentially through nine gates. Complete the exit condition of each gate before proceeding to the next.

| Gate | Content | Exit condition |
|---|---|---|
| G0 | Session build: routing, buses, naming, gain staging | No track clips; routing map recorded |
| G1 | Repair: phase, timing, hum, clicks, noise | No sustained negative correlation; hum minimized |
| G2 | Static balance with faders and pan | Relative level table matches intent within stated tolerance |
| G3 | Subtractive EQ: remove measured excess | Every cut traces to a measurement |
| G4 | Compression: control dynamic range | Crest adjusted to prediction; compared level matched |
| G5 | Additive EQ and saturation: targeted coloration | Every addition traces to the brief |
| G6 | Space: delay and reverb via sends | Correlation and mono level within tolerance |
| G7 | Automation: movement following structure | Short-term level swing within target range |
| G8 | Bus processing and final checklist | Section 14 list completed |

This sequence prioritizes dependencies (Tier 2). G1 must conclude before G2, as delay compensation alters phase relationships and summed levels, which would invalidate prior static balance adjustments.

## 6. G0: session build

Four tasks.

**Naming.** Name tracks prior to rendering to ensure correct stem file names.

**Buses.** Group sources (e.g., drums, bass, guitars). Execute `create_bus` and document routing with `list_tracks` and `list_sends`.

**Gain staging.** Establish operating ranges for processing and maintain headroom on the master sum. Execute `detect_clipping` on the master after all tracks are active.

**Verify routing.** Mute sources for each bus and verify the bus output is zero.

## 7. G1: repair

**Polarity and delay.** Utilize timing offsets from the intake record. For multiple microphones on a single source:
1. Apply delay compensation based on measured samples.
2. Measure correlation with `analyze_stereo_field` on the soloed pair.
3. Measure summed level against individual tracks.
If correlation is negative after delay compensation, invert polarity and remeasure. If unresolved, record as a source placement constraint.

**Hum and buzz.** Identify fundamental frequency and harmonics using a 1/3 octave sweep. Apply narrow notch filters.

**Broadband noise.** Apply gating or noise reduction only if measured dynamic range indicates the noise floor will interfere with automation. Compare signal level in silent passages before and after processing.

**Clicks, pops, DC offset.** Process at the item level.

**Exit condition for G1:** No sustained negative correlation in low frequencies between microphones on one source; hum peaks reduced to the noise floor.

## 8. G2: static balance

**Measurement window.** Select a representative time selection. Maintain this exact window for all G2 measurements.

**Level table.** Execute `render_stems`, measure LUFS-I or RMS of each stem over the selected window, and calculate the difference relative to the primary element defined in the brief.

**Intent verification.** Compare relative levels to the brief's specifications or a level-matched reference file. State the reference used. (Tier 3 references vary by genre).

**Pan.** Apply with `set_track_pan`. Verify mono compatibility by measuring correlation and mono fold level versus stereo level.

**Exit condition for G2:** Relative level table aligns with stated intent and tolerance.

## 9. G3: subtractive EQ

**Find resonances.** Execute a 1/3 octave sweep through the file bridge:
1. Apply level gate.
2. Normalize to signal mean.
3. Identify peaks exceeding the local trend.

**Filter application.** Frequency is determined by the peak. Q is determined by peak width. Apply attenuation equal to 50-66% of the measured excess. Resweep to verify reduction.

**High-pass filters (HPF).** 
1. Identify the lowest fundamental frequency.
2. Measure energy below that frequency.
3. Apply HPF only if significant energy exists below the fundamental range.

**Masking.** (Tier 2)
1. Measure spectra of overlapping sources A and B, normalized to their means.
2. Identify frequency bands with concurrent high relative energy.
3. Apply subtractive EQ to the lower-priority source according to the brief.

**Exit condition for G3:** All EQ cuts correspond to a documented measurement.

## 10. G4: compression

**Crest factor target.** Measure initial crest factor and define the target crest factor prior to processing.

**Threshold.** Determine based on the level distribution to target a specific proportion of the signal.

**Ratio.** Calculate based on desired gain reduction at the maximum peak (Tier 1).

**Attack.** Set based on desired transient modification.

**Release.** Determine from material tempo. Execute `analyze_transients`, calculate median onset spacing, and set release to complete recovery before the subsequent onset.

**Verification:**
1. Compare resulting crest factor to target.
2. Measure LUFS-I for makeup gain calculation.
3. Compare level-matched spectra to verify tonal shifts.
4. For rhythmic material, recount detected onsets to verify transients are not excessively suppressed.

**Exit condition for G4:** Crest factor meets target; level-matched spectral comparison documented.

## 11. G5: additive EQ and saturation

**Traceability.** Every adjustment in G5 must correspond to a specific requirement in the brief.

**Application:**
- Apply additive EQ with low Q and moderate gain.
- Verify saturation by comparing level-matched spectra and confirming new harmonic energy. Measure crest factor changes.
- Verify stereo correlation after applying asymmetric processing.

**Exit condition for G5:** Documented list of adjustments and their corresponding justifications from the brief.

## 12. G6: space

**Routing.** Utilize sends to effects buses with `create_send`.

**Pre-delay.** (Tier 3) Calculate based on tempo (60000 / BPM) and select a fraction that avoids masking subsequent transients.

**Reverb time.** (Tier 2) Target a decay time that does not overlap subsequent notes, using median onset spacing from `analyze_transients`.

**Low-frequency filtering.** Apply a high-pass filter on the send to maintain mono compatibility.

**Verification:**
1. Measure L/R correlation.
2. Measure mono fold level.
3. Measure bass band energy (level matched).

**Exit condition for G6:** The three metrics are within documented tolerances.

## 13. G7: automation

**Structure mapping.** Measure RMS per bar to identify section boundaries.

**Level automation:**
1. Measure short-term level via a sliding window.
2. Define target level values per section.
3. Generate envelope points (`add_volume_automation`) based on the delta between measured and target levels.
4. Apply rate limiting to envelope changes.

**Macro automation.** Adjust bus or master levels between sections based on the brief.

**Exit condition for G7:** Short-term level variance is within target range; inter-section level changes correspond to the structure map.

## 14. G8: buses, and the final checklist

Document any processing applied to the master bus for handoff to mastering.

| Metric | Measurement Method | Threshold |
|---|---|---|
| Clipping | `detect_clipping` (master/buses) | 0 samples at 0 dBFS |
| True Peak | `analyze_loudness` | ~ -6 dBTP (Tier 3 convention) |
| Mono Compatibility | Correlation & mono fold level | Positive correlation; standard mono level |
| Spectral Balance | `analyze_frequency_spectrum` | Within G2 tolerance (level matched) |
| Dynamic Range | `analyze_dynamics` | Crest factor recorded |
| Solo/Mute State | `list_tracks` | Matches intake record |
| Track Count | `list_tracks` | Matches intake record (no temp tracks) |

Report any metrics that fail to meet the thresholds.

## 15. System constraints

Measurements determine resonances, levels, phase alignment, masking, and dynamic range. Measurements do not evaluate aesthetic quality or emotional impact.

When addressing subjective components:
1. Distinguish measured results from conventions (Tier 3).
2. Document unprocessed elements and rationale.
3. Provide one parameter and directional consequences for user review (e.g., "Adjust instrument bus automation between -1.5 and -2.5 dB to modify vocal presence").

## 16. User feedback

1. Translate subjective feedback into measurable hypotheses (e.g., "muddy" correlates to 200-500 Hz excess).
2. Measure and apply adjustments if data supports the hypothesis.
3. If data does not support the hypothesis, report the measurement results and propose one test adjustment.
Do not apply adjustments without measurement verification.

## 17. Troubleshooting

| Symptom | Cause |
|---|---|
| Inconsistent comparisons | Lack of level matching |
| Adjustment degrades result | Incorrect hypothesis; revert and reassess |
| Static balance invalidated | Phase/delay modified after G2 |
| Source character altered by EQ | Q value too low |
| Compressor pumps | Release time exceeds onset spacing |
| Mono sum lacks bass | Missing HPF on reverb send |
| Stem mismatch | Stems exclude bus/master processing |
| Measurement discrepancy | Inconsistent window, solo state, or stale stem |
| Render distortion | Integer format clipping (check dBTP) |
| Latency persists | Bypassed plugin requires offline state |

## 18. Handoff to mastering

Provide the following for [audio-mastering](./audio-mastering.md):
1. Mix file (sample rate, bit depth, format).
2. LUFS-I and dBTP.
3. Crest factor and DR.
4. L/R correlation and mono sum level.
5. Master/bus processing list.
6. The brief and delivery target.
7. Unresolved source constraints.

## 19. Session closure

- Restore solo, mute, and bypass states to intake configuration.
- Remove temporary analysis tracks.
- Restore time selection and render settings.
- Remove temporary stem files.
- Do not save the project file; allow the user to execute the save operation.
