from __future__ import annotations

from typing import Any
from urllib import error, parse, request
import json
import socket

from .file_discovery import discover_remote_files_for_record
from .manifest_loader import load_model_catalog

CIVITAI_API_BASE = "https://civitai.com/api/v1"
CIVITAI_WEB_BASE = "https://civitai.com"
CIVITAI_PROVIDER = "civitai"
DEFAULT_TIMEOUT_SECONDS = 20
CIVITAI_METADATA_SCHEMA_ID = "neo.admin.models.civitai.metadata.v1"
CIVITAI_DISCOVERY_SCHEMA_ID = "neo.admin.models.civitai.discovery.v1"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _record_from_payload(payload: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[str]]:
    data = _as_dict(payload)
    warnings: list[str] = []
    record = _as_dict(data.get("record")) or None
    catalog_id = _clean(data.get("catalog_id"))
    if catalog_id:
        catalog_record = find_catalog_record(catalog_id)
        if catalog_record is None:
            warnings.append("catalog_record_not_found")
        else:
            record = catalog_record
    if record is None and _as_dict(data.get("source")):
        record = {
            "id": _clean(data.get("catalog_id")) or "ad_hoc_civitai_source",
            "display_name": _clean(data.get("display_name")) or "Ad hoc Civitai Source",
            "category": _clean(data.get("category")) or "image",
            "base_model": _clean(data.get("base_model")) or "unknown",
            "model_type": _clean(data.get("model_type")) or "unknown",
            "source_mode": "discover_files",
            "source": _as_dict(data.get("source")),
            "file_rules": _as_dict(data.get("file_rules")),
            "install": _as_dict(data.get("install")),
            "ui": _as_dict(data.get("ui")),
        }
    return record, warnings


def _source_from_payload(payload: dict[str, Any] | None, record: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    source = {**_as_dict(record.get("source") if record else {}), **_as_dict(data.get("source"))}
    model_id = _clean(data.get("model_id")) or _clean(data.get("civitai_model_id")) or _clean(source.get("model_id"))
    version_id = _clean(data.get("version_id")) or _clean(data.get("model_version_id")) or _clean(source.get("version_id")) or _clean(source.get("model_version_id"))
    return {**source, "provider": CIVITAI_PROVIDER, "model_id": model_id, "version_id": version_id}


def _safe_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return min(max(timeout, 3), 60)


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _size_bytes_from_file(file_row: dict[str, Any]) -> int | None:
    for key in ("sizeBytes", "size_bytes", "size"):
        value = _safe_int(file_row.get(key))
        if value is not None and value >= 0:
            return value
    size_kb = _safe_int(file_row.get("sizeKB"))
    if size_kb is not None and size_kb >= 0:
        return size_kb * 1024
    metadata = _as_dict(file_row.get("metadata"))
    for key in ("sizeBytes", "size_bytes", "size"):
        value = _safe_int(metadata.get(key))
        if value is not None and value >= 0:
            return value
    return None


def _request_json(url: str, *, token: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any] | list[Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Neo-Studio-Model-Guide/phase5",
    }
    if token:
        # Civitai auth handling has changed over time. Keep the token request-scoped and try common header forms.
        headers["Authorization"] = f"Bearer {token}"
        headers["X-API-Key"] = token
    req = request.Request(url, headers=headers)
    with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - user-triggered model metadata lookup
        body = response.read().decode("utf-8")
    if not body.strip():
        return {}
    return json.loads(body)


def _civitai_model_url(model_id: str) -> str:
    safe_model_id = parse.quote(_clean(model_id), safe="")
    return f"{CIVITAI_API_BASE}/models/{safe_model_id}"


def _civitai_version_url(version_id: str) -> str:
    safe_version_id = parse.quote(_clean(version_id), safe="")
    return f"{CIVITAI_API_BASE}/model-versions/{safe_version_id}"


def _civitai_web_model_url(model_id: str, version_id: str = "") -> str:
    params = {"modelVersionId": version_id} if _clean(version_id) else {}
    suffix = f"?{parse.urlencode(params)}" if params else ""
    return f"{CIVITAI_WEB_BASE}/models/{parse.quote(_clean(model_id), safe='')}{suffix}"


def _error_payload(schema_id: str, status: str, message: str, *, warnings: list[str] | None = None, model_id: str = "", version_id: str = "", source: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_id": schema_id,
        "ok": False,
        "status": status,
        "provider": CIVITAI_PROVIDER,
        "model_id": model_id,
        "version_id": version_id,
        "source": _as_dict(source),
        "warnings": warnings or [],
        "errors": [message],
        "privacy_policy": {
            "remote_metadata_saved": False,
            "remote_previews_saved": False,
            "tokens_saved": False,
            "downloads": False,
        },
    }


def _normalize_exception(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, error.HTTPError):
        if exc.code == 401:
            return "authentication_required", "Civitai returned 401. A token may be required for this model or version."
        if exc.code == 403:
            return "access_denied", "Civitai returned 403. The model may require login, access approval, or an API token."
        if exc.code == 404:
            return "not_found", "Civitai model or version was not found."
        return "http_error", f"Civitai returned HTTP {exc.code}."
    if isinstance(exc, error.URLError):
        return "network_error", "Could not reach Civitai. Check internet access or try again later."
    if isinstance(exc, socket.timeout):
        return "timeout", "Civitai request timed out."
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_response", "Civitai returned a response that was not valid JSON."
    return "remote_error", f"Civitai metadata request failed: {type(exc).__name__}"


def _preview_urls_from_version(version: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for image in _as_list(version.get("images")):
        if not isinstance(image, dict):
            continue
        url = _clean(image.get("url"))
        if url.startswith(("http://", "https://")) and url not in urls:
            urls.append(url)
    return urls


def _normalize_version(version: dict[str, Any], *, model_id: str = "") -> dict[str, Any]:
    version_id = _clean(version.get("id"))
    files = [item for item in _as_list(version.get("files")) if isinstance(item, dict)]
    images = [item for item in _as_list(version.get("images")) if isinstance(item, dict)]
    base_model = _clean(version.get("baseModel")) or _clean(version.get("base_model"))
    return {
        "id": version_id,
        "name": _clean(version.get("name")),
        "base_model": base_model,
        "base_model_type": _clean(version.get("baseModelType")) or _clean(version.get("base_model_type")),
        "published_at": _clean(version.get("publishedAt")) or _clean(version.get("published_at")),
        "updated_at": _clean(version.get("updatedAt")) or _clean(version.get("updated_at")),
        "availability": _clean(version.get("availability")),
        "trained_words": [str(item) for item in _as_list(version.get("trainedWords")) if str(item).strip()],
        "file_count": len(files),
        "image_count": len(images),
        "preview_urls": _preview_urls_from_version(version),
        "source_url": _civitai_web_model_url(model_id, version_id) if model_id else "",
    }


def _metadata_from_model_info(info: dict[str, Any], *, source: dict[str, Any], selected_version: dict[str, Any] | None = None) -> dict[str, Any]:
    model_id = _clean(info.get("id")) or _clean(source.get("model_id"))
    versions = [_normalize_version(item, model_id=model_id) for item in _as_list(info.get("modelVersions")) if isinstance(item, dict)]
    tags = [str(item) for item in _as_list(info.get("tags")) if str(item).strip()]
    stats = _as_dict(info.get("stats"))
    creator = _as_dict(info.get("creator"))
    preview_urls: list[str] = []
    selected = _as_dict(selected_version)
    if selected:
        preview_urls.extend(_preview_urls_from_version(selected))
    for version in _as_list(info.get("modelVersions")):
        if not isinstance(version, dict):
            continue
        for url in _preview_urls_from_version(version):
            if url not in preview_urls:
                preview_urls.append(url)
    return {
        "schema_id": CIVITAI_METADATA_SCHEMA_ID,
        "ok": True,
        "status": "ready",
        "provider": CIVITAI_PROVIDER,
        "model_id": model_id,
        "version_id": _clean(source.get("version_id")),
        "source": source,
        "metadata": {
            "id": model_id,
            "name": _clean(info.get("name")),
            "description": _clean(info.get("description")),
            "type": _clean(info.get("type")),
            "nsfw": bool(info.get("nsfw")),
            "poi": bool(info.get("poi")),
            "mode": _clean(info.get("mode")),
            "tags": tags,
            "creator": {
                "username": _clean(creator.get("username")) or _clean(creator.get("name")),
                "image": _clean(creator.get("image")),
            },
            "stats": stats,
            "downloads": stats.get("downloadCount") or stats.get("downloads"),
            "likes": stats.get("favoriteCount") or stats.get("thumbsUpCount") or stats.get("likes"),
            "version_count": len(versions),
            "base_models": sorted({item.get("base_model") for item in versions if item.get("base_model")}),
            "preview_urls": preview_urls,
            "source_url": _civitai_web_model_url(model_id, _clean(source.get("version_id"))) if model_id else "",
        },
        "versions": versions,
        "selected_version": _normalize_version(selected, model_id=model_id) if selected else {},
        "privacy_policy": {
            "remote_metadata_saved": False,
            "remote_previews_saved": False,
            "tokens_saved": False,
            "downloads": False,
        },
    }


def _metadata_from_version_info(version: dict[str, Any], *, source: dict[str, Any]) -> dict[str, Any]:
    model = _as_dict(version.get("model"))
    model_id = _clean(model.get("id")) or _clean(source.get("model_id"))
    version_id = _clean(version.get("id")) or _clean(source.get("version_id"))
    source = {**source, "model_id": model_id, "version_id": version_id}
    pseudo_model = {
        "id": model_id,
        "name": _clean(model.get("name")),
        "description": _clean(model.get("description")),
        "type": _clean(model.get("type")),
        "nsfw": bool(model.get("nsfw")),
        "poi": bool(model.get("poi")),
        "mode": _clean(model.get("mode")),
        "tags": _as_list(model.get("tags")),
        "creator": _as_dict(model.get("creator")),
        "stats": _as_dict(model.get("stats")),
        "modelVersions": [version],
    }
    return _metadata_from_model_info(pseudo_model, source=source, selected_version=version)


def _version_matches(version: dict[str, Any], version_id: str) -> bool:
    return bool(_clean(version_id) and _clean(version.get("id")) == _clean(version_id))


def _select_versions(info: dict[str, Any], *, version_id: str = "") -> list[dict[str, Any]]:
    versions = [item for item in _as_list(info.get("modelVersions")) if isinstance(item, dict)]
    if version_id:
        matches = [item for item in versions if _version_matches(item, version_id)]
        return matches or []
    return versions


def _rows_from_civitai_versions(versions: list[dict[str, Any]], *, model_id: str = "") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for version in versions:
        version_id = _clean(version.get("id"))
        source_url = _civitai_web_model_url(model_id, version_id) if model_id else ""
        base_model = _clean(version.get("baseModel")) or _clean(version.get("base_model"))
        preview_urls = _preview_urls_from_version(version)
        for file_row in _as_list(version.get("files")):
            if not isinstance(file_row, dict):
                continue
            name = _clean(file_row.get("name")) or _clean(file_row.get("filename"))
            if not name:
                continue
            rows.append({
                "path": name,
                "name": name,
                "type": "file",
                "size": _size_bytes_from_file(file_row),
                "download_url": _clean(file_row.get("downloadUrl")) or _clean(file_row.get("download_url")),
                "source_url": source_url,
                "file_id": file_row.get("id"),
                "version_id": version_id,
                "version_name": _clean(version.get("name")),
                "base_model": base_model,
                "primary": bool(file_row.get("primary")),
                "civitai_file_type": _clean(file_row.get("type")),
                "hashes": _as_dict(file_row.get("hashes")),
                "pickle_scan_result": _clean(file_row.get("pickleScanResult")),
                "virus_scan_result": _clean(file_row.get("virusScanResult")),
                "metadata": {
                    "provider": CIVITAI_PROVIDER,
                    "file_metadata": _as_dict(file_row.get("metadata")),
                    "preview_urls": preview_urls,
                },
            })
    return rows


def fetch_civitai_model(*, model_id: str, token: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    response = _request_json(_civitai_model_url(model_id), token=token, timeout=timeout)
    return _as_dict(response)


def fetch_civitai_version(*, version_id: str, token: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    response = _request_json(_civitai_version_url(version_id), token=token, timeout=timeout)
    return _as_dict(response)


def admin_civitai_metadata_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    record, warnings = _record_from_payload(data)
    source = _source_from_payload(data, record)
    model_id = _clean(source.get("model_id"))
    version_id = _clean(source.get("version_id"))
    token = _clean(data.get("token"))
    timeout = _safe_timeout(data.get("timeout_seconds"))
    offline_model = _as_dict(data.get("offline_model_info")) or _as_dict(data.get("offline_model"))
    offline_version = _as_dict(data.get("offline_version_info")) or _as_dict(data.get("offline_version"))
    if not model_id and not version_id and not offline_model and not offline_version:
        return _error_payload(CIVITAI_METADATA_SCHEMA_ID, "missing_source_id", "A Civitai model_id or version_id is required.", warnings=warnings, source=source)
    if offline_version:
        return {**_metadata_from_version_info(offline_version, source=source), "warnings": warnings, "mode": "offline_payload"}
    if offline_model:
        selected_versions = _select_versions(offline_model, version_id=version_id)
        selected = selected_versions[0] if selected_versions else None
        return {**_metadata_from_model_info(offline_model, source=source, selected_version=selected), "warnings": warnings, "mode": "offline_payload"}
    try:
        if version_id and not model_id:
            version = fetch_civitai_version(version_id=version_id, token=token, timeout=timeout)
            return {**_metadata_from_version_info(version, source=source), "warnings": warnings, "mode": "remote"}
        model = fetch_civitai_model(model_id=model_id, token=token, timeout=timeout)
    except Exception as exc:  # pragma: no cover - network behavior is environment dependent
        status, message = _normalize_exception(exc)
        return _error_payload(CIVITAI_METADATA_SCHEMA_ID, status, message, warnings=warnings, model_id=model_id, version_id=version_id, source=source)
    selected_versions = _select_versions(model, version_id=version_id)
    selected = selected_versions[0] if selected_versions else None
    return {**_metadata_from_model_info(model, source=source, selected_version=selected), "warnings": warnings, "mode": "remote"}


def admin_civitai_discover_files_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    record, warnings = _record_from_payload(data)
    if record is None:
        return _error_payload(CIVITAI_DISCOVERY_SCHEMA_ID, "missing_record", "A catalog_id, record, or source payload is required.", warnings=warnings)
    source = _source_from_payload(data, record)
    model_id = _clean(source.get("model_id"))
    version_id = _clean(source.get("version_id"))
    token = _clean(data.get("token"))
    timeout = _safe_timeout(data.get("timeout_seconds"))
    if _clean(_as_dict(record.get("source")).get("provider")).lower() not in {"", CIVITAI_PROVIDER} and not _as_dict(data.get("source")):
        return _error_payload(CIVITAI_DISCOVERY_SCHEMA_ID, "provider_mismatch", "The selected catalog record is not a Civitai source.", warnings=warnings, model_id=model_id, version_id=version_id, source=source)
    offline_rows = _as_list(data.get("offline_files"))
    offline_model = _as_dict(data.get("offline_model_info")) or _as_dict(data.get("offline_model"))
    offline_version = _as_dict(data.get("offline_version_info")) or _as_dict(data.get("offline_version"))
    if offline_rows:
        rows = [item for item in offline_rows if isinstance(item, dict)]
        mode = "offline_payload"
    elif offline_version:
        rows = _rows_from_civitai_versions([offline_version], model_id=model_id)
        mode = "offline_payload"
    elif offline_model:
        versions = _select_versions(offline_model, version_id=version_id)
        if version_id and not versions:
            return _error_payload(CIVITAI_DISCOVERY_SCHEMA_ID, "version_not_found", "The requested Civitai version was not present in the provided model payload.", warnings=warnings, model_id=model_id, version_id=version_id, source=source)
        rows = _rows_from_civitai_versions(versions, model_id=_clean(offline_model.get("id")) or model_id)
        mode = "offline_payload"
    else:
        if not model_id and not version_id:
            return _error_payload(CIVITAI_DISCOVERY_SCHEMA_ID, "missing_source_id", "A Civitai model_id or version_id is required for file discovery.", warnings=warnings, source=source)
        try:
            if version_id and not model_id:
                version = fetch_civitai_version(version_id=version_id, token=token, timeout=timeout)
                rows = _rows_from_civitai_versions([version], model_id=model_id)
            else:
                model = fetch_civitai_model(model_id=model_id, token=token, timeout=timeout)
                versions = _select_versions(model, version_id=version_id)
                if version_id and not versions:
                    return _error_payload(CIVITAI_DISCOVERY_SCHEMA_ID, "version_not_found", "The requested Civitai version was not present in the model payload.", warnings=warnings, model_id=model_id, version_id=version_id, source=source)
                rows = _rows_from_civitai_versions(versions, model_id=model_id)
            mode = "remote"
        except Exception as exc:  # pragma: no cover - network behavior is environment dependent
            status, message = _normalize_exception(exc)
            return _error_payload(CIVITAI_DISCOVERY_SCHEMA_ID, status, message, warnings=warnings, model_id=model_id, version_id=version_id, source=source)
    discovery = discover_remote_files_for_record(record, rows, provider=CIVITAI_PROVIDER)
    return {
        **discovery,
        "schema_id": CIVITAI_DISCOVERY_SCHEMA_ID,
        "ok": True,
        "status": "ready",
        "mode": mode,
        "model_id": model_id,
        "version_id": version_id,
        "source": source,
        "warnings": warnings,
        "capabilities": {
            "remote_metadata": True,
            "file_discovery": True,
            "remote_previews": True,
            "downloads": False,
            "tokens_saved": False,
            "metadata_saved": False,
        },
    }
