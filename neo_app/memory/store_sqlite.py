from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import MemoryEvent
from .policies import normalize_memory_policy
from .unified_schema import ensure_unified_memory_schema, unified_memory_schema_status


class SQLiteMemoryStore:
    """Required base memory store. Uses Python's built-in sqlite3 only."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    surface TEXT NOT NULL,
                    subtab TEXT,
                    source TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT,
                    project_id TEXT,
                    extension_id TEXT,
                    provider_id TEXT,
                    family TEXT,
                    loader TEXT,
                    tags_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    importance TEXT NOT NULL,
                    should_embed INTEGER NOT NULL,
                    searchable_text TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_namespace ON memory_events(namespace)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_surface ON memory_events(surface)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_timestamp ON memory_events(timestamp)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_sources (
                    source_id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    root_path TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    index_policy TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    priority INTEGER NOT NULL,
                    description TEXT,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_documents (
                    document_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    title TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    retention_scope TEXT NOT NULL DEFAULT 'project',
                    memory_state TEXT NOT NULL DEFAULT 'active',
                    importance TEXT NOT NULL DEFAULT 'normal',
                    approval_state TEXT NOT NULL DEFAULT 'not_required',
                    policy_score REAL NOT NULL DEFAULT 0.5,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    indexed_at TEXT NOT NULL,
                    UNIQUE(source_id, source_path)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_documents_source ON memory_documents(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_documents_hash ON memory_documents(content_hash)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    chunk_index INTEGER NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    summary TEXT,
                    tags_json TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    start_line INTEGER,
                    end_line INTEGER,
                    content_hash TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    trust_level TEXT NOT NULL,
                    retention_scope TEXT NOT NULL DEFAULT 'project',
                    memory_state TEXT NOT NULL DEFAULT 'active',
                    importance TEXT NOT NULL DEFAULT 'normal',
                    approval_state TEXT NOT NULL DEFAULT 'not_required',
                    policy_score REAL NOT NULL DEFAULT 0.5,
                    searchable_text TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_chunks_source ON memory_chunks(source_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_chunks_document ON memory_chunks(document_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_chunks_hash ON memory_chunks(content_hash)")
            for table in ("memory_documents", "memory_chunks"):
                for column, ddl in (
                    ("retention_scope", "TEXT NOT NULL DEFAULT 'project'"),
                    ("memory_state", "TEXT NOT NULL DEFAULT 'active'"),
                    ("importance", "TEXT NOT NULL DEFAULT 'normal'"),
                    ("approval_state", "TEXT NOT NULL DEFAULT 'not_required'"),
                    ("policy_score", "REAL NOT NULL DEFAULT 0.5"),
                ):
                    try:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
                    except sqlite3.OperationalError:
                        pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_chunks_policy ON memory_chunks(memory_state, retention_scope, approval_state)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_chunks_policy_score ON memory_chunks(policy_score)")
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_chunks_fts USING fts5(
                        chunk_id UNINDEXED,
                        source_id UNINDEXED,
                        title,
                        content,
                        searchable_text
                    )
                    """
                )
            except sqlite3.OperationalError:
                # Some embedded Python builds may omit FTS5. Phase 7 keeps LIKE fallback.
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    embedding_id TEXT PRIMARY KEY,
                    chunk_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    dimension INTEGER NOT NULL,
                    vector_store TEXT NOT NULL,
                    collection_name TEXT,
                    indexed_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_embeddings_chunk ON memory_embeddings(chunk_id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_retrieval_traces (
                    trace_id TEXT PRIMARY KEY,
                    query TEXT NOT NULL,
                    consumer TEXT NOT NULL,
                    profile TEXT NOT NULL,
                    sources_json TEXT NOT NULL,
                    results_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            # Phase M2: canonical additive Neo memory schema. Legacy tables remain
            # available for current surfaces while M3+ ingestion migrates writes
            # into neo_memory_* sandboxes.
            ensure_unified_memory_schema(conn)

    def write_event(self, event: MemoryEvent) -> MemoryEvent:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_events (
                    event_id, timestamp, namespace, surface, subtab, source, event_type,
                    title, summary, project_id, extension_id, provider_id, family, loader,
                    tags_json, payload_json, importance, should_embed, searchable_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.timestamp,
                    event.namespace,
                    event.surface,
                    event.subtab,
                    event.source,
                    event.event_type,
                    event.title,
                    event.summary,
                    event.project_id,
                    event.extension_id,
                    event.provider_id,
                    event.family,
                    event.loader,
                    json.dumps(event.tags),
                    json.dumps(event.payload),
                    event.importance,
                    1 if event.should_embed else 0,
                    event.searchable_text(),
                ),
            )
        return event

    def list_events(self, namespace: str | None = None, surface: str | None = None, limit: int = 20) -> list[MemoryEvent]:
        clauses: list[str] = []
        values: list[object] = []
        if namespace:
            clauses.append("namespace = ?")
            values.append(namespace)
        if surface:
            clauses.append("surface = ?")
            values.append(surface)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM memory_events {where} ORDER BY timestamp DESC LIMIT ?"
        values.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        return [self._row_to_event(row) for row in rows]

    def search_events(self, query: str, namespace: str | None = None, surface: str | None = None, limit: int = 20) -> list[MemoryEvent]:
        clauses: list[str] = []
        values: list[object] = []
        if query:
            clauses.append("searchable_text LIKE ?")
            values.append(f"%{query}%")
        if namespace:
            clauses.append("namespace = ?")
            values.append(namespace)
        if surface:
            clauses.append("surface = ?")
            values.append(surface)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM memory_events {where} ORDER BY timestamp DESC LIMIT ?"
        values.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        return [self._row_to_event(row) for row in rows]


    def upsert_source(self, source: dict[str, Any], *, updated_at: str) -> dict[str, Any]:
        payload = dict(source or {})
        source_id = str(payload.get("source_id") or "").strip()
        if not source_id:
            raise ValueError("source_id is required")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_sources (
                    source_id, label, source_type, root_path, enabled, index_policy,
                    visibility, trust_level, priority, description, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    source_id,
                    str(payload.get("label") or source_id),
                    str(payload.get("source_type") or "unknown"),
                    str(payload.get("root_path") or ""),
                    1 if payload.get("enabled", True) else 0,
                    str(payload.get("index_policy") or "hash_update"),
                    str(payload.get("visibility") or "expert"),
                    str(payload.get("trust_level") or "confirmed"),
                    int(payload.get("priority") or 50),
                    str(payload.get("description") or ""),
                    json.dumps({k: v for k, v in payload.items() if k not in {"source_id", "label", "source_type", "root_path", "enabled", "index_policy", "visibility", "trust_level", "priority", "description"}}),
                    updated_at,
                ),
            )
        return payload

    def list_sources(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM memory_sources ORDER BY priority DESC, source_id ASC").fetchall()
        out = []
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            item = dict(meta)
            item.update({
                "source_id": row["source_id"],
                "label": row["label"],
                "source_type": row["source_type"],
                "root_path": row["root_path"],
                "enabled": bool(row["enabled"]),
                "index_policy": row["index_policy"],
                "visibility": row["visibility"],
                "trust_level": row["trust_level"],
                "priority": row["priority"],
                "description": row["description"] or "",
                "updated_at": row["updated_at"],
            })
            out.append(item)
        return out

    def upsert_document(self, document: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_documents (
                    document_id, source_id, source_path, title, source_type, content_hash,
                    status, visibility, trust_level, retention_scope, memory_state, importance,
                    approval_state, policy_score, metadata_json, updated_at, indexed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    document["document_id"], document["source_id"], document["source_path"],
                    document.get("title") or document["source_path"], document.get("source_type") or "document",
                    document["content_hash"], document.get("status") or "indexed",
                    document.get("visibility") or "expert", document.get("trust_level") or "confirmed",
                    (document.get("policy") or {}).get("retention_scope") or document.get("retention_scope") or "project",
                    (document.get("policy") or {}).get("memory_state") or document.get("memory_state") or "active",
                    (document.get("policy") or {}).get("importance") or document.get("importance") or "normal",
                    (document.get("policy") or {}).get("approval_state") or document.get("approval_state") or "not_required",
                    float((document.get("policy") or {}).get("policy_score", document.get("policy_score", 0.5))),
                    json.dumps(document.get("metadata") or {}), document["updated_at"], document["indexed_at"],
                ),
            )
        return document

    def replace_document_chunks(self, document_id: str, chunks: list[dict[str, Any]]) -> int:
        with self._connect() as conn:
            existing = conn.execute("SELECT chunk_id FROM memory_chunks WHERE document_id = ?", (document_id,)).fetchall()
            conn.execute("DELETE FROM memory_chunks WHERE document_id = ?", (document_id,))
            try:
                for row in existing:
                    conn.execute("DELETE FROM memory_chunks_fts WHERE chunk_id = ?", (row["chunk_id"],))
            except sqlite3.OperationalError:
                pass
            for chunk in chunks:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_chunks (
                        chunk_id, document_id, source_id, chunk_index, title, content, summary,
                        tags_json, source_path, start_line, end_line, content_hash, visibility,
                        trust_level, retention_scope, memory_state, importance, approval_state,
                        policy_score, searchable_text, metadata_json, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        chunk["chunk_id"], chunk["document_id"], chunk["source_id"], int(chunk.get("chunk_index") or 0),
                        chunk.get("title") or "Memory chunk", chunk.get("content") or "", chunk.get("summary") or "",
                        json.dumps(chunk.get("tags") or []), chunk.get("source_path") or "", chunk.get("start_line"), chunk.get("end_line"),
                        chunk.get("content_hash") or "", chunk.get("visibility") or "expert", chunk.get("trust_level") or "confirmed",
                        (chunk.get("policy") or {}).get("retention_scope") or chunk.get("retention_scope") or "project",
                        (chunk.get("policy") or {}).get("memory_state") or chunk.get("memory_state") or "active",
                        (chunk.get("policy") or {}).get("importance") or chunk.get("importance") or "normal",
                        (chunk.get("policy") or {}).get("approval_state") or chunk.get("approval_state") or "not_required",
                        float((chunk.get("policy") or {}).get("policy_score", chunk.get("policy_score", 0.5))),
                        chunk.get("searchable_text") or " ".join([str(chunk.get("title") or ""), str(chunk.get("content") or "")]),
                        json.dumps(chunk.get("metadata") or {}), chunk["updated_at"],
                    ),
                )
                try:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO memory_chunks_fts (chunk_id, source_id, title, content, searchable_text)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            chunk["chunk_id"], chunk["source_id"], chunk.get("title") or "Memory chunk",
                            chunk.get("content") or "", chunk.get("searchable_text") or "",
                        ),
                    )
                except sqlite3.OperationalError:
                    pass
        return len(chunks)

    def upsert_embedding_refs(self, refs: list[dict[str, Any]]) -> int:
        if not refs:
            return 0
        with self._connect() as conn:
            for ref in refs:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO memory_embeddings (
                        embedding_id, chunk_id, source_id, model_id, dimension, vector_store, collection_name, indexed_at, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ref["embedding_id"], ref["chunk_id"], ref["source_id"], ref.get("model_id") or "unknown",
                        int(ref.get("dimension") or 0), ref.get("vector_store") or "sqlite", ref.get("collection_name") or "",
                        ref["indexed_at"], json.dumps(ref.get("metadata") or {}),
                    ),
                )
        return len(refs)

    def search_chunks(self, query: str, *, sources: list[str] | None = None, limit: int = 12) -> list[dict[str, Any]]:
        return self.search_chunks_keyword(query, sources=sources, limit=limit)

    def search_chunks_keyword(self, query: str, *, sources: list[str] | None = None, limit: int = 12) -> list[dict[str, Any]]:
        tokens = [part.strip() for part in str(query or "").replace('"', ' ').split() if part.strip()]
        if not tokens:
            return self._search_chunks_like(query, sources=sources, limit=limit)
        fts_query = " OR ".join(token.replace("'", "") for token in tokens[:10])
        source_clause = ""
        values: list[object] = [fts_query]
        if sources:
            placeholders = ",".join("?" for _ in sources)
            source_clause = f" AND c.source_id IN ({placeholders})"
            values.extend(sources)
        values.append(limit)
        sql = f"""
            SELECT c.*, bm25(memory_chunks_fts) AS fts_rank
            FROM memory_chunks_fts
            JOIN memory_chunks c ON c.chunk_id = memory_chunks_fts.chunk_id
            WHERE memory_chunks_fts MATCH ?{source_clause}
            ORDER BY fts_rank ASC
            LIMIT ?
        """
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, values).fetchall()
            results = [self._row_to_chunk(row, query=query) for row in rows]
            for rank, item in enumerate(results):
                base = item.get("score") or 0.0
                item["score"] = round(max(float(base), 1.0 / (1.0 + rank)), 6)
                item["retrieval_type"] = "fts_keyword"
            if results:
                return results
        except sqlite3.OperationalError:
            pass
        return self._search_chunks_like(query, sources=sources, limit=limit)

    def _search_chunks_like(self, query: str, *, sources: list[str] | None = None, limit: int = 12) -> list[dict[str, Any]]:
        clauses: list[str] = []
        values: list[object] = []
        tokens = [part.strip() for part in str(query or "").replace('"', ' ').split() if part.strip()]
        if tokens:
            token_clauses = []
            for token in tokens[:8]:
                token_clauses.append("searchable_text LIKE ?")
                values.append(f"%{token}%")
            clauses.append("(" + " OR ".join(token_clauses) + ")")
        if sources:
            placeholders = ",".join("?" for _ in sources)
            clauses.append(f"source_id IN ({placeholders})")
            values.extend(sources)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM memory_chunks {where} ORDER BY updated_at DESC LIMIT ?"
        values.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, values).fetchall()
        results = [self._row_to_chunk(row, query=query) for row in rows]
        for item in results:
            item["retrieval_type"] = "keyword"
        return results

    def get_chunks_by_ids(self, chunk_ids: list[str], *, query: str = "") -> list[dict[str, Any]]:
        clean_ids = [str(item) for item in chunk_ids if str(item or "").strip()]
        if not clean_ids:
            return []
        placeholders = ",".join("?" for _ in clean_ids)
        with self._connect() as conn:
            rows = conn.execute(f"SELECT * FROM memory_chunks WHERE chunk_id IN ({placeholders})", clean_ids).fetchall()
        by_id = {row["chunk_id"]: self._row_to_chunk(row, query=query) for row in rows}
        return [by_id[item] for item in clean_ids if item in by_id]


    def inspect_chunks(self, *, query: str = "", source_id: str | None = None, memory_state: str | None = None, trust_level: str | None = None, approval_state: str | None = None, visibility: str | None = None, limit: int = 25, offset: int = 0) -> dict[str, Any]:
        clauses: list[str] = []
        values: list[object] = []
        tokens = [part.strip() for part in str(query or "").replace('"', ' ').split() if part.strip()]
        if tokens:
            token_clauses = []
            for token in tokens[:8]:
                token_clauses.append("searchable_text LIKE ?")
                values.append(f"%{token}%")
            clauses.append("(" + " OR ".join(token_clauses) + ")")
        filters = {
            "source_id": source_id,
            "memory_state": memory_state,
            "trust_level": trust_level,
            "approval_state": approval_state,
            "visibility": visibility,
        }
        for column, value in filters.items():
            clean = str(value or "").strip()
            if clean:
                clauses.append(f"{column} = ?")
                values.append(clean)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        safe_limit = max(1, min(int(limit or 25), 100))
        safe_offset = max(0, int(offset or 0))
        with self._connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) AS count FROM memory_chunks {where}", values).fetchone()["count"]
            rows = conn.execute(
                f"""
                SELECT chunk_id, document_id, source_id, chunk_index, title, summary, tags_json, source_path,
                       start_line, end_line, visibility, trust_level, retention_scope, memory_state, importance,
                       approval_state, policy_score, updated_at, substr(content, 1, 700) AS content_preview,
                       substr(searchable_text, 1, 900) AS searchable_preview
                FROM memory_chunks
                {where}
                ORDER BY updated_at DESC, source_id ASC, chunk_index ASC
                LIMIT ? OFFSET ?
                """,
                values + [safe_limit, safe_offset],
            ).fetchall()
        chunks = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            item["snippet"] = (item.pop("content_preview") or item.pop("searchable_preview") or "").replace("\n", " ")[:420]
            chunks.append(item)
        return {"total": int(total or 0), "limit": safe_limit, "offset": safe_offset, "chunks": chunks}

    def get_chunk_detail(self, chunk_id: str) -> dict[str, Any] | None:
        clean = str(chunk_id or "").strip()
        if not clean:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT c.*, d.title AS document_title, d.status AS document_status, d.content_hash AS document_hash,
                       d.indexed_at AS document_indexed_at, d.metadata_json AS document_metadata_json
                FROM memory_chunks c
                LEFT JOIN memory_documents d ON d.document_id = c.document_id
                WHERE c.chunk_id = ?
                """,
                (clean,),
            ).fetchone()
        if not row:
            return None
        item = self._row_to_chunk(row, query="")
        item["document"] = {
            "document_id": row["document_id"],
            "title": row["document_title"],
            "status": row["document_status"],
            "content_hash": row["document_hash"],
            "indexed_at": row["document_indexed_at"],
            "metadata": json.loads(row["document_metadata_json"] or "{}"),
        }
        return item

    def get_retrieval_trace(self, trace_id: str) -> dict[str, Any] | None:
        clean = str(trace_id or "").strip()
        if not clean:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_retrieval_traces WHERE trace_id = ?", (clean,)).fetchone()
        if not row:
            return None
        return {
            "trace_id": row["trace_id"],
            "query": row["query"],
            "consumer": row["consumer"],
            "profile": row["profile"],
            "sources": json.loads(row["sources_json"] or "[]"),
            "results": json.loads(row["results_json"] or "[]"),
            "created_at": row["created_at"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
        }

    def list_retrieval_traces(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM memory_retrieval_traces ORDER BY created_at DESC LIMIT ?", (max(1, min(int(limit), 100)),)).fetchall()
        traces = []
        for row in rows:
            traces.append({
                "trace_id": row["trace_id"],
                "query": row["query"],
                "consumer": row["consumer"],
                "profile": row["profile"],
                "sources": json.loads(row["sources_json"] or "[]"),
                "results": json.loads(row["results_json"] or "[]"),
                "created_at": row["created_at"],
                "metadata": json.loads(row["metadata_json"] or "{}"),
            })
        return traces


    def related_chunks(self, chunk_id: str, *, limit: int = 8) -> dict[str, Any]:
        clean = str(chunk_id or "").strip()
        if not clean:
            return {"total": 0, "chunks": []}
        base = self.get_chunk_detail(clean)
        if not base:
            return {"total": 0, "chunks": []}
        safe_limit = max(1, min(int(limit or 8), 25))
        source_id = str(base.get("source_id") or "")
        source_path = str(base.get("source_path") or "")
        title = str(base.get("title") or "")
        tokens = [part.strip() for part in title.replace('"', ' ').split() if len(part.strip()) > 2][:6]
        clauses = ["chunk_id != ?"]
        values: list[object] = [clean]
        relation_hint = "same_source"
        if source_path:
            clauses.append("source_path = ?")
            values.append(source_path)
            relation_hint = "same_source_path"
        elif source_id:
            clauses.append("source_id = ?")
            values.append(source_id)
        if tokens:
            token_clauses = []
            for token in tokens:
                token_clauses.append("searchable_text LIKE ?")
                values.append(f"%{token}%")
            clauses.append("(" + " OR ".join(token_clauses) + ")")
            relation_hint = relation_hint + "+title_match"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT chunk_id, document_id, source_id, chunk_index, title, summary, tags_json, source_path,
                       start_line, end_line, visibility, trust_level, retention_scope, memory_state, importance,
                       approval_state, policy_score, updated_at, substr(content, 1, 700) AS content_preview
                FROM memory_chunks
                WHERE {' AND '.join(clauses)}
                ORDER BY CASE WHEN source_path = ? THEN 0 ELSE 1 END, ABS(chunk_index - ?), updated_at DESC
                LIMIT ?
                """,
                values + [source_path, int(base.get("chunk_index") or 0), safe_limit],
            ).fetchall()
        chunks = []
        for row in rows:
            item = dict(row)
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            item["snippet"] = (item.pop("content_preview") or "").replace("\n", " ")[:420]
            item["relation_hint"] = relation_hint
            chunks.append(item)
        return {"total": len(chunks), "base_chunk_id": clean, "relation_hint": relation_hint, "chunks": chunks}

    def consolidation_sources_for_summary(self, chunk_id: str) -> dict[str, Any]:
        base = self.get_chunk_detail(chunk_id)
        if not base:
            return {"ok": False, "status": "missing_chunk", "chunk_id": chunk_id}
        metadata = base.get("metadata") or {}
        source_chunk_ids = metadata.get("source_chunk_ids") or []
        sources = self.get_chunks_by_ids(source_chunk_ids) if source_chunk_ids else []
        return {
            "ok": True,
            "summary_chunk": {k: v for k, v in base.items() if k != "content"} | {"content": base.get("content")},
            "source_chunk_ids": source_chunk_ids,
            "sources": [{k: v for k, v in item.items() if k != "content"} | {"snippet": (item.get("content") or "")[:520].replace("\n", " ")} for item in sources],
        }

    def list_memory_policies(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT source_id, retention_scope, memory_state, visibility, trust_level, importance, approval_state, COUNT(*) AS count
                FROM memory_chunks
                GROUP BY source_id, retention_scope, memory_state, visibility, trust_level, importance, approval_state
                ORDER BY source_id ASC, count DESC
                """
            ).fetchall()
        return {
            "chunk_policy_counts": [dict(row) for row in rows],
        }

    def update_chunk_policy(self, chunk_id: str, policy: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM memory_chunks WHERE chunk_id = ?", (chunk_id,)).fetchone()
            if not row:
                return {"ok": False, "status": "missing_chunk", "chunk_id": chunk_id}
            normalized = normalize_memory_policy(row["source_id"], policy)
            metadata = json.loads(row["metadata_json"] or "{}")
            metadata["policy_override"] = True
            conn.execute(
                """
                UPDATE memory_chunks
                SET retention_scope = ?, memory_state = ?, visibility = ?, trust_level = ?,
                    importance = ?, approval_state = ?, policy_score = ?, metadata_json = ?
                WHERE chunk_id = ?
                """,
                (
                    normalized["retention_scope"], normalized["memory_state"], normalized["visibility"], normalized["trust_level"],
                    normalized["importance"], normalized["approval_state"], float(normalized["policy_score"]), json.dumps(metadata), chunk_id,
                ),
            )
        return {"ok": True, "status": "updated", "chunk_id": chunk_id, "policy": normalized}



    def list_conflict_groups(self, *, query: str = "", source_id: str | None = None, limit: int = 25) -> dict[str, Any]:
        groups = self._build_conflict_groups(query=query, source_id=source_id, hydrate=False)
        safe_limit = max(1, min(int(limit or 25), 100))
        return {"total": len(groups), "limit": safe_limit, "groups": groups[:safe_limit]}

    def get_conflict_group(self, group_id: str) -> dict[str, Any] | None:
        clean = str(group_id or "").strip()
        if not clean:
            return None
        groups = self._build_conflict_groups(hydrate=True)
        for group in groups:
            if group.get("group_id") == clean:
                return group
        return None

    def resolve_conflict_group(self, *, group_id: str | None = None, chunk_ids: list[str] | None = None, action: str = "", canonical_chunk_id: str | None = None, note: str = "") -> dict[str, Any]:
        selected_ids = [str(item).strip() for item in (chunk_ids or []) if str(item or "").strip()]
        if not selected_ids and group_id:
            group = self.get_conflict_group(group_id)
            if group:
                selected_ids = [chunk.get("chunk_id") for chunk in group.get("chunks") or [] if chunk.get("chunk_id")]
        selected_ids = list(dict.fromkeys(selected_ids))
        if not selected_ids:
            return {"ok": False, "status": "missing_chunks", "action": action}
        action = str(action or "").strip()
        canonical_chunk_id = str(canonical_chunk_id or "").strip()
        allowed = {"promote_canonical", "mark_all_canon", "deprecate_others", "flag_group_conflict", "archive_group", "mark_draft", "restore_active"}
        if action not in allowed:
            return {"ok": False, "status": "unknown_action", "action": action, "allowed_actions": sorted(allowed)}
        updated: list[dict[str, Any]] = []
        for chunk_id in selected_ids:
            policy: dict[str, Any]
            if action == "promote_canonical":
                is_canonical = chunk_id == canonical_chunk_id or (not canonical_chunk_id and chunk_id == selected_ids[0])
                if is_canonical:
                    policy = {"retention_scope": "canon", "memory_state": "canon", "trust_level": "confirmed", "importance": "high", "approval_state": "approved"}
                else:
                    policy = {"memory_state": "deprecated", "trust_level": "deprecated", "importance": "low", "approval_state": "rejected"}
            elif action == "deprecate_others":
                is_canonical = chunk_id == canonical_chunk_id
                policy = {"retention_scope": "canon", "memory_state": "canon", "trust_level": "confirmed", "importance": "high", "approval_state": "approved"} if is_canonical else {"memory_state": "deprecated", "trust_level": "deprecated", "importance": "low", "approval_state": "rejected"}
            elif action == "mark_all_canon":
                policy = {"retention_scope": "canon", "memory_state": "canon", "trust_level": "confirmed", "importance": "high", "approval_state": "approved"}
            elif action == "flag_group_conflict":
                policy = {"memory_state": "conflicting", "trust_level": "conflicting", "importance": "high", "approval_state": "pending"}
            elif action == "archive_group":
                policy = {"memory_state": "archived", "importance": "low"}
            elif action == "mark_draft":
                policy = {"retention_scope": "draft", "memory_state": "draft", "trust_level": "draft", "importance": "normal", "approval_state": "pending"}
            else:
                policy = {"memory_state": "active", "approval_state": "approved", "trust_level": "confirmed"}
            result = self.update_chunk_policy(chunk_id, policy)
            detail = self.get_chunk_detail(chunk_id)
            if detail:
                metadata = dict(detail.get("metadata") or {})
                metadata["last_conflict_resolution"] = {"action": action, "group_id": group_id or "manual", "canonical_chunk_id": canonical_chunk_id, "note": note}
                with self._connect() as conn:
                    conn.execute("UPDATE memory_chunks SET metadata_json = ? WHERE chunk_id = ?", (json.dumps(metadata), chunk_id))
                detail = self.get_chunk_detail(chunk_id)
            updated.append({"chunk_id": chunk_id, "result": result, "chunk": detail})
        return {"ok": True, "status": "resolved", "action": action, "group_id": group_id, "canonical_chunk_id": canonical_chunk_id or (selected_ids[0] if action == "promote_canonical" else ""), "updated_count": len(updated), "updated": updated}

    def list_canon_manager_items(self, *, source_id: str | None = None, include_candidates: bool = True, query: str = "", limit: int = 50) -> dict[str, Any]:
        clauses = []
        values: list[object] = []
        if source_id:
            clauses.append("source_id = ?")
            values.append(str(source_id))
        if include_candidates:
            clauses.append("(memory_state IN ('canon', 'draft', 'conflicting') OR retention_scope = 'canon' OR approval_state = 'pending')")
        else:
            clauses.append("(memory_state = 'canon' OR retention_scope = 'canon')")
        tokens = [part.strip() for part in str(query or "").replace('"', ' ').split() if part.strip()]
        if tokens:
            token_clauses = []
            for token in tokens[:8]:
                token_clauses.append("searchable_text LIKE ?")
                values.append(f"%{token}%")
            clauses.append("(" + " OR ".join(token_clauses) + ")")
        where = "WHERE " + " AND ".join(clauses)
        safe_limit = max(1, min(int(limit or 50), 150))
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_chunks
                {where}
                ORDER BY CASE memory_state WHEN 'canon' THEN 0 WHEN 'conflicting' THEN 1 WHEN 'draft' THEN 2 ELSE 3 END,
                         policy_score DESC, updated_at DESC
                LIMIT ?
                """,
                values + [safe_limit],
            ).fetchall()
        items = [self._row_to_chunk(row, query=query) for row in rows]
        return {
            "total": len(items),
            "limit": safe_limit,
            "items": [{k: v for k, v in item.items() if k != "content"} | {"snippet": (item.get("content") or "")[:420].replace("\n", " ")} for item in items],
        }

    def _build_conflict_groups(self, *, query: str = "", source_id: str | None = None, hydrate: bool = False) -> list[dict[str, Any]]:
        clauses = ["memory_state IN ('canon', 'draft', 'conflicting', 'active')"]
        values: list[object] = []
        if source_id:
            clauses.append("source_id = ?")
            values.append(str(source_id))
        tokens = [part.strip() for part in str(query or "").replace('"', ' ').split() if part.strip()]
        if tokens:
            token_clauses = []
            for token in tokens[:8]:
                token_clauses.append("searchable_text LIKE ?")
                values.append(f"%{token}%")
            clauses.append("(" + " OR ".join(token_clauses) + ")")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_chunks
                WHERE {' AND '.join(clauses)}
                ORDER BY source_id ASC, title ASC, updated_at DESC
                LIMIT 1200
                """,
                values,
            ).fetchall()
        buckets: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for row in rows:
            item = self._row_to_chunk(row, query=query)
            metadata = item.get("metadata") or {}
            key = str(metadata.get("canon_key") or metadata.get("entity_id") or item.get("title") or item.get("source_path") or "").lower()
            key = re.sub(r"[^a-z0-9]+", " ", key).strip()[:96] or str(item.get("chunk_id"))
            buckets.setdefault((str(item.get("source_id") or "unknown"), key), []).append(item)
        groups: list[dict[str, Any]] = []
        for (src, key), items in buckets.items():
            if len(items) < 2 and not any(item.get("memory_state") == "conflicting" or item.get("trust_level") == "conflicting" for item in items):
                continue
            states = {str(item.get("memory_state") or "") for item in items}
            trusts = {str(item.get("trust_level") or "") for item in items}
            approvals = {str(item.get("approval_state") or "") for item in items}
            has_explicit = "conflicting" in states or "conflicting" in trusts
            has_canon = "canon" in states or any(item.get("retention_scope") == "canon" for item in items)
            has_draftish = bool(states & {"draft", "active"}) or bool(approvals & {"pending", "rejected"}) or bool(trusts & {"draft", "inferred", "mixed"})
            distinct_hashes = {str(item.get("content_hash") or item.get("metadata", {}).get("content_hash") or item.get("chunk_id")) for item in items}
            if not (has_explicit or (has_canon and has_draftish) or (len(items) > 1 and len(distinct_hashes) > 1 and has_draftish)):
                continue
            if has_explicit:
                conflict_type = "explicit_conflict"
                severity = "high"
            elif has_canon and has_draftish:
                conflict_type = "draft_vs_canon"
                severity = "high"
            else:
                conflict_type = "duplicate_candidate"
                severity = "normal"
            ids = sorted(str(item.get("chunk_id")) for item in items)
            group_id = hashlib.sha1("|".join(ids).encode("utf-8")).hexdigest()[:16]
            preview_chunks = items if hydrate else items[:5]
            groups.append({
                "group_id": group_id,
                "source_id": src,
                "canon_key": key,
                "title": items[0].get("title") or key,
                "conflict_type": conflict_type,
                "severity": severity,
                "chunk_count": len(items),
                "states": sorted(states),
                "trust_levels": sorted(trusts),
                "approval_states": sorted(approvals),
                "source_paths": sorted({str(item.get("source_path") or "") for item in items if item.get("source_path")})[:8],
                "suggested_actions": ["promote_canonical", "mark_all_canon", "flag_group_conflict", "mark_draft", "archive_group"],
                "chunks": [({k: v for k, v in item.items() if hydrate or k != "content"} | {"snippet": (item.get("content") or "")[:420].replace("\n", " ")}) for item in preview_chunks],
            })
        groups.sort(key=lambda group: (0 if group["severity"] == "high" else 1, -group["chunk_count"], group["source_id"], group["title"]))
        return groups

    def unified_schema_status(self) -> dict[str, Any]:
        with self._connect() as conn:
            return unified_memory_schema_status(conn)


    def document_stats(self) -> dict[str, Any]:
        with self._connect() as conn:
            source_rows = conn.execute("SELECT source_id, COUNT(*) AS count FROM memory_documents GROUP BY source_id").fetchall()
            chunk_rows = conn.execute("SELECT source_id, COUNT(*) AS count FROM memory_chunks GROUP BY source_id").fetchall()
            doc_count = conn.execute("SELECT COUNT(*) AS count FROM memory_documents").fetchone()["count"]
            chunk_count = conn.execute("SELECT COUNT(*) AS count FROM memory_chunks").fetchone()["count"]
            emb_count = conn.execute("SELECT COUNT(*) AS count FROM memory_embeddings").fetchone()["count"]
            policy_rows = conn.execute("SELECT memory_state, COUNT(*) AS count FROM memory_chunks GROUP BY memory_state").fetchall()
            trust_rows = conn.execute("SELECT trust_level, COUNT(*) AS count FROM memory_chunks GROUP BY trust_level").fetchall()
            scope_rows = conn.execute("SELECT retention_scope, COUNT(*) AS count FROM memory_chunks GROUP BY retention_scope").fetchall()
        with self._connect() as conn:
            unified = unified_memory_schema_status(conn)
        return {
            "document_count": doc_count,
            "chunk_count": chunk_count,
            "embedding_ref_count": emb_count,
            "documents_by_source": {row["source_id"]: row["count"] for row in source_rows},
            "chunks_by_source": {row["source_id"]: row["count"] for row in chunk_rows},
            "chunks_by_memory_state": {row["memory_state"]: row["count"] for row in policy_rows},
            "chunks_by_trust_level": {row["trust_level"]: row["count"] for row in trust_rows},
            "chunks_by_retention_scope": {row["retention_scope"]: row["count"] for row in scope_rows},
            "unified_schema": unified,
        }



    def list_consolidation_candidates(self, *, source_id: str | None = None, query: str = "", min_group_size: int = 2, limit: int = 25) -> dict[str, Any]:
        """Return heuristic groups that are good candidates for durable summaries.

        This is intentionally conservative: it never mutates memory. Groups are
        based on source + canon/entity/title keys and policy state. Admin must
        explicitly run a consolidation action before any summary chunk is written.
        """
        clauses = ["memory_state NOT IN ('deprecated', 'archived')", "approval_state != 'rejected'", "source_id != 'memory_consolidation'"]
        values: list[object] = []
        if source_id:
            clauses.append("source_id = ?")
            values.append(str(source_id))
        tokens = [part.strip() for part in str(query or "").replace('"', ' ').split() if part.strip()]
        if tokens:
            token_clauses = []
            for token in tokens[:8]:
                token_clauses.append("searchable_text LIKE ?")
                values.append(f"%{token}%")
            clauses.append("(" + " OR ".join(token_clauses) + ")")
        where = "WHERE " + " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT chunk_id, document_id, source_id, title, summary, source_path, start_line, end_line,
                       substr(content, 1, 900) AS content_preview, tags_json, visibility, trust_level,
                       retention_scope, memory_state, importance, approval_state, policy_score,
                       content_hash, updated_at, metadata_json
                FROM memory_chunks
                {where}
                ORDER BY source_id ASC, title ASC, updated_at DESC
                LIMIT 800
                """,
                values,
            ).fetchall()
        buckets: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            meta = json.loads(row["metadata_json"] or "{}")
            raw_key = str(meta.get("canon_key") or meta.get("entity_id") or row["source_path"] or row["title"] or "").lower()
            norm_key = re.sub(r"[^a-z0-9]+", " ", raw_key).strip()[:96] or "untitled"
            key = f"{row['source_id']}::{norm_key}"
            item = dict(row)
            item["metadata"] = meta
            item["tags"] = json.loads(item.pop("tags_json") or "[]")
            item["snippet"] = (item.pop("content_preview") or item.get("summary") or "").replace("\n", " ")[:420]
            buckets.setdefault(key, []).append(item)
        groups = []
        min_group_size = max(2, int(min_group_size or 2))
        for key, items in buckets.items():
            if len(items) < min_group_size:
                continue
            source = items[0].get("source_id") or "unknown"
            state_counts: dict[str, int] = {}
            approval_counts: dict[str, int] = {}
            for item in items:
                state_counts[str(item.get("memory_state") or "active")] = state_counts.get(str(item.get("memory_state") or "active"), 0) + 1
                approval_counts[str(item.get("approval_state") or "not_required")] = approval_counts.get(str(item.get("approval_state") or "not_required"), 0) + 1
            digest = hashlib.sha1((key + "|" + "|".join(item["chunk_id"] for item in items[:20])).encode("utf-8", errors="ignore")).hexdigest()[:16]
            canonish = sum(1 for item in items if item.get("memory_state") == "canon" or item.get("retention_scope") == "canon")
            groups.append({
                "group_id": f"consolidate_{digest}",
                "group_key": key,
                "source_id": source,
                "title": items[0].get("title") or key,
                "chunk_count": len(items),
                "state_counts": state_counts,
                "approval_counts": approval_counts,
                "canonish_count": canonish,
                "recommended_action": "create_summary_keep_originals" if canonish else "create_summary_review_originals",
                "confidence": round(min(0.95, 0.45 + len(items) * 0.08 + canonish * 0.05), 3),
                "chunks": items[:8],
                "chunk_ids": [item["chunk_id"] for item in items],
            })
        groups.sort(key=lambda item: (item["chunk_count"], item["confidence"]), reverse=True)
        safe_limit = max(1, min(int(limit or 25), 100))
        return {"total": len(groups), "limit": safe_limit, "groups": groups[:safe_limit]}

    def create_consolidated_summary(self, *, chunk_ids: list[str], title: str = "", note: str = "", archive_originals: bool = False) -> dict[str, Any]:
        clean_ids = list(dict.fromkeys(str(item).strip() for item in (chunk_ids or []) if str(item or "").strip()))
        if len(clean_ids) < 2:
            return {"ok": False, "status": "need_at_least_two_chunks", "chunk_ids": clean_ids}
        chunks = self.get_chunks_by_ids(clean_ids)
        if len(chunks) < 2:
            return {"ok": False, "status": "chunks_not_found", "requested": clean_ids, "found": len(chunks)}
        now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat()
        source_id = "memory_consolidation"
        source_paths = [str(item.get("source_path") or "") for item in chunks]
        sources = sorted(set(str(item.get("source_id") or "unknown") for item in chunks))
        title = str(title or chunks[0].get("title") or "Consolidated memory summary").strip()
        summary_lines = [f"# {title}", "", "Consolidated from Memory Engine review. Originals remain auditable by chunk ID/source path.", ""]
        if note:
            summary_lines.extend([f"Admin note: {note}", ""])
        summary_lines.append("## Durable summary")
        seen = set()
        for idx, item in enumerate(chunks, start=1):
            snippet = str(item.get("summary") or item.get("content") or item.get("snippet") or "").strip().replace("\r", "")
            snippet = re.sub(r"\s+", " ", snippet)[:520]
            if snippet and snippet not in seen:
                seen.add(snippet)
                summary_lines.append(f"- {snippet}")
        summary_lines.extend(["", "## Source chunks"])
        for item in chunks:
            line = f"- {item.get('chunk_id')} · {item.get('source_id')} · {item.get('source_path')}"
            if item.get("start_line"):
                line += f":{item.get('start_line')}-{item.get('end_line') or item.get('start_line')}"
            summary_lines.append(line)
        content = "\n".join(summary_lines).strip()
        digest = hashlib.sha256(("|".join(clean_ids) + content).encode("utf-8", errors="ignore")).hexdigest()
        document_id = f"memory_consolidation::{digest[:24]}"
        source_path = f"neo_data/memory/consolidated/{digest[:16]}.md"
        metadata = {
            "consolidation_version": "memory-consolidation.v1",
            "source_chunk_ids": clean_ids,
            "source_ids": sources,
            "source_paths": source_paths,
            "archive_originals": bool(archive_originals),
            "admin_note": note,
        }
        policy = normalize_memory_policy(source_id, {"memory_state": "active", "retention_scope": "long_term", "trust_level": "confirmed", "importance": "high", "approval_state": "approved"})
        document = {
            "document_id": document_id,
            "source_id": source_id,
            "source_path": source_path,
            "title": title,
            "source_type": "memory_summary",
            "content_hash": digest,
            "status": "indexed",
            "visibility": policy["visibility"],
            "trust_level": policy["trust_level"],
            "policy": policy,
            "metadata": metadata,
            "updated_at": now,
            "indexed_at": now,
        }
        chunk = {
            "chunk_id": f"memory_consolidation_chunk::{digest[:24]}",
            "document_id": document_id,
            "source_id": source_id,
            "chunk_index": 0,
            "title": title,
            "content": content,
            "summary": f"Consolidated summary from {len(chunks)} memory chunks.",
            "tags": ["memory", "consolidation", "summary"],
            "source_path": source_path,
            "start_line": 1,
            "end_line": len(content.splitlines()),
            "content_hash": digest,
            "visibility": policy["visibility"],
            "trust_level": policy["trust_level"],
            "policy": policy,
            "searchable_text": " ".join([title, content, " ".join(clean_ids), " ".join(sources)]),
            "metadata": metadata,
            "updated_at": now,
        }
        # Register virtual consolidation source if it is not already in memory_sources.
        self.upsert_source({"source_id": source_id, "label": "Memory Consolidation", "source_type": "memory_summary", "root_path": "neo_data/memory/consolidated", "enabled": True, "index_policy": "review_write", "visibility": "expert", "trust_level": "confirmed", "priority": 85, "description": "Durable summaries created by Memory Consolidation."}, updated_at=now)
        self.upsert_document(document)
        self.replace_document_chunks(document_id, [chunk])
        updated_originals: list[dict[str, Any]] = []
        if archive_originals:
            for chunk_id in clean_ids:
                updated_originals.append(self.update_chunk_policy(chunk_id, {"memory_state": "archived", "importance": "low", "approval_state": "approved"}))
        return {"ok": True, "status": "summary_created", "summary_chunk_id": chunk["chunk_id"], "summary_document_id": document_id, "source_chunk_count": len(chunks), "archive_originals": bool(archive_originals), "updated_originals": updated_originals, "summary": self.get_chunk_detail(chunk["chunk_id"])}


    def list_retention_candidates(self, *, source_id: str | None = None, query: str = "", max_age_days: int = 30, limit: int = 50) -> dict[str, Any]:
        """Return review candidates for decay/retention maintenance.

        This is advisory only. It never mutates chunks. The service layer exposes
        explicit run actions for Admin-reviewed retention changes.
        """
        max_age_days = max(1, int(max_age_days or 30))
        safe_limit = max(1, min(int(limit or 50), 200))
        clauses = ["1 = 1"]
        values: list[object] = []
        if source_id:
            clauses.append("source_id = ?")
            values.append(str(source_id))
        if query:
            tokens = [part.strip() for part in str(query).replace('"', ' ').split() if part.strip()]
            if tokens:
                token_clauses = []
                for token in tokens[:8]:
                    token_clauses.append("searchable_text LIKE ?")
                    values.append(f"%{token}%")
                clauses.append("(" + " OR ".join(token_clauses) + ")")
        where = "WHERE " + " AND ".join(clauses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM memory_chunks
                {where}
                ORDER BY updated_at ASC, policy_score ASC
                LIMIT ?
                """,
                values + [max(500, safe_limit * 8)],
            ).fetchall()
        now = datetime.now(timezone.utc)
        candidates: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        for row in rows:
            item = self._row_to_chunk(row, query=query)
            age_days = None
            try:
                stamp = datetime.fromisoformat(str(item.get("updated_at") or "").replace("Z", "+00:00"))
                if stamp.tzinfo is None:
                    stamp = stamp.replace(tzinfo=timezone.utc)
                age_days = max(0, int((now - stamp).total_seconds() // 86400))
            except Exception:
                age_days = None
            metadata = dict(item.get("metadata") or {})
            decay_policy = str(metadata.get("decay_policy") or item.get("decay_policy") or "")
            # decay_policy usually lives in the normalized source policy rather than columns.
            source_policy = normalize_memory_policy(item.get("source_id"), {})
            decay_policy = decay_policy or str(source_policy.get("decay_policy") or "soft_decay")
            ttl_days = source_policy.get("ttl_days")
            try:
                ttl_days = int(ttl_days) if ttl_days not in (None, "") else None
            except Exception:
                ttl_days = None
            reasons: list[str] = []
            recommended_action = "mark_for_review"
            state = str(item.get("memory_state") or "active")
            retention = str(item.get("retention_scope") or "project")
            trust = str(item.get("trust_level") or "confirmed")
            approval = str(item.get("approval_state") or "not_required")
            policy_score = float(item.get("policy_score") or 0.5)
            protected = retention in {"system", "canon"} or state == "canon" or trust in {"system"}
            if item.get("source_id") in {"system_records", "neo_codebase", "admin_config", "extension_manifests", "surface_blueprints"}:
                protected = True
                if age_days is not None and age_days >= max_age_days:
                    reasons.append("source_backed_refresh_candidate")
                    recommended_action = "refresh_source"
            if ttl_days is not None and age_days is not None and age_days >= ttl_days:
                reasons.append("ttl_expired")
                recommended_action = "deprecate_stale_external" if item.get("source_id") == "internet_external" else "mark_for_review"
            if retention in {"temporary", "session"} and age_days is not None and age_days >= max_age_days:
                reasons.append("stale_temporary_or_session")
                recommended_action = "archive_temporary"
            if state in {"deprecated", "conflicting", "draft"}:
                reasons.append(f"state_{state}")
                recommended_action = "mark_for_review" if state != "deprecated" else "archive_temporary"
            if approval in {"pending", "rejected"}:
                reasons.append(f"approval_{approval}")
            if trust in {"mixed", "inferred", "draft", "conflicting", "deprecated"}:
                reasons.append(f"trust_{trust}")
            if policy_score <= 0.34:
                reasons.append("low_policy_score")
                recommended_action = "mark_for_review"
            if not reasons:
                continue
            if protected and recommended_action not in {"refresh_source", "mark_for_review"}:
                recommended_action = "mark_for_review"
                reasons.append("protected_memory_no_auto_archive")
            item.update({
                "age_days": age_days,
                "ttl_days": ttl_days,
                "decay_policy": decay_policy,
                "protected": bool(protected),
                "retention_reasons": reasons,
                "recommended_action": recommended_action,
                "content_preview": str(item.get("content") or item.get("summary") or "").replace("\n", " ")[:420],
            })
            candidates.append(item)
            counts[recommended_action] = counts.get(recommended_action, 0) + 1
        candidates.sort(key=lambda item: (0 if item.get("recommended_action") != "refresh_source" else 1, -(item.get("age_days") or 0), item.get("policy_score") or 0.5))
        return {"total": len(candidates), "limit": safe_limit, "max_age_days": max_age_days, "action_counts": counts, "candidates": candidates[:safe_limit]}

    def apply_retention_action(self, *, chunk_ids: list[str], action: str, note: str = "") -> dict[str, Any]:
        clean_ids = list(dict.fromkeys(str(item).strip() for item in (chunk_ids or []) if str(item or "").strip()))
        allowed = {"archive_temporary", "deprecate_stale_external", "mark_for_review", "keep"}
        action = str(action or "").strip()
        if action not in allowed:
            return {"ok": False, "status": "unknown_action", "action": action, "allowed_actions": sorted(allowed)}
        if not clean_ids:
            return {"ok": False, "status": "missing_chunks", "action": action}
        updated: list[dict[str, Any]] = []
        for chunk_id in clean_ids:
            detail = self.get_chunk_detail(chunk_id)
            if not detail:
                updated.append({"chunk_id": chunk_id, "ok": False, "status": "missing_chunk"})
                continue
            protected = detail.get("retention_scope") in {"system", "canon"} or detail.get("memory_state") == "canon" or detail.get("trust_level") == "system"
            if action == "keep":
                policy = {"approval_state": "approved", "importance": detail.get("importance") or "normal"}
            elif action == "mark_for_review":
                policy = {"approval_state": "pending", "importance": "normal"}
            elif action == "deprecate_stale_external":
                if detail.get("source_id") != "internet_external" and protected:
                    policy = {"approval_state": "pending", "importance": "high"}
                else:
                    policy = {"memory_state": "deprecated", "trust_level": "deprecated", "importance": "low", "approval_state": "rejected"}
            else:  # archive_temporary
                if protected:
                    policy = {"approval_state": "pending", "importance": "high"}
                else:
                    policy = {"memory_state": "archived", "importance": "low", "approval_state": "approved"}
            result = self.update_chunk_policy(chunk_id, policy)
            new_detail = self.get_chunk_detail(chunk_id)
            if new_detail:
                metadata = dict(new_detail.get("metadata") or {})
                metadata["last_retention_action"] = {"action": action, "note": note, "protected": bool(protected), "timestamp": datetime.now(timezone.utc).isoformat()}
                with self._connect() as conn:
                    conn.execute("UPDATE memory_chunks SET metadata_json = ? WHERE chunk_id = ?", (json.dumps(metadata), chunk_id))
                new_detail = self.get_chunk_detail(chunk_id)
            updated.append({"chunk_id": chunk_id, "ok": bool(result.get("ok")), "status": result.get("status"), "chunk": new_detail})
        return {"ok": True, "status": "retention_action_applied", "action": action, "updated_count": len(updated), "updated": updated}

    def diagnostics_snapshot(self, *, stale_days: int = 14, trace_limit: int = 12) -> dict[str, Any]:
        """Return compact Memory Engine health counters for Admin diagnostics.

        This stays SQLite-only so the dashboard works even when Chroma or local
        embedding dependencies are not installed.
        """
        with self._connect() as conn:
            source_rows = conn.execute("SELECT * FROM memory_sources ORDER BY priority DESC, source_id ASC").fetchall()
            doc_rows = conn.execute(
                """
                SELECT source_id, COUNT(*) AS document_count, MAX(indexed_at) AS last_indexed_at,
                       MAX(updated_at) AS last_updated_at
                FROM memory_documents
                GROUP BY source_id
                """
            ).fetchall()
            chunk_rows = conn.execute("SELECT source_id, COUNT(*) AS chunk_count FROM memory_chunks GROUP BY source_id").fetchall()
            event_rows = conn.execute("SELECT namespace, COUNT(*) AS count FROM memory_events GROUP BY namespace ORDER BY count DESC").fetchall()
            recent_event_rows = conn.execute(
                """
                SELECT namespace, surface, event_type, title, timestamp
                FROM memory_events
                ORDER BY timestamp DESC
                LIMIT 12
                """
            ).fetchall()
            trace_rows = conn.execute(
                """
                SELECT consumer, profile, COUNT(*) AS count, MAX(created_at) AS last_trace_at
                FROM memory_retrieval_traces
                GROUP BY consumer, profile
                ORDER BY count DESC
                """
            ).fetchall()
            recent_traces = conn.execute(
                "SELECT * FROM memory_retrieval_traces ORDER BY created_at DESC LIMIT ?",
                (max(1, min(int(trace_limit), 50)),),
            ).fetchall()
            policy_rows = conn.execute(
                """
                SELECT memory_state, trust_level, approval_state, COUNT(*) AS count
                FROM memory_chunks
                GROUP BY memory_state, trust_level, approval_state
                ORDER BY count DESC
                """
            ).fetchall()
        doc_by_source = {row["source_id"]: dict(row) for row in doc_rows}
        chunk_by_source = {row["source_id"]: int(row["chunk_count"]) for row in chunk_rows}
        sources = []
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        stale_seconds = max(1, int(stale_days)) * 86400
        for row in source_rows:
            sid = row["source_id"]
            doc = doc_by_source.get(sid, {})
            last_indexed = doc.get("last_indexed_at")
            age_days = None
            is_stale = False
            if last_indexed:
                try:
                    stamp = datetime.fromisoformat(str(last_indexed).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        stamp = stamp.replace(tzinfo=timezone.utc)
                    age_days = round(max(0.0, (now - stamp).total_seconds() / 86400.0), 2)
                    is_stale = (now - stamp).total_seconds() > stale_seconds
                except Exception:
                    is_stale = True
            enabled = bool(row["enabled"])
            document_count = int(doc.get("document_count") or 0)
            chunk_count = int(chunk_by_source.get(sid, 0))
            status = "ready" if document_count and not is_stale else ("stale" if document_count and is_stale else ("needs_index" if enabled else "planned"))
            sources.append({
                "source_id": sid,
                "label": row["label"],
                "enabled": enabled,
                "source_type": row["source_type"],
                "root_path": row["root_path"],
                "document_count": document_count,
                "chunk_count": chunk_count,
                "last_indexed_at": last_indexed,
                "age_days": age_days,
                "status": status,
            })
        parsed_traces = []
        total_rejected = 0
        blocked_or_empty = 0
        for row in recent_traces:
            meta = json.loads(row["metadata_json"] or "{}")
            rejected_count = int(meta.get("rejected_count") or 0)
            total_rejected += rejected_count
            results = json.loads(row["results_json"] or "[]")
            if not results:
                blocked_or_empty += 1
            parsed_traces.append({
                "trace_id": row["trace_id"],
                "query": row["query"],
                "consumer": row["consumer"],
                "profile": row["profile"],
                "result_count": len(results),
                "rejected_count": rejected_count,
                "backend_used": meta.get("backend_used") or "unknown",
                "created_at": row["created_at"],
            })
        policy_counts = [dict(row) for row in policy_rows]
        policy_alerts = []
        for item in policy_counts:
            if item.get("memory_state") in {"conflicting", "deprecated"} or item.get("approval_state") in {"pending", "rejected"} or item.get("trust_level") in {"conflicting", "deprecated"}:
                policy_alerts.append(item)
        return {
            "source_health": sources,
            "event_counts_by_namespace": {row["namespace"]: row["count"] for row in event_rows},
            "recent_events": [dict(row) for row in recent_event_rows],
            "retrieval_counts_by_consumer_profile": [dict(row) for row in trace_rows],
            "recent_retrieval_traces": parsed_traces,
            "retrieval_quality": {
                "recent_trace_count": len(parsed_traces),
                "recent_rejected_count": total_rejected,
                "recent_empty_trace_count": blocked_or_empty,
            },
            "policy_counts": policy_counts,
            "policy_alerts": policy_alerts[:20],
        }

    def write_retrieval_trace(self, trace: dict[str, Any]) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_retrieval_traces (
                    trace_id, query, consumer, profile, sources_json, results_json, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace["trace_id"], trace.get("query") or "", trace.get("consumer") or "assistant", trace.get("profile") or "smart",
                    json.dumps(trace.get("sources") or []), json.dumps(trace.get("results") or []), trace["created_at"],
                    json.dumps(trace.get("metadata") or {}),
                ),
            )
        return trace

    def _row_to_chunk(self, row: sqlite3.Row, *, query: str = "") -> dict[str, Any]:
        text = row["searchable_text"] or ""
        q_tokens = {part.lower() for part in str(query or "").split() if part}
        hay = text.lower()
        overlap = sum(1 for token in q_tokens if token in hay)
        score = round(overlap / max(1, len(q_tokens)), 6) if q_tokens else 0.25
        return {
            "chunk_id": row["chunk_id"],
            "document_id": row["document_id"],
            "source_id": row["source_id"],
            "chunk_index": row["chunk_index"],
            "title": row["title"],
            "content": row["content"],
            "summary": row["summary"] or "",
            "tags": json.loads(row["tags_json"] or "[]"),
            "source_path": row["source_path"],
            "content_hash": row["content_hash"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "visibility": row["visibility"],
            "trust_level": row["trust_level"],
            "retention_scope": row["retention_scope"],
            "memory_state": row["memory_state"],
            "importance": row["importance"],
            "approval_state": row["approval_state"],
            "policy_score": row["policy_score"],
            "score": score,
            "retrieval_type": "keyword",
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "updated_at": row["updated_at"],
        }

    def _row_to_event(self, row: sqlite3.Row) -> MemoryEvent:
        return MemoryEvent(
            event_id=row["event_id"],
            timestamp=row["timestamp"],
            namespace=row["namespace"],
            surface=row["surface"],
            subtab=row["subtab"],
            source=row["source"],
            event_type=row["event_type"],
            title=row["title"],
            summary=row["summary"] or "",
            project_id=row["project_id"],
            extension_id=row["extension_id"],
            provider_id=row["provider_id"],
            family=row["family"],
            loader=row["loader"],
            tags=json.loads(row["tags_json"]),
            payload=json.loads(row["payload_json"]),
            importance=row["importance"],
            should_embed=bool(row["should_embed"]),
        )
