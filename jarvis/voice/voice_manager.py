"""Voice orchestration: wake word → capture → transcribe → converse → speak.

Modes
-----
* ``wake``        : wait for "Hey JARVIS", greet, then listen for the command.
* ``push_to_talk``: listen immediately (dashboard mic button / hotkey).
* ``continuous``  : keep listening for commands until told to stop.

The loop runs on a dedicated daemon thread with its own asyncio loop, so a
blocking microphone read never stalls the FastAPI server. All state changes
are published on the event bus for the dashboard.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any, Dict, Optional

import numpy as np

from .. import config
from ..event_bus import EventType, bus
from .speech_output import speech
from .speech_recognition import AudioCapture, SpeechRecognizer, list_input_devices
from .wake_word import FRAME_SAMPLES, WakeWordDetector

log = logging.getLogger(__name__)


class VoiceManager:
    def __init__(self) -> None:
        self.detector: Optional[WakeWordDetector] = None
        self.recognizer = SpeechRecognizer(
            model_size=config.get().voice.whisper_model,
            compute_type=config.get().voice.whisper_compute_type,
            language=config.get().voice.language,
            silence_ms=config.get().voice.vad_silence_ms,
        )
        self.capture: Optional[AudioCapture] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._mode = "idle"          # idle | wake | push_to_talk | continuous
        self._state = "idle"
        self._conversation = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._ptt_requested = threading.Event()
        self.last_error = ""

    # -- wiring -------------------------------------------------------------
    def attach_conversation(self, conversation) -> None:
        self._conversation = conversation

    def _set_state(self, state: str) -> None:
        self._state = state
        bus.publish(EventType.STATE, {"state": state, "voice_mode": self._mode})

    @property
    def mode(self) -> str:
        return self._mode

    @property
    def listening(self) -> bool:
        return self._mode != "idle" and self._thread is not None and self._thread.is_alive()

    # -- lifecycle ----------------------------------------------------------
    def start(self, mode: str = "wake") -> Dict[str, Any]:
        if self.listening:
            return {"ok": False, "message": f"Already listening in {self._mode} mode."}
        settings = config.get().voice
        if not settings.stt_available:
            return {"ok": False, "message": "Voice input is disabled in settings."}

        if not self.recognizer.load():
            return {"ok": False, "message": self.recognizer.detail or "Whisper unavailable."}

        try:
            self.capture = AudioCapture(device=settings.input_device,
                                        samplerate=settings.sample_rate)
            self.capture.start()
        except RuntimeError as exc:
            self.last_error = str(exc)
            return {"ok": False, "message": self.last_error}

        if mode in ("wake", "continuous") and settings.wake_word_enabled:
            if self.detector is None:
                self.detector = WakeWordDetector(settings.wake_word_model)
            elif not self.detector.available:
                # First-run model download may have failed; retry on demand
                # instead of leaving the wake word permanently unavailable.
                self.detector.retry_load()
        if mode == "wake" and (self.detector is None or not self.detector.available):
            bus.notify("Wake word unavailable",
                       (self.detector.detail if self.detector else "openWakeWord missing")
                       + " Falling back to push-to-talk.", level="warning")
            mode = "push_to_talk"

        self._mode = mode
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="voice-loop", daemon=True)
        self._thread.start()
        bus.log(f"Voice listening started ({mode})", source="voice")
        return {"ok": True, "message": f"Listening in {mode.replace('_', ' ')} mode.",
                "data": {"mode": mode}}

    def stop(self) -> Dict[str, Any]:
        self._stop.set()
        self._mode = "idle"
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)
        if self.capture:
            self.capture.stop()
            self.capture = None
        speech.stop()
        self._set_state("idle")
        bus.log("Voice listening stopped", source="voice")
        return {"ok": True, "message": "Voice input stopped."}

    def push_to_talk(self) -> Dict[str, Any]:
        """One-shot listen (also used by the dashboard mic button)."""
        if not self.listening:
            result = self.start("push_to_talk")
            if not result["ok"]:
                return result
        self._ptt_requested.set()
        return {"ok": True, "message": "Listening for your command…"}

    def say(self, text: str) -> None:
        """Speak synchronously (used by the conversation engine)."""
        speech.speak(text)

    def speak_async(self, text: str) -> None:
        speech.speak_async(text)

    def stop_speaking(self) -> None:
        speech.stop()

    # -- main loop ----------------------------------------------------------
    def _run(self) -> None:
        assert self.capture is not None
        settings = config.get().voice
        greeting = settings.greeting
        try:
            while not self._stop.is_set():
                if self._mode == "wake":
                    triggered = self._wait_for_wake_word()
                    if not triggered:
                        continue
                    self._set_state("listening")
                    bus.publish(EventType.WAKE, {"phrase": settings.wake_word_model})
                    if greeting:
                        self.say(greeting)
                elif not self._ptt_requested.is_set() and self._mode == "push_to_talk":
                    if self._stop.wait(0.2):
                        break
                    continue
                elif self._mode == "continuous":
                    self._set_state("listening")
                self._ptt_requested.clear()

                utterance = self.recognizer.capture_utterance(self.capture)
                if utterance is None:
                    self._set_state("wake" if self._mode == "wake" else "idle")
                    continue

                self._set_state("thinking")
                result = self.recognizer.transcribe(utterance.audio)
                if not result.get("ok") or not result.get("text"):
                    if result.get("error"):
                        bus.log(f"Transcription failed: {result['error']}", level="error",
                                source="voice")
                    self._set_state("wake" if self._mode == "wake" else "idle")
                    continue

                text = result["text"].strip()
                bus.publish(EventType.TRANSCRIPT, {"text": text,
                                                   "language": result.get("language", "en")})
                bus.log(f"Heard: {text}", source="voice")
                self._dispatch(text)

                if self._mode == "push_to_talk":
                    self._mode = "idle"
                    self._stop.set()
                    break
                self._set_state("wake" if self._mode == "wake" else "idle")
        except Exception as exc:  # keep the app alive, report honestly
            self.last_error = str(exc)
            log.exception("Voice loop crashed")
            bus.notify("Voice error", str(exc), level="error")
        finally:
            if self.capture:
                self.capture.stop()
            self._set_state("idle")
            bus.publish(EventType.STATE, {"state": "idle", "voice_mode": "idle"})

    def _wait_for_wake_word(self) -> bool:
        assert self.capture is not None
        self._set_state("wake_listening")
        while not self._stop.is_set():
            if self._ptt_requested.is_set():
                return True
            block = self.capture.read(timeout=0.3)
            if block is None:
                continue
            # publish mic level for the visualiser (cheap, 4x/sec cap)
            bus.publish_threadsafe(EventType.AUDIO_LEVEL,
                                   {"level": round(self.capture.level, 4)})
            if self.detector is None or not self.detector.available:
                # no wake word engine: treat any loud speech as the trigger
                if self.capture.level > 0.06:
                    return True
                continue
            if len(block) != FRAME_SAMPLES:
                block = np.resize(block, FRAME_SAMPLES)
            result = self.detector.process_frame(block)
            if result.get("detected"):
                self.detector.reset()
                return True
        return False

    def _dispatch(self, text: str) -> None:
        """Run the conversation coroutine on our own loop."""
        if self._conversation is None:
            return
        if self._loop is None or not self._loop.is_running():
            self._loop = asyncio.new_event_loop()
            threading.Thread(target=self._loop.run_forever, name="voice-async",
                             daemon=True).start()
        future = asyncio.run_coroutine_threadsafe(
            self._conversation.handle(text, speak=True, source="voice"), self._loop)
        try:
            future.result(timeout=300)
        except Exception:
            log.exception("Conversation handling failed in the voice loop")
        finally:
            self._ptt_requested.clear()

    # -- status --------------------------------------------------------------
    def status(self) -> Dict[str, Any]:
        return {
            "mode": self._mode,
            "listening": self.listening,
            "state": self._state,
            "wake_word": self.detector.status() if self.detector else
                         {"available": False, "detail": "not initialised"},
            "stt": self.recognizer.status(),
            "tts": speech.status(),
            "devices": list_input_devices()[:10],
            "level": round(self.capture.level, 4) if self.capture else 0.0,
            "error": self.last_error or self.recognizer.load_error,
        }


#: Application-wide voice orchestrator.  NOTE: deliberately not named
#: ``voice_manager`` — that would shadow this module inside the package.
manager = VoiceManager()
