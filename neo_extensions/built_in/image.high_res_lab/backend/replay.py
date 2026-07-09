from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .constants import EXTENSION_ID, EXTENSION_TYPE, WORKSPACE_APP
from .payload_schema import disabled_block, normalize_block
from .validation import validate_high_res_lab_payload

RESTORE_POLICY = "revalidate_route_nodes_high_res_lab_before_enable"


def _metadata(block: dict[str, Any]) -> dict[str, Any]:
    return block.get("metadata") if isinstance(block.get("metadata"), dict) else {}


def _is_block(value: Any) -> bool:
    return isinstance(value, dict) and (
        "enabled" in value or "params" in value or "inputs" in value or "assets" in value
    )


def extract_replay_block(value: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract a High-Res Lab block from either a direct block or an output reuse payload.

    Accepted shapes:
    - {enabled, params, assets, metadata}
    - {extensions: {image.high_res_lab: block}}
    - {extensions: {payloads: {image.high_res_lab: block}}}
    - {extensions: {replay_payloads: {image.high_res_lab: block}}}
    """
    source = value if isinstance(value, dict) else {}
    if _is_block(source):
        return deepcopy(source)
    extensions = source.get("extensions") if isinstance(source.get("extensions"), dict) else {}
    if _is_block(extensions.get(EXTENSION_ID)):
        return deepcopy(extensions[EXTENSION_ID])
    payloads = extensions.get("payloads") if isinstance(extensions.get("payloads"), dict) else {}
    replay_payloads = extensions.get("replay_payloads") if isinstance(extensions.get("replay_payloads"), dict) else {}
    if _is_block(replay_payloads.get(EXTENSION_ID)):
        return deepcopy(replay_payloads[EXTENSION_ID])
    if _is_block(payloads.get(EXTENSION_ID)):
        return deepcopy(payloads[EXTENSION_ID])
    nested = replay_payloads.get("extensions") if isinstance(replay_payloads.get("extensions"), dict) else {}
    if _is_block(nested.get(EXTENSION_ID)):
        return deepcopy(nested[EXTENSION_ID])
    return {}


def safe_replay_block(
    block: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: Iterable[str] | dict[str, Any] | None = None,
    enforce_route_state: bool = True,
) -> dict[str, Any]:
    """Return a route-safe block suitable for output replay/reuse.

    The block is never trusted as-is. It is normalized and revalidated. If route/node
    requirements fail, the restored block is disabled but keeps restore metadata for UI review.
    """
    source = extract_replay_block(block)
    requested_enabled = bool(source.get("enabled"))
    if not source:
        safe = disabled_block("No High-Res Lab replay payload was found.", route=route)
    else:
        validation = validate_high_res_lab_payload(
            source,
            route=route,
            available_nodes=available_nodes,
            strict=False,
        )
        safe = deepcopy(validation.get("block") if isinstance(validation.get("block"), dict) else {})
        if not safe:
            safe = normalize_block(source, route=route, enforce_route_state=enforce_route_state)
        if requested_enabled and not validation.get("workflow_patch_allowed"):
            safe = disabled_block(validation.get("reason") or "Replay requires route/node revalidation before enable.", route=route)
    meta = _metadata(safe)
    safe["metadata"] = {
        **meta,
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "safe_replay": True,
        "replay_ready": True,
        "revalidation_required": True,
        "restore_policy": RESTORE_POLICY,
        "restore_state": "restored_for_backend_revalidation" if safe.get("enabled") else "restored_gated_for_review",
    }
    return safe


def replay_payload(
    block: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: Iterable[str] | dict[str, Any] | None = None,
    enforce_route_state: bool = True,
) -> dict[str, Any]:
    safe = safe_replay_block(block, route=route, available_nodes=available_nodes, enforce_route_state=enforce_route_state)
    params = deepcopy(safe.get("params") if isinstance(safe.get("params"), dict) else {})
    assets = deepcopy(safe.get("assets") if isinstance(safe.get("assets"), dict) else {})
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": deepcopy(route or {}),
        "assets": assets,
        "params": params,
        "outputs": {},
        "workflow_summary": "High-Res Lab replay payload is route-safe and requires backend revalidation before reuse.",
        "assistant_summary": "High-Res Lab settings can be reused after route/node revalidation.",
        "replay_payload": {"extensions": {EXTENSION_ID: safe}},
    }


def restore_settings_from_replay(value: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return UI-draft settings from a safe replay block without enabling unsafe routes."""
    safe = safe_replay_block(value, route=route)
    params = deepcopy(safe.get("params") if isinstance(safe.get("params"), dict) else {})
    meta = _metadata(safe)
    return {
        **params,
        "enabled": bool(safe.get("enabled")),
        "restored_from_output": meta.get("restored_from_output", ""),
        "restore_policy": meta.get("restore_policy") or RESTORE_POLICY,
        "restore_state": meta.get("restore_state") or ("restored_for_backend_revalidation" if safe.get("enabled") else "restored_gated_for_review"),
    }
