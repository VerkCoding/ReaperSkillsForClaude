<#
.SYNOPSIS
  Executes the installation process for REAPER and Claude requirements.

.DESCRIPTION
  Performs installation in dependency order:
    1. Creates file snapshot for revert operations.
    2. Verifies winget availability.
    3. Installs Python, Git, REAPER, Claude Desktop, and Claude Code.
    4. Configures dependencies, REAPER bridge, distant API, and Claude.
    5. Performs health check.

  REAPER and Claude are installed only if absent. Existing installations are not modified to prevent interference with existing application state and user data.
  Python and Git are installed or upgraded as standard developer tools.
  All packages are sourced from the winget community repository to utilize vendor-provided installers.

  Application installations route through downloadCache. Packages present in the cache are installed locally. Packages not present are fetched using `winget download` prior to local installation. This design prevents redundant downloads across multiple executions or environments, mitigating rate-limiting risks.

  If the cache is unavailable, installation defaults to standard `winget install` behavior. Offline preparation is supported via lib-download-cache.ps1.

.PARAMETER SkipApps
  Configures the plugin without executing application installations.

.PARAMETER Force
  Bypasses the application running state warning.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-everything.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipApps,
    [switch]$Force,
    [switch]$Confirmed,
    [string]$ReaperResourcePath
)

$ErrorActionPreference = 'Stop'

# Ensures the RFC_LOG_PATH environment variable is removed if execution terminates early. This prevents subsequent scripts from appending data to an orphaned log file when run in the same caller process.
trap { Remove-Item Env:\RFC_LOG_PATH -ErrorAction SilentlyContinue; break }

# Includes required library functions for console output and logging.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

$script:Problems = @()
function Add-Problem($m) { $script:Problems += $m }

# Stores pre-existing application states to determine required first-run actions post-installation.
$script:WasPresent = @{}

$Here       = $PSScriptRoot
$PluginRoot = Split-Path -Parent $Here

# Dot-sourcing integrates external functions into the current scope to utilize shared variables and standard output functions.
. (Join-Path $Here 'lib-app-control.ps1')
. (Join-Path $Here 'lib-download-cache.ps1')

# Determines if the Python installation should modify the system PATH variable.
# Modifying PATH is avoided when a compatible Python version exists, to prevent altering the environment for other applications. The MCP server, REAPER, and configuration scripts utilize specific Python paths or the virtual environment. A system PATH modification is only required when no compatible version (>= 3.8) is present to ensure the initial launch script executes successfully.
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
    Write-Info "Compatible Python detected on PATH. PATH modification omitted."
    'InstallAllUsers=0 Include_test=0'
} else {
    Write-Info "Compatible Python not found on PATH. PATH modification enabled."
    'PrependPath=1 InstallAllUsers=0 Include_test=0'
}

# Retrieves the application installation sequence. Shared with cache generation scripts to ensure consistency.
$Apps = Get-AppList -PythonCustom $pythonCustom

# Initializes transcript logging to capture raw standard output and standard error from native commands.
# This operates independently of the structured step log to ensure complete output capture without intermingling log formats. Filenames include timestamps to prevent concurrent execution conflicts. A retention policy limits the number of stored transcript files.
$LogDir = Join-Path $env:USERPROFILE '.reaper-for-claude\logs'
$LogFile = Join-Path $LogDir ("transcript-{0}.log" -f (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
$transcribing = $false
try {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    Start-Transcript -Path $LogFile -Force | Out-Null
    $transcribing = $true
    Get-ChildItem $LogDir -Filter 'transcript-*.log' -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -Skip 10 |
        ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }
} catch {
}

# Disables ConsoleQuickEdit to prevent unintended script pauses caused by user mouse interactions.
[void](Disable-ConsoleQuickEdit)

$stepLog = Start-RunLog 'setup'
# Exports the log path to an environment variable to allow child script scopes to append to the parent log file.
if ($stepLog) { $env:RFC_LOG_PATH = $stepLog }
Write-Banner "REAPER for Claude installation initialized."
Write-Info "plugin      $PluginRoot"
if ($stepLog)      { Write-Info "log         $stepLog" }
if ($transcribing) { Write-Info "transcript  $LogFile" }

# Validates application termination to prevent external state overwrites during configuration modification.
Write-Step "Terminating REAPER and Claude processes."

# Prevents redundant user prompts if confirmation was provided prior to execution.
if (-not $Confirmed) {
    Write-Host ""
    Write-Host "  Save active data in REAPER and Claude." -ForegroundColor Yellow
    Write-Host "  Applications will be terminated during execution." -ForegroundColor Gray
    Write-Host ""
    Write-Host "  Press Enter to proceed or Ctrl+C to cancel." -ForegroundColor Yellow
    [void](Read-Host)
}

$reaperClosed = Request-AppClosed -Kind reaper -Label 'REAPER'

# The return value for Claude is discarded as its execution state is validated separately during plugin configuration steps.
[void](Request-AppClosed -Kind claude -Label 'Claude')

if (-not $reaperClosed) {
    Add-Problem "REAPER process remained active. Configuration modifications may fail. Terminate process and retry."
}

# Executes system snapshot to enable rollback capabilities prior to system modifications.
Write-Step "Generating configuration backup."
try {
    # Utilizes a hashtable for splatting to enforce named parameter binding and prevent positional parameter errors.
    $snapArgs = @{ Backup = $true }
    if ($ReaperResourcePath) { $snapArgs['ReaperResourcePath'] = $ReaperResourcePath }
    # Suppresses success stream output to avoid duplicate path logging in transcripts, as backup-restore.ps1 writes status updates independently.
    & (Join-Path $Here 'backup-restore.ps1') @snapArgs | Out-Null
    Write-Info "Rollback functionality enabled."
} catch {
    Write-Err "Snapshot creation failed: $_"
    Add-Problem "Backup failure. Rollback capabilities will be unavailable."
}

if ($SkipApps) {
    Write-Step "Applications"
    Write-Warn2 "Application installation bypassed via parameter."
} else {
    Write-Step "Package manager"
    $haveWinget = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try { $null = & winget --version 2>&1; $haveWinget = ($LASTEXITCODE -eq 0) } catch { }
    }

    if ($haveWinget) {
        Write-Ok "winget version $(& winget --version) confirmed."
    } else {
        # Executes direct deployment of winget packages to resolve standard dependency requirements when winget is absent. This method operates independently of external repository providers.
        Write-Warn2 "winget missing. Initiating installation."
        try {
            & (Join-Path $Here 'install-winget.ps1') -Embedded | Out-Host
        } catch {
            Write-Warn2 "winget installation failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        }

        # Re-evaluates system paths post-installation to detect command availability in the current session context.
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
        if ($haveWinget) { Write-Ok "winget version $(& winget --version) initialized." }
    }

    Write-Step "Applications"

    # Evaluates cache availability prior to deployment routines to minimize redundant network transfer operations and prevent bandwidth threshold violations.
    if (Get-CacheDir) {
        Write-Info "downloadCache detected. Local installations enabled."
    }
    if (-not $haveWinget) {
        Write-Warn2 "winget absent. Installation limited to downloadCache and direct python.org sources."
    }

    # Accumulates missing manual installations for consolidated error reporting.
    $manual = @()

    foreach ($app in $Apps) {
        # Isolates application installations within try/catch blocks to ensure process continuation upon individual component failure.
        try {
            # Evaluates existing installation status to dictate installation operations and determine execution of first-run procedures later in the sequence. Fallback detection mechanism utilized when winget is absent.
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

            $script:WasPresent[$app.Id] = $installed

            if ($installed -and $app.Safe) {
                Write-Ok "$($app.Name) existing installation verified."
                continue
            }

            if ($installed) {
                if (-not $haveWinget) {
                    # Upgrade operations are unsupported without winget to prevent unintended version downgrades from static cache sources.
                    Write-Ok "$($app.Name) existing installation verified."
                    continue
                }
                Write-Info "Checking upgrade availability for $($app.Name)."
            } else {
                Write-Info "Initiating $($app.Name) deployment."

                # Routes all payload acquisitions through downloadCache to retain binary assets across multiple test executions and mitigate redundant transfers.
                $pkg       = Find-CachedPackage -Id $app.Id
                $fromCache = $false

                if ($pkg -and $pkg.Installer) {
                    Write-Info ("  Installing {0} {1} from local cache." -f $app.Name, $pkg.Version)
                    $fromCache = Install-CachedPackage -Package $pkg -ExtraArgs $app.Custom
                    if (-not $fromCache) { Write-Warn2 "  Local installation failed. Initiating fallback procedure." }
                } elseif ($pkg) {
                    # Bypasses local installation attempts for packages previously identified as incompatible with offline execution.
                    Write-Info "  Package requires direct winget execution. Bypassing cache."
                } elseif ($haveWinget) {
                    Write-Info "  Downloading package to local cache."
                    $pkg = Get-PackageToCache -Id $app.Id
                    if ($pkg) {
                        $fromCache = Install-CachedPackage -Package $pkg -ExtraArgs $app.Custom
                        if (-not $fromCache) { Write-Warn2 "  Installation failed. Reverting to standard winget execution." }
                    }
                }

                if ($fromCache) {
                    Write-Ok "$($app.Name) deployed from local cache."
                    continue
                }
            }

            if (-not $haveWinget) {
                if ($app.Id -like 'Python.Python.*') {
                    # Utilizes direct python.org acquisition to circumvent winget dependencies, ensuring base required component availability.
                    Write-Info "Executing direct Python installation."
                    # Evaluates process exit codes directly as the child script communicates failure states via non-zero exit codes rather than exceptions.
                    $pyOk = $false
                    try {
                        & (Join-Path $Here 'install-python.ps1') -Version '3.12.10' | Out-Host
                        $pyOk = ($LASTEXITCODE -eq 0)
                    } catch {
                        Write-Err "Python installation failure: $_"
                    }
                    if ($pyOk) {
                        Write-Ok "$($app.Name) deployed."
                    } else {
                        Write-Err "$($app.Name) deployment failed."
                        Add-Problem "Manual Python installation required. Ensure 'Add python.exe to PATH' is selected."
                    }
                } else {
                    Write-Warn2 "$($app.Name) deployment unavailable."
                    $manual += $app.Name
                }
                continue
            }

            # Configures precise winget parameters to avoid identifier collisions and restrict sourcing to vendor distributions.
            $wargs = @(
                'install', '-e', '--id', $app.Id, '--source', 'winget',
                '--accept-package-agreements', '--accept-source-agreements'
            )
            if ($app.Custom) { $wargs += @('--custom', $app.Custom) }

            # Implements retry logic for transient network failures during package retrieval.
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                for ($attempt = 1; $attempt -le 2; $attempt++) {
                    & winget @wargs
                    $code = $LASTEXITCODE
                    if ($code -eq 0 -or $code -eq -1978335189) { break }
                    if ($attempt -eq 1) {
                        Write-Warn2 ("  {0} failure (exit {1}). Retrying." -f $app.Name, $code)
                        Start-Sleep -Seconds 5
                    }
                }
            } finally {
                $ErrorActionPreference = $prevEAP
            }

            # Exit code -1978335189 indicates a current installation state and is parsed as a success condition.
            if ($code -eq 0 -or $code -eq -1978335189) {
                Write-Ok "$($app.Name) deployed."
            } else {
                Write-Err "$($app.Name): winget termination code $code."
                Add-Problem "$($app.Name) deployment failed (exit $code). Manual installation required."
            }
        } catch {
            Write-Err "$($app.Name): $($_.Exception.Message)"
            Add-Problem "$($app.Name) installation failure. Refer to error output. Execution continuing."
        }
    }

    if ($manual.Count -gt 0) {
        Write-Host ""
        Write-Info "Manual installation required for the following components:"
        Write-Info "    REAPER  https://www.reaper.fm/download.php"
        Write-Info "    Claude  https://claude.ai/download"
        Write-Info "Execute [3] Prepare Offline Files on a networked machine to generate a cache directory."
        Write-Info "Rerun [1] to resume installation."
        Add-Problem ("Manual installation required for {0}." -f ($manual -join ' and '))
    }

    # Refreshes process environment variables from registry configurations to enable immediate access to newly installed binaries.
    Update-PathFromRegistry

    # Appends specific application directories to the session PATH to mitigate missing registry configurations from non-standard installer behaviors.
    $newPaths = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\Scripts'),
        (Join-Path $env:ProgramFiles 'Python312'),
        (Join-Path $env:ProgramFiles 'Git\cmd'),
        (Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Links')
    )

    # Scans for Claude Code directory paths containing dynamic hash values to enable subsequent plugin registration steps.
    $pkgRoot = Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'
    if (Test-Path $pkgRoot) {
        Get-ChildItem $pkgRoot -Directory -Filter 'Anthropic.ClaudeCode*' -ErrorAction SilentlyContinue |
            ForEach-Object { $newPaths += $_.FullName }
    }

    foreach ($p in $newPaths) {
        if ((Test-Path $p) -and ($env:PATH -notlike "*$p*")) { $env:PATH = "$p;$env:PATH" }
    }
}

# Executes initial application runs to populate required configuration files and sessions for newly installed components. Excludes pre-existing installations to preserve user state.
if (-not $SkipApps) {
    $reaperWasNew = ($script:WasPresent.ContainsKey('Cockos.REAPER') -and -not $script:WasPresent['Cockos.REAPER'])
    $claudeWasNew = ($script:WasPresent.ContainsKey('Anthropic.Claude') -and -not $script:WasPresent['Anthropic.Claude'])

    if ($reaperWasNew -or $claudeWasNew) {
        Write-Step "Initial execution"

        # Enforces orderly termination of Claude's post-installation auto-launch to ensure configuration profiles are written to disk prior to subsequent operations.
        if ($claudeWasNew) { [void](Wait-ClaudeSettled) }

        if ($reaperWasNew) {
            $resource = if ($ReaperResourcePath) { $ReaperResourcePath } else { Join-Path $env:APPDATA 'REAPER' }
            if (-not (Invoke-ReaperFirstRun -ReaperResourcePath $resource)) {
                Add-Problem "REAPER configuration missing. Manual execution required. Rerun [1] post-execution."
            }
        }

        if ($claudeWasNew) {
            if (-not (Invoke-ClaudeFirstRun)) {
                Add-Problem "Claude authentication pending. Sign-in required for plugin functionality."
            }
        }
    }
}

# Validates running states prior to plugin configuration to prevent application overrides of newly written configuration files.
[void](Confirm-AppsClosed -Because "Validating process state prior to configuration write operations.")

Write-Step "Plugin configuration"
# Utilizes hashtable parameters to enforce correct mapping within the target script.
$installArgs = @{}
if ($ReaperResourcePath) { $installArgs['ReaperResourcePath'] = $ReaperResourcePath }
if ($Force)              { $installArgs['Force'] = $true }

# Redirects child script error reporting to a temporary file for unified consolidation within the parent summary.
$problemsFile = Join-Path $env:TEMP ("rfc-problems-" + [Guid]::NewGuid().ToString("N").Substring(0, 8) + ".txt")
$installArgs['ProblemsOut'] = $problemsFile

try {
    # Modifies error preferences temporarily to prevent standard error streams from native commands from aborting execution.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & (Join-Path $Here 'configure-plugin.ps1') @installArgs | Out-Host
    } finally {
        $ErrorActionPreference = $prevEAP
    }
} catch {
    Write-Err "Plugin configuration failure: $_"
    Add-Problem "Execute configuration step independently after resolving errors."
}

if (Test-Path $problemsFile) {
    Get-Content $problemsFile -ErrorAction SilentlyContinue |
        Where-Object { $_.Trim() } | ForEach-Object { Add-Problem $_ }
    Remove-Item $problemsFile -Force -ErrorAction SilentlyContinue
}

# Executes self-test diagnostic to validate server launch capabilities and dependency integrity.
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
    Write-Err  "Server launch failure detected."
    Write-Info "Application connection will fail. Execute dependency rebuild:"
    Write-Host ""
    Write-Host "        python `"$PluginRoot\scripts\bootstrap.py`"" -ForegroundColor White
    Write-Host ""
    if ($transcribing) { Write-Info "Diagnostic information appended to $LogFile" }
    Add-Problem "Server execution failed. Execute scripts\bootstrap.py to reconstruct environment."
}

Write-Result -Problems $script:Problems

if ($stepLog)      { Write-Info "log         $stepLog" }
if ($transcribing) {
    Write-Info "transcript  $LogFile"
    try { Stop-Transcript | Out-Null } catch { }
}
Write-Host ""

# Removes logging environment variables to prevent output contamination across independent script executions.
Remove-Item Env:\RFC_LOG_PATH -ErrorAction SilentlyContinue

# Returns process termination code indicating composite execution status.
exit $(if ($script:Problems.Count -eq 0) { 0 } else { 1 })
