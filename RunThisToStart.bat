@echo off
setlocal
chcp 65001 >nul
title REAPER for Claude

rem ===========================================================================
rem  REAPER for Claude - setup
rem
rem  Two options for the setup itself, on purpose. Everything it can do belongs
rem  to one of them: put it all in place, or take it all back out. The individual
rem  steps still exist as scripts in install\ and are runnable on their own, but
rem  nobody should have to choose between them to get started.
rem
rem  [3] is not a third way to install. It downloads what [1] would download and
rem  then stops, which is the only useful thing to do from a machine that is not
rem  the one being set up - no internet there, or wiped after every run.
rem
rem      [1] -> install\setup-all.ps1           backup, apps, plugin, health check
rem      [2] -> install\revert-all.ps1          restore that backup, remove our files
rem      [3] -> install\fill-download-cache.ps1 fetch the installers, install nothing
rem ===========================================================================

rem %~dp0 is this file's own folder with a trailing backslash, so the menu works
rem whatever the current directory is - "Run as administrator" starts you in
rem System32.
set "PLUGIN=%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"

:menu
cls
echo ===============================================
echo   REAPER for Claude
echo ===============================================
echo.
echo   Claude works inside REAPER as an audio engineer:
echo   mixing, mastering, MIDI, FX, rendering, and real
echo   DSP measurement.
echo.
echo   -------------------------------------------
echo.
echo   [1] Install Everything
echo.
echo       Backs up your current settings first, then installs
echo       whatever is missing - Python, Git, REAPER, Claude -
echo       and sets up the plugin. Takes several minutes.
echo.
echo       REAPER and Claude are SKIPPED if you already have
echo       them. Your projects, presets and chats are never
echo       touched.
echo.
echo   [2] Revert Everything
echo.
echo       Restores the settings backed up by [1] and removes
echo       what it added. Does not uninstall REAPER, Claude,
echo       Python or Git - it only undoes the setup.
echo.
echo   [3] Prepare Offline Files
echo.
echo       Downloads what [1] needs into downloadCache\ and
echo       installs nothing. For a machine with no internet, or
echo       one you wipe often - fill it here, copy that folder
echo       over there, and [1] installs from disk.
echo.
echo   [0] Exit
echo.

set "CHOICE="
set /p CHOICE="Choose: "

rem `set /p` cannot report end of input: at EOF it leaves the variable alone, so
rem a menu that redraws on empty input would spin forever with stdin closed or
rem redirected. Pressing Enter is a fair way to ask for a redraw, so empty is not
rem an error - but twenty in a row is nobody typing.
if not defined CHOICE goto no_input
set "EMPTIES=0"

rem Keep the first character only. Both options are a single digit, so nothing
rem is lost, and a pasted line or a redirected stdin handing `set /p` more than
rem one line at once can no longer leave a newline inside CHOICE - which would
rem make the comparison below fail to parse rather than redraw.
set "CHOICE=%CHOICE:~0,1%"

if "%CHOICE%"=="1" goto install
if "%CHOICE%"=="2" goto revert
if "%CHOICE%"=="3" goto cache
if "%CHOICE%"=="0" goto quit
goto menu

:no_input
set /a EMPTIES+=1
if %EMPTIES% GEQ 20 goto quit
goto menu

rem ===========================================================================

:install
cls
echo ===============================================
echo   Install Everything
echo ===============================================
echo.
echo   SAVE YOUR WORK in REAPER and Claude before continuing.
echo   Both get closed automatically while this runs.
echo.
echo   From here it is hands-off, except for one thing: if
echo   Claude has to be installed, you will be asked to sign
echo   in. Everything else opens, waits and closes by itself.
echo.
echo   What happens:
echo     1. back up your REAPER and Claude settings
echo     2. install Python, Git, and - only if missing -
echo        REAPER and Claude
echo     3. open each newly installed one, let it finish
echo        setting itself up, then close it
echo     4. set up the plugin and check it works
echo.
echo   Takes several minutes. Vendor installers, not the Store.
echo.
set "GO="
set /p GO="  Start? [y/N]: "
if /I not "%GO%"=="y" goto menu

rem -Confirmed: the user has just read all of the above and agreed. Asking a
rem second time inside the script reads as though something changed.
%PS% "%PLUGIN%install\setup-all.ps1" -Confirmed

rem Captured on its own line, before anything else can overwrite it. setup-all
rem now exits 0 only when it has nothing left to report.
rem
rem This menu used to print "you are done" no matter what came back - the same
rem cheerful block after a run where winget died, REAPER and Claude were never
rem installed, and five things had failed. Sending somebody off to test a setup
rem that did not happen is worse than telling them nothing at all.
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" goto install_ok

echo ===============================================
echo   Setup did not finish
echo ===============================================
echo.
echo   The numbered list above says what is still needed.
echo   Do not start REAPER and Claude expecting this to work
echo   yet - the steps that failed are listed there.
echo.
echo   Fix those, then choose [1] again. It keeps whatever
echo   already worked and only fills in the rest.
echo.
echo   Full log of the run:
echo     %USERPROFILE%\.reaper-for-claude\logs
echo.
pause
goto menu

:install_ok
echo ===============================================
echo   Setup finished
echo ===============================================
echo.
echo   1. Start REAPER.
echo   2. Restart Claude.
echo   3. Ask Claude: "Check the current REAPER project info"
echo.
echo   If it answers, you are done.
echo.
pause
goto menu

:revert
cls
%PS% "%PLUGIN%install\revert-all.ps1"
echo.
pause
goto menu

:cache
cls
echo ===============================================
echo   Prepare Offline Files
echo ===============================================
echo.
echo   Downloads the winget installer, Python, Git, REAPER and
echo   Claude into the downloadCache folder beside this file.
echo.
echo   Nothing is installed and nothing outside that folder is
echo   touched. Around 500 MB. Anything already there is left
echo   alone, so running this twice is cheap.
echo.
%PS% "%PLUGIN%install\fill-download-cache.ps1"
echo.
pause
goto menu

:quit
endlocal
exit /b 0
