<#
.SYNOPSIS
  Back up, or restore, every file this setup is capable of changing.

.DESCRIPTION
  "Revert Everything" is only honest if a snapshot was taken before anything
  moved, so install-everything.ps1 calls this first, every time, before its first write.

  Two kinds of entry are recorded, and the difference is what makes a clean
  revert possible:

    existed = true    the file was already there, and a copy is stored.
                      Restoring means putting the copy back.
    existed = false   the file did not exist, so the setup created it.
                      Restoring means deleting it.

  Without that distinction, a revert either leaves our files behind or deletes
  files that were the user's to begin with.

  What is never touched: REAPER projects, media, plugin settings, presets, and
  anything Claude has stored. This tracks the specific configuration files the
  installer writes to, and nothing else.

  Exactly one snapshot matters: the state of the machine BEFORE this setup ever
  touched it. It lives in a directory called `original` and is written once.

  This is not a detail. Eight different failure messages across these scripts
  tell the user to "re-run [1]", and every run used to take a fresh snapshot and
  restore the NEWEST one. So the second run photographed the machine we had
  already modified, and Revert then restored our own half-finished install -
  quietly, with no error, having also pruned the real original away after ten
  runs. The backup silently stopped being a backup at exactly the moment the
  design told people to lean on it hardest.

  So: written once, never overwritten, never pruned.

.PARAMETER Backup
  Create the original snapshot if there is not one already. Prints its directory.

.PARAMETER Restore
  Restore the original snapshot, or the one named by -From.

.PARAMETER List
  Show the snapshots that exist.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File backup-restore.ps1 -Backup
#>
[CmdletBinding(DefaultParameterSetName = 'List')]
param(
    [Parameter(ParameterSetName = 'Backup')]  [switch]$Backup,
    [Parameter(ParameterSetName = 'Restore')] [switch]$Restore,
    [Parameter(ParameterSetName = 'Restore')] [string]$From,
    [Parameter(ParameterSetName = 'List')]    [switch]$List,
    [string]$ReaperResourcePath
)

$ErrorActionPreference = 'Stop'

function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  .      $m" -ForegroundColor Gray }
function Write-Warn2($m){ Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

# For Get-ClaudeProfilePath and Test-ClaudeSignedIn, recorded in the manifest.
. (Join-Path $PSScriptRoot 'lib-app-control.ps1')

$Store = Join-Path $env:USERPROFILE '.reaper-for-claude\backups'

function Get-ReaperPath {
    if ($ReaperResourcePath) { return $ReaperResourcePath }
    $p = Join-Path $env:APPDATA 'REAPER'
    if (Test-Path (Join-Path $p 'reaper.ini')) { return $p }
    return $null
}

function Get-TrackedPaths {
    <#
      Every file the installer can create or modify. Listed in one place so a
      new write anywhere in the setup has one obvious spot to be declared, and
      the revert cannot silently fall behind.
    #>
    $paths = @()

    $reaper = Get-ReaperPath
    if ($reaper) {
        $paths += Join-Path $reaper 'reaper.ini'            # distant API, Python ReaScript
        $paths += Join-Path $reaper 'reaper-kb.ini'         # activate_reapy_server action
        $paths += Join-Path $reaper 'reaper-extstate.ini'   # that action's id
        $paths += Join-Path $reaper 'Scripts\__startup.lua' # bridge loader appended
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

    # `claude plugin marketplace add` writes here.
    $paths += Join-Path $env:USERPROFILE '.claude\settings.json'

    return $paths | Select-Object -Unique
}

function Get-InstalledApps {
    <#
      Recorded for reporting only. A revert restores configuration; it never
      uninstalls an application, because by then the user may have projects,
      chats or repositories depending on it. Knowing what was already present
      lets the revert tell them exactly which installs were ours to remove by
      hand if they want to.
    #>
    $ids = @('Python.Python.3.12', 'Git.Git', 'Cockos.REAPER', 'Anthropic.Claude', 'Anthropic.ClaudeCode')
    $result = [ordered]@{}
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        foreach ($id in $ids) { $result[$id] = $null }   # unknown, not false
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
      The pre-setup snapshot, or $null if this machine has never had one.

      Machines set up before this changed have timestamped directories instead.
      There the OLDEST is the pristine one, for the same reason the newest is
      not: each later run photographed a machine the previous run had modified.
      It is promoted in place so this only has to be worked out once.
    #>
    $original = Join-Path $Store 'original'
    if (Test-Path (Join-Path $original 'manifest.json')) { return $original }

    $oldest = Get-ChildItem $Store -Directory -ErrorAction SilentlyContinue |
              Where-Object { Test-Path (Join-Path $_.FullName 'manifest.json') } |
              Sort-Object Name | Select-Object -First 1
    if (-not $oldest) { return $null }

    try {
        Rename-Item -LiteralPath $oldest.FullName -NewName 'original' -ErrorAction Stop
        Write-Info "Promoted the earliest snapshot ($($oldest.Name)) to the original."
        return $original
    } catch {
        # Could not rename - use it where it stands rather than lose it.
        return $oldest.FullName
    }
}

# ---------------------------------------------------------------------------
if ($Backup) {
    $dir = Get-OriginalSnapshot
    if ($dir) {
        # Already have the one that matters. Taking another now would photograph
        # a machine this setup has already changed.
        $when = try {
            (Get-Content (Join-Path $dir 'manifest.json') -Raw | ConvertFrom-Json).created
        } catch { 'an earlier run' }
        Write-Ok "Original configuration already backed up ($when)."
        Write-Info "Keeping it - that is the one [2] Revert Everything restores."
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
            # Flat names with an index: two REAPER installs, or the plain and
            # MSIX Claude configs, share a filename and would overwrite here.
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
        # Which of Claude's own state directories were here before any of this
        # ran, and whether an account was already attached. Recorded as fact
        # rather than inferred later, because after the setup has installed
        # Claude there is no way to tell its profile from one the user has had
        # for a year. Revert needs exactly that distinction - see the note in
        # revert-everything.ps1.
        claudeProfilesBefore = @(Get-ClaudeProfilePath)
        claudeSignedInBefore = [bool](Test-ClaudeSignedIn)
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $dir 'manifest.json'),
        ($manifest | ConvertTo-Json -Depth 8),
        (New-Object System.Text.UTF8Encoding $false))

    $saved = ($entries | Where-Object { $_.existed }).Count
    Write-Ok "Original configuration backed up: $dir"
    Write-Info "$saved existing file(s) copied, $($entries.Count - $saved) noted as absent."
    # Nothing is pruned here any more. There is one snapshot, it is the only one
    # that can undo this setup, and it is not ours to delete.

    Write-Output $dir
    exit 0
}

# ---------------------------------------------------------------------------
if ($Restore) {
    # The original, not the newest. See the note at the top of this file.
    $dir = if ($From) { $From } else { Get-OriginalSnapshot }
    if (-not $dir -or -not (Test-Path (Join-Path $dir 'manifest.json'))) {
        Write-Err "No snapshot found under $Store"
        Write-Info "A snapshot is taken automatically at the start of [1] Install everything."
        exit 1
    }

    $manifest = Get-Content (Join-Path $dir 'manifest.json') -Raw | ConvertFrom-Json
    Write-Info "Restoring snapshot from $($manifest.created)"

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
                # Absent before, present now: the setup created it.
                Remove-Item -LiteralPath $e.path -Force
                $removed++
            }
        } catch {
            Write-Warn2 "Could not restore $($e.path): $_"
            $failed++
        }
    }

    Write-Ok "$restored file(s) restored, $removed created file(s) removed."
    if ($failed) { Write-Warn2 "$failed file(s) could not be restored - see above." }

    Write-Output ($manifest.appsPresentBefore | ConvertTo-Json -Compress)
    exit $(if ($failed) { 1 } else { 0 })
}

# ---------------------------------------------------------------------------
$snaps = Get-ChildItem $Store -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending
if (-not $snaps) {
    Write-Info "No snapshots under $Store"
    exit 0
}
Write-Host ""
Write-Host "  Snapshots (newest first):" -ForegroundColor Cyan
foreach ($s in $snaps) {
    $m = Get-Content (Join-Path $s.FullName 'manifest.json') -Raw -ErrorAction SilentlyContinue | ConvertFrom-Json
    $n = if ($m) { ($m.entries | Where-Object { $_.existed }).Count } else { '?' }
    Write-Host ("    {0}   {1} file(s)" -f $s.Name, $n)
}
Write-Host ""
