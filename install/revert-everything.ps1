<#
.SYNOPSIS
  Undo everything install-everything.ps1 changed.

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
  powershell -ExecutionPolicy Bypass -File revert-everything.ps1
#>
[CmdletBinding()]
param(
    [string]$From,
    [switch]$Yes
)

$ErrorActionPreference = 'Stop'

# How this talks, and the log it writes. Same function names the rest of
# this file already calls - see lib-console.ps1.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

# For Get-ClaudeProfilePath and Test-ClaudeSignedIn.
. (Join-Path $PSScriptRoot 'lib-app-control.ps1')

$Here       = $PSScriptRoot
$DataDir    = Join-Path $env:USERPROFILE '.reaper-for-claude'
$Store      = Join-Path $DataDir 'backups'

$MarketplaceName = 'reaper-skills-for-claude'
$PluginRef       = "reaper-for-claude@$MarketplaceName"

$stepLog = Start-RunLog 'revert'
Write-Banner "REAPER for Claude - revert everything"
if ($stepLog) { Write-Info "log  $stepLog" }

# Everything that could not be put back, collected here rather than only warned
# about, so the closing word matches the machine. Declared up here because the
# first thing that can fail - removing the developer junction - happens before
# the restore does, and a list that starts halfway down the file can only ever
# hold half the failures.
$problems = @()

# The original, not the newest.
#
# "Revert Everything" can only mean "back to before this setup ever ran", and
# that is one specific snapshot. Picking the newest meant that on any machine
# where [1] had been run twice - which eight different failure messages tell
# people to do - Revert restored the half-installed state left by the first run
# instead. backup-restore.ps1 now writes `original` once and promotes the earliest
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
        $problems += "The developer link is still there: $link - it shadows the marketplace plugin, so delete it by hand."
    }
}

# ---------------------------------------------------------------------------
# 2. Files
# ---------------------------------------------------------------------------
Write-Step "Restoring configuration"
$restoreOk = $true

# Hand this run's log down, so the child writes WHICH file it could not put back
# into the log the summary below points at. Without it backup-restore.ps1 opens
# its own backup-<timestamp>.log and the revert log holds this step's header and
# nothing under it - the user is sent to a file that names no failure. Scoped to
# the call and cleared in the finally, so none of the early exits above this
# point can leak it into an interactive session.
if ($stepLog) { $env:RFC_LOG_PATH = $stepLog }
try {
    & (Join-Path $Here 'backup-restore.ps1') -Restore -From $snapshot | Out-Host
    # The exit code, not just the exception. backup-restore.ps1 ends with
    # `exit 1` when a file could not be put back - locked by something still
    # running, or a permissions refusal - and a child script exiting non-zero
    # does NOT throw, so catching alone saw nothing and this went on to print
    # REVERTED over a machine whose configuration had not been restored. On the
    # one path a user reaches when they are already recovering from something,
    # that is the worst possible thing to get wrong.
    $restoreOk = ($LASTEXITCODE -eq 0)
} catch {
    Write-Err "Restore failed: $_"
    exit 1
} finally {
    Remove-Item Env:\RFC_LOG_PATH -ErrorAction SilentlyContinue
}
if (-not $restoreOk) {
    $problems += "Some files could not be restored - see the warnings above. Close REAPER and Claude, then run [2] again."
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
        $problems += "The dependency virtualenv is still there: $venv - close whatever is using it, then delete it."
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
# 3b. A Claude profile that only this setup ever created.
#
# Restoring configuration cannot fix a Claude that will not start. The damage
# that actually strands people is in the profile itself - Chromium's Local
# Storage, a LevelDB - and a revert that only puts JSON files back reports
# success over a machine where Claude is still broken. That is the state this
# exists for: Claude installed by the setup, killed before it finished writing
# its profile, never signed into, and no way back.
#
# Two conditions, both from the manifest, and both required:
#
#   the profile did not exist before this setup ran   (so it is not theirs)
#   no account was attached, then or now              (so nothing is in it)
#
# Under those, the directory provably holds nothing the user made: no chats, no
# account, no settings they chose. It is renamed rather than deleted, so even a
# wrong call here is recoverable by hand.
#
# Anything else - a profile that predates the setup, or one with a session in
# it - is left completely alone. A signed-in Claude has conversations in it, and
# no install problem is worth those.
# ---------------------------------------------------------------------------
Write-Step "Claude profile"
if ($null -eq $manifest.claudeProfilesBefore) {
    Write-Info "This snapshot predates profile tracking; leaving Claude's data alone."
} elseif (Test-ClaudeSignedIn) {
    Write-Info "Claude has an account attached, so its profile is yours now - left alone."
} elseif ($manifest.claudeSignedInBefore) {
    Write-Info "Claude was already signed in before this ran; leaving its profile alone."
} else {
    $before = @($manifest.claudeProfilesBefore)
    $ours   = @(Get-ClaudeProfilePath | Where-Object { $before -notcontains $_ })

    if ($ours.Count -eq 0) {
        Write-Info "No Claude profile was created by this setup; nothing to reset."
    } else {
        foreach ($dir in $ours) {
            $aside = "$dir.before-revert-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            try {
                Move-Item -LiteralPath $dir -Destination $aside -Force -ErrorAction Stop
                Write-Ok "Reset $dir"
                Write-Info "  Moved aside to $aside - delete it once Claude starts again."
                Write-Info "  It was created by this setup and never signed into, so there"
                Write-Info "  are no conversations in it."
            } catch {
                Write-Warn2 "Could not move $dir : $($_.Exception.Message.Split([Environment]::NewLine)[0])"
                Write-Info  "  Close Claude fully and try again, or rename that folder by hand."
                $problems += "Claude's profile could not be reset: $dir - close Claude fully and run [2] again, or rename that folder by hand."
            }
        }
    }
}

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

Write-Result -Problems $problems -DoneWord 'REVERTED'
Write-Host "     Restart REAPER and Claude so they reload their configuration."
if ($stepLog) { Write-Host "     Log: $stepLog" -ForegroundColor DarkGray }
Write-Host ""
