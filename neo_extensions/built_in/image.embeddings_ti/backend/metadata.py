from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_ID = "embeddings_ti"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "assets"
SURFACE = "image"


def _clean_route(route: dict[str, Any] | None = None) -> dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    mode = route.get("workflow_mode") or route.get("mode") or "generate"
    return {
        "backend": route.get("backend") or route.get("provider") or route.get("provider_id") or "",
        "family": route.get("family") or "",
        "loader": route.get("loader") or "",
        "mode": mode,
        "workflow_mode": mode,
        "workspace_app": route.get("workspace_app") or WORKSPACE_APP,
        "route_state": route.get("route_state") or route.get("state") or "unknown",
        "prompt_patch": route.get("prompt_patch") or "",
    }


def _extension_block(validation_result: dict[str, Any] | None = None) -> dict[str, Any]:
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    block = validation_result.get("block") if isinstance(validation_result.get("block"), dict) else {}
    return deepcopy(block)


def _items_from_block(block: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    block = block if isinstance(block, dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    items = params.get("items") if isinstance(params.get("items"), list) else []
    return [deepcopy(item) for item in items if isinstance(item, dict)]


def _assets_from_block(block: dict[str, Any] | None = None) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    return deepcopy(assets)


def _token(item: dict[str, Any]) -> str:
    return str(item.get("token") or item.get("name") or "").strip()


def _tokens(items: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        token = _token(item)
        key = token.casefold()
        if token and key not in seen:
            seen.add(key)
            result.append(token)
    return result


def _target_groups(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(items),
        "positive": len([item for item in items if str(item.get("target") or "") == "positive_prompt"]),
        "negative": len([item for item in items if str(item.get("target") or "") == "negative_prompt"]),
        "finish_positive": len([item for item in items if str(item.get("target") or "") == "finish_positive"]),
        "finish_negative": len([item for item in items if str(item.get("target") or "") == "finish_negative"]),
    }


def _workflow_summary(block: dict[str, Any], workflow_patch: dict[str, Any], route: dict[str, Any]) -> str:
    items = _items_from_block(block)
    if not block.get("enabled") or not items:
        reason = (block.get("metadata") or {}).get("reason") or workflow_patch.get("reason") or "disabled or gated"
        return f"Embeddings/TI did not mutate the workflow: {reason}."
    family = route.get("family") or "family unknown"
    loader = route.get("loader") or "loader unknown"
    state = route.get("route_state") or "unknown"
    if workflow_patch.get("mutated"):
        applied = workflow_patch.get("applied_item_count") or len(workflow_patch.get("applied_tokens") or [])
        nodes = workflow_patch.get("patched_prompt_nodes") if isinstance(workflow_patch.get("patched_prompt_nodes"), dict) else {}
        node_ids = []
        for values in nodes.values():
            if isinstance(values, list):
                node_ids.extend(str(value) for value in values)
        node_label = ", ".join(sorted(set(node_ids))) or "not recorded"
        return f"Applied {applied} Embeddings/TI prompt-token chip(s) to {family}/{loader} ({state}). Prompt node ids: {node_label}."
    duplicate_count = int(workflow_patch.get("duplicate_item_count") or 0) if isinstance(workflow_patch, dict) else 0
    if duplicate_count and duplicate_count >= len(items):
        return f"Used {len(items)} existing Embeddings/TI prompt-token chip(s) already present in {family}/{loader} ({state}); no duplicate append needed."
    return f"Validated {len(items)} Embeddings/TI chip(s) for {family}/{loader} ({state}); no prompt text was changed."


def assistant_summary_from_payload(
    block: dict[str, Any] | None = None,
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> str:
    block = block if isinstance(block, dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    workflow_patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    items = _items_from_block(block)
    clean_route = _clean_route(route)
    if not block.get("enabled") or not items:
        reason = metadata.get("reason") or workflow_patch.get("reason") or "disabled or gated"
        return f"Embeddings/TI was not applied ({reason})."
    names = ", ".join(_tokens(items)[:4])
    suffix = "..." if len(items) > 4 else ""
    verb = "applied" if workflow_patch.get("mutated") else "validated"
    groups = _target_groups(items)
    target_bits = []
    if groups["positive"]:
        target_bits.append(f"{groups['positive']} positive")
    if groups["negative"]:
        target_bits.append(f"{groups['negative']} negative")
    if groups["finish_positive"] or groups["finish_negative"]:
        target_bits.append(f"{groups['finish_positive'] + groups['finish_negative']} finish/deferred")
    target_label = ", ".join(target_bits) or "targets unknown"
    route_label = "+".join(str(clean_route.get(key) or "") for key in ("backend", "family", "loader", "workflow_mode") if clean_route.get(key))
    duplicate_count = int(workflow_patch.get("duplicate_item_count") or 0) if isinstance(workflow_patch, dict) else 0
    if not workflow_patch.get("mutated") and duplicate_count and duplicate_count >= len(items):
        return f"Embeddings/TI used existing prompt token(s) for {len(items)} chip(s): {names}{suffix}. Targets: {target_label}. Route: {route_label}."
    return f"Embeddings/TI {verb} {len(items)} prompt-token chip(s): {names}{suffix}. Targets: {target_label}. Route: {route_label}."


def replay_payload_from_block(block: dict[str, Any] | None = None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    safe_block = {
        "enabled": bool(block.get("enabled")),
        "version": block.get("version", 1),
        "inputs": {},
        "params": deepcopy(block.get("params") if isinstance(block.get("params"), dict) else {}),
        "assets": deepcopy(block.get("assets") if isinstance(block.get("assets"), dict) else {}),
        "metadata": deepcopy(block.get("metadata") if isinstance(block.get("metadata"), dict) else {}),
    }
    if not safe_block["enabled"]:
        safe_block["inputs"] = {}
        safe_block["params"] = {}
        safe_block["assets"] = {}
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": _clean_route(route),
        "payload": safe_block,
        "restore_policy": "revalidate_before_enable",
        "revalidation_required": True,
        "revalidate_keys": [
            "backend",
            "family",
            "loader",
            "workflow_mode",
            "route_state",
            "embedding_library",
            "embedding_tokens",
            "prompt_targets",
            "prompt_patch_strategy",
        ],
    }


def memory_readiness_shape(
    route: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    *,
    assets: dict[str, Any] | None = None,
    validation: list[dict[str, Any]] | None = None,
    replay_payload: dict[str, Any] | None = None,
    outputs: dict[str, Any] | None = None,
    workflow_summary: str = "",
    assistant_summary: str = "",
) -> dict[str, Any]:
    params = params if isinstance(params, dict) else {}
    items = params.get("items") if isinstance(params.get("items"), list) else []
    item_rows = [item for item in items if isinstance(item, dict)]
    replay_payload = replay_payload if isinstance(replay_payload, dict) else {}
    clean_route = _clean_route(route)
    return {
        "schema_version": "neo.embeddings_ti.memory_event.v1",
        "event_type": "extension_workflow_used",
        "namespace": f"extension:{EXTENSION_ID}",
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "surface": SURFACE,
        "subtab": clean_route.get("workflow_mode") or clean_route.get("mode") or "generate",
        "route": clean_route,
        "assets": assets or {},
        "params": params,
        "outputs": outputs or {},
        "embedding_tokens": _tokens(item_rows),
        "embedding_count": len(item_rows),
        "target_groups": _target_groups(item_rows),
        "workflow_summary": workflow_summary or "Embeddings/TI records prompt-token chips, route state, library assets, and prompt-patch summary for replay.",
        "assistant_summary": assistant_summary or "Embeddings/TI was {} for the active Image route.".format("recorded" if item_rows else "not active"),
        "validation": validation or [],
        "replay_payload": replay_payload,
        "restore_policy": replay_payload.get("restore_policy") or "revalidate_before_enable",
        "revalidation_required": True,
        "revalidate_keys": replay_payload.get("revalidate_keys") or [
            "backend",
            "family",
            "loader",
            "workflow_mode",
            "route_state",
            "embedding_library",
            "embedding_tokens",
            "prompt_targets",
        ],
    }


def build_output_extension_metadata(
    validation_result: dict[str, Any] | None = None,
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build output-record extension metadata for Embeddings/TI usage, replay, and future Assistant access."""
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    workflow_patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    block = _extension_block(validation_result)
    clean_route = _clean_route(route or validation_result.get("route"))
    enabled = bool(block.get("enabled"))
    items = _items_from_block(block)
    assets = _assets_from_block(block)
    mutated = bool(workflow_patch.get("mutated") or workflow_patch.get("applied"))
    duplicate_count = int(workflow_patch.get("duplicate_item_count") or 0) if isinstance(workflow_patch, dict) else 0
    if mutated:
        status = "used"
    elif enabled and items and duplicate_count and duplicate_count >= len(items):
        status = "used_existing_prompt_token"
    elif enabled and items:
        status = "validated"
    else:
        status = "disabled"
    reason = workflow_patch.get("reason") or (block.get("metadata") or {}).get("reason") or validation_result.get("reason") or ""
    groups = _target_groups(items)
    summary = assistant_summary_from_payload(block, workflow_patch=workflow_patch, route=clean_route)
    workflow_summary = _workflow_summary(block, workflow_patch, clean_route)
    tokens = _tokens(items)

    usage = {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "status": status,
        "enabled": enabled,
        "workflow_patch_applied": mutated,
        "route_state": clean_route.get("route_state"),
        "label": "Embeddings / Textual Inversion",
        "embedding_count": len(items),
        "embedding_tokens": tokens,
        "positive_count": groups["positive"],
        "negative_count": groups["negative"],
        "finish_count": groups["finish_positive"] + groups["finish_negative"],
        "node": workflow_patch.get("node") or "none",
    }
    if reason:
        usage["reason"] = reason

    patch = deepcopy(workflow_patch) if workflow_patch else {
        "extension_id": EXTENSION_ID,
        "applied": False,
        "mutated": False,
        "reason": reason or "No Embeddings/TI workflow patch recorded.",
    }
    validation = deepcopy(validation_result.get("validation") or [])
    replay = replay_payload_from_block(block, route=clean_route)
    memory = memory_readiness_shape(
        route=clean_route,
        params=deepcopy(block.get("params") if isinstance(block.get("params"), dict) else {}),
        assets=assets,
        validation=validation,
        replay_payload=replay,
        workflow_summary=workflow_summary,
        assistant_summary=summary,
    )
    return {
        "used": [usage],
        "payloads": {EXTENSION_ID: deepcopy(block)},
        "workflow_patches": [patch],
        "validation": validation,
        "assistant_summary": summary,
        "assistant_summaries": {EXTENSION_ID: summary},
        "workflow_summary": workflow_summary,
        "replay_payloads": {EXTENSION_ID: replay},
        "memory_events": {EXTENSION_ID: memory},
    }


def output_metadata(payload: dict[str, Any], route: dict[str, Any] | None = None, *, gated_reason: str = "") -> dict[str, Any]:
    block = (payload.get("extensions") or {}).get(EXTENSION_ID, {}) if isinstance(payload, dict) else {}
    items = _items_from_block(block) if isinstance(block, dict) and block.get("enabled") else []
    clean_route = _clean_route(route)
    replay = replay_payload_from_block(block if isinstance(block, dict) else {}, route=clean_route)
    summary = assistant_summary_from_payload(block if isinstance(block, dict) else {}, route=clean_route)
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "enabled": bool(block.get("enabled") and items) if isinstance(block, dict) else False,
        "params_used": {"items": items},
        "assets_used": block.get("assets") or {} if isinstance(block, dict) else {},
        "route": clean_route,
        "workflow_summary": _workflow_summary(block if isinstance(block, dict) else {}, {}, clean_route),
        "assistant_summary": summary,
        "gated_reason": gated_reason,
        "replay_payload": replay,
        "memory_event": memory_readiness_shape(route=clean_route, params={"items": items} if items else {}, assets=block.get("assets") or {} if isinstance(block, dict) else {}, replay_payload=replay, assistant_summary=summary),
    }
