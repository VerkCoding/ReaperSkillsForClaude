# Tracking latency in REAPER

Oct 25, 2026 · measured on REAPER 7.82, Windows, Focusrite USB ASIO at 48 kHz with a 64-sample buffer

> **In this plugin:** this document owns the monitoring-latency budget while recording, which FX may sit on a monitored path, and the layout of a low-latency tracking project. Levels follow [gain-staging.md](gain-staging.md), compressor settings follow [compression.md](compression.md), reverb settings follow [reverb.md](reverb.md).
> Written for hands-on work in the REAPER interface. The last section, "Applying in this plugin", maps each step to the MCP tools and the Lua bridge.

## 1. What the performer hears

Round trip = the interface's input latency + its output latency + the PDC of every FX chain on the path the live input takes to the ears.

- The first two are what REAPER reports (`GetInputOutputLatency`): 188 + 188 samples, 7.8 ms, on the measured system. They follow the buffer size set in the driver's own control panel, not anything in the project.
- The third is the part a project decides.

How it was measured: a JSFX in the armed track's input FX writes an impulse and records the sample count; detector JSFX further along the path report how many samples later the impulse arrives. The track was armed with record mode "Record: disable (input monitoring only)", the project playing, the master muted (master FX still run when the master is muted).

| Where an FX reporting 1024 samples of PDC sits | Added to the live input |
|---|---|
| Nowhere | 0 |
| Track FX of the armed track | 1024 |
| Input FX (record FX) of the armed track | 1024, and the recorded file carries that FX |
| A parent folder of the armed track | 1024 |
| Master FX | 1024 |
| A playback track beside it, not armed (also when muted, also with its chain set to ignore PDC) | 0 |
| The same FX bypassed, or set offline (a JSFX and Acon DeNoise 2, VST3) | 0 |
| A cue bus fed by a pre-fader send | 1024 (pre-fader is after the track FX) |
| A cue bus fed by a pre-FX send | still 1024: REAPER delays the pre-FX tap to keep it aligned with the track's output. 0 only when the track's chain PDC mode is 2, "ignore PDC" |
| A cue bus that also receives a playback track carrying PDC | 0: the live input is not held back for it |

Two consequences follow.

1. **Every chain costs whole blocks.** REAPER compensates each chain by its latency rounded up to the buffer size (`chain_pdc_actual`), and those rounded values add up along the path. In a copy of a real recording project at 64 samples, a vocal path of TDR Nova (184) on the track, UADx Pure Plate (31) on its bus and FabFilter Pro-L 2 (731) on the pre-master folder reports 946 samples and measured 1024 (192 + 64 + 768). A plugin that reports a single sample, such as Waves SSLChannel, costs a full block on its path.
2. **The latency sits on the path, not only on the track.** A lookahead limiter on a pre-master folder delays every monitored input beneath it. Removing the track's own plugins does not help then.

## 2. Budget and measured plugins

The Tier 3 working limit is 10 ms round trip for a performer ([audio-recording.md](../audio-recording.md), section 7). With 7.8 ms of interface latency, the budget for FX on the monitored path is zero blocks.

PDC at 48 kHz as REAPER reads it (`pdc`), with the settings found in two real projects; values that depend on a setting are marked. CPU per instance: 8 instances on 8 tracks playing 240 s of a sung vocal, REAPER's process CPU over 10 s, minus the mean of empty-chain runs before and after, divided by 8. The runs scatter by about 0.5 % per instance; a value under 0.5 % reads as "below noise". Not the realtime-thread load: these tracks were not armed, so anticipative FX processed them ahead of time.

| Plugin | PDC (samples) | CPU (% of one core) |
|---|---|---|
| sonible smartChain (Normal latency mode) | 4032 | 5.5 |
| oeksound soothe2 | 2048 | 5.8 |
| Acon Digital DeNoise 2 | 1024 | 3.0 |
| FabFilter Pro-L 2 (lookahead; two settings) | 731, 532 | 7.7 |
| ReaLimit, default settings | 480 | below noise |
| TDR Nova (setting) | 184 | 6.7 |
| Arturia Rev SPRING-636 | 53 | 16.5 |
| Waves Scheps Omni Channel 2 Stereo (one instance; others 0) | 49 | 4.9 |
| UADx Pure Plate Reverb | 31 | 2.4 |
| Plugin Alliance bx_glue | 15 | 4.6 |
| IK TONEX | 7 | 7.2 |
| Waves SSLChannel Stereo, SSLGChannel Stereo | 1 | 3.1, 1.1 |
| Waves Scheps Omni Channel 2 Mono | 0 | 1.8 |
| iZotope Nectar 4 Delay | 0 | 8.5 |
| ValhallaFutureVerb | 0 | 6.2 |
| ToneLib-NoiseReducer | 0 | 3.1 |
| Kilohearts kHs Reverb | 0 | 2.8 |
| Youlean Loudness Meter 2; JS Loudness Meter (Cockos) | 0 | 2.7 each |
| Blenheim Sound reVUe (CLAP), five runs | 0 | 0.4 to 2.4, mean 1.5 |
| Voxengo SPAN Plus | 0 | 1.2 |
| JS Volume/Pan Smoother v5 | 0 | 0.7 |
| ReaEQ, ReaComp, ReaGate, ReaVerbate, ReaDelay, ReaXcomp, JS Volume Adjustment, JS Dynamic Range Meter (defaults) | 0 | below noise (0.7 at most) |

A whole recording project of 14 armed and monitored tracks with the third-party chains above (13 Scheps Omni Channel, 3 TDR Nova, 3 DeNoise 2, 3 ToneLib-NoiseReducer, TONEX, SSLChannel, Pro-L 2, Pure Plate, Nectar 4 Delay, 13 JS volume smoothers, 19 reVUe, two loudness meters) cost 68.7 % of one core with its FX on against off (four alternating 10 s pairs, spread 9.1).

## 3. A low-latency tracking project

1. **Record the input, not the output.** Record mode "Record: input" writes the dry signal; the comfort FX live in track FX and on returns, never in input FX.
2. **Zero PDC on every monitored path.** Track FX, every folder above the input, the master, the cue bus and anything between them. Stock ReaEQ, ReaComp, ReaGate, ReaVerbate, ReaDelay and ReaXcomp reported 0 at default settings. ReaComp's "Pre-comp" control is a lookahead; it stood at its default 0 in that measurement, so read `pdc` again after changing it.
3. **No lookahead limiter, linear-phase EQ or oversampling above the inputs.** A safety limiter on a cue bus is ReaComp at a high ratio with a fast attack, not ReaLimit (480 samples).
4. **Cue mixes come from pre-fader sends** into a cue bus with its own hardware output, so the engineer's faders do not change what the singer hears. Pre-fader sends still carry the track FX, so the zero-PDC rule covers the track too.
5. **Comfort effects on returns.** A short plate or room (ReaVerbate) and a slap or eighth-note delay (ReaDelay) on returns fed from the vocal, 100 % wet, settings from [reverb.md](reverb.md).
6. **Mixing chains stay bypassed while tracking.** On REAPER 7.82 a bypassed or offline FX adds no latency, so a mixing chain can wait in the project, bypassed, until the session is over.
7. **Interface latency is the floor.** Halving the buffer in the driver's control panel lowers it; whether the computer holds that at full session load is a test for the user.

## 4. Settings checked on REAPER 7.82

| Setting | Where | Finding |
|---|---|---|
| Anticipative FX processing | Preferences, Audio, Buffering (global) | On during every measurement: the armed, monitored track added 0 samples. Armed tracks run in realtime |
| Chain PDC mode | Per FX chain, `chain_pdc_mode` | 2 (ignore PDC) stops compensation of that chain; the FX still delays its own output. It took a pre-FX send out of the track's alignment and left the track's output unchanged |
| PDC auto-bypass | Preferences, Audio (global, REAPER 7.34 and later), `pdcautobypassms` | Bypasses FX above a PDC threshold on record-armed tracks, by REAPER's help text. Off on the measured system; not tested here. Global: the user decides |
| Input FX versus track FX | Track | Both add their PDC to the monitored input; only input FX end up in the file |
| Bypass versus offline | FX | Both remove the FX's latency from the path |

## Applying in this plugin

Claude measures and builds; the user sets the driver buffer, the global preferences and the interface's own routing.

| Step in this document | How to do it in the plugin |
|---|---|
| Read each FX's PDC | Bridge: `TrackFX_GetNamedConfigParm(tr, fx, "pdc")`; per chain `chain_pdc_reporting`, and `chain_pdc_actual` after playback has started ([Plugin Control](../../../reaper-mcp/references/plugin-control.md#latency)) |
| Latency of a monitored path | Bridge: sum `chain_pdc_actual` of the track, each `GetParentTrack` up the tree and the master, then add `GetInputOutputLatency()`; convert at `GetAudioDeviceInfo("SRATE")` |
| Prove a path with an impulse | Bridge: a generator JSFX in input FX and a detector JSFX where the path ends, sharing `gmem`; arm with record mode 2 and the master muted. Noise reducers and amp simulators swallow or reshape the impulse, so probe those paths by their PDC |
| Arm, input, monitoring, record mode | Bridge: `I_RECARM`, `I_RECINPUT`, `I_RECMON`, `I_RECMODE` (no tool sets these) |
| Folders | Bridge: `I_FOLDERDEPTH` |
| Pre-fader cue sends | `create_send`, then `set_send_routing` for the mode |
| Cue bus hardware output and master send off | Bridge: `CreateTrackSend(tr, nil)` and `B_MAINSEND` |
| Stock FX and their settings | `add_fx`, then `set_fx_parameter` following the [Plugin protocol](../../../reaper-mcp/references/plugin-protocol.md) |
| CPU of a chain | Bridge plus a process CPU sample from outside REAPER: alternate the chain on and off (`I_FXEN`) during playback and compare |
| Driver buffer, enabled hardware outputs, anticipative FX, PDC auto-bypass | The user, in the driver's control panel and in REAPER's Preferences |
