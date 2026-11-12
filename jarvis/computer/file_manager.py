"""File and folder operations with safety rails.

* Paths are validated before any write/delete; protected system paths are
  refused outright (see security/command_validator.py).
* Destructive operations return ``needs_confirmation=True`` metadata so the
  tool layer asks the user *before* anything is removed.
* Moves/deletes use the Recycle Bin when available so recovery is possible.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import config
from ..security.command_validator import validator
from .platform_support import support

log = logging.getLogger(__name__)

HOME = Path.home()
KNOWN_ROOTS = [HOME / "Desktop", HOME / "Downloads", HOME / "Documents",
               HOME / "Pictures", HOME / "Videos", HOME / "Music", HOME]

TEXT_SUFFIXES = {".txt", ".md", ".py", ".js", ".ts", ".json", ".csv", ".html",
                 ".css", ".yml", ".yaml", ".ini", ".log", ".bat", ".ps1", ".sh"}


class FileOperationError(RuntimeError):
    pass


def resolve(path: str) -> Path:
    """Expand ~, env vars and known folder aliases; return an absolute Path."""
    if not path:
        raise ValueError("No path supplied")
    raw = str(path).strip().strip('"').strip("'")
    aliases = {
        "desktop": HOME / "Desktop", "downloads": HOME / "Downloads",
        "documents": HOME / "Documents", "pictures": HOME / "Pictures",
        "videos": HOME / "Videos", "music": HOME / "Music", "home": HOME,
        "project folder": HOME / "Pictures",  # placeholder replaced by config
    }
    lowered = raw.lower()
    for alias, target in aliases.items():
        if lowered == alias or lowered.startswith(alias + os.sep) or lowered.startswith(alias + "/"):
            raw = str(target) + raw[len(alias):]
            break
    return Path(os.path.expandvars(os.path.expanduser(raw))).resolve()


def _guard(path, operation: str) -> None:
    """Refuse protected targets.

    The *raw* path string is checked first so Windows paths are still recognised
    when the code runs on a different OS (where resolve() would mangle them).
    """
    extra = config.get().security.protected_paths
    raw = str(path)
    if validator.is_protected_path(raw, extra):
        raise FileOperationError(f"Protected path cannot be modified: {raw}")
    result = validator.validate_fs_operation(operation, str(path), extra_protected=extra)
    if not result.allowed:
        raise FileOperationError("; ".join(result.reasons))


def _send_to_recycle_bin(path: Path) -> bool:
    if support.is_windows:
        try:
            from send2trash import send2trash  # type: ignore
            send2trash(str(path))
            return True
        except Exception:
            return False
    return False


# ---------------------------------------------------------------------------
# Create / write
# ---------------------------------------------------------------------------
def create_folder(path: str, name: Optional[str] = None) -> Dict[str, Any]:
    _guard(path, "create")
    target = resolve(path)
    if name:
        _guard(str(target / name), "create")
        target = target / name
    _guard(target, "create")
    if target.exists():
        return {"ok": False, "message": f"'{target}' already exists."}
    target.mkdir(parents=True, exist_ok=False)
    return {"ok": True, "verified": target.is_dir(),
            "message": f"Folder created at {target}.", "data": {"path": str(target)}}


def create_file(path: str, name: Optional[str] = None, content: str = "",
                overwrite: bool = False) -> Dict[str, Any]:
    _guard(path, "write")
    target = resolve(path) if name is None else resolve(path) / name
    _guard(target, "write")
    if target.exists() and not overwrite:
        return {"ok": False, "message": f"'{target.name}' already exists. "
                                        "Say 'overwrite' to replace it."}
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    verified = target.exists() and target.stat().st_size >= len(content.encode("utf-8"))
    return {"ok": True, "verified": verified,
            "message": f"Wrote {target.name} ({len(content)} characters).",
            "data": {"path": str(target)}}


def append_to_file(path: str, content: str) -> Dict[str, Any]:
    target = resolve(path)
    _guard(target, "write")
    with target.open("a", encoding="utf-8") as fh:
        fh.write(content)
    return {"ok": True, "verified": True, "message": f"Appended to {target.name}.",
            "data": {"path": str(target)}}


def read_file(path: str, max_chars: int = 8000) -> Dict[str, Any]:
    target = resolve(path)
    if not target.is_file():
        return {"ok": False, "message": f"'{target}' is not a readable file."}
    if target.stat().st_size > 5 * 1024 * 1024:
        return {"ok": False, "message": "File is larger than 5 MB; refusing to load it into context."}
    try:
        text = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"ok": False, "message": f"Could not read {target.name}: {exc}"}
    truncated = len(text) > max_chars
    return {"ok": True, "verified": True,
            "message": f"Read {target.name}" + (" (truncated)." if truncated else "."),
            "data": {"path": str(target), "content": text[:max_chars], "truncated": truncated}}


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------
def search_files(query: str, root: Optional[str] = None, extension: Optional[str] = None,
                 limit: int = 25, max_depth: int = 6) -> Dict[str, Any]:
    base = resolve(root) if root else HOME
    if not base.exists():
        return {"ok": False, "message": f"Search root does not exist: {base}"}
    query_lower = (query or "").lower().lstrip("*")
    matches: List[Dict[str, Any]] = []
    base_depth = len(base.parts)
    try:
        for dirpath, dirnames, filenames in os.walk(base, topdown=True):
            dirnames[:] = [d for d in dirnames if not d.startswith(("$", "."))
                           and d.lower() not in {"node_modules", "appdata", "windows",
                                                 "program files", "program files (x86)"}]
            if len(Path(dirpath).parts) - base_depth >= max_depth:
                dirnames[:] = []
            for filename in filenames:
                if extension and not filename.lower().endswith(extension.lower().lstrip("*")):
                    continue
                if query_lower and query_lower not in filename.lower():
                    continue
                full = Path(dirpath) / filename
                try:
                    stat = full.stat()
                except OSError:
                    continue
                matches.append({
                    "name": filename, "path": str(full),
                    "size_kb": round(stat.st_size / 1024, 1),
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="minutes"),
                })
                if len(matches) >= limit:
                    raise StopIteration
    except StopIteration:
        pass
    matches.sort(key=lambda m: m["modified"], reverse=True)
    if not matches:
        return {"ok": True, "verified": True,
                "message": f"No files matching '{query}' found under {base}.", "data": {"results": []}}
    listing = "; ".join(f"{m['name']} ({m['size_kb']} KB)" for m in matches[:6])
    return {"ok": True, "verified": True,
            "message": f"Found {len(matches)} match(es): {listing}",
            "data": {"results": matches, "root": str(base)}}


def latest_file(extension: str = ".pdf", root: Optional[str] = None) -> Dict[str, Any]:
    base = resolve(root) if root else HOME / "Downloads"
    candidates: List[Tuple[float, Path]] = []
    for search_root in ([base] if root else [HOME / "Downloads", HOME / "Desktop", HOME / "Documents"]):
        if not search_root.exists():
            continue
        for path in search_root.rglob(f"*{extension}"):
            if path.is_file():
                try:
                    candidates.append((path.stat().st_mtime, path))
                except OSError:
                    continue
    if not candidates:
        return {"ok": False, "message": f"No {extension} files found."}
    candidates.sort(reverse=True)
    newest = candidates[0][1]
    return {"ok": True, "verified": True,
            "message": f"Latest {extension} is {newest.name} at {newest}.",
            "data": {"path": str(newest),
                     "modified": datetime.fromtimestamp(candidates[0][0]).isoformat(timespec="minutes")}}


def find_large_files(min_gb: float = 1.0, root: Optional[str] = None,
                     limit: int = 20) -> Dict[str, Any]:
    base = resolve(root) if root else HOME
    threshold = min_gb * 1024 ** 3
    found: List[Dict[str, Any]] = []
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d.lower() not in
                       {"appdata", "node_modules", ".git", "windows"}]
        for filename in filenames:
            full = Path(dirpath) / filename
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size >= threshold:
                found.append({"path": str(full), "size_gb": round(size / 1024 ** 3, 2)})
    found.sort(key=lambda f: f["size_gb"], reverse=True)
    found = found[:limit]
    return {"ok": True, "verified": True,
            "message": (f"{len(found)} file(s) larger than {min_gb} GB: " +
                        ", ".join(f"{Path(f['path']).name} ({f['size_gb']} GB)" for f in found[:5]))
                       if found else f"No files larger than {min_gb} GB found.",
            "data": {"files": found}}


def find_duplicates(root: Optional[str] = None, limit: int = 20) -> Dict[str, Any]:
    """Hash-based duplicate detection (size pre-filter, then SHA-256)."""
    base = resolve(root) if root else HOME / "Downloads"
    by_size: Dict[int, List[Path]] = defaultdict(list)
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for filename in filenames:
            full = Path(dirpath) / filename
            try:
                size = full.stat().st_size
            except OSError:
                continue
            if size > 1024:  # ignore tiny files
                by_size[size].append(full)

    groups: List[Dict[str, Any]] = []
    for size, paths in by_size.items():
        if len(paths) < 2:
            continue
        by_hash: Dict[str, List[Path]] = defaultdict(list)
        for path in paths:
            digest = hashlib.sha256()
            try:
                with path.open("rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
            except OSError:
                continue
            by_hash[digest.hexdigest()].append(path)
        for digest, same in by_hash.items():
            if len(same) > 1:
                groups.append({"hash": digest[:16], "size_kb": round(size / 1024, 1),
                               "files": [str(p) for p in same]})
        if len(groups) >= limit:
            break
    wasted_kb = sum(g["size_kb"] * (len(g["files"]) - 1) for g in groups)
    return {"ok": True, "verified": True,
            "message": (f"{len(groups)} duplicate group(s) found, "
                        f"~{round(wasted_kb / 1024, 1)} MB reclaimable."
                        if groups else "No duplicates found."),
            "data": {"groups": groups[:limit], "reclaimable_mb": round(wasted_kb / 1024, 1)}}


# ---------------------------------------------------------------------------
# Modify / relocate / remove
# ---------------------------------------------------------------------------
def rename_path(path: str, new_name: str) -> Dict[str, Any]:
    _guard(path, "rename")
    source = resolve(path)
    if not source.exists():
        return {"ok": False, "message": f"'{source}' does not exist."}
    target = source.with_name(new_name)
    _guard(target, "write")
    if target.exists():
        return {"ok": False, "message": f"'{new_name}' already exists."}
    source.rename(target)
    return {"ok": True, "verified": target.exists(),
            "message": f"Renamed to {target.name}.", "data": {"path": str(target)}}


def move_path(source: str, destination: str) -> Dict[str, Any]:
    _guard(source, "move")
    _guard(destination, "move")
    src, dst = resolve(source), resolve(destination)
    if not src.exists():
        return {"ok": False, "message": f"'{src}' does not exist."}
    _guard(dst, "move")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_dir() and src.is_file():
        dst = dst / src.name
    shutil.move(str(src), str(dst))
    return {"ok": True, "verified": dst.exists(),
            "message": f"Moved {src.name} to {dst}.", "data": {"path": str(dst)}}


def copy_path(source: str, destination: str) -> Dict[str, Any]:
    _guard(destination, "copy")
    src, dst = resolve(source), resolve(destination)
    if not src.exists():
        return {"ok": False, "message": f"'{src}' does not exist."}
    _guard(dst, "write")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)
    return {"ok": True, "verified": dst.exists(),
            "message": f"Copied {src.name} to {dst}.", "data": {"path": str(dst)}}


def delete_path(path: str, permanent: bool = False) -> Dict[str, Any]:
    """Delete a file/folder. The tool layer requires confirmation before calling."""
    _guard(path, "delete")
    target = resolve(path)
    if not target.exists():
        return {"ok": False, "message": f"'{target}' does not exist."}
    _guard(target, "delete")
    if permanent:
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        how = "permanently deleted"
    else:
        if _send_to_recycle_bin(target):
            how = "moved to the Recycle Bin"
        else:
            # No Recycle Bin available (missing send2trash, or a non-Windows host).
            # The user already approved this deletion, so proceed — but say so.
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            how = ("deleted permanently (Recycle Bin unavailable — "
                   "install 'send2trash' to enable recoverable deletion)")
    return {"ok": True, "verified": not target.exists(),
            "message": f"{target.name} {how}.", "data": {"path": str(target)}}


def organise_downloads(by: str = "type", root: Optional[str] = None,
                       dry_run: bool = True) -> Dict[str, Any]:
    """Group files into subfolders by type/date. Defaults to a dry run."""
    base = resolve(root) if root else HOME / "Downloads"
    if not base.exists():
        return {"ok": False, "message": f"{base} does not exist."}
    categories = {
        "type": {
            "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"},
            "Documents": {".pdf", ".docx", ".doc", ".xlsx", ".pptx", ".txt", ".md", ".csv"},
            "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
            "Installers": {".exe", ".msi", ".msix"},
            "Audio": {".mp3", ".wav", ".flac", ".m4a"},
            "Video": {".mp4", ".mkv", ".mov", ".avi"},
            "Code": {".py", ".js", ".ts", ".json", ".html", ".css", ".java", ".c", ".cpp"},
        }
    }
    plan: List[Dict[str, str]] = []
    movers = categories.get(by, categories["type"])
    for item in base.iterdir():
        if item.is_dir():
            continue
        suffix = item.suffix.lower()
        destination = "Other"
        for folder, extensions in movers.items():
            if suffix in extensions:
                destination = folder
                break
        if by == "date":
            destination = datetime.fromtimestamp(item.stat().st_mtime).strftime("%Y-%m")
        plan.append({"file": item.name, "destination": destination})

    if dry_run:
        preview = ", ".join(f"{p['file']} → {p['destination']}" for p in plan[:8])
        return {"ok": True, "verified": True,
                "message": f"Dry run: {len(plan)} file(s) would be organised. {preview}",
                "data": {"plan": plan, "dry_run": True}}

    moved = 0
    for entry in plan:
        source = base / entry["file"]
        target_dir = base / entry["destination"]
        target_dir.mkdir(exist_ok=True)
        try:
            shutil.move(str(source), str(target_dir / entry["file"]))
            moved += 1
        except OSError as exc:
            log.warning("Could not move %s: %s", source, exc)
    return {"ok": True, "verified": moved == len(plan),
            "message": f"Organised {moved} file(s) in {base} by {by}.",
            "data": {"moved": moved, "total": len(plan)}}


def disk_usage_breakdown(root: Optional[str] = None, depth: int = 1) -> Dict[str, Any]:
    base = resolve(root) if root else HOME
    entries: List[Dict[str, Any]] = []
    try:
        children = list(base.iterdir())
    except OSError as exc:
        return {"ok": False, "message": f"Cannot list {base}: {exc}"}
    for child in children:
        try:
            if child.is_dir():
                size = sum(f.stat().st_size for f in child.rglob("*") if f.is_file())
            else:
                size = child.stat().st_size
        except OSError:
            continue
        entries.append({"name": child.name, "mb": round(size / 1024 ** 2, 1),
                        "is_dir": child.is_dir()})
    entries.sort(key=lambda e: e["mb"], reverse=True)
    top = entries[:10]
    return {"ok": True, "verified": True,
            "message": "Largest items: " + ", ".join(f"{e['name']} ({e['mb']} MB)" for e in top[:6]),
            "data": {"entries": top, "root": str(base)}}


def file_metadata(path: str) -> Dict[str, Any]:
    target = resolve(path)
    if not target.exists():
        return {"ok": False, "message": f"'{target}' does not exist."}
    stat = target.stat()
    return {"ok": True, "verified": True, "message": f"{target.name} metadata.",
            "data": {
                "path": str(target), "size_bytes": stat.st_size,
                "size_mb": round(stat.st_size / 1024 ** 2, 3),
                "created": datetime.fromtimestamp(stat.st_ctime).isoformat(timespec="seconds"),
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                "is_dir": target.is_dir(),
            }}
