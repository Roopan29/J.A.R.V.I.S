"""Logging: rotating file handler in JARVIS_HOME/logs plus a console handler.

Importing this module is what configures logging; every other module just
calls ``logging.getLogger(__name__)``.
"""
from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

from . import config

_CONFIGURED = False

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)-28s | %(message)s"


def setup(level: int = logging.INFO, console: bool = True) -> Path:
    """Configure root logging once. Returns the active log file path."""
    global _CONFIGURED
    logfile = config.LOG_DIR / "jarvis.log"
    if _CONFIGURED:
        return logfile

    root = logging.getLogger()
    root.setLevel(level)

    formatter = logging.Formatter(_FORMAT, datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.handlers.RotatingFileHandler(
        logfile, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        root.addHandler(stream)

    # Third-party noise control
    for noisy in ("httpx", "httpcore", "urllib3", "faster_whisper", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    logging.getLogger(__name__).info("Logging started -> %s", logfile)
    return logfile


def tail(lines: int = 200) -> list[str]:
    """Return the last ``lines`` log lines (used by the dashboard)."""
    logfile = config.LOG_DIR / "jarvis.log"
    if not logfile.exists():
        return []
    with logfile.open("r", encoding="utf-8", errors="replace") as fh:
        return fh.readlines()[-lines:]
