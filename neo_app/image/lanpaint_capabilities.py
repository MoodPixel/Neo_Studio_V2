from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import PurePath
from typing import Any, Iterable, Mapping

from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.image.lanpaint_family_adapter import get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_capability_discovery import lanpaint_snapshot_freshness
from neo_app.image.lanpaint_route_contract import (
    ENGINE_ID,
    MODE_ID,
    SUPPORTED_MODES,
    normalize_family_id,
    normalize_loader_id,
    normalize_provider_id,
)

SCHEMA_ID = "neo.image.lanpaint_route_capabilities.v1"
SCHEMA_VERSION = 1
AUTHORITY = "neo_app.image.lanpaint_capabilities"
PHASE8_STATE = "capability_gating_and_diagnostics"
SUPPORTED_PROVIDERS = ("comfyui", "comfyui_portable")
READY_STATUS = "experimental_available"
BLOCKED_NODE_STATUS = "blocked_missing_nodes"
BLOCKED_MODEL_STATUS = "blocked_missing_models"
BLOCKED_STALE_STATUS = "blocked_stale_capability_snapshot"
UNSUPPORTED_STATUS = "unsupported"

# The concrete Krea 2 Turbo GGUF graph needs every exact node below. The model
# loader is an alias group because ComfyUI-GGUF has shipped both class names.
REQUIRED_NODE_GROUPS: tuple[dict[str, Any], ...] = (
    {"role": "source_loader", "aliases": ("LoadImage",), "pack_id": "comfy-core"},
    {"role": "mask_converter", "aliases": ("ImageToMask",), "pack_id": "comfy-core"},
    {"role": "clip_loader", "aliases": ("CLIPLoader",), "pack_id": "comfy-core"},
    {"role": "positive_conditioning", "aliases": ("CLIPTextEncode",), "pack_id": "comfy-core"},
    {"role": "negative_conditioning", "aliases": ("ConditioningZeroOut",), "pack_id": "comfy-core"},
    {"role": "vae_loader", "aliases": ("VAELoader",), "pack_id": "comfy-core"},
    {"role": "gguf_model_loader", "aliases": ("UnetLoaderGGUF", "LoaderGGUF"), "pack_id": "ComfyUI-GGUF"},
    {"role": "crop_by_mask", "aliases": ("CropByMask",), "pack_id": "ComfyUI-InpaintEasy"},
    {"role": "processing_resize", "aliases": ("ImageResizeKJv2",), "pack_id": "ComfyUI-KJNodes"},
    {"role": "mask_refinement", "aliases": ("GrowMaskWithBlur",), "pack_id": "ComfyUI-KJNodes"},
    {"role": "latent_encode", "aliases": ("VAEEncode",), "pack_id": "comfy-core"},
    {"role": "latent_noise_mask", "aliases": ("SetLatentNoiseMask",), "pack_id": "comfy-core"},
    {"role": "differential_diffusion", "aliases": ("DifferentialDiffusionAdvanced",), "pack_id": "ComfyUI-KJNodes"},
    {"role": "lanpaint_sampler", "aliases": ("LanPaint_KSampler",), "pack_id": "LanPaint"},
    {"role": "latent_decode", "aliases": ("VAEDecode",), "pack_id": "comfy-core"},
    {"role": "source_composite", "aliases": ("ImageCompositeMasked",), "pack_id": "comfy-core"},
    {"role": "output_handoff", "aliases": ("PreviewImage",), "pack_id": "comfy-core"},
)

CONDITIONAL_NODE_GROUPS: dict[str, dict[str, Any]] = {
    "invert_mask": {"role": "invert_mask", "aliases": ("InvertMask",), "pack_id": "comfy-core"},
    "model_only_lora": {"role": "model_only_lora", "aliases": ("LoraLoaderModelOnly",), "pack_id": "comfy-core"},
}

# Execution-critical inputs only. Optional inputs may vary safely across node
# versions; any required input drift must block the route before queue submit.
REQUIRED_NODE_INPUTS: dict[str, tuple[str, ...]] = {
    "LoadImage": ("image",),
    "ImageToMask": ("image", "channel"),
    "InvertMask": ("mask",),
    "CheckpointLoaderSimple": ("ckpt_name",),
    "CLIPLoader": ("clip_name", "type", "device"),
    "TripleCLIPLoader": ("clip_name1", "clip_name2", "clip_name3"),
    "TripleCLIPLoaderGGUF": ("clip_name1", "clip_name2", "clip_name3"),
    "QuadrupleCLIPLoader": ("clip_name1", "clip_name2", "clip_name3", "clip_name4"),
    "QuadrupleCLIPLoaderGGUF": ("clip_name1", "clip_name2", "clip_name3", "clip_name4"),
    "CLIPLoaderGGUF": ("clip_name", "type", "device"),
    "ClipLoaderGGUF": ("clip_name", "type", "device"),
    "CLIPTextEncode": ("clip", "text"),
    "ConditioningZeroOut": ("conditioning",),
    "VAELoader": ("vae_name",),
    "UnetLoaderGGUF": ("unet_name",),
    "LoaderGGUF": ("gguf_name",),
    "LoraLoader": ("model", "clip", "lora_name", "strength_model", "strength_clip"),
    "LoraLoaderModelOnly": ("model", "lora_name", "strength_model"),
    "ModelSamplingAuraFlow": ("model", "shift"),
    "ModelSamplingSD3": ("model", "shift"),
    "UNETLoader": ("unet_name", "weight_dtype"),
    "CropByMask": ("image", "mask", "padding"),
    "ImageResizeKJv2": (
        "image", "width", "height", "upscale_method", "keep_proportion",
        "pad_color", "crop_position", "divisible_by",
    ),
    "GrowMaskWithBlur": (
        "mask", "expand", "incremental_expandrate", "tapered_corners",
        "flip_input", "blur_radius", "lerp_alpha", "decay_factor",
    ),
    "VAEEncode": ("pixels", "vae"),
    "SetLatentNoiseMask": ("samples", "mask"),
    "DifferentialDiffusionAdvanced": ("model", "samples", "mask", "multiplier"),
    "LanPaint_KSampler": (
        "model", "positive", "negative", "latent_image", "seed", "steps",
        "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps",
        "LanPaint_PromptMode", "LanPaint_Info", "Inpainting_mode",
    ),
    "RandomNoise": ("noise_seed",),
    "KSamplerSelect": ("sampler_name",),
    "Ideogram4Scheduler": ("steps", "width", "height", "mu", "std"),
    "DualModelGuider": ("model", "positive", "model_negative", "negative", "cfg"),
    "LanPaint_SamplerCustomAdvanced": (
        "noise", "guider", "sampler", "sigmas", "latent_image",
        "LanPaint_NumSteps", "LanPaint_Lambda", "LanPaint_StepSize",
        "LanPaint_Beta", "LanPaint_Friction", "LanPaint_PromptMode",
        "LanPaint_EarlyStop", "LanPaint_Info", "LanPaint_InnerThreshold",
        "LanPaint_InnerPatience",
    ),
    "VAEDecode": ("samples", "vae"),
    "ImageCompositeMasked": ("destination", "source", "mask", "x", "y", "resize_source"),
    "PreviewImage": ("images",),
}

_ASSET_ROLE_CONFIG: dict[str, dict[str, Any]] = {
    "model": {
        "loader_id": "gguf",
        "role_id": "gguf_unet",
        "label": "Krea 2 Turbo GGUF transformer",
        "tokens_all": ("krea", "turbo"),
        "suffixes": (".gguf",),
    },
    "text_encoder": {
        "loader_id": "gguf",
        "role_id": "krea2_clip_loader",
        "label": "Qwen3-VL-4B Krea 2 text encoder",
        "tokens_any": ("qwen3vl", "qwen3-vl", "qwen3_vl"),
        "tokens_all": ("4b",),
    },
    "vae": {
        "loader_id": "gguf",
        "role_id": "qwen_image_vae",
        "label": "Qwen Image VAE",
        "tokens_all": ("qwen", "image", "vae"),
    },
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _normal_name(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return text.casefold()


def _basename(value: Any) -> str:
    text = str(value or "").strip().replace("\\", "/")
    return PurePath(text).name.casefold()


def _input_names(payload: Mapping[str, Any] | None) -> set[str]:
    data = _mapping(payload)
    names: set[str] = set()
    for key in ("required", "optional", "all"):
        raw = data.get(key)
        if isinstance(raw, (list, tuple, set)):
            names.update(str(item) for item in raw)
    return names


def _node_map(backend_capabilities: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(backend_capabilities.get("object_info_node_inputs"))


def _loader_role(backend_capabilities: Mapping[str, Any], loader_id: str, role_id: str) -> dict[str, Any]:
    loaders = _mapping(backend_capabilities.get("loaders"))
    loader = _mapping(loaders.get(loader_id))
    return _mapping(_mapping(loader.get("roles")).get(role_id))


def _role_assets(backend_capabilities: Mapping[str, Any], loader_id: str, role_id: str) -> list[str]:
    role = _loader_role(backend_capabilities, loader_id, role_id)
    assets = _mapping(role.get("assets"))
    values: list[str] = []
    for raw in assets.values():
        if isinstance(raw, (list, tuple, set)):
            values.extend(str(item) for item in raw if str(item).strip())
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        key = _normal_name(value)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def _asset_matches(name: str, config: Mapping[str, Any]) -> bool:
    normalized = _normal_name(name)
    base = _basename(name)
    tokens_all = tuple(str(item).casefold() for item in config.get("tokens_all", ()))
    tokens_any = tuple(str(item).casefold() for item in config.get("tokens_any", ()))
    tokens_none = tuple(str(item).casefold() for item in config.get("tokens_none", ()))
    suffixes = tuple(str(item).casefold() for item in config.get("suffixes", ()))
    if tokens_all and not all(token in normalized for token in tokens_all):
        return False
    if tokens_any and not any(token in normalized for token in tokens_any):
        return False
    if tokens_none and any(token in normalized for token in tokens_none):
        return False
    if suffixes and not base.endswith(suffixes):
        return False
    return True


def _selected_in_catalog(selected: str, catalog: Iterable[str]) -> bool:
    selected_full = _normal_name(selected)
    selected_base = _basename(selected)
    for candidate in catalog:
        if selected_full == _normal_name(candidate) or selected_base == _basename(candidate):
            return True
    return False


def _issue(code: str, message: str, *, field: str = "route", pack_id: str = "", node_classes: Iterable[str] = (), assets: Iterable[str] = ()) -> dict[str, Any]:
    return {
        "code": code,
        "field": field,
        "message": message,
        "pack_id": pack_id,
        "node_classes": [str(item) for item in node_classes],
        "assets": [str(item) for item in assets],
    }


def _fingerprint(report: Mapping[str, Any]) -> str:
    payload = deepcopy(dict(report))
    payload.pop("capability_fingerprint", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _route_policy_requirements(
    provider_id: str,
    family_id: str,
    loader_id: str,
    *,
    family_adapter: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    adapter = deepcopy(dict(family_adapter)) if isinstance(family_adapter, Mapping) else get_lanpaint_family_adapter(
        family_id,
        loader=loader_id,
        provider_id=provider_id,
        mode=MODE_ID,
        engine=ENGINE_ID,
    )
    binding = _mapping(adapter.get("binding"))
    loaders = _mapping(adapter.get("loaders"))
    model = _mapping(loaders.get("model"))
    text = _mapping(loaders.get("text_encoder"))
    vae = _mapping(loaders.get("vae"))
    conditioning = _mapping(adapter.get("conditioning"))
    negative = _mapping(conditioning.get("negative"))
    lora = _mapping(adapter.get("lora"))
    return {
        "adapter": adapter,
        "policy": _mapping(adapter.get("policy")),
        "supported": bool(binding.get("selectable")),
        "model_role_id": str(model.get("role_id") or ""),
        "model_nodes": tuple(model.get("accepted_node_classes") or ()),
        "text_role_id": str(text.get("role_id") or ""),
        "text_nodes": tuple(text.get("accepted_node_classes") or ()),
        "preferred_text_node": str(text.get("preferred_node_class") or ""),
        "clip_type": str(text.get("clip_type") or ""),
        "vae_role_id": str(vae.get("role_id") or ""),
        "vae_nodes": tuple(vae.get("accepted_node_classes") or ()),
        "negative_node": str(negative.get("node_class") or "CLIPTextEncode"),
        "negative_strategy": str(negative.get("negative_conditioning_policy") or "clip_text_encode"),
        "lora_mode": str(lora.get("mode") or "model_and_clip"),
        "capability_contract": _mapping(adapter.get("capabilities")),
        "asset_slots": _mapping(_mapping(adapter.get("assets")).get("slots")),
    }


def _dynamic_node_groups(family_id: str, loader_id: str, requirements: Mapping[str, Any]) -> list[dict[str, Any]]:
    capability = _mapping(requirements.get("capability_contract"))
    groups = []
    for item in capability.get("node_groups") or []:
        group = _mapping(item)
        aliases = tuple(str(value) for value in group.get("aliases") or () if str(value))
        if aliases:
            groups.append({
                "role": str(group.get("role") or "node"),
                "aliases": aliases,
                "pack_id": str(group.get("pack_id") or "comfy-core"),
            })
    return groups


def _asset_role_config(family_id: str, loader_id: str, requirements: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    slots = _mapping(requirements.get("asset_slots"))
    if family_id in {"sdxl", "sd15"} and loader_id == "checkpoint":
        return {"model": {"loader_id": "checkpoint", "role_id": "checkpoint", "label": f"{family_id} checkpoint", "suffixes": (".safetensors", ".ckpt")}}
    if family_id == "flux":
        configs = {
            "model": {"tokens_any": ("flux1", "flux.1", "flux_1", "flux"), "tokens_none": ("flux2", "flux.2", "klein", "krea")},
            "text_encoder": {"tokens_any": ("t5xxl", "t5_xxl", "t5-xxl", "t5")},
            "text_encoder_2": {"tokens_any": ("clip_l", "clip-l", "clip l")},
            "vae": {"tokens_any": ("ae", "vae")},
        }
        if loader_id == "gguf":
            configs["model"] = {**configs["model"], "suffixes": (".gguf",)}
        return {
            slot_id: {"loader_id": loader_id, "role_id": str(_mapping(slot).get("role_id") or ""), "label": f"Flux.1 {slot_id.replace('_', ' ')}", **configs.get(slot_id, {})}
            for slot_id, slot in slots.items() if _mapping(slot).get("required", True)
        }
    if family_id in {"flux2_dev", "flux2_klein"}:
        is_dev = family_id == "flux2_dev"
        configs = {
            "model": ({"tokens_any": ("flux2", "flux.2", "flux_2"), "tokens_none": ("klein",)} if is_dev else {"tokens_all": ("flux", "2", "klein")}),
            "text_encoder": ({"tokens_any": ("mistral", "mistral3", "mistral_3"), "tokens_none": ("qwen",)} if is_dev else {"tokens_any": ("qwen3", "qwen_3", "qwen-3"), "tokens_none": ("mistral",)}),
            "vae": {"tokens_any": ("flux2", "flux_2", "vae", "ae")},
        }
        if loader_id == "gguf":
            configs["model"] = {**configs["model"], "suffixes": (".gguf",)}
            if not is_dev:
                configs["text_encoder"] = {**configs["text_encoder"], "suffixes": (".gguf", ".safetensors", ".pt", ".bin")}
        return {
            slot_id: {"loader_id": loader_id, "role_id": str(_mapping(slot).get("role_id") or ""), "label": f"{family_id} {slot_id.replace('_', ' ')}", **configs.get(slot_id, {})}
            for slot_id, slot in slots.items() if _mapping(slot).get("required", True)
        }
    if family_id == "hidream":
        configs = {
            "model": {
                "tokens_any": ("hidream", "hi-dream", "hi_dream"),
                "tokens_none": ("e1", "e1.1", "o1", "edit"),
                **({"suffixes": (".gguf",)} if loader_id == "gguf" else {}),
            },
            "text_encoder": {"tokens_any": ("clip_l_hidream", "clip-l-hidream", "clip_l", "clip-l")},
            "text_encoder_2": {"tokens_any": ("clip_g_hidream", "clip-g-hidream", "clip_g", "clip-g")},
            "text_encoder_3": {"tokens_any": ("t5xxl", "t5_xxl", "t5-xxl")},
            "text_encoder_4": {
                "tokens_any": ("llama", "llama3", "llama_3", "llama-3"),
                "tokens_all": ("8b",),
            },
            "vae": {"tokens_any": ("ae", "vae")},
        }
        return {
            slot_id: {
                "loader_id": loader_id,
                "role_id": str(_mapping(slot).get("role_id") or ""),
                "label": f"HiDream-I1 {slot_id.replace('_', ' ')}",
                **configs.get(slot_id, {}),
            }
            for slot_id, slot in slots.items() if _mapping(slot).get("required", True)
        }
    if family_id == "anima":
        configs = {
            "model": {"tokens_all": ("anima",), **({"suffixes": (".gguf",)} if loader_id == "gguf" else {})},
            "text_encoder": {"tokens_any": ("qwen_3_06b", "qwen3_06b", "qwen-3-0.6b")},
            "vae": {"tokens_all": ("qwen", "image"), "tokens_any": ("vae", "ae")},
        }
        return {slot_id: {"loader_id": loader_id, "role_id": str(_mapping(slot).get("role_id") or ""), "label": f"Anima {slot_id.replace('_', ' ')}", **configs.get(slot_id,{})} for slot_id,slot in slots.items() if _mapping(slot).get("required",True)}
    if family_id == "ideogram4":
        configs = {
            "model": {"tokens_any": ("ideogram4", "ideogram_4", "ideogram-4"), "tokens_none": ("unconditional",), **({"suffixes": (".gguf",)} if loader_id == "gguf" else {})},
            "text_encoder": {"tokens_any": ("qwen3_vl", "qwen3-vl", "qwen_3_vl")},
            "vae": {"tokens_any": ("flux2", "flux_2", "vae")},
        }
        return {slot_id: {"loader_id": loader_id, "role_id": str(_mapping(slot).get("role_id") or ""), "label": f"Ideogram 4 {slot_id.replace('_', ' ')}", **configs.get(slot_id,{})} for slot_id,slot in slots.items() if _mapping(slot).get("required",True)}
    if family_id == "sd35":
        configs = {
            "model": {"tokens_any": ("sd3", "sd_3", "stable-diffusion-3", "stable_diffusion_3"), **({"suffixes": (".gguf",)} if loader_id == "gguf" else {})},
            "text_encoder": {"tokens_any": ("clip_l", "clip-l", "clip l")},
            "text_encoder_2": {"tokens_any": ("clip_g", "clip-g", "clip g")},
            "text_encoder_3": {"tokens_any": ("t5", "t5xxl")},
            "vae": {"tokens_any": ("vae", "ae")},
        }
        return {
            slot_id: {"loader_id": loader_id, "role_id": str(_mapping(slot).get("role_id") or ""), "label": f"SD 3.5 {slot_id.replace('_', ' ')}", **configs.get(slot_id, {})}
            for slot_id, slot in slots.items() if _mapping(slot).get("required", True)
        }
    model_tokens = {
        "krea2_turbo": {"tokens_all": ("krea", "turbo")},
        "qwen_image": {"tokens_all": ("qwen", "image"), "tokens_none": ("edit",)},
        "qwen_image_edit_2509": {"tokens_all": ("qwen", "image", "edit", "2509"), "tokens_none": ("2511",)},
        "qwen_image_edit_2511": {"tokens_all": ("qwen", "image", "edit", "2511"), "tokens_none": ("2509",)},
        "z_image": {"tokens_all": ("z", "image"), "tokens_none": ("turbo",)},
        "z_image_turbo": {"tokens_all": ("z", "image", "turbo")},
    }.get(family_id, {})
    if loader_id == "gguf":
        model_tokens = {**model_tokens, "suffixes": (".gguf",)}
    if family_id == "krea2_turbo":
        text_tokens = {"tokens_any": ("qwen3vl", "qwen3-vl", "qwen3_vl"), "tokens_all": ("4b",)}
        vae_tokens = {"tokens_all": ("qwen", "image", "vae")}
    elif family_id == "qwen_image":
        text_tokens = {"tokens_all": ("qwen",), "tokens_any": ("text", "encoder", "clip", "qwen")}
        vae_tokens = {"tokens_all": ("qwen",), "tokens_any": ("vae", "ae")}
    elif family_id in {"qwen_image_edit_2509", "qwen_image_edit_2511"}:
        text_tokens = {"tokens_all": ("qwen",), "tokens_any": ("2.5", "2_5", "2-5", "vl", "vision")}
        vae_tokens = {"tokens_all": ("qwen",), "tokens_any": ("vae", "ae")}
    else:
        text_tokens = {"tokens_all": ("qwen", "3"), "tokens_any": ("4b", "encoder", "qwen")}
        vae_tokens = {"tokens_any": ("ae", "vae")}
    return {
        "model": {"loader_id": loader_id, "role_id": str(requirements.get("model_role_id") or ""), "label": f"{family_id} {loader_id} transformer", **model_tokens},
        "text_encoder": {"loader_id": loader_id, "role_id": str(requirements.get("text_role_id") or ""), "label": f"{family_id} text encoder", **text_tokens},
        "vae": {"loader_id": loader_id, "role_id": str(requirements.get("vae_role_id") or ""), "label": f"{family_id} VAE / AE", **vae_tokens},
    }


def _expansion_profile_from_registry(
    registry: Mapping[str, Any] | None,
    *,
    family_id: str,
    loader_id: str,
    provider_id: str,
) -> dict[str, Any] | None:
    if not isinstance(registry, Mapping):
        return get_lanpaint_family_expansion_profile(family_id, loader=loader_id, provider_id=provider_id)
    profiles = registry.get("profiles") if isinstance(registry.get("profiles"), list) else []
    for raw in profiles:
        profile = _mapping(raw)
        identity = _mapping(profile.get("identity"))
        if normalize_family_id(identity.get("family")) != family_id or normalize_loader_id(identity.get("loader")) != loader_id:
            continue
        providers = [normalize_provider_id(item) for item in identity.get("provider_ids", [])]
        if provider_id and provider_id not in providers:
            return None
        return deepcopy(profile)
    return None


def evaluate_lanpaint_route_capabilities(
    backend_capabilities: Mapping[str, Any] | None,
    *,
    provider_id: Any,
    family: Any,
    loader: Any,
    mode: Any = MODE_ID,
    engine: Any = ENGINE_ID,
    selected_assets: Mapping[str, Any] | None = None,
    require_invert_mask: bool = False,
    require_model_only_lora: bool = False,
    require_model_clip_lora: bool = False,
    physical_validation_passed: bool = False,
    family_adapter: Mapping[str, Any] | None = None,
    adapter_registry: Mapping[str, Any] | None = None,
    expansion_registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the fail-closed, family-policy-driven LanPaint readiness report."""

    backend = _mapping(backend_capabilities)
    provider = normalize_provider_id(provider_id)
    family_id = normalize_family_id(family)
    loader_id = normalize_loader_id(loader)
    mode_id = str(mode or MODE_ID).strip().lower().replace("-", "_")
    engine_id = str(engine or ENGINE_ID).strip().lower().replace("-", "_")
    requirements = _route_policy_requirements(
        provider, family_id, loader_id, family_adapter=family_adapter
    )
    supported_route = bool(
        provider in SUPPORTED_PROVIDERS
        and requirements.get("supported")
        and mode_id in SUPPORTED_MODES
        and engine_id == ENGINE_ID
    )
    route_key = f"{provider}:{family_id}:{loader_id}:{mode_id}:{engine_id}"
    blockers: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    selected = _mapping(selected_assets)
    expansion_profile = _expansion_profile_from_registry(
        expansion_registry, family_id=family_id, loader_id=loader_id, provider_id=provider
    )
    expansion_summary = {}
    if expansion_profile:
        expansion_summary = {
            "profile_id": expansion_profile["identity"]["profile_id"],
            "display_name": expansion_profile["identity"]["display_name"],
            "onboarding_state": expansion_profile["onboarding"]["state"],
            "policy_status": expansion_profile["family_policy"]["policy_status"],
            "required_work": list(expansion_profile["onboarding"]["required_work"]),
            "route_status": expansion_profile["execution"]["route_status"],
            "selectable": bool(expansion_profile["execution"].get("selectable")),
            "executable": bool(expansion_profile["execution"].get("executable")),
            "profile_fingerprint": expansion_profile["profile_fingerprint"],
        }

    requested_lora_mode = "model_only" if require_model_only_lora else ("model_and_clip" if require_model_clip_lora else str(requirements.get("lora_mode") or "model_only"))
    lora_requested = bool(require_model_only_lora or require_model_clip_lora)
    report: dict[str, Any] = {
        "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "authority": AUTHORITY,
        "phase_state": PHASE8_STATE,
        "route": {"provider_id": provider, "family": family_id, "loader": loader_id, "mode": mode_id, "engine": engine_id, "route_key": route_key, "supported": supported_route},
        "family_policy": {
            "policy_id": _mapping(requirements.get("policy")).get("policy_id"),
            "policy_fingerprint": _mapping(requirements.get("policy")).get("policy_fingerprint"),
            "clip_type": requirements.get("clip_type"),
            "family_variant": deepcopy(_mapping(_mapping(requirements.get("adapter")).get("family_variant"))),
            "stability_policy": deepcopy(_mapping(_mapping(requirements.get("adapter")).get("stability_policy"))),
        },
        "family_adapter": {
            "schema_id": _mapping(requirements.get("adapter")).get("schema_id"),
            "adapter_id": _mapping(_mapping(requirements.get("adapter")).get("identity")).get("adapter_id"),
            "adapter_fingerprint": _mapping(requirements.get("adapter")).get("adapter_fingerprint"),
            "binding_state": _mapping(_mapping(requirements.get("adapter")).get("binding")).get("state"),
            "graph_profile": _mapping(_mapping(requirements.get("adapter")).get("binding")).get("graph_profile"),
            "stabilization_state": _mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("state"),
            "loader_parity_group": _mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("loader_parity_group"),
            "new_binding_activated": bool(_mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("new_binding_activated")),
            "completed_phase20": bool(_mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("completed_phase20")),
            "onboarded_phase21": bool(_mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("onboarded_phase21")),
            "onboarded_phase22": bool(_mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("onboarded_phase22")),
            "phase21_state": _mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("phase_state"),
            "physical_validation": _mapping(_mapping(requirements.get("adapter")).get("stabilization")).get("physical_validation"),
        },
        "expansion_scaffold": expansion_summary,
        "discovery": {
            "checked": bool(backend), "reachable": bool(backend.get("reachable")),
            "object_info_available": bool(backend.get("object_info_available")),
            "discovery_status": str(backend.get("discovery_status") or "not_checked"),
            "provider_errors": [str(item) for item in backend.get("errors", [])] if isinstance(backend.get("errors"), list) else [],
            "provider_warnings": [str(item) for item in backend.get("warnings", [])] if isinstance(backend.get("warnings"), list) else [],
        },
        "checks": {}, "blockers": blockers, "warnings": warnings, "remediation": [],
        "status": UNSUPPORTED_STATUS, "selectable": False, "executable": False,
        "lora": {"mode": requested_lora_mode, "requested": lora_requested, "supported": False, "status": "not_checked"},
    }

    if not supported_route:
        if expansion_summary:
            blockers.append(_issue("scaffolded_route_not_activated", f"{expansion_summary['display_name']} remains scaffolded and has no active compiler/capability binding."))
            report["remediation"] = [*expansion_summary["required_work"], "Use Native Inpaint or an onboarded LanPaint route."]
        else:
            blockers.append(_issue("unsupported_route", "This provider/family/loader combination has no LanPaint compiler binding."))
            report["remediation"] = ["Select an onboarded LanPaint family/loader route, or use Native Inpaint."]
        report["capability_fingerprint"] = _fingerprint(report)
        return report

    if not backend or not backend.get("reachable") or not backend.get("object_info_available"):
        blockers.append(_issue("backend_capability_snapshot_unavailable", "The connected ComfyUI profile has no live /object_info capability snapshot.", field="backend_capabilities"))

    current_registry = dict(adapter_registry) if isinstance(adapter_registry, Mapping) else lanpaint_family_adapter_registry(provider)
    adapter_route_key = f"{family_id}:{loader_id}:{mode_id}:{engine_id}"
    snapshot_freshness = lanpaint_snapshot_freshness(
        backend,
        expected_registry_fingerprint=str(current_registry.get("registry_fingerprint") or ""),
        expected_route_key=adapter_route_key,
    )
    report["discovery"]["snapshot_freshness"] = snapshot_freshness
    if snapshot_freshness.get("stale"):
        reasons = ", ".join(str(item) for item in snapshot_freshness.get("reasons") or ())
        blockers.append(_issue(
            "stale_capability_snapshot",
            f"The selected ComfyUI capability snapshot predates the current LanPaint adapter registry ({reasons}). Reconnect/Test the profile to rebuild object_info capabilities.",
            field="backend_capabilities",
        ))

    node_map = _node_map(backend)
    groups = _dynamic_node_groups(family_id, loader_id, requirements)
    if require_invert_mask:
        groups.append(CONDITIONAL_NODE_GROUPS["invert_mask"])
    if lora_requested:
        groups.append(
            {"role": requested_lora_mode, "aliases": ("LoraLoaderModelOnly",), "pack_id": "comfy-core"}
            if requested_lora_mode == "model_only"
            else {"role": requested_lora_mode, "aliases": ("LoraLoader",), "pack_id": "comfy-core"}
        )

    node_checks: list[dict[str, Any]] = []
    missing_by_pack: dict[str, list[str]] = {}
    signature_mismatches: list[dict[str, Any]] = []
    for group in groups:
        aliases = tuple(str(item) for item in group["aliases"] if str(item))
        selected_node = next((name for name in aliases if name in node_map), "")
        available = bool(selected_node)
        node_check = {"role": str(group["role"]), "aliases": list(aliases), "selected_node": selected_node, "available": available, "pack_id": str(group["pack_id"])}
        if not available:
            missing_by_pack.setdefault(str(group["pack_id"]), []).append(" or ".join(aliases))
        else:
            required_inputs = set(REQUIRED_NODE_INPUTS.get(selected_node, ()))
            declared_inputs = _input_names(_mapping(node_map.get(selected_node)))
            missing_inputs = sorted(required_inputs - declared_inputs)
            node_check.update({"required_inputs": sorted(required_inputs), "declared_inputs": sorted(declared_inputs), "missing_inputs": missing_inputs})
            if missing_inputs:
                signature_mismatches.append({"node_class": selected_node, "missing_inputs": missing_inputs, "pack_id": str(group["pack_id"])})
        node_checks.append(node_check)

    for pack_id, missing in sorted(missing_by_pack.items()):
        blockers.append(_issue("missing_required_nodes", f"{pack_id} is missing required LanPaint node classes: {', '.join(missing)}.", field="nodes", pack_id=pack_id, node_classes=missing))
    for mismatch in signature_mismatches:
        blockers.append(_issue("incompatible_node_signature", f"{mismatch['node_class']} is installed but does not expose required inputs: {', '.join(mismatch['missing_inputs'])}.", field="node_signatures", pack_id=mismatch["pack_id"], node_classes=(mismatch["node_class"],)))

    loader_checks: dict[str, Any] = {}
    role_items = []
    asset_slots = _mapping(requirements.get("asset_slots"))
    if asset_slots:
        role_items = [(slot_id, str(_mapping(slot).get("role_id") or "")) for slot_id, slot in asset_slots.items() if _mapping(slot).get("required", True)]
    else:
        role_items = [(key, str(requirements.get(key) or "")) for key in ("model_role_id", "text_role_id", "vae_role_id")]
    for key, role_id in role_items:
        role = _loader_role(backend, loader_id, role_id)
        loader_checks[role_id or key] = {"available": bool(role.get("available")), "backend_node": str(role.get("backend_node") or "")}
        if not role.get("available"):
            blockers.append(_issue(f"{key}_unavailable", f"The connected backend did not prove loader role {loader_id}.{role_id}.", field="loader"))

    model_checks: dict[str, Any] = {}
    for key, config in _asset_role_config(family_id, loader_id, requirements).items():
        catalog = _role_assets(backend, str(config["loader_id"]), str(config["role_id"]))
        candidates = [item for item in catalog if _asset_matches(item, config)]
        if config.get("tokens_none"):
            candidates = [item for item in candidates if not any(token in _normal_name(item) for token in config["tokens_none"])]
        selected_name = str(selected.get(key) or "").strip()
        selected_found = _selected_in_catalog(selected_name, catalog) if selected_name else None
        compatible_selected = _asset_matches(selected_name, config) if selected_name else None
        if selected_name and config.get("tokens_none") and any(token in _normal_name(selected_name) for token in config["tokens_none"]):
            compatible_selected = False
        model_checks[key] = {"label": str(config["label"]), "catalog": catalog, "candidates": candidates, "selected": selected_name, "selected_found": selected_found, "available": bool(candidates)}
        if not catalog:
            blockers.append(_issue(f"missing_{key}_catalog", f"ComfyUI did not advertise any assets for {config['label']}.", field=key))
        elif not candidates:
            blockers.append(_issue(f"missing_compatible_{key}", f"No compatible {config['label']} candidate was found in the connected ComfyUI catalog.", field=key, assets=catalog))
        if selected_name and selected_found is False:
            blockers.append(_issue(f"selected_{key}_not_found", f"The selected {config['label']} is not present in the connected ComfyUI catalog.", field=key, assets=(selected_name,)))
        elif selected_name and compatible_selected is False:
            blockers.append(_issue(f"selected_{key}_incompatible", f"The selected asset does not match the required {config['label']} family.", field=key, assets=(selected_name,)))

    if requested_lora_mode == "model_only":
        lora_node_class = "LoraLoaderModelOnly"
    else:
        lora_node_class = "LoraLoader"
    lora_available = lora_node_class in node_map
    lora_signature_ok = bool(lora_available and not (set(REQUIRED_NODE_INPUTS[lora_node_class]) - _input_names(_mapping(node_map.get(lora_node_class)))))
    report["lora"].update({"supported": bool(lora_available and lora_signature_ok), "status": READY_STATUS if lora_available and lora_signature_ok else BLOCKED_NODE_STATUS})
    if not lora_requested and not lora_available:
        warning_code = "model_only_lora_unavailable" if requested_lora_mode == "model_only" else "model_clip_lora_unavailable"
        warnings.append(_issue(warning_code, f"Base LanPaint can run, but {requested_lora_mode} LoRA execution is unavailable until {lora_node_class} is present.", field="lora", node_classes=(lora_node_class,)))

    report["checks"] = {
        "nodes": {"items": node_checks, "missing_by_pack": [{"pack_id": pack, "missing_node_classes": values} for pack, values in sorted(missing_by_pack.items())], "signature_mismatches": signature_mismatches, "ok": not missing_by_pack and not signature_mismatches},
        "loaders": {"items": loader_checks, "ok": all(item["available"] for item in loader_checks.values())},
        "models": {"items": model_checks, "ok": all(item["available"] and item["selected_found"] is not False for item in model_checks.values())},
    }
    stale_codes = {"stale_capability_snapshot"}
    node_codes = {"backend_capability_snapshot_unavailable", "missing_required_nodes", "incompatible_node_signature", "model_role_id_unavailable", "text_role_id_unavailable", "vae_role_id_unavailable"}
    # Exact asset-slot loader roles (for example HiDream's fourth encoder) are
    # model/catalog failures, not missing Comfy node classes. Keep only the
    # generic model/text/VAE loader-role contracts in the node bucket.
    stale_blockers = [item for item in blockers if item["code"] in stale_codes]
    node_blockers = [item for item in blockers if item["code"] in node_codes]
    model_blockers = [item for item in blockers if item not in node_blockers and item not in stale_blockers]
    if stale_blockers:
        status = BLOCKED_STALE_STATUS
    elif node_blockers:
        status = BLOCKED_NODE_STATUS
    elif model_blockers:
        status = BLOCKED_MODEL_STATUS
    else:
        status = "available" if physical_validation_passed else READY_STATUS

    remediation: list[str] = []
    if any(item["code"] == "backend_capability_snapshot_unavailable" for item in blockers):
        remediation.append("Connect/Test the selected ComfyUI backend profile to refresh /object_info.")
    if any(item["code"] == "stale_capability_snapshot" for item in blockers):
        remediation.append("Reconnect/Test the selected ComfyUI profile after this Neo update so the LanPaint route matrix is rebuilt from the current adapter registry.")
    for pack in report["checks"]["nodes"]["missing_by_pack"]:
        remediation.append(("Update ComfyUI core" if pack["pack_id"] == "comfy-core" else f"Install or update {pack['pack_id']}") + ", restart ComfyUI, then refresh backend capabilities.")
    if model_blockers:
        remediation.append(f"Install/select compatible {family_id} model, text encoder and VAE/AE assets advertised by the connected backend.")
    if report["lora"]["requested"] and not report["lora"]["supported"]:
        remediation.append(f"Update ComfyUI so {lora_node_class} is available, or disable active base/global LoRA rows for this run.")

    report["status"] = status
    report["selectable"] = status in {"available", READY_STATUS}
    report["executable"] = report["selectable"]
    report["remediation"] = list(dict.fromkeys(remediation))
    report["capability_fingerprint"] = _fingerprint(report)
    return report


__all__ = [
    "AUTHORITY",
    "BLOCKED_MODEL_STATUS",
    "BLOCKED_NODE_STATUS",
    "BLOCKED_STALE_STATUS",
    "CONDITIONAL_NODE_GROUPS",
    "PHASE8_STATE",
    "READY_STATUS",
    "REQUIRED_NODE_GROUPS",
    "REQUIRED_NODE_INPUTS",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SUPPORTED_PROVIDERS",
    "UNSUPPORTED_STATUS",
    "evaluate_lanpaint_route_capabilities",
]
