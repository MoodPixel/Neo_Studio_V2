from __future__ import annotations

import json
import re
from typing import Any

ASSISTANT_CONTRACT_VERSION = "assistant_v2_wave5_memory_engine_1"
ASSISTANT_REQUIRED_SUBTABS = {"chat", "projects", "memory", "context", "tools", "guide", "validation", "inspector"}
ASSISTANT_RETRIEVAL_PROFILES = {"fast", "smart", "deep"}
ASSISTANT_SAFE_SURFACES = {
    "assistant",
    "admin",
    "image",
    "video",
    "voice",
    "prompt_captioning",
    "roleplay",
}
ASSISTANT_SAFE_ACTIONS = {"explain", "improve", "debug", "plan", "guide", "summarize", "save", "review"}
ASSISTANT_BLOCKED_TOOL_INTENTS = {
    "run_shell",
    "shell",
    "cmd",
    "powershell",
    "terminal",
    "exec",
    "delete_file",
    "rm_rf",
    "patch_apply",
    "apply_patch",
    "external_connector",
    "destructive_file_operation",
}
MAX_CONTEXT_TEXT_CHARS = 24000
MAX_SURFACE_PAYLOAD_CHARS = 16000
MAX_TITLE_CHARS = 180
MAX_SUMMARY_CHARS = 2200


def contract_lock_payload() -> dict[str, Any]:
    """Public Assistant lock metadata returned by bootstrap and diagnostics."""
    return {
        "contract_version": ASSISTANT_CONTRACT_VERSION,
        "locked": True,
        "required_subtabs": sorted(ASSISTANT_REQUIRED_SUBTABS),
        "retrieval_profiles": sorted(ASSISTANT_RETRIEVAL_PROFILES),
        "safe_surfaces": sorted(ASSISTANT_SAFE_SURFACES),
        "safe_actions": sorted(ASSISTANT_SAFE_ACTIONS),
        "blocked_tool_intents": sorted(ASSISTANT_BLOCKED_TOOL_INTENTS),
        "memory_engine_owner": True,
        "admin_engine_memory_owner": True,  # legacy compatibility alias
        "assistant_memory_engine_integration": True,
        "assistant_is_user_facing_layer": True,
    }


def clamp_retrieval_profile(value: Any, default: str = "smart") -> str:
    profile = str(value or "").strip().lower()
    return profile if profile in ASSISTANT_RETRIEVAL_PROFILES else default


def normalize_surface_id(value: Any, default: str = "assistant") -> str:
    surface = re.sub(r"[^a-z0-9_\-]+", "", str(value or "").strip().lower().replace("-", "_"))[:80]
    return surface or default


def normalize_suggested_action(value: Any, default: str = "explain") -> str:
    action = re.sub(r"[^a-z0-9_\-]+", "", str(value or "").strip().lower().replace("-", "_"))[:64]
    return action if action in ASSISTANT_SAFE_ACTIONS else default


def trim_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def compact_json_payload(value: Any, limit: int = MAX_SURFACE_PAYLOAD_CHARS) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        encoded = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return {"_error": "payload_not_serializable"}
    if len(encoded) <= limit:
        return value
    return {
        "_truncated": True,
        "_original_chars": len(encoded),
        "preview": encoded[:limit],
    }


def is_blocked_tool_id(tool_id: Any) -> bool:
    normalized = normalize_surface_id(tool_id, default="")
    return normalized in ASSISTANT_BLOCKED_TOOL_INTENTS
