from __future__ import annotations

from typing import Any

from .constants import ACTIVE_ROUTE_STATES, EXTENSION_ID, PHASE
from .node_discovery import node_gate_for_support
from .payload_schema import normalize_block
from .support_matrix import support_for_route


def _block_enabled(block: dict[str, Any]) -> bool:
    return bool(block.get("enabled") or block.get("params", {}).get("enabled") or block.get("inputs", {}).get("enabled"))


def _validation_item(level: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"extension_id": EXTENSION_ID, "level": level, "code": code, "message": message, **extra}


def validate_and_normalize_payload(payload: Any, *, route: dict[str, Any] | None = None, available_nodes: Any = None) -> dict[str, Any]:
    block = normalize_block(payload)
    raw_support = support_for_route(route)
    support = node_gate_for_support(raw_support, available_nodes)
    enabled = _block_enabled(block)
    params = block.get("params", {})
    node_status = support.get("node_status", {})
    normalization = block.get("metadata", {}).get("normalization", {})

    validations: list[dict[str, Any]] = []
    warnings = list(normalization.get("warnings", []))
    ignored_params = list(normalization.get("ignored_params", []))
    clamped_params = list(normalization.get("clamped_params", []))

    if ignored_params:
        validations.append(_validation_item(
            "warning",
            "adetailer_stale_fields_removed",
            "Ignored ADetailer fields were removed from the clean payload.",
            ignored_params=ignored_params,
        ))
    if clamped_params:
        validations.append(_validation_item(
            "warning",
            "adetailer_params_clamped",
            "One or more ADetailer numeric values were clamped to safe V1-compatible limits.",
            clamped_params=clamped_params,
        ))
    for warning in warnings:
        validations.append(_validation_item(
            "warning",
            f"adetailer_{warning}",
            f"ADetailer payload normalization warning: {warning}.",
        ))

    pass_count = int(normalization.get("detailer_pass_count") or 0)
    enabled_pass_count = int(normalization.get("enabled_detailer_pass_count") or 0)
    if pass_count > 1:
        validations.append(_validation_item(
            "info",
            "adetailer_multi_pass_payload_ready",
            "ADetailer multi-pass payload was normalized as first-class runtime data.",
            detailer_pass_count=pass_count,
            enabled_detailer_pass_count=enabled_pass_count,
            primary_runtime_pass_id=normalization.get("primary_runtime_pass_id"),
        ))

    if enabled and enabled_pass_count == 0:
        validations.append(_validation_item(
            "warning",
            "adetailer_no_enabled_detailer_passes",
            "ADetailer is requested but all detailer passes are disabled; no workflow mutation should run.",
            detailer_pass_count=pass_count,
        ))

    if not enabled:
        validations.append(_validation_item(
            "info",
            "adetailer_not_requested",
            "ADetailer was not requested for this run; no runtime workflow mutation is allowed.",
            route_state=support["state"],
        ))
    elif support["state"] in ACTIVE_ROUTE_STATES and node_status.get("ready"):
        validations.append(_validation_item(
            "info",
            "adetailer_payload_ready",
            "ADetailer payload is clean and route/node readiness permits a later workflow patch phase.",
            route_state=support["state"],
            node_status="ready",
        ))
    elif raw_support["state"] in ACTIVE_ROUTE_STATES and not node_status.get("ready"):
        validations.append(_validation_item(
            "warning",
            support.get("reason_code", "nodes_missing"),
            support.get("reason", "Required ADetailer nodes are not available."),
            route_state=support["state"],
            pre_node_state=support.get("pre_node_state"),
            missing_required=node_status.get("missing_required", []),
        ))
    else:
        validations.append(_validation_item(
            "warning" if support["state"] != "unsupported" else "error",
            f"adetailer_{support['state']}",
            support.get("reason", "ADetailer route is gated or unsupported."),
            route_state=support["state"],
            reason=support.get("reason"),
        ))

    runtime_ready = enabled and enabled_pass_count > 0 and support["state"] in ACTIVE_ROUTE_STATES and bool(node_status.get("ready"))
    workflow_patch_allowed = runtime_ready and bool(support.get("workflow_patch_allowed"))
    active_patch_data_allowed = workflow_patch_allowed

    return {
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "skeleton_only": False,
        "multi_pass_payload_ready": bool(normalization.get("multi_pass_payload_ready")),
        "enabled": enabled,
        "runtime_ready": runtime_ready,
        "workflow_patch_allowed": workflow_patch_allowed,
        "workflow_patch_ready_for_later_phase": False,
        "active_patch_data_allowed": active_patch_data_allowed,
        "block": block,
        "params": params,
        "derived": normalization,
        "support": support,
        "raw_support": raw_support,
        "node_status": node_status,
        "validation": validations,
    }
