from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

MemoryBackendStatus = Literal["available", "missing_optional_dependency", "disabled"]
MemoryImportance = Literal["low", "normal", "high"]


class MemoryEvent(BaseModel):
    """Structured Neo activity event written by base surfaces and extensions."""

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    namespace: str = "global"
    surface: str = "global"
    subtab: str | None = None
    source: Literal["base", "extension", "provider", "admin", "assistant", "system"] = "system"
    event_type: str
    title: str
    summary: str = ""
    project_id: str | None = None
    extension_id: str | None = None
    provider_id: str | None = None
    family: str | None = None
    loader: str | None = None
    tags: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    importance: MemoryImportance = "normal"
    should_embed: bool = False

    def searchable_text(self) -> str:
        parts = [self.title, self.summary, self.surface, self.subtab or "", self.event_type]
        parts.extend(self.tags)
        if self.family:
            parts.append(self.family)
        if self.loader:
            parts.append(self.loader)
        return " ".join(part for part in parts if part)


class MemoryQuery(BaseModel):
    query: str = ""
    namespace: str | None = None
    surface: str | None = None
    limit: int = 20
    semantic: bool = False


class MemorySearchResult(BaseModel):
    event: MemoryEvent
    score: float | None = None
    backend: str = "sqlite"


class MemoryCapabilityStatus(BaseModel):
    sqlite: MemoryBackendStatus
    chroma: MemoryBackendStatus
    sentence_transformers: MemoryBackendStatus
    transformers: MemoryBackendStatus
    semantic_search_enabled: bool
    notes: list[str] = Field(default_factory=list)
