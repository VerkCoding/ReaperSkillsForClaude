<#
  The download cache: downloadCache\ beside RunThisToStart.bat.

  Dot-sourced by repair-winget.ps1, setup-all.ps1 and fill-download-cache.ps1.
  Nothing here runs on its own.

  Why this exists
  ---------------
  A machine without winget needs over 300 MB before anything can be installed:
  the winget bundle itself, its Windows App Runtime, and two framework packages.
  On a real machine that happens once and nobody notices. On a machine that gets
  wiped between runs - Windows Sandbox, a test VM, a lab image - it happens every
  single time, and repeating a 200 MB fetch from the same host often enough gets
  the address rate-limited or blocked outright. Which is exactly what happened
  here: GitHub stopped answering, and with it went the only source of the winget
  client AND the Git for Windows installer.

  So: download once, keep the file, use it next time. The directory is
  gitignored, sits next to the batch file, and is therefore inside whatever
  folder a Sandbox has shared into it - which is what makes the second run free.

  The rule, in one line
  ---------------------
  If downloadCache has the file, use it. If not, download it, and put it there.

  Nothing here is ever required. An empty, unwritable or missing downloadCache
  changes nothing about how the setup behaves; it just downloads, as before.

  Two kinds of thing live in it
  -----------------------------
    1. The winget bootstrap files, under fixed names. Named rather than
       discovered because there is no manifest for them - they are four specific
       URLs, listed in $RfcBootstrapFiles below.

    2. Whole winget packages, in the layout `winget download` writes: the
       installer and a merged .yaml manifest beside it, sharing a basename.
       That layout is the useful one, because the manifest carries the silent
       switches and success codes for the installer next to it. This does not
       have to guess how to run REAPER's or Git's installer quietly - the
       manifest says, in the same words winget itself would have used.
#>

# Resolved at dot-source time, when $PSScriptRoot is unambiguously this file's
# folder. Every caller lives in install\, so the cache is one level up.
$RfcCacheDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'downloadCache'

# The four files winget cannot be installed without, in the order they are
# needed. One list, so repair-winget.ps1 and fill-download-cache.ps1 cannot
# drift apart about what "cached" means.
#
# MinMB is a floor, not a size: a proxy or captive portal answers with an HTML
# login page and HTTP 200, which would otherwise be saved under an .appx name
# and fail later with an error about the package rather than about the network.
$RfcBootstrapFiles = @(
    [pscustomobject]@{
        Name  = 'windowsappruntime.exe'; MinMB = 40
        Label = 'Windows App Runtime'
        Url   = 'https://aka.ms/windowsappsdk/1.8/latest/windowsappruntimeinstall-x64.exe'
    }
    [pscustomobject]@{
        Name  = 'VCLibs.appx'; MinMB = 3
        Label = 'VCLibs'
        Url   = 'https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx'
    }
    [pscustomobject]@{
        Name  = 'UIXaml.appx'; MinMB = 2
        Label = 'UI.Xaml'
        Url   = 'https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.x64.appx'
    }
    [pscustomobject]@{
        Name  = 'winget.msixbundle'; MinMB = 100
        Label = 'winget'
        Url   = 'https://aka.ms/getwinget'
    }
)

# ---------------------------------------------------------------------------
# The directory
# ---------------------------------------------------------------------------

function Get-CacheDir {
    <#
      The cache directory, or $null if there is none and one could not be made.

      Never throws. A read-only shared folder, a locked-down profile or a full
      disk all mean "no cache", which is a perfectly ordinary state - not a
      reason to stop installing.
    #>
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

function Get-CachedFile {
    <#
      A cached file by name, or $null.

      The size floor is checked on the way OUT as well as in. A cache is a
      directory anyone can drop a file into, and half a download left behind by
      a crashed run looks exactly like a real one until it is installed.
    #>
    param([string]$Name, [double]$MinMB = 0)

    $dir = Get-CacheDir
    if (-not $dir) { return $null }

    $path = Join-Path $dir $Name
    if (-not (Test-Path $path -PathType Leaf)) { return $null }

    if ($MinMB -gt 0) {
        $mb = (Get-Item $path).Length / 1MB
        if ($mb -lt $MinMB) {
            Write-Warn2 ("downloadCache\{0} is only {1:N1} MB - too small to be the real file, ignoring it." -f $Name, $mb)
            return $null
        }
    }
    return $path
}

function Save-ToCache {
    <#
      Keep a file that was just downloaded. Best effort, always.

      The common reason this fails is a Sandbox folder shared read-only, which
      is a legitimate way to run this and must not turn into an error.
    #>
    param([string]$Path, [string]$Name)

    $dir = Get-CacheDir -Create
    if (-not $dir) {
        Write-Info "downloadCache is not writable here; not keeping a copy."
        return $false
    }
    try {
        Copy-Item -LiteralPath $Path -Destination (Join-Path $dir $Name) -Force -ErrorAction Stop
        Write-Info "Kept as downloadCache\$Name - the next run will not download it again."
        return $true
    } catch {
        Write-Info "Could not keep a copy in downloadCache: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        return $false
    }
}

# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

function Get-RemoteFile {
    <#
      Download to a path, and refuse anything implausibly small. Returns the
      size in MB, or throws.

      Retried, because these are large files over a connection that may not be
      good. A 207 MB download dropping at 60% is not a reason to give up and
      spend several minutes failing over to a route that works even less often -
      it is a reason to ask again.
    #>
    param([string]$Url, [string]$Path, [double]$MinMB, [int]$Attempts = 3)

    $lastError = $null
    for ($try = 1; $try -le $Attempts; $try++) {
        try {
            if (Test-Path $Path) { Remove-Item $Path -Force -ErrorAction SilentlyContinue }
            Invoke-WebRequest -Uri $Url -OutFile $Path -UseBasicParsing -UserAgent 'Mozilla/5.0' -TimeoutSec 600
            $mb = (Get-Item $Path).Length / 1MB
            if ($mb -lt $MinMB) {
                throw ("only {0:N1} MB - expected at least {1} MB, so that is not the package" -f $mb, $MinMB)
            }
            return $mb
        } catch {
            $lastError = $_.Exception.Message.Split([Environment]::NewLine)[0]
            if ($try -lt $Attempts) {
                Write-Warn2 ("  attempt {0} failed ({1}); retrying..." -f $try, $lastError)
                Start-Sleep -Seconds (3 * $try)
            }
        }
    }
    throw ("could not download after {0} attempts: {1}" -f $Attempts, $lastError)
}

function Get-BootstrapFile {
    <#
      One of $RfcBootstrapFiles, from the cache if it is there and from the
      network if it is not - and cached on the way past either way.

      -Path is where the caller wants it to end up; the cache keeps its own
      copy under the fixed name. Copying rather than installing straight out of
      downloadCache is deliberate: Add-AppxPackage and the runtime installer
      both open these files, and the cache is a folder that may be shared,
      read-only, or on a network path.
    #>
    param([pscustomobject]$File, [string]$Path)

    $hit = Get-CachedFile -Name $File.Name -MinMB $File.MinMB
    if ($hit) {
        Copy-Item -LiteralPath $hit -Destination $Path -Force
        $mb = (Get-Item $Path).Length / 1MB
        Write-Info ("{0}: using downloadCache\{1} ({2:N1} MB, no download)." -f $File.Label, $File.Name, $mb)
        return $mb
    }

    Write-Info ("Downloading {0}..." -f $File.Label)
    $mb = Get-RemoteFile -Url $File.Url -Path $Path -MinMB $File.MinMB
    Write-Info ("  {0:N1} MB" -f $mb)
    [void](Save-ToCache -Path $Path -Name $File.Name)
    return $mb
}

# ---------------------------------------------------------------------------
# Whole packages, in `winget download` layout
# ---------------------------------------------------------------------------

function Find-CachedPackage {
    <#
      The cached package for a winget id, or $null.

      Matched by reading PackageIdentifier out of every .yaml in the cache
      rather than by filename, because `winget download` names its files after
      the package NAME and version - REAPER_7.78_Machine_X64_exe_en-US.yaml -
      and neither of those is the id we are looking for.

      Returns everything needed to run the installer: the file, its type, the
      silent switches the manifest declares, and the exit codes it counts as
      success.
    #>
    param([string]$Id)

    $dir = Get-CacheDir
    if (-not $dir) { return $null }

    foreach ($yaml in @(Get-ChildItem $dir -Filter '*.yaml' -File -ErrorAction SilentlyContinue)) {
        $text = try { Get-Content -LiteralPath $yaml.FullName -Raw -ErrorAction Stop } catch { $null }
        if (-not $text) { continue }

        $pkgId = ([regex]::Match($text, '(?m)^PackageIdentifier:\s*(\S+)\s*$')).Groups[1].Value
        if ($pkgId -ne $Id) { continue }

        # The installer is the file beside the manifest sharing its basename.
        # `winget download` writes exactly that pair, flat, one installer per
        # manifest - which is why the flat regexes below are safe: a merged
        # manifest from a download has a single Installers: entry.
        $base = [System.IO.Path]::GetFileNameWithoutExtension($yaml.Name)
        $installer = Get-ChildItem $dir -File -Filter "$base.*" -ErrorAction SilentlyContinue |
                     Where-Object { $_.Extension -ne '.yaml' } | Select-Object -First 1
        if (-not $installer) {
            Write-Warn2 "downloadCache\$($yaml.Name) has no installer beside it - ignoring it."
            continue
        }

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
            Installer    = $installer.FullName
            Type         = ([regex]::Match($text, '(?m)^\s*InstallerType:\s*(\S+)\s*$')).Groups[1].Value.ToLower()
            Nested       = ([regex]::Match($text, '(?m)^\s*NestedInstallerType:\s*(\S+)\s*$')).Groups[1].Value.ToLower()
            Silent       = $silent
            SuccessCodes = $codes
        }
    }
    return $null
}

function Test-CachedPackageUsable {
    <#
      Can this cached package actually be installed from disk?

      One kind cannot: a portable or archive package. winget does not run an
      installer for those - it extracts the payload into its own Packages
      directory, links it into WinGet\Links, adds that to PATH and records the
      whole lot in a tracking file so `winget uninstall` still works.
      Reimplementing that bookkeeping to save one download would be trading a
      correct install for a fast one, and getting it subtly wrong leaves a
      `claude` that winget cannot then repair.

      Asked separately from installing so fill-download-cache.ps1 can throw
      away a package it would never be able to use, rather than leaving a
      hundred megabytes in the cache that every run politely ignores.
    #>
    param([pscustomobject]$Package)

    if ($Package.Nested) { return $false }
    return ($Package.Type -notin @('zip', 'archive', 'portable', 'msstore'))
}

function Install-CachedPackage {
    <#
      Run a cached installer. Returns $true only if it reported success.

      Nothing here is invented. The switches come from the manifest that
      `winget download` wrote beside the installer, and where a manifest
      declares none, the fallback is that installer technology's documented
      default - which is the same table winget applies. That includes the
      awkward case: for a bare `exe` winget passes no arguments at all, so
      neither does this. Such an installer may show a window; it would have
      shown the same window under winget.

      Failure is never fatal. The caller falls back to a normal winget install,
      so the worst a bad cached file can cost is the time it took to try.
    #>
    param(
        [pscustomobject]$Package,
        # Appended after the silent switches, exactly as winget's --custom does.
        [string]$ExtraArgs
    )

    $file = $Package.Installer
    $type = $Package.Type

    if (-not (Test-CachedPackageUsable -Package $Package)) {
        Write-Info "  that package is extracted rather than installed - leaving it to winget."
        return $false
    }

    if ($type -in @('msix', 'appx', 'msixbundle', 'appxbundle')) {
        try {
            Add-AppxPackage -Path $file -ErrorAction Stop
            return $true
        } catch {
            Write-Warn2 "  $($_.Exception.Message.Split([Environment]::NewLine)[0])"
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
            # winget's own per-type defaults. 'exe' is genuinely empty there:
            # a plain exe gets run with nothing, because nothing can be assumed
            # about it.
            $line = switch ($type) {
                'inno'     { '/VERYSILENT /NORESTART /SUPPRESSMSGBOXES' }
                'nullsoft' { '/S' }
                'burn'     { '/quiet /norestart' }
                default    { '' }
            }
            if (-not $line) {
                Write-Info "  the manifest declares no silent switch, so this installer may"
                Write-Info "  show a window. winget would run it exactly the same way."
            }
        }
    } else {
        Write-Info "  installer type '$type' is not one this can run - leaving it to winget."
        return $false
    }

    if ($ExtraArgs) { $line = ("$line $ExtraArgs").Trim() }

    try {
        # Start-Process refuses an empty -ArgumentList, and "no arguments" is a
        # real answer here rather than a missing one.
        $proc = if ($line) {
            Start-Process -FilePath $exe -ArgumentList $line -Wait -PassThru -ErrorAction Stop
        } else {
            Start-Process -FilePath $exe -Wait -PassThru -ErrorAction Stop
        }
    } catch {
        Write-Warn2 "  could not start the cached installer: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        return $false
    }

    $ok = @(0) + $Package.SuccessCodes
    if ($ok -contains $proc.ExitCode) { return $true }

    Write-Warn2 ("  the cached installer exited {0}." -f $proc.ExitCode)
    return $false
}
