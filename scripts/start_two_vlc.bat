@echo off
chcp 65001 >nul 2>&1
setlocal

REM ============================================================
REM  VLC Bitrate OSD - dual instance launcher (Windows)
REM  Starts two VLC players (HTTP ports 8080 / 8081) and two OSD
REM  overlays, each pinned to its own VLC window.
REM  Useful for comparing two encodings or two streams side by side.
REM ============================================================

REM ---------------- Configuration ----------------
set "VLC_PATH=C:\Program Files\VideoLAN\VLC\vlc.exe"
set "PYTHON=python"
set "HTTP_PASS=vlc123"
set "PORT1=8080"
set "PORT2=8081"
REM -----------------------------------------------

if not exist "%VLC_PATH%" (
    echo [ERROR] VLC not found at:
    echo         %VLC_PATH%
    echo         Please edit VLC_PATH in this script.
    pause
    exit /b 1
)

echo ============================================
echo   VLC Bitrate OSD - dual instance launcher
echo ============================================
echo.
echo Enter two video files to compare.
echo Press Enter on either prompt to start a VLC without a file.
echo.

set "VIDEO1="
set "VIDEO2="
set /p "VIDEO1=Video file 1: "
set /p "VIDEO2=Video file 2: "

echo.
echo Starting VLC instance 1 (HTTP port %PORT1%) ...
if defined VIDEO1 (
    start "" "%VLC_PATH%" --extraintf http --http-port %PORT1% --http-password %HTTP_PASS% --no-one-instance --loop "%VIDEO1%"
) else (
    start "" "%VLC_PATH%" --extraintf http --http-port %PORT1% --http-password %HTTP_PASS% --no-one-instance
)

echo Starting VLC instance 2 (HTTP port %PORT2%) ...
if defined VIDEO2 (
    start "" "%VLC_PATH%" --extraintf http --http-port %PORT2% --http-password %HTTP_PASS% --no-one-instance --loop "%VIDEO2%"
) else (
    start "" "%VLC_PATH%" --extraintf http --http-port %PORT2% --http-password %HTTP_PASS% --no-one-instance
)

echo Waiting for VLC to come up ...
timeout /t 5 /nobreak >nul

echo.
echo Starting OSD overlay 1 (port %PORT1%, pinned to VLC window 0) ...
start "VLC-OSD-%PORT1%" "%PYTHON%" "%~dp0..\vlc_bitrate_monitor.py" --password %HTTP_PASS% --port %PORT1% --instance 0

echo Starting OSD overlay 2 (port %PORT2%, pinned to VLC window 1) ...
start "VLC-OSD-%PORT2%" "%PYTHON%" "%~dp0..\vlc_bitrate_monitor.py" --password %HTTP_PASS% --port %PORT2% --instance 1

echo.
echo ============================================
echo   Ready.
echo   - VLC 1 + OSD on port %PORT1%
echo   - VLC 2 + OSD on port %PORT2%
echo   - OSD hotkeys: drag to move, G graph, +/- opacity, Esc/Q quit
echo ============================================
pause

endlocal
