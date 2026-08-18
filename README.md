# REAPER for Claude - WIP

Claude works inside REAPER as an audio engineer assistant: mixing, mastering, MIDI, FX,
rendering, and real DSP measurement.

One Claude plugin: three skills and an MCP server with 58 REAPER tools,
installed the same way on Claude Code, Claude Desktop and claude.ai.

```
You:    Master this to -14 LUFS, keep the transients.
Claude: [measures the actual loudness, sets the chain, renders, measures again]
```

| | Claude Code | Claude Desktop | claude.ai (web) |
| --- | --- | --- | --- |
| Skills: setup, channel, craft | yes | yes | yes |
| MCP server, 58 REAPER tools | yes | yes | **no** |
| Lua file bridge | yes | where shell access exists | **no** |

The web app has no local machine to reach, so a local MCP server cannot exist
there. That is the browser, not a broken install.

---

## Install

**Windows.** Everything under the installer is cross-platform. See
[Porting](DOCS.md#porting).

**1.** Clone it:

```bash
git clone https://github.com/VerkCoding/ReaperSkillsForClaude.git
```

**2.** Double-click **`RunThisToStart.bat`** and choose **`[1] Install Everything`**.

It backs up your current settings, installs anything missing, sets up the
plugin, and checks it works. Takes several minutes. The only thing it needs from
you is a Claude sign-in, if Claude wasn't already installed.

**3.** Start **REAPER first, then Claude**, and ask:

> Check the current REAPER project info

If it answers, you're done.

> [!NOTE]
> **If REAPER shows a "ReaScript task control" dialog** saying
> *"activate_reapy_server.py is running in the background"*, choose **New
> instance**, never **Terminate instances**. Terminating stops both the server
> and the bridge, which is why that choice leaves Claude reporting an error.
> Nothing is broken.

### Something went wrong?

```powershell
powershell -ExecutionPolicy Bypass -File install\health-check.ps1
```

Every line is `[ok]`, `[warn]` or `[FAIL]`, and each failure carries a fix. Or
just ask Claude, since the **reaper-core-setup** skill covers all of it.

Full guide, options and internals: **[DOCS.md](DOCS.md)**.

---

## Roadmap

Honest state of things. This works end to end on Windows; the gaps below are
real and known.

**Before a wider release**

- [ ] Finish Reaper-MCP skills
- [ ] Finish Reaper-Audio-Engineer skills
- [ ] Links Everything with Reaper-Core-Setup skills

**Wanted**

- [ ] Make Tutorial Video
- [ ] ON GOD FIX EVERY BUG
- [ ] Linux && MacOS

**Known and deliberate**

- Claude Code is installed through winget rather than from the cache. winget
  extracts it instead of running an installer, and reproducing that bookkeeping
  to save one download is a bad trade.
- REAPER and Claude are never reinstalled if already present, so an existing
  install is never repaired by this. That is on purpose.

---

## Shout-outs

This plugin stands on three REAPER projects that got there first.

**Ideology of: [xDarkzx/Reaper-MCP](https://github.com/xDarkzx/Reaper-MCP)**
(Apache-2.0). The idea that an AI should reach REAPER through a *broad* tool
surface rather than a handful of calls: composition, mixing, mastering, QC,
ReaScript automation, all of it. That ambition set the target here.

**Toolkit by: [bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp)**
(MIT). The MCP server here is derived from it. The tool surface started as
theirs, and much of it still is.

**Inspired by: [DevWesC/Reaper-Claude-MCP](https://github.com/DevWesC/Reaper-Claude-MCP)**
(MIT). The Claude-and-REAPER pairing, and the PowerShell-installer approach to
making it actually land on a Windows machine.

And the ground everything stands on:

**[python-reapy](https://github.com/RomeoDespres/reapy)** by Roméo Després. The
distant API that lets anything outside REAPER talk to it at all. Most of what
this plugin does rests on it.

**[Cockos](https://www.reaper.fm/)**, for REAPER and for a ReaScript API broad
enough that a project like this is even possible.

**[EXLOUD/winget-installer](https://github.com/EXLOUD/winget-installer)**. Not
used here, but reading it is what turned up the winget version pin. Its author
had already found that pinning to 1.8.1911 avoids an entire dependency, which is
the single change that made the offline install simple.

Licence, third-party components and what the installer downloads:
**[LEGAL.md](LEGAL.md)**.
