from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from neo_app.assistant.project_brain import PROJECT_BRAIN_DIR, rebuild_project_brain_payload
from neo_app.knowledge.service import NativeKnowledgeService

from .retrieval_engine import UnifiedMemoryRetrievalEngine
from .source_registry import get_memory_source
from .unified_schema import ensure_unified_memory_schema
from .vector_store import reset_chroma_collection

MIGRATION_SCHEMA_ID = "neo.memory.migration.nkb12.v1"
MIGRATION_VERSION = "1.0.0"
ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"
REPORT_ROOT = ROOT_DIR / "neo_data" / "memory" / "migrations" / "nkb12"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        result = json.loads(str(value or "{}"))
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _safe_project_dirs() -> list[Path]:
    if not PROJECT_BRAIN_DIR.exists():
        return []
    rows: list[Path] = []
    for path in PROJECT_BRAIN_DIR.iterdir():
        if not path.is_dir():
            continue
        if any((path / name).exists() for name in ("uploads", "snapshots", "memory_index")):
            rows.append(path)
    return sorted(rows, key=lambda item: item.name.lower())


def _upload_inventory(project_root: Path) -> dict[str, int]:
    uploads = project_root / "uploads"
    if not uploads.exists():
        return {"files": 0, "sidecars": 0, "structured_manifests": 0, "legacy_without_manifest": 0}
    sidecars = [p for p in uploads.glob("*.json") if not p.name.endswith(".knowledge.json")]
    manifests = list(uploads.glob("*.knowledge.json"))
    file_count = 0
    legacy = 0
    for sidecar in sidecars:
        record = _json_load(sidecar.read_text(encoding="utf-8", errors="replace"))
        stored_path = str(record.get("stored_path") or "").strip()
        if stored_path:
            path = ROOT_DIR / stored_path if not Path(stored_path).is_absolute() else Path(stored_path)
            if path.exists() and path.is_file():
                file_count += 1
                manifest = path.with_suffix(path.suffix + ".knowledge.json")
                if not manifest.exists():
                    legacy += 1
    return {"files": file_count, "sidecars": len(sidecars), "structured_manifests": len(manifests), "legacy_without_manifest": legacy}


@dataclass
class MigrationCallbacks:
    progress: Callable[..., Any] | None = None
    checkpoint: Callable[[str], Any] | None = None

    def emit(self, phase: str, percent: int, message: str, **extra: Any) -> None:
        if self.progress:
            self.progress(phase=phase, percent=percent, message=message, extra=extra or None)

    def check(self, message: str) -> None:
        if self.checkpoint:
            self.checkpoint(message)


class NKB12MigrationService:
    """Non-destructive migration/reindex coordinator for the Unified Brain.

    Native stores are never rewritten. Public Project files are replayed through
    the NKB-7 source-authoritative ingestion path. Static indexes and embeddings
    are disposable projections and may be rebuilt deliberately.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, root_dir: Path = ROOT_DIR) -> None:
        self.db_path = Path(db_path)
        self.root_dir = Path(root_dir)
        self.native = NativeKnowledgeService(self.root_dir)

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        ensure_unified_memory_schema(conn)
        return conn

    def _memory_inventory(self) -> dict[str, Any]:
        tables = (
            "neo_memory_objects", "neo_memory_facts", "neo_memory_edges",
            "neo_memory_fragments", "neo_memory_embeddings", "neo_memory_jobs",
        )
        counts: dict[str, int] = {}
        with self._connect() as conn:
            for table in tables:
                try:
                    counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except sqlite3.Error:
                    counts[table] = 0
            legacy_confirmed = int(conn.execute("SELECT COUNT(*) FROM neo_memory_fragments WHERE trust_level='confirmed'").fetchone()[0])
            legacy_canon = int(conn.execute("SELECT COUNT(*) FROM neo_memory_fragments WHERE memory_type='canon' OR json_extract(metadata_json, '$.canon') = 1").fetchone()[0]) if self._json1_available(conn) else 0
            queued_embeddings = int(conn.execute("SELECT COUNT(*) FROM neo_memory_fragments WHERE status='active' AND embedding_status!='indexed'").fetchone()[0])
            active_project_source = int(conn.execute("SELECT COUNT(*) FROM neo_memory_fragments WHERE status='active' AND metadata_json LIKE '%\"evidence_role\": \"project_source\"%'").fetchone()[0])
        return {
            "tables": counts,
            "legacy_confirmed_fragments": legacy_confirmed,
            "legacy_canon_like_fragments": legacy_canon,
            "queued_embeddings": queued_embeddings,
            "active_project_source_fragments": active_project_source,
            "policy": "Legacy trust/canon-like values are inventoried only; NKB-12 does not reinterpret them as NKB-4 authority semantics.",
        }

    @staticmethod
    def _json1_available(conn: sqlite3.Connection) -> bool:
        try:
            conn.execute("SELECT json('{}')").fetchone()
            return True
        except sqlite3.Error:
            return False

    def _native_inventory(self) -> dict[str, Any]:
        status = self.native.status(deep=True)
        registry = status.get("registry") if isinstance(status.get("registry"), dict) else {}
        adapters = registry.get("adapters") if isinstance(registry.get("adapters"), list) else []
        return {
            "status": status.get("status") or "ready",
            "adapters": [
                {
                    "adapter_id": row.get("adapter_id"),
                    "status": row.get("status"),
                    "authority_count": int(row.get("authority_count") or row.get("count") or 0),
                }
                for row in adapters if isinstance(row, dict)
            ],
            "policy": "Inventory only. NKB-12 never bulk-copies native authority into Unified Memory.",
        }

    def plan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        projects: list[dict[str, Any]] = []
        for project_root in _safe_project_dirs():
            inv = _upload_inventory(project_root)
            projects.append({"project_id": project_root.name, **inv, "needs_rebuild": bool(inv["files"] or inv["legacy_without_manifest"])})
        source_steps = []
        for source_id in ("system_records", "neo_codebase"):
            source = get_memory_source(source_id) or {}
            source_steps.append({"source_id": source_id, "exists": bool(source.get("exists")), "action": "reindex_projection", "destructive": False})
        memory = self._memory_inventory()
        plan = {
            "schema_id": MIGRATION_SCHEMA_ID,
            "version": MIGRATION_VERSION,
            "status": "planned",
            "planned_at": _now(),
            "dry_run": True,
            "projects": projects,
            "memory": memory,
            "native_knowledge": self._native_inventory(),
            "steps": [
                {"id": "backup_guard", "action": "record pre-migration inventory and report", "destructive": False},
                {"id": "project_brain", "action": "replay existing Project Brain projects through NKB-7", "project_count": len(projects), "destructive": False},
                {"id": "native_authority", "action": "inventory NKB-6 native authorities; do not rewrite native stores", "destructive": False},
                {"id": "static_indexes", "action": "rebuild disposable System Record and legacy code search projections", "sources": source_steps, "destructive": False},
                {"id": "embeddings", "action": "reindex active Unified Memory fragments using current embedding settings", "queued_before": memory.get("queued_embeddings", 0), "destructive": False},
                {"id": "validation", "action": "write report and re-scan counts", "destructive": False},
            ],
            "invariants": [
                "No native Image/Video/Voice/Roleplay/Prompt/Caption store is rewritten.",
                "No legacy confirmed/canon/status value is promoted into the NKB-4 authority model by migration alone.",
                "Project files remain authority; NKB-7 manifests/fragments are rebuildable projections.",
                "Re-running the migration is idempotent and may only refresh disposable projections/reports.",
                "No memory rows are deleted as a migration cleanup shortcut.",
            ],
        }
        if data.get("project_id"):
            target = str(data.get("project_id") or "")
            plan["projects"] = [row for row in projects if row.get("project_id") == target]
        return plan

    def status(self) -> dict[str, Any]:
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        reports = sorted(REPORT_ROOT.glob("migration_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        latest = {}
        if reports:
            try:
                latest = json.loads(reports[0].read_text(encoding="utf-8"))
            except Exception:
                latest = {}
        return {
            "schema_id": MIGRATION_SCHEMA_ID,
            "version": MIGRATION_VERSION,
            "status": "ready",
            "report_root": str(REPORT_ROOT),
            "latest_report": latest,
            "plan_endpoint": "/api/memory/migration/nkb12/plan",
            "run_endpoint": "/api/memory/migration/nkb12/run",
            "jobs_endpoint": "/api/memory/jobs?job_type=nkb12_migration",
        }

    def run(self, payload: dict[str, Any] | None = None, *, progress_callback: Callable[..., Any] | None = None, cancel_callback: Callable[[str], Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        callbacks = MigrationCallbacks(progress_callback, cancel_callback)
        plan = self.plan(data)
        migration_id = f"migration_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid4().hex[:8]}"
        REPORT_ROOT.mkdir(parents=True, exist_ok=True)
        report_path = REPORT_ROOT / f"{migration_id}.json"
        report: dict[str, Any] = {
            "schema_id": MIGRATION_SCHEMA_ID,
            "version": MIGRATION_VERSION,
            "migration_id": migration_id,
            "status": "running",
            "started_at": _now(),
            "plan": plan,
            "steps": [],
            "warnings": [],
            "errors": [],
        }
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

        def record(step: dict[str, Any]) -> None:
            report["steps"].append(step)
            report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        callbacks.emit("inventory", 5, "Recorded NKB-12 pre-migration inventory.")
        callbacks.check("Cancelled before Project Brain migration.")

        project_rows = list(plan.get("projects") or [])
        total_projects = len(project_rows)
        for index, row in enumerate(project_rows, start=1):
            project_id = str(row.get("project_id") or "general")
            callbacks.emit("project_brain", 8 + round((index - 1) / max(1, total_projects) * 38), f"Rebuilding Project Brain {index}/{total_projects}: {project_id}", current=index - 1, total=total_projects)
            callbacks.check(f"Cancelled before rebuilding Project Brain project {project_id}.")
            try:
                result = rebuild_project_brain_payload(
                    {"project_id": project_id, "surface": str(data.get("surface") or "assistant"), "limit": int(data.get("project_index_limit") or 80)},
                    progress_callback=None,
                    cancel_callback=callbacks.check,
                )
                record({"step": "project_brain", "project_id": project_id, "ok": bool(result.get("ok")), "report_id": ((result.get("report") or {}).get("report_id") if isinstance(result.get("report"), dict) else "")})
            except Exception as exc:
                warning = f"Project Brain rebuild failed for {project_id}: {exc}"
                report["warnings"].append(warning[:1000])
                record({"step": "project_brain", "project_id": project_id, "ok": False, "error": str(exc)[:1000]})

        callbacks.check("Cancelled before native-authority validation.")
        callbacks.emit("native_authority", 48, "Validating native knowledge adapters without rewriting native stores.")
        native_after = self._native_inventory()
        record({"step": "native_authority", "ok": native_after.get("status") in {"ready", "degraded"}, "inventory": native_after})

        from .service import get_memory_service
        memory_service = get_memory_service()
        if bool(data.get("reindex_static", True)):
            for idx, source_id in enumerate(("system_records", "neo_codebase"), start=1):
                callbacks.check(f"Cancelled before reindexing {source_id}.")
                callbacks.emit("static_indexes", 52 + idx * 10, f"Reindexing disposable source projection: {source_id}.")
                try:
                    reset = {"documents": 0, "chunks": 0, "embeddings": 0}
                    chroma_reset = {"status": "skipped"}
                    if bool(data.get("reset_static_projection", True)):
                        reset = memory_service.store.reset_source_projection(source_id)
                        chroma_reset = reset_chroma_collection(source_id)
                    result = memory_service.index_source(source_id, force=True, limit=data.get(f"{source_id}_limit"))
                    record({"step": "static_index", "source_id": source_id, "ok": bool(result.get("ok")), "projection_reset": reset, "chroma_reset": chroma_reset, "result": {k: v for k, v in result.items() if k not in {"documents", "chunks", "results"}}})
                except Exception as exc:
                    report["warnings"].append(f"Static index {source_id} failed: {exc}"[:1000])
                    record({"step": "static_index", "source_id": source_id, "ok": False, "error": str(exc)[:1000]})

        callbacks.check("Cancelled before embedding reindex.")
        embedding_result: dict[str, Any] = {"status": "skipped", "indexed_count": 0}
        if bool(data.get("reindex_embeddings", True)):
            callbacks.emit("embeddings", 78, "Rebuilding disposable Unified Memory embeddings using current settings.")
            try:
                reset_count = 0
                if bool(data.get("reset_embedding_projection", True)):
                    with self._connect() as conn:
                        reset_count = int(conn.execute("SELECT COUNT(*) FROM neo_memory_embeddings").fetchone()[0])
                        conn.execute("DELETE FROM neo_memory_embeddings")
                        conn.execute("UPDATE neo_memory_fragments SET embedding_status='queued' WHERE status='active' AND content!=''")
                engine = UnifiedMemoryRetrievalEngine(self.db_path)
                batch_limit = max(1, min(int(data.get("embedding_limit") or 5000), 5000))
                indexed_total = 0
                batches = 0
                last_result: dict[str, Any] = {}
                while batches < int(data.get("embedding_max_batches") or 100):
                    callbacks.check("Cancelled during embedding projection rebuild.")
                    last_result = engine.index_embeddings({
                        "force": False,
                        "limit": batch_limit,
                        "allow_fallback": bool(data.get("allow_embedding_fallback", True)),
                    })
                    batches += 1
                    indexed_now = int(last_result.get("indexed_count") or 0)
                    indexed_total += indexed_now
                    if indexed_now < batch_limit or last_result.get("status") == "no_pending_fragments":
                        break
                embedding_result = {
                    "ok": True,
                    "status": "indexed" if indexed_total else str(last_result.get("status") or "no_pending_fragments"),
                    "reset_embedding_rows": reset_count,
                    "indexed_count": indexed_total,
                    "batches": batches,
                    "last_embedding": last_result.get("embedding") or {},
                    "policy": "neo_memory_embeddings is a disposable search projection. Authority fragments/native stores were not deleted.",
                }
                record({"step": "embeddings", "ok": True, "result": embedding_result})
            except Exception as exc:
                report["warnings"].append(f"Embedding reindex failed: {exc}"[:1000])
                record({"step": "embeddings", "ok": False, "error": str(exc)[:1000]})

        callbacks.check("Cancelled before NKB-12 validation.")
        callbacks.emit("validation", 94, "Validating migration invariants and final counts.")
        after = self._memory_inventory()
        report.update({
            "status": "completed_with_warnings" if report["warnings"] else "completed",
            "finished_at": _now(),
            "after": {"memory": after, "native_knowledge": native_after},
            "message": "NKB-12 migration/reindex completed without rewriting native authority.",
        })
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        callbacks.emit("completed", 100, report["message"])
        return {"ok": True, "schema_id": MIGRATION_SCHEMA_ID, "status": report["status"], "message": report["message"], "migration_id": migration_id, "report_path": str(report_path), "report": report}
