from __future__ import annotations

from typing import Any, Iterable

from .constants import ACTIVE_ROUTE_STATES, EXTENSION_ID, PHASE
from .node_discovery import inspect_nodes
from .payload_schema import normalize_block, normalize_params
from .support_matrix import route_reason, route_state
from .route_profiles import route_profile_summary


def _warning(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}


def _error(field: str, message: str) -> dict[str, str]:
    return {"field": field, "message": message}


def _coerce_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _raw_param_source(raw: dict[str, Any] | None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    return raw.get("params") if isinstance(raw.get("params"), dict) else raw


def _clamp_warnings(raw: dict[str, Any] | None, params: dict[str, Any]) -> list[dict[str, str]]:
    source = _raw_param_source(raw)
    ranges = {
        "scale": (1.1, 4.0),
        "steps": (4, 80),
        "denoise": (0.05, 0.95),
        "cfg": (1.0, 30.0),
        "tile_size": (64, 4096),
        "tile_overlap": (0, 1024),
    }
    warnings: list[dict[str, str]] = []
    for field, (minimum, maximum) in ranges.items():
        if field not in source:
            continue
        parsed = _coerce_float(source.get(field))
        if parsed is None:
            warnings.append(_warning(field, f"Invalid {field} value was replaced with the default/sanitized value."))
        elif parsed < minimum or parsed > maximum:
            warnings.append(_warning(field, f"{field} was clamped to the safe range {minimum}–{maximum}."))
    if params.get("tile_overlap", 0) >= params.get("tile_size", 1):
        warnings.append(_warning("tile_overlap", "Tile overlap should stay smaller than tile size before Phase J workflow patching."))
    return warnings


def validate_route_support(route: dict[str, Any] | None = None) -> dict[str, Any]:
    state = route_state(route)
    profile = route_profile_summary(route)
    return {
        "ok": state in ACTIVE_ROUTE_STATES,
        "route_state": state,
        "reason": route_reason(route),
        "route_profile": profile,
    }


def validate_high_res_lab_payload(
    raw: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: Iterable[str] | dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    state = route_state(route)
    profile = route_profile_summary(route)
    enabled_requested = bool(raw.get("enabled"))
    params = normalize_params(raw, route=route)
    node_status = inspect_nodes(available_nodes, mode=params.get("mode"))
    route_ok = state in ACTIVE_ROUTE_STATES
    nodes_ok = node_status.get("status") in {"available", "unknown_not_checked"}

    warnings: list[dict[str, str]] = []
    errors: list[dict[str, str]] = []

    if enabled_requested and not route_ok:
        warnings.append(_warning("route_state", route_reason(route)))
    allowed_strategies = set(profile.get("allowed_strategies") or [])
    blocked_strategies = set(profile.get("blocked_strategies") or [])
    if enabled_requested and allowed_strategies and params.get("strategy") not in allowed_strategies:
        warnings.append(_warning("strategy", f"Strategy {params.get('strategy')} is not recommended for route profile {profile.get('profile_id')}; use one of {sorted(allowed_strategies)}."))
    if enabled_requested and params.get("strategy") in blocked_strategies:
        warnings.append(_warning("strategy", f"Strategy {params.get('strategy')} is blocked for route profile {profile.get('profile_id')}; High-Res Lab will use the safe generic refine path instead of a family fallback."))
    cfg_policy = str(profile.get("cfg_policy") or "")
    if enabled_requested and cfg_policy == "qwen_rapid_aio_low_cfg" and "cfg" in _raw_param_source(raw):
        warnings.append(_warning("cfg", "Qwen Rapid AIO High-Res Lab caps refine CFG at 2.0 to avoid SD-style over-guidance."))
    if enabled_requested and cfg_policy in {"qwen_safe_low_cfg", "qwen_2509_safe_low_cfg"} and "cfg" in _raw_param_source(raw):
        warnings.append(_warning("cfg", "Qwen High-Res Lab caps refine CFG at 4.0 to preserve source/edit conditioning."))
    if enabled_requested and cfg_policy == "z_image_aura_safe" and "cfg" in _raw_param_source(raw):
        warnings.append(_warning("cfg", "ZImage High-Res Lab caps refine CFG at 3.5 to preserve AuraFlow/source conditioning."))
    if enabled_requested and cfg_policy == "z_image_turbo_low_cfg" and "cfg" in _raw_param_source(raw):
        warnings.append(_warning("cfg", "ZImage Turbo High-Res Lab caps refine CFG at 1.0 to preserve distilled Turbo/source conditioning."))
    if enabled_requested and node_status.get("status") == "unknown_not_checked":
        warnings.append(_warning("node_availability", "Required Comfy node availability was not supplied; Phase I validates shape but defers hard gating to provider discovery."))
    if enabled_requested and node_status.get("status") == "provider_gated":
        msg = node_status.get("reason") or "Required base Comfy nodes are missing."
        if strict:
            errors.append(_error("node_availability", msg))
        else:
            warnings.append(_warning("node_availability", msg))
    if params.get("mode") == "latent" and "upscaler" in _raw_param_source(raw):
        warnings.append(_warning("upscaler", "Upscaler model is hidden for latent mode and was removed from the payload."))
    if params.get("mode") == "latent" and float(params.get("scale") or 1) >= 1.4 and float(params.get("denoise") or 0) < 0.35:
        warnings.append(_warning("denoise", "Latent high-res with low denoise can blur or warp details; use the Latent Rebuild preset or denoise around 0.45–0.65 for stronger latent reconstruction."))
    optional_caps = node_status.get("optional_capabilities") if isinstance(node_status.get("optional_capabilities"), dict) else {}
    optional_groups = optional_caps.get("groups") if isinstance(optional_caps.get("groups"), dict) else {}
    model_upscale_state = (optional_groups.get("model_upscale") or {}).get("state") if isinstance(optional_groups.get("model_upscale"), dict) else None
    tiled_vae_state = (optional_groups.get("tiled_vae_decode") or {}).get("state") if isinstance(optional_groups.get("tiled_vae_decode"), dict) else None
    ultimate_state = (optional_groups.get("ultimate_sd_upscale") or {}).get("state") if isinstance(optional_groups.get("ultimate_sd_upscale"), dict) else None
    if params.get("mode") == "image_upscale" and params.get("upscaler") and model_upscale_state != "available":
        warnings.append(_warning("upscaler", "Upscaler model path is optional; missing UpscaleModelLoader/ImageUpscaleWithModel will fall back to ImageScale without gating High-Res Lab."))
    if params.get("tiled_vae") and tiled_vae_state == "provider_gated":
        warnings.append(_warning("tiled_vae", "VAEDecodeTiled is optional and missing; workflow patch will fall back to standard VAEDecode."))
    if params.get("strategy") == "ultimate_sd_upscale" and ultimate_state != "available":
        warnings.append(_warning("strategy", "Ultimate SD Upscale is optional and unavailable; standard High-Res Lab patch remains available."))
    warnings.extend(_clamp_warnings(raw, params))

    # Phase I validation may disable the block for confirmed missing required nodes, but it still never emits graph patch data.
    block = normalize_block(raw, route=route, enforce_route_state=True)
    if enabled_requested and route_ok and node_status.get("status") == "provider_gated":
        block = normalize_block({"enabled": False}, route=route, enforce_route_state=True)
        block["metadata"]["status"] = "provider_gated"
        block["metadata"]["reason"] = node_status.get("reason") or "Required base Comfy nodes are missing."

    if block.get("enabled"):
        warnings.append(_warning("workflow_patch", "Validation ready; workflow graph mutation is allowed for available Comfy routes."))

    return {
        "ok": not errors,
        "phase": PHASE,
        "skeleton_only": False,
        "payload_runtime_ready": True,
        "validation_runtime_ready": True,
        "enabled": bool(block.get("enabled")),
        "route_state": state,
        "route_profile": profile,
        "route_ok": route_ok,
        "node_status": node_status,
        "reason": ("Validation ready; workflow patch may be applied." if block.get("enabled") else block.get("metadata", {}).get("reason") or route_reason(route)),
        "warnings": warnings,
        "errors": errors,
        "block": block,
        "can_emit_workflow_patch": bool(block.get("enabled") and route_ok and nodes_ok and not errors),
        "workflow_patch_allowed": bool(block.get("enabled") and route_ok and nodes_ok and not errors),
    }


def validate_and_normalize_payload(
    raw: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: Iterable[str] | dict[str, Any] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    return validate_high_res_lab_payload(raw, route=route, available_nodes=available_nodes, strict=strict)
