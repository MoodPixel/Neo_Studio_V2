from __future__ import annotations

from typing import Any

from neo_app.roleplay.engine_bridge import roleplay_engine_bridge_state
from neo_app.roleplay.forge import list_forge_records
from neo_app.core.pydantic_compat import model_to_dict
from neo_app.roleplay.sqlite_store import (
    ensure_roleplay_memory_schema,
    roleplay_sqlite_state_payload,
    upsert_forge_record_memory,
)
from neo_app.roleplay.human_memory import roleplay_human_memory_state_payload

MEMORY_NAMESPACES = [
    "roleplay",
    "roleplay.project",
    "roleplay.scene",
    "roleplay.story",
    "roleplay.character",
    "roleplay.canon",
    "roleplay.memory",
    "roleplay.runtime",
    "roleplay.forge",
]


def roleplay_memory_state_payload() -> dict[str, Any]:
    sqlite_state = ensure_roleplay_memory_schema()
    engine_bridge = roleplay_engine_bridge_state()
    return {
        "schema_id": "neo.roleplay.memory.v1",
        "version": "1.0.0-advanced-memory",
        "surface_id": "roleplay",
        "status": "active",
        "ready": bool(sqlite_state.get("ready")),
        "sqlite": sqlite_state,
        "namespaces": MEMORY_NAMESPACES,
        "admin_engine_bridge": {
            "ready": bool(engine_bridge.get("ready")),
            "embedding_provider": engine_bridge.get("embedding_profiles", {}).get("active_provider_id") or "",
            "embedding_model_path": engine_bridge.get("embedding_profiles", {}).get("active_model_path") or "",
            "reranker_provider": engine_bridge.get("reranker_profiles", {}).get("active_provider_id") or "",
            "reranker_model_path": engine_bridge.get("reranker_profiles", {}).get("active_model_path") or "",
            "vector_store_ready": bool(engine_bridge.get("vector_store_ready")),
            "vector_path": (engine_bridge.get("vector_store") or {}).get("persist_path") or (engine_bridge.get("vector_store") or {}).get("root") or "",
        },
        "pipelines": {
            "forge_record_to_entity": "ready",
            "forge_record_to_memory_fragment": "ready_deep_builder_compile",
            "scene_setup_to_continuity": "ready",
            "scene_turn_writeback": "ready",
            "storyline_to_shared_memory": "ready_foundation_shared",
            "story_session_to_continuity": "ready_foundation_continuity",
            "story_checkpoint_memory": "ready_foundation_checkpoint",
            "vector_indexing": "ready_external_or_local_fallback",
            "reranked_retrieval": "ready_external_or_lexical_fallback",
        },
        "human_memory": roleplay_human_memory_state_payload(),
        "active_features": [
            "forge_to_entity_graph",
            "scene_turn_writeback",
            "story_checkpoint_memory",
            "sqlite_vector_index",
            "external_semantic_engine_when_configured",
            "local_semantic_fallback",
            "human_scene_memory_packets",
            "character_knowledge_boundaries",
            "emotional_continuity_state",
            "unresolved_thread_tracking",
        ],
        "deferred_features": [
            "deep_builder_record_memory_compiler",
            "sandbox_scoped_fragment_compile",
            "compiled_entity_graph_edges",
            "story_checkpoint_publish_to_shared_world",
            "continuity_merge_checks",
            "chroma_collection_write",
            "full_provenance_graph_ui",
        ],
    }


def sync_forge_memory_foundation_payload(kind: str | None = None) -> dict[str, Any]:
    ensure_roleplay_memory_schema()
    records = [model_to_dict(record) for record in list_forge_records(kind)]
    synced: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for record in records:
        try:
            synced.append(upsert_forge_record_memory(record))
        except Exception as exc:
            errors.append({"record_id": record.get("record_id") or "", "error": str(exc)})
    return {
        "schema_id": "neo.roleplay.memory.sync_foundation.v1",
        "status": "synced" if not errors else "partial",
        "kind": kind or "all",
        "record_count": len(records),
        "synced_count": len(synced),
        "error_count": len(errors),
        "synced": synced,
        "errors": errors,
        "memory": roleplay_memory_state_payload(),
    }
