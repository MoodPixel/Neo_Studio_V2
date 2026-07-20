from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.image.inpaint_payload import normalize_inpaint_target_aliases
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_app.providers.comfy_workflows.qwen_stitch_route import apply_qwen_stitch_route
from neo_app.image.qwen_stitch_contract import extract_qwen_stitch_payload, qwen_stitch_has_ready_group
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile


@dataclass(frozen=True)
class QwenGGUFDefaults:
    """Provider compiler defaults for the first Qwen Image GGUF txt2img route.

    Phase 12.12 enables text-to-image only. Image-conditioned Qwen routes
    remain mmproj/source-image gated for Phase 12.13.
    """

    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg: float = 4.0
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    latent_node: str = "EmptyLatentImage"
    unet_loader: str = "UnetLoaderGGUF"
    clip_loader: str = "CLIPLoaderGGUF"
    clip_type: str = "qwen_image"
    clip_device: str = "default"


QWEN_GGUF_DEFAULTS = QwenGGUFDefaults()

# Phase 12.30 stability preset for Qwen Image GGUF semantic-edit routes.
# These values are only defaults; explicit user/runtime params still win.
QWEN_GGUF_INPAINT_DEFAULT_STEPS = 8
QWEN_GGUF_INPAINT_DEFAULT_DENOISE = 0.90
QWEN_GGUF_INPAINT_DEFAULT_MASK_GROW = 3
QWEN_GGUF_INPAINT_DEFAULT_MASK_BLUR = 0
QWEN_GGUF_INPAINT_DEFAULT_COMPOSITE_FEATHER = 10



def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _role_backend_node(capabilities: dict[str, Any], loader: str, role: str) -> str | None:
    loaders = capabilities.get("loaders") if isinstance(capabilities, dict) else None
    loader_payload = (loaders or {}).get(loader) if isinstance(loaders, dict) else None
    roles = (loader_payload or {}).get("roles") if isinstance(loader_payload, dict) else None
    role_payload = (roles or {}).get(role) if isinstance(roles, dict) else None
    if isinstance(role_payload, dict):
        value = role_payload.get("backend_node") or role_payload.get("backend_key")
        return str(value).strip() if value else None
    return None


def _normalize_gguf_unet_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"UnetLoaderGGUF", "LoaderGGUF"} else QWEN_GGUF_DEFAULTS.unet_loader


def _normalize_gguf_clip_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"CLIPLoaderGGUF", "ClipLoaderGGUF"} else QWEN_GGUF_DEFAULTS.clip_loader


def _gguf_unet_inputs(loader_class: str, model_name: str) -> dict[str, Any]:
    # city96/ComfyUI-GGUF has exposed both names across builds. LoaderGGUF uses
    # gguf_name while UnetLoaderGGUF uses unet_name.
    if loader_class == "LoaderGGUF":
        return {"gguf_name": model_name}
    return {"unet_name": model_name}


def _vae_loader_for(vae_name: str) -> str:
    return "VaeGGUF" if str(vae_name or "").lower().endswith(".gguf") else "VAELoader"



def _int_param(params: dict[str, Any], name: str, default: int = 0) -> int:
    try:
        return int(params.get(name, default) or 0)
    except (TypeError, ValueError):
        return int(default)


QWEN_EDIT_NODE_CANDIDATES = (
    "TextEncodeQwenImageEditPlus",
    "TextEncodeQwenImageEditPlus_lrzjason",
    "TextEncodeQwenImageEditPlusAdvance_lrzjason",
    "TextEncodeQwenImageEditPlusPro_lrzjason",
)


def _qwen_object_info_node_inputs(backend_capabilities: dict[str, Any] | None, node_name: str) -> dict[str, Any]:
    node_inputs = ((backend_capabilities or {}).get("object_info_node_inputs") or {}).get(node_name) or {}
    return node_inputs if isinstance(node_inputs, dict) else {}


def _qwen_edit_node_input_names(backend_capabilities: dict[str, Any] | None, node_name: str = "TextEncodeQwenImageEditPlus") -> set[str]:
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
    if requested and requested in available:
        return requested
    # Prefer the built-in/core node as the stable default. If the user installed
    # a custom lrzjason node and explicitly selects it later, the compiler can use
    # the same conditioning output contract. We do not silently swap nodes because
    # custom nodes may encode resize/crop differently.
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
    largest = max(int(width or 0), int(height or 0), QWEN_GGUF_DEFAULTS.width)
    # Rapid-AIO guidance commonly uses a target slightly below the final long edge
    # (example: 896 for a 1024px output). Keep it divisible by 32 for VAE safety.
    estimated = int((largest * 0.875) // 32) * 32
    return max(512, min(1536, estimated or 896))


def _truthy_param(params: dict[str, Any], name: str, default: bool = False) -> bool:
    value = params.get(name)
    if value in (None, ""):
        return bool(default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in {"0", "false", "no", "off", "disabled"}


def _qwen_edit_plus_extra_inputs(params: dict[str, Any], width: int, height: int, backend_capabilities: dict[str, Any] | None, node_name: str = "TextEncodeQwenImageEditPlus") -> dict[str, Any]:
    supported_inputs = _qwen_edit_node_input_names(backend_capabilities, node_name)
    target_size = _qwen_edit_target_size(params, width, height)
    extra: dict[str, Any] = {}

    # M4 audit correction: do not force an undeclared `size` input. The repeated
    # runtime failure proved the error is inside the Comfy Qwen node implementation
    # when that node references a missing local variable. Prompt extras cannot fix
    # a Python NameError inside the node. Only send sizing inputs that the live
    # backend actually advertises, unless the user explicitly forces a diagnostic.
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
        "note": "If Comfy raises `size is not defined`, the Qwen edit node file is incompatible/half-patched; update or repair comfy_extras/nodes_qwen.py and restart ComfyUI.",
    }


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




def _named_image_value(params: dict[str, Any], *names: str) -> str:
    for name in names:
        value = params.get(name)
        if isinstance(value, dict):
            value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
        text = str(value or "").strip()
        if text:
            return text.split("/")[-1].split("\\")[-1]
    return ""


def _qwen_img2img_reference_image_names(params: dict[str, Any], base_source_name: str) -> tuple[str, str, str, dict[str, Any]]:
    """Return V1-style Qwen img2img source lanes: image1 base, image2 reference, image3 composition.

    V1 fed TextEncodeQwenImageEditPlus up to three images for Qwen image edit:
    base image, optional reference image 2, and a composition/guide lane. Pass N3
    defaults Image 3 to the uploaded composition image when provided; otherwise
    it safely falls back to the base image. V2 keeps this provider-owned and only exposes the extra
    lanes for Qwen GGUF img2img so checkpoint/Flux routes cannot receive stale
    hidden values.
    """
    ref2 = _named_image_value(
        params,
        "comfy_source_image_2_name",
        "source_image_2_name",
        "source_image__2_name",
        "reference_image_2_name",
        "source_image_2",
        "source_image_2_path",
        "source_image_2_url",
    )
    comp = _named_image_value(
        params,
        "comfy_source_image_3_name",
        "source_image_3_name",
        "source_image__3_name",
        "composition_image_name",
        "reference_image_3_name",
        "source_image_3",
        "source_image_3_path",
        "source_image_3_url",
    )
    default_composition_mode = "composition_image" if comp else "source_image"
    composition_source_mode = str(params.get("composition_source_mode") or params.get("qwen_composition_source_mode") or default_composition_mode).strip().lower() or default_composition_mode
    if composition_source_mode not in {"source_image", "composition_image"}:
        composition_source_mode = default_composition_mode
    image3 = comp if composition_source_mode == "composition_image" and comp else base_source_name
    meta = {
        "qwen_multi_reference": True,
        "qwen_source_images": {
            "base_image_name": base_source_name,
            "reference_image_2_name": ref2,
            "composition_image_name": comp,
            "composition_source_mode": composition_source_mode,
            "image3_effective_name": image3,
        },
    }
    return base_source_name, ref2, image3, meta



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
        or params.get("mask_image_path")
        or params.get("inpaint_mask")
        or params.get("mask")
    )
    if isinstance(value, dict):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
    return str(value or "").strip().split("/")[-1].split("\\")[-1]





def _build_qwen_image_conditioning_and_latent(
    *,
    workflow: dict[str, Any],
    mode: str,
    params: dict[str, Any],
    width: int,
    height: int,
    batch_count: int,
    vae_ref: list[Any],
    backend_capabilities: dict[str, Any] | None = None,
    family: str = "qwen_image",
) -> tuple[str, list[Any] | None, dict[str, Any], list[str]]:
    """Return (next_id, sampler_latent_ref, image_condition_ref, notes)."""

    notes: list[str] = []
    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    next_id = 10
    if mode == "txt2img":
        workflow["6"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        return "7", ["6", 0], {}, notes

    stitch_only_ready = mode == "img2img" and qwen_stitch_has_ready_group(extract_qwen_stitch_payload(params))
    if not source_name and not stitch_only_ready:
        raise ValueError(f"Qwen GGUF {mode} requires a source image.")

    source_ref: list[Any] | None = None
    qwen_image_ref: list[Any] | None = None
    if source_name:
        workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
        source_ref = [str(next_id), 0]
        next_id += 1
        qwen_image_ref = list(source_ref)

    source_size_ref: list[Any] | None = None
    if mode == "inpaint":
        workflow[str(next_id)] = {"class_type": "GetImageSize", "inputs": {"image": list(source_ref)}}
        source_size_ref = [str(next_id), 0]
        next_id += 1

    metadata: dict[str, Any] = {
        "source_image_name": source_name,
        "qwen_mmproj_required": True,
    }

    def attach_stitch_route(current_next_id: int, current_qwen_inputs: dict[str, list[Any]]) -> tuple[int, dict[str, list[Any]]]:
        stitched_next_id, stitched_inputs, stitch_meta, stitch_notes, stitch_errors = apply_qwen_stitch_route(
            workflow,
            params,
            current_qwen_inputs,
            current_next_id,
            family=str(family or "qwen_image"),
            loader="gguf",
            mode=mode,
            backend_capabilities=backend_capabilities,
        )
        if stitch_meta.get("enabled") or stitch_meta.get("groups"):
            metadata["qwen_stitch"] = stitch_meta
        notes.extend(stitch_notes)
        notes.extend(f"Stitch Images: {error}" for error in stitch_errors)
        return stitched_next_id, stitched_inputs

    if mode == "outpaint":
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        padding = outpaint_payload["padding"]
        mask = outpaint_payload["mask"]
        left = int(padding.get("left", 0) or 0)
        top = int(padding.get("top", 0) or 0)
        right = int(padding.get("right", 0) or 0)
        bottom = int(padding.get("bottom", 0) or 0)
        feather = int(mask.get("feather", 16) or 16)
        if outpaint_padding_total(outpaint_payload) <= 0:
            raise ValueError("Qwen GGUF outpaint requires padding on at least one side.")
        next_id, source_ref, working_width, working_height, source_scale_meta = _insert_outpaint_source_scale_node(
            workflow, next_id, source_ref, outpaint_payload, fallback_width=width, fallback_height=height
        )
        workflow[str(next_id)] = {
            "class_type": "ImagePadForOutpaint",
            "inputs": {"image": list(source_ref), "left": left, "top": top, "right": right, "bottom": bottom, "feathering": feather},
        }
        qwen_image_ref = [str(next_id), 0]
        next_id += 1
        effective_width = max(64, int(working_width) + left + right)
        effective_height = max(64, int(working_height) + top + bottom)
        workflow[str(next_id)] = {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": effective_width, "height": effective_height, "batch_size": batch_count},
        }
        metadata.update({
            "qwen_outpaint_base_size": {"width": int(working_width), "height": int(working_height)},
            "outpaint_payload": outpaint_payload,
            "_neo_outpaint_contract": outpaint_payload,
            "qwen_outpaint_source_resolution": outpaint_payload.get("source_resolution", {}),
            "qwen_outpaint_source_scale_node": source_scale_meta or {},
            "qwen_outpaint_padding": {"left": left, "top": top, "right": right, "bottom": bottom, "feather": feather, "blur": int(mask.get("blur", 8) or 8)},
            "qwen_outpaint_effective_size": {"width": effective_width, "height": effective_height},
        })
        scale_note = " + source working-copy scale" if source_scale_meta else ""
        notes.append(f"Qwen GGUF outpaint branch engaged: {scale_note} ImagePadForOutpaint + effective latent canvas {effective_width}x{effective_height}.")
        latent_ref = [str(next_id), 0]
        next_id, stitched_inputs = attach_stitch_route(next_id + 1, {"image1": qwen_image_ref})
        return str(next_id), latent_ref, stitched_inputs, metadata | {"notes": notes}

    if mode == "inpaint":
        if not mask_name:
            raise ValueError("Qwen GGUF inpaint requires a mask image.")
        workflow[str(next_id)] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        mask_ref: list[Any] = [str(next_id), 0]
        next_id += 1
        grow = max(0, _int_param(params, "mask_grow", _int_param(params, "grow_mask_by", QWEN_GGUF_INPAINT_DEFAULT_MASK_GROW)))
        blur = max(0, _int_param(params, "mask_blur", _int_param(params, "blur_mask_by", QWEN_GGUF_INPAINT_DEFAULT_MASK_BLUR)))
        if grow or blur:
            workflow[str(next_id)] = {
                "class_type": "GrowMaskWithBlur",
                "inputs": {
                    "mask": list(mask_ref),
                    "expand": grow,
                    "incremental_expandrate": 0,
                    "tapered_corners": True,
                    "flip_input": False,
                    "blur_radius": blur,
                    "lerp_alpha": 1,
                    "decay_factor": 1,
                    "fill_holes": False,
                },
            }
            mask_ref = [str(next_id), 0]
            next_id += 1

        # Keep V1 mask sizing behavior inside the provider workflow. The UI/editor
        # can keep its unified overlay/brush behavior while the Comfy graph normalizes
        # the saved mask to the loaded source image before the final composite.
        if source_size_ref is not None:
            workflow[str(next_id)] = {"class_type": "MaskToImage", "inputs": {"mask": list(mask_ref)}}
            mask_image_ref = [str(next_id), 0]
            next_id += 1
            workflow[str(next_id)] = {
                "class_type": "ImageScale",
                "inputs": {
                    "image": list(mask_image_ref),
                    "upscale_method": "nearest-exact",
                    "width": list(source_size_ref),
                    "height": [source_size_ref[0], 1],
                    "crop": "center",
                },
            }
            scaled_mask_image_ref = [str(next_id), 0]
            next_id += 1
            workflow[str(next_id)] = {"class_type": "ImageToMask", "inputs": {"image": list(scaled_mask_image_ref), "channel": "red"}}
            mask_ref = [str(next_id), 0]
            next_id += 1

        inpaint_target = str(_param(params, "inpaint_target", "mask_mode", default="masked") or "masked").strip().lower()
        if inpaint_target == "unmasked":
            workflow[str(next_id)] = {"class_type": "InvertMask", "inputs": {"mask": list(mask_ref)}}
            mask_ref = [str(next_id), 0]
            next_id += 1

        # Phase 12.30 route lock: Qwen GGUF inpaint must put the mask inside the
        # sampler path, not only paste a full-frame Qwen edit through a final
        # ImageCompositeMasked node. The stable non-LanPaint route encodes the
        # source, attaches the normalized mask as a latent noise mask, samples
        # through DifferentialDiffusion + normal KSampler, then uses a feathered
        # final ImageCompositeMasked guard.
        workflow[str(next_id)] = {
            "class_type": "VAEEncode",
            "inputs": {
                "pixels": list(source_ref),
                "vae": list(vae_ref),
            },
        }
        encoded_ref: list[Any] = [str(next_id), 0]
        next_id += 1
        workflow[str(next_id)] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {
                "samples": list(encoded_ref),
                "mask": list(mask_ref),
            },
        }
        sampler_ref: list[Any] = [str(next_id), 0]
        next_id += 1
        metadata.update({
            "mask_image_name": mask_name,
            "mask_grow": grow,
            "mask_blur": blur,
            "_neo_qwen_inpaint_source_ref": list(source_ref),
            "_neo_qwen_inpaint_mask_ref": list(mask_ref),
            "_neo_qwen_inpaint_uses_latent_noise_mask": True,
            "_neo_qwen_inpaint_uses_empty_latent": False,
            "_neo_qwen_inpaint_sampler": "KSampler",
            "_neo_qwen_inpaint_uses_differential_diffusion": True,
            "_neo_qwen_inpaint_final_composite": True,
            "_neo_qwen_inpaint_uses_feathered_final_composite": True,
            "_neo_qwen_inpaint_composite_feather": max(0, _int_param(params, "qwen_inpaint_composite_feather", _int_param(params, "composite_feather", QWEN_GGUF_INPAINT_DEFAULT_COMPOSITE_FEATHER))),
            "_neo_qwen_inpaint_mask_normalized_to_source": True,
            "inpaint_target": inpaint_target,
        })
        notes.append("Qwen GGUF inpaint branch engaged: source VAEEncode + normalized SetLatentNoiseMask + DifferentialDiffusion + normal KSampler + feathered final masked composite guard; LanPaint is not used.")
        next_id, stitched_inputs = attach_stitch_route(next_id, {"image1": qwen_image_ref} if qwen_image_ref else {})
        return str(next_id), sampler_ref, stitched_inputs, metadata | {"notes": notes}

    # Pass F: normal qwen_image is single-source. Multi-source Qwen editing is
    # intentionally reserved for a separate qwen_image_edit_2509 family pass.
    qwen_inputs: dict[str, list[Any]] = {"image1": qwen_image_ref} if qwen_image_ref else {}
    multi_source_allowed = str(family or "qwen_image") in {"qwen_rapid_aio", "qwen_image_edit_2509"}
    if mode == "img2img" and multi_source_allowed:
        _base_name, ref2_name, image3_name, ref_meta = _qwen_img2img_reference_image_names(params, source_name)
        if ref2_name:
            workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": ref2_name, "upload": "image"}}
            qwen_inputs["image2"] = [str(next_id), 0]
            next_id += 1
        if image3_name:
            if image3_name == source_name:
                qwen_inputs["image3"] = list(source_ref)
            elif image3_name == ref2_name and "image2" in qwen_inputs:
                qwen_inputs["image3"] = list(qwen_inputs["image2"])
            else:
                workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": image3_name, "upload": "image"}}
                qwen_inputs["image3"] = [str(next_id), 0]
                next_id += 1
        metadata.update(ref_meta)
        notes.append("Qwen GGUF img2img multi-reference branch engaged for a multi-source-capable Qwen family: image1 base + optional image2 reference + image3 composition/source lane.")
    elif mode == "img2img":
        ref2_name = _named_image_value(params, "comfy_source_image_2_name", "source_image_2_name", "source_image__2_name", "reference_image_2_name", "source_image_2", "source_image_2_path", "source_image_2_url")
        image3_name = _named_image_value(params, "comfy_source_image_3_name", "source_image_3_name", "source_image__3_name", "composition_image_name", "reference_image_3_name", "source_image_3", "source_image_3_path", "source_image_3_url")
        ignored = [name for name in (ref2_name, image3_name) if name and name != source_name]
        metadata.update({
            "qwen_multi_reference": False,
            "qwen_source_image_limit": 1,
            "ignored_source_images": ignored,
            "qwen_source_images": {"base_image_name": source_name, "reference_image_2_name": "", "composition_image_name": "", "image3_effective_name": ""},
        })
        notes.append("Qwen Image Edit GGUF single-source branch engaged: source_image_2/source_image_3 ignored until Qwen Image Edit 2509 family is selected.")
    next_id, qwen_inputs = attach_stitch_route(next_id, qwen_inputs)
    workflow[str(next_id)] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
    notes.append("Qwen GGUF img2img branch engaged: source image conditioning + EmptyLatentImage sampler input.")
    return str(next_id + 1), [str(next_id), 0], qwen_inputs, metadata | {"notes": notes}


def compile_qwen_gguf_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile Qwen Image GGUF routes.

    Phase 12.12 enabled txt2img. Phase 12.13 adds image-conditioned
    img2img/inpaint/outpaint routes. MMProj is required for image routes and
    kept as explicit metadata because current Comfy GGUF loader variants expose
    different mmproj loading contracts.
    """

    params = job.params or {}
    defaults = QWEN_GGUF_DEFAULTS
    mode = str(route.mode or job.mode or "txt2img")
    if mode == "inpaint":
        params = normalize_inpaint_target_aliases(params)
    if mode == "edit":
        mode = "img2img"
    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    backend_capabilities = backend_capabilities or {}
    unet_loader = _normalize_gguf_unet_loader(
        _param(params, "gguf_unet_loader", "unet_loader", "_neo_effective_gguf_unet_loader", default=_role_backend_node(backend_capabilities, "gguf", "gguf_unet") or defaults.unet_loader)
    )
    clip_loader = _normalize_gguf_clip_loader(
        _param(params, "gguf_clip_loader", "gguf_single_clip_loader", "clip_loader", "_neo_effective_gguf_clip_loader", default=_role_backend_node(backend_capabilities, "gguf", "gguf_text_encoder_primary") or defaults.clip_loader)
    )

    gguf_unet = job.model or _param(params, "gguf_unet", "gguf_model", "qwen_model", "model", "model_name", default="qwen-image.gguf")
    text_encoder = _param(params, "qwen_text_encoder", "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name", default="qwen_text_encoder.gguf")
    mmproj = _param(params, "qwen_mmproj", "gguf_mmproj", "mmproj", "mmproj_name", "gguf_mmproj_name", default="")
    vae = _param(params, "vae", "qwen_vae", "vae_or_ae", "ae", default="qwen_image_vae.safetensors")
    vae_loader = str(_param(params, "vae_loader", "gguf_vae_loader", default=_vae_loader_for(str(vae))))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    steps_default = QWEN_GGUF_INPAINT_DEFAULT_STEPS if mode == "inpaint" else defaults.steps
    steps = int(_param(params, "steps", default=steps_default))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = 1 if mode in {"img2img", "inpaint", "outpaint"} else int(_param(params, "batch_count", "batch_size", default=1))
    denoise_default = QWEN_GGUF_INPAINT_DEFAULT_DENOISE if mode == "inpaint" else (1.0 if mode in {"img2img", "outpaint"} else defaults.denoise)
    denoise = float(_param(params, "denoise", "strength", default=denoise_default))
    requested_cfg = float(_param(params, "cfg", default=defaults.cfg))
    cfg = requested_cfg
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    clip_type = str(_param(params, "gguf_clip_type", "clip_type", "text_encoder_type", default=defaults.clip_type)) or defaults.clip_type

    if mode in {"img2img", "inpaint", "outpaint"} and not str(mmproj or "").strip():
        validation.errors.append(f"Qwen GGUF {mode} requires a Qwen mmproj sidecar before queue.")
        validation.ok = False

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.qwen_gguf",
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
        "qwen_gguf_profile": {
            "family": "qwen_image",
            "visible_family": job.family or "qwen_image",
            "loader": "gguf",
            "compiler": "comfy.qwen_gguf",
            "enabled_modes": ["txt2img", "img2img", "edit", "inpaint", "outpaint"],
            "mmproj_required_for": ["img2img", "inpaint", "outpaint", "edit"],
            "source_policy": "Qwen Rapid AIO and Qwen Image Edit 2509 GGUF img2img/edit may consume Image 1 plus optional Image 2/Image 3; inpaint/outpaint stay single-source mask/canvas.",
            "provider_nodes": {"gguf_unet_loader": unet_loader, "gguf_clip_single_loader": clip_loader, "vae_loader": vae_loader},
            "sampler_cfg_policy": "use_route_cfg_for_qwen_gguf",
            "mmproj_policy": "optional_for_txt2img_required_for_image_routes",
        },
        "gguf_unet": gguf_unet,
        "gguf_model": gguf_unet,
        "qwen_text_encoder": text_encoder,
        "gguf_text_encoder_1": text_encoder,
        "gguf_text_encoder_primary": text_encoder,
        "qwen_mmproj": mmproj,
        "gguf_mmproj": mmproj,
        "gguf_clip_mode": "single",
        "gguf_clip_type": "qwen_image",
        "vae": vae,
        "denoise": denoise,
        "requested_cfg": requested_cfg,
        "sampler_cfg_effective": cfg,
        "sampler_cfg_source": "user_or_qwen_default",
        "cfg": cfg,
    }

    clip_inputs: dict[str, Any] = {"clip_name": text_encoder, "type": clip_type, "device": clip_device}
    clip_node: dict[str, Any] = {"class_type": clip_loader, "inputs": clip_inputs}
    if mmproj:
        clip_node["_meta"] = {"neo_mmproj_sidecar": mmproj, "neo_mmproj_policy": "required_for_image_routes" if mode != "txt2img" else "optional_for_txt2img"}

    workflow: dict[str, Any] = {
        "1": {"class_type": unet_loader, "inputs": _gguf_unet_inputs(unet_loader, str(gguf_unet))},
        "2": clip_node,
        "3": {"class_type": vae_loader, "inputs": {"vae_name": vae}},
    }

    if mode == "txt2img":
        workflow.update({
            "4": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
            "5": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_negative, "clip": ["2", 0]}},
            "6": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}},
            "7": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": seed,
                    "steps": steps,
                    "cfg": cfg,
                    "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
                    "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
                    "denoise": denoise,
                    "model": ["1", 0],
                    "positive": ["4", 0],
                    "negative": ["5", 0],
                    "latent_image": ["6", 0],
                },
            },
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["3", 0]}},
            "9": {"class_type": "PreviewImage", "inputs": {"images": ["8", 0]}},
        })
        actual_params["qwen_gguf_profile"]["mmproj_policy"] = "optional_for_txt2img"
        actual_params.update({
            "_neo_effective_qwen_route": True,
            "_neo_effective_mode": "txt2img",
            "_neo_effective_mmproj_required": False,
            "_neo_effective_gguf_mmproj": mmproj,
            "_neo_sampler_node_id": "7",
        })
        actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
            route={**route.as_dict(), "workflow_mode": "generate", "route_state": "available" if route.status == "available" else route.status},
            model_ref=["1", 0],
            clip_ref=["2", 0],
            sampler_node_id="7",
            sampler_model_input="model",
            loader_node_class="LoraLoader",
            source="comfy.qwen_gguf",
            strategy="lora_loader_model_clip_consumer_rewire",
            validated=False,
            notes=["Qwen GGUF compiler owns single model/clip refs; txt2img sampler consumes the model ref directly."],
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
                "poll_timeout_seconds": int(_param(params, "poll_timeout_seconds", default=0)),
                "poll_interval_ms": int(_param(params, "poll_interval_ms", default=1500)),
                "phase_notes": [
                    "Phase 12.12 enables the Qwen Image GGUF txt2img route.",
                    "Qwen GGUF txt2img uses GGUF UNet + single CLIPLoaderGGUF(type=qwen_image) + AE/VAE.",
                    "Qwen mmproj is optional and recorded for txt2img; Phase 12.13 image routes require it.",
                    "Qwen GGUF keeps single-encoder routing, uses the route CFG value, does not use LanPaint, and must not fall back to Flux GGUF.",
                    f"Prompt conditioning mode: {conditioning_mode}.",
                ],
                "prompt_conditioning": conditioning,
            },
        )

    try:
        next_id, sampler_latent_ref, qwen_image_inputs, qwen_route_meta = _build_qwen_image_conditioning_and_latent(
            workflow=workflow,
            mode=mode,
            params=params,
            width=width,
            height=height,
            batch_count=batch_count,
            vae_ref=["3", 0],
            backend_capabilities=backend_capabilities,
            family=job.family or "qwen_image",
        )
    except ValueError as exc:
        validation.errors.append(str(exc))
        validation.ok = False
        next_id = "6"
        workflow["6"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        sampler_latent_ref = ["6", 0]
        qwen_image_inputs = {}
        qwen_route_meta = {"notes": [str(exc)]}

    qwen_stitch_meta = qwen_route_meta.get("qwen_stitch") if isinstance(qwen_route_meta, dict) else {}
    if isinstance(qwen_stitch_meta, dict):
        stitch_errors = ((qwen_stitch_meta.get("validation") or {}).get("errors") or []) if isinstance(qwen_stitch_meta.get("validation"), dict) else []
        for item in stitch_errors:
            message = str(item.get("message") if isinstance(item, dict) else item).strip()
            if message and message not in validation.errors:
                validation.errors.append(message)
        if stitch_errors:
            validation.ok = False

    encode_node = _select_qwen_edit_node(params, backend_capabilities) if mode in {"img2img", "inpaint", "outpaint"} else "CLIPTextEncode"
    if encode_node in QWEN_EDIT_NODE_CANDIDATES:
        qwen_edit_extra_inputs = _qwen_edit_plus_extra_inputs(params, width, height, backend_capabilities, encode_node)
        positive_inputs: dict[str, Any] = {"prompt": effective_prompt, "clip": ["2", 0], "vae": ["3", 0], **qwen_image_inputs, **qwen_edit_extra_inputs}
        negative_inputs: dict[str, Any] = {"prompt": effective_negative, "clip": ["2", 0], "vae": ["3", 0], **qwen_image_inputs, **qwen_edit_extra_inputs}
        actual_params["qwen_edit_node_compatibility"] = _qwen_edit_node_compatibility_snapshot(backend_capabilities, encode_node, qwen_edit_extra_inputs)
        if qwen_edit_extra_inputs:
            actual_params["qwen_edit_plus_extra_inputs"] = dict(qwen_edit_extra_inputs)
            actual_params["qwen_target_size"] = int(qwen_edit_extra_inputs.get("target_size") or qwen_edit_extra_inputs.get("size") or 0)
    else:
        positive_inputs = {"text": effective_prompt, "clip": ["2", 0]}
        negative_inputs = {"text": effective_negative, "clip": ["2", 0]}

    workflow[str(next_id)] = {"class_type": encode_node, "inputs": positive_inputs}
    positive_ref = [str(next_id), 0]
    neg_id = str(int(next_id) + 1)
    workflow[neg_id] = {"class_type": encode_node, "inputs": negative_inputs}
    negative_ref = [neg_id, 0]
    sampler_id = str(int(next_id) + 2)
    decode_id = str(int(next_id) + 3)
    save_id = str(int(next_id) + 4)
    effective_sampler = sampler if sampler != "provider_default" else defaults.sampler
    effective_scheduler = scheduler if scheduler != "provider_default" else defaults.scheduler
    sampler_model_ref: list[Any] = ["1", 0]
    if mode == "inpaint":
        # DifferentialDiffusion makes the normalized latent noise mask affect the
        # normal KSampler path without depending on LanPaint custom node schemas.
        workflow[sampler_id] = {"class_type": "DifferentialDiffusion", "inputs": {"model": sampler_model_ref}}
        sampler_model_ref = [sampler_id, 0]
        sampler_id = str(int(sampler_id) + 1)
        decode_id = str(int(decode_id) + 1)
        save_id = str(int(save_id) + 1)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": effective_sampler,
            "scheduler": effective_scheduler,
            "denoise": denoise,
            "model": sampler_model_ref,
            "positive": positive_ref,
            "negative": negative_ref,
            "latent_image": sampler_latent_ref,
        },
    }
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}

    notes = list(qwen_route_meta.pop("notes", [])) if isinstance(qwen_route_meta, dict) else []
    inpaint_source_ref = qwen_route_meta.pop("_neo_qwen_inpaint_source_ref", None) if isinstance(qwen_route_meta, dict) else None
    inpaint_mask_ref = qwen_route_meta.pop("_neo_qwen_inpaint_mask_ref", None) if isinstance(qwen_route_meta, dict) else None
    output_image_ref: list[Any] = [decode_id, 0]
    if mode == "inpaint" and inpaint_source_ref and inpaint_mask_ref:
        composite_mask_ref = list(inpaint_mask_ref)
        composite_feather = 0
        if isinstance(qwen_route_meta, dict):
            composite_feather = int(qwen_route_meta.get("_neo_qwen_inpaint_composite_feather") or 0)
        if composite_feather > 0:
            feather_id = save_id
            save_id = str(int(save_id) + 1)
            workflow[feather_id] = {
                "class_type": "GrowMaskWithBlur",
                "inputs": {
                    "mask": composite_mask_ref,
                    "expand": 0,
                    "incremental_expandrate": 0,
                    "tapered_corners": True,
                    "flip_input": False,
                    "blur_radius": composite_feather,
                    "lerp_alpha": 1,
                    "decay_factor": 1,
                    "fill_holes": False,
                },
            }
            composite_mask_ref = [feather_id, 0]
        composite_id = save_id
        save_id = str(int(save_id) + 1)
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {
                "destination": list(inpaint_source_ref),
                "source": [decode_id, 0],
                "x": 0,
                "y": 0,
                "resize_source": True,
                "mask": composite_mask_ref,
            },
        }
        output_image_ref = [composite_id, 0]
    workflow[save_id] = {"class_type": "PreviewImage", "inputs": {"images": output_image_ref}}

    actual_params.update(qwen_route_meta if isinstance(qwen_route_meta, dict) else {})
    if "qwen_outpaint_base_size" in actual_params:
        actual_params["_neo_qwen_outpaint_base_size"] = actual_params["qwen_outpaint_base_size"]
        actual_params["_neo_qwen_outpaint_padding"] = actual_params["qwen_outpaint_padding"]
        actual_params["_neo_qwen_outpaint_effective_size"] = actual_params["qwen_outpaint_effective_size"]
        actual_params["_neo_qwen_outpaint_source_resolution"] = actual_params.get("qwen_outpaint_source_resolution", {})
        actual_params["_neo_qwen_outpaint_source_scale_node"] = actual_params.get("qwen_outpaint_source_scale_node", {})
    actual_params["_neo_effective_qwen_route"] = True
    actual_params["_neo_effective_mode"] = mode
    actual_params["_neo_effective_mmproj_required"] = mode in {"img2img", "inpaint", "outpaint"}
    actual_params["_neo_effective_gguf_mmproj"] = mmproj
    actual_params["_neo_sampler_node_id"] = sampler_id
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.qwen_gguf",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["Qwen GGUF image compiler owns model/clip refs; image-conditioned edit nodes are patched by exact clip ref only."],
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
            "poll_timeout_seconds": int(_param(params, "poll_timeout_seconds", default=0)),
            "poll_interval_ms": int(_param(params, "poll_interval_ms", default=1500)),
            "phase_notes": [
                "V25.9.20 Pass N3 completes Qwen Rapid AIO GGUF normal workflow coverage; edit aliases to img2img before graph compile.",
                "Qwen image-conditioned routes use TextEncodeQwenImageEditPlus with source image context and require mmproj.",
                "Qwen inpaint uses source VAEEncode + normalized SetLatentNoiseMask + DifferentialDiffusion + normal KSampler + feathered final ImageCompositeMasked guard.",
                "Qwen outpaint uses ImagePadForOutpaint and an effective padded latent canvas.",
                "Qwen GGUF keeps single-encoder routing, uses the route CFG value, does not use LanPaint, and must not fall back to Flux GGUF.",
                *notes,
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
