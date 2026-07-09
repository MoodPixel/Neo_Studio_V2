from __future__ import annotations
from typing import Any
from .node_discovery import inspect_nodes
from .payload_schema import EXTENSION_ID, normalize_block
from .support_matrix import ACTIVE_STATES, route_reason, route_state


def _note(level: str, field: str, message: str, **extra: Any) -> dict[str, Any]:
    row = {"level": level, "field": field, "message": message}
    row.update(extra)
    return row


def validate_and_normalize_payload(raw_payload: dict[str, Any] | None, *, backend: str = "comfyui", family: str = "sdxl", loader: str = "checkpoint", workflow_mode: str = "generate", object_info: Any = None, require_assets: bool = True) -> dict[str, Any]:
    state = route_state(backend, family, loader, workflow_mode)
    route = {"backend": backend, "family": family, "loader": loader, "workflow_mode": "generate" if workflow_mode == "txt2img" else workflow_mode, "route_state": state}
    block, notes = normalize_block(raw_payload, route=route)
    notes = [dict(n) for n in notes]
    if not block.get("enabled"):
        return {"ok": True, "enabled": False, "state": state, "reason": block.get("metadata", {}).get("reason", "disabled"), "route": route, "validation": notes, "node_status": {}, "block": block, "active_units": [], "extension_id": EXTENSION_ID}
    units = block.get("inputs", {}).get("units") or []
    if state not in ACTIVE_STATES:
        return {"ok": False, "enabled": False, "state": state, "reason": route_reason(state), "route": route, "validation": notes + [_note("error", "route", route_reason(state))], "node_status": {}, "block": {"enabled": False, "version": block.get("version", 1), "inputs": {}, "params": {}, "assets": {}, "metadata": {**block.get("metadata", {}), "reason": route_reason(state)}}, "active_units": [], "extension_id": EXTENSION_ID}
    node_status = inspect_nodes(object_info)
    errors = [n for n in notes if n.get("level") == "error"]
    warnings = [n for n in notes if n.get("level") == "warning"]
    if not units:
        errors.append(_note("error", "inputs.units", "IP Adapter is enabled but no active units were provided."))
    has_standard = any(str(u.get("mode") or "standard") != "faceid" for u in units)
    has_faceid = any(str(u.get("mode") or "standard") == "faceid" for u in units)
    if has_standard and not node_status.get("standard_available"):
        errors.append(_note("error", "nodes.standard", "Standard IP Adapter nodes are missing.", missing=node_status.get("standard_missing") or []))
    if has_faceid and not node_status.get("faceid_available"):
        errors.append(_note("error", "nodes.faceid", "FaceID IP Adapter nodes are missing.", missing=node_status.get("faceid_missing") or []))
    for idx, unit in enumerate(units):
        prefix = f"inputs.units[{idx}]"
        if str(unit.get("mode") or "standard") != "faceid" and not unit.get("model"):
            errors.append(_note("error", f"{prefix}.model", "Standard IP Adapter unit requires an IP Adapter model."))
        if not unit.get("clip_vision"):
            errors.append(_note("error", f"{prefix}.clip_vision", "IP Adapter unit requires a CLIP Vision model."))
        if require_assets and not unit.get("image_names") and not unit.get("image_name"):
            errors.append(_note("error", f"{prefix}.image", "IP Adapter unit requires at least one reference image."))
    provider_node_errors = [e for e in errors if str(e.get("field") or "").startswith("nodes.")]
    result_state = "provider_gated" if provider_node_errors else state
    reason = "validated" if not errors else (provider_node_errors[0].get("message") if provider_node_errors else "validation_failed")
    active = units if not errors else []
    if errors:
        block = {"enabled": False, "version": block.get("version", 1), "inputs": {}, "params": {}, "assets": {}, "metadata": {**(block.get("metadata") or {}), "reason": reason, "route": route, "route_state": result_state}}
    return {"ok": not errors, "enabled": not errors, "state": result_state, "reason": reason, "route": route, "validation": notes + warnings + [e for e in errors if e not in notes], "node_status": node_status, "block": block, "active_units": active, "extension_id": EXTENSION_ID}
