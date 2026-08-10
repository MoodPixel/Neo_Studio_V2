from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import CFG_SAFETY_CAP, EXTENSION_ID, PHASE
from .model_catalog import prepare_detailer_assets_for_execution
from .model_source import (
    DEDICATED_CHECKPOINT_SOURCE,
    GENERATION_MODEL_SOURCE,
    add_dedicated_prompt_nodes,
    apply_detailer_model_source_plan,
    prompt_text_for_runtime_unit,
    public_model_source_metadata,
)
from .lora_branch import (
    INHERIT_ALL,
    apply_detailer_lora_branch,
    prompt_text_for_lora_runtime_unit,
    public_lora_branch_metadata,
    validate_sampling_reapply_contract,
)
from .payload_schema import parse_sep_targets
from .family_presets import materialize_family_preset, public_family_preset_metadata
from .identity_policy import public_identity_policy_metadata
from .route_contract import build_adetailer_route_contract, contract_metadata, normalize_adetailer_route_contract
from .validation import validate_and_normalize_payload
from .diagnostics import append_diagnostic_issue, refresh_prequeue_diagnostics, validate_graph_invariants


def _next_graph_id(workflow: dict[str, Any], preferred: int | str | None = None) -> str:
    if preferred is not None:
        candidate = str(preferred)
        if candidate not in workflow:
            return candidate
    numeric_ids: list[int] = []
    for key in workflow:
        try:
            numeric_ids.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return str((max(numeric_ids) if numeric_ids else 0) + 1)


def _copy_ref(ref: Any, fallback: list[Any] | None = None) -> list[Any]:
    if isinstance(ref, (list, tuple)) and len(ref) >= 2:
        idx = ref[1]
        if isinstance(idx, str) and idx.isdigit():
            idx = int(idx)
        return [str(ref[0]), idx]
    return deepcopy(fallback or [])




def _clamp_int(value: Any, default: int, lo: int, hi: int) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _clamp_float(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    return max(lo, min(hi, number))


def _find_canvas_size(workflow: dict[str, Any], params: dict[str, Any] | None = None) -> tuple[int, int]:
    src = params if isinstance(params, dict) else {}
    width = _clamp_int(src.get("width"), 0, 0, 16384)
    height = _clamp_int(src.get("height"), 0, 0, 16384)
    if width and height:
        return width, height
    for node in workflow.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if node.get("class_type") in {"EmptyLatentImage", "EmptySD3LatentImage", "ImageResizeKJ", "ImageScaleToTotalPixels"}:
            w = _clamp_int(inputs.get("width"), 0, 0, 16384)
            h = _clamp_int(inputs.get("height"), 0, 0, 16384)
            if w and h:
                return w, h
    return 1024, 1024


def _parse_manual_boxes_for_canvas(raw_value: Any, width: int, height: int) -> list[dict[str, float]]:
    """Parse V1 manual-box text into normalized x/y/w/h boxes for MaskToSEGS.

    Supports V1 examples: xywh:120,80,300,300, xyxy:120,80,420,380,
    and percentage components like 12%,10%,28%,28%.
    """
    raw_text = str(raw_value or "").strip()
    if not raw_text or width <= 0 or height <= 0:
        return []

    def parse_component(token: str, base: int) -> float:
        value = str(token or "").strip()
        if not value:
            raise ValueError("empty coordinate")
        if value.endswith("%"):
            return max(0.0, min(1.0, float(value[:-1]) / 100.0))
        number = float(value)
        # Values <= 1 are treated as already normalized; larger values are pixels.
        return max(0.0, min(1.0, number if 0.0 <= number <= 1.0 else number / float(base)))

    boxes: list[dict[str, float]] = []
    # JSON-like values are already normalized/absolute in Phase F2 normalization, but
    # the V1 textarea path is line-based; keep both supported.
    lines = [line.strip() for line in str(raw_text).replace(";", "\n").splitlines() if line.strip()]
    for line in lines:
        cleaned = line.strip()
        mode = "xywh"
        lowered = cleaned.lower()
        if lowered.startswith("xyxy:"):
            mode = "xyxy"
            cleaned = cleaned.split(":", 1)[1]
        elif lowered.startswith("xywh:"):
            cleaned = cleaned.split(":", 1)[1]
        parts = [part.strip() for part in cleaned.split(",") if part.strip()]
        if len(parts) < 4:
            # Fall back to the permissive parser used by Phase F2.
            import re
            parts = re.findall(r"[-+]?\d*\.?\d+%?", cleaned)[:4]
        if len(parts) < 4:
            continue
        try:
            if mode == "xyxy":
                x1 = parse_component(parts[0], width)
                y1 = parse_component(parts[1], height)
                x2 = parse_component(parts[2], width)
                y2 = parse_component(parts[3], height)
                x = min(x1, x2)
                y = min(y1, y2)
                w = abs(x2 - x1)
                h = abs(y2 - y1)
            else:
                x = parse_component(parts[0], width)
                y = parse_component(parts[1], height)
                w = parse_component(parts[2], width)
                h = parse_component(parts[3], height)
            x = max(0.0, min(1.0, x))
            y = max(0.0, min(1.0, y))
            w = max(0.0, min(1.0 - x, w))
            h = max(0.0, min(1.0 - y, h))
            if w > 0 and h > 0:
                boxes.append({"x": round(x, 6), "y": round(y, 6), "w": round(w, 6), "h": round(h, 6)})
        except Exception:
            continue
    return boxes


def _build_rect_mask_layers(graph: dict[str, Any], next_id: str, width: int, height: int, box: dict[str, float]) -> tuple[str, list[tuple[list[Any], float]]]:
    x_px = max(0, min(width - 8, int(round(width * float(box.get("x") or 0.0)))))
    y_px = max(0, min(height - 8, int(round(height * float(box.get("y") or 0.0)))))
    region_width = max(8, min(width - x_px, int(round(width * float(box.get("w") or 0.33)))))
    region_height = max(8, min(height - y_px, int(round(height * float(box.get("h") or 0.33)))))
    base_mask_id = next_id
    graph[base_mask_id] = {"class_type": "SolidMask", "inputs": {"value": 0.0, "width": width, "height": height}}
    region_mask_id = _next_graph_id(graph)
    graph[region_mask_id] = {"class_type": "SolidMask", "inputs": {"value": 1.0, "width": region_width, "height": region_height}}
    composite_id = _next_graph_id(graph)
    graph[composite_id] = {"class_type": "MaskComposite", "inputs": {"destination": [base_mask_id, 0], "source": [region_mask_id, 0], "x": x_px, "y": y_px, "operation": "add"}}
    return _next_graph_id(graph), [([composite_id, 0], 1.0)]


def _build_manual_detailer_segs(
    graph: dict[str, Any],
    next_id: str,
    *,
    width: int,
    height: int,
    manual_box: dict[str, float],
    params: dict[str, Any],
    node_status: dict[str, Any],
) -> tuple[str, list[Any] | None, list[str]]:
    available = set(node_status.get("available_nodes") or [])
    needed = {"MaskToSEGS", "SolidMask", "MaskComposite"}
    if "MaskToSEGS" not in available:
        return next_id, None, ["manual boxes require MaskToSEGS; manual pass skipped safely"]
    next_id, mask_layers = _build_rect_mask_layers(graph, next_id, width, height, manual_box)
    if not mask_layers:
        return next_id, None, ["manual box could not be converted into a mask"]
    mask_ref = _copy_ref(mask_layers[0][0])
    mask_to_segs_id = next_id
    graph[mask_to_segs_id] = {"class_type": "MaskToSEGS", "inputs": {"mask": mask_ref, "combined": False, "crop_factor": 1.12, "bbox_fill": False, "drop_size": 1, "contour_fill": False}}
    segs_ref: list[Any] = [mask_to_segs_id, 0]
    next_id = _next_graph_id(graph)
    available = set(node_status.get("available_nodes") or [])
    dilation = max(0, int(params.get("bbox_grow") or 0))
    if dilation and "ImpactDilateMaskInSEGS" in available:
        dilate_id = next_id
        graph[dilate_id] = {"class_type": "ImpactDilateMaskInSEGS", "inputs": {"segs": _copy_ref(segs_ref), "dilation": dilation}}
        segs_ref = [dilate_id, 0]
        next_id = _next_graph_id(graph)
    blur_value = max(0, int(params.get("mask_blur") or 0))
    if blur_value and "ImpactGaussianBlurMaskInSEGS" in available:
        blur_id = next_id
        kernel_size = max(3, blur_value * 2 + 1)
        graph[blur_id] = {"class_type": "ImpactGaussianBlurMaskInSEGS", "inputs": {"segs": _copy_ref(segs_ref), "kernel_size": kernel_size, "sigma": max(1.0, round(blur_value / 2.0, 2))}}
        segs_ref = [blur_id, 0]
        next_id = _next_graph_id(graph)
    return next_id, segs_ref, []


def _detailer_pass_units(params: dict[str, Any], derived: dict[str, Any], width: int, height: int) -> list[dict[str, Any]]:
    """Expand validated detailer_passes[] into V1-style ordered runtime units."""
    shared = dict(params)
    passes = params.get("detailer_passes") if isinstance(params.get("detailer_passes"), list) else []
    units: list[dict[str, Any]] = []
    if not passes:
        passes = [{k: params.get(k) for k in ("id", "label", "enabled", "mode", "detector_type", "detector_model", "target_order", "start_index", "count", "min_area", "max_area", "target_mode", "manual_boxes", "reference_lock", "positive_prompt", "negative_prompt")}]
    for pass_index, item in enumerate(passes, start=1):
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        unit = dict(shared)
        unit.update(item)
        unit["pass_index"] = pass_index
        unit["pass_id"] = item.get("id") or f"pass-{pass_index}"
        unit["pass_label"] = item.get("label") or ("Primary pass" if pass_index == 1 else f"Pass {pass_index}")
        unit["target_order"] = item.get("target_order") or params.get("target_order") or "auto"
        unit["order_mode"] = unit["target_order"]
        manual_boxes = _parse_manual_boxes_for_canvas(unit.get("manual_boxes"), width, height) if str(unit.get("target_mode") or "auto_detect") == "manual_boxes" else []
        base_units = []
        if str(unit.get("target_mode") or "auto_detect") == "manual_boxes":
            if manual_boxes:
                for box_idx, box in enumerate(manual_boxes, start=1):
                    box_unit = dict(unit)
                    box_unit["manual_box"] = box
                    box_unit["manual_box_index"] = box_idx
                    base_units.append(box_unit)
            else:
                skip_unit = dict(unit)
                skip_unit["skip_reason"] = "manual boxes mode was enabled but no valid boxes were provided"
                base_units.append(skip_unit)
        else:
            base_units.append(unit)
        for base_unit in base_units:
            pos_parts = parse_sep_targets(base_unit.get("positive_prompt"))
            neg_parts = parse_sep_targets(base_unit.get("negative_prompt"))
            if not pos_parts and str(base_unit.get("positive_prompt") or "").strip():
                pos_parts = [str(base_unit.get("positive_prompt") or "").strip()]
            if not neg_parts and str(base_unit.get("negative_prompt") or "").strip():
                neg_parts = [str(base_unit.get("negative_prompt") or "").strip()]
            target_count = max(len(pos_parts), len(neg_parts), 1)
            if target_count > 1:
                base_start = max(1, int(base_unit.get("start_index") or 1))
                for sep_idx in range(target_count):
                    sep_unit = dict(base_unit)
                    sep_unit["positive_prompt"] = pos_parts[min(sep_idx, len(pos_parts) - 1)] if pos_parts else ""
                    sep_unit["negative_prompt"] = neg_parts[min(sep_idx, len(neg_parts) - 1)] if neg_parts else ""
                    sep_unit["start_index"] = base_start + sep_idx
                    sep_unit["count"] = 1
                    sep_unit["_sep_target_filter"] = True
                    sep_unit["_sep_target_index"] = sep_idx + 1
                    sep_unit["_sep_target_total"] = target_count
                    units.append(sep_unit)
            else:
                units.append(base_unit)
    return units


def _node_inputs(workflow: dict[str, Any], node_id: str | int) -> dict[str, Any]:
    node = workflow.get(str(node_id))
    inputs = node.get("inputs") if isinstance(node, dict) else None
    return inputs if isinstance(inputs, dict) else {}


def _find_first_node(workflow: dict[str, Any], class_types: set[str] | tuple[str, ...]) -> tuple[str | None, dict[str, Any] | None]:
    classes = set(class_types)
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") in classes:
            return str(node_id), node
    return None, None


def _source_image_name_from_route(route: dict[str, Any] | None) -> str:
    route_data = route if isinstance(route, dict) else {}
    actual_params = route_data.get("actual_params") if isinstance(route_data.get("actual_params"), dict) else {}
    route_params = route_data.get("params") if isinstance(route_data.get("params"), dict) else {}
    for container in (actual_params, route_params, route_data):
        for key in ("comfy_source_image_name", "source_image_name"):
            value = str(container.get(key) or "").strip()
            if value:
                return value.replace("\\", "/")
    return ""


def _upstream_load_image_ref(
    workflow: dict[str, Any],
    ref: Any,
    *,
    visited: set[str] | None = None,
) -> list[Any] | None:
    """Resolve a declared image lane back to LoadImage without scanning peers.

    IP Adapter and ControlNet legitimately add their own LoadImage nodes.  Those
    nodes are reference/conditioning assets, not ADetailer pixel sources.  Only
    follow the image/pixels connection owned by the base VAE encoder.
    """

    if not isinstance(ref, (list, tuple)) or len(ref) < 2:
        return None
    node_id = str(ref[0])
    seen = visited if isinstance(visited, set) else set()
    if node_id in seen:
        return None
    seen.add(node_id)
    node = workflow.get(node_id)
    if not isinstance(node, dict):
        return None
    if node.get("class_type") == "LoadImage":
        return [node_id, 0]
    inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
    for input_name in ("image", "images", "pixels"):
        upstream = inputs.get(input_name)
        resolved = _upstream_load_image_ref(workflow, upstream, visited=seen)
        if resolved is not None:
            return resolved
    return None


def _find_source_load_image_ref(
    workflow: dict[str, Any],
    *,
    expected_image_name: str = "",
) -> list[Any] | None:
    """Find only the base source-image lane used by VAE encoding.

    An exact uploaded Comfy source name wins when the route exposes it.  The
    structural fallback is restricted to LoadImage nodes upstream of the base
    VAE encoder.  There is intentionally no "first LoadImage" fallback.
    """

    expected = str(expected_image_name or "").strip().replace("\\", "/")
    if expected:
        for node_id, node in workflow.items():
            if not isinstance(node, dict) or node.get("class_type") != "LoadImage":
                continue
            image_name = str((node.get("inputs") or {}).get("image") or "").strip().replace("\\", "/")
            if image_name == expected:
                return [str(node_id), 0]
    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") not in {"VAEEncode", "VAEEncodeForInpaint"}:
            continue
        pixels = (node.get("inputs") or {}).get("pixels")
        resolved = _upstream_load_image_ref(workflow, pixels)
        if resolved is not None:
            return resolved
    return None


def _is_preview_detailer_output_pass(
    validation: dict[str, Any],
    params: dict[str, Any],
    route: dict[str, Any] | None = None,
) -> bool:
    block = validation.get("block") if isinstance(validation.get("block"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    route_data = route if isinstance(route, dict) else {}
    actual_params = route_data.get("actual_params") if isinstance(route_data.get("actual_params"), dict) else {}
    route_modes = {
        str(value or "").strip().lower()
        for value in (
            route_data.get("workflow_mode"),
            route_data.get("mode"),
            actual_params.get("workflow_mode"),
            actual_params.get("mode"),
        )
        if str(value or "").strip()
    }
    source_route_active = bool(route_modes & {"img2img", "inpaint", "outpaint"})
    preview_marker = bool(
        params.get("detailer_output_pass")
        or metadata.get("detailer_output_pass")
        or metadata.get("source_mode") == "preview_action_selected_output"
        or inputs.get("preview_action_source")
        or metadata.get("preview_action_source")
    )
    return source_route_active and preview_marker


def _find_output_consumers(workflow: dict[str, Any], base_image_ref: list[Any]) -> list[tuple[str, str]]:
    consumers: list[tuple[str, str]] = []
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") not in {"SaveImage", "PreviewImage"}:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if "images" in inputs:
            consumers.append((str(node_id), "images"))
    if consumers:
        return consumers
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for name, value in inputs.items():
            if value == base_image_ref:
                consumers.append((str(node_id), str(name)))
    return consumers


def _rewrite_output_consumers(workflow: dict[str, Any], consumers: list[tuple[str, str]], new_ref: list[Any]) -> None:
    for node_id, input_name in consumers:
        inputs = workflow.get(str(node_id), {}).get("inputs")
        if isinstance(inputs, dict):
            inputs[input_name] = _copy_ref(new_ref)


def _detector_provider(params: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
    detector_type = str(params.get("detector_type") or "bbox").strip().lower()
    model_name = str(params.get("detector_model") or "").strip().replace("\\", "/")
    if detector_type.startswith("onnx"):
        if model_name.lower().startswith("onnx/"):
            model_name = model_name.split("/", 1)[1]
        return "ONNXDetectorProvider", {"model_name": model_name}, model_name
    # Impact Pack expects Ultralytics models under bbox/ or segm/ unless the user already supplied a scoped path.
    if not model_name.lower().startswith(("bbox/", "segm/")):
        model_name = f"{'segm' if detector_type == 'segm' else 'bbox'}/{model_name}"
    return "UltralyticsDetectorProvider", {"model_name": model_name}, model_name


def _provider_model_choices(available_nodes: Any, provider_class: str) -> list[str] | None:
    """Read the live model choices from a Comfy /object_info node schema.

    ``None`` means the caller supplied node names only or an incomplete test
    schema, so execution-value validation is unavailable. An empty list means
    Comfy supplied a real model choice field with no accepted values.
    """
    if not isinstance(available_nodes, dict):
        return None
    info = available_nodes.get("object_info") if isinstance(available_nodes.get("object_info"), dict) else available_nodes
    schema = info.get(provider_class) if isinstance(info, dict) else None
    if not isinstance(schema, dict):
        return None
    input_schema = schema.get("input") if isinstance(schema.get("input"), dict) else {}
    for section_name in ("required", "optional"):
        section = input_schema.get(section_name) if isinstance(input_schema.get(section_name), dict) else {}
        for field_name in ("model_name", "model", "detector_model"):
            raw = section.get(field_name)
            if raw is None:
                continue
            first = raw[0] if isinstance(raw, (list, tuple)) and raw else raw
            if isinstance(first, dict):
                first = first.get("choices") or first.get("values") or first.get("options") or []
            if isinstance(first, (list, tuple, set)):
                return [str(item).strip().replace("\\", "/") for item in first if str(item).strip()]
            return []
    return None


def _resolve_provider_detector_value(params: dict[str, Any], available_nodes: Any) -> dict[str, Any]:
    provider_class, _inputs, expected = _detector_provider(params)
    choices = _provider_model_choices(available_nodes, provider_class)
    if choices is None:
        return {"status": "unchecked", "provider": provider_class, "requested": expected, "resolved": expected, "choice_count": None}
    by_folded = {choice.casefold(): choice for choice in choices}
    exact = by_folded.get(expected.casefold())
    if exact:
        return {"status": "accepted", "provider": provider_class, "requested": expected, "resolved": exact, "choice_count": len(choices)}
    basename = expected.rsplit("/", 1)[-1].casefold()
    basename_matches = [choice for choice in choices if choice.rsplit("/", 1)[-1].casefold() == basename]
    if len(basename_matches) == 1:
        return {"status": "canonicalized", "provider": provider_class, "requested": expected, "resolved": basename_matches[0], "choice_count": len(choices)}
    return {
        "status": "rejected",
        "provider": provider_class,
        "requested": expected,
        "resolved": "",
        "choice_count": len(choices),
        "error_code": "adetailer_detector_not_accepted_by_comfy_provider",
    }


def _add_prompt_nodes(
    graph: dict[str, Any],
    next_id: str,
    *,
    clip_ref: list[Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    positive_text: str,
    negative_text: str,
) -> tuple[str, list[Any], list[Any], list[str]]:
    added: list[str] = []
    if not positive_text.strip() and not negative_text.strip():
        return next_id, positive_ref, negative_ref, added
    positive_id = next_id
    graph[positive_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": positive_text.strip(), "clip": _copy_ref(clip_ref)}}
    next_id = _next_graph_id(graph)
    negative_id = next_id
    graph[negative_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text.strip(), "clip": _copy_ref(clip_ref)}}
    next_id = _next_graph_id(graph)
    added.extend([positive_id, negative_id])
    return next_id, [positive_id, 0], [negative_id, 0], added


def _order_target_for_mode(target_order: str) -> tuple[str, bool] | None:
    mapping = {
        "left_to_right": ("x1", False),
        "right_to_left": ("x1", True),
        "top_to_bottom": ("y1", False),
        "bottom_to_top": ("y1", True),
        "area_desc": ("area(=w*h)", True),
        "largest_first": ("area(=w*h)", True),
        "area_asc": ("area(=w*h)", False),
        "smallest_first": ("area(=w*h)", False),
        "confidence_desc": ("confidence", True),
        "score_desc": ("confidence", True),
        "auto": ("none", True),
    }
    return mapping.get(str(target_order or "auto"))


def _should_use_segs(params: dict[str, Any], derived: dict[str, Any], node_status: dict[str, Any]) -> bool:
    capabilities = node_status.get("capabilities") if isinstance(node_status.get("capabilities"), dict) else {}
    has_segs = bool(capabilities.get("segs_detailer_path"))
    advanced_targeting = (
        bool(params.get("_sep_target_filter"))
        or bool(params.get("manual_box"))
        or str(params.get("target_mode") or "auto_detect") == "manual_boxes"
        or int(params.get("count") if params.get("count") is not None else params.get("top_k") or 1) not in (0, 1)
        or int(params.get("start_index") or 1) != 1
        or int(params.get("min_area") or 0) > 0
        or int(params.get("max_area") or 0) > 0
        or str(params.get("target_order") or "auto") not in {"auto", "area_desc"}
        or bool(params.get("custom_classes"))
    )
    return has_segs and advanced_targeting


_REFERENCE_LOCK_DENOISE_CAPS = {
    "soft_identity": 0.30,
    "strong_identity": 0.20,
    "face_only": 0.25,
}


def _resolve_reference_lock_policy(params: dict[str, Any], reference_context: dict[str, Any] | None) -> dict[str, Any]:
    """Resolve Reference Lock as upstream conditioning policy, never pixel ownership."""
    requested = str(params.get("reference_lock") or "none").strip().lower()
    context = reference_context if isinstance(reference_context, dict) else {}
    ip_adapter = context.get("ip_adapter") if isinstance(context.get("ip_adapter"), dict) else {}
    controlnet = context.get("controlnet") if isinstance(context.get("controlnet"), dict) else {}
    faceid_active = bool(ip_adapter.get("applied") and ip_adapter.get("faceid_active"))
    standard_active = bool(ip_adapter.get("applied") and ip_adapter.get("standard_active"))
    ip_adapter_active = bool(ip_adapter.get("applied") and (faceid_active or standard_active))
    controlnet_active = bool(controlnet.get("applied"))
    effective = requested
    warning_code = ""
    warning = ""

    if requested in {"soft_identity", "strong_identity", "face_only"} and not faceid_active:
        effective = "none"
        warning_code = "adetailer_reference_lock_faceid_missing"
        warning = "Reference Lock requires an applied FaceID IP Adapter unit; the pass will run without the requested lock."
    elif requested == "face_only" and str(params.get("mode") or "face").lower() != "face":
        effective = "none"
        warning_code = "adetailer_reference_lock_face_scope_mismatch"
        warning = "Face only Reference Lock can be used only by a face detailer pass; the pass will run without the requested lock."
    elif requested == "style_only" and not standard_active:
        effective = "none"
        warning_code = "adetailer_reference_lock_ipadapter_missing"
        warning = "Style only Reference Lock requires an applied standard IP Adapter unit; the pass will run without the requested lock."
    elif requested == "controlnet" and not controlnet_active:
        effective = "none"
        warning_code = "adetailer_reference_lock_controlnet_missing"
        warning = "Follow ControlNet requires an applied ControlNet unit; the pass will run without the requested lock."
    elif requested == "ipadapter" and not ip_adapter_active:
        effective = "none"
        warning_code = "adetailer_reference_lock_ipadapter_missing"
        warning = "Legacy IP-Adapter / FaceID lock requires an applied IP Adapter unit; the pass will run without the requested lock."
    elif requested == "both" and not (ip_adapter_active and controlnet_active):
        effective = "none"
        warning_code = "adetailer_reference_lock_dependencies_missing"
        warning = "Legacy both requires applied IP Adapter and ControlNet units; the pass will run without the requested lock."

    requested_denoise = _clamp_float(params.get("denoise"), 0.35, 0.0, 1.0)
    denoise_cap = _REFERENCE_LOCK_DENOISE_CAPS.get(effective)
    effective_denoise = min(requested_denoise, denoise_cap) if denoise_cap is not None else requested_denoise
    return {
        "requested": requested,
        "effective": effective,
        "applied": effective != "none",
        "scope": "generated_face_region" if effective == "face_only" else "upstream_conditioning",
        "pixel_source_policy": "generated_or_explicit_finish_output_never_reference_asset",
        "faceid_active": faceid_active,
        "standard_ip_adapter_active": standard_active,
        "controlnet_active": controlnet_active,
        "requested_denoise": requested_denoise,
        "effective_denoise": effective_denoise,
        "denoise_cap": denoise_cap,
        "warning_code": warning_code,
        "warning": warning,
    }

def _add_sam_loader(graph: dict[str, Any], next_id: str, params: dict[str, Any], node_status: dict[str, Any]) -> tuple[str, list[Any] | None, list[str]]:
    sam_model = str(params.get("sam_model") or "").strip()
    capabilities = node_status.get("capabilities") if isinstance(node_status.get("capabilities"), dict) else {}
    if not sam_model or not capabilities.get("sam_loader"):
        return next_id, None, []
    sam_id = next_id
    graph[sam_id] = {"class_type": "SAMLoader", "inputs": {"model_name": sam_model, "device_mode": "AUTO"}}
    return _next_graph_id(graph), [sam_id, 0], [sam_id]


def _add_face_detailer_pass(
    graph: dict[str, Any],
    next_id: str,
    *,
    current_image_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    vae_ref: list[Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    params: dict[str, Any],
    seed: int,
    sampler_name: str,
    scheduler: str,
    node_status: dict[str, Any],
) -> tuple[str, list[Any], list[str], str]:
    detector_class, detector_inputs, detector_model_name = _detector_provider(params)
    detector_id = next_id
    graph[detector_id] = {"class_type": detector_class, "inputs": detector_inputs}
    next_id = _next_graph_id(graph)
    sam_nodes: list[str]
    next_id, sam_ref, sam_nodes = _add_sam_loader(graph, next_id, params, node_status)
    detailer_inputs: dict[str, Any] = {
        "image": _copy_ref(current_image_ref),
        "model": _copy_ref(model_ref),
        "clip": _copy_ref(clip_ref),
        "vae": _copy_ref(vae_ref),
        "guide_size": float(params.get("guide_size") or 768),
        "guide_size_for": True,
        "max_size": float(params.get("max_size") or 1024),
        "seed": max(1, int(seed or 1)),
        "steps": int(params.get("steps") or 20),
        "cfg": float(params.get("cfg") if params.get("cfg") is not None else CFG_SAFETY_CAP),
        "sampler_name": sampler_name,
        "scheduler": scheduler,
        "positive": _copy_ref(positive_ref),
        "negative": _copy_ref(negative_ref),
        "denoise": float(params.get("denoise") if params.get("denoise") is not None else 0.25),
        "feather": int(params.get("noise_mask_feather") if params.get("noise_mask_feather") is not None else params.get("mask_blur") or 4),
        "noise_mask": bool(params.get("noise_mask", True)),
        "force_inpaint": bool(params.get("force_inpaint", True)),
        "bbox_threshold": float(params.get("confidence") or 0.30),
        "bbox_dilation": int(params.get("bbox_grow") or 16),
        "bbox_crop_factor": 2.0,
        "sam_detection_hint": "center-1",
        "sam_dilation": int(params.get("bbox_grow") or 16),
        "sam_threshold": 0.88,
        "sam_bbox_expansion": int(params.get("bbox_grow") or 16),
        "sam_mask_hint_threshold": 0.70,
        "sam_mask_hint_use_negative": "False",
        "drop_size": 10,
        "bbox_detector": [detector_id, 0],
        "wildcard": "",
        "cycle": 1,
        "inpaint_model": False,
        "noise_mask_feather": int(params.get("noise_mask_feather") if params.get("noise_mask_feather") is not None else params.get("mask_blur") or 4),
    }
    if detector_class == "UltralyticsDetectorProvider" and params.get("detector_type") == "segm":
        detailer_inputs["segm_detector_opt"] = [detector_id, 1]
    if sam_ref is not None:
        detailer_inputs["sam_model_opt"] = _copy_ref(sam_ref)
    detailer_id = next_id
    graph[detailer_id] = {"class_type": "FaceDetailer", "inputs": detailer_inputs}
    next_id = _next_graph_id(graph)
    return next_id, [detailer_id, 0], [detector_id, *sam_nodes, detailer_id], detector_model_name


def _add_segs_detailer_pass(
    graph: dict[str, Any],
    next_id: str,
    *,
    current_image_ref: list[Any],
    model_ref: list[Any],
    clip_ref: list[Any],
    vae_ref: list[Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    params: dict[str, Any],
    seed: int,
    sampler_name: str,
    scheduler: str,
    derived: dict[str, Any],
    node_status: dict[str, Any],
    width: int,
    height: int,
) -> tuple[str, list[Any], list[str], str, list[str]]:
    notes: list[str] = []
    detector_model_name = str(params.get("detector_model") or "").strip()
    manual_box = params.get("manual_box") if isinstance(params.get("manual_box"), dict) else None

    if manual_box is not None:
        next_id, segs_ref, manual_notes = _build_manual_detailer_segs(
            graph,
            next_id,
            width=width,
            height=height,
            manual_box=manual_box,
            params=params,
            node_status=node_status,
        )
        notes.extend(manual_notes)
        detector_model_name = "manual boxes"
        if segs_ref is None:
            return next_id, current_image_ref, [], detector_model_name, notes
    else:
        detector_class, detector_inputs, detector_model_name = _detector_provider(params)
        detector_id = next_id
        graph[detector_id] = {"class_type": detector_class, "inputs": detector_inputs}
        next_id = _next_graph_id(graph)
        detector_type = str(params.get("detector_type") or "bbox")
        segs_id = next_id
        labels = str(params.get("custom_classes") or "all") or "all"
        if detector_type == "segm" and detector_class == "UltralyticsDetectorProvider":
            graph[segs_id] = {"class_type": "SegmDetectorSEGS", "inputs": {"segm_detector": [detector_id, 1], "image": _copy_ref(current_image_ref), "threshold": float(params.get("confidence") or 0.30), "dilation": int(params.get("bbox_grow") or 16), "crop_factor": 2.0, "drop_size": 10, "labels": labels}}
        else:
            graph[segs_id] = {"class_type": "BboxDetectorSEGS", "inputs": {"bbox_detector": [detector_id, 0], "image": _copy_ref(current_image_ref), "threshold": float(params.get("confidence") or 0.30), "dilation": int(params.get("bbox_grow") or 16), "crop_factor": 2.0, "drop_size": 10, "labels": labels}}
        segs_ref = [segs_id, 0]
        next_id = _next_graph_id(graph)

    node_ids: list[str] = []
    if manual_box is None:
        node_ids.append(str(int(next_id) - 2 if str(next_id).isdigit() else ""))
    # Ordered filtering covers V1 order/start/count/[SEP] target selection.
    order = _order_target_for_mode(str(params.get("target_order") or "auto"))
    start_index = max(1, int(params.get("start_index") or 1))
    count = int(params.get("count") if params.get("count") is not None else params.get("top_k") or 1)
    if bool(params.get("_sep_target_filter")) or order is not None or start_index != 1 or count not in (0, 1):
        target, descending = order or ("none", True)
        filter_id = next_id
        graph[filter_id] = {"class_type": "ImpactSEGSOrderedFilter", "inputs": {"segs": _copy_ref(segs_ref), "target": target, "order": bool(descending), "take_start": max(0, start_index - 1), "take_count": count if count > 0 else 9999}}
        segs_ref = [filter_id, 0]
        node_ids.append(filter_id)
        next_id = _next_graph_id(graph)

    min_area = int(params.get("min_area") or 0)
    max_area = int(params.get("max_area") or 0)
    if min_area > 0 or max_area > 0:
        available = set(node_status.get("available_nodes") or [])
        if "ImpactSEGSRangeFilter" in available:
            range_id = next_id
            graph[range_id] = {"class_type": "ImpactSEGSRangeFilter", "inputs": {"segs": _copy_ref(segs_ref), "target": "area(=w*h)", "mode": True, "min_value": max(0, min_area), "max_value": max_area if max_area > 0 else 67108864}}
            segs_ref = [range_id, 0]
            node_ids.append(range_id)
            next_id = _next_graph_id(graph)
        else:
            notes.append("area filtering requested but ImpactSEGSRangeFilter is unavailable")

    pipe_id = next_id
    graph[pipe_id] = {"class_type": "ToBasicPipe", "inputs": {"model": _copy_ref(model_ref), "clip": _copy_ref(clip_ref), "vae": _copy_ref(vae_ref), "positive": _copy_ref(positive_ref), "negative": _copy_ref(negative_ref)}}
    next_id = _next_graph_id(graph)
    detailer_id = next_id
    graph[detailer_id] = {"class_type": "SEGSDetailer", "inputs": {"image": _copy_ref(current_image_ref), "segs": _copy_ref(segs_ref), "guide_size": float(params.get("guide_size") or 768), "guide_size_for": True, "max_size": float(max(int(params.get("max_size") or 1024), int(params.get("guide_size") or 768))), "seed": max(1, int(seed or 1)), "steps": int(params.get("steps") or 20), "cfg": float(params.get("cfg") if params.get("cfg") is not None else CFG_SAFETY_CAP), "sampler_name": sampler_name, "scheduler": scheduler, "denoise": float(params.get("denoise") if params.get("denoise") is not None else 0.25), "noise_mask": bool(params.get("noise_mask", True)), "force_inpaint": bool(params.get("force_inpaint", True)), "basic_pipe": [pipe_id, 0], "refiner_ratio": 0.2, "batch_size": 1, "cycle": 1, "inpaint_model": False, "noise_mask_feather": int(params.get("noise_mask_feather") if params.get("noise_mask_feather") is not None else params.get("mask_blur") or 4)}}
    next_id = _next_graph_id(graph)
    paste_id = next_id
    graph[paste_id] = {"class_type": "SEGSPaste", "inputs": {"image": _copy_ref(current_image_ref), "segs": [detailer_id, 0], "feather": int(params.get("mask_blur") or 4), "alpha": 255}}
    next_id = _next_graph_id(graph)
    node_ids.extend([pipe_id, detailer_id, paste_id])
    return next_id, [paste_id, 0], node_ids, detector_model_name, notes


def build_workflow_patch_summary(
    *,
    route: dict[str, Any] | None,
    validation: dict[str, Any],
    node_ids: list[str] | None = None,
    previous_image_ref: list[Any] | None = None,
    patched_image_ref: list[Any] | None = None,
    output_consumers: list[tuple[str, str]] | None = None,
    patch_path: str = "none",
    detector_model: str = "",
    reason: str = "",
    applied: bool = False,
    pass_summaries: list[dict[str, Any]] | None = None,
    skipped_passes: list[dict[str, Any]] | None = None,
    asset_bridge: dict[str, Any] | None = None,
    route_contract: dict[str, Any] | None = None,
    graph_invariants: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pass_summaries = list(pass_summaries or [])
    skipped_passes = list(skipped_passes or [])
    paths = sorted({str(item.get("patch_path") or "") for item in pass_summaries if item.get("patch_path")})
    node_class = "mixed" if len(paths) > 1 else ("FaceDetailer" if patch_path == "face_detailer" else ("SEGSDetailer" if patch_path == "segs_detailer" else ""))
    diagnostics = refresh_prequeue_diagnostics(
        validation,
        runtime={
            "phase": "workflow_patch",
            "graph_mutation_started": bool(applied or node_ids),
            "graph_mutated": bool(applied),
        },
    )
    result = {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "applied": bool(applied),
        "mutated": bool(applied),
        "runtime_ready": bool(validation.get("runtime_ready")),
        "patch_type": "image_finish_detailer",
        "patch_path": patch_path,
        "patch_paths": paths or ([patch_path] if patch_path != "none" else []),
        "node": node_class,
        "node_class": node_class,
        "node_ids": list(node_ids or []),
        "previous_image_ref": deepcopy(previous_image_ref or []),
        "patched_image_ref": deepcopy(patched_image_ref or previous_image_ref or []),
        "output_consumers": [{"node_id": node_id, "input": input_name} for node_id, input_name in (output_consumers or [])],
        "detector_model": detector_model,
        "params_used": deepcopy(validation.get("params") or {}),
        "route": deepcopy(route or {}),
        "node_status": deepcopy(validation.get("node_status") or {}),
        "reason": reason,
        "multi_pass_workflow_ready": True,
        "detailer_pass_count": int((validation.get("derived") or {}).get("detailer_pass_count") or 0),
        "enabled_detailer_pass_count": int((validation.get("derived") or {}).get("enabled_detailer_pass_count") or 0),
        "runtime_unit_count": len(pass_summaries),
        "pass_summaries": deepcopy(pass_summaries),
        "reference_lock_policies": [deepcopy(item.get("reference_lock")) for item in pass_summaries if isinstance(item.get("reference_lock"), dict)],
        "skipped_passes": deepcopy(skipped_passes),
        "asset_bridge": deepcopy(asset_bridge or {}),
        "detailer_model_source": public_model_source_metadata(validation.get("model_source") if isinstance(validation.get("model_source"), dict) else {}),
        "detailer_lora_branch": public_lora_branch_metadata(validation.get("lora_branch") if isinstance(validation.get("lora_branch"), dict) else {}),
        "identity_policy": public_identity_policy_metadata(validation.get("identity_policy") if isinstance(validation.get("identity_policy"), dict) else {}),
        "family_preset": public_family_preset_metadata(validation.get("family_preset") if isinstance(validation.get("family_preset"), dict) else {}),
        "route_contract": contract_metadata(route_contract),
        "prequeue_diagnostics": deepcopy(diagnostics),
        "graph_invariants": deepcopy(graph_invariants or {}),
    }
    return result


def apply_adetailer_patch(
    workflow: dict[str, Any],
    *,
    payload: Any,
    route: dict[str, Any] | None = None,
    available_nodes: Any = None,
    model_ref: list[Any] | tuple[Any, ...] | None = None,
    clip_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int = "5",
    seed: int | str | None = None,
    sampler_name: str | None = None,
    scheduler: str | None = None,
    reference_context: dict[str, Any] | None = None,
    route_contract: dict[str, Any] | None = None,
    base_route_contract: dict[str, Any] | None = None,
    lora_patch_profile: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    graph = deepcopy(workflow or {})
    incoming_graph = deepcopy(graph)
    validation = validate_and_normalize_payload(
        payload,
        route=route,
        available_nodes=available_nodes,
        lora_patch_profile=lora_patch_profile,
    )
    if not validation.get("workflow_patch_allowed"):
        model_source_validation = validation.get("model_source") if isinstance(validation.get("model_source"), dict) else {}
        source_errors = model_source_validation.get("errors") if isinstance(model_source_validation.get("errors"), list) else []
        source_reason = str((source_errors[0] if source_errors and isinstance(source_errors[0], dict) else {}).get("message") or "").strip()
        lora_validation = validation.get("lora_branch") if isinstance(validation.get("lora_branch"), dict) else {}
        lora_errors = lora_validation.get("errors") if isinstance(lora_validation.get("errors"), list) else []
        lora_reason = str((lora_errors[0] if lora_errors and isinstance(lora_errors[0], dict) else {}).get("message") or "").strip()
        identity_validation = validation.get("identity_policy") if isinstance(validation.get("identity_policy"), dict) else {}
        identity_errors = identity_validation.get("errors") if isinstance(identity_validation.get("errors"), list) else []
        identity_reason = str((identity_errors[0] if identity_errors and isinstance(identity_errors[0], dict) else {}).get("message") or "").strip()
        preset_validation = validation.get("family_preset") if isinstance(validation.get("family_preset"), dict) else {}
        preset_errors = preset_validation.get("errors") if isinstance(preset_validation.get("errors"), list) else []
        preset_reason = str((preset_errors[0] if preset_errors and isinstance(preset_errors[0], dict) else {}).get("message") or "").strip()
        reason = identity_reason or preset_reason or lora_reason or source_reason or validation.get("support", {}).get("reason") or "ADetailer is gated, disabled, or unsupported."
        patch = build_workflow_patch_summary(route=route, validation=validation, reason=reason, applied=False)
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    requested_params = deepcopy(validation.get("params") or {})
    derived = validation.get("derived") or {}

    contract_result = normalize_adetailer_route_contract(
        route_contract,
        route=route,
        workflow=graph,
        require_validated=True,
    )
    if not contract_result.get("valid"):
        errors = list(contract_result.get("errors") or [])
        validation.setdefault("validation", []).append({
            "extension_id": EXTENSION_ID,
            "level": "error",
            "code": "adetailer_route_contract_invalid",
            "message": "ADetailer requires an explicit compiler-owned image/model/CLIP/VAE/conditioning contract. Checkpoint-shaped node fallbacks are not permitted.",
            "ok": False,
            "blocked": True,
            "errors": errors,
            "contract_schema_id": contract_result.get("schema_id"),
        })
        validation["workflow_patch_allowed"] = False
        validation["active_patch_data_allowed"] = False
        reason = "ADetailer was blocked before graph mutation because its compiler-owned route contract is missing or invalid."
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            reason=reason,
            applied=False,
            route_contract=contract_result,
        )
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    contract = contract_result["contract"]
    contract_refs = contract["refs"]
    identity_plan = validation.get("identity_policy") if isinstance(validation.get("identity_policy"), dict) else {}
    sampling_gate = validate_sampling_reapply_contract(
        graph,
        plan=identity_plan,
        route_contract=contract,
    )
    if not sampling_gate.get("ready"):
        for issue in sampling_gate.get("errors", []):
            validation.setdefault("validation", []).append({
                "extension_id": EXTENSION_ID,
                "level": "error",
                "code": str(issue.get("code") or "adetailer_identity_sampling_reapply_invalid"),
                "field": issue.get("field"),
                "message": str(issue.get("message") or "Qwen Edit model-sampling lineage cannot be safely reapplied after the identity LoRA."),
                "ok": False,
                "blocked": True,
                **{key: value for key, value in issue.items() if key not in {"code", "field", "message"}},
            })
        validation["workflow_patch_allowed"] = False
        validation["active_patch_data_allowed"] = False
        identity_plan = {**identity_plan, "ready": False, "sampling_reapply": sampling_gate}
        validation["identity_policy"] = identity_plan
        reason = "ADetailer Qwen Edit identity protection was blocked because the compiler-owned sampling lineage could not be reapplied safely."
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            reason=reason,
            applied=False,
            route_contract=contract_result,
        )
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}
    identity_plan = {**identity_plan, "sampling_reapply": sampling_gate}
    validation["identity_policy"] = identity_plan
    lora_plan = validation.get("lora_branch") if isinstance(validation.get("lora_branch"), dict) else {}
    needs_compiler_base = (
        str((validation.get("model_source") or {}).get("source") or "") == GENERATION_MODEL_SOURCE
        and str(lora_plan.get("inheritance_effective") or INHERIT_ALL) != INHERIT_ALL
    )
    base_contract_result = normalize_adetailer_route_contract(
        base_route_contract,
        route=route,
        workflow=None,
        require_validated=True,
    ) if needs_compiler_base else {"valid": True, "contract": contract}
    if needs_compiler_base and not base_contract_result.get("valid"):
        errors = list(base_contract_result.get("errors") or [])
        validation.setdefault("validation", []).append({
            "extension_id": EXTENSION_ID,
            "level": "error",
            "code": "adetailer_lora_base_contract_invalid",
            "message": "The isolated ADetailer LoRA branch requires the original compiler-owned model/CLIP contract when generation LoRAs are not inherited in full.",
            "ok": False,
            "blocked": True,
            "errors": errors,
        })
        validation["workflow_patch_allowed"] = False
        validation["active_patch_data_allowed"] = False
        reason = "ADetailer LoRA isolation was blocked because the original compiler contract is unavailable."
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            reason=reason,
            applied=False,
            route_contract=contract_result,
        )
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}
    base_contract_refs = (base_contract_result.get("contract") or {}).get("refs") if isinstance(base_contract_result.get("contract"), dict) else {}
    contract_sampler = contract["sampler"]
    contract_sampler_inputs = contract_sampler["inputs"]
    contract_sampler_node_id = str(contract_sampler["node_id"])
    family_preset_runtime = materialize_family_preset(
        validation.get("family_preset") if isinstance(validation.get("family_preset"), dict) else {},
        requested_params,
        workflow=graph,
        contract=contract,
        sampler_name_override=sampler_name,
        scheduler_override=scheduler,
        available_nodes=available_nodes,
    )
    validation["family_preset"] = {
        **(validation.get("family_preset") if isinstance(validation.get("family_preset"), dict) else {}),
        **family_preset_runtime,
    }
    if not family_preset_runtime.get("ready"):
        for issue in family_preset_runtime.get("errors", []):
            validation.setdefault("validation", []).append({
                "extension_id": EXTENSION_ID,
                "level": "error",
                "code": str(issue.get("code") or "adetailer_family_preset_runtime_invalid"),
                "field": issue.get("field"),
                "message": str(issue.get("message") or "ADetailer family preset could not be materialized."),
                "ok": False,
                "blocked": True,
            })
        validation["workflow_patch_allowed"] = False
        validation["active_patch_data_allowed"] = False
        reason = "ADetailer was blocked before graph mutation because its family-owned sampling preset is invalid."
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            reason=reason,
            applied=False,
            route_contract=contract_result,
        )
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}
    for issue in family_preset_runtime.get("warnings", []):
        code = str(issue.get("code") or "adetailer_family_preset_runtime_warning")
        if not any(str(item.get("code") or "") == code for item in validation.setdefault("validation", []) if isinstance(item, dict)):
            validation["validation"].append({
                "extension_id": EXTENSION_ID,
                "level": "warning",
                "code": code,
                "field": issue.get("field"),
                "message": str(issue.get("message") or "ADetailer family-preset warning."),
                "ok": True,
                "blocked": False,
            })
    params = deepcopy(family_preset_runtime.get("effective_params") or requested_params)
    validation["requested_params"] = requested_params
    validation["params"] = params
    width, height = _find_canvas_size(graph, params)
    runtime_units = _detailer_pass_units(params, derived, width, height)
    runtime_units = [unit for unit in runtime_units if unit.get("enabled", True)]
    if not runtime_units:
        append_diagnostic_issue(
            validation, code="adetailer_runtime_passes_empty", stage="pass_payload",
            message="ADetailer is enabled but no runnable detailer pass remained after preset materialization.",
            remediation="Enable at least one valid pass or turn ADetailer off before queueing.",
        )
        reason = "ADetailer is enabled but no enabled detailer pass/runtime unit remained after family-preset materialization."
        patch = build_workflow_patch_summary(route=route, validation=validation, reason=reason, applied=False, route_contract=contract_result)
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}
    generated_image_ref = _copy_ref(contract_refs["image"])
    initial_image_ref = _copy_ref(generated_image_ref)

    preview_output_pass = _is_preview_detailer_output_pass(validation, params, route)
    source_only_ref = _find_source_load_image_ref(
        graph,
        expected_image_name=_source_image_name_from_route(route),
    ) if preview_output_pass else None
    if preview_output_pass and source_only_ref is None:
        validation.setdefault("validation", []).append({
            "extension_id": EXTENSION_ID,
            "level": "error",
            "code": "adetailer_explicit_source_missing",
            "message": "ADetailer post-output execution requires the declared Img2Img source lane; no owned source LoadImage was found.",
            "ok": False,
            "blocked": True,
        })
        validation["workflow_patch_allowed"] = False
        validation["active_patch_data_allowed"] = False
        reason = "ADetailer post-output execution was blocked because its explicit source-image lane is missing."
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            previous_image_ref=generated_image_ref,
            patched_image_ref=generated_image_ref,
            output_consumers=[],
            reason=reason,
            applied=False,
            route_contract=contract_result,
        )
        patch["source_ownership"] = {
            "kind": "missing_explicit_post_output_source",
            "policy": "declared_source_encoder_only_no_arbitrary_loadimage_fallback",
        }
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}
    current_image_ref = source_only_ref or generated_image_ref
    initial_image_ref = _copy_ref(current_image_ref)
    output_consumers = _find_output_consumers(graph, current_image_ref)
    if not output_consumers:
        append_diagnostic_issue(
            validation, code="adetailer_output_consumer_missing", stage="output_ownership",
            message="ADetailer could not find an explicit SaveImage/PreviewImage output consumer to own the final repaired image.",
            remediation="Use a compiler graph with an explicit route-owned SaveImage/PreviewImage consumer.",
        )
        reason = "ADetailer could not find a SaveImage/PreviewImage consumer; the explicit request was blocked before detector staging."
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            previous_image_ref=current_image_ref,
            patched_image_ref=current_image_ref,
            output_consumers=[],
            reason=reason,
            applied=False,
            route_contract=contract_result,
        )
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    detector_execution: list[dict[str, Any]] = []
    rejected_detectors: list[dict[str, Any]] = []
    for unit in runtime_units:
        if unit.get("manual_box") is not None or not str(unit.get("detector_model") or "").strip():
            continue
        resolution = _resolve_provider_detector_value(unit, available_nodes)
        detector_execution.append(resolution)
        if resolution.get("status") == "rejected":
            rejected_detectors.append(resolution)
            continue
        resolved_value = str(resolution.get("resolved") or "").strip()
        if resolved_value:
            unit["detector_model"] = resolved_value

    if rejected_detectors:
        safe_models = [str(item.get("requested") or "selected detector").rsplit("/", 1)[-1] for item in rejected_detectors]
        append_diagnostic_issue(
            validation, code="adetailer_detector_not_accepted_by_comfy_provider", stage="detector_provider",
            field="detector_model",
            message="The selected detector is not in the active Comfy detector provider's accepted model list.",
            remediation="Install or stage the detector, restart ComfyUI, and refresh ADetailer models.",
            models=safe_models[:4],
            provider_choice_counts=[int(item.get("choice_count") or 0) for item in rejected_detectors[:4]],
        )
        reason = "ADetailer blocked the workflow before staging because Comfy does not accept the selected detector value."
        patch = build_workflow_patch_summary(
            route=route, validation=validation, previous_image_ref=current_image_ref,
            patched_image_ref=current_image_ref, output_consumers=output_consumers,
            reason=reason, applied=False, route_contract=contract_result,
        )
        patch["detector_execution"] = deepcopy(detector_execution)
        return {"workflow": incoming_graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    asset_bridge = prepare_detailer_assets_for_execution(params, route=route)
    if asset_bridge.get("blocked_count"):
        blocked_models = [
            str(item.get("model") or "selected detector")
            for item in asset_bridge.get("records", [])
            if isinstance(item, dict) and item.get("status") == "blocked"
        ]
        # Stable historical diagnostic token: {"code": "adetailer_execution_bridge_failed"}
        append_diagnostic_issue(
            validation, code="adetailer_execution_bridge_failed", stage="detector_assets",
            message="ADetailer could not safely prepare the selected detector for Comfy execution.",
            bridge_status=asset_bridge.get("status"), models=blocked_models[:4],
        )
        reason = "ADetailer execution bridge blocked the workflow before Comfy queue."
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            previous_image_ref=current_image_ref,
            patched_image_ref=current_image_ref,
            output_consumers=output_consumers,
            reason=reason,
            applied=False,
            asset_bridge=asset_bridge,
            route_contract=contract_result,
        )
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    sampler_inputs = _node_inputs(graph, contract_sampler_node_id)
    effective_seed = int(seed or sampler_inputs.get(contract_sampler_inputs["seed"]) or 1)
    effective_sampler = str(params.get("sampler_name") or sampler_name or sampler_inputs.get(contract_sampler_inputs["sampler_name"]) or "euler")
    effective_scheduler = str(params.get("scheduler") or scheduler or sampler_inputs.get(contract_sampler_inputs["scheduler"]) or "normal")

    # Keep a clean rollback point. A dedicated checkpoint/prompt branch is
    # provisional until at least one runtime pass successfully produces a new
    # image. A skipped-only run must return the incoming graph unchanged.
    graph_before_detailer_nodes = deepcopy(graph)
    model_source_plan_before_runtime = deepcopy(validation.get("model_source") if isinstance(validation.get("model_source"), dict) else {})
    next_id = _next_graph_id(graph)
    model_source_result = apply_detailer_model_source_plan(
        graph,
        next_id,
        plan=validation.get("model_source") if isinstance(validation.get("model_source"), dict) else {},
        contract_refs=contract_refs,
    )
    graph = model_source_result["workflow"]
    next_id = str(model_source_result["next_id"])
    vae_ref = _copy_ref(model_source_result.get("vae_ref"))
    current_model_ref = _copy_ref(model_source_result.get("model_ref"))
    current_clip_ref = _copy_ref(model_source_result.get("clip_ref"))
    positive_ref = _copy_ref(model_source_result.get("positive_ref"))
    negative_ref = _copy_ref(model_source_result.get("negative_ref"))
    model_source_runtime = model_source_result.get("model_source") if isinstance(model_source_result.get("model_source"), dict) else {}
    validation["model_source"] = {
        **(validation.get("model_source") if isinstance(validation.get("model_source"), dict) else {}),
        **model_source_runtime,
    }
    lora_branch_plan_before_runtime = deepcopy(validation.get("lora_branch") if isinstance(validation.get("lora_branch"), dict) else {})
    identity_policy_plan_before_runtime = deepcopy(validation.get("identity_policy") if isinstance(validation.get("identity_policy"), dict) else {})
    lora_branch_result = apply_detailer_lora_branch(
        graph,
        next_id,
        plan=validation.get("lora_branch") if isinstance(validation.get("lora_branch"), dict) else {},
        model_source_refs={
            "model": current_model_ref,
            "clip": current_clip_ref,
            "vae": vae_ref,
            "positive": positive_ref,
            "negative": negative_ref,
        },
        base_contract_refs=base_contract_refs,
        route_contract=contract,
        identity_policy=identity_plan,
    )
    graph = lora_branch_result["workflow"]
    next_id = str(lora_branch_result["next_id"])
    current_model_ref = _copy_ref(lora_branch_result.get("model_ref"))
    current_clip_ref = _copy_ref(lora_branch_result.get("clip_ref"))
    vae_ref = _copy_ref(lora_branch_result.get("vae_ref"), vae_ref)
    positive_ref = _copy_ref(lora_branch_result.get("positive_ref"), positive_ref)
    negative_ref = _copy_ref(lora_branch_result.get("negative_ref"), negative_ref)
    lora_branch_runtime = lora_branch_result.get("lora_branch") if isinstance(lora_branch_result.get("lora_branch"), dict) else {}
    validation["lora_branch"] = {
        **(validation.get("lora_branch") if isinstance(validation.get("lora_branch"), dict) else {}),
        **lora_branch_runtime,
    }
    validation["identity_policy"] = {
        **(validation.get("identity_policy") if isinstance(validation.get("identity_policy"), dict) else {}),
        "sampling_reapply": deepcopy(lora_branch_result.get("sampling_reapply") or sampling_gate),
    }
    node_ids: list[str] = list(model_source_result.get("node_ids") or []) + list(lora_branch_result.get("node_ids") or [])
    pass_summaries: list[dict[str, Any]] = []
    skipped_passes: list[dict[str, Any]] = []
    detector_models: list[str] = []
    patch_paths: list[str] = []
    notes: list[str] = []

    for unit_index, unit in enumerate(runtime_units, start=1):
        pass_label = str(unit.get("pass_label") or f"Pass {unit.get('pass_index') or unit_index}")
        runtime_label = pass_label
        if unit.get("manual_box_index"):
            runtime_label += f" · box {unit.get('manual_box_index')}"
        if unit.get("_sep_target_filter"):
            runtime_label += f" · target {unit.get('_sep_target_index')}/{unit.get('_sep_target_total')}"

        if unit.get("skip_reason"):
            skipped_passes.append({"label": runtime_label, "reason": unit.get("skip_reason"), "pass_id": unit.get("pass_id")})
            continue
        if unit.get("manual_box") is None and not str(unit.get("detector_model") or "").strip():
            skipped_passes.append({"label": runtime_label, "reason": "no detector model selected", "pass_id": unit.get("pass_id")})
            continue

        lock_policy = _resolve_reference_lock_policy(unit, reference_context)
        effective_unit = deepcopy(unit)
        effective_unit["denoise"] = lock_policy["effective_denoise"]
        if lock_policy.get("warning"):
            validation.setdefault("validation", []).append({
                "extension_id": EXTENSION_ID,
                "level": "warning",
                "code": lock_policy.get("warning_code"),
                "field": "reference_lock",
                "message": lock_policy.get("warning"),
                "ok": True,
                "blocked": False,
                "pass_id": unit.get("pass_id"),
            })

        pos_text = str(unit.get("positive_prompt") or "").strip()
        neg_text = str(unit.get("negative_prompt") or "").strip()
        prompt_lineage = {"positive_source": "route_conditioning", "negative_source": "route_conditioning"}
        if str(model_source_runtime.get("source") or "") == DEDICATED_CHECKPOINT_SOURCE:
            if bool(lora_branch_result.get("requires_prompt_reencode")):
                pos_text, neg_text, prompt_lineage = prompt_text_for_lora_runtime_unit(lora_branch_runtime, params, unit)
            else:
                pos_text, neg_text, prompt_lineage = prompt_text_for_runtime_unit(model_source_runtime, unit)
            prompt_result = add_dedicated_prompt_nodes(
                graph,
                next_id,
                clip_ref=current_clip_ref,
                positive_text=pos_text,
                negative_text=neg_text,
            )
            graph = prompt_result["workflow"]
            next_id = str(prompt_result["next_id"])
            pass_positive_ref = _copy_ref(prompt_result.get("positive_ref"))
            pass_negative_ref = _copy_ref(prompt_result.get("negative_ref"))
            prompt_nodes = list(prompt_result.get("node_ids") or [])
        elif bool(lora_branch_result.get("requires_prompt_reencode")):
            pos_text, neg_text, prompt_lineage = prompt_text_for_lora_runtime_unit(lora_branch_runtime, params, unit)
            prompt_result = add_dedicated_prompt_nodes(
                graph,
                next_id,
                clip_ref=current_clip_ref,
                positive_text=pos_text,
                negative_text=neg_text,
            )
            graph = prompt_result["workflow"]
            next_id = str(prompt_result["next_id"])
            pass_positive_ref = _copy_ref(prompt_result.get("positive_ref"))
            pass_negative_ref = _copy_ref(prompt_result.get("negative_ref"))
            prompt_nodes = list(prompt_result.get("node_ids") or [])
        else:
            next_id, pass_positive_ref, pass_negative_ref, prompt_nodes = _add_prompt_nodes(
                graph,
                next_id,
                clip_ref=current_clip_ref,
                positive_ref=positive_ref,
                negative_ref=negative_ref,
                positive_text=pos_text,
                negative_text=neg_text,
            )
        node_ids.extend(prompt_nodes)

        use_segs = _should_use_segs(effective_unit, derived, validation.get("node_status") or {})
        patch_path = "segs_detailer" if use_segs else "face_detailer"
        if unit.get("manual_box") is not None and not use_segs:
            skipped_passes.append({"label": runtime_label, "reason": "manual boxes require SEGSDetailer/MaskToSEGS routing", "pass_id": unit.get("pass_id")})
            continue

        previous_ref = _copy_ref(current_image_ref)
        if patch_path == "segs_detailer":
            next_id, current_image_ref, added, detector_model, pass_notes = _add_segs_detailer_pass(
                graph,
                next_id,
                current_image_ref=current_image_ref,
                model_ref=current_model_ref,
                clip_ref=current_clip_ref,
                vae_ref=vae_ref,
                positive_ref=pass_positive_ref,
                negative_ref=pass_negative_ref,
                params=effective_unit,
                seed=effective_seed + unit_index - 1,
                sampler_name=effective_sampler,
                scheduler=effective_scheduler,
                derived=derived,
                node_status=validation.get("node_status") or {},
                width=width,
                height=height,
            )
            if pass_notes:
                notes.extend(f"{runtime_label}: {note}" for note in pass_notes)
            if current_image_ref == previous_ref:
                skipped_passes.append({"label": runtime_label, "reason": "; ".join(pass_notes) or "SEGS pass produced no new image", "pass_id": unit.get("pass_id")})
                continue
        else:
            next_id, current_image_ref, added, detector_model = _add_face_detailer_pass(
                graph,
                next_id,
                current_image_ref=current_image_ref,
                model_ref=current_model_ref,
                clip_ref=current_clip_ref,
                vae_ref=vae_ref,
                positive_ref=pass_positive_ref,
                negative_ref=pass_negative_ref,
                params=effective_unit,
                seed=effective_seed + unit_index - 1,
                sampler_name=effective_sampler,
                scheduler=effective_scheduler,
                node_status=validation.get("node_status") or {},
            )
        node_ids.extend(added)
        detector_models.append(detector_model)
        patch_paths.append(patch_path)
        pass_summaries.append({
            "label": runtime_label,
            "pass_id": unit.get("pass_id"),
            "pass_index": unit.get("pass_index"),
            "patch_path": patch_path,
            "detector_model": detector_model,
            "target_mode": unit.get("target_mode"),
            "manual_box_index": unit.get("manual_box_index"),
            "sep_target_index": unit.get("_sep_target_index"),
            "sep_target_total": unit.get("_sep_target_total"),
            "previous_image_ref": previous_ref,
            "patched_image_ref": _copy_ref(current_image_ref),
            "reference_lock": deepcopy(lock_policy),
            "prompt_lineage": deepcopy(prompt_lineage),
            "detailer_model_source": str(model_source_runtime.get("source") or "generation_model"),
            "detailer_lora_branch": {
                "inheritance_effective": lora_branch_runtime.get("inheritance_effective"),
                "applied_lora_count": lora_branch_runtime.get("applied_lora_count"),
                "lora_names": deepcopy(lora_branch_runtime.get("lora_names") or []),
            },
            "identity_policy": public_identity_policy_metadata(validation.get("identity_policy") if isinstance(validation.get("identity_policy"), dict) else {}),
            "family_preset": {
                "mode": family_preset_runtime.get("mode"),
                "family": family_preset_runtime.get("family"),
                "preset_id": family_preset_runtime.get("preset_id"),
                "effective_values": deepcopy(family_preset_runtime.get("effective_values") or {}),
            },
        })

    if not pass_summaries:
        append_diagnostic_issue(
            validation, code="adetailer_no_runnable_passes", stage="pass_payload",
            message="ADetailer produced no runnable repair pass; the request is blocked instead of queueing the base graph.",
            remediation="Correct or enable at least one detailer pass, or turn ADetailer off.",
        )
        reason = "ADetailer found no runnable detailer passes after V1 multi-pass expansion."
        if skipped_passes:
            reason += " skipped: " + "; ".join(f"{item.get('label')}: {item.get('reason')}" for item in skipped_passes[:4])
        validation["model_source"] = model_source_plan_before_runtime
        validation["lora_branch"] = lora_branch_plan_before_runtime
        validation["identity_policy"] = identity_policy_plan_before_runtime
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            previous_image_ref=initial_image_ref,
            patched_image_ref=initial_image_ref,
            output_consumers=output_consumers,
            reason=reason,
            applied=False,
            skipped_passes=skipped_passes,
            asset_bridge=asset_bridge,
            route_contract=contract_result,
        )
        return {"workflow": graph_before_detailer_nodes, "mutated": False, "workflow_patch": patch, "validation": validation}

    _rewrite_output_consumers(graph, output_consumers, current_image_ref)
    graph_invariants = validate_graph_invariants(
        incoming_graph, graph, route_contract=contract, output_consumers=output_consumers,
        previous_image_ref=initial_image_ref, patched_image_ref=current_image_ref,
        pass_summaries=pass_summaries,
    )
    if not graph_invariants.get("ready"):
        for issue in graph_invariants.get("errors", []):
            append_diagnostic_issue(
                validation, code=str(issue.get("code") or "adetailer_graph_invariant_failed"),
                stage="graph_invariants", message=str(issue.get("message") or "ADetailer final graph invariant failed."),
                field=issue.get("field"),
                **{key: value for key, value in issue.items() if key not in {"code", "message", "field", "stage", "level", "blocked", "ok", "remediation", "extension_id"}},
            )
        reason = "ADetailer rolled back the complete finish branch because the final graph invariant gate failed."
        patch = build_workflow_patch_summary(
            route=route, validation=validation, previous_image_ref=initial_image_ref,
            patched_image_ref=initial_image_ref, output_consumers=output_consumers,
            reason=reason, applied=False, skipped_passes=skipped_passes, asset_bridge=asset_bridge,
            route_contract=contract_result, graph_invariants=graph_invariants,
        )
        patch["rollback"] = {"applied": True, "scope": "entire_adetailer_mutation", "reason": "graph_invariant_failure"}
        patch["detector_execution"] = deepcopy(detector_execution)
        return {"workflow": incoming_graph, "mutated": False, "workflow_patch": patch, "validation": validation}
    unique_paths = sorted(set(patch_paths))
    patch_path = "mixed" if len(unique_paths) > 1 else unique_paths[0]
    reason = f"ADetailer applied {len(pass_summaries)} V1-style runtime detailer pass(es) from {int(derived.get('enabled_detailer_pass_count') or 0)} enabled card(s)."
    if any(item.get("sep_target_total") for item in pass_summaries):
        reason += f" Expanded [SEP] prompts into {sum(1 for item in pass_summaries if item.get('sep_target_total')) or len(pass_summaries)} ordered pass(es)."
    if any(item.get("manual_box_index") for item in pass_summaries):
        reason += " Manual boxes were routed through MaskToSEGS/SEGSDetailer."
    if notes:
        reason += " " + " ".join(notes[:4])
    if str(model_source_runtime.get("source") or "") == DEDICATED_CHECKPOINT_SOURCE:
        reason += f" Dedicated {str(model_source_runtime.get('family') or '').upper()} checkpoint {str(model_source_runtime.get('checkpoint') or '').rsplit('/', 1)[-1]} supplied MODEL/CLIP/VAE for the repair branch."
    identity_runtime = validation.get("identity_policy") if isinstance(validation.get("identity_policy"), dict) else {}
    if str(identity_runtime.get("effective") or "none") == "detailer_identity_lora":
        cloned_count = len(((identity_runtime.get("sampling_reapply") or {}).get("cloned_node_ids") or []))
        reason += f" Qwen Edit identity-LoRA assistance reapplied {cloned_count} compiler-owned model-sampling wrapper(s) after the isolated model-only LoRA."
    elif str(identity_runtime.get("effective") or "none") == "dedicated_detailer_model":
        reason += " Qwen Edit identity handling used the dedicated detailer-model fallback."
    if lora_branch_runtime.get("applied_lora_count"):
        reason += f" Applied {int(lora_branch_runtime.get('applied_lora_count') or 0)} detailer-branch LoRA(s) without rewiring the main generation sampler."
    elif str(lora_branch_runtime.get("inheritance_effective") or INHERIT_ALL) != INHERIT_ALL:
        reason += f" Detailer generation-LoRA inheritance used {str(lora_branch_runtime.get('inheritance_effective') or '').replace('_', ' ')}."
    if asset_bridge.get("staged_count"):
        reason += f" Staged {asset_bridge['staged_count']} selected detector model(s) into Comfy's native detector folders before queue."
    patch = build_workflow_patch_summary(
        route=route,
        validation=validation,
        node_ids=node_ids,
        previous_image_ref=initial_image_ref,
        patched_image_ref=current_image_ref,
        output_consumers=output_consumers,
        patch_path=patch_path,
        detector_model=", ".join([m for m in detector_models if m][:4]),
        reason=reason,
        applied=True,
        pass_summaries=pass_summaries,
        skipped_passes=skipped_passes,
        asset_bridge=asset_bridge,
        route_contract=contract_result,
        graph_invariants=graph_invariants,
    )
    patch["detector_execution"] = deepcopy(detector_execution)
    patch["source_ownership"] = {
        "kind": "explicit_post_output_source" if source_only_ref is not None else "generated_or_current_finish_output",
        "source_ref": _copy_ref(current_image_ref if not pass_summaries else pass_summaries[0].get("previous_image_ref")),
        "policy": "declared_source_encoder_only_no_arbitrary_loadimage_fallback",
    }
    return {"workflow": graph, "mutated": True, "workflow_patch": patch, "validation": validation, "image_ref": current_image_ref}
