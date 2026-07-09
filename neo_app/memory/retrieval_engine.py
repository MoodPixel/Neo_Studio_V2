from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.admin.semantic_engine import embed_texts, rerank_results, semantic_engine_state_payload
from neo_app.memory.retrieval_profiles import get_retrieval_profile, retrieval_profiles_payload
from neo_app.memory.unified_schema import ensure_unified_memory_schema

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"
M9_SCHEMA_ID = "neo.memory.retrieval_rerank.m9.v1"
M9_PHASE = "M9"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _safe_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except Exception:
        return fallback


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()[:24]


def _tokens(text: str) -> list[str]:
    return [token.lower() for token in re.findall(r"[A-Za-z0-9_@#'’.-]{2,}", text or "")[:96]]


def _norm_score(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    if n <= 0:
        return 0.0
    dot = sum(float(a[i]) * float(b[i]) for i in range(n))
    na = math.sqrt(sum(float(v) * float(v) for v in a[:n])) or 1.0
    nb = math.sqrt(sum(float(v) * float(v) for v in b[:n])) or 1.0
    return max(0.0, min(1.0, (dot / (na * nb) + 1.0) / 2.0))


def _clean_text(value: Any, *, max_chars: int = 1200) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "…"
    return text


def _recency(updated_at: str | None) -> float:
    if not updated_at:
        return 0.25
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400.0)
        return round(1.0 / (1.0 + age_days / 60.0), 6)
    except Exception:
        return 0.25


def _priority(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return 0.5


class UnifiedMemoryRetrievalEngine:
    """Phase M9 hybrid retrieval and reranker engine for unified Neo memory.

    SQLite remains authoritative. Embeddings are stored as SQLite vector_json rows
    in neo_memory_embeddings. Chroma remains optional, not required for M9.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            ensure_unified_memory_schema(conn)
            cols = {str(row[1]) for row in conn.execute("PRAGMA table_info(neo_memory_embeddings)").fetchall()}
            if "vector_json" not in cols:
                conn.execute("ALTER TABLE neo_memory_embeddings ADD COLUMN vector_json TEXT NOT NULL DEFAULT '[]'")
            if "score_json" not in cols:
                conn.execute("ALTER TABLE neo_memory_embeddings ADD COLUMN score_json TEXT NOT NULL DEFAULT '{}'")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_embeddings_scope ON neo_memory_embeddings(surface, project_id, scope_id, status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_neo_memory_embeddings_model ON neo_memory_embeddings(model_id, dimension)")

    def status(self) -> dict[str, Any]:
        self._ensure_schema()
        with self._connect() as conn:
            def count(table: str, where: str = "", params: tuple[Any, ...] = ()) -> int:
                try:
                    sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
                    return int(conn.execute(sql, params).fetchone()[0])
                except Exception:
                    return 0
            queued = count("neo_memory_fragments", "status = 'active' AND embedding_status != 'indexed'")
            active_fragments = count("neo_memory_fragments", "status = 'active'")
            embeddings = count("neo_memory_embeddings", "status = 'indexed'")
            recent = [dict(row) for row in conn.execute(
                """
                SELECT access_id, consumer, surface, project_id, scope_id, query, created_at, metadata_json
                FROM neo_memory_access_log
                ORDER BY created_at DESC
                LIMIT 8
                """
            ).fetchall()]
        semantic = semantic_engine_state_payload()
        return {
            "schema_id": M9_SCHEMA_ID,
            "phase": M9_PHASE,
            "status": "ready",
            "label": "Unified Memory Retrieval + Reranker Upgrade",
            "sqlite_source_of_truth": True,
            "counts": {
                "active_fragments": active_fragments,
                "indexed_embeddings": embeddings,
                "queued_fragments": queued,
            },
            "semantic_engine": semantic,
            "retrieval_profiles": retrieval_profiles_payload(),
            "reranker_profiles": self.reranker_profiles(),
            "recent_access": [{**item, "metadata": _safe_json(item.pop("metadata_json", "{}"), {})} for item in recent],
            "endpoints": {
                "status": "/api/memory/retrieval-rerank/status",
                "index": "/api/memory/retrieval-rerank/index",
                "query": "/api/memory/retrieval-rerank/query",
            },
            "policy": "Metadata/graph filters first, keyword + SQLite vector retrieval second, rerank shortlist third, Control Center decides final prompt brief.",
        }

    def reranker_profiles(self) -> dict[str, Any]:
        semantic = semantic_engine_state_payload()
        configured = semantic.get("reranker") if isinstance(semantic.get("reranker"), dict) else {}
        return {
            "schema_id": "neo.memory.reranker_profiles.m9.v1",
            "status": "ready",
            "active_admin_reranker": configured,
            "profiles": [
                {"profile_id": "fallback_keyword", "label": "Fallback keyword", "candidate_limit": 24, "rerank_top": 0, "uses_external_model": False},
                {"profile_id": "fast_local", "label": "Fast local", "candidate_limit": 32, "rerank_top": 8, "uses_external_model": True},
                {"profile_id": "balanced_local", "label": "Balanced local", "candidate_limit": 48, "rerank_top": 12, "uses_external_model": True},
                {"profile_id": "high_quality_local", "label": "High quality local", "candidate_limit": 72, "rerank_top": 16, "uses_external_model": True},
                {"profile_id": "experimental_heavy", "label": "Experimental heavy", "candidate_limit": 96, "rerank_top": 24, "uses_external_model": True, "warning": "Use only after benchmark/model warm-up passes."},
            ],
            "policy": "Do not reduce canon detail to fix slow models. Pick a model/profile that matches the PC and task, then rerank a shortlist.",
        }

    def index_embeddings(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_schema()
        data = payload or {}
        surface = str(data.get("surface") or "").strip()
        project_id = str(data.get("project_id") or "").strip()
        scope_id = str(data.get("scope_id") or "").strip()
        limit = max(1, min(int(data.get("limit") or 250), 5000))
        force = bool(data.get("force", False))
        allow_fallback = bool(data.get("allow_fallback", True))
        clauses = ["status = 'active'", "content != ''"]
        params: list[Any] = []
        if not force:
            clauses.append("embedding_status != 'indexed'")
        if surface:
            clauses.append("surface = ?")
            params.append(surface)
        if project_id:
            clauses.append("project_id = ?")
            params.append(project_id)
        if scope_id:
            clauses.append("scope_id = ?")
            params.append(scope_id)
        started = time.time()
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT fragment_id, surface, project_id, scope_id, title, content, memory_type, source_type, source_id, updated_at
                FROM neo_memory_fragments
                WHERE {' AND '.join(clauses)}
                ORDER BY priority DESC, updated_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()
            fragments = [dict(row) for row in rows]
            if not fragments:
                return {"ok": True, "schema_id": M9_SCHEMA_ID, "phase": M9_PHASE, "status": "no_pending_fragments", "indexed_count": 0, "filters": {"surface": surface, "project_id": project_id, "scope_id": scope_id, "force": force}}
            texts = [f"{item.get('title') or ''}\n{item.get('content') or ''}" for item in fragments]
            emb = embed_texts(texts, allow_fallback=allow_fallback)
            vectors = emb.get("vectors") or []
            stamp = _now()
            indexed = []
            for item, vector in zip(fragments, vectors):
                if not vector:
                    continue
                embedding_id = f"neoemb:{_hash(str(item.get('fragment_id')) + ':' + str(emb.get('model_id')))}"
                metadata = {
                    "phase": M9_PHASE,
                    "embedding_mode": emb.get("mode"),
                    "fallback_used": bool(emb.get("fallback_used")),
                    "memory_type": item.get("memory_type"),
                    "source_type": item.get("source_type"),
                    "source_id": item.get("source_id"),
                }
                conn.execute(
                    """
                    INSERT INTO neo_memory_embeddings(
                        embedding_id, fragment_id, surface, project_id, scope_id, model_id, dimension,
                        vector_store, collection_name, vector_ref, status, created_at, updated_at,
                        metadata_json, vector_json, score_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(embedding_id) DO UPDATE SET
                        surface=excluded.surface,
                        project_id=excluded.project_id,
                        scope_id=excluded.scope_id,
                        model_id=excluded.model_id,
                        dimension=excluded.dimension,
                        vector_store=excluded.vector_store,
                        vector_ref=excluded.vector_ref,
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        metadata_json=excluded.metadata_json,
                        vector_json=excluded.vector_json,
                        score_json=excluded.score_json
                    """,
                    (
                        embedding_id, item.get("fragment_id"), item.get("surface") or "global", item.get("project_id"), item.get("scope_id"),
                        str(emb.get("model_id") or emb.get("mode") or "unknown"), len(vector), "sqlite_vector_json", "neo_memory_embeddings", embedding_id,
                        "indexed", stamp, stamp, _json(metadata), _json(vector), _json({"source_updated_at": item.get("updated_at")}),
                    ),
                )
                conn.execute("UPDATE neo_memory_fragments SET embedding_status = 'indexed', updated_at = ? WHERE fragment_id = ?", (stamp, item.get("fragment_id")))
                indexed.append({"fragment_id": item.get("fragment_id"), "embedding_id": embedding_id, "dimension": len(vector), "surface": item.get("surface"), "scope_id": item.get("scope_id")})
            conn.execute(
                """
                INSERT OR REPLACE INTO neo_memory_jobs(job_id, job_type, status, surface, project_id, scope_id, started_at, finished_at, progress_json, result_json, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (f"m9_index:{uuid4().hex[:12]}", "m9_embedding_index", "completed", surface or "global", project_id or None, scope_id or None, _now(), _now(), _json({"input": len(fragments)}), _json({"indexed_count": len(indexed), "embedding": {k: v for k, v in emb.items() if k != "vectors"}}), "", stamp, stamp),
            )
        return {
            "ok": True,
            "schema_id": M9_SCHEMA_ID,
            "phase": M9_PHASE,
            "status": "indexed",
            "indexed_count": len(indexed),
            "input_count": len(fragments),
            "elapsed_ms": round((time.time() - started) * 1000, 2),
            "embedding": {k: v for k, v in emb.items() if k != "vectors"},
            "indexed_preview": indexed[:50],
            "policy": "Embeddings are cached in SQLite vector_json. Chroma remains optional mirror, not source of truth.",
        }

    def retrieve(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self._ensure_schema()
        data = payload or {}
        query = str(data.get("query") or data.get("user_input") or "").strip()
        profile_id = str(data.get("profile") or data.get("profile_id") or "smart").strip() or "smart"
        profile = get_retrieval_profile(profile_id)
        limit = max(1, min(int(data.get("limit") or 8), 80))
        candidate_limit = max(limit, min(int(data.get("candidate_limit") or (limit * int(profile.get("candidate_multiplier") or 4))), 300))
        rerank_top = max(0, min(int(data.get("rerank_top") or data.get("top_n") or limit), 64))
        semantic_enabled = bool(data.get("semantic", profile.get("semantic", True)))
        rerank_enabled = bool(data.get("rerank", profile.get("rerank", True))) and rerank_top > 0
        surface = str(data.get("surface") or "").strip()
        project_id = str(data.get("project_id") or "").strip()
        scope_id = str(data.get("scope_id") or "").strip()
        memory_types = data.get("memory_types") if isinstance(data.get("memory_types"), list) else []
        consumer = str(data.get("consumer") or "control_center").strip() or "control_center"
        started = time.time()
        keyword_candidates = self._keyword_candidates(query, surface=surface, project_id=project_id, scope_id=scope_id, memory_types=memory_types, limit=candidate_limit)
        vector_candidates: list[dict[str, Any]] = []
        semantic_status: dict[str, Any] = {"status": "skipped", "reason": "semantic_disabled" if not semantic_enabled else "no_query"}
        if semantic_enabled and query:
            # Make retrieval self-healing: index missing scoped fragments in a bounded batch before vector search.
            self.index_embeddings({"surface": surface, "project_id": project_id, "scope_id": scope_id, "limit": int(data.get("index_limit") or 250), "force": False, "allow_fallback": True})
            emb = embed_texts([query], allow_fallback=True)
            vectors = emb.get("vectors") or []
            if vectors:
                vector_candidates = self._vector_candidates(vectors[0], surface=surface, project_id=project_id, scope_id=scope_id, memory_types=memory_types, limit=candidate_limit)
                semantic_status = {"status": "ready", "model_id": emb.get("model_id") or emb.get("mode"), "mode": emb.get("mode"), "fallback_used": bool(emb.get("fallback_used")), "candidate_count": len(vector_candidates)}
            else:
                semantic_status = {"status": "failed", "reason": "query_embedding_failed", "embedding": {k: v for k, v in emb.items() if k != "vectors"}}
        candidates = self._merge_candidates(keyword_candidates, vector_candidates)
        weights = {
            "keyword": _norm_score(data.get("keyword_weight", profile.get("keyword_weight", 0.4))),
            "vector": _norm_score(data.get("vector_weight", profile.get("vector_weight", 0.35))),
            "recency": _norm_score(data.get("recency_weight", profile.get("recency_weight", 0.1))),
            "importance": _norm_score(data.get("importance_weight", profile.get("importance_weight", 0.15))),
        }
        for item in candidates:
            score = item.get("keyword_score", 0.0) * weights["keyword"] + item.get("vector_score", 0.0) * weights["vector"] + _recency(item.get("updated_at")) * weights["recency"] + _priority(item.get("priority")) * weights["importance"]
            item["score"] = round(max(0.0, min(1.0, score)), 6)
            item["score_components"] = {"keyword": item.get("keyword_score", 0), "vector": item.get("vector_score", 0), "recency": _recency(item.get("updated_at")), "importance": _priority(item.get("priority"))}
        candidates.sort(key=lambda item: item.get("score") or 0, reverse=True)
        reranker_status: dict[str, Any] = {"status": "skipped", "reason": "disabled" if not rerank_enabled else "no_candidates"}
        if rerank_enabled and candidates:
            shortlist = candidates[:candidate_limit]
            rr = rerank_results(query, shortlist, top_n=min(rerank_top, limit), allow_fallback=True)
            reranker_status = {k: v for k, v in rr.items() if k != "results"}
            selected = rr.get("results") or shortlist[:limit]
        else:
            selected = candidates[:limit]
        selected = selected[:limit]
        trace_id = f"m9retrieval_{uuid4().hex[:12]}"
        stamp = _now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO neo_memory_access_log(access_id, consumer, surface, project_id, scope_id, query, result_ids_json, created_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trace_id, consumer, surface or "global", project_id or None, scope_id or None, query, _json([item.get("fragment_id") for item in selected]), stamp, _json({
                    "phase": M9_PHASE,
                    "profile": profile_id,
                    "candidate_count": len(candidates),
                    "keyword_candidate_count": len(keyword_candidates),
                    "vector_candidate_count": len(vector_candidates),
                    "semantic": semantic_status,
                    "reranker": reranker_status,
                    "weights": weights,
                })),
            )
        return {
            "ok": True,
            "schema_id": M9_SCHEMA_ID,
            "phase": M9_PHASE,
            "status": "ready",
            "trace_id": trace_id,
            "query": query,
            "profile": profile_id,
            "scope": {"surface": surface, "project_id": project_id, "scope_id": scope_id, "memory_types": memory_types},
            "backend_used": "+".join(["metadata_scope", "keyword", "sqlite_vector" if vector_candidates else "", "reranker" if rerank_enabled else ""]).strip("+"),
            "semantic": semantic_status,
            "reranker": reranker_status,
            "stats": {"result_count": len(selected), "candidate_count": len(candidates), "keyword_candidate_count": len(keyword_candidates), "vector_candidate_count": len(vector_candidates), "elapsed_ms": round((time.time() - started) * 1000, 2)},
            "results": [self._public_result(item) for item in selected],
            "policy": "Full memory remains in SQLite; prompt receives a compact Control Center brief built from selected results.",
        }

    def _base_where(self, *, surface: str = "", project_id: str = "", scope_id: str = "", memory_types: list[Any] | None = None) -> tuple[list[str], list[Any]]:
        clauses = ["f.status = 'active'", "f.content != ''"]
        params: list[Any] = []
        if surface:
            clauses.append("f.surface = ?")
            params.append(surface)
        if project_id:
            clauses.append("f.project_id = ?")
            params.append(project_id)
        if scope_id:
            clauses.append("f.scope_id = ?")
            params.append(scope_id)
        clean_types = [str(item) for item in (memory_types or []) if str(item or "").strip()]
        if clean_types:
            placeholders = ",".join("?" for _ in clean_types)
            clauses.append(f"f.memory_type IN ({placeholders})")
            params.extend(clean_types)
        return clauses, params

    def _keyword_candidates(self, query: str, *, surface: str, project_id: str, scope_id: str, memory_types: list[Any], limit: int) -> list[dict[str, Any]]:
        tokens = _tokens(query)[:10]
        clauses, params = self._base_where(surface=surface, project_id=project_id, scope_id=scope_id, memory_types=memory_types)
        score_parts: list[str] = []
        score_params: list[Any] = []
        if tokens:
            token_clauses = []
            for token in tokens:
                like = f"%{token}%"
                token_clauses.append("(lower(f.content) LIKE ? OR lower(f.title) LIKE ? OR lower(f.summary) LIKE ?)")
                params.extend([like, like, like])
                score_parts.append("CASE WHEN lower(f.content) LIKE ? OR lower(f.title) LIKE ? OR lower(f.summary) LIKE ? THEN 1 ELSE 0 END")
                score_params.extend([like, like, like])
            clauses.append("(" + " OR ".join(token_clauses) + ")")
        score_expr = " + ".join(score_parts) if score_parts else "0"
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT f.*, ({score_expr}) AS keyword_hits
                FROM neo_memory_fragments f
                WHERE {' AND '.join(clauses)}
                ORDER BY keyword_hits DESC, f.priority DESC, f.updated_at DESC
                LIMIT ?
                """,
                (*score_params, *params, limit),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            hits = float(item.pop("keyword_hits") or 0.0)
            item["keyword_score"] = min(1.0, hits / max(1.0, len(tokens) or 1.0)) if tokens else 0.15
            item["vector_score"] = 0.0
            item["retrieval_type"] = "keyword"
            out.append(item)
        return out

    def _vector_candidates(self, query_vector: list[float], *, surface: str, project_id: str, scope_id: str, memory_types: list[Any], limit: int) -> list[dict[str, Any]]:
        clauses, params = self._base_where(surface=surface, project_id=project_id, scope_id=scope_id, memory_types=memory_types)
        clauses.append("e.status = 'indexed'")
        clauses.append("e.vector_json != '[]'")
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT f.*, e.embedding_id, e.model_id, e.dimension, e.vector_json, e.metadata_json AS embedding_metadata_json
                FROM neo_memory_embeddings e
                JOIN neo_memory_fragments f ON f.fragment_id = e.fragment_id
                WHERE {' AND '.join(clauses)}
                ORDER BY f.priority DESC, f.updated_at DESC
                LIMIT ?
                """,
                (*params, max(limit * 3, limit)),
            ).fetchall()
        scored = []
        for row in rows:
            item = dict(row)
            vector = _safe_json(item.pop("vector_json", "[]"), [])
            sim = _cosine(query_vector, vector if isinstance(vector, list) else [])
            item["keyword_score"] = 0.0
            item["vector_score"] = round(sim, 6)
            item["score"] = item["vector_score"]
            item["retrieval_type"] = "sqlite_vector"
            item["embedding"] = {"embedding_id": item.pop("embedding_id", ""), "model_id": item.pop("model_id", ""), "dimension": item.pop("dimension", 0), "metadata": _safe_json(item.pop("embedding_metadata_json", "{}"), {})}
            scored.append(item)
        scored.sort(key=lambda item: item.get("vector_score") or 0, reverse=True)
        return scored[:limit]

    def _merge_candidates(self, candidates: list[dict[str, Any]], vector_candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for item in candidates + vector_candidates:
            fid = str(item.get("fragment_id") or "")
            if not fid:
                continue
            if fid not in merged:
                merged[fid] = dict(item)
                continue
            existing = merged[fid]
            existing["keyword_score"] = max(float(existing.get("keyword_score") or 0), float(item.get("keyword_score") or 0))
            existing["vector_score"] = max(float(existing.get("vector_score") or 0), float(item.get("vector_score") or 0))
            types = set(str(existing.get("retrieval_type") or "").split("+")) | set(str(item.get("retrieval_type") or "").split("+"))
            existing["retrieval_type"] = "+".join(sorted(t for t in types if t))
        return list(merged.values())

    def _public_result(self, item: dict[str, Any]) -> dict[str, Any]:
        metadata = _safe_json(item.get("metadata_json"), {})
        return {
            "fragment_id": item.get("fragment_id"),
            "surface": item.get("surface"),
            "project_id": item.get("project_id"),
            "scope_id": item.get("scope_id"),
            "source_type": item.get("source_type"),
            "source_id": item.get("source_id"),
            "memory_type": item.get("memory_type"),
            "title": item.get("title") or "Memory fragment",
            "content": item.get("content") or "",
            "snippet": _clean_text(item.get("content"), max_chars=520),
            "summary": item.get("summary") or "",
            "score": _norm_score(item.get("score")),
            "keyword_score": _norm_score(item.get("keyword_score")),
            "vector_score": _norm_score(item.get("vector_score")),
            "retrieval_type": item.get("retrieval_type") or "unknown",
            "priority": _priority(item.get("priority")),
            "confidence": item.get("confidence"),
            "trust_level": item.get("trust_level"),
            "updated_at": item.get("updated_at"),
            "metadata": metadata,
            "score_components": item.get("score_components") or {},
        }
