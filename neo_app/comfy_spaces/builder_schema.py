from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.graph_analyzer import SPACE_ANALYSIS_SCHEMA_VERSION
from neo_app.comfy_spaces.graph_anchors import GraphAnchorRole
from neo_app.comfy_spaces.dynamic_controls import SpaceDynamicBehavior, SpaceDynamicCollection
from neo_app.comfy_spaces.paths import (
    SPACE_BUILDER_DRAFT_FILENAME,
    SPACE_DRAFT_ANALYSIS_FILENAME,
    SPACE_PREVIEW_MANIFEST_FILENAME,
    SPACE_PREVIEW_UI_FILENAME,
)
from neo_app.comfy_spaces.schema import (
    SpaceAuthor,
    SpaceInputMultiplicity,
    SpaceOutputCollection,
    SpacePresentation,
    StrictContractModel,
)
from neo_app.comfy_spaces.type_system import (
    SpaceInputControl,
    SpaceOptionContract,
    SpaceOutputRenderer,
    SpaceTypeConstraint,
)


SPACE_BUILDER_DRAFT_SCHEMA_VERSION = "neo.comfy_space.builder_draft.v1"

_SPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)

BuilderDefaultMode = Literal["template", "none", "custom"]


def _safe_id(value: str, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _SPACE_ID_RE.fullmatch(text):
        raise ValueError(f"{field_name} must use lowercase letters, numbers, dot, underscore, or hyphen")
    return text


class SpaceBuilderProvenance(StrictContractModel):
    import_id: str
    source_name: str = "workflow.json"
    source_sha256: str = ""
    import_canonical_workflow_sha256: str
    frozen_workflow_sha256: str
    analysis_sha256: str
    analysis_schema_version: Literal[SPACE_ANALYSIS_SCHEMA_VERSION] = SPACE_ANALYSIS_SCHEMA_VERSION
    runtime_profile_id: str = ""
    object_info_used: bool = False
    frozen_import: Literal[True] = True

    @field_validator("import_id")
    @classmethod
    def validate_import_id(cls, value: str) -> str:
        return _safe_id(value, field_name="import_id")

    @field_validator(
        "source_sha256",
        "import_canonical_workflow_sha256",
        "frozen_workflow_sha256",
        "analysis_sha256",
    )
    @classmethod
    def validate_optional_sha256(cls, value: str, info) -> str:
        text = str(value or "").strip().lower()
        if info.field_name == "source_sha256" and not text:
            return ""
        if not _SHA256_RE.fullmatch(text):
            raise ValueError(f"{info.field_name} must be a 64-character SHA-256 hex digest")
        return text


class SpaceBuilderIdentity(StrictContractModel):
    space_id: str
    name: str
    version: str = "0.1.0"
    description: str = ""
    author: SpaceAuthor = Field(default_factory=SpaceAuthor)
    presentation: SpacePresentation = Field(default_factory=SpacePresentation)

    @field_validator("space_id")
    @classmethod
    def validate_space_id(cls, value: str) -> str:
        return _safe_id(value, field_name="space_id")

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
            raise ValueError("version must be semantic version text such as 1.0.0")
        return text


class SpaceBuilderSectionDraft(StrictContractModel):
    section_id: str
    title: str
    description: str = ""
    order: int = 100
    advanced: bool = False
    collapsible: bool = False
    default_collapsed: bool = False

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str) -> str:
        return _safe_id(value, field_name="section_id")

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("section title must not be empty")
        return text

    @model_validator(mode="after")
    def validate_collapse_state(self):
        if self.default_collapsed and not self.collapsible:
            raise ValueError("default_collapsed requires collapsible=true")
        return self


class SpaceBuilderAnchorDraft(StrictContractModel):
    anchor_id: str
    semantic_role: GraphAnchorRole
    confirmed: bool = False

    @field_validator("anchor_id")
    @classmethod
    def validate_anchor_id(cls, value: str) -> str:
        return _safe_id(value, field_name="anchor_id")


class SpaceBuilderStageDraft(StrictContractModel):
    stage_id: str
    label: str
    description: str = ""
    order: int = 100
    required_stage_ids: list[str] = Field(default_factory=list)
    target_output_ids: list[str] = Field(default_factory=list)
    intermediate_output_ids: list[str] = Field(default_factory=list)
    enabled_by_default: bool = True
    optional: bool = False
    can_run_alone: bool = False
    resource_policy: Literal["automatic", "keep_loaded", "free_memory", "unload_models"] = "automatic"

    @field_validator("stage_id")
    @classmethod
    def validate_stage_id(cls, value: str) -> str:
        return _safe_id(value, field_name="stage_id")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("stage label must not be empty")
        return text


class SpaceBuilderInputDraft(StrictContractModel):
    candidate_id: str
    enabled: bool = False
    input_id: str
    label: str
    help_text: str = ""
    section_id: str = "main"
    order: int = 100
    advanced: bool = False
    required: bool = False
    multiplicity: SpaceInputMultiplicity = "single"
    default_mode: BuilderDefaultMode = "template"
    default: Any = None
    control_override: SpaceInputControl | None = None
    constraints_override: SpaceTypeConstraint | None = None
    options_override: SpaceOptionContract | None = None

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _safe_id(value, field_name="candidate_id")

    @field_validator("input_id")
    @classmethod
    def validate_input_id(cls, value: str) -> str:
        return _safe_id(value, field_name="input_id")

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str) -> str:
        return _safe_id(value, field_name="section_id")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("input label must not be empty")
        return text


class SpaceBuilderOutputDraft(StrictContractModel):
    candidate_id: str
    enabled: bool = False
    output_id: str
    label: str
    order: int = 100
    collection: SpaceOutputCollection = "first"
    primary: bool = False
    renderer_override: SpaceOutputRenderer | None = None

    @field_validator("candidate_id")
    @classmethod
    def validate_candidate_id(cls, value: str) -> str:
        return _safe_id(value, field_name="candidate_id")

    @field_validator("output_id")
    @classmethod
    def validate_output_id(cls, value: str) -> str:
        return _safe_id(value, field_name="output_id")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("output label must not be empty")
        return text


class ComfySpaceBuilderDraft(StrictContractModel):
    schema_version: Literal[SPACE_BUILDER_DRAFT_SCHEMA_VERSION] = SPACE_BUILDER_DRAFT_SCHEMA_VERSION
    kind: Literal["comfy_space_builder_draft"] = "comfy_space_builder_draft"
    draft_id: str
    created_at: str
    updated_at: str
    revision: int = Field(default=1, ge=1)
    execution_performed: Literal[False] = False
    human_review_required: Literal[True] = True
    identity: SpaceBuilderIdentity
    provenance: SpaceBuilderProvenance
    submit_label: str = "Create"
    results_title: str = "Results"
    sections: list[SpaceBuilderSectionDraft] = Field(
        default_factory=lambda: [SpaceBuilderSectionDraft(section_id="main", title="Main", order=10)]
    )
    anchors: list[SpaceBuilderAnchorDraft] = Field(default_factory=list)
    stages: list[SpaceBuilderStageDraft] = Field(default_factory=list)
    dynamic_behaviors: list[SpaceDynamicBehavior] = Field(default_factory=list)
    dynamic_collections: list[SpaceDynamicCollection] = Field(default_factory=list)
    inputs: list[SpaceBuilderInputDraft] = Field(default_factory=list)
    outputs: list[SpaceBuilderOutputDraft] = Field(default_factory=list)

    @field_validator("draft_id")
    @classmethod
    def validate_draft_id(cls, value: str) -> str:
        return _safe_id(value, field_name="draft_id")

    @field_validator("submit_label", "results_title")
    @classmethod
    def validate_visible_text(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        return text

    @model_validator(mode="after")
    def validate_draft(self):
        section_ids: set[str] = set()
        for item in self.sections:
            if item.section_id in section_ids:
                raise ValueError(f"duplicate section_id: {item.section_id}")
            section_ids.add(item.section_id)
        if not self.sections:
            raise ValueError("builder draft requires at least one section")

        anchor_ids: set[str] = set()
        for item in self.anchors:
            if item.anchor_id in anchor_ids:
                raise ValueError(f"duplicate anchor_id: {item.anchor_id}")
            anchor_ids.add(item.anchor_id)

        stage_ids: set[str] = set()
        for item in self.stages:
            if item.stage_id in stage_ids:
                raise ValueError(f"duplicate stage_id: {item.stage_id}")
            stage_ids.add(item.stage_id)
        for item in self.stages:
            missing = [stage_id for stage_id in item.required_stage_ids if stage_id not in stage_ids]
            if missing:
                raise ValueError(f"stage '{item.stage_id}' requires unknown stage(s): {', '.join(missing)}")

        behavior_ids: set[str] = set()
        for item in self.dynamic_behaviors:
            if item.behavior_id in behavior_ids:
                raise ValueError(f"duplicate dynamic behavior_id: {item.behavior_id}")
            behavior_ids.add(item.behavior_id)

        collection_ids: set[str] = set()
        collection_inputs: set[str] = set()
        for item in self.dynamic_collections:
            if item.collection_id in collection_ids:
                raise ValueError(f"duplicate dynamic collection_id: {item.collection_id}")
            if item.input_id in collection_inputs:
                raise ValueError(f"input '{item.input_id}' has more than one dynamic collection")
            collection_ids.add(item.collection_id)
            collection_inputs.add(item.input_id)

        candidate_ids: set[str] = set()
        enabled_input_ids: set[str] = set()
        for item in self.inputs:
            if item.candidate_id in candidate_ids:
                raise ValueError(f"duplicate input candidate_id: {item.candidate_id}")
            candidate_ids.add(item.candidate_id)
            if item.section_id not in section_ids:
                raise ValueError(
                    f"builder input '{item.input_id}' references unknown section_id: {item.section_id}"
                )
            if item.enabled:
                if item.input_id in enabled_input_ids:
                    raise ValueError(f"duplicate enabled input_id: {item.input_id}")
                enabled_input_ids.add(item.input_id)

        output_candidates: set[str] = set()
        enabled_output_ids: set[str] = set()
        primary_count = 0
        for item in self.outputs:
            if item.candidate_id in output_candidates:
                raise ValueError(f"duplicate output candidate_id: {item.candidate_id}")
            output_candidates.add(item.candidate_id)
            if item.enabled:
                if item.output_id in enabled_output_ids:
                    raise ValueError(f"duplicate enabled output_id: {item.output_id}")
                enabled_output_ids.add(item.output_id)
                primary_count += 1 if item.primary else 0
        if primary_count > 1:
            raise ValueError("builder draft may enable at most one primary output")
        return self


class SpaceBuilderSchemaBundle(StrictContractModel):
    """Schema-only bundle used to publish one coherent JSON Schema document."""

    schema_version: Literal[SPACE_BUILDER_DRAFT_SCHEMA_VERSION] = SPACE_BUILDER_DRAFT_SCHEMA_VERSION
    draft: ComfySpaceBuilderDraft
