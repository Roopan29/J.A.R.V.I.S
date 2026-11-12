"""Keyboard, mouse and clipboard tools."""
from __future__ import annotations

from ...computer import mouse_keyboard
from ...computer.platform_support import NotSupported
from ...database.models import ActionResult
from ..permissions import Capability
from ..tool_registry import ToolSpec, registry


def _wrap(fn, *args, **kwargs) -> ActionResult:
    try:
        result = fn(*args, **kwargs)
    except NotSupported as exc:
        return ActionResult(ok=False, message=str(exc))
    except (OSError, ValueError) as exc:
        return ActionResult(ok=False, message=f"{type(exc).__name__}: {exc}")
    if isinstance(result, dict):
        return ActionResult(ok=bool(result.get("ok", True)),
                            message=result.get("message", ""),
                            data=result.get("data", {}),
                            verified=bool(result.get("verified", False)))
    return ActionResult(ok=True, message=str(result))


registry.register(ToolSpec(
    name="type_text",
    description="Type text into the currently focused window.",
    parameters={"type": "object",
                "properties": {"text": {"type": "string"},
                               "interval": {"type": "number", "default": 0.01}},
                "required": ["text"]},
    handler=lambda text, interval=0.01: _wrap(mouse_keyboard.type_text, text, interval),
    category="input", capability=Capability.INPUT_CONTROL, risk="medium",
))

registry.register(ToolSpec(
    name="press_keys",
    description="Press a key combination, e.g. ['ctrl','s'] or ['enter'].",
    parameters={"type": "object",
                "properties": {"keys": {"type": "array", "items": {"type": "string"}}},
                "required": ["keys"]},
    handler=lambda keys: _wrap(mouse_keyboard.press_keys, keys),
    category="input", capability=Capability.INPUT_CONTROL,
))

registry.register(ToolSpec(
    name="move_mouse",
    description="Move the mouse pointer to absolute screen coordinates.",
    parameters={"type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                               "duration": {"type": "number", "default": 0.25}},
                "required": ["x", "y"]},
    handler=lambda x, y, duration=0.25: _wrap(mouse_keyboard.move_mouse, x, y, duration),
    category="input", capability=Capability.INPUT_CONTROL,
))

registry.register(ToolSpec(
    name="click",
    description="Click the mouse, optionally at coordinates.",
    parameters={"type": "object",
                "properties": {"x": {"type": "integer"}, "y": {"type": "integer"},
                               "button": {"type": "string", "enum": ["left", "right", "middle"]},
                               "clicks": {"type": "integer", "default": 1}}},
    handler=lambda x=None, y=None, button="left", clicks=1:
        _wrap(mouse_keyboard.click, x, y, button, clicks),
    category="input", capability=Capability.INPUT_CONTROL, risk="medium",
))

registry.register(ToolSpec(
    name="scroll",
    description="Scroll the mouse wheel.",
    parameters={"type": "object",
                "properties": {"amount": {"type": "integer", "default": 3},
                               "x": {"type": "integer"}, "y": {"type": "integer"}}},
    handler=lambda amount=3, x=None, y=None: _wrap(mouse_keyboard.scroll, amount, x, y),
    category="input", capability=Capability.INPUT_CONTROL,
))

registry.register(ToolSpec(
    name="read_clipboard",
    description="Read the current clipboard contents.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(mouse_keyboard.read_clipboard),
    category="clipboard", capability=Capability.CLIPBOARD,
))

registry.register(ToolSpec(
    name="write_clipboard",
    description="Copy text to the clipboard.",
    parameters={"type": "object", "properties": {"text": {"type": "string"}},
                "required": ["text"]},
    handler=lambda text: _wrap(mouse_keyboard.write_clipboard, text),
    category="clipboard", capability=Capability.CLIPBOARD,
))

registry.register(ToolSpec(
    name="copy_selection",
    description="Press Ctrl+C in the active window and read what was copied.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(mouse_keyboard.copy_selection),
    category="clipboard", capability=Capability.CLIPBOARD,
))
