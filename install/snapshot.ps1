<#
.SYNOPSIS
  Back up, or restore, every file this setup is capable of changing.

.DESCRIPTION
  "Revert Everything" is only honest if a snapshot was taken before anything
  moved, so setup-all.ps1 calls this first, every time, before its first write.

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

.PARAMETER Backup
  Take a snapshot. Prints the directory it created.

.PARAMETER Restore
  Restore the most recent snapshot, or the one named by -From.

.PARAMETER List
  Show the snapshots that exist.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File snapshot.ps1 -Backup
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

# ---------------------------------------------------------------------------
if ($Backup) {
    $stamp = Get-Date -Format 'yyyy-MM-dd_HHmmss'
    $dir   = Join-Path $Store $stamp
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
        created           = (Get-Date).ToString('o')
        reaperPath        = Get-ReaperPath
        entries           = $entries
        appsPresentBefore = Get-InstalledApps
    }
    [System.IO.File]::WriteAllText(
        (Join-Path $dir 'manifest.json'),
        ($manifest | ConvertTo-Json -Depth 8),
        (New-Object System.Text.UTF8Encoding $false))

    $saved = ($entries | Where-Object { $_.existed }).Count
    Write-Ok "Snapshot saved: $dir"
    Write-Info "$saved existing file(s) copied, $($entries.Count - $saved) noted as absent."

    # Keep a handful. These are small text files, but an unbounded pile of them
    # is its own kind of mess.
    Get-ChildItem $Store -Directory -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending | Select-Object -Skip 10 |
        ForEach-Object { Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue }

    Write-Output $dir
    exit 0
}

# ---------------------------------------------------------------------------
if ($Restore) {
    if ($From) {
        $dir = $From
    } else {
        $dir = Get-ChildItem $Store -Directory -ErrorAction SilentlyContinue |
               Sort-Object Name -Descending | Select-Object -First 1 |
               ForEach-Object { $_.FullName }
    }
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
