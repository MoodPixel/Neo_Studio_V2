from __future__ import annotations

import json
from typing import Any

from .constants import (
    COMMERCIAL_PROVIDER_IDS,
    DEFAULTS,
    EXTENSION_ID,
    EXTENSION_VERSION,
    MAX_SAM_SUBJECTS,
    NATIVE_MODEL_IDS,
    NATIVE_PRESET_MODELS,
    PRESET_MODEL_CANDIDATES,
    SAM_EXECUTION_MODES,
    SAM_MODEL_VARIANTS,
    SAM_REFINEMENT_MODEL_IDS,
)
from .segmentation_lab import normalize_segmentation_lab
from .region_segmentation import normalize_region_segmentation
from .mask_utilities import normalize_mask_utility
from .matting import normalize_matting

VALID_PRESETS = set(PRESET_MODEL_CANDIDATES)
VALID_DEVICES = {"AUTO", "CPU"}
VALID_DTYPES = {"float32", "float16"}
VALID_UPSCALE_METHODS = {"bilinear", "nearest", "nearest-exact", "bicubic"}
VALID_WORKFLOW_MODES = {"segment", "refine_mask", "interactive_sam", "segmentation_lab", "region_segmentation", "mask_utility", "matting"}
VALID_PREVIEW_BACKGROUNDS = {"checkerboard", "white", "black"}
VALID_ENGINES = {"smart", "comfy_birefnet", "comfy_rmbg", "comfy_segmentation", "comfy_region_segmentation", "comfy_mask_utility", "comfy_matting", "native_rembg", "native_sam", "commercial_api"}
VALID_FALLBACK_POLICIES = {"never", "on_unavailable", "on_unavailable_or_queue_failure"}
VALID_NATIVE_PROVIDERS = {"AUTO", "CPU", "CUDA"}
VALID_SAM_REFINE_MODES = {"birefnet_gate", "sam_only"}
VALID_SAM_EXECUTION = set(SAM_EXECUTION_MODES)
VALID_SAM_DETECTOR_TYPES = {"bbox", "segm"}
VALID_COMMERCIAL_OUTPUT_SIZES = {"auto", "preview", "small", "regular", "medium", "hd", "4k", "50mp"}
VALID_COMMERCIAL_SUBJECT_TYPES = {"auto", "person", "product", "car"}
VALID_COMMERCIAL_TRANSPARENCY_HANDLING = {"return_input_if_non_opaque", "discard_alpha_layer"}
MAX_SAM_PROMPTS = 64
MAX_SAM_RECTANGLES = 8


class PayloadContractError(ValueError):
    pass


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _int(value: Any, default: int, low: int, high: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = int(default)
    return max(low, min(high, parsed))


def _float(value: Any, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(low, min(high, parsed))


def _sam_prompt_source(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise PayloadContractError(f"Invalid SAM prompt JSON: {exc.msg}") from exc
    return value if isinstance(value, list) else []


def normalize_sam_prompts(value: Any) -> list[dict[str, Any]]:
    prompts: list[dict[str, Any]] = []
    rectangle_count = 0
    for item in _sam_prompt_source(value):
        if not isinstance(item, dict) or len(prompts) >= MAX_SAM_PROMPTS:
            continue
        prompt_type = str(item.get("type") or "point").strip().lower()
        if prompt_type == "point":
            prompts.append({
                "type": "point",
                "label": 1 if _int(item.get("label"), 1, 0, 1) else 0,
                "x": _float(item.get("x"), 0.5, 0.0, 1.0),
                "y": _float(item.get("y"), 0.5, 0.0, 1.0),
            })
        elif prompt_type in {"rectangle", "box"} and rectangle_count < MAX_SAM_RECTANGLES:
            x1 = _float(item.get("x1"), 0.0, 0.0, 1.0)
            y1 = _float(item.get("y1"), 0.0, 0.0, 1.0)
            x2 = _float(item.get("x2"), 1.0, 0.0, 1.0)
            y2 = _float(item.get("y2"), 1.0, 0.0, 1.0)
            if abs(x2 - x1) < 0.002 or abs(y2 - y1) < 0.002:
                continue
            prompts.append({
                "type": "rectangle",
                "x1": min(x1, x2),
                "y1": min(y1, y2),
                "x2": max(x1, x2),
                "y2": max(y1, y2),
            })
            rectangle_count += 1
    return prompts



def _point_list(value: Any, *, label: int) -> list[dict[str, Any]]:
    rows = value if isinstance(value, list) else []
    result: list[dict[str, Any]] = []
    for item in rows[:32]:
        if not isinstance(item, dict):
            continue
        result.append({
            "type": "point",
            "label": label,
            "x": _float(item.get("x"), 0.5, 0.0, 1.0),
            "y": _float(item.get("y"), 0.5, 0.0, 1.0),
        })
    return result


def normalize_sam_subjects(value: Any, legacy_prompts: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value or "[]")
        except json.JSONDecodeError as exc:
            raise PayloadContractError(f"Invalid SAM subject JSON: {exc.msg}") from exc
    rows = value if isinstance(value, list) else []
    subjects: list[dict[str, Any]] = []
    for index, item in enumerate(rows[:MAX_SAM_SUBJECTS], start=1):
        if not isinstance(item, dict):
            continue
        bbox_value = item.get("bbox")
        if isinstance(bbox_value, dict) and bbox_value:
            bbox_raw = bbox_value
        elif any(key in item for key in ("x1", "y1", "x2", "y2")):
            bbox_raw = item
        else:
            bbox_raw = {}
        bbox: dict[str, float] = {}
        if bbox_raw:
            x1 = _float(bbox_raw.get("x1"), 0.0, 0.0, 1.0)
            y1 = _float(bbox_raw.get("y1"), 0.0, 0.0, 1.0)
            x2 = _float(bbox_raw.get("x2"), 1.0, 0.0, 1.0)
            y2 = _float(bbox_raw.get("y2"), 1.0, 0.0, 1.0)
            if abs(x2 - x1) >= 0.002 and abs(y2 - y1) >= 0.002:
                bbox = {"x1": min(x1, x2), "y1": min(y1, y2), "x2": max(x1, x2), "y2": max(y1, y2)}
        keep_points = _point_list(item.get("keep_points"), label=1)
        remove_points = _point_list(item.get("remove_points"), label=0)
        if not bbox and not keep_points:
            continue
        subject_id = str(item.get("id") or f"subject_{index}").strip() or f"subject_{index}"
        subjects.append({
            "id": subject_id[:96],
            "label": str(item.get("label") or f"Subject {index}").strip()[:160],
            "selected": _bool(item.get("selected"), True),
            "source": str(item.get("source") or "manual").strip()[:64],
            "confidence": _float(item.get("confidence"), 0.0, 0.0, 1.0),
            "bbox": bbox,
            "keep_points": keep_points,
            "remove_points": remove_points,
        })
    if subjects:
        return subjects
    legacy = list(legacy_prompts or [])
    if not legacy:
        return []
    boxes = [item for item in legacy if item.get("type") == "rectangle"]
    points = [item for item in legacy if item.get("type") == "point"]
    if boxes:
        for index, box in enumerate(boxes[:MAX_SAM_SUBJECTS], start=1):
            subjects.append({
                "id": f"legacy_subject_{index}",
                "label": f"Subject {index}",
                "selected": True,
                "source": "legacy_prompt",
                "confidence": 0.0,
                "bbox": {k: float(box[k]) for k in ("x1", "y1", "x2", "y2")},
                "keep_points": points if index == 1 else [],
                "remove_points": [],
            })
        return subjects
    return [{
        "id": "legacy_subject_1",
        "label": "Subject 1",
        "selected": True,
        "source": "legacy_prompt",
        "confidence": 0.0,
        "bbox": {},
        "keep_points": [item for item in points if int(item.get("label") or 0) == 1],
        "remove_points": [item for item in points if int(item.get("label") or 0) == 0],
    }]


def normalize_settings(raw: dict[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise PayloadContractError(f"Invalid Background Removal settings JSON: {exc.msg}") from exc
    source = raw if isinstance(raw, dict) else {}
    preset = str(source.get("preset") or source.get("background_removal_preset") or DEFAULTS["preset"]).strip().lower()
    if preset not in VALID_PRESETS:
        preset = DEFAULTS["preset"]
    workflow_mode = str(source.get("workflow_mode") or DEFAULTS["workflow_mode"]).strip().lower()
    if workflow_mode not in VALID_WORKFLOW_MODES:
        workflow_mode = DEFAULTS["workflow_mode"]
    device = str(source.get("device") or DEFAULTS["device"]).strip().upper()
    if device not in VALID_DEVICES:
        device = DEFAULTS["device"]
    dtype = str(source.get("dtype") or DEFAULTS["dtype"]).strip().lower()
    if dtype not in VALID_DTYPES:
        dtype = DEFAULTS["dtype"]
    upscale_method = str(source.get("upscale_method") or DEFAULTS["upscale_method"]).strip().lower()
    if upscale_method not in VALID_UPSCALE_METHODS:
        upscale_method = DEFAULTS["upscale_method"]
    preview_background = str(source.get("preview_background") or DEFAULTS["preview_background"]).strip().lower()
    if preview_background not in VALID_PREVIEW_BACKGROUNDS:
        preview_background = DEFAULTS["preview_background"]
    engine = str(source.get("engine") or DEFAULTS["engine"]).strip().lower()
    if engine not in VALID_ENGINES:
        engine = DEFAULTS["engine"]
    fallback_policy = str(source.get("fallback_policy") or DEFAULTS["fallback_policy"]).strip().lower()
    if fallback_policy not in VALID_FALLBACK_POLICIES:
        fallback_policy = DEFAULTS["fallback_policy"]
    native_provider = str(source.get("native_provider") or DEFAULTS["native_provider"]).strip().upper()
    if native_provider not in VALID_NATIVE_PROVIDERS:
        native_provider = DEFAULTS["native_provider"]
    native_model = str(source.get("native_model") or NATIVE_PRESET_MODELS.get(preset) or DEFAULTS["native_model"]).strip()
    if native_model not in NATIVE_MODEL_IDS:
        native_model = NATIVE_PRESET_MODELS.get(preset) or DEFAULTS["native_model"]
    sam_model_variant = str(source.get("sam_model_variant") or DEFAULTS["sam_model_variant"]).strip()
    if sam_model_variant not in SAM_MODEL_VARIANTS:
        sam_model_variant = DEFAULTS["sam_model_variant"]
    sam_refine_mode = str(source.get("sam_refine_mode") or DEFAULTS["sam_refine_mode"]).strip().lower()
    if sam_refine_mode not in VALID_SAM_REFINE_MODES:
        sam_refine_mode = DEFAULTS["sam_refine_mode"]
    sam_refine_model = str(source.get("sam_refine_model") or DEFAULTS["sam_refine_model"]).strip()
    if sam_refine_model not in SAM_REFINEMENT_MODEL_IDS:
        sam_refine_model = DEFAULTS["sam_refine_model"]
    sam_prompts = normalize_sam_prompts(source.get("sam_prompts"))
    sam_subjects = normalize_sam_subjects(source.get("sam_subjects"), sam_prompts)
    sam_execution = str(source.get("sam_execution") or DEFAULTS["sam_execution"]).strip().lower()
    if sam_execution not in VALID_SAM_EXECUTION:
        sam_execution = DEFAULTS["sam_execution"]
    sam_detector_type = str(source.get("sam_detector_type") or DEFAULTS["sam_detector_type"]).strip().lower()
    if sam_detector_type not in VALID_SAM_DETECTOR_TYPES:
        sam_detector_type = DEFAULTS["sam_detector_type"]
    sam_mask_operation = str(source.get("sam_mask_operation") or DEFAULTS["sam_mask_operation"]).strip().lower()
    if sam_mask_operation not in {"union", "intersection", "subtract"}:
        sam_mask_operation = DEFAULTS["sam_mask_operation"]
    segmentation_lab = normalize_segmentation_lab(source)
    region_segmentation = normalize_region_segmentation(source)
    mask_utility = normalize_mask_utility(source)
    matting = normalize_matting(source)
    commercial_output_size = str(source.get("commercial_output_size") or DEFAULTS["commercial_output_size"]).strip().lower()
    if commercial_output_size not in VALID_COMMERCIAL_OUTPUT_SIZES:
        commercial_output_size = DEFAULTS["commercial_output_size"]
    commercial_subject_type = str(source.get("commercial_subject_type") or DEFAULTS["commercial_subject_type"]).strip().lower()
    if commercial_subject_type not in VALID_COMMERCIAL_SUBJECT_TYPES:
        commercial_subject_type = DEFAULTS["commercial_subject_type"]
    commercial_transparency_handling = str(source.get("commercial_transparency_handling") or DEFAULTS["commercial_transparency_handling"]).strip().lower()
    if commercial_transparency_handling not in VALID_COMMERCIAL_TRANSPARENCY_HANDLING:
        commercial_transparency_handling = DEFAULTS["commercial_transparency_handling"]
    commercial_profile_id = str(source.get("commercial_profile_id") or "").strip()
    if engine == "commercial_api":
        fallback_policy = "never"
        workflow_mode = "segment"
    segmentation_lab = normalize_segmentation_lab(source)
    if workflow_mode == "segmentation_lab":
        segmentation_lab["enabled"] = True
        engine = "comfy_segmentation"
    elif engine == "comfy_segmentation":
        workflow_mode = "segmentation_lab"
        segmentation_lab["enabled"] = True
    if workflow_mode == "region_segmentation":
        region_segmentation["enabled"] = True
        engine = "comfy_region_segmentation"
    elif engine == "comfy_region_segmentation":
        workflow_mode = "region_segmentation"
        region_segmentation["enabled"] = True
    if workflow_mode == "mask_utility":
        mask_utility["enabled"] = True
        engine = "comfy_mask_utility"
    elif engine == "comfy_mask_utility":
        workflow_mode = "mask_utility"
        mask_utility["enabled"] = True
    if workflow_mode == "matting":
        matting["enabled"] = True
        engine = "comfy_matting"
    elif engine == "comfy_matting":
        workflow_mode = "matting"
        matting["enabled"] = True
    return {
        "enabled": _bool(source.get("enabled"), True),
        "workflow_mode": workflow_mode,
        "engine": engine,
        "fallback_policy": fallback_policy,
        "native_model": native_model,
        "native_provider": native_provider,
        "native_alpha_matting": _bool(source.get("native_alpha_matting"), DEFAULTS["native_alpha_matting"]),
        "native_post_process_mask": _bool(source.get("native_post_process_mask"), DEFAULTS["native_post_process_mask"]),
        "native_foreground_threshold": _int(source.get("native_foreground_threshold"), DEFAULTS["native_foreground_threshold"], 0, 255),
        "native_background_threshold": _int(source.get("native_background_threshold"), DEFAULTS["native_background_threshold"], 0, 255),
        "native_erode_size": _int(source.get("native_erode_size"), DEFAULTS["native_erode_size"], 0, 255),
        "resolved_engine": str(source.get("resolved_engine") or "").strip().lower(),
        "resolved_model": str(source.get("resolved_model") or "").strip(),
        "fallback_used": _bool(source.get("fallback_used"), False),
        "fallback_reason": str(source.get("fallback_reason") or "").strip(),
        "native_output_root": str(source.get("native_output_root") or "").strip(),
        "source_width": _int(source.get("source_width"), 0, 0, 65535),
        "source_height": _int(source.get("source_height"), 0, 0, 65535),
        "preset": preset,
        "model": str(source.get("model") or source.get("birefnet_model") or "").strip().replace("\\", "/"),
        "rmbg_model": str(source.get("rmbg_model") or "").strip().replace("\\", "/"),
        "rmbg_node_class": str(source.get("rmbg_node_class") or "").strip(),
        "rmbg_input_map": {str(key): str(value) for key, value in (source.get("rmbg_input_map") or {}).items() if str(key).strip() and str(value).strip()} if isinstance(source.get("rmbg_input_map"), dict) else {},
        "rmbg_sensitivity": _float(source.get("rmbg_sensitivity"), DEFAULTS["rmbg_sensitivity"], 0.0, 1.0),
        "rmbg_mask_blur": _int(source.get("rmbg_mask_blur"), DEFAULTS["rmbg_mask_blur"], 0, 64),
        "rmbg_mask_offset": _int(source.get("rmbg_mask_offset"), DEFAULTS["rmbg_mask_offset"], -64, 64),
        "rmbg_invert_output": _bool(source.get("rmbg_invert_output"), DEFAULTS["rmbg_invert_output"]),
        "rmbg_refine_foreground": _bool(source.get("rmbg_refine_foreground"), DEFAULTS["rmbg_refine_foreground"]),
        "rmbg_background": str(source.get("rmbg_background") or DEFAULTS["rmbg_background"]).strip() if str(source.get("rmbg_background") or DEFAULTS["rmbg_background"]).strip() in {"Alpha", "Color"} else DEFAULTS["rmbg_background"],
        "rmbg_background_color": str(source.get("rmbg_background_color") or DEFAULTS["rmbg_background_color"]).strip()[:32],
        "device": device,
        "dtype": dtype,
        "use_weight": _bool(source.get("use_weight"), False),
        "width": _int(source.get("width"), DEFAULTS["width"], 256, 4096),
        "height": _int(source.get("height"), DEFAULTS["height"], 256, 4096),
        "upscale_method": upscale_method,
        "mask_threshold": _float(source.get("mask_threshold"), DEFAULTS["mask_threshold"], 0.0, 1.0),
        "mask_expand": _int(source.get("mask_expand"), DEFAULTS["mask_expand"], -128, 128),
        "mask_feather": _int(source.get("mask_feather"), DEFAULTS["mask_feather"], 0, 128),
        "foreground_estimation": _bool(source.get("foreground_estimation"), DEFAULTS["foreground_estimation"]),
        "blur_size": _int(source.get("blur_size"), DEFAULTS["blur_size"], 1, 255),
        "blur_size_two": _int(source.get("blur_size_two"), DEFAULTS["blur_size_two"], 1, 255),
        "save_mask": _bool(source.get("save_mask"), True),
        "preview_image": _bool(source.get("preview_image"), False),
        "preview_background": preview_background,
        "manual_mask": _bool(source.get("manual_mask"), workflow_mode in {"refine_mask", "interactive_sam", "segmentation_lab", "region_segmentation"}),
        "mask_source": str(source.get("mask_source") or ("interactive_sam" if workflow_mode == "interactive_sam" else ("segmentation_lab" if workflow_mode == "segmentation_lab" else ("region_segmentation" if workflow_mode == "region_segmentation" else ("mask_utility" if workflow_mode == "mask_utility" else ("matting" if workflow_mode == "matting" else ("manual_review" if workflow_mode == "refine_mask" else DEFAULTS["mask_source"]))))))).strip(),
        "source_mode": str(source.get("source_mode") or DEFAULTS["source_mode"]).strip(),
        "commercial_profile_id": commercial_profile_id,
        "commercial_upload_consent": _bool(source.get("commercial_upload_consent"), False),
        "commercial_output_size": commercial_output_size,
        "commercial_subject_type": commercial_subject_type,
        "commercial_preserve_semitransparency": _bool(source.get("commercial_preserve_semitransparency"), DEFAULTS["commercial_preserve_semitransparency"]),
        "commercial_transparency_handling": commercial_transparency_handling,
        "parent_result_id": str(source.get("parent_result_id") or "").strip(),
        "parent_file_id": str(source.get("parent_file_id") or "").strip(),
        "sam_prompts": sam_prompts,
        "sam_subjects": sam_subjects,
        "sam_execution": sam_execution,
        "sam_comfy_model": str(source.get("sam_comfy_model") or "").strip().replace("\\", "/"),
        "sam_detector_model": str(source.get("sam_detector_model") or "").strip().replace("\\", "/"),
        "sam_detector_type": sam_detector_type,
        "sam_mask_operation": sam_mask_operation,
        "sam_detection_confidence": _float(source.get("sam_detection_confidence"), DEFAULTS["sam_detection_confidence"], 0.01, 0.99),
        "sam_node_map": dict(source.get("sam_node_map") or {}) if isinstance(source.get("sam_node_map"), dict) else {},
        "sam_shared_refine_enabled": _bool(source.get("sam_shared_refine_enabled"), False),
        "sam_refine_fallback_used": _bool(source.get("sam_refine_fallback_used"), False),
        "sam_refine_fallback_reason": str(source.get("sam_refine_fallback_reason") or "").strip(),
        "sam_model_variant": sam_model_variant,
        "sam_quantized": _bool(source.get("sam_quantized"), DEFAULTS["sam_quantized"]),
        "sam_refine_mode": sam_refine_mode,
        "sam_refine_model": sam_refine_model,
        "sam_refine_fallback": _bool(source.get("sam_refine_fallback"), DEFAULTS["sam_refine_fallback"]),
        "sam_gate_expand": _int(source.get("sam_gate_expand"), DEFAULTS["sam_gate_expand"], 0, 128),
        "sam_gate_feather": _int(source.get("sam_gate_feather"), DEFAULTS["sam_gate_feather"], 0, 128),
        "segmentation_lab_enabled": bool(segmentation_lab.get("enabled")),
        "segmentation_adapter": segmentation_lab.get("adapter") or "auto",
        "segmentation_node_class": str(source.get("segmentation_node_class") or "").strip(),
        "segmentation_mask_operation": segmentation_lab.get("mask_operation") or "union",
        "segmentation_lab_prompts": segmentation_lab.get("prompts") or [],
        "segmentation_threshold": segmentation_lab.get("threshold", 0.35),
        "segmentation_confidence_threshold": segmentation_lab.get("confidence_threshold", 0.5),
        "segmentation_max_segments": segmentation_lab.get("max_segments", 0),
        "segmentation_sam_model": segmentation_lab.get("sam_model") or "",
        "segmentation_sam2_model": segmentation_lab.get("sam2_model") or "",
        "segmentation_dino_model": segmentation_lab.get("dino_model") or "",
        "segmentation_device": segmentation_lab.get("device") or "Auto",
        "segmentation_segment_pick": segmentation_lab.get("segment_pick", 0),
        "region_segmentation_enabled": bool(region_segmentation.get("enabled")),
        "region_segmentation_adapter": region_segmentation.get("adapter") or "auto",
        "region_segmentation_node_class": region_segmentation.get("node_class") or "",
        "region_segmentation_mask_operation": region_segmentation.get("mask_operation") or "union",
        "region_segmentation_targets": region_segmentation.get("targets") or [],
        "mask_utility_enabled": bool(mask_utility.get("enabled")),
        "mask_utility_operation": mask_utility.get("operation") or "enhance",
        "mask_utility_node_class": mask_utility.get("node_class") or "",
        "mask_utility_input_names": mask_utility.get("input_names") or [],
        "mask_utility_mask_names": mask_utility.get("mask_names") or [],
        "mask_utility_mask_operation": mask_utility.get("mask_operation") or "union",
        "mask_utility_mask_channel": mask_utility.get("mask_channel") or "red",
        "mask_utility_extract_mode": mask_utility.get("extract_mode") or "extract_masked_area",
        "mask_utility_background": mask_utility.get("background") or "Alpha",
        "mask_utility_background_color": mask_utility.get("background_color") or "#FFFFFF",
        "mask_utility_color": mask_utility.get("color") or "#FFFFFF",
        "mask_utility_threshold": mask_utility.get("threshold", 10),
        "mask_utility_sensitivity": mask_utility.get("sensitivity", 1.0),
        "mask_utility_mask_blur": mask_utility.get("mask_blur", 0),
        "mask_utility_mask_offset": mask_utility.get("mask_offset", 0),
        "mask_utility_smooth": mask_utility.get("smooth", 0.0),
        "mask_utility_fill_holes": bool(mask_utility.get("fill_holes")),
        "mask_utility_invert": bool(mask_utility.get("invert")),
        "mask_utility_padding": mask_utility.get("padding", 0),
        "mask_utility_overlay_opacity": mask_utility.get("overlay_opacity", 0.5),
        "mask_utility_overlay_color": mask_utility.get("overlay_color") or "#0000FF",
        "mask_utility_lama_removal_strength": mask_utility.get("lama_removal_strength", 230),
        "mask_utility_lama_edge_smoothness": mask_utility.get("lama_edge_smoothness", 8),
        "mask_utility_resize_width": mask_utility.get("resize_width", 0),
        "mask_utility_resize_height": mask_utility.get("resize_height", 0),
        "mask_utility_resize_megapixels": mask_utility.get("resize_megapixels", 0),
        "mask_utility_resize_scale_by": mask_utility.get("resize_scale_by", 1.0),
        "mask_utility_resize_mode": mask_utility.get("resize_mode") or "longest_side",
        "mask_utility_resize_value": mask_utility.get("resize_value", 0),
        "mask_utility_resize_method": mask_utility.get("resize_method") or "lanczos",
        "mask_utility_resize_device": mask_utility.get("resize_device") or "cpu",
        "mask_utility_resize_divisible_by": mask_utility.get("resize_divisible_by", 2),
        "mask_utility_resize_output_mode": mask_utility.get("resize_output_mode") or "stretch",
        "mask_utility_resize_crop_position": mask_utility.get("resize_crop_position") or "center",
        "mask_utility_resize_pad_color": mask_utility.get("resize_pad_color") or "#FFFFFF",
        "mask_utility_crop_width": mask_utility.get("crop_width", 1024),
        "mask_utility_crop_height": mask_utility.get("crop_height", 1024),
        "mask_utility_crop_x_offset": mask_utility.get("crop_x_offset", 0),
        "mask_utility_crop_y_offset": mask_utility.get("crop_y_offset", 0),
        "mask_utility_crop_position": mask_utility.get("crop_position") or "center",
        "mask_utility_crop_split": bool(mask_utility.get("crop_split")),
        "matting_enabled": bool(matting.get("enabled")),
        "matting_profile": matting.get("profile") or "birefnet_hr",
        "matting_model": matting.get("model") or "",
        "matting_node_class": matting.get("node_class") or "",
        "matting_input_names": matting.get("input_names") or [],
        "matting_model_choices": matting.get("model_choices") or [],
        "matting_process_res": matting.get("process_res", 2048),
        "matting_device": matting.get("device") or "Auto",
        "matting_mask_refine": bool(matting.get("mask_refine")),
        "matting_sensitivity": matting.get("sensitivity", 0.9),
        "matting_transparent_object": bool(matting.get("transparent_object")),
        "matting_use_source_alpha": bool(matting.get("use_source_alpha")),
        "matting_edge_mode": matting.get("edge_mode") or "high_resolution_edges",
        "matting_mask_blur": matting.get("mask_blur", 0),
        "matting_mask_offset": matting.get("mask_offset", 0),
        "matting_invert": bool(matting.get("invert")),
        "matting_background": matting.get("background") or "Alpha",
        "matting_background_color": matting.get("background_color") or "#222222",
        "matting_refine_foreground": bool(matting.get("refine_foreground")),
        "matting_mask_names": matting.get("mask_names") or [],
    }


def validate_payload_settings(
    settings: dict[str, Any],
    *,
    require_source: bool = False,
    source_images: list[str] | None = None,
    mask_images: list[str] | None = None,
) -> dict[str, Any]:
    clean = normalize_settings(settings)
    errors: list[str] = []
    if not clean.get("enabled"):
        errors.append("Background Removal is disabled.")
    if require_source and not [item for item in (source_images or []) if str(item or "").strip()]:
        errors.append("Pick a source image for Background Removal.")
    if clean.get("workflow_mode") == "refine_mask" and not [item for item in (mask_images or []) if str(item or "").strip()]:
        errors.append("Mask Review refinement needs a reviewed mask PNG.")
    if clean.get("workflow_mode") == "interactive_sam":
        subjects = [item for item in (clean.get("sam_subjects") or []) if item.get("selected", True)]
        prompts = list(clean.get("sam_prompts") or [])
        positive_subjects = [item for item in subjects if item.get("bbox") or item.get("keep_points")]
        has_positive = bool(positive_subjects) or any(item.get("type") == "rectangle" or (item.get("type") == "point" and int(item.get("label") or 0) == 1) for item in prompts)
        if not has_positive:
            errors.append("Interactive SAM needs at least one selected subject box or positive Keep point.")
        if clean.get("sam_execution") == "comfy_impact":
            if not subjects:
                errors.append("Comfy Impact SAM needs at least one selected subject box.")
            elif any(not item.get("bbox") for item in subjects):
                errors.append("Every Comfy Impact SAM subject needs a selection box.")
            elif any(item.get("keep_points") or item.get("remove_points") for item in subjects):
                errors.append("Per-subject correction points require Neo Native ONNX SAM.")
    if clean.get("workflow_mode") == "segmentation_lab":
        prompts = [item for item in (clean.get("segmentation_lab_prompts") or []) if item.get("enabled", True)]
        if not prompts:
            errors.append("Segmentation Lab needs at least one natural-language object prompt.")
        if clean.get("segmentation_mask_operation") not in {"union", "intersection", "subtract"}:
            errors.append("Segmentation Lab mask operation must be union, intersection, or subtract.")
        if clean.get("engine") != "comfy_segmentation":
            errors.append("Segmentation Lab requires its verified Comfy prompt-segmentation route.")
    if clean.get("workflow_mode") == "region_segmentation":
        targets = [item for item in (clean.get("region_segmentation_targets") or []) if item.get("enabled", True)]
        if not targets:
            errors.append("Face, clothes, and fashion segmentation needs at least one enabled region target.")
        if clean.get("region_segmentation_mask_operation") not in {"union", "intersection", "subtract"}:
            errors.append("Region segmentation mask operation must be union, intersection, or subtract.")
        if clean.get("engine") != "comfy_region_segmentation":
            errors.append("Face, clothes, and fashion segmentation requires its verified Comfy region route.")
    if clean.get("workflow_mode") == "mask_utility":
        if clean.get("mask_utility_operation") not in {"enhance", "combine", "extract", "crop_object", "convert", "color_to_mask", "mask_overlay", "object_remove_lama", "image_mask_resize", "image_crop"}:
            errors.append("Mask utility operation is not supported.")
        if clean.get("mask_utility_mask_operation") not in {"union", "intersection", "difference"}:
            errors.append("Mask utility combine mode must be union, intersection, or difference.")
        if clean.get("engine") != "comfy_mask_utility":
            errors.append("Mask utilities require their verified Comfy utility route.")
        if clean.get("mask_utility_operation") in {"enhance", "combine", "extract", "crop_object", "mask_overlay", "object_remove_lama"} and not [item for item in (mask_images or []) if str(item or "").strip()]:
            errors.append("This mask utility needs at least one uploaded mask image.")
        if clean.get("mask_utility_operation") == "combine" and len([item for item in (mask_images or []) if str(item or "").strip()]) > 4:
            errors.append("Mask Combiner accepts at most four mask images.")
    if clean.get("workflow_mode") == "matting":
        if clean.get("engine") != "comfy_matting":
            errors.append("Advanced matting requires the strict Comfy matting engine.")
        if clean.get("matting_profile") in {"sdmatte", "sdmatte_plus"} and not clean.get("matting_use_source_alpha") and not [item for item in (mask_images or []) if str(item or "").strip()]:
            errors.append("SDMatte matting needs an uploaded trimap/mask, or enable source alpha as the mask.")
        if clean.get("matting_process_res", 0) > 2560:
            errors.append("Matting process resolution cannot exceed 2560.")
    if clean.get("engine") == "commercial_api":
        if not clean.get("commercial_profile_id"):
            errors.append("Choose a commercial background-removal provider profile.")
        if not clean.get("commercial_upload_consent"):
            errors.append("Confirm the per-run external upload and credit consent before using a commercial provider.")
        if clean.get("workflow_mode") != "segment":
            errors.append("Commercial background-removal providers support the standard removal run only.")
    return {"ok": not errors, "errors": errors, "settings": clean}


def build_payload_block(
    settings: dict[str, Any],
    *,
    enabled: bool,
    route: dict[str, Any],
    source_images: list[dict[str, Any]],
    mask_images: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    clean = normalize_settings(settings)
    return {
        "enabled": bool(enabled),
        "version": EXTENSION_VERSION,
        "inputs": {
            "source_mode": clean.get("source_mode") or "selected_result_or_upload",
            "workflow_mode": clean.get("workflow_mode") or "segment",
            "mask_source": clean.get("mask_source") or "birefnet",
        },
        "params": clean if enabled else {},
        "assets": {
            "source_images": source_images if enabled else [],
            "mask_images": list(mask_images or []) if enabled else [],
        },
        "metadata": {
            "extension_id": EXTENSION_ID,
            "extension_type": "built_in",
            "workspace_app": "finish",
            "route": route,
            "output_format": "png_rgba",
            "mask_output": bool(clean.get("save_mask")),
            "refinement_only": clean.get("workflow_mode") == "refine_mask",
            "interactive_selection": clean.get("workflow_mode") == "interactive_sam",
            "segmentation_lab": clean.get("workflow_mode") == "segmentation_lab",
            "region_segmentation": clean.get("workflow_mode") == "region_segmentation",
            "mask_utilities": clean.get("workflow_mode") == "mask_utility",
            "matting": clean.get("workflow_mode") == "matting",
            "non_destructive": True,
        },
    }
