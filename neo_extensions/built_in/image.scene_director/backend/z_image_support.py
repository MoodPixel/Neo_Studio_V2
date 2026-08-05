from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

PHASE = "SD-28.6"
SCHEMA = "neo.image.scene_director.z_image_full_support.v1"
Z_IMAGE_FAMILIES = {"z_image", "z_image_turbo"}
Z_IMAGE_LOADERS = {"diffusion_model", "gguf"}
Z_IMAGE_MODES = {"generate", "img2img", "inpaint"}

# Neo provider defaults. Scene Director validates/preserves these; it does not
# rewrite the provider sampler profile.
Z_IMAGE_TURBO_NEO_STEPS = 9
Z_IMAGE_TURBO_COMFY_CFG = 1.0
Z_IMAGE_BASE_REFERENCE_STEPS = 35
Z_IMAGE_BASE_MIN_STEPS = 28
Z_IMAGE_BASE_REFERENCE_CFG = 3.5
Z_IMAGE_BASE_MIN_CFG = 2.5

# Official Z-Image / Z-Image Turbo transformer architecture signature.
Z_IMAGE_DIM = 3840
Z_IMAGE_IN_CHANNELS = 16
Z_IMAGE_HEADS = 30
Z_IMAGE_MAIN_LAYERS = 30
Z_IMAGE_REFINER_LAYERS = 2
Z_IMAGE_PATCH_SIZE = 2


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_z_image_family(value: Any) -> str:
    raw = _norm(value)
    aliases = {
        "zimage": "z_image",
        "z_image_base": "z_image",
        "zimage_base": "z_image",
        "z_image_turbo": "z_image_turbo",
        "zimage_turbo": "z_image_turbo",
        "zimage_turbo_6b": "z_image_turbo",
    }
    return aliases.get(raw, raw)


def _route_value(route: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    source = route if isinstance(route, dict) else {}
    for container_key in ("actual_params", "params"):
        container = source.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in keys:
            if container.get(key) not in (None, ""):
                return container.get(key)
    for key in keys:
        if source.get(key) not in (None, ""):
            return source.get(key)
    return default


def resolve_z_image_profile(route: dict[str, Any] | None) -> dict[str, Any]:
    family = normalize_z_image_family(_route_value(route, "family", "model_family", default=""))
    loader = _norm(_route_value(route, "loader", "model_loader", "loader_type", default="diffusion_model"))
    if loader in {"native", "unet", "unet_loader", "diffusionmodel"}:
        loader = "diffusion_model"
    mode = _norm(_route_value(route, "workflow_mode", "mode", default="generate"))
    if mode in {"txt2img", "text2image", "text_to_image", "generation"}:
        mode = "generate"
    model_name = str(_route_value(route, "diffusion_model", "gguf_model", "gguf_unet", "model", "model_name", default="") or "")
    turbo = family == "z_image_turbo"
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "family": family,
        "loader": loader,
        "mode": mode,
        "model_name": model_name,
        "variant": "turbo" if turbo else ("base" if family == "z_image" else "unknown"),
        "expected_signature": {
            "dim": Z_IMAGE_DIM,
            "in_channels": Z_IMAGE_IN_CHANNELS,
            "n_heads": Z_IMAGE_HEADS,
            "main_layers": Z_IMAGE_MAIN_LAYERS,
            "noise_refiner_layers": Z_IMAGE_REFINER_LAYERS,
            "context_refiner_layers": Z_IMAGE_REFINER_LAYERS,
            "patch_size": Z_IMAGE_PATCH_SIZE,
        },
        "turbo_reference_steps": Z_IMAGE_TURBO_NEO_STEPS,
        "base_reference_steps": Z_IMAGE_BASE_REFERENCE_STEPS,
        "base_reference_cfg": Z_IMAGE_BASE_REFERENCE_CFG,
    }


def _binding_sources(binding: dict[str, Any]) -> list[dict[str, Any]]:
    owner = binding.get("owner_row") if isinstance(binding.get("owner_row"), dict) else {}
    source = owner.get("source_record") if isinstance(owner.get("source_record"), dict) else {}
    metadata = owner.get("metadata") if isinstance(owner.get("metadata"), dict) else {}
    return [binding, owner, source, metadata]


def _binding_declared_family(binding: dict[str, Any]) -> str:
    for source in _binding_sources(binding):
        for key in ("lora_family", "model_family", "checkpoint_family", "family"):
            value = source.get(key)
            if str(value or "").strip():
                return str(value)
    return ""


def classify_z_image_binding_compatibility(binding: dict[str, Any], target_family: Any) -> dict[str, Any]:
    target = normalize_z_image_family(target_family)
    declared_raw = _binding_declared_family(binding)
    declared = normalize_z_image_family(declared_raw)
    base = {
        "target_family": target,
        "declared_family": declared_raw,
        "shared_transformer_architecture": True,
        "runtime_preflight_required": True,
    }
    if target not in Z_IMAGE_FAMILIES:
        return {**base, "compatible": False, "state": "not_z_image_target", "reason": "Z-Image compatibility classification was requested for a non-Z-Image target."}
    if not declared_raw:
        return {
            **base,
            "compatible": None,
            "state": "unknown_runtime_preflight_required",
            "reason": "LoRA metadata does not declare Z-Image Base/Turbo; live layer resolution must prove compatibility.",
        }
    if declared not in Z_IMAGE_FAMILIES and declared not in {"zimage", "z_image_family"}:
        return {
            **base,
            "compatible": False,
            "state": "declared_family_incompatible",
            "reason": f"LoRA declares family '{declared_raw}', which is not a Z-Image family.",
        }
    if declared == target or declared in {"zimage", "z_image_family"}:
        return {
            **base,
            "compatible": True,
            "state": "same_variant_structurally_compatible",
            "reason": "LoRA metadata targets the same Z-Image variant; live layer resolution remains authoritative.",
        }
    return {
        **base,
        "compatible": None,
        "state": "base_turbo_cross_variant_runtime_preflight_required",
        "reason": "Z-Image Base and Turbo share the transformer architecture, but the official model contract does not guarantee cross-variant LoRA interchangeability; live layer resolution must prove compatibility.",
    }


def filter_z_image_bindings(bindings: list[dict[str, Any]] | None, target_family: Any) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for index, binding in enumerate(bindings or [], start=1):
        if not isinstance(binding, dict):
            continue
        compatibility = classify_z_image_binding_compatibility(binding, target_family)
        record = {**deepcopy(binding), "z_image_compatibility": compatibility}
        if compatibility.get("compatible") is False:
            rejected.append({
                "index": index,
                "region_id": str(binding.get("region_id") or ""),
                "lora_name": str(binding.get("name") or binding.get("lora_name") or ""),
                "compatibility": compatibility,
            })
            continue
        accepted.append(record)
        if compatibility.get("compatible") is None:
            unknown.append({
                "index": index,
                "region_id": str(binding.get("region_id") or ""),
                "lora_name": str(binding.get("name") or binding.get("lora_name") or ""),
                "compatibility": compatibility,
            })
    return {
        "schema": "neo.image.scene_director.z_image_lora_compatibility.v1",
        "phase": PHASE,
        "target_family": normalize_z_image_family(target_family),
        "accepted": accepted,
        "rejected": rejected,
        "unknown": unknown,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "unknown_count": len(unknown),
    }


def _ref_node(workflow: dict[str, Any], ref: Any) -> dict[str, Any] | None:
    if not isinstance(ref, (list, tuple)) or not ref:
        return None
    node = workflow.get(str(ref[0]))
    return node if isinstance(node, dict) else None


def _model_chain(workflow: dict[str, Any], model_ref: Any, max_depth: int = 12) -> list[dict[str, Any]]:
    chain: list[dict[str, Any]] = []
    ref = model_ref
    seen: set[str] = set()
    for _ in range(max_depth):
        if not isinstance(ref, (list, tuple)) or not ref:
            break
        node_id = str(ref[0])
        if node_id in seen:
            break
        seen.add(node_id)
        node = workflow.get(node_id)
        if not isinstance(node, dict):
            break
        chain.append({"node_id": node_id, "class_type": str(node.get("class_type") or "")})
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        next_ref = inputs.get("model")
        if not isinstance(next_ref, (list, tuple)):
            break
        ref = next_ref
    return chain


def validate_z_image_sampler_profile(
    workflow: dict[str, Any],
    *,
    sampler_node_id: Any,
    route: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = resolve_z_image_profile(route)
    family = profile.get("family")
    sampler = workflow.get(str(sampler_node_id)) if isinstance(workflow, dict) else None
    errors: list[str] = []
    warnings: list[str] = []
    if family not in Z_IMAGE_FAMILIES:
        return {"schema": SCHEMA, "phase": PHASE, "applicable": False, "ok": True, "profile": profile, "errors": [], "warnings": []}
    if profile.get("loader") not in Z_IMAGE_LOADERS:
        errors.append(f"Z-Image Scene Director requires diffusion_model or gguf loader, got '{profile.get('loader')}'.")
    if not isinstance(sampler, dict) or str(sampler.get("class_type") or "") not in {"KSampler", "KSamplerAdvanced"}:
        errors.append("Z-Image Scene Director could not resolve the provider KSampler.")
        inputs: dict[str, Any] = {}
    else:
        inputs = sampler.get("inputs") if isinstance(sampler.get("inputs"), dict) else {}
    try:
        steps = int(inputs.get("steps")) if inputs.get("steps") is not None else None
    except Exception:
        steps = None
    try:
        cfg = float(inputs.get("cfg")) if inputs.get("cfg") is not None else None
    except Exception:
        cfg = None
    negative_node = _ref_node(workflow, inputs.get("negative"))
    negative_class = str((negative_node or {}).get("class_type") or "")
    chain = _model_chain(workflow, inputs.get("model"))
    chain_classes = [item["class_type"] for item in chain]
    if "ModelSamplingAuraFlow" not in chain_classes:
        errors.append("Z-Image must preserve the provider ModelSamplingAuraFlow MODEL wrapper before sampling.")
    if family == "z_image_turbo":
        if steps is not None and steps != Z_IMAGE_TURBO_NEO_STEPS:
            errors.append(f"Z-Image Turbo Neo provider steps must remain {Z_IMAGE_TURBO_NEO_STEPS}; got {steps!r}.")
        if cfg is not None and abs(cfg - Z_IMAGE_TURBO_COMFY_CFG) > 1e-6:
            errors.append(f"Z-Image Turbo Neo provider CFG must remain {Z_IMAGE_TURBO_COMFY_CFG}; got {cfg!r}.")
        if negative_class != "ConditioningZeroOut":
            errors.append("Z-Image Turbo must preserve zeroed negative conditioning via ConditioningZeroOut.")
    else:
        if negative_class != "CLIPTextEncode":
            errors.append("Z-Image Base must preserve encoded negative conditioning via CLIPTextEncode.")
        if steps is not None and steps < Z_IMAGE_BASE_MIN_STEPS:
            warnings.append(f"Z-Image Base is below Neo's normal {Z_IMAGE_BASE_MIN_STEPS}-step floor ({steps}); Scene Director preserves the provider/user value instead of rewriting it.")
        if cfg is not None and cfg < Z_IMAGE_BASE_MIN_CFG:
            warnings.append(f"Z-Image Base CFG is below Neo's normal {Z_IMAGE_BASE_MIN_CFG} floor ({cfg}); Scene Director preserves it instead of rewriting it.")
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "applicable": True,
        "ok": not errors,
        "profile": profile,
        "steps": steps,
        "cfg": cfg,
        "negative_class": negative_class,
        "model_chain": chain,
        "model_sampling_aura_flow_present": "ModelSamplingAuraFlow" in chain_classes,
        "errors": errors,
        "warnings": warnings,
        "single_sampler_policy": True,
        "scene_director_may_change_sampler_profile": False,
    }


def z_image_full_support_contract(route: dict[str, Any] | None) -> dict[str, Any]:
    profile = resolve_z_image_profile(route)
    supported = (
        profile.get("family") in Z_IMAGE_FAMILIES
        and profile.get("loader") in Z_IMAGE_LOADERS
        and profile.get("mode") in Z_IMAGE_MODES
    )
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "profile": profile,
        "supported": supported,
        "regional_prompt": supported,
        "regional_lora": supported,
        "single_sampler_required": True,
        "global_model_mutation_allowed": False,
        "regional_clip_lora": False,
        "masked_finish_pass_fallback": False,
        "runtime_proof_required_per_run": True,
        "base_turbo_cross_variant_auto_compatible": False,
        "z_image_architecture_signature": deepcopy(profile.get("expected_signature") or {}),
    }


__all__ = [
    "PHASE",
    "SCHEMA",
    "Z_IMAGE_FAMILIES",
    "Z_IMAGE_LOADERS",
    "Z_IMAGE_MODES",
    "Z_IMAGE_TURBO_NEO_STEPS",
    "Z_IMAGE_TURBO_COMFY_CFG",
    "Z_IMAGE_BASE_REFERENCE_STEPS",
    "Z_IMAGE_BASE_REFERENCE_CFG",
    "Z_IMAGE_DIM",
    "Z_IMAGE_IN_CHANNELS",
    "Z_IMAGE_HEADS",
    "Z_IMAGE_MAIN_LAYERS",
    "Z_IMAGE_REFINER_LAYERS",
    "Z_IMAGE_PATCH_SIZE",
    "normalize_z_image_family",
    "resolve_z_image_profile",
    "classify_z_image_binding_compatibility",
    "filter_z_image_bindings",
    "validate_z_image_sampler_profile",
    "z_image_full_support_contract",
]
