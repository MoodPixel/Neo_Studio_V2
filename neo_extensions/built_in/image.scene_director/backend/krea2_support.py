from __future__ import annotations

from copy import deepcopy
from typing import Any

PHASE = "SD-28.4"
SCHEMA = "neo.image.scene_director.krea2_full_support.v1"
KREA2_FAMILIES = {"krea2", "krea2_turbo"}
KREA2_LOADERS = {"diffusion_model", "gguf"}
KREA2_MODES = {"generate", "img2img", "inpaint"}
KREA2_TURBO_STEPS = 8
KREA2_TURBO_COMFY_CFG = 1.0
KREA2_RAW_REFERENCE_STEPS = 52
KREA2_RAW_REFERENCE_CFG = 3.5


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_krea2_family(value: Any) -> str:
    raw = _norm(value)
    aliases = {
        "krea_2": "krea2",
        "krea2_raw": "krea2",
        "krea_2_raw": "krea2",
        "krea2raw": "krea2",
        "krea_raw": "krea2",
        "krea2_turbo": "krea2_turbo",
        "krea_2_turbo": "krea2_turbo",
        "krea2turbo": "krea2_turbo",
        "krea_turbo": "krea2_turbo",
    }
    return aliases.get(raw, raw)


def _binding_declared_family(binding: dict[str, Any]) -> str:
    owner = binding.get("owner_row") if isinstance(binding.get("owner_row"), dict) else {}
    source = owner.get("source_record") if isinstance(owner.get("source_record"), dict) else {}
    metadata = owner.get("metadata") if isinstance(owner.get("metadata"), dict) else {}
    # UI/catalog records can legitimately carry sentinel values such as
    # ``unknown`` when the LoRA file has no embedded Neo family metadata.
    # Treat those as *missing metadata*, not as an explicit declaration of an
    # incompatible architecture.  The Krea2 regional runtime already has a
    # fail-closed layer-resolution proof for metadata-less LoRAs.
    unknown_tokens = {"unknown", "auto", "unspecified", "unset", "none", "verify"}
    for value in (
        binding.get("lora_family"),
        binding.get("model_family"),
        binding.get("checkpoint_family"),
        owner.get("lora_family"),
        owner.get("model_family"),
        owner.get("checkpoint_family"),
        owner.get("family"),
        source.get("lora_family"),
        source.get("model_family"),
        source.get("family"),
        metadata.get("lora_family"),
        metadata.get("model_family"),
        metadata.get("family"),
    ):
        if str(value or "").strip():
            normalized = _norm(value)
            if normalized in unknown_tokens:
                continue
            return normalized
    return ""


def classify_krea2_binding_compatibility(binding: dict[str, Any], target_family: Any) -> dict[str, Any]:
    target = normalize_krea2_family(target_family)
    declared_raw = _binding_declared_family(binding)
    declared = normalize_krea2_family(declared_raw)
    if target not in KREA2_FAMILIES:
        return {
            "compatible": False,
            "state": "not_krea2_target",
            "target_family": target,
            "declared_family": declared_raw,
            "reason": "Krea2 compatibility classification was requested for a non-Krea2 target.",
        }
    if not declared_raw:
        return {
            "compatible": None,
            "state": "unknown_runtime_preflight_required",
            "target_family": target,
            "declared_family": "",
            "cross_variant_compatible": True,
            "reason": "LoRA metadata does not declare a model family; runtime Krea2 layer resolution must prove compatibility.",
        }
    if declared in KREA2_FAMILIES or declared in {"krea", "krea2_family"}:
        return {
            "compatible": True,
            "state": "compatible",
            "target_family": target,
            "declared_family": declared,
            "cross_variant_compatible": True,
            "reason": "Krea2 RAW/Turbo share the Krea2 LoRA architecture; RAW-trained LoRAs are valid Turbo inference targets.",
        }
    return {
        "compatible": False,
        "state": "declared_family_incompatible",
        "target_family": target,
        "declared_family": declared,
        "cross_variant_compatible": False,
        "reason": f"LoRA declares family '{declared_raw}', which is not a Krea2 family.",
    }


def filter_krea2_bindings(bindings: list[dict[str, Any]] | None, target_family: Any) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for index, binding in enumerate(bindings or [], start=1):
        if not isinstance(binding, dict):
            continue
        compatibility = classify_krea2_binding_compatibility(binding, target_family)
        record = {**deepcopy(binding), "krea2_compatibility": compatibility}
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
        "schema": "neo.image.scene_director.krea2_lora_compatibility.v1",
        "phase": PHASE,
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




def _conditioning_source_class(workflow: dict[str, Any], ref: Any) -> tuple[str, list[str]]:
    """Resolve the semantic source class through provider conditioning wrappers.

    Native inpaint feeds the sampler from InpaintModelConditioning outputs, so
    the sampler's immediate negative class is not ConditioningZeroOut even when
    Krea2 Turbo zero-negative semantics are preserved upstream.  Walk only the
    known conditioning-preserving wrappers and stop on ambiguous combines.
    """
    trace: list[str] = []
    current = list(ref) if isinstance(ref, (list, tuple)) and len(ref) >= 2 else None
    visited: set[tuple[str, str]] = set()
    while current:
        node_id = str(current[0])
        key = (node_id, str(current[1]))
        if key in visited:
            break
        visited.add(key)
        node = workflow.get(node_id) if isinstance(workflow, dict) else None
        if not isinstance(node, dict):
            break
        class_type = str(node.get("class_type") or "")
        trace.append(class_type)
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        try:
            output_index = int(current[1])
        except Exception:
            output_index = 0
        if class_type == "InpaintModelConditioning":
            parent = inputs.get("positive" if output_index == 0 else "negative" if output_index == 1 else "")
        elif class_type in {"FluxGuidance"}:
            parent = inputs.get("conditioning")
        else:
            break
        current = list(parent) if isinstance(parent, (list, tuple)) and len(parent) >= 2 else None
    return (trace[-1] if trace else ""), trace
def validate_krea2_sampler_profile(
    workflow: dict[str, Any],
    *,
    sampler_node_id: Any,
    family: Any,
    loader: Any,
) -> dict[str, Any]:
    target = normalize_krea2_family(family)
    loader_norm = _norm(loader)
    sampler = workflow.get(str(sampler_node_id)) if isinstance(workflow, dict) else None
    errors: list[str] = []
    warnings: list[str] = []
    if target not in KREA2_FAMILIES:
        return {
            "schema": SCHEMA,
            "phase": PHASE,
            "applicable": False,
            "ok": True,
            "family": target,
            "loader": loader_norm,
            "errors": [],
            "warnings": [],
        }
    if loader_norm not in KREA2_LOADERS:
        errors.append(f"Krea2 Scene Director requires diffusion_model or gguf loader, got '{loader_norm}'.")
    if not isinstance(sampler, dict) or str(sampler.get("class_type") or "") not in {"KSampler", "KSamplerAdvanced"}:
        errors.append("Krea2 Scene Director could not resolve the provider sampler.")
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
    resolved_negative_class, negative_conditioning_trace = _conditioning_source_class(workflow, inputs.get("negative"))
    if target == "krea2_turbo":
        if steps != KREA2_TURBO_STEPS:
            errors.append(f"Krea2 Turbo must preserve {KREA2_TURBO_STEPS} sampling steps; got {steps!r}.")
        if cfg is None or abs(cfg - KREA2_TURBO_COMFY_CFG) > 1e-6:
            errors.append(f"Krea2 Turbo Comfy CFG must remain {KREA2_TURBO_COMFY_CFG}; got {cfg!r}.")
        if resolved_negative_class != "ConditioningZeroOut":
            errors.append("Krea2 Turbo must preserve zeroed negative conditioning via ConditioningZeroOut, including through native inpaint conditioning wrappers.")
        profile = "turbo_8_step_zero_negative"
    else:
        profile = "raw_full_sampler_cfg"
        if steps is not None and steps <= 8:
            warnings.append("Krea2 RAW is using a very low step count; Scene Director preserves the provider/user sampler rather than silently converting it to Turbo.")
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "applicable": True,
        "ok": not errors,
        "family": target,
        "loader": loader_norm,
        "profile": profile,
        "steps": steps,
        "cfg": cfg,
        "negative_class": negative_class,
        "resolved_negative_class": resolved_negative_class,
        "negative_conditioning_trace": negative_conditioning_trace,
        "turbo_expected_steps": KREA2_TURBO_STEPS,
        "turbo_expected_cfg": KREA2_TURBO_COMFY_CFG,
        "raw_reference_steps": KREA2_RAW_REFERENCE_STEPS,
        "raw_reference_cfg": KREA2_RAW_REFERENCE_CFG,
        "errors": errors,
        "warnings": warnings,
        "single_sampler_policy": True,
        "scene_director_may_change_sampler_profile": False,
    }


def krea2_full_support_contract(family: Any, loader: Any, mode: Any) -> dict[str, Any]:
    family_norm = normalize_krea2_family(family)
    loader_norm = _norm(loader)
    mode_norm = _norm(mode)
    if mode_norm in {"txt2img", "generation"}:
        mode_norm = "generate"
    supported = family_norm in KREA2_FAMILIES and loader_norm in KREA2_LOADERS and mode_norm in KREA2_MODES
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "family": family_norm,
        "loader": loader_norm,
        "mode": mode_norm,
        "supported": supported,
        "regional_prompt": supported,
        "regional_lora": supported,
        "raw_turbo_lora_cross_compatibility": True,
        "single_sampler_required": True,
        "global_model_mutation_allowed": False,
        "regional_clip_lora": False,
        "masked_finish_pass_fallback": False,
        "runtime_proof_required_per_run": True,
        "live_gpu_validation_external_to_static_suite": True,
        "reason": "" if supported else "Route is outside the SD-28.4 Krea2 support contract.",
    }


__all__ = [
    "PHASE",
    "SCHEMA",
    "KREA2_FAMILIES",
    "KREA2_LOADERS",
    "KREA2_MODES",
    "KREA2_TURBO_STEPS",
    "KREA2_TURBO_COMFY_CFG",
    "normalize_krea2_family",
    "classify_krea2_binding_compatibility",
    "filter_krea2_bindings",
    "validate_krea2_sampler_profile",
    "krea2_full_support_contract",
]
