from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .constants import EXTENSION_ID
from .route_profiles import (
    KNOWN_STATES,
    base_route_state,
    high_res_lab_state_for_route,
    normalized_route,
    route_profile_summary,
)

DATA_PATH = Path(__file__).with_name("support_matrix_data.json")
_ALLOWED = set(KNOWN_STATES)


def load_support_matrix() -> dict[str, Any]:
    return json.loads(DATA_PATH.read_text(encoding="utf-8"))


def route_key(route: dict[str, Any] | None = None) -> str:
    normalized = normalized_route(route)
    return f"{normalized['backend']}.{normalized['family']}.{normalized['loader']}.{normalized['mode']}.finish"


def _row_for_key(key: str) -> dict[str, Any] | None:
    for row in load_support_matrix().get("routes", []):
        if row.get("route_id") == key:
            return row
    return None


def route_state(route: dict[str, Any] | None = None) -> str:
    state = high_res_lab_state_for_route(route)
    return state if state in _ALLOWED else "provider_gated"


def is_route_active(route: dict[str, Any] | None = None) -> bool:
    return route_state(route) in {"available", "experimental_available"}


def route_reason(route: dict[str, Any] | None = None) -> str:
    key = route_key(route)
    row = _row_for_key(key)
    profile = route_profile_summary(route)
    state = profile.get("high_res_lab_state") or route_state(route)
    if state in {"available", "experimental_available"}:
        return (
            row.get("reason") if isinstance(row, dict) and row.get("reason") else
            f"High-Res Lab route profile {profile.get('profile_id')} is active for {profile.get('family')}+{profile.get('loader')}+{profile.get('mode')}."
        )
    if state == "implementation_target":
        notes = profile.get("notes") if isinstance(profile.get("notes"), list) else []
        note = notes[0] if notes else "This route needs a dedicated family enablement pass before High-Res Lab is selectable."
        return f"implementation_target: {note}"
    if row and row.get("reason"):
        return str(row.get("reason"))
    base_state = base_route_state(route)
    return f"{state}: Base route state is {base_state}; High-Res Lab must not create a fallback route."


def manifest_route_states() -> dict[str, str]:
    states: dict[str, str] = {}
    for row in load_support_matrix().get("routes", []):
        route = {
            "backend": row.get("backend"),
            "family": row.get("family"),
            "loader": row.get("loader"),
            "mode": row.get("mode"),
            "base_route_state": row.get("base_route_state"),
        }
        key = f"{route['backend']}:{route['family']}:{route['loader']}:{route['mode']}"
        states[key] = route_state(route)
    return states


def support_summary() -> dict[str, Any]:
    data = load_support_matrix()
    rows = data.get("routes", [])
    derived_states = []
    for row in rows:
        derived_states.append(route_state({
            "backend": row.get("backend"),
            "family": row.get("family"),
            "loader": row.get("loader"),
            "mode": row.get("mode"),
            "base_route_state": row.get("base_route_state"),
        }))
    return {
        "extension_id": EXTENSION_ID,
        "phase": data.get("phase"),
        "source_phase": data.get("source_phase"),
        "state_counts": dict(Counter(derived_states)),
        "route_count": len(rows),
        "route_profile_contract": "P8.5",
    }
