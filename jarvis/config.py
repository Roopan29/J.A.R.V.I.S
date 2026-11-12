"""Configuration: typed model, JSON persistence, environment overrides.

Precedence (highest first):
    environment variables  ->  config.json in JARVIS_HOME  ->  defaults

API keys are NEVER written to config.json; they live in .env or the OS
keyring-backed secret store (see security/secrets.py).
"""
from __future__ import annotations

import json
import os
import platform
import sys
import threading
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path
from typing import Any, Dict, Optional


def _default_home() -> Path:
    env = os.environ.get("JARVIS_HOME")
    if env:
        return Path(env).expanduser().resolve()
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home())
        return Path(base) / "Jarvis"
    return Path.home() / ".jarvis"


HOME: Path = _default_home()
CONFIG_PATH: Path = HOME / "config.json"
LOG_DIR: Path = HOME / "logs"
DATA_DIR: Path = HOME / "data"
CACHE_DIR: Path = HOME / "cache"

for _d in (HOME, LOG_DIR, DATA_DIR, CACHE_DIR):
    _d.mkdir(parents=True, exist_ok=True)


@dataclass
class VoiceSettings:
    wake_word_enabled: bool = True
    wake_word_model: str = "hey jarvis"
    continuous_listening: bool = False
    push_to_talk_hotkey: str = "ctrl+alt+j"
    greeting: str = "Yes, sir. How may I assist you?"
    input_device: Optional[int] = None
    sample_rate: int = 16000
    whisper_model: str = "base"
    whisper_compute_type: str = "int8"
    language: str = "en"
    vad_silence_ms: int = 900
    stt_available: bool = True


@dataclass
class SpeechSettings:
    engine: str = "auto"           # auto | piper | sapi
    voice: str = "en_US-lessac-medium"
    rate: float = 1.0
    volume: float = 0.9
    output_device: Optional[int] = None
    interruptible: bool = True


@dataclass
class AISettings:
    provider: str = "ollama"       # ollama | openai | anthropic | none
    model: str = "llama3.1"
    temperature: float = 0.4
    max_tokens: int = 1024
    context_turns: int = 12
    system_prompt: str = ""
    stream: bool = True


@dataclass
class SecuritySettings:
    confirm_destructive: bool = True
    allow_powershell: bool = True
    allow_screen_capture: bool = False
    allow_file_write: bool = True
    allow_app_launch: bool = True
    allow_shutdown: bool = False
    audit_logging: bool = True
    app_allowlist: list = field(default_factory=list)
    protected_paths: list = field(default_factory=list)


@dataclass
class AppearanceSettings:
    theme: str = "jarvis_cyan"
    accent: str = "#00e5ff"
    animations: bool = True
    scan_lines: bool = True
    reduced_motion: bool = False


@dataclass
class StartupSettings:
    launch_at_login: bool = False
    start_minimised: bool = False
    start_listening: bool = False
    auto_start_server: bool = True
    notify_on_start: bool = True


@dataclass
class AppSettings:
    version: int = 1
    assistant_name: str = "JARVIS"
    user_title: str = "sir"
    user_name: str = ""
    server_host: str = "127.0.0.1"
    server_port: int = 8712
    voice: VoiceSettings = field(default_factory=VoiceSettings)
    speech: SpeechSettings = field(default_factory=SpeechSettings)
    ai: AISettings = field(default_factory=AISettings)
    security: SecuritySettings = field(default_factory=SecuritySettings)
    appearance: AppearanceSettings = field(default_factory=AppearanceSettings)
    startup: StartupSettings = field(default_factory=StartupSettings)
    memory_enabled: bool = True
    theme: str = "jarvis"

    # -- helpers ---------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def section(self, name: str):
        if not hasattr(self, name):
            raise KeyError(name)
        return getattr(self, name)

    def update_section(self, name: str, values: Dict[str, Any]) -> None:
        """Apply a partial update to a nested settings section, ignoring junk."""
        target = self.section(name)
        valid = {f.name: f.type for f in fields(target)}
        for key, value in values.items():
            if key in valid:
                setattr(target, key, value)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
_lock = threading.RLock()
_settings: Optional[AppSettings] = None


def _apply_env(settings: AppSettings) -> None:
    env = os.environ
    if env.get("JARVIS_AI_PROVIDER"):
        settings.ai.provider = env["JARVIS_AI_PROVIDER"]
    if env.get("JARVIS_AI_MODEL"):
        settings.ai.model = env["JARVIS_AI_MODEL"]
    if env.get("JARVIS_CLOUD_MODEL") and settings.ai.provider != "ollama":
        settings.ai.model = env["JARVIS_CLOUD_MODEL"]
    if env.get("JARVIS_WHISPER_MODEL"):
        settings.voice.whisper_model = env["JARVIS_WHISPER_MODEL"]
    if env.get("JARVIS_PIPER_VOICE"):
        settings.speech.voice = env["JARVIS_PIPER_VOICE"]
    if env.get("JARVIS_APP_ALLOWLIST"):
        extra = [a.strip() for a in env["JARVIS_APP_ALLOWLIST"].split(";") if a.strip()]
        settings.security.app_allowlist = sorted(
            set(settings.security.app_allowlist) | set(extra)
        )
    if env.get("JARVIS_PORT"):
        try:
            settings.server_port = int(env["JARVIS_PORT"])
        except ValueError:
            pass


def load() -> AppSettings:
    """Load settings once, creating config.json with defaults on first run."""
    global _settings
    with _lock:
        if _settings is not None:
            return _settings
        settings = AppSettings()
        if CONFIG_PATH.exists():
            try:
                raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                for key, value in raw.items():
                    if key in ("voice", "speech", "ai", "security",
                               "appearance", "startup") and isinstance(value, dict):
                        settings.update_section(key, value)
                    elif hasattr(settings, key) and not isinstance(value, dict):
                        setattr(settings, key, value)
            except (json.JSONDecodeError, OSError) as exc:  # pragma: no cover
                print(f"[config] Could not read {CONFIG_PATH}: {exc}. Using defaults.",
                      file=sys.stderr)
        _apply_env(settings)
        _settings = settings
        save(settings)
        return settings


def save(settings: Optional[AppSettings] = None) -> None:
    global _settings
    with _lock:
        settings = settings or _settings
        if settings is None:
            return
        _settings = settings
        tmp = CONFIG_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(settings.to_dict(), indent=2), encoding="utf-8")
        tmp.replace(CONFIG_PATH)


def get() -> AppSettings:
    return _settings if _settings is not None else load()


def reset() -> AppSettings:
    global _settings
    with _lock:
        _settings = AppSettings()
        save(_settings)
        return _settings


# ---------------------------------------------------------------------------
# Secrets (never persisted to config.json)
# ---------------------------------------------------------------------------
def _read_dotenv() -> Dict[str, str]:
    path = Path(__file__).resolve().parent.parent / ".env"
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_DOTENV = _read_dotenv()


def secret(key: str, default: str = "") -> str:
    return os.environ.get(key) or _DOTENV.get(key, default)


def api_key_for(provider: str) -> str:
    return {
        "openai": secret("OPENAI_API_KEY"),
        "anthropic": secret("ANTHROPIC_API_KEY"),
        "ollama": "",
    }.get(provider, secret(f"{provider.upper()}_API_KEY"))


def ollama_host() -> str:
    return secret("OLLAMA_HOST", "http://127.0.0.1:11434").rstrip("/")


def openai_base_url() -> str:
    return secret("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def is_windows() -> bool:
    return platform.system() == "Windows"
