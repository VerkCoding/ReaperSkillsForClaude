# REAPER for Claude: documentation

This document provides detailed information about installation, system changes, architecture, and references.

- [Installing](#installing)
- [Troubleshooting](#troubleshooting)
- [How it works](#how-it-works)
- [Reference](#reference)
- [Development](#development)

---

## Installing

`RunThisToStart.bat` provides a menu with three options:

| | |
| --- | --- |
| **`[1]` Install Everything** | Closes applications, creates a snapshot, installs missing dependencies, performs first run, configures the plugin, and runs a health check. |
| **`[2]` Revert Everything** | Restores the snapshot and removes files added by `[1]`. |
| **`[3]` Prepare Offline Files** | Downloads dependencies for offline installation on another machine. See [The download cache](#the-download-cache). |

### What it installs

The script installs packages using the respective vendor's installer (reaper.fm, claude.ai, git-scm) via the winget community source.

| Package | If already installed |
| --- | --- |
| Python 3.12 | Upgraded |
| Git | Upgraded |
| REAPER | Skipped |
| Claude Desktop | Skipped |
| Claude Code | Skipped |

The script skips REAPER and Claude if they are already installed to prevent accidental modification of existing user data, such as presets, FX chains, and local history.

### What it touches outside this folder

| Component | Location |
| --- | --- |
| Dependency virtual environment | `%USERPROFILE%\.reaper-for-claude\venv` |
| `python-reapy` module | Base Python user site-packages ([Details](#two-interpreters)) |
| `claude_bridge.lua`, `enable_reapy.py` | `<REAPER>\Scripts\` |
| `__startup.lua` | `<REAPER>\Scripts\` (Appends one `dofile()` call; does not overwrite) |
| Distant API settings | `reaper.ini`, `reaper-kb.ini`, `reaper-extstate.ini` |
| MCP server entry | `claude_desktop_config.json` (Backed up before modification) |
| Snapshots and logs | `%USERPROFILE%\.reaper-for-claude\` |

### What it asks of you

**Save open work.** `[1]` requests REAPER and Claude to close. Configuration changes made while the applications are running are discarded upon exit.

The script sends a close request, allowing REAPER to display its save prompt. Applications are not force-quit, except during the first-run step.

**A Claude account sign-in is required** for the plugin to load.

| Condition | Behavior |
| --- | --- |
| Press Enter without closing REAPER | The REAPER configuration step is skipped. Close the application and re-run `[1]`. |
| Press Enter without closing Claude | The script continues and logs a warning regarding `claude_desktop_config.json`. |
| Close Claude without signing in | Claude reopens to prompt for sign-in again. |
| Never sign in | After six attempts, the script continues and reports the missing sign-in. |
| Run from a terminal inside Claude | The script detects the host environment and aborts the closing request. |

Incomplete steps are listed at the end. The installation script can be safely re-run.

### Requirements

These are configured automatically by `[1]`:

| | |
| --- | --- |
| **REAPER** | Version 6 or 7+. If installed by the script, it is launched once to generate the resource folder. |
| **Python** | Version 3.8+ is required. The script installs Python 3.12 alongside existing versions without changing the default system interpreter. |

### What `[2]` restores

`[1]` copies target configuration files into `%USERPROFILE%\.reaper-for-claude\backups\original\` before modification. The script notes if a file did not exist prior to execution.

The snapshot is written once and is not overwritten. This prevents capturing a partially modified state during subsequent runs.

`[2]` restores these files, removes the virtual environment, and unregisters the plugin. It does not uninstall REAPER, Claude, Python, or Git. Projects, media, presets, and conversations are not modified.

If the script installed Claude but no sign-in occurred, `[2]` renames the Claude profile directory instead of deleting it. This handles interrupted profile creation without deleting user data.

---

## Troubleshooting

Run the health check script:

```powershell
powershell -ExecutionPolicy Bypass -File install\health-check.ps1
```

The script outputs `[ok]`, `[warn]`, or `[FAIL]` with corresponding fixes. It verifies the directory layout, server startup, distant API configuration, bridge heartbeat, live connection status, Claude integration, and checks for conflicting older installations.

| Symptom | Cause |
| --- | --- |
| No REAPER tools at all | Expected behavior on claude.ai. For local installations, the server failed to start; execute the health check. |
| Tools fail with a socket error | REAPER is not running, or the distant API is unconfigured. Persistent failures indicate configuration issues. |
| Bridge times out | REAPER is not running `claude_bridge.lua`. Review `status.txt` in the bridge directory. |
| `PARSE_ERROR` on byte one | Lua script contains a UTF-8 BOM. Use `--code` or `--lua-file` to strip it. |
| Renders are silent | Caused by the `offlineinact` preference. Refer to [rendering.md](skills/reaper-mcp/references/rendering.md). |
| Duplicate tools | An older version is loading from `~\.claude\skills\`. Remove `reaper-mcp` and `reaper-ai-engineer-skill`. |
| Edits to local files do nothing | The plugin was installed via the marketplace. See [Editing the plugin](#editing-the-plugin). |

**"ReaScript task control" dialog on first run**: Select **New instance**. Do not select **Terminate instances**, as this action stops both the reapy server and the Lua bridge.

**`No module named 'mcp.server.fastmcp'`**: This occurs if `mcp` version 2.0 or higher is installed. Rebuild the environment: `python scripts\bootstrap.py --recreate`.

**`import reapy` fails**: Rebuild the environment: `py -3.12 scripts\bootstrap.py --recreate`.

**Socket error / `WinError 10053`**: Run `python reaper\enable_reapy.py --check`. This indicates a web interface is bound to port **2306**. Execute `--repair`, then restart REAPER.

**Claude Desktop reverted to an old MCP entry**: Claude Desktop overwrites `claude_desktop_config.json` upon exit. Close Claude Desktop entirely (including the system tray), execute `configure-plugin.ps1 -Only claude`, and restart the application.

**The marketplace stopped resolving**: Local marketplace entries use absolute paths. If the directory is moved, re-add it: `claude plugin marketplace add <new path>`.

**The dependency install is slow**: This is expected due to the total size (477 MB across 12,212 files). Performance is reduced in sandbox environments due to the lack of a pip cache, antivirus scanning, and virtual disk I/O. The installation occurs once, and the virtual environment persists across updates.

If the process hangs during `Building wheel for llvmlite`, pip is compiling LLVM, which takes substantial time and frequently fails. Use an older Python interpreter: `py -3.12 scripts\bootstrap.py --recreate`.

---

## How it works

### Layout

```
ReaperSkillsForClaude/              
├── .claude-plugin/                 
├── skills/                         
│   ├── reaper-core-setup/          
│   ├── reaper-mcp/                 
│   └── reaper-audio-engineer/      
├── scripts/                        
│   ├── launch_server.py            
│   ├── bootstrap.py                
│   ├── bridge.py                   
│   ├── benchmark_tools.py          
│   └── doctor.py                   
├── bin/reaper-bridge               
├── src/reaper_mcp/                 
├── reaper/                         
└── install/                        
```

Directory structure principles:

- **`skills/` contains documentation files.**
- **`scripts/` contains cross-platform executables.**
- **`install/` contains Windows-specific PowerShell scripts.**
- **The MCP server is declared in `plugin.json`.**

### Two interpreters

REAPER does not execute Python as a subprocess. It loads a Python shared library to run ReaScripts within its own process. It cannot utilize standard virtual environments.

Therefore, `reapy` must be accessible to two distinct interpreters:

| Interpreter | Function | Requirements |
| --- | --- | --- |
| The virtual environment | Executes the MCP server externally | All packages in `requirements.txt` |
| REAPER's embedded Python | Executes `activate_reapy_server.py` | The `reapy` package |

To ensure connectivity, `bootstrap.py` installs `python-reapy` (along with `psutil` and `typing-extensions`) into the base Python user site-packages directory. The larger numeric libraries remain isolated in the virtual environment.

The system `PATH` is modified only if no usable Python 3 installation is detected.

### Starting the server

`plugin.json` executes `scripts/launch_server.py`. If a stdio server terminates during startup, it reports 'server failed to connect'. To prevent this, the launcher:

1. **Probes interpreters** (`REAPER_MCP_PYTHON`, the virtual environment, the current interpreter, and versions like `py -3.12`) to verify dependency imports.
2. **Re-executes itself** using a valid interpreter.
3. **Initializes a diagnostic server** if dependencies are unmet, providing the `reaper_setup_status` tool instead of a failed connection.

Timeouts during probing are distinguished from failures. Import times for large modules may exceed timeouts on initial runs. The managed virtual environment is retained if its probe times out, as its integrity was verified during the build process.

Dependencies are not installed during server startup to prevent initialization timeouts.

### Reaching REAPER

The plugin uses two independent communication routes with REAPER:

- **The MCP server** connects via reapy's distant API. This requires Python ReaScript, a web interface on port **2307**, and the `activate_reapy_server` action. These are configured by `reaper/enable_reapy.py`.
- **The file bridge** executes Lua scripts within REAPER for operations unsupported by the standard API, such as offline DSP measurement and plugin parameter resolution.

```bash
reaper-bridge --code 'return reaper.GetAppVersion()'
```

The `reaper-bridge` executable accepts Lua code directly to avoid UTF-8 BOM parsing errors caused by temporary files.

Before importing reapy, the server polls for REAPER's published server port. This read operation prevents reapy from incorrectly triggering the ReaScript action multiple times.

### The download cache

`[1]` utilizes the `downloadCache\` directory. The setup uses `winget download` instead of `winget install`. This retains the downloaded files and manifests, preventing redundant downloads in ephemeral environments and avoiding IP rate-limiting.

- Downloaded files are matched loosely by name.
- The cache acts as an optimization. If missing, files are downloaded directly.
- Existing application installations are not overwritten by cached installers.
- Claude Code is downloaded but its manifest is retained as a marker, as winget extracts it rather than running a standalone installer.
- The directory is gitignored and can be safely deleted.
- Downloads are validated using HTTP status checks and file size minimums.

### Logs

Logs are written to `%USERPROFILE%\.reaper-for-claude\logs\` in timestamped, level-tagged plain text format.

```
21:54:21  STEP  Applications
21:54:21  OK    Git ready (from downloadCache).
21:54:21  WARN  REAPER failed (exit 1); retrying once...
21:54:21  FAIL  Claude Code: winget exited 1.
```

The `setup-<time>.log` records structured installation steps. The `transcript-<time>.log` captures raw terminal output, including winget and pip logs.

---

## Reference

### Scripts

The PowerShell scripts in `install\` can be executed independently.

| Script | Description |
| --- | --- |
| `install-everything.ps1` | Executes `[1]`. Use `-SkipApps` to configure only. |
| `revert-everything.ps1` | Executes `[2]`. Use `-From <dir>` to select a snapshot. |
| `fill-download-cache.ps1` | Executes `[3]`. Use `-Force` to re-download; `-Consolidate` to copy external files. |
| `configure-plugin.ps1` | Configures dependencies, REAPER bridge, and Claude. |
| `install-winget.ps1` | Installs or repairs winget. |
| `install-python.ps1` | Installs Python via direct download. |
| `backup-restore.ps1` | Creates or restores snapshots (`-Backup`, `-Restore`, `-List`). |
| `health-check.ps1` | Diagnostic utility. |

### Environment variables

| Variable | Description |
| --- | --- |
| `REAPER_MCP_PYTHON` | Overrides the Python interpreter used for the server. |
| `REAPER_MCP_REAPER_PYTHON` | Specifies the interpreter embedded by REAPER (written to `reaper.ini`). |
| `REAPER_MCP_DATA_DIR` | Defines the virtual environment path. Default: `~\.reaper-for-claude`. |
| `REAPER_MCP_PLUGIN_ROOT` | Defines the plugin root directory. |

### Pinned versions

| Dependency | Version |
| --- | --- |
| **Python** | `3.12` |
| **winget** | `v1.8.1911` |
| **UI.Xaml** | `v2.8.6` |
| **VCLibs** | `14.00 Desktop` |

> [!WARNING]
> Python 3.13 introduces unnamed sections to `configparser`. The `reapy` 0.10.0 package attempts to cast these names to lowercase, resulting in a crash that empties `reaper.ini`. The installation script mandates Python 3.12 or older for configuration to prevent data loss. The server component supports Python 3.14.

Python 3.12 is selected as libraries like `numba` and `llvmlite` experience delays in releasing wheels for newer Python versions.

The winget version is pinned to 1.8.1911 to avoid a framework dependency introduced in 1.9+, which causes deployment errors.

### No winget?

For environments lacking App Installer, `[1]` performs the following:

1. Registers an existing App Installer provision.
2. Deploys VCLibs, UI.Xaml, and the winget bundle directly using HTTPS.
3. Falls back to a PowerShell Gallery bootstrap if direct deployment is blocked by policy.

Downloads utilize `curl.exe` with strict failure flags and size validation to ensure integrity.

---

## Development

### Editing the plugin

Installing from a marketplace copies the directory into a versioned cache. Modifications to local files will not apply until the `version` field is incremented and the plugin is reinstalled. For live editing, execute:

```powershell
powershell -ExecutionPolicy Bypass -File install\configure-plugin.ps1 -Only claude -Link
```

This creates a junction at `~\.claude\skills\reaper-for-claude` pointing to the repository.

Do not use both the marketplace installation and the local junction simultaneously, as Claude Code resolves conflicts in favor of the marketplace. To remove the marketplace entry:

```powershell
claude plugin uninstall reaper-for-claude@reaper-skills-for-claude
claude plugin marketplace remove reaper-skills-for-claude
```

The `-Link` flag executes these removal commands automatically.

### Installing by hand

To manually install the plugin:

| Surface | Command |
| --- | --- |
| **Claude Code** | `/plugin marketplace add <this folder>` then `/plugin install reaper-for-claude@reaper-skills-for-claude` |
| **Claude Desktop / claude.ai** | Customize → Plugins → Personal plugins → **+** → Add marketplace |

For Claude Desktop and claude.ai, provide the remote repository URL:

```
https://github.com/VerkCoding/ReaperSkillsForClaude.git
```

### Porting

The core server components and Lua scripts are cross-platform. Adding macOS or Linux support requires a platform-specific installer and updating the `command` field in `plugin.json` from `python` to `python3`.
