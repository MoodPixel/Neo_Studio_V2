from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.inpaint_payload import normalize_inpaint_target_aliases
from neo_app.image.krea2_contract import (
    KREA2_EDIT_ENGINE_IDENTITY,
    KREA2_IDENTITY_EDIT_NODE_CLASSES,
    KREA2_IDENTITY_EDIT_RECOMMENDED_LORA,
    KREA2_IDENTITY_EDIT_NODE_REPO,
    check_krea2_compatibility,
    normalize_krea2_edit_engine,
    normalize_krea2_identity_fit_mode,
    resolve_krea2_variant,
)
from neo_app.image.outpaint_contract import normalize_outpaint_payload, outpaint_padding_total
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.comfy_workflows.adetailer_route_contract import publish_adetailer_route_contract
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



def _source_image_name_for_lane(params: dict[str, Any], lane: int) -> str:
    if lane == 1:
        return _source_image_name(params)
    aliases = {
        2: (
            "comfy_source_image_2_name", "source_image_2_name", "source_image__2_name",
            "reference_image_2_name", "source_image_2", "source_image_2_path",
            "source_image__2", "reference_image_2", "source_image_2_url",
        ),
        3: (
            "comfy_source_image_3_name", "source_image_3_name", "source_image__3_name",
            "composition_image_name", "reference_image_3_name", "source_image_3",
            "source_image_3_path", "source_image__3", "composition_image",
            "reference_image_3", "source_image_3_url",
        ),
    }
    for key in aliases.get(int(lane), ()):  # pragma: no branch - tiny fixed map
        value = _image_name_value(params.get(key))
        if value:
            return value
    return ""


def _backend_role_available(backend_capabilities: dict[str, Any] | None, loader: str, role_id: str) -> bool | None:
    capabilities = backend_capabilities if isinstance(backend_capabilities, dict) else {}
    if not capabilities.get("object_info_available"):
        return None
    loaders = capabilities.get("loaders") if isinstance(capabilities.get("loaders"), dict) else {}
    loader_row = loaders.get(loader) if isinstance(loaders.get(loader), dict) else {}
    roles = loader_row.get("roles") if isinstance(loader_row.get("roles"), dict) else {}
    role = roles.get(role_id) if isinstance(roles.get(role_id), dict) else {}
    return bool(role.get("available"))


def _validate_krea2_identity_runtime(
    validation: ProviderValidationResult,
    backend_capabilities: dict[str, Any] | None,
    *,
    loader: str,
    identity_lora: str,
    two_reference: bool,
) -> None:
    capabilities = backend_capabilities if isinstance(backend_capabilities, dict) else {}
    if not capabilities.get("object_info_available"):
        message = (
            "Krea 2 Identity Edit runtime nodes could not be verified because Comfy /object_info is unavailable. "
            f"Install/update {KREA2_IDENTITY_EDIT_NODE_REPO} before queueing."
        )
        if message not in validation.warnings:
            validation.warnings.append(message)
        return

    role_map = {
        "krea2_edit_model_patch": "Krea2EditModelPatch (v1.2+ sockets)",
        "krea2_edit_grounded_encode": "Krea2EditGroundedEncode (v1.2+ sockets)",
        "krea2_identity_lora_loader": "LoraLoaderModelOnly",
        "krea2_edit_target_latent": "EmptySD3LatentImage",
    }
    missing = [label for role_id, label in role_map.items() if _backend_role_available(capabilities, loader, role_id) is False]
    if missing:
        validation.errors.append(
            "Krea 2 Identity Edit requires the current comfyui-krea2edit workflow contract. Missing/incompatible nodes: "
            + ", ".join(missing)
            + f". Install/update {KREA2_IDENTITY_EDIT_NODE_REPO} and restart ComfyUI."
        )
        validation.ok = False

    loaders = capabilities.get("loaders") if isinstance(capabilities.get("loaders"), dict) else {}
    loader_row = loaders.get(loader) if isinstance(loaders.get(loader), dict) else {}
    roles = loader_row.get("roles") if isinstance(loader_row.get("roles"), dict) else {}
    lora_role = roles.get("krea2_identity_lora_loader") if isinstance(roles.get("krea2_identity_lora_loader"), dict) else {}
    lora_assets = lora_role.get("assets") if isinstance(lora_role.get("assets"), dict) else {}
    names: list[str] = []
    for values in lora_assets.values():
        if isinstance(values, list):
            names.extend(str(value) for value in values)
    if names and identity_lora:
        selected = identity_lora.replace("\\", "/").casefold()
        normalized = {name.replace("\\", "/").casefold() for name in names}
        basename_matches = {name.rsplit("/", 1)[-1] for name in normalized}
        if selected not in normalized and selected.rsplit("/", 1)[-1] not in basename_matches:
            validation.errors.append(
                f"Krea 2 Identity Edit LoRA '{identity_lora}' is not present in the live LoraLoaderModelOnly catalog. "
                f"Install {KREA2_IDENTITY_EDIT_RECOMMENDED_LORA} under ComfyUI/models/loras (or a subfolder) and refresh the backend."
            )
            validation.ok = False

    if two_reference:
        patch_available = _backend_role_available(capabilities, loader, "krea2_edit_model_patch")
        grounded_available = _backend_role_available(capabilities, loader, "krea2_edit_grounded_encode")
        if patch_available is False or grounded_available is False:
            validation.ok = False


def _compile_krea2_identity_edit(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None,
    params: dict[str, Any],
    mode: str,
    loader: str,
    model_node: dict[str, Any],
    model_name: str,
    compiler_id: str,
    text_encoder: str,
    vae: str,
    variant: str,
    turbo: bool,
    compatibility: Any,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler: str,
    scheduler: str,
    denoise: float,
    batch_count: int,
    clip_device: str,
    conditioning: dict[str, Any],
    effective_prompt: str,
) -> CompiledJob:
    source_name = _source_image_name_for_lane(params, 1)
    source_b_name = _source_image_name_for_lane(params, 2)
    mask_name = _mask_image_name(params) if mode == "inpaint" else ""
    identity_lora = str(require_explicit_asset_selection(
        validation,
        "Krea 2 Identity Edit LoRA",
        params.get("krea2_identity_edit_lora"),
        params.get("identity_edit_lora"),
        params.get("edit_lora"),
    ))
    identity_lora_strength = float(_param(params, "krea2_identity_edit_lora_strength", "identity_edit_lora_strength", default=1.0))
    ref_boost = float(_param(params, "krea2_identity_edit_ref_boost", "ref_boost", default=4.0))
    ref_boost_a = float(_param(params, "krea2_identity_edit_ref_boost_a", "ref_boost_a", default=1.0))
    fit_mode = normalize_krea2_identity_fit_mode(_param(params, "krea2_identity_edit_fit_mode", "fit_mode", default="fit"))
    grounding_px = max(0, int(_param(params, "krea2_identity_edit_grounding_px", "grounding_px", default=768)))
    system_prompt = str(_param(params, "krea2_identity_edit_system_prompt", "grounding_system_prompt", default="") or "")
    two_reference = bool(source_b_name)
    if str(job.negative_prompt or "").strip():
        warning = "Krea 2 Identity Edit uses an empty image-grounded negative to match training; the user negative prompt is not applied in this engine."
        if warning not in validation.warnings:
            validation.warnings.append(warning)

    _validate_krea2_identity_runtime(
        validation,
        backend_capabilities,
        loader=loader,
        identity_lora=identity_lora,
        two_reference=two_reference,
    )

    workflow: dict[str, Any] = {
        "1": model_node,
        "2": {"class_type": "CLIPLoader", "inputs": {"clip_name": text_encoder, "type": KREA2_DEFAULTS.clip_type, "device": clip_device}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": vae}},
    }
    next_id = 6
    route_notes: list[str] = [
        "Krea 2 Identity Edit uses dual conditioning: clean VAE source tokens for appearance plus Qwen3-VL image-grounded instruction conditioning for semantics.",
        "The identity-edit LoRA is model-only and is applied before Krea2EditModelPatch; the native Qwen3-VL conditioning stack is never LoRA-patched.",
    ]

    workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_name, "upload": "image"}}
    source_ref: list[Any] = [str(next_id), 0]
    original_source_ref = list(source_ref)
    next_id += 1
    mask_ref: list[Any] | None = None
    outpaint_payload: dict[str, Any] | None = None
    scale_meta: dict[str, Any] | None = None

    if mode == "outpaint":
        outpaint_payload = normalize_outpaint_payload(params, default_width=width, default_height=height)
        padding = outpaint_payload["padding"]
        left = int(padding.get("left", 0) or 0)
        top = int(padding.get("top", 0) or 0)
        right = int(padding.get("right", 0) or 0)
        bottom = int(padding.get("bottom", 0) or 0)
        # Identity Edit v1.2 learned outpaint through its centered `fit` geometry.
        # Do not inject ImagePadForOutpaint pixels as clean source tokens: blank
        # padding would become part of the appearance reference and is outside the
        # training recipe. Neo still uses the outpaint editor to resolve target size.
        next_id, source_ref, working_width, working_height, scale_meta = _insert_outpaint_source_scale_node(
            workflow, next_id, source_ref, outpaint_payload, fallback_width=width, fallback_height=height
        )
        width = max(64, int(working_width) + left + right)
        height = max(64, int(working_height) + top + bottom)
        asymmetric = left != right or top != bottom
        if asymmetric:
            warning = (
                "Krea 2 Identity Edit v1.2 centers the source reference inside the expanded target. "
                "Asymmetric outpaint padding controls target size but cannot side-anchor the clean reference tokens; "
                "use balanced padding when exact source placement matters."
            )
            if warning not in validation.warnings:
                validation.warnings.append(warning)
        route_notes.append("Identity Edit outpaint keeps the clean source unpadded and lets v1.2 `fit` geometry center it inside the expanded EmptySD3LatentImage target; Neo padding controls target size, not a diffusion mask.")

    if mode == "inpaint" and mask_name:
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
        route_notes.append("Identity Edit inpaint uses the trained instruction edit for generation and Neo's mask only as the final pixel-composite boundary, preventing unrelated regions from being committed.")
    else:
        grow = 0
        blur = 0
        inpaint_target = "masked"

    source_b_ref: list[Any] | None = None
    if source_b_name:
        workflow[str(next_id)] = {"class_type": "LoadImage", "inputs": {"image": source_b_name, "upload": "image"}}
        source_b_ref = [str(next_id), 0]
        next_id += 1
        route_notes.append("Two-reference mode follows training order: Image 1 is the scene/composition reference and Image 2 is the subject/identity reference.")

    workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_ref), "vae": ["3", 0]}}
    source_latent_ref = [str(next_id), 0]
    next_id += 1
    source_b_latent_ref: list[Any] | None = None
    if source_b_ref is not None:
        workflow[str(next_id)] = {"class_type": "VAEEncode", "inputs": {"pixels": list(source_b_ref), "vae": ["3", 0]}}
        source_b_latent_ref = [str(next_id), 0]
        next_id += 1

    workflow[str(next_id)] = {"class_type": "EmptySD3LatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
    target_latent_ref = [str(next_id), 0]
    next_id += 1

    workflow[str(next_id)] = {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {"model": ["1", 0], "lora_name": identity_lora, "strength_model": identity_lora_strength},
    }
    identity_lora_ref = [str(next_id), 0]
    identity_lora_node_id = str(next_id)
    next_id += 1

    patch_inputs: dict[str, Any] = {
        "model": list(identity_lora_ref),
        "source_latent": list(source_latent_ref),
        "ref_boost": ref_boost,
        "ref_boost_a": ref_boost_a,
        "fit_mode": fit_mode,
        "vae": ["3", 0],
        "source_image": list(source_ref),
        "target_latent": list(target_latent_ref),
    }
    if source_b_latent_ref is not None and source_b_ref is not None:
        patch_inputs["source_latent_b"] = list(source_b_latent_ref)
        patch_inputs["source_image_b"] = list(source_b_ref)
    workflow[str(next_id)] = {"class_type": "Krea2EditModelPatch", "inputs": patch_inputs}
    model_ref = [str(next_id), 0]
    patch_node_id = str(next_id)
    next_id += 1

    grounded_common: dict[str, Any] = {
        "clip": ["2", 0],
        "image": list(source_ref),
        "grounding_px": grounding_px,
        "system_prompt": system_prompt,
    }
    if source_b_ref is not None:
        grounded_common["image_b"] = list(source_b_ref)
    workflow["4"] = {"class_type": "Krea2EditGroundedEncode", "inputs": {**grounded_common, "prompt": effective_prompt}}
    workflow["5"] = {"class_type": "Krea2EditGroundedEncode", "inputs": {**grounded_common, "prompt": ""}}

    sampler_id = str(next_id)
    workflow[sampler_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": int(_param(params, "actual_seed", "seed", default=0)),
            "steps": steps,
            "cfg": cfg,
            "sampler_name": sampler if sampler != "provider_default" else KREA2_DEFAULTS.sampler,
            "scheduler": scheduler if scheduler != "provider_default" else KREA2_DEFAULTS.scheduler,
            "denoise": denoise,
            "model": list(model_ref),
            "positive": ["4", 0],
            "negative": ["5", 0],
            "latent_image": list(target_latent_ref),
        },
    }
    next_id += 1
    decode_id = str(next_id)
    workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
    output_ref: list[Any] = [decode_id, 0]
    next_id += 1

    if mode == "inpaint" and mask_ref is not None:
        composite_id = str(next_id)
        workflow[composite_id] = {
            "class_type": "ImageCompositeMasked",
            "inputs": {"destination": list(original_source_ref), "source": [decode_id, 0], "x": 0, "y": 0, "resize_source": True, "mask": list(mask_ref)},
        }
        output_ref = [composite_id, 0]
        next_id += 1

    workflow[str(next_id)] = {"class_type": "PreviewImage", "inputs": {"images": output_ref}}

    seed = int(_param(params, "actual_seed", "seed", default=0))
    requested_seed = int(_param(params, "requested_seed", "seed", default=seed))
    actual_params: dict[str, Any] = {
        **params,
        "seed": seed,
        "actual_seed": seed,
        "requested_seed": requested_seed,
        "workflow_type": route.workflow_type or f"image.{mode}.{'krea2_turbo' if turbo else 'krea2'}_{'gguf' if loader == 'gguf' else 'native'}_identity_edit",
        "krea2_variant": variant,
        "krea2_edit_engine": KREA2_EDIT_ENGINE_IDENTITY,
        "krea2_identity_edit_lora": identity_lora,
        "krea2_identity_edit_lora_strength": identity_lora_strength,
        "krea2_identity_edit_ref_boost": ref_boost,
        "krea2_identity_edit_ref_boost_a": ref_boost_a,
        "krea2_identity_edit_fit_mode": fit_mode,
        "krea2_identity_edit_grounding_px": grounding_px,
        "krea2_identity_edit_system_prompt": system_prompt,
        "qwen3vl_text_encoder": text_encoder,
        "text_encoder_1": text_encoder,
        "text_encoder_2": "",
        "vae": vae,
        "diffusion_model": "" if loader == "gguf" else model_name,
        "gguf_model": model_name if loader == "gguf" else "",
        "clip_type": KREA2_DEFAULTS.clip_type,
        "steps": steps,
        "cfg": cfg,
        "denoise": denoise,
        "prompt_conditioning_mode": str(conditioning.get("mode") or "raw"),
        "clamp": str(conditioning.get("mode") or "raw"),
        "source_image_name": source_name,
        "source_image_2_name": source_b_name,
        "reference_image_2_name": source_b_name,
        "_neo_sampler_node_id": sampler_id,
        "_neo_krea2_image_mode_adapter": True,
        "_neo_krea2_identity_edit": True,
        "_neo_krea2_identity_edit_dual_conditioning": True,
        "_neo_krea2_identity_edit_two_reference": two_reference,
        "_neo_krea2_identity_edit_identity_lora_node_id": identity_lora_node_id,
        "_neo_krea2_identity_edit_patch_node_id": patch_node_id,
        "_neo_krea2_identity_edit_target_latent": list(target_latent_ref),
        "_neo_krea2_identity_edit_negative_policy": "empty grounded negative with the same source image(s), matching training unconditional conditioning",
        **({"masked_edit_engine": "krea2_identity_edit", "inpaint_engine": "native", "masked_edit_engine_state": "krea2_identity_edit_family_graph"} if mode in {"inpaint", "outpaint"} else {}),
        "krea2_profile": {
            "family": "krea2_turbo" if turbo else "krea2",
            "variant": variant,
            "loader": loader,
            "compiler": compiler_id,
            "architecture": "krea2_identity_edit_dual_conditioning_v1_2",
            "compatibility": compatibility.as_dict(),
            "clip_loader": "CLIPLoader(type=krea2)",
            "text_encoder_policy": "Qwen3-VL-4B native/safetensors; image-grounded 12-layer feature aggregation",
            "vae_policy": "Qwen Image VAE; pixel-space fit path wired to Krea2EditModelPatch",
            "gguf_policy": "GGUF may quantize the Krea diffusion transformer only; Identity Edit LoRA and Qwen3-VL remain native/safetensors",
            "image_mode_policy": "ComfyUI-Krea2Edit v1.2+ instruction edit engine with source appearance tokens + image-grounded Qwen3-VL semantics",
            "node_classes": list(KREA2_IDENTITY_EDIT_NODE_CLASSES),
            "node_repo": KREA2_IDENTITY_EDIT_NODE_REPO,
            "recommended_lora": KREA2_IDENTITY_EDIT_RECOMMENDED_LORA,
        },
        "_neo_effective_krea2_native_route": loader != "gguf",
        "_neo_effective_krea2_gguf_route": loader == "gguf",
    }
    if mode == "inpaint":
        actual_params.update({
            "mask_image_name": mask_name,
            "mask_grow": grow,
            "mask_blur": blur,
            "inpaint_target": inpaint_target,
            "_neo_krea2_identity_inpaint_final_mask_composite": True,
        })
    if mode == "outpaint" and outpaint_payload is not None:
        actual_params.update({
            "outpaint_payload": outpaint_payload,
            "_neo_outpaint_contract": outpaint_payload,
            "krea2_outpaint_source_scale_node": scale_meta or {},
            "krea2_outpaint_effective_size": {"width": width, "height": height},
            "_neo_krea2_identity_outpaint_instruction_canvas": True,
            "_neo_krea2_identity_outpaint_alignment": "centered_reference_fit",
            "_neo_krea2_identity_outpaint_padding_role": "target_size_only",
        })

    actual_params["_neo_lora_patch_profile"] = build_lora_patch_profile(
        route={**route.as_dict(), "workflow_mode": mode, "route_state": "available" if route.status == "available" else route.status},
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
        notes=[
            "Global Krea LoRAs are rewired upstream of the dedicated Identity Edit LoRA so all model-only patches exist before Krea2EditModelPatch wraps the diffusion forward.",
        ],
    )

    publish_adetailer_route_contract(
        actual_params=actual_params,
        workflow=workflow,
        route=route,
        image_ref=output_ref,
        model_ref=model_ref,
        clip_ref=["2", 0],
        vae_ref=["3", 0],
        positive_ref=["4", 0],
        negative_ref=["5", 0],
        sampler_node_id=sampler_id,
        source=compiler_id,
        compiler_id=compiler_id,
        model_sampling_state="identity_edit_patched",
        model_sampling_ref=model_ref,
        model_sampling_nodes=[identity_lora_node_id, patch_node_id],
        notes=["Krea 2 Identity Edit publishes the patched model plus grounded positive/negative conditioning as its detail-repair anchors."],
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
            "compile_route": {**route.as_dict(), "compiler_id": compiler_id, "workflow_engine": KREA2_EDIT_ENGINE_IDENTITY},
            "capabilities": capabilities,
            "backend_capabilities": backend_capabilities or {},
            "phase_notes": [
                "Krea 2 Identity Edit is an opt-in engine layered onto Neo's existing Krea 2 RAW/Turbo routes; the legacy latent adapters remain the default.",
                "SafeTensor and GGUF routes share the same edit graph after the base diffusion loader. GGUF does not quantize Qwen3-VL, VAE, or the Identity Edit LoRA.",
                "Krea2EditModelPatch receives VAE latent source(s), the blur-proof pixel path, and the same EmptySD3LatentImage target used by KSampler so source VAE pre-encoding happens before sampling.",
                "Krea2EditGroundedEncode is used for both positive instruction conditioning and an empty-prompt grounded negative, matching the training recipe.",
                *route_notes,
            ],
            "prompt_conditioning": conditioning,
        },
    )


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

    The existing img2img/inpaint/outpaint adapters remain the default. Image modes
    may explicitly opt into the community Krea 2 Identity Edit v1.2 engine, which
    uses Krea2EditModelPatch + Krea2EditGroundedEncode with a selected model-only
    Identity Edit LoRA. The opt-in graph is shared by native and GGUF diffusion
    loading; GGUF remains transformer-only.
    """

    raw_params = job.params or {}
    mode = str(route.mode or job.mode or "txt2img")
    params = normalize_inpaint_target_aliases(raw_params) if mode == "inpaint" else raw_params
    loader = str(route.loader or job.loader or "diffusion_model")
    defaults = KREA2_DEFAULTS
    is_gguf = loader == "gguf"
    image_mode = mode in {"img2img", "edit", "inpaint", "outpaint"}
    edit_engine = normalize_krea2_edit_engine(_param(params, "krea2_edit_engine", "edit_engine", "image_edit_engine", default="native")) if image_mode else "native"
    identity_edit = bool(image_mode and edit_engine == KREA2_EDIT_ENGINE_IDENTITY)
    if identity_edit and mode in {"inpaint", "outpaint"}:
        masked_engine = str(_param(params, "masked_edit_engine", "inpaint_engine", default="native") or "native").strip().lower().replace("-", "_")
        if masked_engine in {"lanpaint", "lan_paint"}:
            validation.errors.append("Krea 2 Identity Edit cannot be stacked with the LanPaint masked engine. Select Native masked editing or disable Identity Edit.")
            validation.ok = False

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
    # Parameter Truth: RAW/Turbo defaults apply only when a value is absent.
    # Explicit user steps/CFG are passed through unchanged for experimentation.
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

    if identity_edit:
        identity_params = {**params, "seed": seed, "actual_seed": seed, "requested_seed": requested_seed}
        return _compile_krea2_identity_edit(
            provider_id=provider_id,
            base_url=base_url,
            job=job,
            validation=validation,
            route=route,
            capabilities=capabilities,
            backend_capabilities=backend_capabilities,
            params=identity_params,
            mode=mode,
            loader=loader,
            model_node=model_node,
            model_name=model_name,
            compiler_id=compiler_id,
            text_encoder=text_encoder,
            vae=vae,
            variant=variant,
            turbo=turbo,
            compatibility=compatibility,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            scheduler=scheduler,
            denoise=denoise,
            batch_count=batch_count,
            clip_device=clip_device,
            conditioning=conditioning,
            effective_prompt=effective_prompt,
        )

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
        "krea2_edit_engine": edit_engine,
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

    if image_mode and batch_count > 1:
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
        notes=["Krea 2 LoRAs use a model-only branch so the native Qwen3-VL conditioning stack stays untouched."],
    )

    publish_adetailer_route_contract(
        actual_params=actual_params,
        workflow=workflow,
        route=route,
        image_ref=output_ref,
        model_ref=model_ref,
        clip_ref=["2", 0],
        vae_ref=["3", 0],
        positive_ref=["4", 0],
        negative_ref=["5", 0],
        sampler_node_id=sampler_id,
        source=compiler_id,
        compiler_id=compiler_id,
        model_sampling_state="patched" if mode in {"inpaint", "outpaint"} else "passthrough",
        model_sampling_ref=model_ref,
        model_sampling_nodes=[str(model_ref[0])] if mode in {"inpaint", "outpaint"} and str(model_ref[0]) != "1" else [],
        notes=["Krea 2 publishes its final image, active model, Qwen3-VL conditioning, VAE, and sampler anchors for isolated detail repair."],
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
