<#
.SYNOPSIS
  Install or repair the winget client.

.DESCRIPTION
  winget ships with App Installer, which is present on current Windows 11 and
  most Windows 10 installs - but not on a fresh Windows Server, not on an image
  built without the Store, and not always after an in-place upgrade. When it is
  missing, the Python install step has nothing to run.

  Three routes, each falling through to the next:

    1. Register the App Installer package already on the machine. Instant, needs
       no network, and fixes the common case where the package is provisioned
       but not registered for this user.

    2. Deploy the packages directly - the bundle plus its Windows App Runtime,
       VCLibs and UI.Xaml. Needs nothing but HTTPS: no Gallery, no NuGet
       provider, no Store. Nothing at all when the files are cached.

    3. Microsoft's documented PSGallery bootstrap, as the fallback:

           Install-PackageProvider -Name NuGet -Force
           Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery
           Repair-WinGetPackageManager

  Where route 2's packages come from
  ----------------------------------
    a. downloadCache, if they are there. Nothing is downloaded.
    b. $RfcMirrorBundle in lib-cache.ps1 - a third party's archive holding all
       four - but only when the 207 MB bundle itself is among what is missing,
       since it is 340 MB and everything else in it is available for less.
       Extraction only; nothing in it is run, and every package taken out is
       checked to be Microsoft-signed before it is kept.
    c. Microsoft's own links, file by file, for whatever is still missing.

  Why route 2 is not last any more
  --------------------------------
  It used to be, on the grounds that it costs hundreds of MB where the Gallery
  bootstrap costs a few. That accounting was wrong: Repair-WinGetPackageManager
  downloads the same 207 MB bundle from the same host, and the few MB is only
  the module that then goes and fetches it.

  Route 2 leaves its files in downloadCache. The Gallery downloads into
  somewhere private and deletes it afterwards. On a machine that is wiped
  between runs that is the difference between paying 207 MB once and paying it
  every single time, which is how an address gets rate-limited and then blocked
  outright. Which is not hypothetical - it is what happened here.

  So route 2 goes second whether or not anything is cached, and the Gallery is
  what to try when deployment is refused outright by policy and route 2 cannot
  work at all.

  Two adjustments to route 2, both about privilege:

    * -AllUsers requires an elevated session and fails without one. It is passed
      only when this is actually running as administrator, so a normal user gets
      a per-user repair instead of an error.
    * Install-Module defaults to an AllUsers scope, which also needs elevation.
      The scope is chosen to match.

  Everything past route 1 needs a working internet connection, unless the cache
  has been filled, and the whole thing takes a minute or two.

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

# Dot-sourced after the Write-* helpers, which it uses to report cache hits.
. (Join-Path $PSScriptRoot 'lib-cache.ps1')

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

function Exit-Working {
    <# The one success ending, said the same way wherever it is reached. #>
    Write-Ok "winget is working: $(& winget --version)"
    if (-not $Embedded) {
        Write-Host ""
        Write-Host "  Close this window and run RunThisToStart.bat again so it picks" -ForegroundColor Gray
        Write-Host "  up the new command." -ForegroundColor Gray
    }
    exit 0
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  winget - install or repair" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

if ((Test-Winget) -and -not $Force) {
    Write-Ok "winget already works ($(& winget --version)). Nothing to do."
    exit 0
}

function Install-WingetDirect {
    <#
      Install winget from packages on disk, fetching whatever is not there yet.

      This is the route that actually works where winget is genuinely absent -
      Windows Sandbox, Windows Server, a stripped image. It needs nothing but
      HTTPS: no PowerShell Gallery, no NuGet provider, no Store. With a filled
      downloadCache it needs not even that.

      Where the files come from, in order:

        1. downloadCache, if they are already there. Nothing is downloaded.
        2. The offline bundle - one archive with all of them. Extraction only;
           nothing in it is executed. See $RfcMirrorBundle.
        3. Microsoft's own links, file by file, for whatever is still missing.

      Four files, not three. winget also declares Microsoft.WindowsAppRuntime.1.8,
      and without it the bundle fails with 0x80073CF3 naming a "framework that
      could not be found", which reads like a corrupt download. It comes in two
      forms: a 40 MB framework .msix, which is simply another dependency, or
      Microsoft's 102 MB setup executable, which has to be run. The package is
      preferred when there is one - smaller, and it makes the whole install a
      single Add-AppxPackage call.
    #>
    $tmp = Join-Path $env:TEMP ("winget-setup-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    $spec = @{}
    foreach ($f in $RfcBootstrapFiles) { $spec[$f.Name] = $f }

    try {
        # The archive is one download instead of four, so it is preferred - but
        # only for what is actually missing, and only when the 207 MB bundle is
        # among it. Fetching 340 MB to obtain a 7 MB VCLibs would be the exact
        # inversion this exists to avoid.
        $missing = Get-MissingBootstrap
        if ($missing.Count -eq 0) {
            Write-Info "Every package is already on disk. Nothing to download."
        } elseif (Test-MirrorWorthwhile -Missing $missing) {
            [void](Expand-MirrorBundle -Only $missing)
        } else {
            Write-Info ("Missing: {0}. Small enough to fetch individually." -f ($missing -join ', '))
        }

        foreach ($name in @("VCLibs.$RfcArch.appx", "UIXaml.$RfcArch.appx", 'winget.msixbundle')) {
            [void](Get-BootstrapFile -File $spec[$name] -Path (Join-Path $tmp $name))
        }
        $deps = @((Join-Path $tmp "VCLibs.$RfcArch.appx"), (Join-Path $tmp "UIXaml.$RfcArch.appx"))

        # The runtime, in whichever form is on disk - and INSTALLED, not merely
        # offered.
        #
        # It has to actually be on the machine before the bundle goes in,
        # because the retry below deliberately drops the dependency list. Handing
        # the runtime over only as a -DependencyPath entry meant that when the
        # first call failed for any reason, the retry ran against a machine with
        # no runtime at all and failed with 0x80073CF3 - the very error the
        # runtime is there to prevent. The executable form was always installed
        # first for this reason; the package form must be too.
        $runtime = Get-CachedRuntime
        if (-not $runtime) {
            # Nothing cached in either form: fetch Microsoft's installer.
            #
            # Taken from where it just landed rather than by asking the cache
            # again. Get-BootstrapFile keeps a copy through Save-ToCache, which
            # is best effort by design - a read-only or full downloadCache is an
            # ordinary state - and re-deriving the path through Get-CachedRuntime
            # would then find nothing and skip installing a 102 MB file sitting
            # in $tmp, leaving the bundle to fail with the 0x80073CF3 this is
            # here to prevent.
            $exeSpec = $spec["windowsappruntime.$RfcArch.exe"]
            $fetched = Join-Path $tmp $exeSpec.Name
            [void](Get-BootstrapFile -File $exeSpec -Path $fetched)
            $runtime = [pscustomobject]@{ Path = $fetched; Spec = $exeSpec; IsPackage = $false }
        }

        if ($runtime -and $runtime.IsPackage) {
            $local = Join-Path $tmp $runtime.Spec.Name
            if ($runtime.Path -ne $local) { Copy-Item -LiteralPath $runtime.Path -Destination $local -Force }
            Write-Info ("Installing Windows App Runtime ({0:N1} MB package)..." -f ((Get-Item $local).Length / 1MB))
            try {
                Add-AppxPackage -Path $local -ErrorAction Stop
            } catch {
                # A newer runtime already present is the usual cause, and is
                # fine. Passing it below as a dependency covers the rest.
                Write-Info "  $($_.Exception.Message.Split([Environment]::NewLine)[0])"
            }
            $deps += $local
        } elseif ($runtime) {
            # Microsoft's form: an installer, not a package, so it cannot go
            # through Add-AppxPackage with the others and has to run first.
            $local = Join-Path $tmp $runtime.Spec.Name
            if ($runtime.Path -ne $local) { Copy-Item -LiteralPath $runtime.Path -Destination $local -Force }
            Write-Info "Installing Windows App Runtime..."
            $proc = Start-Process -FilePath $local -ArgumentList '--quiet' -Wait -PassThru
            if ($proc.ExitCode -ne 0) {
                Write-Warn2 ("  runtime installer exited {0}; continuing anyway" -f $proc.ExitCode)
            }
        } else {
            Write-Warn2 "No Windows App Runtime in either form; the bundle may refuse to install."
        }

        # One call, with the dependencies passed as dependencies.
        #
        # Installing them as separate Add-AppxPackage calls is what fails with
        # 0x80073CF3, "package failed updates, dependency or conflict
        # validation": each call is validated on its own, so the deployment
        # engine never gets to match the bundle's declared dependencies against
        # the files provided, and a version or architecture that does not line
        # up is only discovered at the end. -DependencyPath hands it everything
        # at once and lets it do that matching itself.
        #
        # This call also validates the MSIX signature chain, so anything that is
        # not the package Microsoft signed does not deploy - the second of the
        # two checks applied to anything taken from the archive.
        $bundle = Join-Path $tmp 'winget.msixbundle'

        # Offering a dependency the machine already has at a NEWER version is
        # itself a conflict, so a failure is retried with fewer of them - but in
        # steps, rather than dropping straight to none.
        #
        # The runtime goes first because it is the one most likely to collide: it
        # has just been installed on its own above, so the copy offered here is
        # redundant by the time this runs. Dropping all three in one go would
        # also give up VCLibs and UI.Xaml, which on a stripped image are not
        # present at any version and cannot be given up at all.
        $attempts = @(
            @{ Deps = $deps;         How = 'with its dependencies' }
            @{ Deps = $deps[0..1];   How = 'without the runtime, which is installed already' }
            @{ Deps = @();           How = 'using only what is already on the machine' }
        )

        for ($i = 0; $i -lt $attempts.Count; $i++) {
            $a = $attempts[$i]
            # Skip a step that is identical to the previous one - there is no
            # third entry to drop when the runtime never made it into $deps.
            if ($i -gt 0 -and (@($a.Deps) -join '|') -eq (@($attempts[$i - 1].Deps) -join '|')) { continue }

            Write-Info "Installing winget $($a.How)..."
            try {
                if (@($a.Deps).Count -gt 0) {
                    Add-AppxPackage -Path $bundle -DependencyPath $a.Deps -ErrorAction Stop
                } else {
                    Add-AppxPackage -Path $bundle -ErrorAction Stop
                }
                return $true
            } catch {
                if ($i -eq $attempts.Count - 1) { throw }
                Write-Info "  $($_.Exception.Message.Split([Environment]::NewLine)[0])"
            }
        }
        return $true
    } finally {
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    }
}

function Invoke-DirectRoute {
    <#
      Route 3, with its error reporting. Returns $true only if winget answers
      afterwards, so the caller can carry on to the next route if it does not.
    #>
    try {
        if (Install-WingetDirect) {
            if (Test-Winget) { return $true }
            Write-Info "Installed, but winget does not respond yet."
        }
    } catch {
        $msg = $_.Exception.Message.Split([Environment]::NewLine)[0]
        Write-Warn2 "Direct install did not succeed: $msg"
        switch -Regex ($msg) {
            '0x80073CF3' { Write-Info "  A dependency is missing or conflicts - not a bad download." }
            '0x80073CF9' { Write-Info "  'Install failed' - often a machine with app deployment restricted." }
            '0x80073D02' { Write-Info "  Another install is in progress. Wait for it, then re-run." }
            '0x80070005' { Write-Info "  Access denied - try running this as administrator." }
            'connection' { Write-Info "  That was the network, not the package. Re-running often just works." }
        }
    }
    return $false
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
    if (Test-Winget) { Exit-Working }
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

$isAdmin = ([Security.Principal.WindowsPrincipal] `
            [Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

Write-Info ("Running " + $(if ($isAdmin) { "elevated" } else { "as a normal user" }))
if (-not $isAdmin) {
    Write-Info "A per-user repair will be attempted. If it fails, re-run this"
    Write-Info "as administrator for a machine-wide one."
}

# ---------------------------------------------------------------------------
# Then deploy the packages, from disk where possible and from the network where
# not. See the .DESCRIPTION for where they come from and in what order.
#
# This used to be last, on the grounds that it costs hundreds of MB where the
# Gallery bootstrap costs a few. That accounting was wrong. Repair-WinGetPackage-
# Manager downloads the very same 207 MB bundle from the very same host - the
# few MB is only the PowerShell module that then goes and fetches it. What this
# route gets for the difference:
#
#   this route     leaves its files on disk, so the next run costs nothing
#   the Gallery    downloads into somewhere private and deletes it afterwards
#
# On a machine that is wiped between runs that is not a small distinction. It is
# the difference between paying 207 MB once and paying it every time, which is
# how an address gets rate-limited and then blocked - which is not hypothetical,
# it is what happened.
#
# So this goes second, cached or not, and the Gallery becomes the fallback for
# the machines where app deployment is restricted and this cannot work at all.
# ---------------------------------------------------------------------------
# Said plainly, because the whole argument for using a third-party mirror is
# that it is disclosed. A line claiming this comes from Microsoft while it
# fetches a stranger's archive would give that away for nothing.
$missingNow = Get-MissingBootstrap
if ($missingNow.Count -eq 0) {
    Write-Info "Every winget package is already on disk. Installing from them."
} elseif (Test-MirrorWorthwhile -Missing $missingNow) {
    Write-Info "Installing winget. Missing: $($missingNow -join ', ')"
    Write-Info "Source: $($RfcMirrorBundle.About)"
    Write-Info "  $($RfcMirrorBundle.Url)"
    Write-Info "That is a third party's copy of Microsoft's own packages, used because"
    Write-Info "it is one download rather than four. Only the packages are taken from"
    Write-Info "it, each checked to be Microsoft-signed; nothing in it is run. If any"
    Write-Info "of that fails, Microsoft's own links are used instead."
} else {
    Write-Info "Installing winget from Microsoft. Missing: $($missingNow -join ', ')"
}
Write-Info "Whatever is downloaded is kept in downloadCache, so this happens once."
if (Invoke-DirectRoute) { Exit-Working }

# ---------------------------------------------------------------------------
# Then PowerShell Gallery - Microsoft's own documented bootstrap.
#
# The fallback now, not the main route: it is what to try when the deployment
# above is refused outright, which is a real state on machines with app
# installation restricted by policy. Everything here is best-effort.
# ---------------------------------------------------------------------------
$galleryOk = $true

try {
    Write-Info "Installing the NuGet package provider..."
    Install-PackageProvider -Name NuGet -Force -Scope $(if ($isAdmin) { 'AllUsers' } else { 'CurrentUser' }) | Out-Null
    Write-Ok "NuGet provider ready."
} catch {
    # The inbox PackageManagement module failing to fetch its provider list from
    # go.microsoft.com. Common on Windows Sandbox, Windows Server, and behind a
    # proxy - and the reason the direct download below exists.
    Write-Warn2 "Could not install the NuGet provider: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
    $galleryOk = $false
}

if ($galleryOk) {
    try {
        Write-Info "Installing the Microsoft.WinGet.Client module from PSGallery..."
        Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery `
                       -Scope $(if ($isAdmin) { 'AllUsers' } else { 'CurrentUser' }) | Out-Null
        Write-Ok "Module installed."
    } catch {
        Write-Warn2 "Could not install Microsoft.WinGet.Client: $($_.Exception.Message.Split([Environment]::NewLine)[0])"
        $galleryOk = $false
    }
}

if ($galleryOk) {
    # Retried, and this is the one that most needs it.
    #
    # Repair-WinGetPackageManager downloads the winget release from GitHub, so
    # it fails with "An error occurred while sending the request" on any network
    # hiccup - seen on a Sandbox run that then had no package manager at all,
    # and so installed neither REAPER nor Claude. That is a dropped connection,
    # not a machine that cannot run winget, and asking again usually settles it.
    #
    # Three attempts, because by here there is nothing better to spend the time
    # on: the direct download has already been tried and failed.
    $attempts = 3
    $repaired = $false
    for ($try = 1; $try -le $attempts; $try++) {
        try {
            Write-Info "Repairing the winget client. This can take a couple of minutes..."
            if ($isAdmin) { Repair-WinGetPackageManager -AllUsers }
            else          { Repair-WinGetPackageManager }
            $repaired = $true
            break
        } catch {
            $msg = $_.Exception.Message.Split([Environment]::NewLine)[0]
            if ($try -lt $attempts) {
                Write-Warn2 "  attempt $try failed ($msg); retrying..."
                Start-Sleep -Seconds (5 * $try)
            } else {
                Write-Warn2 "Repair-WinGetPackageManager failed after $try attempts: $msg"
                if (-not $isAdmin) { Write-Info "  Some repairs need administrator rights." }
            }
        }
    }
    if (-not $repaired) { $galleryOk = $false }
}

if (Test-Winget) { Exit-Working }

Write-Host ""
Write-Err "Could not get winget working on this machine."
Write-Host ""
Write-Info "The setup continues without it: Python still installs directly from"
Write-Info "python.org, which is the only thing it genuinely cannot do without."
Write-Info "REAPER and Claude will need installing by hand -"
Write-Info "    REAPER  https://www.reaper.fm/download.php"
Write-Info "    Claude  https://claude.ai/download"
Write-Info "then run [1] again; it fills in only what is missing."
Write-Host ""
Write-Info "If the failures above mention the connection, this is worth simply"
Write-Info "retrying - a 300 MB download over a flaky link fails often enough."
Write-Info "If they mention being blocked or refused, run [3] Prepare Offline"
Write-Info "Files on a machine that can reach the internet and copy the"
Write-Info "downloadCache folder across."
Write-Host ""
Write-Info "Install 'App Installer' from the Microsoft Store, or install Python"
Write-Info "by hand from https://www.python.org/downloads/ with 'Add python.exe"
Write-Info "to PATH' ticked."
exit 1
