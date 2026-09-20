@echo off
chcp 65001 >nul 2>&1
setlocal

REM ============================================================
REM  VLC Bitrate OSD - single instance launcher (Windows)
REM  Starts VLC with its HTTP interface enabled, then optionally
REM  launches the Python OSD overlay window.
REM ============================================================

REM ---------------- Configuration ----------------
REM Edit these paths for your machine.
set "VLC_PATH=C:\Program Files\VideoLAN\VLC\vlc.exe"
REM Leave PYTHON empty to auto-detect an interpreter that has tkinter,
REM or pin it to a full path, e.g. set "PYTHON=D:\Python311\python.exe"
REM A machine-local override can also go in scripts\local.bat (git-ignored).
set "PYTHON="
set "HTTP_PORT=8080"
set "HTTP_PASS=vlc123"
REM -----------------------------------------------

REM Optional machine-local override (git-ignored). Set PYTHON or VLC_PATH in
REM scripts\local.bat so your personal paths never end up in the repository.
if exist "%~dp0local.bat" call "%~dp0local.bat"

REM ---------------- Python check ----------------
REM The overlay is a tkinter window, and tkinter is an optional component:
REM several Windows Python bundles and most Linux distros without python3-tk
REM do not ship it. Launching a broken overlay silently is worse than
REM refusing to start, so the interpreter is verified up front.
if not defined PYTHON (
    for %%P in (python py python3) do (
        if not defined PYTHON (
            %%P -c "import tkinter" >nul 2>&1 && set "PYTHON=%%P"
        )
    )
)
if not defined PYTHON (
    echo [ERROR] No Python with tkinter found ^(tried: python, py, python3^).
    echo         The OSD overlay needs tkinter. Either install it, or set
    echo         PYTHON in this script to a full path that has it, e.g.
    echo             set "PYTHON=D:\Python311\python.exe"
    pause
    exit /b 1
)
echo Using Python: %PYTHON%
REM -----------------------------------------------

if not exist "%VLC_PATH%" (
    echo [ERROR] VLC not found at:
    echo         %VLC_PATH%
    echo         Please edit VLC_PATH in this script.
    pause
    exit /b 1
)

echo ============================================
echo   VLC Bitrate OSD launcher
echo ============================================
echo.
echo Starting VLC with HTTP interface on port %HTTP_PORT% ...
start "" "%VLC_PATH%" --extraintf http --http-port %HTTP_PORT% --http-password %HTTP_PASS%

echo.
echo Next steps:
echo   1. Play a video in VLC
echo   2. Launch the OSD overlay from another terminal:
echo        %PYTHON% vlc_bitrate_monitor.py --password %HTTP_PASS%
echo   3. Or use the VLC menu: View -^> Bitrate OSD
echo.

set /p "choice=Launch the Python OSD overlay now? (Y/N) "
if /i "%choice%"=="Y" (
    echo Starting OSD overlay ...
    start "VLC-OSD-%HTTP_PORT%" "%PYTHON%" "%~dp0..\vlc_bitrate_monitor.py" --password %HTTP_PASS% --port %HTTP_PORT%
)

endlocal
