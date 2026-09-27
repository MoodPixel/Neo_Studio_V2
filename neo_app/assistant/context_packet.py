from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any
from uuid import uuid4

CONTEXT_PACKET_SCHEMA_ID = "neo.assistant.context_packet.v1"
CONTEXT_PACKET_PHASE = "NKB-9"

_PROFILE_BUDGETS = {
    "fast": 7000,
    "smart": 12000,
    "deep": 18000,
}

_PROFILE_ITEM_LIMITS = {
    "fast": 1200,
    "smart": 1700,
    "deep": 2300,
}



def _clean(value: Any, *, limit: int = 0) -> str:
    text = str(value or "").replace("\r\n", "\n").strip()
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    if limit and len(text) > limit:
        return text[: max(0, limit - 1)].rstrip() + "…"
    return text



def _fingerprint(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()
    if not normalized:
        return ""
    return hashlib.sha256(normalized[:4000].encode("utf-8", errors="ignore")).hexdigest()



def _profile(value: Any) -> str:
    profile = str(value or "smart").strip().lower()
    return profile if profile in _PROFILE_BUDGETS else "smart"



def _compact_scope(scope: dict[str, Any] | None) -> dict[str, str]:
    data = scope if isinstance(scope, dict) else {}
    return {
        "name": _clean(data.get("name") or data.get("scope_name") or "General Assistant", limit=180),
        "type": _clean(data.get("type") or "assistant_workspace", limit=100),
        "description": _clean(data.get("description"), limit=900),
        "notes": _clean(data.get("notes"), limit=900),
    }



def _compact_identity(identity: dict[str, Any] | None) -> dict[str, str]:
    data = identity if isinstance(identity, dict) else {}
    return {
        "surface_id": str(data.get("surface_id") or data.get("surface") or "assistant"),
        "scope_id": str(data.get("scope_id") or data.get("project_id") or "general"),
        "project_id": str(data.get("project_id") or ""),
        "workspace_id": str(data.get("workspace_id") or "assistant"),
    }



def _compact_citation(value: dict[str, Any] | None) -> dict[str, Any]:
    citation = value if isinstance(value, dict) else {}
    return {
        "index": citation.get("index"),
        "source_id": str(citation.get("source_id") or ""),
        "source_path": str(citation.get("source_path") or ""),
        "start_line": citation.get("start_line"),
        "end_line": citation.get("end_line"),
        "label": str(citation.get("label") or citation.get("source_path") or citation.get("source_id") or ""),
        "viewer_endpoint": str(citation.get("viewer_endpoint") or ""),
    }



def _compact_authority(value: dict[str, Any] | None) -> dict[str, str]:
    authority = value if isinstance(value, dict) else {}
    return {
        "evidence_role": str(authority.get("evidence_role") or "supporting_context"),
        "source_integrity": str(authority.get("source_integrity") or "present"),
        "lifecycle": str(authority.get("lifecycle") or "active"),
        "conflict": str(authority.get("conflict") or "none"),
    }



def _evidence_candidates(gateway_result: dict[str, Any], *, profile: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    per_item_limit = _PROFILE_ITEM_LIMITS[profile]
    seen: set[str] = set()
    for rank, item in enumerate(gateway_result.get("items") or [], start=1):
        if not isinstance(item, dict):
            continue
        content = _clean(item.get("content") or item.get("snippet"), limit=per_item_limit)
        fp = _fingerprint(content)
        if not content or (fp and fp in seen):
            continue
        if fp:
            seen.add(fp)
        citation = _compact_citation(item.get("citation") if isinstance(item.get("citation"), dict) else {})
        authority = _compact_authority(item.get("authority") if isinstance(item.get("authority"), dict) else {})
        rows.append({
            "packet_evidence_id": f"ev_{rank}",
            "item_id": str(item.get("item_id") or ""),
            "source_lane": str(item.get("source_lane") or "context"),
            "kind": str(item.get("kind") or "context"),
            "title": _clean(item.get("title") or "Context", limit=220),
            "content": content,
            "snippet": _clean(item.get("snippet") or content, limit=800),
            "score": item.get("score"),
            "authority": authority,
            "epistemic_role": str(item.get("epistemic_role") or "declarative_passage"),
            "epistemic_state": str(item.get("epistemic_state") or "established"),
            "supports_positive_claims": bool(item.get("supports_positive_claims", True)),
            "answerability_score": item.get("answerability_score"),
            "answerability_reasons": list(item.get("answerability_reasons") or []),
            "answer_target": str(item.get("answer_target") or ""),
            "direct_definition": bool(item.get("direct_definition")),
            "citation": citation,
            "provenance": {
                "adapter": str(((item.get("provenance") or {}) if isinstance(item.get("provenance"), dict) else {}).get("adapter") or ""),
                "native_ref": dict(((item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}).get("native_ref") or {}) if isinstance((((item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}).get("native_ref")), dict) else {},
            },
        })
    return rows



def _render_scope(scope: dict[str, str]) -> str:
    lines = [f"- Name: {scope.get('name') or 'General Assistant'}", f"- Type: {scope.get('type') or 'assistant_workspace'}"]
    if scope.get("description"):
        lines.append(f"- Description: {scope['description']}")
    if scope.get("notes"):
        lines.append(f"- Notes: {scope['notes']}")
    return "\n".join(lines)



def _render_evidence(item: dict[str, Any]) -> str:
    authority = item.get("authority") if isinstance(item.get("authority"), dict) else {}
    citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
    cite = f" [{citation.get('index')}]" if citation.get("index") else ""
    source = citation.get("label") or citation.get("source_path") or citation.get("source_id") or item.get("source_lane") or "context"
    meta = " · ".join(part for part in [
        str(authority.get("evidence_role") or "supporting_context"),
        str(authority.get("source_integrity") or "present"),
        str(authority.get("lifecycle") or "active"),
    ] if part)
    epistemic_role = str(item.get("epistemic_role") or "declarative_passage")
    epistemic_state = str(item.get("epistemic_state") or "established")
    positive = "yes" if item.get("supports_positive_claims", True) else "no"
    answerability = item.get("answerability_score")
    answerability_text = f" · Answerability: {answerability}" if answerability is not None else ""
    return (
        f"{cite} {item.get('title') or 'Context'} — {source}\n"
        f"  Type: {meta}\n"
        f"  Epistemic: {epistemic_role} / {epistemic_state} · Supports affirmative claims: {positive}{answerability_text}\n"
        f"  {_clean(item.get('content'), limit=3000)}"
    ).strip()



def _base_overhead(packet: dict[str, Any], *, direct_inputs: list[dict[str, Any]] | None = None) -> int:
    planner = packet.get("planner") if isinstance(packet.get("planner"), dict) else {}
    scope = packet.get("scope") if isinstance(packet.get("scope"), dict) else {}
    live = packet.get("live_context") if isinstance(packet.get("live_context"), list) else []
    legacy = packet.get("legacy_context") if isinstance(packet.get("legacy_context"), list) else []
    direct = direct_inputs if isinstance(direct_inputs, list) else []
    text = [
        str(planner.get("intent") or "general_recall"),
        str(planner.get("claim_type") or "general_fact"),
        str(planner.get("known_state") or "not_established"),
        _render_scope(scope),
    ]
    text.extend(str(item.get("content") or "") for item in live if isinstance(item, dict))
    text.extend(str(item.get("content") or "") for item in legacy if isinstance(item, dict))
    text.extend(str(item.get("content") or "") for item in direct if isinstance(item, dict))
    return sum(len(part) for part in text) + 900



def _select_for_budget(packet: dict[str, Any], *, direct_inputs: list[dict[str, Any]] | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    budget = int(((packet.get("budget") or {}) if isinstance(packet.get("budget"), dict) else {}).get("max_chars") or _PROFILE_BUDGETS["smart"])
    direct = list(direct_inputs or [])
    candidates = list(packet.get("candidate_evidence") or [])
    used = _base_overhead(packet, direct_inputs=direct)
    selected: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        content = _clean(item.get("content"))
        fp = _fingerprint(content)
        if not content or (fp and fp in seen):
            if item.get("item_id"):
                dropped.append(str(item.get("item_id")))
            continue
        projected = len(_render_evidence(item)) + 80
        remaining = budget - used
        if remaining <= 500:
            if item.get("item_id"):
                dropped.append(str(item.get("item_id")))
            continue
        if projected > remaining:
            clipped = deepcopy(item)
            clipped["content"] = _clean(content, limit=max(300, remaining - 220))
            if len(clipped["content"]) < 180:
                if item.get("item_id"):
                    dropped.append(str(item.get("item_id")))
                continue
            item = clipped
            projected = len(_render_evidence(item)) + 80
        selected.append(item)
        if fp:
            seen.add(fp)
        used += projected
    return selected, {
        "max_chars": budget,
        "used_chars_estimate": min(used, budget),
        "candidate_evidence_count": len(candidates),
        "selected_evidence_count": len(selected),
        "dropped_evidence_count": len(dropped),
        "dropped_item_ids": dropped[:40],
        "direct_input_count": len(direct),
    }



def render_context_packet(packet: dict[str, Any]) -> str:
    planner = packet.get("planner") if isinstance(packet.get("planner"), dict) else {}
    scope = packet.get("scope") if isinstance(packet.get("scope"), dict) else {}
    live = [item for item in (packet.get("live_context") or []) if isinstance(item, dict) and _clean(item.get("content"))]
    legacy = [item for item in (packet.get("legacy_context") or []) if isinstance(item, dict) and _clean(item.get("content"))]
    direct = [item for item in (packet.get("direct_inputs") or []) if isinstance(item, dict) and _clean(item.get("content"))]
    evidence = [item for item in (packet.get("evidence") or []) if isinstance(item, dict) and _clean(item.get("content"))]

    lines = [
        "NEO CONTEXT PACKET",
        f"Intent: {planner.get('intent') or 'general_recall'}",
        f"Claim type: {planner.get('claim_type') or 'general_fact'}",
        f"Scope class: {planner.get('scope_class') or 'unspecified'}",
        f"Hard sandbox: {'yes' if planner.get('hard_sandbox') else 'no'}",
        f"Evidence state: {planner.get('known_state') or 'not_established'}",
        f"Fail-closed recommended: {'yes' if planner.get('fail_closed_recommended') else 'no'}",
    ]
    canonical_terms = [str(v) for v in (planner.get("canonical_terms") or []) if str(v or "").strip()]
    corrected_terms = [str(v) for v in (planner.get("corrected_terms") or []) if str(v or "").strip()]
    if canonical_terms:
        lines.append("Canonical query terms: " + ", ".join(canonical_terms))
    if corrected_terms:
        lines.append("Spelling/alias recovery applied: " + ", ".join(corrected_terms))
        lines.append(f"Effective retrieval query: {planner.get('retrieval_query') or planner.get('original_query') or ''}")
    lines.extend([
        "",
        "[Active scope]",
        _render_scope(scope),
    ])
    if live:
        lines.extend(["", "[Live state]"])
        for item in live:
            lines.append(f"- {item.get('title') or 'Live surface state'}: {_clean(item.get('content'), limit=3500)}")
    if legacy:
        lines.extend(["", "[Explicit legacy context]"])
        for item in legacy:
            lines.append(f"- {item.get('title') or 'Legacy context'}: {_clean(item.get('content'), limit=3000)}")
    if direct:
        lines.extend(["", "[Current-turn direct inputs]"])
        for item in direct:
            lines.append(f"- {item.get('title') or 'Uploaded document'}: {_clean(item.get('content'), limit=5000)}")
    lines.extend(["", "[Verified/retrieved evidence]"])
    if evidence:
        for item in evidence:
            lines.append(_render_evidence(item))
    else:
        lines.append("- No evidence item survived retrieval, authority checks, and packet budgeting for this turn.")
    lines.extend([
        "",
        "[Packet semantics]",
        "- This packet is the only model-visible retrieval context for this turn.",
        "- Compatibility Project Brain, Scope Knowledge, Guide, and memory projections are Inspector-only when this packet is present.",
        "- Citations in brackets refer to source-backed evidence selected by the Retrieval Planner.",
        "- Evidence marked unresolved_question or Supports affirmative claims: no can establish that a question/uncertainty exists, but cannot establish a positive answer to that question.",
        "- Prefer direct definitions, entity profiles, and declarative passages for factual answers. Synthesize one coherent answer rather than one answer per evidence item.",
    ])
    return _clean("\n".join(lines), limit=int(((packet.get("budget") or {}) if isinstance(packet.get("budget"), dict) else {}).get("max_chars") or _PROFILE_BUDGETS["smart"]))



def build_context_packet(
    *,
    gateway_result: dict[str, Any] | None,
    identity: dict[str, Any] | None,
    retrieval_profile: str = "smart",
    scope: dict[str, Any] | None = None,
    live_context: list[dict[str, Any]] | None = None,
    legacy_context: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    gateway = gateway_result if isinstance(gateway_result, dict) else {}
    profile = _profile(retrieval_profile or gateway.get("profile"))
    planner = gateway.get("planner") if isinstance(gateway.get("planner"), dict) else {}
    raw_live_context = [dict(item) for item in (live_context or []) if isinstance(item, dict) and _clean(item.get("content"))]
    identity_compact = _compact_identity(identity or gateway.get("identity"))
    suppress_project_catalog_live_context = bool(
        planner.get("hard_sandbox")
        and identity_compact.get("surface_id") in {"assistant", "global"}
    )
    provider_live_context = [] if suppress_project_catalog_live_context else raw_live_context
    packet: dict[str, Any] = {
        "ok": True,
        "schema_id": CONTEXT_PACKET_SCHEMA_ID,
        "phase": CONTEXT_PACKET_PHASE,
        "packet_id": f"ctxpkt_{uuid4().hex[:12]}",
        "status": "ready",
        "profile": profile,
        "identity": identity_compact,
        "scope": _compact_scope(scope),
        "planner": {
            "intent": str(planner.get("intent") or "general_recall"),
            "claim_type": str(planner.get("claim_type") or "general_fact"),
            "target_surface": str(planner.get("target_surface") or ""),
            "scope_class": str(planner.get("scope_class") or ""),
            "hard_sandbox": bool(planner.get("hard_sandbox")),
            "known_state": str(gateway.get("known_state") or "not_established"),
            "fail_closed_recommended": bool(gateway.get("fail_closed_recommended")),
            "canonical_terms": [str(v) for v in ((planner.get("query_normalization") or {}).get("resolved_terms") or (planner.get("query_normalization") or {}).get("canonical_terms") or [])[:8]],
            "corrected_terms": [str(v) for v in ((planner.get("query_normalization") or {}).get("corrected_terms") or [])[:6]],
            "original_query": str(((planner.get("query_normalization") or {}).get("original_query") or planner.get("query") or "")),
            "retrieval_query": str(planner.get("retrieval_query") or planner.get("query") or ""),
        },
        "live_context": provider_live_context,
        "legacy_context": [dict(item) for item in (legacy_context or []) if isinstance(item, dict) and _clean(item.get("content"))],
        "direct_inputs": [],
        "candidate_evidence": _evidence_candidates(gateway, profile=profile),
        "evidence": [],
        "budget": {"max_chars": _PROFILE_BUDGETS[profile]},
        "diagnostics": {
            "gateway_trace_id": str(gateway.get("gateway_trace_id") or ""),
            "planner_trace_id": str(gateway.get("planner_trace_id") or ""),
            "compatibility_context_provider_visible": False,
            "single_model_context": True,
            "project_catalog_live_context_suppressed": suppress_project_catalog_live_context,
            "project_catalog_live_context_suppressed_count": len(raw_live_context) if suppress_project_catalog_live_context else 0,
        },
        "policy": "NKB-9 packages the NKB-8 verified shortlist, compact scope, current live state, and current-turn direct document input into one bounded provider-visible packet. Compatibility context remains Inspector-only.",
    }
    selected, budget_diag = _select_for_budget(packet)
    packet["evidence"] = selected
    packet["budget"].update(budget_diag)
    packet["rendered_text"] = render_context_packet(packet)
    packet["budget"]["rendered_chars"] = len(packet["rendered_text"])
    return packet



def finalize_context_packet(packet: dict[str, Any] | None, attachment_context: dict[str, Any] | None = None) -> dict[str, Any]:
    base = deepcopy(packet) if isinstance(packet, dict) else {}
    if base.get("schema_id") != CONTEXT_PACKET_SCHEMA_ID:
        return base
    attachment = attachment_context if isinstance(attachment_context, dict) else {}
    direct: list[dict[str, Any]] = []
    document_text = _clean(attachment.get("document_context"), limit=5200)
    if document_text:
        direct.append({
            "kind": "current_turn_document",
            "title": "Uploaded document context",
            "content": document_text,
            "authority": {"evidence_role": "current_turn_user_input", "source_integrity": "present", "lifecycle": "active", "conflict": "none"},
        })
    base["direct_inputs"] = direct
    selected, budget_diag = _select_for_budget(base, direct_inputs=direct)
    base["evidence"] = selected
    base["budget"].update(budget_diag)
    base["rendered_text"] = render_context_packet(base)
    base["budget"]["rendered_chars"] = len(base["rendered_text"])
    base["status"] = "ready"
    return base



def context_packet_status_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": CONTEXT_PACKET_SCHEMA_ID,
        "phase": CONTEXT_PACKET_PHASE,
        "status": "ready",
        "profiles": {name: {"max_chars": budget, "per_evidence_chars": _PROFILE_ITEM_LIMITS[name]} for name, budget in _PROFILE_BUDGETS.items()},
        "provider_visible_sources": ["context_packet", "conversation_history", "current_user_message", "vision_attachments"],
        "compatibility_sources": ["project_brain", "project_knowledge", "built_in_guides", "memory_engine", "source_grounding", "admin_memory"],
        "policy": "One bounded context packet is provider-visible. Compatibility projections remain available to Inspector and legacy UI surfaces but are not compiled beside the packet.",
    }
