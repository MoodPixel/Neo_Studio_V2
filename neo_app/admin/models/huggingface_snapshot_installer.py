from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .huggingface_cache import resolve_huggingface_cache
from .manifest_loader import load_model_catalog

SNAPSHOT_INSTALL_SCHEMA_ID = "neo.admin.models.huggingface.snapshot_install.v1"
PHASE_ID = "phase4_5_3_huggingface_snapshot_installer"
HF_PROVIDER = "huggingface"
SNAPSHOT_SOURCE_MODE = "repository_snapshot"
SNAPSHOT_STRATEGY = "huggingface_snapshot"
HF_CACHE_TARGET = "hf_cache"


class HuggingFaceSnapshotInstallError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code or "snapshot_install_failed")
        self.message = str(message or "Hugging Face snapshot installation failed.")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _clean_lower(value: Any) -> str:
    return _clean(value).lower()


def _catalog_record(catalog_id: str) -> dict[str, Any] | None:
    wanted = _clean(catalog_id)
    if not wanted:
        return None
    catalog = load_model_catalog()
    for record in _as_list(catalog.get("records")):
        if isinstance(record, dict) and _clean(record.get("id")) == wanted:
            return record
    return None


def is_huggingface_snapshot_record(record: dict[str, Any] | None) -> bool:
    item = _as_dict(record)
    source = _as_dict(item.get("source"))
    install = _as_dict(item.get("install"))
    return (
        _clean_lower(item.get("source_mode")) == SNAPSHOT_SOURCE_MODE
        and _clean_lower(source.get("provider")) == HF_PROVIDER
        and _clean_lower(install.get("strategy")) == SNAPSHOT_STRATEGY
        and _clean_lower(install.get("target_type")) == HF_CACHE_TARGET
    )


def _load_snapshot_download() -> Callable[..., Any]:
    try:
        from huggingface_hub import snapshot_download  # type: ignore
    except Exception as exc:  # pragma: no cover - environment dependent
        raise HuggingFaceSnapshotInstallError(
            "huggingface_hub_missing",
            "The Neo Studio environment does not have huggingface_hub. Re-run setup_neo_studio_venv.bat before installing repository snapshots.",
        ) from exc
    return snapshot_download


def huggingface_snapshot_dependency_state() -> dict[str, Any]:
    try:
        import huggingface_hub  # type: ignore
        _load_snapshot_download()
    except HuggingFaceSnapshotInstallError as exc:
        return {
            "available": False,
            "version": "",
            "error_code": exc.code,
            "message": exc.message,
        }
    except Exception as exc:  # pragma: no cover - defensive only
        return {
            "available": False,
            "version": "",
            "error_code": "huggingface_hub_import_failed",
            "message": f"huggingface_hub could not be imported ({type(exc).__name__}).",
        }
    return {
        "available": True,
        "version": str(getattr(huggingface_hub, "__version__", "") or ""),
        "error_code": "",
        "message": "",
    }


def build_huggingface_snapshot_install_request(
    payload: dict[str, Any] | None = None,
    *,
    cache_resolution: dict[str, Any] | None = None,
    require_dependency: bool = True,
) -> dict[str, Any]:
    """Build a validated, non-secret install request for one HF repository snapshot.

    This function is planning/validation only. It never creates cache directories,
    contacts Hugging Face, or writes model/runtime state.
    """

    data = _as_dict(payload)
    catalog_id = _clean(data.get("catalog_id"))
    record = _catalog_record(catalog_id)
    errors: list[str] = []

    if record is None:
        errors.append("catalog_record_not_found")
        return {
            "schema_id": SNAPSHOT_INSTALL_SCHEMA_ID,
            "phase": PHASE_ID,
            "ok": False,
            "status": "missing_record",
            "catalog_id": catalog_id,
            "warnings": [],
            "errors": errors,
        }

    source = _as_dict(record.get("source"))
    install = _as_dict(record.get("install"))
    source_mode = _clean_lower(record.get("source_mode"))
    provider = _clean_lower(source.get("provider"))
    strategy = _clean_lower(install.get("strategy"))
    target_type = _clean_lower(install.get("target_type"))
    repo = _clean(source.get("repo"))
    revision = _clean(source.get("revision")) or "main"

    if source_mode != SNAPSHOT_SOURCE_MODE:
        errors.append(f"unsupported_source_mode:{source_mode or 'unknown'}")
    if provider != HF_PROVIDER:
        errors.append(f"unsupported_snapshot_provider:{provider or 'unknown'}")
    if strategy != SNAPSHOT_STRATEGY:
        errors.append(f"unsupported_snapshot_strategy:{strategy or 'unknown'}")
    if target_type != HF_CACHE_TARGET:
        errors.append(f"unsupported_snapshot_target:{target_type or 'unknown'}")
    if not repo or "/" not in repo:
        errors.append("huggingface_repo_missing_or_invalid")

    cache = cache_resolution or resolve_huggingface_cache()
    cache_dir = _clean(cache.get("hub_cache"))
    if not cache_dir:
        errors.append("huggingface_cache_unresolved")

    dependency = huggingface_snapshot_dependency_state() if require_dependency else {
        "available": True,
        "version": "not_checked",
        "error_code": "",
        "message": "",
    }
    if require_dependency and not bool(dependency.get("available")):
        errors.append(_clean(dependency.get("error_code")) or "huggingface_hub_missing")

    warnings: list[str] = []
    if bool(source.get("requires_token") or source.get("gated") or source.get("private")):
        warnings.append("source_may_require_session_token_or_access_approval")

    ok = not errors
    return {
        "schema_id": SNAPSHOT_INSTALL_SCHEMA_ID,
        "phase": PHASE_ID,
        "ok": ok,
        "status": "ready" if ok else "needs_attention",
        "catalog_id": catalog_id,
        "display_name": _clean(record.get("display_name")) or catalog_id,
        "record": {
            "id": catalog_id,
            "category": _clean_lower(record.get("category")),
            "base_model": _clean_lower(record.get("base_model")),
            "model_type": _clean_lower(record.get("model_type")),
            "source_mode": source_mode,
        },
        "source": {
            "provider": provider,
            "repo": repo,
            "revision": revision,
            "source_url": _clean(source.get("source_url")) or (f"https://huggingface.co/{repo}" if repo else ""),
            "requires_token": bool(source.get("requires_token") or source.get("gated") or source.get("private")),
        },
        "install": {
            "strategy": strategy,
            "target_type": target_type,
            "backend_targets": [_clean_lower(item) for item in _as_list(install.get("backend_targets")) if _clean(item)],
            "probe_id": _clean(install.get("probe_id")),
            "expected_size_mb": install.get("expected_size_mb"),
            "allow_patterns": [_clean(item) for item in _as_list(install.get("allow_patterns")) if _clean(item)],
            "ignore_patterns": [_clean(item) for item in _as_list(install.get("ignore_patterns")) if _clean(item)],
        },
        "cache": {
            "hub_cache": cache_dir,
            "hf_home": _clean(cache.get("hf_home")),
            "source": _clean(cache.get("source")),
            "source_kind": _clean(cache.get("source_kind")),
            "exists_before_install": bool(cache.get("exists")),
        },
        "dependency": dependency,
        "warnings": sorted(set(warnings)),
        "errors": errors,
        "policy": {
            "explicit_confirmation_required": True,
            "uses_huggingface_snapshot_download": True,
            "uses_local_dir": False,
            "huggingface_owns_cache_layout": True,
            "tokens_persisted": False,
            "manifest_mutated": False,
            "runtime_download_on_generate": False,
            "disk_preflight": True,
            "disk_preflight_owner": "admin_download_manager_phase4_5_4",
            "installed_probe": True,
            "installed_probe_owner": "admin_download_manager_phase4_5_5",
        },
    }


def _normalized_snapshot_error(exc: Exception) -> HuggingFaceSnapshotInstallError:
    if isinstance(exc, HuggingFaceSnapshotInstallError):
        return exc
    name = type(exc).__name__
    lowered = name.lower()
    if "gatedrepo" in lowered:
        return HuggingFaceSnapshotInstallError("huggingface_gated_repo", "The Hugging Face repository is gated. Approve access and provide a session token if required.")
    if "repositorynotfound" in lowered:
        return HuggingFaceSnapshotInstallError("huggingface_repository_not_found", "The Hugging Face repository was not found or is not accessible with the current session.")
    if "revisionnotfound" in lowered:
        return HuggingFaceSnapshotInstallError("huggingface_revision_not_found", "The requested Hugging Face repository revision was not found.")
    if "entrynotfound" in lowered:
        return HuggingFaceSnapshotInstallError("huggingface_snapshot_entry_not_found", "A required Hugging Face snapshot entry could not be resolved.")
    if "connection" in lowered or "timeout" in lowered:
        return HuggingFaceSnapshotInstallError("huggingface_network_error", "The Hugging Face snapshot download could not complete because of a network error.")
    return HuggingFaceSnapshotInstallError("huggingface_snapshot_download_failed", f"Hugging Face snapshot download failed ({name}).")


def install_huggingface_snapshot(
    request_payload: dict[str, Any],
    *,
    token: str = "",
    snapshot_download_fn: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Execute one complete repository snapshot installation.

    `snapshot_download` owns the cache layout. Neo deliberately supplies no
    `local_dir`, so blobs/refs/snapshots remain standard Hugging Face cache data.
    """

    request_data = _as_dict(request_payload)
    if not bool(request_data.get("ok")):
        raise HuggingFaceSnapshotInstallError("snapshot_request_not_ready", "The repository snapshot install request is not ready.")

    source = _as_dict(request_data.get("source"))
    install = _as_dict(request_data.get("install"))
    cache = _as_dict(request_data.get("cache"))
    repo = _clean(source.get("repo"))
    revision = _clean(source.get("revision")) or "main"
    cache_dir = _clean(cache.get("hub_cache"))
    if not repo or not cache_dir:
        raise HuggingFaceSnapshotInstallError("snapshot_request_incomplete", "The repository id or Hugging Face cache path is missing.")

    downloader = snapshot_download_fn or _load_snapshot_download()
    kwargs: dict[str, Any] = {
        "repo_id": repo,
        "revision": revision,
        "cache_dir": cache_dir,
        "token": _clean(token) or None,
    }
    allow_patterns = [_clean(item) for item in _as_list(install.get("allow_patterns")) if _clean(item)]
    ignore_patterns = [_clean(item) for item in _as_list(install.get("ignore_patterns")) if _clean(item)]
    if allow_patterns:
        kwargs["allow_patterns"] = allow_patterns
    if ignore_patterns:
        kwargs["ignore_patterns"] = ignore_patterns

    try:
        resolved = downloader(**kwargs)
    except Exception as exc:  # pragma: no cover - exercised via normalized unit doubles
        raise _normalized_snapshot_error(exc) from exc

    if not isinstance(resolved, (str, Path)) or not _clean(resolved):
        raise HuggingFaceSnapshotInstallError("snapshot_path_missing", "Hugging Face completed without returning a snapshot path.")

    snapshot_path = Path(str(resolved))
    # This remains an execution sanity check. The Admin download manager runs
    # the Phase 4.5.5 authoritative cache-ref + content probe before a job can
    # transition to completed.
    if not snapshot_path.exists() or not snapshot_path.is_dir():
        raise HuggingFaceSnapshotInstallError("snapshot_path_not_materialized", "Hugging Face returned a snapshot path that is not present on disk.")

    resolved_revision = snapshot_path.name if snapshot_path.parent.name == "snapshots" else ""
    return {
        "schema_id": SNAPSHOT_INSTALL_SCHEMA_ID,
        "phase": PHASE_ID,
        "ok": True,
        "status": "completed",
        "catalog_id": _clean(request_data.get("catalog_id")),
        "source": {
            "provider": HF_PROVIDER,
            "repo": repo,
            "requested_revision": revision,
            "resolved_revision": resolved_revision,
        },
        "cache": {
            "hub_cache": cache_dir,
            "snapshot_path": str(snapshot_path),
        },
        "verification": {
            "level": "installer_return_path_only",
            "path_exists": True,
            "path_is_directory": True,
            "authoritative_installed_probe": False,
            "authoritative_installed_probe_owner": "admin_download_manager_phase4_5_5",
        },
        "policy": {
            "used_local_dir": False,
            "huggingface_owns_cache_layout": True,
            "filtered_materialization": bool(allow_patterns or ignore_patterns),
            "allow_patterns": allow_patterns,
            "ignore_patterns": ignore_patterns,
            "tokens_persisted": False,
            "disk_preflight": True,
            "disk_preflight_owner": "admin_download_manager_phase4_5_4",
        },
    }
