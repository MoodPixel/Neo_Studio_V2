from __future__ import annotations

import hashlib
import json
import os
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

SEMANTIC_PROFILE_SCHEMA_ID = "neo.admin.semantic_profile.v1"
SEMANTIC_PROFILE_VERSION = "1.0.0-aci5"


def _stable_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _package_version(name: str) -> str:
    try:
        return importlib_metadata.version(name)
    except Exception:
        return "unknown"


def _normalize_pathish(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if os.path.isabs(text):
        try:
            return os.path.normcase(os.path.normpath(str(Path(text))))
        except Exception:
            return text.replace("\\", "/")
    return text.replace("\\", "/")


def _embedding_model_ref(config: dict[str, Any]) -> str:
    return str(
        config.get("active_model_path")
        or config.get("active_model_name")
        or config.get("active_profile_id")
        or ""
    ).strip()


def _reranker_model_ref(config: dict[str, Any]) -> str:
    return str(
        config.get("active_model_path")
        or config.get("active_model_name")
        or config.get("active_profile_id")
        or ""
    ).strip()


def embedding_runtime_name(provider: str) -> str:
    value = str(provider or "").strip()
    if value in {"sentence_transformers", "local_embedding_model"}:
        return "sentence_transformers"
    if value == "openai_compatible_embeddings":
        return "openai_compatible_embeddings"
    if value == "local_hash_embeddings":
        return "deterministic_hash_nonsemantic"
    return value or "unknown"


def embedding_runtime_version(provider: str) -> str:
    runtime = embedding_runtime_name(provider)
    if runtime == "sentence_transformers":
        return _package_version("sentence-transformers")
    if runtime == "openai_compatible_embeddings":
        return "remote-api"
    if runtime == "deterministic_hash_nonsemantic":
        return "builtin"
    return "unknown"


def reranker_runtime_name(provider: str) -> str:
    value = str(provider or "").strip()
    if value in {"cross_encoder", "bge_reranker", "local_reranker"}:
        return "sentence_transformers.CrossEncoder"
    if value == "qwen3_reranker":
        return "transformers.AutoModelForCausalLM"
    if value == "openai_compatible_reranker":
        return "openai_compatible_reranker"
    if value == "lexical_overlap":
        return "lexical_overlap_nonsemantic"
    if value == "none":
        return "disabled"
    return value or "unknown"


def reranker_runtime_version(provider: str) -> str:
    runtime = reranker_runtime_name(provider)
    if runtime == "sentence_transformers.CrossEncoder":
        return _package_version("sentence-transformers")
    if runtime == "transformers.AutoModelForCausalLM":
        return _package_version("transformers")
    if runtime == "openai_compatible_reranker":
        return "remote-api"
    if runtime in {"lexical_overlap_nonsemantic", "disabled"}:
        return "builtin"
    return "unknown"


def embedding_config_descriptor(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(config or {})
    provider = str(cfg.get("active_provider_id") or "sentence_transformers").strip() or "sentence_transformers"
    return {
        "schema_id": SEMANTIC_PROFILE_SCHEMA_ID,
        "version": SEMANTIC_PROFILE_VERSION,
        "runtime": embedding_runtime_name(provider),
        "runtime_version": embedding_runtime_version(provider),
        "provider": provider,
        "profile_id": str(cfg.get("active_profile_id") or "").strip(),
        "model_ref": _normalize_pathish(_embedding_model_ref(cfg)),
        "model_path": _normalize_pathish(cfg.get("active_model_path")),
        "model_name": str(cfg.get("active_model_name") or "").strip(),
        "model_revision": str(cfg.get("active_model_revision") or "").strip(),
        "base_url": str(cfg.get("active_base_url") or cfg.get("base_url") or "").strip().rstrip("/"),
        "requested_dimension": int(cfg.get("default_dimension") or 0) or None,
        "pooling": str(cfg.get("pooling") or "model_defined"),
        "normalization": "l2" if bool(cfg.get("normalize_embeddings", True)) else "model_output",
        "query_prompt_name": str(cfg.get("query_prompt_name") or "").strip(),
        "document_prompt_name": str(cfg.get("document_prompt_name") or "").strip(),
        "query_instruction": str(cfg.get("query_instruction") or "").strip(),
        "document_instruction": str(cfg.get("document_instruction") or "").strip(),
        "trust_remote_code": bool(cfg.get("trust_remote_code", True)),
    }


def reranker_config_descriptor(config: dict[str, Any] | None) -> dict[str, Any]:
    cfg = dict(config or {})
    provider = str(cfg.get("active_provider_id") or "none").strip() or "none"
    return {
        "schema_id": SEMANTIC_PROFILE_SCHEMA_ID,
        "version": SEMANTIC_PROFILE_VERSION,
        "runtime": reranker_runtime_name(provider),
        "runtime_version": reranker_runtime_version(provider),
        "provider": provider,
        "profile_id": str(cfg.get("active_profile_id") or "").strip(),
        "model_ref": _normalize_pathish(_reranker_model_ref(cfg)),
        "model_path": _normalize_pathish(cfg.get("active_model_path")),
        "model_name": str(cfg.get("active_model_name") or "").strip(),
        "model_revision": str(cfg.get("active_model_revision") or "").strip(),
        "base_url": str(cfg.get("active_base_url") or cfg.get("base_url") or "").strip().rstrip("/"),
        "max_length": int(cfg.get("max_length") or 2048),
        "trust_remote_code": bool(cfg.get("trust_remote_code", True)),
    }


def semantic_config_descriptor(engine_state: dict[str, Any] | None) -> dict[str, Any]:
    state = dict(engine_state or {})
    return {
        "schema_id": "neo.admin.semantic_config_fingerprint.v1",
        "embedding": embedding_config_descriptor(state.get("embedding_profiles") if isinstance(state.get("embedding_profiles"), dict) else {}),
        "reranker": reranker_config_descriptor(state.get("reranker_profiles") if isinstance(state.get("reranker_profiles"), dict) else {}),
    }


def semantic_config_fingerprint(engine_state: dict[str, Any] | None) -> str:
    return _sha256_text(_stable_json(semantic_config_descriptor(engine_state)))


def embedding_space_descriptor(
    config: dict[str, Any] | None,
    *,
    detected_dimension: int,
    model_id: str = "",
) -> dict[str, Any]:
    base = embedding_config_descriptor(config)
    base.update({
        "schema_id": "neo.admin.embedding_space.v1",
        "detected_dimension": int(detected_dimension or 0),
        "resolved_model_id": _normalize_pathish(model_id or base.get("model_ref") or ""),
    })
    return base


def embedding_profile_fingerprint(
    config: dict[str, Any] | None,
    *,
    detected_dimension: int,
    model_id: str = "",
) -> str:
    return _sha256_text(_stable_json(embedding_space_descriptor(config, detected_dimension=detected_dimension, model_id=model_id)))


def fingerprint_short(value: str, length: int = 12) -> str:
    return str(value or "")[: max(6, int(length or 12))]
