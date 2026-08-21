from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.image.inpaint_payload import normalize_inpaint_target_aliases
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import first_explicit_asset_selection, require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.comfy_workflows.adetailer_route_contract import publish_adetailer_route_contract
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_app.providers.comfy_workflows.qwen_stitch_route import apply_qwen_stitch_route
from neo_app.image.qwen_stitch_contract import extract_qwen_stitch_payload, qwen_stitch_has_ready_group
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile
from neo_app.providers.comfy_workflows.vae_decode_utils import (
    build_vae_decode_node,
    build_vae_loader_node,
    resolve_vae_decode_strategy,
    vae_decode_profile_payload,
)


QWEN_EDIT_NODE_CANDIDATES = (
    "TextEncodeQwenImageEditPlus",
    "TextEncodeQwenImageEditPlus_lrzjason",
    "TextEncodeQwenImageEditPlusAdvance_lrzjason",
    "TextEncodeQwenImageEditPlusPro_lrzjason",
)


@dataclass(frozen=True)
class QwenRapidAioDefaults:
    width: int = 1024
    height: int = 1024
    steps: int = 4
    cfg: float = 1.0
    denoise_txt2img: float = 1.0
    denoise_img2img: float = 1.0
    denoise_inpaint: float = 0.9
    sampler: str = "euler"
    scheduler: str = "simple"
    checkpoint: str = ""
    edit_node: str = "TextEncodeQwenImageEditPlus"


@dataclass(frozen=True)
class QwenNativeEditDefaults:
    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg: float = 4.0
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    latent_node: str = "EmptySD3LatentImage"
    sampling_node: str = "ModelSamplingAuraFlow"
    aura_shift: float = 3.1
    clip_type: str = "qwen_image"
    clip_device: str = "default"
    model: str = ""
    text_encoder: str = ""
    vae: str = ""


QWEN_RAPID_AIO_DEFAULTS = QwenRapidAioDefaults()
QWEN_NATIVE_EDIT_DEFAULTS = QwenNativeEditDefaults()


def _qwen_output_prefix(*, family: Any, mode: Any, variant: str = "qwen") -> str:
    raw = f"NeoStudio_{variant}_{family or 'qwen'}_{mode or 'generate'}"
    clean = []
    for char in str(raw):
        clean.append(char if char.isalnum() else "_")
    return "".join(clean).strip("_") or "NeoStudio_Qwen"


def _append_qwen_output_handoff(
    workflow: dict[str, Any],
    *,
    next_id: int,
    image_ref: list[Any],
    filename_prefix: str,
) -> tuple[int, str, str]:
    save_id = str(next_id)
    workflow[save_id] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": filename_prefix, "images": list(image_ref)},
    }
    next_id += 1
    preview_id = str(next_id)
    workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": list(image_ref)}}
    next_id += 1
    return next_id, save_id, preview_id


def _is_gguf_file(value: Any) -> bool:
    return str(value or "").strip().lower().endswith(".gguf")


def _qwen_native_edit_clip_loader_for_encoder(text_encoder: Any, requested_loader: Any = None) -> str:
    candidate = str(requested_loader or "").strip()
    if _is_gguf_file(text_encoder):
        return candidate if candidate in {"CLIPLoaderGGUF", "ClipLoaderGGUF"} else "CLIPLoaderGGUF"
    return "CLIPLoader"


def _backend_supports_qwen_mixed_clip_loader(backend_capabilities: dict[str, Any] | None, loader_class: str, *, clip_type: str = "qwen_image") -> bool:
    object_info = ((backend_capabilities or {}).get("object_info_node_inputs") or {})
    node_info = object_info.get(loader_class) or object_info.get("ClipLoaderGGUF" if loader_class == "CLIPLoaderGGUF" else loader_class) or {}
    if not isinstance(node_info, dict) or not node_info:
        return False
    if loader_class == "CLIPLoader":
        return True
    type_spec = node_info.get("type")
    if isinstance(type_spec, (list, tuple, set)):
        return clip_type in {str(item) for item in type_spec}
    if isinstance(type_spec, dict):
        values = type_spec.get("values") or type_spec.get("choices") or []
        return clip_type in {str(item) for item in values}
    return True


QWEN_RAPID_AIO_BUNDLED_COMPONENT_FIELDS = {
    # Split/component model fields that must not survive on the bundled
    # CheckpointLoaderSimple route. The AIO checkpoint owns UNet, text encoder,
    # vision/edit stack, and VAE outputs through loader outputs [0], [1], [2].
    "diffusion_model",
    "unet",
    "unet_name",
    "qwen_image_edit_model",
    "qwen_model",
    "text_encoder_1",
    "text_encoder_2",
    "text_encoder_primary",
    "text_encoder_secondary",
    "qwen_text_encoder",
    "qwen3_text_encoder",
    "clip_name",
    "clip_type",
    "clip_device",
    "gguf_text_encoder_1",
    "gguf_text_encoder_2",
    "gguf_text_encoder_primary",
    "gguf_text_encoder_secondary",
    "qwen_mmproj",
    "mmproj",
    "mmproj_name",
    "vae",
    "vae_or_ae",
    "qwen_vae",
    "ae",
    "gguf_model",
    "gguf_unet",
    "gguf_clip_type",
    "gguf_clip_mode",
}

def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _select_qwen_rapid_aio_checkpoint(job: NeoJob, params: dict[str, Any], defaults: QwenRapidAioDefaults) -> str:
    return first_explicit_asset_selection((
        job.model,
        params.get("qwen_rapid_aio_checkpoint"),
        params.get("checkpoint"),
        params.get("ckpt_name"),
        params.get("model"),
        params.get("model_name"),
        defaults.checkpoint,
    ))


def _prune_qwen_rapid_aio_bundled_params(params: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    cleaned = dict(params or {})
    removed: list[str] = []
    for key in sorted(QWEN_RAPID_AIO_BUNDLED_COMPONENT_FIELDS):
        if key in cleaned:
            cleaned.pop(key, None)
            removed.append(key)
    return cleaned, removed


def _int_param(params: dict[str, Any], *names: str, default: int = 0) -> int:
    value = _param(params, *names, default=default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _source_image_name(params: dict[str, Any]) -> str:
    value = (
        params.get("comfy_source_image_name")
        or params.get("source_image_name")
        or params.get("source_image")
        or params.get("source_image_path")
        or params.get("init_image")
        or params.get("image")
    )
    if isinstance(value, dict):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
    return str(value or "").strip().split("/")[-1].split("\\")[-1]



def _outpaint_working_size(outpaint_payload: dict[str, Any], fallback_width: int, fallback_height: int) -> tuple[int, int, bool, dict[str, Any]]:
    resolution = outpaint_payload.get("source_resolution") if isinstance(outpaint_payload, dict) else {}
    if not isinstance(resolution, dict) or not resolution:
        resolution = {}
        working = {}
        mode = "keep_original"
    else:
        working = resolution.get("working_size") if isinstance(resolution.get("working_size"), dict) else {}
        mode = str(resolution.get("mode") or "auto").strip().lower()
    width = max(64, int(working.get("width") or fallback_width or 1024))
    height = max(64, int(working.get("height") or fallback_height or 1024))
    return width, height, mode != "keep_original", resolution


def _insert_outpaint_source_scale_node(workflow: dict[str, Any], next_id: int, source_ref: list[Any], outpaint_payload: dict[str, Any], *, fallback_width: int, fallback_height: int) -> tuple[int, list[Any], int, int, dict[str, Any] | None]:
    working_width, working_height, should_scale, resolution = _outpaint_working_size(outpaint_payload, fallback_width, fallback_height)
    if not should_scale:
        return next_id, source_ref, working_width, working_height, None
    workflow[str(next_id)] = {
        "class_type": "ImageScale",
        "inputs": {
            "image": list(source_ref),
            "upscale_method": "lanczos",
            "width": int(working_width),
            "height": int(working_height),
            "crop": "disabled",
        },
    }
    return next_id + 1, [str(next_id), 0], working_width, working_height, {
        "class_type": "ImageScale",
        "working_size": {"width": int(working_width), "height": int(working_height)},
        "source_resolution": resolution,
    }

def _mask_image_name(params: dict[str, Any]) -> str:
    value = (
        params.get("comfy_mask_image_name")
        or params.get("mask_image_name")
        or params.get("mask_image")
        or params.get("mask_path")
        or params.get("mask")
    )
    if isinstance(value, dict):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
    return str(value or "").strip().split("/")[-1].split("\\")[-1]


def _named_image_name(params: dict[str, Any], *names: str) -> str:
    for name in names:
        value = params.get(name)
        if isinstance(value, dict):
            value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
        text = str(value or "").strip()
        if text:
            return text.split("/")[-1].split("\\")[-1]
    return ""


def _qwen_object_info_node_inputs(backend_capabilities: dict[str, Any] | None, node_name: str) -> dict[str, Any]:
    node_inputs = ((backend_capabilities or {}).get("object_info_node_inputs") or {}).get(node_name) or {}
    return node_inputs if isinstance(node_inputs, dict) else {}


def _qwen_edit_node_input_names(backend_capabilities: dict[str, Any] | None, node_name: str) -> set[str]:
    node_inputs = _qwen_object_info_node_inputs(backend_capabilities, node_name)
    names = node_inputs.get("all") if isinstance(node_inputs, dict) else []
    return {str(name) for name in names or []}


def _qwen_available_edit_nodes(backend_capabilities: dict[str, Any] | None) -> list[str]:
    node_map = (backend_capabilities or {}).get("object_info_node_inputs") or {}
    if not isinstance(node_map, dict):
        return []
    return [name for name in QWEN_EDIT_NODE_CANDIDATES if isinstance(node_map.get(name), dict) and node_map.get(name)]


def _select_qwen_edit_node(params: dict[str, Any], backend_capabilities: dict[str, Any] | None) -> str:
    requested = str(_param(params, "qwen_edit_node", "qwen_edit_encoder_node", default="") or "").strip()
    available = _qwen_available_edit_nodes(backend_capabilities)
    if requested and (not available or requested in available):
        return requested
    if "TextEncodeQwenImageEditPlus" in available:
        return "TextEncodeQwenImageEditPlus"
    return available[0] if available else "TextEncodeQwenImageEditPlus"


def _qwen_edit_target_size(params: dict[str, Any], width: int, height: int) -> int:
    explicit = _param(params, "qwen_target_size", "qwen_edit_target_size", "target_size", "size", default=None)
    try:
        if explicit not in (None, ""):
            value = int(float(explicit))
            if value > 0:
                return max(64, value)
    except (TypeError, ValueError):
        pass
    largest = max(int(width or 0), int(height or 0), 1024)
    estimated = int((largest * 0.875) // 32) * 32
    return max(512, min(1536, estimated or 896))


def _truthy_param(params: dict[str, Any], name: str, default: bool = False) -> bool:
    value = params.get(name)
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _qwen_edit_plus_extra_inputs(params: dict[str, Any], width: int, height: int, backend_capabilities: dict[str, Any] | None, node_name: str) -> dict[str, Any]:
    supported_inputs = _qwen_edit_node_input_names(backend_capabilities, node_name)
    target_size = _qwen_edit_target_size(params, width, height)
    extra: dict[str, Any] = {}
    if "size" in supported_inputs or _truthy_param(params, "qwen_force_size_input", default=False):
        extra["size"] = target_size
    if "target_size" in supported_inputs or _truthy_param(params, "qwen_force_target_size_input", default=False):
        extra["target_size"] = target_size
    return extra


def _qwen_edit_node_compatibility_snapshot(backend_capabilities: dict[str, Any] | None, node_name: str, extra_inputs: dict[str, Any]) -> dict[str, Any]:
    supported_inputs = sorted(_qwen_edit_node_input_names(backend_capabilities, node_name))
    available_nodes = _qwen_available_edit_nodes(backend_capabilities)
    return {
        "selected_node": node_name,
        "available_nodes": available_nodes,
        "declared_inputs": supported_inputs,
        "sent_extra_inputs": sorted(extra_inputs.keys()),
        "declares_target_size": "target_size" in supported_inputs,
        "declares_size": "size" in supported_inputs,
        "risk": "node_internal_size_nameerror_possible" if node_name == "TextEncodeQwenImageEditPlus" and "target_size" not in supported_inputs and "size" not in supported_inputs else "normal",
    }

def _backend_declares_node(backend_capabilities: dict[str, Any] | None, node_name: str) -> bool:
    node_map = (backend_capabilities or {}).get("object_info_node_inputs") or {}
    return isinstance(node_map.get(node_name), dict) and bool(node_map.get(node_name))


def _qwen_node_available_or_unknown(backend_capabilities: dict[str, Any] | None, node_name: str) -> bool:
    if backend_capabilities is None:
        return True
    node_map = (backend_capabilities or {}).get("object_info_node_inputs") or {}
    if not isinstance(node_map, dict) or not node_map:
        return True
    return _backend_declares_node(backend_capabilities, node_name)


def _qwen_live_object_info_available(backend_capabilities: dict[str, Any] | None) -> bool:
    if not isinstance(backend_capabilities, dict):
        return False
    node_map = backend_capabilities.get("object_info_node_inputs") or {}
    discovery_status = str(backend_capabilities.get("discovery_status") or "").strip().lower()
    return discovery_status == "available" and isinstance(node_map, dict) and bool(node_map)


def _require_qwen_parity_node(
    validation: ProviderValidationResult,
    backend_capabilities: dict[str, Any] | None,
    node_name: str,
    *,
    reason: str,
) -> bool:
    available = _qwen_node_available_or_unknown(backend_capabilities, node_name)
    if available:
        return True
    if _qwen_live_object_info_available(backend_capabilities):
        message = f"Qwen native parity requires Comfy node {node_name}: {reason}"
        if message not in validation.errors:
            validation.errors.append(message)
        validation.ok = False
    return False


def _append_qwen_source_scale_node(
    workflow: dict[str, Any],
    next_id: int,
    source_ref: list[Any],
    *,
    backend_capabilities: dict[str, Any] | None,
) -> tuple[int, list[Any], dict[str, Any] | None]:
    if not _qwen_node_available_or_unknown(backend_capabilities, "FluxKontextImageScale"):
        return next_id, source_ref, None
    workflow[str(next_id)] = {"class_type": "FluxKontextImageScale", "inputs": {"image": list(source_ref)}}
    return next_id + 1, [str(next_id), 0], {"class_type": "FluxKontextImageScale", "node_id": str(next_id)}


def _append_qwen_multi_reference_latent_method(
    workflow: dict[str, Any],
    next_id: int,
    conditioning_ref: list[Any],
    *,
    backend_capabilities: dict[str, Any] | None,
    method: str = "index_timestep_zero",
) -> tuple[int, list[Any], dict[str, Any] | None]:
    if not _qwen_node_available_or_unknown(backend_capabilities, "FluxKontextMultiReferenceLatentMethod"):
        return next_id, conditioning_ref, None
    workflow[str(next_id)] = {
        "class_type": "FluxKontextMultiReferenceLatentMethod",
        "inputs": {"conditioning": list(conditioning_ref), "reference_latents_method": method},
    }
    return next_id + 1, [str(next_id), 0], {"class_type": "FluxKontextMultiReferenceLatentMethod", "node_id": str(next_id), "method": method}


def _load_qwen_source_images(
    workflow: dict[str, Any],
    params: dict[str, Any],
    *,
    start_id: int = 20,
    max_images: int = 3,
    backend_capabilities: dict[str, Any] | None = None,
) -> tuple[int, dict[str, list[Any]], list[str], dict[str, Any]]:
    next_id = start_id
    qwen_inputs: dict[str, list[Any]] = {}
    notes: list[str] = []
    max_images = max(1, min(3, int(max_images or 1)))
    all_names = [
        _source_image_name(params),
        _named_image_name(params, "source_image_2", "reference_image_2", "qwen_source_image_2", "image2"),
        _named_image_name(params, "source_image_3", "reference_image_3", "qwen_source_image_3", "image3"),
    ]
    names = all_names[:max_images]
    ignored_names = [name for name in all_names[max_images:] if name]
    meta: dict[str, Any] = {"source_images": {}, "qwen_source_image_limit": max_images, "source_image_nodes": {}}
    if ignored_names:
        meta["ignored_source_images"] = ignored_names
        notes.append(f"Qwen edit route ignored extra source lane(s) above limit {max_images}: " + ", ".join(ignored_names))
    scaled_lanes: list[str] = []
    for idx, name in enumerate(names, start=1):
        if not name:
            continue
        load_id = str(next_id)
        workflow[load_id] = {"class_type": "LoadImage", "inputs": {"image": name, "upload": "image"}}
        image_ref: list[Any] = [load_id, 0]
        next_id += 1
        next_id, image_ref, scale_meta = _append_qwen_source_scale_node(
            workflow,
            next_id,
            image_ref,
            backend_capabilities=backend_capabilities,
        )
        lane = f"image{idx}"
        qwen_inputs[lane] = list(image_ref)
        meta["source_images"][lane] = name
        lane_nodes = {"load": load_id}
        if scale_meta:
            lane_nodes["scale"] = scale_meta.get("node_id")
            scaled_lanes.append(lane)
        meta["source_image_nodes"][lane] = lane_nodes
    if qwen_inputs:
        notes.append("Qwen edit conditioning received source image lane(s): " + ", ".join(sorted(qwen_inputs.keys())))
    if scaled_lanes:
        notes.append("FluxKontextImageScale preprocess applied to: " + ", ".join(sorted(scaled_lanes)))
        meta["qwen_source_preprocess_node"] = "FluxKontextImageScale"
    return next_id, qwen_inputs, notes, meta


def _build_qwen_edit_conditioning_nodes(
    *,
    workflow: dict[str, Any],
    next_id: int,
    prompt: str,
    negative: str,
    clip_ref: list[Any],
    vae_ref: list[Any],
    qwen_inputs: dict[str, list[Any]],
    width: int,
    height: int,
    params: dict[str, Any],
    backend_capabilities: dict[str, Any] | None,
) -> tuple[int, list[Any], list[Any], dict[str, Any]]:
    edit_node = _select_qwen_edit_node(params, backend_capabilities)
    extra = _qwen_edit_plus_extra_inputs(params, width, height, backend_capabilities, edit_node)
    positive_inputs: dict[str, Any] = {"prompt": prompt, "clip": list(clip_ref), "vae": list(vae_ref), **qwen_inputs, **extra}
    negative_inputs: dict[str, Any] = {"prompt": negative, "clip": list(clip_ref), "vae": list(vae_ref), **qwen_inputs, **extra}
    workflow[str(next_id)] = {"class_type": edit_node, "inputs": positive_inputs}
    positive_ref = [str(next_id), 0]
    next_id += 1
    workflow[str(next_id)] = {"class_type": edit_node, "inputs": negative_inputs}
    negative_ref = [str(next_id), 0]
    next_id += 1
    return next_id, positive_ref, negative_ref, {
        "qwen_edit_node_compatibility": _qwen_edit_node_compatibility_snapshot(backend_capabilities, edit_node, extra),
        "qwen_edit_plus_extra_inputs": dict(extra),
    }


def compile_qwen_rapid_aio_checkpoint(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile Qwen Image Edit Rapid AIO checkpoint routes.

    The AIO checkpoint is intentionally separate from Qwen native split-model
    and Qwen GGUF. It uses CheckpointLoaderSimple and Qwen edit conditioning.
    Pass N3 locks normal workflows: txt2img, img2img/edit, inpaint, and
    outpaint. Img2img/edit can consume up to three source lanes; inpaint and
    outpaint remain single-source mask/canvas workflows until physically
    validated as multi-reference.
    """

    raw_params = job.params or {}
    defaults = QWEN_RAPID_AIO_DEFAULTS
    checkpoint = require_explicit_asset_selection(
        validation,
        "Qwen Rapid AIO checkpoint",
        _select_qwen_rapid_aio_checkpoint(job, raw_params, defaults),
    )
    params, pruned_component_fields = _prune_qwen_rapid_aio_bundled_params(raw_params)
    params["qwen_rapid_aio_checkpoint"] = checkpoint
    params["checkpoint"] = checkpoint
    params["model"] = checkpoint
    mode = str(route.mode or job.mode or "txt2img")
    if mode == "edit":
        mode = "img2img"
    if mode == "inpaint":
        params = normalize_inpaint_target_aliases(params)
    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    steps = int(_param(params, "steps", default=defaults.steps))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    cfg = float(_param(params, "cfg", default=defaults.cfg))
    denoise_default = defaults.denoise_inpaint if mode == "inpaint" else (defaults.denoise_img2img if mode in {"img2img", "outpaint"} else defaults.denoise_txt2img)
    denoise = float(_param(params, "denoise", "strength", default=denoise_default))

    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    stitch_only_ready = mode in {"img2img", "edit"} and qwen_stitch_has_ready_group(extract_qwen_stitch_payload(params))
    if mode in {"img2img", "inpaint", "outpaint"} and not source_name and not stitch_only_ready:
        validation.errors.append(f"Qwen Rapid AIO {mode} requires a source image.")
        validation.ok = False
    if mode == "inpaint" and not mask_name:
        validation.errors.append("Qwen Rapid AIO inpaint requires a mask image.")
        validation.ok = False
    if mode == "outpaint":
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        if outpaint_padding_total(outpaint_payload) <= 0:
            validation.errors.append("Qwen Rapid AIO outpaint requires padding on at least one side.")
            validation.ok = False

    workflow: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": str(checkpoint)}},
    }

    next_id = 20
    qwen_inputs: dict[str, list[Any]] = {}
    source_ref: list[Any] | None = None
    mask_ref: list[Any] | None = None
    route_notes: list[str] = []
    route_meta: dict[str, Any] = {}
    if mode in {"img2img", "inpaint", "outpaint"} and source_name:
        # Pass N3: Rapid AIO img2img/edit can consume up to three Qwen source
        # lanes. Mask/canvas routes stay single-source until a validated
        # multi-reference inpaint/outpaint graph exists.
        source_limit = 3 if mode == "img2img" else 1
        next_id, qwen_inputs, notes, meta = _load_qwen_source_images(workflow, params, start_id=next_id, max_images=source_limit, backend_capabilities=backend_capabilities)
        route_notes.extend(notes)
        route_meta.update(meta)
        source_ref = list(qwen_inputs.get("image1") or []) or None

    if mode == "outpaint" and source_ref is not None:
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        padding = outpaint_payload["padding"]
        mask = outpaint_payload["mask"]
        next_id, source_ref, working_width, working_height, source_scale_meta = _insert_outpaint_source_scale_node(
            workflow, next_id, source_ref, outpaint_payload, fallback_width=width, fallback_height=height
        )
        workflow[str(next_id)] = {
            "class_type": "ImagePadForOutpaint",
            "inputs": {
                "image": list(source_ref),
                "left": int(padding.get("left", 0) or 0),
                "top": int(padding.get("top", 0) or 0),
                "right": int(padding.get("right", 0) or 0),
                "bottom": int(padding.get("bottom", 0) or 0),
                "feathering": int(mask.get("feather", 16) or 16),
            },
        }
        source_ref = [str(next_id), 0]
        qwen_inputs["image1"] = list(source_ref)
        next_id += 1
        route_meta["outpaint_payload"] = outpaint_payload
        route_meta["qwen_aio_outpaint_source_resolution"] = outpaint_payload.get("source_resolution", {})
        route_meta["qwen_aio_outpaint_source_scale_node"] = source_scale_meta or {}
        width = max(64, int(working_width) + int(padding.get("left", 0) or 0) + int(padding.get("right", 0) or 0))
        height = max(64, int(working_height) + int(padding.get("top", 0) or 0) + int(padding.get("bottom", 0) or 0))
        scale_note = " with source working-copy scale" if source_scale_meta else ""
        route_notes.append(f"Qwen Rapid AIO outpaint branch uses{scale_note} ImagePadForOutpaint and padded latent {width}x{height}.")

    next_id, qwen_inputs, stitch_meta, stitch_notes, stitch_errors = apply_qwen_stitch_route(
        workflow,
        params,
        qwen_inputs,
        next_id,
        family="qwen_rapid_aio",
        loader="checkpoint_aio",
        mode=mode,
        backend_capabilities=backend_capabilities,
    )
    if stitch_meta.get("enabled") or stitch_meta.get("groups"):
        route_meta["qwen_stitch"] = stitch_meta
    route_notes.extend(stitch_notes)
    for error in stitch_errors:
        validation.errors.append(error)
    if stitch_errors:
        validation.ok = False

    next_id, positive_ref, negative_ref, edit_meta = _build_qwen_edit_conditioning_nodes(
        workflow=workflow,
        next_id=next_id,
        prompt=effective_prompt,
        negative=effective_negative,
        clip_ref=["1", 1],
        vae_ref=["1", 2],
        qwen_inputs=qwen_inputs,
        width=width,
        height=height,
        params=params,
        backend_capabilities=backend_capabilities,
    )
    route_meta.update(edit_meta)

    model_sampling_nodes: list[str] = []
    if mode == "inpaint" and source_ref is not None and mask_name:
        workflow[str(next_id)] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        mask_ref = [str(next_id), 0]
        next_id += 1
        grow = max(0, _int_param(params, "mask_grow", "grow_mask_by", default=3))
        blur = max(0, _int_param(params, "mask_blur", "blur_mask_by", default=0))
        if grow or blur:
            workflow[str(next_id)] = {
                "class_type": "GrowMaskWithBlur",
                "inputs": {"mask": list(mask_ref), "expand": grow, "incremental_expandrate": 0, "tapered_corners": True, "flip_input": False, "blur_radius": blur, "lerp_alpha": 1, "decay_factor": 1, "fill_holes": False},
            }
            mask_ref = [str(next_id), 0]
            next_id += 1
        inpaint_target = str(_param(params, "inpaint_target", "mask_mode", default="masked") or "masked").strip().lower()
        if inpaint_target in {"unmasked", "not_masked", "not_masked_area"}:
            workflow[str(next_id)] = {"class_type": "InvertMask", "inputs": {"mask": list(mask_ref)}}
            mask_ref = [str(next_id), 0]
            next_id += 1
        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["1", 2]}}
        encoded_ref = [str(next_id), 0]
        next_id += 1
        workflow[str(next_id)] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(encoded_ref), "mask": list(mask_ref)}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        differential_id = str(next_id)
        workflow[differential_id] = {"class_type": "DifferentialDiffusion", "inputs": {"model": ["1", 0]}}
        model_ref = [differential_id, 0]
        model_sampling_nodes.append(differential_id)
        next_id += 1
        route_meta.update({"mask_image_name": mask_name, "mask_grow": grow, "mask_blur": blur, "inpaint_target": inpaint_target, "_neo_qwen_aio_inpaint_source_ref": source_ref, "_neo_qwen_aio_inpaint_mask_ref": mask_ref})
        route_notes.append("Qwen Rapid AIO inpaint uses source VAEEncode + SetLatentNoiseMask + DifferentialDiffusion.")
    else:
        workflow[str(next_id)] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        model_ref = ["1", 0]

    sampler_id = str(next_id)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler if sampler != "provider_default" else defaults.sampler, "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler, "denoise": denoise, "model": model_ref, "positive": positive_ref, "negative": negative_ref, "latent_image": latent_ref},
    }
    next_id += 1
    decode_id = str(next_id)
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["1", 2]}}
    output_ref: list[Any] = [decode_id, 0]
    next_id += 1
    if mode == "inpaint" and source_ref is not None and mask_ref is not None:
        composite_id = str(next_id)
        workflow[composite_id] = {"class_type": "ImageCompositeMasked", "inputs": {"destination": list(source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": list(mask_ref)}}
        output_ref = [composite_id, 0]
        next_id += 1
    output_prefix = _qwen_output_prefix(family=job.family, mode=mode, variant="qwen_rapid_aio")
    next_id, save_output_node_id, preview_output_node_id = _append_qwen_output_handoff(
        workflow,
        next_id=next_id,
        image_ref=output_ref,
        filename_prefix=output_prefix,
    )

    actual_params = {
        **params,
        **route_meta,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.qwen_rapid_aio",
        "_neo_output_import_node_ids": [save_output_node_id],
        "_neo_output_contract": {
            "schema": "neo.image.output_contract.v1",
            "final_image_node_ids": [save_output_node_id],
            "preview_node_ids": [preview_output_node_id],
            "provider_final_output_policy": "prefer_save_image_over_preview",
            "preview_role": "live_preview_only",
            "filename_prefix": output_prefix,
        },
        "prompt_conditioning_mode": conditioning_mode,
        "clamp": conditioning_mode,
        "prompt_conditioning": {
            "mode": conditioning_mode,
            "display_mode": conditioning.get("display_mode"),
            "changed": bool(conditioning.get("changed")),
            "weighted_tags": int(conditioning.get("weighted_tags") or 0),
            "clamped_tags": int(conditioning.get("clamped_tags") or 0),
            "positive": conditioning.get("positive") or {},
            "negative": conditioning.get("negative") or {},
        },
        "qwen_rapid_aio_profile": {
            "family": "qwen_rapid_aio",
            "visible_family": job.family or "qwen_rapid_aio",
            "loader": "checkpoint_aio",
            "compiler": "comfy.qwen_rapid_aio_checkpoint",
            "enabled_modes": ["txt2img", "img2img", "inpaint", "outpaint", "edit"],
            "status": "available",
            "source_policy": "img2img/edit up to 3 source images; inpaint/outpaint single-source mask/canvas",
            "bundled_component_policy": "external_encoder_vae_mmproj_split_model_fields_pruned",
            "pruned_component_fields": sorted(pruned_component_fields),
            "checkpoint": str(checkpoint),
            "default_steps": defaults.steps,
            "default_cfg": defaults.cfg,
            "provider_nodes": {"checkpoint_loader": "CheckpointLoaderSimple", "conditioning": route_meta.get("qwen_edit_node_compatibility", {}).get("selected_node", defaults.edit_node)},
        },
        "checkpoint": str(checkpoint),
        "qwen_rapid_aio_checkpoint": str(checkpoint),
        "qwen_rapid_aio_pruned_component_fields": sorted(pruned_component_fields),
        "cfg": cfg,
        "denoise": denoise,
        "qwen_multi_reference": bool(route_meta.get("qwen_multi_reference", len(qwen_inputs) > 1)),
        "qwen_source_image_limit": int(route_meta.get("qwen_source_image_limit") or (3 if mode == "img2img" else (1 if mode in {"inpaint", "outpaint"} else 0))),
        "_neo_effective_qwen_route": True,
        "_neo_effective_mode": mode,
        "_neo_sampler_node_id": sampler_id,
    }
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["1", 1],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.qwen_rapid_aio_checkpoint",
        strategy="lora_loader_model_clip_chain",
        validated=False,
        notes=["Qwen Rapid AIO checkpoint emits a profile for diagnostics; LoRA route stays implementation_target until physically validated."],
    )

    publish_adetailer_route_contract(
        actual_params=actual_params,
        workflow=workflow,
        route=route,
        image_ref=output_ref,
        model_ref=model_ref,
        clip_ref=["1", 1],
        vae_ref=["1", 2],
        positive_ref=positive_ref,
        negative_ref=negative_ref,
        sampler_node_id=sampler_id,
        source="comfy.qwen_rapid_aio_checkpoint",
        compiler_id="comfy.qwen_rapid_aio_checkpoint",
        model_sampling_state="patched" if model_sampling_nodes else "provider_owned",
        model_sampling_ref=model_ref,
        model_sampling_nodes=model_sampling_nodes,
        notes=["Phase 5 Qwen Rapid AIO publishes bundled checkpoint refs and the final inpaint composite when present."],
    )

    return CompiledJob(
        provider_id=provider_id,
        compile_status="compiled" if validation.ok else "mock_compiled",
        backend_payload={
            "provider_id": provider_id,
            "backend": "comfyui",
            "base_url": base_url,
            "validation": model_to_dict(validation),
            "prompt": workflow,
            "client_id": f"neo-studio-v2-{uuid4().hex[:8]}",
            "actual_params": actual_params,
            "runtime_progress_source": "comfyui.websocket_and_history",
            "compile_route": route.as_dict(),
            "capabilities": capabilities,
            "backend_capabilities": backend_capabilities or {},
            "phase_notes": [
                "V25.9.20 Pass N3 completes Qwen Rapid AIO normal workflow implementation.",
                "V25.9.20 P2.1 binds qwen_rapid_aio_checkpoint to the normal Comfy checkpoint catalog, removes guessed checkpoint filenames, and rejects an unselected bundled checkpoint.",
                "Rapid AIO uses CheckpointLoaderSimple + TextEncodeQwenImageEditPlus conditioning; img2img/edit can use Image 1 plus optional Image 2/Image 3, while inpaint/outpaint stay single-source mask/canvas routes.",
                *route_notes,
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )


def compile_qwen_native_edit(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile Qwen Image Edit split diffusion-model image-conditioned routes.

    P3 promotes the earlier Qwen audit into implemented local workflows for
    normal Qwen Image Edit and Qwen Image Edit 2509/2511 safetensors/components
    routes: single-source normal Qwen img2img/edit, 2509 1-3 source
    img2img/edit, mask-based inpaint, and canvas outpaint. 2509 inpaint and
    outpaint intentionally prune to single-source mask/canvas routes.
    exists.
    """

    params = job.params or {}
    defaults = QWEN_NATIVE_EDIT_DEFAULTS
    mode = str(route.mode or job.mode or "img2img")
    if mode == "edit":
        mode = "img2img"
    if mode == "inpaint":
        params = normalize_inpaint_target_aliases(params)

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    if mode in {"img2img", "inpaint", "outpaint"} and not source_name:
        validation.errors.append(f"Qwen native {mode} requires at least one source image.")
        validation.ok = False
    if mode == "inpaint" and not mask_name:
        validation.errors.append("Qwen native inpaint requires a mask image.")
        validation.ok = False
    if mode == "outpaint":
        outpaint_payload = normalize_outpaint_payload(params, default_width=int(_param(params, "width", default=defaults.width)), default_height=int(_param(params, "height", default=defaults.height)))
        if outpaint_padding_total(outpaint_payload) <= 0:
            validation.errors.append("Qwen native outpaint requires padding on at least one side.")
            validation.ok = False

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    visible_family = str(job.family or "qwen_image")
    diffusion_model = require_explicit_asset_selection(
        validation,
        f"{visible_family.replace('_', ' ')} diffusion model",
        job.model, params.get("qwen_image_edit_model"), params.get("diffusion_model"), params.get("model"), params.get("model_name"),
    )
    text_encoder = require_explicit_asset_selection(
        validation,
        "Qwen Image text encoder",
        params.get("qwen_text_encoder"), params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name"),
    )
    vae = require_explicit_asset_selection(
        validation,
        "Qwen Image VAE",
        params.get("vae"), params.get("qwen_vae"), params.get("vae_or_ae"),
    )
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    steps = int(_param(params, "steps", default=defaults.steps))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    cfg = float(_param(params, "cfg", default=defaults.cfg))
    denoise = float(_param(params, "denoise", "strength", default=defaults.denoise))
    aura_shift = float(_param(params, "qwen_aura_shift", "aura_shift", "shift", default=defaults.aura_shift))
    qwen_cfgnorm_enabled = _truthy_param(params, "qwen_cfgnorm_enabled", default=True)
    qwen_cfgnorm_strength = float(_param(params, "qwen_cfgnorm_strength", "cfgnorm_strength", default=1.0))
    qwen_cfgnorm_pre_cfg = _truthy_param({**params, "qwen_cfgnorm_pre_cfg": params.get("qwen_cfgnorm_pre_cfg", params.get("pre_cfg"))}, "qwen_cfgnorm_pre_cfg", default=False)
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    requested_clip_loader = _param(params, "clip_loader", "text_encoder_loader", "qwen_clip_loader", default="")
    clip_loader = _qwen_native_edit_clip_loader_for_encoder(text_encoder, requested_clip_loader)
    text_encoder_file_type = "gguf" if _is_gguf_file(text_encoder) else "native"
    if clip_loader != "CLIPLoader" and backend_capabilities is not None and not _backend_supports_qwen_mixed_clip_loader(backend_capabilities, clip_loader, clip_type=defaults.clip_type):
        validation.errors.append(f"Selected Qwen text encoder '{text_encoder}' needs {clip_loader}(type={defaults.clip_type}) in ComfyUI.")
        validation.ok = False
    vae_decode_strategy = resolve_vae_decode_strategy(
        params=params,
        family=visible_family,
        loader=route.loader or job.loader or "diffusion_model",
        mode=mode,
        backend_capabilities=backend_capabilities,
        validation=validation,
    )

    workflow: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": diffusion_model, "weight_dtype": weight_dtype}},
        "2": {"class_type": clip_loader, "inputs": {"clip_name": text_encoder, "type": defaults.clip_type, "device": clip_device}},
        "3": build_vae_loader_node(vae, vae_decode_strategy),
    }

    route_notes: list[str] = []
    route_meta: dict[str, Any] = {}
    if mode == "img2img":
        _require_qwen_parity_node(
            validation,
            backend_capabilities,
            "FluxKontextImageScale",
            reason="img2img source preprocessing must match the native Qwen edit graph instead of silently bypassing image scaling.",
        )
    _require_qwen_parity_node(
        validation,
        backend_capabilities,
        defaults.sampling_node,
        reason="the selected Qwen route uses ModelSamplingAuraFlow before KSampler.",
    )
    if qwen_cfgnorm_enabled:
        _require_qwen_parity_node(
            validation,
            backend_capabilities,
            "CFGNorm",
            reason="CFGNorm is enabled in Neo Parameters and must not be silently dropped.",
        )
    max_source_images = 3 if visible_family in {"qwen_image_edit_2509", "qwen_image_edit_2511"} and mode == "img2img" else 1
    next_id, qwen_inputs, notes, image_meta = _load_qwen_source_images(workflow, params, start_id=20, max_images=max_source_images, backend_capabilities=backend_capabilities)
    route_notes.extend(notes)
    route_meta.update(image_meta)
    if mode == "img2img" and visible_family in {"qwen_image_edit_2509", "qwen_image_edit_2511"} and len(qwen_inputs) >= 1:
        _require_qwen_parity_node(
            validation,
            backend_capabilities,
            "FluxKontextMultiReferenceLatentMethod",
            reason="Qwen 2509/2511 native img2img requires index_timestep_zero conditioning on both prompt branches, including single-source edits.",
        )
    source_ref: list[Any] | None = list(qwen_inputs.get("image1") or []) or None
    original_source_ref: list[Any] | None = list(source_ref) if source_ref else None
    mask_ref: list[Any] | None = None

    if mode == "outpaint" and source_ref is not None:
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        padding = outpaint_payload["padding"]
        mask = outpaint_payload["mask"]
        left = int(padding.get("left", 0) or 0)
        top = int(padding.get("top", 0) or 0)
        right = int(padding.get("right", 0) or 0)
        bottom = int(padding.get("bottom", 0) or 0)
        feather = int(mask.get("feather", 16) or 16)
        next_id, source_ref, working_width, working_height, source_scale_meta = _insert_outpaint_source_scale_node(
            workflow, next_id, source_ref, outpaint_payload, fallback_width=width, fallback_height=height
        )
        workflow[str(next_id)] = {
            "class_type": "ImagePadForOutpaint",
            "inputs": {"image": list(source_ref), "left": left, "top": top, "right": right, "bottom": bottom, "feathering": feather},
        }
        source_ref = [str(next_id), 0]
        qwen_inputs["image1"] = list(source_ref)
        next_id += 1
        width = max(64, int(working_width) + left + right)
        height = max(64, int(working_height) + top + bottom)
        route_meta.update({
            "outpaint_payload": outpaint_payload,
            "_neo_outpaint_contract": outpaint_payload,
            "qwen_native_outpaint_base_size": {"width": int(working_width), "height": int(working_height)},
            "qwen_native_outpaint_padding": {"left": left, "top": top, "right": right, "bottom": bottom, "feather": feather, "blur": int(mask.get("blur", 8) or 8)},
            "qwen_native_outpaint_effective_size": {"width": width, "height": height},
            "qwen_native_outpaint_source_resolution": outpaint_payload.get("source_resolution", {}),
            "qwen_native_outpaint_source_scale_node": source_scale_meta or {},
        })
        scale_note = " with source working-copy scale" if source_scale_meta else ""
        route_notes.append(f"Qwen native outpaint uses{scale_note} ImagePadForOutpaint and a padded latent canvas {width}x{height}.")

    next_id, positive_ref, negative_ref, edit_meta = _build_qwen_edit_conditioning_nodes(
        workflow=workflow,
        next_id=next_id,
        prompt=effective_prompt,
        negative=effective_negative,
        clip_ref=["2", 0],
        vae_ref=["3", 0],
        qwen_inputs=qwen_inputs,
        width=width,
        height=height,
        params=params,
        backend_capabilities=backend_capabilities,
    )
    route_meta.update(edit_meta)
    if mode == "img2img" and visible_family in {"qwen_image_edit_2509", "qwen_image_edit_2511"} and len(qwen_inputs) >= 1:
        next_id, positive_ref, positive_mref_meta = _append_qwen_multi_reference_latent_method(
            workflow,
            next_id,
            positive_ref,
            backend_capabilities=backend_capabilities,
        )
        next_id, negative_ref, negative_mref_meta = _append_qwen_multi_reference_latent_method(
            workflow,
            next_id,
            negative_ref,
            backend_capabilities=backend_capabilities,
        )
        if positive_mref_meta and negative_mref_meta:
            route_meta["qwen_multi_reference_latent_method"] = {
                "enabled": True,
                "method": positive_mref_meta.get("method", "index_timestep_zero"),
                "positive_node_id": positive_mref_meta.get("node_id"),
                "negative_node_id": negative_mref_meta.get("node_id"),
            }
            route_notes.append("Qwen native 2509/2511 img2img uses FluxKontextMultiReferenceLatentMethod(index_timestep_zero) on both prompt branches for single- and multi-source edits.")

    aura_sampling_id = str(next_id)
    workflow[aura_sampling_id] = {"class_type": defaults.sampling_node, "inputs": {"model": ["1", 0], "shift": aura_shift}}
    model_ref = [aura_sampling_id, 0]
    model_sampling_nodes = [aura_sampling_id]
    next_id += 1
    qwen_cfgnorm_applied = False
    if qwen_cfgnorm_enabled and _qwen_node_available_or_unknown(backend_capabilities, "CFGNorm"):
        cfgnorm_id = str(next_id)
        workflow[cfgnorm_id] = {"class_type": "CFGNorm", "inputs": {"model": list(model_ref), "strength": qwen_cfgnorm_strength, "pre_cfg": qwen_cfgnorm_pre_cfg}}
        model_ref = [cfgnorm_id, 0]
        model_sampling_nodes.append(cfgnorm_id)
        qwen_cfgnorm_applied = True
        next_id += 1
        route_notes.append(f"Qwen native sampling inserts CFGNorm(strength={qwen_cfgnorm_strength}, pre_cfg={qwen_cfgnorm_pre_cfg}).")

    if mode == "inpaint" and source_ref is not None and mask_name:
        workflow[str(next_id)] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        mask_ref = [str(next_id), 0]
        next_id += 1
        grow = max(0, _int_param(params, "mask_grow", "grow_mask_by", default=3))
        blur = max(0, _int_param(params, "mask_blur", "blur_mask_by", default=0))
        if grow or blur:
            workflow[str(next_id)] = {
                "class_type": "GrowMaskWithBlur",
                "inputs": {"mask": list(mask_ref), "expand": grow, "incremental_expandrate": 0, "tapered_corners": True, "flip_input": False, "blur_radius": blur, "lerp_alpha": 1, "decay_factor": 1, "fill_holes": False},
            }
            mask_ref = [str(next_id), 0]
            next_id += 1
        inpaint_target = str(_param(params, "inpaint_target", "mask_mode", default="masked") or "masked").strip().lower()
        if inpaint_target in {"unmasked", "not_masked", "not_masked_area"}:
            workflow[str(next_id)] = {"class_type": "InvertMask", "inputs": {"mask": list(mask_ref)}}
            mask_ref = [str(next_id), 0]
            next_id += 1
        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["3", 0]}}
        encoded_ref = [str(next_id), 0]
        next_id += 1
        workflow[str(next_id)] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(encoded_ref), "mask": list(mask_ref)}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        differential_id = str(next_id)
        workflow[differential_id] = {"class_type": "DifferentialDiffusion", "inputs": {"model": list(model_ref)}}
        model_ref = [differential_id, 0]
        model_sampling_nodes.append(differential_id)
        next_id += 1
        route_meta.update({
            "mask_image_name": mask_name,
            "mask_grow": grow,
            "mask_blur": blur,
            "inpaint_target": inpaint_target,
            "_neo_qwen_native_inpaint_source_ref": list(source_ref),
            "_neo_qwen_native_inpaint_mask_ref": list(mask_ref),
            "_neo_qwen_native_inpaint_uses_latent_noise_mask": True,
            "_neo_qwen_native_inpaint_uses_differential_diffusion": True,
            "_neo_qwen_native_inpaint_final_composite": True,
        })
        route_notes.append("Qwen native inpaint uses source VAEEncode + SetLatentNoiseMask + ModelSamplingAuraFlow + DifferentialDiffusion.")
    else:
        if mode in {"img2img", "outpaint"} and source_ref is not None:
            workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["3", 0]}}
            latent_ref = [str(next_id), 0]
            next_id += 1
            route_meta["qwen_source_latent_mode"] = "vae_encode_image1"
            route_notes.append("Qwen native img2img/outpaint uses Image 1 as the VAE latent anchor instead of an empty latent canvas.")
        else:
            workflow[str(next_id)] = {"class_type": defaults.latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}}
            latent_ref = [str(next_id), 0]
            next_id += 1

    if batch_count > 1 and mode in {"img2img", "inpaint", "outpaint"}:
        workflow[str(next_id)] = {"class_type": "RepeatLatentBatch", "inputs": {"samples": list(latent_ref), "amount": batch_count}}
        latent_ref = [str(next_id), 0]
        next_id += 1

    sampler_id = str(next_id)
    workflow[sampler_id] = {"class_type": "KSampler", "inputs": {"seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler if sampler != "provider_default" else defaults.sampler, "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler, "denoise": denoise, "model": model_ref, "positive": positive_ref, "negative": negative_ref, "latent_image": latent_ref}}
    next_id += 1
    decode_id = str(next_id)
    workflow[decode_id] = build_vae_decode_node([sampler_id, 0], ["3", 0], vae_decode_strategy)
    output_ref: list[Any] = [decode_id, 0]
    next_id += 1

    if mode == "inpaint" and original_source_ref is not None and mask_ref is not None:
        composite_id = str(next_id)
        workflow[composite_id] = {"class_type": "ImageCompositeMasked", "inputs": {"destination": list(original_source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": list(mask_ref)}}
        output_ref = [composite_id, 0]
        next_id += 1
    output_prefix = _qwen_output_prefix(family=job.family, mode=mode, variant="qwen_native_edit")
    next_id, save_output_node_id, preview_output_node_id = _append_qwen_output_handoff(
        workflow,
        next_id=next_id,
        image_ref=output_ref,
        filename_prefix=output_prefix,
    )

    effective_source_limit = max_source_images
    actual_params = {
        **params,
        **route_meta,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.qwen_native_edit",
        "_neo_output_import_node_ids": [save_output_node_id],
        "_neo_output_contract": {
            "schema": "neo.image.output_contract.v1",
            "final_image_node_ids": [save_output_node_id],
            "preview_node_ids": [preview_output_node_id],
            "provider_final_output_policy": "prefer_save_image_over_preview",
            "preview_role": "live_preview_only",
            "filename_prefix": output_prefix,
        },
        "prompt_conditioning_mode": conditioning_mode,
        "clamp": conditioning_mode,
        "prompt_conditioning": {"mode": conditioning_mode, "display_mode": conditioning.get("display_mode"), "changed": bool(conditioning.get("changed")), "weighted_tags": int(conditioning.get("weighted_tags") or 0), "clamped_tags": int(conditioning.get("clamped_tags") or 0), "positive": conditioning.get("positive") or {}, "negative": conditioning.get("negative") or {}},
        "qwen_native_edit_profile": {
            "family": "qwen_image",
            "visible_family": visible_family,
            "loader": "diffusion_model",
            "compiler": "comfy.qwen_native_edit",
            "enabled_modes": ["img2img", "edit", "inpaint", "outpaint"],
            "source_image_limit": effective_source_limit,
            "source_image_limit_policy": "2509/2511 allow 1-3 sources for img2img/edit only; inpaint/outpaint are single-source mask/canvas workflows.",
            "status": "available",
            "provider_nodes": {"diffusion_model_loader": "UNETLoader", "text_encoder_loader": clip_loader, "requested_text_encoder_loader": requested_clip_loader or clip_loader, "conditioning": route_meta.get("qwen_edit_node_compatibility", {}).get("selected_node", "TextEncodeQwenImageEditPlus"), "multi_reference_latent_method": route_meta.get("qwen_multi_reference_latent_method", {}).get("enabled") and "FluxKontextMultiReferenceLatentMethod" or "disabled", "source_preprocess": route_meta.get("qwen_source_preprocess_node") or "disabled", "sampling_patch": defaults.sampling_node, "cfgnorm": "CFGNorm" if qwen_cfgnorm_applied else "disabled", "vae_loader": vae_decode_strategy["loader_node_class"], "vae_decode": vae_decode_strategy["decode_node_class"]},
            "text_encoder_policy": "single_qwen_mixed_loader",
            "text_encoder_file_type": text_encoder_file_type,
            "loader_rule": "Qwen safetensors/components edit routes use CLIPLoader(type=qwen_image) for native encoders and CLIPLoaderGGUF(type=qwen_image) for GGUF encoders.",
            "img2img_parity_contract": "Img2img/outpaint use Image 1 as the VAE latent anchor; 2509/2511 img2img applies FluxKontextMultiReferenceLatentMethod(index_timestep_zero) for single- and multi-source edits.",
        },
        "vae_decode_mode": vae_decode_strategy["resolved"],
        "vae_decode_profile": vae_decode_profile_payload(vae_decode_strategy),
        "diffusion_model": diffusion_model,
        "qwen_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "gguf_text_encoder_primary": text_encoder if text_encoder_file_type == "gguf" else params.get("gguf_text_encoder_primary", ""),
        "_neo_effective_qwen_text_encoder_loader": clip_loader,
        "vae": vae,
        "qwen_aura_shift": aura_shift,
        "qwen_cfgnorm_enabled": qwen_cfgnorm_enabled,
        "qwen_cfgnorm_strength": qwen_cfgnorm_strength,
        "qwen_cfgnorm_pre_cfg": qwen_cfgnorm_pre_cfg,
        "qwen_clip_loader_mode": str(_param(params, "qwen_clip_loader_mode", default="auto") or "auto"),
        "qwen_requested_clip_loader": requested_clip_loader or "auto",
        "cfg": cfg,
        "denoise": denoise,
        "qwen_multi_reference": effective_source_limit > 1,
        "qwen_source_image_limit": effective_source_limit,
        "_neo_effective_qwen_route": True,
        "_neo_effective_mode": mode,
        "_neo_sampler_node_id": sampler_id,
    }
    is_qwen_edit_family = str(job.family or "").strip().lower() in {"qwen_image_edit_2509", "qwen_image_edit_2511"}
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoaderModelOnly" if is_qwen_edit_family else "LoraLoader",
        requires_clip=not is_qwen_edit_family,
        source="comfy.qwen_native_edit",
        strategy="lora_loader_model_only_consumer_rewire" if is_qwen_edit_family else "lora_loader_model_clip_consumer_rewire",
        patch_clip_consumers=not is_qwen_edit_family,
        validated=False,
        notes=["Qwen Edit 2509/2511 detailer LoRAs are model-only and must be followed by the compiler-owned AuraFlow/DifferentialDiffusion lineage." if is_qwen_edit_family else "Qwen Image Base edit retains its existing LoRA strategy."],
    )

    publish_adetailer_route_contract(
        actual_params=actual_params,
        workflow=workflow,
        route=route,
        image_ref=output_ref,
        model_ref=model_ref,
        clip_ref=["2", 0],
        vae_ref=["3", 0],
        positive_ref=positive_ref,
        negative_ref=negative_ref,
        sampler_node_id=sampler_id,
        source="comfy.qwen_native_edit",
        compiler_id="comfy.qwen_native_edit",
        model_sampling_state="patched",
        model_sampling_ref=model_ref,
        model_sampling_nodes=model_sampling_nodes,
        notes=["Phase 6 Qwen Edit 2509/2511 route publishes exact AuraFlow/DifferentialDiffusion lineage and final image ownership." if is_qwen_edit_family else "Phase 5 Qwen Image Base edit route publishes exact AuraFlow/DifferentialDiffusion lineage and final image ownership."],
    )

    return CompiledJob(
        provider_id=provider_id,
        compile_status="compiled" if validation.ok else "mock_compiled",
        backend_payload={
            "provider_id": provider_id,
            "backend": "comfyui",
            "base_url": base_url,
            "validation": model_to_dict(validation),
            "prompt": workflow,
            "client_id": f"neo-studio-v2-{uuid4().hex[:8]}",
            "actual_params": actual_params,
            "runtime_progress_source": "comfyui.websocket_and_history",
            "compile_route": route.as_dict(),
            "capabilities": capabilities,
            "backend_capabilities": backend_capabilities or {},
            "phase_notes": [
                "V25.9.20 P3 promotes Qwen native safetensors/components image-conditioned workflows as real selectable routes instead of planned gates.",
                "Qwen native edit uses UNETLoader + CLIPLoader(type=qwen_image) + TextEncodeQwenImageEditPlus + ModelSamplingAuraFlow.",
                "Native inpaint uses source VAEEncode + SetLatentNoiseMask + DifferentialDiffusion + final masked composite; native outpaint uses ImagePadForOutpaint + padded latent canvas.",
                f"VAE decode path: {vae_decode_strategy['decode_node_class']}.",
                *route_notes,
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
