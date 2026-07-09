from __future__ import annotations

from copy import deepcopy
from typing import Any

from .payload_schema import (
    ACTIVE_ROUTE_STATES,
    EXTENSION_ID,
    active_block,
    disabled_block,
    normalize_lora_rows,
    payload_wrapper,
    sanitize_block,
    validate_payload_block_shape,
)
from .support_matrix import route_state as declared_route_state, support_reason


def _route_context(route: dict[str, Any] | None) -> dict[str, Any]:
    route = dict(route or {})
    backend = route.get("backend") or route.get("provider_id") or "comfyui"
    family = route.get("family") or "sdxl"
    loader = route.get("loader") or "checkpoint"
    mode = route.get("workflow_mode") or route.get("mode") or "generate"
    state = route.get("route_state") or declared_route_state(str(backend), str(family), str(loader), str(mode))
    reason = route.get("reason") or support_reason(str(backend), str(family), str(loader), str(mode))
    return {
        "backend": str(backend),
        "family": str(family),
        "loader": str(loader),
        "workflow_mode": str(mode),
        "mode": str(mode),
        "route_key": route.get("route_key") or f"{family}:{loader}:{mode}",
        "route_state": str(state),
        "reason": str(reason or ""),
    }


def _raw_extension_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    if EXTENSION_ID in payload and isinstance(payload.get(EXTENSION_ID), dict):
        return payload.get(EXTENSION_ID) or {}
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return payloads.get(EXTENSION_ID) or {}
    extensions = payload.get("extensions")
    if isinstance(extensions, dict) and isinstance(extensions.get(EXTENSION_ID), dict):
        return extensions.get(EXTENSION_ID) or {}
    return {}


def _note(level: str, field: str, message: str) -> dict[str, str]:
    return {"level": level, "field": field, "message": message}


def _row_validation_notes(raw_rows: list[Any], clean_rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    notes: list[dict[str, str]] = []
    raw_count = len(raw_rows)
    if raw_count and raw_count != len(clean_rows):
        notes.append(_note("info", "params.loras", f"Normalized LoRA rows from {raw_count} to {len(clean_rows)} active row(s). Disabled, duplicate, or empty rows were stripped."))
    for row in clean_rows:
        if str(row.get("apply_to") or "") != "global":
            notes.append(_note("warning", "apply_to", "Regional LoRA targeting is preserved in payload but workflow graph patching requires a validated Scene Director route."))
        if row.get("target") == "finish":
            notes.append(_note("warning", "target", "Finish-pass LoRA targeting is preserved in payload and will be applied only when a validated finish compiler path exists."))
    return notes


def validate_and_normalize_payload(
    payload: dict[str, Any] | None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None = None,
    cfg: float | int | str = 7.0,
) -> dict[str, Any]:
    """Return the Phase D server-side payload decision for LoRA Stack.

    This is intentionally graph-neutral. It sanitizes the browser payload, strips stale
    hidden fields, gates unsupported routes, and returns the clean block that a future
    workflow patch may consume.
    """
    _ = available_nodes, cfg  # LoRA Stack has no custom-node dependency in Phase D.
    raw_block = _raw_extension_block(payload)
    requested_enabled = bool(raw_block.get("enabled", False))
    raw_params = raw_block.get("params") if isinstance(raw_block.get("params"), dict) else {}
    raw_assets = raw_block.get("assets") if isinstance(raw_block.get("assets"), dict) else {}
    raw_rows = raw_params.get("loras") if isinstance(raw_params.get("loras"), list) else []
    clean_rows = normalize_lora_rows(raw_rows)
    route_ctx = _route_context(route)

    validation: list[dict[str, str]] = []
    validation.extend(_row_validation_notes(raw_rows, clean_rows))
    errors: list[str] = []
    warnings: list[str] = []
    active = False

    def finish(block: dict[str, Any], *, ok: bool, active_flag: bool) -> dict[str, Any]:
        shape_errors = validate_payload_block_shape(block)
        all_validation = [*validation]
        if shape_errors:
            all_validation.extend(_note("error", "block", item) for item in shape_errors)
        error_messages = [item["message"] for item in all_validation if item.get("level") == "error"]
        warning_messages = [item["message"] for item in all_validation if item.get("level") == "warning"]
        return {
            "ok": ok and not error_messages,
            "active": active_flag and not error_messages,
            "extension_id": EXTENSION_ID,
            "payload": payload_wrapper(block),
            "block": block,
            "params": deepcopy(block.get("params", {})),
            "assets": deepcopy(block.get("assets", {})),
            "route": route_ctx,
            "node_status": {"required": [], "available": True, "reason": "LoRA Stack uses standard Comfy LoraLoader when the compiler route supports it."},
            "validation": all_validation,
            "errors": error_messages,
            "warnings": warning_messages,
            "workflow_patch_allowed": active_flag and ok and not error_messages,
        }

    if not requested_enabled:
        return finish(disabled_block("disabled", route=route_ctx, requested_rows=clean_rows), ok=True, active_flag=False)

    if not clean_rows:
        validation.append(_note("info", "params.loras", "LoRA Stack was enabled but no valid active LoRA rows were selected."))
        return finish(disabled_block("no_valid_lora_rows", route=route_ctx, requested_rows=clean_rows), ok=True, active_flag=False)

    if route_ctx["route_state"] not in ACTIVE_ROUTE_STATES:
        reason = route_ctx.get("reason") or f"Route gated: {route_ctx['route_state']}"
        validation.append(_note("warning", "route_state", reason))
        if clean_rows:
            validation.append(_note("info", "metadata.requested.loras", "LoRA rows are preserved as requested intent metadata while the direct route is gated."))
        return finish(disabled_block(reason, route=route_ctx, requested_rows=clean_rows), ok=True, active_flag=False)

    block = active_block(clean_rows, route=route_ctx, assets=raw_assets)
    # Re-sanitize once more to enforce the exact Phase D extension block contract.
    block = sanitize_block(block, route=route_ctx)
    active = bool(block.get("enabled") and block.get("params", {}).get("loras"))
    return finish(block, ok=True, active_flag=active)


def validate_payload(payload: dict[str, Any], route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compatibility wrapper used by Phase B/C tests and the no-op workflow hook."""
    result = validate_and_normalize_payload(payload, route=route)
    return {
        "ok": result["ok"],
        "active": result["active"],
        "errors": result["errors"],
        "warnings": result["warnings"],
        "loras": result.get("params", {}).get("loras", []),
        "block": result.get("block", {}),
        "payload": result.get("payload", {}),
        "validation": result.get("validation", []),
        "workflow_patch_allowed": result.get("workflow_patch_allowed", False),
    }
