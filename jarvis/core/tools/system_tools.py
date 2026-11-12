"""System monitoring, power and PowerShell tools."""
from __future__ import annotations

from ...computer import powershell, system_control
from ...computer.platform_support import NotSupported
from ...database.models import ActionResult
from ...security.command_validator import validator
from ..permissions import Capability
from ..tool_registry import ToolSpec, registry


def _wrap(fn, *args, **kwargs) -> ActionResult:
    try:
        result = fn(*args, **kwargs)
    except NotSupported as exc:
        return ActionResult(ok=False, message=str(exc))
    except (OSError, ValueError, RuntimeError) as exc:
        return ActionResult(ok=False, message=f"{type(exc).__name__}: {exc}")
    if isinstance(result, dict):
        return ActionResult(ok=bool(result.get("ok", True)),
                            message=result.get("message", ""),
                            data=result.get("data", {}),
                            verified=bool(result.get("verified", False)))
    return ActionResult(ok=True, message=str(result))


registry.register(ToolSpec(
    name="system_status",
    description="Report real CPU, RAM, GPU, battery, disk, network and uptime status.",
    parameters={"type": "object",
                "properties": {"detail": {"type": "string",
                                          "enum": ["all", "cpu", "memory", "gpu", "battery", "disk"],
                                          "default": "all"}}},
    handler=lambda detail="all": _wrap(system_control.system_status, detail),
    category="system",
))

registry.register(ToolSpec(
    name="list_processes",
    description="List the top processes by CPU or memory usage.",
    parameters={"type": "object",
                "properties": {"limit": {"type": "integer", "default": 15},
                               "sort_by": {"type": "string", "enum": ["memory", "cpu"],
                                           "default": "memory"}}},
    handler=lambda limit=15, sort_by="memory": _wrap(system_control.list_processes, limit, sort_by),
    category="system",
))

registry.register(ToolSpec(
    name="network_info",
    description="Report network interfaces, IP addresses and connectivity.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(lambda: {"ok": True, "verified": True,
                                   "message": _network_message(),
                                   "data": system_control.network_info()}),
    category="system",
))


def _network_message() -> str:
    info = system_control.network_info()
    return (f"Local IP {info['local_ip']}, "
            f"{'online' if info['online'] else 'offline'}, "
            f"sent {info['bytes_sent_mb']} MB / received {info['bytes_recv_mb']} MB.")


registry.register(ToolSpec(
    name="set_volume",
    description="Set or mute the system volume.",
    parameters={"type": "object",
                "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100},
                               "mute": {"type": "boolean"},
                               "delta": {"type": "integer"}}},
    handler=lambda level=None, mute=None, delta=None:
        _wrap(system_control.set_volume, level, mute, delta),
    category="system",
))

registry.register(ToolSpec(
    name="set_brightness",
    description="Set display brightness (internal laptop panels only).",
    parameters={"type": "object",
                "properties": {"level": {"type": "integer", "minimum": 0, "maximum": 100}},
                "required": ["level"]},
    handler=lambda level: _wrap(system_control.set_brightness, level),
    category="system",
))

registry.register(ToolSpec(
    name="lock_workstation",
    description="Lock the Windows workstation.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(system_control.lock_workstation),
    category="system",
))

registry.register(ToolSpec(
    name="power_action",
    description="Shut down, restart, sleep or hibernate the computer. Always asks first.",
    parameters={"type": "object",
                "properties": {"action": {"type": "string",
                                          "enum": ["shutdown", "restart", "sleep", "hibernate", "abort"]}},
                "required": ["action"]},
    handler=lambda action: _wrap(system_control.power_action, action),
    category="system", capability=Capability.POWER, risk="high",
    requires_confirmation=True, dangerous=True,
    confirmation_template="{action} the computer now?",
))

registry.register(ToolSpec(
    name="open_task_manager",
    description="Open Windows Task Manager.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(system_control.open_task_manager),
    category="system",
))

registry.register(ToolSpec(
    name="run_powershell",
    description="Run a PowerShell command after safety validation. Blocked commands are "
                "refused; risky ones require confirmation. Never elevated.",
    parameters={"type": "object",
                "properties": {
                    "command": {"type": "string", "description": "PowerShell command"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"]},
    handler=lambda command, timeout=30: _run_powershell(command, timeout),
    category="system", capability=Capability.POWERSHELL, risk="high",
    requires_confirmation=True,
    confirmation_template="Run this PowerShell command?\n{command}",
))


def _run_powershell(command: str, timeout: int = 30) -> ActionResult:
    validation = validator.validate_powershell(command)
    if not validation.allowed:
        return ActionResult(ok=False, message="; ".join(validation.reasons),
                            data=validation.to_dict())
    try:
        result = powershell.run(command, timeout)
    except NotSupported as exc:
        return ActionResult(ok=False, message=str(exc))
    return ActionResult(ok=bool(result.get("ok")), message=result.get("message", ""),
                        data=result.get("data", {}), verified=True)


registry.register(ToolSpec(
    name="health_check",
    description="Run proactive health checks (battery, CPU, memory, disk).",
    parameters={"type": "object", "properties": {}},
    handler=lambda: _wrap(lambda: _health()),
    category="system",
))


def _health() -> ActionResult:
    warnings = system_control.health_warnings()
    if not warnings:
        return ActionResult(ok=True, verified=True, message="All systems nominal, sir.")
    message = "; ".join(f"{w['title']}: {w['message']}" for w in warnings)
    return ActionResult(ok=True, verified=True, message=message, data={"warnings": warnings})
