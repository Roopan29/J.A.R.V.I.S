"""Tool registry integrity and real filesystem operations (sandboxed)."""
from __future__ import annotations

import asyncio

import pytest

from jarvis.computer import file_manager, system_control


def test_registry_has_core_tools(registry):
    names = set(registry.names())
    required = {
        "open_application", "close_application", "create_folder", "create_file",
        "read_file", "search_files", "delete_path", "move_path", "copy_path",
        "type_text", "press_keys", "read_clipboard", "system_status",
        "list_processes", "run_powershell", "web_search", "research_topic",
        "remember", "recall_memory", "set_reminder", "set_timer", "create_note",
        "add_todo", "take_screenshot", "describe_screen", "run_workflow",
    }
    missing = required - names
    assert not missing, f"missing tools: {missing}"


def test_every_tool_has_valid_schema(registry):
    for spec in registry.all():
        schema = spec.json_schema()
        assert schema["type"] == "function"
        assert schema["function"]["name"] == spec.name
        assert schema["function"]["description"], spec.name
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        assert "properties" in params
        # every required key must exist in properties
        for required in params.get("required", []):
            assert required in params["properties"], f"{spec.name}: {required}"


def test_dangerous_tools_require_confirmation(registry):
    for name in ("delete_path", "power_action", "move_path", "run_powershell"):
        spec = registry.get(name)
        assert spec is not None and spec.requires_confirmation, name


@pytest.mark.asyncio
async def test_system_status_returns_real_data(registry):
    result = await registry.invoke("system_status", {"detail": "cpu"})
    assert result.ok
    assert result.data["cpu"]["percent"] is not None
    assert result.data["cpu"]["cores_logical"] >= 1
    assert isinstance(result.message, str) and result.message


@pytest.mark.asyncio
async def test_file_roundtrip(tmp_path, registry):
    folder = await registry.invoke("create_folder",
                                   {"path": str(tmp_path), "name": "Projects"})
    assert folder.ok, folder.message

    written = await registry.invoke("create_file",
                                    {"path": str(tmp_path / "Projects"),
                                     "name": "notes.txt",
                                     "content": "hello jarvis"})
    assert written.ok, written.message
    assert (tmp_path / "Projects" / "notes.txt").read_text() == "hello jarvis"

    read_back = await registry.invoke("read_file",
                                      {"path": str(tmp_path / "Projects" / "notes.txt")})
    assert read_back.ok
    assert "hello jarvis" in read_back.data["content"]

    found = await registry.invoke("search_files",
                                  {"query": "notes", "root": str(tmp_path)})
    assert found.ok and found.data["results"]

    renamed = await registry.invoke("rename_path",
                                    {"path": str(tmp_path / "Projects" / "notes.txt"),
                                     "new_name": "renamed.txt"})
    assert renamed.ok
    assert (tmp_path / "Projects" / "renamed.txt").exists()


@pytest.mark.asyncio
async def test_delete_requires_confirmation_then_runs(tmp_path):
    """delete_path must prompt; approving it deletes for real."""
    from jarvis.core.permissions import PermissionManager
    from jarvis.core.tool_registry import ToolRegistry

    manager = PermissionManager(timeout=5)
    registry = ToolRegistry(manager)
    for spec in __import__("jarvis.core.tool_registry", fromlist=["registry"]).registry.all():
        registry.register(spec)

    target = tmp_path / "doomed.txt"
    target.write_text("bye")

    async def approve():
        await asyncio.sleep(0.1)
        manager.resolve_any(True)

    asyncio.create_task(approve())
    result = await registry.invoke("delete_path", {"path": str(target)})
    assert result.ok, result.message
    assert not target.exists()


@pytest.mark.asyncio
async def test_protected_path_delete_is_refused(tmp_path):
    # Direct module call bypasses confirmation but must still be refused.
    from jarvis.computer.file_manager import FileOperationError, delete_path
    with pytest.raises(FileOperationError):
        delete_path("C:\\Windows\\System32\\kernel32.dll")


def test_latest_file_and_large_files(tmp_path):
    (tmp_path / "a.pdf").write_bytes(b"x" * 10)
    (tmp_path / "b.pdf").write_bytes(b"x" * 20)
    result = file_manager.latest_file(".pdf", str(tmp_path))
    assert result["ok"] is True

    big = tmp_path / "big.bin"
    big.write_bytes(b"0" * 2048)
    large = file_manager.find_large_files(0.000001, str(tmp_path))
    assert large["ok"] and large["data"]["files"]


def test_organise_downloads_dry_run_does_not_move(tmp_path):
    (tmp_path / "photo.png").write_bytes(b"x")
    (tmp_path / "doc.pdf").write_bytes(b"x")
    result = file_manager.organise_downloads("type", str(tmp_path), dry_run=True)
    assert result["ok"]
    assert (tmp_path / "photo.png").exists()
    assert not (tmp_path / "Images").exists()

    moved = file_manager.organise_downloads("type", str(tmp_path), dry_run=False)
    assert moved["ok"]
    assert (tmp_path / "Images" / "photo.png").exists()


def test_find_duplicates_detects_copies(tmp_path):
    payload = b"duplicate content " * 200
    (tmp_path / "one.bin").write_bytes(payload)
    (tmp_path / "two.bin").write_bytes(payload)
    result = file_manager.find_duplicates(str(tmp_path))
    assert result["ok"]
    assert result["data"]["groups"], "identical files should be grouped"


def test_system_status_message_is_truthful(registry):
    data = system_control.full_snapshot()
    assert 0 <= data["memory"]["percent"] <= 100
    assert data["disk"], "at least one volume should be reported"
    if data["battery"]["available"]:
        assert 0 <= data["battery"]["percent"] <= 100
