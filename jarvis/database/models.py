"""Plain dataclasses mapping to database rows.

Kept dependency-free so they can be serialised straight to the dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ConversationTurn:
    id: Optional[int] = None
    session_id: str = ""
    role: str = "user"                 # user | assistant | system | tool
    content: str = ""
    intent: str = ""
    tools: str = ""                    # JSON encoded list of tool names
    created_at: str = ""               # ISO-8601


@dataclass
class MemoryItem:
    id: Optional[int] = None
    category: str = "general"          # preference | fact | project | shortcut | general
    key: str = ""
    value: str = ""
    source: str = "user"
    pinned: bool = False
    enabled: bool = True
    updated_at: str = ""


@dataclass
class TaskRecord:
    id: Optional[int] = None
    task_id: str = ""                  # uuid used by the queue
    title: str = ""
    kind: str = "tool"                 # tool | plan | automation
    payload: str = ""                  # JSON
    status: str = "queued"             # queued|running|done|failed|cancelled
    progress: float = 0.0
    detail: str = ""
    attempts: int = 0
    created_at: str = ""
    updated_at: str = ""


@dataclass
class AuditEntry:
    id: Optional[int] = None
    tool: str = ""
    arguments: str = ""                # JSON
    outcome: str = ""                  # success | denied | blocked | error
    message: str = ""
    actor: str = "assistant"
    created_at: str = ""


@dataclass
class Reminder:
    id: Optional[int] = None
    message: str = ""
    due_at: str = ""                   # ISO-8601 local time
    repeat: str = "none"               # none | daily | weekly
    done: bool = False
    created_at: str = ""


@dataclass
class Note:
    id: Optional[int] = None
    title: str = ""
    body: str = ""
    tags: str = ""
    created_at: str = ""
    updated_at: str = ""


@dataclass
class TodoItem:
    id: Optional[int] = None
    text: str = ""
    done: bool = False
    priority: int = 0
    due_at: str = ""
    created_at: str = ""


@dataclass
class CustomCommand:
    id: Optional[int] = None
    trigger: str = ""                  # phrase, e.g. "coding mode"
    action: str = ""                   # JSON action list
    enabled: bool = True
    description: str = ""
    created_at: str = ""


@dataclass
class SettingRow:
    key: str = ""
    value: str = ""


@dataclass
class ActionResult:
    """Uniform tool result returned to the AI engine and the dashboard."""

    ok: bool = False
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ok": self.ok,
            "message": self.message,
            "data": self.data,
            "verified": self.verified,
            "warnings": self.warnings,
        }
