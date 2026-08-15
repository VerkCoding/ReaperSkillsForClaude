# REAPER for Claude: documentation

Everything past the [quick start](README.md#install): the full install, what the
setup does to your machine, how the pieces fit, and the reference tables.

- [Installing](#installing)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Reference](#reference)
- [Development](#development)

---

## Installing

`RunThisToStart.bat` is a menu over three scripts.

| | |
| --- | --- |
| **`[1]` Install Everything** | Close apps → snapshot → install what's missing → first run → configure → health check |
| **`[2]` Revert Everything** | Restore that snapshot, remove what `[1]` added |
| **`[3]` Prepare Offline Files** | Download what `[1]` needs and install nothing |

`[3]` is for a machine that is *not* the one being set up: fill the cache where
there is a connection, copy the folder across, and `[1]` runs from disk. See
[The download cache](#the-download-cache).

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

### What it touches outside this folder

| Thing | Where |
| --- | --- |
| Dependency virtualenv | `%USERPROFILE%\.reaper-for-claude\venv` |
| `python-reapy` only | your base Python's user site-packages ([why](#two-interpreters)) |
| `claude_bridge.lua`, `enable_reapy.py` | `<REAPER>\Scripts\` |
| `__startup.lua` | `<REAPER>\Scripts\`, one `dofile()` **appended, never overwritten** |
| Distant API settings | `reaper.ini`, `reaper-kb.ini`, `reaper-extstate.ini` |
| MCP server entry | `claude_desktop_config.json`, backed up before each write |
| Snapshots and logs | `%USERPROFILE%\.reaper-for-claude\` |

### What it asks of you

**Save your work first.** `[1]` closes REAPER and Claude, because both hold
their settings in memory and write them back on exit. Anything written
underneath a running instance is silently discarded.

It asks the way the X button does, so REAPER's *"save changes?"* prompt still
appears. **Your own session is never force-quit.** The one exception is the
first-run step, where the installer opened the application itself seconds
earlier and there is nothing to lose.

**Signing in to Claude is required**, not optional. A freshly installed Claude
has no account, and without one it cannot load the plugin. Leaving without one
takes typing `SKIP`.

| If you | Then |
| --- | --- |
| Press Enter without closing REAPER | The REAPER step is **skipped, not attempted**. Reported at the end; close it and re-run `[1]`. |
| Press Enter without closing Claude | Continues, and warns Claude may revert the MCP entry. Re-run `[1]` if the tools are missing. |
| Close Claude without signing in | It reopens and keeps waiting. |
| Never sign in | Bounded: after six tries it continues and says loudly what is missing. |
| Run this from a terminal inside Claude | Detected; it refuses to close its own host and says so. |

Everything unfinished is listed at the end, and re-running `[1]` is safe.

### Requirements

Handled by `[1]`; this is what it is arranging.

| | |
| --- | --- |
| **REAPER** | v6 or v7+. If `[1]` installs it, it launches it once itself so the resource folder exists. |
| **Python** | Any 3.8+ on `PATH` is enough. `[1]` installs 3.12 alongside and **does not make it your default**. |

### What `[2]` restores

`[1]` snapshots every file it can touch before its first write, into
`%USERPROFILE%\.reaper-for-claude\backups\original\`. Files that existed are
copied; files that did **not** are noted as absent, so reverting deletes
exactly what the setup created and nothing else.

The snapshot is written **once and never overwritten**. Eight different failure
messages tell people to re-run `[1]`, and a second snapshot would photograph a
machine the first run had already changed, so "revert" would restore the
half-finished state instead of the original.

`[2]` restores those files, removes the virtualenv, and unregisters the plugin.
It **does not uninstall REAPER, Claude, Python or Git**; it reports which ones
`[1]` installed and leaves that to you. Projects, media, presets and
conversations are never touched.

**One narrow exception.** If `[1]` installed Claude and Claude was never signed
into, `[2]` moves its profile aside. Restoring config files cannot fix a Claude
that will not start, and the state that strands people is a profile interrupted
while being created. Both conditions come from the snapshot and both are
required, so the directory provably holds nothing you made. It is **renamed, not
deleted**.

---

## Troubleshooting

**Start here:**

```powershell
powershell -ExecutionPolicy Bypass -File install\health-check.ps1
```

Every line is `[ok]`, `[warn]` or `[FAIL]`, and each failure carries a `->` fix.
It checks the layout, whether the server can *actually start*, the distant API,
the bridge heartbeat, a live connection when REAPER is running, both Claude
surfaces, and whether an older install is still loading in parallel.

| Symptom | Cause |
| --- | --- |
| No REAPER tools at all | On claude.ai, expected. Otherwise the server did not start; run the health check. |
| Tools fail with a socket error | REAPER is not running, or the distant API was never configured. A *persistent* failure means configuration. |
| Bridge times out | REAPER is not running `claude_bridge.lua`. Check `status.txt` in the bridge directory. |
| `PARSE_ERROR` on byte one | Lua reached the bridge with a UTF-8 BOM. Use `--code`, or `--lua-file` so it is stripped. |
| Renders are silent | The `offlineinact` preference. See [rendering.md](skills/reaper-mcp/references/rendering.md). |
| Everything appears twice | An older install is still loading from `~\.claude\skills\`. Delete `reaper-mcp` and `reaper-ai-engineer-skill`. |
| Edits here do nothing | You installed from the marketplace, which is a copy. See [Editing](#editing-the-plugin). |

**"ReaScript task control" dialog on first run**: choose **New instance**, never
**Terminate instances**. The dialog is modal, so while it is open REAPER runs no
background scripts at all; terminating stops the reapy server *and* the Lua
bridge. The server was only asked to start twice. The setup now waits for REAPER
to publish its port instead of asking again, so it should not appear.

**`No module named 'mcp.server.fastmcp'`**. `mcp` 2.0+ got installed and removed
the FastMCP API. `requirements.txt` pins `mcp<2.0.0`; an older environment needs
`python scripts\bootstrap.py --recreate`.

**`import reapy` fails**. Rebuild, optionally on a different Python:
`py -3.12 scripts\bootstrap.py --recreate`.

**Socket error / `WinError 10053`**. Run `python reaper\enable_reapy.py --check`.
A web interface on **2306** is the problem: `--repair`, then restart REAPER.

**Claude Desktop reverted to an old MCP entry**. Desktop holds
`claude_desktop_config.json` in memory and rewrites it from its own state, the
way REAPER does with `reaper.ini`. Quit it fully including the tray icon, run
`configure-plugin.ps1 -Only claude`, then start it again.

**The marketplace stopped resolving**. A local marketplace stores an absolute
path, so moving this folder breaks it. Re-add:
`claude plugin marketplace add <new path>`.

**The dependency install is very slow**. Expected, and it is the size: **477 MB
across 12,212 files**, of which `librosa`'s chain is 345 MB. A Sandbox makes it
worse three ways: no pip cache to reuse, Defender scanning every extracted
file, and a virtual disk at its worst with many small files. It runs once; the
virtualenv lives outside the plugin and survives updates.

If it is **stuck** rather than slow, look for `Building wheel for llvmlite`.
That means pip is compiling LLVM, which takes about an hour and usually fails.
Bootstrap passes `--only-binary=:all:` to prevent it. The fix is an older
interpreter: `py -3.12 scripts\bootstrap.py --recreate`.

---

## How it works

### Layout

```
ReaperSkillsForClaude/              # marketplace root AND plugin root
├── .claude-plugin/                 #   plugin.json + marketplace.json
├── skills/                         # documentation only, no executables
│   ├── reaper-core-setup/          #   install, diagnose, repair; routes the rest
│   ├── reaper-mcp/                 #   the channel: MCP tools and the Lua bridge
│   └── reaper-audio-engineer/      #   the engineering playbook
├── scripts/                        # every cross-platform executable
│   ├── launch_server.py            #   picks an interpreter, then serves
│   ├── bootstrap.py                #   builds the dependency virtualenv
│   ├── bridge.py                   #   the file-bridge client
│   ├── benchmark_tools.py          #   exercises all 58 tools against REAPER
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
site-packages, a deliberate and narrow exception. It needs only `psutil` and
`typing-extensions`; the compiled numeric stack that makes a global install
worth avoiding stays in the virtualenv.

**Your `PATH` is not changed** unless there is no usable Python 3 on it. Nothing
here wants the *default* interpreter: the server uses the virtualenv by absolute
path, REAPER uses `pythonlibpath64` from `reaper.ini`, and the configure step
uses `py -3.12`, which ignores `PATH` order. `launch_server.py` is deliberately
written in the Python 2/3 intersection so even a 2.7 `python` can parse it and
hand over. One f-string there would fail before reaching the check that
explains why.

### Starting the server

`plugin.json` runs `scripts/launch_server.py` with whatever `python` is on PATH,
frequently the wrong one. A stdio server that dies during startup surfaces only
as *"server failed to connect"*, so the launcher:

1. **Probes for an interpreter that can import the dependencies**, in order:
   `REAPER_MCP_PYTHON`, the managed virtualenv, the current interpreter, then
   `py -3.12` and friends. It tests the imports rather than comparing versions.
2. **Re-launches itself** under that interpreter.
3. **Falls back to a one-tool diagnostic server** when nothing works, so Claude
   gets `reaper_setup_status` instead of a dead connection.

A probe that **times out** is treated differently from one that fails. A missing
module answers in milliseconds; a cold first import of numpy, mcp and reapy can
exceed the timeout on a fresh machine. The managed virtualenv is kept when its
probe merely times out. `bootstrap.py` built and verified it, so slow is the
only thing that can mean.

Dependencies are never installed at startup: a cold install takes minutes, and a
server that misses its initialize timeout is dropped as failed.

The virtualenv sits at a fixed path rather than under the host's plugin data
directory, which only exists when Claude launches the server. A venv built by
the installer from a terminal would land somewhere the server never looks.

### Reaching REAPER

Two independent routes, so losing one leaves the other working.

- **The MCP server** connects through reapy's distant API: Python ReaScript on,
  a web interface on port **2307**, the `activate_reapy_server` action
  registered, and its id recorded. All four are handled by
  `reaper/enable_reapy.py`, all idempotent.
- **The file bridge** runs arbitrary Lua inside REAPER through a watched
  directory, for what the API cannot express: offline DSP measurement, finding a
  plugin parameter by its formatted value, and the silent-render workaround.

```bash
reaper-bridge --code 'return reaper.GetAppVersion()'
```

`reaper-bridge` is on the Bash tool's PATH whenever the plugin is enabled. It
takes Lua directly, which removes a trap rather than documenting it: writing a
temp `.lua` with PowerShell 5.1 prepends a UTF-8 BOM, which the bridge rejects
with `PARSE_ERROR` on byte one.

**Port 2306 is not a web interface.** It is the socket reapy's own server binds.
An earlier setup added a web interface there, which takes the port first and
produces `WinError 10053`. `--repair` removes it, and the installer runs that
before configuring.

Before importing reapy, the server **waits for REAPER to publish its server
port** rather than letting reapy find it missing and perform the action again,
which is what produced the task-control dialog. The wait is a plain read with no
side effects, and it is skipped entirely when REAPER is not running, because
then reapy raises instead of prompting and there is nothing to prevent.

### The download cache

**`[1]` installs through `downloadCache\` and fills it as it goes.** Already
there means install from disk; not there means fetch it *into* the cache and
install from disk anyway. The second run costs nothing.

That is why `winget install` is not what runs: it downloads into its own temp
directory and deletes it, so a machine that gets wiped and re-run re-downloaded
everything every time. `winget download` is the same fetch from the same source,
kept, with the manifest beside it, which is also how the installer knows the
silent switches without guessing.

- **Files you already have are used where they lie.** Name and place are matched
  loosely on purpose: nobody renames the winget bundle, and the folder shared
  into a Sandbox is usually the one *containing* the clone. Four directories are
  searched, nearest first: `downloadCache\`, the plugin folder, its parent, and
  a `downloadCache\` there. Read-only; downloads are written to `downloadCache\`
  and nowhere else.
- **It is only ever an optimisation.** Absent, empty or unreadable, `[1]`
  behaves exactly as it would have and downloads what it needs.
- **Only for what is missing.** An installed application is never replaced by a
  cached copy, which could as easily be a downgrade.
- **Claude Code is the exception**. winget extracts it rather than running an
  installer. It is downloaded once, found unusable from disk, and its manifest
  kept as a note so later runs skip it.
- **Gitignored**, and safe to delete at any time.

Repeating a 200 MB fetch from the same host often enough is how an address gets
rate-limited and then blocked. That is not hypothetical. It is what this exists
to stop.

### Logs

Every run writes one, to `%USERPROFILE%\.reaper-for-claude\logs\`. Timestamped,
level-tagged plain ASCII, so it greps and pastes anywhere:

```
21:54:21  STEP  Applications
21:54:21  OK    Git ready (from downloadCache).
21:54:21  WARN  REAPER failed (exit 1); retrying once...
21:54:21  FAIL  Claude Code: winget exited 1.
```

`[1]` writes two. `setup-<time>.log` is that structured trace: *what did the
setup do*. `transcript-<time>.log` is the raw capture including winget's and
pip's own output: *what did everything else say*. Read the first.

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
| **winget** | `v1.8.1911`: `microsoft/winget-cli`, 252 MB |
| **UI.Xaml** | `v2.8.6`: `microsoft/microsoft-ui-xaml`, 5 MB |
| **VCLibs** | `14.00 Desktop`: `aka.ms/Microsoft.VCLibs.<arch>.14.00.Desktop.appx`, 7 MB |

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

**Python 3.12 rather than the newest** also because `numba` and `llvmlite`,
which `librosa` needs, routinely take months to publish wheels for a new
release.

**winget is pinned because 1.9+ added a dependency 1.8 does not have.** The
current release declares `Microsoft.WindowsAppRuntime.1.8`, which means either a
102 MB setup executable or a framework package Microsoft does not publish
standalone, and getting it wrong is a `0x80073CF3` that reads like a corrupt
download. 1.8.1911 predates it: bundle plus two appx files, one
`Add-AppxPackage` call.

Packages are named per architecture (`VCLibs.x64.appx`, `VCLibs.arm64.appx`), so
a cache copied to a different architecture reads as empty rather than as
complete-but-undeployable.

### No winget?

It ships with App Installer, absent on a fresh Windows Server, on images built
without the Store, and inside Windows Sandbox. `[1]` installs it:

1. **Register an App Installer that is already provisioned**: instant, offline.
2. **Deploy the packages directly**: register VCLibs and UI.Xaml, then the
   bundle with both as `-DependencyPath`. Needs nothing but HTTPS, and nothing at
   all once cached.
3. **PowerShell Gallery bootstrap**, as the fallback for a machine where step 2
   is refused by policy.

Downloads use `curl.exe` (in Windows since 1803) with `Invoke-WebRequest` behind
it, `--fail` so an HTTP error cannot be saved as the package, and a size floor,
which catches what `--fail` cannot, since a captive portal and a retired `aka.ms`
link both answer `200`.

---

## Development

### Editing the plugin

Installing from a marketplace **copies** this folder into a versioned cache, so
edits here do not reach Claude until you bump `version` in both manifests and
reinstall. For live editing:

```powershell
powershell -ExecutionPolicy Bypass -File install\configure-plugin.ps1 -Only claude -Link
```

That creates a junction at `~\.claude\skills\reaper-for-claude` pointing here,
which loads in place.

**Use one route or the other.** They share a plugin name, Claude Code resolves it
in favour of the marketplace, and it skips the link silently, so with both
present you edit and nothing happens, with no error. Uninstalling is not enough
either; the marketplace **entry** reserves the name:

```powershell
claude plugin uninstall reaper-for-claude@reaper-skills-for-claude
claude plugin marketplace remove reaper-skills-for-claude
```

`-Link` does both for you, and the health check flags the shadowed state.

Changes to a `SKILL.md` apply immediately. Changes to `plugin.json` need
`/reload-plugins` or a restart.

### Installing by hand

`[1]` handles both Claude surfaces. Otherwise:

| Surface | How |
| --- | --- |
| **Claude Code** | `/plugin marketplace add <this folder>` then `/plugin install reaper-for-claude@reaper-skills-for-claude` |
| **Claude Desktop / claude.ai** | Customize → Plugins → Personal plugins → **+** → Add marketplace |

Desktop and the web app fetch marketplaces from a **git host**, not a local path,
so the Plugins UI needs this pushed somewhere first. Until then the installer
writes the MCP server straight into `claude_desktop_config.json`, which gives
Desktop the tools immediately, just not the skills.

### Porting

Only `RunThisToStart.bat` and `install/*.ps1` are Windows-specific. The server,
`launch_server.py`, `bootstrap.py`, `doctor.py`, `bridge.py`, `enable_reapy.py`
and the Lua listener are cross-platform, so macOS or Linux support means writing
an installer and changing `command` in `plugin.json` from `python` to `python3`,
not touching any of the logic.
