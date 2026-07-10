from __future__ import annotations

from collections import Counter
from typing import Any

from .installed_scanner import load_installed_index
from .manifest_loader import (
    load_model_catalog,
    load_model_packs,
    load_workspace_requirements,
    validate_loaded_manifests,
)
from .manifest_schema import validate_workspace_requirements
from .model_packs import build_model_pack_download_plan, build_model_pack_status

WORKSPACE_REQUIREMENTS_PAYLOAD_SCHEMA_ID = "neo.admin.models.workspace_requirements_payload.v1"
WORKSPACE_STATUS_SCHEMA_ID = "neo.admin.models.workspace_status.v1"
WORKSPACE_DOWNLOAD_PLAN_SCHEMA_ID = "neo.admin.models.workspace_download_plan.v1"
PHASE_ID = "phase10_workspace_integration"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_lower(value: Any) -> str:
    return _clean(value).lower()


def _catalog_map() -> dict[str, dict[str, Any]]:
    catalog = load_model_catalog()
    return {
        _clean(record.get("id")): record
        for record in _as_list(catalog.get("records"))
        if isinstance(record, dict) and _clean(record.get("id"))
    }


def _workspace_records() -> list[dict[str, Any]]:
    manifest = load_workspace_requirements()
    return [item for item in _as_list(manifest.get("workspaces")) if isinstance(item, dict)]


def _workspace_map() -> dict[str, dict[str, Any]]:
    return {_clean(item.get("id")): item for item in _workspace_records() if _clean(item.get("id"))}


def _summarize_workspaces(workspaces: list[dict[str, Any]]) -> dict[str, Any]:
    surface_counts = Counter(_clean_lower(item.get("surface_id")) or "unknown" for item in workspaces)
    backend_counts = Counter(_clean_lower(item.get("backend")) or "unknown" for item in workspaces)
    base_counts = Counter(_clean_lower(item.get("base_model")) or "unknown" for item in workspaces)
    pack_count = sum(len(_as_list(item.get("model_pack_ids"))) for item in workspaces)
    required_count = sum(len(_as_list(item.get("required_catalog_ids"))) for item in workspaces)
    optional_count = sum(len(_as_list(item.get("optional_catalog_ids"))) for item in workspaces)
    return {
        "workspace_count": len(workspaces),
        "recommended_count": sum(1 for item in workspaces if bool(item.get("recommended"))),
        "linked_pack_count": pack_count,
        "required_catalog_link_count": required_count,
        "optional_catalog_link_count": optional_count,
        "surface_counts": dict(sorted(surface_counts.items())),
        "backend_counts": dict(sorted(backend_counts.items())),
        "base_model_counts": dict(sorted(base_counts.items())),
    }


def _catalog_preview(catalog_id: str, catalog_by_id: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    record = catalog_by_id.get(catalog_id)
    if not record:
        return None
    return {
        "id": record.get("id"),
        "display_name": record.get("display_name"),
        "category": record.get("category"),
        "base_model": record.get("base_model"),
        "model_type": record.get("model_type"),
        "source_mode": record.get("source_mode"),
        "install": record.get("install"),
    }


def _workspace_preview(workspace: dict[str, Any], catalog_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    required_ids = [_clean(item) for item in _as_list(workspace.get("required_catalog_ids")) if _clean(item)]
    optional_ids = [_clean(item) for item in _as_list(workspace.get("optional_catalog_ids")) if _clean(item)]
    return {
        "id": workspace.get("id"),
        "surface_id": workspace.get("surface_id"),
        "workspace_label": workspace.get("workspace_label"),
        "display_name": workspace.get("display_name"),
        "description": workspace.get("description"),
        "backend": workspace.get("backend"),
        "base_model": workspace.get("base_model"),
        "workflow_family": workspace.get("workflow_family"),
        "recommended": bool(workspace.get("recommended")),
        "model_pack_ids": _as_list(workspace.get("model_pack_ids")),
        "required_catalog_ids": required_ids,
        "optional_catalog_ids": optional_ids,
        "required_models": [_catalog_preview(catalog_id, catalog_by_id) for catalog_id in required_ids],
        "optional_models": [_catalog_preview(catalog_id, catalog_by_id) for catalog_id in optional_ids],
        "triggers": _as_dict(workspace.get("triggers")),
        "guide_filter": _as_dict(workspace.get("guide_filter")),
        "ui": _as_dict(workspace.get("ui")),
        "actions": {
            "open_model_guide_path": "Admin → Models",
            "filter_endpoint": "/api/admin/models/filter",
            "scan_installed_endpoint": "/api/admin/models/scan-installed",
            "pack_status_endpoint": "/api/admin/models/packs/status",
            "pack_download_plan_endpoint": "/api/admin/models/packs/download/plan",
        },
    }


def admin_model_workspace_requirements_payload() -> dict[str, Any]:
    manifest = load_workspace_requirements()
    workspaces = _workspace_records()
    catalog_by_id = _catalog_map()
    validation = validate_loaded_manifests()
    workspace_validation = validate_workspace_requirements(
        manifest,
        catalog=load_model_catalog(),
        model_packs=load_model_packs(),
    )
    return {
        "schema_id": WORKSPACE_REQUIREMENTS_PAYLOAD_SCHEMA_ID,
        "phase": PHASE_ID,
        "status": "ready" if validation.get("ok") and workspace_validation.ok else "needs attention",
        "workspace_manifest": {
            "schema_id": manifest.get("schema_id"),
            "version": manifest.get("version"),
            "updated_at": manifest.get("updated_at"),
            "description": manifest.get("description"),
        },
        "summary": _summarize_workspaces(workspaces),
        "workspaces": [_workspace_preview(item, catalog_by_id) for item in workspaces],
        "validation": {
            "ok": validation.get("ok") and workspace_validation.ok,
            "errors": list(validation.get("errors") or []) + list(workspace_validation.errors),
            "warnings": list(validation.get("warnings") or []) + list(workspace_validation.warnings),
        },
        "capabilities": {
            "workspace_requirements": True,
            "workspace_status": True,
            "workspace_download_planning": True,
            "workspace_ui_warnings": True,
            "starts_download_jobs": False,
            "actual_downloads": False,
        },
        "privacy_policy": {
            "repo_manifest_only": True,
            "remote_calls": False,
            "stores_user_paths": False,
            "stores_tokens": False,
            "stores_remote_metadata": False,
            "saves_remote_previews": False,
            "runtime_data_policy": "Workspace requirements are public repo metadata. Status checks may read an explicit scan payload or neo_data/cache/model_installed_index.json; they do not write paths, tokens, previews, or remote metadata.",
        },
    }


def _installed_status_by_catalog(payload: dict[str, Any] | None = None) -> tuple[dict[str, dict[str, Any]], bool]:
    source = _as_dict(payload)
    scan = _as_dict(source.get("scan")) or _as_dict(source.get("installed_scan"))
    if not scan:
        scan = _as_dict(load_installed_index())
    result: dict[str, dict[str, Any]] = {}
    for item in _as_list(scan.get("catalog_status")):
        if isinstance(item, dict) and _clean(item.get("catalog_id")):
            result[_clean(item.get("catalog_id"))] = item
    return result, bool(result)


def _workspace_matches(workspace: dict[str, Any], filters: dict[str, Any]) -> bool:
    if _clean(filters.get("workspace_id")):
        return _clean(workspace.get("id")) == _clean(filters.get("workspace_id"))
    workspace_ids = {_clean(item) for item in _as_list(filters.get("workspace_ids")) if _clean(item)}
    if workspace_ids and _clean(workspace.get("id")) not in workspace_ids:
        return False
    for key in ("surface_id", "backend", "base_model", "workflow_family"):
        value = _clean_lower(filters.get(key))
        if value and _clean_lower(workspace.get(key)) != value:
            return False
    return True


def _requested_workspaces(payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = _as_dict(payload)
    workspaces = _workspace_records()
    selected = [item for item in workspaces if _workspace_matches(item, data)]
    if (data.get("workspace_id") or data.get("workspace_ids")) and not selected:
        return []
    return selected if any(data.get(key) for key in ("workspace_id", "workspace_ids", "surface_id", "backend", "base_model", "workflow_family")) else workspaces


def _catalog_status_row(catalog_id: str, required: bool, installed_by_id: dict[str, dict[str, Any]], has_scan: bool, catalog_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    installed = installed_by_id.get(catalog_id, {})
    local_status = _clean(installed.get("overall_status")) or ("not_scanned" if not has_scan else "unknown")
    if local_status == "installed":
        readiness = "installed"
    elif local_status == "local_candidates":
        readiness = "needs_review"
    elif local_status == "missing":
        readiness = "missing"
    else:
        readiness = "not_scanned" if not has_scan else "unknown"
    return {
        "catalog_id": catalog_id,
        "required": required,
        "readiness": readiness,
        "local_status": local_status,
        "record": _catalog_preview(catalog_id, catalog_by_id),
        "installed_status": installed or None,
    }


def build_workspace_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    requested = _requested_workspaces(data)
    if (data.get("workspace_id") or data.get("workspace_ids")) and not requested:
        return {
            "schema_id": WORKSPACE_STATUS_SCHEMA_ID,
            "ok": False,
            "status": "workspace_not_found",
            "phase": PHASE_ID,
            "workspaces": [],
            "errors": ["Requested workspace requirement was not found."],
            "warnings": [],
        }
    catalog_by_id = _catalog_map()
    installed_by_id, has_scan = _installed_status_by_catalog(data)
    rows: list[dict[str, Any]] = []
    for workspace in requested:
        pack_ids = [_clean(item) for item in _as_list(workspace.get("model_pack_ids")) if _clean(item)]
        pack_status = build_model_pack_status({"pack_ids": pack_ids, "scan": data.get("scan") or data.get("installed_scan")}) if pack_ids else None
        required_rows = [
            _catalog_status_row(_clean(catalog_id), True, installed_by_id, has_scan, catalog_by_id)
            for catalog_id in _as_list(workspace.get("required_catalog_ids"))
            if _clean(catalog_id)
        ]
        optional_rows = [
            _catalog_status_row(_clean(catalog_id), False, installed_by_id, has_scan, catalog_by_id)
            for catalog_id in _as_list(workspace.get("optional_catalog_ids"))
            if _clean(catalog_id)
        ]
        required_problem_count = sum(1 for item in required_rows if item.get("readiness") != "installed")
        required_installed_count = sum(1 for item in required_rows if item.get("readiness") == "installed")
        pack_problem_count = 0
        pack_variant_count = 0
        if pack_status:
            for pack in _as_list(pack_status.get("packs")):
                if not isinstance(pack, dict):
                    continue
                if pack.get("overall_status") in {"missing_required", "unknown"}:
                    pack_problem_count += 1
                for item in _as_list(pack.get("items")):
                    if isinstance(item, dict) and item.get("readiness") == "needs_variant_selection":
                        pack_variant_count += 1
        if not has_scan:
            overall = "not_scanned"
        elif required_problem_count or pack_problem_count:
            overall = "missing_required"
        elif pack_variant_count:
            overall = "needs_variant_selection"
        else:
            overall = "ready"
        missing_required = [item for item in required_rows if item.get("readiness") not in {"installed", "not_scanned"}]
        actions = {
            "open_model_guide_path": "Admin → Models",
            "guide_filter": _as_dict(workspace.get("guide_filter")),
            "scan_installed_endpoint": "/api/admin/models/scan-installed",
            "workspace_status_endpoint": "/api/admin/models/workspaces/status",
            "pack_status_endpoint": "/api/admin/models/packs/status",
            "pack_download_plan_endpoint": "/api/admin/models/packs/download/plan",
        }
        rows.append({
            "workspace_id": workspace.get("id"),
            "display_name": workspace.get("display_name"),
            "surface_id": workspace.get("surface_id"),
            "backend": workspace.get("backend"),
            "base_model": workspace.get("base_model"),
            "workflow_family": workspace.get("workflow_family"),
            "overall_status": overall,
            "severity": _as_dict(workspace.get("ui")).get("severity_when_missing") or "warning",
            "summary": {
                "has_installed_scan": has_scan,
                "required_total": len(required_rows),
                "required_installed_count": required_installed_count,
                "required_problem_count": required_problem_count,
                "optional_total": len(optional_rows),
                "pack_count": len(pack_ids),
                "pack_problem_count": pack_problem_count,
                "pack_variant_selection_count": pack_variant_count,
            },
            "required_models": required_rows,
            "optional_models": optional_rows,
            "pack_status": pack_status,
            "ui_message": _workspace_message(workspace, overall, has_scan, required_rows),
            "actions": actions,
            "missing_required": missing_required,
        })
    return {
        "schema_id": WORKSPACE_STATUS_SCHEMA_ID,
        "ok": True,
        "status": "ready",
        "phase": PHASE_ID,
        "has_installed_scan": has_scan,
        "workspaces": rows,
        "summary": {
            "workspace_count": len(rows),
            "ready_count": sum(1 for item in rows if item.get("overall_status") == "ready"),
            "missing_required_count": sum(1 for item in rows if item.get("overall_status") == "missing_required"),
            "needs_variant_selection_count": sum(1 for item in rows if item.get("overall_status") == "needs_variant_selection"),
            "not_scanned_count": sum(1 for item in rows if item.get("overall_status") == "not_scanned"),
        },
        "errors": [],
        "warnings": [] if has_scan else ["installed_scan_not_available"],
        "privacy_policy": {
            "remote_calls": False,
            "stores_user_paths": False,
            "stores_tokens": False,
            "stores_remote_metadata": False,
            "runtime_read_policy": "Reads an explicit scan payload or neo_data/cache/model_installed_index.json only when available. No remote metadata, previews, tokens, or user paths are written.",
        },
    }


def _workspace_message(workspace: dict[str, Any], overall: str, has_scan: bool, required_rows: list[dict[str, Any]]) -> str:
    ui = _as_dict(workspace.get("ui"))
    if not has_scan:
        return str(ui.get("empty_state") or "Run an installed-model scan in Admin → Models before checking workspace readiness.")
    if overall == "ready":
        return f"{workspace.get('display_name')} model requirements look ready."
    missing_names = [
        _as_dict(row.get("record")).get("display_name") or row.get("catalog_id")
        for row in required_rows
        if row.get("readiness") != "installed"
    ]
    if missing_names:
        return "Missing required model(s): " + ", ".join(str(item) for item in missing_names)
    return str(ui.get("empty_state") or "Some model requirements need attention in Admin → Models.")


def build_workspace_download_plan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    requested = _requested_workspaces(data)
    if (data.get("workspace_id") or data.get("workspace_ids")) and not requested:
        return {
            "schema_id": WORKSPACE_DOWNLOAD_PLAN_SCHEMA_ID,
            "ok": False,
            "status": "workspace_not_found",
            "phase": PHASE_ID,
            "workspace_plans": [],
            "errors": ["Requested workspace requirement was not found."],
            "warnings": [],
        }
    workspace_plans: list[dict[str, Any]] = []
    errors: list[str] = []
    for workspace in requested:
        pack_plans: list[dict[str, Any]] = []
        for pack_id in _as_list(workspace.get("model_pack_ids")):
            if not _clean(pack_id):
                continue
            plan_payload = dict(data)
            plan_payload["pack_id"] = _clean(pack_id)
            plan_payload.setdefault("backend", workspace.get("backend"))
            plan = build_model_pack_download_plan(plan_payload)
            pack_plans.append(plan)
            if not plan.get("ok"):
                errors.extend([f"{workspace.get('id')}:{pack_id}:{error}" for error in _as_list(plan.get("errors"))])
        workspace_plans.append({
            "workspace_id": workspace.get("id"),
            "display_name": workspace.get("display_name"),
            "backend": workspace.get("backend"),
            "base_model": workspace.get("base_model"),
            "pack_plans": pack_plans,
            "summary": {
                "pack_plan_count": len(pack_plans),
                "ready_pack_plan_count": sum(1 for plan in pack_plans if plan.get("ok")),
                "attention_pack_plan_count": sum(1 for plan in pack_plans if not plan.get("ok")),
            },
        })
    ok = not errors
    return {
        "schema_id": WORKSPACE_DOWNLOAD_PLAN_SCHEMA_ID,
        "ok": ok,
        "status": "ready" if ok else "needs_attention",
        "phase": PHASE_ID,
        "workspace_plans": workspace_plans,
        "summary": {
            "workspace_plan_count": len(workspace_plans),
            "ready_workspace_plan_count": sum(1 for item in workspace_plans if item.get("summary", {}).get("attention_pack_plan_count") == 0),
            "attention_workspace_plan_count": sum(1 for item in workspace_plans if item.get("summary", {}).get("attention_pack_plan_count") != 0),
        },
        "confirmation": {
            "required": True,
            "reason": "Workspace integration only composes pack/item download plans. Phase 8 download jobs still require explicit confirmation per plan.",
        },
        "capabilities": {
            "workspace_integration": True,
            "workspace_download_planning": True,
            "actual_downloads": False,
            "starts_download_jobs": False,
        },
        "errors": errors,
        "warnings": [],
        "privacy_policy": {
            "remote_calls": False,
            "download_jobs_saved": False,
            "tokens_saved": False,
            "runtime_write_policy": "Workspace download planning composes existing pack download plans only. It does not download, create folders, save tokens, or save remote metadata.",
        },
    }


def admin_model_workspace_status_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_workspace_status(payload)


def admin_model_workspace_download_plan_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_workspace_download_plan(payload)
