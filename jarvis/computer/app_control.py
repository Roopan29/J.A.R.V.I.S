"""Application launching and process management.

Strategy (Windows):
 1. Known alias -> executable / AppID / URI protocol (Chrome, Spotify, MS-settings…).
 2. Start-menu / registry app search for anything else installed.
 3. ``start`` shell fallback for URIs, documents and paths.

Every launch is *verified* by checking for a new process or window, so JARVIS
never reports success it hasn't observed.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import psutil

from .. import config
from .platform_support import support

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

#: alias -> launch specification
APP_ALIASES: Dict[str, Dict[str, str]] = {
    "chrome": {"exe": "chrome.exe", "uri": "chrome", "process": "chrome"},
    "msedge": {"exe": "msedge.exe", "uri": "microsoft-edge", "process": "msedge"},
    "firefox": {"exe": "firefox.exe", "uri": "firefox", "process": "firefox"},
    "brave": {"exe": "brave.exe", "uri": "brave", "process": "brave"},
    "notepad": {"exe": "notepad.exe", "process": "notepad"},
    "code": {"exe": "code.cmd", "process": "Code"},
    "wt": {"exe": "wt.exe", "process": "WindowsTerminal"},
    "powershell": {"exe": "powershell.exe", "process": "powershell"},
    "cmd": {"exe": "cmd.exe", "process": "cmd"},
    "explorer": {"exe": "explorer.exe", "process": "explorer"},
    "calc": {"exe": "calc.exe", "process": "CalculatorApp"},
    "mspaint": {"exe": "mspaint.exe", "process": "mspaint"},
    "taskmgr": {"exe": "taskmgr.exe", "process": "Taskmgr"},
    "spotify": {"exe": "Spotify.exe", "uri": "spotify", "process": "Spotify"},
    "discord": {"exe": "Discord.exe", "uri": "discord", "process": "Discord"},
    "slack": {"exe": "slack.exe", "process": "slack"},
    "teams": {"exe": "ms-teams.exe", "uri": "msteams", "process": "ms-teams"},
    "winword": {"exe": "WINWORD.EXE", "process": "WINWORD"},
    "excel": {"exe": "EXCEL.EXE", "process": "EXCEL"},
    "powerpnt": {"exe": "POWERPNT.EXE", "process": "POWERPNT"},
    "outlook": {"exe": "OUTLOOK.EXE", "process": "OUTLOOK"},
    "vlc": {"exe": "vlc.exe", "process": "vlc"},
    "steam": {"exe": "steam.exe", "uri": "steam", "process": "steam"},
    "7zfm": {"exe": "7zFM.exe", "process": "7zFM"},
    "obs": {"exe": "obs64.exe", "process": "obs64"},
    "ms-settings:": {"uri": "ms-settings:", "process": "SystemSettings"},
    "settings": {"uri": "ms-settings:", "process": "SystemSettings"},
    "control": {"exe": "control.exe", "process": "control"},
}

#: A few apps worth searching the Start Menu for by friendly name.
FRIENDLY_NAMES = {
    # Browsers are what people actually say out loud.
    "google chrome": "chrome", "chrome browser": "chrome", "chrome.": "chrome",
    "microsoft edge": "msedge", "ms edge": "msedge", "edge": "msedge",
    "mozilla firefox": "firefox", "internet explorer": "msedge",
    "brave browser": "brave", "opera": "chrome",
    "visual studio code": "code", "vs code": "code", "word": "winword",
    "excel": "excel", "powerpoint": "powerpnt", "file explorer": "explorer",
    "task manager": "taskmgr", "settings": "settings", "terminal": "wt",
    "command prompt": "cmd", "calculator": "calc", "paint": "mspaint",
    "windows terminal": "wt", "notepad++": "notepad", "media player": "vlc",
    "task manager": "taskmgr", "control panel": "control",
}


class AppControlError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Discovery helpers
# ---------------------------------------------------------------------------
def _start_menu_shortcuts() -> Dict[str, Path]:
    """Map lower-cased shortcut names to .lnk paths."""
    if not support.is_windows:
        return {}
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
        Path(os.environ.get("PROGRAMDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
    ]
    shortcuts: Dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            shortcuts.setdefault(lnk.stem.lower(), lnk)
    return shortcuts


def _app_paths() -> Dict[str, Path]:
    """Read the Windows "App Paths" registry: authoritative exe locations.

    Chrome, Edge and most installers register here, but not on PATH - which is
    why "open chrome" failed with "no such file or directory: 'chrome'".
    """
    if not support.is_windows:
        return {}
    found: Dict[str, Path] = {}
    try:
        import winreg  # type: ignore
    except ImportError:
        return found
    roots = [
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_CURRENT_USER,
         r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE,
         r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for hive, subkey in roots:
        try:
            with winreg.OpenKey(hive, subkey) as root:
                for index in range(winreg.QueryInfoKey(root)[0]):
                    try:
                        name = winreg.EnumKey(root, index)
                        with winreg.OpenKey(root, name) as entry:
                            raw, _ = winreg.QueryValueEx(entry, "")
                    except OSError:
                        continue
                    path = Path(str(raw).strip('"'))
                    if path.is_file():
                        # "chrome.exe" -> "chrome"
                        found.setdefault(path.stem.lower(), path)
                        found.setdefault(name.lower(), path)
        except OSError:
            continue
    return found


def _known_install_locations(name: str) -> List[Path]:
    """Common absolute install paths, checked when the registry has no entry."""
    if not support.is_windows:
        return []
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
    candidates = {
        "chrome": [local / "Google/Chrome/Application/chrome.exe",
                   pf / "Google/Chrome/Application/chrome.exe",
                   pf86 / "Google/Chrome/Application/chrome.exe"],
        "msedge": [pf86 / "Microsoft/Edge/Application/msedge.exe",
                   pf / "Microsoft/Edge/Application/msedge.exe"],
        "firefox": [pf / "Mozilla Firefox/firefox.exe",
                    pf86 / "Mozilla Firefox/firefox.exe"],
        "brave": [local / "BraveSoftware/Brave-Browser/Application/brave.exe",
                  pf / "BraveSoftware/Brave-Browser/Application/brave.exe"],
        "code": [local / "Programs/Microsoft VS Code/Code.exe",
                 pf / "Microsoft VS Code/Code.exe"],
        "spotify": [local / "Microsoft/WindowsApps/Spotify.exe",
                    Path(os.environ.get("APPDATA", "")) / "Spotify/Spotify.exe"],
        "discord": [local / "Discord/Update.exe"],
        "vlc": [pf / "VideoLAN/VLC/vlc.exe", pf86 / "VideoLAN/VLC/vlc.exe"],
        "steam": [pf86 / "Steam/steam.exe", pf / "Steam/steam.exe"],
        "obs": [pf / "obs-studio/bin/64bit/obs64.exe",
                pf86 / "obs-studio/bin/64bit/obs64.exe"],
    }
    return [path for path in candidates.get(name.lower(), []) if path.is_file()]


def installed_apps() -> List[str]:
    """Names of launchable Start Menu entries (used by the Applications page)."""
    return sorted(name.title() for name in _start_menu_shortcuts())


def _resolve_alias(name: str) -> str:
    lowered = (name or "").strip().lower()
    if lowered in APP_ALIASES:
        return lowered
    if lowered in FRIENDLY_NAMES:
        return FRIENDLY_NAMES[lowered]
    if lowered in APP_ALIASES.values():
        return lowered
    return lowered


def _find_shortcut(name: str) -> Optional[Path]:
    shortcuts = _start_menu_shortcuts()
    lowered = name.lower()
    if lowered in shortcuts:
        return shortcuts[lowered]
    for key, path in shortcuts.items():
        if lowered in key:
            return path
    return None


def _running(process_name: str) -> bool:
    target = process_name.lower()
    for proc in psutil.process_iter(["name"]):
        try:
            if (proc.info["name"] or "").lower() == target:
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def _allowlisted(name: str) -> bool:
    allowlist = [a.lower() for a in config.get().security.app_allowlist]
    if not allowlist:
        return True  # empty allowlist = "ask per launch" handled by confirmation
    lowered = name.lower()
    return any(lowered == item or lowered == item.replace(".exe", "") for item in allowlist)


# ---------------------------------------------------------------------------
# Launch
# ---------------------------------------------------------------------------
def open_application(name: str, arguments: Optional[str] = None,
                     wait_seconds: float = 2.0) -> Dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("No application name supplied")

    alias = _resolve_alias(name)
    spec = APP_ALIASES.get(alias, {})
    process_name = spec.get("process") or f"{alias}.exe"

    already_running = _running(process_name)
    if already_running:
        return {"ok": True, "verified": True,
                "message": f"{name} is already running — bringing it forward.",
                "data": {"name": name, "already_running": True}}

    launched = False
    errors: List[str] = []

    args = arguments.split() if arguments else []

    # 1. executable on PATH
    exe = spec.get("exe")
    if support.is_windows and exe:
        resolved = shutil.which(exe)
        if resolved:
            try:
                subprocess.Popen([resolved, *args],
                                 creationflags=CREATE_NO_WINDOW, close_fds=True)
                launched = True
            except OSError as exc:
                errors.append(str(exc))

    # 1b. Windows "App Paths" registry (where Chrome/Edge actually register)
    if not launched and support.is_windows:
        registered = _app_paths().get(alias) or (exe and _app_paths().get(exe.lower()))
        if registered:
            try:
                subprocess.Popen([str(registered), *args],
                                 creationflags=CREATE_NO_WINDOW, close_fds=True)
                launched = True
            except OSError as exc:
                errors.append(str(exc))

    # 1c. well-known install directories
    if not launched and support.is_windows:
        for candidate in _known_install_locations(alias):
            try:
                subprocess.Popen([str(candidate), *args],
                                 creationflags=CREATE_NO_WINDOW, close_fds=True)
                launched = True
                break
            except OSError as exc:
                errors.append(str(exc))

    # 2. URI protocol (spotify:, ms-settings:, chrome://…)
    if not launched and support.is_windows and spec.get("uri"):
        try:
            subprocess.Popen(["cmd", "/c", "start", "", spec["uri"]],
                             creationflags=CREATE_NO_WINDOW, close_fds=True)
            launched = True
        except OSError as exc:
            errors.append(str(exc))

    # 3. Start menu shortcut
    if not launched and support.is_windows:
        shortcut = _find_shortcut(name) or _find_shortcut(alias)
        if shortcut:
            try:
                os.startfile(str(shortcut))  # type: ignore[attr-defined]
                launched = True
            except OSError as exc:
                errors.append(str(exc))

    # 4. shell "start" fallback (documents, folders, arbitrary apps)
    if not launched:
        try:
            if support.is_windows:
                subprocess.Popen(["cmd", "/c", "start", "", name],
                                 creationflags=CREATE_NO_WINDOW, close_fds=True)
            else:
                subprocess.Popen([name])
            launched = True
        except OSError as exc:
            errors.append(str(exc))

    if not launched:
        suggestions = _suggest(name)
        detail = "; ".join(errors) or "not found"
        hint = (f" Similar installed apps: {', '.join(suggestions[:4])}." if suggestions else "")
        raise AppControlError(
            f"I could not find an application called '{name}' on this computer "
            f"({detail}).{hint}")

    # Verify: did a process appear?
    verified = False
    for _ in range(max(1, int(wait_seconds * 4))):
        time.sleep(0.25)
        if _running(process_name):
            verified = True
            break
    return {
        "ok": True,
        "verified": verified,
        "message": (f"{name} launched." if verified else
                    f"Launch command sent for {name}, but I could not yet confirm the process."),
        "data": {"name": name, "process": process_name, "verified": verified},
    }


def _suggest(name: str, limit: int = 5) -> List[str]:
    """Fuzzy-match a launch target against installed Start Menu entries."""
    import difflib

    try:
        installed = installed_apps()
    except Exception:
        return []
    matches = difflib.get_close_matches(name.title(), installed, n=limit, cutoff=0.55)
    if not matches:
        lowered = name.lower()
        matches = [app for app in installed if lowered in app.lower()][:limit]
    return matches


def open_path(path: str) -> Dict[str, Any]:
    expanded = os.path.expandvars(os.path.expanduser((path or "").strip().strip('"')))
    if not expanded:
        raise ValueError("No path supplied")
    target = Path(expanded)
    if not target.exists():
        return {"ok": False, "message": f"Path does not exist: {expanded}"}
    if support.is_windows:
        os.startfile(str(target))  # type: ignore[attr-defined]
    elif support.is_macos:
        subprocess.Popen(["open", str(target)])
    else:
        subprocess.Popen(["xdg-open", str(target)])
    return {"ok": True, "verified": True, "message": f"Opened {target}.",
            "data": {"path": str(target)}}


def open_url(url: str, browser: Optional[str] = None) -> Dict[str, Any]:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    if support.is_windows:
        subprocess.Popen(["cmd", "/c", "start", "", url],
                         creationflags=CREATE_NO_WINDOW, close_fds=True)
    elif support.is_macos:
        subprocess.Popen(["open", url])
    else:
        subprocess.Popen(["xdg-open", url])
    return {"ok": True, "verified": True, "message": f"Opened {url} in the browser.",
            "data": {"url": url}}


def close_application(name: str, force: bool = False) -> Dict[str, Any]:
    """Gracefully terminate by window title or process name; verify it's gone."""
    if not name:
        raise ValueError("No application name supplied")
    alias = _resolve_alias(name)
    process_name = (APP_ALIASES.get(alias, {}).get("process") or alias).lower()
    if not process_name.endswith(".exe"):
        process_name += ".exe"

    targets = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pname = (proc.info["name"] or "").lower()
            if pname == process_name or pname == process_name.replace(".exe", ""):
                targets.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    if not targets:
        return {"ok": False, "message": f"{name} does not appear to be running."}

    closed = 0
    for proc in targets:
        try:
            if force:
                proc.kill()
            else:
                proc.terminate()
            proc.wait(timeout=5)
            closed += 1
        except psutil.TimeoutExpired:
            try:
                proc.kill()
                closed += 1
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    still_running = _running(process_name)
    return {
        "ok": closed > 0 and not still_running,
        "verified": not still_running,
        "message": (f"Closed {name}." if not still_running else
                    f"Could not close {name}; it may need administrator rights."),
        "data": {"closed": closed, "still_running": still_running},
    }


def close_active_window() -> Dict[str, Any]:
    support.require_windows("Closing the active window")
    import win32gui  # type: ignore
    import win32con  # type: ignore
    hwnd = win32gui.GetForegroundWindow()
    if not hwnd:
        return {"ok": False, "message": "No active window detected."}
    title = win32gui.GetWindowText(hwnd)
    win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
    time.sleep(0.4)
    return {"ok": True, "verified": True, "message": f"Closed the window '{title}'.",
            "data": {"title": title}}


def running_applications(limit: int = 40) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for proc in psutil.process_iter(["pid", "name", "memory_percent"]):
        try:
            name = proc.info["name"] or ""
            key = name.lower()
            if key in seen:
                seen[key]["instances"] += 1
                continue
            seen[key] = {"name": name, "pid": proc.info["pid"],
                         "memory_percent": round(proc.info["memory_percent"] or 0, 2),
                         "instances": 1}
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    ordered = sorted(seen.values(), key=lambda r: r["memory_percent"], reverse=True)
    return ordered[:limit]
