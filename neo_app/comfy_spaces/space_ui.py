from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.paths import SPACE_UI_FILENAME
from neo_app.comfy_spaces.schema import ComfySpaceManifest, StrictContractModel
from neo_app.comfy_spaces.type_system import (
    SpaceInputControl,
    SpaceOptionContract,
    SpaceOutputRenderer,
    SpaceTypeConstraint,
)
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_UI_SCHEMA_VERSION = "neo.comfy_space.ui.v1"

_SPACE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)


class SpaceUIContractError(ValueError):
    """Raised when durable Comfy Space presentation data is invalid."""


def _validate_id(value: str, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _SPACE_ID_RE.fullmatch(text):
        raise ValueError(f"{field_name} must use lowercase letters, numbers, dot, underscore, or hyphen")
    return text


class SpaceUISection(StrictContractModel):
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
        return _validate_id(value, field_name="section_id")

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


class SpaceUIInputPresentation(StrictContractModel):
    input_id: str
    control: SpaceInputControl
    label: str
    help_text: str = ""
    section_id: str = "main"
    order: int = 100
    advanced: bool = False
    semantic_role: str = "generic"
    source_node_id: str = ""
    source_node_label: str = ""
    model_type: str | None = None
    accepted_mime_types: list[str] = Field(default_factory=list)
    supports_multiple: bool = False
    constraints: SpaceTypeConstraint = Field(default_factory=SpaceTypeConstraint)
    options: SpaceOptionContract = Field(default_factory=SpaceOptionContract)

    @field_validator("input_id")
    @classmethod
    def validate_input_id(cls, value: str) -> str:
        return _validate_id(value, field_name="input_id")

    @field_validator("section_id")
    @classmethod
    def validate_section_id(cls, value: str) -> str:
        return _validate_id(value, field_name="section_id")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("input label must not be empty")
        return text


class SpaceUIOutputPresentation(StrictContractModel):
    output_id: str
    renderer: SpaceOutputRenderer
    label: str
    order: int = 100
    previewable: bool = False
    downloadable: bool = False

    @field_validator("output_id")
    @classmethod
    def validate_output_id(cls, value: str) -> str:
        return _validate_id(value, field_name="output_id")

    @field_validator("label")
    @classmethod
    def validate_label(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("output label must not be empty")
        return text


class SpaceUILayout(StrictContractModel):
    mode: Literal["form"] = "form"
    submit_label: str = "Create"
    results_title: str = "Results"
    sections: list[SpaceUISection] = Field(
        default_factory=lambda: [SpaceUISection(section_id="main", title="Main", order=10)]
    )

    @field_validator("submit_label", "results_title")
    @classmethod
    def validate_visible_text(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        return text

    @model_validator(mode="after")
    def validate_sections(self):
        seen: set[str] = set()
        for section in self.sections:
            if section.section_id in seen:
                raise ValueError(f"duplicate section_id: {section.section_id}")
            seen.add(section.section_id)
        if not self.sections:
            raise ValueError("Space UI requires at least one section")
        return self


class ComfySpaceUI(StrictContractModel):
    schema_version: Literal[SPACE_UI_SCHEMA_VERSION] = SPACE_UI_SCHEMA_VERSION
    kind: Literal["comfy_space_ui"] = "comfy_space_ui"
    space_id: str
    version: str
    declarative_only: Literal[True] = True
    execution_authority: Literal[False] = False
    binding_authority: Literal[False] = False
    layout: SpaceUILayout = Field(default_factory=SpaceUILayout)
    inputs: list[SpaceUIInputPresentation] = Field(default_factory=list)
    outputs: list[SpaceUIOutputPresentation] = Field(default_factory=list)

    @field_validator("space_id")
    @classmethod
    def validate_space_id(cls, value: str) -> str:
        return _validate_id(value, field_name="space_id")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not _VERSION_RE.fullmatch(text):
            raise ValueError("version must be semantic version text such as 1.0.0 or 1.2.0-beta.1")
        return text

    @model_validator(mode="after")
    def validate_references(self):
        section_ids = {item.section_id for item in self.layout.sections}
        input_ids: set[str] = set()
        for item in self.inputs:
            if item.input_id in input_ids:
                raise ValueError(f"duplicate UI input_id: {item.input_id}")
            input_ids.add(item.input_id)
            if item.section_id not in section_ids:
                raise ValueError(
                    f"UI input '{item.input_id}' references unknown section_id: {item.section_id}"
                )

        output_ids: set[str] = set()
        for item in self.outputs:
            if item.output_id in output_ids:
                raise ValueError(f"duplicate UI output_id: {item.output_id}")
            output_ids.add(item.output_id)
        return self


class SpaceUISchemaBundle(StrictContractModel):
    """Schema-only bundle used to publish one coherent JSON Schema document."""

    schema_version: Literal[SPACE_UI_SCHEMA_VERSION] = SPACE_UI_SCHEMA_VERSION
    ui: ComfySpaceUI


def parse_space_ui(payload: Mapping[str, Any]) -> ComfySpaceUI:
    if not isinstance(payload, Mapping):
        raise SpaceUIContractError("Comfy Space UI must be a JSON object")
    try:
        return model_from_dict(ComfySpaceUI, dict(payload))
    except Exception as exc:
        raise SpaceUIContractError(str(exc)) from exc


def load_space_ui(path: Path | str) -> ComfySpaceUI:
    ui_path = Path(path)
    try:
        payload = json.loads(ui_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceUIContractError(f"Unable to read Comfy Space UI: {ui_path}") from exc
    return parse_space_ui(payload)


def space_ui_payload(ui: ComfySpaceUI) -> dict[str, Any]:
    return model_to_dict(ui)


def validate_space_ui_against_manifest(
    ui: ComfySpaceUI | Mapping[str, Any],
    manifest: ComfySpaceManifest | Mapping[str, Any],
) -> dict[str, Any]:
    """Validate presentation references against execution/binding authority.

    The UI contract is intentionally incapable of defining node bindings. This
    check only proves that every durable UI control/result points at a stable ID
    that exists in the manifest and that both files describe the same Space
    identity/version.
    """

    from neo_app.comfy_spaces.contract import parse_space_manifest

    parsed_ui = ui if isinstance(ui, ComfySpaceUI) else parse_space_ui(ui)
    parsed_manifest = (
        manifest if isinstance(manifest, ComfySpaceManifest) else parse_space_manifest(dict(manifest))
    )

    errors: list[dict[str, str]] = []
    if parsed_ui.space_id != parsed_manifest.space_id:
        errors.append({"code": "space_id_mismatch", "message": "space_ui space_id does not match manifest"})
    if parsed_ui.version != parsed_manifest.version:
        errors.append({"code": "version_mismatch", "message": "space_ui version does not match manifest"})

    manifest_input_ids = {item.input_id for item in parsed_manifest.inputs}
    ui_input_ids = {item.input_id for item in parsed_ui.inputs}
    manifest_output_ids = {item.output_id for item in parsed_manifest.outputs}
    ui_output_ids = {item.output_id for item in parsed_ui.outputs}

    for missing in sorted(manifest_input_ids - ui_input_ids):
        errors.append({"code": "missing_ui_input", "message": f"manifest input '{missing}' has no UI presentation"})
    for unknown in sorted(ui_input_ids - manifest_input_ids):
        errors.append({"code": "unknown_ui_input", "message": f"space_ui references unknown input_id '{unknown}'"})
    for missing in sorted(manifest_output_ids - ui_output_ids):
        errors.append({"code": "missing_ui_output", "message": f"manifest output '{missing}' has no UI presentation"})
    for unknown in sorted(ui_output_ids - manifest_output_ids):
        errors.append({"code": "unknown_ui_output", "message": f"space_ui references unknown output_id '{unknown}'"})

    return {
        "schema_version": SPACE_UI_SCHEMA_VERSION,
        "valid": not errors,
        "execution_performed": False,
        "binding_authority": "space_manifest",
        "presentation_authority": "space_ui",
        "errors": errors,
    }
