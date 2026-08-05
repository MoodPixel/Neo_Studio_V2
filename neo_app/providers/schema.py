from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


ProviderStatus = Literal["available", "configured", "mock", "disabled", "missing_config", "error"]
JobStatus = Literal[
    "queued",
    "running",
    "importing",
    "completed",
    "completed_with_warnings",
    "failed",
    "cancelled",
    "paused",
    "saved_in_comfy_only",
    "import_failed",
    "completed_no_outputs_recoverable",
    "completed_import_failed",
]


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

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        if str(self.surface or "").strip().casefold() != "image":
            return

        # IP-6 normalizes Output Intent first. Realistic / Anime are metadata-only
        # in this phase, but canonicalizing the intent before preset resolution
        # keeps the existing family+variant+loader+mode+intent key deterministic.
        from neo_app.image.output_intents import prepare_output_intent_payload

        prepared = prepare_output_intent_payload({
            "surface": self.surface,
            "subtab": self.subtab,
            "mode": self.mode,
            "provider_id": self.provider_id,
            "family": self.family,
            "loader": self.loader,
            "model": self.model,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "params": self.params,
            "extensions": self.extensions,
        })
        object.__setattr__(self, "params", dict(prepared.get("params") or {}))

        # IP-3/IP-5 resolves sampling preset submission semantics before IP-2 reads
        # guidance fields. This keeps Provider Defaults authoritative, preserves
        # manual Clean Slate values, and lets negative eligibility see the actual
        # effective sampling controls rather than stale preset values.
        from neo_app.image.sampling_presets import prepare_sampling_preset_payload

        prepared = prepare_sampling_preset_payload({
            "surface": self.surface,
            "subtab": self.subtab,
            "mode": self.mode,
            "provider_id": self.provider_id,
            "family": self.family,
            "loader": self.loader,
            "model": self.model,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "params": self.params,
            "extensions": self.extensions,
        })
        object.__setattr__(self, "params", dict(prepared.get("params") or {}))

        # IP-2 is the single Image job boundary for effective negative prompting.
        # Import lazily so provider schemas stay import-safe and the capability
        # registry never depends back on provider models.
        from neo_app.image.negative_prompt_eligibility import prepare_negative_prompt_payload

        prepared = prepare_negative_prompt_payload({
            "surface": self.surface,
            "subtab": self.subtab,
            "mode": self.mode,
            "provider_id": self.provider_id,
            "family": self.family,
            "loader": self.loader,
            "model": self.model,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "params": self.params,
            "extensions": self.extensions,
        })
        object.__setattr__(self, "negative_prompt", prepared.get("negative_prompt"))
        object.__setattr__(self, "params", dict(prepared.get("params") or {}))

        # IP-8 is the final preset release gate. It runs after IP-2 so it can
        # verify effective negative execution state, and before provider
        # validation/compile so invalid preset state fails closed.
        from neo_app.image.sampling_preset_release_lock import prepare_sampling_preset_release_lock_payload

        prepared = prepare_sampling_preset_release_lock_payload({
            "surface": self.surface,
            "subtab": self.subtab,
            "mode": self.mode,
            "provider_id": self.provider_id,
            "family": self.family,
            "loader": self.loader,
            "model": self.model,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "params": self.params,
            "extensions": self.extensions,
        }, raise_on_block=False)
        object.__setattr__(self, "params", dict(prepared.get("params") or {}))

        # Inspector is observational only and is attached after the release lock
        # so it can report the final contract state without affecting execution.
        from neo_app.image.sampling_preset_inspector import prepare_sampling_preset_inspector_payload

        prepared = prepare_sampling_preset_inspector_payload({
            "surface": self.surface,
            "subtab": self.subtab,
            "mode": self.mode,
            "provider_id": self.provider_id,
            "family": self.family,
            "loader": self.loader,
            "model": self.model,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "params": self.params,
            "extensions": self.extensions,
        })
        object.__setattr__(self, "params", dict(prepared.get("params") or {}))


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
