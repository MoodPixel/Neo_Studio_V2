from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field

FamilyStatus = Literal["base", "extension_ready", "provider_only", "placeholder"]


class LoaderType(BaseModel):
    loader_id: str
    display_name: str
    description: str = ""
    requires_extension: bool = False
    extension_id: str | None = None


class LoaderRole(BaseModel):
    role_id: str
    display_name: str
    description: str = ""
    kind: str = "asset"
    required_by_default: bool = False
    backend_neutral: bool = True


class LoaderContract(BaseModel):
    loader_id: str
    display_name: str
    description: str = ""
    backend_neutral: bool = True
    required_roles: list[str] = Field(default_factory=list)
    optional_roles: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    role_definitions: dict[str, LoaderRole] = Field(default_factory=dict)
    mode_role_overrides: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    provider_translation_required: bool = True
    core_must_not_reference_backend_nodes: bool = True


class LoaderContractResolution(BaseModel):
    loader: str
    backend_neutral: bool = True
    provider_translation_required: bool = True
    required_roles: list[str] = Field(default_factory=list)
    optional_roles: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    role_definitions: dict[str, dict[str, Any]] = Field(default_factory=dict)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ParameterField(BaseModel):
    field_id: str
    label: str
    control_type: str = "text"
    role_id: str | None = None
    required: bool = False
    backend_neutral: bool = True
    advanced: bool = False
    modes: list[str] = Field(default_factory=list)
    help_text: str = ""
    default: Any | None = None
    status_badge: str | None = None
    visible_when: dict[str, Any] = Field(default_factory=dict)


class ParameterProfile(BaseModel):
    profile_id: str
    display_name: str
    description: str = ""
    families: list[str] = Field(default_factory=list)
    loaders: list[str] = Field(default_factory=list)
    modes: list[str] = Field(default_factory=list)
    shared_fields: list[ParameterField] = Field(default_factory=list)
    family_fields: list[ParameterField] = Field(default_factory=list)
    readiness_fields: list[str] = Field(default_factory=list)
    hidden_fields: list[str] = Field(default_factory=list)
    disabled_fields: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ParameterProfileResolution(BaseModel):
    profile_id: str | None = None
    family: str
    loader: str | None = None
    mode: str | None = None
    fields: list[dict[str, Any]] = Field(default_factory=list)
    hidden_fields: list[str] = Field(default_factory=list)
    disabled_fields: list[str] = Field(default_factory=list)
    readiness_fields: list[str] = Field(default_factory=list)
    readiness_gates: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ModelFamily(BaseModel):
    family_id: str
    display_name: str
    surfaces: list[str] = Field(default_factory=list)
    category: str = "image"
    status: FamilyStatus = "base"
    description: str = ""

    # Backward-compatible flat selectors used by the current UI/API.
    supported_modes: list[str] = Field(default_factory=list)
    supported_backends: list[str] = Field(default_factory=list)
    supported_loaders: list[str] = Field(default_factory=list)
    default_loader: str = "checkpoint"
    default_params: dict = Field(default_factory=dict)
    required_extensions: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    # Phase 12.1 registry contract fields. These describe capability/readiness
    # intent only; provider compilers still own backend graph/API translation.
    runtime_profiles: list[str] = Field(default_factory=list)
    parameter_profiles: list[str] = Field(default_factory=list)
    mode_support: dict[str, str] = Field(default_factory=dict)
    loader_mode_support: dict[str, dict[str, str]] = Field(default_factory=dict)
    required_roles: dict[str, list[str]] = Field(default_factory=dict)
    backend_requirements: dict[str, dict[str, list[str]]] = Field(default_factory=dict)
    readiness_gates: dict[str, list[str]] = Field(default_factory=dict)


class ModelFamilyCompatibilityRequest(BaseModel):
    surface: str
    mode: str | None = None
    backend: str | None = None
    family: str
    loader: str | None = None


class ModelFamilyCompatibilityResult(BaseModel):
    ok: bool
    family: str
    loader: str | None = None
    backend: str | None = None
    mode: str | None = None
    mode_status: str | None = None
    runtime_profiles: list[str] = Field(default_factory=list)
    parameter_profiles: list[str] = Field(default_factory=list)
    selected_parameter_profile: str | None = None
    parameter_profile: dict[str, Any] = Field(default_factory=dict)
    required_roles: list[str] = Field(default_factory=list)
    optional_roles: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    loader_contract: dict[str, Any] = Field(default_factory=dict)
    backend_requirements: list[str] = Field(default_factory=list)
    readiness_gates: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    required_extensions: list[str] = Field(default_factory=list)

class ReadinessValidationRequest(BaseModel):
    surface: str
    mode: str
    family: str
    loader: str | None = None
    backend: str | None = None
    provider_id: str | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    selected_assets: dict[str, Any] = Field(default_factory=dict)
    backend_capabilities: dict[str, Any] = Field(default_factory=dict)
    extension_ids: list[str] = Field(default_factory=list)


class ReadinessValidationResult(BaseModel):
    ok: bool
    ready: bool
    family: str
    loader: str | None = None
    mode: str | None = None
    provider_id: str | None = None
    backend: str | None = None
    mode_status: str | None = None
    required_roles: list[str] = Field(default_factory=list)
    readiness_gates: list[str] = Field(default_factory=list)
    satisfied: list[str] = Field(default_factory=list)
    blockers: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    checks: dict[str, Any] = Field(default_factory=dict)

