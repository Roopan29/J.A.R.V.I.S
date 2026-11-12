"""Memory tools."""
from __future__ import annotations

from ...database.models import ActionResult
from ...intelligence import memory
from ..tool_registry import ToolSpec, registry


def _to_result(payload) -> ActionResult:
    if isinstance(payload, ActionResult):
        return payload
    if isinstance(payload, dict):
        return ActionResult(ok=bool(payload.get("ok", True)),
                            message=payload.get("message", ""),
                            data=payload.get("data", {}),
                            verified=bool(payload.get("verified", False)))
    return ActionResult(ok=True, message=str(payload))


registry.register(ToolSpec(
    name="remember",
    description="Store a durable fact or preference about the user. Never store passwords, "
                "API keys or other secrets.",
    parameters={"type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Short label, e.g. 'name'"},
                    "value": {"type": "string"},
                    "category": {"type": "string",
                                 "enum": memory.CATEGORIES, "default": "general"},
                },
                "required": ["key", "value"]},
    handler=lambda key, value, category="general":
        _to_result(memory.remember(key, value, category, source="assistant")),
    category="memory",
))

registry.register(ToolSpec(
    name="recall_memory",
    description="Look up what you remember about the user.",
    parameters={"type": "object",
                "properties": {"query": {"type": "string"},
                               "category": {"type": "string", "enum": memory.CATEGORIES}}},
    handler=lambda query=None, category=None: _to_result(memory.recall(query, category)),
    category="memory",
))

registry.register(ToolSpec(
    name="forget_memory",
    description="Delete a memory entry by key. Requires confirmation.",
    parameters={"type": "object",
                "properties": {"key": {"type": "string"}},
                "required": ["key"]},
    handler=lambda key: _to_result(memory.forget(key=key)),
    category="memory", risk="medium", requires_confirmation=True,
    confirmation_template="Forget what I remember about '{key}'?",
))
