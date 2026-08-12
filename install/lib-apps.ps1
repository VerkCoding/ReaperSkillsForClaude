<#
  Shared helpers for finding, closing and first-running REAPER and Claude.

  Dot-sourced by setup-all.ps1. Nothing here runs on its own.

  The care in this file is mostly about one hazard: Claude Desktop and the
  Claude Code CLI are BOTH called claude.exe. Closing "claude" by name would
  kill the CLI - and since people run this setup from a terminal inside Claude,
  it can kill the very session executing the installer. So processes are matched
  by executable path, and this script's own ancestry is excluded from anything
  that closes.

  Closing is always polite first. CloseMainWindow is the same request the X
  button sends, so REAPER's "save changes?" prompt appears and the user keeps
  control of their work.

  Escalating to a kill is allowed in exactly one situation: the first-run steps,
  where this script opened the application itself moments earlier. There is no
  project and no unsaved edit to lose, only a splash screen or a licence dialog
  in the way, and making the user click through it is the clunky part worth
  removing.

  The user's own session is never killed. When they are asked to close REAPER at
  the start, a refusal means asking again - hours of unsaved work is not
  something to trade for a smoother install.
#>

# Deliberately no Set-StrictMode here. This file is dot-sourced, so a strict
# mode set here would apply to the whole installer, which was not written under
# it - and strict mode turns "this registry key has no DisplayName" from a
# false into a terminating error, which is precisely the shape of most probing
# done below.

# ---------------------------------------------------------------------------
# Process identity
# ---------------------------------------------------------------------------

function Get-AncestorPids {
    <#
      Every process between this one and the root, so we can refuse to close any
      of them. Running the installer from a terminal hosted inside Claude is
      normal, and "close Claude" must not mean "kill yourself".
    #>
    $ids = @()
    $current = $PID
    for ($i = 0; $i -lt 12; $i++) {
        $ids += $current
        $p = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
        if (-not $p -or -not $p.ParentProcessId -or $p.ParentProcessId -eq 0) { break }
        $current = $p.ParentProcessId
    }
    return $ids
}

function Get-ProcPath {
    param($Process)
    try { return $Process.Path } catch { return $null }
}

# A non-blocking keypress helper lived here, so the sign-in wait could offer
# "press any key to skip". Signing in is required now and leaving it is a typed
# SKIP, so nothing used it any more.

function Get-TargetProcesses {
    <#
      .PARAMETER Kind
        'reaper'  - reaper.exe
        'claude'  - Claude DESKTOP only. Excludes claude-code, whose executable
                    shares the name and is very likely running this script.
    #>
    param([ValidateSet('reaper', 'claude')] [string]$Kind)

    $ancestors = Get-AncestorPids
    $names = if ($Kind -eq 'reaper') { @('reaper', 'reaper64') } else { @('claude', 'Claude') }

    $procs = @()
    foreach ($n in $names) {
        $procs += Get-Process -Name $n -ErrorAction SilentlyContinue
    }

    $procs | Where-Object {
        $_.Id -notin $ancestors -and
        ($Kind -ne 'claude' -or (Get-ProcPath $_) -notlike '*claude-code*')
    } | Sort-Object Id -Unique
}

function Test-SelfHostedBy {
    <# Is this script running inside the application we are about to close? #>
    param([ValidateSet('reaper', 'claude')] [string]$Kind)

    $ancestors = Get-AncestorPids
    foreach ($id in $ancestors) {
        $p = Get-Process -Id $id -ErrorAction SilentlyContinue
        if (-not $p) { continue }
        $path = Get-ProcPath $p
        if ($Kind -eq 'claude' -and $p.Name -match 'claude' -and $path -notlike '*claude-code*') { return $true }
        if ($Kind -eq 'reaper' -and $p.Name -match 'reaper') { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------
# Closing
# ---------------------------------------------------------------------------

function Confirm-AppsClosed {
    <#
      Re-check that REAPER and Claude are still closed, and close anything that
      came back.

      Called after every point where the installer waited on the user. Between
      the prompt and the answer, anything can have happened: REAPER restarted
      from a jump list, Claude relaunched from the tray, or the user opened one
      to check something before pressing Enter. Both applications rewrite their
      settings from memory when they exit, so a single one that slipped back
      open silently discards whatever is written next.

      Cheap when there is nothing to do - it just enumerates processes.

      -AllowForce is passed through for the first-run checks, where this script
      opened the application itself. It is deliberately NOT used for the check
      before the configuration writes: by then the user has been through several
      prompts and may have reopened REAPER with a project in it.
    #>
    param([string]$Because = "", [switch]$AllowForce)

    $reopened = @()
    foreach ($kind in @('reaper', 'claude')) {
        if (@(Get-TargetProcesses $kind).Count -gt 0 -and -not (Test-SelfHostedBy $kind)) {
            $reopened += $kind
        }
    }
    if ($reopened.Count -eq 0) { return $true }

    if ($Because) { Write-Info $Because }
    $allClosed = $true
    foreach ($kind in $reopened) {
        $label = if ($kind -eq 'reaper') { 'REAPER' } else { 'Claude' }
        Write-Warn2 "$label is open again - closing it before continuing."
        if (-not (Request-AppClosed -Kind $kind -Label $label -AllowForce:$AllowForce)) {
            $allClosed = $false
        }
    }
    return $allClosed
}

function Request-AppClosed {
    <#
      Close an application. Politely first, always.

      CloseMainWindow is the same request the X button sends, so REAPER's
      "save changes?" prompt appears and the user stays in control of their
      work. Still running afterwards usually means such a prompt is waiting.

      What happens then depends on -AllowForce: without it, ask the user and
      look again; with it, end the process. See that parameter for why the
      difference is safe.

      Returns $true if the application is gone.
    #>
    param(
        [ValidateSet('reaper', 'claude')] [string]$Kind,
        [string]$Label,
        [int]$GraceSeconds = 15,
        # Escalate to a hard kill instead of asking the user again.
        #
        # Only ever passed from the first-run steps, and the distinction is the
        # whole safety argument: there, THIS SCRIPT opened the application
        # seconds ago, so there is no project, no unsaved edit and no reason to
        # negotiate. It is a splash screen or a licence dialog holding things
        # up, and prompting a user to click through it is the clunky part.
        #
        # Never passed for the opening "close your apps" step. That one is the
        # user's own running session, which may have hours of unsaved work in
        # it, and no amount of convenience justifies taking that away.
        [switch]$AllowForce
    )

    if (Test-SelfHostedBy $Kind) {
        Write-Warn2 "$Label is hosting this installer, so it cannot be closed from here."
        if ($Kind -eq 'claude') {
            Write-Info "That is usually fine. One consequence: Claude rewrites its own"
            Write-Info "config from memory, so the MCP server entry written later may be"
            Write-Info "reverted when it exits. Restart Claude at the end and re-run"
            Write-Info "option [1] if the REAPER tools are missing."
        }
        return $false
    }

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $procs = @(Get-TargetProcesses $Kind)
        if ($procs.Count -eq 0) {
            if ($attempt -eq 1) { Write-Ok "$Label is closed." } else { Write-Ok "$Label closed." }
            return $true
        }

        Write-Info "Asking $Label to close ($($procs.Count) process(es))..."
        foreach ($p in $procs) {
            try { if ($p.MainWindowHandle -ne 0) { [void]$p.CloseMainWindow() } } catch { }
        }

        $deadline = (Get-Date).AddSeconds($GraceSeconds)
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Milliseconds 500
            if (@(Get-TargetProcesses $Kind).Count -eq 0) {
                Write-Ok "$Label closed."
                return $true
            }
        }

        if ($AllowForce) {
            # Stop-Process -Force is taskkill /F, done against the exact process
            # IDs Get-TargetProcesses returned - which have already had this
            # script's own ancestry and the Claude Code CLI filtered out of them.
            # `taskkill /IM claude.exe` would have hit both, and one of those is
            # frequently the terminal running this.
            $doomed = @(Get-TargetProcesses $Kind)
            Write-Warn2 "$Label did not close on request; ending it ($($doomed.Count) process(es))."
            foreach ($p in $doomed) {
                try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { }
            }
            Start-Sleep -Seconds 2
            if (@(Get-TargetProcesses $Kind).Count -eq 0) {
                Write-Ok "$Label closed."
                return $true
            }
            Write-Warn2 "$Label survived that; continuing anyway."
            return $false
        }

        Write-Warn2 "$Label is still running."
        Write-Info  "It may be showing a 'save changes?' prompt - answer it, then continue."
        Write-Host  "  Press Enter once $Label is closed (or to continue anyway)." -ForegroundColor Yellow
        [void](Read-Host)
        # The next loop iteration re-enumerates and re-sends the close, so a
        # window that reappeared while the prompt was waiting is caught here
        # rather than surviving into the writes that follow.
    }

    if (@(Get-TargetProcesses $Kind).Count -eq 0) {
        Write-Ok "$Label closed."
        return $true
    }

    # Say what continuing actually costs, rather than just noting it. Both
    # applications rewrite their settings from memory when they exit, so the
    # step that writes to them is the one that will be lost.
    Write-Warn2 "Continuing with $Label still running."
    if ($Kind -eq 'reaper') {
        Write-Info "REAPER's connection settings will be SKIPPED rather than written"
        Write-Info "and then discarded when it exits. Close REAPER and re-run [1] to"
        Write-Info "finish that step."
    } else {
        Write-Info "Claude may revert the MCP server entry written later. If the REAPER"
        Write-Info "tools are missing afterwards, close Claude fully and re-run [1]."
    }
    return $false
}

# ---------------------------------------------------------------------------
# Locating executables
# ---------------------------------------------------------------------------

function Get-UninstallEntry {
    param([string]$Pattern, [string]$ExcludePattern)
    $keys = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    # Most uninstall keys have no DisplayName at all, so the property is fetched
    # rather than dereferenced - the difference between a clean miss and an
    # error under any caller that has strict mode on.
    Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object {
        $name = $_.PSObject.Properties['DisplayName']
        $name -and $name.Value -and $name.Value -match $Pattern -and
        (-not $ExcludePattern -or $name.Value -notmatch $ExcludePattern)
    } | Select-Object -First 1
}

function Get-ReaperExe {
    $e = Get-UninstallEntry -Pattern 'REAPER'
    if ($e -and $e.InstallLocation) {
        $exe = Join-Path $e.InstallLocation 'reaper.exe'
        if (Test-Path $exe) { return $exe }
    }
    foreach ($p in @(
        "$env:ProgramFiles\REAPER (x64)\reaper.exe",
        "$env:ProgramFiles\REAPER\reaper.exe",
        "${env:ProgramFiles(x86)}\REAPER\reaper.exe",
        "$env:APPDATA\REAPER\reaper.exe"
    )) { if (Test-Path $p) { return $p } }
    return $null
}

function Start-ClaudeDesktop {
    <#
      Claude Desktop ships either as a normal executable or as an MSIX package,
      and the two are launched completely differently. Try the executable first,
      then fall back to the AppsFolder shell path an MSIX install needs.
      Returns $true if a launch was attempted.
    #>
    $e = Get-UninstallEntry -Pattern 'Claude' -ExcludePattern 'Claude Code'
    if ($e -and $e.InstallLocation) {
        foreach ($n in @('Claude.exe', 'claude.exe')) {
            $exe = Join-Path $e.InstallLocation $n
            if (Test-Path $exe) { Start-Process $exe; return $true }
        }
    }
    foreach ($p in @(
        "$env:LOCALAPPDATA\AnthropicClaude\Claude.exe",
        "$env:LOCALAPPDATA\Programs\Claude\Claude.exe",
        "$env:ProgramFiles\Claude\Claude.exe"
    )) { if (Test-Path $p) { Start-Process $p; return $true } }

    try {
        $pkg = Get-AppxPackage -Name 'Claude*' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($pkg) {
            $appId = (Get-AppxPackageManifest $pkg).Package.Applications.Application.Id
            if ($appId -is [array]) { $appId = $appId[0] }
            Start-Process "shell:AppsFolder\$($pkg.PackageFamilyName)!$appId"
            return $true
        }
    } catch { }

    return $false
}

# ---------------------------------------------------------------------------
# Login detection
# ---------------------------------------------------------------------------

function Test-ClaudeSignedIn {
    <#
      Presence check only. Token VALUES are never read, printed or stored -
      the question is "is there a session", not "what is it".

      lastKnownAccountUuid is written once an account is attached, and survives
      the app being closed, which makes it a better signal than any window state.
    #>
    $paths = @("$env:APPDATA\Claude\config.json")
    Get-ChildItem "$env:LOCALAPPDATA\Packages" -Filter 'Claude_*' -Directory -ErrorAction SilentlyContinue |
        ForEach-Object { $paths += Join-Path $_.FullName 'LocalCache\Roaming\Claude\config.json' }

    foreach ($p in $paths) {
        if (-not (Test-Path $p)) { continue }
        try {
            $j = Get-Content $p -Raw -ErrorAction Stop | ConvertFrom-Json
        } catch { continue }
        $names = $j.PSObject.Properties.Name
        if ($names -contains 'lastKnownAccountUuid' -and
            -not [string]::IsNullOrWhiteSpace([string]$j.'lastKnownAccountUuid')) { return $true }
        if (($names -contains 'oauth:tokenCacheV2') -or ($names -contains 'oauth:tokenCache')) { return $true }
    }
    return $false
}

# ---------------------------------------------------------------------------
# First run
# ---------------------------------------------------------------------------

function Invoke-ReaperFirstRun {
    <#
      A freshly installed REAPER has no resource folder: reaper.ini and
      everything beside it appear only after it has run once. Without this, the
      whole REAPER half of the setup has nothing to write to, and the user is
      told to "launch REAPER once and come back" - which is a strange thing for
      a one-click installer to say.
    #>
    param([string]$ReaperResourcePath, [int]$TimeoutSeconds = 180)

    $ini = Join-Path $ReaperResourcePath 'reaper.ini'
    if (Test-Path $ini) {
        Write-Ok "REAPER has already created its configuration."
        return $true
    }

    $exe = Get-ReaperExe
    if (-not $exe) {
        Write-Warn2 "Could not find reaper.exe to launch."
        return $false
    }

    Write-Info "Starting REAPER once so it creates its configuration..."
    Write-Host ""
    Write-Host "  REAPER is opening. If it shows a first-run or licence dialog," -ForegroundColor Yellow
    Write-Host "  click through it. This will close REAPER again automatically." -ForegroundColor Yellow
    Write-Host ""
    try { Start-Process $exe } catch {
        Write-Warn2 "Could not start REAPER: $_"
        return $false
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        if (Test-Path $ini) {
            # Written progressively; let it settle before anything reads it.
            # This wait matters more now that the close can be forced: REAPER
            # must have finished writing the file we came here for before it is
            # allowed to die without a clean exit.
            Start-Sleep -Seconds 3
            Write-Ok "REAPER created its configuration."
            [void](Request-AppClosed -Kind reaper -Label 'REAPER' -GraceSeconds 10 -AllowForce)
            # REAPER writes reaper.ini again on exit, so give that write time to
            # land, then make sure it did not come back up - a splash screen or
            # a queued restart would otherwise still be holding the file.
            Start-Sleep -Seconds 2
            [void](Confirm-AppsClosed -AllowForce)
            return (Test-Path $ini)
        }
    }

    Write-Warn2 "REAPER did not create reaper.ini within $TimeoutSeconds seconds."
    Write-Host "  Finish any dialog it is showing, then press Enter." -ForegroundColor Yellow
    [void](Read-Host)
    [void](Request-AppClosed -Kind reaper -Label 'REAPER' -GraceSeconds 10 -AllowForce)
    Start-Sleep -Seconds 2
    [void](Confirm-AppsClosed -AllowForce)
    return (Test-Path $ini)
}

function Invoke-ClaudeFirstRun {
    <#
      A fresh Claude has no session, and the plugin is useless without one. Open
      it, let the user sign in, and confirm from config rather than from a
      window title - a window can be open with nobody signed in.
    #>
    param(
        [int]$TimeoutSeconds = 600,
        # How long to give Claude to show a process before deciding the launch
        # did not take. Generous: a cold Electron start on a slow disk is not
        # quick.
        [int]$AppearSeconds = 45
    )

    if (Test-ClaudeSignedIn) {
        Write-Ok "Claude is already signed in."
        return $true
    }

    Write-Info "Opening Claude so you can sign in..."
    if (-not (Start-ClaudeDesktop)) {
        Write-Warn2 "Could not find Claude to launch. Open it yourself and sign in."
    }

    Write-Host ""
    Write-Host "  SIGN IN TO CLAUDE in the window that opened." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  This is required, not optional. Claude was just installed, so it" -ForegroundColor Yellow
    Write-Host "  has no account attached - and without one it cannot load the" -ForegroundColor Yellow
    Write-Host "  plugin or reach REAPER, which is the whole point of this setup." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  This continues by itself the moment you are signed in." -ForegroundColor Gray
    Write-Host ""

    $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
    $launchedAt = Get-Date
    $sawRunning = $false
    $ticks      = 0

    # Reopening extends the deadline, so without a cap the timeout can never be
    # reached and a Claude that fails to stay open - crashing at launch, say -
    # would prompt forever. Refusals are counted separately: pressing Enter at
    # the prompt means "retry", so that too needs a bound.
    $reopens     = 0
    $maxReopens  = 3
    $refusals    = 0
    $maxRefusals = 6

    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 1000
        $ticks++

        if (Test-ClaudeSignedIn) {
            Write-Host ""
            Write-Ok "Signed in to Claude."
            # The session is already on disk - that is what was just detected -
            # so there is nothing for a clean shutdown to preserve.
            [void](Request-AppClosed -Kind claude -Label 'Claude' -GraceSeconds 10 -AllowForce)
            # Signing in often leaves Claude relaunching or restoring a window,
            # so verify rather than trusting the first close.
            [void](Confirm-AppsClosed -AllowForce)
            return $true
        }

        # No keypress escape here, unlike the other waits. Signing in is
        # required, so leaving it has to be a deliberate act rather than
        # something a stray keystroke does - see the SKIP prompt below.

        # Two ways this stalls, both of which would otherwise burn the whole
        # timeout waiting on a window that is never going to be signed into:
        # Claude was open and the user closed it, or it never appeared at all.
        # A launch call returning success only means a launch was attempted.
        $running = @(Get-TargetProcesses 'claude').Count -gt 0
        $stall   = $null
        if ($running) {
            $sawRunning = $true
        } elseif ($sawRunning) {
            $stall = "Claude was closed before a sign-in was recorded."
        } elseif (((Get-Date) - $launchedAt).TotalSeconds -gt $AppearSeconds) {
            $stall = "Claude does not appear to have started."
        }

        if ($stall) {
            Write-Host ""
            Write-Warn2 $stall

            # Leaving without a session is possible, but only on purpose. A
            # single keypress must not do it, because the result is a plugin
            # that installs cleanly and then does nothing - the worst outcome
            # to debug. Typing the word is the whole point.
            if ($reopens -lt $maxReopens) {
                Write-Host "    [Enter] open Claude again and keep waiting" -ForegroundColor Gray
                Write-Host "    SKIP    continue without signing in (not recommended)" -ForegroundColor Gray
            } else {
                Write-Warn2 "Reopened $maxReopens times without a sign-in."
                Write-Host "    [Enter] try once more" -ForegroundColor Gray
                Write-Host "    SKIP    continue without signing in (not recommended)" -ForegroundColor Gray
            }

            # Case-insensitive, but still the whole word: the point is that
            # leaving takes a deliberate four letters, not that the user has to
            # guess the capitalisation.
            $ans = Read-Host "  Type SKIP, or press Enter to retry"
            if ("$ans".Trim() -eq 'SKIP') {
                Write-Host ""
                Write-Warn2 "Continuing WITHOUT a Claude sign-in, at your request."
                Write-Info  "The plugin will be installed, but Claude cannot use it until"
                Write-Info  "you sign in. Do that, then restart Claude."
                return $false
            }

            $refusals++
            if ($refusals -ge $maxRefusals) {
                # Anything but SKIP means "retry", so an empty pipe or somebody
                # leaning on Enter would loop here forever. Bounded, loudly.
                Write-Host ""
                Write-Warn2 "No sign-in after $maxRefusals attempts. Continuing without one."
                Write-Info  "Sign in to Claude yourself, then restart it."
                return $false
            }

            $reopens++
            if (Start-ClaudeDesktop) {
                $sawRunning = $false
                $launchedAt = Get-Date
                $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
                Write-Info "Waiting again..."
                continue
            }
            Write-Warn2 "Could not reopen Claude. Open it yourself and sign in."
            $sawRunning = $false
            $launchedAt = Get-Date
            $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
        }

        if ($ticks % 30 -eq 0) {
            Write-Host "  still waiting for sign-in... (press a key to skip)" -ForegroundColor DarkGray
        }
    }

    Write-Warn2 "No sign-in detected after $([int]($TimeoutSeconds / 60)) minutes."
    Write-Info  "You can sign in later; the plugin is installed either way."
    return $false
}
