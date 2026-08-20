from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.responses import FileResponse, JSONResponse

from . import ENGINE_PHASE, ENGINE_VERSION, PROTOCOL_ID, PROTOCOL_VERSION, SERVICE_ID
from .catalog import GatewayCatalog
from .config import GatewayConfig
from .errors import VoiceEngineError
from .jobs import GatewayJobService
from .registry import ManifestRegistry
from .scheduler import VoiceResourceScheduler
from .supervisor import WorkerSupervisor


class VoiceEngineRuntime:
    def __init__(
        self,
        config: GatewayConfig | None = None,
        supervisor: WorkerSupervisor | None = None,
        registry: ManifestRegistry | None = None,
        catalog: GatewayCatalog | None = None,
        scheduler: VoiceResourceScheduler | None = None,
        jobs: GatewayJobService | None = None,
    ) -> None:
        self.config = config or GatewayConfig.from_env()
        self.config.ensure_runtime_dirs()
        self.supervisor = supervisor or WorkerSupervisor(self.config)
        self.registry = registry or ManifestRegistry(self.config)
        self.registry.reload()
        self.registry.sync_supervisor(self.supervisor)
        self.catalog = catalog or GatewayCatalog(self.supervisor, self.registry)
        if jobs is not None and scheduler is None:
            scheduler = getattr(jobs, "scheduler", None)
        self.scheduler = scheduler or VoiceResourceScheduler(self.config, self.supervisor)
        self.jobs = jobs or GatewayJobService(self.config, self.supervisor, self.catalog, self.scheduler)

    def shutdown(self) -> None:
        self.jobs.shutdown()
        self.supervisor.shutdown()


def build_app(runtime: VoiceEngineRuntime | None = None) -> FastAPI:
    state = runtime or VoiceEngineRuntime()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        state.shutdown()

    app = FastAPI(title="Neo Voice Engine", version=ENGINE_VERSION, lifespan=lifespan)
    app.state.voice_engine = state

    @app.exception_handler(VoiceEngineError)
    async def voice_engine_error_handler(_request: Request, exc: VoiceEngineError):
        return JSONResponse(status_code=exc.http_status, content=exc.payload())

    @app.get("/api/voice/health")
    def health() -> dict[str, Any]:
        workers = state.supervisor.probe_all()
        queue = state.jobs.queue_snapshot()
        scheduler = state.scheduler.snapshot(refresh_hardware=False)
        catalog = state.catalog.snapshot()
        registry = state.registry.snapshot()
        registry_errors = registry.get("errors") or []
        if registry_errors:
            status = "degraded"
            message = "Neo Voice Engine gateway is ready, but one or more manifests were rejected or conflicted."
        elif workers["failed"] and not workers["ready"]:
            status = "degraded"
            message = "Neo Voice Engine gateway is ready, but registered workers are unavailable."
        elif workers["failed"]:
            status = "degraded"
            message = "Neo Voice Engine is ready with one or more degraded workers."
        elif workers["registered"] == 0:
            status = "ready"
            message = "Neo Voice Engine gateway is ready; no model workers are registered yet."
        else:
            status = "ready"
            message = "Neo Voice Engine is ready."
        return {
            "schema_id": "neo.voice_engine.health.v1",
            "service_id": SERVICE_ID,
            "protocol_id": PROTOCOL_ID,
            "protocol_version": PROTOCOL_VERSION,
            "phase": ENGINE_PHASE,
            "status": status,
            "message": message,
            "gateway_version": ENGINE_VERSION,
            "runtime": state.config.runtime_paths_payload(),
            "queue": queue,
            "scheduler": scheduler,
            "workers": workers,
            "registry": {
                "schema_id": registry.get("schema_id"),
                "generation": registry.get("generation"),
                "manifest_count": registry.get("manifest_count"),
                "engine_count": registry.get("engine_count"),
                "model_count": registry.get("model_count"),
                "errors": registry_errors,
            },
            "catalog": {"models": len(catalog["models"]), "voices": len(catalog["voices"]), "errors": catalog["errors"]},
        }

    @app.get("/api/voice/registry")
    def registry(refresh: bool = Query(default=False)) -> dict[str, Any]:
        if refresh:
            return state.registry.refresh(state.supervisor)
        return state.registry.snapshot()

    @app.get("/api/voice/scheduler")
    def scheduler(refresh_hardware: bool = Query(default=True)) -> dict[str, Any]:
        return state.scheduler.snapshot(refresh_hardware=refresh_hardware)

    @app.get("/api/voice/capabilities")
    def capabilities(refresh: bool = Query(default=True)) -> dict[str, Any]:
        snapshot = state.catalog.refresh() if refresh else state.catalog.snapshot()
        all_models = snapshot["models"]
        models = [model for model in all_models if bool((model.get("runtime") or {}).get("executable", False))]
        tasks = sorted({task for model in models for task in (model.get("tasks") or [])})
        formats = sorted({fmt for model in models for fmt in (model.get("output_formats") or [])})
        multilingual = any(len(model.get("languages") or []) > 1 for model in models)
        return {
            "schema_id": "neo.voice_engine.capabilities.v1",
            "provider_id": "neo_voice_engine",
            "tts": "tts" in tasks,
            "voice_clone": "voice_clone" in tasks,
            "reference_audio": any(model.get("reference_audio") is True for model in models),
            "multilingual": multilingual,
            "async_jobs": True,
            "cancel": True,
            "output_formats": formats,
            "tasks": tasks,
            "execution": {
                "progress": True,
                "scheduler": "gpu_aware",
                "worker_isolation": True,
                "vram_preflight": True,
                "model_lifecycle": True,
                "bounded_worker_recovery": True,
                "external_runtime_root": True,
            },
            "catalog": {"declared_models": len(all_models), "executable_models": len(models)},
            "discovery_errors": snapshot["errors"],
        }

    @app.get("/api/voice/models")
    def models(refresh: bool = Query(default=True)) -> dict[str, Any]:
        snapshot = state.catalog.refresh() if refresh else state.catalog.snapshot()
        return {
            "schema_id": "neo.voice_engine.models.v1",
            "provider_id": "neo_voice_engine",
            "models": snapshot["models"],
            "errors": snapshot["errors"],
        }

    @app.get("/api/voice/voices")
    def voices(refresh: bool = Query(default=True)) -> dict[str, Any]:
        snapshot = state.catalog.refresh() if refresh else state.catalog.snapshot()
        return {
            "schema_id": "neo.voice_engine.voices.v1",
            "provider_id": "neo_voice_engine",
            "voices": snapshot["voices"],
            "errors": snapshot["errors"],
        }

    @app.post("/api/voice/models/{model_id}/unload")
    def unload_model(model_id: str) -> dict[str, Any]:
        declared = state.registry.model(model_id) if state.registry is not None else None
        if declared is None:
            snapshot = state.catalog.snapshot()
            declared = next((item for item in snapshot.get("models") or [] if str(item.get("id") or "") == model_id), None)
        if declared is None:
            raise VoiceEngineError("unsupported_model", f"Voice model '{model_id}' is not declared by the gateway.", http_status=404)
        return state.scheduler.unload_model(model_id, reason="api_request")

    @app.get("/api/voice/controls")
    def controls(model_id: str, mode: str = "tts") -> dict[str, Any]:
        return state.catalog.controls(model_id, mode)

    @app.post("/api/voice/render")
    def render(payload: dict[str, Any]) -> dict[str, Any]:
        return state.jobs.submit(payload if isinstance(payload, dict) else {})

    @app.get("/api/voice/jobs/{provider_job_id}")
    def poll_job(provider_job_id: str) -> dict[str, Any]:
        return state.jobs.public_job(provider_job_id)

    @app.post("/api/voice/jobs/{provider_job_id}/cancel")
    def cancel_job(provider_job_id: str) -> dict[str, Any]:
        return state.jobs.cancel(provider_job_id)

    @app.get("/api/voice/jobs/{provider_job_id}/output")
    def output(provider_job_id: str):
        path, media_type = state.jobs.result_path(provider_job_id)
        return FileResponse(path=str(path), media_type=media_type, filename=path.name)

    return app


runtime = VoiceEngineRuntime()
app = build_app(runtime)
