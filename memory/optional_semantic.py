from __future__ import annotations

from importlib.util import find_spec


def optional_status() -> dict:
    chroma_ok = find_spec("chromadb") is not None
    sentence_ok = find_spec("sentence_transformers") is not None
    transformers_ok = find_spec("transformers") is not None
    notes: list[str] = []
    if not chroma_ok:
        notes.append("chromadb is not installed; semantic vector storage is unavailable.")
    if not sentence_ok:
        notes.append("sentence-transformers is not installed; local embedding/reranker execution is unavailable.")
    if not transformers_ok:
        notes.append("transformers is not installed; transformer model execution is unavailable.")

    semantic_state: dict = {}
    try:
        from neo_app.admin.semantic_engine import semantic_engine_state_payload
        semantic_state = semantic_engine_state_payload()
    except Exception as exc:
        notes.append(f"Semantic readiness could not be resolved: {str(exc)[:300]}")

    qualified = bool(semantic_state.get("semantic_search_enabled")) and semantic_state.get("status") in {"ready", "degraded"}
    return {
        "sqlite": "available",
        "chroma": "available" if chroma_ok else "missing_optional_dependency",
        "sentence_transformers": "available" if sentence_ok else "missing_optional_dependency",
        "transformers": "available" if transformers_ok else "missing_optional_dependency",
        "semantic_runtime_dependencies_available": bool(chroma_ok and sentence_ok),
        "semantic_search_enabled": qualified,
        "semantic_readiness": semantic_state.get("status") or "unknown",
        "embedding_profile_fingerprint": (semantic_state.get("embedding") or {}).get("profile_fingerprint") or "",
        "notes": notes,
    }
