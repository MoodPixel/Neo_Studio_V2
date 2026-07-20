from __future__ import annotations

"""Live contract helpers for the upstream ComfyUI-RMBG generic node.

The upstream package registers one generic node as ``RMBG``.  Neo keeps this
route separate from the BiRefNet-specific nodes so model discovery and prompt
graphs cannot silently cross the two node families.
"""

from typing import Any

from .public_hygiene import portable_model_identifiers

RMBG_NODE_CLASS = "RMBG"
RMBG_CATALOG_SCHEMA_ID = "neo.image.background_removal.rmbg_node.v1"
RMBG_INPUT_ALIASES = {
    "image": ("image",),
    "model": ("model",),
    "sensitivity": ("sensitivity",),
    "process_res": ("process_res",),
    "mask_blur": ("mask_blur",),
    "mask_offset": ("mask_offset",),
    "invert_output": ("invert_output",),
    "refine_foreground": ("refine_foreground",),
    "background": ("background",),
    "background_color": ("background_color",),
}


def _input_groups(node_spec: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    spec = node_spec if isinstance(node_spec, dict) else {}
    inputs = spec.get("input") if isinstance(spec.get("input"), dict) else {}
    required = inputs.get("required") if isinstance(inputs.get("required"), dict) else {}
    optional = inputs.get("optional") if isinstance(inputs.get("optional"), dict) else {}
    return required, optional


def _choice_values(spec: Any) -> list[str]:
    if not isinstance(spec, (list, tuple)) or not spec:
        return []
    choices = spec[0]
    if not isinstance(choices, (list, tuple, set)):
        return []
    return [str(item).strip() for item in choices if str(item).strip()]


def _first_input(groups: tuple[dict[str, Any], dict[str, Any]], canonical: str) -> str:
    required, optional = groups
    for candidate in RMBG_INPUT_ALIASES.get(canonical, (canonical,)):
        if candidate in required or candidate in optional:
            return candidate
    return ""


def build_rmbg_node_catalog(object_info: dict[str, Any] | None) -> dict[str, Any]:
    """Build a public-safe readiness record from live Comfy ``/object_info``."""

    info = object_info if isinstance(object_info, dict) else {}
    node_spec = info.get(RMBG_NODE_CLASS)
    groups = _input_groups(node_spec)
    input_map = {canonical: _first_input(groups, canonical) for canonical in RMBG_INPUT_ALIASES}
    missing = [name for name in ("image", "model") if not input_map.get(name)]
    model_choices = portable_model_identifiers(_choice_values((groups[0] or {}).get(input_map.get("model", ""))), "rmbg")
    if not model_choices:
        model_choices = portable_model_identifiers(_choice_values((groups[1] or {}).get(input_map.get("model", ""))), "rmbg")
    blockers: list[str] = []
    if not node_spec:
        blockers.append("ComfyUI-RMBG generic node RMBG is not registered in live /object_info.")
    if missing:
        blockers.append("ComfyUI-RMBG RMBG is missing required input(s): " + ", ".join(missing) + ".")
    if not model_choices:
        blockers.append("No live model choices were exposed by the ComfyUI-RMBG RMBG node.")
    available = bool(node_spec and not missing and model_choices)
    return {
        "schema_id": RMBG_CATALOG_SCHEMA_ID,
        "available": available,
        "node_class": RMBG_NODE_CLASS if node_spec else "",
        "model_choices": model_choices,
        "input_map": {key: value for key, value in input_map.items() if value},
        "required_inputs": sorted(groups[0].keys()),
        "optional_inputs": sorted(groups[1].keys()),
        "blockers": blockers,
        "source": "live_comfy_object_info",
        "path_policy": "portable_identifiers_only",
    }


def build_rmbg_node_graph(source_image_name: str, settings: dict[str, Any]) -> tuple[dict[str, Any], list[Any], list[Any], list[str]]:
    """Compile the generic RMBG node graph from a previously verified contract."""

    image_name = str(source_image_name or "").strip()
    node_class = str(settings.get("rmbg_node_class") or "").strip()
    model_name = str(settings.get("rmbg_model") or "").strip().replace("\\", "/")
    input_map = settings.get("rmbg_input_map") if isinstance(settings.get("rmbg_input_map"), dict) else {}
    image_input = str(input_map.get("image") or "").strip()
    model_input = str(input_map.get("model") or "").strip()
    if not image_name:
        raise ValueError("Background Removal needs a source image.")
    if not node_class or not image_input or not model_input:
        raise ValueError("ComfyUI-RMBG RMBG node contract is unavailable. Refresh the live Comfy node catalog.")
    if not model_name:
        raise ValueError("Choose an installed ComfyUI-RMBG model before removing the background.")

    inputs: dict[str, Any] = {image_input: ["1", 0], model_input: model_name}
    values = {
        "sensitivity": float(settings.get("rmbg_sensitivity") if settings.get("rmbg_sensitivity") is not None else 1.0),
        "process_res": int(settings.get("width") or 1024),
        "mask_blur": int(settings.get("rmbg_mask_blur") or 0),
        "mask_offset": int(settings.get("rmbg_mask_offset") or 0),
        "invert_output": bool(settings.get("rmbg_invert_output")),
        "refine_foreground": bool(settings.get("rmbg_refine_foreground")),
        "background": str(settings.get("rmbg_background") or "Alpha"),
        "background_color": str(settings.get("rmbg_background_color") or "#222222"),
    }
    for canonical, value in values.items():
        input_name = str(input_map.get(canonical) or "").strip()
        if input_name:
            inputs[input_name] = value
    graph = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "2": {"class_type": node_class, "inputs": inputs},
    }
    return graph, ["2", 0], ["2", 1], [f"ComfyUI-RMBG generic RMBG node uses {model_name}." ]
