"""Reminders, timers, notes and todos tools."""
from __future__ import annotations

from ...database.models import ActionResult
from ...productivity import notes, reminders, timers
from ..tool_registry import ToolSpec, registry


def _to_result(payload) -> ActionResult:
    if isinstance(payload, dict):
        return ActionResult(ok=bool(payload.get("ok", True)),
                            message=payload.get("message", ""),
                            data=payload.get("data", {}),
                            verified=bool(payload.get("verified", False)))
    return ActionResult(ok=True, message=str(payload))


registry.register(ToolSpec(
    name="set_reminder",
    description="Set a reminder for a specific time.",
    parameters={"type": "object",
                "properties": {
                    "message": {"type": "string"},
                    "due_at": {"type": "string",
                               "description": "ISO-8601 datetime, 'HH:MM', or a relative "
                                              "offset like '10m'"},
                    "repeat": {"type": "string", "enum": ["none", "daily", "weekly"],
                               "default": "none"},
                },
                "required": ["message"]},
    handler=lambda message, due_at=None, repeat="none":
        _to_result(reminders.set_reminder(message, due_at, repeat)),
    category="productivity",
))

registry.register(ToolSpec(
    name="list_reminders",
    description="List upcoming reminders.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: ActionResult(ok=True, verified=True,
                                 message=f"{len(reminders.list_reminders())} reminder(s).",
                                 data={"reminders": reminders.list_reminders()}),
    category="productivity",
))

registry.register(ToolSpec(
    name="delete_reminder",
    description="Delete a reminder by id.",
    parameters={"type": "object", "properties": {"reminder_id": {"type": "integer"}},
                "required": ["reminder_id"]},
    handler=lambda reminder_id: _to_result(reminders.delete_reminder(reminder_id)),
    category="productivity",
))

registry.register(ToolSpec(
    name="set_timer",
    description="Start a countdown timer that announces when it finishes.",
    parameters={"type": "object",
                "properties": {"seconds": {"type": "integer"},
                               "label": {"type": "string", "default": "Timer"}},
                "required": ["seconds"]},
    handler=lambda seconds, label="Timer": _to_result(timers.timer_manager.set_timer(seconds, label)),
    category="productivity",
))

registry.register(ToolSpec(
    name="cancel_timer",
    description="Cancel running timers.",
    parameters={"type": "object", "properties": {"timer_id": {"type": "string"}}},
    handler=lambda timer_id=None: _to_result(timers.timer_manager.cancel(timer_id)),
    category="productivity",
))

registry.register(ToolSpec(
    name="list_timers",
    description="List active timers.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: ActionResult(ok=True, verified=True,
                                 message=f"{len(timers.timer_manager.list())} active timer(s).",
                                 data={"timers": timers.timer_manager.list()}),
    category="productivity",
))

registry.register(ToolSpec(
    name="create_note",
    description="Create a note.",
    parameters={"type": "object",
                "properties": {"title": {"type": "string"}, "body": {"type": "string"},
                               "tags": {"type": "string"}},
                "required": ["title"]},
    handler=lambda title, body="", tags="": _to_result(notes.create_note(title, body, tags)),
    category="productivity",
))

registry.register(ToolSpec(
    name="list_notes",
    description="List saved notes.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: ActionResult(ok=True, verified=True,
                                 message=f"{len(notes.list_notes())} note(s).",
                                 data={"notes": notes.list_notes()}),
    category="productivity",
))

registry.register(ToolSpec(
    name="add_todo",
    description="Add an item to the to-do list.",
    parameters={"type": "object",
                "properties": {"text": {"type": "string"},
                               "priority": {"type": "integer", "minimum": 0, "maximum": 3},
                               "due_at": {"type": "string"}},
                "required": ["text"]},
    handler=lambda text, priority=0, due_at="": _to_result(notes.add_todo(text, priority, due_at)),
    category="productivity",
))

registry.register(ToolSpec(
    name="list_todos",
    description="List the to-do list.",
    parameters={"type": "object",
                "properties": {"include_done": {"type": "boolean", "default": True}}},
    handler=lambda include_done=True: ActionResult(
        ok=True, verified=True,
        message=f"{len(notes.list_todos(include_done))} task(s).",
        data={"todos": notes.list_todos(include_done)}),
    category="productivity",
))

registry.register(ToolSpec(
    name="complete_todo",
    description="Mark a to-do item done (or reopen it).",
    parameters={"type": "object",
                "properties": {"todo_id": {"type": "integer"},
                               "done": {"type": "boolean", "default": True}},
                "required": ["todo_id"]},
    handler=lambda todo_id, done=True: _to_result(notes.complete_todo(todo_id, done)),
    category="productivity",
))

registry.register(ToolSpec(
    name="daily_summary",
    description="Summarise today's tasks, reminders and notes.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _to_result(notes.daily_summary()),
    category="productivity",
))
