from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.sqlite_store import ensure_roleplay_memory_schema, _connect
from neo_app.roleplay.provenance import provenance_graph_payload, provenance_trace_payload

SCHEMA_ID = "neo.roleplay.phase15.provenance_debug_ui.v1"
VERSION = "15.0.0-provenance-debug-ui"

ROOT = Path("neo_data/roleplay")
REPORT_PATH = ROOT / "provenance_debug_state.json"
SYSTEM_MEMORY_DOC = Path("neo_system_records/05_MEMORY_SYSTEM/ROLEPLAY_PHASE_15_PROVENANCE_DEBUG_UI.md")
SYSTEM_SURFACE_DOC = Path("neo_system_records/06_SURFACES/roleplay/PHASE_15_PROVENANCE_DEBUG_UI.md")

DEBUG_TABLES = (
    "rp_retrieval_traces",
    "rp_scene_memory_packets",
    "rp_turn_writebacks",
    "rp_continuity_events",
    "rp_continuity_rows",
    "rp_callback_anchors",
    "rp_unresolved_threads",
    "rp_story_checkpoint_snapshots",
    "rp_story_restore_events",
    "rp_contradiction_reports",
    "rp_vector_index",
    "rp_chroma_mirror_log",
    "rp_retrieval_label_proofs",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(value: Any, default: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value or "")
    except Exception:
        return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _write_doc(path: Path, title: str, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# {title}\n\n{body.strip()}\n", encoding="utf-8")


def ensure_provenance_debug_schema() -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS rp_debug_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                source_table TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                scene_id TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                storyline_id TEXT DEFAULT '',
                sandbox_id TEXT DEFAULT '',
                status TEXT DEFAULT 'active',
                payload_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_debug_events_type ON rp_debug_events(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_debug_events_source ON rp_debug_events(source_table, source_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rp_debug_events_scene ON rp_debug_events(scene_id)")
        conn.commit()
    return {"ok": True, "status": "ready", "table": "rp_debug_events"}


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"] or 0)
    except Exception:
        return 0


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _recent_rows(conn: sqlite3.Connection, table: str, *, limit: int = 8) -> list[dict[str, Any]]:
    cols = _columns(conn, table)
    if not cols:
        return []
    order_col = "created_at" if "created_at" in cols else "updated_at" if "updated_at" in cols else "rowid"
    try:
        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?", (int(limit),)).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        title = data.get("title") or data.get("query") or data.get("summary") or data.get("event_type") or data.get("packet_id") or data.get("trace_id") or data.get("writeback_id") or data.get("checkpoint_id") or data.get("report_id") or "record"
        source_id = data.get("trace_id") or data.get("packet_id") or data.get("writeback_id") or data.get("event_id") or data.get("checkpoint_id") or data.get("report_id") or data.get("fragment_id") or data.get("row_id") or data.get("source_id") or ""
        payload_preview = ""
        for key in ("payload_json", "results_json", "packet_json", "summary", "content", "event_json"):
            if data.get(key):
                payload_preview = str(data.get(key))[:900]
                break
        out.append({
            "source_table": table,
            "source_id": str(source_id or ""),
            "title": str(title or source_id or table)[:160],
            "status": str(data.get("status") or data.get("vector_status") or data.get("chroma_status") or "active"),
            "scene_id": str(data.get("scene_id") or ""),
            "session_id": str(data.get("session_id") or ""),
            "sandbox_id": str(data.get("sandbox_id") or ""),
            "created_at": str(data.get("created_at") or data.get("updated_at") or data.get("indexed_at") or ""),
            "payload_preview": payload_preview,
        })
    return out


def _recent_debug_matrix(conn: sqlite3.Connection, *, limit: int = 8) -> dict[str, list[dict[str, Any]]]:
    return {table: _recent_rows(conn, table, limit=limit) for table in DEBUG_TABLES}


def _read_latest_scene_execution() -> dict[str, Any]:
    try:
        path = ROOT / "scene" / "transcript.json"
        if not path.exists():
            path = ROOT / "scene_transcript.json"
        payload = _read_json(path.read_text(encoding="utf-8"), {}) if path.exists() else {}
        turns = payload.get("turns") or payload.get("transcript") or []
        last_turn = turns[-1] if isinstance(turns, list) and turns else {}
        return {
            "path": str(path),
            "turn_count": len(turns) if isinstance(turns, list) else 0,
            "last_turn": last_turn if isinstance(last_turn, dict) else {},
            "last_execution": payload.get("last_execution") or (last_turn.get("metadata", {}) if isinstance(last_turn, dict) else {}),
        }
    except Exception as exc:
        return {"error": str(exc), "turn_count": 0, "last_turn": {}, "last_execution": {}}


def _latest_packet_file() -> dict[str, Any]:
    root = ROOT / "scene_packets"
    try:
        files = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            return {}
        payload = _read_json(files[0].read_text(encoding="utf-8"), {})
        return {"path": str(files[0]), "packet_id": payload.get("scene_packet_id") or payload.get("packet_id"), "title": payload.get("title"), "counts": payload.get("counts") or {}}
    except Exception:
        return {}


def provenance_debug_contract_payload(write_report: bool = True) -> dict[str, Any]:
    ensure_provenance_debug_schema()
    payload = {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "status": "active",
        "ready": True,
        "purpose": "One debug layer for proving which Roleplay/Novel memory, retrieval traces, scene packets, writebacks, checkpoints, and canon guards were used.",
        "source_of_truth": "SQLite remains authoritative; debug UI reads SQLite rows and linked JSON snapshots.",
        "endpoints": {
            "contract": "/api/roleplay/provenance-debug/contract",
            "state": "/api/roleplay/provenance-debug/state",
            "dashboard": "/api/roleplay/provenance-debug/dashboard",
            "inspect": "/api/roleplay/provenance-debug/inspect",
            "ensure_schema": "/api/roleplay/provenance-debug/ensure-schema",
        },
        "debug_surfaces": [
            "Studio > Inspector",
            "Studio > Runtime",
            "Scene > Scene Chat proof panels",
            "Stories > Inspector > Provenance",
        ],
        "tracks": [
            "retrieval_trace",
            "scene_packet",
            "memory_injection",
            "turn_writeback",
            "continuity_event",
            "checkpoint_restore",
            "contradiction_report",
            "vector_index",
            "chroma_mirror",
            "retrieval_label_proof",
        ],
    }
    if write_report:
        _write_json(ROOT / "provenance_debug_contract.json", payload)
        _write_doc(SYSTEM_MEMORY_DOC, "Roleplay Phase 15 — Provenance + Debug UI", "Adds a unified debug/provenance dashboard across retrieval traces, scene packets, turn writebacks, continuity, checkpoints, vectors, Chroma mirror, and contradiction reports. SQLite remains the source of truth; debug endpoints only inspect and report.")
        _write_doc(SYSTEM_SURFACE_DOC, "Roleplay Surface Phase 15 — Provenance + Debug UI", "Studio Inspector now has a compact debug dashboard. It shows recent traces, scene packets, writebacks, continuity rows, restore events, and graph counts so Scene Chat can prove what memory it used.")
    return payload


def provenance_debug_state_payload(write_report: bool = True) -> dict[str, Any]:
    ensure_provenance_debug_schema()
    with _connect() as conn:
        counts = {table: _table_count(conn, table) for table in DEBUG_TABLES}
        counts["rp_debug_events"] = _table_count(conn, "rp_debug_events")
    graph = provenance_graph_payload(limit=120)
    scene = _read_latest_scene_execution()
    packet = _latest_packet_file()
    payload = {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "status": "active",
        "ready": True,
        "counts": counts,
        "graph_summary": graph.get("counts", {}),
        "latest_scene_execution": scene,
        "latest_scene_packet_file": packet,
        "health": {
            "has_retrieval_traces": counts.get("rp_retrieval_traces", 0) > 0,
            "has_scene_packets": counts.get("rp_scene_memory_packets", 0) > 0 or bool(packet),
            "has_writebacks": counts.get("rp_turn_writebacks", 0) > 0,
            "has_checkpoints": counts.get("rp_story_checkpoint_snapshots", 0) > 0,
            "has_vector_index": counts.get("rp_vector_index", 0) > 0,
        },
        "created_at": _now(),
    }
    if write_report:
        _write_json(REPORT_PATH, payload)
    return payload


def provenance_debug_dashboard_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_provenance_debug_schema()
    data = payload or {}
    limit = max(1, min(int(data.get("limit") or 8), 50))
    scope = str(data.get("scope_id") or data.get("sandbox_id") or "").strip()
    with _connect() as conn:
        matrix = _recent_debug_matrix(conn, limit=limit)
    if scope:
        for table, rows in list(matrix.items()):
            matrix[table] = [row for row in rows if scope in json.dumps(row, ensure_ascii=False)]
    graph = provenance_graph_payload(scope_id=scope, limit=160)
    scene = _read_latest_scene_execution()
    packet = _latest_packet_file()
    return {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "status": "active",
        "ready": True,
        "scope_id": scope,
        "recent": matrix,
        "graph_summary": graph.get("counts", {}),
        "latest_scene_execution": scene,
        "latest_scene_packet_file": packet,
        "diagnostics": [
            "Trace every retrieval before trusting Scene Chat output.",
            "Scene packets are the runtime contract; generation should reference an active packet id.",
            "Writebacks are candidate/runtime by default and should not auto-promote to canon.",
            "Chroma is optional; SQLite/vector rows remain the source-of-truth debug target.",
        ],
        "created_at": _now(),
    }


def provenance_debug_inspect_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    source_table = str(data.get("source_table") or "").strip()
    source_id = str(data.get("source_id") or "").strip()
    node_id = str(data.get("node_id") or "").strip()
    if node_id and ":" in node_id and not source_table:
        source_table, source_id = node_id.split(":", 1)
    direct_row: dict[str, Any] | None = None
    if source_table and source_id:
        with _connect() as conn:
            cols = _columns(conn, source_table)
            id_candidates = ["trace_id", "packet_id", "scene_packet_id", "writeback_id", "event_id", "checkpoint_id", "report_id", "fragment_id", "row_id", "source_id", "entity_id", "id"]
            for col in id_candidates:
                if col in cols:
                    try:
                        row = conn.execute(f"SELECT * FROM {source_table} WHERE {col} = ? LIMIT 1", (source_id,)).fetchone()
                    except Exception:
                        row = None
                    if row:
                        direct_row = dict(row)
                        break
    trace = provenance_trace_payload(source_table=source_table, source_id=source_id, node_id=node_id)
    return {
        "schema_id": SCHEMA_ID,
        "version": VERSION,
        "status": "found" if direct_row or trace.get("status") == "found" else "not_found",
        "source_table": source_table,
        "source_id": source_id,
        "node_id": node_id or (f"{source_table}:{source_id}" if source_table and source_id else ""),
        "direct_row": direct_row,
        "provenance_trace": trace,
        "created_at": _now(),
    }
