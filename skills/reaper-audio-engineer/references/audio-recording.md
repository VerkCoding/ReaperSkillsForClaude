# Recording and intake

## 1. Where this document sits

Three documents run in sequence. Each has an entry condition and a written handoff:

| Document | Answers | Hands off |
|---|---|---|
| **audio-recording** (this one) | Can this material be mixed, and what is the mix aiming at? | Intake record and brief |
| **[audio-mixing](./audio-mixing.md)** | Given that material, how is the mix built? | A mix plus its headroom spec |
| **[audio-mastering](./audio-mastering.md)** | How does that mix become a deliverable? | Master file and deliverable set |

This document does not repeat the transport layer. Calling REAPER, running Lua through the file bridge, reading samples, and avoiding a silent render all live in the `reaper-mcp` skill. Read that when you need to operate something. Read this when you need to know **what to measure and what the number means**.

### Three evidence tiers

- **Tier 1**: published specifications, industry standards, verifiable arithmetic, repeatable measurements.
- **Tier 2**: reasoning built on Tier 1. Most of the diagnostic procedure here is this tier. Nobody proved these; they are simply usable.
- **Tier 3**: trade convention, numbers passed around the industry, personal taste.

Every numeric threshold below carries its tier. A Tier 3 threshold is a starting point, not a pass/fail standard.

## 2. First principles

**Mix quality is capped by source quality.** This is a constraint, not advice. Four kinds of damage survive any mix move: real clipping already written into the file, a noise floor above the quietest musical passage, phase broken at the moment of recording, and a bad performance. Catching them at intake costs minutes. Catching them at mastering costs the session.

**You do not have ears.** Three consequences, all mandatory:

1. Never write that you heard something. Write what you measured.
2. Every conclusion traces back to a specific measurement, along with how it was taken.
3. When the user offers a listening note, that is the most valuable data in the session. Do not nod along. Go find it in the numbers.

**Diagnose first, treat second.** Open no plugin before you know what you are treating. An EQ placed because "vocals usually need it" is decoration, not engineering.

## 3. What the measurement tools actually do

This section matters more than it looks. The `analyze_*` tools share a property that leads straight to wrong conclusions:

**Every `analyze_*` tool renders the whole project and measures the master output.** None of them measures a single track. Consequences:

- To measure one source: `set_track_solo` on it, measure, then unsolo. Record the original solo and mute state before touching anything, and put it back exactly.
- Or `render_stems` and measure each file. Note that this tool solos one track at a time, so a stem carries that track's own FX but **not** bus or master processing.
- Numbers are always post-FX and post-fader. To measure raw material, bypass the track's FX first, and restore afterwards.

Per-tool limits:

| Tool | Gives | Limit worth knowing |
|---|---|---|
| `analyze_loudness` | LUFS-I and dBTP per ITU-R BS.1770 | A whole-programme number. Says nothing about which section is loud. |
| `analyze_frequency_spectrum` | RMS across 7 fixed bands | Too coarse to find a narrow resonance. Use it for overall balance, never to pick an EQ frequency. |
| `analyze_dynamics` | RMS, peak, crest factor, a reduced DR figure | Crest depends on the measurement window. Comparisons only mean something when the window matches. |
| `analyze_stereo_field` | mid/side balance, width, L/R correlation | Averaged over the whole programme. A short out-of-phase passage can be averaged away. |
| `analyze_transients` | up to 100 onsets | Hard ceiling of 100. For a full song, measure over a time selection. |
| `detect_clipping` | samples at or over 0 dBFS | Sample peak, not true peak. Blind to inter-sample peaks. |

When you need real frequency resolution, a 1/3 octave resonance sweep for instance, use the Lua toolkit in `./audio-measurement.md`. Seven bands cannot locate a resonance peak.

## 4. The intake record

Run all eight checks below before anything else. Write the result as a table, one row per source, with a status column: **pass**, **warning**, **blocker**.

### 4.1 Technical inventory

Read `get_project_info` and `list_tracks`. For each source file, record sample rate, bit depth, channel count and length.

Three things must agree across the session: sample rate, start point, and speed. A file at the wrong sample rate that the DAW silently converts will play at the right pitch through a sample rate conversion nobody asked for. A file with the wrong start point looks right on screen and is wrong in the audio.

How to spot a misread sample rate: the length is off by exactly the ratio. A 44.1 kHz file read as 48 kHz is about 8.8 percent shorter and about 1.4 semitones sharp. (Tier 1, this is division.)

### 4.2 Clipping

`detect_clipping` with each source soloed, or on each rendered stem.

Read the result in two layers:

- **Zero samples at 0 dBFS**: proves nothing yet. The file may have clipped at the preamp or the converter and then been turned down, in which case the peak sits below 0 while the waveform is already flat-topped.
- **More than zero samples at 0 dBFS**: digital clipping is present. Count consecutive samples. A few isolated ones are usually harmless; runs of tens of samples are flat-topped waveform and do not come back.

To catch clipping that was turned down afterwards, compare crest factor against the typical range for that source type (section 4.5). A kick drum with 6 dB of crest was almost certainly clipped somewhere before it reached the file. (Tier 2.)

### 4.3 Peak and true peak

`analyze_loudness` gives dBTP. On raw material this is not a pass/fail criterion. It is input to the headroom budget.

Worth stating: **true peak above sample peak is normal and not a fault.** A large gap, a few dB, tells you the signal carries a lot of high frequency content and will need more care at the final limiter. (Tier 1 for the definition, Tier 2 for reading it.)

### 4.4 Noise floor

Pick a window where the source is not playing, set a time selection, and measure. The number you want is the RMS of that silent window.

The usable dynamic range of a source is playing RMS minus silent RMS. This number decides what compression and automation are allowed to do: every move that lifts the quiet parts lifts the noise floor with them, and that ratio does not change.

Orienting thresholds (Tier 3, depends on genre and arrangement):

- Above 60 dB: the noise floor is barely a consideration.
- 40 to 60 dB: usable, but aggressive upward automation will expose the floor.
- Below 40 dB: the noise floor is a real constraint. Tell the user before mixing, rather than letting it surface at mastering.

Classify the noise too, because the treatments differ: broadband hiss from a preamp, sharp peaks at the mains frequency and its harmonics (hum, 50 or 60 Hz), or bleed from another source. `analyze_frequency_spectrum` on the silent window separates the second group, because it concentrates energy in the bass bands.

### 4.5 Crest factor

`analyze_dynamics` on each soloed source, measured over a representative passage rather than the whole song.

The ranges below are **Tier 3**. They are orienting numbers that swing widely with microphone, distance, room and performance. Use them to spot anomalies, never to grade:

| Source | Typical crest | Lower means | Higher means |
|---|---|---|---|
| Close-miked kick, snare | 15-20 dB | compressed or clipped before the file | distant miking, or an overzealous gate |
| Overheads, room | 12-18 dB | already compressed | dead room, or close placement |
| Sustained vocal | 10-15 dB | compressed on the way in | very wide performance range, needs heavy automation |
| Bass DI | 8-14 dB | compressed or limited | uneven picking |
| Amplified guitar | 8-14 dB | the amp compresses by nature | recorded direct, no amp |
| Unmastered mix | 12-18 dB | a limiter is already on the master | sparse arrangement |

A measurement outside the range is a question to answer, not a fault to fix. Log it with a hypothesis.

### 4.6 Phase correlation between microphones on one source

Applies wherever one source was captured by more than one microphone: a drum kit, a guitar with two mics, DI plus amp, an overhead pair.

Solo exactly the two tracks in question, run `analyze_stereo_field`, and read L/R correlation:

- Near **+1**: the two signals are nearly identical. For a stereo pair that means a narrow image; for two mics on one source it is usually normal.
- Near **0**: uncorrelated. For an overhead pair this is a wide image and fine.
- **Negative**: the signals cancel. Stop here. Sustained negative correlation in the bass means the mix loses its low end when summed to mono.

Mind the tool's limit: the number is an average over the passage. A short out-of-phase section can be averaged away. When in doubt, measure several short time selections instead of one pass over the whole song.

A cheap and effective cross-check: measure the combined level with both tracks up, then compare against each track alone. Two in-phase signals sum toward +6 dB; two out-of-phase signals produce a level below either one alone. (Tier 1, this is amplitude addition.)

### 4.7 Timing offset

When two microphones capture one source at different distances, they are offset by the path difference divided by the speed of sound. At 20 degrees C sound travels about 343 m per second, so **1 ms corresponds to about 34.3 cm**. (Tier 1.)

That offset produces comb filtering when the two are summed: for delay t, the nulls sit at f = (2n+1) / (2t). A 1 ms offset puts the first null at 500 Hz. (Tier 1.)

How to measure the offset without listening: run `analyze_transients` on each soloed track and compare the onset time of the same hit across the two. The difference is the offset to compensate. For finer resolution, run a cross-correlation over the samples through the file bridge; the method is in the Lua toolkit.

Time alignment itself belongs to [audio-mixing](./audio-mixing.md), in the repair section. Here you only measure it and write the number down.

### 4.8 DC offset, bleed, head and tail silence

Three remaining checks, one line each:

- **DC offset**: a sample mean meaningfully away from zero. It eats headroom and makes a limiter work asymmetrically. Measure through the file bridge; no MCP tool covers it.
- **Bleed**: measure a track's level during the passages where its own instrument is not playing. Bleed is not a fault, but it decides whether a gate is usable at all, and it limits EQ freedom, because EQ on a track also EQs the bleed.
- **Head and tail silence**: record it, because it affects the start point and the fades at delivery.

### 4.9 Record format

Output a table, one row per source. The last column is a verdict, one of three: **pass**, **warning with the concrete consequence**, **blocker with the reason and the action needed**.

One presentation rule: **do not list a number that does not lead to a decision.** A three line record of warnings is worth more than three pages of unremarkable measurements.

## 5. Fixable and not fixable

This table exists so you can say no. Most of the time wasted in a session goes into treating something that should have been sent back. (Whole table is Tier 2.)

| Measured symptom | Fixable in the mix? | The right move |
|---|---|---|
| A few isolated samples at 0 dBFS | Yes | Lower clip gain, note it, move on |
| Long runs of flat-topped samples | No | Report it and ask for a re-record or another take |
| Unusually low crest on a close-miked source | Partly | The material arrived compressed. More compression will clamp it further. Reset expectations. |
| Usable dynamic range below 40 dB | Partly | Say up front that upward automation will expose the noise floor |
| Hum at the mains frequency and its harmonics | Yes | Narrow notch in the repair pass, before anything else |
| Persistent negative correlation in the bass | Usually | Flip polarity or compensate delay. If it stays negative, it is a mic placement fault and does not fix. |
| Timing offset between mics on one source | Yes | Compensate by the number measured in 4.7 |
| Wrong pitch or timing in the performance | Out of scope | This is a human decision, not an algorithmic one. Ask. |
| A source that is not what it claims, for instance a kick with its energy at 3 kHz and 35 dB of crest | No | That is a trigger click, not a drum. Say so plainly and describe the alternative. |

The last row is the general rule: **when a measurement proves something cannot be fixed with a knob, say so rather than quietly pretending to have treated it.**

## 6. Gain staging while recording, in a floating-point engine

This is where received wisdom and technical reality diverge most, so it needs stating precisely.

**What is unlimited is not the problem.** REAPER's mix engine runs in floating point. A channel exceeding 0 dBFS inside the engine is not destroyed; pull the fader down and the signal returns intact. The representable range of 32 bit float is wider than any musical situation. (Tier 1.)

**What is limited is the problem.** Three places still clip for real:

1. **Preamp and converter.** These are analog and conversion circuits with hard ceilings. No file format rescues a signal that clipped before it became numbers. A 32 bit float recorder is no exception: the float part sits after the ADC, and the ADC and preamp still have ceilings.
2. **Fixed-point plugins, and plugins modelling analog gear.** Many of these have a calibrated operating point around the equivalent of 0 VU, and feeding them much hotter produces distortion you did not choose.
3. **Rendering to an integer format.** This is where everything above 0 dBFS clips for real.

**The practical conclusion**: the target level while recording is not about preserving headroom in the file, it is about placing the signal in the good operating range of the analog hardware. The number usually quoted is a peak around -18 to -12 dBFS, and it descends from calibration standards that place the analog reference at -18 or -20 dBFS. The calibration standard itself is Tier 1; "record at -18" as a rule of thumb is **Tier 3**, and it moves with how a specific device is calibrated.

What is actually worth doing, and this is Tier 1: **measure, do not guess.** Have the source play its loudest passage, read the peak, and set gain so the loudest part still clears 0 dBFS by enough for one surprise.

## 7. When you are actually driving the recording

If the session really uses `start_recording`, run this list first, because each item is a familiar way to lose a take:

1. **Signal path**: confirm the track input points at the right port and the track is record armed. Check with `get_track_info`, not by assumption.
2. **Project sample rate and bit depth** already match the final delivery target. Changing after recording adds an unnecessary conversion.
3. **No track left soloed** from an earlier session. This is the number one cause of "everything is silent".
4. **Monitoring latency**: the threshold at which performers stop being able to play is usually quoted around 10 ms. That number is **Tier 3**, trade convention rather than established research, and a drummer is far more sensitive than a pad player. If the monitoring chain carries plugins with large compensation, bypass or offline them before recording. Bypass keeps the latency; only offlining gives it back.
5. **Disk space and take names**: name the tracks before recording, because file names come from track names.
6. **Record a short test** and run 4.2 through 4.5 on it. Catching a gain error after ten seconds is cheaper than after three hours.

Afterwards, run the full intake record on the material you just recorded. Material you recorded yourself is not exempt.

## 8. The brief: the one thing measurement cannot replace

No mix is right in the absolute. A mix is right for an intention. This is a real constraint on the work here: **without a stated intention, every choice you make is just a default.**

Four things are needed before mixing starts:

1. **Delivery target.** Streaming, broadcast, film, club, or an internal reference. It sets the loudness target and true peak ceiling in [audio-mastering](./audio-mastering.md).
2. **One or two reference tracks.** Not to imitate, but to have a measurable frame. With a reference file, every statement about spectral balance turns from an opinion into a comparison.
3. **Tone and manner, stated in one sentence.** Close and dry, or distant and wide. Clean, or deliberately dirty. Built around the vocal, or around the groove.
4. **Hard constraints.** For example: keep the performance as is, no pitch correction, keep the length, must survive mono summing.

**When the brief is missing, do not stop and wait.** Write down the assumptions you are working under, carry on, and put that list at the top of the handoff. A written assumption is corrected in one sentence; an unstated one costs a remix.

Reasonable defaults with no information at all (Tier 3, and label them as defaults): streaming target, must sum to mono, performance untouched, length unchanged.

## 9. Handoff to mixing

[audio-mixing](./audio-mixing.md) needs exactly six things. If one is missing, say it is missing rather than leaving it blank:

1. **The intake table**, including every warning and blocker.
2. **The source list** with classification and grouping, meaning which mics belong to one source.
3. **Measured timing offsets** between mics on one source, in samples and in ms.
4. **Usable dynamic range** per source, and the list of sources with a meaningful noise floor.
5. **The brief**, or the assumption list standing in for it.
6. **Initial session state**: track count, which tracks were soloed or muted, which FX were enabled. This is the baseline for restoring the session.

## 10. Common traps

| Symptom | Cause |
|---|---|
| Every source measures the same loudness | Measuring the whole project instead of soloing each track |
| Soloing a track changes nothing in the numbers | Another track was already soloed; solo is not exclusive |
| Good numbers but the render is silent | Media items are offline; see `reaper-mcp` |
| The same source gives two different crest figures | The two measurements used different time windows |
| Phase correlation looks fine but mono summing still loses bass | The correlation figure is a whole-song average; measure per section |
| Plugin bypassed but the latency is still there | Bypass preserves PDC; you have to offline it |
| Noise floor measures implausibly low | The measurement window landed on a passage edited to digital silence |

## 11. Before leaving the session

- Restore every solo, mute and bypass exactly as received.
- Delete any temporary analysis tracks. Compare the track count against the number recorded in section 9.6.
- Restore the time selection and the render settings.
- **Do not save the project.** The user's session is often in an unsaved state, and if a plugin failed to load during this session, saving makes that damage permanent. Say that you did not save and let them decide.
