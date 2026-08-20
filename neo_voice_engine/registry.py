from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

from .config import GatewayConfig
from .manifest import ManifestDocument, ManifestValidationError, load_manifest
from .supervisor import WorkerSpec, WorkerSupervisor


@dataclass(frozen=True)
class RegistryLoadResult:
    manifests: tuple[ManifestDocument, ...]
    errors: tuple[dict[str, Any], ...]


def _source_label(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _resolve_project_path(project_root: Path, relative: str) -> Path | None:
    raw = str(relative or "").strip()
    if not raw:
        return None
    path = (project_root / Path(raw)).resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError(f"Manifest path escaped project root: {relative}") from exc
    return path


def _resolve_scoped_path(config: GatewayConfig, relative: str, scope: str = "project") -> Path | None:
    raw = str(relative or "").strip()
    if not raw:
        return None
    scope_id = str(scope or "project").strip().lower()
    if scope_id == "voice_runtime":
        base = config.runtime_root.resolve()
    elif scope_id == "project":
        base = config.project_root.resolve()
    else:
        raise ValueError(f"Unsupported manifest path scope: {scope}")
    path = (base / Path(raw)).resolve()
    try:
        path.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"Manifest path escaped {scope_id} root: {relative}") from exc
    return path


def _scoped_path_label(relative: str, scope: str) -> str:
    raw = str(relative or "").strip().replace("\\", "/")
    if str(scope or "project").strip().lower() == "voice_runtime":
        return f"voice_runtime://{raw}"
    return raw


def _render(value: str, replacements: dict[str, str]) -> str:
    result = str(value)
    for key, replacement in replacements.items():
        result = result.replace("{" + key + "}", replacement)
    return result


class ManifestRegistry:
    """Durable VO-E3 manifest registry.

    Manifests own public engine/model identity and static metadata. Worker live
    discovery may enrich runtime state, but it may not invent or reroute manifest
    model IDs.
    """

    def __init__(self, config: GatewayConfig) -> None:
        self.config = config
        self._lock = threading.RLock()
        self._manifests: dict[str, ManifestDocument] = {}
        self._models: dict[str, dict[str, Any]] = {}
        self._voices: list[dict[str, Any]] = []
        self._workers: dict[str, WorkerSpec] = {}
        self._engines: dict[str, dict[str, Any]] = {}
        self._errors: list[dict[str, Any]] = []
        self._generation = 0

    def _scan(self) -> RegistryLoadResult:
        documents: list[ManifestDocument] = []
        errors: list[dict[str, Any]] = []
        seen_paths: set[Path] = set()
        for root in self.config.effective_manifest_roots:
            if not root.exists() or not root.is_dir():
                continue
            for path in sorted(root.glob("*.json"), key=lambda item: item.name.lower()):
                resolved = path.resolve()
                if resolved in seen_paths:
                    continue
                seen_paths.add(resolved)
                try:
                    documents.append(load_manifest(resolved))
                except ManifestValidationError as exc:
                    errors.append(
                        {
                            "code": exc.code,
                            "message": exc.message,
                            "field": exc.field,
                            "source": _source_label(resolved, self.config.project_root),
                        }
                    )
                except Exception as exc:  # noqa: BLE001 - malformed plugin file must not crash gateway.
                    errors.append(
                        {
                            "code": "invalid_manifest",
                            "message": str(exc),
                            "field": "",
                            "source": _source_label(resolved, self.config.project_root),
                        }
                    )
        return RegistryLoadResult(tuple(documents), tuple(errors))

    def _manifest_worker_spec(self, document: ManifestDocument) -> WorkerSpec:
        payload = document.payload
        engine = payload["engine"]
        worker = engine["worker"]
        environment = worker["environment"]
        project_root = self.config.project_root.resolve()
        environment_scope = str(environment.get("scope") or "project")
        environment_root = _resolve_scoped_path(self.config, environment.get("root") or "", environment_scope)
        python_path = _resolve_scoped_path(self.config, environment.get("python") or "", environment_scope)
        cwd = _resolve_project_path(project_root, worker.get("cwd") or "") or project_root
        replacements = {
            "project_root": str(project_root),
            "neo_runtime_root": str((self.config.neo_runtime_root or self.config.runtime_root.parent).resolve()),
            "runtime_root": str(self.config.runtime_root.resolve()),
            "voice_runtime_root": str(self.config.runtime_root.resolve()),
            "envs_root": str(self.config.envs_root.resolve()),
            "models_root": str(self.config.models_root.resolve()),
            "cache_root": str(self.config.cache_root.resolve()),
            "temp_root": str(self.config.temp_root.resolve()),
            "state_root": str(self.config.state_root.resolve()),
            "engine_root": str(environment_root or project_root),
            "python": str(python_path or "python"),
            "gateway_host": self.config.host,
            "gateway_port": str(self.config.port),
        }
        command = tuple(_render(str(item), replacements) for item in worker.get("command") or [])
        env = {str(key): _render(str(value), replacements) for key, value in (worker.get("env") or {}).items()}
        mode = str(worker["mode"])
        return WorkerSpec(
            engine_id=str(engine["id"]),
            label=str(engine.get("label") or engine["id"]),
            base_url=str(worker["base_url"]),
            command=command,
            cwd=cwd,
            env=env,
            managed=mode == "managed_process",
            auto_start=bool(worker.get("auto_start", False)),
            health_path=str(worker.get("health_path") or "/api/voice/health"),
            startup_timeout_seconds=float(worker.get("startup_timeout_seconds") or self.config.worker_start_timeout_seconds),
            source="manifest",
            manifest_id=document.manifest_id,
            environment_kind=str(environment.get("kind") or "external"),
            environment_root=str(environment_root or ""),
            python_path=str(python_path or ""),
        )

    def _install_state(self, document: ManifestDocument, model: dict[str, Any]) -> dict[str, Any]:
        worker_install = document.payload["engine"]["worker"]["installation"]
        model_install = model["install"]
        strategy = str(model_install.get("strategy") or worker_install.get("strategy") or "manual")
        if strategy == "external":
            return {
                "strategy": strategy,
                "state": "external",
                "required_paths": [],
                "missing_paths": [],
                "optional_paths": [],
                "expected_size_mb": model_install.get("expected_size_mb"),
            }
        required = list(dict.fromkeys([*(worker_install.get("required_paths") or []), *(model_install.get("required_paths") or [])]))
        optional = list(dict.fromkeys([*(worker_install.get("optional_paths") or []), *(model_install.get("optional_paths") or [])]))
        environment = document.payload["engine"]["worker"]["environment"]
        env_scope = str(environment.get("scope") or "project")
        env_probe = str(environment.get("python") or environment.get("root") or "").strip()
        env_label = _scoped_path_label(env_probe, env_scope) if env_probe and str(environment.get("kind") or "") == "venv" else ""
        required_labels = list(dict.fromkeys(([env_label] if env_label else []) + required))
        existing: list[str] = []
        if env_label:
            resolved_env = _resolve_scoped_path(self.config, env_probe, env_scope)
            if resolved_env is not None and resolved_env.exists():
                existing.append(env_label)
        for item in required:
            resolved = _resolve_project_path(self.config.project_root, item)
            if resolved is not None and resolved.exists():
                existing.append(item)
        missing = [item for item in required_labels if item not in existing]
        if not required_labels:
            state = "installed"
        elif not missing:
            state = "installed"
        elif existing:
            state = "partial"
        else:
            state = "not_installed"
        return {
            "strategy": strategy,
            "state": state,
            "required_paths": required_labels,
            "missing_paths": missing,
            "optional_paths": optional,
            "environment_scope": env_scope,
            "expected_size_mb": model_install.get("expected_size_mb"),
        }

    def reload(self) -> dict[str, Any]:
        scan = self._scan()
        errors = [dict(item) for item in scan.errors]
        documents = [doc for doc in scan.manifests if doc.payload.get("enabled", True)]

        manifest_groups: dict[str, list[ManifestDocument]] = {}
        engine_groups: dict[str, list[ManifestDocument]] = {}
        model_groups: dict[str, list[ManifestDocument]] = {}
        for document in documents:
            manifest_groups.setdefault(document.manifest_id, []).append(document)
            engine_groups.setdefault(document.engine_id, []).append(document)
            for model in document.payload["models"]:
                model_groups.setdefault(str(model["id"]), []).append(document)

        conflicted_manifests = {key for key, values in manifest_groups.items() if len(values) > 1}
        conflicted_engines = {key for key, values in engine_groups.items() if len(values) > 1}
        conflicted_models = {key for key, values in model_groups.items() if len(values) > 1}

        for manifest_id in sorted(conflicted_manifests):
            sources = [_source_label(doc.source_path, self.config.project_root) for doc in manifest_groups[manifest_id]]
            errors.append({"code": "duplicate_manifest_id", "message": f"Duplicate manifest_id '{manifest_id}' was rejected fail-closed.", "manifest_id": manifest_id, "sources": sources})
        for engine_id in sorted(conflicted_engines):
            sources = [_source_label(doc.source_path, self.config.project_root) for doc in engine_groups[engine_id]]
            errors.append({"code": "duplicate_engine_id", "message": f"Duplicate engine_id '{engine_id}' was rejected fail-closed.", "engine_id": engine_id, "sources": sources})
        for model_id in sorted(conflicted_models):
            sources = [_source_label(doc.source_path, self.config.project_root) for doc in model_groups[model_id]]
            errors.append({"code": "duplicate_model_id", "message": f"Duplicate public model_id '{model_id}' was rejected fail-closed.", "model_id": model_id, "sources": sources})

        manifests: dict[str, ManifestDocument] = {}
        workers: dict[str, WorkerSpec] = {}
        engines: dict[str, dict[str, Any]] = {}
        models: dict[str, dict[str, Any]] = {}
        voices: list[dict[str, Any]] = []

        for document in documents:
            if document.manifest_id in conflicted_manifests or document.engine_id in conflicted_engines:
                continue
            model_ids = {str(item["id"]) for item in document.payload["models"]}
            if any(model_id in conflicted_models for model_id in model_ids):
                # Keep non-conflicting models from this engine usable, but never publish the conflicting IDs.
                pass
            manifests[document.manifest_id] = document
            try:
                workers[document.engine_id] = self._manifest_worker_spec(document)
            except Exception as exc:  # noqa: BLE001
                errors.append(
                    {
                        "code": "worker_spec_invalid",
                        "message": str(exc),
                        "manifest_id": document.manifest_id,
                        "engine_id": document.engine_id,
                        "source": _source_label(document.source_path, self.config.project_root),
                    }
                )
                manifests.pop(document.manifest_id, None)
                continue
            worker_payload = document.payload["engine"]["worker"]
            engines[document.engine_id] = {
                "engine_id": document.engine_id,
                "label": str(document.payload["engine"].get("label") or document.engine_id),
                "description": str(document.payload["engine"].get("description") or ""),
                "manifest_id": document.manifest_id,
                "manifest_version": document.payload["manifest_version"],
                "source": _source_label(document.source_path, self.config.project_root),
                "worker": {
                    "mode": worker_payload["mode"],
                    "base_url": worker_payload["base_url"],
                    "auto_start": worker_payload["auto_start"],
                    "health_path": worker_payload["health_path"],
                    "environment": dict(worker_payload["environment"]),
                    "installation": dict(worker_payload["installation"]),
                },
            }
            for model in document.payload["models"]:
                model_id = str(model["id"])
                if model_id in conflicted_models:
                    continue
                install = self._install_state(document, model)
                models[model_id] = {
                    "id": model_id,
                    "engine_id": document.engine_id,
                    "manifest_id": document.manifest_id,
                    "manifest_version": document.payload["manifest_version"],
                    "label": str(model["label"]),
                    "description": str(model.get("description") or ""),
                    "default": bool(model.get("default", False)),
                    "tasks": list(model["tasks"]),
                    "languages": list(model["languages"]),
                    "voice_modes": list(model["voice_modes"]),
                    "output_formats": list(model["output_formats"]),
                    "streaming": bool(model["streaming"]),
                    "reference_audio": bool(model["reference_audio"]),
                    "hardware": dict(model["hardware"]),
                    "lifecycle": dict(model.get("lifecycle") or {}),
                    "license": dict(model["license"]),
                    "sources": [dict(item) for item in model["sources"]],
                    "install": install,
                    "install_state": install["state"],
                    "availability": "installed" if install["state"] == "installed" else install["state"],
                    "tags": list(model.get("tags") or []),
                    "source": "manifest",
                }
            for voice in document.payload.get("voices") or []:
                voices.append(
                    {
                        "id": voice["id"],
                        "label": voice["label"],
                        "engine_id": document.engine_id,
                        "model_ids": list(voice["model_ids"]),
                        "kind": voice["kind"],
                        "languages": list(voice["languages"]),
                        "source": "manifest",
                    }
                )

        with self._lock:
            self._manifests = manifests
            self._workers = workers
            self._engines = engines
            self._models = models
            self._voices = voices
            self._errors = errors
            self._generation += 1
        return self.snapshot()

    def sync_supervisor(self, supervisor: WorkerSupervisor) -> dict[str, Any]:
        with self._lock:
            specs = list(self._workers.values())
        return supervisor.sync_manifest_workers(specs)

    def refresh(self, supervisor: WorkerSupervisor | None = None) -> dict[str, Any]:
        snapshot = self.reload()
        if supervisor is not None:
            snapshot["worker_sync"] = self.sync_supervisor(supervisor)
        return snapshot

    def model(self, model_id: str) -> dict[str, Any] | None:
        with self._lock:
            raw = self._models.get(str(model_id or "").strip())
            return dict(raw) if raw else None

    def models(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for _, value in sorted(self._models.items())]

    def voices(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for value in self._voices]

    def worker_specs(self) -> list[WorkerSpec]:
        with self._lock:
            return list(self._workers.values())

    def manifest_engine_ids(self) -> set[str]:
        with self._lock:
            return set(self._workers)

    def errors(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for value in self._errors]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            roots = [_source_label(root, self.config.project_root) for root in self.config.effective_manifest_roots]
            return {
                "schema_id": "neo.voice_engine.registry.v1",
                "generation": self._generation,
                "manifest_schema_id": "neo.voice_engine.manifest.v1",
                "manifest_roots": roots,
                "manifest_count": len(self._manifests),
                "engine_count": len(self._engines),
                "model_count": len(self._models),
                "voice_count": len(self._voices),
                "manifests": [
                    {
                        "manifest_id": manifest_id,
                        "manifest_version": document.payload["manifest_version"],
                        "engine_id": document.engine_id,
                        "source": _source_label(document.source_path, self.config.project_root),
                    }
                    for manifest_id, document in sorted(self._manifests.items())
                ],
                "engines": [dict(value) for _, value in sorted(self._engines.items())],
                "models": [dict(value) for _, value in sorted(self._models.items())],
                "voices": [dict(value) for value in self._voices],
                "errors": [dict(value) for value in self._errors],
            }
