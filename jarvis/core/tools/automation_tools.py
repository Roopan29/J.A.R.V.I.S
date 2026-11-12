"""Task planning / execution and custom-command tools."""
from __future__ import annotations

from ...automation import custom_commands, task_executor
from ...automation.task_queue import task_queue
from ...database.models import ActionResult
from ..tool_registry import ToolSpec, registry


def _to_result(payload) -> ActionResult:
    if isinstance(payload, dict):
        return ActionResult(ok=bool(payload.get("ok", True)),
                            message=payload.get("message", ""),
                            data=payload.get("data", {}),
                            verified=bool(payload.get("verified", False)))
    return ActionResult(ok=True, message=str(payload))


registry.register(ToolSpec(
    name="run_workflow",
    description="Plan and run a multi-step goal, e.g. 'prepare my laptop for programming'. "
                "Steps are queued, visible on the dashboard and cancellable.",
    parameters={"type": "object",
                "properties": {"goal": {"type": "string"},
                               "auto_plan": {"type": "boolean", "default": True}},
                "required": ["goal"]},
    handler=lambda goal, auto_plan=True: _to_result(_goal_sync_adapter(goal, auto_plan)),
    category="automation", risk="medium",
))

registry.register(ToolSpec(
    name="queue_status",
    description="Report queued/running tasks and their progress.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: ActionResult(ok=True, verified=True,
                                 message=_queue_message(),
                                 data={"tasks": task_queue.list(10),
                                       "active": task_queue.active()}),
    category="automation",
))

registry.register(ToolSpec(
    name="cancel_tasks",
    description="Cancel the running task and queued tasks.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: ActionResult(ok=True, verified=True,
                                 message=f"Cancelled {task_queue.cancel_all()} task(s)."),
    category="automation",
))

registry.register(ToolSpec(
    name="create_custom_command",
    description="Create a phrase that triggers a sequence of tool calls, e.g. trigger "
                "'coding mode' with steps opening VS Code and a folder.",
    parameters={"type": "object",
                "properties": {
                    "trigger": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "object"},
                              "description": "[{\"tool\":\"open_application\",\"arguments\":{\"name\":\"code\"}}]"},
                    "description": {"type": "string"},
                },
                "required": ["trigger", "steps"]},
    handler=lambda trigger, steps, description="":
        _to_result(custom_commands.add_command(trigger, steps, description)),
    category="automation", risk="medium", requires_confirmation=True,
    confirmation_template="Create a custom command for '{trigger}'?",
))

registry.register(ToolSpec(
    name="list_custom_commands",
    description="List custom voice commands.",
    parameters={"type": "object", "properties": {}},
    handler=lambda: ActionResult(ok=True, verified=True,
                                 message=f"{len(custom_commands.list_commands())} custom command(s).",
                                 data={"commands": custom_commands.list_commands()}),
    category="automation",
))


def _queue_message() -> str:
    tasks = task_queue.list(10)
    if not tasks:
        return "The task queue is empty."
    active = task_queue.active()
    if active:
        return f"Running '{active['title']}' at {int(active['progress'] * 100)}%; " \
               f"{len(tasks) - 1} more in the queue."
    return f"{len(tasks)} recent task(s), none running."


def _goal_sync_adapter(goal: str, auto_plan: bool) -> dict:
    """The executor is async; the registry awaits coroutines automatically."""
    return task_executor.execute_goal(goal, auto_plan)  # returns a coroutine
