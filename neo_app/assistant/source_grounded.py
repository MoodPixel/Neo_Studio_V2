from __future__ import annotations

from typing import Any

from neo_app.assistant.contracts import clamp_retrieval_profile
from neo_app.assistant.store import assistant_profile
from neo_app.memory.retrieval_gateway import gateway_grounding_payload, retrieve_context


def build_source_grounded_context(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compatibility source-grounding API backed by the Phase 5 Retrieval Gateway.

    Before Phase 5 this helper performed an independent MemoryService Search UX
    query. That created a second retrieval result beside M9/Control Center. The
    public helper/route remains available, but its implementation now delegates
    to the single Retrieval Gateway and projects the historical v1 response.
    """

    data = payload or {}
    question = str(data.get("question") or data.get("message") or data.get("query") or "").strip()
    if not question:
        return {"ok": False, "status": "missing_question", "message": "Question is required."}

    requested_profile = clamp_retrieval_profile(
        str(data.get("retrieval_profile") or assistant_profile().get("retrieval_profile") or "smart")
    )
    sources = data.get("sources") if isinstance(data.get("sources"), list) else None
    limit = max(1, min(int(data.get("limit") or 8), 20))
    gateway = retrieve_context({
        "query": question,
        "retrieval_profile": requested_profile,
        "consumer": "assistant_source_grounded_answer_compat",
        "limit": limit,
        "include_unified": False,
        "include_guides": False,
        "knowledge_sources": sources or [],
    })
    grounded = gateway_grounding_payload(gateway, question=question)
    return {
        **grounded,
        # Preserve the established public route/schema while recording the new
        # implementation authority explicitly.
        "schema_id": "neo.assistant.source_grounded_answer.v1",
        "compatibility": {
            "implementation": "retrieval_gateway",
            "gateway_schema_id": gateway.get("schema_id") or "",
            "gateway_trace_id": gateway.get("gateway_trace_id") or "",
        },
    }
