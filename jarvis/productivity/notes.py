"""Notes and to-do lists backed by SQLite."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..database.database import db
from ..event_bus import bus


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------
def create_note(title: str, body: str = "", tags: str = "") -> Dict[str, Any]:
    title = (title or "").strip()
    if not title:
        return {"ok": False, "message": "A note needs a title."}
    note_id = db.add_note(title[:200], body[:20000], tags[:200])
    bus.log(f"Note created: {title}", source="notes")
    return {"ok": True, "verified": True, "message": f"Created note '{title}'.",
            "data": {"id": note_id, "title": title}}


def list_notes() -> List[Dict[str, Any]]:
    return db.notes()


def get_note(note_id: int) -> Optional[Dict[str, Any]]:
    return next((n for n in db.notes() if n["id"] == note_id), None)


def update_note(note_id: int, title: Optional[str] = None, body: Optional[str] = None,
                tags: Optional[str] = None) -> Dict[str, Any]:
    db.update_note(note_id, title, body, tags)
    return {"ok": True, "verified": True, "message": "Note updated."}


def delete_note(note_id: int) -> Dict[str, Any]:
    db.delete_note(note_id)
    return {"ok": True, "verified": True, "message": "Note deleted."}


# ---------------------------------------------------------------------------
# Todos
# ---------------------------------------------------------------------------
def add_todo(text: str, priority: int = 0, due_at: str = "") -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"ok": False, "message": "What should I add to the list?"}
    todo_id = db.add_todo(text[:500], max(0, min(int(priority), 3)), due_at)
    return {"ok": True, "verified": True, "message": f"Added '{text}' to your list.",
            "data": {"id": todo_id}}


def list_todos(include_done: bool = True) -> List[Dict[str, Any]]:
    return db.todos(include_done=include_done)


def complete_todo(todo_id: int, done: bool = True) -> Dict[str, Any]:
    db.set_todo_done(todo_id, done)
    return {"ok": True, "verified": True,
            "message": "Marked done." if done else "Reopened."}


def delete_todo(todo_id: int) -> Dict[str, Any]:
    db.delete_todo(todo_id)
    return {"ok": True, "verified": True, "message": "Task removed from the list."}


def daily_summary() -> Dict[str, Any]:
    todos = [t for t in db.todos(include_done=False)]
    reminders = db.reminders()
    notes = db.notes(limit=5)
    lines = []
    if todos:
        lines.append(f"{len(todos)} open task(s): " + ", ".join(t["text"][:60] for t in todos[:5]))
    if reminders:
        lines.append(f"{len(reminders)} reminder(s), next: {reminders[0]['due_at'][11:16]} "
                     f"— {reminders[0]['message'][:60]}")
    if notes:
        lines.append(f"{len(notes)} recent note(s)")
    message = ". ".join(lines) + "." if lines else "Nothing pending, sir. All clear."
    return {"ok": True, "verified": True, "message": message,
            "data": {"todos": todos, "reminders": reminders, "notes": notes}}
