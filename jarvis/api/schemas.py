"""Pydantic request/response models for the REST API."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CommandRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)
    speak: bool = True
    session_id: Optional[str] = None


class SpeakRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class PermissionResponse(BaseModel):
    approved: bool
    remember: bool = False


class SettingsPatch(BaseModel):
    section: Optional[str] = None
    values: Dict[str, Any] = Field(default_factory=dict)


class MemoryCreate(BaseModel):
    key: str
    value: str
    category: str = "general"


class NoteCreate(BaseModel):
    title: str
    body: str = ""
    tags: str = ""


class NoteUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None
    tags: Optional[str] = None


class TodoCreate(BaseModel):
    text: str
    priority: int = 0
    due_at: str = ""


class ReminderCreate(BaseModel):
    message: str
    due_at: Optional[str] = None
    repeat: str = "none"


class CustomCommandCreate(BaseModel):
    trigger: str
    steps: List[Dict[str, Any]]
    description: str = ""


class ToolInvoke(BaseModel):
    name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class LaunchRequest(BaseModel):
    name: str
    arguments: Optional[str] = None


class FileAction(BaseModel):
    operation: str
    paths: Dict[str, str] = Field(default_factory=dict)
    options: Dict[str, Any] = Field(default_factory=dict)
