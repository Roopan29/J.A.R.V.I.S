"""Async event bus — the spine connecting backend modules to the dashboard.

Everything that happens (a log line, a task update, a metric sample, a state
change) is published as an event. The WebSocket layer fans events out to
connected dashboards. Subscribers receive an :class:`Event`.
"""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Deque, Dict, List, Optional, Set

log = logging.getLogger(__name__)


class EventType:
    """Canonical event names (plain strings so the JS side stays simple)."""

    STATE = "state"                    # assistant state change
    METRICS = "metrics"                # system telemetry sample
    LOG = "log"                        # activity log line for the dashboard
    NOTIFICATION = "notification"      # toast / alert
    TRANSCRIPT = "transcript"          # speech recognised
    RESPONSE = "response"              # assistant reply
    COMMAND = "command"                # command received (voice or typed)
    TASK = "task"                      # task queue update
    PERMISSION = "permission"          # confirmation request / resolution
    MEMORY = "memory"                  # memory changed
    SETTINGS = "settings"              # settings changed
    WAKE = "wake"                      # wake word detected
    ERROR = "error"
    AUDIO_LEVEL = "audio_level"        # mic level for the visualiser


@dataclass
class Event:
    type: str
    data: Dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "type": self.type, "ts": self.ts, "data": self.data}

    def to_json(self) -> str:
        import json
        return json.dumps(self.to_dict(), default=str)


Subscriber = Callable[[Event], Any]


class EventBus:
    """Thread-safe-ish async bus with a replay buffer.

    Callbacks may be sync or async. Sync callbacks are executed inline; async
    ones are scheduled on the running loop. Publishing from a worker thread is
    supported through :meth:`publish_threadsafe`.
    """

    def __init__(self, history: int = 300) -> None:
        self._subscribers: Dict[str, Set[Subscriber]] = {}
        self._everyone: Set[Subscriber] = set()
        self._history: Deque[Event] = deque(maxlen=history)
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    # -- loop awareness ---------------------------------------------------
    def bind_loop(self, loop: Optional[asyncio.AbstractEventLoop] = None) -> None:
        self._loop = loop or asyncio.get_running_loop()

    # -- subscription -----------------------------------------------------
    def subscribe(self, event_type: str = "*", callback: Subscriber = None) -> Subscriber:
        if callback is None:  # allow @bus.subscribe("state") usage
            def decorator(fn: Subscriber) -> Subscriber:
                self.subscribe(event_type, fn)
                return fn
            return decorator  # type: ignore[return-value]
        if event_type == "*":
            self._everyone.add(callback)
        else:
            self._subscribers.setdefault(event_type, set()).add(callback)
        return callback

    def unsubscribe(self, event_type: str, callback: Subscriber) -> None:
        target = self._everyone if event_type == "*" else self._subscribers.get(event_type, set())
        target.discard(callback)

    # -- publishing -------------------------------------------------------
    def publish(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> Event:
        event = Event(type=event_type, data=data or {})
        self._history.append(event)
        callbacks = list(self._subscribers.get(event_type, ())) + list(self._everyone)
        for callback in callbacks:
            try:
                result = callback(event)
                if inspect.isawaitable(result):
                    self._schedule(result)
            except Exception:  # a bad subscriber must not break the app
                log.exception("Event subscriber failed for %s", event_type)
        return event

    def publish_threadsafe(self, event_type: str, data: Optional[Dict[str, Any]] = None) -> None:
        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(self.publish, event_type, data or {})
        else:
            self.publish(event_type, data)

    def _schedule(self, awaitable) -> None:
        try:
            asyncio.get_running_loop().create_task(awaitable)
        except RuntimeError:
            if self._loop and self._loop.is_running():
                asyncio.run_coroutine_threadsafe(awaitable, self._loop)
            else:  # pragma: no cover - nothing we can do without a loop
                log.debug("Dropping async subscriber result: no running loop")
                close = getattr(awaitable, "close", None)
                if callable(close):
                    close()

    # -- introspection ----------------------------------------------------
    def history(self, limit: int = 100, types: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        items = list(self._history)
        if types:
            items = [e for e in items if e.type in types]
        return [e.to_dict() for e in items[-limit:]]

    # -- convenience helpers used across the app --------------------------
    def log(self, message: str, level: str = "info", source: str = "core", **extra) -> None:
        payload = {"message": message, "level": level, "source": source, **extra}
        self.publish(EventType.LOG, payload)
        log.debug("[%s] %s", source, message)

    def notify(self, title: str, message: str, level: str = "info") -> None:
        self.publish(EventType.NOTIFICATION, {"title": title, "message": message, "level": level})


#: Application-wide bus instance.
bus = EventBus()

__all__ = ["Event", "EventBus", "EventType", "bus", "asdict"]
