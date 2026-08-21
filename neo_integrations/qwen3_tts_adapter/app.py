from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from . import ADAPTER_PHASE, ADAPTER_VERSION
from neo_voice_engine.qwen3_tts_runtime_resolver import probe_qwen3_tts_runtime_model, runtime_registry_snapshot

from .engine import (
    BUILT_IN_SPEAKERS,
    LANGUAGE_NAMES,
    MODEL_SPECS,
    SUPPORTED_MODELS,
    GeneratedAudio,
    Qwen3TTSAdapterError,
    Qwen3TTSEngine,
    dependency_status,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Qwen3TTSJobService:
    def __init__(self, engine: Qwen3TTSEngine | None = None):
        self.engine = engine or Qwen3TTSEngine()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="neo-qwen3-tts")
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
        self._update(
            job_id,
            status="running",
            progress={"percent": max(0, min(99, int(percent))), "stage": stage, "label": label},
            message=label,
        )

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        provider_job_id = f"q3tts_{uuid4().hex[:16]}"
        model_id = str(payload.get("model_id") or payload.get("model") or "").strip()
        record = {
            "schema_id": "neo.qwen3_tts.job.v1",
            "provider_job_id": provider_job_id,
            "job_id": provider_job_id,
            "status": "queued",
            "message": "Qwen3-TTS generation queued.",
            "progress": {"percent": 5, "stage": "queued", "label": "Queued in Qwen3-TTS worker"},
            "created_at": _now(),
            "updated_at": _now(),
            "request_mode": str(payload.get("mode") or "tts"),
            "model_id": model_id,
            "output_path": "",
            "media_type": "",
            "sample_rate": None,
            "error": None,
            "warnings": [],
            "cancel_requested": False,
        }
        with self._lock:
            self._jobs[provider_job_id] = record
            self._futures[provider_job_id] = self._executor.submit(self._run, provider_job_id, dict(payload))
        return self.public_job(provider_job_id)

    def _run(self, job_id: str, payload: dict[str, Any]) -> None:
        try:
            if self._jobs.get(job_id, {}).get("cancel_requested"):
                self._update(
                    job_id,
                    status="cancelled",
                    message="Qwen3-TTS job cancelled before synthesis.",
                    progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"},
                )
                return
            audio: GeneratedAudio = self.engine.generate(
                payload,
                progress=lambda p, s, l: self._progress(job_id, p, s, l),
            )
            if self._jobs.get(job_id, {}).get("cancel_requested"):
                audio.path.unlink(missing_ok=True)
                self._update(
                    job_id,
                    status="cancelled",
                    message="Qwen3-TTS job cancelled; generated audio was discarded.",
                    progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"},
                )
                return
            self._update(
                job_id,
                status="completed",
                message="Qwen3-TTS audio is ready for Neo-owned import.",
                progress={"percent": 100, "stage": "completed", "label": "Qwen3-TTS generation completed"},
                output_path=str(audio.path),
                media_type=audio.media_type,
                model_id=audio.model_id,
                sample_rate=audio.sample_rate,
                warnings=audio.warnings,
                output_url=f"/api/voice/jobs/{job_id}/output",
            )
        except Exception as exc:  # noqa: BLE001
            normalized = exc if isinstance(exc, Qwen3TTSAdapterError) else None
            message = str(exc) if normalized is not None else f"Qwen3-TTS worker failed: {exc}"
            self._update(
                job_id,
                status="failed",
                message=message,
                error={
                    "code": normalized.code if normalized is not None else "worker_unavailable",
                    "message": message,
                    "retryable": normalized.retryable if normalized is not None else True,
                    "details": normalized.details if normalized is not None else {},
                },
                progress={"percent": 0, "stage": "failed", "label": "Qwen3-TTS generation failed"},
            )

    def public_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            raw = dict(self._jobs.get(job_id) or {})
        if not raw:
            raise KeyError(job_id)
        return {key: value for key, value in raw.items() if key not in {"output_path", "cancel_requested"}}

    def result_path(self, job_id: str) -> tuple[Path, str] | None:
        with self._lock:
            raw = dict(self._jobs.get(job_id) or {})
        if raw.get("status") != "completed":
            return None
        path = Path(str(raw.get("output_path") or ""))
        if not path.exists() or not path.is_file():
            self._update(
                job_id,
                status="failed",
                message="Qwen3-TTS output disappeared before Neo could import it.",
                error={"code": "output_missing", "message": "Worker output is missing.", "retryable": True, "details": {}},
            )
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
                job.update(
                    status="cancelled",
                    message="Qwen3-TTS job cancelled before synthesis.",
                    progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"},
                )
        return self.public_job(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


def _model_payload(engine: Qwen3TTSEngine, model_id: str) -> dict[str, Any]:
    spec = MODEL_SPECS[model_id]
    role = str(spec["role"])
    install = probe_qwen3_tts_runtime_model(
        project_root=engine.neo_root,
        voice_runtime_root=engine.voice_runtime_root,
        model_id=model_id,
        legacy_model_root=engine.model_root,
    )
    return {
        "id": model_id,
        "name": str(spec["label"]),
        "label": str(spec["label"]),
        "kind": "voice_model",
        "engine_id": "qwen3_tts",
        "upstream_model_id": str(spec["upstream_model_id"]),
        "role": role,
        "model_size": str(spec["size_class"]),
        "tasks": list(spec["tasks"]),
        "languages": list(LANGUAGE_NAMES),
        "reference_audio": bool(spec["reference_audio"]),
        "built_in_speakers": bool(spec["built_in_speakers"]),
        "instruction_control": bool(spec["instruction_control"]),
        "streaming_upstream": True,
        "streaming_worker": False,
        "output_formats": ["wav"],
        "loaded": engine.loaded_model_id == model_id,
        "install_state": install.get("state"),
        "install": install,
        "local_model_candidates": [
            str(engine.model_root / model_id),
            str(engine.model_root / str(spec["upstream_model_id"]).rsplit("/", 1)[-1]),
        ],
    }


def _controls_for(model_id: str, mode: str) -> list[dict[str, Any]]:
    if model_id not in MODEL_SPECS:
        raise Qwen3TTSAdapterError(f"Unsupported Qwen3-TTS model '{model_id}'.")
    spec = MODEL_SPECS[model_id]
    role = str(spec["role"])
    expected = {"custom_voice": "tts", "base_clone": "voice_clone", "voice_design": "voice_design"}[role]
    if str(mode or expected).strip().lower() != expected:
        return []
    controls: list[dict[str, Any]] = [
        {"id": "language", "label": "Language", "type": "select", "default": "auto", "options": list(LANGUAGE_NAMES)},
    ]
    if role == "custom_voice":
        controls.append({"id": "speaker", "label": "Speaker", "type": "select", "default": "ryan", "options": list(BUILT_IN_SPEAKERS)})
        if bool(spec["instruction_control"]):
            controls.append({"id": "voice_instruction", "label": "Voice Instruction", "type": "text", "default": ""})
    elif role == "base_clone":
        controls.extend(
            [
                {"id": "x_vector_only_mode", "label": "Quick speaker-embedding clone", "type": "boolean", "default": False},
                {"id": "reference_transcript", "label": "Reference Transcript", "type": "text", "default": "", "required_unless": "x_vector_only_mode"},
            ]
        )
    else:
        controls.append({"id": "voice_description", "label": "Voice Description", "type": "text", "default": "", "required": True})
    controls.extend(
        [
            {"id": "seed", "label": "Seed", "type": "integer", "default": -1, "min": -1, "max": 2147483647},
            {"id": "temperature", "label": "Temperature", "type": "number", "default": 0.9, "min": 0.01, "max": 4.0},
            {"id": "top_k", "label": "Top K", "type": "integer", "default": 50, "min": 1, "max": 1000},
            {"id": "top_p", "label": "Top P", "type": "number", "default": 1.0, "min": 0.01, "max": 1.0},
            {"id": "repetition_penalty", "label": "Repetition Penalty", "type": "number", "default": 1.05, "min": 0.1, "max": 4.0},
            {"id": "max_new_tokens", "label": "Max New Tokens", "type": "integer", "default": 2048, "min": 64, "max": 16384},
        ]
    )
    return controls


def build_app(engine: Qwen3TTSEngine | None = None, jobs: Qwen3TTSJobService | None = None) -> FastAPI:
    runtime_engine = engine or Qwen3TTSEngine()
    job_service = jobs or Qwen3TTSJobService(runtime_engine)
    app = FastAPI(title="Neo Studio Qwen3-TTS Adapter", version=ADAPTER_VERSION)

    @app.get("/api/voice/health")
    def health() -> dict[str, Any]:
        deps = dependency_status()
        required_ready = bool(deps["qwen_tts"] and deps["torch"] and deps["transformers"] and deps["accelerate"] and deps["soundfile"])
        return {
            "schema_id": "neo.qwen3_tts.health.v1",
            "phase": ADAPTER_PHASE,
            "provider_id": "qwen3_tts",
            "status": "ready" if required_ready else "dependency_missing",
            "message": "Qwen3-TTS worker is ready." if required_ready else "Qwen3-TTS worker is running but dependencies are missing. Run setup_qwen3_tts_backend.bat.",
            "adapter_version": ADAPTER_VERSION,
            "dependencies": deps,
            "device": runtime_engine.device,
            "loaded_model_id": runtime_engine.loaded_model_id,
            "load_source": runtime_engine.load_source,
            "load_source_kind": runtime_engine.load_source_kind,
            "source_resolution": runtime_engine.source_resolution,
            "queue_mode": "single_gpu_worker",
            "model_root": str(runtime_engine.model_root),
            "voice_runtime_root": str(runtime_engine.voice_runtime_root),
            "model_registry": runtime_registry_snapshot(
                project_root=runtime_engine.neo_root,
                voice_runtime_root=runtime_engine.voice_runtime_root,
                legacy_model_root=runtime_engine.model_root,
            ),
        }

    @app.get("/api/voice/capabilities")
    def capabilities() -> dict[str, Any]:
        return {
            "schema_id": "neo.qwen3_tts.capabilities.v1",
            "phase": ADAPTER_PHASE,
            "provider_id": "qwen3_tts",
            "tts": True,
            "voice_clone": True,
            "voice_design": True,
            "reference_audio": True,
            "multilingual": True,
            "async_jobs": True,
            "cancel": True,
            "model_lifecycle": True,
            "live_language_discovery": True,
            "live_speaker_discovery": True,
            "output_formats": ["wav"],
            "streaming_upstream": True,
            "streaming_worker": False,
            "models": list(SUPPORTED_MODELS),
        }

    @app.get("/api/voice/model-registry")
    def model_registry() -> dict[str, Any]:
        return runtime_registry_snapshot(
            project_root=runtime_engine.neo_root,
            voice_runtime_root=runtime_engine.voice_runtime_root,
            legacy_model_root=runtime_engine.model_root,
        )

    @app.get("/api/voice/models")
    def models(family: str | None = None) -> dict[str, Any]:
        del family
        return {"schema_id": "neo.qwen3_tts.models.v1", "models": [_model_payload(runtime_engine, model_id) for model_id in SUPPORTED_MODELS]}

    @app.get("/api/voice/voices")
    def voices(family: str | None = None, model_id: str | None = None) -> dict[str, Any]:
        del family
        requested = str(model_id or "").strip()
        target_models = [requested] if requested in MODEL_SPECS else [MODEL_SPECS_ID for MODEL_SPECS_ID in ("qwen3_tts_17b_custom_voice", "qwen3_tts_06b_custom_voice")]
        items: list[dict[str, Any]] = []
        live = runtime_engine.discovered_speakers(requested) if requested else []
        if requested and live:
            for speaker in live:
                items.append({"id": str(speaker).lower().replace("-", "_").replace(" ", "_"), "name": str(speaker), "label": str(speaker), "kind": "built_in_voice", "model_ids": [requested]})
        else:
            for voice_id, data in BUILT_IN_SPEAKERS.items():
                items.append({"id": voice_id, "name": data["provider_name"], "label": data["provider_name"], "kind": "built_in_voice", "native_language": data["native_language"], "model_ids": target_models})
        return {"schema_id": "neo.qwen3_tts.voices.v1", "voices": items, "authoritative": bool(requested and live)}

    @app.get("/api/voice/controls")
    def controls(model_id: str, mode: str = "tts") -> dict[str, Any]:
        try:
            return {"schema_id": "neo.qwen3_tts.controls.v1", "model_id": model_id, "mode": mode, "controls": _controls_for(model_id, mode), "authoritative": True}
        except Qwen3TTSAdapterError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/voice/models/{model_id}/lifecycle")
    def model_lifecycle(model_id: str) -> dict[str, Any]:
        try:
            return {"schema_id": "neo.qwen3_tts.model_lifecycle.v1", **runtime_engine.model_lifecycle(model_id)}
        except Qwen3TTSAdapterError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/voice/models/{model_id}/load")
    def load_model(model_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = payload if isinstance(payload, dict) else {}
        try:
            if bool(body.get("blocking", False)):
                result = runtime_engine.prepare_model(model_id, device=str(body.get("device") or ""), device_index=body.get("device_index"))
            else:
                if model_id not in MODEL_SPECS:
                    raise Qwen3TTSAdapterError(f"Unsupported Qwen3-TTS model '{model_id}'.")
                result = runtime_engine.apply_execution_hint(device=str(body.get("device") or ""), device_index=body.get("device_index"))
                result["model_id"] = model_id
            return {"schema_id": "neo.qwen3_tts.model_lifecycle.v1", **result}
        except Qwen3TTSAdapterError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

    @app.post("/api/voice/models/{model_id}/unload")
    def unload_model(model_id: str) -> dict[str, Any]:
        try:
            return {"schema_id": "neo.qwen3_tts.model_lifecycle.v1", **runtime_engine.unload_model(model_id)}
        except Qwen3TTSAdapterError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/voice/render")
    def render(payload: dict[str, Any]) -> dict[str, Any]:
        model_id = str(payload.get("model_id") or payload.get("model") or "").strip()
        if model_id not in MODEL_SPECS:
            raise HTTPException(status_code=400, detail=f"Unsupported Qwen3-TTS model '{model_id}'.")
        role = str(MODEL_SPECS[model_id]["role"])
        expected_mode = {"custom_voice": "tts", "base_clone": "voice_clone", "voice_design": "voice_design"}[role]
        mode = str(payload.get("mode") or expected_mode).strip().lower()
        if mode != expected_mode:
            raise HTTPException(status_code=400, detail=f"Model '{model_id}' requires mode '{expected_mode}'.")
        if not str(payload.get("text") or payload.get("script") or "").strip():
            raise HTTPException(status_code=400, detail="Text is required.")
        return job_service.submit(payload)

    @app.get("/api/voice/jobs/{provider_job_id}")
    def poll_job(provider_job_id: str) -> dict[str, Any]:
        try:
            return job_service.public_job(provider_job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Qwen3-TTS job not found.") from exc

    @app.get("/api/voice/jobs/{provider_job_id}/output")
    def output(provider_job_id: str):
        try:
            ready = job_service.result_path(provider_job_id)
            if not ready:
                raise HTTPException(status_code=409, detail="Qwen3-TTS output is not ready.")
            path, media_type = ready
            return FileResponse(path=str(path), media_type=media_type, filename=path.name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Qwen3-TTS job not found.") from exc

    @app.post("/api/voice/jobs/{provider_job_id}/cancel")
    def cancel_job(provider_job_id: str) -> dict[str, Any]:
        try:
            return job_service.cancel(provider_job_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Qwen3-TTS job not found.") from exc

    app.state.qwen3_tts_engine = runtime_engine
    app.state.qwen3_tts_jobs = job_service
    return app


engine = Qwen3TTSEngine()
jobs = Qwen3TTSJobService(engine)
app = build_app(engine, jobs)
