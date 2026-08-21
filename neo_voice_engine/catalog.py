from __future__ import annotations

import threading
from typing import Any

from .errors import VoiceEngineError
from .registry import ManifestRegistry
from .supervisor import WorkerSupervisor


_READY_STATES = {"ready", "connected", "ok", "healthy"}


class GatewayCatalog:
    """Manifest-authoritative catalogue with VO-E2 legacy worker fallback.

    VO-E3 manifests own stable engine/model routing. Live worker discovery can enrich
    declared records and can still serve manually registered legacy workers during the
    migration window, but it may not invent model IDs for a manifest-owned engine.
    """

    def __init__(self, supervisor: WorkerSupervisor, registry: ManifestRegistry | None = None) -> None:
        self.supervisor = supervisor
        self.registry = registry
        self._lock = threading.RLock()
        self._models: dict[str, dict[str, Any]] = {}
        self._voices: list[dict[str, Any]] = []
        self._errors: list[dict[str, Any]] = []

    @staticmethod
    def _normalize_tasks(item: dict[str, Any]) -> list[str]:
        tasks = [str(value).strip() for value in (item.get("tasks") or []) if str(value).strip()]
        if not tasks:
            tasks.append("tts")
            if item.get("voice_clone") is True or item.get("reference_audio") is True:
                tasks.append("voice_clone")
        return list(dict.fromkeys(tasks))

    @staticmethod
    def _normalize_model(engine_id: str, item: dict[str, Any]) -> dict[str, Any] | None:
        model_id = str(item.get("id") or item.get("model_id") or item.get("name") or "").strip()
        if not model_id:
            return None
        tasks = GatewayCatalog._normalize_tasks(item)
        output_formats = [str(value).lower() for value in (item.get("output_formats") or ["wav", "mp3"]) if str(value).strip()]
        languages = [str(value) for value in (item.get("languages") or []) if str(value).strip()]
        voice_modes = [str(value) for value in (item.get("voice_modes") or []) if str(value).strip()]
        if "voice_clone" in tasks and "reference_clone" not in voice_modes:
            voice_modes.append("reference_clone")
        hardware = item.get("hardware") if isinstance(item.get("hardware"), dict) else {}
        license_info = item.get("license") if isinstance(item.get("license"), dict) else {}
        return {
            "id": model_id,
            "engine_id": engine_id,
            "label": str(item.get("label") or item.get("name") or model_id),
            "tasks": tasks,
            "languages": languages,
            "voice_modes": voice_modes,
            "output_formats": output_formats or ["wav"],
            "availability": str(item.get("availability") or "installed"),
            "install_state": str(item.get("install_state") or "external"),
            "streaming": bool(item.get("streaming", False)),
            "reference_audio": bool(item.get("reference_audio", item.get("voice_clone", "voice_clone" in tasks))),
            "hardware": {
                "cpu": bool(hardware.get("cpu", False)),
                "cuda": bool(hardware.get("cuda", False)),
                "rocm": bool(hardware.get("rocm", False)),
                "mps": bool(hardware.get("mps", False)),
                "directml": bool(hardware.get("directml", False)),
                "xpu": bool(hardware.get("xpu", False)),
                "min_vram_mb": hardware.get("min_vram_mb"),
                "recommended_vram_mb": hardware.get("recommended_vram_mb"),
                "min_ram_mb": hardware.get("min_ram_mb"),
                "gpu_exclusive": bool(hardware.get("gpu_exclusive", bool(hardware.get("cuda", False)))),
                "allow_cpu_fallback": bool(hardware.get("allow_cpu_fallback", bool(hardware.get("cpu", False)))),
            },
            "lifecycle": {
                "evictable": bool((item.get("lifecycle") or {}).get("evictable", True)) if isinstance(item.get("lifecycle"), dict) else True,
                "idle_unload_seconds": (item.get("lifecycle") or {}).get("idle_unload_seconds") if isinstance(item.get("lifecycle"), dict) else None,
                "unload_strategy": str((item.get("lifecycle") or {}).get("unload_strategy") or "auto") if isinstance(item.get("lifecycle"), dict) else "auto",
            },
            "license": {
                "name": str(license_info.get("name") or "unknown"),
                "spdx_id": str(license_info.get("spdx_id") or ""),
                "commercial_use": license_info.get("commercial_use", "unknown"),
                "redistribution": license_info.get("redistribution", "unknown"),
                "requires_user_acceptance": bool(license_info.get("requires_user_acceptance", False)),
                "url": str(license_info.get("url") or ""),
            },
            "sources": [dict(value) for value in (item.get("sources") or []) if isinstance(value, dict)],
            "source": "worker_live_discovery_legacy",
            "runtime": {"state": "ready", "executable": True, "managed": False, "auto_start": False},
        }

    def _manifest_runtime_overlay(self, model: dict[str, Any], worker: dict[str, Any]) -> dict[str, Any]:
        result = dict(model)
        install_state = str(result.get("install_state") or (result.get("install") or {}).get("state") or "not_installed")
        worker_state = str(worker.get("state") or "stopped").lower()
        managed = bool(worker.get("managed", False))
        auto_start = bool(worker.get("auto_start", False))
        startup_policy = str(worker.get("startup_policy") or ("on_demand" if auto_start else "manual")).lower()
        installed = install_state in {"installed", "external"}
        executable = installed and (worker_state == "ready" or (managed and startup_policy == "on_demand"))
        if install_state == "partial":
            availability = "partial"
        elif install_state == "not_installed":
            availability = "not_installed"
        elif executable:
            availability = "installed"
        else:
            availability = "unavailable"
        result["availability"] = availability
        result["runtime"] = {
            "state": worker_state,
            "executable": executable,
            "managed": managed,
            "auto_start": auto_start,
            "startup_policy": startup_policy,
            "install_state": str((result.get("install") or {}).get("runtime_state") or ""),
        }
        return result

    def refresh(self) -> dict[str, Any]:
        models: dict[str, dict[str, Any]] = {}
        voices: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        manifest_engine_ids: set[str] = set()

        if self.registry is not None:
            registry_snapshot = self.registry.refresh(self.supervisor)
            errors.extend([dict(item) for item in registry_snapshot.get("errors") or []])
            sync = registry_snapshot.get("worker_sync") if isinstance(registry_snapshot.get("worker_sync"), dict) else {}
            errors.extend([dict(item) for item in sync.get("conflicts") or []])
            manifest_engine_ids = self.registry.manifest_engine_ids()
            worker_public: dict[str, dict[str, Any]] = {}
            for engine_id in sorted(manifest_engine_ids):
                try:
                    self.supervisor.probe(engine_id)
                    worker_public[engine_id] = self.supervisor.public_worker(engine_id)
                except Exception as exc:  # noqa: BLE001
                    errors.append({"code": "worker_probe_failed", "engine_id": engine_id, "message": str(exc)})
                    worker_public[engine_id] = {"engine_id": engine_id, "state": "failed", "managed": False, "auto_start": False}

            for model in self.registry.models():
                engine_id = str(model["engine_id"])
                worker = worker_public.get(engine_id) or {"state": "unregistered", "managed": False, "auto_start": False, "startup_policy": "manual"}
                models[str(model["id"])] = self._manifest_runtime_overlay(model, worker)

            voices.extend(self.registry.voices())

            # Runtime drift is diagnostic only. Manifests remain authoritative.
            for engine_id in sorted(manifest_engine_ids):
                worker = worker_public.get(engine_id) or {}
                if str(worker.get("state") or "").lower() != "ready":
                    continue
                client = self.supervisor.client(engine_id)
                try:
                    raw_models = client.models()
                    items = raw_models.get("models") or raw_models.get("items") or []
                    live_ids = {
                        str((item.get("id") or item.get("model_id") or item.get("name")) if isinstance(item, dict) else item).strip()
                        for item in items if str((item.get("id") or item.get("model_id") or item.get("name")) if isinstance(item, dict) else item).strip()
                    }
                    declared_ids = {model_id for model_id, model in models.items() if model.get("engine_id") == engine_id}
                    for model_id in sorted(live_ids - declared_ids):
                        errors.append({"code": "undeclared_worker_model_ignored", "engine_id": engine_id, "model_id": model_id, "message": "Worker advertised a model not declared by its manifest; the model was ignored."})
                except Exception as exc:  # noqa: BLE001
                    errors.append({"code": "model_discovery_failed", "engine_id": engine_id, "message": str(exc)})
                try:
                    raw_voices = client.voices()
                    voice_items = raw_voices.get("voices") or raw_voices.get("items") or []
                    existing_voice_keys = {(str(item.get("engine_id") or ""), str(item.get("id") or "")) for item in voices}
                    declared_model_ids = {model_id for model_id, model in models.items() if model.get("engine_id") == engine_id}
                    for raw in voice_items if isinstance(voice_items, list) else []:
                        if not isinstance(raw, dict):
                            raw = {"id": str(raw), "label": str(raw)}
                        voice_id = str(raw.get("id") or raw.get("voice_id") or raw.get("name") or "").strip()
                        if not voice_id or (engine_id, voice_id) in existing_voice_keys:
                            continue
                        model_ids = [str(value) for value in (raw.get("model_ids") or []) if str(value).strip() in declared_model_ids]
                        voices.append({
                            "id": voice_id,
                            "label": str(raw.get("label") or raw.get("name") or voice_id),
                            "engine_id": engine_id,
                            "model_ids": model_ids,
                            "kind": str(raw.get("kind") or "preset"),
                            "languages": [str(value) for value in (raw.get("languages") or []) if str(value).strip()],
                            "source": "worker_live_enrichment",
                        })
                        existing_voice_keys.add((engine_id, voice_id))
                except Exception as exc:  # noqa: BLE001
                    errors.append({"code": "voice_discovery_failed", "engine_id": engine_id, "message": str(exc)})

        # VO-E2 compatibility lane: manually registered workers can still be discovered
        # until their VO-E5+ manifest migration is complete.
        for engine_id in self.supervisor.engine_ids():
            if engine_id in manifest_engine_ids:
                continue
            health = self.supervisor.probe(engine_id)
            if str(health.get("status") or "").lower() not in _READY_STATES:
                continue
            client = self.supervisor.client(engine_id)
            try:
                raw_models = client.models()
                items = raw_models.get("models") or raw_models.get("items") or []
                for raw in items if isinstance(items, list) else []:
                    if not isinstance(raw, dict):
                        raw = {"id": str(raw), "label": str(raw)}
                    normalized = self._normalize_model(engine_id, raw)
                    if not normalized:
                        continue
                    model_id = normalized["id"]
                    existing = models.get(model_id)
                    if existing and existing["engine_id"] != engine_id:
                        errors.append({"code": "duplicate_model_id", "model_id": model_id, "engine_ids": [existing["engine_id"], engine_id]})
                        models.pop(model_id, None)
                        continue
                    if not any(err.get("model_id") == model_id and err.get("code") == "duplicate_model_id" for err in errors):
                        models[model_id] = normalized
            except Exception as exc:  # noqa: BLE001 - one worker must not poison all discovery.
                errors.append({"code": "model_discovery_failed", "engine_id": engine_id, "message": str(exc)})
            try:
                raw_voices = client.voices()
                voice_items = raw_voices.get("voices") or raw_voices.get("items") or []
                for raw in voice_items if isinstance(voice_items, list) else []:
                    if not isinstance(raw, dict):
                        raw = {"id": str(raw), "label": str(raw)}
                    voice_id = str(raw.get("id") or raw.get("voice_id") or raw.get("name") or "").strip()
                    if not voice_id:
                        continue
                    voices.append({
                        "id": voice_id,
                        "label": str(raw.get("label") or raw.get("name") or voice_id),
                        "engine_id": engine_id,
                        "model_ids": [str(value) for value in (raw.get("model_ids") or []) if str(value).strip()],
                        "kind": str(raw.get("kind") or "preset"),
                        "languages": [str(value) for value in (raw.get("languages") or []) if str(value).strip()],
                        "source": "worker_live_discovery_legacy",
                    })
            except Exception as exc:  # noqa: BLE001
                errors.append({"code": "voice_discovery_failed", "engine_id": engine_id, "message": str(exc)})

        with self._lock:
            self._models = models
            self._voices = voices
            self._errors = errors
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "models": [dict(value) for _, value in sorted(self._models.items())],
                "voices": [dict(value) for value in self._voices],
                "errors": [dict(value) for value in self._errors],
            }

    def executable_models(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(value) for value in self._models.values() if bool((value.get("runtime") or {}).get("executable", False))]

    def resolve_model(self, model_id: str, *, refresh: bool = True) -> dict[str, Any]:
        requested = str(model_id or "").strip()
        if not requested or requested == "provider_default":
            raise VoiceEngineError(
                "unsupported_model",
                "Neo Voice Engine requires an explicit public model_id until a default model is registered.",
                http_status=400,
            )
        if refresh:
            self.refresh()
        with self._lock:
            model = dict(self._models.get(requested) or {})
        if not model:
            raise VoiceEngineError("unsupported_model", f"Voice model '{requested}' is not available in the gateway catalogue.", http_status=400)
        install = model.get("install") if isinstance(model.get("install"), dict) else {}
        install_state = str(model.get("install_state") or "")
        runtime_state = str(install.get("runtime_state") or install_state)
        model_state = str(install.get("model_state") or install_state)
        runtime_install = install.get("runtime") if isinstance(install.get("runtime"), dict) else {}
        runtime_has_authoritative_probe = bool(runtime_install.get("probes"))
        if runtime_state not in {"installed", "external"} and runtime_has_authoritative_probe:
            raise VoiceEngineError(
                "dependency_missing",
                f"Voice runtime for model '{requested}' is not fully installed.",
                details={
                    "model_id": requested,
                    "engine_id": model.get("engine_id"),
                    "runtime_install_state": runtime_state,
                    "model_install_state": model_state,
                    "install": install,
                },
                http_status=409,
            )
        if model_state not in {"installed", "external"}:
            raise VoiceEngineError(
                "model_not_installed",
                f"Voice model '{requested}' is declared but its local model snapshot is not fully installed.",
                details={
                    "model_id": requested,
                    "runtime_install_state": runtime_state,
                    "model_install_state": model_state,
                    "install": install,
                    "install_state": install_state,
                },
                http_status=409,
            )
        if not bool((model.get("runtime") or {}).get("executable", True)):
            raise VoiceEngineError(
                "worker_unavailable",
                f"Voice model '{requested}' is installed but its worker is not currently executable.",
                retryable=True,
                details={"model_id": requested, "engine_id": model.get("engine_id"), "runtime": model.get("runtime") or {}},
                http_status=503,
            )
        return model

    def controls(self, model_id: str, mode: str) -> dict[str, Any]:
        requested_model_id = str(model_id or "").strip()
        requested_mode = str(mode or "tts").strip().lower() or "tts"

        # Provider-control discovery is metadata discovery, not model execution.
        # For manifest-owned models, read the already-loaded registry first so a
        # static control contract never depends on a full catalogue refresh,
        # install-state probe, worker health, or worker startup.
        manifest_model = self.registry.model(requested_model_id) if self.registry is not None else None
        if manifest_model:
            declared = [
                dict(item)
                for item in (manifest_model.get("provider_controls") or [])
                if requested_mode in {str(value).strip().lower() for value in (item.get("modes") or [])}
            ]
            if declared:
                return {
                    "schema_id": "neo.voice_engine.controls.v1",
                    "provider_id": "neo_voice_engine",
                    "engine_id": manifest_model["engine_id"],
                    "model_id": requested_model_id,
                    "mode": requested_mode,
                    "authoritative": True,
                    "authority": "selected_model_manifest",
                    "controls": declared,
                    "worker_contacted": False,
                }

            declared_tasks = {str(value).strip().lower() for value in (manifest_model.get("tasks") or []) if str(value).strip()}
            if requested_mode not in declared_tasks:
                return {
                    "schema_id": "neo.voice_engine.controls.v1",
                    "provider_id": "neo_voice_engine",
                    "engine_id": manifest_model["engine_id"],
                    "model_id": requested_model_id,
                    "mode": requested_mode,
                    "authoritative": True,
                    "authority": "selected_model_manifest_mode_unsupported",
                    "controls": [],
                    "worker_contacted": False,
                }

        # Dynamic-control compatibility lane. Only reach the worker when the
        # selected model does not have an authoritative manifest contract for
        # this supported mode. This intentionally keeps legacy/manual workers
        # and future dynamic-control backends working.
        model = self.resolve_model(requested_model_id)
        declared = [
            dict(item)
            for item in (model.get("provider_controls") or [])
            if requested_mode in {str(value).strip().lower() for value in (item.get("modes") or [])}
        ]
        if declared:
            return {
                "schema_id": "neo.voice_engine.controls.v1",
                "provider_id": "neo_voice_engine",
                "engine_id": model["engine_id"],
                "model_id": requested_model_id,
                "mode": requested_mode,
                "authoritative": True,
                "authority": "selected_model_manifest",
                "controls": declared,
                "worker_contacted": False,
            }
        client = self.supervisor.client(model["engine_id"])
        payload = client.controls(requested_model_id, requested_mode)
        return {
            "schema_id": "neo.voice_engine.controls.v1",
            "provider_id": "neo_voice_engine",
            "engine_id": model["engine_id"],
            "model_id": requested_model_id,
            "mode": requested_mode,
            "authoritative": bool(payload.get("authoritative", False)),
            "authority": "worker_live_fallback",
            "controls": payload.get("controls") if isinstance(payload.get("controls"), list) else [],
            "worker_contacted": True,
            "worker": payload,
        }
