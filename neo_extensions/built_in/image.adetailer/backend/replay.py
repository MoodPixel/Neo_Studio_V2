from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .constants import ACTIVE_ROUTE_STATES, EXTENSION_ID, EXTENSION_TYPE, PHASE, WORKSPACE_APP
from .payload_schema import default_payload_block, normalize_block
from .support_matrix import support_for_route
from .validation import validate_and_normalize_payload
from .readiness import build_replay_readiness, summarize_replay_readiness
from .execution_recipe import (
    REPLAY_CONTRACT_SCHEMA_ID,
    build_locked_replay_block,
    validate_execution_recipe,
)


def _extract_replay_block(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    src = metadata if isinstance(metadata, dict) else {}
    direct_extensions = src.get("extensions") if isinstance(src.get("extensions"), dict) else {}
    if isinstance(direct_extensions.get(EXTENSION_ID), dict):
        return direct_extensions[EXTENSION_ID]
    if isinstance(src.get(EXTENSION_ID), dict):
        return src[EXTENSION_ID]
    if isinstance(src.get("params"), dict) and ("enabled" in src or src.get("metadata")):
        return src
    for key in ("replay_payload", "safe_replay_payload"):
        value = src.get(key)
        if isinstance(value, dict):
            if isinstance(value.get("extensions"), dict) and isinstance(value["extensions"].get(EXTENSION_ID), dict):
                return value["extensions"][EXTENSION_ID]
            if isinstance(value.get(EXTENSION_ID), dict):
                return value[EXTENSION_ID]
            if isinstance(value.get("payload"), dict):
                return value["payload"]
    payloads = src.get("replay_payloads") if isinstance(src.get("replay_payloads"), dict) else {}
    if isinstance(payloads.get(EXTENSION_ID), dict):
        return payloads[EXTENSION_ID]
    payload_container = src.get("payloads") if isinstance(src.get("payloads"), dict) else {}
    if isinstance(payload_container.get(EXTENSION_ID), dict):
        return payload_container[EXTENSION_ID]
    return default_payload_block()


def _recipe_from_block(block: Mapping[str, Any]) -> dict[str, Any]:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), Mapping) else {}
    recipe = metadata.get("execution_recipe") if isinstance(metadata.get("execution_recipe"), Mapping) else {}
    return deepcopy(dict(recipe))


def build_replay_payload(metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a disabled, recipe-locked ADetailer replay payload.

    Phase 9 restores the exact effective detailer recipe instead of re-resolving
    mutable family preset defaults. The result remains disabled until the target
    route, nodes, detector catalog, LoRAs, identity policy, and warnings are
    revalidated against the current backend.
    """
    raw = _extract_replay_block(metadata)
    block = normalize_block({"extensions": {EXTENSION_ID: raw}})
    recipe = _recipe_from_block(block)
    recipe_validation = validate_execution_recipe(recipe)
    if recipe_validation.get("ready") and recipe:
        block = build_locked_replay_block(block, recipe)
    else:
        block["enabled"] = False
        block.setdefault("params", {})["enabled"] = False
    block.setdefault("metadata", {})
    block["metadata"].update({
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "source_phase": "Phase 9",
        "replay_restore_ready": bool(recipe_validation.get("ready")),
        "revalidation_required": True,
        "replay_contract_schema_id": REPLAY_CONTRACT_SCHEMA_ID,
        "replay_recipe_locked": bool(recipe),
        "ready_to_auto_enable": False,
        "restore_policy": "restore_exact_effective_adetailer_recipe_disabled_then_revalidate_route_nodes_models_detectors_loras_identity_sampling_and_warnings",
        "recipe_validation": deepcopy(recipe_validation),
    })
    return {"extensions": {EXTENSION_ID: block}}


def restore_from_replay(
    payload: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    node_inventory: Any = None,
) -> dict[str, Any]:
    """Restore exact ADetailer recipe state, disabled pending live revalidation."""
    envelope = build_replay_payload(payload or {})
    block = deepcopy(envelope["extensions"][EXTENSION_ID])
    block["enabled"] = False
    block.setdefault("params", {})["enabled"] = False
    block.setdefault("metadata", {})
    block["metadata"].update({
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "restored_from_replay": True,
        "restore_policy": "user_confirm_after_exact_recipe_revalidation",
        "source_phase": "Phase 9",
        "restore_enabled": False,
        "ready_to_auto_enable": False,
    })

    support = support_for_route(route or {})
    enabled_probe = deepcopy(block)
    enabled_probe["enabled"] = True
    enabled_probe.setdefault("params", {})["enabled"] = True
    validation = validate_and_normalize_payload(
        {"extensions": {EXTENSION_ID: enabled_probe}},
        route=route or {},
        available_nodes=node_inventory,
    )
    readiness = build_replay_readiness(
        block,
        route=route or {},
        node_inventory=node_inventory,
        node_status=validation.get("node_status") if isinstance(validation, dict) else {},
    )
    replay_contract = validation.get("replay_contract") if isinstance(validation.get("replay_contract"), dict) else {}
    can_enable = (
        support.get("state") in ACTIVE_ROUTE_STATES
        and bool(validation.get("workflow_patch_allowed"))
        and bool(readiness.get("can_enable_after_revalidation"))
        and bool(replay_contract.get("ready", True))
    )
    reason = "" if can_enable else (
        summarize_replay_readiness(readiness)
        or validation.get("reason")
        or support.get("reason")
        or "Replay restored disabled until the exact ADetailer recipe can be revalidated for this route."
    )
    diagnostics = deepcopy(validation.get("prequeue_diagnostics") or {})
    recipe = _recipe_from_block(block)
    block["metadata"].update({
        "replay_readiness": readiness,
        "can_enable_after_revalidation": can_enable,
        "restore_enabled": False,
        "prequeue_diagnostics": diagnostics,
        "replay_contract_validation": deepcopy(replay_contract),
    })
    return {
        "extension_id": EXTENSION_ID,
        "enabled": False,
        "can_enable_after_revalidation": can_enable,
        "payload": block,
        "route": deepcopy(route or {}),
        "support": support,
        "validation": validation,
        "readiness": readiness,
        "replay_contract": deepcopy(replay_contract),
        "execution_recipe_fingerprint": str(recipe.get("fingerprint") or ""),
        "prequeue_diagnostics": diagnostics,
        "reason": reason,
    }
