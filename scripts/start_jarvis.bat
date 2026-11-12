@echo off
REM ==========================================================================
REM  J.A.R.V.I.S. - launcher
REM  Starts the backend server and opens the dashboard in your browser.
REM  Usage:
REM     start_jarvis.bat                normal launch (server + dashboard)
REM     start_jarvis.bat --minimised    same, no console window (used at login)
REM     start_jarvis.bat --diagnose     capability report only
REM     start_jarvis.bat --voice-test   microphone + speech test
REM     start_jarvis.bat --headless     server only, no browser
REM ==========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] JARVIS is not installed.
    echo         Run scripts\setup_environment.bat then scripts\install.bat
    pause
    exit /b 1
)

set "VPY=.venv\Scripts\python.exe"
set "MODE=%~1"

if /i "%MODE%"=="--diagnose" goto diagnose
if /i "%MODE%"=="--voice-test" goto voicetest

REM --- Guard against a second instance on the same port ---------------------
%VPY% -c "import socket,sys;s=socket.socket();r=s.connect_ex(('127.0.0.1',8712));s.close();sys.exit(0 if r else 1)" >nul 2>&1
if errorlevel 1 (
    echo [NOTE] JARVIS is already running on port 8712 - opening the dashboard.
    start "" "http://127.0.0.1:8712/"
    exit /b 0
)

if /i "%MODE%"=="--minimised" (
    echo [..] Starting JARVIS in the background ...
    start "" /min "%VPY%" -m jarvis.main --no-browser
    timeout /t 4 /nobreak >nul
    start "" "http://127.0.0.1:8712/"
    exit /b 0
)

if /i "%MODE%"=="--headless" goto headless

echo.
echo  Starting J.A.R.V.I.S. - the dashboard will open in your browser.
echo  Close this window or press Ctrl+C to shut down.
echo.
"%VPY%" -m jarvis.main
goto end

:headless
"%VPY%" -m jarvis.main --no-browser
goto end

:diagnose
echo.
echo  J.A.R.V.I.S. capability report
echo  ------------------------------
"%VPY%" -m jarvis.main --diagnose
echo.
pause
goto end

:voicetest
echo.
echo  Microphone and speech test - speak when prompted.
echo.
"%VPY%" -m jarvis.main --voice-test
echo.
pause
goto end

:end
if errorlevel 1 (
    echo.
    echo [ERROR] JARVIS exited with an error. Check:
    echo         %APPDATA%\Jarvis\logs\jarvis.log
    pause
)
endlocal
