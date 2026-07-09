from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PHASE_ID = "M10"
OBSERVABILITY_SCHEMA_ID = "neo.memory.observability.m10"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_load(value: Any, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return fallback


def _short(text: Any, limit: int = 220) -> str:
    clean = " ".join(str(text or "").split())
    if len(clean) <= limit:
        return clean
    return clean[: max(0, limit - 1)].rstrip() + "…"


class MemoryObservabilityEngine:
    """Read-only observability layer for Neo Memory + Control Center.

    M10 intentionally does not mutate memory. It exposes inspector payloads that
    make the current memory/control pipeline visible before deeper automation.
    """

    def __init__(self, db_path: Path, *, root_dir: Path | None = None) -> None:
        self.db_path = Path(db_path)
        self.root_dir = root_dir or Path(__file__).resolve().parents[2]

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
        try:
            return conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (table,)).fetchone() is not None
        except sqlite3.Error:
            return False

    @staticmethod
    def _count(conn: sqlite3.Connection, table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
        if not MemoryObservabilityEngine._table_exists(conn, table):
            return 0
        try:
            row = conn.execute(f"SELECT COUNT(*) AS count FROM {table} {where}", params).fetchone()
            return int(row["count"] if row else 0)
        except sqlite3.Error:
            return 0

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            tables = {
                table: self._table_exists(conn, table)
                for table in [
                    "neo_memory_events",
                    "neo_memory_fragments",
                    "neo_memory_summaries",
                    "neo_memory_embeddings",
                    "neo_memory_access_log",
                    "neo_memory_jobs",
                    "neo_memory_conflicts",
                    "neo_control_center_traces",
                ]
            }
            counts = {table: self._count(conn, table) for table in tables}
            return {
                "ok": True,
                "schema_id": OBSERVABILITY_SCHEMA_ID,
                "phase": PHASE_ID,
                "status": "ready" if all(tables.values()) else "partial",
                "db_path": str(self.db_path),
                "tables": tables,
                "counts": counts,
                "panels": [
                    "memory_inspector",
                    "retrieval_inspector",
                    "control_center_inspector",
                    "roleplay_scene_inspector",
                    "prompt_contract_inspector",
                    "timeline_inspector",
                ],
                "policy": "Read-only observability; no memory mutation in M10.",
                "generated_at": _now(),
            }

    def snapshot(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        surface = payload.get("surface") or None
        project_id = payload.get("project_id") or None
        scope_id = payload.get("scope_id") or None
        limit = max(1, min(int(payload.get("limit") or 25), 100))
        filters = {"surface": surface, "project_id": project_id, "scope_id": scope_id, "limit": limit}
        with self._connect() as conn:
            return {
                "ok": True,
                "schema_id": OBSERVABILITY_SCHEMA_ID,
                "phase": PHASE_ID,
                "status": "ready",
                "generated_at": _now(),
                "filters": filters,
                "summary": self._summary(conn, surface=surface, project_id=project_id, scope_id=scope_id),
                "memory_inspector": self._memory_panel(conn, surface=surface, project_id=project_id, scope_id=scope_id, limit=limit),
                "retrieval_inspector": self._retrieval_panel(conn, surface=surface, project_id=project_id, scope_id=scope_id, limit=limit),
                "control_center_inspector": self._control_center_panel(conn, surface=surface, project_id=project_id, scope_id=scope_id, limit=limit),
                "roleplay_scene_inspector": self._roleplay_panel(limit=limit),
                "prompt_contract_inspector": self._prompt_contract_panel(),
                "timeline_inspector": self._timeline_panel(conn, surface=surface, project_id=project_id, scope_id=scope_id, limit=limit),
            }

    def inspect_memory(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self._connect() as conn:
            return {"ok": True, "panel": "memory_inspector", **self._memory_panel(conn, surface=payload.get("surface"), project_id=payload.get("project_id"), scope_id=payload.get("scope_id"), limit=max(1, min(int(payload.get("limit") or 25), 100)))}

    def inspect_retrieval(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self._connect() as conn:
            return {"ok": True, "panel": "retrieval_inspector", **self._retrieval_panel(conn, surface=payload.get("surface"), project_id=payload.get("project_id"), scope_id=payload.get("scope_id"), limit=max(1, min(int(payload.get("limit") or 25), 100)))}

    def inspect_control_center(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        with self._connect() as conn:
            return {"ok": True, "panel": "control_center_inspector", **self._control_center_panel(conn, surface=payload.get("surface"), project_id=payload.get("project_id"), scope_id=payload.get("scope_id"), limit=max(1, min(int(payload.get("limit") or 25), 100)))}

    def inspect_roleplay_scene(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = payload or {}
        return {"ok": True, "panel": "roleplay_scene_inspector", **self._roleplay_panel(limit=max(1, min(int(payload.get("limit") or 25), 100)))}

    def _scope_where(self, surface: str | None, project_id: str | None, scope_id: str | None, *, table_alias: str = "") -> tuple[str, list[Any]]:
        prefix = f"{table_alias}." if table_alias else ""
        clauses: list[str] = []
        params: list[Any] = []
        if surface:
            clauses.append(f"{prefix}surface=?")
            params.append(surface)
        if project_id:
            clauses.append(f"{prefix}project_id=?")
            params.append(project_id)
        if scope_id:
            clauses.append(f"{prefix}scope_id=?")
            params.append(scope_id)
        return (" WHERE " + " AND ".join(clauses)) if clauses else "", params

    def _summary(self, conn: sqlite3.Connection, *, surface: str | None, project_id: str | None, scope_id: str | None) -> dict[str, Any]:
        where, params = self._scope_where(surface, project_id, scope_id)
        counts = {
            "events": self._count(conn, "neo_memory_events", where, tuple(params)),
            "objects": self._count(conn, "neo_memory_objects", where, tuple(params)),
            "facts": self._count(conn, "neo_memory_facts", where, tuple(params)),
            "edges": self._count(conn, "neo_memory_edges", where, tuple(params)),
            "fragments": self._count(conn, "neo_memory_fragments", where, tuple(params)),
            "summaries": self._count(conn, "neo_memory_summaries", where, tuple(params)),
            "embeddings": self._count(conn, "neo_memory_embeddings", where, tuple(params)),
            "jobs": self._count(conn, "neo_memory_jobs", where, tuple(params)),
            "conflicts": self._count(conn, "neo_memory_conflicts", where, tuple(params)),
            "control_traces": self._count(conn, "neo_control_center_traces", where, tuple(params)),
        }
        return {"counts": counts, "readiness": "ready" if counts["fragments"] or counts["events"] else "empty", "scope": {"surface": surface, "project_id": project_id, "scope_id": scope_id}}

    def _memory_panel(self, conn: sqlite3.Connection, *, surface: str | None, project_id: str | None, scope_id: str | None, limit: int) -> dict[str, Any]:
        where, params = self._scope_where(surface, project_id, scope_id)
        panels: dict[str, Any] = {"status": "ready", "recent_fragments": [], "recent_summaries": [], "fact_samples": [], "embedding_status": []}
        if self._table_exists(conn, "neo_memory_fragments"):
            rows = conn.execute(f"SELECT fragment_id, surface, project_id, scope_id, memory_type, title, summary, content, priority, confidence, trust_level, status, embedding_status, updated_at FROM neo_memory_fragments{where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
            panels["recent_fragments"] = [dict(row) | {"content_preview": _short(row["content"], 260)} for row in rows]
            try:
                panels["embedding_status"] = [dict(row) for row in conn.execute(f"SELECT embedding_status AS status, COUNT(*) AS count FROM neo_memory_fragments{where} GROUP BY embedding_status ORDER BY count DESC", tuple(params)).fetchall()]
            except sqlite3.Error:
                pass
        if self._table_exists(conn, "neo_memory_summaries"):
            rows = conn.execute(f"SELECT summary_id, surface, project_id, scope_id, summary_type, title, content, confidence, status, updated_at FROM neo_memory_summaries{where} ORDER BY updated_at DESC LIMIT ?", (*params, limit)).fetchall()
            panels["recent_summaries"] = [dict(row) | {"content_preview": _short(row["content"], 260)} for row in rows]
        if self._table_exists(conn, "neo_memory_facts"):
            rows = conn.execute(f"SELECT fact_id, surface, project_id, scope_id, fact_type, statement, confidence, trust_level, status, updated_at FROM neo_memory_facts{where} ORDER BY updated_at DESC LIMIT ?", (*params, min(limit, 12))).fetchall()
            panels["fact_samples"] = [dict(row) for row in rows]
        return panels

    def _retrieval_panel(self, conn: sqlite3.Connection, *, surface: str | None, project_id: str | None, scope_id: str | None, limit: int) -> dict[str, Any]:
        panel: dict[str, Any] = {"status": "ready", "recent_access": [], "recent_legacy_traces": [], "jobs": []}
        where, params = self._scope_where(surface, project_id, scope_id)
        if self._table_exists(conn, "neo_memory_access_log"):
            rows = conn.execute(f"SELECT access_id, consumer, surface, project_id, scope_id, query, result_ids_json, created_at, metadata_json FROM neo_memory_access_log{where} ORDER BY created_at DESC LIMIT ?", (*params, limit)).fetchall()
            panel["recent_access"] = [dict(row) | {"result_ids": _json_load(row["result_ids_json"], []), "metadata": _json_load(row["metadata_json"], {})} for row in rows]
        if self._table_exists(conn, "memory_retrieval_traces"):
            legacy_where = ""
            legacy_params: list[Any] = []
            if surface:
                # Legacy traces do not always have a surface column, so filter through metadata later.
                pass
            rows = conn.execute(f"SELECT trace_id, query, profile, consumer, sources_json, results_json, metadata_json, created_at FROM memory_retrieval_traces{legacy_where} ORDER BY created_at DESC LIMIT ?", (*legacy_params, limit)).fetchall()
            traces = []
            for row in rows:
                metadata = _json_load(row["metadata_json"], {}) or {}
                if surface and metadata.get("surface") and metadata.get("surface") != surface:
                    continue
                results = _json_load(row["results_json"], []) or []
                traces.append({"trace_id": row["trace_id"], "query": row["query"], "profile": row["profile"], "consumer": row["consumer"], "sources": _json_load(row["sources_json"], []), "result_count": len(results), "metadata": metadata, "created_at": row["created_at"]})
            panel["recent_legacy_traces"] = traces[:limit]
        if self._table_exists(conn, "neo_memory_jobs"):
            job_where, job_params = self._scope_where(surface, project_id, scope_id)
            rows = conn.execute(f"SELECT job_id, job_type, status, surface, project_id, scope_id, started_at, finished_at, error, updated_at, progress_json, result_json FROM neo_memory_jobs{job_where} ORDER BY updated_at DESC LIMIT ?", (*job_params, min(limit, 12))).fetchall()
            panel["jobs"] = [dict(row) | {"progress": _json_load(row["progress_json"], {}), "result": _json_load(row["result_json"], {})} for row in rows]
        return panel

    def _control_center_panel(self, conn: sqlite3.Connection, *, surface: str | None, project_id: str | None, scope_id: str | None, limit: int) -> dict[str, Any]:
        panel: dict[str, Any] = {"status": "ready", "recent_traces": [], "controllers": []}
        where, params = self._scope_where(surface, project_id, scope_id)
        if self._table_exists(conn, "neo_control_center_traces"):
            rows = conn.execute(f"SELECT trace_id, controller, surface, project_id, scope_id, intent, user_input, memory_sources_json, selected_context_json, prompt_contract_id, backend_profile_id, validation_json, writeback_plan_json, status, created_at, metadata_json FROM neo_control_center_traces{where} ORDER BY created_at DESC LIMIT ?", (*params, limit)).fetchall()
            panel["recent_traces"] = [dict(row) | {
                "user_input_preview": _short(row["user_input"], 180),
                "memory_sources": _json_load(row["memory_sources_json"], []),
                "selected_context": _json_load(row["selected_context_json"], {}),
                "validation": _json_load(row["validation_json"], {}),
                "writeback_plan": _json_load(row["writeback_plan_json"], {}),
                "metadata": _json_load(row["metadata_json"], {}),
            } for row in rows]
            try:
                panel["controllers"] = [dict(row) for row in conn.execute(f"SELECT controller, surface, COUNT(*) AS count, MAX(created_at) AS last_trace_at FROM neo_control_center_traces{where} GROUP BY controller, surface ORDER BY count DESC", tuple(params)).fetchall()]
            except sqlite3.Error:
                pass
        return panel

    def _roleplay_panel(self, *, limit: int) -> dict[str, Any]:
        roleplay_db = self.root_dir / "neo_data" / "roleplay" / "roleplay.sqlite"
        panel: dict[str, Any] = {"status": "unavailable", "db_path": str(roleplay_db), "scene_packets": [], "character_states": [], "relationship_states": [], "unresolved_threads": [], "fragment_counts": []}
        if not roleplay_db.exists():
            panel["error"] = "roleplay.sqlite not found"
            return panel
        try:
            conn = sqlite3.connect(roleplay_db)
            conn.row_factory = sqlite3.Row
            with conn:
                panel["status"] = "ready"
                for table, key in [
                    ("rp_scene_memory_packets", "scene_packets"),
                    ("rp_character_states", "character_states"),
                    ("rp_relationship_state", "relationship_states"),
                    ("rp_unresolved_threads", "unresolved_threads"),
                ]:
                    if self._table_exists(conn, table):
                        cols = [row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()]
                        order_col = "updated_at" if "updated_at" in cols else ("created_at" if "created_at" in cols else cols[0])
                        rows = conn.execute(f"SELECT * FROM {table} ORDER BY {order_col} DESC LIMIT ?", (min(limit, 12),)).fetchall()
                        panel[key] = [self._clean_roleplay_row(dict(row)) for row in rows]
                if self._table_exists(conn, "rp_memory_fragments"):
                    cols = [row[1] for row in conn.execute("PRAGMA table_info(rp_memory_fragments)").fetchall()]
                    group_col = "memory_type" if "memory_type" in cols else ("fragment_type" if "fragment_type" in cols else None)
                    if group_col:
                        panel["fragment_counts"] = [dict(row) for row in conn.execute(f"SELECT {group_col} AS fragment_type, COUNT(*) AS count FROM rp_memory_fragments GROUP BY {group_col} ORDER BY count DESC LIMIT 20").fetchall()]
                    else:
                        panel["fragment_counts"] = [{"fragment_type": "all", "count": self._count(conn, "rp_memory_fragments")}]
        except Exception as exc:
            panel["status"] = "error"
            panel["error"] = str(exc)[:400]
        finally:
            try:
                conn.close()  # type: ignore[name-defined]
            except Exception:
                pass
        return panel

    def _clean_roleplay_row(self, row: dict[str, Any]) -> dict[str, Any]:
        cleaned: dict[str, Any] = {}
        for key, value in row.items():
            if isinstance(value, str) and (key.endswith("_json") or value.startswith("{") or value.startswith("[")):
                loaded = _json_load(value, value)
                cleaned[key[:-5] if key.endswith("_json") else key] = loaded
            elif isinstance(value, str) and len(value) > 360:
                cleaned[key] = _short(value, 360)
            else:
                cleaned[key] = value
        return cleaned

    def _prompt_contract_panel(self) -> dict[str, Any]:
        try:
            from neo_app.control_center.prompt_contracts import list_prompt_contracts, prompt_contract_status_payload
            contracts = list_prompt_contracts()
            status = prompt_contract_status_payload()
            return {"status": "ready", "summary": status, "contracts": [{"contract_id": item.get("contract_id"), "surface": item.get("surface"), "intent": item.get("intent"), "version": item.get("version"), "rules": item.get("rules", [])[:6], "output_format": item.get("output_format", "")} for item in contracts]}
        except Exception as exc:
            return {"status": "error", "error": str(exc)[:400], "contracts": []}

    def _timeline_panel(self, conn: sqlite3.Connection, *, surface: str | None, project_id: str | None, scope_id: str | None, limit: int) -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        where, params = self._scope_where(surface, project_id, scope_id)
        sources = [
            ("neo_memory_events", "created_at", "memory_event", "title", "summary"),
            ("neo_memory_fragments", "updated_at", "memory_fragment", "title", "summary"),
            ("neo_memory_summaries", "updated_at", "memory_summary", "title", "content"),
            ("neo_control_center_traces", "created_at", "control_trace", "intent", "user_input"),
            ("neo_memory_jobs", "updated_at", "memory_job", "job_type", "status"),
        ]
        for table, time_col, kind, title_col, text_col in sources:
            if not self._table_exists(conn, table):
                continue
            try:
                rows = conn.execute(f"SELECT *, {time_col} AS observed_at FROM {table}{where} ORDER BY {time_col} DESC LIMIT ?", (*params, min(limit, 12))).fetchall()
                for row in rows:
                    data = dict(row)
                    items.append({"kind": kind, "observed_at": data.get("observed_at"), "surface": data.get("surface"), "project_id": data.get("project_id"), "scope_id": data.get("scope_id"), "title": _short(data.get(title_col), 120), "preview": _short(data.get(text_col), 220)})
            except sqlite3.Error:
                continue
        items.sort(key=lambda item: item.get("observed_at") or "", reverse=True)
        return {"status": "ready", "items": items[:limit]}
