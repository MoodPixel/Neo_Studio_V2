from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

ROOT_DIR = Path(__file__).resolve().parents[3]
MODEL_PATHS_DIR = ROOT_DIR / "neo_data" / "config"
MODEL_PATHS_PATH = MODEL_PATHS_DIR / "model_paths.json"

MODEL_PATHS_SCHEMA_ID = "neo.admin.models.paths.v1"
MODEL_PATHS_VERSION = "0.9.0-phase9"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _clean_path_value(value: Any) -> str:
    text = str(value or "").strip().strip('"').strip("'")
    if "\x00" in text:
        return ""
    return text


def default_model_paths_payload() -> dict[str, Any]:
    stamp = _now()
    return {
        "schema_id": MODEL_PATHS_SCHEMA_ID,
        "version": MODEL_PATHS_VERSION,
        "created_at": stamp,
        "updated_at": stamp,
        "policy": "User model paths are local runtime settings stored under neo_data and must not be committed to the repo.",
        "backends": {
            "comfyui": {
                "enabled": True,
                "root": "",
                "models_root": "",
                "notes": "Set models_root to the ComfyUI models folder, for example F:/Backends/ComfyUI_windows_portable/ComfyUI/models.",
            },
            "forge": {
                "enabled": False,
                "root": "",
                "models_root": "",
                "notes": "Set models_root to the Forge models folder when Forge support is needed.",
            },
            "koboldcpp": {
                "enabled": True,
                "root": "",
                "models_root": "",
                "notes": "Set models_root to the folder where local LLM GGUF files should be stored.",
            },
            "local_llm": {
                "enabled": True,
                "user_llm_models_root": "",
                "user_embedding_models_root": "",
                "user_reranker_models_root": "",
                "notes": "Generic local model roots for non-backend-specific LLM, embedding, and reranker files.",
            },
        },
        "download": {
            "temp_root": "neo_data/downloads/tmp",
            "completed_root": "neo_data/downloads/completed",
            "failed_root": "neo_data/downloads/failed",
            "notes": "Download folders are used by the Phase 8 Download Manager for temp files, completed history, failed/cancelled jobs, and local job state.",
        },
    }


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return deepcopy(fallback)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(fallback)
    return loaded if isinstance(loaded, dict) else deepcopy(fallback)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _merge_defaults(existing: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    changed = False
    merged = deepcopy(existing)
    for key, value in defaults.items():
        if key not in merged:
            merged[key] = deepcopy(value)
            changed = True
    backends = _as_dict(merged.get("backends"))
    default_backends = _as_dict(defaults.get("backends"))
    for backend_id, default_backend in default_backends.items():
        backend = _as_dict(backends.get(backend_id))
        for key, value in _as_dict(default_backend).items():
            if key not in backend:
                backend[key] = deepcopy(value)
                changed = True
        backends[backend_id] = backend
    merged["backends"] = backends
    download = _as_dict(merged.get("download"))
    for key, value in _as_dict(defaults.get("download")).items():
        if key not in download:
            download[key] = deepcopy(value)
            changed = True
    merged["download"] = download
    if merged.get("schema_id") != MODEL_PATHS_SCHEMA_ID:
        merged["schema_id"] = MODEL_PATHS_SCHEMA_ID
        changed = True
    if merged.get("version") != MODEL_PATHS_VERSION:
        merged["version"] = MODEL_PATHS_VERSION
        changed = True
    return merged, changed


def load_model_paths(*, create: bool = False) -> dict[str, Any]:
    defaults = default_model_paths_payload()
    exists = MODEL_PATHS_PATH.exists()
    payload = _read_json(MODEL_PATHS_PATH, defaults)
    payload, changed = _merge_defaults(payload, defaults)
    if create and (not exists or changed):
        payload["updated_at"] = _now()
        _write_json(MODEL_PATHS_PATH, payload)
    return payload


def update_model_paths_config(update: dict[str, Any] | None) -> dict[str, Any]:
    existing = load_model_paths(create=True)
    update = _as_dict(update)
    backends_update = _as_dict(update.get("backends"))
    backends = _as_dict(existing.get("backends"))
    allowed_backend_keys = {
        "comfyui": {"enabled", "root", "models_root", "notes"},
        "forge": {"enabled", "root", "models_root", "notes"},
        "koboldcpp": {"enabled", "root", "models_root", "notes"},
        "local_llm": {"enabled", "user_llm_models_root", "user_embedding_models_root", "user_reranker_models_root", "notes"},
    }
    for backend_id, allowed_keys in allowed_backend_keys.items():
        incoming = _as_dict(backends_update.get(backend_id))
        if not incoming:
            continue
        current = _as_dict(backends.get(backend_id))
        for key in allowed_keys:
            if key not in incoming:
                continue
            if key == "enabled":
                current[key] = bool(incoming.get(key))
            else:
                current[key] = _clean_path_value(incoming.get(key))
        backends[backend_id] = current
    download_update = _as_dict(update.get("download"))
    if download_update:
        download = _as_dict(existing.get("download"))
        for key in {"temp_root", "completed_root", "failed_root", "notes"}:
            if key in download_update:
                download[key] = _clean_path_value(download_update.get(key))
        existing["download"] = download
    existing["backends"] = backends
    existing["updated_at"] = _now()
    _write_json(MODEL_PATHS_PATH, existing)
    return existing


def admin_model_paths_payload(*, create: bool = False) -> dict[str, Any]:
    payload = load_model_paths(create=create)
    return {
        "schema_id": "neo.admin.models.paths_payload.v1",
        "status": "ready",
        "phase": "phase10_workspace_integration",
        "store": {
            "exists": MODEL_PATHS_PATH.exists(),
            "path": str(MODEL_PATHS_PATH.relative_to(ROOT_DIR)),
            "policy": "local_only_gitignored_neo_data",
        },
        "paths": payload,
        "capabilities": {
            "path_configuration": True,
            "folder_resolution": True,
            "installed_scan": True,
            "downloads": True,
            "remote_metadata": True,
        },
    }


def save_model_paths_payload(update: dict[str, Any] | None) -> dict[str, Any]:
    saved = update_model_paths_config(update)
    return {
        "schema_id": "neo.admin.models.paths_save_payload.v1",
        "status": "saved",
        "phase": "phase10_workspace_integration",
        "store": {
            "exists": MODEL_PATHS_PATH.exists(),
            "path": str(MODEL_PATHS_PATH.relative_to(ROOT_DIR)),
            "policy": "local_only_gitignored_neo_data",
        },
        "paths": saved,
    }
