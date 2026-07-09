"""Executable ComfyUI graph wiring for the LayerDiffuse external extension.

Phase 12 scope:
- Sync Neo prompt-driven templates to the verified standalone ComfyUI SDXL RGBA workflow.
- Use the official LayeredDiffusionApply -> KSampler -> VAEDecode -> LayeredDiffusionDecodeRGBA chain.
- Save the RGBA decode as the primary output instead of the plain RGB preview.
- Keep RGB preview/split outputs as explicit sidecars.

This module is extension-local. It does not patch Neo core. Neo's external
workflow runtime may call `build_comfyui_graph(...)` from the workflow patch.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

GRAPH_WIRING_VERSION = "layerdiffuse-verified-comfy-graph-v2-phase-n-preserve-blend"
EXTENSION_ID = "image.layerdiffuse"
VERIFIED_REFERENCE_WORKFLOW = "layer_diffusion_fg_example_rgba.json"
VERIFIED_REFERENCE_CHAIN = [
    "CheckpointLoaderSimple",
    "LayeredDiffusionApply",
    "KSampler",
    "VAEDecode",
    "LayeredDiffusionDecodeRGBA",
    "SaveImage",
]

PROMPT_DRIVEN_EXECUTABLE_MODES = {"transparent_asset", "rgb_alpha_split", "overlay_fx"}
IMAGE_CONDITIONED_EXECUTABLE_MODES = {"foreground_on_background", "background_aware_blend", "extract_foreground"}
BACKGROUND_MODES_NEED_OBJECT_INFO = {
    "extract_background",
    "generate_fg_from_bg",
    "generate_bg_from_fg",
    "joint_bg_fg_blend_sd15",
}

DEFAULTS: dict[str, Any] = {
    "ckpt_name": "sd_xl_base_1.0.safetensors",
    "width": 1024,
    "height": 1024,
    "batch_size": 1,
    "seed": 0,
    "steps": 28,
    "cfg": 5.0,
    "sampler_name": "dpmpp_2m_sde",
    "scheduler": "karras",
    "denoise": 1.0,
    "negative_prompt": "solid background, gray background, white background, black background, scenery, watermark, text, blurry, low quality",
    "layerdiffuse_weight": 1.0,
    "sub_batch_size": 16,
}


def _clean(value: Any, fallback: str = "") -> str:
    text = str(value if value is not None else "").strip()
    return text or fallback




def _seed(value: Any, fallback: int = 0) -> int:
    try:
        seed = int(value)
    except Exception:
        seed = int(fallback)
    if seed < 0:
        return int(fallback) if int(fallback) >= 0 else 0
    return seed

def _num(value: Any, fallback: int | float) -> int | float:
    try:
        if isinstance(fallback, int) and not isinstance(fallback, bool):
            return int(value)
        return float(value)
    except Exception:
        return fallback


def _clamp_float(value: Any, fallback: float, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = float(fallback)
    return max(float(min_value), min(float(max_value), number))


def _multiple_of_64(value: Any, fallback: int) -> int:
    try:
        ivalue = int(value)
    except Exception:
        ivalue = fallback
    ivalue = max(64, ivalue)
    return int(round(ivalue / 64.0) * 64)


def sd_version_for(model_family: str | None) -> str:
    family = _clean(model_family, "sdxl").lower().replace(" ", "_")
    if family in {"sd", "sd15", "sd1.5", "sd_1_5", "sd1_5"}:
        return "SD1x"
    return "SDXL"


def layerdiffuse_config_for(mode: str, model_family: str | None) -> str:
    sd_version = sd_version_for(model_family)
    if sd_version == "SD1x":
        return "SD15, Attention Injection, attn_sharing"
    # Phase 12 sync: the verified standalone ComfyUI workflow from ComfyUI-layerdiffuse
    # uses SDXL Conv Injection for foreground RGBA generation. Keep this default so Neo
    # matches the known-good graph before adding advanced per-mode tuning.
    return "SDXL, Conv Injection"


def context_value(context: Mapping[str, Any] | None, *keys: str, fallback: Any = None) -> Any:
    context = context or {}
    for key in keys:
        value = context.get(key)
        if value not in (None, ""):
            return value
    return fallback


def normalized_context(context: Mapping[str, Any] | None, effective_state: Mapping[str, Any] | None = None) -> dict[str, Any]:
    context = dict(context or {})
    effective_state = dict(effective_state or {})
    model_family = _clean(effective_state.get("model_family") or context_value(context, "model_family", "family", fallback="sdxl"), "sdxl")
    width = _multiple_of_64(context_value(context, "width", "W", fallback=DEFAULTS["width"]), DEFAULTS["width"])
    height = _multiple_of_64(context_value(context, "height", "H", fallback=DEFAULTS["height"]), DEFAULTS["height"])
    return {
        "ckpt_name": _clean(context_value(context, "ckpt_name", "checkpoint", "model", fallback=DEFAULTS["ckpt_name"]), DEFAULTS["ckpt_name"]),
        "positive_prompt": _clean(context_value(context, "prompt", "positive_prompt", "positive", fallback="")),
        "negative_prompt": _clean(context_value(context, "negative_prompt", "negative", fallback=DEFAULTS["negative_prompt"]), DEFAULTS["negative_prompt"]),
        "width": width,
        "height": height,
        "batch_size": 1,
        "seed": _seed(context_value(context, "actual_seed", "seed", fallback=DEFAULTS["seed"]), DEFAULTS["seed"]),
        "steps": int(_num(context_value(context, "steps", fallback=DEFAULTS["steps"]), DEFAULTS["steps"])),
        "cfg": float(_num(context_value(context, "cfg", "cfg_scale", fallback=DEFAULTS["cfg"]), DEFAULTS["cfg"])),
        "sampler_name": _clean(context_value(context, "sampler_name", "sampler", fallback=DEFAULTS["sampler_name"]), DEFAULTS["sampler_name"]),
        "scheduler": _clean(context_value(context, "scheduler", fallback=DEFAULTS["scheduler"]), DEFAULTS["scheduler"]),
        "denoise": float(_num(context_value(context, "denoise", "denoising_strength", fallback=DEFAULTS["denoise"]), DEFAULTS["denoise"])),
        "sd_version": sd_version_for(model_family),
        "model_family": model_family,
        "sub_batch_size": int(_num(context_value(context, "sub_batch_size", fallback=DEFAULTS["sub_batch_size"]), DEFAULTS["sub_batch_size"])),
        "layerdiffuse_weight": float(_num(context_value(context, "layerdiffuse_weight", fallback=DEFAULTS["layerdiffuse_weight"]), DEFAULTS["layerdiffuse_weight"])),
    }


def _save_node(prefix: str, source_node: str = "10") -> dict[str, Any]:
    return {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": prefix,
            "images": [source_node, 0],
        },
    }



def _optional_image_name(value: Any, fallback: str = "") -> str:
    return _clean(value, fallback)

def _image_name(value: Any, fallback: str = "") -> str:
    """Return a required ComfyUI LoadImage-compatible input-folder image name.

    Image-conditioned LayerDiffuse modes must never compile a LoadImage node with
    an empty string. Comfy resolves empty values to its input directory and then
    crashes with a confusing FileNotFoundError against ``ComfyUI/input/``. Neo
    should fail earlier with a clear missing-slot message.
    """
    text = _clean(value, fallback)
    if not text:
        raise ValueError("LayerDiffuse image-conditioned mode requires a non-empty Comfy input filename for each required slot.")
    return text


def _vae_encode_node(image_node: str, vae_ref: list[Any] | None = None) -> dict[str, Any]:
    return {
        "class_type": "VAEEncode",
        "inputs": {
            "pixels": [image_node, 0],
            "vae": vae_ref or ["1", 2],
        },
    }


def _load_image_node(image_name: str) -> dict[str, Any]:
    return {
        "class_type": "LoadImage",
        "inputs": {"image": image_name, "upload": "image"},
    }


def _layerdiffuse_cond_config(condition_kind: str, model_family: str | None) -> str:
    sd_version = sd_version_for(model_family)
    if sd_version == "SD1x":
        # The upstream SD1.5 conditional modes are not enabled by this phase.
        return "SD15, Foreground, attn_sharing"
    if condition_kind == "background":
        return "SDXL, Background"
    return "SDXL, Foreground"


def build_image_conditioned_graph(
    *,
    mode: str,
    context: Mapping[str, Any] | None = None,
    effective_state: Mapping[str, Any] | None = None,
    filename_prefix: str = "Neo_LayerDiffuse",
) -> dict[str, Any]:
    """Build executable SDXL image-conditioned LayerDiffuse graphs.

    Supported Phase C modes:
    - foreground_on_background: prompt + background image -> transparent/preview output.
    - background_aware_blend: prompt + foreground image + background image ->
      LayerDiffuse foreground harmonization composited over the requested background.
    - extract_foreground: composite/source + known background -> extracted foreground-ish output.

    The graphs deliberately use only stable upstream node signatures from
    huchenlei/ComfyUI-layerdiffuse and standard ComfyUI nodes.
    """
    mode = _clean(mode, "foreground_on_background")
    if mode not in IMAGE_CONDITIONED_EXECUTABLE_MODES:
        raise ValueError(f"Mode '{mode}' is not image-conditioned executable.")
    ctx = normalized_context(context, effective_state)
    raw = dict(effective_state.get("raw_state") or effective_state or {}) if isinstance(effective_state, Mapping) else {}
    # Build_workflow_patch passes normalized raw state as the first argument and
    # some tests/callers pass slot fields directly in effective_state. Prefer
    # explicit effective raw_state when present, but do not drop normalized slot
    # values from the raw_state argument.
    raw_background_name = raw.get("background_image_id") or context_value(context, "background_image_id")
    raw_foreground_name = raw.get("foreground_image_id") or context_value(context, "foreground_image_id")
    raw_source_name = raw.get("source_image_id") or context_value(context, "source_image_id")
    background_name = _optional_image_name(raw_background_name)
    foreground_name = _optional_image_name(raw_foreground_name)
    source_name = _optional_image_name(raw_source_name)
    prefix = _clean(filename_prefix, "Neo_LayerDiffuse")

    graph: dict[str, dict[str, Any]] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ctx["ckpt_name"]}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": ctx["positive_prompt"], "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": ctx["negative_prompt"], "clip": ["1", 1]}},
    }

    if mode == "foreground_on_background":
        background_name = _image_name(background_name)
        graph.update({
            "4": _load_image_node(background_name),
            "5": _vae_encode_node("4"),
            "6": {
                "class_type": "LayeredDiffusionCondApply",
                "inputs": {
                    "model": ["1", 0],
                    "cond": ["2", 0],
                    "uncond": ["3", 0],
                    "latent": ["5", 0],
                    "config": _layerdiffuse_cond_config("background", ctx["model_family"]),
                    "weight": ctx["layerdiffuse_weight"],
                },
            },
            "7": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["6", 1], "negative": ["6", 2], "latent_image": ["5", 0], "seed": ctx["seed"], "steps": ctx["steps"], "cfg": ctx["cfg"], "sampler_name": ctx["sampler_name"], "scheduler": ctx["scheduler"], "denoise": ctx["denoise"]}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
            "10": {"class_type": "LayeredDiffusionDecodeRGBA", "inputs": {"samples": ["7", 0], "images": ["8", 0], "sd_version": ctx["sd_version"], "sub_batch_size": ctx["sub_batch_size"]}},
            "11": _save_node(f"{prefix}_fg_on_bg_rgba", "10"),
            "12": _save_node(f"{prefix}_fg_on_bg_preview", "8"),
        })
        return graph

    if mode == "background_aware_blend":
        foreground_name = _image_name(foreground_name)
        background_name = _image_name(background_name)
        # LayeredDiffusionCondApply harmonizes/softens the foreground and emits a
        # foreground image + alpha mask through LayeredDiffusionDecode. The final
        # compositor must be the only saved image path for this mode; a debug
        # foreground SaveImage node can be picked up by Neo's generic Comfy history
        # collector and make the UI show the pre-composite foreground instead of
        # the requested background blend.
        #
        # Phase N: Background-aware blend should preserve the uploaded foreground.
        # The base image route usually carries denoise=1.0 for txt2img, but using
        # that value here fully regenerates the foreground and creates a melted
        # face/body. Use an extension-local blend strength default instead.
        blend_denoise = _clamp_float(
            context_value(context, "layerdiffuse_blend_strength", "blend_strength", "blend_denoise", fallback=0.35),
            0.35,
            0.05,
            1.0,
        )
        graph.update({
            "4": _load_image_node(foreground_name),
            "5": _vae_encode_node("4"),
            "6": {
                "class_type": "LayeredDiffusionCondApply",
                "inputs": {
                    "model": ["1", 0],
                    "cond": ["2", 0],
                    "uncond": ["3", 0],
                    "latent": ["5", 0],
                    "config": _layerdiffuse_cond_config("foreground", ctx["model_family"]),
                    "weight": ctx["layerdiffuse_weight"],
                },
            },
            "7": {"class_type": "KSampler", "inputs": {"model": ["6", 0], "positive": ["6", 1], "negative": ["6", 2], "latent_image": ["5", 0], "seed": ctx["seed"], "steps": ctx["steps"], "cfg": ctx["cfg"], "sampler_name": ctx["sampler_name"], "scheduler": ctx["scheduler"], "denoise": blend_denoise}},
            "8": {"class_type": "VAEDecode", "inputs": {"samples": ["7", 0], "vae": ["1", 2]}},
            "10": {"class_type": "LayeredDiffusionDecode", "inputs": {"samples": ["7", 0], "images": ["8", 0], "sd_version": ctx["sd_version"], "sub_batch_size": ctx["sub_batch_size"]}},
            "13": _load_image_node(background_name),
            # Use the original foreground alpha as the compositor cutout mask.
            # LayeredDiffusionDecode's mask can represent the generated canvas rather
            # than the uploaded transparent foreground, which causes the full red/source
            # image to cover the destination background. Comfy LoadImage returns an
            # inverted alpha-style mask for transparent PNGs, so InvertMask gives the
            # subject area for ImageCompositeMasked.
            "17": {"class_type": "InvertMask", "inputs": {"mask": ["4", 1]}},
            "15": {
                "class_type": "ImageCompositeMasked",
                "inputs": {
                    "destination": ["13", 0],
                    "source": ["10", 0],
                    "x": 0,
                    "y": 0,
                    "resize_source": False,
                    "mask": ["17", 0],
                },
            },
            "11": _save_node(f"{prefix}_blend", "15"),
            "12": _save_node(f"{prefix}_blend_preview", "15"),
        })
        return graph

    # extract_foreground
    source_name = _image_name(source_name)
    background_name = _image_name(background_name)
    graph.update({
        "4": _load_image_node(source_name),
        "5": _vae_encode_node("4"),  # blended/source latent
        "6": _load_image_node(background_name),
        "7": _vae_encode_node("6"),  # known background latent
        "8": {
            "class_type": "LayeredDiffusionDiffApply",
            "inputs": {
                "model": ["1", 0],
                "cond": ["2", 0],
                "uncond": ["3", 0],
                "blended_latent": ["5", 0],
                "latent": ["7", 0],
                "config": _layerdiffuse_cond_config("background", ctx["model_family"]),
                "weight": ctx["layerdiffuse_weight"],
            },
        },
        "9": {"class_type": "KSampler", "inputs": {"model": ["8", 0], "positive": ["8", 1], "negative": ["8", 2], "latent_image": ["5", 0], "seed": ctx["seed"], "steps": ctx["steps"], "cfg": ctx["cfg"], "sampler_name": ctx["sampler_name"], "scheduler": ctx["scheduler"], "denoise": ctx["denoise"]}},
        "10": {"class_type": "VAEDecode", "inputs": {"samples": ["9", 0], "vae": ["1", 2]}},
        "11": {"class_type": "LayeredDiffusionDecodeRGBA", "inputs": {"samples": ["9", 0], "images": ["10", 0], "sd_version": ctx["sd_version"], "sub_batch_size": ctx["sub_batch_size"]}},
        "12": _save_node(f"{prefix}_extracted_rgba", "11"),
        "13": {"class_type": "LayeredDiffusionDecode", "inputs": {"samples": ["9", 0], "images": ["10", 0], "sd_version": ctx["sd_version"], "sub_batch_size": ctx["sub_batch_size"]}},
        "14": _save_node(f"{prefix}_extracted_rgb", "13"),
        "15": _save_node(f"{prefix}_extract_preview", "10"),
    })
    return graph


def build_prompt_driven_graph(
    *,
    mode: str,
    context: Mapping[str, Any] | None = None,
    effective_state: Mapping[str, Any] | None = None,
    filename_prefix: str = "Neo_LayerDiffuse",
) -> dict[str, Any]:
    """Build an executable ComfyUI API prompt for prompt-driven LayerDiffuse modes.

    Primary output is node 11, which saves the image produced by
    LayeredDiffusionDecodeRGBA. Node 12 saves the plain RGB VAEDecode preview only.
    For split modes, node 13/14 save split outputs from LayeredDiffusionDecode and
    keep RGBA as the primary output for editor-safe transparency.
    """
    mode = _clean(mode, "transparent_asset")
    if mode not in PROMPT_DRIVEN_EXECUTABLE_MODES:
        raise ValueError(f"Mode '{mode}' is not prompt-driven executable in Phase 11.")
    ctx = normalized_context(context, effective_state)
    ld_config = layerdiffuse_config_for(mode, ctx["model_family"])
    prefix = _clean(filename_prefix, "Neo_LayerDiffuse")

    graph: dict[str, dict[str, Any]] = {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": ctx["ckpt_name"]},
        },
        "2": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ctx["positive_prompt"], "clip": ["1", 1]},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": ctx["negative_prompt"], "clip": ["1", 1]},
        },
        "4": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": ctx["width"], "height": ctx["height"], "batch_size": 1},
        },
        "5": {
            "class_type": "LayeredDiffusionApply",
            "inputs": {"model": ["1", 0], "config": ld_config, "weight": ctx["layerdiffuse_weight"]},
        },
        "6": {
            "class_type": "KSampler",
            "inputs": {
                "model": ["5", 0],
                "positive": ["2", 0],
                "negative": ["3", 0],
                "latent_image": ["4", 0],
                "seed": ctx["seed"],
                "steps": ctx["steps"],
                "cfg": ctx["cfg"],
                "sampler_name": ctx["sampler_name"],
                "scheduler": ctx["scheduler"],
                "denoise": ctx["denoise"],
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["1", 2]},
        },
        "10": {
            "class_type": "LayeredDiffusionDecodeRGBA",
            "inputs": {
                "samples": ["6", 0],
                "images": ["7", 0],
                "sd_version": ctx["sd_version"],
                "sub_batch_size": ctx["sub_batch_size"],
            },
        },
        "11": _save_node(f"{prefix}_rgba", "10"),
        "12": _save_node(f"{prefix}_preview_rgb", "7"),
    }

    if mode in {"rgb_alpha_split", "overlay_fx"}:
        graph["13"] = {
            "class_type": "LayeredDiffusionDecode",
            "inputs": {
                "samples": ["6", 0],
                "images": ["7", 0],
                "sd_version": ctx["sd_version"],
                "sub_batch_size": ctx["sub_batch_size"],
            },
        }
        graph["14"] = _save_node(f"{prefix}_rgb", "13")
        # The LayeredDiffusionDecode MASK output is declared for the Neo output collector.
        # If the local runtime supports mask-saving nodes, Neo can bind ["13", 1] as alpha_mask.
        # We do not guess a custom mask-to-image saver here to avoid invalid node dependency.

    return graph


def build_output_bindings(mode: str) -> dict[str, Any]:
    if mode == "foreground_on_background":
        return {
            "rgba_image": {"node_id": "11", "source_node_id": "10", "source_output_index": 0, "role": "primary", "required": True},
            "preview_image": {"node_id": "12", "source_node_id": "8", "source_output_index": 0, "role": "preview", "required": True},
            "alpha_mask": {"node_id": None, "source_node_id": "10", "source_output_index": 0, "role": "mask", "required": False, "collector_required": True},
        }
    if mode == "background_aware_blend":
        return {
            "blended_image": {"node_id": "11", "source_node_id": "15", "source_output_index": 0, "role": "primary", "required": True},
            "preview_image": {"node_id": "12", "source_node_id": "15", "source_output_index": 0, "role": "preview", "required": True},
            "alpha_mask": {"node_id": None, "source_node_id": "17", "source_output_index": 0, "role": "mask", "required": False, "collector_required": True},
        }
    if mode == "extract_foreground":
        return {
            "rgba_image": {"node_id": "12", "source_node_id": "11", "source_output_index": 0, "role": "primary", "required": True},
            "rgb_image": {"node_id": "14", "source_node_id": "13", "source_output_index": 0, "role": "sidecar", "required": False},
            "alpha_mask": {"node_id": None, "source_node_id": "13", "source_output_index": 1, "role": "mask", "required": False, "collector_required": True},
            "preview_image": {"node_id": "15", "source_node_id": "10", "source_output_index": 0, "role": "preview", "required": True},
        }
    base = {
        "rgba_image": {"node_id": "11", "source_node_id": "10", "source_output_index": 0, "role": "primary", "required": True},
        "preview_image": {"node_id": "12", "source_node_id": "7", "source_output_index": 0, "role": "preview", "required": True},
    }
    if mode in {"rgb_alpha_split", "overlay_fx"}:
        base["rgb_image"] = {"node_id": "14", "source_node_id": "13", "source_output_index": 0, "role": "sidecar", "required": False}
        base["alpha_mask"] = {"node_id": None, "source_node_id": "13", "source_output_index": 1, "role": "mask", "required": False, "collector_required": True}
    else:
        base["alpha_mask"] = {"node_id": None, "source_node_id": "10", "source_output_index": 0, "role": "mask", "required": False, "collector_required": True}
    return base


def build_comfyui_graph(
    raw_state: Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    effective_state: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    raw_state = dict(raw_state or {})
    effective_state = dict(effective_state or {})
    mode = _clean(effective_state.get("mode") or raw_state.get("mode"), "transparent_asset")
    if mode in BACKGROUND_MODES_NEED_OBJECT_INFO:
        return {
            "graph_wiring_version": GRAPH_WIRING_VERSION,
            "extension_id": EXTENSION_ID,
            "mode": mode,
            "executable": False,
            "blocked_reason": "requires_future_mode_specific_workflow_mapping",
            "notes": [
                "Core Phase C enables foreground_on_background, background_aware_blend, and extract_foreground only.",
                "Other upstream LayerDiffuse modes remain intentionally guarded.",
            ],
        }
    if mode in IMAGE_CONDITIONED_EXECUTABLE_MODES:
        graph = build_image_conditioned_graph(mode=mode, context={**raw_state, **dict(context or {})}, effective_state={**raw_state, **effective_state})
    else:
        graph = build_prompt_driven_graph(mode=mode, context=context, effective_state=effective_state)
    return {
        "graph_wiring_version": GRAPH_WIRING_VERSION,
        "verified_reference_workflow": VERIFIED_REFERENCE_WORKFLOW,
        "verified_reference_chain": VERIFIED_REFERENCE_CHAIN,
        "extension_id": EXTENSION_ID,
        "mode": mode,
        "format": "comfyui_api_prompt",
        "executable": True,
        "primary_output_type": "rgba_image",
        "primary_output_node": "11" if mode != "extract_foreground" else "12",
        "preview_output_node": "12" if mode != "extract_foreground" else "15",
        "output_bindings": build_output_bindings(mode),
        "graph": graph,
        "guardrails": {
            "batch_size": 1,
            "dimensions_multiple_of": 64,
            "primary_output_must_use": "LayeredDiffusionDecodeRGBA -> SaveImage",
            "layerdiffuse_apply_must_feed_sampler": True,
            "image_conditioned_modes_enabled": sorted(IMAGE_CONDITIONED_EXECUTABLE_MODES),
            "plain_vaedecode_is_preview_only": True,
        },
    }


def assert_graph_routes_rgba_primary(graph_package: Mapping[str, Any]) -> None:
    """Raise AssertionError if the graph saves RGB preview as the primary output."""
    graph = dict(graph_package.get("graph") or {})
    rgba_node = graph.get("10") or {}
    save_node = graph.get("11") or {}
    preview_node = graph.get("12") or {}
    assert graph.get("5", {}).get("class_type") == "LayeredDiffusionApply", "node 5 must apply LayerDiffuse before sampling"
    assert graph.get("6", {}).get("inputs", {}).get("model") == ["5", 0], "KSampler must receive the LayerDiffuse-patched model"
    assert rgba_node.get("class_type") == "LayeredDiffusionDecodeRGBA", "node 10 must decode RGBA"
    assert save_node.get("class_type") == "SaveImage", "node 11 must save primary RGBA output"
    assert save_node.get("inputs", {}).get("images") == ["10", 0], "primary save must use RGBA decode output"
    assert preview_node.get("inputs", {}).get("images") == ["7", 0], "preview save must use plain VAE decode only"


def clone_graph_package(package: Mapping[str, Any]) -> dict[str, Any]:
    return deepcopy(dict(package))
