from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.sandbox_contract import (
    context_json,
    context_scope_id,
    derive_sandbox_context,
)
from neo_app.roleplay.sqlite_store import _connect, _json, ensure_roleplay_memory_schema, roleplay_sqlite_state_payload
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.studio import list_studio_sources

NOVEL_SCHEMA_ID = "neo.roleplay.novel_memory_path.v1"
NOVEL_VERSION = "0.1.0-phase5-novel-system-memory-path"
NOVEL_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "novel_memory_path_contract.json"
CANON_RECORDS_DIR = ROLEPLAY_DATA_ROOT / "canon_records"
BREAKDOWNS_DIR = ROLEPLAY_DATA_ROOT / "source_breakdowns"
_MAX_CHUNK_CHARS = 1800
_MIN_CHUNK_CHARS = 360


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _slug(value: Any, default: str = "item") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "-", _clean(value).lower()).strip("-")
    return cleaned[:80] or default


def _stable_id(*parts: Any, prefix: str = "novel") -> str:
    base = "|".join(_clean(part) for part in parts)
    digest = hashlib.blake2b(base.encode("utf-8"), digest_size=8).hexdigest()
    readable = _slug(parts[-1] if parts else "row", "row")[:42]
    return f"{prefix}:{readable}:{digest}"


def _read_source_body(source_id: str) -> dict[str, Any] | None:
    ensure_roleplay_foundation(write_manifest=True)
    for path in (ROLEPLAY_DATA_ROOT / "source_documents").glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if _clean(data.get("source_id")) == _clean(source_id):
            data["_storage_path"] = _relative_to_root(path)
            return data
    return None


def _all_sources(project_id: str = "") -> list[dict[str, Any]]:
    rows = []
    for source in list_studio_sources(project_id or None):
        data = model_to_dict(source)
        stored = _read_source_body(data.get("source_id", "")) or {}
        data.update({k: v for k, v in stored.items() if k not in {"source_id", "created_at", "updated_at"}})
        data.setdefault("body", stored.get("body", ""))
        data.setdefault("meta", stored.get("meta", {}))
        rows.append(data)
    return rows


def _paragraph_chunks(text: str, *, max_chars: int = _MAX_CHUNK_CHARS) -> list[str]:
    clean = re.sub(r"\r\n?", "\n", text or "").strip()
    if not clean:
        return []
    parts = [p.strip() for p in re.split(r"\n\s*\n+", clean) if p.strip()]
    chunks: list[str] = []
    current = ""
    for part in parts:
        if len(part) > max_chars:
            sentences = re.split(r"(?<=[.!?。！？])\s+", part)
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if current and len(current) + len(sentence) + 1 > max_chars:
                    chunks.append(current.strip())
                    current = ""
                current = (current + " " + sentence).strip()
            continue
        if current and len(current) + len(part) + 2 > max_chars and len(current) >= _MIN_CHUNK_CHARS:
            chunks.append(current.strip())
            current = part
        else:
            current = (current + "\n\n" + part).strip() if current else part
    if current.strip():
        chunks.append(current.strip())
    return chunks


def _source_context(source: dict[str, Any]) -> dict[str, str]:
    meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
    payload = {
        "id": source.get("source_id") or "",
        "kind": "source_document",
        "project_id": source.get("project_id") or meta.get("project_id") or "",
        "source_record_id": source.get("source_id") or "",
        "source_record_kind": "source_document",
        "memory_scope": "source_document",
        "promotion_scope": meta.get("promotion_scope") or "draft",
        "canon_snapshot_id": meta.get("canon_snapshot_id") or "",
        "storyline_id": meta.get("storyline_id") or "",
        "session_id": meta.get("session_id") or "",
        "branch_id": meta.get("branch_id") or "",
        "links": {"scope": {
            "project_id": source.get("project_id") or "",
            "universe_id": meta.get("universe_id") or "",
            "world_id": meta.get("world_id") or "",
            "region_id": meta.get("region_id") or "",
            "city_id": meta.get("city_id") or "",
            "location_id": meta.get("location_id") or "",
        }},
        "meta": meta,
    }
    ctx = derive_sandbox_context(payload, record_id=source.get("source_id") or "", kind="source_document")
    ctx["source_record_id"] = source.get("source_id") or ""
    ctx["source_record_kind"] = "source_document"
    ctx["memory_scope"] = "source_document"
    ctx["promotion_scope"] = meta.get("promotion_scope") or "draft"
    if source.get("project_id") and not ctx.get("project_id"):
        ctx["project_id"] = source.get("project_id") or ""
    if not ctx.get("sandbox_id"):
        ctx["sandbox_id"] = ctx.get("project_id") or ctx.get("world_id") or source.get("source_id") or "global"
    return ctx


def _memory_type_for_chunk(source_type: str, meta: dict[str, Any], chunk_text: str) -> str:
    joined = f"{source_type} {json.dumps(meta, ensure_ascii=False)} {chunk_text[:320]}".lower()
    if any(token in joined for token in ("continuity", "must remember", "canon", "rule", "cannot", "forbidden", "never")):
        return "continuity_rule"
    if any(token in joined for token in ("relationship", "romance", "trust", "betray", "kiss", "bond")):
        return "relationship_shift"
    if any(token in joined for token in ("world", "city", "kingdom", "magic", "law", "faith")):
        return "world_lore"
    if any(token in joined for token in ("chapter", "scene", "pov", "beat")):
        return "scene_summary"
    return "canon_fact"


def build_source_breakdown(source: dict[str, Any]) -> dict[str, Any]:
    source_id = _clean(source.get("source_id"))
    title = _clean(source.get("title")) or source_id or "Untitled source"
    body = _clean(source.get("body"))
    meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
    chunks = _paragraph_chunks(body)
    context = _source_context(source)
    scope_id = context_scope_id(context)
    records = []
    for index, chunk in enumerate(chunks, start=1):
        memory_type = _memory_type_for_chunk(source.get("source_type") or meta.get("document_type") or "source", meta, chunk)
        chunk_id = _stable_id(source_id, index, chunk[:120], prefix="source-chunk")
        records.append({
            "chunk_id": chunk_id,
            "source_id": source_id,
            "project_id": source.get("project_id") or "",
            "chunk_index": index,
            "title": f"{title} · chunk {index}",
            "content": chunk,
            "memory_type": memory_type,
            "status": "draft_breakdown",
            "scope_id": scope_id,
            "sandbox_context": context,
            "meta": {
                "document_type": source.get("source_type") or meta.get("document_type") or "text",
                "chapter_number": meta.get("chapter_number") or meta.get("chapter") or "",
                "scene_number": meta.get("scene_number") or meta.get("scene") or "",
                "order_index": meta.get("order_index") or "",
                "pov": meta.get("pov") or "",
                "part_arc": meta.get("part_arc") or "",
                "source_title": title,
            },
        })
    return {
        "schema_id": "neo.roleplay.novel.source_breakdown.v1",
        "source_id": source_id,
        "title": title,
        "project_id": source.get("project_id") or "",
        "status": "ready" if records else "empty",
        "chunk_count": len(records),
        "scope_id": scope_id,
        "sandbox_context": context,
        "chunks": records,
    }


def _apply_sandbox_update(conn: sqlite3.Connection, table: str, key_column: str, key_value: str, context: dict[str, str]) -> None:
    conn.execute(
        f"""
        UPDATE {table}
        SET project_id=?, sandbox_id=?, universe_id=?, world_id=?, region_id=?, city_id=?, location_id=?,
            source_record_id=?, source_record_kind=?, canon_snapshot_id=?, storyline_id=?, session_id=?, branch_id=?,
            memory_scope=?, promotion_scope=?, sandbox_json=?
        WHERE {key_column}=?
        """,
        (
            context.get("project_id", ""), context.get("sandbox_id", ""), context.get("universe_id", ""), context.get("world_id", ""),
            context.get("region_id", ""), context.get("city_id", ""), context.get("location_id", ""), context.get("source_record_id", ""),
            context.get("source_record_kind", ""), context.get("canon_snapshot_id", ""), context.get("storyline_id", ""), context.get("session_id", ""),
            context.get("branch_id", ""), context.get("memory_scope", "source_document"), context.get("promotion_scope", "draft"), context_json(context), key_value,
        ),
    )


def ensure_novel_memory_schema() -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    CANON_RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    BREAKDOWNS_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
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
            """
        )
        from neo_app.roleplay.sandbox_contract import ensure_roleplay_sandbox_schema
        ensure_roleplay_sandbox_schema(conn)
        conn.commit()
    return novel_memory_state_payload()


def compile_source_document_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_novel_memory_schema()
    source_id = _clean(payload.get("source_id"))
    source = payload.get("source") if isinstance(payload.get("source"), dict) else None
    if not source and source_id:
        source = _read_source_body(source_id)
    if not source:
        raise ValueError(f"Source document not found: {source_id or '(missing source_id)'}")
    now = _now()
    breakdown = build_source_breakdown(source)
    context = breakdown.get("sandbox_context") or {}
    scope_id = breakdown.get("scope_id") or context_scope_id(context)
    delete_existing = bool(payload.get("delete_existing", True))
    promote_to_memory = payload.get("promote_to_memory", True) is not False
    promote_to_canon = payload.get("promote_to_canon", False) is True
    source_id = breakdown["source_id"]
    meta = source.get("meta") if isinstance(source.get("meta"), dict) else {}
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_source_documents(source_id, project_id, title, source_type, status, body_preview, storage_path, meta_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id) DO UPDATE SET project_id=excluded.project_id, title=excluded.title, source_type=excluded.source_type, status=excluded.status, body_preview=excluded.body_preview, storage_path=excluded.storage_path, meta_json=excluded.meta_json, updated_at=excluded.updated_at
            """,
            (source_id, source.get("project_id") or "", source.get("title") or source_id, source.get("source_type") or meta.get("document_type") or "text", meta.get("draft_status") or "draft", (source.get("body") or "")[:360], source.get("_storage_path") or source.get("storage_path") or "", _json(meta), source.get("created_at") or now, now),
        )
        _apply_sandbox_update(conn, "rp_source_documents", "source_id", source_id, context)
        if delete_existing:
            old_chunk_ids = [str(row[0]) for row in conn.execute("SELECT chunk_id FROM rp_source_chunks WHERE source_id=?", (source_id,)).fetchall()]
            old_canon_ids = [str(row[0]) for row in conn.execute("SELECT canon_id FROM rp_canon_records WHERE source_id=?", (source_id,)).fetchall()]
            conn.execute("DELETE FROM rp_source_chunks WHERE source_id=?", (source_id,))
            conn.execute("DELETE FROM rp_canon_records WHERE source_id=?", (source_id,))
            conn.execute("DELETE FROM rp_memory_fragments WHERE source_type='source_document' AND source_id=?", (source_id,))
            for row_id in old_chunk_ids + old_canon_ids:
                conn.execute("DELETE FROM rp_vector_index WHERE source_id=?", (row_id,))
        for chunk in breakdown["chunks"]:
            conn.execute(
                """
                INSERT INTO rp_source_chunks(chunk_id, source_id, project_id, chunk_index, title, content, memory_type, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chunk_id) DO UPDATE SET title=excluded.title, content=excluded.content, memory_type=excluded.memory_type, status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (chunk["chunk_id"], source_id, source.get("project_id") or "", chunk["chunk_index"], chunk["title"], chunk["content"], chunk["memory_type"], chunk["status"], _json(chunk), now, now),
            )
            _apply_sandbox_update(conn, "rp_source_chunks", "chunk_id", chunk["chunk_id"], context)
            if promote_to_memory:
                fragment_id = _stable_id(source_id, chunk["chunk_id"], "fragment", prefix="novel-frag")
                conn.execute(
                    """
                    INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(fragment_id) DO UPDATE SET namespace=excluded.namespace, memory_type=excluded.memory_type, status=excluded.status, content=excluded.content, tags_json=excluded.tags_json, payload_json=excluded.payload_json, vector_status=excluded.vector_status, updated_at=excluded.updated_at
                    """,
                    (fragment_id, f"novel.source.{chunk['memory_type']}", "source_document", source_id, chunk["memory_type"], "compiled_source_document", f"{chunk['title']}\n{chunk['content']}", _json(["novel", "source_document", chunk["memory_type"]]), _json(chunk), "not_indexed", now, now),
                )
                _apply_sandbox_update(conn, "rp_memory_fragments", "fragment_id", fragment_id, context)
            if promote_to_canon:
                canon_id = _stable_id(source_id, chunk["chunk_id"], "canon", prefix="canon")
                conn.execute(
                    """
                    INSERT INTO rp_canon_records(canon_id, source_id, project_id, canon_type, title, content, status, payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(canon_id) DO UPDATE SET canon_type=excluded.canon_type, title=excluded.title, content=excluded.content, status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                    """,
                    (canon_id, source_id, source.get("project_id") or "", chunk["memory_type"], chunk["title"], chunk["content"], "candidate_canon", _json(chunk), now, now),
                )
                _apply_sandbox_update(conn, "rp_canon_records", "canon_id", canon_id, context)
        conn.commit()
    BREAKDOWNS_DIR.mkdir(parents=True, exist_ok=True)
    breakdown_path = BREAKDOWNS_DIR / f"{_slug(source_id)}.breakdown.json"
    breakdown_path.write_text(json.dumps(breakdown, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {
        "schema_id": "neo.roleplay.novel.compile_source_document.v1",
        "status": "compiled" if breakdown["chunks"] else "empty",
        "source_id": source_id,
        "project_id": source.get("project_id") or "",
        "chunk_count": len(breakdown["chunks"]),
        "memory_fragment_count": len(breakdown["chunks"]) if promote_to_memory else 0,
        "canon_record_count": len(breakdown["chunks"]) if promote_to_canon else 0,
        "scope_id": scope_id,
        "sandbox_context": context,
        "breakdown_path": _relative_to_root(breakdown_path),
        "sqlite": roleplay_sqlite_state_payload(),
        "next_step": "Run vector indexing, then Runtime retrieval. Promote to canon only after review.",
    }


def compile_all_source_documents_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_novel_memory_schema()
    payload = payload or {}
    project_id = _clean(payload.get("project_id"))
    sources = _all_sources(project_id)
    compiled: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    total_chunks = 0
    total_memory = 0
    total_canon = 0
    for source in sources:
        try:
            result = compile_source_document_payload({
                "source": source,
                "delete_existing": payload.get("delete_existing", True),
                "promote_to_memory": payload.get("promote_to_memory", True),
                "promote_to_canon": payload.get("promote_to_canon", False),
            })
            compiled.append(result)
            total_chunks += int(result.get("chunk_count") or 0)
            total_memory += int(result.get("memory_fragment_count") or 0)
            total_canon += int(result.get("canon_record_count") or 0)
        except Exception as exc:
            errors.append({"source_id": _clean(source.get("source_id")), "title": _clean(source.get("title")), "error": str(exc)})
    return {
        "schema_id": "neo.roleplay.novel.compile_all_source_documents.v1",
        "status": "compiled" if not errors else "partial",
        "project_id": project_id or "all",
        "source_count": len(sources),
        "compiled_count": len(compiled),
        "error_count": len(errors),
        "chunk_count": total_chunks,
        "memory_fragment_count": total_memory,
        "canon_record_count": total_canon,
        "compiled": compiled[:100],
        "errors": errors,
        "sqlite": roleplay_sqlite_state_payload(),
        "novel": novel_memory_state_payload(),
    }


def _count(conn, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def novel_memory_state_payload() -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    CANON_RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    BREAKDOWNS_DIR.mkdir(parents=True, exist_ok=True)
    counts = {"rp_source_documents": 0, "rp_source_chunks": 0, "rp_canon_records": 0}
    try:
        with _connect() as conn:
            for table in list(counts):
                counts[table] = _count(conn, table)
    except Exception:
        pass
    return {
        "schema_id": NOVEL_SCHEMA_ID,
        "version": NOVEL_VERSION,
        "phase": "Phase 5 — Novel System Memory Path",
        "status": "implemented",
        "source_count": counts["rp_source_documents"],
        "chunk_count": counts["rp_source_chunks"],
        "canon_record_count": counts["rp_canon_records"],
        "breakdown_root": _relative_to_root(BREAKDOWNS_DIR),
        "canon_root": _relative_to_root(CANON_RECORDS_DIR),
        "tables": counts,
        "pipeline": ["Project", "Source", "Generate breakdown", "Compile source memory", "Index retrieval", "Runtime", "Scene"],
        "mode_note": "Roleplay Builder records and Novel source documents share SQLite/retrieval infrastructure, but keep source_type/sandbox scope separate.",
    }


def novel_memory_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    state = novel_memory_state_payload()
    contract = {
        "schema_id": NOVEL_SCHEMA_ID,
        "version": NOVEL_VERSION,
        "status": "implemented",
        "purpose": "Add the V2 Novel/source-document memory path alongside interactive Roleplay Builder memory.",
        "inputs": ["Studio projects", "Studio source documents", "chapter/scene metadata", "author notes"],
        "outputs": ["rp_source_documents", "rp_source_chunks", "rp_canon_records", "rp_memory_fragments with source_type=source_document"],
        "source_types": ["novel_chapter", "novel_scene_section", "novel_outline", "author_notes", "reference_excerpt"],
        "memory_types": ["canon_fact", "continuity_rule", "character_state", "relationship_shift", "world_lore", "timeline_event", "foreshadowing_anchor", "scene_summary"],
        "sandboxing": "Source chunks and compiled memory fragments receive Phase 2 sandbox columns and sandbox_json.",
        "canon_policy": "Compile defaults to candidate memory. Canon records are only written when promote_to_canon=true.",
        "api_endpoints": {
            "contract": "GET /api/roleplay/novel/memory-contract",
            "state": "GET /api/roleplay/novel/state",
            "compile_one": "POST /api/roleplay/novel/compile-source-document",
            "compile_all": "POST /api/roleplay/novel/compile-all-source-documents",
        },
        "state": state,
        "next_required_phase": "Phase 6 — Source Document + Canon Breakdown Pipeline",
    }
    if write_report:
        NOVEL_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        NOVEL_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract
