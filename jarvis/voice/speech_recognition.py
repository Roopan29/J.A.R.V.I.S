"""Speech-to-text with faster-whisper plus energy/VAD-based phrase capture.

Two layers:
  * :class:`AudioCapture`   — sounddevice input stream at 16 kHz mono int16,
    with device selection, RMS level metering for the visualiser.
  * :class:`SpeechRecognizer` — faster-whisper model wrapper (lazy loaded)
    with a simple adaptive energy VAD to delimit utterances.

Interval-based silence detection avoids a hard dependency on webrtcvad, while
still using it when available for better noise robustness.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np

log = logging.getLogger(__name__)

SAMPLE_RATE = 16000
BLOCK = 1280          # 80 ms


# ---------------------------------------------------------------------------
# Device discovery
# ---------------------------------------------------------------------------
def list_input_devices() -> List[Dict[str, Any]]:
    try:
        import sounddevice as sd  # type: ignore
    except (ImportError, OSError) as exc:
        log.info("sounddevice unavailable: %s", exc)
        return []
    try:
        devices = sd.query_devices()
    except Exception as exc:  # PortAudio errors
        log.warning("Could not query audio devices: %s", exc)
        return []
    result = []
    for index, device in enumerate(devices):
        if device.get("max_input_channels", 0) > 0:
            result.append({
                "index": index,
                "name": device.get("name", f"Device {index}"),
                "channels": device.get("max_input_channels"),
                "default_samplerate": int(device.get("default_samplerate", 0)),
            })
    return result


def list_output_devices() -> List[Dict[str, Any]]:
    try:
        import sounddevice as sd  # type: ignore
        devices = sd.query_devices()
    except Exception:
        return []
    return [{"index": i, "name": d.get("name", f"Device {i}")}
            for i, d in enumerate(devices) if d.get("max_output_channels", 0) > 0]


def default_input_device() -> Optional[int]:
    try:
        import sounddevice as sd  # type: ignore
        return int(sd.default.device[0])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
class AudioCapture:
    """Threaded microphone capture with a callback per block."""

    def __init__(self, device: Optional[int] = None, samplerate: int = SAMPLE_RATE) -> None:
        self.device = device
        self.samplerate = samplerate
        self._stream = None
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=200)
        self._level = 0.0
        self._running = False
        self.error: str = ""

    @property
    def level(self) -> float:
        return self._level

    def start(self) -> None:
        try:
            import sounddevice as sd  # type: ignore
        except (ImportError, OSError) as exc:
            self.error = f"sounddevice is unavailable: {exc}"
            raise RuntimeError(self.error) from exc

        def callback(indata, frames, time_info, status):  # noqa: ANN001
            if status:
                log.debug("Audio status: %s", status)
            block = indata.copy().flatten()
            self._level = float(np.sqrt(np.mean(block.astype(np.float32) ** 2)) / 32768.0)
            try:
                self._queue.put_nowait(block)
            except queue.Full:
                try:
                    self._queue.get_nowait()
                    self._queue.put_nowait(block)
                except queue.Empty:
                    pass

        try:
            self._stream = sd.InputStream(
                samplerate=self.samplerate, channels=1, dtype="int16",
                blocksize=BLOCK, device=self.device, callback=callback,
            )
            self._stream.start()
            self._running = True
            self.error = ""
        except Exception as exc:
            self.error = f"Could not open the microphone: {exc}"
            raise RuntimeError(self.error) from exc

    def read(self, timeout: float = 0.5) -> Optional[np.ndarray]:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        self._running = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    @property
    def running(self) -> bool:
        return self._running


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------
@dataclass
class Utterance:
    audio: np.ndarray
    duration: float
    peak_level: float


class SpeechRecognizer:
    def __init__(self, model_size: str = "base", compute_type: str = "int8",
                 language: str = "en", silence_ms: int = 900) -> None:
        self.model_size = model_size
        self.compute_type = compute_type
        self.language = language
        self.silence_ms = silence_ms
        self._model = None
        self.available = False
        self.detail = ""
        self.load_error = ""
        self._thread_lock = threading.Lock()

    # -- lazy model load ---------------------------------------------------
    def load(self) -> bool:
        with self._thread_lock:
            if self._model is not None:
                return True
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except ImportError:
                self.detail = ("Speech recognition needs faster-whisper "
                               "(pip install faster-whisper).")
                self.load_error = self.detail
                log.info(self.detail)
                return False
            try:
                log.info("Loading faster-whisper model '%s'…", self.model_size)
                self._model = WhisperModel(self.model_size, device="cpu",
                                           compute_type=self.compute_type)
                self.available = True
                self.detail = f"Whisper '{self.model_size}' loaded."
                log.info(self.detail)
                return True
            except Exception as exc:
                self.detail = f"Could not load Whisper model: {exc}"
                self.load_error = self.detail
                log.warning(self.detail)
                return False

    # -- VAD-ish utterance segmentation ------------------------------------
    @staticmethod
    def _rms(block: np.ndarray) -> float:
        if block.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(block.astype(np.float32) ** 2)))

    def capture_utterance(self, capture: AudioCapture,
                          max_seconds: float = 12.0,
                          initial_timeout: float = 6.0) -> Optional[Utterance]:
        """Block until speech starts, then until silence; return the audio.

        Adaptive threshold: 2.5x the running noise floor, with a floor of 180
        RMS to ignore background hum.
        """
        blocks: List[np.ndarray] = []
        noise_floor = 250.0
        pre_roll: List[np.ndarray] = []
        started = False
        start_time = time.time()
        silence_blocks_needed = max(3, int(self.silence_ms / 80))
        silent_run = 0
        peak = 0.0

        while True:
            if time.time() - start_time > max_seconds + initial_timeout:
                break
            block = capture.read(timeout=0.4)
            if block is None:
                continue
            energy = self._rms(block)
            threshold = max(180.0, noise_floor * 2.5)

            if not started:
                noise_floor = 0.95 * noise_floor + 0.05 * energy
                pre_roll.append(block)
                pre_roll = pre_roll[-6:]           # 0.5 s of context
                if energy > threshold:
                    started = True
                    blocks.extend(pre_roll)
                    start_time = time.time()
                elif time.time() - start_time > initial_timeout:
                    return None
                continue

            blocks.append(block)
            peak = max(peak, energy)
            silent_run = silent_run + 1 if energy < threshold else 0
            if silent_run >= silence_blocks_needed:
                break
            if time.time() - start_time > max_seconds:
                break

        if not blocks:
            return None
        audio = np.concatenate(blocks)
        duration = len(audio) / SAMPLE_RATE
        if duration < 0.35:      # cough, click, noise
            return None
        return Utterance(audio=audio, duration=duration, peak_level=peak)

    # -- transcription ------------------------------------------------------
    def transcribe(self, audio: np.ndarray, language: Optional[str] = None) -> Dict[str, Any]:
        if not self.load():
            return {"ok": False, "text": "", "error": self.detail}
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32) / 32768.0
        try:
            segments, info = self._model.transcribe(
                audio,
                language=language or self.language,
                beam_size=1,
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 400},
                condition_on_previous_text=False,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            log.exception("Transcription failed")
            return {"ok": False, "text": "", "error": str(exc)}
        return {"ok": True, "text": text, "language": getattr(info, "language", language or "en"),
                "duration": round(getattr(info, "duration", 0.0), 2)}

    def status(self) -> Dict[str, Any]:
        return {"available": self.available, "model": self.model_size,
                "detail": self.detail or ("not loaded" if not self.available else "ready")}
