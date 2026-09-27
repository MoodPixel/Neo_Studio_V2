from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

from neo_app.context_identity import memory_filter_from_payload, resolve_canonical_identity
from neo_app.memory.retrieval_profiles import get_retrieval_profile
from neo_app.memory.scope_priority import build_scope_priority_plan
from neo_app.memory.retrieval_planner import UnifiedRetrievalPlanner, RETRIEVAL_PLANNER_PHASE

ROOT_DIR = Path(__file__).resolve().parents[2]
DEFAULT_DB_PATH = ROOT_DIR / "neo_data" / "memory" / "global" / "neo_memory.sqlite3"

RETRIEVAL_GATEWAY_SCHEMA_ID = "neo.memory.retrieval_gateway.v1"
RETRIEVAL_GATEWAY_PHASE = "NKB-8"

# Phase 6 keeps experiential/project/surface memory in Unified M9, but routes it
# through query-driven scope-priority targets. Static knowledge remains separate.
# Phase 5 established the single gateway and reserves
# the legacy document/chunk index for durable/static knowledge. Assistant memory,
# prompt libraries, and Project Workspace remain compatibility sources elsewhere
# until their owning migration phases; they are deliberately not queried here.
STATIC_KNOWLEDGE_SOURCES = frozenset({
    "system_records",
    "neo_codebase",
    "extension_manifests",
    "admin_config",
    "surface_blueprints",
    "memory_consolidation",
})

_CODE_QUERY_TERMS = (
    "code", "repo", "file", "route", "function", "class", "test", "css",
    "javascript", "python", "where is", "which file", "implementation",
)
_ADMIN_QUERY_TERMS = (
    "admin", "memory engine", "backend", "provider", "extension", "surface",
    "control center", "health", "configuration", "config", "registry",
)


def _clean(value: Any, *, limit: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _score(value: Any, default: float = 0.0) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except Exception:
        return default


def _fingerprint(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    return hashlib.sha256(normalized[:2400].encode("utf-8", errors="ignore")).hexdigest() if normalized else ""


def _query_terms(value: str) -> set[str]:
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "what", "how",
        "can", "you", "help", "please", "about", "into", "does", "did", "use",
        "our", "neo", "studio", "assistant",
    }
    return {
        token for token in re.findall(r"[a-z0-9_+-]{3,}", str(value or "").lower())
        if token not in stop
    }


def _resolve_knowledge_profile(requested_profile: str, query: str) -> str:
    requested = str(requested_profile or "smart").strip().lower() or "smart"
    text = str(query or "").lower()
    if requested == "fast":
        return "fast"
    if any(term in text for term in _CODE_QUERY_TERMS):
        return "code_audit"
    if any(term in text for term in _ADMIN_QUERY_TERMS):
        return "admin_diagnostic"
    if requested == "deep":
        return "deep"
    return "assistant_project"


def _knowledge_sources(profile_id: str, explicit: list[str] | None = None) -> list[str]:
    if explicit:
        return [str(item) for item in explicit if str(item) in STATIC_KNOWLEDGE_SOURCES]
    profile = get_retrieval_profile(profile_id)
    sources = [str(item) for item in profile.get("sources") or [] if str(item) in STATIC_KNOWLEDGE_SOURCES]
    if "system_records" not in sources:
        sources.insert(0, "system_records")
    return list(dict.fromkeys(sources))


def _normalize_unified_item(item: dict[str, Any], *, target: dict[str, Any] | None = None) -> dict[str, Any]:
    content = _clean(item.get("content") or item.get("summary") or item.get("snippet"), limit=2200)
    fragment_id = str(item.get("fragment_id") or "")
    target = target if isinstance(target, dict) else {}
    metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
    evidence = metadata.get("evidence") if isinstance(metadata.get("evidence"), dict) else {}
    native_ref = evidence.get("native_ref") if isinstance(evidence.get("native_ref"), dict) else {}
    source_locator = evidence.get("source_locator") if isinstance(evidence.get("source_locator"), dict) else {}
    evidence_provenance = evidence.get("provenance") if isinstance(evidence.get("provenance"), dict) else {}
    evidence_conflict = evidence.get("conflict") if isinstance(evidence.get("conflict"), dict) else {}
    evidence_semantic = evidence.get("semantic") if isinstance(evidence.get("semantic"), dict) else {}
    base_score = _score(item.get("score"), 0.0)
    target_priority = _score(target.get("priority"), 1.0)
    # Scope affects priority, not eligibility. Keep query relevance dominant while
    # giving the active scope/project a modest, bounded preference.
    blended_score = min(1.0, (base_score * 0.9) + (target_priority * 0.1))
    source_path = str(source_locator.get("path") or metadata.get("stored_path") or "")
    return {
        "item_id": fragment_id or f"unified:{_fingerprint(content)[:16]}",
        "source_lane": "unified_memory",
        "kind": "memory",
        "surface": str(item.get("surface") or "global"),
        "project_id": str(item.get("project_id") or ""),
        "scope_id": str(item.get("scope_id") or ""),
        "source_type": str(item.get("source_type") or "memory"),
        "source_id": str(item.get("source_id") or fragment_id or "unified_memory"),
        "memory_type": str(item.get("memory_type") or "fragment"),
        "title": _clean(item.get("title") or "Memory fragment", limit=220),
        "content": content,
        "snippet": _clean(item.get("snippet") or content, limit=700),
        "score": round(blended_score, 6),
        "base_score": base_score,
        "scope_priority": target_priority,
        "retrieval_type": str(item.get("retrieval_type") or "memory"),
        "trust_level": str(item.get("trust_level") or ""),
        "memory_state": "active",
        "approval_state": "",
        "citation": {
            "chunk_id": "",
            "source_id": str(item.get("source_id") or fragment_id or ""),
            "source_path": source_path,
            "start_line": source_locator.get("start_line"),
            "end_line": source_locator.get("end_line"),
            "label": str(source_locator.get("label") or metadata.get("filename") or source_path or ""),
            "viewer_endpoint": "",
        } if source_path else {},
        "metadata": {
            **metadata,
            "scope_priority_target": target.get("target_id") or "",
            "scope_priority_reason": target.get("reason") or "",
            "native_ref": native_ref,
        },
        "provenance": {
            "adapter": str(native_ref.get("adapter_id") or "unified_memory"),
            "fragment_id": fragment_id,
            "source_id": str(item.get("source_id") or ""),
            "native_ref": native_ref,
            "scope_priority_target": target.get("target_id") or "",
            "scope_priority_reason": target.get("reason") or "",
            "target_surface": target.get("surface") or "",
            "target_project_id": target.get("project_id") or "",
            "target_scope_id": target.get("scope_id") or "",
        },
        "authority": {
            "evidence_role": str(metadata.get("evidence_role") or evidence_semantic.get("evidence_role") or "experiential_memory"),
            "lifecycle": str(item.get("status") or "active"),
            "source_integrity": str(evidence_provenance.get("source_integrity") or "present"),
            "conflict": str(evidence_conflict.get("state") or "none"),
        },
    }


def _normalize_knowledge_item(item: dict[str, Any]) -> dict[str, Any]:
    citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
    content = _clean(item.get("content") or item.get("snippet") or item.get("summary"), limit=2200)
    chunk_id = str(item.get("chunk_id") or citation.get("chunk_id") or "")
    source_path = str(item.get("source_path") or citation.get("source_path") or "")
    source_id = str(item.get("source_id") or citation.get("source_id") or "knowledge_index")
    start_line = item.get("start_line") or citation.get("start_line")
    end_line = item.get("end_line") or citation.get("end_line")
    native_ref: dict[str, Any] = {}
    if source_path and source_id == "neo_codebase":
        native_ref = {
            "adapter_id": "neo.code",
            "authority_namespace": "repository.test" if source_path.replace("\\", "/").startswith("tests/") else "repository.file",
            "native_id": f"{source_path}::L{start_line}-{end_line}" if start_line else source_path,
            "resolver_kind": "repository_range" if start_line else "file",
            "resolver_key": {"path": source_path, "start_line": start_line or 0, "end_line": end_line or 0},
        }
    elif source_path and source_id == "system_records":
        native_ref = {
            "adapter_id": "neo.docs",
            "authority_namespace": "system_record.section" if start_line else "system_record.document",
            "native_id": f"{source_path}#L{start_line}-L{end_line}" if start_line else source_path,
            "resolver_kind": "repository_range" if start_line else "file",
            "resolver_key": {"path": source_path, "start_line": start_line or 0, "end_line": end_line or 0},
        }
    return {
        "item_id": chunk_id or f"knowledge:{_fingerprint(source_path + content)[:16]}",
        "source_lane": "knowledge_index",
        "kind": "knowledge",
        "surface": "global",
        "project_id": "",
        "scope_id": "",
        "source_type": str(item.get("source_type") or "indexed_document"),
        "source_id": source_id,
        "memory_type": "knowledge_chunk",
        "title": _clean(item.get("title") or citation.get("title") or source_path or "Knowledge source", limit=220),
        "content": content,
        "snippet": _clean(item.get("snippet") or content, limit=700),
        "score": _score(item.get("score"), 0.0),
        "retrieval_type": str(item.get("retrieval_type") or "knowledge"),
        "trust_level": str(item.get("trust_level") or citation.get("trust_level") or "confirmed"),
        "memory_state": str(item.get("memory_state") or citation.get("memory_state") or "active"),
        "approval_state": str(item.get("approval_state") or citation.get("approval_state") or "approved"),
        "citation": {
            "chunk_id": chunk_id,
            "source_id": str(item.get("source_id") or citation.get("source_id") or ""),
            "source_path": source_path,
            "start_line": start_line,
            "end_line": end_line,
            "label": str(citation.get("label") or source_path or chunk_id),
            "viewer_endpoint": str(item.get("viewer_endpoint") or citation.get("viewer_endpoint") or ""),
        },
        "metadata": {"native_ref": native_ref} if native_ref else {},
        "provenance": {
            "adapter": "knowledge_index",
            "chunk_id": chunk_id,
            "source_path": source_path,
            "native_ref": native_ref,
        },
        "authority": {
            "evidence_role": ("validation_test" if source_path.replace("\\", "/").startswith("tests/") else "runtime_implementation" if str(item.get("source_id") or "") == "neo_codebase" else "developer_reference"),
            "lifecycle": str(item.get("memory_state") or citation.get("memory_state") or "active"),
            "source_integrity": "present",
            "conflict": "none",
        },
    }


def _guide_relevant(guide: dict[str, Any], query: str, surface: str) -> bool:
    query_terms = _query_terms(query)
    guide_text = " ".join([
        str(guide.get("title") or ""),
        " ".join(str(item) for item in guide.get("tags") or []),
        " ".join(str(item) for item in guide.get("applies_to") or []),
        str(guide.get("excerpt") or "")[:1600],
    ])
    guide_terms = _query_terms(guide_text)
    if query_terms and query_terms & guide_terms:
        return True
    guide_surface = str(guide.get("surface") or "global")
    return bool(surface not in {"", "global", "assistant"} and guide_surface in {surface, "global"})


def _normalize_guide_item(guide: dict[str, Any], *, rank: int) -> dict[str, Any]:
    path = str(guide.get("path") or "")
    guide_id = str(guide.get("guide_id") or path or f"guide_{rank}")
    content = _clean(guide.get("excerpt"), limit=1800)
    # search_guides already ranks by scope/term relevance. Convert position to a
    # bounded score so it can share one shortlist with other gateway adapters.
    score = max(0.52, 0.88 - ((rank - 1) * 0.04))
    return {
        "item_id": f"guide:{guide_id}",
        "source_lane": "guide_index",
        "kind": "guide",
        "surface": str(guide.get("surface") or "global"),
        "project_id": "",
        "scope_id": "",
        "source_type": "neo_guide",
        "source_id": guide_id,
        "memory_type": "guide",
        "title": _clean(guide.get("title") or guide_id, limit=220),
        "content": content,
        "snippet": _clean(content, limit=700),
        "score": round(score, 6),
        "retrieval_type": "guide_rank",
        "trust_level": "confirmed",
        "memory_state": "active",
        "approval_state": "approved",
        "citation": {
            "chunk_id": "",
            "source_id": guide_id,
            "source_path": path,
            "start_line": None,
            "end_line": None,
            "label": path or guide_id,
            "viewer_endpoint": "",
        },
        "metadata": {
            "tags": list(guide.get("tags") or []),
            "applies_to": list(guide.get("applies_to") or []),
            "version": guide.get("version"),
        },
        "provenance": {"adapter": "guide_index", "guide_id": guide_id, "path": path},
        "authority": {"evidence_role": "current_guide", "lifecycle": str(guide.get("status") or "active"), "source_integrity": "present", "conflict": "none"},
    }


def _dedupe_rank(items: list[dict[str, Any]], *, limit: int) -> tuple[list[dict[str, Any]], int]:
    """Deduplicate one candidate pool, then apply a small adapter-balance floor.

    Phase 5 merges independent retrieval authorities. A Guide with a synthetic
    rank score must not accidentally crowd a directly matched Unified Memory row
    out of a normal Assistant context window (and vice versa). When the context
    limit can hold all active lanes, reserve one best unique item per lane, then
    fill the remaining slots by global score. This is adapter balance only; query-
    driven cross-scope expansion remains Phase 6.
    """
    ranked = sorted(
        [item for item in items if isinstance(item, dict) and _clean(item.get("content"))],
        key=lambda item: (_score(item.get("score")), 1 if item.get("source_lane") == "unified_memory" else 0),
        reverse=True,
    )

    unique: list[dict[str, Any]] = []
    fingerprints: dict[str, int] = {}
    dropped = 0
    for item in ranked:
        fp = _fingerprint(item.get("content") or item.get("snippet") or "")
        if not fp:
            continue
        if fp in fingerprints:
            existing = unique[fingerprints[fp]]
            # Phase 6 can retrieve the same fragment through more than one approved
            # target. That is a routing duplicate, not a second content duplicate.
            # Keep Phase 5 duplicate accounting stable for genuinely distinct items.
            if str(existing.get("item_id") or "") != str(item.get("item_id") or ""):
                dropped += 1
            lanes = list(existing.get("provenance_lanes") or [existing.get("source_lane")])
            if item.get("source_lane") not in lanes:
                lanes.append(item.get("source_lane"))
            existing["provenance_lanes"] = lanes
            target_ids = list(existing.get("scope_priority_targets") or [])
            for candidate in (
                (existing.get("provenance") or {}).get("scope_priority_target"),
                (item.get("provenance") or {}).get("scope_priority_target"),
            ):
                if candidate and candidate not in target_ids:
                    target_ids.append(candidate)
            if target_ids:
                existing["scope_priority_targets"] = target_ids
            # Prefer a citation-bearing duplicate without changing the selected text.
            if not (existing.get("citation") or {}).get("source_path") and (item.get("citation") or {}).get("source_path"):
                existing["citation"] = item.get("citation")
            continue
        fingerprints[fp] = len(unique)
        unique.append({**item, "provenance_lanes": [item.get("source_lane")]})

    if len(unique) <= limit:
        return unique, dropped

    active_lanes = [
        lane for lane in ("unified_memory", "knowledge_index", "guide_index")
        if any(item.get("source_lane") == lane for item in unique)
    ]
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    if limit >= len(active_lanes) and len(active_lanes) > 1:
        for lane in active_lanes:
            best = next((item for item in unique if item.get("source_lane") == lane), None)
            if best is not None:
                selected.append(best)
                selected_ids.add(str(best.get("item_id") or id(best)))

    for item in unique:
        key = str(item.get("item_id") or id(item))
        if key in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(key)
        if len(selected) >= limit:
            break

    selected = selected[:limit]
    selected.sort(
        key=lambda item: (_score(item.get("score")), 1 if item.get("source_lane") == "unified_memory" else 0),
        reverse=True,
    )
    return selected, dropped


def _evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for item in items:
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        if not (citation.get("source_path") or citation.get("chunk_id") or item.get("kind") == "guide"):
            continue
        index = len(evidence) + 1
        citation["index"] = index
        item["citation"] = citation
        evidence.append({
            "index": index,
            "item_id": item.get("item_id"),
            "kind": item.get("kind"),
            "title": item.get("title") or "Source",
            "source_id": citation.get("source_id") or item.get("source_id") or "",
            "source_path": citation.get("source_path") or "",
            "start_line": citation.get("start_line"),
            "end_line": citation.get("end_line"),
            "citation_label": citation.get("label") or citation.get("source_path") or item.get("source_id") or "",
            "viewer_endpoint": citation.get("viewer_endpoint") or "",
            "score": item.get("score"),
            "trust_level": item.get("trust_level") or "",
            "memory_state": item.get("memory_state") or "active",
            "approval_state": item.get("approval_state") or "",
            "snippet": _clean(item.get("snippet") or item.get("content"), limit=1100),
        })
    return evidence


def render_gateway_context(result: dict[str, Any], *, limit_chars: int = 9000) -> str:
    rows: list[str] = []
    for item in result.get("items") or []:
        if not isinstance(item, dict):
            continue
        lane = str(item.get("source_lane") or "context")
        score = item.get("score")
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        cite = f" [{citation.get('index')}]" if citation.get("index") else ""
        score_hint = f" · {float(score):.3f}" if isinstance(score, (int, float)) else ""
        source_hint = citation.get("source_path") or item.get("source_id") or ""
        where = f" · {source_hint}" if source_hint else ""
        rows.append(
            f"- [{lane}{score_hint}]{cite} {item.get('title') or 'Context'}{where}: "
            f"{_clean(item.get('content') or item.get('snippet'), limit=1400)}"
        )
        if sum(len(row) for row in rows) >= limit_chars:
            break
    return _clean("\n".join(rows), limit=limit_chars)


def gateway_grounding_payload(result: dict[str, Any], *, question: str = "") -> dict[str, Any]:
    evidence = list(result.get("evidence") or [])
    lines: list[str] = []
    for item in evidence:
        label = item.get("citation_label") or item.get("source_path") or item.get("source_id") or "source"
        line = f"[{item.get('index')}] {item.get('title') or 'Source'} — {label}"
        if item.get("snippet"):
            line += f"\n    {_clean(item.get('snippet'), limit=650)}"
        lines.append(line)
    return {
        "ok": True,
        "schema_id": "neo.assistant.source_grounded_answer.v2",
        "status": "ready" if evidence else "insufficient_evidence",
        "question": str(question or result.get("query") or ""),
        "requested_profile": result.get("profile") or "smart",
        "memory_engine_profile": result.get("adapters", {}).get("knowledge_index", {}).get("profile") or "",
        "trace_id": result.get("gateway_trace_id") or "",
        "backend_used": "retrieval_gateway",
        "evidence_count": len(evidence),
        "confidence": "grounded" if evidence else "insufficient_evidence",
        "grounding_policy": "Source grounding is projected from the single Retrieval Gateway result; Phase 6 scope expansion does not execute a second independent Assistant search path.",
        "instructions": [
            "Use cited static knowledge for factual Neo/code/system claims.",
            "Do not invent missing memory when the gateway returned no relevant item.",
        ],
        "evidence": evidence,
        "evidence_block": "\n".join(lines) if lines else "No cited evidence retrieved.",
        "answer_scaffold": "Use the retrieved evidence directly and cite bracket numbers when factual source attribution helps." if evidence else "No cited static evidence was retrieved.",
        "search_result": {
            "schema_id": RETRIEVAL_GATEWAY_SCHEMA_ID,
            "gateway_trace_id": result.get("gateway_trace_id") or "",
            "counts": result.get("counts") or {},
            "adapters": result.get("adapters") or {},
        },
    }


class RetrievalGateway:
    """Single Assistant retrieval boundary introduced in Phase 5.

    The gateway does not replace storage engines. It orchestrates two existing
    authorities behind one result contract:
      * Unified M9 -> experiential/project/surface memory
      * Knowledge index -> source-backed static Neo records/code/config

    Built-in Neo Guides are a lightweight knowledge adapter and join the same
    ranked/deduplicated result. Control Center remains the final selector.
    """

    def __init__(self, db_path: Path = DEFAULT_DB_PATH, root_dir: Path = ROOT_DIR) -> None:
        self.db_path = Path(db_path)
        self.root_dir = Path(root_dir).resolve()
        self.planner = UnifiedRetrievalPlanner(self.db_path, self.root_dir)

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "schema_id": RETRIEVAL_GATEWAY_SCHEMA_ID,
            "phase": RETRIEVAL_GATEWAY_PHASE,
            "status": "ready",
            "adapters": ["project_structured", "unified_memory", "native_authority", "knowledge_index", "guide_index"],
            "static_knowledge_sources": sorted(STATIC_KNOWLEDGE_SOURCES),
            "retrieval_planner": self.planner.status(),
            "policy": "Assistant retrieval uses the NKB-8 Unified Retrieval Planner to select lanes, fuse ranks, rerank, adjudicate authority, and hydrate strong evidence before NKB-9 packet assembly.",
        }

    def retrieve(self, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = dict(payload or {})
        query = str(data.get("query") or data.get("user_input") or data.get("message") or "").strip()
        requested_profile = str(data.get("profile") or data.get("retrieval_profile") or "smart").strip().lower() or "smart"
        limit = max(1, min(int(data.get("limit") or 10), 40))
        include_unified = data.get("include_unified", True) is not False
        include_knowledge = data.get("include_knowledge", True) is not False
        include_guides = data.get("include_guides", True) is not False
        consumer = str(data.get("consumer") or "assistant_retrieval_gateway")

        # Historical Assistant APIs call the active Assistant Scope ``project_id``.
        # Treat that field as a Scope at the Assistant retrieval boundary even
        # when a caller forgot to duplicate it into ``legacy_project_id``. True
        # Delivery Projects travel separately as delivery_project_id/identity.
        assistant_scope_boundary = bool(
            data.get("legacy_project_id")
            or data.get("scope_id")
            or str(consumer or "").startswith("assistant")
            or str(data.get("controller") or "").lower() == "assistant"
        )
        identity = resolve_canonical_identity(
            data,
            legacy_project_is_scope=assistant_scope_boundary,
            source="retrieval_gateway",
        )
        memory_filter = memory_filter_from_payload(
            {**data, "identity": identity.as_dict()},
            legacy_project_is_scope=assistant_scope_boundary,
        )
        planner_query = str(data.get("planner_query") or query).strip()
        planner_plan = self.planner.analyze({**data, "identity": identity.as_dict(), "query": planner_query, "retrieval_profile": requested_profile})
        retrieval_query = str(planner_plan.get("retrieval_query") or query).strip() or query
        planned_lanes = planner_plan.get("lanes") if isinstance(planner_plan.get("lanes"), dict) else {}
        # Explicit caller source restrictions remain authoritative (for example
        # the compatibility source-grounded route can request System Records only).
        if data.get("include_project_structured") is False:
            planned_lanes["project_structured"] = False
            (planner_plan.get("lane_weights") or {})["project_structured"] = 0.0
        if data.get("include_native") is False:
            planned_lanes["native_authority"] = False
            (planner_plan.get("lane_weights") or {})["native_authority"] = 0.0
        if "include_unified" not in data:
            include_unified = bool(planned_lanes.get("unified_memory", True))
        if "include_knowledge" not in data:
            include_knowledge = bool(planned_lanes.get("knowledge_index", True))
        if "include_guides" not in data:
            include_guides = bool(planned_lanes.get("guide_index", True))
        adapter_limit = max(limit, min(limit * 3, 36))
        adapters: dict[str, Any] = {}
        normalized: list[dict[str, Any]] = []

        scope_policy = build_scope_priority_plan(query, identity, db_path=self.db_path)

        if include_unified:
            try:
                from neo_app.memory.retrieval_engine import UnifiedMemoryRetrievalEngine

                engine = UnifiedMemoryRetrievalEngine(self.db_path)
                target_reports: list[dict[str, Any]] = []
                total_rows = 0
                for target in scope_policy.get("targets") or []:
                    if not isinstance(target, dict):
                        continue
                    unified_profile = "roleplay_runtime" if str(target.get("surface") or "") == "roleplay" else requested_profile
                    if unified_profile not in {"fast", "smart", "deep", "assistant_project", "roleplay_runtime", "creator_workflow", "code_audit", "admin_diagnostic"}:
                        unified_profile = "smart"
                    result = engine.retrieve({
                        "query": retrieval_query,
                        # Phase 6 target filters are already resolved by the scope-priority
                        # planner. Do not pass canonical identity here or M9 would translate
                        # them back into the active-scope storage filter.
                        "surface": target.get("surface") or "",
                        "project_id": target.get("project_id") or "",
                        "scope_id": target.get("scope_id") or "",
                        "profile": unified_profile,
                        "consumer": f"{consumer}:scope_priority:{target.get('target_id') or 'target'}",
                        "limit": adapter_limit,
                        "candidate_limit": max(adapter_limit * 4, 24),
                        "rerank_top": min(adapter_limit, 18),
                        "semantic": requested_profile != "fast",
                        "rerank": requested_profile != "fast",
                    })
                    rows = list(result.get("results") or [])
                    total_rows += len(rows)
                    normalized.extend(_normalize_unified_item(item, target=target) for item in rows if isinstance(item, dict))
                    target_reports.append({
                        "target_id": target.get("target_id") or "",
                        "surface": target.get("surface") or "",
                        "project_id": target.get("project_id") or "",
                        "scope_id": target.get("scope_id") or "",
                        "priority": target.get("priority"),
                        "reason": target.get("reason") or "",
                        "trace_id": result.get("trace_id") or "",
                        "backend_used": result.get("backend_used") or "",
                        "result_count": len(rows),
                        "stats": result.get("stats") or {},
                    })
                adapters["unified_memory"] = {
                    "ok": True,
                    "status": "ready" if target_reports else "no_targets",
                    "profile": requested_profile,
                    "trace_id": next((str(row.get("trace_id") or "") for row in target_reports if row.get("trace_id")), ""),
                    "backend_used": "scope_priority+m9",
                    "result_count": total_rows,
                    "target_count": len(target_reports),
                    "targets": target_reports,
                    "scope_policy": {
                        "schema_id": scope_policy.get("schema_id"),
                        "phase": scope_policy.get("phase"),
                        "expanded_surfaces": scope_policy.get("expanded_surfaces") or [],
                        "blocked_expansions": scope_policy.get("blocked_expansions") or [],
                    },
                }
            except Exception as exc:
                adapters["unified_memory"] = {"ok": False, "status": "error", "error": str(exc)[:600], "result_count": 0, "target_count": 0}
        else:
            adapters["unified_memory"] = {"ok": True, "status": "disabled", "result_count": 0, "target_count": 0}

        planner_intent = str(planner_plan.get("intent") or "")
        if planner_intent == "neo_development":
            knowledge_profile = "code_audit"
        elif planner_intent == "neo_admin_diagnostic":
            knowledge_profile = "admin_diagnostic"
        elif requested_profile == "fast":
            knowledge_profile = "fast"
        elif requested_profile == "deep":
            knowledge_profile = "deep"
        else:
            knowledge_profile = "assistant_project"
        sources = _knowledge_sources(knowledge_profile, data.get("knowledge_sources") if isinstance(data.get("knowledge_sources"), list) else None)
        if include_knowledge and query:
            try:
                # Lazy import avoids MemoryService <-> RetrievalGateway import cycles.
                from neo_app.memory.service import get_memory_service

                result = get_memory_service().search_ux({
                    "query": query,
                    "profile": knowledge_profile,
                    "consumer": consumer,
                    "limit": adapter_limit,
                    "semantic": requested_profile != "fast",
                    "sources": sources,
                })
                rows = list(result.get("results") or [])
                normalized.extend(_normalize_knowledge_item(item) for item in rows if isinstance(item, dict))
                adapters["knowledge_index"] = {
                    "ok": bool(result.get("ok", True)),
                    "status": result.get("status") or "ready",
                    "profile": result.get("profile") or knowledge_profile,
                    "trace_id": result.get("trace_id") or "",
                    "backend_used": result.get("backend_used") or "",
                    "sources": sources,
                    "result_count": len(rows),
                    "stats": result.get("stats") or {},
                }
            except Exception as exc:
                adapters["knowledge_index"] = {"ok": False, "status": "error", "error": str(exc)[:600], "sources": sources, "result_count": 0}
        else:
            adapters["knowledge_index"] = {"ok": True, "status": "disabled" if not include_knowledge else "no_query", "sources": sources, "result_count": 0}

        if include_guides and query:
            try:
                from neo_app.assistant.guides import search_guides

                guide_surfaces: list[str] = []
                for target in scope_policy.get("targets") or []:
                    surface = str((target or {}).get("canonical_surface") or (target or {}).get("surface") or "").strip()
                    if surface in {"", "assistant", "project"}:
                        surface = "global"
                    if surface not in guide_surfaces:
                        guide_surfaces.append(surface)
                if not guide_surfaces:
                    guide_surfaces = [identity.surface_id or "global"]
                guide_surfaces = guide_surfaces[:4]
                raw_guides: list[dict[str, Any]] = []
                seen_guides: set[str] = set()
                for guide_surface in guide_surfaces:
                    guide_payload = search_guides(
                        query,
                        surface=guide_surface,
                        project_id=identity.scope_id,
                        limit=min(adapter_limit, 12),
                    )
                    for guide in guide_payload.get("guides") or []:
                        if not isinstance(guide, dict) or not _guide_relevant(guide, query, guide_surface):
                            continue
                        gid = str(guide.get("guide_id") or guide.get("path") or _fingerprint(str(guide)))
                        if gid in seen_guides:
                            continue
                        seen_guides.add(gid)
                        raw_guides.append(guide)
                normalized.extend(_normalize_guide_item(guide, rank=index) for index, guide in enumerate(raw_guides[:adapter_limit], 1))
                adapters["guide_index"] = {
                    "ok": True,
                    "status": "ready",
                    "result_count": min(len(raw_guides), adapter_limit),
                    "candidate_count": len(raw_guides),
                    "surfaces": guide_surfaces,
                }
            except Exception as exc:
                adapters["guide_index"] = {"ok": False, "status": "error", "error": str(exc)[:600], "result_count": 0}
        else:
            adapters["guide_index"] = {"ok": True, "status": "disabled" if not include_guides else "no_query", "result_count": 0}

        project_candidates = self.planner.project_structured_candidates(planner_plan, limit=adapter_limit)
        native_candidates, native_reports = self.planner.native_candidates(planner_plan, limit=adapter_limit)
        normalized.extend(project_candidates)
        normalized.extend(native_candidates)
        adapters["project_structured"] = {"ok": True, "status": "ready" if (planner_plan.get("lanes") or {}).get("project_structured") else "disabled", "result_count": len(project_candidates)}
        adapters["native_authority"] = {"ok": True, "status": "ready" if (planner_plan.get("lanes") or {}).get("native_authority") else "disabled", "result_count": len(native_candidates), "adapters": native_reports}

        lane_items: dict[str, list[dict[str, Any]]] = {}
        for item in normalized:
            lane_items.setdefault(str(item.get("source_lane") or "unknown"), []).append(item)
        fused = self.planner.fuse(planner_plan, lane_items, limit=max(limit * 4, 24))
        reranked, reranker_status = self.planner.rerank(planner_plan, list(fused.get("items") or []), top_n=max(limit * 2, 12))
        adjudicated = self.planner.adjudicate(planner_plan, reranked, limit=limit)
        selected = list(adjudicated.get("items") or [])
        duplicates_removed = int(fused.get("duplicates_removed") or 0)
        evidence = _evidence(selected)
        gateway_trace_id = f"gateway_{uuid4().hex[:12]}"
        counts = {
            "result_count": len(selected),
            "candidate_count": len(normalized),
            "duplicates_removed": duplicates_removed,
            "project_structured": sum(1 for item in selected if item.get("source_lane") == "project_structured"),
            "unified_memory": sum(1 for item in selected if item.get("source_lane") == "unified_memory"),
            "native_authority": sum(1 for item in selected if item.get("source_lane") == "native_authority"),
            "knowledge_index": sum(1 for item in selected if item.get("source_lane") == "knowledge_index"),
            "guide_index": sum(1 for item in selected if item.get("source_lane") == "guide_index"),
            "evidence_count": len(evidence),
            "authority_rejected": len(adjudicated.get("rejected") or []),
        }
        adapter_errors = [name for name, state in adapters.items() if not state.get("ok", False)]
        return {
            "ok": bool(selected) or len(adapter_errors) < len(adapters),
            "schema_id": RETRIEVAL_GATEWAY_SCHEMA_ID,
            "phase": RETRIEVAL_GATEWAY_PHASE,
            "status": "ready" if selected else ("partial" if adapter_errors else "no_results"),
            "gateway_trace_id": gateway_trace_id,
            "query": query,
            "planner_query": planner_query,
            "profile": requested_profile,
            "identity": identity.as_dict(),
            "memory_filter": {k: memory_filter.get(k) for k in ("surface", "project_id", "scope_id")},
            "scope_policy": scope_policy,
            "retrieval_targets": list(scope_policy.get("targets") or []),
            "planner": planner_plan,
            "planner_trace_id": planner_plan.get("planner_trace_id") or "",
            "planner_phase": RETRIEVAL_PLANNER_PHASE,
            "known_state": adjudicated.get("known_state") or "not_established",
            "fail_closed_recommended": bool(adjudicated.get("fail_closed_recommended")),
            "fusion": {**fused, "items": [], "reranker": reranker_status},
            "authority_rejections": [{k: item.get(k) for k in ("item_id", "title", "source_lane", "authority_reasons", "score")} for item in (adjudicated.get("rejected") or [])[:20]],
            "items": selected,
            "evidence": evidence,
            "counts": counts,
            "adapters": adapters,
            "adapter_errors": adapter_errors,
            "policy": "NKB-8 plans evidence lanes by intent/claim type, fuses lane ranks with weighted RRF, reranks the fused shortlist, adjudicates task-sensitive authority, and hydrates strong native/Project evidence. NKB-9 owns final packet assembly.",
        }


def retrieval_gateway_status_payload() -> dict[str, Any]:
    return RetrievalGateway().status()


def retrieve_context(payload: dict[str, Any] | None = None, *, db_path: Path | None = None) -> dict[str, Any]:
    return RetrievalGateway(db_path or DEFAULT_DB_PATH).retrieve(payload or {})
