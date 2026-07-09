from __future__ import annotations

from typing import Any, Literal

SupportState = Literal[
    "available",
    "experimental_available",
    "planned_gated",
    "provider_gated",
    "unsupported",
]

VALID_STATES = {
    "available",
    "experimental_available",
    "planned_gated",
    "provider_gated",
    "unsupported",
}

PROMPT_TEXT_TOOLS = {
    "prompt_generate": "Prompt generation",
    "prompt_enhance": "Prompt enhance",
    "prompt_rewrite": "Prompt rewrite",
    "prompt_cleanup": "Prompt cleanup",
    "negative_prompt": "Negative prompt generation",
    "text_transform": "Text transformation",
}

CAPTION_TOOLS = {
    "image_captioning": "Single image captioning",
    "result_image_captioning": "Caption from existing result image",
    "batch_captioning": "Batch image captioning",
}

LOCAL_ONLY_TOOLS = {
    "save_prompt": "Save prompt",
    "load_prompt": "Load saved prompt",
    "save_caption": "Save caption",
    "load_caption": "Load saved caption",
    "copy_export": "Copy / export",
    "cross_tab_handoff": "Send to workspace",
    "result_metadata": "Result metadata",
    "replay_payload": "Replay payload readiness",
    "assistant_summary": "Assistant-readable summary",
}

PLANNED_LIBRARY_TOOLS = {
    "character_library": "Character library",
    "caption_components": "Reusable caption components",
    "prompt_history": "Prompt recent work",
    "caption_history": "Caption browser/history",
}


def _as_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _profile_status(profile: dict[str, Any] | None) -> str:
    if not profile:
        return "missing_config"
    if profile.get("enabled") is False:
        return "disabled"
    return str(profile.get("runtime_status") or profile.get("runtime", {}).get("status") or profile.get("profile_status") or "disconnected")


def _profile_flags(profile: dict[str, Any] | None) -> dict[str, bool]:
    flags = (profile or {}).get("capability_flags") or {}
    runtime_caps = (profile or {}).get("runtime", {}).get("capabilities") or {}
    runtime_supports_vision = _as_bool(runtime_caps.get("runtime_supports_vision", runtime_caps.get("supports_vision")), False)
    runtime_supports_captioning = _as_bool(runtime_caps.get("runtime_supports_captioning", runtime_caps.get("supports_captioning")), False)
    effective_supports_vision = _as_bool(flags.get("supports_vision"), False) or runtime_supports_vision
    effective_supports_captioning = _as_bool(flags.get("supports_captioning"), False) or runtime_supports_captioning or runtime_supports_vision
    return {
        "supports_text": _as_bool(flags.get("supports_text", runtime_caps.get("supports_text")), True),
        "supports_vision": effective_supports_vision,
        "supports_captioning": effective_supports_captioning,
        "streaming_enabled": _as_bool(runtime_caps.get("streaming_enabled", flags.get("streaming_enabled")), False),
    }


def _profiles_for_prompt_captioning(backend_payload: dict[str, Any]) -> list[dict[str, Any]]:
    profiles = backend_payload.get("profiles") or []
    return [
        profile
        for profile in profiles
        if profile.get("surface") in {"prompt_captioning", "text"}
    ]


def _default_profile(backend_payload: dict[str, Any], *, vision: bool = False) -> dict[str, Any] | None:
    profiles = _profiles_for_prompt_captioning(backend_payload)
    defaults = backend_payload.get("defaults") or {}
    wanted_ids = [defaults.get("prompt_captioning"), defaults.get("text")]
    if vision:
        for profile in profiles:
            flags = _profile_flags(profile)
            if profile.get("enabled") is not False and flags["supports_vision"] and flags["supports_captioning"]:
                return profile
    for profile_id in wanted_ids:
        if profile_id:
            match = next((profile for profile in profiles if profile.get("profile_id") == profile_id), None)
            if match:
                return match
    return next((profile for profile in profiles if profile.get("enabled") is not False), None) or (profiles[0] if profiles else None)


def _provider_reason(profile: dict[str, Any] | None, required: str) -> tuple[SupportState, str]:
    if not profile:
        return "provider_gated", "No Text Backend Profile is configured for Prompt & Captioning."
    if profile.get("enabled") is False:
        return "provider_gated", f"Backend profile '{profile.get('profile_id')}' is disabled."
    flags = _profile_flags(profile)
    if required == "text" and not flags["supports_text"]:
        return "provider_gated", f"Backend profile '{profile.get('profile_id')}' does not declare text support."
    if required == "caption" and not (flags["supports_vision"] and flags["supports_captioning"]):
        return "provider_gated", "Selected backend profile has no detected vision/caption support. Load a KoboldCpp vision model/mmproj or enable Vision + Caption flags."
    status = _profile_status(profile)
    if status in {"offline", "missing_config", "error"}:
        return "provider_gated", f"Backend profile '{profile.get('profile_id')}' is {status}."
    # V2 keeps auto-connect off by default; disconnected local profiles can be configured but not actively proven.
    if status in {"disconnected", "enabled"}:
        return "experimental_available", f"Backend profile '{profile.get('profile_id')}' is configured but not connected/tested yet."
    return "available", f"Backend profile '{profile.get('profile_id')}' supports this route."


def _entry(tool_id: str, label: str, state: SupportState, reason: str, *, mode: str, provider_id: str | None = None, profile_id: str | None = None, required_assets: list[str] | None = None) -> dict[str, Any]:
    return {
        "tool_id": tool_id,
        "label": label,
        "mode": mode,
        "state": state,
        "reason": reason,
        "provider_id": provider_id or "local",
        "backend_profile_id": profile_id or "",
        "required_assets": required_assets or [],
    }


def get_support_matrix(backend_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return route-aware support for Prompt & Captioning.

    This matrix is capability-based. It does not execute tools, but it can use
    backend runtime capability detection to unlock Caption Studio when KoboldCpp
    reports a loaded vision/mmproj route.
    """
    backend_payload = backend_payload or {"profiles": [], "defaults": {}}
    text_profile = _default_profile(backend_payload, vision=False)
    caption_profile = _default_profile(backend_payload, vision=True)
    text_state, text_reason = _provider_reason(text_profile, "text")
    caption_state, caption_reason = _provider_reason(caption_profile, "caption")

    tools: list[dict[str, Any]] = []
    for tool_id, label in PROMPT_TEXT_TOOLS.items():
        tools.append(_entry(tool_id, label, text_state, text_reason, mode="prompt_builder", provider_id=(text_profile or {}).get("provider_id"), profile_id=(text_profile or {}).get("profile_id")))
    for tool_id, label in CAPTION_TOOLS.items():
        required_assets = ["image"] if tool_id != "batch_captioning" else ["image_batch"]
        tools.append(_entry(tool_id, label, caption_state, caption_reason, mode="captioning", provider_id=(caption_profile or {}).get("provider_id"), profile_id=(caption_profile or {}).get("profile_id"), required_assets=required_assets))
    for tool_id, label in LOCAL_ONLY_TOOLS.items():
        tools.append(_entry(tool_id, label, "available", "Local Prompt/Captioning library and handoff routes are migrated.", mode="library"))
    for tool_id, label in PLANNED_LIBRARY_TOOLS.items():
        tools.append(_entry(tool_id, label, "available", "Library, history, and component behavior is available in the Prompt/Captioning surface.", mode="library"))

    by_state: dict[str, int] = {state: 0 for state in VALID_STATES}
    for item in tools:
        by_state[item["state"]] = by_state.get(item["state"], 0) + 1

    return {
        "surface_id": "prompt_captioning",
        "states": sorted(VALID_STATES),
        "profiles": {
            "text_default": (text_profile or {}).get("profile_id", ""),
            "caption_default": (caption_profile or {}).get("profile_id", ""),
        },
        "tools": tools,
        "summary": by_state,
        "rules": [
            "Text-only providers may run prompt tools only.",
            "Image captioning requires explicit Vision + Caption flags or detected KoboldCpp runtime vision support.",
            "Disabled/offline/missing providers are provider_gated.",
            "Prompt/Captioning metadata records are replay-ready but do not enable Assistant memory behavior.",
        ],
    }
