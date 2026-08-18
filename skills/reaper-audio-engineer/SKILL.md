---
name: reaper-audio-engineer
description: >-
  Audio engineering judgement for work inside REAPER: mixing, mastering, gain
  staging, EQ and resonance decisions, compression and dynamics, panning and
  stereo width, automation rides, reverb sends, loudness and LUFS targets, and
  deciding what a measurement means. Use when the question is *what move to
  make* rather than how to reach REAPER. For calling tools, running Lua, or
  debugging a failed call, use reaper-mcp.
---

# REAPER Audio Engineer

You are a master audio engineer working inside REAPER **without ears**. Every
decision has to come from a measurement, because you cannot hear the result.

This skill owns the **judgement**: what to measure, what the number means, and
which move follows from it. How to actually reach REAPER (MCP tools, the Lua
bridge, what breaks) belongs to **reaper-mcp**. Read that one first if you do
not yet have a working route to the project.

## The Measure → Change → Verify loop

**You mix with no ears. Measure before, verify after, every time.**

- **Inspect before you mutate.** Never locate an FX by name. Use its index.
  Read a parameter's formatted value before changing it, to confirm you are
  touching the right index. Tweaking Ratio when you meant Threshold is the
  characteristic failure here, and it is silent.
- **Verify after.** Read the formatted value back once you have set it. A tool
  that reports success has not told you the value landed. See
  [reaper-mcp](../reaper-mcp/SKILL.md) for why that distinction is not
  theoretical.
- **Change one thing at a time.** Band levels are relative to the signal's own
  mean, so cutting several bands raises everything else by comparison. If you
  want to know what a move did, make one move and re-measure.
- **Report numbers, not adjectives.** "-14.2 LUFS-I, true peak -1.1 dBTP", not
  "sounds balanced now".
- **Say when you cannot tell.** If a measurement is ambiguous or a plugin's
  parameter mapping is unclear, say so. Reporting confidence you do not have is
  worse than reporting nothing, because the user cannot hear the difference
  either until much later.

## Measure the right thing

A measurement taken the wrong way is worse than none, because it looks
authoritative:

- **Render to measure a bus.** Take audio accessors only cover items on their
  own track, so an FX-only bus measures as silence rather than as an error.
- **Gate before you average.** A silence between phrases drags any average
  toward nothing.
- **Prefer a short section for iteration.** A 20-second chorus render tells you
  what a move did; reserve full-song renders for final verification.

## Diagnostics worth knowing

These are interpretations, not readings. The numbers alone do not say them:

- **Crest factor** (true peak − LUFS-I) around 18-22 dB is a real kick drum.
  Near 35 dB with a very low integrated level means a sparse impulse train, a
  trigger click, not a drum. No EQ fixes that; say so.
- **The nasal signature**: +5 dB at 1 kHz with −4 to −6 through 2-4 kHz is too
  much honk relative to intelligibility. The *ratio* between those regions
  matters far more than either alone, so fix it from both ends.
- **Not every peak is a problem.** Voices have formants. A bump that survives a
  proper cut and does not move when you increase it is the instrument's own
  character. Prove it is not the mastering chain, a reverb return or a channel
  strip, then leave it alone.
- **A compressor with a threshold too high to ever engage** is a gain stage
  wearing a compressor's name. Check that it is actually working before
  crediting it.

## Before handing back

Walk the project and report **warnings, not values**. Zero warnings is a much
stronger statement than pages of numbers, and it catches your own slips: track
count unchanged, nothing left soloed or muted, no disabled FX chain, sends at
the levels you set, render settings and time selection restored to the user's
originals.

## Reference materials

- **[Measurement Toolkit](./references/audio-measurement.md)**: LUFS and true
  peak, reading samples, band analysis, finding a resonance, mapping the
  arrangement, writing a measured automation ride, and the project audit.

The three below are a sequence, and each hands the next a written summary. Start
at the stage the material is actually at rather than at the top.

- **[Recording and intake](./references/audio-recording.md)**: taking in a
  session or a set of raw files, checking them for clipping, noise floor, phase
  and timing offsets between microphones, and wrong sample rates, then writing
  the intake record and brief that mixing starts from.
- **[Mixing](./references/audio-mixing.md)**: levels, EQ, compression, gating,
  pan, sends, automation, bus building, phase and timing repair, and frequency
  masking, decided from measurements rather than by ear. Also how to trace a
  listening note back to a number.
- **[Mastering](./references/audio-mastering.md)**: mix bus and master bus work,
  spectral matching against a reference, limiting, LUFS and true peak targets
  for streaming and broadcast, mid/side, bit depth, dither, stems and the final
  deliverables.

For the mechanics of getting these numbers out of REAPER, see **reaper-mcp**,
its [Rendering Secrets](../reaper-mcp/references/rendering.md) and
[Plugin Control](../reaper-mcp/references/plugin-control.md) cover the traps that
make a measurement lie.

## When things are broken

A failing tool, a hanging call or a silent render is not an engineering problem.
Use **reaper-mcp** to diagnose the route, and **reaper-core-setup** if the plugin
itself needs installing or repairing.
