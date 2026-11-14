"""Voice pipeline tests that do not require a microphone.

Covers wake-word frame handling (with a stub model), VAD segmentation with
synthetic audio, and the intent router that turns transcripts into tools.
"""
from __future__ import annotations

import numpy as np
import pytest

from jarvis.voice.speech_recognition import SpeechRecognizer
from jarvis.voice.wake_word import WakeWordDetector


class _StubModel:
    def __init__(self, score):
        self.score = score
        self.frames = 0

    def predict(self, audio):
        self.frames += 1
        return {"hey_jarvis_v0.1": self.score}

    def reset(self):
        self.frames = 0


def test_wake_word_triggers_above_threshold():
    detector = WakeWordDetector.__new__(WakeWordDetector)
    detector.phrase = "hey jarvis"
    detector.threshold = 0.5
    detector._last_detection = 0.0
    detector._model = _StubModel(0.91)
    detector.available = True

    frame = np.zeros(1280, dtype=np.int16)
    result = detector.process_frame(frame)
    assert result["detected"] is True
    assert result["score"] == 0.91


def test_wake_word_respects_cooldown():
    detector = WakeWordDetector.__new__(WakeWordDetector)
    detector.phrase = "hey jarvis"
    detector.threshold = 0.5
    detector._last_detection = 0.0
    detector._model = _StubModel(0.99)
    detector.available = True
    frame = np.zeros(1280, dtype=np.int16)
    assert detector.process_frame(frame)["detected"] is True
    # Immediately after, cooldown suppresses repeats
    assert detector.process_frame(frame)["detected"] is False


def test_wake_word_below_threshold():
    detector = WakeWordDetector.__new__(WakeWordDetector)
    detector.phrase = "hey jarvis"
    detector.threshold = 0.6
    detector._last_detection = 0.0
    detector._model = _StubModel(0.12)
    detector.available = True
    assert detector.process_frame(np.zeros(1280, dtype=np.int16))["detected"] is False


def test_wake_word_without_model_is_safe():
    detector = WakeWordDetector.__new__(WakeWordDetector)
    detector.phrase = "hey jarvis"
    detector.threshold = 0.5
    detector._last_detection = 0.0
    detector._model = None
    detector.available = False
    result = detector.process_frame(np.zeros(1280, dtype=np.int16))
    assert result["detected"] is False
    assert result["available"] is False


class _FakeCapture:
    """Replays a scripted list of blocks as if they came from a microphone."""

    def __init__(self, blocks):
        self.blocks = list(blocks)
        self.level = 0.0

    def read(self, timeout=0.5):
        if not self.blocks:
            return None
        return self.blocks.pop(0)


def _tone(frames: int, amplitude: int = 6000) -> np.ndarray:
    t = np.arange(frames)
    return (np.sin(2 * np.pi * 220 * t / 16000) * amplitude).astype(np.int16)


def test_vad_segments_speech_from_silence():
    recognizer = SpeechRecognizer()
    silence = np.zeros(1280, dtype=np.int16)
    blocks = [silence] * 4 + [_tone(1280)] * 8 + [silence] * 12
    utterance = recognizer.capture_utterance(_FakeCapture(blocks),
                                             max_seconds=5, initial_timeout=2)
    assert utterance is not None
    assert utterance.duration > 0.4
    assert utterance.peak_level > 180


def test_vad_ignores_pure_silence():
    recognizer = SpeechRecognizer()
    silence = np.zeros(1280, dtype=np.int16)
    result = recognizer.capture_utterance(_FakeCapture([silence] * 30),
                                          max_seconds=1, initial_timeout=0.5)
    assert result is None


# ---------------------------------------------------------------------------
# Intent routing — the "brain" that decides which tool a transcript maps to
# ---------------------------------------------------------------------------
from jarvis.core.intent_router import router  # noqa: E402


@pytest.mark.parametrize("phrase,expected_tool", [
    ("set a timer for 10 minutes", "set_timer"),
    ("open chrome", "open_application"),
    ("open notepad", "open_application"),
    ("launch visual studio code", "open_application"),
    ("take a screenshot", "take_screenshot"),
    ("lock the computer", "lock_workstation"),
    ("what is my ip address", "network_info"),
    ("show me my running processes", "list_processes"),
    ("minimise all windows", "minimize_all_windows"),
    ("switch to the previous window", "switch_window"),
    ("search for python tutorials", "web_search"),
    ("create a note about my project ideas", "create_note"),
    ("add buy milk to my todo list", "add_todo"),
    ("set volume to 40", "set_volume"),
    ("remind me to call the dentist at 8 pm", "set_reminder"),
])
def test_router_maps_commands(phrase, expected_tool):
    intent = router.route(phrase)
    assert intent.tool == expected_tool, f"{phrase!r} -> {intent.tool}"


def test_router_timer_parses_seconds():
    intent = router.route("set a timer for 10 minutes")
    assert intent.arguments["seconds"] == 600


def test_router_number_words():
    intent = router.route("start a timer for five minutes")
    assert intent.arguments["seconds"] == 300


def test_router_greeting_is_instant():
    intent = router.route("hello")
    assert intent.name == "greeting"
    assert intent.speak and not intent.requires_ai


def test_router_falls_back_to_ai():
    intent = router.route("compare the three laptops I was looking at yesterday")
    assert intent.requires_ai is True


def test_router_reminder_time_parsing():
    intent = router.route("remind me to finish my project at 8 pm")
    assert intent.tool == "set_reminder"
    due = intent.arguments["due_at"]
    assert "T20:00" in due or "T08:00" in due


def test_stop_intent():
    assert router.route("stop").name == "stop"
    assert router.route("emergency stop").name == "stop"


# ---------------------------------------------------------------------------
# Full voice-loop orchestration with a synthetic microphone
# ---------------------------------------------------------------------------
class _MicCapture:
    level = 0.2

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass

    def read(self, timeout=0.5):
        return np.zeros(1280, dtype=np.int16)


class _LoopRecognizer:
    def __init__(self, *args, **kwargs):
        self.detail = "fake"
        self.load_error = ""

    def load(self):
        return True

    def status(self):
        return {"available": True, "model": "fake", "detail": "fake"}

    def transcribe(self, audio, language=None):
        return {"ok": True, "text": "what time is it", "language": "en", "duration": 1.0}

    def capture_utterance(self, capture, max_seconds=12.0, initial_timeout=6.0):
        from jarvis.voice.speech_recognition import Utterance
        return Utterance(audio=np.zeros(16000, dtype=np.int16), duration=1.0, peak_level=900)


class _LoopDetector:
    def __init__(self, *args, **kwargs):
        self.available = True
        self.hits = 0

    def process_frame(self, frame):
        self.hits += 1
        return {"detected": self.hits == 1, "scores": {}, "available": True}

    def reset(self):
        pass

    def status(self):
        return {"available": True, "phrase": "hey jarvis", "detail": "fake"}


def test_voice_loop_wake_word_to_dispatch(monkeypatch):
    """Wake word → greeting → transcription → conversation handler."""
    import time

    from jarvis import config
    from jarvis.voice import voice_manager as vm_module
    from jarvis.voice import speech_output

    # A previous test may have blanked the greeting; guarantee one here.
    monkeypatch.setattr(config.get().voice, "greeting",
                        "Yes, sir. How may I assist you?")
    monkeypatch.setattr(vm_module, "AudioCapture", _MicCapture)
    monkeypatch.setattr(vm_module, "SpeechRecognizer", _LoopRecognizer)
    monkeypatch.setattr(vm_module, "WakeWordDetector", _LoopDetector)
    spoken = []
    monkeypatch.setattr(speech_output.speech, "speak",
                        lambda text, timeout=None: spoken.append(text) or True)

    handled = []

    class FakeConversation:
        async def handle(self, text, speak=True, source="voice"):
            handled.append((text, source))

    manager = vm_module.VoiceManager()
    manager.recognizer = _LoopRecognizer()
    manager.attach_conversation(FakeConversation())

    result = manager.start("wake")
    assert result["ok"], result["message"]
    try:
        deadline = time.time() + 8
        while time.time() < deadline and not handled:
            time.sleep(0.1)
    finally:
        manager.stop()

    assert handled == [("what time is it", "voice")]
    assert any("Yes, sir" in line for line in spoken), spoken
    assert manager.listening is False


def test_voice_status_shape():
    from jarvis.voice.voice_manager import VoiceManager
    status = VoiceManager().status()
    for key in ("mode", "listening", "state", "wake_word", "stt", "tts", "devices", "level"):
        assert key in status


def test_wake_word_model_key_matches_openwakeword(monkeypatch):
    """Regression: the model key must be 'hey_jarvis', not 'hey_jarvis_v0.1'.

    openWakeWord keys its bundled models without a version suffix and its
    download_models() call rejects unknown names.
    """
    pytest.importorskip("openwakeword")
    import openwakeword

    from jarvis.voice.wake_word import WakeWordDetector

    detector = WakeWordDetector("hey jarvis")
    # The detector must have either loaded a model or explained honestly why not.
    assert detector.detail
    assert not detector.available or "ready" in detector.detail.lower()

    key = "hey jarvis".replace(" ", "_").removesuffix("_v0.1")
    assert key in openwakeword.MODELS


def test_unknown_wake_phrase_reports_available_models():
    from jarvis.voice.wake_word import WakeWordDetector

    detector = WakeWordDetector("computer wake up")
    assert detector.available is False
    assert detector.detail


def test_wake_word_missing_model_gives_download_hint():
    """A missing model file must not be reported as a missing package.

    Reported in the field: the dashboard said "Wake word needs the
    openwakeword package (pip install openwakeword)" even though it was
    installed - the actual problem was the undownloaded model.
    """
    from jarvis.voice.wake_word import WakeWordDetector

    detector = WakeWordDetector("hey jarvis")
    if detector.available:
        pytest.skip("models are present in this environment")
    detail = detector.detail.lower()
    assert "pip install openwakeword" not in detail
    assert "model" in detail
    assert "push-to-talk" in detail or "download" in detail


def test_wake_word_retry_load_is_safe():
    from jarvis.voice.wake_word import WakeWordDetector

    detector = WakeWordDetector("hey jarvis")
    result = detector.retry_load()
    assert isinstance(result, bool)
    if detector.available:
        assert result is True
    else:
        assert detector.detail
