from __future__ import annotations

from copy import deepcopy
from typing import Any

from .payload_schema import EXTENSION_ID, normalize_payload

DROP_PREFIXES = ("_neo_",)


def _drop_private(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _drop_private(child) for key, child in value.items() if not str(key).startswith(DROP_PREFIXES)}
    if isinstance(value, list):
        return [_drop_private(child) for child in value]
    return deepcopy(value)


def replay_payload(block: dict[str, Any] | None, *, route: dict[str, Any] | None = None, enforce_route_state: bool = True) -> dict[str, Any]:
    """Build a replay-safe ControlNet payload.

    Phase D intentionally runs the same sanitizer used for generation payloads so
    stale hidden fields and inactive-route data cannot return during reuse.
    """
    safe_raw = _drop_private(block or {})
    payload, _notes = normalize_payload(safe_raw, route=route, enforce_route_state=enforce_route_state)
    return {"extensions": {EXTENSION_ID: payload["extensions"][EXTENSION_ID]}}
