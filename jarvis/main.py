"""J.A.R.V.I.S. entry point.

Usage
-----
    python -m jarvis.main                  launch the dashboard + server
    python -m jarvis.main --no-browser     server only
    python -m jarvis.main --diagnose       environment/capability report
    python -m jarvis.main --voice-test     record 5s, transcribe, speak back
    python -m jarvis.main --port 9000      override the port
    python -m jarvis.main --headless-voice start without a browser, listening

On Windows, ``start_jarvis.bat`` calls this with pythonw so no console appears.
"""
from __future__ import annotations

import argparse
import json
import logging
import platform
import sys
import webbrowser
from pathlib import Path

# Allow "python jarvis/main.py" as well as "python -m jarvis.main"
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    __package__ = "jarvis"

from . import __version__, config  # noqa: E402
from .logging_setup import setup as setup_logging  # noqa: E402

log = logging.getLogger("jarvis.main")


BANNER = r"""
     ██╗ █████╗ ██████╗ ██╗   ██╗██╗███████╗
     ██║██╔══██╗██╔══██╗██║   ██║██║██╔════╝
     ██║███████║██████╔╝██║   ██║██║███████╗
██   ██║██╔══██║██╔══██╗╚██╗ ██╔╝██║╚════██║
╚█████╔╝██║  ██║██║  ██║ ╚████╔╝ ██║███████║
 ╚════╝ ╚═╝  ╚═╝╚═╝  ╚═╝  ╚═══╝  ╚═╝╚══════╝
      Just A Rather Very Intelligent System
"""


def diagnose() -> int:
    """Print an honest capability report for this machine."""
    from .computer.platform_support import support
    from .computer import powershell, system_control
    from .core.tool_registry import load_all_tools
    from .voice.speech_output import available_engines
    from .voice.speech_recognition import list_input_devices
    from .voice.wake_word import WakeWordDetector
    from . import database  # noqa: F401  (ensures schema creation)

    reg = load_all_tools()
    report = {
        "version": __version__,
        "python": sys.version.split()[0],
        "platform": f"{platform.system()} {platform.release()}",
        "home": str(config.HOME),
        "config": str(config.CONFIG_PATH),
        "tools": len(reg.all()),
        "capabilities": support.summary(),
        "powershell_available": powershell.is_available(),
        "microphones": list_input_devices(),
        "speech_engines": available_engines(),
        "wake_word": WakeWordDetector().status(),
        "system": {
            "cpu_percent": system_control.cpu_info()["percent"],
            "memory_percent": system_control.memory_info()["percent"],
            "battery": system_control.battery_info(),
            "gpu": system_control.gpu_info(),
        },
        "ai_provider": config.get().ai.provider,
        "ai_model": config.get().ai.model,
    }
    print(BANNER)
    print(json.dumps(report, indent=2, default=str))

    problems = []
    if not support.is_windows:
        problems.append("Not running on Windows: window/volume/power tools are unavailable.")
    if not report["microphones"]:
        problems.append("No microphone detected: voice input will not work.")
    engines = report["speech_engines"]["engines"]
    if not any(engines.values()):
        problems.append("No speech engine: JARVIS will answer in text only.")
    if not engines.get("piper"):
        problems.append("Piper not ready: falling back to SAPI5 (robotic but reliable).")
    print("\nNotes:")
    for problem in problems or ["All core capabilities look available."]:
        print(f"  - {problem}")
    return 0


def voice_test() -> int:
    """End-to-end voice smoke test with real hardware."""
    from .voice.speech_output import speech
    from .voice.speech_recognition import AudioCapture, SpeechRecognizer

    print("Initialising speech engine…")
    print(f"  engine: {speech.engine}")
    print("Initialising Whisper (may download a model on first run)…")
    recognizer = SpeechRecognizer(model_size=config.get().voice.whisper_model)
    if not recognizer.load():
        print(f"FAILED: {recognizer.detail}")
        return 1
    print("Recording 5 seconds — speak now…")
    capture = AudioCapture(device=config.get().voice.input_device)
    try:
        capture.start()
    except RuntimeError as exc:
        print(f"FAILED: {exc}")
        return 1
    import time
    import numpy as np
    blocks = []
    end = time.time() + 5
    while time.time() < end:
        block = capture.read(timeout=0.5)
        if block is not None:
            blocks.append(block)
    capture.stop()
    if not blocks:
        print("FAILED: no audio captured.")
        return 1
    audio = np.concatenate(blocks)
    result = recognizer.transcribe(audio)
    if not result.get("ok"):
        print(f"FAILED: {result.get('error')}")
        return 1
    print(f"Transcribed: {result['text']!r}")
    print("Speaking the transcription back…")
    speech.speak(f"I heard you say: {result['text']}")
    print("Done.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. desktop AI assistant")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open the dashboard automatically")
    parser.add_argument("--window", action="store_true",
                        help="Open the dashboard in a native desktop window "
                             "(requires pywebview; falls back to the browser)")
    parser.add_argument("--headless-voice", action="store_true",
                        help="Start listening immediately without opening a browser")
    parser.add_argument("--diagnose", action="store_true", help="Print a capability report")
    parser.add_argument("--voice-test", action="store_true", help="Run a microphone/TTS test")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)

    setup_logging(level=logging.DEBUG if args.debug else logging.INFO)

    if args.diagnose:
        return diagnose()
    if args.voice_test:
        return voice_test()

    print(BANNER)
    print(f"  version {__version__}  ·  data: {config.HOME}")

    settings = config.get()
    host = args.host or settings.server_host
    port = args.port or settings.server_port
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '') else host}:{port}/"

    if args.headless_voice:
        settings.startup.start_listening = True
        settings.startup.auto_start_server = True
        config.save(settings)

    print(f"  dashboard: {url}")
    print(f"  AI: {settings.ai.provider}/{settings.ai.model}")
    print("  Press Ctrl+C to stop.\n")

    if args.window:
        return _run_desktop_window(url, host, port, open_browser=not args.no_browser)

    if args.headless_voice:
        try:
            from .api.server import run
            run(host=host, port=port, open_browser=False)
        except KeyboardInterrupt:
            print("\nShutting down.")
        return 0

    try:
        from .api.server import run
        run(host=host, port=port, open_browser=not args.no_browser)
    except KeyboardInterrupt:
        print("\nShutting down.")
    except OSError as exc:
        print(f"Could not start the server on {host}:{port} — {exc}", file=sys.stderr)
        print("Another JARVIS instance may already be running, or the port is in use.",
              file=sys.stderr)
        return 1
    return 0


def _run_desktop_window(url: str, host: str, port: int, open_browser: bool) -> int:
    """Start the server in a thread and host the dashboard in a native window."""
    import threading

    from .desktop.window import DesktopWindow, wait_for_server, webview_available

    from .api.server import create_app
    import uvicorn

    app = create_app()
    config_ = uvicorn.Config(app, host=host, port=port, log_level="info", access_log=False)
    server = uvicorn.Server(config_)
    thread = threading.Thread(target=server.run, daemon=True, name="uvicorn")
    thread.start()

    if not wait_for_server(url):
        print("Server did not become ready in time.", file=sys.stderr)
        return 1

    def stop() -> None:
        server.should_exit = True

    if not webview_available():
        print("  pywebview is not installed - opening the browser instead.")
        print("  Install it with: pip install pywebview")
        webbrowser.open(url)
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            server.should_exit = True
        return 0

    window = DesktopWindow(url, on_close=stop)
    shown = window.start()
    server.should_exit = True
    wait = 0
    while thread.is_alive() and wait < 50:
        thread.join(0.1)
        wait += 1
    if not shown:
        webbrowser.open(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
