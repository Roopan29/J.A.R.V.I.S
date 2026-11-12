"""API integration tests using FastAPI's TestClient (real app, real DB)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from jarvis.api.server import create_app
    with TestClient(create_app()) as test_client:
        yield test_client


def test_health(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["version"]
    assert body["data"]["tools"] > 40


def test_metrics_are_real(client):
    body = client.get("/api/metrics").json()["data"]
    assert "cpu" in body and body["cpu"]["cores_logical"] >= 1
    assert 0 <= body["memory"]["percent"] <= 100


def test_tools_endpoint(client):
    body = client.get("/api/tools").json()["data"]
    names = {tool["name"] for tool in body["tools"]}
    assert "open_application" in names
    assert "power_action" in names


def test_command_endpoint_uses_router(client):
    response = client.post("/api/command", json={"text": "what time is it", "speak": False})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["intent"] == "time"
    assert ":" in data["text"]


def test_settings_roundtrip_never_leaks_secrets(client):
    body = client.get("/api/settings").json()["data"]
    assert "secrets" in body
    assert "api_key" not in str(body.get("ai", {})).lower()
    updated = client.put("/api/settings",
                         json={"section": "voice", "values": {"greeting": "At your service."}})
    assert updated.status_code == 200
    assert updated.json()["data"]["voice"]["greeting"] == "At your service."


def test_tool_invoke_endpoint(client):
    response = client.post("/api/tools/invoke",
                           json={"name": "system_status", "arguments": {"detail": "cpu"}})
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["cpu"]["percent"] is not None


def test_powershell_endpoint_blocks_dangerous(client):
    response = client.get("/api/system/powershell",
                          params={"command": "mimikatz.exe sekurlsa::logonpasswords"})
    assert response.status_code == 403


def test_permission_resolution_flow(client):
    import asyncio
    from jarvis.core.permissions import permissions

    async def scenario():
        task = asyncio.create_task(
            permissions.request("test", "Do the thing?", timeout=5))
        await asyncio.sleep(0.05)
        pending = client.get("/api/permissions/pending").json()["data"]["requests"]
        assert pending, "request should be listed"
        response = client.post(f"/api/permissions/{pending[0]['id']}",
                               json={"approved": True})
        assert response.status_code == 200
        assert await task is True

    asyncio.get_event_loop().run_until_complete(scenario())


def test_dashboard_is_served(client):
    page = client.get("/")
    assert page.status_code == 200
    assert "J.A.R.V.I.S." in page.text
    for asset in ("/static/styles/main.css", "/static/js/core.js",
                  "/static/js/pages.js", "/static/js/app.js"):
        assert client.get(asset).status_code == 200, asset


def test_unknown_api_route_is_404(client):
    assert client.get("/api/definitely-not-a-route").status_code == 404


def test_emergency_stop(client):
    response = client.post("/api/emergency-stop")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_websocket_streams_events(client):
    """The dashboard's live channel must greet and then stream real events."""
    import json
    from jarvis.event_bus import bus

    with client.websocket_connect("/ws") as ws:
        hello = json.loads(ws.receive_text())
        assert hello["type"] == "hello"
        assert "version" in hello["data"]

        bus.publish("log", {"message": "test ping", "level": "info", "source": "test"})
        for _ in range(5):
            message = json.loads(ws.receive_text())
            if message["type"] == "log" and message["data"].get("message") == "test ping":
                break
        else:
            raise AssertionError("log event was not delivered over the socket")


def test_websocket_command_action(client):
    import json

    with client.websocket_connect("/ws") as ws:
        ws.receive_text()  # hello
        ws.send_text(json.dumps({"action": "ping"}))
        assert json.loads(ws.receive_text())["type"] in ("pong", "log", "state", "metrics")


def test_files_endpoints(client, tmp_path):
    (tmp_path / "alpha.txt").write_text("data")
    listing = client.get("/api/files/list", params={"path": str(tmp_path)}).json()["data"]
    names = [e["name"] for e in listing["entries"]]
    assert "alpha.txt" in names

    search = client.get("/api/files/search",
                        params={"q": "alpha", "root": str(tmp_path)}).json()
    assert search["data"]["results"]


def test_memory_endpoints(client):
    created = client.post("/api/memory", json={"key": "unit_test_key", "value": "42"})
    assert created.status_code == 200
    items = client.get("/api/memory").json()["data"]["items"]
    entry = next(i for i in items if i["key"] == "unit_test_key")
    assert client.delete(f"/api/memory/{entry['id']}").status_code == 200


def test_productivity_endpoints(client):
    note = client.post("/api/notes", json={"title": "API note", "body": "text"})
    assert note.status_code == 200
    todo = client.post("/api/todos", json={"text": "API todo"})
    assert todo.status_code == 200
    reminder = client.post("/api/reminders", json={"message": "API reminder", "due_at": "10m"})
    assert reminder.status_code == 200
    timer = client.post("/api/timers", params={"seconds": 5, "label": "API timer"})
    assert timer.status_code == 200
    client.delete("/api/timers")


def test_secrets_are_never_returned(client):
    client.post("/api/settings/secret",
                params={"name": "OPENAI_API_KEY", "value": "sk-test-should-not-leak"})
    body = client.get("/api/settings").text
    assert "sk-test-should-not-leak" not in body
