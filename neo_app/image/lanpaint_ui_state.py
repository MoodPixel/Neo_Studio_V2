from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from neo_app.image.lanpaint_family_adapter import resolve_lanpaint_family_adapter
from neo_app.image.lanpaint_route_contract import (
    ENGINE_ID,
    MODE_ID,
    ROUTE_FAMILY_ID,
    normalize_family_id,
    normalize_loader_id,
    normalize_provider_id,
)

SCHEMA_ID = "neo.image.lanpaint_ui_state.v1"
SCHEMA_VERSION = 1
AUTHORITY = "neo_app.image.lanpaint_ui_state"
PHASE7_STATE = "route_aware_ui_state_with_phase15_sd_families"
SUPPORTED_PROVIDERS = {"comfyui", "comfyui_portable"}
SUPPORTED_FAMILY_LOADERS = {
    ("sdxl", "checkpoint"), ("sd15", "checkpoint"),
    ("sd35", "diffusion_model"), ("sd35", "gguf"),
    ("krea2_turbo", "diffusion_model"), ("krea2_turbo", "gguf"),
    ("qwen_image", "diffusion_model"), ("qwen_image", "gguf"),
    ("z_image", "diffusion_model"), ("z_image", "gguf"),
    ("z_image_turbo", "diffusion_model"), ("z_image_turbo", "gguf"),
}
FAMILY_LABELS = {
    "sdxl": "SDXL",
    "sd15": "SD 1.5",
    "sd35": "SD 3.5",
    "krea2_turbo": "Krea 2 Turbo",
    "qwen_image": "Qwen Image",
    "z_image": "Z-Image Base",
    "z_image_turbo": "Z-Image Turbo",
}

# Flat payload fields are retained because the Phase 5/6 compiler already consumes
# them. The nested state is the portable/replay contract; the flat values are the
# compatibility bridge for current providers.
FIELD_PATHS: dict[str, tuple[str, ...]] = {
    "lanpaint_crop_padding": ("crop_policy", "padding_px"),
    "lanpaint_processing_width": ("crop_policy", "processing_size", "width"),
    "lanpaint_processing_height": ("crop_policy", "processing_size", "height"),
    "lanpaint_resize_method": ("crop_policy", "resize_method"),
    "lanpaint_sampling_mask_expand": ("mask_policy", "sampling", "expand_px"),
    "lanpaint_sampling_mask_blur": ("mask_policy", "sampling", "blur_radius"),
    "lanpaint_stitch_mask_expand": ("mask_policy", "stitch", "expand_px"),
    "lanpaint_stitch_mask_blur": ("mask_policy", "stitch", "blur_radius"),
    "lanpaint_steps": ("sampler_policy", "steps"),
    "lanpaint_cfg": ("sampler_policy", "cfg"),
    "lanpaint_sampler": ("sampler_policy", "sampler_name"),
    "lanpaint_scheduler": ("sampler_policy", "scheduler"),
    "lanpaint_denoise": ("sampler_policy", "denoise"),
    "lanpaint_thinking_steps": ("sampler_policy", "lanpaint_thinking_steps"),
    "lanpaint_prompt_mode": ("sampler_policy", "prompt_mode"),
    "lanpaint_stitch_resize_method": ("stitch_policy", "resize_method"),
}

_NESTED_ALIASES: dict[str, tuple[str, ...]] = {
    "lanpaint_crop_padding": ("controls", "crop", "padding_px"),
    "lanpaint_processing_width": ("controls", "crop", "processing_width"),
    "lanpaint_processing_height": ("controls", "crop", "processing_height"),
    "lanpaint_resize_method": ("controls", "crop", "resize_method"),
    "lanpaint_sampling_mask_expand": ("controls", "sampling_mask", "expand_px"),
    "lanpaint_sampling_mask_blur": ("controls", "sampling_mask", "blur_radius"),
    "lanpaint_stitch_mask_expand": ("controls", "stitch_mask", "expand_px"),
    "lanpaint_stitch_mask_blur": ("controls", "stitch_mask", "blur_radius"),
    "lanpaint_steps": ("controls", "sampler", "steps"),
    "lanpaint_cfg": ("controls", "sampler", "cfg"),
    "lanpaint_sampler": ("controls", "sampler", "sampler_name"),
    "lanpaint_scheduler": ("controls", "sampler", "scheduler"),
    "lanpaint_denoise": ("controls", "sampler", "denoise"),
    "lanpaint_thinking_steps": ("controls", "sampler", "thinking_steps"),
    "lanpaint_prompt_mode": ("controls", "sampler", "prompt_mode"),
    "lanpaint_stitch_resize_method": ("controls", "stitch", "resize_method"),
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _get_path(value: Mapping[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = value
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _requested_value(raw: Mapping[str, Any], field: str) -> Any:
    nested = _mapping(raw.get("lanpaint_ui_state"))
    nested_value = _get_path(nested, _NESTED_ALIASES[field])
    if nested_value not in (None, ""):
        return nested_value
    value = raw.get(field)
    return value if value not in (None, "") else None


def _request_contract(
    raw: Mapping[str, Any],
    *,
    provider_id: str,
    family: str,
    loader: str,
    mode: str,
) -> dict[str, Any]:
    return {
        "identity": {
            "provider_id": provider_id,
            "family": family,
            "loader": loader,
            "mode": mode,
            "engine": ENGINE_ID,
            "variant": "default",
        },
        "crop_policy": {
            "padding_px": _requested_value(raw, "lanpaint_crop_padding"),
            "processing_size": {
                "width": _requested_value(raw, "lanpaint_processing_width"),
                "height": _requested_value(raw, "lanpaint_processing_height"),
            },
            "resize_method": _requested_value(raw, "lanpaint_resize_method"),
        },
        "mask_policy": {
            "sampling": {
                "expand_px": _requested_value(raw, "lanpaint_sampling_mask_expand"),
                "blur_radius": _requested_value(raw, "lanpaint_sampling_mask_blur"),
            },
            "stitch": {
                "expand_px": _requested_value(raw, "lanpaint_stitch_mask_expand"),
                "blur_radius": _requested_value(raw, "lanpaint_stitch_mask_blur"),
            },
        },
        "sampler_policy": {
            "steps": _requested_value(raw, "lanpaint_steps"),
            "cfg": _requested_value(raw, "lanpaint_cfg"),
            "sampler_name": _requested_value(raw, "lanpaint_sampler"),
            "scheduler": _requested_value(raw, "lanpaint_scheduler"),
            "denoise": _requested_value(raw, "lanpaint_denoise"),
            "lanpaint_thinking_steps": _requested_value(raw, "lanpaint_thinking_steps"),
            "prompt_mode": _requested_value(raw, "lanpaint_prompt_mode"),
        },
        "stitch_policy": {
            "resize_method": _requested_value(raw, "lanpaint_stitch_resize_method"),
        },
    }


def _source_for(field: str, raw: Mapping[str, Any], policy_id: str) -> str:
    return "explicit_user_override" if _requested_value(raw, field) not in (None, "") else policy_id or "family_policy"


def _route_key(provider_id: str, family: str, loader: str, mode: str, engine: str) -> str:
    return f"{provider_id}:{family}:{loader}:{mode}:{engine}"


def _fingerprint(value: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(value))
    payload.pop("state_fingerprint", None)
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolved_flat_values(resolved_contract: Mapping[str, Any]) -> dict[str, Any]:
    return {field: _get_path(resolved_contract, path) for field, path in FIELD_PATHS.items()}


def normalize_lanpaint_ui_state(
    raw: Mapping[str, Any] | None,
    *,
    provider_id: Any,
    family: Any,
    loader: Any,
    mode: Any = MODE_ID,
    engine: Any = ENGINE_ID,
) -> dict[str, Any]:
    """Normalize the route-aware Phase 7 LanPaint UI/replay state.

    Saved controls are retained even when the route is inactive. Execution is
    active only for an exact eligible route and an explicit ``engine=lanpaint``.
    Family policy remains the default authority; user values are normalized by
    the canonical LanPaint route contract before they reach a provider compiler.
    """

    values = _mapping(raw)
    provider = normalize_provider_id(provider_id)
    family_id = normalize_family_id(family)
    loader_id = normalize_loader_id(loader)
    mode_id = str(mode or MODE_ID).strip().lower().replace("-", "_")
    if mode_id in {"inpainting", "mask_inpaint"}:
        mode_id = MODE_ID
    engine_id = str(engine or "native").strip().lower().replace("-", "_") or "native"
    if engine_id == "lan_paint":
        engine_id = ENGINE_ID

    adapter = resolve_lanpaint_family_adapter(
        _request_contract(values, provider_id=provider, family=family_id, loader=loader_id, mode=mode_id)
    )
    binding = _mapping(adapter.get("binding"))
    policy = _mapping(adapter.get("policy"))
    eligible = bool(provider in SUPPORTED_PROVIDERS and binding.get("selectable") and mode_id == MODE_ID)
    active = eligible and engine_id == ENGINE_ID

    spatial = _mapping(adapter.get("spatial"))
    resolved_contract = {
        "crop_policy": _mapping(spatial.get("crop")),
        "mask_policy": _mapping(spatial.get("mask")),
        "sampler_policy": _mapping(_mapping(adapter.get("sampler")).get("defaults")),
        "stitch_policy": _mapping(spatial.get("stitch")),
    }
    resolved_flat = _resolved_flat_values(resolved_contract)
    policy_id = str(policy.get("policy_id") or "family_policy")
    diagnostics = _mapping(adapter.get("diagnostics"))
    issues = [deepcopy(item) for item in diagnostics.get("contract_issues", []) if isinstance(item, Mapping)]
    issues.extend(deepcopy(item) for item in diagnostics.get("policy_resolution_issues", []) if isinstance(item, Mapping))
    errors = [item for item in issues if item.get("level") == "error"]
    warnings = [item for item in issues if item.get("level") == "warning"]

    requested_flat = {field: _requested_value(values, field) for field in FIELD_PATHS}
    value_sources = {field: _source_for(field, values, policy_id) for field in FIELD_PATHS}
    route_state = "experimental_available" if eligible else "unsupported"
    if not eligible:
        warnings.append({
            "level": "warning",
            "field": "route",
            "message": "LanPaint controls are preserved but inactive because the selected route is not eligible.",
        })

    state: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "phase_state": PHASE7_STATE,
        "route_family_id": ROUTE_FAMILY_ID,
        "route": {
            "provider_id": provider,
            "family": family_id,
            "loader": loader_id,
            "mode": mode_id,
            "engine": ENGINE_ID if active else "native",
            "requested_engine": engine_id,
            "route_key": _route_key(provider, family_id, loader_id, mode_id, ENGINE_ID),
            "route_state": route_state,
            "eligible": eligible,
            "active": active,
            "selectable": eligible,
            "variant": str(_mapping(adapter.get("identity")).get("variant") or "default"),
        },
        "badges": {
            "provider": "ComfyUI Portable" if provider == "comfyui_portable" else ("ComfyUI" if provider == "comfyui" else provider),
            "family": FAMILY_LABELS.get(family_id, family_id),
            "loader": "GGUF" if loader_id == "gguf" else "Safetensors",
            "engine": "LanPaint",
            "state": "Experimental" if eligible else "Unsupported",
            "lora_mode": ("Model-only" if _mapping(adapter.get("lora")).get("mode") == "model_only" else "Model + CLIP") if eligible else "Unavailable",
        },
        "controls": {
            "crop": {
                "padding_px": resolved_flat.get("lanpaint_crop_padding"),
                "processing_width": resolved_flat.get("lanpaint_processing_width"),
                "processing_height": resolved_flat.get("lanpaint_processing_height"),
                "resize_method": resolved_flat.get("lanpaint_resize_method"),
            },
            "sampling_mask": {
                "expand_px": resolved_flat.get("lanpaint_sampling_mask_expand"),
                "blur_radius": resolved_flat.get("lanpaint_sampling_mask_blur"),
            },
            "stitch_mask": {
                "expand_px": resolved_flat.get("lanpaint_stitch_mask_expand"),
                "blur_radius": resolved_flat.get("lanpaint_stitch_mask_blur"),
            },
            "sampler": {
                "steps": resolved_flat.get("lanpaint_steps"),
                "cfg": resolved_flat.get("lanpaint_cfg"),
                "sampler_name": resolved_flat.get("lanpaint_sampler"),
                "scheduler": resolved_flat.get("lanpaint_scheduler"),
                "denoise": resolved_flat.get("lanpaint_denoise"),
                "thinking_steps": resolved_flat.get("lanpaint_thinking_steps"),
                "prompt_mode": resolved_flat.get("lanpaint_prompt_mode"),
            },
            "stitch": {
                "resize_method": resolved_flat.get("lanpaint_stitch_resize_method"),
                "preserve_source_dimensions": True,
            },
        },
        "requested_flat": requested_flat,
        "resolved_flat": resolved_flat,
        "flat_params": resolved_flat,
        "value_sources": value_sources,
        "family_policy": {
            "policy_id": policy_id,
            "policy_fingerprint": str(policy.get("policy_fingerprint") or ""),
            "resolution_state": str(policy.get("resolution_state") or ""),
        },
        "family_adapter": {
            "schema_id": adapter.get("schema_id"),
            "adapter_id": _mapping(adapter.get("identity")).get("adapter_id"),
            "adapter_fingerprint": adapter.get("adapter_fingerprint"),
            "binding_state": binding.get("state"),
            "graph_profile": binding.get("graph_profile"),
            "lora_compatibility_key": _mapping(adapter.get("lora")).get("compatibility_key"),
        },
        "validation": {
            "ok": not errors,
            "eligible": eligible,
            "active": active,
            "errors": errors,
            "warnings": warnings,
        },
        "state_policy": {
            "preserve_saved_controls_when_inactive": True,
            "force_execution_engine_native_when_ineligible": True,
            "family_policy_defaults_are_authoritative": True,
            "flat_payload_compatibility_enabled": True,
        },
    }
    state["state_fingerprint"] = _fingerprint(state)
    return state


__all__ = [
    "AUTHORITY",
    "FIELD_PATHS",
    "PHASE7_STATE",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SUPPORTED_PROVIDERS",
    "normalize_lanpaint_ui_state",
]
