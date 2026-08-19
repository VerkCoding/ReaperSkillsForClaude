<#
  Provides console output formatting and logging functions.

  Dot-sourced across multiple scripts to maintain consistent output formatting and a unified log file.

  Architectural considerations:
  - Consolidates duplicated Write-* functions to reduce maintenance overhead.
  - Maintains existing function names to avoid modifying call sites.
  - Generates glyphs from [char] codes at runtime. This prevents parsing errors in Windows PowerShell 5.1 when reading ANSI-encoded .ps1 files without a BOM, ensuring the file remains ASCII.
  - Falls back to ASCII characters when the console is on an OEM code page to prevent rendering issues.
#>

$RfcUnicodeConsole = $false
try {
    # Querying GetConsoleOutputCP() directly ensures accurate detection of the console's actual code page.
    # Relying on [Console]::OutputEncoding is inaccurate if 'chcp 65001' was executed before the PowerShell process started.
    if (-not ('Rfc.Cp' -as [type])) {
        Add-Type -Namespace Rfc -Name Cp -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint GetConsoleOutputCP();
'@ -ErrorAction Stop
    }
    if ([Rfc.Cp]::GetConsoleOutputCP() -eq 65001) {
        # Align OutputEncoding with the console code page to prevent UTF-8 characters from being processed by an OEM encoder.
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
        $RfcUnicodeConsole = $true
    }
} catch { }

$RfcGlyph = if ($RfcUnicodeConsole) {
    @{
        Ok = [char]0x2713
        Info = [char]0x00B7
        Warn = [char]0x0021
        Err = [char]0x00D7
        H = [char]0x2500
        V = [char]0x2502
        TL = [char]0x250C; TR = [char]0x2510
        BL = [char]0x2514; BR = [char]0x2518
        Dot = [char]0x00B7
    }
} else {
    @{
        Ok = '+'; Info = '-'; Warn = '!'; Err = 'x'
        H = '-'; V = '|'
        TL = '+'; TR = '+'; BL = '+'; BR = '+'
        Dot = '-'
    }
}

$RfcWidth = 62

# State variables are conditionally initialized.
# This prevents resetting state if the file is dot-sourced multiple times within the same caller scope.
if (-not (Test-Path 'variable:RfcStep'))    { $RfcStep    = 0 }
if (-not (Test-Path 'variable:RfcLogPath')) { $RfcLogPath = $null }

function Start-RunLog {
    <#
      Initializes log file path for structured output.
      This provides state tracking separate from Start-Transcript raw capture.
    #>
    param([string]$Name)

    # Reuses existing log path from the environment to ensure child scripts running in separate scopes write to the same log file.
    if ($env:RFC_LOG_PATH) {
        $script:RfcLogPath = $env:RFC_LOG_PATH
        return $script:RfcLogPath
    }

    # Reset step counter for top-level executions.
    Remove-Item Env:\RFC_STEP -ErrorAction SilentlyContinue

    try {
        $dir = Join-Path $env:USERPROFILE '.reaper-for-claude\logs'
        New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
        $script:RfcLogPath = Join-Path $dir ("{0}-{1}.log" -f $Name, (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
        Write-LogLine 'RUN' "$Name  |  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  |  PowerShell $($PSVersionTable.PSVersion)"

        # Limits log retention to prevent unbounded directory growth.
        Get-ChildItem $dir -Filter "$Name-*.log" -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending | Select-Object -Skip 10 |
            ForEach-Object { Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue }

        return $script:RfcLogPath
    } catch {
        $script:RfcLogPath = $null
        return $null
    }
}

function Write-LogLine {
    <#
      Writes timestamped ASCII strings to the log file.
      Non-ASCII characters are removed to guarantee readability across different text viewers without encoding issues.
    #>
    param([string]$Level, [string]$Message)

    if (-not $script:RfcLogPath) { return }
    try {
        $clean = ($Message -replace '[^\x20-\x7E]', '')
        $line = "{0}  {1,-5} {2}" -f (Get-Date -Format 'HH:mm:ss'), $Level, $clean
        [System.IO.File]::AppendAllText($script:RfcLogPath, $line + [Environment]::NewLine,
                                        (New-Object System.Text.UTF8Encoding $false))
    } catch { }
}

function Write-Ok($m)    { Write-Host "  $($RfcGlyph.Ok)  $m"   -ForegroundColor Green;    Write-LogLine 'OK'   $m }
function Write-Info($m)  { Write-Host "  $($RfcGlyph.Info)  $m" -ForegroundColor DarkGray; Write-LogLine 'INFO' $m }
function Write-Warn2($m) { Write-Host "  $($RfcGlyph.Warn)  $m" -ForegroundColor Yellow;   Write-LogLine 'WARN' $m }
function Write-Err($m)   { Write-Host "  $($RfcGlyph.Err)  $m"  -ForegroundColor Red;      Write-LogLine 'FAIL' $m }

function Write-Step {
    <#
      Outputs a numbered section line. Total count is omitted due to conditional step execution.
    #>
    param([string]$m)

    # Uses the environment variable to maintain state across child script scopes.
    # The '-as' operator is used instead of a cast to prevent terminating errors from non-numeric environment variable values.
    $inherited = if ($env:RFC_STEP) { $env:RFC_STEP -as [int] } else { $null }
    $script:RfcStep = if ($null -ne $inherited) { $inherited + 1 } else { $script:RfcStep + 1 }
    $env:RFC_STEP   = $script:RfcStep

    $label = " $($script:RfcStep) $($RfcGlyph.Dot) $m "
    # Offset by 2 characters to align with banner formatting dimensions.
    $tail  = [string]$RfcGlyph.H * [Math]::Max(3, $RfcWidth - 2 - $label.Length)

    Write-Host ""
    Write-Host ("  " + ([string]$RfcGlyph.H * 2) + $label + $tail) -ForegroundColor Cyan
    Write-LogLine 'STEP' $m
}

function Write-Banner {
    <#
      Outputs a header for script execution.
      Uses distinct Write-Host calls per line to ensure stream capture mechanisms do not insert unintended newlines.
    #>
    param([string]$Title, [string[]]$Lines = @())

    $inner = $RfcWidth - 2
    $g = $RfcGlyph
    Write-Host ""
    Write-Host ("  {0}{1}{2}" -f $g.TL, ([string]$g.H * $inner), $g.TR) -ForegroundColor DarkCyan
    Write-Host ("  {0} {1} {2}" -f $g.V, $Title.PadRight($inner - 2), $g.V) -ForegroundColor Cyan
    Write-Host ("  {0}{1}{2}" -f $g.BL, ([string]$g.H * $inner), $g.BR) -ForegroundColor DarkCyan

    Write-LogLine 'RUN' $Title
    foreach ($l in $Lines) { Write-Info $l }
}

function Write-Result {
    <#
      Outputs the execution summary and remaining items.
      Format is distinct from banners to visually differentiate terminal states.
    #>
    param([string[]]$Problems = @(), [string]$DoneWord = 'Done')

    $g = $RfcGlyph
    Write-Host ""
    if ($Problems.Count -eq 0) {
        Write-Host ("  {0}  {1}" -f $g.Ok, $DoneWord) -ForegroundColor Green
        Write-LogLine 'DONE' $DoneWord
    } else {
        Write-Host ("  {0}  {1} - {2} item(s) pending" -f $g.Warn, $DoneWord, $Problems.Count) -ForegroundColor Yellow
        Write-LogLine 'DONE' "$DoneWord with $($Problems.Count) item(s) pending"
        Write-Host ""
        $i = 1
        foreach ($p in $Problems) {
            Write-Host ("     {0}. {1}" -f $i, $p) -ForegroundColor Yellow
            Write-LogLine 'TODO' "$i. $p"
            $i++
        }
    }
    Write-Host ""
}
