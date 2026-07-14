from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from neo_extensions.built_in.adetailer.backend.model_catalog import list_detailer_models

from .constants import PRESET_MODEL_CANDIDATES, SAM_COMFY_FILENAMES


# Comfy SAM targets the exact Impact Pack node contract. Other nodes with similar
# names often expose incompatible inputs and must not be treated as aliases without
# a dedicated adapter.
SAM_NODE_ALIASES: dict[str, tuple[str, ...]] = {
    "SAMLoader": ("SAMLoader",),
    "SAMDetectorCombined": ("SAMDetectorCombined",),
    "MaskToSEGS": ("MaskToSEGS",),
    "SolidMask": ("SolidMask",),
    "MaskComposite": ("MaskComposite",),
    "JoinImageWithAlpha": ("JoinImageWithAlpha",),
    "MaskToImage": ("MaskToImage",),
    "SaveImage": ("SaveImage",),
}

SAM_OPTIONAL_NODE_ALIASES: dict[str, tuple[str, ...]] = {
    "LoadRembgByBiRefNetModel": ("LoadRembgByBiRefNetModel",),
    "GetMaskByBiRefNet": ("GetMaskByBiRefNet",),
    "GrowMask": ("GrowMask",),
    "FeatherMask": ("FeatherMask",),
    "BlurFusionForegroundEstimation": ("BlurFusionForegroundEstimation",),
}


@dataclass(frozen=True)
class SharedSamResolution:
    ready: bool
    model: str
    node_map: dict[str, str]
    missing_nodes: tuple[str, ...]
    reason: str
    refinement_model: str = ""
    refinement_ready: bool = True
    refinement_fallback: bool = False
    warnings: tuple[str, ...] = ()


def _node_map(available_nodes: list[str] | tuple[str, ...] | set[str]) -> tuple[dict[str, str], tuple[str, ...]]:
    available = {str(item) for item in available_nodes if str(item).strip()}
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for canonical, aliases in SAM_NODE_ALIASES.items():
        actual = next((alias for alias in aliases if alias in available), "")
        if actual:
            resolved[canonical] = actual
        else:
            missing.append(canonical)
    return resolved, tuple(missing)


_PERSON_TOKENS = ("person", "people", "human", "fullbody", "full_body", "full-body", "body")
_FACE_ONLY_TOKENS = ("face", "facial", "hand", "eye", "mouth", "lip", "head")


def _dedupe_models(rows: list[str] | tuple[str, ...]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in rows:
        value = str(raw or "").strip().replace("\\", "/")
        if not value:
            continue
        key = value.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def detector_is_person_capable(model_name: str) -> bool:
    """Return whether a detector is a reasonable whole-person candidate.

    Explicit person/human models win.  Generic YOLO/COCO checkpoints are also
    person-capable even when their filename is simply ``yolov8m.pt``.  Models
    explicitly named for face/hand/head details are excluded.
    """

    folded = str(model_name or "").casefold().replace("\\", "/")
    base = folded.rsplit("/", 1)[-1]
    if any(token in folded for token in _PERSON_TOKENS):
        return True
    if any(token in base for token in _FACE_ONLY_TOKENS):
        return False
    return "yolo" in base or "coco" in base


def detector_is_detail_only(model_name: str) -> bool:
    folded = str(model_name or "").casefold().replace("\\", "/")
    base = folded.rsplit("/", 1)[-1]
    return any(token in base for token in _FACE_ONLY_TOKENS)


def preferred_person_detector(models: list[str] | tuple[str, ...]) -> str:
    rows = _dedupe_models(list(models))
    explicit = next((item for item in rows if any(token in item.casefold() for token in _PERSON_TOKENS)), "")
    if explicit:
        return explicit
    generic = next((item for item in rows if detector_is_person_capable(item)), "")
    if generic:
        return generic
    return rows[0] if len(rows) == 1 and not any(token in rows[0].casefold() for token in _FACE_ONLY_TOKENS) else ""


def resolve_person_detector_choice(requested: str, models: list[str] | tuple[str, ...]) -> str:
    """Preserve an exact explicit custom choice unless it is detail-only.

    Person preference repairs missing or face/hand/head-only selections. It is
    not a filter: an explicitly selected arbitrary one-class detector remains
    executable even when its filename does not contain a person token.
    """

    rows = _dedupe_models(list(models))
    exact = next((item for item in rows if item.casefold() == str(requested or "").strip().casefold()), "")
    if exact and not detector_is_detail_only(exact):
        return exact
    return preferred_person_detector(rows)


def _preferred_detector(models: list[str]) -> str:
    return preferred_person_detector(models)


def _resolve_exact_or_candidate(requested: str, models: list[str], candidates: tuple[str, ...]) -> str:
    by_fold = {item.casefold(): item for item in models}
    if requested:
        return by_fold.get(requested.casefold(), "")
    for candidate in candidates:
        if candidate.casefold() in by_fold:
            return by_fold[candidate.casefold()]
    return models[0] if len(models) == 1 else ""


def build_shared_sam_catalog(
    *,
    available_nodes: list[str] | tuple[str, ...] | set[str] = (),
    detector_root: str = "",
    sam_root: str = "",
    birefnet_models: list[str] | tuple[str, ...] = (),
    live_sam_models: list[str] | tuple[str, ...] = (),
    live_bbox_models: list[str] | tuple[str, ...] = (),
    live_segm_models: list[str] | tuple[str, ...] = (),
    backend_details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detailer = list_detailer_models(
        detector_root=detector_root,
        sam_root=sam_root,
        backend_details=backend_details,
    )
    disk_models = [str(item) for item in (detailer.get("sam_models") or []) if str(item).strip()]
    models: list[str] = []
    seen_models: set[str] = set()
    for item in [*disk_models, *[str(value) for value in live_sam_models]]:
        name = str(item or "").strip().replace("\\", "/")
        if not name:
            continue
        key = name.casefold()
        if key in seen_models:
            continue
        seen_models.add(key)
        models.append(name)
    bbox_models = _dedupe_models([*[str(item) for item in (detailer.get("bbox_models") or []) if str(item).strip()], *[str(item) for item in live_bbox_models]])
    segm_models = _dedupe_models([*[str(item) for item in (detailer.get("segm_models") or []) if str(item).strip()], *[str(item) for item in live_segm_models]])
    node_map, missing = _node_map(available_nodes)
    available = {str(item) for item in available_nodes if str(item).strip()}
    optional_map = {
        canonical: next((alias for alias in aliases if alias in available), "")
        for canonical, aliases in SAM_OPTIONAL_NODE_ALIASES.items()
    }
    node_map.update({key: value for key, value in optional_map.items() if value})
    refinement_models = [str(item) for item in birefnet_models if str(item).strip()]
    detailer_sources = [str(item) for item in (detailer.get("sources") or []) if str(item).strip()]
    detailer_diagnostics = detailer.get("diagnostics") if isinstance(detailer.get("diagnostics"), dict) else {}
    return {
        "ready": bool(models) and not missing,
        "models": models,
        "sam_dir": "ComfyUI/models/sams",
        "bbox_models": bbox_models,
        "segm_models": segm_models,
        "default_bbox_model": _preferred_detector(bbox_models),
        "default_segm_model": _preferred_detector(segm_models),
        "detector_counts": {"bbox": len(bbox_models), "segm": len(segm_models)},
        "detector_sources": detailer_sources,
        "detector_scan": {
            "schema_id": "neo.image.background_removal.detector_scan.v1",
            "counts": {"bbox": len(bbox_models), "segm": len(segm_models)},
            "sources": detailer_sources,
            "standard_filesystem": dict(detailer_diagnostics.get("standard_filesystem") or {}),
            "comfy_model_folders": dict(detailer_diagnostics.get("comfy_model_folders") or {}),
            "warnings": list(detailer_diagnostics.get("warnings") or []),
            "path_policy": "absolute_paths_server_side_only",
        },
        "birefnet_models": refinement_models,
        "node_map": node_map,
        "missing_nodes": list(missing),
        "required_nodes": list(SAM_NODE_ALIASES),
        "optional_nodes": list(SAM_OPTIONAL_NODE_ALIASES),
        "sources": [name for name, rows in (("filesystem", disk_models), ("comfy_object_info", list(live_sam_models)), ("comfy_detector_object_info", [*live_bbox_models, *live_segm_models])) if rows],
        "source": "comfy_sam_catalog",
        "reuse_note": "Uses SAM checkpoints discovered from the standard ComfyUI/models/sams catalog.",
    }


def resolve_shared_sam(settings: dict[str, Any], catalog: dict[str, Any]) -> SharedSamResolution:
    models = [str(item) for item in (catalog.get("models") or []) if str(item).strip()]
    node_map = dict(catalog.get("node_map") or {})
    requested = str(settings.get("sam_comfy_model") or "").strip()
    if requested:
        exact = next((item for item in models if item.casefold() == requested.casefold()), "")
        if not exact:
            return SharedSamResolution(False, "", node_map, tuple(catalog.get("missing_nodes") or ()), f"Comfy SAM model is not installed: {requested}")
        model = exact
    else:
        expected = SAM_COMFY_FILENAMES.get(str(settings.get("sam_model_variant") or ""), "")
        model = next((item for item in models if item.casefold() == expected.casefold()), "") if expected else ""
        if not model and len(models) == 1:
            model = models[0]

    missing = tuple(str(item) for item in (catalog.get("missing_nodes") or []) if str(item).strip())
    if missing:
        return SharedSamResolution(False, model, node_map, missing, "Impact Pack SAM nodes are missing: " + ", ".join(missing))
    if not model:
        return SharedSamResolution(False, "", node_map, (), "No Comfy SAM model matches the selected variant. Choose a model installed under ComfyUI/models/sams.")

    if str(settings.get("sam_refine_mode") or "sam_only") != "birefnet_gate":
        return SharedSamResolution(True, model, node_map, (), f"Comfy SAM model ready: {model}")

    refinement_models = [str(item) for item in (catalog.get("birefnet_models") or []) if str(item).strip()]
    requested_refinement = str(settings.get("model") or "").strip()
    preset = str(settings.get("preset") or "interactive_select")
    candidates = PRESET_MODEL_CANDIDATES.get(preset, PRESET_MODEL_CANDIDATES["interactive_select"])
    refinement_model = _resolve_exact_or_candidate(requested_refinement, refinement_models, candidates)
    refinement_missing: list[str] = []
    if requested_refinement and not refinement_model:
        refinement_missing.append(f"BiRefNet model {requested_refinement}")
    elif not refinement_model:
        refinement_missing.append("an installed Comfy BiRefNet edge model")
    for key in ("LoadRembgByBiRefNetModel", "GetMaskByBiRefNet"):
        if not node_map.get(key):
            refinement_missing.append(key)
    if int(settings.get("sam_gate_expand") or 0) and not node_map.get("GrowMask"):
        refinement_missing.append("GrowMask")
    if int(settings.get("sam_gate_feather") or 0) and not node_map.get("FeatherMask"):
        refinement_missing.append("FeatherMask")

    if refinement_missing:
        reason = "Comfy SAM is ready, but BiRefNet edge handoff is unavailable: " + ", ".join(refinement_missing)
        if bool(settings.get("sam_refine_fallback", True)):
            return SharedSamResolution(
                True,
                model,
                node_map,
                (),
                reason + ". SAM-only fallback will be used.",
                refinement_model="",
                refinement_ready=False,
                refinement_fallback=True,
                warnings=(reason,),
            )
        return SharedSamResolution(
            False,
            model,
            node_map,
            tuple(refinement_missing),
            reason + ". Enable SAM-only fallback or install the missing assets.",
            refinement_model="",
            refinement_ready=False,
        )

    return SharedSamResolution(
        True,
        model,
        node_map,
        (),
        f"Comfy SAM model ready: {model}; Comfy BiRefNet edge model ready: {refinement_model}",
        refinement_model=refinement_model,
        refinement_ready=True,
    )


def subjects_support_comfy(subjects: list[dict[str, Any]]) -> tuple[bool, str]:
    selected = [item for item in subjects if item.get("selected", True)]
    if not selected:
        return False, "Select at least one detected or manual subject."
    for item in selected:
        bbox = item.get("bbox") if isinstance(item.get("bbox"), dict) else {}
        if not bbox:
            return False, "Comfy Impact SAM requires a box for every selected subject."
        if item.get("keep_points") or item.get("remove_points"):
            return False, "Per-subject Keep/Remove correction points require Neo Native ONNX SAM."
    return True, "Selected subjects are box-addressable through Impact Pack SAM."
