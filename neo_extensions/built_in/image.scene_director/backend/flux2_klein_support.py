from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

PHASE = "SD-28.5"
SCHEMA = "neo.image.scene_director.flux2_klein_full_support.v1"
KLEIN_FAMILY = "flux2_klein"
KLEIN_LOADERS = {"diffusion_model", "gguf"}
KLEIN_MODES = {"generate", "img2img", "inpaint"}
KLEIN_SCALES = {"4b", "9b"}
KLEIN_DISTILLED_REFERENCE_STEPS = 4
KLEIN_BASE_REFERENCE_STEPS = 50
KLEIN_COMFY_CFG = 1.0
# Transformer depth is the runtime family discriminator. 4B uses a 3072-wide
# transformer in current FLUX.2 Klein implementations; the Qwen3-4B text
# encoder is 2560-wide, so do not confuse encoder width with DiT hidden size.
# Width remains diagnostic only because quantized/custom operations may expose
# it differently; block depth is what fail-closes Klein vs FLUX.2 dev/unknown.
KLEIN_SIGNATURES = {
    "4b": {"double_blocks": 5, "single_blocks": 20, "transformer_hidden_reference": 3072},
    "9b": {"double_blocks": 8, "single_blocks": 24, "transformer_hidden_reference": 4096},
}


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def normalize_klein_family(value: Any) -> str:
    raw = _norm(value)
    aliases = {
        "flux_2_klein": KLEIN_FAMILY,
        "flux2klein": KLEIN_FAMILY,
        "klein": KLEIN_FAMILY,
        "klein_4b": KLEIN_FAMILY,
        "klein_9b": KLEIN_FAMILY,
        "flux2_klein_4b": KLEIN_FAMILY,
        "flux2_klein_9b": KLEIN_FAMILY,
    }
    return aliases.get(raw, raw)


def _route_value(route: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    source = route if isinstance(route, dict) else {}
    for nested_key in ("actual_params", "params"):
        nested = source.get(nested_key)
        if not isinstance(nested, dict):
            continue
        for key in keys:
            if nested.get(key) not in (None, ""):
                return nested.get(key)
    for key in keys:
        if source.get(key) not in (None, ""):
            return source.get(key)
    return default


def _scale_from_text(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    if re.search(r"(?:^|[^0-9a-z])9b(?:[^0-9a-z]|$)", text):
        return "9b"
    if re.search(r"(?:^|[^0-9a-z])4b(?:[^0-9a-z]|$)", text):
        return "4b"
    return ""


def _variant_kind_from_text(value: Any) -> str:
    text = _norm(value)
    if not text:
        return "unknown"
    if "base" in text:
        return "base"
    if "distill" in text or "turbo" in text:
        return "distilled"
    # Current Neo's plain Klein 4B/9B variants point at the production distilled
    # route unless an explicit Base model identity is selected.
    if "klein" in text and ("4b" in text or "9b" in text):
        return "distilled"
    return "unknown"


def resolve_klein_profile(route: dict[str, Any] | None) -> dict[str, Any]:
    family = normalize_klein_family(_route_value(route, "family", "model_family", default=KLEIN_FAMILY))
    loader = _norm(_route_value(route, "loader", "model_loader", "loader_type", default="diffusion_model"))
    mode = _norm(_route_value(route, "workflow_mode", "mode", default="generate"))
    if mode in {"txt2img", "generation"}:
        mode = "generate"
    model_name = str(_route_value(
        route,
        "diffusion_model", "gguf_model", "gguf_unet", "model", "model_name",
        default="",
    ) or "")
    variant = str(_route_value(route, "flux_variant", "variant", default="flux2_klein") or "flux2_klein")
    scale = _scale_from_text(model_name) or _scale_from_text(variant)
    kind = _variant_kind_from_text(model_name)
    if kind == "unknown":
        kind = _variant_kind_from_text(variant)
    signature = deepcopy(KLEIN_SIGNATURES.get(scale) or {})
    return {
        "schema": "neo.image.scene_director.flux2_klein.profile.v1",
        "phase": PHASE,
        "family": family,
        "loader": loader,
        "mode": mode,
        "model_name": model_name,
        "variant": variant,
        "scale": scale,
        "variant_kind": kind,
        "expected_signature": signature,
        "scale_proven": scale in KLEIN_SCALES,
        "variant_kind_proven": kind in {"base", "distilled"},
        "distilled_reference_steps": KLEIN_DISTILLED_REFERENCE_STEPS,
        "base_reference_steps": KLEIN_BASE_REFERENCE_STEPS,
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
                return _norm(value)
    return ""


def _binding_scale(binding: dict[str, Any]) -> str:
    for source in _binding_sources(binding):
        for key in ("lora_scale", "model_scale", "flux_scale", "lora_variant", "model_variant", "flux_variant", "variant"):
            scale = _scale_from_text(source.get(key))
            if scale:
                return scale
    return _scale_from_text(binding.get("name") or binding.get("lora_name"))


def _binding_variant_kind(binding: dict[str, Any]) -> str:
    for source in _binding_sources(binding):
        for key in ("lora_variant", "model_variant", "flux_variant", "variant", "model_name"):
            kind = _variant_kind_from_text(source.get(key))
            if kind != "unknown":
                return kind
    return _variant_kind_from_text(binding.get("name") or binding.get("lora_name"))


def classify_klein_binding_compatibility(binding: dict[str, Any], route: dict[str, Any] | None) -> dict[str, Any]:
    profile = resolve_klein_profile(route)
    target_scale = profile.get("scale") or ""
    target_kind = profile.get("variant_kind") or "unknown"
    declared_raw = _binding_declared_family(binding)
    declared_family = normalize_klein_family(declared_raw)
    lora_scale = _binding_scale(binding)
    lora_kind = _binding_variant_kind(binding)

    base = {
        "target_family": KLEIN_FAMILY,
        "target_scale": target_scale,
        "target_variant_kind": target_kind,
        "declared_family": declared_raw,
        "lora_scale": lora_scale,
        "lora_variant_kind": lora_kind,
        "runtime_preflight_required": True,
    }
    if declared_raw and declared_family != KLEIN_FAMILY:
        return {**base, "compatible": False, "state": "declared_family_incompatible", "reason": f"LoRA declares family '{declared_raw}', which is not FLUX.2 Klein."}
    if target_scale and lora_scale and target_scale != lora_scale:
        return {**base, "compatible": False, "state": "model_scale_incompatible", "reason": f"FLUX.2 Klein {lora_scale.upper()} LoRA cannot be applied to a {target_scale.upper()} transformer."}
    if target_kind in {"base", "distilled"} and lora_kind in {"base", "distilled"} and target_kind != lora_kind:
        return {
            **base,
            "compatible": None,
            "state": "same_scale_base_distilled_runtime_preflight_required",
            "reason": "Base and distilled Klein weights are not declared LoRA-interchangeable by the official model contract; live layer resolution must prove compatibility.",
        }
    if not target_scale or not lora_scale:
        return {
            **base,
            "compatible": None,
            "state": "unknown_scale_runtime_preflight_required",
            "reason": "Klein model/LoRA scale could not be proven from metadata; runtime layer resolution must prove compatibility.",
        }
    return {
        **base,
        "compatible": True,
        "state": "same_scale_structurally_compatible",
        "reason": f"LoRA and active FLUX.2 Klein transformer both resolve to {target_scale.upper()}; runtime layer resolution remains authoritative.",
    }


def filter_klein_bindings(bindings: list[dict[str, Any]] | None, route: dict[str, Any] | None) -> dict[str, Any]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unknown: list[dict[str, Any]] = []
    for index, binding in enumerate(bindings or [], start=1):
        if not isinstance(binding, dict):
            continue
        compatibility = classify_klein_binding_compatibility(binding, route)
        record = {**deepcopy(binding), "flux2_klein_compatibility": compatibility}
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
        "schema": "neo.image.scene_director.flux2_klein_lora_compatibility.v1",
        "phase": PHASE,
        "profile": resolve_klein_profile(route),
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


def validate_klein_sampler_profile(
    workflow: dict[str, Any],
    *,
    sampler_node_id: Any,
    route: dict[str, Any] | None,
) -> dict[str, Any]:
    profile = resolve_klein_profile(route)
    sampler = workflow.get(str(sampler_node_id)) if isinstance(workflow, dict) else None
    errors: list[str] = []
    warnings: list[str] = []
    if profile.get("family") != KLEIN_FAMILY:
        return {"schema": SCHEMA, "phase": PHASE, "applicable": False, "ok": True, "profile": profile, "errors": [], "warnings": []}
    if profile.get("loader") not in KLEIN_LOADERS:
        errors.append(f"FLUX.2 Klein Scene Director requires diffusion_model or gguf loader, got '{profile.get('loader')}'.")
    if not isinstance(sampler, dict) or str(sampler.get("class_type") or "") not in {"KSampler", "KSamplerAdvanced"}:
        errors.append("FLUX.2 Klein Scene Director could not resolve the provider sampler.")
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
    positive_node = _ref_node(workflow, inputs.get("positive"))
    positive_class = str((positive_node or {}).get("class_type") or "")
    if cfg is not None and abs(cfg - KLEIN_COMFY_CFG) > 1e-6:
        errors.append(f"FLUX.2 Klein Neo provider CFG must remain {KLEIN_COMFY_CFG}; got {cfg!r}.")
    if negative_class != "ConditioningZeroOut":
        errors.append("FLUX.2 Klein must preserve zeroed negative conditioning via ConditioningZeroOut.")
    if positive_class != "FluxGuidance":
        errors.append("FLUX.2 Klein must preserve FluxGuidance as the sampler's positive conditioning input.")
    if profile.get("variant_kind") == "distilled" and steps is not None and steps != KLEIN_DISTILLED_REFERENCE_STEPS:
        warnings.append(f"Distilled Klein is normally a {KLEIN_DISTILLED_REFERENCE_STEPS}-step route; Scene Director preserves the provider/user step count ({steps}) instead of rewriting it.")
    if profile.get("variant_kind") == "base" and steps is not None and steps < 20:
        warnings.append("Klein Base is running a low step count; Scene Director preserves it rather than silently converting the route.")
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "applicable": True,
        "ok": not errors,
        "profile": profile,
        "steps": steps,
        "cfg": cfg,
        "positive_class": positive_class,
        "negative_class": negative_class,
        "errors": errors,
        "warnings": warnings,
        "single_sampler_policy": True,
        "scene_director_may_change_sampler_profile": False,
    }


def klein_full_support_contract(route: dict[str, Any] | None) -> dict[str, Any]:
    profile = resolve_klein_profile(route)
    supported = (
        profile.get("family") == KLEIN_FAMILY
        and profile.get("loader") in KLEIN_LOADERS
        and profile.get("mode") in KLEIN_MODES
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
        "scale_compatibility_required": True,
        "base_distilled_cross_variant_auto_compatible": False,
        "live_gpu_validation_external_to_static_suite": True,
        "reason": "" if supported else "Route is outside the SD-28.5 FLUX.2 Klein support contract.",
    }


__all__ = [
    "PHASE", "SCHEMA", "KLEIN_FAMILY", "KLEIN_LOADERS", "KLEIN_MODES", "KLEIN_SCALES",
    "KLEIN_SIGNATURES", "normalize_klein_family", "resolve_klein_profile",
    "classify_klein_binding_compatibility", "filter_klein_bindings",
    "validate_klein_sampler_profile", "klein_full_support_contract",
]
