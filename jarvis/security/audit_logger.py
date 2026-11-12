"""Audit trail: every tool invocation, approval decision and command run.

Writes to SQLite (queryable from the dashboard) and to the rotating log file.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..database.database import db
from ..event_bus import bus

log = logging.getLogger(__name__)

SUCCESS = "success"
DENIED = "denied"
BLOCKED = "blocked"
ERROR = "error"
CANCELLED = "cancelled"


class AuditLogger:
    def __init__(self) -> None:
        self._enabled = True

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def record(self, tool: str, arguments: Optional[Dict[str, Any]] = None,
               outcome: str = SUCCESS, message: str = "", actor: str = "assistant") -> None:
        arguments = arguments or {}
        if outcome not in (SUCCESS,):
            log.warning("audit | %s | %s | %s | %s", tool, outcome, message,
                        {k: v for k, v in list(arguments.items())[:6]})
        else:
            log.info("audit | %s | %s | %s", tool, outcome, message)
        if not self._enabled:
            return
        try:
            db.audit(tool, arguments, outcome, message, actor)
        except Exception:  # pragma: no cover - audit must never crash the app
            log.exception("Failed to persist audit entry")
        bus.publish("log", {
            "message": f"{tool}: {message or outcome}",
            "level": "info" if outcome == SUCCESS else "warning",
            "source": "audit",
        })

    def recent(self, limit: int = 100) -> List[Dict[str, Any]]:
        return db.audit_entries(limit)


audit_logger = AuditLogger()
