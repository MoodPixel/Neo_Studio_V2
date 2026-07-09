from __future__ import annotations

from typing import Any

from .node_decision import detect_node_status
from .payload_schema import EXTENSION_ID, active_regions, normalize_scene_director_payload
from .support_matrix import ACTIVE_STATES, get_scene_director_support, normalize_route

VALIDATION_SCHEMA_VERSION = "neo.extension.validation.v1"


def _message(level: str, field: str, message: str, code: str) -> dict[str, Any]:
    return {
        "extension_id": EXTENSION_ID,
        "level": level,
        "field": field,
        "message": message,
        "code": code,
    }


def _route_from_kwargs(route: dict[str, Any] | None = None, **kwargs: Any) -> dict[str, Any]:
    base = dict(route or {})
    for key, value in kwargs.items():
        if value is not None:
            base[key] = value
    return normalize_route(base)


def _raw_scene_director_enabled(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    extensions = payload.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get(EXTENSION_ID), dict):
        return extensions[EXTENSION_ID].get("enabled") is not False
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return payloads[EXTENSION_ID].get("enabled") is not False
    direct = payload.get(EXTENSION_ID)
    if isinstance(direct, dict):
        return direct.get("enabled") is not False
    if "scene_director_enabled" in payload:
        value = payload.get("scene_director_enabled")
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "yes", "on", "enabled"}
    if "scene_director_state" in payload or "scene_director_regional_units" in payload or "regional_prompt_regions" in payload:
        return True
    return False


def _detect_duplicate_region_ids(regions: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    duplicates: list[str] = []
    for region in regions:
        rid = str(region.get("id") or "")
        if not rid:
            continue
        if rid in seen and rid not in duplicates:
            duplicates.append(rid)
        seen.add(rid)
    return duplicates


def validate_scene_director_payload(
    payload: Any,
    *,
    route: dict[str, Any] | None = None,
    node_status: Any = None,
    object_info: Any = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Validate and normalize the Scene Director extension block.

    Validation is intentionally route-aware but not destructive: unsupported or
    missing-node routes are reported as gated warnings and the normalized payload
    is already pruned so no graph mutation can occur. The only hard validation
    error in Phase I is an explicitly enabled Scene Director state with no active
    regions, because V1 treated that as a real user-facing problem rather than a
    silent plain-generation success.
    """
    normalized_route = normalize_route(route or {})
    nodes = object_info if object_info is not None else node_status
    support = get_scene_director_support(normalized_route, node_status=nodes, object_info=nodes, require_node=True)
    normalized = normalize_scene_director_payload(payload, route=normalized_route, node_status=node_status, object_info=object_info)
    block = normalized.get("extensions", {}).get(EXTENSION_ID, {})
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    regions = block.get("inputs", {}).get("regions", []) if isinstance(block.get("inputs"), dict) else []
    active = active_regions(block) if isinstance(block, dict) else []
    raw_enabled = _raw_scene_director_enabled(payload)
    enabled = bool(block.get("enabled"))
    node = detect_node_status(nodes)

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    infos: list[dict[str, Any]] = []

    if raw_enabled and metadata.get("reason") == "no_active_regions":
        errors.append(_message(
            "error",
            "inputs.regions",
            "Scene Director is enabled but has no active regions. Add a visible enabled region with a prompt or identity/reference before generation.",
            "no_active_regions",
        ))

    duplicate_ids = _detect_duplicate_region_ids(regions if isinstance(regions, list) else [])
    for rid in duplicate_ids:
        warnings.append(_message(
            "warning",
            "inputs.regions.id",
            f"Duplicate region id '{rid}' was detected after normalization; region ids should be stable and unique for replay.",
            "duplicate_region_id",
        ))

    route_state = str(metadata.get("route_state") or support.get("state") or "unsupported")
    route_compatible_state = str(support.get("route_state") or route_state)
    if raw_enabled and route_state not in ACTIVE_STATES:
        warnings.append(_message(
            "warning",
            "route",
            str(metadata.get("gated_reason") or support.get("reason") or "Scene Director is gated for this route."),
            f"route_{route_state}",
        ))

    if raw_enabled and route_compatible_state in ACTIVE_STATES and not node.get("available"):
        warnings.append(_message(
            "warning",
            "node_status",
            str(node.get("missing_reason") or "Scene Director node is missing."),
            "missing_scene_director_node",
        ))

    if metadata.get("warnings"):
        for item in metadata.get("warnings") or []:
            infos.append(_message("info", "metadata.warnings", str(item), "payload_normalization_note"))

    if metadata.get("workflow_patch_requested") and not metadata.get("workflow_patch_allowed"):
        warnings.append(_message(
            "warning",
            "metadata.workflow_patch_allowed",
            str(metadata.get("gated_reason") or "Scene Director workflow patch is not allowed for this route/node state."),
            "workflow_patch_gated",
        ))

    if strict and raw_enabled and route_state in {"unsupported", "planned_gated"}:
        errors.append(_message(
            "error",
            "route",
            str(metadata.get("gated_reason") or support.get("reason") or "Scene Director is not supported for this route."),
            "strict_route_gated",
        ))

    ok = not errors
    result = {
        "extension_id": EXTENSION_ID,
        "schema": VALIDATION_SCHEMA_VERSION,
        "ok": ok,
        "enabled": enabled,
        "raw_enabled": raw_enabled,
        "normalized_payload": normalized,
        "block": block,
        "route": normalized_route,
        "support": support,
        "route_state": route_state,
        "route_compatible_state": route_compatible_state,
        "node_status": node,
        "workflow_patch_requested": bool(metadata.get("workflow_patch_requested")),
        "workflow_patch_allowed": bool(metadata.get("workflow_patch_allowed")),
        "can_emit_workflow_patch": bool(metadata.get("workflow_patch_requested") and metadata.get("workflow_patch_allowed") and ok),
        "gated_reason": str(metadata.get("gated_reason") or support.get("reason") or ""),
        "errors": errors,
        "warnings": warnings,
        "infos": infos,
        "active_regions": active,
        "regional_count": int(metadata.get("regional_count") or len(active) or 0),
        "subject_count": int(metadata.get("subject_count") or 0),
        "detail_region_count": int(metadata.get("detail_region_count") or 0),
    }
    return result


def validate_and_normalize_payload(
    payload: Any,
    *,
    backend: str = "comfyui",
    family: str = "sdxl",
    loader: str = "checkpoint",
    workflow_mode: str = "generate",
    object_info: Any = None,
    node_status: Any = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Compatibility wrapper consumed by workflow hooks from earlier phases."""
    route = _route_from_kwargs(
        backend=backend,
        family=family,
        loader=loader,
        workflow_mode=workflow_mode,
    )
    result = validate_scene_director_payload(
        payload,
        route=route,
        object_info=object_info,
        node_status=node_status,
        strict=strict,
    )
    return {
        "extension_id": EXTENSION_ID,
        "enabled": result["enabled"],
        "ok": result["ok"],
        "block": result["block"],
        "normalized_payload": result["normalized_payload"],
        "validation": result["errors"] + result["warnings"] + result["infos"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "infos": result["infos"],
        "route": result["route"],
        "route_state": result["route_state"],
        "reason": result["gated_reason"],
        "active_regions": result["active_regions"],
        "workflow_patch_requested": result["workflow_patch_requested"],
        "workflow_patch_allowed": result["workflow_patch_allowed"],
        "can_emit_workflow_patch": result["can_emit_workflow_patch"],
        "node_status": result["node_status"],
    }
