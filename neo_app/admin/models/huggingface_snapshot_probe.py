from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import re

from .huggingface_cache import resolve_huggingface_cache

HF_SNAPSHOT_PROBE_SCHEMA_ID = "neo.admin.models.huggingface.snapshot_probe.v1"
PHASE_ID = "phase4_5_5_huggingface_snapshot_installed_probe"
HF_PROVIDER = "huggingface"
SNAPSHOT_SOURCE_MODE = "repository_snapshot"
SNAPSHOT_STRATEGY = "huggingface_snapshot"
HF_CACHE_TARGET = "hf_cache"

PROBE_STATE_INSTALLED = "installed"
PROBE_STATE_NOT_INSTALLED = "not_installed"
PROBE_STATE_PARTIAL = "partial"
PROBE_STATE_STALE = "stale"
PROBE_STATE_CORRUPT = "corrupt"
PROBE_STATE_UNVERIFIED = "unverified"

_COMMIT_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value or "").strip().strip('"').strip("'")


def _clean_lower(value: Any) -> str:
    return _clean(value).lower()


def is_huggingface_repository_snapshot_record(record: dict[str, Any] | None) -> bool:
    item = _as_dict(record)
    source = _as_dict(item.get("source"))
    install = _as_dict(item.get("install"))
    return (
        _clean_lower(item.get("source_mode")) == SNAPSHOT_SOURCE_MODE
        and _clean_lower(source.get("provider")) == HF_PROVIDER
        and _clean_lower(install.get("strategy")) == SNAPSHOT_STRATEGY
        and _clean_lower(install.get("target_type")) == HF_CACHE_TARGET
    )


def _safe_revision_parts(revision: str) -> list[str] | None:
    text = _clean(revision).replace("\\", "/")
    if not text or text.startswith("/"):
        return None
    parts = [part for part in text.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        return None
    return parts


def _repo_cache_folder_name(repo_id: str) -> str:
    """Return HF's standard on-disk model repo folder name without writing it.

    The public Hub cache convention is `models--OWNER--REPO`. Use the library
    helper when available, but keep a deterministic read-only fallback so an
    existing cache can still be diagnosed if the Python package is unavailable.
    """

    repo = _clean(repo_id).strip("/")
    if not repo:
        return ""
    try:
        from huggingface_hub.file_download import repo_folder_name  # type: ignore

        return str(repo_folder_name(repo_id=repo, repo_type="model") or "")
    except Exception:
        return "models--" + repo.replace("/", "--")


def _list_snapshot_commits(snapshots_root: Path) -> list[str]:
    if not snapshots_root.exists() or not snapshots_root.is_dir():
        return []
    rows: list[str] = []
    try:
        for child in snapshots_root.iterdir():
            if child.name.startswith("."):
                continue
            if child.is_dir():
                rows.append(child.name)
    except OSError:
        return []
    return sorted(set(rows))


def _snapshot_tree_stats(snapshot_path: Path) -> dict[str, Any]:
    file_count = 0
    directory_count = 0
    broken_symlinks: list[str] = []
    stat_errors: list[str] = []
    total_materialized_bytes = 0

    try:
        iterator = snapshot_path.rglob("*")
        for item in iterator:
            try:
                if item.is_symlink() and not item.exists():
                    broken_symlinks.append(str(item))
                    continue
                if item.is_dir():
                    directory_count += 1
                    continue
                if item.is_file():
                    file_count += 1
                    try:
                        total_materialized_bytes += int(item.stat().st_size)
                    except OSError:
                        stat_errors.append(str(item))
            except OSError:
                stat_errors.append(str(item))
    except OSError as exc:
        stat_errors.append(f"snapshot_walk:{type(exc).__name__}")

    return {
        "file_count": file_count,
        "directory_count": directory_count,
        "broken_symlink_count": len(broken_symlinks),
        "broken_symlinks": broken_symlinks[:50],
        "stat_error_count": len(stat_errors),
        "stat_errors": stat_errors[:50],
        "materialized_file_bytes": total_materialized_bytes,
    }


def _probe_content_contract(probe_id: str, snapshot_path: Path, catalog_id: str) -> dict[str, Any]:
    probe = _clean_lower(probe_id)
    if probe == "qwen3_tts_model_snapshot":
        try:
            from neo_voice_engine.qwen3_tts_model_registry import probe_model_snapshot_directory
        except Exception as exc:
            return {
                "probe_id": probe,
                "state": PROBE_STATE_UNVERIFIED,
                "message": f"Qwen snapshot verifier could not be loaded ({type(exc).__name__}).",
                "errors": ["snapshot_content_probe_import_failed"],
                "missing_paths": [],
            }
        result = probe_model_snapshot_directory(snapshot_path, catalog_id, canonical_path=snapshot_path)
        state = _clean_lower(result.get("state"))
        if state not in {PROBE_STATE_INSTALLED, PROBE_STATE_PARTIAL, PROBE_STATE_NOT_INSTALLED}:
            state = PROBE_STATE_UNVERIFIED
        return {**result, "state": state, "errors": []}

    if probe == "chatterbox_model_snapshot":
        try:
            from neo_voice_engine.chatterbox_model_registry import probe_model_snapshot_directory
        except Exception as exc:
            return {
                "probe_id": probe,
                "state": PROBE_STATE_UNVERIFIED,
                "message": f"Chatterbox snapshot verifier could not be loaded ({type(exc).__name__}).",
                "errors": ["snapshot_content_probe_import_failed"],
                "missing_paths": [],
            }
        result = probe_model_snapshot_directory(snapshot_path, catalog_id, canonical_path=snapshot_path)
        state = _clean_lower(result.get("state"))
        if state not in {PROBE_STATE_INSTALLED, PROBE_STATE_PARTIAL, PROBE_STATE_NOT_INSTALLED}:
            state = PROBE_STATE_UNVERIFIED
        return {**result, "state": state, "errors": []}

    return {
        "probe_id": probe,
        "state": PROBE_STATE_UNVERIFIED,
        "message": f"No Admin repository-snapshot verifier is registered for probe '{probe or '<missing>'}'.",
        "errors": ["snapshot_content_probe_not_supported"],
        "missing_paths": [],
    }


def _payload(
    *,
    record: dict[str, Any],
    state: str,
    reason: str,
    cache: dict[str, Any],
    repo_path: Path | None = None,
    snapshot_path: Path | None = None,
    requested_revision: str = "",
    resolved_revision: str = "",
    content_probe: dict[str, Any] | None = None,
    tree: dict[str, Any] | None = None,
    cached_revisions: list[str] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    message: str = "",
) -> dict[str, Any]:
    source = _as_dict(record.get("source"))
    install = _as_dict(record.get("install"))
    catalog_id = _clean(record.get("id"))
    normalized_errors = [str(item) for item in (errors or []) if str(item).strip()]
    normalized_warnings = [str(item) for item in (warnings or []) if str(item).strip()]
    installed = state == PROBE_STATE_INSTALLED
    return {
        "schema_id": HF_SNAPSHOT_PROBE_SCHEMA_ID,
        "phase": PHASE_ID,
        "ok": state != PROBE_STATE_UNVERIFIED,
        "state": state,
        "installed": installed,
        "reason": reason,
        "message": message,
        "catalog_id": catalog_id,
        "display_name": _clean(record.get("display_name")) or catalog_id,
        "source": {
            "provider": _clean_lower(source.get("provider")),
            "repo": _clean(source.get("repo")),
            "requested_revision": requested_revision,
            "resolved_revision": resolved_revision,
        },
        "install": {
            "strategy": _clean_lower(install.get("strategy")),
            "target_type": _clean_lower(install.get("target_type")),
            "probe_id": _clean_lower(install.get("probe_id")),
        },
        "cache": {
            "hub_cache": _clean(cache.get("hub_cache")),
            "cache_source": _clean(cache.get("source")),
            "repo_path": str(repo_path) if repo_path is not None else "",
            "snapshot_path": str(snapshot_path) if snapshot_path is not None else "",
            "cached_revisions": list(cached_revisions or []),
        },
        "tree": tree or {
            "file_count": 0,
            "directory_count": 0,
            "broken_symlink_count": 0,
            "broken_symlinks": [],
            "stat_error_count": 0,
            "stat_errors": [],
            "materialized_file_bytes": 0,
        },
        "content_probe": content_probe or {},
        "warnings": normalized_warnings,
        "errors": normalized_errors,
        "policy": {
            "local_only": True,
            "remote_calls": False,
            "writes": False,
            "creates_directories": False,
            "manifest_mutated": False,
            "receipt_is_authority": False,
            "requires_requested_revision_resolution": True,
            "requires_content_probe_pass": True,
            "runtime_binding": False,
        },
    }


def probe_huggingface_repository_snapshot(
    record: dict[str, Any],
    *,
    cache_resolution: dict[str, Any] | None = None,
    content_probe_fn: Callable[[str, Path, str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Authoritatively classify one manifest-declared HF repository snapshot.

    The probe is offline and read-only. It resolves the manifest revision through
    Hugging Face's local `refs/`/`snapshots/` cache structure, checks for broken
    materialization, and then applies the manifest-declared content probe. A
    receipt or a previous successful download job is never treated as proof.
    """

    item = _as_dict(record)
    source = _as_dict(item.get("source"))
    install = _as_dict(item.get("install"))
    cache = cache_resolution or resolve_huggingface_cache()
    requested_revision = _clean(source.get("revision")) or "main"
    repo = _clean(source.get("repo"))
    catalog_id = _clean(item.get("id"))

    if not is_huggingface_repository_snapshot_record(item):
        return _payload(
            record=item,
            state=PROBE_STATE_UNVERIFIED,
            reason="record_not_huggingface_repository_snapshot",
            cache=cache,
            requested_revision=requested_revision,
            errors=["snapshot_probe_record_contract_invalid"],
            message="The selected catalog record is not a supported Hugging Face repository snapshot.",
        )
    if not repo or "/" not in repo:
        return _payload(
            record=item,
            state=PROBE_STATE_UNVERIFIED,
            reason="huggingface_repo_missing_or_invalid",
            cache=cache,
            requested_revision=requested_revision,
            errors=["huggingface_repo_missing_or_invalid"],
            message="The repository snapshot record does not declare a valid Hugging Face repo id.",
        )

    hub_cache = _clean(cache.get("hub_cache"))
    if not hub_cache:
        return _payload(
            record=item,
            state=PROBE_STATE_UNVERIFIED,
            reason="huggingface_cache_unresolved",
            cache=cache,
            requested_revision=requested_revision,
            errors=["huggingface_cache_unresolved"],
            message="The Hugging Face cache path could not be resolved.",
        )

    hub_path = Path(hub_cache).expanduser()
    folder_name = _repo_cache_folder_name(repo)
    repo_path = hub_path / folder_name if folder_name else hub_path
    refs_root = repo_path / "refs"
    snapshots_root = repo_path / "snapshots"

    if not hub_path.exists():
        return _payload(
            record=item,
            state=PROBE_STATE_NOT_INSTALLED,
            reason="huggingface_cache_missing",
            cache=cache,
            repo_path=repo_path,
            requested_revision=requested_revision,
            message="The resolved Hugging Face cache does not exist yet.",
        )
    if not hub_path.is_dir():
        return _payload(
            record=item,
            state=PROBE_STATE_CORRUPT,
            reason="huggingface_cache_not_directory",
            cache=cache,
            repo_path=repo_path,
            requested_revision=requested_revision,
            errors=["huggingface_cache_not_directory"],
            message="The resolved Hugging Face cache path is not a directory.",
        )
    if not repo_path.exists():
        return _payload(
            record=item,
            state=PROBE_STATE_NOT_INSTALLED,
            reason="repository_cache_missing",
            cache=cache,
            repo_path=repo_path,
            requested_revision=requested_revision,
            message="This repository is not present in the resolved Hugging Face cache.",
        )
    if not repo_path.is_dir():
        return _payload(
            record=item,
            state=PROBE_STATE_CORRUPT,
            reason="repository_cache_not_directory",
            cache=cache,
            repo_path=repo_path,
            requested_revision=requested_revision,
            errors=["huggingface_repository_cache_corrupt"],
            message="The Hugging Face repository cache path is not a directory.",
        )

    cached_revisions = _list_snapshot_commits(snapshots_root)
    revision_parts = _safe_revision_parts(requested_revision)
    if not revision_parts:
        return _payload(
            record=item,
            state=PROBE_STATE_CORRUPT,
            reason="requested_revision_invalid",
            cache=cache,
            repo_path=repo_path,
            requested_revision=requested_revision,
            cached_revisions=cached_revisions,
            errors=["huggingface_revision_invalid"],
            message="The manifest revision cannot be mapped safely into the local Hugging Face cache.",
        )

    resolved_revision = ""
    requested_is_commit = bool(_COMMIT_RE.fullmatch(requested_revision))
    if requested_is_commit:
        resolved_revision = requested_revision
    else:
        ref_path = refs_root.joinpath(*revision_parts)
        if ref_path.exists():
            if not ref_path.is_file():
                return _payload(
                    record=item,
                    state=PROBE_STATE_CORRUPT,
                    reason="revision_ref_not_file",
                    cache=cache,
                    repo_path=repo_path,
                    requested_revision=requested_revision,
                    cached_revisions=cached_revisions,
                    errors=["huggingface_revision_ref_corrupt"],
                    message="The Hugging Face revision ref exists but is not a file.",
                )
            try:
                resolved_revision = ref_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                return _payload(
                    record=item,
                    state=PROBE_STATE_CORRUPT,
                    reason="revision_ref_unreadable",
                    cache=cache,
                    repo_path=repo_path,
                    requested_revision=requested_revision,
                    cached_revisions=cached_revisions,
                    errors=[f"huggingface_revision_ref_unreadable:{type(exc).__name__}"],
                    message="The Hugging Face revision ref could not be read.",
                )
            if not _COMMIT_RE.fullmatch(resolved_revision):
                return _payload(
                    record=item,
                    state=PROBE_STATE_CORRUPT,
                    reason="revision_ref_invalid_commit",
                    cache=cache,
                    repo_path=repo_path,
                    requested_revision=requested_revision,
                    resolved_revision=resolved_revision,
                    cached_revisions=cached_revisions,
                    errors=["huggingface_revision_ref_invalid"],
                    message="The Hugging Face revision ref does not contain a valid commit hash.",
                )
        else:
            if cached_revisions:
                return _payload(
                    record=item,
                    state=PROBE_STATE_STALE,
                    reason="requested_revision_not_cached",
                    cache=cache,
                    repo_path=repo_path,
                    requested_revision=requested_revision,
                    cached_revisions=cached_revisions,
                    warnings=["other_cached_revisions_present"],
                    message="The repository has cached snapshots, but the manifest-requested revision is not locally resolved.",
                )
            return _payload(
                record=item,
                state=PROBE_STATE_NOT_INSTALLED,
                reason="requested_revision_not_cached",
                cache=cache,
                repo_path=repo_path,
                requested_revision=requested_revision,
                cached_revisions=cached_revisions,
                message="The requested repository revision is not present in the Hugging Face cache.",
            )

    snapshot_path = snapshots_root / resolved_revision
    if not snapshot_path.exists():
        if requested_is_commit:
            return _payload(
                record=item,
                state=PROBE_STATE_NOT_INSTALLED,
                reason="requested_commit_not_cached",
                cache=cache,
                repo_path=repo_path,
                snapshot_path=snapshot_path,
                requested_revision=requested_revision,
                resolved_revision=resolved_revision,
                cached_revisions=cached_revisions,
                message="The explicitly requested Hugging Face commit is not present in the local cache.",
            )
        return _payload(
            record=item,
            state=PROBE_STATE_CORRUPT,
            reason="resolved_snapshot_missing",
            cache=cache,
            repo_path=repo_path,
            snapshot_path=snapshot_path,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            cached_revisions=cached_revisions,
            errors=["huggingface_snapshot_missing_for_resolved_revision"],
            message="The local Hugging Face revision ref points to a snapshot directory that is missing.",
        )
    if not snapshot_path.is_dir():
        return _payload(
            record=item,
            state=PROBE_STATE_CORRUPT,
            reason="snapshot_path_not_directory",
            cache=cache,
            repo_path=repo_path,
            snapshot_path=snapshot_path,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            cached_revisions=cached_revisions,
            errors=["huggingface_snapshot_path_corrupt"],
            message="The resolved Hugging Face snapshot path is not a directory.",
        )

    tree = _snapshot_tree_stats(snapshot_path)
    if tree.get("broken_symlink_count") or tree.get("stat_error_count"):
        return _payload(
            record=item,
            state=PROBE_STATE_CORRUPT,
            reason="snapshot_materialization_corrupt",
            cache=cache,
            repo_path=repo_path,
            snapshot_path=snapshot_path,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            cached_revisions=cached_revisions,
            tree=tree,
            errors=["huggingface_snapshot_materialization_corrupt"],
            message="The Hugging Face snapshot contains broken links or unreadable files.",
        )
    if int(tree.get("file_count") or 0) <= 0:
        return _payload(
            record=item,
            state=PROBE_STATE_PARTIAL,
            reason="snapshot_empty",
            cache=cache,
            repo_path=repo_path,
            snapshot_path=snapshot_path,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            cached_revisions=cached_revisions,
            tree=tree,
            errors=["huggingface_snapshot_empty"],
            message="The resolved Hugging Face snapshot directory is empty.",
        )

    content_probe_runner = content_probe_fn or _probe_content_contract
    content_probe = _as_dict(content_probe_runner(_clean(install.get("probe_id")), snapshot_path, catalog_id))
    content_state = _clean_lower(content_probe.get("state"))
    content_errors = [str(item) for item in _as_list(content_probe.get("errors")) if str(item).strip()]

    if content_state == PROBE_STATE_INSTALLED:
        return _payload(
            record=item,
            state=PROBE_STATE_INSTALLED,
            reason="requested_revision_and_content_verified",
            cache=cache,
            repo_path=repo_path,
            snapshot_path=snapshot_path,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            cached_revisions=cached_revisions,
            tree=tree,
            content_probe=content_probe,
            message="The requested Hugging Face snapshot is installed and passed its manifest-declared content probe.",
        )
    if content_state in {PROBE_STATE_PARTIAL, PROBE_STATE_NOT_INSTALLED}:
        return _payload(
            record=item,
            state=PROBE_STATE_PARTIAL,
            reason="snapshot_content_incomplete",
            cache=cache,
            repo_path=repo_path,
            snapshot_path=snapshot_path,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            cached_revisions=cached_revisions,
            tree=tree,
            content_probe=content_probe,
            errors=content_errors or ["huggingface_snapshot_content_incomplete"],
            message=_clean(content_probe.get("message")) or "The Hugging Face snapshot exists but required model content is incomplete.",
        )

    return _payload(
        record=item,
        state=PROBE_STATE_UNVERIFIED,
        reason="snapshot_content_probe_unavailable",
        cache=cache,
        repo_path=repo_path,
        snapshot_path=snapshot_path,
        requested_revision=requested_revision,
        resolved_revision=resolved_revision,
        cached_revisions=cached_revisions,
        tree=tree,
        content_probe=content_probe,
        errors=content_errors or ["snapshot_content_probe_not_supported"],
        message=_clean(content_probe.get("message")) or "The snapshot exists but its manifest-declared content probe could not verify it.",
    )


def repository_snapshot_catalog_status(record: dict[str, Any], *, cache_resolution: dict[str, Any] | None = None) -> dict[str, Any]:
    """Adapt the HF snapshot probe to the Admin installed-scanner row contract."""

    probe = probe_huggingface_repository_snapshot(record, cache_resolution=cache_resolution)
    source = _as_dict(record.get("source"))
    install = _as_dict(record.get("install"))
    return {
        "catalog_id": _clean(record.get("id")),
        "display_name": record.get("display_name"),
        "category": record.get("category"),
        "base_model": record.get("base_model"),
        "model_type": record.get("model_type"),
        "source_mode": record.get("source_mode"),
        "overall_status": probe.get("state"),
        "reason": probe.get("reason"),
        "install_strategy": install.get("strategy"),
        "probe_id": install.get("probe_id"),
        "repo": source.get("repo"),
        "revision": source.get("revision"),
        "resolved_revision": _as_dict(probe.get("source")).get("resolved_revision"),
        "snapshot_path": _as_dict(probe.get("cache")).get("snapshot_path"),
        "snapshot_probe": probe,
        "backends": [],
    }


def probe_huggingface_snapshot_install_request(request_payload: dict[str, Any]) -> dict[str, Any]:
    """Probe the exact cache target captured in a snapshot install request."""

    request = _as_dict(request_payload)
    record_summary = _as_dict(request.get("record"))
    source = _as_dict(request.get("source"))
    install = _as_dict(request.get("install"))
    record = {
        "id": _clean(request.get("catalog_id") or record_summary.get("id")),
        "display_name": _clean(request.get("display_name")),
        "category": _clean(record_summary.get("category")),
        "base_model": _clean(record_summary.get("base_model")),
        "model_type": _clean(record_summary.get("model_type")),
        "source_mode": _clean(record_summary.get("source_mode")),
        "source": {
            "provider": _clean(source.get("provider")),
            "repo": _clean(source.get("repo")),
            "revision": _clean(source.get("revision")) or "main",
        },
        "install": {
            "strategy": _clean(install.get("strategy")),
            "target_type": _clean(install.get("target_type")),
            "backend_targets": [str(item) for item in _as_list(install.get("backend_targets"))],
            "probe_id": _clean(install.get("probe_id")),
        },
    }
    cache = _as_dict(request.get("cache"))
    cache_resolution = {
        "hub_cache": _clean(cache.get("hub_cache")),
        "hf_home": _clean(cache.get("hf_home")),
        "source": _clean(cache.get("source")),
        "source_kind": _clean(cache.get("source_kind")),
    }
    return probe_huggingface_repository_snapshot(record, cache_resolution=cache_resolution)
