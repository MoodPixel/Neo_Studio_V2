from __future__ import annotations

from copy import deepcopy
from typing import Any

# P8.5 keeps the rollout decision inside the High-Res Lab extension.
# Base routes may be available, but the extension only becomes selectable when
# a route profile explicitly says its sampler/model/VAE anchors are safe.

ACTIVE_BASE_STATES = {"available", "experimental_available"}
KNOWN_STATES = {
    "available",
    "experimental_available",
    "implementation_target",
    "planned_gated",
    "provider_gated",
    "unsupported",
}

BACKEND_ALIASES = {
    "comfyui_portable": "comfyui",
    "comfy": "comfyui",
    "automatic1111": "a1111",
    "sd_webui": "a1111",
}

MODE_ALIASES = {
    "generate": "txt2img",
    "text_to_image": "txt2img",
    "image_to_image": "img2img",
}

DEFAULT_PROFILE: dict[str, Any] = {
    "profile_id": "generic_implementation_target",
    "family_group": "generic",
    "model_kind": "unknown",
    "rollout_state": "implementation_target",
    "default_strategy": "forge_pixel_refine",
    "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
    "blocked_strategies": ["ultimate_sd_upscale"],
    "snap_multiple": 8,
    "negative_prompt_policy": "preserve_if_present",
    "cfg_policy": "preserve_route_default",
    "sampler_policy": "preserve_base_sampler",
    "latent_policy": "warn_low_denoise",
    "anchor_policy": "detect_model_sampler_vae_decode",
    "hidden_controls": [],
    "notes": [
        "P8.5 route-profile contract is present, but this family/loader still needs a dedicated High-Res Lab enablement pass.",
        "Do not fallback to SDXL or another family if anchors are not safe.",
    ],
}

# Existing stable routes keep working. Target families get explicit route
# profiles and are promoted one family pass at a time inside this extension.
_ROUTE_PROFILES: dict[tuple[str, str], dict[str, Any]] = {
    ("sdxl", "checkpoint"): {
        **DEFAULT_PROFILE,
        "profile_id": "sd_checkpoint_highres",
        "family_group": "sd",
        "model_kind": "sdxl_checkpoint",
        "rollout_state": "available",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "ultimate_sd_upscale"],
        "blocked_strategies": [],
        "snap_multiple": 8,
        "negative_prompt_policy": "preserve",
        "cfg_policy": "sd_checkpoint_cfg",
        "notes": ["Classic SD checkpoint route has proven sampler/model/VAE anchors for High-Res Lab."],
    },
    ("sd15", "checkpoint"): {
        **DEFAULT_PROFILE,
        "profile_id": "sd_checkpoint_highres",
        "family_group": "sd",
        "model_kind": "sd15_checkpoint",
        "rollout_state": "available",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "ultimate_sd_upscale"],
        "blocked_strategies": [],
        "snap_multiple": 8,
        "negative_prompt_policy": "preserve",
        "cfg_policy": "sd_checkpoint_cfg",
        "notes": ["Classic SD checkpoint route has proven sampler/model/VAE anchors for High-Res Lab."],
    },
    ("flux", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux_components_highres",
        "family_group": "flux",
        "model_kind": "flux_components",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "preserve_base_sampler_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_flux",
        "anchor_policy": "detect_flux_model_sampler_vae_decode",
        "hidden_controls": ["cfg", "ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 12, "denoise": 0.26, "cfg_policy": "preserve_base_sampler_cfg"},
            "detail_push": {"steps": 16, "denoise": 0.34, "cfg_policy": "preserve_base_sampler_cfg"},
            "latent_rebuild": {"steps": 18, "denoise": 0.48, "cfg_policy": "preserve_base_sampler_cfg"},
        },
        "notes": [
            "P8.1 promotes Flux 1 Safetensors / Components for High-Res Lab using Flux-native sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled; Ultimate SD Upscale remains blocked for Flux because it expects SD-style conditioning semantics.",
            "High-Res Lab preserves Flux sampler CFG from the base workflow; FluxGuidance remains the real guidance control.",
        ],
    },
    ("flux", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux_gguf_highres",
        "family_group": "flux",
        "model_kind": "flux_gguf",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "preserve_base_sampler_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_flux",
        "anchor_policy": "detect_flux_gguf_model_sampler_vae_decode",
        "hidden_controls": ["cfg", "ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 10, "denoise": 0.24, "cfg_policy": "preserve_base_sampler_cfg"},
            "detail_push": {"steps": 14, "denoise": 0.32, "cfg_policy": "preserve_base_sampler_cfg"},
            "latent_rebuild": {"steps": 18, "denoise": 0.46, "cfg_policy": "preserve_base_sampler_cfg"},
        },
        "notes": [
            "P8.1 promotes Flux 1 GGUF for High-Res Lab using GGUF-native sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled; Ultimate SD Upscale remains blocked for Flux GGUF.",
            "High-Res Lab must preserve the provider-owned neutral KSampler CFG for Flux GGUF; Flux guidance stays in CLIPTextEncodeFlux.",
        ],
    },
    ("flux2_klein", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux2_klein_components_highres",
        "family_group": "flux2_klein",
        "model_kind": "flux2_klein_components",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "preserve_base_sampler_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_flux2_klein",
        "anchor_policy": "detect_flux2_klein_model_sampler_vae_decode",
        "hidden_controls": ["cfg", "ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 10, "denoise": 0.24, "cfg_policy": "preserve_base_sampler_cfg"},
            "detail_push": {"steps": 14, "denoise": 0.32, "cfg_policy": "preserve_base_sampler_cfg"},
            "latent_rebuild": {"steps": 18, "denoise": 0.46, "cfg_policy": "preserve_base_sampler_cfg"},
        },
        "notes": [
            "P8.3 promotes Flux Klein Safetensors / Components for High-Res Lab using Klein-native sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled; SD-style Ultimate SD Upscale remains blocked for Flux Klein.",
            "High-Res Lab preserves the base Klein sampler CFG instead of injecting SDXL-style CFG; Klein guidance remains owned by the compiled route.",
        ],
    },
    ("flux2_klein", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux2_klein_gguf_highres",
        "family_group": "flux2_klein",
        "model_kind": "flux2_klein_gguf",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "preserve_base_sampler_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_flux2_klein",
        "anchor_policy": "detect_flux2_klein_gguf_model_sampler_vae_decode",
        "hidden_controls": ["cfg", "ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 8, "denoise": 0.22, "cfg_policy": "preserve_base_sampler_cfg"},
            "detail_push": {"steps": 12, "denoise": 0.30, "cfg_policy": "preserve_base_sampler_cfg"},
            "latent_rebuild": {"steps": 16, "denoise": 0.44, "cfg_policy": "preserve_base_sampler_cfg"},
        },
        "notes": [
            "P8.3 promotes Flux Klein GGUF for High-Res Lab using GGUF-native Flux2/Klein sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled; SD-style Ultimate SD Upscale remains blocked for Flux Klein GGUF.",
            "High-Res Lab preserves the provider-owned KSampler CFG for Klein GGUF and never falls back to Flux 1, Qwen, ZImage, SDXL, or Flux Fill.",
        ],
    },
    ("qwen_image", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_image_components_highres",
        "family_group": "qwen",
        "model_kind": "qwen_image_components",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "qwen_reedit"],
        "blocked_strategies": ["ultimate_sd_upscale"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "qwen_safe_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_qwen",
        "anchor_policy": "detect_qwen_edit_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 10, "denoise": 0.24, "cfg": 3.0},
            "detail_push": {"steps": 14, "denoise": 0.32, "cfg": 3.5},
            "latent_rebuild": {"steps": 16, "denoise": 0.46, "cfg": 3.0},
        },
        "notes": [
            "P8.2 promotes normal Qwen Image Edit Safetensors / Components for High-Res Lab while preserving Qwen edit conditioning anchors.",
            "Normal qwen_image stays single-source; Qwen Image Edit 2509 owns 1-3 source edit behavior.",
            "Ultimate SD Upscale remains blocked because it assumes SD-style conditioning semantics.",
        ],
    },
    ("qwen_image", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_image_gguf_highres",
        "family_group": "qwen",
        "model_kind": "qwen_image_gguf",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "qwen_reedit"],
        "blocked_strategies": ["ultimate_sd_upscale"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "qwen_safe_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_qwen",
        "anchor_policy": "detect_qwen_gguf_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 10, "denoise": 0.22, "cfg": 3.0},
            "detail_push": {"steps": 14, "denoise": 0.30, "cfg": 3.5},
            "latent_rebuild": {"steps": 16, "denoise": 0.44, "cfg": 3.0},
        },
        "notes": [
            "P8.2 promotes normal Qwen Image Edit GGUF for High-Res Lab through the Qwen GGUF route anchors.",
            "Normal qwen_image GGUF remains single-source; Qwen 2509 owns 1-3 source edit behavior.",
            "Ultimate SD Upscale remains blocked because it assumes SD-style conditioning semantics.",
        ],
    },
    ("qwen_rapid_aio", "checkpoint_aio"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_rapid_aio_checkpoint_highres",
        "family_group": "qwen",
        "model_kind": "qwen_rapid_aio_checkpoint",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "qwen_reedit"],
        "blocked_strategies": ["ultimate_sd_upscale"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "qwen_rapid_aio_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_qwen_aio",
        "anchor_policy": "detect_qwen_aio_checkpoint_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale", "external_vae", "external_text_encoder", "mmproj"],
        "recommended_presets": {
            "balanced_finish": {"steps": 8, "denoise": 0.22, "cfg_policy": "qwen_rapid_aio_low_cfg"},
            "detail_push": {"steps": 10, "denoise": 0.30, "cfg_policy": "qwen_rapid_aio_low_cfg"},
            "latent_rebuild": {"steps": 12, "denoise": 0.42, "cfg_policy": "qwen_rapid_aio_low_cfg"},
        },
        "notes": [
            "P8.2 promotes Qwen Rapid AIO checkpoint/bundled High-Res Lab routes without exposing external VAE/text encoder/MMProj controls.",
            "AIO refine stays low-CFG and short-step by default; hidden bundled fields remain pruned by the route profile.",
            "Ultimate SD Upscale remains blocked because it assumes SD-style conditioning semantics.",
        ],
    },
    ("qwen_rapid_aio", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_rapid_aio_gguf_highres",
        "family_group": "qwen",
        "model_kind": "qwen_rapid_aio_gguf",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "qwen_reedit"],
        "blocked_strategies": ["ultimate_sd_upscale"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "qwen_rapid_aio_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_qwen_aio",
        "anchor_policy": "detect_qwen_rapid_aio_gguf_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 8, "denoise": 0.22, "cfg_policy": "qwen_rapid_aio_low_cfg"},
            "detail_push": {"steps": 10, "denoise": 0.30, "cfg_policy": "qwen_rapid_aio_low_cfg"},
            "latent_rebuild": {"steps": 12, "denoise": 0.42, "cfg_policy": "qwen_rapid_aio_low_cfg"},
        },
        "notes": [
            "P8.2 promotes Qwen Rapid AIO GGUF High-Res Lab routes through Qwen GGUF sampler/model/VAE anchors.",
            "Rapid GGUF refine stays low-CFG and short-step by default.",
            "Ultimate SD Upscale remains blocked because it assumes SD-style conditioning semantics.",
        ],
    },
    ("qwen_image_edit_2509", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_2509_components_highres",
        "family_group": "qwen_2509",
        "model_kind": "qwen_2509_components",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "qwen_reedit"],
        "blocked_strategies": ["ultimate_sd_upscale"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "qwen_2509_safe_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_qwen_2509",
        "anchor_policy": "detect_qwen_2509_edit_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 10, "denoise": 0.22, "cfg": 3.0},
            "detail_push": {"steps": 14, "denoise": 0.30, "cfg": 3.5},
            "latent_rebuild": {"steps": 16, "denoise": 0.44, "cfg": 3.0},
        },
        "notes": [
            "P8.2 promotes Qwen Image Edit 2509 Safetensors / Components for High-Res Lab.",
            "2509 keeps 1-3 source policy for img2img/edit at the base route; High-Res Lab patches the compiled route without downgrading to normal qwen_image.",
            "Inpaint/outpaint remain single-source mask/canvas when the base route compiled that way.",
        ],
    },
    ("qwen_image_edit_2509", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_2509_gguf_highres",
        "family_group": "qwen_2509",
        "model_kind": "qwen_2509_gguf",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only", "qwen_reedit"],
        "blocked_strategies": ["ultimate_sd_upscale"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "qwen_2509_safe_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_qwen_2509",
        "anchor_policy": "detect_qwen_2509_gguf_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 10, "denoise": 0.22, "cfg": 3.0},
            "detail_push": {"steps": 14, "denoise": 0.30, "cfg": 3.5},
            "latent_rebuild": {"steps": 16, "denoise": 0.44, "cfg": 3.0},
        },
        "notes": [
            "P8.2 promotes Qwen Image Edit 2509 GGUF for High-Res Lab.",
            "2509 GGUF keeps 1-3 source policy for img2img/edit at the base route; High-Res Lab patches the compiled route without downgrading to normal qwen_image.",
            "Inpaint/outpaint remain single-source mask/canvas when the base route compiled that way.",
        ],
    },
    ("z_image", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_components_highres",
        "family_group": "z_image",
        "model_kind": "z_image_components",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "z_image_aura_safe",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_z_image",
        "anchor_policy": "detect_z_image_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 10, "denoise": 0.24, "cfg": 3.0},
            "detail_push": {"steps": 14, "denoise": 0.32, "cfg": 3.5},
            "latent_rebuild": {"steps": 18, "denoise": 0.46, "cfg": 3.0},
        },
        "notes": [
            "P8.4 promotes ZImage Safetensors / Components for High-Res Lab using ZImage-native ModelSamplingAuraFlow sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled; SD-style Ultimate SD Upscale and Qwen re-edit remain blocked for ZImage.",
            "High-Res Lab caps ZImage refine CFG to an Aura-safe range and never falls back to Flux, Qwen, SDXL, ZImage Turbo, or GGUF when the selected route is component-based.",
        ],
    },
    ("z_image", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_gguf_highres",
        "family_group": "z_image",
        "model_kind": "z_image_gguf",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit"],
        "snap_multiple": 16,
        "negative_prompt_policy": "preserve_route_conditioning",
        "cfg_policy": "z_image_aura_safe",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_z_image_gguf",
        "anchor_policy": "detect_z_image_gguf_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 8, "denoise": 0.22, "cfg": 2.8},
            "detail_push": {"steps": 12, "denoise": 0.30, "cfg": 3.2},
            "latent_rebuild": {"steps": 16, "denoise": 0.44, "cfg": 2.8},
        },
        "notes": [
            "P8.4 promotes ZImage GGUF for High-Res Lab using GGUF-native ZImage sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled; SD-style Ultimate SD Upscale and Qwen re-edit remain blocked for ZImage GGUF.",
            "High-Res Lab caps ZImage GGUF refine CFG to an Aura-safe range and never falls back to SDXL, Qwen, Flux, ZImage Turbo, or component routes.",
        ],
    },
    ("z_image_turbo", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_turbo_components_highres",
        "family_group": "z_image_turbo",
        "model_kind": "z_image_turbo_components",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit", "detail_push_sd"],
        "snap_multiple": 16,
        "negative_prompt_policy": "zero_or_hide",
        "cfg_policy": "z_image_turbo_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_z_image_turbo",
        "anchor_policy": "detect_z_image_turbo_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 6, "denoise": 0.22, "cfg": 1.0},
            "detail_push": {"steps": 8, "denoise": 0.30, "cfg": 1.0},
            "latent_rebuild": {"steps": 10, "denoise": 0.40, "cfg": 1.0},
        },
        "notes": [
            "P8.5 promotes ZImage Turbo Safetensors / Components for High-Res Lab using Turbo-native ModelSamplingAuraFlow sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled with low-step/low-CFG Turbo-safe defaults; SD-style Ultimate SD Upscale and Qwen re-edit remain blocked.",
            "High-Res Lab caps Turbo refine CFG to 1.0 and never falls back to base ZImage, SDXL, Qwen, Flux, or GGUF when the selected route is component-based.",
        ],
    },
    ("z_image_turbo", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_turbo_gguf_highres",
        "family_group": "z_image_turbo",
        "model_kind": "z_image_turbo_gguf",
        "rollout_state": "available",
        "default_strategy": "forge_pixel_refine",
        "allowed_strategies": ["forge_pixel_refine", "standard", "upscale_only"],
        "blocked_strategies": ["ultimate_sd_upscale", "qwen_reedit", "detail_push_sd"],
        "snap_multiple": 16,
        "negative_prompt_policy": "zero_or_hide",
        "cfg_policy": "z_image_turbo_low_cfg",
        "sampler_policy": "preserve_base_sampler",
        "latent_policy": "warn_low_denoise_z_image_turbo_gguf",
        "anchor_policy": "detect_z_image_turbo_gguf_model_sampler_vae_decode",
        "hidden_controls": ["ultimate_sd_upscale"],
        "recommended_presets": {
            "balanced_finish": {"steps": 5, "denoise": 0.20, "cfg": 1.0},
            "detail_push": {"steps": 8, "denoise": 0.28, "cfg": 1.0},
            "latent_rebuild": {"steps": 10, "denoise": 0.38, "cfg": 1.0},
        },
        "notes": [
            "P8.5 promotes ZImage Turbo GGUF for High-Res Lab using GGUF-native Turbo sampler/model/VAE anchors.",
            "Pixel refine, latent refine, and upscale-only are enabled with conservative Turbo defaults; SD-style Ultimate SD Upscale and Qwen re-edit remain blocked.",
            "High-Res Lab caps Turbo GGUF refine CFG to 1.0 and never falls back to base ZImage, SDXL, Qwen, Flux, or component routes.",
        ],
    },
}


def normalize_backend_id(value: Any) -> str:
    raw = str(value or "comfyui").strip() or "comfyui"
    return BACKEND_ALIASES.get(raw, raw)


def normalize_mode_id(value: Any) -> str:
    raw = str(value or "txt2img").strip() or "txt2img"
    return MODE_ALIASES.get(raw, raw)


def normalized_route(route: dict[str, Any] | None = None) -> dict[str, str]:
    route = route if isinstance(route, dict) else {}
    return {
        "backend": normalize_backend_id(route.get("backend") or route.get("provider") or route.get("provider_id") or "comfyui"),
        "family": str(route.get("family") or "").strip(),
        "loader": str(route.get("loader") or "").strip(),
        "mode": normalize_mode_id(route.get("mode") or route.get("workflow_mode") or "txt2img"),
    }


def route_profile_key(route: dict[str, Any] | None = None) -> tuple[str, str]:
    normalized = normalized_route(route)
    return normalized["family"], normalized["loader"]


def get_route_profile(route: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = deepcopy(_ROUTE_PROFILES.get(route_profile_key(route), DEFAULT_PROFILE))
    normalized = normalized_route(route)
    profile["backend"] = normalized["backend"]
    profile["family"] = normalized["family"]
    profile["loader"] = normalized["loader"]
    profile["mode"] = normalized["mode"]
    return profile


def _base_route_state_from_matrix(route: dict[str, Any]) -> str:
    normalized = normalized_route(route)
    if normalized["backend"] not in {"comfyui", "forge", "a1111"}:
        return "provider_gated"
    try:
        from neo_app.models.route_matrix import resolve_model_backend_route

        entry = resolve_model_backend_route(
            normalized["family"],
            normalized["loader"],
            normalized["mode"],
            normalized["backend"],
        )
        state = str(entry.state or "provider_gated")
    except Exception:
        state = str(route.get("route_state") or route.get("state") or route.get("status") or "provider_gated")
    return state if state in KNOWN_STATES else "provider_gated"


def base_route_state(route: dict[str, Any] | None = None) -> str:
    route = route if isinstance(route, dict) else {}
    explicit = str(route.get("route_state") or route.get("base_route_state") or "").strip()
    if explicit in KNOWN_STATES:
        return explicit
    return _base_route_state_from_matrix(route)


def high_res_lab_state_for_route(route: dict[str, Any] | None = None, *, static_state: str | None = None) -> str:
    normalized = normalized_route(route)
    base_state = base_route_state(normalized)
    if base_state == "unsupported":
        return "unsupported"
    if base_state in {"planned_gated", "provider_gated", "implementation_target"}:
        return base_state
    profile = get_route_profile(normalized)
    rollout_state = str(profile.get("rollout_state") or "implementation_target")
    if rollout_state not in KNOWN_STATES:
        rollout_state = "implementation_target"
    if rollout_state in {"available", "experimental_available"}:
        return "experimental_available" if base_state == "experimental_available" else rollout_state
    return rollout_state


def route_profile_summary(route: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = get_route_profile(route)
    normalized = normalized_route(route)
    base_state = base_route_state(normalized)
    extension_state = high_res_lab_state_for_route(normalized)
    return {
        **profile,
        "backend": normalized["backend"],
        "family": normalized["family"],
        "loader": normalized["loader"],
        "mode": normalized["mode"],
        "base_route_state": base_state,
        "high_res_lab_state": extension_state,
        "active": extension_state in {"available", "experimental_available"},
    }


def all_route_profiles() -> dict[str, dict[str, Any]]:
    return {f"{family}:{loader}": deepcopy(profile) for (family, loader), profile in sorted(_ROUTE_PROFILES.items())}
