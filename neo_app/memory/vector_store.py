from __future__ import annotations

from pathlib import Path
from typing import Any

from neo_app.admin.engine import admin_engine_state_payload

ROOT_DIR = Path(__file__).resolve().parents[2]
VECTOR_STORE_SCHEMA_ID = "neo.memory.vector_store.v1"


def _collection_name(source_id: str, engine_state: dict[str, Any] | None = None) -> str:
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    prefix = str(vector.get("collection_prefix") or "neo").strip() or "neo"
    clean = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(source_id or "global")).strip("_") or "global"
    return f"{prefix}_{clean}"


def vector_store_status(engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    active_store = str(vector.get("active_store") or "sqlite_vector_index")
    try:
        import chromadb  # type: ignore
        chroma_available = True
    except Exception:
        chroma_available = False
    return {
        "schema_id": VECTOR_STORE_SCHEMA_ID,
        "status": "ready" if active_store != "chroma" or chroma_available else "fallback_ready",
        "active_store": active_store,
        "chroma_available": chroma_available,
        "sqlite_fallback": True,
        "write_enabled": bool(vector.get("write_enabled")),
        "root": vector.get("root") or vector.get("persist_path") or "neo_data/vector_store",
        "note": "Chroma is used when selected and installed; SQLite document/chunk storage remains the source of truth.",
    }


def upsert_chroma_chunks(chunks: list[dict[str, Any]], vectors: list[list[float]], *, source_id: str, engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Best-effort Chroma mirror. SQLite remains authoritative if Chroma is missing."""
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    if str(vector.get("active_store") or "") != "chroma" or not bool(vector.get("write_enabled")):
        return {"status": "skipped", "reason": "chroma_not_active_or_write_disabled", "count": 0}
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": "chromadb_missing", "error": str(exc)[:500], "count": 0}
    root = ROOT_DIR / str(vector.get("persist_path") or vector.get("root") or "neo_data/vector_store")
    root.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(root))
    collection = client.get_or_create_collection(_collection_name(source_id, state))
    ids = [str(chunk.get("chunk_id")) for chunk in chunks]
    docs = [str(chunk.get("content") or "") for chunk in chunks]
    metas = []
    for chunk in chunks:
        metas.append({
            "source_id": str(chunk.get("source_id") or source_id),
            "document_id": str(chunk.get("document_id") or ""),
            "source_path": str(chunk.get("source_path") or ""),
            "title": str(chunk.get("title") or ""),
            "visibility": str(chunk.get("visibility") or "expert"),
            "trust_level": str(chunk.get("trust_level") or "confirmed"),
            "retention_scope": str(chunk.get("retention_scope") or (chunk.get("policy") or {}).get("retention_scope") or "project"),
            "memory_state": str(chunk.get("memory_state") or (chunk.get("policy") or {}).get("memory_state") or "active"),
            "importance": str(chunk.get("importance") or (chunk.get("policy") or {}).get("importance") or "normal"),
            "approval_state": str(chunk.get("approval_state") or (chunk.get("policy") or {}).get("approval_state") or "not_required"),
            "policy_score": float(chunk.get("policy_score") or (chunk.get("policy") or {}).get("policy_score") or 0.5),
        })
    collection.upsert(ids=ids, documents=docs, embeddings=vectors, metadatas=metas)
    return {"status": "mirrored", "store": "chroma", "collection": collection.name, "count": len(ids)}


def query_chroma_chunks(query_vector: list[float], *, source_id: str, limit: int = 12, engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Best-effort Chroma query. Returns an empty result set when Chroma is not active/available."""
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    if str(vector.get("active_store") or "") != "chroma":
        return {"status": "skipped", "reason": "chroma_not_active", "results": [], "count": 0}
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": "chromadb_missing", "error": str(exc)[:500], "results": [], "count": 0}
    try:
        root = ROOT_DIR / str(vector.get("persist_path") or vector.get("root") or "neo_data/vector_store")
        client = chromadb.PersistentClient(path=str(root))
        collection_name = _collection_name(source_id, state)
        collection = client.get_collection(collection_name)
        raw = collection.query(query_embeddings=[query_vector], n_results=max(1, min(int(limit), 100)))
    except Exception as exc:
        return {"status": "failed", "reason": "chroma_query_error", "error": str(exc)[:700], "results": [], "count": 0}
    ids = (raw.get("ids") or [[]])[0] if isinstance(raw, dict) else []
    docs = (raw.get("documents") or [[]])[0] if isinstance(raw, dict) else []
    metas = (raw.get("metadatas") or [[]])[0] if isinstance(raw, dict) else []
    distances = (raw.get("distances") or [[]])[0] if isinstance(raw, dict) else []
    results: list[dict[str, Any]] = []
    for index, chunk_id in enumerate(ids or []):
        distance = distances[index] if index < len(distances or []) else None
        try:
            score = 1.0 / (1.0 + float(distance)) if distance is not None else 0.5
        except Exception:
            score = 0.5
        meta = metas[index] if index < len(metas or []) and isinstance(metas[index], dict) else {}
        results.append({
            "chunk_id": str(chunk_id),
            "content": docs[index] if index < len(docs or []) else "",
            "score": round(score, 6),
            "retrieval_type": "vector",
            "source_id": meta.get("source_id") or source_id,
            "document_id": meta.get("document_id") or "",
            "source_path": meta.get("source_path") or "",
            "title": meta.get("title") or "Vector result",
            "visibility": meta.get("visibility") or "expert",
            "trust_level": meta.get("trust_level") or "confirmed",
            "retention_scope": meta.get("retention_scope") or "project",
            "memory_state": meta.get("memory_state") or "active",
            "importance": meta.get("importance") or "normal",
            "approval_state": meta.get("approval_state") or "not_required",
            "policy_score": meta.get("policy_score") or 0.5,
            "metadata": meta,
        })
    return {"status": "ready", "store": "chroma", "collection": collection_name, "results": results, "count": len(results)}
