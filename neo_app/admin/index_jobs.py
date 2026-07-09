from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, Thread
from typing import Any
from uuid import uuid4
import json
import traceback

from neo_app.admin.engine import ENGINE_DATA_DIR, INDEXING_STATE_PATH, ROOT_DIR, _read_json, _write_json
from neo_app.services.runtime_debug_logs import LOG_ROOT, display_path, log_surface_event, record_surface_error, record_surface_snapshot

INDEX_JOBS_PATH = ENGINE_DATA_DIR / "index_jobs.json"
INDEX_JOB_LOG_DIR = ENGINE_DATA_DIR / "index_job_logs"
ADMIN_SURFACE_INDEX_JOB_LOG_DIR = LOG_ROOT / "admin" / "index_jobs"

_LOCK = Lock()
_RUNNING_THREADS: dict[str, Thread] = {}


def _admin_log_summary(job: dict[str, Any] | None = None, *, message: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    job = job if isinstance(job, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    return {
        "job_id": str(job.get("job_id") or extra.get("job_id") or ""),
        "job_type": str(job.get("job_type") or extra.get("job_type") or ""),
        "status": str(job.get("status") or extra.get("status") or ""),
        "mode": str(job.get("mode") or extra.get("mode") or ""),
        "scope_id": str(job.get("scope_id") or extra.get("scope_id") or ""),
        "progress": int(job.get("progress") or extra.get("progress") or 0),
        "message": str(message or job.get("message") or extra.get("message") or "")[:500],
    }


def _safe_log_admin_event(event: str, *, run_id: str = "", payload: dict[str, Any] | None = None, level: str = "INFO") -> None:
    try:
        log_surface_event("admin", event, run_id=run_id or None, level=level, payload=payload or {})
    except Exception:
        pass


def _admin_surface_log_path(job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in {"_", "-"}) or "job"
    return ADMIN_SURFACE_INDEX_JOB_LOG_DIR / f"{safe}.log"



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_state() -> dict[str, Any]:
    return {
        "schema_id": "neo.admin.engine.index_jobs.v1",
        "version": "0.1.0-background-indexing",
        "status": "ready",
        "updated_at": _now(),
        "jobs": [],
        "active_job_id": "",
        "paths": {
            "jobs_state": str(INDEX_JOBS_PATH.relative_to(ROOT_DIR)),
            "logs_root": str(INDEX_JOB_LOG_DIR.relative_to(ROOT_DIR)),
        },
        "supported_job_types": ["roleplay_memory_vectors"],
        "supported_modes": ["changed_only", "force_reindex"],
        "notes": [
            "Admin owns background indexing jobs.",
            "Roleplay requests indexing through Admin Engine and keeps direct synchronous indexing as fallback.",
        ],
    }


def _ensure_state() -> dict[str, Any]:
    ENGINE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_SURFACE_INDEX_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    if not INDEX_JOBS_PATH.exists():
        _write_json(INDEX_JOBS_PATH, _default_state())
    state = _read_json(INDEX_JOBS_PATH, _default_state())
    for key, value in _default_state().items():
        if key not in state:
            state[key] = value
    return state


def _write_state(state: dict[str, Any]) -> None:
    state["updated_at"] = _now()
    _write_json(INDEX_JOBS_PATH, state)


def _log_path(job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in {"_", "-"}) or "job"
    return INDEX_JOB_LOG_DIR / f"{safe}.log"


def _append_log(job_id: str, message: str) -> None:
    INDEX_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_SURFACE_INDEX_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{_now()}] {message}\n"
    with _log_path(job_id).open("a", encoding="utf-8") as handle:
        handle.write(line)
    with _admin_surface_log_path(job_id).open("a", encoding="utf-8") as handle:
        handle.write(line)
    _safe_log_admin_event("admin.index_job.log_line", run_id=job_id, payload={"job_id": job_id, "message": str(message or "")[:500], "legacy_log_path": display_path(_log_path(job_id)), "surface_log_path": display_path(_admin_surface_log_path(job_id))})


def _find_job(state: dict[str, Any], job_id: str) -> dict[str, Any] | None:
    for job in state.get("jobs") or []:
        if job.get("job_id") == job_id:
            return job
    return None


def _update_job(job_id: str, **changes: Any) -> dict[str, Any]:
    with _LOCK:
        state = _ensure_state()
        job = _find_job(state, job_id)
        if not job:
            raise KeyError(job_id)
        job.update(changes)
        job["updated_at"] = _now()
        running = [item.get("job_id") for item in state.get("jobs") or [] if item.get("status") in {"pending", "running"}]
        state["active_job_id"] = running[0] if running else ""
        _write_state(state)
        _sync_engine_indexing_state(state)
        return job


def _sync_engine_indexing_state(job_state: dict[str, Any]) -> None:
    try:
        indexing = _read_json(INDEXING_STATE_PATH, {})
        jobs = job_state.get("jobs") or []
        pending = [job for job in jobs if job.get("status") == "pending"]
        running = [job for job in jobs if job.get("status") == "running"]
        completed = [job for job in jobs if job.get("status") == "completed"]
        failed = [job for job in jobs if job.get("status") == "failed"]
        cancelled = [job for job in jobs if job.get("status") == "cancelled"]
        indexing.update({
            "status": "running" if running else "ready",
            "job_queue_enabled": True,
            "active_job_id": running[0].get("job_id") if running else "",
            "pending_jobs": [job.get("job_id") for job in pending],
            "running_jobs": [job.get("job_id") for job in running],
            "completed_job_count": len(completed),
            "failed_job_count": len(failed),
            "cancelled_job_count": len(cancelled),
            "last_job_at": jobs[0].get("updated_at") if jobs else None,
        })
        if completed:
            indexing["last_indexed_at"] = completed[0].get("completed_at") or completed[0].get("updated_at")
        _write_json(INDEXING_STATE_PATH, indexing)
    except Exception:
        pass


def index_job_queue_state_payload() -> dict[str, Any]:
    with _LOCK:
        state = _ensure_state()
        jobs = sorted(state.get("jobs") or [], key=lambda item: item.get("created_at") or "", reverse=True)
        state["jobs"] = jobs[:50]
        summary = {
            "total": len(jobs),
            "pending": sum(1 for job in jobs if job.get("status") == "pending"),
            "running": sum(1 for job in jobs if job.get("status") == "running"),
            "completed": sum(1 for job in jobs if job.get("status") == "completed"),
            "failed": sum(1 for job in jobs if job.get("status") == "failed"),
            "cancelled": sum(1 for job in jobs if job.get("status") == "cancelled"),
        }
        state["summary"] = summary
        _sync_engine_indexing_state(state)
        return state


def _run_roleplay_memory_vector_job(job_id: str, payload: dict[str, Any]) -> None:
    try:
        job = _update_job(job_id, status="running", progress=5, started_at=_now(), message="Index job started.")
        _append_log(job_id, "Index job started.")
        _safe_log_admin_event("admin.index_job.running", run_id=job_id, payload=_admin_log_summary(job, message="Index job started."))
        with _LOCK:
            state = _ensure_state()
            job = _find_job(state, job_id)
            if job and job.get("cancel_requested"):
                cancelled_job = _update_job(job_id, status="cancelled", progress=100, completed_at=_now(), message="Cancelled before indexing started.")
                _append_log(job_id, "Cancelled before indexing started.")
                _safe_log_admin_event("admin.index_job.cancelled", run_id=job_id, level="WARNING", payload=_admin_log_summary(cancelled_job, message="Cancelled before indexing started."))
                return
        _update_job(job_id, progress=18, message="Loading Roleplay memory rows.")
        _append_log(job_id, "Loading Roleplay memory rows.")
        from neo_app.roleplay.retrieval import index_roleplay_memory_vectors_payload

        mode = str(payload.get("mode") or "changed_only")
        index_payload = {
            "scope_id": str(payload.get("scope_id") or payload.get("scope") or ""),
            "limit": int(payload.get("limit") or 500),
            "force": bool(payload.get("force") or mode == "force_reindex"),
            "source": "admin_background_index_job",
        }
        _update_job(job_id, progress=36, message="Embedding and indexing memory rows.")
        _append_log(job_id, f"Index payload: {json.dumps(index_payload, sort_keys=True)}")
        result = index_roleplay_memory_vectors_payload(index_payload)
        index_result = result.get("index") or result
        completed_job = _update_job(
            job_id,
            status="completed",
            progress=100,
            completed_at=_now(),
            message="Index job completed.",
            result={
                "status": index_result.get("status"),
                "indexed_count": index_result.get("indexed_count", 0),
                "skipped_count": index_result.get("skipped_count", 0),
                "source_row_count": index_result.get("source_row_count", 0),
                "mode": index_result.get("mode") or index_result.get("embedding_mode") or "background_index",
            },
        )
        _append_log(job_id, f"Index job completed: {json.dumps(index_result, sort_keys=True, default=str)[:4000]}")
        _safe_log_admin_event("admin.index_job.completed", run_id=job_id, payload={**_admin_log_summary(completed_job, message="Index job completed."), "result": completed_job.get("result") or {}})
        try:
            record_surface_snapshot("admin", "neo_last_index_job.json", {"job": completed_job, "summary": _admin_log_summary(completed_job, message="Index job completed.")}, run_id=job_id)
        except Exception:
            pass
    except Exception as exc:
        failed_job = _update_job(job_id, status="failed", progress=100, completed_at=_now(), message=str(exc), error=traceback.format_exc())
        _append_log(job_id, f"FAILED: {exc}\n{traceback.format_exc()}")
        _safe_log_admin_event("admin.index_job.failed", run_id=job_id, level="ERROR", payload=_admin_log_summary(failed_job, message=str(exc)))
        try:
            record_surface_error("admin", "Admin index job failed.", exc=exc, payload=_admin_log_summary(failed_job, message=str(exc)), run_id=job_id)
        except Exception:
            pass
    finally:
        _RUNNING_THREADS.pop(job_id, None)


def create_index_job_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    job_type = str(data.get("job_type") or "roleplay_memory_vectors")
    if job_type != "roleplay_memory_vectors":
        raise ValueError(f"Unsupported index job type: {job_type}")
    mode = str(data.get("mode") or ("force_reindex" if data.get("force") else "changed_only"))
    if mode not in {"changed_only", "force_reindex"}:
        raise ValueError(f"Unsupported index job mode: {mode}")
    job_id = f"idx_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
    job = {
        "job_id": job_id,
        "job_type": job_type,
        "title": str(data.get("title") or "Roleplay memory vector indexing"),
        "status": "pending",
        "mode": mode,
        "scope_id": str(data.get("scope_id") or data.get("scope") or ""),
        "limit": int(data.get("limit") or 500),
        "force": bool(data.get("force") or mode == "force_reindex"),
        "progress": 0,
        "cancel_requested": False,
        "message": "Queued.",
        "created_at": _now(),
        "updated_at": _now(),
        "started_at": None,
        "completed_at": None,
        "result": {},
        "error": "",
        "log_path": str(_log_path(job_id).relative_to(ROOT_DIR)),
    }
    with _LOCK:
        state = _ensure_state()
        state["jobs"] = [job] + list(state.get("jobs") or [])[:99]
        state["active_job_id"] = job_id
        _write_state(state)
        _sync_engine_indexing_state(state)
    _append_log(job_id, f"Queued job: {json.dumps(job, sort_keys=True, default=str)}")
    _safe_log_admin_event("admin.index_job.created", run_id=job_id, payload=_admin_log_summary(job, message="Queued."))
    try:
        record_surface_snapshot("admin", "neo_last_index_job.json", {"job": job, "summary": _admin_log_summary(job, message="Queued.")}, run_id=job_id)
    except Exception:
        pass
    thread = Thread(target=_run_roleplay_memory_vector_job, args=(job_id, job), daemon=True)
    _RUNNING_THREADS[job_id] = thread
    thread.start()
    return {"schema_id": "neo.admin.engine.index_job.create.v1", "status": "queued", "job": job, "queue": index_job_queue_state_payload()}


def cancel_index_job_payload(job_id: str) -> dict[str, Any]:
    if not job_id:
        raise ValueError("job_id is required")
    with _LOCK:
        state = _ensure_state()
        job = _find_job(state, job_id)
        if not job:
            raise KeyError(job_id)
        if job.get("status") in {"completed", "failed", "cancelled"}:
            job["message"] = "Job already finished."
        elif job.get("status") == "pending":
            job["status"] = "cancelled"
            job["progress"] = 100
            job["completed_at"] = _now()
            job["message"] = "Cancelled before running."
        else:
            job["cancel_requested"] = True
            job["message"] = "Cancel requested; job will stop at the next safe checkpoint."
        job["updated_at"] = _now()
        _write_state(state)
        _sync_engine_indexing_state(state)
    _append_log(job_id, "Cancel requested.")
    _safe_log_admin_event("admin.index_job.cancel_requested", run_id=job_id, level="WARNING", payload=_admin_log_summary(job, message="Cancel requested."))
    return {"schema_id": "neo.admin.engine.index_job.cancel.v1", "status": "cancel_requested", "job": job, "queue": index_job_queue_state_payload()}


def read_index_job_log_payload(job_id: str, tail_lines: int = 200) -> dict[str, Any]:
    path = _log_path(job_id)
    if not path.exists():
        lines: list[str] = []
    else:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-max(1, min(tail_lines, 1000)):]
    return {"schema_id": "neo.admin.engine.index_job.log.v1", "job_id": job_id, "line_count": len(lines), "lines": lines}
