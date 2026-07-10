from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from .manifest_loader import (
    load_category_map,
    load_folder_rules,
    load_model_catalog,
    load_model_catalog_schema,
    validate_loaded_manifests,
)
from .model_paths import admin_model_paths_payload, save_model_paths_payload
from .path_resolver import admin_model_resolve_target_payload
from .installed_scanner import admin_installed_models_payload, admin_scan_installed_models_payload
from .source_huggingface import admin_huggingface_metadata_payload, admin_huggingface_discover_files_payload
from .source_civitai import admin_civitai_metadata_payload, admin_civitai_discover_files_payload
from .category_normalizer import build_filter_options, normalize_records, admin_model_filter_payload
from .download_planner import admin_model_download_plan_payload
from .download_manager import (
    admin_model_download_cancel_payload,
    admin_model_download_job_payload,
    admin_model_download_jobs_payload,
    admin_model_download_start_payload,
)
from .model_packs import (
    admin_model_pack_download_plan_payload,
    admin_model_pack_status_payload,
    admin_model_packs_payload,
)
from .workspace_integration import (
    admin_model_workspace_download_plan_payload,
    admin_model_workspace_requirements_payload,
    admin_model_workspace_status_payload,
)

CATALOG_PAYLOAD_SCHEMA_ID = "neo.admin.models.catalog_payload.v1"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _record_group_key(record: dict[str, Any]) -> str:
    ui = _as_dict(record.get("ui"))
    explicit = str(ui.get("filter_group") or "").strip()
    if explicit:
        return explicit
    parts = [record.get("category"), record.get("base_model"), record.get("model_type")]
    return " / ".join(str(part or "unknown").strip().title() for part in parts)


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    domain_counts = Counter(str(item.get("category") or "unknown") for item in records)
    model_type_counts = Counter(str(item.get("model_type") or "unknown") for item in records)
    base_model_counts = Counter(str(item.get("base_model") or "unknown") for item in records)
    source_mode_counts = Counter(str(item.get("source_mode") or "unknown") for item in records)
    provider_counts = Counter(str(_as_dict(item.get("source")).get("provider") or "unknown") for item in records)
    recommended_count = sum(1 for item in records if bool(_as_dict(item.get("ui")).get("recommended")))
    dynamic_count = sum(1 for item in records if item.get("source_mode") == "discover_files")
    return {
        "record_count": len(records),
        "recommended_count": recommended_count,
        "dynamic_source_count": dynamic_count,
        "domain_counts": dict(sorted(domain_counts.items())),
        "model_type_counts": dict(sorted(model_type_counts.items())),
        "base_model_counts": dict(sorted(base_model_counts.items())),
        "source_mode_counts": dict(sorted(source_mode_counts.items())),
        "provider_counts": dict(sorted(provider_counts.items())),
    }


def _group_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[_record_group_key(record)].append(record)
    groups: list[dict[str, Any]] = []
    for group_id, items in sorted(grouped.items(), key=lambda pair: pair[0].lower()):
        groups.append({
            "group_id": group_id,
            "label": group_id,
            "count": len(items),
            "record_ids": [str(item.get("id")) for item in items],
            "categories": sorted({str(category) for item in items for category in _as_list(_as_dict(item.get("ui")).get("creative_categories")) if str(category).strip()}),
            "model_types": sorted({str(item.get("model_type") or "unknown") for item in items}),
            "base_models": sorted({str(item.get("base_model") or "unknown") for item in items}),
        })
    return groups


def _install_targets(records: list[dict[str, Any]], folder_rules: dict[str, Any]) -> list[dict[str, Any]]:
    backend_rules = _as_dict(folder_rules.get("backends"))
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in records:
        install = _as_dict(record.get("install"))
        target_type = str(install.get("target_type") or record.get("model_type") or "unknown")
        for backend in _as_list(install.get("backend_targets")):
            backend_id = str(backend or "").strip()
            if not backend_id:
                continue
            backend_map = _as_dict(backend_rules.get(backend_id))
            subdir = str(install.get(f"{backend_id}_subdir") or backend_map.get(target_type) or install.get("path_rule") or "")
            key = (backend_id, target_type, subdir)
            if key in seen:
                continue
            seen.add(key)
            rows.append({"backend": backend_id, "target_type": target_type, "subdir": subdir})
    return sorted(rows, key=lambda item: (item["backend"], item["target_type"], item["subdir"]))


def admin_model_catalog_payload() -> dict[str, Any]:
    """Return the Phase 1 read-only Admin Model Guide catalog payload."""

    catalog = load_model_catalog()
    folder_rules = load_folder_rules()
    category_map = load_category_map()
    records = [item for item in _as_list(catalog.get("records")) if isinstance(item, dict)]
    records = normalize_records(records, category_map=category_map)
    validation = validate_loaded_manifests()
    return {
        "schema_id": CATALOG_PAYLOAD_SCHEMA_ID,
        "phase": "phase10_workspace_integration",
        "status": "ready" if validation.get("ok") else "needs attention",
        "capabilities": {
            "manifest_loading": True,
            "schema_validation": True,
            "static_catalog": True,
            "grouping": True,
            "remote_metadata": True,
            "huggingface_discovery": True,
            "civitai_discovery": True,
            "category_normalization": True,
            "advanced_filtering": True,
            "download_planning": True,
            "remote_previews": True,
            "installed_scan": True,
            "path_configuration": True,
            "folder_resolution": True,
            "downloads": True,
            "actual_downloads": True,
            "model_packs": True,
            "pack_status": True,
            "pack_download_planning": True,
            "workspace_integration": True,
            "workspace_requirements": True,
            "workspace_status": True,
            "workspace_download_planning": True,
        },
        "catalog": _as_dict(catalog.get("catalog")),
        "summary": _summarize_records(records),
        "groups": _group_records(records),
        "filter_options": build_filter_options(records, category_map=category_map),
        "records": records,
        "folder_rules": folder_rules,
        "category_map": category_map,
        "install_targets": _install_targets(records, folder_rules),
        "validation": validation,
        "privacy_policy": {
            "repo_manifest_only": True,
            "stores_user_paths": False,
            "stores_tokens": False,
            "loads_remote_metadata": False,
            "saves_remote_previews": False,
            "runtime_data_policy": "User model paths are stored under neo_data/config/model_paths.json. Installed scan indexes are stored under neo_data/cache/model_installed_index.json. Download jobs are stored under neo_data/downloads/download_jobs.json. Recommended model packs and workspace requirements are public repo manifests. Hugging Face and Civitai metadata is session-only; tokens, remote metadata, and previews are not stored.",
        },
    }


def admin_model_catalog_summary_payload() -> dict[str, Any]:
    payload = admin_model_catalog_payload()
    return {
        "schema_id": "neo.admin.models.catalog_summary.v1",
        "status": payload.get("status"),
        "phase": payload.get("phase"),
        "catalog": payload.get("catalog"),
        "summary": payload.get("summary"),
        "groups": payload.get("groups"),
        "validation": payload.get("validation"),
        "endpoints": {
            "catalog": "/api/admin/models/catalog",
            "folder_rules": "/api/admin/models/folder-rules",
            "category_map": "/api/admin/models/category-map",
            "schema": "/api/admin/models/schema",
            "paths": "/api/admin/models/paths",
            "resolve_target": "/api/admin/models/resolve-target",
            "installed": "/api/admin/models/installed",
            "scan_installed": "/api/admin/models/scan-installed",
            "huggingface_metadata": "/api/admin/models/remote/huggingface/metadata",
            "huggingface_discover_files": "/api/admin/models/remote/huggingface/discover-files",
            "civitai_metadata": "/api/admin/models/remote/civitai/metadata",
            "civitai_discover_files": "/api/admin/models/remote/civitai/discover-files",
            "filter": "/api/admin/models/filter",
            "download_plan": "/api/admin/models/download/plan",
            "download_start": "/api/admin/models/download/start",
            "download_cancel": "/api/admin/models/download/cancel",
            "download_jobs": "/api/admin/models/download/jobs",
            "download_job": "/api/admin/models/download/jobs/{job_id}",
            "packs": "/api/admin/models/packs",
            "pack_status": "/api/admin/models/packs/status",
            "pack_download_plan": "/api/admin/models/packs/download/plan",
            "workspaces": "/api/admin/models/workspaces",
            "workspace_status": "/api/admin/models/workspaces/status",
            "workspace_download_plan": "/api/admin/models/workspaces/download/plan",
        },
    }


def admin_model_folder_rules_payload() -> dict[str, Any]:
    folder_rules = load_folder_rules()
    validation = validate_loaded_manifests()
    return {
        "schema_id": "neo.admin.models.folder_rules_payload.v1",
        "status": "ready" if validation.get("ok") else "needs attention",
        "folder_rules": folder_rules,
        "validation": validation,
    }


def admin_model_category_map_payload() -> dict[str, Any]:
    category_map = load_category_map()
    validation = validate_loaded_manifests()
    return {
        "schema_id": "neo.admin.models.category_map_payload.v1",
        "status": "ready" if validation.get("ok") else "needs attention",
        "category_map": category_map,
        "validation": validation,
    }


def admin_model_schema_payload() -> dict[str, Any]:
    return {
        "schema_id": "neo.admin.models.schema_payload.v1",
        "status": "ready",
        "schema": load_model_catalog_schema(),
    }


def admin_model_paths_state_payload() -> dict[str, Any]:
    return admin_model_paths_payload(create=False)


def admin_model_paths_save_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return save_model_paths_payload(payload)


def admin_model_target_resolution_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_resolve_target_payload(payload)


def admin_model_installed_state_payload() -> dict[str, Any]:
    return admin_installed_models_payload()


def admin_model_scan_installed_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_scan_installed_models_payload(payload)


def admin_model_huggingface_metadata_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_huggingface_metadata_payload(payload)


def admin_model_huggingface_discover_files_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_huggingface_discover_files_payload(payload)


def admin_model_civitai_metadata_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_civitai_metadata_payload(payload)


def admin_model_civitai_discover_files_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_civitai_discover_files_payload(payload)


def admin_model_filter_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_filter_payload(payload)


def admin_model_download_plan_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_download_plan_payload(payload)


def admin_model_download_start_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_download_start_payload(payload)


def admin_model_download_cancel_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_download_cancel_payload(payload)


def admin_model_download_jobs_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_download_jobs_payload(payload)


def admin_model_download_job_state_payload(job_id: str) -> dict[str, Any]:
    return admin_model_download_job_payload(job_id)


def admin_model_packs_state_payload() -> dict[str, Any]:
    return admin_model_packs_payload()


def admin_model_pack_status_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_pack_status_payload(payload)


def admin_model_pack_download_plan_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_pack_download_plan_payload(payload)


def admin_model_workspace_requirements_state_payload() -> dict[str, Any]:
    return admin_model_workspace_requirements_payload()


def admin_model_workspace_status_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_workspace_status_payload(payload)


def admin_model_workspace_download_plan_state_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return admin_model_workspace_download_plan_payload(payload)
