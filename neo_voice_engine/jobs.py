from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from .catalog import GatewayCatalog
from .config import GatewayConfig
from .errors import VoiceEngineError
from .supervisor import WorkerSupervisor
from .scheduler import ResourceLease, VoiceResourceScheduler
from .worker_client import WorkerAudio


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
RESERVED_CONTROL_KEYS = {
    "provider_id", "profile_id", "engine_id", "model_id", "voice_id", "mode", "job_id",
    "reference_audio", "reference_id", "output_url", "output_path", "worker_command", "environment",
}
VALID_REQUEST_SCHEMAS = {
    "neo.voice.provider_generation_request.v1",
    "neo.voice.provider_clone_request.v1",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_extension(media_type: str, filename: str, requested_format: str) -> str:
    fmt = str(requested_format or "").strip().lower().lstrip(".")
    if fmt in {"wav", "mp3", "flac", "ogg", "m4a"}:
        return fmt
    suffix = Path(str(filename or "")).suffix.lower().lstrip(".")
    if suffix in {"wav", "mp3", "flac", "ogg", "m4a"}:
        return suffix
    mapping = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/flac": "flac", "audio/ogg": "ogg", "audio/x-wav": "wav", "audio/wav": "wav"}
    return mapping.get(str(media_type or "").split(";", 1)[0].lower(), "wav")


class GatewayJobService:
    def __init__(
        self,
        config: GatewayConfig,
        supervisor: WorkerSupervisor,
        catalog: GatewayCatalog,
        scheduler: VoiceResourceScheduler | None = None,
    ) -> None:
        self.config = config
        self.supervisor = supervisor
        self.catalog = catalog
        self.scheduler = scheduler or VoiceResourceScheduler(config, supervisor)
        self.config.ensure_runtime_dirs()
        self._executor = ThreadPoolExecutor(max_workers=self.config.max_concurrent_jobs, thread_name_prefix="neo-voice-engine")
        self._lock = threading.RLock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._futures: dict[str, Future[Any]] = {}

    def _update(self, job_id: str, **updates: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") in TERMINAL_STATUSES:
                return
            job.update(updates)
            job["updated_at"] = _now()

    def _terminal(self, job_id: str, status: str, **updates: Any) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError(status)
        with self._lock:
            job = self._jobs.get(job_id)
            if not job or job.get("status") in TERMINAL_STATUSES:
                return
            job.update(updates)
            job["status"] = status
            job["updated_at"] = _now()

    def _progress(self, job_id: str, percent: int, stage: str, label: str, message: str | None = None) -> None:
        self._update(
            job_id,
            status="running",
            message=message or label,
            progress={"percent": max(0, min(99, int(percent))), "stage": str(stage), "label": str(label)},
        )

    def _validate_clone_reference(self, payload: dict[str, Any], model: dict[str, Any]) -> None:
        reference = payload.get("reference_audio") if isinstance(payload.get("reference_audio"), dict) else {}
        if reference.get("authorization_confirmed") is not True:
            raise VoiceEngineError("reference_unauthorized", "Voice clone reference authorization is required.", http_status=400)
        qc_status = str(reference.get("qc_status") or "").strip()
        if qc_status not in {"usable", "usable_with_warnings"}:
            raise VoiceEngineError("reference_unusable", "Voice clone reference failed Neo QC readiness.", details={"qc_status": qc_status}, http_status=400)
        if model.get("reference_audio") is not True or "voice_clone" not in (model.get("tasks") or []):
            raise VoiceEngineError("unsupported_task", f"Model '{model['id']}' does not support reference voice cloning.", http_status=400)
        if str(reference.get("transport") or "") != "neo_owned_local_path":
            raise VoiceEngineError("reference_path_forbidden", "VO-E2 only permits the frozen same-machine neo_owned_local_path clone transport.", http_status=400)
        if not self.config.allow_local_reference_paths:
            raise VoiceEngineError("reference_path_forbidden", "Local reference paths are disabled because this gateway is not configured as loopback-only.", http_status=403)
        raw_path = str(reference.get("local_path") or "").strip()
        if not raw_path:
            raise VoiceEngineError("reference_path_forbidden", "Voice clone request did not include a local reference path.", http_status=400)
        path = Path(raw_path).expanduser().resolve()
        root = self.config.neo_reference_root.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise VoiceEngineError(
                "reference_path_forbidden",
                "Voice clone reference path is outside the configured Neo reference root.",
                details={"reference_root": str(root)},
                http_status=403,
            ) from exc
        if not path.exists() or not path.is_file():
            raise VoiceEngineError("reference_unusable", "Voice clone reference file does not exist.", details={"path": str(path)}, http_status=400)

    def _validate_request(self, payload: dict[str, Any]) -> tuple[dict[str, Any], str]:
        schema_id = str(payload.get("schema_id") or "").strip()
        if schema_id and schema_id not in VALID_REQUEST_SCHEMAS:
            raise VoiceEngineError("invalid_request", f"Unsupported Voice request schema '{schema_id}'.", http_status=400)
        mode = str(payload.get("mode") or "tts").strip().lower()
        if mode not in {"tts", "voice_clone"}:
            raise VoiceEngineError("unsupported_task", f"Voice task '{mode}' is not active in protocol v1.", http_status=400)
        text = str(payload.get("text") or payload.get("script") or "").strip()
        if not text:
            raise VoiceEngineError("invalid_request", "Voice generation text is required.", http_status=400)
        model_id = str(payload.get("model_id") or payload.get("model") or "").strip()
        model = self.catalog.resolve_model(model_id)
        task = "voice_clone" if mode == "voice_clone" else "tts"
        if task not in (model.get("tasks") or []):
            raise VoiceEngineError("unsupported_task", f"Model '{model_id}' does not support '{task}'.", http_status=400)
        supplied_engine = str(payload.get("engine_id") or "").strip()
        if supplied_engine and supplied_engine != model["engine_id"]:
            raise VoiceEngineError(
                "invalid_request",
                "Submitted engine_id does not own the selected public model_id.",
                details={"submitted_engine_id": supplied_engine, "catalog_engine_id": model["engine_id"], "model_id": model_id},
                http_status=400,
            )
        controls = payload.get("provider_controls") if isinstance(payload.get("provider_controls"), dict) else {}
        reserved = sorted(key for key in controls if key in RESERVED_CONTROL_KEYS)
        if reserved:
            raise VoiceEngineError("invalid_control", "Provider controls attempted to override reserved routing/security fields.", details={"reserved_keys": reserved}, http_status=400)
        if mode == "voice_clone":
            self._validate_clone_reference(payload, model)
        return model, task

    def submit(self, payload: dict[str, Any]) -> dict[str, Any]:
        model, task = self._validate_request(payload)
        provider_job_id = f"nve_{uuid4().hex[:20]}"
        record = {
            "schema_id": "neo.voice_engine.job.v1",
            "provider_job_id": provider_job_id,
            "status": "queued",
            "message": "Voice job accepted.",
            "route": {"engine_id": model["engine_id"], "model_id": model["id"], "task": task},
            "progress": {"percent": 5, "stage": "queued", "label": "Queued"},
            "created_at": _now(),
            "updated_at": _now(),
            "neo_job_id": str(payload.get("job_id") or ""),
            "worker_job_id": "",
            "cancel_requested": False,
            "output_path": "",
            "media_type": "",
            "format": "",
            "sample_rate": None,
            "warnings": [],
            "execution": {},
            "error": None,
        }
        with self._lock:
            self._jobs[provider_job_id] = record
            self._futures[provider_job_id] = self._executor.submit(self._run, provider_job_id, dict(payload), dict(model))
        return {
            "schema_id": "neo.voice_engine.job_accept.v1",
            "provider_job_id": provider_job_id,
            "status": "queued",
            "message": "Voice job accepted.",
            "progress": {"percent": 5, "stage": "queued", "label": "Queued"},
        }

    def _write_output(self, job_id: str, audio: WorkerAudio, requested_format: str) -> Path:
        if not audio.data:
            raise VoiceEngineError("output_missing", "Voice worker returned empty audio.", retryable=True, http_status=502)
        extension = _safe_extension(audio.media_type, audio.filename, requested_format)
        path = self.config.outputs_root / f"{job_id}.{extension}"
        path.write_bytes(audio.data)
        if path.stat().st_size <= 0:
            raise VoiceEngineError("output_missing", "Voice Engine could not persist temporary provider audio.", retryable=True, http_status=500)
        return path

    def _complete_with_audio(self, job_id: str, audio: WorkerAudio, worker_payload: dict[str, Any], requested_format: str) -> None:
        path = self._write_output(job_id, audio, requested_format)
        extension = path.suffix.lower().lstrip(".") or requested_format or "wav"
        self._terminal(
            job_id,
            "completed",
            message="Voice audio is ready for Neo-owned import.",
            progress={"percent": 100, "stage": "completed", "label": "Completed"},
            output_path=str(path),
            output_url=f"/api/voice/jobs/{job_id}/output",
            media_type=audio.media_type or "audio/wav",
            format=extension,
            sample_rate=worker_payload.get("sample_rate"),
            warnings=worker_payload.get("warnings") if isinstance(worker_payload.get("warnings"), list) else [],
        )

    def _run(self, job_id: str, payload: dict[str, Any], model: dict[str, Any]) -> None:
        worker_job_id = ""
        worker_contacted = False
        engine_id = str(model["engine_id"])
        requested_format = str(payload.get("output_format") or ((payload.get("params") or {}).get("output_format") if isinstance(payload.get("params"), dict) else "") or "wav")
        lease: ResourceLease | None = None
        try:
            if self._is_cancel_requested(job_id):
                self._terminal(job_id, "cancelled", message="Voice job cancelled before resource admission.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                return

            self._progress(job_id, 8, "waiting_for_resources", "Waiting for resources")
            lease = self.scheduler.acquire(job_id, model, cancel_check=lambda: self._is_cancel_requested(job_id))
            self._update(job_id, execution=self.scheduler.execution_hint(lease))
            if self._is_cancel_requested(job_id):
                self._terminal(job_id, "cancelled", message="Voice job cancelled before model preparation.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                return

            self._progress(job_id, 10, "preparing_model", "Preparing model")
            self.scheduler.prepare_model(lease, model)
            if self._is_cancel_requested(job_id):
                self._terminal(job_id, "cancelled", message="Voice job cancelled before synthesis.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                return

            client = self.supervisor.client(engine_id)
            self._progress(job_id, 12, "worker_dispatch", "Dispatching to worker")
            dispatch_payload = dict(payload)
            dispatch_payload["_neo_execution"] = self.scheduler.execution_hint(lease)
            worker_contacted = True
            worker_accept = client.submit(dispatch_payload)
            worker_job_id = str(worker_accept.get("provider_job_id") or worker_accept.get("job_id") or "").strip()
            if not worker_job_id:
                raise VoiceEngineError("worker_unavailable", "Voice worker did not return a provider job ID.", retryable=True, http_status=502)
            self._update(job_id, worker_job_id=worker_job_id)

            while True:
                if self._is_cancel_requested(job_id):
                    try:
                        client.cancel(worker_job_id)
                    except Exception:
                        pass
                    self._terminal(job_id, "cancelled", message="Voice job cancelled; worker output will not be imported.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                    return
                worker_status = client.poll(worker_job_id)
                if isinstance(worker_status, WorkerAudio):
                    self._complete_with_audio(job_id, worker_status, {}, requested_format)
                    return
                status = str(worker_status.get("status") or "running").strip().lower()
                progress = worker_status.get("progress") if isinstance(worker_status.get("progress"), dict) else {}
                if status == "completed":
                    try:
                        audio = client.output(worker_job_id, str(worker_status.get("output_url") or "") or None)
                    except VoiceEngineError:
                        raise
                    self._complete_with_audio(job_id, audio, worker_status, requested_format)
                    return
                if status == "failed":
                    worker_error = worker_status.get("error") if isinstance(worker_status.get("error"), dict) else {}
                    message = str(worker_status.get("message") or worker_error.get("message") or worker_status.get("error") or "Voice worker failed.")
                    worker_code = str(worker_error.get("code") or "worker_unavailable")
                    allowed_codes = {
                        "model_not_installed", "model_load_failed", "dependency_missing", "worker_unavailable",
                        "worker_crashed", "gpu_oom", "output_missing", "cancelled", "internal_error",
                    }
                    code = worker_code if worker_code in allowed_codes else "worker_unavailable"
                    raise VoiceEngineError(code, message, retryable=bool(worker_error.get("retryable", True)), details={"worker_job_id": worker_job_id, **(worker_error.get("details") if isinstance(worker_error.get("details"), dict) else {})}, http_status=502)
                if status == "cancelled":
                    self._terminal(job_id, "cancelled", message=str(worker_status.get("message") or "Voice worker cancelled the job."), progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                    return
                percent = int(progress.get("percent") or 20)
                stage = str(progress.get("stage") or "synthesizing")
                label = str(progress.get("label") or worker_status.get("message") or "Running")
                self._progress(job_id, percent, stage, label, str(worker_status.get("message") or label))
                time.sleep(self.config.worker_poll_seconds)
        except VoiceEngineError as exc:
            if self._is_cancel_requested(job_id) or exc.code == "cancelled":
                self._terminal(job_id, "cancelled", message="Voice job cancelled.", progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"})
                return
            recovery = (
                self.supervisor.recover_after_failure(engine_id, code=exc.code, message=exc.message)
                if worker_contacted
                else {"attempted": False, "reason": "worker_not_contacted", "code": exc.code}
            )
            error = exc.payload()["error"]
            if recovery.get("attempted"):
                details = dict(error.get("details") or {})
                details["worker_recovery"] = recovery
                error["details"] = details
            self._terminal(
                job_id,
                "failed",
                message=exc.message,
                progress={"percent": 0, "stage": "failed", "label": "Voice generation failed"},
                error=error,
            )
        except Exception as exc:  # noqa: BLE001 - protect gateway process from worker/runtime faults.
            recovery = (
                self.supervisor.recover_after_failure(engine_id, code="worker_unavailable", message=str(exc))
                if worker_contacted
                else {"attempted": False, "reason": "worker_not_contacted", "code": "internal_error"}
            )
            self._terminal(
                job_id,
                "failed",
                message=f"Neo Voice Engine job failed: {exc}",
                progress={"percent": 0, "stage": "failed", "label": "Voice generation failed"},
                error={"code": "internal_error", "message": str(exc), "retryable": True, "details": {"worker_job_id": worker_job_id, "worker_recovery": recovery}},
            )
        finally:
            self.scheduler.release(lease)

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return bool((self._jobs.get(job_id) or {}).get("cancel_requested"))

    def public_job(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            raw = dict(self._jobs.get(job_id) or {})
        if not raw:
            raise VoiceEngineError("job_not_found", "Voice Engine job was not found.", http_status=404)
        hidden = {"output_path", "cancel_requested", "worker_job_id", "neo_job_id"}
        payload = {key: value for key, value in raw.items() if key not in hidden and value != ""}
        payload["schema_id"] = "neo.voice_engine.job.v1"
        return payload

    def cancel(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise VoiceEngineError("job_not_found", "Voice Engine job was not found.", http_status=404)
            if job.get("status") in TERMINAL_STATUSES:
                return self.public_job(job_id)
            job["cancel_requested"] = True
            job["updated_at"] = _now()
            future = self._futures.get(job_id)
            if future is not None and future.cancel():
                job.update(
                    status="cancelled",
                    message="Voice job cancelled before worker execution.",
                    progress={"percent": 0, "stage": "cancelled", "label": "Cancelled"},
                )
        return self.public_job(job_id)

    def result_path(self, job_id: str) -> tuple[Path, str]:
        with self._lock:
            raw = dict(self._jobs.get(job_id) or {})
        if not raw:
            raise VoiceEngineError("job_not_found", "Voice Engine job was not found.", http_status=404)
        if raw.get("status") != "completed":
            raise VoiceEngineError("output_missing", "Voice Engine output is not available because the job is not completed.", retryable=True, http_status=409)
        path = Path(str(raw.get("output_path") or ""))
        if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
            raise VoiceEngineError("output_missing", "Voice Engine temporary output is missing.", retryable=True, http_status=410)
        return path, str(raw.get("media_type") or "audio/wav")

    def queue_snapshot(self) -> dict[str, int | str]:
        with self._lock:
            statuses = [str(item.get("status") or "") for item in self._jobs.values()]
        return {
            "mode": "managed",
            "active_jobs": sum(1 for status in statuses if status == "running"),
            "queued_jobs": sum(1 for status in statuses if status == "queued"),
        }

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
