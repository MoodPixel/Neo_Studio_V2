from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


ProviderStatus = Literal["available", "configured", "mock", "disabled", "missing_config", "error"]
JobStatus = Literal["queued", "running", "completed", "failed", "cancelled", "paused"]


class ProviderCapability(BaseModel):
    capability_id: str
    display_name: str
    surface: str
    modes: list[str] = Field(default_factory=list)
    families: list[str] = Field(default_factory=list)
    loaders: list[str] = Field(default_factory=list)


class ProviderFeatureCapabilities(BaseModel):
    """Runtime feature capabilities used by the UI and surfaces.

    This is intentionally separate from the broad provider capability list above.
    The broad list answers "what can this provider do?"; this object answers
    "which runtime controls can the current provider safely expose?"
    """

    progress: bool = False
    live_preview: bool = False
    cancel: bool = False
    pause: bool = False
    resume: bool = False
    clip_skip: bool = False
    prompt_conditioning: bool = False
    node_manager: bool = False
    output_handoff: str = "provider_native"
    progress_source: str = "polling"
    live_preview_source: str = "none"


class BackendRoleCapability(BaseModel):
    role_id: str
    available: bool = False
    backend_key: str | None = None
    backend_node: str | None = None
    aliases: list[str] = Field(default_factory=list)
    assets: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class BackendLoaderCapability(BaseModel):
    loader_id: str
    available: bool = False
    roles: dict[str, BackendRoleCapability] = Field(default_factory=dict)
    assets: dict[str, list[str]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class BackendCapabilityDiscoveryResult(BaseModel):
    provider_id: str
    backend: str
    discovery_version: str = "0.1.0"
    discovery_status: Literal["available", "offline", "error", "mock"] = "mock"
    reachable: bool = False
    object_info_available: bool = False
    loaders: dict[str, BackendLoaderCapability] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ProviderManifest(BaseModel):
    provider_id: str
    display_name: str
    provider_type: str
    surfaces: list[str] = Field(default_factory=list)
    status: ProviderStatus = "mock"
    connection_kind: str = "local"
    supported_modes: list[str] = Field(default_factory=list)
    supported_families: list[str] = Field(default_factory=list)
    supported_loaders: list[str] = Field(default_factory=list)
    capabilities: list[ProviderCapability] = Field(default_factory=list)
    config_schema: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""


class NeoJob(BaseModel):
    job_id: str | None = None
    surface: str
    subtab: str
    mode: str
    provider_id: str
    family: str | None = None
    loader: str | None = None
    model: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    extensions: dict[str, Any] | list[dict[str, Any]] = Field(default_factory=dict)


class ProviderValidationResult(BaseModel):
    ok: bool
    provider_id: str
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class CompiledJob(BaseModel):
    provider_id: str
    compile_status: Literal["compiled", "mock_compiled"] = "mock_compiled"
    backend_payload: dict[str, Any] = Field(default_factory=dict)


class ProviderRunResult(BaseModel):
    job_id: str
    provider_id: str
    status: JobStatus = "queued"
    message: str = ""
    outputs: list[dict[str, Any]] = Field(default_factory=list)
    client_id: str | None = None
    runtime: dict[str, Any] = Field(default_factory=dict)
