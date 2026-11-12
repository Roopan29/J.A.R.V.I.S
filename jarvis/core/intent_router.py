"""Deterministic intent routing for well-known commands.

WHY: a local 7B model is slow and sometimes sloppy at tool calling. Anything
that can be handled exactly (open Chrome, set a timer, lock the PC, check
CPU…) is routed here first, instantly, with no model round-trip. Everything
else falls through to the LLM with the full tool catalogue.

This is *not* a fake AI layer: it only matches explicit command shapes and
returns the matching tool + arguments. When nothing matches, the LLM decides.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Optional


@dataclass
class Intent:
    name: str = "unknown"
    tool: Optional[str] = None
    arguments: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0
    speak: str = ""          # optional canned confirmation
    requires_ai: bool = False
    notes: str = ""


_APP_ALIASES = {
    "chrome": "chrome", "google chrome": "chrome",
    "edge": "msedge", "microsoft edge": "msedge",
    "firefox": "firefox", "brave": "brave",
    "notepad": "notepad", "notepad++": "notepad++",
    "vs code": "code", "vscode": "code", "visual studio code": "code", "code": "code",
    "terminal": "wt", "windows terminal": "wt", "powershell": "powershell",
    "command prompt": "cmd", "cmd": "cmd",
    "spotify": "spotify", "discord": "discord", "slack": "slack", "teams": "teams",
    "word": "winword", "excel": "excel", "powerpoint": "powerpnt", "outlook": "outlook",
    "explorer": "explorer", "file explorer": "explorer", "files": "explorer",
    "calculator": "calc", "calc": "calc", "paint": "mspaint",
    "task manager": "taskmgr", "settings": "ms-settings:",
    "steam": "steam", "vlc": "vlc", "7zip": "7zfm",
}

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30,
    "forty-five": 45, "an": 1, "a": 1, "half": 0.5,
}

_UNIT_SECONDS = {
    "second": 1, "seconds": 1, "sec": 1, "secs": 1,
    "minute": 60, "minutes": 60, "min": 60, "mins": 60,
    "hour": 3600, "hours": 3600, "hr": 3600, "hrs": 3600,
}


class IntentRouter:
    """Rule based first pass. Returns ``Intent(requires_ai=True)`` when unsure."""

    def __init__(self) -> None:
        self._custom = None  # set by automation.custom_commands

    # -- helpers ----------------------------------------------------------
    @staticmethod
    def _norm(text: str) -> str:
        return re.sub(r"\s+", " ", (text or "").strip().lower())

    @staticmethod
    def _extract_app(text: str) -> Optional[str]:
        for alias in sorted(_APP_ALIASES, key=len, reverse=True):
            if re.search(rf"\b{re.escape(alias)}\b", text):
                return _APP_ALIASES[alias]
        return None

    @staticmethod
    def _parse_quantity(text: str) -> Optional[float]:
        match = re.search(r"(\d+(?:\.\d+)?)\s*(second|seconds|secs?|minutes?|mins?|hours?|hrs?)",
                          text)
        if match:
            return float(match.group(1)) * _UNIT_SECONDS[match.group(2).rstrip("s") + "s"] \
                if match.group(2).rstrip("s") + "s" in _UNIT_SECONDS else None
        match = re.search(r"(\w+(?:-\w+)?)\s+(second|seconds|minute|minutes|hour|hours)", text)
        if match and match.group(1) in _NUMBER_WORDS:
            unit = match.group(2)
            unit_key = unit if unit in _UNIT_SECONDS else unit + "s"
            return _NUMBER_WORDS[match.group(1)] * _UNIT_SECONDS.get(unit_key, 60)
        return None

    # -- main -------------------------------------------------------------
    def route(self, text: str) -> Intent:
        raw = (text or "").strip()
        t = self._norm(raw)
        if not t:
            return Intent(name="empty", requires_ai=False, speak="I did not catch that, sir.")

        # Greetings / small talk that should be answered instantly & in character
        if re.fullmatch(r"(hi|hey|hello|yo|good (morning|afternoon|evening))[\s.!]*", t):
            return Intent(name="greeting", confidence=0.95,
                          speak=self._greeting())

        if re.fullmatch(r"(thanks|thank you|cheers)[\s.!]*", t):
            return Intent(name="thanks", confidence=0.95,
                          speak="Always a pleasure, sir.")

        if re.search(r"\b(stop|cancel|halt|abort)\b.*\b(everything|speaking|that|it)\b", t) \
                or t in {"stop", "cancel", "emergency stop", "abort"}:
            return Intent(name="stop", confidence=0.9)

        # Timer
        if re.search(r"\b(set|start|create)?\s*(a\s+)?timer\b", t):
            seconds = self._parse_quantity(t)
            if seconds:
                label = re.sub(r".*timer\s*(for|of)?\s*", "", raw, flags=re.I).strip() or "Timer"
                return Intent(name="timer", tool="set_timer",
                              arguments={"seconds": int(seconds), "label": label},
                              confidence=0.92,
                              speak=f"Timer set for {_human_duration(seconds)}.")
            return Intent(name="timer_ambiguous", confidence=0.5,
                          speak="For how long should I set the timer, sir?")

        # Reminder
        match = re.search(r"\bremind me (?:to|about|that)\s+(.+)", t)
        if match:
            body = match.group(1)
            due = self._parse_due_time(body)
            text_only = re.split(r"\s+(?:at|in|by)\s+\d", body)[0].strip() or body
            if due:
                return Intent(name="reminder", tool="set_reminder",
                              arguments={"message": text_only.title(), "due_at": due.isoformat()},
                              confidence=0.9,
                              speak=f"Reminder set for {due.strftime('%H:%M')}, sir.")
            return Intent(name="reminder", tool="set_reminder",
                          arguments={"message": text_only.title()},
                          confidence=0.6,
                          speak="I will remind you shortly; tell me a time if you need one.")

        # Notes / todos
        match = re.search(r"\b(?:make|create|add|take)\s+(?:a\s+)?note\s*(?:about|that|saying|:)?\s*(.+)", t)
        if match:
            return Intent(name="note", tool="create_note",
                          arguments={"title": match.group(1)[:60].title(),
                                     "body": match.group(1)},
                          confidence=0.9, speak="Noted, sir.")
        match = re.search(r"\badd\s+(.+?)\s+to\s+my\s+(?:to.?do|task)\s*list\b", t)
        if match:
            return Intent(name="todo", tool="add_todo", arguments={"text": match.group(1)},
                          confidence=0.9, speak="Added to your list, sir.")

        # Screenshot
        if re.search(r"\btake?\s+(a\s+)?screenshot|\bcapture (the )?screen\b", t):
            return Intent(name="screenshot", tool="take_screenshot", confidence=0.92,
                          speak="Capturing the screen now.")

        # System queries ---------------------------------------------------
        if re.search(r"\b(cpu|processor)\b.*\b(usage|load|percent)|how('s| is) (the )?cpu", t):
            return Intent(name="sys_cpu", tool="system_status", arguments={"detail": "cpu"},
                          confidence=0.9)
        if re.search(r"\b(ram|memory)\b.*\b(usage|load)|how much (ram|memory)", t):
            return Intent(name="sys_ram", tool="system_status", arguments={"detail": "memory"},
                          confidence=0.9)
        if re.search(r"\bbattery\b", t) and re.search(r"\b(level|status|charge|health)\b", t):
            return Intent(name="sys_battery", tool="system_status", arguments={"detail": "battery"},
                          confidence=0.9)
        if re.search(r"\b(gpu|graphics card)\b", t):
            return Intent(name="sys_gpu", tool="system_status", arguments={"detail": "gpu"},
                          confidence=0.85)
        if re.search(r"\b(disk|storage)\b.*\b(space|free|usage)|how much (disk|storage)", t):
            return Intent(name="sys_disk", tool="system_status", arguments={"detail": "disk"},
                          confidence=0.88)
        if re.search(r"\b(full|complete) system (status|report|check)\b|system status", t):
            return Intent(name="sys_all", tool="system_status", arguments={"detail": "all"},
                          confidence=0.9)
        if re.search(r"\b(what.s|whats|what is) my ip\b|\bip (address|info)\b", t):
            return Intent(name="sys_ip", tool="network_info", confidence=0.9)
        if re.search(r"\b(running )?processes\b|\bwhat.s running\b", t):
            return Intent(name="sys_processes", tool="list_processes",
                          arguments={"limit": 15}, confidence=0.85)

        # Power / session ---------------------------------------------------
        if re.search(r"\block (the |my )?(computer|pc|laptop|screen|windows)\b", t):
            return Intent(name="lock", tool="lock_workstation", confidence=0.95,
                          speak="Locking the workstation.")
        if re.search(r"\b(shut ?down|power off) (the )?(computer|pc|laptop)\b", t):
            return Intent(name="shutdown", tool="power_action",
                          arguments={"action": "shutdown"}, confidence=0.9)
        if re.search(r"\b(restart|reboot) (the )?(computer|pc|laptop)\b", t):
            return Intent(name="restart", tool="power_action",
                          arguments={"action": "restart"}, confidence=0.9)
        if re.search(r"\b(what time is it|current time|the time)\b", t):
            return Intent(name="time", confidence=0.9,
                          speak=f"It is {datetime.now().strftime('%H:%M')}, sir.")
        if re.search(r"\b(what.s the date|today.s date|what day is it)\b", t):
            return Intent(name="date", confidence=0.9,
                          speak=f"Today is {datetime.now().strftime('%A, %d %B %Y')}.")

        # Clipboard
        if re.search(r"\b(copy|what.s) (the )?(selected|clipboard)\b", t):
            return Intent(name="clipboard_read", tool="read_clipboard", confidence=0.85)

        # Input control -----------------------------------------------------
        match = re.search(r"\btype\s+(?:out\s+)?[\"']?(.+?)[\"']?$", t)
        if match and re.search(r"\btype\b", t) and len(match.group(1)) > 1:
            return Intent(name="type_text", tool="type_text",
                          arguments={"text": match.group(1)}, confidence=0.75)

        # Window management -------------------------------------------------
        if re.search(r"\b(minimi[sz]e) all\b|\bshow (the )?desktop\b", t):
            return Intent(name="minimize_all", tool="minimize_all_windows", confidence=0.9)
        if re.search(r"\b(switch|go) to (the )?(previous|last) window\b|\balt.?tab\b", t):
            return Intent(name="switch_window", tool="switch_window", confidence=0.88)
        if re.search(r"\b(close|quit|exit) (the )?(current|active|this) (app|application|window)\b", t):
            return Intent(name="close_active", tool="close_active_window", confidence=0.9)
        match = re.search(r"\b(?:switch to|focus|activate)\s+([\w .+]{2,30})", t)
        if match and not re.search(r"\b(open|launch|start)\b", t):
            return Intent(name="focus_app", tool="focus_application",
                          arguments={"name": match.group(1).strip()}, confidence=0.8)

        # Application control ------------------------------------------------
        match = re.search(r"\b(open|launch|start|run)\s+(?:up\s+)?(?:the\s+)?([\w .+:]{2,40})", t)
        if match:
            target_text = match.group(2).strip()
            app = self._extract_app(target_text)
            if app:
                return Intent(name="open_app", tool="open_application",
                              arguments={"name": app}, confidence=0.92,
                              speak=f"Opening {target_text}.")
            if re.search(r"\b(folder|directory)\b", target_text):
                return Intent(name="open_folder", tool="open_path",
                              arguments={"path": target_text}, confidence=0.6)
            # "open <url or search>" -> browser search
            if re.search(r"\.(com|org|net|io|dev|ai|co\.uk)\b", target_text):
                return Intent(name="open_url", tool="open_url",
                              arguments={"url": target_text}, confidence=0.85)
            return Intent(name="open_unknown", requires_ai=True, confidence=0.4,
                          notes=target_text)

        # Browser search ------------------------------------------------------
        match = re.search(r"\b(search|google|look up|find)\s+(?:for\s+)?(.+)", t)
        if match and not re.search(r"\b(file|folder|document|pdf)\b", match.group(2)):
            query = match.group(2).strip()
            return Intent(name="web_search", tool="web_search",
                          arguments={"query": query}, confidence=0.85)

        # Research / summarise => AI + web
        if re.search(r"\b(research|investigate|explain|summar(ise|ize)|compare)\b", t):
            return Intent(name="research", requires_ai=True, confidence=0.7)

        # Explicit tool-call-ish phrases
        if re.search(r"\b(mute|unmute)\b", t):
            return Intent(name="mute", tool="set_volume",
                          arguments={"mute": True if "unmute" not in t else False},
                          confidence=0.9)
        match = re.search(r"\b(?:set )?volume (?:to )?(\d{1,3})\b", t)
        if match:
            return Intent(name="volume", tool="set_volume",
                          arguments={"level": int(match.group(1))}, confidence=0.9)
        match = re.search(r"\bbrightness (?:to )?(\d{1,3})\b", t)
        if match:
            return Intent(name="brightness", tool="set_brightness",
                          arguments={"level": int(match.group(1))}, confidence=0.85)

        return Intent(name="unknown", requires_ai=True, confidence=0.2)

    # -- greeting ---------------------------------------------------------
    @staticmethod
    def _greeting() -> str:
        from .. import config
        hour = datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        return f"Good {part}, {config.get().user_title}. JARVIS online and at your service."

    # -- time parsing -----------------------------------------------------
    @staticmethod
    def _parse_due_time(text: str) -> Optional[datetime]:
        now = datetime.now()
        # "in 10 minutes"
        match = re.search(r"\bin\s+(\d+)\s+(second|minute|hour|day)s?\b", text)
        if match:
            n = int(match.group(1))
            unit = match.group(2)
            delta = {"second": timedelta(seconds=n), "minute": timedelta(minutes=n),
                     "hour": timedelta(hours=n), "day": timedelta(days=n)}[unit]
            return now + delta
        # "at 8 pm" / "at 20:30"
        match = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", text)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            meridiem = (match.group(3) or "").lower()
            if meridiem == "pm" and hour < 12:
                hour += 12
            elif meridiem == "am" and hour == 12:
                hour = 0
            elif not meridiem and hour < 7:
                hour += 12  # "at 8" in the evening
            due = now.replace(hour=hour % 24, minute=minute, second=0, microsecond=0)
            if due < now:
                due += timedelta(days=1)
            return due
        return None


def _human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} seconds"
    if seconds < 3600:
        minutes, secs = divmod(seconds, 60)
        return f"{minutes} minute{'s' if minutes != 1 else ''}" + (f" {secs} seconds" if secs else "")
    hours, rest = divmod(seconds, 3600)
    return f"{hours} hour{'s' if hours != 1 else ''}" + (f" {rest // 60} minutes" if rest else "")


router = IntentRouter()
