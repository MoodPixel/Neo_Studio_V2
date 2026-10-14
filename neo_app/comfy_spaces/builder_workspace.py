from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Mapping

from neo_app.comfy_spaces.builder_service import (
    SPACE_BUILDER_SERVICE_VERSION,
    SpaceBuilderError,
    builder_service_capabilities,
    compile_builder_draft,
    create_builder_draft_from_import,
    load_builder_draft,
    save_builder_draft,
)
from neo_app.comfy_spaces.importer import SpaceWorkflowImportError, import_comfy_workflow_payload
from neo_app.comfy_spaces.paths import (
    SPACE_BUILDER_DRAFT_FILENAME,
    comfy_space_draft_analysis_path,
    comfy_space_draft_preview_manifest_path,
    comfy_space_draft_preview_ui_path,
    comfy_space_draft_preview_dynamic_controls_path,
    comfy_space_manifest_path,
    comfy_space_publication_receipt_path,
    comfy_space_draft_replacement_plan_path,
    comfy_spaces_drafts_root,
)
from neo_app.comfy_spaces.publish_service import (
    SpacePublishError,
    evaluate_builder_draft_publish_readiness,
    publish_builder_draft,
    publish_service_capabilities,
)
from neo_app.comfy_spaces.runtime_probe import probe_comfy_profile_object_info
from neo_app.comfy_spaces.versioning import (
    SpaceVersioningError,
    confirm_replacement_review,
    create_workflow_replacement_draft,
    load_replacement_plan,
    versioning_capabilities,
)
from neo_app.core.pydantic_compat import model_to_dict


SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION = "neo.comfy_space.builder_workspace.v1"


class SpaceBuilderWorkspaceError(ValueError):
    """Raised when the Admin Builder workspace request cannot be fulfilled safely."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SpaceBuilderWorkspaceError(f"Unable to read Builder workspace file: {path}") from exc


def _preview_payload(draft_id: str, root_dir: Path | str | None) -> dict[str, Any]:
    manifest_path = comfy_space_draft_preview_manifest_path(draft_id, root_dir)
    ui_path = comfy_space_draft_preview_ui_path(draft_id, root_dir)
    dynamic_path = comfy_space_draft_preview_dynamic_controls_path(draft_id, root_dir)
    if not manifest_path.is_file() or not ui_path.is_file():
        return {"available": False, "manifest": None, "ui": None, "dynamic_controls": None, "status": "not_compiled"}
    return {
        "available": True,
        "manifest": _read_json(manifest_path),
        "ui": _read_json(ui_path),
        "dynamic_controls": _read_json(dynamic_path) if dynamic_path.is_file() else None,
        "status": "compiled",
    }




def _publication_payload(draft, root_dir: Path | str | None) -> dict[str, Any]:
    manifest_path = comfy_space_manifest_path(draft.identity.space_id, draft.identity.version, root_dir)
    receipt_path = comfy_space_publication_receipt_path(draft.identity.space_id, draft.identity.version, root_dir)
    if not manifest_path.is_file():
        return {
            "published": False,
            "space_id": draft.identity.space_id,
            "version": draft.identity.version,
            "manifest_path": str(manifest_path),
            "receipt": None,
            "status": "not_published",
        }
    receipt = None
    status = "published"
    if receipt_path.is_file():
        try:
            payload = _read_json(receipt_path)
            receipt = payload if isinstance(payload, dict) else None
        except SpaceBuilderWorkspaceError:
            status = "published_receipt_invalid"
    else:
        status = "published_receipt_missing"
    return {
        "published": True,
        "space_id": draft.identity.space_id,
        "version": draft.identity.version,
        "manifest_path": str(manifest_path),
        "receipt_path": str(receipt_path),
        "receipt": receipt,
        "status": status,
    }

def _analysis_payload(draft_id: str, root_dir: Path | str | None) -> dict[str, Any]:
    payload = _read_json(comfy_space_draft_analysis_path(draft_id, root_dir))
    if not isinstance(payload, dict):
        raise SpaceBuilderWorkspaceError("Builder analysis must be a JSON object")
    return payload


def _draft_summary(draft_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
        preview = _preview_payload(draft_id, root_dir)
        publication = _publication_payload(draft, root_dir)
        return {
            "draft_id": draft.draft_id,
            "space_id": draft.identity.space_id,
            "name": draft.identity.name,
            "version": draft.identity.version,
            "description": draft.identity.description,
            "revision": draft.revision,
            "updated_at": draft.updated_at,
            "created_at": draft.created_at,
            "source_name": draft.provenance.source_name,
            "runtime_profile_id": draft.provenance.runtime_profile_id,
            "enabled_input_count": sum(1 for item in draft.inputs if item.enabled),
            "enabled_output_count": sum(1 for item in draft.outputs if item.enabled),
            "preview_available": bool(preview["available"]),
            "published": bool(publication["published"]),
            "publication_status": publication["status"],
            "status": "ready",
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 - invalid drafts remain visible for diagnosis
        return {
            "draft_id": str(draft_id),
            "space_id": "",
            "name": str(draft_id),
            "version": "",
            "description": "",
            "revision": 0,
            "updated_at": "",
            "created_at": "",
            "source_name": "",
            "runtime_profile_id": "",
            "enabled_input_count": 0,
            "enabled_output_count": 0,
            "preview_available": False,
            "published": False,
            "publication_status": "unknown",
            "status": "invalid",
            "error": str(exc),
        }


def list_builder_drafts(*, root_dir: Path | str | None = None) -> dict[str, Any]:
    root = comfy_spaces_drafts_root(root_dir)
    rows: list[dict[str, Any]] = []
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and (child / SPACE_BUILDER_DRAFT_FILENAME).is_file():
                rows.append(_draft_summary(child.name, root_dir=root_dir))
    rows.sort(key=lambda item: (str(item.get("updated_at") or ""), str(item.get("name") or "").lower()), reverse=True)
    return {
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "service_version": SPACE_BUILDER_SERVICE_VERSION,
        "status": "ready",
        "drafts": rows,
        "summary": {
            "draft_count": len(rows),
            "ready_count": sum(1 for item in rows if item["status"] == "ready"),
            "invalid_count": sum(1 for item in rows if item["status"] != "ready"),
            "preview_count": sum(1 for item in rows if item["preview_available"]),
            "published_count": sum(1 for item in rows if item.get("published")),
        },
    }


def builder_workspace_payload(draft_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        draft = load_builder_draft(draft_id, root_dir=root_dir, verify_frozen_files=True)
        analysis = _analysis_payload(draft_id, root_dir)
        preview = _preview_payload(draft_id, root_dir)
    except (SpaceBuilderError, SpaceBuilderWorkspaceError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc

    replacement_plan = load_replacement_plan(draft_id, root_dir=root_dir)

    return {
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "service_version": SPACE_BUILDER_SERVICE_VERSION,
        "status": "ready",
        "loaded_at": _now_iso(),
        "draft": model_to_dict(draft),
        "analysis": {
            "summary": analysis.get("summary") or {},
            "graph": analysis.get("graph") or {},
            "dependencies": analysis.get("dependencies") or {},
            "runtime": analysis.get("runtime") or {},
            "candidates": analysis.get("candidates") or {},
            "graph_anchors": analysis.get("graph_anchors") or {},
            "authoring_map": analysis.get("authoring_map") or {},
            "warnings": analysis.get("warnings") or [],
            "errors": analysis.get("errors") or [],
        },
        "preview": preview,
        "replacement": model_to_dict(replacement_plan) if replacement_plan is not None else None,
        "publication": _publication_payload(draft, root_dir),
        "capabilities": builder_workspace_capabilities(),
    }


def create_builder_workspace_from_workflow(
    workflow: Mapping[str, Any],
    *,
    source_name: str = "workflow.json",
    runtime_profile_id: str = "",
    use_live_object_info: bool = False,
    root_dir: Path | str | None = None,
    space_id: str | None = None,
    name: str | None = None,
) -> dict[str, Any]:
    if not isinstance(workflow, Mapping):
        raise SpaceBuilderWorkspaceError("Workflow must be a JSON object")

    object_info: Mapping[str, Any] | None = None
    runtime_probe: Mapping[str, Any] | None = None
    clean_runtime_profile_id = str(runtime_profile_id or "").strip()
    # NCS-5E.1: a selected Comfy runtime is itself consent to perform the
    # read-only schema probe. Normal ComfyUI Save workflows frequently need
    # /object_info to resolve positional widget values and custom-node schemas;
    # making authors understand a second toggle produced a needless UX trap.
    should_probe_runtime = bool(clean_runtime_profile_id)
    if use_live_object_info and not clean_runtime_profile_id:
        raise SpaceBuilderWorkspaceError("Select a Comfy runtime profile before enabling live runtime analysis")
    if should_probe_runtime:
        try:
            object_info, runtime_probe = probe_comfy_profile_object_info(clean_runtime_profile_id)
        except Exception as exc:  # noqa: BLE001 - normalize provider errors at Admin boundary
            raise SpaceBuilderWorkspaceError(str(exc)) from exc

    try:
        imported = import_comfy_workflow_payload(
            dict(workflow),
            source_name=source_name,
            root_dir=root_dir,
            object_info=object_info,
            runtime_profile_id=clean_runtime_profile_id,
            runtime_probe=runtime_probe,
        )
        created = create_builder_draft_from_import(
            imported["import_id"],
            root_dir=root_dir,
            space_id=space_id,
            name=name,
        )
    except (SpaceWorkflowImportError, SpaceBuilderError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc

    return {
        "ok": True,
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "import_id": imported["import_id"],
        "draft_id": created["draft_id"],
        "source_shape": str((imported.get("normalization") or {}).get("source_shape") or ""),
        "source_format": str((imported.get("record") or {}).get("source_format") or ""),
        "converted_from_editor": bool((imported.get("normalization") or {}).get("converted_from_editor")),
        "normalization": imported.get("normalization") or {},
        "runtime_probe": runtime_probe or {},
        "workspace": builder_workspace_payload(created["draft_id"], root_dir=root_dir),
        "execution_performed": False,
        "published": False,
    }


def create_replacement_workspace_from_workflow(
    workflow: Mapping[str, Any],
    *,
    base_space_id: str,
    base_version: str,
    target_version: str,
    source_name: str = "workflow.json",
    runtime_profile_id: str = "",
    use_live_object_info: bool = False,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    if not isinstance(workflow, Mapping):
        raise SpaceBuilderWorkspaceError("Workflow must be a JSON object")
    object_info: Mapping[str, Any] | None = None
    runtime_probe: Mapping[str, Any] | None = None
    clean_runtime_profile_id = str(runtime_profile_id or "").strip()
    if use_live_object_info and not clean_runtime_profile_id:
        raise SpaceBuilderWorkspaceError("Select a Comfy runtime profile before enabling live runtime analysis")
    if clean_runtime_profile_id:
        try:
            object_info, runtime_probe = probe_comfy_profile_object_info(clean_runtime_profile_id)
        except Exception as exc:  # noqa: BLE001
            raise SpaceBuilderWorkspaceError(str(exc)) from exc
    try:
        imported = import_comfy_workflow_payload(
            dict(workflow), source_name=source_name, root_dir=root_dir, object_info=object_info,
            runtime_profile_id=clean_runtime_profile_id, runtime_probe=runtime_probe,
        )
        replacement = create_workflow_replacement_draft(
            space_id=base_space_id, from_version=base_version, to_version=target_version,
            import_id=imported["import_id"], root_dir=root_dir,
        )
    except (SpaceWorkflowImportError, SpaceBuilderError, SpaceVersioningError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc
    return {
        "ok": True,
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "import_id": imported["import_id"],
        "draft_id": replacement["draft_id"],
        "replacement_plan": replacement["plan"],
        "runtime_probe": runtime_probe or {},
        "workspace": builder_workspace_payload(replacement["draft_id"], root_dir=root_dir),
        "execution_performed": False,
        "published": False,
    }


def confirm_builder_replacement_review(
    draft_id: str, *, confirm_review: bool, root_dir: Path | str | None = None,
) -> dict[str, Any]:
    try:
        result = confirm_replacement_review(draft_id, confirm_review=confirm_review, root_dir=root_dir)
    except (SpaceVersioningError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc
    return {**result, "workspace": builder_workspace_payload(draft_id, root_dir=root_dir)}


def save_builder_workspace(
    draft_id: str,
    draft_payload: Mapping[str, Any],
    *,
    expected_revision: int | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    try:
        saved = save_builder_draft(
            draft_id,
            draft_payload,
            root_dir=root_dir,
            expected_revision=expected_revision,
        )
    except (SpaceBuilderError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc
    return {
        "ok": True,
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "revision": saved.revision,
        "workspace": builder_workspace_payload(draft_id, root_dir=root_dir),
        "execution_performed": False,
        "published": False,
    }


def compile_builder_workspace_preview(draft_id: str, *, root_dir: Path | str | None = None) -> dict[str, Any]:
    try:
        compiled = compile_builder_draft(draft_id, root_dir=root_dir, write_preview=True)
    except (SpaceBuilderError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc
    return {
        "ok": bool(compiled.get("ok")),
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "compile": compiled,
        "workspace": builder_workspace_payload(draft_id, root_dir=root_dir),
        "execution_performed": False,
        "published": False,
    }


def check_builder_workspace_publish_readiness(
    draft_id: str,
    *,
    expected_revision: int | None = None,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    try:
        gate = evaluate_builder_draft_publish_readiness(
            draft_id,
            root_dir=root_dir,
            expected_revision=expected_revision,
        )
    except (SpacePublishError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc
    return {
        "ok": bool(gate.get("ready")),
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "publish_gate": gate,
        "execution_performed": False,
        "published": False,
    }


def publish_builder_workspace(
    draft_id: str,
    *,
    expected_revision: int,
    confirm_publish: bool,
    root_dir: Path | str | None = None,
) -> dict[str, Any]:
    try:
        result = publish_builder_draft(
            draft_id,
            root_dir=root_dir,
            expected_revision=expected_revision,
            confirm_publish=confirm_publish,
        )
    except (SpacePublishError, ValueError) as exc:
        raise SpaceBuilderWorkspaceError(str(exc)) from exc
    return {
        **result,
        "workspace_schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "workspace": builder_workspace_payload(draft_id, root_dir=root_dir),
    }


def builder_workspace_capabilities() -> dict[str, Any]:
    return {
        "schema_version": SPACE_BUILDER_WORKSPACE_SCHEMA_VERSION,
        "status": "preview",
        "builder_service_version": SPACE_BUILDER_SERVICE_VERSION,
        "admin_workspace_implemented": True,
        "workflow_json_import": True,
        "workflow_replacement_import": True,
        "replacement_diff_review": True,
        "replacement_review_confirmation": True,
        "normal_save_workflow_import": True,
        "api_workflow_import": True,
        "preferred_import_format": "comfy_save",
        "editor_workflow_conversion": True,
        "live_object_info_optional": True,
        "selected_runtime_auto_object_info": True,
        "draft_list_open": True,
        "draft_edit_save": True,
        "optimistic_revision_save": True,
        "candidate_inspection": True,
        "all_detected_candidates_visible": True,
        "candidate_exposure_recommendations_advisory": True,
        "node_aware_control_recommendations": True,
        "recommendation_policy": "node_aware_v2",
        "graph_anchor_inspection": True,
        "graph_anchor_author_confirmation": True,
        "graph_anchor_auto_confirmation": False,
        "node_aware_authoring_map": True,
        "section_grouping_modes": ["manual", "semantic_role", "source_node", "source_subgraph"],
        "sections_change_execution": False,
        "graph_mutation_implemented": False,
        "runtime_model_catalog_resolution": True,
        "runtime_model_catalog_authority": "comfy_object_info_combo",
        "input_control_authoring": True,
        "output_authoring": True,
        "presentation_authoring": True,
        "preview_compile": True,
        "preview_executes_workflow": False,
        "shared_runtime_renderer_implemented": True,
        "shared_runtime_renderer_schema_version": "neo.comfy_space.renderer.v1",
        "publish_gate_implemented": True,
        "publish_readiness_check": True,
        "published_library_promotion": True,
        "publish_service_contract": publish_service_capabilities(),
        # These two flags describe the Admin Builder host itself. Builder Preview
        # stays non-executing even though NCS-7 now provides a separate runtime service.
        "workflow_execution_implemented": False,
        "builder_preview_executes_workflow": False,
        "asset_transport_implemented": False,
        "neutral_asset_bus_implemented": True,
        "asset_bus_runtime_wiring_deferred_to_ncs7": False,
        "universal_execution_runtime_implemented": True,
        "execution_runtime_public_runner_mounted": True,
        "public_comfy_spaces_surface_implemented": True,
        "draft_delete_implemented": False,
        "versioning_contract": versioning_capabilities(),
        "service_contract": builder_service_capabilities(),
    }
