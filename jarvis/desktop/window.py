"""Native window host for the JARVIS dashboard (pywebview + WebView2)."""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)


def webview_available() -> bool:
    """True when the pywebview package can actually be imported."""
    try:
        import webview  # noqa: F401  # type: ignore
        return True
    except Exception:  # ImportError, or a broken native dependency
        return False


class DesktopWindow:
    """Run the dashboard inside a frameless-ish native window.

    The FastAPI server is started on a background thread; this class only owns
    the window lifecycle. Closing the window stops the whole application.
    """

    def __init__(self, url: str, title: str = "J.A.R.V.I.S.",
                 width: int = 1440, height: int = 900,
                 fullscreen: bool = False, on_close=None) -> None:
        self.url = url
        self.title = title
        self.width = width
        self.height = height
        self.fullscreen = fullscreen
        self._on_close = on_close
        self._window = None

    def start(self) -> bool:
        """Open the window and block until it closes. Returns True if shown."""
        if not webview_available():
            log.info("pywebview not installed; dashboard opens in the default browser.")
            return False
        import webview  # type: ignore

        self._window = webview.create_window(
            self.title,
            self.url,
            width=self.width,
            height=self.height,
            fullscreen=self.fullscreen,
            background_color="#02060c",
            text_select=True,
            confirm_close=False,
        )
        if self._on_close:
            self._window.events.closed += self._on_close
        try:
            webview.start(gui="edgechromium" if _has_edge() else None, debug=False)
        except Exception as exc:  # pragma: no cover - GUI environment specific
            log.warning("Native window failed (%s); use the browser dashboard.", exc)
            return False
        return True

    def destroy(self) -> None:
        if self._window is not None:
            try:
                self._window.destroy()
            except Exception:
                pass


def _has_edge() -> bool:
    import os
    candidates = [
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\EdgeWebView\Application"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\EdgeWebView\Application"),
    ]
    return any(os.path.isdir(path) for path in candidates)


def wait_for_server(url: str, timeout: float = 25.0) -> bool:
    """Poll the health endpoint until the server answers."""
    import urllib.error
    import urllib.request

    health = url.rstrip("/") + "/api/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(health, timeout=1.5) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False
