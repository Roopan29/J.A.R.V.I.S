"""Wake-word detection with openWakeWord ("Hey JARVIS").

Design
------
* openWakeWord ships a pretrained ``hey_jarvis`` model — exactly what we need.
* The detector runs on the same 16 kHz mono int16 stream as the recogniser.
* If openWakeWord is not installed, :class:`WakeWordDetector.available` stays
  False and the voice manager falls back to push-to-talk / always-listen,
  telling the user why rather than failing silently.

Pure detection logic is separated from the audio loop (``process_frame``) so it
can be unit tested without a microphone.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
FRAME_SAMPLES = 1280        # 80 ms — openWakeWord's native chunk size
DEFAULT_THRESHOLD = 0.55
COOLDOWN_SECONDS = 2.0      # ignore repeat detections right after one fires


class WakeWordDetector:
    def __init__(self, phrase: str = "hey jarvis", threshold: float = DEFAULT_THRESHOLD) -> None:
        self.phrase = (phrase or "hey jarvis").lower()
        self.threshold = threshold
        self._model = None
        self._last_detection = 0.0
        self.available = False
        self.detail = ""
        self._load()

    # -- model loading -----------------------------------------------------
    def _load(self) -> None:
        try:
            import openwakeword  # type: ignore
            from openwakeword.model import Model  # type: ignore
        except ImportError:
            self.detail = ("Wake word needs the 'openwakeword' package "
                           "(pip install openwakeword). Push-to-talk still works.")
            log.info(self.detail)
            return

        # openWakeWord keys its bundled models without a version suffix
        # ("hey_jarvis", "alexa", ...); download_models() needs the same key.
        model_key = self.phrase.replace(" ", "_").removesuffix("_v0.1")
        if "jarvis" not in self.phrase:
            self.detail = (f"openWakeWord has no pretrained model for '{self.phrase}'. "
                           "The bundled models cover 'hey jarvis', 'alexa', 'hey mycroft', "
                           "'hey rhasspy' and 'timer'.")
            log.warning(self.detail)
        try:
            # Ensure the pretrained models are downloaded once.
            known = set(getattr(openwakeword, "MODELS", {}).keys())
            if known and model_key not in known:
                self.detail = (f"openWakeWord has no bundled model '{model_key}'. "
                               f"Available: {', '.join(sorted(known))}.")
                log.warning(self.detail)
                return
            try:
                openwakeword.utils.download_models(model_names=[model_key])
            except Exception as exc:  # first-run download needs internet
                log.info("Wake-word model download skipped (%s); will retry on use.", exc)
            self._model = Model(wakeword_models=[model_key], inference_framework="onnx")
            self.available = True
            self.detail = f"Wake word '{self.phrase}' ready."
            log.info(self.detail)
        except Exception as exc:
            self.detail = f"Could not initialise openWakeWord: {exc}"
            log.warning(self.detail)
            self._model = None

    # -- detection ---------------------------------------------------------
    def process_frame(self, frame: np.ndarray) -> Dict[str, Any]:
        """Feed one int16 frame; returns scores and whether it triggered."""
        if self._model is None:
            return {"detected": False, "scores": {}, "available": False}
        audio = frame.astype(np.int16, copy=False)
        try:
            scores = self._model.predict(audio)
        except Exception as exc:  # malformed frame
            log.debug("Wake-word predict failed: %s", exc)
            return {"detected": False, "scores": {}, "available": True}

        best_name, best_score = "", 0.0
        for name, score in scores.items():
            if float(score) > best_score:
                best_name, best_score = name, float(score)

        now = time.time()
        detected = best_score >= self.threshold and now - self._last_detection > COOLDOWN_SECONDS
        if detected:
            self._last_detection = now
            log.info("Wake word detected (%s, score %.2f)", best_name, best_score)
        return {"detected": detected, "scores": {k: round(float(v), 3) for k, v in scores.items()},
                "best": best_name, "score": round(best_score, 3), "available": True}

    def reset(self) -> None:
        if self._model is not None and hasattr(self._model, "reset"):
            try:
                self._model.reset()
            except Exception:
                pass

    def status(self) -> Dict[str, Any]:
        return {"available": self.available, "phrase": self.phrase,
                "threshold": self.threshold, "detail": self.detail}
