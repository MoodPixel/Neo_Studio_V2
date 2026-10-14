from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Literal, Mapping, Sequence

from pydantic import Field, field_validator

from neo_app.comfy_spaces.graph_analyzer import normalize_comfy_api_workflow
from neo_app.comfy_spaces.schema import (
    ComfySpaceManifest,
    SpaceInputDefinition,
    SpaceOutputDefinition,
    SpaceValueKind,
    StrictContractModel,
)
from neo_app.comfy_spaces.contract import parse_space_manifest
from neo_app.core.pydantic_compat import model_to_dict


SPACE_BINDING_ENGINE_SCHEMA_VERSION = "neo.comfy_space.binding_engine.v1"
SPACE_ASSET_REFERENCE_TYPE = "neo_asset_ref"

BindingSeverity = Literal["error", "warning"]
BindingInputStatus = Literal[
    "applied",
    "applied_default",
    "applied_resolved_asset",
    "deferred_asset",
    "omitted_optional",
]
OutputResolutionStatus = Literal[
    "resolved",
    "missing_node_output",
    "missing_field",
    "missing_output_slots",
    "invalid_output_index",
    "empty_collection",
]

_ASSET_KINDS = {"image", "mask", "video", "audio", "file"}
_MISSING = object()


class SpaceBindingError(ValueError):
    """Raised when the binding API receives a malformed contract/value envelope."""


class SpaceAssetReference(StrictContractModel):
    """Portable NCS-4 asset handle.

    NCS-4 deliberately knows only the stable identity/kind. The NCS-6 neutral
    asset bus owns upload, storage, metadata, and conversion into a Comfy-ready
    filename/value before feeding the existing resolved-asset seam.
    """

    ref_type: Literal[SPACE_ASSET_REFERENCE_TYPE] = SPACE_ASSET_REFERENCE_TYPE
    asset_id: str
    kind: Literal["image", "mask", "video", "audio", "file"]

    @field_validator("asset_id")
    @classmethod
    def validate_asset_id(cls, value: str) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("asset_id must not be empty")
        if len(text) > 256:
            raise ValueError("asset_id is too long")
        return text


class SpaceBindingIssue(StrictContractModel):
    severity: BindingSeverity
    code: str
    message: str
    input_id: str | None = None
    output_id: str | None = None
    node_id: str | None = None
    field: str | None = None


class SpaceBindingValidationReport(StrictContractModel):
    schema_version: Literal[SPACE_BINDING_ENGINE_SCHEMA_VERSION] = SPACE_BINDING_ENGINE_SCHEMA_VERSION
    valid: bool
    execution_performed: Literal[False] = False
    workflow_mutated: Literal[False] = False
    input_binding_count: int = Field(default=0, ge=0)
    output_binding_count: int = Field(default=0, ge=0)
    errors: list[SpaceBindingIssue] = Field(default_factory=list)
    warnings: list[SpaceBindingIssue] = Field(default_factory=list)


class SpaceInputBindingRecord(StrictContractModel):
    input_id: str
    node_id: str
    field: str
    kind: SpaceValueKind
    source: Literal["provided", "default", "template"]
    status: BindingInputStatus
    value_supplied: bool = False
    asset_ref_count: int = Field(default=0, ge=0)


class SpaceBindingPlanReport(StrictContractModel):
    schema_version: Literal[SPACE_BINDING_ENGINE_SCHEMA_VERSION] = SPACE_BINDING_ENGINE_SCHEMA_VERSION
    ok: bool
    ready_for_execution: bool
    clone_created: bool
    source_template_unchanged: bool
    execution_performed: Literal[False] = False
    workflow_binding_implemented: Literal[True] = True
    asset_transport_implemented: Literal[False] = False
    applied_count: int = Field(default=0, ge=0)
    defaulted_count: int = Field(default=0, ge=0)
    resolved_asset_count: int = Field(default=0, ge=0)
    deferred_asset_count: int = Field(default=0, ge=0)
    omitted_optional_count: int = Field(default=0, ge=0)
    bindings: list[SpaceInputBindingRecord] = Field(default_factory=list)
    errors: list[SpaceBindingIssue] = Field(default_factory=list)
    warnings: list[SpaceBindingIssue] = Field(default_factory=list)


class SpaceOutputResolutionRecord(StrictContractModel):
    output_id: str
    node_id: str
    field: str | None = None
    output_index: int | None = Field(default=None, ge=0)
    kind: SpaceValueKind
    collection: Literal["first", "all"]
    primary: bool = False
    resolved: bool
    status: OutputResolutionStatus
    value: Any = None
    asset_collection_deferred: bool = False


class SpaceOutputResolutionReport(StrictContractModel):
    schema_version: Literal[SPACE_BINDING_ENGINE_SCHEMA_VERSION] = SPACE_BINDING_ENGINE_SCHEMA_VERSION
    ok: bool
    execution_performed: Literal[False] = False
    output_binding_resolution_implemented: Literal[True] = True
    asset_transport_implemented: Literal[False] = False
    resolved_count: int = Field(default=0, ge=0)
    unresolved_count: int = Field(default=0, ge=0)
    outputs: list[SpaceOutputResolutionRecord] = Field(default_factory=list)


class SpaceBindingSchemaBundle(StrictContractModel):
    """Schema-only bundle used to publish one coherent JSON Schema document."""

    schema_version: Literal[SPACE_BINDING_ENGINE_SCHEMA_VERSION] = SPACE_BINDING_ENGINE_SCHEMA_VERSION
    asset_reference: SpaceAssetReference
    validation_report: SpaceBindingValidationReport
    binding_report: SpaceBindingPlanReport
    output_report: SpaceOutputResolutionReport


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_manifest(value: ComfySpaceManifest | Mapping[str, Any]) -> ComfySpaceManifest:
    if isinstance(value, ComfySpaceManifest):
        return value
    if not isinstance(value, Mapping):
        raise SpaceBindingError("manifest must be a ComfySpaceManifest or mapping")
    return parse_space_manifest(dict(value))


def _is_link(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
        and not isinstance(value[1], bool)
    )


def _canonical_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> bool:
    try:
        json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError, OverflowError):
        return False
    return True


def _issue(
    severity: BindingSeverity,
    code: str,
    message: str,
    *,
    input_id: str | None = None,
    output_id: str | None = None,
    node_id: str | None = None,
    field: str | None = None,
) -> SpaceBindingIssue:
    return SpaceBindingIssue(
        severity=severity,
        code=code,
        message=message,
        input_id=input_id,
        output_id=output_id,
        node_id=node_id,
        field=field,
    )


def _object_info_input_names(object_info: Mapping[str, Any] | None, class_type: str) -> set[str] | None:
    if not object_info:
        return None
    node_info = _mapping(object_info.get(class_type))
    if not node_info:
        return set()
    input_root = _mapping(node_info.get("input"))
    names: set[str] = set()
    for section in ("required", "optional", "hidden"):
        names.update(str(name) for name in _mapping(input_root.get(section)).keys())
    return names


def _object_info_output_count(object_info: Mapping[str, Any] | None, class_type: str) -> int | None:
    if not object_info:
        return None
    node_info = _mapping(object_info.get(class_type))
    if not node_info:
        return 0
    outputs = node_info.get("output")
    return len(outputs) if isinstance(outputs, Sequence) and not isinstance(outputs, (str, bytes, bytearray)) else 0


def validate_space_bindings(
    workflow: Mapping[str, Any],
    manifest: ComfySpaceManifest | Mapping[str, Any],
    *,
    object_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate manifest locators against a Comfy API graph without mutating it.

    Input bindings must target literal/template fields. Neo never overwrites a
    graph link through the user-control binding path. Output fields are runtime
    history/result locators and therefore cannot always be proven statically;
    output indexes are range-checked when live ``object_info`` is supplied.
    """

    graph, _ = normalize_comfy_api_workflow(workflow)
    space = _as_manifest(manifest)
    errors: list[SpaceBindingIssue] = []
    warnings: list[SpaceBindingIssue] = []

    for item in space.inputs:
        node_id = item.binding.node_id
        field = item.binding.field
        node = _mapping(graph.get(node_id))
        if not node:
            errors.append(_issue(
                "error", "missing_input_node",
                f"Input '{item.input_id}' targets missing node {node_id}.",
                input_id=item.input_id, node_id=node_id, field=field,
            ))
            continue
        inputs = _mapping(node.get("inputs"))
        if field not in inputs:
            errors.append(_issue(
                "error", "missing_input_field",
                f"Input '{item.input_id}' targets missing template field {node_id}.{field}.",
                input_id=item.input_id, node_id=node_id, field=field,
            ))
            continue
        if _is_link(inputs[field]):
            errors.append(_issue(
                "error", "linked_input_target",
                f"Input '{item.input_id}' cannot replace linked socket {node_id}.{field}; expose a literal input instead.",
                input_id=item.input_id, node_id=node_id, field=field,
            ))

        class_type = str(node.get("class_type") or "").strip()
        runtime_names = _object_info_input_names(object_info, class_type)
        if runtime_names is not None and field not in runtime_names:
            warnings.append(_issue(
                "warning", "runtime_input_not_declared",
                f"Selected runtime does not declare {class_type}.{field}; re-analyze the workflow before publishing.",
                input_id=item.input_id, node_id=node_id, field=field,
            ))

    for item in space.outputs:
        node_id = item.binding.node_id
        node = _mapping(graph.get(node_id))
        if not node:
            errors.append(_issue(
                "error", "missing_output_node",
                f"Output '{item.output_id}' targets missing node {node_id}.",
                output_id=item.output_id, node_id=node_id, field=item.binding.field,
            ))
            continue
        class_type = str(node.get("class_type") or "").strip()
        if item.binding.output_index is not None:
            output_count = _object_info_output_count(object_info, class_type)
            if output_count is not None and item.binding.output_index >= output_count:
                errors.append(_issue(
                    "error", "invalid_output_index",
                    f"Output '{item.output_id}' targets socket {item.binding.output_index}, but {class_type} exposes {output_count} output(s).",
                    output_id=item.output_id, node_id=node_id, field=item.binding.field,
                ))

    report = SpaceBindingValidationReport(
        valid=not errors,
        input_binding_count=len(space.inputs),
        output_binding_count=len(space.outputs),
        errors=errors,
        warnings=warnings,
    )
    return model_to_dict(report)


def _validate_asset_ref(value: Any, kind: str) -> SpaceAssetReference:
    if not isinstance(value, Mapping):
        raise SpaceBindingError(
            f"{kind} inputs require a {SPACE_ASSET_REFERENCE_TYPE} object; raw paths/filenames are not accepted by NCS-4"
        )
    try:
        ref = SpaceAssetReference(**dict(value))
    except Exception as exc:
        raise SpaceBindingError(str(exc)) from exc
    if ref.kind != kind:
        raise SpaceBindingError(f"asset kind mismatch: expected {kind}, got {ref.kind}")
    return ref


def _validate_scalar_kind(kind: str, value: Any) -> None:
    if kind == "text" and not isinstance(value, str):
        raise SpaceBindingError("text input requires a string")
    if kind == "integer" and (not isinstance(value, int) or isinstance(value, bool)):
        raise SpaceBindingError("integer input requires an integer")
    if kind == "number" and (not isinstance(value, (int, float)) or isinstance(value, bool)):
        raise SpaceBindingError("number input requires an integer or float")
    if kind == "boolean" and not isinstance(value, bool):
        raise SpaceBindingError("boolean input requires true/false")
    if kind == "choice" and not isinstance(value, (str, int, float, bool)):
        raise SpaceBindingError("choice input requires a scalar choice value")
    if kind == "json" and not _json_safe(value):
        raise SpaceBindingError("json input must be JSON serializable")


def _validate_input_value(definition: SpaceInputDefinition, value: Any) -> int:
    values: list[Any]
    if definition.multiplicity == "multiple":
        if not isinstance(value, (list, tuple)):
            raise SpaceBindingError(f"input '{definition.input_id}' requires multiple values")
        values = list(value)
        if definition.required and not values:
            raise SpaceBindingError(f"input '{definition.input_id}' requires at least one value")
    else:
        values = [value]

    asset_count = 0
    for item in values:
        if definition.kind in _ASSET_KINDS:
            _validate_asset_ref(item, definition.kind)
            asset_count += 1
        else:
            _validate_scalar_kind(definition.kind, item)
    return asset_count


def bind_space_inputs(
    workflow: Mapping[str, Any],
    manifest: ComfySpaceManifest | Mapping[str, Any],
    values: Mapping[str, Any] | None = None,
    *,
    resolved_asset_values: Mapping[str, Any] | None = None,
    object_info: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Clone a workflow and inject reviewed user values into literal node fields.

    Asset controls accept only portable ``neo_asset_ref`` objects. NCS-4 itself
    does not transport them; the NCS-6 neutral asset bus resolves trusted refs and
    supplies ``resolved_asset_values`` through this seam. This hook remains data
    injection only and performs no upload, filesystem lookup, or Comfy run.
    """

    graph, _ = normalize_comfy_api_workflow(workflow)
    space = _as_manifest(manifest)
    supplied = dict(values or {})
    resolved_assets = dict(resolved_asset_values or {})
    validation = validate_space_bindings(graph, space, object_info=object_info)

    errors = [SpaceBindingIssue(**item) for item in validation["errors"]]
    warnings = [SpaceBindingIssue(**item) for item in validation["warnings"]]
    known_ids = {item.input_id for item in space.inputs}
    input_by_id = {item.input_id: item for item in space.inputs}

    unknown_supplied = [key for key in supplied if not isinstance(key, str) or key not in known_ids]
    for unknown in sorted(unknown_supplied, key=lambda item: str(item)):
        token = str(unknown)
        errors.append(_issue("error", "unknown_input_id", f"Unknown Space input: {token}", input_id=token))

    unknown_resolved = [key for key in resolved_assets if not isinstance(key, str) or key not in known_ids]
    for unknown in sorted(unknown_resolved, key=lambda item: str(item)):
        token = str(unknown)
        errors.append(_issue("error", "unknown_resolved_asset_id", f"Resolved asset does not match a Space input: {token}", input_id=token))

    for input_id in sorted(set(resolved_assets).intersection(known_ids)):
        if input_by_id[input_id].kind not in _ASSET_KINDS:
            errors.append(_issue(
                "error", "resolved_value_for_non_asset_input",
                f"Resolved asset values are only valid for media/file inputs: {input_id}",
                input_id=input_id,
                node_id=input_by_id[input_id].binding.node_id,
                field=input_by_id[input_id].binding.field,
            ))

    # Structural locator conflicts fail before any clone can be treated as usable.
    # This prevents a stale/malicious manifest from accidentally creating missing
    # node fields or replacing a linked graph socket.
    if errors:
        report = SpaceBindingPlanReport(
            ok=False,
            ready_for_execution=False,
            clone_created=False,
            source_template_unchanged=True,
            errors=errors,
            warnings=warnings,
        )
        return {"ok": False, "workflow": None, "report": model_to_dict(report)}

    source_digest_before = _canonical_digest(graph)
    clone = deepcopy(graph)
    records: list[SpaceInputBindingRecord] = []
    applied_count = 0
    defaulted_count = 0
    resolved_asset_count = 0
    deferred_asset_count = 0
    omitted_optional_count = 0

    for definition in space.inputs:
        input_id = definition.input_id
        if input_id in supplied:
            value = supplied[input_id]
            source = "provided"
            status: BindingInputStatus = "applied"
        elif definition.default is not None:
            value = deepcopy(definition.default)
            source = "default"
            status = "applied_default"
        elif definition.required:
            errors.append(_issue(
                "error", "missing_required_input",
                f"Required Space input '{input_id}' was not supplied and has no default.",
                input_id=input_id, node_id=definition.binding.node_id, field=definition.binding.field,
            ))
            continue
        else:
            records.append(SpaceInputBindingRecord(
                input_id=input_id,
                node_id=definition.binding.node_id,
                field=definition.binding.field,
                kind=definition.kind,
                source="template",
                status="omitted_optional",
                value_supplied=False,
            ))
            omitted_optional_count += 1
            continue

        try:
            asset_ref_count = _validate_input_value(definition, value)
        except SpaceBindingError as exc:
            errors.append(_issue(
                "error", "invalid_input_value", str(exc),
                input_id=input_id, node_id=definition.binding.node_id, field=definition.binding.field,
            ))
            continue

        node = _mapping(clone.get(definition.binding.node_id))
        node_inputs = _mapping(node.get("inputs"))

        if definition.kind in _ASSET_KINDS:
            if input_id not in resolved_assets:
                records.append(SpaceInputBindingRecord(
                    input_id=input_id,
                    node_id=definition.binding.node_id,
                    field=definition.binding.field,
                    kind=definition.kind,
                    source=source,  # type: ignore[arg-type]
                    status="deferred_asset",
                    value_supplied=True,
                    asset_ref_count=asset_ref_count,
                ))
                deferred_asset_count += 1
                continue
            runtime_value = resolved_assets[input_id]
            if not _json_safe(runtime_value):
                errors.append(_issue(
                    "error", "invalid_resolved_asset_value",
                    f"Resolved asset value for '{input_id}' is not JSON serializable.",
                    input_id=input_id, node_id=definition.binding.node_id, field=definition.binding.field,
                ))
                continue
            node_inputs[definition.binding.field] = deepcopy(runtime_value)
            node["inputs"] = node_inputs
            clone[definition.binding.node_id] = node
            records.append(SpaceInputBindingRecord(
                input_id=input_id,
                node_id=definition.binding.node_id,
                field=definition.binding.field,
                kind=definition.kind,
                source=source,  # type: ignore[arg-type]
                status="applied_resolved_asset",
                value_supplied=True,
                asset_ref_count=asset_ref_count,
            ))
            applied_count += 1
            resolved_asset_count += 1
            if source == "default":
                defaulted_count += 1
            continue

        node_inputs[definition.binding.field] = deepcopy(value)
        node["inputs"] = node_inputs
        clone[definition.binding.node_id] = node
        records.append(SpaceInputBindingRecord(
            input_id=input_id,
            node_id=definition.binding.node_id,
            field=definition.binding.field,
            kind=definition.kind,
            source=source,  # type: ignore[arg-type]
            status=status,
            value_supplied=True,
        ))
        applied_count += 1
        if source == "default":
            defaulted_count += 1

    source_digest_after = _canonical_digest(graph)
    source_unchanged = source_digest_before == source_digest_after
    if not source_unchanged:
        errors.append(_issue("error", "template_mutation_detected", "Source workflow changed during binding; result rejected."))

    ok = not errors
    ready_for_execution = ok and deferred_asset_count == 0
    report = SpaceBindingPlanReport(
        ok=ok,
        ready_for_execution=ready_for_execution,
        clone_created=True,
        source_template_unchanged=source_unchanged,
        applied_count=applied_count,
        defaulted_count=defaulted_count,
        resolved_asset_count=resolved_asset_count,
        deferred_asset_count=deferred_asset_count,
        omitted_optional_count=omitted_optional_count,
        bindings=records,
        errors=errors,
        warnings=warnings,
    )
    return {
        "ok": ok,
        "workflow": clone if ok else None,
        "report": model_to_dict(report),
    }


def _sequence_value(value: Any) -> list[Any] | None:
    if isinstance(value, (list, tuple)):
        return list(value)
    return None


def _resolve_output_value(node_payload: Any, definition: SpaceOutputDefinition) -> tuple[bool, OutputResolutionStatus, Any]:
    binding = definition.binding
    payload = node_payload

    if binding.field is not None:
        payload_map = _mapping(payload)
        if binding.field not in payload_map:
            return False, "missing_field", None
        value = payload_map[binding.field]
    else:
        slots = _sequence_value(payload)
        if slots is None:
            payload_map = _mapping(payload)
            slots = _sequence_value(payload_map.get("outputs"))
            if slots is None:
                slots = _sequence_value(payload_map.get("_neo_output_slots"))
        if slots is None:
            return False, "missing_output_slots", None
        index = int(binding.output_index or 0)
        if index >= len(slots):
            return False, "invalid_output_index", None
        value = slots[index]

    if definition.collection == "all":
        sequence = _sequence_value(value)
        resolved = sequence if sequence is not None else [value]
        return True, "resolved", resolved

    sequence = _sequence_value(value)
    if sequence is not None:
        if not sequence:
            return False, "empty_collection", None
        return True, "resolved", sequence[0]
    return True, "resolved", value


def resolve_space_outputs(
    execution_outputs: Mapping[str, Any],
    manifest: ComfySpaceManifest | Mapping[str, Any],
) -> dict[str, Any]:
    """Resolve declared output locators from already-supplied Comfy result data.

    This function does **not** run Comfy and does not copy/download artifacts.
    Media/file values are deliberately returned as raw result payloads with
    ``asset_collection_deferred=True`` until NCS-7 supplies runtime artifact
    coordinates to the NCS-6 collection service.
    """

    space = _as_manifest(manifest)
    result_root = {str(key): value for key, value in dict(execution_outputs or {}).items()}
    records: list[SpaceOutputResolutionRecord] = []
    resolved_count = 0

    for definition in space.outputs:
        node_id = definition.binding.node_id
        if node_id not in result_root:
            records.append(SpaceOutputResolutionRecord(
                output_id=definition.output_id,
                node_id=node_id,
                field=definition.binding.field,
                output_index=definition.binding.output_index,
                kind=definition.kind,
                collection=definition.collection,
                primary=definition.primary,
                resolved=False,
                status="missing_node_output",
                asset_collection_deferred=definition.kind in _ASSET_KINDS,
            ))
            continue

        resolved, status, value = _resolve_output_value(result_root[node_id], definition)
        if resolved:
            resolved_count += 1
        records.append(SpaceOutputResolutionRecord(
            output_id=definition.output_id,
            node_id=node_id,
            field=definition.binding.field,
            output_index=definition.binding.output_index,
            kind=definition.kind,
            collection=definition.collection,
            primary=definition.primary,
            resolved=resolved,
            status=status,
            value=deepcopy(value),
            asset_collection_deferred=definition.kind in _ASSET_KINDS,
        ))

    report = SpaceOutputResolutionReport(
        ok=resolved_count == len(space.outputs),
        resolved_count=resolved_count,
        unresolved_count=len(space.outputs) - resolved_count,
        outputs=records,
    )
    return model_to_dict(report)


def binding_engine_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_BINDING_ENGINE_SCHEMA_VERSION,
        "template_policy": "clone_then_bind",
        "workflow_binding_implemented": True,
        "output_binding_resolution_implemented": True,
        "linked_socket_overwrite_allowed": False,
        "raw_filesystem_asset_paths_allowed": False,
        "asset_reference_type": SPACE_ASSET_REFERENCE_TYPE,
        "asset_transport_implemented": False,
        "workflow_execution_implemented": False,
    }
