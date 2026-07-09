from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


SurfaceStatus = Literal["active", "planned", "foundation", "disabled"]


class MemoryPolicy(BaseModel):
    namespaces: list[str] = Field(default_factory=list)
    record_events: list[str] = Field(default_factory=list)


class SurfaceSubtab(BaseModel):
    subtab_id: str
    display_name: str
    description: str = ""
    slots: list[str] = Field(default_factory=list)


class SurfaceDefinition(BaseModel):
    surface_id: str
    display_name: str
    description: str = ""
    status: SurfaceStatus = "planned"
    provider_types: list[str] = Field(default_factory=list)
    extension_targets: list[str] = Field(default_factory=list)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    subtabs: list[SurfaceSubtab] = Field(default_factory=list)


class SurfaceManifest(BaseModel):
    schema_version: str
    surfaces: list[SurfaceDefinition]
