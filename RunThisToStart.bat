@echo off
setlocal
chcp 65001 >nul
title REAPER for Claude

rem ===========================================================================
rem  REAPER for Claude - setup
rem
rem  The menu simplifies access to the underlying PowerShell scripts to prevent
rem  user error when ordering execution steps.
rem  Option [3] exists to facilitate installation in offline environments by
rem  populating the cache without executing the installer.
rem ===========================================================================

rem  Resolves the absolute path to prevent execution failures when the script
rem  is invoked from a different working directory, such as System32 during
rem  elevation.
set "PLUGIN=%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"

:menu
cls
echo ===============================================
echo   REAPER for Claude
echo ===============================================
echo.
echo   This integration connects Claude with REAPER.
echo   It provides tools for mixing, mastering, MIDI,
echo   FX, rendering, and DSP measurement.
echo.
echo   -------------------------------------------
echo.
echo   [1] Install Everything
echo.
echo       Backs up settings, installs missing dependencies
echo       (Python, Git, REAPER, Claude), and configures
echo       the plugin.
echo.
echo       Skips REAPER and Claude if already installed.
echo       Existing user data is not modified.
echo.
echo   [2] Revert Everything
echo.
echo       Restores settings from the backup and removes
echo       plugin files. Does not uninstall dependencies
echo       (REAPER, Claude, Python, Git).
echo.
echo   [3] Prepare Offline Files
echo.
echo       Downloads dependencies into downloadCache\ without
echo       installing them. Use this to prepare files for an
echo       offline environment.
echo.
echo   [0] Exit
echo.

set "CHOICE="
set /p CHOICE="Choose: "

rem  Prevents infinite loops when stdin is redirected or closed, as `set /p`
rem  does not update the variable on EOF. Allows intentional redraws via Enter
rem  while terminating after excessive empty inputs.
if not defined CHOICE goto no_input
set "EMPTIES=0"

rem  Prevents syntax errors during evaluation by discarding unexpected input
rem  length or trailing characters from pasted input.
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
echo   Save open work in REAPER and Claude. Both applications
echo   will be closed during this process.
echo.
echo   A Claude sign-in is required if the application is not
echo   already installed.
echo.
echo   Execution sequence:
echo     1. Create configuration backups.
echo     2. Install missing dependencies.
echo     3. Initialize new installations.
echo     4. Configure the plugin and run a health check.
echo.
set "GO="
set /p GO="  Start? [y/N]: "
if /I not "%GO%"=="y" goto menu

rem  Bypasses the secondary confirmation prompt in the PowerShell script to
rem  avoid redundant user interaction.
%PS% "%PLUGIN%install\install-everything.ps1" -Confirmed

rem  Captures the exit code immediately to ensure accurate error reporting.
rem  Prevents displaying a success message if the underlying PowerShell script
rem  encountered failures.
set "RC=%ERRORLEVEL%"

rem ---------------------------------------------------------------------------
rem  Installs the Visual C++ runtime to fulfill dependencies for compiled
rem  Python wheels (e.g., librosa, soxr), which require msvcp140.dll.
rem  Windows does not include this DLL by default.
rem  Both x64 and x86 architectures are included to support existing 64-bit
rem  requirements and potential future 32-bit integrations.
rem  Execution is placed after capturing the previous script's exit code to
rem  preserve the error state. It includes a winget availability check to
rem  prevent execution errors if the package manager installation failed.
rem ---------------------------------------------------------------------------
where winget >nul 2>&1
if errorlevel 1 goto after_vcredist

echo.
echo   Visual C++ runtime (x64 and x86)...
winget install -e --id Microsoft.VCRedist.2015+.x64 --source winget --accept-package-agreements --accept-source-agreements
winget install -e --id Microsoft.VCRedist.2015+.x86 --source winget --accept-package-agreements --accept-source-agreements

:after_vcredist

echo.
if "%RC%"=="0" goto install_ok

echo ===============================================
echo   Setup did not finish
echo ===============================================
echo.
echo   Incomplete steps are listed above. Resolve the reported
echo   issues before continuing.
echo.
echo   Select [1] again to resume installation. Completed steps
echo   will be skipped.
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
echo   2. Start Claude.
echo   3. Prompt Claude: "Check the current REAPER project info"
echo.
echo   If the project information is returned, the setup is complete.
echo.
pause
goto menu

:revert
cls
%PS% "%PLUGIN%install\revert-everything.ps1"
echo.
pause
goto menu

:cache
cls
echo ===============================================
echo   Prepare Offline Files
echo ===============================================
echo.
echo   Downloads the required installers into the downloadCache folder.
echo.
echo   No installation occurs. Files already present in the cache
echo   are skipped.
echo.
%PS% "%PLUGIN%install\fill-download-cache.ps1"
echo.
pause
goto menu

:quit
endlocal
exit /b 0
