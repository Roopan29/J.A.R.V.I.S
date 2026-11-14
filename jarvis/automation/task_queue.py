"""Persistent task queue with progress, cancellation, cancellation tokens,
retry/backoff and full history.

Runs on a dedicated asyncio worker so the HTTP API never blocks.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

from ..database.database import db
from ..event_bus import EventType, bus

log = logging.getLogger(__name__)

QUEUED = "queued"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"

Step = Callable[["TaskContext"], Awaitable[Dict[str, Any]]]


@dataclass
class TaskContext:
    """Passed to each step; supports cooperative cancellation."""
    task_id: str
    cancel_event: asyncio.Event
    progress_callback: Callable[[float, str], None]

    @property
    def cancelled(self) -> bool:
        return self.cancel_event.is_set()

    def check_cancelled(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError(f"Task {self.task_id} cancelled")

    def progress(self, value: float, detail: str = "") -> None:
        self.progress_callback(max(0.0, min(1.0, value)), detail)


@dataclass
class QueuedTask:
    task_id: str
    title: str
    kind: str = "plan"
    payload: Dict[str, Any] = field(default_factory=dict)
    steps: List[Step] = field(default_factory=list)
    status: str = QUEUED
    progress: float = 0.0
    detail: str = ""
    attempts: int = 0
    max_attempts: int = 2
    result: Dict[str, Any] = field(default_factory=dict)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    created_at: float = field(default_factory=time.time)

    def public(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id, "title": self.title, "kind": self.kind,
            "status": self.status, "progress": round(self.progress, 3),
            "detail": self.detail, "attempts": self.attempts,
            "result": self.result, "payload": self.payload,
            "created_at": self.created_at,
        }


class TaskQueue:
    def __init__(self) -> None:
        self._tasks: Dict[str, QueuedTask] = {}
        self._order: List[str] = []
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._worker: Optional[asyncio.Task] = None
        self._running_task: Optional[QueuedTask] = None
        self._runner_future: Optional[asyncio.Task] = None

    # -- lifecycle --------------------------------------------------------
    def ensure_worker(self) -> None:
        """Start the worker on the running loop.

        ``asyncio.get_event_loop()`` is deprecated and raises when no loop is
        running on Python 3.14, so require a running loop explicitly.
        """
        if self._worker is not None and not self._worker.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            log.debug("Task queue worker deferred: no running event loop")
            return
        self._worker = loop.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        while True:
            task_id = await self._queue.get()
            cancelled = False
            try:
                task = self._tasks.get(task_id)
                if task is not None and task.status == QUEUED:
                    await self._execute(task)
            except asyncio.CancelledError:
                cancelled = True
                raise
            finally:
                if not cancelled:
                    self._queue.task_done()

    # -- submission -------------------------------------------------------
    def submit(self, title: str, steps: List[Step], *, kind: str = "plan",
               payload: Optional[Dict[str, Any]] = None,
               max_attempts: int = 2) -> QueuedTask:
        task = QueuedTask(task_id=uuid.uuid4().hex[:10], title=title, kind=kind,
                          payload=payload or {}, steps=steps, max_attempts=max_attempts)
        self._tasks[task.task_id] = task
        self._order.append(task.task_id)
        db.upsert_task(task.task_id, title, kind, QUEUED, 0, "Queued", payload)
        self.ensure_worker()
        self._queue.put_nowait(task.task_id)
        bus.publish(EventType.TASK, task.public())
        bus.log(f"Task queued: {title}", source="tasks")
        return task

    # -- execution --------------------------------------------------------
    async def shutdown(self) -> None:
        """Stop the worker cleanly (called on application exit)."""
        self.cancel_all()
        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except (asyncio.CancelledError, Exception):
                pass
        self._worker = None

    async def _execute(self, task: QueuedTask) -> None:
        if task.cancel_event.is_set():
            task.status = CANCELLED
            self._persist(task)
            return
        task.status = RUNNING
        task.attempts += 1
        self._running_task = task
        self._persist(task)
        bus.publish(EventType.STATE, {"state": "executing", "task": task.title})

        def report(value: float, detail: str) -> None:
            task.progress = value
            task.detail = detail
            self._persist(task)
            bus.publish(EventType.TASK, task.public())

        context = TaskContext(task_id=task.task_id, cancel_event=task.cancel_event,
                              progress_callback=report)
        results: List[Dict[str, Any]] = []
        self._runner_future = asyncio.current_task()
        try:
            for index, step in enumerate(task.steps):
                context.check_cancelled()
                report(index / max(1, len(task.steps)), f"Step {index + 1}/{len(task.steps)}")
                outcome = await step(context)
                results.append(outcome or {})
                if outcome and outcome.get("ok") is False and outcome.get("fatal"):
                    raise RuntimeError(outcome.get("message", "Step failed"))
            task.status = DONE
            task.progress = 1.0
            task.detail = "Completed"
            task.result = {"steps": results}
            bus.notify("Task complete", task.title, level="info")
        except asyncio.CancelledError:
            task.status = CANCELLED
            task.detail = "Cancelled (shutdown)"
            bus.publish(EventType.TASK, task.public())
            raise          # never swallow: asyncio shutdown must complete
        except Exception as exc:
            log.exception("Task failed: %s", task.title)
            task.result = {"steps": results, "error": str(exc)}
            if task.attempts < task.max_attempts:
                task.status = QUEUED
                task.detail = f"Retrying after error: {exc}"
                self._persist(task)
                bus.publish(EventType.TASK, task.public())
                await asyncio.sleep(min(2 ** task.attempts, 5))
                self._queue.put_nowait(task.task_id)
                self._running_task = None
                return
            task.status = FAILED
            task.detail = str(exc)[:300]
            bus.notify("Task failed", f"{task.title}: {exc}", level="error")
        finally:
            self._running_task = None
            self._persist(task)
            bus.publish(EventType.TASK, task.public())
            bus.publish(EventType.STATE, {"state": "idle"})

    def _persist(self, task: QueuedTask) -> None:
        db.upsert_task(task.task_id, task.title, task.kind, task.status,
                       task.progress, task.detail, task.payload, task.attempts)

    # -- control ----------------------------------------------------------
    def cancel(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if not task:
            return False
        task.cancel_event.set()
        if self._running_task is task and self._runner_future is not None:
            self._runner_future.cancel()
        task.status = CANCELLED
        task.detail = "Cancelled by user"
        self._persist(task)
        bus.publish(EventType.TASK, task.public())
        return True

    def cancel_all(self) -> int:
        count = 0
        for task in self._tasks.values():
            if task.status in (QUEUED, RUNNING):
                self.cancel(task.task_id)
                count += 1
        return count

    def cancel_running(self) -> bool:
        if self._running_task:
            return self.cancel(self._running_task.task_id)
        return False

    # -- introspection ----------------------------------------------------
    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        task = self._tasks.get(task_id)
        return task.public() if task else None

    def list(self, limit: int = 30) -> List[Dict[str, Any]]:
        ids = self._order[-limit:]
        return [self._tasks[i].public() for i in ids if i in self._tasks]

    def active(self) -> Optional[Dict[str, Any]]:
        return self._running_task.public() if self._running_task else None


#: Application-wide queue. Named ``tasks`` so it does not shadow this module.
tasks = TaskQueue()
