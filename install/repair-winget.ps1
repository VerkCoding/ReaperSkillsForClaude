<#
.SYNOPSIS
  Install or repair the winget client.

.DESCRIPTION
  winget ships with App Installer, which is present on current Windows 11 and
  most Windows 10 installs - but not on a fresh Windows Server, not on an image
  built without the Store, and not always after an in-place upgrade. When it is
  missing, the Python install step has nothing to run.

  This is Microsoft's documented bootstrap: pull the WinGet PowerShell module
  from PSGallery and let it repair the client.

      Install-PackageProvider -Name NuGet -Force
      Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery
      Repair-WinGetPackageManager

  Two adjustments to that sequence, both about privilege:

    * -AllUsers requires an elevated session and fails without one. It is passed
      only when this is actually running as administrator, so a normal user gets
      a per-user repair instead of an error.
    * Install-Module defaults to an AllUsers scope, which also needs elevation.
      The scope is chosen to match.

  Downloads from PSGallery, so it needs a working internet connection, and takes
  a minute or two.

.PARAMETER Force
  Run even when winget already works.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File repair-winget.ps1
#>
[CmdletBinding()]
param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # progress bars slow this down a lot

function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  .      $m" -ForegroundColor Gray }
function Write-Warn2($m){ Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

function Test-Winget {
    $cmd = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    # Present on PATH is not the same as working: a half-registered App Installer
    # leaves the shim behind and fails on every invocation.
    try {
        $null = & winget --version 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  winget - install or repair" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

if ((Test-Winget) -and -not $Force) {
    Write-Ok "winget already works ($(& winget --version)). Nothing to do."
    exit 0
}

Write-Info "You may not need this at all: install-python.ps1 downloads Python"
Write-Info "directly from python.org when winget is missing, so the setup does"
Write-Info "not depend on winget existing."
Write-Host ""

# ---------------------------------------------------------------------------
# Try the offline repair first.
#
# App Installer is frequently *provisioned* but not *registered* for the current
# user - the usual state in Windows Sandbox, on Windows Server, and after some
# in-place upgrades. Registering it is instant, needs no network, and fixes a
# large share of cases. Reaching for PowerShell Gallery first means depending on
# an internet round trip to solve a problem that is often purely local.
# ---------------------------------------------------------------------------
Write-Info "Trying to register the App Installer package already on this machine..."
try {
    Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
    if (Test-Winget) {
        Write-Ok "winget is working: $(& winget --version)"
        Write-Host ""
        Write-Host "  Close this window and run RunThisToStart.bat again." -ForegroundColor Gray
        exit 0
    }
    Write-Info "Registered, but winget still does not respond."
} catch {
    Write-Info "Not available to register: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
}

$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Info ("Running " + $(if ($isAdmin) { "elevated" } else { "as a normal user" }))
if (-not $isAdmin) {
    Write-Info "A per-user repair will be attempted. If it fails, re-run this"
    Write-Info "as administrator for a machine-wide one."
}

# Windows PowerShell 5.1 still negotiates TLS 1.0 by default on older builds,
# and PSGallery refuses anything below 1.2 - which surfaces as an unhelpful
# "unable to download from URI" rather than a handshake error.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Warn2 "Could not force TLS 1.2; continuing."
}

try {
    Write-Info "Installing the NuGet package provider..."
    Install-PackageProvider -Name NuGet -Force -Scope $(if ($isAdmin) { 'AllUsers' } else { 'CurrentUser' }) | Out-Null
    Write-Ok "NuGet provider ready."
} catch {
    Write-Err "Could not install the NuGet provider: $_"
    Write-Host ""
    Write-Info "This is the inbox PackageManagement module failing to fetch its"
    Write-Info "provider list from go.microsoft.com. It is common on Windows"
    Write-Info "Sandbox, Windows Server, and behind a proxy, and there is no"
    Write-Info "reliable way around it from here."
    Write-Host ""
    Write-Info "You do not need winget. Run option [8] instead - it downloads"
    Write-Info "Python straight from python.org when winget is unavailable."
    exit 1
}

try {
    Write-Info "Installing the Microsoft.WinGet.Client module from PSGallery..."
    Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery `
                   -Scope $(if ($isAdmin) { 'AllUsers' } else { 'CurrentUser' }) | Out-Null
    Write-Ok "Module installed."
} catch {
    Write-Err "Could not install Microsoft.WinGet.Client: $_"
    exit 1
}

try {
    Write-Info "Repairing the winget client. This can take a couple of minutes..."
    if ($isAdmin) {
        Repair-WinGetPackageManager -AllUsers
    } else {
        Repair-WinGetPackageManager
    }
} catch {
    Write-Err "Repair-WinGetPackageManager failed: $_"
    if (-not $isAdmin) {
        Write-Info "Try again from an administrator prompt - some repairs need it."
    }
    exit 1
}

if (Test-Winget) {
    Write-Ok "winget is working: $(& winget --version)"
    Write-Host ""
    Write-Host "  Close this window and run RunThisToStart.bat again so it picks" -ForegroundColor Gray
    Write-Host "  up the new command." -ForegroundColor Gray
    exit 0
}

Write-Err "winget still does not respond after the repair."
Write-Info "Install 'App Installer' from the Microsoft Store, or install Python"
Write-Info "by hand from https://www.python.org/downloads/ with 'Add python.exe"
Write-Info "to PATH' ticked."
exit 1
