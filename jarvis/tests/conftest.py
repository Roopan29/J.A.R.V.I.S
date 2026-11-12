"""Test fixtures: isolated JARVIS_HOME so tests never touch real user data."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

# Use a throwaway home directory BEFORE importing any jarvis module.
_TMP_HOME = Path(tempfile.mkdtemp(prefix="jarvis-test-"))
os.environ["JARVIS_HOME"] = str(_TMP_HOME)
os.environ.pop("OPENAI_API_KEY", None)
os.environ.pop("ANTHROPIC_API_KEY", None)
os.environ.pop("BRAVE_SEARCH_API_KEY", None)

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


@pytest.fixture(scope="session")
def jarvis_home() -> Path:
    return _TMP_HOME


@pytest.fixture(scope="session", autouse=True)
def _schema():
    from jarvis.database.database import db
    db.init_schema()
    return db


@pytest.fixture()
def registry():
    from jarvis.core.tool_registry import load_all_tools
    return load_all_tools()


@pytest.fixture()
def db():
    from jarvis.database.database import db as database
    return database
