# Tracking latency in REAPER

Oct 25, 2026 · measured on REAPER 7.82, Windows, Focusrite USB ASIO at 48 kHz with a 64-sample buffer · plugin library in section 2 added Oct 26, 2026, on REAPER 7.83. Sections 5 to 7 and the notes dated November: REAPER 7.83, same interface, Oct 26 to Nov 9, 2026

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

### In two real projects (REAPER 7.82, 2026-10-25)

PDC at 48 kHz as REAPER reads it (`pdc`), with the settings found in two real projects; values that depend on a setting are marked. CPU per instance: 8 instances on 8 tracks playing 240 s of a sung vocal, REAPER's process CPU over 10 s, minus the mean of empty-chain runs before and after, divided by 8. The runs scatter by about 0.5 % per instance; a value under 0.5 % reads as "below noise". Not the realtime-thread load: these tracks were not armed, so anticipative FX processed them ahead of time.

| Plugin | PDC (samples) | CPU (% of one core) |
|---|---|---|
| sonible smartChain (Normal latency mode) | 4032 | 5.5 |
| oeksound soothe2 | 2048 | 5.8 |
| Waves Clarity Vx Mono (every setting tried, Nov 2026) | 2048 | not measured |
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

PDC belongs to the settings, not to the plugin's name (Nov 2026):

- **FabFilter Pro-Q 4** reports 0 at default in Zero Latency mode, 5120 or 9219 as soon as one band has Spectral on (resolution Medium or High), and 3 with Character at Subtle or Warm, which costs a whole block. Details in [Plugin Control](../../../reaper-mcp/references/plugin-control.md#fabfilter-pro-q-4).
- **FabFilter Pro-L 2** with True Peak Limiting on reported 3115 (lookahead 0.18 ms) and 3131 (5 ms); it has no zero-latency setting.
- A noise reducer the user adds to a vocal can be the whole problem: Clarity Vx on a lead vocal took that singer's round trip from 7.8 ms to about 50 ms. It sits in track FX, so the recorded file stays dry, and bypassing it while tracking removes the delay and keeps its settings.

So read `pdc` on each instance of the project in front of you. This table, and any scan made on fresh instances, says what a plugin does at the settings named.

A whole recording project of 14 armed and monitored tracks with the third-party chains above (13 Scheps Omni Channel, 3 TDR Nova, 3 DeNoise 2, 3 ToneLib-NoiseReducer, TONEX, SSLChannel, Pro-L 2, Pure Plate, Nectar 4 Delay, 13 JS volume smoothers, 19 reVUe, two loudness meters) cost 68.7 % of one core with its FX on against off (four alternating 10 s pairs, spread 9.1).

### Across one plugin library (REAPER 7.83, 2026-10-26)

Measured on REAPER 7.83/x64, Windows 11, the same Focusrite USB ASIO interface and a 48 kHz project rate, while choosing light replacements for the stock FX of a 64-sample tracking project. The audio device ran as the user's preferences left it.

**PDC.** `TrackFX_GetNamedConfigParm(tr, fx, "pdc")`, in samples, for 71 installed plugins at their factory defaults; Pro-L 2 was read with the settings found on a master. A setting can change a plugin's PDC (lookahead, linear phase, oversampling), so read `pdc` again after changing one.

Reported 0 at their defaults:

| Vendor | Plugins |
|---|---|
| Kilohearts | kHs Filter, kHs 3-Band EQ, kHs Compressor, kHs Clipper, kHs Delay, kHs Reverb, kHs Transient Shaper, kHs Gain |
| ZL | ZL Equalizer 2, ZL Compressor |
| Voxengo | PrimeEQ, Marquis Compressor, Deft Compressor, Tempo Delay, OldSkoolVerb, Peakbuster, Boogex, Tube Amp |
| Waves | CLA-76 Mono, CLA-2A Mono, CLA-3A Mono, DeEsser Mono, Sibilance-Live Mono, C1 comp Mono, C1 gate Mono, SSLComp Stereo, API-2500 Stereo, API-550A Mono, Scheps Omni Channel 2 Mono, Q10 Mono, Waves Tune Real-Time Mono, Vocal Rider Live Mono, Torque-Live Mono, H-Delay Stereo, RVerb Stereo, TrueVerb Stereo |
| FabFilter | Pro-Q 4, Pro-C 3, Pro-G |
| Valhalla DSP | ValhallaRoom, ValhallaVintageVerb |
| Focusrite | Red 2 EQ, Red 3 Compressor |
| Others | YS - Reverb-24 (Yuri Semenov), TONE3000, Torpedo Wall Of Sound (Two Notes): it output silence on this system ([Plugin Control](../../../reaper-mcp/references/plugin-control.md#registry-of-measured-plugins)) |

Reported more than 0, so each costs at least one block on a monitored path (64 samples, 1.3 ms, at the 64-sample buffer; section 1):

| Plugin | PDC (samples) |
|---|---|
| Waves SSLChannel Mono | 1 |
| IK TONEX | 7 |
| Relab LX480 Essentials | 13 |
| Voxengo EBusLim | 23 |
| Waves GTR Amp Mono | 40 |
| Waves Magma Springs Stereo | 49 |
| Waves SSL EV2 Channel Mono | 59 |
| Waves RCompressor Mono, RVox Mono, RDeEsser Mono, Silk Vocal Live Mono; Soundshed Guitar; XLN Addictive Keys | 64 |
| Waves RChannel Mono | 65 |
| Techivation T-De-Esser 2 | 70 |
| Kilohearts kHs Limiter | 95 |
| Waves PRS Dallas Mono | 175 |
| TDR VOS SlickEQ | 183 |
| TDR Kotelnikov, TDR Nova | 184 |
| Kilohearts kHs Gate | 240 |
| Klevgrand Korvpressor | 480 |
| FabFilter Pro-DS | 720 |
| FabFilter Pro-L 2 (settings found on a master) | 731 |
| Techivation Tilt EQ | 2118 |

Where both sets hold a plugin, the readings agree: TDR Nova 184, TONEX 7, Pro-L 2 731, Scheps Omni Channel 2 Mono 0, kHs Reverb 0, and Waves SSLChannel 1 in its Mono form as in its Stereo form.

**CPU per instance.** 8 instances of one plugin on 8 tracks, each track fed by a JS Pink Noise Generator, the master muted, the tracks not armed. REAPER's process CPU averaged over 8 s after a 2.5 s warm-up, minus the mean of 4 runs with the generators alone, divided by 8. Factory defaults throughout.

The noise is large. The four generator-only baselines drifted from 49.0 % to 56.3 % of one core in total, 7.3 points, or 0.9 per instance once divided by 8, and three plugins came out below zero. Read every figure as ±1 % and the list as two groups, not a ranking:

| Vendor | Under 2 %: not separable from one another or from the stock FX | 2 % and above: measurably heavier |
|---|---|---|
| Cockos (stock) | ReaComp −0.64, ReaDelay 0.85, ReaEQ −0.20, ReaGate 1.09, ReaVerbate 0.34 | |
| FabFilter | Pro-Q 4 1.24 | Pro-C 3 3.31, Pro-G 4.44 |
| Focusrite | Red 2 EQ 1.41, Red 3 Compressor 1.24 | |
| Kilohearts | kHs 3-Band EQ 0.80, kHs Clipper 0.04, kHs Compressor 0.21, kHs Delay 0.95, kHs Filter 1.14 | |
| Two Notes | Torpedo Wall Of Sound 0.51, with silent output | |
| Valhalla DSP | ValhallaVintageVerb 0.12 | ValhallaRoom 2.80 |
| Voxengo | Tube Amp 0.87 | Boogex 3.83, its own reverb on by default |
| Waves | C1 comp −0.23, C1 gate 0.58, CLA-2A Mono 0.38, CLA-76 Mono 0.97, DeEsser Mono 0.99, RVerb Stereo 0.70, Sibilance-Live Mono 0.75, SSLComp Stereo 1.51 | API-2500 Stereo 3.22, CLA-3A Mono 2.00, H-Delay Stereo 2.29 |
| Others | YS - Reverb-24 1.78, ZL Equalizer 2 0.87 | |

- A negative figure is noise: the baseline drifted by more than the plugin cost. CLA-3A Mono at 2.00 sits on the line between the groups.
- Torpedo Wall Of Sound produced no sound in this test, so its figure need not be what it costs once it does. Boogex's figure includes the reverb it switches on by default.
- Not the realtime-thread load: the tracks were not armed, so anticipative FX processed them ahead of time.
- The method differs from the 2026-10-25 runs (pink noise against a sung vocal, 8 s against 10 s, generator-only baselines against empty-chain runs), so compare figures within one table, not across the two.

## 3. A low-latency tracking project

1. **Record the input, not the output.** Record mode "Record: input" writes the dry signal; the comfort FX live in track FX and on returns, never in input FX.
2. **Zero PDC on every monitored path.** Track FX, every folder above the input, the master, the cue bus and anything between them. Stock ReaEQ, ReaComp, ReaGate, ReaVerbate, ReaDelay and ReaXcomp reported 0 at default settings. ReaComp's "Pre-comp" control is a lookahead; it stood at its default 0 in that measurement, so read `pdc` again after changing it. Section 2 lists the third-party plugins that reported 0 at their defaults.
3. **No lookahead limiter, linear-phase EQ or oversampling above the inputs.** A safety limiter on a cue bus is ReaComp at a high ratio with a fast attack, not ReaLimit (480 samples).
4. **Cue mixes come from pre-fader sends** into a cue bus with its own hardware output, so the engineer's faders do not change what the singer hears. Pre-fader sends still carry the track FX, so the zero-PDC rule covers the track too.
5. **Comfort effects on returns.** A short plate or room (ReaVerbate) and a slap or eighth-note delay (ReaDelay) on returns fed from the vocal, 100 % wet, settings from [reverb.md](reverb.md).
6. **Mixing chains stay bypassed while tracking.** On REAPER 7.82 a bypassed or offline FX adds no latency, so a mixing chain can wait in the project, bypassed, until the session is over.
7. **Know where the monitored signal comes out before arming.** A live microphone monitored through REAPER into loudspeakers in the same room feeds back. Read which hardware outputs REAPER has enabled and ask the user what they feed; do not arm a microphone track or turn its monitoring on until the user has confirmed. For a measurement that needs armed tracks, mute the master: the FX still run and the load is the same (section 5).
8. **Interface latency is the floor.** Halving the buffer in the driver's control panel lowers it; whether the computer holds that at full session load is a test for the user.

## 4. Settings checked on REAPER 7.82

| Setting | Where | Finding |
|---|---|---|
| Anticipative FX processing | Preferences, Audio, Buffering (global) | On during every measurement: the armed, monitored track added 0 samples. Armed tracks run in realtime |
| Chain PDC mode | Per FX chain, `chain_pdc_mode` | 2 (ignore PDC) stops compensation of that chain; the FX still delays its own output. It took a pre-FX send out of the track's alignment and left the track's output unchanged |
| PDC auto-bypass | Preferences, Audio (global, REAPER 7.34 and later), `pdcautobypassms` | Bypasses FX above a PDC threshold on record-armed tracks, by REAPER's help text. Off on the measured system; not tested here. Global: the user decides |
| Input FX versus track FX | Track | Both add their PDC to the monitored input; only input FX end up in the file |
| Bypass versus offline | FX | Both remove the FX's latency from the path |

## 5. Realtime load: what arming costs

Latency is one budget; the other is whether each block finishes in time. At 64 samples and 48 kHz a block lasts 1.33 ms. Measured on a 63-track tracking project with one base plugin per folder level, Ryzen 7 5700X3D (8 cores, 16 threads), reading "RT CPU" and "RT longest-block" from REAPER's Performance Meter (2026-11-09):

| Live state | RT CPU | RT longest-block |
|---|---|---|
| Nothing armed | 4 % | 0.30 to 0.41 ms |
| 3 vocals armed and monitored | 31 % | 0.85 to 0.99 ms |
| 4 instruments armed, monitoring off | 41.5 % | 0.97 to 1.35 ms |
| All 7 | 45 to 53 % | 1.3 to 1.9 ms, RT xruns stayed 0 |

- **Arming alone moves a path to the realtime thread**: the armed track, every folder above it and every return it sends to. Monitoring off does not keep them on anticipative processing. With nothing armed the same project ran at 4 %.
- **RT CPU is the sum of the FX on those paths.** It matched the per-instance cost of the plugins on the armed paths, measured one plugin at a time in a scratch tab with pink noise (Scheps Omni Channel 2 3 to 5.5 % at the user's settings, SSL 4K B 3.2 to 3.4, Nectar 4 Delay 8.5, Pro-C 3 3.3, Pro-Q 4 about 1.2, kHs Reverb about 2.8, each in % of one core). To lower it, count the FX on every armed path and lighten those; disarming a track, or removing the send that ties a return to it, takes a whole chain off the realtime thread.
- **The Performance Meter's per-track "FX CPU" column does not show this.** It read about 0.2 % for chains that cost several percent of a core, because it is a share of all cores.
- **Muting the master did not change the load**, and SoundID Reference in the monitoring FX made no measurable difference. A muted master is the safe way to test with live microphones in the room.

**"Allow live FX multiprocessing"** (Preferences, Audio, Buffering; `syncsmpmax2` in reaper.ini) spreads the armed paths over several cores. REAPER's own note beside it reads "may reduce performance at low latencies", and it is off by default. Same project, all 7 tracks armed and monitored, master muted:

| Live FX multiprocessing | RT CPU | RT longest-block |
|---|---|---|
| Off | 50 to 51 % | 1.49 to 1.62 ms, over the 1.33 ms block |
| 4 CPUs | 34 to 35 % | 0.90 to 1.24 ms |
| 8 CPUs | 30 to 31 % | 0.80 to 1.03 ms |
| 16 CPUs | 29 to 30 % | 0.75 to 1.02 ms |
| 8 CPUs, transport playing | 29 to 32 % | 0.73 to 1.25 ms, one 1.79 ms spike of unknown cause, RT xruns 0 |

It paid because the session had four independent live branches (percussion, bass, guitar, vocals); a session with one live chain has nothing to spread. The number of physical cores was enough: 16 did no better than 8. It is a global preference: back up reaper.ini, change it only when the user asks, and measure before and after.

"Close audio device when stopped and application is inactive" has a sub-option, "...when inactive and tracks are record armed". With the sub-option off, an armed session keeps the device open while the user works in another window; read both before advising the user to turn the first one off for a live session.

## 6. Scanning a whole plugin library

To offer the user only what is safe on a monitored path, 1,307 plugins were measured at their default settings (2026-10-26). Loading that many plugins is the risk, not the measuring:

- **Run each scanned plugin in its own process.** REAPER's "Dedicated process" mode is a line `<file>=2 1` per plugin in `reaper-vstbridge64.ini`; REAPER applies it at once, with no restart. Back the file up, add the lines for the scan, restore it afterwards and compare the bytes. A plugin that crashes after loading then takes down its host, not REAPER, and REAPER's memory stays flat. Loaded natively, the same scan grew REAPER to 14 to 18 GB and crashed it.
- **It does not contain a plugin that dies while loading**: REAPER then tries it natively. One metering plugin with missing files called `abort()` this way and had to be skipped by name. Keep a skip list and resume after it.
- **Several plugins of one vendor starting in parallel processes can deadlock REAPER's main thread** (seen with Arturia and with iZotope RX). Scan in small batches, with a watchdog that ends the hosts of a batch that stops answering.
- **Ask the user not to touch REAPER while it runs**, work in a scratch tab, and tell them first: a crash loses whatever is unsaved in their other tabs. Check `IsProjectDirty` on every tab before starting.
- **The measurement.** Per plugin: `pdc` read three times with the audio engine running, and the delay of a 256-sample chirp sent once per 48000 samples, found by cross-correlation against an empty reference track in the same cycle, in two runs. Effective latency is the larger of the two. Cross-correlation suits clean paths; use the onset when a dry path arrives before the processed one, the impulse arrival for amplifiers and distortion, the earlier of onset and correlation for modulation effects, and the reported value for instruments and for reverbs and delays that are 100 % wet.
- **The result describes one machine.** Keep the scan and any inventory built from it out of the repository (gitignored); only general findings belong in these documents.
- **Folders in the FX browser** made from the result live in `reaper-fxfolders.ini`. Back it up, leave the user's own folders as they are, and say that new folders show only after REAPER restarts.

## 7. What is installed

Suggest and insert only plugins the user has. Before recommending one, read the library instead of assuming:

| File in REAPER's resource folder | Holds | Line format |
|---|---|---|
| `reaper-vstplugins64.ini` | VST2 and VST3, 64-bit | `file[<shellid>]=hash,uid,Name (Vendor)`, with `!!!VSTi` after an instrument's name |
| `reaper-vstplugins.ini` | 32-bit plugins, run through REAPER's bridge | the same |
| `reaper-clap-win64.ini` | CLAP | `id=flags`, a vertical bar, then `Name (Vendor)` |
| `reaper-jsfx.ini` | JSFX | `NAME path "JS: ..."` |
| `reaper-fxtags.ini` | the `[developer]` and `[category]` tags | one plugin per line |

To merge the formats into one list, match by name with the vendor prefix removed (some VST2 names carry the vendor in front, as in "FabFilter Pro-Q 3") and by vendor without "Inc." or "LLC". The vendor is the last bracket that is not a note such as "(Mono)", "(x86)" or "(10ch)". One library came to 2,073 plugins this way, 1,828 of them third-party. A plugin that exists only in the 32-bit list runs bridged; say so before suggesting it for a low-latency session. If the user keeps their own export of the list, that file is the newest truth: read it first, and change nothing in it.

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
| CPU of one plugin | Bridge plus a process CPU sample from outside REAPER, in a scratch tab: 8 tracks, each carrying the plugin and fed by a JS Pink Noise Generator, master muted, not armed; subtract the mean of generator-only runs and divide by 8. Expect about ±1 % per instance (section 2) |
| Realtime load (section 5) | No API reads RT CPU or RT longest-block: open View, Performance Meter and read it from a screenshot, several readings per state. Arm and monitor with the master muted (`B_MUTE` on the master), and put every arm, monitor and mute flag back afterwards |
| Which paths are on the realtime thread | Bridge: for each track with `I_RECARM` = 1, walk `GetParentTrack` to the top and follow its sends (`GetTrackSendInfo_Value`, `P_DESTTRACK`); list the FX on all of them |
| Live FX multiprocessing | Read `syncsmpmax2` in reaper.ini. Changing it is the user's decision: with their go-ahead, back up reaper.ini and set it in Preferences, Audio, Buffering |
| Which hardware outputs exist and where the master goes | Bridge: `GetNumAudioOutputs`, `GetOutputChannelName`, the master's hardware sends. Where they lead in the room is the user's answer |
| Scan the library, list what is installed (sections 6 and 7) | Bridge in a scratch tab plus the ini files; results stay gitignored |
| Driver buffer, enabled hardware outputs, anticipative FX, PDC auto-bypass, live FX multiprocessing | The user, in the driver's control panel and in REAPER's Preferences |
