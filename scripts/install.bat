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
%VPY% -m pip install --upgrade pip setuptools wheel --quiet

REM ---------------------------------------------------------------- core stack
REM These MUST install: the dashboard, system control and the API need them.
echo.
echo [..] Installing core packages ...
%VPY% -m pip install "fastapi>=0.110,<1.0" uvicorn websockets httptools watchfiles ^
    colorama python-multipart python-dotenv psutil httpx numpy
if errorlevel 1 (
    echo.
    echo [ERROR] Core installation failed. Most likely causes:
    echo         - no internet connection
    echo         - a Python version newer than the packages support
    echo         Python 3.11 or 3.12 is the most reliable choice.
    pause
    exit /b 1
)
echo [OK] Core packages installed.

REM ------------------------------------------------------------ web / filesystem
echo [..] Installing web, file and automation packages ...
%VPY% -m pip install beautifulsoup4 lxml send2trash pyperclip mss Pillow pytesseract
if errorlevel 1 echo [WARN] Some web/file packages failed - related tools will report why.

REM ------------------------------------------------------------- voice (input)
echo.
echo [..] Installing voice input packages ...
%VPY% -m pip install sounddevice soundfile scipy faster-whisper
if errorlevel 1 (
    echo [WARN] Speech recognition packages failed to install.
    echo        JARVIS will run, but voice input will be unavailable.
    echo        Fix: install Python 3.11 or 3.12 and re-run scripts\install.bat
)
REM openWakeWord imports tqdm/scipy/scikit-learn/onnxruntime/requests at run
REM time. Installing it with --no-deps left it broken ("Wake word unavailable"
REM on the dashboard even though pip reported success), so install real deps.
%VPY% -m pip install openwakeword
if errorlevel 1 (
    echo [WARN] openWakeWord unavailable - push-to-talk still works.
    echo        Install it later with: .venv\Scripts\python -m pip install openwakeword
)
REM First-run model download (about 2 MB); harmless if it fails - retried on use.
%VPY% -c "import openwakeword.utils as u; u.download_models(model_names=['hey_jarvis'])" 2>nul
if errorlevel 1 echo [INFO] Wake-word model will download on first use (needs internet).
%VPY% -m pip install webrtcvad-wheels
if errorlevel 1 echo [INFO] webrtcvad not available - using the built-in energy VAD.

REM ------------------------------------------------------------ voice (output)
echo [..] Installing speech output packages ...
%VPY% -m pip install pyttsx3
if errorlevel 1 echo [WARN] pyttsx3 unavailable - no SAPI5 fallback voice.
%VPY% -m pip install piper-tts
if errorlevel 1 echo [INFO] Piper unavailable - SAPI5 will be used instead.

REM ------------------------------------------------------- Windows integration
echo [..] Installing Windows control packages ...
%VPY% -m pip install pywin32 pycaw comtypes
if errorlevel 1 echo [WARN] Some Windows packages failed - see the README troubleshooting section.
REM pyautogui ships no wheels, so it is installed separately from source.
%VPY% -m pip install pyautogui pygetwindow
if errorlevel 1 echo [WARN] pyautogui unavailable - mouse/keyboard automation will be disabled.

REM ------------------------------------------------------------ search (optional)
%VPY% -m pip install ddgs --quiet
if errorlevel 1 echo [INFO] ddgs unavailable - the keyless DuckDuckGo scraper will be used.

echo.
echo [..] Verifying installation ...
%VPY% -c "import fastapi, uvicorn, psutil, httpx; print('    core web/system stack: OK')"
%VPY% -c "import sounddevice; print('    audio input: OK')" 2>nul || echo    audio input: NOT AVAILABLE
%VPY% -c "import pyttsx3; print('    SAPI5 speech: OK')" 2>nul || echo    SAPI5 speech: NOT AVAILABLE
%VPY% -c "import faster_whisper; print('    faster-whisper: OK')" 2>nul || echo    faster-whisper: NOT AVAILABLE
%VPY% -c "import openwakeword, tqdm, scipy, sklearn, onnxruntime; print('    wake word (openWakeWord): OK')" 2>nul || echo    wake word (openWakeWord): NOT AVAILABLE - push-to-talk still works
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
