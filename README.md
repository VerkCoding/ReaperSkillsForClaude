# REAPER for Claude

Claude works inside REAPER as an audio engineer: mixing, mastering, MIDI, FX,
rendering, and real DSP measurement.

One Claude plugin — two skills and an MCP server with 59 REAPER tools —
installed the same way on Claude Code, Claude Desktop and claude.ai.

```
You:    Master this to -14 LUFS, keep the transients.
Claude: [measures the actual loudness, sets the chain, renders, measures again]
```

| | Claude Code | Claude Desktop | claude.ai (web) |
| --- | --- | --- | --- |
| Skills — playbook, setup | yes | yes | yes |
| MCP server, 59 REAPER tools | yes | yes | **no** |
| Lua file bridge | yes | where shell access exists | **no** |

The web app has no local machine to reach, so a local MCP server cannot exist
there. That is the browser, not a broken install.

---

## Install

**Windows.** Everything under the installer is cross-platform — see
[Porting](DOCS.md#porting).

**1.** Clone it:

```bash
git clone <this repo>
```

**2.** Double-click **`RunThisToStart.bat`** and choose **`[1] Install Everything`**.

It backs up your current settings, installs anything missing, sets up the
plugin, and checks it works. Takes several minutes. The only thing it needs from
you is a Claude sign-in, if Claude wasn't already installed.

**3.** Start **REAPER first, then Claude**, and ask:

> Check the current REAPER project info

If it answers, you're done.

> [!NOTE]
> **If REAPER shows a "ReaScript task control" dialog** — *"activate_reapy_server.py
> is running in the background"* — choose **New instance**, never **Terminate
> instances**. Terminating stops both the server and the bridge, which is why
> that choice leaves Claude reporting an error. Nothing is broken.

### Something went wrong?

```powershell
powershell -ExecutionPolicy Bypass -File install\health-check.ps1
```

Every line is `[ok]`, `[warn]` or `[FAIL]`, and each failure carries a fix. Or
just ask Claude — the **reaper-setup** skill covers all of it.

Full guide, options and internals: **[DOCS.md](DOCS.md)**.

---

## Roadmap

Honest state of things. This works end to end on Windows; the gaps below are
real and known.

**Before a wider release**

- [ ] Confirm the cold-start ReaScript dialog fix on a genuinely cold machine.
      The fix is in and unit-tested, but only a first-ever launch exercises it.
- [ ] Move the test suite into the repository. Everything is currently verified
      with throwaway harnesses; a project this size needs them checked in.
- [ ] Push to a git host, so Claude Desktop and claude.ai can add the
      marketplace. Until then Desktop gets the tools but not the skills.

**Wanted**

- [ ] macOS and Linux installers. Only `RunThisToStart.bat` and `install/*.ps1`
      are Windows-specific — the server, launcher, bootstrap, health check,
      bridge and Lua listener are already cross-platform.
- [ ] ARM64 verification. The code paths exist and are architecture-aware, but
      have never run on one.
- [ ] A first-run walkthrough for people who have never used an MCP server.

**Known and deliberate**

- Claude Code is installed through winget rather than from the cache — winget
  extracts it instead of running an installer, and reproducing that bookkeeping
  to save one download is a bad trade.
- REAPER and Claude are never reinstalled if already present, so an existing
  install is never repaired by this. That is on purpose.

---

## Shout-outs

**[bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp)** —
the MCP server here is derived from it. The tool surface started as theirs.

**[python-reapy](https://github.com/RomeoDespres/reapy)** by Roméo Després — the
distant API that lets anything outside REAPER talk to it at all. Most of what
this plugin does rests on it.

**[Cockos](https://www.reaper.fm/)** — for REAPER, and for a ReaScript API broad
enough that a project like this is even possible.

**[EXLOUD/winget-installer](https://github.com/EXLOUD/winget-installer)** — not
used here, but reading it is what turned up the winget version pin. Its author
had already found that pinning to 1.8.1911 avoids an entire dependency, which is
the single change that made the offline install simple.

Licence, third-party components and what the installer downloads:
**[LEGAL.md](LEGAL.md)**.
