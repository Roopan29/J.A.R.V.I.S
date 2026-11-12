"""Filesystem tools. Destructive tools are confirmation-gated."""
from __future__ import annotations

from ...computer import file_manager
from ...computer.platform_support import NotSupported
from ...database.models import ActionResult
from ..permissions import Capability
from ..tool_registry import ToolSpec, registry


def _wrap(fn, *args, **kwargs) -> ActionResult:
    try:
        result = fn(*args, **kwargs)
    except NotSupported as exc:
        return ActionResult(ok=False, message=str(exc))
    except (OSError, ValueError) as exc:
        return ActionResult(ok=False, message=f"{type(exc).__name__}: {exc}")
    if isinstance(result, dict):
        return ActionResult(ok=bool(result.get("ok", True)),
                            message=result.get("message", ""),
                            data=result.get("data", {}),
                            verified=bool(result.get("verified", False)))
    return ActionResult(ok=True, message=str(result))


_OBJ = {"type": "object"}


registry.register(ToolSpec(
    name="create_folder",
    description="Create a new folder. Understands aliases like Desktop, Downloads, Documents.",
    parameters={"type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Parent folder or full folder path"},
                    "name": {"type": "string", "description": "Folder name (omit if path is complete)"},
                },
                "required": ["path"]},
    handler=lambda path, name=None: _wrap(file_manager.create_folder, path, name),
    category="files", capability=Capability.FILE_WRITE,
))

registry.register(ToolSpec(
    name="create_file",
    description="Create a text file with content.",
    parameters={"type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Folder or full file path"},
                    "name": {"type": "string", "description": "File name incl. extension"},
                    "content": {"type": "string", "description": "Text content"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path"]},
    handler=lambda path, name=None, content="", overwrite=False:
        _wrap(file_manager.create_file, path, name, content, overwrite),
    category="files", capability=Capability.FILE_WRITE,
))

registry.register(ToolSpec(
    name="read_file",
    description="Read the text content of a file.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string"}, "max_chars": {"type": "integer"}},
                "required": ["path"]},
    handler=lambda path, max_chars=8000: _wrap(file_manager.read_file, path, max_chars),
    category="files",
))

registry.register(ToolSpec(
    name="search_files",
    description="Search for files by name fragment under a folder (default: user home).",
    parameters={"type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "root": {"type": "string"},
                    "extension": {"type": "string", "description": "e.g. '.pdf'"},
                    "limit": {"type": "integer", "default": 25},
                },
                "required": ["query"]},
    handler=lambda query, root=None, extension=None, limit=25:
        _wrap(file_manager.search_files, query, root, extension, limit),
    category="files",
))

registry.register(ToolSpec(
    name="latest_file",
    description="Find the most recently modified file of a given type.",
    parameters={"type": "object",
                "properties": {"extension": {"type": "string", "default": ".pdf"},
                               "root": {"type": "string"}}},
    handler=lambda extension=".pdf", root=None: _wrap(file_manager.latest_file, extension, root),
    category="files",
))

registry.register(ToolSpec(
    name="find_large_files",
    description="List files larger than a threshold size.",
    parameters={"type": "object",
                "properties": {"min_gb": {"type": "number", "default": 1.0},
                               "root": {"type": "string"},
                               "limit": {"type": "integer", "default": 20}}},
    handler=lambda min_gb=1.0, root=None, limit=20:
        _wrap(file_manager.find_large_files, min_gb, root, limit),
    category="files",
))

registry.register(ToolSpec(
    name="find_duplicates",
    description="Detect duplicate files by content hash and report reclaimable space.",
    parameters={"type": "object",
                "properties": {"root": {"type": "string"}, "limit": {"type": "integer"}}},
    handler=lambda root=None, limit=20: _wrap(file_manager.find_duplicates, root, limit),
    category="files",
))

registry.register(ToolSpec(
    name="rename_path",
    description="Rename a file or folder.",
    parameters={"type": "object",
                "properties": {"path": {"type": "string"}, "new_name": {"type": "string"}},
                "required": ["path", "new_name"]},
    handler=lambda path, new_name: _wrap(file_manager.rename_path, path, new_name),
    category="files", capability=Capability.FILE_WRITE, risk="medium",
))

registry.register(ToolSpec(
    name="move_path",
    description="Move a file or folder to another location.",
    parameters={"type": "object",
                "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
                "required": ["source", "destination"]},
    handler=lambda source, destination: _wrap(file_manager.move_path, source, destination),
    category="files", capability=Capability.FILE_WRITE, risk="medium",
    requires_confirmation=True,
    confirmation_template="Move '{source}' to '{destination}'?",
))

registry.register(ToolSpec(
    name="copy_path",
    description="Copy a file or folder.",
    parameters={"type": "object",
                "properties": {"source": {"type": "string"}, "destination": {"type": "string"}},
                "required": ["source", "destination"]},
    handler=lambda source, destination: _wrap(file_manager.copy_path, source, destination),
    category="files", capability=Capability.FILE_WRITE,
))

registry.register(ToolSpec(
    name="delete_path",
    description="Delete a file or folder. Moves to the Recycle Bin unless permanent=true. "
                "Always requires confirmation.",
    parameters={"type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "permanent": {"type": "boolean", "default": False,
                                  "description": "Permanently delete instead of using the Recycle Bin"},
                },
                "required": ["path"]},
    handler=lambda path, permanent=False: _wrap(file_manager.delete_path, path, permanent),
    category="files", capability=Capability.FILE_WRITE, risk="high",
    requires_confirmation=True, dangerous=True,
    confirmation_template="Delete '{path}'? (permanent if you asked for permanent)"
))

registry.register(ToolSpec(
    name="organise_downloads",
    description="Organise the Downloads folder by file type or date. Dry run by default.",
    parameters={"type": "object",
                "properties": {
                    "by": {"type": "string", "enum": ["type", "date"], "default": "type"},
                    "root": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": True},
                }},
    handler=lambda by="type", root=None, dry_run=True:
        _wrap(file_manager.organise_downloads, by, root, dry_run),
    category="files", capability=Capability.FILE_WRITE, risk="medium",
))

registry.register(ToolSpec(
    name="disk_usage_breakdown",
    description="Show the largest folders/files inside a directory.",
    parameters={"type": "object", "properties": {"root": {"type": "string"}}},
    handler=lambda root=None: _wrap(file_manager.disk_usage_breakdown, root),
    category="files",
))

registry.register(ToolSpec(
    name="file_metadata",
    description="Get size, timestamps and type information for a path.",
    parameters={"type": "object", "properties": {"path": {"type": "string"}},
                "required": ["path"]},
    handler=lambda path: _wrap(file_manager.file_metadata, path),
    category="files",
))
