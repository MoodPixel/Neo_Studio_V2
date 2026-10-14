from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from typing import Any

from neo_app.admin.engine import admin_engine_state_payload
from neo_app.admin.semantic_profile import fingerprint_short, semantic_config_fingerprint

ROOT_DIR = Path(__file__).resolve().parents[2]
VECTOR_STORE_SCHEMA_ID = "neo.memory.vector_store.v2"
VECTOR_BINDING_SCHEMA_ID = "neo.memory.vector_binding.v1"




_CANONICAL_DRIFT_SOURCES = {"neo_codebase", "system_records", "assistant_memory", "project_workspace", "roleplay_memory"}
_CANONICAL_MEMORY_DB = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"


def _canonical_chunk_snapshot(source_id: str, *, max_rows: int = 5000) -> dict[str, Any]:
    if source_id not in _CANONICAL_DRIFT_SOURCES:
        return {"status": "not_applicable", "source_id": source_id}
    if not _CANONICAL_MEMORY_DB.exists():
        return {"status": "not_available", "source_id": source_id, "reason": "canonical_memory_db_missing"}
    try:
        with sqlite3.connect(_CANONICAL_MEMORY_DB) as conn:
            conn.row_factory = sqlite3.Row
            table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='memory_chunks'").fetchone()
            if not table:
                return {"status": "not_available", "source_id": source_id, "reason": "memory_chunks_table_missing"}
            total = int(conn.execute("SELECT COUNT(*) FROM memory_chunks WHERE source_id = ? AND memory_state NOT IN ('archived','superseded')", (source_id,)).fetchone()[0])
            rows = conn.execute("SELECT chunk_id, content_hash FROM memory_chunks WHERE source_id = ? AND memory_state NOT IN ('archived','superseded') ORDER BY chunk_id LIMIT ?", (source_id, max_rows)).fetchall()
        return {
            "status": "ready",
            "source_id": source_id,
            "count": total,
            "exact": total <= max_rows,
            "chunks": {str(row["chunk_id"]): str(row["content_hash"] or "") for row in rows},
        }
    except Exception as exc:
        return {"status": "degraded", "source_id": source_id, "reason": "canonical_snapshot_failed", "error": str(exc)[:500]}


def _collection_drift(collection: Any, source_id: str, *, max_rows: int = 5000) -> dict[str, Any]:
    canonical = _canonical_chunk_snapshot(source_id, max_rows=max_rows)
    if canonical.get("status") != "ready":
        return {"status": canonical.get("status") or "not_available", "source_id": source_id, "canonical": canonical}
    try:
        index_count = int(collection.count())
    except Exception:
        index_count = -1
    result: dict[str, Any] = {
        "status": "ready",
        "source_id": source_id,
        "canonical_count": int(canonical.get("count") or 0),
        "indexed_count": index_count,
        "exact": bool(canonical.get("exact")) and 0 <= index_count <= max_rows,
        "missing_count": max(0, int(canonical.get("count") or 0) - max(0, index_count)),
        "orphan_count": max(0, max(0, index_count) - int(canonical.get("count") or 0)),
        "stale_count": 0,
        "missing_sample": [],
        "orphan_sample": [],
        "stale_sample": [],
    }
    if result["exact"]:
        try:
            raw = collection.get(limit=max_rows, include=["metadatas"])
            ids = [str(item) for item in (raw.get("ids") or [])]
            metas = raw.get("metadatas") or []
            indexed = {item_id: (metas[idx] if idx < len(metas) and isinstance(metas[idx], dict) else {}) for idx, item_id in enumerate(ids)}
            canonical_chunks = dict(canonical.get("chunks") or {})
            canonical_ids = set(canonical_chunks)
            indexed_ids = set(indexed)
            missing = sorted(canonical_ids - indexed_ids)
            orphan = sorted(indexed_ids - canonical_ids)
            stale = []
            for chunk_id in sorted(canonical_ids & indexed_ids):
                expected_hash = str(canonical_chunks.get(chunk_id) or "")
                actual_hash = str((indexed.get(chunk_id) or {}).get("content_hash") or "")
                if expected_hash and actual_hash != expected_hash:
                    stale.append(chunk_id)
            result.update({
                "missing_count": len(missing),
                "orphan_count": len(orphan),
                "stale_count": len(stale),
                "missing_sample": missing[:20],
                "orphan_sample": orphan[:20],
                "stale_sample": stale[:20],
            })
        except Exception as exc:
            result.update({"status": "degraded", "reason": "exact_drift_probe_failed", "error": str(exc)[:500]})
            return result
    if result["missing_count"] or result["orphan_count"] or result["stale_count"]:
        result["status"] = "reindex_required"
        result["reason"] = "canonical_vector_drift"
    return result


def _collection_name(source_id: str, engine_state: dict[str, Any] | None = None) -> str:
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    prefix = str(vector.get("collection_prefix") or "neo").strip() or "neo"
    clean = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(source_id or "global")).strip("_") or "global"
    return f"{prefix}_{clean}"


def _vector_root(state: dict[str, Any]) -> Path:
    vector = dict(state.get("vector_store") or {})
    raw = Path(str(vector.get("persist_path") or vector.get("root") or "neo_data/vector_store"))
    return raw if raw.is_absolute() else ROOT_DIR / raw


def _qualified_binding(state: dict[str, Any]) -> dict[str, Any]:
    qualification = dict(state.get("semantic_qualification") or {})
    current_config_fp = semantic_config_fingerprint(state)
    if not qualification.get("config_fingerprint") or qualification.get("config_fingerprint") != current_config_fp:
        return {"status": "qualification_required", "reason": "semantic_qualification_stale_or_missing"}
    embedding = dict(qualification.get("embedding") or {})
    if embedding.get("status") != "passed":
        return {"status": "blocked", "reason": "embedding_not_qualified", "qualification_status": qualification.get("status")}
    profile_fp = str(embedding.get("profile_fingerprint") or "")
    dimension = int(embedding.get("dimension") or 0)
    if not profile_fp or dimension <= 0:
        return {"status": "blocked", "reason": "qualified_embedding_missing_profile_identity"}
    return {
        "schema_id": VECTOR_BINDING_SCHEMA_ID,
        "status": "ready",
        "profile_fingerprint": profile_fp,
        "profile_fingerprint_short": fingerprint_short(profile_fp),
        "dimension": dimension,
        "model_id": str(embedding.get("model_id") or ""),
        "provider": str(embedding.get("provider") or ""),
        "qualified_at": qualification.get("qualified_at"),
    }


def _binding_metadata(binding: dict[str, Any]) -> dict[str, Any]:
    return {
        "neo_binding_schema": VECTOR_BINDING_SCHEMA_ID,
        "neo_embedding_profile_fingerprint": str(binding.get("profile_fingerprint") or ""),
        "neo_embedding_dimension": int(binding.get("dimension") or 0),
        "neo_embedding_model_id": str(binding.get("model_id") or "")[:500],
        "neo_embedding_provider": str(binding.get("provider") or "")[:120],
        "neo_embedding_qualified_at": str(binding.get("qualified_at") or "")[:120],
    }


def _collection_compatibility(collection: Any, binding: dict[str, Any]) -> dict[str, Any]:
    try:
        count = int(collection.count())
    except Exception:
        count = -1
    metadata = dict(getattr(collection, "metadata", None) or {})
    expected_fp = str(binding.get("profile_fingerprint") or "")
    expected_dim = int(binding.get("dimension") or 0)
    actual_fp = str(metadata.get("neo_embedding_profile_fingerprint") or "")
    try:
        actual_dim = int(metadata.get("neo_embedding_dimension") or 0)
    except Exception:
        actual_dim = 0
    if count == 0:
        return {
            "status": "bindable_empty",
            "count": count,
            "expected_profile_fingerprint": expected_fp,
            "actual_profile_fingerprint": actual_fp,
            "expected_dimension": expected_dim,
            "actual_dimension": actual_dim,
            "metadata": metadata,
        }
    if not actual_fp:
        return {
            "status": "reindex_required",
            "reason": "legacy_collection_missing_embedding_profile_fingerprint",
            "count": count,
            "expected_profile_fingerprint": expected_fp,
            "actual_profile_fingerprint": "",
            "expected_dimension": expected_dim,
            "actual_dimension": actual_dim,
            "metadata": metadata,
        }
    if actual_fp != expected_fp:
        return {
            "status": "reindex_required",
            "reason": "embedding_profile_fingerprint_mismatch",
            "count": count,
            "expected_profile_fingerprint": expected_fp,
            "actual_profile_fingerprint": actual_fp,
            "expected_dimension": expected_dim,
            "actual_dimension": actual_dim,
            "metadata": metadata,
        }
    if expected_dim and actual_dim and actual_dim != expected_dim:
        return {
            "status": "reindex_required",
            "reason": "embedding_dimension_mismatch",
            "count": count,
            "expected_profile_fingerprint": expected_fp,
            "actual_profile_fingerprint": actual_fp,
            "expected_dimension": expected_dim,
            "actual_dimension": actual_dim,
            "metadata": metadata,
        }
    return {
        "status": "ready",
        "count": count,
        "expected_profile_fingerprint": expected_fp,
        "actual_profile_fingerprint": actual_fp,
        "expected_dimension": expected_dim,
        "actual_dimension": actual_dim,
        "metadata": metadata,
    }


def _validate_vectors(vectors: list[list[float]], binding: dict[str, Any]) -> tuple[bool, str]:
    expected_dim = int(binding.get("dimension") or 0)
    if expected_dim <= 0:
        return False, "qualified_embedding_dimension_missing"
    for vector in vectors:
        if not isinstance(vector, list) or len(vector) != expected_dim:
            return False, f"vector_dimension_mismatch_expected_{expected_dim}"
        try:
            if not all(math.isfinite(float(value)) for value in vector):
                return False, "non_finite_vector"
        except Exception:
            return False, "invalid_vector_value"
    return True, ""


def vector_store_status(engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    active_store = str(vector.get("active_store") or "sqlite_vector_index")
    try:
        import chromadb  # type: ignore  # noqa: F401
        chroma_available = True
    except Exception:
        chroma_available = False
    binding = _qualified_binding(state)
    compatibility = chroma_semantic_compatibility(
        engine_state=state,
        profile_fingerprint=str(binding.get("profile_fingerprint") or ""),
        dimension=int(binding.get("dimension") or 0),
        model_id=str(binding.get("model_id") or ""),
    ) if binding.get("status") == "ready" else {"status": binding.get("status"), "reason": binding.get("reason")}
    status = "ready"
    if active_store == "chroma" and not chroma_available:
        status = "degraded"
    if compatibility.get("status") == "reindex_required":
        status = "reindex_required"
    elif compatibility.get("status") in {"blocked", "qualification_required", "unavailable"}:
        status = "degraded"
    return {
        "schema_id": VECTOR_STORE_SCHEMA_ID,
        "status": status,
        "active_store": active_store,
        "chroma_available": chroma_available,
        "sqlite_source_of_truth": True,
        "write_enabled": bool(vector.get("write_enabled")),
        "root": vector.get("root") or vector.get("persist_path") or "neo_data/vector_store",
        "embedding_binding": binding,
        "semantic_compatibility": compatibility,
        "note": "Chroma is a disposable semantic projection. Every non-empty collection must match the currently qualified embedding profile fingerprint before reads/writes are allowed.",
    }


def chroma_semantic_compatibility(
    *,
    engine_state: dict[str, Any] | None = None,
    profile_fingerprint: str = "",
    dimension: int = 0,
    model_id: str = "",
) -> dict[str, Any]:
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    active_store = str(vector.get("active_store") or "sqlite_vector_index")
    if active_store != "chroma":
        return {"status": "ready", "mode": "not_applicable", "active_store": active_store, "collections": [], "reindex_required": False}
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": "chromadb_missing", "error": str(exc)[:500], "collections": [], "reindex_required": False}
    if not profile_fingerprint or int(dimension or 0) <= 0:
        binding = _qualified_binding(state)
        if binding.get("status") != "ready":
            return {"status": binding.get("status") or "blocked", "reason": binding.get("reason") or "embedding_not_qualified", "collections": [], "reindex_required": False}
    else:
        binding = {
            "status": "ready",
            "profile_fingerprint": profile_fingerprint,
            "profile_fingerprint_short": fingerprint_short(profile_fingerprint),
            "dimension": int(dimension),
            "model_id": model_id,
            "provider": str(((state.get("semantic_qualification") or {}).get("embedding") or {}).get("provider") or ""),
            "qualified_at": (state.get("semantic_qualification") or {}).get("qualified_at"),
        }
    root = _vector_root(state)
    root.mkdir(parents=True, exist_ok=True)
    prefix = str(vector.get("collection_prefix") or "neo").strip() or "neo"
    try:
        client = chromadb.PersistentClient(path=str(root))
        listed = client.list_collections()
    except Exception as exc:
        return {"status": "unavailable", "reason": "chroma_open_failed", "error": str(exc)[:700], "collections": [], "reindex_required": False}
    rows: list[dict[str, Any]] = []
    for raw in listed or []:
        name = str(getattr(raw, "name", raw) or "")
        if not name or not name.startswith(f"{prefix}_"):
            continue
        try:
            collection = raw if hasattr(raw, "count") and hasattr(raw, "metadata") else client.get_collection(name)
            comp = _collection_compatibility(collection, binding)
            source_id = name[len(prefix) + 1:] if name.startswith(f"{prefix}_") else ""
            drift = _collection_drift(collection, source_id) if comp.get("status") == "ready" else {"status": "not_checked", "reason": "profile_incompatible"}
            if drift.get("status") == "reindex_required":
                comp = {**comp, "status": "reindex_required", "reason": drift.get("reason") or "canonical_vector_drift"}
            rows.append({"collection": name, "source_id": source_id, **comp, "drift": drift})
        except Exception as exc:
            rows.append({"collection": name, "status": "degraded", "reason": "collection_probe_failed", "error": str(exc)[:500]})
    stale = [row for row in rows if row.get("status") == "reindex_required"]
    degraded = [row for row in rows if row.get("status") == "degraded"]
    status = "reindex_required" if stale else ("degraded" if degraded else "ready")
    missing_vectors = sum(int((row.get("drift") or {}).get("missing_count") or 0) for row in rows)
    orphan_vectors = sum(int((row.get("drift") or {}).get("orphan_count") or 0) for row in rows)
    stale_vectors = sum(int((row.get("drift") or {}).get("stale_count") or 0) for row in rows)
    return {
        "status": status,
        "active_store": active_store,
        "root": str(root),
        "read_enabled": status == "ready",
        "write_enabled": bool(vector.get("write_enabled")) and status == "ready",
        "configured_write_enabled": bool(vector.get("write_enabled")),
        "binding": binding,
        "collection_count": len(rows),
        "stale_collection_count": len(stale),
        "reindex_sources": sorted({str(row.get("source_id") or "") for row in stale if str(row.get("source_id") or "")}),
        "missing_vector_count": missing_vectors,
        "orphan_vector_count": orphan_vectors,
        "stale_vector_count": stale_vectors,
        "reindex_required": bool(stale),
        "collections": rows,
    }


def upsert_chroma_chunks(
    chunks: list[dict[str, Any]],
    vectors: list[list[float]],
    *,
    source_id: str,
    engine_state: dict[str, Any] | None = None,
    force_reindex: bool = False,
) -> dict[str, Any]:
    """Mirror qualified vectors into Chroma without ever mixing vector spaces."""
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    if str(vector.get("active_store") or "") != "chroma" or not bool(vector.get("write_enabled")):
        return {"status": "skipped", "reason": "chroma_not_active_or_write_disabled", "count": 0}
    binding = _qualified_binding(state)
    if binding.get("status") != "ready":
        return {"status": "blocked", "reason": binding.get("reason") or "embedding_not_qualified", "count": 0, "embedding_binding": binding}
    valid, reason = _validate_vectors(vectors, binding)
    if not valid:
        return {"status": "blocked", "reason": reason, "count": 0, "embedding_binding": binding}
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": "chromadb_missing", "error": str(exc)[:500], "count": 0}
    root = _vector_root(state)
    root.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(root))
    name = _collection_name(source_id, state)
    metadata = _binding_metadata(binding)
    try:
        collection = client.get_collection(name)
        comp = _collection_compatibility(collection, binding)
        if comp.get("status") == "reindex_required":
            if not force_reindex:
                return {"status": "reindex_required", "reason": comp.get("reason"), "collection": name, "count": 0, "compatibility": comp, "embedding_binding": binding}
            client.delete_collection(name)
            collection = client.create_collection(name, metadata=metadata)
            comp = {"status": "reset_for_reindex", "reason": "explicit_force_reindex"}
        elif comp.get("status") == "bindable_empty":
            collection.modify(metadata=metadata)
    except Exception:
        collection = client.get_or_create_collection(name, metadata=metadata)
        comp = _collection_compatibility(collection, binding)
        if comp.get("status") == "bindable_empty":
            try:
                collection.modify(metadata=metadata)
            except Exception:
                pass
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
            "content_hash": str(chunk.get("content_hash") or ""),
            "embedding_profile_fingerprint": str(binding.get("profile_fingerprint") or ""),
            "embedding_dimension": int(binding.get("dimension") or 0),
        })
    collection.upsert(ids=ids, documents=docs, embeddings=vectors, metadatas=metas)
    return {"status": "mirrored", "store": "chroma", "collection": collection.name, "count": len(ids), "embedding_binding": binding, "compatibility": comp}


def query_chroma_chunks(query_vector: list[float], *, source_id: str, limit: int = 12, engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Query Chroma only when its vector space matches the qualified profile."""
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    if str(vector.get("active_store") or "") != "chroma":
        return {"status": "skipped", "reason": "chroma_not_active", "results": [], "count": 0}
    binding = _qualified_binding(state)
    if binding.get("status") != "ready":
        return {"status": "blocked", "reason": binding.get("reason") or "embedding_not_qualified", "results": [], "count": 0, "embedding_binding": binding}
    valid, reason = _validate_vectors([query_vector], binding)
    if not valid:
        return {"status": "blocked", "reason": reason, "results": [], "count": 0, "embedding_binding": binding}
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": "chromadb_missing", "error": str(exc)[:500], "results": [], "count": 0}
    try:
        root = _vector_root(state)
        client = chromadb.PersistentClient(path=str(root))
        collection_name = _collection_name(source_id, state)
        collection = client.get_collection(collection_name)
        comp = _collection_compatibility(collection, binding)
        if comp.get("status") == "reindex_required":
            return {"status": "reindex_required", "reason": comp.get("reason"), "collection": collection_name, "results": [], "count": 0, "compatibility": comp, "embedding_binding": binding}
        if comp.get("status") == "bindable_empty":
            return {"status": "ready", "store": "chroma", "collection": collection_name, "results": [], "count": 0, "compatibility": comp, "embedding_binding": binding}
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
    return {"status": "ready", "store": "chroma", "collection": collection_name, "results": results, "count": len(results), "embedding_binding": binding, "compatibility": comp}



def delete_chroma_collection(source_id: str, engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Delete one disposable Chroma collection for lifecycle cleanup.

    Unlike reset/reindex, cleanup is allowed even when vector writes are disabled:
    deleting an ephemeral chat must not leave a private semantic projection behind.
    Canonical source data is never touched here.
    """
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": "chromadb_missing", "error": str(exc)[:500], "source_id": source_id}
    try:
        root = _vector_root(state)
        root.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(root))
        name = _collection_name(source_id, state)
        try:
            client.delete_collection(name)
            return {"status": "deleted", "collection": name, "source_id": source_id}
        except Exception as exc:
            return {"status": "clean", "collection": name, "source_id": source_id, "note": str(exc)[:300]}
    except Exception as exc:
        return {"status": "failed", "reason": "chroma_delete_error", "error": str(exc)[:700], "source_id": source_id}

def reset_chroma_collection(source_id: str, engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    """Reset one disposable Chroma mirror collection; canonical SQLite/source data is untouched."""
    state = engine_state or admin_engine_state_payload()
    vector = dict(state.get("vector_store") or {})
    if str(vector.get("active_store") or "") != "chroma" or not bool(vector.get("write_enabled")):
        return {"status": "skipped", "reason": "chroma_not_active_or_write_disabled", "source_id": source_id}
    try:
        import chromadb  # type: ignore
    except Exception as exc:
        return {"status": "unavailable", "reason": "chromadb_missing", "error": str(exc)[:500], "source_id": source_id}
    try:
        root = _vector_root(state)
        root.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(root))
        name = _collection_name(source_id, state)
        try:
            client.delete_collection(name)
            return {"status": "reset", "collection": name, "source_id": source_id}
        except Exception as exc:
            return {"status": "clean", "collection": name, "source_id": source_id, "note": str(exc)[:300]}
    except Exception as exc:
        return {"status": "failed", "reason": "chroma_reset_error", "error": str(exc)[:700], "source_id": source_id}
