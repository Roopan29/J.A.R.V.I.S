"""FastAPI application factory, WebSocket fan-out and the metrics pusher."""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, List, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__, config
from ..automation.task_queue import task_queue
from ..computer import system_control
from ..core.conversation import conversation
from ..core.permissions import permissions
from ..core.tool_registry import load_all_tools
from ..event_bus import EventType, bus
from ..productivity.reminders import scheduler as reminder_scheduler
from ..productivity.timers import timer_manager
from ..voice.speech_output import speech
from ..voice.voice_manager import voice_manager
from . import routes

log = logging.getLogger(__name__)

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"
METRICS_INTERVAL = 2.0
HEALTH_INTERVAL = 20.0


# ---------------------------------------------------------------------------
# WebSocket hub
# ---------------------------------------------------------------------------
class ConnectionHub:
    def __init__(self) -> None:
        self._clients: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._clients.add(websocket)
        log.info("Dashboard connected (%d active)", len(self._clients))

    def disconnect(self, websocket: WebSocket) -> None:
        self._clients.discard(websocket)

    async def broadcast(self, payload: str) -> None:
        dead: List[WebSocket] = []
        for client in list(self._clients):
            try:
                await client.send_text(payload)
            except Exception:
                dead.append(client)
        for client in dead:
            self.disconnect(client)

    @property
    def count(self) -> int:
        return len(self._clients)


hub = ConnectionHub()


# ---------------------------------------------------------------------------
# Background workers
# ---------------------------------------------------------------------------
async def metrics_pusher() -> None:
    """Push real telemetry every couple of seconds (never fake numbers)."""
    loop = asyncio.get_running_loop()
    while True:
        try:
            if hub.count:
                snapshot = await loop.run_in_executor(None, system_control.full_snapshot)
                await hub.broadcast(json.dumps({"id": "metrics", "type": EventType.METRICS,
                                                "ts": __import__("time").time(),
                                                "data": snapshot}, default=str))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Metrics push failed")
        await asyncio.sleep(METRICS_INTERVAL)


async def health_monitor() -> None:
    """Proactive alerts: low battery, high CPU/RAM, low disk."""
    seen: Dict[str, float] = {}
    loop = asyncio.get_running_loop()
    import time as _time
    while True:
        await asyncio.sleep(HEALTH_INTERVAL)
        try:
            warnings = await loop.run_in_executor(None, system_control.health_warnings)
            now = _time.time()
            for warning in warnings:
                key = warning["title"]
                if now - seen.get(key, 0) < 900:      # don't nag more than 4x/hour
                    continue
                seen[key] = now
                bus.notify(warning["title"], warning["message"], level="warning")
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Health monitor failed")


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------
def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        settings = config.get()

        # Load the tool catalogue before anything can call it
        tool_registry = load_all_tools()

        # Wire voice <-> conversation <-> reminders so speech works everywhere
        conversation.attach_speaker(voice_manager.say)
        conversation.set_memory_enabled(settings.memory_enabled)
        voice_manager.attach_conversation(conversation)
        permissions.add_listener(lambda request: speech.speak_async(request.description))
        reminder_scheduler.attach_speaker(speech.speak_async)
        reminder_scheduler.start()
        timer_manager.attach_speaker(speech.speak_async)

        # Every bus event is forwarded to dashboards
        async def forward(event):
            if hub.count:
                await hub.broadcast(event.to_json())

        bus.subscribe("*", forward)

        tasks = [asyncio.create_task(metrics_pusher()),
                 asyncio.create_task(health_monitor())]
        task_queue.ensure_worker()

        if settings.startup.start_listening:
            result = voice_manager.start("wake" if settings.voice.wake_word_enabled
                                         else "push_to_talk")
            log.info("Auto-start listening: %s", result.get("message"))

        bus.log(f"JARVIS {__version__} online — {len(tool_registry.all())} tools loaded",
                source="core")
        if settings.startup.notify_on_start:
            bus.notify("JARVIS online", "All systems operational.", level="info")

        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            await task_queue.shutdown()
            reminder_scheduler.stop()
            voice_manager.stop()
            speech.stop()
            log.info("JARVIS shutting down")

    app = FastAPI(title="J.A.R.V.I.S.", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", openapi_url="/api/openapi.json")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],          # local desktop app; the browser is trusted
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(routes.router)

    # -- WebSocket -------------------------------------------------------
    @app.websocket("/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await hub.connect(websocket)
        try:
            await websocket.send_text(json.dumps({
                "type": "hello", "data": {"version": __version__,
                                          "history": bus.history(40)}}))
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                await _handle_ws_message(message, websocket)
        except WebSocketDisconnect:
            hub.disconnect(websocket)
        except Exception:
            log.exception("WebSocket error")
            hub.disconnect(websocket)

    async def _handle_ws_message(message: Dict[str, Any], websocket: WebSocket) -> None:
        action = message.get("action")
        if action == "ping":
            await websocket.send_text(json.dumps({"type": "pong"}))
        elif action == "command":
            text = str(message.get("text", ""))[:4000]
            if text:
                asyncio.create_task(conversation.handle(text, speak=bool(
                    message.get("speak", True)), source="dashboard"))
        elif action == "permission":
            permissions.resolve(str(message.get("id")), bool(message.get("approved")))
        elif action == "stop":
            voice_manager.stop()
            speech.stop()

    # -- static dashboard ------------------------------------------------
    if DASHBOARD_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(DASHBOARD_DIR)), name="static")

        @app.get("/")
        async def index() -> FileResponse:
            return FileResponse(DASHBOARD_DIR / "index.html")

        @app.get("/{path:path}")
        async def spa(path: str) -> Any:
            candidate = DASHBOARD_DIR / path
            if candidate.is_file():
                return FileResponse(candidate)
            if path.startswith("api/") or path == "ws":
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return FileResponse(DASHBOARD_DIR / "index.html")

    @app.exception_handler(Exception)
    async def unhandled_exception(request, exc):  # pragma: no cover
        log.exception("Unhandled API error on %s", request.url.path)
        bus.publish(EventType.ERROR, {"source": "api", "message": str(exc)})
        return JSONResponse({"ok": False, "message": str(exc)}, status_code=500)

    return app


def run(host: str | None = None, port: int | None = None, open_browser: bool = True) -> None:
    """Start the server (used by main.py and the desktop launcher)."""
    import threading
    import webbrowser

    import uvicorn

    settings = config.get()
    host = host or settings.server_host
    port = port or settings.server_port
    app = create_app()

    if open_browser:
        def open_later() -> None:
            import time
            time.sleep(1.5)
            webbrowser.open(f"http://{host}:{port}/")
        threading.Thread(target=open_later, daemon=True).start()

    uvicorn.run(app, host=host, port=port, log_level="info", access_log=False)
