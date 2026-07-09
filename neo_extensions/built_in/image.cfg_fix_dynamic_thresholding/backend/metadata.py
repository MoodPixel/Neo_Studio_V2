from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_ID = "cfg_fix_dynamic_thresholding"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "generations"


def _clean_route(route: dict | None = None) -> dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    return {
        "backend": route.get("backend") or route.get("provider") or "",
        "family": route.get("family") or "",
        "loader": route.get("loader") or "",
        "mode": route.get("mode") or route.get("workflow_mode") or "generate",
        "workflow_mode": route.get("workflow_mode") or route.get("mode") or "generate",
        "workspace_app": route.get("workspace_app") or WORKSPACE_APP,
        "route_state": route.get("route_state") or route.get("state") or "unknown",
    }


def _extension_block(validation_result: dict[str, Any] | None = None) -> dict[str, Any]:
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    block = validation_result.get("block") if isinstance(validation_result.get("block"), dict) else {}
    return deepcopy(block)


def _patch_applied(workflow_patch: dict[str, Any] | None = None) -> bool:
    return bool(isinstance(workflow_patch, dict) and workflow_patch.get("applied"))


def assistant_summary_from_payload(
    block: dict[str, Any] | None = None,
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> str:
    """Build a compact user/Assistant-readable summary for Output Inspector and memory."""
    block = block if isinstance(block, dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    clean_route = _clean_route(route)
    if not block.get("enabled"):
        reason = metadata.get("reason") or (workflow_patch or {}).get("reason") or "disabled or gated"
        return f"CFG Fix / Dynamic Thresholding was not applied ({reason})."
    mode = params.get("mode") or "simple"
    preset = params.get("preset") or "custom"
    mimic = params.get("mimic_scale")
    percentile = params.get("threshold_percentile")
    node = metadata.get("node") or (workflow_patch or {}).get("node") or (workflow_patch or {}).get("node_class") or ("DynamicThresholdingFull" if mode == "full" else "DynamicThresholdingSimple")
    applied = "applied" if _patch_applied(workflow_patch) else "validated"
    bits = [f"CFG Fix / Dynamic Thresholding {applied}", f"{mode} mode", f"preset {preset}"]
    if mimic is not None and percentile is not None:
        bits.append(f"mimic {mimic} / threshold {percentile}")
    if node:
        bits.append(f"node {node}")
    route_label = "+".join(str(clean_route.get(key) or "") for key in ("backend", "family", "loader", "workflow_mode") if clean_route.get(key))
    if route_label:
        bits.append(route_label)
    return "; ".join(bits) + "."


def replay_payload_from_block(block: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return extension replay data that must be revalidated before reuse."""
    block = block if isinstance(block, dict) else {}
    safe_block = {
        "enabled": bool(block.get("enabled")),
        "version": block.get("version", 1),
        "inputs": {},
        "params": deepcopy(block.get("params") if isinstance(block.get("params"), dict) else {}),
        "assets": {},
        "metadata": deepcopy(block.get("metadata") if isinstance(block.get("metadata"), dict) else {}),
    }
    if not safe_block["enabled"]:
        safe_block["params"] = {}
        safe_block["inputs"] = {}
        safe_block["assets"] = {}
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": _clean_route(route),
        "payload": safe_block,
        "restore_policy": "revalidate_before_enable",
        "revalidate_keys": ["backend", "family", "loader", "workflow_mode", "route_state", "node_availability", "cfg"],
    }


def build_output_extension_metadata(
    validation_result: dict[str, Any] | None = None,
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the finalized output-record extension metadata block for Phase F."""
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    workflow_patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    block = _extension_block(validation_result)
    clean_route = _clean_route(route)
    enabled = bool(block.get("enabled"))
    applied = bool(workflow_patch.get("applied"))
    status = "used" if applied else ("validated" if enabled else "disabled")
    usage = {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "status": status,
        "enabled": enabled,
        "workflow_patch_applied": applied,
        "route_state": clean_route.get("route_state"),
        "label": "CFG Fix / Dynamic Thresholding",
    }
    if workflow_patch.get("node") or workflow_patch.get("node_class"):
        usage["node"] = workflow_patch.get("node") or workflow_patch.get("node_class")
    elif isinstance(block.get("metadata"), dict) and block["metadata"].get("node"):
        usage["node"] = block["metadata"].get("node")
    if workflow_patch.get("reason"):
        usage["reason"] = workflow_patch.get("reason")
    elif isinstance(block.get("metadata"), dict) and block["metadata"].get("reason"):
        usage["reason"] = block["metadata"].get("reason")

    patch = deepcopy(workflow_patch) if workflow_patch else {
        "extension_id": EXTENSION_ID,
        "applied": False,
        "reason": (block.get("metadata") or {}).get("reason", "No workflow patch recorded."),
    }
    validation = deepcopy(validation_result.get("validation") or [])
    return {
        "used": [usage],
        "payloads": {EXTENSION_ID: deepcopy(block)},
        "workflow_patches": [patch],
        "validation": validation,
        "assistant_summary": assistant_summary_from_payload(block, workflow_patch=workflow_patch, route=clean_route),
        "replay_payloads": {EXTENSION_ID: replay_payload_from_block(block, route=clean_route)},
    }


def memory_readiness_shape(route: dict | None = None, params: dict | None = None, *, validation: list[dict[str, Any]] | None = None, replay_payload: dict | None = None, outputs: dict | None = None) -> dict:
    active = bool(params)
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": _clean_route(route),
        "assets": {},
        "params": params or {},
        "outputs": outputs or {},
        "workflow_summary": "CFG Fix / Dynamic Thresholding validates the route and, when available, patches the Comfy MODEL path before sampling.",
        "assistant_summary": "CFG Fix / Dynamic Thresholding was {} for the active Generations route.".format("recorded" if active else "not active"),
        "validation": validation or [],
        "replay_payload": replay_payload or {},
    }


def output_metadata_preview(validation_result: dict[str, Any]) -> dict[str, Any]:
    """Phase-D-compatible preview: payload/validation only, no synthetic patch."""
    metadata = build_output_extension_metadata(validation_result)
    metadata["workflow_patches"] = []
    return {"extensions": metadata}
