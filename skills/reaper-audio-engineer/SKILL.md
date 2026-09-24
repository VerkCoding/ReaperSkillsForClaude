---
name: reaper-audio-engineer
description: >-
  Audio engineering rules for work inside REAPER: mixing, mastering, gain
  staging, EQ decisions, compression and dynamics, panning and stereo width, 
  automation, reverb sends, loudness and LUFS targets, and evaluating measurements.
  Includes target numbers per source and plugin-specific workflows such as
  sonible smart:chain. Use for audio decisions. Use reaper-mcp for calling tools,
  running Lua, or debugging.
---

# REAPER Audio Engineer

This profile performs audio engineering tasks within REAPER using numerical analysis instead of audio playback. Every decision is based on a recorded measurement.

This profile handles the analysis of measurements and the required changes. Refer to **reaper-mcp** for instruction on executing changes in REAPER or handling the Lua bridge.

## Measurement and Modification Process

Measure audio data prior to making changes. Verify audio data after making changes.

- **Confirm parameters prior to modification.** Locate an FX instance by index instead of name. Read a parameter's formatted value before modifying it to ensure the correct index is targeted. 
- **Verify parameters after modification.** Read the formatted value again after setting it. A reported success from a tool does not guarantee the value was applied: three tools were found reporting the requested value back while leaving REAPER untouched, and others accepted out-of-range input that REAPER silently truncated. Refer to [reaper-mcp](../reaper-mcp/SKILL.md) for details.
- **Measure in the right units.** A sample peak is not a true peak, an FFT magnitude is not a band level, and a mono sum is not a stereo measurement. Each of these has produced a confident wrong number. Refer to [Measurement Toolkit](./references/audio-measurement.md#traps-that-produce-confident-wrong-numbers).
- **Perform single modifications.** Band levels are relative. Applying a cut to multiple bands simultaneously alters the overall balance. Execute one change and measure again to isolate the effect.
- **Output numerical values.** Report data such as "-14.2 LUFS-I, true peak -1.1 dBTP". Do not use qualitative descriptions.
- **State ambiguities.** If a measurement is unclear or a parameter mapping is undefined, state this fact. Do not provide information without certainty.

## Measurement Requirements

Measurements must follow specific constraints to be valid:

- **Render buses for measurement.** Audio accessors only operate on items located on their specific track. An FX-only bus will return silence. Render the bus output to measure it.
- **Apply gates prior to averaging.** Silent periods between audio signals will lower the average measurement.
- **Use short sections for testing.** Render short segments (e.g., 20 seconds) to evaluate individual changes. Use full-song renders for final output verification.

## Diagnostic Metrics

Specific data points indicate specific audio characteristics:

- **Crest factor.** A crest factor (true peak minus LUFS-I) between 18-22 dB correlates with a kick drum signal. A crest factor near 35 dB with a low integrated level correlates with a sparse impulse signal, such as a trigger click. EQ adjustments do not alter this.
- **Frequency ratios.** A measurement of +5 dB at 1 kHz combined with -4 to -6 dB from 2-4 kHz indicates an imbalance affecting intelligibility. Modify both frequency ranges to correct the ratio.
- **Formants.** Not all peaks require attenuation. Formants in vocal tracks are natural characteristics. If a peak remains after attenuation and does not increase when boosted, it is inherent to the source. Verify it is not caused by the mastering chain, reverb return, or channel strip before leaving it unaltered.
- **Compressor engagement.** A compressor with a threshold set above the signal peak functions only as a gain stage. Verify gain reduction is occurring.

## Project Verification

Report warnings and discrepancies. Confirm that track counts remain unchanged, no tracks are unintentionally soloed or muted, FX chains are enabled, send levels match intended settings, and render settings and time selections are restored to their initial states.

## Reference Materials

- **[Measurement Toolkit](./references/audio-measurement.md)**: Details on LUFS, true peak, sample reading, band analysis, resonance identification, arrangement mapping, automation writing, and project audits.

The following resources form a sequence. Reference the document corresponding to the current project stage.

- **[Recording and intake](./references/audio-recording.md)**: Instructions for intake of sessions or raw files, checking for clipping, noise floor, phase offsets, timing offsets, and sample rate discrepancies.
- **[Mixing](./references/audio-mixing.md)**: Instructions for levels, EQ, compression, gating, panning, sends, automation, buses, phase repair, timing repair, and frequency masking based on measurements.
- **[Mastering](./references/audio-mastering.md)**: Instructions for mix bus and master bus processing, spectral matching, limiting, LUFS targets, true peak targets, mid/side processing, bit depth, dither, stems, and deliverables.

For instructions on retrieving measurements from REAPER, refer to **reaper-mcp**. The documents [Rendering Secrets](../reaper-mcp/references/rendering.md) and [Plugin Control](../reaper-mcp/references/plugin-control.md) describe potential errors in data collection.

## Topic library

A topic document holds the target numbers and working methods for one subject. The process documents above say how to measure and verify; a topic document says what to aim for. Read the topic before choosing a value in its subject.

| Document | Covers | Read when |
|---|---|---|
| [Gain staging](./references/topics/gain-staging.md) | Levels per source and per stage, meters, headroom, loudness targets per platform | Setting input or bus levels, checking headroom, level-matched A/B, choosing a delivery loudness |
| [Compression](./references/topics/compression.md) | Compressor types, settings per source and bus, sidechain, parallel, de-ess, multiband, genre chains | Adding or adjusting a compressor, limiter or ducking |
| [Reverb](./references/topics/reverb.md) | Reverb parameters and types, send/return set-up, depth, settings per source, tempo-matched pre-delay and decay | Adding space, building reverb buses, fixing a muddy or distant mix |
| [sonible smart:chain](./references/topics/plugin-sonible-smartchain.md) | Gain staging and balance with smart:chain: learn, groups and layers, Auto Level | Only when a track carries `VST3: smartChain (sonible)` |

Rules for topic documents:

- They are written for hands-on work in the REAPER interface. Each ends with "Applying in this plugin", which maps its steps to MCP tools and the bridge. Act through that section; take numbers and reasoning from the body.
- Their numbers are starting points (Tier 3, as defined in [Recording and intake](./references/audio-recording.md)) unless the document cites a standard. Measure before and after, as the process documents require.
- Each number has one owner: levels, meters and loudness belong to gain staging; compressor settings to compression; reverb and its ducking to reverb. A plugin document takes its targets from those. When documents disagree, the owner wins; report the disagreement.
- Steps inside a plugin's own interface, such as smart:chain's learn button, are the user's to perform. Name the step, wait for confirmation, then measure.

## Troubleshooting

Tool failures, hanging calls, or empty renders are routing or setup errors. Use **reaper-mcp** to diagnose the route. Use **reaper-core-setup** if plugin installation or repair is required.
