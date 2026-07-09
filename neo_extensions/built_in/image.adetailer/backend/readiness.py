from __future__ import annotations

from copy import deepcopy
from typing import Any

from .constants import ACTIVE_ROUTE_STATES, EXTENSION_ID, EXTENSION_TYPE, PHASE, WORKSPACE_APP
from .payload_schema import normalize_block, parse_manual_boxes
from .support_matrix import support_for_route


_REQUIRED_NODE_KEYS = ("FaceDetailer", "UltralyticsDetectorProvider")
_BBOX_SEGS_NODE_KEYS = ("BboxDetectorSEGS", "SEGSDetailer", "SEGSPaste", "ToBasicPipe", "CLIPTextEncode")
_SEGM_SEGS_NODE_KEYS = ("SegmDetectorSEGS", "SEGSDetailer", "SEGSPaste", "ToBasicPipe", "CLIPTextEncode")
_MANUAL_NODE_KEYS = ("MaskToSEGS", "SEGSDetailer", "SEGSPaste", "ToBasicPipe", "CLIPTextEncode")


def _node_names(node_inventory: Any = None, node_status: dict[str, Any] | None = None) -> set[str]:
    names: set[str] = set()
    if isinstance(node_inventory, dict):
        candidates = node_inventory.get("available_nodes") or node_inventory.get("nodes") or node_inventory.get("present") or []
    else:
        candidates = node_inventory or []
    if isinstance(candidates, dict):
        candidates = list(candidates.keys())
    if isinstance(candidates, (list, tuple, set)):
        names.update(str(item) for item in candidates if item)
    status = node_status if isinstance(node_status, dict) else {}
    for key in ("available_nodes", "present_required", "present_optional", "required", "optional"):
        value = status.get(key)
        if isinstance(value, (list, tuple, set)):
            names.update(str(item) for item in value if item)
    return names


def _missing(required: tuple[str, ...], available: set[str]) -> list[str]:
    return [item for item in required if item not in available]


def _detector_assets(params: dict[str, Any]) -> list[str]:
    models: list[str] = []
    raw_passes = params.get("detailer_passes") if isinstance(params.get("detailer_passes"), list) else []
    for item in raw_passes:
        if isinstance(item, dict) and item.get("detector_model"):
            models.append(str(item.get("detector_model")))
    if params.get("detector_model"):
        models.append(str(params.get("detector_model")))
    seen: set[str] = set()
    out: list[str] = []
    for model in models:
        clean = model.strip()
        if clean and clean not in seen:
            out.append(clean)
            seen.add(clean)
    return out


def _pass_readiness(pass_data: dict[str, Any], index: int, available_nodes: set[str]) -> dict[str, Any]:
    enabled = bool(pass_data.get("enabled", True))
    target_mode = str(pass_data.get("target_mode") or "auto_detect")
    detector_type = str(pass_data.get("detector_type") or "bbox")
    model = str(pass_data.get("detector_model") or "").strip()
    manual_boxes, manual_warnings = parse_manual_boxes(pass_data.get("manual_boxes"))
    needs_manual = target_mode == "manual_boxes"
    needs_segs = needs_manual or bool(pass_data.get("positive_prompt") and "[SEP]" in str(pass_data.get("positive_prompt"))) or target_mode == "auto_detect"
    required_nodes: tuple[str, ...]
    if needs_manual:
        required_nodes = _MANUAL_NODE_KEYS
    elif needs_segs:
        required_nodes = _SEGM_SEGS_NODE_KEYS if detector_type.startswith("segm") else _BBOX_SEGS_NODE_KEYS
    else:
        required_nodes = _REQUIRED_NODE_KEYS
    missing_nodes = _missing(required_nodes, available_nodes)
    blockers: list[str] = []
    if enabled and not model and not needs_manual:
        blockers.append("detector_model_missing")
    if enabled and needs_manual and not manual_boxes:
        blockers.append("manual_boxes_missing")
    if manual_warnings:
        blockers.extend(manual_warnings)
    if missing_nodes:
        blockers.append("required_nodes_missing")
    return {
        "index": index,
        "id": pass_data.get("id") or ("primary" if index == 0 else f"pass-{index + 1}"),
        "label": pass_data.get("label") or ("Primary pass" if index == 0 else f"Pass {index + 1}"),
        "enabled": enabled,
        "target_mode": target_mode,
        "detector_type": detector_type,
        "detector_model": model,
        "manual_box_count": len(manual_boxes),
        "required_nodes": list(required_nodes),
        "missing_nodes": missing_nodes,
        "blockers": blockers,
        "can_run": enabled and not blockers,
    }


def build_replay_readiness(
    payload: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    node_inventory: Any = None,
    node_status: dict[str, Any] | None = None,
    workflow_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a replay/memory readiness contract for ADetailer.

    Phase K separates capability readiness from user enable state. A replay payload
    is always restored disabled, but this report tells the UI/assistant exactly
    what must be checked before the user can enable it again.
    """
    block = normalize_block({"extensions": {EXTENSION_ID: payload}} if isinstance(payload, dict) else {})
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    passes = params.get("detailer_passes") if isinstance(params.get("detailer_passes"), list) else []
    route_data = deepcopy(route or {})
    support = support_for_route(route_data)
    available_nodes = _node_names(node_inventory, node_status)
    pass_reports = [_pass_readiness(item, idx, available_nodes) for idx, item in enumerate(passes) if isinstance(item, dict)]
    enabled_reports = [item for item in pass_reports if item.get("enabled")]
    blocking_reports = [item for item in enabled_reports if item.get("blockers")]
    route_ready = support.get("state") in ACTIVE_ROUTE_STATES
    node_ready = not _missing(_REQUIRED_NODE_KEYS, available_nodes) if available_nodes else bool((node_status or {}).get("ready"))
    has_enabled_pass = bool(enabled_reports)
    can_enable = bool(route_ready and node_ready and has_enabled_pass and not blocking_reports)
    checklist = {
        "route": route_ready,
        "required_nodes": node_ready,
        "detector_models": all(bool(item.get("detector_model")) or item.get("target_mode") == "manual_boxes" for item in enabled_reports),
        "pass_cards": has_enabled_pass,
        "manual_boxes": all(not (item.get("target_mode") == "manual_boxes" and item.get("manual_box_count", 0) == 0) for item in enabled_reports),
        "impact_pack": node_ready,
    }
    blockers: list[str] = []
    if not route_ready:
        blockers.append("route_not_supported")
    if not node_ready:
        blockers.append("required_nodes_missing")
    if not has_enabled_pass:
        blockers.append("no_enabled_detailer_passes")
    for item in blocking_reports:
        blockers.extend(str(reason) for reason in item.get("blockers") or [])
    # Stable order, no duplicates.
    deduped_blockers: list[str] = []
    for item in blockers:
        if item not in deduped_blockers:
            deduped_blockers.append(item)
    patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    runtime = {
        "workflow_patch_applied": bool(patch.get("applied") or patch.get("mutated")),
        "runtime_unit_count": int(patch.get("runtime_unit_count") or 0),
        "patch_paths": deepcopy(patch.get("patch_paths") if isinstance(patch.get("patch_paths"), list) else ([] if not patch.get("patch_path") else [patch.get("patch_path")])),
        "patched_image_ref": deepcopy(patch.get("patched_image_ref") if isinstance(patch.get("patched_image_ref"), list) else []),
        "previous_image_ref": deepcopy(patch.get("previous_image_ref") if isinstance(patch.get("previous_image_ref"), list) else []),
    }
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "phase": "K",
        "source_phase": PHASE,
        "route": route_data,
        "support": support,
        "restore_enabled": False,
        "revalidation_required": True,
        "can_enable_after_revalidation": can_enable,
        "restore_policy": "restore_disabled_until_route_nodes_detector_models_pass_cards_manual_boxes_and_impact_pack_validate",
        "checklist": checklist,
        "blockers": deduped_blockers,
        "detector_assets": _detector_assets(params),
        "sam_assets": [str(params.get("sam_model"))] if params.get("sam_model") else [],
        "detailer_pass_count": len(pass_reports),
        "enabled_detailer_pass_count": len(enabled_reports),
        "passes": pass_reports,
        "runtime": runtime,
    }


def summarize_replay_readiness(readiness: dict[str, Any] | None = None) -> str:
    data = readiness if isinstance(readiness, dict) else {}
    if data.get("can_enable_after_revalidation"):
        return "ADetailer replay is ready to re-enable after user confirmation."
    blockers = data.get("blockers") if isinstance(data.get("blockers"), list) else []
    if blockers:
        return "ADetailer replay restored disabled; blocked by " + ", ".join(str(item) for item in blockers) + "."
    return "ADetailer replay restored disabled until route, nodes, detector models, pass cards, and manual boxes are revalidated."
