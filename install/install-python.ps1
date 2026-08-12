<#
.SYNOPSIS
  Install Python, with or without winget.

.DESCRIPTION
  winget is the pleasant path, not a requirement. It is missing on Windows
  Server, on images built without the Store, and inside Windows Sandbox, and
  repairing it depends on PowerShell Gallery - which fails on exactly the same
  machines, with:

      Unable to download from URI 'https://go.microsoft.com/fwlink/?LinkID=627338'

  That is the inbox PackageManagement module failing to fetch its provider list.
  Chasing it is a detour: the goal here is a working Python, and python.org
  serves the same installer winget would have downloaded. So this tries winget,
  and falls straight through to a direct download when it is unavailable or
  unhappy.

  Everything is per-user, so no elevation is needed.

.PARAMETER Version
  Python version to fetch directly. Defaults to 3.12, because numba and llvmlite
  - which librosa needs - routinely lag new Python releases by months.

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
$ProgressPreference    = 'SilentlyContinue'   # PS 5.1 progress bars make downloads several times slower

function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  .      $m" -ForegroundColor Gray }
function Write-Warn2($m){ Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

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
    # PATH is read once at process start, so an install from a moment ago is
    # invisible here. Only prepend the new directory - rebuilding PATH from the
    # registry returns REG_EXPAND_SZ values whose literal %SystemRoot% will not
    # be re-expanded, which breaks every later command in this session.
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

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  Install Python" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

$existing = Test-Python
if ($existing -and -not $Force) {
    Write-Ok "$existing is already installed and on PATH. Nothing to do."
    exit 0
}

# ---------------------------------------------------------------------------
# 1. winget, if it is genuinely working.
#    Present on PATH is not the same as working: a half-registered App Installer
#    leaves the shim behind and fails on every call.
# ---------------------------------------------------------------------------
$wingetOk = $false
if (Get-Command winget -ErrorAction SilentlyContinue) {
    try {
        $null = & winget --version 2>&1
        $wingetOk = ($LASTEXITCODE -eq 0)
    } catch { $wingetOk = $false }
}

if ($wingetOk) {
    Write-Info "Using winget."
    # -e forces an exact ID match: there is no Python.Python.3, and a loose
    # match on that string can resolve to Python 3.0.
    # --custom appends to winget's own silent switches; --override would replace
    # them. InstallAllUsers=0 rather than --scope user, because this is a burn
    # bundle with no user-scope installer declared and --scope user would fail.
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
    Write-Warn2 "winget finished but Python is still not visible; trying a direct download."
} else {
    Write-Info "winget is unavailable or not responding; downloading from python.org."
}

# ---------------------------------------------------------------------------
# 2. Direct from python.org. This is the same installer winget fetches - its
#    manifest points at exactly this URL - so nothing is lost by skipping the
#    middleman, and the whole PSGallery dependency disappears with it.
# ---------------------------------------------------------------------------
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
$file = "python-$Version-$arch.exe"
$url  = "https://www.python.org/ftp/python/$Version/$file"
$dest = Join-Path $env:TEMP $file

try {
    # Old Windows PowerShell still negotiates TLS 1.0, which python.org refuses.
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Warn2 "Could not force TLS 1.2; continuing."
}

Write-Info "Downloading $url"
try {
    Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
} catch {
    Write-Err "Download failed: $_"
    Write-Info "Check the connection, or install by hand from"
    Write-Info "https://www.python.org/downloads/ - tick 'Add python.exe to PATH'."
    exit 1
}

$size = (Get-Item $dest).Length
if ($size -lt 5MB) {
    # A proxy login page or an error body would land here as a small file that
    # then fails to execute with a meaningless error.
    Write-Err "The download is only $([int]($size/1KB)) KB - that is not the installer."
    Write-Info "A proxy or captive portal probably returned a page instead of the file."
    exit 1
}
Write-Ok "Downloaded $([int]($size/1MB)) MB."

Write-Info "Installing (per-user, no elevation needed). This takes a minute..."
$p = Start-Process -FilePath $dest -Wait -PassThru -ArgumentList @(
    '/quiet', 'InstallAllUsers=0', 'PrependPath=1', 'Include_test=0'
)
if ($p.ExitCode -ne 0) {
    Write-Err "The installer exited with code $($p.ExitCode)."
    Write-Info "Run it by hand to see the error: $dest"
    exit 1
}

Remove-Item $dest -ErrorAction SilentlyContinue

$null = Update-SessionPath
$now = Test-Python
if ($now) {
    Write-Ok "$now installed."
    Write-Host ""
    Write-Host "  Close this window and run RunThisToStart.bat again so it picks" -ForegroundColor Gray
    Write-Host "  up the new PATH." -ForegroundColor Gray
    exit 0
}

Write-Err "Python was installed but is still not on PATH in this session."
Write-Info "Close this window and run RunThisToStart.bat again."
exit 1
