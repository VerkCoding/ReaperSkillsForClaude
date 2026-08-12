---
name: reaper-setup
description: >-
  Install, diagnose and repair the REAPER for Claude plugin. Use when REAPER
  tools are missing or failing, when the file bridge times out, when
  `reaper_setup_status` reports a problem, when the user is setting this up for
  the first time, or when they ask why Claude cannot reach REAPER. Covers the
  Python environment, REAPER's distant API, the bridge listener, and the
  differences between Claude Code, Claude Desktop and claude.ai.
---

# REAPER for Claude — setup and repair

Four things have to be true before Claude can work in REAPER. Diagnose in this
order; each one depends on the ones above it.

| # | Requirement | Fails as |
| --- | --- | --- |
| 1 | A Python that can `import mcp, reapy, numpy` | No REAPER tools, or only `reaper_setup_status` |
| 2 | REAPER running | Socket errors from every tool |
| 3 | REAPER's distant API configured | `WinError 10053`, connection refused |
| 4 | `claude_bridge.lua` loaded in REAPER | Bridge commands time out |

Requirements 3 and 4 are independent: the MCP server needs the distant API, the
file bridge needs the Lua listener. Losing one leaves the other working, so
establish which route is broken before changing anything.

## Start here

On Windows, run the health check:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/install/doctor.ps1"
```

Every line is `[ok]`, `[warn]` or `[FAIL]`, and each failure carries a `->` fix.
Read its output before guessing — it checks all four requirements plus where the
plugin is installed.

## 1. The Python environment

The MCP server does **not** run under whatever `python` resolves to. A launcher
probes for an interpreter that can actually import the dependencies, preferring
a virtualenv the plugin owns. Check it:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.py" --check
```

Build or repair it:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.py"
```

This creates a virtualenv under the host's persistent plugin data directory and
installs `requirements.txt` into it. It touches nothing else on the system, and
it survives plugin updates. A cold install takes a few minutes, mostly librosa.

**Do not tell the user to `pip install` into their system Python.** That is how
an unrelated project gets broken, and the launcher would not necessarily pick
that interpreter anyway.

If the install fails because no wheel exists for their Python version, bootstrap
with a different interpreter — the launcher will find the venv regardless of
which Python built it:

```bash
py -3.12 "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.py" --recreate
```

The environment lives at `%USERPROFILE%\.reaper-for-claude\venv`, a fixed path
that both the installer and the running server resolve identically. It is
deliberately not derived from the host's plugin data directory: that only exists
when Claude launches the server, so a venv built from a terminal would land
somewhere the server never looks.

**Two interpreters need reapy, and the virtualenv only covers one.** REAPER
loads a Python shared library and runs ReaScripts inside its own process, so it
uses the *base* installation and cannot see a virtualenv. `reapy` therefore has
to be importable by the base Python as well, or `activate_reapy_server` fails
and the distant API never starts — which presents as "everything installed,
nothing connects". `bootstrap.py` handles this, installing only `python-reapy`
there. If it ever needs doing by hand:

```
"<base python>" -m pip install --user python-reapy
```

`--check` reports both sides separately, and so does the health check.

**The version limit applies to configuring REAPER, not to running the server.**
Two interpreters, two different requirements:

| Job | Requirement |
| --- | --- |
| Running the MCP server | any Python where the imports work — 3.14 is fine |
| Configuring REAPER (`enable_reapy.py`) | **3.12 or older** |

reapy 0.10.0 crashes partway through rewriting `reaper.ini` on Python 3.13+,
because `configparser` gained unnamed sections and reapy calls `.lower()` on the
sentinel. The crash leaves `reaper.ini` **empty** — every REAPER preference
gone, with nothing in the error naming the file.

`enable_reapy.py` refuses to run there, backs the file up first and restores it
if it shrinks, and the installer picks a 3.12-or-older interpreter for that step
alone. If the health check says REAPER *cannot be reconfigured*, that is what it
means, and the fix is:

```
winget install -e --id Python.Python.3.12
py -3.12 -m pip install python-reapy
```

Never work around this by forcing the configure step onto a newer interpreter.

**`No module named 'mcp.server.fastmcp'`** means `mcp` 2.0 or newer got
installed. It dropped the FastMCP API the tool modules are written against.
`requirements.txt` pins `mcp<2.0.0`; an environment built before that pin needs
rebuilding with `--recreate`. This is the failure that hits a fresh install
hardest, because a machine that already had `mcp` 1.x keeps working and shows no
sign of the problem.

To force a specific interpreter permanently, set `REAPER_MCP_PYTHON` to its full
path in the environment Claude starts in.

## 2–3. REAPER and the distant API

Connecting from outside REAPER needs four things, all handled by
`reapy.config.configure_reaper()` and all idempotent:

| # | Step | Writes to |
| --- | --- | --- |
| 1 | Enable Python ReaScript + path to the Python shared library | `reaper.ini` |
| 2 | Add a web interface on port **2307** | `reaper.ini` |
| 3 | Register the `activate_reapy_server` ReaScript | `reaper-kb.ini` |
| 4 | Record that action's id | `reaper-extstate.ini` |

**Close REAPER first.** It rewrites `reaper.ini` when it exits, so anything
written while it is open is discarded. This is the single most common reason a
setup "did not take".

```bash
python "${CLAUDE_PLUGIN_ROOT}/reaper/enable_reapy.py" --check    # report only
python "${CLAUDE_PLUGIN_ROOT}/reaper/enable_reapy.py"            # configure
python "${CLAUDE_PLUGIN_ROOT}/reaper/enable_reapy.py" --repair   # fix port 2306
```

**Port 2306 is not a web interface.** It is `REAPY_SERVER_PORT`, the socket
reapy's own server binds. An earlier version of the setup script added a web
interface there, which takes the port before the server can and produces
`WinError 10053` at connect time. `--repair` removes it.

It also runs **inside** REAPER if the user would rather not close it: *Actions →
Show action list… → ReaScript: Run…* → `<REAPER resource path>/Scripts/enable_reapy.py`,
then restart REAPER.

## 4. The bridge listener

`claude_bridge.lua` goes in `<REAPER resource path>/Scripts/`, and
`__startup.lua` gets a one-line `dofile()` so REAPER loads it at launch. The
installer appends that line and backs up an existing `__startup.lua` — it never
overwrites one, because plenty of people already have a startup script and
losing it silently would be unrecoverable.

Check liveness by reading `status.txt` in `<REAPER resource path>/claude_bridge/`:
a fresh heartbeat means the listener is alive, a stale one means REAPER is
closed or the script was stopped (*Actions → Close all running scripts* does
that).

## Installing from scratch

On Windows, everything above is automated:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/install/install.ps1"
```

It bootstraps Python, installs the REAPER-side files, configures the distant
API, and runs the health check. It is idempotent — re-running after a failure is
safe. Close REAPER before running it.

On macOS and Linux the REAPER-side steps are manual: copy
`reaper/claude_bridge.lua` into `<REAPER resource path>/Scripts/`, add the
`dofile()` line to `__startup.lua`, and run `reaper/enable_reapy.py`.

## Which surface is this running on?

The answer changes what is even possible, so establish it before diagnosing.

| Surface | MCP server | File bridge | Notes |
| --- | --- | --- | --- |
| **Claude Code** | Yes | Yes | Full access. `reaper-bridge` is on the Bash tool's PATH. |
| **Claude Desktop** | Yes | Only where shell access exists | Local MCP servers run on the user's machine. |
| **claude.ai (web)** | No | No | No local machine to reach. The skills load and are useful as reference; the tools cannot exist. |

If a user on the web app is asking why REAPER tools are missing, that is the
answer — nothing is broken, and pointing them at the health check wastes their
time. Tell them to use Claude Desktop or Claude Code on the machine REAPER runs
on.

## After any change

1. **Restart REAPER** if `reaper.ini` was touched.
2. **Restart Claude**, or run `/reload-plugins` in Claude Code — changes to
   `SKILL.md` apply immediately, but MCP server config does not.
3. Verify with: *"Check the current REAPER project info."*
