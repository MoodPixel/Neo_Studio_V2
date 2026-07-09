from __future__ import annotations

from copy import deepcopy
from typing import Any

from .memory_event import build_memory_event
from .payload_schema import EXTENSION_ID
from .replay import replay_payload


def _route_state(route: dict[str, Any] | None) -> str:
    return str((route or {}).get("route_state") or (route or {}).get("state") or "unknown")


def _active_units(block: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    units = inputs.get("units") if isinstance(inputs.get("units"), list) else []
    return [unit for unit in units if isinstance(unit, dict) and unit.get("enabled", True)]


def _basename(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Keep Windows and POSIX path cleanup outside f-string expressions; Python
    # forbids backslashes inside f-string expression parts.
    return text.replace("\\", "/").rsplit("/", 1)[-1]


def _unit_label(unit: dict[str, Any]) -> str:
    mode = "FaceID" if str(unit.get("mode") or "standard") == "faceid" else "Standard"
    model_source = (unit.get("faceid_model") or unit.get("faceid_preset")) if mode == "FaceID" else unit.get("model")
    model = _basename(model_source)
    clip = _basename(unit.get("clip_vision"))
    label = mode
    if model:
        label += f" / {model}"
    if clip:
        label += f" / CLIP {clip}"
    return label


def _patch_applied(patch: dict[str, Any] | None) -> bool:
    return bool(isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")))


def _node_status_summary(node_status: dict[str, Any] | None) -> dict[str, Any]:
    status = deepcopy(node_status or {})
    return {
        "readiness_state": status.get("readiness_state") or ("ready" if status.get("standard_available") or status.get("faceid_available") else "unknown"),
        "standard_available": bool(status.get("standard_available")),
        "faceid_available": bool(status.get("faceid_available")),
        "image_batch_available": bool(status.get("image_batch_available")),
        "missing": deepcopy(status.get("missing") or {"standard": status.get("standard_missing") or [], "faceid": status.get("faceid_missing") or []}),
        "model_inputs": deepcopy(status.get("model_inputs") or {}),
    }


def _assistant_summary(*, block: dict[str, Any], patch: dict[str, Any] | None, route: dict[str, Any] | None, reason: str = "") -> str:
    enabled = bool(block.get("enabled"))
    units = _active_units(block)
    applied = _patch_applied(patch)
    state = _route_state(route)
    if not enabled:
        return f"IP Adapter was not applied ({reason or state or 'disabled'})."
    if applied:
        standard = sum(1 for unit in units if str(unit.get("mode") or "standard") != "faceid")
        faceid = len(units) - standard
        names = ", ".join(_unit_label(unit) for unit in units[:3])
        suffix = "…" if len(units) > 3 else ""
        node_classes = patch.get("node_classes") if isinstance(patch, dict) and isinstance(patch.get("node_classes"), list) else []
        node = ", ".join(str(item) for item in node_classes[:3]) or patch.get("node") or patch.get("node_class") or "IPAdapter"
        return f"IP Adapter applied {len(units)} unit(s) through {node}: {standard} standard, {faceid} FaceID{'; ' + names + suffix if names else ''}."
    return f"IP Adapter requested but not patched ({reason or state or 'not applied'})."


def build_output_extension_metadata(validation_result: dict[str, Any] | None = None, *, workflow_patch: dict[str, Any] | None = None, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build Output Inspector/replay/memory-ready metadata for IP Adapter.

    Mirrors the ControlNet metadata layer: the extension owns its compact
    inspector summary, safe replay payload, node-readiness snapshot, and
    memory/readiness event while core only stores approved extension slots.
    """
    result = validation_result or {}
    block = deepcopy(result.get("block") if isinstance(result.get("block"), dict) else {})
    patch = deepcopy(workflow_patch or {})
    route_data = deepcopy(result.get("route") if isinstance(result.get("route"), dict) else (route or {}))
    enabled = bool(block.get("enabled"))
    applied = _patch_applied(patch)
    state = _route_state(route_data)
    reason = str(result.get("reason") or patch.get("reason") or (block.get("metadata") or {}).get("reason") or "").strip()
    units = _active_units(block)
    standard_units = [unit for unit in units if str(unit.get("mode") or "standard") != "faceid"]
    faceid_units = [unit for unit in units if str(unit.get("mode") or "standard") == "faceid"]
    safe_replay = replay_payload(block, route=route_data, enforce_route_state=True).get("extensions", {}).get(EXTENSION_ID, {})
    assistant_summary = _assistant_summary(block=block, patch=patch, route=route_data, reason=reason)
    assets = deepcopy(block.get("assets") if isinstance(block.get("assets"), dict) else {})
    params = deepcopy(block.get("params") if isinstance(block.get("params"), dict) else {})
    params.setdefault("units", deepcopy(units))
    node_status = _node_status_summary(result.get("node_status") if isinstance(result.get("node_status"), dict) else {})
    outputs = {
        "workflow_patch_applied": applied,
        "workflow_nodes_added": deepcopy(patch.get("nodes_added") if isinstance(patch.get("nodes_added"), list) else patch.get("node_ids") if isinstance(patch.get("node_ids"), list) else []),
        "node_classes": deepcopy(patch.get("node_classes") if isinstance(patch.get("node_classes"), list) else []),
        "sampler_node_id": str(patch.get("sampler_node_id") or ""),
        "unit_count": len(units),
        "standard_unit_count": len(standard_units),
        "faceid_unit_count": len(faceid_units),
    }
    memory_event = build_memory_event(
        route=route_data,
        assets=assets,
        params=params,
        outputs=outputs,
        workflow_summary=assistant_summary,
        assistant_summary=assistant_summary,
        replay_payload={"extensions": {EXTENSION_ID: safe_replay}},
    )
    return {
        "used": [{
            "extension_id": EXTENSION_ID,
            "extension_type": "built_in",
            "workspace_app": "reference",
            "enabled": enabled,
            "status": "used" if applied else ("gated" if enabled else "disabled"),
            "state": state,
            "route_state": state,
            "applied": applied,
            "workflow_patch_applied": applied,
            "node": ", ".join(outputs["node_classes"]) or patch.get("node") or patch.get("node_class") or "",
            "reason": reason,
            "ip_adapter_unit_count": len(units),
            "ip_adapter_standard_count": len(standard_units),
            "ip_adapter_faceid_count": len(faceid_units),
            "node_readiness": node_status.get("readiness_state") or "unknown",
        }],
        "payloads": {EXTENSION_ID: deepcopy(block)} if block else {},
        "workflow_patches": [deepcopy(patch)] if patch else [],
        "validation": deepcopy(result.get("validation") or []),
        "replay_payloads": {EXTENSION_ID: safe_replay} if safe_replay else {},
        "assistant_summary": assistant_summary,
        "assistant_summaries": {EXTENSION_ID: assistant_summary},
        "memory_events": {EXTENSION_ID: memory_event},
        "node_status": {EXTENSION_ID: node_status},
    }
