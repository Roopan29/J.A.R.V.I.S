"""Screen capture, OCR and vision-model tools (permission gated)."""
from __future__ import annotations

from ...computer import screen_vision
from ...computer.platform_support import NotSupported
from ...database.models import ActionResult
from ..permissions import Capability
from ..tool_registry import ToolSpec, registry


def _wrap(fn, *args, **kwargs) -> ActionResult:
    try:
        result = fn(*args, **kwargs)
    except NotSupported as exc:
        return ActionResult(ok=False, message=str(exc))
    except (OSError, ValueError, RuntimeError) as exc:
        return ActionResult(ok=False, message=f"{type(exc).__name__}: {exc}")
    if isinstance(result, dict):
        result.pop("_image", None)
        return ActionResult(ok=bool(result.get("ok", True)),
                            message=result.get("message", ""),
                            data=result.get("data", {}),
                            verified=bool(result.get("verified", False)))
    return ActionResult(ok=True, message=str(result))


def _describe_screen(question: str = "Describe what is on screen.") -> ActionResult:
    return _wrap(screen_vision.analyse_screen, question)


registry.register(ToolSpec(
    name="take_screenshot",
    description="Capture a screenshot and save it to disk.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string", "description": "Optional save path"}}},
    handler=lambda path=None: _wrap(screen_vision.screenshot_to_file, path),
    category="vision", capability=Capability.SCREEN_CAPTURE, risk="medium",
))

registry.register(ToolSpec(
    name="read_screen_text",
    description="OCR the screen and return the visible text.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(screen_vision.ocr),
    category="vision", capability=Capability.SCREEN_CAPTURE, risk="medium",
))

registry.register(ToolSpec(
    name="describe_screen",
    description="Describe what is on screen (uses a vision model, falls back to OCR), "
                "and explain errors or point out UI elements.",
    parameters={"type": "object",
                "properties": {"question": {"type": "string",
                                            "description": "What to look for on screen"}}},
    handler=_describe_screen,
    category="vision", capability=Capability.SCREEN_CAPTURE, risk="medium",
))

registry.register(ToolSpec(
    name="list_visible_windows",
    description="List windows currently visible on screen (screen permission required).",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(screen_vision.identify_windows),
    category="vision", capability=Capability.SCREEN_CAPTURE,
))


def _describe_screen(question: str = "Describe what is on the screen and highlight any "
                                   "errors or dialogs.") -> ActionResult:
    return _wrap(screen_vision.analyse_screen, question)
