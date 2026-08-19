<#
.SYNOPSIS
  Configure plugin components.

.DESCRIPTION
  Configures Python virtualenv, REAPER Lua bridge, REAPER distant API, and Claude integration. Configuration blocks are independent and idempotent.

.PARAMETER Only
  Target specific configuration area: python, reaper, claude.

.PARAMETER Link
  Install into Claude Code via directory junction.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File configure-plugin.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File configure-plugin.ps1 -Only reaper -Force
#>
[CmdletBinding()]
param(
    [ValidateSet('python', 'reaper', 'claude')] [string]$Only,
    [string]$ReaperResourcePath,
    [switch]$SkipBootstrap,
    [switch]$SkipDesktop,
    [switch]$SkipCode,
    [switch]$SkipReaperConfig,
    [switch]$Link,
    [switch]$Force,
    [string]$ProblemsOut
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'lib-console.ps1')

$script:Problems = @()
function Add-Problem($m) { $script:Problems += $m }

function Invoke-Native {
    <#
      Prevents native command stderr from generating ErrorRecords.
      Avoids termination when $ErrorActionPreference = 'Stop' is active.
    #>
    param([scriptblock]$Block)
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { & $Block } finally { $ErrorActionPreference = $prev }
}

$doPython = (-not $Only) -or ($Only -eq 'python')
$doReaper = (-not $Only) -or ($Only -eq 'reaper')
$doClaude = (-not $Only) -or ($Only -eq 'claude')

$PluginRoot = Split-Path -Parent $PSScriptRoot
$Bootstrap  = Join-Path $PluginRoot 'scripts\bootstrap.py'
$Launcher   = Join-Path $PluginRoot 'scripts\launch_server.py'
$ReaperSrc  = Join-Path $PluginRoot 'reaper'
$EnableRpy  = Join-Path $ReaperSrc 'enable_reapy.py'
$BridgeLua  = Join-Path $ReaperSrc 'claude_bridge.lua'
$Manifest   = Join-Path $PluginRoot '.claude-plugin\plugin.json'

foreach ($p in @($Bootstrap, $Launcher, $EnableRpy, $BridgeLua, $Manifest)) {
    if (-not (Test-Path $p)) { throw "Missing file: $p" }
}

$MarketplaceName = 'reaper-skills-for-claude'
$PluginName      = 'reaper-for-claude'
$PluginRef       = "$PluginName@$MarketplaceName"

function Test-MarketplaceInstalled {
    <#
      Checks for marketplace installation.
      Required because junction presence does not guarantee execution priority.
    #>
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { return $false }
    $listing = Invoke-Native { & claude plugin list 2>&1 | Out-String }
    return [bool]($listing -match [regex]::Escape($PluginRef))
}

function Clear-MarketplaceRoute {
    <#
      Removes marketplace configuration.
      Prevents marketplace entries from overriding local directory junctions.
    #>
    if (-not (Get-Command claude -ErrorAction SilentlyContinue)) { return }

    $listing = Invoke-Native { & claude plugin list 2>&1 | Out-String }
    if ($listing -match [regex]::Escape($PluginRef)) {
        Write-Info "Removing marketplace installation."
        Invoke-Native { & claude plugin uninstall $PluginRef 2>&1 | ForEach-Object { Write-Info $_ } }
    }

    $markets = Invoke-Native { & claude plugin marketplace list 2>&1 | Out-String }
    if ($markets -match [regex]::Escape($MarketplaceName)) {
        Write-Info "Removing marketplace registration."
        Invoke-Native { & claude plugin marketplace remove $MarketplaceName 2>&1 | ForEach-Object { Write-Info $_ } }
    }
}

[void](Start-RunLog 'configure-plugin')
Write-Banner "Plugin configuration"
Write-Info "Path: $PluginRoot"

$PythonExe = $null
if ($doPython -or $doReaper) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $PythonExe = $cmd.Source }
}

if ($doPython) {
    Write-Step "Python environment"

    if (-not $PythonExe) {
        Write-Err "Python executable not found."
        Add-Problem "Install Python and add it to PATH."
    } elseif ($SkipBootstrap) {
        Write-Warn2 "Skipping dependency installation."
    } else {
        $pyVer = & python -c "import sys;print('%d.%d'%sys.version_info[:2])" 2>$null
        Write-Info "Python version: $pyVer ($PythonExe)"

        Invoke-Native { & python $Bootstrap }
        if ($LASTEXITCODE -eq 0) {
            Write-Ok "Dependencies installed."
        } else {
            Write-Err "bootstrap.py exit code: $LASTEXITCODE."
            Add-Problem "Check output. Retry command: py -3.12 `"$Bootstrap`" --recreate"
        }
    }
}

# Select Python interpreter for REAPER configuration.
# Required because reapy 0.10.0 modifies reaper.ini and requires Python <= 3.12.
$ReapyPython = $null

if ($PythonExe) {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $chosen = & python $Bootstrap --print-reaper-python 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0 -and $chosen) {
            $chosen = "$chosen".Trim()
            $null = & $chosen -c 'import sys; sys.exit(0 if sys.version_info[:2] <= (3, 12) else 1)' 2>&1
            $verOk = ($LASTEXITCODE -eq 0)
            $null = & $chosen -c 'import reapy' 2>&1
            $reapyOk = ($LASTEXITCODE -eq 0)

            if ($verOk -and $reapyOk -and (Test-Path $chosen)) {
                $ReapyPython = $chosen
            } elseif (-not $verOk) {
                Write-Info "Selected interpreter $chosen is > 3.12."
            }
        }
    } catch { }
    $ErrorActionPreference = $prevEAP
}

function Invoke-Reapy {
    param([string[]]$ScriptArgs)
    & $ReapyPython @ScriptArgs
}

$ReaperFound = $false
$ScriptsDir  = $null

if ($doReaper) {
    Write-Step "REAPER bridge listener"

    if (-not $ReaperResourcePath) { $ReaperResourcePath = Join-Path $env:APPDATA 'REAPER' }
    $ReaperFound = Test-Path (Join-Path $ReaperResourcePath 'reaper.ini')

    if (-not $ReaperFound) {
        $exe = @(
            "$env:ProgramFiles\REAPER (x64)\reaper.exe",
            "$env:ProgramFiles\REAPER\reaper.exe",
            "${env:ProgramFiles(x86)}\REAPER\reaper.exe"
        ) | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1

        if ($exe) {
            Write-Err "reaper.ini not found."
            Add-Problem "Execute REAPER to generate configuration file. Specify -ReaperResourcePath for portable installations."
        } else {
            Write-Err "REAPER installation not found."
            Add-Problem "Install REAPER and execute once."
        }
    } else {
        Write-Info "Resource path: $ReaperResourcePath"
        $ScriptsDir = Join-Path $ReaperResourcePath 'Scripts'
        New-Item -ItemType Directory -Force -Path $ScriptsDir | Out-Null

        Copy-Item -Force $BridgeLua (Join-Path $ScriptsDir 'claude_bridge.lua')
        Write-Ok "Copied claude_bridge.lua."

        Copy-Item -Force $EnableRpy (Join-Path $ScriptsDir 'enable_reapy.py')
        Write-Ok "Copied enable_reapy.py."

        $startup = Join-Path $ScriptsDir '__startup.lua'
        $loader  = @'

-- >>> claude-bridge (managed by REAPER for Claude) >>>
do
  local sep = package.config:sub(1, 1)
  local f = reaper.GetResourcePath() .. sep .. "Scripts" .. sep .. "claude_bridge.lua"
  local h = io.open(f, "r")
  if h then h:close(); dofile(f) end
end
-- <<< claude-bridge <<<
'@

        if (-not (Test-Path $startup)) {
            [System.IO.File]::WriteAllText($startup, $loader.TrimStart("`r", "`n"),
                                           (New-Object System.Text.UTF8Encoding $false))
            Write-Ok "Created __startup.lua."
        } elseif ((Get-Content $startup -Raw) -match 'claude_bridge') {
            Write-Ok "__startup.lua contains bridge loader."
        } else {
            Copy-Item -Force $startup "$startup.bak"
            Add-Content -Path $startup -Value $loader -Encoding UTF8
            Write-Ok "Modified __startup.lua. Backup created."
        }

        Write-Info "Bridge directory: $(Join-Path $ReaperResourcePath 'claude_bridge')"
    }

    # Execute outside REAPER process to prevent concurrent reaper.ini modifications.
    Write-Step "REAPER distant API"

    if ($SkipReaperConfig) {
        Write-Warn2 "Skipped distant API configuration."
    } elseif (-not $ReaperFound -or -not $PythonExe) {
        Write-Warn2 "Skipped distant API configuration. Missing Python or REAPER path."
        Add-Problem "Run: python `"$EnableRpy`""
    } elseif (-not $ReapyPython) {
        Write-Err "Compatible Python version not found."
        Add-Problem "Install Python 3.12 and python-reapy."
    } else {
        $reaperRunning = @(Get-Process -Name 'reaper' -ErrorAction SilentlyContinue).Count -gt 0
        if ($reaperRunning -and -not $Force) {
            Write-Warn2 "REAPER process detected."
            Add-Problem "Close REAPER and execute configuration."
        } else {
            Write-Info "Interpreter: $ReapyPython"
            $pyArgs = @($EnableRpy, '--resource-path', $ReaperResourcePath)
            if ($Force) { $pyArgs += '--force' }
            Invoke-Native { Invoke-Reapy $pyArgs }
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Distant API configuration applied."
            } else {
                Write-Err "enable_reapy.py exit code: $LASTEXITCODE."
                Add-Problem "Execute: $ReapyPython `"$EnableRpy`""
            }
        }
    }
}

if ($doClaude) {
    Write-Step "Claude Code"

    $LinkPath = Join-Path $env:USERPROFILE ".claude\skills\$PluginName"

    if ($SkipCode) {
        Write-Warn2 "Skipped Claude Code configuration."
    } elseif ($Link) {
        $skillsDir = Split-Path -Parent $LinkPath
        New-Item -ItemType Directory -Force -Path $skillsDir | Out-Null

        $existing = Get-Item $LinkPath -ErrorAction SilentlyContinue
        if ($existing) {
            $isLink = $existing.Attributes -band [IO.FileAttributes]::ReparsePoint
            if (-not $isLink) {
                Write-Err "Directory exists at junction path."
                Add-Problem "Remove $LinkPath and execute."
            } else {
                (Get-Item $LinkPath).Delete()
                $existing = $null
            }
        }

        if (-not (Test-Path $LinkPath)) {
            & cmd /c mklink /J "`"$LinkPath`"" "`"$PluginRoot`"" | Out-Null
            if (Test-Path $LinkPath) {
                Write-Ok "Junction created."
            } else {
                Write-Err "Junction creation failed."
                Add-Problem "Execute: mklink /J `"$LinkPath`" `"$PluginRoot`""
            }
        }

        Clear-MarketplaceRoute

        if (Get-Command claude -ErrorAction SilentlyContinue) {
            $after = Invoke-Native { & claude plugin list 2>&1 | Out-String }
            if ($after -match "$PluginName@skills-dir") { Write-Ok "Plugin loaded." }
            else { Write-Warn2 "Plugin load status unverified." }
        }
    } else {
        $claude = Get-Command claude -ErrorAction SilentlyContinue

        if (Test-Path $LinkPath) {
            Write-Warn2 "Junction exists at $LinkPath"

            if (Test-MarketplaceInstalled) {
                Write-Info "Marketplace and junction configurations both present."
                Add-Problem "Remove junction or marketplace configuration."
            } else {
                Write-Info "Junction retained."
            }
        } elseif (-not $claude) {
            Write-Warn2 "CLI executable not found."
            Write-Info "Execute in Claude Code:"
            Write-Info "  /plugin marketplace add `"$PluginRoot`""
            Write-Info "  /plugin install $PluginRef"
        } else {
            Invoke-Native {
                & claude plugin marketplace add "$PluginRoot" --scope user 2>&1 |
                    ForEach-Object { Write-Info $_ }
                & claude plugin install $PluginRef --scope user 2>&1 |
                    ForEach-Object { Write-Info $_ }
            }
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "Installation complete."
            } else {
                Write-Warn2 "CLI returned $LASTEXITCODE."
            }
        }
    }

    Write-Step "Claude Desktop"

    if ($SkipDesktop) {
        Write-Warn2 "Skipped Claude Desktop configuration."
    } else {
        $desktopRunning = @(
            Get-Process -Name 'claude', 'Claude' -ErrorAction SilentlyContinue | Where-Object {
                $p = try { $_.Path } catch { $null }
                $p -notlike '*claude-code*'
            }
        ).Count -gt 0
        if ($desktopRunning) {
            Write-Warn2 "Claude Desktop process detected."
            Add-Problem "Terminate Claude Desktop and execute configuration."
        }

        $desktopPython = 'python'
        $resolved = Get-Command python -ErrorAction SilentlyContinue
        if ($resolved) {
            $prevEAP = $ErrorActionPreference
            $ErrorActionPreference = 'Continue'
            try {
                $null = & python -c "import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)" 2>&1
                if ($LASTEXITCODE -eq 0) { $desktopPython = $resolved.Source }
            } catch { }
            $ErrorActionPreference = $prevEAP
        }
        if ($desktopPython -eq 'python') {
            $best = Invoke-Native { & python $Launcher --self-test 2>$null | Select-Object -Last 1 }
            if ($LASTEXITCODE -eq 0 -and $best -and (Test-Path "$best")) {
                $desktopPython = "$best".Trim()
            } else {
                Write-Warn2 "Target Python unavailable."
            }
        }
        Write-Info "Target interpreter: $desktopPython"

        $mcpEntry = [ordered]@{
            command = $desktopPython
            args    = @($Launcher)
            env     = [ordered]@{ REAPER_MCP_PLUGIN_ROOT = $PluginRoot }
        }

        $configPaths = @( Join-Path $env:APPDATA 'Claude\claude_desktop_config.json' )
        $msix = Join-Path $env:LOCALAPPDATA 'Packages'
        if (Test-Path $msix) {
            Get-ChildItem $msix -Filter 'Claude_*' -Directory -ErrorAction SilentlyContinue |
                ForEach-Object {
                    $configPaths += Join-Path $_.FullName 'LocalCache\Roaming\Claude\claude_desktop_config.json'
                }
        }

        $found = $false
        foreach ($cfg in $configPaths) {
            if (-not (Test-Path $cfg)) { continue }
            $found = $true
            try {
                $raw = Get-Content $cfg -Raw
                if ([string]::IsNullOrWhiteSpace($raw)) { $raw = '{}' }
                $json = $raw | ConvertFrom-Json
                if (-not $json.PSObject.Properties['mcpServers']) {
                    $json | Add-Member -MemberType NoteProperty -Name 'mcpServers' -Value ([PSCustomObject]@{})
                }
                $json.mcpServers | Add-Member -MemberType NoteProperty -Name 'reaper' -Value $mcpEntry -Force
                Copy-Item -Force $cfg "$cfg.bak"
                [System.IO.File]::WriteAllText($cfg, ($json | ConvertTo-Json -Depth 12),
                                               (New-Object System.Text.UTF8Encoding $false))
                Write-Ok "Configuration file modified: $cfg"
            } catch {
                Write-Err "Modification failed for $cfg"
                Add-Problem "Modify $cfg manually."
            }
        }
        if (-not $found) { Write-Info "Configuration file not found." }
    }

    # Record legacy paths to prevent duplication of tools.
    $superseded = @()
    foreach ($d in @('reaper-mcp', 'reaper-ai-engineer-skill')) {
        $p = Join-Path $env:USERPROFILE ".claude\skills\$d"
        if (Test-Path $p) { $superseded += $p }
    }

    $independent = Join-Path $env:USERPROFILE '.claude\skills\audio-engineer-reaper'

    if ($superseded.Count -gt 0 -or (Test-Path $independent)) {
        Write-Step "Legacy configurations detected"
        foreach ($p in $superseded) { Write-Warn2 "Legacy path: $p" }
        if ($superseded.Count -gt 0) {
            Write-Info "Duplicate items identified."
            Add-Problem "Remove legacy items."
        }
        if (Test-Path $independent) {
            Write-Info "Independent legacy configuration present: $independent"
        }
    }
}

$doctor = Join-Path $PSScriptRoot 'health-check.ps1'
if (Test-Path $doctor) {
    if ($ReaperResourcePath) { & $doctor -ReaperResourcePath $ReaperResourcePath }
    else { & $doctor }
}

if ($ProblemsOut) {
    try {
        Set-Content -Path $ProblemsOut -Value @($script:Problems) -Encoding UTF8
    } catch {
    }
    return
}

Write-Result -Problems $script:Problems
if ($script:Problems.Count -eq 0) {
    Write-Host "Restart REAPER."
    Write-Host "Restart Claude."
} else {
    Write-Host "Execution requires retry."
}

exit $(if ($script:Problems.Count -eq 0) { 0 } else { 1 })
