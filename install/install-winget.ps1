<#
.SYNOPSIS
  Install or repair the winget client.

.DESCRIPTION
  This script installs or repairs the winget client. It is required because winget is not present on all Windows environments.

  Execution paths:
    1. Register existing App Installer package.
    2. Deploy packages directly.
    3. PowerShell Gallery bootstrap.

.PARAMETER Force
  Execute installation even if winget is already functional.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File install-winget.ps1
#>
[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$Embedded
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'

. (Join-Path $PSScriptRoot 'lib-console.ps1')
. (Join-Path $PSScriptRoot 'lib-download-cache.ps1')

function Test-Winget {
    $cmd = Get-Command winget -ErrorAction SilentlyContinue
    if (-not $cmd) { return $false }
    try {
        $null = & winget --version 2>&1
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Exit-Working {
    Write-Ok "Winget is functional: $(& winget --version)"
    if (-not $Embedded) {
        Write-Host ""
        Write-Host "Restart execution environment to apply command updates." -ForegroundColor Gray
    }
    exit 0
}

[void](Start-RunLog 'install-winget')
Write-Banner "Winget installation"

if ((Test-Winget) -and -not $Force) {
    Write-Ok "Winget is functional ($(& winget --version)). Execution terminated."
    exit 0
}

function Install-WingetDirect {
    $tmp = Join-Path $env:TEMP ("winget-setup-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    $spec = @{}
    foreach ($f in $RfcBootstrapFiles) { $spec[$f.Name] = $f }

    try {
        $missing = Get-MissingBootstrap
        if ($missing.Count -eq 0) {
            Write-Info "Packages located locally."
        } else {
            Write-Info ("Missing packages: {0}." -f ($missing -join ', '))
        }

        foreach ($name in @("VCLibs.$RfcArch.appx", "UIXaml.$RfcArch.appx", 'winget.msixbundle')) {
            [void](Get-BootstrapFile -File $spec[$name] -Path (Join-Path $tmp $name))
        }
        $deps = @((Join-Path $tmp "VCLibs.$RfcArch.appx"), (Join-Path $tmp "UIXaml.$RfcArch.appx"))

        # Framework registration is separated from bundle installation to accommodate environments with pre-existing dependencies.
        Write-Info "Registering framework packages."
        foreach ($dep in $deps) {
            $leaf = Split-Path -Leaf $dep
            try {
                Add-AppxPackage -Path $dep -ErrorAction Stop
                Write-Info "Registered $leaf."
            } catch {
                Write-Info "Registration error for $leaf : $($_.Exception.Message.Split([Environment]::NewLine)[0])"
            }
        }

        $bundle = Join-Path $tmp 'winget.msixbundle'

        # Sequential dependency application accounts for potential conflicts with pre-installed newer framework versions.
        $attempts = @(
            @{ Deps = $deps; How = 'with dependencies' }
            @{ Deps = @();   How = 'without dependencies' }
        )

        for ($i = 0; $i -lt $attempts.Count; $i++) {
            $a = $attempts[$i]
            if ($i -gt 0 -and (@($a.Deps) -join '|') -eq (@($attempts[$i - 1].Deps) -join '|')) { continue }

            Write-Info "Executing winget installation $($a.How)."
            try {
                if (@($a.Deps).Count -gt 0) {
                    Add-AppxPackage -Path $bundle -DependencyPath $a.Deps -ErrorAction Stop
                } else {
                    Add-AppxPackage -Path $bundle -ErrorAction Stop
                }
                return $true
            } catch {
                if ($i -eq $attempts.Count - 1) { throw }
                Write-Info "Installation error: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
            }
        }
        return $true
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-DirectRoute {
    try {
        if (Install-WingetDirect) {
            if (Test-Winget) { return $true }
            Write-Info "Installation completed. Winget unresponsive."
        }
    } catch {
        $msg = $_.Exception.Message.Split([Environment]::NewLine)[0]
        Write-Warn2 "Direct installation failed: $msg"
        switch -Regex ($msg) {
            '0x80073CF3' { Write-Info "Dependency error." }
            '0x80073CF9' { Write-Info "Deployment restricted." }
            '0x80073D02' { Write-Info "Concurrent installation detected." }
            '0x80070005' { Write-Info "Permission denied." }
            'connection' { Write-Info "Network connectivity error." }
        }
    }
    return $false
}

Write-Info "Initiating winget resolution."
Write-Host ""

# Offline registration mitigates network dependency for environments where packages are provisioned but not registered.
Write-Info "Registering local App Installer package."
try {
    Add-AppxPackage -RegisterByFamilyName -MainPackage Microsoft.DesktopAppInstaller_8wekyb3d8bbwe -ErrorAction Stop
    if (Test-Winget) { Exit-Working }
    Write-Info "Registration completed. Winget unresponsive."
} catch {
    Write-Info "App Installer not registered: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
}

# TLS 1.2 is enforced to maintain compatibility with modern network endpoints.
try {
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Warn2 "TLS 1.2 enforcement failed."
}

$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Info ("Execution context: " + $(if ($isAdmin) { "Administrator" } else { "Standard User" }))

$missingNow = Get-MissingBootstrap
if ($missingNow.Count -eq 0) {
    Write-Info "Executing installation from local cache."
} else {
    Write-Info "Downloading packages from Microsoft endpoint."
}
if (Invoke-DirectRoute) { Exit-Working }

$galleryOk = $true

try {
    Write-Info "Installing NuGet package provider."
    Install-PackageProvider -Name NuGet -Force -Scope $(if ($isAdmin) { 'AllUsers' } else { 'CurrentUser' }) | Out-Null
    Write-Ok "NuGet provider installed."
} catch {
    Write-Warn2 "NuGet provider installation failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
    $galleryOk = $false
}

if ($galleryOk) {
    try {
        Write-Info "Installing Microsoft.WinGet.Client module."
        Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery `
                       -Scope $(if ($isAdmin) { 'AllUsers' } else { 'CurrentUser' }) | Out-Null
        Write-Ok "Module installed."
    } catch {
        Write-Warn2 "Microsoft.WinGet.Client installation failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        $galleryOk = $false
    }
}

if ($galleryOk) {
    # Redundancy accounts for transient network unavailability during release retrieval.
    $attempts = 3
    $repaired = $false
    for ($try = 1; $try -le $attempts; $try++) {
        try {
            Write-Info "Repairing winget client."
            if ($isAdmin) { Repair-WinGetPackageManager -AllUsers }
            else          { Repair-WinGetPackageManager }
            $repaired = $true
            break
        } catch {
            $msg = $_.Exception.Message.Split([Environment]::NewLine)[0]
            if ($try -lt $attempts) {
                Write-Warn2 "Attempt $try failed: $msg. Retrying."
                Start-Sleep -Seconds (5 * $try)
            } else {
                Write-Warn2 "Repair-WinGetPackageManager failed: $msg"
            }
        }
    }
    if (-not $repaired) { $galleryOk = $false }
}

if (Test-Winget) { Exit-Working }

Write-Host ""
Write-Err "Winget resolution failed."
Write-Host ""
Write-Info "Manual installation required for REAPER and Claude."
Write-Info "  REAPER: https://www.reaper.fm/download.php"
Write-Info "  Claude: https://claude.ai/download"
Write-Host ""
Write-Info "Manual Python installation from https://www.python.org/downloads/ requires 'Add python.exe to PATH' selection."
exit 1
