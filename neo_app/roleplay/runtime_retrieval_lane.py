from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from neo_app.admin.semantic_engine import rerank_results
from neo_app.roleplay.engine_bridge import roleplay_engine_bridge_state
from neo_app.roleplay.retrieval import (
    roleplay_retrieval_state_payload,
    search_retrieval_foundation_payload,
    search_retrieval_semantic_payload,
)
from neo_app.roleplay.sqlite_upgrade import ensure_roleplay_sqlite_upgrade_schema, rebuild_roleplay_memory_search_documents
from neo_app.roleplay.storage import ROLEPLAY_DATA_ROOT, ROLEPLAY_SQLITE_PATH, _relative_to_root, ensure_roleplay_foundation
from neo_app.roleplay.retrieval_labels_debug_proof import label_runtime_retrieval_rows, write_retrieval_label_proofs

PHASE10_SCHEMA_ID: Final[str] = "neo.roleplay.runtime_retrieval_lane.v1"
PHASE10_VERSION: Final[str] = "1.0.0-phase10-runtime-retrieval-lane"
PHASE10_CONTRACT_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "runtime_retrieval_lane_contract.json"
PHASE10_STATE_PATH: Final[Path] = ROLEPLAY_DATA_ROOT / "runtime_retrieval_lane_state.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _connect() -> sqlite3.Connection:
    ensure_roleplay_foundation(write_manifest=True)
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    ROLEPLAY_SQLITE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(ROLEPLAY_SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_int(value: Any, default: int = 12, minimum: int = 1, maximum: int = 100) -> int:
    try:
        parsed = int(value)
    except Exception:
        parsed = default
    return max(minimum, min(parsed, maximum))


def _table_count(conn: sqlite3.Connection, table: str) -> int:
    try:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
    except Exception:
        return 0


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def _result_key(item: dict[str, Any]) -> str:
    return "::".join([
        str(item.get("table") or item.get("source_table") or "row"),
        str(item.get("result_id") or item.get("source_id") or item.get("index_id") or item.get("title") or ""),
    ])


def _normalize_result(item: dict[str, Any], *, lane: str) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    source_table = item.get("table") or item.get("source_table") or payload.get("source_table") or ""
    source_id = item.get("source_id") or payload.get("source_id") or item.get("result_id") or ""
    title = item.get("title") or payload.get("title") or source_id or "Untitled memory"
    content = str(item.get("content") or payload.get("content") or "")
    return {
        "result_id": str(item.get("result_id") or source_id or _result_key(item)),
        "index_id": str(item.get("index_id") or payload.get("index_id") or ""),
        "table": str(source_table),
        "title": str(title),
        "content": content[:1600],
        "scope_id": str(item.get("scope_id") or payload.get("scope_id") or ""),
        "source_id": str(source_id),
        "status": str(item.get("status") or payload.get("status") or "active"),
        "score": float(item.get("score") or 0.0),
        "vector_score": item.get("vector_score"),
        "rerank_score": item.get("rerank_score"),
        "lane": lane,
        "payload": payload or item.get("payload") or {},
    }


def _combine_candidates(keyword_results: list[dict[str, Any]], semantic_results: list[dict[str, Any]], *, limit: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    combined: dict[str, dict[str, Any]] = {}
    raw: list[dict[str, Any]] = []
    for lane, rows in (("keyword", keyword_results), ("semantic", semantic_results)):
        for row in rows:
            normalized = _normalize_result(row, lane=lane)
            raw.append(normalized)
            key = _result_key(normalized)
            existing = combined.get(key)
            if not existing:
                normalized["lanes"] = [lane]
                normalized["keyword_score"] = normalized["score"] if lane == "keyword" else 0.0
                normalized["semantic_score"] = normalized["score"] if lane == "semantic" else 0.0
                combined[key] = normalized
            else:
                if lane not in existing.get("lanes", []):
                    existing.setdefault("lanes", []).append(lane)
                existing["keyword_score"] = max(float(existing.get("keyword_score") or 0.0), normalized["score"] if lane == "keyword" else 0.0)
                existing["semantic_score"] = max(float(existing.get("semantic_score") or 0.0), normalized["score"] if lane == "semantic" else 0.0)
                if len(normalized.get("content") or "") > len(existing.get("content") or ""):
                    existing["content"] = normalized.get("content") or existing.get("content")
    merged: list[dict[str, Any]] = []
    for item in combined.values():
        keyword_score = float(item.get("keyword_score") or 0.0)
        semantic_score = float(item.get("semantic_score") or 0.0)
        lane_bonus = 0.08 if len(item.get("lanes") or []) > 1 else 0.0
        item["combined_score"] = round((semantic_score * 0.68) + (keyword_score * 0.32) + lane_bonus, 6)
        item["score"] = item["combined_score"]
        merged.append(item)
    merged = sorted(merged, key=lambda row: float(row.get("combined_score") or row.get("score") or 0.0), reverse=True)
    return raw[: max(limit * 3, limit)], merged[: max(limit * 2, limit)]


def _write_runtime_trace(*, query: str, scope_id: str, mode: str, candidates: list[dict[str, Any]], results: list[dict[str, Any]], diagnostics: dict[str, Any]) -> dict[str, Any]:
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    now = _now()
    trace_id = f"runtime-trace-{now.replace(':', '').replace('.', '-')}"
    engine_snapshot = {
        "phase": "phase10_runtime_retrieval_lane",
        "mode": mode,
        "candidate_count": len(candidates),
        "result_count": len(results),
        "diagnostics": diagnostics,
    }
    with _connect() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(rp_retrieval_traces)").fetchall()}
        if "trace_type" in columns:
            conn.execute(
                """
                INSERT INTO rp_retrieval_traces(trace_id, trace_type, query, scope_id, result_count, engine_snapshot_json, results_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trace_id, "runtime_retrieval", query, scope_id, len(results), _json(engine_snapshot), _json(results), "runtime_retrieval", now),
            )
        else:
            conn.execute(
                """
                INSERT INTO rp_retrieval_traces(trace_id, query, scope_id, result_count, engine_snapshot_json, results_json, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (trace_id, query, scope_id, len(results), _json(engine_snapshot), _json(results), "runtime_retrieval", now),
            )
        conn.commit()
    return {"trace_id": trace_id, "created_at": now, "engine_snapshot": engine_snapshot}


def runtime_retrieval_lane_contract_payload(*, write_report: bool = False) -> dict[str, Any]:
    payload = {
        "schema_id": PHASE10_SCHEMA_ID,
        "version": PHASE10_VERSION,
        "status": "active",
        "purpose": "Runtime retrieval lane for previewing exactly which memory rows can feed Scene packets before Scene Chat generation.",
        "endpoints": {
            "contract": "/api/roleplay/runtime-retrieval/contract",
            "state": "/api/roleplay/runtime-retrieval/state",
            "run": "/api/roleplay/runtime-retrieval/run",
        },
        "pipeline": [
            "optional rebuild search documents",
            "keyword candidate retrieval",
            "semantic/vector candidate retrieval",
            "candidate merge",
            "optional rerank",
            "runtime retrieval trace write",
            "UI diagnostics / source proof",
        ],
        "candidate_shape": ["title", "table", "source_id", "scope_id", "score", "lanes", "retrieval_label", "scene_category", "why_selected", "proof_flags", "payload"],
        "locked_rules": [
            "Runtime retrieval must be scoped by sandbox/scope when supplied.",
            "Keyword retrieval remains available even if embeddings, reranker, or Chroma are offline.",
            "Scene Chat should consume a scene packet later, not invisible ad-hoc memory.",
            "Every runtime retrieval run writes a trace row for provenance.",
        ],
    }
    if write_report:
        _write_json(PHASE10_CONTRACT_PATH, payload)
    return payload


def runtime_retrieval_lane_state_payload(*, write_report: bool = False) -> dict[str, Any]:
    ensure_roleplay_sqlite_upgrade_schema(rebuild_search=False)
    with _connect() as conn:
        counts = {
            "entities": _table_count(conn, "rp_entities"),
            "memory_fragments": _table_count(conn, "rp_memory_fragments"),
            "search_documents": _table_count(conn, "rp_memory_search_documents"),
            "vector_index": _table_count(conn, "rp_vector_index"),
            "retrieval_traces": _table_count(conn, "rp_retrieval_traces"),
            "canon_records": _table_count(conn, "rp_canon_records"),
            "source_chunks": _table_count(conn, "rp_source_chunks"),
        }
    retrieval = roleplay_retrieval_state_payload()
    engine = roleplay_engine_bridge_state()
    payload = {
        "schema_id": "neo.roleplay.runtime_retrieval_lane.state.v1",
        "version": PHASE10_VERSION,
        "status": "active",
        "ready": counts["search_documents"] > 0 or counts["memory_fragments"] > 0 or counts["entities"] > 0,
        "sqlite_path": _relative_to_root(ROLEPLAY_SQLITE_PATH),
        "counts": counts,
        "retrieval": retrieval,
        "admin_engine_bridge": {
            "embedding_provider": ((engine.get("embedding_profiles") or {}).get("active_provider_id") or "fallback"),
            "reranker_provider": ((engine.get("reranker_profiles") or {}).get("active_provider_id") or "fallback"),
            "vector_store_ready": bool(engine.get("vector_store_ready")),
            "retrieval_defaults_ready": bool(engine.get("retrieval_defaults_ready")),
        },
        "ui": {
            "primary_action": "Run runtime retrieval",
            "diagnostics": ["candidate counts", "pre-rerank rows", "reranked shortlist", "trace id", "source proof"],
        },
    }
    if write_report:
        _write_json(PHASE10_STATE_PATH, payload)
    return payload


def run_runtime_retrieval_payload(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    scope_id = str(payload.get("scope_id") or payload.get("scope") or "").strip()
    mode = str(payload.get("mode") or "hybrid").strip().lower() or "hybrid"
    limit = _safe_int(payload.get("limit"), default=8, maximum=24)
    # Keep candidate discovery broad enough, but do not let Runtime packet builds send huge batches to the reranker.
    candidate_limit = _safe_int(payload.get("candidate_limit"), default=max(limit * 2, 16), maximum=48)
    rerank_candidate_limit = _safe_int(payload.get("rerank_candidate_limit"), default=min(max(limit, 6), 10), maximum=16)
    rerank = bool(payload.get("rerank", True))
    rebuild_search = bool(payload.get("rebuild_search", False))
    memory_types = payload.get("memory_types") or "entities,memory_fragments,shared_memories,continuity,turn_summaries,story_checkpoints"
    if isinstance(memory_types, str):
        memory_types = [part.strip() for part in memory_types.split(",") if part.strip()]

    if rebuild_search:
        rebuild_roleplay_memory_search_documents(limit=20000)

    keyword: dict[str, Any] = {"search": {"results": []}}
    semantic: dict[str, Any] = {"search": {"results": []}}
    if mode in {"keyword", "hybrid", "auto"}:
        keyword = search_retrieval_foundation_payload({
            "query": query,
            "scope_id": scope_id,
            "memory_types": memory_types,
            "limit": candidate_limit,
            "source": "runtime_retrieval_keyword",
        })
    if mode in {"semantic", "vector", "hybrid", "auto"}:
        semantic = search_retrieval_semantic_payload({
            "query": query,
            "scope_id": scope_id,
            "limit": candidate_limit,
            "rerank": False,
            "fallback_keyword": False,
            "source": "runtime_retrieval_semantic",
        })

    keyword_results = ((keyword.get("search") or {}).get("results") or []) if isinstance(keyword, dict) else []
    semantic_results = ((semantic.get("search") or {}).get("results") or []) if isinstance(semantic, dict) else []
    raw_candidates, merged = _combine_candidates(keyword_results, semantic_results, limit=limit)

    reranker_engine: dict[str, Any] = {"mode": "disabled"}
    final_results = merged[:limit]
    if rerank and merged:
        engine_state = roleplay_engine_bridge_state()
        # Inject runtime-safe reranker caps into the Admin-owned config snapshot for this request only.
        reranker_profiles = dict((engine_state.get("reranker_profiles") or {}))
        reranker_profiles.setdefault("max_candidates_per_request", rerank_candidate_limit)
        reranker_profiles.setdefault("candidate_text_limit", 900)
        engine_state = dict(engine_state, reranker_profiles=reranker_profiles)
        reranked = rerank_results(query, merged, engine_state=engine_state, top_n=limit, allow_fallback=True)
        final_results = reranked.get("results") or final_results
        reranker_engine = {k: v for k, v in reranked.items() if k != "results"}

    diagnostics = {
        "mode": mode,
        "scope_id": scope_id,
        "limit": limit,
        "candidate_limit": candidate_limit,
        "rerank_candidate_limit": rerank_candidate_limit,
        "keyword_candidate_count": len(keyword_results),
        "semantic_candidate_count": len(semantic_results),
        "merged_candidate_count": len(merged),
        "final_result_count": len(final_results),
        "rerank": rerank,
        "reranker_engine": reranker_engine,
        "semantic_engine": (semantic.get("search") or {}).get("embedding_engine") or {},
        "keyword_trace_id": (keyword.get("search") or {}).get("trace_id") or "",
        "semantic_trace_id": (semantic.get("search") or {}).get("trace_id") or "",
    }
    raw_candidates = label_runtime_retrieval_rows(raw_candidates)
    merged = label_runtime_retrieval_rows(merged)
    final_results = label_runtime_retrieval_rows(final_results)
    diagnostics["label_proof"] = {"enabled": True, "labeled_results": len(final_results)}
    trace = _write_runtime_trace(query=query, scope_id=scope_id, mode=mode, candidates=raw_candidates, results=final_results, diagnostics=diagnostics)
    diagnostics["label_proof"] = write_retrieval_label_proofs(trace_id=trace["trace_id"], query=query, results=final_results)
    result = {
        "schema_id": "neo.roleplay.runtime_retrieval_lane.run.v1",
        "version": PHASE10_VERSION,
        "status": "searched",
        "trace_id": trace["trace_id"],
        "query": query,
        "scope_id": scope_id,
        "mode": mode,
        "candidate_count": len(raw_candidates),
        "result_count": len(final_results),
        "candidates_before_rerank": raw_candidates[:candidate_limit],
        "merged_candidates": merged[:candidate_limit],
        "results": final_results,
        "diagnostics": diagnostics,
        "created_at": trace["created_at"],
    }
    return {
        "schema_id": "neo.roleplay.runtime_retrieval_lane.response.v1",
        "status": "searched",
        "search": result,
        "runtime_retrieval": runtime_retrieval_lane_state_payload(write_report=True),
    }
