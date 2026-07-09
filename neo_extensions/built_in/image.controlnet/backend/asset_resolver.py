from __future__ import annotations

from copy import deepcopy
from typing import Any

from .support_matrix import TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL, normalize_controlnet_task, normalize_workflow_mode

SCHEMA = "neo.image.controlnet.asset_resolver.v1"
PHASE = "QK"

SOURCE_IMAGE_KEYS = (
    "comfy_source_image_name",
    "source_image_name",
    "source_image",
    "source_image_path",
    "source_image_url",
    "init_image",
    "image",
    "source_url",
)
MASK_IMAGE_KEYS = (
    "comfy_mask_image_name",
    "mask_image_name",
    "mask_image",
    "mask_image_path",
    "mask_image_url",
    "inpaint_mask",
    "source_mask",
)
OUTPAINT_CANVAS_KEYS = (
    "outpaint_canvas_image",
    "outpaint_canvas_image_name",
    "outpaint_padded_image",
    "outpaint_padded_image_name",
    "padded_image",
)
OUTPAINT_MASK_KEYS = (
    "outpaint_mask_image",
    "outpaint_mask_image_name",
    "outpaint_mask",
    "outpaint_mask_name",
    "padded_mask",
)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return int(default)


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value not in (None, "") else default)
    except (TypeError, ValueError):
        return float(default)


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _asset_ref(value: Any) -> str:
    """Return the best Comfy/file/image reference string from mixed asset shapes."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in (
            "comfy_image_name",
            "image_name",
            "mask_name",
            "workflow_source",
            "filename",
            "name",
            "path",
            "url",
            "ref",
            "asset_id",
            "map_id",
        ):
            picked = value.get(key)
            if picked not in (None, ""):
                return str(picked).strip()
    return ""


def _bucket_value(assets: dict[str, Any], bucket_name: str, uid: str | None = None) -> Any:
    bucket = assets.get(bucket_name)
    if isinstance(bucket, dict):
        keys = []
        if uid:
            keys.append(str(uid))
        keys.extend(["primary", "default"])
        for key in keys:
            value = bucket.get(key)
            if value not in (None, ""):
                return value
    elif isinstance(bucket, list):
        return bucket[0] if bucket else None
    elif bucket not in (None, ""):
        return bucket
    return None


def _first_asset_ref(assets: dict[str, Any], bucket_names: tuple[str, ...], uid: str | None = None) -> str:
    for bucket_name in bucket_names:
        ref = _asset_ref(_bucket_value(assets, bucket_name, uid))
        if ref:
            return ref
    return ""


def _first_param_ref(params: dict[str, Any], keys: tuple[str, ...]) -> str:
    return _asset_ref(_pick(params, *keys))


def _active_units(block: dict[str, Any]) -> list[dict[str, Any]]:
    units = _as_list(_as_dict(block.get("inputs")).get("units"))
    active: list[dict[str, Any]] = []
    for index, unit in enumerate(units):
        if not isinstance(unit, dict) or unit.get("enabled") is False:
            continue
        clean = deepcopy(unit)
        clean["uid"] = str(clean.get("uid") or f"unit_{index + 1}")
        active.append(clean)
    return active


def _outpaint_padding(params: dict[str, Any]) -> dict[str, int]:
    nested = _as_dict(params.get("padding") or params.get("outpaint_padding"))

    def side(name: str, *flat_keys: str) -> int:
        return max(0, _as_int(_pick(nested, name) if nested.get(name) not in (None, "") else _pick(params, *flat_keys), 0))

    return {
        "left": side("left", "outpaint_left", "pad_left", "left"),
        "right": side("right", "outpaint_right", "pad_right", "right"),
        "top": side("top", "outpaint_top", "pad_top", "top"),
        "bottom": side("bottom", "outpaint_bottom", "pad_bottom", "bottom"),
    }


def _source_resolution(params: dict[str, Any]) -> dict[str, Any]:
    nested = _as_dict(params.get("outpaint_source_resolution"))
    if nested:
        return deepcopy(nested)
    source_w = _as_int(_pick(params, "source_image_width", "outpaint_source_width"), 0)
    source_h = _as_int(_pick(params, "source_image_height", "outpaint_source_height"), 0)
    working_w = _as_int(_pick(params, "outpaint_working_width", "width", "base_width"), 0)
    working_h = _as_int(_pick(params, "outpaint_working_height", "height", "base_height"), 0)
    if not any([source_w, source_h, working_w, working_h]):
        return {}
    return {
        "mode": str(_pick(params, "outpaint_source_resolution_mode") or "auto"),
        "max_long_edge": _as_int(_pick(params, "outpaint_source_max_long_edge"), 1536),
        "max_megapixels": _as_float(_pick(params, "outpaint_source_max_megapixels"), 4.0),
        "source_size": {"width": source_w, "height": source_h},
        "working_size": {"width": working_w, "height": working_h},
    }


def _resolution_refs(params: dict[str, Any]) -> tuple[str, str]:
    source_resolution = _source_resolution(params)
    working_size = _as_dict(source_resolution.get("working_size"))
    source_size = _as_dict(source_resolution.get("source_size"))
    working = ""
    original = ""
    if working_size.get("width") and working_size.get("height"):
        working = f"{int(working_size.get('width'))}x{int(working_size.get('height'))}"
    if source_size.get("width") and source_size.get("height"):
        original = f"{int(source_size.get('width'))}x{int(source_size.get('height'))}"
    return original, working





def _phase_o_sd_checkpoint_adapter_ready(task: str, route: dict[str, Any] | None) -> bool:
    route = route if isinstance(route, dict) else {}
    family = str(route.get("family") or "")
    loader = str(route.get("loader") or "")
    mode = normalize_workflow_mode(route.get("workflow_mode"))
    return loader == "checkpoint" and family in {"sdxl", "sd15"} and (
        (task == TASK_INPAINT_CONTROL and mode == "inpaint")
        or (task == TASK_OUTPAINT_CONTROL and mode == "outpaint")
    )


def _phase_p_qwen_adapter_ready(task: str, route: dict[str, Any] | None) -> bool:
    route = route if isinstance(route, dict) else {}
    family = str(route.get("family") or "")
    loader = str(route.get("loader") or "")
    mode = normalize_workflow_mode(route.get("workflow_mode"))
    qwen_route = (
        (family in {"qwen_image", "qwen_image_edit_2509"} and loader in {"diffusion_model", "gguf"})
        or (family == "qwen_rapid_aio" and loader in {"gguf", "checkpoint_aio"})
    )
    return qwen_route and (
        (task == TASK_INPAINT_CONTROL and mode == "inpaint")
        or (task == TASK_OUTPAINT_CONTROL and mode == "outpaint")
    )


def _phase_q_flux_adapter_ready(task: str, route: dict[str, Any] | None) -> bool:
    route = route if isinstance(route, dict) else {}
    family = str(route.get("family") or "")
    loader = str(route.get("loader") or "")
    mode = normalize_workflow_mode(route.get("workflow_mode"))
    return family in {"flux", "flux2_klein"} and loader in {"diffusion_model", "gguf"} and (
        (task == TASK_INPAINT_CONTROL and mode == "inpaint")
        or (task == TASK_OUTPAINT_CONTROL and mode == "outpaint")
    )




def _is_flux2_klein_params(params: dict[str, Any]) -> bool:
    variant = str(_pick(params, "flux_variant", "variant") or "").strip().lower().replace("-", "_").replace(" ", "_")
    if variant in {"klein", "flux2_klein", "flux_2_klein", "klein_4b", "klein_9b", "klein_4b_distilled", "klein_9b_distilled"}:
        return True
    if params.get("_neo_effective_flux2_klein_gguf_route") is True:
        return True
    if isinstance(params.get("flux2_klein_gguf_profile"), dict):
        return True
    for key in ("gguf_unet", "gguf_model", "model", "model_name"):
        text = str(params.get(key) or "").strip().lower().replace("_", "-")
        if "klein" in text and ("flux-2" in text or "flux2" in text or text.startswith("klein")):
            return True
    return False

def _flux_adapter(params: dict[str, Any]) -> str:
    value = str(_pick(params, "flux_controlnet_adapter", "controlnet_flux_adapter", "flux_cn_adapter", "flux_klein_controlnet_adapter") or "auto").strip().lower().replace("-", "_")
    if value in {"fun_union", "flux2_fun_union", "flux_2_fun_union", "flux2", "klein", "klein_fun", "flux2_klein"}:
        return "fun_union"
    if value in {"alimama", "flux_inpaint", "flux_controlnet_inpaint", "inpaint", "controlnet"}:
        return "alimama"
    if _is_flux2_klein_params(params):
        return "fun_union"
    return "auto"


def _qwen_adapter(params: dict[str, Any]) -> str:
    value = str(_pick(params, "qwen_controlnet_adapter", "controlnet_qwen_adapter", "qwen_cn_adapter") or "auto").strip().lower()
    if value in {"diffsynth", "diff_synth", "model_patch", "model-patch", "patch"}:
        return "diffsynth"
    if value in {"instantx", "instant_x", "native_controlnet", "controlnet"}:
        return "instantx"
    return "auto"

def _public_route(route: dict[str, Any] | None) -> dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    allowed = ("backend", "family", "loader", "workflow_mode", "route_state", "controlnet_task", "controlnet_task_state")
    return {key: deepcopy(route.get(key)) for key in allowed if route.get(key) not in (None, "")}

def _route_image_params(route: dict[str, Any] | None) -> dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    params = {}
    for key in ("params", "actual_params"):
        value = route.get(key)
        if isinstance(value, dict):
            params.update(value)
    return params

def _base_result(task: str, block: dict[str, Any] | None, route: dict[str, Any] | None = None) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    params = _as_dict(block.get("params"))
    task = normalize_controlnet_task(task or params.get("controlnet_task"))
    return {
        "schema": SCHEMA,
        "phase": PHASE,
        "controlnet_task": task,
        "workflow_mode": normalize_workflow_mode((route or {}).get("workflow_mode")),
        "route": _public_route(route),
        "ready": False,
        "missing": [],
        "warnings": [],
        "assets": {},
        "unit_assets": {},
        "adapter_execution": "blocked_until_adapter_phase",
    }


def resolve_controlnet_inpaint_assets(
    block: dict[str, Any] | None,
    *,
    image_params: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve Image Tab source + painted mask assets for future CN inpaint adapters.

    Phase N is adapter-prep only. It normalizes asset references and reports
    readiness, but it does not imply the generic map ControlNet patcher can run
    inpaint control.
    """
    block = block if isinstance(block, dict) else {}
    assets = _as_dict(block.get("assets"))
    params = {**_route_image_params(route), **_as_dict(image_params), **_as_dict(block.get("params"))}
    result = _base_result(TASK_INPAINT_CONTROL, block, route)
    if _phase_o_sd_checkpoint_adapter_ready(TASK_INPAINT_CONTROL, route):
        result["adapter_execution"] = "sd_checkpoint_phase_o_adapter_ready"
    elif _phase_p_qwen_adapter_ready(TASK_INPAINT_CONTROL, route):
        result["adapter_execution"] = "qwen_phase_p_adapter_ready"
        result["qwen_controlnet_adapter"] = _qwen_adapter(params)
    elif _phase_q_flux_adapter_ready(TASK_INPAINT_CONTROL, route):
        adapter = "fun_union" if str((route or {}).get("family") or "") == "flux2_klein" else _flux_adapter(params)
        result["adapter_execution"] = "flux_phase_qk_klein_adapter_ready" if adapter == "fun_union" else "flux_phase_q_adapter_ready"
        result["flux_controlnet_adapter"] = adapter
        result["flux2_klein_controlnet"] = adapter == "fun_union"
    source_image = (
        _first_asset_ref(assets, ("source_images", "inpaint_source_images", "control_images"), "primary")
        or _first_param_ref(params, SOURCE_IMAGE_KEYS)
    )
    mask_image = (
        _first_asset_ref(assets, ("source_masks", "inpaint_masks", "control_masks"), "primary")
        or _first_param_ref(params, MASK_IMAGE_KEYS)
    )
    target = str(_pick(params, "inpaint_selection_target", "inpaint_target") or "masked_area").strip() or "masked_area"
    context = str(_pick(params, "inpaint_context_mode") or "masked_region_focus").strip() or "masked_region_focus"
    if not source_image:
        result["missing"].append("source_image")
    if not mask_image:
        result["missing"].append("mask_image")
    result["assets"] = {
        "source_image": source_image,
        "mask_image": mask_image,
        "target": target,
        "context_mode": context,
        "mask_grow": max(0, _as_int(_pick(params, "mask_grow", "inpaint_mask_grow"), 0)),
        "mask_blur": max(0, _as_int(_pick(params, "mask_blur", "inpaint_mask_blur"), 0)),
        "qwen_controlnet_adapter": _qwen_adapter(params),
        "flux_controlnet_adapter": _flux_adapter(params),
    }
    if target in {"not_masked_area", "unmasked", "not_masked"}:
        result["assets"]["mask_inversion_required"] = True
    units = _active_units(block)
    for unit in units:
        uid = str(unit.get("uid"))
        unit_source = _first_asset_ref(assets, ("source_images", "inpaint_source_images", "control_images"), uid) or source_image
        unit_mask = _first_asset_ref(assets, ("source_masks", "inpaint_masks", "control_masks"), uid) or mask_image
        result["unit_assets"][uid] = {
            "source_image": unit_source,
            "mask_image": unit_mask,
            "target": target,
            "context_mode": context,
            "model": str(unit.get("model") or ""),
        }
    result["ready"] = not result["missing"] and bool(units)
    if not units:
        result["missing"].append("active_controlnet_unit")
        result["ready"] = False
    return result


def resolve_controlnet_outpaint_assets(
    block: dict[str, Any] | None,
    *,
    image_params: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Resolve source, working-copy policy, padding, and canvas/mask intent for outpaint.

    The outpaint adapter can later generate the padded canvas/mask when explicit
    canvas assets are not present. This resolver records that intent so adapters
    can reuse the same Phase H/J source-resolution policy instead of inventing
    their own sizing rules.
    """
    block = block if isinstance(block, dict) else {}
    assets = _as_dict(block.get("assets"))
    params = {**_route_image_params(route), **_as_dict(image_params), **_as_dict(block.get("params"))}
    result = _base_result(TASK_OUTPAINT_CONTROL, block, route)
    if _phase_o_sd_checkpoint_adapter_ready(TASK_OUTPAINT_CONTROL, route):
        result["adapter_execution"] = "sd_checkpoint_phase_o_adapter_ready"
    elif _phase_p_qwen_adapter_ready(TASK_OUTPAINT_CONTROL, route):
        result["adapter_execution"] = "qwen_phase_p_adapter_ready"
        result["qwen_controlnet_adapter"] = _qwen_adapter(params)
    elif _phase_q_flux_adapter_ready(TASK_OUTPAINT_CONTROL, route):
        adapter = "fun_union" if str((route or {}).get("family") or "") == "flux2_klein" else _flux_adapter(params)
        result["adapter_execution"] = "flux_phase_qk_klein_adapter_ready" if adapter == "fun_union" else "flux_phase_q_adapter_ready"
        result["flux_controlnet_adapter"] = adapter
        result["flux2_klein_controlnet"] = adapter == "fun_union"
    source_image = (
        _first_asset_ref(assets, ("source_images", "outpaint_source_images", "control_images"), "primary")
        or _first_param_ref(params, SOURCE_IMAGE_KEYS)
    )
    canvas_image = (
        _first_asset_ref(assets, ("outpaint_canvas_images", "padded_images", "control_images"), "primary")
        or _first_param_ref(params, OUTPAINT_CANVAS_KEYS)
    )
    mask_image = (
        _first_asset_ref(assets, ("outpaint_masks", "padded_masks", "control_masks"), "primary")
        or _first_param_ref(params, OUTPAINT_MASK_KEYS)
    )
    padding = _outpaint_padding(params)
    padding_total = sum(padding.values())
    source_resolution = _source_resolution(params)
    original_size, working_size = _resolution_refs(params)
    if not source_image and not canvas_image:
        result["missing"].append("source_image")
    if not padding_total and not canvas_image:
        result["missing"].append("outpaint_padding")
    if not mask_image:
        result["warnings"].append({
            "level": "info",
            "field": "assets.outpaint_masks",
            "message": "No explicit outpaint mask asset attached; adapter should generate the padded mask from padding.",
        })
    if not canvas_image:
        result["warnings"].append({
            "level": "info",
            "field": "assets.outpaint_canvas_images",
            "message": "No explicit padded canvas attached; adapter should generate it from the source image and padding.",
        })
    result["assets"] = {
        "source_image": source_image,
        "canvas_image": canvas_image,
        "mask_image": mask_image,
        "padding": padding,
        "padding_total": padding_total,
        "source_resolution": source_resolution,
        "original_size": original_size,
        "working_size": working_size,
        "canvas_generation": "adapter_generate_from_padding" if not canvas_image else "explicit_asset",
        "mask_generation": "adapter_generate_from_padding" if not mask_image else "explicit_asset",
        "qwen_controlnet_adapter": _qwen_adapter(params),
        "flux_controlnet_adapter": _flux_adapter(params),
    }
    units = _active_units(block)
    for unit in units:
        uid = str(unit.get("uid"))
        unit_source = _first_asset_ref(assets, ("source_images", "outpaint_source_images", "control_images"), uid) or source_image
        unit_canvas = _first_asset_ref(assets, ("outpaint_canvas_images", "padded_images", "control_images"), uid) or canvas_image
        unit_mask = _first_asset_ref(assets, ("outpaint_masks", "padded_masks", "control_masks"), uid) or mask_image
        result["unit_assets"][uid] = {
            "source_image": unit_source,
            "canvas_image": unit_canvas,
            "mask_image": unit_mask,
            "padding": deepcopy(padding),
            "source_resolution": deepcopy(source_resolution),
            "model": str(unit.get("model") or ""),
        }
    result["ready"] = not result["missing"] and bool(units)
    if not units:
        result["missing"].append("active_controlnet_unit")
        result["ready"] = False
    return result


def resolve_controlnet_task_assets(
    block: dict[str, Any] | None,
    *,
    image_params: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
) -> dict[str, Any]:
    block = block if isinstance(block, dict) else {}
    params = _as_dict(block.get("params"))
    task = normalize_controlnet_task(params.get("controlnet_task"))
    if task == TASK_INPAINT_CONTROL:
        return resolve_controlnet_inpaint_assets(block, image_params=image_params, route=route)
    if task == TASK_OUTPAINT_CONTROL:
        return resolve_controlnet_outpaint_assets(block, image_params=image_params, route=route)
    result = _base_result(task, block, route)
    result["adapter_execution"] = "map_control_uses_existing_workflow_patcher"
    result["ready"] = True
    return result
