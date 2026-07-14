"""Phase E payload contract for the Image Upscale built-in extension.

This module normalizes V1 Image Upscale settings into Neo Studio V2's
canonical extension payload block. It does not queue jobs and does not mutate a
workflow graph; later phases consume this clean block through the extension-owned
queue/workflow path.
"""
from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .constants import (
    CODEFORMER_FACE_DETECTION_OPTIONS,
    EXTENSION_ID,
    EXTENSION_VERSION,
    SEEDVR2_ATTENTION_MODES,
    SEEDVR2_ALPHA_MODES,
    SEEDVR2_COLOR_CORRECTION_OPTIONS,
    SEEDVR2_DIT_DEFAULT,
    SEEDVR2_VAE_DEFAULT,
    WORKSPACE_APP,
)
from .support_matrix import ACTIVE_ROUTE_STATES, support_for_route

PAYLOAD_SCHEMA_VERSION = "neo.extension.payload.v1"
PAYLOAD_CONTRACT_PHASE = "J6"
REPLAY_CONTRACT_PHASE = "K"
REPLAY_RESTORE_POLICY = "revalidate_route_nodes_image_upscale_source_assets_and_optional_models_before_enable"

PROFILE_DEFAULT = "preserve_2x"
VALID_PROFILES = {"custom", "preserve_2x", "preserve_4x", "portrait_restore_2x"}
VALID_RESIZE_METHODS = {"lanczos", "bicubic", "bilinear", "area", "nearest-exact"}
VALID_RESTORE_ASSISTS = {"off", "codeformer"}
VALID_SOURCE_MODES = {"selected_result", "upload", "batch", "selected_result_or_upload"}
VALID_UPSCALE_ENGINES = {"basic", "seedvr2"}

DEFAULTS: dict[str, Any] = {
    "profile": PROFILE_DEFAULT,
    "upscale_engine": "basic",
    "upscale_model": "",
    "scale": 2.0,
    "resize_method": "lanczos",
    "restore_assist": "off",
    "restore_model": "",
    "restore_fidelity": 0.65,
    "restore_detection": "retinaface_resnet50",
    "source_mode": "selected_result_or_upload",
    "seedvr2_dit_model": SEEDVR2_DIT_DEFAULT,
    "seedvr2_vae_model": SEEDVR2_VAE_DEFAULT,
    "seedvr2_sizing_mode": "scale_factor",
    "seedvr2_resolution": 1080,
    "seedvr2_max_resolution": 0,
    "seedvr2_source_width": 0,
    "seedvr2_source_height": 0,
    "seedvr2_output_width": 0,
    "seedvr2_output_height": 0,
    "seedvr2_batch_size": 1,
    "seedvr2_seed": 42,
    "seedvr2_device": "cuda:0",
    "seedvr2_offload_device": "cpu",
    "seedvr2_blocks_to_swap": 32,
    "seedvr2_swap_io_components": True,
    "seedvr2_cache_models": False,
    "seedvr2_encode_tiled": True,
    "seedvr2_decode_tiled": True,
    "seedvr2_tile_size": 1024,
    "seedvr2_tile_overlap": 128,
    "seedvr2_attention_mode": "sdpa",
    "seedvr2_color_correction": "lab",
    "seedvr2_input_noise_scale": 0.0,
    "seedvr2_latent_noise_scale": 0.0,
    "seedvr2_enable_debug": False,
    "seedvr2_alpha_mode": "auto",
    "seedvr2_source_format": "",
    "seedvr2_source_image_mode": "",
    "seedvr2_source_has_alpha": False,
    "seedvr2_source_has_transparency": False,
    "seedvr2_alpha_min": 255,
    "seedvr2_alpha_max": 255,
    "seedvr2_alpha_route_applied": False,
    "seedvr2_output_format": "png",
}

V1_KEY_ALIASES = {
    "image_upscale_profile": "profile",
    "image_upscale_engine": "upscale_engine",
    "image_upscale_model": "upscale_model",
    "image_upscale_scale": "scale",
    "image_upscale_resize_method": "resize_method",
    "image_upscale_restore_assist": "restore_assist",
    "image_upscale_restore_model": "restore_model",
    "image_upscale_restore_fidelity": "restore_fidelity",
    "image_upscale_restore_detection": "restore_detection",
    "seedvr2_dit_model": "seedvr2_dit_model",
    "seedvr2_vae_model": "seedvr2_vae_model",
    "seedvr2_sizing_mode": "seedvr2_sizing_mode",
    "seedvr2_resolution": "seedvr2_resolution",
    "seedvr2_max_resolution": "seedvr2_max_resolution",
    "seedvr2_source_width": "seedvr2_source_width",
    "seedvr2_source_height": "seedvr2_source_height",
    "seedvr2_output_width": "seedvr2_output_width",
    "seedvr2_output_height": "seedvr2_output_height",
    "seedvr2_batch_size": "seedvr2_batch_size",
    "seedvr2_seed": "seedvr2_seed",
    "seedvr2_device": "seedvr2_device",
    "seedvr2_offload_device": "seedvr2_offload_device",
    "seedvr2_blocks_to_swap": "seedvr2_blocks_to_swap",
    "seedvr2_swap_io_components": "seedvr2_swap_io_components",
    "seedvr2_cache_models": "seedvr2_cache_models",
    "seedvr2_encode_tiled": "seedvr2_encode_tiled",
    "seedvr2_decode_tiled": "seedvr2_decode_tiled",
    "seedvr2_tile_size": "seedvr2_tile_size",
    "seedvr2_tile_overlap": "seedvr2_tile_overlap",
    "seedvr2_attention_mode": "seedvr2_attention_mode",
    "seedvr2_color_correction": "seedvr2_color_correction",
    "seedvr2_input_noise_scale": "seedvr2_input_noise_scale",
    "seedvr2_latent_noise_scale": "seedvr2_latent_noise_scale",
    "seedvr2_enable_debug": "seedvr2_enable_debug",
    "seedvr2_alpha_mode": "seedvr2_alpha_mode",
    "seedvr2_source_format": "seedvr2_source_format",
    "seedvr2_source_image_mode": "seedvr2_source_image_mode",
    "seedvr2_source_has_alpha": "seedvr2_source_has_alpha",
    "seedvr2_source_has_transparency": "seedvr2_source_has_transparency",
    "seedvr2_alpha_min": "seedvr2_alpha_min",
    "seedvr2_alpha_max": "seedvr2_alpha_max",
    "seedvr2_alpha_route_applied": "seedvr2_alpha_route_applied",
    "seedvr2_output_format": "seedvr2_output_format",
    "_neo_source_output_id": "source_output_id",
    "_neo_parent_output_id": "parent_output_id",
}


class PayloadContractError(ValueError):
    """Raised only when the payload contract receives unrecoverable input."""


def _as_mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise PayloadContractError(f"Invalid Image Upscale settings_json: {exc.msg}") from exc
        if not isinstance(loaded, dict):
            raise PayloadContractError("Image Upscale settings_json must decode to an object.")
        return loaded
    if isinstance(value, dict):
        return dict(value)
    raise PayloadContractError("Image Upscale settings must be a mapping or JSON object string.")


def _unwrap_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Accept V1 flat payloads and V2 extension-envelope payloads."""
    current = dict(settings)
    extensions = current.get("extensions")
    if isinstance(extensions, dict):
        block = extensions.get(EXTENSION_ID)
        if isinstance(block, dict):
            merged: dict[str, Any] = {}
            for key in ("inputs", "params", "assets", "metadata"):
                if isinstance(block.get(key), dict):
                    merged.update(block[key])
            if "enabled" in block:
                merged["enabled"] = block["enabled"]
            current = merged
    return current


def _alias_v1_keys(settings: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(settings)
    for old_key, new_key in V1_KEY_ALIASES.items():
        if old_key in normalized and new_key not in normalized:
            normalized[new_key] = normalized[old_key]
    return normalized



def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value if value is not None else "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _normalize_upscale_engine(value: Any) -> str:
    engine = _text(value, DEFAULTS["upscale_engine"]).lower().replace(" ", "_").replace("-", "_")
    if engine in {"esrgan", "model", "interpolation", "basic_esrgan"}:
        return "basic"
    return engine if engine in VALID_UPSCALE_ENGINES else DEFAULTS["upscale_engine"]


def _seedvr2_batch_size(value: Any) -> int:
    size = max(1, min(_int(value, DEFAULTS["seedvr2_batch_size"]), 81))
    # SeedVR2's video mode prefers 4n+1. For single images, 1 is correct.
    if size == 1:
        return 1
    remainder = (size - 1) % 4
    if remainder:
        size = size - remainder
    return max(5, size)

def _text(value: Any, default: str = "") -> str:
    text = str(value if value is not None else default).strip()
    return text


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_profile(value: Any) -> str:
    profile = _text(value, PROFILE_DEFAULT).lower().replace(" ", "_")
    return profile if profile in VALID_PROFILES else PROFILE_DEFAULT


def _normalize_resize_method(value: Any) -> str:
    method = _text(value, DEFAULTS["resize_method"]).lower().replace("_", "-")
    return method if method in VALID_RESIZE_METHODS else DEFAULTS["resize_method"]


def _normalize_restore_assist(value: Any) -> str:
    assist = _text(value, DEFAULTS["restore_assist"]).lower().replace(" ", "_")
    return assist if assist in VALID_RESTORE_ASSISTS else DEFAULTS["restore_assist"]



def _normalize_seedvr2_sizing_mode(value: Any) -> str:
    mode = _text(value, DEFAULTS["seedvr2_sizing_mode"]).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "scale": "scale_factor",
        "scale_factor": "scale_factor",
        "target_scale": "scale_factor",
        "short_edge": "short_edge",
        "short_edge_target": "short_edge",
        "max_edge": "max_edge",
        "max_edge_target": "max_edge",
        "manual": "manual",
    }
    return aliases.get(mode, DEFAULTS["seedvr2_sizing_mode"])


def _normalize_seedvr2_alpha_mode(value: Any) -> str:
    mode = _text(value, DEFAULTS["seedvr2_alpha_mode"]).lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "automatic": "auto",
        "auto_preserve": "auto",
        "force": "preserve",
        "force_preserve": "preserve",
        "rgba": "preserve",
        "flatten": "discard",
        "opaque": "discard",
        "remove_alpha": "discard",
    }
    mode = aliases.get(mode, mode)
    return mode if mode in SEEDVR2_ALPHA_MODES else DEFAULTS["seedvr2_alpha_mode"]


def compute_seedvr2_resolution_contract(settings: dict[str, Any]) -> dict[str, Any]:
    """Compute SeedVR2 output sizing from source dimensions + target scale.

    SeedVR2's node uses ``resolution`` as the shortest-edge target, not a
    multiplier. Neo exposes target scale to users, so this contract converts
    scale into short/max edge values when source dimensions are known.
    """
    clean = dict(settings or {})
    if _normalize_upscale_engine(clean.get("upscale_engine", DEFAULTS["upscale_engine"])) != "seedvr2":
        return clean
    mode = _normalize_seedvr2_sizing_mode(clean.get("seedvr2_sizing_mode", DEFAULTS["seedvr2_sizing_mode"]))
    source_w = max(0, _int(clean.get("seedvr2_source_width", 0), 0))
    source_h = max(0, _int(clean.get("seedvr2_source_height", 0), 0))
    scale = _clamp(_float(clean.get("scale", DEFAULTS["scale"]), DEFAULTS["scale"]), 0.25, 8.0)
    short_edge = max(256, min(_int(clean.get("seedvr2_resolution", DEFAULTS["seedvr2_resolution"]), DEFAULTS["seedvr2_resolution"]), 4096))
    max_edge = max(0, min(_int(clean.get("seedvr2_max_resolution", DEFAULTS["seedvr2_max_resolution"]), DEFAULTS["seedvr2_max_resolution"]), 8192))
    output_w = max(0, _int(clean.get("seedvr2_output_width", 0), 0))
    output_h = max(0, _int(clean.get("seedvr2_output_height", 0), 0))

    if source_w and source_h:
        source_short = min(source_w, source_h)
        source_long = max(source_w, source_h)
        if mode == "scale_factor":
            short_edge = max(256, min(int(round(source_short * scale)), 4096))
            max_edge = max(0, min(int(round(source_long * scale)), 8192))
        elif mode == "max_edge":
            if max_edge <= 0:
                max_edge = max(256, min(int(round(source_long * scale)), 8192))
            ratio = max_edge / source_long if source_long else 1.0
            short_edge = max(256, min(int(round(source_short * ratio)), 4096))
        elif mode == "short_edge":
            ratio = short_edge / source_short if source_short else 1.0
            max_edge = max(0, min(int(round(source_long * ratio)), 8192))
        # manual keeps the supplied short/max fields.
        ratio = short_edge / source_short if source_short else 1.0
        computed_w = max(1, int(round(source_w * ratio)))
        computed_h = max(1, int(round(source_h * ratio)))
        if max_edge > 0 and max(computed_w, computed_h) > max_edge:
            ratio = max_edge / max(computed_w, computed_h)
            computed_w = max(1, int(round(computed_w * ratio)))
            computed_h = max(1, int(round(computed_h * ratio)))
            short_edge = min(computed_w, computed_h)
        output_w, output_h = computed_w, computed_h

    clean["seedvr2_sizing_mode"] = mode
    clean["seedvr2_source_width"] = source_w
    clean["seedvr2_source_height"] = source_h
    clean["seedvr2_resolution"] = short_edge
    clean["seedvr2_max_resolution"] = max_edge
    clean["seedvr2_output_width"] = output_w
    clean["seedvr2_output_height"] = output_h
    return clean

def _normalize_source_mode(value: Any) -> str:
    mode = _text(value, DEFAULTS["source_mode"]).lower().replace(" ", "_")
    return mode if mode in VALID_SOURCE_MODES else DEFAULTS["source_mode"]


def _normalize_source_images(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    raw_items: list[Any]
    if isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]
    images: list[dict[str, Any]] = []
    for item in raw_items:
        if item is None:
            continue
        if isinstance(item, dict):
            image = {str(k): v for k, v in item.items() if v not in (None, "")}
        else:
            image = {"name": _text(item)}
        if image:
            images.append(image)
    return images


def normalize_settings(settings: dict[str, Any] | str | None = None) -> dict[str, Any]:
    """Normalize V1/V2 Image Upscale settings and strip stale hidden fields."""
    raw = _alias_v1_keys(_unwrap_settings(_as_mapping(settings)))
    clean: dict[str, Any] = {}

    clean["profile"] = _normalize_profile(raw.get("profile", DEFAULTS["profile"]))
    clean["upscale_engine"] = _normalize_upscale_engine(raw.get("upscale_engine", DEFAULTS["upscale_engine"]))
    clean["scale"] = round(_clamp(_float(raw.get("scale", DEFAULTS["scale"]), DEFAULTS["scale"]), 0.25, 8.0), 4)
    clean["resize_method"] = _normalize_resize_method(raw.get("resize_method", DEFAULTS["resize_method"]))
    clean["upscale_model"] = _text(raw.get("upscale_model", DEFAULTS["upscale_model"]))
    clean["restore_assist"] = _normalize_restore_assist(raw.get("restore_assist", DEFAULTS["restore_assist"]))
    clean["source_mode"] = _normalize_source_mode(raw.get("source_mode", DEFAULTS["source_mode"]))

    # Keep source linkage as inputs, but only when present. These are safe
    # replay/source references, not workflow mutation flags.
    for link_key in ("source_output_id", "parent_output_id", "source_image_name"):
        value = _text(raw.get(link_key))
        if value:
            clean[link_key] = value

    # Hidden-field cleanup: restore details only survive when CodeFormer is on.
    if clean["restore_assist"] == "codeformer":
        clean["restore_model"] = _text(raw.get("restore_model", DEFAULTS["restore_model"]))
        clean["restore_fidelity"] = round(_clamp(_float(raw.get("restore_fidelity", DEFAULTS["restore_fidelity"]), DEFAULTS["restore_fidelity"]), 0.0, 1.0), 4)
        detection = _text(raw.get("restore_detection", DEFAULTS["restore_detection"])) or DEFAULTS["restore_detection"]
        clean["restore_detection"] = detection if detection in CODEFORMER_FACE_DETECTION_OPTIONS else DEFAULTS["restore_detection"]

    if clean["upscale_engine"] == "seedvr2":
        raw = compute_seedvr2_resolution_contract({**raw, "upscale_engine": clean["upscale_engine"], "scale": clean["scale"]})
        clean["seedvr2_alpha_mode"] = _normalize_seedvr2_alpha_mode(raw.get("seedvr2_alpha_mode", DEFAULTS["seedvr2_alpha_mode"]))
        clean["seedvr2_sizing_mode"] = _normalize_seedvr2_sizing_mode(raw.get("seedvr2_sizing_mode", DEFAULTS["seedvr2_sizing_mode"]))
        clean["seedvr2_source_width"] = max(0, _int(raw.get("seedvr2_source_width", 0), 0))
        clean["seedvr2_source_height"] = max(0, _int(raw.get("seedvr2_source_height", 0), 0))
        clean["seedvr2_output_width"] = max(0, _int(raw.get("seedvr2_output_width", 0), 0))
        clean["seedvr2_output_height"] = max(0, _int(raw.get("seedvr2_output_height", 0), 0))
        clean["seedvr2_dit_model"] = _text(raw.get("seedvr2_dit_model", DEFAULTS["seedvr2_dit_model"]))
        clean["seedvr2_vae_model"] = _text(raw.get("seedvr2_vae_model", DEFAULTS["seedvr2_vae_model"]))
        clean["seedvr2_resolution"] = max(256, min(_int(raw.get("seedvr2_resolution", DEFAULTS["seedvr2_resolution"]), DEFAULTS["seedvr2_resolution"]), 4096))
        clean["seedvr2_max_resolution"] = max(0, min(_int(raw.get("seedvr2_max_resolution", DEFAULTS["seedvr2_max_resolution"]), DEFAULTS["seedvr2_max_resolution"]), 8192))
        clean["seedvr2_batch_size"] = _seedvr2_batch_size(raw.get("seedvr2_batch_size", DEFAULTS["seedvr2_batch_size"]))
        clean["seedvr2_seed"] = max(0, min(_int(raw.get("seedvr2_seed", DEFAULTS["seedvr2_seed"]), DEFAULTS["seedvr2_seed"]), 2**32 - 1))
        clean["seedvr2_device"] = _text(raw.get("seedvr2_device", DEFAULTS["seedvr2_device"])) or DEFAULTS["seedvr2_device"]
        clean["seedvr2_offload_device"] = _text(raw.get("seedvr2_offload_device", DEFAULTS["seedvr2_offload_device"])) or DEFAULTS["seedvr2_offload_device"]
        clean["seedvr2_blocks_to_swap"] = max(0, min(_int(raw.get("seedvr2_blocks_to_swap", DEFAULTS["seedvr2_blocks_to_swap"]), DEFAULTS["seedvr2_blocks_to_swap"]), 36))
        clean["seedvr2_swap_io_components"] = _bool(raw.get("seedvr2_swap_io_components", DEFAULTS["seedvr2_swap_io_components"]), DEFAULTS["seedvr2_swap_io_components"])
        clean["seedvr2_cache_models"] = _bool(raw.get("seedvr2_cache_models", DEFAULTS["seedvr2_cache_models"]), DEFAULTS["seedvr2_cache_models"])
        clean["seedvr2_encode_tiled"] = _bool(raw.get("seedvr2_encode_tiled", DEFAULTS["seedvr2_encode_tiled"]), DEFAULTS["seedvr2_encode_tiled"])
        clean["seedvr2_decode_tiled"] = _bool(raw.get("seedvr2_decode_tiled", DEFAULTS["seedvr2_decode_tiled"]), DEFAULTS["seedvr2_decode_tiled"])
        clean["seedvr2_tile_size"] = max(256, min(_int(raw.get("seedvr2_tile_size", DEFAULTS["seedvr2_tile_size"]), DEFAULTS["seedvr2_tile_size"]), 2048))
        clean["seedvr2_tile_overlap"] = max(0, min(_int(raw.get("seedvr2_tile_overlap", DEFAULTS["seedvr2_tile_overlap"]), DEFAULTS["seedvr2_tile_overlap"]), 512))
        attention = _text(raw.get("seedvr2_attention_mode", DEFAULTS["seedvr2_attention_mode"]), DEFAULTS["seedvr2_attention_mode"])
        clean["seedvr2_attention_mode"] = attention if attention in SEEDVR2_ATTENTION_MODES else DEFAULTS["seedvr2_attention_mode"]
        color = _text(raw.get("seedvr2_color_correction", DEFAULTS["seedvr2_color_correction"]), DEFAULTS["seedvr2_color_correction"])
        clean["seedvr2_color_correction"] = color if color in SEEDVR2_COLOR_CORRECTION_OPTIONS else DEFAULTS["seedvr2_color_correction"]
        clean["seedvr2_input_noise_scale"] = round(_clamp(_float(raw.get("seedvr2_input_noise_scale", DEFAULTS["seedvr2_input_noise_scale"]), DEFAULTS["seedvr2_input_noise_scale"]), 0.0, 1.0), 4)
        clean["seedvr2_latent_noise_scale"] = round(_clamp(_float(raw.get("seedvr2_latent_noise_scale", DEFAULTS["seedvr2_latent_noise_scale"]), DEFAULTS["seedvr2_latent_noise_scale"]), 0.0, 1.0), 4)
        clean["seedvr2_enable_debug"] = _bool(raw.get("seedvr2_enable_debug", DEFAULTS["seedvr2_enable_debug"]), DEFAULTS["seedvr2_enable_debug"])
        clean["seedvr2_source_format"] = _text(raw.get("seedvr2_source_format", DEFAULTS["seedvr2_source_format"])).upper()
        clean["seedvr2_source_image_mode"] = _text(raw.get("seedvr2_source_image_mode", DEFAULTS["seedvr2_source_image_mode"])).upper()
        clean["seedvr2_source_has_alpha"] = _bool(raw.get("seedvr2_source_has_alpha", DEFAULTS["seedvr2_source_has_alpha"]), DEFAULTS["seedvr2_source_has_alpha"])
        clean["seedvr2_source_has_transparency"] = _bool(raw.get("seedvr2_source_has_transparency", DEFAULTS["seedvr2_source_has_transparency"]), DEFAULTS["seedvr2_source_has_transparency"])
        clean["seedvr2_alpha_min"] = max(0, min(_int(raw.get("seedvr2_alpha_min", DEFAULTS["seedvr2_alpha_min"]), DEFAULTS["seedvr2_alpha_min"]), 255))
        clean["seedvr2_alpha_max"] = max(0, min(_int(raw.get("seedvr2_alpha_max", DEFAULTS["seedvr2_alpha_max"]), DEFAULTS["seedvr2_alpha_max"]), 255))
        clean["seedvr2_alpha_route_applied"] = _bool(raw.get("seedvr2_alpha_route_applied", DEFAULTS["seedvr2_alpha_route_applied"]), DEFAULTS["seedvr2_alpha_route_applied"])
        clean["seedvr2_output_format"] = "png"

    return clean


def split_payload_parts(settings: dict[str, Any] | str | None = None, *, source_images: Any = None) -> dict[str, dict[str, Any]]:
    clean = normalize_settings(settings)
    assets = {"source_images": _normalize_source_images(source_images if source_images is not None else clean.pop("source_images", None))}
    inputs: dict[str, Any] = {"source_mode": clean.pop("source_mode", DEFAULTS["source_mode"])}
    for link_key in ("source_output_id", "parent_output_id", "source_image_name"):
        if link_key in clean:
            inputs[link_key] = clean.pop(link_key)
    params = clean
    return {"inputs": inputs, "params": params, "assets": assets}


def validate_payload_settings(settings: dict[str, Any] | str | None = None, *, require_source: bool = False, source_images: Any = None) -> dict[str, Any]:
    parts = split_payload_parts(settings, source_images=source_images)
    params = parts["params"]
    inputs = parts["inputs"]
    assets = parts["assets"]
    errors: list[str] = []
    warnings: list[str] = []

    force_rgba = params.get("upscale_engine") == "seedvr2" and params.get("seedvr2_alpha_mode") == "preserve"
    if params.get("restore_assist") == "codeformer" and not params.get("restore_model") and not force_rgba:
        errors.append("CodeFormer restore requires a restore_model before Image Upscale can queue. Place CodeFormer models in ComfyUI/models/facerestore_models/.")
    if params.get("upscale_engine") == "seedvr2":
        if not params.get("seedvr2_dit_model"):
            errors.append("Choose a SeedVR2 DiT model before queueing SeedVR2. Models live in ComfyUI/models/SEEDVR2/.")
        if not params.get("seedvr2_vae_model"):
            errors.append("Choose a SeedVR2 VAE model before queueing SeedVR2. Models live in ComfyUI/models/SEEDVR2/.")
        if params.get("seedvr2_alpha_mode") == "preserve" and params.get("restore_assist") == "codeformer":
            warnings.append("CodeFormer restore will be skipped because Force Preserve RGBA is active and the current restore route is not alpha-safe.")
        elif params.get("seedvr2_alpha_mode") == "auto" and params.get("restore_assist") == "codeformer":
            warnings.append("Auto Preserve applies CodeFormer only to opaque sources and skips it per job when real transparency is detected.")
    has_source_ref = bool(inputs.get("source_output_id") or inputs.get("source_image_name") or assets.get("source_images"))
    if require_source and not has_source_ref:
        errors.append("Pick at least one source image for Image Upscale.")
    if params.get("upscale_engine") == "seedvr2":
        warnings.append("SeedVR2 is experimental for Image Upscale and may use heavy VRAM; install ComfyUI-SeedVR2_VideoUpscaler and place models in ComfyUI/models/SEEDVR2/.")
        if params.get("seedvr2_alpha_mode") == "auto":
            warnings.append("SeedVR2 transparency is detected independently for each source image; transparent sources use the RGBA graph when JoinImageWithAlpha is available.")
    elif not params.get("upscale_model"):
        warnings.append("Image Upscale will use interpolation-only resize because no upscale model is selected.")

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "inputs": inputs,
        "params": params,
        "assets": assets,
    }


def empty_payload_block(enabled: bool = False, *, route: dict[str, Any] | None = None, reason: str | None = None) -> dict[str, Any]:
    support = support_for_route(route or {})
    return {
        "enabled": bool(enabled) and support["state"] in ACTIVE_ROUTE_STATES,
        "version": EXTENSION_VERSION,
        "inputs": {},
        "params": {},
        "assets": {},
        "metadata": {
            "schema_version": PAYLOAD_SCHEMA_VERSION,
            "extension_id": EXTENSION_ID,
            "extension_type": "built_in",
            "workspace_app": WORKSPACE_APP,
            "phase": PAYLOAD_CONTRACT_PHASE,
            "runtime_activation": False,
            "queue_route_activation": True,
            "workflow_graph_mutation": False,
            "route_state": support["state"],
            "gated_reason": reason or support.get("reason"),
        },
    }


def build_payload_block(
    settings: dict[str, Any] | str | None = None,
    *,
    enabled: bool = True,
    route: dict[str, Any] | None = None,
    source_images: Any = None,
    include_validation: bool = True,
) -> dict[str, Any]:
    """Build the canonical Image Upscale extension payload block.

    If the extension is disabled or the route is gated/unsupported, params/assets
    are stripped so hidden or inactive values cannot leak into an active payload.
    """
    support = support_for_route(route or {})
    active = bool(enabled) and support["state"] in ACTIVE_ROUTE_STATES
    if not active:
        block = empty_payload_block(False, route=route, reason=support.get("reason"))
        block["metadata"].update({
            "requested_enabled": bool(enabled),
            "payload_suppressed": True,
        })
        return block

    validation = validate_payload_settings(settings, source_images=source_images)
    metadata = {
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "workspace_app": WORKSPACE_APP,
        "phase": PAYLOAD_CONTRACT_PHASE,
        "runtime_activation": False,
        "queue_route_activation": True,
        "workflow_graph_mutation": False,
        "route": support["route"],
        "route_state": support["state"],
        "route_reason": support.get("reason"),
        "parameter_profile": support.get("parameter_profile"),
        "clean_payload": True,
        "hidden_fields_stripped": True,
    }
    if include_validation:
        metadata["validation"] = {
            "ok": validation["ok"],
            "errors": list(validation["errors"]),
            "warnings": list(validation["warnings"]),
        }
    return {
        "enabled": True,
        "version": EXTENSION_VERSION,
        "inputs": deepcopy(validation["inputs"]),
        "params": deepcopy(validation["params"]),
        "assets": deepcopy(validation["assets"]),
        "metadata": metadata,
    }


def payload_wrapper(block: dict | None = None) -> dict:
    return {"extensions": {EXTENSION_ID: block or empty_payload_block(False)}}


def build_extension_payload(
    settings: dict[str, Any] | str | None = None,
    *,
    enabled: bool = True,
    route: dict[str, Any] | None = None,
    source_images: Any = None,
) -> dict[str, Any]:
    return payload_wrapper(build_payload_block(settings, enabled=enabled, route=route, source_images=source_images))


def _source_asset_signature(asset: dict[str, Any]) -> dict[str, Any]:
    """Return the stable replay/reuse identity for a source image asset."""
    if not isinstance(asset, dict):
        asset = {"name": _text(asset)}
    clean: dict[str, Any] = {}
    for key in (
        "filename",
        "name",
        "stored_filename",
        "path",
        "source_image_name",
        "source_output_id",
        "parent_output_id",
        "result_id",
        "file_id",
        "width",
        "height",
    ):
        value = asset.get(key)
        if value not in (None, "", []):
            clean[key] = deepcopy(value)
    return clean


def _source_assets_for_replay(source: dict[str, Any]) -> list[dict[str, Any]]:
    assets = source.get("assets") if isinstance(source.get("assets"), dict) else {}
    source_images = assets.get("source_images") if isinstance(assets.get("source_images"), list) else []
    clean = [_source_asset_signature(item) for item in source_images]
    clean = [item for item in clean if item]
    inputs = source.get("inputs") if isinstance(source.get("inputs"), dict) else {}
    input_asset = _source_asset_signature({
        "source_image_name": inputs.get("source_image_name"),
        "source_output_id": inputs.get("source_output_id"),
        "parent_output_id": inputs.get("parent_output_id"),
    })
    if input_asset and not clean:
        clean.append(input_asset)
    return clean


def replay_payload_from_block(block: dict[str, Any] | None) -> dict[str, Any]:
    """Build a durable replay payload for Image Upscale output metadata.

    Phase K keeps enough information to rerun the same utility settings, but it
    does not assume the original source file still exists. Restore/reuse callers
    must revalidate route nodes, optional models, and source assets before
    enabling or queueing the extension.
    """
    source = block or empty_payload_block(False)
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    params = deepcopy(source.get("params") or {})
    inputs = deepcopy(source.get("inputs") or {})
    assets = deepcopy(source.get("assets") or {})
    source_assets = _source_assets_for_replay(source)
    return {
        "extension_id": EXTENSION_ID,
        "enabled": bool(source.get("enabled")),
        "version": source.get("version", EXTENSION_VERSION),
        "schema_version": PAYLOAD_SCHEMA_VERSION,
        "replay_phase": REPLAY_CONTRACT_PHASE,
        "restore_policy": REPLAY_RESTORE_POLICY,
        "reuse_policy": "reuse_settings_reselect_or_revalidate_source_assets",
        "inputs": inputs,
        "params": params,
        "assets": assets,
        "source_assets": source_assets,
        "route": deepcopy(metadata.get("route") or {}),
        "route_state": metadata.get("route_state"),
        "model_requirements": {
            "upscale_engine": params.get("upscale_engine", DEFAULTS["upscale_engine"]),
            "upscale_model": params.get("upscale_model", ""),
            "restore_assist": params.get("restore_assist", DEFAULTS["restore_assist"]),
            "restore_model": params.get("restore_model", ""),
            "seedvr2_dit_model": params.get("seedvr2_dit_model", ""),
            "seedvr2_vae_model": params.get("seedvr2_vae_model", ""),
        },
        "readiness": {
            "requires_route_revalidation": True,
            "requires_node_revalidation": True,
            "requires_model_revalidation": True,
            "requires_source_revalidation": True,
            "source_count": len(source_assets),
            "can_reuse_settings_without_source": True,
        },
    }


def validate_replay_payload(
    replay: dict[str, Any] | None,
    *,
    available_source_names: list[str] | set[str] | tuple[str, ...] | None = None,
    require_source: bool = True,
) -> dict[str, Any]:
    """Validate replay metadata before auto-restore/requeue.

    This is intentionally conservative: settings can be reused, but queueing
    should be blocked until source assets are present/reselected.
    """
    payload = replay if isinstance(replay, dict) else {}
    errors: list[str] = []
    warnings: list[str] = []
    if payload.get("extension_id") != EXTENSION_ID:
        errors.append("Replay payload does not belong to Image Upscale.")
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    validation = validate_payload_settings(params, require_source=False)
    errors.extend(validation.get("errors") or [])
    warnings.extend(validation.get("warnings") or [])
    source_assets = payload.get("source_assets")
    if not isinstance(source_assets, list):
        source_assets = []
    if require_source and not source_assets:
        errors.append("Image Upscale replay requires a source image to be reselected or restored.")
    available = {str(item).casefold() for item in (available_source_names or []) if str(item or "").strip()}
    missing_sources: list[str] = []
    if available:
        for asset in source_assets:
            if not isinstance(asset, dict):
                continue
            names = [asset.get(key) for key in ("filename", "name", "stored_filename", "source_image_name")]
            clean_names = [str(item).casefold() for item in names if str(item or "").strip()]
            if clean_names and not any(item in available for item in clean_names):
                missing_sources.append(str(names[0] or clean_names[0]))
    if missing_sources:
        errors.append("Replay source asset(s) are not available: " + ", ".join(missing_sources))
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "source_assets": deepcopy(source_assets),
        "restore_policy": payload.get("restore_policy") or REPLAY_RESTORE_POLICY,
    }


def reuse_settings_from_replay(
    replay: dict[str, Any] | None,
    *,
    source_width: int = 0,
    source_height: int = 0,
    strip_source_links: bool = True,
) -> dict[str, Any]:
    """Create safe settings for reusing an Image Upscale replay on a new source."""
    payload = replay if isinstance(replay, dict) else {}
    settings = deepcopy(payload.get("params") if isinstance(payload.get("params"), dict) else {})
    if strip_source_links:
        for key in ("source_output_id", "parent_output_id", "source_image_name"):
            settings.pop(key, None)
    if source_width:
        settings["seedvr2_source_width"] = int(source_width)
    if source_height:
        settings["seedvr2_source_height"] = int(source_height)
    return normalize_settings(settings)
