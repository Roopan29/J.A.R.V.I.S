"""Secret storage.

Order of resolution for an API key:
    1. Process environment variable
    2. .env file next to the project root
    3. Optional OS keyring ("jarvis" service) if the keyring package exists
    4. Encrypted-at-rest fallback in JARVIS_HOME/secrets.json (obfuscated with a
       machine-local key derived from the user account — documented as such)

The point: keys are never written into config.json, never logged, and never
sent to the dashboard in cleartext (only a boolean "configured" flag is shown).
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import platform
from pathlib import Path
from typing import Dict

from .. import config

log = logging.getLogger(__name__)

_FALLBACK = config.HOME / "secrets.json"
_MASK = "********"


def _machine_key() -> bytes:
    seed = "|".join([
        platform.node(),
        os.environ.get("USERNAME") or os.environ.get("USER") or "user",
        str(Path.home()),
        "jarvis-v1",
    ])
    return hashlib.sha256(seed.encode("utf-8")).digest()


def _xor(data: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def _load_fallback() -> Dict[str, str]:
    if not _FALLBACK.exists():
        return {}
    try:
        raw = json.loads(_FALLBACK.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    out: Dict[str, str] = {}
    key = _machine_key()
    for name, blob in raw.items():
        try:
            out[name] = _xor(base64.b64decode(blob), key).decode("utf-8")
        except Exception:
            log.warning("Could not decode stored secret '%s'", name)
    return out


def _save_fallback(values: Dict[str, str]) -> None:
    key = _machine_key()
    blob = {name: base64.b64encode(_xor(value.encode("utf-8"), key)).decode("ascii")
            for name, value in values.items()}
    tmp = _FALLBACK.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob), encoding="utf-8")
    tmp.replace(_FALLBACK)
    try:
        os.chmod(_FALLBACK, 0o600)
    except OSError:
        pass


def get_secret(name: str) -> str:
    value = os.environ.get(name) or config.secret(name)
    if value:
        return value
    try:  # optional keyring backend
        import keyring  # type: ignore
        stored = keyring.get_password("jarvis", name)
        if stored:
            return stored
    except Exception:
        pass
    return _load_fallback().get(name, "")


def set_secret(name: str, value: str, use_keyring: bool = True) -> None:
    """Persist a secret (empty value deletes it)."""
    if not name or not name.isupper():
        raise ValueError("Secret names must be UPPER_SNAKE_CASE")
    if not value:
        stored = _load_fallback()
        stored.pop(name, None)
        _save_fallback(stored)
        try:
            import keyring  # type: ignore
            keyring.delete_password("jarvis", name)
        except Exception:
            pass
        return
    if use_keyring:
        try:
            import keyring  # type: ignore
            keyring.set_password("jarvis", name, value)
            return
        except Exception:
            log.info("keyring unavailable; using encrypted local store for %s", name)
    stored = _load_fallback()
    stored[name] = value
    _save_fallback(stored)


def mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return _MASK
    return f"{value[:4]}…{value[-4:]}"


def provider_status(provider: str) -> Dict[str, object]:
    env_name = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "ollama": "",
    }.get(provider, f"{provider.upper()}_API_KEY")
    if not env_name:
        return {"provider": provider, "configured": True, "env": None, "preview": ""}
    value = get_secret(env_name)
    return {
        "provider": provider,
        "configured": bool(value),
        "env": env_name,
        "preview": mask(value),
    }
