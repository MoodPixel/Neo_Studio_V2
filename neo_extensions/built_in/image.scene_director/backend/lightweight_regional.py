from __future__ import annotations

from copy import deepcopy
from typing import Any

from .execution_strategy import (
    ENGINE_LIGHTWEIGHT_REGIONAL,
    EXECUTION_STRATEGY_PHASE,
    REGIONAL_NEGATIVE_FAMILIES,
    ZERO_NEGATIVE_FAMILIES,
    normalize_scene_director_family,
    resolve_scene_director_execution_strategy,
)
from .prompt_authority import PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY, normalize_prompt_authority
from .regional_lora_delta import build_regional_lora_delta_contract, apply_regional_lora_delta
from .krea2_support import KREA2_FAMILIES, validate_krea2_sampler_profile
from .krea2_regional_external import (
    KREA2_APPLY_REGIONAL,
    KREA2_REGIONAL_BUILDER,
    KREA2_REGIONAL_ENGINE,
    KREA2_REGIONAL_NODE_REPO,
    apply_krea2_regional_external,
)
from .flux2_klein_support import KLEIN_FAMILY, validate_klein_sampler_profile
from .z_image_support import Z_IMAGE_FAMILIES, validate_z_image_sampler_profile
from .modern_subject_authority import build_modern_subject_authority, compile_regional_subject_prompt, is_character_region, merge_subject_authority_prompt

EXTENSION_ID = "image.scene_director"
LIGHTWEIGHT_REGIONAL_SCHEMA = "neo.image.scene_director.lightweight_regional.runtime.v9"
LIGHTWEIGHT_RUNTIME_PROOF_SCHEMA = "neo.image.scene_director.lightweight_regional.runtime_proof.v7"


def _ref(value: Any, fallback: list[Any]) -> list[Any]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [str(value[0]), value[1]]
    return list(fallback)


def _node_names(nodes: Any) -> set[str]:
    if isinstance(nodes, dict):
        names = {str(k) for k in nodes.keys()}
        for value in nodes.values():
            if isinstance(value, str):
                names.add(value)
            elif isinstance(value, dict):
                name = value.get("class_type") or value.get("name") or value.get("display_name")
                if name:
                    names.add(str(name))
        return names
    if isinstance(nodes, (list, tuple, set)):
        return {str(item) for item in nodes}
    return set()


def _next_numeric_id(workflow: dict[str, Any]) -> int:
    values: list[int] = []
    for key in workflow:
        try:
            values.append(int(str(key)))
        except Exception:
            continue
    return max(values, default=0) + 1


def _sampler_ids(workflow: dict[str, Any]) -> list[str]:
    return sorted(
        str(node_id)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and str(node.get("class_type") or "") in {"KSampler", "KSamplerAdvanced"}
    )


def _route_value(route: dict[str, Any], key: str, default: Any = None) -> Any:
    for source_key in ("actual_params", "params"):
        source = route.get(source_key)
        if isinstance(source, dict) and source.get(key) not in (None, ""):
            return source.get(key)
    if route.get(key) not in (None, ""):
        return route.get(key)
    return default


def _canvas_size(workflow: dict[str, Any], route: dict[str, Any]) -> tuple[int, int]:
    try:
        width = int(float(_route_value(route, "width", 0) or 0))
        height = int(float(_route_value(route, "height", 0) or 0))
    except Exception:
        width = height = 0
    if width > 0 and height > 0:
        return max(64, width), max(64, height)
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if inputs.get("width") and inputs.get("height"):
            try:
                return max(64, int(inputs["width"])), max(64, int(inputs["height"]))
            except Exception:
                continue
    return 1024, 1024


def _pixel_rect(region: dict[str, Any], width: int, height: int) -> tuple[int, int, int, int]:
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
    try:
        x = float(bbox.get("x", 0.0) or 0.0)
        y = float(bbox.get("y", 0.0) or 0.0)
        w = float(bbox.get("w", 1.0) or 1.0)
        h = float(bbox.get("h", 1.0) or 1.0)
    except Exception:
        x, y, w, h = 0.0, 0.0, 1.0, 1.0
    if abs(x) <= 1.0 and abs(w) <= 1.0:
        left = int(round(width * x))
        right = int(round(width * (x + w)))
    else:
        left = int(round(x))
        right = int(round(x + w))
    if abs(y) <= 1.0 and abs(h) <= 1.0:
        top = int(round(height * y))
        bottom = int(round(height * (y + h)))
    else:
        top = int(round(y))
        bottom = int(round(y + h))
    left = max(0, min(width - 1, left))
    top = max(0, min(height - 1, top))
    right = max(left + 1, min(width, right))
    bottom = max(top + 1, min(height, bottom))
    return left, top, right - left, bottom - top


def _feather_value(region: dict[str, Any], region_width: int, region_height: int) -> int:
    mask = region.get("mask") if isinstance(region.get("mask"), dict) else {}
    raw = mask.get("feather", region.get("mask_feather", region.get("feather", 0)))
    try:
        value = max(0, int(round(float(raw or 0))))
    except Exception:
        value = 0
    return min(value, max(0, region_width // 2), max(0, region_height // 2))


def _find_flux_guidance(workflow: dict[str, Any], positive_ref: list[Any], route: dict[str, Any]) -> float:
    node = workflow.get(str(positive_ref[0])) if positive_ref else None
    if isinstance(node, dict) and str(node.get("class_type") or "") == "FluxGuidance":
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        try:
            return float(inputs.get("guidance", 1.0))
        except Exception:
            pass
    try:
        return float(_route_value(route, "flux_guidance", _route_value(route, "guidance", 1.0)) or 1.0)
    except Exception:
        return 1.0


def _sampler_snapshot(node: dict[str, Any]) -> dict[str, Any]:
    inputs = deepcopy(node.get("inputs") if isinstance(node.get("inputs"), dict) else {})
    return {
        "class_type": node.get("class_type"),
        "inputs": inputs,
        "non_conditioning_inputs": {k: deepcopy(v) for k, v in inputs.items() if k not in {"positive", "negative"}},
    }


def _no_patch(
    workflow: dict[str, Any],
    *,
    validation: dict[str, Any] | None,
    model_ref: list[Any],
    clip_ref: list[Any],
    sampler_node_id: str,
    reason: str,
    strategy: dict[str, Any],
) -> dict[str, Any]:
    patch = {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": EXECUTION_STRATEGY_PHASE,
        "patch_type": "scene_director_lightweight_regional_prompt",
        "applied": False,
        "mutated": False,
        "node": None,
        "node_class": None,
        "nodes_added": [],
        "sampler_node_id": str(sampler_node_id),
        "sampler_rewired": False,
        "workflow_patch_allowed": False,
        "scene_director_engine": strategy.get("engine"),
        "scene_director_execution_strategy": deepcopy(strategy),
        "scene_director_regional_lora_applied": False,
        "scene_director_regional_lora_status": "not_executed",
        "reason": reason,
    }
    return {
        "workflow": deepcopy(workflow),
        "workflow_patch": patch,
        "validation": validation or {},
        "model_ref": list(model_ref),
        "clip_ref": list(clip_ref),
        "positive_ref": None,
        "negative_ref": None,
        "mutated": False,
        "changed": False,
        "extension_id": EXTENSION_ID,
        "phase": EXECUTION_STRATEGY_PHASE,
    }



def _krea2_external_patch_result(
    graph: dict[str, Any],
    *,
    validation: dict[str, Any],
    strategy: dict[str, Any],
    route_data: dict[str, Any],
    available_nodes: Any,
    model_output_ref: list[Any],
    clip_output_ref: list[Any],
    sampler_key: str,
    krea2_sampler_profile: dict[str, Any],
) -> dict[str, Any]:
    """Compile IMG-SD3 Krea2 through the external Krea2 Regional runtime.

    Neo retains loader, latent, sampler, decode, img2img/inpaint and finish-pass
    ownership.  Only MODEL + Krea regional CONDITIONING are delegated to the
    external Builder/Apply nodes proven by the user's GGUF benchmark workflow.
    """
    width, height = _canvas_size(graph, route_data)
    adapter = apply_krea2_regional_external(
        graph,
        validation=validation,
        strategy=strategy,
        route=route_data,
        available_nodes=available_nodes,
        model_ref=model_output_ref,
        clip_ref=clip_output_ref,
        sampler_node_id=sampler_key,
        width=width,
        height=height,
    )
    runtime_proof = deepcopy(adapter.get("runtime_proof") or {})
    lora_contract = deepcopy(adapter.get("lora_contract") or {})
    nodes_added = [str(item) for item in (adapter.get("nodes_added") or []) if item is not None]
    applied = bool(adapter.get("applied"))
    reason = str(adapter.get("reason") or "")
    block = validation.get("block") if isinstance(validation.get("block"), dict) else {}
    regions = ((block.get("inputs") or {}).get("regions") if isinstance(block.get("inputs"), dict) else []) or []

    normalized_validation = dict(validation)
    normalized_validation["node_status"] = {
        "required": True,
        "custom_scene_director_node_required": False,
        "engine": KREA2_REGIONAL_ENGINE,
        "available": applied or not runtime_proof.get("missing_nodes"),
        "selected_node": KREA2_APPLY_REGIONAL if applied else None,
        "required_node_classes": [KREA2_REGIONAL_BUILDER, KREA2_APPLY_REGIONAL],
        "missing_node_classes": list(runtime_proof.get("missing_nodes") or []),
        "regional_lora_runtime_node": KREA2_APPLY_REGIONAL,
        "regional_lora_runtime_node_status": str(runtime_proof.get("regional_lora_compile_status") or ""),
        "external_runtime_repo": KREA2_REGIONAL_NODE_REPO,
        "fallback_policy": "never_fallback_to_neoregionalloradelta_or_global_lora",
    }

    previous_sampler = graph.get(sampler_key) if isinstance(graph.get(sampler_key), dict) else {}
    previous_inputs = previous_sampler.get("inputs") if isinstance(previous_sampler.get("inputs"), dict) else {}
    previous_model_ref = deepcopy(previous_inputs.get("model") or model_output_ref)
    previous_positive_ref = deepcopy(previous_inputs.get("positive"))
    previous_negative_ref = deepcopy(previous_inputs.get("negative"))

    patch = {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": EXECUTION_STRATEGY_PHASE,
        "patch_type": "scene_director_krea2_regional_external",
        "applied": applied,
        "mutated": applied,
        "node": KREA2_APPLY_REGIONAL if applied else None,
        "node_class": KREA2_APPLY_REGIONAL if applied else None,
        "node_classes": sorted({
            str((adapter.get("workflow") or {}).get(node_id, {}).get("class_type") or "")
            for node_id in nodes_added
            if isinstance((adapter.get("workflow") or {}).get(node_id), dict)
        } - {""}),
        "nodes_added": nodes_added,
        "scene_node_id": None,
        "sampler_node_id": sampler_key,
        "sampler_rewired": bool(applied),
        "conditioning_wrapper_rewired": bool(runtime_proof.get("conditioning_wrapper_rewired")),
        "conditioning_rewire_location": str(runtime_proof.get("conditioning_rewire_location") or "sampler_inputs"),
        "inpaint_conditioning_wrapper_node_id": str(runtime_proof.get("inpaint_conditioning_wrapper_node_id") or ""),
        "regions": len([r for r in regions if isinstance(r, dict) and r.get("enabled") is not False and r.get("visible") is not False]),
        "subject_count": int((block.get("metadata") or {}).get("subject_count") or validation.get("subject_count") or 0),
        "detail_region_count": int((block.get("metadata") or {}).get("detail_region_count") or validation.get("detail_region_count") or 0),
        "route": validation.get("route") or route_data,
        "route_state": validation.get("route_state") or "available",
        "workflow_readiness_state": validation.get("route_state") or "available",
        "workflow_patch_allowed": applied,
        "node_status": normalized_validation.get("node_status"),
        "fallback_policy": "never_fallback_to_neoregionalloradelta_or_global_lora",
        "previous_model_ref": previous_model_ref,
        "patched_model_ref": deepcopy(adapter.get("model_ref") or previous_model_ref),
        "clip_ref": deepcopy(clip_output_ref),
        "patched_clip_ref": deepcopy(clip_output_ref),
        "previous_positive_ref": previous_positive_ref,
        "previous_negative_ref": previous_negative_ref,
        "patched_positive_ref": deepcopy(adapter.get("positive_ref") or previous_positive_ref),
        "patched_negative_ref": deepcopy(adapter.get("negative_ref") or previous_negative_ref),
        "conditioning_anchor_positive_ref": deepcopy(adapter.get("positive_ref") or previous_positive_ref),
        "conditioning_anchor_negative_ref": deepcopy(adapter.get("negative_ref") or previous_negative_ref),
        "scene_director_engine": KREA2_REGIONAL_ENGINE,
        "scene_director_execution_strategy": deepcopy(strategy),
        "scene_director_modern_core": deepcopy(runtime_proof.get("modern_scene_director_core") or {}),
        "scene_director_lightweight_regional_prompt": {
            "schema": LIGHTWEIGHT_REGIONAL_SCHEMA,
            "phase": EXECUTION_STRATEGY_PHASE,
            "status": "external_runtime_armed" if applied else str(runtime_proof.get("regional_lora_compile_status") or "blocked"),
            "family": str(strategy.get("family") or route_data.get("family") or ""),
            "canvas": {"width": width, "height": height},
            "external_engine": KREA2_REGIONAL_ENGINE,
            "external_runtime_repo": KREA2_REGIONAL_NODE_REPO,
            "builder_node_id": str(adapter.get("builder_node_id") or ""),
            "apply_node_id": str(adapter.get("apply_node_id") or ""),
            "layout_in_base": runtime_proof.get("layout_in_base"),
            "adaptive_masks": runtime_proof.get("adaptive_masks"),
            "exclusive_masks": runtime_proof.get("exclusive_masks"),
            "restrict_img_attn": runtime_proof.get("restrict_img_attn"),
            "regional_prompt_policy": "external_builder_joint_attention",
            "global_model_weight_mutation": False,
        },
        "scene_director_lightweight_runtime_proof": runtime_proof,
        "scene_director_extra_samplers_added": 0,
        "scene_director_character_lock_execution": {"status": "not_used_krea2_regional_external", "phase": EXECUTION_STRATEGY_PHASE},
        "scene_director_regional_lora_contract": lora_contract,
        "scene_director_regional_lora_graph_patch": {
            "status": str(runtime_proof.get("regional_lora_compile_status") or ""),
            "applied": applied,
            "nodes_added": nodes_added,
            "builder_node_id": str(adapter.get("builder_node_id") or ""),
            "apply_node_id": str(adapter.get("apply_node_id") or ""),
            "external_runtime_repo": KREA2_REGIONAL_NODE_REPO,
            "reason": reason,
        },
        "scene_director_regional_lora_applied": bool(applied and int(lora_contract.get("route_count") or 0) > 0),
        "scene_director_regional_lora_status": str(runtime_proof.get("regional_lora_compile_status") or lora_contract.get("status") or ""),
        "scene_director_regional_lora_nodes_added": [str(adapter.get("apply_node_id"))] if adapter.get("apply_node_id") and int(lora_contract.get("route_count") or 0) > 0 else [],
        "scene_director_regional_lora_runtime_gpu_proven": False,
        "scene_director_regional_lora_clip_delta_execution": lora_contract.get("clip_delta_execution"),
        "scene_director_krea2_sampler_profile": deepcopy(krea2_sampler_profile),
        "scene_director_krea2_lora_compatibility": deepcopy(lora_contract.get("binding_compatibility") or {"status": "owned_by_external_runtime", "route_count": int(lora_contract.get("route_count") or 0)}),
        "reason": reason,
        "notes": [
            "IMG-SD3 delegates Krea2 regional prompt and region-assigned LoRA ownership to januspluto/ComfyUI-Krea2-Regional while preserving Neo's provider-owned GGUF/native loader, latent, sampler and decode path.",
            "Adaptive masks default to refine boxes, exclusive masks default on, and restrict_img_attn defaults off based on the validated GGUF benchmark used for this integration.",
            "Krea2 does not silently fall back to NeoRegionalLoRADelta or global LoRA loading when the external runtime is unavailable.",
        ],
    }
    return {
        "workflow": deepcopy(adapter.get("workflow") or graph),
        "workflow_patch": patch,
        "validation": normalized_validation,
        "model_ref": list(adapter.get("model_ref") or model_output_ref),
        "clip_ref": list(clip_output_ref),
        "positive_ref": deepcopy(adapter.get("positive_ref")),
        "negative_ref": deepcopy(adapter.get("negative_ref")),
        "mutated": applied,
        "changed": applied,
        "extension_id": EXTENSION_ID,
        "phase": EXECUTION_STRATEGY_PHASE,
    }


def build_lightweight_regional_plan(
    route: dict[str, Any] | None = None,
    *,
    scene_graph: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = resolve_scene_director_execution_strategy(route)
    if strategy.get("engine") != ENGINE_LIGHTWEIGHT_REGIONAL:
        raise ValueError("Lightweight regional planning requires a modern Scene Director target family.")
    return {
        "schema": LIGHTWEIGHT_REGIONAL_SCHEMA,
        "phase": EXECUTION_STRATEGY_PHASE,
        "status": strategy.get("status"),
        "executable": bool(strategy.get("execution_enabled")),
        "workflow_mutation_allowed": bool(strategy.get("workflow_patch_ready")),
        "model_mutation_allowed": False,
        "model_wrapper_insertion_allowed": bool((strategy.get("regional_lora") or {}).get("supported")),
        "sampler_count_mutation_allowed": False,
        "scene_graph_preserved": bool(scene_graph),
        "scene_graph": deepcopy(scene_graph) if isinstance(scene_graph, dict) else None,
        "execution_strategy": deepcopy(strategy),
        "regional_prompt": deepcopy(strategy.get("regional_prompt") or {}),
        "regional_lora": deepcopy(strategy.get("regional_lora") or {}),
        "sampler_policy": deepcopy(strategy.get("sampler_policy") or {}),
        "repair_policy": deepcopy(strategy.get("repair_policy") or {}),
        "blockers": [] if strategy.get("execution_enabled") else ["lightweight_regional_route_not_executable"],
        "policy": "SD-28.7 keeps the existing sampler and enforces the release lock after compilation. Krea2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo regional prompts + regional LoRA are full-support lightweight routes; family-specific LoRA wrappers are spatial-scope filtered and never mutate global weights or CLIP.",
    }


def _validate_payload(payload: Any, route: dict[str, Any], available_nodes: Any) -> dict[str, Any]:
    # Lazy import keeps the graph compiler independently testable and avoids
    # importing the legacy V054 patcher on lightweight routes.
    from .validation import validate_and_normalize_payload

    return validate_and_normalize_payload(
        payload,
        backend=str(route.get("backend") or route.get("provider_id") or "comfyui"),
        family=str(route.get("family") or ""),
        loader=str(route.get("loader") or ""),
        workflow_mode=str(route.get("workflow_mode") or route.get("mode") or "generate"),
        object_info=available_nodes,
        node_status=available_nodes,
    )



def _conditioning_parent_refs(workflow: dict[str, Any], ref: Any) -> list[list[Any]]:
    """Return conditioning parents for the provider-owned conditioning chain.

    The output index matters for InpaintModelConditioning: output 0 is positive,
    output 1 is negative, and output 2 is latent.  Following this chain lets
    IMG-SD1D edit the existing upstream CLIP text while preserving wrappers and
    metadata attached by native inpaint conditioning.
    """
    if not isinstance(ref, (list, tuple)) or len(ref) < 2:
        return []
    node = workflow.get(str(ref[0])) if isinstance(workflow, dict) else None
    if not isinstance(node, dict):
        return []
    class_type = str(node.get("class_type") or "")
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    try:
        output_index = int(ref[1])
    except Exception:
        output_index = 0

    if class_type == "InpaintModelConditioning":
        key = "positive" if output_index == 0 else ("negative" if output_index == 1 else "")
        value = inputs.get(key) if key else None
        return [list(value)] if isinstance(value, (list, tuple)) and len(value) >= 2 else []
    if class_type in {"FluxGuidance", "ConditioningZeroOut", "ConditioningSetMask"}:
        value = inputs.get("conditioning")
        return [list(value)] if isinstance(value, (list, tuple)) and len(value) >= 2 else []
    if class_type == "ConditioningCombine":
        refs: list[list[Any]] = []
        for key in ("conditioning_1", "conditioning_2"):
            value = inputs.get(key)
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                refs.append(list(value))
        return refs
    return []


def _resolve_inpaint_conditioning_wrapper(
    workflow: dict[str, Any],
    positive_ref: Any,
    negative_ref: Any,
) -> dict[str, Any] | None:
    """Resolve a shared native InpaintModelConditioning sampler wrapper.

    Comfy's native inpaint route exposes positive/negative/latent as outputs
    0/1/2 of one InpaintModelConditioning node.  Scene Director must combine
    regional conditioning *before* that node so every conditioning entry keeps
    the same source-image/mask metadata.
    """
    if not (
        isinstance(positive_ref, (list, tuple)) and len(positive_ref) >= 2
        and isinstance(negative_ref, (list, tuple)) and len(negative_ref) >= 2
        and str(positive_ref[0]) == str(negative_ref[0])
    ):
        return None
    node_id = str(positive_ref[0])
    node = workflow.get(node_id) if isinstance(workflow, dict) else None
    if not isinstance(node, dict) or str(node.get("class_type") or "") != "InpaintModelConditioning":
        return None
    try:
        positive_index = int(positive_ref[1])
        negative_index = int(negative_ref[1])
    except Exception:
        return None
    if positive_index != 0 or negative_index != 1:
        return None
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    raw_positive = inputs.get("positive")
    raw_negative = inputs.get("negative")
    if not (
        isinstance(raw_positive, (list, tuple)) and len(raw_positive) >= 2
        and isinstance(raw_negative, (list, tuple)) and len(raw_negative) >= 2
    ):
        return None
    return {
        "node_id": node_id,
        "node": node,
        "positive_ref": list(raw_positive),
        "negative_ref": list(raw_negative),
        "sampler_positive_ref": list(positive_ref),
        "sampler_negative_ref": list(negative_ref),
        "latent_ref": [node_id, 2],
    }


def _find_clip_text_sources(workflow: dict[str, Any], ref: Any) -> list[str]:
    found: list[str] = []
    visited: set[tuple[str, str]] = set()

    def walk(current: Any) -> None:
        if not isinstance(current, (list, tuple)) or len(current) < 2:
            return
        node_id = str(current[0])
        key = (node_id, str(current[1]))
        if key in visited:
            return
        visited.add(key)
        node = workflow.get(node_id) if isinstance(workflow, dict) else None
        if not isinstance(node, dict):
            return
        if str(node.get("class_type") or "") == "CLIPTextEncode":
            if node_id not in found:
                found.append(node_id)
            return
        for parent in _conditioning_parent_refs(workflow, current):
            walk(parent)

    walk(ref)
    return found


def _merge_authority_into_provider_text(workflow: dict[str, Any], ref: Any, bridge_text: str) -> dict[str, Any]:
    sources = _find_clip_text_sources(workflow, ref)
    if len(sources) != 1:
        return {
            "applied": False,
            "status": "source_not_unique" if sources else "source_not_found",
            "source_node_ids": sources,
            "conditioning_ref": list(ref) if isinstance(ref, (list, tuple)) else None,
        }
    node_id = sources[0]
    node = workflow.get(node_id)
    inputs = node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else None
    if inputs is None:
        return {"applied": False, "status": "source_inputs_missing", "source_node_ids": sources}
    before = str(inputs.get("text") or "").strip()
    after = merge_subject_authority_prompt(before, bridge_text)
    inputs["text"] = after
    return {
        "applied": bool(after),
        "status": "merged_into_provider_clip_text",
        "source_node_id": node_id,
        "source_node_ids": sources,
        "conditioning_ref": list(ref) if isinstance(ref, (list, tuple)) else None,
        "text_changed": after != before,
        "text_before": before,
        "text_after": after,
    }


def _merge_trigger_terms(prompt: str, binding: dict[str, Any]) -> str:
    """Append LoRA Stack activation terms only to the owning regional prompt."""
    terms: list[str] = []
    owner = binding.get("owner_row") if isinstance(binding.get("owner_row"), dict) else {}
    for value in (
        binding.get("source_record_activation_text"),
        binding.get("source_record_trigger_words"),
        owner.get("source_record_activation_text"),
        owner.get("source_record_trigger_words"),
        owner.get("activation_text"),
        owner.get("trigger_words"),
    ):
        text = str(value or "").strip()
        if text:
            terms.extend(part.strip() for part in text.replace("\n", ",").split(",") if part.strip())
    existing = [part.strip() for part in str(prompt or "").replace("\n", ",").split(",") if part.strip()]
    seen = {item.casefold() for item in existing}
    for term in terms:
        if term.casefold() not in seen:
            existing.append(term)
            seen.add(term.casefold())
    return ", ".join(existing)


def apply_lightweight_regional_prompt_patch(
    workflow: dict[str, Any],
    *,
    payload: Any,
    route: dict[str, Any] | None,
    available_nodes: Any,
    model_ref: list[Any] | tuple[Any, ...] | None = None,
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int = "5",
    **_: Any,
) -> dict[str, Any]:
    """Apply the modern Scene Director engine selected for the active family.

    IMG-SD3 routes Krea2 RAW/Turbo through the external Krea2 Regional Builder/Apply
    engine while preserving Neo's provider sampler/latent path. FLUX.2 Klein and
    Z-Image retain NeoRegionalLoRADelta. No modern route falls back to global LoRA.
    """
    graph = deepcopy(workflow or {})
    route_data = dict(route or {})
    strategy = resolve_scene_director_execution_strategy(route_data)
    family = normalize_scene_director_family(strategy.get("family") or route_data.get("family"))
    model_output_ref = _ref(model_ref, ["1", 0])
    clip_output_ref = _ref(clip_ref, ["2", 0])
    sampler_key = str(sampler_node_id)

    if strategy.get("engine") != ENGINE_LIGHTWEIGHT_REGIONAL or not strategy.get("execution_enabled"):
        return _no_patch(
            graph,
            validation=None,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason=str(strategy.get("reason") or "Lightweight regional execution is not enabled for this route."),
            strategy=strategy,
        )

    validation = _validate_payload(payload, route_data, available_nodes)
    if not validation.get("enabled") or not validation.get("ok") or not validation.get("can_emit_workflow_patch"):
        node_status = validation.get("node_status") if isinstance(validation.get("node_status"), dict) else {}
        missing_nodes = [str(item) for item in (node_status.get("missing_node_classes") or [])]
        if family in KREA2_FAMILIES and any(name in missing_nodes for name in (KREA2_REGIONAL_BUILDER, KREA2_APPLY_REGIONAL)):
            validation_reason = (
                "Krea 2 Scene Director requires januspluto/ComfyUI-Krea2-Regional "
                "(Krea2RegionalBuilder + Krea2ApplyRegional). Install/update the pack and restart ComfyUI."
            )
        else:
            validation_reason = str(validation.get("reason") or node_status.get("missing_reason") or "Scene Director lightweight validation did not allow workflow mutation.")
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason=validation_reason,
            strategy=strategy,
        )

    sampler = graph.get(sampler_key)
    if not isinstance(sampler, dict) or str(sampler.get("class_type") or "") not in {"KSampler", "KSamplerAdvanced"}:
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason=f"Scene Director lightweight engine could not resolve sampler node {sampler_key!r}.",
            strategy=strategy,
        )
    sampler_inputs = sampler.get("inputs") if isinstance(sampler.get("inputs"), dict) else {}
    if "positive" not in sampler_inputs or "negative" not in sampler_inputs:
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason="Scene Director lightweight engine requires sampler positive and negative conditioning inputs.",
            strategy=strategy,
        )

    krea2_sampler_profile = validate_krea2_sampler_profile(
        graph,
        sampler_node_id=sampler_key,
        family=family,
        loader=strategy.get("loader") or route_data.get("loader"),
    ) if family in KREA2_FAMILIES else {"applicable": False, "ok": True}
    if not krea2_sampler_profile.get("ok", True):
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason="Krea2 sampler profile validation failed: " + "; ".join(krea2_sampler_profile.get("errors") or []),
            strategy=strategy,
        )
    klein_sampler_profile = validate_klein_sampler_profile(
        graph,
        sampler_node_id=sampler_key,
        route={**route_data, "family": family, "loader": strategy.get("loader") or route_data.get("loader")},
    ) if family == KLEIN_FAMILY else {"applicable": False, "ok": True}
    if not klein_sampler_profile.get("ok", True):
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason="FLUX.2 Klein sampler profile validation failed: " + "; ".join(klein_sampler_profile.get("errors") or []),
            strategy=strategy,
        )
    z_image_sampler_profile = validate_z_image_sampler_profile(
        graph,
        sampler_node_id=sampler_key,
        route={**route_data, "family": family, "loader": strategy.get("loader") or route_data.get("loader")},
    ) if family in Z_IMAGE_FAMILIES else {"applicable": False, "ok": True}
    if not z_image_sampler_profile.get("ok", True):
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason="Z-Image sampler profile validation failed: " + "; ".join(z_image_sampler_profile.get("errors") or []),
            strategy=strategy,
        )

    before_sampler_ids = _sampler_ids(graph)
    sampler_before = _sampler_snapshot(sampler)
    original_model_input = deepcopy(sampler_inputs.get("model"))
    original_latent_input = deepcopy(sampler_inputs.get("latent_image"))
    original_positive_ref = _ref(sampler_inputs.get("positive"), ["0", 0])
    original_negative_ref = _ref(sampler_inputs.get("negative"), ["0", 0])
    inpaint_wrapper = _resolve_inpaint_conditioning_wrapper(graph, original_positive_ref, original_negative_ref)
    conditioning_positive_ref = list(inpaint_wrapper["positive_ref"]) if inpaint_wrapper else list(original_positive_ref)
    conditioning_negative_ref = list(inpaint_wrapper["negative_ref"]) if inpaint_wrapper else list(original_negative_ref)
    positive_ref = list(conditioning_positive_ref)
    negative_ref = list(conditioning_negative_ref)

    block = validation.get("block") if isinstance(validation.get("block"), dict) else {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    contracts = inputs.get("contracts") if isinstance(inputs.get("contracts"), dict) else {}
    # IMG-SD2: modern Scene Director is a regional LoRA-isolation tool first.
    # Historical SD1C/SD1D cast/subject prompt contracts remain available only
    # behind an explicit future/experimental strict_cast_control flag. Saved
    # contracts_enabled values from older drafts must not mutate modern prompts.
    strict_cast_control = bool(
        params.get("strict_cast_control")
        or contracts.get("strict_cast_control")
        or contracts.get("modern_strict_cast_control")
    )
    modern_contracts = {**contracts, "enabled": strict_cast_control}
    lora_bindings = assets.get("lora_bindings") if isinstance(assets.get("lora_bindings"), list) else []
    prompt_authority = normalize_prompt_authority(params.get("prompt_authority") or (inputs.get("global") or {}).get("prompt_authority"))
    scene_director_only = prompt_authority == PROMPT_AUTHORITY_SCENE_DIRECTOR_ONLY
    width, height = _canvas_size(graph, route_data)

    # IMG-SD3: Krea2 is compiled by the tested external Krea2 Regional engine.
    # Bypass SD2's internal masked-conditioning + NeoRegionalLoRADelta path entirely.
    if family in KREA2_FAMILIES:
        return _krea2_external_patch_result(
            graph,
            validation=validation,
            strategy=strategy,
            route_data=route_data,
            available_nodes=available_nodes,
            model_output_ref=model_output_ref,
            clip_output_ref=clip_output_ref,
            sampler_key=sampler_key,
            krea2_sampler_profile=krea2_sampler_profile,
        )

    flux_guidance = _find_flux_guidance(graph, conditioning_positive_ref, route_data) if family == "flux2_klein" else None
    subject_authority = build_modern_subject_authority(
        regions,
        contracts=modern_contracts,
        canvas_width=width,
        canvas_height=height,
    )

    regional_lora_contract = build_regional_lora_delta_contract(
        route_data,
        bindings=lora_bindings,
        regions=regions,
        canvas={"width": width, "height": height},
    )
    compatibility_meta = regional_lora_contract.get("binding_compatibility") if isinstance(regional_lora_contract.get("binding_compatibility"), dict) else {}
    effective_trigger_bindings = compatibility_meta.get("accepted") if family in (set(KREA2_FAMILIES) | {KLEIN_FAMILY} | set(Z_IMAGE_FAMILIES)) and isinstance(compatibility_meta.get("accepted"), list) else lora_bindings
    trigger_binding_by_region = {
        str(binding.get("region_id") or ""): binding
        for binding in effective_trigger_bindings
        if isinstance(binding, dict) and str(binding.get("region_id") or "")
    }

    prompt_regions = [region for region in regions if isinstance(region, dict) and str(region.get("prompt") or "").strip()]
    negative_regions = [region for region in regions if isinstance(region, dict) and str(region.get("negative_prompt") or "").strip()]
    has_regional_lora_request = bool(regional_lora_contract.get("route_count"))
    if not prompt_regions and not has_regional_lora_request:
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason="No active Scene Director regional prompt or regional LoRA route was found.",
            strategy=strategy,
        )

    # Conditioning nodes are only required when prompt/negative lanes need them.
    names = _node_names(available_nodes)
    required = [str(item) for item in (strategy.get("required_comfy_nodes") or [])] if prompt_regions else []
    missing = sorted(name for name in required if available_nodes is not None and name not in names)
    if missing:
        return _no_patch(
            graph,
            validation=validation,
            model_ref=model_output_ref,
            clip_ref=clip_output_ref,
            sampler_node_id=sampler_key,
            reason="Missing required Comfy built-in nodes: " + ", ".join(missing),
            strategy=strategy,
        )

    next_id = _next_numeric_id(graph)
    nodes_added: list[str] = []
    lane_records: list[dict[str, Any]] = []
    negative_lane_records: list[dict[str, Any]] = []
    suppressed_negative_regions: list[str] = []
    subject_authority_node_ids: list[str] = []
    subject_authority_bridge_ref: list[Any] | None = None
    subject_authority_negative_ref: list[Any] | None = None
    subject_authority_merge: dict[str, Any] = {"applied": False, "status": "inactive"}
    subject_authority_negative_merge: dict[str, Any] = {"applied": False, "status": "inactive"}

    def add_node(class_type: str, inputs_payload: dict[str, Any]) -> list[Any]:
        nonlocal next_id
        node_id = str(next_id)
        next_id += 1
        graph[node_id] = {"class_type": class_type, "inputs": inputs_payload}
        nodes_added.append(node_id)
        return [node_id, 0]

    blank_mask_ref: list[Any] | None = None
    if prompt_regions or (family in REGIONAL_NEGATIVE_FAMILIES and negative_regions):
        blank_mask_ref = add_node("SolidMask", {"value": 0.0, "width": int(width), "height": int(height)})

    if scene_director_only and prompt_regions:
        positive_ref = add_node("ConditioningZeroOut", {"conditioning": list(conditioning_positive_ref)})
        negative_ref = add_node("ConditioningZeroOut", {"conditioning": list(conditioning_negative_ref)})

    # IMG-SD1D: subject structure belongs inside the provider's existing global
    # semantic text, not in a second full-canvas conditioning entry.  Mutating
    # the one upstream CLIPTextEncode preserves the provider wrapper chain
    # (FluxGuidance/InpaintModelConditioning/etc.) and prevents style dilution.
    subject_bridge_text = str(subject_authority.get("global_bridge") or "").strip()
    if subject_authority.get("enabled") and subject_bridge_text and not scene_director_only:
        subject_authority_merge = _merge_authority_into_provider_text(graph, conditioning_positive_ref, subject_bridge_text)
        if not subject_authority_merge.get("applied"):
            return _no_patch(
                graph,
                validation=validation,
                model_ref=model_output_ref,
                clip_ref=clip_output_ref,
                sampler_node_id=sampler_key,
                reason=(
                    "Scene Director subject authority could not resolve one provider global CLIP text source for a safe IMG-SD1D merge "
                    f"({subject_authority_merge.get('status')})."
                ),
                strategy=strategy,
            )
        subject_authority_bridge_ref = list(conditioning_positive_ref)

    # Scene-Director-only is a legacy isolation mode on modern routes.  Do not
    # reintroduce SD1C's styleless full-canvas lane just to force count authority.
    # Regional one-subject contracts still execute, while the global bridge is
    # explicitly reported as skipped by prompt-authority policy.
    elif subject_authority.get("enabled") and subject_bridge_text and scene_director_only:
        subject_authority_merge = {
            "applied": False,
            "status": "skipped_scene_director_only",
            "conditioning_ref": list(conditioning_positive_ref),
        }

    # Base/non-turbo families can merge the count-aware negative contract into
    # the provider's existing negative CLIP text. Turbo/zero-negative families
    # remain untouched. InpaintModelConditioning is preserved because the merge
    # happens at its upstream text source rather than after the wrapper.
    subject_negative_text = str(subject_authority.get("global_negative_bridge") or "").strip()
    if subject_authority.get("enabled") and subject_negative_text and family in REGIONAL_NEGATIVE_FAMILIES:
        subject_authority_negative_merge = _merge_authority_into_provider_text(graph, conditioning_negative_ref, subject_negative_text)
        if subject_authority_negative_merge.get("applied"):
            subject_authority_negative_ref = list(conditioning_negative_ref)

    for index, region in enumerate(prompt_regions, start=1):
        region_id = str(region.get("id") or f"scene_region_{index}")
        authored_prompt = _merge_trigger_terms(str(region.get("prompt") or "").strip(), trigger_binding_by_region.get(region_id, {}))
        prompt = compile_regional_subject_prompt(authored_prompt, subject_authority, region=region)
        x, y, rw, rh = _pixel_rect(region, width, height)
        feather = _feather_value(region, rw, rh)
        try:
            strength = max(0.0, min(10.0, float(region.get("strength", 1.0) or 1.0)))
        except Exception:
            strength = 1.0
        local_mask_ref = add_node("SolidMask", {"value": 1.0, "width": int(rw), "height": int(rh)})
        if feather > 0:
            local_mask_ref = add_node("FeatherMask", {
                "mask": list(local_mask_ref), "left": feather, "top": feather, "right": feather, "bottom": feather,
            })
        full_mask_ref = add_node("MaskComposite", {
            "destination": list(blank_mask_ref or ["0", 0]), "source": list(local_mask_ref),
            "x": int(x), "y": int(y), "operation": "add",
        })
        encoded_ref = add_node("CLIPTextEncode", {"text": prompt, "clip": list(clip_output_ref)})
        conditioning_ref = encoded_ref
        adapter_node = None
        if family == "flux2_klein":
            conditioning_ref = add_node("FluxGuidance", {"conditioning": list(encoded_ref), "guidance": float(flux_guidance or 1.0)})
            adapter_node = conditioning_ref[0]
        # ComfyUI's current multidimensional sampler path cannot resolve
        # ``set_area_to_bounds`` for image models whose latent carries an
        # extra singleton dimension (for example Krea2/Qwen image latents).
        # ``mask bounds`` flips that flag on inside ConditioningSetMask and
        # eventually sends a 3D mask into get_mask_aabb(), which only handles
        # 2D masks.  The mask itself already provides the spatial authority,
        # so modern/lightweight Scene Director must use the multidim-safe
        # default area policy instead of asking ComfyUI to derive bounds.
        masked_ref = add_node("ConditioningSetMask", {
            "conditioning": list(conditioning_ref), "mask": list(full_mask_ref),
            "strength": float(strength), "set_cond_area": "default",
        })
        positive_ref = add_node("ConditioningCombine", {"conditioning_1": list(positive_ref), "conditioning_2": list(masked_ref)})
        lane_records.append({
            "region_id": region_id,
            "label": region.get("label") or region_id,
            "role": region.get("type") or region.get("role") or "object",
            "prompt_present": True,
            "authored_prompt_with_regional_lora_triggers": authored_prompt,
            "prompt_with_regional_lora_triggers": prompt,
            "subject_contract_applied": bool(is_character_region(region) and subject_authority.get("enabled") and subject_authority.get("regional_subject_contract")),
            "regional_lora_trigger_terms_local_only": bool(trigger_binding_by_region.get(region_id)),
            "rect_px": {"x": x, "y": y, "w": rw, "h": rh},
            "feather": feather,
            "strength": strength,
            "mask_ref": list(full_mask_ref),
            "encode_ref": list(encoded_ref),
            "family_adapter_ref": [adapter_node, 0] if adapter_node else None,
            "masked_conditioning_ref": list(masked_ref),
        })

    if family in REGIONAL_NEGATIVE_FAMILIES:
        for index, region in enumerate(negative_regions, start=1):
            region_id = str(region.get("id") or f"scene_region_{index}")
            text = str(region.get("negative_prompt") or "").strip()
            if not text:
                continue
            x, y, rw, rh = _pixel_rect(region, width, height)
            feather = _feather_value(region, rw, rh)
            try:
                strength = max(0.0, min(10.0, float(region.get("strength", 1.0) or 1.0)))
            except Exception:
                strength = 1.0
            local_mask_ref = add_node("SolidMask", {"value": 1.0, "width": int(rw), "height": int(rh)})
            if feather > 0:
                local_mask_ref = add_node("FeatherMask", {
                    "mask": list(local_mask_ref), "left": feather, "top": feather, "right": feather, "bottom": feather,
                })
            full_mask_ref = add_node("MaskComposite", {
                "destination": list(blank_mask_ref or ["0", 0]), "source": list(local_mask_ref),
                "x": int(x), "y": int(y), "operation": "add",
            })
            encoded_ref = add_node("CLIPTextEncode", {"text": text, "clip": list(clip_output_ref)})
            masked_ref = add_node("ConditioningSetMask", {
                "conditioning": list(encoded_ref), "mask": list(full_mask_ref),
                "strength": float(strength), "set_cond_area": "default",
            })
            negative_ref = add_node("ConditioningCombine", {"conditioning_1": list(negative_ref), "conditioning_2": list(masked_ref)})
            negative_lane_records.append({
                "region_id": region_id,
                "rect_px": {"x": x, "y": y, "w": rw, "h": rh},
                "feather": feather,
                "strength": strength,
                "mask_ref": list(full_mask_ref),
                "masked_conditioning_ref": list(masked_ref),
            })
    elif family in ZERO_NEGATIVE_FAMILIES and prompt_regions:
        suppressed_negative_regions = [str(region.get("id") or "") for region in negative_regions]
        negative_ref = add_node("ConditioningZeroOut", {"conditioning": list(positive_ref)})

    directional_conflicts = list(subject_authority.get("directional_conflicts") or [])
    if directional_conflicts:
        validation = dict(validation)
        validation_warnings = validation.setdefault("warnings", [])
        validation_rows = validation.setdefault("validation", [])
        for conflict in directional_conflicts:
            warning = {
                "extension_id": EXTENSION_ID,
                "level": "warning",
                "field": "inputs.regions.prompt",
                "code": str(conflict.get("code") or "prompt_direction_vs_mask_position"),
                "region_id": str(conflict.get("region_id") or ""),
                "message": str(conflict.get("message") or "Regional prompt direction conflicts with its mask position."),
            }
            validation_warnings.append(deepcopy(warning))
            validation_rows.append(deepcopy(warning))

    # Native inpaint owns source-image/mask metadata inside
    # InpaintModelConditioning.  Combine Scene Director lanes into that node's
    # inputs, then leave the sampler connected to outputs 0/1/2 unchanged.
    # Non-inpaint routes continue to rewire the sampler conditioning directly.
    if inpaint_wrapper:
        wrapper_inputs = inpaint_wrapper["node"].setdefault("inputs", {})
        wrapper_inputs["positive"] = list(positive_ref)
        wrapper_inputs["negative"] = list(negative_ref)
        sampler["inputs"]["positive"] = list(original_positive_ref)
        sampler["inputs"]["negative"] = list(original_negative_ref)
        sampler_conditioning_rewired = False
        conditioning_wrapper_rewired = bool(lane_records or negative_lane_records or suppressed_negative_regions or scene_director_only)
    else:
        sampler["inputs"]["positive"] = list(positive_ref)
        sampler["inputs"]["negative"] = list(negative_ref)
        sampler_conditioning_rewired = bool(lane_records or negative_lane_records or suppressed_negative_regions or scene_director_only)
        conditioning_wrapper_rewired = False

    # Insert the regional MODEL wrapper after any LoRA Stack global MODEL patch,
    # then rewire provider model consumers generically. This preserves wrappers
    # such as DifferentialDiffusion instead of bypassing them at the sampler.
    lora_graph_result = apply_regional_lora_delta(
        graph,
        contract=regional_lora_contract,
        model_ref=model_output_ref,
        available_nodes=available_nodes,
    )
    graph = lora_graph_result["workflow"]
    patched_model_ref = list(lora_graph_result.get("model_ref") or model_output_ref)
    lora_graph_meta = lora_graph_result.get("metadata") if isinstance(lora_graph_result.get("metadata"), dict) else {}
    lora_nodes_added = list(lora_graph_meta.get("nodes_added") or [])
    nodes_added.extend(node_id for node_id in lora_nodes_added if node_id not in nodes_added)
    sampler = graph.get(sampler_key) if isinstance(graph.get(sampler_key), dict) else sampler

    after_sampler_ids = _sampler_ids(graph)
    sampler_after = _sampler_snapshot(sampler)
    final_model_input = deepcopy(sampler_after["inputs"].get("model"))
    final_latent_input = deepcopy(sampler_after["inputs"].get("latent_image"))
    model_input_unchanged = final_model_input == original_model_input
    latent_input_unchanged = final_latent_input == original_latent_input
    sampler_count_unchanged = before_sampler_ids == after_sampler_ids
    sampler_parameters_preserved = {
        k: v for k, v in sampler_before["non_conditioning_inputs"].items() if k != "model"
    } == {
        k: v for k, v in sampler_after["non_conditioning_inputs"].items() if k != "model"
    }
    single_sampler_preserved = sampler_count_unchanged and len(after_sampler_ids) == len(before_sampler_ids)
    regional_lora_applied = bool(lora_graph_meta.get("applied"))

    proof = {
        "schema": LIGHTWEIGHT_RUNTIME_PROOF_SCHEMA,
        "phase": EXECUTION_STRATEGY_PHASE,
        "engine": ENGINE_LIGHTWEIGHT_REGIONAL,
        "family": family,
        "loader": strategy.get("loader"),
        "mode": strategy.get("mode"),
        "sampler_ids_before": before_sampler_ids,
        "sampler_ids_after": after_sampler_ids,
        "sampler_count_before": len(before_sampler_ids),
        "sampler_count_after": len(after_sampler_ids),
        "single_sampler_preserved": single_sampler_preserved,
        "sampler_parameters_preserved": sampler_parameters_preserved,
        "latent_input_unchanged": latent_input_unchanged,
        "model_input_unchanged": model_input_unchanged,
        "model_input_change_expected": regional_lora_applied and str(sampler_before["inputs"].get("model")) == str(model_output_ref),
        "original_model_input": deepcopy(original_model_input),
        "final_model_input": deepcopy(final_model_input),
        "original_latent_input": deepcopy(original_latent_input),
        "final_latent_input": deepcopy(final_latent_input),
        "positive_input_rewired": bool((inpaint_wrapper and list(inpaint_wrapper["positive_ref"]) != list(positive_ref)) or (not inpaint_wrapper and list(original_positive_ref) != list(positive_ref))),
        "negative_input_rewired": bool((inpaint_wrapper and list(inpaint_wrapper["negative_ref"]) != list(negative_ref)) or (not inpaint_wrapper and list(original_negative_ref) != list(negative_ref))),
        "conditioning_rewire_location": "inpaint_model_conditioning_inputs" if inpaint_wrapper else "sampler_inputs",
        "sampler_conditioning_rewired": sampler_conditioning_rewired,
        "inpaint_conditioning_wrapper_node_id": str(inpaint_wrapper.get("node_id") or "") if inpaint_wrapper else "",
        "inpaint_conditioning_wrapper_preserved": bool(
            not inpaint_wrapper
            or (
                list(sampler_after["inputs"].get("positive") or []) == list(original_positive_ref)
                and list(sampler_after["inputs"].get("negative") or []) == list(original_negative_ref)
                and list(sampler_after["inputs"].get("latent_image") or []) == list(original_latent_input or [])
            )
        ),
        "inpaint_conditioning_anchor_positive_ref": deepcopy(conditioning_positive_ref),
        "inpaint_conditioning_anchor_negative_ref": deepcopy(conditioning_negative_ref),
        "conditioning_wrapper_rewired": conditioning_wrapper_rewired,
        "regional_prompt_lane_count": len(lane_records),
        "regional_negative_lane_count": len(negative_lane_records),
        "regional_negative_suppressed_count": len(suppressed_negative_regions),
        "subject_authority_applied": bool(subject_authority_merge.get("applied")),
        "subject_authority_merge_mode": str(subject_authority_merge.get("status") or "inactive"),
        "subject_authority_bridge_ref": deepcopy(subject_authority_bridge_ref),
        "subject_authority_negative_ref": deepcopy(subject_authority_negative_ref),
        "subject_authority_node_ids": list(subject_authority_node_ids),
        "subject_authority_source_text_node_id": str(subject_authority_merge.get("source_node_id") or ""),
        "subject_authority_negative_source_text_node_id": str(subject_authority_negative_merge.get("source_node_id") or ""),
        "subject_authority_merge": deepcopy(subject_authority_merge),
        "subject_authority_negative_merge": deepcopy(subject_authority_negative_merge),
        "subject_authority": deepcopy(subject_authority),
        "modern_scene_director_core": {
            "schema": "neo.image.scene_director.modern_lora_isolation_core.v1",
            "primary_purpose": "regional_lora_isolation",
            "global_prompt_mutation": bool(subject_authority_merge.get("applied")),
            "regional_prompt_policy": "optional_local_reinforcement",
            "strict_cast_control_requested": strict_cast_control,
            "strict_cast_control_default": False,
            "scene_composition_authority": "provider_model_and_user_global_prompt",
        },
        "prompt_conflict_count": len(directional_conflicts),
        "heavy_sd_repairs_added": False,
        "character_lock_nodes_added": 0,
        "repair_sampler_nodes_added": 0,
        "regional_lora_nodes_added": len(lora_nodes_added),
        "regional_lora_route_count": int(regional_lora_contract.get("route_count") or 0),
        "regional_lora_compile_status": lora_graph_meta.get("status"),
        "regional_lora_model_consumers_rewired": deepcopy(lora_graph_meta.get("model_consumers_rewired") or []),
        "global_model_mutation": False,
        "runtime_gpu_proven": False,
        "runtime_status": "graph_contract_armed_runtime_proof_pending" if regional_lora_applied else "graph_contract_applied_runtime_proof_pending",
        "krea2_sampler_profile": deepcopy(krea2_sampler_profile),
        "krea2_profile_preserved": bool(krea2_sampler_profile.get("ok", True)),
        "flux2_klein_sampler_profile": deepcopy(klein_sampler_profile),
        "flux2_klein_profile_preserved": bool(klein_sampler_profile.get("ok", True)),
        "z_image_sampler_profile": deepcopy(z_image_sampler_profile),
        "z_image_profile_preserved": bool(z_image_sampler_profile.get("ok", True)),
    }
    proof["contract_ok"] = bool(
        sampler_count_unchanged
        and sampler_parameters_preserved
        and latent_input_unchanged
        and (len(lane_records) > 0 or regional_lora_applied)
        and (not regional_lora_applied or len(lora_nodes_added) == 1)
        and (not strict_cast_control or scene_director_only or bool(subject_authority_merge.get("applied")))
        and bool(proof.get("inpaint_conditioning_wrapper_preserved", True))
    )

    validation = dict(validation)
    validation["node_status"] = {
        "required": True,
        "custom_scene_director_node_required": False,
        "engine": ENGINE_LIGHTWEIGHT_REGIONAL,
        "available": True,
        "selected_node": "ComfyBuiltInMaskedRegionalConditioning" if lane_records else None,
        "required_node_classes": required,
        "missing_node_classes": [],
        "regional_lora_runtime_node": regional_lora_contract.get("runtime_node"),
        "regional_lora_runtime_node_status": lora_graph_meta.get("status"),
        "fallback_policy": "never_fallback_to_classic_v054_or_global_lora",
    }

    patch = {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": EXECUTION_STRATEGY_PHASE,
        "patch_type": "scene_director_lightweight_regional",
        "applied": bool(lane_records or regional_lora_applied),
        "mutated": bool(lane_records or regional_lora_applied),
        "node": "ComfyBuiltInMaskedRegionalConditioning" if lane_records else ("NeoRegionalLoRADelta" if regional_lora_applied else None),
        "node_class": "NeoRegionalLoRADelta" if regional_lora_applied else None,
        "node_classes": sorted({str(graph[node_id].get("class_type")) for node_id in nodes_added if isinstance(graph.get(node_id), dict)}),
        "nodes_added": nodes_added,
        "scene_node_id": None,
        "sampler_node_id": sampler_key,
        "sampler_rewired": sampler_conditioning_rewired,
        "conditioning_wrapper_rewired": conditioning_wrapper_rewired,
        "conditioning_rewire_location": "inpaint_model_conditioning_inputs" if inpaint_wrapper else "sampler_inputs",
        "inpaint_conditioning_wrapper_node_id": str(inpaint_wrapper.get("node_id") or "") if inpaint_wrapper else "",
        "regions": len(regions),
        "subject_count": int((block.get("metadata") or {}).get("subject_count") or validation.get("subject_count") or 0),
        "detail_region_count": int((block.get("metadata") or {}).get("detail_region_count") or validation.get("detail_region_count") or 0),
        "route": validation.get("route") or route_data,
        "route_state": validation.get("route_state") or "experimental_available",
        "workflow_readiness_state": validation.get("route_state") or "experimental_available",
        "workflow_patch_allowed": True,
        "node_status": validation.get("node_status"),
        "fallback_policy": "never_fallback_to_classic_v054_or_global_lora",
        "previous_model_ref": deepcopy(model_output_ref),
        "patched_model_ref": deepcopy(patched_model_ref),
        "clip_ref": deepcopy(clip_output_ref),
        "patched_clip_ref": deepcopy(clip_output_ref),
        "previous_positive_ref": deepcopy(original_positive_ref),
        "previous_negative_ref": deepcopy(original_negative_ref),
        "patched_positive_ref": deepcopy(original_positive_ref if inpaint_wrapper else positive_ref),
        "patched_negative_ref": deepcopy(original_negative_ref if inpaint_wrapper else negative_ref),
        "conditioning_anchor_positive_ref": deepcopy(positive_ref),
        "conditioning_anchor_negative_ref": deepcopy(negative_ref),
        "scene_director_engine": ENGINE_LIGHTWEIGHT_REGIONAL,
        "scene_director_execution_strategy": deepcopy(strategy),
        "scene_director_prompt_authority": prompt_authority,
        "scene_director_global_prompt_excluded": scene_director_only,
        "scene_director_modern_core": {
            "schema": "neo.image.scene_director.modern_lora_isolation_core.v1",
            "primary_purpose": "regional_lora_isolation",
            "global_prompt_policy": "provider_owned_unchanged_by_default",
            "regional_prompt_policy": "optional_local_reinforcement",
            "strict_cast_control_requested": strict_cast_control,
        },
        "scene_director_lightweight_regional_prompt": {
            "schema": LIGHTWEIGHT_REGIONAL_SCHEMA,
            "phase": EXECUTION_STRATEGY_PHASE,
            "status": "applied" if lane_records else "not_requested",
            "family": family,
            "canvas": {"width": width, "height": height},
            "set_cond_area": "default",
            "set_cond_area_reason": "multidim_safe_mask_conditioning",
            "positive_lanes": deepcopy(lane_records),
            "negative_lanes": deepcopy(negative_lane_records),
            "negative_policy": "zero_from_final_positive" if family in ZERO_NEGATIVE_FAMILIES else "masked_regional_negative",
            "suppressed_negative_region_ids": suppressed_negative_regions,
            "flux_guidance": flux_guidance,
            "single_sampler_policy": True,
            "subject_authority": deepcopy(subject_authority),
            "subject_authority_merge_mode": str(subject_authority_merge.get("status") or "inactive"),
            "subject_authority_bridge_ref": deepcopy(subject_authority_bridge_ref),
            "subject_authority_negative_ref": deepcopy(subject_authority_negative_ref),
            "subject_authority_merge": deepcopy(subject_authority_merge),
            "subject_authority_negative_merge": deepcopy(subject_authority_negative_merge),
            "prompt_conflicts": deepcopy(directional_conflicts),
            "prompt_conflict_policy": "warning_only_no_silent_prompt_rewrite",
            "global_model_weight_mutation": False,
        },
        "scene_director_lightweight_runtime_proof": proof,
        "scene_director_extra_samplers_added": 0,
        "scene_director_character_lock_execution": {"status": "not_used_lightweight_regional", "phase": EXECUTION_STRATEGY_PHASE},
        "scene_director_regional_lora_contract": deepcopy(regional_lora_contract),
        "scene_director_regional_lora_graph_patch": deepcopy(lora_graph_meta),
        "scene_director_regional_lora_applied": regional_lora_applied,
        "scene_director_regional_lora_status": lora_graph_meta.get("status") or regional_lora_contract.get("status"),
        "scene_director_regional_lora_nodes_added": lora_nodes_added,
        "scene_director_regional_lora_runtime_gpu_proven": False,
        "scene_director_regional_lora_clip_delta_execution": regional_lora_contract.get("clip_delta_execution"),
        "scene_director_krea2_sampler_profile": deepcopy(krea2_sampler_profile),
        "scene_director_krea2_lora_compatibility": deepcopy(regional_lora_contract.get("binding_compatibility") or {}) if family in KREA2_FAMILIES else {},
        "scene_director_flux2_klein_sampler_profile": deepcopy(klein_sampler_profile),
        "scene_director_flux2_klein_lora_compatibility": deepcopy(regional_lora_contract.get("binding_compatibility") or {}) if family == KLEIN_FAMILY else {},
        "scene_director_z_image_sampler_profile": deepcopy(z_image_sampler_profile),
        "scene_director_z_image_lora_compatibility": deepcopy(regional_lora_contract.get("binding_compatibility") or {}) if family in Z_IMAGE_FAMILIES else {},
        "reason": "" if proof["contract_ok"] else (
            str(lora_graph_meta.get("reason") or "lightweight_regional_runtime_contract_failed")
            if not lane_records and has_regional_lora_request else "lightweight_regional_runtime_contract_failed"
        ),
        "notes": [
            "SD-28.7 release-lock keeps Krea2 RAW/Turbo, FLUX.2 Klein, and Z-Image Base/Turbo to the lightweight regional prompt + regional LoRA contract while preserving the provider sampler.",
            "Krea2, Klein, and Z-Image use family-specific NeoRegionalLoRADelta spatial module policies; Klein preserves FluxGuidance, while Z-Image preserves ModelSamplingAuraFlow and Base/Turbo negative-conditioning semantics.",
            "Regional CLIP LoRA mutation, global LoraLoader fallback, masked finish samplers, V054 Character Lock repairs, and fixed two-route caps are disabled on the lightweight route.",
            "Z-Image token padding is handled fail-closed: caption padding and image padding receive zero regional LoRA mask values.",
            "IMG-SD2 makes regional LoRA isolation the primary modern Scene Director contract. The provider/user global prompt is not mutated by default, and regional prompts are optional local reinforcement only.",
            "SD1C/SD1D subject-count/cast prompt contracts are disabled by default on modern routes and require an explicit strict_cast_control request; prompt-vs-mask direction conflicts remain diagnostic-only.",
            "Per-run runtime proof remains required; this static package cannot substitute for a live Comfy GPU leakage test.",
        ],
    }

    mutated = bool(lane_records or regional_lora_applied)
    return {
        "workflow": graph,
        "workflow_patch": patch,
        "validation": validation,
        "model_ref": list(patched_model_ref),
        "clip_ref": list(clip_output_ref),
        "positive_ref": list(original_positive_ref if inpaint_wrapper else positive_ref),
        "negative_ref": list(original_negative_ref if inpaint_wrapper else negative_ref),
        "mutated": mutated,
        "changed": mutated,
        "extension_id": EXTENSION_ID,
        "phase": EXECUTION_STRATEGY_PHASE,
    }


def execute_lightweight_regional(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return apply_lightweight_regional_prompt_patch(*args, **kwargs)


__all__ = [
    "LIGHTWEIGHT_REGIONAL_SCHEMA",
    "LIGHTWEIGHT_RUNTIME_PROOF_SCHEMA",
    "build_lightweight_regional_plan",
    "apply_lightweight_regional_prompt_patch",
    "execute_lightweight_regional",
]
