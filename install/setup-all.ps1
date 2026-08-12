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

# ---------------------------------------------------------------------------
# Whether installing Python may touch PATH.
#
# PrependPath=1 makes our 3.12 the machine's `python`. On a system that already
# has one - 3.14, or an old 2.7 - that silently changes what every other project
# on the machine resolves, which is not ours to do for the sake of an audio
# plugin.
#
# It turns out we do not need it. Three different things want a Python here, and
# none of them wants the default one:
#
#   the MCP server      runs from the virtualenv, which any 3.10+ can build
#   REAPER's ReaScripts run under whatever reaper.ini's pythonlibpath64 names
#   the configure step  needs <=3.12, reached with `py -3.12`, which ignores
#                       PATH order entirely
#
# The only PATH dependency left is that SOME python has to exist to launch
# scripts/launch_server.py, which then re-execs into the virtualenv. That needs
# 3.6 or newer merely to parse - the file uses f-strings - so an existing 3.x is
# perfectly good and is left exactly where it is.
#
# The exception is a machine whose `python` is Python 2, or has none at all.
# There, launch_server.py will not even parse, so PATH does have to change.
# ---------------------------------------------------------------------------
$pathPythonUsable = $false
if (Get-Command python -ErrorAction SilentlyContinue) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>&1
        $pathPythonUsable = ($LASTEXITCODE -eq 0)
    } catch { }
    $ErrorActionPreference = $prevEAP
}

$pythonCustom = if ($pathPythonUsable) {
    Write-Info "A usable Python is already on PATH - leaving it as the default."
    'InstallAllUsers=0 Include_test=0'
} else {
    Write-Info "No usable Python on PATH, so the new one will be added to it."
    'PrependPath=1 InstallAllUsers=0 Include_test=0'
}

# Applications, in dependency order. `Safe` means "leave it alone if present",
# which is the whole of the promise not to touch user data.
$Apps = @(
    [pscustomobject]@{ Id = 'Python.Python.3.12'; Name = 'Python 3.12'; Safe = $false
                       Custom = $pythonCustom }
    [pscustomobject]@{ Id = 'Git.Git';            Name = 'Git';          Safe = $false; Custom = $null }
    [pscustomobject]@{ Id = 'Cockos.REAPER';      Name = 'REAPER';       Safe = $true;  Custom = $null }
    [pscustomobject]@{ Id = 'Anthropic.Claude';   Name = 'Claude Desktop'; Safe = $true; Custom = $null }
    [pscustomobject]@{ Id = 'Anthropic.ClaudeCode'; Name = 'Claude Code'; Safe = $true; Custom = $null }
)

# ---------------------------------------------------------------------------
# Record the whole run to a file.
#
# Everything here is console output, and the interesting part - why pip or
# winget gave up - scrolls past several minutes before the end. When something
# does not work, "what did it say?" has no answer, and the only evidence left is
# the symptom hours later. A transcript costs nothing and turns that into a file
# anyone can read or send on.
# ---------------------------------------------------------------------------
$LogDir = Join-Path $env:USERPROFILE '.reaper-for-claude\logs'
$LogFile = Join-Path $LogDir ("setup-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
$transcribing = $false
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Start-Transcript -Path $LogFile -Force | Out-Null
    $transcribing = $true
    # Keep the last handful; these are small, but unbounded is its own mess.
    Get-ChildItem $LogDir -Filter 'setup-*.log' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -Skip 10 |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
} catch {
    # Transcription is a nicety, never a reason not to install.
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  REAPER for Claude - install everything" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan
Write-Info "Plugin: $PluginRoot"
if ($transcribing) { Write-Info "Log:    $LogFile" }

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
    # Out-Null alone, not Out-Host. snapshot.ps1 reports progress with Write-Host,
    # which bypasses the pipeline and is displayed either way, and returns the
    # snapshot directory on the success stream for programmatic callers. Piping
    # through Out-Host renders that path too, so the transcript showed the
    # directory twice - once in the [ok] line and once bare.
    & (Join-Path $Here 'snapshot.ps1') @snapArgs | Out-Null
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
        # winget is the backbone: REAPER, Claude, Git and Python all come
        # through it, so getting it installed is worth doing properly rather
        # than working around.
        #
        # repair-winget.ps1 now downloads it from Microsoft's release directly,
        # which needs nothing but HTTPS - no PowerShell Gallery, no NuGet
        # provider, no Store - and is the route that works on the machines that
        # lack it. The Gallery bootstrap is still there behind it.
        Write-Warn2 "winget is not available - installing it."
        try {
            & (Join-Path $Here 'repair-winget.ps1') -Embedded | Out-Host
        } catch {
            Write-Warn2 "winget install did not succeed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        }

        # Re-check by path as well as by name. Add-AppxPackage puts the shim in
        # WindowsApps, which is already on PATH, but this process cached its
        # command lookups at startup.
        $shim = Join-Path $env:LOCALAPPDATA 'Microsoft\WindowsApps\winget.exe'
        if (Test-Path $shim) { $env:PATH = "$(Split-Path $shim);$env:PATH" }
        Get-Command winget -ErrorAction SilentlyContinue | Out-Null
        if (Get-Command winget -ErrorAction SilentlyContinue) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try { $null = & winget --version 2>&1; $haveWinget = ($LASTEXITCODE -eq 0) } catch { }
            $ErrorActionPreference = $prevEAP
        }
        if ($haveWinget) { Write-Ok "winget is now available: $(& winget --version)" }
    }

    Write-Step "Applications"
    if (-not $haveWinget) {
        # Python from python.org, which needs nothing but a network connection.
        # Everything else in the plugin depends on it; REAPER and Claude do not
        # have an equally stable direct URL, so those are named for the user
        # rather than guessed at.
        Write-Info "Installing Python directly from python.org..."
        try {
            & (Join-Path $Here 'install-python.ps1') -Version '3.12.10' | Out-Host
        } catch {
            Write-Err "Python could not be installed: $_"
            Add-Problem "Install Python from https://www.python.org/downloads/ with 'Add python.exe to PATH' ticked."
        }
        Write-Host ""
        Write-Info "REAPER and Claude cannot be fetched without winget. If you do"
        Write-Info "not already have them:"
        Write-Info "    REAPER  https://www.reaper.fm/download.php"
        Write-Info "    Claude  https://claude.ai/download"
        Write-Info "Install them, then run [1] again - everything else will be"
        Write-Info "left as it is and only the missing pieces filled in."
        Add-Problem "Install REAPER and Claude by hand (winget was unavailable), then re-run [1]."
    } else {
        foreach ($app in $Apps) {
            # Each application is isolated.
            #
            # This loop previously ran bare under $ErrorActionPreference =
            # 'Stop', so anything winget did that PowerShell treats as a
            # terminating error - and merging a native command's stderr is
            # enough - aborted the WHOLE run at that application. REAPER and
            # Claude sit at the end of the list, so a hiccup on Python or Git
            # took them with it, along with the plugin configuration below, and
            # the output stopped mid-step with no line naming the cause.
            #
            # One application failing is now one application failing.
            try {
                $prevEAP = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    $listing = & winget list --id $app.Id --exact --source winget 2>&1 | Out-String
                } finally {
                    $ErrorActionPreference = $prevEAP
                }
                $installed = $listing -match [regex]::Escape($app.Id)

                # Remembered so the first-run steps below only fire for
                # applications this run actually introduced. Opening and closing
                # an app somebody already uses would be presumptuous, and
                # pointless - it has a config and a session already.
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

                # -e for an exact ID match: "REAPER" alone also matches an
                # unrelated ScytheLabs.Reaper, and Python.Python.3 can resolve to
                # Python 3.0. --source winget for the vendor's own installer
                # rather than a Store package.
                $wargs = @(
                    'install', '-e', '--id', $app.Id, '--source', 'winget',
                    '--accept-package-agreements', '--accept-source-agreements'
                )
                if ($app.Custom) { $wargs += @('--custom', $app.Custom) }

                # Retried, because winget's download step fails on a flaky
                # connection with things like "InternetOpenUrl() failed,
                # 0x80072f78" - a transient network error, not a package that
                # cannot be installed. One retry turns most of those into a
                # success rather than a line in the summary.
                $prevEAP = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    for ($attempt = 1; $attempt -le 2; $attempt++) {
                        & winget @wargs
                        $code = $LASTEXITCODE
                        if ($code -eq 0 -or $code -eq -1978335189) { break }
                        if ($attempt -eq 1) {
                            Write-Warn2 ("  {0} failed (exit {1}); retrying once..." -f $app.Name, $code)
                            Start-Sleep -Seconds 5
                        }
                    }
                } finally {
                    $ErrorActionPreference = $prevEAP
                }

                # 0x8A15002B / -1978335189: "no applicable upgrade found", i.e.
                # it is already current. Not a failure.
                if ($code -eq 0 -or $code -eq -1978335189) {
                    Write-Ok "$($app.Name) ready."
                } else {
                    Write-Err "$($app.Name): winget exited $code."
                    Add-Problem "$($app.Name) did not install (winget exit $code). Install it by hand, then re-run [1]."
                }
            } catch {
                Write-Err "$($app.Name): $($_.Exception.Message)"
                Add-Problem "$($app.Name) could not be installed - see the error above. The rest of the setup continued."
            }
        }
    }

    # A process reads PATH once at startup, so anything installed above is
    # invisible to this one. Prepend the known locations so the steps below can
    # actually call python.
    $newPaths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\Scripts'),
        (Join-Path $env:ProgramFiles 'Python312'),
        (Join-Path $env:ProgramFiles 'Git\cmd'),
        # Where winget puts command-line aliases for most packages.
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
    )

    # Claude Code specifically.
    #
    # It is a portable package, so winget does not alias it into Links - it
    # extracts it under WinGet\Packages\<Id>_<source>\ and puts THAT on PATH.
    # This matters more than it looks: without it the plugin is never registered
    # with Claude Code at all. The installer says "Path environment variable
    # modified; restart your shell", this process cannot, so `claude` is not
    # found and the step quietly downgrades to printing instructions - which is
    # exactly what happened on the last clean run.
    #
    # Matched by wildcard because the folder carries a source hash
    # (Anthropic.ClaudeCode_Microsoft.Winget.Source_8wekyb3d8bbwe) that is not
    # ours to predict.
    $pkgRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $pkgRoot) {
        Get-ChildItem $pkgRoot -Directory -Filter 'Anthropic.ClaudeCode*' -ErrorAction SilentlyContinue |
            ForEach-Object { $newPaths += $_.FullName }
    }

    foreach ($p in $newPaths) {
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

# Collect its problems into this run's summary instead of letting it print a
# second, possibly contradictory, banner of its own.
$problemsFile = Join-Path $env:TEMP ("rfc-problems-" + [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".txt")
$installArgs['ProblemsOut'] = $problemsFile

try {
    # Same NativeCommandError hazard as inside install.ps1: pip and the claude
    # CLI both write to stderr in normal operation, and under EAP 'Stop' that
    # would abort the configuration this call exists to perform.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & (Join-Path $Here 'install.ps1') @installArgs | Out-Host
    } finally {
        $ErrorActionPreference = $prevEAP
    }
} catch {
    Write-Err "Plugin configuration failed: $_"
    Add-Problem "Re-run this option once the error above is resolved."
}

if (Test-Path $problemsFile) {
    Get-Content $problemsFile -ErrorAction SilentlyContinue |
        Where-Object { $_.Trim() } | ForEach-Object { Add-Problem $_ }
    Remove-Item $problemsFile -Force -ErrorAction SilentlyContinue
}

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# The one check that decides whether any of this worked.
#
# Everything above can report success while the server is unable to start - a
# pip failure scrolls past, the plugin registers fine, the health check runs,
# and the user is told to restart Claude. They then find "server disconnected"
# with no idea which step let them down. Ask the launcher directly and say so in
# terms nobody can scroll past.
# ---------------------------------------------------------------------------
$serverOk = $false
if (Get-Command python -ErrorAction SilentlyContinue) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & python (Join-Path $PluginRoot 'scripts\launch_server.py') --self-test 2>&1
        $serverOk = ($LASTEXITCODE -eq 0)
    } catch { }
    $ErrorActionPreference = $prevEAP
}

if (-not $serverOk) {
    Write-Host ""
    Write-Host "===============================================" -ForegroundColor Red
    Write-Host "  THE REAPER SERVER CANNOT START" -ForegroundColor Red
    Write-Host "===============================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "  Everything else may have installed, but Claude will show" -ForegroundColor Yellow
    Write-Host "  'reaper: server disconnected' until this is fixed." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  The dependencies are missing. Build them with:" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "      python `"$PluginRoot\scripts\bootstrap.py`"" -ForegroundColor White
    Write-Host ""
    Write-Host "  Whatever went wrong is in the log, above the summary:" -ForegroundColor Gray
    if ($transcribing) {
        Write-Host "      $LogFile" -ForegroundColor White
    }
    Add-Problem "The REAPER server cannot start - run scripts\bootstrap.py and read its output."
}

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

if ($transcribing) {
    Write-Host "  Full log of this run:" -ForegroundColor Gray
    Write-Host "    $LogFile" -ForegroundColor Gray
    Write-Host ""
    try { Stop-Transcript | Out-Null } catch { }
}
