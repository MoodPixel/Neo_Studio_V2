from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.admin.semantic_engine import embed_texts, rerank_results, semantic_engine_state_payload
from neo_app.roleplay.engine_bridge import roleplay_engine_bridge_state
from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema, rebuild_roleplay_memory_search_documents
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation

PHASE8_SCHEMA_ID: Final[str] = "neo.roleplay.memory.embedding_reranker_adapter.v1"
PHASE8_VERSION: Final[str] = "1.0.0-phase8-embedding-reranker-adapter"
PHASE8_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "embedding_reranker_adapter_contract.json"
PHASE8_STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "embedding_reranker_adapter_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_int(value: Any, default: int = 12, minimum: int = 1, maximum: int = 5000) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _embedding_dimension(engine_state: dict[str, Any] | None = None, fallback: int = 96) -> int:
    state = engine_state or roleplay_engine_bridge_state()
    embeddings = dict(state.get("embedding_profiles") or {})
    return _safe_int(embeddings.get("default_dimension"), default=fallback, minimum=16, maximum=4096)


def _embedding_model_id(engine_state: dict[str, Any] | None = None) -> str:
    state = engine_state or roleplay_engine_bridge_state()
    embeddings = dict(state.get("embedding_profiles") or {})
    return str(
        embeddings.get("active_model_name")
        or embeddings.get("active_model_path")
        or embeddings.get("active_profile_id")
        or embeddings.get("active_provider_id")
        or "local_hash_embeddings"
    )


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _vector_norm(vec: list[float]) -> float:
    return math.sqrt(sum(float(v) * float(v) for v in vec)) or 1.0


def _normalize_vector(vec: Any) -> list[float]:
    if not isinstance(vec, (list, tuple)):
        return []
    clean: list[float] = []
    for item in vec:
        try:
            clean.append(float(item))
        except Exception:
            continue
    norm = _vector_norm(clean)
    return [round(v / norm, 8) for v in clean]


def _read_search_documents(conn: sqlite3.Connection, *, scope_id: str = "", limit: int = 500, force: bool = False) -> list[dict[str, Any]]:
    clean_scope = str(scope_id or "").strip()
    lim = _safe_int(limit, default=500, maximum=10000)
    base_sql = """
        SELECT doc_id, source_table, source_id, sandbox_id, memory_scope, promotion_scope,
               title, content, tags_text, payload_json, updated_at
        FROM rp_memory_search_documents
        WHERE (? = '' OR sandbox_id = ? OR source_id = ? OR payload_json LIKE ?)
        ORDER BY updated_at DESC
        LIMIT ?
    """
    rows = conn.execute(base_sql, (clean_scope, clean_scope, clean_scope, f"%{clean_scope}%", lim)).fetchall()
    docs: list[dict[str, Any]] = []
    for row in rows:
        data = dict(row)
        index_id = f"search_doc:{data.get('doc_id') or data.get('source_id')}"
        if not force and conn.execute("SELECT 1 FROM rp_vector_index WHERE index_id = ?", (index_id,)).fetchone():
            continue
        content = str(data.get("content") or "").strip()
        title = str(data.get("title") or data.get("source_id") or data.get("doc_id") or "").strip()
        tags = str(data.get("tags_text") or "").strip()
        if not content and not title:
            continue
        docs.append({
            "index_id": index_id,
            "doc_id": str(data.get("doc_id") or ""),
            "source_table": str(data.get("source_table") or "rp_memory_search_documents"),
            "source_id": str(data.get("source_id") or data.get("doc_id") or ""),
            "source_type": str(data.get("memory_scope") or "memory_search_document"),
            "scope_id": str(data.get("sandbox_id") or ""),
            "title": title,
            "content": f"{title}\n{content}\n{tags}".strip(),
            "raw_content": content,
            "payload": data,
        })
    return docs


def roleplay_embedding_reranker_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    state = roleplay_embedding_reranker_state_payload(write_report=False)
    contract = {
        "schema_id": PHASE8_SCHEMA_ID,
        "contract_version": PHASE8_VERSION,
        "phase": "Phase 8 — Embedding + Reranker Adapter",
        "generated_at": _now(),
        "owner_contract": {
            "admin_engine_owns": ["embedding provider config", "reranker provider config", "model paths", "API base URLs", "API key env names"],
            "roleplay_owns": ["sandbox-scoped memory documents", "SQLite vector cache", "retrieval traces", "fallback safety"],
        },
        "provider_lanes": {
            "embeddings": ["sentence_transformers_local", "openai_compatible_embeddings", "hash_vector_fallback"],
            "rerankers": ["cross_encoder_reranker", "openai_compatible_reranker", "lexical_rerank_fallback", "disabled"],
        },
        "pipeline": [
            "compile Builder / Source / Canon memory",
            "rebuild rp_memory_search_documents",
            "embed search documents through Admin semantic engine",
            "write rp_vector_index as SQLite cache",
            "semantic retrieval may rerank through Admin semantic engine",
            "fallback to hash embeddings / lexical rerank if external models are unavailable",
        ],
        "endpoints": {
            "contract": "/api/roleplay/embedding-reranker/contract",
            "state": "/api/roleplay/embedding-reranker/state",
            "embed": "/api/roleplay/embedding-reranker/embed",
            "rerank": "/api/roleplay/embedding-reranker/rerank",
            "index_search_documents": "/api/roleplay/embedding-reranker/index-search-documents",
        },
        "state": state,
        "next_required_phase": "Phase 9 — Chroma Vector Mirror",
    }
    if write_report:
        PHASE8_CONTRACT_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE8_CONTRACT_PATH.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return contract


def roleplay_embedding_reranker_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    engine_bridge = roleplay_engine_bridge_state()
    semantic_state = semantic_engine_state_payload()
    with _connect() as conn:
        counts = {
            "search_documents": _table_count(conn, "rp_memory_search_documents"),
            "vector_index": _table_count(conn, "rp_vector_index"),
            "retrieval_traces": _table_count(conn, "rp_retrieval_traces"),
        }
    embeddings = dict(engine_bridge.get("embedding_profiles") or {})
    reranker = dict(engine_bridge.get("reranker_profiles") or {})
    payload = {
        "schema_id": PHASE8_SCHEMA_ID,
        "contract_version": PHASE8_VERSION,
        "status": "ready" if counts["search_documents"] >= 0 else "partial",
        "checked_at": _now(),
        "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        "counts": counts,
        "admin_engine_bridge": {
            "embedding_provider": embeddings.get("active_provider_id") or "local_hash_embeddings",
            "embedding_model": _embedding_model_id(engine_bridge),
            "embedding_dimension": _embedding_dimension(engine_bridge),
            "reranker_provider": reranker.get("active_provider_id") or "none",
            "reranker_model": reranker.get("active_model_name") or reranker.get("active_model_path") or reranker.get("active_profile_id") or "",
        },
        "semantic_engine": semantic_state,
        "fallbacks": {
            "embedding": "local_hash_embeddings",
            "reranker": "lexical_overlap",
            "no_crash_policy": True,
        },
    }
    if write_report:
        PHASE8_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        PHASE8_STATE_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return payload


def embed_roleplay_texts_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    raw_texts = data.get("texts") if isinstance(data.get("texts"), list) else [data.get("text") or ""]
    texts = [str(item or "") for item in raw_texts]
    engine_state = roleplay_engine_bridge_state()
    dim = _safe_int(data.get("dimension"), default=_embedding_dimension(engine_state), minimum=16, maximum=4096)
    result = embed_texts(texts, engine_state=engine_state, dimension=dim, allow_fallback=bool(data.get("allow_fallback", True)))
    return {
        "schema_id": "neo.roleplay.embedding_reranker.embed.v1",
        "status": result.get("status") or "embedded",
        "embedding": {k: v for k, v in result.items() if k != "vectors"},
        "vector_count": len(result.get("vectors") or []),
        "sample_dimension": len((result.get("vectors") or [[]])[0]) if result.get("vectors") else 0,
        "vectors": result.get("vectors") if bool(data.get("include_vectors")) else None,
        "state": roleplay_embedding_reranker_state_payload(write_report=False),
    }


def rerank_roleplay_results_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    query = str(data.get("query") or "")
    results = data.get("results") if isinstance(data.get("results"), list) else []
    top_n = _safe_int(data.get("top_n") or data.get("limit"), default=len(results) or 8, maximum=100)
    engine_state = roleplay_engine_bridge_state()
    result = rerank_results(query, results, engine_state=engine_state, top_n=top_n, allow_fallback=bool(data.get("allow_fallback", True)))
    return {
        "schema_id": "neo.roleplay.embedding_reranker.rerank.v1",
        "status": result.get("status") or "reranked",
        "reranker": {k: v for k, v in result.items() if k != "results"},
        "results": result.get("results") or results,
        "state": roleplay_embedding_reranker_state_payload(write_report=False),
    }


def index_roleplay_search_documents_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    rebuild_search = bool(data.get("rebuild_search", True))
    if rebuild_search:
        rebuild_result = rebuild_roleplay_memory_search_documents()
    else:
        rebuild_result = {"status": "skipped"}

    engine_state = roleplay_engine_bridge_state()
    model_id = _embedding_model_id(engine_state)
    dimension = _safe_int(data.get("dimension"), default=_embedding_dimension(engine_state), minimum=16, maximum=4096)
    scope_id = str(data.get("scope_id") or data.get("scope") or "").strip()
    limit = _safe_int(data.get("limit"), default=500, maximum=10000)
    force = bool(data.get("force", False))
    now = _now()

    with _connect() as conn:
        docs = _read_search_documents(conn, scope_id=scope_id, limit=limit, force=force)
        if not docs:
            return {
                "schema_id": "neo.roleplay.embedding_reranker.index_search_documents.v1",
                "status": "no_pending_documents",
                "scope_id": scope_id,
                "indexed_count": 0,
                "skipped_or_existing_count": max(0, _table_count(conn, "rp_memory_search_documents")),
                "rebuild_search": rebuild_result,
                "state": roleplay_embedding_reranker_state_payload(write_report=False),
            }
        emb_result = embed_texts([doc["content"] for doc in docs], engine_state=engine_state, dimension=dimension, allow_fallback=bool(data.get("allow_fallback", True)))
        vectors = emb_result.get("vectors") or []
        indexed: list[dict[str, Any]] = []
        for idx, doc in enumerate(docs):
            vec = _normalize_vector(vectors[idx] if idx < len(vectors) else [])
            if not vec:
                continue
            payload_json = dict(doc.get("payload") or {})
            payload_json["phase8"] = {
                "adapter_version": PHASE8_VERSION,
                "source_doc_id": doc.get("doc_id"),
                "embedding_mode": emb_result.get("mode"),
                "fallback_used": bool(emb_result.get("fallback_used")),
            }
            conn.execute(
                """
                INSERT INTO rp_vector_index(index_id, source_table, source_id, source_type, scope_id, title, content,
                                            embedding_json, embedding_dimension, model_id, vector_status, payload_json, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(index_id) DO UPDATE SET
                    source_table=excluded.source_table,
                    source_id=excluded.source_id,
                    source_type=excluded.source_type,
                    scope_id=excluded.scope_id,
                    title=excluded.title,
                    content=excluded.content,
                    embedding_json=excluded.embedding_json,
                    embedding_dimension=excluded.embedding_dimension,
                    model_id=excluded.model_id,
                    vector_status=excluded.vector_status,
                    payload_json=excluded.payload_json,
                    indexed_at=excluded.indexed_at
                """,
                (
                    doc["index_id"],
                    doc["source_table"],
                    doc["source_id"],
                    doc["source_type"],
                    doc["scope_id"],
                    doc["title"],
                    doc["content"],
                    _json(vec),
                    len(vec),
                    str(emb_result.get("model_id") or model_id),
                    "indexed",
                    _json(payload_json),
                    now,
                ),
            )
            indexed.append({
                "index_id": doc["index_id"],
                "source_table": doc["source_table"],
                "source_id": doc["source_id"],
                "scope_id": doc["scope_id"],
                "dimension": len(vec),
            })
        conn.commit()

    return {
        "schema_id": "neo.roleplay.embedding_reranker.index_search_documents.v1",
        "status": "indexed",
        "scope_id": scope_id,
        "indexed_count": len(indexed),
        "indexed": indexed[:100],
        "embedding_engine": {k: v for k, v in emb_result.items() if k != "vectors"},
        "model_id": str(emb_result.get("model_id") or model_id),
        "dimension": int(emb_result.get("dimension") or (len(vectors[0]) if vectors else dimension)),
        "fallback_used": bool(emb_result.get("fallback_used")),
        "rebuild_search": rebuild_result,
        "state": roleplay_embedding_reranker_state_payload(write_report=True),
    }
