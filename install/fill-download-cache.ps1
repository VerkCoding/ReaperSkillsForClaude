<#
.SYNOPSIS
  Download everything the setup needs, now, into downloadCache.

.DESCRIPTION
  [1] Install Everything downloads as it goes. That is right for one machine on
  a normal connection, and wrong in three situations this exists for:

    * The machine that has to be set up cannot reach the internet, or is behind
      something that blocks the vendors. Fill the cache on a machine that can,
      copy the folder across, and [1] installs from disk.

    * The machine gets wiped between runs - Windows Sandbox, a test VM, a lab
      image. Without a cache every single run re-downloads the same 252 MB
      winget bundle, and repeating that often enough is how an address gets
      rate-limited and then blocked. Which is not hypothetical; it is why this
      script was written.

    * Several machines are being set up and downloading once is simply kinder
      than downloading five times.

  What it fetches
  ---------------
    winget 1.8.1911 and its two dependencies   (~265 MB, from Microsoft)
    Python, Git, REAPER, Claude Desktop        (via `winget download`)

  The winget packages come from pinned Microsoft URLs. The versions are not
  incidental - see $RfcBootstrapFiles in lib-cache.ps1.

  Anything already in the cache is left alone, so this is safe to re-run and
  cheap when it has nothing to do. Nothing is installed. Nothing on this machine
  is changed outside the downloadCache folder.

  What it will not fetch
  ----------------------
  Packages winget extracts rather than installs - Claude Code is one - are
  downloaded, found to be unusable from disk, and deleted again with a note.
  Installing those means reproducing winget's own bookkeeping (its Packages
  directory, its Links folder, its PATH entry, its tracking file), and a
  half-right version of that leaves a `claude` that winget cannot repair. Those
  still need a real winget on the target machine.

  Files you already have
  ----------------------
  Anything already on disk is used where it lies and not downloaded again -
  including files downloaded by hand under their own names, and files sitting
  beside this repository rather than inside it. That last one matters: the
  folder shared into a Sandbox is normally the one CONTAINING the clone, so
  that is where installers actually accumulate.

  Those are left where they are rather than copied, because duplicating 207 MB
  to tidy it up is a poor trade. Pass -Consolidate when the point is to carry
  one self-contained folder to another machine.

.PARAMETER Force
  Re-download files that are already cached.

.PARAMETER Consolidate
  Copy files found outside downloadCache into it, so the folder can be carried
  to another machine on its own.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File fill-download-cache.ps1
#>
[CmdletBinding()]
param([switch]$Force, [switch]$Consolidate)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # PS 5.1 progress bars make downloads several times slower

function Write-Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  .      $m" -ForegroundColor Gray }
function Write-Warn2($m){ Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

. (Join-Path $PSScriptRoot 'lib-apps.ps1')
. (Join-Path $PSScriptRoot 'lib-cache.ps1')

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  REAPER for Claude - prepare offline files" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

$dir = Get-CacheDir -Create
if (-not $dir) {
    Write-Err "Could not create $RfcCacheDir"
    Write-Info "This needs somewhere to put the files. Check the folder is writable."
    exit 1
}
Write-Info "Into: $dir"
Write-Info "Nothing is installed and nothing outside that folder is touched."

try {
    # Old Windows PowerShell still negotiates TLS 1.0, which these hosts refuse.
    [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
} catch {
    Write-Warn2 "Could not force TLS 1.2; continuing."
}

$failed  = @()
$outside = @()   # found, but not inside downloadCache

function Register-CacheLocation {
    <#
      Note where a file that already exists actually lives, and copy it into
      downloadCache if -Consolidate says to. Returns $true when it ends up
      inside the cache.

      One place for this, because every kind of file that can already be here
      has to be accounted for the same way. A file left out of it is a file
      -Consolidate does not copy and the summary does not mention - which is a
      run that prints CACHE READY over a folder that is missing something, and
      only fails later on the offline machine where nothing can fix it.
    #>
    param([string]$Path, [string]$Name, [string]$Label)

    $mb = (Get-Item -LiteralPath $Path).Length / 1MB
    if ((Split-Path -Parent $Path) -ieq $dir) {
        Write-Ok ("{0}: already here ({1:N1} MB)." -f $Label, $mb)
        return $true
    }

    # Found, but not in downloadCache - downloaded by hand, or left beside the
    # repository rather than inside it. Not downloaded again either way.
    Write-Ok ("{0}: already on disk ({1:N1} MB), not downloaded." -f $Label, $mb)
    Write-Info "  $Path"
    if (-not $Consolidate) {
        # Deliberately not copied by default. Duplicating 207 MB to tidy it is a
        # poor trade when the file is already somewhere the setup looks, and the
        # folder it is in is usually the one being shared.
        Write-Info "  Outside downloadCache, so it travels only if that folder does."
        Write-Info "  Re-run with -Consolidate to copy it in."
        $script:outside += $Label
        return $false
    }
    if (Save-ToCache -Path $Path -Name $Name) {
        Write-Ok ("{0}: copied into downloadCache." -f $Label)
        return $true
    }
    Write-Warn2 ("{0}: could not be copied in; leaving it where it is." -f $Label)
    return $false
}

# ---------------------------------------------------------------------------
# 1. The winget client. The big one, and the one that matters most: it is what
#    a wiped machine needs before it can fetch anything else at all.
# ---------------------------------------------------------------------------
Write-Step "winget and its dependencies"

# -Force clears the cache ONCE, here, before anything is fetched.
if ($Force) {
    foreach ($n in $RfcBootstrapFiles.Name) {
        Remove-Item (Join-Path $dir $n) -Force -ErrorAction SilentlyContinue
    }
}

$missing = Get-MissingBootstrap
if ($missing.Count -eq 0) {
    Write-Ok "Every winget package is already on disk."
} else {
    Write-Info ("Missing: {0}." -f ($missing -join ', '))
}

foreach ($f in $RfcBootstrapFiles) {
    $have = Get-CachedFile -Name $f.Name -MinMB $f.MinMB -Match $f.Match
    if ($have) {
        [void](Register-CacheLocation -Path $have -Name $f.Name -Label $f.Label)
        continue
    }

    # Downloaded to TEMP and copied in by Get-BootstrapFile, so a download that
    # dies halfway cannot leave a truncated file in the cache under a name every
    # later run will trust.
    $tmp = Join-Path $env:TEMP ("rfc-fill-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    try {
        [void](Get-BootstrapFile -File $f -Path $tmp)
        Write-Ok "$($f.Label) cached."
    } catch {
        Write-Err "$($f.Label): $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        $failed += $f.Label
    } finally {
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
    }
}

# ---------------------------------------------------------------------------
# 2. The applications, through winget itself.
#
#    `winget download` writes the installer and a merged manifest beside it,
#    and that manifest is the point: it carries the silent switches and success
#    codes for the file next to it. It is what lets setup-all install REAPER
#    from disk without anybody having to know that REAPER's installer takes /S.
# ---------------------------------------------------------------------------
Write-Step "Applications"

$wingetOk = $false
if (Get-Command winget -ErrorAction SilentlyContinue) {
    try { $null = & winget --version 2>&1; $wingetOk = ($LASTEXITCODE -eq 0) } catch { }
}

if (-not $wingetOk) {
    Write-Warn2 "winget does not work on THIS machine, so the applications cannot be"
    Write-Warn2 "downloaded here. The winget files above are cached either way, which"
    Write-Warn2 "is the part a machine without winget cannot get for itself."
} else {
    foreach ($app in (Get-AppList)) {
        $existing = Find-CachedPackage -Id $app.Id
        if ($existing -and -not $Force) {
            if ($existing.Installer) {
                Write-Ok "$($app.Name) $($existing.Version): already cached."
            } else {
                # Manifest kept, payload dropped: an earlier run established
                # this one cannot be installed from disk. Downloading it again
                # would only re-establish that.
                Write-Info "$($app.Name): not installable from disk - noted, skipping."
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

        # $null covers two different things, and they are not both failures.
        # A package winget extracts rather than installs leaves its manifest
        # behind as a note; anything else genuinely did not download.
        $after = Find-CachedPackage -Id $app.Id
        if ($after -and -not $after.Installer) {
            Write-Info "  That one still needs a working winget on the target machine."
        } else {
            $failed += $app.Name
        }
    }
}

# ---------------------------------------------------------------------------
# 3. Say what is there, in the folder itself as well as on screen. Somebody
#    finding a 400 MB directory in six months deserves to know what it is.
# ---------------------------------------------------------------------------
$files = @(Get-ChildItem $dir -File -ErrorAction SilentlyContinue)
$total = ($files | Measure-Object -Property Length -Sum).Sum / 1MB

$readme = @"
REAPER for Claude - downloadCache
=================================

Installer files, kept so a re-run does not download them again.

Made by install\fill-download-cache.ps1. Used automatically by
RunThisToStart.bat [1] Install Everything: anything in here is installed from
disk instead of being fetched.

Safe to delete. Deleting it only means the next install downloads again.

To set up a machine that cannot reach the internet, copy this whole folder to
the same place beside RunThisToStart.bat on that machine.

Last filled: $((Get-Date).ToString('yyyy-MM-dd HH:mm'))
"@
try {
    [System.IO.File]::WriteAllText((Join-Path $dir 'README.txt'), $readme,
        (New-Object System.Text.UTF8Encoding $false))
} catch { }

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
if ($failed.Count -eq 0) {
    Write-Host "  CACHE READY" -ForegroundColor Green
} else {
    Write-Host "  CACHE PARTLY FILLED" -ForegroundColor Yellow
}
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""
Write-Info ("{0} file(s), {1:N0} MB in {2}" -f $files.Count, $total, $dir)

if ($outside.Count -gt 0) {
    Write-Host ""
    Write-Info "Already on disk, outside downloadCache: $($outside -join ', ')"
    Write-Info "The setup finds them there, so nothing needs doing. To make"
    Write-Info "downloadCache carry everything on its own, re-run with -Consolidate."
}

if ($failed.Count -gt 0) {
    Write-Host ""
    Write-Warn2 "These could not be downloaded: $($failed -join ', ')"
    Write-Info  "Run this again later - whatever did work is kept and skipped."
}

Write-Host ""
Write-Info "Run [1] Install Everything on this machine, or copy the downloadCache"
Write-Info "folder beside RunThisToStart.bat on the machine you are setting up."
Write-Host ""

exit $(if ($failed.Count -eq 0) { 0 } else { 1 })
