<#
.SYNOPSIS
  Executes health checks for the REAPER for Claude plugin.

.DESCRIPTION
  Wrapper script that executes scripts\doctor.py.
  A single cross-platform Python script prevents diagnosis discrepancies between platforms.
  The ps1 file is maintained for backward compatibility with existing documentation.

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
    Write-Host "Error: Missing file $Doctor." -ForegroundColor Red
    exit 1
}

# Python interpreter selection prioritizes the server environment to ensure dependency availability matches runtime conditions.
$Python = $null
$launcher = Join-Path $PluginRoot 'scripts\launch_server.py'
if ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-Path $launcher)) {
    $best = & python $launcher --self-test 2>$null | Select-Object -Last 1
    if ($LASTEXITCODE -eq 0 -and $best) { $Python = "$best".Trim() }
}
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python3 -ErrorAction SilentlyContinue }
    if (-not $cmd) {
        Write-Host "Error: Python interpreter not found on system PATH." -ForegroundColor Red
        Write-Host "Resolution: Install Python and append to PATH." -ForegroundColor Gray
        exit 1
    }
    $Python = $cmd.Source
}

# The py launcher syntax requires arguments to be passed as separate array elements to be interpreted correctly.
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
