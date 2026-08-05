from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable, Mapping

SCHEMA_ID = "neo.image.lanpaint_capability_snapshot.v1"
SCHEMA_VERSION = 1
AUTHORITY = "neo_app.image.lanpaint_capability_discovery"
PHASE_STATE = "phase22_1_registry_driven_discovery_and_cache_repair"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _iter_aliases(group: Mapping[str, Any]) -> Iterable[str]:
    for value in group.get("aliases") or ():
        name = str(value or "").strip()
        if name:
            yield name


def build_lanpaint_discovery_contract(
    adapter_registry: Mapping[str, Any] | None,
    *,
    base_node_classes: Iterable[str] = (),
) -> dict[str, Any]:
    """Build the exact object_info discovery scope from selectable adapters.

    The provider may include a small base set for compiler nodes that are shared
    with txt2img/img2img graphs. Family-specific LanPaint nodes come from the
    active adapter registry, so onboarding a family cannot silently outgrow a
    hand-maintained whitelist.
    """

    registry = _mapping(adapter_registry)
    required_nodes = {str(item).strip() for item in base_node_classes if str(item).strip()}
    route_requirements: dict[str, dict[str, Any]] = {}

    adapters = registry.get("adapters") if isinstance(registry.get("adapters"), list) else []
    for raw_adapter in adapters:
        adapter = _mapping(raw_adapter)
        binding = _mapping(adapter.get("binding"))
        if not binding.get("selectable"):
            continue
        identity = _mapping(adapter.get("identity"))
        route_key = str(identity.get("route_key") or "").strip()
        capability = _mapping(adapter.get("capabilities"))
        route_nodes: set[str] = set()
        groups: list[dict[str, Any]] = []

        for raw_group in capability.get("node_groups") or ():
            group = _mapping(raw_group)
            aliases = sorted(set(_iter_aliases(group)))
            if not aliases:
                continue
            route_nodes.update(aliases)
            groups.append({
                "role": str(group.get("role") or "node"),
                "aliases": aliases,
                "required": bool(group.get("required", True)),
                "pack_id": str(group.get("pack_id") or "comfy-core"),
                "conditional": False,
            })

        conditional = _mapping(capability.get("conditional_node_groups"))
        for condition_id, raw_group in conditional.items():
            group = _mapping(raw_group)
            aliases = sorted(set(_iter_aliases(group)))
            if not aliases:
                continue
            route_nodes.update(aliases)
            groups.append({
                "role": str(group.get("role") or condition_id or "conditional_node"),
                "aliases": aliases,
                "required": False,
                "pack_id": str(group.get("pack_id") or "comfy-core"),
                "conditional": True,
                "condition_id": str(condition_id),
            })

        required_nodes.update(route_nodes)
        if route_key:
            route_requirements[route_key] = {
                "adapter_id": str(identity.get("adapter_id") or ""),
                "adapter_fingerprint": str(adapter.get("adapter_fingerprint") or ""),
                "node_classes": sorted(route_nodes),
                "node_groups": groups,
            }

    active_route_keys = sorted(route_requirements)
    required_node_classes = sorted(required_nodes)
    fingerprint_payload = {
        "adapter_registry_fingerprint": str(registry.get("registry_fingerprint") or ""),
        "active_route_keys": active_route_keys,
        "required_node_classes": required_node_classes,
        "route_requirements": route_requirements,
    }
    discovery_fingerprint = hashlib.sha256(json.dumps(
        fingerprint_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")).hexdigest()

    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "phase_state": PHASE_STATE,
        "adapter_registry_schema_id": str(registry.get("schema_id") or ""),
        "adapter_registry_schema_version": registry.get("schema_version"),
        "adapter_registry_fingerprint": str(registry.get("registry_fingerprint") or ""),
        "active_route_keys": active_route_keys,
        "required_node_classes": required_node_classes,
        "route_requirements": route_requirements,
        "discovery_fingerprint": discovery_fingerprint,
    }


def build_lanpaint_capability_snapshot_metadata(
    discovery_contract: Mapping[str, Any],
    *,
    discovered_node_classes: Iterable[str] = (),
    object_info_available: bool,
) -> dict[str, Any]:
    contract = _mapping(discovery_contract)
    discovered = sorted({str(item).strip() for item in discovered_node_classes if str(item).strip()})
    required = [str(item) for item in contract.get("required_node_classes") or ()]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "authority": AUTHORITY,
        "phase_state": PHASE_STATE,
        "adapter_registry_fingerprint": str(contract.get("adapter_registry_fingerprint") or ""),
        "discovery_fingerprint": str(contract.get("discovery_fingerprint") or ""),
        "active_route_keys": [str(item) for item in contract.get("active_route_keys") or ()],
        "required_node_classes": required,
        "discovered_node_classes": discovered,
        "missing_node_classes": sorted(set(required) - set(discovered)),
        "object_info_available": bool(object_info_available),
    }


def lanpaint_snapshot_freshness(
    backend_capabilities: Mapping[str, Any] | None,
    *,
    expected_registry_fingerprint: str,
    expected_route_key: str,
) -> dict[str, Any]:
    backend = _mapping(backend_capabilities)
    metadata = _mapping(backend.get("lanpaint_capability_snapshot"))
    matrix_present = isinstance(backend.get("lanpaint_route_capability_matrix"), Mapping)
    registry_present = isinstance(backend.get("lanpaint_family_adapters"), Mapping)
    snapshot_present = bool(metadata)
    actual_fingerprint = str(metadata.get("adapter_registry_fingerprint") or "")
    active_route_keys = {str(item) for item in metadata.get("active_route_keys") or ()}

    reasons: list[str] = []
    # Synthetic unit callers often provide only object_info_node_inputs. They are
    # not persisted profile snapshots and should not be rejected as stale.
    snapshot_like = bool(matrix_present or registry_present or snapshot_present)
    if snapshot_like and not snapshot_present:
        reasons.append("snapshot_metadata_missing")
    if snapshot_present and expected_registry_fingerprint and actual_fingerprint != expected_registry_fingerprint:
        reasons.append("adapter_registry_fingerprint_mismatch")
    if snapshot_present and expected_route_key and expected_route_key not in active_route_keys:
        reasons.append("route_missing_from_snapshot_registry")

    return {
        "checked": snapshot_like,
        "fresh": not reasons,
        "stale": bool(reasons),
        "reasons": reasons,
        "expected_registry_fingerprint": str(expected_registry_fingerprint or ""),
        "actual_registry_fingerprint": actual_fingerprint,
        "expected_route_key": str(expected_route_key or ""),
        "snapshot_active_route_keys": sorted(active_route_keys),
        "metadata": deepcopy(metadata),
    }


__all__ = [
    "AUTHORITY",
    "PHASE_STATE",
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "build_lanpaint_capability_snapshot_metadata",
    "build_lanpaint_discovery_contract",
    "lanpaint_snapshot_freshness",
]
