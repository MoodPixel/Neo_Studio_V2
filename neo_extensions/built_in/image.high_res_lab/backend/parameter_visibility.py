from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import ACTIVE_ROUTE_STATES, DEFAULT_PARAMS
from .support_matrix import route_state
from .route_profiles import route_profile_summary

CONTROL_IDS = [
    "enabled",
    "profile",
    "mode",
    "resize_method",
    "scale",
    "steps",
    "denoise",
    "cfg",
    "sampler",
    "scheduler",
    "upscaler",
    "tiled_vae",
    "tile_size",
    "tile_overlap",
    "ultimate_sd_upscale",
]


def _optional_group_state(optional_capabilities: dict[str, Any] | None, group_id: str) -> str:
    groups = (optional_capabilities or {}).get("groups") if isinstance(optional_capabilities, dict) else {}
    group = groups.get(group_id) if isinstance(groups, dict) else None
    if isinstance(group, dict):
        return str(group.get("state") or "unknown_not_checked")
    return "unknown_not_checked"


def parameter_visibility(
    params: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    display_mode: str = "guided",
    optional_capabilities: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    params = {**DEFAULT_PARAMS, **(params or {})}
    state = route_state(route)
    active = route is None or state in ACTIVE_ROUTE_STATES
    compact = display_mode == "compact"
    expert = display_mode == "expert"
    provider_gated = state in {"implementation_target", "planned_gated", "provider_gated"}
    normal_visible = active or (expert and provider_gated)
    mode = "image_upscale" if params.get("strategy") == "qwen_reedit" else str(params.get("mode") or DEFAULT_PARAMS["mode"])
    tiled_requested = bool(params.get("tiled_vae")) or params.get("strategy") == "ultimate_sd_upscale"
    model_state = _optional_group_state(optional_capabilities, "model_upscale")
    tiled_state = _optional_group_state(optional_capabilities, "tiled_vae_decode")
    ultimate_state = _optional_group_state(optional_capabilities, "ultimate_sd_upscale")
    profile = route_profile_summary(route)
    blocked_strategies = set(profile.get("blocked_strategies") or [])
    hidden_controls = set(profile.get("hidden_controls") or [])

    def item(visible: bool, disabled: bool = False, reason: str = "") -> dict[str, Any]:
        return {"visible": bool(visible), "disabled": bool(disabled), "reason": reason}

    def is_visible(control_id: str, visible: bool) -> bool:
        return bool(visible and control_id not in hidden_controls)

    return {
        "enabled": item(is_visible("enabled", normal_visible), not active),
        "profile": item(is_visible("profile", normal_visible), not active),
        "mode": item(is_visible("mode", not compact and normal_visible), (not active) or params.get("strategy") == "qwen_reedit", "Qwen re-edit locks mode to image upscale." if params.get("strategy") == "qwen_reedit" else ""),
        "resize_method": item(is_visible("resize_method", not compact and normal_visible), not active),
        "scale": item(is_visible("scale", normal_visible), not active),
        "steps": item(is_visible("steps", normal_visible), not active),
        "denoise": item(is_visible("denoise", normal_visible), not active),
        "cfg": item(is_visible("cfg", not compact and normal_visible), not active, "CFG is hidden for this route profile; the extension preserves the base sampler CFG." if "cfg" in hidden_controls else ""),
        "sampler": item(is_visible("sampler", not compact and normal_visible), not active),
        "scheduler": item(is_visible("scheduler", not compact and normal_visible), not active),
        "upscaler": item(is_visible("upscaler", not compact and normal_visible and mode == "image_upscale"), (not active) or model_state == "provider_gated", "Optional model-upscale nodes are unavailable; ImageScale fallback remains available." if model_state == "provider_gated" else ""),
        "tiled_vae": item(is_visible("tiled_vae", not compact and normal_visible), not active),
        "tile_size": item(is_visible("tile_size", not compact and normal_visible and tiled_requested), (not active) or tiled_state == "provider_gated", "VAEDecodeTiled is unavailable; VAEDecode fallback remains available." if tiled_state == "provider_gated" else ""),
        "tile_overlap": item(is_visible("tile_overlap", not compact and normal_visible and tiled_requested), (not active) or tiled_state == "provider_gated", "VAEDecodeTiled is unavailable; VAEDecode fallback remains available." if tiled_state == "provider_gated" else ""),
        "ultimate_sd_upscale": item(is_visible("ultimate_sd_upscale", expert and normal_visible and "ultimate_sd_upscale" not in blocked_strategies), (not active) or ultimate_state != "available", "Ultimate SD Upscale is optional/unavailable or blocked for this route profile." if ((ultimate_state != "available") or ("ultimate_sd_upscale" in blocked_strategies)) else ""),
    }


def visible_control_ids(
    params: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    display_mode: str = "guided",
    optional_capabilities: dict[str, Any] | None = None,
) -> list[str]:
    visibility = parameter_visibility(params, route=route, display_mode=display_mode, optional_capabilities=optional_capabilities)
    return [control_id for control_id, policy in visibility.items() if policy.get("visible")]


def sanitize_hidden_params(
    params: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    display_mode: str = "guided",
    optional_capabilities: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean = deepcopy(params or {})
    visibility = parameter_visibility(clean, route=route, display_mode=display_mode, optional_capabilities=optional_capabilities)
    if not visibility.get("upscaler", {}).get("visible"):
        clean.pop("upscaler", None)
    if not visibility.get("tile_size", {}).get("visible"):
        clean.pop("tile_size", None)
    if not visibility.get("tile_overlap", {}).get("visible"):
        clean.pop("tile_overlap", None)
    if not visibility.get("cfg", {}).get("visible"):
        clean.pop("cfg", None)
    return clean
