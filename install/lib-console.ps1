<#
  How the installer talks, and what it writes down.

  Dot-sourced by every script in this folder. Nothing here runs on its own, and
  nothing here decides anything - it only decides how things are said.

  Why it exists
  -------------
  The same five Write-* functions were pasted into six scripts. Identical, so
  nobody noticed; which also meant nobody could improve the output without
  editing it six times, and the one script that wanted a log had to invent one.

  Three things this is for:

    Short.  One line per event, a fixed gutter, and the interesting word first.
            "Git ready" beats "[ok]   Git ready." and reads down the page.

    Shown.  Everything printed is also written to a plain-text log with a
            timestamp and a level. That file is the answer to "what did it say?"
            hours later, and it is the thing to send when something goes wrong.
            It is greppable ASCII on purpose - no colour, no glyphs, no boxes.

    Pretty. Enough structure to follow at a glance while it scrolls, and not one
            character more.

  The function NAMES are the ones the rest of the code already calls. That is
  deliberate: this changes how every line looks and where it goes without a
  single call site having to move.

  On glyphs
  ---------
  Every character here is built from [char] codes rather than typed into the
  file. Windows PowerShell 5.1 reads a .ps1 with no byte-order mark as ANSI, so
  a box-drawing character typed literally does not merely look wrong - it ends
  the string it is in and the script fails to parse. Building them at runtime
  keeps these files pure ASCII, which is also why they survive being edited by
  anything.

  They are still only used when the console can render them. A console left on
  an OEM code page gets the ASCII set instead, which is plainer and correct
  rather than a screenful of question marks.
#>

# ---------------------------------------------------------------------------
# What we can draw with
# ---------------------------------------------------------------------------

$RfcUnicodeConsole = $false
try {
    # Ask the console what code page it is ON, not what .NET thinks it was when
    # this process started. RunThisToStart.bat runs `chcp 65001` before
    # PowerShell exists, but a `chcp` from inside a running session changes the
    # console without updating [Console]::OutputEncoding - and reading only the
    # latter reports an OEM code page on a console that is perfectly capable,
    # which is how this quietly fell back to ASCII the first time it was tried.
    if (-not ('Rfc.Cp' -as [type])) {
        Add-Type -Namespace Rfc -Name Cp -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern uint GetConsoleOutputCP();
'@ -ErrorAction Stop
    }
    if ([Rfc.Cp]::GetConsoleOutputCP() -eq 65001) {
        # Line them up, or UTF-8 glyphs get written through an OEM encoder and
        # arrive as mojibake - worse than the ASCII set they replaced.
        [Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
        $RfcUnicodeConsole = $true
    }
} catch { }

$RfcGlyph = if ($RfcUnicodeConsole) {
    @{
        Ok = [char]0x2713            # check mark
        Info = [char]0x00B7          # middle dot
        Warn = [char]0x0021          # !
        Err = [char]0x00D7           # multiplication sign, reads as a cross
        H = [char]0x2500             # horizontal
        V = [char]0x2502             # vertical
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

$RfcWidth = 62      # everything is drawn to this, so nothing ragged

# The two below are state, and are guarded because this file is now loaded more
# than once per script: lib-app-control.ps1 and lib-download-cache.ps1 each
# dot-source it so they stand on their own, and a dot-source runs in the CALLER's
# scope - so a second load writes over what the first one set up.
#
# Unguarded, that reset $RfcLogPath to $null. A script that dot-sourced one of
# those libraries after Start-RunLog kept printing to the console and stopped
# writing to the log entirely, since Write-LogLine returns at its null check.
# Measured: two logged lines became one, with no error either side of it. Every
# script today happens to load its libraries before opening the log, so this was
# an unwritten ordering rule whose only symptom was a silent hole in the file
# people are told to send. Loading twice is now what the comments already claim
# it is - harmless.
if (-not (Test-Path 'variable:RfcStep'))    { $RfcStep    = 0 }
if (-not (Test-Path 'variable:RfcLogPath')) { $RfcLogPath = $null }

# ---------------------------------------------------------------------------
# The log
# ---------------------------------------------------------------------------

function Start-RunLog {
    <#
      Open the plain-text log every Write-* below also writes to.

      Separate from Start-Transcript, which install-everything.ps1 still uses:
      the transcript is a raw capture including winget's and pip's own chatter,
      and this is the structured trace of what the setup decided and when. The
      first answers "what happened", the second answers "what did WE do", and
      when something goes wrong both are worth having.

      Best effort. A log is a convenience, never a reason not to install.
    #>
    param([string]$Name)

    # A child script joins the log its parent already opened rather than opening
    # a second one, or none.
    #
    # install-everything.ps1 calls configure-plugin.ps1, install-winget.ps1 and
    # install-python.ps1 as separate scripts. Each is its own PowerShell scope,
    # so each had its own idea of whether a log was open - and the answer was
    # "no". That left a hole in the log exactly where the interesting part
    # happens: a real run showed "Configuring the plugin" followed by ninety
    # seconds of silence and then DONE, while the transcript beside it held
    # three hundred lines of detail. The one file people are told to read first
    # was blank precisely where they needed it.
    if ($env:RFC_LOG_PATH) {
        $script:RfcLogPath = $env:RFC_LOG_PATH
        return $script:RfcLogPath
    }

    # Past that point this is a top-level run, not a child joining one, so the
    # step counter starts over. Tied to opening the log rather than left to each
    # script to remember, because "am I the parent?" is a question already
    # answered here and answering it twice is how the two would drift apart.
    Remove-Item Env:\RFC_STEP -ErrorAction SilentlyContinue

    try {
        $dir = Join-Path $env:USERPROFILE '.reaper-for-claude\logs'
        New-Item -ItemType Directory -Force -Path $dir -ErrorAction Stop | Out-Null
        $script:RfcLogPath = Join-Path $dir ("{0}-{1}.log" -f $Name, (Get-Date -Format 'yyyy-MM-dd_HHmmss'))
        Write-LogLine 'RUN' "$Name  |  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')  |  PowerShell $($PSVersionTable.PSVersion)"

        # Keep the last handful. These are small, but unbounded is its own mess.
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
      One timestamped, level-tagged, plain-ASCII line. Never throws.

      Non-ASCII is stripped rather than encoded. This file exists to be opened
      by whatever is to hand - Notepad, findstr, a pasted chat message - and any
      of those reading a UTF-8 byte as ANSI turns a tidy line into one with
      stray characters in it, which is exactly the kind of noise that makes
      someone distrust a log. Nothing here needs more than ASCII to say.
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

# ---------------------------------------------------------------------------
# The vocabulary
#
# One gutter width for all of them, so the text starts in the same column and
# the eye can run straight down it.
# ---------------------------------------------------------------------------

function Write-Ok($m)    { Write-Host "  $($RfcGlyph.Ok)  $m"   -ForegroundColor Green;    Write-LogLine 'OK'   $m }
function Write-Info($m)  { Write-Host "  $($RfcGlyph.Info)  $m" -ForegroundColor DarkGray; Write-LogLine 'INFO' $m }
function Write-Warn2($m) { Write-Host "  $($RfcGlyph.Warn)  $m" -ForegroundColor Yellow;   Write-LogLine 'WARN' $m }
function Write-Err($m)   { Write-Host "  $($RfcGlyph.Err)  $m"  -ForegroundColor Red;      Write-LogLine 'FAIL' $m }

function Write-Step {
    <#
      A numbered section rule. The number is sequential and carries no total on
      purpose - steps here are conditional, and a "3 of 7" that turns out to be
      3 of 5 is worse than no number at all.
    #>
    param([string]$m)

    # Carried in the environment for the same reason the log path is: a child
    # script is its own PowerShell scope, so $RfcStep restarted at zero there
    # and the console showed steps 1..5 from the parent followed by 1.. again
    # from configure-plugin.ps1, as if the run had begun over.
    #
    # -as rather than a cast. [int]$env:RFC_STEP throws on anything non-numeric,
    # and a cast failure is terminating whatever $ErrorActionPreference says - so
    # one odd value in a short, generic environment variable killed the installer
    # at its next section rule, naming neither the variable nor the script. -as
    # yields $null instead, which falls back to this script's own count.
    $inherited = if ($env:RFC_STEP) { $env:RFC_STEP -as [int] } else { $null }
    $script:RfcStep = if ($null -ne $inherited) { $inherited + 1 } else { $script:RfcStep + 1 }
    $env:RFC_STEP   = $script:RfcStep

    $label = " $($script:RfcStep) $($RfcGlyph.Dot) $m "
    # -2 for the two leading rule characters, so a step rule ends in the same
    # column as the banner box above it. Two characters out is not a bug, but a
    # column that almost lines up looks like a mistake, and the whole point of
    # drawing anything is that it reads as deliberate.
    $tail  = [string]$RfcGlyph.H * [Math]::Max(3, $RfcWidth - 2 - $label.Length)

    Write-Host ""
    Write-Host ("  " + ([string]$RfcGlyph.H * 2) + $label + $tail) -ForegroundColor Cyan
    Write-LogLine 'STEP' $m
}

function Write-Banner {
    <#
      The one boxed header a script opens with.

      Every line is a single Write-Host. -NoNewline would allow two colours in
      one line, and costs more than it is worth: the pieces are reassembled with
      newlines between them by anything that captures the stream, so a banner
      that looked right on screen arrived in the transcript - and in a piped
      run - broken across five lines. One call, one colour, one line, every
      time.
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
      The closing block: one word for the outcome, then whatever is left to do.

      Deliberately not a banner. A run that failed should not look like a run
      that succeeded with a different word in the middle of it, so the colour
      carries the meaning and the list carries the detail.
    #>
    param([string[]]$Problems = @(), [string]$DoneWord = 'DONE')

    $g = $RfcGlyph
    Write-Host ""
    if ($Problems.Count -eq 0) {
        Write-Host ("  {0}  {1}" -f $g.Ok, $DoneWord) -ForegroundColor Green
        Write-LogLine 'DONE' $DoneWord
    } else {
        Write-Host ("  {0}  {1} - {2} thing(s) still to do" -f $g.Warn, $DoneWord, $Problems.Count) -ForegroundColor Yellow
        Write-LogLine 'DONE' "$DoneWord with $($Problems.Count) problem(s)"
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
