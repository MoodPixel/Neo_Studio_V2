from __future__ import annotations

from copy import deepcopy
from math import isclose
from typing import Any, Mapping

SCHEMA_VERSION = "neo.image.multi_ksampler.v2"
LEGACY_SCHEMA_VERSION = "neo.image.multi_ksampler.v1"
STRATEGY = "sequential_latent_refinement"
SUPPORTED_STAGE_COUNTS = {2, 3}
SUPPORTED_BASE_NODE_CLASSES = {"KSampler"}
SUPPORTED_STAGE_BACKENDS = {"inherit", "standard", "res4lyf_clownshark"}
SUPPORTED_TRANSITION_OPERATIONS = {"none", "latent_upscale"}
SUPPORTED_LATENT_UPSCALE_METHODS = {"nearest-exact", "bilinear", "area", "bicubic", "bislerp"}
CLOWNSHARK_NODE_CLASSES = {"ClownsharKSampler_Beta", "ClownsharKSampler"}


class MultiKSamplerError(ValueError):
    """Raised when a requested Multi-KSampler graph cannot be represented safely."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int(value: Any, *, field: str, minimum: int | None = None) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise MultiKSamplerError(f"{field} must be an integer.") from exc
    if minimum is not None and result < minimum:
        raise MultiKSamplerError(f"{field} must be at least {minimum}.")
    return result


def _float(value: Any, *, field: str, minimum: float | None = None, maximum: float | None = None) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MultiKSamplerError(f"{field} must be a number.") from exc
    if minimum is not None and result < minimum:
        raise MultiKSamplerError(f"{field} must be at least {minimum}.")
    if maximum is not None and result > maximum:
        raise MultiKSamplerError(f"{field} must be at most {maximum}.")
    return result


def _inherit(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "inherit", "stage1", "stage_1", "same"})


def _normalize_backend(value: Any) -> str:
    backend = str(value or "inherit").strip().lower().replace("-", "_").replace(" ", "_")
    if backend in {"res4lyf", "clownshark", "clownsharksampler", "clownsharkksampler", "clownsharkksampler_beta"}:
        backend = "res4lyf_clownshark"
    if backend in {"ksampler", "core", "comfy"}:
        backend = "standard"
    if backend not in SUPPORTED_STAGE_BACKENDS:
        raise MultiKSamplerError("Sampler backend must be Standard KSampler, ClownsharKSampler, or Use Stage 1.")
    return backend


def _normalize_transition(stage: Mapping[str, Any], *, stage_index: int, legacy_inter_stage: Any = None) -> dict[str, Any]:
    raw = _mapping(stage.get("transition")) or _mapping(stage.get("input_transform"))
    operation = str(raw.get("operation", raw.get("type", "none")) or "none").strip().lower().replace("-", "_").replace(" ", "_")
    if not raw and stage_index == 2 and isinstance(legacy_inter_stage, str) and legacy_inter_stage.strip().lower() not in {"", "none"}:
        operation = str(legacy_inter_stage).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "off": "none",
        "disabled": "none",
        "latent": "latent_upscale",
        "upscale": "latent_upscale",
        "latent_upscale_by": "latent_upscale",
        "latentupscale": "latent_upscale",
    }
    operation = aliases.get(operation, operation)
    if operation not in SUPPORTED_TRANSITION_OPERATIONS:
        raise MultiKSamplerError(f"Before Stage {stage_index} transition must be None or Latent Upscale.")
    if operation == "none":
        return {
            "operation": "none",
            "upscale_method": str(raw.get("upscale_method", raw.get("method", "bislerp")) or "bislerp"),
            "scale_by": raw.get("scale_by", raw.get("scale", 1.5)),
        }
    method = str(raw.get("upscale_method", raw.get("method", "bislerp")) or "bislerp").strip().lower()
    if method not in SUPPORTED_LATENT_UPSCALE_METHODS:
        raise MultiKSamplerError(
            f"Before Stage {stage_index} latent upscale method must be one of: {', '.join(sorted(SUPPORTED_LATENT_UPSCALE_METHODS))}."
        )
    scale_by = _float(raw.get("scale_by", raw.get("scale", 1.5)), field=f"Before Stage {stage_index} latent upscale scale", minimum=0.01, maximum=8.0)
    return {"operation": "latent_upscale", "upscale_method": method, "scale_by": scale_by}


def normalize_multi_ksampler_request(params: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _mapping(params)
    raw = _mapping(source.get("multi_ksampler"))
    enabled = bool(raw.get("enabled", source.get("multi_ksampler_enabled", False)))
    if not enabled:
        return {
            "schema": SCHEMA_VERSION,
            "accepted_schema": str(raw.get("schema") or LEGACY_SCHEMA_VERSION),
            "enabled": False,
            "stage_count": 1,
            "strategy": STRATEGY,
            "stages": [],
            "transitions": [],
            "inter_stage_upscale": "none",
        }

    stage_count = _int(raw.get("stage_count", source.get("multi_ksampler_stage_count", 2)), field="Multi-KSampler stage count", minimum=2)
    if stage_count not in SUPPORTED_STAGE_COUNTS:
        raise MultiKSamplerError("Multi-KSampler stage count must be 2 or 3.")

    legacy_inter_stage = raw.get("inter_stage_upscale", "none")
    stages: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    for index in range(2, stage_count + 1):
        stage_key = f"stage{index}"
        stage = _mapping(raw.get(stage_key))
        default_steps = 12 if index == 2 else 8
        default_denoise = 0.25 if index == 2 else 0.15
        backend = _normalize_backend(stage.get("backend", "inherit"))
        transition = _normalize_transition(stage, stage_index=index, legacy_inter_stage=legacy_inter_stage)
        stages.append({
            "stage": index,
            "backend": backend,
            "steps": stage.get("steps", default_steps),
            "cfg": stage.get("cfg", "inherit"),
            "sampler": stage.get("sampler", "inherit"),
            "scheduler": stage.get("scheduler", "inherit"),
            "denoise": stage.get("denoise", default_denoise),
            "seed_policy": str(stage.get("seed_policy", "same") or "same").strip().lower(),
            "transition": transition,
        })
        transitions.append({"from_stage": index - 1, "to_stage": index, **transition})

    return {
        "schema": SCHEMA_VERSION,
        "accepted_schema": str(raw.get("schema") or LEGACY_SCHEMA_VERSION),
        "enabled": True,
        "stage_count": stage_count,
        "strategy": STRATEGY,
        "stages": stages,
        "transitions": transitions,
        "inter_stage_upscale": "latent_upscale" if any(row.get("operation") == "latent_upscale" for row in transitions) else "none",
    }


def _next_numeric_node_id(workflow: Mapping[str, Any]) -> int:
    ids: list[int] = []
    for key in workflow.keys():
        try:
            ids.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return (max(ids) + 1) if ids else 1


def _replace_output_ref(value: Any, *, source_id: str, replacement_id: str) -> Any:
    if isinstance(value, list):
        if len(value) == 2 and str(value[0]) == source_id and value[1] == 0:
            return [replacement_id, 0]
        return [_replace_output_ref(item, source_id=source_id, replacement_id=replacement_id) for item in value]
    if isinstance(value, tuple):
        return tuple(_replace_output_ref(item, source_id=source_id, replacement_id=replacement_id) for item in value)
    if isinstance(value, dict):
        return {key: _replace_output_ref(item, source_id=source_id, replacement_id=replacement_id) for key, item in value.items()}
    return value


def _base_sampler_id(workflow: Mapping[str, Any], actual_params: Mapping[str, Any]) -> str:
    preferred = str(actual_params.get("_neo_sampler_node_id") or "").strip()
    if preferred and isinstance(workflow.get(preferred), Mapping):
        return preferred
    for node_id, node in workflow.items():
        if isinstance(node, Mapping) and str(node.get("class_type") or "") == "KSampler":
            return str(node_id)
    return ""


def _resolve_stage(stage: Mapping[str, Any], base_inputs: Mapping[str, Any], base_seed: int) -> dict[str, Any]:
    index = _int(stage.get("stage"), field="Multi-KSampler stage", minimum=2)
    steps = _int(stage.get("steps"), field=f"Stage {index} steps", minimum=1)
    denoise = _float(stage.get("denoise"), field=f"Stage {index} denoise", minimum=0.0, maximum=1.0)

    cfg_raw = stage.get("cfg")
    cfg = base_inputs.get("cfg") if _inherit(cfg_raw) else _float(cfg_raw, field=f"Stage {index} CFG", minimum=0.0)
    if isinstance(cfg, (list, tuple, dict)) or cfg is None:
        raise MultiKSamplerError(f"Stage {index} cannot inherit CFG from this sampler graph.")

    sampler_raw = stage.get("sampler")
    sampler = base_inputs.get("sampler_name") if _inherit(sampler_raw) else str(sampler_raw).strip()
    if not sampler or isinstance(sampler, (list, tuple, dict)):
        raise MultiKSamplerError(f"Stage {index} cannot inherit sampler from this sampler graph.")

    scheduler_raw = stage.get("scheduler")
    scheduler = base_inputs.get("scheduler") if _inherit(scheduler_raw) else str(scheduler_raw).strip()
    if not scheduler or isinstance(scheduler, (list, tuple, dict)):
        raise MultiKSamplerError(f"Stage {index} cannot inherit scheduler from this sampler graph.")

    seed_policy = str(stage.get("seed_policy") or "same").strip().lower()
    if seed_policy == "same":
        seed = base_seed
    elif seed_policy == "increment":
        seed = base_seed + (index - 1)
    else:
        raise MultiKSamplerError(f"Stage {index} seed policy must be 'same' or 'increment'.")

    backend = _normalize_backend(stage.get("backend", "inherit"))
    transition = _mapping(stage.get("transition"))
    return {
        "stage": index,
        "backend": backend,
        "steps": steps,
        "cfg": float(cfg),
        "sampler": str(sampler),
        "scheduler": str(scheduler),
        "denoise": denoise,
        "seed_policy": seed_policy,
        "seed": seed,
        "transition": deepcopy(transition),
    }


def _backend_from_node_class(class_type: str) -> str:
    if class_type in CLOWNSHARK_NODE_CLASSES:
        return "res4lyf_clownshark"
    if class_type == "KSampler":
        return "standard"
    return "unknown"


def _same_value(left: Any, right: Any) -> bool:
    try:
        return isclose(float(left), float(right), rel_tol=1e-9, abs_tol=1e-9)
    except (TypeError, ValueError):
        return str(left) == str(right)


def verify_multi_ksampler_workflow(
    workflow: Mapping[str, Any] | None,
    actual_params: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Verify the physical Stage 1/2/3 chain against compiler-owned metadata.

    This is intentionally separate from shared Stage 1 Parameter Integrity. It
    proves every later sampler and optional LatentUpscaleBy transition before
    Comfy receives the graph.
    """
    graph = _mapping(workflow)
    actual = _mapping(actual_params)
    meta = _mapping(actual.get("multi_ksampler"))
    if not meta.get("enabled"):
        return {"schema": "neo.image.multi_ksampler.integrity.v1", "status": "disabled", "mismatches": [], "checked_stages": 0, "checked_transitions": 0}

    mismatches: list[dict[str, Any]] = []
    stage_rows = [dict(row) for row in (meta.get("stage_nodes") or []) if isinstance(row, Mapping)]
    resolved_rows = {int(row.get("stage")): dict(row) for row in (meta.get("resolved_stages") or []) if isinstance(row, Mapping) and str(row.get("stage") or "").isdigit()}
    transition_rows = {int(row.get("to_stage")): dict(row) for row in (meta.get("transition_nodes") or []) if isinstance(row, Mapping) and str(row.get("to_stage") or "").isdigit()}
    stages = {int(row.get("stage")): row for row in stage_rows if str(row.get("stage") or "").isdigit()}

    def mismatch(stage: int | str, field: str, expected: Any, observed: Any, *, node_id: str = "") -> None:
        mismatches.append({"stage": stage, "field": field, "expected": expected, "observed": observed, "node_id": node_id})

    ordered = sorted(stages)
    if ordered != list(range(1, int(meta.get("stage_count") or 1) + 1)):
        mismatch("pipeline", "stage_order", list(range(1, int(meta.get("stage_count") or 1) + 1)), ordered)

    previous_stage_id = ""
    checked_stages = 0
    checked_transitions = 0
    for stage in ordered:
        row = stages[stage]
        node_id = str(row.get("node_id") or "")
        node = _mapping(graph.get(node_id))
        if not node:
            mismatch(stage, "node", "present", "missing", node_id=node_id)
            previous_stage_id = node_id
            continue
        checked_stages += 1
        class_type = str(node.get("class_type") or "")
        expected_backend = str(row.get("backend") or "standard")
        observed_backend = _backend_from_node_class(class_type)
        if expected_backend != observed_backend:
            mismatch(stage, "backend", expected_backend, observed_backend, node_id=node_id)
        inputs = _mapping(node.get("inputs"))

        if stage >= 2:
            resolved = resolved_rows.get(stage, {})
            for meta_key, input_key in (("steps", "steps"), ("cfg", "cfg"), ("sampler", "sampler_name"), ("scheduler", "scheduler"), ("denoise", "denoise"), ("seed", "seed")):
                if meta_key in resolved and not _same_value(resolved.get(meta_key), inputs.get(input_key)):
                    mismatch(stage, meta_key, resolved.get(meta_key), inputs.get(input_key), node_id=node_id)

            transition = transition_rows.get(stage)
            if transition and transition.get("operation") == "latent_upscale":
                transition_id = str(transition.get("node_id") or "")
                transition_node = _mapping(graph.get(transition_id))
                checked_transitions += 1
                if not transition_node:
                    mismatch(stage, "transition_node", "LatentUpscaleBy", "missing", node_id=transition_id)
                    expected_latent = [transition_id, 0]
                else:
                    if str(transition_node.get("class_type") or "") != "LatentUpscaleBy":
                        mismatch(stage, "transition_class", "LatentUpscaleBy", transition_node.get("class_type"), node_id=transition_id)
                    transition_inputs = _mapping(transition_node.get("inputs"))
                    if transition_inputs.get("samples") != [previous_stage_id, 0]:
                        mismatch(stage, "transition_input", [previous_stage_id, 0], transition_inputs.get("samples"), node_id=transition_id)
                    if not _same_value(transition.get("scale_by"), transition_inputs.get("scale_by")):
                        mismatch(stage, "transition_scale_by", transition.get("scale_by"), transition_inputs.get("scale_by"), node_id=transition_id)
                    if str(transition.get("upscale_method") or "") != str(transition_inputs.get("upscale_method") or ""):
                        mismatch(stage, "transition_upscale_method", transition.get("upscale_method"), transition_inputs.get("upscale_method"), node_id=transition_id)
                    expected_latent = [transition_id, 0]
            else:
                expected_latent = [previous_stage_id, 0]
            if inputs.get("latent_image") != expected_latent:
                mismatch(stage, "latent_image", expected_latent, inputs.get("latent_image"), node_id=node_id)
        previous_stage_id = node_id

    terminal = str(meta.get("terminal_sampler_node_id") or "")
    if ordered and terminal != str(stages[ordered[-1]].get("node_id") or ""):
        mismatch("pipeline", "terminal_sampler_node_id", stages[ordered[-1]].get("node_id"), terminal)

    return {
        "schema": "neo.image.multi_ksampler.integrity.v1",
        "status": "mismatch" if mismatches else "verified",
        "mismatches": mismatches,
        "checked_stages": checked_stages,
        "checked_transitions": checked_transitions,
        "terminal_sampler_node_id": terminal,
    }


def patch_multi_ksampler_workflow(
    workflow: Mapping[str, Any] | None,
    *,
    actual_params: Mapping[str, Any] | None,
    params: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Insert Stage 2/3 KSampler refinement passes after Neo's base sampler.

    Phase 6 productionizes the direct latent chain from Phase 4 and allows an
    optional core ``LatentUpscaleBy`` transition before Stage 2 and/or Stage 3.
    Sampler backend replacement (RES4LYF) still runs after this compiler so one
    stage-chain implementation remains authoritative.
    """
    graph = deepcopy(dict(workflow or {}))
    actual = deepcopy(dict(actual_params or {}))
    request = normalize_multi_ksampler_request(params)
    if not request.get("enabled"):
        actual["multi_ksampler"] = request
        return graph, actual, request

    sampler_id = _base_sampler_id(graph, actual)
    if not sampler_id:
        raise MultiKSamplerError("Multi-KSampler requires a compiler-declared base KSampler node on this route.")
    base_node = graph.get(sampler_id)
    if not isinstance(base_node, Mapping):
        raise MultiKSamplerError("Multi-KSampler base sampler node is missing from the final workflow.")
    class_type = str(base_node.get("class_type") or "")
    if class_type not in SUPPORTED_BASE_NODE_CLASSES:
        raise MultiKSamplerError(f"Multi-KSampler does not yet support base sampler node '{class_type}'.")
    base_inputs = _mapping(base_node.get("inputs"))
    required = {"model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"}
    missing = sorted(key for key in required if key not in base_inputs)
    if missing:
        raise MultiKSamplerError(f"Multi-KSampler base KSampler is missing required input(s): {', '.join(missing)}.")

    try:
        base_seed = int(base_inputs.get("seed"))
    except (TypeError, ValueError) as exc:
        raise MultiKSamplerError("Multi-KSampler requires a concrete Stage 1 seed before compilation.") from exc

    original_node_ids = list(graph.keys())
    next_id = _next_numeric_node_id(graph)
    previous_sampler_id = sampler_id
    resolved_stages: list[dict[str, Any]] = []
    transition_nodes: list[dict[str, Any]] = []
    base_backend = _normalize_backend((params or {}).get("sampler_backend") or "standard") if isinstance(params, Mapping) else "standard"
    if base_backend == "inherit":
        base_backend = "standard"
    stage_nodes: list[dict[str, Any]] = [{"stage": 1, "node_id": sampler_id, "class_type": class_type, "backend": base_backend}]

    for requested_stage in request.get("stages") or []:
        resolved = _resolve_stage(requested_stage, base_inputs, base_seed)
        stage_index = int(resolved["stage"])
        latent_input: list[Any] = [previous_sampler_id, 0]
        transition = _mapping(resolved.get("transition"))
        if transition.get("operation") == "latent_upscale":
            transition_id = str(next_id)
            next_id += 1
            graph[transition_id] = {
                "class_type": "LatentUpscaleBy",
                "inputs": {
                    "samples": [previous_sampler_id, 0],
                    "upscale_method": str(transition.get("upscale_method") or "bislerp"),
                    "scale_by": float(transition.get("scale_by")),
                },
            }
            latent_input = [transition_id, 0]
            transition_nodes.append({
                "from_stage": stage_index - 1,
                "to_stage": stage_index,
                "operation": "latent_upscale",
                "node_id": transition_id,
                "class_type": "LatentUpscaleBy",
                "upscale_method": str(transition.get("upscale_method") or "bislerp"),
                "scale_by": float(transition.get("scale_by")),
            })
        else:
            transition_nodes.append({"from_stage": stage_index - 1, "to_stage": stage_index, "operation": "none", "node_id": ""})

        node_id = str(next_id)
        next_id += 1
        stage_inputs = deepcopy(base_inputs)
        stage_inputs.update({
            "latent_image": latent_input,
            "seed": resolved["seed"],
            "steps": resolved["steps"],
            "cfg": resolved["cfg"],
            "sampler_name": resolved["sampler"],
            "scheduler": resolved["scheduler"],
            "denoise": resolved["denoise"],
        })
        graph[node_id] = {"class_type": "KSampler", "inputs": stage_inputs}
        resolved_stages.append({**resolved, "node_id": node_id, "latent_input": deepcopy(latent_input)})
        effective_backend = base_backend if resolved.get("backend") == "inherit" else resolved.get("backend", "standard")
        stage_nodes.append({"stage": stage_index, "node_id": node_id, "class_type": "KSampler", "backend": effective_backend})
        previous_sampler_id = node_id

    terminal_id = previous_sampler_id
    for node_id in original_node_ids:
        if str(node_id) == sampler_id:
            continue
        node = graph.get(node_id)
        if not isinstance(node, Mapping):
            continue
        graph[node_id] = _replace_output_ref(node, source_id=sampler_id, replacement_id=terminal_id)

    metadata = {
        **request,
        "state": "applied",
        "base_sampler_node_id": sampler_id,
        "terminal_sampler_node_id": terminal_id,
        "stage_nodes": stage_nodes,
        "transition_nodes": transition_nodes,
        "resolved_stages": resolved_stages,
        "route_node_class": class_type,
        "consumer_rewire": "stage1_output_to_terminal_stage_output",
        "quality_claim": "none",
        "notes": [
            "Stage 1 is the existing Parameters KSampler.",
            "Stage 2/3 are independent latent refinement passes and may add detail or may degrade an image depending on settings.",
            "Optional inter-stage LatentUpscaleBy uses ComfyUI core and preserves the exact user-selected scale and method.",
        ],
    }
    actual["multi_ksampler"] = metadata
    actual["_neo_multi_ksampler_terminal_node_id"] = terminal_id
    return graph, actual, metadata


__all__ = [
    "SCHEMA_VERSION",
    "LEGACY_SCHEMA_VERSION",
    "STRATEGY",
    "MultiKSamplerError",
    "normalize_multi_ksampler_request",
    "patch_multi_ksampler_workflow",
    "verify_multi_ksampler_workflow",
]
