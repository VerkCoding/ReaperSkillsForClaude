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
    # Set by RunThisToStart.bat, which has already explained what this does and
    # had the user agree. Suppresses the second "press Enter" that would
    # otherwise ask the same question again.
    [switch]$Confirmed,
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

# Which applications existed before this run. Populated during the install loop
# and read by the first-run steps.
$script:WasPresent = @{}

$Here       = $PSScriptRoot
$PluginRoot = Split-Path -Parent $Here

# Dot-sourced so its helpers share these Write-* functions and $script: scope.
. (Join-Path $Here 'lib-apps.ps1')

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
# 0. Get REAPER and Claude closed first.
#
#    Both of them hold their configuration in memory and write it back when they
#    exit, so anything written underneath a running instance is discarded -
#    reaper.ini for REAPER, claude_desktop_config.json for Claude. Asking first
#    and closing second is the only way those writes survive.
#
#    Nothing is force-killed. The user is asked to save, and a close request is
#    the same one the X button sends, so REAPER's save prompt still appears.
# ---------------------------------------------------------------------------
Write-Step "Closing REAPER and Claude"

# No prompt here when the menu already asked. Two confirmations for one decision
# is one too many: the user has already read what this does and said yes, and
# being asked again reads as though something changed.
if (-not $Confirmed) {
    Write-Host ""
    Write-Host "  Save any open work in REAPER and Claude now." -ForegroundColor Yellow
    Write-Host "  Both are closed while this runs." -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Press Enter to continue, or Ctrl+C to stop." -ForegroundColor Yellow
    [void](Read-Host)
}

$reaperClosed = Request-AppClosed -Kind reaper -Label 'REAPER'
$claudeClosed = Request-AppClosed -Kind claude -Label 'Claude'

if (-not $reaperClosed) {
    Add-Problem "REAPER stayed open, so its connection settings may not have been saved. Close it and re-run [1]."
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
            & (Join-Path $Here 'repair-winget.ps1') -Embedded | Out-Host
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

            # Remembered so the first-run steps below only fire for applications
            # this run actually introduced. Opening and closing an app somebody
            # already uses would be presumptuous, and pointless - it has a
            # config and a session already.
            $script:WasPresent[$app.Id] = $installed

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
# 3b. First run.
#
# A freshly installed REAPER has no reaper.ini - the resource folder appears
# only after it has run once - and a fresh Claude has no session. The rest of
# the setup needs both, so rather than ending with "now go launch REAPER
# yourself", do it here and wait.
#
# Only for applications this run installed. Anything that was already here has a
# config and a session, and opening it uninvited would be presumptuous.
# ---------------------------------------------------------------------------
if (-not $SkipApps) {
    $reaperWasNew = ($script:WasPresent.ContainsKey('Cockos.REAPER') -and -not $script:WasPresent['Cockos.REAPER'])
    $claudeWasNew = ($script:WasPresent.ContainsKey('Anthropic.Claude') -and -not $script:WasPresent['Anthropic.Claude'])

    if ($reaperWasNew -or $claudeWasNew) {
        Write-Step "First run"

        if ($reaperWasNew) {
            $resource = if ($ReaperResourcePath) { $ReaperResourcePath } else { Join-Path $env:APPDATA 'REAPER' }
            if (-not (Invoke-ReaperFirstRun -ReaperResourcePath $resource)) {
                Add-Problem "REAPER has not created its configuration yet. Launch REAPER once, close it, then re-run [1]."
            }
        }

        if ($claudeWasNew) {
            if (-not (Invoke-ClaudeFirstRun)) {
                Add-Problem "Claude is NOT signed in. The plugin is installed, but Claude cannot use it until you sign in and restart it."
            }
        }
    }
}

# ---------------------------------------------------------------------------
# 4. The plugin itself. install.ps1 owns this and is unchanged by the wrapper.
#
# One last check before it runs. Everything below writes to files that REAPER
# and Claude rewrite from memory on exit, and by now the user has been through
# several prompts, an application install and possibly a sign-in - any of which
# can have left one of them running again. This is the last moment where that is
# still cheap to fix.
# ---------------------------------------------------------------------------
[void](Confirm-AppsClosed -Because "Checking both are still closed before writing any configuration.")

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
