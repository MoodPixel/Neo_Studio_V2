"""Phase RMBG-5 advanced matting and high-resolution edge contracts."""
from __future__ import annotations

from typing import Any

from .public_hygiene import portable_model_identifier


SCHEMA_ID = "neo.image.background_removal.matting.v1"
SCHEMA_VERSION = 1
MAX_PROCESS_RESOLUTION = 2560

_PROFILE_DEFINITIONS: dict[str, dict[str, Any]] = {
    "birefnet_hr": {
        "label": "BiRefNet HR",
        "node_class": "BiRefNetRMBG",
        "model_candidates": ("BiRefNet-HR", "BiRefNet-HR.safetensors", "General-HR.safetensors", "Matting-HR.safetensors"),
        "default_process_res": 2048,
        "requires_mask": False,
        "edge_focus": "high_resolution_edges",
    },
    "birefnet_matting": {
        "label": "BiRefNet Matting",
        "node_class": "BiRefNetRMBG",
        "model_candidates": ("BiRefNet-matting", "BiRefNet-matting.safetensors", "Matting.safetensors"),
        "default_process_res": 1024,
        "requires_mask": False,
        "edge_focus": "soft_alpha",
    },
    "birefnet_hr_matting": {
        "label": "BiRefNet HR Matting",
        "node_class": "BiRefNetRMBG",
        "model_candidates": ("BiRefNet-HR-matting", "BiRefNet-HR-matting.safetensors", "Matting-HR.safetensors"),
        "default_process_res": 2048,
        "requires_mask": False,
        "edge_focus": "high_resolution_soft_alpha",
    },
    "birefnet_lite_2k": {
        "label": "BiRefNet Lite 2K",
        "node_class": "BiRefNetRMBG",
        "model_candidates": ("BiRefNet_lite-2K", "BiRefNet_lite-2K.safetensors", "General-Lite-2K.safetensors"),
        "default_process_res": 2048,
        "requires_mask": False,
        "edge_focus": "high_resolution_edges_low_vram",
    },
    "sdmatte": {
        "label": "SDMatte",
        "node_class": "AILab_SDMatte",
        "model_candidates": ("SDMatte",),
        "default_process_res": 1024,
        "requires_mask": True,
        "edge_focus": "trimap_guided_alpha",
    },
    "sdmatte_plus": {
        "label": "SDMatte Plus",
        "node_class": "AILab_SDMatte",
        "model_candidates": ("SDMatte_plus", "SDMatte Plus"),
        "default_process_res": 1024,
        "requires_mask": True,
        "edge_focus": "trimap_guided_alpha_plus",
    },
}


def _input_names(spec: dict[str, Any]) -> set[str]:
    block = spec.get("input") if isinstance(spec, dict) else {}
    names: set[str] = set()
    if isinstance(block, dict):
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


def _model_choices(spec: dict[str, Any]) -> list[str]:
    block = spec.get("input") if isinstance(spec, dict) else {}
    required = block.get("required") if isinstance(block, dict) else {}
    raw = required.get("model") if isinstance(required, dict) else None
    values: Any = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        identifier = portable_model_identifier(value)
        key = identifier.casefold()
        if identifier and key not in seen:
            seen.add(key)
            result.append(identifier)
    return result


def _candidate_match(candidates: tuple[str, ...], choices: list[str]) -> str:
    by_full = {item.casefold(): item for item in choices}
    by_base: dict[str, list[str]] = {}
    for item in choices:
        by_base.setdefault(item.rsplit("/", 1)[-1].casefold(), []).append(item)
    for candidate in candidates:
        actual = by_full.get(candidate.casefold())
        if actual:
            return actual
        matches = by_base.get(candidate.rsplit("/", 1)[-1].casefold(), [])
        if len(matches) == 1:
            return matches[0]
    return ""


def build_matting_catalog(object_info: dict[str, Any] | None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for profile_id, definition in _PROFILE_DEFINITIONS.items():
        found = _find_node(object_info, definition["node_class"])
        node_name, spec = found if found else ("", {})
        names = _input_names(spec)
        required = {"image", "model"} if definition["node_class"] == "BiRefNetRMBG" else {"image", "model", "device", "process_res"}
        missing = sorted(required - names)
        choices = _model_choices(spec)
        blockers: list[str] = []
        if not object_info:
            blockers.append("Live ComfyUI /object_info is unavailable.")
        elif not found:
            blockers.append(f"The installed Comfy profile does not expose {definition['node_class']}.")
        elif missing:
            blockers.append(f"Live node {node_name} is missing verified input(s): {', '.join(missing)}.")
        elif not choices:
            blockers.append(f"Live node {node_name} did not expose a verified model choice list.")
        selected_default = _candidate_match(definition["model_candidates"], choices)
        if found and choices and not selected_default:
            blockers.append(f"No live model choice matches the {definition['label']} profile.")
        rows.append({
            "id": profile_id,
            "label": definition["label"],
            "available": not blockers,
            "node_class": node_name,
            "required_inputs": sorted(required),
            "input_names": sorted(names),
            "model_choices": choices,
            "default_model": selected_default,
            "default_process_res": definition["default_process_res"],
            "max_process_res": MAX_PROCESS_RESOLUTION,
            "requires_mask": bool(definition["requires_mask"]),
            "edge_focus": definition["edge_focus"],
            "blockers": blockers,
            "execution_policy": "live_object_info_exact_node_and_model_choice_only",
        })
    available = [row for row in rows if row["available"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "profiles": rows,
        "available": bool(available),
        "limits": {"max_process_resolution": MAX_PROCESS_RESOLUTION},
        "safety": {
            "requires_live_object_info": True,
            "requires_live_model_choice": True,
            "no_silent_fallback": True,
            "path_policy": "portable_identifiers_only",
        },
    }


def normalize_matting(source: dict[str, Any] | None) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    profile = str(raw.get("matting_profile") or "birefnet_hr").strip().lower()
    if profile not in _PROFILE_DEFINITIONS:
        profile = "birefnet_hr"
    node_class = str(raw.get("matting_node_class") or "").strip()
    model = portable_model_identifier(raw.get("matting_model") or "")
    device = str(raw.get("matting_device") or "Auto").strip()
    if device not in {"Auto", "CPU", "GPU"}:
        device = "Auto"
    try:
        process_res = int(float(raw.get("matting_process_res", _PROFILE_DEFINITIONS[profile]["default_process_res"])))
    except (TypeError, ValueError):
        process_res = _PROFILE_DEFINITIONS[profile]["default_process_res"]
    process_res = max(256, min(MAX_PROCESS_RESOLUTION, (process_res // 8) * 8))
    try:
        sensitivity = float(raw.get("matting_sensitivity", 0.9))
    except (TypeError, ValueError):
        sensitivity = 0.9
    sensitivity = max(0.1, min(1.0, sensitivity))
    try:
        mask_blur = int(float(raw.get("matting_mask_blur", 0)))
    except (TypeError, ValueError):
        mask_blur = 0
    try:
        mask_offset = int(float(raw.get("matting_mask_offset", 0)))
    except (TypeError, ValueError):
        mask_offset = 0
    edge_mode = str(raw.get("matting_edge_mode") or "high_resolution_edges").strip().lower()
    if edge_mode not in {"soft_alpha", "high_resolution_edges", "trimap_guided_alpha", "foreground_estimation"}:
        edge_mode = "high_resolution_edges"
    mask_names = [portable_model_identifier(item) for item in (raw.get("matting_mask_names") or [])]
    mask_names = [item for item in mask_names if item][:1]
    return {
        "enabled": raw.get("matting_enabled", raw.get("workflow_mode") == "matting") is True or str(raw.get("matting_enabled", "")).strip().lower() in {"1", "true", "yes", "on"},
        "profile": profile,
        "node_class": node_class,
        "input_names": [str(item) for item in (raw.get("matting_input_names") or []) if str(item).strip()],
        "model": model,
        "model_choices": [portable_model_identifier(item) for item in (raw.get("matting_model_choices") or []) if portable_model_identifier(item)],
        "process_res": process_res,
        "device": device,
        "mask_refine": raw.get("matting_mask_refine", True) is not False and str(raw.get("matting_mask_refine", "true")).strip().lower() not in {"0", "false", "no", "off"},
        "sensitivity": sensitivity,
        "transparent_object": raw.get("matting_transparent_object", True) is not False and str(raw.get("matting_transparent_object", "true")).strip().lower() not in {"0", "false", "no", "off"},
        "use_source_alpha": raw.get("matting_use_source_alpha", False) is True or str(raw.get("matting_use_source_alpha", "")).strip().lower() in {"1", "true", "yes", "on"},
        "edge_mode": edge_mode,
        "mask_blur": max(0, min(64, mask_blur)),
        "mask_offset": max(-64, min(64, mask_offset)),
        "invert": raw.get("matting_invert", False) is True or str(raw.get("matting_invert", "")).strip().lower() in {"1", "true", "yes", "on"},
        "background": str(raw.get("matting_background") or "Alpha").strip() if str(raw.get("matting_background") or "Alpha").strip() in {"Alpha", "Color"} else "Alpha",
        "background_color": str(raw.get("matting_background_color") or "#222222").strip()[:16],
        "refine_foreground": raw.get("matting_refine_foreground", False) is True or str(raw.get("matting_refine_foreground", "")).strip().lower() in {"1", "true", "yes", "on"},
        "mask_names": mask_names,
    }


def resolve_matting_profile(catalog: dict[str, Any] | None, profile: str, selected_model: str = "") -> dict[str, Any]:
    profile_id = profile if profile in _PROFILE_DEFINITIONS else "birefnet_hr"
    rows = [row for row in (catalog or {}).get("profiles", []) if isinstance(row, dict)]
    row = next((item for item in rows if item.get("id") == profile_id), None)
    if not row or not row.get("available"):
        return {"ready": False, "profile": profile_id, "blockers": list((row or {}).get("blockers") or [f"Matting profile is unavailable: {profile_id}." ])}
    choices = [str(item) for item in (row.get("model_choices") or []) if str(item).strip()]
    selected = portable_model_identifier(selected_model)
    if selected:
        actual = next((item for item in choices if item.casefold() == selected.casefold()), "")
        if not actual:
            return {"ready": False, "profile": profile_id, "blockers": [f"Selected matting model is not in the live choice list: {selected}." ]}
    else:
        actual = str(row.get("default_model") or "")
    if not actual:
        return {"ready": False, "profile": profile_id, "blockers": [f"No live model choice is available for matting profile: {profile_id}." ]}
    return {"ready": True, "profile": profile_id, "row": row, "model": actual, "blockers": []}
