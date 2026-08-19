<#
  Provides a local download cache in downloadCache\ directory. Required to prevent rate-limiting when reinstalling frequently on ephemeral machines. Caches winget bootstrap files and winget packages. Searches for cached files by name and directory location.
#>

# Dot-source lib-console.ps1 to ensure logging functions are available independent of caller state.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

$RfcPluginRoot = Split-Path -Parent $PSScriptRoot
$RfcCacheDir   = Join-Path $RfcPluginRoot 'downloadCache'

# Determine system architecture for package selection. VCLibs and UI.Xaml require matching architectures to avoid deployment errors. Use PROCESSOR_ARCHITEW6432 to identify the host architecture regardless of the current process architecture.
$RfcArch = if (($env:PROCESSOR_ARCHITEW6432, $env:PROCESSOR_ARCHITECTURE) -contains 'ARM64') {
    'arm64'
} else {
    'x64'
}

# Define required bootstrap files for offline winget installation. The MinMB property validates file completeness. Filename matching patterns ensure detection of existing packages regardless of origin. Specific versions are pinned to maintain dependency compatibility.
$RfcBootstrapFiles = @(
    [pscustomobject]@{
        Name  = "VCLibs.$RfcArch.appx"; MinMB = 3
        Label = 'VCLibs'
        Match = @("*VCLibs*$RfcArch*.appx")
        Url   = "https://aka.ms/Microsoft.VCLibs.$RfcArch.14.00.Desktop.appx"
    }
    [pscustomobject]@{
        Name  = "UIXaml.$RfcArch.appx"; MinMB = 2
        Label = 'UI.Xaml'
        Match = @("*UI.Xaml*$RfcArch*.appx", "*UIXaml*$RfcArch*.appx")
        Url   = "https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.$RfcArch.appx"
    }
    [pscustomobject]@{
        Name  = 'winget.msixbundle'; MinMB = 200
        Label = 'winget 1.8.1911'
        Match = @('*DesktopAppInstaller*.msixbundle', 'winget*.msixbundle')
        Url   = 'https://github.com/microsoft/winget-cli/releases/download/v1.8.1911/Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle'
    }
)

# Winget is pinned to 1.8.1911 to avoid the Microsoft.WindowsAppRuntime.1.8 dependency introduced in newer versions. This version requires only VCLibs and UI.Xaml. The UWPDesktop version of VCLibs is used instead of the standard version as the standalone link for the latter is retired.

function Get-CacheDir {
    <# Returns the downloadCache directory path. Used as the designated write location for new downloads. Returns null if creation fails. #>
    param([switch]$Create)

    if (Test-Path $RfcCacheDir -PathType Container) { return $RfcCacheDir }
    if (-not $Create) { return $null }
    try {
        New-Item -ItemType Directory -Force -Path $RfcCacheDir -ErrorAction Stop | Out-Null
        return $RfcCacheDir
    } catch {
        return $null
    }
}

function Get-CacheSearchDir {
    <# Returns directories to search for existing files, ordered by proximity. Accommodates environments where installers are placed in parent directories. Limits search scope to specific paths to prevent unintended execution of unverified files. #>
    $dirs = @(
        $RfcCacheDir
        $RfcPluginRoot
        (Split-Path -Parent $RfcPluginRoot)
        (Join-Path (Split-Path -Parent $RfcPluginRoot) 'downloadCache')
    )
    $seen = New-Object System.Collections.Generic.HashSet[string] ([StringComparer]::OrdinalIgnoreCase)
    $out  = @()
    foreach ($d in $dirs) {
        if ($d -and (Test-Path $d -PathType Container) -and $seen.Add($d)) { $out += $d }
    }
    return $out
}

function Get-CachedFile {
    <# Searches for a cached file by exact name or pattern match. Checks file size against MinMB to reject incomplete downloads. #>
    param([string]$Name, [double]$MinMB = 0, [string[]]$Match)

    foreach ($dir in (Get-CacheSearchDir)) {
        $candidates = @()
        $exact = Join-Path $dir $Name
        if (Test-Path $exact -PathType Leaf) { $candidates += (Get-Item -LiteralPath $exact) }
        foreach ($pattern in @($Match)) {
            if (-not $pattern) { continue }
            $candidates += @(Get-ChildItem -LiteralPath $dir -File -Filter $pattern -ErrorAction SilentlyContinue)
        }

        foreach ($f in $candidates) {
            if ($MinMB -gt 0 -and ($f.Length / 1MB) -lt $MinMB) {
                Write-Warn2 ("{0} size {1:N1} MB is below minimum required size." -f $f.Name, ($f.Length / 1MB))
                continue
            }
            return $f.FullName
        }
    }
    return $null
}

function Save-ToCache {
    <# Copies a downloaded file to the cache directory for future use. #>
    param([string]$Path, [string]$Name)

    $dir = Get-CacheDir -Create
    if (-not $dir) {
        Write-Info "Cache directory is not writable. File not saved."
        return $false
    }
    try {
        $kept = Join-Path $dir $Name
        Copy-Item -LiteralPath $Path -Destination $kept -Force -ErrorAction Stop
        Write-Info "Saved to cache as $kept"
        return $true
    } catch {
        Write-Info "Failed to copy file to cache: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        return $false
    }
}

function Get-BootstrapSpec {
    <# Retrieves a bootstrap file definition by name to supply parameters for individual downloads. #>
    param([string]$Name)
    $RfcBootstrapFiles | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
}

function Get-RemoteFile {
    <# Downloads a file and validates its size. Includes retry logic for network instability. Prefers curl.exe over Invoke-WebRequest for performance reasons. Fails if the file is smaller than MinMB to prevent caching error pages. #>
    param([string]$Url, [string]$Path, [double]$MinMB, [int]$Attempts = 3, [int]$TimeoutSec = 600)

    $curl = Get-Command curl.exe -CommandType Application -ErrorAction SilentlyContinue |
            Select-Object -First 1

    $lastError = $null
    for ($try = 1; $try -le $Attempts; $try++) {
        try {
            if (Test-Path $Path) { Remove-Item $Path -Force -ErrorAction SilentlyContinue }

            if ($curl) {
                $prevEAP = $ErrorActionPreference
                $ErrorActionPreference = 'Continue'
                try {
                    & $curl.Source -L --fail -sS --connect-timeout 30 `
                        --max-time $TimeoutSec -A 'Mozilla/5.0' -o $Path $Url
                    $code = $LASTEXITCODE
                } finally {
                    $ErrorActionPreference = $prevEAP
                }
                if ($code -ne 0) { throw "curl exited $code" }
            } else {
                Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing -UserAgent 'Mozilla/5.0' -TimeoutSec $TimeoutSec
            }

            if (-not (Test-Path $Path)) { throw "nothing was written" }
            $mb = (Get-Item $Path).Length / 1MB
            if ($mb -lt $MinMB) {
                throw ("only {0:N1} MB - expected at least {1} MB, so that is not the package" -f $mb, $MinMB)
            }
            return $mb
        } catch {
            $lastError = $_.Exception.Message.Split([Environment]::NewLine)[0]
            if ($try -lt $Attempts) {
                Write-Warn2 ("Download attempt {0} failed: {1}. Retrying." -f $try, $lastError)
                Start-Sleep -Seconds (3 * $try)
            }
        }
    }
    # Removes invalid or incomplete files to prevent them from being cached or executed.
    Remove-Item $Path -Force -ErrorAction SilentlyContinue
    throw ("Download failed after {0} attempts: {1}" -f $Attempts, $lastError)
}

function Get-BootstrapFile {
    <# Copies a bootstrap file from the cache or downloads it if absent. Copies are used for installation to handle locked files or network share restrictions. #>
    param([pscustomobject]$File, [string]$Path)

    $hit = Get-CachedFile -Name $File.Name -MinMB $File.MinMB -Match $File.Match
    if ($hit) {
        Copy-Item -LiteralPath $hit -Destination $Path -Force
        $mb = (Get-Item $Path).Length / 1MB
        # Logs the use of the cached file.
        Write-Info ("{0}: Found cached file {1}, size {2:N1} MB." -f $File.Label, $hit, $mb)
        return $mb
    }

    Write-Info ("Downloading {0}." -f $File.Label)
    $mb = Get-RemoteFile -Url $File.Url -Path $Path -MinMB $File.MinMB
    Write-Info ("Downloaded {0:N1} MB." -f $mb)
    [void](Save-ToCache -Path $Path -Name $File.Name)
    return $mb
}

function Get-MissingBootstrap {
    <# Identifies bootstrap files missing from the cache to determine if an offline installation is possible. #>
    $missing = @()
    foreach ($f in $RfcBootstrapFiles) {
        if (-not (Get-CachedFile -Name $f.Name -MinMB $f.MinMB -Match $f.Match)) { $missing += $f.Name }
    }
    return $missing
}

function Find-CachedPackage {
    <# Retrieves cached package details by parsing YAML manifests for the specified PackageIdentifier. Returns the manifest and associated installer file. Missing installer payloads indicate archive packages handled directly by winget. #>
    param([string]$Id)

    $yamls = @()
    foreach ($dir in (Get-CacheSearchDir)) {
        $yamls += @(Get-ChildItem -LiteralPath $dir -Filter '*.yaml' -File -ErrorAction SilentlyContinue)
    }

    foreach ($yaml in $yamls) {
        $text = try { Get-Content -LiteralPath $yaml.FullName -Raw -ErrorAction Stop } catch { $null }
        if (-not $text) { continue }

        $pkgId = ([regex]::Match($text, '(?m)^PackageIdentifier:\s*(\S+)\s*$')).Groups[1].Value
        if ($pkgId -ne $Id) { continue }

        # Locates the installer payload by matching the manifest basename. Excludes partial downloads and unrelated files.
        $base = [System.IO.Path]::GetFileNameWithoutExtension($yaml.Name)
        $beside = @(Get-ChildItem -LiteralPath $yaml.DirectoryName -File -Filter "$base.*" -ErrorAction SilentlyContinue |
                    Where-Object { $_.Extension -ne '.yaml' })

        $installer = $beside |
                     Where-Object { [System.IO.Path]::GetFileNameWithoutExtension($_.Name) -eq $base } |
                     Select-Object -First 1

        # Records partial or rejected files associated with the manifest.
        $partial = @($beside |
                     Where-Object { [System.IO.Path]::GetFileNameWithoutExtension($_.Name) -ne $base } |
                     ForEach-Object { $_.FullName })

        $silent = ([regex]::Match($text, '(?m)^\s*Silent:\s*(.+?)\s*$')).Groups[1].Value
        if (-not $silent) {
            $silent = ([regex]::Match($text, '(?m)^\s*SilentWithProgress:\s*(.+?)\s*$')).Groups[1].Value
        }

        $codes = @()
        $block = [regex]::Match($text, '(?ms)^\s*InstallerSuccessCodes:\s*\r?\n((?:\s*-\s*-?\d+\s*\r?\n?)+)')
        if ($block.Success) {
            foreach ($line in ($block.Groups[1].Value -split "`n")) {
                $m = [regex]::Match($line, '^\s*-\s*(-?\d+)\s*$')
                if ($m.Success) { $codes += [int]$m.Groups[1].Value }
            }
        }

        return [pscustomobject]@{
            Id           = $pkgId
            Version      = ([regex]::Match($text, '(?m)^PackageVersion:\s*(\S+)\s*$')).Groups[1].Value
            Manifest     = $yaml.FullName
            Installer    = if ($installer) { $installer.FullName } else { $null }
            Partial      = $partial
            Type         = ([regex]::Match($text, '(?m)^\s*InstallerType:\s*(\S+)\s*$')).Groups[1].Value.ToLower()
            Nested       = ([regex]::Match($text, '(?m)^\s*NestedInstallerType:\s*(\S+)\s*$')).Groups[1].Value.ToLower()
            Silent       = $silent
            SuccessCodes = $codes
        }
    }
    return $null
}

function Get-PackageToCache {
    <# Uses 'winget download' to fetch a package and its manifest to the cache directory. Enables subsequent offline installations. Archive or portable packages are downloaded but lack a traditional installer payload. #>
    param([string]$Id)

    $dir = Get-CacheDir -Create
    if (-not $dir) { return $null }

    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & winget download -e --id $Id --source winget -d $dir `
            --accept-package-agreements --accept-source-agreements
        $code = $LASTEXITCODE
    } catch {
        Write-Warn2 "Download failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        return $null
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    if ($code -ne 0) {
        Write-Warn2 "Winget download returned exit code $code."
        return $null
    }

    $pkg = Find-CachedPackage -Id $Id
    if (-not $pkg -or -not $pkg.Installer) {
        Write-Warn2 "Download completed. Installer or manifest missing."
        return $null
    }

    if (-not (Test-CachedPackageUsable -Package $pkg)) {
        Remove-Item -LiteralPath $pkg.Installer -Force -ErrorAction SilentlyContinue
        Write-Info "Package type is '$($pkg.Type)'. Winget extracts this package format. Retaining manifest only."
        return $null
    }

    Write-Info "Package saved to cache."
    return $pkg
}

function Test-CachedPackageUsable {
    <# Evaluates whether the cached package type supports standard execution. Excludes archive and portable types which require winget extraction and linking. #>
    param([pscustomobject]$Package)

    if (-not $Package -or -not $Package.Installer) { return $false }
    if ($Package.Nested) { return $false }
    return ($Package.Type -notin @('zip', 'archive', 'portable', 'msstore'))
}

function Install-CachedPackage {
    <# Executes the cached installer using arguments specified in its manifest or system defaults. Returns true upon successful exit code. #>
    param(
        [pscustomobject]$Package,
        [string]$ExtraArgs
    )

    $file = $Package.Installer
    $type = $Package.Type

    if (-not (Test-CachedPackageUsable -Package $Package)) {
        Write-Info "Package requires extraction. Deferring to winget."
        return $false
    }

    if ($type -in @('msix', 'appx', 'msixbundle', 'appxbundle')) {
        try {
            Add-AppxPackage -Path $file -ErrorAction Stop
            return $true
        } catch {
            Write-Warn2 "Installation failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
            return $false
        }
    }

    $exe  = $file
    $line = $Package.Silent

    if ($type -in @('msi', 'wix')) {
        if (-not $line) { $line = '/qn /norestart' }
        $exe  = 'msiexec.exe'
        $line = "/i `"$file`" $line"
    } elseif ($type -in @('exe', 'inno', 'nullsoft', 'burn')) {
        if (-not $line) {
            # Applies default silent switches based on installer type.
            $line = switch ($type) {
                'inno'     { '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES' }
                'nullsoft' { '/S' }
                'burn'     { '/quiet /norestart' }
                default    { '' }
            }
            if (-not $line) {
                Write-Info "No silent switch declared in manifest."
            }
        }
    } else {
        Write-Info "Unsupported installer type '$type'. Deferring to winget."
        return $false
    }

    if ($ExtraArgs) { $line = ("$line $ExtraArgs").Trim() }

    try {
        # Conditionally passes ArgumentList to avoid Start-Process errors with empty parameters.
        $proc = if ($line) {
            Start-Process -FilePath $exe -ArgumentList $line -Wait -PassThru -ErrorAction Stop
        } else {
            Start-Process -FilePath $exe -Wait -PassThru -ErrorAction Stop
        }
    } catch {
        Write-Warn2 "Failed to start installer: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        return $false
    }

    $ok = @(0) + $Package.SuccessCodes
    if ($ok -contains $proc.ExitCode) { return $true }

    Write-Warn2 ("Installer returned exit code {0}." -f $proc.ExitCode)
    return $false
}
