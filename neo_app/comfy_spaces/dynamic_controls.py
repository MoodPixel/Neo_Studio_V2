from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator, model_validator

from neo_app.comfy_spaces.schema import ComfySpaceManifest, StrictContractModel
from neo_app.comfy_spaces.space_ui import ComfySpaceUI
from neo_app.comfy_spaces.stages import SpaceStageSidecar
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict


SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION = "neo.comfy_space.dynamic_controls.v1"

_SAFE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,127}$")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")

DynamicSource = Literal["input", "stage_enabled", "addon_present", "variant_active"]
DynamicOperator = Literal[
    "equals", "not_equals", "in", "not_in", "truthy", "falsy", "exists",
    "gt", "gte", "lt", "lte",
]
DynamicTargetType = Literal["input", "section"]
DynamicEffect = Literal["visible", "enabled"]
DynamicConditionMode = Literal["all", "any"]
DynamicCollectionMode = Literal["repeater", "reference_list"]


class SpaceDynamicControlsError(ValueError):
    """Raised when a declarative dynamic-control contract is invalid."""


def _safe_id(value: str, *, field_name: str) -> str:
    text = str(value or "").strip().lower()
    if not _SAFE_ID_RE.fullmatch(text):
        raise ValueError(f"{field_name} must use lowercase letters, numbers, dot, underscore, or hyphen")
    return text


class SpaceDynamicPredicate(StrictContractModel):
    source: DynamicSource
    ref: str = ""
    operator: DynamicOperator = "truthy"
    value: Any = None

    @field_validator("ref")
    @classmethod
    def validate_ref(cls, value: str) -> str:
        text = str(value or "").strip()
        if text and not _REF_RE.fullmatch(text):
            raise ValueError("dynamic predicate ref contains unsupported characters")
        return text

    @model_validator(mode="after")
    def validate_source_contract(self):
        if self.source == "variant_active":
            if self.ref:
                raise ValueError("variant_active predicates do not use ref")
        elif not self.ref:
            raise ValueError(f"{self.source} predicates require ref")
        if self.operator in {"in", "not_in"} and not isinstance(self.value, list):
            raise ValueError(f"{self.operator} requires a list value")
        if self.operator in {"gt", "gte", "lt", "lte"} and (
            not isinstance(self.value, (int, float)) or isinstance(self.value, bool)
        ):
            raise ValueError(f"{self.operator} requires a numeric value")
        return self


class SpaceDynamicCondition(StrictContractModel):
    mode: DynamicConditionMode = "all"
    predicates: list[SpaceDynamicPredicate] = Field(min_length=1, max_length=16)
    negate: bool = False


class SpaceDynamicBehavior(StrictContractModel):
    behavior_id: str
    target_type: DynamicTargetType
    target_id: str
    effect: DynamicEffect
    condition: SpaceDynamicCondition

    @field_validator("behavior_id", "target_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return _safe_id(value, field_name=info.field_name)


class SpaceDynamicCollection(StrictContractModel):
    collection_id: str
    input_id: str
    mode: DynamicCollectionMode = "repeater"
    label: str = "Items"
    item_label: str = "Item"
    min_items: int = Field(default=0, ge=0, le=64)
    max_items: int = Field(default=8, ge=1, le=64)
    allow_reorder: bool = True

    @field_validator("collection_id", "input_id")
    @classmethod
    def validate_ids(cls, value: str, info) -> str:
        return _safe_id(value, field_name=info.field_name)

    @field_validator("label", "item_label")
    @classmethod
    def validate_labels(cls, value: str, info) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError(f"{info.field_name} must not be empty")
        return text

    @model_validator(mode="after")
    def validate_bounds(self):
        if self.min_items > self.max_items:
            raise ValueError("min_items cannot exceed max_items")
        return self


class ComfySpaceDynamicControls(StrictContractModel):
    schema_version: Literal[SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION] = SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION
    kind: Literal["comfy_space_dynamic_controls"] = "comfy_space_dynamic_controls"
    space_id: str
    version: str
    declarative_only: Literal[True] = True
    execution_authority: Literal[False] = False
    binding_authority: Literal[False] = False
    generated_neo_javascript: Literal[False] = False
    behaviors: list[SpaceDynamicBehavior] = Field(default_factory=list)
    collections: list[SpaceDynamicCollection] = Field(default_factory=list)

    @field_validator("space_id")
    @classmethod
    def validate_space_id(cls, value: str) -> str:
        return _safe_id(value, field_name="space_id")

    @field_validator("version")
    @classmethod
    def validate_version(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("version must not be empty")
        return text

    @model_validator(mode="after")
    def validate_unique_ids(self):
        behavior_ids: set[str] = set()
        for item in self.behaviors:
            if item.behavior_id in behavior_ids:
                raise ValueError(f"duplicate behavior_id: {item.behavior_id}")
            behavior_ids.add(item.behavior_id)
        collection_ids: set[str] = set()
        collection_inputs: set[str] = set()
        for item in self.collections:
            if item.collection_id in collection_ids:
                raise ValueError(f"duplicate collection_id: {item.collection_id}")
            if item.input_id in collection_inputs:
                raise ValueError(f"input '{item.input_id}' has more than one dynamic collection")
            collection_ids.add(item.collection_id)
            collection_inputs.add(item.input_id)
        return self


class SpaceDynamicControlsSchemaBundle(StrictContractModel):
    schema_version: Literal[SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION] = SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION
    dynamic_controls: ComfySpaceDynamicControls


def parse_dynamic_controls(payload: Mapping[str, Any]) -> ComfySpaceDynamicControls:
    try:
        return model_from_dict(ComfySpaceDynamicControls, dict(payload))
    except Exception as exc:
        raise SpaceDynamicControlsError(str(exc)) from exc


def dynamic_controls_payload(value: ComfySpaceDynamicControls | Mapping[str, Any]) -> dict[str, Any]:
    model = value if isinstance(value, ComfySpaceDynamicControls) else parse_dynamic_controls(value)
    return model_to_dict(model)


def load_dynamic_controls(path: Path | str) -> ComfySpaceDynamicControls:
    source = Path(path)
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceDynamicControlsError(f"Unable to read dynamic controls: {source}") from exc
    if not isinstance(payload, Mapping):
        raise SpaceDynamicControlsError("Dynamic controls root must be a JSON object")
    return parse_dynamic_controls(payload)


def validate_dynamic_controls(
    dynamic_controls: ComfySpaceDynamicControls | Mapping[str, Any],
    manifest: ComfySpaceManifest | Mapping[str, Any],
    ui: ComfySpaceUI | Mapping[str, Any],
    stages: SpaceStageSidecar | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    from neo_app.comfy_spaces.contract import parse_space_manifest
    from neo_app.comfy_spaces.space_ui import parse_space_ui
    from neo_app.comfy_spaces.stages import parse_stage_sidecar

    dyn = dynamic_controls if isinstance(dynamic_controls, ComfySpaceDynamicControls) else parse_dynamic_controls(dynamic_controls)
    man = manifest if isinstance(manifest, ComfySpaceManifest) else parse_space_manifest(dict(manifest))
    view = ui if isinstance(ui, ComfySpaceUI) else parse_space_ui(dict(ui))
    stage_model = None
    if stages is not None:
        stage_model = stages if isinstance(stages, SpaceStageSidecar) else parse_stage_sidecar(stages)

    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if dyn.space_id != man.space_id or dyn.space_id != view.space_id:
        errors.append({"code": "space_id_mismatch", "message": "Dynamic controls identity does not match the Space"})
    if dyn.version != man.version or dyn.version != view.version:
        errors.append({"code": "version_mismatch", "message": "Dynamic controls version does not match the Space"})

    manifest_inputs = {item.input_id: item for item in man.inputs}
    ui_inputs = {item.input_id for item in view.inputs}
    section_ids = {item.section_id for item in view.layout.sections}
    stage_ids = {item.stage_id for item in stage_model.stages} if stage_model else set()

    for behavior in dyn.behaviors:
        if behavior.target_type == "input" and behavior.target_id not in ui_inputs:
            errors.append({"code": "unknown_behavior_input", "message": f"Behavior '{behavior.behavior_id}' targets unknown input '{behavior.target_id}'"})
        if behavior.target_type == "section" and behavior.target_id not in section_ids:
            errors.append({"code": "unknown_behavior_section", "message": f"Behavior '{behavior.behavior_id}' targets unknown section '{behavior.target_id}'"})
        for predicate in behavior.condition.predicates:
            if predicate.source == "input" and predicate.ref not in manifest_inputs:
                errors.append({"code": "unknown_condition_input", "message": f"Behavior '{behavior.behavior_id}' references unknown input '{predicate.ref}'"})
            if predicate.source == "stage_enabled" and predicate.ref not in stage_ids:
                errors.append({"code": "unknown_condition_stage", "message": f"Behavior '{behavior.behavior_id}' references unknown stage '{predicate.ref}'"})

    for collection in dyn.collections:
        definition = manifest_inputs.get(collection.input_id)
        if definition is None:
            errors.append({"code": "unknown_collection_input", "message": f"Collection '{collection.collection_id}' targets unknown input '{collection.input_id}'"})
            continue
        if definition.multiplicity != "multiple":
            errors.append({"code": "collection_requires_multiple", "message": f"Collection '{collection.collection_id}' requires manifest input '{collection.input_id}' multiplicity=multiple"})
            continue
        asset = definition.kind in {"image", "mask", "video", "audio", "file"}
        if collection.mode == "reference_list" and not asset:
            errors.append({"code": "reference_list_requires_asset", "message": f"Reference list '{collection.collection_id}' requires an asset input"})
        if collection.mode == "repeater" and asset:
            warnings.append({"code": "asset_repeater_normalized", "message": f"Asset input '{collection.input_id}' is better represented as a reference_list"})

    return {
        "schema_version": SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION,
        "valid": not errors,
        "behavior_count": len(dyn.behaviors),
        "collection_count": len(dyn.collections),
        "errors": errors,
        "warnings": warnings,
        "execution_authority": False,
        "generated_neo_javascript": False,
    }


def _source_value(
    predicate: SpaceDynamicPredicate,
    *,
    values: Mapping[str, Any],
    enabled_stage_ids: Sequence[str],
    addon_module_ids: Sequence[str],
    variant_active: bool,
) -> Any:
    if predicate.source == "input":
        return values.get(predicate.ref)
    if predicate.source == "stage_enabled":
        return predicate.ref in set(enabled_stage_ids)
    if predicate.source == "addon_present":
        return predicate.ref in set(addon_module_ids)
    if predicate.source == "variant_active":
        return bool(variant_active)
    return None


def _predicate_matches(predicate: SpaceDynamicPredicate, actual: Any) -> bool:
    op = predicate.operator
    expected = predicate.value
    if op == "equals":
        return actual == expected
    if op == "not_equals":
        return actual != expected
    if op == "in":
        return actual in (expected or [])
    if op == "not_in":
        return actual not in (expected or [])
    if op == "truthy":
        return bool(actual)
    if op == "falsy":
        return not bool(actual)
    if op == "exists":
        return actual is not None and actual != ""
    if op in {"gt", "gte", "lt", "lte"}:
        if not isinstance(actual, (int, float)) or isinstance(actual, bool):
            return False
        if op == "gt":
            return actual > expected
        if op == "gte":
            return actual >= expected
        if op == "lt":
            return actual < expected
        return actual <= expected
    return False


def evaluate_dynamic_condition(
    condition: SpaceDynamicCondition | Mapping[str, Any],
    *,
    values: Mapping[str, Any] | None = None,
    enabled_stage_ids: Sequence[str] | None = None,
    addon_module_ids: Sequence[str] | None = None,
    variant_active: bool = False,
) -> bool:
    model = condition if isinstance(condition, SpaceDynamicCondition) else model_from_dict(SpaceDynamicCondition, dict(condition))
    results = [
        _predicate_matches(
            predicate,
            _source_value(
                predicate,
                values=dict(values or {}),
                enabled_stage_ids=list(enabled_stage_ids or []),
                addon_module_ids=list(addon_module_ids or []),
                variant_active=variant_active,
            ),
        )
        for predicate in model.predicates
    ]
    matched = all(results) if model.mode == "all" else any(results)
    return not matched if model.negate else matched


def evaluate_dynamic_controls(
    dynamic_controls: ComfySpaceDynamicControls | Mapping[str, Any],
    *,
    values: Mapping[str, Any] | None = None,
    enabled_stage_ids: Sequence[str] | None = None,
    addon_module_ids: Sequence[str] | None = None,
    variant_active: bool = False,
) -> dict[str, Any]:
    dyn = dynamic_controls if isinstance(dynamic_controls, ComfySpaceDynamicControls) else parse_dynamic_controls(dynamic_controls)
    state: dict[str, dict[str, bool]] = {}
    for behavior in dyn.behaviors:
        key = f"{behavior.target_type}:{behavior.target_id}"
        target = state.setdefault(key, {"visible": True, "enabled": True})
        matched = evaluate_dynamic_condition(
            behavior.condition,
            values=values,
            enabled_stage_ids=enabled_stage_ids,
            addon_module_ids=addon_module_ids,
            variant_active=variant_active,
        )
        # Multiple rules targeting the same effect compose conservatively: all must pass.
        target[behavior.effect] = bool(target[behavior.effect] and matched)
    return {
        "schema_version": SPACE_DYNAMIC_CONTROLS_SCHEMA_VERSION,
        "states": state,
        "behavior_count": len(dyn.behaviors),
        "execution_mutated": False,
        "binding_mutated": False,
    }


def dynamic_controls_json_schema() -> dict[str, Any]:
    return SpaceDynamicControlsSchemaBundle.model_json_schema()
