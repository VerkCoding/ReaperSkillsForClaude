<#
.SYNOPSIS
  Revert changes made by install-everything.ps1.

.DESCRIPTION
  Restores system state to before install-everything.ps1 execution:

    * Restores configuration files from snapshot.
    * Deletes setup-created files.
    * Removes dependency virtualenv.
    * Unregisters plugin from Claude Code.

  Exclusions:
  Does not uninstall REAPER, Claude, Python, or Git.

  Applications installed during setup are identified but not removed.
  REAPER projects, media, presets, FX chains, and Claude conversations are not modified.

.PARAMETER From
  Restore a specific snapshot directory.

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

# Import functions from lib-console.ps1 to provide logging capabilities.
. (Join-Path $PSScriptRoot 'lib-console.ps1')

# Import functions from lib-app-control.ps1 for Get-ClaudeProfilePath and Test-ClaudeSignedIn.
. (Join-Path $PSScriptRoot 'lib-app-control.ps1')

$Here       = $PSScriptRoot
$DataDir    = Join-Path $env:USERPROFILE '.reaper-for-claude'
$Store      = Join-Path $DataDir 'backups'

$MarketplaceName = 'reaper-skills-for-claude'
$PluginRef       = "reaper-for-claude@$MarketplaceName"

$stepLog = Start-RunLog 'revert'
Write-Banner "Revert changes"
if ($stepLog) { Write-Info "Log: $stepLog" }

# Collect restoration failures to report at the end of the script execution.
$problems = @()

# The original snapshot is prioritized to prevent restoring an intermediate state if install-everything.ps1 was executed multiple times.
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
    Write-Err "No snapshot found."
    Write-Info "Snapshots are located in $Store."
    Write-Host ""
    Write-Info "No changes made."
    exit 1
}

$manifest = Get-Content (Join-Path $snapshot 'manifest.json') -Raw | ConvertFrom-Json

Write-Info "Snapshot: $snapshot"
Write-Info "Taken:    $($manifest.created)"
Write-Host ""
Write-Host "This will:"
Write-Host "- Restore REAPER and Claude configuration files."
Write-Host "- Remove files added to the REAPER Scripts folder."
Write-Host "- Delete the dependency virtualenv."
Write-Host "- Unregister the plugin from Claude Code."
Write-Host ""
Write-Host "This will not uninstall REAPER, Claude, Python, or Git."
Write-Host "This will not modify projects, media, presets, or conversations."
Write-Host ""

if (-not $Yes) {
    $answer = Read-Host "Type REVERT to continue"
    if ($answer -cne 'REVERT') {
        Write-Info "Cancelled. No changes made."
        exit 0
    }
}

# REAPER rewrites reaper.ini upon exiting. The process must be closed to prevent it from overwriting the restored configuration.
if (@(Get-Process -Name 'reaper' -ErrorAction SilentlyContinue).Count -gt 0) {
    Write-Host ""
    Write-Warn2 "REAPER is running. reaper.ini will be overwritten on exit."
    Write-Host "Close REAPER, then press Enter to continue."
    [void](Read-Host)
}

# Claude Code registration is removed before file restoration to ensure ~/.claude/settings.json changes are overwritten by the restored file.
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
    Write-Info "claude CLI not found on PATH. Skipping."
}

$link = Join-Path $env:USERPROFILE ".claude\skills\reaper-for-claude"
if (Test-Path $link) {
    try {
        # A junction must be removed as a directory entry to avoid deleting the referenced repository.
        (Get-Item $link).Delete()
        Write-Ok "Developer link removed."
    } catch {
        Write-Warn2 "Could not remove $link : $_"
        $problems += "Developer link remains: $link. Manual removal required."
    }
}

Write-Step "Restoring configuration"
$restoreOk = $true

# The log path is passed to backup-restore.ps1 to capture specific file restoration failures in the main log.
if ($stepLog) { $env:RFC_LOG_PATH = $stepLog }
try {
    & (Join-Path $Here 'backup-restore.ps1') -Restore -From $snapshot | Out-Host
    # Check LASTEXITCODE to detect non-terminating errors from the child script execution.
    $restoreOk = ($LASTEXITCODE -eq 0)
} catch {
    Write-Err "Restore failed: $_"
    exit 1
} finally {
    Remove-Item Env:\RFC_LOG_PATH -ErrorAction SilentlyContinue
}
if (-not $restoreOk) {
    $problems += "File restoration incomplete. Close REAPER and Claude, then execute again."
}

Write-Step "Dependency environment"
$venv = Join-Path $DataDir 'venv'
if (Test-Path $venv) {
    try {
        Remove-Item $venv -Recurse -Force
        Write-Ok "Removed $venv"
    } catch {
        Write-Warn2 "Could not remove $venv : $_"
        Write-Info "Manual deletion required."
        $problems += "Dependency virtualenv remains: $venv. Manual deletion required."
    }
} else {
    Write-Info "Virtualenv not found."
}

Remove-Item (Join-Path $DataDir 'requirements.sha256') -Force -ErrorAction SilentlyContinue

# python-reapy is left in the system Python environment to prevent dependency conflicts with other software.
Write-Info "python-reapy retained in system Python."

# Claude profiles created by the setup without an attached account are renamed.
# This recovers instances where Claude fails to start due to an incomplete profile.
# Profiles with existing user data or sessions are retained to prevent data loss.
Write-Step "Claude profile"
if ($null -eq $manifest.claudeProfilesBefore) {
    Write-Info "Snapshot predates profile tracking. Claude data retained."
} elseif (Test-ClaudeSignedIn) {
    Write-Info "Claude account detected. Profile retained."
} elseif ($manifest.claudeSignedInBefore) {
    Write-Info "Claude was signed in prior to setup. Profile retained."
} else {
    $before = @($manifest.claudeProfilesBefore)
    $ours   = @(Get-ClaudeProfilePath | Where-Object { $before -notcontains $_ })

    if ($ours.Count -eq 0) {
        Write-Info "No setup-created Claude profile found."
    } else {
        foreach ($dir in $ours) {
            $aside = "$dir.before-revert-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
            try {
                Move-Item -LiteralPath $dir -Destination $aside -Force -ErrorAction Stop
                Write-Ok "Reset $dir"
                Write-Info "Moved to $aside. Manual deletion recommended."
            } catch {
                Write-Warn2 "Could not move $dir : $($_.Exception.Message.Split([Environment]::NewLine)[0])"
                Write-Info "Manual rename required."
                $problems += "Claude profile reset failed: $dir. Manual rename required."
            }
        }
    }
}

# Applications installed during setup are identified for manual removal.
Write-Step "Applications"
$ours = @()
if ($manifest.appsPresentBefore) {
    foreach ($p in $manifest.appsPresentBefore.PSObject.Properties) {
        if ($p.Value -eq $false) { $ours += $p.Name }
    }
}
if ($ours.Count -gt 0) {
    Write-Info "Applications installed during setup:"
    foreach ($id in $ours) { Write-Host "         $id" }
    Write-Host ""
    Write-Info "Applications retained. To remove:"
    Write-Host "winget uninstall -e --id <id>"
} else {
    Write-Info "No applications installed during setup."
}

Write-Result -Problems $problems -DoneWord 'REVERTED'
Write-Host "Restart REAPER and Claude to apply changes."
if ($stepLog) { Write-Host "Log: $stepLog" }
Write-Host ""
