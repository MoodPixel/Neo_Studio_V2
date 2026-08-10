from __future__ import annotations

from copy import deepcopy
from math import isclose
from typing import Any, Mapping

SCHEMA_VERSION = "neo.image.parameter_integrity.v1"
SUBMISSION_SCHEMA_VERSION = "neo.image.parameter_integrity_submission.v1"

# These are the shared generation controls whose values must survive every
# boundary unchanged when the user supplied a concrete value.
TRACKED_FIELDS: tuple[str, ...] = (
    "width",
    "height",
    "steps",
    "cfg",
    "true_cfg",
    "sampler",
    "sampler_backend",
    "scheduler",
    "denoise",
    "batch_count",
    "seed",
    "flux_guidance",
    "guidance",
    "clip_skip",
)

# Values that explicitly delegate choice to the provider are not treated as
# concrete user values for mismatch blocking.
DELEGATED_VALUES = {"", "provider_default", "automatic", "auto", "default", None}

# Aliases used by provider payloads / compiler diagnostics.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "width": ("width",),
    "height": ("height",),
    "steps": ("steps", "num_inference_steps"),
    "cfg": ("cfg", "cfg_scale", "sampler_cfg"),
    "true_cfg": ("true_cfg",),
    "sampler": ("sampler", "sampler_name"),
    "sampler_backend": ("sampler_backend",),
    "scheduler": ("scheduler",),
    "denoise": ("denoise", "denoising_strength", "strength"),
    # Forge exposes image count as n_iter while keeping batch_size=1.
    "batch_count": ("batch_count", "n_iter", "batch_size"),
    "seed": ("seed", "actual_seed", "noise_seed"),
    "flux_guidance": ("flux_guidance",),
    "guidance": ("guidance", "guidance_scale"),
    "clip_skip": ("clip_skip",),
}

_NUMERIC_FIELDS = {
    "width", "height", "steps", "cfg", "true_cfg", "denoise", "batch_count",
    "seed", "flux_guidance", "guidance", "clip_skip",
}


def _plain(value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, (int, float, str)):
        return value
    return str(value)


def _has_concrete_value(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {str(item).lower() for item in DELEGATED_VALUES if isinstance(item, str)}
    return value not in DELEGATED_VALUES


def _lookup(values: Mapping[str, Any] | None, field: str) -> tuple[bool, Any, str]:
    source = values if isinstance(values, Mapping) else {}
    for key in _FIELD_ALIASES.get(field, (field,)):
        if key in source and source.get(key) is not None:
            return True, _plain(source.get(key)), key
    return False, None, ""


def snapshot_parameter_values(values: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return a privacy-safe snapshot of generation controls only."""
    result: dict[str, Any] = {}
    aliases: dict[str, str] = {}
    for field in TRACKED_FIELDS:
        present, value, alias = _lookup(values, field)
        if present:
            result[field] = value
            if alias and alias != field:
                aliases[field] = alias
    if aliases:
        result["_aliases"] = aliases
    return result


def _normalize_for_compare(field: str, value: Any) -> Any:
    if field in _NUMERIC_FIELDS:
        try:
            return float(value)
        except (TypeError, ValueError):
            return value
    if isinstance(value, str):
        return value.strip().lower()
    return value


def values_match(field: str, requested: Any, observed: Any) -> bool:
    left = _normalize_for_compare(field, requested)
    right = _normalize_for_compare(field, observed)
    if isinstance(left, float) and isinstance(right, float):
        return isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    return left == right


def _submission_fields(submission: Mapping[str, Any] | None, key: str) -> dict[str, Any]:
    payload = submission if isinstance(submission, Mapping) else {}
    value = payload.get(key)
    if isinstance(value, Mapping):
        return snapshot_parameter_values(value)
    return {}


def start_parameter_integrity_trace(raw_params: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(raw_params or {})
    submission = raw.get("_neo_parameter_integrity_submission")
    trace: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "tracing",
        "policy": "explicit_user_values_are_authoritative",
        "blocking_policy": "block_before_queue_on_concrete_sampling_mismatch",
        "tracked_fields": list(TRACKED_FIELDS),
        "stages": {
            "ui_before_build": _submission_fields(submission, "ui_before_build"),
            "client_payload": _submission_fields(submission, "client_payload") or snapshot_parameter_values(raw),
            "api_received": snapshot_parameter_values(raw),
        },
        "comparisons": [],
        "mismatches": [],
        "unverified": [],
    }
    return recalculate_parameter_integrity(trace)


def advance_parameter_integrity_trace(trace: Mapping[str, Any] | None, stage: str, values: Mapping[str, Any] | None) -> dict[str, Any]:
    out = deepcopy(dict(trace or {}))
    out.setdefault("schema_version", SCHEMA_VERSION)
    out.setdefault("policy", "explicit_user_values_are_authoritative")
    out.setdefault("blocking_policy", "block_before_queue_on_concrete_sampling_mismatch")
    stages = out.setdefault("stages", {})
    stages[str(stage)] = snapshot_parameter_values(values)
    return recalculate_parameter_integrity(out)


def _comparison(from_stage: str, to_stage: str, field: str, requested: Any, observed: Any, status: str, reason: str = "") -> dict[str, Any]:
    row = {
        "from_stage": from_stage,
        "to_stage": to_stage,
        "field": field,
        "requested": requested,
        "observed": observed,
        "status": status,
    }
    if reason:
        row["reason"] = reason
    return row


def recalculate_parameter_integrity(trace: Mapping[str, Any] | None) -> dict[str, Any]:
    out = deepcopy(dict(trace or {}))
    stages = out.get("stages") if isinstance(out.get("stages"), Mapping) else {}
    order = [
        name for name in (
            "ui_before_build", "client_payload", "api_received", "api_normalized",
            "neojob", "provider_actual", "workflow_final",
        ) if isinstance(stages.get(name), Mapping) and stages.get(name)
    ]
    comparisons: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    unverified: list[dict[str, Any]] = []

    for left_name, right_name in zip(order, order[1:]):
        left = stages.get(left_name) or {}
        right = stages.get(right_name) or {}
        for field in TRACKED_FIELDS:
            if field not in left:
                continue
            requested = left.get(field)
            # Delegated/sentinel values intentionally allow the next boundary to choose.
            if not _has_concrete_value(requested):
                comparisons.append(_comparison(left_name, right_name, field, requested, right.get(field), "delegated"))
                continue
            # Seed -1 means random seed and is expected to become a concrete runtime seed.
            if field == "seed":
                try:
                    if int(float(requested)) < 0:
                        comparisons.append(_comparison(left_name, right_name, field, requested, right.get(field), "delegated", "negative seed requests runtime randomization"))
                        continue
                except (TypeError, ValueError):
                    pass
            if field not in right:
                row = _comparison(left_name, right_name, field, requested, None, "unverified", "field not represented at this boundary")
                comparisons.append(row)
                unverified.append(row)
                continue
            observed = right.get(field)
            if values_match(field, requested, observed):
                comparisons.append(_comparison(left_name, right_name, field, requested, observed, "match"))
            else:
                row = _comparison(left_name, right_name, field, requested, observed, "mismatch")
                comparisons.append(row)
                mismatches.append(row)

    out["comparisons"] = comparisons
    out["mismatches"] = mismatches
    out["unverified"] = unverified
    if mismatches:
        out["status"] = "mismatch"
    elif order and order[-1] == "workflow_final":
        out["status"] = "verified" if not unverified else "verified_with_untracked_boundaries"
    elif len(order) >= 2:
        out["status"] = "verified_to_last_available_boundary" if not unverified else "partial"
    else:
        out["status"] = "tracing"
    out["summary"] = {
        "stage_count": len(order),
        "comparison_count": len(comparisons),
        "mismatch_count": len(mismatches),
        "unverified_count": len(unverified),
        "last_stage": order[-1] if order else "",
    }
    return out


def _sampler_like_nodes(workflow: Mapping[str, Any] | None) -> list[tuple[str, Mapping[str, Any]]]:
    graph = workflow if isinstance(workflow, Mapping) else {}
    rows: list[tuple[str, Mapping[str, Any]]] = []
    for node_id, node in graph.items():
        if not isinstance(node, Mapping):
            continue
        class_type = str(node.get("class_type") or "")
        if (
            class_type in {"KSampler", "KSamplerAdvanced", "LanPaint_KSampler", "ClownsharKSampler_Beta", "ClownsharKSampler"}
            or "SamplerCustom" in class_type
            or class_type == "KSamplerSelect"
        ):
            rows.append((str(node_id), node))
    return rows


def extract_workflow_parameter_values(workflow: Mapping[str, Any] | None, actual_params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Extract the values physically serialized into the final Comfy graph.

    For custom-sampler graphs, steps/CFG can live on scheduler/guider nodes rather
    than the sampler node, so this intentionally scans the full graph.
    """
    graph = workflow if isinstance(workflow, Mapping) else {}
    actual = actual_params if isinstance(actual_params, Mapping) else {}
    values: dict[str, Any] = {}
    proof: dict[str, Any] = {"sampler_nodes": []}

    sampler_id = str(actual.get("_neo_sampler_node_id") or "")
    samplers = _sampler_like_nodes(graph)
    for node_id, node in samplers:
        proof["sampler_nodes"].append({"node_id": node_id, "class_type": str(node.get("class_type") or "")})
    sampler_node = None
    if sampler_id and isinstance(graph.get(sampler_id), Mapping):
        sampler_node = graph.get(sampler_id)
        proof["declared_sampler_node_id"] = sampler_id
    else:
        sampler_node = next((node for _node_id, node in samplers if str(node.get("class_type") or "") in {"KSampler", "KSamplerAdvanced", "LanPaint_KSampler", "ClownsharKSampler_Beta", "ClownsharKSampler"}), None)
        if sampler_node is not None:
            proof["declared_sampler_node_id"] = next((node_id for node_id, node in samplers if node is sampler_node), "")

    if isinstance(sampler_node, Mapping):
        inputs = sampler_node.get("inputs") if isinstance(sampler_node.get("inputs"), Mapping) else {}
        sampler_class = str(sampler_node.get("class_type") or "")
        if sampler_class in {"ClownsharKSampler_Beta", "ClownsharKSampler"}:
            values["sampler_backend"] = "res4lyf_clownshark"
        elif sampler_class in {"KSampler", "KSamplerAdvanced"}:
            values["sampler_backend"] = "standard"
        for field, key in (("steps", "steps"), ("cfg", "cfg"), ("sampler", "sampler_name"), ("scheduler", "scheduler"), ("denoise", "denoise"), ("seed", "seed")):
            if key in inputs and not isinstance(inputs.get(key), (list, tuple, dict)):
                values[field] = _plain(inputs.get(key))

    # Custom sampler graphs externalize some controls.
    for node_id, node in graph.items():
        if not isinstance(node, Mapping):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), Mapping) else {}
        if "sampler" not in values and class_type == "KSamplerSelect" and "sampler_name" in inputs:
            values["sampler"] = _plain(inputs.get("sampler_name"))
        if "steps" not in values and class_type.endswith("Scheduler") and "steps" in inputs:
            values["steps"] = _plain(inputs.get("steps"))
        if "scheduler" not in values and class_type.endswith("Scheduler"):
            if "scheduler" in inputs and not isinstance(inputs.get("scheduler"), (list, tuple, dict)):
                values["scheduler"] = _plain(inputs.get("scheduler"))
            elif class_type == "Ideogram4Scheduler":
                # Specialized scheduler node has no string selector: its class is the scheduler choice.
                values["scheduler"] = "ideogram4"
        if "cfg" not in values and class_type in {"CFGGuider", "DualModelGuider"} and "cfg" in inputs:
            values["cfg"] = _plain(inputs.get("cfg"))
        if "seed" not in values and class_type == "RandomNoise" and "noise_seed" in inputs:
            values["seed"] = _plain(inputs.get("noise_seed"))
        if class_type in {"EmptyLatentImage", "EmptyFlux2LatentImage"}:
            if "width" not in values and "width" in inputs:
                values["width"] = _plain(inputs.get("width"))
            if "height" not in values and "height" in inputs:
                values["height"] = _plain(inputs.get("height"))
            if "batch_count" not in values and "batch_size" in inputs:
                values["batch_count"] = _plain(inputs.get("batch_size"))
        if class_type == "RepeatLatentBatch" and "amount" in inputs:
            values["batch_count"] = _plain(inputs.get("amount"))
        if class_type in {"FluxGuidance", "CLIPTextEncodeFlux"} and "guidance" in inputs:
            values["flux_guidance"] = _plain(inputs.get("guidance"))

    multi_meta = actual.get("multi_ksampler") if isinstance(actual.get("multi_ksampler"), Mapping) else None
    if isinstance(multi_meta, Mapping) and multi_meta.get("enabled"):
        proof["multi_ksampler"] = {
            "schema": multi_meta.get("schema"),
            "stage_count": multi_meta.get("stage_count"),
            "stage_nodes": deepcopy(multi_meta.get("stage_nodes") or []),
            "transition_nodes": deepcopy(multi_meta.get("transition_nodes") or []),
            "terminal_sampler_node_id": multi_meta.get("terminal_sampler_node_id"),
            "integrity": deepcopy(multi_meta.get("integrity") or {}),
        }
    values["_workflow_proof"] = proof
    return values


def finalize_comfy_parameter_integrity(
    trace: Mapping[str, Any] | None,
    *,
    actual_params: Mapping[str, Any] | None,
    workflow: Mapping[str, Any] | None,
) -> dict[str, Any]:
    out = advance_parameter_integrity_trace(trace, "provider_actual", actual_params)
    workflow_values = extract_workflow_parameter_values(workflow, actual_params)
    out = advance_parameter_integrity_trace(out, "workflow_final", workflow_values)
    # advance() intentionally snapshots only fields, so restore graph proof separately.
    stages = out.setdefault("stages", {})
    stages.setdefault("workflow_final", {})["_workflow_proof"] = workflow_values.get("_workflow_proof", {})
    return recalculate_parameter_integrity(out)


def finalize_provider_parameter_integrity(
    trace: Mapping[str, Any] | None,
    *,
    actual_params: Mapping[str, Any] | None,
    provider_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    out = advance_parameter_integrity_trace(trace, "provider_actual", actual_params)
    if isinstance(provider_payload, Mapping):
        payload_snapshot = snapshot_parameter_values(provider_payload)
        # Translate Forge/API payload names to canonical fields where possible.
        if "cfg_scale" in provider_payload:
            payload_snapshot["cfg"] = _plain(provider_payload.get("cfg_scale"))
        if "sampler_name" in provider_payload:
            payload_snapshot["sampler"] = _plain(provider_payload.get("sampler_name"))
        if "denoising_strength" in provider_payload:
            payload_snapshot["denoise"] = _plain(provider_payload.get("denoising_strength"))
        if "n_iter" in provider_payload:
            payload_snapshot["batch_count"] = _plain(provider_payload.get("n_iter"))
        out = advance_parameter_integrity_trace(out, "workflow_final", payload_snapshot)
    return recalculate_parameter_integrity(out)


def concrete_mismatches(trace: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    payload = trace if isinstance(trace, Mapping) else {}
    return [dict(row) for row in payload.get("mismatches", []) if isinstance(row, Mapping)]


def mismatch_message(trace: Mapping[str, Any] | None) -> str:
    rows = concrete_mismatches(trace)
    if not rows:
        return ""
    parts = []
    for row in rows[:6]:
        parts.append(f"{row.get('field')}: {row.get('requested')} -> {row.get('observed')} ({row.get('from_stage')}→{row.get('to_stage')})")
    suffix = "" if len(rows) <= 6 else f"; +{len(rows) - 6} more"
    return "Parameter integrity mismatch before queue: " + "; ".join(parts) + suffix


__all__ = [
    "SCHEMA_VERSION",
    "SUBMISSION_SCHEMA_VERSION",
    "TRACKED_FIELDS",
    "snapshot_parameter_values",
    "start_parameter_integrity_trace",
    "advance_parameter_integrity_trace",
    "recalculate_parameter_integrity",
    "extract_workflow_parameter_values",
    "finalize_comfy_parameter_integrity",
    "finalize_provider_parameter_integrity",
    "concrete_mismatches",
    "mismatch_message",
]
