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
    [switch]$Force,
    # Set by setup-all.ps1. The advice to close the window and start again is
    # right when this is run on its own from the menu, and wrong mid-install:
    # the caller re-checks winget itself and carries straight on, so telling the
    # user to restart makes a working run look like a failed one.
    [switch]$Embedded
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

function Get-File {
    <#
      Download to a path, and refuse anything implausibly small.

      A proxy or captive portal answers with an HTML login page and HTTP 200,
      which then gets saved under an .appx name and fails to install with an
      error about the package rather than about the network.
    #>
    param([string]$Url, [string]$Path, [double]$MinMB)

    Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing -UserAgent 'Mozilla/5.0'
    $mb = (Get-Item $Path).Length / 1MB
    if ($mb -lt $MinMB) {
        throw ("downloaded only {0:N1} MB from {1} - expected at least {2} MB, so that is not the package" -f $mb, $Url, $MinMB)
    }
    return $mb
}

function Install-WingetDirect {
    <#
      Install winget from Microsoft's own release, with its dependencies.

      This is the route that actually works where winget is genuinely absent -
      Windows Sandbox, Windows Server, a stripped image. It needs nothing but
      HTTPS: no PowerShell Gallery, no NuGet provider, no Store.

      Order matters. The bundle declares VCLibs and UI.Xaml as dependencies, so
      installing it first fails with a dependency error that reads like a
      corrupt download.
    #>
    $tmp = Join-Path $env:TEMP ("winget-setup-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    $parts = @(
        @{ Name = 'VCLibs';  Min = 3;   File = 'VCLibs.appx'
           Url  = 'https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx' }
        @{ Name = 'UI.Xaml'; Min = 2;   File = 'UIXaml.appx'
           Url  = 'https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.x64.appx' }
        @{ Name = 'winget';  Min = 100; File = 'winget.msixbundle'
           Url  = 'https://aka.ms/getwinget' }
    )

    try {
        foreach ($p in $parts) {
            $dest = Join-Path $tmp $p.File
            Write-Info ("Downloading {0}..." -f $p.Name)
            $mb = Get-File -Url $p.Url -Path $dest -MinMB $p.Min
            Write-Info ("  {0:N1} MB" -f $mb)
        }

        foreach ($p in $parts) {
            $dest = Join-Path $tmp $p.File
            Write-Info ("Installing {0}..." -f $p.Name)
            try {
                Add-AppxPackage -Path $dest -ErrorAction Stop
            } catch {
                # The dependencies are frequently already present and a newer
                # version refuses to downgrade. That is not a failure unless the
                # bundle itself cannot install.
                if ($p.Name -eq 'winget') { throw }
                Write-Info ("  already present or newer; continuing")
            }
        }
        return $true
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Info "winget is the backbone of the install, so this tries hard to get it."
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
        if (-not $Embedded) {
            Write-Host ""
            Write-Host "  Close this window and run RunThisToStart.bat again." -ForegroundColor Gray
        }
        exit 0
    }
    Write-Info "Registered, but winget still does not respond."
} catch {
    # Expected whenever App Installer is genuinely absent rather than merely
    # unregistered - a fresh Windows Sandbox, for instance, has nothing to
    # register. 0x80073CF9 is the usual HRESULT here. It reads like a failure
    # because it is one, but it is the cheap attempt, not the plan.
    Write-Info "Nothing to register here ($($_.Exception.Message.Split([Environment]::NewLine)[0]))"
    Write-Info "That is normal when App Installer was never present. Falling back..."
}

# ---------------------------------------------------------------------------
# TLS first: everything below downloads, and Windows PowerShell 5.1 on older
# builds still offers TLS 1.0, which these hosts refuse.
# ---------------------------------------------------------------------------
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Warn2 "Could not force TLS 1.2; continuing."
}

# ---------------------------------------------------------------------------
# Install it straight from Microsoft's release.
#
# This is deliberately ahead of the PowerShell Gallery route. The Gallery route
# is the one people quote, and it fails on exactly the machines that need it:
# the inbox PackageManagement module cannot fetch its provider list from
# go.microsoft.com on a Sandbox, a Server, or anything proxied, and reports
#
#   Unable to download from URI 'https://go.microsoft.com/fwlink/?LinkID=627338'
#
# before it has done anything. Downloading three files over HTTPS has no such
# dependency, which is why it goes first now rather than last.
# ---------------------------------------------------------------------------
Write-Info "Installing winget from Microsoft's release (about 220 MB)..."
try {
    if (Install-WingetDirect) {
        if (Test-Winget) {
            Write-Ok "winget is working: $(& winget --version)"
            if (-not $Embedded) {
                Write-Host ""
                Write-Host "  Close this window and run RunThisToStart.bat again." -ForegroundColor Gray
            }
            exit 0
        }
        Write-Info "Installed, but winget does not respond yet."
    }
} catch {
    Write-Warn2 "Direct install did not succeed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
    Write-Info  "Falling back to the PowerShell Gallery route."
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
    if (-not $Embedded) {
        Write-Host ""
        Write-Host "  Close this window and run RunThisToStart.bat again so it picks" -ForegroundColor Gray
        Write-Host "  up the new command." -ForegroundColor Gray
    }
    exit 0
}

Write-Err "winget still does not respond after the repair."
Write-Info "Install 'App Installer' from the Microsoft Store, or install Python"
Write-Info "by hand from https://www.python.org/downloads/ with 'Add python.exe"
Write-Info "to PATH' ticked."
exit 1
