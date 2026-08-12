<#
.SYNOPSIS
  Install everything REAPER for Claude needs, in one pass.

.DESCRIPTION
  The whole setup, in the order the dependencies actually require:

    1. Snapshot every file this can change, so [2] Revert Everything works
    2. Make sure winget is available
    3. Install Python, Git, REAPER, Claude Desktop and Claude Code
    4. Configure the plugin: dependencies, REAPER bridge, distant API, Claude
    5. Health check

  On applications and your data
  -----------------------------
  REAPER and Claude are installed only when they are ABSENT. If either is
  already on the machine it is left completely alone - not upgraded, not
  repaired, not reinstalled. Their installers are well behaved, but a reinstall
  is a needless risk to a REAPER resource folder holding years of preferences,
  FX chains and templates, and to Claude's local history. Nothing here is worth
  that.

  Python and Git are ordinary developer tooling with no user state to lose, so
  those are installed or upgraded normally.

  Every package comes from the winget community source, which uses each
  vendor's real installer - reaper.fm, claude.ai, git-scm - rather than a
  Microsoft Store package.

.PARAMETER SkipApps
  Configure the plugin only; install no applications.

.PARAMETER Force
  Continue past the "REAPER is running" warning.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File setup-all.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipApps,
    [switch]$Force,
    [string]$ReaperResourcePath
)

$ErrorActionPreference = 'Stop'

function Write-Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  .      $m" -ForegroundColor Gray }
function Write-Warn2($m){ Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

$script:Problems = @()
function Add-Problem($m) { $script:Problems += $m }

$Here       = $PSScriptRoot
$PluginRoot = Split-Path -Parent $Here

# Applications, in dependency order. `Safe` means "leave it alone if present",
# which is the whole of the promise not to touch user data.
$Apps = @(
    [pscustomobject]@{ Id = 'Python.Python.3.12'; Name = 'Python 3.12'; Safe = $false
                       Custom = 'PrependPath=1 InstallAllUsers=0 Include_test=0' }
    [pscustomobject]@{ Id = 'Git.Git';            Name = 'Git';          Safe = $false; Custom = $null }
    [pscustomobject]@{ Id = 'Cockos.REAPER';      Name = 'REAPER';       Safe = $true;  Custom = $null }
    [pscustomobject]@{ Id = 'Anthropic.Claude';   Name = 'Claude Desktop'; Safe = $true; Custom = $null }
    [pscustomobject]@{ Id = 'Anthropic.ClaudeCode'; Name = 'Claude Code'; Safe = $true; Custom = $null }
)

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  REAPER for Claude - install everything" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Info "Plugin: $PluginRoot"

# ---------------------------------------------------------------------------
# 0. REAPER must be closed before anything writes to reaper.ini, because REAPER
#    rewrites that file when it exits and would discard everything.
# ---------------------------------------------------------------------------
if (@(Get-Process -Name 'reaper' -ErrorAction SilentlyContinue).Count -gt 0 -and -not $Force) {
    Write-Warn2 "REAPER is running."
    Write-Info  "It rewrites reaper.ini when it exits, which would silently discard"
    Write-Info  "the connection settings written below."
    Write-Host  ""
    Write-Host  "  Close REAPER, then press Enter to continue (or Ctrl+C to stop)." -ForegroundColor Yellow
    [void](Read-Host)
}

# ---------------------------------------------------------------------------
# 1. Snapshot. First, unconditionally, before a single byte changes.
# ---------------------------------------------------------------------------
Write-Step "Backing up current configuration"
try {
    # Hashtable splatting, not an array. Splatting an array passes its elements
    # POSITIONALLY, so @('-Backup') arrives as a value rather than binding the
    # switch, and the call fails with "a positional parameter cannot be found".
    $snapArgs = @{ Backup = $true }
    if ($ReaperResourcePath) { $snapArgs['ReaperResourcePath'] = $ReaperResourcePath }
    & (Join-Path $Here 'snapshot.ps1') @snapArgs | Out-Host
    Write-Info "Undo this whole setup later with [2] Revert Everything."
} catch {
    Write-Err "Could not take a snapshot: $_"
    Add-Problem "No backup was taken, so [2] Revert Everything will not be able to undo this run."
}

# ---------------------------------------------------------------------------
# 2 & 3. Applications
# ---------------------------------------------------------------------------
if ($SkipApps) {
    Write-Step "Applications"
    Write-Warn2 "Skipped (-SkipApps)."
} else {
    Write-Step "Package manager"
    $haveWinget = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try { $null = & winget --version 2>&1; $haveWinget = ($LASTEXITCODE -eq 0) } catch { }
    }

    if ($haveWinget) {
        Write-Ok "winget $(& winget --version)"
    } else {
        Write-Info "winget is missing; attempting a repair..."
        try {
            & (Join-Path $Here 'repair-winget.ps1') | Out-Host
        } catch {
            Write-Warn2 "winget repair did not succeed."
        }
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            try { $null = & winget --version 2>&1; $haveWinget = ($LASTEXITCODE -eq 0) } catch { }
        }
    }

    Write-Step "Applications"
    if (-not $haveWinget) {
        Write-Warn2 "No winget, so applications cannot be installed automatically."
        # Python is the one thing the plugin genuinely cannot run without, and
        # install-python.ps1 can fetch it straight from python.org.
        Write-Info "Falling back to a direct Python download..."
        try {
            & (Join-Path $Here 'install-python.ps1') -Version '3.12.10' | Out-Host
        } catch {
            Write-Err "Python could not be installed: $_"
            Add-Problem "Install Python from https://www.python.org/downloads/ with 'Add python.exe to PATH' ticked."
        }
        Add-Problem "Install REAPER (reaper.fm) and Claude (claude.ai/download) by hand - winget was unavailable."
    } else {
        foreach ($app in $Apps) {
            $installed = (& winget list --id $app.Id --exact --source winget 2>&1 | Out-String) -match [regex]::Escape($app.Id)

            if ($installed -and $app.Safe) {
                Write-Ok "$($app.Name) is already installed - left untouched."
                continue
            }
            if ($installed) {
                Write-Info "$($app.Name) is already installed; checking for an upgrade."
            } else {
                Write-Info "Installing $($app.Name)..."
            }

            # -e for an exact ID match: "REAPER" alone also matches an unrelated
            # ScytheLabs.Reaper, and Python.Python.3 can resolve to Python 3.0.
            # --source winget for the vendor's own installer rather than a Store
            # package.
            $wargs = @(
                'install', '-e', '--id', $app.Id, '--source', 'winget',
                '--accept-package-agreements', '--accept-source-agreements'
            )
            if ($app.Custom) { $wargs += @('--custom', $app.Custom) }

            & winget @wargs
            $code = $LASTEXITCODE

            # 0x8A15002B / -1978335189: "no applicable upgrade found", i.e. it is
            # already current. Not a failure.
            if ($code -eq 0 -or $code -eq -1978335189) {
                Write-Ok "$($app.Name) ready."
            } else {
                Write-Err "$($app.Name) install returned $code."
                Add-Problem "Install $($app.Name) by hand, then re-run."
            }
        }
    }

    # A process reads PATH once at startup, so anything installed above is
    # invisible to this one. Prepend the known locations so the steps below can
    # actually call python.
    foreach ($p in @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\Scripts'),
        (Join-Path $env:ProgramFiles 'Python312'),
        (Join-Path $env:ProgramFiles 'Git\cmd')
    )) {
        if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) { $env:PATH = "$p;$env:PATH" }
    }
}

# ---------------------------------------------------------------------------
# 4. The plugin itself. install.ps1 owns this and is unchanged by the wrapper.
# ---------------------------------------------------------------------------
Write-Step "Configuring the plugin"
# Hashtable, for the same reason as the snapshot call above: an array splat is
# positional, so '-Force' would bind to install.ps1's first parameter, -Only,
# and fail its ValidateSet.
$installArgs = @{}
if ($ReaperResourcePath) { $installArgs['ReaperResourcePath'] = $ReaperResourcePath }
if ($Force)              { $installArgs['Force'] = $true }

try {
    & (Join-Path $Here 'install.ps1') @installArgs | Out-Host
} catch {
    Write-Err "Plugin configuration failed: $_"
    Add-Problem "Re-run this option once the error above is resolved."
}

# ---------------------------------------------------------------------------
Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
if ($script:Problems.Count -eq 0) {
    Write-Host "  DONE" -ForegroundColor Green
    Write-Host "===============================================" -ForegroundColor Cyan
} else {
    Write-Host "  DONE - with $($script:Problems.Count) thing(s) to fix" -ForegroundColor Yellow
    Write-Host "===============================================" -ForegroundColor Cyan
    Write-Host ""
    $i = 1
    foreach ($p in $script:Problems) { Write-Host "  $i. $p" -ForegroundColor Yellow; $i++ }
}
Write-Host ""
