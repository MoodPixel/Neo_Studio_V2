from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import CFG_SAFETY_CAP, EXTENSION_ID, PHASE
from .payload_schema import parse_sep_targets
from .validation import validate_and_normalize_payload


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


def _find_source_load_image_ref(workflow: dict[str, Any]) -> list[Any] | None:
    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") != "VAEEncode":
            continue
        pixels = (node.get("inputs") or {}).get("pixels")
        if isinstance(pixels, (list, tuple)) and len(pixels) >= 2:
            src = workflow.get(str(pixels[0]))
            if isinstance(src, dict) and src.get("class_type") == "LoadImage":
                return [str(pixels[0]), 0]
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            return [str(node_id), 0]
    return None


def _is_preview_detailer_output_pass(validation: dict[str, Any], params: dict[str, Any]) -> bool:
    block = validation.get("block") if isinstance(validation.get("block"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    return bool(
        params.get("detailer_output_pass")
        or metadata.get("detailer_output_pass")
        or metadata.get("source_mode") == "preview_action_selected_output"
        or inputs.get("preview_action_source")
        or metadata.get("preview_action_source")
    )


def _find_base_image_ref(workflow: dict[str, Any]) -> list[Any]:
    # ADetailer is the final selective repair pass. If another finish extension
    # such as High-Res Lab already rewired Save/Preview to an upscaled image,
    # use that current output as the ADetailer source instead of falling back
    # to the original VAEDecode node.
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") in {"SaveImage", "PreviewImage"}:
            ref = (node.get("inputs") or {}).get("images")
            if isinstance(ref, (list, tuple)):
                return _copy_ref(ref)
    node_id, _node = _find_first_node(workflow, {"VAEDecode", "VAEDecodeTiled"})
    if node_id:
        return [node_id, 0]
    return ["8", 0]


def _find_vae_ref(workflow: dict[str, Any]) -> list[Any]:
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") in {"VAEDecode", "VAEDecodeTiled"}:
            ref = (node.get("inputs") or {}).get("vae")
            if isinstance(ref, (list, tuple)):
                return _copy_ref(ref)
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "CheckpointLoaderSimple":
            return [str(node_id), 2]
    return ["1", 2]


def _find_sampler_refs(workflow: dict[str, Any], sampler_node_id: str | int = "5") -> dict[str, list[Any]]:
    inputs = _node_inputs(workflow, sampler_node_id)
    if not inputs:
        sampler_id, sampler = _find_first_node(workflow, {"KSampler", "KSamplerAdvanced"})
        inputs = sampler.get("inputs", {}) if sampler else {}
    return {
        "positive": _copy_ref(inputs.get("positive"), ["6", 0]),
        "negative": _copy_ref(inputs.get("negative"), ["7", 0]),
        "latent": _copy_ref(inputs.get("latent_image"), []),
        "model": _copy_ref(inputs.get("model"), ["1", 0]),
    }


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
    model_name = str(params.get("detector_model") or "").strip()
    if detector_type.startswith("onnx"):
        return "ONNXDetectorProvider", {"model_name": model_name}, model_name
    # Impact Pack expects Ultralytics models under bbox/ or segm/ unless the user already supplied a scoped path.
    if "/" not in model_name:
        model_name = f"{'segm' if detector_type == 'segm' else 'bbox'}/{model_name}"
    return "UltralyticsDetectorProvider", {"model_name": model_name}, model_name


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
    graph[positive_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": positive_text.strip(), "clip": _copy_ref(clip_ref, ["1", 1])}}
    next_id = _next_graph_id(graph)
    negative_id = next_id
    graph[negative_id] = {"class_type": "CLIPTextEncode", "inputs": {"text": negative_text.strip(), "clip": _copy_ref(clip_ref, ["1", 1])}}
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
        or str(params.get("reference_lock") or "none") != "none"
    )
    return has_segs and advanced_targeting

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
        "model": _copy_ref(model_ref, ["1", 0]),
        "clip": _copy_ref(clip_ref, ["1", 1]),
        "vae": _copy_ref(vae_ref, ["1", 2]),
        "guide_size": 512.0,
        "guide_size_for": True,
        "max_size": 1024.0,
        "seed": max(1, int(seed or 1)),
        "steps": int(params.get("steps") or 20),
        "cfg": float(params.get("cfg") if params.get("cfg") is not None else CFG_SAFETY_CAP),
        "sampler_name": sampler_name,
        "scheduler": scheduler,
        "positive": _copy_ref(positive_ref),
        "negative": _copy_ref(negative_ref),
        "denoise": float(params.get("denoise") or 0.35),
        "feather": int(params.get("mask_blur") or 4),
        "noise_mask": True,
        "force_inpaint": True,
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
        "noise_mask_feather": int(params.get("mask_blur") or 4),
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
    graph[detailer_id] = {"class_type": "SEGSDetailer", "inputs": {"image": _copy_ref(current_image_ref), "segs": _copy_ref(segs_ref), "guide_size": 512.0, "guide_size_for": True, "max_size": float(max(width, height, 1024)), "seed": max(1, int(seed or 1)), "steps": int(params.get("steps") or 20), "cfg": float(params.get("cfg") if params.get("cfg") is not None else CFG_SAFETY_CAP), "sampler_name": sampler_name, "scheduler": scheduler, "denoise": float(params.get("denoise") or 0.35), "noise_mask": True, "force_inpaint": bool(params.get("force_inpaint", True)), "basic_pipe": [pipe_id, 0], "refiner_ratio": 0.2, "batch_size": 1, "cycle": 1, "inpaint_model": False, "noise_mask_feather": int(params.get("mask_blur") or 4)}}
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
) -> dict[str, Any]:
    pass_summaries = list(pass_summaries or [])
    skipped_passes = list(skipped_passes or [])
    paths = sorted({str(item.get("patch_path") or "") for item in pass_summaries if item.get("patch_path")})
    node_class = "mixed" if len(paths) > 1 else ("FaceDetailer" if patch_path == "face_detailer" else ("SEGSDetailer" if patch_path == "segs_detailer" else ""))
    return {
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
        "skipped_passes": deepcopy(skipped_passes),
    }


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
    **_: Any,
) -> dict[str, Any]:
    graph = deepcopy(workflow or {})
    validation = validate_and_normalize_payload(payload, route=route, available_nodes=available_nodes)
    if not validation.get("workflow_patch_allowed"):
        reason = validation.get("support", {}).get("reason") or "ADetailer is gated, disabled, or unsupported."
        patch = build_workflow_patch_summary(route=route, validation=validation, reason=reason, applied=False)
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    params = validation.get("params") or {}
    derived = validation.get("derived") or {}
    width, height = _find_canvas_size(graph, params)
    runtime_units = _detailer_pass_units(params, derived, width, height)
    runtime_units = [unit for unit in runtime_units if unit.get("enabled", True)]
    if not runtime_units:
        reason = "ADetailer is enabled but no enabled detailer pass/runtime unit remained after expansion."
        patch = build_workflow_patch_summary(route=route, validation=validation, reason=reason, applied=False)
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    refs = _find_sampler_refs(graph, sampler_node_id=sampler_node_id)
    source_only_ref = _find_source_load_image_ref(graph) if _is_preview_detailer_output_pass(validation, params) else None
    current_image_ref = source_only_ref or _find_base_image_ref(graph)
    output_consumers = _find_output_consumers(graph, current_image_ref)
    if not output_consumers:
        reason = "ADetailer could not find a SaveImage/PreviewImage consumer to replace; workflow mutation skipped safely."
        patch = build_workflow_patch_summary(route=route, validation=validation, previous_image_ref=current_image_ref, patched_image_ref=current_image_ref, output_consumers=[], reason=reason, applied=False)
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    vae_ref = _find_vae_ref(graph)
    current_model_ref = _copy_ref(model_ref, refs["model"] or ["1", 0])
    current_clip_ref = _copy_ref(clip_ref, ["1", 1])
    positive_ref = refs["positive"]
    negative_ref = refs["negative"]
    sampler_inputs = _node_inputs(graph, sampler_node_id)
    effective_seed = int(seed or sampler_inputs.get("seed") or 1)
    effective_sampler = sampler_name or str(sampler_inputs.get("sampler_name") or "euler")
    effective_scheduler = scheduler or str(sampler_inputs.get("scheduler") or "normal")

    next_id = _next_graph_id(graph)
    node_ids: list[str] = []
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

        pos_text = str(unit.get("positive_prompt") or "").strip()
        neg_text = str(unit.get("negative_prompt") or "").strip()
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

        use_segs = _should_use_segs(unit, derived, validation.get("node_status") or {})
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
                params=unit,
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
                params=unit,
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
        })

    if not pass_summaries:
        reason = "ADetailer found no runnable detailer passes after V1 multi-pass expansion."
        if skipped_passes:
            reason += " skipped: " + "; ".join(f"{item.get('label')}: {item.get('reason')}" for item in skipped_passes[:4])
        patch = build_workflow_patch_summary(
            route=route,
            validation=validation,
            previous_image_ref=_find_base_image_ref(workflow or {}),
            patched_image_ref=_find_base_image_ref(workflow or {}),
            output_consumers=output_consumers,
            reason=reason,
            applied=False,
            skipped_passes=skipped_passes,
        )
        return {"workflow": graph, "mutated": False, "workflow_patch": patch, "validation": validation}

    _rewrite_output_consumers(graph, output_consumers, current_image_ref)
    unique_paths = sorted(set(patch_paths))
    patch_path = "mixed" if len(unique_paths) > 1 else unique_paths[0]
    reason = f"ADetailer applied {len(pass_summaries)} V1-style runtime detailer pass(es) from {int(derived.get('enabled_detailer_pass_count') or 0)} enabled card(s)."
    if any(item.get("sep_target_total") for item in pass_summaries):
        reason += f" Expanded [SEP] prompts into {sum(1 for item in pass_summaries if item.get('sep_target_total')) or len(pass_summaries)} ordered pass(es)."
    if any(item.get("manual_box_index") for item in pass_summaries):
        reason += " Manual boxes were routed through MaskToSEGS/SEGSDetailer."
    if notes:
        reason += " " + " ".join(notes[:4])
    patch = build_workflow_patch_summary(
        route=route,
        validation=validation,
        node_ids=node_ids,
        previous_image_ref=_find_base_image_ref(workflow or {}),
        patched_image_ref=current_image_ref,
        output_consumers=output_consumers,
        patch_path=patch_path,
        detector_model=", ".join([m for m in detector_models if m][:4]),
        reason=reason,
        applied=True,
        pass_summaries=pass_summaries,
        skipped_passes=skipped_passes,
    )
    return {"workflow": graph, "mutated": True, "workflow_patch": patch, "validation": validation, "image_ref": current_image_ref}
