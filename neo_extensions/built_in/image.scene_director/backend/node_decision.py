from __future__ import annotations

from typing import Any

from .support_matrix import ACTIVE_STATES, EXTENSION_ID, route_state
from .provider_capabilities import resolve_provider_capabilities_v054

DECISION = "hybrid"
FULL_WORKFLOW_REQUIRES_NODE = True
PREFERRED_NODE_ORDER = ("NeoSceneDirectorV054",)
REQUIRED_NODE_CLASSES = PREFERRED_NODE_ORDER
NODE_REQUIRED_CAPABILITIES = (
    "workflow_patch",
    "regional_conditioning",
    "scene_graph_json_execution",
    "mask_outputs",
    "layout_control_output",
    "identity_plan_execution",
)
NON_NODE_SAFE_CAPABILITIES = (
    "ui_state",
    "region_planning",
    "payload_normalization",
    "validation",
    "metadata",
    "replay_payload",
    "route_gating",
    "assistant_summary",
)
FALLBACK_POLICY = "no_fake_graph_support"


def available_node_names(nodes: Any) -> set[str]:
    """Normalize Comfy node catalogs returned as list/set/dict/object-info mappings."""
    if isinstance(nodes, dict):
        names = {str(k) for k in nodes.keys()}
        for value in nodes.values():
            if isinstance(value, str):
                names.add(value)
            elif isinstance(value, dict):
                class_type = value.get("class_type") or value.get("name") or value.get("display_name")
                if class_type:
                    names.add(str(class_type))
        return names
    if isinstance(nodes, (set, list, tuple)):
        return {str(x) for x in nodes}
    return set()


def detect_node_status(nodes: Any) -> dict[str, Any]:
    names = available_node_names(nodes)
    selected = next((name for name in PREFERRED_NODE_ORDER if name in names), None)
    return {
        "required": True,
        "decision": DECISION,
        "preferred_node_order": list(PREFERRED_NODE_ORDER),
        "required_node_classes": list(REQUIRED_NODE_CLASSES),
        "available": bool(selected),
        "selected_node": selected,
        "missing_reason": None if selected else "Scene Director custom node not detected in Comfy node catalog. Expected NeoSceneDirectorV054.",
        "fallback_policy": FALLBACK_POLICY,
        "non_node_safe_capabilities": list(NON_NODE_SAFE_CAPABILITIES),
        "node_required_capabilities": list(NODE_REQUIRED_CAPABILITIES),
    }


def workflow_readiness(*, route: dict[str, Any] | None = None, available_nodes: Any = None, enabled: bool = False) -> dict[str, Any]:
    route = route or {}
    state = route_state(
        backend=route.get("backend") or route.get("provider_id") or "comfyui",
        family=route.get("family") or "sdxl",
        loader=route.get("loader") or "checkpoint",
        workflow_mode=route.get("workflow_mode") or route.get("mode") or "generate",
        object_info=available_nodes,
    )
    node_status = detect_node_status(available_nodes)
    provider_capabilities = resolve_provider_capabilities_v054(route, object_info=available_nodes)
    patch_allowed = bool(enabled and state in ACTIVE_STATES and node_status.get("available"))
    if not enabled:
        readiness_state = "disabled"
        reason = "Scene Director is disabled."
    elif state not in ACTIVE_STATES:
        readiness_state = state
        reason = "Route is not eligible for Scene Director workflow mutation."
    elif not node_status.get("available"):
        readiness_state = "provider_gated"
        reason = str(node_status.get("missing_reason"))
    else:
        readiness_state = state
        reason = "Scene Director workflow patch is allowed."
    return {
        "extension_id": EXTENSION_ID,
        "decision": DECISION,
        "route_state": state,
        "workflow_readiness_state": readiness_state,
        "workflow_patch_allowed": patch_allowed,
        "reason": reason,
        "node_status": node_status,
        "provider_capabilities": provider_capabilities,
        "fallback_policy": FALLBACK_POLICY,
    }
