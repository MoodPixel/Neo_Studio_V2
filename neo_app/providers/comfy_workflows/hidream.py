from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile


HIDREAM_VARIANTS: dict[str, dict[str, Any]] = {
    "HiDream-I1": {
        "variant_id": "HiDream-I1",
        "label": "HiDream-I1",
        "role": "image_generation",
        "supported_modes": ["txt2img"],
        "default_steps": 28,
        "default_cfg": 4.0,
    },
    "HiDream-E1": {
        "variant_id": "HiDream-E1",
        "label": "HiDream-E1",
        "role": "edit_variant",
        "supported_modes": ["txt2img"],
        "gated_modes": ["img2img", "inpaint", "edit"],
        "default_steps": 28,
        "default_cfg": 4.0,
    },
    "HiDream-O1": {
        "variant_id": "HiDream-O1",
        "label": "HiDream-O1",
        "role": "outpaint_variant",
        "supported_modes": ["txt2img"],
        "gated_modes": ["outpaint"],
        "default_steps": 28,
        "default_cfg": 4.0,
    },
}


@dataclass(frozen=True)
class HiDreamDefaults:
    """Provider compiler defaults for Phase 12.16 HiDream first route.

    HiDream is registered as a real family, but Phase 12.16 only queues the
    safest provider-supported txt2img route. Image-conditioned modes remain
    gated until a selected variant has a proven workflow compiler.
    """

    width: int = 1024
    height: int = 1024
    steps: int = 28
    cfg: float = 4.0
    denoise: float = 1.0
    sampler: str = "euler"
    scheduler: str = "normal"
    latent_node: str = "EmptySD3LatentImage"
    native_unet_loader: str = "UNETLoader"
    native_clip_loader: str = "CLIPLoader"
    gguf_unet_loader: str = "UnetLoaderGGUF"
    gguf_clip_loader: str = "CLIPLoaderGGUF"
    clip_type: str = "hidream"
    clip_device: str = "default"


HIDREAM_DEFAULTS = HiDreamDefaults()


def _param(params: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _role_payload(backend_capabilities: dict[str, Any], loader: str, role: str) -> dict[str, Any]:
    loaders = backend_capabilities.get("loaders") if isinstance(backend_capabilities, dict) else None
    payload = (loaders or {}).get(loader) if isinstance(loaders, dict) else None
    roles = (payload or {}).get("roles") if isinstance(payload, dict) else None
    role_payload = (roles or {}).get(role) if isinstance(roles, dict) else None
    return role_payload if isinstance(role_payload, dict) else {}


def _role_available(backend_capabilities: dict[str, Any], loader: str, role: str) -> bool:
    return bool(_role_payload(backend_capabilities, loader, role).get("available"))


def _loader_available(backend_capabilities: dict[str, Any], loader: str) -> bool:
    loaders = backend_capabilities.get("loaders") if isinstance(backend_capabilities, dict) else None
    payload = (loaders or {}).get(loader) if isinstance(loaders, dict) else None
    return bool(isinstance(payload, dict) and payload.get("available"))


def _capability_blockers(backend_capabilities: dict[str, Any], loader: str) -> list[str]:
    if not backend_capabilities or backend_capabilities.get("reachable") is False:
        return ["HiDream route requires live Comfy object_info discovery before graph compile."]
    if not _loader_available(backend_capabilities, loader):
        return [f"HiDream {loader} loader path was not discovered from Comfy object_info."]
    required = {
        "diffusion_model": ["diffusion_model", "text_encoder_primary", "vae_or_ae", "sampler"],
        "gguf": ["gguf_unet", "gguf_text_encoder_primary", "gguf_vae", "sampler"],
    }.get(loader, [])
    blockers: list[str] = []
    for role in required:
        if not _role_available(backend_capabilities, loader, role):
            blockers.append(f"HiDream {loader} route requires discovered role: {role}.")
    return blockers


def _normalize_variant(value: Any) -> str:
    raw = str(value or "HiDream-I1").strip() or "HiDream-I1"
    aliases = {
        "i1": "HiDream-I1",
        "hidream-i1": "HiDream-I1",
        "hidream_i1": "HiDream-I1",
        "e1": "HiDream-E1",
        "hidream-e1": "HiDream-E1",
        "hidream_e1": "HiDream-E1",
        "o1": "HiDream-O1",
        "hidream-o1": "HiDream-O1",
        "hidream_o1": "HiDream-O1",
    }
    return aliases.get(raw.casefold(), raw if raw in HIDREAM_VARIANTS else "HiDream-I1")


def _normalize_gguf_unet_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"UnetLoaderGGUF", "LoaderGGUF"} else HIDREAM_DEFAULTS.gguf_unet_loader


def _normalize_gguf_clip_loader(value: Any) -> str:
    candidate = str(value or "").strip()
    return candidate if candidate in {"CLIPLoaderGGUF", "ClipLoaderGGUF"} else HIDREAM_DEFAULTS.gguf_clip_loader


def _gguf_unet_inputs(loader_class: str, model_name: str) -> dict[str, Any]:
    return {"gguf_name": model_name} if loader_class == "LoaderGGUF" else {"unet_name": model_name}


def _vae_loader_for(loader: str, vae_name: str) -> str:
    if loader == "gguf" and str(vae_name or "").lower().endswith(".gguf"):
        return "VaeGGUF"
    return "VAELoader"


def compile_hidream_txt2img(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any],
) -> CompiledJob:
    """Compile Phase 12.16 HiDream provider-supported txt2img graph.

    This route is intentionally conservative. It only emits a Comfy graph when
    live discovery confirms the selected loader roles. Variant declarations are
    recorded in metadata, while img2img/inpaint/outpaint/edit remain gated.
    """

    params = job.params or {}
    defaults = HIDREAM_DEFAULTS
    loader = route.loader
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
                    "Phase 12.16 declares HiDream txt2img, but graph compile is gated by live Comfy discovery.",
                    "No Comfy prompt graph was generated because required HiDream loader roles were not discovered.",
                ],
            },
        )

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    variant = _normalize_variant(_param(params, "hidream_variant", "variant", default="HiDream-I1"))
    variant_meta = HIDREAM_VARIANTS[variant]
    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    diffusion_model = job.model or _param(params, "diffusion_model", "model", "unet", "model_name", default="hidream_i1_dev.safetensors")
    gguf_model = job.model or _param(params, "gguf_model", "gguf_unet", "model", "model_name", default="hidream_i1_dev_Q4_K_M.gguf")
    text_encoder = _param(params, "text_encoder_1", "text_encoder_primary", "clip_name", default="provider_default")
    vae = _param(params, "vae", "ae", "vae_or_ae", default="ae.safetensors")
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))
    clip_type = str(_param(params, "clip_type", "text_encoder_type", default=defaults.clip_type))
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=defaults.clip_device))
    steps = int(_param(params, "steps", default=variant_meta.get("default_steps", defaults.steps)))
    cfg = float(_param(params, "cfg", default=variant_meta.get("default_cfg", defaults.cfg)))
    sampler = str(_param(params, "sampler", default=defaults.sampler))
    scheduler = str(_param(params, "scheduler", default=defaults.scheduler))
    width = int(_param(params, "width", default=defaults.width))
    height = int(_param(params, "height", default=defaults.height))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    denoise = float(_param(params, "denoise", default=defaults.denoise))

    if loader == "gguf":
        model_loader = _normalize_gguf_unet_loader(_param(params, "gguf_unet_loader", "gguf_model_loader", default=_role_payload(backend_capabilities, "gguf", "gguf_unet").get("backend_node")))
        clip_loader = _normalize_gguf_clip_loader(_param(params, "gguf_clip_loader", "gguf_text_encoder_loader", default=_role_payload(backend_capabilities, "gguf", "gguf_text_encoder_primary").get("backend_node")))
        model_name = str(gguf_model)
        workflow_1 = {"class_type": model_loader, "inputs": _gguf_unet_inputs(model_loader, model_name)}
        workflow_2 = {"class_type": clip_loader, "inputs": {"clip_name": str(text_encoder), "type": clip_type, "device": clip_device}}
        vae_loader = _vae_loader_for(loader, str(vae))
        workflow_type = route.workflow_type or "image.txt2img.hidream_gguf"
        compiler = "comfy.hidream_gguf"
    else:
        model_name = str(diffusion_model)
        workflow_1 = {"class_type": defaults.native_unet_loader, "inputs": {"unet_name": model_name, "weight_dtype": weight_dtype}}
        workflow_2 = {"class_type": defaults.native_clip_loader, "inputs": {"clip_name": str(text_encoder), "type": clip_type, "device": clip_device}}
        vae_loader = "VAELoader"
        workflow_type = route.workflow_type or "image.txt2img.hidream_native"
        compiler = "comfy.hidream_native"

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
        "hidream_variant": variant,
        "hidream_variants": HIDREAM_VARIANTS,
        "hidream_profile": {
            "family": "hidream",
            "loader": loader,
            "compiler": compiler,
            "selected_variant": variant,
            "variants": HIDREAM_VARIANTS,
            "enabled_modes": ["txt2img"],
            "gated_modes": ["img2img", "inpaint", "outpaint", "edit"],
            "provider_nodes": {
                "model_loader": workflow_1["class_type"],
                "text_encoder_loader": workflow_2["class_type"],
                "vae_loader": vae_loader,
                "sampler": "KSampler",
            },
        },
        "diffusion_model": model_name if loader == "diffusion_model" else "",
        "gguf_model": model_name if loader == "gguf" else "",
        "text_encoder_1": str(text_encoder),
        "vae": str(vae),
        "cfg": cfg,
        "denoise": denoise,
    }

    workflow: dict[str, Any] = {
        "1": workflow_1,
        "2": workflow_2,
        "3": {"class_type": vae_loader, "inputs": {"vae_name": str(vae)}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": effective_negative, "clip": ["2", 0]}},
        "6": {"class_type": defaults.latent_node, "inputs": {"width": width, "height": height, "batch_size": batch_count}},
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
    }
    actual_params["_neo_sampler_node_id"] = "7"
    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": "generate", "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id="7",
        sampler_model_input="model",
        loader_node_class="LoraLoader",
        source="comfy.hidream",
        strategy="lora_loader_model_clip_consumer_rewire",
        validated=False,
        notes=["HiDream compiler owns model/clip refs; image-conditioned modes remain gated."],
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
                "Phase 12.16 enables HiDream txt2img for discovered provider-supported loader paths only.",
                "HiDream-I1, HiDream-E1, and HiDream-O1 are registered as variants; only txt2img is queue-enabled in this phase.",
                "HiDream img2img, inpaint, outpaint, and edit remain gated until variant-specific workflows exist.",
                f"Prompt conditioning mode: {conditioning_mode}.",
            ],
            "prompt_conditioning": conditioning,
        },
    )
