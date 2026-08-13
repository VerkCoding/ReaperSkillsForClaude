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

    2. Deploy the packages directly - the winget bundle plus VCLibs and
       UI.Xaml. Needs nothing but HTTPS: no Gallery, no NuGet provider, no
       Store. Nothing at all when the files are cached.

    3. Microsoft's documented PSGallery bootstrap, as the fallback:

           Install-PackageProvider -Name NuGet -Force
           Install-Module -Name Microsoft.WinGet.Client -Force -Repository PSGallery
           Repair-WinGetPackageManager

  Where route 2's packages come from
  ----------------------------------
    a. downloadCache, if they are there. Nothing is downloaded.
    b. Microsoft's own links otherwise, file by file, each pinned to a specific
       version. See $RfcBootstrapFiles in lib-download-cache.ps1 for why the versions
       matter and are not free to move.

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
  powershell -ExecutionPolicy Bypass -File install-winget.ps1
#>
[CmdletBinding()]
param(
    [switch]$Force,
    # Set by install-everything.ps1. The advice to close the window and start again is
    # right when this is run on its own from the menu, and wrong mid-install:
    # the caller re-checks winget itself and carries straight on, so telling the
    # user to restart makes a working run look like a failed one.
    [switch]$Embedded
)

$ErrorActionPreference = 'Stop'
$ProgressPreference    = 'SilentlyContinue'   # progress bars slow this down a lot

# How this talks, and the log it writes. Same function names the rest of
# this file already calls - see lib-console.ps1.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

# Dot-sourced after the Write-* helpers, which it uses to report cache hits.
. (Join-Path $PSScriptRoot 'lib-download-cache.ps1')

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

if (-not $Embedded) { [void](Start-RunLog 'winget') }
Write-Banner "winget - install or repair"

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
        2. Microsoft's own links, file by file, for whatever is still missing.

      Three files, and that is the point of pinning winget to 1.8.1911. The
      current release also declares Microsoft.WindowsAppRuntime.1.8, and without
      it the bundle fails with 0x80073CF3 naming a "framework that could not be
      found" - which reads like a corrupt download and is not one. 1.8 predates
      that dependency, so the whole install is one Add-AppxPackage call with two
      ordinary appx dependencies and no separate installer process.
    #>
    $tmp = Join-Path $env:TEMP ("winget-setup-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Force -Path $tmp | Out-Null

    $spec = @{}
    foreach ($f in $RfcBootstrapFiles) { $spec[$f.Name] = $f }

    try {
        # Fetch whatever is not on disk. Three files, each from its own pinned
        # URL - see $RfcBootstrapFiles in lib-download-cache.ps1 for why the versions are
        # not free to move.
        $missing = Get-MissingBootstrap
        if ($missing.Count -eq 0) {
            Write-Info "Every package is already on disk. Nothing to download."
        } else {
            Write-Info ("Missing: {0}." -f ($missing -join ', '))
        }

        foreach ($name in @("VCLibs.$RfcArch.appx", "UIXaml.$RfcArch.appx", 'winget.msixbundle')) {
            [void](Get-BootstrapFile -File $spec[$name] -Path (Join-Path $tmp $name))
        }
        $deps = @((Join-Path $tmp "VCLibs.$RfcArch.appx"), (Join-Path $tmp "UIXaml.$RfcArch.appx"))

        # Register the frameworks individually first, swallowing failures.
        #
        # This is the reference script's own order, and it is the more forgiving
        # one. Once a framework is REGISTERED ON THE MACHINE, the bundle resolves
        # it from there and does not need it handed over at all - which is why
        # that script can install the bundle bare and still work. Failures here
        # are expected and harmless: offering a framework the machine already
        # has at a newer version is itself a conflict, and it is already
        # satisfied, so there is nothing to do.
        #
        # It is not a replacement for -DependencyPath below, it is a belt to its
        # braces. Registration makes them present; -DependencyPath makes them
        # matched. A stripped image needs the first, and a machine with a newer
        # copy of one dependency and none of the other needs both.
        Write-Info "Registering the framework packages..."
        foreach ($dep in $deps) {
            $leaf = Split-Path -Leaf $dep
            try {
                Add-AppxPackage -Path $dep -ErrorAction Stop
                Write-Info "  $leaf registered."
            } catch {
                Write-Info "  $leaf : $($_.Exception.Message.Split([Environment]::NewLine)[0])"
            }
        }

        # Then the bundle. Add-AppxPackage validates the MSIX signature chain,
        # so nothing that is not the package Microsoft signed can deploy.
        $bundle = Join-Path $tmp 'winget.msixbundle'

        # Offering a dependency the machine already has at a NEWER version is
        # itself a conflict, so a failure is retried without them. Handing them
        # over is the right first move on a stripped image, where they are not
        # present at any version; falling back is the right second one on a
        # machine that already has newer copies registered.
        $attempts = @(
            @{ Deps = $deps; How = 'with its dependencies' }
            @{ Deps = @();   How = 'using only what is already on the machine' }
        )

        for ($i = 0; $i -lt $attempts.Count; $i++) {
            $a = $attempts[$i]
            # Skip a step identical to the previous one, which happens when
            # there was nothing left to drop.
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
$missingNow = Get-MissingBootstrap
if ($missingNow.Count -eq 0) {
    Write-Info "Every winget package is already on disk. Installing from them."
} else {
    Write-Info "Installing $((Get-BootstrapSpec 'winget.msixbundle').Label) from Microsoft."
    Write-Info "  Missing: $($missingNow -join ', ')"
    Write-Info "Whatever is downloaded is kept in downloadCache, so this happens once."
}
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
