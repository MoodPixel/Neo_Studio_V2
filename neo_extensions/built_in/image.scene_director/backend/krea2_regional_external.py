from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from .krea2_support import filter_krea2_bindings

KREA2_REGIONAL_BUILDER = "Krea2RegionalBuilder"
KREA2_APPLY_REGIONAL = "Krea2ApplyRegional"
KREA2_REGIONAL_ENGINE = "krea2_regional_external"
KREA2_REGIONAL_NODE_REPO = "januspluto/ComfyUI-Krea2-Regional"
KREA2_EXTERNAL_SCHEMA = "neo.image.scene_director.krea2_regional_external.v1"
KREA2_EXTERNAL_PROOF_SCHEMA = "neo.image.scene_director.krea2_regional_external.proof.v1"


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
        return {str(v) for v in nodes}
    return set()


def _input_names(nodes: Any, node_name: str) -> set[str]:
    if not isinstance(nodes, dict):
        return set()
    value = nodes.get(node_name)
    if isinstance(value, (list, tuple, set)):
        return {str(v) for v in value}
    if isinstance(value, dict):
        if isinstance(value.get("inputs"), (list, tuple, set)):
            return {str(v) for v in value["inputs"]}
        input_types = value.get("input") or value.get("inputs")
        if isinstance(input_types, dict):
            names: set[str] = set()
            for group in ("required", "optional"):
                rows = input_types.get(group)
                if isinstance(rows, dict):
                    names.update(str(k) for k in rows.keys())
            if names:
                return names
    return set()


def _next_id(graph: dict[str, Any]) -> int:
    nums: list[int] = []
    for key in graph:
        try:
            nums.append(int(str(key)))
        except Exception:
            pass
    return max(nums, default=0) + 1


def _bbox(region: dict[str, Any]) -> dict[str, float]:
    raw = region.get("bbox")
    if isinstance(raw, dict):
        x = raw.get("x", 0.0); y = raw.get("y", 0.0); w = raw.get("w", 1.0); h = raw.get("h", 1.0)
    elif isinstance(raw, (list, tuple)) and len(raw) >= 4:
        x, y, w, h = raw[:4]
    else:
        x, y, w, h = 0.0, 0.0, 1.0, 1.0
    try:
        x = float(x); y = float(y); w = float(w); h = float(h)
    except Exception:
        x, y, w, h = 0.0, 0.0, 1.0, 1.0
    x = min(1.0, max(0.0, x)); y = min(1.0, max(0.0, y))
    w = min(1.0 - x, max(0.001, w)); h = min(1.0 - y, max(0.001, h))
    return {"x": x, "y": y, "w": w, "h": h}


def _binding_name(binding: dict[str, Any]) -> str:
    owner = binding.get("owner_row") if isinstance(binding.get("owner_row"), dict) else {}
    candidates = [
        binding.get("name"), binding.get("lora_name"), owner.get("name"),
        binding.get("source_record_id"), owner.get("source_record_id"),
    ]
    for value in candidates:
        text = str(value or "").strip()
        if text and (text.lower().endswith((".safetensors", ".pt", ".pth", ".bin")) or "/" in text or "\\" in text):
            return text
    return next((str(value).strip() for value in candidates if str(value or "").strip()), "")


def _binding_trigger(binding: dict[str, Any]) -> str:
    for key in ("source_record_activation_text", "source_record_trigger_words", "trigger_words"):
        text = str(binding.get(key) or "").strip()
        if text:
            return text
    return ""


def _global_prompt_from_graph(graph: dict[str, Any], ref: list[Any], fallback: str) -> str:
    current = list(ref)
    visited: set[str] = set()
    for _ in range(12):
        node_id = str(current[0]) if current else ""
        if not node_id or node_id in visited:
            break
        visited.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            break
        cls = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if cls == "CLIPTextEncode":
            text = str(inputs.get("text") or "").strip()
            return text or fallback
        next_ref = None
        for key in ("conditioning", "positive", "conditioning_1"):
            value = inputs.get(key)
            if isinstance(value, (list, tuple)) and len(value) >= 2:
                next_ref = [str(value[0]), value[1]]
                break
        if next_ref is None:
            break
        current = next_ref
    return fallback


def _sampler_ids(graph: dict[str, Any]) -> list[str]:
    return sorted(
        str(k) for k, v in graph.items()
        if isinstance(v, dict) and str(v.get("class_type") or "") in {"KSampler", "KSamplerAdvanced"}
    )


def build_regions_data(regions: list[dict[str, Any]], bindings: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]], list[str]]:
    by_region: dict[str, list[dict[str, Any]]] = {}
    routes: list[dict[str, Any]] = []
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        region_id = str(binding.get("region_id") or binding.get("apply_to") or "").strip()
        name = _binding_name(binding)
        if not region_id or not name:
            continue
        try:
            strength = float(binding.get("strength", 1.0) or 1.0)
        except Exception:
            strength = 1.0
        row_id = str(binding.get("row_id") or binding.get("lora_row_id") or binding.get("uid") or "")
        item = {"name": name, "strength": strength}
        by_region.setdefault(region_id, []).append(item)
        routes.append({
            "row_id": row_id,
            "lora_row_id": row_id,
            "region_id": region_id,
            "lora_name": name,
            "name": name,
            "strength": strength,
        })

    output_regions: list[dict[str, Any]] = []
    fallback_regions: list[str] = []
    for index, region in enumerate(regions, start=1):
        if not isinstance(region, dict) or region.get("enabled") is False or region.get("visible") is False:
            continue
        region_id = str(region.get("id") or f"scene_region_{index}")
        loras = by_region.get(region_id, [])
        prompt = str(region.get("prompt") or "").strip()
        if not prompt and loras:
            trigger = ""
            for binding in bindings:
                if isinstance(binding, dict) and str(binding.get("region_id") or binding.get("apply_to") or "") == region_id:
                    trigger = _binding_trigger(binding)
                    if trigger:
                        break
            prompt = trigger or ("person" if str(region.get("type") or region.get("role") or "").lower() == "character" else "subject")
            fallback_regions.append(region_id)
        if not prompt:
            continue
        box = _bbox(region)
        output_regions.append({
            "shape": "rect",
            "x": box["x"], "y": box["y"], "w": box["w"], "h": box["h"],
            "desc": prompt,
            "rtype": "obj",
            "text": "",
            "loras": deepcopy(loras),
        })

    state = {"regions": output_regions, "base_loras": [], "grid": {"guide": "grid", "n": 16, "snap": False}}
    return json.dumps(state, separators=(",", ":")), routes, fallback_regions


def apply_krea2_regional_external(
    workflow: dict[str, Any],
    *,
    validation: dict[str, Any],
    strategy: dict[str, Any],
    route: dict[str, Any],
    available_nodes: Any,
    model_ref: list[Any],
    clip_ref: list[Any],
    sampler_node_id: str,
    width: int,
    height: int,
) -> dict[str, Any]:
    graph = deepcopy(workflow)
    names = _node_names(available_nodes)
    required = [KREA2_REGIONAL_BUILDER, KREA2_APPLY_REGIONAL]
    missing = [name for name in required if name not in names]
    if missing:
        return {
            "workflow": graph,
            "applied": False,
            "model_ref": list(model_ref),
            "positive_ref": None,
            "negative_ref": None,
            "nodes_added": [],
            "reason": "Krea 2 Scene Director requires ComfyUI-Krea2-Regional. Missing nodes: " + ", ".join(missing),
            "runtime_proof": {
                "schema": KREA2_EXTERNAL_PROOF_SCHEMA,
                "engine": KREA2_REGIONAL_ENGINE,
                "contract_ok": False,
                "regional_lora_compile_status": "missing_external_runtime",
                "required_nodes": required,
                "missing_nodes": missing,
            },
            "lora_contract": {"route_count": 0, "routes": [], "status": "missing_external_runtime"},
        }

    sampler = graph.get(str(sampler_node_id))
    if not isinstance(sampler, dict):
        raise ValueError(f"Krea2 Regional could not resolve sampler node {sampler_node_id!r}.")
    sampler_inputs = sampler.setdefault("inputs", {})
    original_positive = deepcopy(sampler_inputs.get("positive"))
    original_negative = deepcopy(sampler_inputs.get("negative"))
    original_model = deepcopy(sampler_inputs.get("model"))
    original_latent = deepcopy(sampler_inputs.get("latent_image"))
    inpaint_wrapper_id = ""
    inpaint_wrapper = None
    conditioning_positive = deepcopy(original_positive)
    conditioning_negative = deepcopy(original_negative)
    if (
        isinstance(original_positive, (list, tuple)) and len(original_positive) >= 2
        and isinstance(original_negative, (list, tuple)) and len(original_negative) >= 2
        and str(original_positive[0]) == str(original_negative[0])
    ):
        candidate_id = str(original_positive[0])
        candidate = graph.get(candidate_id)
        try:
            pos_idx = int(original_positive[1]); neg_idx = int(original_negative[1])
        except Exception:
            pos_idx = neg_idx = -1
        if isinstance(candidate, dict) and str(candidate.get("class_type") or "") == "InpaintModelConditioning" and pos_idx == 0 and neg_idx == 1:
            wrapper_inputs = candidate.get("inputs") if isinstance(candidate.get("inputs"), dict) else {}
            if isinstance(wrapper_inputs.get("positive"), (list, tuple)) and isinstance(wrapper_inputs.get("negative"), (list, tuple)):
                inpaint_wrapper_id = candidate_id
                inpaint_wrapper = candidate
                conditioning_positive = deepcopy(wrapper_inputs.get("positive"))
                conditioning_negative = deepcopy(wrapper_inputs.get("negative"))
    sampler_inputs_before = deepcopy(sampler_inputs)
    before_samplers = _sampler_ids(graph)

    block = validation.get("block") if isinstance(validation.get("block"), dict) else {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    regions = inputs.get("regions") if isinstance(inputs.get("regions"), list) else []
    bindings = assets.get("lora_bindings") if isinstance(assets.get("lora_bindings"), list) else []
    family = str(strategy.get("family") or route.get("family") or "")
    compatibility = filter_krea2_bindings(bindings, family)
    accepted_bindings = compatibility.get("accepted") if isinstance(compatibility.get("accepted"), list) else []
    regions_data, routes, fallback_regions = build_regions_data(regions, accepted_bindings)
    if not json.loads(regions_data).get("regions"):
        return {
            "workflow": graph,
            "applied": False,
            "model_ref": list(model_ref),
            "positive_ref": original_positive,
            "negative_ref": original_negative,
            "nodes_added": [],
            "reason": "Krea2 Regional found no active regions after Scene Director normalization.",
            "runtime_proof": {"schema": KREA2_EXTERNAL_PROOF_SCHEMA, "engine": KREA2_REGIONAL_ENGINE, "contract_ok": False, "regional_lora_compile_status": "no_regions"},
            "lora_contract": {"route_count": len(routes), "routes": routes, "status": "no_regions"},
        }

    global_info = inputs.get("global") if isinstance(inputs.get("global"), dict) else {}
    fallback_prompt = str(global_info.get("positive_prompt") or "").strip()
    positive_ref = list(conditioning_positive) if isinstance(conditioning_positive, (list, tuple)) else ["4", 0]
    global_prompt = _global_prompt_from_graph(graph, positive_ref, fallback_prompt) or "an image"

    krea = params.get("krea2_regional") if isinstance(params.get("krea2_regional"), dict) else {}
    adaptive_masks = str(krea.get("adaptive_masks") or "refine boxes")
    if adaptive_masks not in {"off", "refine boxes", "free (ignore boxes)"}:
        adaptive_masks = "refine boxes"
    exclusive_masks = bool(krea.get("exclusive_masks", True))
    restrict_img_attn = bool(krea.get("restrict_img_attn", False))
    layout_in_base = str(krea.get("layout_in_base") or "position hints")
    if layout_in_base not in {"off", "position hints", "full JSON"}:
        layout_in_base = "position hints"

    next_id = _next_id(graph)
    builder_id = str(next_id); next_id += 1
    builder_inputs: dict[str, Any] = {
        "clip": list(clip_ref),
        "width": int(width),
        "height": int(height),
        "grow_px": int(krea.get("grow_px", 0) or 0),
        "feather_px": int(krea.get("feather_px", 0) or 0),
        "base_prompt": global_prompt,
        "background": "",
        "aesthetics": "",
        "lighting": "",
        "medium": "",
        "region_append": "",
        "import_mode": "when empty",
        "regions_data": regions_data,
        "layout_in_base": layout_in_base,
    }
    builder_live_inputs = _input_names(available_nodes, KREA2_REGIONAL_BUILDER)
    if builder_live_inputs:
        builder_inputs = {k: v for k, v in builder_inputs.items() if k in builder_live_inputs}
    graph[builder_id] = {"class_type": KREA2_REGIONAL_BUILDER, "inputs": builder_inputs}

    # Feed the external regional engine the provider-active model that already
    # reaches the sampler. This preserves upstream model wrappers/modifiers
    # (for example DifferentialDiffusion or unrelated global LoRAs) instead of
    # bypassing them by snapping back to the compiler's earlier base model ref.
    active_model_ref = (
        deepcopy(original_model)
        if isinstance(original_model, (list, tuple)) and len(original_model) >= 2
        else list(model_ref)
    )

    apply_inputs: dict[str, Any] = {
        "model": active_model_ref,
        "conditioning": [builder_id, 1],
        "regions": [builder_id, 0],
        "restrict_img_attn": restrict_img_attn,
        "exclusive_masks": exclusive_masks,
        "adaptive_masks": adaptive_masks,
        "adaptive_steps": int(krea.get("adaptive_steps", 2) or 2),
        "adaptive_threshold": float(krea.get("adaptive_threshold", 0.45) or 0.45),
        "base_loras_exclude_regions": bool(krea.get("base_loras_exclude_regions", False)),
        "region_lock_strength": float(krea.get("region_lock_strength", 0.4) or 0.0),
        "region_lock_start": float(krea.get("region_lock_start", 0.35) or 0.35),
        "region_lock_end": float(krea.get("region_lock_end", 0.85) or 0.85),
        "restrict_end_percent": float(krea.get("restrict_end_percent", 0.5) or 0.5),
        "base_loras": [builder_id, 2],
        "unmaskable_layers": str(krea.get("unmaskable_layers") or "skip"),
    }
    live_inputs = _input_names(available_nodes, KREA2_APPLY_REGIONAL)
    if live_inputs:
        apply_inputs = {k: v for k, v in apply_inputs.items() if k in live_inputs}

    apply_id = str(next_id); next_id += 1
    graph[apply_id] = {"class_type": KREA2_APPLY_REGIONAL, "inputs": apply_inputs}

    # Preserve provider-owned latent/sampler parameters. MODEL changes to the external
    # patched model. Conditioning is rewired at the native inpaint wrapper when one
    # exists so source-image/mask metadata and wrapper latent output remain intact.
    sampler_inputs["model"] = [apply_id, 0]
    negative_ref = original_negative
    zero_id = None
    if inpaint_wrapper is not None:
        wrapper_inputs = inpaint_wrapper.setdefault("inputs", {})
        wrapper_inputs["positive"] = [apply_id, 1]
        if family == "krea2_turbo":
            zero_id = str(next_id); next_id += 1
            graph[zero_id] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": [apply_id, 1]}}
            wrapper_inputs["negative"] = [zero_id, 0]
        else:
            wrapper_inputs["negative"] = deepcopy(conditioning_negative)
        # Sampler stays on InpaintModelConditioning outputs 0/1/2.
        sampler_inputs["positive"] = deepcopy(original_positive)
        sampler_inputs["negative"] = deepcopy(original_negative)
        negative_ref = deepcopy(original_negative)
    else:
        sampler_inputs["positive"] = [apply_id, 1]
        if family == "krea2_turbo":
            zero_id = str(next_id); next_id += 1
            graph[zero_id] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": [apply_id, 1]}}
            sampler_inputs["negative"] = [zero_id, 0]
            negative_ref = [zero_id, 0]

    after_samplers = _sampler_ids(graph)
    sampler_after = graph.get(str(sampler_node_id)) if isinstance(graph.get(str(sampler_node_id)), dict) else {}
    sampler_inputs_after = sampler_after.get("inputs") if isinstance(sampler_after.get("inputs"), dict) else {}
    nodes_added = [builder_id, apply_id] + ([zero_id] if zero_id else [])
    route_count = len(routes)
    region_count = len(json.loads(regions_data).get("regions") or [])
    preserved_keys = set(sampler_inputs_before).union(sampler_inputs_after) - {"model", "positive", "negative"}
    sampler_parameters_preserved = all(sampler_inputs_before.get(k) == sampler_inputs_after.get(k) for k in preserved_keys)
    latent_input_unchanged = sampler_inputs_after.get("latent_image") == original_latent
    proof = {
        "schema": KREA2_EXTERNAL_PROOF_SCHEMA,
        "engine": KREA2_REGIONAL_ENGINE,
        "family": family,
        "loader": strategy.get("loader"),
        "mode": strategy.get("mode"),
        "external_runtime_repo": KREA2_REGIONAL_NODE_REPO,
        "required_nodes": required,
        "missing_nodes": [],
        "builder_node_id": builder_id,
        "apply_node_id": apply_id,
        "regional_lora_route_count": route_count,
        "regional_lora_requested_count": len(bindings),
        "regional_lora_rejected_count": int(compatibility.get("rejected_count") or 0),
        "regional_lora_unknown_count": int(compatibility.get("unknown_count") or 0),
        "regional_lora_nodes_added": 2 if route_count > 0 else 0,
        "external_engine_nodes_added": 2,
        "external_runtime_node_ids": [builder_id, apply_id],
        "region_count": region_count,
        "regional_lora_compile_status": "external_runtime_armed",
        "regional_prompt_lane_count": len(json.loads(regions_data).get("regions") or []),
        "fallback_prompt_region_ids": fallback_regions,
        "single_sampler_preserved": before_samplers == after_samplers,
        "sampler_parameters_preserved": sampler_parameters_preserved,
        "latent_input_unchanged": latent_input_unchanged,
        "conditioning_rewire_location": "inpaint_model_conditioning_inputs" if inpaint_wrapper is not None else "sampler_inputs",
        "sampler_conditioning_rewired": False if inpaint_wrapper is not None else True,
        "conditioning_wrapper_rewired": bool(inpaint_wrapper is not None),
        "inpaint_conditioning_wrapper_node_id": inpaint_wrapper_id,
        "inpaint_conditioning_wrapper_preserved": bool(
            inpaint_wrapper is None
            or (sampler_inputs_after.get("positive") == original_positive and sampler_inputs_after.get("negative") == original_negative and sampler_inputs_after.get("latent_image") == original_latent)
        ),
        "inpaint_conditioning_anchor_positive_ref": deepcopy(conditioning_positive),
        "inpaint_conditioning_anchor_negative_ref": deepcopy(conditioning_negative),
        "sampler_ids_before": before_samplers,
        "sampler_ids_after": after_samplers,
        "sampler_model_before": original_model,
        "external_model_input_ref": deepcopy(active_model_ref),
        "sampler_model_after": [apply_id, 0],
        "sampler_positive_before": original_positive,
        "sampler_positive_after": deepcopy(sampler_inputs_after.get("positive")),
        "sampler_negative_before": original_negative,
        "sampler_negative_after": deepcopy(sampler_inputs_after.get("negative")),
        "global_prompt_source": "provider_positive_clip_text",
        "global_prompt_text": global_prompt,
        "layout_in_base": layout_in_base,
        "adaptive_masks": adaptive_masks,
        "exclusive_masks": exclusive_masks,
        "restrict_img_attn": restrict_img_attn,
        "adaptive_steps": apply_inputs.get("adaptive_steps"),
        "adaptive_threshold": apply_inputs.get("adaptive_threshold"),
        "region_lock_strength": apply_inputs.get("region_lock_strength"),
        "restrict_end_percent": apply_inputs.get("restrict_end_percent"),
        "unmaskable_layers": apply_inputs.get("unmaskable_layers"),
        "global_model_mutation": False,
        "heavy_sd_repairs_added": False,
        "repair_sampler_nodes_added": 0,
        "runtime_gpu_proven": False,
        "runtime_status": "external_runtime_armed",
        "modern_scene_director_core": {
            "schema": "neo.image.scene_director.krea2_regional_engine.v1",
            "primary_purpose": "regional_lora_isolation",
            "global_prompt_mutation": False,
            "global_prompt_policy": "provider_owned_passed_to_krea2_regional_builder",
            "regional_prompt_policy": "external_joint_attention_regional_ownership",
            "scene_composition_authority": "provider_model_and_user_global_prompt",
            "external_runtime_repo": KREA2_REGIONAL_NODE_REPO,
        },
        "external_engine_verified": True,
        "contract_ok": bool(before_samplers == after_samplers and sampler_parameters_preserved and latent_input_unchanged and region_count > 0),
    }
    lora_contract = {
        "schema": KREA2_EXTERNAL_SCHEMA,
        "status": "external_runtime_armed",
        "adapter": KREA2_REGIONAL_ENGINE,
        "runtime_node": KREA2_APPLY_REGIONAL,
        "route_count": route_count,
        "route_limit": None,
        "routes": routes,
        "isolation_goal": "joint_attention_regional_prompt_and_per_token_lora_gating",
        "isolation_profile": "krea2_regional_adaptive_exclusive",
        "hard_region_isolation_claimed": False,
        "clip_delta_execution": "regional_prompt_tokens_and_image_tokens_owned_by_external_engine",
        "external_runtime_repo": KREA2_REGIONAL_NODE_REPO,
        "binding_compatibility": deepcopy(compatibility),
    }
    return {
        "workflow": graph,
        "applied": bool(proof["contract_ok"]),
        "model_ref": [apply_id, 0],
        "positive_ref": deepcopy(sampler_inputs_after.get("positive")),
        "negative_ref": deepcopy(sampler_inputs_after.get("negative")),
        "nodes_added": nodes_added,
        "reason": "" if proof["contract_ok"] else "Krea2 Regional external runtime contract failed.",
        "runtime_proof": proof,
        "lora_contract": lora_contract,
        "builder_regions_data": regions_data,
        "builder_node_id": builder_id,
        "apply_node_id": apply_id,
    }


__all__ = [
    "KREA2_REGIONAL_BUILDER",
    "KREA2_APPLY_REGIONAL",
    "KREA2_REGIONAL_ENGINE",
    "KREA2_REGIONAL_NODE_REPO",
    "apply_krea2_regional_external",
    "build_regions_data",
]
