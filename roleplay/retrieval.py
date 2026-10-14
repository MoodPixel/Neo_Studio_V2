from __future__ import annotations

from typing import Any

from neo_app.roleplay.engine_bridge import roleplay_engine_bridge_state
from neo_app.admin.semantic_engine import embed_texts, rerank_results, semantic_engine_state_payload
from neo_app.roleplay.sqlite_store import create_retrieval_trace_placeholder, ensure_roleplay_memory_schema, index_roleplay_memory_vectors, roleplay_sqlite_state_payload, search_roleplay_memory_foundation, search_roleplay_vectors


def roleplay_retrieval_state_payload() -> dict[str, Any]:
    sqlite_state = ensure_roleplay_memory_schema()
    engine_bridge = roleplay_engine_bridge_state()
    semantic_state = semantic_engine_state_payload()
    table_counts = sqlite_state.get("table_counts") or {}
    embedding = dict(semantic_state.get("embedding") or {})
    reranker = dict(semantic_state.get("reranker") or {})
    return {
        "schema_id": "neo.roleplay.retrieval.v2",
        "version": "2.0.0-aci5-qualified-semantic",
        "surface_id": "roleplay",
        "status": "active",
        "ready": bool(sqlite_state.get("ready")),
        "sqlite": roleplay_sqlite_state_payload(),
        "admin_engine_bridge": {
            "embedding_ready": bool(embedding.get("qualified")),
            "reranker_ready": bool(reranker.get("qualified")),
            "semantic_status": semantic_state.get("status"),
            "embedding_profile_fingerprint": embedding.get("profile_fingerprint") or "",
            "vector_store_ready": bool(engine_bridge.get("vector_store_ready")),
            "retrieval_defaults_ready": bool(engine_bridge.get("retrieval_defaults_ready")),
        },
        "available_memory_rows": {
            "entities": int(table_counts.get("rp_entities") or 0),
            "memory_fragments": int(table_counts.get("rp_memory_fragments") or 0),
            "shared_memories": int(table_counts.get("rp_shared_memories") or 0),
            "continuity_rows": int(table_counts.get("rp_continuity_rows") or 0),
            "turn_summaries": int(table_counts.get("rp_turn_summaries") or 0),
            "story_checkpoints": int(table_counts.get("rp_story_checkpoints") or 0),
            "retrieval_traces": int(table_counts.get("rp_retrieval_traces") or 0),
        },
        "search_foundation": {
            "ready": bool(sqlite_state.get("ready")),
            "mode": "sqlite_keyword",
            "supported_scopes": ["global", "scene", "story", "runtime", "entity"],
            "supported_memory_types": ["entities", "memory_fragments", "shared_memories", "continuity", "turn_summaries", "story_checkpoints"],
        },
        "semantic_search": {
            "ready": bool(semantic_state.get("semantic_search_enabled")) and int(table_counts.get("rp_vector_index") or 0) > 0,
            "mode": "qualified_semantic_only_with_keyword_fallback",
            "indexed_rows": int(table_counts.get("rp_vector_index") or 0),
            "index_endpoint": "/api/roleplay/retrieval/index-roleplay-memory",
            "search_endpoint": "/api/roleplay/retrieval/search-semantic",
        },
        "semantic_engine": semantic_state,
        "active_features": [
            "qualified_external_embedding_execution",
            "qualified_external_reranker_execution",
            "keyword_retrieval_when_semantic_unavailable",
            "fingerprint_bound_sqlite_vector_index",
            "admin_background_index_queue",
        ],
        "deferred_features": [],
    }

def create_retrieval_trace_placeholder_payload(payload: dict[str, Any]) -> dict[str, Any]:
    engine_bridge = roleplay_engine_bridge_state()
    trace = create_retrieval_trace_placeholder(
        query=str(payload.get("query") or ""),
        scope_id=str(payload.get("scope_id") or ""),
        engine_snapshot={
            "embedding_provider": (engine_bridge.get("embedding_profiles") or {}).get("active_provider_id") or "",
            "reranker_provider": (engine_bridge.get("reranker_profiles") or {}).get("active_provider_id") or "",
            "vector_store_ready": bool(engine_bridge.get("vector_store_ready")),
        },
    )
    return {
        "schema_id": "neo.roleplay.retrieval.trace.placeholder.v1",
        "status": "saved",
        "trace": trace,
        "retrieval": roleplay_retrieval_state_payload(),
    }



def search_retrieval_foundation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    memory_types = payload.get("memory_types") or payload.get("types") or []
    if isinstance(memory_types, str):
        memory_types = [item.strip() for item in memory_types.split(",") if item.strip()]
    result = search_roleplay_memory_foundation(
        query=str(payload.get("query") or ""),
        scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
        memory_types=memory_types,
        limit=int(payload.get("limit") or 12),
        source=str(payload.get("source") or "roleplay"),
    )
    return {
        "schema_id": "neo.roleplay.retrieval.search.keyword.v1",
        "status": "searched",
        "mode": "sqlite_keyword",
        "search": result,
        "retrieval": roleplay_retrieval_state_payload(),
    }



def index_roleplay_memory_vectors_payload(payload: dict[str, Any]) -> dict[str, Any]:
    engine_state = roleplay_engine_bridge_state()
    semantic = semantic_engine_state_payload()
    embedding = dict(semantic.get("embedding") or {})
    profile_fp = str(embedding.get("profile_fingerprint") or "")
    dimension = int(embedding.get("dimension") or 0)
    if not embedding.get("qualified") or not profile_fp or dimension <= 0:
        return {
            "schema_id": "neo.roleplay.retrieval.vector.index.v3",
            "status": "semantic_unavailable",
            "index": {"status": "skipped", "indexed_count": 0, "reason": semantic.get("status") or "embedding_not_qualified"},
            "semantic_engine": semantic,
            "retrieval": roleplay_retrieval_state_payload(),
        }

    def embedding_fn(texts: list[str]) -> dict[str, Any]:
        result = embed_texts(texts, engine_state=engine_state, dimension=dimension, allow_fallback=False, purpose="document")
        if str(result.get("profile_fingerprint") or "") != profile_fp:
            raise RuntimeError("Roleplay embedding profile changed after qualification")
        return result

    result = index_roleplay_memory_vectors(
        scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
        limit=int(payload.get("limit") or 500),
        force=bool(payload.get("force") or False),
        model_id=profile_fp,
        dimension=dimension,
        embedding_fn=embedding_fn,
        embedding_mode="aci5_qualified_semantic",
    )
    result["embedding_profile_fingerprint"] = profile_fp
    return {
        "schema_id": "neo.roleplay.retrieval.vector.index.v3",
        "status": result.get("status"),
        "index": result,
        "semantic_engine": semantic_engine_state_payload(),
        "retrieval": roleplay_retrieval_state_payload(),
    }

def search_retrieval_semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    engine_state = roleplay_engine_bridge_state()
    semantic = semantic_engine_state_payload()
    embedding = dict(semantic.get("embedding") or {})
    reranker = dict(semantic.get("reranker") or {})
    profile_fp = str(embedding.get("profile_fingerprint") or "")
    dimension = int(embedding.get("dimension") or 0)
    query = str(payload.get("query") or "")
    fallback_keyword = bool(payload.get("fallback_keyword", True))
    if not embedding.get("qualified") or not profile_fp or dimension <= 0 or semantic.get("status") in {"blocked", "qualification_required", "reindex_required"}:
        fallback = search_roleplay_memory_foundation(
            query=query,
            scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
            memory_types=[],
            limit=int(payload.get("limit") or 12),
            source=str(payload.get("source") or "roleplay_semantic_unavailable"),
        ) if fallback_keyword else {"results": [], "result_count": 0}
        return {
            "schema_id": "neo.roleplay.retrieval.semantic.search.v3",
            "status": "degraded",
            "mode": "keyword_fallback" if fallback_keyword else "semantic_unavailable",
            "search": {"status": "semantic_unavailable", "results": [], "result_count": 0, "fallback": fallback, "embedding_engine": {"status": semantic.get("status")}},
            "semantic_engine": semantic,
            "retrieval": roleplay_retrieval_state_payload(),
        }
    use_rerank = bool(payload.get("rerank", True)) and str(reranker.get("qualification_status") or "") in {"passed", "degraded"} and reranker.get("provider") != "none"
    try:
        query_embedding_result = embed_texts([query], engine_state=engine_state, dimension=dimension, allow_fallback=False, purpose="query")
        if str(query_embedding_result.get("profile_fingerprint") or "") != profile_fp:
            raise RuntimeError("Roleplay query embedding profile changed after qualification")
    except Exception as exc:
        fallback = search_roleplay_memory_foundation(
            query=query,
            scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
            memory_types=[],
            limit=int(payload.get("limit") or 12),
            source=str(payload.get("source") or "roleplay_semantic_runtime_failure"),
        ) if fallback_keyword else {"results": [], "result_count": 0}
        return {
            "schema_id": "neo.roleplay.retrieval.semantic.search.v3",
            "status": "degraded",
            "mode": "keyword_fallback" if fallback_keyword else "semantic_runtime_failure",
            "search": {"status": "semantic_runtime_failure", "error": str(exc)[:800], "results": [], "result_count": 0, "fallback": fallback},
            "semantic_engine": semantic_engine_state_payload(),
            "retrieval": roleplay_retrieval_state_payload(),
        }
    vectors = query_embedding_result.get("vectors") or []
    query_vec = vectors[0] if vectors else None
    result = search_roleplay_vectors(
        query=query,
        scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
        limit=int(payload.get("limit") or 12),
        rerank=False,
        min_score=float(payload.get("min_score") or -1.0),
        model_id=profile_fp,
        dimension=dimension,
        source=str(payload.get("source") or "roleplay"),
        query_embedding=query_vec,
        embedding_mode="aci5_qualified_semantic",
        reranker_label=str(reranker.get("provider") or "disabled") if use_rerank else "disabled",
    )
    if use_rerank and result.get("results"):
        try:
            allow_fallback = str(reranker.get("qualification_status") or "") == "degraded" and reranker.get("provider") == "lexical_overlap"
            rerank_result = rerank_results(query, result.get("results") or [], engine_state=engine_state, top_n=int(payload.get("limit") or 12), allow_fallback=allow_fallback)
            result["results"] = rerank_result.get("results") or result.get("results") or []
            result["result_count"] = len(result["results"])
            result["reranker_engine"] = {k: v for k, v in rerank_result.items() if k != "results"}
            result["mode"] = f"{result.get('mode')}_plus_{rerank_result.get('mode')}"
        except Exception as exc:
            result["reranker_engine"] = {"status": "degraded", "reason": "qualified_reranker_runtime_failed", "error": str(exc)[:700]}
    result["embedding_engine"] = {k: v for k, v in query_embedding_result.items() if k != "vectors"}
    if not result.get("results") and fallback_keyword:
        result["fallback"] = search_roleplay_memory_foundation(
            query=query,
            scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
            memory_types=[],
            limit=int(payload.get("limit") or 12),
            source=str(payload.get("source") or "roleplay_semantic_fallback"),
        )
    return {
        "schema_id": "neo.roleplay.retrieval.semantic.search.v3",
        "status": "searched",
        "mode": "vector_search",
        "search": result,
        "semantic_engine": semantic_engine_state_payload(),
        "retrieval": roleplay_retrieval_state_payload(),
    }

