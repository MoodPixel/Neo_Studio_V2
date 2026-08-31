from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

SUPPORTED = "supported"
PROVISIONAL = "provisional"
BLOCKED = "blocked"
UNSUPPORTED = "unsupported"
KNOWN_STATES = {SUPPORTED, PROVISIONAL, BLOCKED, UNSUPPORTED}

MATRIX_SCHEMA_VERSION = "neo.video.lora_stack.support_matrix.v1"
MATRIX_SOURCE = "neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json"
DATA_PATH = Path(__file__).with_name("support_matrix_data.json")


def _load_data() -> dict[str, Any]:
    try:
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {
            "schema_version": MATRIX_SCHEMA_VERSION,
            "policy": "Support matrix unavailable; fail closed.",
            "supported_backends": [],
            "groups": [],
        }
    return data if isinstance(data, dict) else {}


MATRIX_DATA = _load_data()
SUPPORTED_BACKENDS = tuple(str(item) for item in MATRIX_DATA.get("supported_backends", []) if str(item))


def _generation_type_from_route_id(route_id: str) -> str:
    parts = str(route_id or "").split(".")
    value = parts[2] if len(parts) > 2 else ""
    if value.startswith("img2vid"):
        return "img2vid"
    return value


def _expand_groups() -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for group in MATRIX_DATA.get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        route_ids = group.get("route_ids") if isinstance(group.get("route_ids"), list) else []
        shared = {key: deepcopy(value) for key, value in group.items() if key != "route_ids"}
        state = str(shared.get("state") or UNSUPPORTED)
        if state not in KNOWN_STATES:
            state = UNSUPPORTED
        shared["state"] = state
        for route_id in route_ids:
            rid = str(route_id or "").strip()
            if not rid:
                continue
            rows[rid] = {
                "route_id": rid,
                "generation_type": _generation_type_from_route_id(rid),
                **deepcopy(shared),
            }
    return rows


ROUTE_SUPPORT = _expand_groups()


def _route_components(route: str | dict[str, Any] | None) -> tuple[str, str, str, str]:
    if isinstance(route, str):
        rid = route.strip()
        parts = rid.split(".")
        family = parts[0] if len(parts) > 0 else ""
        loader = parts[1] if len(parts) > 1 else ""
        generation_type = _generation_type_from_route_id(rid)
        return rid, family, loader, generation_type

    route = route if isinstance(route, dict) else {}
    rid = str(route.get("route_id") or route.get("id") or "").strip()
    family = str(route.get("family") or "").strip()
    loader = str(route.get("loader") or route.get("loader_type") or "").strip()
    generation_type = str(route.get("generation_type") or route.get("workflow_mode") or route.get("mode") or "").strip()
    return rid, family, loader, generation_type


def _backend_id(route: str | dict[str, Any] | None, backend: str | None = None) -> str:
    if backend:
        return str(backend).strip()
    if isinstance(route, dict):
        return str(route.get("backend_id") or route.get("backend") or "comfyui").strip()
    return "comfyui"


def _blocked_unknown(*, route_id: str = "", family: str = "", loader: str = "", generation_type: str = "", backend: str = "") -> dict[str, Any]:
    return {
        "route_id": route_id,
        "family": family,
        "loader": loader,
        "generation_type": generation_type,
        "backend": backend,
        "state": BLOCKED,
        "supports_standard_lora": False,
        "supports_speed_lora": False,
        "supports_branch_targeting": False,
        "allowed_targets": ["all"],
        "required_loader_type": "unknown",
        "required_loader_nodes": [],
        "allow_generic_lora_loader_fallback": False,
        "patch_profile_required": True,
        "validated_topology": False,
        "reason": "No exact Video LoRA support-matrix row exists for this route. Fail closed.",
    }


def support_for_route(route: str | dict[str, Any] | None, *, backend: str | None = None) -> dict[str, Any]:
    rid, family, loader, generation_type = _route_components(route)
    backend_id = _backend_id(route, backend)

    if backend_id not in SUPPORTED_BACKENDS:
        result = _blocked_unknown(
            route_id=rid,
            family=family,
            loader=loader,
            generation_type=generation_type,
            backend=backend_id,
        )
        result["reason"] = f"Video LoRA Stack does not support backend {backend_id!r}."
        return result

    row = ROUTE_SUPPORT.get(rid) if rid else None

    # Component fallback exists only to resolve a canonical route row when a
    # caller has not preserved route_id. It never borrows support from a
    # different loader or generation type.
    if row is None and family and loader and generation_type:
        matches = [
            item
            for item in ROUTE_SUPPORT.values()
            if item.get("family") == family
            and item.get("loader") == loader
            and item.get("generation_type") == generation_type
        ]
        if len(matches) == 1:
            row = matches[0]

    if row is None:
        return _blocked_unknown(
            route_id=rid,
            family=family,
            loader=loader,
            generation_type=generation_type,
            backend=backend_id,
        )

    result = deepcopy(row)
    result["backend"] = backend_id
    result["eligible"] = result.get("state") == SUPPORTED
    result["fail_closed"] = result.get("state") != SUPPORTED
    return result


def route_support_state(route: str | dict[str, Any] | None, *, backend: str | None = None) -> str:
    return str(support_for_route(route, backend=backend).get("state") or BLOCKED)


def supports_lora(route: str | dict[str, Any] | None, *, backend: str | None = None) -> bool:
    support = support_for_route(route, backend=backend)
    if support.get("state") != SUPPORTED:
        return False
    return bool(support.get("supports_standard_lora") or support.get("supports_speed_lora"))


def supports_standard_lora(route: str | dict[str, Any] | None, *, backend: str | None = None) -> bool:
    support = support_for_route(route, backend=backend)
    return bool(support.get("state") == SUPPORTED and support.get("supports_standard_lora"))


def supports_speed_lora(route: str | dict[str, Any] | None, *, backend: str | None = None) -> bool:
    support = support_for_route(route, backend=backend)
    return bool(support.get("state") == SUPPORTED and support.get("supports_speed_lora"))


def supports_branch_targeting(route: str | dict[str, Any] | None, *, backend: str | None = None) -> bool:
    support = support_for_route(route, backend=backend)
    return bool(support.get("state") == SUPPORTED and support.get("supports_branch_targeting"))


def allowed_targets(route: str | dict[str, Any] | None, *, backend: str | None = None) -> tuple[str, ...]:
    support = support_for_route(route, backend=backend)
    values = support.get("allowed_targets") if isinstance(support.get("allowed_targets"), list) else ["all"]
    return tuple(str(item) for item in values if str(item)) or ("all",)


def required_loader_type(route: str | dict[str, Any] | None, *, backend: str | None = None) -> str:
    return str(support_for_route(route, backend=backend).get("required_loader_type") or "unknown")


def required_loader_nodes(route: str | dict[str, Any] | None, *, backend: str | None = None) -> tuple[str, ...]:
    support = support_for_route(route, backend=backend)
    values = support.get("required_loader_nodes") if isinstance(support.get("required_loader_nodes"), list) else []
    return tuple(str(item) for item in values if str(item))


def validate_lora_rows_for_route(
    route: str | dict[str, Any] | None,
    rows: list[dict[str, Any]] | None,
    *,
    backend: str | None = None,
) -> list[str]:
    support = support_for_route(route, backend=backend)
    errors: list[str] = []

    if support.get("state") != SUPPORTED:
        return [
            f"Video LoRA route {support.get('route_id') or '<unknown>'} is {support.get('state')}; LoRA application is fail-closed. {support.get('reason') or ''}".strip()
        ]

    valid_targets = set(allowed_targets(route, backend=backend))
    for index, row in enumerate(rows or []):
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        role = str(row.get("role") or "standard").strip().lower()
        target = str(row.get("target") or "all").strip().lower()

        if role == "speed" and not support.get("supports_speed_lora"):
            errors.append(f"Video LoRA row {index + 1}: speed LoRAs are not supported on this route.")
        elif role != "speed" and not support.get("supports_standard_lora"):
            errors.append(f"Video LoRA row {index + 1}: standard LoRAs are not supported on this route.")

        if target not in valid_targets:
            errors.append(
                f"Video LoRA row {index + 1}: target {target!r} is not valid for this route; allowed targets: {', '.join(sorted(valid_targets))}."
            )

    return errors


def support_matrix_payload() -> dict[str, Any]:
    rows = [deepcopy(ROUTE_SUPPORT[key]) for key in sorted(ROUTE_SUPPORT)]
    counts = {state: len([row for row in rows if row.get("state") == state]) for state in sorted(KNOWN_STATES)}
    return {
        "schema_version": MATRIX_SCHEMA_VERSION,
        "source": MATRIX_SOURCE,
        "policy": MATRIX_DATA.get("policy") or "Exact-route, fail-closed Video LoRA compatibility.",
        "supported_backends": list(SUPPORTED_BACKENDS),
        "counts": counts,
        "routes": rows,
    }
