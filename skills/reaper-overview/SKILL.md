---
name: reaper-overview
description: |
  Claude works inside REAPER as an audio engineer: mixing, mastering, MIDI, FX, rendering, and real DSP measurement. Controls REAPER via MCP and ReaScript.
license: MIT
metadata:
  version: "1.7.0"
---

# REAPER for Claude

Claude works inside REAPER as an audio engineer: mixing, mastering, MIDI, FX, rendering, and real DSP measurement.

## Skills Included

- **reaper-audio-engineer**: End-to-end audio engineering workflows (gain staging, balancing, EQ, compression, spatial, stem rendering).
- **reaper-core-setup**: REAPER environment validation, Python bridge setup, and diagnostic health checks.
- **reaper-panning**: Stereo placement: pan positions and width, the centre and mono low end, REAPER's pan law and pan modes as measured, and panning conventions for 30 genres.
- **reaper-mcp**: 71 automated MCP tools for project management, track manipulation, routing, automation, markers, item editing, MIDI sequencing, undo, and DSP analysis.

**Routes:** MCP tools first; the Lua bridge for what no tool covers, batched reads and read-backs. Rules in reaper-mcp, "Choosing a route".

For complete documentation and usage instructions, see [DOCS.md](../../DOCS.md).
