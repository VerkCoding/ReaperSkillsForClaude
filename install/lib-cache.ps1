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
  If the file is already here, use it. If not, download it, and keep it.

  Nothing here is ever required. An empty, unwritable or missing downloadCache
  changes nothing about how the setup behaves; it just downloads, as before.

  "Already here" is deliberately generous
  ---------------------------------------
  Two ways, both because the alternative is re-downloading 207 MB while the
  file sits a few inches away.

  By name: nobody renames the winget bundle. It arrives as
  Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle and stays that way, so
  each entry below carries the patterns it plausibly arrived under as well as
  the name we would have given it.

  By place: the folder shared into a Sandbox is normally the one CONTAINING
  this repository - installers get dropped beside the clone, not inside it. So
  four directories are searched, nearest first: downloadCache, the plugin root,
  its parent, and a downloadCache in that parent. Read-only, all of them;
  anything downloaded is still written to downloadCache and nowhere else. The
  search stops there on purpose - a cache that installs something it found
  lying around in Downloads would be worse than no cache.

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
# folder. Every caller lives in install\, so the plugin root is one level up.
$RfcPluginRoot = Split-Path -Parent $PSScriptRoot
$RfcCacheDir   = Join-Path $RfcPluginRoot 'downloadCache'

# The four files winget cannot be installed without, in the order they are
# needed. One list, so repair-winget.ps1 and fill-download-cache.ps1 cannot
# drift apart about what "cached" means.
#
# MinMB is a floor, not a size: a proxy or captive portal answers with an HTML
# login page and HTTP 200, which would otherwise be saved under an .appx name
# and fail later with an error about the package rather than about the network.
#
# Match is the reason this works on a folder somebody filled by hand. Nobody who
# downloads the winget bundle renames it to winget.msixbundle - it arrives as
# Microsoft.DesktopAppInstaller_8wekyb3d8bbwe.msixbundle and that is what they
# keep. A cache that only recognises our own naming would sit next to a 207 MB
# file it needed and download it again, which is precisely the failure this
# whole thing exists to prevent. Patterns are specific enough not to grab an
# unrelated package, and the size floor still applies to whatever they match.
$RfcBootstrapFiles = @(
    [pscustomobject]@{
        Name  = 'windowsappruntime.exe'; MinMB = 40
        Label = 'Windows App Runtime'
        Match = @('WindowsAppRuntimeInstall*.exe', '*windowsappruntime*.exe')
        Url   = 'https://aka.ms/windowsappsdk/1.8/latest/windowsappruntimeinstall-x64.exe'
    }
    [pscustomobject]@{
        Name  = 'VCLibs.appx'; MinMB = 3
        Label = 'VCLibs'
        Match = @('Microsoft.VCLibs*.appx', '*VCLibs*.appx')
        Url   = 'https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx'
    }
    [pscustomobject]@{
        Name  = 'UIXaml.appx'; MinMB = 2
        Label = 'UI.Xaml'
        Match = @('Microsoft.UI.Xaml*.appx', '*UI.Xaml*.appx', '*UIXaml*.appx')
        Url   = 'https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.x64.appx'
    }
    [pscustomobject]@{
        Name  = 'winget.msixbundle'; MinMB = 100
        Label = 'winget'
        Match = @('*DesktopAppInstaller*.msixbundle', 'winget*.msixbundle')
        Url   = 'https://aka.ms/getwinget'
    }
)

# The App Runtime as a package rather than an installer.
#
# Microsoft's own link for it is a 102 MB setup executable that has to be run;
# the mirror below carries the 40 MB framework .msix instead, which can simply
# be handed to Add-AppxPackage as one more dependency. Preferred when present,
# which is why it is a separate entry rather than a fifth downloadable one -
# there is no URL to fetch it from on its own.
$RfcRuntimeMsix = [pscustomobject]@{
    Name  = 'windowsappruntime.msix'; MinMB = 15
    Label = 'Windows App Runtime (package)'
    Match = @('Microsoft.WindowsAppRuntime.1.8.msix', 'Microsoft.WindowsAppRuntime.*.msix')
    Url   = $null
}

# The mirror: one archive holding every file above, for every architecture.
#
# A third party's re-host of Microsoft's own packages, and used as the first
# place to DOWNLOAD from - never as something to run. Only the packages are
# taken out of it; the Install-Winget.ps1 and Launcher.bat it also contains are
# ignored, because running a stranger's script with administrator rights on
# every machine this plugin is installed on is not a trade worth making for a
# faster download.
#
# What makes taking the packages defensible is that Windows checks them.
# Add-AppxPackage validates the MSIX signature chain at deployment, so a bundle
# that is not the one Microsoft signed does not install - it fails, and the
# Microsoft download below runs instead. That is the whole safety argument, and
# it is why extraction is the only thing done here.
#
# It is worth being clear about what this does NOT fix: the archive is a GitHub
# release asset, served from the same host as Microsoft's own release. If GitHub
# is rate-limiting or blocking an address, this fails exactly as that does. The
# thing that solves that is the cache, not the source.
$RfcMirrorBundle = [pscustomobject]@{
    Label = 'winget offline bundle'
    Url   = 'https://github.com/EXLOUD/winget-installer/releases/download/v1.2.0/winget-v1.12.460-exloud.zip'
    MinMB = 300
    About = 'EXLOUD/winget-installer v1.2.0 (winget 1.12.460)'
}

function Get-MirrorContents {
    <#
      Which entries of the mirror archive are wanted, and what to call them
      once extracted.

      Matched by wildcard rather than by exact path so a version bump inside the
      archive does not silently stop finding anything - the names carry versions
      (Microsoft.VCLibs.14.00_14.0.33728_x64.Appx) and those will move.
    #>
    $arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'x64' }
    @(
        [pscustomobject]@{ As = 'winget.msixbundle'
                           Pattern = '*Microsoft.DesktopAppInstaller_*.msixbundle' }
        [pscustomobject]@{ As = 'VCLibs.appx'
                           Pattern = "*Microsoft.VCLibs.14.00_*_$arch.appx" }
        [pscustomobject]@{ As = 'UIXaml.appx'
                           Pattern = "*Microsoft.UI.Xaml.2.8_*_$arch.appx" }
        [pscustomobject]@{ As = 'windowsappruntime.msix'
                           Pattern = "*win10-$arch/Microsoft.WindowsAppRuntime.1.8.msix" }
    )
}

# ---------------------------------------------------------------------------
# The directories
# ---------------------------------------------------------------------------

function Get-CacheDir {
    <#
      Where cached files are WRITTEN, or $null if it does not exist and could
      not be made. Only ever downloadCache itself - the extra places searched
      below are somebody else's folders and not ours to put things in.

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

function Get-CacheSearchDir {
    <#
      Where cached files are LOOKED FOR, nearest first.

      Wider than where they are written, because the folder somebody shares into
      a Sandbox is usually the one CONTAINING this repository, not this
      repository itself - the installers get dropped beside the clone, not
      inside it. Insisting they live in exactly one subdirectory would mean
      re-downloading a file already sitting one level up.

      Only these four, and only ever read from. This does not go hunting through
      Downloads or the rest of the disk: a cache that quietly installs something
      it found lying around is worse than no cache.
    #>
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
    <#
      A cached file, by our name for it or by any name it plausibly arrived
      under. $null if there is not one.

      The size floor is checked on the way OUT as well as in. A cache is a
      directory anyone can drop a file into, and half a download left behind by
      a crashed run looks exactly like a real one until it is installed.
    #>
    param([string]$Name, [double]$MinMB = 0, [string[]]$Match)

    foreach ($dir in (Get-CacheSearchDir)) {
        # Our own name first, everywhere, before any pattern anywhere: an exact
        # match is never the wrong file, and a pattern occasionally is.
        $candidates = @()
        $exact = Join-Path $dir $Name
        if (Test-Path $exact -PathType Leaf) { $candidates += (Get-Item -LiteralPath $exact) }
        foreach ($pattern in @($Match)) {
            if (-not $pattern) { continue }
            $candidates += @(Get-ChildItem -LiteralPath $dir -File -Filter $pattern -ErrorAction SilentlyContinue)
        }

        foreach ($f in $candidates) {
            if ($MinMB -gt 0 -and ($f.Length / 1MB) -lt $MinMB) {
                Write-Warn2 ("{0} is only {1:N1} MB - too small to be the real file, ignoring it." -f $f.Name, ($f.Length / 1MB))
                continue
            }
            return $f.FullName
        }
    }
    return $null
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
        $kept = Join-Path $dir $Name
        Copy-Item -LiteralPath $Path -Destination $kept -Force -ErrorAction Stop
        Write-Info "Kept as $kept"
        Write-Info "  - the next run will not download it again."
        return $true
    } catch {
        Write-Info "Could not keep a copy in downloadCache: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        return $false
    }
}

# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

function Get-BootstrapSpec {
    <# One entry of $RfcBootstrapFiles by name, so callers can ask about a
       single file without repeating its patterns and size floor. #>
    param([string]$Name)
    $RfcBootstrapFiles | Where-Object { $_.Name -eq $Name } | Select-Object -First 1
}

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

    $hit = Get-CachedFile -Name $File.Name -MinMB $File.MinMB -Match $File.Match
    if ($hit) {
        Copy-Item -LiteralPath $hit -Destination $Path -Force
        $mb = (Get-Item $Path).Length / 1MB
        # Named in full, because "using the cached copy" is not a claim anyone
        # should have to take on trust when the alternative is 207 MB.
        Write-Info ("{0}: using {1} ({2:N1} MB, no download)." -f $File.Label, $hit, $mb)
        return $mb
    }

    Write-Info ("Downloading {0}..." -f $File.Label)
    $mb = Get-RemoteFile -Url $File.Url -Path $Path -MinMB $File.MinMB
    Write-Info ("  {0:N1} MB" -f $mb)
    [void](Save-ToCache -Path $Path -Name $File.Name)
    return $mb
}

function Test-BootstrapComplete {
    <# Is every file needed to install winget offline already on disk? #>
    foreach ($f in $RfcBootstrapFiles) {
        if ($f.Name -eq 'windowsappruntime.exe') { continue }   # handled below
        if (-not (Get-CachedFile -Name $f.Name -MinMB $f.MinMB -Match $f.Match)) { return $false }
    }
    # Either form of the runtime will do.
    if (Get-CachedFile -Name $RfcRuntimeMsix.Name -MinMB $RfcRuntimeMsix.MinMB -Match $RfcRuntimeMsix.Match) { return $true }
    $exe = Get-BootstrapSpec 'windowsappruntime.exe'
    return [bool](Get-CachedFile -Name $exe.Name -MinMB $exe.MinMB -Match $exe.Match)
}

function Expand-MirrorBundle {
    <#
      Fetch the mirror archive and take the packages out of it. Returns how
      many were extracted; 0 means the caller should fall back to Microsoft.

      Extraction only. Nothing from the archive is ever executed - see
      $RfcMirrorBundle for why that line matters and why Add-AppxPackage's
      signature check is what makes taking the packages safe.

      The archive itself is not kept. It carries all three architectures, so
      most of its 340 MB is for machines this is not, and the four files pulled
      out of it are the thing worth keeping.
    #>
    $dir = Get-CacheDir -Create
    if (-not $dir) {
        Write-Info "Nowhere to extract to; skipping the offline bundle."
        return 0
    }

    $tmp = Join-Path $env:TEMP ("rfc-mirror-" + [Guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null
    $zip = Join-Path $tmp 'winget-offline.zip'
    $taken = 0

    try {
        Write-Info ("Downloading the {0} (about 340 MB)..." -f $RfcMirrorBundle.Label)
        Write-Info ("  {0}" -f $RfcMirrorBundle.About)
        [void](Get-RemoteFile -Url $RfcMirrorBundle.Url -Path $zip -MinMB $RfcMirrorBundle.MinMB)

        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction Stop
        $archive = [IO.Compression.ZipFile]::OpenRead($zip)
        try {
            foreach ($want in (Get-MirrorContents)) {
                # Separators normalised before matching. The zip format says
                # entry names use '/', and this archive does - but plenty of
                # tools write '\' instead, and the only pattern here with a
                # directory in it is the runtime, so a mismatch would silently
                # cost 40 MB of package and fetch Microsoft's 102 MB installer
                # instead. Cheaper to normalise than to depend on it.
                $entry = $archive.Entries |
                         Where-Object { ($_.FullName -replace '\\', '/') -like $want.Pattern } |
                         Select-Object -First 1
                if (-not $entry) {
                    Write-Warn2 "  $($want.As): nothing matching in the archive."
                    continue
                }
                [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, (Join-Path $dir $want.As), $true)
                Write-Info ("  {0}  <-  {1} ({2:N1} MB)" -f $want.As, $entry.FullName, ($entry.Length / 1MB))
                $taken++
            }
        } finally {
            $archive.Dispose()
        }

        if ($taken -gt 0) {
            Write-Ok "$taken package(s) extracted into downloadCache."
            Write-Info "Kept there, so this download happens once and not again."
        }
    } catch {
        Write-Warn2 "  offline bundle failed: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        Write-Info  "  Falling back to Microsoft's own downloads."
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }

    return $taken
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

      A manifest with no installer beside it is returned too, with Installer
      set to $null. That is not damage - it is a note. Some packages cannot be
      installed from disk at all (winget extracts them rather than running an
      installer), and the way that is recorded is to keep the 2 KB manifest and
      drop the payload. Without it, every run would download a hundred megabytes
      to rediscover the same thing and throw it away again.
    #>
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

        # The installer is the file beside the manifest sharing its basename.
        # `winget download` writes exactly that pair, flat, one installer per
        # manifest - which is why the flat regexes below are safe: a merged
        # manifest from a download has a single Installers: entry.
        $base = [System.IO.Path]::GetFileNameWithoutExtension($yaml.Name)
        $installer = Get-ChildItem -LiteralPath $yaml.DirectoryName -File -Filter "$base.*" -ErrorAction SilentlyContinue |
                     Where-Object { $_.Extension -ne '.yaml' } | Select-Object -First 1

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
            Type         = ([regex]::Match($text, '(?m)^\s*InstallerType:\s*(\S+)\s*$')).Groups[1].Value.ToLower()
            Nested       = ([regex]::Match($text, '(?m)^\s*NestedInstallerType:\s*(\S+)\s*$')).Groups[1].Value.ToLower()
            Silent       = $silent
            SuccessCodes = $codes
        }
    }
    return $null
}

function Get-PackageToCache {
    <#
      Fetch a winget package INTO the cache, and hand back what was fetched.
      $null if it could not be, for any reason.

      This is the difference between a cache that fills itself and one that
      never does. `winget install` downloads into winget's own temp directory
      and deletes it afterwards, so a hundred runs install a hundred fresh
      downloads and downloadCache stays empty - which is exactly what happened.
      `winget download` puts the installer somewhere we choose and writes the
      manifest beside it, and that manifest is what makes the file installable
      later without anybody guessing at silent switches.

      So: one download, into the cache, and the install runs from there. The
      run after this one costs nothing.

      Packages winget extracts rather than installs cannot be used from disk.
      Those keep their manifest and lose their payload - see Find-CachedPackage
      for why that is a note rather than damage.
    #>
    param([string]$Id)

    $dir = Get-CacheDir -Create
    if (-not $dir) { return $null }

    # winget writes progress to stderr in normal operation, and merging a native
    # command's stderr under EAP 'Stop' is itself a terminating error.
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & winget download -e --id $Id --source winget -d $dir `
            --accept-package-agreements --accept-source-agreements
        $code = $LASTEXITCODE
    } catch {
        Write-Warn2 "  could not download it: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        return $null
    } finally {
        $ErrorActionPreference = $prevEAP
    }

    if ($code -ne 0) {
        Write-Warn2 "  winget download exited $code."
        return $null
    }

    $pkg = Find-CachedPackage -Id $Id
    if (-not $pkg -or -not $pkg.Installer) {
        Write-Warn2 "  downloaded, but no installer and manifest pair turned up."
        return $null
    }

    if (-not (Test-CachedPackageUsable -Package $pkg)) {
        Remove-Item -LiteralPath $pkg.Installer -Force -ErrorAction SilentlyContinue
        Write-Info "  that is a '$($pkg.Type)' package - winget extracts those rather than"
        Write-Info "  installing them, so it cannot be run from disk. Keeping the manifest"
        Write-Info "  as a note, so later runs do not download it again to find that out."
        return $null
    }

    Write-Info "  kept in downloadCache - the next run will not download it again."
    return $pkg
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

    if (-not $Package -or -not $Package.Installer) { return $false }
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
