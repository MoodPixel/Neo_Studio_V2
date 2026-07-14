from __future__ import annotations

from typing import Any

from .payload_schema import normalize_settings

FOREGROUND_PREFIX = "NeoStudioBackgroundRemoved"
MASK_PREFIX = "NeoStudioBackgroundMask"


def _apply_mask_refinement(
    graph: dict[str, Any],
    next_id: int,
    mask_ref: list[Any],
    settings: dict[str, Any],
) -> tuple[list[Any], int, list[str]]:
    notes: list[str] = []
    expand = int(settings.get("mask_expand") or 0)
    feather = int(settings.get("mask_feather") or 0)
    current = mask_ref
    if expand:
        node_id = str(next_id)
        graph[node_id] = {
            "class_type": _node_class(settings, "GrowMask"),
            "inputs": {
                "mask": current,
                "expand": expand,
                "tapered_corners": True,
            },
        }
        current = [node_id, 0]
        next_id += 1
        notes.append(f"Mask edge offset applied: {expand:+d}px.")
    if feather:
        node_id = str(next_id)
        graph[node_id] = {
            "class_type": _node_class(settings, "FeatherMask"),
            "inputs": {
                "mask": current,
                "left": feather,
                "top": feather,
                "right": feather,
                "bottom": feather,
            },
        }
        current = [node_id, 0]
        next_id += 1
        notes.append(f"Mask feather applied: {feather}px.")
    return current, next_id, notes


def _add_foreground_composite(
    graph: dict[str, Any],
    next_id: int,
    source_ref: list[Any],
    mask_ref: list[Any],
    settings: dict[str, Any],
) -> tuple[list[Any], int, str]:
    node_id = str(next_id)
    if settings.get("foreground_estimation"):
        graph[node_id] = {
            "class_type": _node_class(settings, "BlurFusionForegroundEstimation"),
            "inputs": {
                "images": source_ref,
                "masks": mask_ref,
                "blur_size": int(settings.get("blur_size") or 91),
                "blur_size_two": int(settings.get("blur_size_two") or 7),
                "fill_color": False,
                "color": 0,
            },
        }
        return [node_id, 0], next_id + 1, "foreground_estimation"
    graph[node_id] = {
        "class_type": _node_class(settings, "JoinImageWithAlpha"),
        "inputs": {
            "image": source_ref,
            "alpha": mask_ref,
        },
    }
    return [node_id, 0], next_id + 1, "alpha_join"


def _add_outputs(
    graph: dict[str, Any],
    next_id: int,
    foreground_ref: list[Any],
    mask_ref: list[Any],
    settings: dict[str, Any],
) -> int:
    graph[str(next_id)] = {
        "class_type": _node_class(settings, "SaveImage"),
        "inputs": {"filename_prefix": FOREGROUND_PREFIX, "images": foreground_ref},
    }
    next_id += 1
    if settings.get("save_mask"):
        graph[str(next_id)] = {
            "class_type": _node_class(settings, "MaskToImage"),
            "inputs": {"mask": mask_ref},
        }
        graph[str(next_id + 1)] = {
            "class_type": _node_class(settings, "SaveImage"),
            "inputs": {"filename_prefix": MASK_PREFIX, "images": [str(next_id), 0]},
        }
        next_id += 2
    if settings.get("preview_image"):
        graph[str(next_id)] = {
            "class_type": _node_class(settings, "PreviewImage"),
            "inputs": {"images": foreground_ref},
        }
        next_id += 1
    return next_id



def _node_class(settings: dict[str, Any], canonical: str) -> str:
    node_map = settings.get("sam_node_map") if isinstance(settings.get("sam_node_map"), dict) else {}
    return str(node_map.get(canonical) or canonical)


def _rect_mask(
    graph: dict[str, Any],
    next_id: int,
    *,
    width: int,
    height: int,
    bbox: dict[str, Any],
    settings: dict[str, Any],
) -> tuple[list[Any], int]:
    x1 = max(0.0, min(1.0, float(bbox.get("x1") or 0.0)))
    y1 = max(0.0, min(1.0, float(bbox.get("y1") or 0.0)))
    x2 = max(x1, min(1.0, float(bbox.get("x2") or 1.0)))
    y2 = max(y1, min(1.0, float(bbox.get("y2") or 1.0)))
    x_px = max(0, min(width - 2, int(round(x1 * width))))
    y_px = max(0, min(height - 2, int(round(y1 * height))))
    box_width = max(2, min(width - x_px, int(round((x2 - x1) * width))))
    box_height = max(2, min(height - y_px, int(round((y2 - y1) * height))))
    base_id = str(next_id)
    graph[base_id] = {"class_type": _node_class(settings, "SolidMask"), "inputs": {"value": 0.0, "width": width, "height": height}}
    region_id = str(next_id + 1)
    graph[region_id] = {"class_type": _node_class(settings, "SolidMask"), "inputs": {"value": 1.0, "width": box_width, "height": box_height}}
    composite_id = str(next_id + 2)
    graph[composite_id] = {
        "class_type": _node_class(settings, "MaskComposite"),
        "inputs": {"destination": [base_id, 0], "source": [region_id, 0], "x": x_px, "y": y_px, "operation": "add"},
    }
    return [composite_id, 0], next_id + 3


def _union_masks(
    graph: dict[str, Any],
    next_id: int,
    mask_refs: list[list[Any]],
    settings: dict[str, Any],
) -> tuple[list[Any], int]:
    current = mask_refs[0]
    for mask_ref in mask_refs[1:]:
        node_id = str(next_id)
        graph[node_id] = {
            "class_type": _node_class(settings, "MaskComposite"),
            "inputs": {"destination": current, "source": mask_ref, "x": 0, "y": 0, "operation": "add"},
        }
        current = [node_id, 0]
        next_id += 1
    return current, next_id


def build_interactive_sam_comfy_workflow(
    source_image_name: str,
    settings: dict[str, Any] | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    clean = normalize_settings(settings)
    image_name = str(source_image_name or "").strip()
    if not image_name:
        raise ValueError("Interactive SAM needs a source image.")
    subjects = [item for item in (clean.get("sam_subjects") or []) if item.get("selected", True)]
    if not subjects:
        raise ValueError("Select at least one subject before running Interactive SAM.")
    if any(not item.get("bbox") for item in subjects):
        raise ValueError("Comfy Impact SAM needs a box for every selected subject.")
    if any(item.get("keep_points") or item.get("remove_points") for item in subjects):
        raise ValueError("Per-subject Keep/Remove points require Neo Native ONNX SAM.")
    model_name = str(clean.get("sam_comfy_model") or "").strip()
    if not model_name:
        raise ValueError("Choose an installed Comfy SAM model from ComfyUI/models/sams.")
    width = max(8, int(clean.get("source_width") or 0))
    height = max(8, int(clean.get("source_height") or 0))
    graph: dict[str, Any] = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "2": {
            "class_type": _node_class(clean, "SAMLoader"),
            "inputs": {"model_name": model_name, "device_mode": "AUTO"},
        },
    }
    next_id = 3
    subject_masks: list[list[Any]] = []
    for subject in subjects:
        rect_ref, next_id = _rect_mask(graph, next_id, width=width, height=height, bbox=subject.get("bbox") or {}, settings=clean)
        segs_id = str(next_id)
        graph[segs_id] = {
            "class_type": _node_class(clean, "MaskToSEGS"),
            "inputs": {"mask": rect_ref, "combined": False, "crop_factor": 1.12, "bbox_fill": False, "drop_size": 1, "contour_fill": False},
        }
        next_id += 1
        sam_id = str(next_id)
        graph[sam_id] = {
            "class_type": _node_class(clean, "SAMDetectorCombined"),
            "inputs": {
                "sam_model": ["2", 0],
                "segs": [segs_id, 0],
                "image": ["1", 0],
                "detection_hint": "center-1",
                "dilation": 0,
                "threshold": 0.93,
                "bbox_expansion": 0,
                "mask_hint_threshold": 0.70,
                "mask_hint_use_negative": "False",
            },
        }
        subject_masks.append([sam_id, 0])
        next_id += 1
    sam_mask, next_id = _union_masks(graph, next_id, subject_masks, clean)

    mask_ref = sam_mask
    gate_notes: list[str] = []
    refine_used = False
    node_map = clean.get("sam_node_map") if isinstance(clean.get("sam_node_map"), dict) else {}
    comfy_birefnet_model = str(clean.get("model") or "").strip()
    shared_refine_enabled = bool(clean.get("sam_shared_refine_enabled"))
    if clean.get("sam_refine_mode") == "birefnet_gate" and shared_refine_enabled and comfy_birefnet_model and all(key in node_map for key in ("LoadRembgByBiRefNetModel", "GetMaskByBiRefNet")):
        gate_settings = dict(clean)
        gate_settings["mask_expand"] = int(clean.get("sam_gate_expand") or 0)
        gate_settings["mask_feather"] = int(clean.get("sam_gate_feather") or 0)
        gate_ref, next_id, gate_notes = _apply_mask_refinement(graph, next_id, sam_mask, gate_settings)
        loader_id = str(next_id)
        graph[loader_id] = {
            "class_type": _node_class(clean, "LoadRembgByBiRefNetModel"),
            "inputs": {"model": comfy_birefnet_model, "device": clean.get("device") or "AUTO", "use_weight": bool(clean.get("use_weight")), "dtype": clean.get("dtype") or "float32"},
        }
        next_id += 1
        mask_id = str(next_id)
        graph[mask_id] = {
            "class_type": _node_class(clean, "GetMaskByBiRefNet"),
            "inputs": {
                "model": [loader_id, 0], "images": ["1", 0],
                "width": int(clean.get("width") or 1024), "height": int(clean.get("height") or 1024),
                "upscale_method": clean.get("upscale_method") or "bilinear", "mask_threshold": 0.0,
            },
        }
        next_id += 1
        multiply_id = str(next_id)
        graph[multiply_id] = {
            "class_type": _node_class(clean, "MaskComposite"),
            "inputs": {"destination": [mask_id, 0], "source": gate_ref, "x": 0, "y": 0, "operation": "multiply"},
        }
        mask_ref = [multiply_id, 0]
        next_id += 1
        refine_used = True

    mask_ref, next_id, refinement_notes = _apply_mask_refinement(graph, next_id, mask_ref, clean)
    foreground_settings = dict(clean)
    foreground_settings["foreground_estimation"] = bool(clean.get("foreground_estimation") and node_map.get("BlurFusionForegroundEstimation"))
    foreground_ref, next_id, composite_mode = _add_foreground_composite(graph, next_id, ["1", 0], mask_ref, foreground_settings)
    _add_outputs(graph, next_id, foreground_ref, mask_ref, clean)
    notes = [
        f"Shared Impact Pack SAM loaded {model_name} once and segmented {len(subjects)} selected subject(s) independently.",
        "Only selected subject masks were unioned; unselected detected people were excluded from the foreground.",
        f"Foreground composition mode: {composite_mode}.",
        *gate_notes,
        *refinement_notes,
    ]
    if refine_used:
        notes.append(f"Comfy BiRefNet soft-edge refinement used {comfy_birefnet_model} inside the combined SAM gate.")
    elif clean.get("sam_refine_mode") == "birefnet_gate":
        fallback_reason = str(clean.get("sam_refine_fallback_reason") or "").strip()
        notes.append("BiRefNet edge handoff was unavailable on the shared Comfy route; the combined SAM mask was preserved." + (f" {fallback_reason}" if fallback_reason else ""))
    return graph, clean, notes


def build_background_refinement_workflow(
    source_image_name: str,
    mask_image_name: str,
    settings: dict[str, Any] | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    base = normalize_settings(settings)
    clean = normalize_settings({**base, "workflow_mode": "refine_mask"})
    image_name = str(source_image_name or "").strip()
    mask_name = str(mask_image_name or "").strip()
    if not image_name:
        raise ValueError("Mask Review refinement needs a source image.")
    if not mask_name:
        raise ValueError("Mask Review refinement needs a reviewed mask PNG.")

    graph: dict[str, Any] = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
        "2": {"class_type": "LoadImage", "inputs": {"image": mask_name, "upload": "image"}},
        "3": {"class_type": "ImageToMask", "inputs": {"image": ["2", 0], "channel": "red"}},
    }
    mask_ref, next_id, refinement_notes = _apply_mask_refinement(graph, 4, ["3", 0], clean)
    foreground_ref, next_id, composite_mode = _add_foreground_composite(graph, next_id, ["1", 0], mask_ref, clean)
    _add_outputs(graph, next_id, foreground_ref, mask_ref, clean)
    notes = [
        "Mask Review refinement reused the saved source and reviewed mask without rerunning BiRefNet segmentation.",
        f"Foreground composition mode: {composite_mode}.",
        *refinement_notes,
    ]
    if clean.get("save_mask"):
        notes.append("The refined grayscale alpha-mask PNG is saved beside the RGBA foreground.")
    return graph, clean, notes


def build_background_removal_workflow(
    source_image_name: str,
    settings: dict[str, Any] | str | None = None,
    mask_image_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    clean = normalize_settings(settings)
    if clean.get("workflow_mode") == "refine_mask":
        return build_background_refinement_workflow(source_image_name, mask_image_name, clean)
    if clean.get("workflow_mode") == "interactive_sam" and clean.get("resolved_engine") == "comfy_sam":
        return build_interactive_sam_comfy_workflow(source_image_name, clean)

    image_name = str(source_image_name or "").strip()
    if not image_name:
        raise ValueError("Background Removal needs a source image.")
    model_name = str(clean.get("model") or "").strip()
    if not model_name:
        raise ValueError("Choose an installed BiRefNet model before removing the background.")

    graph: dict[str, Any] = {
        "1": {
            "class_type": "LoadImage",
            "inputs": {"image": image_name, "upload": "image"},
        },
        "2": {
            "class_type": "LoadRembgByBiRefNetModel",
            "inputs": {
                "model": model_name,
                "device": clean.get("device") or "AUTO",
                "use_weight": bool(clean.get("use_weight")),
                "dtype": clean.get("dtype") or "float32",
            },
        },
        "3": {
            "class_type": "RembgByBiRefNetAdvanced",
            "inputs": {
                "model": ["2", 0],
                "images": ["1", 0],
                "width": int(clean.get("width") or 1024),
                "height": int(clean.get("height") or 1024),
                "upscale_method": clean.get("upscale_method") or "bilinear",
                "blur_size": int(clean.get("blur_size") or 91),
                "blur_size_two": int(clean.get("blur_size_two") or 7),
                "fill_color": False,
                "color": 0,
                "mask_threshold": float(clean.get("mask_threshold") or 0.0),
            },
        },
    }

    mask_ref, next_id, refinement_notes = _apply_mask_refinement(graph, 4, ["3", 1], clean)
    needs_recompose = bool(refinement_notes) or not clean.get("foreground_estimation")
    if needs_recompose:
        foreground_ref, next_id, composite_mode = _add_foreground_composite(graph, next_id, ["1", 0], mask_ref, clean)
    else:
        foreground_ref = ["3", 0]
        composite_mode = "birefnet_advanced"
    _add_outputs(graph, next_id, foreground_ref, mask_ref, clean)

    notes = [
        f"BiRefNet background removal uses {model_name} with preset {clean.get('preset')}.",
        f"Foreground composition mode: {composite_mode}.",
        "The foreground is saved as an RGBA PNG with the predicted soft mask as alpha.",
        *refinement_notes,
    ]
    if clean.get("save_mask"):
        notes.append("A grayscale alpha-mask PNG is saved as a second output.")
    return graph, clean, notes
