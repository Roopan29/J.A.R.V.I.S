"""Application and window control tools."""
from __future__ import annotations

from ...computer import app_control, window_manager
from ...computer.platform_support import NotSupported
from ...database.models import ActionResult
from ..permissions import Capability
from ..tool_registry import ToolSpec, registry


def _wrap(fn, *args, **kwargs) -> ActionResult:
    try:
        result = fn(*args, **kwargs)
    except NotSupported as exc:
        return ActionResult(ok=False, message=str(exc))
    if isinstance(result, dict):
        return ActionResult(ok=bool(result.get("ok", True)),
                            message=result.get("message", ""),
                            data=result.get("data", {}),
                            verified=bool(result.get("verified", False)))
    return ActionResult(ok=True, message=str(result))


registry.register(ToolSpec(
    name="open_application",
    description="Launch an application on the computer, e.g. Chrome, Notepad, VS Code, "
                "Spotify. Verifies the process started.",
    parameters={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "Application name or executable, "
                                                      "e.g. 'chrome', 'notepad', 'code'"},
            "arguments": {"type": "string", "description": "Optional command-line arguments"},
        },
        "required": ["name"],
    },
    handler=lambda name, arguments=None: _wrap(app_control.open_application, name, arguments),
    category="applications",
    capability=Capability.APP_LAUNCH,
))

registry.register(ToolSpec(
    name="open_path",
    description="Open a file, folder or document with its default Windows application.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string",
                                        "description": "Absolute path or known folder, "
                                                       "e.g. 'Downloads' or 'C:/Users/me/Desktop/notes.txt'"}},
                "required": ["path"]},
    handler=lambda path: _wrap(app_control.open_path, path),
    category="applications",
    capability=Capability.FILE_WRITE,
))

registry.register(ToolSpec(
    name="open_url",
    description="Open a URL in the default browser.",
    parameters={"type": "object",
                "properties": {"url": {"type": "string", "description": "Web address"}},
                "required": ["url"]},
    handler=lambda url: _wrap(app_control.open_url, url),
    category="applications",
    capability=Capability.NETWORK,
))

registry.register(ToolSpec(
    name="close_application",
    description="Close a running application by name. Verifies it has exited.",
    parameters={"type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Application to close"},
                    "force": {"type": "boolean", "description": "Force-kill if it does not exit"},
                },
                "required": ["name"]},
    handler=lambda name, force=False: _wrap(app_control.close_application, name, force),
    category="applications",
    capability=Capability.APP_LAUNCH,
    risk="medium",
    requires_confirmation=False,
))

registry.register(ToolSpec(
    name="close_active_window",
    description="Close the currently focused window.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(app_control.close_active_window),
    category="applications",
    capability=Capability.APP_LAUNCH,
    risk="medium",
))

registry.register(ToolSpec(
    name="focus_application",
    description="Bring an open application's window to the foreground.",
    parameters={"type": "object",
                "properties": {"name": {"type": "string",
                                        "description": "Part of the window title, e.g. 'Visual Studio Code'"}},
                "required": ["name"]},
    handler=lambda name: _wrap(window_manager.focus_application, name),
    category="windows",
))

registry.register(ToolSpec(
    name="switch_window",
    description="Switch to the previous window (Alt+Tab).",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(window_manager.switch_window),
    category="windows",
))

registry.register(ToolSpec(
    name="minimize_all_windows",
    description="Minimise every window and show the desktop.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(window_manager.minimize_all_windows),
    category="windows",
))

registry.register(ToolSpec(
    name="list_windows",
    description="List the visible windows on screen with their titles.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(lambda: {"ok": True, "message": "Windows listed.",
                                   "data": {"windows": window_manager.list_windows()},
                                   "verified": True}),
    category="windows",
))

registry.register(ToolSpec(
    name="list_installed_apps",
    description="List applications available in the Windows Start Menu.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(lambda: {"ok": True, "verified": True,
                                   "message": "Start Menu applications listed.",
                                   "data": {"apps": app_control.installed_apps()}}),
    category="applications",
))
