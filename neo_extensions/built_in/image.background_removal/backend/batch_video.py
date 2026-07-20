"""Phase RMBG-7 batch-image and frame-wise video segmentation contracts.

The live ComfyUI ``/object_info`` response remains authoritative.  This module
only builds graphs when the installed node classes expose the exact inputs that
the graph uses.  Video support is deliberately frame-wise: it does not claim
temporal propagation, optical-flow tracking, or identity persistence.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any

from .public_hygiene import portable_model_identifier


SCHEMA_ID = "neo.image.background_removal.batch_video.v1"
SCHEMA_VERSION = 1
MAX_BATCH_IMAGES = 32
MAX_VIDEO_FRAMES = 1200
MAX_PROMPT_LENGTH = 512
DEFAULT_FPS = 24.0
VALID_ROUTES = {"batch_images", "video_framewise"}
VALID_BATCH_EXECUTION = {"comfy_batch"}

_VIDEO_LOAD_CANDIDATES = (
    "VHS_LoadVideo",
    "VHS_LoadVideoPath",
    "LoadVideo",
    "LoadVideoPath",
    "LoadVideoUpload",
)
_VIDEO_COMBINE_CANDIDATES = ("VHS_VideoCombine", "VideoCombine")
_IMAGE_BATCH_CANDIDATES = ("ImageBatch",)
_MASK_TO_IMAGE_CANDIDATES = ("MaskToImage",)
_BATCH_SEGMENT_CANDIDATES = (
    "SegmentV2",
    "Segment_V2",
    "Segment_v2",
    "Segment_v1",
)
_VIDEO_FILE_SUFFIXES = {".mp4", ".mov", ".webm", ".mkv", ".avi", ".gif"}


def _node_spec(object_info: dict[str, Any] | None, candidates: tuple[str, ...]) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(object_info, dict):
        return None
    folded = {str(key).casefold(): (str(key), value) for key, value in object_info.items() if isinstance(value, dict)}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return None


def _input_groups(spec: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    block = spec.get("input") if isinstance(spec, dict) else {}
    block = block if isinstance(block, dict) else {}
    groups = []
    for section in ("required", "optional", "hidden"):
        value = block.get(section)
        groups.append(value if isinstance(value, dict) else {})
    return groups[0], groups[1], groups[2]


def _input_names(spec: dict[str, Any] | None) -> set[str]:
    return set().union(*[set(group) for group in _input_groups(spec)])


def _choices(spec: dict[str, Any] | None, name: str, role: str = "") -> list[str]:
    required, optional, _hidden = _input_groups(spec)
    value = required.get(name, optional.get(name))
    if not isinstance(value, (list, tuple)) or not value:
        return []
    values = value[0] if isinstance(value[0], (list, tuple)) else value
    if not isinstance(values, (list, tuple)):
        return []
    result: list[str] = []
    for item in values:
        clean = portable_model_identifier(item, role) if role else str(item or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def _first_input(names: set[str], candidates: tuple[str, ...]) -> str:
    for candidate in candidates:
        if candidate in names:
            return candidate
    return ""


def _safe_comfy_name(value: Any, *, suffixes: set[str] | None = None) -> str:
    """Accept an upload name, never a server-local or user-local path."""

    raw = str(value or "").strip().replace("\\", "/")
    if not raw or "\x00" in raw or raw.startswith(("/", "//", "~/", "file:", "http:", "https:")):
        return ""
    if len(raw) >= 3 and raw[1] == ":" and raw[2] == "/":
        return ""
    parts = PurePosixPath(raw).parts
    if ".." in parts:
        return ""
    if suffixes and PurePosixPath(raw).suffix.casefold() not in suffixes:
        return ""
    return raw


def normalize_batch_video_settings(source: dict[str, Any] | None) -> dict[str, Any]:
    raw = source if isinstance(source, dict) else {}
    route = str(raw.get("rmbg_batch_route") or raw.get("route") or "batch_images").strip().lower()
    if route not in VALID_ROUTES:
        route = "batch_images"
    execution = str(raw.get("rmbg_batch_execution") or raw.get("execution") or "comfy_batch").strip().lower()
    if execution not in VALID_BATCH_EXECUTION:
        execution = "comfy_batch"
    try:
        max_frames = max(1, min(MAX_VIDEO_FRAMES, int(raw.get("rmbg_video_max_frames", MAX_VIDEO_FRAMES))))
    except (TypeError, ValueError):
        max_frames = MAX_VIDEO_FRAMES
    try:
        fps = max(1.0, min(60.0, float(raw.get("rmbg_video_fps", DEFAULT_FPS))))
    except (TypeError, ValueError):
        fps = DEFAULT_FPS
    threshold_raw = raw.get("segmentation_threshold", raw.get("rmbg_segmentation_threshold", 0.35))
    try:
        threshold = max(0.05, min(0.95, float(threshold_raw)))
    except (TypeError, ValueError):
        threshold = 0.35
    prompt = str(raw.get("segmentation_prompt") or raw.get("rmbg_segmentation_prompt") or "foreground subject").strip()[:MAX_PROMPT_LENGTH]
    adapter = str(raw.get("segmentation_adapter") or raw.get("rmbg_segmentation_adapter") or "auto").strip().lower()
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "route": route,
        "execution": execution,
        "adapter": adapter,
        "prompt": prompt,
        "threshold": threshold,
        "sam_model": portable_model_identifier(raw.get("segmentation_sam_model") or raw.get("rmbg_sam_model"), "sam"),
        "dino_model": portable_model_identifier(raw.get("segmentation_dino_model") or raw.get("rmbg_dino_model"), "groundingdino"),
        "fps": fps,
        "max_frames": max_frames,
        "save_mask": raw.get("save_mask", True) is not False,
        "filename_prefix": "Neo_RMBG/segmentation",
        "temporal_mode": "framewise",
        "temporal_tracking": False,
    }


def build_batch_video_catalog(object_info: dict[str, Any] | None) -> dict[str, Any]:
    """Return live-gated contracts for batch images and video segmentation."""

    info = object_info if isinstance(object_info, dict) else {}
    segment_found = _node_spec(info, _BATCH_SEGMENT_CANDIDATES)
    load_found = _node_spec(info, _VIDEO_LOAD_CANDIDATES)
    combine_found = _node_spec(info, _VIDEO_COMBINE_CANDIDATES)
    batch_found = _node_spec(info, _IMAGE_BATCH_CANDIDATES)
    mask_found = _node_spec(info, _MASK_TO_IMAGE_CANDIDATES)
    segment_name, segment_spec = segment_found or ("", {})
    load_name, load_spec = load_found or ("", {})
    combine_name, combine_spec = combine_found or ("", {})
    batch_name, batch_spec = batch_found or ("", {})
    mask_name, mask_spec = mask_found or ("", {})
    segment_inputs = _input_names(segment_spec)
    load_inputs = _input_names(load_spec)
    combine_inputs = _input_names(combine_spec)
    batch_inputs = _input_names(batch_spec)
    mask_inputs = _input_names(mask_spec)
    blockers: list[str] = []
    if not info:
        blockers.append("Live ComfyUI /object_info is unavailable.")
    if not segment_found:
        blockers.append("No verified batch-capable SegmentV2/Segment_v1 node is visible.")
    missing_segment = sorted({"image", "prompt"} - segment_inputs)
    if missing_segment:
        blockers.append(f"Live segmentation node {segment_name or '<missing>'} is missing: {', '.join(missing_segment)}.")
    segment_sam_models = _choices(segment_spec, "sam_model", "sam")
    segment_dino_models = _choices(segment_spec, "dino_model", "groundingdino")
    if "sam_model" in segment_inputs and not segment_sam_models:
        blockers.append(f"Live segmentation node {segment_name} exposes no installed sam_model choices.")
    if "dino_model" in segment_inputs and not segment_dino_models:
        blockers.append(f"Live segmentation node {segment_name} exposes no installed dino_model choices.")
    segment_ready = bool(segment_found and not missing_segment and ("sam_model" not in segment_inputs or segment_sam_models) and ("dino_model" not in segment_inputs or segment_dino_models))
    if not _first_input(load_inputs, ("video", "video_path", "path", "file", "filename", "upload", "video_name")):
        blockers.append(f"Live video loader {load_name or '<missing>'} has no supported video input.")
    if "images" not in combine_inputs:
        blockers.append(f"Live video combiner {combine_name or '<missing>'} is missing images input.")
    if not _first_input(mask_inputs, ("mask", "images")):
        blockers.append(f"Live mask-to-image node {mask_name or '<missing>'} has no supported mask input.")
    batch_image_ready = bool(segment_ready and batch_found and {"image1", "image2"}.issubset(batch_inputs))
    video_ready = bool(segment_found and load_found and combine_found and mask_found and not blockers)
    batch_blockers = [] if batch_image_ready else [
        "Single-graph image batches require live ImageBatch with image1 and image2 inputs plus a verified SegmentV2/Segment_v1 node."
    ]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "available": bool(batch_image_ready or video_ready),
        "contracts": {
            "batch_images": {
                "available": batch_image_ready,
                "execution": "single_comfy_batch",
                "segment_node": segment_name,
                "image_batch_node": batch_name,
                "required_inputs": ["image1", "image2"],
                "blockers": batch_blockers,
            },
            "video_framewise": {
                "available": video_ready,
                "execution": "framewise_no_temporal_tracking",
                "segment_node": segment_name,
                "video_loader": load_name,
                "video_combiner": combine_name,
                "mask_to_image": mask_name,
                "required_inputs": {"loader": sorted(load_inputs), "combiner": sorted(combine_inputs)},
                "blockers": [] if video_ready else blockers,
            },
        },
        "segment": {
            "node_class": segment_name,
            "input_names": sorted(segment_inputs),
            "sam_models": segment_sam_models,
            "dino_models": segment_dino_models,
        },
        "limits": {"max_batch_images": MAX_BATCH_IMAGES, "max_video_frames": MAX_VIDEO_FRAMES, "max_prompt_length": MAX_PROMPT_LENGTH},
        "safety": {
            "requires_live_object_info": True,
            "requires_exact_inputs": True,
            "temporal_tracking": "not_implemented",
            "no_silent_fallback": True,
            "path_policy": "portable_comfy_input_names_only",
        },
    }


def resolve_batch_video_route(catalog: dict[str, Any] | None, route: str) -> dict[str, Any]:
    requested = route if route in VALID_ROUTES else "batch_images"
    row = ((catalog or {}).get("contracts") or {}).get(requested)
    if not isinstance(row, dict) or not row.get("available"):
        return {
            "ready": False,
            "route": requested,
            "blockers": list((row or {}).get("blockers") or [f"RMBG batch/video route is unavailable: {requested}."]),
        }
    return {"ready": True, "route": requested, "contract": row, "blockers": []}


def _segment_inputs(spec: dict[str, Any], settings: dict[str, Any], image_ref: list[Any], model_choices: dict[str, list[str]]) -> dict[str, Any]:
    names = _input_names(spec)
    inputs: dict[str, Any] = {"image": image_ref, "prompt": settings["prompt"]}
    if "sam_model" in names:
        inputs["sam_model"] = settings.get("sam_model") or (model_choices.get("sam_model") or [""])[0]
    if "dino_model" in names:
        inputs["dino_model"] = settings.get("dino_model") or (model_choices.get("dino_model") or [""])[0]
    if "threshold" in names:
        inputs["threshold"] = settings["threshold"]
    if "mask_blur" in names:
        inputs["mask_blur"] = 0
    if "mask_offset" in names:
        inputs["mask_offset"] = 0
    if "background" in names:
        inputs["background"] = "Alpha"
    if "background_color" in names:
        inputs["background_color"] = "#222222"
    if "invert_output" in names:
        inputs["invert_output"] = False
    missing = [name for name in ("sam_model", "dino_model") if name in names and not str(inputs.get(name) or "").strip()]
    if missing:
        raise ValueError(f"Live segmentation node has no installed model choice for: {', '.join(missing)}.")
    return inputs


def _save_image_node(node_id: str, image_ref: list[Any], prefix: str) -> dict[str, Any]:
    return {"class_type": "SaveImage", "inputs": {"images": image_ref, "filename_prefix": prefix}}


def build_batch_image_workflow(source_names: list[str], settings: dict[str, Any] | None, object_info: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    clean = normalize_batch_video_settings(settings)
    names = [_safe_comfy_name(item, suffixes={".png", ".jpg", ".jpeg", ".webp", ".bmp"}) for item in source_names]
    names = [item for item in names if item]
    if not names:
        raise ValueError("Batch segmentation needs at least one uploaded image name.")
    if len(names) > MAX_BATCH_IMAGES:
        raise ValueError(f"Batch segmentation accepts at most {MAX_BATCH_IMAGES} images per run.")
    catalog = build_batch_video_catalog(object_info)
    route = resolve_batch_video_route(catalog, "batch_images")
    if not route["ready"]:
        raise ValueError("; ".join(route["blockers"]))
    segment_name, segment_spec = _node_spec(object_info, _BATCH_SEGMENT_CANDIDATES) or ("", {})
    batch_name, batch_spec = _node_spec(object_info, _IMAGE_BATCH_CANDIDATES) or ("", {})
    graph: dict[str, Any] = {}
    next_id = 1
    refs: list[list[Any]] = []
    for name in names:
        node_id = str(next_id)
        graph[node_id] = {"class_type": "LoadImage", "inputs": {"image": name, "upload": "image"}}
        refs.append([node_id, 0])
        next_id += 1
    image_ref = refs[0]
    batch_inputs = _input_names(batch_spec)
    for ref in refs[1:]:
        node_id = str(next_id)
        graph[node_id] = {"class_type": batch_name, "inputs": {"image1": image_ref, "image2": ref}}
        image_ref = [node_id, 0]
        next_id += 1
    model_choices = {
        "sam_model": _choices(segment_spec, "sam_model", "sam"),
        "dino_model": _choices(segment_spec, "dino_model", "groundingdino"),
    }
    graph[str(next_id)] = {"class_type": segment_name, "inputs": _segment_inputs(segment_spec, clean, image_ref, model_choices)}
    segment_ref = [str(next_id), 0]
    mask_ref = [str(next_id), 1]
    next_id += 1
    mask_name, mask_spec = _node_spec(object_info, _MASK_TO_IMAGE_CANDIDATES) or ("", {})
    mask_input = _first_input(_input_names(mask_spec), ("mask", "images"))
    graph[str(next_id)] = {"class_type": mask_name, "inputs": {mask_input: mask_ref}}
    mask_image_ref = [str(next_id), 0]
    next_id += 1
    graph[str(next_id)] = _save_image_node(str(next_id), segment_ref, f"{clean['filename_prefix']}/batch_foreground")
    next_id += 1
    if clean["save_mask"]:
        graph[str(next_id)] = _save_image_node(str(next_id), mask_image_ref, f"{clean['filename_prefix']}/batch_mask")
    notes = [
        f"Queued one Comfy batch graph for {len(names)} image(s) using {segment_name}.",
        f"Images were concatenated with live {batch_name} nodes; no per-item fallback was used.",
        "Batch outputs preserve source order as the Comfy IMAGE batch order.",
    ]
    return graph, clean, notes


def _video_loader_inputs(spec: dict[str, Any], video_name: str, settings: dict[str, Any]) -> dict[str, Any]:
    names = _input_names(spec)
    video_field = _first_input(names, ("video", "video_path", "path", "file", "filename", "upload", "video_name"))
    if not video_field:
        raise ValueError("Live video loader has no supported video input.")
    inputs: dict[str, Any] = {video_field: video_name}
    defaults: dict[str, Any] = {
        "force_rate": settings["fps"],
        "frame_load_cap": settings["max_frames"],
        "skip_first_frames": 0,
        "select_every_nth": 1,
        "custom_width": 0,
        "custom_height": 0,
    }
    for name, value in defaults.items():
        if name in names:
            inputs[name] = value
    return inputs


def _video_combine_inputs(spec: dict[str, Any], image_ref: list[Any], settings: dict[str, Any], audio_ref: list[Any] | None = None) -> dict[str, Any]:
    names = _input_names(spec)
    if "images" not in names:
        raise ValueError("Live video combiner is missing images input.")
    inputs: dict[str, Any] = {"images": image_ref}
    values: dict[str, Any] = {
        "frame_rate": settings["fps"],
        "loop_count": 0,
        "filename_prefix": settings["filename_prefix"],
        "pingpong": False,
        "save_output": True,
    }
    for name, value in values.items():
        if name in names:
            inputs[name] = value
    if "format" in names:
        choices = _choices(spec, "format")
        inputs["format"] = next((item for item in choices if "mp4" in item.casefold()), choices[0] if choices else "video/h264-mp4")
    if audio_ref is not None and "audio" in names:
        inputs["audio"] = audio_ref
    return inputs


def build_video_framewise_workflow(video_name: str, settings: dict[str, Any] | None, object_info: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    clean = normalize_batch_video_settings({**(settings or {}), "rmbg_batch_route": "video_framewise"})
    safe_video = _safe_comfy_name(video_name, suffixes=_VIDEO_FILE_SUFFIXES)
    if not safe_video:
        raise ValueError("Video segmentation needs a portable uploaded video name.")
    catalog = build_batch_video_catalog(object_info)
    route = resolve_batch_video_route(catalog, "video_framewise")
    if not route["ready"]:
        raise ValueError("; ".join(route["blockers"]))
    load_name, load_spec = _node_spec(object_info, _VIDEO_LOAD_CANDIDATES) or ("", {})
    combine_name, combine_spec = _node_spec(object_info, _VIDEO_COMBINE_CANDIDATES) or ("", {})
    segment_name, segment_spec = _node_spec(object_info, _BATCH_SEGMENT_CANDIDATES) or ("", {})
    mask_name, mask_spec = _node_spec(object_info, _MASK_TO_IMAGE_CANDIDATES) or ("", {})
    model_choices = {
        "sam_model": _choices(segment_spec, "sam_model", "sam"),
        "dino_model": _choices(segment_spec, "dino_model", "groundingdino"),
    }
    graph: dict[str, Any] = {
        "1": {"class_type": load_name, "inputs": _video_loader_inputs(load_spec, safe_video, clean)},
    }
    graph["2"] = {"class_type": segment_name, "inputs": _segment_inputs(segment_spec, clean, ["1", 0], model_choices)}
    mask_input = _first_input(_input_names(mask_spec), ("mask", "images"))
    graph["3"] = {"class_type": mask_name, "inputs": {mask_input: ["2", 1]}}
    graph["4"] = {"class_type": combine_name, "inputs": _video_combine_inputs(combine_spec, ["2", 0], clean, ["1", 1])}
    if clean["save_mask"]:
        graph["5"] = {"class_type": combine_name, "inputs": _video_combine_inputs(combine_spec, ["3", 0], {**clean, "filename_prefix": f"{clean['filename_prefix']}/mask"}, ["1", 1])}
    notes = [
        f"Queued frame-wise video segmentation with {segment_name} over {load_name}.",
        f"Foreground and alpha-mask video outputs use {combine_name}.",
        "Temporal propagation/tracking is intentionally disabled; each frame is segmented independently in the loaded IMAGE batch.",
    ]
    return graph, clean, notes


def is_supported_video_filename(filename: str | None) -> bool:
    return PurePosixPath(str(filename or "").strip()).suffix.casefold() in _VIDEO_FILE_SUFFIXES
