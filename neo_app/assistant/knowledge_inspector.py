from __future__ import annotations

from typing import Any

from neo_app.assistant.context_packet import CONTEXT_PACKET_SCHEMA_ID
from neo_app.assistant.store import get_session

KNOWLEDGE_INSPECTOR_SCHEMA_ID = "neo.assistant.knowledge_inspector.v1"
KNOWLEDGE_INSPECTOR_PHASE = "NKB-11"


def _clean(value: Any, limit: int = 1600) -> str:
    return str(value or "").strip()[:limit]


def _candidate_view(item: dict[str, Any] | None, *, selected: bool = False) -> dict[str, Any]:
    row = item if isinstance(item, dict) else {}
    citation = row.get("citation") if isinstance(row.get("citation"), dict) else {}
    authority = row.get("authority") if isinstance(row.get("authority"), dict) else {}
    hydration = row.get("hydration") if isinstance(row.get("hydration"), dict) else {}
    return {
        "item_id": _clean(row.get("item_id"), 240),
        "title": _clean(row.get("title") or row.get("source_id") or "Evidence", 260),
        "source_lane": _clean(row.get("source_lane") or "unknown", 80),
        "score": row.get("score"),
        "relevance_score": row.get("relevance_score"),
        "authority_multiplier": row.get("authority_multiplier"),
        "answerability_score": row.get("answerability_score"),
        "answerability_multiplier": row.get("answerability_multiplier"),
        "answerability_reasons": [str(v)[:180] for v in (row.get("answerability_reasons") or []) if str(v or "").strip()][:12],
        "answer_target": _clean(row.get("answer_target"), 160),
        "direct_definition": bool(row.get("direct_definition")),
        "entity_heading_match": bool(row.get("entity_heading_match")),
        "epistemic_role": _clean(row.get("epistemic_role") or "declarative_passage", 100),
        "epistemic_state": _clean(row.get("epistemic_state") or "established", 100),
        "supports_positive_claims": bool(row.get("supports_positive_claims", True)),
        "authority_status": _clean(row.get("authority_status") or ("accepted" if selected else "candidate"), 60),
        "authority_reasons": [str(v)[:180] for v in (row.get("authority_reasons") or []) if str(v or "").strip()][:12],
        "evidence_role": _clean(authority.get("evidence_role") or (row.get("provenance") or {}).get("evidence_role"), 100),
        "source_integrity": _clean(authority.get("source_integrity") or hydration.get("status") or "", 80),
        "lifecycle": _clean(authority.get("lifecycle") or "", 80),
        "conflict": _clean(authority.get("conflict") or "", 80),
        "fusion_lanes": [str(v)[:80] for v in (row.get("fusion_lanes") or row.get("provenance_lanes") or [])][:8],
        "lane_ranks": dict(row.get("lane_ranks") or {}) if isinstance(row.get("lane_ranks"), dict) else {},
        "raw_scores": dict(row.get("raw_scores") or {}) if isinstance(row.get("raw_scores"), dict) else {},
        "citation": {
            "index": citation.get("index"),
            "source_path": _clean(citation.get("source_path"), 600),
            "start_line": citation.get("start_line"),
            "end_line": citation.get("end_line"),
            "label": _clean(citation.get("label"), 360),
        },
        "hydration": {k: v for k, v in hydration.items() if k not in {"record", "file", "text", "fragment", "search_text"}},
        "snippet": _clean(row.get("snippet") or row.get("content"), 800),
    }


def _rejected_view(item: dict[str, Any] | None) -> dict[str, Any]:
    row = item if isinstance(item, dict) else {}
    return {
        "item_id": _clean(row.get("item_id"), 240),
        "title": _clean(row.get("title") or "Rejected evidence", 260),
        "source_lane": _clean(row.get("source_lane") or "unknown", 80),
        "score": row.get("score"),
        "authority_reasons": [str(v)[:180] for v in (row.get("authority_reasons") or []) if str(v or "").strip()][:12],
    }


def build_knowledge_inspector_trace(
    *,
    context_pack: dict[str, Any] | None = None,
    compiled_prompt: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
    output_guard: dict[str, Any] | None = None,
    session_id: str = "",
    project_id: str = "",
    user_text: str = "",
    status: str = "ready",
) -> dict[str, Any]:
    pack = context_pack if isinstance(context_pack, dict) else {}
    gateway = pack.get("retrieval_gateway") if isinstance(pack.get("retrieval_gateway"), dict) else {}
    packet = pack.get("context_packet") if isinstance(pack.get("context_packet"), dict) else {}
    planner = gateway.get("planner") if isinstance(gateway.get("planner"), dict) else {}
    compiled = compiled_prompt if isinstance(compiled_prompt, dict) else {}
    diag = diagnostics if isinstance(diagnostics, dict) else {}
    grounding = compiled.get("grounding_policy") if isinstance(compiled.get("grounding_policy"), dict) else (diag.get("grounding") if isinstance(diag.get("grounding"), dict) else {})
    compiler_diag = compiled.get("diagnostics") if isinstance(compiled.get("diagnostics"), dict) else (diag.get("prompt_compiler") if isinstance(diag.get("prompt_compiler"), dict) else {})
    guard = output_guard if isinstance(output_guard, dict) else (diag.get("output_guard") if isinstance(diag.get("output_guard"), dict) else {})
    enforcement = guard.get("grounding_enforcement") if isinstance(guard.get("grounding_enforcement"), dict) else {}
    fusion = gateway.get("fusion") if isinstance(gateway.get("fusion"), dict) else {}
    packet_budget = packet.get("budget") if isinstance(packet.get("budget"), dict) else {}

    selected = [_candidate_view(item, selected=True) for item in (gateway.get("items") or []) if isinstance(item, dict)][:40]
    rejected = [_rejected_view(item) for item in (gateway.get("authority_rejections") or []) if isinstance(item, dict)][:40]
    citations = []
    for item in gateway.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        citations.append({
            "index": item.get("index"),
            "title": _clean(item.get("title"), 260),
            "source_path": _clean(item.get("source_path"), 600),
            "start_line": item.get("start_line"),
            "end_line": item.get("end_line"),
            "score": item.get("score"),
            "snippet": _clean(item.get("snippet"), 700),
        })

    lane_plan = []
    lanes = planner.get("lanes") if isinstance(planner.get("lanes"), dict) else {}
    weights = planner.get("lane_weights") if isinstance(planner.get("lane_weights"), dict) else {}
    adapters = gateway.get("adapters") if isinstance(gateway.get("adapters"), dict) else {}
    for lane in ("project_structured", "unified_memory", "native_authority", "knowledge_index", "guide_index"):
        state = adapters.get(lane) if isinstance(adapters.get(lane), dict) else {}
        lane_plan.append({
            "lane": lane,
            "selected": bool(lanes.get(lane)),
            "weight": weights.get(lane),
            "status": _clean(state.get("status"), 80),
            "candidate_count": state.get("result_count", 0),
            "error": _clean(state.get("error"), 300),
        })

    return {
        "ok": True,
        "schema_id": KNOWLEDGE_INSPECTOR_SCHEMA_ID,
        "phase": KNOWLEDGE_INSPECTOR_PHASE,
        "status": status,
        "session_id": _clean(session_id, 160),
        "scope_id": _clean(project_id or (gateway.get("identity") or {}).get("scope_id") or "general", 160),
        "query": _clean(user_text or gateway.get("query"), 1200),
        "trace_ids": {
            "assistant_control": _clean(diag.get("assistant_control_trace_id"), 160),
            "gateway": _clean(gateway.get("gateway_trace_id"), 160),
            "planner": _clean(gateway.get("planner_trace_id"), 160),
            "context_packet": _clean(packet.get("packet_id"), 160),
        },
        "decision": {
            "behavior_mode": _clean(diag.get("behavior_mode") or grounding.get("behavior_mode"), 80),
            "grounding_mode": _clean(grounding.get("mode"), 80),
            "intent": _clean(planner.get("intent"), 100),
            "claim_type": _clean(planner.get("claim_type"), 100),
            "target_surface": _clean(planner.get("target_surface"), 100),
            "scope_class": _clean(planner.get("scope_class") or "", 100),
            "hard_sandbox": bool(planner.get("hard_sandbox")),
            "known_state": _clean(gateway.get("known_state") or grounding.get("known_state") or "not_established", 80),
            "fail_closed_recommended": bool(gateway.get("fail_closed_recommended")),
            "deterministic_fail_closed": bool(grounding.get("deterministic_fail_closed")),
            "resolution_reason": _clean(grounding.get("resolution_reason"), 300),
        },
        "query_analysis": {
            "entities": list(planner.get("entities") or [])[:20],
            "keywords": list(planner.get("keywords") or [])[:30],
            "query_variants": list(planner.get("query_variants") or [])[:20],
            "retrieval_query": _clean(planner.get("retrieval_query") or planner.get("query"), 1200),
            "normalization": dict(planner.get("query_normalization") or {}) if isinstance(planner.get("query_normalization"), dict) else {},
            "preferred_evidence_roles": list((planner.get("authority") or {}).get("preferred_evidence_roles") or [])[:20],
        },
        "lanes": lane_plan,
        "fusion": {
            "method": _clean(fusion.get("method"), 80),
            "rrf_k": fusion.get("rrf_k"),
            "lane_counts": dict(fusion.get("lane_counts") or {}) if isinstance(fusion.get("lane_counts"), dict) else {},
            "duplicates_removed": fusion.get("duplicates_removed", 0),
            "reranker": dict(fusion.get("reranker") or {}) if isinstance(fusion.get("reranker"), dict) else {},
        },
        "evidence": {
            "selected": selected,
            "rejected": rejected,
            "selected_count": len(selected),
            "rejected_count": len(rejected),
            "citations": citations,
        },
        "context_packet": {
            "schema_id": _clean(packet.get("schema_id"), 120),
            "provider_visible": packet.get("schema_id") == CONTEXT_PACKET_SCHEMA_ID,
            "candidate_count": len(packet.get("candidate_evidence") or []),
            "selected_count": len(packet.get("evidence") or []),
            "direct_input_count": len(packet.get("direct_inputs") or []),
            "live_context_count": len(packet.get("live_context") or []),
            "budget": dict(packet_budget),
            "compatibility_context_provider_visible": bool((packet.get("diagnostics") or {}).get("compatibility_context_provider_visible")),
            "project_catalog_live_context_suppressed": bool((packet.get("diagnostics") or {}).get("project_catalog_live_context_suppressed")),
        },
        "prompt": {
            "schema_id": _clean(compiler_diag.get("schema_id"), 120),
            "message_count": compiler_diag.get("message_count"),
            "system_message_count": compiler_diag.get("system_message_count"),
            "context_packet_used": bool(compiler_diag.get("context_packet_used") or packet.get("schema_id") == CONTEXT_PACKET_SCHEMA_ID),
        },
        "output": {
            "grounding_enforced": bool(enforcement.get("enforced")),
            "enforcement_reason": _clean(enforcement.get("reason"), 180),
            "provider_text_discarded": bool(enforcement.get("provider_text_discarded")),
            "repair_attempted": bool(guard.get("repair_attempted")),
            "repair_used": bool(guard.get("repair_used")),
            "established_evidence_refusal_blocked": bool(guard.get("established_evidence_refusal_blocked")),
            "evidence_item_answer_dump_detected": bool(
                "evidence_item_answer_dump" in ((guard.get("assessment") or {}).get("issues") or [])
                or "evidence_item_answer_dump" in ((guard.get("repair_assessment") or {}).get("issues") or [])
            ),
        },
        "policy": "NKB-11 is an observability projection over the exact Assistant turn. It does not search, rerank, hydrate, or alter authority decisions.",
    }


def knowledge_inspector_status_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": KNOWLEDGE_INSPECTOR_SCHEMA_ID,
        "phase": KNOWLEDGE_INSPECTOR_PHASE,
        "status": "ready",
        "sections": ["decision", "query_analysis", "lanes", "fusion", "evidence", "context_packet", "prompt", "output"],
        "read_only": True,
        "policy": "Inspector renders stored turn diagnostics. It never performs a second retrieval or modifies knowledge authority.",
    }


def knowledge_inspector_trace_payload(session_id: str) -> dict[str, Any]:
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "schema_id": KNOWLEDGE_INSPECTOR_SCHEMA_ID, "status": "missing_session_id", "trace": {}}
    session = get_session(sid)
    if not isinstance(session, dict):
        return {"ok": False, "schema_id": KNOWLEDGE_INSPECTOR_SCHEMA_ID, "status": "session_not_found", "trace": {}}
    diagnostics = session.get("last_diagnostics") if isinstance(session.get("last_diagnostics"), dict) else {}
    trace = diagnostics.get("knowledge_inspector") if isinstance(diagnostics.get("knowledge_inspector"), dict) else {}
    return {
        "ok": bool(trace),
        "schema_id": KNOWLEDGE_INSPECTOR_SCHEMA_ID,
        "status": "ready" if trace else "no_trace",
        "session_id": sid,
        "trace": trace,
    }
