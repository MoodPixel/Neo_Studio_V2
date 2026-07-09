from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import json
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT_DIR / "neo_data" / "admin" / "tool_permissions.json"

TOOL_REGISTRY_SCHEMA = "neo.tool_registry.v1"
TOOL_REGISTRY_VERSION = "0.1.0"

RISK_LEVELS = ["read_only", "low", "medium", "high", "external", "blocked"]
PERMISSION_MODES = ["allow", "confirm", "block"]
TOOL_CATEGORIES = ["memory", "assistant", "operator", "admin", "roleplay", "voice", "internet", "surface"]

_CANONICAL_TOOLS: list[dict[str, Any]] = [
    {
        "tool_id": "memory.retrieve",
        "label": "Retrieve Memory Engine context",
        "category": "memory",
        "action_type": "memory_retrieve",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Search indexed memory and return read-only context with citations/traces.",
        "endpoints": ["/api/memory/retrieve", "/api/memory/search-ux"],
    },
    {
        "tool_id": "memory.index",
        "label": "Index Memory Engine source",
        "category": "memory",
        "action_type": "memory_index",
        "risk_level": "medium",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Index or refresh approved Memory Engine sources such as system records, codebase, assistant memory, or roleplay memory.",
        "endpoints": ["/api/memory/index"],
    },
    {
        "tool_id": "memory.inspect",
        "label": "Inspect memory chunk",
        "category": "memory",
        "action_type": "memory_inspect",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Open indexed memory chunks, citations, source paths, and retrieval traces.",
        "endpoints": ["/api/memory/inspect/chunks", "/api/memory/citations/{chunk_id}"],
    },
    {
        "tool_id": "memory.review",
        "label": "Review memory policy state",
        "category": "memory",
        "action_type": "memory_review",
        "risk_level": "medium",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Approve, reject, mark canon/draft, deprecate, archive, or restore indexed memory chunks.",
        "endpoints": ["/api/memory/inspect/review", "/api/memory/canon/promote", "/api/memory/conflicts/resolve"],
    },
    {
        "tool_id": "memory.consolidate",
        "label": "Consolidate memory summaries",
        "category": "memory",
        "action_type": "memory_consolidate",
        "risk_level": "medium",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Create durable summaries or archive originals only when explicitly confirmed.",
        "endpoints": ["/api/memory/consolidation/run"],
    },
    {
        "tool_id": "memory.retention",
        "label": "Apply retention review action",
        "category": "memory",
        "action_type": "memory_retention",
        "risk_level": "medium",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Apply review-gated retention actions. Never deletes memory or silently mutates canon/system memory.",
        "endpoints": ["/api/memory/retention/run"],
    },
    {
        "tool_id": "assistant.context.preview",
        "label": "Preview Assistant context pack",
        "category": "assistant",
        "action_type": "preview_context_pack",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Show the exact project/thread/memory context Assistant would inject.",
        "endpoints": ["/api/assistant/context-pack"],
    },
    {
        "tool_id": "assistant.context.search",
        "label": "Search Assistant context",
        "category": "assistant",
        "action_type": "search_assistant_context",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Search saved Assistant chats, projects, captures, and context cards.",
        "endpoints": ["/api/assistant/search"],
    },
    {
        "tool_id": "assistant.knowledge.save",
        "label": "Save project knowledge",
        "category": "assistant",
        "action_type": "save_project_knowledge",
        "risk_level": "low",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Save text into Assistant project knowledge for future context packs.",
        "endpoints": ["/api/assistant/context-items"],
    },
    {
        "tool_id": "assistant.surface.attach_context",
        "label": "Attach surface context",
        "category": "assistant",
        "action_type": "attach_surface_context",
        "risk_level": "low",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Attach a surface snapshot to Assistant/project context.",
        "endpoints": ["/api/assistant/surface-context"],
    },
    {
        "tool_id": "assistant.source_grounding.preview",
        "label": "Preview grounded answer sources",
        "category": "assistant",
        "action_type": "assistant_source_grounding",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Preview Memory Engine evidence and citations for a source-grounded Assistant answer.",
        "endpoints": ["/api/assistant/source-grounded-answer"],
    },
    {
        "tool_id": "operator.plan",
        "label": "Plan Neo Operator command",
        "category": "operator",
        "action_type": "operator_plan",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Detect intent, select retrieval profile, and plan safe/gated actions.",
        "endpoints": ["/api/operator/plan"],
    },
    {
        "tool_id": "operator.run_safe",
        "label": "Run safe Operator read",
        "category": "operator",
        "action_type": "operator_run_safe",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Run read-only retrieval/context actions through Neo Operator.",
        "endpoints": ["/api/operator/run"],
    },
    {
        "tool_id": "operator.run_confirmed",
        "label": "Run confirmed Operator actions",
        "category": "operator",
        "action_type": "operator_run_confirmed",
        "risk_level": "medium",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Run confirmation-required Operator actions such as indexing or approved external access.",
        "endpoints": ["/api/operator/run"],
    },
    {
        "tool_id": "admin.diagnostics.read",
        "label": "Read Admin diagnostics",
        "category": "admin",
        "action_type": "admin_diagnostics",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Read Admin Control Center, Memory Health, and diagnostics state.",
        "endpoints": ["/api/admin/control-center", "/api/memory/diagnostics"],
    },
    {
        "tool_id": "roleplay.human_memory.read",
        "label": "Read Roleplay human memory",
        "category": "roleplay",
        "action_type": "roleplay_context",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Read roleplay human memory state, canon, continuity, and unresolved threads.",
        "endpoints": ["/api/roleplay/human-memory/state"],
    },
    {
        "tool_id": "roleplay.human_memory.sync",
        "label": "Sync Roleplay scene memory",
        "category": "roleplay",
        "action_type": "roleplay_memory_sync",
        "risk_level": "medium",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Create/update scene memory packets and index roleplay memory when confirmed.",
        "endpoints": ["/api/roleplay/human-memory/sync-scene"],
    },
    {
        "tool_id": "voice.transcribe",
        "label": "Transcribe voice input",
        "category": "voice",
        "action_type": "voice_transcribe",
        "risk_level": "low",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Transcribe local audio into text input for Neo Operator. No voice output.",
        "endpoints": ["/api/voice/input/transcribe"],
    },
    {
        "tool_id": "internet.research",
        "label": "Optional Internet/API research",
        "category": "internet",
        "action_type": "internet_research",
        "risk_level": "external",
        "permission": "confirm",
        "requires_confirmation": True,
        "description": "Use Admin-enabled external providers for web/API context. Disabled by default.",
        "endpoints": ["/api/internet/access/run"],
    },
    {
        "tool_id": "surface.guide",
        "label": "Guide current tab",
        "category": "surface",
        "action_type": "guide_current_tab",
        "risk_level": "read_only",
        "permission": "allow",
        "requires_confirmation": False,
        "description": "Show a lightweight guidance card for the current Neo surface/subtab.",
        "endpoints": [],
    },
]

_PERMISSION_PROFILES: dict[str, dict[str, Any]] = {
    "locked_down": {
        "profile_id": "locked_down",
        "label": "Locked down",
        "description": "Read-only local context only. Blocks writes, indexing, external access, and review actions.",
        "default_mode": "block",
        "category_modes": {"memory": "block", "assistant": "block", "operator": "block", "admin": "allow", "roleplay": "block", "voice": "block", "internet": "block", "surface": "allow"},
        "tool_overrides": {"memory.retrieve": "allow", "memory.inspect": "allow", "operator.plan": "allow", "operator.run_safe": "allow", "admin.diagnostics.read": "allow", "assistant.source_grounding.preview": "allow", "surface.guide": "allow"},
    },
    "local_safe": {
        "profile_id": "local_safe",
        "label": "Local safe",
        "description": "Allows local read/search/preview tools. Confirms indexing and memory writes. Blocks external internet/API access.",
        "default_mode": "confirm",
        "category_modes": {"memory": "confirm", "assistant": "confirm", "operator": "confirm", "admin": "allow", "roleplay": "confirm", "voice": "allow", "internet": "block", "surface": "allow"},
        "tool_overrides": {"memory.retrieve": "allow", "memory.inspect": "allow", "assistant.context.preview": "allow", "assistant.context.search": "allow", "assistant.source_grounding.preview": "allow", "operator.plan": "allow", "operator.run_safe": "allow", "admin.diagnostics.read": "allow", "roleplay.human_memory.read": "allow", "voice.transcribe": "allow", "surface.guide": "allow"},
    },
    "guided": {
        "profile_id": "guided",
        "label": "Guided",
        "description": "Allows read-only tools, requires confirmation for local writes/indexing/review, and confirms external access if Admin internet mode allows it.",
        "default_mode": "confirm",
        "category_modes": {"memory": "confirm", "assistant": "confirm", "operator": "confirm", "admin": "allow", "roleplay": "confirm", "voice": "allow", "internet": "confirm", "surface": "allow"},
        "tool_overrides": {"memory.retrieve": "allow", "memory.inspect": "allow", "assistant.context.preview": "allow", "assistant.context.search": "allow", "assistant.source_grounding.preview": "allow", "operator.plan": "allow", "operator.run_safe": "allow", "admin.diagnostics.read": "allow", "roleplay.human_memory.read": "allow", "voice.transcribe": "allow", "surface.guide": "allow"},
    },
    "power_user": {
        "profile_id": "power_user",
        "label": "Power user",
        "description": "Allows local read/write/indexing tools, still confirms external/internet and high-risk actions.",
        "default_mode": "allow",
        "category_modes": {"internet": "confirm", "memory": "allow", "assistant": "allow", "operator": "allow", "admin": "allow", "roleplay": "allow", "voice": "allow", "surface": "allow"},
        "tool_overrides": {"internet.research": "confirm", "memory.review": "confirm", "memory.retention": "confirm", "operator.run_confirmed": "confirm"},
    },
}

_ACTION_TYPE_TO_TOOL_ID = {
    "memory_retrieve": "memory.retrieve",
    "memory_index": "memory.index",
    "memory_inspect": "memory.inspect",
    "memory_review": "memory.review",
    "internet_research": "internet.research",
    "surface_hint": "surface.guide",
    "ask_for_input": "operator.plan",
    "preview_context_pack": "assistant.context.preview",
    "search_assistant_context": "assistant.context.search",
    "save_project_knowledge": "assistant.knowledge.save",
    "attach_surface_context": "assistant.surface.attach_context",
    "guide_current_tab": "surface.guide",
    "assistant_source_grounding": "assistant.source_grounding.preview",
    "roleplay_context": "roleplay.human_memory.read",
    "voice_transcribe": "voice.transcribe",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def _active_profile_id() -> str:
    data = _read_config()
    profile_id = str(data.get("active_profile_id") or "guided")
    return profile_id if profile_id in _PERMISSION_PROFILES else "guided"


def _custom_tool_modes() -> dict[str, str]:
    data = _read_config()
    raw = data.get("tool_modes")
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items() if str(v) in PERMISSION_MODES}


def canonical_tools() -> list[dict[str, Any]]:
    return deepcopy(_CANONICAL_TOOLS)


def permission_profiles_payload() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_id": "neo.tool_registry.permission_profiles.v1",
        "active_profile_id": _active_profile_id(),
        "profiles": list(deepcopy(_PERMISSION_PROFILES).values()),
        "risk_levels": RISK_LEVELS,
        "permission_modes": PERMISSION_MODES,
        "custom_tool_modes": _custom_tool_modes(),
        "config_path": str(CONFIG_PATH.relative_to(ROOT_DIR)) if CONFIG_PATH.is_relative_to(ROOT_DIR) else str(CONFIG_PATH),
    }


def set_permission_profile_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = payload or {}
    profile_id = str(data.get("profile_id") or data.get("active_profile_id") or "").strip()
    if profile_id not in _PERMISSION_PROFILES:
        raise ValueError("Unknown tool permission profile")
    current = _read_config()
    current["active_profile_id"] = profile_id
    modes = data.get("tool_modes")
    if isinstance(modes, dict):
        current["tool_modes"] = {str(k): str(v) for k, v in modes.items() if str(v) in PERMISSION_MODES}
    current["updated_at"] = _now()
    _write_config(current)
    return permission_profiles_payload()


def _base_mode_for_tool(tool: dict[str, Any], profile: dict[str, Any], custom_modes: dict[str, str]) -> str:
    tool_id = str(tool.get("tool_id") or "")
    if tool_id in custom_modes:
        return custom_modes[tool_id]
    overrides = profile.get("tool_overrides") if isinstance(profile.get("tool_overrides"), dict) else {}
    if tool_id in overrides:
        mode = str(overrides[tool_id])
        if mode in PERMISSION_MODES:
            return mode
    category_modes = profile.get("category_modes") if isinstance(profile.get("category_modes"), dict) else {}
    category = str(tool.get("category") or "")
    if category in category_modes:
        mode = str(category_modes[category])
        if mode in PERMISSION_MODES:
            return mode
    mode = str(profile.get("default_mode") or "confirm")
    return mode if mode in PERMISSION_MODES else "confirm"


def effective_tool_registry_payload() -> dict[str, Any]:
    profile_id = _active_profile_id()
    profile = deepcopy(_PERMISSION_PROFILES[profile_id])
    custom_modes = _custom_tool_modes()
    tools: list[dict[str, Any]] = []
    counts = {"allow": 0, "confirm": 0, "block": 0}
    by_category: dict[str, dict[str, int]] = {}
    for raw in canonical_tools():
        tool = deepcopy(raw)
        mode = _base_mode_for_tool(tool, profile, custom_modes)
        tool["permission_mode"] = mode
        tool["enabled"] = mode != "block"
        tool["requires_confirmation"] = bool(mode == "confirm" or raw.get("requires_confirmation")) if mode != "block" else False
        tool["blocked"] = mode == "block"
        tool["profile_id"] = profile_id
        counts[mode] += 1
        category = str(tool.get("category") or "uncategorized")
        by_category.setdefault(category, {"allow": 0, "confirm": 0, "block": 0, "total": 0})
        by_category[category][mode] += 1
        by_category[category]["total"] += 1
        tools.append(tool)
    return {
        "ok": True,
        "schema_id": TOOL_REGISTRY_SCHEMA,
        "registry_version": TOOL_REGISTRY_VERSION,
        "status": "ready",
        "active_profile_id": profile_id,
        "active_profile": profile,
        "tools": tools,
        "counts": {**counts, "total": len(tools)},
        "by_category": by_category,
        "categories": TOOL_CATEGORIES,
        "risk_levels": RISK_LEVELS,
        "permission_modes": PERMISSION_MODES,
        "policy": "Tool availability is centralized. Assistant and Neo Operator should classify actions through this registry instead of hardcoding permissions per UI panel.",
    }


def tool_for_action_type(action_type: str) -> dict[str, Any] | None:
    tool_id = _ACTION_TYPE_TO_TOOL_ID.get(str(action_type or ""))
    if not tool_id:
        return None
    registry = effective_tool_registry_payload()
    return next((tool for tool in registry.get("tools", []) if tool.get("tool_id") == tool_id), None)


def annotate_action_with_tool_policy(action: dict[str, Any]) -> dict[str, Any]:
    item = deepcopy(action)
    tool = tool_for_action_type(str(item.get("action_type") or ""))
    if not tool:
        item.setdefault("tool_policy", {"tool_id": None, "permission_mode": "confirm", "reason": "unregistered_action_type"})
        item["requires_confirmation"] = True
        return item
    mode = str(tool.get("permission_mode") or "confirm")
    item["tool_id"] = tool.get("tool_id")
    item["tool_label"] = tool.get("label")
    item["tool_category"] = tool.get("category")
    item["tool_policy"] = {
        "tool_id": tool.get("tool_id"),
        "permission_mode": mode,
        "enabled": bool(tool.get("enabled")),
        "risk_level": tool.get("risk_level"),
        "profile_id": tool.get("profile_id"),
    }
    if mode == "block":
        item["status"] = "blocked"
        item["requires_confirmation"] = False
        item["reason"] = item.get("reason") or "blocked_by_tool_permission_profile"
    elif mode == "confirm":
        item["requires_confirmation"] = True
        item.setdefault("risk_level", tool.get("risk_level") or "medium")
    else:
        item["requires_confirmation"] = False
        item.setdefault("risk_level", tool.get("risk_level") or "read_only")
    return item


def tool_registry_status_payload() -> dict[str, Any]:
    registry = effective_tool_registry_payload()
    return {
        "ok": True,
        "schema_id": "neo.tool_registry.status.v1",
        "status": registry.get("status") or "ready",
        "registry_version": TOOL_REGISTRY_VERSION,
        "active_profile_id": registry.get("active_profile_id"),
        "counts": registry.get("counts") or {},
        "categories": registry.get("categories") or [],
        "endpoints": ["/api/tools/registry", "/api/tools/permission-profiles", "/api/tools/permission-profiles/set"],
        "policy": registry.get("policy"),
    }
