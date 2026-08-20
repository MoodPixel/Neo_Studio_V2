from __future__ import annotations

from typing import Any

VAE_DECODE_MODE_NATIVE = "native"
VAE_DECODE_MODE_VAE_UTILS = "vae_utils_auto"

_QWEN_SUPPORTED_FAMILIES = {"qwen_image", "qwen_image_edit_2509", "qwen_image_edit_2511"}
_KREA_SUPPORTED_FAMILIES = {"krea2", "krea2_turbo"}
_SUPPORTED_MODES = {"txt2img", "img2img", "edit"}
_REQUIRED_VAE_UTILS_NODES = {"VAEUtils_CustomVAELoader", "VAEUtils_VAEDecodeTiled"}


def normalize_vae_decode_mode(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"vae_utils", "vae_utils_auto", "custom_vae_utils", "wan_2x", "wan2x", "wan_upscale_2x"}:
        return VAE_DECODE_MODE_VAE_UTILS
    return VAE_DECODE_MODE_NATIVE


def route_supports_vae_utils(*, family: Any = "", loader: Any = "", mode: Any = "") -> bool:
    resolved_family = str(family or "").strip().lower()
    resolved_loader = str(loader or "").strip().lower()
    resolved_mode = str(mode or "txt2img").strip().lower()
    if resolved_mode not in _SUPPORTED_MODES:
        return False
    if resolved_family in _QWEN_SUPPORTED_FAMILIES:
        return resolved_loader in {"diffusion_model", "gguf"}
    if resolved_family in _KREA_SUPPORTED_FAMILIES:
        return resolved_loader in {"diffusion_model", "gguf"}
    return False


def backend_supports_vae_utils(backend_capabilities: dict[str, Any] | None) -> bool:
    capabilities = backend_capabilities or {}
    diagnostics = capabilities.get("vae_utils_decode_diagnostics") or {}
    if isinstance(diagnostics, dict) and diagnostics.get("available") is True:
        return True
    node_map = capabilities.get("object_info_node_inputs") or {}
    if not isinstance(node_map, dict):
        return False
    return _REQUIRED_VAE_UTILS_NODES.issubset(set(node_map.keys()))


def resolve_vae_decode_strategy(
    *,
    params: dict[str, Any] | None,
    family: Any,
    loader: Any,
    mode: Any,
    backend_capabilities: dict[str, Any] | None = None,
    validation: Any | None = None,
) -> dict[str, Any]:
    requested = normalize_vae_decode_mode((params or {}).get("vae_decode_mode"))
    supported_route = route_supports_vae_utils(family=family, loader=loader, mode=mode)
    node_support = backend_supports_vae_utils(backend_capabilities)
    vae_name = str((params or {}).get("vae") or (params or {}).get("qwen_vae") or (params or {}).get("vae_or_ae") or "").strip()
    incompatible_gguf_vae = requested == VAE_DECODE_MODE_VAE_UTILS and vae_name.lower().endswith(".gguf")
    active = requested == VAE_DECODE_MODE_VAE_UTILS and supported_route and not incompatible_gguf_vae
    if incompatible_gguf_vae and validation is not None:
        message = "VAE Utils decode requires a normal Comfy VAE file (for example the Wan/Qwen 2x safetensors VAE); GGUF VAE files must use the native GGUF VAE loader."
        if message not in validation.errors:
            validation.errors.append(message)
        validation.ok = False
    if requested == VAE_DECODE_MODE_VAE_UTILS and not supported_route and validation is not None:
        message = f"VAE Utils decode is not supported for {family}/{loader}/{mode}; falling back to native VAE decode."
        if message not in validation.warnings:
            validation.warnings.append(message)
    if active and not node_support and validation is not None:
        message = "VAE Utils decode requires ComfyUI-VAE-Utils with VAEUtils_CustomVAELoader and VAEUtils_VAEDecodeTiled visible in live /object_info."
        if message not in validation.errors:
            validation.errors.append(message)
        validation.ok = False
    resolved = VAE_DECODE_MODE_VAE_UTILS if active else VAE_DECODE_MODE_NATIVE
    return {
        "requested": requested,
        "resolved": resolved,
        "enabled": resolved == VAE_DECODE_MODE_VAE_UTILS,
        "supported_route": supported_route,
        "node_support": node_support,
        "loader_node_class": "VAEUtils_CustomVAELoader" if resolved == VAE_DECODE_MODE_VAE_UTILS else "VAELoader",
        "decode_node_class": "VAEUtils_VAEDecodeTiled" if resolved == VAE_DECODE_MODE_VAE_UTILS else "VAEDecode",
        "note": (
            "Uses ComfyUI-VAE-Utils CustomVAELoader + VAEDecodeTiled with auto upscale detection (-1)."
            if resolved == VAE_DECODE_MODE_VAE_UTILS
            else "Uses standard Comfy VAELoader + VAEDecode."
        ),
    }


def build_vae_loader_node(vae_name: str, strategy: dict[str, Any]) -> dict[str, Any]:
    if strategy.get("enabled"):
        return {
            "class_type": "VAEUtils_CustomVAELoader",
            "inputs": {
                "vae_name": vae_name,
                "disable_offload": True,
            },
        }
    return {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}}


def build_vae_decode_node(samples_ref: list[Any], vae_ref: list[Any], strategy: dict[str, Any]) -> dict[str, Any]:
    if strategy.get("enabled"):
        return {
            "class_type": "VAEUtils_VAEDecodeTiled",
            "inputs": {
                "samples": list(samples_ref),
                "vae": list(vae_ref),
                "upscale": -1,
                "tile": False,
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 4096,
                "temporal_overlap": 64,
            },
        }
    return {"class_type": "VAEDecode", "inputs": {"samples": list(samples_ref), "vae": list(vae_ref)}}


def vae_decode_profile_payload(strategy: dict[str, Any]) -> dict[str, Any]:
    return {
        "requested_mode": strategy.get("requested") or VAE_DECODE_MODE_NATIVE,
        "resolved_mode": strategy.get("resolved") or VAE_DECODE_MODE_NATIVE,
        "enabled": bool(strategy.get("enabled")),
        "supported_route": bool(strategy.get("supported_route")),
        "node_support": bool(strategy.get("node_support")),
        "loader_node_class": strategy.get("loader_node_class") or "VAELoader",
        "decode_node_class": strategy.get("decode_node_class") or "VAEDecode",
        "note": strategy.get("note") or "",
    }
