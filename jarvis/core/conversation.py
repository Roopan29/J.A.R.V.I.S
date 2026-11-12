"""Conversation orchestration: intent routing, LLM tool-calling loop, memory.

Flow for one user turn
----------------------
1. Deterministic router tries to resolve the command (instant, no model).
2. Otherwise the LLM is called with the full tool catalogue and a system
   prompt describing JARVIS's personality, safety rules and the live
   environment. Tool calls are executed through the registry (which enforces
   permissions/confirmation) and fed back to the model, up to N rounds.
3. The final reply is stored in SQLite, published to the dashboard, spoken
   aloud, and any memorable facts are extracted into the memory store.

Honesty rule: the model sees the *actual* tool results. If a tool failed, the
prompt forbids claiming success.
"""
from __future__ import annotations

import asyncio
import json
import logging
import platform
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from .. import config
from ..database.database import db
from ..database.models import ActionResult
from ..event_bus import EventType, bus
from .ai_engine import AIProviderError, ai_engine
from .intent_router import Intent, router
from .tool_registry import ToolRegistry, registry

log = logging.getLogger(__name__)

MAX_TOOL_ROUNDS = 5

_SYSTEM_TEMPLATE = """You are {name}, a private AI assistant running locally on the user's \
{os_name} computer. You address the user as "{title}" and speak with the composed, precise, \
slightly dry wit of Tony Stark's JARVIS. Be concise: replies are spoken aloud, so keep them to \
1–3 sentences unless the user asks for detail.

CURRENT CONTEXT
- Local time: {now}
- Host: {host} ({os_name} {os_version})
- AI provider: {provider}, model: {model}
- Voice: the user may be speaking (speech-to-text) or typing.

CAPABILITIES
You can control this computer through the provided tools: launch/close applications, manage \
files and folders, type and click, inspect the screen, run *validated* PowerShell, monitor \
system performance, search the web, keep notes/reminders/todos, and remember facts.

RULES — non-negotiable
1. NEVER claim an action succeeded unless a tool result confirms it. If a tool failed or was \
   denied, say exactly what happened.
2. Destructive actions (deleting files, shutting down, installing software, running elevated \
   commands) require the user's confirmation — the tool layer will ask; do not assume consent.
3. Prefer one accurate tool call over many speculative ones. Verify with a follow-up tool \
   (e.g. list the folder) when the user's request implies a visible result.
4. Don't invent facts. For current events or anything time-sensitive, use web_search and cite \
   the source domains in your answer.
5. If you cannot do something (missing permission, no GPU, provider offline), say so plainly \
   and offer the closest alternative.
6. Never expose API keys, tokens or file contents the user did not ask for.
7. Answer normal conversation directly, without tools.

{memory_block}
{extra_block}"""


@dataclass
class Reply:
    text: str
    intent: str = "unknown"
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    session_id: str = ""
    provider: str = ""
    model: str = ""
    spoken: bool = False
    error: str = ""
    need_confirmation: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text, "intent": self.intent, "tool_calls": self.tool_calls,
            "session_id": self.session_id, "provider": self.provider,
            "model": self.model, "spoken": self.spoken, "error": self.error,
            "need_confirmation": self.need_confirmation,
        }


class ConversationEngine:
    def __init__(self, tool_registry: Optional[ToolRegistry] = None) -> None:
        self.registry = tool_registry or registry
        self.session_id = f"session-{uuid.uuid4().hex[:8]}"
        self._history: List[Dict[str, Any]] = []
        self._memory_enabled = True
        self._extra_context: str = ""
        self._last_intent: Intent = Intent()
        self._speaker = None       # injected by the voice manager
        self._cancelled = False

    # -- wiring -----------------------------------------------------------
    def attach_speaker(self, speaker) -> None:
        """Give the engine a callable coroutine ``speak(text)``."""
        self._speaker = speaker

    def set_memory_enabled(self, enabled: bool) -> None:
        self._memory_enabled = enabled

    def set_extra_context(self, text: str) -> None:
        self._extra_context = text or ""

    def cancel(self) -> None:
        self._cancelled = True

    def reset(self) -> str:
        self.session_id = f"session-{uuid.uuid4().hex[:8]}"
        self._history.clear()
        self._cancelled = False
        return self.session_id

    # -- prompt building ---------------------------------------------------
    def _memory_block(self) -> str:
        if not self._memory_enabled or not config.get().memory_enabled:
            return "MEMORY: disabled by the user."
        items = db.memory_items(enabled_only=True, limit=40)
        if not items:
            return "MEMORY: nothing learned about the user yet."
        lines = [f"- [{m['category']}] {m['key']}: {m['value']}" for m in items]
        return "WHAT YOU REMEMBER ABOUT THE USER\n" + "\n".join(lines)

    def system_prompt(self) -> str:
        settings = config.get()
        return _SYSTEM_TEMPLATE.format(
            name=settings.assistant_name,
            title=settings.user_title,
            os_name=platform.system(),
            os_version=platform.release(),
            host=platform.node(),
            now=datetime.now().strftime("%A %d %B %Y, %H:%M"),
            provider=ai_engine.provider.name,
            model=settings.ai.model,
            memory_block=self._memory_block(),
            extra_block=self._extra_context,
        )

    def _build_messages(self, text: str) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = [{"role": "system", "content": self.system_prompt()}]
        turns = settings_turns = config.get().ai.context_turns
        history = self._history[-settings_turns * 2:] if turns else []
        for item in history:
            messages.append({"role": item["role"], "content": item["content"]})
        messages.append({"role": "user", "content": text})
        return messages

    # -- public API ---------------------------------------------------------
    async def handle(self, text: str, *, speak: bool = True,
                     source: str = "text", session_id: Optional[str] = None) -> Reply:
        text = (text or "").strip()
        if not text:
            return Reply(text="I did not catch that, sir.", session_id=self.session_id)

        self._cancelled = False
        if session_id:
            self.session_id = session_id
        bus.publish(EventType.COMMAND, {"text": text, "source": source})
        db.add_turn(self.session_id, "user", text)
        bus.publish(EventType.STATE, {"state": "thinking"})

        reply: Reply
        try:
            intent = router.route(text)
            self._last_intent = intent
            reply = await self._dispatch(text, intent)
        except asyncio.CancelledError:
            bus.publish(EventType.STATE, {"state": "idle"})
            raise
        except AIProviderError as exc:
            reply = Reply(text=f"My language model is unavailable: {exc}",
                          error=str(exc), session_id=self.session_id)
        except Exception as exc:  # keep the assistant alive
            log.exception("Conversation failure")
            reply = Reply(text="Something went wrong while processing that, sir. "
                               "The details are in the activity log.",
                          error=str(exc), session_id=self.session_id)
        finally:
            bus.publish(EventType.STATE, {"state": "idle"})

        reply.session_id = self.session_id
        db.add_turn(self.session_id, "assistant", reply.text, intent=reply.intent,
                    tools=[c.get("name", "") for c in reply.tool_calls])
        self._history.append({"role": "user", "content": text})
        self._history.append({"role": "assistant", "content": reply.text})
        self._history = self._history[-40:]
        self._extract_memories(text, reply)

        bus.publish(EventType.RESPONSE, reply.to_dict())
        if speak and self._speaker and reply.text:
            try:
                await self._speaker(reply.text)
                reply.spoken = True
            except Exception:
                log.exception("Speech output failed")
        return reply

    # -- dispatch ------------------------------------------------------------
    async def _dispatch(self, text: str, intent: Intent) -> Reply:
        # 0. custom commands defined by the user
        custom = self._match_custom_command(text)
        if custom:
            return await self._run_custom(custom)

        # 1. deterministic routes that need no model
        if intent.name == "stop":
            from .permissions import permissions
            permissions.deny_all()
            if self._speaker and hasattr(self._speaker, "stop"):
                self._speaker.stop()
            return Reply(text="Standing down, sir.", intent="stop")

        if intent.name == "empty":
            return Reply(text=intent.speak or "Yes, sir?", intent="empty")

        if intent.tool and intent.confidence >= 0.6:
            result = await self.registry.invoke(intent.tool, intent.arguments)
            return Reply(text=self._narrate(intent, result), intent=intent.name,
                         tool_calls=[{"name": intent.tool, "arguments": intent.arguments,
                                      "ok": result.ok}])

        if intent.name in {"greeting", "thanks", "time", "date"} and intent.speak:
            return Reply(text=intent.speak, intent=intent.name)

        # 2. LLM with tools
        return await self._llm_turn(text, intent)

    def _narrate(self, intent: Intent, result: ActionResult) -> str:
        if intent.speak and result.ok:
            return intent.speak
        if result.message:
            return result.message
        return "Done, sir." if result.ok else "That did not work, sir."

    # -- LLM tool loop --------------------------------------------------------
    async def _llm_turn(self, text: str, intent: Intent) -> Reply:
        provider = ai_engine.provider
        health = await ai_engine.health()
        if not health.get("online") and provider.name != "none":
            return Reply(text=self._offline_message(health), intent="offline",
                         error="provider offline", provider=provider.name)
        if provider.name == "none":
            return Reply(text=self._offline_message(health), intent="offline",
                         error="no provider", provider="none")

        messages = self._build_messages(text)
        tools = self.registry.schemas()
        collected: List[Dict[str, Any]] = []
        final_text = ""

        for round_index in range(MAX_TOOL_ROUNDS):
            if self._cancelled:
                return Reply(text="Cancelled, sir.", intent=intent.name,
                             tool_calls=collected, provider=provider.name,
                             model=config.get().ai.model)
            result = await ai_engine.chat(messages, tools=tools)
            if not result.has_tool_calls:
                final_text = (result.content or "").strip()
                break

            # record assistant tool-call message verbatim for the next round
            assistant_message: Dict[str, Any] = {
                "role": "assistant",
                "content": result.content or "",
                "tool_calls": [
                    {"id": call.id, "type": "function",
                     "function": {"name": call.name,
                                  "arguments": json.dumps(call.arguments)}}
                    for call in result.tool_calls
                ],
            }
            messages.append(assistant_message)

            for call in result.tool_calls:
                bus.log(f"AI requested {call.name}({_short_json(call.arguments)})",
                        source="ai")
                outcome = await self.registry.invoke(call.name, call.arguments, source="ai")
                collected.append({"name": call.name, "arguments": call.arguments,
                                  "ok": outcome.ok, "message": outcome.message,
                                  "data": _trim_data(outcome.data)})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "name": call.name,
                    "content": json.dumps(outcome.to_dict(), default=str)[:6000],
                })
            if result.content and not final_text:
                final_text = result.content.strip()

        if not final_text:
            if collected:
                final_text = _summarise_tool_results(collected)
            else:
                final_text = "I could not complete that, sir. The model returned no answer."
        return Reply(text=final_text, intent=intent.name, tool_calls=collected,
                     provider=provider.name, model=config.get().ai.model)

    @staticmethod
    def _offline_message(health: Dict[str, Any]) -> str:
        detail = health.get("detail", "no model available")
        return (f"My language model is offline, sir — {detail} "
                "I can still run direct commands such as opening apps, timers, "
                "system status, files and clipboard operations.")

    # -- custom commands ------------------------------------------------------
    def _match_custom_command(self, text: str) -> Optional[Dict[str, Any]]:
        lowered = re.sub(r"^hey jarvis[, ]*", "", text.lower()).strip(" .!?")
        for command in db.custom_commands(enabled_only=True):
            trigger = command["trigger"]
            if lowered == trigger or lowered.startswith(trigger + " ") \
                    or re.search(rf"\b{re.escape(trigger)}\b", lowered):
                try:
                    command["action_parsed"] = json.loads(command["action"])
                except json.JSONDecodeError:
                    command["action_parsed"] = {}
                return command
        return None

    async def _run_custom(self, command: Dict[str, Any]) -> Reply:
        actions = command.get("action_parsed", {}).get("steps", [])
        results, messages = [], []
        for step in actions:
            tool = step.get("tool") or step.get("action")
            args = step.get("arguments", {}) or {}
            if not tool:
                continue
            outcome = await self.registry.invoke(tool, args, source="custom")
            results.append({"name": tool, "arguments": args, "ok": outcome.ok,
                            "message": outcome.message})
            messages.append(outcome.message or tool)
        ok = all(r["ok"] for r in results) if results else False
        if not results:
            return Reply(text=f"'{command['trigger']}' has no steps configured, sir.",
                         intent="custom_command")
        summary = command.get("description") or command["trigger"].title()
        return Reply(text=(f"{summary} — complete, sir." if ok else
                           f"{summary} — partly failed: " + "; ".join(messages)),
                     intent="custom_command", tool_calls=results)

    # -- memory extraction ----------------------------------------------------
    _MEMORY_PATTERNS = [
        (r"\bmy name is\s+([A-Z][\w'-]*(?:\s+[A-Z][\w'-]*)?)", "fact", "name"),
        (r"\bcall me\s+([A-Z][\w'-]*)", "fact", "name"),
        (r"\bremember(?: that)?\s+(.{3,200})", "fact", "note"),
        (r"\bi (?:work|am working) (?:at|for)\s+(.{2,60})", "fact", "workplace"),
        (r"\bi (?:live|am based) in\s+(.{2,60})", "fact", "location"),
        (r"\bmy (?:project|projects) (?:is|are)\s+(.{2,120})", "project", "project"),
        (r"\bi (?:prefer|like)\s+(.{3,120})", "preference", "preference"),
        (r"\bmy favourite ([\w ]{2,30}) is\s+(.{2,60})", "preference", "favourite"),
        (r"\bi use\s+([\w+#. ]{2,40})\s+for\b", "preference", "tools"),
    ]

    def _extract_memories(self, user_text: str, reply: Reply) -> None:
        if not (self._memory_enabled and config.get().memory_enabled):
            return
        if re.search(r"\b(forget|don't remember|do not remember)\b", user_text, re.I):
            return
        changed = False
        for pattern, category, key in self._MEMORY_PATTERNS:
            match = re.search(pattern, user_text, re.I)
            if not match:
                continue
            value = (match.groups()[-1] or "").strip(" .")
            if len(value) < 2 or len(value) > 200:
                continue
            if re.search(r"\b(password|api[_ ]?key|token|secret|pin)\b", value, re.I):
                continue
            db.remember(key=key, value=value, category=category, source="conversation")
            changed = True
        if changed:
            bus.publish(EventType.MEMORY, {"event": "updated"})

    # -- history -------------------------------------------------------------
    def history(self, limit: int = 50) -> List[Dict[str, Any]]:
        return db.recent_turns(self.session_id, limit)


def _short_json(data: Dict[str, Any], limit: int = 120) -> str:
    text = json.dumps(data, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def _trim_data(data: Dict[str, Any], limit: int = 8) -> Dict[str, Any]:
    trimmed: Dict[str, Any] = {}
    for index, (key, value) in enumerate(data.items()):
        if index >= limit:
            break
        if isinstance(value, (list, tuple)):
            trimmed[key] = list(value)[:8]
        elif isinstance(value, dict):
            trimmed[key] = {k: v for k, v in list(value.items())[:8]}
        else:
            trimmed[key] = value
    return trimmed


def _summarise_tool_results(results: List[Dict[str, Any]]) -> str:
    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]
    parts = []
    if ok:
        parts.append("Completed: " + ", ".join(r["name"] for r in ok) + ".")
    if failed:
        parts.append("Failed: " + ", ".join(f"{r['name']} ({r['message'][:80]})" for r in failed) + ".")
    return " ".join(parts) if parts else "I could not complete that, sir."


conversation = ConversationEngine()
