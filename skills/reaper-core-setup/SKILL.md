---
name: reaper-core-setup
description: >-
  The foundation the other REAPER skills stand on: install, diagnose and repair
  the REAPER for Claude plugin, and work out which skill a request belongs to.
  Use when REAPER tools are missing or failing, when the file bridge times out,
  when `reaper_setup_status` reports a problem, when the user is setting this up
  for the first time, when they ask why Claude cannot reach REAPER, or when it
  is unclear whether a request is a transport problem or an engineering one.
  Covers the Python environment, REAPER's distant API, the bridge listener, and
  the differences between Claude Code, Claude Desktop and claude.ai.
---

# REAPER for Claude: core setup

This plugin contains three skills. This skill establishes the connection and determines which of the other two skills handles a request.

## The three skills

| Skill | Owns | Reach for it when |
| --- | --- | --- |
| **reaper-core-setup** (this one) | Installation, the health check, repair, and which surface you are on | Nothing works yet, or you cannot tell which layer is at fault |
| **reaper-mcp** | The channel: MCP tools, the Lua bridge, handling silent failures and hangs | REAPER connection is required, or a call failed, hung, or returned abnormal data |
| **reaper-audio-engineer** | The craft: measurement, interpretation, and subsequent actions | A working route exists and the task is to determine the next action |

**reaper-mcp determines if the command executed successfully. reaper-audio-engineer determines if the command was correct.** A silent render falls under reaper-mcp. A render that executes but is too quiet falls under reaper-audio-engineer.

Transitions between skills follow this order:

1. **Here**, until the health check is clean.
2. **reaper-mcp**, until you have a route and a value you trust.
3. **reaper-audio-engineer**, to decide and to interpret.
4. Back to **reaper-mcp** to apply the change and read it back.

If a measurement is anomalous (e.g., a bus reads silence, all sources report identical levels, a parameter does not change), treat it as a transport failure rather than an engineering result. Return to step 2. reaper-mcp lists specific transport failures.

## Four requirements

Four requirements must be met before Claude can interact with REAPER. Diagnose in the following sequence, as each requirement depends on the preceding ones.

| # | Requirement | Fails as |
| --- | --- | --- |
| 1 | A Python that can `import mcp, reapy, numpy` | No REAPER tools, or only `reaper_setup_status` |
| 2 | REAPER running | Socket errors from every tool |
| 3 | REAPER's distant API configured | `WinError 10053`, connection refused |
| 4 | `claude_bridge.lua` loaded in REAPER | Bridge commands time out |

Requirements 3 and 4 are independent. The MCP server requires the distant API, and the file bridge requires the Lua listener. If one fails, the other remains functional. Determine which route is broken before modifying configuration.

## Start here

On Windows, run the health check:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/install/health-check.ps1"
```

Review the output of the health check. Output lines indicate `[ok]`, `[warn]`, or `[FAIL]`. Failures include a `->` fix. The script checks all four requirements and the plugin installation path.

## 1. The Python environment

The MCP server uses a specific Python environment, not necessarily the system default. A launcher locates an interpreter capable of importing dependencies, prioritizing the plugin's virtual environment. To verify:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.py" --check
```

Build or repair it:

```bash
python "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.py"
```

This command creates a virtualenv in the host's persistent plugin data directory and installs `requirements.txt`. It does not modify system files and persists across plugin updates. Installation duration is primarily dependent on compiling or downloading librosa.

**Do not instruct the user to run `pip install` on their system Python environment.** Modifying the system Python may affect other projects, and the launcher may not select that interpreter.

If installation fails due to missing wheels for the Python version, bootstrap using a different interpreter. The launcher locates the venv regardless of the originating Python version:

```bash
py -3.12 "${CLAUDE_PLUGIN_ROOT}/scripts/bootstrap.py" --recreate
```

The environment path is `%USERPROFILE%\.reaper-for-claude\venv`. This path is fixed and resolved identically by the installer and the server. It is not derived from the host's plugin data directory, as that directory exists only when Claude launches the server.

**Two interpreters require reapy; the virtualenv provides it for only one.** REAPER loads a Python shared library and executes ReaScripts within its own process using the base Python installation, which does not access the virtualenv. `reapy` must be importable by the base Python, otherwise `activate_reapy_server` fails and the distant API does not start. `bootstrap.py` installs `python-reapy` in the base environment. Manual installation command:

```
"<base python>" -m pip install --user python-reapy
```

`--check` and the health check report the status of both environments separately.

**The version limit applies to configuring REAPER, not to running the server.**
Two interpreters, two different requirements:

| Job | Requirement |
| --- | --- |
| Running the MCP server | any Python where the imports work; 3.14 is fine |
| Configuring REAPER (`enable_reapy.py`) | **3.12 or older** |

reapy 0.10.0 fails during `reaper.ini` modification on Python 3.13+ due to changes in `configparser` regarding unnamed sections. This failure results in an empty `reaper.ini` file, deleting all REAPER preferences.

`enable_reapy.py` blocks execution on Python 3.13+, creates a backup, and restores the file if truncation occurs. The installer selects a Python 3.12 or older interpreter for this step. If the health check indicates REAPER cannot be reconfigured, execute the following fix:

```
winget install -e --id Python.Python.3.12
py -3.12 -m pip install python-reapy
```

Do not attempt to execute the configuration step using Python 3.13 or newer.

**`No module named 'mcp.server.fastmcp'`** indicates `mcp` version 2.0 or newer is installed. This version removes the FastMCP API required by the tool modules. `requirements.txt` specifies `mcp<2.0.0`. Environments created prior to this specification must be rebuilt using `--recreate`.

To set a specific interpreter, configure the `REAPER_MCP_PYTHON` environment variable with the full path before starting Claude.

## 2-3. REAPER and the distant API

External connection to REAPER requires four idempotent configuration steps, performed by `reapy.config.configure_reaper()`:

| # | Step | Writes to |
| --- | --- | --- |
| 1 | Enable Python ReaScript + path to the Python shared library | `reaper.ini` |
| 2 | Add a web interface on port **2307** | `reaper.ini` |
| 3 | Register the `activate_reapy_server` ReaScript | `reaper-kb.ini` |
| 4 | Record that action's id | `reaper-extstate.ini` |

**Close REAPER prior to execution.** REAPER overwrites `reaper.ini` upon exit. Modifications made while REAPER is running will be discarded.

```bash
python "${CLAUDE_PLUGIN_ROOT}/reaper/enable_reapy.py" --check    # report only
python "${CLAUDE_PLUGIN_ROOT}/reaper/enable_reapy.py"            # configure
python "${CLAUDE_PLUGIN_ROOT}/reaper/enable_reapy.py" --repair   # fix port 2306
```

**Port 2306 is not a web interface.** It is `REAPY_SERVER_PORT`, used by the reapy server socket. Previous setup scripts assigned a web interface to this port, preventing server binding and resulting in `WinError 10053`. The `--repair` flag resolves this issue.

To execute the script within REAPER: Navigate to *Actions → Show action list… → ReaScript: Run…* → select `<REAPER resource path>/Scripts/enable_reapy.py`. Restart REAPER after execution.

## 4. The bridge listener

`claude_bridge.lua` is located in `<REAPER resource path>/Scripts/`. A `dofile()` statement is appended to `__startup.lua` to ensure REAPER loads the script at launch. The installer backs up existing `__startup.lua` files and appends the line to prevent data loss for users with custom startup scripts.

To verify liveness, read `status.txt` in `<REAPER resource path>/claude_bridge/`. A recent timestamp indicates the listener is active. An old timestamp indicates REAPER is closed or the script was terminated (e.g., via *Actions → Close all running scripts*).

## Installing from scratch

Windows installation automation command:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File "${CLAUDE_PLUGIN_ROOT}/install/configure-plugin.ps1"
```

This script bootstraps Python, installs REAPER-side files, configures the distant API, and executes the health check. The script is idempotent. REAPER must be closed before execution.

macOS and Linux require manual execution of REAPER-side steps: Copy `reaper/claude_bridge.lua` to `<REAPER resource path>/Scripts/`. Add the `dofile()` command to `__startup.lua`. Execute `reaper/enable_reapy.py`.

## Which surface is this running on?

Determine the operating surface prior to diagnosis, as capabilities vary by platform.

| Surface | MCP server | File bridge | Notes |
| --- | --- | --- | --- |
| **Claude Code** | Yes | Yes | Full access. `reaper-bridge` is present in the Bash tool PATH. |
| **Claude Desktop** | Yes | Only where shell access exists | Local MCP servers execute on the local machine. |
| **claude.ai (web)** | No | No | No local machine access. Skills function as reference material; tools are unavailable. |

Users on the web application will not have access to REAPER tools. Do not direct web application users to the health check. Instruct them to use Claude Desktop or Claude Code on the system hosting REAPER.

## After any change

1. Restart REAPER if `reaper.ini` was modified.
2. Restart Claude or execute `/reload-plugins` in Claude Code. MCP server configuration changes require a restart.
3. Verify operation using the command: `Check the current REAPER project info.`
