from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .chatterbox_model_registry import MODEL_SPECS as CHATTERBOX_MODEL_SPECS
from .chatterbox_runtime_resolver import probe_chatterbox_runtime_model
from .qwen3_tts_model_registry import MODEL_SPECS as QWEN_MODEL_SPECS
from .qwen3_tts_runtime_resolver import probe_qwen3_tts_runtime_model
from .runtime_paths import resolve_voice_runtime_paths

VOICE_MODEL_COMPATIBILITY_SCHEMA_ID = "neo.voice_engine.voice_model_compatibility.v1"
PHASE_ID = "phase4_6_1_legacy_voice_model_compatibility"


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _runtime_source_label(source_kind: str) -> str:
    value = _clean(source_kind)
    if value == "legacy_runtime_snapshot":
        return "Legacy Neo Runtime"
    if value == "huggingface_cache_snapshot":
        return "Hugging Face cache"
    return ""


def _unsupported_payload(record: dict[str, Any], *, reason: str, message: str) -> dict[str, Any]:
    model_id = _clean(record.get("id"))
    return {
        "schema_id": VOICE_MODEL_COMPATIBILITY_SCHEMA_ID,
        "phase": PHASE_ID,
        "catalog_id": model_id,
        "state": "not_applicable",
        "installed": False,
        "runtime_available": False,
        "source_kind": "",
        "source_label": "",
        "source_path": "",
        "legacy_compatible": False,
        "reason": reason,
        "message": message,
        "runtime_probe": {},
        "policy": {
            "legacy_local_supported": True,
            "legacy_migration_required": False,
            "huggingface_copy_optional_when_legacy_ready": True,
            "remote_calls": False,
            "downloads": False,
            "generation_may_download": False,
        },
    }


def probe_voice_model_runtime_compatibility(
    *,
    project_root: Path,
    record: dict[str, Any],
    cache_resolution: dict[str, Any] | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return the executable Voice-model truth without downloading or migrating files.

    Phase 4.6.1 deliberately separates two questions:
      1. Is the Admin-managed Hugging Face copy installed?
      2. Can the Voice runtime already execute this model from a supported local source?

    A complete historical Qwen snapshot under Neo_Runtime remains a permanent valid
    source. It is never copied into the Hugging Face cache and never requires a
    network migration. Chatterbox's historical first-use cache is already the
    standard Hugging Face cache, so the Chatterbox resolver continues to reuse it
    in place when its local ref/materialization/content probe is authoritative.
    """

    item = _as_dict(record)
    if _clean(item.get("category")).lower() != "voice":
        return _unsupported_payload(item, reason="not_voice_model", message="The catalog record is not a Voice model.")

    model_id = _clean(item.get("id"))
    base_model = _clean(item.get("base_model")).lower()
    paths = resolve_voice_runtime_paths(Path(project_root), environ=environ)

    if base_model == "qwen" and model_id in QWEN_MODEL_SPECS:
        runtime = probe_qwen3_tts_runtime_model(
            project_root=paths.project_root,
            voice_runtime_root=paths.voice_runtime_root,
            model_id=model_id,
            cache_resolution=cache_resolution,
        )
    elif base_model == "chatterbox" and model_id in CHATTERBOX_MODEL_SPECS:
        runtime = probe_chatterbox_runtime_model(
            project_root=paths.project_root,
            model_id=model_id,
            cache_resolution=cache_resolution,
        )
    else:
        return _unsupported_payload(
            item,
            reason="voice_runtime_binding_unavailable",
            message="This Voice catalog record does not yet have a unified local runtime resolver.",
        )

    source_kind = _clean(runtime.get("source_kind"))
    installed = bool(runtime.get("installed")) and _clean(runtime.get("state")) == "installed"
    legacy = installed and source_kind == "legacy_runtime_snapshot"
    source_label = _runtime_source_label(source_kind)
    if legacy:
        message = "Runtime-ready from the existing Neo_Runtime model snapshot. No Hugging Face migration or re-download is required."
    elif installed and source_kind == "huggingface_cache_snapshot":
        message = "Runtime-ready from the verified local Hugging Face cache snapshot."
    else:
        message = _clean(runtime.get("message")) or "No executable local Voice model source is currently available."

    return {
        "schema_id": VOICE_MODEL_COMPATIBILITY_SCHEMA_ID,
        "phase": PHASE_ID,
        "catalog_id": model_id,
        "state": "installed" if installed else _clean(runtime.get("state")) or "not_installed",
        "installed": installed,
        "runtime_available": installed,
        "source_kind": source_kind,
        "source_label": source_label,
        "source_path": _clean(runtime.get("source_path")),
        "legacy_compatible": legacy,
        "reason": "legacy_runtime_snapshot" if legacy else ("huggingface_cache_snapshot" if installed else "runtime_not_installed"),
        "message": message,
        "runtime_probe": runtime,
        "paths": {
            "voice_runtime_root": str(paths.voice_runtime_root),
            "legacy_qwen_root": str(paths.voice_runtime_root / "models" / "qwen3_tts") if base_model == "qwen" else "",
        },
        "policy": {
            "legacy_local_supported": True,
            "legacy_migration_required": False,
            "huggingface_copy_optional_when_legacy_ready": True,
            "remote_calls": False,
            "downloads": False,
            "generation_may_download": False,
        },
    }
