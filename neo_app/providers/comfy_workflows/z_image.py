from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.image.inpaint_payload import normalize_inpaint_target_aliases
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile


@dataclass(frozen=True)
class ZImageDefaults:
    """Provider compiler defaults for the first safe Z-Image txt2img routes.

    V25.9.20 Pass H locks base ZImage as text-to-image only. V25.9.20
    Pass I promotes ZImage Turbo to a separate visible family that reuses this
    compiler while forcing turbo conditioning/defaults from the route family.
    The Comfy template uses a Qwen3 4B text encoder through CLIPLoader
    type=lumina2, a Z-Image diffusion model, Flux AE/VAE, EmptySD3LatentImage,
    ModelSamplingAuraFlow, and KSampler. P5 adds ZImage base Safetensors /
    Components image-conditioned routes by reusing this native stack and
    switching the latent branch to source VAEEncode, SetLatentNoiseMask,
    DifferentialDiffusion, or ImagePadForOutpaint as required by mode.
    """

    width: int = 1024
    height: int = 1024
    turbo_steps: int = 9
    base_steps: int = 35
    base_min_steps: int = 28
    turbo_cfg: float = 1.0
    base_cfg: float = 3.5
    base_min_cfg: float = 2.5
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    latent_node: str = "EmptySD3LatentImage"
    sampling_node: str = "ModelSamplingAuraFlow"
    aura_shift: float = 3.0
    clip_type: str = "lumina2"
    clip_device: str = "default"
    native_unet_loader: str = "UNETLoader"
    native_clip_loader: str = "CLIPLoader"
    gguf_unet_loader: str = "UnetLoaderGGUF"
    gguf_clip_loader: str = "CLIPLoaderGGUF"


Z_IMAGE_DEFAULTS = ZImageDefaults()


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

def _role_payload(backend_capabilities: dict[str, Any], loader: str, role: str) -> dict[str, Any]:
    loaders = backend_capabilities.get("loaders") if isinstance(backend_capabilities, dict) else None
    loader_payload = (loaders or {}).get(loader) if isinstance(loaders, dict) else None
    roles = (loader_payload or {}).get("roles") if isinstance(loader_payload, dict) else None
    value = (roles or {}).get(role) if isinstance(roles, dict) else None
    return value if isinstance(value, dict) else {}


def _role_available(backend_capabilities: dict[str, Any], loader: str, role: str) -> bool:
    payload = _role_payload(backend_capabilities, loader, role)
    return bool(payload.get("available") and (payload.get("backend_node") or payload.get("backend_key")))


def _loader_available(backend_capabilities: dict[str, Any], loader: str) -> bool:
    loaders = backend_capabilities.get("loaders") if isinstance(backend_capabilities, dict) else None
    payload = (loaders or {}).get(loader) if isinstance(loaders, dict) else None
    return bool(isinstance(payload, dict) and payload.get("available"))


def _capability_blockers(backend_capabilities: dict[str, Any], loader: str) -> list[str]:
    if not backend_capabilities or backend_capabilities.get("reachable") is False:
        return ["Z-Image route requires live Comfy object_info discovery before graph compile."]
    if not _loader_available(backend_capabilities, loader):
        return [f"Z-Image {loader} loader path was not discovered from Comfy object_info."]
    required = {
        "diffusion_model": ["diffusion_model", "text_encoder_primary", "vae_or_ae", "aura_sampling"],
        # V25.9.18: Z-Image GGUF only requires the transformer/model to be GGUF.
        # Official/common routes still use normal AE/VAE safetensors, so do not
        # block GGUF Z-Image on a GGUF VAE role.
        "gguf": ["gguf_unet", "gguf_text_encoder_primary", "vae_or_ae", "aura_sampling"],
    }.get(loader, [])
    blockers: list[str] = []
    for role in required:
        if not _role_available(backend_capabilities, loader, role):
            blockers.append(f"Z-Image {loader} route requires discovered role: {role}.")
    return blockers


def _normalize_gguf_unet_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"UnetLoaderGGUF", "LoaderGGUF"} else Z_IMAGE_DEFAULTS.gguf_unet_loader


def _normalize_gguf_clip_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"CLIPLoaderGGUF", "ClipLoaderGGUF"} else Z_IMAGE_DEFAULTS.gguf_clip_loader


def _gguf_unet_inputs(loader_class: str, model_name: str) -> dict[str, Any]:
    return {"gguf_name": model_name} if loader_class == "LoaderGGUF" else {"unet_name": model_name}


def _vae_loader_for(loader: str, vae_name: str) -> str:
    if loader == "gguf" and str(vae_name or "").lower().endswith(".gguf"):
        return "VaeGGUF"
    return "VAELoader"


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() not in {"", "0", "false", "no", "off", "disabled"}


def _z_image_variant_from_model(model_name: str) -> str:
    lowered = str(model_name or "").casefold()
    return "turbo" if "turbo" in lowered else "base"


def _z_image_quant_from_model(model_name: str) -> str:
    lowered = str(model_name or "")
    for token in ("Q8_0", "Q6_K", "Q5_K_M", "Q5_K_S", "Q4_K_M", "Q4_K_S", "Q3_K_M", "Q2_K"):
        if token.casefold() in lowered.casefold():
            return token
    return ""


def _z_image_clip_type(value: Any) -> str:
    """Return the only valid Comfy CLIP type for current Z-Image routes.

    Older Neo UI payloads used the internal GGUF architecture token `z_image`
    as the CLIPLoaderGGUF `type`. Current Comfy CLIPLoaderGGUF exposes Z-Image
    through `lumina2`, so the compiler must never pass `z_image` into the node.
    """

    candidate = str(value or "").strip().lower().replace("-", "_")
    if candidate in {"", "z_image", "zimage", "z_image_turbo", "zimage_turbo"}:
        return Z_IMAGE_DEFAULTS.clip_type
    return str(value or Z_IMAGE_DEFAULTS.clip_type).strip() or Z_IMAGE_DEFAULTS.clip_type


def compile_z_image_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any],
) -> CompiledJob:
    """Compile ZImage base/Turbo txt2img plus component image modes.

    P5 promotes `z_image + diffusion_model + img2img/inpaint/outpaint`; P6
    promotes `z_image_turbo + diffusion_model + img2img/inpaint/outpaint`; P8.4/P8.5
    syncs ZImage and ZImage Turbo GGUF image-conditioned routes to the same
    provider-owned ZImage stack with loader-specific model nodes. This
    UNETLoader, CLIPLoader(type=lumina2), VAELoader, ModelSamplingAuraFlow,
    KSampler, and route-specific source/mask/canvas latent branches.
    """

    raw_params = job.params or {}
    mode = str(route.mode or job.mode or "txt2img")
    params = normalize_inpaint_target_aliases(raw_params) if mode == "inpaint" else raw_params
    defaults = Z_IMAGE_DEFAULTS
    loader = route.loader
    route_family = route.family or job.family or "z_image"
    is_turbo_family = route_family == "z_image_turbo"
    image_mode = mode in {"img2img", "edit", "inpaint", "outpaint"}

    if image_mode and (route_family not in {"z_image", "z_image_turbo"} or loader not in {"diffusion_model", "gguf"}):
        validation.errors.append(
            f"P5/P8.5 implements ZImage base/Turbo image modes for Safetensors / Components and GGUF only; got {route_family}+{loader}+{mode}."
        )
        validation.ok = False

    blockers = _capability_blockers(backend_capabilities, loader)
    if blockers:
        for blocker in blockers:
            if blocker not in validation.errors:
                validation.errors.append(blocker)
        validation.ok = False
        return CompiledJob(
            provider_id=provider_id,
            compile_status="mock_compiled",
            backend_payload={
                "provider_id": provider_id,
                "backend": "comfyui",
                "base_url": base_url,
                "validation": model_to_dict(validation),
                "compile_route": route.as_dict(),
                "neo_job": model_to_dict(job),
                "backend_capabilities": backend_capabilities,
                "phase_notes": [
                    "ZImage compile is blocked by live Comfy object_info discovery.",
                    "No Comfy prompt graph was generated because required ZImage loader roles were not discovered.",
                ],
            },
        )

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    if loader == "gguf":
        diffusion_model = ""
        gguf_model = job.model or _param(
            params,
            "gguf_model",
            "gguf_unet",
            "model",
            "model_name",
            default="z_image_turbo_Q4_K_M.gguf" if is_turbo_family else "z_image_Q8_0.gguf",
        )
        text_encoder = _param(params, "qwen3_text_encoder", "text_encoder_1", "text_encoder_primary", "clip_name", default="qwen_3_4b.safetensors")
        vae = _param(params, "vae", "ae", "vae_or_ae", default="ae.safetensors")
    else:
        diffusion_model = require_explicit_asset_selection(
            validation,
            f"{'Z-Image Turbo' if is_turbo_family else 'Z-Image'} diffusion model",
            job.model, params.get("diffusion_model"), params.get("model"), params.get("unet"), params.get("model_name"),
        )
        gguf_model = ""
        text_encoder = require_explicit_asset_selection(
            validation,
            "Z-Image Qwen3 text encoder",
            params.get("qwen3_text_encoder"), params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name"),
        )
        vae = require_explicit_asset_selection(
            validation,
            "Z-Image VAE / AE",
            params.get("vae"), params.get("ae"), params.get("vae_or_ae"),
        )

    selected_model_for_variant = str(gguf_model if loader == "gguf" else diffusion_model)
    z_image_variant = "turbo" if is_turbo_family else _z_image_variant_from_model(selected_model_for_variant)
    turbo_raw = _param(params, "turbo_mode", "z_image_turbo", default=None)
    turbo_mode = True if is_turbo_family else (_truthy(turbo_raw) if turbo_raw is not None else z_image_variant == "turbo")
    if turbo_mode:
        z_image_variant = "turbo"

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    if is_turbo_family or turbo_mode:
        steps_default = defaults.turbo_steps
        cfg_default = defaults.turbo_cfg
    else:
        steps_default = defaults.base_steps
        cfg_default = defaults.base_cfg
    if image_mode:
        steps_default = int(_param(params, "steps", default=steps_default) or steps_default)
    steps = int(_param(params, "steps", default=steps_default))
    cfg = float(_param(params, "cfg", default=cfg_default))
    runtime_default_adjustments: list[dict[str, Any]] = []
    if route_family == "z_image" and not image_mode and not turbo_mode:
        if steps < defaults.base_min_steps:
            previous_steps = steps
            steps = defaults.base_steps
            runtime_default_adjustments.append(
                {
                    "field": "steps",
                    "from": previous_steps,
                    "to": steps,
                    "reason": "Base ZImage txt2img steps clamped away from Turbo range.",
                }
            )
        if cfg < defaults.base_min_cfg:
            previous_cfg = cfg
            cfg = defaults.base_cfg
            runtime_default_adjustments.append(
                {
                    "field": "cfg",
                    "from": previous_cfg,
                    "to": cfg,
                    "reason": "Base ZImage txt2img CFG clamped away from Turbo range.",
                }
            )
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    denoise_default = defaults.denoise if mode == "txt2img" else 0.75
    denoise = float(_param(params, "denoise", "strength", default=denoise_default))
    aura_shift = float(_param(params, "z_image_aura_shift", "aura_shift", "shift", default=defaults.aura_shift))
    clip_type = _z_image_clip_type(_param(params, "clip_type", "text_encoder_type", default=defaults.clip_type))

    source_name = _source_image_name(params) if image_mode else ""
    mask_name = _mask_image_name(params) if mode == "inpaint" else ""
    if image_mode and not source_name:
        validation.errors.append(f"ZImage{' Turbo' if is_turbo_family else ''} {loader} {mode} requires Image 1 / source image.")
        validation.ok = False
    if mode == "inpaint" and not mask_name:
        validation.errors.append(f"ZImage{' Turbo' if is_turbo_family else ''} {loader} inpaint requires a mask image.")
        validation.ok = False
    if mode == "outpaint":
        outpaint_payload_check = normalize_outpaint_payload(params, default_width=width, default_height=height)
        if outpaint_padding_total(outpaint_payload_check) <= 0:
            validation.errors.append(f"ZImage{' Turbo' if is_turbo_family else ''} {loader} outpaint requires padding on at least one side.")
            validation.ok = False

    if loader == "gguf":
        model_loader = _normalize_gguf_unet_loader(_param(params, "gguf_unet_loader", "gguf_model_loader", default=_role_payload(backend_capabilities, "gguf", "gguf_unet").get("backend_node")))
        clip_loader = _normalize_gguf_clip_loader(_param(params, "gguf_clip_loader", "gguf_text_encoder_loader", default=_role_payload(backend_capabilities, "gguf", "gguf_text_encoder_primary").get("backend_node")))
        model_name = str(gguf_model)
        workflow_1 = {"class_type": model_loader, "inputs": _gguf_unet_inputs(model_loader, model_name)}
        workflow_2 = {"class_type": clip_loader, "inputs": {"clip_name": str(text_encoder), "type": clip_type, "device": clip_device}}
        vae_loader = _vae_loader_for(loader, str(vae))
        workflow_type = route.workflow_type or "image.txt2img.z_image_gguf"
        compiler = "comfy.z_image_gguf"
    else:
        model_name = str(diffusion_model)
        workflow_1 = {"class_type": defaults.native_unet_loader, "inputs": {"unet_name": model_name, "weight_dtype": weight_dtype}}
        workflow_2 = {"class_type": defaults.native_clip_loader, "inputs": {"clip_name": str(text_encoder), "type": clip_type, "device": clip_device}}
        vae_loader = "VAELoader"
        workflow_type = route.workflow_type or (f"image.{mode}.z_image_native" if image_mode else "image.txt2img.z_image_native")
        compiler = "comfy.z_image_native"

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": workflow_type,
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
        "z_image_profile": {
            "family": route_family,
            "loader": loader,
            "compiler": compiler,
            "enabled_modes": ["txt2img", "img2img", "inpaint", "outpaint"] if (route_family in {"z_image", "z_image_turbo"} and loader in {"diffusion_model", "gguf"}) else ["txt2img"],
            "gated_modes": [] if (route_family in {"z_image", "z_image_turbo"} and loader in {"diffusion_model", "gguf"}) else ["img2img", "inpaint", "outpaint", "edit"],
            "turbo_mode": turbo_mode,
            "z_image_variant": z_image_variant,
            "z_image_quant": _z_image_quant_from_model(model_name),
            "base_turbo_detection": "family_forced_turbo" if is_turbo_family else ("filename_contains_turbo" if turbo_raw is None else "user_override"),
            "default_width": defaults.width,
            "default_height": defaults.height,
            "default_turbo_steps": defaults.turbo_steps,
            "default_base_steps": defaults.base_steps,
            "default_base_cfg": defaults.base_cfg,
            "base_min_steps": defaults.base_min_steps,
            "base_min_cfg": defaults.base_min_cfg,
            "default_clip_type": defaults.clip_type,
            "effective_clip_type": clip_type,
            "provider_nodes": {
                "model_loader": workflow_1["class_type"],
                "text_encoder_loader": workflow_2["class_type"],
                "sampling_patch": defaults.sampling_node,
                "vae_loader": vae_loader,
                "latent_source": "source_vaeencode" if mode in {"img2img", "edit"} else ("source_mask" if mode == "inpaint" else ("source_outpaint_canvas" if mode == "outpaint" else defaults.latent_node)),
            },
            "source_policy": "Image 1 only for P5/P6 ZImage base/Turbo component image modes; Image 2/Image 3 stay hidden/pruned.",
            "does_not_use": ["Flux", "Flux Fill", "Qwen Image Edit compiler", "SD checkpoint", "Base ZImage high-step defaults"] if is_turbo_family else ["Flux", "Flux Fill", "Qwen Image Edit compiler", "SD checkpoint", "ZImage Turbo defaults"],
        },
        "diffusion_model": model_name if loader == "diffusion_model" else "",
        "gguf_model": model_name if loader == "gguf" else "",
        "qwen3_text_encoder": str(text_encoder),
        "vae": str(vae),
        "turbo_mode": turbo_mode,
        "z_image_variant": z_image_variant,
        "z_image_quant": _z_image_quant_from_model(model_name),
        "cfg": cfg,
        "steps": steps,
        "denoise": denoise,
        "z_image_aura_shift": aura_shift,
        "clip_type": clip_type,
        "runtime_default_adjustments": runtime_default_adjustments,
        "_neo_z_image_native_mode": mode,
        "_neo_effective_z_image_native_route": route_family == "z_image" and loader == "diffusion_model",
        "_neo_effective_z_image_turbo_native_route": route_family == "z_image_turbo" and loader == "diffusion_model",
        "_neo_effective_z_image_turbo_gguf_route": route_family == "z_image_turbo" and loader == "gguf",
    }

    workflow: dict[str, Any] = {
        "1": workflow_1,
        "2": workflow_2,
        "3": {"class_type": vae_loader, "inputs": {"vae_name": str(vae)}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
    }
    if turbo_mode:
        workflow["5"] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": ["4", 0]}}
    else:
        workflow["5"] = {"class_type": "CLIPTextEncode", "inputs": {"text": effective_negative, "clip": ["2", 0]}}

    next_id = 6
    source_ref: list[Any] | None = None
    original_source_ref: list[Any] | None = None
    mask_ref: list[Any] | None = None
    route_notes: list[str] = []
    latent_ref: list[Any] | None = None

    if not image_mode:
        workflow[str(next_id)] = {"class_type": defaults.latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}}
        latent_ref = [str(next_id), 0]
        next_id += 1

    if image_mode and source_name:
        workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
        source_ref = [str(next_id), 0]
        original_source_ref = list(source_ref)
        next_id += 1
        actual_params.update({"source_image_name": source_name, "_neo_z_image_image1_latent_anchor": True})

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
        workflow[str(next_id)] = {"class_type": "ImagePadForOutpaint", "inputs": {"image": list(source_ref), "left": left, "top": top, "right": right, "bottom": bottom, "feathering": feather}}
        source_ref = [str(next_id), 0]
        mask_ref = [str(next_id), 1]
        next_id += 1
        width = max(64, int(working_width) + left + right)
        height = max(64, int(working_height) + top + bottom)
        actual_params.update({
            "outpaint_payload": outpaint_payload,
            "_neo_outpaint_contract": outpaint_payload,
            "z_image_outpaint_base_size": {"width": int(working_width), "height": int(working_height)},
            "z_image_outpaint_padding": {"left": left, "top": top, "right": right, "bottom": bottom, "feather": feather, "blur": int(mask.get("blur", 8) or 8)},
            "z_image_outpaint_effective_size": {"width": width, "height": height},
            "z_image_outpaint_source_resolution": outpaint_payload.get("source_resolution", {}),
            "z_image_outpaint_source_scale_node": source_scale_meta or {},
            "_neo_z_image_outpaint_uses_image_pad_mask": True,
        })
        route_notes.append(f"{'P6 ZImage Turbo' if is_turbo_family else 'P5 ZImage'} outpaint uses ImagePadForOutpaint + VAEEncode + SetLatentNoiseMask on the native ZImage stack.")

    workflow[str(next_id)] = {"class_type": defaults.sampling_node, "inputs": {"model": ["1", 0], "shift": aura_shift}}
    model_ref = [str(next_id), 0]
    next_id += 1

    if mode in {"img2img", "edit"} and source_ref is not None:
        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["3", 0]}}
        latent_ref = [str(next_id), 0]
        next_id += 1
        route_notes.append(f"{'P6 ZImage Turbo' if is_turbo_family else 'P5 ZImage'} img2img uses Image 1 as the VAEEncode latent anchor.")
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
            "_neo_z_image_inpaint_uses_latent_noise_mask": True,
            "_neo_z_image_inpaint_uses_differential_diffusion": True,
            "_neo_z_image_inpaint_final_composite": True,
        })
        route_notes.append(f"{'P6 ZImage Turbo' if is_turbo_family else 'P5 ZImage'} inpaint uses source VAEEncode + SetLatentNoiseMask + DifferentialDiffusion + final masked composite.")
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
        workflow[composite_id] = {"class_type": "ImageCompositeMasked", "inputs": {"destination": list(original_source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": list(mask_ref)}}
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
        loader_node_class="LoraLoader",
        source=compiler,
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["ZImage compiler owns model/clip refs; non-generate modes remain LoRA matrix implementation targets until validated."],
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
            "backend_capabilities": backend_capabilities,
            "phase_notes": [
                "P5/P6 promote ZImage base/Turbo Safetensors / Components img2img/inpaint/outpaint as real selectable workflows.",
                "ZImage routes use Qwen3 text encoder via CLIPLoader type=lumina2, AE/VAE, ModelSamplingAuraFlow, KSampler, and route-specific latent source branches.",
                "P6 Turbo image modes keep family-forced Turbo defaults and zeroed negative conditioning, without falling back to base ZImage high-step defaults.",
                "Implemented image modes do not fallback to Flux, Flux Fill, Qwen Image Edit, SD checkpoint, or GGUF compilers.",
                *route_notes,
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
