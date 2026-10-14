from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from neo_app.admin.semantic_engine import embed_texts, semantic_engine_state_payload
from neo_app.memory.vector_store import delete_chroma_collection, query_chroma_chunks, reset_chroma_collection, upsert_chroma_chunks

ROOT_DIR = Path(__file__).resolve().parents[2]
ASSISTANT_DATA_DIR = ROOT_DIR / "neo_data" / "assistant"
CONVERSATION_INDEX_DIR = ASSISTANT_DATA_DIR / "conversation_indexes"

CONVERSATION_INDEX_SCHEMA_ID = "neo.assistant.conversation_index.v1"
CONVERSATION_RETRIEVAL_SCHEMA_ID = "neo.assistant.conversation_retrieval.v1"
CONVERSATION_INDEX_VERSION = "1.0.0-aci7.2"
DEFAULT_TOP_K = 6
DEFAULT_FTS_CANDIDATES = 12
DEFAULT_SEMANTIC_CANDIDATES = 12
DEFAULT_CONTEXT_CHARS = 5200
MAX_INDEX_TEXT_CHARS = 8000
MAX_ANCHORS_PER_ITEM = 24

_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_'-]*")
_PROPER_RE = re.compile(r"\b(?:[A-Z][a-zA-Z0-9_-]{2,})(?:\s+[A-Z][a-zA-Z0-9_-]{2,}){0,3}\b")
_QUOTED_RE = re.compile(r"[\"'“”‘’]([^\"'“”‘’]{2,80})[\"'“”‘’]")
_IDENTIFIER_RE = re.compile(r"\b(?:[A-Za-z]+[_-][A-Za-z0-9_-]+|[A-Za-z]{2,}\d+[A-Za-z0-9_-]*)\b")
_DECISION_RE = re.compile(
    r"\b(?:we\s+)?(?:decided|chose|picked|settled\s+on|agreed\s+on|will\s+use|should\s+use|call(?:ed)?|named?|renamed?)\b[:\s-]*(.{2,240})",
    flags=re.I,
)
_STOP = {
    "about", "after", "again", "also", "because", "before", "being", "could", "from", "have", "into",
    "just", "more", "only", "should", "that", "their", "there", "these", "they", "this", "those", "through",
    "using", "very", "what", "when", "where", "which", "while", "with", "would", "your", "assistant", "user",
    "neo", "please", "want", "need", "make", "keep", "like", "prefer", "response", "responses", "question",
}
_QUERY_CONTROL_STOP = _STOP | {
    "did", "does", "doing", "done", "we", "me", "tell", "said", "say", "decide", "decided", "discuss",
    "discussed", "remember", "recall", "earlier", "previously", "again", "original", "originally", "old",
    "current", "latest", "settled", "agreed", "picked", "chose", "choose", "bring", "back", "remind",
}
_HISTORICAL_DECISION_MARKERS = ("originally", "initially", "first decided", "before we changed", "old decision", "previous decision", "used to")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _slug(value: str, fallback: str = "chat") -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()).strip("._-").lower()
    return clean[:100] or fallback


def _index_path(session_id: str) -> Path:
    CONVERSATION_INDEX_DIR.mkdir(parents=True, exist_ok=True)
    return CONVERSATION_INDEX_DIR / f"{_slug(session_id)}.sqlite3"


def _source_id(session_id: str) -> str:
    return f"assistant_chat_{_slug(session_id)}"


def _hash_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8", errors="ignore")).hexdigest()


def _clean(text: Any, limit: int = 0) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit and len(value) > limit:
        return value[: max(1, limit - 1)].rstrip() + "…"
    return value


def extract_conversation_anchors(text: str) -> list[dict[str, str]]:
    """Deterministic topic/entity anchors; no model call is required on the write path."""
    value = str(text or "")
    anchors: list[tuple[str, str]] = []
    for match in _QUOTED_RE.findall(value):
        clean = _clean(match, 80)
        if clean:
            anchors.append(("quoted", clean))
    for match in _IDENTIFIER_RE.findall(value):
        clean = _clean(match, 80)
        if clean:
            anchors.append(("identifier", clean))
    for match in _PROPER_RE.findall(value):
        clean = _clean(match, 80)
        if clean and clean.lower() not in _STOP:
            anchors.append(("entity", clean))
    tokens = [token for token in _TOKEN_RE.findall(value) if len(token) >= 4 and token.lower() not in _STOP]
    seen_tokens: set[str] = set()
    for token in tokens:
        key = token.lower()
        if key in seen_tokens:
            continue
        seen_tokens.add(key)
        anchors.append(("topic", token))
        if len(seen_tokens) >= 10:
            break
    output: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for kind, raw in anchors:
        key = (kind, raw.lower())
        if key in seen:
            continue
        seen.add(key)
        output.append({"kind": kind, "value": raw})
        if len(output) >= MAX_ANCHORS_PER_ITEM:
            break
    return output




def _query_terms(query: str) -> list[str]:
    raw = [token.replace('"', '') for token in _TOKEN_RE.findall(str(query or "")) if len(token) > 1]
    filtered: list[str] = []
    seen: set[str] = set()
    for token in raw:
        key = token.lower()
        if key in _QUERY_CONTROL_STOP or key in seen:
            continue
        seen.add(key)
        filtered.append(token)
    if filtered:
        return filtered[:20]
    fallback: list[str] = []
    seen.clear()
    for token in raw:
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        fallback.append(token)
    return fallback[:20]


def _historical_decision_query(query: str) -> bool:
    low = _clean(query).lower()
    return any(marker in low for marker in _HISTORICAL_DECISION_MARKERS)

def _decision_record(text: str, anchors: list[dict[str, str]]) -> dict[str, str] | None:
    match = _DECISION_RE.search(str(text or ""))
    if not match:
        return None
    summary = _clean(match.group(0), 360)
    tail = _clean(match.group(1), 240)
    # Prefer the decision dimension (left side of a copula/assignment) over the
    # chosen value. This lets ``Kest green ability is X`` supersede the same
    # decision dimension when X changes later.
    lhs = re.split(r"\b(?:is|are|will\s+be|should\s+be|becomes?|to)\b|[=:]", tail, maxsplit=1, flags=re.I)[0]
    lhs_tokens = [token.lower() for token in _TOKEN_RE.findall(lhs) if len(token) >= 3 and token.lower() not in _STOP][:6]
    if lhs_tokens:
        key_basis = "|".join(lhs_tokens)
        topic = " ".join(lhs_tokens[:4])
    else:
        preferred = [a["value"] for a in anchors if a.get("kind") in {"entity", "identifier", "quoted"}]
        if not preferred:
            preferred = [a["value"] for a in anchors if a.get("kind") == "topic"]
        key_basis = "|".join(str(item).lower() for item in preferred[:2]) or tail.lower()
        topic = preferred[0] if preferred else _clean(tail, 80)
    if not key_basis:
        return None
    decision_key = hashlib.sha256(key_basis.encode("utf-8", errors="ignore")).hexdigest()[:20]
    return {"decision_key": decision_key, "summary": summary, "topic": topic}


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_items (
            item_key TEXT PRIMARY KEY,
            item_kind TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            role TEXT NOT NULL DEFAULT '',
            message_index INTEGER NOT NULL DEFAULT -1,
            created_at TEXT NOT NULL DEFAULT '',
            content_hash TEXT NOT NULL,
            content TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            anchors_json TEXT NOT NULL DEFAULT '[]',
            source_message_ids_json TEXT NOT NULL DEFAULT '[]',
            semantic_profile_fingerprint TEXT NOT NULL DEFAULT '',
            semantic_content_hash TEXT NOT NULL DEFAULT '',
            semantic_indexed_at TEXT NOT NULL DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS conversation_decisions (
            decision_key TEXT NOT NULL,
            item_key TEXT NOT NULL,
            message_id TEXT NOT NULL DEFAULT '',
            topic TEXT NOT NULL DEFAULT '',
            summary TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            superseded_by_item_key TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT '',
            updated_at TEXT NOT NULL,
            PRIMARY KEY (decision_key, item_key)
        )
        """
    )
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(conversation_items)").fetchall()}
    for name in ("semantic_profile_fingerprint", "semantic_content_hash", "semantic_indexed_at"):
        if name not in columns:
            conn.execute(f"ALTER TABLE conversation_items ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
    conn.execute("CREATE TABLE IF NOT EXISTS conversation_index_meta (meta_key TEXT PRIMARY KEY, meta_value TEXT NOT NULL DEFAULT '')")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_items_message ON conversation_items(message_id, message_index)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_conversation_decisions_status ON conversation_decisions(decision_key, status, updated_at)")
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(item_key UNINDEXED, item_kind UNINDEXED, title, content, anchors)")
    return conn


def _canonical_items(
    session: dict[str, Any],
    *,
    exclude_message_ids: set[str] | None = None,
    message_start_index: int = 0,
    include_checkpoints: bool = True,
) -> list[dict[str, Any]]:
    excluded = {str(item) for item in (exclude_message_ids or set()) if str(item)}
    rows: list[dict[str, Any]] = []
    messages = session.get("messages") if isinstance(session.get("messages"), list) else []
    start_at = max(0, min(int(message_start_index or 0), len(messages)))
    for idx in range(start_at, len(messages)):
        message = messages[idx]
        if not isinstance(message, dict):
            continue
        message_id = str(message.get("message_id") or "")
        if message_id and message_id in excluded:
            continue
        text = _clean(message.get("text"), MAX_INDEX_TEXT_CHARS)
        if not text:
            continue
        anchors = extract_conversation_anchors(text)
        item_key = f"msg:{message_id or idx}"
        rows.append({
            "item_key": item_key,
            "item_kind": "message",
            "message_id": message_id,
            "role": "assistant" if str(message.get("role") or "") == "assistant" else "user",
            "message_index": idx,
            "created_at": str(message.get("created_at") or ""),
            "content": text,
            "title": f"Chat message {idx + 1}",
            "anchors": anchors,
            "source_message_ids": [message_id] if message_id else [],
            "content_hash": _hash_text(text),
        })
    if include_checkpoints:
        checkpoints = session.get("conversation_checkpoints") if isinstance(session.get("conversation_checkpoints"), list) else []
        for checkpoint in checkpoints:
            if not isinstance(checkpoint, dict):
                continue
            summary = _clean(checkpoint.get("summary"), MAX_INDEX_TEXT_CHARS)
            checkpoint_id = str(checkpoint.get("checkpoint_id") or "")
            if not summary or not checkpoint_id:
                continue
            source_ids = [str(v) for v in checkpoint.get("source_message_ids") or [] if str(v or "") and str(v) not in excluded]
            if not source_ids:
                continue
            rows.append({
                "item_key": f"cp:{checkpoint_id}",
                "item_kind": "checkpoint",
                "message_id": "",
                "role": "system",
                "message_index": int(checkpoint.get("source_end_index") or -1),
                "created_at": str(checkpoint.get("created_at") or ""),
                "content": summary,
                "title": f"Conversation checkpoint {checkpoint_id}",
                "anchors": extract_conversation_anchors(summary),
                "source_message_ids": source_ids,
                "content_hash": str(checkpoint.get("content_hash") or _hash_text(summary)),
            })
    return rows


def _meta_map(conn: sqlite3.Connection) -> dict[str, str]:
    return {str(row["meta_key"]): str(row["meta_value"] or "") for row in conn.execute("SELECT meta_key, meta_value FROM conversation_index_meta").fetchall()}


def _set_meta(conn: sqlite3.Connection, values: dict[str, Any]) -> None:
    conn.executemany(
        "INSERT INTO conversation_index_meta(meta_key,meta_value) VALUES(?,?) ON CONFLICT(meta_key) DO UPDATE SET meta_value=excluded.meta_value",
        [(str(key), str(value if value is not None else "")) for key, value in values.items()],
    )


def sync_conversation_index(
    session: dict[str, Any] | None,
    *,
    exclude_message_ids: set[str] | None = None,
) -> dict[str, Any]:
    target = session if isinstance(session, dict) else {}
    session_id = str(target.get("session_id") or "").strip()
    if not session_id:
        return {"status": "skipped", "reason": "session_id_missing", "changed": 0}
    excluded = {str(item) for item in (exclude_message_ids or set()) if str(item)}
    messages = target.get("messages") if isinstance(target.get("messages"), list) else []
    # Foreground recall excludes only the just-appended current user turn. The
    # saved/indexed prefix therefore ends immediately before that message.
    effective_count = len(messages)
    while effective_count > 0:
        tail = messages[effective_count - 1]
        tail_id = str((tail or {}).get("message_id") or "") if isinstance(tail, dict) else ""
        if tail_id and tail_id in excluded:
            effective_count -= 1
            continue
        break
    path = _index_path(session_id)
    changed_keys: list[str] = []
    removed_keys: list[str] = []
    stamp = _now()
    with _connect(path) as conn:
        meta = _meta_map(conn)
        try:
            indexed_count = max(0, int(meta.get("indexed_message_count") or 0))
        except Exception:
            indexed_count = 0
        previous_last_id = str(meta.get("last_indexed_message_id") or "")
        current_last_id = ""
        if effective_count > 0 and isinstance(messages[effective_count - 1], dict):
            current_last_id = str(messages[effective_count - 1].get("message_id") or "")
        prior_prefix_hash = str(meta.get("checkpoint_processed_prefix_hash") or "")
        current_prefix_hash = str((target.get("checkpoint_state") or {}).get("processed_prefix_hash") or "") if isinstance(target.get("checkpoint_state"), dict) else ""
        prefix_changed = bool(prior_prefix_hash and current_prefix_hash and prior_prefix_hash != current_prefix_hash)
        append_contract_broken = bool(
            effective_count < indexed_count
            or (indexed_count > 0 and indexed_count <= len(messages) and previous_last_id and str((messages[indexed_count - 1] or {}).get("message_id") or "") != previous_last_id)
        )
        full_rebuild = bool(prefix_changed or append_contract_broken)
        message_start = 0 if full_rebuild else min(indexed_count, effective_count)
        working = {**target, "messages": messages[:effective_count]}
        canonical = _canonical_items(working, exclude_message_ids=excluded, message_start_index=message_start, include_checkpoints=True)
        canonical_by_key = {str(item["item_key"]): item for item in canonical}
        existing_rows = conn.execute("SELECT item_key,item_kind,content_hash FROM conversation_items").fetchall()
        existing = {str(row["item_key"]): {"kind": str(row["item_kind"]), "hash": str(row["content_hash"] or "")} for row in existing_rows}

        # Checkpoints are derived and may roll/rebuild; reconcile their small set
        # every turn. Message rows remain append-only unless the contract breaks.
        current_checkpoint_keys = {key for key in canonical_by_key if key.startswith("cp:")}
        for key, info in existing.items():
            remove = False
            if info["kind"] == "checkpoint" and key not in current_checkpoint_keys:
                remove = True
            elif full_rebuild and info["kind"] == "message" and key not in canonical_by_key:
                remove = True
            if remove:
                conn.execute("DELETE FROM conversation_items WHERE item_key=?", (key,))
                conn.execute("DELETE FROM conversation_fts WHERE item_key=?", (key,))
                conn.execute("DELETE FROM conversation_decisions WHERE item_key=?", (key,))
                removed_keys.append(key)
        if removed_keys:
            # A deleted source item must disappear from the disposable semantic
            # projection. Mark all remaining items for a bounded collection rebuild.
            conn.execute("UPDATE conversation_items SET semantic_profile_fingerprint='', semantic_content_hash='', semantic_indexed_at='' ")

        for key, item in canonical_by_key.items():
            existing_hash = existing.get(key, {}).get("hash")
            if existing_hash == item["content_hash"]:
                continue
            anchors_json = json.dumps(item["anchors"], ensure_ascii=False, sort_keys=True)
            source_ids_json = json.dumps(item["source_message_ids"], ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO conversation_items(item_key,item_kind,message_id,role,message_index,created_at,content_hash,content,title,anchors_json,source_message_ids_json,active,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(item_key) DO UPDATE SET
                    item_kind=excluded.item_kind,message_id=excluded.message_id,role=excluded.role,message_index=excluded.message_index,
                    created_at=excluded.created_at,content_hash=excluded.content_hash,content=excluded.content,title=excluded.title,
                    anchors_json=excluded.anchors_json,source_message_ids_json=excluded.source_message_ids_json,active=1,updated_at=excluded.updated_at
                """,
                (key,item["item_kind"],item["message_id"],item["role"],item["message_index"],item["created_at"],item["content_hash"],item["content"],item["title"],anchors_json,source_ids_json,1,stamp),
            )
            conn.execute("DELETE FROM conversation_fts WHERE item_key=?", (key,))
            anchor_text = " ".join(str(anchor.get("value") or "") for anchor in item["anchors"])
            conn.execute("INSERT INTO conversation_fts(item_key,item_kind,title,content,anchors) VALUES(?,?,?,?,?)", (key,item["item_kind"],item["title"],item["content"],anchor_text))
            conn.execute("DELETE FROM conversation_decisions WHERE item_key=?", (key,))
            if item["item_kind"] == "message":
                decision = _decision_record(item["content"], item["anchors"])
                if decision:
                    prior = conn.execute(
                        "SELECT item_key FROM conversation_decisions WHERE decision_key=? AND status='active' ORDER BY updated_at DESC LIMIT 1",
                        (decision["decision_key"],),
                    ).fetchone()
                    if prior and str(prior["item_key"]) != key:
                        conn.execute(
                            "UPDATE conversation_decisions SET status='superseded', superseded_by_item_key=?, updated_at=? WHERE decision_key=? AND status='active'",
                            (key, stamp, decision["decision_key"]),
                        )
                    conn.execute(
                        "INSERT OR REPLACE INTO conversation_decisions(decision_key,item_key,message_id,topic,summary,status,superseded_by_item_key,created_at,updated_at) VALUES(?,?,?,?,?,'active','',?,?)",
                        (decision["decision_key"], key, item["message_id"], decision["topic"], decision["summary"], item["created_at"] or stamp, stamp),
                    )
            changed_keys.append(key)

        semantic_rebuild_required = bool(removed_keys) or str(meta.get("semantic_collection_rebuild_required") or "") == "1"
        _set_meta(conn, {
            "indexed_message_count": effective_count,
            "last_indexed_message_id": current_last_id,
            "checkpoint_processed_prefix_hash": current_prefix_hash,
            "semantic_collection_rebuild_required": "1" if semantic_rebuild_required else "0",
            "updated_at": stamp,
        })
        conn.commit()
        counts = conn.execute("SELECT item_kind, COUNT(*) AS count FROM conversation_items GROUP BY item_kind").fetchall()
        decision_counts = conn.execute("SELECT status, COUNT(*) AS count FROM conversation_decisions GROUP BY status").fetchall()
    return {
        "schema_id": CONVERSATION_INDEX_SCHEMA_ID,
        "version": CONVERSATION_INDEX_VERSION,
        "status": "ready",
        "session_id": session_id,
        "path": str(path),
        "changed": len(changed_keys),
        "removed": len(removed_keys),
        "changed_keys": changed_keys[:50],
        "counts": {str(row["item_kind"]): int(row["count"]) for row in counts},
        "decision_counts": {str(row["status"]): int(row["count"]) for row in decision_counts},
        "semantic_reindex_needed": bool(changed_keys or removed_keys),
        "full_rebuild": full_rebuild,
        "indexed_message_count": effective_count,
        "updated_at": stamp,
    }


def _fts_results(session_id: str, query: str, *, limit: int) -> list[dict[str, Any]]:
    path = _index_path(session_id)
    if not path.exists():
        return []
    terms = _query_terms(query)
    if not terms:
        return []
    match = " OR ".join(f'"{term}"' for term in terms[:20])
    with _connect(path) as conn:
        rows = conn.execute(
            """
            SELECT f.item_key, f.item_kind, bm25(conversation_fts) AS rank,
                   i.message_id, i.role, i.message_index, i.created_at, i.content, i.title,
                   i.anchors_json, i.source_message_ids_json,
                   COALESCE(d.status, '') AS decision_status, COALESCE(d.topic, '') AS decision_topic,
                   COALESCE(d.superseded_by_item_key, '') AS superseded_by_item_key
            FROM conversation_fts f JOIN conversation_items i ON i.item_key=f.item_key
            LEFT JOIN conversation_decisions d ON d.item_key=i.item_key
            WHERE conversation_fts MATCH ?
            ORDER BY rank LIMIT ?
            """,
            (match, max(1, min(int(limit), 40))),
        ).fetchall()
    out: list[dict[str, Any]] = []
    for rank_index, row in enumerate(rows, start=1):
        out.append({
            "item_key": str(row["item_key"]),
            "item_kind": str(row["item_kind"]),
            "message_id": str(row["message_id"] or ""),
            "role": str(row["role"] or ""),
            "message_index": int(row["message_index"] or -1),
            "created_at": str(row["created_at"] or ""),
            "content": str(row["content"] or ""),
            "title": str(row["title"] or ""),
            "anchors": json.loads(str(row["anchors_json"] or "[]")),
            "source_message_ids": json.loads(str(row["source_message_ids_json"] or "[]")),
            "decision_status": str(row["decision_status"] or ""),
            "decision_topic": str(row["decision_topic"] or ""),
            "superseded_by_item_key": str(row["superseded_by_item_key"] or ""),
            "fts_rank": rank_index,
        })
    return out


def _items_by_keys(session_id: str, keys: list[str]) -> dict[str, dict[str, Any]]:
    path = _index_path(session_id)
    if not path.exists() or not keys:
        return {}
    placeholders = ",".join("?" for _ in keys)
    with _connect(path) as conn:
        rows = conn.execute(
            f"""SELECT i.item_key,i.item_kind,i.message_id,i.role,i.message_index,i.created_at,i.content,i.title,i.anchors_json,i.source_message_ids_json,
                       COALESCE(d.status, '') AS decision_status, COALESCE(d.topic, '') AS decision_topic,
                       COALESCE(d.superseded_by_item_key, '') AS superseded_by_item_key
                FROM conversation_items i LEFT JOIN conversation_decisions d ON d.item_key=i.item_key
                WHERE i.item_key IN ({placeholders})""",
            tuple(keys),
        ).fetchall()
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        out[str(row["item_key"])] = {
            "item_key": str(row["item_key"]),
            "item_kind": str(row["item_kind"]),
            "message_id": str(row["message_id"] or ""),
            "role": str(row["role"] or ""),
            "message_index": int(row["message_index"] or -1),
            "created_at": str(row["created_at"] or ""),
            "content": str(row["content"] or ""),
            "title": str(row["title"] or ""),
            "anchors": json.loads(str(row["anchors_json"] or "[]")),
            "source_message_ids": json.loads(str(row["source_message_ids_json"] or "[]")),
            "decision_status": str(row["decision_status"] or ""),
            "decision_topic": str(row["decision_topic"] or ""),
            "superseded_by_item_key": str(row["superseded_by_item_key"] or ""),
        }
    return out


def _rrf_merge(
    fts: list[dict[str, Any]],
    vector: list[dict[str, Any]],
    *,
    limit: int,
    historical_decisions: bool = False,
) -> list[dict[str, Any]]:
    scored: dict[str, dict[str, Any]] = {}
    k = 60.0
    for rank, item in enumerate(fts, start=1):
        key = str(item.get("item_key") or "")
        if not key:
            continue
        row = scored.setdefault(key, dict(item))
        row["rrf_score"] = float(row.get("rrf_score") or 0.0) + 1.0 / (k + rank)
        row["retrieval_lanes"] = list(dict.fromkeys([*(row.get("retrieval_lanes") or []), "fts"]))
    for rank, item in enumerate(vector, start=1):
        key = str(item.get("item_key") or item.get("chunk_id") or "")
        if not key:
            continue
        row = scored.setdefault(key, dict(item))
        row["rrf_score"] = float(row.get("rrf_score") or 0.0) + 1.0 / (k + rank)
        row["semantic_score"] = float(item.get("score") or 0.0)
        row["retrieval_lanes"] = list(dict.fromkeys([*(row.get("retrieval_lanes") or []), "semantic"]))
    rows = list(scored.values())
    for item in rows:
        kind_bonus = 0.003 if item.get("item_kind") == "message" else 0.0
        decision_state = str(item.get("decision_status") or "")
        if historical_decisions:
            decision_bonus = 0.012 if decision_state == "superseded" else (0.0 if decision_state == "active" else 0.0)
        else:
            decision_bonus = 0.012 if decision_state == "active" else (-0.02 if decision_state == "superseded" else 0.0)
        item["score"] = round(float(item.get("rrf_score") or 0.0) + kind_bonus + decision_bonus, 8)
    rows.sort(key=lambda row: (float(row.get("score") or 0.0), int(row.get("message_index") or -1)), reverse=True)
    return rows[: max(1, min(int(limit), 24))]


def retrieve_conversation_context(
    session_id: str,
    query: str,
    *,
    session: dict[str, Any] | None = None,
    exclude_message_id: str = "",
    top_k: int = DEFAULT_TOP_K,
    max_context_chars: int = DEFAULT_CONTEXT_CHARS,
) -> dict[str, Any]:
    if session and isinstance(session, dict):
        sync_conversation_index(session, exclude_message_ids={exclude_message_id} if exclude_message_id else set())
    fts = _fts_results(session_id, query, limit=DEFAULT_FTS_CANDIDATES)
    semantic_diag: dict[str, Any] = {"status": "skipped", "reason": "semantic_not_ready"}
    vector_rows: list[dict[str, Any]] = []
    try:
        semantic = semantic_engine_state_payload()
        # Exact same-chat lexical/anchor hits are the cheapest/most precise
        # continuity path. Invoke semantic retrieval only when lexical recall is
        # thin and no exact topic/entity anchor already grounds the query.
        query_anchors = {str(a.get("value") or "").lower() for a in extract_conversation_anchors(query) if str(a.get("value") or "")}
        anchor_hit = any(
            str(anchor.get("value") or "").lower() in query_anchors
            for item in fts
            for anchor in (item.get("anchors") or [])
            if isinstance(anchor, dict)
        )
        run_semantic = (not anchor_hit) and len(fts) < min(3, max(1, int(top_k)))
        if semantic.get("semantic_search_enabled") and run_semantic:
            embedded = embed_texts([query], allow_fallback=False, purpose="query")
            vectors = embedded.get("vectors") if isinstance(embedded.get("vectors"), list) else []
            if vectors:
                queried = query_chroma_chunks(vectors[0], source_id=_source_id(session_id), limit=DEFAULT_SEMANTIC_CANDIDATES)
                semantic_diag = {key: value for key, value in queried.items() if key != "results"}
                raw = queried.get("results") if isinstance(queried.get("results"), list) else []
                keys = [str(item.get("chunk_id") or "") for item in raw if str(item.get("chunk_id") or "")]
                canonical = _items_by_keys(session_id, keys)
                for item in raw:
                    key = str(item.get("chunk_id") or "")
                    row = dict(canonical.get(key) or {})
                    if not row:
                        continue
                    row["score"] = float(item.get("score") or 0.0)
                    row["item_key"] = key
                    vector_rows.append(row)
            else:
                semantic_diag = {"status": "unavailable", "reason": "query_embedding_empty"}
        elif semantic.get("semantic_search_enabled"):
            semantic_diag = {"status": "skipped", "reason": "anchor_match" if anchor_hit else "fts_sufficient", "fts_count": len(fts)}
    except Exception as exc:
        semantic_diag = {"status": "unavailable", "reason": "semantic_query_failed", "error": str(exc)[:500]}

    merged = _rrf_merge(
        fts,
        vector_rows,
        limit=max(top_k * 2, top_k),
        historical_decisions=_historical_decision_query(query),
    )
    selected: list[dict[str, Any]] = []
    covered_message_ids: set[str] = set()
    for item in merged:
        source_ids = {str(v) for v in item.get("source_message_ids") or [] if str(v or "")}
        if item.get("item_kind") == "checkpoint" and source_ids & covered_message_ids:
            continue
        selected.append(item)
        if item.get("item_kind") == "message" and item.get("message_id"):
            covered_message_ids.add(str(item.get("message_id")))
        if len(selected) >= max(1, min(int(top_k), 10)):
            break
    context_blocks: list[str] = []
    public: list[dict[str, Any]] = []
    used = 0
    for rank, item in enumerate(selected, start=1):
        content = _clean(item.get("content"), 1800)
        if not content:
            continue
        kind = str(item.get("item_kind") or "message")
        if kind == "checkpoint":
            label = f"Chat checkpoint · source messages {', '.join(item.get('source_message_ids') or [])}"
        else:
            label = f"Chat message {int(item.get('message_index') or -1) + 1} · {item.get('role') or 'user'} · {item.get('message_id') or ''}"
        decision_status = str(item.get("decision_status") or "")
        decision_note = f"\nDecision state: {'current' if decision_status == 'active' else 'historical/superseded'}" if decision_status else ""
        block = f"[{label}]{decision_note}\n{content}"
        remaining = max(0, int(max_context_chars) - used)
        if remaining <= 0:
            break
        if len(block) > remaining:
            block = block[:remaining].rstrip()
        context_blocks.append(block)
        used += len(block)
        public.append({
            "rank": rank,
            "item_key": item.get("item_key"),
            "item_kind": kind,
            "message_id": item.get("message_id"),
            "message_index": item.get("message_index"),
            "source_message_ids": item.get("source_message_ids") or [],
            "anchors": item.get("anchors") or [],
            "score": item.get("score"),
            "retrieval_lanes": item.get("retrieval_lanes") or [],
            "decision_status": item.get("decision_status") or "",
            "decision_topic": item.get("decision_topic") or "",
            "superseded_by_item_key": item.get("superseded_by_item_key") or "",
        })
    context = ""
    if context_blocks:
        context = (
            "Relevant evidence retrieved from this same chat. These are derived continuity records; "
            "the current user message and newer source messages take precedence.\n\n"
            + "\n\n---\n\n".join(context_blocks)
        )
    return {
        "schema_id": CONVERSATION_RETRIEVAL_SCHEMA_ID,
        "version": CONVERSATION_INDEX_VERSION,
        "status": "ready" if context_blocks else "empty",
        "session_id": session_id,
        "query": str(query or ""),
        "selected_count": len(public),
        "selected": public,
        "context": context,
        "context_chars": len(context),
        "fts_count": len(fts),
        "semantic": semantic_diag,
        "same_chat_only": True,
    }


def index_conversation_semantic(
    session_id: str,
    *,
    progress_callback: Callable[..., Any] | None = None,
    cancel_callback: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    from neo_app.assistant.store import get_session

    session = get_session(session_id)
    if not session:
        return {"status": "missing_session", "session_id": session_id, "indexed": 0}
    sync = sync_conversation_index(session)
    semantic = semantic_engine_state_payload()
    vector_compat = semantic.get("vector_compatibility") if isinstance(semantic.get("vector_compatibility"), dict) else {}
    if not semantic.get("semantic_search_enabled") or not bool(vector_compat.get("write_enabled")):
        return {
            "status": "semantic_write_unavailable",
            "session_id": session_id,
            "indexed": 0,
            "semantic_status": semantic.get("status"),
            "vector_write_enabled": bool(vector_compat.get("write_enabled")),
            "sync": sync,
        }
    path = _index_path(session_id)
    profile_fp = str((semantic.get("embedding") or {}).get("profile_fingerprint") or "")
    if not profile_fp:
        return {"status": "semantic_write_unavailable", "session_id": session_id, "indexed": 0, "reason": "profile_fingerprint_missing", "sync": sync}
    with _connect(path) as conn:
        meta = _meta_map(conn)
        rebuild_collection = str(meta.get("semantic_collection_rebuild_required") or "") == "1"
        if rebuild_collection:
            rows = conn.execute(
                """
                SELECT item_key,item_kind,message_id,message_index,content_hash,content,title,source_message_ids_json,
                       semantic_profile_fingerprint,semantic_content_hash
                FROM conversation_items
                ORDER BY message_index,item_key
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT item_key,item_kind,message_id,message_index,content_hash,content,title,source_message_ids_json,
                       semantic_profile_fingerprint,semantic_content_hash
                FROM conversation_items
                WHERE semantic_profile_fingerprint != ? OR semantic_content_hash != content_hash
                ORDER BY message_index,item_key
                """,
                (profile_fp,),
            ).fetchall()
    if not rows:
        return {"status": "up_to_date", "session_id": session_id, "indexed": 0, "sync": sync, "profile_fingerprint": profile_fp}
    reset_result: dict[str, Any] = {"status": "not_needed"}
    if rebuild_collection:
        reset_result = reset_chroma_collection(_source_id(session_id))
        if reset_result.get("status") not in {"reset", "clean"}:
            return {
                "status": "semantic_collection_reset_failed",
                "session_id": session_id,
                "indexed": 0,
                "sync": sync,
                "reset": reset_result,
                "profile_fingerprint": profile_fp,
            }
    if progress_callback:
        progress_callback(phase="chat_semantic_embedding", current=0, total=len(rows), percent=5, message="Embedding changed same-chat continuity records.")
    chunks: list[dict[str, Any]] = []
    vectors: list[list[float]] = []
    batch_size = 16
    for offset in range(0, len(rows), batch_size):
        if cancel_callback:
            cancel_callback("Cancelled while indexing chat continuity embeddings.")
        batch = rows[offset:offset + batch_size]
        texts = [str(row["content"] or "") for row in batch]
        embedded = embed_texts(texts, allow_fallback=False, purpose="document")
        batch_vectors = embedded.get("vectors") if isinstance(embedded.get("vectors"), list) else []
        if len(batch_vectors) != len(batch):
            raise RuntimeError("Conversation semantic indexing returned an unexpected vector count")
        vectors.extend(batch_vectors)
        for row in batch:
            chunks.append({
                "chunk_id": str(row["item_key"]),
                "source_id": _source_id(session_id),
                "document_id": session_id,
                "source_path": f"assistant://chat/{session_id}",
                "title": str(row["title"] or "Chat continuity"),
                "content": str(row["content"] or ""),
                "content_hash": str(row["content_hash"] or ""),
                "visibility": "private",
                "trust_level": "confirmed",
                "retention_scope": "chat",
                "memory_state": "active",
                "importance": "normal",
                "approval_state": "not_required",
                "policy_score": 0.5,
            })
        if progress_callback:
            done = min(len(rows), offset + len(batch))
            progress_callback(phase="chat_semantic_embedding", current=done, total=len(rows), percent=min(85, 5 + int((done / len(rows)) * 80)), message="Embedding same-chat continuity records.")
    mirrored = upsert_chroma_chunks(chunks, vectors, source_id=_source_id(session_id), force_reindex=True)
    if mirrored.get("status") == "mirrored":
        stamp = _now()
        with _connect(path) as conn:
            conn.executemany(
                "UPDATE conversation_items SET semantic_profile_fingerprint=?, semantic_content_hash=content_hash, semantic_indexed_at=? WHERE item_key=?",
                [(profile_fp, stamp, str(row["item_key"])) for row in rows],
            )
            _set_meta(conn, {"semantic_collection_rebuild_required": "0", "updated_at": stamp})
            conn.commit()
    if progress_callback:
        progress_callback(phase="chat_semantic_finalizing", current=len(rows), total=len(rows), percent=95, message="Finalizing same-chat semantic projection.")
    return {
        "status": str(mirrored.get("status") or "completed"),
        "session_id": session_id,
        "indexed": len(chunks),
        "sync": sync,
        "chroma": mirrored,
        "semantic_collection_rebuilt": bool(rebuild_collection),
        "reset": reset_result,
        "profile_fingerprint": profile_fp,
    }


def schedule_conversation_semantic_index(session_id: str, *, delay_seconds: float = 90.0) -> dict[str, Any]:
    if not str(session_id or "").strip():
        return {"status": "skipped", "reason": "session_id_missing"}
    try:
        semantic = semantic_engine_state_payload()
        vector_compat = semantic.get("vector_compatibility") if isinstance(semantic.get("vector_compatibility"), dict) else {}
        if not semantic.get("semantic_search_enabled") or not bool(vector_compat.get("write_enabled")):
            return {"status": "skipped", "reason": "semantic_write_unavailable"}
        from neo_app.memory.job_service import get_memory_job_service
        service = get_memory_job_service()
        created = service.create(
            job_type="assistant_chat_semantic_index",
            payload={"session_id": session_id, "source": "aci7_2_conversation_continuity"},
            title="Assistant chat continuity index",
            surface="assistant",
            scope_id="chat",
            dedupe_key=f"aci7_2_chat_semantic:{session_id}",
            start=False,
        )
        job = created.get("job") if isinstance(created.get("job"), dict) else {}
        job_id = str(job.get("job_id") or "")
        if job_id:
            scheduled = service.schedule_start(job_id, delay_seconds=delay_seconds)
            return {"status": "scheduled", "job_id": job_id, "deduplicated": bool(created.get("deduplicated")), "delay_seconds": scheduled.get("delay_seconds") or delay_seconds}
        return {"status": "failed", "reason": "job_not_created"}
    except Exception as exc:
        return {"status": "degraded", "reason": "job_schedule_failed", "error": str(exc)[:500]}


def conversation_index_status(session_id: str) -> dict[str, Any]:
    path = _index_path(session_id)
    if not path.exists():
        return {"schema_id": CONVERSATION_INDEX_SCHEMA_ID, "status": "not_built", "session_id": session_id, "same_chat_only": True}
    with _connect(path) as conn:
        counts = conn.execute("SELECT item_kind, COUNT(*) AS count FROM conversation_items GROUP BY item_kind").fetchall()
        decision_counts = conn.execute("SELECT status, COUNT(*) AS count FROM conversation_decisions GROUP BY status").fetchall()
    return {
        "schema_id": CONVERSATION_INDEX_SCHEMA_ID,
        "version": CONVERSATION_INDEX_VERSION,
        "status": "ready",
        "session_id": session_id,
        "path": str(path),
        "counts": {str(row["item_kind"]): int(row["count"]) for row in counts},
        "decision_counts": {str(row["status"]): int(row["count"]) for row in decision_counts},
        "same_chat_only": True,
    }


def delete_conversation_index(session_id: str) -> dict[str, Any]:
    path = _index_path(session_id)
    deleted_files: list[str] = []
    for candidate in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm")):
        try:
            if candidate.exists():
                candidate.unlink()
                deleted_files.append(str(candidate))
        except Exception:
            pass
    chroma = delete_chroma_collection(_source_id(session_id))
    return {"status": "deleted", "session_id": session_id, "files": deleted_files, "chroma": chroma}
