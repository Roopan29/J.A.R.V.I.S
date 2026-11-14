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


# -- Windows app resolution -------------------------------------------------
# "open chrome" failed in the field because Chrome is not on PATH. These tests
# pin the fallbacks that make it work, using a fake winreg and fake filesystem.


def test_app_paths_reads_windows_registry(monkeypatch, tmp_path):
    import sys
    import types

    from jarvis.computer import app_control, platform_support

    exe = tmp_path / "chrome.exe"
    exe.write_bytes(b"MZ")

    class FakeKey:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    fake_winreg = types.SimpleNamespace(
        HKEY_LOCAL_MACHINE=1, HKEY_CURRENT_USER=2,
        OpenKey=lambda hive, sub: FakeKey(),
        QueryInfoKey=lambda key: (1, 0, 0),
        EnumKey=lambda key, index: "chrome.exe",
        QueryValueEx=lambda key, name: (str(exe), 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(platform_support.support, "is_windows", True)

    found = app_control._app_paths()
    assert found.get("chrome") == exe
    assert found.get("chrome.exe") == exe


def test_known_install_locations_finds_chrome(monkeypatch, tmp_path):
    from jarvis.computer import app_control, platform_support

    exe = tmp_path / "Google/Chrome/Application/chrome.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")

    monkeypatch.setattr(platform_support.support, "is_windows", True)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf86"))

    assert exe in app_control._known_install_locations("chrome")
    assert app_control._known_install_locations("chrome")


def test_spoken_browser_names_resolve_to_aliases():
    from jarvis.computer.app_control import _resolve_alias

    assert _resolve_alias("Google Chrome") == "chrome"
    assert _resolve_alias("chrome browser") == "chrome"
    assert _resolve_alias("Microsoft Edge") == "msedge"
    assert _resolve_alias("ms edge") == "msedge"
    assert _resolve_alias("windows terminal") == "wt"
