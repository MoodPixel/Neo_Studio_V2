from __future__ import annotations

from copy import deepcopy
from typing import Any

EXECUTION_STRATEGY_PHASE = "SD-28.7"
EXECUTION_STRATEGY_SCHEMA = "neo.image.scene_director.execution_strategy.v7"
PUBLIC_NODE_CLASS = "NeoSceneDirectorV054"
REGIONAL_LORA_NODE_CLASS = "NeoRegionalLoRADelta"

ENGINE_CLASSIC_V054 = "classic_v054"
ENGINE_LIGHTWEIGHT_REGIONAL = "lightweight_regional"
ENGINE_UNSUPPORTED = "unsupported"

STATE_ACTIVE = "active"
STATE_EXPERIMENTAL = "experimental"
STATE_PLANNED_GATED = "planned_gated"
STATE_PROVIDER_GATED = "provider_gated"
STATE_UNSUPPORTED = "unsupported"

CLASSIC_SDXL_FAMILIES = {"sdxl", "sdxl_sd"}
CLASSIC_SD15_FAMILIES = {"sd", "sd15", "sd1.5", "sd_1_5", "sd1_5", "stable_diffusion_1_5"}
MODERN_LIGHTWEIGHT_FAMILIES = {
    "krea2",
    "krea2_turbo",
    "flux2_klein",
    "z_image",
    "z_image_turbo",
}

MODERN_FAMILY_LOADERS = {
    "krea2": {"diffusion_model", "gguf"},
    "krea2_turbo": {"diffusion_model", "gguf"},
    "flux2_klein": {"diffusion_model", "gguf"},
    "z_image": {"diffusion_model", "gguf"},
    "z_image_turbo": {"diffusion_model", "gguf"},
}

SUPPORTED_EXECUTION_MODES = {"generate", "img2img", "inpaint"}
PLANNED_EXECUTION_MODES = {"outpaint"}

LIGHTWEIGHT_CORE_NODES = (
    "CLIPTextEncode",
    "ConditioningSetMask",
    "ConditioningCombine",
    "ConditioningZeroOut",
    "SolidMask",
    "MaskComposite",
    "FeatherMask",
)

ZERO_NEGATIVE_FAMILIES = {"krea2_turbo", "flux2_klein", "z_image_turbo"}
REGIONAL_NEGATIVE_FAMILIES = {"krea2", "z_image"}


def _norm(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip().lower().replace("-", "_").replace(" ", "_")


def normalize_scene_director_family(value: Any) -> str:
    family = _norm(value, "sdxl")
    aliases = {
        "sd_xl": "sdxl",
        "stable_diffusion_xl": "sdxl",
        "sd_15": "sd15",
        "sd_1_5": "sd15",
        "sd1_5": "sd15",
        "stable_diffusion_15": "sd15",
        "stable_diffusion_1_5": "sd15",
        "krea_2": "krea2",
        "krea2_raw": "krea2",
        "krea_2_raw": "krea2",
        "krea2raw": "krea2",
        "krea_2_turbo": "krea2_turbo",
        "krea2turbo": "krea2_turbo",
        "flux_2_klein": "flux2_klein",
        "flux2klein": "flux2_klein",
        "flux2_klein_4b": "flux2_klein",
        "flux2_klein_9b": "flux2_klein",
        "zimage": "z_image",
        "zimage_turbo": "z_image_turbo",
        "qwenimage": "qwen_image",
        "qwen_image_edit": "qwen_image",
    }
    return aliases.get(family, family)


def normalize_scene_director_loader(value: Any, family: Any = None) -> str:
    """Normalize Scene Director loader aliases with family-aware Safetensors semantics.

    Neo's visible "Safetensors / Components" route is represented internally as
    ``diffusion_model`` for modern families but classic SD checkpoint workflows still
    use ``checkpoint``.  Keeping that distinction here lets the UI contract and backend
    execution strategy share one route meaning instead of teaching either side a fake
    checkpoint fallback.
    """
    loader = _norm(value, "checkpoint")
    normalized_family = normalize_scene_director_family(family) if family is not None else ""
    if loader in {"safetensors", "safetensor"}:
        return "diffusion_model" if normalized_family in MODERN_LIGHTWEIGHT_FAMILIES else "checkpoint"
    aliases = {
        "ckpt": "checkpoint",
        "checkpoint_loader": "checkpoint",
        "checkpointloader": "checkpoint",
        "components": "diffusion_model",
        "component": "diffusion_model",
        "safetensors_components": "diffusion_model",
        "safetensors_components_loader": "diffusion_model",
        "gguf_loader": "gguf",
        "ggufloader": "gguf",
        "diffusion": "diffusion_model",
        "diffusionmodel": "diffusion_model",
        "diffusion_model_loader": "diffusion_model",
        "unet": "diffusion_model",
        "unet_loader": "diffusion_model",
        "native": "diffusion_model",
    }
    return aliases.get(loader, loader)


def normalize_scene_director_mode(value: Any) -> str:
    mode = _norm(value, "generate")
    if mode in {"txt2img", "text2image", "text_to_image", "generation"}:
        return "generate"
    return mode


def _route_values(route: dict[str, Any] | None = None, **overrides: Any) -> dict[str, str]:
    data = dict(route or {})
    data.update({key: value for key, value in overrides.items() if value is not None})
    backend_raw = _norm(data.get("backend") or data.get("provider") or data.get("provider_id") or "comfyui", "comfyui")
    family = normalize_scene_director_family(data.get("family") or data.get("model_family"))
    return {
        "backend": {
            "comfy": "comfyui",
            "comfyui": "comfyui",
            "comfyui_portable": "comfyui",
            "comfy_portable": "comfyui",
        }.get(backend_raw, backend_raw),
        "family": family,
        "loader": normalize_scene_director_loader(data.get("loader") or data.get("model_loader") or data.get("loader_type"), family),
        "mode": normalize_scene_director_mode(data.get("workflow_mode") or data.get("mode") or data.get("subtab")),
    }


def _base_contract(route: dict[str, str]) -> dict[str, Any]:
    return {
        "schema": EXECUTION_STRATEGY_SCHEMA,
        "phase": EXECUTION_STRATEGY_PHASE,
        "route": deepcopy(route),
        "family": route["family"],
        "loader": route["loader"],
        "mode": route["mode"],
        "public_node": PUBLIC_NODE_CLASS,
        "new_exported_node_required": False,
        "public_contract_preserved": True,
        "public_input_contract_preserved": True,
        "public_output_contract_preserved": True,
        "saved_workflow_contract_preserved": True,
        "workflow_patch_ready": False,
        "execution_enabled": False,
        "custom_scene_director_node_required": False,
        "required_comfy_nodes": [],
        "regional_prompt": {
            "supported": False,
            "mode": "off",
            "implementation_state": STATE_UNSUPPORTED,
        },
        "regional_lora": {
            "supported": False,
            "mode": "off",
            "implementation_state": STATE_UNSUPPORTED,
            "runtime_proof_required": True,
            "global_model_mutation_allowed": False,
        },
        "sampler_policy": {
            "single_sampler_required": False,
            "hidden_sampler_passes_allowed": False,
            "sampler_count_mutation_allowed": False,
        },
        "repair_policy": {
            "heavy_sd_repairs_allowed": False,
            "automatic_midpoint_repair": False,
            "automatic_end_refinement": False,
            "automatic_background_repaint": False,
            "automatic_masked_lora_finish_pass": False,
        },
        "fallback_policy": "no_cross_family_fallback",
        "release_lock": {
            "phase": "SD-28.7",
            "enforced": True,
            "state": "preflight",
            "fail_closed": True,
            "gpu_proof_is_separate": True,
        },
        "reason": "Scene Director has no execution strategy for this route.",
    }


def _classic_strategy(route: dict[str, str], *, experimental: bool = False) -> dict[str, Any]:
    result = _base_contract(route)
    result.update({
        "engine": ENGINE_CLASSIC_V054,
        "status": STATE_EXPERIMENTAL if experimental else STATE_ACTIVE,
        "workflow_patch_ready": True,
        "execution_enabled": True,
        "custom_scene_director_node_required": True,
        "required_comfy_nodes": [PUBLIC_NODE_CLASS],
        "reason": (
            "SD1.5 keeps the existing experimental V054 route unchanged."
            if experimental
            else "SDXL checkpoint keeps the existing locked V054 execution route unchanged."
        ),
    })
    result["regional_prompt"] = {
        "supported": True,
        "mode": "v054_attention_patch",
        "implementation_state": STATE_EXPERIMENTAL if experimental else STATE_ACTIVE,
    }
    result["regional_lora"] = {
        "supported": True,
        "mode": "v054_existing_contract",
        "implementation_state": STATE_EXPERIMENTAL if experimental else STATE_ACTIVE,
        "runtime_proof_required": True,
        "global_model_mutation_allowed": False,
    }
    result["sampler_policy"] = {
        "single_sampler_required": False,
        "hidden_sampler_passes_allowed": True,
        "sampler_count_mutation_allowed": True,
        "note": "Existing V054 user-selected repair/refinement plans remain untouched.",
    }
    result["repair_policy"] = {
        "heavy_sd_repairs_allowed": True,
        "automatic_midpoint_repair": "existing_v054_policy",
        "automatic_end_refinement": "existing_v054_policy",
        "automatic_background_repaint": "existing_v054_policy",
        "automatic_masked_lora_finish_pass": "existing_v054_policy",
    }
    result["fallback_policy"] = "preserve_existing_v054_only"
    return result


def _modern_lightweight_strategy(route: dict[str, str]) -> dict[str, Any]:
    result = _base_contract(route)
    family = route["family"]
    loader = route["loader"]
    mode = route["mode"]
    recognized_loaders = sorted(MODERN_FAMILY_LOADERS.get(family, set()))
    loader_ready = loader in MODERN_FAMILY_LOADERS.get(family, set())
    mode_ready = mode in SUPPORTED_EXECUTION_MODES
    executable = bool(loader_ready and mode_ready)
    required_nodes = list(LIGHTWEIGHT_CORE_NODES)
    if family == "flux2_klein":
        required_nodes.append("FluxGuidance")

    krea2_full = bool(executable and family in {"krea2", "krea2_turbo"})
    klein_full = bool(executable and family == "flux2_klein")
    z_image_full = bool(executable and family in {"z_image", "z_image_turbo"})
    promoted_full = bool(krea2_full or klein_full or z_image_full)
    result.update({
        "engine": ENGINE_LIGHTWEIGHT_REGIONAL,
        "status": STATE_ACTIVE if promoted_full else (STATE_EXPERIMENTAL if executable else STATE_UNSUPPORTED),
        "recognized_loaders": recognized_loaders,
        "workflow_patch_ready": executable,
        "execution_enabled": executable,
        "custom_scene_director_node_required": False,
        "required_comfy_nodes": required_nodes if executable else [],
        "reason": (
            (
                (f"{family} uses the SD-28.4 Krea2 full-support lightweight engine: masked regional prompts and model-side regional LoRA are enabled with one sampler and per-run runtime proof."
                 if krea2_full else
                 "FLUX.2 Klein uses the SD-28.5 full lightweight engine: FluxGuidance-aware masked regional prompts plus a family-specific model-side regional LoRA activation-delta adapter, with one sampler and per-run runtime proof."
                 if klein_full else
                 ("Z-Image Turbo" if family == "z_image_turbo" else "Z-Image") + " uses the SD-28.7 release-locked lightweight engine: native masked regional prompts plus the Z-Image family-specific model-side regional LoRA activation-delta adapter, with one sampler and per-run runtime proof."
                 if z_image_full else
                 f"{family} keeps the lightweight regional prompt engine; regional LoRA remains family-adapter gated.")
            )
            if executable
            else f"{family} is a lightweight-regional target, but loader={loader!r} or mode={mode!r} is outside the SD-28.7 release contract."
        ),
    })
    result["regional_prompt"] = {
        "supported": executable,
        "mode": "masked_conditioning" if executable else "off",
        "implementation_state": STATE_ACTIVE if promoted_full else (STATE_EXPERIMENTAL if executable else STATE_UNSUPPORTED),
        "set_cond_area": "mask bounds",
        "mask_source": "scene_region_bbox",
        "regional_negative_supported": family in REGIONAL_NEGATIVE_FAMILIES,
        "negative_policy": "zero_from_final_positive" if family in ZERO_NEGATIVE_FAMILIES else "masked_regional_negative",
        "family_conditioning_adapter": "flux_guidance" if family == "flux2_klein" else "direct_clip_conditioning",
    }
    krea2_regional_lora = bool(executable and family in {"krea2", "krea2_turbo"})
    klein_regional_lora = bool(executable and family == "flux2_klein")
    z_image_regional_lora = bool(executable and family in {"z_image", "z_image_turbo"})
    modern_regional_lora = bool(krea2_regional_lora or klein_regional_lora or z_image_regional_lora)
    lora_mode = (
        "krea2_activation_delta_v2" if krea2_regional_lora
        else "flux2_klein_activation_delta_v1" if klein_regional_lora
        else "z_image_activation_delta_v1" if z_image_regional_lora
        else "adapter_gated"
    )
    result["regional_lora"] = {
        "supported": modern_regional_lora,
        "mode": lora_mode,
        "implementation_state": STATE_ACTIVE if modern_regional_lora else (STATE_PLANNED_GATED if executable else STATE_UNSUPPORTED),
        "runtime_proof_required": True,
        "runtime_proof_scope": "per_run",
        "runtime_gpu_proven": False,
        "global_model_mutation_allowed": False,
        "clip_delta_execution": "suppressed_model_side_only" if modern_regional_lora else "not_available",
        "custom_node_required_when_requested": modern_regional_lora,
        "required_node": REGIONAL_LORA_NODE_CLASS if modern_regional_lora else None,
        "route_limit": None,
        "finish_pass_fallback": "disabled_by_default",
        "adapter_gate_reason": "" if modern_regional_lora else "No family-specific masked model-delta adapter is validated for this modern family in SD-28.7.",
        "raw_turbo_cross_variant_compatible": bool(krea2_regional_lora),
        "supported_loaders": sorted(MODERN_FAMILY_LOADERS.get(family, set())) if modern_regional_lora else [],
    }
    result["sampler_policy"] = {
        "single_sampler_required": True,
        "hidden_sampler_passes_allowed": False,
        "sampler_count_mutation_allowed": False,
        "conditioning_rewire_allowed": executable,
        "turbo_profile_must_be_preserved": family in {"krea2_turbo", "z_image_turbo"},
        "klein_low_step_profile_must_be_preserved": family == "flux2_klein",
    }
    result["repair_policy"] = {
        "heavy_sd_repairs_allowed": False,
        "automatic_midpoint_repair": False,
        "automatic_end_refinement": False,
        "automatic_background_repaint": False,
        "automatic_masked_lora_finish_pass": False,
    }
    result["fallback_policy"] = "never_fallback_to_classic_v054"
    return result


def resolve_scene_director_execution_strategy(
    route: dict[str, Any] | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Resolve Scene Director's execution engine without mutating a workflow.

    SD-28.7 release-locks Krea2 RAW/Turbo, FLUX.2 Klein, and Z-Image
    Base/Turbo on their family-specific masked regional LoRA adapters while retaining
    per-run runtime proof. Classic
    SDXL/SD1.5 keeps V054 unchanged.
    """
    normalized = _route_values(route, **overrides)
    family = normalized["family"]
    loader = normalized["loader"]
    mode = normalized["mode"]
    backend = normalized["backend"]

    if family in CLASSIC_SDXL_FAMILIES and loader == "checkpoint":
        result = _classic_strategy(normalized, experimental=False)
    elif family in CLASSIC_SD15_FAMILIES and loader == "checkpoint":
        result = _classic_strategy(normalized, experimental=True)
    elif family in MODERN_LIGHTWEIGHT_FAMILIES:
        result = _modern_lightweight_strategy(normalized)
    else:
        result = _base_contract(normalized)
        result.update({
            "engine": ENGINE_UNSUPPORTED,
            "status": STATE_UNSUPPORTED,
            "reason": f"{family} has no Scene Director execution engine and must not fallback to V054.",
        })
        return result

    if backend != "comfyui":
        result.update({
            "status": STATE_PROVIDER_GATED,
            "workflow_patch_ready": False,
            "execution_enabled": False,
            "reason": "Scene Director execution requires a validated ComfyUI backend.",
        })
        return result
    if mode in PLANNED_EXECUTION_MODES:
        result.update({
            "status": STATE_PLANNED_GATED,
            "workflow_patch_ready": False,
            "execution_enabled": False,
            "reason": "Scene Director outpaint remains planned-gated until its dedicated canvas/mask policy is implemented.",
        })
        if result.get("engine") == ENGINE_LIGHTWEIGHT_REGIONAL:
            result["regional_prompt"] = {**(result.get("regional_prompt") or {}), "supported": False, "implementation_state": STATE_PLANNED_GATED}
        return result
    if mode not in SUPPORTED_EXECUTION_MODES:
        result.update({
            "status": STATE_UNSUPPORTED,
            "workflow_patch_ready": False,
            "execution_enabled": False,
            "reason": f"Scene Director does not support workflow mode {mode!r} in SD-28.7.",
        })
    return result


__all__ = [
    "EXECUTION_STRATEGY_PHASE",
    "EXECUTION_STRATEGY_SCHEMA",
    "PUBLIC_NODE_CLASS",
    "REGIONAL_LORA_NODE_CLASS",
    "ENGINE_CLASSIC_V054",
    "ENGINE_LIGHTWEIGHT_REGIONAL",
    "ENGINE_UNSUPPORTED",
    "CLASSIC_SDXL_FAMILIES",
    "CLASSIC_SD15_FAMILIES",
    "MODERN_LIGHTWEIGHT_FAMILIES",
    "MODERN_FAMILY_LOADERS",
    "SUPPORTED_EXECUTION_MODES",
    "PLANNED_EXECUTION_MODES",
    "LIGHTWEIGHT_CORE_NODES",
    "ZERO_NEGATIVE_FAMILIES",
    "REGIONAL_NEGATIVE_FAMILIES",
    "normalize_scene_director_family",
    "normalize_scene_director_loader",
    "normalize_scene_director_mode",
    "resolve_scene_director_execution_strategy",
]
