"""Conversation context assembly helpers.

Keeps the rolling window small enough for local models while preserving the
important pieces: system prompt, recent turns, tool results, memory.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .. import config
from ..database.database import db


def rolling_window(session_id: str, turns: int = 0) -> List[Dict[str, str]]:
    turns = turns or config.get().ai.context_turns
    history = db.recent_turns(session_id, limit=turns * 2)
    messages: List[Dict[str, str]] = []
    for row in history:
        if row["role"] in ("user", "assistant") and row["content"]:
            messages.append({"role": row["role"], "content": row["content"]})
    return messages


def summarise_session(session_id: str, max_items: int = 12) -> Dict[str, Any]:
    history = db.recent_turns(session_id, limit=max_items)
    topics = [row["content"][:80] for row in history if row["role"] == "user"]
    return {"turns": len(history), "topics": topics}
