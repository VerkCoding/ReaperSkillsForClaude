<#
.SYNOPSIS
  Health check for the REAPER for Claude plugin. Changes nothing.

.DESCRIPTION
  A Windows entry point for scripts\doctor.py, which holds the actual checks.

  This used to be a full PowerShell implementation. It was replaced by a wrapper
  when the plugin gained a POSIX installer: two health checks, one per platform,
  can disagree about what "working" means, and the one you happen to run tells
  you the setup is fine. Keeping the checks in Python means macOS and Linux
  users get exactly the same diagnosis, and there is one place to fix a wrong
  one.

  The filename is kept because the skills and the docs point at it.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File health-check.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File health-check.ps1 -ReaperResourcePath "E:\REAPER\Portable"
#>
[CmdletBinding()]
param(
    [string]$ReaperResourcePath,
    [switch]$SkipLive
)

$ErrorActionPreference = 'Continue'

$PluginRoot = Split-Path -Parent $PSScriptRoot
$Doctor     = Join-Path $PluginRoot 'scripts\doctor.py'

if (-not (Test-Path $Doctor)) {
    Write-Host "  [FAIL] Missing $Doctor - this install is incomplete." -ForegroundColor Red
    exit 1
}

# Run the check under an interpreter that can import the dependencies, not
# merely the first `python` on PATH. Otherwise the health check reports missing
# imports that the server itself would never have hit, and sends the user off to
# fix something that is not broken.
$Python = $null
$launcher = Join-Path $PluginRoot 'scripts\launch_server.py'
if ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-Path $launcher)) {
    # Select-Object -Last 1 because a native command yields an array when it
    # prints more than one line, and .Trim() on an array throws.
    $best = & python $launcher --self-test 2>$null | Select-Object -Last 1
    if ($LASTEXITCODE -eq 0 -and $best) { $Python = "$best".Trim() }
}
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $cmd) {
        Write-Host "  [FAIL] No Python on PATH, so the health check cannot run." -ForegroundColor Red
        Write-Host "         -> Install Python from python.org and tick 'Add Python to PATH'." -ForegroundColor Gray
        exit 1
    }
    $Python = $cmd.Source
}

# `py -3.12` comes back as two tokens; a path is one.
$pyArgs = @()
if ($Python -like 'py -*') {
    $parts  = $Python.Split(' ')
    $Python = $parts[0]
    $pyArgs += $parts[1]
}

$pyArgs += $Doctor
if ($ReaperResourcePath) { $pyArgs += @('--resource-path', $ReaperResourcePath) }
if ($SkipLive)           { $pyArgs += '--skip-live' }

& $Python @pyArgs
exit $LASTEXITCODE
