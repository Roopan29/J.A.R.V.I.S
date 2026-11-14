"""Tool registry — the single catalogue of everything JARVIS can do.

Each tool declares a JSON-schema for its arguments, a risk level, the
capability it needs, and whether it requires confirmation. The AI engine
converts these specs into provider-specific tool definitions; callers go
through :meth:`ToolRegistry.invoke` which enforces permissions and audits.
"""
from __future__ import annotations

import inspect
import json
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from ..database.models import ActionResult
from ..event_bus import EventType, bus
from ..security.audit_logger import auditor as audit_logger, BLOCKED, DENIED, ERROR, SUCCESS
from .permissions import PermissionManager, permissions

log = logging.getLogger(__name__)

Handler = Callable[..., Any]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    handler: Optional[Handler] = None
    category: str = "general"
    capability: str = ""
    risk: str = "low"                      # low | medium | high
    requires_confirmation: bool = False
    confirmation_template: str = ""        # e.g. "Delete {path}?"
    dangerous: bool = False
    #: Optional fast validation run *before* asking for confirmation. Returns an
    #: error message to abort with, or None when the call may proceed.
    precheck: Optional[Callable[[Dict[str, Any]], Optional[str]]] = None

    def json_schema(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def public(self) -> Dict[str, Any]:
        return {
            "name": self.name, "description": self.description, "category": self.category,
            "risk": self.risk, "capability": self.capability,
            "requires_confirmation": self.requires_confirmation,
            "parameters": self.parameters,
        }


class ToolRegistry:
    def __init__(self, permission_manager: Optional[PermissionManager] = None) -> None:
        self._tools: Dict[str, ToolSpec] = {}
        self.permissions = permission_manager or permissions
        self._executed: List[Dict[str, Any]] = []

    # -- registration -----------------------------------------------------
    def register(self, spec: ToolSpec) -> ToolSpec:
        if not spec.name:
            raise ValueError("Tool needs a name")
        if spec.name in self._tools:
            log.debug("Overriding tool %s", spec.name)
        self._tools[spec.name] = spec
        return spec

    def tool(self, name: str, description: str, parameters: Optional[Dict[str, Any]] = None,
             **kwargs) -> Callable[[Handler], Handler]:
        """Decorator form: ``@registry.tool("open_app", "...", {...})``."""
        def decorator(handler: Handler) -> Handler:
            self.register(ToolSpec(name=name, description=description,
                                   parameters=parameters or {"type": "object", "properties": {}},
                                   handler=handler, **kwargs))
            return handler
        return decorator

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def all(self) -> List[ToolSpec]:
        return [self._tools[n] for n in sorted(self._tools)]

    def schemas(self) -> List[Dict[str, Any]]:
        return [t.json_schema() for t in self.all()]

    def catalogue(self) -> List[Dict[str, Any]]:
        return [t.public() for t in self.all()]

    def by_category(self) -> Dict[str, List[Dict[str, Any]]]:
        out: Dict[str, List[Dict[str, Any]]] = {}
        for spec in self.all():
            out.setdefault(spec.category, []).append(spec.public())
        return out

    # -- invocation -------------------------------------------------------
    async def invoke(self, name: str, arguments: Optional[Dict[str, Any]] = None,
                     *, source: str = "assistant",
                     skip_confirmation: bool = False) -> ActionResult:
        arguments = arguments or {}
        spec = self._tools.get(name)
        if spec is None:
            return ActionResult(ok=False, message=f"Unknown tool '{name}'.")

        grant = False
        if spec.capability and not self.permissions.check_capability(spec.capability):
            # The capability is switched off, but the user explicitly asked for
            # this action. Rather than dead-ending, offer a one-time approval so
            # "take a screenshot" works without hunting through Settings.
            label = _CAPABILITY_LABELS.get(spec.capability, spec.capability)
            approved = await self.permissions.request(
                action=f"enable:{spec.capability}",
                description=f"Allow '{label}' for this action? "
                            f"({spec.description or name})",
                tool=name, risk="high", arguments=arguments,
            )
            if not approved:
                audit_logger.record(name, arguments, BLOCKED,
                                    f"Capability '{spec.capability}' denied by user")
                bus.log(f"Blocked '{name}': {spec.capability} denied", level="warning")
                return ActionResult(
                    ok=False,
                    message=(f"Permission denied: '{label}' is off. "
                             f"Enable it in Settings → Security, or say yes when asked."))
            # Grant just this one call; the setting itself is unchanged. The
            # user has already answered "yes" for this action, so it must not
            # immediately be asked about again by the tool's own confirmation.
            grant = True
            audit_logger.record(name, arguments, "approved",
                                f"One-time grant for '{spec.capability}'")
            bus.log(f"One-time permission granted for '{label}'", level="warning")

        if grant:
            # Already authorised by the one-time approval above.
            skip_confirmation = True

        if spec.precheck is not None:
            try:
                problem = spec.precheck(arguments)
            except Exception as exc:  # a broken precheck must not block the tool
                log.warning("Precheck for %s failed: %s", name, exc)
                problem = None
            if problem:
                audit_logger.record(name, arguments, BLOCKED, problem)
                bus.log(problem, level="warning", source="tools")
                return ActionResult(ok=False, message=problem)

        if spec.requires_confirmation and not skip_confirmation:
            description = (spec.confirmation_template or
                           f"Run {name} with {json.dumps(arguments)[:160]}?")
            try:
                description = description.format(**arguments)
            except (KeyError, IndexError, ValueError):
                pass
            approved = await self.permissions.request(
                name, description, tool=name, risk=spec.risk, arguments=arguments
            )
            if not approved:
                audit_logger.record(name, arguments, DENIED, "User declined confirmation")
                return ActionResult(ok=False, message="Action cancelled — confirmation declined.",
                                    data={"denied": True})

        bus.publish(EventType.STATE, {"state": "executing", "tool": name})
        bus.log(f"Executing {name}", source="tools")
        started = time.perf_counter()

        try:
            handler = spec.handler
            if handler is None:
                raise RuntimeError(f"Tool '{name}' has no handler")
            # A one-time grant must reach the tool's own capability guard,
            # otherwise power_action etc. would refuse the action the user
            # just approved. The setting itself stays off.
            scope = (self.permissions.one_time_grant(spec.capability)
                     if grant else nullcontext())
            with scope:
                result = handler(**arguments) if not _accepts_extra(handler) else handler(arguments)
                if inspect.isawaitable(result):
                    result = await result
            if isinstance(result, ActionResult):
                action_result = result
            elif isinstance(result, dict):
                action_result = ActionResult(ok=bool(result.get("ok", True)),
                                             message=result.get("message", ""),
                                             data=result.get("data", {}),
                                             verified=bool(result.get("verified", False)))
            else:
                action_result = ActionResult(ok=True, message=str(result))
        except TypeError as exc:
            duration = time.perf_counter() - started
            log.warning("Tool %s argument error: %s", name, exc)
            audit_logger.record(name, arguments, ERROR, f"Bad arguments: {exc}")
            return ActionResult(ok=False, message=f"Invalid arguments for {name}: {exc}")
        except Exception as exc:
            log.exception("Tool %s failed", name)
            audit_logger.record(name, arguments, ERROR, str(exc))
            bus.publish(EventType.ERROR, {"source": name, "message": str(exc)})
            return ActionResult(ok=False, message=f"{name} failed: {exc}")

        duration = time.perf_counter() - started
        outcome = SUCCESS if action_result.ok else ERROR
        audit_logger.record(name, arguments, outcome, action_result.message)
        record = {"tool": name, "arguments": arguments, "ok": action_result.ok,
                  "message": action_result.message, "duration_ms": round(duration * 1000, 1),
                  "verified": action_result.verified, "ts": time.time()}
        self._executed.append(record)
        self._executed = self._executed[-200:]
        bus.log(action_result.message or name, level="info" if action_result.ok else "error",
                source="tools", tool=name)
        bus.publish(EventType.STATE, {"state": "idle", "last_tool": name})
        return action_result

    def recent_executions(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self._executed[-limit:]


def _accepts_extra(handler: Handler) -> bool:
    """Detect handlers declared as ``handler(arguments: dict)``."""
    try:
        params = list(inspect.signature(handler).parameters.values())
    except (TypeError, ValueError):
        return False
    if len(params) == 1 and params[0].name in {"args", "arguments", "kwargs", "payload"}:
        return True
    return False


#: Human-readable names for capability prompts.
_CAPABILITY_LABELS = {
    "allow_screen_capture": "screen capture",
    "allow_powershell": "PowerShell commands",
    "allow_app_launch": "launching applications",
    "allow_file_write": "writing files",
    "allow_shutdown": "shutdown and restart",
    "allow_clipboard": "clipboard access",
    "allow_input_control": "mouse and keyboard control",
    "allow_network": "network access",
}


#: Application-wide registry, populated by core.tools setup.
registry = ToolRegistry()


def load_all_tools() -> ToolRegistry:
    """Import every tool module so their decorators run, then return registry."""
    from . import tools  # noqa: F401  (module import registers everything)
    return registry
