from __future__ import annotations

from typing import Any

from .metadata import EXTENSION_ID, replay_payload_from_block
from .validation import validate_and_normalize_payload


def build_replay_payload(payload: dict[str, Any] | None, *, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return the rich Phase J replay envelope after route-aware revalidation."""
    result = validate_and_normalize_payload(payload, route=route)
    block = result.get("block") if isinstance(result.get("block"), dict) else {}
    replay = replay_payload_from_block(block, route=result.get("route") if isinstance(result.get("route"), dict) else route)
    replay["ok"] = bool(result.get("ok"))
    replay["state"] = result.get("state")
    replay["reason"] = result.get("reason")
    replay["workflow_patch_allowed"] = bool(result.get("workflow_patch_allowed"))
    replay["validation"] = result.get("validation") or []
    return replay


def replay_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return the compact legacy replay block consumed by older restore tests."""
    block = (payload.get("extensions") or {}).get(EXTENSION_ID) if isinstance(payload, dict) else None
    if not isinstance(block, dict):
        return {"extensions": {}}
    replay = replay_payload_from_block(block)
    return {"extensions": {EXTENSION_ID: replay["payload"]}}
