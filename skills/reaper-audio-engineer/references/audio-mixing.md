# Mixing

## 1. Entry condition and scope

This document assumes the handoff from [audio-recording](./audio-recording.md) already exists: the intake record, the source list, measured timing offsets, usable dynamic range, the brief or its assumption list, and the initial session state.

**If it does not, run the recording pass first.** Mixing unvetted material is the surest way to treat a symptom with a tool unrelated to its cause.

The transport layer, running Lua through the file bridge, and the render traps live in `reaper-mcp`. The offline measurement toolkit is in `./audio-measurement.md`. This document decides **what to measure, in what order, and which move the number leads to**.

The three evidence tiers carry over from the recording pass: Tier 1 is specification and arithmetic, Tier 2 is usable reasoning, Tier 3 is trade convention. Every threshold is tagged.

## 2. The basic loop

Every operation in this document runs these five steps. No exceptions.

1. **Hypothesis.** State the problem as a sentence that could be wrong. "Bass and kick are competing in 60-120 Hz" is a hypothesis. "The bass sounds a bit muddy" is not, because you cannot hear.
2. **Predict the measurement.** Write down the number you expect to see after the move, before making it. This step gets skipped most often, and it is what separates method from decoration.
3. **Change one thing.** One logical change at a time, so the user's undo still means something.
4. **Measure again.** Same method, same time window, same solo state. A number taken a different way is not comparable.
5. **Keep or undo.** If the measurement misses the prediction, the hypothesis was wrong rather than the move too small. Undo and diagnose again. Turning the knob further "to be sure" is how sessions get wrecked.

Step 2 is the most expensive and the most valuable. One recorded wrong prediction teaches more than ten correct moves that recorded nothing.

## 3. The level-matching rule

**Every before-and-after comparison must be level matched before you conclude anything.** This is a hard rule, placed here because it applies to every remaining section.

The reason is Tier 1: raising level changes both perceived loudness and the shape of the ear's frequency sensitivity. Any processing that makes a signal louder seems better, even when louder is all it did. For a measuring system the trap is worse: many spectral and crest figures move with level, so an unmatched comparison is wrong systematically rather than randomly.

The procedure:

1. Measure LUFS-I of state A.
2. Measure LUFS-I of state B.
3. Take the difference d = LUFS(B) - LUFS(A).
4. Apply -d to B, and only then compare spectrum, crest and correlation.

Skip step 4 and the conclusion is unusable. When reporting, say that you level matched and give the value of d, because d is information in itself: an EQ that raises LUFS by 3 dB is not a tonal change, it is a level change in disguise.

## 4. Measuring one element: the right way and the cheap way

The most important constraint of the toolset, again: **every `analyze_*` renders the whole project and measures the master output.** There are two ways to measure an element, and they cost very differently.

**The slow way, for a single number:** record the current solo and mute state, `set_track_solo` the track, run the measurement, then restore. Each measurement is a full project render.

**The fast way, for many elements:** run `render_stems` once across every track you need, then measure each file. One render buys the whole table.

Three things to remember about stems:

- A stem carries that track's own FX but **not** bus or master processing, because the tool solos one track at a time.
- To measure raw material, bypass the track's FX before rendering, and restore afterwards.
- A stem is a snapshot. After any large round of changes, the old stem set is no longer comparable.

For measurements needing more than seven bands of resolution, a 1/3 octave resonance sweep or a cross-correlation to find sample offset, use the Lua toolkit in `./audio-measurement.md`.

Two habits keep the numbers honest, both Tier 2:

- **Gate by level before analysing**, so the silence between vocal phrases does not drag the average down.
- **Normalise to the signal's own mean** when comparing spectra, so you read tonal balance rather than level.

## 5. Order of operations

A mix runs through nine gates. Each has an entry condition and a measurement to leave by. **Do not enter a gate before the previous one has passed**, because each gate changes the numbers the next one depends on.

| Gate | Content | Exit condition |
|---|---|---|
| G0 | Session build: routing, buses, naming, gain staging | No track clips; the routing map is written down |
| G1 | Repair: phase, timing, hum, clicks, noise | No sustained negative correlation; hum is gone |
| G2 | Static balance with faders and pan | The relative level table matches the intent within a stated tolerance |
| G3 | Subtractive EQ: remove what you can prove | Every cut traces to a measurement |
| G4 | Compression: control dynamic range | Crest moved by the predicted amount, compared level matched |
| G5 | Additive EQ and saturation: colour on purpose | Every addition traces to a sentence in the brief |
| G6 | Space: delay and reverb through sends | Correlation and mono level did not degrade past tolerance |
| G7 | Automation: movement following song structure | Short-term level swing sits inside the target range |
| G8 | Bus work and the final checklist | The whole list in section 14 has passed |

This order is **Tier 2**. It arranges the work by dependency: each gate needs numbers the previous one produced. If a specific project shows a step is redundant or missing, change it and write down why.

One consequence worth stating plainly: **G1 must finish before G2.** Balancing levels on material that is still out of phase is balancing something about to change. After correct delay compensation, the summed level of a microphone group can move by several dB, and the whole balance table has to be redone.

## 6. G0: session build

Four jobs. None takes long, and all four cost a lot when skipped.

**Naming and colour.** Track names decide stem file names later. Name them before rendering anything.

**Build buses.** Group by source: drums, bass, guitars, keys, backing vocals, effects. The reason is not tidiness: a bus is where shared processing goes, and where a whole group's level can be measured in one move. Use `create_bus`, and record the routing map with `list_tracks` and `list_sends` once it is built.

**Gain staging.** The aim here is not headroom for a floating-point engine, which does not need it (see the recording document, section 6). The aim is to put each track in a sensible operating range for the plugins that will sit on it, and to bring the master sum below the ceiling with room to spare. Measure with `detect_clipping` on the master once every track is up.

**Verify routing by measurement, not by memory.** For each bus, mute all of its sources and confirm the bus level falls silent. Wrong routing causes problems that look like audio faults and are not.

## 7. G1: repair

This gate has the highest value-to-effort ratio in the whole mix, and it is the one most often skipped because it is not fun.

**Polarity and delay.** Take the timing offsets from the intake record. For each pair of mics on one source:

1. Compensate delay by the measured number of samples.
2. Measure correlation again with `analyze_stereo_field`, with exactly that pair soloed.
3. Measure the pair's summed level against each track alone.

Correlation moving from negative to positive, with the summed level rising, means the compensation was right. Correlation still negative after delay compensation means the problem is polarity rather than timing; flip polarity and measure again. If neither rescues it, it is a mic placement fault from the recording and no knob fixes it. Put it in the report.

**Hum and buzz.** Narrow peaks at the mains frequency and its harmonics. Locate them with a 1/3 octave sweep, treat them with narrow notches at those exact frequencies and at whichever harmonics carry meaningful energy. Notch before anything else, because a compressor placed after hum will respond to the hum.

**Broadband noise and bleed.** Only treat it when a measurement proves it is a constraint, meaning the usable dynamic range in the intake record is narrow enough that automation will expose the floor. A gate is an expensive tool: it also cuts the natural tail of the source. Measure before placing it, and measure after by comparing the level of a silent passage before and after.

**Clicks, pops, DC offset.** Fix these at item level, not with a plugin on the track.

**Exit condition for G1:** no sustained negative correlation in the bass between mics on one source, and the hum peaks are down at the noise floor.

## 8. G2: static balance

Static balance is the mix. Everything else is refinement. If the level table is wrong, no EQ rescues it.

**Choose the measurement window.** A passage where all the main elements play, usually a chorus, long enough for the average to mean something. Record where that window is and use exactly it for every measurement in this gate. Changing the window partway destroys comparability.

**Build the level table.** Run `render_stems` once, measure LUFS-I or RMS of each stem over the chosen window, then express everything as a difference from the central element. The central element comes from the brief: usually the vocal, but not for music built around the groove.

**Check against the intent.** This is where the brief becomes usable. A sentence like "built around the vocal, close and dry" translates into a target range of differences. If a reference file exists, measure it the same way and compare, after level matching.

No table of numbers here is a standard. The figures passed around about "how many dB the vocal sits above the music" are **Tier 3** and vary by genre enough to carry no information. What is usable is the frame of reference: measure a reference from that same genre, and state what you are comparing against.

**Pan.** Set it with `set_track_pan`. Once set, check the mono sum: measure correlation, and measure the level of the mono fold against the stereo version. Wide panning improves the stereo image and costs energy when summed to mono; that budget has to be spent deliberately.

**Exit condition for G2:** the relative level table matches the intent within a tolerance you stated yourself, and that tolerance is written down.

## 9. G3: subtractive EQ

**The rule: cut what you can prove, add what you intend.** G3 does only the first half. The second belongs to G5.

**Find resonances.** The seven bands of `analyze_frequency_spectrum` are not enough; they show overall balance and cannot locate a narrow peak. Use a 1/3 octave sweep through the file bridge:

1. Gate by level to drop the silences.
2. Normalise to the signal's own mean, so you read shape rather than level.
3. Look for peaks rising above the local trend. A peak a few dB up over a width narrower than 1/3 octave is a resonance candidate.

**Place the cut.** Frequency comes from the measured peak. Q comes from the peak's width: a narrow peak needs high Q, a broad one low Q. Start the cut at roughly half to two thirds of the measured excess rather than all of it (Tier 3: a resonance is also part of the source's character, and flattening it completely usually takes the character with it).

After cutting, sweep again and confirm the peak fell by the predicted amount. If it fell further than predicted, Q is too low and you just removed the neighbourhood too.

**A high-pass filter is not a default.** This is where received wisdom does real damage. Before putting an HPF on a track:

1. Identify the lowest fundamental the source actually plays.
2. Measure the energy below that frequency.
3. If the energy down there is already far below the band's peak, the HPF cleans nothing and still adds phase shift in the neighbourhood.

An HPF is worth placing when a measurement shows real energy below the source's working range: floor rumble, wind, handling noise. Placing one because "every track should have an HPF" is not.

**Masking between two elements.** When two sources compete over a frequency region, the measurement procedure (Tier 2):

1. Measure the spectrum of A alone and B alone, each normalised to its own mean.
2. Find bands where both carry high energy relative to their own shape.
3. In those bands, the brief decides who takes priority. Cut the other one, do not boost the priority one.

Why cut rather than boost: boosting raises total energy in an already crowded band, making the problem worse and then compensating with level. Cutting solves it where it lives.

**Exit condition for G3:** every cut on every track traces to a specific measurement. If you cannot name the measurement, take it out.

## 10. G4: compression

The compressor is the tool most often used by formula. All four main parameters can be derived from measurements.

**Before placing it: measure crest factor** on a representative window, and **decide how much crest you want left**. That number is the budget for the whole gate. Without a budget there is no way to know whether you have compressed enough.

**Threshold** follows from the level distribution: set it so gain reduction happens over the proportion of material you intend, rather than "on the peaks". A threshold that keeps the compressor working continuously gives a completely different effect from one catching only the highest peaks, and both are right in their own context.

**Ratio** follows from the reduction you want at the highest peak: for a signal x dB over threshold, the reduction is x times (1 - 1/ratio). This is Tier 1, it is the definition of ratio.

**Attack** follows from your intent for the transient. A fast attack catches the transient, reduces crest more, and softens the hit. A slow attack lets the transient through, keeps the hit, and reduces crest less. There is no correct value; there is a choice you must be able to justify.

**Release** follows from the material's tempo. Use `analyze_transients` on the soloed source to get onset times, take the median spacing between them, and set release so the compressor recovers before the next hit on rhythmic material. A release longer than that spacing makes the compressor overlap between notes and pump with the beat. (Tier 2, derived from how an envelope follower works.)

**Verify after placing it, all four checks:**

1. Crest before and after, against the budget you set.
2. LUFS-I before and after, to know the makeup gain needed.
3. Spectrum before and after, **level matched**. Compression always changes spectral balance, and that change must be something you accepted rather than something you did not notice.
4. On rhythmic material, measure transients again after compression and compare the number of detected onsets. A sharp drop means the attack is erasing the hit.

**If you need more than about 6 dB of reduction to hit the budget**, stop. That figure is Tier 3, but the reasoning is not: reduction that large usually means the problem is in the static balance or in the source material, and compression is covering something that automation or clip gain solves more cleanly.

**Exit condition for G4:** crest moved by the predicted amount, and a level-matched spectral comparison exists.

## 11. G5: additive EQ and saturation

This gate differs from every other one in a single respect: **it fixes nothing.** It creates colour. So it has its own rule:

**Every move in G5 must trace to a sentence in the brief.** If no sentence in the brief justifies it, take it out. This is not formality: a system that cannot hear and is free to add frequencies will drift away from the intent with no mechanism to notice.

How to work:

- Add with low Q and small amounts. Narrow, strong boosts are repair work, and repair cuts rather than boosts.
- Saturation adds harmonics. Measure it by comparing level-matched spectra before and after: new energy appearing at multiples of the original region shows it is working. Measure crest too, since most saturators also reduce crest.
- After each move, measure stereo correlation again if the processing is not symmetric across the two channels.

**Exit condition for G5:** a list of moves, each paired with the sentence in the brief that justifies it.

## 12. G6: space

**Use sends, not inserts.** Two reasons, both Tier 2: the dry signal stays intact so the wet/dry ratio stays independently adjustable, and several sources sharing one space sound like they are in the same place. Build it with `create_send` to an effects bus.

**Pre-delay** sets the distance between source and space. Deriving it from tempo: one beat is 60000 divided by BPM in ms, and pre-delay usually takes a small fraction of that so the early reflections do not land between notes. This is **Tier 3**, a way of choosing rather than a law.

**Reverb time** has to be compared against the song's tempo. A tail longer than the spacing between notes will overlap the next one. On clearly rhythmic material, use the median onset spacing from `analyze_transients` as the reference. (Tier 2.)

**Cut the low end on the send.** Low frequency energy fed into a reverb fills the space with something hard to localise and degrades mono compatibility. This is verifiable: measure correlation and the mono fold level before and after enabling the send.

**Three mandatory measurements after adding space:**

1. L/R correlation before and after. A drop is normal; going negative is not.
2. The mono fold level before and after. A large drop means the reverb is cancelling when summed.
3. Bass band energy before and after, level matched.

**Exit condition for G6:** those three numbers sit inside a tolerance you stated, and the tolerance is written down.

## 13. G7: automation

Automation is where the mix gains movement that follows the song. For a measuring system it is also the easiest gate to get right, because it derives entirely from data.

**Map the structure first.** Measure RMS per bar at low resolution to get the shape of intro, verse, chorus. Without that map there is no way to know whether a section is louder or quieter than its counterpart.

**Ride from measurements.** The procedure for one track:

1. Measure short-term level over a sliding window across the whole track.
2. Define the target line, usually flat within a section and stepping between sections.
3. Write envelope points with `add_volume_automation` for the difference between measured level and the target line.
4. Limit the rate of change. An envelope that moves too fast reads as modulation rather than balance.

The implementation is in `./audio-measurement.md`.

**Macro automation.** At bus and master level, automation serves song structure rather than individual notes. This is where measurement stops and the brief decides: the level difference between verse and chorus is a storytelling choice, not a measurable target.

**Exit condition for G7:** the swing of short-term level sits inside the stated target range, and the steps between sections match the structure map.

## 14. G8: buses, and the final checklist

Processing on buses and on the master during the mix is legitimate, with one constraint: **if you put processing on the master during the mix, say so at handoff**, because mastering needs to know what it received.

The list below must pass before leaving the mix. Every item is a measurement, not an impression.

| Item | How to measure | Threshold |
|---|---|---|
| No clipping anywhere | `detect_clipping` on the master and on each bus | 0 samples at 0 dBFS |
| Headroom for mastering | `analyze_loudness` | leave the true peak around -6 dBTP (Tier 3, this is a handoff convention) |
| Mono summing keeps the low end | correlation and mono fold level | no sustained negative correlation; no unusual drop in mono level |
| Spectral balance against the reference | `analyze_frequency_spectrum` on both, level matched | deviation inside the tolerance stated at G2 |
| Remaining dynamic range | `analyze_dynamics` | crest inside a sensible range for the genre; record the number |
| No leftover solo or mute | `list_tracks` | matches the initial state in the intake record |
| Track count matches intake | `list_tracks` | no temporary analysis tracks left |

The reporting rule: **no warnings is a stronger statement than pages of numbers.** Run this list and report the items that failed, rather than printing everything.

## 15. What measurement does not decide

Worth saying plainly, because this is the real boundary of the method in this document.

Measurement finds resonances, level errors, phase errors, buried elements, gain staging mistakes, frequency masking, wrong dynamic range. It does **not** tell you whether the mix is good, whether it carries the song's emotion, which phrase a performance should rise on, or whether a colour decision serves the song.

Three things to do at that boundary:

1. **Say which part was measured and which part is convention.** Do not let a Tier 3 choice look like a measured result.
2. **State what you deliberately left alone, and why.** A list of places considered and left is worth as much as the list of places changed.
3. **Hand back one knob and two directions.** This is the cheapest interface between a measuring system and a person with ears. Instead of a long description, give exactly one parameter and two directions with their consequences:

> If the vocal still gets buried in the chorus, pull the instrument bus automation from -1.5 down to -2.5 dB. If the vocal has separated too far from the music, push it back up to -0.5.

That form turns one listening pass by the user into a decision, rather than into a description that needs reinterpreting.

## 16. When the user gives a listening note

A listening note is the most valuable data in the session, because it comes from a sense you do not have. How to handle it:

1. **Translate it into a testable hypothesis.** "Nasal" is a measurable claim: sweep 1/3 octave and look for a peak around 1 kHz with a dip through 2-4 kHz. "Muddy" usually reduces to excess energy at 200-500 Hz relative to the reference. "Thin" usually reduces to missing energy around the source's fundamental.
2. **Go find it in the data.** If you find it, fix it and report both the measurement and the move.
3. **If you cannot find it, say so.** Not finding it does not mean the user is wrong; it means the phenomenon is outside what you can measure. State what you measured, what you did not see, and propose one change for them to listen to.

Never agree with a listening note and then act on it without measuring. That is the moment a system with no ears starts inventing.

## 17. Common traps

| Symptom | Cause |
|---|---|
| Every change seems like an improvement | No level matching before comparing |
| The measurement misses the prediction, and turning further makes it worse | The hypothesis was wrong. Undo and diagnose again rather than turning further |
| Static balance finished, then had to be redone entirely | G1 was not finished before G2; delay compensation moved the group levels |
| EQ cut by the numbers but the source lost its character | Q too low, the cut spread into the neighbourhood. Sweep again and check the width |
| The compressor pumps with the beat | Release longer than the spacing between onsets |
| Reverb loses the bass when summed to mono | No low cut on the send |
| A stem measures nothing like it sounds in the mix | Stems carry no bus or master processing |
| Two measurements of the same thing disagree | Different time window, different solo state, or a stale stem set |
| The mix does not clip but the render distorts | Rendering to an integer format; check true peak, not just sample peak |
| Plugin bypassed and the latency did not drop | Bypass preserves PDC; you have to offline it |

## 18. Handoff to mastering

[audio-mastering](./audio-mastering.md) needs exactly seven things:

1. **The mix file**, with its sample rate, bit depth and format stated.
2. **Measured LUFS-I and dBTP** of that file.
3. **Measured crest factor and DR.**
4. **L/R correlation and the mono summing result.**
5. **The list of processing currently on the master and on the buses**, in particular whether a limiter is present. If there is one, say so.
6. **The brief**, including the delivery target.
7. **The list of things deliberately left alone**, and the source limitations found during intake that the mix could not remove.

Item 7 matters more than it looks. It stops mastering from trying to fix, with a broad tool, something that was a constraint of the source material. That is the fastest way to ruin a master.

## 19. Before leaving the session

- Restore every solo, mute and bypass exactly as the intake record describes.
- Delete any temporary analysis tracks and check the track count.
- Restore the time selection and the render settings.
- Delete temporary stem files if they are not part of the deliverables.
- **Do not save the project.** Say that you did not save and let the user decide.
