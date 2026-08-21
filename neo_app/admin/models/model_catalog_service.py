from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from .manifest_loader import (
    ROOT_DIR,
    load_category_map,
    load_folder_rules,
    load_model_catalog,
    load_model_catalog_schema,
    validate_loaded_manifests,
)
from .model_paths import admin_model_paths_payload, save_model_paths_payload
from .path_resolver import admin_model_resolve_target_payload
from .installed_scanner import admin_installed_models_payload, admin_scan_installed_models_payload
from .huggingface_cache import resolve_huggingface_cache
from .huggingface_snapshot_probe import repository_snapshot_catalog_status
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
REPOSITORY_SNAPSHOT_STATUS_SCHEMA_ID = "neo.admin.models.repository_snapshot_status.v1"
REPOSITORY_SNAPSHOT_STATUS_PHASE = "phase4_5_6_admin_models_installed_state_ux"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
    snapshot_count = sum(1 for item in records if item.get("source_mode") == "repository_snapshot")
    return {
        "record_count": len(records),
        "recommended_count": recommended_count,
        "dynamic_source_count": dynamic_count,
        "repository_snapshot_count": snapshot_count,
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
        if str(record.get("source_mode") or "").strip().lower() == "repository_snapshot":
            # Snapshot installs target the Hugging Face cache, not a Neo backend
            # model folder. Keep them out of file-folder target summaries.
            continue
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


def _repository_snapshot_targets(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("source_mode") or "").strip().lower() != "repository_snapshot":
            continue
        source = _as_dict(record.get("source"))
        install = _as_dict(record.get("install"))
        rows.append({
            "catalog_id": str(record.get("id") or ""),
            "display_name": str(record.get("display_name") or record.get("id") or ""),
            "category": str(record.get("category") or ""),
            "base_model": str(record.get("base_model") or ""),
            "model_type": str(record.get("model_type") or ""),
            "provider": str(source.get("provider") or ""),
            "repo": str(source.get("repo") or ""),
            "revision": str(source.get("revision") or ""),
            "strategy": str(install.get("strategy") or ""),
            "target_type": str(install.get("target_type") or ""),
            "backend_targets": [str(item) for item in _as_list(install.get("backend_targets")) if str(item).strip()],
            "probe_id": str(install.get("probe_id") or ""),
            "expected_size_mb": install.get("expected_size_mb"),
            "allow_patterns": [str(item) for item in _as_list(install.get("allow_patterns")) if str(item).strip()],
            "ignore_patterns": [str(item) for item in _as_list(install.get("ignore_patterns")) if str(item).strip()],
        })
    return rows


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
            "repository_snapshot_manifest": True,
            "huggingface_cache_resolution": True,
            "repository_snapshot_install": True,
            "huggingface_snapshot_download": True,
            "huggingface_snapshot_disk_preflight": True,
            "huggingface_snapshot_installed_probe": True,
            "repository_snapshot_content_verification": True,
            "repository_snapshot_live_status": True,
            "repository_snapshot_install_state_ux": True,
            "voice_model_lifecycle_unification": True,
            "repository_snapshot_filtered_materialization": True,
            "chatterbox_repository_snapshot_runtime_binding": True,
            "legacy_voice_model_compatibility": True,
            "no_redownload_voice_migration": True,
        },
        "catalog": _as_dict(catalog.get("catalog")),
        "summary": _summarize_records(records),
        "groups": _group_records(records),
        "filter_options": build_filter_options(records, category_map=category_map),
        "records": records,
        "folder_rules": folder_rules,
        "category_map": category_map,
        "install_targets": _install_targets(records, folder_rules),
        "repository_snapshots": _repository_snapshot_targets(records),
        "validation": validation,
        "privacy_policy": {
            "repo_manifest_only": True,
            "stores_user_paths": False,
            "stores_tokens": False,
            "loads_remote_metadata": False,
            "saves_remote_previews": False,
            "runtime_data_policy": "User model paths are stored under neo_data/config/model_paths.json. Installed scan indexes are stored under neo_data/cache/model_installed_index.json. Download jobs, including Hugging Face repository-snapshot install jobs, are stored under neo_data/downloads/download_jobs.json. Recommended model packs and workspace requirements are public repo manifests. Hugging Face and Civitai metadata is session-only; tokens, remote metadata, and previews are not stored.",
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
            "repository_snapshot_status": "/api/admin/models/repository-snapshots/status",
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


def admin_model_repository_snapshot_status_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return fresh local-only installed state for repository-snapshot records.

    Phase 4.5.6 keeps this separate from the persisted all-model Installed scan so
    opening Admin → Models can cheaply refresh Hugging Face snapshot truth without
    walking every configured ComfyUI/Forge/local-model folder.
    """

    data = _as_dict(payload)
    requested_catalog_id = _clean(data.get("catalog_id"))
    catalog = load_model_catalog()
    records = [
        item
        for item in _as_list(catalog.get("records"))
        if isinstance(item, dict) and _clean(item.get("source_mode")).lower() == "repository_snapshot"
    ]
    if requested_catalog_id:
        selected = [item for item in records if _clean(item.get("id")) == requested_catalog_id]
        if not selected:
            return {
                "schema_id": REPOSITORY_SNAPSHOT_STATUS_SCHEMA_ID,
                "phase": REPOSITORY_SNAPSHOT_STATUS_PHASE,
                "ok": False,
                "status": "not_found",
                "checked_at": _now(),
                "catalog_id": requested_catalog_id,
                "summary": {"record_count": 0},
                "rows": [],
                "warnings": [],
                "errors": ["repository_snapshot_catalog_record_not_found"],
                "policy": {"local_only": True, "remote_calls": False, "writes": False, "persists_scan": False},
            }
        records = selected

    cache = resolve_huggingface_cache(include_library_snapshot=False)
    rows: list[dict[str, Any]] = []
    for record in records:
        row = repository_snapshot_catalog_status(record, cache_resolution=cache)
        if _clean(record.get("category")).lower() == "voice":
            try:
                from neo_voice_engine.voice_model_compatibility import probe_voice_model_runtime_compatibility

                runtime = probe_voice_model_runtime_compatibility(
                    project_root=ROOT_DIR,
                    record=record,
                    cache_resolution=cache,
                )
            except Exception as exc:  # fail-soft diagnostics; HF snapshot truth remains available
                runtime = {
                    "schema_id": "neo.voice_engine.voice_model_compatibility.v1",
                    "phase": "phase4_6_1_legacy_voice_model_compatibility",
                    "catalog_id": _clean(record.get("id")),
                    "state": "unavailable",
                    "installed": False,
                    "runtime_available": False,
                    "source_kind": "",
                    "source_label": "",
                    "source_path": "",
                    "legacy_compatible": False,
                    "reason": f"runtime_probe_error:{type(exc).__name__}",
                    "message": "Voice runtime compatibility could not be checked; Hugging Face snapshot status is still authoritative for the Admin copy.",
                    "policy": {
                        "legacy_local_supported": True,
                        "legacy_migration_required": False,
                        "huggingface_copy_optional_when_legacy_ready": True,
                        "remote_calls": False,
                        "downloads": False,
                        "generation_may_download": False,
                    },
                }
            row = {**row, "runtime": runtime}
        rows.append(row)
    state_counts = Counter(_clean(row.get("overall_status")) or "unknown" for row in rows)
    runtime_rows = [_as_dict(row.get("runtime")) for row in rows if _as_dict(row.get("runtime"))]
    runtime_installed_count = sum(1 for runtime in runtime_rows if bool(runtime.get("runtime_available")))
    legacy_runtime_count = sum(1 for runtime in runtime_rows if bool(runtime.get("runtime_available")) and _clean(runtime.get("source_kind")) == "legacy_runtime_snapshot")
    hf_runtime_count = sum(1 for runtime in runtime_rows if bool(runtime.get("runtime_available")) and _clean(runtime.get("source_kind")) == "huggingface_cache_snapshot")
    return {
        "schema_id": REPOSITORY_SNAPSHOT_STATUS_SCHEMA_ID,
        "phase": REPOSITORY_SNAPSHOT_STATUS_PHASE,
        "ok": True,
        "status": "ready",
        "checked_at": _now(),
        "catalog_id": requested_catalog_id,
        "summary": {
            "record_count": len(rows),
            "installed_count": state_counts.get("installed", 0),
            "not_installed_count": state_counts.get("not_installed", 0),
            "partial_count": state_counts.get("partial", 0),
            "stale_count": state_counts.get("stale", 0),
            "corrupt_count": state_counts.get("corrupt", 0),
            "unverified_count": state_counts.get("unverified", 0),
            "state_counts": dict(sorted(state_counts.items())),
            "voice_runtime_checked_count": len(runtime_rows),
            "voice_runtime_installed_count": runtime_installed_count,
            "voice_legacy_runtime_count": legacy_runtime_count,
            "voice_huggingface_runtime_count": hf_runtime_count,
            "voice_runtime_unavailable_count": max(0, len(runtime_rows) - runtime_installed_count),
        },
        "cache": {
            "hub_cache": _clean(cache.get("hub_cache")),
            "source": _clean(cache.get("source")),
            "exists": bool(cache.get("exists")),
        },
        "rows": rows,
        "warnings": [],
        "errors": [],
        "capabilities": {
            "repository_snapshot_live_status": True,
            "authoritative_installed_probe": True,
            "local_only": True,
            "full_model_folder_scan": False,
            "persists_scan": False,
            "voice_runtime_compatibility_status": True,
            "legacy_voice_model_compatibility": True,
            "no_redownload_migration": True,
        },
        "policy": {
            "local_only": True,
            "remote_calls": False,
            "writes": False,
            "persists_scan": False,
            "receipt_is_authority": False,
            "runtime_binding": False,
        },
    }


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
