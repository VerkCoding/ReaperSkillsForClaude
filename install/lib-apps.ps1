<#
  Shared helpers for finding, closing and first-running REAPER and Claude.

  Dot-sourced by setup-all.ps1. Nothing here runs on its own.

  The care in this file is mostly about one hazard: Claude Desktop and the
  Claude Code CLI are BOTH called claude.exe. Closing "claude" by name would
  kill the CLI - and since people run this setup from a terminal inside Claude,
  it can kill the very session executing the installer. So processes are matched
  by executable path, and this script's own ancestry is excluded from anything
  that closes.

  Nothing here is ever force-killed. REAPER prompts to save on a close request,
  and a forced kill would throw that prompt away along with the user's work.
  When a graceful close does not take, the answer is to ask the user again.
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

function Test-KeyPressed {
    <#
      Non-blocking keypress check, so a wait loop can offer "press a key to
      skip" without giving up its polling. Guarded because [Console]::KeyAvailable
      throws outright when stdin is redirected, which is exactly what happens if
      anyone drives this from a script.
    #>
    try {
        if ([Console]::KeyAvailable) {
            [void][Console]::ReadKey($true)
            return $true
        }
    } catch { }
    return $false
}

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

function Request-AppClosed {
    <#
      Close an application, politely and repeatedly, never by force.

      CloseMainWindow is the same request the X button sends, so REAPER's
      "save changes?" prompt appears and the user stays in control of their
      work. If it is still running afterwards, that usually means such a prompt
      is waiting - so ask, wait, and look again rather than escalating.

      Returns $true if the application is gone, $false if the user chose to
      continue with it still running.
    #>
    param(
        [ValidateSet('reaper', 'claude')] [string]$Kind,
        [string]$Label,
        [int]$GraceSeconds = 15
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

        Write-Warn2 "$Label is still running."
        Write-Info  "It may be showing a 'save changes?' prompt - answer it, then continue."
        Write-Host  "  Press Enter once $Label is closed (or to continue anyway)." -ForegroundColor Yellow
        [void](Read-Host)
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
            Start-Sleep -Seconds 3
            Write-Ok "REAPER created its configuration."
            [void](Request-AppClosed -Kind reaper -Label 'REAPER')
            # REAPER writes reaper.ini again on exit, so re-confirm after closing.
            Start-Sleep -Seconds 1
            return (Test-Path $ini)
        }
    }

    Write-Warn2 "REAPER did not create reaper.ini within $TimeoutSeconds seconds."
    Write-Host "  Finish any dialog it is showing, then press Enter." -ForegroundColor Yellow
    [void](Read-Host)
    [void](Request-AppClosed -Kind reaper -Label 'REAPER')
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
    Write-Host "  Sign in to Claude in the window that opened." -ForegroundColor Yellow
    Write-Host "  This continues by itself once you are signed in." -ForegroundColor Yellow
    Write-Host "  Press any key here to skip and carry on without signing in." -ForegroundColor Gray
    Write-Host ""

    $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
    $launchedAt = Get-Date
    $sawRunning = $false
    $ticks      = 0

    # Reopening extends the deadline, so without a cap the timeout can never be
    # reached and a Claude that fails to stay open - crashing at launch, say -
    # would prompt forever.
    $reopens    = 0
    $maxReopens = 3

    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 1000
        $ticks++

        if (Test-ClaudeSignedIn) {
            Write-Host ""
            Write-Ok "Signed in to Claude."
            [void](Request-AppClosed -Kind claude -Label 'Claude')
            return $true
        }

        # Skipping has to be possible. Without it, someone who decides not to
        # sign in now has no way out of this loop but Ctrl+C, which would
        # abandon the install halfway.
        if (Test-KeyPressed) {
            Write-Host ""
            Write-Warn2 "Skipped at your request - no sign-in recorded."
            return $false
        }

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

            if ($reopens -ge $maxReopens) {
                Write-Info "Tried $maxReopens times. Carrying on without a session."
                return $false
            }

            Write-Host "    [R] open it again and wait" -ForegroundColor Gray
            Write-Host "    [S] skip - sign in later yourself" -ForegroundColor Gray
            $ans = Read-Host "  Choose [R/S]"
            if ($ans -match '^[Rr]') {
                $reopens++
                if (Start-ClaudeDesktop) {
                    $sawRunning = $false
                    $launchedAt = Get-Date
                    $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
                    Write-Info "Waiting again (attempt $reopens of $maxReopens)..."
                    continue
                }
                Write-Warn2 "Could not reopen Claude."
            }
            Write-Info "Carrying on without a Claude session."
            return $false
        }

        if ($ticks % 30 -eq 0) {
            Write-Host "  still waiting for sign-in... (press a key to skip)" -ForegroundColor DarkGray
        }
    }

    Write-Warn2 "No sign-in detected after $([int]($TimeoutSeconds / 60)) minutes."
    Write-Info  "You can sign in later; the plugin is installed either way."
    return $false
}
