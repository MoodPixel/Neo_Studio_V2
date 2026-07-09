from __future__ import annotations

from typing import Any

from .payload_schema import (
    ACTIVE_ROUTE_STATES as PAYLOAD_ACTIVE_ROUTE_STATES,
    ASSET_ALLOWED_KEYS,
    BLOCK_ALLOWED_KEYS,
    EXTENSION_ID,
    PARAM_ALLOWED_KEYS,
    ITEM_ALLOWED_KEYS,
    TARGET_ALIASES,
    disabled_block,
    payload_wrapper,
    raw_extension_block,
    sanitize_block,
    normalize_strength,
    normalize_target,
    validate_payload_block_shape,
)
from .support_matrix import ACTIVE_ROUTE_STATES, normalize_route, parameter_profile, route_support


def _raw_item_count(raw: dict[str, Any]) -> int:
    params = raw.get("params") if isinstance(raw, dict) else {}
    if not isinstance(params, dict):
        return 0
    raw_items = params.get("items")
    if isinstance(raw_items, list):
        return len(raw_items)
    legacy_count = 0
    for key in ("selected_tokens", "base_positive", "base_negative", "finish_positive", "finish_negative"):
        value = params.get(key)
        if isinstance(value, list):
            legacy_count += len(value)
        elif isinstance(value, str) and value.strip():
            legacy_count += len([part for part in value.replace("\n", ",").split(",") if part.strip()])
    return legacy_count


def _clean_item_count(block: dict[str, Any]) -> int:
    params = block.get("params") if isinstance(block, dict) else {}
    items = params.get("items") if isinstance(params, dict) else []
    return len(items) if isinstance(items, list) else 0


def _collect_stripped_fields(raw: dict[str, Any]) -> list[str]:
    stripped: list[str] = []
    if not isinstance(raw, dict):
        return stripped
    for key in sorted(set(raw.keys()) - set(BLOCK_ALLOWED_KEYS)):
        stripped.append(f"top_level.{key}")
    inputs = raw.get("inputs")
    if isinstance(inputs, dict):
        for key in sorted(inputs.keys()):
            stripped.append(f"inputs.{key}")
    params = raw.get("params")
    if isinstance(params, dict):
        for key in sorted(set(params.keys()) - set(PARAM_ALLOWED_KEYS)):
            stripped.append(f"params.{key}")
        raw_items = params.get("items")
        if isinstance(raw_items, list):
            for index, item in enumerate(raw_items):
                if isinstance(item, dict):
                    for key in sorted(set(item.keys()) - set(ITEM_ALLOWED_KEYS)):
                        stripped.append(f"params.items[{index}].{key}")
    assets = raw.get("assets")
    if isinstance(assets, dict):
        for key in sorted(set(assets.keys()) - set(ASSET_ALLOWED_KEYS)):
            stripped.append(f"assets.{key}")
    return stripped


def _collect_normalization_warnings(raw: dict[str, Any], block: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    if not isinstance(raw, dict):
        return warnings
    raw_count = _raw_item_count(raw)
    clean_count = _clean_item_count(block)
    if raw_count and clean_count < raw_count:
        warnings.append(f"Embeddings/TI dropped {raw_count - clean_count} invalid or duplicate chip item(s) during validation.")
    params = raw.get("params")
    if isinstance(params, dict):
        raw_items = params.get("items")
        if isinstance(raw_items, list):
            for index, item in enumerate(raw_items):
                if not isinstance(item, dict):
                    continue
                if item.get("target") is not None:
                    normalized_target = normalize_target(item.get("target"))
                    if str(item.get("target") or "").strip().lower() not in {normalized_target, *TARGET_ALIASES.keys()}:
                        warnings.append(f"Embeddings/TI normalized params.items[{index}].target to {normalized_target}.")
                raw_strength = item.get("strength", 1.0)
                try:
                    value = float(raw_strength)
                except (TypeError, ValueError):
                    warnings.append(f"Embeddings/TI normalized params.items[{index}].strength to 1.0.")
                    continue
                normalized = normalize_strength(value)
                if abs(normalized - value) > 0.0005:
                    warnings.append(f"Embeddings/TI clamped params.items[{index}].strength to {normalized}.")
    items = ((block.get("params") or {}).get("items") or []) if isinstance(block, dict) else []
    if any(isinstance(item, dict) and item.get("target") in {"finish_positive", "finish_negative"} for item in items):
        warnings.append("Embeddings/TI finish-target chips are preserved but only compile when the selected finish route validates them.")
    return warnings


def validation_contract() -> dict[str, Any]:
    return {
        "phase": "I",
        "extension_id": EXTENSION_ID,
        "route_policy": "support_matrix_required_before_active_payload",
        "shape_policy": "unsupported_keys_reported_and_clean_block_strips_them",
        "stale_field_policy": "report_stripped_fields_and_disable_on_gated_routes",
        "semantic_policy": "normalize_tokens_targets_strengths_and_warn_on_dropped_items",
        "workflow_policy": "workflow_patch_allowed_only_for_active_routes_with_classic_ti_prompt_token_append",
        "node_policy": "non_node_based_no_required_nodes",
    }


def validate_and_normalize_payload(
    payload: dict[str, Any] | None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: Any = None,
) -> dict[str, Any]:
    ctx = normalize_route(route)
    support = route_support(ctx["backend"], ctx["family"], ctx["loader"], ctx["workflow_mode"])
    profile = parameter_profile(ctx["backend"], ctx["family"], ctx["loader"], ctx["workflow_mode"])
    route_payload = {
        **ctx,
        "route_state": support["state"],
        "reason": support["reason"],
        "prompt_patch": support["prompt_patch"],
        "parameter_profile": support["parameter_profile"],
    }

    raw = raw_extension_block(payload)
    errors = validate_payload_block_shape(raw) if raw else []
    block = sanitize_block(raw, route=route_payload) if raw else disabled_block("missing_payload", route=route_payload)

    stripped_fields = _collect_stripped_fields(raw) if raw else []
    warnings: list[str] = []
    warnings.extend(_collect_normalization_warnings(raw, block) if raw else [])
    if stripped_fields:
        warnings.append("Embeddings/TI stripped unsupported or stale payload fields from the clean block.")

    if support["state"] not in ACTIVE_ROUTE_STATES and block.get("enabled"):
        warnings.append("Embeddings/TI active prompt payload stripped because the selected route is gated.")
        block = disabled_block(support["reason"], route=route_payload)

    if block.get("enabled") and support["prompt_patch"] != "classic_ti_prompt_token_append":
        warnings.append("Embeddings/TI active prompt payload stripped because no prompt patch strategy is available.")
        block = disabled_block(support["reason"], route=route_payload)

    active_items = ((block.get("params") or {}).get("items") or []) if isinstance(block, dict) else []
    has_items = isinstance(active_items, list) and bool(active_items)
    if raw and raw.get("enabled") and not has_items and support["state"] in ACTIVE_ROUTE_STATES:
        warnings.append("Embeddings/TI was enabled but no valid chip items remained after validation.")

    workflow_patch_allowed = bool(
        not errors
        and block.get("enabled")
        and has_items
        and support["state"] in ACTIVE_ROUTE_STATES
        and support.get("prompt_patch") == "classic_ti_prompt_token_append"
    )
    ok = not errors and (not block.get("enabled") or support["state"] in ACTIVE_ROUTE_STATES)
    visibility = {
        "profile_id": profile.get("profile_id"),
        "visible_controls": list(profile.get("visible_controls") or []),
        "expert_controls": list(profile.get("expert_controls") or []),
        "diagnostic_controls": list(profile.get("diagnostic_controls") or []),
        "payload_params": list(profile.get("payload_params") or []),
        "payload_assets": list(profile.get("payload_assets") or []),
        "stale_field_policy": profile.get("stale_field_policy"),
    }
    return {
        "ok": ok,
        "extension_id": EXTENSION_ID,
        "state": support["state"],
        "workflow_patch_allowed": workflow_patch_allowed,
        "reason": support["reason"],
        "errors": errors,
        "warnings": warnings,
        "stripped_fields": stripped_fields,
        "stale_fields": stripped_fields,
        "route": route_payload,
        "support": support,
        "parameter_profile": profile,
        "visibility": visibility,
        "block": block,
        "payload": payload_wrapper(block),
        "replay_safe": bool(ok and (not block.get("enabled") or workflow_patch_allowed)),
        "validation_contract": validation_contract(),
        "validation": [{
            "extension_id": EXTENSION_ID,
            "state": support["state"],
            "ok": ok,
            "reason": support["reason"],
            "workflow_patch_allowed": workflow_patch_allowed,
            "stripped_fields": stripped_fields,
        }],
    }
