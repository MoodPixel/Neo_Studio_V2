from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4
import re

from neo_app.memory.service import get_memory_service
from neo_app.internet.service import internet_access_status_payload, plan_internet_access_payload, run_internet_access_payload
from neo_app.tool_registry import annotate_action_with_tool_policy, tool_registry_status_payload
from neo_app.tool_ledger import record_tool_ledger_event, tool_ledger_status_payload

OPERATOR_SCHEMA_VERSION = "neo.operator.v1"
OPERATOR_RUNTIME_VERSION = "0.4.0"

_INTENT_PATTERNS: list[tuple[str, float, list[str]]] = [
    ("code_lookup", 0.86, ["where is", "which file", "what file", "code", "function", "route", "api", "implemented", "controls", "owns"]),
    ("admin_diagnostic", 0.82, ["admin", "memory engine", "engine", "backend", "model path", "chroma", "reranker", "embedding", "health", "diagnostic"]),
    ("roleplay_context", 0.82, ["roleplay", "scene", "canon", "character", "relationship", "continuity", "rp", "story"]),
    ("memory_lookup", 0.78, ["remember", "memory", "records", "what did we", "phase", "decision", "changelog", "system record"]),
    ("index_memory", 0.74, ["index", "reindex", "refresh memory", "scan records", "scan codebase", "update memory"]),
    ("creator_workflow", 0.7, ["prompt", "caption", "image", "workflow", "preset", "asset", "project"]),
    ("internet_research", 0.76, ["internet", "web", "search online", "research online", "lookup online", "api", "current", "latest"]),
]

_SOURCE_BY_INTENT: dict[str, list[str]] = {
    "code_lookup": ["neo_codebase", "system_records"],
    "admin_diagnostic": ["admin_config", "system_records", "neo_codebase"],
    "roleplay_context": ["roleplay_memory", "system_records"],
    "memory_lookup": ["system_records", "assistant_memory"],
    "index_memory": ["system_records", "neo_codebase", "assistant_memory", "roleplay_memory"],
    "creator_workflow": ["assistant_memory", "prompt_libraries", "system_records"],
    "internet_research": ["system_records", "assistant_memory"],
    "general": ["system_records", "assistant_memory"],
}

_PROFILE_BY_INTENT: dict[str, str] = {
    "code_lookup": "code_audit",
    "admin_diagnostic": "admin_diagnostic",
    "roleplay_context": "roleplay_runtime",
    "memory_lookup": "assistant_project",
    "index_memory": "admin_diagnostic",
    "creator_workflow": "creator_workflow",
    "internet_research": "smart",
    "general": "smart",
}

_INDEXABLE_SOURCE_HINTS: dict[str, list[str]] = {
    "system_records": ["system", "records", "docs", "changelog"],
    "neo_codebase": ["code", "codebase", "repo", "files", "python", "javascript", "css"],
    "assistant_memory": ["assistant"],
    "roleplay_memory": ["roleplay", "rp", "scene", "canon"],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _detect_intent(text: str) -> dict[str, Any]:
    clean = _normalize_text(text)
    lower = clean.lower()
    if not clean:
        return {"intent": "empty", "confidence": 0.0, "matched_terms": []}
    if re.search(r"\b(reindex|index|refresh memory|scan records|scan codebase|update memory)\b", lower):
        return {"intent": "index_memory", "confidence": 0.92, "matched_terms": ["index"]}
    if re.search(r"\b(search online|research online|look up online|lookup online|internet|web search|latest|current)\b", lower):
        return {"intent": "internet_research", "confidence": 0.88, "matched_terms": ["internet"]}
    scores: list[tuple[float, str, list[str]]] = []
    for intent, base, terms in _INTENT_PATTERNS:
        matched = [term for term in terms if term in lower]
        if matched:
            score = min(0.98, base + (len(matched) - 1) * 0.03)
            scores.append((score, intent, matched))
    if not scores:
        return {"intent": "general", "confidence": 0.58, "matched_terms": []}
    scores.sort(reverse=True)
    score, intent, matched = scores[0]
    return {"intent": intent, "confidence": round(score, 3), "matched_terms": matched}


def _selected_index_sources(text: str) -> list[str]:
    lower = text.lower()
    matches: list[str] = []
    for source_id, hints in _INDEXABLE_SOURCE_HINTS.items():
        if any(hint in lower for hint in hints):
            matches.append(source_id)
    return matches or ["system_records"]


def _action(action_type: str, label: str, *, risk: str = "low", requires_confirmation: bool = False, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "action_id": f"op_act_{uuid4().hex[:12]}",
        "action_type": action_type,
        "label": label,
        "risk_level": risk,
        "requires_confirmation": bool(requires_confirmation),
        "payload": payload or {},
        "status": "planned",
    }


def operator_status_payload() -> dict[str, Any]:
    memory = get_memory_service().memory_engine_status()
    internet = internet_access_status_payload()
    tools = tool_registry_status_payload()
    return {
        "schema_id": "neo.operator.status.v1",
        "status": "ready",
        "label": "Neo Operator",
        "runtime_version": OPERATOR_RUNTIME_VERSION,
        "input_modes": ["text", "voice_transcribed_text"],
        "future_input_modes": ["microphone_capture"],
        "output_modes": ["text", "action_plan", "memory_context"],
        "future_output_modes": ["image", "video", "voice"],
        "permission_policy": {
            "local_read": "allowed",
            "memory_retrieve": "allowed",
            "memory_index": "confirmation_required",
            "write_actions": "confirmation_required",
            "internet": internet.get("mode") or "disabled",
            "voice": "voice_input_ready",
            "external_actions": internet.get("permission_policy", {}).get("default", "disabled"),
            "tool_profile": tools.get("active_profile_id"),
        },
        "capabilities": [
            "intent_detection",
            "memory_engine_retrieval",
            "codebase_lookup_planning",
            "admin_diagnostic_planning",
            "roleplay_context_planning",
            "safe_action_gating",
            "operator_memory_writeback",
            "voice_transcript_command_bridge",
            "optional_internet_api_planning",
            "central_tool_registry",
            "permission_profiles",
            "tool_execution_ledger",
        ],
        "tool_execution_ledger": tool_ledger_status_payload(),
        "internet_access": {"status": internet.get("status"), "mode": internet.get("mode"), "provider_count": (internet.get("capabilities") or {}).get("provider_count", 0), "enabled_provider_count": (internet.get("capabilities") or {}).get("enabled_provider_count", 0)},
        "tool_registry": tools,
        "memory_engine": {
            "status": memory.get("status"),
            "document_count": (memory.get("stats") or {}).get("document_count", 0),
            "chunk_count": (memory.get("stats") or {}).get("chunk_count", 0),
            "retrieval_profiles": len((memory.get("retrieval_profiles") or {}).get("profiles") or []),
        },
        "policy": "Neo Operator is text-first, local-first, tool-registry-gated, voice-input-ready, optionally internet/API-ready, and uses Memory Engine as its context spine.",
    }


def plan_operator_command(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    command = _normalize_text(data.get("command") or data.get("text") or data.get("message") or "")
    detection = _detect_intent(command)
    intent = detection["intent"]
    profile = str(data.get("profile") or _PROFILE_BY_INTENT.get(intent) or "smart")
    sources = data.get("sources")
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list) or not sources:
        sources = _SOURCE_BY_INTENT.get(intent, _SOURCE_BY_INTENT["general"])
    actions: list[dict[str, Any]] = []
    if intent == "empty":
        actions.append(_action("ask_for_input", "Ask for a clear command", payload={"reason": "empty_command"}))
    else:
        actions.append(_action("memory_retrieve", f"Retrieve context with {profile}", payload={"query": command, "profile": profile, "sources": sources, "limit": int(data.get("limit") or 8)}))
        if intent == "index_memory":
            index_sources = _selected_index_sources(command)
            for source_id in index_sources:
                actions.append(_action("memory_index", f"Index {source_id.replace('_', ' ')}", risk="medium", requires_confirmation=True, payload={"source_id": source_id}))
        if intent in {"code_lookup", "admin_diagnostic"}:
            actions.append(_action("surface_hint", "Suggest opening Admin Memory Engine diagnostics", payload={"surface": "admin", "subtab": "memory"}))
        if intent == "roleplay_context":
            actions.append(_action("surface_hint", "Suggest checking Roleplay human memory state", payload={"surface": "roleplay", "subtab": "scene"}))
        if intent == "internet_research":
            internet_plan = plan_internet_access_payload({"query": command, "provider_type": data.get("provider_type") or "search_api"})
            permission = internet_plan.get("permission_summary") or {}
            internet_action = _action("internet_research", "Use optional Internet/API access for external context", risk="medium", requires_confirmation=bool(permission.get("confirmation_required", True) or not permission.get("allowed", False)), payload={"query": command, "internet_plan": internet_plan})
            if not permission.get("allowed", False):
                internet_action["status"] = "blocked"
                internet_action["reason"] = permission.get("reason") or "internet_access_not_allowed"
            actions.append(internet_action)
    actions = [annotate_action_with_tool_policy(action) for action in actions]
    for action in actions:
        try:
            record_tool_ledger_event({
                "actor": str(data.get("actor") or "operator"),
                "surface": str(data.get("surface") or "assistant"),
                "intent": intent,
                "status": "planned" if action.get("status") != "blocked" else "blocked",
                "blocked": action.get("status") == "blocked",
                "confirmed": False,
                "action": action,
                "payload": {"command": command, "profile": profile, "sources": sources},
                "result_summary": f"Planned {action.get('action_type')} for intent {intent}",
                "metadata": {"phase": "25", "source": "operator.plan"},
            })
        except Exception:
            pass
    return {
        "ok": True,
        "schema_id": "neo.operator.plan.v1",
        "operator_version": OPERATOR_RUNTIME_VERSION,
        "status": "planned" if intent != "empty" else "needs_input",
        "command": command,
        "intent": intent,
        "confidence": detection["confidence"],
        "matched_terms": detection.get("matched_terms") or [],
        "retrieval_profile": profile,
        "sources": sources,
        "actions": actions,
        "tool_registry": tool_registry_status_payload(),
        "permission_summary": {
            "safe_read_actions": sum(1 for item in actions if not item.get("requires_confirmation") and item.get("status") != "blocked"),
            "confirmation_required": sum(1 for item in actions if item.get("requires_confirmation")),
            "blocked": sum(1 for item in actions if item.get("status") == "blocked"),
        },
    }


def _compact_result(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": item.get("title") or item.get("source_path") or "Memory result",
        "source_id": item.get("source_id"),
        "source_path": item.get("source_path"),
        "start_line": item.get("start_line"),
        "end_line": item.get("end_line"),
        "score": item.get("score"),
        "retrieval_type": item.get("retrieval_type"),
        "snippet": item.get("snippet") or item.get("summary") or "",
        "memory_state": item.get("memory_state"),
        "trust_level": item.get("trust_level"),
    }


def run_operator_command(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    plan = plan_operator_command(data)
    execute_confirmed = bool(data.get("execute_confirmed") or data.get("confirm"))
    memory = get_memory_service()
    executed: list[dict[str, Any]] = []
    retrieval_payload = next((item.get("payload") for item in plan.get("actions", []) if item.get("action_type") == "memory_retrieve"), None)
    retrieval = None
    if retrieval_payload:
        retrieval = memory.retrieve({**retrieval_payload, "consumer": "operator"})
        executed.append({"action_type": "memory_retrieve", "status": "completed", "trace_id": retrieval.get("trace_id"), "result_count": len(retrieval.get("results") or [])})
        try:
            action = next((a for a in plan.get("actions", []) if a.get("action_type") == "memory_retrieve"), {})
            record_tool_ledger_event({"actor": str(data.get("actor") or "operator"), "surface": str(data.get("surface") or "assistant"), "intent": plan.get("intent"), "status": "executed", "confirmed": False, "action": action, "memory_trace_id": retrieval.get("trace_id"), "result_summary": f"Retrieved {len(retrieval.get('results') or [])} memory result(s)", "payload": retrieval_payload, "metadata": {"phase": "25", "source": "operator.run"}})
        except Exception:
            pass
    blocked: list[dict[str, Any]] = []
    external_context = None
    for action in plan.get("actions", []):
        if action.get("action_type") != "memory_index":
            continue
        if not execute_confirmed:
            blocked_action = {**action, "status": "blocked", "reason": "confirmation_required"}
            blocked.append(blocked_action)
            try:
                record_tool_ledger_event({"actor": str(data.get("actor") or "operator"), "surface": str(data.get("surface") or "assistant"), "intent": plan.get("intent"), "status": "blocked", "blocked": True, "confirmed": False, "action": blocked_action, "result_summary": "Blocked until confirmation", "metadata": {"phase": "25", "source": "operator.run"}})
            except Exception:
                pass
            continue
        source_id = str((action.get("payload") or {}).get("source_id") or "system_records")
        index_result = memory.index_source(source_id, force=bool(data.get("force", False)), limit=data.get("index_limit"))
        executed.append({"action_type": "memory_index", "source_id": source_id, "status": index_result.get("status"), "indexed_documents": index_result.get("indexed_documents", 0), "indexed_chunks": index_result.get("indexed_chunks", 0)})
        try:
            record_tool_ledger_event({"actor": str(data.get("actor") or "operator"), "surface": str(data.get("surface") or "assistant"), "intent": plan.get("intent"), "status": "executed", "confirmed": True, "action": action, "payload": {"source_id": source_id}, "result_summary": f"Indexed {index_result.get('indexed_documents', 0)} document(s) / {index_result.get('indexed_chunks', 0)} chunk(s)", "metadata": {"phase": "25", "source": "operator.run", "index_result": index_result}})
        except Exception:
            pass
    for action in plan.get("actions", []):
        if action.get("action_type") != "internet_research":
            continue
        if action.get("status") == "blocked":
            blocked_action = {**action, "status": "blocked", "reason": action.get("reason") or "internet_access_blocked"}
            blocked.append(blocked_action)
            try:
                record_tool_ledger_event({"actor": str(data.get("actor") or "operator"), "surface": str(data.get("surface") or "assistant"), "intent": plan.get("intent"), "status": "blocked", "blocked": True, "action": blocked_action, "result_summary": blocked_action.get("reason"), "metadata": {"phase": "25", "source": "operator.internet"}})
            except Exception:
                pass
            continue
        if action.get("requires_confirmation") and not execute_confirmed:
            blocked_action = {**action, "status": "blocked", "reason": "confirmation_required"}
            blocked.append(blocked_action)
            try:
                record_tool_ledger_event({"actor": str(data.get("actor") or "operator"), "surface": str(data.get("surface") or "assistant"), "intent": plan.get("intent"), "status": "blocked", "blocked": True, "action": blocked_action, "result_summary": "Internet/API action requires confirmation", "metadata": {"phase": "25", "source": "operator.internet"}})
            except Exception:
                pass
            continue
        external_context = run_internet_access_payload({"query": plan.get("command"), "provider_type": data.get("provider_type") or "search_api", "execute_confirmed": execute_confirmed})
        if external_context and external_context.get("status") == "blocked":
            blocked.append({**action, "status": "blocked", "reason": external_context.get("reason") or "internet_access_blocked"})
        else:
            executed.append({"action_type": "internet_research", "status": external_context.get("status"), "ok": external_context.get("ok"), "reason": external_context.get("reason")})
            try:
                record_tool_ledger_event({"actor": str(data.get("actor") or "operator"), "surface": str(data.get("surface") or "assistant"), "intent": plan.get("intent"), "status": "executed", "confirmed": execute_confirmed, "action": action, "payload": {"query": plan.get("command")}, "result_summary": f"Internet/API context status: {external_context.get('status')}", "metadata": {"phase": "25", "source": "operator.internet", "external_context": external_context}})
            except Exception:
                pass
    results = [_compact_result(item) for item in ((retrieval or {}).get("results") or [])]
    response_text = _operator_response_text(plan, results, blocked)
    if external_context:
        response_text += f"\nExternal context: {external_context.get('status')}"
    event = memory.record_event({
        "namespace": "operator",
        "surface": "assistant",
        "source": "assistant",
        "event_type": "operator.command.ran",
        "title": "Operator command ran",
        "summary": response_text[:900],
        "tags": ["operator", plan.get("intent"), plan.get("retrieval_profile")],
        "payload": {"command": plan.get("command"), "intent": plan.get("intent"), "plan": plan, "executed": executed, "blocked": blocked, "external_context_status": (external_context or {}).get("status"), "result_count": len(results)},
        "importance": "normal",
        "should_embed": True,
    })
    return {
        "ok": True,
        "schema_id": "neo.operator.run.v1",
        "operator_version": OPERATOR_RUNTIME_VERSION,
        "status": "completed" if not blocked else "completed_with_blocked_actions",
        "plan": plan,
        "retrieval": retrieval,
        "external_context": external_context,
        "results": results,
        "executed_actions": executed,
        "blocked_actions": blocked,
        "response_text": response_text,
        "memory_event": event.get("event"),
    }


def _operator_response_text(plan: dict[str, Any], results: list[dict[str, Any]], blocked: list[dict[str, Any]]) -> str:
    command = plan.get("command") or ""
    intent = plan.get("intent") or "general"
    if intent == "empty":
        return "Give Neo a command first."
    lines = [f"Intent: {intent} · profile: {plan.get('retrieval_profile')}"]
    if results:
        top = results[0]
        lines.append(f"Top context: {top.get('title')} ({top.get('source_path') or 'memory'})")
        if top.get("snippet"):
            lines.append(str(top.get("snippet"))[:260])
    else:
        lines.append("No strong memory context found yet. Index relevant sources or rephrase the command.")
    if blocked:
        lines.append(f"{len(blocked)} action(s) need confirmation before Neo changes/indexes anything.")
    return "\n".join(lines)
