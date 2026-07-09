from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .constants import EXTENSION_ID, NODE_ALIASES, OPTIONAL_NODES, PHASE, REQUIRED_NODES


def _coerce_node_names(available_nodes: Any = None) -> set[str]:
    """Normalize common Comfy /object_info node containers into a class-name set.

    Supported inputs:
    - None / empty: unchecked
    - {class_name: {...}} from Comfy /object_info
    - {"nodes": [...]} or {"object_info": {...}}
    - list/tuple/set of class names
    - list of dicts with class_type/name/class_name/id
    """
    if not available_nodes:
        return set()

    candidate = available_nodes
    if isinstance(candidate, Mapping):
        for key in ("object_info", "nodes", "available_nodes", "node_classes", "class_types"):
            nested = candidate.get(key)
            if nested:
                candidate = nested
                break

    names: set[str] = set()
    if isinstance(candidate, Mapping):
        for key, value in candidate.items():
            names.add(str(key))
            if isinstance(value, Mapping):
                for field in ("class_type", "class_name", "name", "display_name", "id"):
                    if value.get(field):
                        names.add(str(value[field]))
    elif isinstance(candidate, (set, list, tuple)):
        for item in candidate:
            if isinstance(item, Mapping):
                for field in ("class_type", "class_name", "name", "display_name", "id"):
                    if item.get(field):
                        names.add(str(item[field]))
            else:
                names.add(str(item))
    else:
        names.add(str(candidate))

    # Add canonical aliases while preserving original names for workflow use later.
    for name in list(names):
        alias = NODE_ALIASES.get(name)
        if alias:
            names.add(alias)
    return {name.strip() for name in names if str(name).strip()}


def discover_node_status(available_nodes: Any = None) -> dict[str, Any]:
    node_set = _coerce_node_names(available_nodes)
    checked = bool(node_set)
    present_required = [node for node in REQUIRED_NODES if node in node_set]
    missing_required = [node for node in REQUIRED_NODES if node not in node_set]
    present_optional = [node for node in OPTIONAL_NODES if node in node_set]
    missing_optional = [node for node in OPTIONAL_NODES if node not in node_set]
    ready = checked and not missing_required

    if ready:
        state = "ready"
        reason_code = "nodes_ready"
        reason = "Required Impact Pack nodes are available for ADetailer runtime gating."
    elif checked:
        state = "provider_gated"
        reason_code = "nodes_missing"
        reason = "Required Impact Pack nodes are missing: " + ", ".join(missing_required)
    else:
        state = "unchecked"
        reason_code = "nodes_unchecked"
        reason = "No Comfy node inventory was provided; runtime workflow mutation must stay gated."

    return {
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "checked": checked,
        "ready": ready,
        "state": state,
        "reason_code": reason_code,
        "reason": reason,
        "required": list(REQUIRED_NODES),
        "optional": list(OPTIONAL_NODES),
        "present_required": present_required,
        "missing_required": missing_required,
        "present_optional": present_optional,
        "missing_optional": missing_optional,
        "available_count": len(node_set),
        "available_nodes": sorted(node_set),
        "capabilities": {
            "face_detailer_path": ready,
            "onnx_detector_provider": "ONNXDetectorProvider" in node_set,
            "sam_loader": "SAMLoader" in node_set or "SAMModelLoader" in node_set,
            "segs_detailer_path": all(node in node_set for node in ("BboxDetectorSEGS", "SEGSDetailer", "SEGSPaste")),
            "mask_refine_path": all(node in node_set for node in ("ImpactDilateMaskInSEGS", "ImpactGaussianBlurMaskInSEGS")),
            "clip_text_encode": "CLIPTextEncode" in node_set,
        },
    }


def node_gate_for_support(support: dict[str, Any], available_nodes: Any = None) -> dict[str, Any]:
    """Merge route support with node readiness.

    Active routes become provider_gated unless required nodes are checked and ready.
    Non-active routes preserve their existing support state and merely attach node status.
    """
    node_status = discover_node_status(available_nodes)
    state = support.get("state")
    gated = dict(support)
    gated["node_status"] = node_status
    gated["node_availability_checked"] = node_status["checked"]
    gated["node_availability_ready"] = node_status["ready"]
    gated["node_availability_phase"] = PHASE

    if state in {"available", "experimental_available"} and not node_status["ready"]:
        gated["pre_node_state"] = state
        gated["state"] = "provider_gated"
        gated["reason_code"] = node_status["reason_code"]
        gated["reason"] = node_status["reason"]
        gated["workflow_patch_allowed"] = False
        gated["parameter_visible"] = False
        gated["parameter_profile"] = "diagnostic_only"
        gated["workflow_patch_profile"] = "none"
    elif state in {"available", "experimental_available"} and node_status["ready"]:
        gated["reason_code"] = "nodes_ready"
        gated["reason"] = node_status["reason"]
        gated["workflow_patch_allowed"] = bool(support.get("workflow_patch_allowed"))
        gated["parameter_visible"] = bool(support.get("parameter_visible"))
    return gated
