from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.krea2_anypaint_capabilities import (
    ADAPTER_BASENAME as KREA2_ANYPAINT_ADAPTER_BASENAME,
    inspect_krea2_anypaint_capabilities,
)
from neo_app.image.krea2_anypaint_canvas import build_krea2_anypaint_canvas_contract
from neo_app.image.krea2_contract import check_krea2_compatibility, resolve_krea2_variant
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.comfy_workflows.adetailer_route_contract import publish_adetailer_route_contract
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile


@dataclass(frozen=True)
class Krea2AnyPaintDefaults:
    width: int = 1024
    height: int = 1024
    steps: int = 8
    cfg: float = 1.0
    sampler: str = "euler"
    scheduler: str = "simple"
    denoise: float = 1.0
    clip_type: str = "krea2"
    clip_device: str = "default"
    reference_max_edge: int = 384
    boundary_redraw_px: int = 32
    adapter_strength: float = 1.0


DEFAULTS = Krea2AnyPaintDefaults()
COMPILER_ID = "comfy.krea2_anypaint.phase4"
ENGINE_ID = "krea2_anypaint"
SUPPORTED_FAMILY = "krea2_turbo"
SUPPORTED_LOADER = "diffusion_model"
SUPPORTED_MODES = {"inpaint", "outpaint"}


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


def _float_param(params: dict[str, Any], *names: str, default: float = 0.0) -> float:
    try:
        return float(_param(params, *names, default=default))
    except (TypeError, ValueError):
        return float(default)


def _bool_param(params: dict[str, Any], *names: str, default: bool = False) -> bool:
    value = _param(params, *names, default=default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value or "").strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled", ""}:
        return False
    return bool(value)


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


def _capability_report(backend_capabilities: dict[str, Any] | None) -> dict[str, Any]:
    capabilities = backend_capabilities if isinstance(backend_capabilities, dict) else {}
    report = capabilities.get("krea2_anypaint_capabilities")
    if isinstance(report, dict) and report:
        return report
    object_info = capabilities.get("object_info_node_inputs")
    reachable = bool(capabilities.get("object_info_available") or object_info)
    return inspect_krea2_anypaint_capabilities(
        object_info if isinstance(object_info, dict) else {},
        provider_id=capabilities.get("provider_id") or "comfyui",
        reachable=reachable,
        error=capabilities.get("object_info_error"),
    )


def _normalized_asset_identity(value: Any) -> str:
    return str(value or "").replace("\\", "/").strip().casefold()


def _normalized_asset_basename(value: Any) -> str:
    return _normalized_asset_identity(value).rsplit("/", 1)[-1]


def _resolve_adapter(validation: ProviderValidationResult, params: dict[str, Any], report: dict[str, Any]) -> str:
    adapter = str(_param(
        params,
        "krea2_anypaint_adapter",
        "krea2_anypaint_lora",
        "krea2_anypaint_adapter_lora",
        "anypaint_adapter",
        "anypaint_lora",
        default="",
    ) or "").strip()
    adapter_info = report.get("adapter") if isinstance(report.get("adapter"), dict) else {}
    candidates = [str(item) for item in (adapter_info.get("candidates") or []) if str(item or "").strip()]
    if not adapter:
        return candidates[0] if candidates else KREA2_ANYPAINT_ADAPTER_BASENAME
    if not candidates:
        return adapter
    normalized = {_normalized_asset_identity(item) for item in candidates}
    basenames = {_normalized_asset_basename(item) for item in candidates}
    if _normalized_asset_identity(adapter) not in normalized and _normalized_asset_basename(adapter) not in basenames:
        validation.errors.append(
            f"Krea 2 AnyPaint adapter '{adapter}' is not present in the live LoraLoaderModelOnly catalog. "
            f"Install {KREA2_ANYPAINT_ADAPTER_BASENAME} under ComfyUI/models/loras and refresh backend capabilities."
        )
        validation.ok = False
    return adapter


def _validate_route(validation: ProviderValidationResult, route: CompileRoute, job: NeoJob) -> tuple[str, str, str]:
    family = str(route.family or job.family or "").strip().lower()
    loader = str(route.loader or job.loader or "").strip().lower()
    mode = str(route.mode or job.mode or "").strip().lower()
    if family != SUPPORTED_FAMILY or loader != SUPPORTED_LOADER or mode not in SUPPORTED_MODES:
        validation.errors.append(
            "Krea 2 AnyPaint Phase 4 supports only krea2_turbo + diffusion_model + inpaint/outpaint."
        )
        validation.ok = False
    return family, loader, mode


def compile_krea2_anypaint_workflow(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile the dedicated Krea 2 AnyPaint masked-edit workflow.

    This compiler is intentionally independent from ``compile_krea2_workflow``.
    It owns route validation, Krea component validation, AnyPaint readiness,
    adapter resolution, mask/canvas preparation, model patching, sampling, and
    final raw VAE decode.
    """

    params = dict(job.params or {})
    family, loader, mode = _validate_route(validation, route, job)

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1))
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed))
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    model_name = str(require_explicit_asset_selection(
        validation,
        "Krea 2 AnyPaint diffusion model",
        job.model,
        params.get("diffusion_model"), params.get("model"), params.get("unet"), params.get("model_name"),
    ))
    text_encoder = str(require_explicit_asset_selection(
        validation,
        "Krea 2 AnyPaint Qwen3-VL-4B text encoder",
        params.get("qwen3vl_text_encoder"), params.get("text_encoder_1"), params.get("text_encoder_primary"), params.get("clip_name"),
    ))
    vae = str(require_explicit_asset_selection(
        validation,
        "Krea 2 AnyPaint Qwen Image VAE",
        params.get("vae"), params.get("vae_or_ae"), params.get("ae"),
    ))

    variant = resolve_krea2_variant(family or SUPPORTED_FAMILY, model_name)
    compatibility = check_krea2_compatibility(family or SUPPORTED_FAMILY, model_name, text_encoder, vae, loader=loader)
    if variant != "turbo":
        validation.errors.append("Krea 2 AnyPaint requires a Krea 2 Turbo diffusion model; the selected model resolves to RAW.")
        validation.ok = False
    if compatibility.compatible is False:
        if compatibility.message not in validation.errors:
            validation.errors.append(compatibility.message)
        validation.ok = False
    elif compatibility.compatible is None and compatibility.message and compatibility.message not in validation.warnings:
        validation.warnings.append(compatibility.message)

    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    if not source_name:
        validation.errors.append(f"Krea 2 AnyPaint {mode} requires Image 1 / source image.")
        validation.ok = False
    if mode == "inpaint" and not mask_name:
        validation.errors.append("Krea 2 AnyPaint inpaint requires a mask image.")
        validation.ok = False

    width = max(64, _int_param(params, "width", default=DEFAULTS.width))
    height = max(64, _int_param(params, "height", default=DEFAULTS.height))
    steps = max(1, _int_param(params, "steps", default=DEFAULTS.steps))
    cfg = _float_param(params, "cfg", default=DEFAULTS.cfg)
    sampler = str(_param(params, "sampler", default=DEFAULTS.sampler))
    scheduler = str(_param(params, "scheduler", default=DEFAULTS.scheduler))
    batch_count = max(1, _int_param(params, "batch_count", "batch_size", default=1))
    denoise = _float_param(params, "denoise", "strength", default=DEFAULTS.denoise)
    clip_device = str(_param(params, "clip_device", "text_encoder_device", default=DEFAULTS.clip_device))
    weight_dtype = str(_param(params, "weight_dtype", "model_precision", default="default"))

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""

    outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height) if mode == "outpaint" else {
        "padding": {"left": 0, "top": 0, "right": 0, "bottom": 0},
        "mask": {"feather": 16, "blur": 8},
        "final_size": {"width": width, "height": height},
    }
    if mode == "outpaint" and outpaint_padding_total(outpaint_payload) <= 0 and not mask_name:
        validation.errors.append("Krea 2 AnyPaint outpaint requires padding on at least one side or a generated mask.")
        validation.ok = False
    padding = outpaint_payload.get("padding") if isinstance(outpaint_payload, dict) else {}
    left = max(0, int((padding or {}).get("left", 0) or 0))
    top = max(0, int((padding or {}).get("top", 0) or 0))
    right = max(0, int((padding or {}).get("right", 0) or 0))
    bottom = max(0, int((padding or {}).get("bottom", 0) or 0))

    canvas_contract = build_krea2_anypaint_canvas_contract(
        params,
        mode=mode,
        default_width=width,
        default_height=height,
    )
    final_canvas = canvas_contract.get("final_size") if isinstance(canvas_contract.get("final_size"), dict) else {}
    source_size = canvas_contract.get("source_size") if isinstance(canvas_contract.get("source_size"), dict) else {}
    final_width = max(16, int(final_canvas.get("width", width) or width))
    final_height = max(16, int(final_canvas.get("height", height) or height))
    if canvas_contract.get("authoritative") is not True:
        message = (
            "Krea 2 AnyPaint source dimensions were not available at submission time. "
            "Neo will preserve the original source pixels and let Krea2AnyPaintPrepare determine the physical 16px-aligned canvas at runtime."
        )
        if message not in validation.warnings:
            validation.warnings.append(message)

    # AnyPaint never uses Neo's generic outpaint working-copy resize policy.
    # Normalize the metadata to source-native geometry so replay/inspection does
    # not imply that an ImageScale node exists in this compiler.
    if mode == "outpaint":
        outpaint_payload = {
            **outpaint_payload,
            "source_resolution": {
                "mode": "keep_original",
                "source_size": {
                    "width": int(source_size.get("width", 0) or 0),
                    "height": int(source_size.get("height", 0) or 0),
                },
                "working_size": {
                    "width": int(source_size.get("width", 0) or 0),
                    "height": int(source_size.get("height", 0) or 0),
                },
                "applies_working_copy": False,
                "owner": "Krea2AnyPaintPrepare",
            },
            "final_size": {"width": final_width, "height": final_height},
        }

    reference_max_edge = max(128, min(768, _int_param(params, "krea2_anypaint_reference_max_edge", "anypaint_reference_max_edge", default=DEFAULTS.reference_max_edge)))
    reference_max_edge -= reference_max_edge % 16
    reference_max_edge = max(128, reference_max_edge)
    boundary_redraw_px = max(0, min(256, _int_param(params, "krea2_anypaint_boundary_redraw_px", "anypaint_boundary_redraw_px", default=DEFAULTS.boundary_redraw_px)))
    kv_cache = _bool_param(params, "krea2_anypaint_kv_cache", "anypaint_kv_cache", default=True)
    vlm_reference = _bool_param(params, "krea2_anypaint_vlm_reference", "anypaint_vlm_reference", default=True)
    adapter_strength = _float_param(params, "krea2_anypaint_lora_strength", "anypaint_lora_strength", default=DEFAULTS.adapter_strength)

    report = _capability_report(backend_capabilities)
    if report.get("capability_ready") is not True:
        for blocker in report.get("blockers") or []:
            message = str(blocker.get("message") or "Krea 2 AnyPaint backend readiness is blocked.")
            if message not in validation.errors:
                validation.errors.append(message)
        validation.ok = False
    if report.get("execution_enabled") is not True:
        message = "Krea 2 AnyPaint execution is not enabled by the current backend capability report."
        if message not in validation.errors:
            validation.errors.append(message)
        validation.ok = False
    adapter_name = _resolve_adapter(validation, params, report)

    workflow: dict[str, Any] = {
        "1": {"class_type": "UNETLoader", "inputs": {"unet_name": model_name, "weight_dtype": weight_dtype}},
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": DEFAULTS.clip_type, "device": clip_device}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
        "4": {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}},
    }
    next_id = 5
    generated_mask_ref: list[Any] | None = None
    if mask_name:
        workflow[str(next_id)] = {"class_type": "LoadImageMask", "inputs": {"image": mask_name, "channel": "red"}}
        generated_mask_ref = [str(next_id), 0]
        next_id += 1

    prepare_inputs: dict[str, Any] = {
        "source": ["4", 0],
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "reference_max_edge": reference_max_edge,
        "boundary_redraw_px": boundary_redraw_px,
    }
    if generated_mask_ref is not None:
        prepare_inputs["generated_mask"] = list(generated_mask_ref)
    prepare_node_id = str(next_id)
    workflow[prepare_node_id] = {"class_type": "Krea2AnyPaintPrepare", "inputs": prepare_inputs}
    next_id += 1

    encode_node_id = str(next_id)
    workflow[encode_node_id] = {
        "class_type": "Krea2AnyPaintEncode",
        "inputs": {
            "clip": ["2", 0],
            "prompt": effective_prompt,
            "vae": ["3", 0],
            "semantic_reference": [prepare_node_id, 0],
            "known_image": [prepare_node_id, 1],
            "keep_mask": [prepare_node_id, 3],
            "vlm_reference": vlm_reference,
        },
    }
    next_id += 1

    negative_node_id = str(next_id)
    workflow[negative_node_id] = {"class_type": "ConditioningZeroOut", "inputs": {"conditioning": [encode_node_id, 0]}}
    next_id += 1

    adapter_node_id = str(next_id)
    workflow[adapter_node_id] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"model": ["1", 0], "lora_name": adapter_name, "strength_model": adapter_strength},
    }
    next_id += 1

    patch_node_id = str(next_id)
    workflow[patch_node_id] = {"class_type": "Krea2AnyPaintModelPatch", "inputs": {"model": [adapter_node_id, 0], "kv_cache": kv_cache}}
    next_id += 1

    latent_ref: list[Any] = [encode_node_id, 1]
    if batch_count > 1:
        workflow[str(next_id)] = {"class_type": "RepeatLatentBatch", "inputs": {"samples": list(latent_ref), "amount": batch_count}}
        latent_ref = [str(next_id), 0]
        next_id += 1

    sampler_id = str(next_id)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed,
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else DEFAULTS.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else DEFAULTS.scheduler,
            "denoise": denoise,
            "model": [patch_node_id, 0],
            "positive": [encode_node_id, 0],
            "negative": [negative_node_id, 0],
            "latent_image": latent_ref,
        },
    }
    next_id += 1

    decode_id = str(next_id)
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    next_id += 1
    output_node_id = str(next_id)
    workflow[output_node_id] = {"class_type": "PreviewImage", "inputs": {"images": [decode_id, 0]}}

    actual_params: dict[str, Any] = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.krea2_anypaint",
        "krea2_variant": "turbo",
        "krea2_edit_engine": "native",
        "inpaint_engine": ENGINE_ID,
        "masked_edit_engine": ENGINE_ID,
        "crop_stitch_enabled": False,
        "krea2_anypaint_phase": str(params.get("krea2_anypaint_phase") or "phase7_runtime_validation"),
        "krea2_anypaint_compiler_id": COMPILER_ID,
        "krea2_anypaint_execution_enabled": True,
        "krea2_anypaint_runtime_validation": True,
        "krea2_anypaint_capability_status": str(report.get("status") or "unknown"),
        "krea2_anypaint_capability_ready": report.get("capability_ready") is True,
        "krea2_anypaint_adapter": adapter_name,
        "krea2_anypaint_lora_strength": adapter_strength,
        "krea2_anypaint_reference_max_edge": reference_max_edge,
        "krea2_anypaint_boundary_redraw_px": boundary_redraw_px,
        "krea2_anypaint_vlm_reference": vlm_reference,
        "krea2_anypaint_kv_cache": kv_cache,
        "krea2_anypaint_canvas_padding": {"left": left, "top": top, "right": right, "bottom": bottom},
        "krea2_anypaint_canvas_contract": canvas_contract,
        "_neo_krea2_anypaint_canvas_contract": canvas_contract,
        "source_image_name": source_name,
        "mask_image_name": mask_name,
        "qwen3vl_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "text_encoder_2": "",
        "vae": vae,
        "vae_decode_mode": "native",
        "diffusion_model": model_name,
        "gguf_model": "",
        "clip_type": DEFAULTS.clip_type,
        "steps": steps,
        "cfg": cfg,
        "denoise": denoise,
        "prompt_conditioning_mode": conditioning_mode,
        "clamp": conditioning_mode,
        "krea2_profile": {
            "family": SUPPORTED_FAMILY,
            "variant": "turbo",
            "loader": SUPPORTED_LOADER,
            "compiler": COMPILER_ID,
            "architecture": "krea2_anypaint_dit_qwen3vl4b_qwen_image_vae",
            "compatibility": compatibility.as_dict(),
            "clip_loader": "CLIPLoader(type=krea2)",
            "text_encoder_policy": "Qwen3-VL-4B native/safetensors; AnyPaint owns its own grounded reference encoding.",
            "vae_policy": "Qwen Image VAE via standard VAELoader.",
            "anypaint": {
                "node_pack": "alexw5702-afk/krea2-anypaint",
                "prepare_node": "Krea2AnyPaintPrepare",
                "encode_node": "Krea2AnyPaintEncode",
                "model_patch_node": "Krea2AnyPaintModelPatch",
                "adapter_lora": adapter_name,
                "model_loader": "LoraLoaderModelOnly",
                "weight_dtype": weight_dtype,
            },
        },
        "outpaint_payload": outpaint_payload if mode == "outpaint" else {},
        "_neo_outpaint_contract": outpaint_payload if mode == "outpaint" else {},
        "krea2_anypaint_contract": {
            "source_node_id": "4",
            "mask_node_id": str(generated_mask_ref[0]) if generated_mask_ref is not None else "",
            "prepare_node_id": prepare_node_id,
            "encode_node_id": encode_node_id,
            "negative_node_id": negative_node_id,
            "adapter_node_id": adapter_node_id,
            "model_patch_node_id": patch_node_id,
            "sampler_node_id": sampler_id,
            "decode_node_id": decode_id,
            "output_node_id": output_node_id,
        },
        "_neo_effective_krea2_native_route": False,
        "_neo_effective_krea2_gguf_route": False,
        "_neo_krea2_anypaint_prepare_outputs": {
            "semantic_reference": [prepare_node_id, 0],
            "known_image": [prepare_node_id, 1],
            "generated_mask": [prepare_node_id, 2],
            "keep_mask": [prepare_node_id, 3],
            "canvas_width": [prepare_node_id, 4],
            "canvas_height": [prepare_node_id, 5],
        },
        "_neo_krea2_anypaint_no_final_composite": True,
        "_neo_sampler_node_id": sampler_id,
    }

    # Width/height become provider-owned derived canvas dimensions only when
    # source metadata proves the actual source geometry. If the source size is
    # unknown, keep the user's requested values so parameter-integrity reports
    # an unverified boundary instead of inventing a physical size.
    if canvas_contract.get("authoritative") is True:
        actual_params["width"] = final_width
        actual_params["height"] = final_height
    actual_params["outpaint_source_resolution_mode"] = "keep_original" if mode == "outpaint" else actual_params.get("outpaint_source_resolution_mode", "")
    actual_params["outpaint_working_width"] = int(source_size.get("width", 0) or 0) if mode == "outpaint" else actual_params.get("outpaint_working_width", 0)
    actual_params["outpaint_working_height"] = int(source_size.get("height", 0) or 0) if mode == "outpaint" else actual_params.get("outpaint_working_height", 0)

    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": mode, "route_state": "available" if route.status == "available" else route.status},
        model_ref=["1", 0],
        clip_ref=["2", 0],
        sampler_node_id=sampler_id,
        sampler_model_input="model",
        loader_node_class="LoraLoaderModelOnly",
        source=COMPILER_ID,
        strategy="lora_loader_model_only_consumer_rewire",
        requires_clip=False,
        patch_clip_consumers=False,
        validated=False,
        notes=[
            "Global Krea 2 LoRAs are rewired upstream of the dedicated AnyPaint adapter; the AnyPaint compiler never borrows the native Krea compiler graph.",
        ],
    )

    publish_adetailer_route_contract(
        actual_params=actual_params,
        workflow=workflow,
        route=route,
        image_ref=[decode_id, 0],
        model_ref=[patch_node_id, 0],
        clip_ref=["2", 0],
        vae_ref=["3", 0],
        positive_ref=[encode_node_id, 0],
        negative_ref=[negative_node_id, 0],
        sampler_node_id=sampler_id,
        source=COMPILER_ID,
        compiler_id=COMPILER_ID,
        model_sampling_state="anypaint_patched",
        model_sampling_ref=[patch_node_id, 0],
        model_sampling_nodes=[adapter_node_id, patch_node_id],
        notes=["Krea 2 AnyPaint is compiled by its standalone provider module and publishes raw VAE decode without a final source composite."],
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
            "compile_route": {**route.as_dict(), "compiler_id": COMPILER_ID, "workflow_engine": ENGINE_ID},
            "capabilities": capabilities,
            "backend_capabilities": backend_capabilities or {},
            "phase_notes": [
                "Krea 2 AnyPaint Phase 4 is compiled by neo_app.providers.comfy_workflows.krea2_anypaint and does not call compile_krea2_workflow.",
                "The standalone graph is source/mask -> Krea2AnyPaintPrepare -> Krea2AnyPaintEncode; base model -> AnyPaint adapter -> Krea2AnyPaintModelPatch -> KSampler -> raw VAEDecode.",
                "Native masked-edit nodes and the native Krea seam-guard composite are intentionally absent.",
                "AnyPaint preserves the source at original pixel resolution; Krea2AnyPaintPrepare owns 16px final-canvas alignment and adds alignment pixels on the right/bottom only.",
                "Global model-only Krea 2 LoRAs remain supported through the standard consumer-rewire patch profile upstream of the AnyPaint adapter.",
                *([f"Outpaint padding: left {left}, top {top}, right {right}, bottom {bottom}; final aligned canvas {final_width}x{final_height}."] if mode == "outpaint" else []),
            ],
            "prompt_conditioning": conditioning,
        },
    )


__all__ = [
    "COMPILER_ID",
    "ENGINE_ID",
    "compile_krea2_anypaint_workflow",
]
