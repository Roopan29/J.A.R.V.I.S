"""Screen capture, OCR and (optionally) vision-model understanding.

Privacy: nothing is captured unless ``security.allow_screen_capture`` is on,
and each capture is announced on the event bus so the dashboard indicator
lights up. Screenshots are written to JARVIS_HOME/cache/screenshots and are
never uploaded anywhere except an explicitly configured vision-capable model.
"""
from __future__ import annotations

import base64
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .. import config
from .platform_support import NotSupported, support

log = logging.getLogger(__name__)

SHOT_DIR = config.CACHE_DIR / "screenshots"
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def _check_permission() -> None:
    """Refuse unless screen capture is allowed.

    Asks the permission layer rather than the raw setting so an approved
    one-time grant ("take a screenshot" -> Allow?) is honoured without
    permanently switching the setting on.
    """
    from ..core.permissions import permissions as _permissions
    if not _permissions.check_capability("allow_screen_capture"):
        raise NotSupported(
            "Screen capture is disabled. Enable 'Allow screen capture' in "
            "Settings → Security, or approve the prompt when asked."
        )


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------
def capture(monitor: int = 0, save: bool = True) -> Dict[str, Any]:
    _check_permission()
    if not support.has_mss:
        raise NotSupported("Screen capture needs the 'mss' package (pip install mss).")
    import mss  # type: ignore
    from ..event_bus import bus

    bus.publish("state", {"state": "executing", "tool": "screen_capture"})
    with mss.mss() as sct:
        monitors = sct.monitors
        index = monitor if 0 <= monitor < len(monitors) else 1 if len(monitors) > 1 else 0
        shot = sct.grab(monitors[index])
        path = SHOT_DIR / f"screen_{time.strftime('%Y%m%d_%H%M%S')}.png"
        if save:
            mss.tools.to_png(shot.rgb, shot.size, output=str(path))
        from PIL import Image
        image = Image.frombytes("RGB", shot.size, shot.rgb)
    bus.publish("state", {"state": "idle"})
    return {
        "ok": True,
        "verified": True,
        "message": f"Captured the screen ({image.width}x{image.height}).",
        "data": {"path": str(path) if save else "", "width": image.width,
                 "height": image.height},
        "_image": image,
    }


def screenshot_to_file(path: Optional[str] = None) -> Dict[str, Any]:
    result = capture()
    image = result.pop("_image")
    if path:
        target = Path(path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        result["message"] = f"Screenshot saved to {target}."
        result["data"]["path"] = str(target)
    return result


# ---------------------------------------------------------------------------
# OCR
# ---------------------------------------------------------------------------
def ocr(path: Optional[str] = None, image=None) -> Dict[str, Any]:
    _check_permission()
    if not (support.has_pytesseract and support.has_tesseract_binary):
        raise NotSupported(
            "OCR needs the Tesseract engine. Install it from "
            "https://github.com/UB-Mannheim/tesseract/wiki and "
            "'pip install pytesseract'."
        )
    import pytesseract  # type: ignore
    from PIL import Image  # type: ignore

    if image is None:
        if not path:
            capture_result = capture()
            image = capture_result["_image"]
        else:
            image = Image.open(path)
    text = pytesseract.image_to_string(image)
    cleaned = "\n".join(line for line in text.splitlines() if line.strip())
    snippet = cleaned[:1200]
    return {
        "ok": True, "verified": True,
        "message": f"Read {len(cleaned)} characters from the screen. " + (snippet[:300] if snippet else ""),
        "data": {"text": snippet, "characters": len(cleaned)},
    }


def identify_windows() -> Dict[str, Any]:
    """Report the visible application windows (no pixels leave the machine)."""
    _check_permission()
    from .window_manager import list_windows
    windows = [w for w in list_windows() if w["title"]][:20]
    names = ", ".join(w["title"] for w in windows[:8])
    return {"ok": True, "verified": True,
            "message": f"{len(windows)} visible window(s): {names}",
            "data": {"windows": windows}}


# ---------------------------------------------------------------------------
# Vision model analysis
# ---------------------------------------------------------------------------
def analyse_screen(question: str = "Describe what is on the screen and highlight any "
                                   "errors or dialogs.") -> Dict[str, Any]:
    """Send the screenshot to a vision-capable model if one is configured.

    Supported: OpenAI-compatible ``gpt-4o``-class models and Ollama vision
    models (llava, qwen2.5-vl, …). Without one, falls back to OCR text so the
    answer is still grounded in real pixels rather than invention.
    """
    _check_permission()
    result = capture()
    image = result["_image"]
    settings = config.get()
    provider = (settings.ai.provider or "").lower()

    try:
        answer, model_used = _ask_vision_model(image, question, provider)
    except NotSupported:
        raise
    except Exception as exc:
        log.warning("Vision model call failed: %s", exc)
        answer, model_used = None, ""

    if answer:
        return {"ok": True, "verified": True, "message": answer,
                "data": {"provider": provider, "model": model_used,
                         "screenshot": result["data"].get("path")}}

    # Grounded fallback: OCR + window list
    ocr_text = ""
    if support.has_pytesseract and support.has_tesseract_binary:
        try:
            import pytesseract  # type: ignore
            ocr_text = pytesseract.image_to_string(image)[:1500]
        except Exception:
            ocr_text = ""
    windows = []
    if support.is_windows:
        try:
            from .window_manager import list_windows
            windows = [w["title"] for w in list_windows()][:8]
        except Exception:
            pass
    message = ("No vision-capable model is configured, so here is the recognised "
               "text instead:\n" + (ocr_text.strip()[:800] if ocr_text.strip()
                                    else "(no readable text detected on screen)"))
    return {"ok": True, "verified": False, "message": message,
            "data": {"windows": windows, "ocr": ocr_text,
                     "screenshot": result["data"].get("path"),
                     "note": "Configure a vision model for semantic understanding."}}


def _ask_vision_model(image, question: str, provider: str):
    import io

    import httpx

    settings = config.get()
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")

    if provider in {"openai", "groq", "openrouter", "together", "custom", "lmstudio"}:
        key = config.api_key_for("openai")
        if not key:
            raise NotSupported("Vision analysis needs an OPENAI_API_KEY for the cloud provider.")
        payload = {
            "model": settings.ai.model,
            "max_tokens": 600,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": question},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{encoded}"}},
                ],
            }],
        }
        resp = httpx.post(f"{config.openai_base_url()}/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json=payload, timeout=90)
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"], payload["model"]

    if provider == "ollama":
        payload = {
            "model": settings.ai.model,
            "prompt": question,
            "images": [encoded],
            "stream": False,
        }
        resp = httpx.post(f"{config.ollama_host()}/api/generate", json=payload, timeout=120)
        if resp.status_code == 400:
            raise NotSupported(
                f"Model '{settings.ai.model}' does not accept images. "
                "Use a vision model such as 'ollama pull llava' and select it in Settings.")
        resp.raise_for_status()
        return resp.json().get("response", ""), payload["model"]

    raise NotSupported("The configured provider does not support vision.")
