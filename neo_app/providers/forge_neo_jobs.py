from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image
from uuid import uuid4

from neo_app.providers.forge_neo_bridge import ForgeBridgeDecision, decide_forge_bridge
from neo_app.providers.forge_neo_client import ForgeNeoClient, ForgeNeoClientError
from neo_app.runtime.job_registry import GenerationJobRegistry
from neo_app.runtime_data import ROOT_DIR

FORGE_JOB_SCHEMA_ID = "neo.provider.forge_job.v1"
FORGE_JOB_ROOT_RELATIVE = Path("neo_data") / "runtime" / "forge_neo"
_TERMINAL = {"completed", "failed", "cancelled"}
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9_.-]+")
_MANAGER_LOCK = threading.RLock()
_MANAGERS: dict[str, "ForgeNeoJobManager"] = {}
_PROCESS_INSTANCE_ID = f"{os.getpid()}-{uuid4().hex[:10]}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_id(value: Any, fallback: str) -> str:
    clean = _SAFE_ID_RE.sub("_", str(value or "").strip())[:160]
    return clean or fallback


def _json_copy(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001
        return deepcopy(value)


def _normalize_forge_request_payload(endpoint: str, payload: dict[str, Any] | None) -> dict[str, Any]:
    """Final provider-boundary normalization for Forge request quirks.

    Forge Neo's txt2img Hires path currently crashes when ``enable_hr`` is true
    and ``hr_additional_modules`` reaches the processing object as ``None``.
    Compiler paths already emit the correct reuse markers, but this queue-boundary
    guard makes the invariant durable even if another UI/compiler path omits them.
    """
    normalized = _json_copy(payload if isinstance(payload, dict) else {})
    if str(endpoint or "").strip() != "/sdapi/v1/txt2img" or not bool(normalized.get("enable_hr")):
        return normalized

    modules = normalized.get("hr_additional_modules")
    if isinstance(modules, str):
        modules = [item.strip() for item in modules.split(",") if item.strip()]
    elif not isinstance(modules, list):
        modules = []
    modules = [str(item).strip() for item in modules if str(item or "").strip()]
    if not modules:
        modules = ["Use same choices"]
    normalized["hr_additional_modules"] = modules

    if not str(normalized.get("hr_checkpoint_name") or "").strip():
        normalized["hr_checkpoint_name"] = "Use same checkpoint"
    return normalized


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _profile_id(profile: dict[str, Any]) -> str:
    return safe_id(profile.get("profile_id") or "forge_local", "forge_local")


def _base_url(profile: dict[str, Any]) -> str:
    connection = profile.get("connection") if isinstance(profile.get("connection"), dict) else {}
    runtime = profile.get("runtime") if isinstance(profile.get("runtime"), dict) else {}
    return str(connection.get("base_url") or runtime.get("base_url") or "http://127.0.0.1:7860").strip().rstrip("/")


def _manager_key(profile: dict[str, Any], root_dir: Path) -> str:
    return f"{root_dir.resolve()}::{_profile_id(profile)}::{_base_url(profile)}"


def _percent(progress: Any) -> float:
    try:
        value = float(progress or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if value <= 1.0:
        value *= 100.0
    return max(0.0, min(99.5, round(value, 2)))


def _detect_image_suffix(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp", "image/webp"
    if data.startswith(b"BM"):
        return ".bmp", "image/bmp"
    return ".png", "image/png"


def _decode_image(value: Any) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Forge returned an empty image payload.")
    if text.startswith("data:image/") and "," in text:
        text = text.split(",", 1)[1]
    missing = len(text) % 4
    if missing:
        text += "=" * (4 - missing)
    try:
        data = base64.b64decode(text, validate=False)
    except Exception as exc:  # noqa: BLE001
        raise ValueError("Forge returned invalid base64 image data.") from exc
    if not data:
        raise ValueError("Forge returned an empty decoded image.")
    return data


def _image_dimensions(data: bytes) -> tuple[int, int]:
    try:
        with Image.open(BytesIO(data)) as image:
            return int(image.width), int(image.height)
    except Exception:
        return 0, 0


def _safe_response_metadata(response: dict[str, Any]) -> dict[str, Any]:
    info_raw = response.get("info")
    info: Any = info_raw
    if isinstance(info_raw, str):
        try:
            info = json.loads(info_raw)
        except Exception:  # noqa: BLE001
            info = info_raw[:4000]
    parameters = response.get("parameters") if isinstance(response.get("parameters"), dict) else {}
    sensitive = {"init_images", "mask", "images", "image", "authorization", "api_key"}
    safe_parameters = {str(key): _json_copy(value) for key, value in parameters.items() if str(key).casefold() not in sensitive}
    return {"info": _json_copy(info), "parameters": safe_parameters}


class ForgeNeoJobManager:
    """Single-worker, file-backed lifecycle manager for one Forge profile.

    Forge's generation endpoints are synchronous and guarded by a backend queue
    lock. Neo therefore owns the durable queue and performs at most one blocking
    generation request per profile. Request payloads are stored only under
    ``neo_data/runtime`` so queued jobs can resume after a Neo restart.
    """

    def __init__(
        self,
        profile: dict[str, Any],
        client: ForgeNeoClient,
        *,
        root_dir: Path | str | None = None,
        start_worker: bool = True,
    ) -> None:
        self.profile = _json_copy(profile if isinstance(profile, dict) else {})
        self.client = client
        self.root_dir = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR.resolve()
        self.profile_id = _profile_id(self.profile)
        self.storage_root = self.root_dir / FORGE_JOB_ROOT_RELATIVE / self.profile_id / "jobs"
        self.registry = GenerationJobRegistry(self.root_dir)
        connection = self.profile.get("connection") if isinstance(self.profile.get("connection"), dict) else {}
        self.generation_timeout_seconds = max(30.0, min(float(connection.get("generation_timeout_seconds") or 3600.0), 86400.0))
        self._condition = threading.Condition(threading.RLock())
        self._worker: threading.Thread | None = None
        self._stopping = False
        self._recover_previous_process_records()
        if start_worker and self._queued_ids():
            self.ensure_worker()

    def _job_dir(self, job_id: str) -> Path:
        return self.storage_root / safe_id(job_id, "forge_job")

    def _state_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "state.json"

    def _request_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / "request.json"

    def _state(self, job_id: str) -> dict[str, Any]:
        return _read_json(self._state_path(job_id))

    def _write_state(self, job_id: str, updates: dict[str, Any]) -> dict[str, Any]:
        with self._condition:
            current = self._state(job_id)
            merged = {**current, **_json_copy(updates), "schema_id": FORGE_JOB_SCHEMA_ID, "job_id": job_id, "profile_id": self.profile_id, "updated_at": utc_now()}
            _atomic_json(self._state_path(job_id), merged)
            return merged

    def _all_states(self) -> list[dict[str, Any]]:
        if not self.storage_root.exists():
            return []
        states = [_read_json(path) for path in self.storage_root.glob("*/state.json")]
        states = [state for state in states if state.get("job_id")]
        return sorted(states, key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""))

    def _queued_ids(self) -> list[str]:
        return [str(state.get("job_id")) for state in self._all_states() if state.get("status") == "queued"]

    def _recover_previous_process_records(self) -> None:
        for state in self._all_states():
            if state.get("status") != "running":
                continue
            if state.get("worker_instance_id") == _PROCESS_INSTANCE_ID:
                continue
            job_id = str(state.get("job_id") or "")
            recovery = {
                "recoverable": True,
                "reason": "neo_process_restarted_during_synchronous_forge_request",
                "action": "explicit_requeue_after_backend_idle",
                "message": "Neo restarted while Forge was generating. The original HTTP response cannot be recovered from the standard Forge API.",
            }
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["recovery"] = recovery
            self._write_state(job_id, {
                "status": "failed",
                "message": recovery["message"],
                "error": recovery["reason"],
                "runtime": runtime,
                "recoverable": True,
                "orphaned_at": utc_now(),
            })
            try:
                self.registry.mark_failed(job_id, surface="image", message=recovery["message"], error=recovery["reason"], runtime=runtime)
            except Exception:  # noqa: BLE001
                pass

    def ensure_worker(self) -> None:
        with self._condition:
            if self._worker and self._worker.is_alive():
                self._condition.notify_all()
                return
            self._worker = threading.Thread(target=self._worker_loop, name=f"neo-forge-{self.profile_id}", daemon=True)
            self._worker.start()
            self._condition.notify_all()

    def stop(self) -> None:
        with self._condition:
            self._stopping = True
            self._condition.notify_all()

    def enqueue(self, *, job: dict[str, Any], compiled: dict[str, Any]) -> dict[str, Any]:
        job_id = safe_id(job.get("job_id"), f"forge-{uuid4().hex[:12]}")
        existing = self._state(job_id)
        if existing and existing.get("status") not in _TERMINAL:
            self.ensure_worker()
            return existing
        endpoint = str(compiled.get("endpoint") or "").strip()
        operation = str(compiled.get("operation") or "").strip()
        request_payload = compiled.get("payload") if isinstance(compiled.get("payload"), dict) else {}
        supported_endpoints = {"/sdapi/v1/txt2img", "/sdapi/v1/img2img", "/sdapi/v1/extra-single-image"}
        supported_operations = {"native_txt2img_upscale"}
        if bool(endpoint) == bool(operation):
            raise ValueError("Forge compiled request must use exactly one endpoint or native operation.")
        if endpoint and endpoint not in supported_endpoints:
            raise ValueError("Forge compiled request uses an unsupported image endpoint.")
        if operation and operation not in supported_operations:
            raise ValueError("Forge compiled request uses an unsupported native operation.")
        if operation and not bool(getattr(self, "bridge_enabled", False)):
            raise ValueError("Forge native operations require the selected Neo Forge Bridge lifecycle.")
        request_payload = _normalize_forge_request_payload(endpoint or "/sdapi/v1/txt2img", request_payload)
        if endpoint in {"/sdapi/v1/txt2img", "/sdapi/v1/img2img"}:
            request_payload["force_task_id"] = job_id
        job_dir = self._job_dir(job_id)
        job_dir.mkdir(parents=True, exist_ok=True)
        _atomic_json(self._request_path(job_id), {"endpoint": endpoint, "operation": operation, "payload": request_payload})
        now = utc_now()
        runtime = {
            "phase": "forge_image_job_lifecycle",
            "execution_state": "queued",
            "progress": {"source": "forge.sdapi.progress", "percent": 0, "label": "Queued for Forge", "eta_seconds": 0},
            "actual_params": _json_copy(compiled.get("actual_params") or {}),
            "route_snapshot": {
                "provider_id": "forge",
                "backend": "forge_neo",
                "endpoint": endpoint,
                "operation": operation,
                "compiler_id": str(compiled.get("compiler_id") or "forge.sdapi_checkpoint"),
            },
            "control": {"cancel_supported": True, "pause_supported": False},
            "recovery": {"recoverable": True, "strategy": "durable_queue_then_explicit_orphan_requeue"},
        }
        state = {
            "schema_id": FORGE_JOB_SCHEMA_ID,
            "job_id": job_id,
            "profile_id": self.profile_id,
            "provider_id": "forge",
            "status": "queued",
            "message": "Queued for Forge Neo.",
            "created_at": now,
            "updated_at": now,
            "attempt": int(existing.get("attempt") or 0) + 1 if existing else 1,
            "request_path": str(self._request_path(job_id).relative_to(self.root_dir)),
            "submitted_job": _json_copy(job),
            "compiled": {key: _json_copy(value) for key, value in compiled.items() if key != "payload"},
            "runtime": runtime,
            "outputs": [],
            "cancel_requested": False,
            "recoverable": True,
        }
        _atomic_json(self._state_path(job_id), state)
        try:
            self.registry.register_queued(
                job_id=job_id,
                surface="image",
                provider_id="forge",
                profile_id=self.profile_id,
                backend_profile_id=self.profile_id,
                provider_job_id=job_id,
                local_job_id=job_id,
                backend="forge_neo",
                mode=str(job.get("mode") or ""),
                family=str(job.get("family") or ""),
                loader=str(job.get("loader") or ""),
                model=str(job.get("model") or ""),
                submitted_job=job,
                compiled_backend_payload=compiled,
                runtime=runtime,
                output_expectations={"kind": "image", "handoff": "forge_response_spool_then_neo_data"},
                message="Queued for Forge Neo.",
            )
        except Exception:  # noqa: BLE001
            pass
        self.ensure_worker()
        return state

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                if self._stopping:
                    return
                queued = self._queued_ids()
                if not queued:
                    self._condition.wait(timeout=1.0)
                    continue
                job_id = queued[0]
            self._execute(job_id)

    def _execute(self, job_id: str) -> None:
        state = self._state(job_id)
        if not state or state.get("status") != "queued":
            return
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        runtime["execution_state"] = "running"
        runtime["progress"] = {"source": "forge.sdapi.progress", "percent": 1, "label": "Starting Forge", "eta_seconds": 0}
        state = self._write_state(job_id, {
            "status": "running",
            "message": "Forge Neo is generating.",
            "started_at": utc_now(),
            "worker_instance_id": _PROCESS_INSTANCE_ID,
            "runtime": runtime,
        })
        try:
            self.registry.mark_running(job_id, surface="image", message="Forge Neo is generating.", runtime=runtime, progress=runtime["progress"])
        except Exception:  # noqa: BLE001
            pass
        request_record = _read_json(self._request_path(job_id))
        try:
            response = self.client.submit(
                str(request_record.get("endpoint") or ""),
                request_record.get("payload") if isinstance(request_record.get("payload"), dict) else {},
                timeout=self.generation_timeout_seconds,
            )
            latest = self._state(job_id)
            if latest.get("cancel_requested"):
                self._finish_cancelled(job_id, "Forge generation was interrupted by the user.")
                return
            outputs = self._spool_outputs(job_id, response)
            if not outputs:
                raise ValueError("Forge completed without returning image data.")
            metadata = _safe_response_metadata(response)
            runtime = latest.get("runtime") if isinstance(latest.get("runtime"), dict) else runtime
            runtime["execution_state"] = "completed_response_spooled"
            runtime["progress"] = {"source": "forge.sdapi.progress", "percent": 100, "label": "Forge completed", "eta_seconds": 0}
            runtime["forge_response"] = metadata
            completed = self._write_state(job_id, {
                "status": "completed",
                "message": "Forge Neo job completed.",
                "completed_at": utc_now(),
                "outputs": outputs,
                "runtime": runtime,
                "recoverable": True,
            })
            try:
                self.registry.mark_completed(job_id, surface="image", message="Forge Neo job completed.", outputs=outputs, runtime=runtime, progress=runtime["progress"])
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            latest = self._state(job_id)
            if latest.get("cancel_requested"):
                self._finish_cancelled(job_id, "Forge generation was interrupted by the user.")
                return
            error_kind = exc.kind if isinstance(exc, ForgeNeoClientError) else type(exc).__name__
            runtime = latest.get("runtime") if isinstance(latest.get("runtime"), dict) else runtime
            runtime["execution_state"] = "failed"
            runtime["progress"] = {"source": "forge.sdapi.progress", "percent": 100, "label": "Forge failed", "eta_seconds": 0}
            runtime["error_kind"] = error_kind
            message = str(exc) or "Forge generation failed."
            self._write_state(job_id, {"status": "failed", "message": message, "error": message, "completed_at": utc_now(), "runtime": runtime, "recoverable": True})
            try:
                self.registry.mark_failed(job_id, surface="image", message=message, error=message, runtime=runtime)
            except Exception:  # noqa: BLE001
                pass

    def _spool_outputs(self, job_id: str, response: dict[str, Any]) -> list[dict[str, Any]]:
        images = response.get("images") if isinstance(response.get("images"), list) else []
        if not images and isinstance(response.get("image"), str) and response.get("image"):
            images = [response.get("image")]
        output_dir = self._job_dir(job_id) / "outputs"
        output_dir.mkdir(parents=True, exist_ok=True)
        metadata = _safe_response_metadata(response)
        outputs: list[dict[str, Any]] = []
        for index, image in enumerate(images, start=1):
            data = _decode_image(image)
            suffix, mime = _detect_image_suffix(data)
            filename = f"forge_{safe_id(job_id, 'job')}_{index}{suffix}"
            target = output_dir / filename
            tmp = target.with_suffix(target.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(target)
            width, height = _image_dimensions(data)
            outputs.append({
                "kind": "image",
                "provider_id": "forge",
                "backend": "forge_neo",
                "provider_owned": True,
                "filename": filename,
                "local_path": str(target),
                "mime_type": mime,
                "width": width,
                "height": height,
                "metadata": {"provider_output_index": index, "width": width, "height": height, **metadata},
            })
        return outputs

    def _progress_payload(self, state: dict[str, Any]) -> dict[str, Any]:
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        return runtime.get("progress") if isinstance(runtime.get("progress"), dict) else {}

    def poll(self, job_id: str) -> dict[str, Any]:
        state = self._state(job_id)
        if not state:
            return {"job_id": job_id, "profile_id": self.profile_id, "status": "failed", "message": "Unknown Forge job.", "outputs": [], "runtime": {"execution_state": "missing"}}
        if state.get("status") == "queued":
            self.ensure_worker()
        elif state.get("status") == "running":
            self._refresh_progress(job_id, state)
            state = self._state(job_id)
        return state

    def _refresh_progress(self, job_id: str, state: dict[str, Any]) -> None:
        try:
            payload = self.client.get_progress(skip_current_image=True, timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["progress_probe_warning"] = str(exc)
            self._write_state(job_id, {"runtime": runtime})
            return
        current_task = str(payload.get("current_task") or "")
        progress = {
            "source": "forge.sdapi.progress",
            "percent": _percent(payload.get("progress")),
            "label": str(payload.get("textinfo") or ("Forge running" if payload.get("progress") else "Forge loading")),
            "eta_seconds": max(0.0, float(payload.get("eta_relative") or 0.0)),
            "current_task": current_task,
        }
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        runtime["progress"] = progress
        runtime["forge_state"] = _json_copy(payload.get("state") if isinstance(payload.get("state"), dict) else {})
        self._write_state(job_id, {"runtime": runtime, "message": progress["label"]})
        try:
            self.registry.mark_running(job_id, surface="image", message=progress["label"], runtime=runtime, progress=progress, poll_state={"current_task": current_task})
        except Exception:  # noqa: BLE001
            pass

    def preview(self, job_id: str) -> dict[str, Any]:
        state = self._state(job_id)
        if not state:
            return {"ok": False, "job_id": job_id, "status": "missing", "is_final": False, "message": "Unknown Forge job."}
        if state.get("status") in _TERMINAL:
            outputs = state.get("outputs") if isinstance(state.get("outputs"), list) else []
            return {"ok": bool(outputs), "job_id": job_id, "status": state.get("status"), "is_final": True, "preview": outputs[0] if outputs else None, "message": state.get("message") or ""}
        try:
            payload = self.client.get_progress(skip_current_image=False, timeout=10.0)
            current_image = str(payload.get("current_image") or "").strip()
            if not current_image:
                return {"ok": False, "job_id": job_id, "status": state.get("status"), "is_final": False, "preview": None, "message": "Forge has not emitted a preview frame yet."}
            if not current_image.startswith("data:image/"):
                current_image = f"data:image/png;base64,{current_image}"
            return {
                "ok": True,
                "job_id": job_id,
                "status": state.get("status"),
                "is_final": False,
                "preview": {"data_url": current_image, "source": "forge.sdapi.progress.current_image"},
                "message": str(payload.get("textinfo") or "Forge live preview"),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "job_id": job_id, "status": state.get("status"), "is_final": False, "preview": None, "message": str(exc)}

    def cancel(self, job_id: str) -> dict[str, Any]:
        state = self._state(job_id)
        if not state:
            return {"job_id": job_id, "profile_id": self.profile_id, "status": "failed", "message": "Unknown Forge job.", "outputs": [], "runtime": {"execution_state": "missing"}}
        status = str(state.get("status") or "")
        if status in _TERMINAL:
            return state
        self._write_state(job_id, {"cancel_requested": True, "message": "Cancel requested."})
        try:
            self.registry.request_cancel(job_id, surface="image")
        except Exception:  # noqa: BLE001
            pass
        if status == "queued":
            self._finish_cancelled(job_id, "Forge job cancelled before submission.")
            return self._state(job_id)
        try:
            progress = self.client.get_progress(skip_current_image=True, timeout=5.0)
            current_task = str(progress.get("current_task") or "")
            if current_task and current_task != job_id:
                state = self._state(job_id)
                runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
                runtime["cancel_warning"] = "Forge reports a different active task; Neo did not interrupt it."
                self._write_state(job_id, {"runtime": runtime, "message": runtime["cancel_warning"]})
                return self._state(job_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            self.client.interrupt(timeout=10.0)
        except Exception as exc:  # noqa: BLE001
            state = self._state(job_id)
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["cancel_warning"] = str(exc)
            self._write_state(job_id, {"runtime": runtime})
        self._finish_cancelled(job_id, "Forge interrupt sent. Current generation was stopped.")
        return self._state(job_id)

    def _finish_cancelled(self, job_id: str, message: str) -> None:
        state = self._state(job_id)
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        runtime["execution_state"] = "cancelled"
        runtime["progress"] = {"source": "forge.sdapi.progress", "percent": 100, "label": "Stopped", "eta_seconds": 0}
        self._write_state(job_id, {"status": "cancelled", "message": message, "completed_at": utc_now(), "runtime": runtime, "outputs": []})
        try:
            self.registry.mark_cancelled(job_id, surface="image", message=message, runtime=runtime)
        except Exception:  # noqa: BLE001
            pass

    def recover(self, job_id: str) -> dict[str, Any]:
        state = self._state(job_id)
        if not state:
            return {"job_id": job_id, "profile_id": self.profile_id, "status": "failed", "message": "Unknown Forge job.", "outputs": [], "runtime": {"execution_state": "missing"}}
        if state.get("status") == "queued":
            self.ensure_worker()
            return state
        if state.get("status") == "completed":
            return state
        recovery = ((state.get("runtime") or {}).get("recovery") or {}) if isinstance(state.get("runtime"), dict) else {}
        if not state.get("recoverable") and not recovery.get("recoverable"):
            return state
        try:
            progress = self.client.get_progress(skip_current_image=True, timeout=10.0)
            active = str(progress.get("current_task") or "")
            if active == job_id:
                runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
                runtime["progress"] = {
                    "source": "forge.sdapi.progress",
                    "percent": _percent(progress.get("progress")),
                    "label": str(progress.get("textinfo") or "Forge is still running the orphaned task"),
                    "eta_seconds": max(0.0, float(progress.get("eta_relative") or 0.0)),
                    "current_task": active,
                }
                runtime["recovery"] = {**recovery, "recoverable": True, "waiting_for_backend_idle": True}
                return self._write_state(job_id, {"status": "running", "message": runtime["progress"]["label"], "runtime": runtime})
            if active:
                return self._write_state(job_id, {"message": "Forge is busy with another task. Recovery was not requeued."})
        except Exception as exc:  # noqa: BLE001
            return self._write_state(job_id, {"message": f"Forge recovery probe failed: {exc}"})
        request_record = _read_json(self._request_path(job_id))
        if (not request_record.get("endpoint") and not request_record.get("operation")) or not isinstance(request_record.get("payload"), dict):
            return self._write_state(job_id, {"status": "failed", "message": "Forge recovery request data is missing.", "recoverable": False})
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        runtime["execution_state"] = "requeued_by_explicit_recovery"
        runtime["progress"] = {"source": "forge.sdapi.progress", "percent": 0, "label": "Recovery requeued", "eta_seconds": 0}
        runtime["recovery"] = {**recovery, "recoverable": True, "requeued_at": utc_now()}
        self._write_state(job_id, {
            "status": "queued",
            "message": "Forge job requeued by explicit recovery.",
            "completed_at": "",
            "error": "",
            "cancel_requested": False,
            "worker_instance_id": "",
            "attempt": int(state.get("attempt") or 1) + 1,
            "runtime": runtime,
            "outputs": [],
        })
        try:
            submitted = state.get("submitted_job") if isinstance(state.get("submitted_job"), dict) else {}
            self.registry.register_queued(
                job_id=job_id,
                surface="image",
                provider_id="forge",
                profile_id=self.profile_id,
                backend_profile_id=self.profile_id,
                provider_job_id=job_id,
                local_job_id=job_id,
                backend="forge_neo",
                mode=str(submitted.get("mode") or ""),
                family=str(submitted.get("family") or ""),
                loader=str(submitted.get("loader") or ""),
                model=str(submitted.get("model") or ""),
                submitted_job=submitted,
                runtime=runtime,
                message="Forge job requeued by explicit recovery.",
            )
        except Exception:  # noqa: BLE001
            pass
        self.ensure_worker()
        return self._state(job_id)



class ForgeNeoBridgeJobManager(ForgeNeoJobManager):
    """Neo-side adapter for the optional Forge-resident durable bridge.

    Neo still keeps a local job mirror for output import and UI continuity, while
    Forge owns the durable backend record, job-specific progress, cancellation,
    history, and result recovery. The bridge is selected only after a successful
    protocol handshake; the standard manager remains the fallback.
    """

    bridge_enabled = True

    def _recover_previous_process_records(self) -> None:
        for state in self._all_states():
            if state.get("status") != "running":
                continue
            if state.get("worker_instance_id") == _PROCESS_INSTANCE_ID:
                continue
            job_id = str(state.get("job_id") or "")
            try:
                remote = self.client.bridge_get_job(job_id, include_images=False, timeout=8.0)
            except Exception:
                remote = {}
            remote_status = str(remote.get("status") or "")
            if remote_status in {"queued", "running"}:
                runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
                runtime["bridge"] = {
                    "enabled": True,
                    "reattached": True,
                    "remote_status": remote_status,
                    "bridge_job_id": job_id,
                }
                runtime["recovery"] = {
                    "recoverable": True,
                    "strategy": "bridge_job_reattach",
                    "message": "Neo restarted and reattached to the Forge Bridge job record.",
                }
                self._write_state(job_id, {
                    "status": "queued",
                    "message": "Reattaching to Forge Bridge job.",
                    "runtime": runtime,
                    "recoverable": True,
                    "worker_instance_id": "",
                })
                continue
            if remote_status == "completed":
                try:
                    remote = self.client.bridge_get_job(job_id, include_images=True, timeout=20.0)
                    response = remote.get("result") if isinstance(remote.get("result"), dict) else {}
                    outputs = self._spool_outputs(job_id, response)
                    runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
                    runtime["bridge"] = {"enabled": True, "recovered": True, "bridge_job_id": job_id}
                    runtime["execution_state"] = "completed_response_spooled"
                    runtime["progress"] = {"source": "forge.bridge.job", "percent": 100, "label": "Forge Bridge completed", "eta_seconds": 0}
                    self._write_state(job_id, {
                        "status": "completed",
                        "message": "Forge Bridge job recovered after Neo restart.",
                        "completed_at": utc_now(),
                        "outputs": outputs,
                        "runtime": runtime,
                        "recoverable": True,
                    })
                    continue
                except Exception:
                    pass
            if remote_status in {"failed", "cancelled"}:
                runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
                runtime["bridge"] = {"enabled": True, "recovered": True, "bridge_job_id": job_id}
                self._write_state(job_id, {
                    "status": remote_status,
                    "message": str(remote.get("message") or f"Forge Bridge job {remote_status}."),
                    "error": str(remote.get("error") or ""),
                    "runtime": runtime,
                    "recoverable": True,
                    "completed_at": utc_now(),
                })
                continue
            recovery = {
                "recoverable": True,
                "reason": "bridge_job_missing_after_neo_restart",
                "action": "explicit_requeue",
                "message": "Neo restarted, but Forge Bridge no longer has the running job record.",
            }
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["recovery"] = recovery
            self._write_state(job_id, {
                "status": "failed",
                "message": recovery["message"],
                "error": recovery["reason"],
                "runtime": runtime,
                "recoverable": True,
                "orphaned_at": utc_now(),
            })

    def _execute(self, job_id: str) -> None:
        state = self._state(job_id)
        if not state or state.get("status") != "queued":
            return
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        runtime["execution_state"] = "running"
        runtime["bridge"] = {"enabled": True, "bridge_job_id": job_id}
        runtime["progress"] = {"source": "forge.bridge.job", "percent": 1, "label": "Starting Forge Bridge", "eta_seconds": 0}
        self._write_state(job_id, {
            "status": "running",
            "message": "Forge Bridge is generating.",
            "started_at": utc_now(),
            "worker_instance_id": _PROCESS_INSTANCE_ID,
            "runtime": runtime,
        })
        request_record = _read_json(self._request_path(job_id))
        try:
            submit_kwargs = {
                "job_id": job_id,
                "endpoint": str(request_record.get("endpoint") or ""),
                "payload": request_record.get("payload") if isinstance(request_record.get("payload"), dict) else {},
                "timeout": 20.0,
            }
            if str(request_record.get("operation") or ""):
                submit_kwargs["operation"] = str(request_record.get("operation") or "")
            self.client.bridge_submit(**submit_kwargs)
            while True:
                remote = self.client.bridge_get_job(job_id, include_images=False, timeout=15.0)
                status = str(remote.get("status") or "failed")
                self._apply_bridge_progress(job_id, remote)
                if status in _TERMINAL:
                    break
                if self._state(job_id).get("cancel_requested"):
                    self.client.bridge_cancel(job_id, timeout=10.0)
                time.sleep(0.5)
            latest = self._state(job_id)
            if status == "cancelled" or latest.get("cancel_requested"):
                self._finish_cancelled(job_id, str(remote.get("message") or "Forge Bridge job cancelled."))
                return
            if status == "failed":
                raise RuntimeError(str(remote.get("error") or remote.get("message") or "Forge Bridge job failed."))
            remote = self.client.bridge_get_job(job_id, include_images=True, timeout=30.0)
            response = remote.get("result") if isinstance(remote.get("result"), dict) else {}
            outputs = self._spool_outputs(job_id, response)
            if not outputs:
                raise ValueError("Forge Bridge completed without recoverable image outputs.")
            metadata = _safe_response_metadata(response)
            runtime = self._state(job_id).get("runtime") or runtime
            runtime["execution_state"] = "completed_response_spooled"
            runtime["progress"] = {"source": "forge.bridge.job", "percent": 100, "label": "Forge Bridge completed", "eta_seconds": 0}
            runtime["forge_response"] = metadata
            runtime["bridge"] = {
                "enabled": True,
                "bridge_job_id": job_id,
                "durable": True,
                "history_available": True,
            }
            self._write_state(job_id, {
                "status": "completed",
                "message": "Forge Bridge job completed.",
                "completed_at": utc_now(),
                "outputs": outputs,
                "runtime": runtime,
                "recoverable": True,
            })
            try:
                self.registry.mark_completed(job_id, surface="image", message="Forge Bridge job completed.", outputs=outputs, runtime=runtime, progress=runtime["progress"])
            except Exception:  # noqa: BLE001
                pass
        except Exception as exc:  # noqa: BLE001
            latest = self._state(job_id)
            if latest.get("cancel_requested"):
                self._finish_cancelled(job_id, "Forge Bridge job cancelled.")
                return
            runtime = latest.get("runtime") if isinstance(latest.get("runtime"), dict) else runtime
            runtime["execution_state"] = "failed"
            runtime["progress"] = {"source": "forge.bridge.job", "percent": 100, "label": "Forge Bridge failed", "eta_seconds": 0}
            runtime["error_kind"] = exc.kind if isinstance(exc, ForgeNeoClientError) else type(exc).__name__
            message = str(exc) or "Forge Bridge generation failed."
            self._write_state(job_id, {"status": "failed", "message": message, "error": message, "completed_at": utc_now(), "runtime": runtime, "recoverable": True})
            try:
                self.registry.mark_failed(job_id, surface="image", message=message, error=message, runtime=runtime)
            except Exception:  # noqa: BLE001
                pass

    def _apply_bridge_progress(self, job_id: str, remote: dict[str, Any]) -> None:
        state = self._state(job_id)
        progress_raw = remote.get("progress") if isinstance(remote.get("progress"), dict) else {}
        progress = {
            "source": "forge.bridge.job",
            "percent": _percent(progress_raw.get("progress", progress_raw.get("percent", 0))),
            "label": str(progress_raw.get("textinfo") or progress_raw.get("label") or remote.get("message") or "Forge Bridge running"),
            "eta_seconds": max(0.0, float(progress_raw.get("eta_relative", progress_raw.get("eta_seconds", 0)) or 0.0)),
            "current_task": str(progress_raw.get("current_task") or remote.get("job_id") or job_id),
        }
        runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
        runtime["progress"] = progress
        runtime["bridge"] = {
            **(runtime.get("bridge") if isinstance(runtime.get("bridge"), dict) else {}),
            "enabled": True,
            "bridge_job_id": job_id,
            "remote_status": str(remote.get("status") or ""),
        }
        self._write_state(job_id, {"runtime": runtime, "message": progress["label"]})
        try:
            self.registry.mark_running(job_id, surface="image", message=progress["label"], runtime=runtime, progress=progress, poll_state={"current_task": progress["current_task"]})
        except Exception:  # noqa: BLE001
            pass

    def _refresh_progress(self, job_id: str, state: dict[str, Any]) -> None:
        try:
            remote = self.client.bridge_get_job(job_id, include_images=False, timeout=10.0)
            self._apply_bridge_progress(job_id, remote)
        except Exception as exc:  # noqa: BLE001
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["progress_probe_warning"] = str(exc)
            self._write_state(job_id, {"runtime": runtime})

    def preview(self, job_id: str) -> dict[str, Any]:
        state = self._state(job_id)
        if not state:
            return {"ok": False, "job_id": job_id, "status": "missing", "is_final": False, "message": "Unknown Forge Bridge job."}
        if state.get("status") in _TERMINAL:
            outputs = state.get("outputs") if isinstance(state.get("outputs"), list) else []
            return {"ok": bool(outputs), "job_id": job_id, "status": state.get("status"), "is_final": True, "preview": outputs[0] if outputs else None, "message": state.get("message") or ""}
        try:
            remote = self.client.bridge_get_job(job_id, include_preview=True, timeout=10.0)
            preview = remote.get("preview") if isinstance(remote.get("preview"), dict) else None
            return {
                "ok": bool(preview),
                "job_id": job_id,
                "status": str(remote.get("status") or state.get("status") or "running"),
                "is_final": False,
                "preview": preview,
                "message": str(remote.get("message") or "Forge Bridge live preview"),
            }
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "job_id": job_id, "status": state.get("status"), "is_final": False, "preview": None, "message": str(exc)}

    def cancel(self, job_id: str) -> dict[str, Any]:
        state = self._state(job_id)
        if not state:
            return {"job_id": job_id, "profile_id": self.profile_id, "status": "failed", "message": "Unknown Forge Bridge job.", "outputs": [], "runtime": {"execution_state": "missing"}}
        status = str(state.get("status") or "")
        if status in _TERMINAL:
            return state
        self._write_state(job_id, {"cancel_requested": True, "message": "Cancel requested through Forge Bridge."})
        if status == "queued":
            self._finish_cancelled(job_id, "Forge Bridge job cancelled before submission.")
            return self._state(job_id)
        try:
            remote = self.client.bridge_cancel(job_id, timeout=10.0)
            if str(remote.get("status") or "") == "cancel_refused":
                return self._write_state(job_id, {"message": str(remote.get("message") or "Forge Bridge refused cancellation.")})
        except Exception as exc:  # noqa: BLE001
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["cancel_warning"] = str(exc)
            self._write_state(job_id, {"runtime": runtime})
        return self._state(job_id)

    def recover(self, job_id: str) -> dict[str, Any]:
        state = self._state(job_id)
        if not state:
            return {"job_id": job_id, "profile_id": self.profile_id, "status": "failed", "message": "Unknown Forge Bridge job.", "outputs": [], "runtime": {"execution_state": "missing"}}
        try:
            remote = self.client.bridge_get_job(job_id, include_images=True, timeout=20.0)
        except Exception:
            return super().recover(job_id)
        remote_status = str(remote.get("status") or "")
        if remote_status == "completed":
            response = remote.get("result") if isinstance(remote.get("result"), dict) else {}
            outputs = self._spool_outputs(job_id, response)
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["execution_state"] = "completed_response_spooled"
            runtime["bridge"] = {"enabled": True, "recovered": True, "bridge_job_id": job_id}
            runtime["progress"] = {"source": "forge.bridge.job", "percent": 100, "label": "Recovered from Forge Bridge", "eta_seconds": 0}
            return self._write_state(job_id, {"status": "completed", "message": "Recovered completed Forge Bridge job.", "outputs": outputs, "runtime": runtime, "recoverable": True})
        if remote_status in {"queued", "running"}:
            runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
            runtime["bridge"] = {"enabled": True, "reattached": True, "bridge_job_id": job_id}
            self._write_state(job_id, {"status": "queued", "message": "Reattaching to Forge Bridge job.", "runtime": runtime, "recoverable": True})
            self.ensure_worker()
            return self._state(job_id)
        if remote_status in {"failed", "cancelled"}:
            return self._write_state(job_id, {"status": remote_status, "message": str(remote.get("message") or f"Forge Bridge job {remote_status}."), "error": str(remote.get("error") or ""), "recoverable": True})
        return super().recover(job_id)

def get_forge_job_manager(
    profile: dict[str, Any],
    client: ForgeNeoClient,
    *,
    root_dir: Path | str | None = None,
    bridge_decision: ForgeBridgeDecision | None = None,
) -> ForgeNeoJobManager:
    root = Path(root_dir).resolve() if root_dir is not None else ROOT_DIR.resolve()
    decision = bridge_decision
    if decision is None:
        handshake: dict[str, Any] = {}
        if hasattr(client, "bridge_handshake"):
            try:
                handshake = client.bridge_handshake(timeout=4.0)
            except Exception:  # noqa: BLE001 - optional bridge must never break standard fallback.
                handshake = {}
        decision = decide_forge_bridge(profile, handshake=handshake)
    key = f"{_manager_key(profile, root)}::{'bridge' if decision.use_bridge else 'standard'}"
    with _MANAGER_LOCK:
        manager = _MANAGERS.get(key)
        if manager is None:
            manager_cls = ForgeNeoBridgeJobManager if decision.use_bridge else ForgeNeoJobManager
            manager = manager_cls(profile, client, root_dir=root)
            _MANAGERS[key] = manager
        return manager


def reset_forge_job_managers_for_tests() -> None:
    with _MANAGER_LOCK:
        for manager in _MANAGERS.values():
            manager.stop()
        _MANAGERS.clear()
