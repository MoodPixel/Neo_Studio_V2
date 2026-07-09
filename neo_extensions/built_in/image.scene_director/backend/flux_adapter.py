from __future__ import annotations

from copy import deepcopy
from typing import Any

FLUX_ADAPTER_PHASE = "SD-V054-19"
FLUX_ADAPTER_SCHEMA = "neo.image.scene_director.flux_adapter_plan.v054.v1"


def _text(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip()


def _role(region: dict[str, Any]) -> str:
    return _text(region.get("role") or region.get("type") or "custom").lower().replace(" ", "_")


def _label(region: dict[str, Any], fallback: str) -> str:
    return _text(region.get("label") or region.get("name") or region.get("id") or fallback)


def _bbox(region: dict[str, Any]) -> list[float] | None:
    raw = region.get("bbox")
    if isinstance(raw, (list, tuple)) and len(raw) == 4:
        try:
            return [float(v) for v in raw]
        except Exception:
            return None
    rect = region.get("rect") or region.get("box")
    if isinstance(rect, dict):
        try:
            x = float(rect.get("x") or 0)
            y = float(rect.get("y") or 0)
            w = float(rect.get("w") or rect.get("width") or 0)
            h = float(rect.get("h") or rect.get("height") or 0)
            return [x, y, x + w, y + h]
        except Exception:
            return None
    return None


def _semantic_region_instruction(region: dict[str, Any], region_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rid = _text(region.get("id") or "region")
    role = _role(region)
    label = _label(region, rid)
    prompt = _text(region.get("prompt") or region.get("text") or "")
    negative = _text(region.get("negative") or region.get("negative_prompt") or "")
    attach_to = _text(region.get("attach_to") or region.get("parent_id") or "")
    parent_label = _label(region_by_id.get(attach_to, {}), attach_to) if attach_to else ""
    relationship = _text(region.get("relationship") or "")
    target_area = _text(region.get("target_area") or "")

    if role == "character":
        instruction = f"Keep {label} as a distinct subject in its region: {prompt}" if prompt else f"Keep {label} as a distinct subject in its region."
    elif attach_to and parent_label:
        relation = f" with relationship {relationship}" if relationship else ""
        target = f" on {target_area}" if target_area else ""
        instruction = f"Apply {label} to {parent_label}{target}{relation}: {prompt}" if prompt else f"Apply {label} to {parent_label}{target}{relation}."
    elif role in {"background", "background_object", "transition_effect"}:
        zone = _text(region.get("zone") or region.get("background_zone") or target_area or "background zone")
        instruction = f"Use {label} as {zone}: {prompt}" if prompt else f"Use {label} as {zone}."
    elif role == "text":
        text = _text(region.get("text") or prompt)
        mode = _text(region.get("mode") or region.get("text_mode") or "composite")
        instruction = f"Text region {label}: render '{text}' using {mode} text handling."
    else:
        instruction = f"Place {label}: {prompt}" if prompt else f"Place {label}."

    return {
        "id": rid,
        "label": label,
        "role": role,
        "bbox": _bbox(region),
        "prompt": prompt,
        "negative": negative,
        "attach_to": attach_to or None,
        "parent_label": parent_label or None,
        "relationship": relationship or None,
        "target_area": target_area or None,
        "instruction": instruction,
        "mask_hint": "region_bbox_or_mask_adapter",
    }


def build_flux_adapter_plan_v054(scene_graph: dict[str, Any] | None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    graph = deepcopy(scene_graph or {}) if isinstance(scene_graph, dict) else {}
    route = dict(route or {})
    regions = [r for r in (graph.get("regions") or []) if isinstance(r, dict)]
    region_by_id = {_text(r.get("id")): r for r in regions if _text(r.get("id"))}
    instructions = [_semantic_region_instruction(region, region_by_id) for region in regions]
    global_block = graph.get("global") if isinstance(graph.get("global"), dict) else {}
    prompt = _text(global_block.get("prompt") or graph.get("prompt") or "")
    negative = _text(global_block.get("negative") or graph.get("negative") or "")
    semantic_prompt = "; ".join([prompt, *[item["instruction"] for item in instructions if item.get("instruction")]]).strip("; ")
    notices = [
        {
            "level": "warning",
            "code": "flux_adapter_planning_only",
            "message": "Flux preserves the V054 scene graph and compiles semantic/mask-adapter instructions, but it must not use the SDXL NeoSceneDirectorV054 node route.",
        },
        {
            "level": "info",
            "code": "flux_latent_replay_disabled",
            "message": "SDXL saved latent replay is disabled for Flux; source images and size-matched masks remain reusable.",
        },
    ]
    return {
        "schema": FLUX_ADAPTER_SCHEMA,
        "phase": FLUX_ADAPTER_PHASE,
        "provider_profile": "flux_adapter_planned",
        "route_kind": "flux_adapter_required",
        "status": "planning_ready",
        "adapter_required": True,
        "uses_sdxl_v054_node": False,
        "scene_graph_json_reused": True,
        "source_graph_version": graph.get("version") or "v054",
        "route": route,
        "global_prompt": prompt,
        "global_negative": negative,
        "semantic_prompt": semantic_prompt,
        "regional_instructions": instructions,
        "mask_strategy": {
            "mode": "bbox_or_mask_adapter",
            "requires_size_match": True,
            "supported_outputs": ["subject_masks", "detail_masks", "background_masks", "inpaint_masks"],
        },
        "conditioning_strategy": {
            "route": "flux_workflow_adapter",
            "regional_conditioning": "workflow_dependent",
            "character_lock": "prompt_or_adapter_dependent",
            "relationship_compiler": "semantic_instruction",
        },
        "controlnet_strategy": {
            "route": "flux_control_adapter_if_available",
            "regional_controlnet": "workflow_dependent",
        },
        "detailer_strategy": {
            "route": "post_generation_detailer_allowed",
            "regional_detailer": True,
        },
        "text_strategy": {
            "default": "composite",
            "native_text_editing": "composite_or_native",
        },
        "replay_policy": {
            "scene_graph_json": "reusable",
            "source_image": "reusable_across_providers",
            "masks": "requires_size_match",
            "saved_latent": "disabled_for_provider",
        },
        "notices": notices,
    }


__all__ = ["FLUX_ADAPTER_PHASE", "FLUX_ADAPTER_SCHEMA", "build_flux_adapter_plan_v054"]
