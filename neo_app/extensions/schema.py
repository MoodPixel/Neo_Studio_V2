from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

ExtensionStatus = Literal[
    "enabled",
    "disabled",
    "missing_requirements",
    "backend_unsupported",
    "parent_missing",
    "invalid_manifest",
    "removed",
]

ExtensionOrigin = Literal["built_in", "external"]
ExtensionDetailMode = Literal["compact", "guided", "expert", "Compact", "Guided", "Expert"]
VALID_IMAGE_WORKSPACE_APPS = {"generations", "assets", "reference", "finish", "results"}
VALID_ROUTE_STATES = {"available", "experimental_available", "implementation_target", "planned_gated", "provider_gated", "unsupported"}


class RequiredNodes(BaseModel):
    comfyui: list[str] = Field(default_factory=list)
    comfyui_portable: list[str] = Field(default_factory=list)
    forge: list[str] = Field(default_factory=list)
    a1111: list[str] = Field(default_factory=list)
    cloud_api: list[str] = Field(default_factory=list)


class ExtensionCapabilityProfile(BaseModel):
    profile_id: str
    family: str | None = None
    loader: str | None = None
    families: list[str] = Field(default_factory=list)
    loaders: list[str] = Field(default_factory=list)
    modes: dict[str, str] = Field(default_factory=dict)
    required_roles: list[str] = Field(default_factory=list)
    optional_roles: list[str] = Field(default_factory=list)
    readiness_gates: dict[str, list[str]] = Field(default_factory=dict)
    params: list[str] = Field(default_factory=list)
    backend_requirements: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class ExtensionMountTarget(BaseModel):
    surface: str = "image"
    workspace_app: str | None = None
    workflow_mode: str | None = None
    slot: str
    route_states: list[str] = Field(default_factory=list)


class ExtensionUIField(BaseModel):
    id: str
    label: str
    type: str = "text"
    compact_label: str | None = None
    help_text: str | None = None
    guided_help: str | None = None
    expert_text: str | None = None
    visible_when: dict[str, Any] = Field(default_factory=dict)
    payload_path: str | None = None


class ExtensionAssetBundle(BaseModel):
    js: list[str] = Field(default_factory=list)
    css: list[str] = Field(default_factory=list)
    html: list[str] = Field(default_factory=list)
    python: list[str] = Field(default_factory=list)
    assets: list[str] = Field(default_factory=list)


class ExtensionPayloadContract(BaseModel):
    schema_version: str = "neo.extension.payload.v1"
    block_key: str = "extensions"
    required_keys: list[str] = Field(default_factory=lambda: ["enabled", "version", "inputs", "params", "assets", "metadata"])
    forbids_hidden_field_leakage: bool = True
    route_validation_required: bool = True


class ExtensionMemoryContract(BaseModel):
    event_type: str = "extension_workflow_used"
    namespace_pattern: str = "extension:{extension_id}"
    assistant_summary_required: bool = True
    record_route: bool = True
    record_assets: bool = True
    record_params: bool = True


class ExtensionOutputContract(BaseModel):
    metadata_slots: list[str] = Field(default_factory=lambda: [
        "extensions.used",
        "extensions.payloads",
        "extensions.workflow_patches",
        "extensions.validation",
    ])
    record_input_assets: bool = True
    record_route_snapshot: bool = True
    record_replay_payload: bool = True


class ExtensionManifest(BaseModel):
    id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    extension_type: str = "workflow_extension"
    extension_origin: ExtensionOrigin | None = None
    surface: str
    subtabs: list[str] = Field(default_factory=list)
    workflow_modes: list[str] = Field(default_factory=list)
    workspace_apps: list[str] = Field(default_factory=list)
    mount_slots: list[str] = Field(default_factory=list)
    mount_targets: list[ExtensionMountTarget] = Field(default_factory=list)
    supported_backends: list[str] = Field(default_factory=list)
    supported_families: list[str] = Field(default_factory=list)
    supported_loaders: list[str] = Field(default_factory=list)
    route_states: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    extends: list[str] = Field(default_factory=list)
    required_nodes: dict[str, list[str]] = Field(default_factory=dict)
    capability_profiles: dict[str, ExtensionCapabilityProfile] = Field(default_factory=dict)
    ui_schema: dict[str, Any] = Field(default_factory=dict)
    payload_contract: ExtensionPayloadContract = Field(default_factory=ExtensionPayloadContract)
    memory_contract: ExtensionMemoryContract = Field(default_factory=ExtensionMemoryContract)
    output_contract: ExtensionOutputContract = Field(default_factory=ExtensionOutputContract)
    memory_policy: dict[str, Any] = Field(default_factory=dict)
    output_policy: dict[str, Any] = Field(default_factory=dict)
    asset_bundle: ExtensionAssetBundle = Field(default_factory=ExtensionAssetBundle)
    entrypoints: dict[str, str] = Field(default_factory=dict)


class ExtensionRecord(BaseModel):
    manifest: ExtensionManifest
    status: ExtensionStatus
    enabled: bool
    install_path: str
    origin: ExtensionOrigin = "external"
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExtensionCompatibilityRequest(BaseModel):
    surface: str
    subtab: str | None = None
    workspace_app: str | None = None
    workflow_mode: str | None = None
    route_state: str | None = None
    provider_id: str | None = None
    family: str | None = None
    loader: str | None = None
    extension_ids: list[str] = Field(default_factory=list)
