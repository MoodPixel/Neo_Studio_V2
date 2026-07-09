from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .unified_schema import ensure_unified_memory_schema, unified_memory_schema_status

M12_PHASE = "M12"
SAFETY_SCHEMA_ID = "neo.memory.safety_guard.phase_m12.v1"
SAFETY_VERSION = "memory-sandbox-guard.v1"

_HIGH_IMPACT_TYPES = {
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
_DEBUG_SOURCE_TYPES = {"debug", "trace", "diagnostic", "scene_memory_injection_trace", "control_center_trace"}
_CANON_TYPES = {"canon", "canon_fact", "canon_memory", "world_lore", "universe_lore"}


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


def _norm(value: Any) -> str:
    return str(value or "").strip()


def _short(value: Any, limit: int = 360) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > limit:
        return text[: limit - 1].rstrip() + "…"
    return text


class MemorySafetyGuard:
    """Phase M12 memory sandbox + safety guard.

    M12 is the anti-memory-soup layer. It does not replace retrieval, writeback,
    prompt contracts, or Control Center planning. It validates that selected
    context and writeback candidates stay inside the intended surface/project/
    scope sandbox, and that high-impact memories are reviewed before becoming
    active long-term memory.
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
            CREATE TABLE IF NOT EXISTS neo_memory_sandbox_rules (
                rule_id TEXT PRIMARY KEY,
                rule_type TEXT NOT NULL,
                surface TEXT NOT NULL DEFAULT '*',
                project_id TEXT,
                scope_type TEXT,
                scope_id TEXT,
                severity TEXT NOT NULL DEFAULT 'block',
                status TEXT NOT NULL DEFAULT 'active',
                description TEXT NOT NULL DEFAULT '',
                rule_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(rule_type, surface, project_id, scope_type, scope_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS neo_memory_safety_violations (
                violation_id TEXT PRIMARY KEY,
                guard_phase TEXT NOT NULL DEFAULT 'M12',
                check_type TEXT NOT NULL,
                severity TEXT NOT NULL DEFAULT 'warn',
                status TEXT NOT NULL DEFAULT 'open',
                surface TEXT NOT NULL DEFAULT 'global',
                project_id TEXT,
                scope_id TEXT,
                source_type TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                item_id TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                resolved_at TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_sandbox_rules_scope ON neo_memory_sandbox_rules(rule_type, surface, project_id, scope_id, status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_safety_violations_scope ON neo_memory_safety_violations(surface, project_id, scope_id, status, created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_safety_violations_source ON neo_memory_safety_violations(source_type, source_id)")
        self._seed_default_rules(conn)

    def _seed_default_rules(self, conn: sqlite3.Connection) -> None:
        stamp = _now()
        defaults = [
            {
                "rule_id": "m12_surface_sandbox_isolation",
                "rule_type": "surface_isolation",
                "surface": "*",
                "severity": "block",
                "description": "Selected memory context must match the active surface unless explicit cross-surface access is requested.",
                "rule_json": {"allow_cross_surface_with_flag": True, "flag": "allow_cross_surface"},
            },
            {
                "rule_id": "m12_project_sandbox_isolation",
                "rule_type": "project_isolation",
                "surface": "*",
                "severity": "block",
                "description": "Selected memory context must match the active project unless explicit cross-project access is requested.",
                "rule_json": {"allow_cross_project_with_flag": True, "flag": "allow_cross_project"},
            },
            {
                "rule_id": "m12_scope_sandbox_isolation",
                "rule_type": "scope_isolation",
                "surface": "roleplay",
                "severity": "block",
                "description": "Roleplay memory must stay inside the active universe/world/scene scope unless graph expansion explicitly permits nearby linked scopes.",
                "rule_json": {"allow_graph_expansion_with_flag": True, "flag": "allow_scope_expansion"},
            },
            {
                "rule_id": "m12_high_impact_review_gate",
                "rule_type": "writeback_review_gate",
                "surface": "*",
                "severity": "block",
                "description": "Canon, relationship, character secret/knowledge, user preference, cross-project, and player-character writebacks require review before apply.",
                "rule_json": {"high_impact_types": sorted(_HIGH_IMPACT_TYPES)},
            },
            {
                "rule_id": "m12_debug_trace_not_canon",
                "rule_type": "debug_trace_guard",
                "surface": "*",
                "severity": "block",
                "description": "Debug traces, diagnostics, and prompt previews cannot become canon or confirmed long-term memory.",
                "rule_json": {"debug_source_types": sorted(_DEBUG_SOURCE_TYPES), "blocked_memory_types": sorted(_CANON_TYPES)},
            },
            {
                "rule_id": "m12_unknown_detail_guard",
                "rule_type": "roleplay_unknown_detail_guard",
                "surface": "roleplay",
                "severity": "warn",
                "description": "Roleplay outputs should mark unspecified appearance/location/emotion facts as unknown instead of inventing them.",
                "rule_json": {"enforced_by": "prompt_contract+output_validator", "phase": "M12 foundation"},
            },
        ]
        for rule in defaults:
            conn.execute(
                """
                INSERT OR IGNORE INTO neo_memory_sandbox_rules (
                    rule_id, rule_type, surface, project_id, scope_type, scope_id,
                    severity, status, description, rule_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    rule["rule_id"], rule["rule_type"], rule.get("surface") or "*", rule.get("project_id"),
                    rule.get("scope_type"), rule.get("scope_id"), rule.get("severity") or "block",
                    rule.get("description") or "", _json(rule.get("rule_json") or {}), stamp, stamp,
                ),
            )

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            schema = unified_memory_schema_status(conn)
            rules = conn.execute("SELECT rule_type, surface, severity, status, COUNT(*) AS count FROM neo_memory_sandbox_rules GROUP BY rule_type, surface, severity, status ORDER BY rule_type").fetchall()
            violations = conn.execute("SELECT status, severity, check_type, COUNT(*) AS count FROM neo_memory_safety_violations GROUP BY status, severity, check_type ORDER BY status, severity").fetchall()
            recent = conn.execute(
                """
                SELECT violation_id, check_type, severity, status, surface, project_id, scope_id, source_type, source_id, item_id, message, created_at
                FROM neo_memory_safety_violations
                ORDER BY created_at DESC
                LIMIT 10
                """
            ).fetchall()
        return {
            "ok": True,
            "schema_id": SAFETY_SCHEMA_ID,
            "phase": M12_PHASE,
            "version": SAFETY_VERSION,
            "status": "ready",
            "rules": [dict(row) for row in rules],
            "violations_by_status": [dict(row) for row in violations],
            "recent_violations": [dict(row) for row in recent],
            "unified_schema": schema,
            "policy": "M12 prevents memory soup: surface/project/scope isolation, high-impact review gates, debug-trace canon blocking, and explicit cross-sandbox permission.",
            "endpoints": {
                "status": "/api/memory/safety/status",
                "rules": "/api/memory/safety/rules",
                "validate_context": "/api/memory/safety/validate-context",
                "validate_writeback": "/api/memory/safety/validate-writeback",
                "audit": "/api/memory/safety/audit",
                "violations": "/api/memory/safety/violations",
            },
        }

    def rules(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        surface = _norm(data.get("surface"))
        with self._connect() as conn:
            clauses = ["status='active'"]
            params: list[Any] = []
            if surface:
                clauses.append("(surface=? OR surface='*')")
                params.append(surface)
            rows = conn.execute(
                f"SELECT * FROM neo_memory_sandbox_rules WHERE {' AND '.join(clauses)} ORDER BY rule_type, surface",
                tuple(params),
            ).fetchall()
        return {"ok": True, "schema_id": SAFETY_SCHEMA_ID, "phase": M12_PHASE, "rules": [dict(row) | {"rule": _loads(row["rule_json"], {})} for row in rows]}

    def validate_context(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        data = payload or {}
        surface = _norm(data.get("surface") or data.get("active_surface") or "global")
        project_id = _norm(data.get("project_id") or data.get("active_project_id")) or None
        scope_id = _norm(data.get("scope_id") or data.get("active_scope_id")) or None
        allow_cross_surface = bool(data.get("allow_cross_surface"))
        allow_cross_project = bool(data.get("allow_cross_project"))
        allow_scope_expansion = bool(data.get("allow_scope_expansion"))
        source_type = _norm(data.get("source_type") or "context_selection")
        source_id = _norm(data.get("source_id") or data.get("trace_id"))
        raw_items = data.get("items")
        if raw_items is None and isinstance(data.get("selected_context"), dict):
            raw_items = data["selected_context"].get("items")
        if raw_items is None and isinstance(data.get("results"), list):
            raw_items = data.get("results")
        items = raw_items if isinstance(raw_items, list) else []
        violations: list[dict[str, Any]] = []
        accepted: list[dict[str, Any]] = []
        rejected: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            item_id = _norm(item.get("fragment_id") or item.get("memory_id") or item.get("id") or f"item_{idx}")
            item_surface = _norm(item.get("surface") or "global")
            item_project = _norm(item.get("project_id")) or None
            item_scope = _norm(item.get("scope_id")) or None
            item_metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            item_source_type = _norm(item.get("source_type") or item_metadata.get("source_type") or "")
            item_memory_type = _norm(item.get("memory_type"))
            local_violations = []
            if surface and item_surface and item_surface != surface and not allow_cross_surface:
                local_violations.append(self._violation("surface_isolation", "block", surface, project_id, scope_id, source_type, source_id, item_id, f"Blocked cross-surface memory: active={surface}, item={item_surface}.", item))
            if project_id and item_project and item_project != project_id and not allow_cross_project:
                local_violations.append(self._violation("project_isolation", "block", surface, project_id, scope_id, source_type, source_id, item_id, f"Blocked cross-project memory: active={project_id}, item={item_project}.", item))
            if scope_id and item_scope and item_scope != scope_id and surface == "roleplay" and not allow_scope_expansion:
                local_violations.append(self._violation("scope_isolation", "block", surface, project_id, scope_id, source_type, source_id, item_id, f"Blocked roleplay cross-scope memory: active={scope_id}, item={item_scope}.", item))
            if item_source_type in _DEBUG_SOURCE_TYPES and item_memory_type in _CANON_TYPES:
                local_violations.append(self._violation("debug_trace_guard", "block", surface, project_id, scope_id, source_type, source_id, item_id, "Blocked diagnostic/debug trace from becoming canon context.", item))
            if local_violations:
                violations.extend(local_violations)
                rejected.append({"item_id": item_id, "title": item.get("title") or "Memory item", "reasons": [v["message"] for v in local_violations]})
            else:
                accepted.append(item)
        if persist and violations:
            with self._connect() as conn:
                for violation in violations:
                    self._record_violation(conn, violation)
        blocked = [v for v in violations if v.get("severity") == "block"]
        return {
            "ok": not blocked,
            "schema_id": SAFETY_SCHEMA_ID,
            "phase": M12_PHASE,
            "status": "blocked" if blocked else ("warn" if violations else "passed"),
            "active_scope": {"surface": surface, "project_id": project_id, "scope_id": scope_id},
            "accepted_count": len(accepted),
            "rejected_count": len(rejected),
            "violation_count": len(violations),
            "violations": violations,
            "accepted_items": accepted,
            "rejected_items": rejected,
            "policy": "Context may only cross surface/project/scope boundaries when explicitly requested and traceable.",
        }

    def validate_writeback(self, payload: dict[str, Any] | None = None, *, persist: bool = True) -> dict[str, Any]:
        data = payload or {}
        items = data.get("items") if isinstance(data.get("items"), list) else [data]
        violations: list[dict[str, Any]] = []
        decisions: list[dict[str, Any]] = []
        for idx, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            surface = _norm(item.get("surface") or data.get("surface") or "global")
            project_id = _norm(item.get("project_id") or data.get("project_id")) or None
            scope_id = _norm(item.get("scope_id") or data.get("scope_id")) or None
            memory_type = _norm(item.get("memory_type") or item.get("type") or "memory_candidate")
            source_type = _norm(item.get("source_type") or data.get("source_type") or "writeback")
            source_id = _norm(item.get("source_id") or item.get("trace_id") or data.get("source_id") or data.get("trace_id"))
            item_id = _norm(item.get("writeback_id") or item.get("candidate_id") or item.get("id") or f"candidate_{idx}")
            status = _norm(item.get("status"))
            risk = _norm(item.get("risk_level") or item.get("risk") or "")
            local: list[dict[str, Any]] = []
            if memory_type in _HIGH_IMPACT_TYPES and status in {"approved", "applied", "auto_applied"} and not bool(item.get("reviewed") or item.get("reviewed_at") or data.get("reviewed")):
                local.append(self._violation("writeback_review_gate", "block", surface, project_id, scope_id, source_type, source_id, item_id, f"High-impact writeback '{memory_type}' cannot auto-apply without review.", item))
            if risk == "auto_allowed" and memory_type in _HIGH_IMPACT_TYPES:
                local.append(self._violation("writeback_risk_mismatch", "block", surface, project_id, scope_id, source_type, source_id, item_id, f"High-impact writeback '{memory_type}' was marked auto_allowed.", item))
            if source_type in _DEBUG_SOURCE_TYPES and memory_type in _CANON_TYPES:
                local.append(self._violation("debug_trace_guard", "block", surface, project_id, scope_id, source_type, source_id, item_id, "Debug/diagnostic source cannot write canon memory.", item))
            violations.extend(local)
            decisions.append({"item_id": item_id, "memory_type": memory_type, "status": "blocked" if any(v.get("severity") == "block" for v in local) else "allowed", "violations": [v["message"] for v in local]})
        if persist and violations:
            with self._connect() as conn:
                for violation in violations:
                    self._record_violation(conn, violation)
        blocked = [v for v in violations if v.get("severity") == "block"]
        return {"ok": not blocked, "schema_id": SAFETY_SCHEMA_ID, "phase": M12_PHASE, "status": "blocked" if blocked else ("warn" if violations else "passed"), "decisions": decisions, "violation_count": len(violations), "violations": violations}

    def audit(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        limit = max(1, min(int(data.get("limit") or 100), 1000))
        persist = bool(data.get("persist", True))
        checks: list[dict[str, Any]] = []
        with self._connect() as conn:
            trace_rows = conn.execute(
                """
                SELECT trace_id, controller, surface, project_id, scope_id, selected_context_json, created_at
                FROM neo_control_center_traces
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        trace_violations: list[dict[str, Any]] = []
        for row in trace_rows:
            selected = _loads(row["selected_context_json"], {}) or {}
            result = self.validate_context({
                "surface": row["surface"],
                "project_id": row["project_id"],
                "scope_id": row["scope_id"],
                "source_type": "control_center_trace",
                "source_id": row["trace_id"],
                "selected_context": selected,
            }, persist=persist)
            if result.get("violation_count"):
                trace_violations.extend(result.get("violations") or [])
        checks.append({"check": "control_center_context_scope", "scanned": len(trace_rows), "violations": len(trace_violations)})

        writeback_violations: list[dict[str, Any]] = []
        with self._connect() as conn:
            if self._table_exists(conn, "neo_memory_writebacks"):
                rows = conn.execute(
                    """
                    SELECT writeback_id, source_trace_id, source_type, source_id, surface, project_id, scope_id, memory_type, title, content, payload_json, risk_level, status, reviewed_at
                    FROM neo_memory_writebacks
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = []
        for row in rows:
            item = dict(row)
            item["reviewed"] = bool(row["reviewed_at"])
            result = self.validate_writeback(item, persist=persist)
            if result.get("violation_count"):
                writeback_violations.extend(result.get("violations") or [])
        checks.append({"check": "writeback_review_gate", "scanned": len(rows), "violations": len(writeback_violations)})

        all_violations = trace_violations + writeback_violations
        return {
            "ok": not any(v.get("severity") == "block" for v in all_violations),
            "schema_id": SAFETY_SCHEMA_ID,
            "phase": M12_PHASE,
            "status": "blocked" if any(v.get("severity") == "block" for v in all_violations) else ("warn" if all_violations else "passed"),
            "checks": checks,
            "violation_count": len(all_violations),
            "violations_preview": all_violations[:25],
            "persisted": persist,
            "policy": "Audit checks recent Control Center context and writebacks for sandbox leakage and unsafe high-impact auto-apply.",
        }

    def violations(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = payload or {}
        limit = max(1, min(int(data.get("limit") or 50), 500))
        status = _norm(data.get("status"))
        severity = _norm(data.get("severity"))
        surface = _norm(data.get("surface"))
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        if severity:
            clauses.append("severity=?")
            params.append(severity)
        if surface:
            clauses.append("surface=?")
            params.append(surface)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM neo_memory_safety_violations{where} ORDER BY created_at DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return {"ok": True, "schema_id": SAFETY_SCHEMA_ID, "phase": M12_PHASE, "violations": [dict(row) | {"details": _loads(row["details_json"], {})} for row in rows], "count": len(rows)}

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        try:
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
        except sqlite3.Error:
            return False

    def _violation(self, check_type: str, severity: str, surface: str, project_id: str | None, scope_id: str | None, source_type: str, source_id: str, item_id: str, message: str, details: Any) -> dict[str, Any]:
        return {
            "violation_id": f"m12viol_{uuid4().hex[:14]}",
            "guard_phase": M12_PHASE,
            "check_type": check_type,
            "severity": severity,
            "status": "open",
            "surface": surface or "global",
            "project_id": project_id,
            "scope_id": scope_id,
            "source_type": source_type,
            "source_id": source_id,
            "item_id": item_id,
            "message": message,
            "details": {"preview": _short(details), "raw": details if isinstance(details, dict) else {}},
            "created_at": _now(),
        }

    def _record_violation(self, conn: sqlite3.Connection, violation: dict[str, Any]) -> None:
        conn.execute(
            """
            INSERT INTO neo_memory_safety_violations (
                violation_id, guard_phase, check_type, severity, status, surface, project_id, scope_id,
                source_type, source_id, item_id, message, details_json, created_at, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                violation.get("violation_id") or f"m12viol_{uuid4().hex[:14]}",
                violation.get("guard_phase") or M12_PHASE,
                violation.get("check_type") or "unknown",
                violation.get("severity") or "warn",
                violation.get("status") or "open",
                violation.get("surface") or "global",
                violation.get("project_id"),
                violation.get("scope_id"),
                violation.get("source_type") or "",
                violation.get("source_id") or "",
                violation.get("item_id") or "",
                violation.get("message") or "",
                _json(violation.get("details") or {}),
                violation.get("created_at") or _now(),
                violation.get("resolved_at"),
            ),
        )
