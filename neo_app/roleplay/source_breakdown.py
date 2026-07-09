from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from neo_app.roleplay.novel_memory import (
    BREAKDOWNS_DIR,
    CANON_RECORDS_DIR,
    _all_sources,
    _clean,
    _read_source_body,
    _relative_to_root,
    _slug,
    build_source_breakdown,
    ensure_novel_memory_schema,
)
from neo_app.roleplay.sandbox_contract import SANDBOX_COLUMNS, context_json, context_scope_id
from neo_app.roleplay.sqlite_store import _connect, _json, roleplay_sqlite_state_payload
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ensure_roleplay_foundation

CANON_BREAKDOWN_SCHEMA_ID = "neo.roleplay.source_canon_breakdown.v1"
CANON_BREAKDOWN_VERSION = "0.1.0-phase6-source-document-canon-breakdown"
CANON_BREAKDOWN_CONTRACT_PATH = ROLEPLAY_DATA_ROOT / "source_canon_breakdown_contract.json"


CANON_TYPES = (
    "canon_fact",
    "continuity_rule",
    "character_state",
    "relationship_shift",
    "world_lore",
    "timeline_event",
    "foreshadowing_anchor",
    "unresolved_thread",
    "scene_summary",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_id(*parts: Any, prefix: str = "canon-breakdown") -> str:
    base = "|".join(_clean(part) for part in parts)
    digest = hashlib.blake2b(base.encode("utf-8"), digest_size=8).hexdigest()
    readable = _slug(parts[-1] if parts else "row", "row")[:42]
    return f"{prefix}:{readable}:{digest}"


def _keywords(text: str, limit: int = 14) -> list[str]:
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "their", "there", "then", "when", "what", "were", "was", "are", "his", "her", "him", "she", "they", "them", "you", "your", "but", "not", "all", "had", "has", "have", "will", "would", "could", "should", "about", "after", "before", "under", "over",
    }
    words = re.findall(r"[A-Za-z][A-Za-z'_-]{2,}", text or "")
    counts: dict[str, int] = {}
    original: dict[str, str] = {}
    for word in words:
        key = word.lower().strip("'_-.")
        if len(key) < 3 or key in stop:
            continue
        counts[key] = counts.get(key, 0) + 1
        original.setdefault(key, word.strip("'_-.")[:40])
    ranked = sorted(counts, key=lambda key: (-counts[key], key))[:limit]
    return [original[key] for key in ranked]


def _canonical_type(memory_type: str, content: str) -> str:
    joined = f"{memory_type} {content[:800]}".lower()
    if any(token in joined for token in ("foreshadow", "omen", "promise", "symbol", "echo", "recurring")):
        return "foreshadowing_anchor"
    if any(token in joined for token in ("unresolved", "mystery", "unknown", "secret", "not yet", "missing", "question")):
        return "unresolved_thread"
    if any(token in joined for token in ("timeline", "year", "era", "before", "after", "during", "then")):
        return "timeline_event"
    if any(token in joined for token in ("relationship", "romance", "trust", "betray", "bond", "partner", "couple")):
        return "relationship_shift"
    if any(token in joined for token in ("character", "pov", "he ", "she ", "they ", "wants", "fears", "believes")):
        return "character_state"
    if any(token in joined for token in ("city", "kingdom", "world", "magic", "law", "faith", "region", "guild")):
        return "world_lore"
    if any(token in joined for token in ("rule", "must", "cannot", "forbidden", "never", "continuity", "canon")):
        return "continuity_rule"
    if any(token in joined for token in ("scene", "chapter", "beat", "opening", "closing")):
        return "scene_summary"
    return memory_type if memory_type in CANON_TYPES else "canon_fact"


def _extract_candidate_records(source: dict[str, Any], chunk: dict[str, Any]) -> list[dict[str, Any]]:
    content = _clean(chunk.get("content"))
    title = _clean(chunk.get("title")) or _clean(source.get("title")) or "Source chunk"
    if not content:
        return []
    ctype = _canonical_type(_clean(chunk.get("memory_type")), content)
    sentence_parts = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|\n+", content) if part.strip()]
    summary = " ".join(sentence_parts[:3]).strip() or content[:500]
    summary = summary[:900]
    keywords = _keywords(content)
    confidence = "medium"
    if any(token in content.lower() for token in ("must", "canon", "never", "cannot", "secret", "revealed", "truth")):
        confidence = "high"
    candidate_id = _stable_id(source.get("source_id"), chunk.get("chunk_id"), ctype, summary[:120], prefix="canon-candidate")
    return [{
        "candidate_id": candidate_id,
        "source_id": source.get("source_id") or chunk.get("source_id") or "",
        "source_chunk_id": chunk.get("chunk_id") or "",
        "project_id": source.get("project_id") or chunk.get("project_id") or "",
        "canon_type": ctype,
        "title": f"{title} · {ctype.replace('_', ' ')}",
        "content": summary,
        "status": "draft_breakdown",
        "confidence": confidence,
        "keywords": keywords,
        "evidence": content[:1600],
        "review_notes": "",
        "sandbox_context": chunk.get("sandbox_context") or {},
        "source_meta": chunk.get("meta") or {},
    }]


def ensure_source_breakdown_schema() -> dict[str, Any]:
    ensure_novel_memory_schema()
    BREAKDOWNS_DIR.mkdir(parents=True, exist_ok=True)
    CANON_RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS rp_source_breakdowns (
                breakdown_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft_breakdown',
                chunk_count INTEGER NOT NULL DEFAULT 0,
                candidate_count INTEGER NOT NULL DEFAULT 0,
                payload_json TEXT NOT NULL DEFAULT '{}',
                storage_path TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS rp_canon_candidates (
                candidate_id TEXT PRIMARY KEY,
                breakdown_id TEXT NOT NULL DEFAULT '',
                source_id TEXT NOT NULL DEFAULT '',
                source_chunk_id TEXT NOT NULL DEFAULT '',
                project_id TEXT NOT NULL DEFAULT '',
                canon_type TEXT NOT NULL DEFAULT 'canon_fact',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'draft_breakdown',
                confidence TEXT NOT NULL DEFAULT 'medium',
                evidence TEXT NOT NULL DEFAULT '',
                review_notes TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_rp_source_breakdowns_source ON rp_source_breakdowns(source_id, status);
            CREATE INDEX IF NOT EXISTS idx_rp_canon_candidates_breakdown ON rp_canon_candidates(breakdown_id, status);
            CREATE INDEX IF NOT EXISTS idx_rp_canon_candidates_source ON rp_canon_candidates(source_id, canon_type);
            """
        )
        from neo_app.roleplay.sandbox_contract import ensure_roleplay_sandbox_schema
        ensure_roleplay_sandbox_schema(conn)
        for table in ("rp_source_breakdowns", "rp_canon_candidates"):
            existing = {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for column, ddl in SANDBOX_COLUMNS.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            for column in ("sandbox_id", "world_id", "project_id", "storyline_id", "session_id", "branch_id", "promotion_scope", "memory_scope"):
                conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_{column} ON {table}({column})")
        conn.commit()
    return source_breakdown_state_payload()


def _apply_sandbox_update(conn: sqlite3.Connection, table: str, key_column: str, key_value: str, context: dict[str, str]) -> None:
    try:
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
                context.get("source_record_kind", "source_document"), context.get("canon_snapshot_id", ""), context.get("storyline_id", ""), context.get("session_id", ""),
                context.get("branch_id", ""), context.get("memory_scope", "source_document"), context.get("promotion_scope", "draft"), context_json(context), key_value,
            ),
        )
    except Exception:
        # Tables are additive-migrated; older workspaces may not yet have Phase 2 columns.
        pass


def generate_source_breakdown_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_source_breakdown_schema()
    source_id = _clean(payload.get("source_id"))
    source = payload.get("source") if isinstance(payload.get("source"), dict) else None
    if not source and source_id:
        source = _read_source_body(source_id)
    if not source:
        raise ValueError(f"Source document not found: {source_id or '(missing source_id)'}")
    source_id = _clean(source.get("source_id")) or source_id
    base = build_source_breakdown(source)
    candidates: list[dict[str, Any]] = []
    for chunk in base.get("chunks", []):
        candidates.extend(_extract_candidate_records(source, chunk))
    breakdown_id = _stable_id(source_id, source.get("updated_at") or source.get("title") or "breakdown", prefix="source-breakdown")
    status = "ready_for_review" if candidates else "empty"
    now = _now()
    breakdown = {
        "schema_id": CANON_BREAKDOWN_SCHEMA_ID,
        "version": CANON_BREAKDOWN_VERSION,
        "breakdown_id": breakdown_id,
        "source_id": source_id,
        "project_id": source.get("project_id") or "",
        "title": source.get("title") or source_id,
        "status": status,
        "chunk_count": len(base.get("chunks", [])),
        "candidate_count": len(candidates),
        "scope_id": base.get("scope_id") or context_scope_id(base.get("sandbox_context") or {}),
        "sandbox_context": base.get("sandbox_context") or {},
        "chunks": base.get("chunks", []),
        "candidates": candidates,
        "review_policy": {
            "default_candidate_status": "draft_breakdown",
            "canon_promotion_is_explicit": True,
            "approved_candidates_write_to": ["rp_canon_records", "rp_memory_fragments"],
        },
        "created_at": now,
        "updated_at": now,
    }
    path = BREAKDOWNS_DIR / f"{_slug(source_id)}.canon_breakdown.json"
    path.write_text(json.dumps(breakdown, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    ctx = base.get("sandbox_context") or {}
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO rp_source_breakdowns(breakdown_id, source_id, project_id, title, status, chunk_count, candidate_count, payload_json, storage_path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(breakdown_id) DO UPDATE SET status=excluded.status, chunk_count=excluded.chunk_count, candidate_count=excluded.candidate_count, payload_json=excluded.payload_json, storage_path=excluded.storage_path, updated_at=excluded.updated_at
            """,
            (breakdown_id, source_id, source.get("project_id") or "", source.get("title") or source_id, status, len(base.get("chunks", [])), len(candidates), _json(breakdown), _relative_to_root(path), now, now),
        )
        _apply_sandbox_update(conn, "rp_source_breakdowns", "breakdown_id", breakdown_id, ctx)
        conn.execute("DELETE FROM rp_canon_candidates WHERE breakdown_id=?", (breakdown_id,))
        for candidate in candidates:
            cctx = candidate.get("sandbox_context") or ctx
            conn.execute(
                """
                INSERT INTO rp_canon_candidates(candidate_id, breakdown_id, source_id, source_chunk_id, project_id, canon_type, title, content, status, confidence, evidence, review_notes, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET breakdown_id=excluded.breakdown_id, canon_type=excluded.canon_type, title=excluded.title, content=excluded.content, status=excluded.status, confidence=excluded.confidence, evidence=excluded.evidence, review_notes=excluded.review_notes, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (candidate["candidate_id"], breakdown_id, source_id, candidate.get("source_chunk_id") or "", candidate.get("project_id") or "", candidate.get("canon_type") or "canon_fact", candidate.get("title") or "Canon candidate", candidate.get("content") or "", candidate.get("status") or "draft_breakdown", candidate.get("confidence") or "medium", candidate.get("evidence") or "", candidate.get("review_notes") or "", _json(candidate), now, now),
            )
            _apply_sandbox_update(conn, "rp_canon_candidates", "candidate_id", candidate["candidate_id"], cctx)
        conn.commit()
    return {
        "schema_id": CANON_BREAKDOWN_SCHEMA_ID,
        "status": status,
        "breakdown_id": breakdown_id,
        "source_id": source_id,
        "project_id": source.get("project_id") or "",
        "chunk_count": len(base.get("chunks", [])),
        "candidate_count": len(candidates),
        "storage_path": _relative_to_root(path),
        "breakdown": breakdown,
        "next_step": "Review candidates, then approve selected/all candidates into canon memory.",
    }


def _load_breakdown_from_db_or_file(breakdown_id: str = "", source_id: str = "") -> dict[str, Any] | None:
    ensure_source_breakdown_schema()
    with _connect() as conn:
        row = None
        if breakdown_id:
            row = conn.execute("SELECT payload_json FROM rp_source_breakdowns WHERE breakdown_id=?", (breakdown_id,)).fetchone()
        if not row and source_id:
            row = conn.execute("SELECT payload_json FROM rp_source_breakdowns WHERE source_id=? ORDER BY updated_at DESC LIMIT 1", (source_id,)).fetchone()
        if row:
            try:
                return json.loads(row[0] or "{}")
            except Exception:
                return None
    if source_id:
        path = BREAKDOWNS_DIR / f"{_slug(source_id)}.canon_breakdown.json"
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
    return None


def _candidate_rows(breakdown_id: str, statuses: list[str] | None = None) -> list[dict[str, Any]]:
    ensure_source_breakdown_schema()
    with _connect() as conn:
        if statuses:
            marks = ",".join("?" for _ in statuses)
            rows = conn.execute(f"SELECT payload_json, status FROM rp_canon_candidates WHERE breakdown_id=? AND status IN ({marks}) ORDER BY canon_type, title", (breakdown_id, *statuses)).fetchall()
        else:
            rows = conn.execute("SELECT payload_json, status FROM rp_canon_candidates WHERE breakdown_id=? ORDER BY canon_type, title", (breakdown_id,)).fetchall()
    candidates = []
    for payload_json, status in rows:
        try:
            item = json.loads(payload_json or "{}")
        except Exception:
            item = {}
        item["status"] = status or item.get("status") or "draft_breakdown"
        candidates.append(item)
    return candidates


def list_source_breakdowns_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_source_breakdown_schema()
    payload = payload or {}
    project_id = _clean(payload.get("project_id"))
    source_id = _clean(payload.get("source_id"))
    limit = int(payload.get("limit") or 50)
    sql = "SELECT breakdown_id, source_id, project_id, title, status, chunk_count, candidate_count, storage_path, created_at, updated_at FROM rp_source_breakdowns"
    params: list[Any] = []
    where = []
    if project_id:
        where.append("project_id=?")
        params.append(project_id)
    if source_id:
        where.append("source_id=?")
        params.append(source_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = [
        {
            "breakdown_id": row[0], "source_id": row[1], "project_id": row[2], "title": row[3], "status": row[4],
            "chunk_count": row[5], "candidate_count": row[6], "storage_path": row[7], "created_at": row[8], "updated_at": row[9],
        }
        for row in rows
    ]
    return {"schema_id": CANON_BREAKDOWN_SCHEMA_ID, "status": "ok", "count": len(items), "items": items}


def approve_source_breakdown_payload(payload: dict[str, Any]) -> dict[str, Any]:
    ensure_source_breakdown_schema()
    breakdown_id = _clean(payload.get("breakdown_id"))
    source_id = _clean(payload.get("source_id"))
    approve_all = payload.get("approve_all", True) is not False
    selected_ids = [str(item) for item in payload.get("candidate_ids") or [] if str(item).strip()]
    status_to_approve = payload.get("status_to_approve") or "draft_breakdown"
    breakdown = _load_breakdown_from_db_or_file(breakdown_id, source_id)
    if not breakdown:
        raise ValueError("Breakdown not found. Generate a source breakdown first.")
    breakdown_id = breakdown.get("breakdown_id") or breakdown_id
    source_id = breakdown.get("source_id") or source_id
    candidates = _candidate_rows(breakdown_id)
    if selected_ids:
        candidates = [item for item in candidates if item.get("candidate_id") in selected_ids]
    elif not approve_all:
        candidates = [item for item in candidates if item.get("status") == status_to_approve]
    if not candidates:
        return {"schema_id": CANON_BREAKDOWN_SCHEMA_ID, "status": "empty", "approved_count": 0, "message": "No candidates matched approval filter."}
    now = _now()
    canon_ids: list[str] = []
    fragment_ids: list[str] = []
    with _connect() as conn:
        for candidate in candidates:
            ctx = candidate.get("sandbox_context") or breakdown.get("sandbox_context") or {}
            canon_id = _stable_id(source_id, candidate.get("candidate_id"), "approved", prefix="canon")
            content = _clean(candidate.get("content"))
            canon_type = candidate.get("canon_type") or "canon_fact"
            conn.execute(
                """
                INSERT INTO rp_canon_records(canon_id, source_id, project_id, canon_type, title, content, status, payload_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(canon_id) DO UPDATE SET canon_type=excluded.canon_type, title=excluded.title, content=excluded.content, status=excluded.status, payload_json=excluded.payload_json, updated_at=excluded.updated_at
                """,
                (canon_id, source_id, candidate.get("project_id") or breakdown.get("project_id") or "", canon_type, candidate.get("title") or "Approved canon", content, "approved_canon", _json(candidate), now, now),
            )
            _apply_sandbox_update(conn, "rp_canon_records", "canon_id", canon_id, {**ctx, "promotion_scope": "canon", "memory_scope": "canon"})
            canon_ids.append(canon_id)
            fragment_id = _stable_id(source_id, candidate.get("candidate_id"), "canon-fragment", prefix="canon-frag")
            conn.execute(
                """
                INSERT INTO rp_memory_fragments(fragment_id, namespace, source_type, source_id, memory_type, status, content, tags_json, payload_json, vector_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(fragment_id) DO UPDATE SET namespace=excluded.namespace, memory_type=excluded.memory_type, status=excluded.status, content=excluded.content, tags_json=excluded.tags_json, payload_json=excluded.payload_json, vector_status=excluded.vector_status, updated_at=excluded.updated_at
                """,
                (fragment_id, f"novel.canon.{canon_type}", "canon_breakdown", source_id, canon_type, "approved_canon", f"{candidate.get('title') or 'Canon'}\n{content}", _json(["novel", "canon", canon_type, *(candidate.get("keywords") or [])]), _json(candidate), "not_indexed", now, now),
            )
            _apply_sandbox_update(conn, "rp_memory_fragments", "fragment_id", fragment_id, {**ctx, "promotion_scope": "canon", "memory_scope": "canon"})
            fragment_ids.append(fragment_id)
            conn.execute("UPDATE rp_canon_candidates SET status=?, updated_at=? WHERE candidate_id=?", ("approved_canon", now, candidate.get("candidate_id")))
        conn.execute("UPDATE rp_source_breakdowns SET status=?, updated_at=? WHERE breakdown_id=?", ("approved_to_canon", now, breakdown_id))
        conn.commit()
    return {
        "schema_id": CANON_BREAKDOWN_SCHEMA_ID,
        "status": "approved",
        "breakdown_id": breakdown_id,
        "source_id": source_id,
        "approved_count": len(candidates),
        "canon_ids": canon_ids,
        "memory_fragment_ids": fragment_ids,
        "sqlite": roleplay_sqlite_state_payload(),
        "next_step": "Run vector indexing so approved canon fragments appear in semantic Runtime retrieval.",
    }


def source_breakdown_state_payload() -> dict[str, Any]:
    ensure_roleplay_foundation(write_manifest=True)
    counts = {"rp_source_breakdowns": 0, "rp_canon_candidates": 0}
    try:
        with _connect() as conn:
            for table in list(counts):
                try:
                    counts[table] = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                except Exception:
                    counts[table] = 0
    except Exception:
        pass
    return {
        "schema_id": CANON_BREAKDOWN_SCHEMA_ID,
        "version": CANON_BREAKDOWN_VERSION,
        "phase": "Phase 6 — Source Document + Canon Breakdown Pipeline",
        "status": "implemented",
        "breakdown_count": counts["rp_source_breakdowns"],
        "candidate_count": counts["rp_canon_candidates"],
        "breakdown_root": _relative_to_root(BREAKDOWNS_DIR),
        "canon_root": _relative_to_root(CANON_RECORDS_DIR),
        "pipeline": ["Save source", "Generate breakdown", "Review candidates", "Approve canon", "Index retrieval", "Runtime", "Scene"],
        "tables": counts,
    }


def source_breakdown_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    state = source_breakdown_state_payload()
    contract = {
        "schema_id": CANON_BREAKDOWN_SCHEMA_ID,
        "version": CANON_BREAKDOWN_VERSION,
        "status": "implemented",
        "purpose": "Add a reviewable source-document breakdown and explicit canon approval lane for Novel/RP V2 source documents.",
        "inputs": ["rp_source_documents", "source_documents/*.json", "chapter/scene metadata", "author notes"],
        "outputs": ["rp_source_breakdowns", "rp_canon_candidates", "rp_canon_records", "rp_memory_fragments with source_type=canon_breakdown"],
        "canon_types": list(CANON_TYPES),
        "policy": {
            "generate_breakdown": "Draft candidates only; no canon promotion.",
            "approve_canon": "Explicit action writes approved canon records and approved canon memory fragments.",
            "sqlite_source_of_truth": True,
            "chroma_policy": "Deferred to indexing/vector phases; approved fragments are vector_status=not_indexed until indexed.",
        },
        "api_endpoints": {
            "state": "GET /api/roleplay/source-breakdown/state",
            "contract": "GET /api/roleplay/source-breakdown/contract",
            "ensure_schema": "POST /api/roleplay/source-breakdown/ensure-schema",
            "generate": "POST /api/roleplay/source-breakdown/generate",
            "list": "POST /api/roleplay/source-breakdown/list",
            "approve": "POST /api/roleplay/source-breakdown/approve-canon",
        },
        "state": state,
        "next_required_phase": "Phase 7 — SQLite Memory Store Upgrade",
    }
    if write_report:
        CANON_BREAKDOWN_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        CANON_BREAKDOWN_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract


def generate_all_source_breakdowns_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_source_breakdown_schema()
    payload = payload or {}
    project_id = _clean(payload.get("project_id"))
    generated = []
    errors = []
    for source in _all_sources(project_id):
        try:
            generated.append(generate_source_breakdown_payload({"source": source}))
        except Exception as exc:
            errors.append({"source_id": _clean(source.get("source_id")), "title": _clean(source.get("title")), "error": str(exc)})
    return {
        "schema_id": CANON_BREAKDOWN_SCHEMA_ID,
        "status": "generated" if not errors else "partial",
        "project_id": project_id or "all",
        "source_count": len(generated) + len(errors),
        "generated_count": len(generated),
        "error_count": len(errors),
        "candidate_count": sum(int(item.get("candidate_count") or 0) for item in generated),
        "generated": generated[:100],
        "errors": errors,
        "state": source_breakdown_state_payload(),
    }
