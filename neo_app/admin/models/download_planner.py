from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from .manifest_loader import load_model_catalog
from .path_resolver import resolve_model_target

DOWNLOAD_PLAN_SCHEMA_ID = "neo.admin.models.download_plan.v1"
PHASE_ID = "phase7_download_planning"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _clean_lower(value: Any) -> str:
    return _clean(value).lower()


def _filename_from_path(path: str) -> str:
    path = _clean(path).replace("\\", "/")
    return PurePosixPath(path).name


def _extension(filename: str) -> str:
    suffix = PurePosixPath(_clean(filename)).suffix
    return suffix.lower()


def _is_windows_absolute(path: str) -> bool:
    return bool(PureWindowsPath(path).drive)


def _path_separator(path: str) -> str:
    return "\\" if ("\\" in path or _is_windows_absolute(path)) else "/"


def _join_path(root: str, filename: str) -> str:
    root = _clean(root).rstrip("\\/")
    filename = _safe_filename(filename)
    if not root or not filename:
        return ""
    return f"{root}{_path_separator(root)}{filename}"


def _safe_filename(filename: str) -> str:
    name = _filename_from_path(filename)
    if name in {"", ".", ".."}:
        return ""
    if "/" in name or "\\" in name or name.startswith(".") and name in {".", ".."}:
        return ""
    return name


def _catalog_records() -> list[dict[str, Any]]:
    catalog = load_model_catalog()
    return [item for item in _as_list(catalog.get("records")) if isinstance(item, dict)]


def find_catalog_record(catalog_id: str) -> dict[str, Any] | None:
    wanted = _clean(catalog_id)
    if not wanted:
        return None
    for record in _catalog_records():
        if _clean(record.get("id")) == wanted:
            return record
    return None


def _record_from_payload(payload: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    warnings: list[str] = []
    record = _as_dict(payload.get("record")) or None
    catalog_id = _clean(payload.get("catalog_id"))
    if catalog_id:
        catalog_record = find_catalog_record(catalog_id)
        if catalog_record is None:
            warnings.append("catalog_record_not_found")
        else:
            record = catalog_record
    return record, warnings


def _variant_from_payload(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ("variant", "remote_file", "file"):
        value = _as_dict(payload.get(key))
        if value:
            return value
    return {}


def _source_for_plan(record: dict[str, Any], payload: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    record_source = _as_dict(record.get("source"))
    source = {**record_source, **_as_dict(payload.get("source")), **_as_dict(variant.get("source"))}
    provider = _clean_lower(payload.get("provider") or variant.get("provider") or source.get("provider"))
    if provider:
        source["provider"] = provider
    return source


def _file_info_for_plan(record: dict[str, Any], payload: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    source = _as_dict(record.get("source"))
    metadata = _as_dict(variant.get("metadata"))
    filename = _clean(payload.get("filename") or variant.get("filename") or source.get("filename"))
    source_path = _clean(payload.get("source_path") or payload.get("path") or variant.get("path") or source.get("filename"))
    if not filename and source_path:
        filename = _filename_from_path(source_path)
    safe_filename = _safe_filename(filename)
    return {
        "filename": safe_filename,
        "source_path": source_path or safe_filename,
        "extension": _extension(safe_filename),
        "size_bytes": variant.get("size_bytes") if isinstance(variant.get("size_bytes"), int) else payload.get("size_bytes"),
        "download_url": _clean(payload.get("download_url") or metadata.get("download_url") or variant.get("download_url")),
        "source_url": _clean(payload.get("source_url") or metadata.get("source_url") or variant.get("source_url")),
        "hashes": _as_dict(payload.get("hashes")) or _as_dict(metadata.get("hashes")),
        "provider_file_id": _clean(metadata.get("file_id") or variant.get("file_id") or payload.get("file_id")),
        "provider_version_id": _clean(metadata.get("version_id") or variant.get("version_id") or payload.get("version_id")),
    }


def _source_download_reference(provider: str, source: dict[str, Any], file_info: dict[str, Any]) -> dict[str, Any]:
    source_path = _clean(file_info.get("source_path"))
    filename = _clean(file_info.get("filename"))
    revision = _clean(source.get("revision")) or "main"
    if provider == "huggingface":
        repo = _clean(source.get("repo"))
        return {
            "provider": provider,
            "repo": repo,
            "revision": revision,
            "filename": filename,
            "source_path": source_path,
            "download_url": f"https://huggingface.co/{repo}/resolve/{revision}/{source_path}" if repo and source_path else "",
            "source_url": f"https://huggingface.co/{repo}/blob/{revision}/{source_path}" if repo and source_path else "",
            "requires_token": bool(source.get("requires_token") or source.get("gated") or source.get("private")),
        }
    if provider == "civitai":
        return {
            "provider": provider,
            "model_id": _clean(source.get("model_id")),
            "version_id": _clean(file_info.get("provider_version_id") or source.get("version_id") or source.get("model_version_id")),
            "file_id": _clean(file_info.get("provider_file_id")),
            "filename": filename,
            "source_path": source_path,
            "download_url": _clean(file_info.get("download_url")),
            "source_url": _clean(file_info.get("source_url")) or (f"https://civitai.com/models/{_clean(source.get('model_id'))}" if _clean(source.get("model_id")) else ""),
            "requires_token": bool(source.get("requires_token") or source.get("private") or source.get("requires_login")),
        }
    return {
        "provider": provider or "unknown",
        "filename": filename,
        "source_path": source_path,
        "download_url": _clean(file_info.get("download_url")),
        "source_url": _clean(file_info.get("source_url") or source.get("source_url")),
        "requires_token": bool(source.get("requires_token") or source.get("private") or source.get("gated")),
    }


def _license_warnings(record: dict[str, Any], source: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    license_info = _as_dict(record.get("license"))
    if bool(license_info.get("requires_user_review")):
        warnings.append("license_review_required")
    if source.get("gated") or source.get("private") or source.get("requires_token"):
        warnings.append("source_may_require_token_or_access_approval")
    return warnings


def _download_mode(record: dict[str, Any]) -> str:
    return _clean_lower(record.get("source_mode")) or "unknown"


def _target_type(record: dict[str, Any], payload: dict[str, Any], variant: dict[str, Any]) -> str:
    install = _as_dict(record.get("install"))
    variant_install = _as_dict(variant.get("install"))
    return _clean_lower(payload.get("target_type") or variant_install.get("target_type") or install.get("target_type") or record.get("model_type"))


def _backend_choices(record: dict[str, Any], payload: dict[str, Any], variant: dict[str, Any]) -> list[str]:
    explicit = _clean_lower(payload.get("backend") or payload.get("backend_id"))
    if explicit:
        return [explicit]
    install = _as_dict(variant.get("install")) or _as_dict(record.get("install"))
    return [_clean_lower(item) for item in _as_list(install.get("backend_targets")) if _clean(item)]


def build_download_plan(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a safe, confirmation-ready download plan without downloading anything."""

    data = _as_dict(payload)
    record, warnings = _record_from_payload(data)
    errors: list[str] = []
    if record is None:
        return {
            "schema_id": DOWNLOAD_PLAN_SCHEMA_ID,
            "ok": False,
            "status": "missing_record",
            "phase": PHASE_ID,
            "warnings": warnings,
            "errors": ["A catalog_id or record payload is required to plan a model download."],
            "capabilities": {"download_planning": True, "actual_downloads": False},
        }

    variant = _variant_from_payload(data)
    source = _source_for_plan(record, data, variant)
    provider = _clean_lower(source.get("provider"))
    source_mode = _download_mode(record)
    target_type = _target_type(record, data, variant)
    file_info = _file_info_for_plan(record, data, variant)
    filename = _clean(file_info.get("filename"))
    extension = _clean_lower(file_info.get("extension"))

    if source_mode == "manual_only" or provider == "manual":
        errors.append("This catalog record is manual-only and does not expose a downloadable source yet.")
    if source_mode == "discover_files" and not variant and not filename:
        errors.append("A discovered file variant is required for dynamic source download planning.")
    if not filename:
        errors.append("No safe filename could be resolved for this download plan.")
    if provider not in {"huggingface", "civitai", ""}:
        warnings.append(f"download_provider_not_supported_yet:{provider}")

    warnings.extend(_license_warnings(record, source))

    backends = _backend_choices(record, data, variant)
    if not backends:
        errors.append("No backend target is declared for this catalog record.")
    selected_backend = backends[0] if backends else ""
    if len(backends) > 1 and not _clean(data.get("backend") or data.get("backend_id")):
        warnings.append(f"multiple_backend_targets_available:selected_{selected_backend}")

    resolution = resolve_model_target(
        backend_id=selected_backend,
        target_type=target_type,
        catalog_id=_clean(record.get("id")),
        model_paths=_as_dict(data.get("model_paths")) or None,
    ) if selected_backend and target_type else {
        "ok": False,
        "status": "needs_path",
        "resolved_path": "",
        "allowed_extensions": [],
        "errors": ["Missing backend or target_type for folder resolution."],
        "warnings": [],
    }
    warnings.extend([str(item) for item in _as_list(resolution.get("warnings"))])
    if not bool(resolution.get("ok")):
        errors.extend([str(item) for item in _as_list(resolution.get("errors"))] or ["Target folder could not be resolved."])

    allowed_extensions = [_clean_lower(item) for item in _as_list(resolution.get("allowed_extensions"))]
    if extension and allowed_extensions and extension not in allowed_extensions:
        errors.append(f"File extension '{extension}' is not allowed for target_type '{target_type}'.")

    final_path = _join_path(_clean(resolution.get("resolved_path")), filename) if bool(resolution.get("ok")) else ""
    source_ref = _source_download_reference(provider, source, file_info)
    if provider == "huggingface" and not _clean(source_ref.get("repo")):
        errors.append("Hugging Face download planning requires a repo id.")
    if provider == "civitai" and not _clean(source_ref.get("download_url")):
        warnings.append("civitai_download_url_missing_until_file_discovery_payload_supplies_it")

    ok = not errors
    return {
        "schema_id": DOWNLOAD_PLAN_SCHEMA_ID,
        "ok": ok,
        "status": "ready" if ok else "needs_attention",
        "phase": PHASE_ID,
        "catalog_id": _clean(record.get("id")),
        "record": {
            "id": record.get("id"),
            "display_name": record.get("display_name"),
            "category": record.get("category"),
            "base_model": record.get("base_model"),
            "model_type": record.get("model_type"),
            "source_mode": record.get("source_mode"),
        },
        "file": file_info,
        "source": source_ref,
        "target": {
            "backend": selected_backend,
            "target_type": target_type,
            "folder_path": _clean(resolution.get("resolved_path")),
            "final_path": final_path,
            "allowed_extensions": allowed_extensions,
            "resolution": resolution,
        },
        "confirmation": {
            "required": True,
            "reason": "Phase 7 only creates a download plan. Phase 8 will require this plan to be confirmed before any file transfer starts.",
            "summary": f"Plan download of {filename} to {final_path}" if final_path and filename else "Download plan needs attention before it can be confirmed.",
        },
        "capabilities": {
            "download_planning": True,
            "actual_downloads": False,
            "creates_folders": False,
            "writes_model_files": False,
            "stores_tokens": False,
            "stores_remote_metadata": False,
        },
        "warnings": sorted(set(warnings)),
        "errors": errors,
        "privacy_policy": {
            "remote_metadata_saved": False,
            "remote_previews_saved": False,
            "tokens_saved": False,
            "download_jobs_saved": False,
            "actual_downloads": False,
            "runtime_write_policy": "Phase 7 is read-only planning. It may read neo_data/config/model_paths.json or an explicit model_paths payload, but it does not write model files, download jobs, tokens, previews, or remote metadata.",
        },
    }


def admin_model_download_plan_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return build_download_plan(payload)
