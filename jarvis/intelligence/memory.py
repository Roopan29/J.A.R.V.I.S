"""Persistent memory over SQLite with a sensitivity filter.

Never stores passwords, keys or tokens: :func:`is_sensitive` rejects them and
the tool layer logs the refusal instead of silently dropping the request.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from ..database.database import db
from ..event_bus import EventType, bus

log = logging.getLogger(__name__)

SENSITIVE = re.compile(
    r"\b(password|passwd|passphrase|api[_ -]?key|secret|token|credential|"
    r"private key|seed phrase|recovery code|ssn|social security|"
    r"credit card|cvv|pin code|otp)\b", re.I)

CATEGORIES = ["preference", "fact", "project", "shortcut", "general"]


def is_sensitive(text: str) -> bool:
    return bool(SENSITIVE.search(text or ""))


def remember(key: str, value: str, category: str = "general",
             source: str = "user") -> Dict[str, Any]:
    key = (key or "").strip()
    value = (value or "").strip()
    if not key or not value:
        return {"ok": False, "message": "Both a key and a value are required."}
    if not db_enabled():
        return {"ok": False, "message": "Memory is disabled in settings."}
    if is_sensitive(key) or is_sensitive(value):
        return {"ok": False,
                "message": "I will not store credentials or secrets in memory, sir. "
                           "Use Settings → AI to store API keys securely instead."}
    if category not in CATEGORIES:
        category = "general"
    db.remember(key=key.lower()[:120], value=value[:1000], category=category, source=source)
    bus.publish(EventType.MEMORY, {"event": "updated", "key": key, "category": category})
    return {"ok": True, "verified": True,
            "message": f"Remembered {key}: {value[:120]}",
            "data": {"key": key, "value": value, "category": category}}


def recall(query: Optional[str] = None, category: Optional[str] = None,
           limit: int = 20) -> Dict[str, Any]:
    if query:
        items = db.memory_search(query, limit=limit)
        label = f"matching '{query}'"
    else:
        items = db.memory_items(category=category, limit=limit)
        label = f"in {category}" if category else "stored"
    if not items:
        return {"ok": True, "verified": True,
                "message": f"I have nothing {label} in memory.", "data": {"items": []}}
    listing = "; ".join(f"{i['key']} = {i['value']}" for i in items[:8])
    return {"ok": True, "verified": True,
            "message": f"{len(items)} memory item(s) {label}: {listing}",
            "data": {"items": items}}


def forget(memory_id: Optional[int] = None, key: Optional[str] = None) -> Dict[str, Any]:
    if memory_id:
        removed = db.forget(memory_id)
        message = "Memory entry deleted." if removed else "No such memory entry."
    elif key:
        matches = [m for m in db.memory_search(key, limit=10) if m["key"] == key.lower()]
        removed = sum(db.forget(m["id"]) for m in matches)
        message = f"Forgot {removed} entry(ies) for '{key}'."
    else:
        return {"ok": False, "message": "Provide a key or an id to forget."}
    bus.publish(EventType.MEMORY, {"event": "deleted"})
    return {"ok": True, "verified": True, "message": message}


def list_all() -> List[Dict[str, Any]]:
    return db.memory_items(limit=500)


def set_enabled(memory_id: int, enabled: bool) -> None:
    db.set_memory_enabled(memory_id, enabled)
    bus.publish(EventType.MEMORY, {"event": "toggled", "id": memory_id, "enabled": enabled})


def clear_all() -> Dict[str, Any]:
    count = db.clear_memory()
    bus.publish(EventType.MEMORY, {"event": "cleared"})
    return {"ok": True, "verified": True, "message": f"Cleared {count} memory entries."}


def db_enabled() -> bool:
    from .. import config
    return config.get().memory_enabled
