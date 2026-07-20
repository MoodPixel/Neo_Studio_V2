from __future__ import annotations

from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Mapping

from neo_app.image.qwen_stitch_contract import (
    extract_qwen_stitch_payload,
    image_stitch_route_support,
    normalize_qwen_stitch_payload,
)

from .qwen_stitch_route import (
    _stitch_inputs,
    image_stitch_node_name,
)


QWEN_MULTI_LANE_ROUTES = {
    ("qwen_rapid_aio", "checkpoint_aio"),
    ("qwen_rapid_aio", "gguf"),
}


def extract_image_stitch_payload(params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read the shared envelope while keeping the original Qwen key compatible."""

    values = params if isinstance(params, Mapping) else {}
    nested = values.get("image_stitch")
    if isinstance(nested, Mapping):
        return deepcopy(dict(nested))
    return extract_qwen_stitch_payload(values)


def image_stitch_has_ready_group(params_or_payload: Mapping[str, Any] | None) -> bool:
    """Return whether a complete Stitch Group can supply a source image."""

    values = params_or_payload if isinstance(params_or_payload, Mapping) else {}
    payload = extract_image_stitch_payload(values) if "groups" not in values else values
    if payload.get("enabled") is not True:
        return False
    for group in payload.get("groups") or []:
        if not isinstance(group, Mapping) or group.get("enabled", True) is False:
            continue
        inputs = group.get("inputs") if isinstance(group.get("inputs"), Mapping) else group
        if not isinstance(inputs, Mapping):
            continue
        a = inputs.get("image_a") or inputs.get("input_a") or inputs.get("a") or inputs.get("left")
        b = inputs.get("image_b") or inputs.get("input_b") or inputs.get("b") or inputs.get("right")
        if _ref_value(a) and _ref_value(b):
            return True
    return False


def image_stitch_is_qwen_lane_route(*, family: str, loader: str, mode: str) -> bool:
    """Qwen Rapid AIO can attach stitched outputs to optional image lanes."""

    return (str(family or "").lower(), str(loader or "").lower()) in QWEN_MULTI_LANE_ROUTES and str(mode or "").lower() in {"img2img", "edit"}


def _ref_value(value: Any) -> str:
    raw = value if isinstance(value, Mapping) else {"ref": value}
    for key in ("comfy_ref", "ref", "path", "filename", "file", "url", "name", "source_id"):
        candidate = str(raw.get(key) or "").strip()
        if candidate:
            return candidate
    return ""


def _group_source_refs(group: Mapping[str, Any]) -> tuple[str, str]:
    inputs = group.get("inputs") if isinstance(group.get("inputs"), Mapping) else group
    return (
        _ref_value(inputs.get("image_a") or inputs.get("input_a") or inputs.get("a") or inputs.get("left")),
        _ref_value(inputs.get("image_b") or inputs.get("input_b") or inputs.get("b") or inputs.get("right")),
    )


def _next_numeric_id(workflow: Mapping[str, Any]) -> int:
    numeric = [int(str(key)) for key in workflow if str(key).isdigit()]
    return max(numeric, default=0) + 1


def patch_source_loadimage_with_stitch(
    workflow: dict[str, Any],
    params: Mapping[str, Any] | None,
    *,
    family: str,
    loader: str,
    mode: str,
    backend_capabilities: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str], list[str]]:
    """Replace a compiled single-source LoadImage with an ImageStitch node.

    The normal compiler still builds its validated source/latent branch. This
    late graph patch keeps every family-specific VAE, mask, canvas, and latent
    contract intact while changing only the pixels entering Image 1.
    """

    payload = extract_image_stitch_payload(params)
    route = image_stitch_route_support(family=family, loader=loader, mode=mode)
    metadata: dict[str, Any] = {
        "schema": "neo.image.stitch.v2",
        "route": route,
        "source_mode": "composite_source",
        "patched": False,
        "groups": [],
    }
    warnings: list[str] = []
    errors: list[str] = []
    if not payload.get("enabled"):
        return workflow, metadata, warnings, errors
    if not route.get("supported"):
        errors.append(str(route.get("reason") or "Stitch Images is unavailable for this route."))
        return workflow, metadata, warnings, errors
    if image_stitch_is_qwen_lane_route(family=family, loader=loader, mode=mode):
        return workflow, metadata | {"source_mode": "qwen_image_lanes", "skipped": "compiled_by_qwen_lane_route"}, warnings, errors

    normalized, diagnostics = normalize_qwen_stitch_payload(
        payload,
        family=family,
        loader=loader,
        mode=mode,
        image_lane_limit=2,
    )
    errors.extend(str(item.get("message") or "Stitch Images validation failed.") for item in normalized["validation"]["errors"])
    warnings.extend(str(item.get("message") or "Stitch Images warning.") for item in normalized["validation"]["warnings"])
    groups = [group for group in normalized.get("groups", []) if group.get("enabled")]
    if len(groups) > 1:
        errors.append("This route uses one stitched composite source; keep only one enabled Stitch Group.")
    if errors or not groups:
        return workflow, metadata | {"validation": normalized.get("validation", {})}, warnings, errors

    group = groups[0]
    image_a, image_b = _group_source_refs(group)
    if not image_a or not image_b:
        errors.append("Stitch Group requires two uploaded images before queueing.")
        return workflow, metadata, warnings, errors

    node_name, availability_source = image_stitch_node_name(backend_capabilities)
    if not node_name:
        errors.append("ComfyUI ImageStitch is not available in the selected backend object_info catalog.")
        return workflow, metadata, warnings, errors

    placeholder = str((params or {}).get("comfy_source_image_name") or "").strip()
    candidates: list[str] = []
    if placeholder:
        candidates.append(PurePosixPath(placeholder.replace("\\", "/")).name)
    direct = str((params or {}).get("source_image_name") or (params or {}).get("source_image") or "").strip()
    if direct:
        candidates.append(PurePosixPath(direct.replace("\\", "/")).name)
    target_id = ""
    for node_id, node in workflow.items():
        if not isinstance(node, Mapping) or node.get("class_type") != "LoadImage":
            continue
        image_name = str((node.get("inputs") or {}).get("image") or "").replace("\\", "/")
        if not candidates or PurePosixPath(image_name).name in candidates:
            target_id = str(node_id)
            break
    if not target_id:
        errors.append("The compiled workflow did not expose a replaceable source LoadImage node.")
        return workflow, metadata, warnings, errors

    next_id = _next_numeric_id(workflow)
    load_a_id = str(next_id)
    workflow[load_a_id] = {"class_type": "LoadImage", "inputs": {"image": image_a, "upload": "image"}}
    load_b_id = str(next_id + 1)
    workflow[load_b_id] = {"class_type": "LoadImage", "inputs": {"image": image_b, "upload": "image"}}
    stitch_inputs = _stitch_inputs(
        group.get("settings") or {},
        load_a_id,
        load_b_id,
        backend_capabilities=backend_capabilities,
        node_name=node_name,
    )
    workflow[target_id] = {"class_type": node_name, "inputs": stitch_inputs}
    metadata.update({
        "patched": True,
        "node": node_name,
        "node_availability_source": availability_source,
        "target_source_node": target_id,
        "workflow_nodes": {"load_image_a": load_a_id, "load_image_b": load_b_id, "image_stitch": target_id},
        "raw_input_names": [group["inputs"]["image_a"]["name"], group["inputs"]["image_b"]["name"]],
        "settings": dict(group.get("settings") or {}),
        "validation": normalized.get("validation", {}),
    })
    warnings.append("Stitch Images replaced the route's Image 1 source with one composite image.")
    return workflow, metadata, warnings, errors
