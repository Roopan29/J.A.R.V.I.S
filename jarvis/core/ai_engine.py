"""AI provider abstraction.

Providers implemented
---------------------
* ``OllamaProvider``    — local models, native /api/chat tool calling.
* ``OpenAIProvider``    — any OpenAI-compatible endpoint (OpenAI, Groq,
                          OpenRouter, Together, LM Studio, vLLM …).
* ``AnthropicProvider`` — Claude messages API with tool use.
* ``NullProvider``      — no model configured: JARVIS reports it honestly and
                          still runs deterministic commands via the router.

No provider is faked. If nothing is reachable, ``health()`` says so and the
conversation layer tells the user instead of inventing an answer.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from .. import config

log = logging.getLogger(__name__)


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    raw_arguments: str = ""


@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    finish_reason: str = ""
    model: str = ""
    provider: str = ""
    usage: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class AIProviderError(RuntimeError):
    pass


class BaseProvider:
    name = "base"

    def __init__(self, settings: Optional[config.AISettings] = None) -> None:
        self.settings = settings or config.get().ai

    async def health(self) -> Dict[str, Any]:
        raise NotImplementedError

    async def chat(self, messages: List[Dict[str, Any]],
                   tools: Optional[List[Dict[str, Any]]] = None,
                   **overrides) -> ChatResult:
        raise NotImplementedError

    async def list_models(self) -> List[str]:
        return []

    @staticmethod
    def _parse_arguments(raw: Any) -> Dict[str, Any]:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {"value": parsed}
            except json.JSONDecodeError:
                log.warning("Model produced non-JSON tool arguments: %r", raw[:200])
                return {}
        return {}


# ---------------------------------------------------------------------------
# Ollama
# ---------------------------------------------------------------------------
class OllamaProvider(BaseProvider):
    name = "ollama"

    def __init__(self, settings: Optional[config.AISettings] = None,
                 host: Optional[str] = None) -> None:
        super().__init__(settings)
        self.host = (host or config.ollama_host()).rstrip("/")

    async def health(self) -> Dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                resp = await client.get(f"{self.host}/api/tags")
                resp.raise_for_status()
                models = [m.get("name", "") for m in resp.json().get("models", [])]
            return {
                "provider": self.name, "online": True, "host": self.host,
                "models": models,
                "model_available": self.settings.model in models or any(
                    m.split(":")[0] == self.settings.model.split(":")[0] for m in models),
                "detail": f"{len(models)} local model(s)",
            }
        except Exception as exc:
            return {
                "provider": self.name, "online": False, "host": self.host,
                "models": [], "model_available": False,
                "detail": f"Ollama unreachable at {self.host} ({type(exc).__name__}). "
                          "Start it with `ollama serve`.",
            }

    async def list_models(self) -> List[str]:
        health = await self.health()
        return list(health.get("models", []))

    async def chat(self, messages, tools=None, **overrides) -> ChatResult:
        payload: Dict[str, Any] = {
            "model": overrides.get("model") or self.settings.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": overrides.get("temperature", self.settings.temperature),
                "num_predict": overrides.get("max_tokens", self.settings.max_tokens),
            },
        }
        if tools:
            payload["tools"] = tools
        try:
            async with httpx.AsyncClient(timeout=180) as client:
                resp = await client.post(f"{self.host}/api/chat", json=payload)
                if resp.status_code == 404:
                    raise AIProviderError(
                        f"Model '{payload['model']}' is not installed. Run: ollama pull {payload['model']}")
                resp.raise_for_status()
                data = resp.json()
        except httpx.ConnectError as exc:
            raise AIProviderError(
                f"Cannot reach Ollama at {self.host}. Start it with `ollama serve`.") from exc
        except httpx.HTTPStatusError as exc:
            raise AIProviderError(f"Ollama returned HTTP {exc.response.status_code}: "
                                  f"{exc.response.text[:300]}") from exc

        message = data.get("message", {}) or {}
        calls: List[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function", {}) or {}
            calls.append(ToolCall(
                id=call.get("id") or f"call_{index}",
                name=fn.get("name", ""),
                arguments=self._parse_arguments(fn.get("arguments")),
                raw_arguments=json.dumps(fn.get("arguments"), default=str),
            ))
        return ChatResult(
            content=message.get("content", "") or "",
            tool_calls=calls,
            finish_reason=data.get("done_reason", "stop"),
            model=data.get("model", payload["model"]),
            provider=self.name,
            usage={"eval_count": data.get("eval_count", 0),
                   "prompt_eval_count": data.get("prompt_eval_count", 0)},
        )


# ---------------------------------------------------------------------------
# OpenAI-compatible
# ---------------------------------------------------------------------------
class OpenAIProvider(BaseProvider):
    name = "openai"

    def __init__(self, settings: Optional[config.AISettings] = None,
                 api_key: Optional[str] = None, base_url: Optional[str] = None) -> None:
        super().__init__(settings)
        self.api_key = api_key or config.api_key_for("openai")
        self.base_url = (base_url or config.openai_base_url()).rstrip("/")

    @property
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def health(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"provider": self.name, "online": False, "models": [],
                    "model_available": False,
                    "detail": "OPENAI_API_KEY not set (see .env.example)."}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get(f"{self.base_url}/models", headers=self._headers)
                resp.raise_for_status()
                models = [m.get("id", "") for m in resp.json().get("data", [])]
            return {"provider": self.name, "online": True, "models": models[:50],
                    "model_available": (not models) or self.settings.model in models,
                    "detail": f"{len(models)} model(s) available"}
        except Exception as exc:
            return {"provider": self.name, "online": False, "models": [],
                    "model_available": False,
                    "detail": f"Provider unreachable: {type(exc).__name__}: {exc}"}

    async def list_models(self) -> List[str]:
        return list((await self.health()).get("models", []))

    async def chat(self, messages, tools=None, **overrides) -> ChatResult:
        if not self.api_key:
            raise AIProviderError("OPENAI_API_KEY is not configured.")
        payload: Dict[str, Any] = {
            "model": overrides.get("model") or self.settings.model,
            "messages": messages,
            "temperature": overrides.get("temperature", self.settings.temperature),
            "max_tokens": overrides.get("max_tokens", self.settings.max_tokens),
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(f"{self.base_url}/chat/completions",
                                         headers=self._headers, json=payload)
                if resp.status_code >= 400:
                    raise AIProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
        except httpx.RequestError as exc:
            raise AIProviderError(f"Network error contacting provider: {exc}") from exc

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message", {}) or {}
        calls = []
        for index, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function", {}) or {}
            calls.append(ToolCall(
                id=call.get("id") or f"call_{index}",
                name=fn.get("name", ""),
                arguments=self._parse_arguments(fn.get("arguments")),
                raw_arguments=fn.get("arguments") or "",
            ))
        return ChatResult(
            content=message.get("content") or "",
            tool_calls=calls,
            finish_reason=choice.get("finish_reason", ""),
            model=data.get("model", payload["model"]),
            provider=self.name,
            usage=data.get("usage", {}),
        )


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
class AnthropicProvider(BaseProvider):
    name = "anthropic"

    def __init__(self, settings: Optional[config.AISettings] = None,
                 api_key: Optional[str] = None) -> None:
        super().__init__(settings)
        self.api_key = api_key or config.api_key_for("anthropic")
        key = config.secret("JARVIS_ANTHROPIC_MODEL")
        if key:
            self.settings.model = key

    @property
    def _headers(self) -> Dict[str, str]:
        return {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def health(self) -> Dict[str, Any]:
        if not self.api_key:
            return {"provider": self.name, "online": False, "models": [],
                    "model_available": False,
                    "detail": "ANTHROPIC_API_KEY not set (see .env.example)."}
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                resp = await client.get("https://api.anthropic.com/v1/models",
                                        headers=self._headers)
                resp.raise_for_status()
                models = [m.get("id", "") for m in resp.json().get("data", [])]
            return {"provider": self.name, "online": True, "models": models,
                    "model_available": (not models) or self.settings.model in models,
                    "detail": f"{len(models)} model(s) available"}
        except Exception as exc:
            return {"provider": self.name, "online": False, "models": [],
                    "model_available": False,
                    "detail": f"Provider unreachable: {type(exc).__name__}: {exc}"}

    async def list_models(self) -> List[str]:
        return list((await self.health()).get("models", []))

    @staticmethod
    def _convert(messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
        system_parts: List[str] = []
        converted: List[Dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "system":
                system_parts.append(str(message.get("content", "")))
                continue
            if role == "tool":
                converted.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": message.get("tool_call_id", ""),
                        "content": str(message.get("content", "")),
                    }],
                })
                continue
            if role == "assistant" and message.get("tool_calls"):
                blocks: List[Dict[str, Any]] = []
                if message.get("content"):
                    blocks.append({"type": "text", "text": message["content"]})
                for call in message["tool_calls"]:
                    fn = call.get("function", {})
                    blocks.append({
                        "type": "tool_use",
                        "id": call.get("id", ""),
                        "name": fn.get("name", ""),
                        "input": BaseProvider._parse_arguments(fn.get("arguments")),
                    })
                converted.append({"role": "assistant", "content": blocks})
                continue
            content = message.get("content", "")
            converted.append({
                "role": "assistant" if role == "assistant" else "user",
                "content": [{"type": "text", "text": str(content)}] if isinstance(content, str) else content,
            })
        return "\n\n".join(p for p in system_parts if p), converted

    async def chat(self, messages, tools=None, **overrides) -> ChatResult:
        if not self.api_key:
            raise AIProviderError("ANTHROPIC_API_KEY is not configured.")
        system, converted = self._convert(messages)
        payload: Dict[str, Any] = {
            "model": overrides.get("model") or self.settings.model,
            "max_tokens": overrides.get("max_tokens", self.settings.max_tokens),
            "temperature": overrides.get("temperature", self.settings.temperature),
            "messages": converted,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post("https://api.anthropic.com/v1/messages",
                                         headers=self._headers, json=payload)
                if resp.status_code >= 400:
                    raise AIProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
        except httpx.RequestError as exc:
            raise AIProviderError(f"Network error contacting Anthropic: {exc}") from exc

        text_parts: List[str] = []
        calls: List[ToolCall] = []
        for block in data.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                calls.append(ToolCall(id=block.get("id", ""), name=block.get("name", ""),
                                      arguments=block.get("input") or {}))
        return ChatResult(content="\n".join(text_parts), tool_calls=calls,
                          finish_reason=data.get("stop_reason", ""),
                          model=data.get("model", payload["model"]),
                          provider=self.name, usage=data.get("usage", {}))


# ---------------------------------------------------------------------------
# Null provider
# ---------------------------------------------------------------------------
class NullProvider(BaseProvider):
    name = "none"

    async def health(self) -> Dict[str, Any]:
        return {"provider": "none", "online": False, "models": [], "model_available": False,
                "detail": "No AI provider configured. Install Ollama or add an API key."}

    async def chat(self, messages, tools=None, **overrides) -> ChatResult:
        return ChatResult(
            content=("My language model is offline, sir. Install Ollama "
                     "(`ollama pull llama3.1`), or add an API key in Settings → AI. "
                     "I can still run direct commands and system queries."),
            provider="none",
            model="none",
        )


# ---------------------------------------------------------------------------
# Factory + manager
# ---------------------------------------------------------------------------
def build_provider(settings: Optional[config.AISettings] = None) -> BaseProvider:
    settings = settings or config.get().ai
    provider = (settings.provider or "none").lower()
    if provider == "ollama":
        return OllamaProvider(settings)
    if provider in {"openai", "groq", "openrouter", "together", "lmstudio", "custom"}:
        return OpenAIProvider(settings)
    if provider == "anthropic":
        return AnthropicProvider(settings)
    return NullProvider(settings)


class AIEngine:
    """Facade the rest of the app talks to; rebuilds when settings change."""

    def __init__(self) -> None:
        self._provider: Optional[BaseProvider] = None

    @property
    def provider(self) -> BaseProvider:
        if self._provider is None:
            self._provider = build_provider()
        return self._provider

    def refresh(self) -> BaseProvider:
        self._provider = build_provider()
        log.info("AI provider set to %s (%s)", self._provider.name, config.get().ai.model)
        return self._provider

    async def health(self) -> Dict[str, Any]:
        try:
            return await self.provider.health()
        except Exception as exc:  # pragma: no cover
            return {"provider": self.provider.name, "online": False,
                    "detail": f"{type(exc).__name__}: {exc}", "models": [],
                    "model_available": False}

    async def chat(self, messages, tools=None, **overrides) -> ChatResult:
        return await self.provider.chat(messages, tools=tools, **overrides)

    async def list_models(self) -> List[str]:
        try:
            return await self.provider.list_models()
        except Exception:
            return []


ai_engine = AIEngine()
