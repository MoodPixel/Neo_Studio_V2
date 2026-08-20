from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from .asset_resolver import resolve_controlnet_task_assets
from .capability_registry import control_intent_from_unit, resolve_route_capability, validate_model_selection_for_route
from .node_discovery import inspect_nodes, preprocessor_status
from .map_preprocessors import build_preprocessor_inputs
from .payload_schema import EXTENSION_ID, normalize_block
from neo_extensions.built_in.lora_stack.backend.catalog_bridge import resolve_exact_provider_catalog_name
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
QWEN_VAE_CONTRACT_SCHEMA_VERSION = "neo.image.controlnet.qwen_vae_contract.v1"
POSE_TRANSFER_METHOD = "qwen_transfer"
POSE_TRANSFER_FAMILY = "qwen_image_edit_2511"
POSE_TRANSFER_LOADERS = {"diffusion_model", "gguf"}
POSE_TRANSFER_MODES = {"img2img", "edit"}
QWEN_EDIT_ENCODERS = {
    "TextEncodeQwenImageEditPlus",
    "TextEncodeQwenImageEditPlus_lrzjason",
    "TextEncodeQwenImageEditPlusAdvance_lrzjason",
    "TextEncodeQwenImageEditPlusPro_lrzjason",
}


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
    pose_transfer_requested = _raw_pose_transfer_requested(raw_block)
    block, notes = normalize_block(raw_block, route=effective_route, enforce_route_state=not pose_transfer_requested)
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


def _is_krea2_controlnet_route(route_data: dict[str, Any] | None, controlnet_task: str = TASK_MAP_CONTROL) -> bool:
    route_data = route_data if isinstance(route_data, dict) else {}
    family = str(route_data.get("family") or "").strip().lower()
    loader = str(route_data.get("loader") or "").strip().lower()
    mode = str(route_data.get("workflow_mode") or route_data.get("mode") or "generate").strip().lower()
    if mode == "txt2img":
        mode = "generate"
    return family in {"krea2", "krea2_turbo"} and loader in {"diffusion_model", "gguf"} and controlnet_task == TASK_MAP_CONTROL and mode == "generate"


def _route_profiled_node_status(status: dict[str, Any], route_data: dict[str, Any], controlnet_task: str) -> dict[str, Any]:
    """Return a node-status view that matches the route adapter.

    P9.2: Qwen map control may expose Qwen/InstantX loader/apply nodes
    instead of SD-style ControlNetLoader. Flux.1 may expose Flux-specific
    loader/apply nodes. The generic map patcher can still chain conditioning
    when the route profile supplies compatible loader/apply schemas.
    """
    if _is_krea2_controlnet_route(route_data, controlnet_task):
        krea_status = status.get("krea2_control") if isinstance(status.get("krea2_control"), dict) else {}
        if not krea_status.get("available") and status.get("object_info_present"):
            return status
        patched = deepcopy(status)
        patched["base_available"] = bool(krea_status.get("available") or not status.get("object_info_present"))
        patched["provider_gated"] = bool(status.get("object_info_present") and not krea_status.get("available"))
        patched["missing"] = list(krea_status.get("missing") or []) if patched["provider_gated"] else []
        patched["route_adapter"] = "krea2_control_lora"
        patched["route_profile_id"] = route_data.get("route_profile_id")
        return patched
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


def _schema_input_mode(schema: dict[str, Any] | None, name: str) -> str:
    """Return the declared input mode without treating an unknown schema as support."""
    if not isinstance(schema, dict) or not schema:
        return "unknown"
    wanted = str(name)
    if wanted in {str(item) for item in schema.get("required_inputs") or []}:
        return "required"
    if wanted in {str(item) for item in schema.get("optional_inputs") or []}:
        return "optional"
    if wanted in {str(item) for item in schema.get("hidden_inputs") or []}:
        return "hidden"
    return "unsupported"


def _qwen_vae_contract(status: dict[str, Any], adapter: str) -> dict[str, Any]:
    """Describe VAE behavior for the selected Qwen adapter from its real node schema.

    DiffSynth is model-patch based and must not inherit the generic ControlNet
    apply-node VAE policy. It only blocks when its own apply node explicitly
    declares ``vae`` as required. InstantX/native Qwen ControlNet is VAE-aware:
    when its apply node explicitly exposes ``vae`` (required or optional), Neo
    resolves and injects the active route VAE and blocks if that VAE is absent.
    Unknown schemas never create a synthetic requirement.
    """
    adapter_name = "instantx" if str(adapter or "").strip().lower() == "instantx" else "diffsynth"
    schema_key = "qwen_instantx_apply" if adapter_name == "instantx" else "qwen_diffsynth_apply"
    schema = _node_schema(status, schema_key)
    mode = _schema_input_mode(schema, "vae")
    supported = mode in {"required", "optional"}
    required = (mode == "required") if adapter_name == "diffsynth" else supported
    if adapter_name == "diffsynth":
        policy = "required_by_apply_schema" if mode == "required" else ("optional_if_available" if mode == "optional" else "not_used")
    else:
        policy = "required_for_vae_aware_apply" if supported else "not_exposed_by_apply_schema"
    return {
        "schema_version": QWEN_VAE_CONTRACT_SCHEMA_VERSION,
        "adapter": adapter_name,
        "schema_key": schema_key,
        "schema_node": str(schema.get("node") or ""),
        "input_mode": mode,
        "supported": supported,
        "required": required,
        "inject": supported,
        "policy": policy,
    }


def _qwen_vae_missing_result(notes: list[dict[str, Any]], contract: dict[str, Any], source: str) -> dict[str, Any]:
    adapter_label = "InstantX" if contract.get("adapter") == "instantx" else "DiffSynth"
    return {
        "ok": False,
        "reason": f"validation_failed: Qwen {adapter_label} ControlNet requires the active Qwen VAE but none was found",
        "notes": notes + [{
            "level": "error",
            "field": "workflow.qwen_controlnet_vae",
            "message": f"The selected Qwen {adapter_label} apply node declares a VAE contract. Connect or configure the matching route-owned Qwen VAE before applying ControlNet.",
            "source": source,
            "adapter": contract.get("adapter"),
            "schema_node": contract.get("schema_node"),
            "input_mode": contract.get("input_mode"),
            "vae_policy": contract.get("policy"),
        }],
        "vae_contract": deepcopy(contract),
    }


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
    vae_contract = _qwen_vae_contract(status, "diffsynth")
    qwen_vae_ref: list[Any] | None = None
    qwen_vae_source = "diffsynth_apply_schema_has_no_vae_input"
    if vae_contract.get("supported"):
        qwen_vae_ref, qwen_vae_source = _resolve_qwen_vae_ref(
            graph,
            (previous_positive_ref, previous_negative_ref),
        )
        if vae_contract.get("required") and not qwen_vae_ref:
            return _qwen_vae_missing_result(notes, vae_contract, qwen_vae_source)
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
        if qwen_vae_ref and vae_contract.get("inject"):
            apply_inputs["vae"] = deepcopy(qwen_vae_ref)
        graph[apply_id] = {"class_type": apply_node, "inputs": apply_inputs}
        current_model_ref = [apply_id, 0]
        created_node_ids.extend([loader_id, apply_id])
        applied = deepcopy(unit)
        applied["model"] = model_name
        applied["adapter"] = "qwen_diffsynth_model_patch"
        applied["adapter_control_image"] = image_source
        applied["adapter_mask"] = mask_source
        applied["vae_policy"] = vae_contract.get("policy")
        if qwen_vae_ref:
            applied["vae_source"] = qwen_vae_source
        applied_units.append(applied)
    graph[sampler_key]["inputs"]["model"] = deepcopy(current_model_ref)
    return {
        "ok": True,
        "reason": "patched",
        "notes": notes + [{
            "level": "info",
            "field": "params.qwen_controlnet_adapter",
            "message": "Qwen DiffSynth model-patch ControlNet patched the sampler model using its own apply-node VAE contract.",
            "controlnet_task": route_data.get("controlnet_task"),
            "control_image_source": image_source,
            "mask_source": mask_source,
            "vae_policy": vae_contract.get("policy"),
            "vae_source": qwen_vae_source if qwen_vae_ref else "",
        }],
        "applied_units": applied_units,
        "created_node_ids": created_node_ids,
        "patched_model_ref": current_model_ref,
        "patched_positive_ref": previous_positive_ref,
        "patched_negative_ref": previous_negative_ref,
        "control_image_source": image_source,
        "mask_source": mask_source,
        "vae_source": qwen_vae_source if qwen_vae_ref else "",
        "vae_contract": deepcopy(vae_contract),
        "adapter": "qwen_diffsynth_model_patch",
    }


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
    vae_contract = _qwen_vae_contract(status, "instantx")
    qwen_vae_ref: list[Any] | None = None
    qwen_vae_source = "instantx_apply_schema_has_no_vae_input"
    if vae_contract.get("supported"):
        qwen_vae_ref, qwen_vae_source = _resolve_qwen_vae_ref(
            graph,
            (previous_positive_ref, previous_negative_ref),
        )
        if vae_contract.get("required") and not qwen_vae_ref:
            return _qwen_vae_missing_result(notes, vae_contract, qwen_vae_source)
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
        applied["vae_policy"] = vae_contract.get("policy")
        if qwen_vae_ref:
            applied["vae_source"] = qwen_vae_source
        applied_units.append(applied)
    graph[sampler_key]["inputs"]["positive"] = deepcopy(current_positive_ref)
    graph[sampler_key]["inputs"]["negative"] = deepcopy(current_negative_ref)
    return {
        "ok": True,
        "reason": "patched",
        "notes": notes + [{
            "level": "info",
            "field": "params.qwen_controlnet_adapter",
            "message": "Qwen InstantX ControlNet patched sampler conditioning using its adapter-specific VAE contract.",
            "controlnet_task": route_data.get("controlnet_task"),
            "control_image_source": image_source,
            "mask_source": mask_source,
            "vae_source": qwen_vae_source if qwen_vae_ref else "",
            "vae_policy": vae_contract.get("policy"),
        }],
        "applied_units": applied_units,
        "created_node_ids": created_node_ids,
        "patched_positive_ref": current_positive_ref,
        "patched_negative_ref": current_negative_ref,
        "control_image_source": image_source,
        "mask_source": mask_source,
        "vae_source": qwen_vae_source if qwen_vae_ref else "",
        "vae_contract": deepcopy(vae_contract),
        "adapter": "qwen_instantx_controlnet",
    }


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



def _raw_pose_transfer_requested(raw_block: Mapping[str, Any] | None) -> bool:
    block = raw_block if isinstance(raw_block, Mapping) else {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), Mapping) else {}
    units = inputs.get("units") if isinstance(inputs.get("units"), list) else block.get("units")
    if not isinstance(units, list):
        return False
    return any(
        isinstance(unit, Mapping)
        and unit.get("enabled", True) is not False
        and str(unit.get("pose_method") or "controlnet").strip().lower() == POSE_TRANSFER_METHOD
        for unit in units
    )


def _pose_transfer_route_supported(route: Mapping[str, Any] | None) -> bool:
    data = route if isinstance(route, Mapping) else {}
    backend = str(data.get("backend") or "").strip().lower()
    family = str(data.get("family") or "").strip().lower()
    loader = str(data.get("loader") or "").strip().lower()
    mode = str(data.get("workflow_mode") or data.get("mode") or "").strip().lower()
    if mode == "generate":
        mode = "txt2img"
    return backend in {"comfyui", "comfyui_portable"} and family == POSE_TRANSFER_FAMILY and loader in POSE_TRANSFER_LOADERS and mode in POSE_TRANSFER_MODES


def _object_info_node_contract(available_nodes: Any, class_type: str) -> tuple[set[str], list[str]]:
    if not isinstance(available_nodes, Mapping):
        return set(), []
    meta = available_nodes.get(class_type)
    if not isinstance(meta, Mapping):
        return set(), []
    input_meta = meta.get("input") if isinstance(meta.get("input"), Mapping) else {}
    names: set[str] = set()
    lora_choices: list[str] = []
    for group in ("required", "optional"):
        group_meta = input_meta.get(group) if isinstance(input_meta.get(group), Mapping) else {}
        for name, spec in group_meta.items():
            names.add(str(name))
            if str(name) == "lora_name" and isinstance(spec, (list, tuple)) and spec and isinstance(spec[0], list):
                lora_choices = [str(item) for item in spec[0] if str(item).strip()]
    return names, lora_choices


def _conditioning_source_node(graph: Mapping[str, Any], ref: Any) -> str | None:
    current = _copy_ref(ref, ["", 0])
    seen: set[str] = set()
    for _ in range(12):
        node_id = str(current[0] or "")
        if not node_id or node_id in seen:
            return None
        seen.add(node_id)
        node = graph.get(node_id) if isinstance(graph, Mapping) else None
        if not isinstance(node, Mapping):
            return None
        class_type = str(node.get("class_type") or "")
        if class_type in QWEN_EDIT_ENCODERS or class_type.startswith("TextEncodeQwenImageEditPlus"):
            return node_id
        inputs = node.get("inputs") if isinstance(node.get("inputs"), Mapping) else {}
        next_ref = None
        for key in ("conditioning", "positive", "negative"):
            candidate = inputs.get(key)
            if isinstance(candidate, (list, tuple)) and len(candidate) >= 2:
                next_ref = candidate
                break
        if next_ref is None:
            return None
        current = _copy_ref(next_ref, ["", 0])
    return None


def _model_sampling_anchor(graph: dict[str, Any], sampler_inputs: Mapping[str, Any]) -> tuple[str | None, list[Any] | None]:
    current = _copy_ref(sampler_inputs.get("model"), ["", 0])
    seen: set[str] = set()
    for _ in range(16):
        node_id = str(current[0] or "")
        if not node_id or node_id in seen:
            break
        seen.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            break
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if str(node.get("class_type") or "") == "ModelSamplingAuraFlow":
            upstream = inputs.get("model")
            return node_id, _copy_ref(upstream, ["", 0]) if isinstance(upstream, (list, tuple)) else None
        upstream = inputs.get("model")
        if not isinstance(upstream, (list, tuple)) or len(upstream) < 2:
            break
        current = _copy_ref(upstream, ["", 0])
    direct = sampler_inputs.get("model")
    return None, _copy_ref(direct, ["", 0]) if isinstance(direct, (list, tuple)) else None


def _append_pose_instruction(inputs: dict[str, Any], instruction: str) -> None:
    clean = str(instruction or "").strip()
    if not clean:
        return
    for key in ("prompt", "text"):
        if key not in inputs:
            continue
        current = str(inputs.get(key) or "").strip()
        if clean.casefold() in current.casefold():
            return
        inputs[key] = f"{current}\n\n{clean}".strip()
        return


def _apply_qwen_pose_transfer_patch(
    graph: dict[str, Any],
    *,
    unit: Mapping[str, Any],
    route_data: Mapping[str, Any],
    available_nodes: Any,
    status: Mapping[str, Any],
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    next_node_id: int | str | None,
) -> dict[str, Any]:
    notes: list[dict[str, Any]] = []
    if not _pose_transfer_route_supported(route_data):
        return {"ok": False, "reason": "Pose Transfer is currently available only for Qwen Image Edit 2511 Img2Img/Edit on local ComfyUI using Safetensors/Components or GGUF.", "notes": notes}
    if not isinstance(available_nodes, Mapping):
        return {"ok": False, "reason": "Pose Transfer needs live Comfy node information so DWPose and the model-only LoRA catalog can be verified.", "notes": notes}

    prep = preprocessor_status("dwpose", dict(status), unit="openpose")
    if prep.get("state") not in {"available", "experimental_available"} or prep.get("backend") != "comfy_preprocessor":
        detail = str(prep.get("reason") or "").strip()
        reason = "DWPose is not available on the selected Comfy backend."
        if detail and "dwpose" not in detail.lower():
            reason = f"{reason} {detail}"
        return {"ok": False, "reason": reason, "notes": notes}
    dwpose_node = str(prep.get("node") or "")
    if not dwpose_node or dwpose_node not in available_nodes:
        return {"ok": False, "reason": "DWPose is not available on the selected Comfy backend.", "notes": notes}

    loader_class = "LoraLoaderModelOnly"
    loader_inputs, lora_choices = _object_info_node_contract(available_nodes, loader_class)
    required_loader_inputs = {"model", "lora_name", "strength_model"}
    if not required_loader_inputs.issubset(loader_inputs):
        return {"ok": False, "reason": "Pose Transfer requires Comfy's LoraLoaderModelOnly node with model, lora_name, and strength_model inputs.", "notes": notes}
    if not lora_choices:
        return {"ok": False, "reason": "The active Comfy LoraLoaderModelOnly node did not publish its LoRA catalog, so the AnyPose LoRAs cannot be verified safely.", "notes": notes}

    base_requested = str(unit.get("pose_base_lora") or "").strip()
    helper_requested = str(unit.get("pose_helper_lora") or "").strip()
    if not base_requested or not helper_requested:
        return {"ok": False, "reason": "Pose Transfer needs both the AnyPose base LoRA and helper LoRA selected.", "notes": notes}
    base_binding = resolve_exact_provider_catalog_name(base_requested, lora_choices)
    helper_binding = resolve_exact_provider_catalog_name(helper_requested, lora_choices)
    if not str(base_binding.get("status") or "").startswith("resolved"):
        return {"ok": False, "reason": f"The selected base pose LoRA '{base_requested}' is not present in the active Comfy LoRA catalog.", "notes": notes, "catalog_binding": base_binding}
    if not str(helper_binding.get("status") or "").startswith("resolved"):
        return {"ok": False, "reason": f"The selected helper pose LoRA '{helper_requested}' is not present in the active Comfy LoRA catalog.", "notes": notes, "catalog_binding": helper_binding}

    positive_encoder_id = _conditioning_source_node(graph, sampler_inputs.get("positive"))
    negative_encoder_id = _conditioning_source_node(graph, sampler_inputs.get("negative"))
    if not positive_encoder_id or not negative_encoder_id:
        return {"ok": False, "reason": "Pose Transfer could not find the active Qwen Image Edit conditioning nodes for this workflow.", "notes": notes}
    positive_node = graph.get(positive_encoder_id) or {}
    negative_node = graph.get(negative_encoder_id) or {}
    positive_inputs = positive_node.get("inputs") if isinstance(positive_node.get("inputs"), dict) else {}
    negative_inputs = negative_node.get("inputs") if isinstance(negative_node.get("inputs"), dict) else {}
    image2_ref = positive_inputs.get("image2") or negative_inputs.get("image2")
    if not isinstance(image2_ref, (list, tuple)) or len(image2_ref) < 2:
        return {"ok": False, "reason": "Pose Transfer uses Image 2 as the pose reference. Add Image 2 before generating.", "notes": notes}
    for node_id, inputs in ((positive_encoder_id, positive_inputs), (negative_encoder_id, negative_inputs)):
        existing = inputs.get("image3")
        if existing not in (None, "", []):
            return {"ok": False, "reason": "Image 3 is reserved for the generated DWPose map while Pose Transfer is enabled. Clear the current Image 3 source first.", "notes": notes, "conflict_node": node_id}

    model_anchor_id, model_upstream_ref = _model_sampling_anchor(graph, sampler_inputs)
    if not model_upstream_ref or not str(model_upstream_ref[0] or ""):
        return {"ok": False, "reason": "Pose Transfer could not locate the active Qwen model input before sampling.", "notes": notes}

    try:
        next_id = int(str(next_node_id)) if next_node_id is not None else None
    except (TypeError, ValueError):
        next_id = None
    pose_node_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(pose_node_id) + 1
    except (TypeError, ValueError):
        next_id = None
    preprocessor_request = {
        "openpose_body": bool(unit.get("openpose_body", True)),
        "openpose_hand": bool(unit.get("openpose_hand", False)),
        "openpose_face": bool(unit.get("openpose_face", False)),
        "settings": {"detect_resolution": int(unit.get("detect_resolution") or 512)},
    }
    graph[pose_node_id] = {
        "class_type": dwpose_node,
        "inputs": build_preprocessor_inputs(
            dwpose_node,
            dict(available_nodes),
            "dwpose",
            preprocessor_request,
            int(unit.get("detect_resolution") or 512),
            image_ref=_copy_ref(image2_ref, ["", 0]),
        ),
    }
    pose_ref = [pose_node_id, 0]
    positive_inputs["image3"] = deepcopy(pose_ref)
    negative_inputs["image3"] = deepcopy(pose_ref)
    instruction = str(unit.get("pose_prompt_instruction") or "").strip()
    _append_pose_instruction(positive_inputs, instruction)

    base_lora_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(base_lora_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[base_lora_id] = {
        "class_type": loader_class,
        "inputs": {
            "model": deepcopy(model_upstream_ref),
            "lora_name": str(base_binding.get("provider_catalog_name") or base_requested),
            "strength_model": float(unit.get("pose_base_strength") or 0.70),
        },
    }
    helper_lora_id = _next_graph_id(graph, next_id)
    graph[helper_lora_id] = {
        "class_type": loader_class,
        "inputs": {
            "model": [base_lora_id, 0],
            "lora_name": str(helper_binding.get("provider_catalog_name") or helper_requested),
            "strength_model": float(unit.get("pose_helper_strength") or 0.70),
        },
    }
    if model_anchor_id:
        anchor_inputs = graph[model_anchor_id].get("inputs") if isinstance(graph[model_anchor_id].get("inputs"), dict) else {}
        anchor_inputs["model"] = [helper_lora_id, 0]
        graph[model_anchor_id]["inputs"] = anchor_inputs
        model_patch_target = {"node_id": model_anchor_id, "class_type": graph[model_anchor_id].get("class_type"), "input": "model"}
    else:
        sampler_inputs["model"] = [helper_lora_id, 0]
        graph[sampler_key]["inputs"] = sampler_inputs
        model_patch_target = {"node_id": sampler_key, "class_type": graph[sampler_key].get("class_type"), "input": "model"}

    notes.append({"level": "info", "field": "pose_transfer", "message": "Pose Transfer uses Image 2 as the DWPose reference and feeds the generated pose map into Qwen Image 3."})
    return {
        "ok": True,
        "reason": "patched",
        "notes": notes,
        "created_node_ids": [pose_node_id, base_lora_id, helper_lora_id],
        "pose_node_id": pose_node_id,
        "pose_node_class": dwpose_node,
        "pose_reference_ref": _copy_ref(image2_ref, ["", 0]),
        "pose_map_ref": pose_ref,
        "positive_encoder_id": positive_encoder_id,
        "negative_encoder_id": negative_encoder_id,
        "base_lora_node_id": base_lora_id,
        "helper_lora_node_id": helper_lora_id,
        "base_lora": str(base_binding.get("provider_catalog_name") or base_requested),
        "helper_lora": str(helper_binding.get("provider_catalog_name") or helper_requested),
        "base_lora_binding": base_binding,
        "helper_lora_binding": helper_binding,
        "model_patch_target": model_patch_target,
        "model_anchor_id": model_anchor_id,
        "prompt_instruction": instruction,
    }

def _resolve_krea2_vae_ref(graph: dict[str, Any]) -> tuple[list[Any] | None, str]:
    preferred = {"VAELoader", "VAEUtils_CustomVAELoader"}
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") in preferred:
            return [str(node_id), 0], str(node.get("class_type") or "")
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if "vae_name" in inputs and "decode" not in class_type.lower() and "encode" not in class_type.lower():
            return [str(node_id), 0], class_type
    return None, "missing_krea2_vae_loader"


def _resolve_krea2_clip_ref(graph: dict[str, Any]) -> tuple[list[Any] | None, str]:
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        if str(node.get("class_type") or "") == "CLIPLoader":
            return [str(node_id), 0], "CLIPLoader"
    return None, "missing_krea2_clip_loader"


def _krea2_conditioning_text(graph: dict[str, Any], ref: Any) -> tuple[str, str]:
    """Recover the route-owned prompt text without inheriting ConditioningZeroOut text.

    Krea 2 Turbo uses ConditioningZeroOut for the negative branch. Ostris needs its
    own encoder on that branch, so a zeroed negative becomes an empty Ostris prompt
    rather than reusing the positive prompt hidden upstream of ConditioningZeroOut.
    """
    current = _copy_ref(ref, ["", 0])
    seen: set[str] = set()
    for _ in range(12):
        node_id = str(current[0] or "")
        if not node_id or node_id in seen:
            break
        seen.add(node_id)
        node = graph.get(node_id)
        if not isinstance(node, dict):
            break
        class_type = str(node.get("class_type") or "")
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        if class_type == "ConditioningZeroOut":
            return "", class_type
        if class_type == "CLIPTextEncode":
            return str(inputs.get("text") or ""), class_type
        upstream = inputs.get("conditioning")
        if not isinstance(upstream, (list, tuple)) or len(upstream) < 2:
            break
        current = _copy_ref(upstream, ["", 0])
    return "", "unresolved"


def _krea2_control_encode_policy(unit: dict[str, Any]) -> dict[str, Any]:
    control_type = str(unit.get("unit") or unit.get("preprocessor") or "auto").strip().lower()
    preprocessor = str(unit.get("preprocessor") or control_type or "none").strip().lower()
    depth_like = control_type == "depth" or preprocessor == "depth"
    return {
        "control_type": control_type or "auto",
        "preprocessor": preprocessor or "none",
        "resize": "match_latent_size",
        "upscale_method": "lanczos",
        "crop": "center",
        "channel_mode": "grayscale" if depth_like else "rgb",
        "normalize": "per_image_minmax" if depth_like else "none",
        # Map inversion is already owned by Neo's map-generation stage. Avoid
        # inverting a generated map twice inside Krea2 Control Image Encode.
        "invert": False,
        "batch_mode": "independent_images",
    }


def _apply_krea2_control_lora_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    status: dict[str, Any],
    available_nodes: Any = None,
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    if len(units) != 1:
        return {"ok": False, "reason": "Krea 2 Control Phase 1 requires exactly one active control unit.", "notes": [{"level": "error", "field": "inputs.units", "message": "Enable exactly one Krea 2 control unit for Generate.", "max_units": 1}]}
    unit = units[0]
    model_name = str(unit.get("model") or "").strip()
    if not model_name:
        return {"ok": False, "reason": "Krea 2 Control LoRA is not selected.", "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Select a Krea 2 Control LoRA from ComfyUI/models/loras."}]}
    krea_status = status.get("krea2_control") if isinstance(status.get("krea2_control"), dict) else {}
    if status.get("object_info_present") and not krea_status.get("available"):
        return {"ok": False, "reason": "Krea 2 Control nodes are missing.", "notes": [{"level": "error", "field": "nodes.krea2_control", "message": "Krea2ControlLoRALoader, Krea2ControlImageEncode, and Krea2ControlApply must all be installed.", "missing": list(krea_status.get("missing") or [])}]}

    # Phase 2.1: the UI/catalog stores portable slash-separated LoRA identities,
    # but Comfy validates combo inputs against the *exact* live object_info value.
    # On Windows that value commonly contains backslashes. Rebind the portable
    # selection to the exact provider enum before graph compilation; never send
    # the normalized UI spelling directly into Krea2ControlLoRALoader.lora_name.
    intent = control_intent_from_unit(unit if isinstance(unit, dict) else {})
    model_compatibility = validate_model_selection_for_route(
        model_name,
        family=route_data.get("family"),
        loader=route_data.get("loader"),
        mode=route_data.get("workflow_mode"),
        intent=intent,
        task=((block.get("params") or {}) if isinstance(block.get("params"), dict) else {}).get("controlnet_task") or TASK_MAP_CONTROL,
        backend=route_data.get("backend"),
        node_status=status,
    )
    if status.get("object_info_present") and model_compatibility.get("status") == "incompatible":
        return {
            "ok": False,
            "reason": "The selected Krea 2 Control LoRA is incompatible with the active control intent.",
            "notes": [{
                "level": "error",
                "field": "inputs.units[0].model",
                "message": "Workflow patching rejected a LoRA outside the Phase 3 intent-bound Control LoRA catalog.",
                "intent": intent,
                "model": model_name,
                "compatibility_status": model_compatibility.get("status"),
                "compatible_models": list(model_compatibility.get("compatible_models") or []),
            }],
            "model_compatibility": model_compatibility,
        }

    loader_class = str(krea_status.get("loader_node") or "Krea2ControlLoRALoader")
    provider_model_name = model_name
    catalog_binding: dict[str, Any] | None = None
    _loader_inputs, provider_lora_choices = _object_info_node_contract(available_nodes, loader_class)
    if provider_lora_choices:
        catalog_binding = resolve_exact_provider_catalog_name(model_name, provider_lora_choices)
        if not str(catalog_binding.get("status") or "").startswith("resolved"):
            return {
                "ok": False,
                "reason": f"The selected Krea 2 Control LoRA '{model_name}' is not present in the active Comfy loader catalog.",
                "notes": [{
                    "level": "error",
                    "field": "inputs.units[0].model",
                    "message": "Refresh Nodes and choose a Krea 2 Control LoRA published by the active Krea2ControlLoRALoader.",
                    "catalog_binding": deepcopy(catalog_binding),
                }],
                "catalog_binding": catalog_binding,
            }
        provider_model_name = str(catalog_binding.get("provider_catalog_name") or model_name)
    elif status.get("object_info_present"):
        return {
            "ok": False,
            "reason": "The active Krea 2 Control LoRA loader did not publish a live lora_name catalog.",
            "notes": [{
                "level": "error",
                "field": "nodes.krea2_control.lora_name",
                "message": "Refresh Nodes after restarting ComfyUI. Neo will not guess a provider enum for Krea2ControlLoRALoader.lora_name.",
            }],
        }

    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    image_name = _asset_to_image_name(_asset_for_unit(assets, str(unit.get("uid") or "unit_1")))
    if not image_name:
        return {"ok": False, "reason": "Krea 2 Control needs a control image or generated map.", "notes": [{"level": "error", "field": "assets.generated_maps", "message": "Build a control map or supply a control image before generation."}]}
    model_ref = _copy_ref(sampler_inputs.get("model"), ["1", 0])
    latent_ref = _copy_ref(sampler_inputs.get("latent_image"), ["6", 0])
    vae_ref, vae_source = _resolve_krea2_vae_ref(graph)
    if vae_ref is None:
        return {"ok": False, "reason": "Krea 2 Control could not find the active Krea2/Qwen VAE.", "notes": [{"level": "error", "field": "workflow.vae", "message": "Krea2ControlImageEncode must use the same Krea2/Qwen image VAE as the base workflow."}]}

    next_id = None
    if next_node_id is not None:
        try:
            next_id = int(str(next_node_id))
        except (TypeError, ValueError):
            next_id = None
    created: list[str] = []
    load_image_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(load_image_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[load_image_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    created.append(load_image_id)

    policy = _krea2_control_encode_policy(unit)
    encode_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(encode_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[encode_id] = {
        "class_type": str(krea_status.get("image_encode_node") or "Krea2ControlImageEncode"),
        "inputs": {
            "control_image": [load_image_id, 0],
            "vae": list(vae_ref),
            "resize": policy["resize"],
            "upscale_method": policy["upscale_method"],
            "crop": policy["crop"],
            "channel_mode": policy["channel_mode"],
            "normalize": policy["normalize"],
            "invert": policy["invert"],
            "batch_mode": policy["batch_mode"],
            "latent": list(latent_ref),
        },
    }
    created.append(encode_id)

    loader_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(loader_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[loader_id] = {
        "class_type": str(krea_status.get("loader_node") or "Krea2ControlLoRALoader"),
        "inputs": {"model": list(model_ref), "lora_name": provider_model_name, "strength": float(unit.get("strength") or 0.45)},
    }
    created.append(loader_id)

    apply_id = _next_graph_id(graph, next_id)
    graph[apply_id] = {
        "class_type": str(krea_status.get("apply_node") or "Krea2ControlApply"),
        "inputs": {"model": [loader_id, 0], "control_latent": [encode_id, 0]},
    }
    created.append(apply_id)
    graph[sampler_key]["inputs"]["model"] = [apply_id, 0]

    applied = deepcopy(unit)
    applied.update({
        "adapter": "krea2_control_lora",
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": float(unit.get("strength") or 0.45),
        "encode_policy": deepcopy(policy),
        "vae_source": vae_source,
        "control_image_source": image_name,
        "start_end_policy": "not_exposed_by_krea2_control_lora",
    })
    return {
        "ok": True,
        "adapter": "krea2_control_lora",
        "created_node_ids": created,
        "applied_units": [applied],
        "patched_model_ref": [apply_id, 0],
        "control_image_source": image_name,
        "encode_policy": policy,
        "vae_source": vae_source,
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": float(unit.get("strength") or 0.45),
        "notes": [{"level": "info", "field": "workflow.krea2_control", "message": "Krea 2 Control LoRA patched the sampler model after Phase 3 intent compatibility validation; the portable UI LoRA identity was rebound to Comfy's exact live loader value before queueing."}],
    }



def _apply_krea2_control_plus_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    status: dict[str, Any],
    available_nodes: Any = None,
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    """Apply the Krea2 Control Plus adapter for Composition / Silhouette.

    This route intentionally remains separate from the physically validated
    native Depth adapter. Control Plus adds start/end scheduling and consumes
    the same Neo control-image/generated-map asset boundary.
    """
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    if len(units) != 1:
        return {"ok": False, "reason": "Krea 2 Control Plus requires exactly one active control unit.", "notes": [{"level": "error", "field": "inputs.units", "message": "Enable exactly one Krea 2 Composition / Silhouette unit for Generate.", "max_units": 1}]}
    unit = units[0]
    intent = control_intent_from_unit(unit if isinstance(unit, dict) else {})
    if intent != "composition_silhouette":
        return {"ok": False, "reason": "Krea 2 Control Plus is reserved for Composition / Silhouette in this phase.", "notes": [{"level": "error", "field": "inputs.units[0].unit", "message": "Select Composition / Silhouette for the Control Plus adapter.", "intent": intent}]}

    model_name = str(unit.get("model") or "").strip()
    if not model_name:
        return {"ok": False, "reason": "Krea 2 Composition Control LoRA is not selected.", "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Select a compatible Krea 2 Composition / Silhouette Control LoRA from ComfyUI/models/loras."}]}

    plus_status = status.get("krea2_control_plus") if isinstance(status.get("krea2_control_plus"), dict) else {}
    if status.get("object_info_present") and not plus_status.get("available"):
        return {"ok": False, "reason": "Krea 2 Control Plus nodes are missing.", "notes": [{"level": "error", "field": "nodes.krea2_control_plus", "message": "Krea2ControlPlusLoRALoader, Krea2ControlPlusImageEncode, and Krea2ControlPlusApply must all be installed.", "missing": list(plus_status.get("missing") or [])}]}

    model_compatibility = validate_model_selection_for_route(
        model_name,
        family=route_data.get("family"),
        loader=route_data.get("loader"),
        mode=route_data.get("workflow_mode"),
        intent=intent,
        task=((block.get("params") or {}) if isinstance(block.get("params"), dict) else {}).get("controlnet_task") or TASK_MAP_CONTROL,
        backend=route_data.get("backend"),
        node_status=status,
    )
    if status.get("object_info_present") and not model_compatibility.get("valid"):
        return {
            "ok": False,
            "reason": "The selected Krea 2 Control Plus LoRA is incompatible with Composition / Silhouette.",
            "notes": [{
                "level": "error",
                "field": "inputs.units[0].model",
                "message": "Workflow patching rejected a LoRA outside the Composition / Silhouette intent-bound Control Plus catalog.",
                "intent": intent,
                "model": model_name,
                "compatibility_status": model_compatibility.get("status"),
                "compatible_models": list(model_compatibility.get("compatible_models") or []),
            }],
            "model_compatibility": model_compatibility,
        }

    loader_class = str(plus_status.get("loader_node") or "Krea2ControlPlusLoRALoader")
    provider_model_name = model_name
    catalog_binding: dict[str, Any] | None = None
    _loader_inputs, provider_lora_choices = _object_info_node_contract(available_nodes, loader_class)
    if provider_lora_choices:
        catalog_binding = resolve_exact_provider_catalog_name(model_name, provider_lora_choices)
        if not str(catalog_binding.get("status") or "").startswith("resolved"):
            return {
                "ok": False,
                "reason": f"The selected Krea 2 Composition LoRA '{model_name}' is not present in the active Control Plus catalog.",
                "notes": [{
                    "level": "error",
                    "field": "inputs.units[0].model",
                    "message": "Refresh Nodes and choose a Composition / Silhouette LoRA published by Krea2ControlPlusLoRALoader.",
                    "catalog_binding": deepcopy(catalog_binding),
                }],
                "catalog_binding": catalog_binding,
            }
        provider_model_name = str(catalog_binding.get("provider_catalog_name") or model_name)
    elif status.get("object_info_present"):
        return {
            "ok": False,
            "reason": "The active Krea 2 Control Plus loader did not publish a live lora_name catalog.",
            "notes": [{
                "level": "error",
                "field": "nodes.krea2_control_plus.lora_name",
                "message": "Refresh Nodes after restarting ComfyUI. Neo will not guess a provider enum for Krea2ControlPlusLoRALoader.lora_name.",
            }],
        }

    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    image_name = _asset_to_image_name(_asset_for_unit(assets, str(unit.get("uid") or "unit_1")))
    if not image_name:
        return {"ok": False, "reason": "Krea 2 Composition control needs a control image or generated map.", "notes": [{"level": "error", "field": "assets.generated_maps", "message": "Supply a composition reference image directly or build an identity map before generation."}]}

    model_ref = _copy_ref(sampler_inputs.get("model"), ["1", 0])
    latent_ref = _copy_ref(sampler_inputs.get("latent_image"), ["6", 0])
    vae_ref, vae_source = _resolve_krea2_vae_ref(graph)
    if vae_ref is None:
        return {"ok": False, "reason": "Krea 2 Control Plus could not find the active Krea2/Qwen VAE.", "notes": [{"level": "error", "field": "workflow.vae", "message": "Krea2ControlPlusImageEncode must use the same Krea2/Qwen image VAE as the base workflow."}]}

    start_percent = max(0.0, min(1.0, float(unit.get("start_percent", 0.0) or 0.0)))
    end_percent = max(0.0, min(1.0, float(unit.get("end_percent", 1.0) or 1.0)))
    if end_percent < start_percent:
        end_percent = 1.0
    strength = float(unit.get("strength") or 0.45)

    next_id = None
    if next_node_id is not None:
        try:
            next_id = int(str(next_node_id))
        except (TypeError, ValueError):
            next_id = None
    created: list[str] = []
    load_image_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(load_image_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[load_image_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    created.append(load_image_id)

    policy = _krea2_control_encode_policy(unit)
    # The public composition/silhouette model is trained on RGB controls without
    # depth-style normalization or inversion.
    policy.update({"channel_mode": "rgb", "normalize": "none", "invert": False})
    encode_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(encode_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[encode_id] = {
        "class_type": str(plus_status.get("image_encode_node") or "Krea2ControlPlusImageEncode"),
        "inputs": {
            "control_image": [load_image_id, 0],
            "vae": list(vae_ref),
            "resize": policy["resize"],
            "upscale_method": policy["upscale_method"],
            "crop": policy["crop"],
            "channel_mode": policy["channel_mode"],
            "normalize": policy["normalize"],
            "invert": policy["invert"],
            "batch_mode": policy["batch_mode"],
            "latent": list(latent_ref),
        },
    }
    created.append(encode_id)

    loader_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(loader_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[loader_id] = {
        "class_type": loader_class,
        "inputs": {
            "model": list(model_ref),
            "lora_name": provider_model_name,
            "strength": strength,
            "start_percent": start_percent,
            "end_percent": end_percent,
        },
    }
    created.append(loader_id)

    apply_id = _next_graph_id(graph, next_id)
    graph[apply_id] = {
        "class_type": str(plus_status.get("apply_node") or "Krea2ControlPlusApply"),
        "inputs": {"model": [loader_id, 0], "control_latent": [encode_id, 0]},
    }
    created.append(apply_id)
    graph[sampler_key]["inputs"]["model"] = [apply_id, 0]

    applied = deepcopy(unit)
    applied.update({
        "adapter": "krea2_control_plus",
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": strength,
        "start_percent": start_percent,
        "end_percent": end_percent,
        "encode_policy": deepcopy(policy),
        "vae_source": vae_source,
        "control_image_source": image_name,
        "start_end_policy": "explicit_control_plus_range",
    })
    return {
        "ok": True,
        "adapter": "krea2_control_plus",
        "created_node_ids": created,
        "applied_units": [applied],
        "patched_model_ref": [apply_id, 0],
        "control_image_source": image_name,
        "encode_policy": policy,
        "vae_source": vae_source,
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": strength,
        "start_percent": start_percent,
        "end_percent": end_percent,
        "notes": [{"level": "info", "field": "workflow.krea2_control_plus", "message": "Krea 2 Composition / Silhouette patched the sampler model through Krea2 Control Plus with explicit start/end scheduling and exact live LoRA catalog rebinding."}],
    }

def _apply_krea2_ostris_openpose_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    status: dict[str, Any],
    available_nodes: Any = None,
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    """Apply Krea 2 Turbo OpenPose through the Ostris reference-edit adapter."""
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    if len(units) != 1:
        return {"ok": False, "reason": "Krea 2 Ostris OpenPose requires exactly one active control unit.", "notes": [{"level": "error", "field": "inputs.units", "message": "Enable exactly one Krea 2 Turbo OpenPose unit for Generate.", "max_units": 1}]}
    unit = units[0]
    intent = control_intent_from_unit(unit if isinstance(unit, dict) else {})
    if intent != "openpose":
        return {"ok": False, "reason": "The Ostris adapter is reserved for OpenPose in Phase 5.", "notes": [{"level": "error", "field": "inputs.units[0].unit", "message": "Select OpenPose for the Krea 2 Ostris adapter.", "intent": intent}]}
    if str(route_data.get("family") or "").strip().lower() != "krea2_turbo":
        return {"ok": False, "reason": "Krea 2 OpenPose is currently qualified only for Krea 2 Turbo.", "notes": [{"level": "error", "field": "route.family", "message": "The verified public OpenPose Control LoRA targets Krea 2 Turbo; RAW remains hidden until a compatible pose LoRA is verified."}]}

    model_name = str(unit.get("model") or "").strip()
    if not model_name:
        return {"ok": False, "reason": "Krea 2 Turbo OpenPose Control LoRA is not selected.", "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Select a compatible Krea 2 Turbo OpenPose Control LoRA from ComfyUI/models/loras."}]}

    ostris_status = status.get("krea2_ostris") if isinstance(status.get("krea2_ostris"), dict) else {}
    if status.get("object_info_present") and not ostris_status.get("available"):
        return {"ok": False, "reason": "Krea 2 Ostris OpenPose nodes are missing.", "notes": [{"level": "error", "field": "nodes.krea2_ostris", "message": "TextEncodeKrea2OstrisEdit, Krea2OstrisEditModelPatch, and LoraLoaderModelOnly must all be installed.", "missing": list(ostris_status.get("missing") or [])}]}
    if status.get("object_info_present") and not isinstance(available_nodes, Mapping):
        return {"ok": False, "reason": "Krea 2 Ostris OpenPose needs live Comfy object_info for safe node-contract binding.", "notes": [{"level": "error", "field": "nodes.object_info", "message": "Refresh Nodes so Neo can verify Ostris and LoraLoaderModelOnly inputs."}]}

    text_class = str(ostris_status.get("text_encode_node") or "TextEncodeKrea2OstrisEdit")
    patch_class = str(ostris_status.get("model_patch_node") or "Krea2OstrisEditModelPatch")
    loader_class = str(ostris_status.get("lora_loader_node") or "LoraLoaderModelOnly")
    if isinstance(available_nodes, Mapping):
        text_inputs, _ = _object_info_node_contract(available_nodes, text_class)
        patch_inputs, _ = _object_info_node_contract(available_nodes, patch_class)
        loader_inputs, provider_lora_choices = _object_info_node_contract(available_nodes, loader_class)
        if not {"clip", "prompt", "vae", "image1"}.issubset(text_inputs):
            return {"ok": False, "reason": "Installed TextEncodeKrea2OstrisEdit contract is incompatible with Phase 5.", "notes": [{"level": "error", "field": "nodes.TextEncodeKrea2OstrisEdit", "message": "Neo requires clip, prompt, vae, and image1 inputs for OpenPose reference conditioning."}]}
        if not {"model", "kv_cache"}.issubset(patch_inputs):
            return {"ok": False, "reason": "Installed Krea2OstrisEditModelPatch contract is incompatible with Phase 5.", "notes": [{"level": "error", "field": "nodes.Krea2OstrisEditModelPatch", "message": "Neo requires model and kv_cache inputs."}]}
        if not {"model", "lora_name", "strength_model"}.issubset(loader_inputs):
            return {"ok": False, "reason": "Installed LoraLoaderModelOnly contract is incompatible with Phase 5.", "notes": [{"level": "error", "field": "nodes.LoraLoaderModelOnly", "message": "Neo requires model, lora_name, and strength_model inputs."}]}
    else:
        provider_lora_choices = []

    model_compatibility = validate_model_selection_for_route(
        model_name,
        family=route_data.get("family"),
        loader=route_data.get("loader"),
        mode=route_data.get("workflow_mode"),
        intent=intent,
        task=((block.get("params") or {}) if isinstance(block.get("params"), dict) else {}).get("controlnet_task") or TASK_MAP_CONTROL,
        backend=route_data.get("backend"),
        node_status=status,
    )
    if status.get("object_info_present") and not model_compatibility.get("valid"):
        return {
            "ok": False,
            "reason": "The selected LoRA is incompatible with Krea 2 Turbo OpenPose.",
            "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Choose an OpenPose Control LoRA from Neo's Ostris intent-filtered catalog.", "model": model_name, "compatible_models": list(model_compatibility.get("compatible_models") or [])}],
            "model_compatibility": model_compatibility,
        }

    provider_model_name = model_name
    catalog_binding: dict[str, Any] | None = None
    if provider_lora_choices:
        catalog_binding = resolve_exact_provider_catalog_name(model_name, provider_lora_choices)
        if not str(catalog_binding.get("status") or "").startswith("resolved"):
            return {"ok": False, "reason": f"The selected Krea 2 Turbo OpenPose LoRA '{model_name}' is not present in LoraLoaderModelOnly's live catalog.", "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Refresh Nodes and choose an installed OpenPose Control LoRA.", "catalog_binding": deepcopy(catalog_binding)}], "catalog_binding": catalog_binding}
        provider_model_name = str(catalog_binding.get("provider_catalog_name") or model_name)
    elif status.get("object_info_present"):
        return {"ok": False, "reason": "LoraLoaderModelOnly did not publish a live lora_name catalog.", "notes": [{"level": "error", "field": "nodes.LoraLoaderModelOnly.lora_name", "message": "Refresh Nodes after restarting ComfyUI. Neo will not guess the provider enum."}]}

    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    image_name = _asset_to_image_name(_asset_for_unit(assets, str(unit.get("uid") or "unit_1")))
    if not image_name:
        return {"ok": False, "reason": "Krea 2 Turbo OpenPose needs a pose map.", "notes": [{"level": "error", "field": "assets.generated_maps", "message": "Build a DWPose/OpenPose map from the control image or supply a pose map directly before generation."}]}

    model_ref = _copy_ref(sampler_inputs.get("model"), ["1", 0])
    clip_ref, clip_source = _resolve_krea2_clip_ref(graph)
    vae_ref, vae_source = _resolve_krea2_vae_ref(graph)
    if clip_ref is None:
        return {"ok": False, "reason": "Krea 2 Ostris OpenPose could not find the active Krea2 Qwen3-VL CLIPLoader.", "notes": [{"level": "error", "field": "workflow.clip", "message": "TextEncodeKrea2OstrisEdit must reuse the base Krea 2 CLIPLoader(type=krea2)."}]}
    if vae_ref is None:
        return {"ok": False, "reason": "Krea 2 Ostris OpenPose could not find the active Krea2/Qwen VAE.", "notes": [{"level": "error", "field": "workflow.vae", "message": "TextEncodeKrea2OstrisEdit must reuse the active Krea2/Qwen VAE for reference latents."}]}

    positive_prompt, positive_source = _krea2_conditioning_text(graph, sampler_inputs.get("positive"))
    negative_prompt, negative_source = _krea2_conditioning_text(graph, sampler_inputs.get("negative"))
    strength = float(unit.get("strength") or 0.85)
    kv_cache = bool(unit.get("ostris_kv_cache", True))

    try:
        next_id = int(str(next_node_id)) if next_node_id is not None else None
    except (TypeError, ValueError):
        next_id = None
    created: list[str] = []

    load_image_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(load_image_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[load_image_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    created.append(load_image_id)

    positive_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(positive_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[positive_id] = {
        "class_type": text_class,
        "inputs": {"clip": list(clip_ref), "prompt": positive_prompt, "vae": list(vae_ref), "image1": [load_image_id, 0]},
    }
    created.append(positive_id)

    negative_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(negative_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[negative_id] = {
        "class_type": text_class,
        "inputs": {"clip": list(clip_ref), "prompt": negative_prompt, "vae": list(vae_ref), "image1": [load_image_id, 0]},
    }
    created.append(negative_id)

    model_patch_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(model_patch_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[model_patch_id] = {"class_type": patch_class, "inputs": {"model": list(model_ref), "kv_cache": kv_cache}}
    created.append(model_patch_id)

    lora_id = _next_graph_id(graph, next_id)
    graph[lora_id] = {
        "class_type": loader_class,
        "inputs": {"model": [model_patch_id, 0], "lora_name": provider_model_name, "strength_model": strength},
    }
    created.append(lora_id)

    graph[sampler_key]["inputs"]["model"] = [lora_id, 0]
    graph[sampler_key]["inputs"]["positive"] = [positive_id, 0]
    graph[sampler_key]["inputs"]["negative"] = [negative_id, 0]

    applied = deepcopy(unit)
    applied.update({
        "adapter": "krea2_ostris_openpose",
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": strength,
        "ostris_kv_cache": kv_cache,
        "control_image_source": image_name,
        "clip_source": clip_source,
        "vae_source": vae_source,
        "positive_prompt_source": positive_source,
        "negative_prompt_source": negative_source,
    })
    return {
        "ok": True,
        "adapter": "krea2_ostris_openpose",
        "created_node_ids": created,
        "applied_units": [applied],
        "patched_model_ref": [lora_id, 0],
        "patched_positive_ref": [positive_id, 0],
        "patched_negative_ref": [negative_id, 0],
        "control_image_source": image_name,
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": strength,
        "ostris_kv_cache": kv_cache,
        "clip_source": clip_source,
        "vae_source": vae_source,
        "conditioning_policy": "ostris_reference_image_conditioning",
        "notes": [{"level": "info", "field": "workflow.krea2_ostris_openpose", "message": "Krea 2 Turbo OpenPose replaced base Krea conditioning with Ostris reference-image conditioning, patched the model through Krea2OstrisEditModelPatch, then applied the intent-bound model-only OpenPose LoRA."}],
    }


def _apply_krea2_nk2e_canny_patch(
    graph: dict[str, Any],
    *,
    block: dict[str, Any],
    route_data: dict[str, Any],
    status: dict[str, Any],
    available_nodes: Any = None,
    sampler_key: str,
    sampler_inputs: dict[str, Any],
    next_node_id: int | str | None = None,
) -> dict[str, Any]:
    """Apply Krea 2 RAW Canny through the NK2E in-context reference adapter.

    The current NK2E preferred graph is model-only LoRA -> NK2EInContextModelNode
    on the sampler model, with a VAE-encoded edge map injected through
    NK2ESetReferenceNode on positive conditioning. The base Krea negative lane is
    preserved. This intentionally does not use the deprecated one-node
    NK2EInContextEditNode shortcut.
    """
    units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    if len(units) != 1:
        return {"ok": False, "reason": "Krea 2 NK2E Canny requires exactly one active control unit.", "notes": [{"level": "error", "field": "inputs.units", "message": "Enable exactly one Krea 2 RAW Canny unit for Generate.", "max_units": 1}]}
    unit = units[0]
    intent = control_intent_from_unit(unit if isinstance(unit, dict) else {})
    if intent != "canny":
        return {"ok": False, "reason": "The NK2E adapter is reserved for Canny in Phase 6.", "notes": [{"level": "error", "field": "inputs.units[0].unit", "message": "Select Canny for the Krea 2 NK2E adapter.", "intent": intent}]}
    if str(route_data.get("family") or "").strip().lower() != "krea2":
        return {"ok": False, "reason": "Krea 2 NK2E Canny is currently qualified only for Krea 2 RAW.", "notes": [{"level": "error", "field": "route.family", "message": "The published NK2E Canny repository declares krea/Krea-2-Raw as its base model; Turbo remains hidden until a compatible checkpoint is verified."}]}

    model_name = str(unit.get("model") or "").strip()
    if not model_name:
        return {"ok": False, "reason": "Krea 2 RAW NK2E Canny LoRA is not selected.", "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Select NK2E-canny-v0.1.safetensors or another compatible NK2E Canny LoRA from ComfyUI/models/loras."}]}

    nk2e_status = status.get("krea2_nk2e") if isinstance(status.get("krea2_nk2e"), dict) else {}
    if status.get("object_info_present") and not nk2e_status.get("available"):
        return {"ok": False, "reason": "Krea 2 NK2E Canny nodes are missing.", "notes": [{"level": "error", "field": "nodes.krea2_nk2e", "message": "NK2EInContextModelNode, NK2ESetReferenceNode, LoraLoaderModelOnly, and VAEEncode must all be available.", "missing": list(nk2e_status.get("missing") or [])}]}
    if status.get("object_info_present") and not isinstance(available_nodes, Mapping):
        return {"ok": False, "reason": "Krea 2 NK2E Canny needs live Comfy object_info for safe node-contract binding.", "notes": [{"level": "error", "field": "nodes.object_info", "message": "Refresh Nodes so Neo can verify NK2E and LoraLoaderModelOnly inputs."}]}

    model_class = str(nk2e_status.get("model_node") or "NK2EInContextModelNode")
    reference_class = str(nk2e_status.get("set_reference_node") or "NK2ESetReferenceNode")
    loader_class = str(nk2e_status.get("lora_loader_node") or "LoraLoaderModelOnly")
    vae_encode_class = str(nk2e_status.get("vae_encode_node") or "VAEEncode")
    if isinstance(available_nodes, Mapping):
        model_inputs, _ = _object_info_node_contract(available_nodes, model_class)
        reference_inputs, _ = _object_info_node_contract(available_nodes, reference_class)
        loader_inputs, provider_lora_choices = _object_info_node_contract(available_nodes, loader_class)
        vae_inputs, _ = _object_info_node_contract(available_nodes, vae_encode_class)
        if not {"model"}.issubset(model_inputs):
            return {"ok": False, "reason": "Installed NK2EInContextModelNode contract is incompatible with Phase 6.", "notes": [{"level": "error", "field": "nodes.NK2EInContextModelNode", "message": "Neo requires the NK2E model wrapper to accept model."}]}
        if not {"conditioning", "reference"}.issubset(reference_inputs):
            return {"ok": False, "reason": "Installed NK2ESetReferenceNode contract is incompatible with Phase 6.", "notes": [{"level": "error", "field": "nodes.NK2ESetReferenceNode", "message": "Neo requires conditioning and reference inputs for the NK2E reference handoff."}]}
        if not {"model", "lora_name", "strength_model"}.issubset(loader_inputs):
            return {"ok": False, "reason": "Installed LoraLoaderModelOnly contract is incompatible with Phase 6.", "notes": [{"level": "error", "field": "nodes.LoraLoaderModelOnly", "message": "Neo requires model, lora_name, and strength_model inputs."}]}
        if not {"pixels", "vae"}.issubset(vae_inputs):
            return {"ok": False, "reason": "Installed VAEEncode contract is incompatible with Phase 6.", "notes": [{"level": "error", "field": "nodes.VAEEncode", "message": "Neo requires pixels and vae inputs to encode the Canny reference map."}]}
    else:
        provider_lora_choices = []

    model_compatibility = validate_model_selection_for_route(
        model_name,
        family=route_data.get("family"),
        loader=route_data.get("loader"),
        mode=route_data.get("workflow_mode"),
        intent=intent,
        task=((block.get("params") or {}) if isinstance(block.get("params"), dict) else {}).get("controlnet_task") or TASK_MAP_CONTROL,
        backend=route_data.get("backend"),
        node_status=status,
    )
    if status.get("object_info_present") and not model_compatibility.get("valid"):
        return {
            "ok": False,
            "reason": "The selected LoRA is incompatible with Krea 2 RAW NK2E Canny.",
            "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Choose an NK2E Canny Control LoRA from Neo's intent-filtered catalog.", "model": model_name, "compatible_models": list(model_compatibility.get("compatible_models") or [])}],
            "model_compatibility": model_compatibility,
        }

    provider_model_name = model_name
    catalog_binding: dict[str, Any] | None = None
    if provider_lora_choices:
        catalog_binding = resolve_exact_provider_catalog_name(model_name, provider_lora_choices)
        if not str(catalog_binding.get("status") or "").startswith("resolved"):
            return {"ok": False, "reason": f"The selected NK2E Canny LoRA '{model_name}' is not present in LoraLoaderModelOnly's live catalog.", "notes": [{"level": "error", "field": "inputs.units[0].model", "message": "Refresh Nodes and choose an installed NK2E Canny LoRA.", "catalog_binding": deepcopy(catalog_binding)}], "catalog_binding": catalog_binding}
        provider_model_name = str(catalog_binding.get("provider_catalog_name") or model_name)
    elif status.get("object_info_present"):
        return {"ok": False, "reason": "LoraLoaderModelOnly did not publish a live lora_name catalog.", "notes": [{"level": "error", "field": "nodes.LoraLoaderModelOnly.lora_name", "message": "Refresh Nodes after restarting ComfyUI. Neo will not guess the provider enum."}]}

    assets = block.get("assets") if isinstance(block.get("assets"), dict) else {}
    image_name = _asset_to_image_name(_asset_for_unit(assets, str(unit.get("uid") or "unit_1")))
    if not image_name:
        return {"ok": False, "reason": "Krea 2 RAW NK2E Canny needs a Canny edge map.", "notes": [{"level": "error", "field": "assets.generated_maps", "message": "Build a Canny map from the control image or supply an existing edge map before generation."}]}

    model_ref = _copy_ref(sampler_inputs.get("model"), ["1", 0])
    positive_ref = _copy_ref(sampler_inputs.get("positive"), ["2", 0])
    negative_ref = _copy_ref(sampler_inputs.get("negative"), ["3", 0])
    vae_ref, vae_source = _resolve_krea2_vae_ref(graph)
    if vae_ref is None:
        return {"ok": False, "reason": "Krea 2 NK2E Canny could not find the active Krea2/Qwen VAE.", "notes": [{"level": "error", "field": "workflow.vae", "message": "VAEEncode must use the same active Krea2/Qwen image VAE as the base workflow."}]}

    strength = float(unit.get("strength") if unit.get("strength") is not None else 0.70)
    try:
        next_id = int(str(next_node_id)) if next_node_id is not None else None
    except (TypeError, ValueError):
        next_id = None
    created: list[str] = []

    load_image_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(load_image_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[load_image_id] = {"class_type": "LoadImage", "inputs": {"image": image_name}}
    created.append(load_image_id)

    vae_encode_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(vae_encode_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[vae_encode_id] = {
        "class_type": vae_encode_class,
        "inputs": {"pixels": [load_image_id, 0], "vae": list(vae_ref)},
    }
    created.append(vae_encode_id)

    set_reference_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(set_reference_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[set_reference_id] = {
        "class_type": reference_class,
        "inputs": {"conditioning": list(positive_ref), "reference": [vae_encode_id, 0]},
    }
    created.append(set_reference_id)

    lora_id = _next_graph_id(graph, next_id)
    try:
        next_id = int(lora_id) + 1
    except (TypeError, ValueError):
        next_id = None
    graph[lora_id] = {
        "class_type": loader_class,
        "inputs": {"model": list(model_ref), "lora_name": provider_model_name, "strength_model": strength},
    }
    created.append(lora_id)

    model_node_id = _next_graph_id(graph, next_id)
    graph[model_node_id] = {"class_type": model_class, "inputs": {"model": [lora_id, 0]}}
    created.append(model_node_id)

    graph[sampler_key]["inputs"]["model"] = [model_node_id, 0]
    graph[sampler_key]["inputs"]["positive"] = [set_reference_id, 0]
    graph[sampler_key]["inputs"]["negative"] = list(negative_ref)

    applied = deepcopy(unit)
    applied.update({
        "adapter": "krea2_nk2e_canny",
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": strength,
        "control_image_source": image_name,
        "vae_source": vae_source,
        "reference_policy": "canny_map_vae_latent_to_nk2e_set_reference",
    })
    return {
        "ok": True,
        "adapter": "krea2_nk2e_canny",
        "created_node_ids": created,
        "applied_units": [applied],
        "patched_model_ref": [model_node_id, 0],
        "patched_positive_ref": [set_reference_id, 0],
        "patched_negative_ref": list(negative_ref),
        "control_image_source": image_name,
        "control_lora": model_name,
        "provider_control_lora": provider_model_name,
        "catalog_binding": deepcopy(catalog_binding) if catalog_binding else None,
        "model_compatibility": deepcopy(model_compatibility),
        "control_strength": strength,
        "vae_source": vae_source,
        "conditioning_policy": "nk2e_in_context_reference_conditioning",
        "reference_policy": "positive_conditioning_sets_global_nk2e_reference",
        "notes": [{"level": "info", "field": "workflow.krea2_nk2e_canny", "message": "Krea 2 RAW Canny VAE-encoded the generated edge map, set it as the NK2E in-context reference on positive conditioning, applied the intent-bound Canny LoRA, and wrapped the sampler model through NK2EInContextModelNode."}],
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

    units_for_method = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
    pose_transfer_units = [unit for unit in units_for_method if str(unit.get("pose_method") or "controlnet") == POSE_TRANSFER_METHOD]
    normal_control_units = [unit for unit in units_for_method if str(unit.get("pose_method") or "controlnet") != POSE_TRANSFER_METHOD]
    capability_method = POSE_TRANSFER_METHOD if pose_transfer_units else ""
    # Preserve the established Pose Transfer product guardrails before the generic
    # family capability gate. Phase 2 must not replace clearer route/mixing errors
    # with a generic capability message.
    if pose_transfer_units:
        if controlnet_task != TASK_MAP_CONTROL:
            return no_patch("Pose Transfer is an Img2Img/Edit reference workflow and cannot be combined with Inpaint/Outpaint ControlNet tasks.", asset_resolution=asset_resolution)
        if len(pose_transfer_units) > 1:
            return no_patch("Use one Pose Transfer unit per generation. Multiple pose-transfer units are not supported in the same run.")
        if normal_control_units:
            return no_patch("Choose either Pose Transfer or ControlNet units for this run. Stacking both systems together is not enabled yet.")

    if str(route_data.get("backend") or "").strip().lower() != "forge":
        capability = resolve_route_capability(
            family=route_data.get("family"),
            loader=route_data.get("loader"),
            mode=route_data.get("workflow_mode"),
            task=controlnet_task,
            backend=route_data.get("backend"),
            method=capability_method,
        )
        if not capability.get("implemented") and not pose_transfer_units:
            return no_patch(
                "capability_gated: no implemented ControlNet adapter matches the active route",
                extra_notes=[{
                    "level": "error",
                    "field": "capability.route",
                    "message": "ControlNet workflow patching is blocked because the Phase 2 capability registry has no implemented adapter for this route.",
                    "family": route_data.get("family"),
                    "loader": route_data.get("loader"),
                    "workflow_mode": route_data.get("workflow_mode"),
                    "controlnet_task": controlnet_task,
                    "method": capability_method,
                }],
                asset_resolution=asset_resolution,
            )
        allowed_intents = set(capability.get("implemented_intents") or []) if capability.get("implemented") else set()
        if capability.get("implemented"):
            invalid = []
            for index, unit in enumerate(units_for_method):
                intent = control_intent_from_unit(unit if isinstance(unit, dict) else {})
                if not intent and controlnet_task != TASK_MAP_CONTROL:
                    continue
                if intent not in allowed_intents:
                    invalid.append({"index": index, "intent": intent or "(none)"})
            if invalid:
                return no_patch(
                    "capability_gated: selected control type is not implemented for the active route",
                    extra_notes=[{
                        "level": "error",
                        "field": "inputs.units",
                        "message": "ControlNet workflow patching rejected a unit outside the family-aware capability registry.",
                        "invalid_units": invalid,
                        "allowed": sorted(allowed_intents),
                    }],
                    asset_resolution=asset_resolution,
                )
            max_units = capability.get("max_active_units")
            if isinstance(max_units, int) and max_units > 0 and len(units_for_method) > max_units:
                return no_patch(
                    f"capability_gated: route supports at most {max_units} active ControlNet unit(s)",
                    extra_notes=[{
                        "level": "error",
                        "field": "inputs.units",
                        "message": "ControlNet workflow patching rejected too many active units for the resolved capability.",
                        "max_units": max_units,
                        "active_units": len(units_for_method),
                    }],
                    asset_resolution=asset_resolution,
                )
            route_data["capability"] = capability
    if pose_transfer_units:
        if not sampler or sampler.get("class_type") != "KSampler":
            return no_patch("Pose Transfer could not find the active KSampler node.", extra_notes=[{"level": "error", "field": "workflow.sampler", "message": "Pose Transfer requires the active KSampler model and conditioning inputs."}])
        pose_status = node_status or inspect_nodes(available_nodes)
        pose_result = _apply_qwen_pose_transfer_patch(
            graph,
            unit=pose_transfer_units[0],
            route_data=route_data,
            available_nodes=available_nodes,
            status=pose_status,
            sampler_key=sampler_key,
            sampler_inputs=sampler_inputs,
            next_node_id=next_node_id,
        )
        if not pose_result.get("ok"):
            return no_patch(str(pose_result.get("reason") or "Pose Transfer could not be applied."), status=pose_status, extra_notes=pose_result.get("notes") or [])
        route_data["pose_transfer_state"] = "experimental_available"
        route_data["pose_transfer_method"] = POSE_TRANSFER_METHOD
        patch = build_workflow_patch_summary(
            route=route_data,
            node_status=pose_status,
            applied_units=[deepcopy(pose_transfer_units[0])],
            node_ids=pose_result.get("created_node_ids") or [],
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            patched_positive_ref=previous_positive_ref,
            patched_negative_ref=previous_negative_ref,
            sampler_node_id=sampler_key,
            reason="patched",
            applied=True,
            controlnet_task=controlnet_task,
        )
        patch.update({
            "adapter": "qwen_2511_pose_transfer",
            "pose_transfer": {key: deepcopy(value) for key, value in pose_result.items() if key not in {"ok", "notes"}},
            "pose_reference_lane": 2,
            "pose_map_lane": 3,
        })
        return {
            "workflow": graph,
            "validation": {"ok": True, "enabled": True, "block": block, "validation": notes + list(pose_result.get("notes") or []), "route": route_data, "node_status": pose_status, "workflow_patch_allowed": True, "reason": "patched", "asset_resolution": {}},
            "workflow_patch": patch,
            "mutated": True,
            "changed": True,
            "extension_id": EXTENSION_ID,
            "phase": PHASE,
            "route_state": "experimental_available",
        }

    if task_state not in ACTIVE_STATES:
        return no_patch(task_route_reason(controlnet_task, task_state), asset_resolution=asset_resolution)

    if not sampler or sampler.get("class_type") != "KSampler":
        return no_patch("validation_failed: target KSampler node was not found", extra_notes=[{"level": "error", "field": "workflow.sampler", "message": "ControlNet patch requires a KSampler node with positive/negative inputs."}])

    status = _route_profiled_node_status(node_status or inspect_nodes(available_nodes), route_data, controlnet_task)

    if _is_krea2_controlnet_route(route_data, controlnet_task):
        krea_units = ((block.get("inputs") or {}).get("units") or []) if isinstance(block.get("inputs"), dict) else []
        krea_intent = control_intent_from_unit(krea_units[0] if krea_units and isinstance(krea_units[0], dict) else {})
        if krea_intent == "composition_silhouette":
            krea_result = _apply_krea2_control_plus_patch(
                graph, block=block, route_data=route_data, status=status, available_nodes=available_nodes,
                sampler_key=sampler_key, sampler_inputs=sampler_inputs, next_node_id=next_node_id,
            )
            default_adapter = "krea2_control_plus"
        elif krea_intent == "openpose":
            krea_result = _apply_krea2_ostris_openpose_patch(
                graph, block=block, route_data=route_data, status=status, available_nodes=available_nodes,
                sampler_key=sampler_key, sampler_inputs=sampler_inputs, next_node_id=next_node_id,
            )
            default_adapter = "krea2_ostris_openpose"
        elif krea_intent == "canny":
            krea_result = _apply_krea2_nk2e_canny_patch(
                graph, block=block, route_data=route_data, status=status, available_nodes=available_nodes,
                sampler_key=sampler_key, sampler_inputs=sampler_inputs, next_node_id=next_node_id,
            )
            default_adapter = "krea2_nk2e_canny"
        elif krea_intent == "depth":
            krea_result = _apply_krea2_control_lora_patch(
                graph, block=block, route_data=route_data, status=status, available_nodes=available_nodes,
                sampler_key=sampler_key, sampler_inputs=sampler_inputs, next_node_id=next_node_id,
            )
            default_adapter = "krea2_control_lora"
        else:
            krea_result = {"ok": False, "reason": f"No Krea 2 execution adapter is implemented for control intent '{krea_intent}'.", "notes": [{"level": "error", "field": "inputs.units[0].unit", "message": "Select a Krea 2 control type exposed by the active capability registry."}]}
            default_adapter = ""
        adapter = str(krea_result.get("adapter") or default_adapter)
        if not krea_result.get("ok"):
            return no_patch(str(krea_result.get("reason") or "Krea 2 Control could not be applied."), status=status, extra_notes=krea_result.get("notes") or [])
        patch = build_workflow_patch_summary(
            route={**route_data, "adapter": adapter, "map_adapter": adapter, "control_image_source": krea_result.get("control_image_source")},
            node_status=status,
            applied_units=krea_result.get("applied_units") or [],
            node_ids=krea_result.get("created_node_ids") or [],
            previous_positive_ref=previous_positive_ref,
            previous_negative_ref=previous_negative_ref,
            patched_positive_ref=krea_result.get("patched_positive_ref") or previous_positive_ref,
            patched_negative_ref=krea_result.get("patched_negative_ref") or previous_negative_ref,
            sampler_node_id=sampler_key,
            reason="patched",
            applied=True,
            controlnet_task=controlnet_task,
        )
        patch.update({
            "adapter": adapter,
            "control_intent": krea_intent,
            "patched_model_ref": krea_result.get("patched_model_ref"),
            "control_lora": krea_result.get("control_lora"),
            "provider_control_lora": krea_result.get("provider_control_lora"),
            "catalog_binding": deepcopy(krea_result.get("catalog_binding") or {}),
            "model_compatibility": deepcopy(krea_result.get("model_compatibility") or {}),
            "control_strength": krea_result.get("control_strength"),
            "start_percent": krea_result.get("start_percent", 0.0),
            "end_percent": krea_result.get("end_percent", 1.0),
            "control_image_source": krea_result.get("control_image_source"),
            "encode_policy": deepcopy(krea_result.get("encode_policy") or {}),
            "vae_source": krea_result.get("vae_source"),
            "clip_source": krea_result.get("clip_source"),
            "ostris_kv_cache": krea_result.get("ostris_kv_cache"),
            "max_units": 1,
            "model_dir": "loras",
            "conditioning_policy": krea_result.get("conditioning_policy") or "preserve_base_krea2_conditioning",
            "reference_policy": krea_result.get("reference_policy"),
        })
        return {
            "workflow": graph,
            "validation": {"ok": True, "enabled": True, "block": block, "validation": notes + list(krea_result.get("notes") or []), "route": route_data, "node_status": status, "workflow_patch_allowed": True, "reason": "patched", "asset_resolution": {}},
            "workflow_patch": patch,
            "mutated": True,
            "changed": True,
            "extension_id": EXTENSION_ID,
            "phase": ("KREA2_CONTROL_PHASE6_CANNY_NK2E" if adapter == "krea2_nk2e_canny" else ("KREA2_CONTROL_PHASE5_OPENPOSE_OSTRIS" if adapter == "krea2_ostris_openpose" else ("KREA2_CONTROL_PHASE4_COMPOSITION" if adapter == "krea2_control_plus" else "KREA2_CONTROL_PHASE1_DEPTH"))),
            "route_state": route_data.get("controlnet_task_state") or route_data.get("route_state"),
        }

    qwen_vae_ref: list[Any] | None = None
    qwen_vae_source = ""
    qwen_vae_contract: dict[str, Any] = {}
    # Qwen inpaint/outpaint adapters own their VAE contracts inside their
    # adapter patchers. Only the generic Qwen map-control lane is InstantX-
    # based here, so it resolves VAE state before the shared map patch loop.
    if _is_qwen_controlnet_route(route_data) and controlnet_task == TASK_MAP_CONTROL:
        qwen_vae_contract = _qwen_vae_contract(status, "instantx")
        qwen_vae_source = "instantx_apply_schema_has_no_vae_input"
        if qwen_vae_contract.get("supported"):
            qwen_vae_ref, qwen_vae_source = _resolve_qwen_vae_ref(
                graph,
                (previous_positive_ref, previous_negative_ref),
            )
            if qwen_vae_contract.get("required") and not qwen_vae_ref:
                missing = _qwen_vae_missing_result(notes, qwen_vae_contract, qwen_vae_source)
                return no_patch(
                    str(missing.get("reason") or "validation_failed: Qwen InstantX ControlNet requires the active Qwen VAE"),
                    status=status,
                    extra_notes=list(missing.get("notes") or [])[len(notes):],
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
        vae_contract = adapter_result.get("vae_contract") if isinstance(adapter_result.get("vae_contract"), dict) else {}
        if vae_contract:
            patch["qwen_controlnet_vae_contract_schema"] = vae_contract.get("schema_version")
            patch["qwen_controlnet_vae_policy"] = vae_contract.get("policy")
            patch["qwen_controlnet_vae_input_mode"] = vae_contract.get("input_mode")
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
        if qwen_vae_contract:
            applied["vae_policy"] = qwen_vae_contract.get("policy")
        if qwen_vae_ref:
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
    if qwen_vae_contract:
        patch["qwen_controlnet_vae_contract_schema"] = qwen_vae_contract.get("schema_version")
        patch["qwen_controlnet_vae_policy"] = qwen_vae_contract.get("policy")
        patch["qwen_controlnet_vae_input_mode"] = qwen_vae_contract.get("input_mode")
    if qwen_vae_ref:
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
