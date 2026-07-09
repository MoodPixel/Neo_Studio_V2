from __future__ import annotations

from copy import deepcopy
from typing import Any

from .node_discovery import node_gate_reason, node_status as build_node_status
from .payload_schema import (
    ACTIVE_ROUTE_STATES,
    EXTENSION_ID,
    LOW_CFG_SKIP_THRESHOLD,
    active_block,
    disabled_block,
    normalize_params,
    payload_wrapper,
)
from .support_matrix import route_state as declared_route_state


def _route_context(route: dict[str, Any] | None) -> dict[str, Any]:
    route = dict(route or {})
    backend = route.get("backend") or route.get("provider_id") or "comfyui"
    family = route.get("family") or "sdxl"
    loader = route.get("loader") or "checkpoint"
    mode = route.get("workflow_mode") or route.get("mode") or "generate"
    state = route.get("route_state") or declared_route_state(str(backend), str(family), str(loader), str(mode))
    return {
        "backend": str(backend),
        "family": str(family),
        "loader": str(loader),
        "workflow_mode": str(mode),
        "route_key": route.get("route_key") or f"{family}:{loader}:{mode}",
        "route_state": str(state),
        "reason": route.get("reason") or "",
    }


def _raw_extension_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if EXTENSION_ID in payload:
        return payload.get(EXTENSION_ID) or {}
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return payloads.get(EXTENSION_ID) or {}
    extensions = payload.get("extensions")
    if isinstance(extensions, dict):
        return extensions.get(EXTENSION_ID) or {}
    return payload


def validate_phase_b_params(params: dict | None) -> list[str]:
    normalized, notes = normalize_params(params or {})
    _ = normalized
    return [note["message"] for note in notes if note.get("level") == "error"]


def validate_and_normalize_payload(
    payload: dict[str, Any] | None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None = None,
    cfg: float | int | str = 7.0,
) -> dict[str, Any]:
    """Return the Phase D server-side payload decision for this extension.

    This function is intentionally graph-neutral. It only decides whether an active,
    clean payload may proceed to Phase E workflow patching.
    """
    raw_block = _raw_extension_block(payload)
    requested_enabled = bool(raw_block.get("enabled", False))
    raw_params = raw_block.get("params") if isinstance(raw_block.get("params"), dict) else {}
    route_ctx = _route_context(route)
    nodes = build_node_status(available_nodes)
    clean_params, validation = normalize_params(raw_params, cfg=cfg)

    def finish(block: dict[str, Any], *, ok: bool, active: bool, extra_validation: list[dict[str, str]] | None = None) -> dict[str, Any]:
        all_validation = [*validation, *(extra_validation or [])]
        errors = [item["message"] for item in all_validation if item.get("level") == "error"]
        warnings = [item["message"] for item in all_validation if item.get("level") == "warning"]
        return {
            "ok": ok and not errors,
            "active": active,
            "extension_id": EXTENSION_ID,
            "payload": payload_wrapper(block),
            "block": block,
            "params": deepcopy(block.get("params", {})),
            "route": route_ctx,
            "node_status": nodes,
            "validation": all_validation,
            "errors": errors,
            "warnings": warnings,
            "workflow_patch_allowed": active and ok and not errors,
        }

    if not requested_enabled or clean_params.get("preset") == "off":
        return finish(
            disabled_block("disabled", route=route_ctx, node_status=nodes, requested=clean_params),
            ok=True,
            active=False,
        )

    if route_ctx["route_state"] not in ACTIVE_ROUTE_STATES:
        reason = route_ctx.get("reason") or f"Route gated: {route_ctx['route_state']}"
        return finish(
            disabled_block(reason, route=route_ctx, node_status=nodes, requested=clean_params),
            ok=True,
            active=False,
            extra_validation=[{"level": "warning", "field": "route_state", "message": reason}],
        )

    gate_reason = node_gate_reason(nodes, requested_mode=clean_params.get("mode", "simple"))
    if gate_reason:
        return finish(
            disabled_block(gate_reason, route=route_ctx, node_status=nodes, requested=clean_params),
            ok=True,
            active=False,
            extra_validation=[{"level": "warning", "field": "nodes", "message": gate_reason}],
        )

    try:
        cfg_value = float(cfg)
    except (TypeError, ValueError):
        cfg_value = 7.0
    if clean_params.get("auto_disable_low_cfg") and cfg_value <= LOW_CFG_SKIP_THRESHOLD:
        reason = "Skipped: low CFG"
        return finish(
            disabled_block(reason, route=route_ctx, node_status=nodes, requested=clean_params),
            ok=True,
            active=False,
            extra_validation=[{"level": "info", "field": "cfg", "message": reason}],
        )

    return finish(active_block(clean_params, route=route_ctx, node_status=nodes), ok=True, active=True)


def validate_payload_block_shape(block: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for key in ("enabled", "version", "inputs", "params", "assets", "metadata"):
        if key not in block:
            errors.append(f"CFG Fix payload block missing key: {key}")
    if block.get("enabled") is False:
        for key in ("inputs", "params", "assets"):
            if block.get(key):
                errors.append(f"Disabled CFG Fix payload must not carry active {key}.")
    return errors
