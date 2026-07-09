from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any

UNIFIED_MEMORY_SCHEMA_ID = "neo.memory.unified_schema.v1"
UNIFIED_MEMORY_SCHEMA_VERSION = "0.1.0-phase-m2"

UNIFIED_MEMORY_TABLES: tuple[str, ...] = (
    "neo_memory_schema_meta",
    "neo_memory_projects",
    "neo_memory_scopes",
    "neo_memory_events",
    "neo_memory_objects",
    "neo_memory_facts",
    "neo_memory_edges",
    "neo_memory_fragments",
    "neo_memory_summaries",
    "neo_memory_embeddings",
    "neo_memory_access_log",
    "neo_memory_conflicts",
    "neo_memory_jobs",
    "neo_control_center_traces",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    return row is not None


def _column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return False
    return any(str(row[1]) == column for row in rows)


def _safe_add_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if _table_exists(conn, table) and not _column_exists(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def ensure_unified_memory_schema(conn: sqlite3.Connection) -> dict[str, Any]:
    """Create Neo's canonical Phase M2 memory schema.

    This is additive and safe to run repeatedly. It does not migrate or delete any
    existing legacy Memory Engine tables. SQLite is the source of truth; Chroma and
    other vector stores remain optional mirrors fed from neo_memory_embeddings refs.
    """
    created_before = {table: _table_exists(conn, table) for table in UNIFIED_MEMORY_TABLES}
    stamp = _now()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_schema_meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_projects (
            project_id TEXT PRIMARY KEY,
            label TEXT NOT NULL,
            surface TEXT NOT NULL DEFAULT 'global',
            project_type TEXT NOT NULL DEFAULT 'workspace',
            status TEXT NOT NULL DEFAULT 'active',
            description TEXT NOT NULL DEFAULT '',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_projects_surface ON neo_memory_projects(surface, status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_scopes (
            scope_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_type TEXT NOT NULL DEFAULT 'global',
            scope_key TEXT NOT NULL DEFAULT 'global',
            parent_scope_id TEXT,
            label TEXT NOT NULL DEFAULT '',
            path_json TEXT NOT NULL DEFAULT '[]',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(surface, project_id, scope_type, scope_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_scopes_lookup ON neo_memory_scopes(surface, project_id, scope_type, scope_key)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_scopes_parent ON neo_memory_scopes(parent_scope_id)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_events (
            memory_event_id TEXT PRIMARY KEY,
            source_event_id TEXT,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            source_type TEXT NOT NULL DEFAULT 'system',
            source_id TEXT,
            event_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            importance TEXT NOT NULL DEFAULT 'normal',
            confidence REAL NOT NULL DEFAULT 1.0,
            trust_level TEXT NOT NULL DEFAULT 'confirmed',
            retention_state TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_events_scope ON neo_memory_events(surface, project_id, scope_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_events_type ON neo_memory_events(event_type, source_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_events_hash ON neo_memory_events(content_hash)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_objects (
            object_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            object_type TEXT NOT NULL,
            object_key TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL DEFAULT '',
            attributes_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            confidence REAL NOT NULL DEFAULT 1.0,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(surface, project_id, object_type, object_key)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_objects_scope ON neo_memory_objects(surface, project_id, scope_id, object_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_objects_key ON neo_memory_objects(object_type, object_key)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_facts (
            fact_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            subject_id TEXT,
            predicate TEXT NOT NULL DEFAULT '',
            object_value TEXT NOT NULL DEFAULT '',
            object_id TEXT,
            fact_type TEXT NOT NULL DEFAULT 'observation',
            statement TEXT NOT NULL,
            source_event_id TEXT,
            confidence REAL NOT NULL DEFAULT 0.75,
            trust_level TEXT NOT NULL DEFAULT 'inferred',
            status TEXT NOT NULL DEFAULT 'active',
            valid_from TEXT,
            valid_to TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT ''
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_facts_scope ON neo_memory_facts(surface, project_id, scope_id, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_facts_subject ON neo_memory_facts(subject_id, predicate)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_facts_hash ON neo_memory_facts(content_hash)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_edges (
            edge_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            source_object_id TEXT NOT NULL,
            target_object_id TEXT NOT NULL,
            edge_type TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            weight REAL NOT NULL DEFAULT 1.0,
            confidence REAL NOT NULL DEFAULT 0.75,
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(surface, project_id, scope_id, source_object_id, target_object_id, edge_type)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_edges_source ON neo_memory_edges(source_object_id, edge_type)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_edges_target ON neo_memory_edges(target_object_id, edge_type)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_fragments (
            fragment_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            source_type TEXT NOT NULL DEFAULT 'memory',
            source_id TEXT,
            memory_type TEXT NOT NULL DEFAULT 'fragment',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            summary TEXT NOT NULL DEFAULT '',
            token_estimate INTEGER NOT NULL DEFAULT 0,
            priority REAL NOT NULL DEFAULT 0.5,
            confidence REAL NOT NULL DEFAULT 0.75,
            trust_level TEXT NOT NULL DEFAULT 'inferred',
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            content_hash TEXT NOT NULL DEFAULT '',
            embedding_status TEXT NOT NULL DEFAULT 'queued'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_fragments_scope ON neo_memory_fragments(surface, project_id, scope_id, memory_type, status)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_fragments_source ON neo_memory_fragments(source_type, source_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_fragments_embed ON neo_memory_fragments(embedding_status, updated_at)")
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS neo_memory_fragments_fts USING fts5(
                fragment_id UNINDEXED,
                surface UNINDEXED,
                project_id UNINDEXED,
                scope_id UNINDEXED,
                title,
                content,
                summary
            )
            """
        )
    except sqlite3.OperationalError:
        pass

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_summaries (
            summary_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            summary_type TEXT NOT NULL DEFAULT 'rolling',
            title TEXT NOT NULL DEFAULT '',
            content TEXT NOT NULL,
            covers_json TEXT NOT NULL DEFAULT '[]',
            source_ids_json TEXT NOT NULL DEFAULT '[]',
            confidence REAL NOT NULL DEFAULT 0.75,
            status TEXT NOT NULL DEFAULT 'active',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_summaries_scope ON neo_memory_summaries(surface, project_id, scope_id, summary_type, status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_embeddings (
            embedding_id TEXT PRIMARY KEY,
            fragment_id TEXT NOT NULL,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            model_id TEXT NOT NULL DEFAULT 'unknown',
            dimension INTEGER NOT NULL DEFAULT 0,
            vector_store TEXT NOT NULL DEFAULT 'sqlite',
            collection_name TEXT NOT NULL DEFAULT '',
            vector_ref TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_embeddings_fragment ON neo_memory_embeddings(fragment_id, model_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_embeddings_status ON neo_memory_embeddings(status, vector_store)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_access_log (
            access_id TEXT PRIMARY KEY,
            consumer TEXT NOT NULL,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            query TEXT NOT NULL DEFAULT '',
            result_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_access_consumer ON neo_memory_access_log(consumer, surface, project_id, created_at)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_conflicts (
            conflict_id TEXT PRIMARY KEY,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            conflict_type TEXT NOT NULL DEFAULT 'fact_conflict',
            status TEXT NOT NULL DEFAULT 'open',
            subject_id TEXT,
            candidate_ids_json TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT '',
            resolution_json TEXT NOT NULL DEFAULT '{}',
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_conflicts_scope ON neo_memory_conflicts(surface, project_id, scope_id, status)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_memory_jobs (
            job_id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'queued',
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            started_at TEXT,
            finished_at TEXT,
            progress_json TEXT NOT NULL DEFAULT '{}',
            result_json TEXT NOT NULL DEFAULT '{}',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_jobs_status ON neo_memory_jobs(job_type, status, created_at)")

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS neo_control_center_traces (
            trace_id TEXT PRIMARY KEY,
            controller TEXT NOT NULL,
            surface TEXT NOT NULL DEFAULT 'global',
            project_id TEXT,
            scope_id TEXT,
            intent TEXT NOT NULL DEFAULT '',
            user_input TEXT NOT NULL DEFAULT '',
            memory_sources_json TEXT NOT NULL DEFAULT '[]',
            selected_context_json TEXT NOT NULL DEFAULT '{}',
            prompt_contract_id TEXT NOT NULL DEFAULT '',
            backend_profile_id TEXT NOT NULL DEFAULT '',
            validation_json TEXT NOT NULL DEFAULT '{}',
            writeback_plan_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'recorded',
            created_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_control_center_scope ON neo_control_center_traces(controller, surface, project_id, scope_id, created_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_control_center_status ON neo_control_center_traces(status, created_at)")

    # Forward-compatible additive columns for early M2 refinements.
    _safe_add_column(conn, "neo_memory_projects", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
    _safe_add_column(conn, "neo_memory_scopes", "metadata_json", "TEXT NOT NULL DEFAULT '{}'")
    _safe_add_column(conn, "neo_memory_fragments", "embedding_status", "TEXT NOT NULL DEFAULT 'queued'")

    meta = {
        "schema_id": UNIFIED_MEMORY_SCHEMA_ID,
        "schema_version": UNIFIED_MEMORY_SCHEMA_VERSION,
        "applied_at": stamp,
        "policy": "SQLite is authoritative; vector stores are optional mirrors; JSON is import/export/snapshot metadata.",
        "phase": "M2",
    }
    for key, value in meta.items():
        conn.execute(
            "INSERT OR REPLACE INTO neo_memory_schema_meta (key, value, updated_at) VALUES (?, ?, ?)",
            (key, json.dumps(value) if isinstance(value, (dict, list)) else str(value), stamp),
        )

    # Seed global project/scope anchors so later surface ingestion can route into
    # stable sandboxes without inventing ids.
    conn.execute(
        """
        INSERT OR IGNORE INTO neo_memory_projects (project_id, label, surface, project_type, status, description, metadata_json, created_at, updated_at)
        VALUES ('global', 'Global Memory', 'global', 'system', 'active', 'Root sandbox for Neo-wide memory routing.', '{}', ?, ?)
        """,
        (stamp, stamp),
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO neo_memory_scopes (scope_id, surface, project_id, scope_type, scope_key, parent_scope_id, label, path_json, metadata_json, created_at, updated_at)
        VALUES ('global:root', 'global', 'global', 'root', 'global', NULL, 'Global Root', '[]', '{}', ?, ?)
        """,
        (stamp, stamp),
    )

    counts = unified_memory_table_counts(conn)
    created_after = {table: _table_exists(conn, table) for table in UNIFIED_MEMORY_TABLES}
    return {
        "schema_id": UNIFIED_MEMORY_SCHEMA_ID,
        "schema_version": UNIFIED_MEMORY_SCHEMA_VERSION,
        "status": "ready" if all(created_after.values()) else "partial",
        "applied_at": stamp,
        "tables": [
            {"table": table, "existed_before": created_before.get(table, False), "exists": created_after.get(table, False), "row_count": counts.get(table)}
            for table in UNIFIED_MEMORY_TABLES
        ],
        "fts_available": _table_exists(conn, "neo_memory_fragments_fts"),
        "policy": meta["policy"],
    }


def unified_memory_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in UNIFIED_MEMORY_TABLES:
        if not _table_exists(conn, table):
            counts[table] = 0
            continue
        try:
            counts[table] = int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            counts[table] = 0
    return counts


def unified_memory_schema_status(conn: sqlite3.Connection) -> dict[str, Any]:
    existing = {table: _table_exists(conn, table) for table in UNIFIED_MEMORY_TABLES}
    counts = unified_memory_table_counts(conn)
    meta_rows = []
    if existing.get("neo_memory_schema_meta"):
        meta_rows = conn.execute("SELECT key, value, updated_at FROM neo_memory_schema_meta ORDER BY key").fetchall()
    meta: dict[str, Any] = {}
    updated_at = None
    for row in meta_rows:
        value = row[1]
        try:
            value = json.loads(value)
        except Exception:
            pass
        meta[str(row[0])] = value
        updated_at = row[2]
    missing = [table for table, ok in existing.items() if not ok]
    return {
        "schema_id": UNIFIED_MEMORY_SCHEMA_ID,
        "schema_version": meta.get("schema_version") or UNIFIED_MEMORY_SCHEMA_VERSION,
        "status": "ready" if not missing else "missing_tables",
        "missing_tables": missing,
        "table_counts": counts,
        "fts_available": _table_exists(conn, "neo_memory_fragments_fts"),
        "updated_at": updated_at,
        "meta": meta,
    }
