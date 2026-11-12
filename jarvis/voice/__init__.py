"""Voice I/O: wake word, speech-to-text, text-to-speech, orchestration."""
from .voice_manager import VoiceManager, manager  # noqa: F401

__all__ = ["VoiceManager", "manager"]
