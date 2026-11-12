"""Permission enforcement: capability gates, confirmation flow, timeouts."""
from __future__ import annotations

import asyncio

import pytest

from jarvis import config
from jarvis.core.permissions import Capability, PermissionManager
from jarvis.core.tool_registry import ToolRegistry, ToolSpec
from jarvis.database.models import ActionResult


def test_capability_respects_settings():
    manager = PermissionManager()
    settings = config.get()
    original = settings.security.allow_powershell
    try:
        settings.security.allow_powershell = False
        assert manager.check_capability(Capability.POWERSHELL) is False
        settings.security.allow_powershell = True
        assert manager.check_capability(Capability.POWERSHELL) is True
        # Unknown capabilities are allowed (they have no gate)
        assert manager.check_capability("") is True
    finally:
        settings.security.allow_powershell = original


@pytest.mark.asyncio
async def test_disabled_capability_blocks_tool():
    settings = config.get()
    original = settings.security.allow_screen_capture
    try:
        settings.security.allow_screen_capture = False
        registry = ToolRegistry(PermissionManager())
        registry.register(ToolSpec(
            name="needs_screen", description="test",
            handler=lambda: ActionResult(ok=True, message="should not run"),
            capability=Capability.SCREEN_CAPTURE,
        ))
        result = await registry.invoke("needs_screen", {})
        assert result.ok is False
        assert "disabled" in result.message.lower() or "denied" in result.message.lower()
    finally:
        settings.security.allow_screen_capture = original


@pytest.mark.asyncio
async def test_confirmation_approved_runs_handler():
    manager = PermissionManager(timeout=5)
    registry = ToolRegistry(manager)
    registry.register(ToolSpec(
        name="risky", description="test", handler=lambda: ActionResult(ok=True, message="ran"),
        requires_confirmation=True, risk="high",
    ))

    async def approve_soon():
        await asyncio.sleep(0.05)
        assert manager.resolve_any(True) is True

    asyncio.create_task(approve_soon())
    result = await registry.invoke("risky", {}, skip_confirmation=False)
    assert result.ok and result.message == "ran"


@pytest.mark.asyncio
async def test_confirmation_denied_blocks_handler():
    manager = PermissionManager(timeout=5)
    registry = ToolRegistry(manager)
    calls = []
    registry.register(ToolSpec(
        name="risky2", description="test",
        handler=lambda: calls.append(1) or ActionResult(ok=True),
        requires_confirmation=True,
    ))

    async def deny_soon():
        await asyncio.sleep(0.05)
        manager.resolve_any(False)

    asyncio.create_task(deny_soon())
    result = await registry.invoke("risky2", {})
    assert result.ok is False
    assert calls == [], "handler must not run when confirmation is denied"


@pytest.mark.asyncio
async def test_confirmation_timeout_denies():
    manager = PermissionManager(timeout=0.2)
    registry = ToolRegistry(manager)
    registry.register(ToolSpec(name="slow", description="t",
                               handler=lambda: ActionResult(ok=True),
                               requires_confirmation=True))
    result = await registry.invoke("slow", {})
    assert result.ok is False


@pytest.mark.asyncio
async def test_auto_approve_after_remember():
    manager = PermissionManager(timeout=5)
    registry = ToolRegistry(manager)
    registry.register(ToolSpec(name="remembered", description="t",
                               handler=lambda: ActionResult(ok=True, message="ok"),
                               requires_confirmation=True))

    async def approve_with_remember():
        await asyncio.sleep(0.05)
        request = manager.pending()[0]
        manager.resolve(request["id"], True, remember=True)

    asyncio.create_task(approve_with_remember())
    first = await registry.invoke("remembered", {})
    second = await registry.invoke("remembered", {})   # no second prompt
    assert first.ok and second.ok


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected():
    registry = ToolRegistry(PermissionManager())
    result = await registry.invoke("does_not_exist", {})
    assert result.ok is False
    assert "unknown tool" in result.message.lower()
