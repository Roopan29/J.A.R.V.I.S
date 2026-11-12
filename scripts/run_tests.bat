@echo off
REM Run the automated test suite.
setlocal
cd /d "%~dp0.."
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] Run setup_environment.bat and install.bat first.
    pause
    exit /b 1
)
echo Running JARVIS test suite...
.venv\Scripts\python.exe -m pytest jarvis/tests -v
echo.
echo Done. Exit code: %errorlevel%
pause
endlocal
