from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult


@dataclass(frozen=True)
class QwenImage21Defaults:
    width: int = 1024
    height: int = 1024
    steps: int = 25
    cfg: float = 1.0
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    clip_type: str = "qwen_image"
    clip_device: str = "default"
    reference_resolution: int = 1024


QWEN_IMAGE_21_DEFAULTS = QwenImage21Defaults()
QWEN21_COMMON_RUNTIME_ROLES = (
    "qwen21_text_encoder_loader",
    "qwen21_vae_loader",
    "qwen21_conditioning",
    "qwen21_sampler",
    "qwen21_decode",
    "qwen21_empty_latent",
)
QWEN21_MODEL_RUNTIME_ROLE = {
    "diffusion_model": "qwen21_diffusion_loader",
    "gguf": "qwen21_gguf_diffusion_loader",
}


def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _backend_role(backend_capabilities: dict[str, Any] | None, loader: str, role_id: str) -> dict[str, Any]:
    loaders = (backend_capabilities or {}).get("loaders") or {}
    loader_payload = loaders.get(loader) or {}
    roles = loader_payload.get("roles") or {}
    role = roles.get(role_id) or {}
    return role if isinstance(role, dict) else {}


def _qwen21_loader(job: NeoJob, route: CompileRoute) -> str:
    loader = str(route.loader or job.loader or "diffusion_model").strip().lower()
    return "gguf" if loader == "gguf" else "diffusion_model"


def _qwen21_gguf_loader_class(backend_capabilities: dict[str, Any] | None) -> str:
    role = _backend_role(backend_capabilities, "gguf", "qwen21_gguf_diffusion_loader")
    candidate = str(role.get("backend_node") or role.get("backend_key") or "").strip()
    if candidate in {"UnetLoaderGGUF", "LoaderGGUF"}:
        return candidate
    generic = _backend_role(backend_capabilities, "gguf", "gguf_unet")
    candidate = str(generic.get("backend_node") or generic.get("backend_key") or "").strip()
    return candidate if candidate in {"UnetLoaderGGUF", "LoaderGGUF"} else "UnetLoaderGGUF"


def _qwen21_model_loader_inputs(loader_class: str, model_name: str, weight_dtype: str) -> dict[str, Any]:
    if loader_class == "LoaderGGUF":
        return {"gguf_name": model_name}
    if loader_class == "UnetLoaderGGUF":
        return {"unet_name": model_name}
    return {"unet_name": model_name, "weight_dtype": weight_dtype}


def _validate_qwen21_runtime(
    validation: ProviderValidationResult,
    backend_capabilities: dict[str, Any] | None,
    *,
    loader: str,
    require_empty_latent: bool = True,
) -> None:
    """Fail closed only when a live capability snapshot is actually present.

    Provider/readiness validation normally catches these blockers before compile,
    but the compiler keeps a second boundary so replay/direct compiler calls cannot
    silently fall back to old Qwen nodes.
    """
    caps = backend_capabilities or {}
    if not caps or not (caps.get("loaders") or caps.get("object_info_node_inputs")):
        return
    common_roles = QWEN21_COMMON_RUNTIME_ROLES if require_empty_latent else tuple(role for role in QWEN21_COMMON_RUNTIME_ROLES if role != "qwen21_empty_latent")
    runtime_roles = (QWEN21_MODEL_RUNTIME_ROLE.get(loader, "qwen21_diffusion_loader"), *common_roles)
    for role_id in runtime_roles:
        role = _backend_role(caps, loader, role_id)
        if role and role.get("available") is True:
            continue
        validation.errors.append(
            f"Qwen Image 2.1 requires live Comfy capability '{role_id}'. Update ComfyUI to a Qwen Image 2.1-capable build and refresh backend capabilities."
        )
        validation.ok = False


def _qwen21_lora_patch_profile(*, route: CompileRoute, loader: str, model_ref: list[Any], sampler_node_id: str) -> dict[str, Any]:
    """Compiler-owned Q21 LoRA anchor contract.

    Q21-5 uses model-only LoRA patching. Qwen3-VL remains untouched, while
    LoraLoaderModelOnly rewires every consumer of the selected Q21 transformer
    (direct KSampler consumers or DifferentialDiffusion in masked workflows).
    """
    return build_lora_patch_profile(
        route=route.as_dict(),
        model_ref=model_ref,
        clip_ref=None,
        sampler_node_id=sampler_node_id,
        sampler_model_input="model",
        loader_node_class="LoraLoaderModelOnly",
        requires_model=True,
        requires_clip=False,
        source="neo_app.providers.comfy_workflows.qwen_image_21.Q21-5",
        strategy="lora_loader_model_only_consumer_rewire",
        patch_model_consumers=True,
        patch_clip_consumers=False,
        validated=False,
        notes=[
            "Q21-5 LoRA inference is experimental and model-only; Qwen3-VL is not patched.",
            "Use LoRAs trained for Qwen Image 2.1. Neo does not assume Qwen Image 1.x, legacy Qwen Edit, SDXL, or Flux LoRAs are compatible.",
            f"Loader route: {loader}.",
        ],
    )


def _qwen21_lora_runtime_metadata(loader: str) -> dict[str, Any]:
    return {
        "schema": "neo.image.qwen_image_21.lora_compatibility.v1",
        "phase": "Q21-5",
        "state": "experimental_available",
        "mode": "model_only",
        "patch_target": "model_only",
        "loader": loader,
        "loader_node_class": "LoraLoaderModelOnly",
        "text_encoder_patched": False,
        "family_requirement": "qwen_image_21",
        "compatibility_warning": "Only use LoRAs trained for Qwen Image 2.1; adjacent Qwen/Image-Edit, SDXL, Flux, and unrelated LoRAs are not assumed compatible.",
        "warning": "Only use LoRAs trained for Qwen Image 2.1; adjacent Qwen/Image-Edit, SDXL, Flux, and unrelated LoRAs are not assumed compatible.",
    }

QWEN21_OUTPUT_CHANNEL_VALUES = {"auto", "rgb", "rgba"}
QWEN21_RGBA_PROMPT_ASSIST = (
    "Use genuine image transparency/alpha for the background where appropriate. "
    "Do not render a checkerboard, matte, fake transparency pattern, or opaque replacement background."
)


def _qwen21_output_channels(params: dict[str, Any]) -> str:
    raw = str(_param(params, "qwen21_output_channels", "output_channels", default="auto") or "auto").strip().lower()
    aliases = {"transparent": "rgba", "alpha": "rgba", "rgba_transparent": "rgba", "native": "auto"}
    normalized = aliases.get(raw, raw)
    return normalized if normalized in QWEN21_OUTPUT_CHANNEL_VALUES else "auto"


def _qwen21_rgba_prompt(prompt: str, output_channels: str) -> tuple[str, dict[str, Any]]:
    user_prompt = str(prompt or "").strip()
    enabled = output_channels == "rgba"
    effective = f"{user_prompt}\n\n{QWEN21_RGBA_PROMPT_ASSIST}".strip() if enabled else user_prompt
    return effective, {
        "schema": "neo.image.qwen_image_21.rgba_prompt_assist.v1",
        "phase": "Q21-6A",
        "enabled": enabled,
        "requested_output_channels": output_channels,
        "assist_text": QWEN21_RGBA_PROMPT_ASSIST if enabled else "",
        "user_prompt_preserved": True,
    }


def _validate_qwen21_rgba_nodes(
    validation: ProviderValidationResult,
    backend_capabilities: dict[str, Any] | None,
    *,
    loader: str,
    output_channels: str,
    has_source_images: bool,
) -> None:
    caps = backend_capabilities or {}
    if output_channels == "auto" or not caps or not caps.get("loaders"):
        return
    required: list[str] = []
    if output_channels == "rgb":
        required.append("qwen21_alpha_split")
    elif output_channels == "rgba" and has_source_images:
        required.append("qwen21_alpha_join")
    missing: list[str] = []
    for role_id in required:
        role = _backend_role(caps, loader, role_id)
        if not role or role.get("available") is not True:
            missing.append(role_id)
    if missing:
        validation.errors.append(
            "Qwen Image 2.1 explicit output-channel policy is missing live Comfy capabilities: "
            + ", ".join(missing)
            + ". Update ComfyUI and refresh backend capabilities."
        )
        validation.ok = False


def _qwen21_join_loaded_alpha(
    workflow: dict[str, Any],
    *,
    image_ref: list[Any],
    mask_ref: list[Any],
    next_id: int,
) -> tuple[list[Any], int, str]:
    node_id = str(next_id)
    workflow[node_id] = {
        "class_type": "JoinImageWithAlpha",
        "inputs": {"image": list(image_ref), "alpha": list(mask_ref)},
    }
    return [node_id, 0], next_id + 1, node_id


def _qwen21_apply_output_channels(
    workflow: dict[str, Any],
    *,
    output_ref: list[Any],
    output_channels: str,
    next_id: int,
) -> tuple[list[Any], int, dict[str, Any]]:
    execution = {
        "schema": "neo.image.qwen_image_21.rgba_execution.v1",
        "phase": "Q21-6A",
        "requested_output_channels": output_channels,
        "effective_output_channels": output_channels,
        "native_decode_contract": "rgba_capable",
        "save_format": "png",
        "jpeg_alpha_safe": False,
        "split_node_id": "",
        "source_alpha_join_node_ids": [],
        "physical_alpha_validation": "pending_Q21-7",
    }
    if output_channels == "rgb":
        split_id = str(next_id)
        workflow[split_id] = {"class_type": "SplitImageWithAlpha", "inputs": {"image": list(output_ref)}}
        execution.update({
            "alpha_policy": "explicit_strip_alpha",
            "split_node_id": split_id,
            "final_image_ref": [split_id, 0],
        })
        return [split_id, 0], next_id + 1, execution
    execution.update({
        "alpha_policy": "native_rgba_passthrough" if output_channels == "rgba" else "auto_preserve_native_channels",
        "final_image_ref": list(output_ref),
    })
    return list(output_ref), next_id, execution


QWEN21_CACHE_DEVICES = {"auto", "gpu", "cpu", "off"}
QWEN21_CACHE_DTYPES = {"default", "int8", "int4"}


def _qwen21_cache_settings(params: dict[str, Any]) -> dict[str, Any]:
    explicit_device = any(key in params for key in ("qwen21_cache_device", "cache_device"))
    explicit_dtype = any(key in params for key in ("qwen21_cache_dtype", "cache_dtype", "cache_precision"))
    raw_device = str(_param(params, "qwen21_cache_device", "cache_device", default="auto") or "auto").strip().lower()
    raw_dtype = str(_param(params, "qwen21_cache_dtype", "cache_dtype", "cache_precision", default="default") or "default").strip().lower()
    device_aliases = {"cuda": "gpu", "vram": "gpu", "ram": "cpu", "disabled": "off", "none": "off"}
    dtype_aliases = {"fp16": "default", "bf16": "default", "native": "default", "8bit": "int8", "4bit": "int4"}
    device = device_aliases.get(raw_device, raw_device)
    dtype = dtype_aliases.get(raw_dtype, raw_dtype)
    if device not in QWEN21_CACHE_DEVICES:
        device = "auto"
    if dtype not in QWEN21_CACHE_DTYPES:
        dtype = "default"
    return {
        "requested": bool(explicit_device or explicit_dtype),
        "explicit_device": bool(explicit_device),
        "explicit_dtype": bool(explicit_dtype),
        "device": device,
        "dtype": dtype,
    }


def _qwen21_cache_role_state(backend_capabilities: dict[str, Any] | None, loader: str) -> tuple[bool | None, dict[str, Any]]:
    caps = backend_capabilities or {}
    live = bool(caps.get("object_info_available") is True or caps.get("loaders"))
    if not live:
        return None, {}
    role = _backend_role(caps, loader, "qwen21_cache")
    return role.get("available") is True, role


def _qwen21_apply_cache(
    workflow: dict[str, Any],
    *,
    params: dict[str, Any],
    loader: str,
    backend_capabilities: dict[str, Any] | None,
    validation: ProviderValidationResult,
    model_ref: list[Any],
    next_id: int,
) -> tuple[list[Any], int, dict[str, Any]]:
    settings = _qwen21_cache_settings(params)
    availability, role = _qwen21_cache_role_state(backend_capabilities, loader)
    execution = {
        "schema": "neo.image.qwen_image_21.cache_execution.v1",
        "phase": "Q21-6B",
        "requested": bool(settings["requested"]),
        "device": settings["device"],
        "dtype": settings["dtype"],
        "node_available": availability,
        "backend_node": str(role.get("backend_node") or role.get("backend_key") or "QwenImage21Cache"),
        "applied": False,
        "node_id": "",
        "model_input_ref": list(model_ref),
        "model_output_ref": list(model_ref),
        "ordering_contract": "base_model -> compatible_model_lora_stack -> QwenImage21Cache -> route_model_patch -> sampler",
        "physical_validation": "pending_Q21-7",
    }

    # Cache is optional. Direct compiler calls that do not request it preserve the
    # pre-Q21-6B graph even when a live backend happens to advertise the node.
    if not settings["requested"]:
        execution["state"] = "not_requested"
        return list(model_ref), next_id, execution

    if availability is False:
        validation.errors.append(
            "Qwen Image 2.1 cache controls were requested, but the connected ComfyUI backend does not expose a compatible QwenImage21Cache(model, device, dtype) node. Refresh/update ComfyUI or clear the cache override."
        )
        validation.ok = False
        execution["state"] = "blocked_missing_node"
        return list(model_ref), next_id, execution

    node_id = str(next_id)
    workflow[node_id] = {
        "class_type": "QwenImage21Cache",
        "inputs": {
            "model": list(model_ref),
            "device": settings["device"],
            "dtype": settings["dtype"],
        },
    }
    execution.update({
        "state": "applied",
        "applied": True,
        "node_id": node_id,
        "model_output_ref": [node_id, 0],
    })
    return [node_id, 0], next_id + 1, execution



def compile_qwen_image_21_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile Qwen Image 2.1 text-to-image for native or Q21-3 GGUF transformer loading.

    The conditioning stack is intentionally independent from Neo's legacy Qwen
    Image/Edit compiler: TextEncodeQwenImage21 is always used and there is no
    ModelSamplingAuraFlow patch. Q21-3 changes only the diffusion-transformer loader;
    Qwen3-VL 8B and the Qwen Image 2.1 VAE remain native/safetensors.
    """
    params = job.params or {}
    defaults = QWEN_IMAGE_21_DEFAULTS
    loader = _qwen21_loader(job, route)
    _validate_qwen21_runtime(validation, backend_capabilities, loader=loader)

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""
    output_channels = _qwen21_output_channels(params)
    cache_settings = _qwen21_cache_settings(params)
    effective_prompt, rgba_prompt_assist = _qwen21_rgba_prompt(effective_prompt, output_channels)
    _validate_qwen21_rgba_nodes(
        validation, backend_capabilities, loader=loader, output_channels=output_channels, has_source_images=False
    )

    model_name = require_explicit_asset_selection(
        validation,
        "Qwen Image 2.1 GGUF model" if loader == "gguf" else "Qwen Image 2.1 diffusion model",
        job.model,
        params.get("gguf_model") if loader == "gguf" else None,
        params.get("gguf_unet") if loader == "gguf" else None,
        params.get("diffusion_model") if loader != "gguf" else None,
        params.get("unet"),
        params.get("model"),
        params.get("model_name"),
    )
    text_encoder = require_explicit_asset_selection(
        validation,
        "Qwen Image 2.1 Qwen3-VL 8B text encoder",
        params.get("qwen21_text_encoder"),
        params.get("qwen_text_encoder"),
        params.get("text_encoder_1"),
        params.get("text_encoder_primary"),
        params.get("clip_name"),
    )
    vae = require_explicit_asset_selection(
        validation,
        "Qwen Image 2.1 VAE",
        params.get("qwen21_vae"),
        params.get("vae"),
        params.get("vae_or_ae"),
    )

    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    steps = int(_param(params, "steps", default=defaults.steps))
    cfg = float(_param(params, "cfg", "true_cfg", default=defaults.cfg))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    denoise = float(_param(params, "denoise", default=defaults.denoise))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    reference_resolution = int(_param(params, "qwen21_reference_resolution", "reference_resolution", default=defaults.reference_resolution))

    # Q21-1 is T2I only. Keep requested dimensions explicit and do not round them
    # silently; Comfy is the final authority for shape compatibility.
    actual_params = {
        **params,
        "family": "qwen_image_21",
        "loader": loader,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": cfg,
        "true_cfg": cfg,
        "sampler": sampler,
        "scheduler": scheduler,
        "denoise": denoise,
        "batch_count": batch_count,
        "diffusion_model": model_name if loader == "diffusion_model" else "",
        "gguf_model": model_name if loader == "gguf" else "",
        "gguf_unet": model_name if loader == "gguf" else "",
        "qwen21_text_encoder": text_encoder,
        "qwen_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "qwen21_vae": vae,
        "vae": vae,
        "qwen21_output_channels": output_channels,
        "qwen21_rgba_prompt_assist": rgba_prompt_assist,
        "qwen21_rgba_effective_prompt": effective_prompt,
        "workflow_type": route.workflow_type or "image.txt2img.qwen_image_21",
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
        "qwen_image_21_profile": {
            "schema": "neo.image.qwen_image_21.runtime.v1",
            "phase": "Q21-3" if loader == "gguf" else "Q21-1",
            "family": "qwen_image_21",
            "loader": loader,
            "mode": "txt2img",
            "compiler": "comfy.qwen_image_21",
            "status": "experimental_available",
            "reference_count": 0,
            "native_reference_limit": 10,
            "provider_nodes": {
                "diffusion_model_loader": _qwen21_gguf_loader_class(backend_capabilities) if loader == "gguf" else "UNETLoader",
                "text_encoder_loader": "CLIPLoader",
                "vae_loader": "VAELoader",
                "conditioning": "TextEncodeQwenImage21",
                "empty_latent": "EmptyLatentImage",
                "sampler": "KSampler",
                "decode": "VAEDecode",
                "cache": None,
            },
            "clip_type": defaults.clip_type,
            "reference_resolution": reference_resolution,
            "future_phases": {
                "edit_refs": "Q21-2",
                "gguf": "implemented_Q21-3" if loader == "gguf" else "Q21-3",
                "inpaint_outpaint": "Q21-4",
                "lora_hires": "implemented_Q21-5",
                "rgba_control": "implemented_Q21-6A",
                "cache_control": "implemented_Q21-6B",
            },
        },
    }

    model_loader_class = _qwen21_gguf_loader_class(backend_capabilities) if loader == "gguf" else "UNETLoader"
    workflow: dict[str, Any] = {
        "1": {
            "class_type": model_loader_class,
            "inputs": _qwen21_model_loader_inputs(model_loader_class, model_name, weight_dtype),
        },
        "2": {
            "class_type": "CLIPLoader",
            "inputs": {"clip_name": text_encoder, "type": defaults.clip_type, "device": clip_device},
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": vae},
        },
        "4": {
            "class_type": "TextEncodeQwenImage21",
            "inputs": {
                "clip": ["2", 0],
                "prompt": effective_prompt,
                "negative_prompt": effective_negative,
                "vae": ["3", 0],
                "resolution": reference_resolution,
            },
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": width, "height": height, "batch_size": batch_count},
        },
        "6": {
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
                "negative": ["4", 1],
                "latent_image": ["5", 0],
            },
        },
        "7": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["6", 0], "vae": ["3", 0]},
        },
    }
    cache_model_ref, next_id, cache_execution = _qwen21_apply_cache(
        workflow,
        params=params,
        loader=loader,
        backend_capabilities=backend_capabilities,
        validation=validation,
        model_ref=["1", 0],
        next_id=8,
    )
    workflow["6"]["inputs"]["model"] = list(cache_model_ref)
    output_ref, next_id, rgba_execution = _qwen21_apply_output_channels(
        workflow, output_ref=["7", 0], output_channels=output_channels, next_id=next_id
    )
    save_id = str(next_id)
    preview_id = str(next_id + 1)
    workflow[save_id] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "Neo_Qwen_Image_21", "images": list(output_ref)}}
    workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": list(output_ref)}}
    actual_params["_neo_qwen21_rgba_execution"] = rgba_execution
    actual_params["_neo_qwen21_cache_execution"] = cache_execution
    if cache_settings["requested"]:
        actual_params["qwen21_cache_device"] = cache_settings["device"]
        actual_params["qwen21_cache_dtype"] = cache_settings["dtype"]
    if isinstance(actual_params.get("qwen_image_21_profile"), dict):
        actual_params["qwen_image_21_profile"]["provider_nodes"]["cache"] = "QwenImage21Cache" if cache_execution.get("applied") else None

    actual_params["_neo_sampler_node_id"] = "6"
    actual_params["_neo_output_import_node_ids"] = [save_id]
    actual_params["_neo_output_contract"] = {
        "schema": "neo.image.output_contract.v1",
        "final_image_node_ids": [save_id],
        "preview_node_ids": [preview_id],
        "provider_final_output_policy": "prefer_save_image_over_preview",
        "preview_role": "live_preview_only",
        "channel_mode": output_channels,
        "portable_format": "png",
        "alpha_preservation": output_channels != "rgb",
    }
    if cache_settings["requested"]:
        actual_params["qwen21_cache_device"] = cache_settings["device"]
        actual_params["qwen21_cache_dtype"] = cache_settings["dtype"]
    actual_params["_neo_lora_patch_profile"] = _qwen21_lora_patch_profile(
        route=route, loader=loader, model_ref=["1", 0], sampler_node_id="6"
    )
    actual_params["qwen21_lora_compatibility"] = _qwen21_lora_runtime_metadata(loader)
    actual_params["_neo_qwen21_phase_gates"] = {
        "lora": "implemented_Q21-5",
        "hires_fix": "implemented_Q21-5",
        "rgba_control": "implemented_Q21-6A",
        "cache_control": "implemented_Q21-6B",
    }

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
                "Q21-3 adds the GGUF diffusion-transformer route while preserving Q21-1 Safetensors/components txt2img semantics." if loader == "gguf" else "Q21-1 implements Qwen Image 2.1 Safetensors/components txt2img.",
                "TextEncodeQwenImage21 owns positive/negative conditioning; legacy Qwen CLIPTextEncode/ModelSamplingAuraFlow stages are intentionally absent.",
                "Official current Comfy T2I topology uses an external EmptyLatentImage for requested width/height.",
                "Q21-3 GGUF uses a GGUF transformer plus native/safetensors Qwen3-VL 8B and native Qwen 2.1 VAE; no MMProj or GGUF text encoder is required." if loader == "gguf" else "Q21-6A adds explicit Auto/RGB/RGBA output-channel policy.",
                "Q21-6B optionally inserts QwenImage21Cache with Auto/GPU/CPU/Off device policy and Default/INT8/INT4 cache precision; compatible model-only LoRA patches are ordered before the cache node.",
            ],
            "prompt_conditioning": conditioning,
        },
    )



def _qwen21_source_value(params: dict[str, Any], lane: int) -> str:
    if lane == 1:
        names = (
            "comfy_source_image_name", "source_image",
            "source_image_path", "source_image_url", "init_image", "image",
        )
    else:
        names = (
            f"comfy_source_image_{lane}_name",
            f"source_image_{lane}", f"source_image_{lane}_path", f"source_image_{lane}_url",
            f"reference_image_{lane}",
        )
    for name in names:
        value = params.get(name)
        if isinstance(value, dict):
            value = value.get("path") or value.get("file") or value.get("filename") or value.get("name") or value.get("url")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _qwen21_collect_sources(validation: ProviderValidationResult, params: dict[str, Any]) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    missing_after_gap: list[int] = []
    gap_seen = False
    for lane in range(1, 11):
        name = _qwen21_source_value(params, lane)
        if not name:
            gap_seen = True
            continue
        if gap_seen:
            missing_after_gap.append(lane)
        sources.append({
            "lane": lane,
            "name": name,
            "role": str(params.get(f"source_image_{lane}_role") or ("primary_edit_target" if lane == 1 else f"reference_{lane}")),
            "original_name": str(params.get("source_image_name") if lane == 1 else params.get(f"source_image_{lane}_name") or ""),
        })
    if not sources or sources[0]["lane"] != 1:
        validation.errors.append("Qwen Image 2.1 edit requires Image 1 as the primary edit target.")
        validation.ok = False
    if missing_after_gap:
        validation.errors.append(
            "Qwen Image 2.1 references must be contiguous from Image 1. Fill or remove earlier empty lanes before using later references."
        )
        validation.ok = False
    if len(sources) > 10:
        validation.errors.append("Qwen Image 2.1 supports at most 10 ordered reference images in Neo.")
        validation.ok = False
    return sources


def compile_qwen_image_21_edit(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile Q21 unified edit with 1-10 ordered images for native or GGUF transformer loading.

    Q21-3 changes only the diffusion-transformer loader; the Qwen3-VL 8B encoder,
    VAE, conditioning node, ordered reference semantics, and canvas ownership remain
    the same as Q21-2. Image 1 is always the primary edit target. Images 2-10 are ordered references.
    The default canvas uses TextEncodeQwenImage21's third (latent) output, which
    follows Image 1 after the node's own reference-resolution preprocessing.
    Users may explicitly select a custom canvas, in which case Neo supplies a
    separate EmptyLatentImage while preserving the same ordered reference stack.
    """
    params = job.params or {}
    defaults = QWEN_IMAGE_21_DEFAULTS
    loader = _qwen21_loader(job, route)
    _validate_qwen21_runtime(validation, backend_capabilities, loader=loader)

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""
    output_channels = _qwen21_output_channels(params)
    cache_settings = _qwen21_cache_settings(params)
    effective_prompt, rgba_prompt_assist = _qwen21_rgba_prompt(effective_prompt, output_channels)

    model_name = require_explicit_asset_selection(
        validation, "Qwen Image 2.1 GGUF model" if loader == "gguf" else "Qwen Image 2.1 diffusion model", job.model,
        params.get("gguf_model") if loader == "gguf" else None,
        params.get("gguf_unet") if loader == "gguf" else None,
        params.get("diffusion_model") if loader != "gguf" else None,
        params.get("unet"), params.get("model"), params.get("model_name"),
    )
    text_encoder = require_explicit_asset_selection(
        validation, "Qwen Image 2.1 Qwen3-VL 8B text encoder",
        params.get("qwen21_text_encoder"), params.get("qwen_text_encoder"), params.get("text_encoder_1"),
        params.get("text_encoder_primary"), params.get("clip_name"),
    )
    vae = require_explicit_asset_selection(
        validation, "Qwen Image 2.1 VAE", params.get("qwen21_vae"), params.get("vae"), params.get("vae_or_ae"),
    )
    sources = _qwen21_collect_sources(validation, params)
    _validate_qwen21_rgba_nodes(
        validation, backend_capabilities, loader=loader, output_channels=output_channels, has_source_images=bool(sources)
    )

    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    steps = int(_param(params, "steps", default=defaults.steps))
    cfg = float(_param(params, "cfg", "true_cfg", default=defaults.cfg))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    reference_resolution = int(_param(params, "qwen21_reference_resolution", "reference_resolution", default=0))
    canvas_mode_raw = str(_param(params, "qwen21_edit_canvas_mode", default="source")).strip().lower().replace("-", "_")
    canvas_mode = "custom" if canvas_mode_raw in {"custom", "custom_size", "explicit", "manual"} else "source"

    model_loader_class = _qwen21_gguf_loader_class(backend_capabilities) if loader == "gguf" else "UNETLoader"
    workflow: dict[str, Any] = {
        "1": {"class_type": model_loader_class, "inputs": _qwen21_model_loader_inputs(model_loader_class, model_name, weight_dtype)},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": defaults.clip_type, "device": clip_device}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
    }

    conditioning_inputs: dict[str, Any] = {
        "clip": ["2", 0],
        "prompt": effective_prompt,
        "negative_prompt": effective_negative,
        "vae": ["3", 0],
        "resolution": reference_resolution,
    }
    reference_records: list[dict[str, Any]] = []
    next_id = 10
    source_alpha_join_node_ids: list[str] = []
    for source in sources:
        lane = int(source["lane"])
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": source["name"]}}
        source_image_ref: list[Any] = [node_id, 0]
        alpha_join_node_id = ""
        if output_channels == "rgba":
            source_image_ref, next_id, alpha_join_node_id = _qwen21_join_loaded_alpha(
                workflow, image_ref=[node_id, 0], mask_ref=[node_id, 1], next_id=next_id
            )
            source_alpha_join_node_ids.append(alpha_join_node_id)
        # Comfy V3 autogrow inputs are serialized under their dotted socket names.
        conditioning_inputs[f"images.image_{lane}"] = list(source_image_ref)
        reference_records.append({
            "lane": lane,
            "token": f"<image{lane}>",
            "role": source["role"],
            "comfy_name": source["name"],
            "name": source["original_name"],
            "load_node_id": node_id,
            "alpha_join_node_id": alpha_join_node_id,
        })

    cache_model_ref, next_id, cache_execution = _qwen21_apply_cache(
        workflow,
        params=params,
        loader=loader,
        backend_capabilities=backend_capabilities,
        validation=validation,
        model_ref=["1", 0],
        next_id=next_id,
    )
    workflow["4"] = {"class_type": "TextEncodeQwenImage21", "inputs": conditioning_inputs}
    if canvas_mode == "custom":
        workflow["5"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}}
        latent_link = ["5", 0]
    else:
        latent_link = ["4", 2]

    workflow["6"] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
            "denoise": 1.0,
            "model": list(cache_model_ref),
            "positive": ["4", 0],
            "negative": ["4", 1],
            "latent_image": latent_link,
        },
    }
    workflow["7"] = {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["3", 0]}}
    output_ref, next_id, rgba_execution = _qwen21_apply_output_channels(
        workflow, output_ref=["7", 0], output_channels=output_channels, next_id=next_id
    )
    rgba_execution["source_alpha_join_node_ids"] = list(source_alpha_join_node_ids)
    save_id = str(next_id)
    next_id += 1
    preview_id = str(next_id)
    workflow[save_id] = {"class_type": "SaveImage", "inputs": {"filename_prefix": "Neo_Qwen_Image_21_Edit", "images": list(output_ref)}}
    workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": list(output_ref)}}

    actual_params = {
        **params,
        "family": "qwen_image_21",
        "loader": loader,
        "mode": "img2img" if job.mode in {"img2img", "image_to_image"} else "edit",
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": cfg,
        "true_cfg": cfg,
        "sampler": sampler,
        "scheduler": scheduler,
        "denoise": 1.0,
        "batch_count": 1,
        "diffusion_model": model_name if loader == "diffusion_model" else "",
        "gguf_model": model_name if loader == "gguf" else "",
        "gguf_unet": model_name if loader == "gguf" else "",
        "qwen21_text_encoder": text_encoder,
        "qwen_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "qwen21_vae": vae,
        "vae": vae,
        "qwen21_output_channels": output_channels,
        "qwen21_rgba_prompt_assist": rgba_prompt_assist,
        "qwen21_rgba_effective_prompt": effective_prompt,
        "qwen21_reference_resolution": reference_resolution,
        "qwen21_edit_canvas_mode": canvas_mode,
        "_neo_qwen21_canvas_execution": {
            "schema": "neo.image.qwen_image_21.canvas_execution.v1",
            "requested_mode": canvas_mode_raw,
            "compiled_mode": canvas_mode,
            "requested_width": width,
            "requested_height": height,
            "latent_source": "EmptyLatentImage" if canvas_mode == "custom" else "TextEncodeQwenImage21.output[2]",
            "latent_node_id": "5" if canvas_mode == "custom" else "4",
            "latent_output_index": 0 if canvas_mode == "custom" else 2,
        },
        "qwen21_reference_count": len(reference_records),
        "qwen21_reference_order": reference_records,
        "workflow_type": route.workflow_type or f"image.{job.mode}.qwen_image_21",
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
        "qwen_image_21_profile": {
            "schema": "neo.image.qwen_image_21.runtime.v1",
            "phase": "Q21-3" if loader == "gguf" else "Q21-2",
            "family": "qwen_image_21",
            "loader": loader,
            "mode": "img2img" if job.mode in {"img2img", "image_to_image"} else "edit",
            "compiler": "comfy.qwen_image_21",
            "status": "experimental_available",
            "reference_count": len(reference_records),
            "native_reference_limit": 10,
            "reference_order": reference_records,
            "primary_edit_target_lane": 1,
            "prompt_token_contract": "<image1>..<image10> follow exact current numeric lane order",
            "edit_canvas_mode": canvas_mode,
            "reference_resolution": reference_resolution,
            "provider_nodes": {
                "diffusion_model_loader": model_loader_class,
                "text_encoder_loader": "CLIPLoader",
                "vae_loader": "VAELoader",
                "reference_loader": "LoadImage",
                "conditioning": "TextEncodeQwenImage21",
                "source_latent": "TextEncodeQwenImage21.output[2]" if canvas_mode == "source" else "EmptyLatentImage",
                "sampler": "KSampler",
                "decode": "VAEDecode",
                "cache": "QwenImage21Cache" if cache_execution.get("applied") else None,
            },
            "future_phases": {
                "gguf": "implemented_Q21-3" if loader == "gguf" else "Q21-3",
                "inpaint_outpaint": "Q21-4",
                "lora_hires": "Q21-5",
                "rgba_control": "implemented_Q21-6A",
                "cache_control": "implemented_Q21-6B",
            },
        },
        "_neo_sampler_node_id": "6",
        "_neo_output_import_node_ids": [save_id],
        "_neo_output_contract": {
            "schema": "neo.image.output_contract.v1",
            "final_image_node_ids": [save_id],
            "preview_node_ids": [preview_id],
            "provider_final_output_policy": "prefer_save_image_over_preview",
            "preview_role": "live_preview_only",
            "channel_mode": output_channels,
            "portable_format": "png",
            "alpha_preservation": output_channels != "rgb",
        },
        "_neo_qwen21_rgba_execution": rgba_execution,
        "_neo_qwen21_cache_execution": cache_execution,
        "_neo_qwen21_phase_gates": {
            "lora": "implemented_Q21-5",
            "hires_fix": "implemented_Q21-5",
            "rgba_control": "implemented_Q21-6A",
            "cache_control": "implemented_Q21-6B",
        },
    }
    actual_params["_neo_lora_patch_profile"] = _qwen21_lora_patch_profile(
        route=route, loader=loader, model_ref=["1", 0], sampler_node_id="6"
    )
    actual_params["qwen21_lora_compatibility"] = _qwen21_lora_runtime_metadata(loader)
    if isinstance(actual_params.get("qwen_image_21_profile"), dict):
        actual_params["qwen_image_21_profile"]["future_phases"]["lora_hires"] = "implemented_Q21-5"

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
                "Q21-2 implements Qwen Image 2.1 Safetensors/components unified edit with 1-10 ordered references.",
                "Image 1 is the primary edit target; Images 2-10 are ordered references and prompt tokens follow the exact lane order.",
                "Default edit canvas uses TextEncodeQwenImage21 latent output so Image 1 owns output size; custom canvas is an explicit override.",
                "Q21-6A adds Auto/RGB/RGBA output policy; explicit RGBA edit reconstructs LoadImage alpha before TextEncodeQwenImage21.",
                "Q21-6B inserts QwenImage21Cache when requested; global compatible model-only LoRA patches are rewired before the cache node and the cached model then feeds the sampler.",
            ],
            "prompt_conditioning": conditioning,
        },
    )



def _qwen21_mask_name(params: dict[str, Any]) -> str:
    for key in ("comfy_mask_image_name", "mask_image_name", "mask_image", "mask_image_path", "mask_image_url"):
        value = params.get(key)
        if isinstance(value, dict):
            value = value.get("path") or value.get("file") or value.get("filename") or value.get("name") or value.get("url")
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _qwen21_outpaint_prompt_assist(prompt: str, *, preserve_source: bool) -> tuple[str, dict[str, Any]]:
    """Append an explicit, inspectable outpaint-only generation hint.

    Q21-4A physically proved that a native expanded latent runs, but visual tests
    showed Qwen could still treat the padded border as content to preserve. The
    assist never replaces user text; it appends a small route-owned instruction
    and records both the policy and exact suffix in actual_params.
    """
    user_prompt = str(prompt or "").strip()
    source_policy = "preserve" if preserve_source else "redraw"
    if preserve_source:
        suffix = (
            "Outpaint only the added canvas outside Image 1. Treat the padded outer border as empty extension space that must be newly generated, "
            "not as gray/blank content to preserve. Continue the visible scene naturally across every expanded edge with coherent perspective, depth, "
            "lighting, textures, colors, and environmental detail. Keep the original Image 1 region unchanged."
        )
    else:
        suffix = (
            "Use Image 1 as the composition anchor while outpainting the added canvas. Treat the padded outer border as empty extension space that must be "
            "newly generated, not as gray/blank content to preserve. Continue the visible scene naturally across every expanded edge with coherent "
            "perspective, depth, lighting, textures, colors, and environmental detail. Source redraw is allowed when needed for a seamless result."
        )
    effective = f"{user_prompt}\n\n{suffix}" if user_prompt else suffix
    return effective, {
        "schema": "neo.image.qwen_image_21.outpaint_prompt_assist.v1",
        "phase": "Q21-4B",
        "applied": True,
        "source_policy": source_policy,
        "user_prompt_preserved_verbatim": True,
        "suffix": suffix,
        "reason": "Q21-4B explicitly tells Qwen that the padded border is generation space, not content to preserve.",
    }


def _validate_qwen21_masked_nodes(
    validation: ProviderValidationResult,
    backend_capabilities: dict[str, Any] | None,
    *,
    mode: str,
    strict: bool,
    grow: int = 0,
) -> None:
    caps = backend_capabilities or {}
    if not caps or not caps.get("loaders"):
        return
    if mode == "inpaint":
        required = ["qwen21_vae_encode", "qwen21_latent_mask", "qwen21_differential", "qwen21_mask_loader"]
        if grow:
            required.append("qwen21_mask_grow")
        if strict:
            required.append("qwen21_composite")
    else:
        required = ["qwen21_outpaint_pad"]
        if strict:
            required.extend(["qwen21_invert_mask", "qwen21_composite"])
    missing: list[str] = []
    for role_id in required:
        role = _backend_role(caps, "diffusion_model", role_id)
        if not role or role.get("available") is not True:
            missing.append(role_id)
    if missing:
        validation.errors.append(
            "Qwen Image 2.1 masked workflow is missing live Comfy capabilities: "
            + ", ".join(missing)
            + ". Update ComfyUI and refresh backend capabilities."
        )
        validation.ok = False


def compile_qwen_image_21_masked(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Q21-4 unified-model inpaint/outpaint for Safetensors/components.

    Qwen owns semantic edit conditioning through TextEncodeQwenImage21. Neo owns
    hard mask authority for inpaint. Q21-4C aligns outpaint with the native Qwen
    Image 2.1 edit contract: ImagePadForOutpaint feeds Image 1 and KSampler consumes
    TextEncodeQwenImage21 output[2], whose latent is sized from that first reference.
    The older external EmptyLatentImage outpaint branch is intentionally removed.
    """
    params = job.params or {}
    defaults = QWEN_IMAGE_21_DEFAULTS
    loader = _qwen21_loader(job, route)
    mode = "outpaint" if job.mode == "outpaint" else "inpaint"
    if loader != "diffusion_model":
        validation.errors.append(
            "Q21-4 masked workflows are physically enabled for Safetensors / Components first; GGUF inpaint/outpaint remains gated."
        )
        validation.ok = False
    _validate_qwen21_runtime(validation, backend_capabilities, loader=loader, require_empty_latent=False)

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""
    output_channels = _qwen21_output_channels(params)
    cache_settings = _qwen21_cache_settings(params)
    effective_prompt, rgba_prompt_assist = _qwen21_rgba_prompt(effective_prompt, output_channels)

    model_name = require_explicit_asset_selection(
        validation,
        "Qwen Image 2.1 diffusion model",
        job.model,
        params.get("diffusion_model"),
        params.get("unet"),
        params.get("model"),
        params.get("model_name"),
    )
    text_encoder = require_explicit_asset_selection(
        validation,
        "Qwen Image 2.1 Qwen3-VL 8B text encoder",
        params.get("qwen21_text_encoder"),
        params.get("qwen_text_encoder"),
        params.get("text_encoder_1"),
        params.get("text_encoder_primary"),
        params.get("clip_name"),
    )
    vae = require_explicit_asset_selection(
        validation,
        "Qwen Image 2.1 VAE",
        params.get("qwen21_vae"),
        params.get("vae"),
        params.get("vae_or_ae"),
    )
    sources = _qwen21_collect_sources(validation, params)
    _validate_qwen21_rgba_nodes(
        validation,
        backend_capabilities,
        loader=loader,
        output_channels=output_channels,
        has_source_images=bool(sources),
    )
    mask_name = _qwen21_mask_name(params) if mode == "inpaint" else ""
    if mode == "inpaint" and not mask_name:
        validation.errors.append("Qwen Image 2.1 inpaint requires a mask image.")
        validation.ok = False

    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    steps = int(_param(params, "steps", default=defaults.steps))
    cfg = float(_param(params, "cfg", "true_cfg", default=defaults.cfg))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))
    requested_reference_resolution = int(_param(params, "qwen21_reference_resolution", "reference_resolution", default=0))
    # Q21-4C: outpaint must preserve the padded Image 1 geometry. TextEncodeQwenImage21
    # documents resolution=0 as keeping each reference at its own size (rounded to 32),
    # and its output[2] latent is derived from the first reference. Positive resolution
    # budgets would resize the padded canvas before latent creation and can shift the edit.
    reference_resolution = 0 if mode == "outpaint" else requested_reference_resolution
    grow = max(0, int(_param(params, "mask_grow", "grow_mask_by", default=0) or 0))
    blur = max(0, int(_param(params, "mask_blur", "blur_mask_by", default=0) or 0))
    inpaint_policy = str(_param(params, "qwen21_inpaint_preservation", default="strict")).strip().lower()
    inpaint_policy = "native" if inpaint_policy in {"native", "allow_spill", "spill"} else "strict"
    outpaint_policy = str(_param(params, "qwen21_outpaint_source_policy", default="preserve")).strip().lower()
    outpaint_policy = "redraw" if outpaint_policy in {"redraw", "native", "allow_redraw"} else "preserve"

    _validate_qwen21_masked_nodes(
        validation,
        backend_capabilities,
        mode=mode,
        strict=(inpaint_policy == "strict") if mode == "inpaint" else (outpaint_policy == "preserve"),
        grow=grow,
    )

    workflow: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model_name, "weight_dtype": weight_dtype}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": defaults.clip_type, "device": clip_device}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
    }
    next_id = 10
    reference_records: list[dict[str, Any]] = []
    load_refs: dict[int, list[Any]] = {}
    source_alpha_join_node_ids: list[str] = []
    for source in sources:
        lane = int(source["lane"])
        node_id = str(next_id)
        next_id += 1
        workflow[node_id] = {"class_type": "LoadImage", "inputs": {"image": source["name"]}}
        source_image_ref = [node_id, 0]
        alpha_join_node_id = ""
        if output_channels == "rgba":
            source_image_ref, next_id, alpha_join_node_id = _qwen21_join_loaded_alpha(
                workflow, image_ref=[node_id, 0], mask_ref=[node_id, 1], next_id=next_id
            )
            source_alpha_join_node_ids.append(alpha_join_node_id)
        load_refs[lane] = list(source_image_ref)
        reference_records.append(
            {
                "lane": lane,
                "token": f"<image{lane}>",
                "role": source["role"],
                "comfy_name": source["name"],
                "name": source["original_name"],
                "load_node_id": node_id,
                "alpha_join_node_id": alpha_join_node_id,
            }
        )

    source_ref = load_refs.get(1)
    if source_ref is None:
        validation.errors.append(f"Qwen Image 2.1 {mode} requires Image 1.")
        validation.ok = False
        source_ref = ["10", 0]

    mask_ref: list[Any] | None = None
    qwen_primary_ref = list(source_ref)
    padded_source_ref: list[Any] | None = None
    outpaint_payload: dict[str, Any] | None = None
    outpaint_padding: dict[str, int] | None = None
    latent_image_ref: list[Any] | None = None
    model_ref, next_id, cache_execution = _qwen21_apply_cache(
        workflow,
        params=params,
        loader=loader,
        backend_capabilities=backend_capabilities,
        validation=validation,
        model_ref=["1", 0],
        next_id=next_id,
    )
    mask_authority = "SetLatentNoiseMask + DifferentialDiffusion"
    outpaint_execution: dict[str, Any] | None = None
    outpaint_prompt_assist: dict[str, Any] | None = None
    padded_preview_id: str | None = None
    native_preview_id: str | None = None

    if mode == "inpaint":
        mask_node_id = str(next_id)
        next_id += 1
        workflow[mask_node_id] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        mask_ref = [mask_node_id, 0]
        if grow:
            grow_id = str(next_id)
            next_id += 1
            workflow[grow_id] = {
                "class_type": "GrowMask",
                "inputs": {"mask": list(mask_ref), "expand": grow, "tapered_corners": True},
            }
            mask_ref = [grow_id, 0]
        if blur:
            validation.warnings.append(
                "Q21-4 uses core-only mask nodes; mask blur is recorded but not applied in this phase. Use mask feathering in the editor or outpaint feathering for soft transitions."
            )
        encode_id = str(next_id)
        next_id += 1
        workflow[encode_id] = {"class_type": "VAEEncode", "inputs": {"pixels": list(qwen_primary_ref), "vae": ["3", 0]}}
        latent_mask_id = str(next_id)
        next_id += 1
        workflow[latent_mask_id] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": [encode_id, 0], "mask": list(mask_ref or ["0", 0])},
        }
        differential_id = str(next_id)
        next_id += 1
        workflow[differential_id] = {"class_type": "DifferentialDiffusion", "inputs": {"model": list(model_ref)}}
        latent_image_ref = [latent_mask_id, 0]
        model_ref = [differential_id, 0]
    else:
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        if outpaint_padding_total(outpaint_payload) <= 0:
            validation.errors.append("Qwen Image 2.1 outpaint requires padding on at least one side.")
            validation.ok = False
        pad = outpaint_payload.get("padding") or {}
        mask_cfg = outpaint_payload.get("mask") or {}
        left = int(pad.get("left", 0) or 0)
        top = int(pad.get("top", 0) or 0)
        right = int(pad.get("right", 0) or 0)
        bottom = int(pad.get("bottom", 0) or 0)
        feather = int(mask_cfg.get("feather", 16) or 16)
        pad_id = str(next_id)
        next_id += 1
        workflow[pad_id] = {
            "class_type": "ImagePadForOutpaint",
            "inputs": {
                "image": list(source_ref),
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "feathering": feather,
            },
        }
        padded_source_ref = [pad_id, 0]
        mask_ref = [pad_id, 1]
        qwen_primary_ref = list(padded_source_ref)
        padded_preview_id = str(next_id)
        next_id += 1
        workflow[padded_preview_id] = {"class_type": "PreviewImage", "inputs": {"images": list(padded_source_ref)}}
        outpaint_padding = {"left": left, "top": top, "right": right, "bottom": bottom, "feather": feather}
        final_size = outpaint_payload.get("final_size") or {}
        width = max(64, int(final_size.get("width", width + left + right) or (width + left + right)))
        height = max(64, int(final_size.get("height", height + top + bottom) or (height + top + bottom)))
        # Q21-4C: do not create a generic external latent for outpaint. The native
        # TextEncodeQwenImage21 node returns output[2] specifically sized from Image 1,
        # which is the padded canvas here. Bind the sampler to that output after node 4
        # is emitted below.
        latent_image_ref = ["4", 2]
        mask_authority = "Qwen native TextEncodeQwenImage21 first-reference latent"
        outpaint_execution = {
            "schema": "neo.image.qwen_image_21.outpaint_runtime.v1",
            "phase": "Q21-4C",
            "conditioning_image1": "ImagePadForOutpaint.output[0]",
            "generation_latent": "TextEncodeQwenImage21.output[2]",
            "latent_alignment_policy": "first_reference_native_latent",
            "requested_reference_resolution": requested_reference_resolution,
            "effective_reference_resolution": reference_resolution,
            "padding_mask": "ImagePadForOutpaint.output[1]",
            "preserve_center_via_composite": outpaint_policy == "preserve",
            "final_canvas": {"width": width, "height": height},
            "diagnostic_capture": {
                "padded_canvas_preview_node_id": padded_preview_id,
                "native_generated_preview_node_id": None,
                "final_result_preview_node_id": None,
            },
        }

    qwen_conditioning_prompt = effective_prompt
    if mode == "outpaint":
        qwen_conditioning_prompt, outpaint_prompt_assist = _qwen21_outpaint_prompt_assist(
            effective_prompt, preserve_source=(outpaint_policy == "preserve")
        )

    conditioning_inputs: dict[str, Any] = {
        "clip": ["2", 0],
        "prompt": qwen_conditioning_prompt,
        "negative_prompt": effective_negative,
        "vae": ["3", 0],
        "resolution": reference_resolution,
        "images.image_1": list(qwen_primary_ref),
    }
    for lane, ref in load_refs.items():
        if lane > 1:
            conditioning_inputs[f"images.image_{lane}"] = list(ref)
    workflow["4"] = {"class_type": "TextEncodeQwenImage21", "inputs": conditioning_inputs}

    sampler_id = str(next_id)
    next_id += 1
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
            "denoise": 1.0,
            "model": list(model_ref),
            "positive": ["4", 0],
            "negative": ["4", 1],
            "latent_image": list(latent_image_ref or ["0", 0]),
        },
    }
    decode_id = str(next_id)
    next_id += 1
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    output_ref = [decode_id, 0]
    if mode == "outpaint":
        native_preview_id = str(next_id)
        next_id += 1
        workflow[native_preview_id] = {"class_type": "PreviewImage", "inputs": {"images": list(output_ref)}}

    strict_applied = False
    if mode == "inpaint" and inpaint_policy == "strict":
        composite_id = str(next_id)
        next_id += 1
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {
                "destination": list(source_ref),
                "source": list(output_ref),
                "x": 0,
                "y": 0,
                "resize_source": True,
                "mask": list(mask_ref or ["0", 0]),
            },
        }
        output_ref = [composite_id, 0]
        strict_applied = True
    elif mode == "outpaint" and outpaint_policy == "preserve":
        invert_id = str(next_id)
        next_id += 1
        workflow[invert_id] = {"class_type": "InvertMask", "inputs": {"mask": list(mask_ref or ["0", 0])}}
        composite_id = str(next_id)
        next_id += 1
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {
                "destination": list(output_ref),
                "source": list(padded_source_ref or source_ref),
                "x": 0,
                "y": 0,
                "resize_source": True,
                "mask": [invert_id, 0],
            },
        }
        output_ref = [composite_id, 0]
        strict_applied = True

    output_ref, next_id, rgba_execution = _qwen21_apply_output_channels(
        workflow, output_ref=list(output_ref), output_channels=output_channels, next_id=next_id
    )
    rgba_execution["source_alpha_join_node_ids"] = list(source_alpha_join_node_ids)

    save_id = str(next_id)
    next_id += 1
    preview_id = str(next_id)
    workflow[save_id] = {
        "class_type": "SaveImage",
        "inputs": {"filename_prefix": f"Neo_Qwen_Image_21_{'Inpaint' if mode == 'inpaint' else 'Outpaint'}", "images": list(output_ref)},
    }
    workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": list(output_ref)}}
    if outpaint_execution is not None:
        outpaint_execution["diagnostic_capture"] = {
            "padded_canvas_preview_node_id": padded_preview_id,
            "native_generated_preview_node_id": native_preview_id,
            "final_result_preview_node_id": preview_id,
        }
        outpaint_execution["native_generated_image_node_id"] = decode_id
        outpaint_execution["final_output_image_ref"] = list(output_ref)

    provider_nodes = {
        "model_loader": "UNETLoader",
        "text_encoder_loader": "CLIPLoader",
        "vae_loader": "VAELoader",
        "conditioning": "TextEncodeQwenImage21",
        "sampler": "KSampler",
        "decode": "VAEDecode",
        "cache": "QwenImage21Cache" if cache_execution.get("applied") else None,
        "composite": "ImageCompositeMasked" if strict_applied else None,
    }
    if mode == "inpaint":
        provider_nodes.update(
            {
                "mask_loader": "LoadImageMask",
                "latent_mask": "SetLatentNoiseMask",
                "differential": "DifferentialDiffusion",
            }
        )
    else:
        provider_nodes.update(
            {
                "canvas_pad": "ImagePadForOutpaint.output[0]",
                "padding_mask": "ImagePadForOutpaint.output[1]",
                "latent_source": "TextEncodeQwenImage21.output[2]",
                "differential": None,
                "latent_mask": None,
            }
        )

    actual_params = {
        **params,
        "family": "qwen_image_21",
        "loader": "diffusion_model",
        "mode": mode,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "width": width,
        "height": height,
        "steps": steps,
        "cfg": cfg,
        "true_cfg": cfg,
        "sampler": sampler,
        "scheduler": scheduler,
        "denoise": 1.0,
        "diffusion_model": model_name,
        "qwen21_text_encoder": text_encoder,
        "qwen_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "qwen21_vae": vae,
        "vae": vae,
        "qwen21_output_channels": output_channels,
        "qwen21_rgba_prompt_assist": rgba_prompt_assist,
        "qwen21_rgba_effective_prompt": effective_prompt,
        "qwen21_reference_resolution": reference_resolution,
        "qwen21_reference_resolution_requested": requested_reference_resolution,
        "qwen21_reference_count": len(reference_records),
        "qwen21_reference_order": reference_records,
        "mask_image_name": mask_name if mode == "inpaint" else "",
        "mask_grow": grow,
        "mask_blur": blur,
        "qwen21_inpaint_preservation": inpaint_policy,
        "qwen21_outpaint_source_policy": outpaint_policy,
        "outpaint_payload": outpaint_payload or {},
        "_neo_outpaint_contract": outpaint_payload or {},
        "qwen21_outpaint_padding": outpaint_padding or {},
        "_neo_qwen21_strict_composite_applied": strict_applied,
        "workflow_type": route.workflow_type or f"image.{mode}.qwen_image_21",
        "qwen_image_21_profile": {
            "schema": "neo.image.qwen_image_21.runtime.v1",
            "phase": "Q21-4C" if mode == "outpaint" else "Q21-4",
            "family": "qwen_image_21",
            "loader": "diffusion_model",
            "mode": mode,
            "compiler": "comfy.qwen_image_21",
            "status": "experimental_available",
            "reference_count": len(reference_records),
            "native_reference_limit": 10,
            "reference_order": reference_records,
            "primary_edit_target_lane": 1,
            "reference_resolution": reference_resolution,
            "mask_authority": mask_authority,
            "output_preservation": inpaint_policy if mode == "inpaint" else outpaint_policy,
            "strict_composite_applied": strict_applied,
            "provider_nodes": provider_nodes,
            "future_phases": {
                "lora": "implemented_Q21-5",
                "hires_fix": "implemented_Q21-5",
                "rgba_control": "implemented_Q21-6A",
                "cache_control": "implemented_Q21-6B",
            },
        },
        "_neo_sampler_node_id": sampler_id,
        "_neo_output_import_node_ids": [save_id],
        "_neo_output_contract": {
            "schema": "neo.image.output_contract.v1",
            "final_image_node_ids": [save_id],
            "preview_node_ids": [preview_id],
            "diagnostic_preview_node_ids": [node_id for node_id in (padded_preview_id, native_preview_id) if node_id],
            "provider_final_output_policy": "prefer_save_image_over_preview",
            "preview_role": "live_preview_only",
            "channel_mode": output_channels,
            "portable_format": "png",
            "alpha_preservation": output_channels != "rgb",
        },
        "_neo_qwen21_rgba_execution": rgba_execution,
        "_neo_qwen21_cache_execution": cache_execution,
        "_neo_qwen21_phase_gates": {
            "lora": "implemented_Q21-5",
            "hires_fix": "implemented_Q21-5",
            "rgba_control": "implemented_Q21-6A",
            "cache_control": "implemented_Q21-6B",
        },
    }
    if outpaint_execution is not None:
        actual_params["_neo_qwen21_outpaint_execution"] = outpaint_execution
    if outpaint_prompt_assist is not None:
        actual_params["qwen21_outpaint_prompt_assist"] = outpaint_prompt_assist
        actual_params["qwen21_outpaint_effective_prompt"] = qwen_conditioning_prompt
    if cache_settings["requested"]:
        actual_params["qwen21_cache_device"] = cache_settings["device"]
        actual_params["qwen21_cache_dtype"] = cache_settings["dtype"]
    actual_params["_neo_lora_patch_profile"] = _qwen21_lora_patch_profile(
        route=route,
        loader="diffusion_model",
        model_ref=["1", 0],
        sampler_node_id=sampler_id,
    )
    actual_params["qwen21_lora_compatibility"] = _qwen21_lora_runtime_metadata("diffusion_model")

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
                "Q21-4 keeps Qwen Image 2.1 semantic edit conditioning while Neo owns mask/canvas authority.",
                "Inpaint uses SetLatentNoiseMask + DifferentialDiffusion; Strict additionally restores pixels outside the mask with ImageCompositeMasked.",
                "Q21-4A removed the padded latent-noise-mask outpaint branch; Q21-4B added prompt assistance and padded/native/final diagnostic previews.",
                "Q21-4C binds outpaint KSampler.latent_image to TextEncodeQwenImage21 output[2], the native latent sized from padded Image 1, and forces effective outpaint reference resolution to 0 so the expanded canvas geometry stays aligned.",
                "Preserve Original restores the center after generation with ImageCompositeMasked; Allow Source Redraw returns the native full-canvas result.",
                "Q21-5 enables model-only LoRA anchors and mask-aware High-Res Lab refinement for this masked route.",
                "Q21-6A adds explicit Auto/RGB/RGBA output-channel policy; RGBA source edits reconstruct LoadImage alpha before Qwen VAE reference encoding, while RGB explicitly strips final alpha before PNG save.",
                "Q21-6B inserts QwenImage21Cache before DifferentialDiffusion on inpaint and before KSampler on outpaint; compatible global model LoRAs are rewired ahead of the cache node.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
