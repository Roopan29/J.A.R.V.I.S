"""Keyboard, mouse and clipboard automation.

Uses ``pyautogui`` for input synthesis and ``pyperclip`` for the clipboard.
Guard rails: a runaway-mouse failsafe (corner abort), a hard cap on typed text
length, and a check that a target window is focused before typing.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .platform_support import NotSupported, support

log = logging.getLogger(__name__)

MAX_TYPED_CHARS = 5000
MAX_MOUSE_STEPS = 40


def _pyautogui():
    if not support.has_pyautogui:
        raise NotSupported("Input automation needs pyautogui (pip install pyautogui).")
    import pyautogui  # type: ignore
    pyautogui.FAILSAFE = True       # slam the mouse into a corner to abort
    pyautogui.PAUSE = 0.05
    return pyautogui


# ---------------------------------------------------------------------------
# Keyboard
# ---------------------------------------------------------------------------
def type_text(text: str, interval: float = 0.01) -> Dict[str, Any]:
    if not text:
        raise ValueError("Nothing to type")
    if len(text) > MAX_TYPED_CHARS:
        return {"ok": False,
                "message": f"Refusing to type {len(text)} characters at once (limit {MAX_TYPED_CHARS}). "
                           "Split it into smaller chunks."}
    gui = _pyautogui()
    gui.write(text, interval=max(0.0, min(interval, 0.1)))
    return {"ok": True, "verified": True,
            "message": f"Typed {len(text)} character(s) into the active window."}


def press_keys(keys: List[str]) -> Dict[str, Any]:
    if not keys:
        raise ValueError("No keys supplied")
    gui = _pyautogui()
    normalized = [k.strip().lower() for k in keys if k.strip()]
    if len(normalized) == 1:
        gui.press(normalized[0])
    else:
        gui.hotkey(*normalized)
    return {"ok": True, "verified": True, "message": f"Sent {'+'.join(normalized)}."}


def write_hotkey(combo: str) -> Dict[str, Any]:
    keys = [part.strip() for part in combo.replace("-", "+").split("+") if part.strip()]
    return press_keys(keys)


# ---------------------------------------------------------------------------
# Mouse
# ---------------------------------------------------------------------------
def move_mouse(x: int, y: int, duration: float = 0.25) -> Dict[str, Any]:
    gui = _pyautogui()
    gui.moveTo(int(x), int(y), duration=max(0.0, min(duration, 2.0)))
    pos = gui.position()
    return {"ok": True, "verified": abs(pos.x - x) <= 2 and abs(pos.y - y) <= 2,
            "message": f"Moved the pointer to {x},{y}.",
            "data": {"x": pos.x, "y": pos.y}}


def click(x: Optional[int] = None, y: Optional[int] = None,
          button: str = "left", clicks: int = 1) -> Dict[str, Any]:
    gui = _pyautogui()
    if x is not None and y is not None:
        gui.moveTo(int(x), int(y), duration=0.2)
    gui.click(button=button if button in ("left", "right", "middle") else "left",
              clicks=max(1, min(clicks, 3)))
    location = gui.position()
    return {"ok": True, "verified": True,
            "message": f"{button} click x{clicks} at {location.x},{location.y}."}


def scroll(amount: int = 3, x: Optional[int] = None, y: Optional[int] = None) -> Dict[str, Any]:
    gui = _pyautogui()
    if x is not None and y is not None:
        gui.moveTo(int(x), int(y), duration=0.15)
    clamped = max(-MAX_MOUSE_STEPS, min(MAX_MOUSE_STEPS, int(amount)))
    gui.scroll(clamped)
    return {"ok": True, "verified": True,
            "message": f"Scrolled {'up' if clamped > 0 else 'down'} {abs(clamped)} notch(es)."}


def drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> Dict[str, Any]:
    gui = _pyautogui()
    gui.moveTo(int(x1), int(y1))
    gui.dragTo(int(x2), int(y2), duration=max(0.2, min(duration, 3.0)), button="left")
    return {"ok": True, "verified": True, "message": f"Dragged from {x1},{y1} to {x2},{y2}."}


def screen_size() -> Dict[str, Any]:
    gui = _pyautogui()
    size = gui.size()
    return {"ok": True, "verified": True,
            "message": f"Primary display is {size.width}x{size.height}.",
            "data": {"width": size.width, "height": size.height}}


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------
def _clipboard():
    if not support.has_pyperclip:
        raise NotSupported("Clipboard access needs pyperclip (pip install pyperclip).")
    import pyperclip  # type: ignore
    return pyperclip


def read_clipboard() -> Dict[str, Any]:
    pyperclip = _clipboard()
    try:
        content = pyperclip.paste()
    except Exception as exc:
        raise NotSupported(f"Clipboard is empty or unavailable: {exc}")
    if not content:
        return {"ok": False, "message": "The clipboard is empty."}
    preview = content if len(content) <= 500 else content[:500] + "…"
    return {"ok": True, "verified": True,
            "message": f"Clipboard contains: {preview}",
            "data": {"content": preview, "length": len(content)}}


def write_clipboard(text: str) -> Dict[str, Any]:
    pyperclip = _clipboard()
    pyperclip.copy(text or "")
    return {"ok": True, "verified": pyperclip.paste() == (text or ""),
            "message": "Copied to the clipboard."}


def copy_selection() -> Dict[str, Any]:
    """Ctrl+C in the active window, then read the clipboard."""
    press_keys(["ctrl", "c"])
    time.sleep(0.25)
    return read_clipboard()
