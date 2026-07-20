from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping


SCHEMA = "neo.image.qwen_stitch.v1"
VERSION = 1

DEFAULT_IMAGE_LANE_LIMIT = 3
MAX_IMAGE_LANE_LIMIT = 4
BASE_IMAGE_LANE = 1
STITCH_INPUTS_PER_GROUP = 2

# Stitch is a shared image-source capability. Qwen Rapid AIO keeps the
# multi-lane behavior; other image routes consume the stitched result as their
# normal Image 1/source anchor.
SUPPORTED_ROUTE_MODES = {
    ("sdxl", "checkpoint"): {"img2img", "inpaint", "outpaint"},
    ("sd15", "checkpoint"): {"img2img", "inpaint", "outpaint"},
    ("flux", "diffusion_model"): {"img2img", "inpaint", "outpaint"},
    ("flux", "gguf"): {"img2img", "inpaint", "outpaint"},
    ("flux1_fill", "diffusion_model"): {"inpaint", "outpaint"},
    ("flux2_klein", "diffusion_model"): {"img2img", "edit", "inpaint", "outpaint"},
    ("flux2_klein", "gguf"): {"img2img", "edit", "inpaint", "outpaint"},
    ("qwen_image", "diffusion_model"): {"img2img", "edit", "inpaint", "outpaint"},
    # Normal Qwen Image GGUF is single-source and does not expose the
    # semantic edit route in the current compile matrix. Qwen Image Edit
    # 2509 owns GGUF edit support below.
    ("qwen_image", "gguf"): {"img2img", "inpaint", "outpaint"},
    ("qwen_image_edit_2509", "diffusion_model"): {"img2img", "edit", "inpaint", "outpaint"},
    ("qwen_image_edit_2509", "gguf"): {"img2img", "edit", "inpaint", "outpaint"},
    ("qwen_rapid_aio", "checkpoint_aio"): {"img2img", "edit", "inpaint", "outpaint"},
    ("qwen_rapid_aio", "gguf"): {"img2img", "edit", "inpaint", "outpaint"},
    ("z_image", "diffusion_model"): {"img2img", "inpaint", "outpaint"},
    ("z_image", "gguf"): {"img2img", "inpaint", "outpaint"},
    ("z_image_turbo", "diffusion_model"): {"img2img", "inpaint", "outpaint"},
    ("z_image_turbo", "gguf"): {"img2img", "inpaint", "outpaint"},
}
SUPPORTED_FAMILIES = {family for family, _loader in SUPPORTED_ROUTE_MODES}
SUPPORTED_LOADERS = {loader for _family, loader in SUPPORTED_ROUTE_MODES}
SUPPORTED_MODES = {mode for modes in SUPPORTED_ROUTE_MODES.values() for mode in modes}
STITCH_DIRECTIONS = {"right", "down", "left", "up"}
DEFAULT_DIRECTION = "right"
DEFAULT_SPACING_COLOR = "black"


def _first_present(values: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = values.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_bool(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _safe_reference(value: Any) -> str:
    """Return a portable reference without retaining a personal filesystem path."""

    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith(("/api/", "http://", "https://", "data:")):
        return text
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def _safe_id(value: Any, fallback: str) -> str:
    text = str(value or "").strip().replace("\\", "_").replace("/", "_")
    text = "_".join(part for part in text.split() if part)
    return (text or fallback)[:80]


def _normalize_image_ref(value: Any, *, field: str, errors: list[dict[str, Any]]) -> dict[str, str]:
    raw = value if isinstance(value, Mapping) else {"ref": value}
    asset_id = _safe_reference(_first_present(raw, "asset_id", "upload_id", "source_id", "id"))
    reference = _first_present(raw, "ref", "reference", "path", "url", "name", "filename", "file")
    safe_ref = _safe_reference(reference)
    name = _safe_reference(_first_present(raw, "name", "filename", "file", "path", "url", "ref", "reference"))
    if not safe_ref and asset_id:
        safe_ref = asset_id
    if not name:
        name = safe_ref
    if not safe_ref:
        errors.append({"level": "error", "field": field, "message": "Stitch input is missing an image reference."})
    return {"ref": safe_ref, "name": name, "asset_id": asset_id}


def qwen_stitch_route_support(
    *,
    family: str | None,
    loader: str | None,
    mode: str | None,
) -> dict[str, Any]:
    """Describe whether Stitch Images is valid for the selected route."""

    normalized_family = str(family or "").strip().lower()
    normalized_loader = str(loader or "").strip().lower()
    normalized_mode = str(mode or "").strip().lower()
    supported = normalized_mode in SUPPORTED_ROUTE_MODES.get((normalized_family, normalized_loader), set())
    return {
        "supported": supported,
        "family": normalized_family,
        "loader": normalized_loader,
        "mode": normalized_mode,
        "reason": "available" if supported else "Stitch Images is unavailable for this family/loader/workflow route.",
    }


def image_stitch_route_support(*, family: str | None, loader: str | None, mode: str | None) -> dict[str, Any]:
    """Backend-neutral alias for the shared Stitch Images capability."""

    return qwen_stitch_route_support(family=family, loader=loader, mode=mode)


def qwen_stitch_capacity(image_lane_limit: Any = DEFAULT_IMAGE_LANE_LIMIT) -> dict[str, int]:
    """Calculate composite and raw-input capacity from the live Qwen lane limit."""

    limit = max(1, min(MAX_IMAGE_LANE_LIMIT, _as_int(image_lane_limit, DEFAULT_IMAGE_LANE_LIMIT)))
    composite_outputs = max(0, limit - BASE_IMAGE_LANE)
    return {
        "image_lane_limit": limit,
        "base_image_lane": BASE_IMAGE_LANE,
        "max_composite_outputs": composite_outputs,
        "max_raw_inputs": composite_outputs * STITCH_INPUTS_PER_GROUP,
        "inputs_per_group": STITCH_INPUTS_PER_GROUP,
    }


def _normalize_settings(raw: Any, *, field: str, warnings: list[dict[str, Any]]) -> dict[str, Any]:
    values = raw if isinstance(raw, Mapping) else {}
    direction = str(_first_present(values, "direction", "layout") or DEFAULT_DIRECTION).strip().lower()
    if direction not in STITCH_DIRECTIONS:
        warnings.append({"level": "warning", "field": f"{field}.direction", "message": f"Unsupported stitch direction normalized to {DEFAULT_DIRECTION}."})
        direction = DEFAULT_DIRECTION
    spacing_width = max(0, min(4096, _as_int(_first_present(values, "spacing_width", "spacing", "gap"), 0)))
    return {
        "direction": direction,
        "match_image_size": _as_bool(_first_present(values, "match_image_size", "match_size"), True),
        "spacing_width": spacing_width,
        "spacing_color": str(_first_present(values, "spacing_color", "color") or DEFAULT_SPACING_COLOR).strip() or DEFAULT_SPACING_COLOR,
    }


def _normalize_group(raw: Any, index: int, *, errors: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> dict[str, Any]:
    values = raw if isinstance(raw, Mapping) else {}
    group_id = _safe_id(_first_present(values, "id", "group_id", "stitch_id"), f"stitch_{index + 1}")
    inputs = values.get("inputs") if isinstance(values.get("inputs"), Mapping) else values
    image_a = _first_present(inputs, "image_a", "input_a", "a", "left", "source_a", "first")
    image_b = _first_present(inputs, "image_b", "input_b", "b", "right", "source_b", "second")
    normalized = {
        "id": group_id,
        "enabled": _as_bool(values.get("enabled"), True),
        "output_lane": max(0, _as_int(_first_present(values, "output_lane", "lane"), BASE_IMAGE_LANE + index + 1)),
        "inputs": {
            "image_a": _normalize_image_ref(image_a, field=f"groups[{index}].inputs.image_a", errors=errors),
            "image_b": _normalize_image_ref(image_b, field=f"groups[{index}].inputs.image_b", errors=errors),
        },
        "settings": _normalize_settings(values.get("settings"), field=f"groups[{index}].settings", warnings=warnings),
    }
    return normalized


def extract_qwen_stitch_payload(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract the canonical Stitch Images envelope from job parameters."""

    values = params if isinstance(params, Mapping) else {}
    nested = values.get("qwen_stitch")
    if isinstance(nested, Mapping):
        return deepcopy(dict(nested))
    return {
        "enabled": _as_bool(values.get("qwen_stitch_enabled"), False),
        "groups": deepcopy(values.get("qwen_stitch_groups") or []),
        "image_lane_limit": values.get("qwen_image_lane_limit", values.get("qwen_source_image_limit", DEFAULT_IMAGE_LANE_LIMIT)),
    }


def qwen_stitch_has_ready_group(raw: Mapping[str, Any] | None) -> bool:
    """Return whether Stitch Images can provide an image-conditioned lane.

    This is intentionally a small preflight predicate used before the normal
    direct Image 1 requirement. A valid Stitch Group may become Image 1 when
    the user chooses Stitch-only Img2Img.
    """

    values = raw if isinstance(raw, Mapping) else {}
    if not _as_bool(values.get("enabled"), False):
        return False
    groups = values.get("groups") if isinstance(values.get("groups"), list) else []
    for group in groups:
        if not isinstance(group, Mapping) or not _as_bool(group.get("enabled"), True):
            continue
        inputs = group.get("inputs") if isinstance(group.get("inputs"), Mapping) else group
        if not isinstance(inputs, Mapping):
            continue
        image_a = _first_present(inputs, "image_a", "input_a", "a", "left", "source_a", "first")
        image_b = _first_present(inputs, "image_b", "input_b", "b", "right", "source_b", "second")
        if image_a not in (None, "") and image_b not in (None, ""):
            return True
    return False


def image_stitch_has_ready_group(raw: Mapping[str, Any] | None) -> bool:
    """Shared alias used by non-Qwen source-anchor routes."""

    return qwen_stitch_has_ready_group(raw)


def normalize_qwen_stitch_payload(
    raw: Mapping[str, Any] | None,
    *,
    family: str | None = None,
    loader: str | None = None,
    mode: str | None = None,
    image_lane_limit: Any = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Normalize the Phase 2 Stitch Images contract without compiling nodes.

    The normalizer preserves invalid groups and reports errors so later compiler
    phases cannot silently discard user-selected images. Image 1 remains the
    primary source lane; each enabled Stitch Group consumes one additional Qwen
    image lane and combines exactly two raw inputs.
    """

    values = raw if isinstance(raw, Mapping) else {}
    route = qwen_stitch_route_support(
        family=family or values.get("family"),
        loader=loader or values.get("loader"),
        mode=mode or values.get("mode"),
    )
    requested_limit = image_lane_limit if image_lane_limit is not None else values.get("image_lane_limit", DEFAULT_IMAGE_LANE_LIMIT)
    capacity = qwen_stitch_capacity(requested_limit)
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    groups_raw = values.get("groups")
    if not isinstance(groups_raw, list):
        groups_raw = []
        if values.get("enabled"):
            errors.append({"level": "error", "field": "groups", "message": "Enabled Stitch Images payload must contain a groups list."})

    groups = [_normalize_group(group, index, errors=errors, warnings=warnings) for index, group in enumerate(groups_raw)]
    enabled_groups = [group for group in groups if group["enabled"]]
    if _as_bool(values.get("enabled"), False) and not route["supported"]:
        errors.append({"level": "error", "field": "route", "message": route["reason"]})
    if len(enabled_groups) > capacity["max_composite_outputs"]:
        errors.append({
            "level": "error",
            "field": "groups",
            "message": f"Stitch Images has {len(enabled_groups)} enabled groups but this route supports {capacity['max_composite_outputs']} composite outputs.",
            "max_composite_outputs": capacity["max_composite_outputs"],
        })

    seen_ids: set[str] = set()
    seen_lanes: set[int] = set()
    for index, group in enumerate(enabled_groups):
        group_id = group["id"]
        if group_id in seen_ids:
            errors.append({"level": "error", "field": f"groups[{index}].id", "message": f"Duplicate Stitch Group id: {group_id}."})
        seen_ids.add(group_id)
        lane = group["output_lane"]
        if lane <= BASE_IMAGE_LANE or lane > capacity["image_lane_limit"]:
            errors.append({
                "level": "error",
                "field": f"groups[{index}].output_lane",
                "message": f"Stitch Group output lane {lane} is outside the available optional lanes 2-{capacity['image_lane_limit']}.",
            })
        if lane in seen_lanes:
            errors.append({"level": "error", "field": f"groups[{index}].output_lane", "message": f"Duplicate Stitch Group output lane: {lane}."})
        seen_lanes.add(lane)

    validation = {
        "ok": not errors,
        "errors": deepcopy(errors),
        "warnings": deepcopy(warnings),
    }
    normalized = {
        "schema": SCHEMA,
        "version": VERSION,
        "enabled": _as_bool(values.get("enabled"), False),
        "route": route,
        "image_lane_limit": capacity["image_lane_limit"],
        "base_image_lane": BASE_IMAGE_LANE,
        "groups": groups,
        "capacity": {
            **capacity,
            "enabled_group_count": len(enabled_groups),
            "raw_input_count": len(enabled_groups) * STITCH_INPUTS_PER_GROUP,
        },
        "validation": validation,
    }
    return normalized, errors + warnings
