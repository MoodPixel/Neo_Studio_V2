from __future__ import annotations
from copy import deepcopy
from typing import Any
from .payload_schema import EXTENSION_ID
from .validation import validate_and_normalize_payload


def _next_id(graph: dict[str, Any]) -> str:
    nums = []
    for k in graph.keys():
        try: nums.append(int(str(k)))
        except Exception: pass
    return str((max(nums) if nums else 0) + 1)


def _ref(value: Any, fallback: list[Any]) -> list[Any]:
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return [str(value[0]), int(value[1]) if str(value[1]).isdigit() else value[1]]
    return deepcopy(fallback)


def _norm_names(unit: dict[str, Any]) -> list[str]:
    names = unit.get("image_names") if isinstance(unit.get("image_names"), list) else []
    clean = [str(v or "").strip() for v in names if str(v or "").strip()]
    one = str(unit.get("image_name") or "").strip()
    if one and one not in clean:
        clean.insert(0, one)
    return clean


def _load_images(graph: dict[str, Any], unit: dict[str, Any]) -> tuple[list[Any] | None, list[str]]:
    refs: list[list[Any]] = []
    added: list[str] = []
    for name in _norm_names(unit):
        node_id = _next_id(graph)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": name, "upload": "image"}}
        refs.append([node_id, 0]); added.append(node_id)
    if not refs:
        return None, added
    cur = refs[0]
    for nxt in refs[1:]:
        node_id = _next_id(graph)
        graph[node_id] = {"class_type": "ImageBatch", "inputs": {"image1": cur, "image2": nxt}}
        cur = [node_id, 0]; added.append(node_id)
    return cur, added


def _apply_standard(graph: dict[str, Any], model_ref: list[Any], unit: dict[str, Any]) -> tuple[list[Any], list[str]]:
    image_ref, added = _load_images(graph, unit)
    if not image_ref:
        return model_ref, added
    loader = _next_id(graph); graph[loader] = {"class_type": "IPAdapterModelLoader", "inputs": {"ipadapter_file": unit.get("model")}}
    clip = _next_id(graph); graph[clip] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": unit.get("clip_vision")}}
    apply = _next_id(graph)
    graph[apply] = {"class_type": "IPAdapterAdvanced", "inputs": {"model": model_ref, "ipadapter": [loader, 0], "image": image_ref, "weight": unit.get("weight", 1.0), "weight_type": unit.get("weight_type", "linear"), "combine_embeds": unit.get("combine_embeds", "concat"), "start_at": unit.get("start_at", 0.0), "end_at": unit.get("end_at", 1.0), "embeds_scaling": unit.get("embeds_scaling", "V only"), "clip_vision": [clip, 0]}}
    return [apply, 0], added + [loader, clip, apply]


def _apply_faceid(graph: dict[str, Any], model_ref: list[Any], unit: dict[str, Any], shared_loader_ref: list[Any] | None) -> tuple[list[Any], list[Any] | None, list[str]]:
    image_ref, added = _load_images(graph, unit)
    if not image_ref:
        return model_ref, shared_loader_ref, added
    if not shared_loader_ref:
        loader = _next_id(graph)
        graph[loader] = {"class_type": "IPAdapterUnifiedLoaderFaceID", "inputs": {"model": model_ref, "preset": unit.get("faceid_preset", "FACEID PLUS V2"), "lora_strength": unit.get("faceid_lora_strength", 0.75), "provider": unit.get("faceid_provider", "CUDA")}}
        shared_loader_ref = [loader, 1]
        model_ref = [loader, 0]
        added.append(loader)
    clip = _next_id(graph); graph[clip] = {"class_type": "CLIPVisionLoader", "inputs": {"clip_name": unit.get("clip_vision")}}
    apply = _next_id(graph)
    graph[apply] = {"class_type": "IPAdapterFaceID", "inputs": {"model": model_ref, "ipadapter": shared_loader_ref, "image": image_ref, "weight": unit.get("weight", 1.0), "weight_faceidv2": unit.get("weight_faceidv2", unit.get("weight", 1.0)), "weight_type": unit.get("weight_type", "linear"), "combine_embeds": unit.get("combine_embeds", "concat"), "start_at": unit.get("start_at", 0.0), "end_at": unit.get("end_at", 1.0), "embeds_scaling": unit.get("embeds_scaling", "V only"), "clip_vision": [clip, 0]}}
    return [apply, 0], shared_loader_ref, added + [clip, apply]


def apply_ip_adapter_patch(workflow: dict[str, Any], *, payload: Any, route: dict[str, Any] | None, available_nodes: Any, model_ref: list[Any] | tuple[Any, ...] | None, sampler_node_id: str | int = "5", sampler_model_input: str = "model") -> dict[str, Any]:
    graph = deepcopy(workflow or {})
    route = route or {}
    validation = validate_and_normalize_payload(payload, backend=route.get("backend") or "comfyui", family=route.get("family") or "sdxl", loader=route.get("loader") or "checkpoint", workflow_mode=route.get("workflow_mode") or route.get("mode") or "generate", object_info=available_nodes, require_assets=True)
    current_ref = _ref(model_ref, ["1", 0])
    if not validation.get("enabled"):
        patch = {"extension_id": EXTENSION_ID, "applied": False, "mutated": False, "reason": validation.get("reason"), "route": validation.get("route"), "nodes_added": []}
        return {"workflow": graph, "model_ref": current_ref, "workflow_patch": patch, "validation": validation}
    added_all: list[str] = []
    shared_faceid = None
    for unit in validation.get("active_units") or []:
        if str(unit.get("mode") or "standard") == "faceid":
            current_ref, shared_faceid, added = _apply_faceid(graph, current_ref, unit, shared_faceid)
        else:
            current_ref, added = _apply_standard(graph, current_ref, unit)
        added_all.extend(added)
    sampler_id = str(sampler_node_id)
    if sampler_id in graph and isinstance(graph[sampler_id].get("inputs"), dict):
        graph[sampler_id]["inputs"][sampler_model_input] = deepcopy(current_ref)
    patch = {"extension_id": EXTENSION_ID, "applied": bool(added_all), "mutated": bool(added_all), "node_classes": sorted({graph[n].get("class_type") for n in added_all if n in graph}), "nodes_added": added_all, "units": len(validation.get("active_units") or []), "route": validation.get("route"), "reason": validation.get("reason")}
    return {"workflow": graph, "model_ref": current_ref, "workflow_patch": patch, "validation": validation}
