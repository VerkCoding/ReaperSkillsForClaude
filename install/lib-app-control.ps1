<#
  Shared helpers for finding, closing and first-running REAPER and Claude.

  This file is dot-sourced by install-everything.ps1.

  Process matching uses executable paths to prevent closing the Claude Code CLI, which shares the 'claude.exe' name with Claude Desktop. Closing the CLI could terminate the session executing the installer. This script's ancestry is excluded from process closure.

  Applications are closed using CloseMainWindow to trigger save prompts, preserving user data.
  Force-closing is used only during first-run steps, as the script initiated the application and no user project data exists to lose.
#>

# Set-StrictMode is omitted because this file is dot-sourced. Applying strict mode here would affect the entire installer. Strict mode treats missing registry keys as terminating errors, which conflicts with registry probing behavior.

# lib-console.ps1 is dot-sourced here to ensure Write-Ok, Write-Info, and Write-Warn2 are defined, preventing errors if a consumer loads this file independently.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

# ---------------------------------------------------------------------------
# The console
# ---------------------------------------------------------------------------

function Disable-ConsoleQuickEdit {
    <#
      Disables QuickEdit mode in the Windows console.

      QuickEdit mode halts console output when text selection is active, pausing script execution.
      This ensures REAPER and Claude processes are not left in indeterminate states due to paused timers.
      Changes apply only to the current console session.
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
      Re-reads PATH from the registry.

      Processes do not receive environment updates after startup. This updates the process PATH with new entries added by installers during execution, allowing installed applications to be located.
    #>
    $parts = @()
    foreach ($scope in 'Machine', 'User') {
        try {
            $v = [Environment]::GetEnvironmentVariable('Path', $scope)
            if ($v) { $parts += $v.Split(';') }
        } catch {
            # Registry read failures are bypassed; existing process PATH remains unmodified.
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
      Retrieves ancestor process IDs to prevent the script from terminating its own host process.
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

function Get-TargetProcesses {
    <#
      .PARAMETER Kind
        'reaper' - matches reaper.exe or reaper64.exe
        'claude' - matches Claude Desktop, excluding claude-code paths.
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
    <#
      Determines if the script is executing within the application targeted for closure.
    #>
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
      Verifies applications remain closed and closes any reopened instances.

      Applications may rewrite settings from memory on exit. A reopened application can discard newly written configuration data.
      Force-closing (-AllowForce) is restricted to applications specifically opened by the script during first-run steps, identified via -Only.
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
        Write-Warn2 "$label reopened. Closing."
        if (-not (Request-AppClosed -Kind $kind -Label $label -AllowForce:$AllowForce)) {
            $allClosed = $false
        }
    }
    return $allClosed
}

function Request-AppClosed {
    <#
      Attempts to close the specified application.

      CloseMainWindow is sent to trigger application save prompts.
      If -AllowForce is provided, the process is terminated to bypass splash screens or dialogs during first-run steps.
      Returns $true if the application processes are terminated.
    #>
    param(
        [ValidateSet('reaper', 'claude')] [string]$Kind,
        [string]$Label,
        [int]$GraceSeconds = 15,
        [switch]$AllowForce
    )

    if (Test-SelfHostedBy $Kind) {
        Write-Warn2 "$Label is hosting the installer. It will not be closed."
        if ($Kind -eq 'claude') {
            Write-Info "Configuration may be overwritten on application exit. Restart application and re-run option [1] if needed."
        }
        return $false
    }

    for ($attempt = 1; $attempt -le 3; $attempt++) {
        $procs = @(Get-TargetProcesses $Kind)
        if ($procs.Count -eq 0) {
            Write-Ok "$Label closed."
            return $true
        }

        Write-Info "Requesting $Label closure ($($procs.Count) process(es))..."
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
            # Stop-Process -Force terminates specific process IDs, preventing termination of the Claude Code CLI.
            # Repeated sweeping ensures helper processes and tray icons are terminated.
            for ($sweep = 1; $sweep -le 4; $sweep++) {
                $doomed = @(Get-TargetProcesses $Kind)
                if ($doomed.Count -eq 0) { break }

                if ($sweep -eq 1) {
                    Write-Warn2 "$Label did not close. Terminating ($($doomed.Count) process(es))."
                } else {
                    Write-Info "Sweep ${sweep}: $($doomed.Count) processes running."
                }

                foreach ($p in $doomed) {
                    try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch { }
                }
                Start-Sleep -Milliseconds 1200
            }

            $left = @(Get-TargetProcesses $Kind).Count
            if ($left -eq 0) {
                Write-Ok "$Label closed."
                return $true
            }
            Write-Warn2 "$Label has $left processes running. Proceeding."
            return $false
        }

        Write-Warn2 "$Label remains open. A prompt may be active."
        Write-Host  "  Close $Label, then press Enter." -ForegroundColor Yellow
        [void](Read-Host)
        # Next iteration handles windows reopened during the prompt wait.
    }

    if (@(Get-TargetProcesses $Kind).Count -eq 0) {
        Write-Ok "$Label closed."
        return $true
    }

    # The application configuration step may be skipped because settings are rewritten from memory on exit.
    Write-Warn2 "Proceeding with $Label open."
    if ($Kind -eq 'reaper') {
        Write-Info "REAPER connection settings skipped. Close REAPER and run option [1]."
    } else {
        Write-Info "Claude MCP server entry may be reverted. If missing, close Claude and run option [1]."
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
    # DisplayName property is fetched directly to avoid strict mode errors for missing keys.
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
      Starts Claude Desktop.

      Attempts executable launch first, then falls back to MSIX AppsFolder shell path.
      Returns $true if a launch is attempted.
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
      Provides the list of applications in dependency order.

      Stored centrally to avoid desynchronization between setup and cache generation scripts.
      Safe=$true indicates existing installations should not be modified.
    #>
    param([string]$PythonCustom)

    @(
        # Microsoft.VCRedist is required because python extensions (e.g., soxr via librosa) require the C++ runtime.
        # Listed first so the runtime is present before Python and before the server self-test that loads those extensions.
        [pscustomobject]@{ Id = 'Microsoft.VCRedist.2015+.x64'; Name = 'Visual C++ runtime (x64)'; Safe = $true; Custom = $null }
        # The x86 runtime is retained for potential 32-bit integrations. It is inexpensive and keeps the offline cache complete.
        [pscustomobject]@{ Id = 'Microsoft.VCRedist.2015+.x86'; Name = 'Visual C++ runtime (x86)'; Safe = $true; Custom = $null }
        [pscustomobject]@{ Id = 'Python.Python.3.12';   Name = 'Python 3.12';     Safe = $false; Custom = $PythonCustom }
        [pscustomobject]@{ Id = 'Git.Git';              Name = 'Git';             Safe = $false; Custom = $null }
        [pscustomobject]@{ Id = 'Cockos.REAPER';        Name = 'REAPER';          Safe = $true;  Custom = $null }
        [pscustomobject]@{ Id = 'Anthropic.Claude';     Name = 'Claude Desktop';  Safe = $true;  Custom = $null }
        [pscustomobject]@{ Id = 'Anthropic.ClaudeCode'; Name = 'Claude Code';     Safe = $true;  Custom = $null }
    )
}

function Test-AppPresent {
    <#
      Checks if an application is installed.

      Performs local path and registry checks instead of using winget, to support offline installations.
      Checks are conservative to ensure existing installations are identified correctly.
    #>
    param([string]$Id)

    switch ($Id) {
        'Microsoft.VCRedist.2015+.x64' {
            # Validates the presence of msvcp140.dll, as the runtime may be installed via various methods.
            return (Test-Path (Join-Path $env:SystemRoot 'System32\msvcp140.dll'))
        }
        'Microsoft.VCRedist.2015+.x86' {
            # The 32-bit runtime lands in SysWOW64 on 64-bit Windows.
            return (Test-Path (Join-Path $env:SystemRoot 'SysWOW64\msvcp140.dll'))
        }
        'Cockos.REAPER'  { return [bool](Get-ReaperExe) }
        'Git.Git'        { return [bool](Get-Command git -ErrorAction SilentlyContinue) }
        'Python.Python.3.12' {
            # Checks for 'python' command availability.
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
      Retrieves existing Claude Desktop profile directories.

      Handles standard installations (%APPDATA%) and MSIX installations (LocalCache).
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
      Verifies the presence of a session in Claude configuration.

      Checks for lastKnownAccountUuid or tokenCache keys.
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
      Executes REAPER to generate initial configuration files (reaper.ini).

      Required for subsequent configuration modifications.
    #>
    param([string]$ReaperResourcePath, [int]$TimeoutSeconds = 180)

    $ini = Join-Path $ReaperResourcePath 'reaper.ini'
    if (Test-Path $ini) {
        Write-Ok "REAPER configuration exists."
        return $true
    }

    $exe = Get-ReaperExe
    if (-not $exe) {
        Write-Warn2 "reaper.exe not found."
        return $false
    }

    Write-Host ""
    Write-Host "  Starting REAPER to generate settings." -ForegroundColor Yellow
    Write-Host "  Dismiss any dialogs. The application will close automatically." -ForegroundColor Gray
    Write-Host ""
    try { Start-Process $exe } catch {
        Write-Warn2 "Failed to start REAPER: $_"
        return $false
    }

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $sawWindow = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1

        # MainWindowHandle checks are used to ensure the application has fully launched before closing.
        if (-not $sawWindow) {
            $sawWindow = @(Get-TargetProcesses reaper |
                           Where-Object { $_.MainWindowHandle -ne 0 }).Count -gt 0
            if ($sawWindow) { Write-Info "REAPER window detected." }
        }

        # Fallback logic handles modal dialogs that do not report a main window handle.
        $iniIsOld = (Test-Path $ini) -and
                    (((Get-Date) - (Get-Item $ini).LastWriteTime).TotalSeconds -gt 30)

        if ((Test-Path $ini) -and ($sawWindow -or $iniIsOld)) {
            if (-not $sawWindow) {
                Write-Info "No main window detected; configuration file timeout reached."
            }
            # Wait for initial writes to complete.
            Start-Sleep -Seconds 4
            Write-Ok "REAPER configuration generated."
            [void](Request-AppClosed -Kind reaper -Label 'REAPER' -GraceSeconds 10 -AllowForce)
            # Wait for exit configuration writes to complete and verify process termination.
            Start-Sleep -Seconds 2
            [void](Confirm-AppsClosed -AllowForce -Only reaper)
            return (Test-Path $ini)
        }
    }

    Write-Warn2 "REAPER startup timed out ($TimeoutSeconds seconds)."
    Write-Host "  Dismiss any dialogs and press Enter." -ForegroundColor Yellow
    [void](Read-Host)
    [void](Request-AppClosed -Kind reaper -Label 'REAPER' -GraceSeconds 10 -AllowForce)
    Start-Sleep -Seconds 2
    [void](Confirm-AppsClosed -AllowForce -Only reaper)
    return (Test-Path $ini)
}

function Wait-ClaudeSettled {
    <#
      Waits for initial Claude startup to complete.

      Allows initial profile creation and Local Storage database initialization to finish before terminating the process, preventing database corruption.
      Returns $false if the application remains open.
    #>
    param([int]$AppearSeconds = 20, [int]$GraceSeconds = 45)

    if (Test-SelfHostedBy 'claude') { return $true }

    $deadline = (Get-Date).AddSeconds($AppearSeconds)
    while ((Get-Date) -lt $deadline -and @(Get-TargetProcesses 'claude').Count -eq 0) {
        Start-Sleep -Milliseconds 500
    }

    $procs = @(Get-TargetProcesses 'claude')
    if ($procs.Count -eq 0) { return $true }

    Write-Info "Claude started. Waiting for initialization..."
    Start-Sleep -Seconds 5
    foreach ($p in $procs) {
        try { if ($p.MainWindowHandle -ne 0) { [void]$p.CloseMainWindow() } } catch { }
    }

    $deadline = (Get-Date).AddSeconds($GraceSeconds)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 500
        if (@(Get-TargetProcesses 'claude').Count -eq 0) {
            Write-Ok "Claude closed."
            return $true
        }
    }

    Write-Info "Claude remains open. Proceeding."
    return $false
}

function Invoke-ClaudeFirstRun {
    <#
      Starts Claude to allow user authentication.

      Authentication is required for plugin operation.
    #>
    param(
        [int]$TimeoutSeconds = 600,
        # Generous timeout provided for application launch.
        [int]$AppearSeconds = 45
    )

    if (Test-ClaudeSignedIn) {
        Write-Ok "Claude session detected."
        return $true
    }

    Write-Info "Starting Claude for authentication..."
    if (-not (Start-ClaudeDesktop)) {
        Write-Warn2 "Claude executable not found. Start application and authenticate manually."
    }

    Write-Host ""
    Write-Host "  ----------------------------------------------------" -ForegroundColor Yellow
    Write-Host "   AUTHENTICATE IN CLAUDE" -ForegroundColor Yellow
    Write-Host "  ----------------------------------------------------" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Process will resume automatically upon authentication." -ForegroundColor Gray
    Write-Host ""

    $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
    $launchedAt = Get-Date
    $sawRunning = $false
    $ticks      = 0

    # Implements bounds for prompt retries and reopen attempts.
    $reopens     = 0
    $maxReopens  = 3
    $refusals    = 0
    $maxRefusals = 6

    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 1000
        $ticks++

        if (Test-ClaudeSignedIn) {
            Write-Host ""
            Write-Ok "Claude authenticated."

            # Allows process time to flush session data to Chromium Local Storage before termination to prevent database corruption.
            Write-Info "Waiting for session data write..."
            Start-Sleep -Seconds 8
            [void](Request-AppClosed -Kind claude -Label 'Claude' -GraceSeconds 30 -AllowForce)
            # Terminate remaining tray processes.
            Start-Sleep -Seconds 2
            [void](Confirm-AppsClosed -AllowForce -Only claude)

            if (-not (Test-ClaudeSignedIn)) {
                # Indicates if session data was not persisted during shutdown.
                Write-Warn2 "Session data write failed."
                Write-Info  "Re-authenticate upon next Claude launch."
                return $false
            }
            Write-Ok "Session verified."
            return $true
        }

        # Requires explicit user input to bypass authentication.

        # Checks process status and elapsed time to detect stalled state.
        $running = @(Get-TargetProcesses 'claude').Count -gt 0
        $stall   = $null
        if ($running) {
            $sawRunning = $true
        } elseif ($sawRunning) {
            $stall = "Claude process terminated before authentication."
        } elseif (((Get-Date) - $launchedAt).TotalSeconds -gt $AppearSeconds) {
            $stall = "Claude launch timeout."
        }

        if ($stall) {
            Write-Host ""
            Write-Warn2 $stall

            # Bypassing authentication requires explicit string input.
            if ($reopens -lt $maxReopens) {
                Write-Host "    [Enter] Retry launch" -ForegroundColor Gray
                Write-Host "    SKIP    Bypass authentication" -ForegroundColor Gray
            } else {
                Write-Warn2 "Retry limit reached ($maxReopens)."
                Write-Host "    [Enter] Retry launch" -ForegroundColor Gray
                Write-Host "    SKIP    Bypass authentication" -ForegroundColor Gray
            }

            # Enforces exact word match.
            $ans = Read-Host "  Input SKIP to bypass, or Enter to retry"
            if ("$ans".Trim() -eq 'SKIP') {
                Write-Host ""
                Write-Warn2 "Authentication bypassed."
                Write-Info  "Plugin requires manual authentication."
                return $false
            }

            $refusals++
            if ($refusals -ge $maxRefusals) {
                # Bounded retry loop.
                Write-Host ""
                Write-Warn2 "Authentication failed after $maxRefusals attempts."
                Write-Info  "Authenticate manually."
                return $false
            }

            $reopens++
            if (Start-ClaudeDesktop) {
                $sawRunning = $false
                $launchedAt = Get-Date
                $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
                Write-Info "Waiting..."
                continue
            }
            Write-Warn2 "Claude launch failed. Authenticate manually."
            $sawRunning = $false
            $launchedAt = Get-Date
            $deadline   = (Get-Date).AddSeconds($TimeoutSeconds)
        }

        if ($ticks % 30 -eq 0) {
            Write-Host "  Waiting for authentication..." -ForegroundColor DarkGray
        }
    }

    Write-Warn2 "Authentication timeout ($([int]($TimeoutSeconds / 60)) minutes)."
    Write-Info  "Authenticate manually."
    return $false
}
