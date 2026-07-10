from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.image.inpaint_payload import normalize_inpaint_target_aliases
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile


@dataclass(frozen=True)
class FluxNativeDefaults:
    """Provider compiler defaults for the first Flux native Comfy route.

    Phase 12.9 intentionally enables only txt2img for split diffusion-model
    Flux. Image-conditioned routes require their own encode/variant semantics
    and remain planned/gated in the compile router.
    """

    width: int = 1024
    height: int = 1024
    steps: int = 30
    cfg: float = 1.0
    flux_guidance: float = 3.5
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "normal"
    latent_node: str = "EmptySD3LatentImage"


FLUX_NATIVE_DEFAULTS = FluxNativeDefaults()


def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default



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


def _add_optional_mask_softening(workflow: dict[str, Any], next_id: int, mask_ref: list[Any], params: dict[str, Any]) -> tuple[int, list[Any], dict[str, Any]]:
    grow = max(0, _int_param(params, "mask_grow", _int_param(params, "grow_mask_by", 3)))
    blur = max(0, _int_param(params, "mask_blur", _int_param(params, "blur_mask_by", 0)))
    meta = {"mask_grow": grow, "mask_blur": blur}
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
    meta["inpaint_target"] = inpaint_target
    return next_id, mask_ref, meta


def _is_klein_variant(value: Any) -> bool:
    normalized = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    return normalized in {"klein", "flux2_klein", "flux_2_klein", "klein_4b", "klein_9b", "klein_4b_distilled", "klein_9b_distilled"}

def compile_flux_klein_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
) -> CompiledJob:
    """Compile FLUX.2 [klein] safetensors/component workflows.

    P4 promotes the component route beyond txt2img. The compiler stays
    Klein-native: one Qwen3 encoder through CLIPLoader(type=flux2), Flux2 VAE,
    FluxGuidance, and Flux2 latent/image branches. It must not borrow Flux 1
    Fill, Flux 1 dual-encoder, SD checkpoint, or Qwen image-edit compilers.
    """

    raw_params = job.params or {}
    mode = str(route.mode or job.mode or "txt2img")
    if mode == "generate":
        mode = "txt2img"
    params = normalize_inpaint_target_aliases(raw_params) if mode == "inpaint" else raw_params
    defaults = FLUX_NATIVE_DEFAULTS
    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""

    diffusion_model = require_explicit_asset_selection(
        validation,
        "Flux 2 Klein diffusion model",
        job.model,
        params.get("diffusion_model"), params.get("model"), params.get("unet"), params.get("model_name"),
    )
    text_encoder = require_explicit_asset_selection(
        validation,
        "Flux 2 Klein Qwen3 text encoder",
        params.get("qwen3_text_encoder"), params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name"),
    )
    vae = require_explicit_asset_selection(
        validation,
        "Flux 2 Klein VAE / AE",
        params.get("vae"), params.get("vae_or_ae"), params.get("ae"),
    )
    flux_guidance = float(_param(params, "flux_guidance", "guidance", default=1.0))
    sampler = str(_param(params, "sampler", default="euler"))
    scheduler = str(_param(params, "scheduler", default="simple"))
    steps = int(_param(params, "steps", default=4))
    width = int(_param(params, "width", default=1024))
    height = int(_param(params, "height", default=1024))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    denoise_default = 1.0 if mode == "txt2img" else 0.75
    denoise = float(_param(params, "denoise", default=denoise_default))
    # FLUX.2 [klein] examples use low sampler CFG; guidance is handled by FluxGuidance.
    cfg = float(_param(params, "cfg", default=1.0))
    if cfg <= 0:
        cfg = 1.0

    source_name = _source_image_name(params) if mode in {"img2img", "edit", "inpaint", "outpaint"} else ""
    if mode in {"img2img", "edit", "inpaint", "outpaint"} and not source_name:
        validation.errors.append(f"Flux 2 Klein Safetensors / Components {mode} requires Image 1 / source image.")
        validation.ok = False

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.flux2_klein_native",
        "prompt_conditioning_mode": conditioning_mode,
        "clamp": conditioning_mode,
        "flux_variant": _param(params, "flux_variant", default="klein_4b"),
        "flux_klein_profile": {
            "family": job.family or "flux2_klein",
            "loader": "diffusion_model",
            "variant": "flux2_klein",
            "compiler": "comfy.flux_klein",
            "enabled_modes": ["txt2img", "img2img", "edit", "inpaint", "outpaint"],
            "gated_modes": [],
            "required_comfy_nodes": [
                "UNETLoader",
                "CLIPLoader",
                "EmptyFlux2LatentImage",
                "LoadImage",
                "VAEEncode",
                "SetLatentNoiseMask",
                "FluxGuidance",
                "ConditioningZeroOut",
                "VAELoader",
                "KSampler",
                "VAEDecode",
            ],
            "text_encoder_policy": "single_qwen3_cliploader_flux2",
            "negative_policy": "ConditioningZeroOut from positive conditioning",
            "source_policy": "txt2img uses EmptyFlux2LatentImage; img2img/edit encode Image 1 as the Flux2 latent anchor; inpaint/outpaint add source mask/canvas via SetLatentNoiseMask.",
            "does_not_use": ["Flux 1 Fill", "Flux 1 DualCLIPLoader", "SD checkpoint", "Qwen image edit compiler"],
        },
        "diffusion_model": diffusion_model,
        "text_encoder_1": text_encoder,
        "qwen3_text_encoder": text_encoder,
        "text_encoder_2": "",
        "vae": vae,
        "flux_guidance": flux_guidance,
        "denoise": denoise,
        "cfg": cfg,
        "steps": steps,
        "_neo_flux2_klein_native_mode": mode,
        "_neo_effective_flux2_klein_native_route": True,
    }

    workflow: dict[str, Any] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": diffusion_model,
                "weight_dtype": str(_param(params, "weight_dtype", "model_precision", default="default")),
            },
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {
                "clip_name": text_encoder,
                "type": "flux2",
                "device": str(_param(params, "clip_device", "text_encoder_device", default="default")),
            },
        },
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
        "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["5", 0], "guidance": flux_guidance}},
        "7": {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["5", 0]}},
    }

    next_id = 8
    source_ref: list[Any] | None = None
    mask_ref: list[Any] | None = None
    route_meta: dict[str, Any] = {"_neo_flux2_klein_native_source_branch": mode}

    if mode == "txt2img":
        workflow["4"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        latent_ref = ["4", 0]
    elif source_name:
        workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
        source_ref = [str(next_id), 0]
        next_id += 1
        route_meta.update({"source_image_name": source_name, "_neo_flux2_klein_image1_latent_anchor": True})

        if mode == "outpaint":
            outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
            if outpaint_padding_total(outpaint_payload) <= 0:
                validation.errors.append("Flux 2 Klein Safetensors / Components outpaint requires padding on at least one side.")
                validation.ok = False
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
            mask_ref = [str(next_id), 1]
            next_id += 1
            route_meta.update({
                "outpaint_payload": outpaint_payload,
                "_neo_outpaint_contract": outpaint_payload,
                "flux2_klein_native_outpaint_source_scale_node": source_scale_meta or {},
                "flux2_klein_native_outpaint_padding": {"left": left, "top": top, "right": right, "bottom": bottom, "feather": feather, "blur": int(mask.get("blur", 8) or 8)},
                "flux2_klein_native_outpaint_effective_size": {"width": max(64, working_width + left + right), "height": max(64, working_height + top + bottom)},
                "_neo_flux2_klein_native_outpaint_uses_image_pad_mask": True,
                "_neo_flux2_klein_native_outpaint_uses_latent_noise_mask": True,
                "_neo_flux2_klein_native_outpaint_uses_differential_diffusion": True,
            })

        if mode == "inpaint":
            mask_name = _mask_image_name(params)
            if not mask_name:
                validation.errors.append("Flux 2 Klein Safetensors / Components inpaint requires a mask image.")
                validation.ok = False
                mask_name = ""
            workflow[str(next_id)] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
            mask_ref = [str(next_id), 0]
            next_id += 1
            next_id, mask_ref, mask_meta = _add_optional_mask_softening(workflow, next_id, mask_ref, params)
            route_meta.update({
                "mask_image_name": mask_name,
                **mask_meta,
                "_neo_flux2_klein_native_inpaint_uses_latent_noise_mask": True,
                "_neo_flux2_klein_native_inpaint_uses_differential_diffusion": True,
                "_neo_flux2_klein_native_inpaint_final_composite": True,
                "_neo_flux2_klein_native_inpaint_composite_feather": max(0, _int_param(params, "flux_inpaint_composite_feather", _int_param(params, "composite_feather", 10))),
            })

        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["3", 0]}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        if mode in {"inpaint", "outpaint"} and mask_ref is not None:
            workflow[str(next_id)] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(latent_ref), "mask": list(mask_ref)}}
            latent_ref = [str(next_id), 0]
            next_id += 1
    else:
        workflow["4"] = {"class_type": "EmptyFlux2LatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        latent_ref = ["4", 0]

    sampler_model_ref: list[Any] = ["1", 0]
    if mode in {"inpaint", "outpaint"}:
        workflow[str(next_id)] = {"class_type": "DifferentialDiffusion", "inputs": {"model": sampler_model_ref}}
        sampler_model_ref = [str(next_id), 0]
        next_id += 1

    sampler_id = str(next_id)
    decode_id = str(next_id + 1)
    preview_id = str(next_id + 2)
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
    output_ref: list[Any] = [decode_id, 0]
    if mode == "inpaint" and source_ref and mask_ref:
        composite_mask_ref = list(mask_ref)
        composite_feather = int(route_meta.get("_neo_flux2_klein_native_inpaint_composite_feather") or 0)
        if composite_feather > 0:
            feather_id = preview_id
            preview_id = str(int(preview_id) + 1)
            workflow[feather_id] = {
                "class_type": "GrowMaskWithBlur",
                "inputs": {"mask": composite_mask_ref, "expand": 0, "incremental_expandrate": 0, "tapered_corners": True, "flip_input": False, "blur_radius": composite_feather, "lerp_alpha": 1, "decay_factor": 1, "fill_holes": False},
            }
            composite_mask_ref = [feather_id, 0]
        composite_id = preview_id
        preview_id = str(int(preview_id) + 1)
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {"destination": list(source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": composite_mask_ref},
        }
        output_ref = [composite_id, 0]
    workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": output_ref}}

    if mode in {"img2img", "edit"}:
        route_meta["_neo_flux2_klein_native_edit_alias"] = "img2img_latent_anchor"
        route_meta["_neo_flux2_klein_native_edit_uses_image1_latent_anchor"] = True
    actual_params.update(route_meta)
    actual_params["_neo_sampler_node_id"] = sampler_id
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.flux_klein",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["Flux 2 Klein native compiler owns model/clip refs; active LoRA routes remain matrix-gated."],
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
            "compile_route": {**route.as_dict(), "compiler_id": "comfy.flux_klein", "workflow_type": route.workflow_type or f"image.{mode}.flux2_klein_native"},
            "capabilities": capabilities,
            "phase_notes": [
                "P4 promotes FLUX.2 [klein] Safetensors / Components img2img/edit/inpaint/outpaint through the Klein-native compiler.",
                "FLUX.2 Klein uses a single Qwen3 text encoder via CLIPLoader(type=flux2), Flux2 VAE, FluxGuidance, and Flux2 latent branches.",
                "Image modes encode Image 1 with VAEEncode; inpaint/outpaint apply SetLatentNoiseMask and DifferentialDiffusion instead of borrowing Flux 1 Fill.",
                "Do not fallback to Flux 1, Flux GGUF, SD checkpoint, or Qwen compilers for Flux Klein component routes.",
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )


def compile_flux_native_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
) -> CompiledJob:
    """Compile Flux 1 native txt2img/img2img split-component workflows.

    Pass O2 keeps base Flux 1 inpaint/outpaint gated to the dedicated
    Flux 1 Fill visible family, but unlocks the plain image-to-image latent
    anchor path for the normal Flux 1 Safetensors / Components route.
    """

    params = job.params or {}
    defaults = FLUX_NATIVE_DEFAULTS
    mode = str(route.mode or job.mode or "txt2img")
    if mode == "generate":
        mode = "txt2img"
    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    diffusion_model = require_explicit_asset_selection(
        validation, "Flux 1 diffusion model", job.model, params.get("diffusion_model"), params.get("model"), params.get("unet"), params.get("model_name")
    )
    text_encoder_1 = require_explicit_asset_selection(
        validation, "Flux 1 primary text encoder", params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name1")
    )
    text_encoder_2 = require_explicit_asset_selection(
        validation, "Flux 1 secondary text encoder", params.get("text_encoder_2"), params.get("text_encoder_secondary"), params.get("clip_name2")
    )
    vae = require_explicit_asset_selection(
        validation, "Flux 1 VAE / AE", params.get("vae"), params.get("vae_or_ae"), params.get("ae")
    )
    flux_guidance = float(_param(params, "flux_guidance", "guidance", default=defaults.flux_guidance))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    steps = int(_param(params, "steps", default=defaults.steps))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    denoise = float(_param(params, "denoise", default=defaults.denoise))
    cfg = float(_param(params, "cfg", default=defaults.cfg))
    if mode == "img2img":
        source_name = _source_image_name(params)
        if not source_name:
            validation.errors.append("Flux 1 Safetensors / Components img2img requires a source image.")
            validation.ok = False
    else:
        source_name = ""

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.flux_native",
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
        "flux_native_profile": {
            "family": "flux",
            "loader": "diffusion_model",
            "default_width": defaults.width,
            "default_height": defaults.height,
            "default_steps": defaults.steps,
            "default_flux_guidance": defaults.flux_guidance,
            "compiler": "comfy.flux_native",
            "enabled_modes": ["txt2img", "img2img"],
            "gated_modes": ["inpaint", "outpaint"],
            "source_policy": "txt2img uses EmptySD3LatentImage; img2img uses Image 1 as VAEEncode latent anchor; inpaint/outpaint are owned by Flux 1 Fill.",
        },
        "diffusion_model": diffusion_model,
        "text_encoder_1": text_encoder_1,
        "text_encoder_2": text_encoder_2,
        "vae": vae,
        "flux_guidance": flux_guidance,
        "denoise": denoise,
        "cfg": cfg,
        "_neo_flux1_native_mode": mode,
    }

    workflow: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": diffusion_model, "weight_dtype": str(_param(params, "weight_dtype", "model_precision", default="default"))}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": text_encoder_1, "clip_name2": text_encoder_2, "type": str(_param(params, "clip_type", "text_encoder_type", default="flux"))}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_negative, "clip": ["2", 0]}},
        "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": flux_guidance}},
    }

    next_id = 7
    if mode == "img2img" and source_name:
        workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
        source_ref = [str(next_id), 0]
        next_id += 1
        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": source_ref, "vae": ["3", 0]}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        actual_params.update({"source_image_name": source_name, "_neo_flux1_native_img2img_latent_anchor": True})
    else:
        workflow[str(next_id)] = {"class_type": defaults.latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        latent_ref = [str(next_id), 0]
        next_id += 1

    sampler_id = str(next_id)
    decode_id = str(next_id + 1)
    preview_id = str(next_id + 2)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
            "denoise": denoise,
            "model": ["1", 0],
            "positive": ["6", 0],
            "negative": ["5", 0],
            "latent_image": latent_ref,
        },
    }
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": [decode_id, 0]}}
    actual_params["_neo_sampler_node_id"] = sampler_id
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.flux_native",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["Flux native compiler owns model/clip refs; LoRA route execution remains support-matrix gated."],
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
            "phase_notes": [
                "Pass O2 enables Flux 1 native Safetensors / Components img2img as an Image-1 VAEEncode latent-anchor workflow.",
                "Flux 1 native inpaint/outpaint remain gated to the dedicated Flux 1 Fill family because FLUX.1 Fill-dev is the correct model/workflow for fill tasks.",
                "Comfy node names are provider-local diagnostics; Image surface contracts stay family+loader+mode.",
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )


def compile_flux_fill_workflow(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
) -> CompiledJob:
    """Compile Flux 1 internal Flux Fill inpaint/outpaint workflows.

    P1 makes this a route variant behind the normal Flux 1 family instead of a
    separate visible Model Family dropdown entry. The legacy `flux1_fill` alias
    remains accepted for saved jobs.
    """

    params = normalize_inpaint_target_aliases(job.params or {}) if str(job.mode or route.mode) == "inpaint" else (job.params or {})
    defaults = FLUX_NATIVE_DEFAULTS
    mode = str(route.mode or job.mode or "inpaint")
    if mode not in {"inpaint", "outpaint"}:
        validation.errors.append(f"Flux 1 Fill supports inpaint/outpaint only, not {mode}.")
        validation.ok = False
        mode = "inpaint"

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    diffusion_model = require_explicit_asset_selection(
        validation, "Flux 1 Fill diffusion model", job.model, params.get("diffusion_model"), params.get("model"), params.get("unet"), params.get("model_name")
    )
    text_encoder_1 = require_explicit_asset_selection(
        validation, "Flux 1 Fill primary text encoder", params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name1")
    )
    text_encoder_2 = require_explicit_asset_selection(
        validation, "Flux 1 Fill secondary text encoder", params.get("text_encoder_2"), params.get("text_encoder_secondary"), params.get("clip_name2")
    )
    vae = require_explicit_asset_selection(
        validation, "Flux 1 Fill VAE / AE", params.get("vae"), params.get("vae_or_ae"), params.get("ae")
    )
    flux_guidance = float(_param(params, "flux_guidance", "guidance", default=30.0))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    steps = int(_param(params, "steps", default=30))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    denoise = float(_param(params, "denoise", default=defaults.denoise))
    cfg = float(_param(params, "cfg", default=1.0))

    source_name = _source_image_name(params)
    if not source_name:
        validation.errors.append(f"Flux 1 Fill {mode} requires a source image.")
        validation.ok = False
        source_name = ""

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.flux_fill_internal",
        "prompt_conditioning_mode": conditioning_mode,
        "clamp": conditioning_mode,
        "flux_fill_profile": {
            "family": route.family or "flux",
            "visible_family": "flux",
            "legacy_alias": "flux1_fill",
            "loader": "diffusion_model",
            "compiler": "comfy.flux_fill",
            "internal_variant": "flux_fill",
            "enabled_modes": ["inpaint", "outpaint"],
            "model_policy": "Use FLUX.1 Fill-dev/compatible fill diffusion model for Flux 1 fill routes.",
            "conditioning_node": "InpaintModelConditioning",
            "sampling_patch": "DifferentialDiffusion",
        },
        "diffusion_model": diffusion_model,
        "text_encoder_1": text_encoder_1,
        "text_encoder_2": text_encoder_2,
        "vae": vae,
        "flux_guidance": flux_guidance,
        "denoise": denoise,
        "cfg": cfg,
        "_neo_flux1_fill_mode": mode,
    }

    workflow: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": diffusion_model, "weight_dtype": str(_param(params, "weight_dtype", "model_precision", default="default"))}},
        "2": {"class_type": "DualCLIPLoader", "inputs": {"clip_name1": text_encoder_1, "clip_name2": text_encoder_2, "type": str(_param(params, "clip_type", "text_encoder_type", default="flux"))}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_negative, "clip": ["2", 0]}},
        "6": {"class_type": "FluxGuidance", "inputs": {"conditioning": ["4", 0], "guidance": flux_guidance}},
    }
    next_id = 7
    if source_name:
        workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
        source_ref = [str(next_id), 0]
        next_id += 1
    else:
        # Keep a syntactically valid graph for diagnostics when validation fails.
        workflow[str(next_id)] = {"class_type": "EmptyImage", "inputs": {"width": width, "height": height, "batch_size": 1, "color": 0}}
        source_ref = [str(next_id), 0]
        next_id += 1

    mask_ref: list[Any] | None = None
    if mode == "outpaint":
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        if outpaint_padding_total(outpaint_payload) <= 0:
            validation.errors.append("Flux 1 Fill outpaint requires padding on at least one side.")
            validation.ok = False
        padding = outpaint_payload["padding"]
        mask = outpaint_payload["mask"]
        left = int(padding.get("left", 0) or 0)
        top = int(padding.get("top", 0) or 0)
        right = int(padding.get("right", 0) or 0)
        bottom = int(padding.get("bottom", 0) or 0)
        feather = int(mask.get("feather", 16) or 16)
        next_id, source_ref, working_width, working_height, source_scale_meta = _insert_outpaint_source_scale_node(workflow, next_id, source_ref, outpaint_payload, fallback_width=width, fallback_height=height)
        workflow[str(next_id)] = {"class_type": "ImagePadForOutpaint", "inputs": {"image": list(source_ref), "left": left, "top": top, "right": right, "bottom": bottom, "feathering": feather}}
        source_ref = [str(next_id), 0]
        mask_ref = [str(next_id), 1]
        next_id += 1
        actual_params.update({
            "outpaint_payload": outpaint_payload,
            "flux_fill_outpaint_padding": {"left": left, "top": top, "right": right, "bottom": bottom, "feather": feather, "blur": int(mask.get("blur", 8) or 8)},
            "flux_fill_outpaint_effective_size": {"width": max(64, working_width + left + right), "height": max(64, working_height + top + bottom)},
            "flux_fill_outpaint_source_scale_node": source_scale_meta or {},
            "_neo_flux_fill_outpaint_uses_image_pad_mask": True,
        })
    else:
        mask_name = _mask_image_name(params)
        if not mask_name:
            validation.errors.append("Flux 1 Fill inpaint requires a mask image.")
            validation.ok = False
            mask_name = ""
        workflow[str(next_id)] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        mask_ref = [str(next_id), 0]
        next_id += 1
        next_id, mask_ref, mask_meta = _add_optional_mask_softening(workflow, next_id, mask_ref, params)
        actual_params.update({"mask_image_name": mask_name, **mask_meta, "_neo_flux_fill_inpaint_uses_inpaint_model_conditioning": True})

    condition_id = str(next_id)
    diff_id = str(next_id + 1)
    sampler_id = str(next_id + 2)
    decode_id = str(next_id + 3)
    preview_id = str(next_id + 4)
    workflow[condition_id] = {"class_type": "InpaintModelConditioning", "inputs": {"positive": ["6", 0], "negative": ["5", 0], "vae": ["3", 0], "pixels": list(source_ref), "mask": list(mask_ref or ["0", 0])}}
    workflow[diff_id] = {"class_type": "DifferentialDiffusion", "inputs": {"model": ["1", 0]}}
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
            "denoise": denoise,
            "model": [diff_id, 0],
            "positive": [condition_id, 0],
            "negative": [condition_id, 1],
            "latent_image": [condition_id, 2],
        },
    }
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    output_ref = [decode_id, 0]
    if mode == "inpaint" and source_name and mask_ref:
        composite_feather = max(0, _int_param(params, "flux_inpaint_composite_feather", _int_param(params, "composite_feather", 10)))
        composite_mask_ref = list(mask_ref)
        if composite_feather > 0:
            feather_id = preview_id
            preview_id = str(int(preview_id) + 1)
            workflow[feather_id] = {"class_type": "GrowMaskWithBlur", "inputs": {"mask": composite_mask_ref, "expand": 0, "incremental_expandrate": 0, "tapered_corners": True, "flip_input": False, "blur_radius": composite_feather, "lerp_alpha": 1, "decay_factor": 1, "fill_holes": False}}
            composite_mask_ref = [feather_id, 0]
        composite_id = preview_id
        preview_id = str(int(preview_id) + 1)
        workflow[composite_id] = {"class_type": "ImageCompositeMasked", "inputs": {"destination": list(source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": composite_mask_ref}}
        output_ref = [composite_id, 0]
        actual_params["_neo_flux_fill_inpaint_final_composite"] = True
    workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": output_ref}}
    actual_params["_neo_sampler_node_id"] = sampler_id
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.flux_fill",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["Flux Fill compiler profile is emitted for diagnostics; LoRA route state currently gates unsupported fill patching unless explicitly promoted."],
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
            "phase_notes": [
                "P1 routes normal Flux 1 Safetensors / Components inpaint/outpaint through the internal Flux Fill workflow.",
                "Flux Fill uses a fill-compatible diffusion model, DualCLIPLoader, VAE, FluxGuidance, InpaintModelConditioning, DifferentialDiffusion, KSampler, and VAEDecode.",
                "Outpaint uses ImagePadForOutpaint output mask; inpaint uses Neo mask loading through LoadImageMask(channel=red).",
                "Flux Fill is not exposed as a normal Model Family dropdown entry.",
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
