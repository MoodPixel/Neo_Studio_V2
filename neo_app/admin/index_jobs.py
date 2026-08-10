from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

from neo_app.admin.engine import ENGINE_DATA_DIR, INDEXING_STATE_PATH, ROOT_DIR, _read_json, _write_json
from neo_app.memory.job_service import get_memory_job_service
from neo_app.services.runtime_debug_logs import LOG_ROOT, display_path, log_surface_event

INDEX_JOBS_PATH = ENGINE_DATA_DIR / "index_jobs.json"
INDEX_JOB_LOG_DIR = ENGINE_DATA_DIR / "index_job_logs"
ADMIN_SURFACE_INDEX_JOB_LOG_DIR = LOG_ROOT / "admin" / "index_jobs"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _admin_log_summary(job: dict[str, Any] | None = None, *, message: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    job = job if isinstance(job, dict) else {}
    extra = extra if isinstance(extra, dict) else {}
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    return {
        "job_id": str(job.get("job_id") or extra.get("job_id") or ""),
        "job_type": str(job.get("job_type") or extra.get("job_type") or ""),
        "status": str(job.get("status") or extra.get("status") or ""),
        "mode": str((job.get("payload") or {}).get("mode") if isinstance(job.get("payload"), dict) else extra.get("mode") or ""),
        "scope_id": str(job.get("scope_id") or extra.get("scope_id") or ""),
        "progress": int(progress.get("percent") or extra.get("progress") or 0),
        "message": str(message or progress.get("message") or extra.get("message") or "")[:500],
    }


def _safe_log_admin_event(event: str, *, run_id: str = "", payload: dict[str, Any] | None = None, level: str = "INFO") -> None:
    try:
        log_surface_event("admin", event, run_id=run_id or None, level=level, payload=payload or {})
    except Exception:
        pass


def _admin_surface_log_path(job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in {"_", "-"}) or "job"
    return ADMIN_SURFACE_INDEX_JOB_LOG_DIR / f"{safe}.log"




def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT_DIR))
    except Exception:
        return str(path)

def _log_path(job_id: str) -> Path:
    safe = "".join(ch for ch in job_id if ch.isalnum() or ch in {"_", "-"}) or "job"
    return INDEX_JOB_LOG_DIR / f"{safe}.log"


def _append_log(job_id: str, message: str) -> None:
    INDEX_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ADMIN_SURFACE_INDEX_JOB_LOG_DIR.mkdir(parents=True, exist_ok=True)
    line = f"[{_now()}] {message}\n"
    for path in (_log_path(job_id), _admin_surface_log_path(job_id)):
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    _safe_log_admin_event(
        "admin.index_job.log_line",
        run_id=job_id,
        payload={"job_id": job_id, "message": str(message or "")[:500], "legacy_log_path": display_path(_log_path(job_id)), "surface_log_path": display_path(_admin_surface_log_path(job_id))},
    )


def _legacy_job(job: dict[str, Any]) -> dict[str, Any]:
    payload = job.get("payload") if isinstance(job.get("payload"), dict) else {}
    progress = job.get("progress") if isinstance(job.get("progress"), dict) else {}
    status = str(job.get("status") or "queued")
    return {
        "job_id": job.get("job_id"),
        "job_type": "roleplay_memory_vectors",
        "title": job.get("title") or "Roleplay memory vector indexing",
        "status": "pending" if status == "queued" else status,
        "mode": payload.get("mode") or "changed_only",
        "scope_id": job.get("scope_id") or payload.get("scope_id") or "",
        "limit": int(payload.get("limit") or 500),
        "force": bool(payload.get("force")),
        "progress": int(progress.get("percent") or 0),
        "cancel_requested": bool(job.get("cancel_requested")),
        "message": progress.get("message") or "",
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
        "started_at": job.get("started_at"),
        "completed_at": job.get("finished_at"),
        "result": job.get("result") or {},
        "error": job.get("error") or "",
        "log_path": _display_path(_log_path(str(job.get("job_id") or "job"))),
        "unified_job": True,
    }


def _projection_payload() -> dict[str, Any]:
    service = get_memory_job_service()
    state = service.list(job_type="roleplay_memory_vectors", limit=100)
    jobs = [_legacy_job(job) for job in state.get("jobs") or []]
    summary = {
        "total": len(jobs),
        "pending": sum(1 for job in jobs if job.get("status") == "pending"),
        "running": sum(1 for job in jobs if job.get("status") == "running"),
        "completed": sum(1 for job in jobs if job.get("status") == "completed"),
        "failed": sum(1 for job in jobs if job.get("status") == "failed"),
        "cancelled": sum(1 for job in jobs if job.get("status") == "cancelled"),
    }
    return {
        "schema_id": "neo.admin.engine.index_jobs.v2",
        "legacy_schema_id": "neo.admin.engine.index_jobs.v1",
        "version": "phase10-unified-memory-jobs-bridge",
        "status": "ready",
        "updated_at": _now(),
        "jobs": jobs[:50],
        "active_job_id": next((str(job.get("job_id") or "") for job in jobs if job.get("status") in {"pending", "running"}), ""),
        "summary": summary,
        "paths": {"jobs_state": _display_path(INDEX_JOBS_PATH), "logs_root": _display_path(INDEX_JOB_LOG_DIR)},
        "supported_job_types": ["roleplay_memory_vectors"],
        "supported_modes": ["changed_only", "force_reindex"],
        "notes": [
            "Compatibility projection only: neo_memory_jobs is the Phase 10 authority.",
            "Legacy Admin routes remain available while Roleplay vector indexing runs through the unified Memory Job Service.",
        ],
    }


def _sync_legacy_projection(state: dict[str, Any]) -> None:
    ENGINE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    _write_json(INDEX_JOBS_PATH, state)
    try:
        indexing = _read_json(INDEXING_STATE_PATH, {})
        indexing.update({
            "status": "running" if state.get("summary", {}).get("running") else "ready",
            "job_queue_enabled": True,
            "job_queue_authority": "neo_memory_jobs",
            "active_job_id": state.get("active_job_id") or "",
            "pending_jobs": [job.get("job_id") for job in state.get("jobs") or [] if job.get("status") == "pending"],
            "running_jobs": [job.get("job_id") for job in state.get("jobs") or [] if job.get("status") == "running"],
            "completed_job_count": int(state.get("summary", {}).get("completed") or 0),
            "failed_job_count": int(state.get("summary", {}).get("failed") or 0),
            "cancelled_job_count": int(state.get("summary", {}).get("cancelled") or 0),
            "last_job_at": (state.get("jobs") or [{}])[0].get("updated_at") if state.get("jobs") else None,
        })
        _write_json(INDEXING_STATE_PATH, indexing)
    except Exception:
        pass


def index_job_queue_state_payload() -> dict[str, Any]:
    state = _projection_payload()
    _sync_legacy_projection(state)
    return state


def create_index_job_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    job_type = str(data.get("job_type") or "roleplay_memory_vectors")
    if job_type != "roleplay_memory_vectors":
        raise ValueError(f"Unsupported index job type: {job_type}")
    mode = str(data.get("mode") or ("force_reindex" if data.get("force") else "changed_only"))
    if mode not in {"changed_only", "force_reindex"}:
        raise ValueError(f"Unsupported index job mode: {mode}")
    scope_id = str(data.get("scope_id") or data.get("scope") or "")
    created = get_memory_job_service().create(
        job_type="roleplay_memory_vectors",
        payload={"mode": mode, "scope_id": scope_id, "limit": int(data.get("limit") or 500), "force": bool(data.get("force") or mode == "force_reindex")},
        title=str(data.get("title") or "Roleplay memory vector indexing"),
        surface="roleplay",
        scope_id=scope_id or None,
        dedupe_key=f"roleplay_memory_vectors:{scope_id or 'all'}:{mode}",
    )
    unified_job = created.get("job") or {}
    job = _legacy_job(unified_job)
    _append_log(str(job.get("job_id") or ""), f"Unified Memory Job queued: {json.dumps(_admin_log_summary(unified_job), sort_keys=True)}")
    state = index_job_queue_state_payload()
    return {"schema_id": "neo.admin.engine.index_job.create.v2", "legacy_schema_id": "neo.admin.engine.index_job.create.v1", "status": "queued" if created.get("status") in {"queued", "already_active"} else created.get("status"), "job": job, "queue": state, "unified_memory_job": unified_job, "deduplicated": bool(created.get("deduplicated"))}


def cancel_index_job_payload(job_id: str) -> dict[str, Any]:
    if not job_id:
        raise ValueError("job_id is required")
    result = get_memory_job_service().cancel(job_id)
    job = _legacy_job(result.get("job") or {})
    _append_log(job_id, "Cancel requested through unified Memory Job Service.")
    return {"schema_id": "neo.admin.engine.index_job.cancel.v2", "legacy_schema_id": "neo.admin.engine.index_job.cancel.v1", "status": result.get("status") or "cancel_requested", "job": job, "queue": index_job_queue_state_payload(), "unified_memory_job": result.get("job")}


def read_index_job_log_payload(job_id: str, tail_lines: int = 200) -> dict[str, Any]:
    lines: list[str] = []
    path = _log_path(job_id)
    if path.exists():
        lines.extend(path.read_text(encoding="utf-8", errors="ignore").splitlines())
    job = get_memory_job_service().get(job_id)
    if job:
        progress = job.get("progress") or {}
        lines.append(f"[{job.get('updated_at') or _now()}] [{job.get('status')}] {progress.get('phase') or ''}: {progress.get('message') or ''}")
        if job.get("error"):
            lines.append(f"ERROR: {job.get('error')}")
    lines = lines[-max(1, min(int(tail_lines or 200), 1000)):]
    return {"schema_id": "neo.admin.engine.index_job.log.v2", "legacy_schema_id": "neo.admin.engine.index_job.log.v1", "job_id": job_id, "line_count": len(lines), "lines": lines, "unified_memory_job": job}
