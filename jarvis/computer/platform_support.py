"""Platform capability detection.

JARVIS targets Windows 10/11. Some modules (window management, volume, power)
depend on Win32 APIs; when running on another OS we must *say so* rather than
silently pretending. Each computer module asks ``support`` before acting.
"""
from __future__ import annotations

import importlib.util
import logging
import platform
import shutil
from dataclasses import dataclass
from typing import Dict

log = logging.getLogger(__name__)


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


class NotSupported(RuntimeError):
    """Raised when a capability is unavailable on this machine/OS."""


@dataclass
class PlatformSupport:
    system: str
    is_windows: bool
    is_linux: bool
    is_macos: bool
    has_pywin32: bool
    has_pyautogui: bool
    has_pyperclip: bool
    has_mss: bool
    has_pytesseract: bool
    has_tesseract_binary: bool
    powershell: str
    has_nvidia_smi: bool

    def require_windows(self, feature: str) -> None:
        if not self.is_windows:
            raise NotSupported(
                f"{feature} requires Windows. JARVIS is running on {self.system}; "
                "this feature is unavailable here."
            )

    def require(self, available: bool, feature: str, package: str) -> None:
        if not available:
            raise NotSupported(
                f"{feature} needs the '{package}' package. "
                f"Install it with: pip install {package}"
            )

    def summary(self) -> Dict[str, object]:
        return {
            "system": self.system,
            "windows": self.is_windows,
            "pywin32": self.has_pywin32,
            "pyautogui": self.has_pyautogui,
            "clipboard": self.has_pyperclip,
            "screen_capture": self.has_mss,
            "ocr": self.has_pytesseract and self.has_tesseract_binary,
            "powershell": self.powershell or None,
            "nvidia_gpu": self.has_nvidia_smi,
        }


def detect() -> PlatformSupport:
    system = platform.system()
    powershell = shutil.which("powershell") or shutil.which("pwsh") or ""
    return PlatformSupport(
        system=system,
        is_windows=system == "Windows",
        is_linux=system == "Linux",
        is_macos=system == "Darwin",
        has_pywin32=_has("win32api") or _has("win32gui"),
        has_pyautogui=_has("pyautogui"),
        has_pyperclip=_has("pyperclip"),
        has_mss=_has("mss"),
        has_pytesseract=_has("pytesseract"),
        has_tesseract_binary=bool(shutil.which("tesseract")),
        powershell=powershell,
        has_nvidia_smi=bool(shutil.which("nvidia-smi")),
    )


support = detect()
