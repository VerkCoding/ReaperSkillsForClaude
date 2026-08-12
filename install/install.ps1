<#
.SYNOPSIS
  Installer for the REAPER for Claude plugin.

.DESCRIPTION
  The repository is the plugin. Nothing is copied into a skills directory any
  more: Claude Code registers this folder as a marketplace and installs from it,
  and Claude Desktop installs the same plugin through its own Plugins UI. That
  removes the class of bug where an edited repository and an installed copy
  drifted apart, and where both loaded at once and every tool appeared twice.

  Four independent things get set up. Each can fail without taking the others
  down, and each is idempotent, so re-running after a failure is safe.

    1. Python      a virtualenv the plugin owns, holding the server's deps
    2. REAPER      the Lua bridge listener, loaded from __startup.lua
    3. REAPER      the reapy distant API, which the MCP server connects through
    4. Claude      Claude Code via the CLI; Claude Desktop via its config

.PARAMETER Only
  Restrict the run to one area: python, reaper, claude.

.PARAMETER Link
  Install into Claude Code as a live-editing skills-directory plugin instead of
  a marketplace install. A marketplace install copies the plugin into a
  versioned cache, so edits here would not reach Claude until the version is
  bumped and it is reinstalled. Use this while developing the plugin.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install.ps1 -Only reaper -Force
#>
[CmdletBinding()]
param(
    [ValidateSet('python', 'reaper', 'claude')] [string]$Only,
    [string]$ReaperResourcePath,
    [switch]$SkipBootstrap,
    [switch]$SkipDesktop,
    [switch]$SkipCode,
    [switch]$SkipReaperConfig,
    [switch]$Link,
    [switch]$Force,
    # Set by setup-all.ps1, which prints the combined summary itself.
    #
    # Without this there are two "DONE" banners, and they can disagree: each
    # script keeps its own $script:Problems, and `&` runs this one in a child
    # scope, so anything found here never reaches the wrapper's list. The user
    # then sees "DONE - with 1 thing to fix" under "DONE - nothing left to do by
    # hand" and has no way to tell which one is true. Writing the problems to
    # this path hands them over instead, and the banner below is skipped.
    [string]$ProblemsOut
)

$ErrorActionPreference = 'Stop'

function Write-Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  .      $m" -ForegroundColor Gray }
function Write-Warn2($m){ Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

$script:Problems = @()
function Add-Problem($m) { $script:Problems += $m }

function Invoke-Native {
    <#
      Run a native command without letting its stderr abort the script.

      Windows PowerShell 5.1 wraps every stderr line from a native executable in
      an ErrorRecord. Under $ErrorActionPreference = 'Stop' that is a TERMINATING
      error, so a pip progress note or a warning from the claude CLI - output
      that means nothing is wrong - throws NativeCommandError and takes the rest
      of the install with it. Observed as:

          [FAIL] Plugin configuration failed: RemoteException

      which stops the REAPER bridge, the distant API and the Claude registration
      from running at all, while the earlier steps report success.

      Exit codes are what these commands actually communicate, and every caller
      here already checks $LASTEXITCODE.
    #>
    param([scriptblock]$Block)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block } finally { $ErrorActionPreference = $prev }
}

$doPython = (-not $Only) -or ($Only -eq 'python')
$doReaper = (-not $Only) -or ($Only -eq 'reaper')
$doClaude = (-not $Only) -or ($Only -eq 'claude')

# ---------------------------------------------------------------------------
# Anchor on the script's own location. $PSScriptRoot is install\, so the plugin
# root is its parent. Nothing here uses a path relative to the caller's cwd -
# "Run as administrator" starts you in System32.
# ---------------------------------------------------------------------------
$PluginRoot = Split-Path -Parent $PSScriptRoot
$Bootstrap  = Join-Path $PluginRoot 'scripts\bootstrap.py'
$Launcher   = Join-Path $PluginRoot 'scripts\launch_server.py'
$ReaperSrc  = Join-Path $PluginRoot 'reaper'
$EnableRpy  = Join-Path $ReaperSrc 'enable_reapy.py'
$BridgeLua  = Join-Path $ReaperSrc 'claude_bridge.lua'
$Manifest   = Join-Path $PluginRoot '.claude-plugin\plugin.json'

foreach ($p in @($Bootstrap, $Launcher, $EnableRpy, $BridgeLua, $Manifest)) {
    if (-not (Test-Path $p)) { throw "Missing plugin file: $p" }
}

$MarketplaceName = 'reaper-skills-for-claude'
$PluginName      = 'reaper-for-claude'
$PluginRef       = "$PluginName@$MarketplaceName"

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  REAPER for Claude - installer" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Info "Plugin: $PluginRoot"

# ---------------------------------------------------------------------------
# 1. Python environment
# ---------------------------------------------------------------------------
$PythonExe = $null
if ($doPython -or $doReaper) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ($doPython) {
    Write-Step "Python environment"

    if (-not $PythonExe) {
        Write-Err "python is not on PATH."
        Add-Problem "Install Python from python.org, ticking 'Add Python to PATH', then re-run."
    } elseif ($SkipBootstrap) {
        Write-Warn2 "Skipping the dependency install (-SkipBootstrap)."
    } else {
        $pyVer = & python -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        Write-Info "Building the environment with Python $pyVer ($PythonExe)"
        Write-Info "A cold install takes a few minutes - librosa is the slow one."

        # bootstrap.py owns the whole decision: where the venv goes, whether the
        # requirements changed, and whether the result actually imports. The
        # installer deliberately does not second-guess it, so there is one
        # implementation of that logic rather than two that can disagree.
        Invoke-Native { & python $Bootstrap }
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Dependencies installed."
        } else {
            Write-Err "bootstrap.py exited $LASTEXITCODE."
            Add-Problem "Read the pip output above. If no wheel exists for Python $pyVer, try: py -3.12 `"$Bootstrap`" --recreate"
        }
    }
}

# ---------------------------------------------------------------------------
# Choosing the interpreter that configures REAPER.
#
# This is NOT the interpreter that runs the server, and conflating them destroys
# data. Two independent requirements:
#
#   * `import reapy` must work. bootstrap.py installs into a virtualenv, so on a
#     fresh machine the system Python has nothing, and configuring would fail at
#     the import - leaving a working-looking install where every REAPER tool
#     errors later.
#
#   * Python must be 3.12 or older. reapy 0.10.0 crashes partway through
#     rewriting reaper.ini on 3.13+, because configparser gained unnamed
#     sections and reapy calls .lower() on the sentinel. The crash leaves
#     reaper.ini EMPTY - every REAPER preference gone, with nothing in the error
#     naming the file. enable_reapy.py refuses to run there, and this picks an
#     interpreter that will not hit it in the first place.
#
# The server has no such limit and happily runs on 3.14, which is why it is
# selected separately.
# ---------------------------------------------------------------------------
$ReapyPython     = $null
$ReapyPythonArgs = @()

# bootstrap.py owns this choice, so ask it rather than repeating the search.
# The interpreter that runs enable_reapy.py BECOMES REAPER's embedded Python -
# reapy writes its shared library path into reaper.ini - so this decision and
# "where does reapy get installed" are the same decision, and having two
# implementations of it would eventually point REAPER at one interpreter while
# installing reapy into another.
if ($PythonExe) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $chosen = & python $Bootstrap --print-reaper-python 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0 -and $chosen) {
            $chosen = "$chosen".Trim()
            # Two conditions, both required, and neither is bootstrap's job to
            # enforce: it reports the best available interpreter even when the
            # best available is unsuitable.
            #
            #   <= 3.12   or enable_reapy refuses and reaper.ini is left alone
            #   has reapy or configuring fails at the import
            #
            # Rejecting here rather than attempting means the user gets the
            # "install 3.12" instruction instead of watching a step fail first.
            $ver = & $chosen -c 'import sys; sys.exit(0 if sys.version_info[:2] <= (3, 12) else 1)' 2>&1
            $verOk = ($LASTEXITCODE -eq 0)
            $null = & $chosen -c 'import reapy' 2>&1
            $reapyOk = ($LASTEXITCODE -eq 0)

            if ($verOk -and $reapyOk -and (Test-Path $chosen)) {
                $ReapyPython = $chosen
            } elseif (-not $verOk) {
                Write-Info "Best available interpreter is $chosen, which is newer than 3.12."
            }
        }
    } catch { }
    $ErrorActionPreference = $prevEAP
}

function Invoke-Reapy {
    param([string[]]$ScriptArgs)
    $all = @()
    if ($ReapyPythonArgs.Count -gt 0) { $all += $ReapyPythonArgs }
    $all += $ScriptArgs
    & $ReapyPython @all
}

# ---------------------------------------------------------------------------
# 2. REAPER: bridge listener
# ---------------------------------------------------------------------------
$ReaperFound = $false
$ScriptsDir  = $null

if ($doReaper) {
    Write-Step "REAPER bridge listener"

    if (-not $ReaperResourcePath) { $ReaperResourcePath = Join-Path $env:APPDATA 'REAPER' }
    $ReaperFound = Test-Path (Join-Path $ReaperResourcePath 'reaper.ini')

    if (-not $ReaperFound) {
        Write-Err "No reaper.ini under $ReaperResourcePath"
        Add-Problem "Launch REAPER once so it creates its config, then re-run. Portable install? Pass -ReaperResourcePath."
    } else {
        Write-Info "Resource path: $ReaperResourcePath"
        $ScriptsDir = Join-Path $ReaperResourcePath 'Scripts'
        New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null

        Copy-Item -Force $BridgeLua (Join-Path $ScriptsDir 'claude_bridge.lua')
        Write-Ok "claude_bridge.lua -> $ScriptsDir"

        # Also drop enable_reapy.py next to it, so it can be run from REAPER's
        # action list later without going back to this folder.
        Copy-Item -Force $EnableRpy (Join-Path $ScriptsDir 'enable_reapy.py')
        Write-Ok "enable_reapy.py -> $ScriptsDir"

        # __startup.lua is REAPER's auto-run hook. Overwriting it would destroy
        # whatever startup script the user already has, so add one dofile() line.
        $startup = Join-Path $ScriptsDir '__startup.lua'
        $loader  = @'

-- >>> claude-bridge (managed by REAPER for Claude) >>>
do
  local sep = package.config:sub(1, 1)
  local f = reaper.GetResourcePath() .. sep .. "Scripts" .. sep .. "claude_bridge.lua"
  local h = io.open(f, "r")
  if h then h:close(); dofile(f) end
end
-- <<< claude-bridge <<<
'@

        if (-not (Test-Path $startup)) {
            [System.IO.File]::WriteAllText($startup, $loader.TrimStart("`r", "`n"),
                                           (New-Object System.Text.UTF8Encoding $false))
            Write-Ok "Created __startup.lua with the bridge loader."
        } elseif ((Get-Content $startup -Raw) -match 'claude_bridge') {
            Write-Ok "__startup.lua already loads the bridge; left untouched."
        } else {
            Copy-Item -Force $startup "$startup.bak"
            Add-Content -Path $startup -Value $loader -Encoding UTF8
            Write-Ok "Appended the bridge loader to your __startup.lua (backup: __startup.lua.bak)."
        }

        Write-Info "Bridge directory: $(Join-Path $ReaperResourcePath 'claude_bridge')"
    }

    # -----------------------------------------------------------------------
    # 3. REAPER: distant API
    #    reapy.config.configure_reaper() works from outside REAPER, so this is
    #    automated - but only with REAPER closed, because REAPER rewrites
    #    reaper.ini on exit and would discard whatever was written underneath it.
    # -----------------------------------------------------------------------
    Write-Step "REAPER distant API"

    if ($SkipReaperConfig) {
        Write-Warn2 "Skipping (-SkipReaperConfig)."
    } elseif (-not $ReaperFound -or -not $PythonExe) {
        Write-Warn2 "Skipped: needs both Python and a REAPER config folder."
        Add-Problem "After fixing the above, run: python `"$EnableRpy`""
    } elseif (-not $ReapyPython) {
        # Refusing is the safe outcome. Running this under 3.13+ would empty
        # reaper.ini, and no amount of connectivity is worth that.
        Write-Err "No Python 3.12-or-older interpreter with reapy is available."
        Write-Info "reapy 0.10.0 empties reaper.ini when it configures REAPER under"
        Write-Info "Python 3.13+, so this step is skipped rather than risked."
        Write-Info "Everything else - the bridge, the server, Claude - is unaffected."
        Add-Problem "Install Python 3.12 (winget install -e --id Python.Python.3.12), then: py -3.12 -m pip install python-reapy  and re-run with -Only reaper"
    } else {
        $reaperRunning = @(Get-Process -Name 'reaper' -ErrorAction SilentlyContinue).Count -gt 0
        if ($reaperRunning -and -not $Force) {
            Write-Warn2 "REAPER is running. It rewrites reaper.ini on exit, which would discard these changes."
            Write-Info  "Close REAPER and re-run, or run this from inside REAPER:"
            Write-Info  "  Actions > Show action list > ReaScript: Run... > $ScriptsDir\enable_reapy.py"
            Add-Problem "Distant API not configured: REAPER was open. Close it and re-run."
        } else {
            Write-Info "Using interpreter: $ReapyPython $($ReapyPythonArgs -join ' ')"
            # Not $args - that is an automatic variable in PowerShell.
            $pyArgs = @($EnableRpy, '--resource-path', $ReaperResourcePath)
            if ($Force) { $pyArgs += '--force' }
            Invoke-Native { Invoke-Reapy $pyArgs }
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Distant API configured."
            } else {
                Write-Err "enable_reapy.py exited $LASTEXITCODE."
                Add-Problem "Run manually with REAPER closed: $ReapyPython `"$EnableRpy`""
            }
        }
    }
}

# ---------------------------------------------------------------------------
# 4. Claude
# ---------------------------------------------------------------------------
if ($doClaude) {
    Write-Step "Claude Code"

    $LinkPath = Join-Path $env:USERPROFILE ".claude\skills\$PluginName"

    if ($SkipCode) {
        Write-Warn2 "Skipping Claude Code (-SkipCode)."
    } elseif ($Link) {
        # A folder under a skills directory containing .claude-plugin/plugin.json
        # loads as a plugin discovered *in place* - no copy, so edits are live.
        # A junction rather than a copy is the whole point; a copy would
        # reintroduce exactly the drift this layout exists to remove.
        $skillsDir = Split-Path -Parent $LinkPath
        New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null

        $existing = Get-Item $LinkPath -ErrorAction SilentlyContinue
        if ($existing) {
            $isLink = $existing.Attributes -band [IO.FileAttributes]::ReparsePoint
            if (-not $isLink) {
                Write-Err "$LinkPath exists and is a real directory, not a link."
                Add-Problem "Delete or rename $LinkPath, then re-run with -Link."
            } else {
                # Junctions are cheap to recreate and the target may have moved.
                (Get-Item $LinkPath).Delete()
                $existing = $null
            }
        }

        if (-not (Test-Path $LinkPath)) {
            # mklink /J needs no elevation, unlike a symbolic link.
            & cmd /c mklink /J "`"$LinkPath`"" "`"$PluginRoot`"" | Out-Null
            if (Test-Path $LinkPath) {
                Write-Ok "Linked $LinkPath -> $PluginRoot"
                Write-Info "Loads as $PluginName@skills-dir. Edits here are live."
            } else {
                Write-Err "Could not create the junction."
                Add-Problem "Create it by hand: mklink /J `"$LinkPath`" `"$PluginRoot`""
            }
        }

        # The two routes share a plugin name, and Claude Code resolves that in
        # favour of the marketplace - silently. Left in place, the link loads
        # nothing and the next edit here simply appears to do nothing.
        #
        # Uninstalling is not sufficient: the marketplace ENTRY reserves the
        # name whether or not anything is installed from it. Both have to go,
        # which is why this clears them rather than printing advice.
        $claude = Get-Command claude -ErrorAction SilentlyContinue
        if ($claude) {
            $listing = Invoke-Native { & claude plugin list 2>&1 | Out-String }
            if ($listing -match [regex]::Escape($PluginRef)) {
                Write-Info "Removing the marketplace install, which would shadow the link."
                & claude plugin uninstall $PluginRef 2>&1 | ForEach-Object { Write-Info $_ }
            }
            $markets = & claude plugin marketplace list 2>&1 | Out-String
            if ($markets -match [regex]::Escape($MarketplaceName)) {
                Write-Info "Removing the marketplace registration, which reserves the name."
                & claude plugin marketplace remove $MarketplaceName 2>&1 | ForEach-Object { Write-Info $_ }
            }
            $after = & claude plugin list 2>&1 | Out-String
            if ($after -match 'reaper-for-claude@skills-dir') { Write-Ok "Loaded in place; edits are live." }
            else { Write-Warn2 "Not loaded yet - restart Claude Code or run /reload-plugins." }
        }
    } else {
        $claude = Get-Command claude -ErrorAction SilentlyContinue

        if (Test-Path $LinkPath) {
            Write-Warn2 "A developer link exists at $LinkPath"
            Add-Problem "Remove it, or everything loads twice: rmdir `"$LinkPath`""
        }

        if (-not $claude) {
            Write-Warn2 "The claude CLI is not on PATH; registering by hand instead."
            Write-Info  "In Claude Code, run:"
            Write-Info  "  /plugin marketplace add `"$PluginRoot`""
            Write-Info  "  /plugin install $PluginRef"
        } else {
            # --scope user is the CLI default, but stated explicitly because it
            # is the design intent rather than a convenience: the plugin belongs
            # to this user account, in every project, and never to one
            # repository or to the machine as a whole. A project-scoped install
            # would work only inside this folder, which is not what anyone wants
            # from an audio toolkit.
            Invoke-Native {
                & claude plugin marketplace add "$PluginRoot" --scope user 2>&1 |
                    ForEach-Object { Write-Info $_ }
                & claude plugin install $PluginRef --scope user 2>&1 |
                    ForEach-Object { Write-Info $_ }
            }
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Installed $PluginRef"
            } else {
                Write-Warn2 "The CLI returned $LASTEXITCODE - it may already be installed."
                Write-Info  "Check with: claude plugin list"
            }
        }
    }

    Write-Step "Claude Desktop"

    if ($SkipDesktop) {
        Write-Warn2 "Skipping Claude Desktop (-SkipDesktop)."
    } else {
        Write-Info "Plugin (recommended - brings the skills as well as the tools):"
        Write-Info "  Customize > Plugins > Personal plugins > + > Add marketplace"
        Write-Info "  Point it at this repository once it is pushed to a git host."
        Write-Info ""
        Write-Info "Writing the MCP server directly into the Desktop config as well, so the"
        Write-Info "REAPER tools work immediately without waiting on that."

        # Claude Desktop holds this file's contents in memory and rewrites it -
        # exactly like REAPER and reaper.ini. An edit made underneath a running
        # Desktop can be silently reverted to whatever it had before, which
        # presents much later as a server pointing at a path that no longer
        # exists. Warn rather than refuse: the write is harmless when it sticks,
        # and the health check catches it when it does not.
        # Path-aware, because the Claude Code CLI is also claude.exe: matching on
        # name alone warns about Desktop being open when only the CLI is
        # running, which is the normal state when this is run from a terminal.
        $desktopRunning = @(
            Get-Process -Name 'claude', 'Claude' -ErrorAction SilentlyContinue | Where-Object {
                $p = try { $_.Path } catch { $null }
                $p -notlike '*claude-code*'
            }
        ).Count -gt 0
        if ($desktopRunning) {
            Write-Warn2 "Claude Desktop is running. It can rewrite this file from its own state,"
            Write-Warn2 "reverting what is written below."
            Add-Problem "Quit Claude Desktop fully (tray icon too), re-run with -Only claude, then start it again."
        }

        # Desktop has no ${CLAUDE_PLUGIN_ROOT} outside the plugin runtime, so the
        # config gets an absolute path. That is safe to write here precisely
        # because the installer knows where the plugin actually is.
        # An absolute interpreter, not the word "python".
        #
        # This file is ours to write and Desktop reads it verbatim, so there is
        # no reason to leave it depending on PATH - which is exactly the thing
        # that changes when the user installs another Python next month, or that
        # points at Python 2 on an older machine, where launch_server.py would
        # not even parse.
        #
        # A system interpreter rather than the virtualenv's: the launcher
        # re-execs into the venv itself, and pinning the venv here would turn
        # "the venv was rebuilt" into a broken Desktop config instead of a
        # recoverable one.
        $desktopPython = 'python'
        $resolved = Get-Command python -ErrorAction SilentlyContinue
        if ($resolved) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $null = & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>&1
                if ($LASTEXITCODE -eq 0) { $desktopPython = $resolved.Source }
            } catch { }
            $ErrorActionPreference = $prevEAP
        }
        if ($desktopPython -eq 'python') {
            # PATH python is missing or too old. Ask the launcher's own search,
            # which knows how to reach a `py -3.12` that PATH never mentions.
            $best = Invoke-Native { & python $Launcher --self-test 2>$null | Select-Object -Last 1 }
            if ($LASTEXITCODE -eq 0 -and $best -and (Test-Path "$best")) {
                $desktopPython = "$best".Trim()
            } else {
                Write-Warn2 "No usable Python found for Claude Desktop; leaving the entry as 'python'."
            }
        }
        Write-Info "Desktop will launch: $desktopPython"

        $mcpEntry = [ordered]@{
            command = $desktopPython
            args    = @($Launcher)
            env     = [ordered]@{ REAPER_MCP_PLUGIN_ROOT = $PluginRoot }
        }

        $configPaths = @( Join-Path $env:APPDATA 'Claude\claude_desktop_config.json' )
        $msix = Join-Path $env:LOCALAPPDATA 'Packages'
        if (Test-Path $msix) {
            Get-ChildItem $msix -Filter 'Claude_*' -Directory -ErrorAction SilentlyContinue |
                ForEach-Object {
                    $configPaths += Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude_desktop_config.json'
                }
        }

        $found = $false
        foreach ($cfg in $configPaths) {
            if (-not (Test-Path $cfg)) { continue }
            $found = $true
            try {
                $raw = Get-Content $cfg -Raw
                if ([string]::IsNullOrWhiteSpace($raw)) { $raw = '{}' }
                $json = $raw | ConvertFrom-Json
                if (-not $json.PSObject.Properties['mcpServers']) {
                    $json | Add-Member -MemberType NoteProperty -Name 'mcpServers' -Value ([PSCustomObject]@{})
                }
                $json.mcpServers | Add-Member -MemberType NoteProperty -Name 'reaper' -Value $mcpEntry -Force
                Copy-Item -Force $cfg "$cfg.bak"   # this file holds the user's other servers
                [System.IO.File]::WriteAllText($cfg, ($json | ConvertTo-Json -Depth 12),
                                               (New-Object System.Text.UTF8Encoding $false))
                # Both a plain and an MSIX install can be present, each with its
                # own config. Naming the file makes two identical-looking lines
                # legible instead of looking like a bug.
                Write-Ok "Claude Desktop updated: $cfg (backup alongside it)"
            } catch {
                Write-Err "Could not update $cfg : $_"
                Add-Problem "Edit $cfg by hand, or re-run after closing Claude Desktop."
            }
        }
        if (-not $found) { Write-Info "No Claude Desktop install found; skipped." }
    }

    # -----------------------------------------------------------------------
    # Older versions installed copies under a skills directory. Those still load
    # if left in place, so every tool would appear twice and edits here would
    # have no visible effect - a genuinely baffling symptom. Report, never
    # delete: the folders are outside this repository and may not all be ours.
    # -----------------------------------------------------------------------
    # Superseded: earlier versions installed these, and this plugin now contains
    # everything they did. reaper-mcp is itself a plugin, so its ~58 tools load a
    # second time alongside ours.
    $superseded = @()
    foreach ($d in @('reaper-mcp', 'reaper-ai-engineer-skill')) {
        $p = Join-Path $env:USERPROFILE ".claude\skills\$d"
        if (Test-Path $p) { $superseded += $p }
    }

    # Overlapping but NOT ours - a separate REAPER skill that carries files this
    # repository does not have. Worth naming, never worth deleting on a guess.
    $independent = Join-Path $env:USERPROFILE '.claude\skills\audio-engineer-reaper'

    if ($superseded.Count -gt 0 -or (Test-Path $independent)) {
        Write-Step "Other REAPER skills already installed"
        foreach ($p in $superseded) { Write-Warn2 "superseded: $p" }
        if ($superseded.Count -gt 0) {
            Write-Info "This plugin replaces those. Left in place they load in parallel."
            Add-Problem "Delete the superseded copies once the plugin is confirmed working."
        }
        if (Test-Path $independent) {
            Write-Info "also present: $independent"
            Write-Info "That one is a separate skill with content not in this repository."
            Write-Info "It overlaps with reaper-audio-engineer. Yours to keep or remove."
        }
    }
}

# ---------------------------------------------------------------------------
# 5. Health check + summary
# ---------------------------------------------------------------------------
$doctor = Join-Path $PSScriptRoot 'doctor.ps1'
if (Test-Path $doctor) {
    if ($ReaperResourcePath) { & $doctor -ReaperResourcePath $ReaperResourcePath }
    else { & $doctor }
}

# Hand the problems to the caller rather than reporting them separately.
if ($ProblemsOut) {
    try {
        Set-Content -Path $ProblemsOut -Value @($script:Problems) -Encoding UTF8
    } catch {
        # If this cannot be written the wrapper simply reports fewer problems;
        # they were all printed as they happened, so nothing is actually lost.
    }
    return
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
if ($script:Problems.Count -eq 0) {
    Write-Host "  DONE - nothing left to do by hand" -ForegroundColor Green
    Write-Host "===============================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "  1. Restart REAPER (it needs to reload reaper.ini)."
    Write-Host "  2. Restart Claude."
    Write-Host "  3. Ask: `"Check the current REAPER project info`""
} else {
    Write-Host "  DONE - with $($script:Problems.Count) thing(s) to fix" -ForegroundColor Yellow
    Write-Host "===============================================" -ForegroundColor Cyan
    Write-Host ""
    $i = 1
    foreach ($p in $script:Problems) { Write-Host "  $i. $p" -ForegroundColor Yellow; $i++ }
    Write-Host ""
    Write-Host "  Re-running this installer after fixing them is safe."
}
Write-Host ""
