from __future__ import annotations

from typing import Any

REQUIRED_NODES = ("DynamicThresholdingSimple",)
OPTIONAL_NODES = ("DynamicThresholdingFull",)
NODE_SOURCE = "https://github.com/mcmonkeyprojects/sd-dynamic-thresholding"


def _node_names(available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None) -> set[str]:
    if available_nodes is None:
        return set()
    if isinstance(available_nodes, dict):
        return {str(key) for key in available_nodes.keys()}
    return {str(node) for node in available_nodes}


def node_status(available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None) -> dict[str, Any]:
    nodes = _node_names(available_nodes)
    missing_required = [node for node in REQUIRED_NODES if node not in nodes]
    missing_optional = [node for node in OPTIONAL_NODES if node not in nodes]
    return {
        "ok": not missing_required,
        "required": list(REQUIRED_NODES),
        "optional": list(OPTIONAL_NODES),
        "available": sorted(nodes.intersection(set(REQUIRED_NODES + OPTIONAL_NODES))),
        "missing_required": missing_required,
        "missing_optional": missing_optional,
        "simple_mode_available": "DynamicThresholdingSimple" in nodes,
        "full_mode_available": "DynamicThresholdingFull" in nodes,
        "source": NODE_SOURCE,
    }


def node_gate_reason(status: dict[str, Any], *, requested_mode: str = "simple") -> str | None:
    if status.get("missing_required"):
        return "Provider gated: DynamicThresholdingSimple is required but was not detected."
    if requested_mode == "full" and not status.get("full_mode_available"):
        return "Provider gated: DynamicThresholdingFull is required for Full mode but was not detected."
    return None
