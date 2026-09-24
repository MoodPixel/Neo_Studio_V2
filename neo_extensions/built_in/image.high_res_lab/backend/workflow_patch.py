from __future__ import annotations

from copy import deepcopy
from typing import Any
from pathlib import Path
import re

from .constants import EXTENSION_ID, PHASE
from .payload_schema import normalize_block
from .validation import validate_and_normalize_payload
from .route_profiles import route_profile_summary


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


def _copy_ref(ref: Any, fallback: list[Any] | None = None) -> list[Any]:
    if isinstance(ref, (list, tuple)) and len(ref) >= 2:
        index = ref[1]
        if isinstance(index, str) and index.isdigit():
            index = int(index)
        return [str(ref[0]), index]
    return deepcopy(fallback or [])


def _extension_block_from_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
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
    return deepcopy(payload)



def _available_has(available_nodes: Any, node_name: str) -> bool:
    if available_nodes is None:
        return False
    if isinstance(available_nodes, dict):
        if node_name in available_nodes:
            return True
        for value in available_nodes.values():
            if value == node_name:
                return True
            if isinstance(value, dict) and node_name in {str(value.get(k)) for k in ("class_type", "node", "name", "type") if value.get(k)}:
                return True
        return False
    return node_name in {str(item) for item in available_nodes}



def _vae_decode_tiled_inputs(samples_ref: list[Any], vae_ref: list[Any], params: dict[str, Any]) -> dict[str, Any]:
    """Build VAEDecodeTiled inputs compatible with current Comfy image/video signatures.

    Recent Comfy builds add temporal_size and temporal_overlap as required
    inputs on VAEDecodeTiled.  High-Res Lab is an image finish pass, but Comfy
    validates required inputs before execution, so we provide safe image-neutral
    temporal defaults alongside the classic tile inputs.
    """
    return {
        "samples": samples_ref,
        "vae": vae_ref,
        "tile_size": int(params.get("tile_size", 512)),
        "overlap": int(params.get("tile_overlap", 64)),
        "temporal_size": int(params.get("temporal_size", 64) or 64),
        "temporal_overlap": int(params.get("temporal_overlap", 8) or 8),
    }

def _node_inputs(workflow: dict[str, Any], node_id: str | int) -> dict[str, Any]:
    node = workflow.get(str(node_id))
    inputs = node.get("inputs") if isinstance(node, dict) else None
    return inputs if isinstance(inputs, dict) else {}


def _find_first_node(workflow: dict[str, Any], class_type: str) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == class_type:
            return str(node_id), node
    return None, None


_VAE_DECODE_CLASSES = {"VAEDecode", "VAEDecodeTiled", "VAEUtils_VAEDecodeTiled"}
_VAE_UTILS_DECODE_CLASS = "VAEUtils_VAEDecodeTiled"
_VAE_UTILS_LOADER_CLASS = "VAEUtils_CustomVAELoader"


def _find_vae_ref(workflow: dict[str, Any]) -> list[Any]:
    # Prefer the VAE already used by the base decode path, including the
    # ComfyUI-VAE-Utils decoder required by Wan/Qwen 2x decode VAEs.
    for node in workflow.values():
        if isinstance(node, dict) and node.get("class_type") in _VAE_DECODE_CLASSES:
            ref = (node.get("inputs") or {}).get("vae")
            if isinstance(ref, (list, tuple)) and len(ref) >= 2:
                return _copy_ref(ref)
    # Fallback to common loader node ids/output slots.
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") in {"CheckpointLoaderSimple", "VAELoader", "VaeGGUF", _VAE_UTILS_LOADER_CLASS}:
            if node.get("class_type") == "CheckpointLoaderSimple":
                return [str(node_id), 2]
            return [str(node_id), 0]
    return ["1", 2]


def _base_decode_node(workflow: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") in _VAE_DECODE_CLASSES:
            return str(node_id), node
    return None, None


def _uses_vae_utils_decode(workflow: dict[str, Any]) -> bool:
    _node_id, node = _base_decode_node(workflow)
    if isinstance(node, dict) and node.get("class_type") == _VAE_UTILS_DECODE_CLASS:
        return True
    return any(
        isinstance(node, dict) and node.get("class_type") == _VAE_UTILS_LOADER_CLASS
        for node in workflow.values()
    )


def _vae_utils_decode_inputs(samples_ref: list[Any], vae_ref: list[Any], params: dict[str, Any], workflow: dict[str, Any]) -> dict[str, Any]:
    # Start from the base VAE-Utils decoder settings when present so High-Res
    # Lab inherits the generation contract instead of creating a different one.
    _node_id, base = _base_decode_node(workflow)
    base_inputs = (base or {}).get("inputs") if isinstance(base, dict) and base.get("class_type") == _VAE_UTILS_DECODE_CLASS else {}
    base_inputs = base_inputs if isinstance(base_inputs, dict) else {}
    return {
        "samples": _copy_ref(samples_ref),
        "vae": _copy_ref(vae_ref),
        "upscale": int(base_inputs.get("upscale", -1)),
        "tile": bool(params.get("tiled_vae", base_inputs.get("tile", False))),
        "tile_size": int(params.get("tile_size", base_inputs.get("tile_size", 512)) or 512),
        "overlap": int(params.get("tile_overlap", base_inputs.get("overlap", 64)) or 64),
        "temporal_size": int(base_inputs.get("temporal_size", 4096) or 4096),
        "temporal_overlap": int(base_inputs.get("temporal_overlap", 64) or 64),
    }


def _build_compatible_vae_decode_node(
    *,
    workflow: dict[str, Any],
    samples_ref: list[Any],
    vae_ref: list[Any],
    params: dict[str, Any],
    available_nodes: Any,
) -> dict[str, Any]:
    if _uses_vae_utils_decode(workflow):
        return {
            "class_type": _VAE_UTILS_DECODE_CLASS,
            "inputs": _vae_utils_decode_inputs(samples_ref, vae_ref, params, workflow),
        }
    decode_class = "VAEDecodeTiled" if bool(params.get("tiled_vae")) and (available_nodes is None or _available_has(available_nodes, "VAEDecodeTiled")) else "VAEDecode"
    decode_inputs = _vae_decode_tiled_inputs(samples_ref, vae_ref, params) if decode_class == "VAEDecodeTiled" else {"samples": _copy_ref(samples_ref), "vae": _copy_ref(vae_ref)}
    return {"class_type": decode_class, "inputs": decode_inputs}


def _find_output_image_consumers(workflow: dict[str, Any], decoded_ref: list[Any] | None = None) -> list[tuple[str, str]]:
    """Find final image outputs without hijacking diagnostic PreviewImage nodes.

    SaveImage is Neo's authoritative backend-native handoff when present. Q21-4B
    deliberately adds intermediate PreviewImage proof nodes, so High-Res must only
    rewrite PreviewImage nodes that share the same image ref as a SaveImage final
    output. Routes without SaveImage keep the historical all-preview fallback.
    """
    save_consumers: list[tuple[str, str]] = []
    preview_consumers: list[tuple[str, str]] = []
    save_refs: set[tuple[Any, ...]] = set()
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") not in {"PreviewImage", "SaveImage"}:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        value = inputs.get("images")
        if not isinstance(value, (list, tuple)) or len(value) < 2:
            continue
        consumer = (str(node_id), "images")
        if node.get("class_type") == "SaveImage":
            save_consumers.append(consumer)
            save_refs.add(tuple(value))
        else:
            preview_consumers.append(consumer)
    if save_consumers:
        matching_previews = []
        for consumer in preview_consumers:
            value = _node_inputs(workflow, consumer[0]).get(consumer[1])
            if isinstance(value, (list, tuple)) and tuple(value) in save_refs:
                matching_previews.append(consumer)
        return [*save_consumers, *matching_previews]
    if preview_consumers:
        return preview_consumers
    consumers: list[tuple[str, str]] = []
    # Fallback: rewrite consumers of the base decode image output.
    if decoded_ref:
        for node_id, node in workflow.items():
            if not isinstance(node, dict):
                continue
            inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
            for name, value in inputs.items():
                if value == decoded_ref:
                    consumers.append((str(node_id), str(name)))
    return consumers


def _find_base_decode_ref(workflow: dict[str, Any]) -> list[Any] | None:
    node_id, _ = _base_decode_node(workflow)
    return [node_id, 0] if node_id else None

def _current_output_ref(workflow: dict[str, Any], consumers: list[tuple[str, str]]) -> list[Any] | None:
    """Return the image ref currently feeding SaveImage/PreviewImage.

    Q21-4 strict masked workflows may insert ImageCompositeMasked after the base
    VAEDecode. High-Res must start from that final Stage-1 image rather than the
    earlier decoder output or it would silently discard strict preservation.
    """
    for consumer_id, input_name in consumers:
        value = _node_inputs(workflow, consumer_id).get(input_name)
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            return _copy_ref(value)
    return None


def _route_identity(route: dict[str, Any] | None) -> tuple[str, str]:
    route = route if isinstance(route, dict) else {}
    family = str(route.get("family") or "").strip()
    mode = str(route.get("mode") or route.get("workflow_mode") or "").strip().lower()
    if mode == "generate":
        mode = "txt2img"
    if mode == "image_to_image":
        mode = "img2img"
    return family, mode


def _qwen21_output_channel_policy(route: dict[str, Any] | None) -> tuple[str, str]:
    route = route if isinstance(route, dict) else {}
    route_params = route.get("actual_params") if isinstance(route.get("actual_params"), dict) else route.get("params") if isinstance(route.get("params"), dict) else {}
    raw = route_params.get("qwen21_output_channels") if isinstance(route_params, dict) else None
    if raw in (None, ""):
        # Backward-compatible direct patch callers predating Q21-6A owned only
        # the RGB-safe A1 bridge. Production Q21-6A compilers always publish
        # qwen21_output_channels in actual_params.
        return "legacy", "legacy_rgb_safe_default"
    value = str(raw).strip().lower()
    aliases = {"transparent": "rgba", "alpha": "rgba", "rgba_transparent": "rgba", "native": "auto"}
    value = aliases.get(value, value)
    return (value if value in {"auto", "rgb", "rgba"} else "auto"), "route_actual_params"


def _load_image_alpha_ref(workflow: dict[str, Any], image_ref: list[Any] | None) -> list[Any] | None:
    if not isinstance(image_ref, (list, tuple)) or len(image_ref) < 2:
        return None
    node = workflow.get(str(image_ref[0]))
    if isinstance(node, dict) and node.get("class_type") == "LoadImage" and int(image_ref[1] or 0) == 0:
        # Comfy LoadImage exposes its decoded alpha as MASK output 1 while IMAGE
        # output 0 is RGB. This keeps preview-action High-Res alpha recoverable.
        return [str(image_ref[0]), 1]
    return None


def _qwen21_inpaint_mask_ref_from_sampler(workflow: dict[str, Any], sampler_inputs: dict[str, Any]) -> list[Any] | None:
    latent_ref = sampler_inputs.get("latent_image")
    if not isinstance(latent_ref, (list, tuple)) or len(latent_ref) < 2:
        return None
    latent_node = workflow.get(str(latent_ref[0]))
    if not isinstance(latent_node, dict) or latent_node.get("class_type") != "SetLatentNoiseMask":
        return None
    mask_ref = (latent_node.get("inputs") or {}).get("mask")
    return _copy_ref(mask_ref) if isinstance(mask_ref, (list, tuple)) and len(mask_ref) >= 2 else None


def _qwen21_outpaint_mask_ref(workflow: dict[str, Any]) -> list[Any] | None:
    encode_node = workflow.get("4")
    if isinstance(encode_node, dict) and encode_node.get("class_type") == "TextEncodeQwenImage21":
        image1_ref = (encode_node.get("inputs") or {}).get("images.image_1")
        if isinstance(image1_ref, (list, tuple)) and len(image1_ref) >= 2:
            pad_node = workflow.get(str(image1_ref[0]))
            if isinstance(pad_node, dict) and pad_node.get("class_type") == "ImagePadForOutpaint":
                return [str(image1_ref[0]), 1]
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "ImagePadForOutpaint":
            return [str(node_id), 1]
    return None


def _resize_mask_with_core_nodes(
    *,
    graph: dict[str, Any],
    next_id: str,
    mask_ref: list[Any],
    target_width: int,
    target_height: int,
    available_nodes: Any,
) -> tuple[list[Any] | None, str, list[str]]:
    """Resize a MASK using long-standing Comfy core/extras nodes.

    MaskToImage -> ImageScale -> ImageToMask avoids relying on a brand-new
    ResizeImageMaskNode signature while remaining compatible with current Comfy.
    """
    required = ("MaskToImage", "ImageScale", "ImageToMask")
    if available_nodes is not None and not all(_available_has(available_nodes, name) for name in required):
        return None, next_id, []
    node_ids: list[str] = []
    to_image_id = next_id
    graph[to_image_id] = {"class_type": "MaskToImage", "inputs": {"mask": _copy_ref(mask_ref)}}
    node_ids.append(to_image_id)
    next_id = _next_graph_id(graph)
    scale_id = next_id
    graph[scale_id] = {
        "class_type": "ImageScale",
        "inputs": {
            "image": [to_image_id, 0],
            "upscale_method": "bilinear",
            "width": int(target_width),
            "height": int(target_height),
            "crop": "disabled",
        },
    }
    node_ids.append(scale_id)
    next_id = _next_graph_id(graph)
    to_mask_id = next_id
    graph[to_mask_id] = {"class_type": "ImageToMask", "inputs": {"image": [scale_id, 0], "channel": "red"}}
    node_ids.append(to_mask_id)
    next_id = _next_graph_id(graph)
    return [to_mask_id, 0], next_id, node_ids


def _scale_image_to_target_node(image_ref: list[Any], target_width: int, target_height: int, resize_method: str) -> dict[str, Any]:
    return {
        "class_type": "ImageScale",
        "inputs": {
            "image": _copy_ref(image_ref),
            "upscale_method": resize_method,
            "width": int(target_width),
            "height": int(target_height),
            "crop": "disabled",
        },
    }

def _find_source_load_image_ref(workflow: dict[str, Any]) -> list[Any] | None:
    """Return the source LoadImage output for V1-style preview finish passes.

    Normal High-Res Lab patches start from the decoded sampler output. Preview
    toolbar finish passes must instead operate on the selected output image, not
    generate a fresh image first. The checkpoint/img2img compiler represents
    that selected output as LoadImage -> VAEEncode, so prefer that LoadImage.
    """
    for node in workflow.values():
        if not isinstance(node, dict) or node.get("class_type") != "VAEEncode":
            continue
        pixels = (node.get("inputs") or {}).get("pixels")
        if isinstance(pixels, (list, tuple)) and len(pixels) >= 2:
            src = workflow.get(str(pixels[0]))
            if isinstance(src, dict) and src.get("class_type") == "LoadImage":
                return [str(pixels[0]), 0]
    for node_id, node in workflow.items():
        if isinstance(node, dict) and node.get("class_type") == "LoadImage":
            return [str(node_id), 0]
    return None


def _is_preview_source_only(block: dict[str, Any], params: dict[str, Any]) -> bool:
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    return bool(
        params.get("upscale_lab_source_only")
        or metadata.get("upscale_lab_source_only")
        or metadata.get("source_mode") == "preview_action_selected_output"
        or inputs.get("preview_action_source")
        or metadata.get("preview_action_source")
    )


def _coerce_positive_int(value: Any) -> int:
    try:
        number = int(round(float(value)))
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _snap_dimension(value: float | int, *, multiple: int = 8, minimum: int = 64) -> int:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(minimum)
    number = max(float(minimum), number)
    return max(minimum, int(round(number / multiple) * multiple))


def _dimension_pair_from_mapping(data: Any) -> tuple[int, int, str]:
    if not isinstance(data, dict):
        return 0, 0, ""
    key_pairs = (
        ("width", "height", "width_height"),
        ("source_width", "source_height", "source_width_height"),
        ("source_image_width", "source_image_height", "source_image_width_height"),
        ("working_width", "working_height", "working_width_height"),
    )
    for width_key, height_key, source in key_pairs:
        width = _coerce_positive_int(data.get(width_key))
        height = _coerce_positive_int(data.get(height_key))
        if width and height:
            return width, height, source
    return 0, 0, ""


def _route_dimensions(route: dict[str, Any] | None) -> tuple[int, int, str]:
    route = route if isinstance(route, dict) else {}
    for key in ("actual_params", "params", "request", "metadata"):
        width, height, source = _dimension_pair_from_mapping(route.get(key))
        if width and height:
            return width, height, f"route.{key}.{source}"
    width, height, source = _dimension_pair_from_mapping(route)
    if width and height:
        return width, height, f"route.{source}"
    return 0, 0, ""


def _workflow_latent_dimensions(workflow: dict[str, Any]) -> tuple[int, int, str]:
    latent_classes = {"EmptyLatentImage", "EmptySD3LatentImage", "EmptyFluxLatentImage", "EmptyFlux2LatentImage"}
    for node_id, node in workflow.items():
        if not isinstance(node, dict) or node.get("class_type") not in latent_classes:
            continue
        inputs = node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
        width = _coerce_positive_int(inputs.get("width"))
        height = _coerce_positive_int(inputs.get("height"))
        if width and height:
            return width, height, f"workflow.{node_id}.{node.get('class_type')}"
    return 0, 0, ""


def _preview_source_dimensions(block: dict[str, Any]) -> tuple[int, int, str]:
    inputs = block.get("inputs") if isinstance(block.get("inputs"), dict) else {}
    metadata = block.get("metadata") if isinstance(block.get("metadata"), dict) else {}
    candidates = []
    for source in (inputs.get("preview_action_source"), metadata.get("preview_action_source")):
        if isinstance(source, dict):
            width, height, pair_source = _dimension_pair_from_mapping(source)
            if width and height:
                return width, height, f"preview_action_source.{pair_source}"
            for key in ("source_saved_path", "saved_path", "source_path", "path", "url"):
                value = str(source.get(key) or "").strip()
                if value and not value.startswith(("http://", "https://", "/api/")):
                    candidates.append(value)
    for raw_path in candidates:
        for candidate in (Path(raw_path), Path.cwd() / raw_path):
            try:
                if not candidate.exists() or not candidate.is_file():
                    continue
                from PIL import Image  # type: ignore
                with Image.open(candidate) as image:
                    width, height = image.size
                if width and height:
                    return int(width), int(height), f"preview_action_source.file:{candidate.as_posix()}"
            except Exception:
                continue
    return 0, 0, ""


def _resolve_hires_target_size(
    workflow: dict[str, Any],
    scale: float,
    *,
    params: dict[str, Any] | None = None,
    block: dict[str, Any] | None = None,
    route: dict[str, Any] | None = None,
    snap_multiple: int = 8,
) -> tuple[int, int, str]:
    params = params if isinstance(params, dict) else {}
    block = block if isinstance(block, dict) else {}
    explicit_width = _coerce_positive_int(params.get("target_width") or params.get("resize_width"))
    explicit_height = _coerce_positive_int(params.get("target_height") or params.get("resize_height"))

    base_width = base_height = 0
    size_source = ""
    for resolver in (
        lambda: _preview_source_dimensions(block),
        lambda: _route_dimensions(route),
        lambda: _workflow_latent_dimensions(workflow),
    ):
        base_width, base_height, size_source = resolver()
        if base_width and base_height:
            break

    if explicit_width and explicit_height:
        return _snap_dimension(explicit_width, multiple=snap_multiple), _snap_dimension(explicit_height, multiple=snap_multiple), "explicit_target_width_height"
    if explicit_width and base_width and base_height:
        return _snap_dimension(explicit_width, multiple=snap_multiple), _snap_dimension(explicit_width * (base_height / base_width), multiple=snap_multiple), f"explicit_target_width+{size_source}"
    if explicit_height and base_width and base_height:
        return _snap_dimension(explicit_height * (base_width / base_height), multiple=snap_multiple), _snap_dimension(explicit_height, multiple=snap_multiple), f"explicit_target_height+{size_source}"

    if not (base_width and base_height):
        # Last-resort fallback keeps the old safe behavior, but records the source
        # so diagnostics can catch missing dimension handoff instead of silently
        # pretending the selected output was square.
        base_width = base_height = 1024
        size_source = "fallback.unknown_source_square"
    return _snap_dimension(base_width * scale, multiple=snap_multiple), _snap_dimension(base_height * scale, multiple=snap_multiple), size_source


def _sampler_inputs_for_refine(
    *,
    base_sampler_inputs: dict[str, Any],
    latent_ref: list[Any],
    model_ref: list[Any],
    params: dict[str, Any],
    route_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    inputs = deepcopy(base_sampler_inputs)
    route_profile = route_profile if isinstance(route_profile, dict) else {}
    cfg_policy = str(route_profile.get("cfg_policy") or "preserve_route_default")
    inputs["model"] = _copy_ref(model_ref, inputs.get("model") if isinstance(inputs.get("model"), list) else ["1", 0])
    inputs["latent_image"] = _copy_ref(latent_ref)
    inputs["steps"] = int(params.get("steps", inputs.get("steps", 12)))
    # Flux/Families with separate guidance controls should not receive SD-style
    # High-Res Lab CFG overrides. Preserve the provider compiler's sampler CFG.
    if cfg_policy == "preserve_base_sampler_cfg":
        inputs["cfg"] = float(base_sampler_inputs.get("cfg", inputs.get("cfg", 1.0)) or 1.0)
    elif cfg_policy == "qwen_rapid_aio_low_cfg":
        # Qwen Rapid AIO routes are tuned around very low CFG. If the UI sends a
        # stale SD-style CFG, clamp it instead of letting High-Res Lab distort the
        # second pass. Missing CFG falls back to the base route sampler CFG.
        inputs["cfg"] = min(float(params.get("cfg", base_sampler_inputs.get("cfg", inputs.get("cfg", 1.0))) or 1.0), 2.0)
    elif cfg_policy in {"qwen_safe_low_cfg", "qwen_2509_safe_low_cfg", "qwen_2511_safe_low_cfg"}:
        # Qwen image/edit routes tolerate CFG, but SDXL-like high CFG pushes the
        # refine pass away from the source/edit conditioning. Keep it route-safe.
        inputs["cfg"] = min(float(params.get("cfg", base_sampler_inputs.get("cfg", inputs.get("cfg", 3.0))) or 3.0), 4.0)
    elif cfg_policy == "z_image_aura_safe":
        # ZImage uses the AuraFlow sampling patch and should not inherit SDXL-like
        # high CFG values in the High-Res second pass. Keep refine guidance in a
        # conservative range while preserving the selected route's model/conditioning.
        inputs["cfg"] = min(float(params.get("cfg", base_sampler_inputs.get("cfg", inputs.get("cfg", 3.0))) or 3.0), 3.5)
    elif cfg_policy == "z_image_turbo_low_cfg":
        # ZImage Turbo is distilled for very low guidance. Preserve the route's
        # Turbo conditioning and clamp any stale SD/ZImage CFG to 1.0 before the
        # High-Res second pass can drift the source image.
        inputs["cfg"] = min(float(params.get("cfg", base_sampler_inputs.get("cfg", inputs.get("cfg", 1.0))) or 1.0), 1.0)
    else:
        inputs["cfg"] = float(params.get("cfg", inputs.get("cfg", 5.2)))
    inputs["denoise"] = float(params.get("denoise", 0.12))
    if params.get("sampler"):
        inputs["sampler_name"] = str(params.get("sampler"))
    if params.get("scheduler"):
        inputs["scheduler"] = str(params.get("scheduler"))
    return inputs



def _infer_upscale_model_native_scale(model_name: str) -> float:
    """Infer native multiplier from common Comfy upscaler model names.

    Mirrors V1 Upscale Lab behavior: a model named 4x-*.pth is treated as a
    native 4x image upscaler. The user-facing High-Res Lab scale remains the
    final target scale, so image-upscale mode must compensate after model
    upscale instead of sending a 4x image into the refine sampler.
    """
    name = str(model_name or "").strip().lower()
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)x(?!\d)", name)
    if not match:
        return 1.0
    try:
        value = float(match.group(1))
    except Exception:
        value = 1.0
    return max(0.1, min(value, 16.0))


def _image_scale_by_node(image_ref: list[Any], resize_method: str, scale_by: float) -> dict[str, Any]:
    return {
        "class_type": "ImageScaleBy",
        "inputs": {
            "image": _copy_ref(image_ref),
            "upscale_method": resize_method,
            "scale_by": max(0.05, min(float(scale_by), 8.0)),
        },
    }



def _build_ultimate_sd_upscale_node_inputs(
    *,
    image_ref: list[Any],
    model_ref: list[Any],
    positive_ref: list[Any],
    negative_ref: list[Any],
    vae_ref: list[Any],
    upscale_model_ref: list[Any] | None,
    params: dict[str, Any],
    base_sampler_inputs: dict[str, Any],
) -> dict[str, Any]:
    """Build V1-parity UltimateSDUpscale inputs for preserve/image-upscale mode.

    V1 used UltimateSDUpscale when available for image_upscale/preserve mode
    because that node performs tiled redraw internally and emits tile-local live
    previews while it works.  The fallback VAEEncode -> KSampler path is kept
    for installs without UltimateSDUpscale, but should not be the preferred
    path when the node exists.
    """
    tile_overlap = int(params.get("tile_overlap", 64) or 64)
    inputs: dict[str, Any] = {
        "image": _copy_ref(image_ref),
        "model": _copy_ref(model_ref),
        "positive": _copy_ref(positive_ref),
        "negative": _copy_ref(negative_ref),
        "vae": _copy_ref(vae_ref),
        "upscale_by": float(params.get("scale", 1.45) or 1.45),
        "seed": int(base_sampler_inputs.get("seed", 1) or 1),
        "steps": int(params.get("steps", base_sampler_inputs.get("steps", 12)) or 12),
        "cfg": float(params.get("cfg", base_sampler_inputs.get("cfg", 5.2)) or 5.2),
        "sampler_name": str(params.get("sampler") or base_sampler_inputs.get("sampler_name") or "euler"),
        "scheduler": str(params.get("scheduler") or base_sampler_inputs.get("scheduler") or "normal"),
        "denoise": float(params.get("denoise", 0.12) or 0.12),
        "mode_type": "Linear",
        "tile_width": int(params.get("tile_size", 512) or 512),
        "tile_height": int(params.get("tile_size", 512) or 512),
        "mask_blur": 8,
        "tile_padding": max(16, min(tile_overlap, 128)),
        "seam_fix_mode": "None",
        "seam_fix_denoise": 1.0,
        "seam_fix_width": max(32, tile_overlap),
        "seam_fix_mask_blur": 8,
        "seam_fix_padding": max(16, min(tile_overlap // 2 if tile_overlap > 0 else 16, 64)),
        "force_uniform_tiles": True,
        "tiled_decode": False,
        "seed_mode": "randomize",
        "control_after_generate": "randomize",
        "batch_size": 1,
    }
    if upscale_model_ref:
        inputs["upscale_model"] = _copy_ref(upscale_model_ref)
    return inputs

def _try_build_ultimate_sd_upscale_path(
    *,
    graph: dict[str, Any],
    next_id: str,
    base_image_ref: list[Any],
    current_model_ref: list[Any],
    sampler_inputs: dict[str, Any],
    vae_ref: list[Any],
    params: dict[str, Any],
    available_nodes: Any,
) -> tuple[bool, str, list[str], list[Any]]:
    upscaler_name = str(params.get("upscaler") or "").strip()
    if not upscaler_name:
        return False, next_id, [], []
    if not (_available_has(available_nodes, "UltimateSDUpscale") and _available_has(available_nodes, "UpscaleModelLoader")):
        return False, next_id, [], []
    if not isinstance(sampler_inputs.get("positive"), (list, tuple)) or not isinstance(sampler_inputs.get("negative"), (list, tuple)):
        return False, next_id, [], []

    node_ids: list[str] = []
    loader_id = next_id
    graph[loader_id] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscaler_name}}
    node_ids.append(loader_id)
    next_id = _next_graph_id(graph)

    ultimate_id = next_id
    graph[ultimate_id] = {
        "class_type": "UltimateSDUpscale",
        "inputs": _build_ultimate_sd_upscale_node_inputs(
            image_ref=base_image_ref,
            model_ref=current_model_ref,
            positive_ref=_copy_ref(sampler_inputs.get("positive")),
            negative_ref=_copy_ref(sampler_inputs.get("negative")),
            vae_ref=vae_ref,
            upscale_model_ref=[loader_id, 0],
            params=params,
            base_sampler_inputs=sampler_inputs,
        ),
    }
    node_ids.append(ultimate_id)
    next_id = _next_graph_id(graph)
    return True, next_id, node_ids, [ultimate_id, 0]

def _build_patch_summary(
    *,
    validation: dict[str, Any],
    route: dict[str, Any] | None,
    applied: bool,
    mode: str = "",
    node_ids: list[str] | None = None,
    previous_output_refs: list[list[Any]] | None = None,
    patched_output_ref: list[Any] | None = None,
    sampler_node_id: str = "",
    reason: str = "",
) -> dict[str, Any]:
    block = validation.get("block") if isinstance(validation, dict) else {}
    params = block.get("params") if isinstance(block, dict) and isinstance(block.get("params"), dict) else {}
    profile = route_profile_summary(route)
    return {
        "extension_id": EXTENSION_ID,
        "extension_type": "built_in",
        "phase": PHASE,
        "applied": bool(applied),
        "mutated": bool(applied),
        "patch_type": "high_res_refine",
        "mode": mode or str(params.get("mode") or ""),
        "node_ids": list(node_ids or []),
        "sampler_node_id": str(sampler_node_id or ""),
        "previous_output_refs": deepcopy(previous_output_refs or []),
        "patched_output_ref": deepcopy(patched_output_ref or []),
        "route": deepcopy(route or {}),
        "node_status": deepcopy(validation.get("node_status") or {}),
        "params_used": deepcopy(params),
        "reason": reason or str(validation.get("reason") or ""),
        "optional_capabilities": deepcopy((validation.get("node_status") or {}).get("optional_capabilities") or {}),
        "route_profile": deepcopy(profile),
        "profile_id": profile.get("profile_id"),
    }


def apply_high_res_lab_patch(
    workflow: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    *,
    route: dict[str, Any] | None = None,
    available_nodes: set[str] | list[str] | tuple[str, ...] | dict[str, Any] | None = None,
    node_status: dict[str, Any] | None = None,
    model_ref: list[Any] | tuple[Any, ...] | None = None,
    sampler_node_id: str | int = "5",
    next_node_id: int | str | None = None,
    **_: Any,
) -> dict[str, Any]:
    """Apply the High-Res Lab second-pass Comfy graph patch.

    The patch is extension-local and intentionally late in the extension chain:
    it keeps upstream model/conditioning mutations intact, reads the target
    KSampler's already-patched inputs, adds an upscale/refine/decode chain, and
    reroutes final PreviewImage/SaveImage consumers to the refined image.
    """
    graph = deepcopy(workflow or {})
    raw_block = _extension_block_from_payload(payload)
    validation = validate_and_normalize_payload(raw_block, route=route, available_nodes=available_nodes, strict=False)
    block = validation.get("block") if isinstance(validation.get("block"), dict) else normalize_block(raw_block, route=route)
    params = block.get("params") if isinstance(block.get("params"), dict) else {}
    route_profile = route_profile_summary(route)
    sampler_key = str(sampler_node_id)

    def no_patch(reason: str) -> dict[str, Any]:
        return {
            "workflow": graph,
            "validation": validation,
            "workflow_patch": _build_patch_summary(validation=validation, route=route, applied=False, sampler_node_id=sampler_key, reason=reason),
            "mutated": False,
            "applied": False,
            "mutated_nodes": [],
        }

    if not validation.get("workflow_patch_allowed"):
        return no_patch(str(validation.get("reason") or "High-Res Lab is disabled or gated."))
    sampler = graph.get(sampler_key)
    if not isinstance(sampler, dict) or sampler.get("class_type") != "KSampler":
        return no_patch("validation_failed: target KSampler node was not found")
    sampler_inputs = sampler.get("inputs") if isinstance(sampler.get("inputs"), dict) else {}
    if not isinstance(sampler_inputs.get("latent_image"), (list, tuple)):
        return no_patch("validation_failed: target KSampler has no latent_image input")

    current_model_ref = _copy_ref(model_ref, sampler_inputs.get("model") if isinstance(sampler_inputs.get("model"), list) else ["1", 0])
    vae_ref = _find_vae_ref(graph)
    previous_decode_ref = _find_base_decode_ref(graph)
    output_consumers = _find_output_image_consumers(graph, previous_decode_ref)
    if not output_consumers:
        return no_patch("validation_failed: no PreviewImage/SaveImage output consumer was found")

    route_family, route_mode = _route_identity(route)
    qwen21_route = route_family == "qwen_image_21"
    qwen21_inpaint_route = qwen21_route and route_mode == "inpaint"
    qwen21_outpaint_route = qwen21_route and route_mode == "outpaint"
    qwen21_masked_route = qwen21_inpaint_route or qwen21_outpaint_route
    qwen21_output_channels, qwen21_output_channels_source = _qwen21_output_channel_policy(route) if qwen21_route else ("auto", "not_applicable")
    stage1_final_output_ref = _current_output_ref(graph, output_consumers)
    qwen21_mask_ref = None
    if qwen21_inpaint_route:
        qwen21_mask_ref = _qwen21_inpaint_mask_ref_from_sampler(graph, sampler_inputs)
    elif qwen21_outpaint_route:
        qwen21_mask_ref = _qwen21_outpaint_mask_ref(graph)
    qwen21_strict_stage1 = bool(
        qwen21_masked_route
        and stage1_final_output_ref
        and isinstance(graph.get(str(stage1_final_output_ref[0])), dict)
        and graph[str(stage1_final_output_ref[0])].get("class_type") == "ImageCompositeMasked"
    )
    if qwen21_inpaint_route and qwen21_mask_ref is None:
        return no_patch("validation_failed: Qwen Image 2.1 masked High-Res could not recover the Stage-1 SetLatentNoiseMask mask reference")
    if qwen21_outpaint_route and qwen21_strict_stage1 and qwen21_mask_ref is None:
        return no_patch("validation_failed: Qwen Image 2.1 outpaint High-Res could not recover the Stage-1 ImagePadForOutpaint mask reference")

    # Q21-5 must consume the target KSampler's already-patched model path. This
    # preserves LoRA rewiring and DifferentialDiffusion for Q21 masked routes.
    if qwen21_route and isinstance(sampler_inputs.get("model"), (list, tuple)):
        current_model_ref = _copy_ref(sampler_inputs.get("model"))

    scale = float(params.get("scale", 1.45))
    mode = str(params.get("mode") or "latent")
    strategy = str(params.get("strategy") or "standard")
    preview_source_only = _is_preview_source_only(block, params)
    target_width, target_height, target_size_source = _resolve_hires_target_size(
        graph,
        scale,
        params=params,
        block=block,
        route=route,
        snap_multiple=int(route_profile.get("snap_multiple") or 8),
    )
    next_id = _next_graph_id(graph, next_node_id)
    node_ids: list[str] = []
    qwen21_resized_mask_ref: list[Any] | None = None
    qwen21_preserve_base_ref: list[Any] | None = None
    qwen21_highres_image_bridge: dict[str, Any] = {
        "schema": "neo.image.qwen_image_21.highres_image_bridge.v2",
        "phase": "Q21-6A",
        "source_contract": "rgba_capable" if qwen21_route else "not_applicable",
        "requested_output_channels": qwen21_output_channels if qwen21_route else "not_applicable",
        "output_channels_source": qwen21_output_channels_source,
        "model_upscaler_contract": "rgb" if qwen21_route else "not_applicable",
        "rgb_normalization": "not_required",
        "alpha_policy": "not_applicable",
        "split_node_id": "",
        "split_mask_ref": [],
        "alpha_resize_node_ids": [],
        "alpha_join_node_id": "",
        "final_rgb_split_node_id": "",
    }

    if mode == "image_upscale":
        # Start from decoded base image when available; otherwise decode the sampler latent first.
        # V1 preview action parity: when High-Res Lab is triggered from a preview
        # toolbar, use the selected output LoadImage directly. Do not upscale the
        # result of a fresh img2img KSampler pass.
        base_image_ref = _find_source_load_image_ref(graph) if preview_source_only else (stage1_final_output_ref if qwen21_route and stage1_final_output_ref else previous_decode_ref)
        if base_image_ref is None:
            decode_id = next_id
            graph[decode_id] = _build_compatible_vae_decode_node(
                workflow=graph,
                samples_ref=[sampler_key, 0],
                vae_ref=vae_ref,
                params=params,
                available_nodes=available_nodes,
            )
            node_ids.append(decode_id)
            base_image_ref = [decode_id, 0]
            next_id = _next_graph_id(graph)
        blocked_strategies = set(route_profile.get("blocked_strategies") or [])
        ultimate_ok = False
        ultimate_node_ids: list[str] = []
        ultimate_image_ref: list[Any] = []
        should_try_ultimate = (
            "ultimate_sd_upscale" not in blocked_strategies
            and (
                strategy == "ultimate_sd_upscale"
                or (route_profile.get("family_group") == "sd" and bool(str(params.get("upscaler") or "").strip()))
            )
        )
        if should_try_ultimate:
            ultimate_ok, next_id, ultimate_node_ids, ultimate_image_ref = _try_build_ultimate_sd_upscale_path(
                graph=graph,
                next_id=next_id,
                base_image_ref=_copy_ref(base_image_ref),
                current_model_ref=current_model_ref,
                sampler_inputs=sampler_inputs,
                vae_ref=vae_ref,
                params=params,
                available_nodes=available_nodes,
            )
        if ultimate_ok:
            node_ids.extend(ultimate_node_ids)
            patched_output_ref = _copy_ref(ultimate_image_ref)
            previous_refs: list[list[Any]] = []
            for consumer_id, input_name in output_consumers:
                inputs = _node_inputs(graph, consumer_id)
                previous_value = inputs.get(input_name)
                if isinstance(previous_value, (list, tuple)) and len(previous_value) >= 2:
                    previous_refs.append(_copy_ref(previous_value))
                inputs[input_name] = _copy_ref(patched_output_ref)
            patch = _build_patch_summary(
                validation=validation,
                route=route,
                applied=True,
                mode=mode,
                node_ids=node_ids,
                previous_output_refs=previous_refs,
                patched_output_ref=patched_output_ref,
                sampler_node_id=sampler_key,
                reason="High-Res Lab Ultimate SD Upscale tiled preserve path applied.",
            )
            patch["patch_type"] = "high_res_ultimate_sd_upscale"
            patch["live_preview_mode"] = "ultimate_sd_upscale_tiles"
            patch["strategy"] = strategy
            patch["target_width"] = target_width
            patch["target_height"] = target_height
            patch["target_size_source"] = target_size_source
            patch["preview_source_only"] = preview_source_only
            if qwen21_route:
                patch["qwen21_highres_image_bridge"] = deepcopy(qwen21_highres_image_bridge)
            return {"workflow": graph, "validation": validation, "workflow_patch": patch, "mutated": True, "applied": True, "mutated_nodes": node_ids, "output_image_ref": patched_output_ref}

        upscaler_name = str(params.get("upscaler") or "").strip()
        resize_method = str(params.get("resize_method") or "lanczos")
        final_image_ref = _copy_ref(base_image_ref)
        qwen21_image_explicit_rgb = False
        qwen21_alpha_ref: list[Any] | None = None
        if upscaler_name and qwen21_route:
            # Qwen Image 2.1 can decode RGBA/4-channel IMAGE tensors, while
            # Spandrel model upscalers (DAT/ESRGAN/etc.) are RGB-only. Split at
            # that boundary. Preview-action LoadImage is already RGB, but its
            # alpha remains recoverable from MASK output 1.
            qwen21_alpha_ref = _load_image_alpha_ref(graph, base_image_ref)
            if qwen21_alpha_ref is not None:
                upscale_input_ref = _copy_ref(base_image_ref)
                qwen21_highres_image_bridge.update({
                    "rgb_normalization": "LoadImage RGB output",
                    "split_mask_ref": _copy_ref(qwen21_alpha_ref),
                })
            else:
                if available_nodes is not None and not _available_has(available_nodes, "SplitImageWithAlpha"):
                    return no_patch(
                        "validation_failed: Qwen Image 2.1 model-upscale High-Res requires SplitImageWithAlpha "
                        "to normalize RGBA output to RGB before ImageUpscaleWithModel"
                    )
                split_id = next_id
                graph[split_id] = {"class_type": "SplitImageWithAlpha", "inputs": {"image": _copy_ref(base_image_ref)}}
                node_ids.append(split_id)
                next_id = _next_graph_id(graph)
                upscale_input_ref = [split_id, 0]
                qwen21_alpha_ref = [split_id, 1]
                qwen21_highres_image_bridge.update({
                    "rgb_normalization": "SplitImageWithAlpha",
                    "split_node_id": split_id,
                    "split_mask_ref": _copy_ref(qwen21_alpha_ref),
                })
            qwen21_image_explicit_rgb = True
            if qwen21_output_channels in {"auto", "rgba"}:
                qwen21_highres_image_bridge["alpha_policy"] = "preserve_and_rejoin_after_rgb_upscale"
            elif qwen21_output_channels_source == "legacy_rgb_safe_default":
                qwen21_highres_image_bridge["alpha_policy"] = "discard_until_q21_6"
            else:
                qwen21_highres_image_bridge["alpha_policy"] = "explicit_strip_alpha"
        else:
            upscale_input_ref = _copy_ref(base_image_ref)
            if qwen21_route:
                qwen21_highres_image_bridge.update({
                    "model_upscaler_contract": "not_used",
                    "alpha_policy": "preserve_in_core_image_path" if qwen21_output_channels != "rgb" else "explicit_strip_at_output",
                })

        if upscaler_name and _available_has(available_nodes, "UpscaleModelLoader") and _available_has(available_nodes, "ImageUpscaleWithModel"):
            loader_id = next_id
            graph[loader_id] = {"class_type": "UpscaleModelLoader", "inputs": {"model_name": upscaler_name}}
            node_ids.append(loader_id)
            next_id = _next_graph_id(graph)
            upscale_id = next_id
            graph[upscale_id] = {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": [loader_id, 0], "image": _copy_ref(upscale_input_ref)}}
            node_ids.append(upscale_id)
            final_image_ref = [upscale_id, 0]
            next_id = _next_graph_id(graph)

            native_scale = _infer_upscale_model_native_scale(upscaler_name)
            extra_scale = scale / native_scale if native_scale > 0 else scale
            # V1 parity: 4x-UltraSharp with scale 1.45 means final target is 1.45x,
            # not 4x followed by a huge refine sampler. Add a compensating
            # ImageScaleBy only when the native model scale differs materially.
            if abs(extra_scale - 1.0) > 0.01:
                rescale_id = next_id
                graph[rescale_id] = _image_scale_by_node(final_image_ref, resize_method, extra_scale)
                node_ids.append(rescale_id)
                final_image_ref = [rescale_id, 0]
                next_id = _next_graph_id(graph)

            if qwen21_route and qwen21_alpha_ref is not None and qwen21_output_channels in {"auto", "rgba"}:
                if available_nodes is not None and not _available_has(available_nodes, "JoinImageWithAlpha"):
                    return no_patch(
                        "validation_failed: Qwen Image 2.1 Auto/RGBA model-upscale High-Res requires JoinImageWithAlpha "
                        "to restore alpha after the RGB-only upscaler"
                    )
                resized_alpha_ref, next_id, alpha_resize_node_ids = _resize_mask_with_core_nodes(
                    graph=graph,
                    next_id=next_id,
                    mask_ref=qwen21_alpha_ref,
                    target_width=target_width,
                    target_height=target_height,
                    available_nodes=available_nodes,
                )
                if resized_alpha_ref is None:
                    return no_patch(
                        "validation_failed: Qwen Image 2.1 Auto/RGBA model-upscale High-Res requires "
                        "MaskToImage + ImageScale + ImageToMask to resize alpha safely"
                    )
                node_ids.extend(alpha_resize_node_ids)
                join_id = next_id
                graph[join_id] = {
                    "class_type": "JoinImageWithAlpha",
                    "inputs": {"image": _copy_ref(final_image_ref), "alpha": _copy_ref(resized_alpha_ref)},
                }
                node_ids.append(join_id)
                next_id = _next_graph_id(graph)
                final_image_ref = [join_id, 0]
                qwen21_image_explicit_rgb = False
                qwen21_highres_image_bridge.update({
                    "alpha_resize_node_ids": list(alpha_resize_node_ids),
                    "alpha_join_node_id": join_id,
                })
        else:
            scale_id = next_id
            graph[scale_id] = _image_scale_by_node(final_image_ref, resize_method, scale)
            node_ids.append(scale_id)
            final_image_ref = [scale_id, 0]
            next_id = _next_graph_id(graph)

        if qwen21_masked_route and qwen21_strict_stage1:
            qwen21_preserve_base_ref = _copy_ref(final_image_ref)

        if strategy == "upscale_only":
            patched_output_ref = _copy_ref(final_image_ref)
            if qwen21_route and qwen21_output_channels == "rgb" and not qwen21_image_explicit_rgb:
                if available_nodes is not None and not _available_has(available_nodes, "SplitImageWithAlpha"):
                    return no_patch("validation_failed: Qwen Image 2.1 RGB High-Res output requires SplitImageWithAlpha")
                final_split_id = next_id
                graph[final_split_id] = {"class_type": "SplitImageWithAlpha", "inputs": {"image": _copy_ref(patched_output_ref)}}
                node_ids.append(final_split_id)
                next_id = _next_graph_id(graph)
                patched_output_ref = [final_split_id, 0]
                qwen21_highres_image_bridge["final_rgb_split_node_id"] = final_split_id
            previous_refs: list[list[Any]] = []
            for consumer_id, input_name in output_consumers:
                inputs = _node_inputs(graph, consumer_id)
                previous_value = inputs.get(input_name)
                if isinstance(previous_value, (list, tuple)) and len(previous_value) >= 2:
                    previous_refs.append(_copy_ref(previous_value))
                inputs[input_name] = _copy_ref(patched_output_ref)
            patch = _build_patch_summary(
                validation=validation,
                route=route,
                applied=True,
                mode=mode,
                node_ids=node_ids,
                previous_output_refs=previous_refs,
                patched_output_ref=patched_output_ref,
                sampler_node_id=sampler_key,
                reason="High-Res Lab upscale-only path applied without diffusion refine.",
            )
            patch["patch_type"] = "high_res_upscale_only"
            patch["strategy"] = strategy
            patch["target_width"] = target_width
            patch["target_height"] = target_height
            patch["target_size_source"] = target_size_source
            patch["preview_source_only"] = preview_source_only
            if qwen21_route:
                patch["qwen21_highres_image_bridge"] = deepcopy(qwen21_highres_image_bridge)
            return {"workflow": graph, "validation": validation, "workflow_patch": patch, "mutated": True, "applied": True, "mutated_nodes": node_ids, "output_image_ref": patched_output_ref}

        encode_id = next_id
        graph[encode_id] = {"class_type": "VAEEncode", "inputs": {"pixels": _copy_ref(final_image_ref), "vae": vae_ref}}
        node_ids.append(encode_id)
        next_id = _next_graph_id(graph)
        refine_latent_ref = [encode_id, 0]
    else:
        latent_input_ref = [sampler_key, 0]
        if preview_source_only:
            source_image_ref = _find_source_load_image_ref(graph)
            if source_image_ref is not None:
                source_encode_id = next_id
                graph[source_encode_id] = {"class_type": "VAEEncode", "inputs": {"pixels": _copy_ref(source_image_ref), "vae": vae_ref}}
                node_ids.append(source_encode_id)
                next_id = _next_graph_id(graph)
                latent_input_ref = [source_encode_id, 0]
        latent_upscale_id = next_id
        graph[latent_upscale_id] = {
            "class_type": "LatentUpscale",
            "inputs": {
                "samples": _copy_ref(latent_input_ref),
                "upscale_method": str(params.get("resize_method") or "lanczos"),
                "width": target_width,
                "height": target_height,
                "crop": "disabled",
            },
        }
        node_ids.append(latent_upscale_id)
        next_id = _next_graph_id(graph)
        refine_latent_ref = [latent_upscale_id, 0]

    if qwen21_masked_route and qwen21_mask_ref is not None:
        qwen21_resized_mask_ref, next_id, mask_resize_node_ids = _resize_mask_with_core_nodes(
            graph=graph,
            next_id=next_id,
            mask_ref=qwen21_mask_ref,
            target_width=target_width,
            target_height=target_height,
            available_nodes=available_nodes,
        )
        if qwen21_resized_mask_ref is None:
            return no_patch("validation_failed: Qwen Image 2.1 masked High-Res requires MaskToImage + ImageScale + ImageToMask to preserve the Stage-1 mask in Stage 2")
        node_ids.extend(mask_resize_node_ids)

        if qwen21_inpaint_route:
            remask_id = next_id
            graph[remask_id] = {
                "class_type": "SetLatentNoiseMask",
                "inputs": {"samples": _copy_ref(refine_latent_ref), "mask": _copy_ref(qwen21_resized_mask_ref)},
            }
            node_ids.append(remask_id)
            next_id = _next_graph_id(graph)
            refine_latent_ref = [remask_id, 0]

        if qwen21_strict_stage1 and qwen21_preserve_base_ref is None and stage1_final_output_ref is not None:
            preserve_scale_id = next_id
            graph[preserve_scale_id] = _scale_image_to_target_node(
                _copy_ref(stage1_final_output_ref), target_width, target_height, str(params.get("resize_method") or "lanczos")
            )
            node_ids.append(preserve_scale_id)
            next_id = _next_graph_id(graph)
            qwen21_preserve_base_ref = [preserve_scale_id, 0]

    refine_sampler_id = next_id
    graph[refine_sampler_id] = {"class_type": "KSampler", "inputs": _sampler_inputs_for_refine(base_sampler_inputs=sampler_inputs, latent_ref=refine_latent_ref, model_ref=current_model_ref, params=params, route_profile=route_profile)}
    node_ids.append(refine_sampler_id)
    next_id = _next_graph_id(graph)

    decode_id = next_id
    graph[decode_id] = _build_compatible_vae_decode_node(
        workflow=graph,
        samples_ref=[refine_sampler_id, 0],
        vae_ref=vae_ref,
        params=params,
        available_nodes=available_nodes,
    )
    node_ids.append(decode_id)
    patched_output_ref = [decode_id, 0]

    qwen21_stage2_strict_composite = False
    if qwen21_masked_route and qwen21_strict_stage1 and qwen21_preserve_base_ref is not None and qwen21_resized_mask_ref is not None:
        composite_id = _next_graph_id(graph)
        graph[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {
                "destination": _copy_ref(qwen21_preserve_base_ref),
                "source": _copy_ref(patched_output_ref),
                "x": 0,
                "y": 0,
                "resize_source": True,
                "mask": _copy_ref(qwen21_resized_mask_ref),
            },
        }
        node_ids.append(composite_id)
        patched_output_ref = [composite_id, 0]
        qwen21_stage2_strict_composite = True

    if qwen21_route and qwen21_output_channels == "rgb":
        if available_nodes is not None and not _available_has(available_nodes, "SplitImageWithAlpha"):
            return no_patch("validation_failed: Qwen Image 2.1 RGB High-Res output requires SplitImageWithAlpha")
        final_split_id = _next_graph_id(graph)
        graph[final_split_id] = {"class_type": "SplitImageWithAlpha", "inputs": {"image": _copy_ref(patched_output_ref)}}
        node_ids.append(final_split_id)
        patched_output_ref = [final_split_id, 0]
        qwen21_highres_image_bridge["final_rgb_split_node_id"] = final_split_id

    previous_refs: list[list[Any]] = []
    for consumer_id, input_name in output_consumers:
        inputs = _node_inputs(graph, consumer_id)
        previous_value = inputs.get(input_name)
        if isinstance(previous_value, (list, tuple)) and len(previous_value) >= 2:
            previous_refs.append(_copy_ref(previous_value))
        inputs[input_name] = _copy_ref(patched_output_ref)

    patch = _build_patch_summary(
        validation=validation,
        route=route,
        applied=True,
        mode=mode,
        node_ids=node_ids,
        previous_output_refs=previous_refs,
        patched_output_ref=patched_output_ref,
        sampler_node_id=sampler_key,
        reason="High-Res Lab second-pass refine chain applied.",
    )
    patch["strategy"] = strategy
    patch["target_width"] = target_width
    patch["target_height"] = target_height
    patch["target_size_source"] = target_size_source
    patch["preview_source_only"] = preview_source_only
    if qwen21_route:
        patch["qwen21_reference_conditioning_preserved"] = True
        patch["qwen21_true_cfg_preserved"] = str(route_profile.get("cfg_policy") or "") == "preserve_base_sampler_cfg"
        patch["qwen21_mask_policy_preserved"] = bool(
            (qwen21_inpaint_route and qwen21_resized_mask_ref)
            or (qwen21_outpaint_route and qwen21_strict_stage1 and qwen21_resized_mask_ref)
        )
        patch["qwen21_stage2_strict_composite"] = bool(qwen21_stage2_strict_composite)
        patch["qwen21_stage1_strict_preservation"] = bool(qwen21_strict_stage1)
        patch["qwen21_highres_image_bridge"] = deepcopy(qwen21_highres_image_bridge)
    return {"workflow": graph, "validation": validation, "workflow_patch": patch, "mutated": True, "applied": True, "mutated_nodes": node_ids, "output_image_ref": patched_output_ref}


def workflow_patch_not_implemented() -> dict:
    return {
        "extension_id": EXTENSION_ID,
        "implemented": True,
        "phase": PHASE,
        "reason": "Comfy high-res/refine graph patching is available for supported High-Res Lab routes.",
    }
