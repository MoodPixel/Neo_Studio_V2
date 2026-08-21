from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.comfy_workflows.adetailer_route_contract import publish_adetailer_route_contract
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile
from neo_app.providers.comfy_workflows.vae_decode_utils import (
    build_vae_decode_node,
    build_vae_loader_node,
    resolve_vae_decode_strategy,
    vae_decode_profile_payload,
)


@dataclass(frozen=True)
class QwenNativeDefaults:
    """Provider compiler defaults for the first Qwen Image native Comfy route.

    P3 keeps this compiler focused on no-source Qwen component generation.
    Image-conditioned Qwen Image Edit and Qwen Image Edit 2509 routes compile
    through comfy.qwen_native_edit instead of being recorded as future gates.
    """

    width: int = 1328
    height: int = 1328
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


QWEN_NATIVE_DEFAULTS = QwenNativeDefaults()


def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _is_gguf_file(value: Any) -> bool:
    return str(value or "").strip().lower().endswith(".gguf")


def _qwen_native_clip_loader_for_encoder(text_encoder: Any, requested_loader: Any = None) -> str:
    candidate = str(requested_loader or "").strip()
    if _is_gguf_file(text_encoder):
        return candidate if candidate in {"CLIPLoaderGGUF", "ClipLoaderGGUF"} else "CLIPLoaderGGUF"
    return "CLIPLoader"


def _backend_supports_qwen_clip_loader(backend_capabilities: dict[str, Any] | None, loader_class: str, *, clip_type: str = "qwen_image") -> bool:
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


def compile_qwen_native_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile the Qwen Image native txt2img graph.

    P3 also lets Qwen Image Edit 2509 no-source component routes use this
    compiler for matrix completeness; image-conditioned modes stay in
    compile_qwen_native_edit. Comfy node names stay provider-local.
    """

    params = job.params or {}
    defaults = QWEN_NATIVE_DEFAULTS
    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    diffusion_model = require_explicit_asset_selection(
        validation,
        "Qwen Image diffusion model",
        job.model, params.get("diffusion_model"), params.get("qwen_model"), params.get("unet"), params.get("model"), params.get("model_name"),
    )
    text_encoder = require_explicit_asset_selection(
        validation,
        "Qwen Image text encoder",
        params.get("qwen_text_encoder"), params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name"),
    )
    vae = require_explicit_asset_selection(
        validation,
        "Qwen Image VAE",
        params.get("vae"), params.get("vae_or_ae"),
    )
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    steps = int(_param(params, "steps", default=defaults.steps))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    denoise = float(_param(params, "denoise", default=defaults.denoise))
    cfg = float(_param(params, "cfg", default=defaults.cfg))
    aura_shift = float(_param(params, "qwen_aura_shift", "aura_shift", "shift", default=defaults.aura_shift))
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    requested_clip_loader = _param(params, "clip_loader", "text_encoder_loader", "qwen_clip_loader", default="")
    clip_loader = _qwen_native_clip_loader_for_encoder(text_encoder, requested_clip_loader)
    text_encoder_file_type = "gguf" if _is_gguf_file(text_encoder) else "native"
    if clip_loader != "CLIPLoader" and backend_capabilities is not None and not _backend_supports_qwen_clip_loader(backend_capabilities, clip_loader, clip_type=defaults.clip_type):
        validation.errors.append(f"Selected Qwen text encoder '{text_encoder}' needs {clip_loader}(type={defaults.clip_type}) in ComfyUI.")
        validation.ok = False
    vae_decode_strategy = resolve_vae_decode_strategy(
        params=params,
        family=job.family or "qwen_image",
        loader=route.loader or job.loader or "diffusion_model",
        mode="txt2img",
        backend_capabilities=backend_capabilities,
        validation=validation,
    )

    actual_params = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or "image.txt2img.qwen_native",
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
        "qwen_native_profile": {
            "family": job.family or "qwen_image",
            "visible_family": job.family or "qwen_image",
            "loader": "diffusion_model",
            "default_width": defaults.width,
            "default_height": defaults.height,
            "default_steps": defaults.steps,
            "default_cfg": defaults.cfg,
            "default_aura_shift": defaults.aura_shift,
            "compiler": "comfy.qwen_native",
            "enabled_modes": ["txt2img"],
            "image_conditioned_compiler": "comfy.qwen_native_edit",
            "provider_nodes": {
                "diffusion_model_loader": "UNETLoader",
                "text_encoder_loader": clip_loader,
                "requested_text_encoder_loader": requested_clip_loader or clip_loader,
                "sampling_patch": defaults.sampling_node,
                "vae_loader": vae_decode_strategy["loader_node_class"],
                "vae_decode": vae_decode_strategy["decode_node_class"],
            },
            "text_encoder_policy": "single_qwen_mixed_loader",
            "text_encoder_file_type": text_encoder_file_type,
            "loader_rule": "Qwen safetensors/components use CLIPLoader(type=qwen_image) for native encoders and CLIPLoaderGGUF(type=qwen_image) for GGUF encoders.",
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
        "denoise": denoise,
        "cfg": cfg,
    }

    workflow: dict[str, Any] = {
        "1": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": diffusion_model,
                "weight_dtype": weight_dtype,
            },
        },
        "2": {
            "class_type": clip_loader,
            "inputs": {
                "clip_name": text_encoder,
                "type": defaults.clip_type,
                "device": clip_device,
            },
        },
        "3": build_vae_loader_node(vae, vae_decode_strategy),
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": effective_prompt, "clip": ["2", 0]},
        },
        "5": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": effective_negative, "clip": ["2", 0]},
        },
        "6": {
            "class_type": defaults.latent_node,
            "inputs": {"width": width, "height": height, "batch_size": batch_count},
        },
        "7": {
            "class_type": defaults.sampling_node,
            "inputs": {"model": ["1", 0], "shift": aura_shift},
        },
        "8": {
            "class_type": "KSampler",
            "inputs": {
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": sampler if sampler != "provider_default" else defaults.sampler,
                "scheduler": scheduler if scheduler != "provider_default" else defaults.scheduler,
                "denoise": denoise,
                "model": ["7", 0],
                "positive": ["4", 0],
                "negative": ["5", 0],
                "latent_image": ["6", 0],
            },
        },
        "9": build_vae_decode_node(["8", 0], ["3", 0], vae_decode_strategy),
        "10": {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": _qwen_output_prefix(family=job.family, mode=job.mode), "images": ["9", 0]},
        },
        "11": {
            "class_type": "PreviewImage",
            "inputs": {"images": ["9", 0]},
        },
    }
    actual_params["_neo_sampler_node_id"] = "8"
    actual_params["_neo_output_import_node_ids"] = ["10"]
    actual_params["_neo_output_contract"] = {
        "schema": "neo.image.output_contract.v1",
        "final_image_node_ids": ["10"],
        "preview_node_ids": ["11"],
        "provider_final_output_policy": "prefer_save_image_over_preview",
        "preview_role": "live_preview_only",
    }
    is_qwen_edit_family = str(job.family or "").strip().lower() in {"qwen_image_edit_2509", "qwen_image_edit_2511"}
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate", "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id="8",
        sampler_model_input="model",
        loader_node_class="LoraLoaderModelOnly" if is_qwen_edit_family else "LoraLoader",
        requires_clip=not is_qwen_edit_family,
        source="comfy.qwen_native",
        strategy="lora_loader_model_only_consumer_rewire" if is_qwen_edit_family else "lora_loader_model_clip_consumer_rewire",
        patch_clip_consumers=not is_qwen_edit_family,
        validated=False,
        notes=["Qwen Edit 2509/2511 uses a model-only LoRA branch so the compiler-owned Qwen CLIP conditioning is not rewritten." if is_qwen_edit_family else "Qwen Image Base retains the model+CLIP LoRA strategy."],
    )

    publish_adetailer_route_contract(
        actual_params=actual_params,
        workflow=workflow,
        route=route,
        image_ref=["9", 0],
        model_ref=["7", 0],
        clip_ref=["2", 0],
        vae_ref=["3", 0],
        positive_ref=["4", 0],
        negative_ref=["5", 0],
        sampler_node_id="8",
        source="comfy.qwen_native",
        compiler_id="comfy.qwen_native",
        model_sampling_state="patched",
        model_sampling_ref=["7", 0],
        model_sampling_nodes=["7"],
        notes=["Phase 6 Qwen Edit generate route publishes exact ModelSamplingAuraFlow lineage." if is_qwen_edit_family else "Phase 5 Qwen Image Base native route publishes exact ModelSamplingAuraFlow and decoded-image anchors."],
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
                "V25.9.20 P3 promotes Qwen component routes out of gate-first wording; txt2img uses the Qwen native compiler.",
                "Qwen native uses UNETLoader plus a mixed single-encoder loader: native encoders use CLIPLoader(type=qwen_image), GGUF encoders use CLIPLoaderGGUF(type=qwen_image).",
                "Qwen img2img, edit, inpaint, and outpaint compile through comfy.qwen_native_edit when an image-conditioned route is selected.",
                f"VAE decode path: {vae_decode_strategy['decode_node_class']}.",
                "Comfy node names are provider-local diagnostics; Image surface contracts stay family+loader+mode.",
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
