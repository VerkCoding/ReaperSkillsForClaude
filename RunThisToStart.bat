@echo off
setlocal
chcp 65001 >nul
title REAPER for Claude

rem ===========================================================================
rem  REAPER for Claude - setup menu
rem
rem  Everything here is a thin front end. The real work lives in install\*.ps1,
rem  which are runnable on their own; this file exists so that double-clicking
rem  is enough, and so the prerequisites are checked before anything slow runs.
rem
rem  Layout:
rem      config          paths and version, defined once
rem      :detect         reads machine state into flags
rem      :menu           draws, reads a choice, dispatches
rem      :opt1..:opt9    one short block per action
rem      subroutines     shared guards and helpers
rem ===========================================================================

rem %~dp0 is this file's own folder with a trailing backslash. Every path below
rem is anchored to it, so the menu works whatever the current directory is -
rem right-click "Run as administrator" starts you in System32.
set "PLUGIN=%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"

rem One source of truth for the Python version. PYFULL is passed to the
rem installer; PYTAG is the directory it creates, derived rather than repeated
rem so the two cannot drift. "3.12" -> "Python312".
set "PYFULL=3.12.10"
set "PYVER=3.12"
set "PYTAG=Python%PYVER:.=%"

rem ===========================================================================
rem  State
rem ===========================================================================
:detect
set "PYOK=0"
set "REAPEROK=0"
set "REAPERRUNNING=0"

python --version >nul 2>&1
if not errorlevel 1 set "PYOK=1"

if exist "%APPDATA%\REAPER\reaper.ini" set "REAPEROK=1"

rem Full path to find.exe on purpose. Git Bash, MSYS and GnuWin32 each ship a
rem `find` that shadows the Windows one when they come first on PATH, and the
rem Unix one rejects these arguments - leaving errorlevel non-zero, which reads
rem as "REAPER is not running". That false negative would wave the user into
rem installing with REAPER open, and REAPER discards reaper.ini edits on exit.
tasklist /FI "IMAGENAME eq reaper.exe" /NH 2>nul | "%SystemRoot%\System32\find.exe" /I "reaper.exe" >nul
if not errorlevel 1 set "REAPERRUNNING=1"

rem ===========================================================================
rem  Menu
rem ===========================================================================
:menu
cls
call :banner "REAPER for Claude"
echo.
echo   This folder IS the plugin. Nothing is copied anywhere -
echo   Claude is pointed at it where it sits.
echo.
echo   Before installing:
if "%PYOK%"=="1"          (echo     [OK]      Python is installed)                        else (echo     [MISSING] Python - choose [8])
if "%REAPEROK%"=="1"      (echo     [OK]      REAPER has been launched at least once)     else (echo     [MISSING] REAPER - launch it once, then close it)
if "%REAPERRUNNING%"=="1" (echo     [!]       REAPER is RUNNING - close it before [1])    else (echo     [OK]      REAPER is closed)
echo.
echo   [1] Install everything
echo       Dependencies, REAPER bridge, distant API, and Claude.
echo       This is the one you want. Takes a few minutes.
echo.
echo   [2] Health check
echo       Diagnose a broken setup. Changes nothing.
echo.
echo   [3] Dependencies only
echo       Build or repair the Python environment.
echo.
echo   [4] REAPER side only
echo       Bridge listener and distant API. Close REAPER first.
echo.
echo   [5] Claude side only
echo       Register the plugin with Claude Code and Desktop.
echo.
echo   [6] Repair REAPER connection
echo       Remove the stray port 2306 web interface. Close REAPER first.
echo.
echo   [7] Developer link
echo       Load this folder in place so your edits are live, instead of
echo       the marketplace copy. Use one route or the other.
echo.
echo   [8] Install Python %PYVER%
echo       Only if the check above says MISSING. Uses winget when you
echo       have it, downloads from python.org when you do not.
echo.
echo   [9] Install or repair winget
echo       Optional. [8] does not need winget, so only bother if you
echo       want winget itself back.
echo.
echo   [0] Exit
echo.

set "CHOICE="
set /p CHOICE="Choose: "

rem `set /p` cannot report end of input: at EOF it simply leaves the variable
rem alone, so a menu that redraws on empty input spins forever when stdin is
rem closed or redirected - pegging a core rather than failing. Pressing Enter is
rem a legitimate way to ask for a redraw, so empty input is not itself an error;
rem twenty in a row is nobody typing.
if not defined CHOICE goto no_input
set "EMPTIES=0"

rem Keep only the first character. Every option is a single digit, so nothing
rem is lost - and it makes the comparison below safe against whatever actually
rem lands in the variable. A pasted line, or a redirected stdin that hands
rem `set /p` more than one line at once, otherwise leaves a newline inside
rem CHOICE, and the `for` below then fails to parse rather than redrawing.
set "CHOICE=%CHOICE:~0,1%"

rem Validate before dispatching. `goto opt%CHOICE%` on a typo would jump to a
rem label that does not exist, which aborts the script instead of redrawing.
set "TARGET="
for %%K in (0 1 2 3 4 5 6 7 8 9) do if "%CHOICE%"=="%%K" set "TARGET=opt%%K"
if not defined TARGET goto menu
goto %TARGET%

:no_input
set /a EMPTIES+=1
if %EMPTIES% GEQ 20 goto opt0
goto menu

rem ===========================================================================
rem  Actions
rem ===========================================================================

:opt0
endlocal
exit /b 0

:opt1
rem Both prerequisites are things only the user can fix, and finding out after
rem a five-minute dependency install is a bad way to find out.
call :require_python || goto pause_return
call :require_reaper || goto pause_return
call :warn_if_reaper_running
%PS% "%PLUGIN%install\install.ps1"
goto final_steps

:opt2
%PS% "%PLUGIN%install\doctor.ps1"
goto pause_return

:opt3
call :require_python || goto pause_return
%PS% "%PLUGIN%install\install.ps1" -Only python
goto pause_return

:opt4
call :require_reaper || goto pause_return
call :warn_if_reaper_running
%PS% "%PLUGIN%install\install.ps1" -Only reaper
goto final_steps

:opt5
%PS% "%PLUGIN%install\install.ps1" -Only claude
goto pause_return

:opt6
call :require_python || goto pause_return
call :warn_if_reaper_running
rem Bare `python` rather than the virtualenv: bootstrap.py installs reapy into
rem the base interpreter too, because REAPER embeds that one and needs to
rem import reapy there. See ensure_reaper_side() in scripts\bootstrap.py.
python "%PLUGIN%reaper\enable_reapy.py" --repair
goto pause_return

:opt7
%PS% "%PLUGIN%install\install.ps1" -Only claude -Link
goto pause_return

:opt8
cls
call :banner "Install Python %PYVER%"
echo.
if "%PYOK%"=="1" (
    echo   Python is already installed and visible on PATH. Nothing to do.
    goto pause_return
)
echo   Installs Python %PYVER% for your user account, with PATH enabled.
echo   No administrator rights needed.
echo.
echo   Uses winget when winget works, and downloads the installer straight
echo   from python.org when it does not - so this does not depend on
echo   winget existing.
echo.
echo   %PYVER% rather than the newest release: numba and llvmlite, which
echo   librosa depends on, often take months to publish wheels for a
echo   brand-new Python.
echo.
call :confirm "Install it now" || goto menu

%PS% "%PLUGIN%install\install-python.ps1" -Version %PYFULL%
call :refresh_path

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   This window still cannot see Python. PATH is read once when a
    echo   program starts, so close this window and run RunThisToStart.bat
    echo   again.
) else (
    for /f "delims=" %%V in ('python --version 2^>^&1') do echo   Ready: %%V
)
goto pause_return

:opt9
cls
call :banner "Install or repair winget"
echo.
echo   You probably do not need this. Option [8] downloads Python directly
echo   when winget is missing, so nothing in this setup depends on it.
echo.
echo   It first tries the offline repair - registering an App Installer
echo   that is present but not registered for your user, which is the
echo   usual state in Windows Sandbox and on Windows Server.
echo.
echo   If that fails it falls back to Microsoft's PSGallery bootstrap,
echo   which needs internet and often fails on those same machines.
echo.
echo   Running this window as administrator gets a machine-wide repair.
echo.
call :confirm "Proceed" || goto menu
%PS% "%PLUGIN%install\repair-winget.ps1"
goto pause_return

rem ===========================================================================
rem  Guards and helpers
rem
rem  These return an exit code so callers can write `call :x || goto y`, which
rem  keeps each action block short enough to read at a glance.
rem ===========================================================================

:banner
echo ===============================================
echo   %~1
echo ===============================================
goto :eof

:confirm
rem %~1 is the question. Anything but y/Y is a no, so a stray keypress cannot
rem start a multi-minute install.
set "ANSWER="
set /p ANSWER="  %~1? [y/N]: "
if /I "%ANSWER%"=="y" exit /b 0
exit /b 1

:require_python
if "%PYOK%"=="1" exit /b 0
echo.
echo   Python is required and is not installed. Choose [8] first.
exit /b 1

:require_reaper
if "%REAPEROK%"=="1" exit /b 0
echo.
echo   REAPER has never been launched, so it has no config folder yet.
echo   Launch REAPER once, close it, then come back.
exit /b 1

:warn_if_reaper_running
rem Not a hard stop: the installer re-checks and skips only the step that
rem cares. But saying it here means the user can fix it before waiting.
if not "%REAPERRUNNING%"=="1" goto :eof
echo.
echo   REAPER is running. It rewrites reaper.ini when it exits, which
echo   would silently discard the connection settings.
echo.
echo   Close REAPER now, then press a key to continue.
pause >nul
goto :eof

:refresh_path
rem A process reads PATH once, at startup, so a Python installed thirty
rem seconds ago is invisible to this window. Prepend the directory the
rem installer created instead of making the user close and reopen.
rem
rem Only prepending, never rebuilding PATH from the registry: those values are
rem REG_EXPAND_SZ and hold a literal %%SystemRoot%% that batch will not
rem re-expand, so reconstructing PATH breaks every later command.
echo.
echo   Making this window see the new install...
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\%PYTAG%*") do (
    if exist "%%D\python.exe" set "PATH=%%D;%%D\Scripts;%PATH%"
)
for /d %%D in ("%ProgramFiles%\%PYTAG%*") do (
    if exist "%%D\python.exe" set "PATH=%%D;%%D\Scripts;%PATH%"
)
goto :eof

rem ===========================================================================
rem  Exits
rem ===========================================================================

:final_steps
rem The only steps that cannot be automated. REAPER holds reaper.ini in memory
rem and writes it back on exit, so what the installer just wrote does not take
rem effect until REAPER is started fresh.
echo.
call :banner "Last steps - do these in order"
echo.
echo   1. Start REAPER.
echo      It reloads reaper.ini at launch and starts the bridge
echo      listener automatically.
echo.
echo   2. Restart Claude, Desktop or Code, so it picks up the server.
echo.
echo   3. Ask Claude: "Check the current REAPER project info"
echo.
echo   If that answers, you are done.
echo   If it does not, come back and choose [2] Health check.
echo.
pause
goto detect

:pause_return
echo.
pause
goto detect
