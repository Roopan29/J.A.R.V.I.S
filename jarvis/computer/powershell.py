"""Validated PowerShell execution.

Every command passes through ``CommandValidator`` first. Blocked commands are
never started. Commands needing confirmation are surfaced to the user by the
tool layer (``run_powershell`` tool marks itself as confirmation-required for
risky patterns). Output, errors and exit codes are captured and logged.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Any, Dict

from .. import config
from ..security.command_validator import validator
from .platform_support import NotSupported, support

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30
MAX_OUTPUT = 8000


class PowerShellError(RuntimeError):
    pass


def is_available() -> bool:
    return bool(support.powershell)


def run(command: str, timeout: int = DEFAULT_TIMEOUT,
        elevated: bool = False) -> Dict[str, Any]:
    """Execute a validated PowerShell command and capture its output."""
    settings = config.get().security
    # Honour a one-time grant without changing the persisted setting.
    from ..core.permissions import permissions as _permissions
    allow = settings.allow_powershell or _permissions.check_capability("allow_powershell")
    validation = validator.validate_powershell(command, allow_powershell=allow)
    if not validation.allowed:
        log.warning("Blocked PowerShell command: %s", validation.reasons)
        return {"ok": False, "blocked": True,
                "message": "Command blocked by the safety validator: " +
                           "; ".join(validation.reasons),
                "data": validation.to_dict()}

    if elevated:
        return {"ok": False,
                "message": "Elevated execution is not permitted; JARVIS never bypasses UAC. "
                           "Run the command yourself in an administrator terminal."}

    if not support.is_windows:
        raise NotSupported("PowerShell is only available on Windows.")

    started = time.perf_counter()
    try:
        completed = subprocess.run(
            [support.powershell, "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True, text=True, timeout=max(1, min(timeout, 120)),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "timed_out": True,
                "message": f"Command timed out after {timeout}s and was stopped."}
    except OSError as exc:
        raise PowerShellError(f"Could not start PowerShell: {exc}") from exc

    duration = round(time.perf_counter() - started, 2)
    stdout = (completed.stdout or "").strip()[:MAX_OUTPUT]
    stderr = (completed.stderr or "").strip()[:MAX_OUTPUT]
    ok = completed.returncode == 0

    message = stdout or stderr or ("Command completed with no output." if ok else "Command failed.")
    if len(message) > 400:
        message = message[:400] + "…"
    return {
        "ok": ok,
        "verified": True,
        "message": message,
        "data": {
            "stdout": stdout, "stderr": stderr, "exit_code": completed.returncode,
            "duration_s": duration,
            "needs_confirmation": validation.needs_confirmation,
            "risk_reasons": validation.reasons,
        },
    }
