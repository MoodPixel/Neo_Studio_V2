"""Portable, inventory-only discovery for ComfyUI-RMBG capabilities.

Phase RMBG-0 deliberately does not build or execute new ComfyUI graphs.  The
live ``/object_info`` response is the authority for node availability; the
names below are candidates from the installed RMBG ecosystem and are never
treated as proof that a node exists or that its inputs are compatible.
"""
from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Iterable

from .public_hygiene import portable_model_identifier


SCHEMA_ID = "neo.image.rmbg.capability_inventory.v1"
SCHEMA_VERSION = 1


RMBG_CAPABILITY_DEFINITIONS: dict[str, tuple[str, ...]] = {
    "background_removal": (
        "RMBG",
        "RMBGNode",
        "RMBG2",
        "RMBGBackgroundRemoval",
        "LoadRembgByBiRefNetModel",
        "RembgByBiRefNetAdvanced",
    ),
    "semantic_segmentation": (
        "Segment",
        "SegmentV2",
        "Segment_v1",
        "Segment_V1",
        "Segment_v2",
        "Segment_V2",
        "SAM2Segment",
        "SAM3Segment",
        "Florence2Segmentation",
        "Florence2ToCoordinates",
        "YoloV8",
        "YoloV8Adv",
    ),
    "prompt_detection": (
        "GroundingDINO",
        "GroundingDINOModel",
        "Segment",
        "SegmentV2",
        "SAM2Segment",
        "SAM3Segment",
    ),
    "semantic_regions": (
        "FaceSegment",
        "ClothesSegment",
        "FashionSegment",
    ),
    "matting": (
        "SDMatte",
        "SDMatteMatting",
        "AILab_SDMatte",
        "BiRefNetRMBG",
        "GetMaskByBiRefNet",
        "LoadRembgByBiRefNetModel",
        "RembgByBiRefNetAdvanced",
    ),
    "mask_utilities": (
        "MaskOverlay",
        "ObjectRemover",
        "LamaRemover",
        "ImageMaskResize",
        "ImageMaskConverter",
        "MaskEnhancer",
        "MaskCombiner",
        "MaskExtractor",
        "ColorToMask",
        "MaskToImage",
        "ImageToMask",
    ),
    "compositing": (
        "ImageCombiner",
        "ImageStitch",
        "AILab_ImageStitch",
        "ICLoRAConcat",
        "ImageCrop",
        "CropObject",
        "ImageCompare",
        "Compare",
        "ColorInput",
        "ImageResize",
    ),
    "batch_tools": (
        "ImageToList",
        "MaskToList",
        "ImageMaskToList",
        "ImageBatch",
        "VHS_LoadVideo",
        "VHS_LoadVideoPath",
        "LoadVideo",
        "VHS_VideoCombine",
        "VideoCombine",
    ),
    "batch_video_segmentation": (
        "ImageBatch",
        "SegmentV2",
        "Segment_V2",
        "Segment_v1",
        "VHS_LoadVideo",
        "VHS_LoadVideoPath",
        "LoadVideo",
        "VHS_VideoCombine",
        "VideoCombine",
        "MaskToImage",
    ),
    "context_conditioning": (
        "AILab_ReferenceLatentMask",
        "ReferenceLatentMask",
        "KontextReferenceLatentMask",
        "ICLoRAConcat",
    ),
}

MODEL_ROLES = ("birefnet", "sam", "bbox", "segm", "sam2", "sam3", "groundingdino", "sdmatte", "clothes", "fashion", "florence2", "ultralytics")

_UNKNOWN_NODE_MARKERS = (
    "rmbg",
    "birefnet",
    "rembg",
    "sam",
    "florence",
    "yolo",
    "mask",
    "segment",
    "matte",
    "stitch",
    "lama",
)


def _safe_node_name(value: Any) -> str:
    """Keep node identifiers portable even if a malformed provider leaks a path."""

    normalized = str(value or "").strip().replace("\\", "/")
    if not normalized or "\x00" in normalized:
        return ""
    if normalized.startswith(("/", "//", "~/", "file:/", "http://", "https://")) or (
        len(normalized) >= 3 and normalized[1] == ":" and normalized[2] == "/"
    ):
        normalized = PurePosixPath(normalized).name
    return normalized


def _available_node_map(object_info: Any) -> dict[str, tuple[str, Any]]:
    if not isinstance(object_info, dict):
        return {}
    rows: dict[str, tuple[str, Any]] = {}
    for raw_name, spec in object_info.items():
        name = _safe_node_name(raw_name)
        if not name:
            continue
        rows.setdefault(name.casefold(), (name, spec))
    return rows


def _input_names(spec: Any) -> list[str]:
    if not isinstance(spec, dict):
        return []
    input_spec = spec.get("input")
    if not isinstance(input_spec, dict):
        return []
    names: list[str] = []
    for section in ("required", "optional", "hidden"):
        values = input_spec.get(section)
        if not isinstance(values, dict):
            continue
        names.extend(str(name) for name in values if str(name).strip())
    return sorted(set(names), key=str.casefold)


def _portable_catalog(values: Iterable[Any] | None, role: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values or ():
        candidate: Any = value
        if isinstance(value, dict):
            candidate = value.get("name") or value.get("model") or value.get("filename") or value.get("id") or value.get("path")
        identifier = portable_model_identifier(candidate, role)
        key = identifier.casefold()
        if identifier and key not in seen:
            seen.add(key)
            result.append(identifier)
    return result


def _candidate_unknown_nodes(available: dict[str, tuple[str, Any]], known: set[str]) -> list[str]:
    unknown: list[str] = []
    for folded, (name, _spec) in available.items():
        if folded in known:
            continue
        if any(marker in folded for marker in _UNKNOWN_NODE_MARKERS):
            unknown.append(name)
    return sorted(unknown, key=str.casefold)


def build_rmbg_capability_inventory(
    object_info: dict[str, Any] | None,
    model_catalogs: dict[str, Iterable[Any]] | None = None,
) -> dict[str, Any]:
    """Build a path-safe inventory from live ComfyUI node metadata.

    ``object_info`` is intentionally accepted as data rather than queried in
    this module.  This keeps discovery testable and makes the safety boundary
    explicit for callers that already own the provider connection.
    """

    available = _available_node_map(object_info)
    catalog_available = bool(available)
    known_candidates: set[str] = {
        candidate.casefold()
        for candidates in RMBG_CAPABILITY_DEFINITIONS.values()
        for candidate in candidates
    }
    capabilities: dict[str, dict[str, Any]] = {}
    for capability, candidates in RMBG_CAPABILITY_DEFINITIONS.items():
        matched: list[dict[str, Any]] = []
        for candidate in candidates:
            row = available.get(candidate.casefold())
            if row is None:
                continue
            actual_name, spec = row
            if any(item.get("name", "").casefold() == actual_name.casefold() for item in matched):
                continue
            matched.append({"name": actual_name, "input_names": _input_names(spec)})
        matched.sort(key=lambda item: str(item["name"]).casefold())
        capabilities[capability] = {
            "available": bool(matched),
            "matched_nodes": matched,
            "candidate_nodes": list(candidates),
            "input_names": sorted({name for item in matched for name in item["input_names"]}, key=str.casefold),
            "status": "available" if matched else ("missing" if catalog_available else "catalog_unavailable"),
        }

    model_catalogs = model_catalogs or {}
    portable_catalogs = {
        role: _portable_catalog(model_catalogs.get(role), role)
        for role in MODEL_ROLES
        if model_catalogs.get(role)
    }
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "object_info_available": catalog_available,
        "available_nodes": sorted((name for name, _spec in available.values()), key=str.casefold),
        "capabilities": capabilities,
        "unknown_candidate_nodes": _candidate_unknown_nodes(available, known_candidates),
        "model_catalogs": portable_catalogs,
        "safety": {
            "inventory_only": True,
            "graph_mutation": False,
            "requires_live_object_info_for_execution": True,
            "unknown_node_policy": "do_not_assume_compatibility",
            "path_policy": "portable_identifiers_only",
        },
        "readiness": {
            "inventory_ready": catalog_available,
            "execution_allowed": False,
            "blockers": [] if catalog_available else ["Live ComfyUI /object_info is unavailable."],
        },
    }


def rmbg_capability_gate(inventory: dict[str, Any] | None, capability: str) -> dict[str, Any]:
    """Return a future-route gate without authorizing graph execution."""

    requested = str(capability or "").strip()
    row = (inventory or {}).get("capabilities", {}).get(requested)
    catalog_available = bool((inventory or {}).get("object_info_available"))
    if not catalog_available:
        blockers = ["Live ComfyUI /object_info is unavailable."]
    elif not isinstance(row, dict) or not row.get("available"):
        blockers = [f"No exact live ComfyUI node matched RMBG capability: {requested or '<empty>'}."]
    else:
        blockers = []
    return {
        "capability": requested,
        "ready": bool(catalog_available and isinstance(row, dict) and row.get("available")),
        "matched_nodes": list(row.get("matched_nodes") or []) if isinstance(row, dict) else [],
        "blockers": blockers,
        "execution_allowed": False,
        "graph_mutation": False,
    }
