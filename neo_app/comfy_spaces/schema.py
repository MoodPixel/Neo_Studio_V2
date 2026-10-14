from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


SPACE_MANIFEST_SCHEMA_VERSION = "neo.comfy_space.manifest.v1"
SPACE_RUNTIME_CONTRACT_VERSION = "neo.comfy_space.runtime.v1"
SPACE_TRUST_CONTRACT_VERSION = "neo.comfy_space.trust.v1"

SpaceLifecycleStatus = Literal["draft", "published", "disabled"]
SpaceValueKind = Literal[
    "text",
    "integer",
    "number",
    "boolean",
    "choice",
    "json",
    "image",
    "mask",
    "video",
    "audio",
    "file",
]
SpaceInputMultiplicity = Literal["single", "multiple"]
SpaceOutputCollection = Literal["first", "all"]
SpaceRuntimeEngine = Literal["comfyui"]
SpaceWorkflowFormat = Literal["comfy_api_json"]
SpaceRuntimeSelection = Literal["compatible_profile", "user_selected"]
SpacePublicationMode = Literal["comfy_spaces_only", "allow_cross_surface_reference"]

_SPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


class StrictContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _require_relative_portable_path(value: str, *, field_name: str, require_json: bool = False) -> str:
    text = str(value or "").strip().replace("\\", "/")
    if not text:
        raise ValueError(f"{field_name} must not be empty")
    if text.startswith("/") or _WINDOWS_ABSOLUTE_RE.match(text):
        raise ValueError(f"{field_name} must be relative to the Space package")
    parts = [part for part in text.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError(f"{field_name} must not traverse outside the Space package")
    if not parts:
        raise ValueError(f"{field_name} must reference a file inside the Space package")
    normalized = "/".join(parts)
    if require_json and not normalized.lower().endswith(".json"):
        raise ValueError(f"{field_name} must reference a JSON workflow")
    return normalized


class SpaceAuthor(StrictContractModel):
    name: str = ""
    url: str | None = None


class SpacePresentation(StrictContractModel):
    category: str = "Workflow"
    tags: list[str] = Field(default_factory=list)
    icon: str | None = None
    thumbnail: str | None = None
    publication_mode: SpacePublicationMode = "comfy_spaces_only"
    additional_surface_ids: list[str] = Field(default_factory=list)

    @field_validator("icon", "thumbnail")
    @classmethod
    def validate_optional_asset_path(cls, value: str | None, info):
        if value is None:
            return value
        return _require_relative_portable_path(value, field_name=info.field_name)

    @field_validator("additional_surface_ids")
    @classmethod
    def normalize_surface_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            surface_id = str(item or "").strip().lower()
            if not surface_id:
                raise ValueError("additional_surface_ids cannot contain empty values")
            if not _SPACE_ID_RE.fullmatch(surface_id):
                raise ValueError(f"invalid additional surface id: {surface_id}")
            if surface_id == "comfy_spaces":
                raise ValueError("comfy_spaces is already the owning surface and must not be repeated")
            if surface_id in seen:
                raise ValueError(f"duplicate additional surface id: {surface_id}")
            seen.add(surface_id)
            normalized.append(surface_id)
        return normalized

    @model_validator(mode="after")
    def validate_publication_mode(self):
        if self.publication_mode == "comfy_spaces_only" and self.additional_surface_ids:
            raise ValueError("additional_surface_ids require allow_cross_surface_reference publication mode")
        return self


class SpaceWorkflowContract(StrictContractModel):
    format: SpaceWorkflowFormat = "comfy_api_json"
    path: str = "workflow.json"
    sha256: str | None = None
    immutable_template: Literal[True] = True
    mutation_policy: Literal["clone_then_bind"] = "clone_then_bind"

    @field_validator("path")
    @classmethod
    def validate_workflow_path(cls, value: str) -> str:
        return _require_relative_portable_path(value, field_name="workflow.path", require_json=True)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str | None) -> str | None:
        if value is None:
            return value
        digest = value.strip().lower()
        if not _SHA256_RE.fullmatch(digest):
            raise ValueError("workflow.sha256 must be a 64-character SHA-256 hex digest")
        return digest


class SpaceRuntimeContract(StrictContractModel):
    contract_version: Literal[SPACE_RUNTIME_CONTRACT_VERSION] = SPACE_RUNTIME_CONTRACT_VERSION
    engine: SpaceRuntimeEngine = "comfyui"
    runtime_selection: SpaceRuntimeSelection = "compatible_profile"
    preferred_profile_id: str | None = None
    shared_queue: Literal[True] = True
    dependency_preflight_required: Literal[True] = True

    @model_validator(mode="after")
    def validate_runtime_selection(self):
        if self.runtime_selection == "user_selected" and self.preferred_profile_id:
            raise ValueError("preferred_profile_id is not used when runtime_selection is user_selected")
        return self


class SpaceTrustContract(StrictContractModel):
    contract_version: Literal[SPACE_TRUST_CONTRACT_VERSION] = SPACE_TRUST_CONTRACT_VERSION
    declarative_only: Literal[True] = True
    neo_python_execution: Literal[False] = False
    neo_javascript_execution: Literal[False] = False
    automatic_dependency_install: Literal[False] = False
    workflow_may_invoke_installed_comfy_custom_nodes: Literal[True] = True
    comfy_dependency_trust_is_separate: Literal[True] = True


class SpaceNodeInputBinding(StrictContractModel):
    binding_type: Literal["node_input"] = "node_input"
    node_id: str
    field: str

    @field_validator("node_id", "field")
    @classmethod
    def validate_binding_token(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        if len(text) > 256:
            raise ValueError(f"{info.field_name} is too long")
        return text


class SpaceNodeOutputBinding(StrictContractModel):
    binding_type: Literal["node_output"] = "node_output"
    node_id: str
    field: str | None = None
    output_index: int | None = Field(default=None, ge=0)

    @field_validator("node_id")
    @classmethod
    def validate_node_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("node_id must not be empty")
        if len(text) > 256:
            raise ValueError("node_id is too long")
        return text

    @field_validator("field")
    @classmethod
    def validate_field(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            raise ValueError("field must not be blank when provided")
        return text

    @model_validator(mode="after")
    def validate_locator(self):
        if self.field is None and self.output_index is None:
            raise ValueError("an output binding requires field and/or output_index")
        return self


class SpaceUIHint(StrictContractModel):
    label: str
    help_text: str = ""
    group: str = "Main"
    order: int = 100
    advanced: bool = False
    hidden: bool = False
    renderer_hint: str | None = None

    @field_validator("label", "group")
    @classmethod
    def validate_visible_text(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        return text


class SpaceInputDefinition(StrictContractModel):
    input_id: str
    kind: SpaceValueKind
    required: bool = True
    multiplicity: SpaceInputMultiplicity = "single"
    default: Any = None
    binding: SpaceNodeInputBinding
    ui: SpaceUIHint

    @field_validator("input_id")
    @classmethod
    def validate_input_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _SPACE_ID_RE.fullmatch(text):
            raise ValueError("input_id must use lowercase letters, numbers, dot, underscore, or hyphen")
        return text


class SpaceOutputDefinition(StrictContractModel):
    output_id: str
    kind: SpaceValueKind
    binding: SpaceNodeOutputBinding
    collection: SpaceOutputCollection = "first"
    primary: bool = False
    label: str = "Result"

    @field_validator("output_id")
    @classmethod
    def validate_output_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _SPACE_ID_RE.fullmatch(text):
            raise ValueError("output_id must use lowercase letters, numbers, dot, underscore, or hyphen")
        return text

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("output label must not be empty")
        return text


class SpaceCustomNodeRequirement(StrictContractModel):
    class_type: str
    package: str | None = None
    version_spec: str | None = None
    required: bool = True

    @field_validator("class_type")
    @classmethod
    def validate_class_type(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("class_type must not be empty")
        return text


class SpaceModelRequirement(StrictContractModel):
    requirement_id: str
    model_type: str
    configured_name: str | None = None
    source_node_id: str | None = None
    source_field: str | None = None
    required: bool = True

    @field_validator("requirement_id")
    @classmethod
    def validate_requirement_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _SPACE_ID_RE.fullmatch(text):
            raise ValueError("requirement_id must use lowercase letters, numbers, dot, underscore, or hyphen")
        return text

    @field_validator("model_type")
    @classmethod
    def validate_model_type(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("model_type must not be empty")
        return text

    @field_validator("configured_name")
    @classmethod
    def validate_configured_name(cls, value: str | None) -> str | None:
        if value is None:
            return value
        text = value.strip()
        if not text:
            return None
        return _require_relative_portable_path(text, field_name="configured_name")


class SpaceDependencyContract(StrictContractModel):
    custom_nodes: list[SpaceCustomNodeRequirement] = Field(default_factory=list)
    models: list[SpaceModelRequirement] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_requirements(self):
        node_keys: set[tuple[str, str]] = set()
        for item in self.custom_nodes:
            key = (item.class_type, item.package or "")
            if key in node_keys:
                raise ValueError(f"duplicate custom node requirement: {item.class_type}")
            node_keys.add(key)

        model_ids: set[str] = set()
        for item in self.models:
            if item.requirement_id in model_ids:
                raise ValueError(f"duplicate model requirement id: {item.requirement_id}")
            model_ids.add(item.requirement_id)
        return self


class ComfySpaceManifest(StrictContractModel):
    schema_version: Literal[SPACE_MANIFEST_SCHEMA_VERSION] = SPACE_MANIFEST_SCHEMA_VERSION
    kind: Literal["comfy_space"] = "comfy_space"
    surface_id: Literal["comfy_spaces"] = "comfy_spaces"
    space_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    status: SpaceLifecycleStatus = "draft"
    author: SpaceAuthor = Field(default_factory=SpaceAuthor)
    presentation: SpacePresentation = Field(default_factory=SpacePresentation)
    workflow: SpaceWorkflowContract = Field(default_factory=SpaceWorkflowContract)
    runtime: SpaceRuntimeContract = Field(default_factory=SpaceRuntimeContract)
    trust: SpaceTrustContract = Field(default_factory=SpaceTrustContract)
    inputs: list[SpaceInputDefinition] = Field(default_factory=list)
    outputs: list[SpaceOutputDefinition] = Field(default_factory=list)
    dependencies: SpaceDependencyContract = Field(default_factory=SpaceDependencyContract)

    @field_validator("space_id")
    @classmethod
    def validate_space_id(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        if not _SPACE_ID_RE.fullmatch(text):
            raise ValueError("space_id must use lowercase letters, numbers, dot, underscore, or hyphen")
        return text

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("name must not be empty")
        return text

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not _VERSION_RE.fullmatch(text):
            raise ValueError("version must be semantic version text such as 1.0.0 or 1.2.0-beta.1")
        return text

    @model_validator(mode="after")
    def validate_contract(self):
        input_ids: set[str] = set()
        input_bindings: set[tuple[str, str]] = set()
        for item in self.inputs:
            if item.input_id in input_ids:
                raise ValueError(f"duplicate input_id: {item.input_id}")
            input_ids.add(item.input_id)
            binding_key = (item.binding.node_id, item.binding.field)
            if binding_key in input_bindings:
                raise ValueError(
                    "multiple user inputs cannot bind to the same Comfy node field: "
                    f"{item.binding.node_id}.{item.binding.field}"
                )
            input_bindings.add(binding_key)

        output_ids: set[str] = set()
        primary_count = 0
        for item in self.outputs:
            if item.output_id in output_ids:
                raise ValueError(f"duplicate output_id: {item.output_id}")
            output_ids.add(item.output_id)
            primary_count += 1 if item.primary else 0
        if primary_count > 1:
            raise ValueError("a Space may declare at most one primary output")
        if self.status == "published" and not self.outputs:
            raise ValueError("a published Space must declare at least one output")
        return self
