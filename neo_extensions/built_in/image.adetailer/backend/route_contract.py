from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

CONTRACT_SCHEMA_ID = "neo.image.adetailer.route_contract.v1"
REQUIRED_REF_KEYS = ("image", "model", "clip", "vae", "positive", "negative")
DEFAULT_SAMPLER_INPUTS = {
    "model": "model",
    "positive": "positive",
    "negative": "negative",
    "latent": "latent_image",
    "seed": "seed",
    "steps": "steps",
    "cfg": "cfg",
    "sampler_name": "sampler_name",
    "scheduler": "scheduler",
    "denoise": "denoise",
}
VALID_MODEL_SAMPLING_STATES = {"passthrough", "patched", "provider_owned"}


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def clean_comfy_ref(value: Any) -> list[Any] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    node_id = _clean_text(value[0])
    if not node_id:
        return None
    output_index = value[1]
    if isinstance(output_index, str) and output_index.strip().isdigit():
        output_index = int(output_index.strip())
    if not isinstance(output_index, int) or output_index < 0:
        return None
    return [node_id, output_index]


def _normalized_mode(value: Any) -> str:
    text = _clean_text(value).lower().replace("-", "_").replace(" ", "_")
    return {
        "txt2img": "generate",
        "text2image": "generate",
        "text_to_image": "generate",
        "image_to_image": "img2img",
        "i2i": "img2img",
        "repair": "inpaint",
        "mask": "inpaint",
    }.get(text, text or "generate")


def _clean_route(route: Mapping[str, Any] | None) -> dict[str, str]:
    source = route if isinstance(route, Mapping) else {}
    return {
        "backend": _clean_text(source.get("backend") or source.get("provider_id") or "comfyui").lower(),
        "provider_id": _clean_text(source.get("provider_id") or source.get("backend") or "comfyui").lower(),
        "family": _clean_text(source.get("family") or source.get("model_family")).lower(),
        "loader": _clean_text(source.get("loader") or source.get("loader_type")).lower(),
        "workflow_mode": _normalized_mode(source.get("workflow_mode") or source.get("mode")),
        "engine": _clean_text(source.get("workflow_engine") or source.get("engine") or source.get("inpaint_engine") or "native").lower(),
        "compiler_id": _clean_text(source.get("compiler_id")),
    }


def build_adetailer_route_contract(
    *,
    route: Mapping[str, Any] | None,
    image_ref: Any,
    model_ref: Any,
    clip_ref: Any,
    vae_ref: Any,
    positive_ref: Any,
    negative_ref: Any,
    sampler_node_id: str | int,
    sampler_inputs: Mapping[str, Any] | None = None,
    model_sampling_ref: Any = None,
    model_sampling_state: str = "passthrough",
    model_sampling_nodes: list[str] | tuple[str, ...] | None = None,
    source: str = "compiler",
    compiler_id: str = "",
    validated: bool = True,
    notes: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """Build the compiler-owned ADetailer graph contract.

    The compiler that created the graph owns every reference. ADetailer may
    consume and rebase this contract, but it must never infer a different
    family graph from checkpoint node IDs.
    """
    clean_route = _clean_route(route)
    clean_compiler_id = _clean_text(compiler_id or clean_route.get("compiler_id"))
    refs = {
        "image": clean_comfy_ref(image_ref) or [],
        "model": clean_comfy_ref(model_ref) or [],
        "clip": clean_comfy_ref(clip_ref) or [],
        "vae": clean_comfy_ref(vae_ref) or [],
        "positive": clean_comfy_ref(positive_ref) or [],
        "negative": clean_comfy_ref(negative_ref) or [],
    }
    clean_sampler_inputs = dict(DEFAULT_SAMPLER_INPUTS)
    if isinstance(sampler_inputs, Mapping):
        for key in DEFAULT_SAMPLER_INPUTS:
            value = _clean_text(sampler_inputs.get(key))
            if value:
                clean_sampler_inputs[key] = value
    model_sampling_state = _clean_text(model_sampling_state).lower() or "passthrough"
    model_sampling_model_ref = clean_comfy_ref(model_sampling_ref) or deepcopy(refs["model"])
    route_key = ":".join((clean_route["provider_id"], clean_route["family"], clean_route["loader"], clean_route["workflow_mode"], clean_route["engine"]))
    return {
        "schema_id": CONTRACT_SCHEMA_ID,
        "schema_version": 1,
        "source": _clean_text(source) or "compiler",
        "compiler_id": clean_compiler_id,
        "route": clean_route,
        "route_key": route_key,
        "refs": refs,
        "sampler": {
            "node_id": _clean_text(sampler_node_id),
            "inputs": clean_sampler_inputs,
        },
        "model_sampling": {
            "state": model_sampling_state,
            "model_ref": model_sampling_model_ref or [],
            "source_node_ids": [_clean_text(item) for item in (model_sampling_nodes or []) if _clean_text(item)],
        },
        "ownership": {
            "authority": "provider_compiler",
            "fallback_policy": "none",
            "rebase_policy": "explicit_sampler_inputs_and_prior_extension_outputs_only",
        },
        "validated": bool(validated),
        "notes": [_clean_text(item) for item in (notes or []) if _clean_text(item)],
    }


def _graph_ref_error(workflow: Mapping[str, Any] | None, key: str, ref: list[Any]) -> str | None:
    if not isinstance(workflow, Mapping):
        return None
    node = workflow.get(str(ref[0]))
    if not isinstance(node, Mapping):
        return f"{key}_node_missing"
    return None


def normalize_adetailer_route_contract(
    contract: Mapping[str, Any] | None,
    *,
    route: Mapping[str, Any] | None = None,
    workflow: Mapping[str, Any] | None = None,
    require_validated: bool = True,
) -> dict[str, Any]:
    authoritative_route = _clean_route(route)
    if not isinstance(contract, Mapping) or not contract:
        return {
            "valid": False,
            "missing": True,
            "schema_id": CONTRACT_SCHEMA_ID,
            "errors": ["route_contract_missing"],
            "reason": "route_contract_missing",
            "contract": {},
            "route": authoritative_route,
        }

    embedded_route = _clean_route(contract.get("route") if isinstance(contract.get("route"), Mapping) else {})
    refs_source = contract.get("refs") if isinstance(contract.get("refs"), Mapping) else {}
    refs = {key: clean_comfy_ref(refs_source.get(key)) or [] for key in REQUIRED_REF_KEYS}
    sampler_source = contract.get("sampler") if isinstance(contract.get("sampler"), Mapping) else {}
    sampler_inputs = dict(DEFAULT_SAMPLER_INPUTS)
    raw_sampler_inputs = sampler_source.get("inputs") if isinstance(sampler_source.get("inputs"), Mapping) else {}
    for key in DEFAULT_SAMPLER_INPUTS:
        value = _clean_text(raw_sampler_inputs.get(key))
        if value:
            sampler_inputs[key] = value
    sampler_node_id = _clean_text(sampler_source.get("node_id"))
    model_sampling_source = contract.get("model_sampling") if isinstance(contract.get("model_sampling"), Mapping) else {}
    model_sampling_state = _clean_text(model_sampling_source.get("state")).lower()
    model_sampling_ref = clean_comfy_ref(model_sampling_source.get("model_ref")) or []
    model_sampling_nodes = [_clean_text(item) for item in model_sampling_source.get("source_node_ids", []) if _clean_text(item)] if isinstance(model_sampling_source.get("source_node_ids"), list) else []

    normalized = {
        "schema_id": CONTRACT_SCHEMA_ID,
        "schema_version": 1,
        "source_schema_id": _clean_text(contract.get("schema_id")),
        "source": _clean_text(contract.get("source")) or "compiler",
        "compiler_id": _clean_text(contract.get("compiler_id") or embedded_route.get("compiler_id")),
        "route": authoritative_route or embedded_route,
        "route_key": ":".join((authoritative_route["provider_id"], authoritative_route["family"], authoritative_route["loader"], authoritative_route["workflow_mode"], authoritative_route["engine"])),
        "refs": refs,
        "sampler": {"node_id": sampler_node_id, "inputs": sampler_inputs},
        "model_sampling": {
            "state": model_sampling_state,
            "model_ref": model_sampling_ref,
            "source_node_ids": model_sampling_nodes,
        },
        "ownership": {
            "authority": "provider_compiler",
            "fallback_policy": "none",
            "rebase_policy": "explicit_sampler_inputs_and_prior_extension_outputs_only",
        },
        "validated": bool(contract.get("validated", False)),
        "notes": [_clean_text(item) for item in contract.get("notes", []) if _clean_text(item)] if isinstance(contract.get("notes"), list) else [],
    }

    errors: list[str] = []
    if normalized["source_schema_id"] != CONTRACT_SCHEMA_ID:
        errors.append("route_contract_schema_invalid")
    if not normalized["source"]:
        errors.append("route_contract_source_missing")
    if not normalized["compiler_id"]:
        errors.append("route_contract_compiler_id_missing")
    if require_validated and not normalized["validated"]:
        errors.append("route_contract_not_compiler_validated")
    for key, ref in refs.items():
        if not ref:
            errors.append(f"{key}_ref_missing")
        else:
            graph_error = _graph_ref_error(workflow, key, ref)
            if graph_error:
                errors.append(graph_error)
    if not sampler_node_id:
        errors.append("sampler_node_id_missing")
    elif isinstance(workflow, Mapping):
        sampler_node = workflow.get(sampler_node_id)
        if not isinstance(sampler_node, Mapping):
            errors.append("sampler_node_missing")
        else:
            sampler_graph_inputs = sampler_node.get("inputs") if isinstance(sampler_node.get("inputs"), Mapping) else {}
            for role in ("model", "positive", "negative"):
                input_name = sampler_inputs[role]
                live_ref = clean_comfy_ref(sampler_graph_inputs.get(input_name))
                if not live_ref:
                    errors.append(f"sampler_{role}_input_missing")
                elif refs[role] and live_ref != refs[role]:
                    errors.append(f"sampler_{role}_ref_mismatch")
    if model_sampling_state not in VALID_MODEL_SAMPLING_STATES:
        errors.append("model_sampling_state_invalid")
    if not model_sampling_ref:
        errors.append("model_sampling_ref_missing")
    else:
        graph_error = _graph_ref_error(workflow, "model_sampling", model_sampling_ref)
        if graph_error:
            errors.append(graph_error)
    if model_sampling_state == "passthrough" and refs["model"] and model_sampling_ref and model_sampling_ref != refs["model"]:
        errors.append("model_sampling_passthrough_ref_mismatch")
    if model_sampling_state == "patched" and not model_sampling_nodes:
        errors.append("model_sampling_source_nodes_missing")
    if isinstance(workflow, Mapping):
        for node_id in model_sampling_nodes:
            if not isinstance(workflow.get(node_id), Mapping):
                errors.append("model_sampling_source_node_missing")
                break

    for field in ("provider_id", "family", "loader", "workflow_mode", "engine"):
        expected = authoritative_route.get(field)
        embedded = embedded_route.get(field)
        if expected and embedded and expected != embedded:
            errors.append(f"route_{field}_mismatch")

    deduped_errors = list(dict.fromkeys(errors))
    return {
        "valid": not deduped_errors,
        "missing": False,
        "schema_id": CONTRACT_SCHEMA_ID,
        "errors": deduped_errors,
        "reason": ",".join(deduped_errors) if deduped_errors else "ok",
        "contract": normalized,
        "route": authoritative_route,
    }


def rebase_adetailer_route_contract(
    contract: Mapping[str, Any] | None,
    *,
    workflow: Mapping[str, Any],
    model_ref: Any = None,
    clip_ref: Any = None,
    image_ref: Any = None,
    sampler_node_id: str | int | None = None,
    refresh_sampler_refs: bool = True,
    lineage_reason: str = "prior_extension_outputs",
) -> dict[str, Any]:
    """Rebase a compiler-owned contract using only declared live graph anchors."""
    rebased = deepcopy(dict(contract)) if isinstance(contract, Mapping) else {}
    refs = rebased.get("refs") if isinstance(rebased.get("refs"), dict) else {}
    rebased["refs"] = refs
    for key, value in (("model", model_ref), ("clip", clip_ref), ("image", image_ref)):
        clean = clean_comfy_ref(value)
        if clean:
            refs[key] = clean
    sampler = rebased.get("sampler") if isinstance(rebased.get("sampler"), dict) else {}
    rebased["sampler"] = sampler
    if sampler_node_id is not None and _clean_text(sampler_node_id):
        sampler["node_id"] = _clean_text(sampler_node_id)
    sampler_inputs = dict(DEFAULT_SAMPLER_INPUTS)
    if isinstance(sampler.get("inputs"), Mapping):
        for key in DEFAULT_SAMPLER_INPUTS:
            value = _clean_text(sampler["inputs"].get(key))
            if value:
                sampler_inputs[key] = value
    sampler["inputs"] = sampler_inputs
    if refresh_sampler_refs:
        sampler_node = workflow.get(_clean_text(sampler.get("node_id"))) if isinstance(workflow, Mapping) else None
        inputs = sampler_node.get("inputs") if isinstance(sampler_node, Mapping) and isinstance(sampler_node.get("inputs"), Mapping) else {}
        for role in ("model", "positive", "negative"):
            clean = clean_comfy_ref(inputs.get(sampler_inputs[role]))
            if clean:
                refs[role] = clean
    model_sampling = rebased.get("model_sampling") if isinstance(rebased.get("model_sampling"), dict) else {}
    rebased["model_sampling"] = model_sampling
    if clean_comfy_ref(refs.get("model")):
        model_sampling["model_ref"] = deepcopy(refs["model"])
    if _clean_text(model_sampling.get("state")) not in VALID_MODEL_SAMPLING_STATES:
        model_sampling["state"] = "passthrough"
    lineage = rebased.get("rebase_lineage") if isinstance(rebased.get("rebase_lineage"), list) else []
    lineage.append({
        "reason": _clean_text(lineage_reason) or "prior_extension_outputs",
        "model_ref": deepcopy(refs.get("model") or []),
        "clip_ref": deepcopy(refs.get("clip") or []),
        "image_ref": deepcopy(refs.get("image") or []),
        "sampler_node_id": _clean_text(sampler.get("node_id")),
    })
    rebased["rebase_lineage"] = lineage[-16:]
    return rebased


def contract_metadata(result: Mapping[str, Any] | None) -> dict[str, Any]:
    source = result if isinstance(result, Mapping) else {}
    contract = source.get("contract") if isinstance(source.get("contract"), Mapping) else {}
    return {
        "schema_id": CONTRACT_SCHEMA_ID,
        "valid": bool(source.get("valid")),
        "missing": bool(source.get("missing")),
        "reason": _clean_text(source.get("reason")),
        "errors": list(source.get("errors") or []),
        "source": _clean_text(contract.get("source")),
        "compiler_id": _clean_text(contract.get("compiler_id")),
        "route_key": _clean_text(contract.get("route_key")),
        "refs": deepcopy(contract.get("refs") or {}),
        "sampler": deepcopy(contract.get("sampler") or {}),
        "model_sampling": deepcopy(contract.get("model_sampling") or {}),
        "fallback_policy": "none",
    }
