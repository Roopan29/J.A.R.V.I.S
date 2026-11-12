"""Reminders with a scheduler thread, plus notes and todos helpers."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from ..database.database import db
from ..event_bus import EventType, bus

log = logging.getLogger(__name__)


class ReminderScheduler:
    """Checks the reminders table every few seconds and fires due ones."""

    def __init__(self, interval: float = 5.0) -> None:
        self.interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._speak: Optional[Callable[[str], None]] = None

    def attach_speaker(self, speak: Callable[[str], None]) -> None:
        self._speak = speak

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="reminder-scheduler",
                                        daemon=True)
        self._thread.start()
        log.info("Reminder scheduler started")

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.check_due()
            except Exception:
                log.exception("Reminder check failed")

    def check_due(self) -> List[Dict[str, Any]]:
        now = datetime.now().isoformat(timespec="seconds")
        due = db.due_reminders(now)
        for reminder in due:
            message = reminder["message"]
            db.complete_reminder(reminder["id"])
            bus.notify("Reminder", message, level="info")
            bus.log(f"Reminder: {message}", source="reminders")
            if reminder["repeat"] in ("daily", "weekly"):
                delta = timedelta(days=1 if reminder["repeat"] == "daily" else 7)
                try:
                    next_due = (datetime.fromisoformat(reminder["due_at"]) + delta).isoformat()
                    db.add_reminder(message, next_due, reminder["repeat"])
                except ValueError:
                    pass
            if self._speak:
                try:
                    self._speak(f"Reminder, sir: {message}")
                except Exception:
                    log.exception("Could not speak reminder")
        return due

    def upcoming(self, limit: int = 20) -> List[Dict[str, Any]]:
        return db.reminders()[:limit]


def parse_when(when: Optional[str]) -> datetime:
    """Parse ISO-8601, ``HH:MM`` or natural offsets like '10m'."""
    if not when:
        return datetime.now() + timedelta(minutes=5)
    text = when.strip().lower()
    match = __import__("re").match(r"^(\d+)\s*(s|m|h|d)$", text)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return datetime.now() + {"s": timedelta(seconds=amount), "m": timedelta(minutes=amount),
                                 "h": timedelta(hours=amount), "d": timedelta(days=amount)}[unit]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%H:%M"):
        try:
            parsed = datetime.strptime(text, fmt)
            if fmt == "%H:%M":
                parsed = datetime.now().replace(hour=parsed.hour, minute=parsed.minute,
                                                second=0, microsecond=0)
                if parsed < datetime.now():
                    parsed += timedelta(days=1)
            return parsed
        except ValueError:
            continue
    return datetime.now() + timedelta(minutes=5)


def set_reminder(message: str, due_at: Optional[str] = None,
                 repeat: str = "none") -> Dict[str, Any]:
    message = (message or "").strip()
    if not message:
        return {"ok": False, "message": "What should I remind you about?"}
    when = parse_when(due_at)
    repeat = repeat if repeat in ("none", "daily", "weekly") else "none"
    reminder_id = db.add_reminder(message, when.isoformat(timespec="seconds"), repeat)
    bus.publish(EventType.LOG, {"message": f"Reminder set: {message}",
                                "level": "info", "source": "reminders"})
    return {"ok": True, "verified": True,
            "message": f"Reminder set for {when.strftime('%H:%M on %d %b')}: {message}",
            "data": {"id": reminder_id, "due_at": when.isoformat(),
                     "repeat": repeat}}


def list_reminders(include_done: bool = False) -> List[Dict[str, Any]]:
    return db.reminders(include_done=include_done)


def delete_reminder(reminder_id: int) -> Dict[str, Any]:
    db.delete_reminder(reminder_id)
    return {"ok": True, "verified": True, "message": "Reminder deleted."}


scheduler = ReminderScheduler()
