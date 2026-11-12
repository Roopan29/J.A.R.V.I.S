"""PROJECT J.A.R.V.I.S. — a real, local-first Windows AI assistant.

Module map
----------
config            Configuration model + persistence
logging_setup     Rotating file + console logging
database          SQLite persistence layer
event_bus         Async pub/sub used by the backend and the dashboard
security/         Command validation, audit log, secret storage
core/             AI engine, conversation, tool registry, permissions, planner
computer/         Windows control: apps, files, input, windows, power, screen
intelligence/     Web search, memory, screen understanding
automation/       Task queue/executor, custom voice commands
productivity/     Reminders, notes, todos, timers
voice/            Wake word, STT, TTS, orchestration
api/              FastAPI server (REST + WebSocket) for the dashboard
dashboard/        Cinematic web UI served by the API
"""

__version__ = "1.0.0"
__app_name__ = "J.A.R.V.I.S."
