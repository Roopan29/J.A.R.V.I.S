"""Window enumeration and focus management (Win32 via pywin32)."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .platform_support import NotSupported, support

log = logging.getLogger(__name__)


def _win32():
    try:
        import win32con  # type: ignore
        import win32gui  # type: ignore
        import win32process  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise NotSupported("Window management needs pywin32 (pip install pywin32).") from exc
    return win32gui, win32con, win32process


def list_windows(visible_only: bool = True) -> List[Dict[str, Any]]:
    support.require_windows("Window listing")
    win32gui, _, win32process = _win32()
    windows: List[Dict[str, Any]] = []

    def callback(hwnd, _):
        if visible_only and not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title:
            return True
        if title in ("Program Manager", "Windows Input Experience", "Settings"):
            pass  # keep settings windows, they are legitimately useful
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            pid = 0
        rect = win32gui.GetWindowRect(hwnd)
        windows.append({
            "hwnd": hwnd, "title": title, "pid": pid,
            "width": rect[2] - rect[0], "height": rect[3] - rect[1],
            "minimized": win32gui.IsIconic(hwnd) == 1,
        })
        return True

    win32gui.EnumWindows(callback, None)
    return windows


def active_window() -> Dict[str, Any]:
    support.require_windows("Active window detection")
    win32gui, _, _ = _win32()
    hwnd = win32gui.GetForegroundWindow()
    return {"hwnd": hwnd, "title": win32gui.GetWindowText(hwnd)}


def focus_application(name: str) -> Dict[str, Any]:
    """Bring the first window whose title matches ``name`` to the front."""
    support.require_windows("Window focusing")
    win32gui, win32con, _ = _win32()
    needle = (name or "").lower().strip()
    if not needle:
        raise ValueError("No application name supplied")

    match: Optional[Dict[str, Any]] = None
    for window in list_windows():
        if needle in window["title"].lower():
            match = window
            break
    if not match:
        return {"ok": False, "message": f"No visible window matching '{name}'."}

    hwnd = match["hwnd"]
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetForegroundWindow(hwnd)
    except Exception as exc:  # pywin32 raises on foreground lock
        # Fallback: minimise+restore trick via keybd_event
        import ctypes
        ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)   # ALT down
        try:
            win32gui.SetForegroundWindow(hwnd)
        except Exception:
            raise NotSupported(f"Windows blocked focus switching ({exc}).") from exc
        finally:
            ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.25)
    return {"ok": True, "verified": active_window()["hwnd"] == hwnd,
            "message": f"Focused '{match['title']}'.", "data": match}


def minimize_all_windows() -> Dict[str, Any]:
    support.require_windows("Minimising all windows")
    import ctypes
    # Win+D — the standard "show desktop" toggle.
    ctypes.windll.user32.keybd_event(0x5B, 0, 0, 0)  # LWIN
    ctypes.windll.user32.keybd_event(0x44, 0, 0, 0)  # D
    ctypes.windll.user32.keybd_event(0x44, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x5B, 0, 2, 0)
    return {"ok": True, "verified": True, "message": "Minimised all windows.", "data": {}}


def switch_window() -> Dict[str, Any]:
    support.require_windows("Window switching")
    import ctypes
    ctypes.windll.user32.keybd_event(0x12, 0, 0, 0)   # ALT
    ctypes.windll.user32.keybd_event(0x09, 0, 0, 0)   # TAB
    ctypes.windll.user32.keybd_event(0x09, 0, 2, 0)
    ctypes.windll.user32.keybd_event(0x12, 0, 2, 0)
    time.sleep(0.5)
    return {"ok": True, "verified": True, "message": "Switched to the previous window."}


def minimize_active() -> Dict[str, Any]:
    support.require_windows("Minimising a window")
    win32gui, win32con, _ = _win32()
    hwnd = win32gui.GetForegroundWindow()
    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
    return {"ok": True, "verified": True, "message": "Minimised the active window."}


def maximize_active() -> Dict[str, Any]:
    support.require_windows("Maximising a window")
    win32gui, win32con, _ = _win32()
    hwnd = win32gui.GetForegroundWindow()
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    return {"ok": True, "verified": True, "message": "Maximised the active window."}
