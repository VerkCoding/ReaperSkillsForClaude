<#
.SYNOPSIS
  Back up or restore configuration files modified by this setup.

.DESCRIPTION
  This script records the initial state of specific configuration files before modification.
  It differentiates between files that existed prior to setup and files created during setup.
  This allows the revert process to restore original files and delete created files.

  Only specific installer-modified configuration files are tracked. User projects, media,
  plugin settings, and external application data remain unmodified.

  A single backup named `original` is created. This prevents subsequent runs from overwriting
  the initial state data, ensuring the revert process always restores the pre-installation state.

.PARAMETER Backup
  Create the original snapshot if one does not exist. Outputs the directory path.

.PARAMETER Restore
  Restore the original snapshot, or the snapshot specified by -From.

.PARAMETER List
  Display existing snapshots.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File backup-restore.ps1 -Backup
#>
[CmdletBinding(DefaultParameterSetName = 'List')]
param(
    [Parameter(ParameterSetName = 'Backup')]  [switch]$Backup,
    [Parameter(ParameterSetName = 'Restore')] [switch]$Restore,
    [Parameter(ParameterSetName = 'Restore')] [string]$From,
    # Required to enable -List parameter syntax and define the default action.
    [Parameter(ParameterSetName = 'List')]    [switch]$List,
    [string]$ReaperResourcePath
)

$ErrorActionPreference = 'Stop'

# Provides standard output and logging functions.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

# Required to access application state verification functions.
. (Join-Path $PSScriptRoot 'lib-app-control.ps1')

# Initializes logging to capture operations for troubleshooting. Standard output is reserved
# for returning the snapshot directory path to the caller. Logging is bypassed for read-only
# operations to prevent log rotation of historical modification data.
$logName = if ($Backup) { 'backup' } elseif ($Restore) { 'restore' } else { $null }
if ($logName) { $null = Start-RunLog $logName }

$Store = Join-Path $env:USERPROFILE '.reaper-for-claude\backups'

function Get-ReaperPath {
    if ($ReaperResourcePath) { return $ReaperResourcePath }
    $p = Join-Path $env:APPDATA 'REAPER'
    if (Test-Path (Join-Path $p 'reaper.ini')) { return $p }
    return $null
}

function Get-TrackedPaths {
    <#
      Centralized definition of all tracked files ensures revert operations remain synchronized
      with installation changes.
    #>
    $paths = @()

    $reaper = Get-ReaperPath
    if ($reaper) {
        $paths += Join-Path $reaper 'reaper.ini'
        $paths += Join-Path $reaper 'reaper-kb.ini'
        $paths += Join-Path $reaper 'reaper-extstate.ini'
        $paths += Join-Path $reaper 'Scripts\__startup.lua'
        $paths += Join-Path $reaper 'Scripts\claude_bridge.lua'
        $paths += Join-Path $reaper 'Scripts\enable_reapy.py'
    }

    $paths += Join-Path $env:APPDATA 'Claude\claude_desktop_config.json'
    $msix = Join-Path $env:LOCALAPPDATA 'Packages'
    if (Test-Path $msix) {
        Get-ChildItem $msix -Filter 'Claude_*' -Directory -ErrorAction SilentlyContinue |
            ForEach-Object {
                $paths += Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude_desktop_config.json'
            }
    }

    # Modified by plugin marketplace operations.
    $paths += Join-Path $env:USERPROFILE '.claude\settings.json'

    return $paths | Select-Object -Unique
}

function Get-InstalledApps {
    <#
      Application presence is recorded to inform the user which software was installed
      by the setup process. Automated uninstallation is avoided to prevent deletion of
      user data or dependencies.
    #>
    $ids = @('Python.Python.3.12', 'Git.Git', 'Cockos.REAPER', 'Anthropic.Claude', 'Anthropic.ClaudeCode')
    $result = [ordered]@{}
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        foreach ($id in $ids) { $result[$id] = $null } # Null indicates indeterminate status.
        return $result
    }
    foreach ($id in $ids) {
        $out = & winget list --id $id --exact --source winget 2>&1 | Out-String
        $result[$id] = ($out -match [regex]::Escape($id))
    }
    return $result
}

function Get-OriginalSnapshot {
    <#
      Retrieves the initial configuration state. For legacy configurations using timestamped
      directories, the oldest directory represents the pre-setup state and is renamed to
      standardize the structure.
    #>
    $original = Join-Path $Store 'original'
    if (Test-Path (Join-Path $original 'manifest.json')) { return $original }

    $oldest = Get-ChildItem $Store -Directory -ErrorAction SilentlyContinue |
              Where-Object { Test-Path (Join-Path $_.FullName 'manifest.json') } |
              Sort-Object Name | Select-Object -First 1
    if (-not $oldest) { return $null }

    try {
        Rename-Item -LiteralPath $oldest.FullName -NewName 'original' -ErrorAction Stop
        Write-Info "Renamed earliest snapshot $($oldest.Name) to original."
        return $original
    } catch {
        # Fallback to current path if rename operation fails.
        return $oldest.FullName
    }
}

# ---------------------------------------------------------------------------
if ($Backup) {
    $dir = Get-OriginalSnapshot
    if ($dir) {
        # Prevents overwriting the pre-installation state with post-installation data.
        $when = try {
            (Get-Content (Join-Path $dir 'manifest.json') -Raw | ConvertFrom-Json).created
        } catch { 'unknown' }
        Write-Ok "Original configuration backup exists. Created: $when."
        Write-Info "Retaining existing backup for revert operations."
        Write-Output $dir
        exit 0
    }

    $dir   = Join-Path $Store 'original'
    $files = Join-Path $dir 'files'
    New-Item -ItemType Directory -Force -Path $files | Out-Null

    $entries = @()
    $i = 0
    foreach ($p in Get-TrackedPaths) {
        $i++
        $exists = Test-Path $p -PathType Leaf
        $stored = $null
        if ($exists) {
            # Index prefix prevents filename collisions for identically named files in different paths.
            $stored = "{0:d2}_{1}" -f $i, (Split-Path -Leaf $p)
            Copy-Item -LiteralPath $p -Destination (Join-Path $files $stored) -Force
        }
        $entries += [ordered]@{ path = $p; existed = $exists; stored = $stored }
    }

    $manifest = [ordered]@{
        created             = (Get-Date).ToString('o')
        reaperPath          = Get-ReaperPath
        entries             = $entries
        appsPresentBefore   = Get-InstalledApps
        # Records pre-installation application state to distinguish between pre-existing
        # user data and data generated during setup. Required for revert validation.
        claudeProfilesBefore = @(Get-ClaudeProfilePath)
        claudeSignedInBefore = [bool](Test-ClaudeSignedIn)
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $dir 'manifest.json'),
        ($manifest | ConvertTo-Json -Depth 8),
        (New-Object System.Text.UTF8Encoding $false))

    $saved = ($entries | Where-Object { $_.existed }).Count
    Write-Ok "Backup completed: $dir"
    Write-Info "Copied $saved existing files. Logged $($entries.Count - $saved) absent files."
    # Retains single initial snapshot for accurate state restoration.

    Write-Output $dir
    exit 0
}

# ---------------------------------------------------------------------------
if ($Restore) {
    # Targets the initial snapshot to ensure restoration of original state.
    $dir = if ($From) { $From } else { Get-OriginalSnapshot }
    if (-not $dir -or -not (Test-Path (Join-Path $dir 'manifest.json'))) {
        Write-Err "Snapshot directory not found: $Store"
        Write-Info "Backup is required before restoration."
        exit 1
    }

    $manifest = Get-Content (Join-Path $dir 'manifest.json') -Raw | ConvertFrom-Json
    Write-Info "Restoring snapshot dated $($manifest.created)."

    $restored = 0; $removed = 0; $failed = 0
    foreach ($e in $manifest.entries) {
        try {
            if ($e.existed) {
                $src = Join-Path $dir "files\$($e.stored)"
                if (Test-Path $src) {
                    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $e.path) | Out-Null
                    Copy-Item -LiteralPath $src -Destination $e.path -Force
                    $restored++
                }
            } elseif (Test-Path $e.path -PathType Leaf) {
                # Deletes files generated during installation to restore original state.
                Remove-Item -LiteralPath $e.path -Force
                $removed++
            }
        } catch {
            Write-Warn2 "Restore failed for $($e.path): $_"
            $failed++
        }
    }

    Write-Ok "Restored $restored files. Removed $removed files."
    if ($failed) { Write-Warn2 "Failed to process $failed files." }

    Write-Output ($manifest.appsPresentBefore | ConvertTo-Json -Compress)
    exit $(if ($failed) { 1 } else { 0 })
}

# ---------------------------------------------------------------------------
$snaps = Get-ChildItem $Store -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending
if (-not $snaps) {
    Write-Info "No snapshots found in $Store."
    exit 0
}
Write-Host ""
Write-Host "Snapshots:" -ForegroundColor Cyan
foreach ($s in $snaps) {
    $m = Get-Content (Join-Path $s.FullName 'manifest.json') -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    $n = if ($m) { ($m.entries | Where-Object { $_.existed }).Count } else { '?' }
    Write-Host ("{0} {1} files" -f $s.Name, $n)
}
Write-Host ""
