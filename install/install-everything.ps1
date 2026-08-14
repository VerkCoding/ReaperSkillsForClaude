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

  downloadCache
  -------------
  Every application install goes through it. Already there means install from
  disk and download nothing; not there means fetch it INTO the cache with
  `winget download` and install from disk anyway. So the first run downloads
  once and the runs after it download nothing.

  That last part is the whole point. `winget install` fetches into its own temp
  directory and deletes it afterwards, so a machine that gets wiped between runs
  - a Sandbox, a test VM, a lab image - re-downloaded Git, Python, REAPER and
  Claude every single time while downloadCache sat there empty. Repeating that
  often enough is how an address gets rate-limited and then blocked.

  It is still only ever an optimisation: an absent, empty or unwritable cache
  falls straight back to `winget install` and behaves as it always did. [3]
  Prepare Offline Files does the same fetching without installing, for setting
  up a machine that cannot reach the vendors at all. See lib-download-cache.ps1.

.PARAMETER SkipApps
  Configure the plugin only; install no applications.

.PARAMETER Force
  Continue past the "REAPER is running" warning.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-everything.ps1
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

# How this talks, and the log it writes. Same function names the rest of
# this file already calls - see lib-console.ps1.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

$script:Problems = @()
function Add-Problem($m) { $script:Problems += $m }

# Which applications existed before this run. Populated during the install loop
# and read by the first-run steps.
$script:WasPresent = @{}

$Here       = $PSScriptRoot
$PluginRoot = Split-Path -Parent $Here

# Dot-sourced so their helpers share these Write-* functions and $script: scope.
. (Join-Path $Here 'lib-app-control.ps1')
. (Join-Path $Here 'lib-download-cache.ps1')

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

# Applications, in dependency order. Defined in lib-app-control.ps1 so
# fill-download-cache.ps1 works from the same list.
$Apps = Get-AppList -PythonCustom $pythonCustom

# ---------------------------------------------------------------------------
# Record the whole run to a file.
#
# Everything here is console output, and the interesting part - why pip or
# winget gave up - scrolls past several minutes before the end. When something
# does not work, "what did it say?" has no answer, and the only evidence left is
# the symptom hours later. A transcript costs nothing and turns that into a file
# anyone can read or send on.
# ---------------------------------------------------------------------------
#
# Two files, and they are not the same file. The transcript is the raw capture -
# winget's own output, pip's, every error verbatim. The step log written by
# Start-RunLog below is the structured trace of what this script decided and
# when. The first answers "what happened", the second answers "what did WE do",
# and the second is the one worth reading first.
#
# They are named apart deliberately. Both used to be "setup-<timestamp>.log",
# which on a run fast enough to land in the same second is one filename - the
# step logger would have appended into the middle of the transcript, and the
# transcript's own pruning would have deleted step logs.
$LogDir = Join-Path $env:USERPROFILE '.reaper-for-claude\logs'
$LogFile = Join-Path $LogDir ("transcript-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
$transcribing = $false
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Start-Transcript -Path $LogFile -Force | Out-Null
    $transcribing = $true
    # Keep the last handful; these are small, but unbounded is its own mess.
    Get-ChildItem $LogDir -Filter 'transcript-*.log' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -Skip 10 |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
} catch {
    # Transcription is a nicety, never a reason not to install.
}

# A click in the console window pauses everything until a key is pressed, and a
# stalled window looks exactly like a busy one. Off before the first line of
# output, so there is no window of opportunity. See Disable-ConsoleQuickEdit.
[void](Disable-ConsoleQuickEdit)

$stepLog = Start-RunLog 'setup'
# Published so configure-plugin.ps1, install-winget.ps1 and install-python.ps1 -
# each a separate PowerShell scope - append to this run's log instead of opening
# their own or, as they did, none at all.
if ($stepLog) { $env:RFC_LOG_PATH = $stepLog }
Write-Banner "REAPER for Claude - install everything"
Write-Info "plugin      $PluginRoot"
if ($stepLog)      { Write-Info "log         $stepLog" }
if ($transcribing) { Write-Info "transcript  $LogFile" }

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

# Claude's result is deliberately not kept, unlike REAPER's below. A REAPER that
# stays open means one step will be skipped, which has to be reported at the
# end. A Claude that stays open does not: Request-AppClosed has already
# explained the consequence itself, Confirm-AppsClosed checks again immediately
# before anything is written, and configure-plugin.ps1 handles a running Claude
# on its own. There is nothing left for this value to decide.
[void](Request-AppClosed -Kind claude -Label 'Claude')

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
    # Out-Null alone, not Out-Host. backup-restore.ps1 reports progress with Write-Host,
    # which bypasses the pipeline and is displayed either way, and returns the
    # snapshot directory on the success stream for programmatic callers. Piping
    # through Out-Host renders that path too, so the transcript showed the
    # directory twice - once in the [ok] line and once bare.
    & (Join-Path $Here 'backup-restore.ps1') @snapArgs | Out-Null
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
        # install-winget.ps1 deploys the packages directly, which needs nothing
        # but HTTPS - no PowerShell Gallery, no NuGet provider, no Store - and
        # is the route that works on the machines that lack winget. It reads
        # them from downloadCache when they are there and downloads them when
        # they are not; see that file for which source it asks first. The
        # Gallery bootstrap is still there behind it.
        Write-Warn2 "winget is not available - installing it."
        try {
            & (Join-Path $Here 'install-winget.ps1') -Embedded | Out-Host
        } catch {
            Write-Warn2 "winget install did not succeed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        }

        # Re-check by path as well as by name. Add-AppxPackage puts the shim in
        # WindowsApps, which is already on PATH, but this process cached its
        # command lookups at startup.
        Update-PathFromRegistry
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

    # downloadCache, if it has been filled, comes before every download below.
    # See lib-download-cache.ps1 for the whole argument; the short version is that a
    # machine wiped between runs pays the same 300 MB every time, and repeating
    # that often enough is what gets an address blocked.
    if (Get-CacheDir) {
        Write-Info "downloadCache is here - anything in it is installed from disk."
    }
    if (-not $haveWinget) {
        Write-Warn2 "winget is unavailable, so only downloadCache and python.org are left."
    }

    # Applications that ended up missing with no way to fetch them. Reported
    # once, at the end, rather than five times in the middle.
    $manual = @()

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
            # ---- is it already here? -------------------------------------
            #
            # winget list is the better answer where there is a winget. Where
            # there is not, the question still has to be answered, because a
            # cached installer can now put REAPER on a machine that never had a
            # package manager - and `Safe` has to keep meaning what it says.
            if ($haveWinget) {
                $prevEAP = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    $listing = & winget list --id $app.Id --exact --source winget 2>&1 | Out-String
                } finally {
                    $ErrorActionPreference = $prevEAP
                }
                $installed = $listing -match [regex]::Escape($app.Id)
            } else {
                $installed = Test-AppPresent -Id $app.Id
            }

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
                if (-not $haveWinget) {
                    # Nothing to upgrade with. A cached installer is no help
                    # here either: it is a fixed version from whenever the cache
                    # was filled, and reinstalling it over a working copy could
                    # just as easily be a downgrade.
                    Write-Ok "$($app.Name) is already installed."
                    continue
                }
                Write-Info "$($app.Name) is already installed; checking for an upgrade."
            } else {
                Write-Info "Installing $($app.Name)..."

                # ---- through the cache, always ---------------------------
                #
                # Not "use the cache if it happens to have this". Every install
                # goes through downloadCache: already there means install from
                # disk, not there means fetch it INTO the cache and install
                # from disk anyway.
                #
                # The difference matters because `winget install` downloads
                # into its own temp directory and deletes it afterwards. So a
                # machine that is wiped and re-run - a Sandbox, a test VM -
                # downloaded Git, Python, REAPER and Claude again every single
                # time while downloadCache sat there empty, which is the exact
                # thing this was built to stop.
                #
                # `winget download` is the same fetch from the same source, put
                # somewhere it survives, with the manifest beside it. One
                # download, then never again.
                $pkg       = Find-CachedPackage -Id $app.Id
                $fromCache = $false

                if ($pkg -and $pkg.Installer) {
                    Write-Info ("  downloadCache has {0} {1} - installing from disk." -f $app.Name, $pkg.Version)
                    # $app.Custom is appended after the manifest's own silent
                    # switches, which is exactly what winget's --custom does.
                    $fromCache = Install-CachedPackage -Package $pkg -ExtraArgs $app.Custom
                    if (-not $fromCache) { Write-Warn2 "  the cached copy did not install; falling back." }
                } elseif ($pkg) {
                    # Manifest but no installer: a previous run already found
                    # out this one cannot be run from disk. Do not spend the
                    # download finding out again.
                    Write-Info "  not installable from disk (noted on an earlier run); using winget."
                } elseif ($haveWinget) {
                    Write-Info "  fetching it into downloadCache..."
                    $pkg = Get-PackageToCache -Id $app.Id
                    if ($pkg) {
                        $fromCache = Install-CachedPackage -Package $pkg -ExtraArgs $app.Custom
                        if (-not $fromCache) { Write-Warn2 "  it did not install; falling back to winget." }
                    }
                }

                if ($fromCache) {
                    Write-Ok "$($app.Name) ready (installed from downloadCache)."
                    continue
                }
            }

            # ---- no winget, and the cache could not help ------------------
            if (-not $haveWinget) {
                if ($app.Id -like 'Python.Python.*') {
                    # Python from python.org, which needs nothing but a network
                    # connection. Everything else in the plugin depends on it,
                    # so it is worth a route of its own; REAPER and Claude have
                    # no equally stable direct URL, so those are named for the
                    # user rather than guessed at.
                    Write-Info "Installing Python directly from python.org..."
                    # Its exit code, not just its exceptions. install-python.ps1
                    # reports a failed download or a non-zero installer by
                    # exiting 1, which throws nothing at all - so catching only
                    # exceptions here would print "Python ready" over the top of
                    # its own error message.
                    $pyOk = $false
                    try {
                        & (Join-Path $Here 'install-python.ps1') -Version '3.12.10' | Out-Host
                        $pyOk = ($LASTEXITCODE -eq 0)
                    } catch {
                        Write-Err "Python could not be installed: $_"
                    }
                    if ($pyOk) {
                        Write-Ok "$($app.Name) ready."
                    } else {
                        Write-Err "$($app.Name) did not install."
                        Add-Problem "Install Python from https://www.python.org/downloads/ with 'Add python.exe to PATH' ticked."
                    }
                } else {
                    Write-Warn2 "$($app.Name) cannot be fetched without winget."
                    $manual += $app.Name
                }
                continue
            }

            # ---- winget --------------------------------------------------
            #
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

    if ($manual.Count -gt 0) {
        Write-Host ""
        Write-Info "Without winget these have to come from somewhere else:"
        Write-Info "    REAPER  https://www.reaper.fm/download.php"
        Write-Info "    Claude  https://claude.ai/download"
        Write-Info "Or, on a machine that can reach the internet, run [3] Prepare"
        Write-Info "Offline Files and copy the downloadCache folder onto this one."
        Write-Info "Either way, run [1] again afterwards - everything already done"
        Write-Info "is left alone and only the missing pieces filled in."
        Add-Problem ("Install {0} by hand (winget was unavailable), then re-run [1]." -f ($manual -join ' and '))
    }

    # A process reads PATH once at startup, so anything installed above is
    # invisible to this one.
    #
    # The authoritative answer is in the registry: installers write there, and
    # "restart your shell" means nothing more than "read it again". So read it
    # again. This is what makes `claude`, `git` and `python` findable below
    # without this script having to know where any of them chose to live.
    Update-PathFromRegistry

    # Then the specific locations, as a safety net for anything that puts a
    # binary somewhere without recording it in the registry. Kept deliberately:
    # the registry read above covers the general case, this covers the ones that
    # do not play by the rules, and neither is a reason to drop the other.
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

        # Claude first, and before REAPER, even though REAPER's first run comes
        # after it. Claude's installer launches the app itself, so by now it is
        # very likely already up and building its profile - and REAPER's first
        # run ends by force-closing what it opened. Getting Claude shut down
        # cleanly here means nothing downstream is racing a Chromium profile
        # that has never been written before.
        if ($claudeWasNew) { [void](Wait-ClaudeSettled) }

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
# 4. The plugin itself. configure-plugin.ps1 owns this and is unchanged by the wrapper.
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
# positional, so '-Force' would bind to configure-plugin.ps1's first parameter, -Only,
# and fail its ValidateSet.
$installArgs = @{}
if ($ReaperResourcePath) { $installArgs['ReaperResourcePath'] = $ReaperResourcePath }
if ($Force)              { $installArgs['Force'] = $true }

# Collect its problems into this run's summary instead of letting it print a
# second, possibly contradictory, banner of its own.
$problemsFile = Join-Path $env:TEMP ("rfc-problems-" + [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".txt")
$installArgs['ProblemsOut'] = $problemsFile

try {
    # Same NativeCommandError hazard as inside configure-plugin.ps1: pip and the claude
    # CLI both write to stderr in normal operation, and under EAP 'Stop' that
    # would abort the configuration this call exists to perform.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & (Join-Path $Here 'configure-plugin.ps1') @installArgs | Out-Host
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
    Write-Err  "The REAPER server cannot start."
    Write-Info "Claude shows 'reaper: server disconnected' until this is fixed."
    Write-Info "Its dependencies are missing. Build them with:"
    Write-Host ""
    Write-Host "        python `"$PluginRoot\scripts\bootstrap.py`"" -ForegroundColor White
    Write-Host ""
    if ($transcribing) { Write-Info "What went wrong is in $LogFile" }
    Add-Problem "The REAPER server cannot start - run scripts\bootstrap.py and read its output."
}

Write-Result -Problems $script:Problems

if ($stepLog)      { Write-Info "log         $stepLog" }
if ($transcribing) {
    Write-Info "transcript  $LogFile"
    try { Stop-Transcript | Out-Null } catch { }
}
Write-Host ""

# Tell the caller whether this worked.
#
# Until now nothing here set an exit code, so RunThisToStart.bat had no way to
# know - and printed "Start REAPER, restart Claude, you are done" after every
# run, including one where winget died, REAPER and Claude were never installed
# and five separate things had failed. A one-click installer that cannot tell
# the user yes or no has not finished the job.
exit $(if ($script:Problems.Count -eq 0) { 0 } else { 1 })
