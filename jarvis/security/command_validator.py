"""Validation of PowerShell commands and risky filesystem paths.

Threat model: the language model can be induced (by a malicious document, a
web page, or a typo) to emit a destructive PowerShell command. We therefore
validate *every* command against a hard blocklist before it reaches a shell,
refuse obfuscation constructs, and require explicit confirmation otherwise.

Nothing here executes anything — this module only answers yes/no/confirm.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# --- Hard blocks: never executed, even with confirmation -------------------
FORBIDDEN_PATTERNS = [
    # credential / secret theft
    (r"\bmimikatz\b", "credential dumping tool"),
    (r"\blsass\.exe\b", "LSASS access"),
    (r"sekurlsa", "credential dump"),
    (r"reg\s+save\s+hklm\\sam", "SAM hive export"),
    (r"\bkeylogger\b", "keylogging"),
    (r"get-credential|\bconvertto-securestring\b.*-asplaintext", "plaintext credential handling"),
    # disabling protection
    (r"set-mppreference\s+.*-disablerealtimemonitoring\s+\$?true", "disabling Defender"),
    (r"add-mppreference\s+.*exclusionpath", "adding Defender exclusions"),
    (r"netsh\s+advfirewall\s+set\s+.*\bstate\s+off", "disabling firewall"),
    (r"bcdedit\s+.*(?:nointegritychecks|testsigning)", "disabling integrity checks"),
    (r"uninstall-windowsfeature\s+windows-defender", "removing Defender"),
    # remote access / persistence
    (r"enable-psremoting", "enabling remote shell"),
    (r"winrm\s+set\s+winrm/config/service.*allowunencrypted", "insecure remote management"),
    (r"new-scheduledtask.*-user\s+system", "SYSTEM-level persistence"),
    (r"schtasks.*\/create.*\/ru\s+system", "SYSTEM-level persistence"),
    (r"reg\s+add\s+.*\\run\b", "run-key persistence"),
    # disk / data destruction
    (r"format-volume\b", "formatting a volume"),
    (r"clear-disk\b", "wiping a disk"),
    (r"remove-partition\b", "deleting a partition"),
    (r"cipher\s+/w", "wiping free space"),
    (r"\bdiskpart\b", "raw disk utility"),
    (r"vssadmin\s+delete\s+shadows", "deleting shadow copies"),
    # fork bomb / resource exhaustion
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
]

#: Commands that need explicit user confirmation before execution.
CONFIRM_PATTERNS = [
    (r"remove-item\b.*-recurse", "recursive delete"),
    (r"\bdel\b\s+/[sfq]", "forced delete"),
    (r"\brmdir\b\s+/s", "recursive directory removal"),
    (r"\brm\s+-rf\b", "recursive delete"),
    (r"stop-process\b.*-force", "force-killing processes"),
    (r"stop-computer|restart-computer|shutdown\b", "power state change"),
    (r"set-executionpolicy", "changing execution policy"),
    (r"install-(package|module)\b|winget\s+install", "software installation"),
    (r"uninstall-(package|module)\b|winget\s+uninstall", "software removal"),
    (r"set-netfirewallprofile", "firewall change"),
    (r"new-localuser|add-localgroupmember", "account modification"),
    (r"takeown\b|icacls\b.*\/grant", "permission modification"),
    (r"reg\s+(add|delete)\b", "registry modification"),
    (r"invoke-expression|\biex\b", "dynamic execution"),
    (r"start-process\b.*-verb\s+runas", "elevation request"),
]

#: Obfuscation constructs: refused outright (never "confirmable").
OBFUSCATION_PATTERNS = [
    (r"-e(nc(odedcommand)?)?\s+[A-Za-z0-9+/=]{20,}", "base64 encoded command"),
    (r"frombase64string", "base64 decoding"),
    (r"\[char\]\s*\d+", "character-code obfuscation"),
    (r"\$\{?[a-z]\}?\s*\+\s*\$\{?[a-z]\}?\s*\+", "string fragmentation"),
    (r"-windowstyle\s+hidden", "hidden window execution"),
    (r"-w\s+hidden", "hidden window execution"),
    (r"downloadstring|downloadfile|invoke-webrequest.*\|\s*iex", "remote payload execution"),
    (r"iwr\b.*\|\s*iex", "remote payload execution"),
    (r"curl\b.*\|\s*(ba)?sh", "piping remote script to shell"),
    (r"certutil\s+-(urlcache|decode|encode)", "certutil payload staging"),
    (r"bitsadmin\s+/transfer", "payload download"),
    (r"mshta\s+(http|javascript|vbscript)", "script host execution"),
]

#: Paths where writes/deletes are refused (or need confirmation).
PROTECTED_PATH_SEGMENTS = [
    "c:\\windows", "c:\\program files", "c:\\program files (x86)",
    "c:\\programdata", "c:\\$recycle.bin", "c:\\system volume information",
    "c:\\boot", "c:\\recovery",
]

MAX_COMMAND_LENGTH = 2000


@dataclass
class ValidationResult:
    allowed: bool
    needs_confirmation: bool = False
    reasons: List[str] = field(default_factory=list)
    sanitized: Optional[str] = None
    matched_rule: str = ""

    def to_dict(self) -> dict:
        return {
            "allowed": self.allowed,
            "needs_confirmation": self.needs_confirmation,
            "reasons": self.reasons,
            "sanitized": self.sanitized,
            "matched_rule": self.matched_rule,
        }


class CommandValidator:
    """Stateless validator for PowerShell and filesystem operations."""

    def __init__(self) -> None:
        self._forbidden = [(re.compile(p, re.I), r) for p, r in FORBIDDEN_PATTERNS]
        self._confirm = [(re.compile(p, re.I), r) for p, r in CONFIRM_PATTERNS]
        self._obfuscation = [(re.compile(p, re.I), r) for p, r in OBFUSCATION_PATTERNS]

    # -- PowerShell -------------------------------------------------------
    def validate_powershell(self, command: str, allow_powershell: bool = True) -> ValidationResult:
        if not allow_powershell:
            return ValidationResult(False, reasons=["PowerShell execution is disabled in settings."])

        command = (command or "").strip()
        if not command:
            return ValidationResult(False, reasons=["Empty command."])
        if len(command) > MAX_COMMAND_LENGTH:
            return ValidationResult(False, reasons=["Command exceeds maximum length."])
        if "\x00" in command:
            return ValidationResult(False, reasons=["Null byte in command."])

        for pattern, reason in self._forbidden:
            if pattern.search(command):
                return ValidationResult(False, reasons=[f"Blocked: {reason}."],
                                        matched_rule=pattern.pattern)
        for pattern, reason in self._obfuscation:
            if pattern.search(command):
                return ValidationResult(False, reasons=[f"Blocked: {reason} (obfuscation)."],
                                        matched_rule=pattern.pattern)

        confirmation_reasons: List[str] = []
        for pattern, reason in self._confirm:
            if pattern.search(command):
                confirmation_reasons.append(reason)

        # Chained commands hide intent; require confirmation each time.
        if re.search(r"[;&]{1,2}\s*\w", command) and len(command.split(";")) > 2:
            confirmation_reasons.append("chained commands")

        return ValidationResult(
            allowed=True,
            needs_confirmation=bool(confirmation_reasons),
            reasons=confirmation_reasons,
            sanitized=command,
        )

    # -- Filesystem -------------------------------------------------------
    def is_protected_path(self, path: str, extra_protected: Optional[List[str]] = None) -> bool:
        # Windows-style paths are checked textually so the guard works on any OS
        # (a Windows path cannot be resolved on Linux, and vice versa).
        raw = str(path or "").strip().replace("/", "\\").lower()
        if re.match(r"^[a-z]:\\?(?:$|\\)", raw):
            if re.fullmatch(r"[a-z]:\\?", raw):
                return True  # a drive root is never a target
            for segment in PROTECTED_PATH_SEGMENTS:
                if raw == segment or raw.startswith(segment.rstrip("\\") + "\\"):
                    return True
        try:
            resolved = str(Path(path).expanduser().resolve()).lower()
        except (OSError, RuntimeError, ValueError):
            return True  # cannot resolve -> treat as protected
        for root in ("/etc", "/usr", "/bin", "/sbin", "/boot", "/sys", "/proc", "/var/lib"):
            if resolved == root or resolved.startswith(root + "/"):
                return True
        for segment in PROTECTED_PATH_SEGMENTS + [p.lower() for p in (extra_protected or [])]:
            if resolved == segment or resolved.startswith(segment.rstrip("\\/") + "\\") \
                    or resolved.startswith(segment.rstrip("\\/") + "/"):
                return True
        # Drive roots are never writable targets
        if re.fullmatch(r"[a-z]:[\\/]?", resolved):
            return True
        return False

    def validate_fs_operation(self, operation: str, *paths: str,
                              extra_protected: Optional[List[str]] = None) -> ValidationResult:
        op = (operation or "").lower()
        if not paths:
            return ValidationResult(False, reasons=["No path supplied."])
        for path in paths:
            if not path:
                return ValidationResult(False, reasons=["Empty path supplied."])
            if self.is_protected_path(path, extra_protected):
                return ValidationResult(
                    False,
                    reasons=[f"Protected path cannot be modified: {path}"],
                )
        destructive = op in {"delete", "move", "overwrite", "cleanup"}
        if destructive and any(not p for p in paths):
            return ValidationResult(False, reasons=["Incomplete destructive operation."])
        return ValidationResult(True, needs_confirmation=destructive, sanitized=paths[0])

    def validate_executable(self, target: str) -> ValidationResult:
        """Block obviously unsafe launch targets (shell interpreters with args)."""
        lowered = (target or "").lower()
        dangerous = ("cmd.exe /c", "powershell -e", "powershell.exe -e", "certutil -urlcache")
        for needle in dangerous:
            if needle in lowered:
                return ValidationResult(False, reasons=["Unsafe executable invocation."])
        return ValidationResult(True, sanitized=target)


validator = CommandValidator()
