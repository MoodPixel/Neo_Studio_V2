from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from neo_app.image.lanpaint_family_policies import (
    COMPLETE_POLICY_STATE,
    PLACEHOLDER_POLICY_STATE,
    get_lanpaint_family_policy,
    resolve_lanpaint_family_policy,
)
from neo_app.image.lanpaint_route_contract import (
    ENGINE_ID,
    MODE_ID,
    ROUTE_FAMILY_ID,
    normalize_family_id,
    normalize_lanpaint_route_contract,
    normalize_loader_id,
    normalize_provider_id,
)

SCHEMA_ID = "neo.image.lanpaint_family_adapter.v2"
SCHEMA_VERSION = 2
REGISTRY_SCHEMA_ID = "neo.image.lanpaint_family_adapter_registry.v2"
AUTHORITY = "neo_app.image.lanpaint_family_adapter"
PHASE13_STATE = "universal_family_adapter_v2"
PHASE14_STATE = "existing_route_parity_stabilization"
PHASE15_STATE = "sd_family_onboarding"
PHASE16_STATE = "flux1_family_onboarding"
PHASE17_STATE = "flux2_dev_klein_onboarding"
PHASE18_STATE = "qwen_image_edit_variant_onboarding"
PHASE20_STATE = "z_image_lanpaint_inpainting_onboarding"
PHASE21_STATE = "hidream_i1_lanpaint_onboarding_hunyuan_video_hold"
PHASE22_STATE = "anima_ideogram4_family_lanpaint_onboarding"
WORKFLOW_TYPE = "image.inpaint.lanpaint"
COMPILER_ID = "comfy.lanpaint.family_aware.v1"
SUPPORTED_PROVIDERS = ("comfyui", "comfyui_portable")

# Phase 13 centralizes current bindings but deliberately activates no new route.
# Later phases may add entries only after family-specific graph and physical tests.
_ACTIVE_BINDINGS: dict[tuple[str, str], dict[str, str]] = {
    ("krea2_turbo", "diffusion_model"): {
        "graph_profile": "krea2_differential_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 14 — Existing route parity and stabilization",
    },
    ("krea2_turbo", "gguf"): {
        "graph_profile": "krea2_differential_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 5 — Krea 2 Turbo GGUF",
    },
    ("qwen_image", "diffusion_model"): {
        "graph_profile": "aura_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 10 — Qwen and Z-Image onboarding",
    },
    ("qwen_image", "gguf"): {
        "graph_profile": "aura_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 10 — Qwen and Z-Image onboarding",
    },
    ("qwen_image_edit_2509", "diffusion_model"): {
        "graph_profile": "qwen_edit_crop_stitch_aura_v1",
        "phase": "LanPaint Route Family Phase 18 — Qwen Image Edit variants",
    },
    ("qwen_image_edit_2509", "gguf"): {
        "graph_profile": "qwen_edit_crop_stitch_aura_v1",
        "phase": "LanPaint Route Family Phase 18 — Qwen Image Edit variants",
    },
    ("qwen_image_edit_2511", "diffusion_model"): {
        "graph_profile": "qwen_edit_crop_stitch_aura_v1",
        "phase": "LanPaint Route Family Phase 18 — Qwen Image Edit variants",
    },
    ("qwen_image_edit_2511", "gguf"): {
        "graph_profile": "qwen_edit_crop_stitch_aura_v1",
        "phase": "LanPaint Route Family Phase 18 — Qwen Image Edit variants",
    },
    ("z_image", "diffusion_model"): {
        "graph_profile": "z_image_lanpaint_base_crop_stitch_v2",
        "phase": "LanPaint Route Family Phase 20 — Z-Image LanPaint inpainting onboarding",
    },
    ("z_image", "gguf"): {
        "graph_profile": "z_image_lanpaint_base_crop_stitch_v2",
        "phase": "LanPaint Route Family Phase 20 — Z-Image LanPaint inpainting onboarding",
    },
    ("z_image_turbo", "diffusion_model"): {
        "graph_profile": "z_image_turbo_lanpaint_crop_stitch_v2",
        "phase": "LanPaint Route Family Phase 20 — Z-Image LanPaint inpainting onboarding",
    },
    ("z_image_turbo", "gguf"): {
        "graph_profile": "z_image_turbo_lanpaint_crop_stitch_v2",
        "phase": "LanPaint Route Family Phase 20 — Z-Image LanPaint inpainting onboarding",
    },
    ("hidream", "diffusion_model"): {
        "graph_profile": "hidream_i1_quad_clip_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 21 — HiDream-I1 onboarding and Hunyuan video hold",
    },
    ("hidream", "gguf"): {
        "graph_profile": "hidream_i1_quad_clip_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 21 — HiDream-I1 onboarding and Hunyuan video hold",
    },
    ("anima", "diffusion_model"): {
        "graph_profile": "anima_base_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 22 — Anima and Ideogram 4 onboarding",
    },
    ("anima", "gguf"): {
        "graph_profile": "anima_base_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 22 — Anima and Ideogram 4 onboarding",
    },
    ("ideogram4", "diffusion_model"): {
        "graph_profile": "ideogram4_dual_model_custom_advanced_v1",
        "phase": "LanPaint Route Family Phase 22 — Anima and Ideogram 4 onboarding",
    },
    ("ideogram4", "gguf"): {
        "graph_profile": "ideogram4_dual_model_custom_advanced_v1",
        "phase": "LanPaint Route Family Phase 22 — Anima and Ideogram 4 onboarding",
    },
    ("flux", "diffusion_model"): {
        "graph_profile": "flux1_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 16 — Flux.1 family onboarding",
    },
    ("flux", "gguf"): {
        "graph_profile": "flux1_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 16 — Flux.1 family onboarding",
    },
    ("flux2_dev", "diffusion_model"): {
        "graph_profile": "flux2_dev_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 17 — Flux.2 Dev and Klein onboarding",
    },
    ("flux2_dev", "gguf"): {
        "graph_profile": "flux2_dev_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 17 — Flux.2 Dev and Klein onboarding",
    },
    ("flux2_klein", "diffusion_model"): {
        "graph_profile": "flux2_klein_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 17 — Flux.2 Dev and Klein onboarding",
    },
    ("flux2_klein", "gguf"): {
        "graph_profile": "flux2_klein_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 17 — Flux.2 Dev and Klein onboarding",
    },
    ("sdxl", "checkpoint"): {
        "graph_profile": "sd_checkpoint_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 15 — SD family onboarding",
    },
    ("sd15", "checkpoint"): {
        "graph_profile": "sd_checkpoint_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 15 — SD family onboarding",
    },
    ("sd35", "diffusion_model"): {
        "graph_profile": "sd3_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 15 — SD family onboarding",
    },
    ("sd35", "gguf"): {
        "graph_profile": "sd3_crop_stitch_v1",
        "phase": "LanPaint Route Family Phase 15 — SD family onboarding",
    },
}

_PHASE15_NEW_BINDINGS = frozenset({("sdxl", "checkpoint"), ("sd15", "checkpoint"), ("sd35", "diffusion_model"), ("sd35", "gguf")})
_PHASE16_NEW_BINDINGS = frozenset({("flux", "diffusion_model"), ("flux", "gguf")})
_PHASE17_NEW_BINDINGS = frozenset({("flux2_dev", "diffusion_model"), ("flux2_dev", "gguf"), ("flux2_klein", "diffusion_model"), ("flux2_klein", "gguf")})
_PHASE18_NEW_BINDINGS = frozenset({("qwen_image_edit_2509", "diffusion_model"), ("qwen_image_edit_2509", "gguf"), ("qwen_image_edit_2511", "diffusion_model"), ("qwen_image_edit_2511", "gguf")})
_PHASE20_COMPLETED_BINDINGS = frozenset({("z_image", "diffusion_model"), ("z_image", "gguf"), ("z_image_turbo", "diffusion_model"), ("z_image_turbo", "gguf")})
_PHASE21_NEW_BINDINGS = frozenset({("hidream", "diffusion_model"), ("hidream", "gguf")})
_PHASE22_NEW_BINDINGS = frozenset({("anima", "diffusion_model"), ("anima", "gguf"), ("ideogram4", "diffusion_model"), ("ideogram4", "gguf")})
_PHASE14_STABILIZED_BINDINGS = frozenset(set(_ACTIVE_BINDINGS) - set(_PHASE15_NEW_BINDINGS) - set(_PHASE16_NEW_BINDINGS) - set(_PHASE17_NEW_BINDINGS) - set(_PHASE18_NEW_BINDINGS) - set(_PHASE20_COMPLETED_BINDINGS) - set(_PHASE21_NEW_BINDINGS) - set(_PHASE22_NEW_BINDINGS))
_PHASE14_NEW_BINDINGS = frozenset({("krea2_turbo", "diffusion_model")})

_ASSET_ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "hidream": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("hidream_clip_l", "clip_l_hidream", "text_encoder_1", "clip_name1"),
        "vae": ("vae", "ae", "vae_or_ae"),
    },
    "anima": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("anima_text_encoder", "qwen3_06b_text_encoder", "text_encoder_1", "clip_name"),
        "vae": ("anima_vae", "qwen_image_vae", "vae", "vae_or_ae"),
    },
    "ideogram4": {
        "model": ("gguf_model", "gguf_unet", "ideogram4_main_model", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("ideogram4_text_encoder", "qwen3_vl_text_encoder", "text_encoder_1", "clip_name"),
        "vae": ("ideogram4_vae", "flux2_vae", "vae", "vae_or_ae"),
    },
    "flux2_dev": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("mistral3_text_encoder", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("flux2_vae", "vae", "vae_or_ae", "ae"),
    },
    "flux2_klein": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("qwen3_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("flux2_vae", "vae", "vae_or_ae", "ae"),
    },
    "flux": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("text_encoder_1", "text_encoder_primary", "clip_name1", "t5xxl"),
        "vae": ("vae", "vae_or_ae", "ae"),
    },
    "sdxl": {
        "model": ("checkpoint", "checkpoint_name", "model", "model_name"),
        "text_encoder": ("checkpoint",),
        "vae": ("checkpoint",),
    },
    "sd15": {
        "model": ("checkpoint", "checkpoint_name", "model", "model_name"),
        "text_encoder": ("checkpoint",),
        "vae": ("checkpoint",),
    },
    "sd35": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("text_encoder_1", "clip_l", "clip_name1"),
        "vae": ("vae", "vae_or_ae", "ae"),
    },
    "krea2_turbo": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("qwen3vl_text_encoder", "qwen3vl_4b_text_encoder", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("qwen_vae", "vae", "vae_or_ae", "ae"),
    },
    "qwen_image": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "qwen_model", "model", "unet", "model_name"),
        "text_encoder": ("qwen_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("qwen_vae", "vae", "vae_or_ae", "ae"),
    },
    "qwen_image_edit_2509": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "qwen_model", "model", "unet", "model_name"),
        "text_encoder": ("qwen_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("qwen_vae", "vae", "vae_or_ae", "ae"),
    },
    "qwen_image_edit_2511": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "qwen_model", "model", "unet", "model_name"),
        "text_encoder": ("qwen_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("qwen_vae", "vae", "vae_or_ae", "ae"),
    },
    "z_image": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("qwen3_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("vae", "ae", "vae_or_ae"),
    },
    "z_image_turbo": {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("qwen3_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("vae", "ae", "vae_or_ae"),
    },
}

_PACK_BY_NODE = {
    "LanPaint_KSampler": "LanPaint",
    "LanPaint_KSamplerAdvanced": "LanPaint",
    "LanPaint_SamplerCustom": "LanPaint",
    "LanPaint_SamplerCustomAdvanced": "LanPaint",
    "CropByMask": "ComfyUI-InpaintEasy",
    "ImageResizeKJv2": "ComfyUI-KJNodes",
    "GrowMaskWithBlur": "ComfyUI-KJNodes",
    "DifferentialDiffusionAdvanced": "ComfyUI-KJNodes",
    "UnetLoaderGGUF": "ComfyUI-GGUF",
    "LoaderGGUF": "ComfyUI-GGUF",
    "CLIPLoaderGGUF": "ComfyUI-GGUF",
    "ClipLoaderGGUF": "ComfyUI-GGUF",
    "TripleCLIPLoaderGGUF": "ComfyUI-GGUF",
    "QuadrupleCLIPLoaderGGUF": "ComfyUI-GGUF",
    "DualCLIPLoaderGGUF": "ComfyUI-GGUF",
    "ModelSamplingFlux": "comfy-core",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple, set)) else []


def _fingerprint_payload(adapter: Mapping[str, Any]) -> dict[str, Any]:
    """Return the immutable adapter architecture used for drift detection.

    Request-resolved crop/mask/stitch values and sampler defaults are execution
    controls with their own UI/replay fingerprints. They must not turn one
    family/loader adapter into a different adapter for every generation.
    The family policy fingerprint remains in this payload, so policy-default or
    architecture changes still invalidate old adapter fingerprints.
    """

    payload = deepcopy(dict(adapter))
    payload.pop("adapter_fingerprint", None)
    payload.pop("validation", None)
    payload.pop("diagnostics", None)
    payload.pop("spatial", None)
    payload.pop("stabilization", None)
    binding = _mapping(payload.get("binding"))
    if binding:
        # Phase rollout provenance is audit metadata, not family architecture.
        # Excluding it preserves existing replay fingerprints while still letting
        # a real binding/loader/graph change invalidate stale lineage.
        binding.pop("new_route_activated_by_phase14", None)
        binding.pop("new_route_activated_by_phase15", None)
        binding.pop("new_route_activated_by_phase16", None)
        binding.pop("new_route_activated_by_phase17", None)
        binding.pop("new_route_activated_by_phase18", None)
        binding.pop("new_route_activated_by_phase21", None)
        binding.pop("new_route_activated_by_phase22", None)
        payload["binding"] = binding
    sampler = _mapping(payload.get("sampler"))
    if sampler:
        sampler.pop("defaults", None)
        payload["sampler"] = sampler
    # Phase 15 adds optional bundled/multi-encoder metadata.  Omit default/empty
    # values so every pre-Phase-15 adapter keeps its exact replay fingerprint.
    loaders = _mapping(payload.get("loaders"))
    for loader_name in ("text_encoder", "vae"):
        entry = _mapping(loaders.get(loader_name))
        if entry:
            if not entry.get("asset_slots"):
                entry.pop("asset_slots", None)
            if not entry.get("bundled_with_model"):
                entry.pop("bundled_with_model", None)
            if int(entry.get("output_port") or 0) == 0:
                entry.pop("output_port", None)
            loaders[loader_name] = entry
    if loaders:
        payload["loaders"] = loaders
    assets = _mapping(_mapping(payload.get("assets")).get("slots"))
    for slot_id, raw in list(assets.items()):
        entry = _mapping(raw)
        if not entry.get("bundled_with_model"):
            entry.pop("bundled_with_model", None)
        assets[slot_id] = entry
    if assets:
        payload["assets"]["slots"] = assets
    return payload


def lanpaint_family_adapter_fingerprint(adapter: Mapping[str, Any]) -> str:
    canonical = json.dumps(_fingerprint_payload(adapter), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _model_role_id(loader_policy: Mapping[str, Any], loader_id: str) -> str:
    role = str(loader_policy.get("model_loader_role") or ("gguf_unet" if loader_id == "gguf" else "diffusion_model"))
    return "gguf_unet" if role == "gguf_diffusion_model" else role


def _asset_slots(policy: Mapping[str, Any], family_id: str, loader_id: str) -> dict[str, Any]:
    loader_policy = _mapping(_mapping(policy.get("loader_policies")).get(loader_id))
    text_policy = _mapping(policy.get("text_encoder_policy"))
    vae_policy = _mapping(policy.get("vae_policy"))
    text_roles = _mapping(text_policy.get("loader_role_ids"))
    vae_roles = _mapping(vae_policy.get("loader_role_ids"))
    aliases = _ASSET_ALIASES.get(family_id, {
        "model": ("gguf_model", "gguf_unet", "diffusion_model", "model", "unet", "model_name"),
        "text_encoder": ("text_encoder_1", "text_encoder_primary", "clip_name"),
        "vae": ("vae", "vae_or_ae", "ae"),
    })
    bundled_text = bool(text_policy.get("bundled_with_model"))
    bundled_vae = bool(vae_policy.get("bundled_with_model"))
    text_role = str(text_roles.get(loader_id) or ("krea2_clip_loader" if text_policy.get("required_clip_type") == "krea2" else ("gguf_text_encoder_primary" if loader_id == "gguf" else "text_encoder_primary")))
    slots: dict[str, Any] = {
        "model": {"role_id": _model_role_id(loader_policy, loader_id), "param_aliases": list(aliases["model"]), "required": True, "portable_identity_only": True},
        "text_encoder": {"role_id": text_role, "param_aliases": list(aliases["text_encoder"]), "required": not bundled_text, "bundled_with_model": bundled_text, "portable_identity_only": True},
        "vae": {"role_id": str(vae_roles.get(loader_id) or vae_policy.get("vae_role") or "vae_or_ae"), "param_aliases": list(aliases["vae"]), "required": not bundled_vae, "bundled_with_model": bundled_vae, "portable_identity_only": True},
    }
    extra_text_slots = _list(text_policy.get("asset_slots"))
    if extra_text_slots:
        first = _mapping(extra_text_slots[0])
        slots["text_encoder"].update({
            "role_id": str(first.get("role_id") or slots["text_encoder"]["role_id"]),
            "param_aliases": [str(item) for item in _list(first.get("param_aliases"))] or slots["text_encoder"]["param_aliases"],
        })
        for item in extra_text_slots[1:]:
            entry = _mapping(item)
            slot_id = str(entry.get("slot_id") or "").strip()
            required_when = str(entry.get("required_when") or "").strip()
            if required_when == "loader_is_gguf" and loader_id != "gguf":
                continue
            if slot_id:
                slots[slot_id] = {"role_id": str(entry.get("role_id") or ""), "param_aliases": [str(value) for value in _list(entry.get("param_aliases"))], "required": True, "portable_identity_only": True}
    return slots


def _node_group(role: str, aliases: list[str], *, required: bool = True) -> dict[str, Any]:
    clean = [str(item) for item in aliases if str(item)]
    pack = next((_PACK_BY_NODE.get(item) for item in clean if _PACK_BY_NODE.get(item)), "comfy-core")
    return {
        "role": role,
        "aliases": clean,
        "required": required,
        "pack_id": pack,
    }


def _capability_contract(policy: Mapping[str, Any], loader_id: str) -> dict[str, Any]:
    loader_policy = _mapping(_mapping(policy.get("loader_policies")).get(loader_id))
    text_policy = _mapping(policy.get("text_encoder_policy"))
    vae_policy = _mapping(policy.get("vae_policy"))
    conditioning = _mapping(policy.get("conditioning_policy"))
    positive = _mapping(conditioning.get("positive"))
    negative = _mapping(conditioning.get("negative"))
    transforms = _list(_mapping(policy.get("model_transform_pipeline")).get("ordered_transforms"))
    text_nodes = _mapping(text_policy.get("loader_node_classes"))
    model_nodes = [str(item) for item in _list(loader_policy.get("accepted_node_classes"))]
    clip_nodes = [str(item) for item in _list(text_nodes.get(loader_id))] or [str(text_policy.get("node_class") or "CLIPLoader")]
    vae_nodes = [str(vae_policy.get("node_class") or "VAELoader")]

    groups = [
        _node_group("source_loader", ["LoadImage"]),
        _node_group("mask_converter", ["ImageToMask"]),
        _node_group("model_loader", model_nodes),
        _node_group("clip_loader", clip_nodes),
        _node_group("positive_conditioning", [str(item) for item in _list(positive.get("accepted_node_classes"))] or [str(positive.get("node_class") or "CLIPTextEncode")]),
        _node_group("negative_conditioning", [str(negative.get("node_class") or "CLIPTextEncode")]),
        _node_group("vae_loader", vae_nodes),
        _node_group("crop_by_mask", ["CropByMask"]),
        _node_group("processing_resize", ["ImageResizeKJv2"]),
        _node_group("mask_refinement", ["GrowMaskWithBlur"]),
        _node_group("latent_encode", [str(vae_policy.get("encode_node_class") or "VAEEncode")]),
        _node_group("latent_noise_mask", ["SetLatentNoiseMask"]),
        _node_group("lanpaint_sampler", [str(_mapping(policy.get("sampler_adapter")).get("node_class") or "LanPaint_KSampler")]),
        _node_group("latent_decode", [str(vae_policy.get("decode_node_class") or "VAEDecode")]),
        _node_group("source_composite", ["ImageCompositeMasked"]),
        _node_group("output_handoff", ["PreviewImage"]),
    ]
    guidance_node = str(positive.get("guidance_node_class") or "")
    if guidance_node:
        groups.append(_node_group("family_guidance", [guidance_node]))
    for transform in transforms:
        item = _mapping(transform)
        loader_scope = [str(value) for value in _list(item.get("loader_scope"))]
        policy_family = str(_mapping(policy.get("identity")).get("family") or "")
        if policy_family == "flux" and loader_scope and loader_id not in loader_scope:
            continue
        transform_id = str(item.get("transform_id") or "")
        if transform_id == "differential_diffusion_advanced":
            groups.append(_node_group("differential_diffusion", ["DifferentialDiffusionAdvanced"]));
        elif transform_id == "model_sampling_aura_flow":
            groups.append(_node_group("family_sampling_transform", ["ModelSamplingAuraFlow"]));
        elif transform_id == "model_sampling_sd3":
            groups.append(_node_group("family_sampling_transform", ["ModelSamplingSD3"]));
        elif transform_id == "model_sampling_flux":
            groups.append(_node_group("family_sampling_transform", ["ModelSamplingFlux"]));

    lora = _mapping(policy.get("lora_policy"))
    conditional = {
        "invert_mask": _node_group("invert_mask", ["InvertMask"], required=False),
        "lora_stack_enabled": _node_group("lora_stack", [str(lora.get("loader_node_class") or "")], required=False),
    }
    return {
        "node_groups": groups,
        "conditional_node_groups": conditional,
        "required_model_roles": [slot.get("role_id") for slot in _asset_slots(policy, str(_mapping(policy.get("identity")).get("family") or ""), loader_id).values()],
        "family_policy_resolution_required": True,
        "live_object_info_required": True,
        "fail_closed": True,
    }


def _adapter_validation(adapter: Mapping[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    identity = _mapping(adapter.get("identity"))
    binding = _mapping(adapter.get("binding"))
    if adapter.get("schema_id") != SCHEMA_ID:
        issues.append({"level": "error", "field": "schema_id", "message": f"Expected {SCHEMA_ID}."})
    for key in ("provider_id", "family", "loader", "mode", "engine", "route_key"):
        if not identity.get(key):
            issues.append({"level": "error", "field": f"identity.{key}", "message": "Required adapter identity field is missing."})
    if binding.get("selectable") and binding.get("state") != "compiler_bound":
        issues.append({"level": "error", "field": "binding", "message": "Selectable adapters must have an exact compiler binding."})
    if binding.get("selectable") and not binding.get("compiler_id"):
        issues.append({"level": "error", "field": "binding.compiler_id", "message": "Selectable adapters require a compiler ID."})
    if _mapping(adapter.get("lora")).get("compatibility_engine_independent") is not True:
        issues.append({"level": "error", "field": "lora.compatibility_engine_independent", "message": "LoRA compatibility must remain engine-independent."})
    return issues


def resolve_lanpaint_family_adapter(route_contract: Mapping[str, Any] | None) -> dict[str, Any]:
    contract, contract_issues = normalize_lanpaint_route_contract(route_contract)
    identity = _mapping(contract.get("identity"))
    provider_id = normalize_provider_id(identity.get("provider_id"))
    family_id = normalize_family_id(identity.get("family"))
    loader_id = normalize_loader_id(identity.get("loader"))
    mode_id = str(identity.get("mode") or MODE_ID)
    engine_id = str(identity.get("engine") or ENGINE_ID)

    resolution = resolve_lanpaint_family_policy(contract)
    policy = _mapping(resolution.get("policy"))
    resolved = _mapping(resolution.get("resolved_contract"))
    policy_identity = _mapping(policy.get("identity"))
    policy_status = str(policy_identity.get("status") or resolution.get("resolution_state") or "missing_policy")
    complete = bool(policy and policy_status == COMPLETE_POLICY_STATE and resolution.get("resolution_state") == "resolved_policy_only")
    binding_spec = _ACTIVE_BINDINGS.get((family_id, loader_id)) if provider_id in SUPPORTED_PROVIDERS and mode_id == MODE_ID and engine_id == ENGINE_ID else None
    bound = bool(complete and binding_spec)

    loader_policy = _mapping(_mapping(policy.get("loader_policies")).get(loader_id))
    text_policy = _mapping(policy.get("text_encoder_policy"))
    vae_policy = _mapping(policy.get("vae_policy"))
    conditioning = deepcopy(_mapping(policy.get("conditioning_policy")))
    route_defaults = _mapping(policy.get("route_defaults"))
    resolved_family = _mapping(resolved.get("family_policy"))
    lora_policy = _mapping(policy.get("lora_policy"))
    sampler_defaults = deepcopy(_mapping(resolved.get("sampler_policy") or route_defaults.get("sampler_policy")))
    latent_policy = deepcopy(_mapping(resolved.get("latent_policy") or route_defaults.get("latent_policy")))

    sampler_contract = str(sampler_defaults.get("sampler_contract") or "basic")
    if sampler_contract == "custom_advanced":
        sampler_node = "LanPaint_SamplerCustomAdvanced"
    elif sampler_contract == "advanced":
        sampler_node = "LanPaint_KSamplerAdvanced"
    elif sampler_contract == "custom":
        sampler_node = "LanPaint_SamplerCustom"
    else:
        sampler_node = "LanPaint_KSampler"
    sampler_adapter = {
        "contract": sampler_contract,
        "node_class": sampler_node,
        "defaults": sampler_defaults,
        "input_semantics": {
            "model": "final_family_model",
            "positive": "positive_conditioning",
            "negative": "negative_conditioning",
            "latent_image": "masked_latent",
        },
    }
    # Allow future policies to override sampler adapter without changing consumers.
    policy["sampler_adapter"] = deepcopy(sampler_adapter)

    assets = _asset_slots(policy, family_id, loader_id) if policy else {}
    capabilities = _capability_contract(policy, loader_id) if complete else {
        "node_groups": [], "conditional_node_groups": {}, "required_model_roles": [],
        "family_policy_resolution_required": True, "live_object_info_required": True, "fail_closed": True,
    }
    compatibility_key = f"{family_id}:{loader_id}:{mode_id}"
    route_key = f"{family_id}:{loader_id}:{mode_id}:{engine_id}"
    graph_profile = str((binding_spec or {}).get("graph_profile") or ("unresolved_family_graph" if not complete else "policy_complete_unbound"))

    adapter: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "phase_state": PHASE13_STATE,
        "route_family_id": ROUTE_FAMILY_ID,
        "identity": {
            "adapter_id": f"lanpaint.{family_id}.{loader_id}.adapter.v2",
            "provider_id": provider_id,
            "family": family_id,
            "loader": loader_id,
            "mode": mode_id,
            "engine": engine_id,
            "variant": str(policy_identity.get("variant") or identity.get("variant") or "default"),
            "compatibility_key": compatibility_key,
            "route_key": route_key,
        },
        "policy": {
            "policy_id": str(policy_identity.get("policy_id") or ""),
            "policy_status": policy_status,
            "policy_fingerprint": str(policy.get("policy_fingerprint") or ""),
            "resolution_state": str(resolution.get("resolution_state") or "missing_policy"),
            "complete": complete,
        },
        "binding": {
            "state": "compiler_bound" if bound else ("policy_complete_unbound" if complete else "scaffold_only"),
            "selectable": bound,
            "executable": bound,
            "compiler_id": COMPILER_ID if bound else None,
            "workflow_type": WORKFLOW_TYPE if bound else None,
            "graph_profile": graph_profile,
            "phase": str((binding_spec or {}).get("phase") or "Phase 13 adapter metadata only"),
            "new_route_activated_by_phase13": False,
            "new_route_activated_by_phase14": bool(bound and (family_id, loader_id) in _PHASE14_NEW_BINDINGS),
            "new_route_activated_by_phase15": bool(bound and (family_id, loader_id) in _PHASE15_NEW_BINDINGS),
            "new_route_activated_by_phase16": bool(bound and (family_id, loader_id) in _PHASE16_NEW_BINDINGS),
            "new_route_activated_by_phase17": bool(bound and (family_id, loader_id) in _PHASE17_NEW_BINDINGS),
            "new_route_activated_by_phase18": bool(bound and (family_id, loader_id) in _PHASE18_NEW_BINDINGS),
            "new_route_activated_by_phase22": bool(bound and (family_id, loader_id) in _PHASE22_NEW_BINDINGS),
        },
        "loaders": {
            "model": {
                "role_id": _model_role_id(loader_policy, loader_id) if loader_policy else "",
                "accepted_node_classes": [str(item) for item in _list(loader_policy.get("accepted_node_classes"))],
                "preferred_node_class": str(loader_policy.get("preferred_node_class") or ""),
                "input_keys": deepcopy(_mapping(loader_policy.get("model_input_keys"))),
                "default_inputs": deepcopy(_mapping(loader_policy.get("default_inputs"))),
                "output_type": str(loader_policy.get("output_type") or "MODEL"),
            },
            "text_encoder": {
                "role_id": str(_mapping(text_policy.get("loader_role_ids")).get(loader_id) or (assets.get("text_encoder") or {}).get("role_id") or ""),
                "accepted_node_classes": [str(item) for item in _list(_mapping(text_policy.get("loader_node_classes")).get(loader_id))] or [str(text_policy.get("node_class") or "CLIPLoader")],
                "preferred_node_class": str(_mapping(text_policy.get("preferred_node_classes")).get(loader_id) or text_policy.get("node_class") or "CLIPLoader"),
                "asset_slots": deepcopy(_list(text_policy.get("asset_slots"))),
                "output_port": int(text_policy.get("output_port") or 0),
                "bundled_with_model": bool(text_policy.get("bundled_with_model")),
                "clip_type": str(text_policy.get("required_clip_type") or ""),
                "default_device": str(text_policy.get("default_device") or "default"),
                "output_type": str(text_policy.get("output_type") or "CLIP"),
            },
            "vae": {
                "role_id": str(_mapping(vae_policy.get("loader_role_ids")).get(loader_id) or (assets.get("vae") or {}).get("role_id") or ""),
                "accepted_node_classes": [str(vae_policy.get("node_class") or "VAELoader")],
                "bundled_with_model": bool(vae_policy.get("bundled_with_model")),
                "output_port": int(vae_policy.get("output_port") or 0),
                "encode_node_class": str(vae_policy.get("encode_node_class") or "VAEEncode"),
                "decode_node_class": str(vae_policy.get("decode_node_class") or "VAEDecode"),
                "output_type": str(vae_policy.get("output_type") or "VAE"),
            },
        },
        "assets": {"slots": assets, "selection_authority": "selected_comfy_profile_catalog"},
        "conditioning": conditioning,
        "spatial": {
            "crop": deepcopy(_mapping(resolved.get("crop_policy") or route_defaults.get("crop_policy"))),
            "mask": deepcopy(_mapping(resolved.get("mask_policy") or route_defaults.get("mask_policy"))),
            "stitch": deepcopy(_mapping(resolved.get("stitch_policy") or route_defaults.get("stitch_policy"))),
        },
        "latent": latent_policy,
        "model_transforms": deepcopy(_mapping(resolved_family.get("model_transform_pipeline") or policy.get("model_transform_pipeline"))),
        "sampler": sampler_adapter,
        "lora": {
            "support_state": str(lora_policy.get("lora_support_state") or "unsupported"),
            "mode": "model_only" if "model_only" in str(lora_policy.get("lora_injection_strategy") or "") else "model_and_clip",
            "injection_strategy": str(lora_policy.get("lora_injection_strategy") or "unsupported"),
            "injection_point": str(lora_policy.get("injection_point") or ""),
            "loader_node_class": str(lora_policy.get("loader_node_class") or ""),
            "strength_fields": [str(item) for item in _list(lora_policy.get("strength_fields"))],
            "allow_multiple": bool(lora_policy.get("allow_multiple")),
            "compatibility_key": compatibility_key,
            "compatibility_engine_independent": True,
            "graph_anchors_owned_by_compiler": True,
        },
        "capabilities": capabilities,
        "replay": {
            "adapter_id_required": True,
            "adapter_fingerprint_required": True,
            "exact_route_required": True,
            "portable_asset_roles": ["source", "mask"],
            "live_capability_revalidation_required": True,
            "selected_asset_catalog_revalidation_required": True,
        },
        "stabilization": {
            "state": ("phase22_anima_ideogram4_onboarded" if (family_id, loader_id) in _PHASE22_NEW_BINDINGS and bound else ("phase21_hidream_onboarded" if (family_id, loader_id) in _PHASE21_NEW_BINDINGS and bound else ("phase20_z_image_onboarded" if (family_id, loader_id) in _PHASE20_COMPLETED_BINDINGS and bound else ("phase18_qwen_edit_onboarded" if (family_id, loader_id) in _PHASE18_NEW_BINDINGS and bound else ("phase17_flux2_onboarded" if (family_id, loader_id) in _PHASE17_NEW_BINDINGS and bound else ("phase16_flux1_onboarded" if (family_id, loader_id) in _PHASE16_NEW_BINDINGS and bound else ("phase15_sd_onboarded" if (family_id, loader_id) in _PHASE15_NEW_BINDINGS and bound else ("phase14_stabilized" if (family_id, loader_id) in _PHASE14_STABILIZED_BINDINGS and bound else "not_stabilized")))))))),
            "phase_state": PHASE22_STATE if (family_id, loader_id) in _PHASE22_NEW_BINDINGS and bound else (PHASE21_STATE if (family_id, loader_id) in _PHASE21_NEW_BINDINGS and bound else (PHASE20_STATE if (family_id, loader_id) in _PHASE20_COMPLETED_BINDINGS and bound else (PHASE18_STATE if (family_id, loader_id) in _PHASE18_NEW_BINDINGS and bound else (PHASE17_STATE if (family_id, loader_id) in _PHASE17_NEW_BINDINGS and bound else (PHASE16_STATE if (family_id, loader_id) in _PHASE16_NEW_BINDINGS and bound else (PHASE15_STATE if (family_id, loader_id) in _PHASE15_NEW_BINDINGS and bound else PHASE14_STATE)))))),
            "loader_parity_group": f"{family_id}:inpaint:lanpaint",
            "new_binding_activated": bool((family_id, loader_id) in (_PHASE14_NEW_BINDINGS | _PHASE15_NEW_BINDINGS | _PHASE16_NEW_BINDINGS | _PHASE17_NEW_BINDINGS | _PHASE18_NEW_BINDINGS | _PHASE21_NEW_BINDINGS | _PHASE22_NEW_BINDINGS) and bound),
            "new_binding_activated_phase15": bool((family_id, loader_id) in _PHASE15_NEW_BINDINGS and bound),
            "new_binding_activated_phase16": bool((family_id, loader_id) in _PHASE16_NEW_BINDINGS and bound),
            "new_binding_activated_phase17": bool((family_id, loader_id) in _PHASE17_NEW_BINDINGS and bound),
            "new_binding_activated_phase18": bool((family_id, loader_id) in _PHASE18_NEW_BINDINGS and bound),
            "new_binding_activated_phase21": bool((family_id, loader_id) in _PHASE21_NEW_BINDINGS and bound),
            "new_binding_activated_phase22": bool((family_id, loader_id) in _PHASE22_NEW_BINDINGS and bound),
            "physical_validation": "pending",
            "promotion_state": "experimental_available" if bound else "unsupported",
        },
        "diagnostics": {
            "contract_issues": deepcopy(contract_issues),
            "policy_resolution_issues": deepcopy(resolution.get("issues") if isinstance(resolution.get("issues"), list) else []),
            "unresolved_reason": str(_mapping(policy.get("placeholder")).get("unresolved_reason") or "") if policy_status == PLACEHOLDER_POLICY_STATE else "",
        },
    }
    if bound and (family_id, loader_id) in _PHASE20_COMPLETED_BINDINGS:
        adapter["binding"]["completed_by_phase20"] = True
        adapter["family_variant"] = deepcopy(_mapping(policy.get("family_variant")))
        adapter["stability_policy"] = deepcopy(_mapping(route_defaults.get("stability_policy")))
        adapter["stabilization"]["completed_phase20"] = True
    if bound and (family_id, loader_id) in _PHASE21_NEW_BINDINGS:
        adapter["binding"]["new_route_activated_by_phase21"] = True
        adapter["family_variant"] = deepcopy(_mapping(policy.get("family_variant")))
        adapter["stabilization"]["onboarded_phase21"] = True
    if bound and (family_id, loader_id) in _PHASE22_NEW_BINDINGS:
        adapter["binding"]["new_route_activated_by_phase22"] = True
        adapter["family_variant"] = deepcopy(_mapping(policy.get("family_variant")))
        adapter["stabilization"]["onboarded_phase22"] = True
    adapter["adapter_fingerprint"] = lanpaint_family_adapter_fingerprint(adapter)
    issues = _adapter_validation(adapter)
    adapter["validation"] = {"ok": not any(item.get("level") == "error" for item in issues), "issues": issues}
    return adapter


def get_lanpaint_family_adapter(
    family: Any,
    *,
    loader: Any,
    provider_id: Any = "comfyui",
    mode: Any = MODE_ID,
    engine: Any = ENGINE_ID,
    variant: Any = "default",
) -> dict[str, Any]:
    contract = {
        "identity": {
            "provider_id": provider_id,
            "family": family,
            "loader": loader,
            "mode": mode,
            "engine": engine,
            "variant": variant,
        }
    }
    return resolve_lanpaint_family_adapter(contract)


def lanpaint_family_adapter_registry(provider_id: Any = "comfyui") -> dict[str, Any]:
    provider = normalize_provider_id(provider_id)
    policies = []
    from neo_app.image.lanpaint_family_policies import lanpaint_family_policy_registry

    for policy in lanpaint_family_policy_registry().get("policies", []):
        identity = _mapping(policy.get("identity"))
        for loader_id in _list(identity.get("loader_ids")):
            policies.append(get_lanpaint_family_adapter(
                identity.get("family"), loader=loader_id, provider_id=provider,
                variant=identity.get("variant") or "default",
            ))
    policies.sort(key=lambda item: (item["identity"]["family"], item["identity"]["loader"]))
    registry = {
        "schema_id": REGISTRY_SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "phase_state": PHASE13_STATE,
        "stabilization_state": PHASE14_STATE,
        "onboarding_state": PHASE22_STATE,
        "previous_onboarding_state": PHASE21_STATE,
        "provider_id": provider,
        "adapters": policies,
        "active_route_keys": [item["identity"]["route_key"] for item in policies if item["binding"]["selectable"]],
        "stabilized_route_keys": [item["identity"]["route_key"] for item in policies if _mapping(item.get("stabilization")).get("state") == "phase14_stabilized"],
        "phase20_completed_route_keys": [item["identity"]["route_key"] for item in policies if _mapping(item.get("binding")).get("completed_by_phase20")],
        "phase21_onboarded_route_keys": [item["identity"]["route_key"] for item in policies if _mapping(item.get("binding")).get("new_route_activated_by_phase21")],
        "phase22_onboarded_route_keys": [item["identity"]["route_key"] for item in policies if _mapping(item.get("binding")).get("new_route_activated_by_phase22")],
        "new_routes_activated": [item["identity"]["route_key"] for item in policies if _mapping(item.get("stabilization")).get("new_binding_activated")],
    }
    registry["registry_fingerprint"] = hashlib.sha256(json.dumps(
        [{"id": item["identity"]["adapter_id"], "fingerprint": item["adapter_fingerprint"]} for item in policies],
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")).hexdigest()
    return registry


def adapter_asset_candidates(adapter: Mapping[str, Any], params: Mapping[str, Any] | None, *, job_model: Any = None) -> dict[str, list[Any]]:
    values = _mapping(params)
    slots = _mapping(_mapping(adapter.get("assets")).get("slots"))
    result: dict[str, list[Any]] = {}
    for slot_id, raw_slot in slots.items():
        slot = _mapping(raw_slot)
        candidates = [values.get(name) for name in _list(slot.get("param_aliases"))]
        if slot_id == "model" and job_model not in (None, ""):
            candidates.insert(0, job_model)
        result[slot_id] = candidates
    return result


def adapter_snapshot(adapter: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(adapter))
    payload.pop("diagnostics", None)
    payload.pop("validation", None)
    return payload


__all__ = [
    "AUTHORITY",
    "COMPILER_ID",
    "PHASE13_STATE",
    "PHASE14_STATE",
    "PHASE15_STATE",
    "PHASE16_STATE",
    "PHASE17_STATE",
    "PHASE18_STATE",
    "PHASE20_STATE",
    "PHASE21_STATE",
    "PHASE22_STATE",
    "REGISTRY_SCHEMA_ID",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "SUPPORTED_PROVIDERS",
    "WORKFLOW_TYPE",
    "adapter_asset_candidates",
    "adapter_snapshot",
    "get_lanpaint_family_adapter",
    "lanpaint_family_adapter_fingerprint",
    "lanpaint_family_adapter_registry",
    "resolve_lanpaint_family_adapter",
]
