from __future__ import annotations

import os
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import ADAPTER_PHASE, ADAPTER_VERSION
from .engine import (
    ADAPTER_MODEL_MULTILINGUAL,
    ADAPTER_MODEL_TURBO,
    SUPPORTED_LANGUAGES,
    ChatterboxAdapterError,
    ChatterboxEngine,
    dependency_status,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatterboxJobService:
    def __init__(self, engine: ChatterboxEngine | None = None):
        self.engine = engine or ChatterboxEngine()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="neo-chatterbox")
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future[Any]] = {}

    def _update(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(updates)
            job["updated_at"] = _now()

    def _progress(self, job_id: str, percent: int, stage: str, label: str) -> None:
        self._update(job_id, status="running", progress={"percent": max(0, min(99, int(percent))), "stage": stage, "label": label}, message=label)

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        external_id = f"cbx_{uuid4().hex[:16]}"
        record = {
            "provider_job_id": external_id,
            "job_id": external_id,
            "status": "queued",
            "message": "Chatterbox generation queued.",
            "progress": {"percent": 5, "stage": "queued", "label": "Queued in Chatterbox adapter"},
            "created_at": _now(),
            "updated_at": _now(),
            "request_mode": str(payload.get("mode") or "tts"),
            "model_id": str(payload.get("model_id") or payload.get("model") or ADAPTER_MODEL_TURBO),
            "output_path": "",
            "media_type": "",
            "error": "",
            "warnings": [],
            "cancel_requested": False,
        }
        with self._lock:
            self._jobs[external_id] = record
            self._futures[external_id] = self._executor.submit(self._run, external_id, dict(payload))
        return self.public_job(external_id)

    def _run(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            if self._jobs.get(job_id, {}).get("cancel_requested"):
                self._update(job_id, status="cancelled", message="Chatterbox job cancelled before synthesis.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                return
            audio = self.engine.generate(payload, progress=lambda p, s, l: self._progress(job_id, p, s, l))
            if self._jobs.get(job_id, {}).get("cancel_requested"):
                audio.path.unlink(missing_ok=True)
                self._update(job_id, status="cancelled", message="Chatterbox job cancelled; generated audio was discarded.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                return
            self._update(
                job_id,
                status="completed",
                message="Chatterbox audio is ready for Neo-owned import.",
                progress={"percent": 100, "stage": "completed", "label": "Chatterbox generation completed"},
                output_path=str(audio.path),
                media_type=audio.media_type,
                model_id=audio.model_id,
                sample_rate=audio.sample_rate,
                warnings=audio.warnings,
            )
        except Exception as exc:  # noqa: BLE001 - normalize worker errors for provider polling.
            message = str(exc) if isinstance(exc, ChatterboxAdapterError) else f"Chatterbox adapter failed: {exc}"
            self._update(job_id, status="failed", message=message, error=message, progress={"percent": 0, "stage": "failed", "label": "Chatterbox generation failed"})

    def public_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            raw = dict(self._jobs.get(job_id) or {})
        if not raw:
            raise KeyError(job_id)
        return {key: value for key, value in raw.items() if key not in {"output_path"}}

    def result_path(self, job_id: str) -> tuple[Path, str] | None:
        with self._lock:
            raw = dict(self._jobs.get(job_id) or {})
        if raw.get("status") != "completed":
            return None
        path = Path(str(raw.get("output_path") or ""))
        if not path.exists() or not path.is_file():
            self._update(job_id, status="failed", message="Chatterbox output disappeared before Neo could import it.", error="missing_output")
            return None
        return path, str(raw.get("media_type") or "audio/wav")

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            if job_id not in self._jobs:
                raise KeyError(job_id)
            job = self._jobs[job_id]
            if job.get("status") in {"completed", "failed", "cancelled"}:
                return self.public_job(job_id)
            job["cancel_requested"] = True
            future = self._futures.get(job_id)
            if future and future.cancel():
                job.update(status="cancelled", message="Chatterbox job cancelled before synthesis.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
        return self.public_job(job_id)


engine = ChatterboxEngine()
jobs = ChatterboxJobService(engine)
app = FastAPI(title="Neo Studio Chatterbox Adapter", version=ADAPTER_VERSION)


@app.get("/api/voice/health")
def health() -> dict[str, Any]:
    deps = dependency_status()
    required_ready = bool(
        deps["chatterbox_tts"]
        and deps["torch"]
        and deps["torchaudio"]
        and deps["perth_watermarker"]
    )
    return {
        "schema_id": "neo.chatterbox.health.v1",
        "phase": ADAPTER_PHASE,
        "provider_id": "chatterbox",
        "status": "ready" if required_ready else "dependency_missing",
        "message": (
            "Chatterbox adapter is ready."
            if required_ready
            else "Chatterbox adapter is running but required model dependencies are incompatible or missing. Run setup_chatterbox_backend.bat."
        ),
        "adapter_version": ADAPTER_VERSION,
        "dependencies": deps,
        "device": engine.device,
        "loaded_model_id": engine.loaded_model_id,
        "loaded_model_source_kind": engine.model_source_kind,
        "loaded_model_source_path": engine.model_source_path,
        "local_only": str(os.getenv("NEO_CHATTERBOX_LOCAL_ONLY") or "1").strip().lower() not in {"0", "false", "no", "off"},
        "queue_mode": "single_gpu_worker",
        "neo_root": str(engine.neo_root),
    }


@app.get("/api/voice/capabilities")
def capabilities() -> dict[str, Any]:
    return {
        "schema_id": "neo.chatterbox.capabilities.v1",
        "phase": ADAPTER_PHASE,
        "provider_id": "chatterbox",
        "tts": True,
        "voice_clone": True,
        "reference_audio": True,
        "multilingual": True,
        "async_jobs": True,
        "output_formats": ["wav", "mp3"],
        "models": [ADAPTER_MODEL_TURBO, ADAPTER_MODEL_MULTILINGUAL],
    }


@app.get("/api/voice/models")
def models(family: str | None = None) -> dict[str, Any]:
    del family
    turbo_runtime = engine.runtime_model_status(ADAPTER_MODEL_TURBO)
    multilingual_runtime = engine.runtime_model_status(ADAPTER_MODEL_MULTILINGUAL)
    return {
        "schema_id": "neo.chatterbox.models.v2",
        "phase": ADAPTER_PHASE,
        "download_policy": "admin_models_only",
        "local_only": True,
        "models": [
            {
                "id": ADAPTER_MODEL_TURBO,
                "name": "Chatterbox Turbo",
                "label": "Chatterbox Turbo",
                "kind": "voice_model",
                "languages": ["en"],
                "voice_clone": True,
                "paralinguistic_tags": True,
                "model_size": "350M",
                "install": turbo_runtime,
                "runtime_source_kind": turbo_runtime.get("source_kind") or "",
                "notes": "Lower-compute English route. Model weights are installed through Admin → Models; managed generation is local-only.",
            },
            {
                "id": ADAPTER_MODEL_MULTILINGUAL,
                "name": "Chatterbox Multilingual V3",
                "label": "Chatterbox Multilingual V3",
                "kind": "voice_model",
                "languages": sorted(SUPPORTED_LANGUAGES),
                "voice_clone": True,
                "paralinguistic_tags": False,
                "model_size": "500M",
                "install": multilingual_runtime,
                "runtime_source_kind": multilingual_runtime.get("source_kind") or "",
                "notes": "Current multilingual V3 checkpoint. Model weights are installed through Admin → Models; managed generation is local-only.",
            },
        ],
    }


@app.get("/api/voice/voices")
def voices(family: str | None = None) -> dict[str, Any]:
    del family
    return {
        "schema_id": "neo.chatterbox.voices.v1",
        "voices": [
            {
                "id": "provider_default",
                "name": "Chatterbox Default",
                "label": "Chatterbox Default",
                "kind": "built_in_voice",
            }
        ],
    }


@app.get("/api/voice/models/{model_id}/lifecycle")
def model_lifecycle(model_id: str) -> dict[str, Any]:
    try:
        return {
            "schema_id": "neo.chatterbox.model_lifecycle.v1",
            **engine.model_lifecycle(model_id),
        }
    except ChatterboxAdapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/voice/models/{model_id}/unload")
def unload_model(model_id: str) -> dict[str, Any]:
    try:
        return {
            "schema_id": "neo.chatterbox.model_lifecycle.v1",
            **engine.unload_model(model_id),
        }
    except ChatterboxAdapterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/voice/render")
def render(payload: dict[str, Any]) -> dict[str, Any]:
    mode = str(payload.get("mode") or "tts").strip().lower()
    if mode not in {"tts", "voice_clone"}:
        raise HTTPException(status_code=400, detail=f"Unsupported Voice mode '{mode}'.")
    if not str(payload.get("text") or payload.get("script") or "").strip():
        raise HTTPException(status_code=400, detail="Text is required.")
    return jobs.submit(payload)


@app.get("/api/voice/jobs/{provider_job_id}")
def poll_job(provider_job_id: str):
    try:
        ready = jobs.result_path(provider_job_id)
        if ready:
            path, media_type = ready
            return FileResponse(path=str(path), media_type=media_type, filename=path.name)
        return jobs.public_job(provider_job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chatterbox job not found.") from exc


@app.post("/api/voice/jobs/{provider_job_id}/cancel")
def cancel_job(provider_job_id: str) -> dict[str, Any]:
    try:
        return jobs.cancel(provider_job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Chatterbox job not found.") from exc
