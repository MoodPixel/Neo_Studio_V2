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

from neo_app.admin.engine import admin_engine_state_payload

SEMANTIC_SCHEMA_ID = "neo.admin.semantic_engine.v1"
SEMANTIC_VERSION = "0.3.1-qwen3-native-provider-ui-proof"


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


def _normalize_vector(vec: Any) -> list[float]:
    """Normalize vectors from lists, tuples, numpy arrays, torch tensors, or scalars.

    The old implementation only accepted list/tuple. SentenceTransformer usually
    returns numpy arrays, so Neo reported `dim 0` even when the model returned a
    valid BGE-M3 vector.
    """
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
            clean.append(float(item))
        except Exception:
            continue
    if not clean:
        return []
    norm = math.sqrt(sum(v * v for v in clean)) or 1.0
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
def _load_sentence_transformer(model_ref: str):
    from sentence_transformers import SentenceTransformer  # type: ignore
    try:
        return SentenceTransformer(model_ref, trust_remote_code=True)
    except TypeError:
        return SentenceTransformer(model_ref)


@lru_cache(maxsize=8)
def _load_cross_encoder(model_ref: str):
    from sentence_transformers import CrossEncoder  # type: ignore
    try:
        return CrossEncoder(model_ref, trust_remote_code=True)
    except TypeError:
        return CrossEncoder(model_ref)


@lru_cache(maxsize=4)
def _load_qwen3_reranker(model_ref: str):
    import torch  # type: ignore
    from transformers import AutoModelForCausalLM, AutoTokenizer  # type: ignore

    tokenizer = AutoTokenizer.from_pretrained(model_ref, trust_remote_code=True)
    if getattr(tokenizer, "pad_token", None) is None:
        tokenizer.pad_token = getattr(tokenizer, "eos_token", None) or tokenizer.unk_token
    kwargs: dict[str, Any] = {"trust_remote_code": True}
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


def _qwen3_rerank_scores(model_ref: str, query: str, documents: list[str]) -> list[float]:
    import torch  # type: ignore

    tokenizer, model = _load_qwen3_reranker(model_ref)
    yes_ids = tokenizer.encode("yes", add_special_tokens=False) or tokenizer.encode(" Yes", add_special_tokens=False)
    no_ids = tokenizer.encode("no", add_special_tokens=False) or tokenizer.encode(" No", add_special_tokens=False)
    if not yes_ids or not no_ids:
        raise RuntimeError("Could not resolve Qwen reranker yes/no token ids")
    yes_id = int(yes_ids[-1])
    no_id = int(no_ids[-1])
    prompts = [_qwen3_prompt(query, doc) for doc in documents]
    device = next(model.parameters()).device
    inputs = tokenizer(prompts, padding=True, truncation=True, max_length=2048, return_tensors="pt").to(device)
    with torch.no_grad():
        outputs = model(**inputs)
        logits = outputs.logits[:, -1, :]
        yes_no = logits[:, [yes_id, no_id]]
        probs = torch.softmax(yes_no, dim=-1)[:, 0]
    return [float(v) for v in probs.detach().cpu().tolist()]


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
    embedding_provider = str(embeddings.get("active_provider_id") or "local_hash_embeddings")
    reranker_provider = str(reranker.get("active_provider_id") or "none")
    embedding_ref = str(embeddings.get("active_model_path") or embeddings.get("active_model_name") or embeddings.get("active_profile_id") or "").strip()
    reranker_ref = str(reranker.get("active_model_path") or reranker.get("active_model_name") or reranker.get("active_profile_id") or "").strip()
    return {
        "schema_id": SEMANTIC_SCHEMA_ID,
        "version": SEMANTIC_VERSION,
        "status": "ready" if embedding_ref or embedding_provider == "local_hash_embeddings" else "fallback_ready",
        "owner": "admin",
        "embedding": {
            "provider": embedding_provider,
            "model_ref": embedding_ref,
            "base_url": embeddings.get("active_base_url") or embeddings.get("base_url") or os.environ.get("NEO_EMBEDDING_BASE_URL", ""),
            "api_key_env": embeddings.get("active_api_key_env") or embeddings.get("api_key_env") or "",
            "dimension": embeddings.get("default_dimension") or None,
            "external_ready": bool(embedding_ref or embeddings.get("active_base_url") or embeddings.get("base_url")),
            "fallback": "local_hash_embeddings",
        },
        "reranker": {
            "provider": reranker_provider,
            "model_ref": reranker_ref,
            "base_url": reranker.get("active_base_url") or reranker.get("base_url") or os.environ.get("NEO_RERANKER_BASE_URL", ""),
            "api_key_env": reranker.get("active_api_key_env") or reranker.get("api_key_env") or "",
            "top_n": reranker.get("default_top_n") or 8,
            "external_ready": bool(reranker_provider != "none" and (reranker_ref or reranker.get("active_base_url") or reranker.get("base_url"))),
            "fallback": "lexical_overlap",
        },
        "vector_store": vector,
        "supported_embedding_providers": ["sentence_transformers", "local_embedding_model", "openai_compatible_embeddings", "local_hash_embeddings"],
        "supported_reranker_providers": ["cross_encoder", "bge_reranker", "qwen3_reranker", "local_reranker", "openai_compatible_reranker", "lexical_overlap", "none"],
    }


def embed_texts(texts: list[str], *, engine_state: dict[str, Any] | None = None, dimension: int | None = None, allow_fallback: bool = True) -> dict[str, Any]:
    config = _embedding_config(engine_state)
    provider = str(config.get("active_provider_id") or "local_hash_embeddings").strip() or "local_hash_embeddings"
    model_ref = str(config.get("active_model_path") or config.get("active_model_name") or config.get("active_profile_id") or "").strip()
    dim = int(dimension or config.get("default_dimension") or 96)
    started = time.time()
    clean_texts = [str(text or "") for text in texts]
    try:
        if provider in {"sentence_transformers", "local_embedding_model"} and model_ref:
            hint = _model_path_hint(model_ref)
            if os.path.isabs(model_ref) and not hint["exists"]:
                raise RuntimeError(f"Embedding model path does not exist: {model_ref}")
            model = _load_sentence_transformer(model_ref)
            vectors = model.encode(clean_texts, normalize_embeddings=True, convert_to_numpy=False)
            out = [_normalize_vector(vec) for vec in vectors]
            actual_dim = len(out[0]) if out else 0
            if actual_dim <= 0:
                raise RuntimeError(f"Embedding model returned a zero-dimensional vector. Path hint: {hint}")
            return {"status": "embedded", "mode": "external_sentence_transformers", "provider": provider, "model_id": model_ref, "dimension": actual_dim, "vectors": out, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "model_hint": hint}
        if provider == "openai_compatible_embeddings":
            base = str(config.get("active_base_url") or config.get("base_url") or os.environ.get("NEO_EMBEDDING_BASE_URL") or "").strip()
            model_name = str(config.get("active_model_name") or model_ref or os.environ.get("NEO_EMBEDDING_MODEL") or "").strip()
            payload = {"model": model_name, "input": clean_texts}
            url = _endpoint(base, "/v1/embeddings")
            response = _post_json(url, payload, api_key=_api_key_from_config(config), timeout=float(config.get("request_timeout") or 60))
            data = response.get("data") or []
            ordered = sorted(data, key=lambda item: int(item.get("index", 0))) if isinstance(data, list) else []
            out = [_normalize_vector(item.get("embedding")) for item in ordered]
            if len(out) != len(clean_texts):
                raise RuntimeError(f"Embedding response returned {len(out)} vectors for {len(clean_texts)} inputs")
            actual_dim = len(out[0]) if out else 0
            if actual_dim <= 0:
                raise RuntimeError("Embedding API returned a zero-dimensional vector")
            return {"status": "embedded", "mode": "external_openai_compatible_embeddings", "provider": provider, "model_id": model_name or model_ref or "openai_compatible_embeddings", "dimension": actual_dim, "vectors": out, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False}
        if provider == "local_hash_embeddings" or not model_ref:
            raise RuntimeError("No external embedding model configured")
    except Exception as exc:
        if not allow_fallback:
            raise
        fallback = [deterministic_text_embedding(text, dimension=dim) for text in clean_texts]
        return {"status": "fallback_embedded", "mode": "local_hash_embeddings", "provider": provider, "model_id": "local_hash_embeddings", "dimension": len(fallback[0]) if fallback else dim, "vectors": fallback, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": True, "error": str(exc)[:800]}
    fallback = [deterministic_text_embedding(text, dimension=dim) for text in clean_texts]
    return {"status": "fallback_embedded", "mode": "local_hash_embeddings", "provider": provider, "model_id": "local_hash_embeddings", "dimension": len(fallback[0]) if fallback else dim, "vectors": fallback, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": True, "error": "Unhandled provider fallback"}


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
        return {"status": "disabled", "mode": "none", "provider": provider, "results": (results or [])[: int(top_n or len(results or []) or 8)], "fallback_used": False}
    limit = _safe_positive_int(top_n or config.get("default_top_n") or len(results) or 8, 8, maximum=100)
    # Runtime packet builds should not feed 30-100 long lore chunks into a causal-LM/cross-encoder reranker.
    # Keep real external reranking, but only on the best shortlist from retrieval. This avoids 90s request deaths.
    max_candidates = _safe_positive_int(config.get("max_candidates_per_request") or min(max(limit, 8), 12), min(max(limit, 8), 12), maximum=32)
    text_limit = _safe_positive_int(config.get("candidate_text_limit") or 900, 900, minimum=200, maximum=4096)
    rerank_items, tail_items = _prepare_rerank_items(results or [], max_candidates=max_candidates, text_limit=text_limit)
    started = time.time()
    meta = {
        "input_count": len(results or []),
        "reranked_candidate_count": len(rerank_items),
        "tail_count": len(tail_items),
        "candidate_text_limit": text_limit,
        "truncated_for_runtime_safety": len(results or []) > len(rerank_items),
    }
    try:
        if provider in {"cross_encoder", "bge_reranker", "local_reranker"} and model_ref:
            hint = _model_path_hint(model_ref)
            if os.path.isabs(model_ref) and not hint["exists"]:
                raise RuntimeError(f"Reranker model path does not exist: {model_ref}")
            model = _load_cross_encoder(model_ref)
            pairs = [(query, str(item.get("content") or item.get("title") or "")) for item in rerank_items]
            scores = model.predict(pairs)
            rescored = []
            for item, score in zip(rerank_items, scores):
                updated = dict(item)
                updated["external_rerank_score"] = float(score)
                updated["score"] = round((float(item.get("score") or 0) * 0.35) + (float(score) * 0.65), 6)
                rescored.append(updated)
            rescored.sort(key=lambda item: item.get("score", 0), reverse=True)
            output = (rescored + tail_items)[:limit]
            return {"status": "reranked", "mode": "external_cross_encoder", "provider": provider, "model_id": model_ref, "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "model_hint": hint, "provider_note": provider_note, **meta}
        if provider == "qwen3_reranker" and model_ref:
            hint = _model_path_hint(model_ref)
            if os.path.isabs(model_ref) and not hint["exists"]:
                raise RuntimeError(f"Qwen3 reranker model path does not exist: {model_ref}")
            documents = [str(item.get("content") or item.get("title") or "") for item in rerank_items]
            scores = _qwen3_rerank_scores(model_ref, query, documents)
            rescored = []
            for item, score in zip(rerank_items, scores):
                updated = dict(item)
                updated["external_rerank_score"] = float(score)
                updated["score"] = round((float(item.get("score") or 0) * 0.35) + (float(score) * 0.65), 6)
                rescored.append(updated)
            rescored.sort(key=lambda item: item.get("score", 0), reverse=True)
            output = (rescored + tail_items)[:limit]
            return {"status": "reranked", "mode": "external_qwen3_reranker", "provider": provider, "model_id": model_ref, "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, "model_hint": hint, "provider_note": provider_note, **meta}
        if provider == "openai_compatible_reranker":
            base = str(config.get("active_base_url") or config.get("base_url") or os.environ.get("NEO_RERANKER_BASE_URL") or "").strip()
            model_name = str(config.get("active_model_name") or model_ref or os.environ.get("NEO_RERANKER_MODEL") or "").strip()
            documents = [str(item.get("content") or item.get("title") or "") for item in rerank_items]
            response = _post_json(_endpoint(base, "/rerank"), {"model": model_name, "query": query, "documents": documents, "top_n": min(limit, len(documents))}, api_key=_api_key_from_config(config), timeout=float(config.get("request_timeout") or 45))
            response_results = response.get("results") or response.get("data") or []
            rescored = []
            for entry in response_results if isinstance(response_results, list) else []:
                idx = int(entry.get("index", 0))
                if 0 <= idx < len(rerank_items):
                    updated = dict(rerank_items[idx])
                    score = float(entry.get("relevance_score", entry.get("score", 0)))
                    updated["external_rerank_score"] = score
                    updated["score"] = round((float(updated.get("score") or 0) * 0.35) + (score * 0.65), 6)
                    rescored.append(updated)
            if rescored:
                rescored.sort(key=lambda item: item.get("score", 0), reverse=True)
                output = (rescored + tail_items)[:limit]
                return {"status": "reranked", "mode": "external_openai_compatible_reranker", "provider": provider, "model_id": model_name or model_ref, "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": False, **meta}
            raise RuntimeError("Reranker response returned no usable results")
        raise RuntimeError("No external reranker model configured")
    except Exception as exc:
        if not allow_fallback:
            raise
        rescored = []
        for item in rerank_items:
            updated = dict(item)
            lexical = _lexical_score(query, f"{updated.get('title','')} {updated.get('content','')}")
            updated["fallback_rerank_score"] = round(lexical, 6)
            updated["score"] = round((float(updated.get("score") or 0) * 0.72) + (lexical * 0.28), 6)
            rescored.append(updated)
        rescored.sort(key=lambda item: item.get("score", 0), reverse=True)
        output = (rescored + tail_items)[:limit]
        return {"status": "fallback_reranked", "mode": "lexical_overlap", "provider": provider, "model_id": "lexical_overlap", "results": output, "elapsed_ms": round((time.time() - started) * 1000, 2), "fallback_used": True, "error": str(exc)[:800], "provider_note": provider_note, **meta}


def semantic_engine_test_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    text = str(data.get("text") or "Neo Studio roleplay memory semantic engine test")
    engine = admin_engine_state_payload()
    emb = embed_texts([text], engine_state=engine, allow_fallback=True)
    rerank_docs = [
        {"title": "Relevant roleplay memory", "content": text, "score": 0.5},
        {"title": "Unrelated memory", "content": "shopping list, weather, unrelated placeholder", "score": 0.2},
    ]
    rerank = rerank_results(text, rerank_docs, engine_state=engine, allow_fallback=True)
    sample_dimension = len((emb.get("vectors") or [[]])[0]) if emb.get("vectors") else int(emb.get("dimension") or 0)
    warnings: list[str] = []
    if sample_dimension <= 0:
        warnings.append("Embedding test returned dimension 0. Check model path, dependencies, and loader errors.")
    if emb.get("fallback_used"):
        warnings.append(f"Embedding fallback used: {emb.get('error') or 'unknown reason'}")
    if rerank.get("provider_note"):
        warnings.append(str(rerank.get("provider_note")))
    if rerank.get("fallback_used"):
        warnings.append(f"Reranker fallback used: {rerank.get('error') or 'unknown reason'}")
    status = "passed" if not warnings else "warning"
    return {
        "schema_id": "neo.admin.semantic_engine.test.v1",
        "status": status,
        "message": f"Semantic test: {emb.get('mode') or 'unknown'} · dim {sample_dimension} · reranker {rerank.get('mode') or 'none'}",
        "warnings": warnings,
        "embedding": {k: v for k, v in emb.items() if k != "vectors"},
        "sample_dimension": sample_dimension,
        "reranker": {k: v for k, v in rerank.items() if k != "results"},
        "rerank_sample": (rerank.get("results") or [])[:2],
        "state": semantic_engine_state_payload(),
    }
