---
name: reaper-panning
description: >-
  Panning and stereo placement in REAPER: where each part sits from left to
  right, what stays in the centre and in mono, how wide each part and each
  section of a song is, and how 30 genres are usually panned (pop, ballad,
  K-pop, R&B, Vietnamese bolero, hip-hop, trap, afrobeats, reggaeton, EDM,
  house, techno, drum and bass, dubstep, rock, metal, indie, folk, country,
  jazz, orchestral and more). Covers REAPER's pan law and pan modes as
  measured, stereo tracks that lose a channel when panned, sends and pan,
  keeping the low end mono with stock FX, and checking a pan decision in mono.
  Use whenever a pan or a width is set, judged or explained. Levels, reverb
  and compression numbers belong to reaper-audio-engineer; tool calls and the
  Lua bridge to reaper-mcp.
---

# REAPER Panning

This skill decides where each part sits in the stereo image and checks the result. It owns pan positions and width, the centre and the mono low end, REAPER's pan law and pan modes, and the panning conventions of each genre ([genres.md](./references/genres.md)).

Other subjects have their own owners. Near and far (depth) is reverb's: [reverb.md](../reaper-audio-engineer/references/topics/reverb.md), section 6 and its distance rule. Levels and the choice of pan law at the start of a project belong to [gain-staging.md](../reaper-audio-engineer/references/topics/gain-staging.md). How to measure and verify follows [reaper-audio-engineer](../reaper-audio-engineer/SKILL.md): measure before and after, one change at a time. Calling tools and the bridge follows [reaper-mcp](../reaper-mcp/SKILL.md).

## Contents

- [Rules for every genre](#rules-for-every-genre)
- [Working order](#working-order)
- [REAPER's pan, measured](#reapers-pan-measured)
- [Checking a pan decision](#checking-a-pan-decision)
- [Applying in this plugin](#applying-in-this-plugin)
- [Troubleshooting](#troubleshooting)

## Rules for every genre

1. **The centre.** Kick, bass, snare and the lead vocal sit in the centre in almost every genre. Below about 100–120 Hz everything stays mono: [Mono low end](#mono-low-end-with-stock-fx).
2. **Where width comes from.** Symmetric doubles, stereo instruments, and reverb and delay. Not from panning every part to the edges.
3. **A double is two performances.** A take copied to a second track and panned hard left and right puts the same signal in both channels. Left equals right, which is a centred mono image, only louder. Delaying the copy by a few milliseconds sounds wide but comb-filters when summed to mono. Check any such trick in mono.
4. **Pan is left and right, not near and far.** A part moves back with level, brightness and reverb (reverb.md's distance rule), not by moving it to the side.
5. **Balance the two sides.** A part placed off centre gets a partner of similar range and rhythm on the other side: hi-hat against ride or shaker, rhythm guitar against keys. A part with no partner leaves its side heavier. Measure the balance after placing it: [Checking a pan decision](#checking-a-pan-decision).
6. **One perspective.** Choose the audience's or the player's view for a drum kit and keep it. Pan the close mics (hi-hat, toms) to where the same drums appear in the overheads; a close mic panned against its overhead image smears the drum. The same holds for a jazz combo or an orchestra: pan by seating.
7. **Width is part of the arrangement.** Many genres keep the verse narrow and open the chorus or the drop. [Width patterns at a glance](./references/genres.md#width-patterns-at-a-glance) groups the 30 genres by how they make width.

## Working order

| Step | What | Price | Payoff | Stability |
| --- | --- | --- | --- | --- |
| 1 | Read the current state of every track with `list_tracks`: `pan`, `pan_mode`, `width`, `pan_law_db` | one call, 178 ms for 21 tracks | Nothing already set gets moved; stereo tracks still in balance mode show up before they are panned | Read only |
| 2 | Name the genre and its width pattern ([genres.md](./references/genres.md)); assign the centre, the pairs and the single parts | none | A plan the user can approve in one message | Nothing changes until the user agrees |
| 3 | Place each stereo track that must move or narrow with one `set_track_pan` call: `pan_mode` "stereo", `width` and `pan` ([Stereo tracks](#stereo-tracks-the-default-mode-drops-a-channel)) | 0.48 s per call in the benchmark (pan alone 0.47 s) | Panning cannot delete a channel | One undo step for mode, width and pan; the reply reads all three back |
| 4 | Pan the other tracks with `set_track_pan` and `pan` alone | the same per call | The part sits where the plan says | One undo step per track |
| 5 | Check in mono and for balance | 18–22 s per render in the benchmark | Hard-panned parts that sink in mono, and a heavy side, are caught | The render tools restore the project's render settings |

## REAPER's pan, measured

Measured 2026-10-08 on REAPER 7.82 in a temporary project: test tones at −20.0 dBFS per channel (amplitude 0.1), rendered and measured channel by channel. Each render took 18–22 s.

The dual pan rows and the width row in balance mode were measured the same way on 2026-10-09.

A new project on this machine reads `PANLAW 1` (0 dB), `PANMODE 3` (stereo balance / mono pan) and `PANLAWFLAGS 3` in its `.rpp`; a new track reads `I_PANMODE` −1 and `D_PANLAW` −1, meaning both follow the project. These are the user's settings: read them, do not change them.

### Pan law: a panned part changes level

One mono 1 kHz tone on one track:

| `D_PAN` | Pan law | L | R | Mono fold (L+R)/2 |
| --- | --- | --- | --- | --- |
| 0 | 0 dB (project default) | −20.0 | −20.0 | −20.0 |
| −0.5 | 0 dB | −20.0 | −26.0 | −22.5 |
| −1 | 0 dB | −20.0 | silent | −26.0 |
| 0 | −3 dB (`D_PANLAW` 0.708) | −23.0 | −23.0 | −23.0 |
| −1 | −3 dB | −20.0 | silent | −26.0 |

- With the 0 dB law, the side a part moves towards keeps its level and the other side is turned down. Centred, the part plays at −20.0 from two speakers; hard left, from one. Two channels at the same level carry 3 dB more power than one, so a part moved from the centre to the edge sounds about 3 dB quieter.
- In mono the same move costs **6.0 dB** with the 0 dB law and **3.0 dB** with the −3 dB law. Hard-panned parts sink against centred ones whenever the mix is heard in mono: check them after panning.
- The law changes every centred part: −3.0 dB at the centre with the −3 dB law. That is why gain-staging.md settles the law at the start of a project and never changes it.
- `D_PANLAW` on a track overrides the project: −1 follows the project, 0.708 is −3 dB.

### Stereo tracks: the default mode drops a channel

A stereo item with 440 Hz only in the left channel and 660 Hz only in the right, each at −20.0:

| Pan mode (`I_PANMODE`) | `D_PAN` | `D_WIDTH` | 440 Hz (from L) | 660 Hz (from R) |
| --- | --- | --- | --- | --- |
| Project default (balance) | −0.5 | — | L −20.0 | R −26.0 |
| Project default (balance) | −1 | — | L −20.0 | **gone** |
| 5, stereo pan | −1 | 0 | L −26.0 | L −26.0 |
| 5, stereo pan | 0 | 0.5 | L −22.5, R −32.0 | L −32.0, R −22.5 |
| Project default (balance) | 0 | 0 | L −26.0, R −26.0 | L −26.0, R −26.0 |
| 6, dual pan (`D_DUALPANL` −1, `D_DUALPANR` +1) | −1 | 1 | L −20.0 | R −20.0 |
| 6, dual pan (`D_DUALPANL` −1, `D_DUALPANR` +1) | −1 | 0 | L −20.0 | R −20.0 |
| 6, dual pan (`D_DUALPANL` −1, `D_DUALPANR` −1) | 0 | 1 | L −20.0 | L −20.0 |

- In the default mode, the pan knob of a stereo track is a balance control. It turns the opposite channel down, and at full pan removes it; nothing moves across. A stereo piano or a stereo room recording panned hard left loses everything that was only in its right channel.
- To place a stereo source, set `I_PANMODE` to 5, narrow it with `D_WIDTH`, then pan. Width w sends (1+w)/2 of each channel to its own side and (1−w)/2 to the other (0.75 and 0.25 at w = 0.5, matching the table). Width 0 is (L+R)/2: content present in one channel only drops 6.0 dB, content common to both keeps its level.
- A source that should be entirely mono, such as a stereo 808 or sub sample, is collapsed by stereo pan mode with width 0.
- `D_WIDTH` also acts in the default mode: width 0 at pan 0 put both tones in both channels at −26.0. Other widths were not measured in this mode.
- Dual pan (mode 6) ignores `D_PAN` and `D_WIDTH`: each channel follows its own pan, `D_DUALPANL` and `D_DUALPANR`.
- `set_track_pan` writes the mode, then the width, then the pan, in one call and one undo step. It refuses pan and width for a track that would be in dual pan mode.

### Sends: only post-fader sends follow the track's pan

A mono 1 kHz tone on a track panned hard left, its master send off, one send to a bus:

| Send mode (`I_SENDMODE`) | The bus receives |
| --- | --- |
| 0, post-fader (post-pan) | L −20.0, R silent |
| 3, pre-fader (post-FX) | L −20.0, R −20.0: centred |

- A post-fader reverb send from a hard-panned part enters the reverb from that side. reverb.md, section 11.5, turns the send's own pan towards the other side to fill the space.
- A pre-fader send ignores the track's pan. A throw from a panned part comes from the centre unless the send's pan is set.
- Not measured here: pre-FX sends (mode 1).

### Mono low end with stock FX

`JS: RBJ Stereo Image Filter` (Liteon, ships with REAPER) high-passes the signal and makes what lies below the corner mono, in proportion to its Filter Amount.

Measured with a stereo file holding 60 Hz and 1 kHz in the side only and 300 Hz in the mid only, at `S - HP (Scale)` 25.7 (corner 120 Hz) and Filter Amount 100%:

| Content | Without the filter | With the filter |
| --- | --- | --- |
| Side, 60 Hz | −20.0 | −32.3 |
| Side, 1 kHz | −20.0 | −20.0 |
| Mid, 300 Hz | −26.0 | −26.0 |
| Mono fold, RMS | −29.0 | −29.0 |

- The mid and the mono fold do not change, so the filter is safe on parts that are already centred.
- The side fell 12.3 dB one octave below the corner: the slope is 12 dB per octave, not a wall. To make a region mono, put the corner about an octave above it.
- Parameters: `0` S - Filter Amount (%), default 100; `1` S - HP (Scale), 0 = off; `2` S - LP (Scale), 100 = off. In the plugin's code, Filter Amount 0% leaves the low end as it is and 100% makes it mono.
- The corner, from the plugin's code: f = floor(8.17742 × 1.059^(16 + 1.20103 × scale)) Hz. The slider moves in steps of 0.05 (2,000 steps), so use the values below; one step lower lands 1 Hz under the corner.
- Through the tool, the normalized value is scale / 100: `set_fx_parameter` with 0.257 read back as 25.7, displayed "25.7".

| Corner | 80 Hz | 100 Hz | 120 Hz | 150 Hz | 200 Hz | 240 Hz |
| --- | --- | --- | --- | --- | --- | --- |
| `S - HP (Scale)` | 19.85 | 23.05 | 25.70 | 28.95 | 33.15 | 35.80 |

Put it on a stereo track or bus whose low end should not be wide: a stereo synth, pad or piano, a mid-bass layer. On the master it is a mastering decision ([audio-mastering.md](../reaper-audio-engineer/references/audio-mastering.md)).

## Checking a pan decision

| Check | How | Price | Payoff | Stability |
| --- | --- | --- | --- | --- |
| The pan values themselves | `list_tracks`: `pan`, `pan_mode`, `width`, `pan_law_db` for every track | one call, 178 ms for 21 tracks | Catches a wrong side, or a stereo track in balance mode | Exact |
| Mono fold, width and correlation of the mix | `analyze_stereo_field`: `mid_rms_db` is the mono fold (L+R)/2, `side_rms_db` the side, `lr_correlation`, `stereo_width_ratio` | 19.9 s in the benchmark (one full render) | Mono compatibility of the whole mix | An average over the project: a short problem hides in it ([audio-recording.md](../reaper-audio-engineer/references/audio-recording.md#46-phase-correlation-between-multiple-microphones-on-a-single-source)). The tool renders the whole project; for one section, `render_time_selection` and measure the file in Python |
| Balance between left and right | `analyze_stereo_field`: `left_rms_db`, `right_rms_db` and `lr_balance_db` (left minus right, positive = left louder). A silent channel reads `null` with a `balance_note` | the same render as the row above | Finds a heavy side | Exact: a stereo tone panned −0.5 in balance mode read L −23.0, R −29.0, balance +6.0, as calculated. An average over the project; a section is measured as in the row above |
| One part's placement and its mono loss | `render_stems` for the part, then L, R and (L+R)/2 of the stem | 18.2 s per call in the benchmark | Confirms where the part sits and what it loses in mono | Exact |

Report the numbers: "guitar L −18.2 / R −24.3 dBFS RMS, mono fold −21.0", not "the guitar sits left". After panning, compare the mono fold of hard-panned parts with the centred ones against the 6.0 dB (0 dB law) or 3.0 dB (−3 dB law) measured above.

## Applying in this plugin

Claude reads, plans and applies pans through the tools; only a track's own pan law needs the bridge. The user approves the plan and listens.

| Step | How to do it in the plugin |
| --- | --- |
| Read every track's pan, mode, width and pan law | `list_tracks` or `get_track_info`: `pan`; `pan_mode` (balance, stereo, dual or classic), with `pan_mode_from_project` true when the track follows the project, whose mode is then shown; `width`; `pan_law_db` (0.0 is the 0 dB law, −3.0 the −3 dB law) with `pan_law_from_project`. A dual pan track also shows `dual_pan` |
| Pan a track | `set_track_pan` with `pan` (−1 left to +1 right); the reply reads it back |
| Place or narrow a stereo track | `set_track_pan` with `pan_mode` "stereo", `width` and `pan` in one call. It writes them in that order as one undo step and reads all three back. `pan_mode` "project" returns a track to the project's mode |
| A pan law for one track | `list_tracks` reads it; writing it takes the bridge: `D_PANLAW`. The project's law is set in Project Settings and is the user's choice |
| Send pan | `set_send_routing` with `pan`; the send mode decides whether the track's pan reaches the send |
| Pan or width automation | `add_envelope_points` with `envelope` "Pan" (−1 left to 1 right) or "Width" (−1 to 1); the tool converts pan to REAPER's inverted storage. A bridge write must invert it itself: [Each envelope stores its own units](../reaper-mcp/references/python-reaper-tools.md#each-envelope-stores-its-own-units). `add_pan_automation` needs the envelope shown first |
| Make the low end mono | `add_fx` "RBJ Stereo Image Filter", then `set_fx_parameter` on index 1 with the normalized value scale / 100 (the slider runs 0 to 100), following the [plugin protocol](../reaper-mcp/references/plugin-protocol.md): write once, read the display back |
| Check | `analyze_stereo_field` for the mono fold, correlation and L/R balance; `render_stems` for one part, then a per-channel measurement ([audio-measurement.md](../reaper-audio-engineer/references/audio-measurement.md)) |

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| A stereo instrument loses notes or body when panned | The default pan mode is a balance control: the opposite channel is turned down, and removed at full pan | `set_track_pan` with `pan_mode` "stereo", `width` and `pan` |
| A part gets quieter when panned, or disappears in mono | Pan law: with 0 dB, full pan costs 6.0 dB in mono | Check in mono after panning; the law is settled at the start (gain-staging.md) |
| The reverb of a hard-panned part sits on one side | A post-fader send carries the track's pan | The send's own pan (reverb.md, section 11.5) |
| A throw from a panned part comes from the centre | A pre-fader send ignores the track's pan | Set the send's pan |
| Pan automation written through the bridge lands on the opposite side | Pan envelope values are stored inverted; the tools convert, a raw bridge write does not | [Each envelope stores its own units](../reaper-mcp/references/python-reaper-tools.md#each-envelope-stores-its-own-units) |
| A doubled part sounds mono | One take copied to two tracks | Two performances, or leave it in the centre |
| The bass gets thinner in mono, or the low end wanders | Stereo content below about 120 Hz | RBJ Stereo Image Filter on the wide part; check `lr_correlation` |
