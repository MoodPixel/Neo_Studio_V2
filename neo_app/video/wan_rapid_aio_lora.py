from __future__ import annotations

from copy import deepcopy
from typing import Any, Final

SCHEMA_VERSION: Final[str] = "neo.video.wan22.rapid_aio_lora.v1"
ROUTES: Final[set[str]] = {
    "wan22.rapid_aio_gguf.txt2vid",
    "wan22.rapid_aio_gguf.img2vid",
}
MODEL_ONLY_LOADER: Final[str] = "LoraLoaderModelOnly"


def _actual_class(object_info: dict[str, Any], wanted: str) -> str:
    folded = {str(key).casefold(): str(key) for key in (object_info or {})}
    return folded.get(wanted.casefold(), "")


def _inputs(entry: Any) -> dict[str, Any]:
    raw = entry.get("input", {}) if isinstance(entry, dict) else {}
    merged: dict[str, Any] = {}
    if isinstance(raw, dict):
        for name in ("required", "optional"):
            group = raw.get(name, {})
            if isinstance(group, dict):
                merged.update(group)
    return merged


def _outputs(entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return []
    raw = entry.get("output") or entry.get("output_name") or []
    return [str(value).upper() for value in raw] if isinstance(raw, (list, tuple)) else []


def _catalog(object_info: dict[str, Any], loader_class: str) -> list[str]:
    spec = _inputs(object_info.get(loader_class, {})).get("lora_name")
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return [str(value) for value in spec[0] if str(value)]
    return []


def validate_rapid_aio_lora_topology(
    route_id: str,
    object_info: dict[str, Any],
    model_loader_class: str,
) -> dict[str, Any]:
    if route_id not in ROUTES:
        raise ValueError(f"WAN Rapid AIO LoRA is not enabled for route {route_id!r}.")
    model_entry = object_info.get(model_loader_class, {})
    if "MODEL" not in _outputs(model_entry):
        raise ValueError("WAN Rapid AIO GGUF loader does not expose a live MODEL output; LoRA injection is blocked.")
    lora_class = _actual_class(object_info, MODEL_ONLY_LOADER)
    if not lora_class:
        raise ValueError("WAN Rapid AIO LoRA requires LoraLoaderModelOnly; generic LoraLoader fallback is forbidden.")
    lora_inputs = _inputs(object_info.get(lora_class, {}))
    required = {"model", "lora_name", "strength_model"}
    missing = sorted(required - set(lora_inputs))
    if missing or ("MODEL" not in _outputs(object_info.get(lora_class, {}))):
        raise ValueError("WAN Rapid AIO LoraLoaderModelOnly has an incompatible live MODEL signature.")
    return {
        "schema_version": SCHEMA_VERSION,
        "route_id": route_id,
        "validated": True,
        "model_loader_class": model_loader_class,
        "model_loader_output": "MODEL",
        "lora_loader_class": lora_class,
        "lora_loader_type": "model_only",
        "allowed_targets": ["all"],
        "catalog": _catalog(object_info, lora_class),
        "generic_fallback": False,
    }


def apply_rapid_aio_lora_stack(
    prompt: dict[str, Any],
    model_ref: list[Any],
    rows: list[dict[str, Any]],
    *,
    route_id: str,
    object_info: dict[str, Any],
    model_loader_class: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    topology = validate_rapid_aio_lora_topology(route_id, object_info, model_loader_class)
    normalized = [deepcopy(row) for row in rows if isinstance(row, dict) and row.get("enabled", True)]
    if any(str(row.get("target") or "all") != "all" for row in normalized):
        raise ValueError("WAN Rapid AIO is a single-model route and accepts target='all' only.")
    standard = [row for row in normalized if str(row.get("role") or "standard") != "speed"]
    speed = [row for row in normalized if str(row.get("role") or "standard") == "speed"]
    ordered = [*standard, *speed]
    catalog = {name.casefold() for name in topology["catalog"]}
    if ordered and not catalog:
        raise ValueError("WAN Rapid AIO LoraLoaderModelOnly exposes no LoRA files in its live catalog.")
    missing = [str(row.get("name") or "") for row in ordered if str(row.get("name") or "").casefold() not in catalog]
    if missing:
        raise ValueError("Selected WAN Rapid AIO LoRA is not visible in the live catalog: " + ", ".join(missing))

    patched = deepcopy(prompt)
    current = list(model_ref)
    numeric = [int(key) for key in patched if str(key).isdigit()]
    next_id = max(numeric, default=0) + 1
    applied: list[dict[str, Any]] = []
    for row in ordered:
        node_id = str(next_id)
        next_id += 1
        strength = float(row.get("strength_model", 1.0))
        patched[node_id] = {
            "class_type": topology["lora_loader_class"],
            "inputs": {"model": current, "lora_name": str(row.get("name") or ""), "strength_model": strength},
            "_meta": {"title": f"Video LoRA · WAN Rapid AIO · {row.get('role') or 'standard'}"},
        }
        current = [node_id, 0]
        applied.append({**row, "strength_model": strength, "target": "all", "node_id": node_id})

    rewired: list[dict[str, str]] = []
    for node_id, node in patched.items():
        if str(node_id) in {str(item["node_id"]) for item in applied} or not isinstance(node, dict):
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        for input_name, value in list(inputs.items()):
            if value == model_ref:
                inputs[input_name] = current
                rewired.append({"node_id": str(node_id), "input": str(input_name)})
    if ordered and not rewired:
        raise ValueError("WAN Rapid AIO compiler exposed no consumer for its final MODEL link.")
    runtime = {
        "schema_version": SCHEMA_VERSION,
        "phase": "phase_16",
        "active": bool(ordered),
        "route_id": route_id,
        "requested_count": len(ordered),
        "applied_count": len(applied),
        "standard_count": len(standard),
        "speed_count": len(speed),
        "applied": applied,
        "initial_model_ref": list(model_ref),
        "final_model_ref": current,
        "model_consumers": rewired,
        "topology": topology,
        "warnings": [],
        "gpu_inference_proven": False,
    }
    return patched, runtime
