from __future__ import annotations

from typing import Any

from .execution_strategy import ENGINE_CLASSIC_V054, ENGINE_LIGHTWEIGHT_REGIONAL, resolve_scene_director_execution_strategy
from .support_matrix import ACTIVE_STATES, EXTENSION_ID, get_scene_director_support
from .provider_capabilities import resolve_provider_capabilities_v054

DECISION = "hybrid"
FULL_WORKFLOW_REQUIRES_NODE = True  # Legacy/public contract: classic V054 still requires its custom node.
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
    """Legacy V054 custom-node status retained for classic-route callers."""
    names = available_node_names(nodes)
    selected = next((name for name in PREFERRED_NODE_ORDER if name in names), None)
    return {
        "required": True,
        "decision": DECISION,
        "engine": ENGINE_CLASSIC_V054,
        "preferred_node_order": list(PREFERRED_NODE_ORDER),
        "required_node_classes": list(REQUIRED_NODE_CLASSES),
        "available": bool(selected),
        "selected_node": selected,
        "missing_node_classes": [] if selected else list(REQUIRED_NODE_CLASSES),
        "missing_reason": None if selected else "Scene Director custom node not detected in Comfy node catalog. Expected NeoSceneDirectorV054.",
        "fallback_policy": FALLBACK_POLICY,
        "non_node_safe_capabilities": list(NON_NODE_SAFE_CAPABILITIES),
        "node_required_capabilities": list(NODE_REQUIRED_CAPABILITIES),
    }


def detect_execution_node_status(nodes: Any, route: dict[str, Any] | None = None) -> dict[str, Any]:
    """Resolve route-specific Scene Director execution requirements.

    Modern lightweight routing does not require NeoSceneDirectorV054. IMG-SD3 Krea2
    RAW/Turbo requires the external ComfyUI-Krea2-Regional Builder + Apply nodes;
    FLUX.2 Klein/Z-Image retain NeoRegionalLoRADelta for compatible region-targeted
    LoRA rows. The classic route keeps the exact old V054 requirement.
    """
    strategy = resolve_scene_director_execution_strategy(route or {})
    engine = str(strategy.get("engine") or "unsupported")
    if engine == ENGINE_CLASSIC_V054:
        status = detect_node_status(nodes)
        status["execution_strategy"] = strategy
        return status

    names = available_node_names(nodes)
    required = [str(item) for item in (strategy.get("required_comfy_nodes") or [])]
    missing = sorted(name for name in required if name not in names)
    available = bool(strategy.get("execution_enabled") and not missing)
    if engine != ENGINE_LIGHTWEIGHT_REGIONAL:
        available = False
    krea_external = str((strategy.get("regional_lora") or {}).get("mode") or "") == "krea2_regional_external"
    selected = ("Krea2ApplyRegional" if krea_external else "ComfyBuiltInMaskedRegionalConditioning") if available else None
    return {
        "required": bool(required),
        "custom_scene_director_node_required": False,
        "decision": DECISION,
        "engine": engine,
        "preferred_node_order": [],
        "required_node_classes": required,
        "available": available,
        "selected_node": selected,
        "missing_node_classes": missing,
        "missing_reason": (
            None
            if available
            else (
                (
                    "Krea 2 Scene Director requires januspluto/ComfyUI-Krea2-Regional; missing: " + ", ".join(missing)
                    if str(strategy.get("family") or "") in {"krea2", "krea2_turbo"}
                    else "Scene Director lightweight execution is missing required Comfy nodes: " + ", ".join(missing)
                )
                if missing
                else str(strategy.get("reason") or "Lightweight regional execution is not enabled for this route.")
            )
        ),
        "fallback_policy": "never_fallback_to_classic_v054",
        "non_node_safe_capabilities": list(NON_NODE_SAFE_CAPABILITIES),
        "node_required_capabilities": (
            ["krea2_external_regional_conditioning", "regional_lora_isolation", "single_sampler_conditioning_rewire"]
            if krea_external
            else ["masked_regional_conditioning", "single_sampler_conditioning_rewire"]
        ),
        "execution_strategy": strategy,
    }


def workflow_readiness(*, route: dict[str, Any] | None = None, available_nodes: Any = None, enabled: bool = False) -> dict[str, Any]:
    route = route or {}
    support = get_scene_director_support(route, object_info=available_nodes, node_status=available_nodes, require_node=True)
    state = str(support.get("state") or "unsupported")
    strategy = support.get("execution_strategy") or resolve_scene_director_execution_strategy(route)
    node_status = detect_execution_node_status(available_nodes, route)
    provider_capabilities = resolve_provider_capabilities_v054(route, object_info=available_nodes)
    if isinstance(provider_capabilities, dict):
        provider_capabilities = dict(provider_capabilities)
        provider_capabilities["execution_strategy"] = strategy

    patch_allowed = bool(enabled and state in ACTIVE_STATES and node_status.get("available"))
    if not enabled:
        readiness_state = "disabled"
        reason = "Scene Director is disabled."
    elif state not in ACTIVE_STATES:
        readiness_state = state
        reason = str(support.get("reason") or "Route is not eligible for Scene Director workflow mutation.")
    elif not node_status.get("available"):
        readiness_state = "provider_gated"
        reason = str(node_status.get("missing_reason") or "Scene Director execution requirements are missing.")
    else:
        readiness_state = state
        if strategy.get("engine") == ENGINE_LIGHTWEIGHT_REGIONAL:
            reason = (
                "Scene Director Krea2 Regional external engine patch is allowed."
                if str((strategy.get("regional_lora") or {}).get("mode") or "") == "krea2_regional_external"
                else "Scene Director lightweight masked regional prompt patch is allowed."
            )
        else:
            reason = "Scene Director classic V054 workflow patch is allowed."

    return {
        "extension_id": EXTENSION_ID,
        "decision": DECISION,
        "engine": strategy.get("engine"),
        "route_state": state,
        "workflow_readiness_state": readiness_state,
        "workflow_patch_allowed": patch_allowed,
        "reason": reason,
        "node_status": node_status,
        "execution_strategy": strategy,
        "provider_capabilities": provider_capabilities,
        "fallback_policy": node_status.get("fallback_policy") or FALLBACK_POLICY,
    }


__all__ = [
    "DECISION",
    "FULL_WORKFLOW_REQUIRES_NODE",
    "PREFERRED_NODE_ORDER",
    "REQUIRED_NODE_CLASSES",
    "NODE_REQUIRED_CAPABILITIES",
    "NON_NODE_SAFE_CAPABILITIES",
    "FALLBACK_POLICY",
    "available_node_names",
    "detect_node_status",
    "detect_execution_node_status",
    "workflow_readiness",
]
