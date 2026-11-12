"""FastAPI application: REST + WebSocket bridge between backend and dashboard."""
from .server import create_app, run  # noqa: F401

__all__ = ["create_app", "run"]
