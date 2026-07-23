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


def _same_ref(value: Any, node_id: str, output: int = 0) -> bool:
    """Return whether a Comfy graph value points to one node output."""
    return (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and str(value[0]) == str(node_id)
        and value[1] == output
    )


def _as_step(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _payload_midstep_sampler_ids(payload: Any) -> set[str]:
    """Read persisted Scene Director midpoint ids when replay metadata exists.

    The normal submit path may only provide the compact extension payload, so
    this is deliberately best-effort. It supplements the graph-shape fallback
    in ``_find_midstep_continuations`` rather than being required for safety.
    """
    found: set[str] = set()

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            schema = str(value.get("schema") or "").lower()
            execution = str(value.get("execution") or "").lower()
            is_midstep = "midstep" in schema or execution == "mid_sampler_split"
            if is_midstep:
                repair_id = value.get("repair_sampler_node_id")
                if repair_id is not None:
                    found.add(str(repair_id))
                for lane in value.get("lanes") or []:
                    if isinstance(lane, dict) and lane.get("sampler_node_id") is not None:
                        found.add(str(lane["sampler_node_id"]))
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return found


def _find_midstep_continuations(
    graph: dict[str, Any],
    *,
    sampler_node_id: str,
    payload: Any,
) -> list[str]:
    """Find Scene Director's second segment(s) that continue the main latent.

    A Character Trait midpoint lane is a ``KSamplerAdvanced`` with noise
    disabled, a non-zero start step, and a latent input from the main sampler.
    This shape is intentionally narrower than “every downstream sampler” so
    unrelated end passes keep their own model authority. Persisted controller
    ids are preferred when available; the structural fallback handles fresh
    submits and older replay payloads that omitted the controller metadata.
    """
    hinted = _payload_midstep_sampler_ids(payload)
    candidates: list[str] = []
    for raw_id, node in (graph or {}).items():
        node_id = str(raw_id)
        if node_id == str(sampler_node_id) or not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") != "KSamplerAdvanced":
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if not _same_ref(inputs.get("latent_image"), str(sampler_node_id), 0):
            continue
        start_at_step = _as_step(inputs.get("start_at_step"), 0)
        end_at_step = _as_step(inputs.get("end_at_step"), 0)
        if start_at_step <= 0 or end_at_step <= start_at_step:
            continue
        if str(inputs.get("add_noise") or "").strip().lower() not in {"disable", "disabled", "off", "false", "0"}:
            continue
        candidates.append(node_id)

    if hinted:
        hinted_candidates = [node_id for node_id in candidates if node_id in hinted]
        if hinted_candidates:
            return sorted(hinted_candidates, key=lambda item: (_as_step((graph[item].get("inputs") or {}).get("start_at_step"), 0), item))
    return sorted(candidates, key=lambda item: (_as_step((graph[item].get("inputs") or {}).get("start_at_step"), 0), item))


def _synchronize_midstep_model_paths(
    graph: dict[str, Any],
    *,
    sampler_node_id: str,
    patched_model_ref: list[Any],
    payload: Any,
) -> dict[str, Any]:
    """Keep midpoint continuation samplers on the exact main MODEL stack."""
    continuation_ids = _find_midstep_continuations(
        graph,
        sampler_node_id=sampler_node_id,
        payload=payload,
    )
    changed: list[str] = []
    previous_refs: dict[str, list[Any]] = {}
    for node_id in continuation_ids:
        node = graph.get(node_id)
        inputs = node.get("inputs") if isinstance(node, dict) and isinstance(node.get("inputs"), dict) else None
        if inputs is None:
            continue
        previous = inputs.get("model")
        if isinstance(previous, (list, tuple)) and len(previous) >= 2:
            previous_refs[node_id] = list(previous)
        if list(previous or []) != list(patched_model_ref):
            inputs["model"] = deepcopy(patched_model_ref)
            changed.append(node_id)
    return {
        "status": "synchronized" if continuation_ids else "not_applicable",
        "sampler_node_id": str(sampler_node_id),
        "continuation_sampler_ids": continuation_ids,
        "changed_sampler_ids": changed,
        "previous_model_refs": previous_refs,
        "model_ref": deepcopy(patched_model_ref),
        "policy": "Every Scene Director midpoint continuation must use the same CFG/model wrapper as the main denoise segment.",
    }


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
    - the sampler's model input is the primary edge rewritten
    - Scene Director midpoint continuations inherit that exact MODEL stack
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

    # Scene Director's optional midpoint repair is a continuation of the main
    # denoise schedule, not a new model pass. If CFG Fix is applied after
    # Scene Director (the common path when no regional reference asset is
    # active), the repair sampler still points at the raw V054 output unless
    # this boundary explicitly propagates the wrapper. The old graph therefore
    # switched from ``DynamicThresholdingFull -> V054`` to raw ``V054`` at the
    # midpoint, which produced the melted/cooked result seen in replay.
    continuation_model_path = _synchronize_midstep_model_paths(
        graph,
        sampler_node_id=sampler_key,
        patched_model_ref=patched_ref,
        payload=payload,
    )

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
    patch["continuation_model_path"] = continuation_model_path

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
