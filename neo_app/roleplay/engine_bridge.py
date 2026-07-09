from __future__ import annotations

from typing import Any

from neo_app.admin.engine import admin_engine_state_payload


def roleplay_engine_bridge_state() -> dict[str, Any]:
    admin_engine = admin_engine_state_payload()
    readiness = admin_engine.get("readiness", {})
    return {
        "schema_id": "neo.roleplay.engine_bridge.v1",
        "version": "0.1.0-engine-bridge-foundation",
        "surface_id": "roleplay",
        "status": "foundation",
        "source": "admin_engine",
        "admin_endpoint": "/api/admin/engine/state",
        "ready": bool(readiness.get("ready")),
        "text_bridge_ready": bool(readiness.get("text_bridge_ready")),
        "embeddings_ready": bool(readiness.get("embeddings_ready")),
        "reranker_ready": bool(readiness.get("reranker_ready")),
        "vector_store_ready": bool(readiness.get("vector_store_ready")),
        "retrieval_defaults_ready": bool(readiness.get("retrieval_defaults_ready")),
        "runtime_defaults_ready": bool(readiness.get("runtime_defaults_ready")),
        "fallback_order": admin_engine.get("text_bridge", {}).get("fallback_order", []),
        "embedding_profiles": admin_engine.get("embedding_profiles", {}),
        "reranker_profiles": admin_engine.get("reranker_profiles", {}),
        "retrieval_defaults": admin_engine.get("retrieval_defaults", {}),
        "runtime_defaults": admin_engine.get("runtime_defaults", {}),
        "vector_store": admin_engine.get("vector_store", {}),
        "paths": admin_engine.get("paths", {}),
        "deferred_features": admin_engine.get("deferred_features", []),
    }
