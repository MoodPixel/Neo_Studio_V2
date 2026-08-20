from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from .execution_strategy import (
    EXECUTION_STRATEGY_PHASE,
    REGIONAL_LORA_NODE_CLASS,
    normalize_scene_director_family,
    resolve_scene_director_execution_strategy,
)
from .krea2_support import filter_krea2_bindings, krea2_full_support_contract
from .flux2_klein_support import filter_klein_bindings, klein_full_support_contract, resolve_klein_profile
from .z_image_support import filter_z_image_bindings, z_image_full_support_contract, resolve_z_image_profile

REGIONAL_LORA_DELTA_SCHEMA = "neo.image.scene_director.regional_lora_delta.contract.v6"
REGIONAL_LORA_GRAPH_SCHEMA = "neo.image.scene_director.regional_lora_delta.graph_patch.v1"
KREA2_ADAPTER = "krea2_activation_delta_v3_strict_isolation"
FLUX2_KLEIN_ADAPTER = "flux2_klein_activation_delta_v1"
Z_IMAGE_ADAPTER = "z_image_activation_delta_v1"
RUNTIME_PROOF_FIELDS = (
    "lora_loaded",
    "model_family_match",
    "region_mask_bound",
    "masked_delta_hook_active",
    "delta_eval_attempted",
    "delta_nonzero",
    "global_model_mutation",
    "sampler_count",
    "forward_hooks_removed",
    "spatial_scope_filter_active",
    "loader_supported",
    "token_mask_scope_proven",
)

FAMILY_ADAPTERS = {
    "krea2": {
        "status": "supported_runtime_contract",
        "adapter": KREA2_ADAPTER,
        "runtime_enabled": True,
        "clip_delta_execution": "suppressed_model_side_only",
    },
    "krea2_turbo": {
        "status": "supported_runtime_contract",
        "adapter": KREA2_ADAPTER,
        "runtime_enabled": True,
        "clip_delta_execution": "suppressed_model_side_only",
    },
    "flux2_klein": {
        "status": "supported_runtime_contract",
        "adapter": FLUX2_KLEIN_ADAPTER,
        "runtime_enabled": True,
        "clip_delta_execution": "suppressed_model_side_only",
        "reason": "SD-28.5 enables the family-specific Flux2 Klein activation-delta adapter; per-run GPU proof remains mandatory.",
    },
    "z_image": {
        "status": "supported_runtime_contract",
        "adapter": Z_IMAGE_ADAPTER,
        "runtime_enabled": True,
        "clip_delta_execution": "suppressed_model_side_only",
        "reason": "SD-28.6 enables the family-specific Z-Image activation-delta adapter; per-run GPU proof remains mandatory.",
    },
    "z_image_turbo": {
        "status": "supported_runtime_contract",
        "adapter": Z_IMAGE_ADAPTER,
        "runtime_enabled": True,
        "clip_delta_execution": "suppressed_model_side_only",
        "reason": "SD-28.6 enables the family-specific Z-Image Turbo activation-delta adapter; per-run GPU proof remains mandatory.",
    },
}


def _node_names(nodes: Any) -> set[str]:
    if isinstance(nodes, dict):
        result = {str(key) for key in nodes}
        for value in nodes.values():
            if isinstance(value, str):
                result.add(value)
            elif isinstance(value, dict):
                name = value.get("class_type") or value.get("name") or value.get("display_name")
                if name:
                    result.add(str(name))
        return result
    if isinstance(nodes, (list, tuple, set)):
        return {str(item) for item in nodes}
    return set()



def _sampler_count(graph: dict[str, Any]) -> int:
    return sum(
        1
        for node in graph.values()
        if isinstance(node, dict) and str(node.get("class_type") or "") in {"KSampler", "KSamplerAdvanced"}
    )


def _next_id(graph: dict[str, Any]) -> str:
    ids: list[int] = []
    for key in graph:
        try:
            ids.append(int(str(key)))
        except Exception:
            pass
    return str(max(ids, default=0) + 1)


def _refs_equal(value: Any, expected: list[Any]) -> bool:
    return value == expected or (isinstance(value, (list, tuple)) and list(value) == list(expected))


def _region_lookup(regions: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    return {
        str(region.get("id") or ""): region
        for region in (regions or [])
        if isinstance(region, dict) and str(region.get("id") or "")
    }


def _canvas(canvas: dict[str, Any] | None) -> dict[str, int]:
    source = canvas if isinstance(canvas, dict) else {}
    try:
        width = max(64, int(source.get("width") or 1024))
        height = max(64, int(source.get("height") or 1024))
    except Exception:
        width = height = 1024
    return {"width": width, "height": height}


def _normalized_bbox(region: dict[str, Any]) -> dict[str, float]:
    bbox = region.get("bbox") if isinstance(region.get("bbox"), dict) else {}
    try:
        x = float(bbox.get("x", 0.0) or 0.0)
        y = float(bbox.get("y", 0.0) or 0.0)
        w = float(bbox.get("w", 1.0) or 1.0)
        h = float(bbox.get("h", 1.0) or 1.0)
    except Exception:
        x, y, w, h = 0.0, 0.0, 1.0, 1.0
    x = max(0.0, min(1.0, x))
    y = max(0.0, min(1.0, y))
    w = max(0.001, min(1.0 - x, w))
    h = max(0.001, min(1.0 - y, h))
    return {"x": x, "y": y, "w": w, "h": h}


def _bbox_overlap_fraction(a: dict[str, float], b: dict[str, float]) -> float:
    ax0, ay0 = float(a.get("x", 0.0)), float(a.get("y", 0.0))
    ax1, ay1 = ax0 + float(a.get("w", 0.0)), ay0 + float(a.get("h", 0.0))
    bx0, by0 = float(b.get("x", 0.0)), float(b.get("y", 0.0))
    bx1, by1 = bx0 + float(b.get("w", 0.0)), by0 + float(b.get("h", 0.0))
    iw = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    ih = max(0.0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    if inter <= 0.0:
        return 0.0
    smaller = max(1e-8, min(float(a.get("w", 0.0)) * float(a.get("h", 0.0)), float(b.get("w", 0.0)) * float(b.get("h", 0.0))))
    return max(0.0, min(1.0, inter / smaller))


def _regional_overlap_diagnostics(routes: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: list[dict[str, Any]] = []
    max_overlap = 0.0
    for i in range(len(routes)):
        for j in range(i + 1, len(routes)):
            a = routes[i]
            b = routes[j]
            overlap = _bbox_overlap_fraction(a.get("bbox") or {}, b.get("bbox") or {})
            max_overlap = max(max_overlap, overlap)
            if overlap > 0.0:
                pairs.append({
                    "route_a": str(a.get("route_id") or ""),
                    "route_b": str(b.get("route_id") or ""),
                    "region_a": str(a.get("region_id") or ""),
                    "region_b": str(b.get("region_id") or ""),
                    "overlap_fraction_of_smaller_region": round(overlap, 6),
                })
    risk = "high" if max_overlap >= 0.20 else ("medium" if max_overlap >= 0.05 else ("low" if max_overlap > 0 else "none"))
    return {
        "pair_count": len(pairs),
        "pairs": pairs,
        "max_overlap_fraction": round(max_overlap, 6),
        "risk": risk,
    }


def _seam_feather(region: dict[str, Any], canvas: dict[str, int]) -> float:
    mask = region.get("mask") if isinstance(region.get("mask"), dict) else {}
    try:
        pixels = max(0.0, float(mask.get("feather", region.get("mask_feather", 0)) or 0.0))
    except Exception:
        pixels = 0.0
    if pixels <= 0:
        return 0.0
    return max(0.0, min(0.25, pixels / float(max(canvas["width"], canvas["height"]))))


def build_regional_lora_delta_contract(
    route: dict[str, Any] | None = None,
    *,
    bindings: list[dict[str, Any]] | None = None,
    regions: list[dict[str, Any]] | None = None,
    canvas: dict[str, Any] | None = None,
) -> dict[str, Any]:
    strategy = resolve_scene_director_execution_strategy(route)
    family = normalize_scene_director_family(strategy.get("family") or (route or {}).get("family"))
    adapter = deepcopy(FAMILY_ADAPTERS.get(family) or {
        "status": "unsupported",
        "adapter": None,
        "runtime_enabled": False,
        "reason": "No regional LoRA adapter is registered for this family.",
    })
    region_by_id = _region_lookup(regions)
    canvas_data = _canvas(canvas)
    if family in {"krea2", "krea2_turbo"}:
        compatibility = filter_krea2_bindings(bindings, family)
    elif family == "flux2_klein":
        compatibility = filter_klein_bindings(bindings, route)
    elif family in {"z_image", "z_image_turbo"}:
        compatibility = filter_z_image_bindings(bindings, family)
    else:
        compatibility = {"accepted": deepcopy(bindings or []), "rejected": [], "unknown": [], "accepted_count": len(bindings or []), "rejected_count": 0, "unknown_count": 0}
    accepted_bindings = compatibility.get("accepted") if isinstance(compatibility.get("accepted"), list) else []
    normalized_routes: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = [
        {**deepcopy(item), "reason": "declared_lora_family_incompatible"}
        for item in (compatibility.get("rejected") or [])
        if isinstance(item, dict)
    ]
    for index, binding in enumerate(accepted_bindings, start=1):
        if not isinstance(binding, dict):
            continue
        region_id = str(binding.get("region_id") or "").strip()
        region = region_by_id.get(region_id)
        lora_name = str(binding.get("name") or binding.get("lora_name") or "").strip()
        if not region or not lora_name:
            skipped.append({"index": index, "region_id": region_id, "reason": "missing_region_or_lora_name"})
            continue
        try:
            strength = max(-4.0, min(4.0, float(binding.get("strength", 0.8) or 0.8)))
        except Exception:
            strength = 0.8
        if abs(strength) <= 1e-8:
            skipped.append({"index": index, "region_id": region_id, "lora_name": lora_name, "reason": "zero_strength"})
            continue
        target = str(binding.get("target") or "both").strip().lower()
        normalized_routes.append({
            "route_id": str(binding.get("uid") or f"regional_lora_{index}"),
            "region_id": region_id,
            "region_index": int(binding.get("region_index") or index),
            "row_id": str(binding.get("row_id") or binding.get("lora_row_id") or ""),
            "lora_name": lora_name,
            "strength": strength,
            "target_requested": target,
            "target_executed": "model_only",
            "clip_delta_execution": "suppressed_model_side_only",
            "bbox": _normalized_bbox(region),
            "canvas": deepcopy(canvas_data),
            "seam_feather": _seam_feather(region, canvas_data),
            "source_record_id": str(binding.get("source_record_id") or ""),
            "source": str(binding.get("source") or "neo_lora_stack_apply_to_targeting"),
            "krea2_compatibility": deepcopy(binding.get("krea2_compatibility") or {}),
            "flux2_klein_compatibility": deepcopy(binding.get("flux2_klein_compatibility") or {}),
            "z_image_compatibility": deepcopy(binding.get("z_image_compatibility") or {}),
            "enabled": True,
        })
    overlap_diagnostics = _regional_overlap_diagnostics(normalized_routes)
    execution_enabled = bool(adapter.get("runtime_enabled") and normalized_routes)
    status = (
        "armed_runtime_contract" if execution_enabled
        else ("no_regional_lora_routes" if not normalized_routes else str(adapter.get("status") or "adapter_gated"))
    )
    return {
        "schema": REGIONAL_LORA_DELTA_SCHEMA,
        "phase": EXECUTION_STRATEGY_PHASE,
        "family": family,
        "adapter": adapter,
        "status": status,
        "execution_enabled": execution_enabled,
        "loader": strategy.get("loader"),
        "krea2_full_support": krea2_full_support_contract(family, strategy.get("loader"), strategy.get("mode")) if family in {"krea2", "krea2_turbo"} else None,
        "flux2_klein_full_support": klein_full_support_contract(route) if family == "flux2_klein" else None,
        "flux2_klein_profile": resolve_klein_profile(route) if family == "flux2_klein" else None,
        "z_image_full_support": z_image_full_support_contract(route) if family in {"z_image", "z_image_turbo"} else None,
        "z_image_profile": resolve_z_image_profile(route) if family in {"z_image", "z_image_turbo"} else None,
        "binding_compatibility": deepcopy(compatibility),
        "strategy": deepcopy(strategy),
        "bindings": deepcopy(bindings or []),
        "routes": normalized_routes,
        "route_count": len(normalized_routes),
        "skipped_bindings": skipped,
        "runtime_node": REGIONAL_LORA_NODE_CLASS if adapter.get("runtime_enabled") else None,
        "runtime_proof_required": True,
        "runtime_proof_fields": list(RUNTIME_PROOF_FIELDS),
        "runtime_gpu_proven": False,
        "hard_region_isolation_claimed": False,
        "isolation_goal": "prevent_cross_character_lora_mixing",
        "isolation_profile": "krea2_strict_no_attention_kv_write" if family in {"krea2", "krea2_turbo"} else "spatial_activation_delta_best_effort",
        "isolation_overlap_diagnostics": overlap_diagnostics,
        "compile_contract_isolation": "activation_delta_masked_model_side" if execution_enabled else "not_executable",
        "global_model_mutation_allowed": False,
        "clip_delta_execution": "suppressed_model_side_only" if execution_enabled else "not_available",
        "masked_finish_pass_fallback": "disabled_by_default",
        "route_limit": None,
        "canvas": canvas_data,
        "policy": (
            "IMG-SD2 regional LoRA isolation uses one cloned MODEL with family-specific spatial-scope-filtered forward-time masked activation deltas. "
            "Krea2 additionally suppresses LoRA writes to attention key/value projections because those tokens can broadcast identity influence to queries outside the owning region. "
            "No global LoRA fallback, CLIP mutation, repair sampler, or fixed route-count cap is allowed."
        ),
    }


def rewire_model_consumers(
    graph: dict[str, Any],
    *,
    original_model_ref: list[Any],
    patched_model_ref: list[Any],
    skip_node_ids: set[str] | None = None,
) -> list[str]:
    rewired: list[str] = []
    skip = {str(item) for item in (skip_node_ids or set())}
    for node_id, node in graph.items():
        node_key = str(node_id)
        if node_key in skip or not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else None
        if not isinstance(inputs, dict):
            continue
        if _refs_equal(inputs.get("model"), original_model_ref):
            inputs["model"] = deepcopy(patched_model_ref)
            rewired.append(node_key)
    return rewired


def apply_regional_lora_delta(
    workflow: dict[str, Any],
    *,
    contract: dict[str, Any],
    model_ref: list[Any],
    available_nodes: Any,
) -> dict[str, Any]:
    graph = deepcopy(workflow or {})
    original_model_ref = list(model_ref)
    routes = contract.get("routes") if isinstance(contract.get("routes"), list) else []
    result = {
        "schema": REGIONAL_LORA_GRAPH_SCHEMA,
        "phase": EXECUTION_STRATEGY_PHASE,
        "status": "not_requested",
        "applied": False,
        "node_id": None,
        "nodes_added": [],
        "previous_model_ref": deepcopy(original_model_ref),
        "patched_model_ref": deepcopy(original_model_ref),
        "model_consumers_rewired": [],
        "global_model_mutation": False,
        "clip_delta_execution": contract.get("clip_delta_execution") or "not_available",
        "runtime_gpu_proven": False,
        "isolation_profile": contract.get("isolation_profile"),
        "isolation_overlap_diagnostics": deepcopy(contract.get("isolation_overlap_diagnostics") or {}),
        "reason": "",
    }
    if not routes:
        result["status"] = "not_requested"
        result["reason"] = "No regional LoRA routes were assigned."
        return {"workflow": graph, "model_ref": original_model_ref, "metadata": result}
    if not contract.get("execution_enabled"):
        result["status"] = "adapter_gated"
        result["reason"] = str((contract.get("adapter") or {}).get("reason") or "Regional LoRA adapter is gated for this family.")
        return {"workflow": graph, "model_ref": original_model_ref, "metadata": result}
    if available_nodes is not None and REGIONAL_LORA_NODE_CLASS not in _node_names(available_nodes):
        result["status"] = "missing_runtime_node"
        result["reason"] = f"Comfy object_info did not expose {REGIONAL_LORA_NODE_CLASS}."
        return {"workflow": graph, "model_ref": original_model_ref, "metadata": result}

    node_id = _next_id(graph)
    canvas = contract.get("canvas") if isinstance(contract.get("canvas"), dict) else {"width": 1024, "height": 1024}
    seam_values = [float(route.get("seam_feather") or 0.0) for route in routes]
    default_seam = max(seam_values, default=0.0)
    graph[node_id] = {
        "class_type": REGIONAL_LORA_NODE_CLASS,
        "inputs": {
            "model": deepcopy(original_model_ref),
            "routes_json": json.dumps(routes, separators=(",", ":"), ensure_ascii=False),
            "family": str(contract.get("family") or "krea2"),
            "loader": str(contract.get("loader") or "diffusion_model"),
            "variant": str(
                ((contract.get("flux2_klein_profile") or {}).get("variant") if isinstance(contract.get("flux2_klein_profile"), dict) else "")
                or ((contract.get("z_image_profile") or {}).get("variant") if isinstance(contract.get("z_image_profile"), dict) else "")
                or "auto"
            ),
            "canvas_width": int(canvas.get("width") or 1024),
            "canvas_height": int(canvas.get("height") or 1024),
            "seam_feather": float(default_seam),
            "sampler_count": int(_sampler_count(graph)),
        },
    }
    patched_model_ref = [node_id, 0]
    rewired = rewire_model_consumers(
        graph,
        original_model_ref=original_model_ref,
        patched_model_ref=patched_model_ref,
        skip_node_ids={node_id},
    )
    if not rewired:
        graph.pop(node_id, None)
        result["status"] = "no_model_consumers_rewired"
        result["reason"] = "Regional LoRA node was not inserted because no provider MODEL consumer matched the active model_ref."
        return {"workflow": graph, "model_ref": original_model_ref, "metadata": result}
    result.update({
        "status": "armed_not_gpu_proven",
        "applied": True,
        "node_id": node_id,
        "nodes_added": [node_id],
        "patched_model_ref": patched_model_ref,
        "model_consumers_rewired": rewired,
        "route_count": len(routes),
        "adapter": (contract.get("adapter") or {}).get("adapter"),
        "reason": "",
    })
    return {"workflow": graph, "model_ref": patched_model_ref, "metadata": result}


def validate_regional_lora_runtime_proof(proof: dict[str, Any] | None) -> dict[str, Any]:
    source = dict(proof or {})
    missing = [field for field in RUNTIME_PROOF_FIELDS if field not in source]
    failed: list[str] = []
    if not missing:
        for field in (
            "lora_loaded",
            "model_family_match",
            "region_mask_bound",
            "masked_delta_hook_active",
            "delta_eval_attempted",
            "delta_nonzero",
            "forward_hooks_removed",
            "spatial_scope_filter_active",
            "loader_supported",
            "token_mask_scope_proven",
        ):
            if source.get(field) is not True:
                failed.append(field)
        if source.get("global_model_mutation") is not False:
            failed.append("global_model_mutation")
        family = str(source.get("family") or "").strip().lower().replace("-", "_")
        if family in {"krea2", "krea2_turbo"}:
            if source.get("cross_region_attention_kv_write_suppressed") is not True:
                failed.append("cross_region_attention_kv_write_suppressed")
            if str(source.get("identity_isolation_profile") or "") != "krea2_strict_no_attention_kv_write":
                failed.append("identity_isolation_profile")
        try:
            if int(source.get("sampler_count") or 0) != 1:
                failed.append("sampler_count")
        except Exception:
            failed.append("sampler_count")
    return {
        "schema": "neo.image.scene_director.regional_lora_delta.runtime_proof.v6",
        "phase": EXECUTION_STRATEGY_PHASE,
        "ready": not missing and not failed,
        "runtime_gpu_proven": bool(source.get("runtime_gpu_proven")) and not missing and not failed,
        "missing_fields": missing,
        "failed_fields": failed,
        "proof": deepcopy(source),
    }


__all__ = [
    "REGIONAL_LORA_DELTA_SCHEMA",
    "REGIONAL_LORA_GRAPH_SCHEMA",
    "KREA2_ADAPTER",
    "RUNTIME_PROOF_FIELDS",
    "FAMILY_ADAPTERS",
    "build_regional_lora_delta_contract",
    "rewire_model_consumers",
    "apply_regional_lora_delta",
    "validate_regional_lora_runtime_proof",
]
