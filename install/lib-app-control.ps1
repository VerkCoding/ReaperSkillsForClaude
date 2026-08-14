<#
  Shared helpers for finding, closing and first-running REAPER and Claude.

  Dot-sourced by install-everything.ps1. Nothing here runs on its own.

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

# Write-Ok / Write-Info / Write-Warn2, used throughout this file, are defined in
# lib-console.ps1. Sourced here rather than assumed from the caller: every
# consumer happens to load lib-console.ps1 first today, and the first one that
# does not - anyone wanting only the app helpers - would get "Write-Info is not
# recognized" at the moment a process refuses to close, not at load. Dot-sourcing
# it twice just redefines the same functions.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

# ---------------------------------------------------------------------------
# The console
# ---------------------------------------------------------------------------

function Disable-ConsoleQuickEdit {
    <#
      Stop a stray mouse click from pausing the whole install.

      QuickEdit is on by default in the Windows console. A click in the window
      starts a text selection, and a console with a selection active BLOCKS the
      next write to it - so the script stops dead at its next Write-Host and
      stays there until somebody presses a key. Nobody does, because a window
      full of output that has simply stopped scrolling looks exactly like a
      window that is busy working. People walk away from it.

      That is not a cosmetic problem here. This setup opens and closes REAPER
      and Claude on timers; a run frozen in the middle of that leaves both in
      whatever half-state they were in.

      Only the console this process is attached to is changed, and only for as
      long as it lives - nothing is written to the registry, so the user's own
      console defaults are untouched. ENABLE_EXTENDED_FLAGS must be set in the
      same call or the QuickEdit bit is ignored.

      Best effort: a redirected or absent console simply has no mode to set,
      which is not a reason to stop installing.
    #>
    try {
        if (-not ('Rfc.Console' -as [type])) {
            Add-Type -Namespace Rfc -Name Console -MemberDefinition @'
[DllImport("kernel32.dll", SetLastError = true)]
public static extern IntPtr GetStdHandle(int nStdHandle);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool GetConsoleMode(IntPtr hConsoleHandle, out uint lpMode);
[DllImport("kernel32.dll", SetLastError = true)]
public static extern bool SetConsoleMode(IntPtr hConsoleHandle, uint dwMode);
'@ -ErrorAction Stop
        }

        $handle = [Rfc.Console]::GetStdHandle(-10)          # STD_INPUT_HANDLE
        if ($handle -eq [IntPtr]::Zero -or $handle -eq [IntPtr](-1)) { return $false }

        $mode = [uint32]0
        if (-not [Rfc.Console]::GetConsoleMode($handle, [ref]$mode)) { return $false }

        $QUICK_EDIT     = [uint32]0x0040
        $EXTENDED_FLAGS = [uint32]0x0080

        if (($mode -band $QUICK_EDIT) -eq 0) { return $true }   # already off

        $new = [uint32](($mode -bor $EXTENDED_FLAGS) -band (-bnot $QUICK_EDIT))
        return [Rfc.Console]::SetConsoleMode($handle, $new)
    } catch {
        return $false
    }
}

# ---------------------------------------------------------------------------
# Process identity
# ---------------------------------------------------------------------------

function Update-PathFromRegistry {
    <#
      Re-read PATH from the registry into this process.

      A process gets its environment when it starts and never hears about
      changes. Installers write the new PATH to the registry and print "restart
      your shell" - and this script cannot restart itself, so anything it
      installs is invisible to it for the rest of the run.

      That was being worked around with a hand-maintained list of guessed
      install directories, which has to track every vendor's packaging decisions
      forever and was already wrong once: Claude Code is a portable winget
      package under WinGet\Packages, not an alias in WinGet\Links, so the
      plugin was never registered with Claude Code at all.

      Reading the registry is what "restart your shell" actually does. The
      current value is merged in rather than replaced, so anything deliberately
      prepended earlier in the run survives.
    #>
    $parts = @()
    foreach ($scope in 'Machine', 'User') {
        try {
            $v = [Environment]::GetEnvironmentVariable('Path', $scope)
            if ($v) { $parts += $v.Split(';') }
        } catch {
            # A locked-down or unreadable hive is not fatal: the process PATH
            # below still holds whatever was there at startup.
        }
    }
    $parts += $env:PATH.Split(';')

    $seen  = New-Object System.Collections.Generic.HashSet[string] ([StringComparer]::OrdinalIgnoreCase)
    $clean = @()
    foreach ($d in $parts) {
        $d = $d.Trim()
        if ($d -and $seen.Add($d)) { $clean += $d }
    }
    $env:PATH = ($clean -join ';')
}

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

      -Only narrows it to one application, and pairs with -AllowForce every time.
      Without it, REAPER's first run - which legitimately force-closes the REAPER
      it just opened - also swept CLAUDE, force-closing an app it had never
      opened and knew nothing about. On a machine with a filled downloadCache
      there is no download to slow the run down, so that sweep lands while
      Claude's installer is still auto-launching it for the very first time and
      Chromium is building its Local Storage. A SIGKILL there leaves a LevelDB
      that cannot be read, and with no account attached yet there is no session
      to fall back on: Claude then fails to start, every time, and nothing in
      the setup or the revert knows why. Force-closing is only ever defensible
      against the application this script just opened, so it now has to be
      named.
    #>
    param(
        [string]$Because = "",
        [switch]$AllowForce,
        [ValidateSet('reaper', 'claude')] [string[]]$Only
    )

    $kinds = if ($Only) { $Only } else { @('reaper', 'claude') }

    $reopened = @()
    foreach ($kind in $kinds) {
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
            #
            # Swept repeatedly rather than once. Claude is Electron: one window
            # plus a dozen helpers plus a tray icon, and killing the main process
            # can leave helpers behind or let a supervisor restart one. A single
            # pass regularly leaves something running in the notification area,
            # still holding the config file this setup is about to write.
            for ($sweep = 1; $sweep -le 4; $sweep++) {
                $doomed = @(Get-TargetProcesses $Kind)
                if ($doomed.Count -eq 0) { break }

                if ($sweep -eq 1) {
                    Write-Warn2 "$Label did not close on request; ending it ($($doomed.Count) process(es))."
                } else {
                    Write-Info "sweep $sweep - $($doomed.Count) still running (helpers or tray)"
                }

                foreach ($p in $doomed) {
                    try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { }
                }
                Start-Sleep -Milliseconds 1200
            }

            $left = @(Get-TargetProcesses $Kind).Count
            if ($left -eq 0) {
                Write-Ok "$Label fully closed, including background processes."
                return $true
            }
            Write-Warn2 "$Label still has $left process(es) running; continuing anyway."
            return $false
        }

        Write-Warn2 "$Label is still open - it may be asking you to save."
        Write-Host  "  Close $Label, then press Enter here." -ForegroundColor Yellow
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

function Get-AppList {
    <#
      The applications, in dependency order. `Safe` means "leave it alone if
      present", which is the whole of the promise not to touch user data.

      Lives here rather than in install-everything.ps1 because two scripts need the same
      list and neither owns it: setup-all installs them, and
      fill-download-cache.ps1 downloads them ahead of time. A list kept in two
      files is a list that will disagree with itself.

      -PythonCustom is setup-all's decision about whether Python may take over
      PATH, which is not a decision this list should be making. Empty is fine
      for any caller that is only naming the packages.
    #>
    param([string]$PythonCustom)

    @(
        # First, because it is what the compiled wheels in the virtualenv link
        # against. Windows ships no C++ runtime and Python installs only the C
        # one (vcruntime140.dll), so on a machine that has never had a C++
        # application installed - a fresh Sandbox, a clean VM - msvcp140.dll is
        # simply absent. numpy and scipy survive that; soxr does not, and soxr
        # is imported by librosa, so `import librosa` fails with "DLL load
        # failed while importing soxr_ext" and takes analyze_frequency_spectrum
        # and analyze_transients with it. The message names neither librosa nor
        # the missing runtime, which is why this belongs in the install rather
        # than in a troubleshooting note.
        [pscustomobject]@{ Id = 'Microsoft.VCRedist.2015+.x64'; Name = 'Visual C++ runtime'; Safe = $true; Custom = $null }
        [pscustomobject]@{ Id = 'Python.Python.3.12';   Name = 'Python 3.12';     Safe = $false; Custom = $PythonCustom }
        [pscustomobject]@{ Id = 'Git.Git';              Name = 'Git';             Safe = $false; Custom = $null }
        [pscustomobject]@{ Id = 'Cockos.REAPER';        Name = 'REAPER';          Safe = $true;  Custom = $null }
        [pscustomobject]@{ Id = 'Anthropic.Claude';     Name = 'Claude Desktop';  Safe = $true;  Custom = $null }
        [pscustomobject]@{ Id = 'Anthropic.ClaudeCode'; Name = 'Claude Code';     Safe = $true;  Custom = $null }
    )
}

function Test-AppPresent {
    <#
      Is this application already on the machine? Asked WITHOUT winget.

      The install loop normally answers this with `winget list`, which is the
      better answer - it knows about versions and about packages this setup did
      not install. But the whole point of downloadCache is that applications can
      now be installed on a machine where winget never appeared, and there the
      question still has to be answered: `Safe = $true` means "leave REAPER
      alone if it is already here", and the first-run steps must only fire for
      what this run actually introduced.

      Deliberately loose. A false "already here" would skip an install; a false
      "missing" only re-runs an installer over itself, which every one of these
      handles. So each check errs towards missing.
    #>
    param([string]$Id)

    switch ($Id) {
        'Microsoft.VCRedist.2015+.x64' {
            # The file the wheels actually need, rather than an uninstall entry.
            # The runtime arrives by many routes - another application's
            # installer, a Windows image that already had it - and what matters
            # is only whether the DLL is loadable.
            return (Test-Path (Join-Path $env:SystemRoot 'System32\msvcp140.dll'))
        }
        'Cockos.REAPER'  { return [bool](Get-ReaperExe) }
        'Git.Git'        { return [bool](Get-Command git -ErrorAction SilentlyContinue) }
        'Python.Python.3.12' {
            # Any Python 3 counts, for the same reason setup-all does not force
            # 3.12 onto PATH: the virtualenv is what actually matters.
            return [bool](Get-Command python -ErrorAction SilentlyContinue)
        }
        'Anthropic.ClaudeCode' { return [bool](Get-Command claude -ErrorAction SilentlyContinue) }
        'Anthropic.Claude' {
            if (Get-UninstallEntry -Pattern 'Claude' -ExcludePattern 'Claude Code') { return $true }
            foreach ($p in @(
                "$env:LOCALAPPDATA\AnthropicClaude\Claude.exe",
                "$env:LOCALAPPDATA\Programs\Claude\Claude.exe",
                "$env:ProgramFiles\Claude\Claude.exe"
            )) { if (Test-Path $p) { return $true } }
            try {
                if (Get-AppxPackage -Name 'Claude*' -ErrorAction SilentlyContinue) { return $true }
            } catch { }
            return $false
        }
    }
    return $false
}

# ---------------------------------------------------------------------------
# Login detection
# ---------------------------------------------------------------------------

function Get-ClaudeProfilePath {
    <#
      Every directory Claude Desktop keeps its own state in, whichever way it
      was installed. Existing ones only.

      Two shapes: a plain install writes to %APPDATA%\Claude, and an MSIX one is
      redirected into its package's LocalCache. Both hold the same things -
      config.json, and Chromium's Local Storage, which is the LevelDB that a
      kill at the wrong moment ruins.

      One list, because three separate places used to enumerate this and the
      backup only knew about the ones that existed when it ran - which on a
      first install is neither of them.
    #>
    $dirs = @()
    $plain = Join-Path $env:APPDATA 'Claude'
    if (Test-Path $plain -PathType Container) { $dirs += $plain }

    Get-ChildItem (Join-Path $env:LOCALAPPDATA 'Packages') -Filter 'Claude_*' -Directory -ErrorAction SilentlyContinue |
        ForEach-Object {
            $d = Join-Path $_.FullName 'LocalCache\Roaming\Claude'
            if (Test-Path $d -PathType Container) { $dirs += $d }
        }
    return $dirs
}

function Test-ClaudeSignedIn {
    <#
      Presence check only. Token VALUES are never read, printed or stored -
      the question is "is there a session", not "what is it".

      lastKnownAccountUuid is written once an account is attached, and survives
      the app being closed, which makes it a better signal than any window state.
    #>
    $paths = @()
    foreach ($d in (Get-ClaudeProfilePath)) { $paths += Join-Path $d 'config.json' }

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

    Write-Host ""
    Write-Host "  Opening REAPER so it can create its settings." -ForegroundColor Yellow
    Write-Host "  If it shows a first-run or licence dialog, click through it." -ForegroundColor Yellow
    Write-Host "  It closes again by itself - nothing to press here." -ForegroundColor Gray
    Write-Host ""
    try { Start-Process $exe } catch {
        Write-Warn2 "Could not start REAPER: $_"
        return $false
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $sawWindow = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1

        # "Fully open" means a main window exists, not merely that the file
        # appeared. REAPER creates reaper.ini early in startup and keeps writing
        # to it, so acting on the file alone can catch it mid-launch - and the
        # close below is forced, which would end it partway through.
        if (-not $sawWindow) {
            $sawWindow = @(Get-TargetProcesses reaper |
                           Where-Object { $_.MainWindowHandle -ne 0 }).Count -gt 0
            if ($sawWindow) { Write-Info "REAPER's window is up." }
        }

        # A window is the better signal, but not a requirement. REAPER can sit
        # behind a modal first-run dialog that reports no main window handle, and
        # waiting the full timeout for a handle that is never coming would be a
        # worse outcome than proceeding on the file alone.
        $iniIsOld = (Test-Path $ini) -and
                    (((Get-Date) - (Get-Item $ini).LastWriteTime).TotalSeconds -gt 30)

        if ((Test-Path $ini) -and ($sawWindow -or $iniIsOld)) {
            if (-not $sawWindow) {
                Write-Info "No main window reported, but the configuration has settled."
            }
            # Let the startup writes settle before ending the process.
            Start-Sleep -Seconds 4
            Write-Ok "REAPER created its configuration."
            [void](Request-AppClosed -Kind reaper -Label 'REAPER' -GraceSeconds 10 -AllowForce)
            # REAPER writes reaper.ini again on exit, so give that write time to
            # land, then make sure it did not come back up - a splash screen or
            # a queued restart would otherwise still be holding the file.
            #
            # -Only reaper. This used to sweep Claude too, which is how a Claude
            # still being created by its own installer got force-killed.
            Start-Sleep -Seconds 2
            [void](Confirm-AppsClosed -AllowForce -Only reaper)
            return (Test-Path $ini)
        }
    }

    Write-Warn2 "REAPER has not finished starting after $TimeoutSeconds seconds."
    Write-Host "  Click through whatever dialog it is showing, then press Enter here." -ForegroundColor Yellow
    [void](Read-Host)
    [void](Request-AppClosed -Kind reaper -Label 'REAPER' -GraceSeconds 10 -AllowForce)
    Start-Sleep -Seconds 2
    [void](Confirm-AppsClosed -AllowForce -Only reaper)
    return (Test-Path $ini)
}

function Wait-ClaudeSettled {
    <#
      Claude's installer opens Claude when it finishes. Let that first launch
      finish on its own terms before anything else in the setup touches it.

      A brand-new Claude spends its first seconds building a profile, including
      Chromium's Local Storage - a LevelDB. Anything that ends the process
      during that leaves a store it cannot read afterwards, and because no
      account has been attached yet there is no session to fall back on. Claude
      then fails to start, every single time, and the fix is deleting a folder
      the user has never heard of.

      So this waits for it to appear, gives the profile write a head start, then
      asks it to close the same way the X button does - and waits properly.

      It never forces. That is the entire point: if Claude will not close, the
      right answer is to leave it running and let the sign-in step below deal
      with it, not to end a process that is still writing. Returning $false is
      not a failure, it is "still open, handle it downstream".
    #>
    param([int]$AppearSeconds = 20, [int]$GraceSeconds = 45)

    if (Test-SelfHostedBy 'claude') { return $true }

    # It may already be up, or the installer may still be getting to it.
    $deadline = (Get-Date).AddSeconds($AppearSeconds)
    while ((Get-Date) -lt $deadline -and @(Get-TargetProcesses 'claude').Count -eq 0) {
        Start-Sleep -Milliseconds 500
    }

    $procs = @(Get-TargetProcesses 'claude')
    if ($procs.Count -eq 0) { return $true }

    Write-Info "Claude opened itself after installing. Letting it finish setting up..."
    Start-Sleep -Seconds 5
    foreach ($p in $procs) {
        try { if ($p.MainWindowHandle -ne 0) { [void]$p.CloseMainWindow() } } catch { }
    }

    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (@(Get-TargetProcesses 'claude').Count -eq 0) {
            Write-Ok "Claude closed cleanly; its profile is intact."
            return $true
        }
    }

    Write-Info "Claude is still open. Leaving it alone rather than ending it -"
    Write-Info "a new Claude stopped mid-setup cannot start again afterwards."
    return $false
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
    Write-Host "  ----------------------------------------------------" -ForegroundColor Yellow
    Write-Host "   SIGN IN TO CLAUDE in the window that just opened." -ForegroundColor Yellow
    Write-Host "  ----------------------------------------------------" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  This is the only thing the setup needs from you." -ForegroundColor Gray
    Write-Host "  It continues on its own the moment you are signed in -" -ForegroundColor Gray
    Write-Host "  nothing to press here." -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Claude was just installed, so it has no account yet, and" -ForegroundColor Gray
    Write-Host "  without one it cannot use the plugin at all." -ForegroundColor Gray
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

            # Claude must be allowed to shut down cleanly here, and this is the
            # one place where forcing it caused real damage: killing it moments
            # after a sign-in signed the user back OUT.
            #
            # Detecting the session in config.json proves the OAuth token
            # reached disk, but that is not the whole session. Electron keeps the
            # rest in Chromium's Local Storage, which is a LevelDB - and LevelDB
            # is exactly the kind of store that a SIGKILL mid-write leaves
            # corrupt. Claude then starts with no usable session and asks the
            # user to sign in again, undoing the step that just succeeded.
            #
            # So: give it time to flush, then ask politely and wait properly.
            # Forcing remains available, but only after half a minute of being
            # asked, by which point it is hung rather than busy.
            Write-Info "Letting Claude save its session before closing it..."
            Start-Sleep -Seconds 8
            [void](Request-AppClosed -Kind claude -Label 'Claude' -GraceSeconds 30 -AllowForce)
            # A tray process that survived is fine to end now - the session has
            # had its chance to flush, and this sweep is what stops Claude
            # holding the config file the setup writes next.
            Start-Sleep -Seconds 2
            [void](Confirm-AppsClosed -AllowForce -Only claude)

            if (-not (Test-ClaudeSignedIn)) {
                # Worth saying out loud rather than discovering later. If the
                # session did not survive the shutdown, the fix is simply to
                # sign in again once Claude reopens.
                Write-Warn2 "Claude's session did not survive the shutdown."
                Write-Info  "Sign in again when you next open Claude - everything else is done."
                return $false
            }
            Write-Ok "Session intact."
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
