"""Conversation engine: router dispatch, tool loop, memory, offline honesty.

The tool-loop test runs against an in-process fake Ollama server, so the whole
HTTP path (request → tool call → tool result → final answer) is exercised for
real without needing a model installed.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from jarvis import config
from jarvis.core.conversation import ConversationEngine


# ---------------------------------------------------------------------------
# Fake provider
# ---------------------------------------------------------------------------
class _FakeOllama(BaseHTTPRequestHandler):
    tool_rounds = 0
    calls = []

    def log_message(self, *args):  # silence
        pass

    def _json(self, payload):
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # /api/tags
        self._json({"models": [{"name": "fake:latest"}]})

    def do_POST(self):  # /api/chat
        length = int(self.headers.get("Content-Length", 0))
        request = json.loads(self.rfile.read(length) or b"{}")
        messages = request.get("messages", [])
        type(self).calls.append(request)
        already_used_tools = any(m.get("role") == "tool" for m in messages)
        if not already_used_tools:
            self._json({
                "model": "fake:latest",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [{
                        "function": {"name": "system_status",
                                     "arguments": {"detail": "cpu"}},
                    }],
                },
                "done": True,
            })
            return
        tool_content = [m for m in messages if m.get("role") == "tool"][-1]["content"]
        self._json({
            "model": "fake:latest",
            "message": {"role": "assistant",
                        "content": "Your CPU reading is in: " + tool_content[:60]},
            "done": True,
        })


@pytest.fixture()
def fake_provider(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _FakeOllama)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    from jarvis.core import ai_engine as engine_module

    original_class = engine_module.OllamaProvider
    settings = config.get().ai
    original_provider, original_model = settings.provider, settings.model
    settings.provider = "ollama"
    settings.model = "fake:latest"

    def local_provider(s=None, host=None):
        return original_class(s, host=f"http://127.0.0.1:{port}")

    engine_module.OllamaProvider = local_provider
    engine_module.ai_engine.refresh()
    try:
        yield
    finally:
        engine_module.OllamaProvider = original_class
        settings.provider, settings.model = original_provider, original_model
        engine_module.ai_engine.refresh()
        server.shutdown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_router_handles_time_without_model(registry):
    engine = ConversationEngine()
    reply = await engine.handle("what time is it", speak=False)
    assert reply.intent == "time"
    assert ":" in reply.text
    assert reply.tool_calls == []


@pytest.mark.asyncio
async def test_router_runs_tool_for_timer(registry):
    engine = ConversationEngine()
    reply = await engine.handle("set a timer for 2 seconds", speak=False)
    assert reply.intent == "timer"
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "set_timer"
    assert reply.tool_calls[0]["ok"] is True


@pytest.mark.asyncio
async def test_stop_intent_denies_pending_permissions(registry):
    from jarvis.core.permissions import permissions
    engine = ConversationEngine()
    reply = await engine.handle("stop", speak=False)
    assert reply.intent == "stop"
    assert permissions.pending() == []


@pytest.mark.asyncio
async def test_offline_provider_is_reported_honestly(registry, monkeypatch):
    """With no reachable provider the engine must not fabricate an answer."""
    from jarvis.core import ai_engine as engine_module

    async def offline():
        return {"provider": "ollama", "online": False, "models": [],
                "model_available": False, "detail": "Ollama unreachable (test)"}

    monkeypatch.setattr(engine_module.ai_engine, "health", offline)
    engine = ConversationEngine()
    reply = await engine.handle("write me a sonnet about the ocean", speak=False)
    assert reply.intent == "offline"
    assert "offline" in reply.text.lower() or "unavailable" in reply.text.lower()
    assert "sonnet" not in reply.text.lower()


@pytest.mark.asyncio
async def test_llm_tool_loop_executes_and_reports(registry, fake_provider):
    engine = ConversationEngine()
    reply = await engine.handle("how is my processor doing right now?", speak=False)
    assert reply.provider == "ollama"
    assert reply.tool_calls, "the model's tool call should have executed"
    assert reply.tool_calls[0]["name"] == "system_status"
    assert reply.tool_calls[0]["ok"] is True
    assert "CPU" in reply.text or "cpu" in reply.text.lower()
    # the tool result must have been fed back to the model
    second_request = _FakeOllama.calls[-1]
    assert any(m.get("role") == "tool" for m in second_request["messages"])


@pytest.mark.asyncio
async def test_conversation_persists_turns(registry, db):
    engine = ConversationEngine()
    await engine.handle("what time is it", speak=False)
    turns = db.recent_turns(engine.session_id, 10)
    roles = [t["role"] for t in turns]
    assert "user" in roles and "assistant" in roles


@pytest.mark.asyncio
async def test_custom_command_takes_priority(registry, db):
    from jarvis.automation import custom_commands

    custom_commands.add_command(
        "sonar test",
        [{"tool": "create_note", "arguments": {"title": "sonar", "body": "ping"}}])
    engine = ConversationEngine()
    reply = await engine.handle("sonar test", speak=False)
    assert reply.intent == "custom_command"
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "create_note"
    assert "complete" in reply.text.lower()


def test_system_prompt_contains_safety_rules(registry):
    engine = ConversationEngine()
    prompt = engine.system_prompt()
    assert "NEVER claim an action succeeded" in prompt
    assert "confirmation" in prompt.lower()
    assert "JARVIS" in prompt


def test_reply_serialises():
    from jarvis.core.conversation import Reply
    payload = Reply(text="hi", intent="greeting").to_dict()
    assert payload["text"] == "hi" and payload["intent"] == "greeting"
    assert "tool_calls" in payload
