<#
.SYNOPSIS
  Undo everything setup-all.ps1 changed.

.DESCRIPTION
  Puts the machine back the way it was before [1] Install Everything:

    * restores every configuration file from the snapshot taken at the start
    * deletes the files the setup created, and only those
    * removes the dependency virtualenv
    * unregisters the plugin from Claude Code

  What it deliberately does NOT do
  --------------------------------
  It does not uninstall REAPER, Claude, Python or Git.

  By the time anyone reverts, those applications may hold projects, chats and
  repositories that have nothing to do with this plugin, and no configuration
  rollback is worth destroying them. The snapshot recorded which of them were
  already present, so this reports exactly which installs came from the setup
  and leaves removing them to you.

  It also never touches REAPER projects, media, presets, FX chains or any
  Claude conversation.

.PARAMETER From
  Restore a specific snapshot directory instead of the most recent.

.PARAMETER Yes
  Skip the confirmation prompt.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File revert-all.ps1
#>
[CmdletBinding()]
param(
    [string]$From,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

function Write-Step($m) { Write-Host "`n=== $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Info($m) { Write-Host "  .      $m" -ForegroundColor Gray }
function Write-Warn2($m){ Write-Host "  [warn] $m" -ForegroundColor Yellow }
function Write-Err($m)  { Write-Host "  [FAIL] $m" -ForegroundColor Red }

$Here       = $PSScriptRoot
$PluginRoot = Split-Path -Parent $Here
$DataDir    = Join-Path $env:USERPROFILE '.reaper-for-claude'
$Store      = Join-Path $DataDir 'backups'

$MarketplaceName = 'reaper-skills-for-claude'
$PluginRef       = "reaper-for-claude@$MarketplaceName"

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  REAPER for Claude - revert everything" -ForegroundColor Cyan
Write-Host "===============================================" -ForegroundColor Cyan

# The original, not the newest.
#
# "Revert Everything" can only mean "back to before this setup ever ran", and
# that is one specific snapshot. Picking the newest meant that on any machine
# where [1] had been run twice - which eight different failure messages tell
# people to do - Revert restored the half-installed state left by the first run
# instead. snapshot.ps1 now writes `original` once and promotes the earliest
# directory on machines that predate that; this mirrors the same choice.
$snapshot = if ($From) { $From } else {
    $original = Join-Path $Store 'original'
    if (Test-Path (Join-Path $original 'manifest.json')) {
        $original
    } else {
        Get-ChildItem $Store -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName 'manifest.json') } |
            Sort-Object Name | Select-Object -First 1 |
            ForEach-Object { $_.FullName }
    }
}

if (-not $snapshot -or -not (Test-Path (Join-Path $snapshot 'manifest.json'))) {
    Write-Err "No snapshot to restore from."
    Write-Info "Snapshots are taken automatically at the start of [1] Install Everything,"
    Write-Info "and kept in $Store"
    Write-Host ""
    Write-Info "Nothing has been changed."
    exit 1
}

$manifest = Get-Content (Join-Path $snapshot 'manifest.json') -Raw | ConvertFrom-Json

Write-Info "Snapshot: $snapshot"
Write-Info "Taken:    $($manifest.created)"
Write-Host ""
Write-Host "  This will:" -ForegroundColor Yellow
Write-Host "    - restore your REAPER and Claude configuration files"
Write-Host "    - remove the files this setup added to REAPER's Scripts folder"
Write-Host "    - delete the dependency virtualenv"
Write-Host "    - unregister the plugin from Claude Code"
Write-Host ""
Write-Host "  It will NOT uninstall REAPER, Claude, Python or Git, and will not" -ForegroundColor Yellow
Write-Host "  touch your projects, media, presets or conversations." -ForegroundColor Yellow
Write-Host ""

if (-not $Yes) {
    $answer = Read-Host "  Type REVERT to continue"
    if ($answer -cne 'REVERT') {
        Write-Info "Cancelled. Nothing has been changed."
        exit 0
    }
}

# ---------------------------------------------------------------------------
# REAPER rewrites reaper.ini when it exits, so a restore underneath a running
# REAPER would be undone the moment it closes.
# ---------------------------------------------------------------------------
if (@(Get-Process -Name 'reaper' -ErrorAction SilentlyContinue).Count -gt 0) {
    Write-Host ""
    Write-Warn2 "REAPER is running. It rewrites reaper.ini on exit, which would undo"
    Write-Warn2 "the restore below."
    Write-Host "  Close REAPER, then press Enter to continue." -ForegroundColor Yellow
    [void](Read-Host)
}

# ---------------------------------------------------------------------------
# 1. Claude Code registration. Done before the file restore, because the CLI
#    writes to ~/.claude/settings.json - which the restore is about to put back.
# ---------------------------------------------------------------------------
Write-Step "Claude Code"
if (Get-Command claude -ErrorAction SilentlyContinue) {
    foreach ($cmd in @(
        @('plugin', 'uninstall', $PluginRef),
        @('plugin', 'marketplace', 'remove', $MarketplaceName)
    )) {
        try { & claude @cmd 2>&1 | ForEach-Object { Write-Info $_ } } catch { }
    }
    Write-Ok "Plugin and marketplace unregistered."
} else {
    Write-Info "The claude CLI is not on PATH; skipping."
}

$link = Join-Path $env:USERPROFILE ".claude\skills\reaper-for-claude"
if (Test-Path $link) {
    try {
        # A junction must be removed as a directory entry, not recursed into -
        # recursing would delete the plugin repository it points at.
        (Get-Item $link).Delete()
        Write-Ok "Developer link removed."
    } catch {
        Write-Warn2 "Could not remove $link : $_"
    }
}

# ---------------------------------------------------------------------------
# 2. Files
# ---------------------------------------------------------------------------
Write-Step "Restoring configuration"
try {
    & (Join-Path $Here 'snapshot.ps1') -Restore -From $snapshot | Out-Host
} catch {
    Write-Err "Restore failed: $_"
    exit 1
}

# ---------------------------------------------------------------------------
# 3. The virtualenv
# ---------------------------------------------------------------------------
Write-Step "Dependency environment"
$venv = Join-Path $DataDir 'venv'
if (Test-Path $venv) {
    try {
        Remove-Item $venv -Recurse -Force
        Write-Ok "Removed $venv"
    } catch {
        Write-Warn2 "Could not remove $venv : $_"
        Write-Info "Close anything using it, then delete it by hand."
    }
} else {
    Write-Info "No virtualenv to remove."
}

Remove-Item (Join-Path $DataDir 'requirements.sha256') -Force -ErrorAction SilentlyContinue

# python-reapy stays in the base interpreter. It is three small pure-Python
# packages, removing them risks breaking something else that came to depend on
# them, and leaving them behind costs nothing.
Write-Info "python-reapy was left in your system Python - harmless, and safe to keep."

# ---------------------------------------------------------------------------
# 4. Applications: report, never remove.
# ---------------------------------------------------------------------------
Write-Step "Applications"
$ours = @()
if ($manifest.appsPresentBefore) {
    foreach ($p in $manifest.appsPresentBefore.PSObject.Properties) {
        if ($p.Value -eq $false) { $ours += $p.Name }
    }
}
if ($ours.Count -gt 0) {
    Write-Info "These were not on this machine before the setup ran:"
    foreach ($id in $ours) { Write-Host "         $id" -ForegroundColor Gray }
    Write-Host ""
    Write-Info "They have been left installed on purpose - you may have projects,"
    Write-Info "chats or repositories in them by now. To remove one yourself:"
    Write-Host "         winget uninstall -e --id <id>" -ForegroundColor Gray
} else {
    Write-Info "Everything was already installed before the setup ran; nothing to report."
}

Write-Host ""
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "  REVERTED" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Restart REAPER and Claude so they reload their configuration."
Write-Host ""
