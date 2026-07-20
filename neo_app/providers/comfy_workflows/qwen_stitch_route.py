from __future__ import annotations

from typing import Any, Mapping

from neo_app.image.qwen_stitch_contract import (
    DEFAULT_IMAGE_LANE_LIMIT,
    extract_qwen_stitch_payload,
    normalize_qwen_stitch_payload,
)


IMAGE_STITCH_NODE_CANDIDATES = ("ImageStitch", "AILab_ImageStitch")


def qwen_edit_image_lane_limit(
    backend_capabilities: Mapping[str, Any] | None,
    *,
    node_name: str = "TextEncodeQwenImageEditPlus",
    default: int = DEFAULT_IMAGE_LANE_LIMIT,
) -> int:
    """Read the installed Qwen encoder's imageN inputs with a safe fallback."""

    node_map = (backend_capabilities or {}).get("object_info_node_inputs") if isinstance(backend_capabilities, Mapping) else {}
    node = node_map.get(node_name) if isinstance(node_map, Mapping) else None
    names = node.get("all") if isinstance(node, Mapping) else []
    lanes = []
    for name in names or []:
        text = str(name)
        if text.startswith("image") and text[5:].isdigit():
            lanes.append(int(text[5:]))
    if not lanes:
        return max(1, min(4, int(default or DEFAULT_IMAGE_LANE_LIMIT)))
    return max(1, min(4, max(lanes)))


def image_stitch_available(backend_capabilities: Mapping[str, Any] | None) -> tuple[bool, str]:
    """Return ImageStitch availability across supported Comfy node aliases."""

    node_name, source = image_stitch_node_name(backend_capabilities)
    return bool(node_name), source


def image_stitch_node_name(backend_capabilities: Mapping[str, Any] | None) -> tuple[str | None, str]:
    """Select the installed stitch class from the live object_info slice.

    ComfyUI-RMBG registers ``AILab_ImageStitch`` even though the visible node
    is described as ImageStitch. Other packs may register the shorter
    ``ImageStitch`` class. When object_info is unavailable, retain the
    canonical class as a diagnostic fallback; the provider will still
    validate the final prompt with ComfyUI.
    """

    node_map = (backend_capabilities or {}).get("object_info_node_inputs") if isinstance(backend_capabilities, Mapping) else {}
    if not isinstance(node_map, Mapping) or not node_map:
        return "ImageStitch", "capability_unknown_assume_canonical"
    for node_name in IMAGE_STITCH_NODE_CANDIDATES:
        if isinstance(node_map.get(node_name), Mapping):
            return node_name, "live_object_info"
    return None, "live_object_info"


def _stitch_node_input_names(
    backend_capabilities: Mapping[str, Any] | None,
    node_name: str,
) -> set[str]:
    node_map = (backend_capabilities or {}).get("object_info_node_inputs") if isinstance(backend_capabilities, Mapping) else {}
    node = node_map.get(node_name) if isinstance(node_map, Mapping) else None
    names = node.get("all") if isinstance(node, Mapping) else []
    return {str(name) for name in names or []}


def _stitch_direction_value(direction: str, input_names: set[str]) -> tuple[str, str]:
    """Map Neo's normalized direction to the installed node's field/value."""

    normalized = str(direction or "right").strip().lower()
    if "concat_direction" in input_names:
        return "concat_direction", {"down": "bottom", "up": "top"}.get(normalized, normalized)
    return "direction", normalized


def _stitch_inputs(
    settings: Mapping[str, Any],
    load_a_id: str,
    load_b_id: str,
    *,
    backend_capabilities: Mapping[str, Any] | None,
    node_name: str,
) -> dict[str, Any]:
    """Build only inputs declared by the selected live stitch node."""

    declared = _stitch_node_input_names(backend_capabilities, node_name)
    # With no live catalog, use the original canonical contract. With a live
    # catalog, undeclared inputs must not be sent because Comfy rejects them.
    known_schema = bool(declared)
    direction_key, direction_value = _stitch_direction_value(settings.get("direction", "right"), declared)
    # ComfyUI-RMBG exposes AILab_ImageStitch with a list-valued ``images``
    # input, while other stitch packs expose the canonical image1/image2
    # pair. Adapt to the live schema instead of sending undeclared inputs.
    if known_schema and "images" in declared and not {"image1", "image2"}.issubset(declared):
        inputs: dict[str, Any] = {"images": [[load_a_id, 0], [load_b_id, 0]]}
    else:
        inputs = {
            "image1": [load_a_id, 0],
            "image2": [load_b_id, 0],
        }
    if not known_schema or direction_key in declared:
        inputs[direction_key] = direction_value
    optional_values = {
        "match_image_size": settings.get("match_image_size"),
        "spacing_width": settings.get("spacing_width"),
        "spacing_color": settings.get("spacing_color"),
    }
    for name, value in optional_values.items():
        if not known_schema or name in declared:
            inputs[name] = value
    return inputs


def _image_ref(group: Mapping[str, Any], side: str) -> str:
    inputs = group.get("inputs") if isinstance(group.get("inputs"), Mapping) else {}
    item = inputs.get(side) if isinstance(inputs, Mapping) else {}
    if isinstance(item, Mapping):
        return str(item.get("ref") or item.get("name") or "").strip()
    return str(item or "").strip()


def apply_qwen_stitch_route(
    workflow: dict[str, Any],
    params: Mapping[str, Any] | None,
    qwen_inputs: dict[str, list[Any]],
    next_id: int,
    *,
    family: str,
    loader: str,
    mode: str,
    backend_capabilities: Mapping[str, Any] | None = None,
    edit_node: str = "TextEncodeQwenImageEditPlus",
) -> tuple[int, dict[str, list[Any]], dict[str, Any], list[str], list[str]]:
    """Compile normalized Stitch Groups into LoadImage + ImageStitch nodes.

    This helper intentionally handles only the Comfy route. UI state, upload
    storage, and replay wiring remain separate phases. Each Stitch Group emits
    one Qwen image lane and consumes two raw image inputs.
    """

    raw = extract_qwen_stitch_payload(params)
    # Rapid AIO keeps optional stitched image lanes for Img2Img/Edit. For
    # Inpaint/Outpaint the shared provider-level source patch supplies one
    # composite Image 1 so mask/canvas routing remains single-source.
    if family == "qwen_rapid_aio" and str(mode or "").lower() not in {"img2img", "edit"}:
        return next_id, qwen_inputs, {
            "schema": str(raw.get("schema") or "neo.image.qwen_stitch.v1"),
            "enabled": bool(raw.get("enabled")),
            "route": {"family": family, "loader": loader, "mode": mode},
            "source_mode": "composite_source",
            "groups": [],
            "skipped": "single_source_mask_canvas_route",
        }, ["Stitch Images will be applied as the composite Image 1 source for this mask/canvas route."], []
    lane_limit = qwen_edit_image_lane_limit(backend_capabilities, node_name=edit_node)
    normalized, diagnostics = normalize_qwen_stitch_payload(
        raw,
        family=family,
        loader=loader,
        mode=mode,
        image_lane_limit=lane_limit,
    )
    active_groups = [group for group in normalized["groups"] if group.get("enabled")]
    meta = {
        "schema": normalized["schema"],
        "version": normalized["version"],
        "enabled": normalized["enabled"],
        "route": normalized["route"],
        "image_lane_limit": normalized["image_lane_limit"],
        "capacity": normalized["capacity"],
        "groups": [],
        "node": "ImageStitch",
        "validation": normalized["validation"],
    }
    notes: list[str] = []
    errors = [str(item.get("message") or "Stitch Images validation failed.") for item in normalized["validation"]["errors"]]
    warnings = [str(item.get("message") or "Stitch Images warning.") for item in normalized["validation"]["warnings"]]
    if not normalized["enabled"]:
        return next_id, qwen_inputs, meta, warnings, errors

    stitch_node, availability_source = image_stitch_node_name(backend_capabilities)
    stitch_available = bool(stitch_node)
    meta["node_availability"] = {
        "available": stitch_available,
        "source": availability_source,
        "selected_class": stitch_node or "",
    }
    if not stitch_available:
        errors.append("ComfyUI ImageStitch is not available in the selected backend object_info catalog (checked ImageStitch and AILab_ImageStitch).")
    if errors:
        meta["validation"] = {**normalized["validation"], "ok": False, "errors": [{"level": "error", "message": message} for message in errors]}
        return next_id, qwen_inputs, meta, warnings, errors

    occupied_lanes = {int(key[5:]) for key in qwen_inputs if key.startswith("image") and key[5:].isdigit()}
    for group in active_groups:
        lane = int(group["output_lane"])
        if lane in occupied_lanes:
            errors.append(f"Stitch Group {group['id']} output lane {lane} is already occupied by a direct Qwen source lane.")
            continue
        image_a = _image_ref(group, "image_a")
        image_b = _image_ref(group, "image_b")
        if not image_a or not image_b:
            continue
        load_a_id = str(next_id)
        workflow[load_a_id] = {"class_type": "LoadImage", "inputs": {"image": image_a, "upload": "image"}}
        next_id += 1
        load_b_id = str(next_id)
        workflow[load_b_id] = {"class_type": "LoadImage", "inputs": {"image": image_b, "upload": "image"}}
        next_id += 1
        settings = group["settings"]
        stitch_id = str(next_id)
        workflow[stitch_id] = {
            "class_type": stitch_node or "ImageStitch",
            "inputs": _stitch_inputs(
                settings,
                load_a_id,
                load_b_id,
                backend_capabilities=backend_capabilities,
                node_name=stitch_node or "ImageStitch",
            ),
        }
        next_id += 1
        qwen_inputs[f"image{lane}"] = [stitch_id, 0]
        occupied_lanes.add(lane)
        meta["groups"].append({
            "id": group["id"],
            "output_lane": lane,
            "raw_input_names": [group["inputs"]["image_a"]["name"], group["inputs"]["image_b"]["name"]],
            "workflow_nodes": {"load_image_a": load_a_id, "load_image_b": load_b_id, "image_stitch": stitch_id},
            "settings": dict(settings),
        })
        notes.append(f"Qwen Stitch Group {group['id']} compiled to image{lane} using ImageStitch.")

    if "image1" not in qwen_inputs:
        # Stitch-only Img2Img is valid: the first compiled Stitch Group becomes
        # the base Image 1 lane, while later groups keep their selected lanes.
        first_compiled = meta["groups"][0] if meta["groups"] else None
        if first_compiled:
            original_lane = int(first_compiled["output_lane"])
            original_ref = qwen_inputs.pop(f"image{original_lane}", None)
            if original_ref:
                qwen_inputs["image1"] = list(original_ref)
                first_compiled["effective_output_lane"] = 1
                first_compiled["promoted_to_base"] = True
                meta["base_source_mode"] = "stitch_group"
                notes.append(f"Qwen Stitch Group {first_compiled['id']} promoted to base Image 1 because no direct source image was selected.")
        if "image1" not in qwen_inputs:
            errors.append("Qwen Stitch Images requires at least one complete Stitch Group when no direct source image is selected.")

    if errors:
        meta["validation"] = {**normalized["validation"], "ok": False, "errors": [{"level": "error", "message": message} for message in errors]}
    else:
        meta["validation"] = {**normalized["validation"], "ok": True}
    meta["compiled_group_count"] = len(meta["groups"])
    return next_id, qwen_inputs, meta, [*warnings, *notes], errors
