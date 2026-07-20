from __future__ import annotations

from copy import deepcopy
from typing import Any

from .asset_resolver import resolve_controlnet_task_assets
from .node_discovery import inspect_nodes, preprocessor_status
from .payload_schema import EXTENSION_ID, normalize_block
from .support_matrix import (
    ACTIVE_STATES,
    TASK_INPAINT_CONTROL,
    TASK_MAP_CONTROL,
    TASK_OUTPAINT_CONTROL,
    controlnet_task_state,
    normalize_controlnet_task,
    task_route_reason,
    route_reason,
    route_state,
    route_profile_for_route,
)

PHASE = "P9.2"


def _next_graph_id(workflow: dict[str, Any], preferred: int | str | None = None) -> str:
    if preferred is not None:
        candidate = str(preferred)
        if candidate not in workflow:
            return candidate
    numeric_ids: list[int] = []
    for key in workflow:
        try:
            numeric_ids.append(int(str(key)))
        except (TypeError, ValueError):
            continue
    return str((max(numeric_ids) if numeric_ids else 0) + 1)


def _copy_ref(ref: Any, fallback: list[Any]) -> list[Any]:
    if isinstance(ref, (list, tuple)) and len(ref) >= 2:
        index = ref[1]
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        return [str(ref[0]), index]
    return deepcopy(fallback)


def _route_with_state(route: dict[str, Any] | None) -> dict[str, Any]:
    data = deepcopy(route or {})
    backend = str(data.get("backend") or "comfyui")
    family = str(data.get("family") or "sdxl")
    loader = str(data.get("loader") or "checkpoint")
    mode = str(data.get("workflow_mode") or data.get("mode") or "generate")
    if mode == "txt2img":
        mode = "generate"
    state = str(data.get("route_state") or route_state(backend, family, loader, mode))
    task = normalize_controlnet_task(str(data.get("controlnet_task") or TASK_MAP_CONTROL), workflow_mode=mode)
    profile = route_profile_for_route(backend, family, loader, mode, task)
    return {**data, "backend": backend, "family": family, "loader": loader, "workflow_mode": mode, "route_state": state, "base_route_state": data.get("base_route_state") or state, "route_profile": profile, "route_profile_id": profile.get("profile_id"), "map_adapter": profile.get("map_adapter"), "inpaint_adapter": profile.get("inpaint_adapter"), "outpaint_adapter": profile.get("outpaint_adapter")}


def _controlnet_payload_block(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Extract the ControlNet block from every payload envelope V2 can pass.

    The shared workflow hook passes the whole extension registry/envelope into
    each extension patcher, for example ``{"payloads": {"image.controlnet":
    {...}}}``.  V1 compatibility can also pass legacy top-level
    ``controlnet_*`` fields.  The patcher must normalize the actual block, not
    the outer envelope, otherwise the sanitizer sees no ``enabled`` flag and
    silently disables ControlNet.
    """
    if not isinstance(payload, dict):
        return {}
    if isinstance(payload.get(EXTENSION_ID), dict):
        return deepcopy(payload.get(EXTENSION_ID) or {})
    payloads = payload.get("payloads")
    if isinstance(payloads, dict) and isinstance(payloads.get(EXTENSION_ID), dict):
        return deepcopy(payloads.get(EXTENSION_ID) or {})
    nested = payload.get("extensions")
    if isinstance(nested, dict) and isinstance(nested.get(EXTENSION_ID), dict):
        return deepcopy(nested.get(EXTENSION_ID) or {})
    legacy_keys = {
        "controlnet_units",
        "controlnet_stack_enabled",
        "controlnet_stack_count",
        "controlnet_name",
        "controlnet_preprocessor",
        "controlnet_strength",
        "control_image_name",
    }
    if legacy_keys.intersection(payload):
        return deepcopy(payload)
    return deepcopy(payload)


def _extension_block_from_payload(payload: dict[str, Any] | None, route: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    raw_block = _controlnet_payload_block(payload or {})
    raw_params = raw_block.get("params") if isinstance(raw_block.get("params"), dict) else {}
    raw_task = normalize_controlnet_task(str(raw_params.get("controlnet_task") or TASK_MAP_CONTROL), workflow_mode=route.get("workflow_mode"))
    task_state = controlnet_task_state(route.get("backend"), route.get("family"), route.get("loader"), route.get("workflow_mode"), raw_task)
    # Phase O: base map-control routes still enforce the route matrix directly.
    # Inpaint/outpaint tasks enforce their task-specific state so SD checkpoint
    # outpaint can run even though standard map-control outpaint remains gated.
    effective_route = {
        **route,
        "controlnet_task": raw_task,
        "controlnet_task_state": task_state,
        "route_state": task_state if raw_task != TASK_MAP_CONTROL else route.get("route_state"),
    }
    profile = route_profile_for_route(effective_route.get("backend"), effective_route.get("family"), effective_route.get("loader"), effective_route.get("workflow_mode"), raw_task)
    effective_route.update({"route_profile": profile, "route_profile_id": profile.get("profile_id"), "map_adapter": profile.get("map_adapter"), "inpaint_adapter": profile.get("inpaint_adapter"), "outpaint_adapter": profile.get("outpaint_adapter")})
    route.update(effective_route)
    block, notes = normalize_block(raw_block, route=effective_route, enforce_route_state=True)
    return block, [dict(note) for note in notes]


def _asset_bucket(assets: dict[str, Any], key: str) -> dict[str, Any]:
    value = assets.get(key)
    return value if isinstance(value, dict) else {}


def _asset_for_unit(assets: dict[str, Any], uid: str) -> Any:
    # Prefer generated maps because Phase F's map API stores preprocessed outputs there.
    for bucket_name in ("generated_maps", "control_images"):
        bucket = _asset_bucket(assets, bucket_name)
        if uid in bucket:
            return bucket.get(uid)
        if "default" in bucket:
            return bucket.get("default")
        if "primary" in bucket:
            return bucket.get("primary")
    return None


def _asset_to_image_name(asset: Any) -> str:
    if isinstance(asset, str):
        return asset.strip()
    if isinstance(asset, dict):
        for key in ("comfy_image_name", "image_name", "workflow_source", "filename", "name", "path", "url", "ref", "map_id", "asset_id"):
            value = asset.get(key)
            if value:
                return str(value).strip()
    return ""


def _find_first_node_by_class(workflow: dict[str, Any], class_type: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return str(node_id), node
    return None, None


def _load_image_ref_for_name(workflow: dict[str, Any], image_name: str) -> list[Any] | None:
    wanted = str(image_name or "").strip()
    if not wanted:
        return None
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") != "LoadImage":
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if str(inputs.get("image") or "").strip() == wanted:
            return [str(node_id), 0]
    return None


def _graph_image_ref_for_sd_task(graph: dict[str, Any], controlnet_task: str, asset_resolution: dict[str, Any]) -> tuple[list[Any] | None, str]:
    assets = asset_resolution.get("assets") if isinstance(asset_resolution.get("assets"), dict) else {}
    if controlnet_task == TASK_INPAINT_CONTROL:
        # Checkpoint inpaint graphs already load the Image Tab source at node 4.
        # The mask stays in the base inpaint latent branch; ControlNet receives
        # the source image as the inpaint ControlNet condition.
        source_image = str(assets.get("source_image") or "").strip()
        existing = _load_image_ref_for_name(graph, source_image) if source_image else None
        if existing:
            return existing, "existing_source_load_image"
        node = graph.get("4") if isinstance(graph.get("4"), dict) else {}
        if node.get("class_type") == "LoadImage":
            return ["4", 0], "checkpoint_source_node_4"
        return None, "missing_checkpoint_source_image_node"
    if controlnet_task == TASK_OUTPAINT_CONTROL:
        # Checkpoint outpaint graphs build a padded canvas with
        # ImagePadForOutpaint. Feed that canvas to the outpaint ControlNet model
        # so the condition image matches the sampler latent canvas.
        pad_id, _ = _find_first_node_by_class(graph, "ImagePadForOutpaint")
        if pad_id:
            return [str(pad_id), 0], "image_pad_for_outpaint_canvas"
        canvas_image = str(assets.get("canvas_image") or "").strip()
        existing = _load_image_ref_for_name(graph, canvas_image) if canvas_image else None
        if existing:
            return existing, "explicit_padded_canvas_load_image"
        return None, "missing_outpaint_padded_canvas_node"
    return None, "unsupported_controlnet_task"



def _is_flux1_controlnet_route(route_data: dict[str, Any] | None) -> bool:
    route_data = route_data if isinstance(route_data, dict) else {}
    return str(route_data.get("family") or "") == "flux" and str(route_data.get("loader") or "") in {"diffusion_model", "gguf"}


def _is_flux2_klein_controlnet_route(route_data: dict[str, Any] | None) -> bool:
    route_data = route_data if isinstance(route_data, dict) else {}
    return str(route_data.get("family") or "") == "flux2_klein" and str(route_data.get("loader") or "") in {"diffusion_model", "gguf"}


def _is_flux_family_controlnet_route(route_data: dict[str, Any] | None) -> bool:
    return _is_flux1_controlnet_route(route_data) or _is_flux2_klein_controlnet_route(route_data)


def _flux_route_active(route_data: dict[str, Any], controlnet_task: str) -> bool:
    mode = str(route_data.get("workflow_mode") or "")
    return _is_flux_family_controlnet_route(route_data) and (
        (controlnet_task == TASK_INPAINT_CONTROL and mode == "inpaint")
        or (controlnet_task == TASK_OUTPAINT_CONTROL and mode == "outpaint")
    )


def _route_profiled_node_status(status: dict[str, Any], route_data: dict[str, Any], controlnet_task: str) -> dict[str, Any]:
    """Return a node-status view that matches the route adapter.

    P9.2: Qwen map control may expose Qwen/InstantX loader/apply nodes
    instead of SD-style ControlNetLoader. Flux.1 may expose Flux-specific
    loader/apply nodes. The generic map patcher can still chain conditioning
    when the route profile supplies compatible loader/apply schemas.
    """
    if _is_qwen_controlnet_route(route_data) and controlnet_task == TASK_MAP_CONTROL:
        qwen_status = status.get("qwen") if isinstance(status.get("qwen"), dict) else {}
        if not qwen_status.get("instantx_available") and status.get("base_available"):
            return status
        if not qwen_status.get("instantx_available") and status.get("object_info_present"):
            return status
        patched = deepcopy(status)
        loader_node = qwen_status.get("instantx_loader_node") or status.get("loader_node") or "ControlNetLoader"
        apply_node = qwen_status.get("instantx_apply_node") or status.get("apply_node") or "ControlNetApplyAdvanced"
        patched["loader_node"] = loader_node
        patched["apply_node"] = apply_node
        patched["base_available"] = True
        patched["loader_available"] = True
        patched["apply_available"] = True
        patched["provider_gated"] = False
        patched["missing"] = []
        schemas = deepcopy(patched.get("input_schemas") if isinstance(patched.get("input_schemas"), dict) else {})
        if isinstance(schemas.get("qwen_instantx_loader"), dict) and schemas.get("qwen_instantx_loader"):
            schemas["loader"] = deepcopy(schemas["qwen_instantx_loader"])
        if isinstance(schemas.get("qwen_instantx_apply"), dict) and schemas.get("qwen_instantx_apply"):
            schemas["apply"] = deepcopy(schemas["qwen_instantx_apply"])
        patched["input_schemas"] = schemas
        patched["route_adapter"] = "qwen_map_control"
        patched["route_profile_id"] = route_data.get("route_profile_id")
        return patched
    if not _is_flux_family_controlnet_route(route_data):
        return status
    adapter_key = "flux2_klein" if _is_flux2_klein_controlnet_route(route_data) else "flux"
    flux_status = status.get(adapter_key) if isinstance(status.get(adapter_key), dict) else {}
    if not flux_status.get("available") and status.get("object_info_present"):
        return status
    patched = deepcopy(status)
    loader_node = flux_status.get("loader_node") or status.get("loader_node") or "ControlNetLoader"
    apply_node = flux_status.get("apply_node") or status.get("apply_node") or "ControlNetApplyAdvanced"
    patched["loader_node"] = loader_node
    patched["apply_node"] = apply_node
    patched["base_available"] = True
    patched["loader_available"] = True
    patched["apply_available"] = True
    patched["provider_gated"] = False
    patched["missing"] = []
    schemas = deepcopy(patched.get("input_schemas") if isinstance(patched.get("input_schemas"), dict) else {})
    loader_schema_key = "flux2_klein_loader" if adapter_key == "flux2_klein" else "flux_loader"
    apply_schema_key = "flux2_klein_apply" if adapter_key == "flux2_klein" else "flux_apply"
    if isinstance(schemas.get(loader_schema_key), dict) and schemas.get(loader_schema_key):
        schemas["loader"] = deepcopy(schemas[loader_schema_key])
    if isinstance(schemas.get(apply_schema_key), dict) and schemas.get(apply_schema_key):
        schemas["apply"] = deepcopy(schemas[apply_schema_key])
    patched["input_schemas"] = schemas
    patched["route_adapter"] = "flux2_klein_fun_union_controlnet" if adapter_key == "flux2_klein" else "flux1_controlnet"
    patched["route_profile_id"] = route_data.get("route_profile_id")
    return patched


def _route_params(route_data: dict[str, Any] | None) -> dict[str, Any]:
    route_data = route_data if isinstance(route_data, dict) else {}
    params: dict[str, Any] = {}
    for key in ("params", "actual_params"):
        value = route_data.get(key)
        if isinstance(value, dict):
            params.update(value)
    return params


def _is_flux2_klein_route(route_data: dict[str, Any] | None) -> bool:
    route_data = route_data if isinstance(route_data, dict) else {}
    if str(route_data.get("family") or "") == "flux2_klein":
        return True
    params = _route_params(route_data)
    variant = str(params.get("flux_variant") or params.get("variant") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if variant in {"klein", "flux2_klein", "flux_2_klein", "klein_4b", "klein_9b", "klein_4b_distilled", "klein_9b_distilled"}:
        return True
    if params.get("_neo_effective_flux2_klein_gguf_route") is True:
        return True
    if isinstance(params.get("flux2_klein_gguf_profile"), dict):
        return True
    for key in ("gguf_unet", "gguf_model", "model", "model_name"):
        text = str(params.get(key) or route_data.get(key) if isinstance(route_data, dict) else "").strip().lower().replace("_", "-")
        if "klein" in text and ("flux-2" in text or "flux2" in text or text.startswith("klein")):
            return True
    return False


def _flux_requested_adapter(block: dict[str, Any], asset_resolution: dict[str, Any] | None, status: dict[str, Any] | None = None, route_data: dict[str, Any] | None = None) -> str:
    params = (block.get("params") or {}) if isinstance(block.get("params"), dict) else {}
    assets = (asset_resolution or {}).get("assets") if isinstance((asset_resolution or {}).get("assets"), dict) else {}
    raw = str(
        params.get("flux_controlnet_adapter")
        or params.get("controlnet_flux_adapter")
        or params.get("flux_cn_adapter")
        or params.get("flux_klein_controlnet_adapter")
        or assets.get("flux_controlnet_adapter")
        or (asset_resolution or {}).get("flux_controlnet_adapter")
        or "auto"
    ).strip().lower().replace("-", "_")
    if raw in {"fun_union", "flux2_fun_union", "flux_2_fun_union", "flux2", "klein", "klein_fun", "flux2_klein"}:
        return "fun_union"
    if raw in {"alimama", "flux_inpaint", "flux_controlnet_inpaint", "inpaint", "controlnet"}:
        return "alimama"
    if _is_flux2_klein_route(route_data):
        return "fun_union"
    return "alimama"


def _is_qwen_controlnet_route(route_data: dict[str, Any] | None) -> bool:
    route_data = route_data if isinstance(route_data, dict) else {}
    family = str(route_data.get("family") or "")
    loader = str(route_data.get("loader") or "")
    return (
        (family in {"qwen_image", "qwen_image_edit_2509"} and loader in {"diffusion_model", "gguf"})
        or (family == "qwen_rapid_aio" and loader in {"checkpoint_aio", "gguf"})
    )


def _qwen_route_active(route_data: dict[str, Any], controlnet_task: str) -> bool:
    mode = str(route_data.get("workflow_mode") or "")
    return _is_qwen_controlnet_route(route_data) and (
        (controlnet_task == TASK_INPAINT_CONTROL and mode == "inpaint")
        or (controlnet_task == TASK_OUTPAINT_CONTROL and mode == "outpaint")
    )


def _qwen_requested_adapter(block: dict[str, Any], asset_resolution: dict[str, Any] | None, status: dict[str, Any] | None = None) -> str:
    params = (block.get("params") or {}) if isinstance(block.get("params"), dict) else {}
    assets = (asset_resolution or {}).get("assets") if isinstance((asset_resolution or {}).get("assets"), dict) else {}
    raw = str(
        params.get("qwen_controlnet_adapter")
        or params.get("controlnet_qwen_adapter")
        or params.get("qwen_cn_adapter")
        or assets.get("qwen_controlnet_adapter")
        or (asset_resolution or {}).get("qwen_controlnet_adapter")
        or "auto"
    ).strip().lower()
    if raw in {"instantx", "instant_x", "native_controlnet", "controlnet"}:
        return "instantx"
    if raw in {"diffsynth", "diff_synth", "model_patch", "model-patch", "patch"}:
        return "diffsynth"
    qwen_status = (status or {}).get("qwen") if isinstance((status or {}).get("qwen"), dict) else {}
    if qwen_status.get("diffsynth_available") or not (status or {}).get("object_info_present"):
        return "diffsynth"
    if qwen_status.get("instantx_available"):
        return "instantx"
    return "diffsynth"


def _node_schema(status: dict[str, Any], key: str) -> dict[str, Any]:
    schemas = status.get("input_schemas") if isinstance(status.get("input_schemas"), dict) else {}
    schema = schemas.get(key) if isinstance(schemas.get(key), dict) else {}
    return schema


def _schema_all_inputs(schema: dict[str, Any] | None) -> set[str]:
    if not isinstance(schema, dict):
        return set()
    names = set(schema.get("required_inputs") or []) | set(schema.get("optional_inputs") or []) | set(schema.get("hidden_inputs") or [])
    return {str(name) for name in names}


def _choose_input_name(schema: dict[str, Any] | None, candidates: tuple[str, ...], fallback: str) -> str:
    names = _schema_all_inputs(schema)
    if not names:
        return fallback
    for candidate in candidates:
        if candidate in names:
            return candidate
    return fallback


def _add_supported_input(inputs: dict[str, Any], schema: dict[str, Any] | None, name: str, value: Any) -> None:
    if _input_supports(schema, name):
        inputs[name] = deepcopy(value)


def _graph_mask_ref_for_flux_task(graph: dict[str, Any], controlnet_task: str, asset_resolution: dict[str, Any]) -> tuple[list[Any] | None, str]:
    assets = asset_resolution.get("assets") if isinstance(asset_resolution.get("assets"), dict) else {}
    if controlnet_task == TASK_OUTPAINT_CONTROL:
        pad_id, _ = _find_first_node_by_class(graph, "ImagePadForOutpaint")
        if pad_id:
            return [str(pad_id), 1], "image_pad_for_outpaint_mask"
    mask_image = str(assets.get("mask_image") or "").strip()
    if mask_image:
        for node_id, node in graph.items():
            if not isinstance(node, dict) or node.get("class_type") not in {"LoadImageMask", "LoadImage"}:
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if str(inputs.get("image") or "").strip() == mask_image:
                return [str(node_id), 0], "existing_flux_mask_node"
    mask_id, _ = _find_first_node_by_class(graph, "LoadImageMask")
    if mask_id:
        return [str(mask_id), 0], "first_flux_load_image_mask"
    return None, "missing_flux_mask_node"


def _graph_image_ref_for_flux_task(graph: dict[str, Any], controlnet_task: str, asset_resolution: dict[str, Any]) -> tuple[list[Any] | None, str]:
    assets = asset_resolution.get("assets") if isinstance(asset_resolution.get("assets"), dict) else {}
    if controlnet_task == TASK_OUTPAINT_CONTROL:
        pad_id, _ = _find_first_node_by_class(graph, "ImagePadForOutpaint")
        if pad_id:
            return [str(pad_id), 0], "image_pad_for_outpaint_canvas"
        canvas_image = str(assets.get("canvas_image") or "").strip()
        existing = _load_image_ref_for_name(graph, canvas_image) if canvas_image else None
        if existing:
            return existing, "explicit_flux_padded_canvas_load_image"
    source_image = str(assets.get("source_image") or "").strip()
    existing = _load_image_ref_for_name(graph, source_image) if source_image else None
    if existing:
        return existing, "existing_flux_source_load_image"
    first_load_id, _ = _find_first_node_by_class(graph, "LoadImage")
    if first_load_id:
        return [str(first_load_id), 0], "first_flux_load_image"
    return None, "missing_flux_source_image_node"


def _flux_unit_model(unit: dict[str, Any], *, adapter: str = "alimama") -> str:
    model = str(unit.get("model") or "").strip()
    if model:
        return model
    if adapter == "fun_union":
        return "FLUX.2-dev-Fun-Controlnet-Union-2602.safetensors"
    return "flux1-dev-controlnet-inpainting-beta.safetensors"


def _graph_mask_ref_for_qwen_task(graph: dict[str, Any], controlnet_task: str, asset_resolution: dict[str, Any]) -> tuple[list[Any] | None, str]:
    assets = asset_resolution.get("assets") if isinstance(asset_resolution.get("assets"), dict) else {}
    if controlnet_task == TASK_OUTPAINT_CONTROL:
        pad_id, _ = _find_first_node_by_class(graph, "ImagePadForOutpaint")
        if pad_id:
            return [str(pad_id), 1], "image_pad_for_outpaint_mask"
    mask_image = str(assets.get("mask_image") or "").strip()
    if mask_image:
        for node_id, node in graph.items():
            if not isinstance(node, dict) or node.get("class_type") not in {"LoadImageMask", "LoadImage"}:
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            if str(inputs.get("image") or "").strip() == mask_image:
                return [str(node_id), 0], "existing_mask_node"
    mask_id, _ = _find_first_node_by_class(graph, "LoadImageMask")
    if mask_id:
        return [str(mask_id), 0], "first_load_image_mask"
    return None, "missing_qwen_mask_node"


def _graph_image_ref_for_qwen_task(graph: dict[str, Any], controlnet_task: str, asset_resolution: dict[str, Any]) -> tuple[list[Any] | None, str]:
    assets = asset_resolution.get("assets") if isinstance(asset_resolution.get("assets"), dict) else {}
    if controlnet_task == TASK_OUTPAINT_CONTROL:
        pad_id, _ = _find_first_node_by_class(graph, "ImagePadForOutpaint")
        if pad_id:
            return [str(pad_id), 0], "image_pad_for_outpaint_canvas"
        canvas_image = str(assets.get("canvas_image") or "").strip()
        existing = _load_image_ref_for_name(graph, canvas_image) if canvas_image else None
        if existing:
            return existing, "explicit_qwen_padded_canvas_load_image"
    source_image = str(assets.get("source_image") or "").strip()
    existing = _load_image_ref_for_name(graph, source_image) if source_image else None
    if existing:
        return existing, "existing_qwen_source_load_image"
    first_load_id, _ = _find_first_node_by_class(graph, "LoadImage")
    if first_load_id:
        return [str(first_load_id), 0], "first_qwen_load_image"
    return None, "missing_qwen_source_image_node"


def _qwen_unit_model(unit: dict[str, Any], *, adapter: str) -> str:
    model = str(unit.get("model") or "").strip()
    if model:
        return model
    return "qwen_image_inpaint_diffsynth_controlnet.safetensors" if adapter == "diffsynth" else "Qwen-Image-Controlnet-Inpainting.safetensors"


def _apply_flux_controlnet_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    notes: list[dict[str, Any]],
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    previous_positive_ref: list[Any],
    previous_negative_ref: list[Any],
    status: dict[str, Any],
    asset_resolution: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    controlnet_task = str(route_data.get("controlnet_task") or TASK_INPAINT_CONTROL)
    if not asset_resolution.get("ready"):
        return {"ok": False, "reason": "validation_failed: flux ControlNet assets are not ready", "notes": notes + [{"level": "error", "field": "asset_resolution", "message": "Flux ControlNet needs source/mask or source/padding assets before workflow patching.", "missing": asset_resolution.get("missing") or []}]}
    control_image_ref, image_source = _graph_image_ref_for_flux_task(graph, controlnet_task, asset_resolution)
    mask_ref, mask_source = _graph_mask_ref_for_flux_task(graph, controlnet_task, asset_resolution)
    if not control_image_ref or not mask_ref:
        return {"ok": False, "reason": f"validation_failed: {image_source if not control_image_ref else mask_source}", "notes": notes + [{"level": "error", "field": "workflow.flux_controlnet_assets", "message": "Flux ControlNet adapter could not find the image or mask node.", "image_source": image_source, "mask_source": mask_source}]}
    adapter = _flux_requested_adapter(block, asset_resolution, status, route_data)
    adapter_key = "flux2_klein" if adapter == "fun_union" else "flux"
    flux_status = status.get(adapter_key) if isinstance(status.get(adapter_key), dict) else {}
    if not flux_status and adapter_key == "flux2_klein":
        flux_status = status.get("flux") if isinstance(status.get("flux"), dict) else {}
    object_info_present = bool(status.get("object_info_present"))
    if object_info_present and not flux_status.get("available"):
        return {"ok": False, "reason": "provider_gated: Flux ControlNet nodes are missing", "notes": notes + [{"level": "error", "field": f"nodes.{adapter_key}_controlnet", "message": "Install/update Flux-compatible ControlNet loader/apply nodes for this Flux inpaint/outpaint ControlNet adapter.", "missing": ["Flux/ControlNet loader", "Flux/ControlNet apply"], "adapter": adapter}]}
    loader_node = str(flux_status.get("loader_node") or status.get("loader_node") or "ControlNetLoader")
    apply_node = str(flux_status.get("apply_node") or status.get("apply_node") or "ControlNetApplyAdvanced")
    loader_schema = _node_schema(status, "flux2_klein_loader" if adapter == "fun_union" else "flux_loader") or _node_schema(status, "flux_loader") or _node_schema(status, "loader")
    apply_schema = _node_schema(status, "flux2_klein_apply" if adapter == "fun_union" else "flux_apply") or _node_schema(status, "flux_apply") or _node_schema(status, "apply")
    model_input = _choose_input_name(loader_schema, ("control_net_name", "controlnet_name", "model_name", "model"), _loader_model_input(status))
    applied_units: list[dict[str, Any]] = []
    created_node_ids: list[str] = []
    current_positive_ref = deepcopy(previous_positive_ref)
    current_negative_ref = deepcopy(previous_negative_ref)
    next_id: int | None = None
    if next_node_id is not None:
        try:
            next_id = int(str(next_node_id))
        except (TypeError, ValueError):
            next_id = None
    for index, unit in enumerate(units):
        uid = str(unit.get("uid") or f"unit_{index + 1}")
        model_name = _flux_unit_model(unit, adapter=adapter)
        loader_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(loader_id) + 1
        except (TypeError, ValueError):
            next_id = None
        graph[loader_id] = {"class_type": loader_node, "inputs": {model_input: model_name}}
        apply_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(apply_id) + 1
        except (TypeError, ValueError):
            next_id = None
        apply_inputs = _apply_node_inputs(apply_node, unit, current_positive_ref, current_negative_ref, [loader_id, 0], list(control_image_ref), {**status, "input_schemas": {**(status.get("input_schemas") or {}), "apply": apply_schema}})
        if _input_supports(apply_schema, "mask"):
            apply_inputs["mask"] = list(mask_ref)
        elif _input_supports(apply_schema, "control_mask"):
            apply_inputs["control_mask"] = list(mask_ref)
        elif _input_supports(apply_schema, "inpaint_mask"):
            apply_inputs["inpaint_mask"] = list(mask_ref)
        if _input_supports(apply_schema, "vae") and sampler_inputs.get("vae"):
            apply_inputs["vae"] = deepcopy(sampler_inputs.get("vae"))
        graph[apply_id] = {"class_type": apply_node, "inputs": apply_inputs}
        current_positive_ref = [apply_id, 0]
        current_negative_ref = [apply_id, 1]
        created_node_ids.extend([loader_id, apply_id])
        applied = deepcopy(unit)
        applied["model"] = model_name
        applied["adapter"] = "flux2_klein_fun_union_controlnet" if adapter == "fun_union" else "flux_alimama_inpaint_controlnet"
        applied["adapter_control_image"] = image_source
        applied["adapter_mask"] = mask_source
        applied_units.append(applied)
    graph[sampler_key]["inputs"]["positive"] = deepcopy(current_positive_ref)
    graph[sampler_key]["inputs"]["negative"] = deepcopy(current_negative_ref)
    adapter_name = "flux2_klein_fun_union_controlnet" if adapter == "fun_union" else "flux_alimama_inpaint_controlnet"
    return {"ok": True, "reason": "patched", "notes": notes + [{"level": "info", "field": "params.flux_controlnet_adapter", "message": "Flux ControlNet adapter patched sampler conditioning with source/canvas image and mask.", "controlnet_task": controlnet_task, "control_image_source": image_source, "mask_source": mask_source, "adapter": adapter}], "applied_units": applied_units, "created_node_ids": created_node_ids, "patched_positive_ref": current_positive_ref, "patched_negative_ref": current_negative_ref, "control_image_source": image_source, "mask_source": mask_source, "adapter": adapter_name, "flux_adapter": adapter}


def _apply_qwen_diffsynth_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    notes: list[dict[str, Any]],
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    previous_positive_ref: list[Any],
    previous_negative_ref: list[Any],
    status: dict[str, Any],
    asset_resolution: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    if not asset_resolution.get("ready"):
        return {"ok": False, "reason": "validation_failed: qwen ControlNet assets are not ready", "notes": notes + [{"level": "error", "field": "asset_resolution", "message": "Qwen DiffSynth ControlNet needs source/mask or source/padding assets before workflow patching.", "missing": asset_resolution.get("missing") or []}]}
    control_image_ref, image_source = _graph_image_ref_for_qwen_task(graph, str(route_data.get("controlnet_task") or TASK_INPAINT_CONTROL), asset_resolution)
    mask_ref, mask_source = _graph_mask_ref_for_qwen_task(graph, str(route_data.get("controlnet_task") or TASK_INPAINT_CONTROL), asset_resolution)
    if not control_image_ref or not mask_ref:
        return {"ok": False, "reason": f"validation_failed: {image_source if not control_image_ref else mask_source}", "notes": notes + [{"level": "error", "field": "workflow.qwen_controlnet_assets", "message": "Qwen DiffSynth ControlNet adapter could not find the image or mask node.", "image_source": image_source, "mask_source": mask_source}]}
    qwen_status = status.get("qwen") if isinstance(status.get("qwen"), dict) else {}
    object_info_present = bool(status.get("object_info_present"))
    if object_info_present and not qwen_status.get("diffsynth_available"):
        return {"ok": False, "reason": "provider_gated: Qwen DiffSynth ControlNet nodes are missing", "notes": notes + [{"level": "error", "field": "nodes.qwen_diffsynth", "message": "Install/update ComfyUI Qwen DiffSynth ControlNet nodes: ModelPatchLoader + QwenImageDiffsynthControlnet.", "missing": ["ModelPatchLoader", "QwenImageDiffsynthControlnet"]}]}
    patch_loader_node = str(qwen_status.get("diffsynth_patch_loader_node") or "ModelPatchLoader")
    apply_node = str(qwen_status.get("diffsynth_apply_node") or "QwenImageDiffsynthControlnet")
    patch_loader_schema = _node_schema(status, "qwen_diffsynth_patch_loader")
    apply_schema = _node_schema(status, "qwen_diffsynth_apply")
    applied_units: list[dict[str, Any]] = []
    created_node_ids: list[str] = []
    current_model_ref = _copy_ref(sampler_inputs.get("model"), ["1", 0])
    next_id: int | None = None
    if next_node_id is not None:
        try:
            next_id = int(str(next_node_id))
        except (TypeError, ValueError):
            next_id = None
    for index, unit in enumerate(units):
        uid = str(unit.get("uid") or f"unit_{index + 1}")
        model_name = _qwen_unit_model(unit, adapter="diffsynth")
        loader_id = _next_graph_id(graph, next_id)
        try: next_id = int(loader_id) + 1
        except (TypeError, ValueError): next_id = None
        model_input = _choose_input_name(patch_loader_schema, ("model_patch_name", "patch_name", "model_name", "model", "patch"), "model_patch_name")
        loader_inputs = {model_input: model_name}
        if model_input != "model" and _input_supports(patch_loader_schema, "model"):
            loader_inputs["model"] = deepcopy(current_model_ref)
        graph[loader_id] = {"class_type": patch_loader_node, "inputs": loader_inputs}
        apply_id = _next_graph_id(graph, next_id)
        try: next_id = int(apply_id) + 1
        except (TypeError, ValueError): next_id = None
        apply_inputs: dict[str, Any] = {}
        _add_supported_input(apply_inputs, apply_schema, "model", current_model_ref)
        patch_input = _choose_input_name(apply_schema, ("model_patch", "patch", "controlnet", "control_net", "control"), "model_patch")
        apply_inputs[patch_input] = [loader_id, 0]
        image_input = _choose_input_name(apply_schema, ("image", "control_image", "pixels"), "image")
        apply_inputs[image_input] = list(control_image_ref)
        mask_input = _choose_input_name(apply_schema, ("mask", "control_mask", "inpaint_mask"), "mask")
        apply_inputs[mask_input] = list(mask_ref)
        _add_supported_input(apply_inputs, apply_schema, "strength", float(unit.get("strength", 0.75)))
        graph[apply_id] = {"class_type": apply_node, "inputs": apply_inputs}
        current_model_ref = [apply_id, 0]
        created_node_ids.extend([loader_id, apply_id])
        applied = deepcopy(unit)
        applied["model"] = model_name
        applied["adapter"] = "qwen_diffsynth_model_patch"
        applied["adapter_control_image"] = image_source
        applied["adapter_mask"] = mask_source
        applied_units.append(applied)
    graph[sampler_key]["inputs"]["model"] = deepcopy(current_model_ref)
    return {"ok": True, "reason": "patched", "notes": notes + [{"level": "info", "field": "params.qwen_controlnet_adapter", "message": "Qwen DiffSynth model-patch ControlNet adapter patched the sampler model input.", "controlnet_task": route_data.get("controlnet_task"), "control_image_source": image_source, "mask_source": mask_source}], "applied_units": applied_units, "created_node_ids": created_node_ids, "patched_model_ref": current_model_ref, "patched_positive_ref": previous_positive_ref, "patched_negative_ref": previous_negative_ref, "control_image_source": image_source, "mask_source": mask_source, "adapter": "qwen_diffsynth_model_patch"}


def _apply_qwen_instantx_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    notes: list[dict[str, Any]],
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    previous_positive_ref: list[Any],
    previous_negative_ref: list[Any],
    status: dict[str, Any],
    asset_resolution: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    if not asset_resolution.get("ready"):
        return {"ok": False, "reason": "validation_failed: qwen ControlNet assets are not ready", "notes": notes + [{"level": "error", "field": "asset_resolution", "message": "Qwen InstantX ControlNet needs source/mask or source/padding assets before workflow patching.", "missing": asset_resolution.get("missing") or []}]}
    control_image_ref, image_source = _graph_image_ref_for_qwen_task(graph, str(route_data.get("controlnet_task") or TASK_INPAINT_CONTROL), asset_resolution)
    mask_ref, mask_source = _graph_mask_ref_for_qwen_task(graph, str(route_data.get("controlnet_task") or TASK_INPAINT_CONTROL), asset_resolution)
    if not control_image_ref or not mask_ref:
        return {"ok": False, "reason": f"validation_failed: {image_source if not control_image_ref else mask_source}", "notes": notes + [{"level": "error", "field": "workflow.qwen_controlnet_assets", "message": "Qwen InstantX ControlNet adapter could not find the image or mask node.", "image_source": image_source, "mask_source": mask_source}]}
    qwen_status = status.get("qwen") if isinstance(status.get("qwen"), dict) else {}
    object_info_present = bool(status.get("object_info_present"))
    if object_info_present and not qwen_status.get("instantx_available"):
        return {"ok": False, "reason": "provider_gated: Qwen InstantX ControlNet nodes are missing", "notes": notes + [{"level": "error", "field": "nodes.qwen_instantx", "message": "Install/update native Qwen/InstantX ControlNet support or standard ControlNetLoader + ControlNetApplyAdvanced nodes.", "missing": ["ControlNetLoader", "ControlNetApplyAdvanced"]}]}
    loader_node = str(qwen_status.get("instantx_loader_node") or status.get("loader_node") or "ControlNetLoader")
    apply_node = str(qwen_status.get("instantx_apply_node") or status.get("apply_node") or "ControlNetApplyAdvanced")
    loader_schema = _node_schema(status, "qwen_instantx_loader") or _node_schema(status, "loader")
    apply_schema = _node_schema(status, "qwen_instantx_apply") or _node_schema(status, "apply")
    qwen_vae_ref, qwen_vae_source = _resolve_qwen_vae_ref(
        graph,
        (previous_positive_ref, previous_negative_ref),
    )
    if _input_supports(apply_schema, "vae") and not qwen_vae_ref:
        return {
            "ok": False,
            "reason": "validation_failed: Qwen ControlNet apply node requires a VAE but the active Qwen workflow has none",
            "notes": notes + [{
                "level": "error",
                "field": "workflow.qwen_controlnet_vae",
                "message": "This Qwen ControlNet requires the active Qwen VAE. Connect or configure a route-owned VAE before applying ControlNet.",
                "source": qwen_vae_source,
            }],
        }
    model_input = _loader_model_input({**status, "model_inputs": status.get("model_inputs") or {}})
    applied_units: list[dict[str, Any]] = []
    created_node_ids: list[str] = []
    current_positive_ref = deepcopy(previous_positive_ref)
    current_negative_ref = deepcopy(previous_negative_ref)
    next_id: int | None = None
    if next_node_id is not None:
        try: next_id = int(str(next_node_id))
        except (TypeError, ValueError): next_id = None
    for index, unit in enumerate(units):
        uid = str(unit.get("uid") or f"unit_{index + 1}")
        model_name = _qwen_unit_model(unit, adapter="instantx")
        loader_id = _next_graph_id(graph, next_id)
        try: next_id = int(loader_id) + 1
        except (TypeError, ValueError): next_id = None
        loader_inputs = {model_input: model_name}
        if model_input == "control_net_name" and _schema_all_inputs(loader_schema) and "controlnet_name" in _schema_all_inputs(loader_schema):
            loader_inputs = {"controlnet_name": model_name}
        graph[loader_id] = {"class_type": loader_node, "inputs": loader_inputs}
        apply_id = _next_graph_id(graph, next_id)
        try: next_id = int(apply_id) + 1
        except (TypeError, ValueError): next_id = None
        apply_inputs = _apply_node_inputs(
            apply_node,
            unit,
            current_positive_ref,
            current_negative_ref,
            [loader_id, 0],
            list(control_image_ref),
            {**status, "input_schemas": {**(status.get("input_schemas") or {}), "apply": apply_schema}},
            vae_ref=qwen_vae_ref,
        )
        if _input_supports(apply_schema, "mask"):
            apply_inputs["mask"] = list(mask_ref)
        elif _input_supports(apply_schema, "control_mask"):
            apply_inputs["control_mask"] = list(mask_ref)
        elif _input_supports(apply_schema, "inpaint_mask"):
            apply_inputs["inpaint_mask"] = list(mask_ref)
        graph[apply_id] = {"class_type": apply_node, "inputs": apply_inputs}
        current_positive_ref = [apply_id, 0]
        current_negative_ref = [apply_id, 1]
        created_node_ids.extend([loader_id, apply_id])
        applied = deepcopy(unit)
        applied["model"] = model_name
        applied["adapter"] = "qwen_instantx_controlnet"
        applied["adapter_control_image"] = image_source
        applied["adapter_mask"] = mask_source
        applied["vae_source"] = qwen_vae_source
        applied_units.append(applied)
    graph[sampler_key]["inputs"]["positive"] = deepcopy(current_positive_ref)
    graph[sampler_key]["inputs"]["negative"] = deepcopy(current_negative_ref)
    return {"ok": True, "reason": "patched", "notes": notes + [{"level": "info", "field": "params.qwen_controlnet_adapter", "message": "Qwen InstantX ControlNet adapter patched sampler conditioning with the active Qwen VAE.", "controlnet_task": route_data.get("controlnet_task"), "control_image_source": image_source, "mask_source": mask_source, "vae_source": qwen_vae_source}], "applied_units": applied_units, "created_node_ids": created_node_ids, "patched_positive_ref": current_positive_ref, "patched_negative_ref": current_negative_ref, "control_image_source": image_source, "mask_source": mask_source, "vae_source": qwen_vae_source, "adapter": "qwen_instantx_controlnet"}


def _apply_qwen_controlnet_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    notes: list[dict[str, Any]],
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    previous_positive_ref: list[Any],
    previous_negative_ref: list[Any],
    status: dict[str, Any],
    asset_resolution: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    adapter = _qwen_requested_adapter(block, asset_resolution, status)
    if adapter == "instantx":
        result = _apply_qwen_instantx_patch(graph, block=block, route_data=route_data, notes=notes, sampler_key=sampler_key, sampler_inputs=sampler_inputs, previous_positive_ref=previous_positive_ref, previous_negative_ref=previous_negative_ref, status=status, asset_resolution=asset_resolution, next_node_id=next_node_id)
    else:
        result = _apply_qwen_diffsynth_patch(graph, block=block, route_data=route_data, notes=notes, sampler_key=sampler_key, sampler_inputs=sampler_inputs, previous_positive_ref=previous_positive_ref, previous_negative_ref=previous_negative_ref, status=status, asset_resolution=asset_resolution, next_node_id=next_node_id)
    result["qwen_adapter"] = adapter
    return result


def _apply_sd_mask_canvas_control_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    notes: list[dict[str, Any]],
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    previous_positive_ref: list[Any],
    previous_negative_ref: list[Any],
    status: dict[str, Any],
    asset_resolution: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    controlnet_task = str(route_data.get("controlnet_task") or TASK_MAP_CONTROL)
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    if not asset_resolution.get("ready"):
        return {
            "ok": False,
            "reason": "validation_failed: inpaint/outpaint ControlNet assets are not ready",
            "notes": notes + [{"level": "error", "field": "asset_resolution", "message": "ControlNet inpaint/outpaint task needs source/mask or source/padding assets before workflow patching.", "missing": asset_resolution.get("missing") or []}],
        }
    control_image_ref, source_kind = _graph_image_ref_for_sd_task(graph, controlnet_task, asset_resolution)
    if not control_image_ref:
        return {
            "ok": False,
            "reason": f"validation_failed: {source_kind}",
            "notes": notes + [{"level": "error", "field": "workflow.control_image", "message": "SD checkpoint ControlNet inpaint/outpaint adapter could not find the source/padded canvas image node.", "controlnet_task": controlnet_task, "source_kind": source_kind}],
        }

    loader_node = str(status.get("loader_node") or "ControlNetLoader")
    apply_node = str(status.get("apply_node") or "ControlNetApplyAdvanced")
    model_input = _loader_model_input(status)
    applied_units: list[dict[str, Any]] = []
    created_node_ids: list[str] = []
    current_positive_ref = deepcopy(previous_positive_ref)
    current_negative_ref = deepcopy(previous_negative_ref)
    next_id: int | None = None
    if next_node_id is not None:
        try:
            next_id = int(str(next_node_id))
        except (TypeError, ValueError):
            next_id = None

    for index, unit in enumerate(units):
        uid = str(unit.get("uid") or f"unit_{index + 1}")
        if not unit.get("model"):
            return {
                "ok": False,
                "reason": "validation_failed: enabled ControlNet unit is missing a model",
                "notes": notes + [{"level": "error", "field": f"inputs.units[{index}].model", "message": "Enabled ControlNet unit is missing a model.", "uid": uid}],
            }

        loader_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(loader_id) + 1
        except (TypeError, ValueError):
            next_id = None
        graph[loader_id] = {"class_type": loader_node, "inputs": {model_input: str(unit.get("model") or "")}}

        apply_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(apply_id) + 1
        except (TypeError, ValueError):
            next_id = None
        graph[apply_id] = {
            "class_type": apply_node,
            "inputs": _apply_node_inputs(apply_node, unit, current_positive_ref, current_negative_ref, [loader_id, 0], list(control_image_ref), status),
        }
        current_positive_ref = [apply_id, 0]
        current_negative_ref = [apply_id, 1]
        created_node_ids.extend([loader_id, apply_id])
        applied = deepcopy(unit)
        applied["adapter_control_image"] = source_kind
        applied_units.append(applied)

    graph[sampler_key]["inputs"]["positive"] = deepcopy(current_positive_ref)
    graph[sampler_key]["inputs"]["negative"] = deepcopy(current_negative_ref)
    return {
        "ok": True,
        "reason": "patched",
        "notes": notes + [{"level": "info", "field": "params.controlnet_task", "message": "SD checkpoint ControlNet inpaint/outpaint adapter patched sampler conditioning.", "controlnet_task": controlnet_task, "control_image_source": source_kind}],
        "applied_units": applied_units,
        "created_node_ids": created_node_ids,
        "patched_positive_ref": current_positive_ref,
        "patched_negative_ref": current_negative_ref,
        "control_image_source": source_kind,
    }


def _resolve_qwen_vae_ref(
    graph: dict[str, Any],
    conditioning_refs: tuple[list[Any], ...] = (),
) -> tuple[list[Any] | None, str]:
    """Resolve the active Qwen VAE from graph connections, never from a path.

    Qwen ControlNet apply nodes can require the same VAE used by the Qwen
    conditioning branch.  The node id is route/workflow-specific, so resolve
    it by following the sampler's positive/negative conditioning references
    first, then use decoder/loader fallbacks already present in the graph.
    """

    visited: set[str] = set()

    def inspect_node(node_id: str, *, source: str) -> tuple[list[Any] | None, str]:
        if node_id in visited:
            return None, source
        visited.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            return None, source
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        vae_ref = _copy_ref(inputs.get("vae"), [])
        if vae_ref:
            return vae_ref, f"{source}:{node_id}"
        for value in inputs.values():
            nested_ref = _copy_ref(value, [])
            if nested_ref:
                resolved, resolved_source = inspect_node(str(nested_ref[0]), source="conditioning_chain")
                if resolved:
                    return resolved, resolved_source
        return None, source

    for ref in conditioning_refs:
        normalized_ref = _copy_ref(ref, [])
        if normalized_ref:
            resolved, source = inspect_node(str(normalized_ref[0]), source="conditioning_node")
            if resolved:
                return resolved, source

    # Some Qwen graphs do not expose the conditioning node as the sampler
    # input, but still expose the active VAE on a Qwen encoder node.
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "").lower()
        if "qwen" not in class_type or not any(token in class_type for token in ("encode", "text")):
            continue
        vae_ref = _copy_ref((node.get("inputs") or {}).get("vae"), [])
        if vae_ref:
            return vae_ref, f"qwen_encoder:{node_id}"

    # Decoder VAE references are route-owned and are a safe fallback when the
    # positive/negative conditioning branch is custom or hidden.
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") not in {"VAEDecode", "VAEEncode"}:
            continue
        vae_ref = _copy_ref((node.get("inputs") or {}).get("vae"), [])
        if vae_ref:
            return vae_ref, f"vae_codec:{node_id}"

    # Final fallback for standard route-owned loaders. These are graph output
    # references, not filesystem paths, and therefore remain portable.
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        if class_type in {"CheckpointLoaderSimple", "CheckpointLoader"}:
            return [str(node_id), 2], f"checkpoint_loader:{node_id}"
        if class_type in {"VAELoader", "VaeGGUF", "VAELoaderGGUF"}:
            return [str(node_id), 0], f"vae_loader:{node_id}"

    return None, "missing_qwen_vae"


def _input_supports(node_schema: dict[str, Any] | None, name: str) -> bool:
    if not isinstance(node_schema, dict):
        return True
    required = set(node_schema.get("required_inputs") or [])
    optional = set(node_schema.get("optional_inputs") or [])
    hidden = set(node_schema.get("hidden_inputs") or [])
    known = required | optional | hidden
    return not known or name in known


def _loader_model_input(node_status: dict[str, Any]) -> str:
    model_inputs = node_status.get("model_inputs") if isinstance(node_status.get("model_inputs"), dict) else {}
    for candidate in ("control_net_name", "controlnet_name", "control_net", "model_name", "model"):
        if candidate in model_inputs:
            return candidate
    schemas = node_status.get("input_schemas") if isinstance(node_status.get("input_schemas"), dict) else {}
    loader_schema = schemas.get("loader") if isinstance(schemas.get("loader"), dict) else {}
    schema_inputs = _schema_all_inputs(loader_schema)
    for candidate in ("control_net_name", "controlnet_name", "control_net", "model_name", "model"):
        if candidate in schema_inputs:
            return candidate
    return "control_net_name"


def _apply_node_inputs(
    node_class: str,
    unit: dict[str, Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    control_ref: list[Any],
    image_ref: list[Any],
    node_status: dict[str, Any],
    *,
    vae_ref: list[Any] | None = None,
) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "positive": deepcopy(positive_ref),
        "negative": deepcopy(negative_ref),
        "control_net": deepcopy(control_ref),
        "image": deepcopy(image_ref),
        "strength": float(unit.get("strength", 0.45)),
    }
    apply_schema = ((node_status.get("input_schemas") or {}).get("apply") or {}) if isinstance(node_status.get("input_schemas"), dict) else {}
    if vae_ref and _input_supports(apply_schema, "vae"):
        inputs["vae"] = deepcopy(vae_ref)
    if "Advanced" in node_class or node_class.startswith("ACN_"):
        if _input_supports(apply_schema, "start_percent"):
            inputs["start_percent"] = float(unit.get("start_percent", 0.0))
        if _input_supports(apply_schema, "end_percent"):
            inputs["end_percent"] = float(unit.get("end_percent", 1.0))
    return inputs


def build_workflow_patch_summary(
    *,
    route: dict[str, Any],
    node_status: dict[str, Any] | None = None,
    applied_units: list[dict[str, Any]] | None = None,
    node_ids: list[str] | None = None,
    previous_positive_ref: list[Any] | None = None,
    previous_negative_ref: list[Any] | None = None,
    patched_positive_ref: list[Any] | None = None,
    patched_negative_ref: list[Any] | None = None,
    sampler_node_id: str | None = None,
    reason: str = "",
    applied: bool | None = None,
    controlnet_task: str = TASK_MAP_CONTROL,
) -> dict[str, Any]:
    applied_units = deepcopy(applied_units or [])
    node_ids = list(node_ids or [])
    mutated = bool(applied_units and node_ids) if applied is None else bool(applied)
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "applied": mutated,
        "mutated": mutated,
        "patch_type": "conditioning",
        "controlnet_task": str(controlnet_task or TASK_MAP_CONTROL),
        "node": (node_status or {}).get("apply_node") or "",
        "node_class": (node_status or {}).get("apply_node") or "",
        "node_ids": node_ids,
        "controlnet_unit_count": len(applied_units),
        "controlnet_units": [
            {
                "uid": str(unit.get("uid") or ""),
                "unit": str(unit.get("unit") or ""),
                "preprocessor": str(unit.get("preprocessor") or ""),
                "model": str(unit.get("model") or ""),
            }
            for unit in applied_units
        ],
        "previous_positive_ref": deepcopy(previous_positive_ref or []),
        "previous_negative_ref": deepcopy(previous_negative_ref or []),
        "patched_positive_ref": deepcopy(patched_positive_ref or previous_positive_ref or []),
        "patched_negative_ref": deepcopy(patched_negative_ref or previous_negative_ref or []),
        "sampler_node_id": str(sampler_node_id or ""),
        "route": deepcopy(route or {}),
        "node_status": {
            "loader_node": (node_status or {}).get("loader_node"),
            "apply_node": (node_status or {}).get("apply_node"),
            "advanced_node": (node_status or {}).get("advanced_node"),
            "base_available": bool((node_status or {}).get("base_available")),
        },
        "reason": reason,
    }


def apply_controlnet_patch(
    workflow: dict[str, Any],
    payload: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None = None,
    node_status: dict[str, Any] | None = None,
    sampler_node_id: str | int = "5",
    next_node_id: int | str | None = None,
    image_params: dict[str, Any] | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Patch a validated Comfy checkpoint workflow with ControlNet conditioning.

    Phase G intentionally patches only active SDXL/SD1.5 checkpoint routes declared
    by the support matrix. It chains ControlNetApply nodes over the sampler's
    positive/negative conditioning and does not mutate unrelated route/family graphs.
    """
    graph = deepcopy(workflow or {})
    route_data = _route_with_state(route)
    block, notes = _extension_block_from_payload(payload or {}, route_data)
    sampler_key = str(sampler_node_id)
    sampler = graph.get(sampler_key) if isinstance(graph.get(sampler_key), dict) else {}
    sampler_inputs = sampler.get("inputs") if isinstance(sampler.get("inputs"), dict) else {}
    previous_positive_ref = _copy_ref(sampler_inputs.get("positive"), ["2", 0])
    previous_negative_ref = _copy_ref(sampler_inputs.get("negative"), ["3", 0])

    def no_patch(reason: str, *, status: dict[str, Any] | None = None, extra_notes: list[dict[str, Any]] | None = None, asset_resolution: dict[str, Any] | None = None) -> dict[str, Any]:
        validation_notes = notes + list(extra_notes or [])
        patch = build_workflow_patch_summary(
            route=route_data,
            node_status=status or node_status or {},
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            patched_positive_ref=previous_positive_ref,
            patched_negative_ref=previous_negative_ref,
            sampler_node_id=sampler_key,
            reason=reason,
            applied=False,
            controlnet_task=str(((block.get("params") or {}) if isinstance(block, dict) else {}).get("controlnet_task") or TASK_MAP_CONTROL),
        )
        return {
            "workflow": graph,
            "validation": {"ok": False if block.get("enabled") else True, "enabled": bool(block.get("enabled")), "block": block, "validation": validation_notes, "route": route_data, "node_status": status or node_status or {}, "workflow_patch_allowed": False, "reason": reason, "asset_resolution": asset_resolution or {}},
            "workflow_patch": patch,
            "mutated": False,
            "changed": False,
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "route_state": route_data.get("controlnet_task_state") or route_data.get("route_state"),
            "gated_reason": reason,
        }

    if not block.get("enabled"):
        return no_patch(str((block.get("metadata") or {}).get("reason") or "disabled"))

    controlnet_task = normalize_controlnet_task(((block.get("params") or {}) if isinstance(block, dict) else {}).get("controlnet_task") or TASK_MAP_CONTROL, workflow_mode=route_data.get("workflow_mode"))
    task_state = controlnet_task_state(route_data.get("backend"), route_data.get("family"), route_data.get("loader"), route_data.get("workflow_mode"), controlnet_task)
    route_data["controlnet_task"] = controlnet_task
    route_data["controlnet_task_state"] = task_state

    asset_resolution = resolve_controlnet_task_assets(block, image_params=image_params, route=route_data) if controlnet_task != TASK_MAP_CONTROL else {}

    if task_state not in ACTIVE_STATES:
        return no_patch(task_route_reason(controlnet_task, task_state), asset_resolution=asset_resolution)

    if not sampler or sampler.get("class_type") != "KSampler":
        return no_patch("validation_failed: target KSampler node was not found", extra_notes=[{"level": "error", "field": "workflow.sampler", "message": "ControlNet patch requires a KSampler node with positive/negative inputs."}])

    status = _route_profiled_node_status(node_status or inspect_nodes(available_nodes), route_data, controlnet_task)

    qwen_vae_ref: list[Any] | None = None
    qwen_vae_source = ""
    if _is_qwen_controlnet_route(route_data):
        qwen_apply_schema = _node_schema(status, "apply")
        qwen_vae_ref, qwen_vae_source = _resolve_qwen_vae_ref(
            graph,
            (previous_positive_ref, previous_negative_ref),
        )
        if _input_supports(qwen_apply_schema, "vae") and not qwen_vae_ref:
            return no_patch(
                "validation_failed: Qwen ControlNet apply node requires a VAE but the active Qwen workflow has none",
                status=status,
                extra_notes=[{
                    "level": "error",
                    "field": "workflow.qwen_controlnet_vae",
                    "message": "This Qwen ControlNet requires the active Qwen VAE. Connect or configure a route-owned VAE before applying ControlNet.",
                    "source": qwen_vae_source,
                }],
            )

    if controlnet_task in {TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL} and _flux_route_active(route_data, controlnet_task):
        adapter_result = _apply_flux_controlnet_patch(
            graph,
            block=block,
            route_data=route_data,
            notes=notes,
            sampler_key=sampler_key,
            sampler_inputs=sampler_inputs,
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            status=status,
            asset_resolution=asset_resolution,
            next_node_id=next_node_id,
        )
        if not adapter_result.get("ok"):
            return no_patch(str(adapter_result.get("reason") or task_route_reason(controlnet_task, "planned_gated")), status=status, extra_notes=adapter_result.get("notes") or [], asset_resolution=asset_resolution)
        patch = build_workflow_patch_summary(
            route={**route_data, "adapter": adapter_result.get("adapter"), "flux_adapter": adapter_result.get("flux_adapter"), "control_image_source": adapter_result.get("control_image_source"), "mask_source": adapter_result.get("mask_source")},
            node_status=status,
            applied_units=adapter_result.get("applied_units") or [],
            node_ids=adapter_result.get("created_node_ids") or [],
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            patched_positive_ref=adapter_result.get("patched_positive_ref") or previous_positive_ref,
            patched_negative_ref=adapter_result.get("patched_negative_ref") or previous_negative_ref,
            sampler_node_id=sampler_key,
            reason="patched",
            applied=True,
            controlnet_task=controlnet_task,
        )
        patch["adapter"] = adapter_result.get("adapter")
        patch["flux_adapter"] = adapter_result.get("flux_adapter")
        patch["control_image_source"] = adapter_result.get("control_image_source")
        patch["mask_source"] = adapter_result.get("mask_source")
        return {
            "workflow": graph,
            "validation": {"ok": True, "enabled": True, "block": block, "validation": adapter_result.get("notes") or notes, "route": route_data, "node_status": status, "workflow_patch_allowed": True, "reason": "patched", "asset_resolution": asset_resolution},
            "workflow_patch": patch,
            "mutated": True,
            "changed": True,
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "route_state": route_data.get("controlnet_task_state") or route_data.get("route_state"),
        }

    if controlnet_task in {TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL} and _qwen_route_active(route_data, controlnet_task):
        adapter_result = _apply_qwen_controlnet_patch(
            graph,
            block=block,
            route_data=route_data,
            notes=notes,
            sampler_key=sampler_key,
            sampler_inputs=sampler_inputs,
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            status=status,
            asset_resolution=asset_resolution,
            next_node_id=next_node_id,
        )
        if not adapter_result.get("ok"):
            return no_patch(str(adapter_result.get("reason") or task_route_reason(controlnet_task, "planned_gated")), status=status, extra_notes=adapter_result.get("notes") or [], asset_resolution=asset_resolution)
        patch = build_workflow_patch_summary(
            route={**route_data, "adapter": adapter_result.get("adapter"), "qwen_adapter": adapter_result.get("qwen_adapter"), "control_image_source": adapter_result.get("control_image_source"), "mask_source": adapter_result.get("mask_source")},
            node_status=status,
            applied_units=adapter_result.get("applied_units") or [],
            node_ids=adapter_result.get("created_node_ids") or [],
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            patched_positive_ref=adapter_result.get("patched_positive_ref") or previous_positive_ref,
            patched_negative_ref=adapter_result.get("patched_negative_ref") or previous_negative_ref,
            sampler_node_id=sampler_key,
            reason="patched",
            applied=True,
            controlnet_task=controlnet_task,
        )
        patch["adapter"] = adapter_result.get("adapter")
        patch["qwen_adapter"] = adapter_result.get("qwen_adapter")
        patch["control_image_source"] = adapter_result.get("control_image_source")
        patch["mask_source"] = adapter_result.get("mask_source")
        if adapter_result.get("vae_source"):
            patch["qwen_controlnet_vae_source"] = adapter_result.get("vae_source")
        if adapter_result.get("patched_model_ref"):
            patch["patched_model_ref"] = adapter_result.get("patched_model_ref")
        return {
            "workflow": graph,
            "validation": {"ok": True, "enabled": True, "block": block, "validation": adapter_result.get("notes") or notes, "route": route_data, "node_status": status, "workflow_patch_allowed": True, "reason": "patched", "asset_resolution": asset_resolution},
            "workflow_patch": patch,
            "mutated": True,
            "changed": True,
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "route_state": route_data.get("controlnet_task_state") or route_data.get("route_state"),
        }

    if status.get("provider_gated"):
        missing = ", ".join(status.get("missing") or [])
        return no_patch(f"provider_gated: ControlNet base Comfy nodes are missing: {missing}", status=status, extra_notes=[{"level": "error", "field": "nodes.base", "message": "Required ControlNet base nodes are missing.", "missing": status.get("missing") or []}])

    if controlnet_task in {TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL}:
        adapter_result = _apply_sd_mask_canvas_control_patch(
            graph,
            block=block,
            route_data=route_data,
            notes=notes,
            sampler_key=sampler_key,
            sampler_inputs=sampler_inputs,
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            status=status,
            asset_resolution=asset_resolution,
            next_node_id=next_node_id,
        )
        if not adapter_result.get("ok"):
            return no_patch(str(adapter_result.get("reason") or task_route_reason(controlnet_task, "planned_gated")), status=status, extra_notes=adapter_result.get("notes") or [], asset_resolution=asset_resolution)
        patch = build_workflow_patch_summary(
            route={**route_data, "adapter": "sd_checkpoint_mask_canvas_control", "control_image_source": adapter_result.get("control_image_source")},
            node_status=status,
            applied_units=adapter_result.get("applied_units") or [],
            node_ids=adapter_result.get("created_node_ids") or [],
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            patched_positive_ref=adapter_result.get("patched_positive_ref") or previous_positive_ref,
            patched_negative_ref=adapter_result.get("patched_negative_ref") or previous_negative_ref,
            sampler_node_id=sampler_key,
            reason="patched",
            applied=True,
            controlnet_task=controlnet_task,
        )
        patch["adapter"] = "sd_checkpoint_mask_canvas_control"
        patch["control_image_source"] = adapter_result.get("control_image_source")
        return {
            "workflow": graph,
            "validation": {"ok": True, "enabled": True, "block": block, "validation": adapter_result.get("notes") or notes, "route": route_data, "node_status": status, "workflow_patch_allowed": True, "reason": "patched", "asset_resolution": asset_resolution},
            "workflow_patch": patch,
            "mutated": True,
            "changed": True,
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "route_state": route_data.get("controlnet_task_state") or route_data.get("route_state"),
        }

    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    if not units:
        return no_patch("no_active_units")

    validation_notes = list(notes)
    applied_units: list[dict[str, Any]] = []
    created_node_ids: list[str] = []
    current_positive_ref = deepcopy(previous_positive_ref)
    current_negative_ref = deepcopy(previous_negative_ref)
    next_id: int | None = None
    if next_node_id is not None:
        try:
            next_id = int(str(next_node_id))
        except (TypeError, ValueError):
            next_id = None

    loader_node = str(status.get("loader_node") or "ControlNetLoader")
    apply_node = str(status.get("apply_node") or "ControlNetApplyAdvanced")
    model_input = _loader_model_input(status)

    for index, unit in enumerate(units):
        uid = str(unit.get("uid") or f"unit_{index + 1}")
        prep = preprocessor_status(unit.get("preprocessor"), status, unit=unit.get("unit"))
        if prep.get("state") == "provider_gated":
            return no_patch(str(prep.get("reason") or "provider_gated: ControlNet preprocessor node is missing"), status=status, extra_notes=[{"level": "error", "field": f"inputs.units[{index}].preprocessor", "message": str(prep.get("reason") or "ControlNet preprocessor node is missing."), "uid": uid, "group": prep.get("group")}])
        if prep.get("state") == "unsupported":
            return no_patch(str(prep.get("reason") or "unsupported: ControlNet preprocessor is not supported"), status=status, extra_notes=[{"level": "error", "field": f"inputs.units[{index}].preprocessor", "message": str(prep.get("reason") or "Unsupported ControlNet preprocessor."), "uid": uid, "group": prep.get("group")}])
        if unit.get("advanced_enabled") and not status.get("advanced_available"):
            return no_patch("provider_gated: Advanced ControlNet was requested but no advanced apply node was detected", status=status, extra_notes=[{"level": "error", "field": f"inputs.units[{index}].advanced_enabled", "message": "Advanced ControlNet was requested but no advanced apply node was detected.", "uid": uid}])
        if not unit.get("model"):
            return no_patch("validation_failed: enabled ControlNet unit is missing a model", status=status, extra_notes=[{"level": "error", "field": f"inputs.units[{index}].model", "message": "Enabled ControlNet unit is missing a model.", "uid": uid}])
        asset = _asset_for_unit(assets, uid)
        image_name = _asset_to_image_name(asset)
        if not image_name:
            return no_patch("validation_failed: enabled ControlNet unit is missing a control image or generated map", status=status, extra_notes=[{"level": "error", "field": f"assets.control_images.{uid}", "message": "Enabled ControlNet unit needs a control image or generated map before workflow patching.", "uid": uid}])

        load_image_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(load_image_id) + 1
        except (TypeError, ValueError):
            next_id = None
        graph[load_image_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}

        loader_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(loader_id) + 1
        except (TypeError, ValueError):
            next_id = None
        graph[loader_id] = {"class_type": loader_node, "inputs": {model_input: str(unit.get("model") or "")}}

        apply_id = _next_graph_id(graph, next_id)
        try:
            next_id = int(apply_id) + 1
        except (TypeError, ValueError):
            next_id = None
        graph[apply_id] = {
            "class_type": apply_node,
            "inputs": _apply_node_inputs(
                apply_node,
                unit,
                current_positive_ref,
                current_negative_ref,
                [loader_id, 0],
                [load_image_id, 0],
                status,
                vae_ref=qwen_vae_ref,
            ),
        }
        current_positive_ref = [apply_id, 0]
        current_negative_ref = [apply_id, 1]
        created_node_ids.extend([load_image_id, loader_id, apply_id])
        applied = deepcopy(unit)
        if qwen_vae_source:
            applied["vae_source"] = qwen_vae_source
        applied_units.append(applied)

    graph[sampler_key]["inputs"]["positive"] = deepcopy(current_positive_ref)
    graph[sampler_key]["inputs"]["negative"] = deepcopy(current_negative_ref)

    patch = build_workflow_patch_summary(
        route=route_data,
        node_status=status,
        applied_units=applied_units,
        node_ids=created_node_ids,
        previous_positive_ref=previous_positive_ref,
        previous_negative_ref=previous_negative_ref,
        patched_positive_ref=current_positive_ref,
        patched_negative_ref=current_negative_ref,
        sampler_node_id=sampler_key,
        reason="patched",
        applied=True,
        controlnet_task=controlnet_task,
    )
    if qwen_vae_source:
        patch["qwen_controlnet_vae_source"] = qwen_vae_source
    return {
        "workflow": graph,
        "validation": {"ok": True, "enabled": True, "block": block, "validation": validation_notes, "route": route_data, "node_status": status, "workflow_patch_allowed": True, "reason": "patched", "asset_resolution": asset_resolution if controlnet_task != TASK_MAP_CONTROL else {}},
        "workflow_patch": patch,
        "mutated": True,
        "changed": True,
        "extension_id": EXTENSION_ID,
        "phase": PHASE,
        "route_state": route_data.get("controlnet_task_state") or route_data.get("route_state"),
    }
