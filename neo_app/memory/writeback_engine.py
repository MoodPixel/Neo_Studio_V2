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
from .durable_candidates import assistant_turn_candidates, surface_event_candidates

WRITEBACK_SCHEMA_ID = "neo.memory.writeback.phase9.v2"
WRITEBACK_VERSION = "durable-memory-writeback.v2"

_LOW_RISK_TYPES = {
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
    "user_memory_directive",
    "project_decision_candidate",
    "high_impact_project_fact",
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


def _status_for_risk(risk: str, auto_apply: bool, *, support_count: int = 1, support_threshold: int = 1, contradiction: bool = False) -> str:
    if contradiction:
        return "pending_review"
    if risk == "auto_allowed":
        if support_count < max(1, support_threshold):
            return "observed"
        return "approved" if auto_apply else "queued"
    return "pending_review"


def _semantic_hash(memory_type: str, content: str) -> str:
    normalized_content = re.sub(r"\s+", " ", str(content or "")).strip().lower()
    return _hash(f"{memory_type}|{normalized_content}", 32)


def _evidence_key(item: dict[str, Any]) -> str:
    return "|".join([str(item.get("source_type") or ""), str(item.get("source_id") or ""), str(item.get("trace_id") or "")])


class MemoryWritebackEngine:
    """Phase 9 durable-memory writeback evolution built on the M11 engine.

    The historical M11 engine remains the storage/lifecycle foundation. Phase 9
    separates searchable history from durable promotion, requires independent
    support for low-risk patterns, and review-gates preferences, project/canon
    changes, contradictions, and cross-project claims. It never trains models.
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
                memory_type TEXT NOT NULL DEFAULT 'durable_memory_candidate',
                candidate_class TEXT NOT NULL DEFAULT '',
                durable_key TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                risk_level TEXT NOT NULL DEFAULT 'review_recommended',
                status TEXT NOT NULL DEFAULT 'queued',
                decision TEXT NOT NULL DEFAULT '',
                decision_reason TEXT NOT NULL DEFAULT '',
                support_count INTEGER NOT NULL DEFAULT 1,
                support_threshold INTEGER NOT NULL DEFAULT 1,
                evidence_json TEXT NOT NULL DEFAULT '[]',
                semantic_hash TEXT NOT NULL DEFAULT '',
                contradiction_state TEXT NOT NULL DEFAULT '',
                supersedes_writeback_id TEXT,
                superseded_by_writeback_id TEXT,
                last_supported_at TEXT,
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
        # Phase 9 is additive: older M11 databases are upgraded in place without
        # rewriting or deleting existing writeback rows.
        columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(neo_memory_writebacks)").fetchall()}
        additions = {
            "candidate_class": "TEXT NOT NULL DEFAULT ''",
            "durable_key": "TEXT NOT NULL DEFAULT ''",
            "decision_reason": "TEXT NOT NULL DEFAULT ''",
            "support_count": "INTEGER NOT NULL DEFAULT 1",
            "support_threshold": "INTEGER NOT NULL DEFAULT 1",
            "evidence_json": "TEXT NOT NULL DEFAULT '[]'",
            "semantic_hash": "TEXT NOT NULL DEFAULT ''",
            "contradiction_state": "TEXT NOT NULL DEFAULT ''",
            "supersedes_writeback_id": "TEXT",
            "superseded_by_writeback_id": "TEXT",
            "last_supported_at": "TEXT",
        }
        for column, ddl in additions.items():
            if column not in columns:
                conn.execute(f"ALTER TABLE neo_memory_writebacks ADD COLUMN {column} {ddl}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_writebacks_durable_key ON neo_memory_writebacks(surface, project_id, scope_id, memory_type, durable_key, status)")

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            schema = unified_memory_schema_status(conn)
            rows = conn.execute(
                "SELECT status, risk_level, COUNT(*) AS count FROM neo_memory_writebacks GROUP BY status, risk_level ORDER BY status, risk_level"
            ).fetchall()
            classes = conn.execute(
                "SELECT candidate_class, memory_type, status, COUNT(*) AS count FROM neo_memory_writebacks GROUP BY candidate_class, memory_type, status ORDER BY count DESC"
            ).fetchall()
            recent = conn.execute(
                """
                SELECT writeback_id, surface, project_id, scope_id, memory_type, candidate_class, durable_key, title,
                       risk_level, status, decision_reason, support_count, support_threshold, contradiction_state,
                       supersedes_writeback_id, superseded_by_writeback_id, created_at, last_supported_at, applied_event_id, applied_fragment_id
                FROM neo_memory_writebacks
                ORDER BY COALESCE(last_supported_at, created_at) DESC
                LIMIT 16
                """
            ).fetchall()
            pending = conn.execute(
                """
                SELECT writeback_id, surface, project_id, scope_id, memory_type, candidate_class, durable_key, title, content,
                       risk_level, status, decision_reason, support_count, support_threshold, contradiction_state,
                       supersedes_writeback_id, created_at, last_supported_at
                FROM neo_memory_writebacks
                WHERE status IN ('pending_review', 'approved', 'queued')
                ORDER BY CASE status WHEN 'pending_review' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, COALESCE(last_supported_at, created_at) DESC
                LIMIT 24
                """
            ).fetchall()
            jobs = conn.execute(
                "SELECT status, COUNT(*) AS count FROM neo_memory_jobs WHERE job_type='memory_writeback' GROUP BY status"
            ).fetchall()
        return {
            "ok": True,
            "schema_id": WRITEBACK_SCHEMA_ID,
            "phase": "9",
            "legacy_phase": "M11",
            "status": "ready",
            "version": WRITEBACK_VERSION,
            "counts_by_status_risk": [dict(row) for row in rows],
            "counts_by_candidate_class": [dict(row) for row in classes],
            "job_counts_by_status": {row["status"]: row["count"] for row in jobs},
            "recent_writebacks": [dict(row) for row in recent],
            "pending_review": [dict(row) for row in pending],
            "unified_schema": schema,
            "policy": {
                "searchable_history_is_not_automatically_durable": True,
                "low_risk_requires_support": True,
                "successful_setting_support_threshold": 2,
                "workflow_preference_support_threshold": 2,
                "preference_changes_require_review": True,
                "project_decisions_require_review": True,
                "cross_project_requires_review": True,
                "roleplay_canon_relationship_secret_requires_review": True,
                "contradictions_never_auto_supersede": True,
                "supersession_happens_on_apply": True,
            },
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
            "phase": "9",
            "legacy_phase": "M11",
            "status": "planned",
            "candidate_count": len(filtered),
            "candidates": filtered[:limit],
            "policy": "Plan is review-aware. Searchable history is not durable by default; supported low-risk candidates may auto-promote, while preferences, project decisions, contradictions, cross-project claims, and canon-sensitive changes require review.",
        }

    def capture_assistant_turn(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        candidates = assistant_turn_candidates(data)
        if not candidates:
            return {
                "ok": True,
                "schema_id": WRITEBACK_SCHEMA_ID,
                "phase": "9",
                "status": "skipped",
                "reason": "no_durable_candidate",
                "candidate_count": 0,
                "items": [],
            }
        surface = str(data.get("surface") or data.get("surface_id") or "assistant")
        scope_id = str(data.get("scope_id") or "general")
        storage_project = data.get("project_id") or data.get("delivery_project_id")
        if not storage_project:
            storage_project = f"assistant:{scope_id}" if surface == "assistant" else (surface if surface not in {"global", "roleplay"} else None)
        return self.run({
            "items": candidates,
            "surface": surface,
            "project_id": storage_project,
            "scope_id": scope_id,
            "auto_apply": True,
            "from_recent_traces": False,
            "source_type": "assistant_turn",
        })

    def capture_surface_event(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        candidates = surface_event_candidates(data)
        if not candidates:
            return {
                "ok": True,
                "schema_id": WRITEBACK_SCHEMA_ID,
                "phase": "9",
                "status": "skipped",
                "reason": "no_durable_surface_candidate",
                "candidate_count": 0,
                "items": [],
            }
        surface = str(data.get("surface") or data.get("surface_id") or "global")
        storage_project = data.get("project_id") or (surface if surface not in {"global", "roleplay", "assistant"} else None)
        return self.run({
            "items": candidates,
            "surface": surface,
            "project_id": storage_project,
            "scope_id": data.get("scope_id"),
            "auto_apply": True,
            "from_recent_traces": False,
            "source_type": "surface_success",
        })

    def run(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        auto_apply = bool(data.get("auto_apply", True))
        dry_run = bool(data.get("dry_run", False))
        apply_reviewed_only = bool(data.get("apply_reviewed_only", False))
        plan = self.plan(data)
        candidates = plan.get("candidates") or []
        if not candidates:
            return {
                "ok": True,
                "schema_id": WRITEBACK_SCHEMA_ID,
                "phase": "9",
                "legacy_phase": "M11",
                "status": "skipped",
                "reason": "no_candidates",
                "candidate_count": 0,
                "inserted": 0,
                "observed": 0,
                "queued": 0,
                "review_required": 0,
                "applied": 0,
                "errors": [],
                "items": [],
            }
        managed_job = bool(data.get("managed_job") or data.get("managed_job_id"))
        progress_callback = data.get("progress_callback") if callable(data.get("progress_callback")) else None
        cancel_callback = data.get("cancel_callback") if callable(data.get("cancel_callback")) else None
        stamp = _now()
        job_id = str(data.get("managed_job_id") or "") or f"writeback_job_{stamp.replace('-', '').replace(':', '').replace('.', '')}_{uuid4().hex[:8]}"
        inserted = 0
        applied = 0
        observed = 0
        queued = 0
        review_required = 0
        errors: list[dict[str, Any]] = []
        items: list[dict[str, Any]] = []
        if progress_callback:
            progress_callback(phase="writeback_candidates", current=0, total=len(candidates), percent=10, message=f"Evaluating {len(candidates)} durable-memory candidate(s).")
        if cancel_callback:
            cancel_callback("Cancelled before durable-memory writeback.")
        with self._connect() as conn:
            if not managed_job:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO neo_memory_jobs (job_id, job_type, status, surface, project_id, scope_id, started_at, progress_json, result_json, error, created_at, updated_at)
                    VALUES (?, 'memory_writeback', 'running', ?, ?, ?, ?, '{}', '{}', '', ?, ?)
                    """,
                    (job_id, str(data.get("surface") or "global"), data.get("project_id"), data.get("scope_id"), stamp, stamp, stamp),
                )
            for candidate_index, cand in enumerate(candidates, start=1):
                if cancel_callback:
                    cancel_callback("Cancelled while evaluating durable-memory candidates.")
                try:
                    wb = self._insert_candidate(conn, cand, auto_apply=auto_apply and not apply_reviewed_only, dry_run=dry_run)
                    inserted += int(wb.get("inserted", 0))
                    observed += int(wb.get("observed", 0))
                    queued += int(wb.get("queued", 0))
                    review_required += int(wb.get("review_required", 0))
                    should_apply = wb.get("status") == "approved" and auto_apply and not dry_run
                    if apply_reviewed_only and wb.get("existing_status") == "approved":
                        should_apply = True
                    if should_apply:
                        applied_result = self._apply_writeback(conn, wb["writeback_id"], dry_run=dry_run)
                        if applied_result.get("applied"):
                            applied += 1
                            wb["status"] = "applied"
                        wb["apply_result"] = applied_result
                    items.append(wb)
                    if progress_callback:
                        progress_callback(phase="writeback_candidates", current=candidate_index, total=len(candidates), percent=10 + round((candidate_index / max(1, len(candidates))) * 80), message=f"Evaluated {candidate_index}/{len(candidates)} durable-memory candidate(s).")
                except Exception as exc:
                    errors.append({"candidate": cand.get("title") or cand.get("memory_type"), "error": str(exc)})
            status = "completed" if not errors else "completed_with_errors"
            if not managed_job:
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
                        _json({"candidates": len(candidates), "inserted": inserted, "observed": observed, "applied": applied, "review_required": review_required}),
                        _json({"items": items[:50], "errors": errors}),
                        "\n".join(err["error"] for err in errors[:5]),
                        job_id,
                    ),
                )
            if progress_callback:
                progress_callback(phase="writeback_finalizing", current=len(candidates), total=len(candidates), percent=95, message="Finalizing durable-memory writeback.")
        return {
            "ok": not errors,
            "schema_id": WRITEBACK_SCHEMA_ID,
            "phase": "9",
            "legacy_phase": "M11",
            "status": status,
            "job_id": job_id,
            "dry_run": dry_run,
            "candidate_count": len(candidates),
            "inserted": inserted,
            "observed": observed,
            "queued": queued,
            "review_required": review_required,
            "applied": applied,
            "errors": errors,
            "items": items[:50],
            "policy": "Searchable history stays history unless a durable candidate passes support/risk gates; contradictions never auto-supersede reviewed memory.",
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
        note = _clean(data.get("note") or data.get("reason"), 1200)
        with self._connect() as conn:
            for writeback_id in ids:
                status = {"approve": "approved", "reject": "rejected", "archive": "archived", "queue": "queued", "apply": "approved"}[decision]
                contradiction_state = "rejected" if decision in {"reject", "archive"} else "reviewed"
                cursor = conn.execute(
                    """
                    UPDATE neo_memory_writebacks
                    SET status=?, decision=?, reviewed_at=?, updated_at=?,
                        decision_reason=CASE WHEN ? != '' THEN ? ELSE decision_reason END,
                        contradiction_state=CASE WHEN contradiction_state != '' THEN ? ELSE contradiction_state END
                    WHERE writeback_id=?
                    """,
                    (status, decision, stamp, stamp, note, note, contradiction_state, writeback_id),
                )
                updated += int(cursor.rowcount or 0)
                if apply_now or decision == "apply":
                    apply_result = self._apply_writeback(conn, writeback_id, dry_run=False)
                    applied += int(bool(apply_result.get("applied")))
                    results.append(apply_result)
        return {
            "ok": True,
            "schema_id": WRITEBACK_SCHEMA_ID,
            "phase": "9",
            "legacy_phase": "M11",
            "status": "reviewed",
            "updated": updated,
            "applied": applied,
            "results": results,
        }

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
        # Phase 9 deliberately removes the old generic assistant-interaction
        # fallback. Assistant turns are classified after successful generation.
        if not planned:
            return []
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
                "candidate_class": "legacy_control_center_plan",
                "title": title,
                "content": content,
                "payload": payload,
                "confidence": 0.72,
                "importance": "normal",
                "support_threshold": 1,
                "durable_key": f"legacy_trace:{row['trace_id']}:{memory_type}:{idx}",
                "decision_reason": "Compatibility candidate from an explicit legacy Control Center writeback lane.",
                "requires_review_for": writeback_plan.get("requires_review_for") or writeback_plan.get("review_required") or [],
            })
        return out

    def _candidate_from_payload(self, item: dict[str, Any], *, idx: int, defaults: dict[str, Any]) -> dict[str, Any]:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {"raw": item}
        evidence = item.get("evidence") if isinstance(item.get("evidence"), list) else defaults.get("evidence") if isinstance(defaults.get("evidence"), list) else []
        return {
            "source_trace_id": item.get("source_trace_id") or defaults.get("trace_id") or defaults.get("source_trace_id"),
            "source_type": item.get("source_type") or defaults.get("source_type") or "manual_writeback",
            "source_id": item.get("source_id") or defaults.get("source_id") or f"manual:{idx}",
            "surface": item.get("surface") or defaults.get("surface") or "global",
            "project_id": item.get("project_id") or defaults.get("project_id"),
            "scope_id": item.get("scope_id") or defaults.get("scope_id"),
            "memory_type": item.get("memory_type") or defaults.get("memory_type") or "memory_candidate",
            "candidate_class": item.get("candidate_class") or defaults.get("candidate_class") or "manual_candidate",
            "title": item.get("title") or defaults.get("title") or "Memory writeback candidate",
            "content": item.get("content") or item.get("summary") or defaults.get("content") or defaults.get("summary") or "",
            "payload": payload,
            "confidence": _safe_float(item.get("confidence", defaults.get("confidence", 0.75))),
            "importance": item.get("importance") or defaults.get("importance") or "normal",
            "requires_review_for": item.get("requires_review_for") or defaults.get("requires_review_for") or [],
            "durable_key": item.get("durable_key") or defaults.get("durable_key") or "",
            "support_threshold": max(1, int(item.get("support_threshold") or defaults.get("support_threshold") or 1)),
            "decision_reason": item.get("decision_reason") or defaults.get("decision_reason") or "",
            "evidence": evidence,
        }

    def _insert_candidate(self, conn: sqlite3.Connection, cand: dict[str, Any], *, auto_apply: bool, dry_run: bool) -> dict[str, Any]:
        content = _clean(cand.get("content"), 2200)
        title = _clean(cand.get("title") or cand.get("memory_type"), 180)
        payload = cand.get("payload") if isinstance(cand.get("payload"), dict) else {}
        memory_type = str(cand.get("memory_type") or "memory_candidate")
        candidate_class = _clean(cand.get("candidate_class") or "candidate", 160)
        risk = _risk_for_type(memory_type, {**payload, "requires_review_for": cand.get("requires_review_for") or []})
        support_threshold = max(1, int(cand.get("support_threshold") or 1))
        stamp = _now()
        semantic_hash = _semantic_hash(memory_type, content)
        durable_key = _clean(cand.get("durable_key"), 700) or f"{memory_type}:{cand.get('scope_id') or cand.get('project_id') or cand.get('surface') or 'global'}:{semantic_hash[:16]}"
        content_hash = _hash("|".join([str(cand.get("source_trace_id") or ""), str(cand.get("source_id") or ""), memory_type, content]))
        writeback_id = "wb_" + content_hash
        evidence = [dict(item) for item in cand.get("evidence") or [] if isinstance(item, dict)]
        source_evidence = {"source_type": cand.get("source_type") or "control_center", "source_id": cand.get("source_id") or "", "trace_id": cand.get("source_trace_id") or ""}
        if _evidence_key(source_evidence).strip("|"):
            evidence.append(source_evidence)
        dedup_evidence: dict[str, dict[str, Any]] = {}
        for item in evidence:
            dedup_evidence[_evidence_key(item) or _hash(_json(item), 12)] = item
        evidence = list(dedup_evidence.values())

        exact = conn.execute("SELECT * FROM neo_memory_writebacks WHERE writeback_id=?", (writeback_id,)).fetchone()
        if exact:
            return {
                "writeback_id": writeback_id,
                "status": exact["status"],
                "existing_status": exact["status"],
                "risk_level": exact["risk_level"],
                "memory_type": exact["memory_type"],
                "candidate_class": exact["candidate_class"],
                "title": exact["title"],
                "surface": exact["surface"],
                "project_id": exact["project_id"],
                "scope_id": exact["scope_id"],
                "durable_key": exact["durable_key"],
                "support_count": int(exact["support_count"] or 1),
                "support_threshold": int(exact["support_threshold"] or 1),
                "decision_reason": exact["decision_reason"],
                "contradiction_state": exact["contradiction_state"],
                "inserted": 0,
                "support_incremented": 0,
                "observed": 1 if exact["status"] == "observed" else 0,
                "queued": 1 if exact["status"] in {"queued", "pending_review"} else 0,
                "review_required": 1 if exact["risk_level"] != "auto_allowed" or exact["status"] == "pending_review" else 0,
            }

        prior = conn.execute(
            """
            SELECT * FROM neo_memory_writebacks
            WHERE surface=? AND COALESCE(project_id, '')=? AND COALESCE(scope_id, '')=? AND memory_type=? AND durable_key=?
              AND status NOT IN ('rejected', 'archived', 'superseded')
            ORDER BY COALESCE(last_supported_at, created_at) DESC
            LIMIT 1
            """,
            (cand.get("surface") or "global", str(cand.get("project_id") or ""), str(cand.get("scope_id") or ""), memory_type, durable_key),
        ).fetchone()

        if prior and str(prior["semantic_hash"] or _semantic_hash(memory_type, prior["content"])) == semantic_hash:
            existing_evidence = _loads(prior["evidence_json"], [])
            if not isinstance(existing_evidence, list):
                existing_evidence = []
            evidence_map = {_evidence_key(item): item for item in existing_evidence if isinstance(item, dict)}
            before = len(evidence_map)
            for item in evidence:
                evidence_map.setdefault(_evidence_key(item) or _hash(_json(item), 12), item)
            incremented = 1 if len(evidence_map) > before else 0
            support_count = int(prior["support_count"] or 1) + incremented
            status = str(prior["status"] or "observed")
            if status not in {"applied", "approved"}:
                status = _status_for_risk(risk, auto_apply, support_count=support_count, support_threshold=support_threshold)
            if not dry_run:
                conn.execute(
                    """
                    UPDATE neo_memory_writebacks
                    SET support_count=?, support_threshold=?, evidence_json=?, confidence=?, last_supported_at=?, updated_at=?,
                        status=?, decision_reason=CASE WHEN decision_reason='' THEN ? ELSE decision_reason END
                    WHERE writeback_id=?
                    """,
                    (
                        support_count,
                        max(int(prior["support_threshold"] or 1), support_threshold),
                        _json(list(evidence_map.values())),
                        max(float(prior["confidence"] or 0.0), _safe_float(cand.get("confidence"))),
                        stamp,
                        stamp,
                        status,
                        _clean(cand.get("decision_reason"), 1200),
                        prior["writeback_id"],
                    ),
                )
            return {
                "writeback_id": prior["writeback_id"],
                "status": status,
                "existing_status": prior["status"],
                "risk_level": prior["risk_level"],
                "memory_type": memory_type,
                "candidate_class": prior["candidate_class"] or candidate_class,
                "title": prior["title"],
                "surface": prior["surface"],
                "project_id": prior["project_id"],
                "scope_id": prior["scope_id"],
                "durable_key": durable_key,
                "support_count": support_count,
                "support_threshold": support_threshold,
                "decision_reason": prior["decision_reason"] or cand.get("decision_reason") or "",
                "contradiction_state": prior["contradiction_state"],
                "inserted": 0,
                "support_incremented": incremented,
                "observed": 1 if status == "observed" else 0,
                "queued": 1 if status in {"queued", "pending_review"} else 0,
                "review_required": 1 if risk != "auto_allowed" or status == "pending_review" else 0,
            }

        contradiction = bool(prior and str(prior["semantic_hash"] or "") != semantic_hash)
        status = _status_for_risk(risk, auto_apply, support_count=1, support_threshold=support_threshold, contradiction=contradiction)
        supersedes_writeback_id = prior["writeback_id"] if contradiction and prior else None
        if contradiction and not dry_run:
            conn.execute(
                "UPDATE neo_memory_writebacks SET contradiction_state='challenged', updated_at=? WHERE writeback_id=?",
                (stamp, prior["writeback_id"]),
            )
        if not dry_run:
            conn.execute(
                """
                INSERT INTO neo_memory_writebacks (
                    writeback_id, source_trace_id, source_type, source_id, surface, project_id, scope_id, memory_type,
                    candidate_class, durable_key, title, content, payload_json, risk_level, status, decision, decision_reason,
                    support_count, support_threshold, evidence_json, semantic_hash, contradiction_state, supersedes_writeback_id,
                    confidence, importance, created_at, updated_at, last_supported_at, metadata_json, content_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    writeback_id,
                    cand.get("source_trace_id"),
                    cand.get("source_type") or "control_center",
                    cand.get("source_id"),
                    cand.get("surface") or "global",
                    cand.get("project_id"),
                    cand.get("scope_id"),
                    memory_type,
                    candidate_class,
                    durable_key,
                    title,
                    content,
                    _json(payload),
                    risk,
                    status,
                    _clean(cand.get("decision_reason"), 1200),
                    support_threshold,
                    _json(evidence),
                    semantic_hash,
                    "conflicts_with_existing" if contradiction else "",
                    supersedes_writeback_id,
                    _safe_float(cand.get("confidence")),
                    cand.get("importance") or "normal",
                    stamp,
                    stamp,
                    stamp,
                    _json({"phase": "9", "legacy_phase": "M11", "auto_apply_requested": auto_apply, "dry_run": dry_run, "candidate_class": candidate_class}),
                    content_hash,
                ),
            )
        return {
            "writeback_id": writeback_id,
            "status": status,
            "existing_status": None,
            "risk_level": risk,
            "memory_type": memory_type,
            "candidate_class": candidate_class,
            "title": title,
            "surface": cand.get("surface") or "global",
            "project_id": cand.get("project_id"),
            "scope_id": cand.get("scope_id"),
            "durable_key": durable_key,
            "support_count": 1,
            "support_threshold": support_threshold,
            "decision_reason": cand.get("decision_reason") or "",
            "contradiction_state": "conflicts_with_existing" if contradiction else "",
            "supersedes_writeback_id": supersedes_writeback_id,
            "inserted": 1,
            "support_incremented": 0,
            "observed": 1 if status == "observed" else 0,
            "queued": 1 if status in {"queued", "pending_review"} else 0,
            "review_required": 1 if risk != "auto_allowed" or contradiction else 0,
        }

    def _supersede_previous(self, conn: sqlite3.Connection, row: sqlite3.Row, stamp: str) -> list[str]:
        durable_key = str(row["durable_key"] or "").strip()
        if not durable_key:
            return []
        previous = conn.execute(
            """
            SELECT writeback_id, applied_event_id, applied_fragment_id, applied_fact_id
            FROM neo_memory_writebacks
            WHERE writeback_id != ? AND surface=? AND COALESCE(project_id, '')=? AND COALESCE(scope_id, '')=?
              AND memory_type=? AND durable_key=? AND status='applied'
            ORDER BY applied_at DESC
            """,
            (row["writeback_id"], row["surface"], str(row["project_id"] or ""), str(row["scope_id"] or ""), row["memory_type"], durable_key),
        ).fetchall()
        superseded: list[str] = []
        for old in previous:
            old_id = old["writeback_id"]
            superseded.append(old_id)
            conn.execute(
                """
                UPDATE neo_memory_writebacks
                SET status='superseded', decision='superseded', contradiction_state='resolved_superseded',
                    superseded_by_writeback_id=?, updated_at=?
                WHERE writeback_id=?
                """,
                (row["writeback_id"], stamp, old_id),
            )
            if old["applied_fragment_id"]:
                conn.execute("UPDATE neo_memory_fragments SET status='superseded', updated_at=? WHERE fragment_id=?", (stamp, old["applied_fragment_id"]))
                try:
                    conn.execute("DELETE FROM neo_memory_fragments_fts WHERE fragment_id=?", (old["applied_fragment_id"],))
                except sqlite3.OperationalError:
                    pass
            if old["applied_fact_id"]:
                conn.execute("UPDATE neo_memory_facts SET status='superseded', updated_at=? WHERE fact_id=?", (stamp, old["applied_fact_id"]))
            if old["applied_event_id"]:
                conn.execute("UPDATE neo_memory_events SET retention_state='superseded', updated_at=? WHERE memory_event_id=?", (stamp, old["applied_event_id"]))
        return superseded

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
        superseded: list[str] = []
        if not dry_run:
            superseded = self._supersede_previous(conn, row, stamp)
            metadata = {
                "writeback_id": writeback_id,
                "phase": "9",
                "legacy_phase": "M11",
                "risk_level": row["risk_level"],
                "candidate_class": row["candidate_class"],
                "durable_key": row["durable_key"],
                "support_count": int(row["support_count"] or 1),
                "support_threshold": int(row["support_threshold"] or 1),
                "superseded_writebacks": superseded,
            }
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
                    _json(metadata),
                    row["importance"],
                    row["confidence"],
                    "confirmed" if row["risk_level"] == "auto_allowed" else "reviewed",
                    stamp,
                    stamp,
                    row["content_hash"],
                ),
            )
            priority = 0.7 if row["importance"] == "high" else 0.64 if row["risk_level"] == "auto_allowed" else 0.58
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
                    priority,
                    row["confidence"],
                    "confirmed" if row["risk_level"] == "auto_allowed" else "reviewed",
                    _json({**metadata, "source_trace_id": row["source_trace_id"], "payload": payload}),
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
            fact_types = {
                "workflow_preference_candidate", "successful_setting_candidate", "project_pattern_candidate",
                "scene_event_candidate", "unresolved_thread_candidate", "project_decision_candidate",
                "user_preference_change", "user_memory_directive",
            }
            if row["memory_type"] in fact_types:
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
                        "durable_writeback_fact",
                        row["content"],
                        event_id,
                        row["confidence"],
                        "confirmed" if row["risk_level"] == "auto_allowed" else "reviewed",
                        _json(metadata),
                        stamp,
                        stamp,
                        row["content_hash"],
                    ),
                )
            conn.execute(
                """
                UPDATE neo_memory_writebacks
                SET status='applied', applied_event_id=?, applied_fragment_id=?, applied_fact_id=?, applied_at=?, updated_at=?,
                    contradiction_state=CASE WHEN ? > 0 THEN 'resolved' ELSE contradiction_state END,
                    supersedes_writeback_id=COALESCE(supersedes_writeback_id, ?)
                WHERE writeback_id=?
                """,
                (event_id, fragment_id, fact_id, stamp, stamp, len(superseded), superseded[0] if superseded else None, writeback_id),
            )
        return {
            "ok": True,
            "writeback_id": writeback_id,
            "applied": not dry_run,
            "event_id": event_id,
            "fragment_id": fragment_id,
            "fact_id": fact_id,
            "superseded_writeback_ids": superseded,
            "dry_run": dry_run,
        }

