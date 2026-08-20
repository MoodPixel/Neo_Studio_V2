from __future__ import annotations

from dataclasses import dataclass
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


MANIFEST_SCHEMA_ID = "neo.voice_engine.manifest.v1"
PUBLIC_TASKS = {
    "tts",
    "voice_clone",
    "voice_design",
    "voice_conversion",
    "realtime_voice_conversion",
    "singing_voice_conversion",
}
WORKER_MODES = {"external_http", "managed_process"}
INSTALL_STRATEGIES = {"external", "managed", "manual", "bundled"}
ENVIRONMENT_KINDS = {"external", "venv", "python", "system"}
ENVIRONMENT_SCOPES = {"project", "voice_runtime"}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")
_FORMAT_RE = re.compile(r"^[a-z0-9][a-z0-9._+-]{0,15}$")


class ManifestValidationError(ValueError):
    def __init__(self, message: str, *, field: str = "", code: str = "invalid_manifest") -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code


@dataclass(frozen=True)
class ManifestDocument:
    source_path: Path
    payload: dict[str, Any]

    @property
    def manifest_id(self) -> str:
        return str(self.payload["manifest_id"])

    @property
    def engine_id(self) -> str:
        return str(self.payload["engine"]["id"])


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestValidationError(f"{field} must be an object.", field=field)
    return value


def _require_list(value: Any, field: str) -> list[Any]:
    if not isinstance(value, list):
        raise ManifestValidationError(f"{field} must be an array.", field=field)
    return value


def _id(value: Any, field: str) -> str:
    raw = str(value or "").strip().lower()
    if not _ID_RE.fullmatch(raw):
        raise ManifestValidationError(
            f"{field} must be a stable lowercase ID using letters, numbers, '.', '_' or '-'.",
            field=field,
        )
    return raw


def _string(value: Any, field: str, *, required: bool = False, default: str = "") -> str:
    raw = str(value if value is not None else default).strip()
    if required and not raw:
        raise ManifestValidationError(f"{field} is required.", field=field)
    return raw


def _string_list(value: Any, field: str, *, lower: bool = False) -> list[str]:
    if value is None:
        return []
    items = _require_list(value, field)
    normalized: list[str] = []
    for index, item in enumerate(items):
        raw = str(item or "").strip()
        if not raw:
            raise ManifestValidationError(f"{field}[{index}] cannot be empty.", field=f"{field}[{index}]")
        if lower:
            raw = raw.lower()
        if raw not in normalized:
            normalized.append(raw)
    return normalized


def _relative_path(value: Any, field: str, *, allow_empty: bool = True) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    if not raw and allow_empty:
        return ""
    if not raw:
        raise ManifestValidationError(f"{field} is required.", field=field)
    path = Path(raw)
    windows_drive = bool(re.match(r"^[A-Za-z]:/", raw))
    unc_path = raw.startswith("//")
    if path.is_absolute() or raw.startswith("~") or windows_drive or unc_path:
        raise ManifestValidationError(
            f"{field} must be project-relative; machine-specific absolute paths belong in runtime configuration, not public manifests.",
            field=field,
        )
    if any(part == ".." for part in path.parts):
        raise ManifestValidationError(f"{field} may not escape the project root.", field=field)
    return path.as_posix()


def _relative_paths(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    items = _require_list(value, field)
    result: list[str] = []
    for index, item in enumerate(items):
        normalized = _relative_path(item, f"{field}[{index}]", allow_empty=False)
        if normalized not in result:
            result.append(normalized)
    return result


def _nonnegative_int(value: Any, field: str) -> int | None:
    if value is None or value == "":
        return None
    try:
        result = int(value)
    except Exception as exc:
        raise ManifestValidationError(f"{field} must be an integer.", field=field) from exc
    if result < 0:
        raise ManifestValidationError(f"{field} must be zero or greater.", field=field)
    return result


def _positive_float(value: Any, field: str, default: float) -> float:
    if value in (None, ""):
        return default
    try:
        result = float(value)
    except Exception as exc:
        raise ManifestValidationError(f"{field} must be numeric.", field=field) from exc
    if result <= 0:
        raise ManifestValidationError(f"{field} must be greater than zero.", field=field)
    return result


def _tri_state(value: Any) -> bool | str:
    if isinstance(value, bool):
        return value
    raw = str(value or "unknown").strip().lower()
    if raw in {"true", "yes", "allowed", "permitted"}:
        return True
    if raw in {"false", "no", "disallowed", "prohibited"}:
        return False
    return "unknown"


def _validate_loopback_url(value: Any, field: str) -> str:
    raw = _string(value, field, required=True)
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ManifestValidationError(f"{field} must be an http(s) URL.", field=field)
    hostname = parsed.hostname.lower()
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ManifestValidationError(
            f"{field} must point to a loopback worker. Remote worker transport is not part of manifest v1.",
            field=field,
        )
    return raw.rstrip("/")


def _normalize_environment(raw: Any, field: str) -> dict[str, Any]:
    data = _require_object(raw or {}, field)
    kind = str(data.get("kind") or "external").strip().lower()
    if kind not in ENVIRONMENT_KINDS:
        raise ManifestValidationError(f"{field}.kind must be one of {sorted(ENVIRONMENT_KINDS)}.", field=f"{field}.kind")
    scope = str(data.get("scope") or "project").strip().lower()
    if scope not in ENVIRONMENT_SCOPES:
        raise ManifestValidationError(
            f"{field}.scope must be one of {sorted(ENVIRONMENT_SCOPES)}.",
            field=f"{field}.scope",
        )
    return {
        "kind": kind,
        "scope": scope,
        "root": _relative_path(data.get("root"), f"{field}.root"),
        "python": _relative_path(data.get("python"), f"{field}.python"),
    }


def _normalize_install(raw: Any, field: str, *, default_strategy: str) -> dict[str, Any]:
    data = _require_object(raw or {}, field)
    strategy = str(data.get("strategy") or default_strategy).strip().lower()
    if strategy not in INSTALL_STRATEGIES:
        raise ManifestValidationError(f"{field}.strategy must be one of {sorted(INSTALL_STRATEGIES)}.", field=f"{field}.strategy")
    return {
        "strategy": strategy,
        "required_paths": _relative_paths(data.get("required_paths"), f"{field}.required_paths"),
        "optional_paths": _relative_paths(data.get("optional_paths"), f"{field}.optional_paths"),
        "expected_size_mb": _nonnegative_int(data.get("expected_size_mb"), f"{field}.expected_size_mb"),
    }


def _normalize_worker(raw: Any, field: str) -> dict[str, Any]:
    data = _require_object(raw, field)
    mode = str(data.get("mode") or "external_http").strip().lower()
    if mode not in WORKER_MODES:
        raise ManifestValidationError(f"{field}.mode must be one of {sorted(WORKER_MODES)}.", field=f"{field}.mode")
    command = _string_list(data.get("command"), f"{field}.command")
    if mode == "managed_process" and not command:
        raise ManifestValidationError(f"{field}.command is required for a managed_process worker.", field=f"{field}.command")
    env_data = data.get("env") or {}
    if not isinstance(env_data, dict):
        raise ManifestValidationError(f"{field}.env must be an object.", field=f"{field}.env")
    env: dict[str, str] = {}
    for key, value in env_data.items():
        env_key = str(key or "").strip()
        if not env_key:
            raise ManifestValidationError(f"{field}.env keys cannot be empty.", field=f"{field}.env")
        env[env_key] = str(value)
    health_path = str(data.get("health_path") or "/api/voice/health").strip()
    if not health_path.startswith("/"):
        raise ManifestValidationError(f"{field}.health_path must begin with '/'.", field=f"{field}.health_path")
    auto_start = bool(data.get("auto_start", False))
    if mode != "managed_process" and auto_start:
        raise ManifestValidationError(f"{field}.auto_start is only valid for managed_process workers.", field=f"{field}.auto_start")
    return {
        "mode": mode,
        "base_url": _validate_loopback_url(data.get("base_url"), f"{field}.base_url"),
        "health_path": health_path,
        "command": command,
        "cwd": _relative_path(data.get("cwd"), f"{field}.cwd"),
        "env": env,
        "auto_start": auto_start,
        "startup_timeout_seconds": _positive_float(data.get("startup_timeout_seconds"), f"{field}.startup_timeout_seconds", 20.0),
        "environment": _normalize_environment(data.get("environment"), f"{field}.environment"),
        "installation": _normalize_install(
            data.get("installation"),
            f"{field}.installation",
            default_strategy="external" if mode == "external_http" else "managed",
        ),
    }


def _normalize_hardware(raw: Any, field: str) -> dict[str, Any]:
    data = _require_object(raw or {}, field)
    return {
        "cpu": bool(data.get("cpu", False)),
        "cuda": bool(data.get("cuda", False)),
        "rocm": bool(data.get("rocm", False)),
        "mps": bool(data.get("mps", False)),
        "directml": bool(data.get("directml", False)),
        "xpu": bool(data.get("xpu", False)),
        "min_vram_mb": _nonnegative_int(data.get("min_vram_mb"), f"{field}.min_vram_mb"),
        "recommended_vram_mb": _nonnegative_int(data.get("recommended_vram_mb"), f"{field}.recommended_vram_mb"),
        "min_ram_mb": _nonnegative_int(data.get("min_ram_mb"), f"{field}.min_ram_mb"),
        "gpu_exclusive": bool(data.get("gpu_exclusive", bool(data.get("cuda", False)))),
        "allow_cpu_fallback": bool(data.get("allow_cpu_fallback", bool(data.get("cpu", False)))),
    }


def _normalize_lifecycle(raw: Any, field: str) -> dict[str, Any]:
    data = _require_object(raw or {}, field)
    strategy = str(data.get("unload_strategy") or "auto").strip().lower()
    if strategy not in {"auto", "worker_api", "stop_worker"}:
        raise ManifestValidationError(
            f"{field}.unload_strategy must be one of ['auto', 'stop_worker', 'worker_api'].",
            field=f"{field}.unload_strategy",
        )
    idle = _nonnegative_int(data.get("idle_unload_seconds"), f"{field}.idle_unload_seconds")
    return {
        "evictable": bool(data.get("evictable", True)),
        "idle_unload_seconds": idle,
        "unload_strategy": strategy,
    }


def _normalize_license(raw: Any, field: str) -> dict[str, Any]:
    data = _require_object(raw or {}, field)
    return {
        "name": str(data.get("name") or "unknown").strip() or "unknown",
        "spdx_id": str(data.get("spdx_id") or "").strip(),
        "commercial_use": _tri_state(data.get("commercial_use")),
        "redistribution": _tri_state(data.get("redistribution")),
        "requires_user_acceptance": bool(data.get("requires_user_acceptance", False)),
        "url": str(data.get("url") or "").strip(),
    }


def _normalize_sources(raw: Any, field: str) -> list[dict[str, Any]]:
    if raw is None:
        return []
    items = _require_list(raw, field)
    result: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        data = _require_object(item, f"{field}[{index}]")
        kind = str(data.get("kind") or "other").strip().lower()
        repo_id = str(data.get("repo_id") or "").strip()
        url = str(data.get("url") or "").strip()
        if not repo_id and not url:
            raise ManifestValidationError(
                f"{field}[{index}] must provide repo_id or url.",
                field=f"{field}[{index}]",
            )
        result.append(
            {
                "kind": kind,
                "repo_id": repo_id,
                "url": url,
                "revision": str(data.get("revision") or "").strip(),
                "filename": str(data.get("filename") or "").strip(),
            }
        )
    return result


def _normalize_model(raw: Any, field: str, engine_install_strategy: str) -> dict[str, Any]:
    data = _require_object(raw, field)
    model_id = _id(data.get("id"), f"{field}.id")
    tasks = _string_list(data.get("tasks"), f"{field}.tasks", lower=True)
    if not tasks:
        raise ManifestValidationError(f"{field}.tasks must contain at least one task.", field=f"{field}.tasks")
    unsupported = [task for task in tasks if task not in PUBLIC_TASKS]
    if unsupported:
        raise ManifestValidationError(
            f"{field}.tasks contains unsupported task(s): {', '.join(unsupported)}.",
            field=f"{field}.tasks",
        )
    reference_audio = bool(data.get("reference_audio", "voice_clone" in tasks))
    if "voice_clone" in tasks and not reference_audio:
        raise ManifestValidationError(
            f"{field}.reference_audio must be true when voice_clone is declared.",
            field=f"{field}.reference_audio",
        )
    output_formats = _string_list(data.get("output_formats") or ["wav"], f"{field}.output_formats", lower=True)
    for index, fmt in enumerate(output_formats):
        if not _FORMAT_RE.fullmatch(fmt):
            raise ManifestValidationError(f"{field}.output_formats[{index}] is invalid.", field=f"{field}.output_formats[{index}]")
    return {
        "id": model_id,
        "label": str(data.get("label") or model_id).strip() or model_id,
        "description": str(data.get("description") or "").strip(),
        "default": bool(data.get("default", False)),
        "tasks": tasks,
        "languages": _string_list(data.get("languages"), f"{field}.languages"),
        "voice_modes": _string_list(data.get("voice_modes"), f"{field}.voice_modes", lower=True),
        "output_formats": output_formats,
        "streaming": bool(data.get("streaming", False)),
        "reference_audio": reference_audio,
        "hardware": _normalize_hardware(data.get("hardware"), f"{field}.hardware"),
        "lifecycle": _normalize_lifecycle(data.get("lifecycle"), f"{field}.lifecycle"),
        "license": _normalize_license(data.get("license"), f"{field}.license"),
        "sources": _normalize_sources(data.get("sources"), f"{field}.sources"),
        "install": _normalize_install(data.get("install"), f"{field}.install", default_strategy=engine_install_strategy),
        "tags": _string_list(data.get("tags"), f"{field}.tags", lower=True),
    }


def _normalize_voice(raw: Any, field: str, model_ids: set[str]) -> dict[str, Any]:
    data = _require_object(raw, field)
    voice_id = _id(data.get("id"), f"{field}.id")
    supported_models = _string_list(data.get("model_ids"), f"{field}.model_ids", lower=True)
    unknown = [model_id for model_id in supported_models if model_id not in model_ids]
    if unknown:
        raise ManifestValidationError(
            f"{field}.model_ids references unknown model(s): {', '.join(unknown)}.",
            field=f"{field}.model_ids",
        )
    return {
        "id": voice_id,
        "label": str(data.get("label") or voice_id).strip() or voice_id,
        "model_ids": supported_models,
        "kind": str(data.get("kind") or "preset").strip().lower(),
        "languages": _string_list(data.get("languages"), f"{field}.languages"),
    }


def normalize_manifest(payload: Any, *, source_path: Path) -> ManifestDocument:
    root = _require_object(payload, "manifest")
    schema_id = str(root.get("schema_id") or "").strip()
    if schema_id != MANIFEST_SCHEMA_ID:
        raise ManifestValidationError(
            f"schema_id must be '{MANIFEST_SCHEMA_ID}'.",
            field="schema_id",
            code="unsupported_manifest_schema",
        )
    manifest_id = _id(root.get("manifest_id"), "manifest_id")
    manifest_version = _string(root.get("manifest_version"), "manifest_version", required=True)
    engine_raw = _require_object(root.get("engine"), "engine")
    engine_id = _id(engine_raw.get("id"), "engine.id")
    worker = _normalize_worker(engine_raw.get("worker"), "engine.worker")
    models_raw = _require_list(root.get("models") or [], "models")
    models: list[dict[str, Any]] = []
    local_model_ids: set[str] = set()
    default_count = 0
    for index, item in enumerate(models_raw):
        model = _normalize_model(item, f"models[{index}]", worker["installation"]["strategy"])
        if model["id"] in local_model_ids:
            raise ManifestValidationError(
                f"Duplicate model ID '{model['id']}' within manifest '{manifest_id}'.",
                field=f"models[{index}].id",
                code="duplicate_model_id",
            )
        local_model_ids.add(model["id"])
        default_count += int(model["default"])
        models.append(model)
    if default_count > 1:
        raise ManifestValidationError("A manifest may declare at most one default model.", field="models", code="duplicate_default_model")

    voices_raw = _require_list(root.get("voices") or [], "voices")
    voices: list[dict[str, Any]] = []
    local_voice_ids: set[str] = set()
    for index, item in enumerate(voices_raw):
        voice = _normalize_voice(item, f"voices[{index}]", local_model_ids)
        if voice["id"] in local_voice_ids:
            raise ManifestValidationError(
                f"Duplicate voice ID '{voice['id']}' within manifest '{manifest_id}'.",
                field=f"voices[{index}].id",
                code="duplicate_voice_id",
            )
        local_voice_ids.add(voice["id"])
        voices.append(voice)

    normalized = {
        "schema_id": MANIFEST_SCHEMA_ID,
        "manifest_id": manifest_id,
        "manifest_version": manifest_version,
        "enabled": bool(root.get("enabled", True)),
        "engine": {
            "id": engine_id,
            "label": str(engine_raw.get("label") or engine_id).strip() or engine_id,
            "description": str(engine_raw.get("description") or "").strip(),
            "worker": worker,
        },
        "models": models,
        "voices": voices,
        "notes": _string_list(root.get("notes"), "notes"),
    }
    return ManifestDocument(source_path=source_path, payload=normalized)


def load_manifest(path: Path) -> ManifestDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestValidationError(
            f"Manifest JSON could not be parsed: {exc.msg} (line {exc.lineno}, column {exc.colno}).",
            field="json",
            code="manifest_json_invalid",
        ) from exc
    except OSError as exc:
        raise ManifestValidationError(f"Manifest could not be read: {exc}", field="file", code="manifest_read_failed") from exc
    return normalize_manifest(raw, source_path=path)
