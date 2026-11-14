@echo off
REM ==========================================================================
REM  J.A.R.V.I.S. - environment setup
REM  Checks Python, creates the virtual environment, copies .env.example.
REM  Safe to run repeatedly. Does NOT install packages (install.bat does).
REM ==========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

echo.
echo ============================================================
echo   PROJECT J.A.R.V.I.S. - ENVIRONMENT SETUP
echo ============================================================
echo.

REM ---------------------------------------------------------------- Python
REM Prefer a Python that every dependency supports. The py launcher is asked
REM for 3.12 and 3.11 first, then newer versions, before falling back to
REM whatever "python" resolves to on PATH.
set "PYTHON="
if not defined JARVIS_PYTHON (
    for %%V in (3.12 3.11 3.13 3.14 3.10) do (
        if not defined PYTHON (
            py -%%V --version >nul 2>&1
            if !errorlevel! equ 0 set "PYTHON=py -%%V"
        )
    )
)
if defined JARVIS_PYTHON (
    %JARVIS_PYTHON% --version >nul 2>&1
    if !errorlevel! equ 0 set "PYTHON=%JARVIS_PYTHON%"
)
if not defined PYTHON (
    for %%P in (python py python3) do (
        if not defined PYTHON (
            %%P --version >nul 2>&1
            if !errorlevel! equ 0 set "PYTHON=%%P"
        )
    )
)
if not defined PYTHON (
    echo [ERROR] Python was not found on PATH.
    echo         Install Python 3.10 - 3.12 from https://www.python.org/downloads/
    echo         During installation, tick "Add python.exe to PATH".
    pause
    exit /b 1
)

for /f "tokens=2 delims= " %%V in ('%PYTHON% --version 2^>^&1') do set "PYVER=%%V"
echo [OK] Python %PYVER% found (%PYTHON%).

for /f "tokens=1,2 delims=." %%A in ("%PYVER%") do (
    set "MAJOR=%%A"
    set "MINOR=%%B"
)
if not "%MAJOR%"=="3" (
    echo [ERROR] Python 3 is required. Found %PYVER%.
    pause
    exit /b 1
)
if %MINOR% LSS 9 (
    echo [ERROR] Python 3.10 or newer is required. Found %PYVER%.
    pause
    exit /b 1
)
if %MINOR% GEQ 14 (
    echo [WARN] Python 3.%MINOR% is very new. Most packages install fine, but if
    echo        voice/mouse libraries fail you can install Python 3.12 alongside
    echo        and point .venv at it by deleting the folder and re-running this.
) else if %MINOR% GEQ 13 (
    echo [WARN] Python 3.%MINOR% detected. Core features work; a few optional
    echo        audio packages may lag behind. Python 3.11/3.12 is safest.
)

REM ------------------------------------------------------- virtual environment
if exist ".venv\Scripts\python.exe" (
    echo [OK] Virtual environment already exists.
) else (
    echo [..] Creating virtual environment in .venv ...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Could not create the virtual environment.
        pause
        exit /b 1
    )
    echo [OK] Virtual environment created.
)

REM ------------------------------------------------------------- config files
if exist ".env" (
    echo [OK] .env already present.
) else (
    if exist ".env.example" (
        copy /y ".env.example" ".env" >nul
        echo [OK] Created .env from .env.example - edit it to configure AI providers.
    )
)

REM ------------------------------------------------------------ data folders
if not exist "%APPDATA%\Jarvis" mkdir "%APPDATA%\Jarvis" 2>nul
echo [OK] Data directory: %APPDATA%\Jarvis

REM --------------------------------------------------------------- audio check
echo.
echo [..] Checking audio devices ...
.venv\Scripts\python.exe -c "import sounddevice as sd; d=sd.query_devices(); i=[x for x in d if x['max_input_channels']>0]; print('    microphones found:', len(i)); print('    default input:', sd.default.device[0]); o=[x for x in d if x['max_output_channels']>0]; print('    speakers found:', len(o))" 2>nul
if errorlevel 1 echo [WARN] sounddevice not installed yet - run install.bat, then re-check.

echo.
echo ============================================================
echo   Environment ready. Next step:  scripts\install.bat
echo ============================================================
echo.
pause
endlocal
