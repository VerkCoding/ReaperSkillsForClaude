# REAPER for Claude

Claude works inside REAPER as an audio engineer: mixing, mastering, MIDI, FX,
rendering, and real DSP measurement.

This folder is a single Claude plugin — two skills and an MCP server with ~58
REAPER tools — installed the same way on Claude Code, Claude Desktop and
claude.ai.

**Windows.** Only the installer and menu are Windows-specific; everything under
them is cross-platform. See [Porting](#porting).

---

## Install

```bash
git clone <this repo>
```

Double-click **`RunThisToStart.bat`** and choose **`[1] Install Everything`**.

That is the whole thing. It backs up your current configuration, installs
anything missing, sets up the plugin, and runs a health check. Then start
REAPER, restart Claude, and ask *"Check the current REAPER project info."*

There are two options, because everything the setup does belongs to one of
them:

| | |
| --- | --- |
| **`[1]` Install Everything** | Close apps → snapshot → install what's missing → first run → configure the plugin → health check |
| **`[2]` Revert Everything** | Restore that snapshot, remove what `[1]` added |

### What `[1]` asks of you

**Save your work first.** `[1]` asks you to close REAPER and Claude, waits, and
then closes anything still open. Both hold their settings in memory and write
them back on exit, so anything written underneath a running instance is
discarded — that is not a precaution, it is the reason the setup would otherwise
silently fail.

**Your own session is never force-quit.** The close request is the same one the
X button sends, so REAPER's *"save changes?"* prompt still appears and waits for
you. If an app does not close, `[1]` asks again rather than escalating — hours of
unsaved work is not worth trading for a smoother install.

There is one exception, and it is the opposite case: during [first
run](#first-run-when-1-installed-something), `[1]` opened the application itself
seconds earlier. There is no project and no unsaved edit — only a splash screen
or licence dialog in the way — so after ten seconds of asking politely it ends
the process rather than making you click through.

For Claude that means **everything, not just the window**. It is an Electron app:
one window, a dozen helper processes and a tray icon, and closing the window
leaves the rest running — still holding the config file the setup is about to
write. So the kill is swept repeatedly until nothing is left.

Even then it targets exact process IDs, with this script's own ancestry and the
Claude Code CLI already filtered out. `taskkill /IM claude.exe` would have hit
both — and one of those is routinely the terminal running the installer.

It also **re-checks after every point where it waited on you** — after the save
prompt, after REAPER's first run, after the Claude sign-in, and once more
immediately before it writes any configuration. Between a prompt and your
answer, an application can easily come back: REAPER from a jump list, Claude
from the tray. One that slipped open would silently discard everything written
next, so it is closed again rather than assumed.

**If you run this from a terminal inside Claude**, `[1]` detects that Claude is
its own host and refuses to close it — killing it would kill the installer.
It says so, continues, and warns that Claude may revert the MCP entry it writes;
restart Claude at the end if the REAPER tools are missing.

### First run, when `[1]` installed something

A freshly installed REAPER has **no `reaper.ini`** — the resource folder only
appears after it has run once — and a fresh Claude has no session. Rather than
finishing with "now go launch REAPER yourself", `[1]` handles both:

- **REAPER** — opens it, waits until it is *fully* up (a main window, not just
  the config file appearing — REAPER writes that early in startup and keeps
  writing), lets the writes settle, then closes it and verifies the file
  exists. Click through any first-run dialog it shows.
- **Claude** — opens it and waits while you sign in, continuing on its own once
  a session exists. It confirms this from Claude's config, not from a window
  being open, because a window can be open with nobody signed in. Only the
  *presence* of a session is checked; no token value is ever read.

  **Signing in is required here, not optional.** Claude has just been installed,
  so it has no account attached, and without one it cannot load the plugin or
  reach REAPER — which is the entire point of the setup. Closing the window or
  pressing Enter reopens Claude and keeps waiting. Leaving without a session
  takes typing `SKIP`, deliberately, because the alternative outcome is a plugin
  that installs perfectly and then does nothing, which is a miserable thing to
  diagnose.

Both steps run **only for applications this run installed**. Anything already on
your machine has a config and a session already, and opening it uninvited would
be presumptuous.

### If you don't follow the prompts

None of it is mandatory, and none of it hangs:

| You do this | What happens |
| --- | --- |
| Press Enter **without closing REAPER** | The REAPER connection step is **skipped, not attempted** — writing it under a running REAPER would only be discarded on exit. Reported at the end; close REAPER and re-run `[1]` to finish it. |
| Press Enter **without closing Claude** | The setup continues and warns that Claude may revert the MCP entry it writes. If the REAPER tools are missing afterwards, close Claude fully and re-run `[1]`. |
| **Close Claude without signing in** | Detected within seconds, and it reopens Claude rather than moving on — signing in is required. |
| **Never sign in**, or Claude fails to start | Bounded: after six attempts it continues without a session and says loudly that the plugin cannot be used until you sign in. |
| Want to skip the sign-in anyway | Type `SKIP` at the prompt. A keypress will not do it — see below. |

Everything unfinished is listed at the end of `[1]`, and re-running it is safe.

### What `[1]` installs

From the winget community source, which uses each vendor's own installer
(reaper.fm, claude.ai, git-scm) rather than a Microsoft Store package.

| Package | If already installed |
| --- | --- |
| Python 3.12 | upgraded normally |
| Git | upgraded normally |
| REAPER | **skipped entirely** |
| Claude Desktop | **skipped entirely** |
| Claude Code | **skipped entirely** |

REAPER and Claude are skipped rather than reinstalled on purpose. Their
installers are well behaved, but a reinstall is a needless risk to a REAPER
resource folder holding years of preferences, FX chains and templates, and to
Claude's local history. Nothing in this setup is worth that.

**Close REAPER first** if it is open. REAPER rewrites `reaper.ini` when it
exits, so anything written while it is running is discarded — `[1]` warns and
waits rather than letting that happen silently.

### What `[2]` restores

`[1]` takes a snapshot before its first write, into
`%USERPROFILE%\.reaper-for-claude\backups\<timestamp>\`. It records two kinds of
entry, and the difference is what makes a clean revert possible: files that
already existed are copied, and files that did **not** exist are noted as
absent, so reverting deletes exactly the ones the setup created and no others.

Tracked: `reaper.ini`, `reaper-kb.ini`, `reaper-extstate.ini`,
`Scripts\__startup.lua`, `Scripts\claude_bridge.lua`, `Scripts\enable_reapy.py`,
`claude_desktop_config.json` (both plain and Store installs), and
`~\.claude\settings.json`.

`[2]` restores those, deletes the dependency virtualenv, and unregisters the
plugin from Claude Code. It **does not uninstall REAPER, Claude, Python or
Git** — by the time anyone reverts, those may hold projects, chats and
repositories that have nothing to do with this plugin. It reports which ones
`[1]` installed and leaves removing them to you. Projects, media, presets, FX
chains and conversations are never touched.

## Requirements

Everything below is handled by `[1]`; this is what it is arranging for you.

| | |
| --- | --- |
| **REAPER** | v6 or v7+. If `[1]` installs it, launch it once and close it, then run `[1]` again so the REAPER-side setup can find its config folder. |
| **Python** | 3.10 or newer, on PATH. `[1]` installs 3.12 when it is missing — via winget, or straight from python.org when winget is unavailable. |

### Running the steps individually

The menu is a front end. Every step is a script in `install\`, runnable on its
own, and `[1]` is just the two of them in order:

| Script | Does |
| --- | --- |
| `setup-all.ps1` | Everything `[1]` does. `-SkipApps` configures the plugin only. |
| `revert-all.ps1` | Everything `[2]` does. `-From <dir>` picks an older snapshot. |
| `snapshot.ps1` | `-Backup`, `-Restore`, `-List` — the backup engine on its own. |
| `install.ps1` | Plugin setup only: `-Only python\|reaper\|claude`, `-Link`, `-Force`. |
| `doctor.ps1` | Health check. Changes nothing. Run it any time. |
| `install-python.ps1` | Python only, winget or direct download. |
| `repair-winget.ps1` | Install or repair winget itself. |

The health check is worth knowing about by name — `[1]` runs it at the end, but
it is the thing to reach for whenever something stops working:

```powershell
powershell -ExecutionPolicy Bypass -File install\doctor.ps1
```

### About the winget invocation

When winget is available, Python is installed with:

```powershell
winget install -e --id Python.Python.3.12 --custom "PrependPath=1 InstallAllUsers=0 Include_test=0"
```

Three details matter, and each cost a debugging session to find:

- **`-e`** forces an exact ID match. Without it `Python.Python.3` is ambiguous
  and can resolve to **Python 3.0**, which really is in the catalogue — and
  `REAPER` alone also matches an unrelated `ScytheLabs.Reaper`.
- **`--custom`**, not `--override`. `--custom` appends to winget's own silent
  switches; `--override` *replaces* them, so you inherit responsibility for
  every default you just discarded.
- **`InstallAllUsers=0`** rather than `--scope user`. winget matches `--scope`
  against the manifest, and Python's is a `burn` bundle with no user-scope
  installer declared, so `--scope user` fails with *"no applicable installer
  found"*. The bundle's own switch works and never needs elevation.

**Python 3.12 rather than the newest release** for two reasons. `numba` and
`llvmlite` — which `librosa` depends on — routinely take months to publish
wheels for a brand-new Python. And more seriously:

> **reapy 0.10.0 destroys `reaper.ini` on Python 3.13+.** Python 3.13 added
> unnamed sections to `configparser`, so parsing yields a `_UnnamedSection`
> sentinel among the section names; reapy calls `.lower()` on each of them and
> raises — *partway through rewriting the file*, leaving it **zero bytes**.
> Every REAPER preference, audio device setting and path is gone, and nothing in
> the resulting error mentions `reaper.ini`.
>
> Three separate guards now exist. `enable_reapy.py` refuses to configure on
> 3.13+ and says why; it copies `reaper.ini` first and restores it if the file
> ends up smaller than it started, whatever the cause; and the installer selects
> a 3.12-or-older interpreter for this step specifically, skipping it with an
> explanation rather than risking the file when none exists.
>
> Only this configuration step is affected. The MCP server itself runs fine on
> 3.14, which is why the two interpreters are chosen separately.

**No winget?** It ships with App Installer, absent on a fresh Windows Server, on
images built without the Store, and inside Windows Sandbox. Nothing here needs
it: `[1]` falls back to downloading Python straight from python.org.
`repair-winget.ps1` exists if you want winget itself back — it tries the offline
repair first (registering an App Installer that is present but unregistered for
your user, the usual state in Sandbox and on Server) before Microsoft's
PSGallery bootstrap, which frequently fails on those same machines with
`Unable to download from URI 'https://go.microsoft.com/fwlink/?LinkID=627338'`.


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

Slow is the expected outcome there, and the install is not divisible: the
analysis tools are part of what this plugin is for. Let it run once - the
virtualenv lives outside the plugin, so it survives plugin updates and is not
rebuilt by the next install.

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
├── skills/                         # documentation only, no executables
│   ├── reaper-audio-engineer/      #   the engineering playbook
│   │   ├── SKILL.md
│   │   └── references/{measurement,plugin-control,rendering}.md
│   └── reaper-setup/SKILL.md       #   install, diagnose, repair
├── scripts/                        # every cross-platform executable
│   ├── launch_server.py            #   picks an interpreter, then serves
│   ├── bootstrap.py                #   builds the dependency virtualenv
│   ├── bridge.py                   #   the file-bridge client
│   └── doctor.py                   #   the health check itself
├── bin/reaper-bridge               # puts bridge.py on the Bash tool's PATH
├── src/reaper_mcp/                 # the MCP server
├── reaper/                         # REAPER-side assets
│   ├── claude_bridge.lua
│   └── enable_reapy.py
└── install/                        # Windows setup, PowerShell
    ├── setup-all.ps1  revert-all.ps1  snapshot.ps1
    ├── install.ps1    doctor.ps1      lib-apps.ps1
    └── install-python.ps1  repair-winget.ps1
```

Three rules keep this navigable:

- **`skills/` holds documentation and nothing else.** The bridge client used to
  live under a skill while `bin/` reached in to run it; it is a plugin-level
  tool, so it sits with the others.
- **`scripts/` is every cross-platform executable**, whether it runs at setup
  time or while the plugin is live. **`install/`** is the Windows-only
  PowerShell that drives them.
- **The MCP server is declared inline in `plugin.json`.** A root `.mcp.json`
  would also be read as a *project* MCP config, starting the server twice when
  this folder is opened in Claude Code — the second time with
  `${CLAUDE_PLUGIN_ROOT}` unresolved. Inlining sidesteps that without needing a
  separate file to point at.

`doctor.ps1` is a thin wrapper around `doctor.py`. Two health checks that can
disagree about what "working" means are worse than one, because whichever you
happen to run tells you the setup is fine.

### Starting the MCP server

`plugin.json` runs `scripts/launch_server.py` with whatever `python` is on
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

For live editing, run:

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

`install.ps1 -Only claude -Link` does both for you, and the health check flags
the shadowed state.

Changes to a `SKILL.md` apply immediately. Changes to `plugin.json` need
`/reload-plugins` or a restart.

## Porting

Only `RunThisToStart.bat` and `install/*.ps1` are Windows-specific. The MCP
server, `launch_server.py`, `bootstrap.py`, `doctor.py`, `bridge.py`,
`enable_reapy.py` and the Lua listener are all cross-platform, so macOS or Linux
support means writing an installer and changing the `command` in `plugin.json`
from `python` to `python3` — not touching any of the logic.

## Credits

The MCP server is derived from
[bonfire-systems/reaper-mcp](https://github.com/bonfire-systems/reaper-mcp)
(MIT). REAPER connectivity uses
[python-reapy](https://github.com/RomeoDespres/reapy).
