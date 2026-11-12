"""Text-to-speech.

Engines, in preference order:
  1. Piper (neural, natural) — needs a downloaded .onnx voice in JARVIS_HOME/models.
  2. Windows SAPI5 via pyttsx3 — always available on Windows, robotic but reliable.
  3. espeak-ng (Linux dev/CI) — so the pipeline can still be exercised.

Speech is serialised through one worker thread: there is never overlapping
audio, and :meth:`SpeechOutput.stop` interrupts mid-sentence.
"""
from __future__ import annotations

import io
import logging
import os
import shutil
import subprocess
import threading
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config
from ..event_bus import EventType, bus

log = logging.getLogger(__name__)

MODEL_DIR = config.HOME / "models" / "piper"


def piper_voice_path(voice: str) -> Optional[Path]:
    for suffix in (".onnx", ".onnx.json"):
        pass
    candidate = MODEL_DIR / f"{voice}.onnx"
    if candidate.exists():
        return candidate
    for path in MODEL_DIR.glob("*.onnx"):
        if path.stem == voice or voice in path.stem:
            return path
    return None


def available_engines() -> Dict[str, Any]:
    engines: Dict[str, Any] = {"piper": False, "sapi": False, "espeak": False}
    detail: Dict[str, str] = {}
    try:
        import piper  # noqa: F401  # type: ignore
        engines["piper"] = True
        detail["piper"] = "piper-tts installed"
    except ImportError:
        detail["piper"] = "install piper-tts and a voice model"
    try:
        import pyttsx3  # noqa: F401  # type: ignore
        engines["sapi"] = True
        detail["sapi"] = "pyttsx3 available (SAPI5 on Windows)"
    except ImportError:
        detail["sapi"] = "install pyttsx3"
    if shutil.which("espeak-ng") or shutil.which("espeak"):
        engines["espeak"] = True
        detail["espeak"] = "espeak binary found"
    voices = [p.stem for p in MODEL_DIR.glob("*.onnx")] if MODEL_DIR.exists() else []
    return {"engines": engines, "detail": detail, "piper_voices": voices,
            "selected": config.get().speech.engine}


class SpeechOutput:
    def __init__(self) -> None:
        self._engine_name = ""
        self._piper_voice = None
        self._sapi = None
        self._lock = threading.Lock()
        self._speaking = False
        self._stop_flag = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._queue: List[str] = []
        self._process: Optional[subprocess.Popen] = None
        self.last_error = ""
        self._init_engine()

    # -- engine selection --------------------------------------------------
    def _init_engine(self) -> None:
        settings = config.get().speech
        preferred = settings.engine.lower()
        if preferred in ("auto", "piper"):
            if self._try_piper(settings.voice):
                return
            if preferred == "piper":
                log.warning("Piper requested but unavailable; falling back")
        if preferred in ("auto", "sapi"):
            if self._try_sapi():
                return
        if self._try_espeak():
            return
        self._engine_name = "none"
        self.last_error = ("No speech engine available. Install 'pyttsx3' on Windows "
                           "(SAPI5) or 'piper-tts' with a voice model.")
        log.warning(self.last_error)

    def _try_piper(self, voice: str) -> bool:
        try:
            from piper import PiperVoice  # type: ignore
        except ImportError:
            return False
        path = piper_voice_path(voice)
        if path is None:
            log.info("Piper installed but no voice model found in %s", MODEL_DIR)
            return False
        try:
            self._piper_voice = PiperVoice.load(str(path))
            self._engine_name = "piper"
            log.info("Piper voice loaded: %s", path.name)
            return True
        except Exception as exc:
            log.warning("Piper load failed: %s", exc)
            return False

    def _try_sapi(self) -> bool:
        try:
            import pyttsx3  # type: ignore
        except ImportError:
            return False
        try:
            self._sapi = pyttsx3.init()
            rate = config.get().speech.rate
            self._sapi.setProperty("rate", int(175 * max(0.5, min(rate, 2.0))))
            volume = config.get().speech.volume
            self._sapi.setProperty("volume", max(0.0, min(volume, 1.0)))
            self._engine_name = "sapi"
            log.info("SAPI5 speech engine ready")
            return True
        except Exception as exc:
            log.warning("pyttsx3 initialisation failed: %s", exc)
            return False

    def _try_espeak(self) -> bool:
        binary = shutil.which("espeak-ng") or shutil.which("espeak")
        if not binary:
            return False
        self._engine_name = "espeak"
        self._espeak_binary = binary
        log.info("espeak engine ready (%s)", binary)
        return True

    # -- public API ---------------------------------------------------------
    @property
    def engine(self) -> str:
        return self._engine_name

    @property
    def speaking(self) -> bool:
        return self._speaking

    def status(self) -> Dict[str, Any]:
        return {"engine": self._engine_name, "speaking": self._speaking,
                "error": self.last_error,
                "voices": [p.stem for p in MODEL_DIR.glob("*.onnx")] if MODEL_DIR.exists() else []}

    def reload(self) -> None:
        self.stop()
        self._piper_voice = None
        self._sapi = None
        self._init_engine()

    def speak_async(self, text: str) -> None:
        """Queue text to be spoken; returns immediately."""
        text = (text or "").strip()
        if not text or self._engine_name == "none":
            if not text:
                return
            self.last_error = "No speech engine configured."
            return
        with self._lock:
            self._queue.append(text)
            if self._worker is None or not self._worker.is_alive():
                self._worker = threading.Thread(target=self._drain, name="tts",
                                                daemon=True)
                self._worker.start()

    def speak(self, text: str, timeout: Optional[float] = None) -> bool:
        """Speak synchronously (blocking until the audio finishes)."""
        text = (text or "").strip()
        if not text:
            return False
        if self._engine_name == "none":
            self.last_error = "No speech engine configured."
            return False
        self._stop_flag.clear()
        done = threading.Event()
        error: Dict[str, str] = {}

        def runner() -> None:
            try:
                self._speak_blocking(text)
            except Exception as exc:  # pragma: no cover
                error["message"] = str(exc)
                log.exception("Speech failed")
            finally:
                done.set()

        thread = threading.Thread(target=runner, name="tts-sync", daemon=True)
        thread.start()
        done.wait(timeout)
        if not done.is_set():
            self.stop()
        return not error and done.is_set()

    def stop(self) -> None:
        """Interrupt speech immediately."""
        self._stop_flag.set()
        with self._lock:
            self._queue.clear()
        if self._process is not None:
            try:
                self._process.terminate()
            except Exception:
                pass
            self._process = None
        if self._sapi is not None and self._speaking:
            try:
                self._sapi.stop()
            except Exception:
                pass
        self._speaking = False
        bus.publish(EventType.STATE, {"state": "idle"})

    # -- internals ----------------------------------------------------------
    def _drain(self) -> None:
        while True:
            with self._lock:
                if not self._queue:
                    self._worker = None
                    return
                text = self._queue.pop(0)
            if self._stop_flag.is_set():
                continue
            try:
                self._speak_blocking(text)
            except Exception:
                log.exception("Speech failed")
                self.last_error = "Speech output error (see log)"

    def _speak_blocking(self, text: str) -> None:
        self._speaking = True
        bus.publish(EventType.STATE, {"state": "speaking"})
        try:
            if self._engine_name == "piper":
                self._speak_piper(text)
            elif self._engine_name == "sapi":
                self._speak_sapi(text)
            elif self._engine_name == "espeak":
                self._speak_espeak(text)
        finally:
            self._speaking = False
            if not self._queue:
                bus.publish(EventType.STATE, {"state": "idle"})

    def _speak_sapi(self, text: str) -> None:
        assert self._sapi is not None
        self._sapi.say(text)
        self._sapi.runAndWait()

    def _speak_espeak(self, text: str) -> None:
        rate = int(165 * max(0.5, min(config.get().speech.rate, 2.0)))
        self._process = subprocess.Popen(
            [self._espeak_binary, "-s", str(rate), "-v", "en-gb", text],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._process.wait()
        self._process = None

    def _speak_piper(self, text: str) -> None:
        assert self._piper_voice is not None
        buffer = io.BytesIO()
        sample_rate = getattr(self._piper_voice.config, "sample_rate", 22050)
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            try:
                for chunk in self._piper_voice.synthesize(text):
                    wav.writeframes(chunk.audio_int16_bytes)
            except AttributeError:  # older piper API
                self._piper_voice.synthesize(text, wav)
        buffer.seek(0)
        self._play_wav(buffer, sample_rate)

    def _play_wav(self, buffer: io.BytesIO, samplerate: int) -> None:
        try:
            import sounddevice as sd  # type: ignore
            with wave.open(buffer, "rb") as wav:
                frames = wav.readframes(wav.getnframes())
            import numpy as np
            audio = np.frombuffer(frames, dtype=np.int16)
            device = config.get().speech.output_device
            sd.play(audio, samplerate=wav.getframerate() if 'wav' in dir() else samplerate,
                    device=device)
            while sd.get_stream().active:
                if self._stop_flag.is_set():
                    sd.stop()
                    return
                time.sleep(0.05)
        except Exception as exc:
            log.warning("sounddevice playback failed (%s); trying winsound", exc)
            if os.name == "nt":
                import winsound
                buffer.seek(0)
                winsound.PlaySound(buffer.read(), winsound.SND_MEMORY)


speech = SpeechOutput()
