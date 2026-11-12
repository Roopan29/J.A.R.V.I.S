"""Task planning and multi-step workflow definitions.

A *plan* is an ordered list of tool calls with human-readable descriptions.
This module turns natural-language goals into plans (deterministically for
known workflows, via the LLM for arbitrary ones) and executes them through
the task queue so progress, cancellation and retries all apply.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from ..core.ai_engine import ai_engine
from ..core.tool_registry import registry
from ..event_bus import bus
from .task_queue import TaskContext, tasks as task_queue

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Built-in workflows
# ---------------------------------------------------------------------------
def coding_mode_steps(project_path: Optional[str] = None,
                      open_browser: bool = False,
                      browser_url: str = "https://github.com") -> List[Dict[str, Any]]:
    steps: List[Dict[str, Any]] = [
        {"tool": "system_status", "arguments": {"detail": "all"},
         "description": "Check system status"},
        {"tool": "open_application", "arguments": {"name": "code"},
         "description": "Open Visual Studio Code"},
    ]
    if project_path:
        steps.append({"tool": "open_path", "arguments": {"path": project_path},
                      "description": f"Open project folder {project_path}"})
    if open_browser:
        steps.append({"tool": "open_url", "arguments": {"url": browser_url},
                      "description": "Open the browser"})
    steps.append({"tool": "focus_application", "arguments": {"name": "Visual Studio Code"},
                  "description": "Bring VS Code to the front"})
    return steps


WORKFLOWS: Dict[str, Any] = {
    "coding mode": coding_mode_steps,
    "prepare for programming": coding_mode_steps,
    "focus mode": lambda **_: [
        {"tool": "minimize_all_windows", "arguments": {}, "description": "Clear the desktop"},
        {"tool": "set_volume", "arguments": {"level": 20}, "description": "Lower volume"},
    ],
    "wrap up": lambda **_: [
        {"tool": "daily_summary", "arguments": {}, "description": "Summarise the day"},
        {"tool": "close_application", "arguments": {"name": "code"},
         "description": "Close VS Code"},
    ],
}


def _normalise(steps: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ensure each step has a description; drop platform-specific steps off-Windows."""
    from ..computer.platform_support import support

    out: List[Dict[str, Any]] = []
    for step in steps:
        tool = step.get("tool")
        if not tool:
            continue
        if not support.is_windows and tool in {
            "focus_application", "minimize_all_windows", "switch_window",
            "lock_workstation", "set_brightness",
        }:
            log.info("Skipping Windows-only step '%s' on %s", tool, support.system)
            continue
        out.append({**step, "description": step.get("description") or tool})
    return out


def plan_for_goal(goal: str) -> Optional[Dict[str, Any]]:
    """Match a goal to a built-in workflow.

    Words like "programming", "developer" and "coding" all map to coding mode,
    so "prepare my laptop for programming" works without an LLM round-trip.
    """
    lowered = (goal or "").lower()
    alias = {
        "coding mode": "coding mode", "programming": "coding mode",
        "developer": "coding mode", "develop": "coding mode",
        "prepare for coding": "coding mode",
        "focus mode": "focus mode", "deep work": "focus mode",
        "wrap up": "wrap up", "end of day": "wrap up", "finish for the day": "wrap up",
    }
    for phrase, key in alias.items():
        if phrase in lowered:
            return {"name": key, "steps": _normalise(WORKFLOWS[key]())}
    for key, builder in WORKFLOWS.items():
        if key in lowered:
            return {"name": key, "steps": _normalise(builder())}
    return None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def _make_step(spec: Dict[str, Any], index: int, total: int):
    async def step(context: TaskContext) -> Dict[str, Any]:
        context.check_cancelled()
        tool = spec.get("tool")
        arguments = spec.get("arguments") or {}
        context.progress((index + 0.5) / max(1, total),
                         spec.get("description") or tool or "step")
        result = await registry.invoke(tool, arguments, source="plan")
        return {"tool": tool, "ok": result.ok, "message": result.message,
                "data": result.data}
    return step


def submit_plan(title: str, steps: List[Dict[str, Any]], kind: str = "plan"):
    handlers = [_make_step(step, i, len(steps)) for i, step in enumerate(steps)]
    return task_queue.submit(title, handlers, kind=kind, payload={"steps": steps})


async def plan_with_llm(goal: str, max_steps: int = 6) -> Dict[str, Any]:
    """Ask the model to decompose an arbitrary goal into tool calls."""
    provider = ai_engine.provider
    health = await ai_engine.health()
    if provider.name == "none" or not health.get("online"):
        return {"ok": False, "steps": [],
                "message": ("I need a language model to plan that, sir — "
                            + str(health.get("detail", "no provider is reachable"))
                            + " Built-in workflows still work, for example "
                              "\"prepare my laptop for programming\".")}
    tools = registry.catalogue()
    tool_lines = "\n".join(f"- {t['name']}: {t['description']}" for t in tools)
    prompt = (
        "You are a planner for a desktop assistant. Break the user's goal into at most "
        f"{max_steps} concrete steps, using ONLY the tools listed below. Respond with JSON "
        'only: {"steps":[{"tool":"name","arguments":{...},"description":"..."}]}\n\n'
        f"TOOLS:\n{tool_lines}\n\nGOAL: {goal}"
    )
    result = await ai_engine.chat([
        {"role": "system", "content": "You output strictly valid JSON. No prose."},
        {"role": "user", "content": prompt},
    ])
    text = (result.content or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {"ok": False, "message": "The model did not return a usable plan.", "steps": []}
    try:
        payload = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        return {"ok": False, "message": f"Plan JSON invalid: {exc}", "steps": []}
    steps = _normalise([s for s in payload.get("steps", [])
                        if isinstance(s, dict) and s.get("tool")])
    for step in steps:
        if registry.get(step["tool"]) is None:
            return {"ok": False, "message": f"Plan used unknown tool '{step['tool']}'.",
                    "steps": []}
    return {"ok": bool(steps), "steps": steps,
            "message": f"Plan with {len(steps)} step(s)."}


async def execute_goal(goal: str, auto_plan: bool = True) -> Dict[str, Any]:
    """Plan (if needed) and queue execution. Returns the queued task info."""
    builtin = plan_for_goal(goal)
    if builtin:
        task = submit_plan(builtin["name"].title(), builtin["steps"])
        return {"ok": True, "source": "workflow", "steps": builtin["steps"],
                "task": task.public()}
    if not auto_plan:
        return {"ok": False, "message": "No matching workflow for that goal.", "steps": []}
    plan = await plan_with_llm(goal)
    if not plan["ok"]:
        return {"ok": False, "message": plan["message"], "steps": plan.get("steps", [])}
    task = submit_plan(goal[:80], plan["steps"])
    bus.log(f"Planned {len(plan['steps'])} step(s) for: {goal}", source="planner")
    return {"ok": True, "source": "llm", "steps": plan["steps"], "task": task.public()}
