from __future__ import annotations

from copy import deepcopy
from typing import Any

from .payload_schema import EXTENSION_ID, FULL_MODE_DEFAULTS
from .validation import validate_and_normalize_payload

PHASE = "E"
SIMPLE_NODE = "DynamicThresholdingSimple"
FULL_NODE = "DynamicThresholdingFull"


def _next_graph_id(workflow: dict[str, Any], preferred: int | str | None = None) -> str:
    if preferred is not None:
        candidate = str(preferred)
        if candidate not in workflow:
            return candidate
    numeric_ids = []
    for key in workflow:
        try:
            numeric_ids.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return str((max(numeric_ids) if numeric_ids else 0) + 1)


def _copy_model_ref(model_ref: list[Any] | tuple[Any, ...] | None) -> list[Any]:
    if isinstance(model_ref, (list, tuple)) and len(model_ref) >= 2:
        return [str(model_ref[0]), int(model_ref[1]) if isinstance(model_ref[1], int) or str(model_ref[1]).isdigit() else model_ref[1]]
    return ["1", 0]


def dynamic_thresholding_node_inputs(params: dict[str, Any], model_ref: list[Any] | tuple[Any, ...]) -> dict[str, Any]:
    """Return Comfy node inputs for the selected CFG Fix mode."""
    mode = str(params.get("mode") or "simple")
    inputs: dict[str, Any] = {
        "model": _copy_model_ref(model_ref),
        "mimic_scale": float(params.get("mimic_scale", 7.0)),
        "threshold_percentile": float(params.get("threshold_percentile", 1.0)),
    }
    if mode == "full":
        inputs.update(deepcopy(FULL_MODE_DEFAULTS))
    return inputs


def dynamic_thresholding_class_type(params: dict[str, Any]) -> str:
    return FULL_NODE if str(params.get("mode") or "simple") == "full" else SIMPLE_NODE


def build_workflow_patch_summary(
    *,
    node_id: str | None,
    node_class: str | None,
    previous_model_ref: list[Any] | None,
    patched_model_ref: list[Any] | None,
    validation_result: dict[str, Any],
) -> dict[str, Any]:
    block = validation_result.get("block") if isinstance(validation_result, dict) else {}
    metadata = block.get("metadata", {}) if isinstance(block, dict) else {}
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "applied": bool(node_id and node_class and validation_result.get("workflow_patch_allowed")),
        "node_id": node_id or "",
        "node_class": node_class or "",
        "previous_model_ref": deepcopy(previous_model_ref or []),
        "patched_model_ref": deepcopy(patched_model_ref or []),
        "route": deepcopy(validation_result.get("route", {})),
        "node_status": deepcopy(validation_result.get("node_status", {})),
        "reason": metadata.get("reason", "") if isinstance(metadata, dict) else "",
    }


def apply_cfg_fix_dynamic_thresholding_patch(
    workflow: dict[str, Any],
    *,
    payload: dict[str, Any] | None,
    route: dict[str, Any] | None,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None,
    cfg: float | int | str,
    model_ref: list[Any] | tuple[Any, ...] | None,
    sampler_node_id: str | int = "5",
    sampler_model_input: str = "model",
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    """Validate and patch a Comfy graph with CFG Fix / Dynamic Thresholding.

    The function is extension-local and graph-safe:
    - disabled/gated/skipped payloads return the original graph unchanged
    - node insertion occurs only after server-side route/node validation
    - the sampler's model input is the only downstream edge rewritten
    """
    graph = deepcopy(workflow or {})
    validation = validate_and_normalize_payload(payload, route=route, available_nodes=available_nodes, cfg=cfg)
    previous_ref = _copy_model_ref(model_ref)
    sampler_key = str(sampler_node_id)

    if not validation.get("workflow_patch_allowed"):
        return {
            "workflow": graph,
            "model_ref": previous_ref,
            "validation": validation,
            "workflow_patch": build_workflow_patch_summary(
                node_id=None,
                node_class=None,
                previous_model_ref=previous_ref,
                patched_model_ref=previous_ref,
                validation_result=validation,
            ),
            "mutated": False,
        }

    params = validation.get("params") or {}
    node_class = dynamic_thresholding_class_type(params)
    node_id = _next_graph_id(graph, next_node_id)
    graph[node_id] = {
        "class_type": node_class,
        "inputs": dynamic_thresholding_node_inputs(params, previous_ref),
    }
    patched_ref = [node_id, 0]

    sampler = graph.get(sampler_key)
    if isinstance(sampler, dict):
        sampler_inputs = sampler.setdefault("inputs", {})
        if isinstance(sampler_inputs, dict):
            sampler_inputs[sampler_model_input] = patched_ref

    patch = build_workflow_patch_summary(
        node_id=node_id,
        node_class=node_class,
        previous_model_ref=previous_ref,
        patched_model_ref=patched_ref,
        validation_result=validation,
    )
    patch["applied"] = True
    patch["sampler_node_id"] = sampler_key
    patch["sampler_model_input"] = sampler_model_input

    return {
        "workflow": graph,
        "model_ref": patched_ref,
        "validation": validation,
        "workflow_patch": patch,
        "mutated": True,
    }


def workflow_patch_not_implemented() -> dict:
    return {
        "extension_id": EXTENSION_ID,
        "implemented": True,
        "phase": PHASE,
        "reason": "Phase E implements Comfy MODEL-in/MODEL-out workflow graph patching for available SD checkpoint routes.",
    }
