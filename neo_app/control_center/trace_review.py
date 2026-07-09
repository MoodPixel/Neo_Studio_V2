from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

M13_PHASE = "M13"
TRACE_REVIEW_SCHEMA_ID = "neo.control_center.trace_review.m13"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _loads(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _short(value: Any, limit: int = 360) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


class ControlCenterTraceReviewEngine:
    """M13 UI-facing review layer for Control Center traces.

    This layer is intentionally lightweight and additive. It does not alter
    retrieval, prompt contracts, backend generation, or memory writeback logic.
    It gives Admin a cockpit view of the latest trace, selected context,
    safety gate result, prompt contract, writeback candidates, and related
    retrieval/safety/writeback records so we stop debugging blind.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS neo_control_center_trace_reviews (
                    review_id TEXT PRIMARY KEY,
                    trace_id TEXT NOT NULL,
                    decision TEXT NOT NULL DEFAULT 'reviewed',
                    note TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL DEFAULT 'local_admin',
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_neo_control_center_trace_reviews_trace ON neo_control_center_trace_reviews(trace_id, created_at)"
            )

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        try:
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
        except sqlite3.Error:
            return False

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            trace_count = self._count(conn, "neo_control_center_traces")
            review_count = self._count(conn, "neo_control_center_trace_reviews")
            writeback_count = self._count(conn, "neo_memory_writebacks")
            violation_count = self._count(conn, "neo_memory_safety_violations", "WHERE status='open'")
        return {
            "ok": True,
            "schema_id": TRACE_REVIEW_SCHEMA_ID,
            "phase": M13_PHASE,
            "status": "ready",
            "trace_count": trace_count,
            "review_count": review_count,
            "open_safety_violations": violation_count,
            "writeback_count": writeback_count,
            "panels": [
                "trace_queue",
                "trace_detail",
                "selected_context",
                "retrieval_diagnostics",
                "safety_guard",
                "prompt_contract",
                "writeback_candidates",
                "review_decision",
            ],
            "policy": "M13 is a cockpit/review layer. It shows and annotates traces; it does not mutate memory content or bypass safety.",
        }

    def _count(self, conn: sqlite3.Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
        if not self._table_exists(conn, table):
            return 0
        try:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} {where}", params).fetchone()
            return int(row["count"] if row else 0)
        except sqlite3.Error:
            return 0

    def dashboard(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        limit = max(1, min(int(payload.get("limit") or 16), 80))
        controller = str(payload.get("controller") or "").strip() or None
        surface = str(payload.get("surface") or "").strip() or None
        trace_id = str(payload.get("trace_id") or "").strip() or None
        query = str(payload.get("query") or "").strip() or None
        traces = self._recent_traces(limit=limit, controller=controller, surface=surface, query=query)
        selected = self.trace_detail(trace_id or (traces[0]["trace_id"] if traces else "")) if traces else {"status": "not_found", "trace_id": trace_id or ""}
        return {
            "ok": True,
            "schema_id": TRACE_REVIEW_SCHEMA_ID,
            "phase": M13_PHASE,
            "status": "ready",
            "generated_at": _now(),
            "filters": {"controller": controller, "surface": surface, "query": query, "limit": limit},
            "summary": self.status(),
            "traces": traces,
            "selected_trace": selected.get("trace"),
            "selected_status": selected.get("status"),
            "review_policy": {
                "purpose": "Inspect exactly what Control Center selected before backend generation.",
                "safe_actions": ["mark_reviewed", "mark_good", "mark_needs_fix", "mark_scope_issue", "mark_prompt_contract_issue", "mark_memory_gap"],
                "not_done_here": ["direct_memory_mutation", "force_backend_generation", "disable_safety_guard"],
            },
        }

    def _recent_traces(self, *, limit: int, controller: str | None, surface: str | None, query: str | None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if controller:
            clauses.append("controller=?")
            params.append(controller)
        if surface:
            clauses.append("surface=?")
            params.append(surface)
        if query:
            like = f"%{query.lower()}%"
            clauses.append("(lower(user_input) LIKE ? OR lower(intent) LIKE ? OR lower(prompt_contract_id) LIKE ?)")
            params.extend([like, like, like])
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            if not self._table_exists(conn, "neo_control_center_traces"):
                return []
            rows = conn.execute(
                f"""
                SELECT trace_id, controller, surface, project_id, scope_id, intent, user_input,
                       prompt_contract_id, backend_profile_id, status, created_at, selected_context_json,
                       validation_json, writeback_plan_json, metadata_json
                FROM neo_control_center_traces
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            reviews = self._latest_reviews(conn, [row["trace_id"] for row in rows])
        out: list[dict[str, Any]] = []
        for row in rows:
            context = _loads(row["selected_context_json"], {})
            validation = _loads(row["validation_json"], {})
            meta = _loads(row["metadata_json"], {})
            out.append({
                "trace_id": row["trace_id"],
                "controller": row["controller"],
                "surface": row["surface"],
                "project_id": row["project_id"],
                "scope_id": row["scope_id"],
                "intent": row["intent"],
                "user_preview": _short(row["user_input"], 160),
                "prompt_contract_id": row["prompt_contract_id"],
                "backend_profile_id": row["backend_profile_id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "context_count": int(context.get("item_count") or len(context.get("items") or [])),
                "selection_mode": context.get("selection_mode"),
                "safety_guard": context.get("safety_guard") or {},
                "validation_status": validation.get("status"),
                "review": reviews.get(row["trace_id"]),
                "phase": meta.get("phase") or meta.get("schema_id") or "control_center",
            })
        return out

    def _latest_reviews(self, conn: sqlite3.Connection, trace_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not trace_ids or not self._table_exists(conn, "neo_control_center_trace_reviews"):
            return {}
        placeholders = ",".join("?" for _ in trace_ids)
        rows = conn.execute(
            f"""
            SELECT trace_id, review_id, decision, note, reviewer, created_at, metadata_json
            FROM neo_control_center_trace_reviews
            WHERE trace_id IN ({placeholders})
            ORDER BY created_at DESC
            """,
            tuple(trace_ids),
        ).fetchall()
        reviews: dict[str, dict[str, Any]] = {}
        for row in rows:
            if row["trace_id"] not in reviews:
                reviews[row["trace_id"]] = dict(row) | {"metadata": _loads(row["metadata_json"], {})}
        return reviews

    def trace_detail(self, trace_id: str) -> dict[str, Any]:
        trace_id = str(trace_id or "").strip()
        if not trace_id:
            return {"status": "not_found", "trace_id": trace_id}
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM neo_control_center_traces WHERE trace_id=?", (trace_id,)).fetchone() if self._table_exists(conn, "neo_control_center_traces") else None
            if not row:
                return {"status": "not_found", "trace_id": trace_id}
            data = dict(row)
            memory_plan = _loads(data.get("memory_sources_json"), {})
            selected_context = _loads(data.get("selected_context_json"), {})
            validation = _loads(data.get("validation_json"), {})
            writeback_plan = _loads(data.get("writeback_plan_json"), {})
            metadata = _loads(data.get("metadata_json"), {})
            prompt_contract = metadata.get("prompt_contract") if isinstance(metadata.get("prompt_contract"), dict) else {}
            related = self._related_records(conn, trace_id, selected_context)
            latest_review = self._latest_reviews(conn, [trace_id]).get(trace_id)
        return {
            "status": "ok",
            "trace": {
                "trace_id": data.get("trace_id"),
                "controller": data.get("controller"),
                "surface": data.get("surface"),
                "project_id": data.get("project_id"),
                "scope_id": data.get("scope_id"),
                "intent": data.get("intent"),
                "user_input": data.get("user_input"),
                "memory_query_plan": memory_plan,
                "selected_context": selected_context,
                "prompt_contract_id": data.get("prompt_contract_id"),
                "prompt_contract": prompt_contract,
                "backend_profile_id": data.get("backend_profile_id"),
                "validation": validation,
                "writeback_plan": writeback_plan,
                "status": data.get("status"),
                "created_at": data.get("created_at"),
                "metadata": metadata,
                "related": related,
                "latest_review": latest_review,
                "review_hints": self._review_hints(selected_context, validation, writeback_plan, related),
            },
        }

    def _related_records(self, conn: sqlite3.Connection, trace_id: str, selected_context: dict[str, Any]) -> dict[str, Any]:
        retrieval_trace_id = selected_context.get("retrieval_trace_id") or ""
        related: dict[str, Any] = {"retrieval_access": [], "safety_violations": [], "writebacks": [], "reviews": []}
        if self._table_exists(conn, "neo_memory_access_log"):
            clauses = ["1=1"]
            params: list[Any] = []
            if retrieval_trace_id:
                clauses.append("(access_id=? OR metadata_json LIKE ?)")
                params.extend([retrieval_trace_id, f"%{retrieval_trace_id}%"])
            else:
                # Fall back to recent Control Center access records if a trace id was not recorded.
                clauses.append("consumer LIKE ?")
                params.append("%control%")
            rows = conn.execute(
                f"""
                SELECT access_id, consumer, surface, project_id, scope_id, query, result_ids_json, created_at, metadata_json
                FROM neo_memory_access_log
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at DESC
                LIMIT 8
                """,
                tuple(params),
            ).fetchall()
            related["retrieval_access"] = [dict(row) | {"result_ids": _loads(row["result_ids_json"], []), "metadata": _loads(row["metadata_json"], {})} for row in rows]
        if self._table_exists(conn, "neo_memory_safety_violations"):
            source_ids = [trace_id]
            if retrieval_trace_id:
                source_ids.append(retrieval_trace_id)
            placeholders = ",".join("?" for _ in source_ids)
            rows = conn.execute(
                f"""
                SELECT violation_id, check_type, severity, status, surface, project_id, scope_id, source_type, source_id, item_id, message, details_json, created_at
                FROM neo_memory_safety_violations
                WHERE source_id IN ({placeholders}) OR details_json LIKE ?
                ORDER BY created_at DESC
                LIMIT 12
                """,
                (*source_ids, f"%{trace_id}%"),
            ).fetchall()
            related["safety_violations"] = [dict(row) | {"details": _loads(row["details_json"], {})} for row in rows]
        if self._table_exists(conn, "neo_memory_writebacks"):
            rows = conn.execute(
                """
                SELECT writeback_id, source_trace_id, source_type, source_id, surface, project_id, scope_id, memory_type, title,
                       content, risk_level, status, confidence, importance, created_at, updated_at, applied_event_id, applied_fragment_id
                FROM neo_memory_writebacks
                WHERE source_trace_id=? OR source_id=?
                ORDER BY created_at DESC
                LIMIT 12
                """,
                (trace_id, trace_id),
            ).fetchall()
            related["writebacks"] = [dict(row) | {"content_preview": _short(row["content"], 240)} for row in rows]
        if self._table_exists(conn, "neo_control_center_trace_reviews"):
            rows = conn.execute(
                """
                SELECT review_id, trace_id, decision, note, reviewer, created_at, metadata_json
                FROM neo_control_center_trace_reviews
                WHERE trace_id=?
                ORDER BY created_at DESC
                LIMIT 12
                """,
                (trace_id,),
            ).fetchall()
            related["reviews"] = [dict(row) | {"metadata": _loads(row["metadata_json"], {})} for row in rows]
        return related

    def _review_hints(self, selected_context: dict[str, Any], validation: dict[str, Any], writeback_plan: dict[str, Any], related: dict[str, Any]) -> list[dict[str, str]]:
        hints: list[dict[str, str]] = []
        safety = selected_context.get("safety_guard") if isinstance(selected_context.get("safety_guard"), dict) else {}
        if safety.get("rejected_count") or safety.get("violation_count"):
            hints.append({"level": "warn", "message": "Safety guard blocked or flagged some context. Inspect blocked items before trusting the output."})
        if not selected_context.get("items"):
            hints.append({"level": "warn", "message": "No selected context reached the prompt. The backend may answer from generic model knowledge."})
        if validation.get("checks") and str(validation.get("status")) in {"planned", "pending"}:
            hints.append({"level": "info", "message": "Validation checks were planned. Full output validation enforcement is a later behavior phase."})
        if (related.get("writebacks") or []) and any(str(item.get("status")) in {"pending_review", "queued"} for item in related.get("writebacks") or []):
            hints.append({"level": "review", "message": "This trace has writeback candidates waiting for review or application."})
        if writeback_plan.get("requires_review_for"):
            hints.append({"level": "info", "message": "High-impact memory changes from this lane require review."})
        return hints or [{"level": "ready", "message": "Trace is inspectable; no immediate review warnings detected."}]

    def record_review(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        trace_id = str(payload.get("trace_id") or "").strip()
        if not trace_id:
            return {"ok": False, "status": "missing_trace_id"}
        decision = str(payload.get("decision") or "reviewed").strip() or "reviewed"
        allowed = {"reviewed", "good", "needs_fix", "scope_issue", "prompt_contract_issue", "memory_gap", "safety_issue"}
        if decision not in allowed:
            return {"ok": False, "status": "unsupported_decision", "decision": decision, "allowed": sorted(allowed)}
        note = str(payload.get("note") or "").strip()
        reviewer = str(payload.get("reviewer") or "local_admin").strip() or "local_admin"
        review_id = f"ccrev_{uuid4().hex[:16]}"
        stamp = _now()
        with self._connect() as conn:
            exists = conn.execute("SELECT 1 FROM neo_control_center_traces WHERE trace_id=?", (trace_id,)).fetchone()
            if not exists:
                return {"ok": False, "status": "trace_not_found", "trace_id": trace_id}
            conn.execute(
                """
                INSERT INTO neo_control_center_trace_reviews (review_id, trace_id, decision, note, reviewer, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (review_id, trace_id, decision, note, reviewer, stamp, _json({"phase": M13_PHASE, "schema_id": TRACE_REVIEW_SCHEMA_ID, "source": "admin_trace_review_ui"})),
            )
        return {"ok": True, "status": "recorded", "review_id": review_id, "trace_id": trace_id, "decision": decision, "created_at": stamp}
