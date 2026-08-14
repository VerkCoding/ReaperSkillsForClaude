# REAPER for Claude

Claude works inside REAPER as an audio engineer: mixing, mastering, MIDI, FX,
rendering, and real DSP measurement.

One Claude plugin — two skills and an MCP server with 59 REAPER tools —
installed the same way on Claude Code, Claude Desktop and claude.ai.

| | Claude Code | Claude Desktop | claude.ai (web) |
| --- | --- | --- | --- |
| Skills — playbook, setup | yes | yes | yes |
| MCP server, 59 REAPER tools | yes | yes | **no** |
| Lua file bridge | yes | where shell access exists | **no** |

The web app has no local machine to reach, so a local MCP server cannot exist
there. That is the browser, not a broken install.

**Windows only for the installer.** Everything under it is cross-platform — see
[Porting](#porting).

---

## Install

```bash
git clone <this repo>
```

Double-click **`RunThisToStart.bat`** and choose **`[1]`**.

| | |
| --- | --- |
| **`[1]` Install Everything** | Close apps → snapshot → install what's missing → first run → configure → health check |
| **`[2]` Revert Everything** | Restore that snapshot, remove what `[1]` added |
| **`[3]` Prepare Offline Files** | Download what `[1]` needs and install nothing |

`[1]` is the whole thing. `[3]` is for a machine that is *not* the one being set
up: fill the cache where there is a connection, copy the folder across, and `[1]`
runs from disk. See [The download cache](#the-download-cache).

### What it installs

From the winget community source, using each vendor's own installer
(reaper.fm, claude.ai, git-scm) rather than a Store package.

| Package | If already installed |
| --- | --- |
| Python 3.12 | upgraded |
| Git | upgraded |
| REAPER | **skipped entirely** |
| Claude Desktop | **skipped entirely** |
| Claude Code | **skipped entirely** |

REAPER and Claude are skipped rather than reinstalled on purpose: a reinstall
risks a REAPER resource folder holding years of presets and FX chains, and
Claude's local history. Nothing here is worth that.

### Outside this folder

| Thing | Where |
| --- | --- |
| Dependency virtualenv | `%USERPROFILE%\.reaper-for-claude\venv` |
| `python-reapy` only | your base Python's user site-packages — [why](#two-interpreters) |
| `claude_bridge.lua`, `enable_reapy.py` | `<REAPER>\Scripts\` |
| `__startup.lua` | `<REAPER>\Scripts\` — one `dofile()` **appended, never overwritten** |
| Distant API settings | `reaper.ini`, `reaper-kb.ini`, `reaper-extstate.ini` |
| MCP server entry | `claude_desktop_config.json` — backed up before each write |

### What it asks of you

**Save your work first.** `[1]` closes REAPER and Claude, because both hold
their settings in memory and write them back on exit — anything written
underneath a running instance is silently discarded.

It asks the way the X button does, so REAPER's *"save changes?"* prompt still
appears. **Your own session is never force-quit.** The one exception is the
first-run step, where the installer opened the application itself seconds
earlier and there is nothing to lose.

**Signing in to Claude is required**, not optional — a freshly installed Claude
has no account, and without one it cannot load the plugin. Leaving without one
takes typing `SKIP`.

| If you | Then |
| --- | --- |
| Press Enter without closing REAPER | The REAPER step is **skipped, not attempted**. Reported at the end; close it and re-run `[1]`. |
| Press Enter without closing Claude | Continues, and warns Claude may revert the MCP entry. Re-run `[1]` if the tools are missing. |
| Close Claude without signing in | It reopens and keeps waiting. |
| Never sign in | Bounded — after six tries it continues and says loudly what is missing. |
| Run this from a terminal inside Claude | Detected; it refuses to close its own host and says so. |

Everything unfinished is listed at the end, and re-running `[1]` is safe.

### What `[2]` restores

`[1]` snapshots every file it can touch before its first write, into
`%USERPROFILE%\.reaper-for-claude\backups\original\`. Files that existed are
copied; files that did **not** are noted as absent — so reverting deletes
exactly what the setup created and nothing else.

`[2]` restores those, removes the virtualenv, and unregisters the plugin. It
**does not uninstall REAPER, Claude, Python or Git**; it reports which ones `[1]`
installed and leaves that to you. Projects, media, presets and conversations are
never touched.

**One narrow exception.** If `[1]` installed Claude and Claude was never signed
into, `[2]` moves its profile aside — restoring config files cannot fix a Claude
that will not start. Both conditions come from the snapshot and both are
required, so the directory provably holds nothing you made. It is **renamed, not
deleted**.

---

## First run

Start **REAPER first, then Claude**, and ask:

> Check the current REAPER project info

> [!NOTE]
> **If REAPER shows a "ReaScript task control" dialog** — *"activate_reapy_server.py
> is running in the background"* — choose **New instance**, never **Terminate
> instances**.
>
> The dialog is modal, so while it is open REAPER runs no background scripts at
> all. Terminating stops the reapy server *and* the Lua bridge, which is why that
> choice leaves Claude reporting an MCP error. Nothing is broken; the server was
> only asked to start twice.
>
> The setup now waits for REAPER to publish its server port instead of asking it
> to start again, so this should not appear. It is documented because the cost of
> the wrong button is high.

Running Lua directly:

```bash
reaper-bridge --code 'return reaper.GetAppVersion()'
```

`reaper-bridge` is on the Bash tool's PATH whenever the plugin is enabled.

---

## Troubleshooting

**Start here:**

```powershell
powershell -ExecutionPolicy Bypass -File install\health-check.ps1
```

Every line is `[ok]`, `[warn]` or `[FAIL]`, and each failure carries a `->` fix.
It checks the layout, whether the server can *actually start*, the distant API,
the bridge heartbeat, a live connection when REAPER is running, both Claude
surfaces, and whether an older install is still loading in parallel. Or just ask
Claude — the **reaper-setup** skill covers all of it.

| Symptom | Cause |
| --- | --- |
| No REAPER tools at all | On claude.ai, expected. Otherwise the server did not start — run the health check. |
| Tools fail with a socket error | REAPER is not running, or the distant API was never configured. A *persistent* failure means configuration. |
| Bridge times out | REAPER is not running `claude_bridge.lua`. Check `status.txt` in the bridge directory. |
| `PARSE_ERROR` on byte one | Lua reached the bridge with a UTF-8 BOM. Use `--code`, or `--lua-file` so it is stripped. |
| Renders are silent | The `offlineinact` preference — see [rendering.md](skills/reaper-audio-engineer/references/rendering.md). |
| Everything appears twice | An older install is still loading from `~\.claude\skills\`. Delete `reaper-mcp` and `reaper-ai-engineer-skill`. |
| Edits here do nothing | You installed from the marketplace, which is a copy. See [Editing](#editing-the-plugin). |

**`No module named 'mcp.server.fastmcp'`** — `mcp` 2.0+ got installed and removed
the FastMCP API. `requirements.txt` pins `mcp<2.0.0`; an older environment needs
`python scripts\bootstrap.py --recreate`.

**`import reapy` fails** — rebuild, optionally on a different Python:
`py -3.12 scripts\bootstrap.py --recreate`.

**Socket error / `WinError 10053`** — run `python reaper\enable_reapy.py --check`.
A web interface on **2306** is the problem: `--repair`, then restart REAPER.

**Claude Desktop reverted to an old MCP entry** — Desktop holds
`claude_desktop_config.json` in memory and rewrites it from its own state, the
way REAPER does with `reaper.ini`. Quit it fully including the tray icon, run
`configure-plugin.ps1 -Only claude`, then start it again.

**The marketplace stopped resolving** — a local marketplace stores an absolute
path, so moving this folder breaks it. Re-add:
`claude plugin marketplace add <new path>`.

**The dependency install is very slow** — expected, and it is the size: **477 MB
across 12,212 files**, of which `librosa`'s chain is 345 MB. A Sandbox makes it
worse three ways — no pip cache to reuse, Defender scanning every extracted
file, and a virtual disk at its worst with many small files. It runs once; the
virtualenv lives outside the plugin and survives updates.

If it is **stuck** rather than slow, look for `Building wheel for llvmlite` —
that means pip is compiling LLVM, which takes about an hour and usually fails.
Bootstrap passes `--only-binary=:all:` to prevent it. The fix is an older
interpreter: `py -3.12 scripts\bootstrap.py --recreate`.

---

## How it works

### Layout

```
ReaperSkillsForClaude/              # marketplace root AND plugin root
├── .claude-plugin/                 #   plugin.json + marketplace.json
├── skills/                         # documentation only, no executables
│   ├── reaper-audio-engineer/      #   the engineering playbook
│   └── reaper-setup/               #   install, diagnose, repair
├── scripts/                        # every cross-platform executable
│   ├── launch_server.py            #   picks an interpreter, then serves
│   ├── bootstrap.py                #   builds the dependency virtualenv
│   ├── bridge.py                   #   the file-bridge client
│   └── doctor.py                   #   the health check itself
├── bin/reaper-bridge               # puts bridge.py on the Bash tool's PATH
├── src/reaper_mcp/                 # the MCP server
├── reaper/                         # claude_bridge.lua, enable_reapy.py
└── install/                        # Windows setup, PowerShell
```

Three rules keep it navigable:

- **`skills/` is documentation and nothing else.**
- **`scripts/` is every cross-platform executable**; `install/` is the
  Windows-only PowerShell that drives them.
- **The MCP server is declared inline in `plugin.json`.** A root `.mcp.json`
  would also be read as a *project* config and start the server twice.

### Two interpreters

REAPER does not run Python as a subprocess. It loads a Python **shared library**
and runs ReaScripts inside its own process. A virtualenv contains no such
library, so REAPER cannot use one.

So two interpreters must import `reapy`, and only one is ours:

| Interpreter | Runs | Needs |
| --- | --- | --- |
| The virtualenv | the MCP server, outside REAPER | all of `requirements.txt` |
| REAPER's embedded Python | `activate_reapy_server.py` | `reapy` |

Miss the second and the setup looks complete while nothing connects. So
`bootstrap.py` also installs `python-reapy` into the base Python's user
site-packages — a deliberate, narrow exception. It needs only `psutil` and
`typing-extensions`; the compiled numeric stack that makes a global install
worth avoiding stays in the virtualenv.

**Your `PATH` is not changed** unless there is no usable Python 3 on it. Nothing
here wants the *default* interpreter: the server uses the virtualenv by absolute
path, REAPER uses `pythonlibpath64` from `reaper.ini`, and the configure step
uses `py -3.12`, which ignores `PATH` order. `launch_server.py` is deliberately
written in the Python 2/3 intersection so even a 2.7 `python` can parse it and
hand over.

### Starting the server

`plugin.json` runs `scripts/launch_server.py` with whatever `python` is on PATH —
frequently the wrong one. A stdio server that dies during startup surfaces only
as *"server failed to connect"*, so the launcher:

1. **Probes for an interpreter that can import the dependencies** — in order:
   `REAPER_MCP_PYTHON`, the managed virtualenv, the current interpreter, then
   `py -3.12` and friends. It tests the imports rather than comparing versions.
2. **Re-launches itself** under that interpreter.
3. **Falls back to a one-tool diagnostic server** when nothing works, so Claude
   gets `reaper_setup_status` instead of a dead connection.

A probe that **times out** is treated differently from one that fails. A missing
module answers in milliseconds; a cold first import of numpy, mcp and reapy can
exceed the timeout on a fresh machine. The managed virtualenv is kept when its
probe merely times out — `bootstrap.py` built and verified it, so slow is the
only thing that can mean.

Dependencies are never installed at startup: a cold install takes minutes, and a
server that misses its initialize timeout is dropped as failed.

### Reaching REAPER

Two independent routes, so losing one leaves the other working.

- **The MCP server** connects through reapy's distant API — Python ReaScript on,
  a web interface on port **2307**, the `activate_reapy_server` action
  registered, and its id recorded. All four are handled by
  `reaper/enable_reapy.py`, all idempotent.
- **The file bridge** runs arbitrary Lua inside REAPER through a watched
  directory, for what the API cannot express: offline DSP measurement, finding a
  plugin parameter by its formatted value, and the silent-render workaround.

**Port 2306 is not a web interface.** It is the socket reapy's own server binds.
An earlier setup added a web interface there, which takes the port first and
produces `WinError 10053`. `--repair` removes it, and the installer runs that
before configuring.

Before importing reapy, the server **waits for REAPER to publish its server
port** rather than letting reapy find it missing and perform the action again —
which is what produced the task-control dialog above. The wait is a plain read
with no side effects, and it is skipped entirely when REAPER is not running,
because then reapy raises instead of prompting and there is nothing to prevent.

### The download cache

**`[1]` installs through `downloadCache\` and fills it as it goes.** Already
there means install from disk; not there means fetch it *into* the cache and
install from disk anyway. The second run costs nothing.

That is why `winget install` is not what runs: it downloads into its own temp
directory and deletes it, so a machine that gets wiped and re-run re-downloaded
everything every time. `winget download` is the same fetch from the same source,
kept, with the manifest beside it — which is also how the installer knows the
silent switches without guessing.

- **Files you already have are used where they lie.** Name and place are matched
  loosely on purpose: nobody renames the winget bundle, and the folder shared
  into a Sandbox is usually the one *containing* the clone. Four directories are
  searched, nearest first — `downloadCache\`, the plugin folder, its parent, and
  a `downloadCache\` there. Read-only; downloads are written to `downloadCache\`
  and nowhere else.
- **It is only ever an optimisation.** Absent, empty or unreadable, `[1]`
  behaves exactly as it would have and downloads what it needs.
- **Only for what is missing.** An installed application is never replaced by a
  cached copy, which could as easily be a downgrade.
- **Claude Code is the exception** — winget extracts it rather than running an
  installer. It is downloaded once, found unusable from disk, and its manifest
  kept as a note so later runs skip it.
- **Gitignored**, and safe to delete at any time.

### Logs

Every run writes one, to `%USERPROFILE%\.reaper-for-claude\logs\`. Timestamped,
level-tagged plain ASCII, so it greps and pastes anywhere:

```
21:54:21  STEP  Applications
21:54:21  OK    Git ready (from downloadCache).
21:54:21  WARN  REAPER failed (exit 1); retrying once...
21:54:21  FAIL  Claude Code: winget exited 1.
```

`[1]` writes two. `setup-<time>.log` is that structured trace — *what did the
setup do*. `transcript-<time>.log` is the raw capture including winget's and
pip's own output — *what did everything else say*. Read the first.

---

## Reference

### Scripts

The menu is a front end; every step in `install\` runs on its own.

| Script | Does |
| --- | --- |
| `install-everything.ps1` | All of `[1]`. `-SkipApps` configures only. |
| `revert-everything.ps1` | All of `[2]`. `-From <dir>` picks another snapshot. |
| `fill-download-cache.ps1` | All of `[3]`. `-Force` re-fetches; `-Consolidate` copies stray files in. |
| `configure-plugin.ps1` | Dependencies, REAPER bridge and Claude. Installs no applications. `-Only python\|reaper\|claude`, `-Link`, `-Force`. |
| `install-winget.ps1` | Install or repair winget itself. |
| `install-python.ps1` | Python only, winget or direct download. |
| `backup-restore.ps1` | `-Backup`, `-Restore`, `-List`. |
| `health-check.ps1` | Changes nothing. Run it any time. |
| `lib-console.ps1` | Dot-sourced. How every script talks, and the log it writes. |
| `lib-app-control.ps1` | Dot-sourced. Finding, closing and first-running REAPER and Claude. |
| `lib-download-cache.ps1` | Dot-sourced. The cache and the winget bootstrap. |

`health-check.ps1` is a thin wrapper around `doctor.py`. Two health checks that
can disagree about what "working" means are worse than one.

### Environment variables

| | |
| --- | --- |
| `REAPER_MCP_PYTHON` | Interpreter to run the server under. Wins over everything. |
| `REAPER_MCP_REAPER_PYTHON` | Interpreter REAPER embeds, written to `reaper.ini`. |
| `REAPER_MCP_DATA_DIR` | Where the virtualenv lives. Default `~\.reaper-for-claude`. |
| `REAPER_MCP_PLUGIN_ROOT` | Plugin root, set by the host. |

### Pinned versions

| | |
| --- | --- |
| **Python** | `3.12` |
| **winget** | `v1.8.1911` — `microsoft/winget-cli`, 252 MB |
| **UI.Xaml** | `v2.8.6` — `microsoft/microsoft-ui-xaml`, 5 MB |
| **VCLibs** | `14.00 Desktop` — `aka.ms/Microsoft.VCLibs.<arch>.14.00.Desktop.appx`, 7 MB |

> [!WARNING]
> **reapy 0.10.0 empties `reaper.ini` on Python 3.13+.** Python 3.13 added
> unnamed sections to `configparser`; reapy calls `.lower()` on each section name
> and raises *partway through rewriting the file*, leaving it **zero bytes**.
> Every REAPER preference and path is gone, and nothing in the error mentions
> `reaper.ini`.
>
> Three guards: `enable_reapy.py` refuses to configure on 3.13+; it copies the
> file first and restores it if it ends up smaller; and the installer picks a
> 3.12-or-older interpreter for that step alone. The server itself runs fine on
> 3.14, which is why the two are chosen separately.

**winget is pinned because 1.9+ added a dependency 1.8 does not have.** The
current release declares `Microsoft.WindowsAppRuntime.1.8`, which means either a
102 MB setup executable or a framework package Microsoft does not publish
standalone — and getting it wrong is a `0x80073CF3` that reads like a corrupt
download. 1.8.1911 predates it: bundle plus two appx files, one
`Add-AppxPackage` call.

Packages are named per architecture (`VCLibs.x64.appx`, `VCLibs.arm64.appx`), so
a cache copied to a different architecture reads as empty rather than as
complete-but-undeployable.

### No winget?

It ships with App Installer, absent on a fresh Windows Server, on images built
without the Store, and inside Windows Sandbox. `[1]` installs it:

1. **Register an App Installer that is already provisioned** — instant, offline.
2. **Deploy the packages directly** — register VCLibs and UI.Xaml, then the
   bundle with both as `-DependencyPath`. Needs nothing but HTTPS, and nothing at
   all once cached.
3. **PowerShell Gallery bootstrap**, as the fallback for a machine where step 2
   is refused by policy.

Downloads use `curl.exe` (in Windows since 1803) with `Invoke-WebRequest` behind
it, `--fail` so an HTTP error cannot be saved as the package, and a size floor —
which catches what `--fail` cannot, since a captive portal and a retired `aka.ms`
link both answer `200`.

---

## Editing the plugin

Installing from a marketplace **copies** this folder into a versioned cache, so
edits here do not reach Claude until you bump `version` in both manifests and
reinstall. For live editing:

```powershell
powershell -ExecutionPolicy Bypass -File install\configure-plugin.ps1 -Only claude -Link
```

That creates a junction at `~\.claude\skills\reaper-for-claude` pointing here,
which loads in place.

**Use one route or the other.** They share a plugin name, Claude Code resolves it
in favour of the marketplace, and it skips the link silently — so with both
present you edit and nothing happens, with no error. Uninstalling is not enough
either; the marketplace **entry** reserves the name:

```powershell
claude plugin uninstall reaper-for-claude@reaper-skills-for-claude
claude plugin marketplace remove reaper-skills-for-claude
```

`-Link` does both for you, and the health check flags the shadowed state.

Changes to a `SKILL.md` apply immediately. Changes to `plugin.json` need
`/reload-plugins` or a restart.

## Installing by hand

`[1]` handles both Claude surfaces. Otherwise:

| Surface | How |
| --- | --- |
| **Claude Code** | `/plugin marketplace add <this folder>` then `/plugin install reaper-for-claude@reaper-skills-for-claude` |
| **Claude Desktop / claude.ai** | Customize → Plugins → Personal plugins → **+** → Add marketplace |

Desktop and the web app fetch marketplaces from a **git host**, not a local path,
so the Plugins UI needs this pushed somewhere first. Until then the installer
writes the MCP server straight into `claude_desktop_config.json`, which gives
Desktop the tools immediately — just not the skills.

## Porting

Only `RunThisToStart.bat` and `install/*.ps1` are Windows-specific. The server,
`launch_server.py`, `bootstrap.py`, `doctor.py`, `bridge.py`, `enable_reapy.py`
and the Lua listener are cross-platform, so macOS or Linux support means writing
an installer and changing `command` in `plugin.json` from `python` to `python3` —
not touching any of the logic.

## Credits

The MCP server is derived from
[bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp)
(MIT). REAPER connectivity uses
[python-reapy](https://github.com/RomeoDespres/reapy).
