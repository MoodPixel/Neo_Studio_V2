from __future__ import annotations

from copy import deepcopy
from typing import Any

from .memory_event import build_memory_event
from .payload_schema import EXTENSION_ID
from .replay import replay_payload


def _route_state(route: dict[str, Any] | None) -> str:
    return str((route or {}).get("route_state") or (route or {}).get("state") or "unknown")


def _active_units(block: dict[str, Any]) -> list[dict[str, Any]]:
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    units = inputs.get("units") if isinstance(inputs.get("units"), list) else []
    return [unit for unit in units if isinstance(unit, dict) and unit.get("enabled")]


def _unit_label(unit: dict[str, Any]) -> str:
    parts = [str(unit.get("unit") or "control"), str(unit.get("preprocessor") or "none")]
    model = str(unit.get("model") or "").strip()
    if model:
        parts.append(model.rsplit("/", 1)[-1].rsplit("\\", 1)[-1])
    return " / ".join(parts)


def _patch_applied(patch: dict[str, Any] | None) -> bool:
    return bool(isinstance(patch, dict) and (patch.get("applied") or patch.get("mutated")))


def _assistant_summary(*, block: dict[str, Any], patch: dict[str, Any] | None, route: dict[str, Any] | None, reason: str = "") -> str:
    enabled = bool(block.get("enabled"))
    units = _active_units(block)
    applied = _patch_applied(patch)
    state = _route_state(route)
    if not enabled:
        return f"ControlNet was not applied ({reason or state or 'disabled'})."
    if applied:
        names = ", ".join(_unit_label(unit) for unit in units[:3])
        suffix = "…" if len(units) > 3 else ""
        node = (patch or {}).get("node") or (patch or {}).get("node_class") or "ControlNetApply"
        return f"ControlNet applied {len(units)} unit(s) through {node}{': ' + names + suffix if names else ''}."
    return f"ControlNet requested but not patched ({reason or state or 'not applied'})."


def build_output_metadata(*, enabled: bool, route: dict[str, Any] | None = None, params: dict[str, Any] | None = None, assets: dict[str, Any] | None = None, node_status: dict[str, Any] | None = None, validation: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "extensions": {
            "used": [{"extension_id": EXTENSION_ID, "extension_type": "built_in", "enabled": bool(enabled), "state": _route_state(route)}],
            "payloads": {EXTENSION_ID: {"params": deepcopy(params or {}), "assets": deepcopy(assets or {})}},
            "workflow_patches": [],
            "validation": deepcopy(validation or []),
            "node_status": {EXTENSION_ID: deepcopy(node_status or {})},
        }
    }


def build_output_extension_metadata(validation_result: dict[str, Any] | None = None, *, workflow_patch: dict[str, Any] | None = None, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build Output Inspector/replay/memory-ready metadata for ControlNet.

    This is intentionally extension-owned. Core output records only normalize the
    approved append slots; ControlNet owns the exact summary, replay block, and
    assistant-readable memory readiness shape.
    """
    result = validation_result or {}
    block = result.get("block") if isinstance(result.get("block"), dict) else {}
    patch = workflow_patch or {}
    route_data = deepcopy(result.get("route") if isinstance(result.get("route"), dict) else (route or {}))
    enabled = bool(block.get("enabled"))
    applied = _patch_applied(patch)
    state = _route_state(route_data)
    reason = str(result.get("reason") or patch.get("reason") or (block.get("metadata") or {}).get("reason") or "").strip()
    units = _active_units(block)
    safe_replay = replay_payload(block, route=route_data, enforce_route_state=True).get("extensions", {}).get(EXTENSION_ID, {})
    assistant_summary = _assistant_summary(block=block, patch=patch, route=route_data, reason=reason)
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    asset_resolution = result.get("asset_resolution") if isinstance(result.get("asset_resolution"), dict) else {}
    outputs = {
        "workflow_patch_applied": applied,
        "workflow_nodes_added": deepcopy(patch.get("node_ids") if isinstance(patch.get("node_ids"), list) else []),
        "sampler_node_id": str(patch.get("sampler_node_id") or ""),
    }
    memory_event = build_memory_event(
        route=route_data,
        assets=assets,
        params=params,
        outputs=outputs,
        workflow_summary=assistant_summary,
        assistant_summary=assistant_summary,
        replay_payload={"extensions": {EXTENSION_ID: safe_replay}},
    )
    return {
        "used": [{
            "extension_id": EXTENSION_ID,
            "extension_type": "built_in",
            "workspace_app": "reference",
            "enabled": enabled,
            "status": "used" if applied else ("gated" if enabled else "disabled"),
            "state": state,
            "route_state": state,
            "applied": applied,
            "workflow_patch_applied": applied,
            "node": patch.get("node") or patch.get("node_class") or "",
            "reason": reason,
            "controlnet_unit_count": len(units),
        }],
        "payloads": {EXTENSION_ID: {**deepcopy(block), **({"asset_resolution": deepcopy(asset_resolution)} if asset_resolution else {})}} if block else {},
        "workflow_patches": [deepcopy(patch)] if patch else [],
        "validation": deepcopy(result.get("validation") or []),
        "replay_payloads": {EXTENSION_ID: safe_replay} if safe_replay else {},
        "assistant_summary": assistant_summary,
        "assistant_summaries": {EXTENSION_ID: assistant_summary},
        "memory_events": {EXTENSION_ID: memory_event},
    }
