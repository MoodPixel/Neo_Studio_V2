from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from uuid import uuid4

from neo_app.comfy_spaces.asset_bus import (
    SpaceAssetBusError,
    asset_content_url,
    collect_comfy_artifact,
    prepare_space_asset_bindings,
)
from neo_app.comfy_spaces.binding_engine import resolve_space_outputs
from neo_app.comfy_spaces.contract import (
    SpaceContractError,
    load_space_manifest,
    manifest_payload,
    parse_space_manifest,
    validate_space_package,
)
from neo_app.comfy_spaces.dynamic_controls import (
    dynamic_controls_payload,
    parse_dynamic_controls,
    validate_dynamic_controls,
)
from neo_app.comfy_spaces.execution_schema import (
    SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION,
    SpaceExecutionIssue,
    SpaceExecutionOutputRecord,
    SpaceExecutionPreflightReport,
    SpaceExecutionRunResult,
    SpaceRunProgressEvent,
    SpaceRunProgressSnapshot,
)
from neo_app.comfy_spaces.graph_analyzer import SpaceWorkflowAnalysisError, analyze_comfy_workflow
from neo_app.comfy_spaces.graph_addons import (
    SpaceGraphAddonError,
    compose_graph_addons,
    parse_graph_addon_requests,
)
from neo_app.comfy_spaces.stages import (
    SpaceStageError,
    parse_stage_sidecar,
    prune_workflow_to_output_nodes,
    resolve_stage_execution,
    stage_sidecar_payload,
    validate_stages_against_manifest,
)
from neo_app.comfy_spaces.variants import SpaceVariantError, resolve_space_variant
from neo_app.comfy_spaces.graph_anchors import (
    graph_anchor_sidecar_payload,
    parse_graph_anchor_sidecar,
)
from neo_app.comfy_spaces.run_progress import (
    build_space_graph_plan,
    ensure_space_run_monitor,
    get_space_run_progress,
    list_space_runs as list_persistent_space_runs,
    progress_runtime_capabilities,
    progress_snapshot_from_record,
    record_space_run_progress,
    recover_space_run_monitors,
)
from neo_app.comfy_spaces.paths import (
    comfy_space_graph_anchors_path,
    comfy_space_manifest_path,
    comfy_space_stages_path,
    comfy_space_dynamic_controls_path,
    comfy_space_publication_receipt_path,
    comfy_space_migration_path,
    comfy_space_ui_path,
)
from neo_app.comfy_spaces.publish_schema import ComfySpacePublicationReceipt
from neo_app.comfy_spaces.versioning import SpaceMigrationAudit
from neo_app.comfy_spaces.runtime_probe import SpaceRuntimeProbeError, probe_comfy_profile_object_info
from neo_app.comfy_spaces.schema import ComfySpaceManifest
from neo_app.comfy_spaces.space_ui import (
    ComfySpaceUI,
    SpaceUIContractError,
    load_space_ui,
    space_ui_payload,
    validate_space_ui_against_manifest,
)
from neo_app.core.pydantic_compat import model_from_dict, model_to_dict
from neo_app.providers.comfy_execution_transport import (
    ComfyExecutionTransportError,
    cancel_comfy_prompt,
    execution_transport_capabilities,
    get_comfy_prompt_history,
    get_comfy_queue_state,
    resolve_comfy_execution_profile,
    submit_comfy_prompt,
)
from neo_app.runtime.job_registry import GenerationJobRegistry, get_generation_job_registry
from neo_app.services.comfy_gpu_lifecycle import ComfyGpuBusyError, get_comfy_gpu_lifecycle_manager


SPACE_EXECUTION_RUNTIME_SERVICE_VERSION = "neo.comfy_space.execution_service.v3"
_SURFACE = "comfy_spaces"
_ASSET_KINDS = {"image", "mask", "video", "audio", "file"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled", "canceled"}


class SpaceExecutionRuntimeError(RuntimeError):
    """Raised when a published Space cannot be safely prepared or executed."""

    def __init__(self, message: str, *, stage: str = "runtime", code: str = "execution_runtime_error") -> None:
        super().__init__(message)
        self.stage = str(stage or "runtime")
        self.code = str(code or "execution_runtime_error")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, tuple):
        return list(value)
    return None


def _canonical_json_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SpaceExecutionRuntimeError(f"Unable to hash published Space file: {path.name}", stage="package", code="package_file_unreadable") from exc
    return digest.hexdigest()


def _read_json(path: Path, *, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceExecutionRuntimeError(f"Unable to read {label}: {path.name}", stage="package", code="package_file_invalid") from exc


def _issue(code: str, message: str, stage: str, **extra: Any) -> SpaceExecutionIssue:
    allowed = {key: value for key, value in extra.items() if key in {"node_id", "input_id", "output_id"} and value not in (None, "")}
    return SpaceExecutionIssue(code=code, message=message, stage=stage, **allowed)


def _job_registry(root_dir: Path | str | None = None) -> GenerationJobRegistry:
    return get_generation_job_registry() if root_dir is None else GenerationJobRegistry(root_dir=root_dir)


def _load_published_space_package(
    space_id: str,
    version: str,
    *,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    manifest_path = comfy_space_manifest_path(space_id, version, root_dir)
    ui_path = comfy_space_ui_path(space_id, version, root_dir)
    graph_anchors_path = comfy_space_graph_anchors_path(space_id, version, root_dir)
    stages_path = comfy_space_stages_path(space_id, version, root_dir)
    dynamic_controls_path = comfy_space_dynamic_controls_path(space_id, version, root_dir)
    receipt_path = comfy_space_publication_receipt_path(space_id, version, root_dir)
    migration_path = comfy_space_migration_path(space_id, version, root_dir)

    try:
        package = validate_space_package(manifest_path)
        manifest = load_space_manifest(manifest_path)
    except (SpaceContractError, ValueError) as exc:
        raise SpaceExecutionRuntimeError(str(exc), stage="package", code="package_invalid") from exc

    if manifest.status != "published":
        raise SpaceExecutionRuntimeError("Comfy Space execution requires a published Space version.", stage="package", code="space_not_published")
    if not manifest.trust.declarative_only or manifest.trust.neo_python_execution or manifest.trust.neo_javascript_execution:
        raise SpaceExecutionRuntimeError("Published Space trust contract is not safe for declarative execution.", stage="package", code="trust_contract_invalid")
    if not manifest.runtime.shared_queue or not manifest.runtime.dependency_preflight_required:
        raise SpaceExecutionRuntimeError("Published Space runtime contract does not require Neo shared queue/preflight.", stage="package", code="runtime_contract_invalid")

    try:
        ui = load_space_ui(ui_path)
    except SpaceUIContractError as exc:
        raise SpaceExecutionRuntimeError(str(exc), stage="package", code="space_ui_invalid") from exc
    ui_check = validate_space_ui_against_manifest(ui, manifest)
    if not ui_check.get("valid"):
        message = "; ".join(str(item.get("message") or "UI/manifest mismatch") for item in ui_check.get("errors") or [])
        raise SpaceExecutionRuntimeError(message or "Published Space UI does not match the manifest.", stage="package", code="space_ui_manifest_mismatch")

    raw_receipt = _read_json(receipt_path, label="publication receipt")
    try:
        receipt = model_from_dict(ComfySpacePublicationReceipt, raw_receipt)
    except Exception as exc:
        raise SpaceExecutionRuntimeError(f"Invalid publication receipt: {exc}", stage="package", code="publication_receipt_invalid") from exc

    if receipt.space_id != manifest.space_id or receipt.version != manifest.version:
        raise SpaceExecutionRuntimeError("Publication receipt identity does not match the published Space.", stage="package", code="publication_receipt_identity_mismatch")
    if not receipt.trust_validated or not receipt.package_validated or receipt.execution_performed:
        raise SpaceExecutionRuntimeError("Publication receipt does not prove a non-executing validated publish gate.", stage="package", code="publication_receipt_trust_invalid")

    graph_anchors = None
    stages = None
    dynamic_controls = None
    migration_audit = None
    if receipt.graph_anchors_sha256:
        raw_graph_anchors = _read_json(graph_anchors_path, label="Graph Anchor sidecar")
        try:
            graph_anchors = parse_graph_anchor_sidecar(raw_graph_anchors)
        except Exception as exc:
            raise SpaceExecutionRuntimeError(f"Invalid Graph Anchor sidecar: {exc}", stage="package", code="graph_anchors_invalid") from exc
        if graph_anchors.space_id != manifest.space_id or graph_anchors.version != manifest.version:
            raise SpaceExecutionRuntimeError("Graph Anchor sidecar identity does not match the published Space.", stage="package", code="graph_anchors_identity_mismatch")

    if receipt.stages_sha256:
        raw_stages = _read_json(stages_path, label="Stage sidecar")
        try:
            stages = parse_stage_sidecar(raw_stages)
        except Exception as exc:
            raise SpaceExecutionRuntimeError(f"Invalid Stage sidecar: {exc}", stage="package", code="stages_invalid") from exc
        stage_check = validate_stages_against_manifest(stages, manifest)
        if not stage_check.get("valid"):
            message = "; ".join(str(item.get("message") or "Stage/manifest mismatch") for item in stage_check.get("errors") or [])
            raise SpaceExecutionRuntimeError(message, stage="package", code="stages_manifest_mismatch")

    if receipt.dynamic_controls_sha256:
        raw_dynamic_controls = _read_json(dynamic_controls_path, label="Dynamic Controls sidecar")
        try:
            dynamic_controls = parse_dynamic_controls(raw_dynamic_controls)
        except Exception as exc:
            raise SpaceExecutionRuntimeError(f"Invalid Dynamic Controls sidecar: {exc}", stage="package", code="dynamic_controls_invalid") from exc
        dynamic_check = validate_dynamic_controls(dynamic_controls, manifest, ui, stages)
        if not dynamic_check.get("valid"):
            message = "; ".join(str(item.get("message") or "Dynamic Controls mismatch") for item in dynamic_check.get("errors") or [])
            raise SpaceExecutionRuntimeError(message, stage="package", code="dynamic_controls_contract_mismatch")

    if receipt.migration_sha256:
        raw_migration = _read_json(migration_path, label="migration audit")
        try:
            migration_audit = model_from_dict(SpaceMigrationAudit, raw_migration)
        except Exception as exc:
            raise SpaceExecutionRuntimeError(f"Invalid migration audit: {exc}", stage="package", code="migration_audit_invalid") from exc
        if migration_audit.space_id != manifest.space_id or migration_audit.to_version != manifest.version:
            raise SpaceExecutionRuntimeError("Migration audit identity does not match the published Space.", stage="package", code="migration_audit_identity_mismatch")

    workflow_path = Path(str(package.get("workflow_path") or ""))
    workflow_payload = _read_json(workflow_path, label="published workflow")
    if not isinstance(workflow_payload, dict):
        raise SpaceExecutionRuntimeError("Published workflow root must be a JSON object.", stage="package", code="workflow_invalid")

    hashes = {
        "manifest": _canonical_json_sha256(manifest_payload(manifest)),
        "workflow": _file_sha256(workflow_path),
        "ui": _canonical_json_sha256(space_ui_payload(ui)),
    }
    expected = {
        "manifest": receipt.manifest_sha256,
        "workflow": receipt.workflow_sha256,
        "ui": receipt.ui_sha256,
    }
    if receipt.graph_anchors_sha256:
        if graph_anchors is None:
            raise SpaceExecutionRuntimeError("Published Space Graph Anchor sidecar is missing.", stage="package", code="graph_anchors_missing")
        hashes["graph_anchors"] = _canonical_json_sha256(graph_anchor_sidecar_payload(graph_anchors))
        expected["graph_anchors"] = receipt.graph_anchors_sha256
    if receipt.stages_sha256:
        if stages is None:
            raise SpaceExecutionRuntimeError("Published Space Stage sidecar is missing.", stage="package", code="stages_missing")
        hashes["stages"] = _canonical_json_sha256(stage_sidecar_payload(stages))
        expected["stages"] = receipt.stages_sha256
    if receipt.dynamic_controls_sha256:
        if dynamic_controls is None:
            raise SpaceExecutionRuntimeError("Published Space Dynamic Controls sidecar is missing.", stage="package", code="dynamic_controls_missing")
        hashes["dynamic_controls"] = _canonical_json_sha256(dynamic_controls_payload(dynamic_controls))
        expected["dynamic_controls"] = receipt.dynamic_controls_sha256
    if receipt.migration_sha256:
        if migration_audit is None:
            raise SpaceExecutionRuntimeError("Published Space migration audit is missing.", stage="package", code="migration_audit_missing")
        hashes["migration"] = _canonical_json_sha256(model_to_dict(migration_audit))
        expected["migration"] = receipt.migration_sha256
    mismatched = [name for name in hashes if hashes[name] != expected[name]]
    if mismatched:
        raise SpaceExecutionRuntimeError(
            f"Published Space integrity check failed for: {', '.join(mismatched)}.",
            stage="package",
            code="publication_integrity_mismatch",
        )

    return {
        "manifest": manifest,
        "ui": ui,
        "graph_anchors": graph_anchors,
        "stages": stages,
        "dynamic_controls": dynamic_controls,
        "migration": migration_audit,
        "receipt": receipt,
        "workflow": workflow_payload,
        "package": package,
        "hashes": hashes,
    }


def _dependency_errors(
    manifest: ComfySpaceManifest,
    analysis: Mapping[str, Any],
) -> tuple[list[SpaceExecutionIssue], list[SpaceExecutionIssue], list[dict[str, Any]]]:
    errors: list[SpaceExecutionIssue] = []
    warnings: list[SpaceExecutionIssue] = []
    dependencies = _mapping(analysis.get("dependencies"))
    missing_classes = {str(item) for item in dependencies.get("missing_node_classes") or []}
    object_info_available = bool(dependencies.get("object_info_available"))

    for requirement in manifest.dependencies.custom_nodes:
        if requirement.required and requirement.class_type in missing_classes:
            hint = f" ({requirement.package})" if requirement.package else ""
            errors.append(_issue(
                "missing_custom_node",
                f"Required Comfy node '{requirement.class_type}'{hint} is not loaded in the selected runtime.",
                "dependencies",
            ))

    model_rows = [dict(item) for item in dependencies.get("model_requirements") or [] if isinstance(item, Mapping)]
    model_by_locator = {
        (str(item.get("node_id") or ""), str(item.get("field") or "")): item
        for item in model_rows
    }
    reconciliations: list[dict[str, Any]] = []
    for requirement in manifest.dependencies.models:
        if not requirement.required or not requirement.configured_name:
            continue
        locator = (str(requirement.source_node_id or ""), str(requirement.source_field or ""))
        row = model_by_locator.get(locator)
        if row is None:
            errors.append(_issue(
                "model_requirement_unresolved",
                f"Required model '{requirement.configured_name}' could not be matched to its published loader binding.",
                "dependencies",
                node_id=requirement.source_node_id,
            ))
            continue
        actual_configured = str(row.get("configured_name") or requirement.configured_name or "")
        availability = row.get("availability")
        if availability is False:
            errors.append(_issue(
                "missing_model",
                f"Required {requirement.model_type} model is not available in the selected Comfy runtime: {actual_configured}",
                "dependencies",
                node_id=requirement.source_node_id,
            ))
            continue
        if availability is None:
            errors.append(_issue(
                "model_catalog_unchecked",
                f"Selected runtime did not expose a usable live catalog for required model: {actual_configured}",
                "dependencies",
                node_id=requirement.source_node_id,
            ))
            continue
        runtime_name = str(row.get("runtime_resolved_name") or actual_configured)
        if runtime_name != actual_configured:
            reconciliations.append({
                "requirement_id": requirement.requirement_id,
                "node_id": str(requirement.source_node_id or ""),
                "field": str(requirement.source_field or ""),
                "configured_name": actual_configured,
                "runtime_resolved_name": runtime_name,
                "resolution_method": str(row.get("resolution_method") or "runtime_catalog"),
            })

    if not object_info_available:
        errors.append(_issue("object_info_unavailable", "Selected Comfy runtime did not provide /object_info.", "dependencies"))
    if str(dependencies.get("preflight_state") or "") != "ready":
        if not errors:
            errors.append(_issue(
                "dependency_preflight_not_ready",
                "Selected Comfy runtime dependency preflight is not ready.",
                "dependencies",
            ))
    for raw in analysis.get("warnings") or []:
        item = _mapping(raw)
        warnings.append(_issue(str(item.get("code") or "analysis_warning"), str(item.get("message") or "Workflow analysis warning."), "dependencies"))
    return errors, warnings, reconciliations


def _apply_runtime_model_reconciliations(workflow: dict[str, Any], reconciliations: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    cloned = deepcopy(workflow)
    for item in reconciliations:
        node_id = str(item.get("node_id") or "")
        field = str(item.get("field") or "")
        value = str(item.get("runtime_resolved_name") or "")
        node = cloned.get(node_id)
        inputs = node.get("inputs") if isinstance(node, Mapping) and isinstance(node.get("inputs"), Mapping) else None
        if inputs is None or field not in inputs or not value:
            raise SpaceExecutionRuntimeError(
                f"Runtime model reconciliation target is no longer valid: node {node_id}.{field}",
                stage="dependencies",
                code="model_reconciliation_target_invalid",
            )
        inputs[field] = value
    return cloned


def _variant_effective_request(
    *,
    variant_id: str,
    space_id: str,
    version: str,
    values: Mapping[str, Any] | None,
    base_default_values: Mapping[str, Any] | None = None,
    graph_addons: Sequence[Mapping[str, Any]] | None,
    stage_run_mode: str | None,
    stage_id: str | None,
    enabled_stage_ids: Sequence[str] | None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    clean_variant = str(variant_id or "").strip().lower()
    if not clean_variant:
        return {
            "variant_id": "",
            "variant_modified": False,
            "values": dict(values or {}),
            "graph_addons": list(graph_addons or []),
            "stage_run_mode": str(stage_run_mode or "all"),
            "stage_id": str(stage_id or ""),
            "enabled_stage_ids": list(enabled_stage_ids or []),
        }

    variant = resolve_space_variant(clean_variant, space_id=space_id, version=version, root_dir=root_dir)
    saved_values = dict(variant.get("saved_values") or {})
    expected_values = dict(base_default_values or {})
    expected_values.update(saved_values)
    requested_values = None if values is None else dict(values)
    effective_values = dict(expected_values)
    if requested_values is not None:
        effective_values.update(requested_values)

    base_addons = [dict(item) for item in variant.get("graph_addons") or [] if isinstance(item, Mapping)]
    effective_addons = base_addons if graph_addons is None else [dict(item) for item in graph_addons if isinstance(item, Mapping)]
    defaults = variant.get("stage_defaults") if isinstance(variant.get("stage_defaults"), Mapping) else {}
    base_mode = str(defaults.get("preferred_run_mode") or "all")
    base_stage_id = str(defaults.get("preferred_stage_id") or "")
    base_enabled = [str(item) for item in defaults.get("enabled_stage_ids") or []]
    effective_mode = str(stage_run_mode if stage_run_mode not in {None, ""} else base_mode)
    effective_stage_id = str(stage_id if stage_id not in {None, ""} else base_stage_id)
    effective_enabled = base_enabled if enabled_stage_ids is None else [str(item) for item in enabled_stage_ids]

    modified = False
    if requested_values is not None and effective_values != expected_values:
        modified = True
    if graph_addons is not None and effective_addons != base_addons:
        modified = True
    if stage_run_mode not in {None, ""} and effective_mode != base_mode:
        modified = True
    if stage_id not in {None, ""} and effective_stage_id != base_stage_id:
        modified = True
    if enabled_stage_ids is not None and effective_enabled != base_enabled:
        modified = True

    return {
        "variant_id": clean_variant,
        "variant_modified": modified,
        "variant": variant,
        "values": effective_values,
        "graph_addons": effective_addons,
        "stage_run_mode": effective_mode,
        "stage_id": effective_stage_id,
        "enabled_stage_ids": effective_enabled,
    }


def _prepare_execution(
    space_id: str,
    version: str,
    *,
    runtime_profile_id: str,
    values: Mapping[str, Any] | None,
    graph_addons: Sequence[Mapping[str, Any]] | None = None,
    variant_id: str = "",
    stage_run_mode: str | None = None,
    stage_id: str | None = None,
    enabled_stage_ids: Sequence[str] | None = None,
    continue_from_run_id: str = "",
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    errors: list[SpaceExecutionIssue] = []
    warnings: list[SpaceExecutionIssue] = []
    provider_id = ""
    package_valid = False
    runtime_reachable = False
    object_info_available = False
    dependency_ready = False
    asset_ready = False
    binding_ready = False
    graph_addons_ready = not bool(graph_addons)
    graph_addon_composition: dict[str, Any] = {
        "schema_version": "neo.comfy_space.graph_addon_composition.v1",
        "ok": True,
        "applied": [],
        "added_nodes": [],
        "asset_resolutions": [],
        "base_space_mutated": False,
        "published_workflow_mutated": False,
    }
    reconciliations: list[dict[str, Any]] = []
    variant_context: dict[str, Any] = {"variant_id": "", "variant_modified": False}

    try:
        package = _load_published_space_package(space_id, version, root_dir=root_dir)
        package_valid = True
    except SpaceExecutionRuntimeError as exc:
        errors.append(_issue(exc.code, str(exc), exc.stage))
        report = SpaceExecutionPreflightReport(
            ok=False,
            ready_for_queue=False,
            space_id=str(space_id or "").strip().lower(),
            version=str(version or "").strip(),
            runtime_profile_id=str(runtime_profile_id or "").strip(),
            provider_id="",
            package_valid=False,
            graph_addons_ready=not bool(graph_addons),
            graph_addon_count=len(graph_addons or []),
            errors=errors,
        )
        return {"report": model_to_dict(report), "prepared": None}

    manifest: ComfySpaceManifest = package["manifest"]
    workflow: dict[str, Any] = package["workflow"]
    try:
        variant_context = _variant_effective_request(
            variant_id=variant_id, space_id=manifest.space_id, version=manifest.version,
            values=values, base_default_values={item.input_id: item.default for item in manifest.inputs},
            graph_addons=graph_addons, stage_run_mode=stage_run_mode,
            stage_id=stage_id, enabled_stage_ids=enabled_stage_ids, root_dir=root_dir,
        )
        values = dict(variant_context.get("values") or {})
        graph_addons = list(variant_context.get("graph_addons") or [])
        stage_run_mode = str(variant_context.get("stage_run_mode") or "all")
        stage_id = str(variant_context.get("stage_id") or "")
        enabled_stage_ids = list(variant_context.get("enabled_stage_ids") or [])
        graph_addons_ready = not bool(graph_addons)
    except SpaceVariantError as exc:
        errors.append(_issue(exc.code, str(exc), "variants"))

    stage_plan: dict[str, Any] = {"run_mode": "all", "selected_stage_ids": [], "target_output_ids": [], "intermediate_output_ids": []}
    stages = package.get("stages")
    if stages is not None:
        try:
            stage_plan = resolve_stage_execution(
                stages, run_mode=stage_run_mode, stage_id=stage_id, enabled_stage_ids=enabled_stage_ids,
            )
        except SpaceStageError as exc:
            errors.append(_issue("stage_selection_invalid", str(exc), "stages"))
    elif str(stage_run_mode or "all").lower() != "all" or stage_id:
        errors.append(_issue("stages_unavailable", "This published Space does not define execution stages.", "stages"))
    try:
        object_info, runtime_probe = probe_comfy_profile_object_info(runtime_profile_id)
        runtime_reachable = bool(runtime_probe.get("reachable"))
        object_info_available = bool(runtime_probe.get("object_info_available"))
        provider_id = str(runtime_probe.get("provider_id") or "")
    except SpaceRuntimeProbeError as exc:
        errors.append(_issue("runtime_probe_failed", str(exc), "runtime"))
        object_info = {}
        runtime_probe = {}

    analysis: dict[str, Any] = {}
    if object_info_available:
        try:
            analysis = analyze_comfy_workflow(
                workflow,
                object_info=object_info,
                runtime_profile_id=runtime_profile_id,
                runtime_probe=runtime_probe,
            )
        except (SpaceWorkflowAnalysisError, ValueError) as exc:
            errors.append(_issue("workflow_analysis_failed", str(exc), "dependencies"))
        if analysis:
            dep_errors, dep_warnings, reconciliations = _dependency_errors(manifest, analysis)
            errors.extend(dep_errors)
            warnings.extend(dep_warnings)
            dependency_ready = not dep_errors

    workflow_for_binding = workflow
    if not errors:
        try:
            addon_requests = parse_graph_addon_requests(graph_addons or [])
            graph_addon_composition = compose_graph_addons(
                workflow,
                graph_anchors=package.get("graph_anchors"),
                object_info=object_info,
                runtime_profile_id=runtime_profile_id,
                requests=addon_requests,
                root_dir=root_dir,
            )
            workflow_for_binding = _mapping(graph_addon_composition.get("workflow"))
            graph_addons_ready = True
        except SpaceGraphAddonError as exc:
            graph_addons_ready = False
            errors.append(_issue(exc.code, str(exc), "graph_addons"))

    prepared_binding: dict[str, Any] | None = None
    if not errors:
        try:
            prepared_binding = prepare_space_asset_bindings(
                workflow_for_binding,
                manifest,
                values or {},
                runtime_profile_id=runtime_profile_id,
                object_info=object_info,
                root_dir=root_dir,
            )
        except (SpaceAssetBusError, ValueError) as exc:
            errors.append(_issue("asset_binding_failed", str(exc), "assets"))
        if prepared_binding is not None:
            asset_report = _mapping(prepared_binding.get("asset_resolution"))
            asset_ready = bool(asset_report.get("ok") and asset_report.get("ready_for_binding"))
            binding_report = _mapping(prepared_binding.get("binding"))
            binding_ready = bool(prepared_binding.get("ok") and prepared_binding.get("ready_for_execution"))
            for raw in asset_report.get("errors") or []:
                item = _mapping(raw)
                errors.append(_issue(str(item.get("code") or "asset_resolution_failed"), str(item.get("message") or "Asset resolution failed."), "assets", input_id=item.get("input_id")))
            for raw in binding_report.get("errors") or []:
                item = _mapping(raw)
                errors.append(_issue(str(item.get("code") or "binding_failed"), str(item.get("message") or "Space input binding failed."), "binding", node_id=item.get("node_id"), input_id=item.get("input_id")))
            for raw in binding_report.get("warnings") or []:
                item = _mapping(raw)
                warnings.append(_issue(str(item.get("code") or "binding_warning"), str(item.get("message") or "Space input binding warning."), "binding", node_id=item.get("node_id"), input_id=item.get("input_id")))

    bound_workflow = _mapping((prepared_binding or {}).get("workflow")) if prepared_binding else {}
    runtime_workflow: dict[str, Any] = {}
    final_analysis: dict[str, Any] = analysis
    if binding_ready and not errors:
        try:
            # Re-run live dependency validation against the actual clone after user
            # values/assets have been injected. This catches a runtime model choice
            # supplied through an exposed model selector rather than trusting publish-time evidence.
            post_bind_analysis = analyze_comfy_workflow(
                bound_workflow,
                object_info=object_info,
                runtime_profile_id=runtime_profile_id,
                runtime_probe=runtime_probe,
            )
            post_errors, post_warnings, post_reconciliations = _dependency_errors(manifest, post_bind_analysis)
            errors.extend(post_errors)
            warnings.extend(post_warnings)
            # Prefer post-bind reconciliation because it represents the actual values
            # that will be submitted. Deduplicate by node+field.
            by_locator: dict[tuple[str, str], dict[str, Any]] = {}
            for row in [*reconciliations, *post_reconciliations]:
                by_locator[(str(row.get("node_id") or ""), str(row.get("field") or ""))] = dict(row)
            reconciliations = list(by_locator.values())
            dependency_ready = not post_errors
            final_analysis = post_bind_analysis
            runtime_workflow = _apply_runtime_model_reconciliations(bound_workflow, reconciliations)
            if stages is not None and stage_plan.get("selected_stage_ids"):
                target_ids = set(stage_plan.get("target_output_ids") or [])
                target_node_ids = [item.binding.node_id for item in manifest.outputs if item.output_id in target_ids]
                runtime_workflow = prune_workflow_to_output_nodes(runtime_workflow, target_node_ids)
                stage_plan["target_node_ids"] = target_node_ids
                stage_plan["node_count"] = len(runtime_workflow)
        except (SpaceWorkflowAnalysisError, SpaceExecutionRuntimeError, ValueError) as exc:
            code = exc.code if isinstance(exc, SpaceExecutionRuntimeError) else "post_bind_preflight_failed"
            errors.append(_issue(code, str(exc), "dependencies"))

    ready = bool(package_valid and runtime_reachable and object_info_available and dependency_ready and asset_ready and binding_ready and graph_addons_ready and runtime_workflow and not errors)
    report = SpaceExecutionPreflightReport(
        ok=ready,
        ready_for_queue=ready,
        space_id=manifest.space_id,
        version=manifest.version,
        runtime_profile_id=str(runtime_profile_id or "").strip(),
        provider_id=provider_id,
        package_valid=package_valid,
        runtime_reachable=runtime_reachable,
        object_info_available=object_info_available,
        dependency_preflight_ready=dependency_ready,
        asset_resolution_ready=asset_ready,
        binding_ready=binding_ready,
        graph_addons_ready=graph_addons_ready,
        graph_addon_count=len(graph_addon_composition.get("applied") or []),
        graph_addons=[dict(item) for item in graph_addon_composition.get("applied") or []],
        graph_addon_added_node_count=len(graph_addon_composition.get("added_nodes") or []),
        variant_id=str(variant_context.get("variant_id") or ""),
        variant_modified=bool(variant_context.get("variant_modified")),
        stage_plan=stage_plan,
        model_reconciliation_count=len(reconciliations),
        runtime_model_reconciliations=reconciliations,
        errors=errors,
        warnings=warnings,
    )
    return {
        "report": model_to_dict(report),
        "prepared": {
            "manifest": manifest,
            "ui": package["ui"],
            "receipt": package["receipt"],
            "runtime_workflow": runtime_workflow,
            "object_info": object_info,
            "analysis": final_analysis,
            "asset_resolution": _mapping((prepared_binding or {}).get("asset_resolution")),
            "binding": _mapping((prepared_binding or {}).get("binding")),
            "runtime_model_reconciliations": reconciliations,
            "graph_addons": [dict(item) for item in graph_addon_composition.get("applied") or []],
            "graph_addon_added_nodes": [dict(item) for item in graph_addon_composition.get("added_nodes") or []],
            "graph_addon_asset_resolutions": [dict(item) for item in graph_addon_composition.get("asset_resolutions") or []],
            "variant_id": str(variant_context.get("variant_id") or ""),
            "variant_modified": bool(variant_context.get("variant_modified")),
            "effective_values": dict(values or {}),
            "stage_plan": stage_plan,
            "continue_from_run_id": str(continue_from_run_id or ""),
        } if ready else None,
    }


def preflight_published_space(
    space_id: str,
    version: str,
    *,
    runtime_profile_id: str,
    values: Mapping[str, Any] | None = None,
    graph_addons: Sequence[Mapping[str, Any]] | None = None,
    variant_id: str = "",
    stage_run_mode: str | None = None,
    stage_id: str | None = None,
    enabled_stage_ids: Sequence[str] | None = None,
    continue_from_run_id: str = "",
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Perform the full selected-runtime NCS-7 preflight without POST /prompt.

    Media inputs may be content-addressed/staged into the selected Comfy runtime by
    NCS-6 so the exact execution clone can be validated. No workflow is queued.
    """

    prepared = _prepare_execution(
        space_id,
        version,
        runtime_profile_id=runtime_profile_id,
        values=values,
        graph_addons=graph_addons,
        variant_id=variant_id,
        stage_run_mode=stage_run_mode,
        stage_id=stage_id,
        enabled_stage_ids=enabled_stage_ids,
        continue_from_run_id=continue_from_run_id,
        root_dir=root_dir,
    )
    return prepared["report"]


def _new_run_id() -> str:
    return f"csrun_{uuid4().hex}"


def _result_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    runtime = _mapping(record.get("runtime"))
    status_raw = str(record.get("status") or "queued").lower()
    if status_raw in {"canceled", "cancelled"}:
        status = "cancelled"
    elif status_raw in {"completed", "failed", "running", "queued"}:
        status = status_raw
    elif bool(_mapping(record.get("control")).get("cancel_requested")):
        status = "cancel_requested"
    else:
        status = "running"
    issues = []
    if status == "failed":
        issues.append(_issue("run_failed", str(record.get("error") or record.get("message") or "Comfy Space run failed."), "running"))
    outputs_payload = record.get("outputs") if isinstance(record.get("outputs"), list) else []
    outputs: list[SpaceExecutionOutputRecord] = []
    for item in outputs_payload:
        try:
            outputs.append(model_from_dict(SpaceExecutionOutputRecord, item))
        except Exception:
            continue
    progress_payload = progress_snapshot_from_record(record)
    try:
        progress = model_from_dict(SpaceRunProgressSnapshot, progress_payload)
    except Exception:
        progress = SpaceRunProgressSnapshot()
    raw_progress_events = runtime.get("progress_events") if isinstance(runtime.get("progress_events"), list) else []
    progress_events: list[SpaceRunProgressEvent] = []
    for item in raw_progress_events[-40:]:
        try:
            progress_events.append(model_from_dict(SpaceRunProgressEvent, item))
        except Exception:
            continue
    result = SpaceExecutionRunResult(
        ok=status not in {"failed"},
        run_id=str(record.get("job_id") or ""),
        status=status,
        space_id=str(runtime.get("space_id") or ""),
        version=str(runtime.get("space_version") or ""),
        runtime_profile_id=str(record.get("profile_id") or ""),
        provider_id=str(record.get("provider_id") or ""),
        prompt_id=str(record.get("provider_job_id") or runtime.get("prompt_id") or ""),
        client_id=str(record.get("client_id") or ""),
        message=str(record.get("message") or ""),
        outputs=outputs,
        results=_mapping(runtime.get("results")),
        errors=issues,
        warnings=[],
        graph_addons=[dict(item) for item in runtime.get("graph_addons") or [] if isinstance(item, Mapping)],
        graph_addon_added_nodes=[dict(item) for item in runtime.get("graph_addon_added_nodes") or [] if isinstance(item, Mapping)],
        variant_id=str(runtime.get("variant_id") or ""),
        variant_modified=bool(runtime.get("variant_modified")),
        stage_plan=_mapping(runtime.get("stage_plan")),
        continue_from_run_id=str(runtime.get("continue_from_run_id") or ""),
        backend_completed=bool(runtime.get("backend_completed") or status == "completed"),
        output_collection_complete=bool(runtime.get("output_collection_complete") or status == "completed"),
        progress=progress,
        recent_progress_events=progress_events,
    )
    return model_to_dict(result)


def start_space_run(
    space_id: str,
    version: str,
    *,
    runtime_profile_id: str,
    values: Mapping[str, Any] | None = None,
    graph_addons: Sequence[Mapping[str, Any]] | None = None,
    variant_id: str = "",
    stage_run_mode: str | None = None,
    stage_id: str | None = None,
    enabled_stage_ids: Sequence[str] | None = None,
    continue_from_run_id: str = "",
    wait_timeout_seconds: float | None = None,
    root_dir: Path | str | None = None,
    monitor_live_progress: bool | None = None,
) -> dict[str, Any]:
    """Queue one published Space on the selected Comfy runtime.

    This is the single NCS-7 execution authority. It never mutates the published
    workflow and never falls back to a different runtime profile.
    """

    prepared = _prepare_execution(
        space_id,
        version,
        runtime_profile_id=runtime_profile_id,
        values=values,
        graph_addons=graph_addons,
        variant_id=variant_id,
        stage_run_mode=stage_run_mode,
        stage_id=stage_id,
        enabled_stage_ids=enabled_stage_ids,
        continue_from_run_id=continue_from_run_id,
        root_dir=root_dir,
    )
    preflight = prepared["report"]
    if not preflight.get("ready_for_queue") or not prepared.get("prepared"):
        first = (preflight.get("errors") or [{}])[0]
        raise SpaceExecutionRuntimeError(
            str(first.get("message") or "Comfy Space execution preflight failed."),
            stage=str(first.get("stage") or "runtime"),
            code=str(first.get("code") or "preflight_failed"),
        )

    bundle = prepared["prepared"]
    manifest: ComfySpaceManifest = bundle["manifest"]
    runtime_workflow: dict[str, Any] = bundle["runtime_workflow"]
    stage_plan = _mapping(bundle.get("stage_plan"))
    if str(stage_plan.get("run_mode") or "") == "continue":
        prior_id = str(continue_from_run_id or "").strip().lower()
        if not prior_id:
            raise SpaceExecutionRuntimeError("Continue requires a completed predecessor run.", stage="stages", code="continue_run_required")
        prior = _job_registry(root_dir).get(prior_id, surface=_SURFACE)
        if not prior or str(prior.get("status") or "").lower() != "completed":
            raise SpaceExecutionRuntimeError("Continue predecessor run is missing or not completed.", stage="stages", code="continue_run_not_completed")
        prior_runtime = _mapping(prior.get("runtime"))
        if str(prior_runtime.get("space_id") or "") != manifest.space_id or str(prior_runtime.get("space_version") or "") != manifest.version:
            raise SpaceExecutionRuntimeError("Continue predecessor belongs to a different Space/version.", stage="stages", code="continue_space_mismatch")
        if str(prior.get("profile_id") or "") != str(runtime_profile_id or ""):
            raise SpaceExecutionRuntimeError("Continue must use the same Comfy runtime as its predecessor run.", stage="stages", code="continue_runtime_mismatch")
        requested_stage = str(stage_plan.get("requested_stage_id") or "")
        package_stages = _load_published_space_package(manifest.space_id, manifest.version, root_dir=root_dir).get("stages")
        if package_stages is not None:
            stage_by_id = {item.stage_id: item for item in package_stages.stages}
            required = set(stage_by_id.get(requested_stage).required_stage_ids if stage_by_id.get(requested_stage) else [])
            prior_selected = set(_mapping(prior_runtime.get("stage_plan")).get("selected_stage_ids") or [])
            if required and not required.issubset(prior_selected):
                raise SpaceExecutionRuntimeError("Continue predecessor did not complete the required previous stage(s).", stage="stages", code="continue_dependency_mismatch")
    run_id = _new_run_id()
    client_id = f"neo-comfy-space-{uuid4().hex}"
    registry = _job_registry(root_dir)
    graph_plan = build_space_graph_plan(runtime_workflow)
    initial_progress = {
        "schema_version": "neo.comfy_space.run_progress.v1",
        "sequence": 0,
        "phase": "preparing",
        "percent": 3.0,
        "percent_mode": "phase",
        "message": "Execution preflight passed; waiting for shared Comfy GPU lease.",
        "event_type": "preparing",
        "source": "neo_runtime",
        "updated_at": "",
        "current_node_id": "",
        "current_display_node_id": "",
        "current_node_class": "",
        "current_node_title": "",
        "current_node_index": 0,
        "node_count": int(graph_plan.get("node_count") or 0),
        "node_step": 0,
        "node_step_max": 0,
        "completed_node_ids": [],
        "cached_node_ids": [],
        "queue_remaining": None,
        "websocket_connected": False,
        "monitor_state": "idle",
        "recoverable": True,
    }

    # Persist the Neo run identity before queueing so a later provider failure still
    # leaves an auditable terminal record rather than an invisible attempt.
    registry.upsert(
        run_id,
        surface=_SURFACE,
        updates={
            "job_id": run_id,
            "surface": _SURFACE,
            "status": "preparing",
            "message": "Execution preflight passed; waiting for shared Comfy GPU lease.",
            "provider_id": str(preflight.get("provider_id") or ""),
            "profile_id": runtime_profile_id,
            "backend_profile_id": runtime_profile_id,
            "client_id": client_id,
            "submitted_job": {
                "space_id": manifest.space_id,
                "version": manifest.version,
                "input_ids": sorted(str(key) for key in (bundle.get("effective_values") or {}).keys()),
                "graph_addon_instance_ids": [str(item.get("instance_id") or "") for item in bundle.get("graph_addons") or []],
                "stage_run_mode": str(stage_plan.get("run_mode") or "all"),
                "stage_ids": list(stage_plan.get("selected_stage_ids") or []),
                "continue_from_run_id": str(continue_from_run_id or ""),
                "variant_id": str(bundle.get("variant_id") or ""),
                "variant_modified": bool(bundle.get("variant_modified")),
            },
            "runtime": {
                "schema_version": SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION,
                "space_id": manifest.space_id,
                "space_version": manifest.version,
                "runtime_model_reconciliations": bundle.get("runtime_model_reconciliations") or [],
                "graph_addons": bundle.get("graph_addons") or [],
                "graph_addon_added_nodes": bundle.get("graph_addon_added_nodes") or [],
                "stage_plan": stage_plan,
                "variant_id": str(bundle.get("variant_id") or ""),
                "variant_modified": bool(bundle.get("variant_modified")),
                "continue_from_run_id": str(continue_from_run_id or ""),
                "graph_plan": graph_plan,
                "progress": initial_progress,
                "progress_events": [],
                "backend_completed": False,
                "output_collection_complete": False,
            },
            "output_expectations": {
                "declared_outputs": [model_to_dict(item) for item in manifest.outputs],
            },
        },
    )

    try:
        resolved_profile = resolve_comfy_execution_profile(runtime_profile_id)
    except ComfyExecutionTransportError as exc:
        registry.mark_failed(run_id, surface=_SURFACE, message=str(exc), error=str(exc))
        raise SpaceExecutionRuntimeError(str(exc), stage="runtime", code="runtime_profile_invalid") from exc

    gpu = get_comfy_gpu_lifecycle_manager()
    lease: dict[str, Any] | None = None
    prompt_id = ""
    try:
        lease = gpu.acquire(
            base_url=str(resolved_profile["base_url"]),
            owner_kind="comfy_space",
            owner_label=f"Comfy Space {manifest.name}",
            profile_id=runtime_profile_id,
            wait_timeout_seconds=wait_timeout_seconds,
            metadata={"run_id": run_id, "space_id": manifest.space_id, "space_version": manifest.version},
        )
        prompt = submit_comfy_prompt(
            runtime_profile_id,
            workflow=runtime_workflow,
            client_id=client_id,
            extra_data={
                "neo": {
                    "surface": _SURFACE,
                    "run_id": run_id,
                    "space_id": manifest.space_id,
                    "space_version": manifest.version,
                    "graph_addon_count": len(bundle.get("graph_addons") or []),
                    "stage_run_mode": str(stage_plan.get("run_mode") or "all"),
                    "stage_ids": list(stage_plan.get("selected_stage_ids") or []),
                    "variant_id": str(bundle.get("variant_id") or ""),
                }
            },
        )
        prompt_id = str(prompt["prompt_id"])
        bind_state = gpu.bind_prompt(str(lease["token"]), prompt_id=prompt_id, cleanup_after=False, watch=True)
        if not bool((bind_state or {}).get("ok")):
            gpu.guard_unbound_after_queue_uncertainty(
                str(lease["token"]),
                cleanup_after=False,
                reason="space_prompt_queued_but_gpu_prompt_binding_failed",
            )
            raise SpaceExecutionRuntimeError(
                "Comfy accepted the prompt, but Neo could not bind the shared GPU lease to its prompt id. The lease is being held until backend queue recovery confirms it is safe to release.",
                stage="queue",
                code="gpu_prompt_binding_failed",
            )
        registry.register_queued(
            job_id=run_id,
            surface=_SURFACE,
            provider_id=str(prompt.get("provider_id") or preflight.get("provider_id") or "comfyui"),
            profile_id=runtime_profile_id,
            backend_profile_id=runtime_profile_id,
            provider_job_id=prompt_id,
            local_job_id=run_id,
            backend="comfyui",
            mode="comfy_space",
            family="comfy_space",
            loader="published_space",
            model="",
            client_id=client_id,
            submitted_job={
                "space_id": manifest.space_id,
                "version": manifest.version,
                "input_ids": sorted(str(key) for key in (bundle.get("effective_values") or {}).keys()),
                "graph_addon_instance_ids": [str(item.get("instance_id") or "") for item in bundle.get("graph_addons") or []],
                "stage_run_mode": str(stage_plan.get("run_mode") or "all"),
                "stage_ids": list(stage_plan.get("selected_stage_ids") or []),
                "continue_from_run_id": str(continue_from_run_id or ""),
                "variant_id": str(bundle.get("variant_id") or ""),
                "variant_modified": bool(bundle.get("variant_modified")),
            },
            runtime={
                "schema_version": SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION,
                "space_id": manifest.space_id,
                "space_version": manifest.version,
                "prompt_id": prompt_id,
                "runtime_model_reconciliations": bundle.get("runtime_model_reconciliations") or [],
                "graph_addons": bundle.get("graph_addons") or [],
                "graph_addon_added_nodes": bundle.get("graph_addon_added_nodes") or [],
                "stage_plan": stage_plan,
                "variant_id": str(bundle.get("variant_id") or ""),
                "variant_modified": bool(bundle.get("variant_modified")),
                "continue_from_run_id": str(continue_from_run_id or ""),
                "graph_plan": graph_plan,
                "progress": {**initial_progress, "phase": "queued", "percent": 8.0, "message": "Queued on selected ComfyUI runtime.", "event_type": "queued"},
                "progress_events": [],
                "backend_completed": False,
                "output_collection_complete": False,
                "shared_gpu_resource_group": str(lease.get("resource_group") or ""),
            },
            output_expectations={"declared_outputs": [model_to_dict(item) for item in manifest.outputs]},
            message="Queued on selected ComfyUI runtime.",
        )
    except ComfyGpuBusyError as exc:
        registry.mark_failed(run_id, surface=_SURFACE, message=str(exc), error=str(exc))
        raise SpaceExecutionRuntimeError(str(exc), stage="resource_lease", code="shared_gpu_busy") from exc
    except ComfyExecutionTransportError as exc:
        if lease:
            token = str(lease.get("token") or "")
            if exc.queue_state_uncertain and token:
                gpu.guard_unbound_after_queue_uncertainty(token, cleanup_after=False, reason="space_prompt_queue_response_uncertain")
            elif token:
                gpu.release(token, state="queue_failed")
        registry.mark_failed(run_id, surface=_SURFACE, message=str(exc), error=str(exc), runtime={"queue_state_uncertain": bool(exc.queue_state_uncertain)})
        raise SpaceExecutionRuntimeError(str(exc), stage="queue", code="prompt_submission_failed") from exc
    except Exception as exc:  # noqa: BLE001 - fail safe around post-queue bookkeeping.
        token = str((lease or {}).get("token") or "")
        if token:
            if prompt_id:
                # Once Comfy returned a prompt id, never free the shared lease merely
                # because Neo-side bookkeeping failed. Hold/reconcile until the backend
                # queue clears; releasing here could allow a second GPU job to overlap.
                gpu.guard_unbound_after_queue_uncertainty(
                    token,
                    cleanup_after=False,
                    reason="space_prompt_queued_post_submit_tracking_failure",
                )
            else:
                gpu.release(token, state="queue_failed")
        registry.mark_failed(
            run_id,
            surface=_SURFACE,
            message=str(exc),
            error=str(exc),
            runtime={"backend_may_be_running": bool(prompt_id), "prompt_id": prompt_id},
        )
        if isinstance(exc, SpaceExecutionRuntimeError):
            raise
        raise SpaceExecutionRuntimeError(
            str(exc),
            stage="queue" if prompt_id else "resource_lease",
            code="post_queue_tracking_failed" if prompt_id else "execution_start_failed",
        ) from exc

    record_space_run_progress(
        run_id,
        event_type="queue_state",
        data={"state": "pending"},
        root_dir=root_dir,
        source="neo_runtime",
        force_message="Queued on selected ComfyUI runtime.",
    )
    should_monitor = (root_dir is None) if monitor_live_progress is None else bool(monitor_live_progress)
    if should_monitor:
        ensure_space_run_monitor(run_id, root_dir=root_dir)
    record = registry.get(run_id, surface=_SURFACE) or {}
    return _result_from_record(record)


def _history_failure(history_item: Mapping[str, Any]) -> tuple[bool, bool, str]:
    status = _mapping(history_item.get("status"))
    status_str = str(status.get("status_str") or "").strip().lower()
    messages = status.get("messages") if isinstance(status.get("messages"), list) else []
    interrupted = False
    details: list[str] = []
    for raw in messages:
        if isinstance(raw, (list, tuple)) and raw:
            event = str(raw[0] or "").strip().lower()
            payload = raw[1] if len(raw) > 1 else None
            if event in {"execution_interrupted", "execution_cancelled", "execution_canceled"}:
                interrupted = True
            if event == "execution_error":
                if isinstance(payload, Mapping):
                    details.append(str(payload.get("exception_message") or payload.get("exception_type") or payload))
                elif payload:
                    details.append(str(payload))
    failed = status_str in {"error", "failed"} or bool(history_item.get("error")) or bool(history_item.get("node_errors"))
    if history_item.get("error"):
        details.append(str(history_item.get("error")))
    if history_item.get("node_errors"):
        details.append(str(history_item.get("node_errors")))
    return failed, interrupted, "; ".join(item for item in details if item) or ("Comfy execution failed." if failed else "")


def _artifact_coordinates(value: Any) -> list[dict[str, str]]:
    values = _sequence(value) or [value]
    coordinates: list[dict[str, str]] = []
    for item in values:
        payload = _mapping(item)
        filename = str(payload.get("filename") or payload.get("name") or "").strip()
        if not filename:
            raise SpaceExecutionRuntimeError("Comfy media output did not include a filename coordinate.", stage="outputs", code="artifact_coordinate_missing")
        coordinates.append({
            "filename": filename,
            "subfolder": str(payload.get("subfolder") or "").strip(),
            "type": str(payload.get("type") or "output").strip() or "output",
        })
    return coordinates


def _collect_declared_outputs(
    *,
    profile_id: str,
    prompt_id: str,
    manifest: ComfySpaceManifest,
    history_outputs: Mapping[str, Any],
    root_dir: Path | str | None,
    output_ids: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected_ids = {str(item) for item in (output_ids or []) if str(item)}
    manifest_for_outputs = manifest
    if selected_ids:
        payload = manifest_payload(manifest)
        payload["outputs"] = [item for item in payload.get("outputs") or [] if str(item.get("output_id") or "") in selected_ids]
        manifest_for_outputs = parse_space_manifest(payload)
    resolution = resolve_space_outputs(history_outputs, manifest_for_outputs)
    if not resolution.get("ok"):
        unresolved = [str(item.get("output_id") or "") for item in resolution.get("outputs") or [] if not item.get("resolved")]
        raise SpaceExecutionRuntimeError(
            f"Comfy completed but declared Space outputs were not found: {', '.join(unresolved)}",
            stage="outputs",
            code="declared_output_missing",
        )

    definitions = {item.output_id: item for item in manifest_for_outputs.outputs}
    records: list[dict[str, Any]] = []
    results: dict[str, Any] = {}
    for resolved in resolution.get("outputs") or []:
        output_id = str(resolved.get("output_id") or "")
        definition = definitions[output_id]
        raw_value = resolved.get("value")
        public_value: Any = raw_value
        collected_ids: list[str] = []
        if definition.kind in _ASSET_KINDS:
            collected_values: list[dict[str, Any]] = []
            for coordinate in _artifact_coordinates(raw_value):
                collected = collect_comfy_artifact(
                    profile_id,
                    kind=definition.kind,
                    filename=coordinate["filename"],
                    subfolder=coordinate["subfolder"],
                    artifact_type=coordinate["type"],
                    prompt_id=prompt_id,
                    node_id=definition.binding.node_id,
                    label=definition.label,
                    retention="durable",
                    metadata={"space_id": manifest.space_id, "space_version": manifest.version, "output_id": output_id},
                    root_dir=root_dir,
                )
                asset = _mapping(collected.get("asset"))
                asset_id = str(asset.get("asset_id") or "")
                collected_ids.append(asset_id)
                collected_values.append({
                    "ref_type": "neo_asset_ref",
                    "asset_id": asset_id,
                    "kind": definition.kind,
                    "url": str(collected.get("content_url") or asset_content_url(asset_id)),
                    "filename": str(asset.get("original_filename") or coordinate["filename"]),
                    "content_type": str(asset.get("content_type") or "application/octet-stream"),
                    "size_bytes": int(asset.get("size_bytes") or 0),
                })
            public_value = collected_values if definition.collection == "all" else collected_values[0]

        record = SpaceExecutionOutputRecord(
            output_id=output_id,
            kind=definition.kind,
            label=definition.label,
            primary=definition.primary,
            collection=definition.collection,
            source_node_id=definition.binding.node_id,
            value=public_value,
            collected_asset_ids=collected_ids,
        )
        payload = model_to_dict(record)
        records.append(payload)
        results[output_id] = public_value
    return records, results


def poll_space_run(run_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    registry = _job_registry(root_dir)
    record = registry.get(run_id, surface=_SURFACE)
    if not record:
        raise SpaceExecutionRuntimeError(f"Unknown Comfy Space run: {run_id}", stage="running", code="run_not_found")
    if str(record.get("surface") or "") != _SURFACE:
        raise SpaceExecutionRuntimeError("Run does not belong to Comfy Spaces.", stage="running", code="run_surface_mismatch")

    status = str(record.get("status") or "").lower()
    if status in _TERMINAL_STATUSES:
        return _result_from_record(record)

    profile_id = str(record.get("profile_id") or "")
    prompt_id = str(record.get("provider_job_id") or "")
    if not profile_id or not prompt_id:
        registry.mark_failed(run_id, surface=_SURFACE, message="Run record is missing selected runtime/prompt identity.", error="runtime lineage incomplete")
        return _result_from_record(registry.get(run_id, surface=_SURFACE) or record)

    try:
        history = get_comfy_prompt_history(profile_id, prompt_id=prompt_id)
    except ComfyExecutionTransportError as exc:
        raise SpaceExecutionRuntimeError(str(exc), stage="running", code="history_poll_failed") from exc

    control = _mapping(record.get("control"))
    cancel_requested = bool(control.get("cancel_requested"))
    if not history.get("found"):
        try:
            queue = get_comfy_queue_state(profile_id)
        except ComfyExecutionTransportError as exc:
            raise SpaceExecutionRuntimeError(str(exc), stage="running", code="queue_poll_failed") from exc
        if prompt_id in queue.get("running_prompt_ids", []):
            registry.mark_running(run_id, surface=_SURFACE, message="Running in ComfyUI.")
            record_space_run_progress(run_id, event_type="queue_state", data={"state": "running", "queue_remaining": queue.get("queue_remaining")}, root_dir=root_dir, source="history_queue")
        elif prompt_id in queue.get("pending_prompt_ids", []):
            registry.upsert(run_id, surface=_SURFACE, updates={"status": "queued", "message": "Waiting in ComfyUI queue."})
            record_space_run_progress(run_id, event_type="queue_state", data={"state": "pending", "queue_remaining": queue.get("queue_remaining")}, root_dir=root_dir, source="history_queue")
        elif cancel_requested:
            registry.mark_cancelled(run_id, surface=_SURFACE, message="Comfy Space run cancelled.", runtime={"backend_completed": False, "output_collection_complete": False})
            record_space_run_progress(run_id, event_type="cancelled", root_dir=root_dir, source="history_queue", force_message="Comfy Space run cancelled.")
            try:
                resolved = resolve_comfy_execution_profile(profile_id)
                get_comfy_gpu_lifecycle_manager().complete_prompt(base_url=str(resolved["base_url"]), prompt_id=prompt_id, state="cancelled", cleanup_after=False)
            except Exception:  # noqa: BLE001 - registry terminal state is still authoritative for the API.
                pass
        else:
            registry.mark_running(run_id, surface=_SURFACE, message="Awaiting ComfyUI history confirmation.")
            record_space_run_progress(run_id, event_type="queue_state", data={"state": "awaiting_history"}, root_dir=root_dir, source="history_queue")
        return _result_from_record(registry.get(run_id, surface=_SURFACE) or record)

    history_item = _mapping(history.get("history_item"))
    failed, interrupted, failure_message = _history_failure(history_item)
    if interrupted or (failed and cancel_requested):
        registry.mark_cancelled(run_id, surface=_SURFACE, message="Comfy Space run cancelled.", runtime={"backend_completed": True, "output_collection_complete": False})
        record_space_run_progress(run_id, event_type="cancelled", root_dir=root_dir, source="history", force_message="Comfy Space run cancelled.")
        terminal_state = "cancelled"
    elif failed:
        registry.mark_failed(run_id, surface=_SURFACE, message=failure_message or "Comfy Space run failed.", error=failure_message or "Comfy execution failed.", runtime={"backend_completed": True, "output_collection_complete": False})
        record_space_run_progress(run_id, event_type="failed", root_dir=root_dir, source="history", force_message=failure_message or "Comfy Space run failed.")
        terminal_state = "failed"
    else:
        runtime = _mapping(record.get("runtime"))
        space_id = str(runtime.get("space_id") or "")
        version = str(runtime.get("space_version") or "")
        try:
            package = _load_published_space_package(space_id, version, root_dir=root_dir)
            manifest: ComfySpaceManifest = package["manifest"]
            history_outputs = _mapping(history_item.get("outputs"))
            stage_plan = _mapping(runtime.get("stage_plan"))
            stage_output_ids = list(stage_plan.get("target_output_ids") or [])
            output_records, results = _collect_declared_outputs(
                profile_id=profile_id,
                prompt_id=prompt_id,
                manifest=manifest,
                history_outputs=history_outputs,
                root_dir=root_dir,
                output_ids=stage_output_ids,
            )
            registry.mark_completed(
                run_id,
                surface=_SURFACE,
                message="Comfy Space run completed and declared outputs were collected into Neo.",
                outputs=output_records,
                runtime={"backend_completed": True, "output_collection_complete": True, "results": results},
            )
            record_space_run_progress(run_id, event_type="completed", root_dir=root_dir, source="history", force_message="Comfy Space run completed and declared outputs were collected into Neo.")
            terminal_state = "completed"
        except (SpaceExecutionRuntimeError, SpaceAssetBusError, ValueError) as exc:
            registry.mark_failed(
                run_id,
                surface=_SURFACE,
                message=f"Comfy finished, but Neo could not collect declared outputs: {exc}",
                error=str(exc),
                runtime={"backend_completed": True, "output_collection_complete": False},
            )
            record_space_run_progress(run_id, event_type="failed", root_dir=root_dir, source="history", force_message=f"Comfy finished, but Neo could not collect declared outputs: {exc}")
            terminal_state = "failed"

    try:
        resolved = resolve_comfy_execution_profile(profile_id)
        gpu_manager = get_comfy_gpu_lifecycle_manager()
        gpu_manager.complete_prompt(
            base_url=str(resolved["base_url"]),
            prompt_id=prompt_id,
            state=terminal_state,
            cleanup_after=False,
        )
        if terminal_state == "completed":
            latest = registry.get(run_id, surface=_SURFACE) or record
            latest_plan = _mapping(_mapping(latest.get("runtime")).get("stage_plan"))
            policy = str(latest_plan.get("resource_policy") or "automatic")
            if policy == "free_memory":
                release = gpu_manager.release_runtime_memory(base_url=str(resolved["base_url"]), unload_models=False, free_memory=True)
                registry.upsert(run_id, surface=_SURFACE, updates={"runtime": {"stage_resource_release": release}})
            elif policy == "unload_models":
                release = gpu_manager.release_runtime_memory(base_url=str(resolved["base_url"]), unload_models=True, free_memory=True)
                registry.upsert(run_id, surface=_SURFACE, updates={"runtime": {"stage_resource_release": release}})
    except Exception:  # noqa: BLE001 - GPU lifecycle watcher/recovery remains a second safety net.
        pass
    return _result_from_record(registry.get(run_id, surface=_SURFACE) or record)


def get_space_run(run_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    record = _job_registry(root_dir).get(run_id, surface=_SURFACE)
    if not record:
        raise SpaceExecutionRuntimeError(f"Unknown Comfy Space run: {run_id}", stage="running", code="run_not_found")
    return _result_from_record(record)


def cancel_space_run(run_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    registry = _job_registry(root_dir)
    record = registry.get(run_id, surface=_SURFACE)
    if not record:
        raise SpaceExecutionRuntimeError(f"Unknown Comfy Space run: {run_id}", stage="cancel", code="run_not_found")
    if str(record.get("status") or "").lower() in _TERMINAL_STATUSES:
        return _result_from_record(record)

    profile_id = str(record.get("profile_id") or "")
    prompt_id = str(record.get("provider_job_id") or "")
    if not profile_id or not prompt_id:
        raise SpaceExecutionRuntimeError("Run record is missing selected runtime/prompt identity.", stage="cancel", code="run_lineage_incomplete")

    registry.request_cancel(run_id, surface=_SURFACE)
    record_space_run_progress(run_id, event_type="cancel_requested", root_dir=root_dir, source="neo_runtime")
    try:
        cancellation = cancel_comfy_prompt(profile_id, prompt_id=prompt_id)
    except ComfyExecutionTransportError as exc:
        raise SpaceExecutionRuntimeError(str(exc), stage="cancel", code="cancel_request_failed") from exc

    state = str(cancellation.get("state") or "")
    if state == "cancelled_pending":
        registry.mark_cancelled(run_id, surface=_SURFACE, message="Queued Comfy Space run cancelled before execution.")
        record_space_run_progress(run_id, event_type="cancelled", root_dir=root_dir, source="neo_runtime", force_message="Queued Comfy Space run cancelled before execution.")
        try:
            resolved = resolve_comfy_execution_profile(profile_id)
            get_comfy_gpu_lifecycle_manager().complete_prompt(base_url=str(resolved["base_url"]), prompt_id=prompt_id, state="cancelled", cleanup_after=False)
        except Exception:  # noqa: BLE001
            pass
        return _result_from_record(registry.get(run_id, surface=_SURFACE) or record)
    if state == "already_finished":
        return poll_space_run(run_id, root_dir=root_dir)
    if state == "not_queued":
        # The prompt may have just completed between queue inspection and cancel.
        return poll_space_run(run_id, root_dir=root_dir)

    # Running prompt: interruption is asynchronous. Keep the shared GPU lease until
    # history/queue confirms completion, then poll_space_run finalizes cancellation.
    registry.upsert(
        run_id,
        surface=_SURFACE,
        updates={"message": "Cancellation requested; waiting for ComfyUI interruption confirmation."},
    )
    current = registry.get(run_id, surface=_SURFACE) or record
    payload = _result_from_record(current)
    payload["status"] = "cancel_requested"
    payload["ok"] = True
    return payload



def list_space_runs(*, limit: int = 50, active_only: bool = False, root_dir: Path | str | None = None) -> dict[str, Any]:
    return list_persistent_space_runs(limit=limit, active_only=active_only, root_dir=root_dir)


def space_run_progress(run_id: str, *, after_sequence: int = 0, root_dir: Path | str | None = None) -> dict[str, Any]:
    payload = get_space_run_progress(run_id, after_sequence=after_sequence, root_dir=root_dir)
    if not payload.get("ok"):
        raise SpaceExecutionRuntimeError(f"Unknown Comfy Space run: {run_id}", stage="running", code="run_not_found")
    return payload


def recover_space_runs(*, root_dir: Path | str | None = None, limit: int = 200) -> dict[str, Any]:
    return recover_space_run_monitors(root_dir=root_dir, limit=limit)

def execution_runtime_capabilities() -> dict[str, Any]:
    transport = execution_transport_capabilities()
    return {
        "schema_version": SPACE_EXECUTION_RUNTIME_SCHEMA_VERSION,
        "service_version": SPACE_EXECUTION_RUNTIME_SERVICE_VERSION,
        "status": "implemented",
        "published_spaces_only": True,
        "selected_runtime_only": True,
        "runtime_fallback": False,
        "publication_receipt_reverified": True,
        "graph_anchor_sidecar_integrity_reverified": True,
        "stage_sidecar_integrity_reverified": True,
        "stage_sidecar_execution_authority": True,
        "stage_aware_execution": True,
        "space_variants": True,
        "variant_base_immutable": True,
        "stage_run_modes": ["all", "stage", "continue"],
        "stage_ancestor_slice_execution": True,
        "stage_resource_policies": ["automatic", "keep_loaded", "free_memory", "unload_models"],
        "graph_anchor_sidecar_execution_authority": False,
        "per_run_graph_addons": True,
        "graph_addons_execution_clone_only": True,
        "graph_addon_builtin_modules": ["neo.controlnet.conditioning"],
        "raw_comfy_node_safe_transform_lane": True,
        "graph_addon_anchor_conflicts_fail_closed": True,
        "live_object_info_preflight": True,
        "persistent_dependency_health": True,
        "dependency_health_execution_authority": False,
        "dependency_health_queue_time_recheck": True,
        "runtime_model_reconciliation_on_clone": True,
        "neutral_asset_bus": True,
        "shared_gpu_lease": True,
        "shared_job_registry": True,
        "prompt_submission": True,
        "history_poll": True,
        "declared_output_resolution": True,
        "output_asset_collection": True,
        "cancel_pending": True,
        "cancel_running": True,
        "persistent_cross_navigation_jobs": True,
        "live_node_progress": True,
        "navigation_safe_progress_snapshot": True,
        "bounded_progress_event_journal": True,
        "startup_run_recovery": True,
        "websocket_history_queue_fallback": True,
        "progress_runtime": progress_runtime_capabilities(),
        "public_runner_mounted": True,
        "automatic_dependency_install": False,
        "generated_space_code": False,
        "transport": transport,
    }
