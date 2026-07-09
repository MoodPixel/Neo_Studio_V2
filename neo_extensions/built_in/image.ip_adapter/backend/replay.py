from __future__ import annotations
from copy import deepcopy
from typing import Any
from .payload_schema import EXTENSION_ID

ACTIVE_STATES = {"available", "experimental_available"}


def _route_state(route: dict[str, Any] | None) -> str:
    return str((route or {}).get("route_state") or (route or {}).get("state") or "unknown")


def replay_payload(block: dict[str, Any] | None, *, route: dict[str, Any] | None = None, enforce_route_state: bool = False) -> dict[str, Any]:
    """Return a route-aware, safe restore payload for Output Inspector reuse."""
    route_data = deepcopy(route or {})
    state = _route_state(route_data)
    block = deepcopy(block or {})
    if enforce_route_state and state not in ACTIVE_STATES:
        block = {
            "enabled": False,
            "version": block.get("version", 1),
            "inputs": {},
            "params": {},
            "assets": {},
            "metadata": {"reason": f"route gated for replay: {state}", "route_state": state},
        }
    elif not block.get("enabled"):
        block = {"enabled": False, "version": block.get("version", 1), "inputs": {}, "params": {}, "assets": {}, "metadata": {"reason": "disabled"}}
    block.setdefault("metadata", {})["route"] = route_data or block.get("metadata", {}).get("route") or {}
    block["metadata"]["restore_policy"] = "revalidate_route_nodes_ip_adapter_models_clip_vision_faceid_and_assets_before_enable"
    block["metadata"]["revalidation_required"] = True
    return {"extensions": {EXTENSION_ID: block}}
