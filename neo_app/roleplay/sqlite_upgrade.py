from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sandbox_contract import ensure_roleplay_sandbox_schema

SQLITE_UPGRADE_SCHEMA_ID: Final[str] = "neo.roleplay.memory.sqlite_upgrade.v1"
SQLITE_UPGRADE_VERSION: Final[str] = "1.0.0-phase7-sqlite-memory-store-upgrade"
SQLITE_UPGRADE_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "sqlite_memory_store_contract.json"
SQLITE_UPGRADE_AUDIT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "sqlite_memory_store_audit.json"

PHASE7_TABLES: Final[tuple[str, ...]] = (
    "rp_schema_migrations",
    "rp_callback_anchors",
    "rp_memory_recurrence",
    "rp_memory_controls",
    "rp_memory_search_documents",
    "rp_memory_store_health",
)

# Additive columns that make existing V2 tables easier to debug, migrate, and search.
PHASE7_ADDITIVE_COLUMNS: Final[dict[str, dict[str, str]]] = {
    "rp_memory_fragments": {
        "title": "TEXT NOT NULL DEFAULT ''",
        "priority": "TEXT NOT NULL DEFAULT 'normal'",
        "salience": "REAL NOT NULL DEFAULT 0.5",
        "confidence": "REAL NOT NULL DEFAULT 0.75",
        "recurrence_key": "TEXT NOT NULL DEFAULT ''",
        "cooldown_until": "TEXT NOT NULL DEFAULT ''",
        "last_used_at": "TEXT NOT NULL DEFAULT ''",
        "use_count": "INTEGER NOT NULL DEFAULT 0",
        "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
    },
    "rp_canon_records": {
        "canon_snapshot_id": "TEXT NOT NULL DEFAULT ''",
        "confidence": "REAL NOT NULL DEFAULT 0.8",
        "provenance_json": "TEXT NOT NULL DEFAULT '{}'",
    },
    "rp_source_chunks": {
        "token_estimate": "INTEGER NOT NULL DEFAULT 0",
        "char_start": "INTEGER NOT NULL DEFAULT 0",
        "char_end": "INTEGER NOT NULL DEFAULT 0",
    },
    "rp_retrieval_traces": {
        "trace_type": "TEXT NOT NULL DEFAULT 'retrieval'",
        "sandbox_id": "TEXT NOT NULL DEFAULT ''",
        "duration_ms": "INTEGER NOT NULL DEFAULT 0",
    },
    "rp_vector_index": {
        "checksum": "TEXT NOT NULL DEFAULT ''",
        "last_verified_at": "TEXT NOT NULL DEFAULT ''",
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual') AND name=?", (table_name,)).fetchone()
    return bool(row)


def _existing_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    if not _table_exists(conn, table_name):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _record_migration(conn: sqlite3.Connection, migration_id: str, description: str, payload: dict[str, Any] | None = None) -> None:
    now = _now()
    conn.execute(
        """
        INSERT INTO rp_schema_migrations(migration_id, phase, description, status, payload_json, applied_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(migration_id) DO UPDATE SET
            status=excluded.status,
            payload_json=excluded.payload_json,
            applied_at=excluded.applied_at
        """,
        (migration_id, "phase7", description, "applied", _json(payload or {}), now),
    )


def ensure_roleplay_sqlite_upgrade_schema(conn: sqlite3.Connection | None = None, *, rebuild_search: bool = False) -> dict[str, Any]:
    """Upgrade Roleplay SQLite into the Phase 7 authoritative memory store.

    This is additive-only. It creates missing tables/indexes and adds safe helper
    columns without renaming or deleting existing V2 data.
    """
    ensure_roleplay_foundation(write_manifest=True)
    owns_connection = conn is None
    conn = conn or _connect()
    created_tables: dict[str, bool] = {}
    added_columns: dict[str, list[str]] = {}
    fts_mode = "table"
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_schema_migrations (
                migration_id TEXT PRIMARY KEY,
                phase TEXT NOT NULL DEFAULT '',
                description TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'applied',
                payload_json TEXT NOT NULL DEFAULT '{}',
                applied_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_callback_anchors (
                anchor_id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL DEFAULT '',
                source_table TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                anchor_type TEXT NOT NULL DEFAULT 'callback_anchor',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                trigger_terms_json TEXT NOT NULL DEFAULT '[]',
                recurrence_key TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'normal',
                status TEXT NOT NULL DEFAULT 'active',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_memory_recurrence (
                recurrence_id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL DEFAULT '',
                source_table TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                recurrence_key TEXT NOT NULL DEFAULT '',
                recurrence_type TEXT NOT NULL DEFAULT 'callback',
                use_count INTEGER NOT NULL DEFAULT 0,
                last_used_at TEXT NOT NULL DEFAULT '',
                cooldown_until TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_memory_controls (
                control_id TEXT PRIMARY KEY,
                sandbox_id TEXT NOT NULL DEFAULT '',
                source_table TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                control_type TEXT NOT NULL DEFAULT 'pin',
                action TEXT NOT NULL DEFAULT 'include',
                reason TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT 'normal',
                expires_at TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_memory_search_documents (
                doc_id TEXT PRIMARY KEY,
                source_table TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                sandbox_id TEXT NOT NULL DEFAULT '',
                memory_scope TEXT NOT NULL DEFAULT '',
                promotion_scope TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                tags_text TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_memory_store_health (
                check_id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'ready',
                checked_at TEXT NOT NULL DEFAULT '',
                table_counts_json TEXT NOT NULL DEFAULT '{}',
                missing_tables_json TEXT NOT NULL DEFAULT '[]',
                missing_columns_json TEXT NOT NULL DEFAULT '{}',
                payload_json TEXT NOT NULL DEFAULT '{}'
            );
            CREATE INDEX IF NOT EXISTS idx_rp_schema_migrations_phase ON rp_schema_migrations(phase, applied_at);
            CREATE INDEX IF NOT EXISTS idx_rp_callback_anchors_sandbox ON rp_callback_anchors(sandbox_id, status, priority);
            CREATE INDEX IF NOT EXISTS idx_rp_callback_anchors_source ON rp_callback_anchors(source_table, source_id);
            CREATE INDEX IF NOT EXISTS idx_rp_memory_recurrence_sandbox ON rp_memory_recurrence(sandbox_id, recurrence_key, status);
            CREATE INDEX IF NOT EXISTS idx_rp_memory_controls_sandbox ON rp_memory_controls(sandbox_id, action, status);
            CREATE INDEX IF NOT EXISTS idx_rp_memory_controls_source ON rp_memory_controls(source_table, source_id);
            CREATE INDEX IF NOT EXISTS idx_rp_memory_search_sandbox ON rp_memory_search_documents(sandbox_id, promotion_scope, memory_scope);
            CREATE INDEX IF NOT EXISTS idx_rp_memory_search_source ON rp_memory_search_documents(source_table, source_id);
            """
        )
        # Create FTS5 mirror when SQLite supports it. Keep the plain helper table
        # either way so the app can run on Python builds without FTS5.
        try:
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS rp_memory_search_fts
                USING fts5(doc_id UNINDEXED, title, content, tags_text, tokenize='porter')
                """
            )
            fts_mode = "fts5"
        except Exception:
            fts_mode = "plain_table_only"

        created_tables = {table: _table_exists(conn, table) for table in PHASE7_TABLES}
        for table, columns in PHASE7_ADDITIVE_COLUMNS.items():
            added_columns[table] = []
            if not _table_exists(conn, table):
                continue
            existing = _existing_columns(conn, table)
            for column, ddl in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    added_columns[table].append(column)

        # Helper indexes on existing core tables only. The Phase 7 endpoint can be
        # called before the full Roleplay schema exists, so every core-table index
        # must be guarded.
        guarded_indexes = {
            "rp_memory_fragments": [
                "CREATE INDEX IF NOT EXISTS idx_rp_fragments_sandbox_memory ON rp_memory_fragments(sandbox_id, memory_scope, promotion_scope, memory_type)",
                "CREATE INDEX IF NOT EXISTS idx_rp_fragments_priority ON rp_memory_fragments(priority, salience, confidence)",
                "CREATE INDEX IF NOT EXISTS idx_rp_fragments_recurrence ON rp_memory_fragments(recurrence_key, cooldown_until, last_used_at)",
            ],
            "rp_canon_records": [
                "CREATE INDEX IF NOT EXISTS idx_rp_canon_snapshot ON rp_canon_records(canon_snapshot_id, status, canon_type)",
            ],
            "rp_source_chunks": [
                "CREATE INDEX IF NOT EXISTS idx_rp_source_chunks_project_status ON rp_source_chunks(project_id, status, memory_type)",
            ],
            "rp_retrieval_traces": [
                "CREATE INDEX IF NOT EXISTS idx_rp_retrieval_trace_type ON rp_retrieval_traces(trace_type, sandbox_id, created_at)",
            ],
            "rp_vector_index": [
                "CREATE INDEX IF NOT EXISTS idx_rp_vector_checksum ON rp_vector_index(checksum, last_verified_at)",
            ],
        }
        for table_name, ddl_items in guarded_indexes.items():
            if _table_exists(conn, table_name):
                for ddl in ddl_items:
                    conn.execute(ddl)
        ensure_roleplay_sandbox_schema(conn)
        _record_migration(conn, "phase7_sqlite_memory_store_upgrade", "Create Phase 7 SQLite memory control, callback, recurrence, health, and search helper tables.", {"fts_mode": fts_mode, "created_tables": created_tables, "added_columns": added_columns})
        if rebuild_search:
            rebuild_roleplay_memory_search_documents(conn=conn)
        health = roleplay_sqlite_health_payload(conn=conn, write_report=False)
        conn.execute(
            """
            INSERT INTO rp_memory_store_health(check_id, status, checked_at, table_counts_json, missing_tables_json, missing_columns_json, payload_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(check_id) DO UPDATE SET
                status=excluded.status,
                checked_at=excluded.checked_at,
                table_counts_json=excluded.table_counts_json,
                missing_tables_json=excluded.missing_tables_json,
                missing_columns_json=excluded.missing_columns_json,
                payload_json=excluded.payload_json
            """,
            ("latest", health.get("status", "ready"), health.get("checked_at", _now()), _json(health.get("table_counts") or {}), _json(health.get("missing_tables") or []), _json(health.get("missing_columns") or {}), _json(health)),
        )
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()
    return {
        "schema_id": SQLITE_UPGRADE_SCHEMA_ID,
        "contract_version": SQLITE_UPGRADE_VERSION,
        "status": "ready" if all(created_tables.values()) else "partial",
        "checked_at": _now(),
        "created_tables": created_tables,
        "added_columns": {table: cols for table, cols in added_columns.items() if cols},
        "fts_mode": fts_mode,
        "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
    }


def _search_doc_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def add(doc_id: str, table: str, source_id: str, sandbox_id: str, memory_scope: str, promotion_scope: str, title: str, content: str, tags_text: str, payload: dict[str, Any]):
        title = str(title or source_id or doc_id).strip()
        content = str(content or "").strip()
        if not (title or content):
            return
        rows.append({
            "doc_id": doc_id,
            "source_table": table,
            "source_id": source_id,
            "sandbox_id": sandbox_id or "",
            "memory_scope": memory_scope or "",
            "promotion_scope": promotion_scope or "",
            "title": title,
            "content": content[:12000],
            "tags_text": tags_text or "",
            "payload_json": _json(payload),
        })

    for row in conn.execute("SELECT entity_id, kind, title, status, tags_json, payload_json, sandbox_id, memory_scope, promotion_scope, updated_at FROM rp_entities").fetchall():
        data = dict(row)
        add(f"rp_entities:{data.get('entity_id')}", "rp_entities", data.get("entity_id", ""), data.get("sandbox_id", ""), data.get("memory_scope", ""), data.get("promotion_scope", ""), data.get("title", ""), data.get("payload_json", ""), data.get("tags_json", ""), data)
    for row in conn.execute("SELECT fragment_id, memory_type, content, tags_json, payload_json, sandbox_id, memory_scope, promotion_scope, updated_at FROM rp_memory_fragments WHERE status NOT IN ('superseded','archived')").fetchall():
        data = dict(row)
        title = str(data.get("title") or data.get("memory_type") or data.get("fragment_id") or "") if "title" in data else str(data.get("memory_type") or data.get("fragment_id") or "")
        add(f"rp_memory_fragments:{data.get('fragment_id')}", "rp_memory_fragments", data.get("fragment_id", ""), data.get("sandbox_id", ""), data.get("memory_scope", ""), data.get("promotion_scope", ""), title, data.get("content", ""), data.get("tags_json", ""), data)
    for row in conn.execute("SELECT canon_id, canon_type, title, content, status, payload_json, sandbox_id, memory_scope, promotion_scope, updated_at FROM rp_canon_records").fetchall():
        data = dict(row)
        add(f"rp_canon_records:{data.get('canon_id')}", "rp_canon_records", data.get("canon_id", ""), data.get("sandbox_id", ""), data.get("memory_scope", ""), data.get("promotion_scope", ""), data.get("title", ""), data.get("content", ""), data.get("canon_type", ""), data)
    for row in conn.execute("SELECT chunk_id, source_id, title, content, memory_type, status, payload_json, sandbox_id, memory_scope, promotion_scope, updated_at FROM rp_source_chunks").fetchall():
        data = dict(row)
        add(f"rp_source_chunks:{data.get('chunk_id')}", "rp_source_chunks", data.get("chunk_id", ""), data.get("sandbox_id", ""), data.get("memory_scope", ""), data.get("promotion_scope", ""), data.get("title", ""), data.get("content", ""), data.get("memory_type", ""), data)
    return rows


def rebuild_roleplay_memory_search_documents(*, conn: sqlite3.Connection | None = None, limit: int | None = None) -> dict[str, Any]:
    owns_connection = conn is None
    conn = conn or _connect()
    now = _now()
    fts_used = False
    try:
        rows = _search_doc_rows(conn)
        if limit is not None:
            try:
                safe_limit = int(limit)
            except Exception:
                safe_limit = 0
            if safe_limit > 0:
                rows = rows[:safe_limit]
        conn.execute("DELETE FROM rp_memory_search_documents")
        try:
            conn.execute("DELETE FROM rp_memory_search_fts")
            fts_used = True
        except Exception:
            fts_used = False
        for row in rows:
            conn.execute(
                """
                INSERT INTO rp_memory_search_documents(doc_id, source_table, source_id, sandbox_id, memory_scope, promotion_scope, title, content, tags_text, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (row["doc_id"], row["source_table"], row["source_id"], row["sandbox_id"], row["memory_scope"], row["promotion_scope"], row["title"], row["content"], row["tags_text"], row["payload_json"], now),
            )
            if fts_used:
                try:
                    conn.execute("INSERT INTO rp_memory_search_fts(doc_id, title, content, tags_text) VALUES (?, ?, ?, ?)", (row["doc_id"], row["title"], row["content"], row["tags_text"]))
                except Exception:
                    fts_used = False
        if owns_connection:
            conn.commit()
    finally:
        if owns_connection:
            conn.close()
    return {"schema_id": SQLITE_UPGRADE_SCHEMA_ID, "status": "rebuilt", "document_count": len(rows), "fts_used": fts_used, "rebuilt_at": now}


def rebuild_roleplay_memory_fts_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    return rebuild_roleplay_memory_search_documents()


def roleplay_sqlite_health_payload(*, conn: sqlite3.Connection | None = None, write_report: bool = False) -> dict[str, Any]:
    owns_connection = conn is None
    conn = conn or _connect()
    required_tables = (
        "rp_entities", "rp_entity_versions", "rp_edges", "rp_memory_fragments", "rp_shared_memories",
        "rp_source_documents", "rp_source_chunks", "rp_canon_records", "rp_relationship_state",
        "rp_character_states", "rp_character_knowledge", "rp_callback_anchors", "rp_memory_recurrence",
        "rp_memory_controls", "rp_memory_search_documents", "rp_retrieval_traces", "rp_scene_memory_packets",
        "rp_continuity_rows", "rp_turn_summaries", "rp_story_checkpoints", "rp_vector_index",
    )
    missing_tables: list[str] = []
    table_counts: dict[str, int] = {}
    missing_columns: dict[str, list[str]] = {}
    try:
        for table in required_tables:
            if not _table_exists(conn, table):
                missing_tables.append(table)
                table_counts[table] = 0
                continue
            table_counts[table] = _count_table(conn, table)
        for table, columns in PHASE7_ADDITIVE_COLUMNS.items():
            if not _table_exists(conn, table):
                continue
            existing = _existing_columns(conn, table)
            miss = [column for column in columns if column not in existing]
            if miss:
                missing_columns[table] = miss
        status = "ready" if not missing_tables and not missing_columns else "partial"
        payload = {
            "schema_id": SQLITE_UPGRADE_SCHEMA_ID,
            "contract_version": SQLITE_UPGRADE_VERSION,
            "status": status,
            "checked_at": _now(),
            "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
            "table_counts": table_counts,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "source_of_truth": "SQLite",
            "mirror_policy": "Chroma/vector stores may mirror; they must not become authoritative.",
        }
        if write_report:
            SQLITE_UPGRADE_AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
            SQLITE_UPGRADE_AUDIT_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return payload
    finally:
        if owns_connection:
            conn.close()


def roleplay_sqlite_upgrade_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    state = ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    health = roleplay_sqlite_health_payload(write_report=write_report)
    contract = {
        "schema_id": SQLITE_UPGRADE_SCHEMA_ID,
        "contract_version": SQLITE_UPGRADE_VERSION,
        "phase": "Phase 7 — SQLite Memory Store Upgrade",
        "generated_at": _now(),
        "authoritative_store": "SQLite roleplay.sqlite remains the source of truth for Roleplay and Novel memory.",
        "migration_policy": "additive_only_no_table_renames_no_destructive_migrations",
        "new_tables": list(PHASE7_TABLES),
        "new_controls": ["callback anchors", "memory recurrence", "pin/suppress/cooldown controls", "search helper documents", "schema migrations", "health report"],
        "existing_table_upgrades": PHASE7_ADDITIVE_COLUMNS,
        "indexing_policy": {
            "search_documents": "Flatten important memory rows into rp_memory_search_documents for stable keyword/FTS retrieval.",
            "fts5": "Use rp_memory_search_fts when available; fall back to plain table search otherwise.",
            "vectors": "rp_vector_index remains local vector cache; Chroma is optional mirror in later phase.",
        },
        "state": state,
        "health": health,
        "paths": {
            "contract_path": _relative_to_root(SQLITE_UPGRADE_CONTRACT_PATH),
            "audit_path": _relative_to_root(SQLITE_UPGRADE_AUDIT_PATH),
            "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        },
        "next_required_phase": "Phase 8 — Embedding + Reranker Adapter",
    }
    if write_report:
        SQLITE_UPGRADE_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        SQLITE_UPGRADE_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract
