from __future__ import annotations

from typing import Any
from urllib import error, parse, request
import json
import socket

from .file_discovery import discover_remote_files_for_record
from .manifest_loader import load_model_catalog

HF_API_BASE = "https://huggingface.co/api/models"
HF_PROVIDER = "huggingface"
DEFAULT_TIMEOUT_SECONDS = 20
HF_METADATA_SCHEMA_ID = "neo.admin.models.huggingface.metadata.v1"
HF_DISCOVERY_SCHEMA_ID = "neo.admin.models.huggingface.discovery.v1"


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
            "id": _clean(data.get("catalog_id")) or "ad_hoc_huggingface_source",
            "display_name": _clean(data.get("display_name")) or "Ad hoc Hugging Face Source",
            "category": _clean(data.get("category")) or "utility",
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
    repo = _clean(data.get("repo")) or _clean(source.get("repo")) or _clean(source.get("repo_id"))
    revision = _clean(data.get("revision")) or _clean(source.get("revision")) or "main"
    path = _clean(data.get("path")) or _clean(source.get("path"))
    return {**source, "provider": HF_PROVIDER, "repo": repo, "revision": revision, "path": path}


def _safe_timeout(value: Any) -> int:
    try:
        timeout = int(value)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT_SECONDS
    return min(max(timeout, 3), 60)


def _request_json(url: str, *, token: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any] | list[Any]:
    headers = {
        "Accept": "application/json",
        "User-Agent": "Neo-Studio-Model-Guide/phase4",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers)
    with request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - user-triggered model metadata lookup
        body = response.read().decode("utf-8")
    if not body.strip():
        return {}
    return json.loads(body)


def _hf_model_url(repo: str, *, revision: str = "main") -> str:
    repo_path = parse.quote(repo.strip("/"), safe="/")
    params = {"revision": revision or "main"}
    return f"{HF_API_BASE}/{repo_path}?{parse.urlencode(params)}"


def _hf_tree_url(repo: str, *, revision: str = "main", path: str = "", recursive: bool = True, expand: bool = True) -> str:
    repo_path = parse.quote(repo.strip("/"), safe="/")
    rev = parse.quote(revision or "main", safe="")
    clean_path = _clean(path).strip("/")
    suffix = f"/{parse.quote(clean_path, safe='/')}" if clean_path else ""
    params = {"recursive": "1" if recursive else "0", "expand": "1" if expand else "0"}
    return f"{HF_API_BASE}/{repo_path}/tree/{rev}{suffix}?{parse.urlencode(params)}"


def _error_payload(schema_id: str, status: str, message: str, *, warnings: list[str] | None = None, repo: str = "", source: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "schema_id": schema_id,
        "ok": False,
        "status": status,
        "provider": HF_PROVIDER,
        "repo": repo,
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
            return "authentication_required", "Hugging Face returned 401. A token may be required for this repository."
        if exc.code == 403:
            return "access_denied", "Hugging Face returned 403. The repository may be gated or private."
        if exc.code == 404:
            return "not_found", "Hugging Face repository or path was not found."
        return "http_error", f"Hugging Face returned HTTP {exc.code}."
    if isinstance(exc, error.URLError):
        return "network_error", "Could not reach Hugging Face. Check internet access or try again later."
    if isinstance(exc, socket.timeout):
        return "timeout", "Hugging Face request timed out."
    if isinstance(exc, json.JSONDecodeError):
        return "invalid_response", "Hugging Face returned a response that was not valid JSON."
    return "remote_error", f"Hugging Face metadata request failed: {type(exc).__name__}"


def _metadata_from_model_info(info: dict[str, Any], *, repo: str, source: dict[str, Any]) -> dict[str, Any]:
    siblings = _as_list(info.get("siblings"))
    tags = [str(item) for item in _as_list(info.get("tags")) if str(item).strip()]
    card = _as_dict(info.get("cardData"))
    preview_candidates: list[str] = []
    for key in ("thumbnail", "image", "preview", "widgetData"):
        value = card.get(key) or info.get(key)
        if isinstance(value, str) and value.startswith(("http://", "https://")):
            preview_candidates.append(value)
    return {
        "schema_id": HF_METADATA_SCHEMA_ID,
        "ok": True,
        "status": "ready",
        "provider": HF_PROVIDER,
        "repo": repo,
        "source": source,
        "metadata": {
            "id": info.get("id") or repo,
            "name": info.get("id") or repo,
            "description": card.get("description") or card.get("summary") or info.get("description") or "",
            "source_url": f"https://huggingface.co/{repo}",
            "sha": info.get("sha") or "",
            "last_modified": info.get("lastModified") or info.get("last_modified") or "",
            "private": bool(info.get("private")),
            "gated": info.get("gated", False),
            "disabled": bool(info.get("disabled")),
            "pipeline_tag": info.get("pipeline_tag") or "",
            "library_name": info.get("library_name") or "",
            "downloads": info.get("downloads"),
            "likes": info.get("likes"),
            "tags": tags,
            "card_data": card,
            "siblings_count": len(siblings),
            "preview_urls": preview_candidates,
        },
        "siblings": siblings,
        "privacy_policy": {
            "remote_metadata_saved": False,
            "remote_previews_saved": False,
            "tokens_saved": False,
            "downloads": False,
        },
    }


def fetch_huggingface_metadata(*, repo: str, revision: str = "main", token: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> dict[str, Any]:
    url = _hf_model_url(repo, revision=revision)
    response = _request_json(url, token=token, timeout=timeout)
    return _as_dict(response)


def fetch_huggingface_tree(*, repo: str, revision: str = "main", path: str = "", token: str = "", timeout: int = DEFAULT_TIMEOUT_SECONDS) -> list[dict[str, Any]]:
    url = _hf_tree_url(repo, revision=revision, path=path, recursive=True, expand=True)
    response = _request_json(url, token=token, timeout=timeout)
    if isinstance(response, list):
        return [item for item in response if isinstance(item, dict)]
    return [item for item in _as_list(_as_dict(response).get("siblings")) if isinstance(item, dict)]


def admin_huggingface_metadata_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    record, warnings = _record_from_payload(data)
    source = _source_from_payload(data, record)
    repo = _clean(source.get("repo"))
    revision = _clean(source.get("revision")) or "main"
    token = _clean(data.get("token"))
    timeout = _safe_timeout(data.get("timeout_seconds"))
    if not repo:
        return _error_payload(HF_METADATA_SCHEMA_ID, "missing_repo", "A Hugging Face repo id is required.", warnings=warnings, source=source)
    offline_info = _as_dict(data.get("offline_model_info"))
    if offline_info:
        return {**_metadata_from_model_info(offline_info, repo=repo, source=source), "warnings": warnings, "mode": "offline_payload"}
    try:
        info = fetch_huggingface_metadata(repo=repo, revision=revision, token=token, timeout=timeout)
    except Exception as exc:  # pragma: no cover - network behavior is environment dependent
        status, message = _normalize_exception(exc)
        return _error_payload(HF_METADATA_SCHEMA_ID, status, message, warnings=warnings, repo=repo, source=source)
    return {**_metadata_from_model_info(info, repo=repo, source=source), "warnings": warnings, "mode": "remote"}


def admin_huggingface_discover_files_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = _as_dict(payload)
    record, warnings = _record_from_payload(data)
    if record is None:
        return _error_payload(HF_DISCOVERY_SCHEMA_ID, "missing_record", "A catalog_id, record, or source payload is required.", warnings=warnings)
    source = _source_from_payload(data, record)
    repo = _clean(source.get("repo"))
    revision = _clean(source.get("revision")) or "main"
    token = _clean(data.get("token"))
    timeout = _safe_timeout(data.get("timeout_seconds"))
    if _clean(_as_dict(record.get("source")).get("provider")).lower() not in {"", HF_PROVIDER} and not _as_dict(data.get("source")):
        return _error_payload(HF_DISCOVERY_SCHEMA_ID, "provider_mismatch", "The selected catalog record is not a Hugging Face source.", warnings=warnings, repo=repo, source=source)
    if not repo:
        return _error_payload(HF_DISCOVERY_SCHEMA_ID, "missing_repo", "A Hugging Face repo id is required for file discovery.", warnings=warnings, source=source)
    offline_rows = _as_list(data.get("offline_tree")) or _as_list(data.get("offline_siblings"))
    if offline_rows:
        rows = [item for item in offline_rows if isinstance(item, dict)]
        mode = "offline_payload"
    else:
        try:
            rows = fetch_huggingface_tree(repo=repo, revision=revision, path=_clean(source.get("path")), token=token, timeout=timeout)
            mode = "remote"
        except Exception as exc:  # pragma: no cover - network behavior is environment dependent
            status, message = _normalize_exception(exc)
            return _error_payload(HF_DISCOVERY_SCHEMA_ID, status, message, warnings=warnings, repo=repo, source=source)
    discovery = discover_remote_files_for_record(record, rows, provider=HF_PROVIDER)
    return {
        **discovery,
        "schema_id": HF_DISCOVERY_SCHEMA_ID,
        "ok": True,
        "status": "ready",
        "mode": mode,
        "repo": repo,
        "revision": revision,
        "source": source,
        "warnings": warnings,
        "capabilities": {
            "remote_metadata": True,
            "file_discovery": True,
            "remote_previews": False,
            "downloads": False,
            "tokens_saved": False,
            "metadata_saved": False,
        },
    }
