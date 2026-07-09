from __future__ import annotations

from typing import Any, Iterable

from .constants import (
    MODE_REQUIRED_NODES,
    OPTIONAL_FEATURE_CONTROLS,
    OPTIONAL_NODE_GROUPS,
    OPTIONAL_NODES,
    PHASE,
    REQUIRED_BASE_NODES,
)


def _available_node_names(available_nodes: Iterable[str] | dict[str, Any] | None = None) -> set[str]:
    if available_nodes is None:
        return set()
    if isinstance(available_nodes, dict):
        names: set[str] = set()
        for key, value in available_nodes.items():
            names.add(str(key))
            if isinstance(value, str):
                names.add(value)
            elif isinstance(value, dict):
                for field in ("class_type", "node", "name", "type"):
                    if value.get(field):
                        names.add(str(value[field]))
                aliases = value.get("aliases")
                if isinstance(aliases, (list, tuple, set)):
                    names.update(str(alias) for alias in aliases)
        return names
    return {str(node) for node in available_nodes}


def optional_node_capabilities(available_nodes: Iterable[str] | dict[str, Any] | None = None) -> dict[str, Any]:
    """Return optional feature availability without gating the base extension.

    Missing optional nodes must only disable the matching optional control/path.
    This is intentionally separate from required-node readiness so the full
    High-Res Lab route is not provider-gated when Ultimate SD Upscale or model
    upscale nodes are absent.
    """
    available = _available_node_names(available_nodes)
    checked = available_nodes is not None
    groups: dict[str, Any] = {}
    controls: dict[str, bool] = {}
    unavailable_controls: dict[str, str] = {}
    active_paths: list[str] = []
    gated_paths: list[str] = []
    for feature_id, nodes in OPTIONAL_NODE_GROUPS.items():
        missing = [node for node in nodes if node not in available] if checked else []
        is_available = checked and not missing
        state = "available" if is_available else ("unknown_not_checked" if not checked else "provider_gated")
        reason = (
            "Optional node path is available."
            if is_available
            else ("Optional node discovery was not supplied." if not checked else f"Missing optional node(s): {', '.join(missing)}")
        )
        groups[feature_id] = {
            "state": state,
            "available": bool(is_available),
            "required_nodes": list(nodes),
            "missing_nodes": missing,
            "reason": reason,
            "controls": list(OPTIONAL_FEATURE_CONTROLS.get(feature_id, [])),
        }
        if is_available:
            active_paths.append(feature_id)
        elif checked:
            gated_paths.append(feature_id)
        for control in OPTIONAL_FEATURE_CONTROLS.get(feature_id, []):
            # Controls may be shared by features. Keep them enabled if any
            # feature that owns them is available.
            controls[control] = bool(controls.get(control) or is_available)
            if not is_available and control not in unavailable_controls:
                unavailable_controls[control] = reason
    return {
        "phase": PHASE,
        "checked": checked,
        "groups": groups,
        "controls": controls,
        "unavailable_controls": unavailable_controls,
        "active_optional_paths": active_paths,
        "gated_optional_paths": gated_paths,
        "policy": "Missing optional nodes gate only the matching optional control/path, not the full extension.",
    }


def inspect_nodes(available_nodes: Iterable[str] | dict[str, Any] | None = None, *, mode: str | None = None) -> dict[str, Any]:
    available = _available_node_names(available_nodes)
    required = MODE_REQUIRED_NODES.get(mode or "", REQUIRED_BASE_NODES)
    missing_required = [node for node in required if node not in available] if available_nodes is not None else []
    optional_available = [node for node in OPTIONAL_NODES if node in available]
    optional_missing = [node for node in OPTIONAL_NODES if node not in available] if available_nodes is not None else []
    optional_capabilities = optional_node_capabilities(available_nodes)
    return {
        "phase": PHASE,
        "custom_node_required": False,
        "required_base_nodes": REQUIRED_BASE_NODES,
        "mode_required_nodes": required,
        "missing_required": missing_required,
        "optional_nodes": OPTIONAL_NODES,
        "optional_available": optional_available,
        "optional_missing": optional_missing,
        "optional_capabilities": optional_capabilities,
        "parameter_visibility": optional_capabilities.get("controls", {}),
        "status": "unknown_not_checked" if available_nodes is None else ("available" if not missing_required else "provider_gated"),
        "reason": "Required base Comfy nodes are available." if available_nodes is not None and not missing_required else ("Required base Comfy node check not supplied." if available_nodes is None else f"Missing required Comfy nodes: {', '.join(missing_required)}"),
    }


def required_nodes_for_mode(mode: str | None = None) -> list[str]:
    return list(MODE_REQUIRED_NODES.get(mode or "", REQUIRED_BASE_NODES))
