from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from neo_app.roleplay.storage import ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.sandbox_contract import (
    apply_context_to_payload,
    context_json,
    context_scope_id,
    derive_sandbox_context,
    ensure_roleplay_sandbox_schema,
)

SCHEMA_ID = "neo.roleplay.memory.sqlite.v1"
SCHEMA_VERSION = "1.4.0-phase7-sqlite-memory-store-upgrade"

ROLEPLAY_MEMORY_TABLES: tuple[str, ...] = (
    "rp_entities",
    "rp_entity_versions",
    "rp_edges",
    "rp_memory_fragments",
    "rp_shared_memories",
    "rp_callback_anchors",
    "rp_memory_recurrence",
    "rp_memory_controls",
    "rp_memory_search_documents",
    "rp_memory_store_health",
    "rp_source_documents",
    "rp_source_chunks",
    "rp_canon_records",
    "rp_relationship_state",
    "rp_character_states",
    "rp_character_knowledge",
    "rp_unresolved_threads",
    "rp_scene_memory_packets",
    "rp_continuity_rows",
    "rp_retrieval_traces",
    "rp_turn_writebacks",
    "rp_continuity_events",
    "rp_turn_summaries",
    "rp_story_checkpoints",
    "rp_vector_index",
    "rp_contradiction_reports",
    "rp_checkpoint_branches",
)


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


def ensure_roleplay_memory_schema() -> dict[str, Any]:
    """Create the Roleplay-specific SQLite backbone.

    This is the Roleplay memory backbone: tables, indexes, table counts,
    JSON payload storage, retrieval traces, and the local vector-index table.
    """

    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_entities (
                entity_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                scope_id TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                source_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_entity_versions (
                version_id TEXT PRIMARY KEY,
                entity_id TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_edges (
                edge_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                relation_type TEXT NOT NULL DEFAULT 'related',
                source_kind TEXT NOT NULL DEFAULT '',
                target_kind TEXT NOT NULL DEFAULT '',
                weight REAL NOT NULL DEFAULT 1.0,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_memory_fragments (
                fragment_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'roleplay.memory',
                source_type TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                memory_type TEXT NOT NULL DEFAULT 'semantic_fact',
                status TEXT NOT NULL DEFAULT 'foundation_stub',
                content TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                vector_status TEXT NOT NULL DEFAULT 'not_indexed',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_shared_memories (
                memory_id TEXT PRIMARY KEY,
                namespace TEXT NOT NULL DEFAULT 'roleplay.shared',
                scope_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_source_documents (
                source_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'text',
                status TEXT NOT NULL DEFAULT 'draft',
                body_preview TEXT NOT NULL DEFAULT '',
                storage_path TEXT NOT NULL DEFAULT '',
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_source_chunks (
                chunk_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                chunk_index INTEGER NOT NULL DEFAULT 0,
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                memory_type TEXT NOT NULL DEFAULT 'canon_fact',
                status TEXT NOT NULL DEFAULT 'draft_breakdown',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_canon_records (
                canon_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                canon_type TEXT NOT NULL DEFAULT 'canon_fact',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'candidate_canon',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_source_documents_project ON rp_source_documents(project_id);
            CREATE INDEX IF NOT EXISTS idx_rp_source_chunks_source ON rp_source_chunks(source_id, chunk_index);
            CREATE INDEX IF NOT EXISTS idx_rp_canon_records_source ON rp_canon_records(source_id, canon_type);
            CREATE TABLE IF NOT EXISTS rp_relationship_state (
                state_id TEXT PRIMARY KEY,
                character_a_id TEXT NOT NULL DEFAULT '',
                character_b_id TEXT NOT NULL DEFAULT '',
                relationship_type TEXT NOT NULL DEFAULT '',
                state_label TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_character_states (
                state_id TEXT PRIMARY KEY,
                character_id TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                current_emotion TEXT NOT NULL DEFAULT '',
                emotional_vector_json TEXT NOT NULL DEFAULT '{}',
                goals_json TEXT NOT NULL DEFAULT '[]',
                boundaries_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                trust_level TEXT NOT NULL DEFAULT 'inferred',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_character_knowledge (
                knowledge_id TEXT PRIMARY KEY,
                character_id TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                subject_id TEXT NOT NULL DEFAULT '',
                knowledge_type TEXT NOT NULL DEFAULT 'known_fact',
                content TEXT NOT NULL DEFAULT '',
                visibility TEXT NOT NULL DEFAULT 'character_only',
                canon_status TEXT NOT NULL DEFAULT 'draft',
                payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_unresolved_threads (
                thread_id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL DEFAULT '',
                scene_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                thread_type TEXT NOT NULL DEFAULT 'open_hook',
                status TEXT NOT NULL DEFAULT 'open',
                priority TEXT NOT NULL DEFAULT 'normal',
                content TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_scene_memory_packets (
                packet_id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                emotional_tone TEXT NOT NULL DEFAULT '',
                relationship_state_json TEXT NOT NULL DEFAULT '{}',
                character_knowledge_json TEXT NOT NULL DEFAULT '[]',
                canon_locks_json TEXT NOT NULL DEFAULT '[]',
                unresolved_threads_json TEXT NOT NULL DEFAULT '[]',
                continuity_warnings_json TEXT NOT NULL DEFAULT '[]',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_continuity_rows (
                row_id TEXT PRIMARY KEY,
                scope_id TEXT NOT NULL DEFAULT '',
                continuity_type TEXT NOT NULL DEFAULT 'note',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_retrieval_traces (
                trace_id TEXT PRIMARY KEY,
                query TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                result_count INTEGER NOT NULL DEFAULT 0,
                engine_snapshot_json TEXT NOT NULL DEFAULT '{}',
                results_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'placeholder',
                created_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_turn_summaries (
                summary_id TEXT PRIMARY KEY,
                scene_id TEXT NOT NULL DEFAULT '',
                turn_id TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_story_checkpoints (
                checkpoint_id TEXT PRIMARY KEY,
                storyline_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_entities_kind ON rp_entities(kind);
            CREATE INDEX IF NOT EXISTS idx_rp_entities_status ON rp_entities(status);
            CREATE INDEX IF NOT EXISTS idx_rp_edges_source ON rp_edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_rp_edges_target ON rp_edges(target_id);
            CREATE INDEX IF NOT EXISTS idx_rp_memory_fragments_namespace ON rp_memory_fragments(namespace);
            CREATE INDEX IF NOT EXISTS idx_rp_memory_fragments_source ON rp_memory_fragments(source_type, source_id);
            CREATE INDEX IF NOT EXISTS idx_rp_character_states_scope ON rp_character_states(scope_id);
            CREATE INDEX IF NOT EXISTS idx_rp_character_knowledge_scope ON rp_character_knowledge(scope_id);
            CREATE INDEX IF NOT EXISTS idx_rp_unresolved_threads_scope ON rp_unresolved_threads(scope_id, status);
            CREATE INDEX IF NOT EXISTS idx_rp_scene_memory_packets_scene ON rp_scene_memory_packets(scene_id, scope_id);
            CREATE TABLE IF NOT EXISTS rp_vector_index (
                index_id TEXT PRIMARY KEY,
                source_table TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT '',
                scope_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                embedding_json TEXT NOT NULL DEFAULT '[]',
                embedding_dimension INTEGER NOT NULL DEFAULT 0,
                model_id TEXT NOT NULL DEFAULT '',
                vector_status TEXT NOT NULL DEFAULT 'indexed',
                payload_json TEXT NOT NULL DEFAULT '{}',
                indexed_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_retrieval_traces_created ON rp_retrieval_traces(created_at);
            CREATE INDEX IF NOT EXISTS idx_rp_vector_source ON rp_vector_index(source_table, source_id);
            CREATE INDEX IF NOT EXISTS idx_rp_vector_scope ON rp_vector_index(scope_id);
            CREATE INDEX IF NOT EXISTS idx_rp_vector_indexed ON rp_vector_index(indexed_at);
                        CREATE TABLE IF NOT EXISTS rp_contradiction_reports (
                report_id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL DEFAULT '',
                severity TEXT NOT NULL DEFAULT 'medium',
                status TEXT NOT NULL DEFAULT 'open',
                scope_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                source_a_table TEXT NOT NULL DEFAULT '',
                source_a_id TEXT NOT NULL DEFAULT '',
                source_b_table TEXT NOT NULL DEFAULT '',
                source_b_id TEXT NOT NULL DEFAULT '',
                evidence_json TEXT NOT NULL DEFAULT '[]',
                resolution_note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_contradictions_status ON rp_contradiction_reports(status);
            CREATE INDEX IF NOT EXISTS idx_rp_contradictions_scope ON rp_contradiction_reports(scope_id);
            CREATE INDEX IF NOT EXISTS idx_rp_contradictions_rule ON rp_contradiction_reports(rule_id);

            CREATE TABLE IF NOT EXISTS rp_checkpoint_branches (
                branch_id TEXT PRIMARY KEY,
                source_checkpoint_id TEXT NOT NULL DEFAULT '',
                source_session_id TEXT NOT NULL DEFAULT '',
                storyline_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'branch',
                branch_type TEXT NOT NULL DEFAULT 'alternate',
                diff_from_checkpoint_id TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_checkpoint_branches_source ON rp_checkpoint_branches(source_checkpoint_id);
            CREATE INDEX IF NOT EXISTS idx_rp_checkpoint_branches_storyline ON rp_checkpoint_branches(storyline_id);
            CREATE INDEX IF NOT EXISTS idx_rp_checkpoint_branches_status ON rp_checkpoint_branches(status);
            """
        )
        ensure_roleplay_sandbox_schema(conn)
        from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema
        ensure_roleplay_sqlite_upgrade_schema(conn, rebuild_search=False)
        conn.commit()
    return roleplay_sqlite_state_payload()


def _count_table(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        row = conn.execute("SELECT 1 FROM sqlite_master WHERE type IN ('table','virtual') AND name=?", (table,)).fetchone()
        return bool(row)
    except Exception:
        return False


def roleplay_sqlite_state_payload() -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    exists = ROLEPLAY_SQLITE_PATH.exists()
    table_counts: dict[str, int] = {}
    if exists:
        with _connect() as conn:
            for table in ROLEPLAY_MEMORY_TABLES:
                table_counts[table] = _count_table(conn, table)
    return {
        "schema_id": SCHEMA_ID,
        "version": SCHEMA_VERSION,
        "status": "ready" if exists else "not_created",
        "ready": exists,
        "path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        "tables": list(ROLEPLAY_MEMORY_TABLES),
        "table_counts": table_counts,
        "active_features": [
            "entity_graph_rows",
            "memory_fragments",
            "novel_source_documents",
            "novel_source_chunks",
            "candidate_canon_records",
            "continuity_rows",
            "turn_summaries",
            "story_checkpoints",
            "retrieval_traces",
            "sqlite_vector_index",
            "sqlite_memory_store_upgrade",
            "callback_anchor_table",
            "memory_recurrence_controls",
            "pin_suppress_memory_controls",
            "search_helper_documents",
            "schema_migration_ledger",
            "memory_store_health_report",
            "contradiction_reports",
            "full_provenance_trace_binding",
            "checkpoint_branching",
            "checkpoint_diff",
            "human_scene_memory_packets",
            "character_emotional_state",
            "character_knowledge_boundaries",
            "unresolved_thread_tracking",
        ],
        "deferred_features": [
            "canvas_provenance_graph_renderer",
            "chroma_authoritative_store_never",
        ],
    }



def _text_for_embedding(*parts: Any) -> str:
    return "\n".join(str(part or "").strip() for part in parts if str(part or "").strip())


def _tokenize_for_vector(text: str) -> list[str]:
    import re
    return re.findall(r"[a-zA-Z0-9_'-]+", (text or "").lower())


def deterministic_text_embedding(text: str, *, dimension: int = 96) -> list[float]:
    """Dependency-free embedding fallback for Phase 13 vector plumbing."""
    import hashlib
    import math
    dim = max(16, min(int(dimension or 96), 1024))
    vec = [0.0] * dim
    for token in _tokenize_for_vector(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if (digest[4] % 2 == 0) else -1.0
        vec[bucket] += sign * (1.0 + min(len(token), 20) / 20.0)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 8) for v in vec]


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = sum(float(a[i]) * float(b[i]) for i in range(size))
    na = sum(float(a[i]) * float(a[i]) for i in range(size)) ** 0.5
    nb = sum(float(b[i]) * float(b[i]) for i in range(size)) ** 0.5
    if not na or not nb:
        return 0.0
    return float(dot / (na * nb))


def lexical_rerank_score(query: str, content: str) -> float:
    q = set(_tokenize_for_vector(query))
    c = set(_tokenize_for_vector(content))
    if not q or not c:
        return 0.0
    return len(q & c) / max(1, len(q))


def _indexable_memory_rows(conn: sqlite3.Connection, *, scope_id: str = "", limit: int = 500) -> list[dict[str, Any]]:
    clean_scope = str(scope_id or "").strip()
    rows: list[dict[str, Any]] = []

    def add(table: str, source_id: str, source_type: str, row_scope: str, title: str, content: str, payload: dict[str, Any]):
        text = _text_for_embedding(title, content, json.dumps(payload, ensure_ascii=False, sort_keys=True)[:1500])
        if text:
            rows.append({"source_table": table, "source_id": source_id, "source_type": source_type, "scope_id": row_scope or "", "title": title or source_id, "content": text[:6000], "payload": payload})

    for row in conn.execute("SELECT entity_id, kind, title, status, scope_id, tags_json, payload_json, updated_at FROM rp_entities WHERE (? = '' OR scope_id = ? OR entity_id = ? OR payload_json LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_entities", data.get("entity_id", ""), data.get("kind", "entity"), data.get("scope_id", ""), data.get("title", ""), data.get("payload_json", ""), data)
    for row in conn.execute("SELECT fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, updated_at FROM rp_memory_fragments WHERE status NOT IN ('superseded','archived') AND (? = '' OR source_id = ? OR namespace LIKE ? OR payload_json LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, f"%{clean_scope}%", f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_memory_fragments", data.get("fragment_id", ""), data.get("memory_type", "memory"), data.get("source_id", ""), data.get("memory_type", "Memory"), data.get("content", ""), data)
    for row in conn.execute("SELECT memory_id, namespace, scope_id, title, content, status, payload_json, updated_at FROM rp_shared_memories WHERE (? = '' OR scope_id = ? OR memory_id = ? OR payload_json LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_shared_memories", data.get("memory_id", ""), "shared_memory", data.get("scope_id", ""), data.get("title", ""), data.get("content", ""), data)
    for row in conn.execute("SELECT row_id, scope_id, continuity_type, title, content, source_id, status, updated_at FROM rp_continuity_rows WHERE (? = '' OR scope_id = ? OR source_id = ? OR row_id LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_continuity_rows", data.get("row_id", ""), data.get("continuity_type", "continuity"), data.get("scope_id", ""), data.get("title", ""), data.get("content", ""), data)
    for row in conn.execute("SELECT chunk_id, source_id, project_id, chunk_index, title, content, memory_type, status, payload_json, updated_at FROM rp_source_chunks WHERE (? = '' OR source_id = ? OR project_id = ? OR payload_json LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_source_chunks", data.get("chunk_id", ""), data.get("memory_type", "source_chunk"), data.get("project_id", ""), data.get("title", ""), data.get("content", ""), data)
    for row in conn.execute("SELECT canon_id, source_id, project_id, canon_type, title, content, status, payload_json, updated_at FROM rp_canon_records WHERE (? = '' OR source_id = ? OR project_id = ? OR payload_json LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_canon_records", data.get("canon_id", ""), data.get("canon_type", "canon"), data.get("project_id", ""), data.get("title", ""), data.get("content", ""), data)
    for row in conn.execute("SELECT summary_id, scene_id, turn_id, summary, payload_json, updated_at FROM rp_turn_summaries WHERE (? = '' OR scene_id = ? OR turn_id = ? OR payload_json LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_turn_summaries", data.get("summary_id", ""), "turn_summary", data.get("scene_id", ""), data.get("turn_id", "Turn summary"), data.get("summary", ""), data)
    for row in conn.execute("SELECT checkpoint_id, storyline_id, session_id, title, summary, payload_json, updated_at FROM rp_story_checkpoints WHERE (? = '' OR storyline_id = ? OR session_id = ? OR checkpoint_id = ? OR payload_json LIKE ?) ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
        data = dict(row)
        add("rp_story_checkpoints", data.get("checkpoint_id", ""), "story_checkpoint", data.get("storyline_id", ""), data.get("title", ""), data.get("summary", ""), data)
    if _table_exists(conn, "rp_callback_anchors"):
        for row in conn.execute("SELECT anchor_id, sandbox_id, anchor_type, title, content, trigger_terms_json, priority, status, payload_json, updated_at FROM rp_callback_anchors WHERE (? = '' OR sandbox_id = ? OR anchor_id = ? OR payload_json LIKE ?) AND status != 'archived' ORDER BY updated_at DESC LIMIT ?", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", limit)).fetchall():
            data = dict(row)
            add("rp_callback_anchors", data.get("anchor_id", ""), data.get("anchor_type", "callback_anchor"), data.get("sandbox_id", ""), data.get("title", ""), data.get("content", ""), data)
    return rows[:limit]


def index_roleplay_memory_vectors(*, scope_id: str = "", limit: int = 500, force: bool = False, model_id: str = "local_hash_embeddings", dimension: int = 96, embedding_fn: Callable[[list[str]], dict[str, Any]] | None = None, embedding_mode: str = "local_hash_vector") -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    clean_scope = str(scope_id or "").strip()
    lim = _safe_limit(limit, default=500, maximum=5000) if "_safe_limit" in globals() else max(1, min(int(limit or 500), 5000))
    now = _now()
    indexed: list[dict[str, str]] = []
    skipped = 0
    embedding_result: dict[str, Any] = {"mode": embedding_mode, "fallback_used": False}
    with _connect() as conn:
        rows = _indexable_memory_rows(conn, scope_id=clean_scope, limit=lim)
        pending: list[dict[str, Any]] = []
        for row in rows:
            index_id = f"{row['source_table']}:{row['source_id']}"
            if not force and conn.execute("SELECT 1 FROM rp_vector_index WHERE index_id = ?", (index_id,)).fetchone():
                skipped += 1
                continue
            pending.append(row)
        if embedding_fn and pending:
            embedding_result = embedding_fn([str(row.get("content") or "") for row in pending])
            vectors = embedding_result.get("vectors") or []
        else:
            vectors = [deterministic_text_embedding(row.get("content", ""), dimension=dimension) for row in pending]
            embedding_result = {"mode": "local_hash_embeddings", "model_id": model_id, "dimension": len(vectors[0]) if vectors else dimension, "fallback_used": embedding_mode == "local_hash_vector"}
        for idx, row in enumerate(pending):
            index_id = f"{row['source_table']}:{row['source_id']}"
            emb = vectors[idx] if idx < len(vectors) else deterministic_text_embedding(row.get("content", ""), dimension=dimension)
            clean_emb = [float(value) for value in emb if isinstance(value, (int, float))]
            conn.execute("""
                INSERT INTO rp_vector_index(index_id, source_table, source_id, source_type, scope_id, title, content, embedding_json, embedding_dimension, model_id, vector_status, payload_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(index_id) DO UPDATE SET source_table=excluded.source_table, source_type=excluded.source_type, scope_id=excluded.scope_id, title=excluded.title, content=excluded.content, embedding_json=excluded.embedding_json, embedding_dimension=excluded.embedding_dimension, model_id=excluded.model_id, vector_status=excluded.vector_status, payload_json=excluded.payload_json, indexed_at=excluded.indexed_at
            """, (index_id, row["source_table"], row["source_id"], row["source_type"], row["scope_id"], row["title"], row["content"], _json(clean_emb), len(clean_emb), str(embedding_result.get("model_id") or model_id), "indexed", _json(row.get("payload") or {}), now))
            indexed.append({"index_id": index_id, "source_id": row["source_id"], "source_table": row["source_table"]})
        conn.commit()
    mode = str(embedding_result.get("mode") or embedding_mode or "local_hash_vector")
    return {"status": "indexed", "mode": mode, "scope_id": clean_scope, "indexed_count": len(indexed), "skipped_count": skipped, "model_id": str(embedding_result.get("model_id") or model_id), "dimension": int(embedding_result.get("dimension") or dimension or 0), "fallback_used": bool(embedding_result.get("fallback_used")), "embedding_engine": {k: v for k, v in embedding_result.items() if k != "vectors"}, "indexed": indexed[:50], "created_at": now}

def search_roleplay_vectors(*, query: str = "", scope_id: str = "", limit: int = 12, rerank: bool = True, min_score: float = -1.0, model_id: str = "local_hash_embeddings", dimension: int = 96, source: str = "roleplay", query_embedding: list[float] | None = None, embedding_mode: str = "vector_search", reranker_label: str = "lexical_overlap") -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    clean_query = str(query or "").strip()
    clean_scope = str(scope_id or "").strip()
    lim = _safe_limit(limit) if "_safe_limit" in globals() else max(1, min(int(limit or 12), 50))
    query_vec = query_embedding if query_embedding else deterministic_text_embedding(clean_query, dimension=dimension)
    results: list[dict[str, Any]] = []
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM rp_vector_index WHERE vector_status NOT IN ('superseded','archived') AND (? = '' OR scope_id = ? OR source_id = ? OR payload_json LIKE ?) ORDER BY indexed_at DESC LIMIT 5000", (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%")).fetchall()
        for row in rows:
            data = dict(row)
            try:
                emb = json.loads(data.get("embedding_json") or "[]")
            except Exception:
                emb = []
            vector_score = cosine_similarity(query_vec, emb)
            lexical_score = lexical_rerank_score(clean_query, f"{data.get('title','')} {data.get('content','')}") if rerank else 0.0
            score = (vector_score * 0.72) + (lexical_score * 0.28) if rerank else vector_score
            if score < min_score:
                continue
            results.append({"result_id": data.get("source_id") or data.get("index_id"), "index_id": data.get("index_id"), "table": data.get("source_table"), "title": data.get("title") or data.get("source_id"), "content": str(data.get("content") or "")[:1200], "scope_id": data.get("scope_id") or "", "source_id": data.get("source_id") or "", "status": data.get("vector_status") or "indexed", "score": round(score, 6), "vector_score": round(vector_score, 6), "rerank_score": round(lexical_score, 6), "payload": data})
        results = sorted(results, key=lambda item: item.get("score", 0), reverse=True)[:lim]
        now = _now()
        trace_id = f"trace-{now.replace(':', '').replace('.', '-') }"
        engine_snapshot = {"mode": embedding_mode, "embedding_model": model_id, "reranker": reranker_label if rerank else "disabled", "source": source}
        conn.execute("""
            INSERT INTO rp_retrieval_traces(trace_id, query, scope_id, result_count, engine_snapshot_json, results_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (trace_id, clean_query, clean_scope, len(results), _json(engine_snapshot), _json(results), "vector_search", now))
        conn.commit()
    return {"trace_id": trace_id, "query": clean_query, "scope_id": clean_scope, "result_count": len(results), "results": results, "status": "vector_search", "mode": embedding_mode, "created_at": now, "engine_snapshot": engine_snapshot}

def _extract_scope_id(payload: dict[str, Any]) -> str:
    context = derive_sandbox_context(payload)
    return context_scope_id(context)


def upsert_forge_record_memory(record: dict[str, Any]) -> dict[str, Any]:
    """Persist a Forge record through the Phase 3 deep Builder compiler.

    Kept under the original function name so existing Forge save / sync calls now
    produce granular sandbox-scoped memory fragments instead of one shallow summary.
    """
    from neo_app.roleplay.builder_memory_compiler import upsert_compiled_builder_record_memory
    return upsert_compiled_builder_record_memory(record, delete_existing=True)


def delete_forge_record_memory(record_id: str) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    clean = str(record_id or "").strip()
    with _connect() as conn:
        entity_deleted = conn.execute("DELETE FROM rp_entities WHERE entity_id = ?", (clean,)).rowcount
        fragment_deleted = conn.execute("DELETE FROM rp_memory_fragments WHERE source_type = 'forge_record' AND source_id = ?", (clean,)).rowcount
        conn.execute("DELETE FROM rp_edges WHERE source_id = ? OR target_id = ?", (clean, clean))
        conn.commit()
    return {"record_id": clean, "entity_rows_deleted": entity_deleted, "fragment_rows_deleted": fragment_deleted}


def create_retrieval_trace_placeholder(*, query: str = "", scope_id: str = "", engine_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    now = _now()
    trace_id = f"trace-{now.replace(':', '').replace('.', '-') }"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_retrieval_traces(trace_id, query, scope_id, result_count, engine_snapshot_json, results_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trace_id, query, scope_id, 0, _json(engine_snapshot or {}), "[]", "placeholder", now),
        )
        conn.commit()
    return {"trace_id": trace_id, "query": query, "scope_id": scope_id, "status": "placeholder", "result_count": 0}


def _safe_limit(value: Any, default: int = 12, maximum: int = 50) -> int:
    try:
        limit = int(value)
    except Exception:
        limit = default
    return max(1, min(limit, maximum))


def _row_result(kind: str, row: sqlite3.Row, *, score: float = 1.0) -> dict[str, Any]:
    data = dict(row)
    result_id = str(data.get("entity_id") or data.get("fragment_id") or data.get("memory_id") or data.get("row_id") or data.get("summary_id") or data.get("checkpoint_id") or "")
    title = str(data.get("title") or data.get("kind") or data.get("memory_type") or data.get("continuity_type") or data.get("scene_id") or data.get("storyline_id") or result_id)
    content = str(data.get("content") or data.get("summary") or data.get("payload_json") or "")
    scope_id = str(data.get("scope_id") or data.get("scene_id") or data.get("storyline_id") or "")
    source_id = str(data.get("source_id") or data.get("turn_id") or data.get("session_id") or result_id)
    return {
        "result_id": result_id,
        "table": kind,
        "title": title,
        "content": content[:1200],
        "scope_id": scope_id,
        "source_id": source_id,
        "status": str(data.get("status") or "foundation"),
        "score": score,
        "payload": data,
    }


def search_roleplay_memory_foundation(*, query: str = "", scope_id: str = "", memory_types: list[str] | None = None, limit: int = 12, source: str = "roleplay") -> dict[str, Any]:
    """Foundation keyword search across Roleplay SQLite rows.

    This intentionally does not call embeddings, vector stores, or rerankers. It
    prepares the retrieval contract and records traces for later engine upgrades.
    """
    ensure_roleplay_memory_schema()
    clean_query = str(query or "").strip()
    clean_scope = str(scope_id or "").strip()
    types = set(str(item).strip() for item in (memory_types or []) if str(item).strip())
    lim = _safe_limit(limit)
    like = f"%{clean_query}%"
    results: list[dict[str, Any]] = []

    def wants(name: str) -> bool:
        return not types or name in types or "all" in types

    with _connect() as conn:
        if wants("entities"):
            sql = """
                SELECT entity_id, kind, title, status, scope_id, tags_json, payload_json, source_path, updated_at
                FROM rp_entities
                WHERE (? = '' OR title LIKE ? OR entity_id LIKE ? OR kind LIKE ? OR tags_json LIKE ? OR payload_json LIKE ?)
                  AND (? = '' OR scope_id = ? OR entity_id = ? OR payload_json LIKE ?)
                ORDER BY updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (clean_query, like, like, like, like, like, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim)).fetchall()
            results.extend(_row_result("entities", row, score=1.0) for row in rows)
        if wants("memory_fragments"):
            sql = """
                SELECT fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, updated_at
                FROM rp_memory_fragments
                WHERE (? = '' OR content LIKE ? OR fragment_id LIKE ? OR namespace LIKE ? OR source_id LIKE ? OR tags_json LIKE ? OR payload_json LIKE ?)
                  AND (? = '' OR source_id = ? OR namespace LIKE ? OR payload_json LIKE ?)
                ORDER BY updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (clean_query, like, like, like, like, like, like, clean_scope, clean_scope, f"%{clean_scope}%", f"%{clean_scope}%", lim)).fetchall()
            results.extend(_row_result("memory_fragments", row, score=0.95) for row in rows)
        if wants("shared_memories"):
            sql = """
                SELECT memory_id, namespace, scope_id, title, content, status, payload_json, updated_at
                FROM rp_shared_memories
                WHERE (? = '' OR title LIKE ? OR content LIKE ? OR memory_id LIKE ? OR namespace LIKE ? OR payload_json LIKE ?)
                  AND (? = '' OR scope_id = ? OR memory_id = ? OR payload_json LIKE ?)
                ORDER BY updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (clean_query, like, like, like, like, like, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim)).fetchall()
            results.extend(_row_result("shared_memories", row, score=0.9) for row in rows)
        if wants("continuity"):
            sql = """
                SELECT row_id, scope_id, continuity_type, title, content, source_id, status, updated_at
                FROM rp_continuity_rows
                WHERE (? = '' OR title LIKE ? OR content LIKE ? OR row_id LIKE ? OR source_id LIKE ?)
                  AND (? = '' OR scope_id = ? OR source_id = ? OR row_id LIKE ?)
                ORDER BY updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (clean_query, like, like, like, like, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim)).fetchall()
            results.extend(_row_result("continuity", row, score=0.85) for row in rows)
        if wants("turn_summaries"):
            sql = """
                SELECT summary_id, scene_id, turn_id, summary, payload_json, updated_at
                FROM rp_turn_summaries
                WHERE (? = '' OR summary LIKE ? OR summary_id LIKE ? OR scene_id LIKE ? OR payload_json LIKE ?)
                  AND (? = '' OR scene_id = ? OR turn_id = ? OR payload_json LIKE ?)
                ORDER BY updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (clean_query, like, like, like, like, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim)).fetchall()
            results.extend(_row_result("turn_summaries", row, score=0.8) for row in rows)
        if wants("story_checkpoints"):
            sql = """
                SELECT checkpoint_id, storyline_id, session_id, title, summary, payload_json, updated_at
                FROM rp_story_checkpoints
                WHERE (? = '' OR title LIKE ? OR summary LIKE ? OR checkpoint_id LIKE ? OR storyline_id LIKE ? OR session_id LIKE ? OR payload_json LIKE ?)
                  AND (? = '' OR storyline_id = ? OR session_id = ? OR checkpoint_id = ? OR payload_json LIKE ?)
                ORDER BY updated_at DESC
                LIMIT ?
            """
            rows = conn.execute(sql, (clean_query, like, like, like, like, like, like, clean_scope, clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim)).fetchall()
            results.extend(_row_result("story_checkpoints", row, score=0.75) for row in rows)

        results = sorted(results, key=lambda item: (item.get("score", 0), str(item.get("payload", {}).get("updated_at") or "")), reverse=True)[:lim]
        now = _now()
        trace_id = f"trace-{now.replace(':', '').replace('.', '-') }"
        engine_snapshot = {"mode": "sqlite_keyword_foundation", "vector": "deferred", "reranker": "deferred", "source": source}
        conn.execute(
            """
            INSERT INTO rp_retrieval_traces(trace_id, query, scope_id, result_count, engine_snapshot_json, results_json, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (trace_id, clean_query, clean_scope, len(results), _json(engine_snapshot), _json(results), "foundation_search", now),
        )
        conn.commit()
    return {
        "trace_id": trace_id,
        "query": clean_query,
        "scope_id": clean_scope,
        "result_count": len(results),
        "results": results,
        "status": "foundation_search",
        "created_at": now,
        "deferred_features": ["embedding_query_execution", "vector_similarity_search", "reranker_execution"],
    }




def _is_polluted_roleplay_memory_text(value: Any) -> bool:
    text = str(value or "")
    if not text.strip():
        return False
    import re
    patterns = (
        r"\[\s*End\s+Scene\s*\]",
        r"\bthe\s+scene\s+ended\b",
        r"\banother\s+session\b",
        r"(?:^|\n|\s)(?:\*\*)?[A-Z][A-Za-z0-9 _'-]{1,80}(?:['’]s)?\s+Response\s*(?:\*\*)?:",
        r"(?:^|\n|\s)(?:#{1,6}\s*)?(?:Next\s+Beat|Summary|Assistant\s+turn|Scene\s+input)\s*:",
        r"\[\s*content\s+redacted",
        r"\bthe\s+scene\s+referenc(?:es|ed)\s+\w+\b",
        r"\bfrom\s+his\s+computer\s+screen\b",
        r"\bdata\s+upload\b",
        r"\bbe\s+a\s+bit\s+more\s+patient\s+please\b",
    )
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

def upsert_scene_setup_memory(setup: dict[str, Any]) -> dict[str, Any]:
    """Persist Scene setup into continuity + memory rows. Foundation-only: no vector writes."""
    ensure_roleplay_memory_schema()
    scene_id = str(setup.get("scene_id") or "default").strip() or "default"
    title = str(setup.get("title") or "Untitled Scene")
    premise = str(setup.get("premise") or "")
    notes = str(setup.get("scene_notes") or "")
    scope_id = str(setup.get("runtime_bundle_id") or setup.get("memory_scope") or scene_id)
    now = _now()
    row_id = f"scene:{scene_id}:setup"
    fragment_id = f"scene:{scene_id}:setup"
    content = "\n\n".join([part for part in [title, premise, notes] if part]).strip()
    payload = {
        "scene_id": scene_id,
        "title": title,
        "tone": setup.get("tone") or "",
        "reply_style": setup.get("reply_style") or "",
        "narrator_posture": setup.get("narrator_posture") or "",
        "continuity_mode": setup.get("continuity_mode") or "",
        "runtime_bundle_id": setup.get("runtime_bundle_id") or "",
        "memory_scope": setup.get("memory_scope") or "roleplay.scene",
        "storage_path": setup.get("storage_path") or "",
    }
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_continuity_rows(row_id, scope_id, continuity_type, title, content, source_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO UPDATE SET
                scope_id=excluded.scope_id,
                title=excluded.title,
                content=excluded.content,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (row_id, scope_id, "scene_setup", title, content, scene_id, "foundation", now, now),
        )
        conn.execute(
            """
            INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fragment_id) DO UPDATE SET
                content=excluded.content,
                payload_json=excluded.payload_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (fragment_id, "roleplay.scene", "scene_setup", scene_id, "continuity_seed", "foundation_stub", content, _json(["scene", "setup"]), _json(payload), "not_indexed", now, now),
        )
        conn.commit()
    return {"scene_id": scene_id, "continuity_row_id": row_id, "fragment_id": fragment_id, "status": "linked"}


def upsert_scene_turn_memory(scene_id: str, turns: list[dict[str, Any]]) -> dict[str, Any]:
    """Persist captured placeholder scene turns into turn summaries + memory fragments."""
    ensure_roleplay_memory_schema()
    clean_scene_id = str(scene_id or "default").strip() or "default"
    now = _now()
    synced: list[dict[str, str]] = []
    with _connect() as conn:
        for turn in turns or []:
            turn_id = str(turn.get("turn_id") or "").strip()
            text = str(turn.get("text") or "").strip()
            role = str(turn.get("role") or "turn")
            if not turn_id or not text:
                continue
            if status not in {"submitted_stream", "submitted_live", "streamed", "generated"}:
                continue
            if _is_polluted_roleplay_memory_text(text):
                continue
            summary_id = f"scene:{clean_scene_id}:turn:{turn_id}"
            fragment_id = f"scene:{clean_scene_id}:turn:{turn_id}"
            summary = text[:700]
            payload = {"scene_id": clean_scene_id, "turn_id": turn_id, "role": role, "status": turn.get("status") or "captured_placeholder"}
            conn.execute(
                """
                INSERT INTO rp_turn_summaries(summary_id, scene_id, turn_id, summary, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(summary_id) DO UPDATE SET
                    summary=excluded.summary,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (summary_id, clean_scene_id, turn_id, summary, _json(payload), str(turn.get("created_at") or now), now),
            )
            conn.execute(
                """
                INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fragment_id) DO UPDATE SET
                    content=excluded.content,
                    payload_json=excluded.payload_json,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (fragment_id, "roleplay.scene", "scene_turn", turn_id, "episodic_memory", "foundation_stub", text, _json(["scene", role]), _json(payload), "not_indexed", str(turn.get("created_at") or now), now),
            )
            synced.append({"turn_id": turn_id, "summary_id": summary_id, "fragment_id": fragment_id})
        conn.commit()
    return {"scene_id": clean_scene_id, "synced_turn_count": len(synced), "synced": synced, "status": "linked"}


def upsert_storyline_memory(record: dict[str, Any]) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    storyline_id = str(record.get("storyline_id") or "").strip()
    if not storyline_id:
        raise ValueError("storyline_id is required")
    title = str(record.get("title") or storyline_id)
    content = "\n\n".join([x for x in [title, str(record.get("premise") or ""), str(record.get("arc") or ""), str(record.get("beats") or "")] if x]).strip()
    now = _now()
    fragment_id = f"storyline:{storyline_id}:summary"
    memory_id = f"storyline:{storyline_id}:shared"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_shared_memories(memory_id, namespace, scope_id, title, content, status, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_id) DO UPDATE SET
                title=excluded.title,
                content=excluded.content,
                status=excluded.status,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (memory_id, "roleplay.story", storyline_id, title, content, str(record.get("status") or "foundation"), _json(record), str(record.get("created_at") or now), now),
        )
        conn.execute(
            """
            INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fragment_id) DO UPDATE SET
                content=excluded.content,
                payload_json=excluded.payload_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (fragment_id, "roleplay.story", "storyline", storyline_id, "story_memory", "foundation_stub", content, _json(["storyline"]), _json(record), "not_indexed", str(record.get("created_at") or now), now),
        )
        conn.commit()
    return {"storyline_id": storyline_id, "memory_id": memory_id, "fragment_id": fragment_id, "status": "linked"}


def upsert_story_session_memory(session: dict[str, Any]) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    session_id = str(session.get("session_id") or "").strip()
    if not session_id:
        raise ValueError("session_id is required")
    storyline_id = str(session.get("storyline_id") or "unassigned")
    title = str(session.get("title") or session_id)
    summary = str(session.get("summary") or "")
    now = _now()
    row_id = f"story_session:{session_id}:continuity"
    fragment_id = f"story_session:{session_id}:summary"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_continuity_rows(row_id, scope_id, continuity_type, title, content, source_id, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id) DO UPDATE SET
                scope_id=excluded.scope_id,
                title=excluded.title,
                content=excluded.content,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (row_id, storyline_id, "story_session", title, summary, session_id, str(session.get("status") or "draft"), str(session.get("created_at") or now), now),
        )
        conn.execute(
            """
            INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fragment_id) DO UPDATE SET
                content=excluded.content,
                payload_json=excluded.payload_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (fragment_id, "roleplay.story", "story_session", session_id, "session_summary", "foundation_stub", f"{title}\n\n{summary}".strip(), _json(["story_session"]), _json(session), "not_indexed", str(session.get("created_at") or now), now),
        )
        conn.commit()
    return {"session_id": session_id, "continuity_row_id": row_id, "fragment_id": fragment_id, "status": "linked"}


def upsert_story_checkpoint_memory(checkpoint: dict[str, Any]) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
    if not checkpoint_id:
        raise ValueError("checkpoint_id is required")
    storyline_id = str(checkpoint.get("storyline_id") or "")
    session_id = str(checkpoint.get("session_id") or "")
    title = str(checkpoint.get("title") or checkpoint_id)
    summary = str(checkpoint.get("summary") or "")
    now = _now()
    fragment_id = f"story_checkpoint:{checkpoint_id}:summary"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_story_checkpoints(checkpoint_id, storyline_id, session_id, title, summary, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(checkpoint_id) DO UPDATE SET
                storyline_id=excluded.storyline_id,
                session_id=excluded.session_id,
                title=excluded.title,
                summary=excluded.summary,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (checkpoint_id, storyline_id, session_id, title, summary, _json(checkpoint), str(checkpoint.get("created_at") or now), now),
        )
        conn.execute(
            """
            INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fragment_id) DO UPDATE SET
                content=excluded.content,
                payload_json=excluded.payload_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (fragment_id, "roleplay.story", "story_checkpoint", checkpoint_id, "checkpoint_summary", "foundation_stub", f"{title}\n\n{summary}".strip(), _json(["checkpoint"]), _json(checkpoint), "not_indexed", str(checkpoint.get("created_at") or now), now),
        )
        conn.commit()
    return {"checkpoint_id": checkpoint_id, "fragment_id": fragment_id, "status": "linked"}




def _rp_slug(value: str) -> str:
    import re
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return cleaned[:80] or "item"


def upsert_roleplay_human_scene_packet(packet: dict[str, Any]) -> dict[str, Any]:
    """Persist a human-memory packet for Scene/Roleplay continuity.

    This layer stores state, not just transcript text: emotional tone,
    character knowledge, relationship state, canon locks, unresolved hooks, and
    continuity warnings. It keeps Roleplay's specialized DB authoritative while
    allowing Memory Engine indexing to publish searchable summaries later.
    """
    ensure_roleplay_memory_schema()
    scene_id = str(packet.get("scene_id") or "default").strip() or "default"
    scope_id = str(packet.get("scope_id") or packet.get("runtime_bundle_id") or scene_id).strip() or scene_id
    title = str(packet.get("title") or f"Scene {scene_id}").strip()
    emotional_tone = str(packet.get("emotional_tone") or "steady").strip()
    now = _now()
    packet_id = f"human_scene:{scene_id}:packet"
    relationship_state = packet.get("relationship_state") if isinstance(packet.get("relationship_state"), dict) else {}
    character_knowledge = packet.get("character_knowledge") if isinstance(packet.get("character_knowledge"), list) else []
    canon_locks = packet.get("canon_locks") if isinstance(packet.get("canon_locks"), list) else []
    unresolved_threads = packet.get("unresolved_threads") if isinstance(packet.get("unresolved_threads"), list) else []
    continuity_warnings = packet.get("continuity_warnings") if isinstance(packet.get("continuity_warnings"), list) else []
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_scene_memory_packets(packet_id, scene_id, scope_id, title, emotional_tone, relationship_state_json, character_knowledge_json, canon_locks_json, unresolved_threads_json, continuity_warnings_json, payload_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(packet_id) DO UPDATE SET
                scope_id=excluded.scope_id,
                title=excluded.title,
                emotional_tone=excluded.emotional_tone,
                relationship_state_json=excluded.relationship_state_json,
                character_knowledge_json=excluded.character_knowledge_json,
                canon_locks_json=excluded.canon_locks_json,
                unresolved_threads_json=excluded.unresolved_threads_json,
                continuity_warnings_json=excluded.continuity_warnings_json,
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at
            """,
            (packet_id, scene_id, scope_id, title, emotional_tone, _json(relationship_state), _json(character_knowledge), _json(canon_locks), _json(unresolved_threads), _json(continuity_warnings), _json(packet), now, now),
        )
        state_id = f"scene:{scene_id}:emotional_state"
        conn.execute(
            """
            INSERT INTO rp_character_states(state_id, character_id, scope_id, display_name, current_emotion, emotional_vector_json, goals_json, boundaries_json, payload_json, trust_level, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(state_id) DO UPDATE SET
                current_emotion=excluded.current_emotion,
                emotional_vector_json=excluded.emotional_vector_json,
                goals_json=excluded.goals_json,
                boundaries_json=excluded.boundaries_json,
                payload_json=excluded.payload_json,
                trust_level=excluded.trust_level,
                updated_at=excluded.updated_at
            """,
            (state_id, scene_id, scope_id, title, emotional_tone, _json(packet.get("emotional_vector") or {}), _json(packet.get("active_goals") or []), _json(packet.get("boundaries") or []), _json(packet), "inferred", now),
        )
        for idx, item in enumerate(character_knowledge[:60]):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or item.get("fact") or "").strip()
            if not content:
                continue
            knowledge_id = f"knowledge:{scene_id}:{_rp_slug(str(item.get('character_id') or item.get('character') or 'scene'))}:{idx}"
            conn.execute(
                """
                INSERT INTO rp_character_knowledge(knowledge_id, character_id, scope_id, subject_id, knowledge_type, content, visibility, canon_status, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(knowledge_id) DO UPDATE SET
                    content=excluded.content,
                    visibility=excluded.visibility,
                    canon_status=excluded.canon_status,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (knowledge_id, str(item.get("character_id") or item.get("character") or scene_id), scope_id, str(item.get("subject_id") or scene_id), str(item.get("knowledge_type") or "known_fact"), content, str(item.get("visibility") or "character_only"), str(item.get("canon_status") or "draft"), _json(item), now),
            )
        for idx, item in enumerate(unresolved_threads[:30]):
            content = str(item.get("content") if isinstance(item, dict) else item).strip()
            if not content:
                continue
            title_value = str((item.get("title") if isinstance(item, dict) else "") or content[:90]).strip()
            thread_id = f"thread:{scene_id}:{idx}:{_rp_slug(title_value)[:36]}"
            payload = item if isinstance(item, dict) else {"content": content}
            conn.execute(
                """
                INSERT INTO rp_unresolved_threads(thread_id, scope_id, scene_id, title, thread_type, status, priority, content, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    title=excluded.title,
                    thread_type=excluded.thread_type,
                    status=excluded.status,
                    priority=excluded.priority,
                    content=excluded.content,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (thread_id, scope_id, scene_id, title_value, str(payload.get("thread_type") or "open_hook"), str(payload.get("status") or "open"), str(payload.get("priority") or "normal"), content, _json(payload), now, now),
            )
        memory_content = "\n".join([
            f"Scene: {title}",
            f"Emotional tone: {emotional_tone}",
            "Canon locks: " + "; ".join(str(x) for x in canon_locks[:8]),
            "Unresolved threads: " + "; ".join(str((x.get('content') if isinstance(x, dict) else x)) for x in unresolved_threads[:8]),
            "Continuity warnings: " + "; ".join(str(x) for x in continuity_warnings[:8]),
        ]).strip()
        conn.execute(
            """
            INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(fragment_id) DO UPDATE SET
                content=excluded.content,
                payload_json=excluded.payload_json,
                status=excluded.status,
                updated_at=excluded.updated_at
            """,
            (f"human_scene:{scene_id}:memory_packet", "roleplay.human", "scene_memory_packet", scene_id, "human_memory_packet", "active", memory_content, _json(["human_memory", "scene", "continuity"]), _json(packet), "not_indexed", now, now),
        )
        conn.commit()
    return {"status": "linked", "packet_id": packet_id, "scene_id": scene_id, "scope_id": scope_id, "character_knowledge_count": len(character_knowledge), "unresolved_thread_count": len(unresolved_threads)}


def get_roleplay_human_scene_packet(scene_id: str = "default") -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    packet_id = f"human_scene:{str(scene_id or 'default').strip() or 'default'}:packet"
    with _connect() as conn:
        row = conn.execute("SELECT * FROM rp_scene_memory_packets WHERE packet_id = ?", (packet_id,)).fetchone()
    if not row:
        return {"status": "missing", "scene_id": scene_id, "packet": {}}
    data = dict(row)
    for key in ("relationship_state_json", "character_knowledge_json", "canon_locks_json", "unresolved_threads_json", "continuity_warnings_json", "payload_json"):
        try:
            data[key.replace("_json", "")] = json.loads(data.get(key) or "{}")
        except Exception:
            data[key.replace("_json", "")] = [] if key.endswith("s_json") else {}
    return {"status": "ready", "scene_id": scene_id, "packet": data}


def roleplay_human_memory_rows(*, limit: int = 300) -> list[dict[str, Any]]:
    ensure_roleplay_memory_schema()
    rows: list[dict[str, Any]] = []
    lim = max(1, min(int(limit or 300), 1000))
    with _connect() as conn:
        for row in conn.execute("SELECT packet_id, scene_id, scope_id, title, emotional_tone, payload_json, updated_at FROM rp_scene_memory_packets ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
            rows.append({"table": "rp_scene_memory_packets", **dict(row)})
        for row in conn.execute("SELECT state_id, character_id, scope_id, display_name, current_emotion, payload_json, updated_at FROM rp_character_states ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
            rows.append({"table": "rp_character_states", **dict(row)})
        for row in conn.execute("SELECT knowledge_id, character_id, scope_id, subject_id, knowledge_type, content, visibility, canon_status, payload_json, updated_at FROM rp_character_knowledge ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
            rows.append({"table": "rp_character_knowledge", **dict(row)})
        for row in conn.execute("SELECT thread_id, scope_id, scene_id, title, thread_type, status, priority, content, payload_json, updated_at FROM rp_unresolved_threads ORDER BY updated_at DESC LIMIT ?", (lim,)).fetchall():
            rows.append({"table": "rp_unresolved_threads", **dict(row)})
    return rows[:lim]
