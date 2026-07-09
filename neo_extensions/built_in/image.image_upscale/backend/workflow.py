"""Phase G Comfy workflow builder for the Image Upscale built-in extension.

This module ports the V1 standalone Image Upscale utility graph into the V2
extension folder boundary. It intentionally does not patch the base generation
compiler: Image Upscale queues its own source-image workflow through the
extension-owned backend route.
"""
from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from .constants import EXTENSION_ID, EXTENSION_VERSION, SEEDVR2_ENGINE_ID
from .payload_schema import DEFAULTS, normalize_settings

SAVE_PREFIX = "NeoStudioUpscale"
WORKFLOW_PHASE = "J6"


def _as_ref(value: Any) -> list[Any] | None:
    if isinstance(value, (list, tuple)) and len(value) == 2:
        try:
            return [str(value[0]), int(value[1])]
        except Exception:  # noqa: BLE001 - preserve V1's tolerant ref normalization.
            return [str(value[0]), 0]
    return None


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(default)
    return max(float(low), min(float(high), parsed))


def _round_scale(value: float) -> float:
    # Keep Comfy payloads readable while avoiding accidental int coercion.
    return round(float(value), 4)


def infer_upscale_model_native_scale(model_name: str) -> float:
    """Infer an upscale model's native scale from common names like 4x-UltraSharp.

    V1 used the same heuristic: if the model name does not include an Nx token,
    assume 1x and let the explicit target scale drive the final ImageScaleBy.
    """
    name = str(model_name or "").strip().lower()
    match = re.search(r"(?<!\d)(\d+(?:\.\d+)?)x(?!\d)", name)
    if not match:
        match = re.search(r"(?<![a-z0-9])x(\d+(?:\.\d+)?)(?!\d)", name)
    if not match:
        return 1.0
    try:
        value = float(match.group(1))
    except (TypeError, ValueError):
        value = 1.0
    return max(0.1, min(value, 16.0))


def _add_load_image_node(graph: dict[str, Any], next_id: int, image_name: str) -> tuple[int, list[Any]]:
    image_name = str(image_name or "").strip()
    if not image_name:
        raise ValueError("Image Upscale needs a source image.")
    node_id = str(next_id)
    graph[node_id] = {
        "class_type": "LoadImage",
        "inputs": {
            "image": image_name,
            "upload": "image",
        },
    }
    return next_id + 1, [node_id, 0]


def _add_image_scale_by_node(
    graph: dict[str, Any],
    next_id: int,
    image_ref: list[Any],
    *,
    resize_method: str,
    scale_by: float,
) -> tuple[int, list[Any]]:
    ref = _as_ref(image_ref)
    if ref is None:
        raise ValueError("ImageScaleBy needs a valid image reference.")
    node_id = str(next_id)
    graph[node_id] = {
        "class_type": "ImageScaleBy",
        "inputs": {
            "image": list(ref),
            "upscale_method": resize_method,
            "scale_by": _round_scale(_clamp_float(scale_by, 1.0, 0.05, 8.0)),
        },
    }
    return next_id + 1, [node_id, 0]


def _add_save_image_node(graph: dict[str, Any], next_id: int, image_ref: list[Any]) -> tuple[int, list[Any]]:
    ref = _as_ref(image_ref)
    if ref is None:
        raise ValueError("SaveImage needs a valid image reference.")
    node_id = str(next_id)
    graph[node_id] = {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": SAVE_PREFIX,
            "images": list(ref),
        },
    }
    return next_id + 1, [node_id, 0]


def _add_preview_image_node(graph: dict[str, Any], next_id: int, image_ref: list[Any]) -> tuple[int, list[Any]]:
    ref = _as_ref(image_ref)
    if ref is None:
        raise ValueError("PreviewImage needs a valid image reference.")
    node_id = str(next_id)
    graph[node_id] = {
        "class_type": "PreviewImage",
        "inputs": {
            "images": list(ref),
        },
    }
    return next_id + 1, [node_id, 0]



def _add_seedvr2_nodes(graph: dict[str, Any], next_id: int, image_ref: list[Any], clean: dict[str, Any]) -> tuple[int, list[Any], list[str]]:
    ref = _as_ref(image_ref)
    if ref is None:
        raise ValueError("SeedVR2VideoUpscaler needs a valid source image reference.")
    dit_id = str(next_id)
    graph[dit_id] = {
        "class_type": "SeedVR2LoadDiTModel",
        "inputs": {
            "model": str(clean.get("seedvr2_dit_model") or "").strip(),
            "device": str(clean.get("seedvr2_device") or "cuda:0").strip(),
            "blocks_to_swap": int(clean.get("seedvr2_blocks_to_swap") or 0),
            "swap_io_components": bool(clean.get("seedvr2_swap_io_components")),
            "offload_device": str(clean.get("seedvr2_offload_device") or "cpu").strip(),
            "cache_model": bool(clean.get("seedvr2_cache_models")),
            "attention_mode": str(clean.get("seedvr2_attention_mode") or "sdpa").strip(),
        },
    }
    vae_id = str(next_id + 1)
    tile_size = int(clean.get("seedvr2_tile_size") or 1024)
    tile_overlap = int(clean.get("seedvr2_tile_overlap") or 128)
    graph[vae_id] = {
        "class_type": "SeedVR2LoadVAEModel",
        "inputs": {
            "model": str(clean.get("seedvr2_vae_model") or "").strip(),
            "device": str(clean.get("seedvr2_device") or "cuda:0").strip(),
            "encode_tiled": bool(clean.get("seedvr2_encode_tiled")),
            "encode_tile_size": tile_size,
            "encode_tile_overlap": tile_overlap,
            "decode_tiled": bool(clean.get("seedvr2_decode_tiled")),
            "decode_tile_size": tile_size,
            "decode_tile_overlap": tile_overlap,
            "offload_device": str(clean.get("seedvr2_offload_device") or "cpu").strip(),
            "cache_model": bool(clean.get("seedvr2_cache_models")),
        },
    }
    upscale_id = str(next_id + 2)
    graph[upscale_id] = {
        "class_type": "SeedVR2VideoUpscaler",
        "inputs": {
            "image": list(ref),
            "dit": [dit_id, 0],
            "vae": [vae_id, 0],
            "seed": int(clean.get("seedvr2_seed") or 42),
            "resolution": int(clean.get("seedvr2_resolution") or 1080),
            "max_resolution": int(clean.get("seedvr2_max_resolution") or 0),
            "batch_size": int(clean.get("seedvr2_batch_size") or 1),
            "uniform_batch_size": False,
            "color_correction": str(clean.get("seedvr2_color_correction") or "lab"),
            "input_noise_scale": float(clean.get("seedvr2_input_noise_scale") or 0.0),
            "latent_noise_scale": float(clean.get("seedvr2_latent_noise_scale") or 0.0),
            "offload_device": str(clean.get("seedvr2_offload_device") or "cpu"),
            "enable_debug": bool(clean.get("seedvr2_enable_debug")),
        },
    }
    size_note = ""
    if clean.get("seedvr2_source_width") and clean.get("seedvr2_source_height"):
        size_note = (
            f" Source {clean.get('seedvr2_source_width')}x{clean.get('seedvr2_source_height')} "
            f"→ target {clean.get('seedvr2_output_width')}x{clean.get('seedvr2_output_height')} "
            f"via {clean.get('seedvr2_sizing_mode', 'scale_factor')}."
        )
    notes = [
        f"SeedVR2 experimental image upscale will use {clean.get('seedvr2_dit_model')} + {clean.get('seedvr2_vae_model')} at short edge {clean.get('seedvr2_resolution')}px, max edge {clean.get('seedvr2_max_resolution')}px.{size_note}",
        "SeedVR2 support is node-gated and expects models in ComfyUI/models/SEEDVR2/.",
    ]
    return next_id + 3, [upscale_id, 0], notes

def _normalize_for_workflow(source_image_name: str, settings: dict[str, Any] | str | None) -> dict[str, Any]:
    raw_preview = bool(isinstance(settings, dict) and settings.get("preview_image"))
    clean = normalize_settings(settings)
    if raw_preview:
        clean["preview_image"] = True
    clean["source_image_name"] = str(source_image_name or clean.get("source_image_name") or "").strip()
    if not clean["source_image_name"]:
        raise ValueError("Image Upscale needs a source image.")
    clean["scale"] = _round_scale(_clamp_float(clean.get("scale"), DEFAULTS["scale"], 0.25, 8.0))
    return clean


def build_image_upscale_workflow(
    source_image_name: str,
    settings: dict[str, Any] | str | None = None,
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Build the standalone Comfy Image Upscale graph.

    Returns ``(workflow, normalized_payload, compile_notes)`` to match the V1
    builder contract consumed by the extension queue route.
    """
    clean = _normalize_for_workflow(source_image_name, settings)
    upscale_engine = str(clean.get("upscale_engine") or "basic").strip().lower()
    upscale_model = str(clean.get("upscale_model") or "").strip()
    resize_method = str(clean.get("resize_method") or DEFAULTS["resize_method"]).strip().lower()
    scale_by = _round_scale(float(clean.get("scale") or DEFAULTS["scale"]))
    restore_assist = str(clean.get("restore_assist") or "off").strip().lower()
    restore_model = str(clean.get("restore_model") or "").strip()
    restore_fidelity = _round_scale(_clamp_float(clean.get("restore_fidelity", DEFAULTS["restore_fidelity"]), DEFAULTS["restore_fidelity"], 0.0, 1.0))
    restore_detection = str(clean.get("restore_detection") or DEFAULTS["restore_detection"]).strip() or DEFAULTS["restore_detection"]

    graph: dict[str, Any] = {}
    compile_notes: list[str] = []
    next_id = 1
    next_id, current_image_ref = _add_load_image_node(graph, next_id, clean["source_image_name"])

    native_scale = 1.0
    applied_model_scale_correction = False
    seedvr2_applied = False
    if upscale_engine == SEEDVR2_ENGINE_ID:
        next_id, current_image_ref, seedvr2_notes = _add_seedvr2_nodes(graph, next_id, current_image_ref, clean)
        compile_notes.extend(seedvr2_notes)
        seedvr2_applied = True
    elif upscale_model:
        loader_id = str(next_id)
        graph[loader_id] = {
            "class_type": "UpscaleModelLoader",
            "inputs": {"model_name": upscale_model},
        }
        upscale_id = str(next_id + 1)
        graph[upscale_id] = {
            "class_type": "ImageUpscaleWithModel",
            "inputs": {
                "upscale_model": [loader_id, 0],
                "image": list(current_image_ref),
            },
        }
        current_image_ref = [upscale_id, 0]
        next_id += 2
        native_scale = infer_upscale_model_native_scale(upscale_model)
        extra_scale = scale_by / native_scale if native_scale > 0 else scale_by
        if abs(extra_scale - 1.0) > 0.01:
            next_id, current_image_ref = _add_image_scale_by_node(
                graph,
                next_id,
                current_image_ref,
                resize_method=resize_method,
                scale_by=extra_scale,
            )
            applied_model_scale_correction = True
        compile_notes.append(f"Image Upscale will use {upscale_model} with a target scale of {scale_by}x.")
    else:
        next_id, current_image_ref = _add_image_scale_by_node(
            graph,
            next_id,
            current_image_ref,
            resize_method=resize_method,
            scale_by=scale_by,
        )
        compile_notes.append(f"Image Upscale will use interpolation-only resize at {scale_by}x ({resize_method}).")

    restore_applied = False
    if restore_assist == "codeformer" and restore_model:
        loader_id = str(next_id)
        graph[loader_id] = {
            "class_type": "FaceRestoreModelLoader",
            "inputs": {"model_name": restore_model},
        }
        restore_id = str(next_id + 1)
        graph[restore_id] = {
            "class_type": "FaceRestoreCFWithModel",
            "inputs": {
                "facerestore_model": [loader_id, 0],
                "image": list(current_image_ref),
                "facedetection": restore_detection,
                "codeformer_fidelity": restore_fidelity,
            },
        }
        current_image_ref = [restore_id, 0]
        next_id += 2
        restore_applied = True
        compile_notes.append(f"CodeFormer restore assist will run with {restore_model} at fidelity {restore_fidelity}.")
    elif restore_assist != "off":
        compile_notes.append("Restore assist was requested, but Neo skipped it because no compatible restore model was selected.")

    # PreviewImage is optional in the support matrix. Keep it opt-in so a basic
    # SaveImage path remains valid on minimal Comfy installs, while tests/UI can
    # request preview diagnostics later.
    if bool(clean.get("preview_image")):
        next_id, _preview_ref = _add_preview_image_node(graph, next_id, current_image_ref)

    next_id, _saved_image_ref = _add_save_image_node(graph, next_id, current_image_ref)

    normalized_payload = {
        **deepcopy(clean),
        "mode": "image_upscale_finish",
        "batch_size": 1,
        "source_image_name": clean["source_image_name"],
        "image_upscale_profile": clean.get("profile", DEFAULTS["profile"]),
        "image_upscale_engine": upscale_engine,
        "image_upscale_model": upscale_model,
        "image_upscale_scale": scale_by,
        "image_upscale_resize_method": resize_method,
        "image_upscale_restore_assist": restore_assist,
        "image_upscale_restore_model": restore_model if restore_assist == "codeformer" else "",
        "image_upscale_restore_fidelity": restore_fidelity if restore_assist == "codeformer" else DEFAULTS["restore_fidelity"],
        "image_upscale_restore_detection": restore_detection if restore_assist == "codeformer" else DEFAULTS["restore_detection"],
        "_neo_extension_id": EXTENSION_ID,
        "_neo_extension_version": EXTENSION_VERSION,
        "_neo_workflow_phase": WORKFLOW_PHASE,
        "_neo_processing_system": "image_upscale",
        "_neo_processing_output_policy": "append_derived",
        "_neo_processing_uses_prompt_context": False,
        "_neo_upscale_native_model_scale": native_scale,
        "_neo_upscale_model_scale_correction": applied_model_scale_correction,
        "_neo_codeformer_applied": restore_applied,
        "_neo_seedvr2_applied": seedvr2_applied,
        "_neo_save_prefix": SAVE_PREFIX,
    }
    return graph, normalized_payload, compile_notes
