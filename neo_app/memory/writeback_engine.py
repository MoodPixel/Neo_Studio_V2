from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .unified_schema import ensure_unified_memory_schema, unified_memory_schema_status

WRITEBACK_SCHEMA_ID = "neo.memory.writeback.phase_m11.v1"
WRITEBACK_VERSION = "memory-writeback-evolution.v1"

_LOW_RISK_TYPES = {
    "assistant_interaction_candidate",
    "workflow_preference_candidate",
    "successful_setting_candidate",
    "project_pattern_candidate",
    "turn_summary",
    "scene_event_candidate",
    "unresolved_thread_candidate",
}
_REVIEW_TYPES = {
    "canon_change",
    "canon_fact_change",
    "relationship_change",
    "relationship_state_change",
    "character_state_candidate",
    "character_knowledge_change",
    "character_secret_reveal",
    "user_preference_change",
    "cross_project_memory",
    "player_character_action",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except Exception:
        return fallback


def _hash(value: str, length: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:length]


def _clean(value: Any, limit: int = 900) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


def _safe_float(value: Any, default: float = 0.75) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(0.0, min(1.0, parsed))


def _risk_for_type(memory_type: str, payload: dict[str, Any]) -> str:
    memory_type = str(memory_type or "").strip()
    review = set(_REVIEW_TYPES)
    review.update(str(item) for item in payload.get("requires_review_for") or [] if item)
    if memory_type in review:
        return "review_required"
    if memory_type in _LOW_RISK_TYPES:
        return "auto_allowed"
    if any(key in memory_type for key in ("canon", "relationship", "secret", "preference", "cross_project")):
        return "review_required"
    return "review_recommended"


def _status_for_risk(risk: str, auto_apply: bool) -> str:
    if risk == "auto_allowed" and auto_apply:
        return "approved"
    return "pending_review" if risk != "auto_allowed" else "queued"


class MemoryWritebackEngine:
    """Phase M11 memory writeback + evolution engine.

    M11 converts Control Center writeback plans and direct surface events into
    reviewable memory candidates, then applies low-risk candidates into the
    unified SQLite memory tables. It does not train models and it does not make
    high-impact canon/user/relationship changes without an explicit review step.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        ensure_unified_memory_schema(conn)
        self.ensure_schema(conn)
        return conn

    def ensure_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS neo_memory_writebacks (
                writeback_id TEXT PRIMARY KEY,
                source_trace_id TEXT,
                source_type TEXT NOT NULL DEFAULT 'control_center',
                source_id TEXT,
                surface TEXT NOT NULL DEFAULT 'global',
                project_id TEXT,
                scope_id TEXT,
                memory_type TEXT NOT NULL DEFAULT 'assistant_interaction_candidate',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                risk_level TEXT NOT NULL DEFAULT 'review_recommended',
                status TEXT NOT NULL DEFAULT 'queued',
                decision TEXT NOT NULL DEFAULT '',
                applied_event_id TEXT,
                applied_fragment_id TEXT,
                applied_fact_id TEXT,
                confidence REAL NOT NULL DEFAULT 0.75,
                importance TEXT NOT NULL DEFAULT 'normal',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_at TEXT,
                applied_at TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                content_hash TEXT NOT NULL DEFAULT '',
                UNIQUE(source_trace_id, source_id, memory_type, content_hash)
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_writebacks_status ON neo_memory_writebacks(status, risk_level, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_writebacks_scope ON neo_memory_writebacks(surface, project_id, scope_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_writebacks_trace ON neo_memory_writebacks(source_trace_id)")

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            schema = unified_memory_schema_status(conn)
            rows = conn.execute(
                "SELECT status, risk_level, COUNT(*) AS count FROM neo_memory_writebacks GROUP BY status, risk_level ORDER BY status, risk_level"
            ).fetchall()
            recent = conn.execute(
                """
                SELECT writeback_id, surface, project_id, scope_id, memory_type, title, risk_level, status, created_at, applied_event_id, applied_fragment_id
                FROM neo_memory_writebacks
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()
            jobs = conn.execute(
                "SELECT status, COUNT(*) AS count FROM neo_memory_jobs WHERE job_type='memory_writeback' GROUP BY status"
            ).fetchall()
        return {
            "ok": True,
            "schema_id": WRITEBACK_SCHEMA_ID,
            "phase": "M11",
            "status": "ready",
            "version": WRITEBACK_VERSION,
            "counts_by_status_risk": [dict(row) for row in rows],
            "job_counts_by_status": {row["status"]: row["count"] for row in jobs},
            "recent_writebacks": [dict(row) for row in recent],
            "unified_schema": schema,
            "policy": "Low-risk workflow/turn candidates may be applied automatically; canon, relationship, character secret, user preference, and cross-project memories require review.",
            "endpoints": {
                "status": "/api/memory/writeback/status",
                "plan": "/api/memory/writeback/plan",
                "run": "/api/memory/writeback/run",
                "review": "/api/memory/writeback/review",
            },
        }

    def plan(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        trace_id = str(data.get("trace_id") or "").strip()
        surface = str(data.get("surface") or "").strip() or None
        project_id = str(data.get("project_id") or "").strip() or None
        scope_id = str(data.get("scope_id") or "").strip() or None
        memory_type = str(data.get("memory_type") or "").strip() or None
        limit = max(1, min(int(data.get("limit") or 25), 200))
        candidates: list[dict[str, Any]] = []

        if trace_id:
            candidates.extend(self._candidates_from_trace(trace_id))
        if data.get("items") and isinstance(data.get("items"), list):
            for idx, item in enumerate(data.get("items") or []):
                if not isinstance(item, dict):
                    continue
                candidates.append(self._candidate_from_payload(item, idx=idx, defaults=data))
        if data.get("content") or data.get("title"):
            candidates.append(self._candidate_from_payload(data, idx=0, defaults=data))

        # Optional backlog mode: latest traces with planned writebacks.
        if not candidates and data.get("from_recent_traces") is not False:
            with self._connect() as conn:
                where = ["writeback_plan_json IS NOT NULL", "writeback_plan_json != '{}'", "writeback_plan_json != ''"]
                params: list[Any] = []
                if surface:
                    where.append("surface=?")
                    params.append(surface)
                if project_id:
                    where.append("project_id=?")
                    params.append(project_id)
                if scope_id:
                    where.append("scope_id=?")
                    params.append(scope_id)
                rows = conn.execute(
                    f"""
                    SELECT trace_id FROM neo_control_center_traces
                    WHERE {' AND '.join(where)}
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (*params, limit),
                ).fetchall()
            for row in rows:
                candidates.extend(self._candidates_from_trace(row["trace_id"]))

        filtered: list[dict[str, Any]] = []
        for cand in candidates:
            if surface and cand.get("surface") != surface:
                continue
            if project_id and cand.get("project_id") != project_id:
                continue
            if scope_id and cand.get("scope_id") != scope_id:
                continue
            if memory_type and cand.get("memory_type") != memory_type:
                continue
            filtered.append(cand)
        return {
            "ok": True,
            "schema_id": WRITEBACK_SCHEMA_ID,
            "phase": "M11",
            "status": "planned",
            "candidate_count": len(filtered),
            "candidates": filtered[:limit],
            "policy": "Plan is review-aware. It only creates candidates; run/apply performs SQLite writes.",
        }

    def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        auto_apply = bool(data.get("auto_apply", True))
        dry_run = bool(data.get("dry_run", False))
        apply_reviewed_only = bool(data.get("apply_reviewed_only", False))
        plan = self.plan(data)
        candidates = plan.get("candidates") or []
        stamp = _now()
        job_id = f"writeback_job_{stamp.replace('-', '').replace(':', '').replace('.', '')}_{uuid4().hex[:8]}"
        inserted = 0
        applied = 0
        queued = 0
        review_required = 0
        errors: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO neo_memory_jobs (job_id, job_type, status, surface, project_id, scope_id, started_at, progress_json, result_json, error, created_at, updated_at)
                VALUES (?, 'memory_writeback', 'running', ?, ?, ?, ?, '{}', '{}', '', ?, ?)
                """,
                (job_id, str(data.get("surface") or "global"), data.get("project_id"), data.get("scope_id"), stamp, stamp, stamp),
            )
            for cand in candidates:
                try:
                    wb = self._insert_candidate(conn, cand, auto_apply=auto_apply and not apply_reviewed_only, dry_run=dry_run)
                    inserted += int(wb.get("inserted", 0))
                    queued += int(wb.get("queued", 0))
                    review_required += int(wb.get("review_required", 0))
                    if (wb.get("status") == "approved" and auto_apply and not dry_run) or (apply_reviewed_only and wb.get("existing_status") == "approved"):
                        applied_result = self._apply_writeback(conn, wb["writeback_id"], dry_run=dry_run)
                        if applied_result.get("applied"):
                            applied += 1
                        wb["apply_result"] = applied_result
                    items.append(wb)
                except Exception as exc:
                    errors.append({"candidate": cand.get("title") or cand.get("memory_type"), "error": str(exc)})
            status = "completed" if not errors else "completed_with_errors"
            conn.execute(
                """
                UPDATE neo_memory_jobs
                SET status=?, finished_at=?, updated_at=?, progress_json=?, result_json=?, error=?
                WHERE job_id=?
                """,
                (
                    status,
                    _now(),
                    _now(),
                    _json({"candidates": len(candidates), "inserted": inserted, "applied": applied, "review_required": review_required}),
                    _json({"items": items[:50], "errors": errors}),
                    "\n".join(err["error"] for err in errors[:5]),
                    job_id,
                ),
            )
        return {
            "ok": not errors,
            "schema_id": WRITEBACK_SCHEMA_ID,
            "phase": "M11",
            "status": status,
            "job_id": job_id,
            "dry_run": dry_run,
            "candidate_count": len(candidates),
            "inserted": inserted,
            "queued": queued,
            "review_required": review_required,
            "applied": applied,
            "errors": errors,
            "items": items[:50],
            "policy": "M11 writes low-risk memory automatically only when allowed; high-risk memory remains pending review.",
        }

    def review(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        ids = data.get("writeback_ids") or data.get("ids") or []
        if isinstance(ids, str):
            ids = [ids]
        ids = [str(item) for item in ids if str(item or "").strip()]
        decision = str(data.get("decision") or data.get("action") or "approve").strip().lower()
        apply_now = bool(data.get("apply", decision in {"approve_and_apply", "apply"}))
        if decision == "approve_and_apply":
            decision = "approve"
        if decision not in {"approve", "reject", "archive", "queue", "apply"}:
            return {"ok": False, "status": "invalid_decision", "error": f"Unsupported decision: {decision}"}
        stamp = _now()
        updated = 0
        applied = 0
        results: list[dict[str, Any]] = []
        with self._connect() as conn:
            for writeback_id in ids:
                status = {"approve": "approved", "reject": "rejected", "archive": "archived", "queue": "queued", "apply": "approved"}[decision]
                conn.execute(
                    "UPDATE neo_memory_writebacks SET status=?, decision=?, reviewed_at=?, updated_at=? WHERE writeback_id=?",
                    (status, decision, stamp, stamp, writeback_id),
                )
                updated += conn.total_changes
                if apply_now or decision == "apply":
                    apply_result = self._apply_writeback(conn, writeback_id, dry_run=False)
                    applied += int(bool(apply_result.get("applied")))
                    results.append(apply_result)
        return {"ok": True, "schema_id": WRITEBACK_SCHEMA_ID, "phase": "M11", "status": "reviewed", "updated": updated, "applied": applied, "results": results}

    def _candidates_from_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM neo_control_center_traces WHERE trace_id=?", (trace_id,)).fetchone()
        if not row:
            return []
        writeback_plan = _loads(row["writeback_plan_json"], {})
        selected_context = _loads(row["selected_context_json"], {})
        metadata = _loads(row["metadata_json"], {})
        planned = writeback_plan.get("planned_memory_types") or writeback_plan.get("low_risk_auto_write") or []
        if isinstance(planned, str):
            planned = [planned]
        if not planned:
            planned = ["assistant_interaction_candidate"]
        text = _clean(row["user_input"], 1200)
        context_items = selected_context.get("items") if isinstance(selected_context, dict) else []
        context_hint = ""
        if isinstance(context_items, list) and context_items:
            context_hint = " Related context: " + "; ".join(_clean((item or {}).get("title") or (item or {}).get("content_preview"), 120) for item in context_items[:3] if isinstance(item, dict))
        out: list[dict[str, Any]] = []
        for idx, memory_type in enumerate(planned):
            title = str(memory_type).replace("_", " ").title()
            content = text if text else title
            if context_hint and idx == 0:
                content = _clean(content + context_hint, 1500)
            payload = {
                "trace_id": row["trace_id"],
                "intent": row["intent"],
                "writeback_plan": writeback_plan,
                "selected_context_refs": [item.get("fragment_id") for item in context_items[:8] if isinstance(item, dict) and item.get("fragment_id")],
                "trace_metadata": metadata,
            }
            out.append({
                "source_trace_id": row["trace_id"],
                "source_type": "control_center_trace",
                "source_id": f"{row['trace_id']}:{idx}",
                "surface": row["surface"] or "global",
                "project_id": row["project_id"],
                "scope_id": row["scope_id"],
                "memory_type": str(memory_type),
                "title": title,
                "content": content,
                "payload": payload,
                "confidence": 0.72,
                "importance": "normal",
                "requires_review_for": writeback_plan.get("requires_review_for") or writeback_plan.get("review_required") or [],
            })
        return out

    def _candidate_from_payload(self, item: dict[str, Any], *, idx: int, defaults: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_trace_id": item.get("source_trace_id") or defaults.get("trace_id") or defaults.get("source_trace_id"),
            "source_type": item.get("source_type") or defaults.get("source_type") or "manual_writeback",
            "source_id": item.get("source_id") or defaults.get("source_id") or f"manual:{idx}",
            "surface": item.get("surface") or defaults.get("surface") or "global",
            "project_id": item.get("project_id") or defaults.get("project_id"),
            "scope_id": item.get("scope_id") or defaults.get("scope_id"),
            "memory_type": item.get("memory_type") or defaults.get("memory_type") or "assistant_interaction_candidate",
            "title": item.get("title") or defaults.get("title") or "Memory writeback candidate",
            "content": item.get("content") or item.get("summary") or defaults.get("content") or defaults.get("summary") or "",
            "payload": item.get("payload") if isinstance(item.get("payload"), dict) else {"raw": item},
            "confidence": _safe_float(item.get("confidence", defaults.get("confidence", 0.75))),
            "importance": item.get("importance") or defaults.get("importance") or "normal",
            "requires_review_for": item.get("requires_review_for") or defaults.get("requires_review_for") or [],
        }

    def _insert_candidate(self, conn: sqlite3.Connection, cand: dict[str, Any], *, auto_apply: bool, dry_run: bool) -> dict[str, Any]:
        content = _clean(cand.get("content"), 2200)
        title = _clean(cand.get("title") or cand.get("memory_type"), 180)
        payload = cand.get("payload") if isinstance(cand.get("payload"), dict) else {}
        risk = _risk_for_type(str(cand.get("memory_type") or ""), {**payload, "requires_review_for": cand.get("requires_review_for") or []})
        status = _status_for_risk(risk, auto_apply=auto_apply)
        stamp = _now()
        content_hash = _hash("|".join([str(cand.get("source_trace_id") or ""), str(cand.get("source_id") or ""), str(cand.get("memory_type") or ""), content]))
        writeback_id = "wb_" + content_hash
        existing = conn.execute("SELECT status FROM neo_memory_writebacks WHERE writeback_id=?", (writeback_id,)).fetchone()
        if not dry_run:
            conn.execute(
                """
                INSERT OR IGNORE INTO neo_memory_writebacks (
                    writeback_id, source_trace_id, source_type, source_id, surface, project_id, scope_id, memory_type,
                    title, content, payload_json, risk_level, status, confidence, importance, created_at, updated_at, metadata_json, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    writeback_id,
                    cand.get("source_trace_id"),
                    cand.get("source_type") or "control_center",
                    cand.get("source_id"),
                    cand.get("surface") or "global",
                    cand.get("project_id"),
                    cand.get("scope_id"),
                    cand.get("memory_type") or "assistant_interaction_candidate",
                    title,
                    content,
                    _json(payload),
                    risk,
                    status,
                    _safe_float(cand.get("confidence")),
                    cand.get("importance") or "normal",
                    stamp,
                    stamp,
                    _json({"phase": "M11", "auto_apply_requested": auto_apply, "dry_run": dry_run}),
                    content_hash,
                ),
            )
        return {
            "writeback_id": writeback_id,
            "status": status,
            "existing_status": existing["status"] if existing else None,
            "risk_level": risk,
            "memory_type": cand.get("memory_type"),
            "title": title,
            "surface": cand.get("surface") or "global",
            "project_id": cand.get("project_id"),
            "scope_id": cand.get("scope_id"),
            "inserted": 0 if existing else 1,
            "queued": 1 if status in {"queued", "pending_review"} else 0,
            "review_required": 1 if risk != "auto_allowed" else 0,
        }

    def _apply_writeback(self, conn: sqlite3.Connection, writeback_id: str, *, dry_run: bool) -> dict[str, Any]:
        row = conn.execute("SELECT * FROM neo_memory_writebacks WHERE writeback_id=?", (writeback_id,)).fetchone()
        if not row:
            return {"ok": False, "writeback_id": writeback_id, "applied": False, "error": "writeback_not_found"}
        if row["status"] not in {"approved", "queued"}:
            return {"ok": False, "writeback_id": writeback_id, "applied": False, "error": f"status_not_applyable:{row['status']}"}
        stamp = _now()
        payload = _loads(row["payload_json"], {})
        event_id = "ev_" + _hash(f"{writeback_id}:event")
        fragment_id = "frag_" + _hash(f"{writeback_id}:fragment")
        fact_id = None
        if not dry_run:
            conn.execute(
                """
                INSERT OR REPLACE INTO neo_memory_events (
                    memory_event_id, source_event_id, surface, project_id, scope_id, source_type, source_id, event_type,
                    title, summary, payload_json, metadata_json, importance, confidence, trust_level, retention_state,
                    created_at, updated_at, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    event_id,
                    row["source_id"],
                    row["surface"],
                    row["project_id"],
                    row["scope_id"],
                    row["source_type"],
                    row["source_trace_id"] or row["source_id"],
                    f"writeback.{row['memory_type']}",
                    row["title"],
                    _clean(row["content"], 700),
                    row["payload_json"],
                    _json({"writeback_id": writeback_id, "phase": "M11", "risk_level": row["risk_level"]}),
                    row["importance"],
                    row["confidence"],
                    "confirmed" if row["risk_level"] == "auto_allowed" else "reviewed",
                    stamp,
                    stamp,
                    row["content_hash"],
                ),
            )
            conn.execute(
                """
                INSERT OR REPLACE INTO neo_memory_fragments (
                    fragment_id, surface, project_id, scope_id, source_type, source_id, memory_type, title, content, summary,
                    token_estimate, priority, confidence, trust_level, status, metadata_json, created_at, updated_at, content_hash, embedding_status
                ) VALUES (?, ?, ?, ?, 'memory_writeback', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, 'queued')
                """,
                (
                    fragment_id,
                    row["surface"],
                    row["project_id"],
                    row["scope_id"],
                    writeback_id,
                    row["memory_type"],
                    row["title"],
                    row["content"],
                    _clean(row["content"], 700),
                    max(1, len(str(row["content"] or "").split())),
                    0.62 if row["risk_level"] == "auto_allowed" else 0.5,
                    row["confidence"],
                    "confirmed" if row["risk_level"] == "auto_allowed" else "reviewed",
                    _json({"writeback_id": writeback_id, "source_trace_id": row["source_trace_id"], "payload": payload}),
                    stamp,
                    stamp,
                    row["content_hash"],
                ),
            )
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO neo_memory_fragments_fts (fragment_id, surface, project_id, scope_id, title, content, summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (fragment_id, row["surface"], row["project_id"], row["scope_id"], row["title"], row["content"], _clean(row["content"], 700)),
                )
            except sqlite3.OperationalError:
                pass
            if row["memory_type"] in {"workflow_preference_candidate", "successful_setting_candidate", "project_pattern_candidate", "scene_event_candidate", "unresolved_thread_candidate"}:
                fact_id = "fact_" + _hash(f"{writeback_id}:fact")
                conn.execute(
                    """
                    INSERT OR REPLACE INTO neo_memory_facts (
                        fact_id, surface, project_id, scope_id, subject_id, predicate, object_value, object_id, fact_type,
                        statement, source_event_id, confidence, trust_level, status, metadata_json, created_at, updated_at, content_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                    """,
                    (
                        fact_id,
                        row["surface"],
                        row["project_id"],
                        row["scope_id"],
                        row["scope_id"] or row["project_id"] or row["surface"],
                        row["memory_type"],
                        row["content"],
                        "writeback_fact",
                        row["content"],
                        event_id,
                        row["confidence"],
                        "confirmed" if row["risk_level"] == "auto_allowed" else "reviewed",
                        _json({"writeback_id": writeback_id, "phase": "M11"}),
                        stamp,
                        stamp,
                        row["content_hash"],
                    ),
                )
            conn.execute(
                """
                UPDATE neo_memory_writebacks
                SET status='applied', applied_event_id=?, applied_fragment_id=?, applied_fact_id=?, applied_at=?, updated_at=?
                WHERE writeback_id=?
                """,
                (event_id, fragment_id, fact_id, stamp, stamp, writeback_id),
            )
        return {"ok": True, "writeback_id": writeback_id, "applied": not dry_run, "event_id": event_id, "fragment_id": fragment_id, "fact_id": fact_id, "dry_run": dry_run}
