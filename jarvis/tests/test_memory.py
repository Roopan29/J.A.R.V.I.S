"""Memory store: persistence, categories, sensitivity filter, recall."""
from __future__ import annotations

import pytest

from jarvis import config
from jarvis.intelligence import memory


@pytest.fixture(autouse=True)
def clean_memory(db):
    config.get().memory_enabled = True
    db.clear_memory()
    yield
    db.clear_memory()


def test_remember_and_recall(db):
    result = memory.remember("editor", "VS Code", "preference")
    assert result["ok"]
    items = memory.recall("editor")["data"]["items"]
    assert items and items[0]["value"] == "VS Code"
    assert items[0]["category"] == "preference"


def test_updates_overwrite_same_key(db):
    memory.remember("name", "Tony", "fact")
    memory.remember("name", "Roopan", "fact")
    items = [m for m in memory.recall("name")["data"]["items"] if m["key"] == "name"]
    assert len(items) == 1
    assert items[0]["value"] == "Roopan"


@pytest.mark.parametrize("key,value", [
    ("password", "hunter2"),
    ("api_key", "sk-1234567890"),
    ("secret", "my banking pin 1234"),
    ("token", "ghp_abcdefghijklmnop"),
])
def test_secrets_are_not_stored(key, value):
    result = memory.remember(key, value)
    assert result["ok"] is False
    assert "will not store" in result["message"].lower()
    assert memory.recall(key)["data"]["items"] == []


def test_sensitive_detection():
    assert memory.is_sensitive("my password is x") is True
    assert memory.is_sensitive("I like tea") is False


def test_forget_and_clear(db):
    memory.remember("colour", "blue")
    assert memory.forget(key="colour")["ok"]
    assert memory.recall("colour")["data"]["items"] == []

    memory.remember("a", "1")
    memory.remember("b", "2")
    cleared = memory.clear_all()
    assert cleared["ok"]


def test_memory_disabled_blocks_writes():
    config.get().memory_enabled = False
    try:
        result = memory.remember("x", "y")
        assert result["ok"] is False
        assert "disabled" in result["message"].lower()
    finally:
        config.get().memory_enabled = True


def test_conversation_extracts_facts(db):
    from jarvis.core.conversation import ConversationEngine

    engine = ConversationEngine()
    engine._extract_memories("my name is Roopan and I prefer dark mode", None)
    names = [m for m in memory.recall("name")["data"]["items"] if m["key"] == "name"]
    assert names and names[0]["value"].lower().startswith("roopan")


def test_passwords_in_conversation_are_ignored(db):
    from jarvis.core.conversation import ConversationEngine
    engine = ConversationEngine()
    engine._extract_memories("remember my password is hunter2", None)
    assert memory.recall("password")["data"]["items"] == []
