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
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile


@dataclass(frozen=True)
class FluxGGUFDefaults:
    """Provider compiler defaults for the first Flux GGUF Comfy route.

    Phase 12.10 enables txt2img only. Image-conditioned and outpaint routes need
    separate latent/source-image semantics and remain planned/gated.
    """

    width: int = 1024
    height: int = 1024
    steps: int = 25
    cfg: float = 1.0
    negative_mode: str = "empty_ignored_by_cfg1"
    flux_guidance: float = 3.5
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    latent_node: str = "EmptySD3LatentImage"
    unet_loader: str = "UnetLoaderGGUF"
    dual_clip_loader: str = "DualCLIPLoaderGGUF"


FLUX_GGUF_DEFAULTS = FluxGGUFDefaults()


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
    return candidate if candidate in {"UnetLoaderGGUF", "LoaderGGUF"} else FLUX_GGUF_DEFAULTS.unet_loader


def _normalize_gguf_dual_clip_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"DualCLIPLoaderGGUF"} else FLUX_GGUF_DEFAULTS.dual_clip_loader


def _normalize_gguf_single_clip_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"CLIPLoaderGGUF", "ClipLoaderGGUF"} else "CLIPLoaderGGUF"


def _is_gguf_file(value: Any) -> bool:
    return str(value or "").strip().lower().endswith(".gguf")


def _flux2_klein_clip_loader_for_encoder(text_encoder: Any, requested_loader: Any) -> str:
    """Pick the correct single text-encoder loader for Klein.

    The Flux.2 Klein transformer can be GGUF while the Qwen3 text encoder can
    still be a native safetensors file. In that mixed setup, Comfy must use
    CLIPLoader(type=flux2), not CLIPLoaderGGUF. Using CLIPLoaderGGUF against a
    safetensors Qwen3 encoder can load a malformed/incomplete clip object and
    crash during CLIPTextEncode with NoneType weight/device errors.
    """
    if _is_gguf_file(text_encoder):
        return _normalize_gguf_single_clip_loader(requested_loader)
    return "CLIPLoader"


_FLUX2_KLEIN_VARIANTS = {"klein", "flux2_klein", "flux_2_klein", "klein_4b", "klein_9b", "klein_4b_distilled", "klein_9b_distilled"}


def _normalized_variant(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _is_flux2_klein_variant(value: Any) -> bool:
    return _normalized_variant(value) in _FLUX2_KLEIN_VARIANTS


def _is_flux2_klein_model_name(value: Any) -> bool:
    text = str(value or "").strip().lower().replace("_", "-")
    return "klein" in text and ("flux-2" in text or "flux2" in text or text.startswith("klein"))


def _resolve_flux2_klein_variant(params: dict[str, Any], model_name: Any) -> str | None:
    """Resolve Klein intent from UI variant first, then selected model name.

    The Image UI can still submit legacy `flux_variant=dev` while the user has
    selected a FLUX.2 Klein GGUF model. In that case the provider compiler must
    not fall back to the legacy DualCLIPLoaderGGUF graph, because Klein requires
    the single Qwen3 Flux2 path.
    """
    raw_variant = _param(params, "flux_variant", "variant", default="")
    if _is_flux2_klein_variant(raw_variant):
        return str(raw_variant)
    if _is_flux2_klein_model_name(model_name):
        name = str(model_name or "").lower()
        return "klein_9b" if "9b" in name else "klein_4b"
    return None


def _default_klein_encoder(variant: str) -> str:
    normalized = _normalized_variant(variant)
    return "qwen_3_8b_fp8mixed.safetensors" if "9b" in normalized else "qwen_3_4b.safetensors"


def _default_klein_model(variant: str) -> str:
    normalized = _normalized_variant(variant)
    return "flux-2-klein-9b-fp8.gguf" if "9b" in normalized else "flux-2-klein-4b-fp8.gguf"


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


def _image_name_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
    return str(value or "").strip().split("/")[-1].split("\\")[-1]


def _source_image_name(params: dict[str, Any]) -> str:
    for key in (
        "comfy_source_image_name",
        "source_image_name",
        "source_image",
        "source_image_path",
        "source_image_url",
        "init_image",
        "image",
    ):
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _reference_image_name(params: dict[str, Any], lane: int) -> str:
    if lane == 2:
        keys = (
            "comfy_source_image_2_name",
            "source_image_2_name",
            "source_image__2_name",
            "reference_image_2_name",
            "source_image_2",
            "source_image_2_path",
            "source_image_2_url",
        )
    elif lane == 3:
        keys = (
            "comfy_source_image_3_name",
            "source_image_3_name",
            "source_image__3_name",
            "composition_image_name",
            "reference_image_3_name",
            "source_image_3",
            "source_image_3_path",
            "source_image_3_url",
        )
    else:
        return ""
    for key in keys:
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _flux_source_stack_metadata(params: dict[str, Any], base_source_name: str) -> dict[str, Any]:
    ref2 = _reference_image_name(params, 2)
    ref3 = _reference_image_name(params, 3)
    if not ref2 and not ref3:
        return {}
    return {
        "flux_multi_source": True,
        "flux_source_images": {
            "base_image_name": base_source_name,
            "reference_image_2_name": ref2,
            "reference_image_2_role": str(params.get("source_image_2_role") or "secondary_subject"),
            "reference_image_3_name": ref3,
            "reference_image_3_role": str(params.get("source_image_3_role") or "composition_guide"),
            "composition_source_mode": str(params.get("composition_source_mode") or params.get("qwen_composition_source_mode") or "source_image"),
        },
        "_neo_flux_gguf_source_stack_payload_parity": True,
        "_neo_flux_gguf_extra_sources_uploaded_for_replay": True,
        "_neo_flux_gguf_extra_sources_conditioning_policy": "metadata_and_adapter_ready; core Flux GGUF latent route uses Image 1 as the source latent anchor",
    }



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
    for key in (
        "comfy_mask_image_name",
        "mask_image_name",
        "mask_image",
        "mask_image_path",
        "inpaint_mask",
        "mask",
    ):
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _build_flux_gguf_latent_branch(
    *,
    workflow: dict[str, Any],
    mode: str,
    params: dict[str, Any],
    width: int,
    height: int,
    batch_count: int,
    vae_ref: list[Any],
    empty_latent_node: str,
    start_id: int,
) -> tuple[int, list[Any], dict[str, Any], list[Any] | None, list[Any] | None]:
    """Build Flux GGUF latent source branch for txt2img/img2img/edit/inpaint/outpaint.

    Flux GGUF image routes stay provider-owned here: no checkpoint/Qwen fallback,
    no UI-specific file handling, and no final-composite-only inpaint masking.
    """
    mode = str(mode or "txt2img")
    metadata: dict[str, Any] = {"_neo_flux_gguf_mode": mode}
    source_ref: list[Any] | None = None
    mask_ref: list[Any] | None = None
    next_id = int(start_id)

    if mode == "txt2img":
        workflow[str(next_id)] = {"class_type": empty_latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        return next_id + 1, [str(next_id), 0], metadata, None, None

    source_name = _source_image_name(params)
    if not source_name:
        raise ValueError(f"Flux GGUF {mode} requires a source image.")

    workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
    source_ref = [str(next_id), 0]
    next_id += 1
    metadata["source_image_name"] = source_name
    metadata.update(_flux_source_stack_metadata(params, source_name))

    if mode == "outpaint":
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        if outpaint_padding_total(outpaint_payload) <= 0:
            raise ValueError("Flux GGUF outpaint requires padding on at least one side.")
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
        # ImagePadForOutpaint returns both the padded pixels and the generated
        # padding mask. Flux must consume that mask as a latent noise mask;
        # otherwise Comfy treats the new side areas as real gray pixels and the
        # sampler preserves them instead of generating the extension.
        source_ref = [str(next_id), 0]
        mask_ref = [str(next_id), 1]
        next_id += 1
        effective_width = max(64, int(working_width) + left + right)
        effective_height = max(64, int(working_height) + top + bottom)
        metadata.update({
            "outpaint_payload": outpaint_payload,
            "_neo_outpaint_contract": outpaint_payload,
            "flux_gguf_outpaint_source_resolution": outpaint_payload.get("source_resolution", {}),
            "flux_gguf_outpaint_source_scale_node": source_scale_meta or {},
            "flux_gguf_outpaint_padding": {"left": left, "top": top, "right": right, "bottom": bottom, "feather": feather, "blur": int(mask.get("blur", 8) or 8)},
            "flux_gguf_outpaint_effective_size": {"width": effective_width, "height": effective_height},
            "_neo_flux_gguf_outpaint_uses_image_pad_mask": True,
            "_neo_flux_gguf_outpaint_uses_latent_noise_mask": True,
            "_neo_flux_gguf_outpaint_uses_differential_diffusion": True,
        })

    if mode == "inpaint":
        mask_name = _mask_image_name(params)
        if not mask_name:
            raise ValueError("Flux GGUF inpaint requires a mask image.")
        workflow[str(next_id)] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        mask_ref = [str(next_id), 0]
        next_id += 1
        grow = max(0, _int_param(params, "mask_grow", _int_param(params, "grow_mask_by", 3)))
        blur = max(0, _int_param(params, "mask_blur", _int_param(params, "blur_mask_by", 0)))
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
        inpaint_target = str(_param(params, "inpaint_target", "mask_mode", default="masked") or "masked").strip().lower()
        if inpaint_target == "unmasked":
            workflow[str(next_id)] = {"class_type": "InvertMask", "inputs": {"mask": list(mask_ref)}}
            mask_ref = [str(next_id), 0]
            next_id += 1
        metadata.update({
            "mask_image_name": mask_name,
            "mask_grow": grow,
            "mask_blur": blur,
            "inpaint_target": inpaint_target,
            "_neo_flux_gguf_inpaint_uses_latent_noise_mask": True,
            "_neo_flux_gguf_inpaint_uses_differential_diffusion": True,
            "_neo_flux_gguf_inpaint_final_composite": True,
            "_neo_flux_gguf_inpaint_composite_feather": max(0, _int_param(params, "flux_inpaint_composite_feather", _int_param(params, "composite_feather", 10))),
        })

    workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": list(vae_ref)}}
    latent_ref: list[Any] = [str(next_id), 0]
    next_id += 1

    if mode in {"inpaint", "outpaint"} and mask_ref is not None:
        workflow[str(next_id)] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(latent_ref), "mask": list(mask_ref)}}
        latent_ref = [str(next_id), 0]
        next_id += 1

    return next_id, latent_ref, metadata, source_ref, mask_ref


def _compile_flux2_klein_gguf_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any],
    conditioning: dict[str, Any],
    effective_prompt: str,
    requested_seed: int,
    seed: int,
) -> CompiledJob:
    """Compile FLUX.2 [klein] GGUF txt2img.

    Klein GGUF is not the legacy Flux GGUF dual-encoder route. It uses a GGUF
    transformer loader, a single Qwen3 text encoder through CLIPLoaderGGUF
    type=flux2, EmptyFlux2LatentImage, FluxGuidance, and the Flux2 VAE.
    """

    params = job.params or {}
    defaults = FLUX_GGUF_DEFAULTS
    mode = str(route.mode or job.mode or "txt2img")
    if mode == "inpaint":
        params = normalize_inpaint_target_aliases(params)
    if mode == "generate":
        mode = "txt2img"
    backend_capabilities = backend_capabilities or {}
    variant = _param(params, "flux_variant", "variant", default="klein_4b")
    unet_loader = _normalize_gguf_unet_loader(
        _param(
            params,
            "gguf_unet_loader",
            "unet_loader",
            "_neo_effective_gguf_unet_loader",
            default=_role_backend_node(backend_capabilities, "gguf", "gguf_unet") or defaults.unet_loader,
        )
    )
    requested_clip_loader = _param(
        params,
        "gguf_clip_loader",
        "gguf_single_clip_loader",
        "clip_loader",
        "_neo_effective_gguf_clip_loader",
        default=_role_backend_node(backend_capabilities, "gguf", "gguf_text_encoder_primary") or "CLIPLoaderGGUF",
    )

    gguf_unet = job.model or _param(params, "gguf_unet", "gguf_model", "model", "model_name", default=_default_klein_model(str(variant)))
    text_encoder = _param(
        params,
        "qwen3_text_encoder",
        "gguf_text_encoder_1",
        "gguf_text_encoder_primary",
        "text_encoder_1",
        "text_encoder_primary",
        "clip_name",
        default=_default_klein_encoder(str(variant)),
    )
    clip_loader = _flux2_klein_clip_loader_for_encoder(text_encoder, requested_clip_loader)
    text_encoder_file_type = "gguf" if _is_gguf_file(text_encoder) else "native"
    vae = _param(params, "vae", "vae_or_ae", "ae", default="flux2-vae.safetensors")
    vae_loader = str(_param(params, "vae_loader", "gguf_vae_loader", default=_vae_loader_for(str(vae))))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    steps = int(_param(params, "steps", default=4))
    sampler = str(_param(params, "sampler", default="euler"))
    scheduler = str(_param(params, "scheduler", default="simple"))
    denoise = float(_param(params, "denoise", default=defaults.denoise))
    flux_guidance = float(_param(params, "flux_guidance", "guidance", default=1.0))
    cfg = float(_param(params, "cfg", default=1.0))
    if cfg <= 0:
        cfg = 1.0
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default="default"))
    # FLUX.2 Klein must always use Comfy CLIP type `flux2`. Older UI state can
    # still submit `clip_type=flux` / `gguf_clip_type=flux` from the legacy Flux
    # GGUF panel. Do not trust those stale route fields for Klein; Comfy rejects
    # CLIPLoader(type=flux) on Flux2-capable builds and requires `flux2`.
    clip_type = "flux2"

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": f"image.{mode}.flux2_klein_gguf",
        "prompt_conditioning_mode": normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw"))),
        "clamp": normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw"))),
        "flux_variant": variant or "klein_4b",
        "gguf_unet": gguf_unet,
        "gguf_model": gguf_unet,
        "text_encoder_1": text_encoder,
        "gguf_text_encoder_1": text_encoder,
        "gguf_text_encoder_primary": text_encoder,
        "qwen3_text_encoder": text_encoder,
        "text_encoder_2": "",
        "gguf_text_encoder_2": "",
        "gguf_clip_mode": "single",
        "gguf_clip_type": "flux2",
        "clip_type": "flux2",
        "vae": vae,
        "flux_guidance": flux_guidance,
        "denoise": denoise,
        "cfg": cfg,
        "steps": steps,
        "flux2_klein_gguf_profile": {
            "family": job.family or "flux2_klein",
            "loader": "gguf",
            "variant": "flux2_klein",
            "compiler": "comfy.flux_gguf.klein",
            "enabled_modes": ["txt2img", "img2img", "edit", "inpaint", "outpaint"],
            "gated_modes": [],
            "provider_nodes": {"gguf_unet_loader": unet_loader, "single_clip_loader": clip_loader, "requested_clip_loader": requested_clip_loader, "vae_loader": vae_loader},
            "text_encoder_policy": "single_qwen3_mixed_loader_flux2",
            "text_encoder_file_type": text_encoder_file_type,
            "loader_rule": "Qwen3 .safetensors uses CLIPLoader(type=flux2); Qwen3 .gguf uses CLIPLoaderGGUF(type=flux2).",
            "latent_node": "EmptyFlux2LatentImage",
            "negative_policy": "ConditioningZeroOut from positive conditioning",
        },
    }

    workflow: dict[str, Any] = {
        "1": {"class_type": unet_loader, "inputs": _gguf_unet_inputs(unet_loader, str(gguf_unet))},
        "2": {"class_type": clip_loader, "inputs": {"clip_name": text_encoder, "type": clip_type, "device": clip_device}},
        "3": {"class_type": vae_loader, "inputs": {"vae_name": vae}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
        "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["5", 0], "guidance": flux_guidance}},
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
    }

    try:
        next_id, latent_ref, route_meta, inpaint_source_ref, inpaint_mask_ref = _build_flux_gguf_latent_branch(
            workflow=workflow,
            mode=mode,
            params=params,
            width=width,
            height=height,
            batch_count=batch_count,
            vae_ref=["3", 0],
            empty_latent_node="EmptyFlux2LatentImage",
            start_id=4 if mode == "txt2img" else 8,
        )
    except ValueError as exc:
        validation.errors.append(str(exc))
        validation.ok = False
        workflow["4"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        next_id, latent_ref, route_meta, inpaint_source_ref, inpaint_mask_ref = 8, ["4", 0], {"notes": [str(exc)]}, None, None

    next_id = max(next_id, max((int(node_id) for node_id in workflow if str(node_id).isdigit()), default=0) + 1)

    sampler_model_ref: list[Any] = ["1", 0]
    if mode in {"inpaint", "outpaint"}:
        workflow[str(next_id)] = {"class_type": "DifferentialDiffusion", "inputs": {"model": sampler_model_ref}}
        sampler_model_ref = [str(next_id), 0]
        next_id += 1

    sampler_id = str(next_id)
    decode_id = str(next_id + 1)
    save_id = str(next_id + 2)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else "euler",
            "scheduler": scheduler if scheduler != "provider_default" else "simple",
            "denoise": denoise,
            "model": sampler_model_ref,
            "positive": ["6", 0],
            "negative": ["7", 0],
            "latent_image": latent_ref,
        },
    }
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    output_image_ref: list[Any] = [decode_id, 0]
    if mode == "inpaint" and inpaint_source_ref and inpaint_mask_ref:
        composite_mask_ref = list(inpaint_mask_ref)
        composite_feather = int(route_meta.get("_neo_flux_gguf_inpaint_composite_feather") or 0) if isinstance(route_meta, dict) else 0
        if composite_feather > 0:
            feather_id = save_id
            save_id = str(int(save_id) + 1)
            workflow[feather_id] = {
                "class_type": "GrowMaskWithBlur",
                "inputs": {"mask": composite_mask_ref, "expand": 0, "incremental_expandrate": 0, "tapered_corners": True, "flip_input": False, "blur_radius": composite_feather, "lerp_alpha": 1, "decay_factor": 1, "fill_holes": False},
            }
            composite_mask_ref = [feather_id, 0]
        composite_id = save_id
        save_id = str(int(save_id) + 1)
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {"destination": list(inpaint_source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": composite_mask_ref},
        }
        output_image_ref = [composite_id, 0]
    workflow[save_id] = {"class_type": "PreviewImage", "inputs": {"images": output_image_ref}}
    actual_params.update(route_meta if isinstance(route_meta, dict) else {})
    if mode == "edit":
        actual_params["_neo_flux2_klein_edit_alias"] = "img2img_latent_anchor"
        actual_params["_neo_flux2_klein_edit_uses_image1_latent_anchor"] = True
    if mode in {"img2img", "edit"}:
        actual_params["_neo_flux2_klein_image1_latent_anchor_validated"] = True
    actual_params["_neo_effective_flux2_klein_gguf_route"] = True
    actual_params["_neo_effective_mode"] = mode
    actual_params["_neo_sampler_node_id"] = sampler_id
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.flux_gguf.klein",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["Klein GGUF compiler owns model/clip refs; LoRA compatibility remains route-matrix experimental."],
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
            "compile_route": {**route.as_dict(), "compiler_id": "comfy.flux_gguf.klein", "workflow_type": f"image.{mode}.flux2_klein_gguf"},
            "capabilities": capabilities,
            "backend_capabilities": backend_capabilities,
            "phase_notes": [
                "Phase M11 keeps FLUX.2 [klein] GGUF as a mixed-loader route when the Qwen3 encoder is safetensors.",
                "Klein GGUF uses a GGUF transformer loader plus CLIPLoader(type=flux2) for Qwen3 safetensors, or CLIPLoaderGGUF(type=flux2) for Qwen3 GGUF.",
                "It does not use the legacy Flux GGUF DualCLIPLoaderGGUF path.",
                "Klein GGUF img2img/edit/inpaint/outpaint use the same provider-owned latent source branch as Flux GGUF with Flux2 latent support; img2img/edit encode Image 1 as the latent anchor, while outpaint consumes the ImagePadForOutpaint mask instead of preserving gray padding.",
            ],
            "prompt_conditioning": conditioning,
        },
    )


def compile_flux_gguf_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile the Phase 12.10 Flux GGUF txt2img graph.

    The V2 contract remains family=flux, loader=gguf, mode=txt2img. Comfy GGUF
    node names stay provider-local in this compiler module.
    """

    params = job.params or {}
    defaults = FLUX_GGUF_DEFAULTS
    mode = str(route.mode or job.mode or "txt2img")
    if mode == "generate":
        mode = "txt2img"
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

    selected_model_name = job.model or _param(params, "gguf_unet", "gguf_model", "unet", "model", "model_name", default="")
    flux_variant = _resolve_flux2_klein_variant(params, selected_model_name)
    if flux_variant:
        # Ensure the Klein compiler sees the resolved variant even when the UI
        # submitted stale legacy Flux variant values such as `dev`.
        job = NeoJob(**{**model_to_dict(job), "params": {**params, "flux_variant": flux_variant}})
        return _compile_flux2_klein_gguf_txt2img(
            provider_id=provider_id,
            base_url=base_url,
            job=job,
            validation=validation,
            route=route,
            capabilities=capabilities,
            backend_capabilities=backend_capabilities or {},
            conditioning=conditioning,
            effective_prompt=effective_prompt,
            requested_seed=requested_seed,
            seed=seed,
        )

    backend_capabilities = backend_capabilities or {}
    unet_loader = _normalize_gguf_unet_loader(
        _param(
            params,
            "gguf_unet_loader",
            "unet_loader",
            "_neo_effective_gguf_unet_loader",
            default=_role_backend_node(backend_capabilities, "gguf", "gguf_unet") or defaults.unet_loader,
        )
    )
    dual_clip_loader = _normalize_gguf_dual_clip_loader(
        _param(
            params,
            "gguf_clip_loader",
            "gguf_dual_clip_loader",
            "clip_loader",
            "_neo_effective_gguf_clip_loader",
            default=_role_backend_node(backend_capabilities, "gguf", "gguf_text_encoder_secondary") or defaults.dual_clip_loader,
        )
    )

    gguf_unet = job.model or _param(params, "gguf_unet", "gguf_model", "unet", "model", "model_name", default="flux1-dev-Q5_K_M.gguf")
    text_encoder_1 = _param(params, "gguf_text_encoder_1", "gguf_text_encoder_primary", "text_encoder_1", "text_encoder_primary", "clip_name1", default="clip_l.gguf")
    text_encoder_2 = _param(params, "gguf_text_encoder_2", "gguf_text_encoder_secondary", "text_encoder_2", "text_encoder_secondary", "clip_name2", default="t5xxl-Q5_K_M.gguf")
    vae = _param(params, "vae", "vae_or_ae", "ae", default="ae.safetensors")
    vae_loader = str(_param(params, "vae_loader", "gguf_vae_loader", default=_vae_loader_for(str(vae))))
    flux_guidance = float(_param(params, "flux_guidance", "gguf_guidance", "guidance", default=defaults.flux_guidance))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    steps = int(_param(params, "steps", default=defaults.steps))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    denoise = float(_param(params, "denoise", default=defaults.denoise))
    # Flux GGUF uses Flux guidance in CLIPTextEncodeFlux as the real guidance control.
    # Do not pass SD-style CFG from the shared UI into KSampler; double guidance
    # can produce muddy/unstable Flux outputs. Keep the sampler CFG neutral.
    requested_cfg = float(_param(params, "cfg", default=defaults.cfg))
    sampler_cfg = float(defaults.cfg)
    flux_negative_mode = str(_param(params, "flux_negative_mode", default=defaults.negative_mode))
    # Historical long-poll fallback used default=900; Phase 12.10M locks the active route budget to 300s.
    poll_timeout_seconds = 300
    poll_interval_ms = int(_param(params, "poll_interval_ms", default=1500))

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.flux_gguf",
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
        "flux_gguf_profile": {
            "family": "flux",
            "loader": "gguf",
            "default_width": defaults.width,
            "default_height": defaults.height,
            "default_steps": defaults.steps,
            "default_flux_guidance": defaults.flux_guidance,
            "compiler": "comfy.flux_gguf",
            "enabled_modes": ["txt2img", "img2img", "edit", "inpaint", "outpaint"],
            "gated_modes": [],
            "provider_nodes": {
                "gguf_unet_loader": unet_loader,
                "gguf_clip_dual_loader": dual_clip_loader,
                "vae_loader": vae_loader,
            },
            "guidance_model": "flux_guidance_controls_prompt_conditioning",
            "sampler_cfg_policy": "force_1.0_for_flux_gguf",
            "negative_mode": flux_negative_mode,
        },
        "gguf_unet": gguf_unet,
        "gguf_text_encoder_1": text_encoder_1,
        "gguf_text_encoder_2": text_encoder_2,
        "vae": vae,
        "flux_guidance": flux_guidance,
        "denoise": denoise,
        "cfg": sampler_cfg,
        "requested_cfg": requested_cfg,
        "sampler_cfg_effective": sampler_cfg,
        "sampler_cfg_source": "forced_flux_neutral_cfg",
        "flux_negative_mode": flux_negative_mode,
        "poll_timeout_seconds": poll_timeout_seconds,
        "poll_interval_ms": poll_interval_ms,
    }

    workflow: dict[str, Any] = {
        "1": {"class_type": unet_loader, "inputs": _gguf_unet_inputs(unet_loader, str(gguf_unet))},
        "2": {
            "class_type": dual_clip_loader,
            "inputs": {
                "clip_name1": text_encoder_1,
                "clip_name2": text_encoder_2,
                "type": str(_param(params, "clip_type", "gguf_clip_type", "text_encoder_type", default="flux")),
            },
        },
        "3": {"class_type": vae_loader, "inputs": {"vae_name": vae}},
        "4": {
            "class_type": "ModelSamplingFlux",
            "inputs": {
                "max_shift": float(_param(params, "max_shift", default=1.15)),
                "base_shift": float(_param(params, "base_shift", default=0.5)),
                "width": width,
                "height": height,
                "model": ["1", 0],
            },
        },
        "5": {
            "class_type": "CLIPTextEncodeFlux",
            "inputs": {"clip_l": effective_prompt, "t5xxl": effective_prompt, "guidance": flux_guidance, "clip": ["2", 0]},
        },
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_negative, "clip": ["2", 0]}},
    }

    try:
        next_id, latent_ref, route_meta, inpaint_source_ref, inpaint_mask_ref = _build_flux_gguf_latent_branch(
            workflow=workflow,
            mode=mode,
            params=params,
            width=width,
            height=height,
            batch_count=batch_count,
            vae_ref=["3", 0],
            empty_latent_node=defaults.latent_node,
            start_id=7,
        )
    except ValueError as exc:
        validation.errors.append(str(exc))
        validation.ok = False
        workflow["7"] = {"class_type": defaults.latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        next_id, latent_ref, route_meta, inpaint_source_ref, inpaint_mask_ref = 8, ["7", 0], {"notes": [str(exc)]}, None, None

    if mode == "outpaint" and isinstance(route_meta, dict):
        effective_size = route_meta.get("flux_gguf_outpaint_effective_size")
        if isinstance(effective_size, dict) and "4" in workflow:
            workflow["4"]["inputs"]["width"] = int(effective_size.get("width") or width)
            workflow["4"]["inputs"]["height"] = int(effective_size.get("height") or height)

    sampler_model_ref: list[Any] = ["4", 0]
    if mode in {"inpaint", "outpaint"}:
        workflow[str(next_id)] = {"class_type": "DifferentialDiffusion", "inputs": {"model": sampler_model_ref}}
        sampler_model_ref = [str(next_id), 0]
        next_id += 1

    sampler_id = str(next_id)
    decode_id = str(next_id + 1)
    save_id = str(next_id + 2)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": sampler_cfg,
            "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
            "denoise": denoise,
            "model": sampler_model_ref,
            "positive": ["5", 0],
            "negative": ["6", 0],
            "latent_image": latent_ref,
        },
    }
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    output_image_ref: list[Any] = [decode_id, 0]
    if mode == "inpaint" and inpaint_source_ref and inpaint_mask_ref:
        composite_mask_ref = list(inpaint_mask_ref)
        composite_feather = int(route_meta.get("_neo_flux_gguf_inpaint_composite_feather") or 0) if isinstance(route_meta, dict) else 0
        if composite_feather > 0:
            feather_id = save_id
            save_id = str(int(save_id) + 1)
            workflow[feather_id] = {
                "class_type": "GrowMaskWithBlur",
                "inputs": {"mask": composite_mask_ref, "expand": 0, "incremental_expandrate": 0, "tapered_corners": True, "flip_input": False, "blur_radius": composite_feather, "lerp_alpha": 1, "decay_factor": 1, "fill_holes": False},
            }
            composite_mask_ref = [feather_id, 0]
        composite_id = save_id
        save_id = str(int(save_id) + 1)
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {"destination": list(inpaint_source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": composite_mask_ref},
        }
        output_image_ref = [composite_id, 0]
    workflow[save_id] = {"class_type": "PreviewImage", "inputs": {"images": output_image_ref}}
    actual_params.update(route_meta if isinstance(route_meta, dict) else {})
    actual_params["_neo_effective_flux_gguf_route"] = True
    actual_params["_neo_effective_mode"] = mode
    actual_params["_neo_sampler_node_id"] = sampler_id
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.flux_gguf",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["Flux GGUF compiler owns UNET and dual-clip refs; ModelSamplingFlux/DifferentialDiffusion consumers are rewired by exact ref."],
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
            "poll_timeout_seconds": poll_timeout_seconds,
            "poll_interval_ms": poll_interval_ms,
            "compile_route": route.as_dict(),
            "capabilities": capabilities,
            "backend_capabilities": backend_capabilities,
            "phase_notes": [
                "Flux GGUF enables txt2img/img2img/inpaint/outpaint through a provider-owned Comfy graph.",
                "Flux GGUF uses GGUF UNet + DualCLIPLoaderGGUF + AE/VAE + CLIPTextEncodeFlux guidance.",
                "Flux GGUF forces KSampler CFG to 1.0; the UI Flux Guidance value controls guidance.",
                "Image-conditioned Flux GGUF modes use source VAEEncode; inpaint adds SetLatentNoiseMask + DifferentialDiffusion + a final masked composite guard; outpaint uses ImagePadForOutpaint output 0 plus its output 1 mask through SetLatentNoiseMask + DifferentialDiffusion.",
                "Phase M14.3 exposes the shared source stack to Flux GGUF: Image 1 is the active latent anchor; Image 2 and Image 3 are uploaded/saved as reference lanes for replay and future adapter/regional routing.",
                "Comfy GGUF node names are provider-local diagnostics; Image surface contracts stay family+loader+mode.",
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
