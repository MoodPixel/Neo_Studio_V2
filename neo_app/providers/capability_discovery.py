from __future__ import annotations

from typing import Any

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.providers.schema import (
    BackendCapabilityDiscoveryResult,
    BackendLoaderCapability,
    BackendRoleCapability,
)


def _node_exists(object_info: dict[str, Any], aliases: list[str]) -> tuple[bool, str | None]:
    for alias in aliases:
        if alias in object_info:
            return True, alias
    return False, None


def _node_required_inputs(object_info: dict[str, Any], node_name: str | None) -> dict[str, Any]:
    if not node_name:
        return {}
    return (((object_info.get(node_name) or {}).get("input") or {}).get("required") or {})


def _node_optional_inputs(object_info: dict[str, Any], node_name: str | None) -> dict[str, Any]:
    if not node_name:
        return {}
    return (((object_info.get(node_name) or {}).get("input") or {}).get("optional") or {})


def _extract_option_list(value: Any) -> list[str]:
    """Extract Comfy object_info option arrays defensively.

    Comfy normally returns an input shape like [["a", "b"], {meta}], but custom
    nodes are not perfectly consistent, so this parser stays conservative.
    """
    if not isinstance(value, list) or not value:
        return []
    first = value[0]
    if isinstance(first, list):
        return [str(item) for item in first]
    if isinstance(first, tuple):
        return [str(item) for item in first]
    return []


def _extract_assets(object_info: dict[str, Any], node_name: str | None, input_names: list[str]) -> dict[str, list[str]]:
    required = _node_required_inputs(object_info, node_name)
    optional = _node_optional_inputs(object_info, node_name)
    inputs = {**optional, **required}
    assets: dict[str, list[str]] = {}
    for input_name in input_names:
        values = _extract_option_list(inputs.get(input_name))
        if values:
            assets[input_name] = values
    return assets


def _extract_matching_assets(object_info: dict[str, Any], node_name: str | None, token: str) -> dict[str, list[str]]:
    required = _node_required_inputs(object_info, node_name)
    optional = _node_optional_inputs(object_info, node_name)
    inputs = {**optional, **required}
    assets: dict[str, list[str]] = {}
    token = token.lower()
    for input_name, value in inputs.items():
        if token in input_name.lower():
            values = _extract_option_list(value)
            if values:
                assets[input_name] = values
    return assets


def _role(
    role_id: str,
    aliases: list[str],
    object_info: dict[str, Any],
    *,
    asset_inputs: list[str] | None = None,
    notes: list[str] | None = None,
) -> BackendRoleCapability:
    available, node_name = _node_exists(object_info, aliases)
    assets = _extract_assets(object_info, node_name, asset_inputs or []) if available else {}
    return BackendRoleCapability(
        role_id=role_id,
        available=available,
        backend_key=node_name,
        backend_node=node_name,
        aliases=aliases,
        assets=assets,
        notes=notes or [],
    )


def _flatten_assets(roles: dict[str, BackendRoleCapability]) -> dict[str, list[str]]:
    assets: dict[str, list[str]] = {}
    for role_id, role in roles.items():
        for input_name, values in role.assets.items():
            key = f"{role_id}.{input_name}"
            assets[key] = list(values)
    return assets


def _loader(loader_id: str, roles: dict[str, BackendRoleCapability], *, notes: list[str] | None = None) -> BackendLoaderCapability:
    return BackendLoaderCapability(
        loader_id=loader_id,
        available=any(role.available for role in roles.values()),
        roles=roles,
        assets=_flatten_assets(roles),
        notes=notes or [],
    )


def discover_comfy_backend_capabilities(
    object_info: dict[str, Any] | None,
    *,
    provider_id: str = "comfyui",
    reachable: bool = True,
    error: str | None = None,
) -> BackendCapabilityDiscoveryResult:
    """Map Comfy `/object_info` into backend-neutral loader capability roles.

    This intentionally does not compile workflows or expose Comfy node names as
    core contracts. Node names are recorded only as provider diagnostics.
    """
    object_info = object_info or {}
    warnings: list[str] = []
    errors: list[str] = []
    if error:
        errors.append(error)

    checkpoint_roles = {
        "checkpoint": _role("checkpoint", ["CheckpointLoaderSimple"], object_info, asset_inputs=["ckpt_name"]),
        "vae": _role("vae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
        "clip_skip": _role("clip_skip", ["CLIPSetLastLayer"], object_info),
        "lora": _role("lora", ["LoraLoader", "LoraLoaderModelOnly"], object_info, asset_inputs=["lora_name"]),
    }

    diffusion_roles = {
        "diffusion_model": _role(
            "diffusion_model",
            ["UNETLoader", "DiffusionModelLoader", "LoadDiffusionModel"],
            object_info,
            asset_inputs=["unet_name", "model_name", "diffusion_model_name"],
        ),
        "text_encoder_primary": _role("text_encoder_primary", ["CLIPLoader"], object_info, asset_inputs=["clip_name", "clip_name1"]),
        "text_encoder_secondary": _role("text_encoder_secondary", ["DualCLIPLoader"], object_info, asset_inputs=["clip_name2"]),
        "vae_or_ae": _role("vae_or_ae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
        "flux_guidance": _role("flux_guidance", ["FluxGuidance"], object_info),
        "aura_sampling": _role("aura_sampling", ["ModelSamplingAuraFlow"], object_info),
        "sampler": _role("sampler", ["KSampler"], object_info, asset_inputs=["sampler_name", "scheduler"]),
        "wan_model": _role(
            "wan_model",
            ["UNETLoader", "DiffusionModelLoader", "LoadDiffusionModel"],
            object_info,
            asset_inputs=["unet_name", "model_name", "diffusion_model_name"],
            notes=["Phase 12.17 diagnostic role only; Wan image compilers remain provider-gated."],
        ),
        "umt5_text_encoder": _role("umt5_text_encoder", ["CLIPLoader"], object_info, asset_inputs=["clip_name", "clip_name1"]),
        "wan_vae": _role("wan_vae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
    }

    unet_roles = {
        "unet": _role(
            "unet",
            ["UNETLoader", "DiffusionModelLoader", "LoadDiffusionModel"],
            object_info,
            asset_inputs=["unet_name", "model_name", "diffusion_model_name"],
        ),
        "vae_or_ae": _role("vae_or_ae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
    }

    gguf_roles = {
        "gguf_unet": _role(
            "gguf_unet",
            ["UnetLoaderGGUF", "LoaderGGUF"],
            object_info,
            asset_inputs=["unet_name", "model_name", "gguf_name"],
        ),
        "gguf_text_encoder_primary": _role(
            "gguf_text_encoder_primary",
            ["CLIPLoaderGGUF", "ClipLoaderGGUF"],
            object_info,
            asset_inputs=["clip_name", "clip_name1", "text_encoder_name"],
        ),
        "gguf_text_encoder_secondary": _role(
            "gguf_text_encoder_secondary",
            ["DualCLIPLoaderGGUF"],
            object_info,
            asset_inputs=["clip_name2", "text_encoder_name2"],
        ),
        "gguf_vae": _role("gguf_vae", ["VaeGGUF", "VAELoaderGGUF"], object_info, asset_inputs=["vae_name", "gguf_name"]),
        "vae_or_ae": _role("vae_or_ae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
        "qwen_mmproj": _role("qwen_mmproj", ["CLIPLoaderGGUF", "ClipLoaderGGUF", "DualCLIPLoaderGGUF"], object_info),
        "flux_guidance": _role("flux_guidance", ["FluxGuidance"], object_info),
        "aura_sampling": _role("aura_sampling", ["ModelSamplingAuraFlow"], object_info),
        "sampler": _role("sampler", ["KSampler"], object_info, asset_inputs=["sampler_name", "scheduler"]),
        "umt5_text_encoder": _role("umt5_text_encoder", ["CLIPLoaderGGUF", "ClipLoaderGGUF"], object_info, asset_inputs=["clip_name", "clip_name1", "text_encoder_name"]),
        "wan_vae": _role("wan_vae", ["VaeGGUF", "VAELoaderGGUF"], object_info, asset_inputs=["vae_name", "gguf_name"]),
    }

    # mmproj is often an input on GGUF/text-encoder nodes rather than a node name.
    mmproj_assets: dict[str, list[str]] = {}
    for candidate in ["CLIPLoaderGGUF", "ClipLoaderGGUF", "DualCLIPLoaderGGUF"]:
        mmproj_assets.update(_extract_matching_assets(object_info, candidate, "mmproj"))
    if mmproj_assets:
        gguf_roles["qwen_mmproj"].available = True
        gguf_roles["qwen_mmproj"].backend_key = gguf_roles["qwen_mmproj"].backend_key or "mmproj_input"
        gguf_roles["qwen_mmproj"].backend_node = gguf_roles["qwen_mmproj"].backend_node or "mmproj_input"
        gguf_roles["qwen_mmproj"].assets = mmproj_assets
        gguf_roles["qwen_mmproj"].notes.append("Detected mmproj input/options from Comfy object_info.")

    if not reachable:
        warnings.append("Provider is not reachable; capability discovery returned an offline snapshot.")
    if object_info and not gguf_roles["gguf_unet"].available:
        warnings.append("GGUF UNet loader capability was not detected from Comfy object_info.")

    loaders = {
        "checkpoint": _loader("checkpoint", checkpoint_roles),
        "diffusion_model": _loader("diffusion_model", diffusion_roles),
        "unet": _loader("unet", unet_roles),
        "gguf": _loader("gguf", gguf_roles, notes=["Comfy node names are diagnostics only; core contracts use logical roles."]),
        "api_model": BackendLoaderCapability(loader_id="api_model", available=False, notes=["Comfy local backend does not expose API-model loader capability."]),
    }

    return BackendCapabilityDiscoveryResult(
        provider_id=provider_id,
        backend="comfyui",
        discovery_version="0.1.0",
        discovery_status="available" if reachable and object_info else "offline",
        reachable=reachable,
        object_info_available=bool(object_info),
        loaders=loaders,
        warnings=warnings,
        errors=errors,
    )


def discovery_result_to_dict(result: BackendCapabilityDiscoveryResult) -> dict[str, Any]:
    return model_to_dict(result)
