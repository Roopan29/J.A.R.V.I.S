"""Task queue, planner and custom commands."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.automation import custom_commands, task_executor
from jarvis.automation.task_queue import (CANCELLED, DONE, FAILED, QUEUED, RUNNING,
                                          TaskQueue)


@pytest.mark.asyncio
async def test_task_runs_and_reports_progress(registry):
    queue = TaskQueue()
    progress = []

    async def step(context):
        context.progress(0.5, "halfway")
        progress.append(0.5)
        return {"ok": True}

    task = queue.submit("demo", [step, step], kind="test")
    for _ in range(40):
        await asyncio.sleep(0.05)
        current = queue.get(task.task_id)
        if current and current["status"] in (DONE, FAILED, CANCELLED):
            break
    final = queue.get(task.task_id)
    assert final["status"] == DONE
    assert final["progress"] == 1.0
    assert progress == [0.5, 0.5]


@pytest.mark.asyncio
async def test_task_cancellation_is_cooperative_and_immediate(registry):
    queue = TaskQueue()

    async def slow(context):
        await asyncio.sleep(10)
        return {"ok": True}

    task = queue.submit("slow", [slow], kind="test")
    await asyncio.sleep(0.15)
    assert queue.cancel(task.task_id) is True
    await asyncio.sleep(0.15)
    assert queue.get(task.task_id)["status"] == CANCELLED


@pytest.mark.asyncio
async def test_task_retries_then_fails(registry):
    queue = TaskQueue()
    attempts = {"n": 0}

    async def flaky(context):
        attempts["n"] += 1
        raise RuntimeError("boom")

    task = queue.submit("flaky", [flaky], kind="test", max_attempts=2)
    for _ in range(80):
        await asyncio.sleep(0.1)
        current = queue.get(task.task_id)
        if current and current["status"] in (DONE, FAILED, CANCELLED):
            break
    assert queue.get(task.task_id)["status"] == FAILED
    assert attempts["n"] == 2


def test_builtin_workflow_planning():
    plan = task_executor.plan_for_goal("prepare my laptop for programming")
    assert plan is not None
    tools = [step["tool"] for step in plan["steps"]]
    assert "open_application" in tools
    assert all("description" in step for step in plan["steps"])


@pytest.mark.asyncio
async def test_execute_goal_queues_workflow(registry):
    result = await task_executor.execute_goal("coding mode")
    assert result["ok"] is True
    assert result["source"] == "workflow"
    assert result["task"]["status"] in (QUEUED, RUNNING, DONE)


@pytest.mark.asyncio
async def test_llm_planning_reports_offline_honestly(registry, monkeypatch):
    """With no provider reachable the planner must say so, not invent a plan."""
    from jarvis.core import ai_engine as engine_module

    async def offline():
        return {"provider": "none", "online": False,
                "detail": "no model configured", "models": [], "model_available": False}

    monkeypatch.setattr(engine_module.ai_engine, "health", offline)
    result = await task_executor.plan_with_llm("do something vague and unrelated")
    assert result["ok"] is False
    assert "language model" in result["message"].lower()


def test_custom_command_crud(db):
    result = custom_commands.add_command(
        "test mode", [{"tool": "create_note",
                       "arguments": {"title": "from custom command"}}],
        "test command")
    assert result["ok"], result["message"]

    commands = custom_commands.list_commands()
    assert any(c["trigger"] == "test mode" for c in commands)

    found = custom_commands.find("test mode")
    assert found and found["action"]["steps"]

    command_id = found["id"]
    assert custom_commands.toggle_command(command_id, False)["ok"]
    assert custom_commands.find("test mode") is None      # disabled
    assert custom_commands.delete_command(command_id)["ok"]


def test_custom_command_rejects_unknown_tool():
    result = custom_commands.add_command("bad", [{"tool": "not_a_tool"}])
    assert result["ok"] is False
