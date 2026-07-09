from __future__ import annotations

from importlib.util import find_spec


def optional_status() -> dict:
    chroma_ok = find_spec("chromadb") is not None
    sentence_ok = find_spec("sentence_transformers") is not None
    transformers_ok = find_spec("transformers") is not None
    notes: list[str] = []
    if not chroma_ok:
        notes.append("chromadb is not installed; semantic vector storage is disabled.")
    if not sentence_ok:
        notes.append("sentence-transformers is not installed; local embeddings are disabled.")
    if not transformers_ok:
        notes.append("transformers is not installed; transformer pipelines are disabled.")
    return {
        "sqlite": "available",
        "chroma": "available" if chroma_ok else "missing_optional_dependency",
        "sentence_transformers": "available" if sentence_ok else "missing_optional_dependency",
        "transformers": "available" if transformers_ok else "missing_optional_dependency",
        "semantic_search_enabled": bool(chroma_ok and sentence_ok),
        "notes": notes,
    }
