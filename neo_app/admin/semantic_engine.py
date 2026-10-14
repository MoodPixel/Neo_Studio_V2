from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from functools import lru_cache
from typing import Any, Callable

from neo_app.admin.engine import SEMANTIC_QUALIFICATION_PATH, admin_engine_state_payload
from neo_app.admin.semantic_profile import (
    embedding_config_descriptor,
    embedding_profile_fingerprint,
    embedding_space_descriptor,
    fingerprint_short,
    reranker_config_descriptor,
    semantic_config_fingerprint,
)

SEMANTIC_SCHEMA_ID = "neo.admin.semantic_engine.v1"
SEMANTIC_VERSION = "0.5.0-aci5-qualified-readiness"


def _tokenize(text: str) -> list[str]:
    import re
    return re.findall(r"[a-zA-Z0-9_'-]+", (text or "").lower())


def deterministic_text_embedding(text: str, *, dimension: int = 96) -> list[float]:
    dim = max(16, min(int(dimension or 96), 4096))
    vec = [0.0] * dim
    for token in _tokenize(text):
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vec[bucket] += sign * (1.0 + min(len(token), 20) / 20.0)
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 8) for v in vec]


def _normalize_vector(vec: Any, *, normalize: bool = True) -> list[float]:
    """Coerce model output into a finite float vector and optionally L2-normalize it."""
    if vec is None:
        return []
    try:
        if hasattr(vec, "detach"):
            vec = vec.detach().cpu().tolist()
        elif hasattr(vec, "tolist"):
            vec = vec.tolist()
    except Exception:
        pass
    if isinstance(vec, (int, float)):
        vec = [vec]
    if not isinstance(vec, (list, tuple)):
        try:
            vec = list(vec)
        except Exception:
            return []
    clean: list[float] = []
    for item in vec:
        try:
            value = float(item)
        except Exception:
            return []
        if not math.isfinite(value):
            return []
        clean.append(value)
    if not clean:
        return []
    if not normalize:
        return [round(v, 8) for v in clean]
    norm = math.sqrt(sum(v * v for v in clean))
    if not math.isfinite(norm) or norm <= 0:
        return []
    return [round(v / norm, 8) for v in clean]

def _api_key_from_config(config: dict[str, Any]) -> str:
    env_name = str(config.get("active_api_key_env") or config.get("api_key_env") or "").strip()
    if env_name:
        return os.environ.get(env_name, "")
    # Supported for local-only/private installs, but UI defaults to API-key env instead.
    return str(config.get("active_api_key") or config.get("api_key") or "").strip()


def _endpoint(base: str, suffix: str) -> str:
    raw = str(base or "").strip().rstrip("/")
    if not raw:
        return ""
    if raw.endswith(suffix):
        return raw
    if raw.endswith("/v1") and suffix.startswith("/"):
        return f"{raw}{suffix}"
    if suffix.startswith("/v1/") and raw.endswith("/v1"):
        return f"{raw}{suffix[3:]}"
    return f"{raw}{suffix}"


def _post_json(url: str, payload: dict[str, Any], *, api_key: str = "", timeout: float = 60.0) -> dict[str, Any]:
    if not url:
        raise RuntimeError("No endpoint URL configured")
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc


def _model_path_hint(model_ref: str) -> dict[str, Any]:
    ref = str(model_ref or "").strip()
    exists = bool(ref and os.path.exists(ref))
    files: list[str] = []
    if exists and os.path.isdir(ref):
        try:
            files = sorted(os.listdir(ref))[:24]
        except Exception:
            files = []
    return {"model_ref": ref, "exists": exists, "sample_files": files}


def _looks_like_qwen3_reranker(model_ref: str) -> bool:
    value = str(model_ref or "").replace("\\", "/").lower()
    return "qwen3" in value and "reranker" in value


def _canonical_reranker_provider(provider: str, model_ref: str) -> tuple[str, str]:
    raw = str(provider or "none").strip() or "none"
    if raw in {"cross_encoder", "bge_reranker", "local_reranker"} and _looks_like_qwen3_reranker(model_ref):
        return "qwen3_reranker", f"Auto-routed {raw} to qwen3_reranker because the model path/name looks like Qwen3-Reranker."
    return raw, ""


@lru_cache(maxsize=8)
def _load_sentence_transformer(model_ref: str, revision: str = "", trust_remote_code: bool = True):
    from sentence_transformers import SentenceTransformer  # type: ignore
    kwargs: dict[str, Any] = {"trust_remote_code": bool(trust_remote_code)}
    if revision:
        kwargs["revision"] = revision
    try:
        return SentenceTransformer(model_ref, **kwargs)
    except TypeError:
        kwargs.pop("trust_remote_code", None)
        return SentenceTransformer(model_ref, **kwargs)

@lru_cache(maxsize=8)
def _load_cross_encoder(model_ref: str, revision: str = "", trust_remote_code: bool = True):
    from sentence_transformers import CrossEncoder  # type: ignore
    kwargs: dict[str, Any] = {"trust_remote_code": bool(trust_remote_code)}
    if revision:
        kwargs["revision"] = revision
    try:
        return CrossEncoder(model_ref, **kwargs)
    except TypeError:
        kwargs.pop("trust_remote_code", None)
        return CrossEncoder(model_ref, **kwargs)

@lru_cache(maxsize=4)
def _load_qwen3_reranker(model_ref: str, revision: str = "", trust_remote_code: bool = True):
    import torch  # type: ignore
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

    common: dict[str, Any] = {"trust_remote_code": bool(trust_remote_code)}
    if revision:
        common["revision"] = revision
    tokenizer = AutoTokenizer.from_pretrained(model_ref, **common)
    if getattr(tokenizer, "pad_token", None) is None:
        tokenizer.pad_token = getattr(tokenizer, "eos_token", None) or tokenizer.unk_token
    kwargs: dict[str, Any] = dict(common)
    if hasattr(torch, "float16"):
        kwargs["torch_dtype"] = getattr(torch, "float16")
    try:
        kwargs["device_map"] = "auto"
        model = AutoModelForCausalLM.from_pretrained(model_ref, **kwargs)
    except Exception:
        kwargs.pop("device_map", None)
        model = AutoModelForCausalLM.from_pretrained(model_ref, **kwargs)
    if getattr(model.config, "pad_token_id", None) is None and getattr(tokenizer, "pad_token_id", None) is not None:
        model.config.pad_token_id = tokenizer.pad_token_id
    model.eval()
    return tokenizer, model

def _qwen3_prompt(query: str, document: str) -> str:
    return (
        "<|im_start|>system\n"
        "Judge whether the Document meets the requirements based on the Query. "
        "The answer can only be yes or no.<|im_end|>\n"
        "<|im_start|>user\n"
        f"Query: {query}\nDocument: {document}<|im_end|>\n"
        "<|im_start|>assistant\n<think>\n\n</think>\n\n"
    )


def _qwen3_rerank_scores(
    model_ref: str,
    query: str,
    documents: list[str],
    *,
    revision: str = "",
    trust_remote_code: bool = True,
    max_length: int = 2048,
    batch_size: int = 4,
) -> list[float]:
    """Score Qwen3 reranker candidates without materializing giant batch logits.

    The previous foreground path sent every candidate through the causal-LM head
    in one batch. With a large vocabulary this can allocate enormous
    ``batch × sequence × vocab`` logits and thrash a 12GB GPU shared with the text
    backend. ACI-7.1 scores small batches and, for Qwen-style causal models, applies
    the LM head only to each sequence's final non-padding hidden state.
    """
    import torch  # type: ignore

    tokenizer, model = _load_qwen3_reranker(model_ref, revision, trust_remote_code)
    yes_ids = tokenizer.encode("yes", add_special_tokens=False) or tokenizer.encode(" Yes", add_special_tokens=False)
    no_ids = tokenizer.encode("no", add_special_tokens=False) or tokenizer.encode(" No", add_special_tokens=False)
    if not yes_ids or not no_ids:
        raise RuntimeError("Could not resolve Qwen reranker yes/no token ids")
    yes_id = int(yes_ids[-1])
    no_id = int(no_ids[-1])
    device = next(model.parameters()).device
    effective_batch = max(1, min(int(batch_size or 4), 8))
    effective_length = max(128, min(int(max_length or 2048), 32768))
    scores: list[float] = []

    for offset in range(0, len(documents), effective_batch):
        batch_docs = documents[offset:offset + effective_batch]
        prompts = [_qwen3_prompt(query, doc) for doc in batch_docs]
        inputs = tokenizer(
            prompts,
            padding=True,
            truncation=True,
            max_length=effective_length,
            return_tensors="pt",
        ).to(device)
        attention_mask = inputs.get("attention_mask")
        with torch.inference_mode():
            base_model = getattr(model, "model", None)
            lm_head = getattr(model, "lm_head", None)
            if base_model is not None and callable(lm_head):
                base_outputs = base_model(**inputs, return_dict=True)
                hidden = base_outputs.last_hidden_state
                if attention_mask is not None:
                    positions = torch.arange(hidden.shape[1], device=hidden.device).unsqueeze(0)
                    last_indices = (attention_mask.long() * positions).max(dim=1).values.clamp_min(0)
                    batch_indices = torch.arange(hidden.shape[0], device=hidden.device)
                    final_hidden = hidden[batch_indices, last_indices, :]
                else:
                    final_hidden = hidden[:, -1, :]
                logits = lm_head(final_hidden)
            else:
                outputs = model(**inputs, return_dict=True)
                full_logits = outputs.logits
                if attention_mask is not None:
                    positions = torch.arange(full_logits.shape[1], device=full_logits.device).unsqueeze(0)
                    last_indices = (attention_mask.long() * positions).max(dim=1).values.clamp_min(0)
                    batch_indices = torch.arange(full_logits.shape[0], device=full_logits.device)
                    logits = full_logits[batch_indices, last_indices, :]
                else:
                    logits = full_logits[:, -1, :]
            yes_no = logits[:, [yes_id, no_id]]
            probs = torch.softmax(yes_no, dim=-1)[:, 0]
        scores.extend(float(v) for v in probs.detach().cpu().tolist())
        del inputs
        if device.type == "cuda":
            # Let the allocator reuse intermediate batch memory without forcing a
            # global cache purge that would slow every request.
            try:
                torch.cuda.synchronize(device)
            except Exception:
                pass

    if not all(math.isfinite(v) for v in scores):
        raise RuntimeError("Qwen reranker returned a non-finite score")
    return scores


def _embedding_config(engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = engine_state or admin_engine_state_payload()
    return dict(state.get("embedding_profiles") or {})


def _reranker_config(engine_state: dict[str, Any] | None = None) -> dict[str, Any]:
    state = engine_state or admin_engine_state_payload()
    return dict(state.get("reranker_profiles") or {})


def semantic_engine_state_payload() -> dict[str, Any]:
    engine = admin_engine_state_payload()
    embeddings = _embedding_config(engine)
    reranker = _reranker_config(engine)
    vector = dict(engine.get("vector_store") or {})
    qualification = dict(engine.get("semantic_qualification") or {})
    config_fingerprint = semantic_config_fingerprint(engine)
    qualification_current = bool(qualification.get("config_fingerprint")) and qualification.get("config_fingerprint") == config_fingerprint

    embedding_provider = str(embeddings.get("active_provider_id") or "sentence_transformers")
    reranker_provider = str(reranker.get("active_provider_id") or "none")
    embedding_ref = str(embeddings.get("active_model_path") or embeddings.get("active_model_name") or embeddings.get("active_profile_id") or "").strip()
    reranker_ref = str(reranker.get("active_model_path") or reranker.get("active_model_name") or reranker.get("active_profile_id") or "").strip()
    embedding_qualification = dict(qualification.get("embedding") or {})
    reranker_qualification = dict(qualification.get("reranker") or {})
    embedding_qualified = qualification_current and embedding_qualification.get("status") == "passed"
    reranker_qualified = qualification_current and reranker_qualification.get("status") in {"passed", "disabled", "degraded"}
    profile_fingerprint = str(embedding_qualification.get("profile_fingerprint") or "") if embedding_qualified else ""
    detected_dimension = int(embedding_qualification.get("dimension") or 0) if embedding_qualified else 0

    vector_compatibility: dict[str, Any] = {"status": "not_checked", "reason": "embedding_not_qualified"}
    if embedding_qualified:
        try:
            from neo_app.memory.vector_store import chroma_semantic_compatibility
            vector_compatibility = chroma_semantic_compatibility(
                engine_state=engine,
                profile_fingerprint=profile_fingerprint,
                dimension=detected_dimension,
                model_id=str(embedding_qualification.get("model_id") or embedding_ref),
            )
        except Exception as exc:
            vector_compatibility = {"status": "degraded", "reason": "compatibility_probe_failed", "error": str(exc)[:700]}

    blockers: list[str] = []
    warnings: list[str] = []
    if embedding_provider == "local_hash_embeddings":
        blockers.append("local_hash_embeddings_is_not_a_qualified_semantic_model")
    elif not embedding_ref and embedding_provider != "openai_compatible_embeddings":
        blockers.append("embedding_model_not_configured")
    if not qualification_current:
        blockers.append("semantic_qualification_required")
    elif embedding_qualification.get("status") != "passed":
        blockers.extend(str(item) for item in qualification.get("blockers") or ["embedding_qualification_failed"])
    if reranker_qualification.get("status") == "degraded":
        warnings.append("Reranking is using a non-semantic fallback; embedding search can still operate.")
    if vector_compatibility.get("status") == "reindex_required":
        warnings.append("One or more vector collections were created with a different embedding profile and require reindexing.")

    if blockers:
        status = "blocked" if qualification_current and qualification.get("status") == "failed" else "qualification_required"
    elif vector_compatibility.get("status") == "reindex_required":
        status = "reindex_required"
    elif not reranker_qualified or reranker_qualification.get("status") == "degraded" or vector_compatibility.get("status") in {"degraded", "unavailable"}:
        status = "degraded"
    else:
        status = "ready"

    return {
        "schema_id": SEMANTIC_SCHEMA_ID,
        "version": SEMANTIC_VERSION,
        "status": status,
        "ready": status == "ready",
        "semantic_search_enabled": bool(embedding_qualified and vector_compatibility.get("status") != "reindex_required"),
        "owner": "admin",
        "config_fingerprint": config_fingerprint,
        "qualification_current": qualification_current,
        "qualification": qualification,
        "embedding": {
            "provider": embedding_provider,
            "model_ref": embedding_ref,
            "base_url": embeddings.get("active_base_url") or embeddings.get("base_url") or os.environ.get("NEO_EMBEDDING_BASE_URL", ""),
            "api_key_env": embeddings.get("active_api_key_env") or embeddings.get("api_key_env") or "",
            "configured_dimension": embeddings.get("default_dimension") or None,
            "dimension": detected_dimension or None,
            "qualified": embedding_qualified,
            "profile_fingerprint": profile_fingerprint,
            "profile_fingerprint_short": fingerprint_short(profile_fingerprint) if profile_fingerprint else "",
            "descriptor": embedding_config_descriptor(embeddings),
            "fallback": "lexical/FTS only; hash vectors are non-semantic and never qualify the engine",
        },
        "reranker": {
            "provider": reranker_provider,
            "model_ref": reranker_ref,
            "base_url": reranker.get("active_base_url") or reranker.get("base_url") or os.environ.get("NEO_RERANKER_BASE_URL", ""),
            "api_key_env": reranker.get("active_api_key_env") or reranker.get("api_key_env") or "",
            "top_n": reranker.get("default_top_n") or 8,
            "qualified": reranker_qualified,
            "qualification_status": reranker_qualification.get("status") or "not_qualified",
            "descriptor": reranker_config_descriptor(reranker),
            "fallback": "lexical_overlap is reported as degraded, not as a qualified reranker",
        },
        "vector_store": vector,
        "vector_compatibility": vector_compatibility,
        "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings + [str(item) for item in qualification.get("warnings") or []])),
        "supported_embedding_providers": ["sentence_transformers", "local_embedding_model", "openai_compatible_embeddings", "local_hash_embeddings"],
        "supported_reranker_providers": ["cross_encoder", "bge_reranker", "qwen3_reranker", "local_reranker", "openai_compatible_reranker", "lexical_overlap", "none"],
    }

def embed_texts(
    texts: list[str],
    *,
    engine_state: dict[str, Any] | None = None,
    dimension: int | None = None,
    allow_fallback: bool = True,
    purpose: str = "document",
) -> dict[str, Any]:
    config = _embedding_config(engine_state)
    provider = str(config.get("active_provider_id") or "sentence_transformers").strip() or "sentence_transformers"
    model_ref = str(config.get("active_model_path") or config.get("active_model_name") or config.get("active_profile_id") or "").strip()
    dim = int(dimension or config.get("default_dimension") or 96)
    started = time.time()
    clean_texts = [str(text or "") for text in texts]
    normalize = bool(config.get("normalize_embeddings", True))
    purpose_key = "query" if str(purpose or "document").lower() == "query" else "document"
    try:
        if provider in {"sentence_transformers", "local_embedding_model"} and model_ref:
            hint = _model_path_hint(model_ref)
            if os.path.isabs(model_ref) and not hint["exists"]:
                raise RuntimeError(f"Embedding model path does not exist: {model_ref}")
            revision = str(config.get("active_model_revision") or "").strip()
            trust_remote_code = bool(config.get("trust_remote_code", True))
            model = _load_sentence_transformer(model_ref, revision, trust_remote_code)
            encode_kwargs: dict[str, Any] = {"normalize_embeddings": normalize, "convert_to_numpy": False}
            instruction = str(config.get(f"{purpose_key}_instruction") or "").strip()
            prompt_name = str(config.get(f"{purpose_key}_prompt_name") or "").strip()
            if instruction:
                encode_kwargs["prompt"] = instruction
            elif prompt_name:
                encode_kwargs["prompt_name"] = prompt_name
            vectors = model.encode(clean_texts, **encode_kwargs)
            out = [_normalize_vector(vec, normalize=normalize) for vec in vectors]
            actual_dim = len(out[0]) if out else 0
            if actual_dim <= 0 or any(len(vec) != actual_dim for vec in out):
                raise RuntimeError(f"Embedding model returned invalid/inconsistent vectors. Path hint: {hint}")
            profile_fp = embedding_profile_fingerprint(config, detected_dimension=actual_dim, model_id=model_ref)
            return {"status": "embedded", "mode": "external_sentence_transformers", "provider": provider, "model_id": model_ref, "dimension": actual_dim, "vectors": out, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "semantic": True, "purpose": purpose_key, "profile_fingerprint": profile_fp, "profile_descriptor": embedding_space_descriptor(config, detected_dimension=actual_dim, model_id=model_ref), "model_hint": hint}
        if provider == "openai_compatible_embeddings":
            base = str(config.get("active_base_url") or config.get("base_url") or os.environ.get("NEO_EMBEDDING_BASE_URL") or "").strip()
            model_name = str(config.get("active_model_name") or model_ref or os.environ.get("NEO_EMBEDDING_MODEL") or "").strip()
            if not base or not model_name:
                raise RuntimeError("OpenAI-compatible embedding base URL and model name are required")
            api_texts = clean_texts
            instruction = str(config.get(f"{purpose_key}_instruction") or "").strip()
            if instruction:
                api_texts = [f"{instruction}{text}" for text in clean_texts]
            payload = {"model": model_name, "input": api_texts}
            url = _endpoint(base, "/v1/embeddings")
            response = _post_json(url, payload, api_key=_api_key_from_config(config), timeout=float(config.get("request_timeout") or 60))
            data = response.get("data") or []
            ordered = sorted(data, key=lambda item: int(item.get("index", 0))) if isinstance(data, list) else []
            out = [_normalize_vector(item.get("embedding"), normalize=normalize) for item in ordered]
            if len(out) != len(clean_texts):
                raise RuntimeError(f"Embedding response returned {len(out)} vectors for {len(clean_texts)} inputs")
            actual_dim = len(out[0]) if out else 0
            if actual_dim <= 0 or any(len(vec) != actual_dim for vec in out):
                raise RuntimeError("Embedding API returned invalid/inconsistent vectors")
            model_id = model_name or model_ref or "openai_compatible_embeddings"
            profile_fp = embedding_profile_fingerprint(config, detected_dimension=actual_dim, model_id=model_id)
            return {"status": "embedded", "mode": "external_openai_compatible_embeddings", "provider": provider, "model_id": model_id, "dimension": actual_dim, "vectors": out, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "semantic": True, "purpose": purpose_key, "profile_fingerprint": profile_fp, "profile_descriptor": embedding_space_descriptor(config, detected_dimension=actual_dim, model_id=model_id)}
        if provider == "local_hash_embeddings":
            raise RuntimeError("local_hash_embeddings is a deterministic non-semantic fallback and cannot qualify semantic retrieval")
        raise RuntimeError(f"Embedding provider is not configured for real inference: {provider}")
    except Exception as exc:
        if not allow_fallback:
            raise
        fallback = [deterministic_text_embedding(text, dimension=dim) for text in clean_texts]
        return {"status": "fallback_embedded", "mode": "local_hash_embeddings", "provider": provider, "model_id": "local_hash_embeddings", "dimension": len(fallback[0]) if fallback else dim, "vectors": fallback, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": True, "semantic": False, "purpose": purpose_key, "profile_fingerprint": "", "error": str(exc)[:800]}

def _lexical_score(query: str, document: str) -> float:
    q = set(_tokenize(query))
    d = set(_tokenize(document))
    if not q or not d:
        return 0.0
    return len(q & d) / max(1, len(q))


def _safe_positive_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 256) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _prepare_rerank_items(results: list[dict[str, Any]], *, max_candidates: int, text_limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    head = [dict(item) for item in (results or [])[:max_candidates]]
    tail = [dict(item) for item in (results or [])[max_candidates:]]
    for item in head:
        content = str(item.get("content") or item.get("text") or item.get("summary") or "")
        title = str(item.get("title") or item.get("source_id") or "")
        item["content"] = content[:text_limit]
        if title and not item.get("title"):
            item["title"] = title
    return head, tail


def rerank_results(query: str, results: list[dict[str, Any]], *, engine_state: dict[str, Any] | None = None, top_n: int | None = None, allow_fallback: bool = True) -> dict[str, Any]:
    config = _reranker_config(engine_state)
    provider = str(config.get("active_provider_id") or "none").strip() or "none"
    model_ref = str(config.get("active_model_path") or config.get("active_model_name") or config.get("active_profile_id") or "").strip()
    provider, provider_note = _canonical_reranker_provider(provider, model_ref)
    if provider == "none":
        return {"status": "disabled", "mode": "none", "provider": provider, "results": (results or [])[: int(top_n or len(results or []) or 8)], "fallback_used": False, "semantic": False}
    limit = max(1, min(int(top_n or config.get("default_top_n") or len(results or []) or 8), 100))
    default_candidate_cap = 12 if provider == "qwen3_reranker" else 24
    max_candidates = _safe_positive_int(config.get("max_candidates_per_request"), max(limit, min(len(results or []), default_candidate_cap)), minimum=1, maximum=96)
    text_limit = _safe_positive_int(config.get("candidate_text_limit"), 900 if provider == "qwen3_reranker" else 1200, minimum=128, maximum=12000)
    batch_size = _safe_positive_int(config.get("batch_size"), 4, minimum=1, maximum=8)
    rerank_items, tail_items = _prepare_rerank_items(results or [], max_candidates=max_candidates, text_limit=text_limit)
    started = time.time()
    meta = {
        "input_candidate_count": len(results or []),
        "reranked_candidate_count": len(rerank_items),
        "candidate_text_limit": text_limit,
        "truncated_for_runtime_safety": len(results or []) > len(rerank_items),
        "batch_size": batch_size if provider == "qwen3_reranker" else None,
    }
    try:
        revision = str(config.get("active_model_revision") or "").strip()
        trust_remote_code = bool(config.get("trust_remote_code", True))
        if provider in {"cross_encoder", "bge_reranker", "local_reranker"} and model_ref:
            hint = _model_path_hint(model_ref)
            if os.path.isabs(model_ref) and not hint["exists"]:
                raise RuntimeError(f"Reranker model path does not exist: {model_ref}")
            model = _load_cross_encoder(model_ref, revision, trust_remote_code)
            pairs = [(query, str(item.get("content") or item.get("title") or "")) for item in rerank_items]
            scores = model.predict(pairs)
            scored = []
            for item, score in zip(rerank_items, scores):
                value = float(score)
                if not math.isfinite(value):
                    raise RuntimeError("Reranker returned a non-finite score")
                updated = dict(item)
                updated["external_rerank_score"] = value
                updated["score"] = value
                scored.append(updated)
            scored.sort(key=lambda item: item.get("external_rerank_score", float("-inf")), reverse=True)
            output = (scored + tail_items)[:limit]
            return {"status": "reranked", "mode": "external_cross_encoder", "provider": provider, "model_id": model_ref, "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "semantic": True, "model_hint": hint, "provider_note": provider_note, **meta}
        if provider == "qwen3_reranker" and model_ref:
            hint = _model_path_hint(model_ref)
            if os.path.isabs(model_ref) and not hint["exists"]:
                raise RuntimeError(f"Qwen3 reranker model path does not exist: {model_ref}")
            documents = [str(item.get("content") or item.get("title") or "") for item in rerank_items]
            scores = _qwen3_rerank_scores(
                model_ref, query, documents, revision=revision, trust_remote_code=trust_remote_code,
                max_length=int(config.get("max_length") or 2048), batch_size=batch_size,
            )
            scored = []
            for item, score in zip(rerank_items, scores):
                updated = dict(item)
                updated["external_rerank_score"] = float(score)
                updated["score"] = float(score)
                scored.append(updated)
            scored.sort(key=lambda item: item.get("external_rerank_score", float("-inf")), reverse=True)
            output = (scored + tail_items)[:limit]
            return {"status": "reranked", "mode": "external_qwen3_reranker", "provider": provider, "model_id": model_ref, "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "semantic": True, "model_hint": hint, "provider_note": provider_note, **meta}
        if provider == "openai_compatible_reranker":
            base = str(config.get("active_base_url") or config.get("base_url") or os.environ.get("NEO_RERANKER_BASE_URL") or "").strip()
            model_name = str(config.get("active_model_name") or model_ref or os.environ.get("NEO_RERANKER_MODEL") or "").strip()
            if not base or not model_name:
                raise RuntimeError("OpenAI-compatible reranker base URL and model name are required")
            documents = [str(item.get("content") or item.get("title") or "") for item in rerank_items]
            response = _post_json(_endpoint(base, "/rerank"), {"model": model_name, "query": query, "documents": documents, "top_n": min(limit, len(documents))}, api_key=_api_key_from_config(config), timeout=float(config.get("request_timeout") or 45))
            api_results = response.get("results") or response.get("data") or []
            scored = []
            for entry in api_results if isinstance(api_results, list) else []:
                idx = int(entry.get("index", -1))
                if 0 <= idx < len(rerank_items):
                    score = float(entry.get("relevance_score", entry.get("score", 0.0)))
                    if not math.isfinite(score):
                        raise RuntimeError("Reranker API returned a non-finite score")
                    updated = dict(rerank_items[idx])
                    updated["external_rerank_score"] = score
                    updated["score"] = score
                    scored.append(updated)
            if scored:
                scored.sort(key=lambda item: item.get("external_rerank_score", float("-inf")), reverse=True)
                output = (scored + tail_items)[:limit]
                return {"status": "reranked", "mode": "external_openai_compatible_reranker", "provider": provider, "model_id": model_name or model_ref, "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "semantic": True, **meta}
            raise RuntimeError("Reranker API returned no usable results")
        if provider == "lexical_overlap":
            raise RuntimeError("lexical_overlap is an explicit non-semantic fallback reranker")
        raise RuntimeError(f"Reranker provider is not configured for real inference: {provider}")
    except Exception as exc:
        if not allow_fallback:
            raise
        rescored = []
        for item in rerank_items:
            updated = dict(item)
            lexical = _lexical_score(query, str(item.get("content") or item.get("title") or ""))
            updated["lexical_rerank_score"] = lexical
            updated["score"] = max(float(updated.get("score") or 0.0), lexical)
            rescored.append(updated)
        rescored.sort(key=lambda item: item.get("score", 0), reverse=True)
        output = (rescored + tail_items)[:limit]
        return {"status": "fallback_reranked", "mode": "lexical_overlap", "provider": provider, "model_id": "lexical_overlap", "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": True, "semantic": False, "error": str(exc)[:800], "provider_note": provider_note, **meta}

def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    denom = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if denom <= 0 or not math.isfinite(denom):
        return 0.0
    score = sum(x * y for x, y in zip(a, b)) / denom
    return float(score) if math.isfinite(score) else 0.0


def _write_qualification(payload: dict[str, Any]) -> None:
    SEMANTIC_QUALIFICATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    SEMANTIC_QUALIFICATION_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def semantic_engine_test_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Qualify the configured semantic stack using real inference only.

    This route intentionally keeps the historical `/semantic/test` API name for
    compatibility, but ACI-5 treats it as a qualification run. Hash embeddings
    and lexical reranking can keep Neo usable; they can never make this pass.
    """
    data = payload or {}
    engine = admin_engine_state_payload()
    embeddings = _embedding_config(engine)
    reranker_cfg = _reranker_config(engine)
    config_fp = semantic_config_fingerprint(engine)
    started = time.time()
    warnings: list[str] = []
    blockers: list[str] = []

    query = str(data.get("query") or "Which vault code belongs to Project Lantern?")
    relevant = str(data.get("relevant") or "Project Lantern uses vault code ORCHID-731 for the recovery vault.")
    unrelated = str(data.get("unrelated") or "A kitchen inventory lists flour, sugar, and cooking oil.")

    embedding_result: dict[str, Any] = {"status": "failed"}
    try:
        doc_emb = embed_texts([relevant, unrelated], engine_state=engine, allow_fallback=False, purpose="document")
        query_emb = embed_texts([query], engine_state=engine, allow_fallback=False, purpose="query")
        doc_vectors = doc_emb.get("vectors") or []
        query_vectors = query_emb.get("vectors") or []
        if len(doc_vectors) != 2 or len(query_vectors) != 1:
            raise RuntimeError("Embedding qualification did not return the expected number of vectors")
        dimension = int(doc_emb.get("dimension") or 0)
        if dimension <= 0 or any(len(vec) != dimension for vec in doc_vectors + query_vectors):
            raise RuntimeError("Embedding qualification returned inconsistent vector dimensions")
        relevant_score = _cosine_similarity(query_vectors[0], doc_vectors[0])
        unrelated_score = _cosine_similarity(query_vectors[0], doc_vectors[1])
        ranking_passed = relevant_score > unrelated_score
        if not ranking_passed:
            blockers.append("embedding_relevance_probe_failed")
        profile_fp = str(doc_emb.get("profile_fingerprint") or embedding_profile_fingerprint(embeddings, detected_dimension=dimension, model_id=str(doc_emb.get("model_id") or "")))
        if query_emb.get("profile_fingerprint") and query_emb.get("profile_fingerprint") != profile_fp:
            blockers.append("query_document_embedding_profile_mismatch")
        embedding_result = {
            "status": "passed" if ranking_passed and not blockers else "failed",
            "provider": doc_emb.get("provider"),
            "mode": doc_emb.get("mode"),
            "model_id": doc_emb.get("model_id"),
            "dimension": dimension,
            "vectors_finite": True,
            "query_relevant_similarity": round(relevant_score, 6),
            "query_unrelated_similarity": round(unrelated_score, 6),
            "relevance_probe_passed": ranking_passed,
            "profile_fingerprint": profile_fp,
            "profile_fingerprint_short": fingerprint_short(profile_fp),
            "profile_descriptor": doc_emb.get("profile_descriptor") or embedding_space_descriptor(embeddings, detected_dimension=dimension, model_id=str(doc_emb.get("model_id") or "")),
            "elapsed_ms": round(float(doc_emb.get("elapsed_ms") or 0) + float(query_emb.get("elapsed_ms") or 0), 2),
        }
    except Exception as exc:
        blockers.append("embedding_qualification_failed")
        embedding_result = {
            "status": "failed",
            "provider": str(embeddings.get("active_provider_id") or ""),
            "model_ref": str(embeddings.get("active_model_path") or embeddings.get("active_model_name") or embeddings.get("active_profile_id") or ""),
            "error": str(exc)[:1200],
            "profile_descriptor": embedding_config_descriptor(embeddings),
        }

    reranker_provider = str(reranker_cfg.get("active_provider_id") or "none")
    reranker_result: dict[str, Any]
    if reranker_provider == "none":
        reranker_result = {"status": "disabled", "provider": "none", "mode": "none", "semantic": False}
    elif reranker_provider == "lexical_overlap":
        warnings.append("lexical_overlap is enabled as a deliberate non-semantic reranker; semantic embedding remains usable but readiness is degraded.")
        reranker_result = {"status": "degraded", "provider": reranker_provider, "mode": "lexical_overlap", "semantic": False}
    else:
        try:
            sample = [
                {"id": "unrelated", "title": "Unrelated", "content": unrelated, "score": 0.5},
                {"id": "relevant", "title": "Relevant", "content": relevant, "score": 0.5},
            ]
            rerank = rerank_results(query, sample, engine_state=engine, top_n=2, allow_fallback=False)
            ranked = rerank.get("results") or []
            top_id = str((ranked[0] if ranked else {}).get("id") or "")
            passed = top_id == "relevant"
            if not passed:
                blockers.append("reranker_relevance_probe_failed")
            reranker_result = {
                "status": "passed" if passed else "failed",
                "provider": rerank.get("provider"),
                "mode": rerank.get("mode"),
                "model_id": rerank.get("model_id"),
                "top_result_id": top_id,
                "relevance_probe_passed": passed,
                "elapsed_ms": rerank.get("elapsed_ms"),
                "descriptor": reranker_config_descriptor(reranker_cfg),
            }
        except Exception as exc:
            blockers.append("reranker_qualification_failed")
            reranker_result = {
                "status": "failed",
                "provider": reranker_provider,
                "model_ref": str(reranker_cfg.get("active_model_path") or reranker_cfg.get("active_model_name") or reranker_cfg.get("active_profile_id") or ""),
                "error": str(exc)[:1200],
                "descriptor": reranker_config_descriptor(reranker_cfg),
            }

    blockers = list(dict.fromkeys(blockers))
    warnings = list(dict.fromkeys(warnings))
    overall_status = "failed" if blockers else ("warning" if warnings else "passed")
    qualified_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    record = {
        "schema_id": "neo.admin.semantic_qualification.v1",
        "version": "1.0.0-aci5",
        "status": overall_status,
        "qualified_at": qualified_at,
        "config_fingerprint": config_fp,
        "embedding": embedding_result,
        "reranker": reranker_result,
        "warnings": warnings,
        "blockers": blockers,
        "elapsed_ms": round((time.time() - started) * 1000, 2),
        "policy": "Qualification uses real inference only. Hash embeddings and lexical reranking are non-semantic fallbacks and never count as qualified readiness.",
    }
    _write_qualification(record)
    state = semantic_engine_state_payload()
    message = (
        f"Semantic qualification {overall_status}: embedding {embedding_result.get('status')}"
        f" · dim {embedding_result.get('dimension') or 0}"
        f" · reranker {reranker_result.get('status')}"
        f" · profile {embedding_result.get('profile_fingerprint_short') or 'none'}"
    )
    return {
        **record,
        "message": message,
        "sample_dimension": int(embedding_result.get("dimension") or 0),
        "state": state,
    }

