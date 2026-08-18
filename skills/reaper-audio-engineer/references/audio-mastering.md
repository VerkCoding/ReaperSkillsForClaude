# Mastering

## 1. Scope, and a boundary to hold

**Mastering is not another pass of mixing.** That is not a slogan; it is the operating constraint of this whole document.

Mixing works on individual elements. Mastering works on one already-summed signal, so every move touches everything at once. From that comes the decision rule:

> **If the correct fix is to touch one element, that is a note for the mix, not a mastering move.** Send it back with the measurement rather than trying to treat it with a broad tool.

The classification (Tier 2):

| The measurement shows | Belongs to |
|---|---|
| The whole programme deviates from the reference across a wide band | Mastering |
| Loudness and true peak do not meet the delivery target | Mastering |
| Overall dynamic range too wide or too narrow for the genre | Mastering, within limits |
| Overall stereo width needs adjusting | Mastering |
| A narrow resonance belonging to one instrument | Mix |
| The vocal is buried or sticks out in one section | Mix |
| Bass and kick competing in the low end | Mix |
| One section has an exposed noise floor | Mix, or a source constraint |
| Mono summing loses the low end | Mix |

The last row is worth expanding: when mono summing loses bass, the temptation is to mono the low band at the master. That hides the symptom and leaves the cause, which is two sources cancelling. Measure it, then send it back to the mix.

The transport layer lives in `reaper-mcp`. The entry condition for this document is the seven-item handoff from [audio-mixing](./audio-mixing.md).

## 2. Entry checks

Run all of these before placing any plugin. Each is a measurement, and each has an action when it fails.

| Check | How to measure | Threshold | If it fails |
|---|---|---|---|
| No clipping | `detect_clipping` | 0 samples at 0 dBFS | Send back to the mix with the sample count and positions |
| Headroom | `analyze_loudness` | true peak around -6 dBTP (Tier 3, handoff convention) | Still acceptable, but record the actual headroom |
| Limiter already on the master | The handoff record, and `list_master_fx` | absent, or declared | If present and undeclared, stop and ask |
| Dynamic range | `analyze_dynamics` | record crest and DR as the baseline before processing | Without a baseline you cannot judge the damage later |
| Mono summing | `analyze_stereo_field` | no sustained negative correlation | Send back to the mix |
| Spectral balance | `analyze_frequency_spectrum` | deviation from the reference inside tolerance | A large deviation in a narrow band goes back to the mix |
| Sample rate and bit depth | `get_project_info` | matches the delivery target, or exceeds it | Write down the conversion plan |

**If there is no reference track, say so immediately.** Without one, every statement about spectral balance is convention and must be recorded as convention. You can still proceed, but state what you are comparing against.

**Record every measurement before processing.** This is the only baseline that can answer the most important question at the end: how much dynamic range did mastering take, and what did it buy.

## 3. Chain order

The reference order below is **Tier 3**. It is trade convention rather than law, and there are sensible masters that reorder several blocks. What is not Tier 3 is the reasoning behind it: each block changes the signal the next one measures, so order determines the result.

1. **Corrective EQ.** Only what a measurement proves. Placed first so later blocks do not react to something about to be removed.
2. **Bus compression.** Glue, macro dynamic control. Small amounts.
3. **Tonal EQ.** Low Q, small amounts, must trace to the brief.
4. **Stereo and mid/side processing.** After EQ, because tonal EQ changes the energy balance between mid and side.
5. **Saturation and harmonics.** Optional, and must be measurable.
6. **Limiter.** Always last in the processing chain.
7. **Dither.** After the limiter, only when reducing bit depth, and only once.

**A warning about `apply_mastering_chain`.** This tool builds three preset configurations: `default` is EQ, compression, limiter; `loud` is EQ, two compression stages, limiter; `gentle` is EQ, light compression, limiter. They are scaffolding that gives you slots, not a decision.

Placing them before measuring violates the diagnose-before-treat principle. `loud` in particular, with two compressors in series, is rarely the right answer, because it commits in advance to a large amount of compression nobody has shown is needed. The correct use: measure first, decide which blocks you need, build it, then use `list_master_fx` and `get_fx_parameters` to get parameter indices, then `set_master_fx_parameter` to set each value.

**Never locate a plugin by name.** In an organised project plugins are often renamed, and a name lookup will silently find the wrong slot. Read the index, set the value, then read the formatted value back to confirm.

## 4. Spectral balance against the reference

The seven bands of `analyze_frequency_spectrum` are the right tool for this. They are too coarse to pick a narrow EQ frequency, but they are enough to read overall balance, and overall balance is exactly what mastering works on.

The procedure (Tier 2):

1. Measure the reference spectrum.
2. Measure the mix spectrum.
3. **Level match before comparing.** Measure LUFS-I on both and compensate the difference. Skip this and whichever is louder will appear to have "more energy in every band", and the conclusion is meaningless.
4. Compare **shape**, not absolute values. What matters is the relationship between bands.
5. Correct broad deviations with low-Q EQ. Narrow deviations do not belong here.

Orienting tolerances (**Tier 3**, and strongly genre dependent):

- Under about 1.5 dB in a band: within the normal difference between two different pieces of music. Not a reason to intervene.
- About 1.5 to 3 dB: worth looking at, and worth correcting if it runs the same direction across several adjacent bands.
- Over 3 dB: worth stopping for. If it sits in a narrow band, it is most likely a note for the mix.

One thing to state plainly, because it is often misread: **the goal is not to make the mix identical to the reference.** Two different pieces of music have different spectra and that is correct. The reference is a frame for spotting systematic deviation, not a mould to pour into.

When using several references, measure them all and take the spread between them as a natural tolerance. If three references from the same genre already differ by 2 dB in a band, then 2 dB in that band is not a deviation.

## 5. Loudness

This is the area carrying the most misinformation, so it needs presenting with the reasoning rather than just the numbers.

### 5.1 The reference points

| Target | Level | Tier |
|---|---|---|
| EBU R128 (European broadcast) | -23 LUFS, tolerance +/-0.5 LU | Tier 1, published standard |
| ATSC A/85 (US broadcast) | -24 LKFS | Tier 1, published standard |
| AES recommendation for streaming | about -16 to -20 LUFS | Tier 1, published document |
| Spotify | about -14 LUFS | Platform published, **subject to change** |
| Apple Music | about -16 LUFS | Platform published, **subject to change** |
| YouTube | about -14 LUFS | Platform published, **subject to change** |
| Tidal, Amazon Music | around -14 LUFS | Platform published, **subject to change** |

The first three are industry standards and stable. The rest are company policy, they have changed several times and will change again. **Check them at delivery time rather than trusting this table**, and record in the report which date you checked.

### 5.2 The normalisation argument, and what it actually implies

Streaming platforms normalise loudness on playback: anything louder than the target gets turned down. The consequence (Tier 2, true as long as the platform does what it published):

A master at -6 LUFS sent to a platform targeting -14 LUFS gets turned down by about 8 dB on playback. It will sound as loud as a master at -14 LUFS. But it had to absorb roughly 8 dB more limiting to reach -6, so at the same playback level it has **less dynamic range and more distortion** without buying a single dB of loudness.

That is the entire argument, and it is enough to derive the working principle: **where normalisation applies, pushing loudness past the target buys nothing and pays for it in dynamic range.**

Three conditions weaken that argument, and they must be stated rather than hidden:

1. **Not every context normalises.** Club playback, DJ use, some downloads, game and application audio can all play at full level. There, high loudness is a legitimate choice.
2. **Platforms behave differently.** Some only turn down and never up, so a very quiet master stays quiet. Some offer users a louder playback mode with their own limiter.
3. **Loudness is not the only goal.** Some genres use heavy compression as musical language. There, heavy compression is an aesthetic decision, not a technical mistake. What is mandatory is that it be chosen rather than happen by default.

**The correct approach:** take the delivery target from the brief, pick the target level, record the reasoning, then measure to confirm. Do not set the target by comparison with another track without measuring that track.

### 5.3 True peak and the ceiling

**The usual ceiling is -1.0 dBTP.** The reasoning is Tier 1 and 2: lossy codecs like AAC, MP3 and Opus do not reconstruct the waveform exactly, and the decoded signal can rise above the original's peaks. A master sitting exactly at 0 dBFS will clip after encoding, in a place nobody checks. EBU R128 specifies a -1 dBTP ceiling for broadcast; the AES streaming recommendation also sits at -1 dBTP.

When a master is loud, the post-encode overshoot is larger, and a lower ceiling of about -1.5 to -2 dBTP is a reasonable precaution (**Tier 3**).

Two technical points to remember:

- `detect_clipping` measures sample peak. It does **not** see inter-sample peaks. The true peak ceiling has to be read from `analyze_loudness`.
- A limiter without true peak mode enabled will let inter-sample peaks through even while its own meter reads safe. Confirm with an independent measurement after rendering rather than trusting the plugin's meter.

## 6. The limiter

The procedure, in this order:

1. **Set the ceiling first.** Per section 5.3. This is a constraint, not something to search for.
2. **Raise input gain gradually** until LUFS-I reaches the target. Measure with `analyze_loudness` after each step rather than reading the plugin meter.
3. **Watch the gain reduction.** This is the number that decides.
4. **Measure everything again** per section 7.

`apply_limiter` puts ReaLimit on the master with a given threshold and release; after that use `get_fx_parameters` for the indices and `set_master_fx_parameter` to adjust. Always read the formatted value back to confirm you set the parameter you meant.

**The gain reduction budget.** If reaching the target needs more than about 3 to 4 dB of average reduction, stop. The figure is **Tier 3**, but the reasoning is not: an amount that large means the mix's dynamic range and the loudness target are in conflict. There are three ways out, and which one you take is a decision that must be stated:

- Bus compression at block 2 does some of the limiter's work, at the cost of changing the character of the music.
- Send it back to the mix to reduce macro dynamic range with automation, which is far cleaner than limiting.
- Accept loudness below target. Where normalisation applies this is usually the best option and the least chosen.

**Release** affects low frequency distortion. Too fast on bass-heavy material creates harmonic distortion; too slow pumps with the beat. Verify by measurement rather than guesswork: compare level-matched spectra before and after the limiter, and look for new energy at multiples of the low band.

## 7. Measuring again after processing

Once the chain is complete, build a before-and-after table. This is the most valuable part of the report, because it states exactly what mastering traded for what.

| Metric | How to measure | What a change means |
|---|---|---|
| LUFS-I | `analyze_loudness` | Whether the target was reached |
| True peak | `analyze_loudness` | Whether it sits under the ceiling |
| Crest factor and DR | `analyze_dynamics` | **This is the price paid.** Record the number, do not blur it |
| 7-band spectrum | `analyze_frequency_spectrum`, level matched | How much the chain changed tonal balance |
| Correlation and width | `analyze_stereo_field` | Whether stereo processing hurt mono compatibility |
| Clipping | `detect_clipping` | Must be zero |
| Onset count | `analyze_transients` over the same time selection | A sharp drop means the limiter is erasing transients |

The crest row must be reported whether or not anyone asks. A master that hits the loudness target exactly while losing 6 dB of crest is a trade, and the person making that trade should be the user rather than you.

## 8. Bit depth, sample rate, dither

**Sample rate.** Deliver at the mix's sample rate when the target allows. Every conversion is an interpolation, and converting between 44.1 and 48 kHz is not a simple ratio, so it costs more than converting between 48 and 96. If conversion is unavoidable, convert **once**, at the end, with a high quality converter.

**Bit depth.** Process in floating point, deliver at the depth the target requires. 24 bit for distribution; 16 bit only when the target specifically requires it, CD for instance.

**Dither** is only needed when **reducing** bit depth, for example from 32 bit float or 24 bit down to 16 bit. Three rules:

1. **Once only, at the very last step, after the limiter.** Dithering twice adds noise twice for no benefit.
2. **Do not dither when you are not reducing bit depth.** Exporting 24 bit from a 32 bit float chain puts the 24 bit noise floor below anything audible in practice, and the argument about dither there is **Tier 3**.
3. **Noise shaping** moves dither noise into a region where the ear is less sensitive. It helps at 16 bit and is close to irrelevant at 24 bit.

The mechanism of dither, turning quantisation distortion correlated with the signal into uncorrelated noise, is **Tier 1**. Whether it is audible at 24 bit is Tier 3.

## 9. Deliverables

The full delivery set. Ask which items the user needs rather than producing all of them:

**The main master file.** State in the name or in accompanying documentation: sample rate, bit depth, LUFS-I, dBTP, and the delivery target.

**Alternate mixes**, when requested:

- **Instrumental**: all lead and backing vocals removed.
- **TV track**: backing vocals kept, lead removed. Used for television performance.
- **Acapella**: vocals only.
- **Clean**: lyrics unsuitable for broadcast removed or replaced.

All four must be rendered from **the same session in the same state** as the main master. Re-rendering after changing something is the surest way to make the delivery set inconsistent.

**Stems.** `render_stems` exports each track by soloing them in turn, so a stem carries the track's FX but not bus or master processing. If the recipient needs stems that sum back exactly to the mix, state that limitation, or build bus-group stems another way.

**Archiving.** Record: the session version, the plugin list with versions, the delivery target, every before and after measurement, and the brief. A master that cannot be rebuilt six months later is a master that works once.

## 10. Final checks

Run these on the **rendered file**, not on the project. This is the distinction that matters: what you deliver is a file, and only the file can answer whether the file is correct.

1. **Render, then measure from the file.** LUFS-I, dBTP, clipping. A discrepancy between project measurements and file measurements means something in the render chain differs from the playback chain.
2. **Check the head and tail of the file.** Leading silence, start point, final fade, and no stray sounds at either end.
3. **Check the length** against the original mix.
4. **Check mono summing** one last time on the rendered file.
5. **Confirm no clipping** with both `detect_clipping` and the dBTP reading.
6. **Check file names and metadata** against the agreed naming convention.
7. **For each alternate mix**, repeat steps 1 through 5.

One trap is expensive enough to call out separately: **a scripted render can produce a file of the right length that is completely silent**, when media items are offline. How to spot it before wasting a render, and how to handle it, is in `reaper-mcp`, under [Rendering Secrets](../../reaper-mcp/references/rendering.md). Always measure the file after rendering; never assume it has audio.

## 11. Reporting honestly

The final report has four parts, in this order:

1. **What was achieved**, with numbers: LUFS-I, dBTP, crest, against the target.
2. **What it cost**, with numbers: dynamic range lost, spectral balance changes, transient changes.
3. **What was not done and why**: what went back to the mix, which source constraints could not be removed, what was deliberately left alone.
4. **What cannot be verified.** State plainly which part is measurement and which is convention. Never claim anything about how the master sounds.

Then close with **one knob and two directions**, so the user's next listening pass is as cheap as possible:

> If the master sounds closed in and airless, drop the limiter input by 1 dB; LUFS-I lands around -15.2 and crest recovers about 1 dB. If it sounds weak against the reference, push it 1 dB the other way and accept the matching loss of crest.

## 12. Common traps

| Symptom | Cause |
|---|---|
| Clean on the project, clipped in the delivered file | Sample peak measured instead of true peak; measure dBTP on the file |
| Rendered file is the right length but silent | Media items offline; see `reaper-mcp` |
| The limiter meter says under ceiling but the file exceeds it | The limiter is not in true peak mode |
| Master hits the LUFS target but sounds quieter than the reference | LUFS-I is a whole-programme average; a wide dynamic range always sounds quieter at the same LUFS |
| Master EQ improves nothing | The problem is in one element; that is a note for the mix |
| Stereo processing widened the mix but lost bass in mono | Side content widened in the low band; check correlation per band |
| The master chain took the punch out of the mix | The limiter or bus compression is erasing transients; measure onset count again |
| The delivery set is inconsistent | The versions were rendered from different session states |
| A plugin parameter lands on an absurd value | Wrong parameter index; read the formatted value back after setting |
| The master sounds different once on the platform | Platform normalisation and lossy encoding; check dBTP and section 5 |

## 13. Before leaving the session

- Restore the render settings, the time selection, and anything bypassed or set offline.
- Restore solo and mute state, and check the track count against the original record.
- Delete temporary files that are not part of the deliverables.
- **Do not save the project.** Say that you did not save, list what changed during the session, and let the user decide.
