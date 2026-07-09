from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json

ROOT_DIR = Path(__file__).resolve().parents[2]
ENGINE_DATA_DIR = ROOT_DIR / "neo_data" / "admin" / "engine"
VECTOR_STORE_DIR = ROOT_DIR / "neo_data" / "vector_store"

ENGINE_CONFIG_PATH = ENGINE_DATA_DIR / "engine_config.json"
TEXT_BRIDGE_PATH = ENGINE_DATA_DIR / "text_bridge.json"
EMBEDDING_PROFILES_PATH = ENGINE_DATA_DIR / "embedding_profiles.json"
RERANKER_PROFILES_PATH = ENGINE_DATA_DIR / "reranker_profiles.json"
VECTOR_STORE_PATH = ENGINE_DATA_DIR / "vector_store.json"
RETRIEVAL_DEFAULTS_PATH = ENGINE_DATA_DIR / "retrieval_defaults.json"
INDEXING_STATE_PATH = ENGINE_DATA_DIR / "indexing_state.json"
RUNTIME_DEFAULTS_PATH = ENGINE_DATA_DIR / "runtime_defaults.json"
HEALTH_SNAPSHOT_PATH = ENGINE_DATA_DIR / "health_snapshot.json"

ENGINE_SCHEMA_ID = "neo.admin.engine.v1"
ENGINE_VERSION = "0.4.2-qwen3-provider-persistence"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return dict(fallback)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, dict) else dict(fallback)
    except Exception:
        return dict(fallback)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")




def _looks_like_qwen3_reranker(value: Any) -> bool:
    text = str(value or "").lower().replace("\\", "/")
    return "qwen3" in text and "reranker" in text


def _normalize_reranker_provider(provider: Any, model_ref: Any = "") -> tuple[str, str]:
    raw = str(provider or "").strip()
    label_map = {
        "qwen3_reranker (qwen3 native)": "qwen3_reranker",
        "qwen3 native": "qwen3_reranker",
        "qwen3": "qwen3_reranker",
        "qwen3-reranker": "qwen3_reranker",
        "qwen3_reranker": "qwen3_reranker",
        "local_reranker": "local_reranker",
        "cross_encoder": "cross_encoder",
        "bge_reranker": "bge_reranker",
        "openai_compatible_reranker": "openai_compatible_reranker",
        "lexical_overlap": "lexical_overlap",
        "lexical_overlap fallback": "lexical_overlap",
        "none": "none",
    }
    key = raw.lower()
    normalized = label_map.get(key, raw or "bge_reranker")
    note = ""
    if normalized in {"cross_encoder", "bge_reranker", "local_reranker"} and _looks_like_qwen3_reranker(model_ref):
        normalized = "qwen3_reranker"
        note = "Persisted qwen3_reranker because the selected model path/name looks like Qwen3-Reranker."
    supported = {"qwen3_reranker", "local_reranker", "cross_encoder", "bge_reranker", "openai_compatible_reranker", "lexical_overlap", "none"}
    if normalized not in supported:
        note = f"Unknown reranker provider '{raw}' was kept out of the active provider and reset to cross_encoder."
        normalized = "cross_encoder"
    return normalized, note

def _merge_unique_list(existing: Any, required: list[str]) -> list[str]:
    merged: list[str] = []
    for item in list(existing or []) + list(required or []):
        value = str(item or "").strip()
        if value and value not in merged:
            merged.append(value)
    return merged


def _migrate_engine_profile_payload(path: Path, existing: dict[str, Any], defaults: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Backfill provider options into already-created Admin config files.

    Phase 18.9 added qwen3_reranker in defaults, but old installs keep
    existing JSON untouched. This migration keeps user model paths while making
    new providers visible in the UI.
    """
    changed = False
    if path == RERANKER_PROFILES_PATH:
        required = ["qwen3_reranker", "local_reranker", "cross_encoder", "bge_reranker", "openai_compatible_reranker", "lexical_overlap", "none"]
        merged = _merge_unique_list(existing.get("supported_provider_ids") or defaults.get("supported_provider_ids"), required)
        if merged != existing.get("supported_provider_ids"):
            existing["supported_provider_ids"] = merged
            changed = True
        notes = "Use qwen3_reranker for Qwen3-Reranker causal-LM yes/no scoring; cross_encoder/local_reranker are for SentenceTransformers-compatible rerankers; none disables reranking."
        if str(existing.get("notes") or "") != notes:
            existing["notes"] = notes
            changed = True
    if path == EMBEDDING_PROFILES_PATH:
        required = ["sentence_transformers", "openai_compatible_embeddings", "local_embedding_model", "local_hash_embeddings"]
        merged = _merge_unique_list(existing.get("supported_provider_ids") or defaults.get("supported_provider_ids"), required)
        if merged != existing.get("supported_provider_ids"):
            existing["supported_provider_ids"] = merged
            changed = True
    return existing, changed


def _ensure_file(path: Path, payload: dict[str, Any]) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        _write_json(path, payload)
        return dict(payload)
    existing = _read_json(path, payload)
    # Keep user-owned settings, but backfill missing foundation keys.
    changed = False
    for key, value in payload.items():
        if key not in existing:
            existing[key] = value
            changed = True
    existing, migrated = _migrate_engine_profile_payload(path, existing, payload)
    if changed or migrated:
        existing["updated_at"] = existing.get("updated_at") or _now()
        _write_json(path, existing)
    return existing


def engine_defaults() -> dict[str, dict[str, Any]]:
    stamp = _now()
    return {
        "engine_config": {
            "schema_id": ENGINE_SCHEMA_ID,
            "version": ENGINE_VERSION,
            "status": "ready",
            "owner": "admin",
            "created_at": stamp,
            "updated_at": stamp,
            "policy": "Admin owns shared Memory Engine configuration. Surfaces consume this through bridge endpoints and should not duplicate embedding, reranker, vector, or retrieval controls.",
            "lanes": ["overview", "text", "embeddings", "reranker", "vector_store", "retrieval", "indexing", "index_jobs", "runtime_defaults", "inspector"],
        },
        "text_bridge": {
            "status": "ready",
            "source": "backend_profiles",
            "profile_surfaces": ["roleplay", "text", "prompt_captioning", "assistant"],
            "fallback_order": ["roleplay", "text", "prompt_captioning", "assistant"],
            "note": "Text profiles stay in the existing backend profile system; Memory Engine exposes bridge/readiness only.",
        },
        "embedding_profiles": {
            "status": "placeholder",
            "active_profile_id": "",
            "active_provider_id": "sentence_transformers",
            "active_model_path": "",
            "active_model_name": "",
            "active_base_url": "",
            "active_api_key_env": "",
            "request_timeout": 60,
            "profiles": [],
            "supported_provider_ids": ["sentence_transformers", "openai_compatible_embeddings", "local_embedding_model", "local_hash_embeddings"],
            "default_dimension": None,
            "storage_policy": "admin_owned_surface_consumed",
            "notes": "Set a local model path or API-backed model name here. Roleplay reads this through the Memory Engine bridge.",
        },
        "reranker_profiles": {
            "status": "placeholder",
            "active_profile_id": "",
            "active_provider_id": "bge_reranker",
            "active_model_path": "",
            "active_model_name": "",
            "active_base_url": "",
            "active_api_key_env": "",
            "request_timeout": 60,
            "profiles": [],
            "supported_provider_ids": ["cross_encoder", "bge_reranker", "qwen3_reranker", "local_reranker", "openai_compatible_reranker", "lexical_overlap", "none"],
            "default_top_n": 8,
            "storage_policy": "admin_owned_surface_consumed",
            "notes": "Set a reranker path/model here. Use qwen3_reranker for Qwen3-Reranker causal-LM yes/no scoring, cross_encoder/local_reranker for SentenceTransformers-compatible rerankers, or none to disable reranking.",
        },
        "vector_store": {
            "status": "ready",
            "active_store": "sqlite_vector_index",
            "root": str(VECTOR_STORE_DIR.relative_to(ROOT_DIR)),
            "persist_path": str(VECTOR_STORE_DIR.relative_to(ROOT_DIR)),
            "collection_prefix": "neo",
            "collections": [],
            "supported_store_ids": ["chroma", "sqlite_fts", "jsonl_debug"],
            "write_enabled": False,
        },
        "retrieval_defaults": {
            "status": "ready",
            "query_top_k": 12,
            "rerank_top_n": 6,
            "max_context_chars": 9000,
            "namespaces": ["roleplay", "assistant", "global"],
            "scoring_policy": "embedding_then_optional_rerank",
            "filters": {"include_archived": False, "canon_priority": True},
        },
        "indexing_state": {
            "status": "ready",
            "last_indexed_at": None,
            "pending_jobs": [],
            "write_enabled": True,
            "planned_jobs": ["roleplay_forge_records", "roleplay_memory_fragments", "story_checkpoints", "assistant_knowledge"],
        },
        "runtime_defaults": {
            "status": "ready",
            "max_tokens": 480,
            "temperature": 0.82,
            "top_p": 0.92,
            "top_k": 60,
            "memory_window": "runtime_anchored",
            "continuity_mode": "checkpoint_first_then_session_then_storyline",
        },
        "health_snapshot": {
            "status": "ready",
            "checked_at": stamp,
            "text_bridge_ready": True,
            "embeddings_ready": False,
            "reranker_ready": False,
            "vector_store_ready": False,
            "indexing_ready": False,
            "notes": ["Foundation files created", "External semantic engine integration enabled with safe fallback", "Chroma/vector collection export-import enabled", "Background indexing job queue enabled"],
        },
    }


def ensure_admin_engine_foundation() -> dict[str, Any]:
    ENGINE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    VECTOR_STORE_DIR.mkdir(parents=True, exist_ok=True)
    defaults = engine_defaults()
    files = {
        "engine_config": _ensure_file(ENGINE_CONFIG_PATH, defaults["engine_config"]),
        "text_bridge": _ensure_file(TEXT_BRIDGE_PATH, defaults["text_bridge"]),
        "embedding_profiles": _ensure_file(EMBEDDING_PROFILES_PATH, defaults["embedding_profiles"]),
        "reranker_profiles": _ensure_file(RERANKER_PROFILES_PATH, defaults["reranker_profiles"]),
        "vector_store": _ensure_file(VECTOR_STORE_PATH, defaults["vector_store"]),
        "retrieval_defaults": _ensure_file(RETRIEVAL_DEFAULTS_PATH, defaults["retrieval_defaults"]),
        "indexing_state": _ensure_file(INDEXING_STATE_PATH, defaults["indexing_state"]),
        "runtime_defaults": _ensure_file(RUNTIME_DEFAULTS_PATH, defaults["runtime_defaults"]),
        "health_snapshot": _ensure_file(HEALTH_SNAPSHOT_PATH, {**defaults["health_snapshot"], "checked_at": _now()}),
    }
    return files


def admin_engine_state_payload() -> dict[str, Any]:
    files = ensure_admin_engine_foundation()
    health = dict(files["health_snapshot"])
    health.update({
        "checked_at": _now(),
        "text_bridge_ready": files["text_bridge"].get("status") == "ready",
        "embeddings_ready": bool(files["embedding_profiles"].get("active_profile_id") or files["embedding_profiles"].get("active_model_path") or files["embedding_profiles"].get("active_model_name")),
        "reranker_ready": bool(files["reranker_profiles"].get("active_profile_id") or files["reranker_profiles"].get("active_model_path") or files["reranker_profiles"].get("active_model_name")) or files["reranker_profiles"].get("active_provider_id") == "none",
        "vector_store_ready": bool(files["vector_store"].get("root") or files["vector_store"].get("persist_path")),
        "indexing_ready": files["indexing_state"].get("status") in {"ready", "foundation"},
    })
    _write_json(HEALTH_SNAPSHOT_PATH, health)
    files["health_snapshot"] = health
    paths = {
        "data_root": str(ENGINE_DATA_DIR.relative_to(ROOT_DIR)),
        "vector_store_root": str(VECTOR_STORE_DIR.relative_to(ROOT_DIR)),
        "engine_config": str(ENGINE_CONFIG_PATH.relative_to(ROOT_DIR)),
        "text_bridge": str(TEXT_BRIDGE_PATH.relative_to(ROOT_DIR)),
        "embedding_profiles": str(EMBEDDING_PROFILES_PATH.relative_to(ROOT_DIR)),
        "reranker_profiles": str(RERANKER_PROFILES_PATH.relative_to(ROOT_DIR)),
        "vector_store": str(VECTOR_STORE_PATH.relative_to(ROOT_DIR)),
        "retrieval_defaults": str(RETRIEVAL_DEFAULTS_PATH.relative_to(ROOT_DIR)),
        "indexing_state": str(INDEXING_STATE_PATH.relative_to(ROOT_DIR)),
        "runtime_defaults": str(RUNTIME_DEFAULTS_PATH.relative_to(ROOT_DIR)),
        "health_snapshot": str(HEALTH_SNAPSHOT_PATH.relative_to(ROOT_DIR)),
    }
    readiness = {
        "ready": True,
        "text_bridge_ready": health["text_bridge_ready"],
        "embeddings_ready": health["embeddings_ready"],
        "reranker_ready": health["reranker_ready"],
        "vector_store_ready": health["vector_store_ready"],
        "indexing_ready": health["indexing_ready"],
        "retrieval_defaults_ready": files["retrieval_defaults"].get("status") in {"foundation", "ready"},
        "runtime_defaults_ready": files["runtime_defaults"].get("status") in {"foundation", "ready"},
    }
    try:
        from neo_app.admin.index_jobs import index_job_queue_state_payload
        index_jobs = index_job_queue_state_payload()
    except Exception:
        index_jobs = {"status": "unavailable", "jobs": [], "summary": {}}

    return {
        "schema_id": ENGINE_SCHEMA_ID,
        "version": ENGINE_VERSION,
        "label": "Memory Engine",
        "surface_id": "admin",
        "tab_id": "memory_engine",
        "status": "ready",
        "owner": "admin",
        "visible_name": "Memory Engine",
        "paths": paths,
        "readiness": readiness,
        "engine_config": files["engine_config"],
        "text_bridge": files["text_bridge"],
        "embedding_profiles": files["embedding_profiles"],
        "reranker_profiles": files["reranker_profiles"],
        "vector_store": files["vector_store"],
        "retrieval_defaults": files["retrieval_defaults"],
        "indexing_state": files["indexing_state"],
        "index_jobs": index_jobs,
        "runtime_defaults": files["runtime_defaults"],
        "health_snapshot": health,
        "active_features": [
            "external sentence-transformers embedding execution when installed/configured",
            "OpenAI-compatible embedding endpoint execution when configured",
            "external cross-encoder reranker execution when installed/configured",
            "OpenAI-compatible reranker endpoint execution when configured",
            "SQLite vector index writes",
            "safe local hash embedding fallback",
            "safe lexical reranker fallback",
            "Chroma/vector collection export/import",
        ],
        "deferred_features": [
            "GPU/model health benchmark panel",
        ],
        "memory_engine_policy": "Embedding, reranker, vector store, retrieval, and indexing settings are Admin-owned and consumed by Assistant, Roleplay, and surfaces through bridge/client APIs.",
    }


def update_runtime_defaults(payload: dict[str, Any] | None) -> dict[str, Any]:
    files = ensure_admin_engine_foundation()
    current = dict(files["runtime_defaults"])
    incoming = payload or {}
    for key in ("max_tokens", "temperature", "top_p", "top_k", "memory_window", "continuity_mode"):
        if key in incoming:
            current[key] = incoming[key]
    current["status"] = "foundation"
    current["updated_at"] = _now()
    _write_json(RUNTIME_DEFAULTS_PATH, current)
    return admin_engine_state_payload()


def update_retrieval_defaults(payload: dict[str, Any] | None) -> dict[str, Any]:
    files = ensure_admin_engine_foundation()
    current = dict(files["retrieval_defaults"])
    incoming = payload or {}
    for key in ("query_top_k", "rerank_top_n", "max_context_chars", "namespaces", "scoring_policy", "filters"):
        if key in incoming:
            current[key] = incoming[key]
    current["status"] = "foundation"
    current["updated_at"] = _now()
    _write_json(RETRIEVAL_DEFAULTS_PATH, current)
    return admin_engine_state_payload()


def update_model_paths(payload: dict[str, Any] | None) -> dict[str, Any]:
    files = ensure_admin_engine_foundation()
    incoming = payload or {}

    embeddings = dict(files["embedding_profiles"])
    for key in ("active_profile_id", "active_provider_id", "active_model_path", "active_model_name", "active_base_url", "active_api_key_env", "request_timeout", "default_dimension"):
        if f"embedding_{key}" in incoming:
            embeddings[key] = incoming[f"embedding_{key}"]
        elif key in incoming.get("embeddings", {}):
            embeddings[key] = incoming["embeddings"][key]
    embeddings["status"] = "ready" if (embeddings.get("active_model_path") or embeddings.get("active_model_name") or embeddings.get("active_profile_id") or embeddings.get("active_base_url") or embeddings.get("active_provider_id") == "local_hash_embeddings") else "placeholder"
    embeddings["updated_at"] = _now()
    _write_json(EMBEDDING_PROFILES_PATH, embeddings)

    reranker = dict(files["reranker_profiles"])
    for key in ("active_profile_id", "active_provider_id", "active_model_path", "active_model_name", "active_base_url", "active_api_key_env", "request_timeout", "default_top_n"):
        if f"reranker_{key}" in incoming:
            reranker[key] = incoming[f"reranker_{key}"]
        elif key in incoming.get("reranker", {}):
            reranker[key] = incoming["reranker"][key]
    reranker_ref = reranker.get("active_model_path") or reranker.get("active_model_name") or reranker.get("active_profile_id") or ""
    normalized_provider, provider_note = _normalize_reranker_provider(reranker.get("active_provider_id"), reranker_ref)
    reranker["active_provider_id"] = normalized_provider
    reranker["supported_provider_ids"] = _merge_unique_list(reranker.get("supported_provider_ids"), ["qwen3_reranker", "local_reranker", "cross_encoder", "bge_reranker", "openai_compatible_reranker", "lexical_overlap", "none"])
    if provider_note:
        reranker["last_provider_persistence_note"] = provider_note
    else:
        reranker.pop("last_provider_persistence_note", None)
    reranker["status"] = "disabled" if reranker.get("active_provider_id") == "none" else ("ready" if (reranker.get("active_model_path") or reranker.get("active_model_name") or reranker.get("active_profile_id") or reranker.get("active_base_url") or reranker.get("active_provider_id") == "lexical_overlap") else "placeholder")
    reranker["updated_at"] = _now()
    _write_json(RERANKER_PROFILES_PATH, reranker)

    vector = dict(files["vector_store"])
    for key in ("active_store", "root", "persist_path", "collection_prefix", "write_enabled"):
        if f"vector_{key}" in incoming:
            vector[key] = incoming[f"vector_{key}"]
        elif key in incoming.get("vector_store", {}):
            vector[key] = incoming["vector_store"][key]
    if isinstance(vector.get("write_enabled"), str):
        vector["write_enabled"] = vector["write_enabled"].lower() in {"1", "true", "yes", "on"}
    vector["status"] = "foundation"
    vector["updated_at"] = _now()
    _write_json(VECTOR_STORE_PATH, vector)
    return admin_engine_state_payload()
