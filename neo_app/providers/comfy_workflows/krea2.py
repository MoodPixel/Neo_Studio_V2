from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.inpaint_payload import normalize_inpaint_target_aliases
from neo_app.image.krea2_contract import check_krea2_compatibility, resolve_krea2_variant
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile


@dataclass(frozen=True)
class Krea2Defaults:
    width: int = 1024
    height: int = 1024
    raw_steps: int = 52
    raw_cfg: float = 3.5
    turbo_steps: int = 8
    # Official Comfy template uses CFG 1 + ConditioningZeroOut even though the
    # standalone Krea reference describes Turbo guidance as CFG 0.
    turbo_comfy_cfg: float = 1.0
    standalone_turbo_cfg: float = 0.0
    sampler: str = "euler"
    scheduler: str = "simple"
    denoise: float = 1.0
    clip_type: str = "krea2"
    clip_device: str = "default"
    latent_node: str = "EmptyLatentImage"
    gguf_unet_loader: str = "UnetLoaderGGUF"


KREA2_DEFAULTS = Krea2Defaults()


def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _int_param(params: dict[str, Any], *names: str, default: int = 0) -> int:
    try:
        return int(_param(params, *names, default=default) or 0)
    except (TypeError, ValueError):
        return int(default)


def _image_name_value(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file") or value.get("url")
    return str(value or "").strip().split("/")[-1].split("\\")[-1]


def _source_image_name(params: dict[str, Any]) -> str:
    for key in ("comfy_source_image_name", "source_image_name", "source_image", "source_image_path", "source_image_url", "init_image", "image"):
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _mask_image_name(params: dict[str, Any]) -> str:
    for key in ("comfy_mask_image_name", "mask_image_name", "mask_image", "mask_image_path", "inpaint_mask", "mask"):
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _normalize_gguf_unet_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"UnetLoaderGGUF", "LoaderGGUF"} else KREA2_DEFAULTS.gguf_unet_loader


def _gguf_unet_inputs(loader_class: str, model_name: str) -> dict[str, Any]:
    return {"gguf_name": model_name} if loader_class == "LoaderGGUF" else {"unet_name": model_name}


def _outpaint_working_size(outpaint_payload: dict[str, Any], fallback_width: int, fallback_height: int) -> tuple[int, int, bool, dict[str, Any]]:
    resolution = outpaint_payload.get("source_resolution") if isinstance(outpaint_payload, dict) else {}
    if not isinstance(resolution, dict) or not resolution:
        return max(64, int(fallback_width or 1024)), max(64, int(fallback_height or 1024)), False, {}
    working = resolution.get("working_size") if isinstance(resolution.get("working_size"), dict) else {}
    mode = str(resolution.get("mode") or "auto").strip().lower()
    width = max(64, int(working.get("width") or fallback_width or 1024))
    height = max(64, int(working.get("height") or fallback_height or 1024))
    return width, height, mode != "keep_original", resolution


def _insert_outpaint_source_scale_node(
    workflow: dict[str, Any],
    next_id: int,
    source_ref: list[Any],
    outpaint_payload: dict[str, Any],
    *,
    fallback_width: int,
    fallback_height: int,
) -> tuple[int, list[Any], int, int, dict[str, Any] | None]:
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


def compile_krea2_workflow(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile Krea 2 RAW/Turbo for native components and GGUF transformers.

    M16 keeps Krea 2 as its own architecture. Native routes use UNETLoader +
    CLIPLoader(type=krea2) + Qwen Image VAE. GGUF routes quantize only the main
    transformer in the stable M16 contract: the Qwen3-VL-4B text encoder remains
    native/safetensors so Comfy can produce the required 12-layer feature stack.

    img2img/inpaint/outpaint are explicit Neo provider-owned latent adaptations,
    not claims of a separate official Krea 2 edit/inpaint checkpoint.
    """

    raw_params = job.params or {}
    mode = str(route.mode or job.mode or "txt2img")
    params = normalize_inpaint_target_aliases(raw_params) if mode == "inpaint" else raw_params
    loader = str(route.loader or job.loader or "diffusion_model")
    defaults = KREA2_DEFAULTS
    is_gguf = loader == "gguf"
    image_mode = mode in {"img2img", "edit", "inpaint", "outpaint"}

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    if is_gguf:
        model_name = str(job.model or _param(params, "gguf_model", "gguf_unet", "model", "model_name", default=""))
        if not model_name:
            validation.errors.append("Krea 2 GGUF requires an explicitly selected GGUF diffusion transformer.")
            validation.ok = False
    else:
        model_name = str(require_explicit_asset_selection(
            validation,
            "Krea 2 diffusion model",
            job.model,
            params.get("diffusion_model"), params.get("model"), params.get("unet"), params.get("model_name"),
        ))

    text_encoder = str(require_explicit_asset_selection(
        validation,
        "Krea 2 Qwen3-VL-4B text encoder",
        params.get("qwen3vl_text_encoder"), params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name"),
    ))
    vae = str(require_explicit_asset_selection(
        validation,
        "Krea 2 Qwen Image VAE",
        params.get("vae"), params.get("vae_or_ae"), params.get("ae"),
    ))

    variant = resolve_krea2_variant(route.family or job.family or params.get("krea2_variant") or "krea2", model_name)
    turbo = variant == "turbo"
    compatibility = check_krea2_compatibility(route.family or job.family or variant, model_name, text_encoder, vae, loader=loader)
    if compatibility.compatible is False:
        if compatibility.message not in validation.errors:
            validation.errors.append(compatibility.message)
        validation.ok = False
    elif compatibility.compatible is None and compatibility.message and compatibility.message not in validation.warnings:
        validation.warnings.append(compatibility.message)

    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    steps_default = defaults.turbo_steps if turbo else defaults.raw_steps
    cfg_default = defaults.turbo_comfy_cfg if turbo else defaults.raw_cfg
    steps = int(_param(params, "steps", default=steps_default))
    cfg = float(_param(params, "cfg", default=cfg_default))
    # Family semantics are authoritative. Old presets must not accidentally turn
    # Turbo into a 52-step/3.5-CFG RAW job or RAW into an 8-step Turbo job.
    if turbo:
        steps = defaults.turbo_steps
        cfg = defaults.turbo_comfy_cfg
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    denoise_default = defaults.denoise if mode == "txt2img" else 0.75
    denoise = float(_param(params, "denoise", "strength", default=denoise_default))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    source_name = _source_image_name(params) if image_mode else ""
    mask_name = _mask_image_name(params) if mode == "inpaint" else ""
    if image_mode and not source_name:
        validation.errors.append(f"Krea 2 {variant.upper()} {mode} requires Image 1 / source image.")
        validation.ok = False
    if mode == "inpaint" and not mask_name:
        validation.errors.append("Krea 2 inpaint requires a mask image.")
        validation.ok = False
    if mode == "outpaint":
        outpaint_check = normalize_outpaint_payload(params, default_width=width, default_height=height)
        if outpaint_padding_total(outpaint_check) <= 0:
            validation.errors.append("Krea 2 outpaint requires padding on at least one side.")
            validation.ok = False

    if is_gguf:
        model_loader = _normalize_gguf_unet_loader(_param(params, "gguf_unet_loader", "unet_loader", default=defaults.gguf_unet_loader))
        model_node = {"class_type": model_loader, "inputs": _gguf_unet_inputs(model_loader, model_name)}
        compiler_id = "comfy.krea2_gguf"
    else:
        model_node = {"class_type": "UNETLoader", "inputs": {"unet_name": model_name, "weight_dtype": weight_dtype}}
        compiler_id = "comfy.krea2"

    workflow: dict[str, Any] = {
        "1": model_node,
        # Deliberately native for both model formats. Krea2 requires Comfy's
        # specialized 12-layer Qwen3-VL aggregation from CLIPLoader(type=krea2).
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": defaults.clip_type, "device": clip_device}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
    }
    if turbo:
        workflow["5"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}}
    else:
        workflow["5"] = {"class_type": "CLIPTextEncode", "inputs": {"text": effective_negative, "clip": ["2", 0]}}

    actual_params: dict[str, Any] = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.{'krea2_turbo' if turbo else 'krea2'}_{'gguf' if is_gguf else 'native'}",
        "krea2_variant": variant,
        "qwen3vl_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "text_encoder_2": "",
        "vae": vae,
        "diffusion_model": "" if is_gguf else model_name,
        "gguf_model": model_name if is_gguf else "",
        "clip_type": defaults.clip_type,
        "steps": steps,
        "cfg": cfg,
        "denoise": denoise,
        "prompt_conditioning_mode": conditioning_mode,
        "clamp": conditioning_mode,
        "krea2_profile": {
            "family": "krea2_turbo" if turbo else "krea2",
            "variant": variant,
            "loader": loader,
            "compiler": compiler_id,
            "architecture": "krea2_native_dit_qwen3vl4b_qwen_image_vae",
            "compatibility": compatibility.as_dict(),
            "clip_loader": "CLIPLoader(type=krea2)",
            "text_encoder_policy": "Qwen3-VL-4B native/safetensors; 12-layer feature aggregation",
            "vae_policy": "Qwen Image VAE",
            "raw_defaults": {"steps": defaults.raw_steps, "cfg": defaults.raw_cfg},
            "turbo_defaults": {"steps": defaults.turbo_steps, "comfy_cfg": defaults.turbo_comfy_cfg, "standalone_reference_cfg": defaults.standalone_turbo_cfg},
            "gguf_policy": "GGUF transformer only in M16; native Qwen3-VL-4B encoder remains mandatory",
            "image_mode_policy": "Neo provider-owned latent adaptation; not an official specialized Krea2 edit/inpaint checkpoint",
        },
        "_neo_effective_krea2_native_route": not is_gguf,
        "_neo_effective_krea2_gguf_route": is_gguf,
        "_neo_krea2_image_mode_adapter": image_mode,
    }

    next_id = 6
    source_ref: list[Any] | None = None
    original_source_ref: list[Any] | None = None
    mask_ref: list[Any] | None = None
    latent_ref: list[Any] | None = None
    model_ref: list[Any] = ["1", 0]
    route_notes: list[str] = []

    if not image_mode:
        workflow[str(next_id)] = {"class_type": defaults.latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        latent_ref = [str(next_id), 0]
        next_id += 1

    if image_mode and source_name:
        workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
        source_ref = [str(next_id), 0]
        original_source_ref = list(source_ref)
        next_id += 1
        actual_params["source_image_name"] = source_name
        actual_params["_neo_krea2_image1_latent_anchor"] = True

    if mode == "outpaint" and source_ref is not None:
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        padding = outpaint_payload["padding"]
        mask_payload = outpaint_payload["mask"]
        left = int(padding.get("left", 0) or 0)
        top = int(padding.get("top", 0) or 0)
        right = int(padding.get("right", 0) or 0)
        bottom = int(padding.get("bottom", 0) or 0)
        feather = int(mask_payload.get("feather", 16) or 16)
        next_id, source_ref, working_width, working_height, scale_meta = _insert_outpaint_source_scale_node(
            workflow, next_id, source_ref, outpaint_payload, fallback_width=width, fallback_height=height
        )
        workflow[str(next_id)] = {
            "class_type": "ImagePadForOutpaint",
            "inputs": {"image": list(source_ref), "left": left, "top": top, "right": right, "bottom": bottom, "feathering": feather},
        }
        source_ref = [str(next_id), 0]
        mask_ref = [str(next_id), 1]
        next_id += 1
        width = max(64, int(working_width) + left + right)
        height = max(64, int(working_height) + top + bottom)
        actual_params.update({
            "outpaint_payload": outpaint_payload,
            "_neo_outpaint_contract": outpaint_payload,
            "krea2_outpaint_source_scale_node": scale_meta or {},
            "krea2_outpaint_effective_size": {"width": width, "height": height},
            "_neo_krea2_outpaint_uses_image_pad_mask": True,
        })
        route_notes.append("Krea 2 outpaint uses the generated ImagePadForOutpaint mask as the latent noise mask.")

    if mode in {"img2img", "edit"} and source_ref is not None:
        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["3", 0]}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        route_notes.append("Krea 2 img2img uses Image 1 as a Qwen Image VAE latent anchor.")
    elif mode == "inpaint" and source_ref is not None and mask_name:
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
        workflow[str(next_id)] = {"class_type": "DifferentialDiffusion", "inputs": {"model": list(model_ref)}}
        model_ref = [str(next_id), 0]
        next_id += 1
        actual_params.update({
            "mask_image_name": mask_name,
            "mask_grow": grow,
            "mask_blur": blur,
            "inpaint_target": inpaint_target,
            "_neo_krea2_inpaint_uses_latent_noise_mask": True,
            "_neo_krea2_inpaint_uses_differential_diffusion": True,
        })
        route_notes.append("Krea 2 inpaint is a Neo latent-mask adapter using VAEEncode + SetLatentNoiseMask + DifferentialDiffusion.")
    elif mode == "outpaint" and source_ref is not None:
        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["3", 0]}}
        encoded_ref = [str(next_id), 0]
        next_id += 1
        workflow[str(next_id)] = {"class_type": "SetLatentNoiseMask", "inputs": {"samples": list(encoded_ref), "mask": list(mask_ref or ["0", 0])}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        workflow[str(next_id)] = {"class_type": "DifferentialDiffusion", "inputs": {"model": list(model_ref)}}
        model_ref = [str(next_id), 0]
        next_id += 1
    elif latent_ref is None:
        workflow[str(next_id)] = {"class_type": defaults.latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        latent_ref = [str(next_id), 0]
        next_id += 1

    sampler_id = str(next_id)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
            "denoise": denoise,
            "model": model_ref,
            "positive": ["4", 0],
            "negative": ["5", 0],
            "latent_image": latent_ref,
        },
    }
    next_id += 1
    decode_id = str(next_id)
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    output_ref: list[Any] = [decode_id, 0]
    next_id += 1

    if mode == "inpaint" and original_source_ref is not None and mask_ref is not None:
        composite_id = str(next_id)
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {"destination": list(original_source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": list(mask_ref)},
        }
        output_ref = [composite_id, 0]
        next_id += 1

    workflow[str(next_id)] = {"class_type": "PreviewImage", "inputs": {"images": output_ref}}
    actual_params["_neo_sampler_node_id"] = sampler_id
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate" if mode == "txt2img" else mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoaderModelOnly",
        source=compiler_id,
        strategy="lora_loader_model_only_consumer_rewire",
        requires_clip=False,
        patch_clip_consumers=False,
        validated=False,
        notes=["Krea 2 official style LoRAs are model-only; M16 records the patch point but keeps route-specific LoRA validation experimental."],
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
            "compile_route": {**route.as_dict(), "compiler_id": compiler_id},
            "capabilities": capabilities,
            "backend_capabilities": backend_capabilities or {},
            "phase_notes": [
                "Phase M16 keeps Krea 2 RAW/Turbo separate from FLUX.1 Krea and FLUX.2 Klein.",
                "Native Krea 2 uses UNETLoader + CLIPLoader(type=krea2) with Qwen3-VL-4B + Qwen Image VAE.",
                "Turbo uses the official Comfy convention: 8 steps, CFG 1.0, and ConditioningZeroOut for negative conditioning.",
                "RAW uses 52 steps / CFG 3.5 defaults and encodes the negative prompt normally.",
                "M16 GGUF support quantizes the diffusion transformer only; the Qwen3-VL-4B encoder remains native to preserve Krea2's 12-layer conditioning stack.",
                "img2img/inpaint/outpaint are Neo provider-owned latent adaptations and are marked experimental in the route matrix.",
                *route_notes,
            ],
            "prompt_conditioning": conditioning,
        },
    )
