@echo off
setlocal EnableDelayedExpansion
chcp 65001 >nul

rem %~dp0 is this file's own folder, with a trailing backslash. Everything below
rem is anchored to it, so this works regardless of the current directory -
rem right-click "Run as administrator" starts you in System32.
set "PLUGIN=%~dp0"
set "PS=powershell -NoProfile -ExecutionPolicy Bypass -File"

rem ---------------------------------------------------------------------------
rem Prerequisites are checked here rather than halfway through the installer.
rem Both failures below are things only the user can fix, and discovering them
rem after a five-minute dependency install is a bad way to find out.
rem ---------------------------------------------------------------------------
:checks
set "PYOK=0"
set "REAPEROK=0"
set "REAPERRUNNING=0"

python --version >nul 2>&1
if not errorlevel 1 set "PYOK=1"

if exist "%APPDATA%\REAPER\reaper.ini" set "REAPEROK=1"

rem Full paths on purpose. Git Bash, MSYS and GnuWin32 all ship a `find` that
rem shadows the Windows one when they are earlier on PATH, and the Unix find
rem rejects these arguments - leaving errorlevel non-zero, which reads as
rem "REAPER is not running". That false negative would wave the user straight
rem into installing with REAPER open, discarding the whole configuration.
tasklist /FI "IMAGENAME eq reaper.exe" /NH 2>nul | "%SystemRoot%\System32\find.exe" /I "reaper.exe" >nul
if not errorlevel 1 set "REAPERRUNNING=1"

:menu
cls
echo ===============================================
echo   REAPER for Claude
echo ===============================================
echo.
echo   This folder IS the plugin. Nothing is copied
echo   anywhere - Claude is pointed at it where it sits.
echo.
echo   Before installing:
if "%PYOK%"=="1"     (echo     [OK]      Python is installed) else (echo     [MISSING] Python  - see [8] below)
if "%REAPEROK%"=="1" (echo     [OK]      REAPER has been launched at least once) else (echo     [MISSING] REAPER  - launch REAPER once, then close it)
if "%REAPERRUNNING%"=="1" (echo     [!]       REAPER is RUNNING - close it before choosing [1]) else (echo     [OK]      REAPER is closed)
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
echo       Load this folder in place so your edits are live,
echo       instead of the marketplace copy. Use one or the other.
echo.
echo   [8] Install Python
echo       Uses winget. Only needed if the check above says MISSING.
echo.
echo   [0] Exit
echo.
set "CHOICE="
set /p CHOICE="Choose: "

if "%CHOICE%"=="1" goto all
if "%CHOICE%"=="2" goto doctor
if "%CHOICE%"=="3" goto python
if "%CHOICE%"=="4" goto reaper
if "%CHOICE%"=="5" goto claude
if "%CHOICE%"=="6" goto repair
if "%CHOICE%"=="7" goto devlink
if "%CHOICE%"=="8" goto pyinstall
if "%CHOICE%"=="0" goto :eof
goto menu

:all
if "%PYOK%"=="0" (
    echo.
    echo   Python is required and is not installed. Choose [8] first.
    goto done
)
if "%REAPEROK%"=="0" (
    echo.
    echo   REAPER has never been launched, so it has no config folder yet.
    echo   Launch REAPER once, close it, then come back.
    goto done
)
if "%REAPERRUNNING%"=="1" (
    echo.
    echo   REAPER is running. It rewrites reaper.ini when it exits, which
    echo   would silently discard the connection settings.
    echo.
    echo   Close REAPER, then press a key to continue.
    pause
)
%PS% "%PLUGIN%install\install.ps1"
goto reaper_steps

:doctor
%PS% "%PLUGIN%install\doctor.ps1"
goto done

:python
%PS% "%PLUGIN%install\install.ps1" -Only python
goto done

:reaper
if "%REAPERRUNNING%"=="1" (
    echo.
    echo   Close REAPER first, then press a key.
    pause
)
%PS% "%PLUGIN%install\install.ps1" -Only reaper
goto reaper_steps

:claude
%PS% "%PLUGIN%install\install.ps1" -Only claude
goto done

:repair
echo.
echo   Close REAPER first, then press a key.
pause
python "%PLUGIN%reaper\enable_reapy.py" --repair
goto done

:devlink
%PS% "%PLUGIN%install\install.ps1" -Only claude -Link
goto done

:pyinstall
cls
echo ===============================================
echo   Install Python
echo ===============================================
echo.
if "%PYOK%"=="1" (
    echo   Python is already installed and visible on PATH. Nothing to do.
    goto done
)

rem Full path for the same reason as find.exe above.
"%SystemRoot%\System32\where.exe" winget >nul 2>&1
if errorlevel 1 (
    echo   winget is not available on this machine.
    echo.
    echo   Install Python by hand instead:
    echo       https://www.python.org/downloads/
    echo.
    echo   On the FIRST screen, tick "Add python.exe to PATH". Without it,
    echo   Claude cannot launch the REAPER server.
    echo.
    goto done
)

echo   This runs:
echo.
echo       winget install -e --id Python.Python.3.12 --custom "PrependPath=1"
echo.
echo   -e forces an exact ID match. Without it "Python.Python.3" is
echo   ambiguous and can resolve to Python 3.0, which is real and useless.
echo.
echo   --custom appends to winget's own silent switches rather than
echo   replacing them, which is what --override would do.
echo.
echo   3.12 rather than the newest release: numba and llvmlite, which
echo   librosa depends on, often take months to publish wheels for a
echo   brand-new Python. 3.12 has wheels for everything today.
echo.
set "GO="
set /p GO="Install it now? [y/N]: "
if /I not "%GO%"=="y" goto menu

echo.
rem InstallAllUsers=0 rather than --scope user: winget matches --scope against
rem the manifest, and this one is a burn bundle that does not declare a user
rem installer, so --scope user fails with "no applicable installer found".
rem Passing the bundle's own switch reaches the same result and never needs
rem elevation.
winget install -e --id Python.Python.3.12 --source winget --accept-package-agreements --accept-source-agreements --custom "PrependPath=1 InstallAllUsers=0 Include_test=0"

echo.
echo   Making this window see the new install...
call :refreshpython

python --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo   Python was installed, but this window still cannot see it.
    echo   PATH is read once when a program starts, so close this window
    echo   and run RunThisToStart.bat again.
) else (
    for /f "delims=" %%V in ('python --version 2^>^&1') do echo   Ready: %%V
)
goto done

rem ---------------------------------------------------------------------------
rem A process reads PATH once, at startup, so a Python installed thirty seconds
rem ago is invisible to this window. Rather than making the user close and
rem reopen, prepend the directory winget just created.
rem
rem Only prepending, never rebuilding PATH from the registry: those values are
rem REG_EXPAND_SZ and contain literal %SystemRoot% that batch will not re-expand,
rem so reconstructing PATH tends to break every other command in the session.
rem ---------------------------------------------------------------------------
:refreshpython
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python312*") do (
    if exist "%%D\python.exe" set "PATH=%%D;%%D\Scripts;%PATH%"
)
for /d %%D in ("%ProgramFiles%\Python312*") do (
    if exist "%%D\python.exe" set "PATH=%%D;%%D\Scripts;%PATH%"
)
goto :eof

rem ---------------------------------------------------------------------------
rem The only steps that cannot be automated. REAPER holds reaper.ini in memory
rem and writes it back on exit, so the settings the installer just wrote do not
rem take effect until REAPER is started fresh.
rem ---------------------------------------------------------------------------
:reaper_steps
echo.
echo ===============================================
echo   Last steps - do these in order
echo ===============================================
echo.
echo   1. Start REAPER.
echo      It reloads reaper.ini at launch, and starts the bridge
echo      listener automatically.
echo.
echo   2. Restart Claude (Desktop or Code) so it picks up the server.
echo.
echo   3. Ask Claude: "Check the current REAPER project info"
echo.
echo   If that answers, you are done.
echo   If it does not, come back and choose [2] Health check.
echo.
pause
goto checks

:done
echo.
pause
goto checks
