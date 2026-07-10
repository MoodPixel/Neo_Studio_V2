from __future__ import annotations

from collections import Counter
from typing import Any

from .download_planner import build_download_plan
from .installed_scanner import load_installed_index
from .manifest_loader import load_model_catalog, load_model_packs, validate_loaded_manifests
from .manifest_schema import validate_model_packs

PACKS_PAYLOAD_SCHEMA_ID = "neo.admin.models.packs_payload.v1"
PACK_STATUS_SCHEMA_ID = "neo.admin.models.pack_status.v1"
PACK_DOWNLOAD_PLAN_SCHEMA_ID = "neo.admin.models.pack_download_plan.v1"
PHASE_ID = "phase9_model_packs"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _clean_lower(value: Any) -> str:
    return _clean(value).lower()


def _catalog_records() -> list[dict[str, Any]]:
    catalog = load_model_catalog()
    return [item for item in _as_list(catalog.get("records")) if isinstance(item, dict)]


def _catalog_map() -> dict[str, dict[str, Any]]:
    return {_clean(record.get("id")): record for record in _catalog_records() if _clean(record.get("id"))}


def _pack_records() -> list[dict[str, Any]]:
    packs = load_model_packs()
    return [item for item in _as_list(packs.get("packs")) if isinstance(item, dict)]


def _pack_map() -> dict[str, dict[str, Any]]:
    return {_clean(pack.get("id")): pack for pack in _pack_records() if _clean(pack.get("id"))}


def _summarize_packs(packs: list[dict[str, Any]]) -> dict[str, Any]:
    category_counts = Counter(_clean(pack.get("category")) or "unknown" for pack in packs)
    base_counts = Counter(_clean(pack.get("base_model")) or "unknown" for pack in packs)
    backend_counts: Counter[str] = Counter()
    item_count = 0
    required_count = 0
    optional_count = 0
    variant_selection_count = 0
    for pack in packs:
        for backend in _as_list(pack.get("backend_targets")):
            backend_counts[_clean_lower(backend) or "unknown"] += 1
        for item in _as_list(pack.get("items")):
            if not isinstance(item, dict):
                continue
            item_count += 1
            if bool(item.get("required", True)):
                required_count += 1
            else:
                optional_count += 1
            if bool(item.get("requires_variant_selection")):
                variant_selection_count += 1
    return {
        "pack_count": len(packs),
        "item_count": item_count,
        "required_item_count": required_count,
        "optional_item_count": optional_count,
        "variant_selection_item_count": variant_selection_count,
        "category_counts": dict(sorted(category_counts.items())),
        "base_model_counts": dict(sorted(base_counts.items())),
        "backend_counts": dict(sorted(backend_counts.items())),
        "recommended_count": sum(1 for pack in packs if bool(pack.get("recommended"))),
    }


def _pack_preview(pack: dict[str, Any], catalog_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    items = []
    for item in _as_list(pack.get("items")):
        if not isinstance(item, dict):
            continue
        catalog_id = _clean(item.get("catalog_id"))
        record = catalog_by_id.get(catalog_id, {})
        items.append({
            "catalog_id": catalog_id,
            "label": item.get("label") or record.get("display_name") or catalog_id,
            "role": item.get("role") or "model",
            "required": bool(item.get("required", True)),
            "backend": _clean_lower(item.get("backend")) or "",
            "requires_variant_selection": bool(item.get("requires_variant_selection")),
            "record": {
                "id": record.get("id"),
                "display_name": record.get("display_name"),
                "category": record.get("category"),
                "base_model": record.get("base_model"),
                "model_type": record.get("model_type"),
                "source_mode": record.get("source_mode"),
            } if record else None,
            "notes": _as_list(item.get("notes")) if isinstance(item.get("notes"), list) else ([item.get("notes")] if item.get("notes") else []),
        })
    return {
        "id": pack.get("id"),
        "display_name": pack.get("display_name"),
        "category": pack.get("category"),
        "base_model": pack.get("base_model"),
        "description": pack.get("description"),
        "backend_targets": _as_list(pack.get("backend_targets")),
        "recommended": bool(pack.get("recommended")),
        "tags": _as_list(pack.get("tags")),
        "items": items,
        "item_count": len(items),
        "required_item_count": sum(1 for item in items if item.get("required")),
        "optional_item_count": sum(1 for item in items if not item.get("required")),
    }


def admin_model_packs_payload() -> dict[str, Any]:
    packs_manifest = load_model_packs()
    packs = _pack_records()
    catalog_by_id = _catalog_map()
    validation = validate_loaded_manifests()
    pack_validation = validate_model_packs(packs_manifest, catalog=load_model_catalog())
    return {
        "schema_id": PACKS_PAYLOAD_SCHEMA_ID,
        "phase": PHASE_ID,
        "status": "ready" if validation.get("ok") and pack_validation.ok else "needs attention",
        "packs_manifest": {
            "schema_id": packs_manifest.get("schema_id"),
            "version": packs_manifest.get("version"),
            "updated_at": packs_manifest.get("updated_at"),
            "description": packs_manifest.get("description"),
        },
        "summary": _summarize_packs(packs),
        "packs": [_pack_preview(pack, catalog_by_id) for pack in packs],
        "validation": {
            "ok": validation.get("ok") and pack_validation.ok,
            "errors": list(validation.get("errors") or []) + list(pack_validation.errors),
            "warnings": list(validation.get("warnings") or []) + list(pack_validation.warnings),
        },
        "capabilities": {
            "model_packs": True,
            "pack_status": True,
            "pack_download_planning": True,
            "actual_downloads": False,
            "workspace_integration": True,
        },
        "privacy_policy": {
            "repo_manifest_only": True,
            "stores_user_paths": False,
            "stores_tokens": False,
            "stores_remote_metadata": False,
            "saves_remote_previews": False,
            "runtime_data_policy": "Pack manifests are public repo metadata. Pack status may read the local installed scan index from neo_data/cache, but packs do not write user paths, tokens, previews, or remote metadata.",
        },
    }


def _installed_status_by_catalog(payload: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    source = _as_dict(payload)
    scan = _as_dict(source.get("scan")) or _as_dict(source.get("installed_scan"))
    if not scan:
        scan = _as_dict(load_installed_index())
    result: dict[str, dict[str, Any]] = {}
    for item in _as_list(scan.get("catalog_status")):
        if isinstance(item, dict) and _clean(item.get("catalog_id")):
            result[_clean(item.get("catalog_id"))] = item
    return result


def _requested_packs(payload: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = _as_dict(payload)
    packs_by_id = _pack_map()
    pack_id = _clean(data.get("pack_id"))
    if pack_id:
        pack = packs_by_id.get(pack_id)
        return [pack] if pack else []
    pack_ids = [_clean(item) for item in _as_list(data.get("pack_ids")) if _clean(item)]
    if pack_ids:
        return [packs_by_id[item] for item in pack_ids if item in packs_by_id]
    return list(packs_by_id.values())


def build_model_pack_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    requested = _requested_packs(data)
    if (data.get("pack_id") or data.get("pack_ids")) and not requested:
        return {
            "schema_id": PACK_STATUS_SCHEMA_ID,
            "ok": False,
            "status": "pack_not_found",
            "phase": PHASE_ID,
            "packs": [],
            "errors": ["Requested model pack was not found."],
            "warnings": [],
        }
    catalog_by_id = _catalog_map()
    installed_by_id = _installed_status_by_catalog(data)
    has_scan = bool(installed_by_id)
    pack_rows: list[dict[str, Any]] = []
    for pack in requested:
        item_rows: list[dict[str, Any]] = []
        required_problem_count = 0
        required_installed_count = 0
        required_total = 0
        optional_problem_count = 0
        for item in _as_list(pack.get("items")):
            if not isinstance(item, dict):
                continue
            catalog_id = _clean(item.get("catalog_id"))
            record = catalog_by_id.get(catalog_id, {})
            installed = installed_by_id.get(catalog_id, {})
            local_status = _clean(installed.get("overall_status")) or ("not_scanned" if not has_scan else "unknown")
            required = bool(item.get("required", True))
            if required:
                required_total += 1
            if local_status == "installed":
                readiness = "installed"
                if required:
                    required_installed_count += 1
            elif bool(item.get("requires_variant_selection")) and local_status != "installed":
                readiness = "needs_variant_selection"
            elif local_status == "local_candidates":
                readiness = "needs_review"
            elif local_status == "missing":
                readiness = "missing"
            else:
                readiness = "unknown" if has_scan else "not_scanned"
            if required and readiness != "installed":
                required_problem_count += 1
            if not required and readiness not in {"installed", "not_scanned"}:
                optional_problem_count += 1
            item_rows.append({
                "catalog_id": catalog_id,
                "label": item.get("label") or record.get("display_name") or catalog_id,
                "role": item.get("role") or "model",
                "required": required,
                "backend": _clean_lower(item.get("backend")),
                "requires_variant_selection": bool(item.get("requires_variant_selection")),
                "readiness": readiness,
                "local_status": local_status,
                "record": {
                    "id": record.get("id"),
                    "display_name": record.get("display_name"),
                    "category": record.get("category"),
                    "base_model": record.get("base_model"),
                    "model_type": record.get("model_type"),
                    "source_mode": record.get("source_mode"),
                } if record else None,
                "installed_status": installed or None,
            })
        if not has_scan:
            overall = "not_scanned"
        elif required_total and required_installed_count == required_total:
            overall = "complete"
        elif required_problem_count:
            overall = "missing_required"
        elif optional_problem_count:
            overall = "complete_with_optional_attention"
        else:
            overall = "ready"
        pack_rows.append({
            "pack_id": pack.get("id"),
            "display_name": pack.get("display_name"),
            "category": pack.get("category"),
            "base_model": pack.get("base_model"),
            "backend_targets": _as_list(pack.get("backend_targets")),
            "recommended": bool(pack.get("recommended")),
            "overall_status": overall,
            "summary": {
                "required_total": required_total,
                "required_installed_count": required_installed_count,
                "required_problem_count": required_problem_count,
                "optional_problem_count": optional_problem_count,
                "has_installed_scan": has_scan,
            },
            "items": item_rows,
        })
    return {
        "schema_id": PACK_STATUS_SCHEMA_ID,
        "ok": True,
        "status": "ready",
        "phase": PHASE_ID,
        "has_installed_scan": has_scan,
        "packs": pack_rows,
        "summary": {
            "pack_count": len(pack_rows),
            "complete_count": sum(1 for item in pack_rows if item.get("overall_status") == "complete"),
            "missing_required_count": sum(1 for item in pack_rows if item.get("overall_status") == "missing_required"),
            "not_scanned_count": sum(1 for item in pack_rows if item.get("overall_status") == "not_scanned"),
        },
        "errors": [],
        "warnings": [] if has_scan else ["installed_scan_not_available"],
        "privacy_policy": {
            "remote_calls": False,
            "stores_user_paths": False,
            "stores_tokens": False,
            "runtime_read_policy": "Reads neo_data/cache/model_installed_index.json only when available, or uses an explicit scan payload.",
        },
    }


def _variant_payload_for_item(data: dict[str, Any], catalog_id: str) -> dict[str, Any]:
    variants = _as_dict(data.get("variants"))
    item_overrides = _as_dict(data.get("item_overrides"))
    direct = _as_dict(variants.get(catalog_id)) or _as_dict(item_overrides.get(catalog_id))
    if direct and any(key in direct for key in ("variant", "file", "remote_file")):
        return direct
    if direct:
        return {"variant": direct}
    return {}


def build_model_pack_download_plan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    pack_id = _clean(data.get("pack_id"))
    pack = _pack_map().get(pack_id)
    if not pack:
        return {
            "schema_id": PACK_DOWNLOAD_PLAN_SCHEMA_ID,
            "ok": False,
            "status": "pack_not_found",
            "phase": PHASE_ID,
            "pack_id": pack_id,
            "item_plans": [],
            "errors": ["A valid pack_id is required to build a pack download plan."],
            "warnings": [],
        }

    include_optional = bool(data.get("include_optional", False))
    backend = _clean_lower(data.get("backend") or data.get("backend_id"))
    model_paths = _as_dict(data.get("model_paths")) or None
    source_overrides = _as_dict(data.get("sources"))
    item_plans: list[dict[str, Any]] = []
    required_errors: list[str] = []
    warnings: list[str] = []
    skipped_optional = 0

    for item in _as_list(pack.get("items")):
        if not isinstance(item, dict):
            continue
        required = bool(item.get("required", True))
        if not required and not include_optional:
            skipped_optional += 1
            continue
        catalog_id = _clean(item.get("catalog_id"))
        plan_payload: dict[str, Any] = {
            "catalog_id": catalog_id,
            "backend": backend or _clean_lower(item.get("backend")),
        }
        if model_paths is not None:
            plan_payload["model_paths"] = model_paths
        source_override = _as_dict(source_overrides.get(catalog_id))
        if source_override:
            plan_payload["source"] = source_override
        variant_payload = _variant_payload_for_item(data, catalog_id)
        plan_payload.update(variant_payload)
        if bool(item.get("requires_variant_selection")) and not any(key in plan_payload for key in ("variant", "file", "remote_file", "filename")):
            item_plan = {
                "schema_id": "neo.admin.models.pack_item_download_plan.v1",
                "ok": False,
                "status": "needs_variant_selection",
                "catalog_id": catalog_id,
                "label": item.get("label") or catalog_id,
                "required": required,
                "errors": ["This pack item requires a selected discovered file variant before download planning."],
                "warnings": [],
                "plan": None,
            }
        else:
            plan = build_download_plan(plan_payload)
            item_plan = {
                "schema_id": "neo.admin.models.pack_item_download_plan.v1",
                "ok": bool(plan.get("ok")),
                "status": plan.get("status"),
                "catalog_id": catalog_id,
                "label": item.get("label") or catalog_id,
                "required": required,
                "errors": list(plan.get("errors") or []),
                "warnings": list(plan.get("warnings") or []),
                "plan": plan,
            }
        if required and not item_plan["ok"]:
            required_errors.append(f"{catalog_id}:" + ";".join(item_plan.get("errors") or [str(item_plan.get("status"))]))
        if not required and item_plan.get("warnings"):
            warnings.extend([f"{catalog_id}:{warning}" for warning in item_plan.get("warnings") or []])
        item_plans.append(item_plan)

    ok = not required_errors
    return {
        "schema_id": PACK_DOWNLOAD_PLAN_SCHEMA_ID,
        "ok": ok,
        "status": "ready" if ok else "needs_attention",
        "phase": PHASE_ID,
        "pack_id": pack_id,
        "display_name": pack.get("display_name"),
        "backend": backend,
        "include_optional": include_optional,
        "item_plans": item_plans,
        "summary": {
            "planned_item_count": len(item_plans),
            "ready_item_count": sum(1 for item in item_plans if item.get("ok")),
            "required_error_count": len(required_errors),
            "skipped_optional_count": skipped_optional,
        },
        "confirmation": {
            "required": True,
            "reason": "Pack download planning only prepares item download plans. Phase 8 download jobs still require explicit confirmation per plan before any file transfer starts.",
        },
        "capabilities": {
            "model_packs": True,
            "pack_download_planning": True,
            "actual_downloads": False,
            "starts_download_jobs": False,
            "stores_tokens": False,
            "stores_remote_metadata": False,
        },
        "errors": required_errors,
        "warnings": warnings,
        "privacy_policy": {
            "remote_calls": False,
            "download_jobs_saved": False,
            "tokens_saved": False,
            "runtime_write_policy": "Pack download planning composes Phase 7 download plans only. It does not download, create folders, write model files, save tokens, or save remote metadata.",
        },
    }


def admin_model_pack_status_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_model_pack_status(payload)


def admin_model_pack_download_plan_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_model_pack_download_plan(payload)
