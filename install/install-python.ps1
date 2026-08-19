<#
.SYNOPSIS
  Install Python via winget or direct download.

.DESCRIPTION
  Provides winget or direct download installation of Python. Winget is unavailable
  in Windows Server and Windows Sandbox environments. The script defaults to 
  direct download when winget is inaccessible.
  Direct download from python.org uses the same installer as winget.
  Installation is scoped per-user to avoid elevation requirements.

.PARAMETER Version
  Python version to install. Defaults to 3.12 for compatibility with numba and llvmlite dependencies.

.PARAMETER Force
  Install even if a working Python is already on PATH.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-python.ps1
#>
[CmdletBinding()]
param(
    [string]$Version = '3.12.10',
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
# Disabling progress bars in PS 5.1 prevents download performance degradation.
$ProgressPreference    = 'SilentlyContinue'

# Load shared console functions.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

function Test-Python {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { return $null }
    try {
        $v = & python --version 2>&1
        if ($LASTEXITCODE -eq 0) { return "$v" }
    } catch { }
    return $null
}

function Update-SessionPath {
    # PATH must be prepended for the current session. Reading from registry 
    # retrieves unexpanded REG_EXPAND_SZ values which can break subsequent commands.
    $short = 'Python' + ($Version -split '\.')[0] + ($Version -split '\.')[1]
    $roots = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\$short"),
        (Join-Path $env:ProgramFiles $short)
    )
    foreach ($r in $roots) {
        if (Test-Path (Join-Path $r 'python.exe')) {
            $env:PATH = "$r;$r\Scripts;$env:PATH"
            return $true
        }
    }
    return $false
}

[void](Start-RunLog 'install-python')
Write-Banner "Python - install"

$existing = Test-Python
if ($existing -and -not $Force) {
    Write-Ok "$existing is installed and on PATH. Exiting."
    exit 0
}

# Winget execution test. The executable shim may be present but non-functional 
# if App Installer is improperly registered.
$wingetOk = $false
if (Get-Command winget -ErrorAction SilentlyContinue) {
    try {
        $null = & winget --version 2>&1
        $wingetOk = ($LASTEXITCODE -eq 0)
    } catch { $wingetOk = $false }
}

if ($wingetOk) {
    Write-Info "Using winget."
    # Exact ID matching prevents resolving to unintended legacy versions like Python 3.0.
    # --custom appends silent switches without removing default ones.
    # InstallAllUsers=0 avoids --scope user failures due to the burn bundle missing user-scope installer declarations.
    $major = ($Version -split '\.')[0..1] -join '.'
    & winget install -e --id "Python.Python.$major" --source winget `
        --accept-package-agreements --accept-source-agreements `
        --custom "PrependPath=1 InstallAllUsers=0 Include_test=0"

    $null = Update-SessionPath
    $now = Test-Python
    if ($now) {
        Write-Ok "$now installed."
        exit 0
    }
    Write-Warn2 "Python is not visible after winget installation. Attempting direct download."
} else {
    Write-Info "Winget is unavailable. Downloading from python.org."
}

# Direct download removes the dependency on PowerShell Gallery while retrieving the same installer.
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
$file = "python-$Version-$arch.exe"
$url  = "https://www.python.org/ftp/python/$Version/$file"
$dest = Join-Path $env:TEMP $file

try {
    # python.org requires TLS 1.2 or higher.
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Warn2 "Failed to set TLS 1.2."
}

Write-Info "Downloading $url"
try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
} catch {
    Write-Err "Download failed: $_"
    Write-Info "Manual installation URL: https://www.python.org/downloads/. Select 'Add python.exe to PATH'."
    exit 1
}

$size = (Get-Item $dest).Length
if ($size -lt 5MB) {
    # A proxy or captive portal response may have been saved instead of the executable.
    Write-Err "File size $([int]($size/1KB)) KB is below expected installer size."
    exit 1
}
Write-Ok "Downloaded $([int]($size/1MB)) MB."

Write-Info "Starting per-user installation."
$p = Start-Process -FilePath $dest -Wait -PassThru -ArgumentList @(
    '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0'
)
if ($p.ExitCode -ne 0) {
    Write-Err "The installer exited with code $($p.ExitCode)."
    Write-Info "Manual execution path: $dest"
    exit 1
}

Remove-Item $dest -ErrorAction SilentlyContinue

$null = Update-SessionPath
$now = Test-Python
if ($now) {
    Write-Ok "$now installed."
    Write-Host ""
    Write-Host "Restart the process using RunThisToStart.bat to apply the updated PATH." -ForegroundColor Gray
    exit 0
}

Write-Err "Python was installed but is not on PATH."
Write-Info "Restart the process using RunThisToStart.bat."
exit 1
