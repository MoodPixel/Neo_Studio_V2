"""Phase RMBG-4 mask and object utility contracts."""
from __future__ import annotations

import json
from typing import Any


SCHEMA_ID = "neo.image.background_removal.mask_utilities.v2"
SCHEMA_VERSION = 2
MAX_MASK_FILES = 4
VALID_OPERATIONS = {
    "enhance", "combine", "extract", "crop_object", "convert", "color_to_mask",
    "mask_overlay", "object_remove_lama", "image_mask_resize", "image_crop",
}
VALID_MASK_OPERATIONS = {"union", "intersection", "difference"}

_DEFINITIONS: dict[str, dict[str, Any]] = {
    "enhance": {"label": "Mask Enhancer", "node_class": "AILab_MaskEnhancer", "required": ("mask",), "mask_output": 0},
    "combine": {"label": "Mask Combiner", "node_class": "AILab_MaskCombiner", "required": ("mask_1", "mode"), "mask_output": 0, "mask_inputs": ("mask_1", "mask_2", "mask_3", "mask_4")},
    "extract": {"label": "Mask Extractor", "node_class": "AILab_MaskExtractor", "required": ("image", "mode", "background", "background_color"), "image_output": 0, "mask_optional": True},
    "crop_object": {"label": "Crop To Object", "node_class": "AILab_CropObject", "required": (), "image_output": 0, "mask_output": 1},
    "convert": {"label": "Image/Mask Converter", "node_class": "AILab_ImageMaskConvert", "required": (), "mask_output": 1, "image_output": 0},
    "color_to_mask": {"label": "Color to Mask", "node_class": "AILab_ColorToMask", "required": ("images", "invert", "threshold", "mask_color"), "mask_output": 0},
    "mask_overlay": {"label": "Mask Overlay", "node_class": "AILab_MaskOverlay", "required": ("mask_opacity", "mask_color", "image", "mask"), "mask_output": 1, "image_output": 0},
    "object_remove_lama": {"label": "Object Remover · Lama", "node_class": "AILab_LamaRemover", "required": ("images", "masks", "removal_strength", "edge_smoothness"), "image_output": 0},
    "image_mask_resize": {"label": "Image + Mask Resize", "node_candidates": ("AILab_ImageResize", "AILab_ImageMaskResize"), "required": ("image", "custom_width", "custom_height", "megapixels", "scale_by", "resize_mode", "resize_value", "upscale_method", "device", "divisible_by", "output_mode", "crop_position", "pad_color"), "mask_output": 1, "image_output": 0, "mask_optional": True},
    "image_crop": {"label": "Image Crop", "node_class": "AILab_ImageCrop", "required": ("image", "width", "height", "x_offset", "y_offset", "split", "position"), "image_output": 0},
}


def _bounded_number(value: Any, default: float, low: float, high: float, *, integer: bool = False) -> int | float:
    """Normalize UI numbers without allowing malformed input to break queueing."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    number = max(low, min(high, number))
    return int(number) if integer else number


def _portable_identifier(value: Any) -> str:
    """Keep runtime upload names portable when they cross the public payload boundary."""

    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized or "\x00" in normalized:
        return ""
    return normalized.rsplit("/", 1)[-1][:160]


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


def _find_node(object_info: dict[str, Any] | None, node_class: str) -> tuple[str, dict[str, Any]] | None:
    for raw_name, spec in (object_info or {}).items():
        if str(raw_name).casefold() == node_class.casefold() and isinstance(spec, dict):
            return str(raw_name), spec
    return None


def _definition_candidates(definition: dict[str, Any]) -> tuple[str, ...]:
    candidates = definition.get("node_candidates")
    if isinstance(candidates, (list, tuple)) and candidates:
        return tuple(str(item) for item in candidates if str(item).strip())
    return (str(definition.get("node_class") or ""),)


def build_mask_utilities_catalog(object_info: dict[str, Any] | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for operation, definition in _DEFINITIONS.items():
        found = next((item for candidate in _definition_candidates(definition) if (item := _find_node(object_info, candidate))), None)
        node_name, spec = found if found else ("", {})
        names = _input_names(spec)
        missing = sorted(set(definition["required"]) - names)
        blockers: list[str] = []
        if not object_info:
            blockers.append("Live ComfyUI /object_info is unavailable.")
        elif not found:
            blockers.append(f"The installed Comfy profile does not expose any supported node for {operation}: {', '.join(_definition_candidates(definition))}.")
        elif missing:
            blockers.append(f"Live node {node_name} is missing verified input(s): {', '.join(missing)}.")
        rows.append({
            "id": operation,
            "label": definition["label"],
            "available": not blockers,
            "node_class": node_name,
            "node_candidates": list(_definition_candidates(definition)),
            "required_inputs": list(definition["required"]),
            "input_names": sorted(names),
            "mask_inputs": list(definition.get("mask_inputs") or []),
            "mask_output": definition.get("mask_output"),
            "image_output": definition.get("image_output"),
            "blockers": blockers,
            "execution_policy": "live_object_info_exact_node_and_input_contract_only",
        })
    available = [row for row in rows if row["available"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "operations": rows,
        "available": bool(available),
        "mask_operations": ["union", "intersection", "difference"],
        "limits": {"max_mask_files": MAX_MASK_FILES},
        "safety": {"requires_live_object_info": True, "no_silent_fallback": True, "path_policy": "portable_identifiers_only"},
    }


def normalize_mask_utility(source: dict[str, Any] | None) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    operation = str(raw.get("mask_utility_operation") or "enhance").strip().lower()
    if operation not in VALID_OPERATIONS:
        operation = "enhance"
    combine_mode = str(raw.get("mask_utility_mask_operation") or "union").strip().lower()
    if combine_mode not in VALID_MASK_OPERATIONS:
        combine_mode = "union"
    channel = str(raw.get("mask_utility_mask_channel") or "red").strip().lower()
    if channel not in {"alpha", "red", "green", "blue"}:
        channel = "red"
    extract_mode = str(raw.get("mask_utility_extract_mode") or "extract_masked_area").strip().lower()
    if extract_mode not in {"extract_masked_area", "apply_mask", "invert_mask"}:
        extract_mode = "extract_masked_area"
    background = str(raw.get("mask_utility_background") or "Alpha").strip()
    if background not in {"Alpha", "original", "Color"}:
        background = "Alpha"
    threshold = _bounded_number(raw.get("mask_utility_threshold", 10), 10, 0, 255, integer=True)
    sensitivity = _bounded_number(raw.get("mask_utility_sensitivity", 1.0), 1.0, 0.0, 1.0)
    return {
        "enabled": raw.get("mask_utility_enabled", raw.get("workflow_mode") == "mask_utility") is True or str(raw.get("mask_utility_enabled", "")).strip().lower() in {"1", "true", "yes", "on"},
        "operation": operation,
        "node_class": str(raw.get("mask_utility_node_class") or "").strip(),
        "input_names": [str(item) for item in (raw.get("mask_utility_input_names") or []) if str(item).strip()],
        "mask_names": [_portable_identifier(item) for item in (raw.get("mask_utility_mask_names") or []) if _portable_identifier(item)][:MAX_MASK_FILES],
        "mask_operation": combine_mode,
        "mask_channel": channel,
        "extract_mode": extract_mode,
        "background": background,
        "background_color": str(raw.get("mask_utility_background_color") or "#FFFFFF").strip()[:16],
        "color": str(raw.get("mask_utility_color") or "#FFFFFF").strip()[:16],
        "threshold": threshold,
        "sensitivity": sensitivity,
        "mask_blur": _bounded_number(raw.get("mask_utility_mask_blur", 0), 0, 0, 64, integer=True),
        "mask_offset": _bounded_number(raw.get("mask_utility_mask_offset", 0), 0, -64, 64, integer=True),
        "smooth": _bounded_number(raw.get("mask_utility_smooth", 0.0), 0.0, 0.0, 128.0),
        "fill_holes": raw.get("mask_utility_fill_holes", False) is True or str(raw.get("mask_utility_fill_holes", "")).strip().lower() in {"1", "true", "yes", "on"},
        "invert": raw.get("mask_utility_invert", False) is True or str(raw.get("mask_utility_invert", "")).strip().lower() in {"1", "true", "yes", "on"},
        "padding": _bounded_number(raw.get("mask_utility_padding", 0), 0, 0, 256, integer=True),
        "overlay_opacity": _bounded_number(raw.get("mask_utility_overlay_opacity", 0.5), 0.5, 0.0, 1.0),
        "overlay_color": str(raw.get("mask_utility_overlay_color") or "#0000FF").strip()[:16],
        "lama_removal_strength": _bounded_number(raw.get("mask_utility_lama_removal_strength", 230), 230, 0, 255, integer=True),
        "lama_edge_smoothness": _bounded_number(raw.get("mask_utility_lama_edge_smoothness", 8), 8, 0, 20, integer=True),
        "resize_width": _bounded_number(raw.get("mask_utility_resize_width", 0), 0, 0, 8192, integer=True),
        "resize_height": _bounded_number(raw.get("mask_utility_resize_height", 0), 0, 0, 8192, integer=True),
        "resize_megapixels": _bounded_number(raw.get("mask_utility_resize_megapixels", 0), 0, 0, 16.0),
        "resize_scale_by": _bounded_number(raw.get("mask_utility_resize_scale_by", 1.0), 1.0, 0.01, 8.0),
        "resize_mode": str(raw.get("mask_utility_resize_mode") or "longest_side").strip().lower() if str(raw.get("mask_utility_resize_mode") or "longest_side").strip().lower() in {"longest_side", "shortest_side"} else "longest_side",
        "resize_value": _bounded_number(raw.get("mask_utility_resize_value", 0), 0, 0, 8192, integer=True),
        "resize_method": str(raw.get("mask_utility_resize_method") or "lanczos").strip().lower() if str(raw.get("mask_utility_resize_method") or "lanczos").strip().lower() in {"nearest-exact", "bilinear", "area", "bicubic", "lanczos"} else "lanczos",
        "resize_device": str(raw.get("mask_utility_resize_device") or "cpu").strip().lower() if str(raw.get("mask_utility_resize_device") or "cpu").strip().lower() in {"cpu", "gpu"} else "cpu",
        "resize_divisible_by": _bounded_number(raw.get("mask_utility_resize_divisible_by", 2), 2, 1, 512, integer=True),
        "resize_output_mode": str(raw.get("mask_utility_resize_output_mode") or "stretch").strip().lower() if str(raw.get("mask_utility_resize_output_mode") or "stretch").strip().lower() in {"stretch", "pad", "pad_edge", "pad_edge_pixel", "crop", "pillarbox_blur"} else "stretch",
        "resize_crop_position": str(raw.get("mask_utility_resize_crop_position") or "center").strip().lower() if str(raw.get("mask_utility_resize_crop_position") or "center").strip().lower() in {"center", "top", "bottom", "left", "right"} else "center",
        "resize_pad_color": str(raw.get("mask_utility_resize_pad_color") or "#FFFFFF").strip()[:16],
        "crop_width": _bounded_number(raw.get("mask_utility_crop_width", 1024), 1024, 0, 8192, integer=True),
        "crop_height": _bounded_number(raw.get("mask_utility_crop_height", 1024), 1024, 0, 8192, integer=True),
        "crop_x_offset": _bounded_number(raw.get("mask_utility_crop_x_offset", 0), 0, -8192, 8192, integer=True),
        "crop_y_offset": _bounded_number(raw.get("mask_utility_crop_y_offset", 0), 0, -8192, 8192, integer=True),
        "crop_position": str(raw.get("mask_utility_crop_position") or "center").strip().lower() if str(raw.get("mask_utility_crop_position") or "center").strip().lower() in {"top-left", "top-center", "top-right", "right-center", "bottom-right", "bottom-center", "bottom-left", "left-center", "center"} else "center",
        "crop_split": raw.get("mask_utility_crop_split", False) is True or str(raw.get("mask_utility_crop_split", "")).strip().lower() in {"1", "true", "yes", "on"},
    }


def resolve_mask_utility(catalog: dict[str, Any] | None, operation: str) -> dict[str, Any]:
    operation = operation if operation in VALID_OPERATIONS else "enhance"
    rows = [row for row in (catalog or {}).get("operations", []) if isinstance(row, dict)]
    row = next((item for item in rows if item.get("id") == operation), None)
    if not row or not row.get("available"):
        return {"ready": False, "operation": operation, "blockers": list((row or {}).get("blockers") or [f"Mask utility operation is unavailable: {operation}."])}
    return {"ready": True, "operation": operation, "row": row, "blockers": []}
