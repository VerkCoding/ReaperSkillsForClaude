@echo off
setlocal
chcp 65001 >nul
title REAPER for Claude

rem ===========================================================================
rem  REAPER for Claude - setup
rem
rem  Two options on purpose. Everything the setup can do belongs to one of them:
rem  put it all in place, or take it all back out. The individual steps still
rem  exist as scripts in install\ and are runnable on their own, but nobody
rem  should have to choose between them to get started.
rem
rem      [1] -> install\setup-all.ps1    backup, apps, plugin, health check
rem      [2] -> install\revert-all.ps1   restore that backup, remove our files
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
echo   SAVE YOUR WORK in REAPER and Claude first.
echo.
echo   This asks you to close both, then closes them for you
echo   if they are still open. They rewrite their own settings
echo   when they exit, so anything written while they run gets
echo   discarded. Nothing is force-quit - REAPER's "save
echo   changes?" prompt still appears and waits for you.
echo.
echo   This will:
echo     1. back up your REAPER and Claude configuration
echo     2. install Python, Git, and - only if missing -
echo        REAPER and Claude
echo     3. if it installed REAPER, open it once so it can
echo        create its settings, then close it for you
echo     4. if it installed Claude, open it and wait while
echo        you sign in
echo     5. set up the plugin and run a health check
echo.
echo   Everything comes from each vendor's own installer,
echo   not the Microsoft Store.
echo.
set "GO="
set /p GO="  Continue? [y/N]: "
if /I not "%GO%"=="y" goto menu

%PS% "%PLUGIN%install\setup-all.ps1"

echo.
echo ===============================================
echo   Last steps - do these in order
echo ===============================================
echo.
echo   1. Start REAPER.
echo      It reloads its settings at launch and starts the
echo      bridge listener automatically.
echo.
echo   2. Restart Claude, Desktop or Code, so it picks up
echo      the server.
echo.
echo   3. Ask Claude: "Check the current REAPER project info"
echo.
echo   If that answers, you are done.
echo.
pause
goto menu

:revert
cls
%PS% "%PLUGIN%install\revert-all.ps1"
echo.
pause
goto menu

:quit
endlocal
exit /b 0
