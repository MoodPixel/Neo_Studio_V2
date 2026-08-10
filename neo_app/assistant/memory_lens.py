from __future__ import annotations

from typing import Any

from neo_app.context_identity import resolve_canonical_identity
from neo_app.assistant.store import get_project, list_context_items, list_memory_captures

MEMORY_LENS_SCHEMA_ID = "neo.assistant.memory_lens.phase11.v1"


def _text(value: Any, limit: int = 320) -> str:
    return str(value or "").strip()[:limit]


def _matches_identity(row: dict[str, Any], identity: Any, memory_filter: dict[str, str]) -> bool:
    if not isinstance(row, dict):
        return False
    row_scope = str(row.get("scope_id") or "").strip()
    row_project = str(row.get("project_id") or "").strip()
    row_surface = str(row.get("surface") or "").strip()
    if row_scope and row_scope == identity.scope_id:
        return True
    if identity.project_id and row_project == identity.project_id:
        return True
    if row_project and row_project == str(memory_filter.get("project_id") or ""):
        if not row_surface or row_surface in {identity.surface_id, str(memory_filter.get("surface") or "")}:
            return True
    if identity.scope_id == "general" and row_project in {"general", "assistant:general"}:
        return True
    return False


def _memory_items(panel: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    rows = panel.get("recent_fragments") if isinstance(panel, dict) else []
    if not isinstance(rows, list):
        return []
    items: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict) or str(row.get("status") or "active") not in {"active", "applied", "summarized"}:
            continue
        items.append({
            "fragment_id": row.get("fragment_id"),
            "title": _text(row.get("title") or row.get("memory_type") or "Memory", 140),
            "summary": _text(row.get("summary") or row.get("content_preview") or row.get("content"), 360),
            "memory_type": row.get("memory_type") or "memory",
            "surface": row.get("surface") or "global",
            "project_id": row.get("project_id") or "",
            "scope_id": row.get("scope_id") or "",
            "priority": row.get("priority"),
            "confidence": row.get("confidence"),
            "trust_level": row.get("trust_level"),
            "embedding_status": row.get("embedding_status") or "",
            "updated_at": row.get("updated_at") or "",
        })
        if len(items) >= limit:
            break
    return items


def assistant_memory_lens_payload(project_id: str = "general", surface: str = "", limit: int = 12) -> dict[str, Any]:
    """User-facing Assistant memory overview.

    This is intentionally a lens, not a second Admin memory editor. It projects
    canonical Unified Memory, durable writeback state, recent retrieval proof,
    Scope Knowledge/manual pins, and background activity for the active Assistant
    Scope while keeping governance actions in Admin -> Memory.
    """

    from neo_app.memory.service import get_memory_service

    limit = max(4, min(int(limit or 12), 40))
    legacy_scope_id = str(project_id or "general").strip() or "general"
    project = get_project(legacy_scope_id) or {}
    resolved_surface = str(surface or project.get("surface_id") or project.get("surface") or "assistant").strip()
    identity = resolve_canonical_identity(
        {
            **project,
            "project_id": legacy_scope_id,
            "scope_id": project.get("scope_id") or legacy_scope_id,
            "surface_id": resolved_surface,
            "delivery_project_id": project.get("delivery_project_id") or "",
        },
        legacy_project_is_scope=True,
        source="assistant_memory_lens",
    )
    memory_filter = identity.memory_filter()
    service = get_memory_service()

    current_snapshot = service.observability_snapshot({**memory_filter, "limit": limit})
    current_memory = current_snapshot.get("memory_inspector") or {}
    current_retrieval = current_snapshot.get("retrieval_inspector") or {}

    general_memory: dict[str, Any] = {}
    if identity.scope_id != "general":
        general_identity = resolve_canonical_identity(
            {"project_id": "general", "scope_id": "general", "surface_id": "global"},
            legacy_project_is_scope=True,
            source="assistant_memory_lens_general",
        )
        general_memory = service.observability_memory({**general_identity.memory_filter(), "limit": min(6, limit)})

    writeback = service.writeback_status()
    pending = [
        row for row in (writeback.get("pending_review") or [])
        if isinstance(row, dict) and _matches_identity(row, identity, memory_filter)
    ][:limit]
    durable = [
        row for row in (writeback.get("recent_writebacks") or [])
        if isinstance(row, dict)
        and str(row.get("status") or "") == "applied"
        and _matches_identity(row, identity, memory_filter)
    ][:limit]

    jobs_payload = service.memory_jobs_status(limit=40)
    jobs = [
        row for row in (jobs_payload.get("jobs") or [])
        if isinstance(row, dict) and _matches_identity(row, identity, memory_filter)
    ][:8]

    trace_payload = service.control_center_trace_list({"controller": "assistant", "limit": 30})
    traces = []
    for trace in trace_payload.get("traces") or []:
        if not isinstance(trace, dict):
            continue
        trace_scope = str(trace.get("scope_id") or "").strip()
        trace_project = str(trace.get("project_id") or "").strip()
        if trace_scope == identity.scope_id or (identity.project_id and trace_project == identity.project_id) or (identity.scope_id == "general" and trace_scope in {"", "general"}):
            traces.append(trace)
        if len(traces) >= 8:
            break

    captures = [
        row for row in list_memory_captures(limit=40)
        if str(row.get("project_id") or "general") in {legacy_scope_id, "general" if identity.scope_id != "general" else legacy_scope_id}
    ][:limit]
    scope_knowledge = list_context_items(project_id=legacy_scope_id, limit=limit)

    current_items = _memory_items(current_memory, limit)
    general_items = _memory_items(general_memory, min(6, limit)) if general_memory else []
    counts = ((current_snapshot.get("summary") or {}).get("counts") or {})

    return {
        "ok": True,
        "schema_id": MEMORY_LENS_SCHEMA_ID,
        "phase": "11",
        "status": "ready",
        "identity": identity.as_dict(),
        "scope": {
            "legacy_scope_id": legacy_scope_id,
            "name": project.get("name") or "General Assistant",
            "type": project.get("type") or "assistant_workspace",
            "surface_id": identity.surface_id,
            "delivery_project_id": identity.project_id or "",
        },
        "summary": {
            "active_memory_count": int(counts.get("fragments") or 0),
            "fact_count": int(counts.get("facts") or 0),
            "durable_count": len(durable),
            "pending_review_count": len(pending),
            "manual_pin_count": len(captures),
            "scope_knowledge_count": len(scope_knowledge),
            "recent_retrieval_count": len(traces),
            "job_count": len(jobs),
        },
        "scope_memory": current_items,
        "general_memory": general_items,
        "durable_memory": durable,
        "pending_review": pending,
        "manual_pins": captures,
        "scope_knowledge": scope_knowledge,
        "recent_retrievals": traces,
        "recent_retrieval_access": (current_retrieval.get("recent_access") or [])[:8],
        "jobs": jobs,
        "diagnostics": {
            "memory_filter": memory_filter,
            "current_memory": current_memory,
            "current_retrieval": current_retrieval,
            "current_summary": current_snapshot.get("summary") or {},
            "writeback_counts": writeback.get("counts_by_status_risk") or [],
            "policy": "Assistant Memory Lens is read/inspect UX. Durable governance, review, conflicts, retention, and deletion remain owned by Admin -> Memory.",
        },
    }
