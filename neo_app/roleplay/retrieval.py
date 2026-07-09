from __future__ import annotations

from typing import Any

from neo_app.roleplay.engine_bridge import roleplay_engine_bridge_state
from neo_app.admin.semantic_engine import embed_texts, rerank_results, semantic_engine_state_payload
from neo_app.roleplay.sqlite_store import create_retrieval_trace_placeholder, ensure_roleplay_memory_schema, index_roleplay_memory_vectors, roleplay_sqlite_state_payload, search_roleplay_memory_foundation, search_roleplay_vectors


def roleplay_retrieval_state_payload() -> dict[str, Any]:
    sqlite_state = ensure_roleplay_memory_schema()
    engine_bridge = roleplay_engine_bridge_state()
    table_counts = sqlite_state.get("table_counts") or {}
    return {
        "schema_id": "neo.roleplay.retrieval.v1",
        "version": "1.0.0-retrieval",
        "surface_id": "roleplay",
        "status": "active",
        "ready": bool(sqlite_state.get("ready")),
        "sqlite": roleplay_sqlite_state_payload(),
        "admin_engine_bridge": {
            "embedding_ready": bool((engine_bridge.get("embedding_profiles") or {}).get("active_model_path")),
            "reranker_ready": bool((engine_bridge.get("reranker_profiles") or {}).get("active_model_path")),
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
            "ready": int(table_counts.get("rp_vector_index") or 0) > 0,
            "mode": "external_semantic_when_configured_with_local_fallback",
            "indexed_rows": int(table_counts.get("rp_vector_index") or 0),
            "index_endpoint": "/api/roleplay/retrieval/index-roleplay-memory",
            "search_endpoint": "/api/roleplay/retrieval/search-semantic",
        },
        "semantic_engine": semantic_engine_state_payload(),
        "active_features": [
            "external_embedding_execution_when_configured",
            "external_reranker_execution_when_configured",
            "local_hash_embedding_fallback",
            "lexical_rerank_fallback",
            "sqlite_vector_index_write",
            "admin_background_index_queue",
        ],
        "deferred_features": [
            "chroma_collection_write",
            "automatic_scene_packet_memory_injection_upgrade",
        ],
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
    embeddings = engine_state.get("embedding_profiles") or {}
    model_id = str(embeddings.get("active_model_name") or embeddings.get("active_model_path") or embeddings.get("active_profile_id") or "local_hash_embeddings")
    dimension = int(embeddings.get("default_dimension") or 96)

    def embedding_fn(texts: list[str]) -> dict[str, Any]:
        return embed_texts(texts, engine_state=engine_state, dimension=dimension, allow_fallback=True)

    result = index_roleplay_memory_vectors(
        scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
        limit=int(payload.get("limit") or 500),
        force=bool(payload.get("force") or False),
        model_id=model_id,
        dimension=dimension,
        embedding_fn=embedding_fn,
        embedding_mode="admin_external_semantic_or_fallback",
    )
    return {
        "schema_id": "neo.roleplay.retrieval.vector.index.v2",
        "status": result.get("status"),
        "index": result,
        "semantic_engine": semantic_engine_state_payload(),
        "retrieval": roleplay_retrieval_state_payload(),
    }

def search_retrieval_semantic_payload(payload: dict[str, Any]) -> dict[str, Any]:
    engine_state = roleplay_engine_bridge_state()
    embeddings = engine_state.get("embedding_profiles") or {}
    reranker = engine_state.get("reranker_profiles") or {}
    model_id = str(embeddings.get("active_model_name") or embeddings.get("active_model_path") or embeddings.get("active_profile_id") or "local_hash_embeddings")
    dimension = int(embeddings.get("default_dimension") or 96)
    use_rerank = bool(payload.get("rerank", True)) and reranker.get("active_provider_id") != "none"
    query = str(payload.get("query") or "")
    query_embedding_result = embed_texts([query], engine_state=engine_state, dimension=dimension, allow_fallback=True)
    vectors = query_embedding_result.get("vectors") or []
    query_vec = vectors[0] if vectors else None
    result = search_roleplay_vectors(
        query=query,
        scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
        limit=int(payload.get("limit") or 12),
        rerank=False,
        min_score=float(payload.get("min_score") or -1.0),
        model_id=str(query_embedding_result.get("model_id") or model_id),
        dimension=int(query_embedding_result.get("dimension") or dimension),
        source=str(payload.get("source") or "roleplay"),
        query_embedding=query_vec,
        embedding_mode=str(query_embedding_result.get("mode") or "semantic_search"),
        reranker_label="external_or_fallback" if use_rerank else "disabled",
    )
    if use_rerank and result.get("results"):
        rerank_result = rerank_results(query, result.get("results") or [], engine_state=engine_state, top_n=int(payload.get("limit") or 12), allow_fallback=True)
        result["results"] = rerank_result.get("results") or result.get("results") or []
        result["result_count"] = len(result["results"])
        result["reranker_engine"] = {k: v for k, v in rerank_result.items() if k != "results"}
        result["mode"] = f"{result.get('mode')}_plus_{rerank_result.get('mode')}"
    result["embedding_engine"] = {k: v for k, v in query_embedding_result.items() if k != "vectors"}
    if not result.get("results") and bool(payload.get("fallback_keyword", True)):
        fallback = search_roleplay_memory_foundation(
            query=query,
            scope_id=str(payload.get("scope_id") or payload.get("scope") or ""),
            memory_types=[],
            limit=int(payload.get("limit") or 12),
            source=str(payload.get("source") or "roleplay_semantic_fallback"),
        )
        result["fallback"] = fallback
    return {
        "schema_id": "neo.roleplay.retrieval.semantic.search.v2",
        "status": "searched",
        "mode": "vector_search",
        "search": result,
        "semantic_engine": semantic_engine_state_payload(),
        "retrieval": roleplay_retrieval_state_payload(),
    }
