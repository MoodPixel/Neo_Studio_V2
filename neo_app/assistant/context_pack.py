from __future__ import annotations

from typing import Any

from neo_app.assistant.contracts import contract_lock_payload, clamp_retrieval_profile
from neo_app.assistant.memory_adapter import memory_health_payload, retrieve_assistant_memory_engine, search_assistant_memory
from neo_app.assistant.source_grounded import build_source_grounded_context
from neo_app.assistant.store import assistant_profile, get_project, get_session, list_memory_captures, list_context_items
from neo_app.assistant.surface_project_context import build_surface_project_context, resolve_surface_project_context_surface, surface_project_context_text
from neo_app.assistant.guides import search_guides, guides_context_text
from neo_app.assistant.project_brain import project_brain_context_text
from neo_app.services.runtime_debug_logs import log_surface_event, record_surface_error, record_surface_snapshot


MAX_THREAD_MESSAGES = 10
ASSISTANT_SCOPE_MEMORY_SOURCES = ["assistant_memory", "system_records", "neo_codebase", "prompt_libraries", "admin_config"]
LEGACY_PROJECT_WORKSPACE_SOURCE = "project_workspace"
LEGACY_PROJECT_WORKSPACE_PHRASES = (
    "project workspace",
    "legacy project workspace",
    "project delivery dashboard",
    "client status view",
    "project package builder",
    "project review queue",
    "project milestone",
    "project deliverable",
)


def _text(value: Any, limit: int = 4000) -> str:
    return str(value or "").strip()[:limit]


def _section(section_id: str, title: str, content: str, *, source: str = "assistant", items: int = 0) -> dict[str, Any]:
    content = _text(content, 8000)
    return {
        "section_id": section_id,
        "title": title,
        "source": source,
        "items": int(items or 0),
        "chars": len(content),
        "preview": content[:260],
        "content": content,
    }


def _format_thread(messages: list[dict[str, Any]]) -> str:
    recent = messages[-MAX_THREAD_MESSAGES:]
    lines: list[str] = []
    for message in recent:
        role = str(message.get("role") or "user").strip()
        text = _text(message.get("text"), 1200)
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _format_memories(memory_result: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in memory_result.get("results", []) if isinstance(memory_result, dict) else []:
        event = item.get("event") if isinstance(item, dict) else {}
        title = event.get("title") or event.get("event_type") or "Memory"
        summary = event.get("summary") or (event.get("payload") or {}).get("text") or ""
        surface = event.get("surface") or "global"
        rows.append(f"- [{surface}] {title}: {_text(summary, 700)}")
    return "\n".join(rows)


def _format_engine_results(memory_result: dict[str, Any]) -> str:
    rows: list[str] = []
    for item in memory_result.get("results", []) if isinstance(memory_result, dict) else []:
        title = item.get("title") or item.get("source_path") or "Memory result"
        source_path = item.get("source_path") or item.get("source_id") or "memory"
        start_line = item.get("start_line")
        end_line = item.get("end_line")
        line_hint = f":L{start_line}-L{end_line}" if start_line and end_line else ""
        score = item.get("score")
        retrieval_type = item.get("retrieval_type") or "memory"
        snippet = item.get("snippet") or item.get("summary") or item.get("content") or ""
        score_hint = f" · score {score}" if score is not None else ""
        rows.append(f"- [{retrieval_type}{score_hint}] {title} ({source_path}{line_hint}): {_text(snippet, 900)}")
    return "\n".join(rows)


def _message_requests_legacy_project_workspace(message: str) -> bool:
    text = str(message or "").lower()
    return any(phrase in text for phrase in LEGACY_PROJECT_WORKSPACE_PHRASES)


def _legacy_project_workspace_context(project_id: str, *, message: str = "", include: bool = False, limit: int = 12) -> tuple[dict[str, Any], str]:
    """Return legacy Project Workspace context only when explicitly requested.

    Assistant Scope is the primary context model. The legacy creator Project
    Workspace can still be inspected on demand, but fallback/general workspace
    data must not silently enter Assistant context packs for Assistant-only
    scopes.
    """

    reason = "excluded_by_default"
    if include:
        reason = "explicit_flag"
    elif _message_requests_legacy_project_workspace(message):
        reason = "explicit_message_phrase"
    else:
        return {}, reason

    try:
        from neo_app.project_workspace import project_workspace_context_payload

        payload = project_workspace_context_payload(project_id, limit=limit)
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}, "legacy_import_failed"

    if not isinstance(payload, dict) or not payload.get("ok"):
        return payload if isinstance(payload, dict) else {}, "legacy_unavailable"
    if payload.get("fallback_used"):
        # Do not let Project Workspace's general fallback rewrite Assistant scope context.
        return {**payload, "context_items": [], "links": []}, "legacy_fallback_rejected"
    requested = str(payload.get("requested_project_id") or project_id or "general")
    actual = str(payload.get("project_id") or "")
    if requested and actual and requested != actual:
        return {**payload, "context_items": [], "links": []}, "legacy_project_mismatch_rejected"
    return payload, reason


def _format_legacy_workspace_context(workspace: dict[str, Any]) -> tuple[str, int, int]:
    context_items = workspace.get("context_items", []) if isinstance(workspace, dict) else []
    links = workspace.get("links", []) if isinstance(workspace, dict) else []
    if not isinstance(context_items, list):
        context_items = []
    if not isinstance(links, list):
        links = []
    workspace_text = "\n".join([
        f"- [legacy workspace · {item.get('kind') or 'note'}] {item.get('title') or item.get('context_id')}: {_text(item.get('text'), 900)}"
        for item in context_items
    ])
    workspace_link_text = "\n".join([
        f"- [legacy link · {link.get('surface') or 'surface'}] {link.get('title') or link.get('link_id')}: {_text(link.get('path') or link.get('ref_id') or link.get('notes'), 500)}"
        for link in links
    ])
    content = "\n".join(part for part in [workspace_text, workspace_link_text] if part)
    return content, len(context_items), len(links)



def _assistant_context_log_summary(
    *,
    session_id: str = "",
    project_id: str = "",
    retrieval_profile: str = "",
    message: str = "",
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    diag = diagnostics if isinstance(diagnostics, dict) else {}
    return {
        "session_id": str(session_id or diag.get("session_id") or ""),
        "scope_id": str(diag.get("scope_id") or project_id or "general"),
        "project_id": str(diag.get("project_id") or project_id or "general"),
        "retrieval_profile": str(retrieval_profile or diag.get("retrieval_profile") or "smart"),
        "message_chars": len(str(message or "")),
        "section_count": int(diag.get("section_count") or 0),
        "chars": int(diag.get("chars") or 0),
        "memory_item_count": int(diag.get("memory_item_count") or 0),
        "project_context_item_count": int(diag.get("project_context_item_count") or 0),
        "surface_context_item_count": int(diag.get("surface_context_item_count") or 0),
        "legacy_project_workspace_included": bool(diag.get("legacy_project_workspace_included")),
        "global_project_workspace_decoupled": bool(diag.get("global_project_workspace_decoupled", True)),
        "memory_sources": diag.get("memory_sources") if isinstance(diag.get("memory_sources"), list) else [],
    }


def build_context_pack(
    session_id: str = "",
    project_id: str = "",
    message: str = "",
    retrieval_profile: str = "smart",
    *,
    include_legacy_project_workspace: bool = False,
    active_surface: str = "",
    surface_context_snapshot: dict[str, Any] | None = None,
    active_surface_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    retrieval_profile = clamp_retrieval_profile(retrieval_profile)
    profile = assistant_profile()
    requested_project_id = str(project_id or profile.get("default_project_id") or "general")
    project = get_project(requested_project_id) or (get_project("general") if requested_project_id == "general" else {}) or {}
    session = get_session(session_id) if session_id else None
    resolved_project_id = str(project_id or project.get("project_id") or requested_project_id or "general")
    messages = session.get("messages") if isinstance(session, dict) and isinstance(session.get("messages"), list) else []
    local_captures = [c for c in list_memory_captures(20) if c.get("project_id") in {resolved_project_id, "general", ""}]
    context_items = [item for item in list_context_items(project_id=resolved_project_id, limit=20)]
    global_context_items = [item for item in list_context_items(project_id="general", limit=8) if resolved_project_id != "general"]
    context_items = (context_items + global_context_items)[:28]

    legacy_workspace, legacy_workspace_reason = _legacy_project_workspace_context(
        resolved_project_id,
        message=message,
        include=include_legacy_project_workspace,
        limit=12,
    )
    legacy_workspace_text, legacy_workspace_context_count, legacy_workspace_link_count = _format_legacy_workspace_context(legacy_workspace)
    legacy_workspace_included = bool(legacy_workspace_text and legacy_workspace_reason in {"explicit_flag", "explicit_message_phrase"})

    resolved_surface = resolve_surface_project_context_surface(resolved_project_id, active_surface)
    live_surface_context: dict[str, Any] = {}
    live_surface_text = ""
    live_surface_included = resolved_surface not in {"", "assistant"} or bool(surface_context_snapshot or active_surface_context)
    if live_surface_included:
        try:
            live_surface_context = build_surface_project_context(
                surface=resolved_surface,
                project_id=resolved_project_id,
                session_id=session_id,
                payload={
                    "project_id": resolved_project_id,
                    "session_id": session_id,
                    "active_surface": resolved_surface,
                    "surface_context_snapshot": surface_context_snapshot or active_surface_context or {},
                },
            )
            live_surface_text = surface_project_context_text(live_surface_context)
        except Exception as exc:
            live_surface_context = {"ok": False, "surface": resolved_surface, "error": str(exc)[:500]}
            live_surface_text = f"Surface project context unavailable for {resolved_surface}: {exc}"

    query_parts = [message, project.get("name"), project.get("description"), project.get("notes")]
    memory_query = " ".join(str(part or "") for part in query_parts).strip()

    guide_payload = search_guides(memory_query or message, surface=resolved_surface, project_id=resolved_project_id, limit={"fast": 4, "smart": 7, "deep": 12}.get(str(retrieval_profile or "smart"), 7))
    guide_text = guides_context_text(guide_payload)
    project_brain_text, project_brain_diag = project_brain_context_text(resolved_project_id, surface=resolved_surface, limit={"fast": 3, "smart": 5, "deep": 8}.get(str(retrieval_profile or "smart"), 5), query=message)
    limit = {"fast": 4, "smart": 8, "deep": 14}.get(str(retrieval_profile or "smart"), 8)
    memory_sources = list(ASSISTANT_SCOPE_MEMORY_SOURCES)
    if legacy_workspace_included:
        memory_sources.append(LEGACY_PROJECT_WORKSPACE_SOURCE)
    memory_result = retrieve_assistant_memory_engine(
        memory_query,
        resolved_project_id,
        profile=retrieval_profile,
        limit=limit,
        semantic=retrieval_profile != "fast",
        sources=memory_sources,
    )
    grounding = build_source_grounded_context({
        "question": memory_query or message,
        "retrieval_profile": retrieval_profile,
        "limit": min(limit, 10),
        "sources": memory_sources,
    }) if (memory_query or message) else {"ok": False, "evidence": []}
    memory_text = _format_engine_results(memory_result)
    legacy_event_memory = {}
    if not memory_text:
        legacy_event_memory = search_assistant_memory(memory_query, resolved_project_id, limit=min(limit, 6), semantic=retrieval_profile != "fast")
        memory_text = _format_memories(legacy_event_memory)

    scope_text = "\n".join([
        f"Assistant scope: {project.get('name') or 'General Assistant'}",
        f"Scope ID: {project.get('scope_id') or project.get('project_id') or resolved_project_id}",
        f"Type: {project.get('type') or 'assistant_workspace'}",
        f"Description: {_text(project.get('description'), 1500) or 'none'}",
        f"Notes: {_text(project.get('notes'), 2500) or 'none'}",
    ])
    local_capture_text = "\n".join([f"- {c.get('title') or c.get('capture_id')}: {_text(c.get('text'), 600)}" for c in local_captures])
    thread_text = _format_thread(messages)
    scope_knowledge_text = "\n".join([
        f"- [{item.get('kind') or 'context'} · {item.get('surface') or 'assistant'}] {item.get('title') or item.get('context_id')}: {_text(item.get('text'), 900)}"
        for item in context_items
    ])
    current_text = _text(message, 4000)
    persona_text = "\n".join([
        "You are Neo Assistant, the user-facing intelligence layer for Neo Studio.",
        "Respect the active Assistant scope and current request above older memory.",
        "Use centralized Memory Engine retrieval as context, not as unquestionable truth.",
        "Do not silently pull legacy Project Workspace context into Assistant scopes unless explicitly requested.",
        "For questions about how Neo works, supported options, model families, parameters, or tabs, prefer built-in Neo guides first.",
        "Use live surface snapshots for current loaded values and indexed metadata only when the user asks about previous outputs, saved prompts, history, sidecars, replay, or inspection.",
        "When source-grounded evidence is available, cite it inline using bracket citations like [1], but do not dump source paths or raw metadata unless the user asks for trace/debug detail.",
        "Use built-in Neo guides, live surface snapshots, indexed project data, and uploaded project context before saying context is missing.",
        "If a surface workspace is active, questions about that surface are inside scope; explain from guides when live values are missing.",
        "Do not present uncited memory claims as facts; state uncertainty when context is thin.",
        "Answer the user's requested deliverable first. For prompt-writing requests, return the usable prompt directly instead of explaining your process.",
        "Be practical, concise, and action-oriented. State uncertainty when context is thin.",
    ])

    sections = [
        _section("persona", "Assistant persona and rules", persona_text, source="assistant"),
        _section("current_message", "Current user message", current_text, source="composer"),
        _section("project", "Active Assistant scope", scope_text, source="assistant_scope"),
        _section("built_in_guides", "Built-in Neo guides", guide_text, source="neo_guides", items=int(guide_payload.get("count") or 0)),
        _section("active_surface_context", "Live surface project context", live_surface_text or "No live surface project context attached for this Assistant scope.", source="assistant_surface_context_provider", items=1 if live_surface_text else 0),
        _section("project_brain", "Assistant project brain snapshots and indexed data", project_brain_text, source="assistant_project_brain", items=int(project_brain_diag.get("snapshot_count") or 0) + int(project_brain_diag.get("index_count") or 0)),
        _section("project_knowledge", "Assistant scope knowledge and saved surface handoffs", scope_knowledge_text or "No Assistant scope knowledge or surface handoffs attached yet.", source="assistant_context_items", items=len(context_items)),
    ]
    if legacy_workspace_included:
        sections.append(_section(
            "legacy_project_workspace",
            "Legacy Project Workspace memory",
            legacy_workspace_text,
            source="project_workspace_legacy_explicit",
            items=legacy_workspace_context_count + legacy_workspace_link_count,
        ))
    sections.extend([
        _section("thread", "Recent thread context", thread_text or "No previous thread messages.", source="assistant_session", items=len(messages[-MAX_THREAD_MESSAGES:])),
        _section("memory_engine", "Memory Engine retrieval", memory_text or "No matching Memory Engine context found.", source="memory_engine", items=len(memory_result.get("results", []) if isinstance(memory_result, dict) else [])),
        _section("source_grounding", "Source-grounded evidence", (grounding.get("evidence_block") if isinstance(grounding, dict) else "") or "No source-grounded evidence retrieved.", source="memory_search_ux", items=len(grounding.get("evidence", []) if isinstance(grounding, dict) else [])),
        _section("admin_memory", "Legacy centralized memory events", _format_memories(legacy_event_memory) if legacy_event_memory else "Memory Engine handled this context pack.", source="memory_engine_legacy_events", items=len(legacy_event_memory.get("results", []) if isinstance(legacy_event_memory, dict) else 0)),
        _section("local_captures", "Assistant local captures", local_capture_text or "No local captures for this scope yet.", source="assistant_local", items=len(local_captures)),
    ])
    prompt_block = "\n\n".join([f"## {s['title']}\n{s['content']}" for s in sections if s.get("content")]).strip()
    health = memory_health_payload()
    diagnostics = {
        "project_id": resolved_project_id,
        "scope_id": project.get("scope_id") or project.get("project_id") or resolved_project_id,
        "session_id": (session or {}).get("session_id") or session_id,
        "retrieval_profile": retrieval_profile or "smart",
        "memory_source": "memory_engine",
        "legacy_memory_source": "admin_engine",
        "memory_backend_used": memory_result.get("backend_used") if isinstance(memory_result, dict) else "unknown",
        "memory_engine_profile": memory_result.get("memory_engine_profile") if isinstance(memory_result, dict) else "unknown",
        "memory_trace_id": memory_result.get("trace_id") if isinstance(memory_result, dict) else "",
        "memory_sources": memory_sources,
        "assistant_scope_context_primary": True,
        "global_project_workspace_decoupled": True,
        "legacy_project_workspace_policy": "excluded_by_default_unless_explicit",
        "legacy_project_workspace_included": legacy_workspace_included,
        "legacy_project_workspace_reason": legacy_workspace_reason,
        "active_surface_context_included": bool(live_surface_text),
        "guide_item_count": int(guide_payload.get("count") or 0),
        "guide_surface": guide_payload.get("surface") or resolved_surface,
        "project_brain_snapshot_count": int(project_brain_diag.get("snapshot_count") or 0),
        "project_brain_index_count": int(project_brain_diag.get("index_count") or 0),
        "active_surface_context_schema_id": live_surface_context.get("schema_id") if isinstance(live_surface_context, dict) else "",
        "active_surface": resolved_surface,
        "active_surface_project_context": {
            "surface": (live_surface_context.get("surface") if isinstance(live_surface_context, dict) else resolved_surface),
            "project_id": (live_surface_context.get("project_id") if isinstance(live_surface_context, dict) else resolved_project_id),
            "live_snapshot_included": bool((live_surface_context or {}).get("live_snapshot_included")) if isinstance(live_surface_context, dict) else False,
            "parameter_count": len((live_surface_context.get("current_parameters") or {}) if isinstance(live_surface_context.get("current_parameters") if isinstance(live_surface_context, dict) else None, dict) else {}),
            "extension_count": len((((live_surface_context.get("extensions") or {}) if isinstance(live_surface_context, dict) else {}).get("extensions") or []) if isinstance(((live_surface_context.get("extensions") or {}) if isinstance(live_surface_context, dict) else {}).get("extensions"), list) else []),
        },
        "source_grounded_answers": True,
        "source_grounding_trace_id": grounding.get("trace_id") if isinstance(grounding, dict) else "",
        "source_grounding_evidence_count": len(grounding.get("evidence", []) if isinstance(grounding, dict) else []),
        "semantic_available": bool((health.get("semantic_available") if isinstance(health, dict) else False)),
        "memory_item_count": len(memory_result.get("results", []) if isinstance(memory_result, dict) else []),
        "local_memory_capture_count": len(local_captures),
        "project_context_item_count": len(context_items),
        # Backward-compatible counters now refer only to explicit legacy inclusion.
        "project_workspace_context_count": legacy_workspace_context_count if legacy_workspace_included else 0,
        "project_workspace_link_count": legacy_workspace_link_count if legacy_workspace_included else 0,
        "active_project_workspace_id": legacy_workspace.get("active_project_id") if legacy_workspace_included and isinstance(legacy_workspace, dict) else "",
        "surface_context_item_count": len([item for item in context_items if item.get("kind") == "surface_context"]) + (1 if live_surface_text else 0),
        "thread_message_count": len(messages),
        "section_count": len(sections),
        "chars": len(prompt_block),
        "memory_health": health,
        "precedence": ["current_message", "built_in_guides", "active_surface_context", "project_brain", "assistant_scope", "scope_knowledge", "source_grounding", "memory_engine", "thread", "persona", "legacy_project_workspace_explicit_only"],
        "lock": contract_lock_payload(),
    }
    pack = {"ok": True, "sections": sections, "prompt_block": prompt_block, "diagnostics": diagnostics, "source_grounding": grounding}
    run_id = str((session or {}).get("session_id") or session_id or resolved_project_id or "context_pack")
    summary = _assistant_context_log_summary(
        session_id=run_id,
        project_id=resolved_project_id,
        retrieval_profile=retrieval_profile,
        message=message,
        diagnostics=diagnostics,
    )
    try:
        log_surface_event("assistant", "assistant.context_pack.built", run_id=run_id, payload=summary)
        record_surface_snapshot("assistant", "neo_last_context_pack.json", {"diagnostics": diagnostics, "summary": summary}, run_id=run_id)
    except Exception as exc:  # pragma: no cover - logging must never break Assistant context building
        try:
            record_surface_error("assistant", "Failed to record Assistant context pack log.", exc=exc, payload=summary, run_id=run_id)
        except Exception:
            pass
    return pack


def compact_context_messages(context_pack: dict[str, Any]) -> list[dict[str, str]]:
    prompt_block = str(context_pack.get("prompt_block") or "").strip()
    return [{"role": "system", "content": prompt_block}] if prompt_block else []
