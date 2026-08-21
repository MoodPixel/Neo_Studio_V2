from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neo_app.admin.models.huggingface_cache import resolve_huggingface_cache
from neo_app.admin.models.huggingface_snapshot_probe import probe_huggingface_repository_snapshot

from .chatterbox_model_registry import MODEL_SPECS

RUNTIME_RESOLVER_SCHEMA_ID = "neo.voice_engine.chatterbox.runtime_resolver.v1"
PHASE_ID = "phase4_6_voice_model_lifecycle_unification"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _catalog_record(project_root: Path, model_id: str) -> tuple[dict[str, Any] | None, str]:
    path = Path(project_root).resolve() / "neo_manifests" / "models" / "model_catalog.json"
    if not path.exists() or not path.is_file():
        return None, "model_catalog_missing"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"model_catalog_unreadable:{type(exc).__name__}"
    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        return None, "model_catalog_records_invalid"
    for row in records:
        if isinstance(row, dict) and _clean(row.get("id")) == model_id:
            return dict(row), ""
    return None, "catalog_record_missing"


def probe_chatterbox_runtime_model(*, project_root: Path, model_id: str, cache_resolution: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = MODEL_SPECS.get(_clean(model_id))
    if spec is None:
        return {
            "schema_id": RUNTIME_RESOLVER_SCHEMA_ID,
            "probe_id": "chatterbox_model_snapshot",
            "phase": PHASE_ID,
            "model_id": _clean(model_id),
            "state": "not_installed",
            "installed": False,
            "source_kind": "",
            "source_path": "",
            "catalog_error": "unknown_chatterbox_model_id",
            "message": "Unknown Chatterbox model ID.",
            "policy": {"remote_calls": False, "downloads": False, "generation_may_download": False},
        }
    record, catalog_error = _catalog_record(Path(project_root), _clean(model_id))
    if record is None:
        return {
            "schema_id": RUNTIME_RESOLVER_SCHEMA_ID,
            "probe_id": "chatterbox_model_snapshot",
            "phase": PHASE_ID,
            "model_id": _clean(model_id),
            "state": "not_installed",
            "installed": False,
            "source_kind": "",
            "source_path": "",
            "catalog_error": catalog_error,
            "repo_id": spec["repo_id"],
            "message": "No Admin repository-snapshot catalog binding exists for this Chatterbox model.",
            "policy": {"remote_calls": False, "downloads": False, "generation_may_download": False},
        }
    source = _as_dict(record.get("source"))
    if _clean(source.get("repo")) != _clean(spec["repo_id"]):
        return {
            "schema_id": RUNTIME_RESOLVER_SCHEMA_ID,
            "probe_id": "chatterbox_model_snapshot",
            "phase": PHASE_ID,
            "model_id": _clean(model_id),
            "state": "partial",
            "installed": False,
            "source_kind": "",
            "source_path": "",
            "catalog_error": "catalog_repo_mismatch",
            "repo_id": _clean(source.get("repo")),
            "message": "The Admin catalog repository does not match the Chatterbox Voice model contract.",
            "policy": {"remote_calls": False, "downloads": False, "generation_may_download": False},
        }
    hf_probe = probe_huggingface_repository_snapshot(record, cache_resolution=cache_resolution or resolve_huggingface_cache())
    state = _clean(hf_probe.get("state"))
    snapshot_path = _clean(_as_dict(hf_probe.get("cache")).get("snapshot_path")) if state == "installed" else ""
    executable = state == "installed" and bool(snapshot_path)
    public_state = "installed" if executable else ("partial" if state in {"partial", "stale", "corrupt", "unverified"} else "not_installed")
    return {
        "schema_id": RUNTIME_RESOLVER_SCHEMA_ID,
        "probe_id": "chatterbox_model_snapshot",
        "phase": PHASE_ID,
        "model_id": _clean(model_id),
        "state": public_state,
        "installed": executable,
        "source_kind": "huggingface_cache_snapshot" if executable else "",
        "source_path": snapshot_path if executable else "",
        "catalog_error": "",
        "repo_id": spec["repo_id"],
        "requested_revision": _clean(_as_dict(hf_probe.get("source")).get("requested_revision")),
        "resolved_revision": _clean(_as_dict(hf_probe.get("source")).get("resolved_revision")),
        "huggingface_probe": hf_probe,
        "message": "Using the authoritative Admin-managed Hugging Face snapshot." if executable else "No verified local Chatterbox snapshot is available. Install or repair the model in Admin → Models.",
        "policy": {
            "huggingface_cache_required": True,
            "huggingface_requires_authoritative_probe": True,
            "existing_huggingface_cache_reused_in_place": True,
            "legacy_migration_required": False,
            "remote_calls": False,
            "downloads": False,
            "generation_may_download": False,
            "manifest_mutated": False,
        },
    }


def resolve_chatterbox_runtime_model_path(*, project_root: Path, model_id: str, cache_resolution: dict[str, Any] | None = None) -> Path | None:
    probe = probe_chatterbox_runtime_model(project_root=project_root, model_id=model_id, cache_resolution=cache_resolution)
    if probe.get("state") != "installed":
        return None
    raw = _clean(probe.get("source_path"))
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if path.exists() and path.is_dir() else None
