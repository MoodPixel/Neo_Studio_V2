from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

SCHEMA_VERSION = "neo.image.res4lyf_clownshark.v1"
BACKEND_STANDARD = "standard"
BACKEND_CLOWNSHARK = "res4lyf_clownshark"
CLOWNSHARK_NODE_CANDIDATES = (
    "ClownsharKSampler_Beta",  # current RES4LYF class id; display name is ClownsharKSampler
    "ClownsharKSampler",       # compatibility candidate for older installs
)
REQUIRED_CLOWNSHARK_INPUTS = {
    "eta",
    "sampler_name",
    "scheduler",
    "steps",
    "steps_to_run",
    "denoise",
    "cfg",
    "seed",
    "sampler_mode",
    "bongmath",
    "model",
    "positive",
    "negative",
    "latent_image",
}
SUPPORTED_SOURCE_NODE_CLASSES = {"KSampler"}


class Res4lyfSamplerError(ValueError):
    """Raised when a requested RES4LYF sampler graph cannot be represented safely."""


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def normalize_sampler_backend(value: Any) -> str:
    text = str(value or BACKEND_STANDARD).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "ksampler": BACKEND_STANDARD,
        "core": BACKEND_STANDARD,
        "comfy": BACKEND_STANDARD,
        "clownshark": BACKEND_CLOWNSHARK,
        "clownsharksampler": BACKEND_CLOWNSHARK,
        "clownsharksampler_beta": BACKEND_CLOWNSHARK,
        "clownsharkksampler": BACKEND_CLOWNSHARK,
        "res4lyf": BACKEND_CLOWNSHARK,
        "res4lyf_clownsharkksampler": BACKEND_CLOWNSHARK,
    }
    result = aliases.get(text, text)
    if result not in {BACKEND_STANDARD, BACKEND_CLOWNSHARK, "inherit"}:
        raise Res4lyfSamplerError(f"Unknown sampler backend '{value}'.")
    return result


def _stage_backend_requests(params: Mapping[str, Any] | None) -> dict[int, str]:
    source = _mapping(params)
    global_backend = normalize_sampler_backend(source.get("sampler_backend", BACKEND_STANDARD))
    result = {1: global_backend}
    multi = _mapping(source.get("multi_ksampler"))
    if not bool(multi.get("enabled")):
        return result
    try:
        count = int(multi.get("stage_count", 2))
    except (TypeError, ValueError):
        count = 2
    for stage in range(2, min(max(count, 2), 3) + 1):
        raw = _mapping(multi.get(f"stage{stage}"))
        backend = normalize_sampler_backend(raw.get("backend", "inherit"))
        result[stage] = global_backend if backend == "inherit" else backend
    return result


def res4lyf_sampler_requested(params: Mapping[str, Any] | None) -> bool:
    return BACKEND_CLOWNSHARK in _stage_backend_requests(params).values()


def _node_inputs(object_info: Mapping[str, Any] | None, node_class: str) -> dict[str, Any]:
    info = _mapping(object_info)
    node = _mapping(info.get(node_class))
    input_block = _mapping(node.get("input"))
    merged: dict[str, Any] = {}
    for group in ("required", "optional"):
        merged.update(_mapping(input_block.get(group)))
    return merged


def _choices_and_default(spec: Any) -> tuple[list[str], Any]:
    """Read Comfy combo choices across legacy and V3 object_info shapes."""
    choices: list[str] = []
    default = None
    if isinstance(spec, (list, tuple)) and spec:
        raw_choices = spec[0]
        meta = spec[1] if len(spec) > 1 and isinstance(spec[1], Mapping) else {}
        if isinstance(raw_choices, (list, tuple)):
            choices = [str(item) for item in raw_choices]
        elif isinstance(meta.get("options"), (list, tuple)):
            choices = [str(item) for item in meta.get("options") or []]
        default = meta.get("default")
    elif isinstance(spec, Mapping):
        raw_choices = spec.get("options") or spec.get("values")
        if isinstance(raw_choices, (list, tuple)):
            choices = [str(item) for item in raw_choices]
        default = spec.get("default")
    if default is None and choices:
        default = choices[0]
    return choices, default


def inspect_res4lyf_sampler(object_info: Mapping[str, Any] | None) -> dict[str, Any]:
    info = _mapping(object_info)
    node_class = next((name for name in CLOWNSHARK_NODE_CANDIDATES if isinstance(info.get(name), Mapping)), "")
    if not node_class:
        return {
            "schema": SCHEMA_VERSION,
            "installed": False,
            "node_class": "",
            "display_name": "ClownsharKSampler",
            "package": "ClownsharkBatwing/RES4LYF",
            "missing_inputs": sorted(REQUIRED_CLOWNSHARK_INPUTS),
            "sampler_names": [],
            "schedulers": [],
            "default_sampler": "",
            "default_scheduler": "",
            "compatible_signature": False,
        }
    inputs = _node_inputs(info, node_class)
    missing = sorted(REQUIRED_CLOWNSHARK_INPUTS - set(inputs.keys()))
    sampler_names, default_sampler = _choices_and_default(inputs.get("sampler_name"))
    schedulers, default_scheduler = _choices_and_default(inputs.get("scheduler"))
    return {
        "schema": SCHEMA_VERSION,
        "installed": True,
        "node_class": node_class,
        "display_name": "ClownsharKSampler",
        "package": "ClownsharkBatwing/RES4LYF",
        "input_names": sorted(inputs.keys()),
        "missing_inputs": missing,
        "sampler_names": sampler_names,
        "schedulers": schedulers,
        "default_sampler": str(default_sampler or ""),
        "default_scheduler": str(default_scheduler or ""),
        "compatible_signature": not missing,
        "mode": "standard_only",
        "notes": [
            "Neo uses RES4LYF's current ClownsharKSampler_Beta class when available; the visible node name in ComfyUI is ClownsharKSampler.",
            "Phase 5 uses sampler_mode=standard, steps_to_run=-1, and does not synthesize RES4LYF guides, sigmas, or option chains.",
        ],
    }


def _is_delegated(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in {"", "provider_default", "automatic", "auto", "default"})


def _float(value: Any, *, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise Res4lyfSamplerError(f"{field} must be a number.") from exc


def _bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    raise Res4lyfSamplerError(f"{field} must be true or false.")


def _resolve_global_res_settings(params: Mapping[str, Any] | None) -> dict[str, Any]:
    source = _mapping(params)
    nested = _mapping(source.get("res4lyf_sampler"))
    eta = nested.get("eta", source.get("res4lyf_eta", 0.5))
    bongmath = nested.get("bongmath", source.get("res4lyf_bongmath", True))
    return {
        "eta": _float(eta, field="RES4LYF eta"),
        "bongmath": _bool(bongmath, field="RES4LYF BongMath"),
        "sampler_mode": "standard",
        "steps_to_run": -1,
    }


def _stage_node_ids(actual_params: Mapping[str, Any] | None) -> dict[int, str]:
    actual = _mapping(actual_params)
    multi = _mapping(actual.get("multi_ksampler"))
    rows = multi.get("stage_nodes") if isinstance(multi.get("stage_nodes"), list) else []
    result: dict[int, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        try:
            stage = int(row.get("stage"))
        except (TypeError, ValueError):
            continue
        node_id = str(row.get("node_id") or "").strip()
        if node_id:
            result[stage] = node_id
    if 1 not in result:
        primary = str(actual.get("_neo_sampler_node_id") or "").strip()
        if primary:
            result[1] = primary
    return result


def _update_multi_metadata(actual: dict[str, Any], stage: int, node_class: str, backend: str) -> None:
    multi = actual.get("multi_ksampler")
    if not isinstance(multi, dict):
        return
    for key in ("stage_nodes", "resolved_stages"):
        rows = multi.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                row_stage = int(row.get("stage"))
            except (TypeError, ValueError):
                continue
            if row_stage == stage:
                row["class_type"] = node_class
                row["backend"] = backend
    if stage == 1:
        multi["route_node_class"] = node_class
    multi["res4lyf_integration"] = True


def patch_res4lyf_sampler_backend(
    workflow: Mapping[str, Any] | None,
    *,
    actual_params: Mapping[str, Any] | None,
    params: Mapping[str, Any] | None,
    object_info: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Replace selected core KSampler stages with RES4LYF ClownsharKSampler.

    The patch runs after Neo's Multi-KSampler stage expansion. This allows each
    stage to choose Standard KSampler or ClownsharKSampler without duplicating
    the stage-chain compiler. Output index 0 remains LATENT on both node types,
    so downstream graph references remain stable.
    """
    graph = deepcopy(dict(workflow or {}))
    actual = deepcopy(dict(actual_params or {}))
    backends = _stage_backend_requests(params)
    requested_stages = sorted(stage for stage, backend in backends.items() if backend == BACKEND_CLOWNSHARK)
    if not requested_stages:
        meta = {"schema": SCHEMA_VERSION, "enabled": False, "backend": BACKEND_STANDARD, "stages": []}
        actual.setdefault("sampler_backend", BACKEND_STANDARD)
        actual["res4lyf_sampler"] = meta
        return graph, actual, meta

    diagnostics = inspect_res4lyf_sampler(object_info)
    if not diagnostics.get("installed"):
        raise Res4lyfSamplerError(
            "ClownsharKSampler requires the RES4LYF custom node pack. Install ClownsharkBatwing/RES4LYF, restart ComfyUI, then Connect/Test the backend again."
        )
    if not diagnostics.get("compatible_signature"):
        missing = ", ".join(diagnostics.get("missing_inputs") or [])
        raise Res4lyfSamplerError(f"Installed ClownsharKSampler has an incompatible node signature; missing input(s): {missing}.")
    node_class = str(diagnostics.get("node_class") or "")
    stage_ids = _stage_node_ids(actual)
    settings = _resolve_global_res_settings(params)
    converted: list[dict[str, Any]] = []

    for stage in requested_stages:
        node_id = stage_ids.get(stage)
        if not node_id or not isinstance(graph.get(node_id), Mapping):
            raise Res4lyfSamplerError(f"Stage {stage} ClownsharKSampler request has no compiler-owned sampler node to replace.")
        node = _mapping(graph.get(node_id))
        class_type = str(node.get("class_type") or "")
        if class_type not in SUPPORTED_SOURCE_NODE_CLASSES:
            raise Res4lyfSamplerError(
                f"Stage {stage} cannot use ClownsharKSampler because its current sampler node is '{class_type or 'unknown'}', not core KSampler."
            )
        inputs = _mapping(node.get("inputs"))
        base_required = {"model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise"}
        missing_base = sorted(base_required - set(inputs.keys()))
        if missing_base:
            raise Res4lyfSamplerError(f"Stage {stage} KSampler is missing required input(s): {', '.join(missing_base)}.")

        sampler_name = inputs.get("sampler_name")
        scheduler = inputs.get("scheduler")
        sampler_choices = diagnostics.get("sampler_names") or []
        scheduler_choices = diagnostics.get("schedulers") or []
        if _is_delegated(sampler_name):
            sampler_name = diagnostics.get("default_sampler") or (sampler_choices[0] if sampler_choices else "")
        if _is_delegated(scheduler):
            scheduler = diagnostics.get("default_scheduler") or (scheduler_choices[0] if scheduler_choices else "")
        if not sampler_name:
            raise Res4lyfSamplerError(f"Stage {stage} ClownsharKSampler requires a concrete sampler name.")
        if not scheduler:
            raise Res4lyfSamplerError(f"Stage {stage} ClownsharKSampler requires a concrete scheduler.")
        if sampler_choices and str(sampler_name) not in {str(item) for item in sampler_choices}:
            raise Res4lyfSamplerError(f"Stage {stage} sampler '{sampler_name}' is not offered by the installed ClownsharKSampler node.")
        if scheduler_choices and str(scheduler) not in {str(item) for item in scheduler_choices}:
            raise Res4lyfSamplerError(f"Stage {stage} scheduler '{scheduler}' is not offered by the installed ClownsharKSampler node.")

        clown_inputs = {
            "eta": settings["eta"],
            "sampler_name": str(sampler_name),
            "scheduler": str(scheduler),
            "steps": inputs.get("steps"),
            "steps_to_run": settings["steps_to_run"],
            "denoise": inputs.get("denoise"),
            "cfg": inputs.get("cfg"),
            "seed": inputs.get("seed"),
            "sampler_mode": settings["sampler_mode"],
            "bongmath": settings["bongmath"],
            "model": deepcopy(inputs.get("model")),
            "positive": deepcopy(inputs.get("positive")),
            "negative": deepcopy(inputs.get("negative")),
            "latent_image": deepcopy(inputs.get("latent_image")),
        }
        graph[node_id] = {"class_type": node_class, "inputs": clown_inputs}
        _update_multi_metadata(actual, stage, node_class, BACKEND_CLOWNSHARK)
        converted.append({
            "stage": stage,
            "node_id": node_id,
            "class_type": node_class,
            "backend": BACKEND_CLOWNSHARK,
            "sampler": str(sampler_name),
            "scheduler": str(scheduler),
            "eta": settings["eta"],
            "bongmath": settings["bongmath"],
            "sampler_mode": settings["sampler_mode"],
            "steps_to_run": settings["steps_to_run"],
        })

    # Preserve Standard stages explicitly in Multi-KSampler metadata so replay
    # and Inspector can reconstruct mixed sampler-backend chains.
    for stage, backend in backends.items():
        if backend == BACKEND_STANDARD:
            node_id = stage_ids.get(stage)
            if node_id:
                _update_multi_metadata(actual, stage, "KSampler", BACKEND_STANDARD)

    global_backend = backends.get(1, BACKEND_STANDARD)
    actual["sampler_backend"] = global_backend
    meta = {
        "schema": SCHEMA_VERSION,
        "enabled": True,
        "package": diagnostics.get("package"),
        "node_class": node_class,
        "global_backend": global_backend,
        "eta": settings["eta"],
        "bongmath": settings["bongmath"],
        "sampler_mode": settings["sampler_mode"],
        "steps_to_run": settings["steps_to_run"],
        "stages": converted,
        "stage_backends": {str(stage): backend for stage, backend in sorted(backends.items())},
        "compatibility_policy": "graph_shape_and_live_node_signature",
        "upstream_documented_families": ["sd15", "sdxl", "flux", "hidream", "sd35", "auraflow", "wan", "chroma"],
        "quality_claim": "none",
    }
    actual["res4lyf_sampler"] = meta
    actual["_neo_res4lyf_sampler_backend"] = meta
    return graph, actual, meta
