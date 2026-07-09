from __future__ import annotations

from copy import deepcopy
from typing import Any

QWEN_ADAPTER_PHASE = "SD-V054-20"
QWEN_ADAPTER_SCHEMA = "neo.image.scene_director.qwen_adapter_plan.v054.v1"


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
    return None


def _qwen_region_instruction(region: dict[str, Any], region_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rid = _text(region.get("id") or "region")
    role = _role(region)
    label = _label(region, rid)
    prompt = _text(region.get("prompt") or region.get("text") or "")
    negative = _text(region.get("negative") or region.get("negative_prompt") or "")
    attach_to = _text(region.get("attach_to") or region.get("parent_id") or "")
    parent_label = _label(region_by_id.get(attach_to, {}), attach_to) if attach_to else ""
    relationship = _text(region.get("relationship") or "")
    target_area = _text(region.get("target_area") or "")
    edit_intent = region.get("edit_intent") if isinstance(region.get("edit_intent"), dict) else {}
    inpaint = region.get("inpaint") if isinstance(region.get("inpaint"), dict) else {}

    if role == "character":
        instruction = f"Preserve {label} as a distinct subject while applying this description: {prompt}" if prompt else f"Preserve {label} as a distinct subject."
    elif attach_to and parent_label:
        relation = f" with relationship {relationship}" if relationship else ""
        target = f" on {target_area}" if target_area else ""
        instruction = f"Apply only this local change to {parent_label}{target}{relation}: {prompt}" if prompt else f"Apply only this local change to {parent_label}{target}{relation}."
    elif role in {"background", "background_object", "transition_effect"}:
        zone = _text(region.get("zone") or region.get("background_zone") or target_area or "background zone")
        instruction = f"Edit the {zone} background region to show {prompt}; keep foreground subjects stable." if prompt else f"Edit the {zone} background region; keep foreground subjects stable."
    elif role == "text":
        text = _text(region.get("text") or prompt)
        mode = _text(region.get("mode") or region.get("text_mode") or "native")
        instruction = f"Render or edit readable text '{text}' in {label} using {mode} text handling."
    else:
        instruction = f"Apply regional edit {label}: {prompt}" if prompt else f"Apply regional edit {label}."

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
        "edit_intent": deepcopy(edit_intent),
        "inpaint": deepcopy(inpaint),
        "instruction": instruction,
        "mask_hint": "optional_mask_adapter_or_bbox",
    }


def build_qwen_adapter_plan_v054(scene_graph: dict[str, Any] | None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    graph = deepcopy(scene_graph or {}) if isinstance(scene_graph, dict) else {}
    route = dict(route or {})
    regions = [r for r in (graph.get("regions") or []) if isinstance(r, dict)]
    region_by_id = {_text(r.get("id")): r for r in regions if _text(r.get("id"))}
    instructions = [_qwen_region_instruction(region, region_by_id) for region in regions]
    global_block = graph.get("global") if isinstance(graph.get("global"), dict) else {}
    prompt = _text(global_block.get("prompt") or graph.get("prompt") or "")
    negative = _text(global_block.get("negative") or graph.get("negative") or "")
    semantic_edit_instruction = " ".join([part for part in [prompt, *[item.get("instruction") for item in instructions]] if part]).strip()
    notices = [
        {
            "level": "warning",
            "code": "qwen_semantic_adapter_planning_only",
            "message": "Qwen uses semantic/mask-adapter instructions from the V054 scene graph; it must not use the SDXL NeoSceneDirectorV054 node route.",
        },
        {
            "level": "info",
            "code": "qwen_latent_replay_disabled",
            "message": "SDXL saved latent replay is disabled for Qwen; source images, scene graph, readable text instructions, and size-matched masks remain reusable.",
        },
    ]
    return {
        "schema": QWEN_ADAPTER_SCHEMA,
        "phase": QWEN_ADAPTER_PHASE,
        "provider_profile": "qwen_semantic_edit_adapter",
        "route_kind": "semantic_edit_adapter",
        "status": "planning_ready",
        "adapter_required": True,
        "uses_sdxl_v054_node": False,
        "scene_graph_json_reused": True,
        "source_graph_version": graph.get("version") or "v054",
        "route": route,
        "global_prompt": prompt,
        "global_negative": negative,
        "semantic_edit_instruction": semantic_edit_instruction,
        "regional_instructions": instructions,
        "mask_strategy": {
            "mode": "optional_mask_or_bbox_adapter",
            "requires_size_match": True,
            "supported_outputs": ["subject_masks", "detail_masks", "background_masks", "inpaint_masks"],
        },
        "conditioning_strategy": {
            "route": "qwen_semantic_instruction_adapter",
            "regional_conditioning": "semantic_or_mask_adapter",
            "character_lock": "semantic_instruction",
            "relationship_compiler": "semantic_instruction",
        },
        "edit_strategy": {
            "route": "source_image_semantic_edit",
            "img2img_region_reuse": True,
            "inpaint_region_targeting": True,
            "native_semantic_editing": True,
        },
        "text_strategy": {
            "default": "native_text_edit_or_composite",
            "native_text_editing": True,
            "composite_fallback": True,
        },
        "replay_policy": {
            "scene_graph_json": "reusable",
            "source_image": "required_for_image_edit_or_reusable_across_providers",
            "masks": "requires_size_match",
            "saved_latent": "disabled_for_provider",
        },
        "notices": notices,
    }


__all__ = ["QWEN_ADAPTER_PHASE", "QWEN_ADAPTER_SCHEMA", "build_qwen_adapter_plan_v054"]
