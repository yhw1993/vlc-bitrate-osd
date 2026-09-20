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
set "PYTHON=python"
set "HTTP_PORT=8080"
set "HTTP_PASS=vlc123"
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
