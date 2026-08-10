from __future__ import annotations

import json
import sqlite3
import threading
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from .unified_schema import ensure_unified_memory_schema

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"
JOB_SCHEMA_ID = "neo.memory.jobs.phase10.v1"
JOB_VERSION = "unified-background-jobs.v1"

_TERMINAL = {"completed", "failed", "cancelled"}
_ACTIVE = {"queued", "running"}
_RUNNING_THREADS: dict[str, threading.Thread] = {}
_THREADS_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _elapsed_seconds(started_at: Any, ended_at: Any) -> float:
    start = str(started_at or "").strip()
    end = str(ended_at or "").strip()
    if not start or not end:
        return 0.0
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        return max(0.0, round((end_dt - start_dt).total_seconds(), 3))
    except Exception:
        return 0.0


class MemoryJobCancelled(RuntimeError):
    pass


@dataclass
class MemoryJobContext:
    service: "MemoryJobService"
    job_id: str

    def progress(
        self,
        *,
        phase: str,
        current: int | None = None,
        total: int | None = None,
        percent: int | None = None,
        message: str = "",
        warning: str = "",
        error: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self.service.update_progress(
            self.job_id,
            phase=phase,
            current=current,
            total=total,
            percent=percent,
            message=message,
            warning=warning,
            error=error,
            extra=extra,
        )

    def checkpoint(self, message: str = "") -> None:
        if self.service.cancel_requested(self.job_id):
            if message:
                self.progress(phase="cancelling", message=message)
            raise MemoryJobCancelled("Job cancelled at a safe checkpoint.")


class MemoryJobService:
    """Phase 10 persistent background-job authority backed by neo_memory_jobs.

    Threads are process-local workers, but state/progress/results are durable in
    SQLite. Navigation/re-rendering never owns a job. A process restart marks a
    previously running worker as interrupted so the stored payload can be retried
    deliberately instead of pretending it is still running.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()
        self._recover_interrupted_jobs()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        ensure_unified_memory_schema(conn)
        self._ensure_columns(conn)
        return conn

    def _ensure_schema(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            ensure_unified_memory_schema(conn)
            self._ensure_columns(conn)

    @staticmethod
    def _ensure_columns(conn: sqlite3.Connection) -> None:
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(neo_memory_jobs)").fetchall()}
        additions = {
            "title": "TEXT NOT NULL DEFAULT ''",
            "payload_json": "TEXT NOT NULL DEFAULT '{}'",
            "dedupe_key": "TEXT NOT NULL DEFAULT ''",
            "retry_of_job_id": "TEXT",
            "cancel_requested": "INTEGER NOT NULL DEFAULT 0",
            "attempt": "INTEGER NOT NULL DEFAULT 1",
        }
        for name, ddl in additions.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE neo_memory_jobs ADD COLUMN {name} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_jobs_dedupe ON neo_memory_jobs(job_type, dedupe_key, status, updated_at)")

    def _recover_interrupted_jobs(self) -> None:
        stamp = _now()
        with self._connect() as conn:
            rows = conn.execute("SELECT job_id, progress_json FROM neo_memory_jobs WHERE status='running'").fetchall()
            for row in rows:
                with _THREADS_LOCK:
                    active_thread = _RUNNING_THREADS.get(str(row["job_id"]))
                    if active_thread and active_thread.is_alive():
                        continue
                progress = _loads(row["progress_json"], {})
                progress.update({
                    "phase": "interrupted",
                    "message": "Worker process stopped before this job finished. Retry is available.",
                    "can_retry": True,
                    "can_cancel": False,
                    "updated_at": stamp,
                })
                conn.execute(
                    "UPDATE neo_memory_jobs SET status='failed', finished_at=?, updated_at=?, progress_json=?, error=? WHERE job_id=?",
                    (stamp, stamp, _json(progress), "Worker process interrupted before completion.", row["job_id"]),
                )

    @staticmethod
    def _row_payload(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        progress = _loads(data.pop("progress_json", "{}"), {})
        result = _loads(data.pop("result_json", "{}"), {})
        payload = _loads(data.pop("payload_json", "{}"), {})
        status = str(data.get("status") or "queued")
        elapsed_end = _now() if status in _ACTIVE else (data.get("finished_at") or data.get("updated_at") or _now())
        elapsed_seconds = _elapsed_seconds(data.get("started_at"), elapsed_end)
        return {
            **data,
            "elapsed_seconds": elapsed_seconds,
            "progress": progress,
            "result": result,
            "payload": payload,
            "cancel_requested": bool(data.get("cancel_requested")),
            "can_cancel": status in _ACTIVE and not bool(data.get("cancel_requested")),
            "can_retry": status in {"failed", "cancelled"},
        }

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM neo_memory_jobs WHERE job_id=?", (str(job_id or ""),)).fetchone()
        return self._row_payload(row) if row else None

    def list(self, *, status: str = "", job_type: str = "", limit: int = 50) -> dict[str, Any]:
        where: list[str] = []
        params: list[Any] = []
        if status:
            where.append("status=?")
            params.append(status)
        if job_type:
            where.append("job_type=?")
            params.append(job_type)
        clause = " WHERE " + " AND ".join(where) if where else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM neo_memory_jobs{clause} ORDER BY updated_at DESC LIMIT ?",
                (*params, max(1, min(int(limit or 50), 200))),
            ).fetchall()
            summary_rows = conn.execute("SELECT status, COUNT(*) AS count FROM neo_memory_jobs GROUP BY status").fetchall()
        jobs = [self._row_payload(row) for row in rows]
        summary = {str(row["status"]): int(row["count"]) for row in summary_rows}
        summary["total"] = sum(summary.values())
        return {
            "ok": True,
            "schema_id": JOB_SCHEMA_ID,
            "version": JOB_VERSION,
            "status": "ready",
            "jobs": jobs,
            "summary": summary,
            "active_job_id": next((job["job_id"] for job in jobs if job.get("status") in _ACTIVE), ""),
        }

    def _find_active_dedupe(self, job_type: str, dedupe_key: str) -> dict[str, Any] | None:
        if not dedupe_key:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM neo_memory_jobs WHERE job_type=? AND dedupe_key=? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1",
                (job_type, dedupe_key),
            ).fetchone()
        return self._row_payload(row) if row else None

    def create(
        self,
        *,
        job_type: str,
        payload: dict[str, Any] | None = None,
        title: str = "",
        surface: str = "global",
        project_id: str | None = None,
        scope_id: str | None = None,
        dedupe_key: str = "",
        retry_of_job_id: str | None = None,
        start: bool = True,
    ) -> dict[str, Any]:
        job_type = str(job_type or "").strip()
        if not job_type:
            raise ValueError("job_type is required")
        payload = dict(payload or {})
        active = self._find_active_dedupe(job_type, dedupe_key)
        if active:
            return {"ok": True, "schema_id": JOB_SCHEMA_ID, "status": "already_active", "deduplicated": True, "job": active}
        job_id = f"memjob_{job_type}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        stamp = _now()
        progress = {
            "phase": "queued",
            "current": 0,
            "total": 0,
            "percent": 0,
            "message": "Queued.",
            "warnings": [],
            "errors": [],
            "can_cancel": True,
            "can_retry": False,
            "updated_at": stamp,
        }
        attempt = 1
        if retry_of_job_id:
            previous = self.get(retry_of_job_id)
            attempt = _safe_int((previous or {}).get("attempt"), 1) + 1
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO neo_memory_jobs (
                    job_id, job_type, status, surface, project_id, scope_id, started_at, finished_at,
                    progress_json, result_json, error, created_at, updated_at, title, payload_json,
                    dedupe_key, retry_of_job_id, cancel_requested, attempt
                ) VALUES (?, ?, 'queued', ?, ?, ?, NULL, NULL, ?, '{}', '', ?, ?, ?, ?, ?, ?, 0, ?)
                """,
                (job_id, job_type, surface or "global", project_id, scope_id, _json(progress), stamp, stamp, title or job_type.replace("_", " ").title(), _json(payload), dedupe_key, retry_of_job_id, attempt),
            )
        job = self.get(job_id) or {"job_id": job_id, "status": "queued"}
        if start:
            self.start(job_id)
        return {"ok": True, "schema_id": JOB_SCHEMA_ID, "status": "queued", "deduplicated": False, "job": self.get(job_id) or job}

    def start(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if not job:
            raise KeyError(job_id)
        if job.get("status") not in {"queued"}:
            return {"ok": True, "schema_id": JOB_SCHEMA_ID, "status": str(job.get("status") or "unknown"), "job": job}
        with _THREADS_LOCK:
            existing = _RUNNING_THREADS.get(job_id)
            if existing and existing.is_alive():
                return {"ok": True, "schema_id": JOB_SCHEMA_ID, "status": "running", "job": self.get(job_id)}
            thread = threading.Thread(target=self._run_worker, args=(job_id,), daemon=True, name=f"neo-memory-{job_id[-12:]}")
            _RUNNING_THREADS[job_id] = thread
            thread.start()
        return {"ok": True, "schema_id": JOB_SCHEMA_ID, "status": "running", "job": self.get(job_id)}

    def update_progress(
        self,
        job_id: str,
        *,
        phase: str,
        current: int | None = None,
        total: int | None = None,
        percent: int | None = None,
        message: str = "",
        warning: str = "",
        error: str = "",
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        stamp = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT progress_json, status, cancel_requested FROM neo_memory_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            progress = _loads(row["progress_json"], {})
            if current is not None:
                progress["current"] = int(current)
            if total is not None:
                progress["total"] = max(0, int(total))
            if percent is None and current is not None and total:
                percent = round((max(0, int(current)) / max(1, int(total))) * 100)
            if percent is not None:
                progress["percent"] = max(0, min(100, int(percent)))
            progress["phase"] = str(phase or progress.get("phase") or "running")
            if message:
                progress["message"] = str(message)[:1000]
            progress.setdefault("warnings", [])
            progress.setdefault("errors", [])
            if warning and warning not in progress["warnings"]:
                progress["warnings"] = (progress["warnings"] + [str(warning)[:1000]])[-20:]
            if error and error not in progress["errors"]:
                progress["errors"] = (progress["errors"] + [str(error)[:1000]])[-20:]
            if extra:
                progress.update(extra)
            progress["cancel_requested"] = bool(row["cancel_requested"])
            progress["can_cancel"] = str(row["status"]) in _ACTIVE and not bool(row["cancel_requested"])
            progress["updated_at"] = stamp
            conn.execute("UPDATE neo_memory_jobs SET progress_json=?, updated_at=? WHERE job_id=?", (_json(progress), stamp, job_id))
        return self.get(job_id) or {}

    def cancel_requested(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT cancel_requested, status FROM neo_memory_jobs WHERE job_id=?", (job_id,)).fetchone()
        return bool(row and (row["cancel_requested"] or row["status"] == "cancelled"))

    def cancel(self, job_id: str) -> dict[str, Any]:
        stamp = _now()
        with self._connect() as conn:
            row = conn.execute("SELECT status, progress_json FROM neo_memory_jobs WHERE job_id=?", (job_id,)).fetchone()
            if not row:
                raise KeyError(job_id)
            status = str(row["status"])
            progress = _loads(row["progress_json"], {})
            if status in _TERMINAL:
                return {"ok": True, "schema_id": JOB_SCHEMA_ID, "status": status, "job": self.get(job_id)}
            if status == "queued":
                progress.update({"phase": "cancelled", "percent": 100, "message": "Cancelled before the worker started.", "can_cancel": False, "can_retry": True, "updated_at": stamp})
                conn.execute("UPDATE neo_memory_jobs SET status='cancelled', cancel_requested=1, finished_at=?, updated_at=?, progress_json=? WHERE job_id=?", (stamp, stamp, _json(progress), job_id))
            else:
                progress.update({"phase": "cancelling", "message": "Cancel requested; the job will stop at the next safe checkpoint.", "cancel_requested": True, "can_cancel": False, "updated_at": stamp})
                conn.execute("UPDATE neo_memory_jobs SET cancel_requested=1, updated_at=?, progress_json=? WHERE job_id=?", (stamp, _json(progress), job_id))
        return {"ok": True, "schema_id": JOB_SCHEMA_ID, "status": "cancel_requested", "job": self.get(job_id)}

    def retry(self, job_id: str) -> dict[str, Any]:
        previous = self.get(job_id)
        if not previous:
            raise KeyError(job_id)
        if previous.get("status") not in {"failed", "cancelled"}:
            raise ValueError("Only failed or cancelled memory jobs can be retried.")
        return self.create(
            job_type=str(previous.get("job_type") or ""),
            payload=dict(previous.get("payload") or {}),
            title=str(previous.get("title") or ""),
            surface=str(previous.get("surface") or "global"),
            project_id=previous.get("project_id"),
            scope_id=previous.get("scope_id"),
            dedupe_key=str(previous.get("dedupe_key") or ""),
            retry_of_job_id=job_id,
            start=True,
        )

    def _run_worker(self, job_id: str) -> None:
        stamp = _now()
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT status, cancel_requested, progress_json FROM neo_memory_jobs WHERE job_id=?", (job_id,)).fetchone()
                if not row or row["status"] != "queued":
                    return
                if row["cancel_requested"]:
                    self.cancel(job_id)
                    return
                progress = _loads(row["progress_json"], {})
                progress.update({"phase": "starting", "percent": max(1, int(progress.get("percent") or 0)), "message": "Worker started.", "can_cancel": True, "updated_at": stamp})
                conn.execute("UPDATE neo_memory_jobs SET status='running', started_at=?, updated_at=?, progress_json=? WHERE job_id=?", (stamp, stamp, _json(progress), job_id))
            job = self.get(job_id) or {}
            handler = self._resolve_handler(str(job.get("job_type") or ""))
            ctx = MemoryJobContext(self, job_id)
            ctx.checkpoint()
            result = handler(ctx, dict(job.get("payload") or {}))
            ctx.checkpoint()
            finished = _now()
            progress = (self.get(job_id) or {}).get("progress") or {}
            progress.update({"phase": "completed", "percent": 100, "message": str((result or {}).get("message") or "Completed."), "can_cancel": False, "can_retry": False, "updated_at": finished})
            with self._connect() as conn:
                conn.execute(
                    "UPDATE neo_memory_jobs SET status='completed', finished_at=?, updated_at=?, progress_json=?, result_json=?, error='' WHERE job_id=?",
                    (finished, finished, _json(progress), _json(result or {}), job_id),
                )
        except MemoryJobCancelled as exc:
            finished = _now()
            job = self.get(job_id) or {}
            progress = dict(job.get("progress") or {})
            progress.update({"phase": "cancelled", "percent": 100, "message": str(exc), "can_cancel": False, "can_retry": True, "updated_at": finished})
            with self._connect() as conn:
                conn.execute("UPDATE neo_memory_jobs SET status='cancelled', finished_at=?, updated_at=?, progress_json=? WHERE job_id=?", (finished, finished, _json(progress), job_id))
        except Exception as exc:
            finished = _now()
            job = self.get(job_id) or {}
            progress = dict(job.get("progress") or {})
            progress.setdefault("errors", [])
            progress["errors"] = (list(progress.get("errors") or []) + [str(exc)[:1000]])[-20:]
            progress.update({"phase": "failed", "message": str(exc)[:1000], "can_cancel": False, "can_retry": True, "updated_at": finished})
            with self._connect() as conn:
                conn.execute(
                    "UPDATE neo_memory_jobs SET status='failed', finished_at=?, updated_at=?, progress_json=?, error=?, result_json=? WHERE job_id=?",
                    (finished, finished, _json(progress), str(exc)[:2000], _json({"traceback": traceback.format_exc()[-8000:]}), job_id),
                )
        finally:
            with _THREADS_LOCK:
                _RUNNING_THREADS.pop(job_id, None)

    def _resolve_handler(self, job_type: str) -> Callable[[MemoryJobContext, dict[str, Any]], dict[str, Any]]:
        handlers: dict[str, Callable[[MemoryJobContext, dict[str, Any]], dict[str, Any]]] = {
            "project_brain_rebuild": self._handle_project_brain_rebuild,
            "memory_consolidation": self._handle_memory_consolidation,
            "embedding_reindex": self._handle_embedding_reindex,
            "memory_writeback": self._handle_memory_writeback,
            "roleplay_memory_vectors": self._handle_roleplay_memory_vectors,
        }
        handler = handlers.get(job_type)
        if not handler:
            raise ValueError(f"Unsupported memory job type: {job_type}")
        return handler

    def supported_job_types(self) -> list[str]:
        return ["project_brain_rebuild", "memory_consolidation", "embedding_reindex", "memory_writeback", "roleplay_memory_vectors"]

    def _handle_project_brain_rebuild(self, ctx: MemoryJobContext, payload: dict[str, Any]) -> dict[str, Any]:
        from neo_app.assistant.project_brain import rebuild_project_brain_payload

        return rebuild_project_brain_payload(payload, progress_callback=ctx.progress, cancel_callback=ctx.checkpoint)

    def _handle_memory_consolidation(self, ctx: MemoryJobContext, payload: dict[str, Any]) -> dict[str, Any]:
        from .consolidation_engine import UnifiedMemoryConsolidationEngine

        ctx.progress(phase="planning", percent=5, message="Planning memory consolidation groups.")
        ctx.checkpoint()
        engine = UnifiedMemoryConsolidationEngine(self.db_path)
        result = engine.run({**payload, "managed_job": True, "managed_job_id": ctx.job_id})
        ctx.progress(phase="consolidation_finalizing", percent=95, message="Finalizing memory consolidation.")
        ctx.checkpoint()
        return result

    def _handle_embedding_reindex(self, ctx: MemoryJobContext, payload: dict[str, Any]) -> dict[str, Any]:
        from .retrieval_engine import UnifiedMemoryRetrievalEngine

        ctx.progress(phase="embedding_index", percent=5, message="Loading queued memory fragments for embedding/indexing.")
        ctx.checkpoint()
        result = UnifiedMemoryRetrievalEngine(self.db_path).index_embeddings({**payload, "managed_job": True, "managed_job_id": ctx.job_id})
        ctx.progress(phase="embedding_finalizing", percent=95, message="Finalizing memory embedding / reindex.")
        ctx.checkpoint()
        return result

    def _handle_memory_writeback(self, ctx: MemoryJobContext, payload: dict[str, Any]) -> dict[str, Any]:
        from .writeback_engine import MemoryWritebackEngine

        ctx.progress(phase="writeback", percent=10, message="Evaluating durable-memory candidates.")
        ctx.checkpoint()
        result = MemoryWritebackEngine(self.db_path).run({**payload, "managed_job": True, "managed_job_id": ctx.job_id})
        ctx.progress(phase="writeback_finalizing", percent=95, message="Finalizing durable-memory writeback.")
        ctx.checkpoint()
        return result

    def _handle_roleplay_memory_vectors(self, ctx: MemoryJobContext, payload: dict[str, Any]) -> dict[str, Any]:
        from neo_app.roleplay.retrieval import index_roleplay_memory_vectors_payload

        ctx.progress(phase="loading", percent=15, message="Loading Roleplay memory rows.")
        ctx.checkpoint()
        mode = str(payload.get("mode") or "changed_only")
        index_payload = {
            "scope_id": str(payload.get("scope_id") or payload.get("scope") or ""),
            "limit": int(payload.get("limit") or 500),
            "force": bool(payload.get("force") or mode == "force_reindex"),
            "source": "unified_memory_job_service",
        }
        ctx.progress(phase="embedding_index", percent=35, message="Embedding and indexing Roleplay memory rows.")
        ctx.checkpoint()
        result = index_roleplay_memory_vectors_payload(index_payload)
        ctx.progress(phase="finalizing", percent=95, message="Finalizing Roleplay memory vector index.")
        return result


_JOB_SERVICES: dict[str, MemoryJobService] = {}
_JOB_SERVICES_LOCK = threading.Lock()


def get_memory_job_service(db_path: Path | None = None) -> MemoryJobService:
    target = Path(db_path or DEFAULT_DB_PATH).resolve()
    key = str(target)
    with _JOB_SERVICES_LOCK:
        service = _JOB_SERVICES.get(key)
        if service is None:
            service = MemoryJobService(target)
            _JOB_SERVICES[key] = service
        return service
