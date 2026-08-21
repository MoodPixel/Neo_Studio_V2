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


def _role_requiring_option(
    role_id: str,
    aliases: list[str],
    object_info: dict[str, Any],
    *,
    option_input: str,
    required_option: str,
    asset_inputs: list[str] | None = None,
    notes: list[str] | None = None,
) -> BackendRoleCapability:
    """Expose a role only when a live Comfy node advertises a required enum option.

    Architecture-specific CLIP loader types are not interchangeable. Merely
    detecting CLIPLoader is insufficient for Krea 2 because older Comfy builds
    may not advertise type=krea2.
    """
    node_exists, node_name = _node_exists(object_info, aliases)
    required = _node_required_inputs(object_info, node_name) if node_exists else {}
    optional = _node_optional_inputs(object_info, node_name) if node_exists else {}
    values = _extract_option_list(({**optional, **required}).get(option_input))
    normalized = {str(value).strip().casefold() for value in values}
    option_ok = str(required_option).strip().casefold() in normalized
    assets = _extract_assets(object_info, node_name, asset_inputs or []) if node_exists else {}
    role_notes = list(notes or [])
    if node_exists and not option_ok:
        role_notes.append(f"{node_name} does not advertise {option_input}={required_option}.")
    return BackendRoleCapability(
        role_id=role_id,
        available=bool(node_exists and option_ok),
        backend_key=node_name,
        backend_node=node_name,
        aliases=aliases,
        assets=assets,
        notes=role_notes,
    )


def _role_requiring_inputs(
    role_id: str,
    aliases: list[str],
    object_info: dict[str, Any],
    *,
    required_inputs: list[str],
    asset_inputs: list[str] | None = None,
    notes: list[str] | None = None,
) -> BackendRoleCapability:
    """Expose a custom-node role only when its live socket contract is new enough.

    This is used for third-party workflow nodes whose class name can remain stable
    while important optional sockets are added across releases.  Neo must not
    compile a newer graph against an older node pack and hope Comfy ignores it.
    """
    node_exists, node_name = _node_exists(object_info, aliases)
    required = _node_required_inputs(object_info, node_name) if node_exists else {}
    optional = _node_optional_inputs(object_info, node_name) if node_exists else {}
    names = set(required) | set(optional)
    missing = [name for name in required_inputs if name not in names]
    assets = _extract_assets(object_info, node_name, asset_inputs or []) if node_exists else {}
    role_notes = list(notes or [])
    if node_exists and missing:
        role_notes.append(f"{node_name} is present but missing required Neo sockets: {', '.join(missing)}.")
    return BackendRoleCapability(
        role_id=role_id,
        available=bool(node_exists and not missing),
        backend_key=node_name,
        backend_node=node_name,
        aliases=aliases,
        assets=assets,
        notes=role_notes,
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
        "qwen3vl_4b_text_encoder": _role("qwen3vl_4b_text_encoder", ["CLIPLoader"], object_info, asset_inputs=["clip_name", "clip_name1"], notes=["Krea 2 asset lane; runtime still requires CLIPLoader type=krea2."]),
        "qwen_image_vae": _role("qwen_image_vae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
        "krea2_clip_loader": _role_requiring_option("krea2_clip_loader", ["CLIPLoader"], object_info, option_input="type", required_option="krea2", asset_inputs=["clip_name", "clip_name1"], notes=["Krea 2 requires Comfy CLIPLoader(type=krea2)."]),
        "krea2_edit_model_patch": _role_requiring_inputs("krea2_edit_model_patch", ["Krea2EditModelPatch"], object_info, required_inputs=["model", "source_latent", "source_latent_b", "ref_boost", "ref_boost_a", "fit_mode", "vae", "source_image", "source_image_b", "target_latent"], notes=["ComfyUI-Krea2Edit v1.2+ appearance-path patch node for Krea 2 Identity Edit."]),
        "krea2_edit_grounded_encode": _role_requiring_inputs("krea2_edit_grounded_encode", ["Krea2EditGroundedEncode"], object_info, required_inputs=["clip", "prompt", "image", "image_b", "grounding_px", "system_prompt"], notes=["ComfyUI-Krea2Edit v1.2+ Qwen3-VL image-grounded instruction encoder."]),
        "krea2_identity_lora_loader": _role("krea2_identity_lora_loader", ["LoraLoaderModelOnly"], object_info, asset_inputs=["lora_name"], notes=["Krea 2 Identity Edit applies its trained LoRA to the diffusion model only, before Krea2EditModelPatch."]),
        "krea2_edit_target_latent": _role("krea2_edit_target_latent", ["EmptySD3LatentImage"], object_info, notes=["Krea 2 Identity Edit uses a clean target-noise latent and wires the same latent into target_latent for pre-encoding."]),
        "qwen_image_clip_loader": _role_requiring_option("qwen_image_clip_loader", ["CLIPLoader", "CLIPLoaderGGUF", "ClipLoaderGGUF"], object_info, option_input="type", required_option="qwen_image", asset_inputs=["clip_name", "clip_name1", "text_encoder_name"], notes=["Qwen Image diffusion-model routes may use CLIPLoader(type=qwen_image) for native encoders or CLIPLoaderGGUF(type=qwen_image) for GGUF encoders."]),
        "qwen_image_edit_text_encoder": _role_requiring_option("qwen_image_edit_text_encoder", ["CLIPLoader", "CLIPLoaderGGUF", "ClipLoaderGGUF"], object_info, option_input="type", required_option="qwen_image", asset_inputs=["clip_name", "clip_name1", "text_encoder_name"], notes=["Qwen Image Edit 2509/2511 diffusion-model routes may use native or GGUF Qwen encoders while keeping the main model in safetensors/components."]),
        "lumina2_clip_loader": _role_requiring_option("lumina2_clip_loader", ["CLIPLoader"], object_info, option_input="type", required_option="lumina2", asset_inputs=["clip_name", "clip_name1"], notes=["Z-Image LanPaint requires CLIPLoader(type=lumina2)."]),
        "text_encoder_secondary": _role("text_encoder_secondary", ["DualCLIPLoader"], object_info, asset_inputs=["clip_name2"]),
        "sd3_triple_clip_loader": _role("sd3_triple_clip_loader", ["TripleCLIPLoader"], object_info, asset_inputs=["clip_name1", "clip_name2", "clip_name3"], notes=["SD 3.5 uses CLIP-L, CLIP-G and T5XXL through TripleCLIPLoader."]),
        "sd3_clip_l": _role("sd3_clip_l", ["TripleCLIPLoader"], object_info, asset_inputs=["clip_name1"]),
        "sd3_clip_g": _role("sd3_clip_g", ["TripleCLIPLoader"], object_info, asset_inputs=["clip_name2"]),
        "sd3_t5xxl": _role("sd3_t5xxl", ["TripleCLIPLoader"], object_info, asset_inputs=["clip_name3"]),
        "sd3_vae": _role("sd3_vae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
        "sd3_sampling": _role("sd3_sampling", ["ModelSamplingSD3"], object_info),
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
        "qwen3vl_4b_text_encoder": _role("qwen3vl_4b_text_encoder", ["CLIPLoader"], object_info, asset_inputs=["clip_name", "clip_name1"], notes=["Krea 2 GGUF keeps the Qwen3-VL-4B encoder native/safetensors."]),
        "qwen_image_vae": _role("qwen_image_vae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
        "krea2_clip_loader": _role_requiring_option("krea2_clip_loader", ["CLIPLoader"], object_info, option_input="type", required_option="krea2", asset_inputs=["clip_name", "clip_name1"], notes=["Krea 2 GGUF uses native CLIPLoader(type=krea2), not CLIPLoaderGGUF."]),
        "krea2_edit_model_patch": _role_requiring_inputs("krea2_edit_model_patch", ["Krea2EditModelPatch"], object_info, required_inputs=["model", "source_latent", "source_latent_b", "ref_boost", "ref_boost_a", "fit_mode", "vae", "source_image", "source_image_b", "target_latent"], notes=["ComfyUI-Krea2Edit v1.2+ works after either native or GGUF Krea 2 diffusion loading."]),
        "krea2_edit_grounded_encode": _role_requiring_inputs("krea2_edit_grounded_encode", ["Krea2EditGroundedEncode"], object_info, required_inputs=["clip", "prompt", "image", "image_b", "grounding_px", "system_prompt"], notes=["GGUF Krea 2 Identity Edit still grounds through the native/safetensors Qwen3-VL-4B CLIP stack."]),
        "krea2_identity_lora_loader": _role("krea2_identity_lora_loader", ["LoraLoaderModelOnly"], object_info, asset_inputs=["lora_name"], notes=["Identity Edit LoRA stays safetensors/model-only even when the Krea 2 transformer is GGUF."]),
        "krea2_edit_target_latent": _role("krea2_edit_target_latent", ["EmptySD3LatentImage"], object_info, notes=["Krea 2 Identity Edit target latent / pre-encode synchronization node."]),
        "gguf_text_encoder_primary": _role(
            "gguf_text_encoder_primary",
            ["CLIPLoaderGGUF", "ClipLoaderGGUF"],
            object_info,
            asset_inputs=["clip_name", "clip_name1", "text_encoder_name"],
        ),
        "qwen_image_clip_loader": _role_requiring_option(
            "qwen_image_clip_loader", ["CLIPLoaderGGUF", "ClipLoaderGGUF"], object_info,
            option_input="type", required_option="qwen_image",
            asset_inputs=["clip_name", "clip_name1", "text_encoder_name"],
            notes=["Qwen Image GGUF keeps architecture-specific CLIP type=qwen_image."],
        ),
        "qwen_image_edit_text_encoder": _role_requiring_option(
            "qwen_image_edit_text_encoder", ["CLIPLoaderGGUF", "ClipLoaderGGUF"], object_info,
            option_input="type", required_option="qwen_image",
            asset_inputs=["clip_name", "clip_name1", "text_encoder_name"],
            notes=["Qwen Image Edit GGUF keeps architecture-specific CLIP type=qwen_image; MMProj remains an explicit sidecar selection."],
        ),
        "lumina2_clip_loader": _role_requiring_option(
            "lumina2_clip_loader", ["CLIPLoaderGGUF", "ClipLoaderGGUF"], object_info,
            option_input="type", required_option="lumina2",
            asset_inputs=["clip_name", "clip_name1", "text_encoder_name"],
            notes=["Z-Image GGUF keeps architecture-specific CLIP type=lumina2."],
        ),
        "gguf_text_encoder_secondary": _role(
            "gguf_text_encoder_secondary",
            ["DualCLIPLoaderGGUF"],
            object_info,
            asset_inputs=["clip_name2", "text_encoder_name2"],
        ),
        "sd3_triple_clip_loader_gguf": _role("sd3_triple_clip_loader_gguf", ["TripleCLIPLoaderGGUF"], object_info, asset_inputs=["clip_name1", "clip_name2", "clip_name3"], notes=["SD 3.5 GGUF supports native or GGUF encoder files through TripleCLIPLoaderGGUF."]),
        "sd3_clip_l": _role("sd3_clip_l", ["TripleCLIPLoaderGGUF"], object_info, asset_inputs=["clip_name1"]),
        "sd3_clip_g": _role("sd3_clip_g", ["TripleCLIPLoaderGGUF"], object_info, asset_inputs=["clip_name2"]),
        "sd3_t5xxl": _role("sd3_t5xxl", ["TripleCLIPLoaderGGUF"], object_info, asset_inputs=["clip_name3"]),
        "sd3_vae": _role("sd3_vae", ["VAELoader"], object_info, asset_inputs=["vae_name"]),
        "sd3_sampling": _role("sd3_sampling", ["ModelSamplingSD3"], object_info),
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
