@echo off
REM ==========================================================================
REM  J.A.R.V.I.S. - dependency installation
REM  Installs Python packages, then verifies the AI backend (Ollama) and
REM  offers to download a Piper voice model. Re-runnable.
REM ==========================================================================
setlocal EnableDelayedExpansion
cd /d "%~dp0.."

echo.
echo ============================================================
echo   PROJECT J.A.R.V.I.S. - INSTALLATION
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] No virtual environment found. Run scripts\setup_environment.bat first.
    pause
    exit /b 1
)

set "VPY=.venv\Scripts\python.exe"

echo [..] Upgrading pip ...
%VPY% -m pip install --upgrade pip --quiet

echo [..] Installing dependencies from requirements.txt (this can take several minutes) ...
%VPY% -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [WARN] Some packages failed. Core functionality may still work.
    echo        Common causes:
    echo          - missing C++ build tools  (https://visualstudio.microsoft.com/visual-cpp-build-tools/)
    echo          - no internet connection
    echo        Retrying without the heavy optional audio extras ...
    %VPY% -m pip install fastapi "uvicorn[standard]" psutil httpx python-dotenv ^
        sounddevice numpy pyautogui pyperclip mss Pillow beautifulsoup4 lxml send2trash
)

echo.
echo [..] Verifying installation ...
%VPY% -c "import fastapi, uvicorn, psutil, httpx; print('    core web/system stack: OK')"
%VPY% -c "import sounddevice; print('    audio input: OK')" 2>nul || echo    audio input: NOT AVAILABLE
%VPY% -c "import pyttsx3; print('    SAPI5 speech: OK')" 2>nul || echo    SAPI5 speech: NOT AVAILABLE
%VPY% -c "import faster_whisper; print('    faster-whisper: OK')" 2>nul || echo    faster-whisper: NOT AVAILABLE
%VPY% -c "import openwakeword; print('    openwakeword: OK')" 2>nul || echo    openwakeword: NOT AVAILABLE
%VPY% -c "import pyautogui; print('    input automation: OK')" 2>nul || echo    input automation: NOT AVAILABLE
%VPY% -c "import win32gui; print('    window control: OK')" 2>nul || echo    window control: NOT AVAILABLE

echo.
echo [..] Initialising database and configuration ...
%VPY% -m jarvis.main --diagnose > "%TEMP%\jarvis_diag.txt" 2>&1
if errorlevel 1 (
    echo [ERROR] Self-test failed. Diagnostics:
    type "%TEMP%\jarvis_diag.txt"
) else (
    echo [OK] Self-test passed. Capability summary:
    %VPY% tools\report_capabilities.py
)

REM ------------------------------------------------------------------ Ollama
echo.
where ollama >nul 2>&1
if errorlevel 1 (
    echo [NOTE] Ollama is not installed. JARVIS works without it (commands, files,
    echo        system control), but conversation needs a language model.
    echo        Download: https://ollama.com/download/windows
    echo        Then run:  ollama pull llama3.1
) else (
    echo [OK] Ollama detected.
    ollama list 2>nul | findstr /i "llama qwen mistral phi gemma" >nul
    if errorlevel 1 (
        echo [NOTE] No models installed yet. Pull one now? It is ~4.7 GB.
        choice /c YN /m "         Run 'ollama pull llama3.1'"
        if !errorlevel! equ 1 ollama pull llama3.1
    ) else (
        echo [OK] At least one model is installed.
    )
)

REM ------------------------------------------------------- Piper voice model
set "VOICE_DIR=%APPDATA%\Jarvis\models\piper"
if not exist "%VOICE_DIR%" mkdir "%VOICE_DIR%" 2>nul
if exist "%VOICE_DIR%\*.onnx" (
    echo [OK] Piper voice model present.
) else (
    echo.
    echo [NOTE] Optional: download the Piper neural voice ^(~65 MB^) for natural speech.
    echo        Without it JARVIS uses the built-in Windows SAPI5 voice.
    choice /c YN /m "         Download the en_US-lessac-medium voice now"
    if !errorlevel! equ 1 (
        echo [..] Downloading Piper voice ...
        powershell -NoProfile -ExecutionPolicy Bypass -Command ^
          "$b='https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium';" ^
          "Invoke-WebRequest -Uri \"$b/en_US-lessac-medium.onnx\" -OutFile '%VOICE_DIR%\en_US-lessac-medium.onnx' -UseBasicParsing;" ^
          "Invoke-WebRequest -Uri \"$b/en_US-lessac-medium.onnx.json\" -OutFile '%VOICE_DIR%\en_US-lessac-medium.onnx.json' -UseBasicParsing"
        if errorlevel 1 (
            echo [WARN] Download failed. JARVIS will use SAPI5 instead.
        ) else (
            echo [OK] Piper voice installed.
        )
    )
)

REM ---------------------------------------------------------------- Tesseract
where tesseract >nul 2>&1
if errorlevel 1 (
    echo.
    echo [NOTE] Optional: Tesseract OCR enables "what is on my screen" reading.
    echo        Install from https://github.com/UB-Mannheim/tesseract/wiki
) else (
    echo [OK] Tesseract OCR detected.
)

echo.
echo ============================================================
echo   INSTALLATION COMPLETE
echo.
echo   Start JARVIS:  scripts\start_jarvis.bat
echo   Diagnostics:   .venv\Scripts\python -m jarvis.main --diagnose
echo ============================================================
echo.
pause
endlocal
