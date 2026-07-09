from __future__ import annotations

from typing import Any

from neo_app.assistant.contracts import clamp_retrieval_profile
from neo_app.assistant.memory_adapter import resolve_assistant_memory_profile
from neo_app.assistant.store import assistant_profile


def _memory_service():
    from neo_app.memory.service import get_memory_service
    return get_memory_service()


def _trim(value: Any, limit: int = 900) -> str:
    return str(value or "").strip()[:limit]


def _citation_label(item: dict[str, Any], index: int) -> str:
    citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
    label = citation.get("label") or item.get("source_path") or item.get("chunk_id") or f"source-{index}"
    return str(label)


def build_source_grounded_context(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a citation-aware Assistant answer packet from Memory Engine search UX.

    This does not call an LLM. It produces a grounded context packet and a concise
    draft answer scaffold that downstream Assistant chat can use. The rule is simple:
    answer only from cited memory results, and explicitly say when the retrieved
    context is thin.
    """
    data = payload or {}
    question = str(data.get("question") or data.get("message") or data.get("query") or "").strip()
    if not question:
        return {"ok": False, "status": "missing_question", "message": "Question is required."}
    requested_profile = clamp_retrieval_profile(str(data.get("retrieval_profile") or assistant_profile().get("retrieval_profile") or "smart"))
    memory_profile = str(data.get("memory_profile") or resolve_assistant_memory_profile(requested_profile, question) or "assistant_project")
    sources = data.get("sources") if isinstance(data.get("sources"), list) else None
    limit = max(1, min(int(data.get("limit") or 8), 20))
    service = _memory_service()
    search_payload = {
        "query": question,
        "profile": memory_profile,
        "consumer": "assistant_source_grounded_answer",
        "limit": limit,
        "semantic": memory_profile != "fast",
    }
    if sources:
        search_payload["sources"] = sources
    result = service.search_ux(search_payload)
    results = result.get("results") if isinstance(result.get("results"), list) else []
    evidence: list[dict[str, Any]] = []
    for idx, item in enumerate(results, start=1):
        citation = item.get("citation") if isinstance(item.get("citation"), dict) else {}
        evidence.append({
            "index": idx,
            "chunk_id": item.get("chunk_id") or citation.get("chunk_id") or "",
            "title": item.get("title") or citation.get("title") or "Memory source",
            "source_id": item.get("source_id") or citation.get("source_id") or "",
            "source_path": item.get("source_path") or citation.get("source_path") or "",
            "start_line": item.get("start_line") or citation.get("start_line"),
            "end_line": item.get("end_line") or citation.get("end_line"),
            "citation_label": _citation_label(item, idx),
            "viewer_endpoint": item.get("viewer_endpoint") or citation.get("viewer_endpoint") or "",
            "score": item.get("score"),
            "trust_level": item.get("trust_level") or citation.get("trust_level") or "",
            "memory_state": item.get("memory_state") or citation.get("memory_state") or "",
            "approval_state": item.get("approval_state") or citation.get("approval_state") or "",
            "snippet": _trim(item.get("snippet") or item.get("summary") or item.get("content"), 1100),
        })
    evidence_lines = []
    for ev in evidence:
        line = f"[{ev['index']}] {ev['title']} — {ev['citation_label']}"
        if ev.get("trust_level") or ev.get("memory_state"):
            line += f" · {ev.get('trust_level') or 'unknown'}/{ev.get('memory_state') or 'active'}"
        if ev.get("snippet"):
            line += f"\n    {_trim(ev.get('snippet'), 600)}"
        evidence_lines.append(line)
    instructions = [
        "Use only the cited evidence for factual claims about Neo records, code, memory, or project history.",
        "Cite sources inline with bracket numbers like [1] after the claim they support.",
        "If evidence is missing or weak, say what could not be verified instead of guessing.",
        "Prefer confirmed/system/canon memory over draft, conflicting, deprecated, or pending memory.",
        "For code or system-record claims, mention source path and line range when available.",
    ]
    if evidence:
        answer_scaffold = "\n".join([
            "Grounded answer scaffold:",
            "- Start with the direct answer in one or two sentences.",
            "- Add citations after each sourced claim, for example [1] or [2].",
            "- Keep unsourced recommendations clearly labeled as recommendations.",
        ])
        confidence = "grounded"
    else:
        answer_scaffold = "I could not find enough indexed Memory Engine evidence to answer this safely. Index relevant sources or broaden the search profile, then try again."
        confidence = "insufficient_evidence"
    return {
        "ok": True,
        "schema_id": "neo.assistant.source_grounded_answer.v1",
        "status": "ready" if evidence else "insufficient_evidence",
        "question": question,
        "requested_profile": requested_profile,
        "memory_engine_profile": memory_profile,
        "trace_id": result.get("trace_id") or "",
        "backend_used": result.get("backend_used") or "",
        "evidence_count": len(evidence),
        "confidence": confidence,
        "grounding_policy": "Assistant answers should use Memory Search UX citations and should not present uncited memory claims as facts.",
        "instructions": instructions,
        "evidence": evidence,
        "evidence_block": "\n".join(evidence_lines) if evidence_lines else "No cited evidence retrieved.",
        "answer_scaffold": answer_scaffold,
        "search_result": result,
    }
