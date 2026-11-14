"""Permission manager and human-in-the-loop confirmation.

Two layers:
  1. *Capability* checks — does the settings grant this class of action at all
     (e.g. ``allow_powershell``)?  Denied outright when switched off.
  2. *Confirmation* — the action is permitted but consequences are meaningful,
     so JARVIS asks via the dashboard (or by voice) and waits for a yes/no.

A confirmation never blocks the event loop: callers ``await`` a Future that the
dashboard REST endpoint or the voice layer resolves.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .. import config
from ..event_bus import EventType, bus

log = logging.getLogger(__name__)


class Capability:
    APP_LAUNCH = "allow_app_launch"
    FILE_WRITE = "allow_file_write"
    POWERSHELL = "allow_powershell"
    SCREEN_CAPTURE = "allow_screen_capture"
    POWER = "allow_shutdown"
    NETWORK = "allow_network"
    INPUT_CONTROL = "allow_input_control"
    CLIPBOARD = "allow_clipboard"


SETTING_TO_CAPABILITY = {
    Capability.APP_LAUNCH: "allow_app_launch",
    Capability.FILE_WRITE: "allow_file_write",
    Capability.POWERSHELL: "allow_powershell",
    Capability.SCREEN_CAPTURE: "allow_screen_capture",
    Capability.POWER: "allow_shutdown",
}


@dataclass
class PermissionRequest:
    id: str
    action: str
    description: str
    risk: str = "medium"                 # low | medium | high
    tool: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    future: Optional[asyncio.Future] = None

    def public(self) -> Dict[str, Any]:
        return {
            "id": self.id, "action": self.action, "description": self.description,
            "risk": self.risk, "tool": self.tool, "arguments": self.arguments,
            "created_at": self.created_at,
        }


class PermissionManager:
    def __init__(self, timeout: float = 60.0) -> None:
        self.timeout = timeout
        self._pending: Dict[str, PermissionRequest] = {}
        self._auto_approve: bool = False       # "don't ask again" for this session only
        self._listeners: List[Callable[[PermissionRequest], Any]] = []
        self._grants: List[str] = []

    # -- capabilities -----------------------------------------------------
    @contextmanager
    def one_time_grant(self, capability: str):
        """Allow ``capability`` for the duration of the block only.

        Used after the user approves a disabled capability for a single action:
        the setting stays off, but the tool's own guard must not refuse the very
        action they just authorised.
        """
        self._grants.append(capability)
        try:
            yield
        finally:
            try:
                self._grants.remove(capability)
            except ValueError:
                pass

    def check_capability(self, capability: str) -> bool:
        if capability and capability in self._grants:
            return True
        if not capability:
            return True
        settings = config.get().security
        if capability == Capability.NETWORK:
            return True
        if capability == Capability.INPUT_CONTROL:
            return bool(getattr(settings, "allow_app_launch", True))
        if capability == Capability.CLIPBOARD:
            return bool(getattr(settings, "allow_file_write", True))
        attr = SETTING_TO_CAPABILITY.get(capability, capability)
        return bool(getattr(settings, attr, True))

    # -- confirmation flow ------------------------------------------------
    def add_listener(self, callback: Callable[[PermissionRequest], Any]) -> None:
        """Voice layer subscribes to speak the question out loud."""
        self._listeners.append(callback)

    async def request(self, action: str, description: str, *, tool: str = "",
                      risk: str = "medium", arguments: Optional[Dict[str, Any]] = None,
                      timeout: Optional[float] = None) -> bool:
        if self._auto_approve:
            log.info("Auto-approved (session): %s", action)
            return True

        request = PermissionRequest(
            id=uuid.uuid4().hex[:10], action=action, description=description,
            risk=risk, tool=tool, arguments=arguments or {},
        )
        loop = asyncio.get_running_loop()
        request.future = loop.create_future()
        self._pending[request.id] = request

        bus.publish(EventType.PERMISSION, {"event": "request", **request.public()})
        bus.notify("Confirmation required", description, level="warning")
        for listener in self._listeners:
            try:
                result = listener(request)
                if asyncio.iscoroutine(result):
                    asyncio.create_task(result)
            except Exception:
                log.exception("Permission listener failed")

        try:
            approved = await asyncio.wait_for(request.future, timeout or self.timeout)
            outcome = "approved" if approved else "denied"
            log.info("Permission %s for %s", outcome, action)
            bus.publish(EventType.PERMISSION,
                        {"event": "resolved", "id": request.id, "approved": bool(approved)})
            return bool(approved)
        except asyncio.TimeoutError:
            log.info("Permission request timed out for %s", action)
            bus.publish(EventType.PERMISSION,
                        {"event": "resolved", "id": request.id, "approved": False,
                         "reason": "timeout"})
            bus.notify("Request expired", f"No response for: {description}", level="warning")
            return False
        finally:
            self._pending.pop(request.id, None)

    def resolve(self, request_id: str, approved: bool, remember: bool = False) -> bool:
        if remember and approved:
            self._auto_approve = True
        request = self._pending.get(request_id)
        if not request or not request.future or request.future.done():
            return False
        request.future.set_result(approved)
        return True

    def resolve_any(self, approved: bool) -> bool:
        """Used by voice: "yes" / "no" answers the newest pending request."""
        if not self._pending:
            return False
        request = max(self._pending.values(), key=lambda r: r.created_at)
        return self.resolve(request.id, approved)

    def pending(self) -> List[Dict[str, Any]]:
        return [r.public() for r in self._pending.values()]

    def set_auto_approve(self, value: bool) -> None:
        self._auto_approve = value

    def deny_all(self) -> None:
        for request in list(self._pending.values()):
            if request.future and not request.future.done():
                request.future.set_result(False)
        self._pending.clear()


permissions = PermissionManager()
