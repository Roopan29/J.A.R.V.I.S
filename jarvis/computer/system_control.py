"""System telemetry and safe system control.

Every number returned here comes from a real sensor or OS API via psutil /
Win32 / nvidia-smi. Nothing is simulated: if a metric is unavailable the key
is simply absent and ``available`` lists what was measured.
"""
from __future__ import annotations

import logging
import platform
import shutil
import socket
import subprocess
import time
from datetime import datetime
from typing import Any, Dict, List, Optional

import psutil

from .. import config
from .platform_support import NotSupported, support

log = logging.getLogger(__name__)

_LOW_BATTERY = 20
_HIGH_CPU = 85
_LOW_DISK_GB = 5

_nvidia_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
def cpu_info() -> Dict[str, Any]:
    freq = psutil.cpu_freq()
    return {
        "percent": psutil.cpu_percent(interval=0.25),
        "per_core": psutil.cpu_percent(interval=0, percpu=True),
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "frequency_mhz": round(freq.current) if freq else None,
        "load_1_5_15": [round(x, 2) for x in psutil.getloadavg()] if hasattr(psutil, "getloadavg") else None,
    }


def memory_info() -> Dict[str, Any]:
    vm = psutil.virtual_memory()
    swap = psutil.swap_memory()
    return {
        "total_gb": round(vm.total / 1024 ** 3, 2),
        "used_gb": round(vm.used / 1024 ** 3, 2),
        "available_gb": round(vm.available / 1024 ** 3, 2),
        "percent": vm.percent,
        "swap_percent": swap.percent,
    }


def gpu_info() -> Dict[str, Any]:
    """GPU utilisation via nvidia-smi (NVIDIA) or psutil fallback (none)."""
    now = time.time()
    if _nvidia_cache["data"] and now - _nvidia_cache["ts"] < 2:
        return _nvidia_cache["data"]

    result: Dict[str, Any] = {"available": False, "vendor": None}
    if shutil.which("nvidia-smi"):
        try:
            output = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=4,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if output.returncode == 0 and output.stdout.strip():
                first = output.stdout.strip().splitlines()[0]
                parts = [p.strip() for p in first.split(",")]
                if len(parts) >= 5:
                    result = {
                        "available": True,
                        "vendor": "nvidia",
                        "name": parts[0],
                        "percent": float(parts[1]) if parts[1].replace(".", "").isdigit() else None,
                        "memory_used_mb": int(float(parts[2])),
                        "memory_total_mb": int(float(parts[3])),
                        "temperature_c": int(float(parts[4])),
                    }
        except (subprocess.SubprocessError, ValueError, OSError) as exc:
            log.debug("nvidia-smi query failed: %s", exc)
    _nvidia_cache.update(ts=now, data=result)
    return result


def battery_info() -> Dict[str, Any]:
    battery = psutil.sensors_battery()
    if battery is None:
        return {"available": False, "reason": "No battery detected (desktop or VM)."}
    seconds = battery.secsleft
    return {
        "available": True,
        "percent": round(battery.percent),
        "plugged": battery.power_plugged,
        "seconds_left": None if seconds in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) else seconds,
    }


def disk_info() -> List[Dict[str, Any]]:
    volumes: List[Dict[str, Any]] = []
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if part.device in seen:
            continue
        seen.add(part.device)
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        volumes.append({
            "device": part.device,
            "mount": part.mountpoint,
            "fstype": part.fstype,
            "total_gb": round(usage.total / 1024 ** 3, 1),
            "used_gb": round(usage.used / 1024 ** 3, 1),
            "free_gb": round(usage.free / 1024 ** 3, 1),
            "percent": usage.percent,
        })
    return volumes


def network_info() -> Dict[str, Any]:
    stats = psutil.net_io_counters()
    hostname = socket.gethostname()
    try:
        local_ip = socket.gethostbyname(hostname)
    except socket.gaierror:
        local_ip = ""
    external_ip = ""
    try:  # cheap UDP trick, no packets are sent
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(0.4)
        sock.connect(("8.8.8.8", 80))
        external_ip = sock.getsockname()[0]
        sock.close()
    except OSError:
        external_ip = local_ip
    interfaces = []
    for name, addrs in psutil.net_if_addrs().items():
        for addr in addrs:
            if addr.family == socket.AF_INET:
                interfaces.append({"name": name, "address": addr.address})
    return {
        "hostname": hostname,
        "local_ip": external_ip or local_ip,
        "bytes_sent_mb": round(stats.bytes_sent / 1024 ** 2, 1),
        "bytes_recv_mb": round(stats.bytes_recv / 1024 ** 2, 1),
        "interfaces": interfaces,
        "online": _internet_reachable(),
    }


def _internet_reachable(timeout: float = 1.0) -> bool:
    for host, port in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue
    return False


def uptime() -> Dict[str, Any]:
    seconds = int(time.time() - psutil.boot_time())
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)
    return {"seconds": seconds, "human": f"{days}d {hours}h {minutes}m"}


def full_snapshot() -> Dict[str, Any]:
    """Everything the dashboard left panel needs, in one call."""
    try:
        cpu = cpu_info()
    except Exception as exc:  # pragma: no cover
        log.warning("CPU metrics failed: %s", exc)
        cpu = {"percent": None}
    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "cpu": cpu,
        "memory": memory_info(),
        "gpu": gpu_info(),
        "battery": battery_info(),
        "disk": disk_info(),
        "network": network_info(),
        "uptime": uptime(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "python": platform.python_version(),
            "hostname": platform.node(),
        },
        "support": support.summary(),
    }


def system_status(detail: str = "all") -> Dict[str, Any]:
    """Human-readable status used by the ``system_status`` tool."""
    detail = (detail or "all").lower()
    parts: List[str] = []
    data: Dict[str, Any] = {}

    if detail in ("all", "cpu"):
        cpu = cpu_info()
        data["cpu"] = cpu
        parts.append(f"CPU at {cpu['percent']:.0f}% across {cpu['cores_logical']} logical cores")
    if detail in ("all", "memory"):
        mem = memory_info()
        data["memory"] = mem
        parts.append(f"RAM {mem['used_gb']} of {mem['total_gb']} GB ({mem['percent']:.0f}%)")
    if detail in ("all", "gpu"):
        gpu = gpu_info()
        data["gpu"] = gpu
        if gpu.get("available"):
            parts.append(f"GPU {gpu['name']} at {gpu.get('percent')}%, "
                         f"{gpu['memory_used_mb']} MB VRAM, {gpu['temperature_c']}°C")
        else:
            parts.append("No NVIDIA GPU telemetry available")
    if detail in ("all", "battery"):
        battery = battery_info()
        data["battery"] = battery
        if battery.get("available"):
            state = "charging" if battery["plugged"] else "on battery"
            parts.append(f"Battery {battery['percent']}% ({state})")
        else:
            parts.append("No battery detected")
    if detail in ("all", "disk"):
        disks = disk_info()
        data["disk"] = disks
        if disks:
            main = disks[0]
            parts.append(f"{main['device']} has {main['free_gb']} GB free of {main['total_gb']} GB")
    if detail in ("all",):
        net = network_info()
        data["network"] = net
        parts.append("network " + ("online" if net["online"] else "offline"))
        data["uptime"] = uptime()
        parts.append(f"uptime {data['uptime']['human']}")

    return {"ok": True, "message": "; ".join(parts) + ".", "data": data, "verified": True}


# ---------------------------------------------------------------------------
# Control actions
# ---------------------------------------------------------------------------
def _psutil_volume_windows(level: Optional[int]) -> bool:
    """Set master volume 0-100 on Windows via the Core Audio API."""
    try:
        from ctypes import cast, POINTER, c_float  # noqa: F401
        from comtypes import CLSCTX_ALL  # type: ignore
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume  # type: ignore
    except ImportError:
        return False
    try:
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(interface, POINTER(IAudioEndpointVolume))
        if level is not None:
            volume.SetMasterVolumeLevelScalar(max(0.0, min(1.0, level / 100)), None)
        return True
    except Exception as exc:  # pragma: no cover
        log.warning("pycaw volume control failed: %s", exc)
        return False


def _send_media_key(key: int) -> None:
    """Tap a media virtual key (0xAD mute, 0xAF vol up, 0xAE vol down)."""
    if not support.is_windows:
        raise NotSupported("Media keys require Windows.")
    import ctypes
    ctypes.windll.user32.keybd_event(key, 0, 0, 0)
    ctypes.windll.user32.keybd_event(key, 0, 2, 0)


def set_volume(level: Optional[int] = None, mute: Optional[bool] = None,
               delta: Optional[int] = None) -> Dict[str, Any]:
    if support.is_windows:
        if mute is not None:
            _send_media_key(0xAD)
            return {"ok": True, "message": "Toggled system mute.", "verified": False}
        if level is not None:
            if _psutil_volume_windows(level):
                return {"ok": True, "message": f"Volume set to {level}%.", "verified": False}
            raise NotSupported(
                "Precise volume control needs the 'pycaw' package "
                "(pip install pycaw comtypes).")
        if delta:
            for _ in range(abs(delta) // 2 or 1):
                _send_media_key(0xAF if delta > 0 else 0xAE)
            return {"ok": True, "message": "Volume adjusted.", "verified": False}

    if support.is_linux and shutil.which("amixer"):
        if mute is not None:
            subprocess.run(["amixer", "-q", "set", "Master", "toggle"], check=False)
            return {"ok": True, "message": "Toggled mute.", "verified": False}
        if level is not None:
            subprocess.run(["amixer", "-q", "set", "Master", f"{level}%"], check=False)
            return {"ok": True, "message": f"Volume set to {level}%.", "verified": False}
    raise NotSupported("Volume control is unavailable on this platform.")


def set_brightness(level: int) -> Dict[str, Any]:
    if support.is_windows:
        command = (
            f"(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods)"
            f".WmiSetBrightness(1,{max(0, min(100, level))})"
        )
        try:
            result = subprocess.run(
                [support.powershell or "powershell", "-NoProfile", "-NonInteractive", "-Command", command],
                capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 0:
                return {"ok": True, "message": f"Brightness set to {level}%.",
                        "verified": False}
            raise NotSupported("This display does not expose brightness via WMI "
                               "(external monitors often don't).")
        except subprocess.SubprocessError as exc:
            raise NotSupported(f"Brightness control failed: {exc}")
    if support.is_linux and shutil.which("brightnessctl"):
        subprocess.run(["brightnessctl", "set", f"{level}%"], check=False)
        return {"ok": True, "message": f"Brightness set to {level}%.", "verified": False}
    raise NotSupported("Brightness control is unavailable on this platform.")


def lock_workstation() -> Dict[str, Any]:
    support.require_windows("Locking the workstation")
    import ctypes
    if not ctypes.windll.user32.LockWorkStation():
        raise RuntimeError("LockWorkStation() failed")
    return {"ok": True, "message": "Workstation locked.", "verified": True}


def power_action(action: str) -> Dict[str, Any]:
    """Shutdown / restart / sleep / hibernate. Caller must confirm first."""
    action = (action or "").lower()
    if not config.get().security.allow_shutdown:
        raise NotSupported("Power actions are disabled in Settings → Security.")
    support.require_windows("Power actions")
    flags = {"shutdown": ["/s", "/t", "30"], "restart": ["/r", "/t", "30"],
             "abort": ["/a"]}
    if action == "sleep":
        import ctypes
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
        return {"ok": True, "message": "Suspending the computer.", "verified": False}
    if action == "hibernate":
        subprocess.run(["shutdown", "/h"], check=False)
        return {"ok": True, "message": "Hibernating.", "verified": False}
    if action in flags:
        subprocess.run(["shutdown", *flags[action]], check=False)
        return {"ok": True,
                "message": f"{action.title()} scheduled in 30 seconds. Run 'shutdown /a' to cancel.",
                "verified": False}
    raise ValueError(f"Unknown power action '{action}'")


def list_processes(limit: int = 15, sort_by: str = "memory") -> Dict[str, Any]:
    rows = []
    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            rows.append({
                "pid": info["pid"],
                "name": info["name"] or "?",
                "cpu": round(info["cpu_percent"] or 0.0, 1),
                "memory_percent": round(info["memory_percent"] or 0.0, 2),
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = "cpu" if sort_by == "cpu" else "memory_percent"
    rows.sort(key=lambda r: r[key], reverse=True)
    top = rows[: max(1, limit)]
    summary = ", ".join(f"{r['name']} ({r[key]}%)" for r in top[:8])
    return {"ok": True, "message": f"Top processes by {sort_by}: {summary}.",
            "data": {"processes": top, "total": len(rows)}, "verified": True}


def open_task_manager() -> Dict[str, Any]:
    support.require_windows("Task Manager")
    subprocess.Popen(["taskmgr.exe"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return {"ok": True, "message": "Task Manager opened.", "verified": True}


# ---------------------------------------------------------------------------
# Proactive health checks
# ---------------------------------------------------------------------------
def health_warnings() -> List[Dict[str, str]]:
    warnings: List[Dict[str, str]] = []
    battery = battery_info()
    if battery.get("available") and not battery["plugged"] and battery["percent"] <= _LOW_BATTERY:
        warnings.append({"level": "warning", "title": "Low battery",
                         "message": f"Battery at {battery['percent']}% and discharging."})
    cpu = psutil.cpu_percent(interval=0.4)
    if cpu >= _HIGH_CPU:
        warnings.append({"level": "warning", "title": "High CPU",
                         "message": f"CPU utilisation is {cpu:.0f}%."})
    memory = psutil.virtual_memory()
    if memory.percent >= 90:
        warnings.append({"level": "warning", "title": "High memory",
                         "message": f"RAM usage is {memory.percent:.0f}%."})
    for disk in disk_info():
        if disk["free_gb"] <= _LOW_DISK_GB:
            warnings.append({"level": "critical", "title": "Low disk space",
                             "message": f"{disk['device']} has only {disk['free_gb']} GB free."})
    return warnings
