"""SQLite database with a small repository API.

Design notes
------------
* One connection per thread (``threading.local``) with WAL enabled, so the
  FastAPI threads, the task worker and the voice loop can all write safely.
* All timestamps are UTC ISO-8601 strings; helpers convert for display.
* Schema migration is additive and idempotent (``CREATE TABLE IF NOT EXISTS``).
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .. import config

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS conversation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    intent TEXT DEFAULT '',
    tools TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversation(session_id, id);
CREATE INDEX IF NOT EXISTS idx_conv_created ON conversation(created_at);

CREATE TABLE IF NOT EXISTS memory (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL DEFAULT 'general',
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    source TEXT DEFAULT 'user',
    pinned INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    updated_at TEXT NOT NULL,
    UNIQUE(category, key)
);
CREATE INDEX IF NOT EXISTS idx_memory_cat ON memory(category);

CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    kind TEXT DEFAULT 'tool',
    payload TEXT DEFAULT '',
    status TEXT DEFAULT 'queued',
    progress REAL DEFAULT 0,
    detail TEXT DEFAULT '',
    attempts INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tool TEXT NOT NULL,
    arguments TEXT DEFAULT '',
    outcome TEXT NOT NULL,
    message TEXT DEFAULT '',
    actor TEXT DEFAULT 'assistant',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit(created_at);

CREATE TABLE IF NOT EXISTS reminders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    due_at TEXT NOT NULL,
    repeat TEXT DEFAULT 'none',
    done INTEGER DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reminder_due ON reminders(done, due_at);

CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT DEFAULT '',
    tags TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL,
    done INTEGER DEFAULT 0,
    priority INTEGER DEFAULT 0,
    due_at TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_commands (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL UNIQUE,
    action TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clipboard_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


# Columns added after the first release. Older databases are missing these
# even though CREATE TABLE IF NOT EXISTS leaves the stale table alone.
REQUIRED_COLUMNS: Dict[str, Dict[str, str]] = {
    "reminders": {"done": "INTEGER DEFAULT 0", "repeat": "TEXT DEFAULT 'none'"},
    "todos": {"done": "INTEGER DEFAULT 0", "priority": "INTEGER DEFAULT 0",
              "due_at": "TEXT DEFAULT ''"},
    "tasks": {"kind": "TEXT DEFAULT 'tool'", "payload": "TEXT DEFAULT ''",
              "status": "TEXT DEFAULT 'queued'", "progress": "REAL DEFAULT 0",
              "detail": "TEXT DEFAULT ''", "attempts": "INTEGER DEFAULT 0"},
}


def _split_statements(script: str) -> List[str]:
    """Split a SQL script into statements, ignoring blank lines and comments."""
    statements: List[str] = []
    buffer: List[str] = []
    for line in script.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buffer.append(line)
        if stripped.endswith(";"):
            statements.append("\n".join(buffer).strip())
            buffer = []
    if buffer:
        statements.append("\n".join(buffer).strip())
    return [stmt.rstrip(";") for stmt in statements if stmt.rstrip(";").strip()]


def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    def __init__(self, path: Optional[Path] = None) -> None:
        self.path = Path(path) if path else config.DATA_DIR / "jarvis.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        self.init_schema()

    # -- connection management -------------------------------------------
    def connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=15000")
            self._local.conn = conn
        return conn

    def init_schema(self) -> None:
        """Create or repair the schema.

        ``CREATE TABLE IF NOT EXISTS`` silently keeps an *older* table shape, so
        a database written by a previous version can be missing columns that the
        indexes and queries below require. Applying statements one at a time and
        adding any missing column keeps start-up working instead of crashing.
        """
        with self._init_lock:
            conn = self.connect()
            self._reconcile_columns(conn)
            for statement in _split_statements(SCHEMA):
                # A single index/table statement can need more than one added
                # column, so retry until it succeeds or nothing is repaired.
                for _ in range(8):
                    try:
                        conn.execute(statement)
                        break
                    except sqlite3.OperationalError as exc:
                        if not self._repair(conn, statement, exc):
                            raise
            conn.commit()
        log.info("Database ready at %s", self.path)

    def _reconcile_columns(self, conn: sqlite3.Connection) -> None:
        """Add every known-required column that an older database lacks."""
        for table, columns in REQUIRED_COLUMNS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # table does not exist yet; SCHEMA will create it
            for column, decl in columns.items():
                if column not in existing:
                    log.warning("Migrating database: adding %s.%s", table, column)
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()

    # -- schema repair -----------------------------------------------------
    def _repair(self, conn: sqlite3.Connection, statement: str,
                exc: sqlite3.OperationalError) -> bool:
        """Add missing columns, then retry the failed statement.

        Returns True when the statement is worth retrying.
        """
        message = str(exc).lower()
        if "no such column" not in message:
            return False
        missing = message.split("no such column:", 1)[1].strip()
        # Work out which table the failing statement belongs to.
        table = ""
        match = re.search(r"\bon\s+([a-z_][a-z0-9_]*)", statement, re.I)
        if match:
            table = match.group(1)
        else:
            match = re.search(r"CREATE TABLE IF NOT EXISTS\s+([a-z_][a-z0-9_]*)", statement, re.I)
            if match:
                table = match.group(1)
        if not table and "done" in missing:
            table = "reminders"
        if not table:
            return False
        return self._ensure_column(conn, table, missing)

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str) -> bool:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            return False
        if column in existing:
            return True
        # Every column we look up here has a harmless default.
        defaults = {"done": "INTEGER DEFAULT 0", "priority": "INTEGER DEFAULT 0",
                    "due_at": "TEXT DEFAULT ''", "repeat": "TEXT DEFAULT 'none'"}
        decl = defaults.get(column, "TEXT DEFAULT ''")
        log.warning("Migrating database: adding %s.%s", table, column)
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.commit()
        return True

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -- low level helpers ------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        conn = self.connect()
        cur = conn.execute(sql, params)
        conn.commit()
        return cur

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        cur = self.connect().execute(sql, params)
        return [dict(row) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- conversation -----------------------------------------------------
    def add_turn(self, session_id: str, role: str, content: str,
                 intent: str = "", tools: Optional[Iterable[str]] = None) -> int:
        cur = self.execute(
            "INSERT INTO conversation(session_id, role, content, intent, tools, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (session_id, role, content, intent,
             json.dumps(list(tools or [])), utcnow()),
        )
        return int(cur.lastrowid)

    def recent_turns(self, session_id: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        if session_id:
            rows = self.query(
                "SELECT * FROM conversation WHERE session_id=? ORDER BY id DESC LIMIT ?",
                (session_id, limit),
            )
        else:
            rows = self.query("SELECT * FROM conversation ORDER BY id DESC LIMIT ?", (limit,))
        return list(reversed(rows))

    def clear_conversation(self, session_id: Optional[str] = None) -> int:
        cur = (self.execute("DELETE FROM conversation WHERE session_id=?", (session_id,))
               if session_id else self.execute("DELETE FROM conversation"))
        return cur.rowcount

    # -- memory -----------------------------------------------------------
    def remember(self, key: str, value: str, category: str = "general",
                 source: str = "user", pinned: bool = False) -> None:
        self.execute(
            """INSERT INTO memory(category, key, value, source, pinned, enabled, updated_at)
               VALUES (?,?,?,?,?,1,?)
               ON CONFLICT(category, key) DO UPDATE SET
                 value=excluded.value, source=excluded.source,
                 pinned=excluded.pinned, updated_at=excluded.updated_at""",
            (category, key, value, source, int(pinned), utcnow()),
        )

    def memory_items(self, category: Optional[str] = None,
                     enabled_only: bool = False, limit: int = 500) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM memory WHERE 1=1"
        params: List[Any] = []
        if category:
            sql += " AND category=?"
            params.append(category)
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY pinned DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        return self.query(sql, params)

    def memory_search(self, term: str, limit: int = 30) -> List[Dict[str, Any]]:
        like = f"%{term}%"
        return self.query(
            "SELECT * FROM memory WHERE (key LIKE ? OR value LIKE ?) ORDER BY pinned DESC, updated_at DESC LIMIT ?",
            (like, like, limit),
        )

    def forget(self, memory_id: int) -> bool:
        return self.execute("DELETE FROM memory WHERE id=?", (memory_id,)).rowcount > 0

    def set_memory_enabled(self, memory_id: int, enabled: bool) -> None:
        self.execute("UPDATE memory SET enabled=? WHERE id=?", (int(enabled), memory_id))

    def clear_memory(self) -> int:
        return self.execute("DELETE FROM memory").rowcount

    # -- tasks ------------------------------------------------------------
    def upsert_task(self, task_id: str, title: str, kind: str = "tool",
                    status: str = "queued", progress: float = 0.0,
                    detail: str = "", payload: Dict[str, Any] | None = None,
                    attempts: int = 0) -> None:
        now = utcnow()
        self.execute(
            """INSERT INTO tasks(task_id,title,kind,payload,status,progress,detail,attempts,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(task_id) DO UPDATE SET
                 status=excluded.status, progress=excluded.progress,
                 detail=excluded.detail, attempts=excluded.attempts,
                 updated_at=excluded.updated_at""",
            (task_id, title, kind, json.dumps(payload or {}), status, progress,
             detail, attempts, now, now),
        )

    def tasks(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.query("SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,))

    # -- audit ------------------------------------------------------------
    def audit(self, tool: str, arguments: Dict[str, Any], outcome: str,
              message: str = "", actor: str = "assistant") -> None:
        self.execute(
            "INSERT INTO audit(tool, arguments, outcome, message, actor, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (tool, json.dumps(arguments, default=str)[:4000], outcome, message[:2000],
             actor, utcnow()),
        )

    def audit_entries(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.query("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (limit,))

    # -- reminders --------------------------------------------------------
    def add_reminder(self, message: str, due_at: str, repeat: str = "none") -> int:
        cur = self.execute(
            "INSERT INTO reminders(message, due_at, repeat, done, created_at) VALUES (?,?,?,0,?)",
            (message, due_at, repeat or 'none', utcnow()),
        )
        return int(cur.lastrowid)

    def reminders(self, include_done: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM reminders"
        if not include_done:
            sql += " WHERE done=0"
        sql += " ORDER BY due_at ASC"
        return self.query(sql)

    def due_reminders(self, now_iso: str) -> List[Dict[str, Any]]:
        return self.query(
            "SELECT * FROM reminders WHERE done=0 AND due_at<=? ORDER BY due_at", (now_iso,)
        )

    def complete_reminder(self, reminder_id: int) -> None:
        self.execute("UPDATE reminders SET done=1 WHERE id=?", (reminder_id,))

    def delete_reminder(self, reminder_id: int) -> None:
        self.execute("DELETE FROM reminders WHERE id=?", (reminder_id,))

    # -- notes ------------------------------------------------------------
    def add_note(self, title: str, body: str = "", tags: str = "") -> int:
        now = utcnow()
        cur = self.execute(
            "INSERT INTO notes(title, body, tags, created_at, updated_at) VALUES (?,?,?,?,?)",
            (title, body, tags, now, now),
        )
        return int(cur.lastrowid)

    def notes(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.query("SELECT * FROM notes ORDER BY updated_at DESC LIMIT ?", (limit,))

    def update_note(self, note_id: int, title: Optional[str] = None,
                    body: Optional[str] = None, tags: Optional[str] = None) -> None:
        sets, params = [], []
        for column, value in (("title", title), ("body", body), ("tags", tags)):
            if value is not None:
                sets.append(f"{column}=?")
                params.append(value)
        if not sets:
            return
        sets.append("updated_at=?")
        params.extend([utcnow(), note_id])
        self.execute(f"UPDATE notes SET {', '.join(sets)} WHERE id=?", params)

    def delete_note(self, note_id: int) -> None:
        self.execute("DELETE FROM notes WHERE id=?", (note_id,))

    # -- todos ------------------------------------------------------------
    def add_todo(self, text: str, priority: int = 0, due_at: str = "") -> int:
        cur = self.execute(
            "INSERT INTO todos(text, done, priority, due_at, created_at) VALUES (?,0,?,?,?)",
            (text, priority, due_at, utcnow()),
        )
        return int(cur.lastrowid)

    def todos(self, include_done: bool = True) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM todos"
        if not include_done:
            sql += " WHERE done=0"
        return self.query(sql + " ORDER BY done ASC, priority DESC, id ASC")

    def set_todo_done(self, todo_id: int, done: bool) -> None:
        self.execute("UPDATE todos SET done=? WHERE id=?", (int(done), todo_id))

    def delete_todo(self, todo_id: int) -> None:
        self.execute("DELETE FROM todos WHERE id=?", (todo_id,))

    # -- custom commands --------------------------------------------------
    def add_custom_command(self, trigger: str, action: Dict[str, Any],
                           description: str = "", enabled: bool = True) -> int:
        cur = self.execute(
            """INSERT INTO custom_commands(trigger, action, enabled, description, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(trigger) DO UPDATE SET action=excluded.action,
                 enabled=excluded.enabled, description=excluded.description""",
            (trigger.lower().strip(), json.dumps(action), int(enabled), description, utcnow()),
        )
        return cur.lastrowid or self._custom_command_id(trigger)

    def _custom_command_id(self, trigger: str) -> int:
        row = self.query_one("SELECT id FROM custom_commands WHERE trigger=?", (trigger.lower().strip(),))
        return int(row["id"]) if row else 0

    def custom_commands(self, enabled_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM custom_commands"
        if enabled_only:
            sql += " WHERE enabled=1"
        return self.query(sql + " ORDER BY trigger")

    def set_custom_command_enabled(self, command_id: int, enabled: bool) -> None:
        self.execute("UPDATE custom_commands SET enabled=? WHERE id=?", (int(enabled), command_id))

    def delete_custom_command(self, command_id: int) -> None:
        self.execute("DELETE FROM custom_commands WHERE id=?", (command_id,))

    # -- settings kv ------------------------------------------------------
    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def get_setting(self, key: str, default: str = "") -> str:
        row = self.query_one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else default

    # -- clipboard history -------------------------------------------------
    def add_clipboard(self, content: str) -> None:
        self.execute("INSERT INTO clipboard_history(content, created_at) VALUES (?,?)",
                     (content, utcnow()))

    def clipboard_history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.query("SELECT * FROM clipboard_history ORDER BY id DESC LIMIT ?", (limit,))

    # -- statistics for the dashboard -------------------------------------
    def stats(self) -> Dict[str, Any]:
        def count(table: str) -> int:
            row = self.query_one(f"SELECT COUNT(*) AS n FROM {table}")
            return int(row["n"]) if row else 0
        return {
            "conversation_turns": count("conversation"),
            "memories": count("memory"),
            "tasks": count("tasks"),
            "audit_entries": count("audit"),
            "notes": count("notes"),
            "todos": count("todos"),
            "reminders": count("reminders"),
        }


#: Default application database (created against JARVIS_HOME).
db = Database()
