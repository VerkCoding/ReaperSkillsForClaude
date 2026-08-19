<#
.SYNOPSIS
  Download required setup files to downloadCache.

.DESCRIPTION
  Downloads files required for installation to allow offline installation or avoid repeated downloads.

  Packages winget extracts rather than installs are not supported for offline installation and require an active internet connection on the target machine.

  Existing files on disk are used and not downloaded again.

.PARAMETER Force
  Re-download files that are already cached.

.PARAMETER Consolidate
  Copy files found outside downloadCache into it.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File fill-download-cache.ps1
#>
[CmdletBinding()]
param([switch]$Force, [switch]$Consolidate)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue' # Prevent progress bars from slowing down downloads.

. (Join-Path $PSScriptRoot 'lib-console.ps1')
. (Join-Path $PSScriptRoot 'lib-app-control.ps1')
. (Join-Path $PSScriptRoot 'lib-download-cache.ps1')

# Prevent pausing execution on terminal click.
[void](Disable-ConsoleQuickEdit)

$stepLog = Start-RunLog 'prepare-offline'
Write-Banner "Download Cache Process"
if ($stepLog) { Write-Info "Log: $stepLog" }

$dir = Get-CacheDir -Create
if (-not $dir) {
    Write-Err "Directory creation failed for $RfcCacheDir."
    Write-Info "Ensure write permissions are available."
    exit 1
}
Write-Info "Target directory: $dir"

try {
    # Establish TLS 1.2 connection for endpoints that require it.
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Warn2 "Failed to set TLS 1.2."
}

$failed  = @()
$outside = @()

function Register-CacheLocation {
    <#
      Record the presence of a file and apply consolidation logic if specified.
    #>
    param([string]$Path, [string]$Name, [string]$Label)

    $mb = (Get-Item -LiteralPath $Path).Length / 1MB
    if ((Split-Path -Parent $Path) -ieq $dir) {
        Write-Ok ("{0} exists ({1:N1} MB)." -f $Label, $mb)
        return $true
    }

    Write-Ok ("{0} found at alternate path ({1:N1} MB). Download skipped." -f $Label, $mb)
    Write-Info "Path: $Path"
    if (-not $Consolidate) {
        # File is skipped because consolidation was not requested.
        $script:outside += $Label
        return $false
    }
    if (Save-ToCache -Path $Path -Name $Name) {
        Write-Ok ("{0} copied to cache." -f $Label)
        return $true
    }
    Write-Warn2 ("{0} copy operation failed." -f $Label)
    return $false
}

Write-Step "Winget dependencies"

# Clear existing cached files when Force parameter is used.
if ($Force) {
    foreach ($n in $RfcBootstrapFiles.Name) {
        Remove-Item (Join-Path $dir $n) -Force -ErrorAction SilentlyContinue
    }
}

$missing = Get-MissingBootstrap
if ($missing.Count -eq 0) {
    Write-Ok "Winget packages are present."
} else {
    Write-Info ("Missing packages: {0}." -f ($missing -join ', '))
}

foreach ($f in $RfcBootstrapFiles) {
    $have = Get-CachedFile -Name $f.Name -MinMB $f.MinMB -Match $f.Match
    if ($have) {
        [void](Register-CacheLocation -Path $have -Name $f.Name -Label $f.Label)
        continue
    }

    # Use a temporary directory to avoid corrupted states on aborted downloads.
    $tmp = Join-Path $env:TEMP ("rfc-fill-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        [void](Get-BootstrapFile -File $f -Path $tmp)
        Write-Ok "$($f.Label) download completed."
    } catch {
        Write-Err "$($f.Label) download failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        $failed += "$($f.Label) download failure"
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

Write-Step "Applications"

$wingetOk = $false
if (Get-Command winget -ErrorAction SilentlyContinue) {
    try { $null = & winget --version 2>&1; $wingetOk = ($LASTEXITCODE -eq 0) } catch { }
}

if (-not $wingetOk) {
    Write-Warn2 "Winget is unavailable. Application downloads skipped."
} else {
    foreach ($app in (Get-AppList)) {
        $existing = Find-CachedPackage -Id $app.Id

        # Remove incomplete download fragments.
        if ($existing -and $existing.Partial -and -not $existing.Installer) {
            Write-Warn2 "$($app.Name): Incomplete download detected. Retrying."
            foreach ($p in $existing.Partial) { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue }
            $existing = $null
        }

        if ($existing -and -not $Force) {
            if ($existing.Installer) {
                Write-Ok "$($app.Name) $($existing.Version): Cached."
            } else {
                # Skip package types that require extraction by winget.
                Write-Info "$($app.Name): Unsupported for offline installation. Skipped."
            }
            continue
        }
        if ($existing -and $Force) {
            Remove-Item -LiteralPath $existing.Manifest -Force -ErrorAction SilentlyContinue
            if ($existing.Installer) {
                Remove-Item -LiteralPath $existing.Installer -Force -ErrorAction SilentlyContinue
            }
        }

        Write-Info "Downloading $($app.Name)..."
        $got = Get-PackageToCache -Id $app.Id
        if ($got) {
            Write-Ok "$($app.Name) $($got.Version) cached."
            continue
        }

        # Track failure for packages that winget installs rather than extracts.
        $after = Find-CachedPackage -Id $app.Id
        if ($after -and -not $after.Installer -and -not $after.Partial) {
            Write-Info "Offline installation not supported for this package."
        } else {
            $failed += "$($app.Name) download failure"
        }
    }
}

$files = @(Get-ChildItem $dir -File -ErrorAction SilentlyContinue)
$total = ($files | Measure-Object -Property Length -Sum).Sum / 1MB

$readme = @"
downloadCache directory

Contents: Installer files.
Generated by: install\fill-download-cache.ps1.

Timestamp: $((Get-Date).ToString('yyyy-MM-dd HH:mm'))
"@
try {
    [System.IO.File]::WriteAllText((Join-Path $dir 'README.txt'), $readme,
        (New-Object System.Text.UTF8Encoding $false))
} catch { }

Write-Result -Problems $failed -DoneWord $(if ($failed.Count -eq 0) { 'CACHE_COMPLETE' } else { 'CACHE_INCOMPLETE' })
Write-Info ("{0} file(s), {1:N0} MB in {2}" -f $files.Count, $total, $dir)

if ($outside.Count -gt 0) {
    Write-Host ""
    Write-Info "Files located outside downloadCache: $($outside -join ', ')"
}

if ($failed.Count -gt 0) {
    Write-Info "Rerun required for failed items."
}

Write-Host ""
Write-Info "Process completed."
Write-Host ""

exit $(if ($failed.Count -eq 0) { 0 } else { 1 })
