from __future__ import annotations

import re
from typing import Any
from uuid import uuid4

from neo_app.operator.contracts import ACTION_REQUEST_SCHEMA, normalize_action

ACTION_PLANNER_SCHEMA = "neo.control_center.action_planner.phase13.v1"

# Compatibility-only language classification lives in Control Center, never in
# Operator. It translates free text into an explicit action request. Normal
# Assistant chat remains governed by the universal COMPLETE/RECALL/... contract.
_INTENT_PATTERNS: list[tuple[str, float, list[str]]] = [
    ("code_lookup", 0.86, ["where is", "which file", "what file", "code", "function", "route", "api", "implemented", "controls", "owns"]),
    ("admin_diagnostic", 0.82, ["admin", "memory engine", "engine", "backend", "model path", "chroma", "reranker", "embedding", "health", "diagnostic"]),
    ("roleplay_context", 0.82, ["roleplay", "scene", "canon", "character", "relationship", "continuity", "rp"]),
    ("memory_lookup", 0.78, ["remember", "memory", "records", "what did we", "phase", "decision", "changelog", "system record"]),
    ("creator_workflow", 0.70, ["prompt", "caption", "image", "workflow", "preset", "asset", "project"]),
]

_SOURCE_BY_INTENT: dict[str, list[str]] = {
    "code_lookup": ["neo_codebase", "system_records"],
    "admin_diagnostic": ["admin_config", "system_records", "neo_codebase"],
    "roleplay_context": ["roleplay_memory", "system_records"],
    "memory_lookup": ["system_records", "assistant_memory"],
    "creator_workflow": ["assistant_memory", "prompt_libraries", "system_records"],
    "general": ["system_records", "assistant_memory"],
}
_PROFILE_BY_INTENT = {
    "code_lookup": "code_audit",
    "admin_diagnostic": "admin_diagnostic",
    "roleplay_context": "roleplay_runtime",
    "memory_lookup": "assistant_project",
    "creator_workflow": "creator_workflow",
    "general": "smart",
}
_INDEXABLE_SOURCE_HINTS = {
    "system_records": ["system", "records", "docs", "changelog"],
    "neo_codebase": ["code", "codebase", "repo", "files", "python", "javascript", "css"],
    "assistant_memory": ["assistant"],
    "roleplay_memory": ["roleplay", "rp", "scene", "canon"],
}
_EXTERNAL_MUTATION_RE = re.compile(
    r"\b(?:send|delete|remove|archive|upload|publish|post|schedule|create|update|change)\b.*\b(?:email|message|file|event|calendar|post|task|record)\b",
    re.I,
)


def _clean(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip())


def _detect_compat_intent(text: str) -> dict[str, Any]:
    clean = _clean(text)
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
            scores.append((min(0.98, base + (len(matched) - 1) * 0.03), intent, matched))
    if not scores:
        return {"intent": "general", "confidence": 0.58, "matched_terms": []}
    scores.sort(reverse=True)
    score, intent, matched = scores[0]
    return {"intent": intent, "confidence": round(score, 3), "matched_terms": matched}


def _selected_index_sources(text: str) -> list[str]:
    lower = text.lower()
    matches = [source_id for source_id, hints in _INDEXABLE_SOURCE_HINTS.items() if any(hint in lower for hint in hints)]
    return matches or ["system_records"]


def plan_control_center_actions(payload: dict[str, Any] | None = None, *, compatibility_read_fallback: bool = False) -> dict[str, Any]:
    data = payload if isinstance(payload, dict) else {}
    command = _clean(data.get("command") or data.get("message") or data.get("text") or "")
    explicit = data.get("actions") if isinstance(data.get("actions"), list) else []
    if explicit:
        actions = [normalize_action(item, requested_by="control_center") for item in explicit if isinstance(item, dict)]
        detection = {"intent": str(data.get("intent") or "assistant.act"), "confidence": 1.0, "matched_terms": ["structured_actions"]}
        profile = str(data.get("profile") or data.get("retrieval_profile") or "smart")
        sources = data.get("sources") if isinstance(data.get("sources"), list) else []
    else:
        detection = _detect_compat_intent(command)
        intent = detection["intent"]
        profile = str(data.get("profile") or data.get("retrieval_profile") or _PROFILE_BY_INTENT.get(intent) or "smart")
        sources = data.get("sources")
        if isinstance(sources, str):
            sources = [sources]
        if not isinstance(sources, list) or not sources:
            sources = list(_SOURCE_BY_INTENT.get(intent, _SOURCE_BY_INTENT["general"]))
        actions: list[dict[str, Any]] = []
        if intent == "empty":
            actions.append(normalize_action({"action_type": "ask_for_input", "label": "Ask for a clear action", "effect_class": "advisory", "requires_confirmation": False, "payload": {"reason": "empty_command"}}, requested_by="control_center"))
        elif intent == "index_memory":
            if compatibility_read_fallback:
                actions.append(normalize_action({"action_type": "memory_retrieve", "label": f"Retrieve context with {profile}", "effect_class": "read", "requires_confirmation": False, "payload": {"query": command, "profile": profile, "sources": sources, "limit": int(data.get("limit") or 8)}}, requested_by="control_center"))
            for source_id in _selected_index_sources(command):
                actions.append(normalize_action({"action_type": "memory_index", "label": f"Index {source_id.replace('_', ' ')}", "effect_class": "write", "requires_confirmation": True, "payload": {"source_id": source_id}}, requested_by="control_center"))
        elif intent == "internet_research":
            from neo_app.internet.service import plan_internet_access_payload
            internet_plan = plan_internet_access_payload({"query": command, "provider_type": data.get("provider_type") or "search_api"})
            actions.append(normalize_action({"action_type": "internet_research", "label": "Use optional Internet/API access", "effect_class": "external", "requires_confirmation": True, "payload": {"query": command, "internet_plan": internet_plan}}, requested_by="control_center"))
        elif _EXTERNAL_MUTATION_RE.search(command):
            actions.append(normalize_action({"action_type": "unsupported_action", "label": "Requested external action is not registered", "effect_class": "write", "requires_confirmation": True, "payload": {"command": command, "reason": "no_registered_tool_for_requested_action"}}, requested_by="control_center"))
        elif compatibility_read_fallback:
            actions.append(normalize_action({"action_type": "memory_retrieve", "label": f"Retrieve context with {profile}", "effect_class": "read", "requires_confirmation": False, "payload": {"query": command, "profile": profile, "sources": sources, "limit": int(data.get("limit") or 8)}}, requested_by="control_center"))
            if intent in {"code_lookup", "admin_diagnostic"}:
                actions.append(normalize_action({"action_type": "surface_hint", "label": "Open Admin diagnostics", "effect_class": "advisory", "requires_confirmation": False, "payload": {"surface": "admin", "subtab": "engine"}}, requested_by="control_center"))
            elif intent == "roleplay_context":
                actions.append(normalize_action({"action_type": "surface_hint", "label": "Open Roleplay continuity", "effect_class": "advisory", "requires_confirmation": False, "payload": {"surface": "roleplay", "subtab": "scene"}}, requested_by="control_center"))

    intent = str(detection.get("intent") or data.get("intent") or "assistant.act")
    return {
        "schema_id": ACTION_REQUEST_SCHEMA,
        "planner_schema_id": ACTION_PLANNER_SCHEMA,
        "request_id": str(data.get("request_id") or f"actreq_{uuid4().hex[:14]}"),
        "requested_by": "assistant_control_center",
        "actor": str(data.get("actor") or "assistant"),
        "surface": str(data.get("surface") or data.get("surface_id") or "assistant"),
        "scope_id": str(data.get("scope_id") or "general"),
        "project_id": str(data.get("project_id") or data.get("delivery_project_id") or ""),
        "trace_id": str(data.get("trace_id") or ""),
        "command": command,
        "intent": intent,
        "confidence": float(detection.get("confidence") or 0.0),
        "matched_terms": list(detection.get("matched_terms") or []),
        "retrieval_profile": profile,
        "sources": list(sources or []),
        "actions": actions,
        "status": "planned" if actions else "no_executable_action",
        "policy": "Assistant/Control Center decides what actions are requested. Operator receives only this structured request and does not classify general human intent.",
        "metadata": {"compatibility_read_fallback": bool(compatibility_read_fallback)},
    }
