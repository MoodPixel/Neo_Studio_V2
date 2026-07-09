from __future__ import annotations

from copy import deepcopy
from typing import Any

EXTENSION_ID = "lora_stack"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "assets"


def _clean_route(route: dict | None = None) -> dict[str, Any]:
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
    }


def _extension_block(validation_result: dict[str, Any] | None = None) -> dict[str, Any]:
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    block = validation_result.get("block") if isinstance(validation_result.get("block"), dict) else {}
    return deepcopy(block)


def _loras_from_block(block: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    block = block if isinstance(block, dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    loras = params.get("loras") if isinstance(params.get("loras"), list) else []
    return deepcopy(loras)


def _assets_from_block(block: dict[str, Any] | None = None) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    return deepcopy(assets)


def _names(rows: list[dict[str, Any]]) -> list[str]:
    return [str(row.get("name") or "") for row in rows if str(row.get("name") or "").strip()]


def _row_groups(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "total": len(rows),
        "global_base": len([row for row in rows if str(row.get("apply_to") or "global") == "global" and str(row.get("target") or "both") in {"both", "base"}]),
        "regional": len([row for row in rows if str(row.get("apply_to") or "global").startswith("scene_region_")]),
        "finish_only": len([row for row in rows if str(row.get("target") or "both") == "finish"]),
    }


def _workflow_summary(block: dict[str, Any], workflow_patch: dict[str, Any], route: dict[str, Any]) -> str:
    rows = _loras_from_block(block)
    groups = _row_groups(rows)
    if not block.get("enabled") or not rows:
        reason = (block.get("metadata") or {}).get("reason") or workflow_patch.get("reason") or "disabled or gated"
        return f"LoRA Stack did not mutate the workflow: {reason}."
    state = route.get("route_state") or "unknown"
    family = route.get("family") or "family unknown"
    loader = route.get("loader") or "loader unknown"
    if workflow_patch.get("applied"):
        patched = workflow_patch.get("lora_count") or groups["global_base"]
        node_ids = workflow_patch.get("node_ids") if isinstance(workflow_patch.get("node_ids"), list) else []
        return f"Applied {patched} global/base LoRA row(s) through Comfy LoraLoader on {family}/{loader} ({state}). Node ids: {', '.join(map(str, node_ids)) or 'not recorded'}."
    return f"Validated {groups['total']} LoRA row(s) for {family}/{loader} ({state}); no graph patch was applied."


def assistant_summary_from_payload(
    block: dict[str, Any] | None = None,
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> str:
    block = block if isinstance(block, dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    workflow_patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    loras = _loras_from_block(block)
    clean_route = _clean_route(route)
    if not block.get("enabled") or not loras:
        reason = metadata.get("reason") or workflow_patch.get("reason") or "disabled or gated"
        return f"LoRA Stack was not applied ({reason})."
    names = ", ".join(_names(loras)[:3])
    suffix = "..." if len(loras) > 3 else ""
    route_label = "+".join(str(clean_route.get(key) or "") for key in ("backend", "family", "loader", "workflow_mode") if clean_route.get(key))
    verb = "applied" if workflow_patch.get("applied") else "validated"
    groups = _row_groups(loras)
    detail = f"{groups['global_base']} global/base"
    if groups["regional"]:
        detail += f", {groups['regional']} regional preserved"
    if groups["finish_only"]:
        detail += f", {groups['finish_only']} finish-only preserved"
    return f"LoRA Stack {verb} {len(loras)} active LoRA row(s): {names}{suffix}. Rows: {detail}. Route: {route_label}."


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
        safe_block["params"] = {}
        safe_block["inputs"] = {}
        safe_block["assets"] = {}
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "route": _clean_route(route),
        "payload": safe_block,
        "restore_policy": "revalidate_before_enable",
        "revalidate_keys": [
            "backend",
            "family",
            "loader",
            "workflow_mode",
            "route_state",
            "lora_catalog",
            "lora_loader_node",
            "scene_director_targets",
            "finish_pass_support",
        ],
    }


def memory_readiness_shape(route: dict | None = None, params: dict | None = None, *, assets: dict | None = None, validation: list[dict[str, Any]] | None = None, replay_payload: dict | None = None, outputs: dict | None = None, workflow_summary: str = "", assistant_summary: str = "") -> dict:
    active = bool(params)
    replay_payload = replay_payload if isinstance(replay_payload, dict) else {}
    clean_route = _clean_route(route)
    loras = (params or {}).get("loras") if isinstance(params, dict) and isinstance((params or {}).get("loras"), list) else []
    return {
        "schema_version": "neo.lora_stack.memory_event.v1",
        "event_type": "extension_workflow_used",
        "namespace": f"extension:{EXTENSION_ID}",
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "surface": "image",
        "subtab": clean_route.get("workflow_mode") or clean_route.get("mode") or "generate",
        "route": clean_route,
        "assets": assets or {},
        "params": params or {},
        "outputs": outputs or {},
        "lora_names": _names(loras) if isinstance(loras, list) else [],
        "lora_count": len(loras) if isinstance(loras, list) else 0,
        "workflow_summary": workflow_summary or "LoRA Stack records ordered LoRA rows, route state, library assets, and workflow patch summary for replay.",
        "assistant_summary": assistant_summary or "LoRA Stack was {} for the active Image route.".format("recorded" if active else "not active"),
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
            "lora_catalog",
            "lora_loader_node",
        ],
    }


def build_output_extension_metadata(
    validation_result: dict[str, Any] | None = None,
    *,
    workflow_patch: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build output-record extension metadata for LoRA Stack usage, replay, and future Assistant access."""
    validation_result = validation_result if isinstance(validation_result, dict) else {}
    workflow_patch = workflow_patch if isinstance(workflow_patch, dict) else {}
    block = _extension_block(validation_result)
    clean_route = _clean_route(route or validation_result.get("route"))
    enabled = bool(block.get("enabled"))
    loras = _loras_from_block(block)
    assets = _assets_from_block(block)
    applied = bool(workflow_patch.get("applied"))
    status = "used" if applied else ("validated" if enabled else "disabled")
    reason = workflow_patch.get("reason") or (block.get("metadata") or {}).get("reason") or ""
    groups = _row_groups(loras)
    summary = assistant_summary_from_payload(block, workflow_patch=workflow_patch, route=clean_route)
    workflow_summary = _workflow_summary(block, workflow_patch, clean_route)

    usage = {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "workspace_app": WORKSPACE_APP,
        "status": status,
        "enabled": enabled,
        "workflow_patch_applied": applied,
        "route_state": clean_route.get("route_state"),
        "label": "LoRA Stack",
        "lora_count": len(loras),
        "lora_names": _names(loras),
        "global_base_count": groups["global_base"],
        "regional_count": groups["regional"],
        "finish_only_count": groups["finish_only"],
        "node": workflow_patch.get("node") or workflow_patch.get("node_class") or ("LoraLoader" if applied else ""),
    }
    if reason:
        usage["reason"] = reason

    patch = deepcopy(workflow_patch) if workflow_patch else {
        "extension_id": EXTENSION_ID,
        "applied": False,
        "reason": reason or "No LoRA Stack workflow patch recorded.",
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
    block = (payload.get("extensions") or {}).get(EXTENSION_ID, {})
    loras = _loras_from_block(block) if block.get("enabled") else []
    clean_route = _clean_route(route)
    replay = replay_payload_from_block(block, route=clean_route)
    summary = assistant_summary_from_payload(block, route=clean_route)
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": EXTENSION_TYPE,
        "enabled": bool(block.get("enabled") and loras),
        "params_used": {"loras": loras},
        "assets_used": block.get("assets") or {},
        "route": clean_route,
        "workflow_summary": _workflow_summary(block, {}, clean_route),
        "assistant_summary": summary,
        "gated_reason": gated_reason,
        "replay_payload": replay,
        "memory_event": memory_readiness_shape(route=clean_route, params={"loras": loras} if loras else {}, assets=block.get("assets") or {}, replay_payload=replay, assistant_summary=summary),
    }


def output_metadata_preview(validation_result: dict[str, Any]) -> dict[str, Any]:
    metadata = build_output_extension_metadata(validation_result)
    metadata["workflow_patches"] = []
    return {"extensions": metadata}
