from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
import json
import sqlite3

from neo_app.tool_registry import permission_profiles_payload, tool_registry_status_payload

ROOT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = ROOT_DIR / "neo_data" / "admin" / "tool_execution_ledger.sqlite3"
TOOL_LEDGER_SCHEMA = "neo.tool_execution_ledger.v1"
TOOL_LEDGER_VERSION = "0.1.0"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tool_ledger_events (
                ledger_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                tool_id TEXT,
                tool_label TEXT,
                category TEXT,
                action_type TEXT,
                risk_level TEXT,
                permission_profile TEXT,
                actor TEXT,
                surface TEXT,
                intent TEXT,
                status TEXT,
                confirmed INTEGER DEFAULT 0,
                blocked INTEGER DEFAULT 0,
                endpoint TEXT,
                payload_preview TEXT,
                result_summary TEXT,
                memory_trace_id TEXT,
                metadata_json TEXT
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_ledger_created ON tool_ledger_events(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_ledger_tool ON tool_ledger_events(tool_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_ledger_status ON tool_ledger_events(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_ledger_actor ON tool_ledger_events(actor)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_ledger_surface ON tool_ledger_events(surface)")
        conn.commit()


def _json_preview(value: Any, limit: int = 1600) -> str:
    try:
        text = json.dumps(value if value is not None else {}, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return text[:limit]


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except Exception:
        metadata = {}
    return {
        "ledger_id": row["ledger_id"],
        "created_at": row["created_at"],
        "tool_id": row["tool_id"],
        "tool_label": row["tool_label"],
        "category": row["category"],
        "action_type": row["action_type"],
        "risk_level": row["risk_level"],
        "permission_profile": row["permission_profile"],
        "actor": row["actor"],
        "surface": row["surface"],
        "intent": row["intent"],
        "status": row["status"],
        "confirmed": bool(row["confirmed"]),
        "blocked": bool(row["blocked"]),
        "endpoint": row["endpoint"],
        "payload_preview": row["payload_preview"],
        "result_summary": row["result_summary"],
        "memory_trace_id": row["memory_trace_id"],
        "metadata": metadata,
    }


def _current_profile_id() -> str:
    try:
        return str((permission_profiles_payload() or {}).get("active_profile_id") or "guided")
    except Exception:
        return "guided"


def record_tool_ledger_event(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    action = data.get("action") if isinstance(data.get("action"), dict) else {}
    policy = action.get("tool_policy") or data.get("tool_policy") or {}
    tool = policy.get("tool") or data.get("tool") or {}
    tool_id = str(data.get("tool_id") or action.get("tool_id") or policy.get("tool_id") or tool.get("tool_id") or action.get("action_type") or "unknown")
    tool_label = str(data.get("tool_label") or action.get("tool_label") or tool.get("label") or action.get("label") or tool_id)
    category = str(data.get("category") or action.get("tool_category") or tool.get("category") or "unknown")
    action_type = str(data.get("action_type") or action.get("action_type") or tool_id)
    risk_level = str(data.get("risk_level") or action.get("risk_level") or tool.get("risk_level") or "low")
    status = str(data.get("status") or action.get("status") or "recorded")
    blocked = bool(data.get("blocked") or status == "blocked" or action.get("status") == "blocked")
    confirmed = bool(data.get("confirmed") or data.get("execute_confirmed") or data.get("confirm"))
    permission_profile = str(data.get("permission_profile") or policy.get("active_profile_id") or _current_profile_id())
    ledger_id = str(data.get("ledger_id") or f"led_{uuid4().hex[:14]}")
    event = {
        "ledger_id": ledger_id,
        "created_at": data.get("created_at") or _now(),
        "tool_id": tool_id,
        "tool_label": tool_label,
        "category": category,
        "action_type": action_type,
        "risk_level": risk_level,
        "permission_profile": permission_profile,
        "actor": str(data.get("actor") or "neo"),
        "surface": str(data.get("surface") or "assistant"),
        "intent": str(data.get("intent") or action.get("intent") or "unknown"),
        "status": status,
        "confirmed": confirmed,
        "blocked": blocked,
        "endpoint": str(data.get("endpoint") or ""),
        "payload_preview": data.get("payload_preview") or _json_preview(data.get("payload") if "payload" in data else action.get("payload")),
        "result_summary": str(data.get("result_summary") or data.get("summary") or "")[:1800],
        "memory_trace_id": str(data.get("memory_trace_id") or data.get("trace_id") or ""),
        "metadata": data.get("metadata") if isinstance(data.get("metadata"), dict) else {},
    }
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO tool_ledger_events (
                ledger_id, created_at, tool_id, tool_label, category, action_type, risk_level,
                permission_profile, actor, surface, intent, status, confirmed, blocked, endpoint,
                payload_preview, result_summary, memory_trace_id, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event["ledger_id"], event["created_at"], event["tool_id"], event["tool_label"],
                event["category"], event["action_type"], event["risk_level"], event["permission_profile"],
                event["actor"], event["surface"], event["intent"], event["status"], int(event["confirmed"]),
                int(event["blocked"]), event["endpoint"], event["payload_preview"], event["result_summary"],
                event["memory_trace_id"], json.dumps(event["metadata"], ensure_ascii=False, default=str),
            ),
        )
        conn.commit()
    return {"ok": True, "schema_id": "neo.tool_ledger.record.v1", "event": event}


def list_tool_ledger_events(filters: dict[str, Any] | None = None) -> dict[str, Any]:
    data = filters or {}
    _ensure_db()
    where: list[str] = []
    params: list[Any] = []
    for key in ["tool_id", "category", "actor", "surface", "status", "risk_level"]:
        if data.get(key):
            where.append(f"{key} = ?")
            params.append(str(data.get(key)))
    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    limit = max(1, min(200, int(data.get("limit") or 50)))
    offset = max(0, int(data.get("offset") or 0))
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute(f"SELECT COUNT(*) AS c FROM tool_ledger_events {where_sql}", params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT * FROM tool_ledger_events {where_sql} ORDER BY created_at DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return {"ok": True, "schema_id": "neo.tool_ledger.events.v1", "total": total, "limit": limit, "offset": offset, "events": [_row_to_event(row) for row in rows]}


def get_tool_ledger_event(ledger_id: str) -> dict[str, Any]:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM tool_ledger_events WHERE ledger_id = ?", (ledger_id,)).fetchone()
    if not row:
        return {"ok": False, "schema_id": "neo.tool_ledger.event.v1", "status": "not_found", "ledger_id": ledger_id}
    return {"ok": True, "schema_id": "neo.tool_ledger.event.v1", "event": _row_to_event(row)}


def tool_ledger_status_payload() -> dict[str, Any]:
    _ensure_db()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        total = conn.execute("SELECT COUNT(*) AS c FROM tool_ledger_events").fetchone()["c"]
        status_rows = conn.execute("SELECT status, COUNT(*) AS c FROM tool_ledger_events GROUP BY status").fetchall()
        risk_rows = conn.execute("SELECT risk_level, COUNT(*) AS c FROM tool_ledger_events GROUP BY risk_level").fetchall()
        recent_rows = conn.execute("SELECT * FROM tool_ledger_events ORDER BY created_at DESC LIMIT 8").fetchall()
    registry = tool_registry_status_payload()
    return {
        "ok": True,
        "schema_id": "neo.tool_ledger.status.v1",
        "status": "ready",
        "label": "Tool Execution Ledger",
        "runtime_version": TOOL_LEDGER_VERSION,
        "db_path": str(DB_PATH.relative_to(ROOT_DIR)),
        "event_count": int(total),
        "status_counts": {row["status"] or "unknown": int(row["c"]) for row in status_rows},
        "risk_counts": {row["risk_level"] or "unknown": int(row["c"]) for row in risk_rows},
        "active_permission_profile": registry.get("active_profile_id"),
        "endpoints": {
            "status": "/api/tools/ledger/status",
            "events": "/api/tools/ledger/events",
            "detail": "/api/tools/ledger/events/{ledger_id}",
            "record": "/api/tools/ledger/record",
        },
        "recent_events": [_row_to_event(row) for row in recent_rows],
        "policy": "Records Assistant, Operator, Voice, Internet/API, and registry-gated tool activity for Admin audit visibility. It does not execute tools by itself.",
    }
