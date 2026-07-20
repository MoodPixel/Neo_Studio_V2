"""Phase RMBG-2 prompt segmentation lab contracts.

This module is intentionally adapter-driven.  A node name from the installed
RMBG package is not enough to authorize execution; the live ComfyUI
``/object_info`` schema and its model choices must match one of the verified
input contracts below.
"""
from __future__ import annotations

import json
from typing import Any

from .public_hygiene import portable_model_identifier


SCHEMA_ID = "neo.image.background_removal.segmentation_lab.v1"
SCHEMA_VERSION = 1
MAX_SEGMENTATION_PROMPTS = 8
MAX_SEGMENTATION_PROMPT_LENGTH = 512
VALID_SEGMENTATION_ADAPTERS = {"auto", "rmbg_v1", "rmbg_v2", "sam2", "sam3"}
VALID_MASK_OPERATIONS = {"union", "intersection", "subtract"}


_ADAPTER_DEFINITIONS: dict[str, dict[str, Any]] = {
    "rmbg_v2": {
        "label": "RMBG Segmentation V2 · SAM + GroundingDINO",
        "candidates": ("SegmentV2",),
        "required": ("image", "prompt", "sam_model", "dino_model"),
        "mask_output": 1,
        "model_inputs": {"sam_model": "sam", "dino_model": "groundingdino"},
        "optional_defaults": {"threshold": 0.35, "mask_blur": 0, "mask_offset": 0, "background": "Alpha", "invert_output": False},
    },
    "rmbg_v1": {
        "label": "RMBG Segmentation V1 · SAM + GroundingDINO",
        "candidates": ("Segment",),
        "required": ("image", "prompt", "sam_model", "dino_model"),
        "mask_output": 1,
        "model_inputs": {"sam_model": "sam", "dino_model": "groundingdino"},
        "optional_defaults": {"threshold": 0.35, "mask_blur": 0, "mask_offset": 0, "background": "Alpha", "invert_output": False},
    },
    "sam2": {
        "label": "SAM2 Segmentation · text prompt",
        "candidates": ("SAM2Segment",),
        "required": ("image", "prompt", "sam2_model", "dino_model", "device"),
        "mask_output": 1,
        "model_inputs": {"sam2_model": "sam2", "dino_model": "groundingdino"},
        "optional_defaults": {"threshold": 0.35, "mask_blur": 0, "mask_offset": 0, "background": "Alpha", "invert_output": False},
    },
    "sam3": {
        "label": "SAM3 Segmentation · text prompt",
        "candidates": ("SAM3Segment",),
        "required": ("image", "prompt", "output_mode", "confidence_threshold"),
        "mask_output": 1,
        "model_inputs": {},
        "optional_defaults": {"max_segments": 0, "segment_pick": 0, "mask_blur": 0, "mask_offset": 0, "device": "Auto", "invert_output": False, "unload_model": False, "background": "Alpha"},
    },
}


def _extract_choices(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)) or not value:
        return []
    first = value[0]
    if isinstance(first, (list, tuple)):
        return [str(item).strip().replace("\\", "/") for item in first if str(item).strip()]
    if all(isinstance(item, str) for item in value):
        return [str(item).strip().replace("\\", "/") for item in value if str(item).strip()]
    return []


def _node_spec(object_info: dict[str, Any] | None, node_name: str) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(object_info, dict):
        return None
    for raw_name, spec in object_info.items():
        if str(raw_name).casefold() == str(node_name).casefold() and isinstance(spec, dict):
            return str(raw_name), spec
    return None


def _input_block(spec: dict[str, Any]) -> dict[str, Any]:
    value = spec.get("input")
    return value if isinstance(value, dict) else {}


def _input_names(spec: dict[str, Any]) -> set[str]:
    block = _input_block(spec)
    names: set[str] = set()
    for section in ("required", "optional", "hidden"):
        values = block.get(section)
        if isinstance(values, dict):
            names.update(str(name) for name in values)
    return names


def _model_choices(spec: dict[str, Any], input_name: str, role: str) -> list[str]:
    block = _input_block(spec)
    values: Any = None
    for section in ("required", "optional"):
        section_values = block.get(section)
        if isinstance(section_values, dict) and input_name in section_values:
            values = section_values[input_name]
            break
    return [portable_model_identifier(item, role) for item in _extract_choices(values) if portable_model_identifier(item, role)]


def build_segmentation_lab_catalog(object_info: dict[str, Any] | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for adapter_id, definition in _ADAPTER_DEFINITIONS.items():
        matched = None
        node_name = ""
        for candidate in definition["candidates"]:
            found = _node_spec(object_info, candidate)
            if found:
                node_name, matched = found
                break
        names = _input_names(matched or {})
        missing_inputs = sorted(set(definition["required"]) - names)
        model_choices = {
            input_name: _model_choices(matched or {}, input_name, role)
            for input_name, role in definition["model_inputs"].items()
        }
        blockers: list[str] = []
        if not object_info:
            blockers.append("Live ComfyUI /object_info is unavailable.")
        elif not matched:
            blockers.append(f"The installed Comfy profile does not expose {definition['label']}.")
        elif missing_inputs:
            blockers.append(f"Live node {node_name} is missing verified input(s): {', '.join(missing_inputs)}.")
        for input_name, choices in model_choices.items():
            if not choices:
                blockers.append(f"Live node {node_name} exposes no installed choices for {input_name}.")
        rows.append({
            "id": adapter_id,
            "label": definition["label"],
            "available": not blockers,
            "node_class": node_name,
            "candidate_nodes": list(definition["candidates"]),
            "required_inputs": list(definition["required"]),
            "input_names": sorted(names),
            "model_choices": model_choices,
            "mask_output": int(definition["mask_output"]),
            "blockers": blockers,
            "execution_policy": "live_object_info_and_live_model_choices_only",
        })
    available = [row for row in rows if row["available"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "adapters": rows,
        "available": bool(available),
        "default_adapter": next((row["id"] for row in available if row["id"] == "rmbg_v2"), available[0]["id"] if available else ""),
        "mask_operations": ["union", "intersection", "subtract"],
        "limits": {"max_prompts": MAX_SEGMENTATION_PROMPTS, "max_prompt_length": MAX_SEGMENTATION_PROMPT_LENGTH},
        "safety": {"requires_live_object_info": True, "requires_live_model_choices": True, "no_silent_fallback": True, "path_policy": "portable_identifiers_only"},
    }


def normalize_segmentation_prompts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError:
            value = [{"prompt": line} for line in value.splitlines()]
    rows = value if isinstance(value, list) else []
    prompts: list[dict[str, Any]] = []
    for index, item in enumerate(rows[:MAX_SEGMENTATION_PROMPTS], start=1):
        if isinstance(item, str):
            item = {"prompt": item}
        if not isinstance(item, dict):
            continue
        prompt = str(item.get("prompt") or item.get("text") or "").strip()[:MAX_SEGMENTATION_PROMPT_LENGTH]
        if not prompt:
            continue
        prompts.append({
            "id": str(item.get("id") or f"prompt_{index}").strip()[:64] or f"prompt_{index}",
            "label": str(item.get("label") or prompt).strip()[:120] or f"Prompt {index}",
            "prompt": prompt,
            "enabled": item.get("enabled", True) is not False,
        })
    return prompts


def normalize_segmentation_lab(source: dict[str, Any] | None) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    adapter = str(raw.get("segmentation_adapter") or "auto").strip().lower()
    if adapter not in VALID_SEGMENTATION_ADAPTERS:
        adapter = "auto"
    operation = str(raw.get("segmentation_mask_operation") or "union").strip().lower()
    if operation not in VALID_MASK_OPERATIONS:
        operation = "union"
    try:
        threshold = max(0.05, min(0.95, float(raw.get("segmentation_threshold", 0.35))))
    except (TypeError, ValueError):
        threshold = 0.35
    try:
        confidence = max(0.05, min(0.95, float(raw.get("segmentation_confidence_threshold", 0.5))))
    except (TypeError, ValueError):
        confidence = 0.5
    try:
        max_segments = max(0, min(128, int(raw.get("segmentation_max_segments", 0))))
    except (TypeError, ValueError):
        max_segments = 0
    try:
        segment_pick = max(0, min(128, int(raw.get("segmentation_segment_pick", 0) or 0)))
    except (TypeError, ValueError):
        segment_pick = 0
    return {
        "enabled": raw.get("segmentation_lab_enabled", raw.get("workflow_mode") == "segmentation_lab") is True or str(raw.get("segmentation_lab_enabled", "")).strip().lower() in {"1", "true", "yes", "on"},
        "adapter": adapter,
        "mask_operation": operation,
        "prompts": normalize_segmentation_prompts(raw.get("segmentation_lab_prompts")),
        "threshold": threshold,
        "confidence_threshold": confidence,
        "max_segments": max_segments,
        "sam_model": portable_model_identifier(raw.get("segmentation_sam_model"), "sam"),
        "sam2_model": portable_model_identifier(raw.get("segmentation_sam2_model"), "sam2"),
        "dino_model": portable_model_identifier(raw.get("segmentation_dino_model"), "groundingdino"),
        "device": str(raw.get("segmentation_device") or "Auto").strip() if str(raw.get("segmentation_device") or "Auto").strip() in {"Auto", "CPU", "GPU"} else "Auto",
        "segment_pick": segment_pick,
    }


def resolve_segmentation_adapter(catalog: dict[str, Any] | None, requested: str = "auto") -> dict[str, Any]:
    rows = [row for row in (catalog or {}).get("adapters", []) if isinstance(row, dict)]
    requested = requested if requested in VALID_SEGMENTATION_ADAPTERS else "auto"
    if requested != "auto":
        row = next((item for item in rows if item.get("id") == requested), None)
        if not row or not row.get("available"):
            return {"ready": False, "adapter": requested, "blockers": list((row or {}).get("blockers") or [f"Segmentation adapter is unavailable: {requested}."])}
        return {"ready": True, "adapter": requested, "row": row, "blockers": []}
    row = next((item for item in rows if item.get("available")), None)
    if not row:
        return {"ready": False, "adapter": "", "blockers": ["No verified prompt-segmentation adapter is available in the active Comfy profile."]}
    return {"ready": True, "adapter": str(row.get("id") or ""), "row": row, "blockers": []}
