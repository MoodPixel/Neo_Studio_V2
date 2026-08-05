from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping

from neo_app.image.lanpaint_route_contract import (
    ENGINE_ID,
    EXECUTION_STATE,
    MODE_ID,
    ROUTE_FAMILY_ID,
    lanpaint_contract_fingerprint,
    normalize_family_id,
    normalize_lanpaint_route_contract,
    normalize_loader_id,
    normalize_provider_id,
)

SCHEMA_ID = "neo.image.lanpaint_family_policy.v1"
SCHEMA_VERSION = 1
REGISTRY_SCHEMA_ID = "neo.image.lanpaint_family_policy_registry.v1"
RESOLUTION_SCHEMA_ID = "neo.image.lanpaint_family_policy_resolution.v1"
AUTHORITY = "neo_app.image.lanpaint_family_policies"
POLICY_STATE = "policy_only"

SUPPORTED_LOCAL_PROVIDERS = ("comfyui", "comfyui_portable")
SUPPORTED_LOADER_IDS = ("checkpoint", "diffusion_model", "gguf")

COMPLETE_POLICY_STATE = "complete_policy"
PLACEHOLDER_POLICY_STATE = "unresolved_placeholder"

_REQUIRED_POLICY_SECTIONS = (
    "identity",
    "loader_policies",
    "text_encoder_policy",
    "vae_policy",
    "conditioning_policy",
    "route_defaults",
    "lora_policy",
    "node_requirements",
    "model_requirements",
    "model_transform_pipeline",
    "execution",
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _slug(value: Any) -> str:
    import re

    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def _policy_fingerprint_payload(policy: Mapping[str, Any]) -> dict[str, Any]:
    payload = deepcopy(dict(policy))
    payload.pop("policy_fingerprint", None)
    payload.pop("validation", None)
    return payload


def lanpaint_family_policy_fingerprint(policy: Mapping[str, Any]) -> str:
    canonical = json.dumps(
        _policy_fingerprint_payload(policy),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _execution_lock(reason: str) -> dict[str, Any]:
    return {
        "enabled": False,
        "selectable": False,
        "state": POLICY_STATE,
        "compiler_id": None,
        "workflow_type": None,
        "execution_ready": False,
        "reason": reason,
    }


def _base_policy_identity(
    *,
    policy_id: str,
    family: str,
    display_name: str,
    status: str,
    supported_loaders: list[str],
    variant: str = "default",
) -> dict[str, Any]:
    return {
        "policy_id": policy_id,
        "route_family_id": ROUTE_FAMILY_ID,
        "family": normalize_family_id(family),
        "display_name": display_name,
        "provider_ids": list(SUPPORTED_LOCAL_PROVIDERS),
        "loader_ids": [normalize_loader_id(item) for item in supported_loaders],
        "mode": MODE_ID,
        "engine": ENGINE_ID,
        "variant": _slug(variant) or "default",
        "status": status,
        "policy_state": POLICY_STATE,
    }


def _krea2_turbo_policy() -> dict[str, Any]:
    """Return the first complete family overlay for the base LanPaint route.

    Values intentionally reflect the submitted Krea 2 Turbo crop/stitch workflow,
    while loader, conditioning, LoRA and capability rules remain explicit so the
    base route can later host Qwen, Z-Image and other families without inheriting
    Krea-specific semantics.
    """

    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id="lanpaint.krea2_turbo.v1",
            family="krea2_turbo",
            display_name="Krea 2 Turbo LanPaint Inpaint",
            status=COMPLETE_POLICY_STATE,
            supported_loaders=["gguf", "diffusion_model"],
            variant="crop_stitch_v1",
        ),
        "loader_policies": {
            "gguf": {
                "model_loader_role": "gguf_diffusion_model",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UnetLoaderGGUF", "LoaderGGUF"],
                "preferred_node_class": "UnetLoaderGGUF",
                "model_input_keys": {
                    "UnetLoaderGGUF": "unet_name",
                    "LoaderGGUF": "gguf_name",
                },
                "output_type": "MODEL",
                "text_encoder_quantization": "forbidden",
                "notes": "Only the Krea 2 diffusion transformer is GGUF; Qwen3-VL-4B remains native/safetensors.",
            },
            "diffusion_model": {
                "model_loader_role": "diffusion_model",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UNETLoader"],
                "preferred_node_class": "UNETLoader",
                "model_input_keys": {"UNETLoader": "unet_name"},
                "default_inputs": {"weight_dtype": "default"},
                "output_type": "MODEL",
                "notes": "Safetensors/component Krea 2 Turbo branch for later parity testing.",
            },
        },
        "text_encoder_policy": {
            "text_encoder_role": "qwen3vl_4b_native",
            "node_class": "CLIPLoader",
            "required_clip_type": "krea2",
            "default_device": "default",
            "accepted_asset_classifications": ["qwen3vl_4b_native"],
            "rejected_asset_classifications": [
                "qwen3vl_4b_gguf",
                "gguf_other",
                "mmproj",
                "qwen3vl_wrong_scale",
                "qwen2_family",
                "qwen3_plain",
            ],
            "output_type": "CLIP",
        },
        "vae_policy": {
            "vae_role": "qwen_image_vae",
            "node_class": "VAELoader",
            "accepted_asset_classifications": ["qwen_image_vae"],
            "rejected_asset_classifications": ["foreign_flux_ae", "foreign_sd_vae"],
            "encode_node_class": "VAEEncode",
            "decode_node_class": "VAEDecode",
            "output_type": "VAE",
        },
        "conditioning_policy": {
            "positive": {
                "positive_conditioning_policy": "clip_text_encode",
                "node_class": "CLIPTextEncode",
                "source": "effective_positive_prompt",
            },
            "negative": {
                "negative_conditioning_policy": "zero_out_positive_conditioning",
                "node_class": "ConditioningZeroOut",
                "source": "positive_conditioning",
                "user_negative_prompt_effect": "not_sampled_for_turbo_policy",
            },
        },
        "route_defaults": {
            "crop_policy": {
                "enabled": True,
                "context_mode": "masked_bounds",
                "padding_px": 152,
                "processing_size": {"width": 768, "height": 768, "multiple_of": 8},
                "resize_method": "lanczos",
            },
            "mask_policy": {
                "sampling": {
                    "expand_px": 45,
                    "blur_radius": 31.0,
                    "fill_holes": False,
                    "invert": False,
                },
                "stitch": {
                    "expand_px": 50,
                    "blur_radius": 9.1,
                    "fill_holes": False,
                    "invert": False,
                },
            },
            "latent_policy": {
                "latent_format": "qwen_image",
                "noise_mask_required": True,
                "differential_diffusion": "required",
            },
            "sampler_policy": {
                "steps": 8,
                "cfg": 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "lanpaint_thinking_steps": 10,
                "prompt_mode": "image_first",
                "inpainting_mode": "image",
                "family_semantics_locked": True,
            },
            "stitch_policy": {
                "enabled": True,
                "restore_crop_size": True,
                "composite_into_source": True,
                "preserve_source_dimensions": True,
                "resize_method": "lanczos",
                "composite_node_role": "source_space_masked_composite",
            },
        },
        "lora_policy": {
            "lora_support_state": "experimental",
            "lora_injection_strategy": "model_only",
            "stack_source": "neo.image.lora_stack",
            "injection_point": "pre_sampler_model_transform",
            "loader_node_class": "LoraLoaderModelOnly",
            "allow_multiple": True,
            "strength_fields": ["strength_model"],
            "clip_strength_supported": False,
            "trigger_word_policy": "canonical_prompt_extension",
            "visible_prompt_mutation": False,
        },
        "node_requirements": {
            "required_node_classes": [
                "CLIPLoader",
                "CLIPTextEncode",
                "ConditioningZeroOut",
                "VAELoader",
                "CropByMask",
                "ImageResizeKJv2",
                "GrowMaskWithBlur",
                "VAEEncode",
                "SetLatentNoiseMask",
                "DifferentialDiffusionAdvanced",
                "LanPaint_KSampler",
                "VAEDecode",
                "ImageCompositeMasked",
            ],
            "loader_specific_node_classes": {
                "gguf": ["UnetLoaderGGUF|LoaderGGUF"],
                "diffusion_model": ["UNETLoader"],
            },
            "conditional_node_classes": {
                "lora_stack_enabled": ["LoraLoaderModelOnly"],
            },
            "required_custom_node_packs": [
                {"pack_id": "LanPaint", "provides": ["LanPaint_KSampler"]},
                {"pack_id": "comfyui-inpainteasy", "provides": ["CropByMask"]},
                {"pack_id": "ComfyUI-KJNodes", "provides": ["ImageResizeKJv2", "GrowMaskWithBlur"]},
            ],
            "authoring_only_nodes_excluded": [
                "SAM3Segment",
                "Switch mask [Crystools]",
                "MaskPreview+",
                "Image Comparer (rgthree)",
                "CR Upscale Image",
                "PreviewImage",
            ],
        },
        "model_requirements": {
            "required_model_roles": [
                {
                    "role_id": "krea2_turbo_diffusion_model",
                    "loader_scope": ["gguf", "diffusion_model"],
                    "selection_policy": "selected_comfy_profile_catalog",
                    "portable_identity_only": True,
                },
                {
                    "role_id": "qwen3vl_4b_native_text_encoder",
                    "loader_scope": ["gguf", "diffusion_model"],
                    "selection_policy": "selected_comfy_profile_catalog",
                    "portable_identity_only": True,
                },
                {
                    "role_id": "qwen_image_vae",
                    "loader_scope": ["gguf", "diffusion_model"],
                    "selection_policy": "selected_comfy_profile_catalog",
                    "portable_identity_only": True,
                },
            ],
            "optional_model_roles": [
                {
                    "role_id": "krea2_compatible_lora",
                    "enabled_when": "neo.image.lora_stack has enabled rows",
                    "portable_identity_only": True,
                }
            ],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [
                {
                    "transform_id": "neo_lora_stack",
                    "required": False,
                    "enabled_when": "lora_stack_has_enabled_rows",
                    "input_type": "MODEL",
                    "output_type": "MODEL",
                    "strategy": "model_only",
                },
                {
                    "transform_id": "differential_diffusion_advanced",
                    "required": True,
                    "input_type": "MODEL",
                    "output_type": "MODEL",
                    "context_inputs": ["masked_latent", "sampling_mask"],
                },
            ],
            "final_output_port": "family_model_transform.sample_model",
        },
        "execution": _execution_lock(
            "Phase 3 resolves family semantics only. Provider node discovery, workflow compilation, UI exposure and execution remain disabled."
        ),
    }
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {
        "ok": not any(item["level"] == "error" for item in issues),
        "issues": deepcopy(issues),
        "complete": True,
    }
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy



def _aura_family_policy(
    *,
    policy_id: str,
    family: str,
    display_name: str,
    clip_type: str,
    text_encoder_role: str,
    vae_role: str,
    model_role: str,
    steps: int,
    cfg: float,
    aura_shift: float,
    zero_negative: bool = False,
    latent_format: str,
    variant: str = "crop_stitch_aura_v1",
    family_variant: str = "standard",
    lanpaint_thinking_steps: int = 5,
    stability_policy: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a complete Qwen/Z-Image LanPaint policy.

    These families share the reusable crop/mask/restore stages but own their
    loader, conditioning, AuraFlow sampling patch, sampler defaults and LoRA
    semantics. They intentionally do not inherit Krea 2 Differential Diffusion.
    """

    negative_policy = {
        "negative_conditioning_policy": "zero_out_positive_conditioning" if zero_negative else "clip_text_encode",
        "node_class": "ConditioningZeroOut" if zero_negative else "CLIPTextEncode",
        "source": "positive_conditioning" if zero_negative else "effective_negative_prompt",
        "user_negative_prompt_effect": "not_sampled_for_distilled_policy" if zero_negative else "sampled",
    }
    required_nodes = [
        "CLIPLoader",
        "CLIPTextEncode",
        "VAELoader",
        "CropByMask",
        "ImageResizeKJv2",
        "GrowMaskWithBlur",
        "VAEEncode",
        "SetLatentNoiseMask",
        "ModelSamplingAuraFlow",
        "LanPaint_KSampler",
        "VAEDecode",
        "ImageCompositeMasked",
    ]
    if zero_negative:
        required_nodes.append("ConditioningZeroOut")

    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id=policy_id,
            family=family,
            display_name=display_name,
            status=COMPLETE_POLICY_STATE,
            supported_loaders=["gguf", "diffusion_model"],
            variant=variant,
        ),
        "loader_policies": {
            "gguf": {
                "model_loader_role": "gguf_unet",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UnetLoaderGGUF", "LoaderGGUF"],
                "preferred_node_class": "UnetLoaderGGUF",
                "model_input_keys": {"UnetLoaderGGUF": "unet_name", "LoaderGGUF": "gguf_name"},
                "default_inputs": {},
                "output_type": "MODEL",
            },
            "diffusion_model": {
                "model_loader_role": "diffusion_model",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UNETLoader"],
                "preferred_node_class": "UNETLoader",
                "model_input_keys": {"UNETLoader": "unet_name"},
                "default_inputs": {"weight_dtype": "default"},
                "output_type": "MODEL",
            },
        },
        "text_encoder_policy": {
            "text_encoder_role": text_encoder_role,
            "loader_role_ids": {
                "diffusion_model": "qwen_image_clip_loader" if clip_type == "qwen_image" else "lumina2_clip_loader",
                "gguf": "qwen_image_clip_loader" if clip_type == "qwen_image" else "lumina2_clip_loader",
            },
            "loader_node_classes": {
                "diffusion_model": ["CLIPLoader"],
                "gguf": ["CLIPLoaderGGUF", "ClipLoaderGGUF"],
            },
            "preferred_node_classes": {
                "diffusion_model": "CLIPLoader",
                "gguf": "CLIPLoaderGGUF",
            },
            "node_class": "CLIPLoader",
            "required_clip_type": clip_type,
            "default_device": "default",
            "accepted_asset_classifications": [text_encoder_role],
            "rejected_asset_classifications": [],
            "output_type": "CLIP",
        },
        "vae_policy": {
            "vae_role": vae_role,
            "loader_role_ids": {"diffusion_model": "vae_or_ae", "gguf": "vae_or_ae"},
            "node_class": "VAELoader",
            "accepted_asset_classifications": [vae_role],
            "rejected_asset_classifications": [],
            "encode_node_class": "VAEEncode",
            "decode_node_class": "VAEDecode",
            "output_type": "VAE",
        },
        "conditioning_policy": {
            "positive": {
                "positive_conditioning_policy": "clip_text_encode",
                "node_class": "CLIPTextEncode",
                "source": "effective_positive_prompt",
            },
            "negative": negative_policy,
        },
        "route_defaults": {
            "crop_policy": {
                "enabled": True,
                "context_mode": "masked_bounds",
                "padding_px": 152,
                "processing_size": {"width": 768, "height": 768, "multiple_of": 8},
                "resize_method": "lanczos",
            },
            "mask_policy": {
                "sampling": {"expand_px": 45, "blur_radius": 31.0, "fill_holes": False, "invert": False},
                "stitch": {"expand_px": 50, "blur_radius": 9.1, "fill_holes": False, "invert": False},
            },
            "latent_policy": {
                "latent_format": latent_format,
                "noise_mask_required": True,
                "differential_diffusion": "not_used",
                "model_sampling_patch": "ModelSamplingAuraFlow",
                "aura_shift": aura_shift,
            },
            "sampler_policy": {
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "lanpaint_thinking_steps": int(lanpaint_thinking_steps),
                "prompt_mode": "image_first",
                "inpainting_mode": "image",
                "family_semantics_locked": True,
            },
            "stitch_policy": {
                "enabled": True,
                "restore_crop_size": True,
                "composite_into_source": True,
                "preserve_source_dimensions": True,
                "resize_method": "lanczos",
                "composite_node_role": "source_space_masked_composite",
            },
        },
        "lora_policy": {
            "lora_support_state": "experimental",
            "lora_injection_strategy": "model_and_clip",
            "stack_source": "neo.image.lora_stack",
            "injection_point": "pre_family_sampling_patch",
            "loader_node_class": "LoraLoader",
            "allow_multiple": True,
            "strength_fields": ["strength_model", "strength_clip"],
            "clip_strength_supported": True,
            "trigger_word_policy": "canonical_prompt_extension",
            "visible_prompt_mutation": False,
        },
        "node_requirements": {
            "required_node_classes": required_nodes,
            "loader_specific_node_classes": {
                "gguf": ["UnetLoaderGGUF|LoaderGGUF", "CLIPLoaderGGUF|ClipLoaderGGUF"],
                "diffusion_model": ["UNETLoader", "CLIPLoader"],
            },
            "conditional_node_classes": {"lora_stack_enabled": ["LoraLoader"]},
            "required_custom_node_packs": [
                {"pack_id": "LanPaint", "provides": ["LanPaint_KSampler"]},
                {"pack_id": "comfyui-inpainteasy", "provides": ["CropByMask"]},
                {"pack_id": "ComfyUI-KJNodes", "provides": ["ImageResizeKJv2", "GrowMaskWithBlur"]},
            ],
            "authoring_only_nodes_excluded": [
                "SAM3Segment", "Switch mask [Crystools]", "MaskPreview+",
                "Image Comparer (rgthree)", "CR Upscale Image", "PreviewImage",
            ],
        },
        "model_requirements": {
            "required_model_roles": [
                {"role_id": model_role, "loader_scope": ["gguf", "diffusion_model"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": text_encoder_role, "loader_scope": ["gguf", "diffusion_model"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": vae_role, "loader_scope": ["gguf", "diffusion_model"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
            ],
            "optional_model_roles": [
                {"role_id": f"{family}_compatible_lora", "enabled_when": "neo.image.lora_stack has enabled rows", "portable_identity_only": True}
            ],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [
                {"transform_id": "neo_lora_stack", "required": False, "enabled_when": "lora_stack_has_enabled_rows", "input_type": "MODEL+CLIP", "output_type": "MODEL+CLIP", "strategy": "model_and_clip"},
                {"transform_id": "model_sampling_aura_flow", "required": True, "input_type": "MODEL", "output_type": "MODEL", "inputs": {"shift": aura_shift}},
            ],
            "final_output_port": "family_model_transform.sample_model",
        },
        "execution": _execution_lock(
            "Family semantics are complete; compile routing, capability gating and UI exposure are owned by Phase 10."
        ),
    }
    if family_variant != "standard":
        policy["family_variant"] = {
            "id": family_variant,
            "canonical_family_id": family,
            "lanpaint_route_only": True,
            "independent_sampling_policy": family_variant in {"base", "turbo"},
        }
    if stability_policy:
        policy["route_defaults"]["stability_policy"] = deepcopy(dict(stability_policy))
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {
        "ok": not any(item["level"] == "error" for item in issues),
        "issues": deepcopy(issues),
        "complete": True,
    }
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy


def _qwen_edit_policy(*, family: str, display_name: str, policy_id: str, model_role: str) -> dict[str, Any]:
    """Build a versioned Qwen Image Edit LanPaint policy.

    Qwen Image Edit shares the Qwen Image transformer/encoder/VAE architecture,
    but its conditioning is source-aware and must never fall back to plain
    CLIPTextEncode. LanPaint uses Image 1 as the single editable canvas; normal
    edit/img2img routes may still use up to three source lanes.
    """
    policy = _aura_family_policy(
        policy_id=policy_id,
        family=family,
        display_name=display_name,
        clip_type="qwen_image",
        text_encoder_role="qwen_image_edit_text_encoder",
        vae_role="qwen_image_vae",
        model_role=model_role,
        steps=20,
        cfg=4.0,
        aura_shift=3.1,
        zero_negative=False,
        latent_format="qwen_image_edit",
    )
    identity = _mapping(policy.get("identity"))
    identity["variant"] = "edit_crop_stitch_aura_v1"
    identity["status"] = COMPLETE_POLICY_STATE
    policy["identity"] = identity

    edit_nodes = [
        "TextEncodeQwenImageEditPlus",
        "TextEncodeQwenImageEditPlus_lrzjason",
        "TextEncodeQwenImageEditPlusAdvance_lrzjason",
        "TextEncodeQwenImageEditPlusPro_lrzjason",
    ]
    policy["conditioning_policy"] = {
        "positive": {
            "positive_conditioning_policy": "qwen_image_edit_plus_single_canvas",
            "node_class": "TextEncodeQwenImageEditPlus",
            "accepted_node_classes": edit_nodes,
            "source": "effective_positive_prompt+image1",
            "source_image_role": "editable_canvas",
            "max_source_images_for_lanpaint": 1,
        },
        "negative": {
            "negative_conditioning_policy": "qwen_image_edit_plus_single_canvas",
            "node_class": "TextEncodeQwenImageEditPlus",
            "accepted_node_classes": edit_nodes,
            "source": "effective_negative_prompt+image1",
            "source_image_role": "editable_canvas",
            "user_negative_prompt_effect": "sampled",
            "max_source_images_for_lanpaint": 1,
        },
    }
    node_req = _mapping(policy.get("node_requirements"))
    required = [item for item in node_req.get("required_node_classes", []) if item != "CLIPTextEncode"]
    required.append("TextEncodeQwenImageEditPlus|TextEncodeQwenImageEditPlus_lrzjason|TextEncodeQwenImageEditPlusAdvance_lrzjason|TextEncodeQwenImageEditPlusPro_lrzjason")
    node_req["required_node_classes"] = required
    node_req["loader_specific_node_classes"] = {
        "diffusion_model": ["UNETLoader", "CLIPLoader"],
        "gguf": ["UnetLoaderGGUF|LoaderGGUF", "CLIPLoaderGGUF|ClipLoaderGGUF"],
    }
    node_req["conditional_node_classes"] = {
        **_mapping(node_req.get("conditional_node_classes")),
        "gguf_edit_conditioning": edit_nodes,
        "lora_stack_enabled": ["LoraLoader"],
    }
    policy["node_requirements"] = node_req

    text_policy = _mapping(policy.get("text_encoder_policy"))
    text_policy["asset_slots"] = [
        {
            "slot_id": "text_encoder",
            "role_id": "qwen_image_edit_text_encoder",
            "param_aliases": ["qwen_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name"],
        },
    ]
    policy["text_encoder_policy"] = text_policy

    model_req = _mapping(policy.get("model_requirements"))
    roles = list(model_req.get("required_model_roles", []))
    roles.append({
        "role_id": "source_image_conditioning",
        "loader_scope": ["diffusion_model", "gguf"],
        "selection_policy": "portable_neo_asset_image1",
        "portable_identity_only": True,
    })
    roles.append({
        "role_id": "qwen_image_edit_mmproj",
        "loader_scope": ["gguf"],
        "selection_policy": "selected_comfy_profile_catalog",
        "portable_identity_only": True,
    })
    model_req["required_model_roles"] = roles
    policy["model_requirements"] = model_req

    policy["source_image_policy"] = {
        "normal_edit_max_sources": 3,
        "lanpaint_max_sources": 1,
        "lanpaint_canvas_role": "image1",
        "additional_sources_behavior": "preserved_in_ui_but_not_injected_into_masked_latent",
        "stitch_supported_modes": ["img2img", "edit"],
    }
    policy["execution"] = _execution_lock(
        "Phase 18 binds the exact versioned Qwen Image Edit family through source-aware conditioning, live capability checks and portable source lineage."
    )
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {
        "ok": not any(item["level"] == "error" for item in issues),
        "issues": deepcopy(issues),
        "complete": True,
    }
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy

def _placeholder_policy(
    *,
    policy_id: str,
    family: str,
    display_name: str,
    loaders: list[str],
    unresolved_reason: str,
) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id=policy_id,
            family=family,
            display_name=display_name,
            status=PLACEHOLDER_POLICY_STATE,
            supported_loaders=loaders,
            variant="future_policy",
        ),
        "loader_policies": {},
        "text_encoder_policy": {
            "text_encoder_role": None,
            "resolution_state": "unresolved",
        },
        "vae_policy": {
            "vae_role": None,
            "resolution_state": "unresolved",
        },
        "conditioning_policy": {
            "positive": {"positive_conditioning_policy": None},
            "negative": {"negative_conditioning_policy": None},
        },
        "route_defaults": {
            "crop_policy": {},
            "mask_policy": {},
            "latent_policy": {},
            "sampler_policy": {},
            "stitch_policy": {},
        },
        "lora_policy": {
            "lora_support_state": "family_policy",
            "lora_injection_strategy": "family_policy",
            "stack_source": "neo.image.lora_stack",
            "injection_point": "pre_sampler_model_transform",
            "visible_prompt_mutation": False,
        },
        "node_requirements": {
            "required_node_classes": [],
            "loader_specific_node_classes": {},
            "conditional_node_classes": {},
            "required_custom_node_packs": [],
            "authoring_only_nodes_excluded": [],
        },
        "model_requirements": {
            "required_model_roles": [],
            "optional_model_roles": [],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [],
            "final_output_port": "family_model_transform.sample_model",
        },
        "placeholder": {
            "inherits_defaults_from": None,
            "unresolved_reason": unresolved_reason,
            "requires_dedicated_family_policy": True,
            "requires_physical_validation": True,
        },
        "execution": _execution_lock(unresolved_reason),
    }
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {
        "ok": not any(item["level"] == "error" for item in issues),
        "issues": deepcopy(issues),
        "complete": False,
    }
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy





def _hidream_i1_policy() -> dict[str, Any]:
    """HiDream-I1 LanPaint image-family policy.

    Phase 21 deliberately targets the I1 diffusion-model family only. HiDream
    E1/E1.1 and O1 use different edit/checkpoint architectures and remain
    variant-gated rather than inheriting this four-encoder I1 graph.
    """
    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id="lanpaint.hidream_i1.v1",
            family="hidream",
            display_name="HiDream-I1 LanPaint Inpaint",
            status=COMPLETE_POLICY_STATE,
            supported_loaders=["diffusion_model", "gguf"],
            variant="hidream_i1_quad_clip_crop_stitch_v1",
        ),
        "family_variant": {
            "id": "HiDream-I1",
            "role": "image_generation",
            "supported_profiles": ["full", "dev", "fast"],
            "blocked_variants": ["HiDream-E1", "HiDream-E1.1", "HiDream-O1"],
            "source_authority": "official_comfy_hidream_i1_workflow",
        },
        "loader_policies": {
            "diffusion_model": {
                "model_loader_role": "diffusion_model",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UNETLoader"],
                "preferred_node_class": "UNETLoader",
                "model_input_keys": {"UNETLoader": "unet_name"},
                "default_inputs": {"weight_dtype": "default"},
                "output_type": "MODEL",
            },
            "gguf": {
                "model_loader_role": "gguf_unet",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UnetLoaderGGUF", "LoaderGGUF"],
                "preferred_node_class": "UnetLoaderGGUF",
                "model_input_keys": {"UnetLoaderGGUF": "unet_name", "LoaderGGUF": "gguf_name"},
                "default_inputs": {},
                "output_type": "MODEL",
            },
        },
        "text_encoder_policy": {
            "text_encoder_role": "hidream_i1_quadruple_text_encoder",
            "loader_role_ids": {
                "diffusion_model": "hidream_quadruple_clip_loader",
                "gguf": "hidream_quadruple_clip_loader_gguf",
            },
            "loader_node_classes": {
                "diffusion_model": ["QuadrupleCLIPLoader"],
                "gguf": ["QuadrupleCLIPLoaderGGUF"],
            },
            "preferred_node_classes": {
                "diffusion_model": "QuadrupleCLIPLoader",
                "gguf": "QuadrupleCLIPLoaderGGUF",
            },
            "node_class": "QuadrupleCLIPLoader",
            "required_clip_type": "hidream",
            "asset_slots": [
                {"slot_id": "text_encoder", "role_id": "hidream_clip_l", "param_aliases": ["hidream_clip_l", "clip_l_hidream", "text_encoder_1", "clip_name1"]},
                {"slot_id": "text_encoder_2", "role_id": "hidream_clip_g", "param_aliases": ["hidream_clip_g", "clip_g_hidream", "text_encoder_2", "clip_name2"]},
                {"slot_id": "text_encoder_3", "role_id": "hidream_t5xxl", "param_aliases": ["hidream_t5xxl", "t5xxl", "text_encoder_3", "clip_name3"]},
                {"slot_id": "text_encoder_4", "role_id": "hidream_llama_3_1_8b", "param_aliases": ["hidream_llama_3_1_8b", "llama_3_1_8b", "text_encoder_4", "clip_name4"]},
            ],
            "output_type": "CLIP",
        },
        "vae_policy": {
            "vae_role": "flux_ae_or_compatible_vae",
            "loader_role_ids": {"diffusion_model": "vae_or_ae", "gguf": "vae_or_ae"},
            "node_class": "VAELoader",
            "encode_node_class": "VAEEncode",
            "decode_node_class": "VAEDecode",
            "output_type": "VAE",
        },
        "conditioning_policy": {
            "positive": {"positive_conditioning_policy": "clip_text_encode", "node_class": "CLIPTextEncode", "source": "effective_positive_prompt"},
            "negative": {"negative_conditioning_policy": "clip_text_encode", "node_class": "CLIPTextEncode", "source": "effective_negative_prompt"},
        },
        "route_defaults": {
            "crop_policy": {"enabled": True, "context_mode": "masked_bounds", "padding_px": 128, "processing_size": {"width": 1024, "height": 1024, "multiple_of": 16}, "resize_method": "lanczos"},
            "mask_policy": {"sampling": {"expand_px": 40, "blur_radius": 28.0, "fill_holes": False, "invert": False}, "stitch": {"expand_px": 48, "blur_radius": 9.0, "fill_holes": False, "invert": False}},
            "latent_policy": {"latent_format": "sd3", "noise_mask_required": True, "differential_diffusion": "not_used", "model_sampling_patch": "ModelSamplingSD3", "sd3_shift": 6.0},
            "sampler_policy": {"steps": 28, "cfg": 1.0, "sampler_name": "lcm", "scheduler": "normal", "denoise": 1.0, "lanpaint_thinking_steps": 5, "prompt_mode": "image_first", "inpainting_mode": "image", "family_semantics_locked": True},
            "variant_profiles": {
                "full": {"steps": 50, "cfg": 5.0, "sd3_shift": 3.0, "sampler_name": "lcm", "scheduler": "normal"},
                "dev": {"steps": 28, "cfg": 1.0, "sd3_shift": 6.0, "sampler_name": "lcm", "scheduler": "normal"},
                "fast": {"steps": 16, "cfg": 1.0, "sd3_shift": 3.0, "sampler_name": "lcm", "scheduler": "normal"},
            },
            "stitch_policy": {"enabled": True, "restore_crop_size": True, "composite_into_source": True, "preserve_source_dimensions": True, "resize_method": "lanczos", "composite_node_role": "source_space_masked_composite"},
        },
        "lora_policy": {
            "lora_support_state": "experimental",
            "lora_injection_strategy": "model_and_clip",
            "stack_source": "neo.image.lora_stack",
            "injection_point": "post_hidream_loaders_pre_sd3_sampling_and_conditioning",
            "loader_node_class": "LoraLoader",
            "allow_multiple": True,
            "strength_fields": ["strength_model", "strength_clip"],
            "clip_strength_supported": True,
            "trigger_word_policy": "canonical_prompt_extension",
            "visible_prompt_mutation": False,
        },
        "node_requirements": {
            "required_node_classes": ["CLIPTextEncode", "VAELoader", "CropByMask", "ImageResizeKJv2", "GrowMaskWithBlur", "VAEEncode", "SetLatentNoiseMask", "ModelSamplingSD3", "LanPaint_KSampler", "VAEDecode", "ImageCompositeMasked"],
            "loader_specific_node_classes": {"diffusion_model": ["UNETLoader", "QuadrupleCLIPLoader"], "gguf": ["UnetLoaderGGUF|LoaderGGUF", "QuadrupleCLIPLoaderGGUF"]},
            "conditional_node_classes": {"lora_stack_enabled": ["LoraLoader"]},
            "required_custom_node_packs": [
                {"pack_id": "LanPaint", "provides": ["LanPaint_KSampler"]},
                {"pack_id": "comfyui-inpainteasy", "provides": ["CropByMask"]},
                {"pack_id": "ComfyUI-KJNodes", "provides": ["ImageResizeKJv2", "GrowMaskWithBlur"]},
                {"pack_id": "ComfyUI-GGUF", "provides": ["UnetLoaderGGUF", "QuadrupleCLIPLoaderGGUF"], "loader_scope": ["gguf"]},
            ],
            "authoring_only_nodes_excluded": [],
        },
        "model_requirements": {
            "required_model_roles": [
                {"role_id": "hidream_i1_diffusion_model", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "hidream_clip_l", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "hidream_clip_g", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "hidream_t5xxl", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "hidream_llama_3_1_8b", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "flux_ae_or_compatible_vae", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
            ],
            "optional_model_roles": [{"role_id": "hidream_i1_compatible_lora", "enabled_when": "neo.image.lora_stack has enabled rows", "portable_identity_only": True}],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [{"transform_id": "model_sampling_sd3", "node_class": "ModelSamplingSD3", "inputs": {"shift": 6.0}}],
            "final_output_port": "family_model_transform.sample_model",
        },
        "execution": _execution_lock("Phase 21 binds HiDream-I1 through four text encoders, ModelSamplingSD3, LanPaint crop/stitch, live capability validation and replay-safe assets."),
    }
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {"ok": not any(item["level"] == "error" for item in issues), "issues": deepcopy(issues), "complete": True}
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy

def _flux1_policy() -> dict[str, Any]:
    """Build the Flux.1 Dev/Schnell LanPaint policy.

    Dev and Schnell share the same Flux.1 dual-encoder architecture and LoRA
    compatibility. Runtime variant resolution owns their distinct step,
    guidance and thinking defaults without creating engine-specific routes.
    """
    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id="lanpaint.flux1.v1",
            family="flux",
            display_name="Flux.1 Dev / Schnell LanPaint Inpaint",
            status=COMPLETE_POLICY_STATE,
            supported_loaders=["diffusion_model", "gguf"],
            variant="flux1_dev_schnell",
        ),
        "loader_policies": {
            "diffusion_model": {
                "model_loader_role": "diffusion_model",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UNETLoader"],
                "preferred_node_class": "UNETLoader",
                "model_input_keys": {"UNETLoader": "unet_name"},
                "default_inputs": {"weight_dtype": "default"},
                "output_type": "MODEL",
            },
            "gguf": {
                "model_loader_role": "gguf_unet",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UnetLoaderGGUF", "LoaderGGUF"],
                "preferred_node_class": "UnetLoaderGGUF",
                "model_input_keys": {"UnetLoaderGGUF": "unet_name", "LoaderGGUF": "gguf_name"},
                "default_inputs": {},
                "output_type": "MODEL",
            },
        },
        "text_encoder_policy": {
            "text_encoder_role": "flux1_dual_text_encoder",
            "loader_role_ids": {"diffusion_model": "dual_clip_loader", "gguf": "gguf_clip_dual_loader"},
            "loader_node_classes": {"diffusion_model": ["DualCLIPLoader"], "gguf": ["DualCLIPLoaderGGUF"]},
            "preferred_node_classes": {"diffusion_model": "DualCLIPLoader", "gguf": "DualCLIPLoaderGGUF"},
            "node_class": "DualCLIPLoader",
            "required_clip_type": "flux",
            "asset_slots": [
                {"slot_id": "text_encoder", "role_id": "flux_t5xxl", "param_aliases": ["text_encoder_1", "text_encoder_primary", "clip_name1", "t5xxl"]},
                {"slot_id": "text_encoder_2", "role_id": "flux_clip_l", "param_aliases": ["text_encoder_2", "text_encoder_secondary", "clip_name2", "clip_l"]},
            ],
            "output_type": "CLIP",
        },
        "vae_policy": {
            "vae_role": "flux_ae",
            "loader_role_ids": {"diffusion_model": "vae_or_ae", "gguf": "vae_or_ae"},
            "node_class": "VAELoader",
            "encode_node_class": "VAEEncode",
            "decode_node_class": "VAEDecode",
            "output_type": "VAE",
        },
        "conditioning_policy": {
            "positive": {
                "positive_conditioning_policy": "clip_text_encode_then_flux_guidance",
                "node_class": "CLIPTextEncode",
                "guidance_node_class": "FluxGuidance",
                "source": "effective_positive_prompt",
            },
            "negative": {
                "negative_conditioning_policy": "zero_out_positive_conditioning",
                "node_class": "ConditioningZeroOut",
                "source": "unguided_positive_conditioning",
                "user_negative_prompt_effect": "not_sampled_cfg1_flux_policy",
            },
        },
        "route_defaults": {
            "crop_policy": {"enabled": True, "context_mode": "masked_bounds", "padding_px": 128, "processing_size": {"width": 1024, "height": 1024, "multiple_of": 16}, "resize_method": "lanczos"},
            "mask_policy": {"sampling": {"expand_px": 40, "blur_radius": 28.0, "fill_holes": False, "invert": False}, "stitch": {"expand_px": 48, "blur_radius": 9.0, "fill_holes": False, "invert": False}},
            "latent_policy": {"latent_format": "flux1", "noise_mask_required": True, "differential_diffusion": "not_used", "model_sampling_patch": "loader_specific", "gguf_sampling_transform": "ModelSamplingFlux", "max_shift": 1.15, "base_shift": 0.5},
            "sampler_policy": {"steps": 30, "cfg": 1.0, "flux_guidance": 1.5, "sampler_name": "euler", "scheduler": "simple", "denoise": 1.0, "lanpaint_thinking_steps": 5, "prompt_mode": "image_first", "inpainting_mode": "image", "family_semantics_locked": True},
            "variant_profiles": {
                "dev": {"steps": 30, "cfg": 1.0, "flux_guidance": 1.5, "lanpaint_thinking_steps": 5, "distilled": True, "guidance_range": [1.0, 2.0]},
                "schnell": {"steps": 4, "cfg": 1.0, "flux_guidance": 1.0, "lanpaint_thinking_steps": 2, "distilled": True, "guidance_range": [0.0, 1.5]},
            },
            "stitch_policy": {"enabled": True, "restore_crop_size": True, "composite_into_source": True, "preserve_source_dimensions": True, "resize_method": "lanczos", "composite_node_role": "source_space_masked_composite"},
        },
        "lora_policy": {
            "lora_support_state": "experimental",
            "lora_injection_strategy": "model_and_clip",
            "stack_source": "neo.image.lora_stack",
            "injection_point": "post_flux_loaders_pre_guidance_and_sampling",
            "loader_node_class": "LoraLoader",
            "allow_multiple": True,
            "strength_fields": ["strength_model", "strength_clip"],
            "clip_strength_supported": True,
            "trigger_word_policy": "canonical_prompt_extension",
            "visible_prompt_mutation": False,
        },
        "node_requirements": {
            "required_node_classes": ["CLIPTextEncode", "FluxGuidance", "ConditioningZeroOut", "VAELoader", "CropByMask", "ImageResizeKJv2", "GrowMaskWithBlur", "VAEEncode", "SetLatentNoiseMask", "LanPaint_KSampler", "VAEDecode", "ImageCompositeMasked"],
            "loader_specific_node_classes": {"diffusion_model": ["UNETLoader", "DualCLIPLoader"], "gguf": ["UnetLoaderGGUF|LoaderGGUF", "DualCLIPLoaderGGUF", "ModelSamplingFlux"]},
            "conditional_node_classes": {"lora_stack_enabled": ["LoraLoader"]},
            "required_custom_node_packs": [
                {"pack_id": "LanPaint", "provides": ["LanPaint_KSampler"]},
                {"pack_id": "comfyui-inpainteasy", "provides": ["CropByMask"]},
                {"pack_id": "ComfyUI-KJNodes", "provides": ["ImageResizeKJv2", "GrowMaskWithBlur"]},
            ],
            "authoring_only_nodes_excluded": [],
        },
        "model_requirements": {
            "required_model_roles": [
                {"role_id": "flux1_diffusion_model", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "flux_t5xxl", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "flux_clip_l", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "flux_ae", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
            ],
            "optional_model_roles": [{"role_id": "flux1_compatible_lora", "enabled_when": "neo.image.lora_stack has enabled rows", "portable_identity_only": True}],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [
                {"transform_id": "neo_lora_stack", "required": False, "enabled_when": "lora_stack_has_enabled_rows", "input_type": "MODEL+CLIP", "output_type": "MODEL+CLIP", "strategy": "model_and_clip"},
                {"transform_id": "model_sampling_flux", "required": True, "loader_scope": ["gguf"], "input_type": "MODEL", "output_type": "MODEL", "inputs": {"max_shift": 1.15, "base_shift": 0.5}},
            ],
            "final_output_port": "family_model_transform.sample_model",
        },
        "execution": _execution_lock("Phase 16 binds Flux.1 Dev/Schnell only after exact dual-encoder, model-loader and variant validation."),
    }
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {"ok": not any(item["level"] == "error" for item in issues), "issues": deepcopy(issues), "complete": True}
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy



def _flux2_policy(*, family: str, display_name: str, policy_id: str) -> dict[str, Any]:
    """Build a Flux.2 Dev or Klein LanPaint policy without Flux.1 inheritance."""
    is_dev = family == "flux2_dev"
    text_role = "flux2_mistral3_text_encoder" if is_dev else "flux2_qwen3_text_encoder"
    text_loaders = {
        "diffusion_model": ["CLIPLoader"],
        # Dev GGUF deliberately keeps the proven native Mistral encoder path.
        "gguf": ["CLIPLoader"] if is_dev else ["CLIPLoader", "CLIPLoaderGGUF"],
    }
    preferred = {"diffusion_model": "CLIPLoader", "gguf": "CLIPLoader" if is_dev else "CLIPLoaderGGUF"}
    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id=policy_id,
            family=family,
            display_name=display_name,
            status=COMPLETE_POLICY_STATE,
            supported_loaders=["diffusion_model", "gguf"],
            variant="flux2_dev" if is_dev else "flux2_klein_base_distilled",
        ),
        "loader_policies": {
            "diffusion_model": {
                "model_loader_role": "diffusion_model",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UNETLoader"],
                "preferred_node_class": "UNETLoader",
                "model_input_keys": {"UNETLoader": "unet_name"},
                "default_inputs": {"weight_dtype": "default"},
                "output_type": "MODEL",
            },
            "gguf": {
                "model_loader_role": "gguf_unet",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["UnetLoaderGGUF", "LoaderGGUF"],
                "preferred_node_class": "UnetLoaderGGUF",
                "model_input_keys": {"UnetLoaderGGUF": "unet_name", "LoaderGGUF": "gguf_name"},
                "default_inputs": {},
                "output_type": "MODEL",
            },
        },
        "text_encoder_policy": {
            "text_encoder_role": text_role,
            "loader_role_ids": {"diffusion_model": text_role, "gguf": text_role},
            "loader_node_classes": text_loaders,
            "preferred_node_classes": preferred,
            "node_class": "CLIPLoader",
            "required_clip_type": "flux2",
            "asset_slots": [{
                "slot_id": "text_encoder",
                "role_id": text_role,
                "param_aliases": (["mistral3_text_encoder", "text_encoder_1", "text_encoder_primary", "clip_name"] if is_dev else ["qwen3_text_encoder", "text_encoder_1", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_primary", "clip_name"]),
            }],
            "output_type": "CLIP",
            "gguf_encoder_policy": "native_mistral_required" if is_dev else "native_or_gguf_qwen3",
        },
        "vae_policy": {
            "vae_role": "flux2_vae",
            "loader_role_ids": {"diffusion_model": "vae_or_ae", "gguf": "vae_or_ae"},
            "node_class": "VAELoader",
            "encode_node_class": "VAEEncode",
            "decode_node_class": "VAEDecode",
            "output_type": "VAE",
        },
        "conditioning_policy": {
            "positive": {
                "positive_conditioning_policy": "clip_text_encode_then_flux_guidance",
                "node_class": "CLIPTextEncode",
                "guidance_node_class": "FluxGuidance",
                "source": "effective_positive_prompt",
            },
            "negative": {
                "negative_conditioning_policy": "zero_out_positive_conditioning",
                "node_class": "ConditioningZeroOut",
                "source": "unguided_positive_conditioning",
                "user_negative_prompt_effect": "not_sampled_cfg1_flux2_policy",
            },
        },
        "route_defaults": {
            "crop_policy": {"enabled": True, "context_mode": "masked_bounds", "padding_px": 128, "processing_size": {"width": 1024, "height": 1024, "multiple_of": 16}, "resize_method": "lanczos"},
            "mask_policy": {"sampling": {"expand_px": 40, "blur_radius": 28.0, "fill_holes": False, "invert": False}, "stitch": {"expand_px": 48, "blur_radius": 9.0, "fill_holes": False, "invert": False}},
            "latent_policy": {"latent_format": "flux2", "latent_node_class": "EmptyFlux2LatentImage", "noise_mask_required": True, "differential_diffusion": "not_used", "model_sampling_patch": "not_used"},
            "sampler_policy": {
                "steps": 28 if is_dev else 4,
                "cfg": 1.0,
                "flux_guidance": 4.0 if is_dev else 1.0,
                "sampler_name": "euler",
                "scheduler": "simple",
                "denoise": 1.0,
                "lanpaint_thinking_steps": 5 if is_dev else 2,
                "prompt_mode": "image_first",
                "inpainting_mode": "image",
                "family_semantics_locked": True,
            },
            "variant_profiles": ({
                "dev": {"steps": 28, "cfg": 1.0, "flux_guidance": 4.0, "lanpaint_thinking_steps": 5, "text_encoder_architecture": "mistral3_small_flux2", "guidance_range": [1.0, 6.0]},
            } if is_dev else {
                "klein_4b": {"steps": 50, "cfg": 1.0, "flux_guidance": 4.0, "lanpaint_thinking_steps": 3, "distilled": False, "encoder_scale": "4b", "guidance_range": [1.0, 6.0]},
                "klein_9b": {"steps": 50, "cfg": 1.0, "flux_guidance": 4.0, "lanpaint_thinking_steps": 3, "distilled": False, "encoder_scale": "8b", "guidance_range": [1.0, 6.0]},
                "klein_4b_distilled": {"steps": 4, "cfg": 1.0, "flux_guidance": 1.0, "lanpaint_thinking_steps": 2, "distilled": True, "encoder_scale": "4b", "guidance_range": [0.0, 2.0]},
                "klein_9b_distilled": {"steps": 4, "cfg": 1.0, "flux_guidance": 1.0, "lanpaint_thinking_steps": 2, "distilled": True, "encoder_scale": "8b", "guidance_range": [0.0, 2.0]},
            }),
            "stitch_policy": {"enabled": True, "restore_crop_size": True, "composite_into_source": True, "preserve_source_dimensions": True, "resize_method": "lanczos", "composite_node_role": "source_space_masked_composite"},
        },
        "lora_policy": {
            "lora_support_state": "experimental",
            "lora_injection_strategy": "model_and_clip",
            "stack_source": "neo.image.lora_stack",
            "injection_point": "post_flux2_loaders_pre_guidance_and_sampling",
            "loader_node_class": "LoraLoader",
            "allow_multiple": True,
            "strength_fields": ["strength_model", "strength_clip"],
            "clip_strength_supported": True,
            "trigger_word_policy": "canonical_prompt_extension",
            "visible_prompt_mutation": False,
        },
        "node_requirements": {
            "required_node_classes": ["CLIPTextEncode", "FluxGuidance", "ConditioningZeroOut", "VAELoader", "CropByMask", "ImageResizeKJv2", "GrowMaskWithBlur", "VAEEncode", "SetLatentNoiseMask", "LanPaint_KSampler", "VAEDecode", "ImageCompositeMasked"],
            "loader_specific_node_classes": {"diffusion_model": ["UNETLoader", "CLIPLoader"], "gguf": ["UnetLoaderGGUF|LoaderGGUF", "CLIPLoader"] if is_dev else ["UnetLoaderGGUF|LoaderGGUF", "CLIPLoader|CLIPLoaderGGUF"]},
            "conditional_node_classes": {"lora_stack_enabled": ["LoraLoader"]},
            "required_custom_node_packs": [
                {"pack_id": "LanPaint", "provides": ["LanPaint_KSampler"]},
                {"pack_id": "comfyui-inpainteasy", "provides": ["CropByMask"]},
                {"pack_id": "ComfyUI-KJNodes", "provides": ["ImageResizeKJv2", "GrowMaskWithBlur"]},
            ],
            "authoring_only_nodes_excluded": ["EmptyFlux2LatentImage"],
        },
        "model_requirements": {
            "required_model_roles": [
                {"role_id": "flux2_dev_diffusion_model" if is_dev else "flux2_klein_diffusion_model", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": text_role, "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "flux2_vae", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
            ],
            "optional_model_roles": [{"role_id": "flux2_compatible_lora", "enabled_when": "neo.image.lora_stack has enabled rows", "portable_identity_only": True}],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [{"transform_id": "neo_lora_stack", "required": False, "enabled_when": "lora_stack_has_enabled_rows", "input_type": "MODEL+CLIP", "output_type": "MODEL+CLIP", "strategy": "model_and_clip"}],
            "final_output_port": "family_model_transform.sample_model",
        },
        "execution": _execution_lock("Phase 17 binds Flux.2 Dev/Klein only after exact family, encoder, loader and variant validation."),
    }
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {"ok": not any(item["level"] == "error" for item in issues), "issues": deepcopy(issues), "complete": True}
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy

def _sd_checkpoint_policy(
    *,
    policy_id: str,
    family: str,
    display_name: str,
    steps: int,
    cfg: float,
    processing_size: int,
) -> dict[str, Any]:
    """Build a classic Stable Diffusion checkpoint LanPaint policy.

    CheckpointLoaderSimple owns MODEL, CLIP and VAE outputs.  The adapter keeps
    those outputs as separate graph roles while requiring only one portable
    checkpoint asset selection.
    """
    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id=policy_id, family=family, display_name=display_name,
            status=COMPLETE_POLICY_STATE, supported_loaders=["checkpoint"],
            variant="checkpoint_crop_stitch_v1",
        ),
        "loader_policies": {
            "checkpoint": {
                "model_loader_role": "checkpoint",
                "binding_state": "family_policy_declared",
                "accepted_node_classes": ["CheckpointLoaderSimple"],
                "preferred_node_class": "CheckpointLoaderSimple",
                "model_input_keys": {"CheckpointLoaderSimple": "ckpt_name"},
                "default_inputs": {},
                "output_type": "MODEL+CLIP+VAE",
                "output_ports": {"model": 0, "clip": 1, "vae": 2},
            }
        },
        "text_encoder_policy": {
            "text_encoder_role": "checkpoint_clip",
            "loader_role_ids": {"checkpoint": "checkpoint"},
            "loader_node_classes": {"checkpoint": ["CheckpointLoaderSimple"]},
            "preferred_node_classes": {"checkpoint": "CheckpointLoaderSimple"},
            "node_class": "CheckpointLoaderSimple",
            "required_clip_type": "stable_diffusion",
            "bundled_with_model": True,
            "output_port": 1, "output_type": "CLIP",
        },
        "vae_policy": {
            "vae_role": "checkpoint_vae",
            "loader_role_ids": {"checkpoint": "checkpoint"},
            "node_class": "CheckpointLoaderSimple",
            "bundled_with_model": True,
            "output_port": 2,
            "encode_node_class": "VAEEncode", "decode_node_class": "VAEDecode",
            "output_type": "VAE",
        },
        "conditioning_policy": {
            "positive": {"positive_conditioning_policy": "clip_text_encode", "node_class": "CLIPTextEncode", "source": "effective_positive_prompt"},
            "negative": {"negative_conditioning_policy": "clip_text_encode", "node_class": "CLIPTextEncode", "source": "effective_negative_prompt", "user_negative_prompt_effect": "sampled"},
        },
        "route_defaults": {
            "crop_policy": {"enabled": True, "context_mode": "masked_bounds", "padding_px": 96, "processing_size": {"width": processing_size, "height": processing_size, "multiple_of": 8}, "resize_method": "lanczos"},
            "mask_policy": {
                "sampling": {"expand_px": 32, "blur_radius": 24.0, "fill_holes": False, "invert": False},
                "stitch": {"expand_px": 40, "blur_radius": 8.0, "fill_holes": False, "invert": False},
            },
            "latent_policy": {"latent_format": family, "noise_mask_required": True, "differential_diffusion": "not_used", "model_sampling_patch": "none"},
            "sampler_policy": {"steps": steps, "cfg": cfg, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "lanpaint_thinking_steps": 5, "prompt_mode": "image_first", "inpainting_mode": "image", "family_semantics_locked": True},
            "stitch_policy": {"enabled": True, "restore_crop_size": True, "composite_into_source": True, "preserve_source_dimensions": True, "resize_method": "lanczos", "composite_node_role": "source_space_masked_composite"},
        },
        "lora_policy": {
            "lora_support_state": "experimental", "lora_injection_strategy": "model_and_clip",
            "stack_source": "neo.image.lora_stack", "injection_point": "post_checkpoint_loader_pre_conditioning",
            "loader_node_class": "LoraLoader", "allow_multiple": True,
            "strength_fields": ["strength_model", "strength_clip"], "clip_strength_supported": True,
            "trigger_word_policy": "canonical_prompt_extension", "visible_prompt_mutation": False,
        },
        "node_requirements": {
            "required_node_classes": ["CheckpointLoaderSimple", "CLIPTextEncode", "CropByMask", "ImageResizeKJv2", "GrowMaskWithBlur", "VAEEncode", "SetLatentNoiseMask", "LanPaint_KSampler", "VAEDecode", "ImageCompositeMasked"],
            "loader_specific_node_classes": {"checkpoint": ["CheckpointLoaderSimple"]},
            "conditional_node_classes": {"lora_stack_enabled": ["LoraLoader"]},
            "required_custom_node_packs": [
                {"pack_id": "LanPaint", "provides": ["LanPaint_KSampler"]},
                {"pack_id": "comfyui-inpainteasy", "provides": ["CropByMask"]},
                {"pack_id": "ComfyUI-KJNodes", "provides": ["ImageResizeKJv2", "GrowMaskWithBlur"]},
            ],
            "authoring_only_nodes_excluded": [],
        },
        "model_requirements": {
            "required_model_roles": [{"role_id": "checkpoint", "loader_scope": ["checkpoint"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True}],
            "optional_model_roles": [{"role_id": f"{family}_compatible_lora", "enabled_when": "neo.image.lora_stack has enabled rows", "portable_identity_only": True}],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [{"transform_id": "neo_lora_stack", "required": False, "enabled_when": "lora_stack_has_enabled_rows", "input_type": "MODEL+CLIP", "output_type": "MODEL+CLIP", "strategy": "model_and_clip"}],
            "final_output_port": "checkpoint_loader.model",
        },
        "execution": _execution_lock("Phase 15 binds this checkpoint family only through the universal LanPaint adapter and live Comfy capability gate."),
    }
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {"ok": not any(item["level"] == "error" for item in issues), "issues": deepcopy(issues), "complete": True}
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy


def _sd35_policy() -> dict[str, Any]:
    """Build the SD 3.5 split/GGUF LanPaint policy with triple text encoders."""
    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "authority": AUTHORITY,
        "identity": _base_policy_identity(
            policy_id="lanpaint.sd35.v1", family="sd35", display_name="Stable Diffusion 3.5 LanPaint Inpaint",
            status=COMPLETE_POLICY_STATE, supported_loaders=["diffusion_model", "gguf"],
            variant="sd3_crop_stitch_v1",
        ),
        "loader_policies": {
            "diffusion_model": {"model_loader_role": "diffusion_model", "binding_state": "family_policy_declared", "accepted_node_classes": ["UNETLoader"], "preferred_node_class": "UNETLoader", "model_input_keys": {"UNETLoader": "unet_name"}, "default_inputs": {"weight_dtype": "default"}, "output_type": "MODEL"},
            "gguf": {"model_loader_role": "gguf_unet", "binding_state": "family_policy_declared", "accepted_node_classes": ["UnetLoaderGGUF"], "preferred_node_class": "UnetLoaderGGUF", "model_input_keys": {"UnetLoaderGGUF": "unet_name"}, "default_inputs": {}, "output_type": "MODEL"},
        },
        "text_encoder_policy": {
            "text_encoder_role": "sd3_triple_text_encoder",
            "loader_role_ids": {"diffusion_model": "sd3_triple_clip_loader", "gguf": "sd3_triple_clip_loader_gguf"},
            "loader_node_classes": {"diffusion_model": ["TripleCLIPLoader"], "gguf": ["TripleCLIPLoaderGGUF"]},
            "preferred_node_classes": {"diffusion_model": "TripleCLIPLoader", "gguf": "TripleCLIPLoaderGGUF"},
            "node_class": "TripleCLIPLoader", "required_clip_type": "sd3",
            "asset_slots": [
                {"slot_id": "text_encoder_1", "role_id": "sd3_clip_l", "param_aliases": ["text_encoder_1", "clip_l", "clip_name1"]},
                {"slot_id": "text_encoder_2", "role_id": "sd3_clip_g", "param_aliases": ["text_encoder_2", "clip_g", "clip_name2"]},
                {"slot_id": "text_encoder_3", "role_id": "sd3_t5xxl", "param_aliases": ["text_encoder_3", "t5xxl", "clip_name3"]},
            ],
            "output_type": "CLIP",
        },
        "vae_policy": {"vae_role": "sd3_vae", "loader_role_ids": {"diffusion_model": "vae_or_ae", "gguf": "vae_or_ae"}, "node_class": "VAELoader", "encode_node_class": "VAEEncode", "decode_node_class": "VAEDecode", "output_type": "VAE"},
        "conditioning_policy": {
            "positive": {"positive_conditioning_policy": "clip_text_encode", "node_class": "CLIPTextEncode", "source": "effective_positive_prompt"},
            "negative": {"negative_conditioning_policy": "clip_text_encode", "node_class": "CLIPTextEncode", "source": "effective_negative_prompt", "user_negative_prompt_effect": "sampled"},
        },
        "route_defaults": {
            "crop_policy": {"enabled": True, "context_mode": "masked_bounds", "padding_px": 128, "processing_size": {"width": 1024, "height": 1024, "multiple_of": 16}, "resize_method": "lanczos"},
            "mask_policy": {"sampling": {"expand_px": 40, "blur_radius": 28.0, "fill_holes": False, "invert": False}, "stitch": {"expand_px": 48, "blur_radius": 9.0, "fill_holes": False, "invert": False}},
            "latent_policy": {"latent_format": "sd3", "noise_mask_required": True, "differential_diffusion": "not_used", "model_sampling_patch": "ModelSamplingSD3", "sd3_shift": 3.0},
            "sampler_policy": {"steps": 28, "cfg": 4.5, "sampler_name": "euler", "scheduler": "sgm_uniform", "denoise": 1.0, "lanpaint_thinking_steps": 5, "prompt_mode": "image_first", "inpainting_mode": "image", "family_semantics_locked": True},
            "stitch_policy": {"enabled": True, "restore_crop_size": True, "composite_into_source": True, "preserve_source_dimensions": True, "resize_method": "lanczos", "composite_node_role": "source_space_masked_composite"},
        },
        "lora_policy": {"lora_support_state": "experimental", "lora_injection_strategy": "model_and_clip", "stack_source": "neo.image.lora_stack", "injection_point": "pre_sd3_sampling_and_conditioning", "loader_node_class": "LoraLoader", "allow_multiple": True, "strength_fields": ["strength_model", "strength_clip"], "clip_strength_supported": True, "trigger_word_policy": "canonical_prompt_extension", "visible_prompt_mutation": False},
        "node_requirements": {
            "required_node_classes": ["CLIPTextEncode", "VAELoader", "CropByMask", "ImageResizeKJv2", "GrowMaskWithBlur", "VAEEncode", "SetLatentNoiseMask", "ModelSamplingSD3", "LanPaint_KSampler", "VAEDecode", "ImageCompositeMasked"],
            "loader_specific_node_classes": {"diffusion_model": ["UNETLoader", "TripleCLIPLoader"], "gguf": ["UnetLoaderGGUF", "TripleCLIPLoaderGGUF"]},
            "conditional_node_classes": {"lora_stack_enabled": ["LoraLoader"]},
            "required_custom_node_packs": [{"pack_id": "LanPaint", "provides": ["LanPaint_KSampler"]}, {"pack_id": "comfyui-inpainteasy", "provides": ["CropByMask"]}, {"pack_id": "ComfyUI-KJNodes", "provides": ["ImageResizeKJv2", "GrowMaskWithBlur"]}],
            "authoring_only_nodes_excluded": [],
        },
        "model_requirements": {
            "required_model_roles": [
                {"role_id": "sd35_diffusion_model", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "sd3_clip_l", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "sd3_clip_g", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "sd3_t5xxl", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
                {"role_id": "sd3_vae", "loader_scope": ["diffusion_model", "gguf"], "selection_policy": "selected_comfy_profile_catalog", "portable_identity_only": True},
            ],
            "optional_model_roles": [{"role_id": "sd35_compatible_lora", "enabled_when": "neo.image.lora_stack has enabled rows", "portable_identity_only": True}],
        },
        "model_transform_pipeline": {
            "ordered_transforms": [
                {"transform_id": "neo_lora_stack", "required": False, "enabled_when": "lora_stack_has_enabled_rows", "input_type": "MODEL+CLIP", "output_type": "MODEL+CLIP", "strategy": "model_and_clip"},
                {"transform_id": "model_sampling_sd3", "required": True, "input_type": "MODEL", "output_type": "MODEL", "inputs": {"shift": 3.0}},
            ],
            "final_output_port": "family_model_transform.sample_model",
        },
        "execution": _execution_lock("Phase 15 binds SD 3.5 only after exact loader, triple-encoder and ModelSamplingSD3 capability validation."),
    }
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {"ok": not any(item["level"] == "error" for item in issues), "issues": deepcopy(issues), "complete": True}
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy

def validate_lanpaint_family_policy(raw: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    policy = _mapping(raw)
    issues: list[dict[str, Any]] = []

    def error(field: str, message: str) -> None:
        issues.append({"level": "error", "field": field, "message": message})

    def warning(field: str, message: str) -> None:
        issues.append({"level": "warning", "field": field, "message": message})

    if policy.get("schema_id") != SCHEMA_ID:
        error("schema_id", f"Expected {SCHEMA_ID}.")
    if policy.get("schema_version") != SCHEMA_VERSION:
        error("schema_version", f"Expected version {SCHEMA_VERSION}.")
    if policy.get("authority") != AUTHORITY:
        error("authority", f"Expected {AUTHORITY}.")

    for section in _REQUIRED_POLICY_SECTIONS:
        if section not in policy:
            error(section, "Required family-policy section is missing.")

    identity = _mapping(policy.get("identity"))
    if identity.get("route_family_id") != ROUTE_FAMILY_ID:
        error("identity.route_family_id", f"Expected {ROUTE_FAMILY_ID}.")
    if identity.get("mode") != MODE_ID:
        error("identity.mode", f"Expected {MODE_ID}.")
    if identity.get("engine") != ENGINE_ID:
        error("identity.engine", f"Expected {ENGINE_ID}.")
    if identity.get("policy_state") != POLICY_STATE:
        error("identity.policy_state", f"Expected {POLICY_STATE}.")
    if not identity.get("policy_id"):
        error("identity.policy_id", "Policy id is required.")
    if not identity.get("family"):
        error("identity.family", "Family id is required.")

    providers = [normalize_provider_id(item) for item in identity.get("provider_ids", [])]
    if not providers or any(item not in SUPPORTED_LOCAL_PROVIDERS for item in providers):
        error("identity.provider_ids", "LanPaint Phase 3 policies may target ComfyUI and ComfyUI Portable only.")
    loaders = [normalize_loader_id(item) for item in identity.get("loader_ids", [])]
    if not loaders or any(item not in SUPPORTED_LOADER_IDS for item in loaders):
        error("identity.loader_ids", "LanPaint loader ids must be checkpoint, diffusion_model or gguf.")

    status = identity.get("status")
    if status not in {COMPLETE_POLICY_STATE, PLACEHOLDER_POLICY_STATE}:
        error("identity.status", "Family policy status is invalid.")

    execution = _mapping(policy.get("execution"))
    if execution.get("enabled") is not False or execution.get("selectable") is not False:
        error("execution", "Phase 3 family policies must not enable or expose a route.")
    if execution.get("state") != POLICY_STATE:
        error("execution.state", f"Expected {POLICY_STATE}.")
    if execution.get("compiler_id") is not None or execution.get("workflow_type") is not None:
        error("execution", "Compiler and workflow bindings are forbidden in Phase 3.")

    if status == COMPLETE_POLICY_STATE:
        family = identity.get("family")
        loader_policies = _mapping(policy.get("loader_policies"))
        declared_loaders = set(loaders)
        if set(loader_policies) != declared_loaders:
            error("loader_policies", "Complete LanPaint policies must define exactly their declared loader branches.")
        text = _mapping(policy.get("text_encoder_policy"))
        if not text.get("required_clip_type"):
            error("text_encoder_policy.required_clip_type", "Complete policies must declare the Comfy CLIP type.")
        vae = _mapping(policy.get("vae_policy"))
        if not vae.get("vae_role"):
            error("vae_policy.vae_role", "Complete policies must declare a VAE/AE role.")
        sampler = _mapping(_mapping(policy.get("route_defaults")).get("sampler_policy"))
        for key in ("steps", "cfg", "sampler_name", "scheduler", "denoise", "lanpaint_thinking_steps", "prompt_mode"):
            if sampler.get(key) in (None, ""):
                error(f"route_defaults.sampler_policy.{key}", "Complete policies must own every sampler default.")
        lora = _mapping(policy.get("lora_policy"))
        ideogram_lora_block = family == "ideogram4" and lora.get("lora_support_state") == "blocked_unproven_dual_model_patch" and lora.get("lora_injection_strategy") == "none"
        if not ideogram_lora_block and (lora.get("lora_support_state") != "experimental" or lora.get("lora_injection_strategy") not in {"model_only", "model_and_clip"}):
            error("lora_policy", "Complete LanPaint LoRA policies must be experimental and declare model_only or model_and_clip, except an explicit dual-model fail-closed policy.")
        required_nodes = set(_mapping(policy.get("node_requirements")).get("required_node_classes", []))
        reusable_sampler = "LanPaint_SamplerCustomAdvanced" if family == "ideogram4" else "LanPaint_KSampler"
        for node in ("CropByMask", "ImageResizeKJv2", "GrowMaskWithBlur", "SetLatentNoiseMask", reusable_sampler, "ImageCompositeMasked"):
            if node not in required_nodes:
                error("node_requirements.required_node_classes", f"Missing reusable LanPaint role node: {node}.")
        if family == "krea2_turbo":
            if text.get("node_class") != "CLIPLoader" or text.get("required_clip_type") != "krea2":
                error("text_encoder_policy", "Krea 2 Turbo requires CLIPLoader(type=krea2).")
            if "qwen3vl_4b_gguf" not in text.get("rejected_asset_classifications", []):
                error("text_encoder_policy", "Krea 2 GGUF must reject GGUF text encoders.")
            if vae.get("vae_role") != "qwen_image_vae":
                error("vae_policy", "Krea 2 Turbo requires the Qwen Image VAE role.")
            negative = _mapping(_mapping(policy.get("conditioning_policy")).get("negative"))
            if negative.get("negative_conditioning_policy") != "zero_out_positive_conditioning":
                error("conditioning_policy.negative", "Krea 2 Turbo must use zeroed negative conditioning.")
            if "DifferentialDiffusionAdvanced" not in required_nodes:
                error("node_requirements.required_node_classes", "Krea 2 Turbo requires DifferentialDiffusionAdvanced.")
            if lora.get("lora_injection_strategy") != "model_only":
                error("lora_policy", "Krea 2 Turbo must remain model-only.")
        elif family in {"qwen_image", "qwen_image_edit_2509", "qwen_image_edit_2511", "z_image", "z_image_turbo"}:
            if "ModelSamplingAuraFlow" not in required_nodes:
                error("node_requirements.required_node_classes", "AuraFlow family policies require ModelSamplingAuraFlow.")
            if "DifferentialDiffusionAdvanced" in required_nodes:
                error("node_requirements.required_node_classes", "Qwen/Z-Image LanPaint must not inherit Krea DifferentialDiffusionAdvanced.")
            if lora.get("lora_injection_strategy") != "model_and_clip":
                error("lora_policy", "Qwen/Z-Image LanPaint uses the existing model+CLIP LoRA path.")
            if family in {"qwen_image_edit_2509", "qwen_image_edit_2511"}:
                positive = _mapping(_mapping(policy.get("conditioning_policy")).get("positive"))
                if positive.get("positive_conditioning_policy") != "qwen_image_edit_plus_single_canvas":
                    error("conditioning_policy.positive", "Qwen Image Edit LanPaint must use source-aware single-canvas edit conditioning.")
                if not any(str(node).startswith("TextEncodeQwenImageEditPlus") for node in required_nodes):
                    error("node_requirements.required_node_classes", "Qwen Image Edit LanPaint requires a TextEncodeQwenImageEditPlus-compatible node.")
        elif family == "flux":
            if set(loader_policies) != {"diffusion_model", "gguf"}:
                error("loader_policies", "Flux.1 LanPaint requires split safetensors and GGUF branches.")
            if text.get("required_clip_type") != "flux":
                error("text_encoder_policy.required_clip_type", "Flux.1 requires DualCLIPLoader(type=flux).")
            if "FluxGuidance" not in required_nodes or "ConditioningZeroOut" not in required_nodes:
                error("node_requirements.required_node_classes", "Flux.1 requires FluxGuidance and zeroed negative conditioning.")
            if lora.get("lora_injection_strategy") != "model_and_clip":
                error("lora_policy", "Flux.1 LanPaint uses model+CLIP LoRA.")
        elif family in {"flux2_dev", "flux2_klein"}:
            if set(loader_policies) != {"diffusion_model", "gguf"}:
                error("loader_policies", "Flux.2 LanPaint requires split safetensors and GGUF model branches.")
            if text.get("required_clip_type") != "flux2":
                error("text_encoder_policy.required_clip_type", "Flux.2 requires CLIPLoader(type=flux2).")
            if "FluxGuidance" not in required_nodes or "ConditioningZeroOut" not in required_nodes:
                error("node_requirements.required_node_classes", "Flux.2 requires FluxGuidance and zeroed negative conditioning.")
            if lora.get("lora_injection_strategy") != "model_and_clip":
                error("lora_policy", "Flux.2 LanPaint uses model+CLIP LoRA.")
            if family == "flux2_dev" and _mapping(text.get("loader_node_classes")).get("gguf") != ["CLIPLoader"]:
                error("text_encoder_policy.loader_node_classes", "Flux.2 Dev GGUF must keep the proven native Mistral3 CLIPLoader path.")
        elif family in {"sdxl", "sd15"}:
            if set(loader_policies) != {"checkpoint"}:
                error("loader_policies", "SDXL and SD 1.5 LanPaint use checkpoint/safetensors only.")
            if "CheckpointLoaderSimple" not in required_nodes:
                error("node_requirements.required_node_classes", "Classic SD checkpoint LanPaint requires CheckpointLoaderSimple.")
            if lora.get("lora_injection_strategy") != "model_and_clip":
                error("lora_policy", "Classic SD checkpoint LanPaint uses model+CLIP LoRA.")
        elif family == "sd35":
            if set(loader_policies) != {"diffusion_model", "gguf"}:
                error("loader_policies", "SD 3.5 LanPaint requires split safetensors and GGUF branches.")
            if "ModelSamplingSD3" not in required_nodes:
                error("node_requirements.required_node_classes", "SD 3.5 LanPaint requires ModelSamplingSD3.")
            if lora.get("lora_injection_strategy") != "model_and_clip":
                error("lora_policy", "SD 3.5 LanPaint uses model+CLIP LoRA.")
        elif family == "hidream":
            if set(loader_policies) != {"diffusion_model", "gguf"}:
                error("loader_policies", "HiDream-I1 LanPaint requires split safetensors and GGUF branches.")
            if text.get("required_clip_type") != "hidream":
                error("text_encoder_policy.required_clip_type", "HiDream-I1 requires the four-encoder hidream CLIP contract.")
            slots = list(text.get("asset_slots") or [])
            if len(slots) != 4:
                error("text_encoder_policy.asset_slots", "HiDream-I1 requires exactly four text-encoder asset slots.")
            if "ModelSamplingSD3" not in required_nodes:
                error("node_requirements.required_node_classes", "HiDream-I1 LanPaint requires ModelSamplingSD3.")
            if lora.get("lora_injection_strategy") != "model_and_clip":
                error("lora_policy", "HiDream-I1 LanPaint uses model+CLIP LoRA.")
        elif family == "anima":
            if text.get("required_clip_type") != "stable_diffusion":
                error("text_encoder_policy.required_clip_type", "Anima requires CLIPLoader(type=stable_diffusion) for Qwen3 0.6B.")
            if vae.get("vae_role") != "qwen_image_vae":
                error("vae_policy.vae_role", "Anima requires the Qwen Image VAE.")
            if lora.get("lora_injection_strategy") != "model_only":
                error("lora_policy", "Anima uses model-only LoRA patching.")
        elif family == "ideogram4":
            if text.get("required_clip_type") != "ideogram4":
                error("text_encoder_policy.required_clip_type", "Ideogram 4 requires CLIPLoader(type=ideogram4).")
            for node in ("Ideogram4Scheduler", "DualModelGuider", "LanPaint_SamplerCustomAdvanced"):
                if node not in required_nodes:
                    error("node_requirements.required_node_classes", f"Ideogram 4 requires {node}.")
            if "LanPaint_KSampler" in required_nodes:
                error("node_requirements.required_node_classes", "Ideogram 4 must not inherit the basic LanPaint_KSampler route.")
        else:
            error("identity.family", "This complete family policy is not approved by the current LanPaint onboarding phase.")
        if _mapping(policy.get("placeholder")):
            error("placeholder", "Complete policies must not publish placeholder inheritance metadata.")
    else:
        placeholder = _mapping(policy.get("placeholder"))
        if not placeholder.get("requires_dedicated_family_policy"):
            error("placeholder", "Unresolved families must require a dedicated family policy.")
        if placeholder.get("inherits_defaults_from") is not None:
            error("placeholder.inherits_defaults_from", "Placeholders must not inherit Krea 2 or another family's defaults.")
        if _mapping(policy.get("loader_policies")):
            error("loader_policies", "Placeholder families must not declare concrete loader bindings.")
        defaults = _mapping(policy.get("route_defaults"))
        if any(_mapping(value) for value in defaults.values()):
            error("route_defaults", "Placeholder families must not publish Krea-derived defaults.")
        if not placeholder.get("unresolved_reason"):
            warning("placeholder.unresolved_reason", "Placeholder should explain why the family remains unresolved.")

    return issues


def normalize_lanpaint_family_policy(raw: Mapping[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    policy = deepcopy(_mapping(raw))
    identity = _mapping(policy.get("identity"))
    identity["family"] = normalize_family_id(identity.get("family"))
    identity["provider_ids"] = sorted({normalize_provider_id(item) for item in identity.get("provider_ids", []) if normalize_provider_id(item)})
    identity["loader_ids"] = sorted({normalize_loader_id(item) for item in identity.get("loader_ids", []) if normalize_loader_id(item)})
    identity["variant"] = _slug(identity.get("variant")) or "default"
    policy["identity"] = identity
    issues = validate_lanpaint_family_policy(policy)
    policy["validation"] = {
        "ok": not any(item["level"] == "error" for item in issues),
        "issues": deepcopy(issues),
        "complete": identity.get("status") == COMPLETE_POLICY_STATE,
    }
    policy["policy_fingerprint"] = lanpaint_family_policy_fingerprint(policy)
    return policy, issues




def _anima_policy() -> dict[str, Any]:
    policy: dict[str, Any] = {
        "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION, "authority": AUTHORITY,
        "identity": _base_policy_identity(policy_id="lanpaint.anima.v1", family="anima", display_name="Anima Base v1 LanPaint Inpaint", status=COMPLETE_POLICY_STATE, supported_loaders=["diffusion_model", "gguf"], variant="anima_base_crop_stitch_v1"),
        "family_variant": {"id": "Anima Base v1", "role": "image_generation", "optional_acceleration": "anima_turbo_lora_v0_2", "source_authority": "official_comfy_anima_workflow"},
        "loader_policies": {
            "diffusion_model": {"model_loader_role":"diffusion_model","binding_state":"family_policy_declared","accepted_node_classes":["UNETLoader"],"preferred_node_class":"UNETLoader","model_input_keys":{"UNETLoader":"unet_name"},"default_inputs":{"weight_dtype":"default"},"output_type":"MODEL"},
            "gguf": {"model_loader_role":"gguf_unet","binding_state":"family_policy_declared","accepted_node_classes":["UnetLoaderGGUF","LoaderGGUF"],"preferred_node_class":"UnetLoaderGGUF","model_input_keys":{"UnetLoaderGGUF":"unet_name","LoaderGGUF":"gguf_name"},"default_inputs":{},"output_type":"MODEL"},
        },
        "text_encoder_policy": {"text_encoder_role":"anima_qwen3_06b_text_encoder","loader_role_ids":{"diffusion_model":"anima_qwen3_06b_text_encoder","gguf":"anima_qwen3_06b_text_encoder"},"loader_node_classes":{"diffusion_model":["CLIPLoader"],"gguf":["CLIPLoader"]},"preferred_node_classes":{"diffusion_model":"CLIPLoader","gguf":"CLIPLoader"},"node_class":"CLIPLoader","required_clip_type":"stable_diffusion","default_device":"default","output_type":"CLIP"},
        "vae_policy": {"vae_role":"qwen_image_vae","loader_role_ids":{"diffusion_model":"qwen_image_vae","gguf":"qwen_image_vae"},"node_class":"VAELoader","encode_node_class":"VAEEncode","decode_node_class":"VAEDecode","output_type":"VAE"},
        "conditioning_policy": {"positive":{"positive_conditioning_policy":"clip_text_encode","node_class":"CLIPTextEncode","source":"effective_positive_prompt"},"negative":{"negative_conditioning_policy":"clip_text_encode","node_class":"CLIPTextEncode","source":"effective_negative_prompt","user_negative_prompt_effect":"sampled"}},
        "route_defaults": {"crop_policy":{"enabled":True,"context_mode":"masked_bounds","padding_px":112,"processing_size":{"width":1024,"height":1024,"multiple_of":16},"resize_method":"lanczos"},"mask_policy":{"sampling":{"expand_px":32,"blur_radius":24.0,"fill_holes":False,"invert":False},"stitch":{"expand_px":40,"blur_radius":8.0,"fill_holes":False,"invert":False}},"latent_policy":{"latent_format":"qwen_image","noise_mask_required":True,"differential_diffusion":"not_used","model_sampling_patch":"none"},"sampler_policy":{"steps":30,"cfg":4.0,"sampler_name":"euler","scheduler":"simple","denoise":1.0,"lanpaint_thinking_steps":5,"prompt_mode":"image_first","inpainting_mode":"image","sampler_contract":"basic","family_semantics_locked":True},"stitch_policy":{"enabled":True,"restore_crop_size":True,"composite_into_source":True,"preserve_source_dimensions":True,"resize_method":"lanczos","composite_node_role":"source_space_masked_composite"}},
        "lora_policy": {"lora_support_state":"experimental","lora_injection_strategy":"model_only","stack_source":"neo.image.lora_stack","injection_point":"post_model_loader_pre_sampler","loader_node_class":"LoraLoaderModelOnly","allow_multiple":True,"strength_fields":["strength_model"],"clip_strength_supported":False,"visible_prompt_mutation":False},
        "node_requirements": {"required_node_classes":["CLIPLoader","VAELoader","CLIPTextEncode","CropByMask","ImageResizeKJv2","GrowMaskWithBlur","VAEEncode","SetLatentNoiseMask","LanPaint_KSampler","VAEDecode","ImageCompositeMasked"],"loader_specific_node_classes":{"diffusion_model":["UNETLoader"],"gguf":["UnetLoaderGGUF","LoaderGGUF"]},"conditional_node_classes":{"lora_stack_enabled":["LoraLoaderModelOnly"]},"required_custom_node_packs":[{"pack_id":"LanPaint","provides":["LanPaint_KSampler"]},{"pack_id":"comfyui-inpainteasy","provides":["CropByMask"]},{"pack_id":"ComfyUI-KJNodes","provides":["ImageResizeKJv2","GrowMaskWithBlur"]}],"authoring_only_nodes_excluded":["EmptyLatentImage"]},
        "model_requirements": {"required_model_roles":[{"role_id":"anima_diffusion_model","loader_scope":["diffusion_model","gguf"],"selection_policy":"selected_comfy_profile_catalog","portable_identity_only":True},{"role_id":"anima_qwen3_06b_text_encoder","loader_scope":["diffusion_model","gguf"],"selection_policy":"selected_comfy_profile_catalog","portable_identity_only":True},{"role_id":"qwen_image_vae","loader_scope":["diffusion_model","gguf"],"selection_policy":"selected_comfy_profile_catalog","portable_identity_only":True}],"optional_model_roles":[{"role_id":"anima_compatible_model_only_lora","enabled_when":"neo.image.lora_stack has enabled rows","portable_identity_only":True}]},
        "model_transform_pipeline": {"ordered_transforms":[{"transform_id":"neo_lora_stack","required":False,"enabled_when":"lora_stack_has_enabled_rows","input_type":"MODEL","output_type":"MODEL","strategy":"model_only"}],"final_output_port":"family_model_transform.sample_model"},
        "execution": _execution_lock("Phase 22 binds Anima only through the family-aware LanPaint adapter and live Comfy capability gate."),
    }
    issues=validate_lanpaint_family_policy(policy); policy["validation"]={"ok":not any(i["level"]=="error" for i in issues),"issues":deepcopy(issues),"complete":True}; policy["policy_fingerprint"]=lanpaint_family_policy_fingerprint(policy); return policy


def _ideogram4_policy() -> dict[str, Any]:
    policy: dict[str, Any] = {
        "schema_id":SCHEMA_ID,"schema_version":SCHEMA_VERSION,"authority":AUTHORITY,
        "identity":_base_policy_identity(policy_id="lanpaint.ideogram4.v1",family="ideogram4",display_name="Ideogram 4 LanPaint Advanced Inpaint",status=COMPLETE_POLICY_STATE,supported_loaders=["diffusion_model","gguf"],variant="ideogram4_dual_model_custom_advanced_v1"),
        "family_variant":{"id":"Ideogram 4","role":"image_generation","dual_model_required":True,"source_authority":"official_comfy_ideogram4_workflow_and_upstream_lanpaint_example"},
        "loader_policies":{
            "diffusion_model":{"model_loader_role":"ideogram4_main_model","binding_state":"family_policy_declared","accepted_node_classes":["UNETLoader"],"preferred_node_class":"UNETLoader","model_input_keys":{"UNETLoader":"unet_name"},"default_inputs":{"weight_dtype":"default"},"output_type":"MODEL"},
            "gguf":{"model_loader_role":"ideogram4_main_model_gguf","binding_state":"family_policy_declared","accepted_node_classes":["UnetLoaderGGUF","LoaderGGUF"],"preferred_node_class":"UnetLoaderGGUF","model_input_keys":{"UnetLoaderGGUF":"unet_name","LoaderGGUF":"gguf_name"},"default_inputs":{},"output_type":"MODEL"},
        },
        "text_encoder_policy":{"text_encoder_role":"ideogram4_qwen3_vl_text_encoder","loader_role_ids":{"diffusion_model":"ideogram4_qwen3_vl_text_encoder","gguf":"ideogram4_qwen3_vl_text_encoder"},"loader_node_classes":{"diffusion_model":["CLIPLoader"],"gguf":["CLIPLoader"]},"preferred_node_classes":{"diffusion_model":"CLIPLoader","gguf":"CLIPLoader"},"node_class":"CLIPLoader","required_clip_type":"ideogram4","default_device":"default","output_type":"CLIP"},
        "vae_policy":{"vae_role":"flux2_vae","loader_role_ids":{"diffusion_model":"flux2_vae","gguf":"flux2_vae"},"node_class":"VAELoader","encode_node_class":"VAEEncode","decode_node_class":"VAEDecode","output_type":"VAE"},
        "conditioning_policy":{"positive":{"positive_conditioning_policy":"clip_text_encode","node_class":"CLIPTextEncode","source":"effective_positive_prompt"},"negative":{"negative_conditioning_policy":"zero_out_positive_conditioning","node_class":"ConditioningZeroOut","source":"positive_conditioning","user_negative_prompt_effect":"not_sampled"}},
        "route_defaults":{"crop_policy":{"enabled":True,"context_mode":"masked_bounds","padding_px":112,"processing_size":{"width":1024,"height":1024,"multiple_of":16},"resize_method":"lanczos"},"mask_policy":{"sampling":{"expand_px":32,"blur_radius":24.0,"fill_holes":False,"invert":False},"stitch":{"expand_px":40,"blur_radius":8.0,"fill_holes":False,"invert":False}},"latent_policy":{"latent_format":"flux2","noise_mask_required":True,"differential_diffusion":"not_used","model_sampling_patch":"ideogram4_scheduler"},"sampler_policy":{"steps":20,"cfg":4.0,"sampler_name":"euler","scheduler":"ideogram4","denoise":1.0,"lanpaint_thinking_steps":5,"prompt_mode":"image_first","inpainting_mode":"image","sampler_contract":"custom_advanced","lanpaint_lambda":16.0,"lanpaint_step_size":0.2,"lanpaint_beta":1.0,"lanpaint_friction":15.0,"lanpaint_early_stop":1,"family_semantics_locked":True},"stitch_policy":{"enabled":True,"restore_crop_size":True,"composite_into_source":True,"preserve_source_dimensions":True,"resize_method":"lanczos","composite_node_role":"source_space_masked_composite"}},
        "lora_policy":{"lora_support_state":"blocked_unproven_dual_model_patch","lora_injection_strategy":"none","stack_source":"neo.image.lora_stack","injection_point":"none","loader_node_class":"LoraLoaderModelOnly","allow_multiple":False,"strength_fields":[],"clip_strength_supported":False,"visible_prompt_mutation":False},
        "node_requirements":{"required_node_classes":["CLIPLoader","VAELoader","CLIPTextEncode","ConditioningZeroOut","CropByMask","ImageResizeKJv2","GrowMaskWithBlur","VAEEncode","SetLatentNoiseMask","RandomNoise","KSamplerSelect","Ideogram4Scheduler","DualModelGuider","LanPaint_SamplerCustomAdvanced","VAEDecode","ImageCompositeMasked"],"loader_specific_node_classes":{"diffusion_model":["UNETLoader"],"gguf":["UnetLoaderGGUF","LoaderGGUF"]},"conditional_node_classes":{},"required_custom_node_packs":[{"pack_id":"LanPaint","provides":["LanPaint_SamplerCustomAdvanced"]},{"pack_id":"comfyui-inpainteasy","provides":["CropByMask"]},{"pack_id":"ComfyUI-KJNodes","provides":["ImageResizeKJv2","GrowMaskWithBlur"]}],"authoring_only_nodes_excluded":["EmptyFlux2LatentImage","SamplerCustomAdvanced"]},
        "model_requirements":{"required_model_roles":[{"role_id":"ideogram4_main_model","loader_scope":["diffusion_model","gguf"],"selection_policy":"selected_comfy_profile_catalog","portable_identity_only":True},{"role_id":"ideogram4_unconditional_model","loader_scope":["diffusion_model","gguf"],"selection_policy":"selected_comfy_profile_catalog","portable_identity_only":True},{"role_id":"ideogram4_qwen3_vl_text_encoder","loader_scope":["diffusion_model","gguf"],"selection_policy":"selected_comfy_profile_catalog","portable_identity_only":True},{"role_id":"flux2_vae","loader_scope":["diffusion_model","gguf"],"selection_policy":"selected_comfy_profile_catalog","portable_identity_only":True}],"optional_model_roles":[]},
        "model_transform_pipeline":{"ordered_transforms":[],"final_output_port":"dual_model_guider"},
        "execution":_execution_lock("Phase 22 binds Ideogram 4 only through its dual-model LanPaint custom-advanced adapter and live capability gate."),
    }
    issues=validate_lanpaint_family_policy(policy); policy["validation"]={"ok":not any(i["level"]=="error" for i in issues),"issues":deepcopy(issues),"complete":True}; policy["policy_fingerprint"]=lanpaint_family_policy_fingerprint(policy); return policy

def lanpaint_family_policy_registry() -> dict[str, Any]:
    policies = [
        _flux1_policy(),
        _flux2_policy(family="flux2_dev", display_name="Flux.2 Dev LanPaint Inpaint", policy_id="lanpaint.flux2_dev.v1"),
        _flux2_policy(family="flux2_klein", display_name="Flux.2 Klein LanPaint Inpaint", policy_id="lanpaint.flux2_klein.v1"),
        _sd_checkpoint_policy(policy_id="lanpaint.sdxl.v1", family="sdxl", display_name="SDXL LanPaint Inpaint", steps=28, cfg=7.0, processing_size=1024),
        _sd_checkpoint_policy(policy_id="lanpaint.sd15.v1", family="sd15", display_name="SD 1.5 LanPaint Inpaint", steps=25, cfg=7.0, processing_size=768),
        _sd35_policy(),
        _hidream_i1_policy(),
        _anima_policy(),
        _ideogram4_policy(),
        _placeholder_policy(
            policy_id="lanpaint.hunyuan_image.video_hold.v1",
            family="hunyuan_image",
            display_name="Hunyuan Image LanPaint (Held)",
            loaders=["diffusion_model", "gguf"],
            unresolved_reason="The upstream LanPaint Hunyuan T2I example uses HunyuanVideo/T2V architecture. It is held for the Video workspace. The separate HunyuanImage family needs its own proven image-model LanPaint graph before Image activation.",
        ),
        _krea2_turbo_policy(),
        _placeholder_policy(
            policy_id="lanpaint.krea2_base.placeholder.v1",
            family="krea2",
            display_name="Krea 2 RAW/Base LanPaint Inpaint",
            loaders=["gguf", "diffusion_model"],
            unresolved_reason="Krea 2 RAW/Base requires its own sampling, negative-conditioning and physical validation policy; it must not inherit Turbo values.",
        ),
        _aura_family_policy(
            policy_id="lanpaint.qwen_image.v1", family="qwen_image",
            display_name="Qwen Image LanPaint Inpaint", clip_type="qwen_image",
            text_encoder_role="qwen_image_text_encoder", vae_role="qwen_image_vae",
            model_role="qwen_image_diffusion_model", steps=20, cfg=4.0, aura_shift=3.1,
            zero_negative=False, latent_format="qwen_image",
        ),
        _placeholder_policy(
            policy_id="lanpaint.qwen_image_edit.placeholder.v1",
            family="qwen_image_edit",
            display_name="Qwen Image Edit (Unversioned) LanPaint Inpaint",
            loaders=["gguf", "diffusion_model"],
            unresolved_reason="Unversioned Qwen Image Edit remains blocked. Select the exact 2509 or 2511 family so conditioning, model detection and replay stay version-safe.",
        ),
        _qwen_edit_policy(
            policy_id="lanpaint.qwen_image_edit_2509.v1",
            family="qwen_image_edit_2509",
            display_name="Qwen Image Edit 2509 LanPaint Inpaint",
            model_role="qwen_image_edit_2509_diffusion_model",
        ),
        _qwen_edit_policy(
            policy_id="lanpaint.qwen_image_edit_2511.v1",
            family="qwen_image_edit_2511",
            display_name="Qwen Image Edit 2511 LanPaint Inpaint",
            model_role="qwen_image_edit_2511_diffusion_model",
        ),
        _aura_family_policy(
            policy_id="lanpaint.z_image.v2", family="z_image",
            display_name="Z-Image Base LanPaint Inpaint", clip_type="lumina2",
            text_encoder_role="qwen3_4b_text_encoder", vae_role="flux_ae_or_compatible_vae",
            model_role="z_image_diffusion_model", steps=35, cfg=3.5, aura_shift=3.0,
            zero_negative=False, latent_format="z_image",
            variant="z_image_lanpaint_base_crop_stitch_v2", family_variant="base",
            lanpaint_thinking_steps=3,
            stability_policy={
                "profile_id": "z_image_lanpaint_base_cautious_v1",
                "risk": "iterative_divergence",
                "recommended_thinking_steps": 3,
                "maximum_default_thinking_steps": 3,
                "lanpaint_step_size": "sampler_internal_default; start_small_when_advanced_control_is_available",
                "source": "upstream_lanpaint_z_image_base_guidance",
            },
        ),
        _placeholder_policy(
            policy_id="lanpaint.z_image_base.placeholder.v1",
            family="z_image_base",
            display_name="Z-Image Base LanPaint Inpaint",
            loaders=["gguf", "diffusion_model"],
            unresolved_reason="Z-Image Base requires independent base-model sampling and LoRA validation; it must not inherit Z-Image Turbo or Krea 2 values.",
        ),
        _aura_family_policy(
            policy_id="lanpaint.z_image_turbo.v2", family="z_image_turbo",
            display_name="Z-Image Turbo LanPaint Inpaint", clip_type="lumina2",
            text_encoder_role="qwen3_4b_text_encoder", vae_role="flux_ae_or_compatible_vae",
            model_role="z_image_turbo_diffusion_model", steps=9, cfg=1.0, aura_shift=3.0,
            zero_negative=True, latent_format="z_image",
            variant="z_image_turbo_lanpaint_crop_stitch_v2", family_variant="turbo",
            lanpaint_thinking_steps=5,
            stability_policy={
                "profile_id": "z_image_turbo_distilled_v1",
                "risk": "distilled_low_step_sensitivity",
                "recommended_thinking_steps": 5,
                "maximum_default_thinking_steps": 5,
                "source": "upstream_lanpaint_z_image_example",
            },
        ),
    ]
    policies = sorted(policies, key=lambda item: item["identity"]["policy_id"])
    registry = {
        "schema_id": REGISTRY_SCHEMA_ID,
        "schema_version": 1,
        "authority": AUTHORITY,
        "route_family_id": ROUTE_FAMILY_ID,
        "policy_state": POLICY_STATE,
        "policies": policies,
        "execution": _execution_lock("The Phase 3 registry resolves policy metadata only and does not expose runnable routes."),
    }
    registry["registry_fingerprint"] = hashlib.sha256(
        json.dumps(
            [{"policy_id": item["identity"]["policy_id"], "fingerprint": item["policy_fingerprint"]} for item in policies],
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return registry


def get_lanpaint_family_policy(
    family: Any,
    *,
    loader: Any | None = None,
    provider_id: Any | None = None,
) -> dict[str, Any] | None:
    family_id = normalize_family_id(family)
    loader_id = normalize_loader_id(loader) if loader not in (None, "") else ""
    provider = normalize_provider_id(provider_id) if provider_id not in (None, "") else ""
    for policy in lanpaint_family_policy_registry()["policies"]:
        identity = policy["identity"]
        if identity["family"] != family_id:
            continue
        if loader_id and loader_id not in identity["loader_ids"]:
            return None
        if provider and provider not in identity["provider_ids"]:
            return None
        return deepcopy(policy)
    return None


def _fill_missing(target: dict[str, Any], defaults: Mapping[str, Any], *, authority: str) -> None:
    for key, value in defaults.items():
        if isinstance(value, Mapping):
            child = target.get(key)
            if not isinstance(child, Mapping):
                child = {}
            child_dict = dict(child)
            _fill_missing(child_dict, value, authority=authority)
            target[key] = child_dict
        elif target.get(key) in (None, "", "family_policy"):
            target[key] = deepcopy(value)
    target["resolved_from_policy"] = authority


def resolve_lanpaint_family_policy(
    route_contract: Mapping[str, Any] | None,
) -> dict[str, Any]:
    contract, contract_issues = normalize_lanpaint_route_contract(route_contract)
    identity = contract["identity"]
    policy = get_lanpaint_family_policy(
        identity["family"],
        loader=identity["loader"],
        provider_id=identity["provider_id"],
    )

    result: dict[str, Any] = {
        "schema_id": RESOLUTION_SCHEMA_ID,
        "schema_version": 1,
        "authority": AUTHORITY,
        "route_family_id": ROUTE_FAMILY_ID,
        "route_key": identity["route_key"],
        "resolution_state": "missing_policy",
        "policy": None,
        "resolved_contract": contract,
        "issues": deepcopy(contract_issues),
        "execution": _execution_lock("Family policy resolution does not enable workflow compilation."),
    }
    if policy is None:
        result["issues"].append({
            "level": "error",
            "field": "family_policy",
            "message": "No LanPaint family policy matches the selected provider, family and loader.",
        })
        return result

    result["policy"] = policy
    status = policy["identity"]["status"]
    if status != COMPLETE_POLICY_STATE:
        result["resolution_state"] = PLACEHOLDER_POLICY_STATE
        result["resolved_contract"]["family_policy"] = {
            "policy_id": policy["identity"]["policy_id"],
            "policy_schema_id": SCHEMA_ID,
            "policy_fingerprint": policy["policy_fingerprint"],
            "owned_fields": list(result["resolved_contract"]["family_policy"].get("owned_fields", [])),
            "resolution_state": PLACEHOLDER_POLICY_STATE,
            "inherits_defaults_from": None,
        }
        result["resolved_contract"]["execution"] = _execution_lock(policy["placeholder"]["unresolved_reason"])
        result["resolved_contract"]["execution"]["state"] = EXECUTION_STATE
        result["resolved_contract"]["contract_fingerprint"] = lanpaint_contract_fingerprint(result["resolved_contract"])
        return result

    policy_id = policy["identity"]["policy_id"]
    resolved = deepcopy(contract)
    defaults = policy["route_defaults"]
    _fill_missing(resolved["crop_policy"], defaults["crop_policy"], authority=policy_id)
    _fill_missing(resolved["mask_policy"], defaults["mask_policy"], authority=policy_id)
    _fill_missing(resolved["latent_policy"], defaults["latent_policy"], authority=policy_id)
    _fill_missing(resolved["sampler_policy"], defaults["sampler_policy"], authority=policy_id)
    _fill_missing(resolved["stitch_policy"], defaults["stitch_policy"], authority=policy_id)

    resolved["lora_policy"].update({
        "support_state": policy["lora_policy"]["lora_support_state"],
        "injection_strategy": policy["lora_policy"]["lora_injection_strategy"],
        "injection_point": policy["lora_policy"]["injection_point"],
        "allow_multiple": policy["lora_policy"]["allow_multiple"],
        "visible_prompt_mutation": False,
        "family_policy_required": False,
        "resolved_from_policy": policy_id,
    })
    required_nodes = list(policy["node_requirements"]["required_node_classes"])
    loader_nodes = list(policy["node_requirements"]["loader_specific_node_classes"].get(identity["loader"], []))
    resolved["capability_requirements"]["required_node_classes"] = required_nodes + loader_nodes
    resolved["capability_requirements"]["required_model_roles"] = [
        item["role_id"] for item in policy["model_requirements"]["required_model_roles"]
    ]
    resolved["capability_requirements"]["family_policy_resolution_required"] = False
    resolved["family_policy"] = {
        "policy_id": policy_id,
        "policy_schema_id": SCHEMA_ID,
        "policy_fingerprint": policy["policy_fingerprint"],
        "owned_fields": list(contract["family_policy"].get("owned_fields", [])),
        "resolution_state": "resolved_policy_only",
        "loader_policy": deepcopy(policy["loader_policies"][identity["loader"]]),
        "text_encoder_policy": deepcopy(policy["text_encoder_policy"]),
        "vae_policy": deepcopy(policy["vae_policy"]),
        "conditioning_policy": deepcopy(policy["conditioning_policy"]),
        "model_transform_pipeline": deepcopy(policy["model_transform_pipeline"]),
    }
    resolved["validation"] = {
        "ok": contract["validation"]["ok"] and policy["validation"]["ok"],
        "errors": deepcopy(contract["validation"].get("errors", [])),
        "warnings": deepcopy(contract["validation"].get("warnings", [])),
        "unresolved_family_policy_fields": [],
        "execution_ready": False,
        "policy_resolution_state": "resolved_policy_only",
    }
    resolved["execution"] = {
        "enabled": False,
        "state": EXECUTION_STATE,
        "compiler_id": None,
        "workflow_type": None,
        "selectable": False,
        "reason": "Krea 2 Turbo family policy is resolved, but Phase 3 does not bind provider nodes or compile a workflow.",
    }
    resolved["contract_fingerprint"] = lanpaint_contract_fingerprint(resolved)

    result["resolution_state"] = "resolved_policy_only"
    result["resolved_contract"] = resolved
    return result


__all__ = [
    "AUTHORITY",
    "COMPLETE_POLICY_STATE",
    "PLACEHOLDER_POLICY_STATE",
    "POLICY_STATE",
    "REGISTRY_SCHEMA_ID",
    "RESOLUTION_SCHEMA_ID",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "get_lanpaint_family_policy",
    "lanpaint_family_policy_fingerprint",
    "lanpaint_family_policy_registry",
    "normalize_lanpaint_family_policy",
    "resolve_lanpaint_family_policy",
    "validate_lanpaint_family_policy",
]
