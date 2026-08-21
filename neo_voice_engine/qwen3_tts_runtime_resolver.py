from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from neo_app.admin.models.huggingface_cache import resolve_huggingface_cache
from neo_app.admin.models.huggingface_snapshot_probe import probe_huggingface_repository_snapshot

from .qwen3_tts_model_registry import MODEL_SPECS, probe_model_install

RUNTIME_RESOLVER_SCHEMA_ID = "neo.voice_engine.qwen3_tts.runtime_resolver.v1"
PHASE_ID = "phase4_5_7_qwen_hf_cache_runtime_binding"


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


def _runtime_payload(
    *,
    model_id: str,
    state: str,
    source_kind: str = "",
    source_path: str = "",
    legacy_probe: dict[str, Any] | None = None,
    hf_probe: dict[str, Any] | None = None,
    catalog_error: str = "",
    message: str = "",
) -> dict[str, Any]:
    hf = _as_dict(hf_probe)
    source = _as_dict(hf.get("source"))
    cache = _as_dict(hf.get("cache"))
    return {
        "schema_id": RUNTIME_RESOLVER_SCHEMA_ID,
        "probe_id": "qwen3_tts_model_snapshot",
        "phase": PHASE_ID,
        "model_id": model_id,
        "state": state,
        "installed": state == "installed",
        "source_kind": source_kind,
        "source_path": source_path,
        "legacy_probe": _as_dict(legacy_probe),
        "huggingface_probe": hf,
        "catalog_error": catalog_error,
        "repo_id": _clean(source.get("repo")) or _clean(MODEL_SPECS.get(model_id, {}).get("upstream_model_id")),
        "requested_revision": _clean(source.get("requested_revision")),
        "resolved_revision": _clean(source.get("resolved_revision")),
        "hub_cache": _clean(cache.get("hub_cache")),
        "message": message,
        "policy": {
            "legacy_snapshot_precedence": True,
            "legacy_snapshot_supported": True,
            "legacy_migration_required": False,
            "huggingface_copy_optional_when_legacy_ready": True,
            "huggingface_cache_fallback": True,
            "huggingface_requires_authoritative_probe": True,
            "remote_calls": False,
            "downloads": False,
            "generation_may_download": False,
            "manifest_mutated": False,
        },
    }


def probe_qwen3_tts_runtime_model(
    *,
    project_root: Path,
    voice_runtime_root: Path,
    model_id: str,
    legacy_model_root: Path | None = None,
    cache_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve one Qwen runtime source without network access or mutation.

    Precedence is deliberately stable for existing users:
      1. complete legacy/local Neo_Runtime snapshot;
      2. authoritative Admin-managed Hugging Face cache snapshot.

    A repository id is never returned from this resolver. Therefore managed Voice
    generation cannot turn a missing installation into an implicit Hub download.
    """

    if model_id not in MODEL_SPECS:
        return _runtime_payload(
            model_id=model_id,
            state="not_installed",
            catalog_error="unknown_qwen_model_id",
            message="Unknown Qwen3-TTS model ID.",
        )

    local_probe = probe_model_install(
        Path(voice_runtime_root).resolve(),
        model_id,
        model_root_override=legacy_model_root,
    )
    if _clean(local_probe.get("state")) == "installed":
        path = _clean(local_probe.get("resolved_path"))
        return _runtime_payload(
            model_id=model_id,
            state="installed",
            source_kind="legacy_runtime_snapshot",
            source_path=path,
            legacy_probe=local_probe,
            message="Using the existing Neo Voice runtime snapshot. No Hugging Face migration or re-download is required.",
        )

    record, catalog_error = _catalog_record(Path(project_root), model_id)
    if record is None:
        state = "partial" if _clean(local_probe.get("state")) == "partial" else "not_installed"
        return _runtime_payload(
            model_id=model_id,
            state=state,
            legacy_probe=local_probe,
            catalog_error=catalog_error,
            message="No Admin repository-snapshot catalog binding exists for this Qwen model; only the legacy local snapshot path is eligible.",
        )

    expected_repo = _clean(MODEL_SPECS[model_id].get("upstream_model_id"))
    actual_repo = _clean(_as_dict(record.get("source")).get("repo"))
    if actual_repo != expected_repo:
        return _runtime_payload(
            model_id=model_id,
            state="partial",
            legacy_probe=local_probe,
            catalog_error="catalog_repo_mismatch",
            message="The Admin catalog repository does not match the Qwen Voice model contract; runtime binding was rejected fail-closed.",
        )

    hf_probe = probe_huggingface_repository_snapshot(
        record,
        cache_resolution=cache_resolution or resolve_huggingface_cache(),
    )
    hf_state = _clean(hf_probe.get("state"))
    if hf_state == "installed":
        path = _clean(_as_dict(hf_probe.get("cache")).get("snapshot_path"))
        if path:
            return _runtime_payload(
                model_id=model_id,
                state="installed",
                source_kind="huggingface_cache_snapshot",
                source_path=path,
                legacy_probe=local_probe,
                hf_probe=hf_probe,
                message="Using the authoritative Admin-managed Hugging Face snapshot.",
            )
        hf_state = "corrupt"

    local_state = _clean(local_probe.get("state"))
    if local_state == "partial" or hf_state in {"partial", "stale", "corrupt", "unverified"}:
        state = "partial"
    else:
        state = "not_installed"
    return _runtime_payload(
        model_id=model_id,
        state=state,
        legacy_probe=local_probe,
        hf_probe=hf_probe,
        message="No executable local Qwen snapshot is available. Install or repair the model in Admin → Models.",
    )


def resolve_qwen3_tts_runtime_model_path(**kwargs: Any) -> Path | None:
    probe = probe_qwen3_tts_runtime_model(**kwargs)
    if probe.get("state") != "installed":
        return None
    raw = _clean(probe.get("source_path"))
    if not raw:
        return None
    path = Path(raw).expanduser().resolve()
    return path if path.exists() and path.is_dir() else None


def runtime_registry_snapshot(
    *,
    project_root: Path,
    voice_runtime_root: Path,
    legacy_model_root: Path | None = None,
    cache_resolution: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cache = cache_resolution or resolve_huggingface_cache()
    rows: list[dict[str, Any]] = []
    for model_id, spec in MODEL_SPECS.items():
        runtime = probe_qwen3_tts_runtime_model(
            project_root=project_root,
            voice_runtime_root=voice_runtime_root,
            model_id=model_id,
            legacy_model_root=legacy_model_root,
            cache_resolution=cache,
        )
        rows.append({
            "id": model_id,
            "label": spec["label"],
            "repo_id": spec["upstream_model_id"],
            "role": spec["role"],
            "size_class": spec["size_class"],
            "install": runtime,
            "runtime_source_kind": runtime.get("source_kind") or "",
        })
    return {
        "schema_id": "neo.voice_engine.qwen3_tts.model_registry.v1",
        "phase": PHASE_ID,
        "models": rows,
        "installed": sum(1 for row in rows if row["install"]["state"] == "installed"),
        "partial": sum(1 for row in rows if row["install"]["state"] == "partial"),
        "not_installed": sum(1 for row in rows if row["install"]["state"] == "not_installed"),
        "download_policy": "explicit_admin_or_legacy_only",
        "runtime_binding": "legacy_then_huggingface_cache",
    }
