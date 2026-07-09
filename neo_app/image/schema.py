
from __future__ import annotations

from pydantic import BaseModel, Field


class ImageOption(BaseModel):
    id: str
    label: str
    description: str = ""


class ImageShellSection(BaseModel):
    section_id: str
    title: str
    description: str = ""
    slot: str
    fields: list[str] = Field(default_factory=list)


class ImageSubtabBase(BaseModel):
    subtab_id: str
    display_name: str
    mode: str
    description: str = ""
    sections: list[ImageShellSection] = Field(default_factory=list)


class ImageSurfaceBaseContract(BaseModel):
    surface_id: str = "image"
    version: str = "0.9.0"
    model_families: list[ImageOption] = Field(default_factory=list)
    loader_types: list[ImageOption] = Field(default_factory=list)
    default_params: dict = Field(default_factory=dict)
    size_presets: list[ImageOption] = Field(default_factory=list)
    subtabs: list[ImageSubtabBase] = Field(default_factory=list)
    memory_events: list[str] = Field(default_factory=list)


class ImageJobDraft(BaseModel):
    surface: str = "image"
    subtab: str = "generate"
    mode: str = "txt2img"
    backend: str = "comfyui_portable"
    family: str = "sdxl"
    loader: str = "checkpoint"
    prompt: str = ""
    negative_prompt: str = ""
    params: dict = Field(default_factory=dict)
    extensions: list[str] = Field(default_factory=list)
    status: str = "draft_only"
