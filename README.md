# REAPER for Claude

Claude works inside REAPER as an audio engineer: mixing, mastering, MIDI, FX,
rendering, and real DSP measurement.

This folder is a single Claude plugin — two skills and an MCP server with ~58
REAPER tools — installed the same way on Claude Code, Claude Desktop and
claude.ai.

**Windows.** Only the installer and menu are Windows-specific; everything under
them is cross-platform. See [Porting](#porting).

---

## Requirements

| | |
| --- | --- |
| **REAPER** | v6 or v7+. **Launch it once and close it** before installing — the first launch creates its config folder, and REAPER rewrites `reaper.ini` on exit, discarding anything written while it is open. |
| **Python** | 3.10 or newer, on PATH. If you don't have it, `RunThisToStart.bat` option **`[8]`** installs it for you. |

Option `[8]` runs:

```powershell
winget install -e --id Python.Python.3.12 --custom "PrependPath=1 InstallAllUsers=0 Include_test=0"
```

Three details in there matter:

- **`-e`** forces an exact ID match. Without it, `Python.Python.3` is ambiguous
  and can resolve to **Python 3.0**, which really is in the winget catalogue.
  Only versioned IDs exist — there is no `Python.Python.3`.
- **`--custom`**, not `--override`. `--custom` appends to winget's own silent
  switches; `--override` *replaces* them, so you inherit responsibility for
  every default you just discarded.
- **`InstallAllUsers=0`** rather than `--scope user`. winget matches `--scope`
  against the manifest, and this one is a `burn` bundle with no user-scope
  installer declared, so `--scope user` fails with *"no applicable installer
  found"*. The bundle's own switch reaches the same result and never needs
  elevation.

**3.12 rather than the newest release** because `numba` and `llvmlite` — which
`librosa` depends on — routinely take months to publish wheels for a brand-new
Python. Any 3.10+ works; the installer tests whether the dependencies actually
build rather than checking a version number, and tells you if yours cannot.

PATH is read once when a program starts, so a Python installed thirty seconds
ago is invisible to the window that installed it. Option `[8]` prepends the new
directory to its own session and re-checks, so you don't have to reopen
anything — and says so plainly if that fails.

**No winget?** It ships with App Installer, which is absent on a fresh Windows
Server, on images built without the Store, and sometimes after an in-place
upgrade. Option **`[9]`** runs Microsoft's documented bootstrap:

```powershell
Install-PackageProvider -Name NuGet -Force
Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery
Repair-WinGetPackageManager
```

Two departures from the sequence as usually quoted, both about privilege:
`-AllUsers` is passed to `Repair-WinGetPackageManager` **only when the window is
actually elevated**, since it errors otherwise, and `Install-Module`'s scope is
matched to the same condition rather than defaulting to `AllUsers`. It also
forces TLS 1.2 before touching PSGallery — Windows PowerShell 5.1 on older
builds still negotiates TLS 1.0, which PSGallery refuses with a misleading
"unable to download from URI".

## Install

```bash
git clone <this repo>
```

Then **double-click `RunThisToStart.bat`** and choose **`[1] Install
everything`**.

The menu checks Python and REAPER before it starts and shows `[OK]`/`[MISSING]`
for each, rather than failing halfway through a five-minute dependency install.
Option `[8]` walks through installing Python.

Option `[1]` then does four independent things, each idempotent, each able to
fail without taking the others down:

1. Builds the dependency virtualenv
2. Installs the Lua bridge listener into REAPER
3. Configures REAPER's distant API, which the MCP server connects through
4. Registers the plugin with Claude Code and Claude Desktop

Finally:

1. **Start REAPER.** It reloads `reaper.ini` at launch and starts the bridge
   listener automatically.
2. **Restart Claude.**
3. Ask: *"Check the current REAPER project info."*

If that answers, you are done. If it does not, run **`[2] Health check`** —
every failure it reports carries the command that fixes it.

### The rest of the menu

| | |
| --- | --- |
| `[2]` | Health check. Changes nothing. |
| `[3]` | Dependencies only |
| `[4]` | REAPER side only — bridge and distant API. Close REAPER first. |
| `[5]` | Claude side only — Code and Desktop |
| `[6]` | Repair the connection — removes the stray port 2306 interface |
| `[7]` | [Developer link](#editing-the-plugin) — load this folder in place so edits are live |
| `[8]` | Install Python via winget — see [Requirements](#requirements) |
| `[9]` | Install or repair winget itself |

`install.ps1` takes the same jobs as flags: `-Only python|reaper|claude`,
`-Link`, `-Force`, `-SkipBootstrap`, `-SkipDesktop`, `-SkipCode`,
`-SkipReaperConfig`, `-ReaperResourcePath "E:\REAPER\Portable"`.

## What runs where

The three surfaces do not offer the same access, and the skills say so rather
than pretending otherwise.

| | Claude Code | Claude Desktop | claude.ai (web) |
| --- | --- | --- | --- |
| Skills — playbook, setup | yes | yes | yes |
| MCP server, ~58 REAPER tools | yes | yes | **no** |
| Lua file bridge | yes | where shell access exists | **no** |

The web app has no local machine to reach, so a local MCP server cannot exist
there. That is a property of the browser, not a broken install.

Option `[1]` handles Claude Code and Desktop. By hand:

| Surface | How |
| --- | --- |
| **Claude Code** | `/plugin marketplace add <this folder>` then `/plugin install reaper-for-claude@reaper-skills-for-claude` |
| **Claude Desktop / claude.ai** | Customize → Plugins → Personal plugins → **+** → Add marketplace → *Add from a repository* |

Desktop and the web app fetch marketplaces from a **git host**, not a local
path, so the Plugins UI needs this pushed to GitHub or another git URL. Until
then the installer writes the MCP server straight into
`claude_desktop_config.json`, which gives Desktop the REAPER tools immediately —
just not the skills.

## What gets installed outside this folder

| Thing | Where | Why |
| --- | --- | --- |
| Dependency virtualenv | `%USERPROFILE%\.reaper-for-claude\venv` | Survives plugin updates; cannot break your system Python |
| `python-reapy` (only) | your base Python's user site-packages | REAPER embeds that interpreter and cannot see a virtualenv — see [Two interpreters](#two-interpreters-and-why-a-virtualenv-is-not-enough) |
| `claude_bridge.lua` | `<REAPER>\Scripts\` | The bridge listener |
| `__startup.lua` | `<REAPER>\Scripts\` | Gets a one-line `dofile()` — **appended, never overwritten**, with a `.bak` |
| Distant API settings | `reaper.ini`, `reaper-kb.ini`, `reaper-extstate.ini` | How the MCP server reaches REAPER |
| MCP server entry | `claude_desktop_config.json` | Backed up before each write |

---

## Troubleshooting

**Start here:** `RunThisToStart.bat` → `[2]`, or
`powershell -ExecutionPolicy Bypass -File install\doctor.ps1`.

Every line is `[ok]`, `[warn]` or `[FAIL]`, and each failure carries a `->` fix.
It checks the layout, whether the MCP server can *actually start*, REAPER's
distant API, the bridge heartbeat, a live `reapy` connection when REAPER is
running, both Claude surfaces, and whether an older install is still loading in
parallel. Or just ask Claude — the **reaper-setup** skill covers all of it.

| Symptom | Cause |
| --- | --- |
| No REAPER tools at all | On claude.ai, expected. Otherwise the server did not start — call `reaper_setup_status` if present, or run the health check. |
| MCP tools fail with a socket error | REAPER is not running, or the distant API was never configured. The server reconnects by itself when REAPER restarts, so a *persistent* failure means configuration. |
| Bridge times out | REAPER is not running `claude_bridge.lua`. Check `status.txt` in the bridge directory. |
| `PARSE_ERROR` on byte one | The Lua reached the bridge with a UTF-8 BOM. Pass `--code`, or `--lua-file` so it gets stripped. |
| Renders are silent | The `offlineinact` preference — see [rendering.md](skills/reaper-audio-engineer/references/rendering.md). |
| Everything appears twice | An older install is still loading from `~\.claude\skills\`. Delete `reaper-mcp` and `reaper-ai-engineer-skill`; this plugin replaces both. |
| Edits to this folder do nothing | You installed from the marketplace, which is a copy. See [Editing the plugin](#editing-the-plugin). |

**`No module named 'mcp.server.fastmcp'`** — `mcp` 2.0+ got installed. It removed
the FastMCP API the tool modules are written against. `requirements.txt` pins
`mcp<2.0.0`; an environment built before that pin needs
`python scripts\bootstrap.py --recreate`. This is the failure that hits a fresh
clone hardest, because a machine that already had `mcp` 1.x keeps working and
shows no sign of the problem.

**`import reapy` fails** — the interpreter running the server cannot load it.
Rebuild the environment, optionally with a different Python:
`py -3.12 scripts\bootstrap.py --recreate`.

**The dependency install is extremely slow** — especially in a VM or Windows
Sandbox. Expected, and it is the size that does it: the full set is **477 MB
across 12,212 files**, and `librosa`'s chain alone (`llvmlite` 117 MB, `scipy`
116 MB, `scikit-learn` 45 MB, `numba` 30 MB) is 345 MB of that.

A disposable environment makes it worse in three compounding ways: there is no
pip wheel cache to reuse, so every byte is downloaded again; Defender scans all
12,000-odd extracted files and cannot be turned off in Sandbox; and the
virtualised disk is at its slowest with exactly this pattern — very many small
files.

Use the core install instead:

```powershell
python scripts\bootstrap.py --core
```

**132 MB, 5,347 files.** The server starts and all 58 tools register; only the
offline analysis tools (loudness, spectrum, transients, stereo field, clipping)
are unavailable, because those libraries are imported lazily. Run bootstrap
again without `--core` later to add them.

If it is not just slow but **stuck**, check whether pip is *building* rather
than downloading — a line like `Building wheel for llvmlite`. That means no
wheel exists for your Python version and pip is compiling LLVM, which takes
about an hour and usually fails. Bootstrap passes `--only-binary=:all:` to
prevent exactly this, so you would only see it with `--allow-source`. The fix is
an older interpreter: `py -3.12 scripts\bootstrap.py --recreate`.

**Socket error / `WinError 10053`** — run
`python reaper\enable_reapy.py --check`. If it reports a web interface on 2306,
that is the problem: `--repair`, then restart REAPER.

**I already had a `__startup.lua`** — it was backed up to `__startup.lua.bak`
and the loader appended. Re-running detects the existing line and will not
duplicate it.

**The marketplace stopped resolving** — a local-directory marketplace stores an
absolute path, so moving or renaming this folder breaks it. Re-add it:
`claude plugin marketplace add <new path>`.

**Claude Desktop reverted to an old MCP entry** — Desktop holds
`claude_desktop_config.json` in memory and can rewrite it from its own state,
exactly the way REAPER does with `reaper.ini`. An edit made while it is running
may be silently undone, which shows up later as a server pointing at a path that
no longer exists. Quit Desktop fully (including the tray icon), run
`install.ps1 -Only claude`, then start it again. The installer warns when it
detects Desktop running, and the health check catches the reverted state.

---

## How it works

### Layout

```
ReaperSkillsForClaude/              # marketplace root AND plugin root
├── .claude-plugin/
│   ├── plugin.json                 #   the plugin manifest
│   └── marketplace.json            #   lists this plugin, source "./"
├── config/mcp.json                 # the `reaper` MCP server
├── skills/
│   ├── reaper-audio-engineer/      #   the engineering playbook
│   │   ├── SKILL.md
│   │   ├── references/{measurement,plugin-control,rendering}.md
│   │   └── scripts/bridge.py       #   the file-bridge client
│   └── reaper-setup/SKILL.md       #   install, diagnose, repair
├── bin/reaper-bridge               # bare command on the Bash tool's PATH
├── scripts/
│   ├── launch_server.py            #   picks an interpreter, then serves
│   ├── bootstrap.py                #   builds the dependency virtualenv
│   └── doctor.py                   #   the health check itself
├── src/reaper_mcp/                 # the MCP server
├── reaper/                         # REAPER-side assets
│   ├── claude_bridge.lua
│   └── enable_reapy.py
└── install/{install.ps1,doctor.ps1}
```

`config/mcp.json` rather than `.mcp.json` on purpose: a root `.mcp.json` is also
a *project* MCP config, so opening this folder in Claude Code would start the
server twice — once as the plugin's and once as the project's, the second with
`${CLAUDE_PLUGIN_ROOT}` unresolved.

`doctor.ps1` is a thin wrapper around `doctor.py`. Two health checks that can
disagree about what "working" means are worse than one, because whichever you
happen to run tells you the setup is fine.

### Starting the MCP server

`config/mcp.json` runs `scripts/launch_server.py` with whatever `python` is on
PATH. That interpreter is frequently the wrong one, and a stdio server that dies
during startup surfaces only as *"server failed to connect"* — the real
ImportError is invisible. So the launcher:

1. **Probes for an interpreter that can actually import the dependencies**, in
   order: `REAPER_MCP_PYTHON`, the managed virtualenv, the current interpreter,
   then `py -3.12` and friends. It tests the imports rather than comparing
   version numbers, because a version gate hard-codes a claim about reapy that
   keeps going stale.
2. **Re-launches itself** under that interpreter.
3. **Falls back to a one-tool diagnostic server** when nothing works, so Claude
   gets `reaper_setup_status` explaining the fix instead of a dead connection.

Dependencies are installed by `scripts/bootstrap.py`, never at startup: a cold
install takes minutes, and a server that misses its initialize timeout is
dropped as failed — installing on demand would turn a slow first run into a
broken one.

The virtualenv sits at a fixed path rather than under the host's plugin data
directory. That variable only exists when Claude launches the server, so a venv
built by the installer from a terminal would land somewhere the server never
looks; and its value differs between a marketplace install and a developer link,
which would mean building the whole dependency set twice.

### Two interpreters, and why a virtualenv is not enough

REAPER does not run Python as a subprocess. It loads a Python **shared library**
— `python3XX.dll` — and runs ReaScripts inside its own process. A virtualenv
contains no such library; it is a redirect layer around a base installation, and
REAPER knows nothing about it.

So two different interpreters have to import `reapy`, and only one of them is
ours:

| Interpreter | Runs | Needs |
| --- | --- | --- |
| The virtualenv | the MCP server, outside REAPER | everything in `requirements.txt` |
| REAPER's embedded Python | `activate_reapy_server.py`, which starts the distant API | `reapy` |

Miss the second and the setup looks complete while nothing connects: the server
has `reapy`, REAPER does not, so the API the server dials never comes up.
`bootstrap.py` therefore also installs `python-reapy` into the base Python's
user site-packages, and the health check tests both sides separately.

That is a deliberate exception to keeping things out of your system Python, and
a narrow one. `python-reapy` needs only `psutil` and `typing-extensions` — three
small pure-Python packages. The compiled numeric stack that makes a global
install worth avoiding (`numpy`, `numba`, `llvmlite`, `scipy`, `scikit-learn`,
`librosa`) stays in the virtualenv, where REAPER never looks and nothing else
can be broken by it.

You do **not** have to configure the DLL path by hand. `enable_reapy.py` resolves
the base installation even when it is run from inside the virtualenv, and writes
`pythonlibpath64` / `pythonlibdll64` into `reaper.ini` for you.

### Reaching REAPER

Two independent routes, so losing one leaves the other working:

- **The MCP server** connects through reapy's distant API. Enabling that needs
  four things, all handled by `reaper/enable_reapy.py` and all idempotent:
  Python ReaScript on, a web interface on port **2307**, the
  `activate_reapy_server` action registered, and its id recorded.
- **The file bridge** runs arbitrary Lua inside REAPER through a watched
  directory, for what the API cannot express: offline DSP measurement, searching
  a plugin parameter by its formatted value, and the silent-render workaround.

**Port 2306 is not a web interface.** It is `REAPY_SERVER_PORT`, the socket
reapy's own server binds. An earlier version of the setup script added a web
interface there, which takes the port before the server can and produces
`WinError 10053` at connect time. `--repair` removes it, and the installer runs
that automatically before configuring.

```bash
reaper-bridge --code 'return reaper.GetAppVersion()'
```

`reaper-bridge` is on the Bash tool's PATH whenever the plugin is enabled. It
takes the Lua directly, which removes the old trap rather than documenting it:
the previous workflow wrote a temp `.lua` file, and writing that with Windows
PowerShell 5.1 prepends a UTF-8 BOM, which the bridge rejects with `PARSE_ERROR`
on byte one.

---

## Editing the plugin

Installing from a marketplace **copies** this folder into a versioned cache
(`~\.claude\plugins\cache\...`). Edits here do not reach Claude until you bump
`version` in both manifests and reinstall — right for a release, infuriating
while working.

For live editing use `RunThisToStart.bat` → `[7]`, or:

```powershell
powershell -ExecutionPolicy Bypass -File install\install.ps1 -Only claude -Link
```

That creates a junction at `~\.claude\skills\reaper-for-claude` pointing here,
which loads *in place*.

**Use one route or the other.** They share a plugin name, Claude Code resolves
that in favour of the marketplace, and it skips the link — reporting it only in
`claude plugin list`. So with both present you edit and nothing happens, with no
error to explain it. Uninstalling is not enough either: the marketplace **entry**
reserves the name whether or not anything is installed from it.

```powershell
claude plugin uninstall reaper-for-claude@reaper-skills-for-claude
claude plugin marketplace remove reaper-skills-for-claude
```

`[7]` does both for you, and the health check flags the shadowed state.

Changes to a `SKILL.md` apply immediately. Changes to `config/mcp.json` or
`plugin.json` need `/reload-plugins` or a restart.

## Porting

Only `RunThisToStart.bat` and `install/*.ps1` are Windows-specific. The MCP
server, `launch_server.py`, `bootstrap.py`, `doctor.py`, `bridge.py`,
`enable_reapy.py` and the Lua listener are all cross-platform, so macOS or Linux
support means writing an installer and changing `config/mcp.json` from `python`
to `python3` — not touching any of the logic.

## Credits

The MCP server is derived from
[bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp)
(MIT). REAPER connectivity uses
[python-reapy](https://github.com/RomeoDespres/reapy).
