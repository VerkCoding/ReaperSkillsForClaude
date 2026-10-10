# Placing parts from measurements

Measured 2026-10-10 on a real mix: OurGoodWolf (indie folk rock, 48 tracks, 25 sources, 260 s, REAPER 7.82), panned from an all-centred state.

> **In this skill:** this document holds the measurements that decide a layout before anything is written: which tracks are stereo, where the drum mics stood, what each vocal part sings, and a prediction of the left/right balance from one stem render. The rules they serve are in [SKILL.md](../SKILL.md); levels and loudness targets follow [gain-staging.md](../../reaper-audio-engineer/references/topics/gain-staging.md).

## Contents

- [1. Which tracks are stereo](#1-which-tracks-are-stereo)
- [2. Where the drum mics stood](#2-where-the-drum-mics-stood)
- [3. What each vocal part sings](#3-what-each-vocal-part-sings)
- [4. Predicting a layout before writing it](#4-predicting-a-layout-before-writing-it)
- [5. Loudness and width after panning](#5-loudness-and-width-after-panning)
- [Applying in this plugin](#applying-in-this-plugin)

| Step | Price | Payoff | Stability |
| --- | --- | --- | --- |
| 1 Source channels, media online | one bridge read, 0.2 s | The tracks that need stereo pan mode are known before the first pan | Read only; 40101 adds no undo point and leaves the dirty flag alone |
| 2 Drum mic geometry, GCC-PHAT | about 1 min of Python on the source files | Room mics go to the side they stood on, consistent with the close mics | Read only |
| 3 Vocal roles from pitch | about 1 min of Python | Doubles, octaves and harmonies told apart without listening | Read only; octave errors show up as ±12 and are easy to spot |
| 4 Prediction from one stem render | 70-77 s render for 48 tracks, about 6 min of statistics, then seconds per layout | Ten layouts compared with nothing written to the project | Broadband balance per section predicted within 0.4 dB of the render after the change |

## 1. Which tracks are stereo

`list_tracks` reports pan, mode, width and pan law, not how many channels the items carry. The bridge reads that with `GetMediaSourceNumChannels`, **but only while the media is online**. With REAPER in the background (`offlineinact` on), every one of the 25 sources read 1 channel, sample rate 0 and length 0. After action 40101 in the same command, two of them read 2 channels: an electric guitar and a piano. Read before 40101, both would have been panned in balance mode, which removes the opposite channel ([Stereo tracks](../SKILL.md#stereo-tracks-the-default-mode-drops-a-channel)).

```lua
reaper.Main_OnCommand(40101, 0)
local o = {}
for i = 0, reaper.CountTracks(0) - 1 do
  local tr = reaper.GetTrack(0, i)
  for k = 0, reaper.CountTrackMediaItems(tr) - 1 do
    local src = reaper.GetMediaItemTake_Source(reaper.GetActiveTake(reaper.GetTrackMediaItem(tr, k)))
    o[#o+1] = i .. " ch=" .. reaper.GetMediaSourceNumChannels(src) .. " sr=" .. reaper.GetMediaSourceSampleRate(src)
  end
end
return table.concat(o, "\n")
```

A reading of `sr=0` means the source is still offline and its channel count is not real.

Then measure what the stereo is, per band, from the source file: level of each channel, correlation between them, and the mono fold loss.

| Source | Correlation L/R (150-500 / 500-2k / 2-6k Hz) | Level L minus R | Mono fold loss | What it is | Placement chosen |
| --- | --- | --- | --- | --- | --- |
| Electric guitar 1 | +0.89 / +0.97 / +0.98 | within 1.3 dB in every band | 0.5 dB | Two near-identical channels: a point source | Stereo mode, width 0.3, pan −0.75 |
| Piano | −0.04 / +0.36 / −0.06 | right louder by 2.0-3.3 dB from 40 Hz to 2 kHz, left louder by 1.6-3.7 dB above 2 kHz | 1.8 dB | A real stereo pair, low strings in the right channel | Stereo mode, width 0.6, pan −0.3: the bass side faces the centre |

A stereo source whose bass sits in one channel is better placed on the opposite side of the centre, so its low strings point inwards. In the simulation of section 4 the same piano panned to the right left the 500-2k band 1.9 dB heavier on that side; panned to the left the band was within 0.8 dB.

## 2. Where the drum mics stood

With one mono overhead, the kit's image comes from the close mics and the room mics, so the rooms decide whether the kit holds one perspective (rule 6). The positions can be measured from the arrival time of each drum in each distant mic.

**Plain cross-correlation does not work here.** Over 80 s of playing, the correlation peaks between a close mic and a room mic were 0.04-0.75, and the lags disagreed between drums in the same mic (room 2: 42.8 ms from the kick, 56.2 ms from the snare). The reverberant field and the repeating pattern dominate.

**GCC-PHAT on single hits does.** The generalised cross-correlation with phase transform (Knapp and Carter, 1976) whitens the spectrum, so the direct path wins over reflections. Method: find the hits in the close mic (frame RMS above the 99.5th percentile minus 18 dB, at least 250 ms apart), take an 80 ms window from 5 ms before each hit in both mics, band-limit both to the drum (kick 60-2,000 Hz, snare 200-8,000, hi-hat 5-16 kHz, ride 3-12 kHz), and add the GCC-PHAT curves of 350-500 hits. Every peak stood 6-16 times above the mean of the curve.

| Mic | Kick | Snare | Hi-hat | Ride | Hi-hat minus ride | Reading |
| --- | --- | --- | --- | --- | --- | --- |
| Overhead | 5.83 ms | 1.77 | 2.97 | 0.50 | +2.47 | Hangs over the ride |
| Room 1 | 4.76 | 3.13 | 4.56 | 3.67 | +0.89 | Close, slightly to the ride side; dark, and 8-9 dB louder than each other room |
| Room 2 | 6.49 | 6.92 | 6.01 | 5.92 | +0.09 | About 2 m in front, on the kit's axis |
| Room 3 | 3.76 | 5.99 | 5.06 | 2.49 | +2.57 | Close, on the ride side |
| Room 4 | 9.61 | 12.70 | 13.08 | 8.84 | +4.24 | Far, beyond the ride |

Lag times 0.343 is metres. The hi-hat lag minus the ride lag says which side a mic is on: positive means nearer the ride.

- **Pairs.** Rooms 2 and 4 matched within 1-2 dB in every band from 40 Hz to 16 kHz: a matched pair of the same model. They were panned as a pair, hard left and right.
- **Side within a pair.** The mic nearer the hi-hat goes to the hi-hat's side. By the precedence effect (Wallach, Newman and Rosenzweig, 1949; Haas, 1951) the earlier arrival decides where a sound is heard within roughly 1-30 ms, so the hi-hat (7 ms earlier in room 2 than in room 4) and the snare (5.8 ms earlier) then sit on the hi-hat's side in the rooms as well, as the close mics place them. The ride arrived earlier in room 2 too, but only by 2.9 ms, the smallest difference.
- **The dominant room in the centre.** Room 1 carried 2.2% of the mix's power against 0.5-0.6% for each other room. Panned off centre it would have pulled the whole room image to one side; it stayed at 0 as the kit's mono room.
- **Low end.** All four rooms together held under 0.4% of the mix's power below 120 Hz (kick 62%, bass 34%). Panning them wide added no stereo low end: side minus mid below 120 Hz went from −24.5 to −26.4 dB, so no mono low-end filter was needed.

## 3. What each vocal part sings

Pitch tracking tells a double from an octave or a harmony. Method: YIN on each vocal track (11 kHz, 1,024-sample frames, 20 ms hop, threshold 0.2), then the interval in semitones between two tracks over the frames where both are voiced and within 35 dB of their loudest. Waveform cross-correlation tells two performances from a copy.

| Pair | Most common interval | Waveform correlation | Role | Placement chosen |
| --- | --- | --- | --- | --- |
| Lead 2 against lead 1 | −12 (39-53%) | — | Octave below the lead | Centre, under the lead |
| Backing vocals 1-4 against each other | −4, −7, −11 from the top voice (48-70%) | 0.00 ± 0.01 | A four-part chord, four separate takes | Spread by role, below |
| Backing vocal 5 against the lead | +3 (44%), +4 (27%) | — | One harmony a third above, alone | −0.3 |

The harmonies whose pitch lies closest to the lead's (median MIDI 69.0 and 65.9 against the lead's 67.7) mask the lead most, so they went furthest out, ±0.65: separation in space lowers masking where the spectra overlap most (spatial release from masking; Cherry, 1953). The top and bottom voices went to ±0.3. Which of those two went left was chosen by level with the prediction of section 4: the top voice is the loudest backing vocal (2.0% of the mix's power against 0.6-0.9%). With the high voices on the left and the low voices on the right, as in a choir seated soprano to bass, the 500-2k band of the two backing-vocal sections came to +0.04 and +0.22 dB; with the top voice on the right, −0.67 and −0.63 dB.

## 4. Predicting a layout before writing it

One stem render of every track ([Measuring every gain stage in one render](../../reaper-mcp/references/rendering.md#measuring-every-gain-stage-in-one-render)) holds enough to predict the left/right balance of any layout:

1. For each stem, each song section and five bands (20-120, 120-500, 500-2k, 2-6k, 6-20k Hz): the mean power of the left and right channels and the mean of L×R.
2. For each folder: its own stem's power divided by the power of the sample sum of its children's stems. The product along a track's parents is the track's gain to the premaster output, which carries the bus compressors and the limiter as a gain.
3. Apply each layout to the children's numbers. Balance mode: the side opposite the pan times (1 − |pan|) with the 0 dB law. Stereo pan mode: first the width crossfeed, (1 + w)/2 to the own side and (1 − w)/2 to the other, then the same balance ([Stereo tracks](../SKILL.md#stereo-tracks-the-default-mode-drops-a-channel)). Returns stay as rendered.
4. Sum and compare the two sides per section and band.

| Check | Predicted | Measured after the change |
| --- | --- | --- |
| Power of the unpanned mix against the rendered premaster, per band | — | −1.66, −0.74, −0.48, −0.58, −0.37 dB: correlated kick mics add more than their powers |
| Broadband balance, whole song | +0.05 dB | +0.28 dB |
| Broadband balance, intro (one guitar alone on the left) | +1.25 dB | +1.43 dB |
| Broadband balance, the section with the second guitar playing throughout | −0.45 dB | −0.33 dB |
| Piano stem, stereo mode, pan −0.3, width 0.6 | L −28.7, R −30.0 dBFS RMS | L −28.9, R −30.0 |
| Guitar stem, stereo mode, pan −0.75, width 0.3 | balance about +12 dB | +11.91 dB |

What the comparisons decided on this song, with the predicted balance (left minus right) of the section where the second guitar plays throughout:

| Layout | 500-2k | 2-6k | 6-20k | Outcome |
| --- | --- | --- | --- | --- |
| Piano and the louder guitar on the same side | +2.55 dB | +3.43 dB | −1.86 dB | Rejected: the louder guitar (8.3% of the mix's power) and the piano (15.1%) outweighed the quieter guitar (5.2%) |
| One guitar each side, hi-hat on the right (audience view) | −1.93 dB | −3.08 dB | −1.87 dB | Rejected: the hi-hat joined the brighter guitar's side in every section from 40 s on |
| The same, hi-hat on the left (player's view) | −1.66 dB | −2.70 dB | +1.92 dB | Kept: the hi-hat answers the brighter guitar |
| Final: guitars −0.75 and +0.6, piano −0.3 at width 0.6 | −1.18 dB | −2.29 dB | +1.92 dB | Chosen |

## 5. Loudness and width after panning

- **Loudness.** The power model predicted −0.75 dB of total power with the 0 dB law. The premaster measured −13.97 → −14.39 LUFS-I (−0.42 LU), with true peak unchanged at −1.00 dBTP: the bus compressors and the limiter gave part of it back. The limiter's input gain held the target: FabFilter Pro-L 2 Gain +10.70 → +11.10 dB gave −14.03 LUFS-I and −1.00 dBTP.
- **Vocal against music.** The lead stays centred while the music is panned, so the lead gains relative to the music: median +0.02 → +0.32 LU over the 400 ms blocks where the lead sings.
- **Mono.** The loss when folding to mono went from 0.26 to 0.28 LU.
- **Width is not one number.** Side minus mid of the whole mix went from −11.8 to −12.1 dB, slightly narrower, while the parts moved apart. Most of the width before came from one wide stereo piano (L/R correlation 0.26), which was narrowed to width 0.6 (0.76); panned mono parts replaced it. Judge width per part and per section, not from the mix's side level.

## Applying in this plugin

Claude measures, simulates and writes; the user approves the layout and listens.

| Step in this document | How to do it in the plugin |
| --- | --- |
| Source channel counts | Bridge: `Main_OnCommand(40101, 0)`, then `GetMediaSourceNumChannels` and `GetMediaSourceSampleRate` per take |
| Source file paths for the analysis | Bridge: `GetMediaSourceFileName`; the project's own `Media` folder may hold copies, so read the path the take uses |
| Mic geometry, vocal pitch, stereo per band | Python on the source files (numpy, scipy and soundfile are in `~/.reaper-for-claude/venv`) |
| One stem render of every track | Bridge, [rendering.md](../../reaper-mcp/references/rendering.md#stems-render_settings-3-and-check-render_targets-first): select all tracks, `RENDER_SETTINGS` 3, 32-bit float, check `RENDER_TARGETS`, action 42230, restore the settings and the selection |
| Apply the layout | `set_track_pan`; stereo sources with `pan_mode` "stereo", `width` and `pan` |
| Read the layout back | Bridge: `D_PAN`, `D_WIDTH`, `I_PANMODE` and `D_VOL` of every track, reporting only those that differ from the defaults |
| Hold the loudness target | `set_fx_parameter` on the limiter's gain, following the [plugin protocol](../../reaper-mcp/references/plugin-protocol.md) |
