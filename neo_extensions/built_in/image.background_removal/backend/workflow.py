from __future__ import annotations

from typing import Any

from .payload_schema import normalize_settings
from .rmbg_node import build_rmbg_node_graph

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
    operation = str(settings.get("sam_mask_operation") or "union")
    comfy_operation = {"union": "add", "intersection": "multiply", "subtract": "subtract"}.get(operation, "add")
    current = mask_refs[0]
    for mask_ref in mask_refs[1:]:
        node_id = str(next_id)
        graph[node_id] = {
            "class_type": _node_class(settings, "MaskComposite"),
            "inputs": {"destination": current, "source": mask_ref, "x": 0, "y": 0, "operation": comfy_operation},
        }
        current = [node_id, 0]
        next_id += 1
    return current, next_id


def _combine_masks(
    graph: dict[str, Any],
    next_id: int,
    mask_refs: list[list[Any]],
    operation: str,
    settings: dict[str, Any],
) -> tuple[list[Any], int]:
    """Combine prompt masks using Comfy's explicit mask algebra operation."""

    if not mask_refs:
        raise ValueError("Segmentation Lab produced no mask references.")
    if len(mask_refs) == 1:
        return mask_refs[0], next_id
    operation = operation if operation in {"union", "intersection", "subtract"} else "union"
    comfy_operation = {"union": "add", "intersection": "multiply", "subtract": "subtract"}[operation]
    current = mask_refs[0]
    for mask_ref in mask_refs[1:]:
        node_id = str(next_id)
        graph[node_id] = {
            "class_type": _node_class(settings, "MaskComposite"),
            "inputs": {"destination": current, "source": mask_ref, "x": 0, "y": 0, "operation": comfy_operation},
        }
        current = [node_id, 0]
        next_id += 1
    return current, next_id


def build_segmentation_lab_workflow(
    source_image_name: str,
    settings: dict[str, Any] | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Build a prompt-segmentation-only graph from a verified RMBG adapter."""

    clean = normalize_settings(settings)
    image_name = str(source_image_name or "").strip()
    if not image_name:
        raise ValueError("Segmentation Lab needs a source image.")
    prompts = [item for item in (clean.get("segmentation_lab_prompts") or []) if item.get("enabled", True)]
    if not prompts:
        raise ValueError("Segmentation Lab needs at least one natural-language object prompt.")
    adapter = str(clean.get("segmentation_adapter") or "").strip().lower()
    node_class = str(clean.get("segmentation_node_class") or "").strip()
    if not adapter or not node_class:
        raise ValueError("Segmentation Lab has no verified live Comfy adapter.")

    graph: dict[str, Any] = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
    }
    next_id = 2
    mask_refs: list[list[Any]] = []
    for prompt in prompts:
        node_id = str(next_id)
        inputs: dict[str, Any] = {"image": ["1", 0], "prompt": str(prompt.get("prompt") or "").strip()}
        if adapter in {"rmbg_v1", "rmbg_v2"}:
            inputs.update({
                "sam_model": str(clean.get("segmentation_sam_model") or ""),
                "dino_model": str(clean.get("segmentation_dino_model") or ""),
                "threshold": float(clean.get("segmentation_threshold") or 0.35),
                "mask_blur": 0,
                "mask_offset": 0,
                "background": "Alpha",
                "invert_output": False,
            })
        elif adapter == "sam2":
            inputs.update({
                "sam2_model": str(clean.get("segmentation_sam2_model") or ""),
                "dino_model": str(clean.get("segmentation_dino_model") or ""),
                "device": str(clean.get("segmentation_device") or "Auto"),
                "threshold": float(clean.get("segmentation_threshold") or 0.35),
                "mask_blur": 0,
                "mask_offset": 0,
                "background": "Alpha",
                "invert_output": False,
            })
        elif adapter == "sam3":
            inputs.update({
                "output_mode": "Merged",
                "confidence_threshold": float(clean.get("segmentation_confidence_threshold") or 0.5),
                "max_segments": int(clean.get("segmentation_max_segments") or 0),
                "segment_pick": int(clean.get("segmentation_segment_pick") or 0),
                "mask_blur": 0,
                "mask_offset": 0,
                "device": str(clean.get("segmentation_device") or "Auto"),
                "invert_output": False,
                "unload_model": False,
                "background": "Alpha",
            })
        else:
            raise ValueError(f"Unsupported verified Segmentation Lab adapter: {adapter}")
        graph[node_id] = {"class_type": node_class, "inputs": inputs}
        mask_refs.append([node_id, 1])
        next_id += 1

    mask_ref, next_id = _combine_masks(graph, next_id, mask_refs, str(clean.get("segmentation_mask_operation") or "union"), clean)
    mask_ref, next_id, refinement_notes = _apply_mask_refinement(graph, next_id, mask_ref, clean)
    foreground_settings = dict(clean)
    foreground_settings["foreground_estimation"] = False
    foreground_ref, next_id, composite_mode = _add_foreground_composite(graph, next_id, ["1", 0], mask_ref, foreground_settings)
    _add_outputs(graph, next_id, foreground_ref, mask_ref, clean)
    operation = str(clean.get("segmentation_mask_operation") or "union")
    notes = [
        f"Segmentation Lab used the verified {adapter} adapter ({node_class}) for {len(prompts)} prompt row(s).",
        f"Prompt masks were combined with {operation} operation in prompt order.",
        f"Foreground composition mode: {composite_mode}.",
        *refinement_notes,
    ]
    return graph, clean, notes


def build_region_segmentation_workflow(
    source_image_name: str,
    settings: dict[str, Any] | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Build a face/clothes/fashion/accessories mask graph from live nodes."""

    clean = normalize_settings(settings)
    image_name = str(source_image_name or "").strip()
    if not image_name:
        raise ValueError("Region segmentation needs a source image.")
    targets = [item for item in (clean.get("region_segmentation_targets") or []) if item.get("enabled", True)]
    if not targets:
        raise ValueError("Region segmentation needs at least one enabled target.")
    default_node_class = str(clean.get("region_segmentation_node_class") or "").strip()
    adapter = str(clean.get("region_segmentation_adapter") or "").strip().lower()

    graph: dict[str, Any] = {"1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}}}
    next_id = 2
    mask_refs: list[list[Any]] = []
    for target in targets:
        node_id = str(next_id)
        target_node_class = str(target.get("node_class") or default_node_class).strip()
        target_adapter = str(target.get("adapter") or target.get("region") or adapter).strip().lower()
        if not target_node_class or target_adapter not in {"face", "clothes", "fashion", "accessories"}:
            raise ValueError("Region segmentation has no verified live Comfy adapter for a target.")
        if target_adapter == "accessories":
            options_node_class = str(target.get("options_node_class") or "").strip()
            if not options_node_class:
                raise ValueError("Accessories segmentation has no verified FashionSegmentAccessories selector node.")
            options_id = str(next_id)
            option_inputs = {str(class_name): True for class_name in target.get("classes") or [] if str(class_name).strip()}
            if not option_inputs:
                raise ValueError("Accessories segmentation needs at least one live accessory or detail class.")
            graph[options_id] = {"class_type": options_node_class, "inputs": option_inputs}
            node_id = str(next_id + 1)
            inputs: dict[str, Any] = {"images": ["1", 0], "accessories_options": [options_id, 0]}
            graph[node_id] = {"class_type": target_node_class, "inputs": inputs}
            next_id += 2
        else:
            inputs = {"images": ["1", 0]}
            for class_name in target.get("classes") or []:
                inputs[str(class_name)] = True
            graph[node_id] = {"class_type": target_node_class, "inputs": inputs}
            next_id += 1
        mask_refs.append([node_id, 1])
    mask_ref, next_id = _combine_masks(graph, next_id, mask_refs, str(clean.get("region_segmentation_mask_operation") or "union"), clean)
    mask_ref, next_id, refinement_notes = _apply_mask_refinement(graph, next_id, mask_ref, clean)
    foreground_settings = dict(clean)
    foreground_settings["foreground_estimation"] = False
    foreground_ref, next_id, composite_mode = _add_foreground_composite(graph, next_id, ["1", 0], mask_ref, foreground_settings)
    _add_outputs(graph, next_id, foreground_ref, mask_ref, clean)
    operation = str(clean.get("region_segmentation_mask_operation") or "union")
    notes = [
        f"Region segmentation used verified {adapter or 'per-target'} adapter(s) for {len(targets)} target row(s).",
        f"Region masks were combined with {operation} operation in target order.",
        f"Foreground composition mode: {composite_mode}.",
        *refinement_notes,
    ]
    return graph, clean, notes


def build_mask_utility_workflow(
    source_image_name: str,
    settings: dict[str, Any] | str | None = None,
    mask_image_names: list[str] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Build a verified RMBG mask/object utility graph."""

    clean = normalize_settings(settings)
    image_name = str(source_image_name or "").strip()
    operation = str(clean.get("mask_utility_operation") or "").strip().lower()
    node_class = str(clean.get("mask_utility_node_class") or "").strip()
    if not image_name:
        raise ValueError("Mask utilities need a source image.")
    if operation not in {"enhance", "combine", "extract", "crop_object", "convert", "color_to_mask", "mask_overlay", "object_remove_lama", "image_mask_resize", "image_crop"} or not node_class:
        raise ValueError("Mask utility has no verified live Comfy adapter.")
    mask_names = [str(item or "").strip() for item in (mask_image_names or []) if str(item or "").strip()][:4]
    if operation in {"enhance", "combine", "extract", "crop_object", "mask_overlay", "object_remove_lama"} and not mask_names:
        raise ValueError(f"Mask utility operation {operation} needs an uploaded mask image.")

    graph: dict[str, Any] = {"1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}}}
    next_id = 2
    input_names = set(clean.get("mask_utility_input_names") or [])
    mask_refs: list[list[Any]] = []
    for mask_name in mask_names:
        load_id = str(next_id)
        graph[load_id] = {"class_type": "LoadImage", "inputs": {"image": mask_name, "upload": "image"}}
        convert_id = str(next_id + 1)
        graph[convert_id] = {"class_type": "ImageToMask", "inputs": {"image": [load_id, 0], "channel": str(clean.get("mask_utility_mask_channel") or "red")}}
        mask_refs.append([convert_id, 0])
        next_id += 2

    result_mask_ref: list[Any] | None = mask_refs[0] if mask_refs else None
    result_image_ref: list[Any] | None = None
    if operation == "mask_overlay":
        if not mask_refs:
            raise ValueError("Mask Overlay needs an uploaded mask image.")
        inputs = {
            "mask_opacity": float(clean.get("mask_utility_overlay_opacity") or 0.5),
            "mask_color": str(clean.get("mask_utility_overlay_color") or "#0000FF"),
            "image": ["1", 0],
            "mask": mask_refs[0],
        }
        graph[str(next_id)] = {"class_type": node_class, "inputs": {key: value for key, value in inputs.items() if not input_names or key in input_names}}
        result_image_ref = [str(next_id), 0]
        result_mask_ref = [str(next_id), 1]
        next_id += 1
    elif operation == "object_remove_lama":
        if not mask_refs:
            raise ValueError("Object Remover · Lama needs an uploaded mask image.")
        inputs = {
            "images": ["1", 0],
            "masks": mask_refs[0],
            "removal_strength": int(clean.get("mask_utility_lama_removal_strength") or 230),
            "edge_smoothness": int(clean.get("mask_utility_lama_edge_smoothness") or 8),
        }
        graph[str(next_id)] = {"class_type": node_class, "inputs": {key: value for key, value in inputs.items() if not input_names or key in input_names}}
        result_image_ref = [str(next_id), 0]
        result_mask_ref = mask_refs[0]
        next_id += 1
    elif operation == "image_mask_resize":
        inputs = {
            "image": ["1", 0],
            "custom_width": int(clean.get("mask_utility_resize_width") or 0),
            "custom_height": int(clean.get("mask_utility_resize_height") or 0),
            "megapixels": float(clean.get("mask_utility_resize_megapixels") or 0.0),
            "scale_by": float(clean.get("mask_utility_resize_scale_by") or 1.0),
            "resize_mode": str(clean.get("mask_utility_resize_mode") or "longest_side"),
            "resize_value": int(clean.get("mask_utility_resize_value") or 0),
            "upscale_method": str(clean.get("mask_utility_resize_method") or "lanczos"),
            "device": str(clean.get("mask_utility_resize_device") or "cpu"),
            "divisible_by": int(clean.get("mask_utility_resize_divisible_by") or 2),
            "output_mode": str(clean.get("mask_utility_resize_output_mode") or "stretch"),
            "crop_position": str(clean.get("mask_utility_resize_crop_position") or "center"),
            "pad_color": str(clean.get("mask_utility_resize_pad_color") or "#FFFFFF"),
        }
        if mask_refs:
            inputs["mask"] = mask_refs[0]
        graph[str(next_id)] = {"class_type": node_class, "inputs": {key: value for key, value in inputs.items() if not input_names or key in input_names}}
        result_image_ref = [str(next_id), 0]
        result_mask_ref = [str(next_id), 1]
        next_id += 1
    elif operation == "image_crop":
        crop_inputs = {
            "image": ["1", 0],
            "width": int(clean.get("mask_utility_crop_width") or 1024),
            "height": int(clean.get("mask_utility_crop_height") or 1024),
            "x_offset": int(clean.get("mask_utility_crop_x_offset") or 0),
            "y_offset": int(clean.get("mask_utility_crop_y_offset") or 0),
            "split": bool(clean.get("mask_utility_crop_split")),
            "position": str(clean.get("mask_utility_crop_position") or "center"),
        }
        graph[str(next_id)] = {"class_type": node_class, "inputs": {key: value for key, value in crop_inputs.items() if not input_names or key in input_names}}
        result_image_ref = [str(next_id), 0]
        next_id += 1
        if mask_refs:
            mask_image_id = str(next_id)
            graph[mask_image_id] = {"class_type": "MaskToImage", "inputs": {"mask": mask_refs[0]}}
            mask_crop_id = str(next_id + 1)
            mask_crop_inputs = {**crop_inputs, "image": [mask_image_id, 0], "split": False}
            graph[mask_crop_id] = {"class_type": node_class, "inputs": {key: value for key, value in mask_crop_inputs.items() if not input_names or key in input_names}}
            mask_convert_id = str(next_id + 2)
            graph[mask_convert_id] = {"class_type": "ImageToMask", "inputs": {"image": [mask_crop_id, 0], "channel": "red"}}
            result_mask_ref = [mask_convert_id, 0]
            next_id += 3
    elif operation == "enhance":
        inputs: dict[str, Any] = {"mask": mask_refs[0]}
        optional = {
            "sensitivity": float(clean.get("mask_utility_sensitivity") or 1.0),
            "mask_blur": int(clean.get("mask_utility_mask_blur") or 0),
            "mask_offset": int(clean.get("mask_utility_mask_offset") or 0),
            "smooth": float(clean.get("mask_utility_smooth") or 0.0),
            "fill_holes": bool(clean.get("mask_utility_fill_holes")),
            "invert_output": bool(clean.get("mask_utility_invert")),
        }
        inputs.update({key: value for key, value in optional.items() if not input_names or key in input_names})
        graph[str(next_id)] = {"class_type": node_class, "inputs": inputs}
        result_mask_ref = [str(next_id), 0]
        next_id += 1
    elif operation == "combine":
        if len(mask_refs) < 1:
            raise ValueError("Mask Combiner needs at least one mask image.")
        mode = {"union": "combine", "intersection": "intersection", "difference": "difference"}.get(str(clean.get("mask_utility_mask_operation") or "union"), "combine")
        inputs = {"mask_1": mask_refs[0], "mode": mode}
        for index, mask_ref in enumerate(mask_refs[1:4], start=2):
            inputs[f"mask_{index}"] = mask_ref
        graph[str(next_id)] = {"class_type": node_class, "inputs": inputs}
        result_mask_ref = [str(next_id), 0]
        next_id += 1
    elif operation == "extract":
        inputs = {"image": ["1", 0], "mode": str(clean.get("mask_utility_extract_mode") or "extract_masked_area"), "background": str(clean.get("mask_utility_background") or "Alpha"), "background_color": str(clean.get("mask_utility_background_color") or "#FFFFFF")}
        if "mask" in input_names or not input_names:
            inputs["mask"] = mask_refs[0]
        graph[str(next_id)] = {"class_type": node_class, "inputs": inputs}
        result_image_ref = [str(next_id), 0]
        next_id += 1
    elif operation == "crop_object":
        inputs = {}
        if "image" in input_names or not input_names:
            inputs["image"] = ["1", 0]
        if "mask" in input_names or not input_names:
            inputs["mask"] = mask_refs[0]
        if "padding" in input_names or not input_names:
            inputs["padding"] = int(clean.get("mask_utility_padding") or 0)
        graph[str(next_id)] = {"class_type": node_class, "inputs": inputs}
        result_image_ref = [str(next_id), 0]
        result_mask_ref = [str(next_id), 1]
        next_id += 1
    elif operation == "convert":
        inputs = {"image": ["1", 0], "mask_channel": str(clean.get("mask_utility_mask_channel") or "alpha")}
        graph[str(next_id)] = {"class_type": node_class, "inputs": inputs}
        result_mask_ref = [str(next_id), 1]
        next_id += 1
    elif operation == "color_to_mask":
        graph[str(next_id)] = {"class_type": node_class, "inputs": {"images": ["1", 0], "invert": bool(clean.get("mask_utility_invert")), "threshold": int(clean.get("mask_utility_threshold") or 10), "mask_color": str(clean.get("mask_utility_color") or "#FFFFFF")}}
        result_mask_ref = [str(next_id), 0]
        next_id += 1

    if result_image_ref is None:
        foreground_settings = dict(clean)
        foreground_settings["foreground_estimation"] = False
        result_image_ref, next_id, _ = _add_foreground_composite(graph, next_id, ["1", 0], result_mask_ref or ["1", 0], foreground_settings)
    _add_outputs(graph, next_id, result_image_ref, result_mask_ref or (mask_refs[0] if mask_refs else ["1", 0]), clean)
    notes = [f"Mask utility used the verified {operation} node ({node_class})."]
    if mask_names:
        notes.append(f"The utility consumed {len(mask_names)} uploaded mask image(s).")
    if operation == "combine":
        notes.append(f"Mask Combiner mode: {clean.get('mask_utility_mask_operation') or 'union'}.")
    if operation == "object_remove_lama":
        notes.append("Lama removed the selected masked object; the source image and selection mask remain non-destructive inputs.")
    if operation == "image_mask_resize":
        notes.append("Image + Mask Resize kept the image and supplied mask on the same resize/pad/crop contract.")
    if operation == "image_crop":
        notes.append("Image Crop prepared the source image; when a mask was supplied, the same crop was applied to keep it aligned.")
    return graph, clean, notes


def build_matting_workflow(
    source_image_name: str,
    settings: dict[str, Any] | str | None = None,
    mask_image_name: str = "",
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Build a live-verified BiRefNet or SDMatte high-resolution edge graph."""

    clean = normalize_settings(settings)
    image_name = str(source_image_name or "").strip()
    node_class = str(clean.get("matting_node_class") or "").strip()
    profile = str(clean.get("matting_profile") or "birefnet_hr").strip().lower()
    model = str(clean.get("matting_model") or "").strip()
    if not image_name:
        raise ValueError("Advanced matting needs a source image.")
    if node_class not in {"BiRefNetRMBG", "AILab_SDMatte"} or not model:
        raise ValueError("Advanced matting has no verified live Comfy node/model choice.")

    graph: dict[str, Any] = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image_name, "upload": "image"}},
    }
    next_id = 2
    input_names = set(clean.get("matting_input_names") or [])
    mask_name = str(mask_image_name or (clean.get("matting_mask_names") or [""])[0] or "").strip()
    mask_ref: list[Any] | None = None
    if node_class == "AILab_SDMatte":
        if mask_name:
            graph[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": mask_name, "upload": "image"}}
            graph[str(next_id + 1)] = {"class_type": "ImageToMask", "inputs": {"image": [str(next_id), 0], "channel": "red"}}
            mask_ref = [str(next_id + 1), 0]
            next_id += 2
        elif clean.get("matting_use_source_alpha"):
            mask_ref = ["1", 1]
        else:
            raise ValueError("SDMatte needs an uploaded trimap/mask or the source alpha mask.")

    inputs: dict[str, Any] = {"image": ["1", 0], "model": model}
    if node_class == "BiRefNetRMBG":
        optional = {
            "mask_blur": int(clean.get("matting_mask_blur") or 0),
            "mask_offset": int(clean.get("matting_mask_offset") or 0),
            "invert_output": bool(clean.get("matting_invert")),
            "refine_foreground": bool(clean.get("matting_refine_foreground") or clean.get("matting_edge_mode") == "foreground_estimation"),
            "background": str(clean.get("matting_background") or "Alpha"),
            "background_color": str(clean.get("matting_background_color") or "#222222"),
        }
        inputs.update({key: value for key, value in optional.items() if not input_names or key in input_names})
    else:
        required = {
            "device": str(clean.get("matting_device") or "Auto"),
            "process_res": int(clean.get("matting_process_res") or 1024),
        }
        inputs.update({key: value for key, value in required.items() if not input_names or key in input_names})
        optional = {
            "mask": mask_ref,
            "transparent_object": bool(clean.get("matting_transparent_object")),
            "mask_refine": bool(clean.get("matting_mask_refine")),
            "sensitivity": float(clean.get("matting_sensitivity") or 0.9),
            "mask_blur": int(clean.get("matting_mask_blur") or 0),
            "mask_offset": int(clean.get("matting_mask_offset") or 0),
            "invert_output": bool(clean.get("matting_invert")),
            "background": str(clean.get("matting_background") or "Alpha"),
            "background_color": str(clean.get("matting_background_color") or "#222222"),
        }
        inputs.update({key: value for key, value in optional.items() if value is not None and (not input_names or key in input_names)})
    graph[str(next_id)] = {"class_type": node_class, "inputs": inputs}
    result_image_ref = [str(next_id), 0]
    result_mask_ref = [str(next_id), 1]
    _add_outputs(graph, next_id + 1, result_image_ref, result_mask_ref, clean)
    notes = [
        f"Advanced matting used the verified {profile} profile ({node_class}) with live model choice {model}.",
        f"Edge mode: {clean.get('matting_edge_mode') or 'high_resolution_edges'}; process resolution: {int(clean.get('matting_process_res') or 0)}px.",
    ]
    if mask_name:
        notes.append("An uploaded trimap/mask was passed to the matting node.")
    elif clean.get("matting_use_source_alpha"):
        notes.append("The source image alpha channel was passed as the matting mask.")
    return graph, clean, notes


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
        f"Only selected subject masks were combined with {clean.get('sam_mask_operation') or 'union'}; unselected detected people were excluded from the foreground.",
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
    mask_image_name: str | list[str] = "",
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    clean = normalize_settings(settings)
    if clean.get("workflow_mode") == "refine_mask":
        return build_background_refinement_workflow(source_image_name, mask_image_name, clean)
    if clean.get("workflow_mode") == "segmentation_lab":
        return build_segmentation_lab_workflow(source_image_name, clean)
    if clean.get("workflow_mode") == "region_segmentation":
        return build_region_segmentation_workflow(source_image_name, clean)
    if clean.get("workflow_mode") == "mask_utility":
        mask_names = mask_image_name if isinstance(mask_image_name, list) else (clean.get("mask_utility_mask_names") or [])
        return build_mask_utility_workflow(source_image_name, clean, mask_names)
    if clean.get("workflow_mode") == "matting":
        mask_name = mask_image_name if isinstance(mask_image_name, str) else ((mask_image_name or [""])[0] if mask_image_name else "")
        return build_matting_workflow(source_image_name, clean, mask_name)
    if clean.get("workflow_mode") == "interactive_sam" and clean.get("resolved_engine") == "comfy_sam":
        return build_interactive_sam_comfy_workflow(source_image_name, clean)

    if clean.get("resolved_engine") == "comfy_rmbg":
        graph, foreground_ref, mask_ref, rmbg_notes = build_rmbg_node_graph(source_image_name, clean)
        refined_mask_ref, next_id, refinement_notes = _apply_mask_refinement(graph, 3, mask_ref, clean)
        if refinement_notes:
            foreground_ref, next_id, composite_mode = _add_foreground_composite(graph, next_id, ["1", 0], refined_mask_ref, clean)
        else:
            refined_mask_ref = mask_ref
            composite_mode = "rmbg_node_alpha"
        _add_outputs(graph, next_id, foreground_ref, refined_mask_ref, clean)
        notes = [
            *rmbg_notes,
            f"Foreground composition mode: {composite_mode}.",
            "The upstream ComfyUI-RMBG node supplied the foreground and alpha mask; BiRefNet nodes were not used.",
            *refinement_notes,
        ]
        if clean.get("save_mask"):
            notes.append("A grayscale alpha-mask PNG is saved as a second output.")
        return graph, clean, notes

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
