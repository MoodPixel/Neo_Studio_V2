from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.native_workflow_lora_contract.v1"
RUNTIME_SCHEMA: Final[str] = "neo.video.native_workflow_lora_runtime.v1"
MODEL_ONLY_LOADER: Final[str] = "LoraLoaderModelOnly"
ROUTE_POLICY: Final[dict[str, dict[str, Any]]] = {
    "wan22.native_workflow.txt2vid": {"roles": ["standard", "speed"], "targets": ["all"]},
    "wan22.native_workflow.img2vid": {"roles": ["standard", "speed"], "targets": ["all"]},
    "ltx23.native_workflow.txt2vid": {"roles": ["standard"], "targets": ["all"]},
    "ltx23.native_workflow.img2vid": {"roles": ["standard"], "targets": ["all"]},
}


def canonical_graph_digest(workflow: dict[str, Any]) -> str:
    raw = json.dumps(workflow, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def build_native_workflow_lora_contract(
    workflow: dict[str, Any], *, route_id: str, model_ref: list[Any], model_consumers: list[dict[str, str]],
    compiler: str = "native_workflow_importer",
) -> dict[str, Any]:
    policy = ROUTE_POLICY.get(route_id)
    if not policy:
        raise ValueError(f"Native-workflow Video LoRA has no exact-route policy for {route_id!r}.")
    return {
        "schema_version": SCHEMA_VERSION,
        "route_id": route_id,
        "owner": "compiler",
        "compiler": compiler,
        "graph_digest": canonical_graph_digest(workflow),
        "loader_type": "model_only",
        "loader_node_class": MODEL_ONLY_LOADER,
        "allowed_roles": list(policy["roles"]),
        "allowed_targets": list(policy["targets"]),
        "anchors": [{"target": "all", "model_ref": list(model_ref), "model_consumers": deepcopy(model_consumers)}],
    }


def _inputs(entry: Any) -> dict[str, Any]:
    raw = entry.get("input", {}) if isinstance(entry, dict) else {}
    merged: dict[str, Any] = {}
    if isinstance(raw, dict):
        for group_name in ("required", "optional"):
            group = raw.get(group_name, {})
            if isinstance(group, dict):
                merged.update(group)
    return merged


def _outputs(entry: Any) -> list[str]:
    raw = entry.get("output", []) if isinstance(entry, dict) else []
    return [str(item).upper() for item in raw] if isinstance(raw, (list, tuple)) else []


def _catalog(entry: Any) -> list[str]:
    spec = _inputs(entry).get("lora_name")
    return [str(item) for item in spec[0] if str(item)] if isinstance(spec, list) and spec and isinstance(spec[0], list) else []


def validate_native_workflow_lora_contract(
    workflow: dict[str, Any], contract: dict[str, Any], object_info: dict[str, Any],
) -> dict[str, Any]:
    route_id = str(contract.get("route_id") or "")
    policy = ROUTE_POLICY.get(route_id)
    errors: list[str] = []
    if contract.get("schema_version") != SCHEMA_VERSION:
        errors.append("unsupported native-workflow LoRA contract schema")
    if not policy:
        errors.append("route is not approved for the native-workflow LoRA contract")
    if contract.get("owner") != "compiler":
        errors.append("contract owner must be compiler")
    if contract.get("graph_digest") != canonical_graph_digest(workflow):
        errors.append("workflow graph digest does not match the compiler contract")
    if contract.get("loader_type") != "model_only" or contract.get("loader_node_class") != MODEL_ONLY_LOADER:
        errors.append("native workflow must declare the model-only loader contract")
    if policy and (contract.get("allowed_roles") != policy["roles"] or contract.get("allowed_targets") != policy["targets"]):
        errors.append("declared role/target capability exceeds exact-route policy")
    anchors = contract.get("anchors") if isinstance(contract.get("anchors"), list) else []
    if len(anchors) != 1 or anchors[0].get("target") != "all":
        errors.append("native workflow must expose exactly one all-target model anchor")
    else:
        anchor = anchors[0]
        model_ref = anchor.get("model_ref")
        if not isinstance(model_ref, list) or len(model_ref) != 2 or str(model_ref[0]) not in workflow:
            errors.append("contract model_ref does not resolve in the imported graph")
        consumers = anchor.get("model_consumers") if isinstance(anchor.get("model_consumers"), list) else []
        if not consumers:
            errors.append("contract exposes no model consumer")
        for consumer in consumers:
            node = workflow.get(str(consumer.get("node_id") or ""), {})
            inputs = node.get("inputs", {}) if isinstance(node, dict) else {}
            if inputs.get(str(consumer.get("input") or "")) != model_ref:
                errors.append("contract consumer binding does not match the imported graph")
                break
    undeclared = [str(node_id) for node_id, node in workflow.items() if isinstance(node, dict) and str(node.get("class_type") or "") in {MODEL_ONLY_LOADER, "LoraLoader"}]
    if undeclared:
        errors.append("imported graph already contains LoRA loader nodes; duplicate graph authority is forbidden")
    loader_entry = object_info.get(MODEL_ONLY_LOADER, {})
    required = {"model", "lora_name", "strength_model"}
    if not required.issubset(_inputs(loader_entry)) or "MODEL" not in _outputs(loader_entry):
        errors.append("live LoraLoaderModelOnly MODEL signature is unavailable or incompatible")
    if errors:
        raise ValueError("Native-workflow Video LoRA contract rejected: " + "; ".join(dict.fromkeys(errors)))
    return {"schema_version": SCHEMA_VERSION, "valid": True, "route_id": route_id, "catalog": _catalog(loader_entry), "policy": deepcopy(policy), "graph_digest": contract["graph_digest"]}


def apply_native_workflow_lora_stack(
    workflow: dict[str, Any], contract: dict[str, Any], rows: list[dict[str, Any]], object_info: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    proof = validate_native_workflow_lora_contract(workflow, contract, object_info)
    enabled = [deepcopy(row) for row in rows if isinstance(row, dict) and row.get("enabled", True)]
    roles = set(proof["policy"]["roles"])
    targets = set(proof["policy"]["targets"])
    if any(str(row.get("role") or "standard") not in roles for row in enabled):
        raise ValueError("Native-workflow Video LoRA row uses a role blocked by exact-route policy.")
    if any(str(row.get("target") or "all") not in targets for row in enabled):
        raise ValueError("Native-workflow Video LoRA row uses a target blocked by exact-route policy.")
    standard = [row for row in enabled if str(row.get("role") or "standard") != "speed"]
    speed = [row for row in enabled if str(row.get("role") or "standard") == "speed"]
    ordered = [*standard, *speed]
    catalog = {name.casefold() for name in proof["catalog"]}
    if ordered and not catalog:
        raise ValueError("Native-workflow LoraLoaderModelOnly exposes an empty live LoRA catalog.")
    missing = [str(row.get("name") or "") for row in ordered if str(row.get("name") or "").casefold() not in catalog]
    if missing:
        raise ValueError("Native-workflow selected LoRA is missing from the live catalog: " + ", ".join(missing))
    patched = deepcopy(workflow)
    anchor = contract["anchors"][0]
    current = list(anchor["model_ref"])
    numeric = [int(key) for key in patched if str(key).isdigit()]
    next_id = max(numeric, default=0) + 1
    applied: list[dict[str, Any]] = []
    for row in ordered:
        node_id = str(next_id); next_id += 1
        strength = float(row.get("strength_model", 1.0))
        patched[node_id] = {"class_type": MODEL_ONLY_LOADER, "inputs": {"model": current, "lora_name": str(row.get("name") or ""), "strength_model": strength}, "_meta": {"title": f"Video LoRA · Native Workflow · {row.get('role') or 'standard'}"}}
        current = [node_id, 0]
        applied.append({**row, "target": "all", "strength_model": strength, "node_id": node_id})
    for consumer in anchor["model_consumers"]:
        patched[str(consumer["node_id"])]["inputs"][str(consumer["input"])] = current
    runtime = {"schema_version": RUNTIME_SCHEMA, "phase": "phase_17", "active": bool(ordered), "route_id": proof["route_id"], "requested_count": len(ordered), "applied_count": len(applied), "standard_count": len(standard), "speed_count": len(speed), "applied": applied, "initial_model_ref": anchor["model_ref"], "final_model_ref": current, "contract_proof": proof, "gpu_inference_proven": False}
    return patched, runtime
