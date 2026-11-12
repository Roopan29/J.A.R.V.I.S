"""REST endpoints. Every dashboard control maps to one of these."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from .. import __version__, config
from ..automation import custom_commands, task_executor
from ..automation.task_queue import task_queue
from ..computer import app_control, file_manager, powershell, screen_vision, system_control
from ..computer.platform_support import NotSupported, support
from ..core.ai_engine import ai_engine
from ..core.conversation import conversation
from ..core.permissions import permissions
from ..core.tool_registry import registry
from ..database.database import db
from ..event_bus import EventType, bus
from ..intelligence import memory, web_search
from ..logging_setup import tail
from ..productivity import notes, reminders, timers
from ..security.secrets import provider_status, set_secret
from ..voice.speech_output import available_engines, speech
from ..voice.speech_recognition import list_input_devices, list_output_devices
from ..voice.voice_manager import voice_manager
from .schemas import (CommandRequest, CustomCommandCreate, FileAction, LaunchRequest,
                      MemoryCreate, NoteCreate, NoteUpdate, PermissionResponse,
                      ReminderCreate, SettingsPatch, SpeakRequest, TodoCreate, ToolInvoke)

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _ok(data: Any = None, message: str = "") -> Dict[str, Any]:
    return {"ok": True, "message": message, "data": data}


# ---------------------------------------------------------------------------
# Status / health
# ---------------------------------------------------------------------------
@router.get("/health")
async def health() -> Dict[str, Any]:
    ai_health = await ai_engine.health()
    return _ok({
        "version": __version__,
        "assistant": config.get().assistant_name,
        "ai": ai_health,
        "voice": voice_manager.status(),
        "speech": speech.status(),
        "support": support.summary(),
        "platform": support.system,
        "tools": len(registry.all()),
        "memory_enabled": config.get().memory_enabled,
        "uptime": system_control.uptime(),
    })


@router.get("/metrics")
async def metrics() -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    snapshot = await loop.run_in_executor(None, system_control.full_snapshot)
    return _ok(snapshot)


@router.get("/events")
async def events(limit: int = 100) -> Dict[str, Any]:
    return _ok(bus.history(limit))


@router.get("/logs")
async def logs(limit: int = 200) -> Dict[str, Any]:
    return _ok({"lines": [line.rstrip() for line in tail(limit)]})


@router.get("/tools")
async def tools() -> Dict[str, Any]:
    return _ok({"tools": registry.catalogue(), "categories": registry.by_category()})


# ---------------------------------------------------------------------------
# Conversation
# ---------------------------------------------------------------------------
@router.post("/command")
async def command(payload: CommandRequest) -> Dict[str, Any]:
    reply = await conversation.handle(payload.text, speak=payload.speak,
                                      source="dashboard", session_id=payload.session_id)
    return _ok(reply.to_dict())


@router.get("/conversation")
async def conversation_history(limit: int = 50) -> Dict[str, Any]:
    return _ok({"turns": db.recent_turns(conversation.session_id, limit),
                "session_id": conversation.session_id})


@router.post("/conversation/clear")
async def clear_conversation() -> Dict[str, Any]:
    removed = db.clear_conversation()
    conversation.reset()
    return _ok({"removed": removed}, "Conversation history cleared.")


@router.post("/emergency-stop")
async def emergency_stop() -> Dict[str, Any]:
    """Stop listening, stop speaking, cancel tasks, deny pending approvals."""
    voice_manager.stop()
    speech.stop()
    permissions.deny_all()
    cancelled = task_queue.cancel_all()
    conversation.cancel()
    bus.notify("Emergency stop", "All activity halted.", level="warning")
    return _ok({"tasks_cancelled": cancelled}, "Emergency stop engaged.")


# ---------------------------------------------------------------------------
# Voice
# ---------------------------------------------------------------------------
@router.get("/voice/status")
async def voice_status() -> Dict[str, Any]:
    return _ok(voice_manager.status())


@router.post("/voice/start")
async def voice_start(mode: str = Query("wake", pattern="^(wake|push_to_talk|continuous)$")
                      ) -> Dict[str, Any]:
    result = voice_manager.start(mode)
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    return _ok(result.get("data"), result.get("message", ""))


@router.post("/voice/stop")
async def voice_stop() -> Dict[str, Any]:
    return _ok(None, voice_manager.stop()["message"])


@router.post("/voice/push-to-talk")
async def voice_ptt() -> Dict[str, Any]:
    result = voice_manager.push_to_talk()
    if not result.get("ok"):
        raise HTTPException(status_code=409, detail=result.get("message"))
    return _ok(None, result["message"])


@router.post("/voice/speak")
async def voice_speak(payload: SpeakRequest) -> Dict[str, Any]:
    if speech.engine == "none":
        raise HTTPException(status_code=409, detail=speech.last_error or "No speech engine")
    speech.speak_async(payload.text)
    return _ok(None, "Speaking.")


@router.post("/voice/stop-speaking")
async def voice_stop_speaking() -> Dict[str, Any]:
    speech.stop()
    return _ok(None, "Speech stopped.")


@router.get("/audio/devices")
async def audio_devices() -> Dict[str, Any]:
    return _ok({"input": list_input_devices(), "output": list_output_devices(),
                "engines": available_engines()})


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
@router.get("/permissions/pending")
async def pending_permissions() -> Dict[str, Any]:
    return _ok({"requests": permissions.pending()})


@router.post("/permissions/{request_id}")
async def resolve_permission(request_id: str, payload: PermissionResponse) -> Dict[str, Any]:
    resolved = permissions.resolve(request_id, payload.approved, payload.remember)
    if not resolved:
        raise HTTPException(status_code=404, detail="No such pending request (it may have expired).")
    return _ok({"approved": payload.approved})


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
@router.get("/settings")
async def get_settings() -> Dict[str, Any]:
    settings = config.get()
    data = settings.to_dict()
    # never leak secrets to the browser
    data["secrets"] = {
        "openai": provider_status("openai"),
        "anthropic": provider_status("anthropic"),
        "brave": {"configured": bool(config.secret("BRAVE_SEARCH_API_KEY"))},
    }
    return _ok(data)


@router.put("/settings")
async def update_settings(payload: SettingsPatch) -> Dict[str, Any]:
    settings = config.get()
    if payload.section:
        if payload.section not in settings.to_dict():
            raise HTTPException(status_code=400, detail=f"Unknown section '{payload.section}'")
        settings.update_section(payload.section, payload.values)
    else:
        for key, value in payload.values.items():
            if hasattr(settings, key) and not isinstance(getattr(settings, key), dict):
                setattr(settings, key, value)
    config.save(settings)
    conversation.set_memory_enabled(settings.memory_enabled)
    ai_engine.refresh()
    bus.publish(EventType.SETTINGS, {"section": payload.section, "values": payload.values})
    return _ok(settings.to_dict(), "Settings saved.")


@router.post("/settings/secret")
async def store_secret(name: str = Query(...), value: str = Query(...)) -> Dict[str, Any]:
    try:
        set_secret(name.strip().upper(), value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    ai_engine.refresh()
    return _ok(None, f"Stored {name.upper()} securely.")


@router.post("/settings/startup")
async def set_startup(enabled: bool = True) -> Dict[str, Any]:
    """Register/remove JARVIS in the per-user Windows Run key (HKCU, no admin)."""
    settings = config.get()
    settings.startup.launch_at_login = enabled
    config.save(settings)
    if not support.is_windows:
        return _ok({"enabled": enabled},
                   "Startup entry saved, but automatic launch only applies on Windows.")
    import subprocess
    from pathlib import Path as _Path
    script = _Path(__file__).resolve().parent.parent.parent / "scripts" / "start_jarvis.bat"
    command = f'"{script}" --minimised'
    key = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run"
    try:
        if enabled:
            subprocess.run(["reg", "add", key, "/v", "JARVIS", "/t", "REG_SZ",
                            "/d", command, "/f"], check=True, capture_output=True, timeout=15)
            return _ok({"enabled": True}, "JARVIS will launch at Windows login.")
        subprocess.run(["reg", "delete", key, "/v", "JARVIS", "/f"],
                       check=False, capture_output=True, timeout=15)
        return _ok({"enabled": False}, "Startup entry removed.")
    except (subprocess.SubprocessError, OSError) as exc:
        raise HTTPException(status_code=500, detail=f"Registry update failed: {exc}")


@router.post("/settings/reset")
async def reset_settings() -> Dict[str, Any]:
    settings = config.reset()
    ai_engine.refresh()
    return _ok(settings.to_dict(), "Settings restored to defaults.")


# ---------------------------------------------------------------------------
# AI
# ---------------------------------------------------------------------------
@router.get("/ai/health")
async def ai_health() -> Dict[str, Any]:
    return _ok(await ai_engine.health())


@router.get("/ai/models")
async def ai_models() -> Dict[str, Any]:
    return _ok({"models": await ai_engine.list_models(), "current": config.get().ai.model})


# ---------------------------------------------------------------------------
# Applications / system
# ---------------------------------------------------------------------------
@router.get("/apps")
async def list_apps() -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    installed = await loop.run_in_executor(None, app_control.installed_apps)
    running = app_control.running_applications(30)
    return _ok({"installed": installed, "running": running})


@router.post("/apps/launch")
async def launch_app(payload: LaunchRequest) -> Dict[str, Any]:
    result = await registry.invoke("open_application",
                                   {"name": payload.name, "arguments": payload.arguments})
    return _ok(result.to_dict(), result.message)


@router.post("/apps/close")
async def close_app(payload: LaunchRequest) -> Dict[str, Any]:
    result = await registry.invoke("close_application", {"name": payload.name})
    return _ok(result.to_dict(), result.message)


@router.get("/system")
async def system_endpoint() -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    snapshot = await loop.run_in_executor(None, system_control.full_snapshot)
    snapshot["processes"] = system_control.list_processes(10)["data"]["processes"]
    snapshot["windows"] = []
    if support.is_windows:
        try:
            from ..computer.window_manager import list_windows
            snapshot["windows"] = list_windows()[:12]
        except Exception:
            pass
    return _ok(snapshot)


@router.post("/system/volume")
async def system_volume(level: Optional[int] = None, mute: Optional[bool] = None
                        ) -> Dict[str, Any]:
    result = await registry.invoke("set_volume", {"level": level, "mute": mute})
    return _ok(result.to_dict(), result.message)


@router.api_route("/system/powershell", methods=["GET", "POST"])
async def run_ps(command: str = Query(..., max_length=2000)) -> Dict[str, Any]:
    from ..security.command_validator import validator
    validation = validator.validate_powershell(command)
    if not validation.allowed:
        raise HTTPException(status_code=403, detail="; ".join(validation.reasons))
    if validation.needs_confirmation:
        approved = await permissions.request(
            "powershell", f"Run PowerShell: {command[:120]}", tool="run_powershell",
            risk="high", arguments={"command": command})
        if not approved:
            raise HTTPException(status_code=403, detail="Confirmation declined.")
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, powershell.run, command)
    return _ok(result, result.get("message", ""))


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
@router.get("/files/list")
async def files_list(path: str = Query("~")) -> Dict[str, Any]:
    try:
        target = file_manager.resolve(path)
        if not target.is_dir():
            raise HTTPException(status_code=400, detail="Not a directory")
        entries = []
        for item in sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:300]:
            try:
                stat = item.stat()
            except OSError:
                continue
            entries.append({
                "name": item.name, "path": str(item), "is_dir": item.is_dir(),
                "size_kb": round(stat.st_size / 1024, 1) if item.is_file() else None,
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
            })
        return _ok({"path": str(target), "parent": str(target.parent), "entries": entries})
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/files/search")
async def files_search(q: str, root: Optional[str] = None, limit: int = 25) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, file_manager.search_files, q, root, None, limit)
    return _ok(result.get("data", {}), result.get("message", ""))


@router.post("/files/action")
async def files_action(payload: FileAction) -> Dict[str, Any]:
    op = payload.operation
    paths = payload.paths
    if op == "open":
        result = await registry.invoke("open_path", {"path": paths.get("path", "")})
    elif op == "create_folder":
        result = await registry.invoke("create_folder",
                                       {"path": paths.get("path", ""),
                                        "name": paths.get("name") or None})
    elif op == "create_file":
        result = await registry.invoke("create_file",
                                       {"path": paths.get("path", ""),
                                        "name": paths.get("name") or None,
                                        "content": payload.options.get("content", "")})
    elif op == "rename":
        result = await registry.invoke("rename_path",
                                       {"path": paths.get("path", ""),
                                        "new_name": paths.get("new_name", "")})
    elif op == "delete":
        result = await registry.invoke("delete_path",
                                       {"path": paths.get("path", ""),
                                        "permanent": bool(payload.options.get("permanent"))})
    elif op == "organise_downloads":
        result = await registry.invoke("organise_downloads",
                                       {"by": payload.options.get("by", "type"),
                                        "dry_run": bool(payload.options.get("dry_run", True))})
    elif op == "duplicates":
        result = await registry.invoke("find_duplicates", {})
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported operation '{op}'")
    return _ok(result.to_dict(), result.message)


# ---------------------------------------------------------------------------
# Vision
# ---------------------------------------------------------------------------
@router.post("/vision/screenshot")
async def vision_screenshot() -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, screen_vision.screenshot_to_file)
    except NotSupported as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _ok(result.get("data", {}), result.get("message", ""))


@router.get("/vision/latest")
async def vision_latest() -> Any:
    shots = sorted(screen_vision.SHOT_DIR.glob("*.png"))
    if not shots:
        raise HTTPException(status_code=404, detail="No screenshots captured yet.")
    return FileResponse(shots[-1], media_type="image/png")


@router.post("/vision/describe")
async def vision_describe(question: str = Query("Describe what is on screen.")) -> Dict[str, Any]:
    loop = asyncio.get_running_loop()
    try:
        result = await loop.run_in_executor(None, screen_vision.analyse_screen, question)
    except NotSupported as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    return _ok(result.get("data", {}), result.get("message", ""))


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------
@router.get("/memory")
async def memory_list(category: Optional[str] = None) -> Dict[str, Any]:
    return _ok({"items": db.memory_items(category=category), "enabled": config.get().memory_enabled})


@router.post("/memory")
async def memory_add(payload: MemoryCreate) -> Dict[str, Any]:
    result = memory.remember(payload.key, payload.value, payload.category)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return _ok(result.get("data"), result.get("message", ""))


@router.delete("/memory/{memory_id}")
async def memory_delete(memory_id: int) -> Dict[str, Any]:
    removed = db.forget(memory_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Memory entry not found.")
    bus.publish(EventType.MEMORY, {"event": "deleted", "id": memory_id})
    return _ok(None, "Memory entry deleted.")


@router.post("/memory/{memory_id}/toggle")
async def memory_toggle(memory_id: int, enabled: bool = True) -> Dict[str, Any]:
    db.set_memory_enabled(memory_id, enabled)
    bus.publish(EventType.MEMORY, {"event": "toggled", "id": memory_id, "enabled": enabled})
    return _ok(None, "Memory updated.")


@router.post("/memory/clear")
async def memory_clear() -> Dict[str, Any]:
    result = memory.clear_all()
    return _ok(None, result["message"])


@router.post("/memory/enabled")
async def memory_enable(enabled: bool = True) -> Dict[str, Any]:
    settings = config.get()
    settings.memory_enabled = enabled
    config.save(settings)
    conversation.set_memory_enabled(enabled)
    bus.publish(EventType.SETTINGS, {"section": "memory", "values": {"memory_enabled": enabled}})
    return _ok({"enabled": enabled}, f"Memory {'enabled' if enabled else 'disabled'}.")


# ---------------------------------------------------------------------------
# Productivity
# ---------------------------------------------------------------------------
@router.get("/notes")
async def notes_list() -> Dict[str, Any]:
    return _ok({"notes": notes.list_notes()})


@router.post("/notes")
async def notes_create(payload: NoteCreate) -> Dict[str, Any]:
    return _ok(notes.create_note(payload.title, payload.body, payload.tags))


@router.put("/notes/{note_id}")
async def notes_update(note_id: int, payload: NoteUpdate) -> Dict[str, Any]:
    return _ok(notes.update_note(note_id, payload.title, payload.body, payload.tags))


@router.delete("/notes/{note_id}")
async def notes_delete(note_id: int) -> Dict[str, Any]:
    return _ok(notes.delete_note(note_id))


@router.get("/todos")
async def todos_list() -> Dict[str, Any]:
    return _ok({"todos": notes.list_todos()})


@router.post("/todos")
async def todos_create(payload: TodoCreate) -> Dict[str, Any]:
    return _ok(notes.add_todo(payload.text, payload.priority, payload.due_at))


@router.post("/todos/{todo_id}")
async def todos_toggle(todo_id: int, done: bool = True) -> Dict[str, Any]:
    return _ok(notes.complete_todo(todo_id, done))


@router.delete("/todos/{todo_id}")
async def todos_delete(todo_id: int) -> Dict[str, Any]:
    return _ok(notes.delete_todo(todo_id))


@router.get("/reminders")
async def reminders_list() -> Dict[str, Any]:
    return _ok({"reminders": reminders.list_reminders(),
                "timers": timers.timer_manager.list()})


@router.post("/reminders")
async def reminders_create(payload: ReminderCreate) -> Dict[str, Any]:
    result = reminders.set_reminder(payload.message, payload.due_at, payload.repeat)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return _ok(result.get("data"), result.get("message", ""))


@router.delete("/reminders/{reminder_id}")
async def reminders_delete(reminder_id: int) -> Dict[str, Any]:
    return _ok(reminders.delete_reminder(reminder_id))


@router.post("/timers")
async def timers_create(seconds: int, label: str = "Timer") -> Dict[str, Any]:
    result = timers.timer_manager.set_timer(seconds, label)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return _ok(result.get("data"), result.get("message", ""))


@router.delete("/timers")
async def timers_cancel(timer_id: Optional[str] = None) -> Dict[str, Any]:
    return _ok(timers.timer_manager.cancel(timer_id))


@router.get("/summary")
async def daily_summary() -> Dict[str, Any]:
    return _ok(notes.daily_summary())


# ---------------------------------------------------------------------------
# Automation
# ---------------------------------------------------------------------------
@router.get("/tasks")
async def tasks_list() -> Dict[str, Any]:
    return _ok({"tasks": task_queue.list(30), "active": task_queue.active(),
                "history": db.tasks(30)})


@router.post("/tasks/cancel")
async def tasks_cancel(task_id: Optional[str] = None) -> Dict[str, Any]:
    if task_id:
        return _ok(None, "Task cancelled." if task_queue.cancel(task_id) else "Task not found.")
    return _ok(None, f"Cancelled {task_queue.cancel_all()} task(s).")


@router.post("/automation/run")
async def automation_run(goal: str = Query(...)) -> Dict[str, Any]:
    result = await task_executor.execute_goal(goal)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return _ok(result, f"Queued: {goal}")


@router.get("/automation/plan")
async def automation_plan(goal: str = Query(...)) -> Dict[str, Any]:
    builtin = task_executor.plan_for_goal(goal)
    if builtin:
        return _ok({"source": "workflow", "steps": builtin["steps"]})
    plan = await task_executor.plan_with_llm(goal)
    return _ok({"source": "llm", **plan})


@router.get("/automation/commands")
async def custom_commands_list() -> Dict[str, Any]:
    return _ok({"commands": custom_commands.list_commands()})


@router.post("/automation/commands")
async def custom_commands_create(payload: CustomCommandCreate) -> Dict[str, Any]:
    result = custom_commands.add_command(payload.trigger, payload.steps, payload.description)
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result.get("message"))
    return _ok(result.get("data"), result.get("message", ""))


@router.post("/automation/commands/{command_id}/toggle")
async def custom_commands_toggle(command_id: int, enabled: bool = True) -> Dict[str, Any]:
    return _ok(custom_commands.toggle_command(command_id, enabled))


@router.delete("/automation/commands/{command_id}")
async def custom_commands_delete(command_id: int) -> Dict[str, Any]:
    return _ok(custom_commands.delete_command(command_id))


# ---------------------------------------------------------------------------
# Web / audit / direct tool invocation
# ---------------------------------------------------------------------------
@router.get("/web/search")
async def web_search_endpoint(q: str, count: int = 6) -> Dict[str, Any]:
    try:
        results = web_search.search(q, count)
    except web_search.SearchUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    return _ok({"results": [r.to_dict() for r in results]})


@router.post("/tools/invoke")
async def invoke_tool(payload: ToolInvoke) -> Dict[str, Any]:
    """Direct tool execution from the dashboard (permissions still apply)."""
    result = await registry.invoke(payload.name, payload.arguments, source="dashboard")
    return {"ok": result.ok, "message": result.message, "data": result.data,
            "verified": result.verified, "warnings": result.warnings,
            "tool": payload.name}


@router.get("/audit")
async def audit(limit: int = 100) -> Dict[str, Any]:
    return _ok({"entries": db.audit_entries(limit)})


@router.get("/stats")
async def stats() -> Dict[str, Any]:
    return _ok({**db.stats(), "events": len(bus.history(500)),
                "tools": len(registry.all()), "version": __version__})
