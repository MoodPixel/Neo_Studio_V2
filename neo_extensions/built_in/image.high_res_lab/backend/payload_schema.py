from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import (
    ACTIVE_ROUTE_STATES,
    ALLOWED_MODES,
    ALLOWED_RESIZE_METHODS,
    ALLOWED_STRATEGIES,
    DEFAULT_PARAMS,
    EXTENSION_ID,
    PHASE,
    PROFILE_PRESETS,
    VERSION,
)
from .support_matrix import route_reason, route_state
from .route_profiles import route_profile_summary
from .parameter_visibility import sanitize_hidden_params, visible_control_ids


def _clamp_number(value: Any, default: float, minimum: float, maximum: float, *, integer: bool = False) -> int | float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    number = max(minimum, min(maximum, number))
    return int(round(number)) if integer else round(number, 4)


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def disabled_block(reason: str = "High-Res Lab is disabled.", *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    state = route_state(route)
    profile = route_profile_summary(route)
    return {
        "enabled": False,
        "version": VERSION,
        "inputs": {},
        "params": {},
        "assets": {},
        "metadata": {
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "status": "disabled",
            "route_state": state,
            "reason": reason,
            "workflow_patch_ready": False,
            "route_profile": profile,
            "profile_id": profile.get("profile_id"),
            "profile_state": profile.get("high_res_lab_state"),
        },
    }


def payload_wrapper(block: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"extensions": {EXTENSION_ID: deepcopy(block) if isinstance(block, dict) else disabled_block()}}


def normalize_inputs(raw: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    inputs = raw.get("inputs") if isinstance(raw.get("inputs"), dict) else {}
    return {k: v for k, v in inputs.items() if v not in (None, "", [], {})}


def normalize_assets(raw: dict[str, Any] | None = None, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    params = params or {}
    assets = raw.get("assets") if isinstance(raw.get("assets"), dict) else {}
    clean: dict[str, Any] = {}
    if params.get("mode") == "image_upscale" and params.get("upscaler"):
        clean["upscaler"] = params["upscaler"]
    for key, value in assets.items():
        if value not in (None, "", [], {}):
            clean[key] = value
    return clean


def normalize_params(raw: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None, display_mode: str = "guided", optional_capabilities: dict[str, Any] | None = None) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    source = raw.get("params") if isinstance(raw.get("params"), dict) else raw
    profile = _clean_text(source.get("profile") or DEFAULT_PARAMS["profile"])
    preset = PROFILE_PRESETS.get(profile, {}) if profile != "custom" else {}
    merged = {**DEFAULT_PARAMS, **preset, **source}

    mode = _clean_text(merged.get("mode") or DEFAULT_PARAMS["mode"])
    if mode not in ALLOWED_MODES:
        mode = DEFAULT_PARAMS["mode"]
    resize_method = _clean_text(merged.get("resize_method") or DEFAULT_PARAMS["resize_method"])
    if resize_method not in ALLOWED_RESIZE_METHODS:
        resize_method = DEFAULT_PARAMS["resize_method"]

    strategy_source = source.get("strategy")
    if strategy_source in (None, "") and _clean_text(source.get("mode")) == "latent":
        strategy = "standard"
    else:
        strategy = _clean_text(strategy_source or merged.get("strategy") or "standard")
    if strategy not in ALLOWED_STRATEGIES:
        strategy = "standard"
    if strategy in {"forge_pixel_refine", "ultimate_sd_upscale", "upscale_only", "qwen_reedit"}:
        mode = "image_upscale"

    clean = {
        "profile": profile if profile in {"custom", *PROFILE_PRESETS.keys()} else "custom",
        "strategy": strategy,
        "mode": mode,
        "resize_method": resize_method,
        "scale": _clamp_number(merged.get("scale"), DEFAULT_PARAMS["scale"], 1.1, 4.0),
        "steps": _clamp_number(merged.get("steps"), DEFAULT_PARAMS["steps"], 4, 80, integer=True),
        "denoise": _clamp_number(merged.get("denoise"), DEFAULT_PARAMS["denoise"], 0.05, 0.95),
        "cfg": _clamp_number(merged.get("cfg"), DEFAULT_PARAMS["cfg"], 1.0, 30.0),
        "sampler": _clean_text(merged.get("sampler")),
        "scheduler": _clean_text(merged.get("scheduler")),
        "tiled_vae": bool(merged.get("tiled_vae", DEFAULT_PARAMS["tiled_vae"])),
        "tile_size": _clamp_number(merged.get("tile_size"), DEFAULT_PARAMS["tile_size"], 64, 4096, integer=True),
        "tile_overlap": _clamp_number(merged.get("tile_overlap"), DEFAULT_PARAMS["tile_overlap"], 0, 1024, integer=True),
    }
    target_width = _clamp_number(merged.get("target_width"), 0, 0, 16384, integer=True) if merged.get("target_width") not in (None, "", 0, "0") else 0
    target_height = _clamp_number(merged.get("target_height"), 0, 0, 16384, integer=True) if merged.get("target_height") not in (None, "", 0, "0") else 0
    if target_width:
        clean["target_width"] = target_width
    if target_height:
        clean["target_height"] = target_height
    if bool(merged.get("upscale_lab_source_only")):
        clean["upscale_lab_source_only"] = True
    # Stale hidden field cleanup: upscaler is only valid for image-upscale mode.
    if mode == "image_upscale":
        clean["upscaler"] = _clean_text(merged.get("upscaler"))
    return sanitize_hidden_params(clean, route=route, display_mode=display_mode, optional_capabilities=optional_capabilities)


def normalize_block(raw: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None, enforce_route_state: bool = True) -> dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    enabled = bool(raw.get("enabled"))
    state = route_state(route)
    if not enabled:
        return disabled_block("High-Res Lab is disabled.", route=route)
    if enforce_route_state and state not in ACTIVE_ROUTE_STATES:
        return disabled_block(f"{state}: {route_reason(route)}", route=route)
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    profile = route_profile_summary(route)
    optional_caps = metadata.get("optional_capabilities") if isinstance(metadata.get("optional_capabilities"), dict) else None
    params = normalize_params(raw, route=route, display_mode="guided", optional_capabilities=optional_caps)
    assets = normalize_assets(raw, params=params)
    return {
        "enabled": True,
        "version": VERSION,
        "inputs": normalize_inputs(raw),
        "params": params,
        "assets": assets,
        "metadata": {
            "extension_id": EXTENSION_ID,
            "extension_type": "built_in",
            "phase": PHASE,
            "status": "workflow_patch_ready",
            "route_state": state,
            "backend": (route or {}).get("backend") or (route or {}).get("provider"),
            "family": (route or {}).get("family"),
            "loader": (route or {}).get("loader"),
            "workflow_mode": (route or {}).get("mode") or (route or {}).get("workflow_mode"),
            "workflow_patch_ready": True,
            "route_profile": profile,
            "profile_id": profile.get("profile_id"),
            "profile_state": profile.get("high_res_lab_state"),
            "workflow_patch_deferred_to_phase": "",
            "parameter_visibility": visible_control_ids(params, route=route, display_mode="guided", optional_capabilities=optional_caps),
            **{k: v for k, v in metadata.items() if k not in {"params", "assets"}},
        },
    }
