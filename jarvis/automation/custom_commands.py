"""User-defined voice triggers ("when I say coding mode, …").

Stored in SQLite as trigger -> {"steps": [...]}. The conversation engine
matches triggers before it consults the model, so custom commands are instant
and always honoured.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from ..database.database import db
from ..event_bus import EventType, bus


def add_command(trigger: str, steps: List[Dict[str, Any]],
                description: str = "") -> Dict[str, Any]:
    trigger = (trigger or "").strip().lower()
    if not trigger:
        return {"ok": False, "message": "A trigger phrase is required."}
    if not steps:
        return {"ok": False, "message": "At least one step is required."}
    from ..core.tool_registry import registry
    for step in steps:
        if not isinstance(step, dict) or not step.get("tool"):
            return {"ok": False, "message": "Each step needs a 'tool' name."}
        if registry.get(step["tool"]) is None:
            return {"ok": False, "message": f"Unknown tool '{step['tool']}'."}
    command_id = db.add_custom_command(trigger, {"steps": steps}, description)
    bus.publish(EventType.SETTINGS, {"event": "custom_command_added", "trigger": trigger})
    return {"ok": True, "verified": True,
            "message": f"Custom command '{trigger}' saved with {len(steps)} step(s).",
            "data": {"id": command_id, "trigger": trigger, "steps": steps}}


def list_commands(enabled_only: bool = False) -> List[Dict[str, Any]]:
    commands = db.custom_commands(enabled_only=enabled_only)
    for command in commands:
        try:
            command["action"] = json.loads(command["action"])
        except (json.JSONDecodeError, TypeError):
            command["action"] = {}
    return commands


def toggle_command(command_id: int, enabled: bool) -> Dict[str, Any]:
    db.set_custom_command_enabled(command_id, enabled)
    return {"ok": True, "verified": True,
            "message": f"Command {'enabled' if enabled else 'disabled'}."}


def delete_command(command_id: int) -> Dict[str, Any]:
    db.delete_custom_command(command_id)
    return {"ok": True, "verified": True, "message": "Custom command deleted."}


def find(trigger: str) -> Optional[Dict[str, Any]]:
    for command in list_commands(enabled_only=True):
        if command["trigger"] == (trigger or "").strip().lower():
            return command
    return None
