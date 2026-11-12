"""Optional native desktop window wrapper.

The dashboard is a local web app served by FastAPI. `DesktopWindow` wraps it in
a native Windows window using pywebview (Edge WebView2), so JARVIS can feel like
a real desktop application instead of a browser tab.

If pywebview/WebView2 is not available, callers fall back to the browser.
"""
from .window import DesktopWindow, webview_available  # noqa: F401

__all__ = ["DesktopWindow", "webview_available"]
