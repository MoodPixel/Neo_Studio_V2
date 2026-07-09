from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

try:  # Comfy nodes run inside a torch environment, but tests should stay import-safe.
    import torch
except Exception:  # pragma: no cover - only used if torch is unavailable in a lightweight env.
    torch = None  # type: ignore[assignment]

from .v054_contract import SCENE_GRAPH_SCHEMA, SCENE_GRAPH_VERSION, validate_scene_graph_v054

NODE_CLASS = "NeoSceneDirectorV054"
NODE_DISPLAY_NAME = "Neo Scene Director V054"
NODE_CATEGORY = "Neo/Image/Scene Director"

ROLE_GROUPS = {
    "subject": {"character"},
    "detail": {"face_detail", "hair_detail", "hand_detail", "character_detail", "clothing", "held_prop", "object", "text", "lighting", "effect", "style", "custom"},
    "background": {"background", "background_object", "transition_effect"},
}


def _parse_scene_graph_json(scene_graph_json: Any) -> dict[str, Any]:
    if isinstance(scene_graph_json, dict):
        return deepcopy(scene_graph_json)
    if isinstance(scene_graph_json, str) and scene_graph_json.strip():
        try:
            parsed = json.loads(scene_graph_json)
        except Exception as exc:
            return {
                "version": SCENE_GRAPH_VERSION,
                "canvas": {"width": 1024, "height": 1024},
                "global": {},
                "regions": [],
                "metadata": {
                    "parse_error": str(exc),
                    "schema": SCENE_GRAPH_SCHEMA,
                },
            }
        if isinstance(parsed, dict):
            return parsed
    return {"version": SCENE_GRAPH_VERSION, "canvas": {"width": 1024, "height": 1024}, "global": {}, "regions": []}


def _int(value: Any, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(round(float(value)))
    except Exception:
        parsed = default
    return max(minimum, parsed)


def _float(value: Any, default: float, lo: float = 0.0, hi: float = 10.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = default
    return max(lo, min(hi, parsed))


def _region_mask_tensor(bbox: list[float], *, width: int, height: int, feather: int = 0):
    if torch is None:  # pragma: no cover
        return {"bbox": bbox, "width": width, "height": height, "feather": feather}
    mask = torch.zeros((height, width), dtype=torch.float32)
    x1 = max(0, min(width, int(round(float(bbox[0]) * width))))
    y1 = max(0, min(height, int(round(float(bbox[1]) * height))))
    x2 = max(x1 + 1, min(width, int(round(float(bbox[2]) * width))))
    y2 = max(y1 + 1, min(height, int(round(float(bbox[3]) * height))))
    mask[y1:y2, x1:x2] = 1.0
    # Phase 2 deliberately keeps mask feathering as metadata-only. Real blur/feather
    # gets implemented when the runtime route is enabled so we can test VRAM/cost.
    return mask


def _empty_mask(*, width: int, height: int):
    if torch is None:  # pragma: no cover
        return {"bbox": [0.0, 0.0, 0.0, 0.0], "width": width, "height": height, "empty": True}
    return torch.zeros((height, width), dtype=torch.float32)


def _stack_masks(masks: list[Any], *, width: int, height: int):
    if not masks:
        return _empty_mask(width=width, height=height)
    if torch is None:  # pragma: no cover
        return masks
    return torch.stack(masks, dim=0)


def build_v054_mask_outputs(scene_graph: dict[str, Any], *, width: int, height: int, mask_feather: int = 0) -> dict[str, Any]:
    """Build typed mask groups for the V054 skeleton node.

    Phase 2 only guarantees deterministic rectangular masks and grouped metadata.
    Real conditioning math, ControlNet routing, detailer routing, and inpaint routing
    are intentionally left for later phases.
    """
    regions = scene_graph.get("regions") if isinstance(scene_graph.get("regions"), list) else []
    region_masks: list[Any] = []
    subject_masks: list[Any] = []
    detail_masks: list[Any] = []
    background_masks: list[Any] = []
    control_masks: list[Any] = []
    inpaint_masks: list[Any] = []
    mask_index: dict[str, dict[str, Any]] = {}

    for region in regions:
        if not isinstance(region, dict):
            continue
        bbox = region.get("bbox") if isinstance(region.get("bbox"), list) else [0.0, 0.0, 1.0, 1.0]
        mask = _region_mask_tensor(bbox, width=width, height=height, feather=mask_feather)
        role = str(region.get("role") or "custom")
        region_id = str(region.get("id") or f"region_{len(region_masks) + 1}")
        region_masks.append(mask)
        group_names = ["region"]
        if role in ROLE_GROUPS["subject"]:
            subject_masks.append(mask)
            group_names.append("subject")
        elif role in ROLE_GROUPS["background"]:
            background_masks.append(mask)
            group_names.append("background")
        else:
            detail_masks.append(mask)
            group_names.append("detail")
        if isinstance(region.get("control"), dict) and region["control"].get("enabled"):
            control_masks.append(mask)
            group_names.append("control")
        if isinstance(region.get("inpaint"), dict) and region["inpaint"].get("enabled"):
            inpaint_masks.append(mask)
            group_names.append("inpaint")
        edit_intent = region.get("edit_intent") if isinstance(region.get("edit_intent"), dict) else {}
        if str(edit_intent.get("mode") or "").lower() in {"modify", "replace"}:
            inpaint_masks.append(mask)
            if "inpaint" not in group_names:
                group_names.append("inpaint")
        mask_index[region_id] = {
            "role": role,
            "groups": group_names,
            "bbox": bbox,
            "attach_to": region.get("attach_to"),
        }

    return {
        "region_masks": _stack_masks(region_masks, width=width, height=height),
        "subject_masks": _stack_masks(subject_masks, width=width, height=height),
        "detail_masks": _stack_masks(detail_masks, width=width, height=height),
        "background_masks": _stack_masks(background_masks, width=width, height=height),
        "control_masks": _stack_masks(control_masks, width=width, height=height),
        "inpaint_masks": _stack_masks(inpaint_masks, width=width, height=height),
        "mask_index": mask_index,
        "counts": {
            "regions": len(region_masks),
            "subjects": len(subject_masks),
            "details": len(detail_masks),
            "backgrounds": len(background_masks),
            "controls": len(control_masks),
            "inpaint": len(inpaint_masks),
        },
    }


def _debug_preview(width: int, height: int):
    if torch is None:  # pragma: no cover
        return None
    return torch.zeros((1, height, width, 3), dtype=torch.float32)


class NeoSceneDirectorV054:
    """Phase 2 V054 skeleton node.

    This is an upgraded V054 contract surface, not a clean-room replacement of
    the whole Scene Director feature. It preserves model/conditioning passthrough
    until Phase 3+ routes real workflow behavior through the new scene graph.
    """

    RETURN_TYPES = (
        "MODEL",
        "CONDITIONING",
        "CONDITIONING",
        "REGION_MASKS",
        "SUBJECT_MASKS",
        "DETAIL_MASKS",
        "BACKGROUND_MASKS",
        "CONTROL_MASKS",
        "INPAINT_MASKS",
        "STRING",
        "IMAGE",
    )
    RETURN_NAMES = (
        "model",
        "positive_conditioning",
        "negative_conditioning",
        "region_masks",
        "subject_masks",
        "detail_masks",
        "background_masks",
        "control_masks",
        "inpaint_masks",
        "scene_metadata",
        "debug_preview",
    )
    FUNCTION = "execute"
    CATEGORY = NODE_CATEGORY
    DESCRIPTION = "Neo V054 JSON scene graph skeleton: validates scene_graph_json and emits typed mask groups/metadata."

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        return {
            "required": {
                "model": ("MODEL",),
                "positive_conditioning": ("CONDITIONING",),
                "negative_conditioning": ("CONDITIONING",),
                "scene_graph_json": ("STRING", {"multiline": True, "default": json.dumps({"version": SCENE_GRAPH_VERSION, "canvas": {"width": 1024, "height": 1024}, "global": {}, "regions": []})}),
                "canvas_width": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8}),
                "canvas_height": ("INT", {"default": 1024, "min": 64, "max": 8192, "step": 8}),
                "base_weight": ("FLOAT", {"default": 0.30, "min": 0.0, "max": 2.0, "step": 0.01}),
                "region_gain": ("FLOAT", {"default": 0.75, "min": 0.0, "max": 2.0, "step": 0.01}),
                "mask_feather": ("INT", {"default": 12, "min": 0, "max": 256, "step": 1}),
                "normalize_masks": ("BOOLEAN", {"default": True}),
                "debug_mode": ("BOOLEAN", {"default": False}),
            },
            "optional": {
                "identity_strength": ("FLOAT", {"default": 0.70, "min": 0.0, "max": 2.0, "step": 0.01}),
                "detail_strength": ("FLOAT", {"default": 0.70, "min": 0.0, "max": 2.0, "step": 0.01}),
                "background_strength": ("FLOAT", {"default": 0.65, "min": 0.0, "max": 2.0, "step": 0.01}),
                "control_mask_mode": (["region", "subject", "background", "global"], {"default": "region"}),
                "inpaint_mask_mode": (["region", "subject", "detail"], {"default": "region"}),
            },
        }

    def execute(
        self,
        model: Any,
        positive_conditioning: Any,
        negative_conditioning: Any,
        scene_graph_json: Any,
        canvas_width: Any = 1024,
        canvas_height: Any = 1024,
        base_weight: Any = 0.30,
        region_gain: Any = 0.75,
        mask_feather: Any = 12,
        normalize_masks: Any = True,
        debug_mode: Any = False,
        **optional: Any,
    ) -> tuple[Any, Any, Any, Any, Any, Any, Any, Any, Any, str, Any]:
        parsed = _parse_scene_graph_json(scene_graph_json)
        validation = validate_scene_graph_v054(parsed)
        normalized_graph = validation.get("scene_graph") if isinstance(validation.get("scene_graph"), dict) else {"version": SCENE_GRAPH_VERSION, "canvas": {}, "global": {}, "regions": []}
        width = _int(canvas_width or (normalized_graph.get("canvas") or {}).get("width"), 1024, 64)
        height = _int(canvas_height or (normalized_graph.get("canvas") or {}).get("height"), 1024, 64)
        feather = _int(mask_feather, 12, 0)
        masks = build_v054_mask_outputs(normalized_graph, width=width, height=height, mask_feather=feather)
        metadata = {
            "schema": SCENE_GRAPH_SCHEMA,
            "node_class": NODE_CLASS,
            "node_version": SCENE_GRAPH_VERSION,
            "phase": "SD-V054-Phase2-node-skeleton",
            "ok": bool(validation.get("ok")),
            "canvas": {"width": width, "height": height},
            "settings": {
                "base_weight": _float(base_weight, 0.30, 0.0, 2.0),
                "region_gain": _float(region_gain, 0.75, 0.0, 2.0),
                "mask_feather": feather,
                "normalize_masks": bool(normalize_masks),
                "debug_mode": bool(debug_mode),
                **{key: optional[key] for key in sorted(optional.keys())},
            },
            "counts": masks["counts"],
            "mask_index": masks["mask_index"],
            "validation_errors": validation.get("errors") or [],
            "validation_warnings": validation.get("warnings") or [],
            "validation_infos": validation.get("infos") or [],
            "scene_graph": normalized_graph,
            "runtime_note": "Phase 2 skeleton validates V054 and emits typed masks/metadata; real conditioning mutation starts in Phase 3.",
        }
        return (
            model,
            positive_conditioning,
            negative_conditioning,
            masks["region_masks"],
            masks["subject_masks"],
            masks["detail_masks"],
            masks["background_masks"],
            masks["control_masks"],
            masks["inpaint_masks"],
            json.dumps(metadata, ensure_ascii=False, sort_keys=True),
            _debug_preview(width, height),
        )


NODE_CLASS_MAPPINGS = {NODE_CLASS: NeoSceneDirectorV054}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_CLASS: NODE_DISPLAY_NAME}
