"""Countdown timers driven by asyncio tasks with cancel support."""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from ..event_bus import bus

log = logging.getLogger(__name__)


@dataclass
class Timer:
    id: str
    label: str
    seconds: int
    ends_at: datetime
    task: Optional[asyncio.Task] = field(default=None, repr=False)
    cancelled: bool = False

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id, "label": self.label, "seconds": self.seconds,
            "ends_at": self.ends_at.isoformat(timespec="seconds"),
            "remaining": max(0, int((self.ends_at - datetime.now()).total_seconds())),
            "cancelled": self.cancelled,
        }


class TimerManager:
    def __init__(self) -> None:
        self._timers: Dict[str, Timer] = {}
        self._speak: Optional[Callable[[str], Any]] = None

    def attach_speaker(self, speak: Callable[[str], Any]) -> None:
        self._speak = speak

    def set_timer(self, seconds: int, label: str = "Timer") -> Dict[str, Any]:
        seconds = int(seconds)
        if seconds <= 0:
            return {"ok": False, "message": "Timer length must be positive."}
        if seconds > 24 * 3600:
            return {"ok": False, "message": "Timers longer than 24 hours are not supported; "
                                            "use a reminder instead."}
        timer = Timer(id=uuid.uuid4().hex[:8], label=label or "Timer", seconds=seconds,
                      ends_at=datetime.now() + timedelta(seconds=seconds))
        self._timers[timer.id] = timer
        timer.task = asyncio.get_running_loop().create_task(self._countdown(timer))
        bus.publish("timer", {"event": "created", **timer.public()})
        return {"ok": True, "verified": True,
                "message": f"{timer.label} set for {seconds} seconds.",
                "data": timer.public()}

    async def _countdown(self, timer: Timer) -> None:
        try:
            await asyncio.sleep(timer.seconds)
        except asyncio.CancelledError:
            timer.cancelled = True
            return
        if timer.cancelled:
            return
        self._timers.pop(timer.id, None)
        bus.notify("Timer finished", timer.label, level="info")
        bus.publish("timer", {"event": "finished", **timer.public()})
        if self._speak:
            try:
                result = self._speak(f"{timer.label}: time is up, sir.")
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                log.exception("Could not announce timer")

    def cancel(self, timer_id: Optional[str] = None) -> Dict[str, Any]:
        if timer_id:
            timer = self._timers.pop(timer_id, None)
            if not timer:
                return {"ok": False, "message": "No such timer."}
            timer.cancelled = True
            if timer.task:
                timer.task.cancel()
            return {"ok": True, "verified": True, "message": f"Cancelled {timer.label}."}
        count = len(self._timers)
        for timer in list(self._timers.values()):
            timer.cancelled = True
            if timer.task:
                timer.task.cancel()
        self._timers.clear()
        return {"ok": True, "verified": True, "message": f"Cancelled {count} timer(s)."}

    def list(self) -> List[Dict[str, Any]]:
        return [t.public() for t in self._timers.values()]


timer_manager = TimerManager()
