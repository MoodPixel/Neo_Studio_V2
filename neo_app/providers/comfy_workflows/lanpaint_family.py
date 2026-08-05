from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.lanpaint_capabilities import PHASE8_STATE, evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import (
    PHASE13_STATE,
    PHASE14_STATE,
    PHASE18_STATE,
    PHASE20_STATE,
    PHASE21_STATE,
    PHASE22_STATE,
    adapter_asset_candidates,
    adapter_snapshot,
    resolve_lanpaint_family_adapter,
)
from neo_app.image.lanpaint_route_contract import ROUTE_FAMILY_ID, normalize_lanpaint_route_contract
from neo_app.image.lanpaint_ui_state import PHASE7_STATE, normalize_lanpaint_ui_state
from neo_app.image.lanpaint_replay import PHASE11_STATE, refresh_lanpaint_replay_contract, validate_lanpaint_replay_request
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile

from .lanpaint import (
    COMPILER_ID,
    PHASE5_VARIANT,
    WORKFLOW_TYPE,
    _active_lora_rows,
    _base_graph_lora_rows,
    _mapping,
    _mask_image_name,
    _optional_input,
    _param,
    _phase5_blocker_messages,
    _source_image_name,
    _verify_selected_asset,
    build_lanpaint_comfy_compile_plan,
    compile_lanpaint_krea2_turbo_inpaint,
)

PHASE10_STATE = "qwen_zimage_family_onboarding"
PHASE20_Z_IMAGE_FAMILIES = ("z_image", "z_image_turbo")
SUPPORTED_PHASE10_FAMILIES = ("qwen_image", "qwen_image_edit_2509", "qwen_image_edit_2511", "z_image", "z_image_turbo")
SUPPORTED_PHASE10_LOADERS = ("diffusion_model", "gguf")


def _phase20_route_variant(family: str) -> str:
    if family == "z_image":
        return "z_image_lanpaint_base_crop_stitch_v2"
    if family == "z_image_turbo":
        return "z_image_turbo_lanpaint_crop_stitch_v2"
    return "crop_stitch_aura_v1"


def _route_request(provider_id: str, family: str, loader: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "provider_id": provider_id,
            "family": family,
            "loader": loader,
            "mode": "inpaint",
            "engine": "lanpaint",
            "variant": _phase20_route_variant(family),
        },
        "crop_policy": {
            "padding_px": _param(params, "lanpaint_crop_padding", "crop_padding"),
            "processing_size": {
                "width": _param(params, "lanpaint_processing_width"),
                "height": _param(params, "lanpaint_processing_height"),
            },
            "resize_method": _param(params, "lanpaint_resize_method"),
        },
        "mask_policy": {
            "sampling": {
                "expand_px": _param(params, "lanpaint_sampling_mask_expand"),
                "blur_radius": _param(params, "lanpaint_sampling_mask_blur"),
            },
            "stitch": {
                "expand_px": _param(params, "lanpaint_stitch_mask_expand"),
                "blur_radius": _param(params, "lanpaint_stitch_mask_blur"),
            },
        },
        "sampler_policy": {
            "steps": _param(params, "lanpaint_steps"),
            "cfg": _param(params, "lanpaint_cfg"),
            "sampler_name": _param(params, "lanpaint_sampler"),
            "scheduler": _param(params, "lanpaint_scheduler"),
            "denoise": _param(params, "lanpaint_denoise"),
            "lanpaint_thinking_steps": _param(params, "lanpaint_thinking_steps"),
            "prompt_mode": _param(params, "lanpaint_prompt_mode"),
        },
        "stitch_policy": {"resize_method": _param(params, "lanpaint_stitch_resize_method")},
    }


def _node_inputs(backend: Mapping[str, Any], node_class: str) -> set[str]:
    payload = _mapping(_mapping(backend.get("object_info_node_inputs")).get(node_class))
    values: set[str] = set()
    for key in ("required", "optional", "all"):
        raw = payload.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.update(str(item) for item in raw)
    return values


def _select_loader_node(plan: Mapping[str, Any], role: str) -> str:
    return str(_mapping(_mapping(plan.get("external_bindings")).get(role)).get("node_class") or "")


def _signature_gate(
    validation: ProviderValidationResult,
    backend: Mapping[str, Any],
    required: Mapping[str, tuple[str, ...]],
) -> list[dict[str, Any]]:
    node_map = _mapping(backend.get("object_info_node_inputs"))
    mismatches: list[dict[str, Any]] = []
    for node_class, inputs in required.items():
        if node_class not in node_map:
            mismatches.append({"node_class": node_class, "state": "missing_node", "missing_inputs": list(inputs)})
            continue
        declared = _node_inputs(backend, node_class)
        missing = sorted(set(inputs) - declared)
        if missing:
            mismatches.append({"node_class": node_class, "state": "incompatible_signature", "missing_inputs": missing, "declared_inputs": sorted(declared)})
    for item in mismatches:
        message = (
            f"LanPaint Phase 10 requires Comfy node {item['node_class']}, but it is unavailable."
            if item["state"] == "missing_node"
            else f"LanPaint Phase 10 requires a compatible {item['node_class']} signature; missing inputs: {', '.join(item['missing_inputs'])}."
        )
        if message not in validation.errors:
            validation.errors.append(message)
    if mismatches:
        validation.ok = False
    return mismatches


def _model_loader_inputs(node_class: str, loader_policy: Mapping[str, Any], model_name: str) -> dict[str, Any]:
    keys = _mapping(loader_policy.get("input_keys"))
    key = str(keys.get(node_class) or ("gguf_name" if node_class == "LoaderGGUF" else "unet_name"))
    inputs = {key: model_name}
    for name, value in _mapping(loader_policy.get("default_inputs")).items():
        inputs[str(name)] = value
    return inputs



_QWEN_EDIT_FAMILIES = {"qwen_image_edit_2509", "qwen_image_edit_2511"}
_QWEN_EDIT_CONDITIONING_NODES = (
    "TextEncodeQwenImageEditPlus",
    "TextEncodeQwenImageEditPlus_lrzjason",
    "TextEncodeQwenImageEditPlusAdvance_lrzjason",
    "TextEncodeQwenImageEditPlusPro_lrzjason",
)


def _select_qwen_edit_conditioning_node(backend: Mapping[str, Any]) -> str:
    node_map = _mapping(backend.get("object_info_node_inputs"))
    return next((name for name in _QWEN_EDIT_CONDITIONING_NODES if name in node_map), "")


def _qwen_edit_conditioning_inputs(
    node_class: str,
    *,
    prompt: str,
    clip_ref: list[Any],
    vae_ref: list[Any],
    image_ref: list[Any],
    backend: Mapping[str, Any],
) -> dict[str, Any]:
    declared = _node_inputs(backend, node_class)
    inputs: dict[str, Any] = {}
    prompt_key = "prompt" if "prompt" in declared else ("text" if "text" in declared else "prompt")
    inputs[prompt_key] = prompt
    inputs["clip"] = clip_ref
    if "vae" in declared:
        inputs["vae"] = vae_ref
    image_key = next((key for key in ("image1", "image", "source_image") if key in declared), "image1")
    inputs[image_key] = image_ref
    return inputs

def compile_lanpaint_family_inpaint(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    """Compile the exact Phase 10 Qwen/Z-Image LanPaint route overlays.

    Krea 2 Turbo safetensors and GGUF are delegated to the parity-stabilized
    Differential Diffusion emitter so both loaders retain the approved graph shape.
    """

    family = str(route.family or job.family or "").strip()
    loader = str(route.loader or job.loader or "").strip()
    if family == "hidream":
        from .lanpaint_hidream import compile_lanpaint_hidream_i1_inpaint
        return compile_lanpaint_hidream_i1_inpaint(
            provider_id=provider_id,
            base_url=base_url,
            job=job,
            validation=validation,
            route=route,
            capabilities=capabilities,
            backend_capabilities=backend_capabilities,
        )

    if family in {"anima", "ideogram4"}:
        from .lanpaint_phase22 import compile_lanpaint_phase22_inpaint
        return compile_lanpaint_phase22_inpaint(
            provider_id=provider_id, base_url=base_url, job=job, validation=validation, route=route,
            capabilities=capabilities, backend_capabilities=backend_capabilities,
        )

    if family in {"flux2_dev", "flux2_klein"}:
        from .lanpaint_flux2 import compile_lanpaint_flux2_inpaint
        return compile_lanpaint_flux2_inpaint(
            provider_id=provider_id,
            base_url=base_url,
            job=job,
            validation=validation,
            route=route,
            capabilities=capabilities,
            backend_capabilities=backend_capabilities,
        )

    if family == "flux":
        from .lanpaint_flux import compile_lanpaint_flux1_inpaint
        return compile_lanpaint_flux1_inpaint(
            provider_id=provider_id,
            base_url=base_url,
            job=job,
            validation=validation,
            route=route,
            capabilities=capabilities,
            backend_capabilities=backend_capabilities,
        )

    if family in {"sdxl", "sd15", "sd35"}:
        from .lanpaint_sd import compile_lanpaint_sd_inpaint
        return compile_lanpaint_sd_inpaint(
            provider_id=provider_id,
            base_url=base_url,
            job=job,
            validation=validation,
            route=route,
            capabilities=capabilities,
            backend_capabilities=backend_capabilities,
        )

    if family == "krea2_turbo" and loader in {"diffusion_model", "gguf"}:
        return compile_lanpaint_krea2_turbo_inpaint(
            provider_id=provider_id,
            base_url=base_url,
            job=job,
            validation=validation,
            route=route,
            capabilities=capabilities,
            backend_capabilities=backend_capabilities,
        )

    params = dict(job.params or {})
    backend = dict(backend_capabilities or {})
    is_qwen_edit = family in _QWEN_EDIT_FAMILIES
    is_z_image_phase20 = family in PHASE20_Z_IMAGE_FAMILIES
    qwen_edit_node = _select_qwen_edit_conditioning_node(backend) if is_qwen_edit else ""
    if family not in SUPPORTED_PHASE10_FAMILIES or loader not in SUPPORTED_PHASE10_LOADERS:
        validation.errors.append(f"LanPaint family compiler has no binding for {family}+{loader}+inpaint.")
        validation.ok = False
    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    if not source_name:
        validation.errors.append("LanPaint inpaint requires Image 1 / a Comfy source image name after provider handoff.")
        validation.ok = False
    if not mask_name:
        validation.errors.append("LanPaint inpaint requires a Comfy mask image name after provider handoff.")
        validation.ok = False

    for replay_error in validate_lanpaint_replay_request(
        params, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint"
    ):
        validation.errors.append(replay_error)
        validation.ok = False

    ui_state = normalize_lanpaint_ui_state(
        params,
        provider_id=provider_id,
        family=family,
        loader=loader,
        mode="inpaint",
        engine="lanpaint",
    )
    params.update({key: value for key, value in dict(ui_state.get("flat_params") or {}).items() if value not in (None, "")})

    route_contract, contract_issues = normalize_lanpaint_route_contract(_route_request(provider_id, family, loader, params))
    for issue in contract_issues:
        message = str(issue.get("message") or "LanPaint route contract validation failed.")
        if issue.get("level") == "error":
            validation.errors.append(message)
            validation.ok = False
        else:
            validation.warnings.append(message)
    adapter = resolve_lanpaint_family_adapter(route_contract)
    adapter_policy = _mapping(adapter.get("policy"))
    adapter_binding = _mapping(adapter.get("binding"))
    adapter_identity = _mapping(adapter.get("identity"))
    adapter_stabilization = _mapping(adapter.get("stabilization"))
    if adapter_policy.get("resolution_state") != "resolved_policy_only" or not adapter_policy.get("complete"):
        validation.errors.append(f"LanPaint Phase 13 requires a complete family adapter policy for {family}.")
        validation.ok = False
    if not adapter_binding.get("selectable"):
        validation.errors.append(f"LanPaint adapter {adapter_binding.get('state') or 'unresolved'} has no exact compiler binding for {family}+{loader}.")
        validation.ok = False

    active_loras = _active_lora_rows(job.extensions, params)
    base_graph_loras = _base_graph_lora_rows(active_loras)
    plan = build_lanpaint_comfy_compile_plan(route_contract, backend, lora_stack_enabled=bool(base_graph_loras))
    for message in _phase5_blocker_messages(plan):
        if message not in validation.errors:
            validation.errors.append(message)
            validation.ok = False

    display_name = str(_mapping(adapter.get("identity")).get("family") or family).replace("_", " ").title()
    asset_candidates = adapter_asset_candidates(adapter, params, job_model=job.model)
    model_name = str(require_explicit_asset_selection(validation, f"{display_name} diffusion model", *asset_candidates.get("model", [])))
    text_encoder = str(require_explicit_asset_selection(validation, f"{display_name} text encoder", *asset_candidates.get("text_encoder", [])))
    vae = str(require_explicit_asset_selection(validation, f"{display_name} VAE / AE", *asset_candidates.get("vae", [])))
    mmproj = ""
    if is_qwen_edit and loader == "gguf":
        mmproj = str(require_explicit_asset_selection(
            validation,
            f"{display_name} Qwen2.5-VL MMProj",
            _param(params, "qwen_mmproj", "gguf_mmproj", "mmproj", "mmproj_name", "vision_mmproj", "qwen_image_edit_mmproj"),
        ))

    loaders = _mapping(adapter.get("loaders"))
    loader_policy = _mapping(loaders.get("model"))
    text_policy = _mapping(loaders.get("text_encoder"))
    vae_policy = _mapping(loaders.get("vae"))
    conditioning_policy = _mapping(adapter.get("conditioning"))
    latent_policy = _mapping(adapter.get("latent"))
    spatial = _mapping(adapter.get("spatial"))
    crop = _mapping(spatial.get("crop"))
    processing = _mapping(crop.get("processing_size"))
    masks = _mapping(spatial.get("mask"))
    sample_mask = _mapping(masks.get("sampling"))
    stitch_mask = _mapping(masks.get("stitch"))
    sampler_policy = _mapping(_mapping(adapter.get("sampler")).get("defaults"))
    stitch_policy = _mapping(spatial.get("stitch"))
    lora_policy = _mapping(adapter.get("lora"))

    model_loader = _select_loader_node(plan, "family_model")
    clip_loader = _select_loader_node(plan, "text_encoder")
    vae_loader = _select_loader_node(plan, "vae") or str((vae_policy.get("accepted_node_classes") or ["VAELoader"])[0])
    if not model_loader or not clip_loader or not vae_loader:
        validation.errors.append("LanPaint family compiler could not resolve model, text-encoder and VAE loader nodes from live capabilities.")
        validation.ok = False

    selection_target = str(params.get("inpaint_selection_target") or params.get("inpaint_mask_target") or "masked_area").strip().lower()
    invert_mask = selection_target in {"not_masked", "not_masked_area", "inverse", "unmasked", "outside_mask"}
    capability_report = evaluate_lanpaint_route_capabilities(
        backend,
        provider_id=provider_id,
        family=family,
        loader=loader,
        mode="inpaint",
        engine="lanpaint",
        selected_assets={"model": model_name, "text_encoder": text_encoder, "vae": vae, **({"mmproj": mmproj} if mmproj else {})},
        require_invert_mask=invert_mask,
        require_model_only_lora=False,
        require_model_clip_lora=bool(base_graph_loras),
    )
    for blocker in capability_report.get("blockers", []):
        message = f"LanPaint capability gate [{blocker.get('code') or 'blocked'}]: {blocker.get('message') or 'Route requirements are unavailable.'}"
        if message not in validation.errors:
            validation.errors.append(message)
            validation.ok = False
    for warning in capability_report.get("warnings", []):
        message = f"LanPaint capability notice [{warning.get('code') or 'notice'}]: {warning.get('message') or ''}".strip()
        if message not in validation.warnings:
            validation.warnings.append(message)

    selected_assets = [
        _verify_selected_asset(validation, backend, loader_id=loader, role_id=str(loader_policy.get("role_id") or ("gguf_unet" if loader == "gguf" else "diffusion_model")), label=f"{display_name} model", selected=model_name),
        _verify_selected_asset(validation, backend, loader_id=loader, role_id=str(text_policy.get("role_id") or ("gguf_text_encoder_primary" if loader == "gguf" else "text_encoder_primary")), label=f"{display_name} text encoder", selected=text_encoder),
        _verify_selected_asset(validation, backend, loader_id=loader, role_id=str(vae_policy.get("role_id") or "vae_or_ae"), label=f"{display_name} VAE / AE", selected=vae),
    ]
    if is_qwen_edit and loader == "gguf":
        selected_assets.append({"role_id": "qwen_image_edit_mmproj", "selected": mmproj, "loader_id": loader, "portable_identity_only": True, "verification_state": "explicit_selection_required"})

    required_signatures: dict[str, tuple[str, ...]] = {
        "LoadImage": ("image",),
        "ImageToMask": ("image", "channel"),
        "CropByMask": ("image", "mask", "padding"),
        "ImageResizeKJv2": ("image", "width", "height", "upscale_method", "keep_proportion", "pad_color", "crop_position", "divisible_by"),
        "GrowMaskWithBlur": ("mask", "expand", "blur_radius"),
        "VAEEncode": ("pixels", "vae"),
        "SetLatentNoiseMask": ("samples", "mask"),
        "ModelSamplingAuraFlow": ("model", "shift"),
        **({} if is_qwen_edit else {"CLIPTextEncode": ("clip", "text")}),
        "LanPaint_KSampler": ("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps", "LanPaint_PromptMode", "Inpainting_mode"),
        "VAEDecode": ("samples", "vae"),
        "ImageCompositeMasked": ("destination", "source", "mask", "x", "y", "resize_source"),
        "PreviewImage": ("images",),
    }
    if is_qwen_edit:
        if not qwen_edit_node:
            validation.errors.append("Qwen Image Edit LanPaint requires TextEncodeQwenImageEditPlus or a verified compatible node variant.")
            validation.ok = False
        else:
            declared = _node_inputs(backend, qwen_edit_node)
            required_edit_inputs = ["clip"]
            required_edit_inputs.append("prompt" if "prompt" in declared else "text")
            required_edit_inputs.append(next((key for key in ("image1", "image", "source_image") if key in declared), "image1"))
            required_signatures[qwen_edit_node] = tuple(required_edit_inputs)
    if model_loader:
        required_signatures[model_loader] = tuple(_model_loader_inputs(model_loader, loader_policy, model_name))
    if clip_loader:
        required_signatures[clip_loader] = ("clip_name", "type")
    if vae_loader:
        required_signatures[vae_loader] = ("vae_name",)
    negative_policy = _mapping(conditioning_policy.get("negative"))
    if negative_policy.get("negative_conditioning_policy") == "zero_out_positive_conditioning":
        required_signatures["ConditioningZeroOut"] = ("conditioning",)
    if invert_mask:
        required_signatures["InvertMask"] = ("mask",)
    signature_mismatches = _signature_gate(validation, backend, required_signatures)

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1) or -1)
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed) or requested_seed)
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647
    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    effective_prompt = conditioning.get("effective_positive") or job.prompt or ""
    effective_negative = conditioning.get("effective_negative") or job.negative_prompt or ""

    padding = int(crop.get("padding_px") or 152)
    process_width = int(processing.get("width") or 768)
    process_height = int(processing.get("height") or 768)
    resize_method = str(crop.get("resize_method") or "lanczos")
    restore_method = str(stitch_policy.get("resize_method") or resize_method)
    sample_expand = int(sample_mask.get("expand_px") or 0)
    sample_blur = float(sample_mask.get("blur_radius") or 0.0)
    stitch_expand = int(stitch_mask.get("expand_px") or 0)
    stitch_blur = float(stitch_mask.get("blur_radius") or 0.0)
    steps = int(sampler_policy.get("steps") or 20)
    cfg = float(sampler_policy.get("cfg") if sampler_policy.get("cfg") is not None else 4.0)
    sampler_name = str(sampler_policy.get("sampler_name") or "euler")
    scheduler = str(sampler_policy.get("scheduler") or "simple")
    denoise = float(sampler_policy.get("denoise") if sampler_policy.get("denoise") is not None else 1.0)
    thinking_steps = int(sampler_policy.get("lanpaint_thinking_steps") or 5)
    prompt_mode = "Prompt First" if str(sampler_policy.get("prompt_mode") or "image_first") == "prompt_first" else "Image First"
    stability_policy = _mapping(adapter.get("stability_policy"))
    family_variant = _mapping(adapter.get("family_variant"))
    if family == "z_image" and thinking_steps > int(stability_policy.get("maximum_default_thinking_steps") or 3):
        validation.warnings.append(
            "Z-Image Base LanPaint is using more than the cautious three thinking iterations; upstream guidance warns that larger iterative updates can diverge."
        )
    aura_shift = float(latent_policy.get("aura_shift") if latent_policy.get("aura_shift") is not None else 3.0)
    clip_type = str(text_policy.get("clip_type") or "")

    workflow: dict[str, Any] = {}
    node_roles: dict[str, str] = {}

    def add(node_id: int, class_type: str, inputs: dict[str, Any], role: str) -> None:
        workflow[str(node_id)] = {"class_type": class_type, "inputs": inputs}
        node_roles[str(node_id)] = role

    sampler_id = 0
    model_loader_id = 0
    clip_loader_id = 0
    if validation.ok:
        add(1, "LoadImage", {"image": source_name}, "source_image")
        add(2, "LoadImage", {"image": mask_name}, "mask_image")
        add(3, "ImageToMask", {"image": ["2", 0], "channel": "red"}, "mask_image_to_mask")
        mask_ref: list[Any] = ["3", 0]
        next_id = 4
        if invert_mask:
            add(next_id, "InvertMask", {"mask": mask_ref}, "mask_target_inversion")
            mask_ref = [str(next_id), 0]
            next_id += 1
        crop_id = next_id
        add(crop_id, "CropByMask", {"image": ["1", 0], "mask": mask_ref, "padding": padding}, "crop_context")
        next_id += 1
        resize_inputs: dict[str, Any] = {
            "image": [str(crop_id), 0], "width": process_width, "height": process_height,
            "upscale_method": resize_method, "keep_proportion": "resize", "pad_color": "0, 0, 0",
            "crop_position": "center", "divisible_by": 2, "mask": [str(crop_id), 1],
        }
        _optional_input(resize_inputs, backend, "ImageResizeKJv2", "device", "cpu")
        resize_id = next_id
        add(resize_id, "ImageResizeKJv2", resize_inputs, "processing_resize")
        next_id += 1
        grow_inputs: dict[str, Any] = {
            "mask": [str(resize_id), 3], "expand": sample_expand, "incremental_expandrate": 0.0,
            "tapered_corners": True, "flip_input": False, "blur_radius": sample_blur,
            "lerp_alpha": 1.0, "decay_factor": 1.0,
        }
        _optional_input(grow_inputs, backend, "GrowMaskWithBlur", "fill_holes", bool(sample_mask.get("fill_holes", False)))
        sample_mask_id = next_id
        add(sample_mask_id, "GrowMaskWithBlur", grow_inputs, "sampling_mask_refine")
        next_id += 1
        vae_loader_id = next_id
        add(vae_loader_id, vae_loader, {"vae_name": vae}, "family_vae")
        next_id += 1
        encode_id = next_id
        add(encode_id, "VAEEncode", {"pixels": [str(resize_id), 0], "vae": [str(vae_loader_id), 0]}, "latent_encode")
        next_id += 1
        noise_mask_id = next_id
        add(noise_mask_id, "SetLatentNoiseMask", {"samples": [str(encode_id), 0], "mask": [str(sample_mask_id), 0]}, "latent_noise_mask")
        next_id += 1
        model_loader_id = next_id
        add(model_loader_id, model_loader, _model_loader_inputs(model_loader, loader_policy, model_name), "family_model_loader")
        next_id += 1
        clip_loader_id = next_id
        clip_inputs = {"clip_name": text_encoder, "type": clip_type}
        _optional_input(clip_inputs, backend, clip_loader, "device", str(text_policy.get("default_device") or "default"))
        add(clip_loader_id, clip_loader, clip_inputs, "family_text_encoder")
        if is_qwen_edit and loader == "gguf" and mmproj:
            workflow[str(clip_loader_id)]["_meta"] = {
                "neo_mmproj_sidecar": mmproj,
                "neo_mmproj_policy": "required_explicit_sidecar_for_qwen_edit_image_conditioning",
            }
        next_id += 1
        positive_id = next_id
        if is_qwen_edit:
            add(positive_id, qwen_edit_node, _qwen_edit_conditioning_inputs(
                qwen_edit_node, prompt=effective_prompt, clip_ref=[str(clip_loader_id), 0],
                vae_ref=[str(vae_loader_id), 0], image_ref=[str(resize_id), 0], backend=backend,
            ), "positive_edit_conditioning")
        else:
            add(positive_id, "CLIPTextEncode", {"text": effective_prompt, "clip": [str(clip_loader_id), 0]}, "positive_conditioning")
        next_id += 1
        negative_id = next_id
        if negative_policy.get("negative_conditioning_policy") == "zero_out_positive_conditioning":
            add(negative_id, "ConditioningZeroOut", {"conditioning": [str(positive_id), 0]}, "negative_conditioning")
        elif is_qwen_edit:
            add(negative_id, qwen_edit_node, _qwen_edit_conditioning_inputs(
                qwen_edit_node, prompt=effective_negative, clip_ref=[str(clip_loader_id), 0],
                vae_ref=[str(vae_loader_id), 0], image_ref=[str(resize_id), 0], backend=backend,
            ), "negative_edit_conditioning")
        else:
            add(negative_id, "CLIPTextEncode", {"text": effective_negative, "clip": [str(clip_loader_id), 0]}, "negative_conditioning")
        next_id += 1
        aura_id = next_id
        add(aura_id, "ModelSamplingAuraFlow", {"model": [str(model_loader_id), 0], "shift": aura_shift}, "family_model_transform")
        next_id += 1
        sampler_id = next_id
        add(sampler_id, "LanPaint_KSampler", {
            "model": [str(aura_id), 0], "seed": seed, "steps": steps, "cfg": cfg,
            "sampler_name": sampler_name, "scheduler": scheduler,
            "positive": [str(positive_id), 0], "negative": [str(negative_id), 0],
            "latent_image": [str(noise_mask_id), 0], "denoise": denoise,
            "LanPaint_NumSteps": thinking_steps, "LanPaint_PromptMode": prompt_mode,
            "LanPaint_Info": "LanPaint KSampler.", "Inpainting_mode": "🖼️ Image Inpainting",
        }, "lanpaint_sample")
        next_id += 1
        decode_id = next_id
        add(decode_id, "VAEDecode", {"samples": [str(sampler_id), 0], "vae": [str(vae_loader_id), 0]}, "latent_decode")
        next_id += 1
        restore_inputs: dict[str, Any] = {
            "image": [str(decode_id), 0], "width": [str(crop_id), 4], "height": [str(crop_id), 5],
            "upscale_method": restore_method, "keep_proportion": "stretch", "pad_color": "0, 0, 0",
            "crop_position": "center", "divisible_by": 2, "mask": [str(sample_mask_id), 0],
        }
        _optional_input(restore_inputs, backend, "ImageResizeKJv2", "device", "cpu")
        restore_id = next_id
        add(restore_id, "ImageResizeKJv2", restore_inputs, "restore_crop_size")
        next_id += 1
        stitch_inputs: dict[str, Any] = {
            "mask": [str(restore_id), 3], "expand": stitch_expand, "incremental_expandrate": 0.0,
            "tapered_corners": True, "flip_input": False, "blur_radius": stitch_blur,
            "lerp_alpha": 1.0, "decay_factor": 1.0,
        }
        _optional_input(stitch_inputs, backend, "GrowMaskWithBlur", "fill_holes", bool(stitch_mask.get("fill_holes", False)))
        stitch_id = next_id
        add(stitch_id, "GrowMaskWithBlur", stitch_inputs, "stitch_mask_refine")
        next_id += 1
        composite_id = next_id
        add(composite_id, "ImageCompositeMasked", {
            "destination": ["1", 0], "source": [str(restore_id), 0],
            "x": [str(crop_id), 2], "y": [str(crop_id), 3], "resize_source": False,
            "mask": [str(stitch_id), 0],
        }, "stitch_composite")
        next_id += 1
        add(next_id, "PreviewImage", {"images": [str(composite_id), 0]}, "output_handoff")

    route_key = f"{family}:{loader}:inpaint:lanpaint"
    lora_route = {
        "backend": provider_id, "provider_id": provider_id, "family": family, "loader": loader,
        "workflow_mode": "inpaint", "mode": "inpaint", "engine": "lanpaint",
        "route_key": route_key, "route_state": "experimental_available",
    }
    lora_patch_profile = build_lora_patch_profile(
        route=lora_route,
        model_ref=[str(model_loader_id), 0] if validation.ok else None,
        clip_ref=[str(clip_loader_id), 0] if validation.ok else None,
        sampler_node_id=str(sampler_id or ""), sampler_model_input="model",
        loader_node_class=str(lora_policy.get("loader_node_class") or "LoraLoader"),
        requires_model=True, requires_clip=True,
        source=("neo_app.providers.comfy_workflows.lanpaint_family.phase18" if is_qwen_edit else ("neo_app.providers.comfy_workflows.lanpaint_family.phase20" if is_z_image_phase20 else "neo_app.providers.comfy_workflows.lanpaint_family.phase10")),
        strategy="lora_loader_model_clip_consumer_rewire",
        patch_model_consumers=True, patch_clip_consumers=True, validated=False,
        notes=[
            (f"Phase 18 versioned Qwen Edit route lock: {family}+{loader}+inpaint+lanpaint only." if is_qwen_edit else (f"Phase 20 Z-Image LanPaint route lock: {family}+{loader}+inpaint+lanpaint only." if is_z_image_phase20 else f"Phase 14 stabilized route lock: {family}+{loader}+inpaint+lanpaint only.")),
            "Rewire model before ModelSamplingAuraFlow and CLIP before positive/negative conditioning.",
            "Physical Comfy validation remains required before available promotion.",
        ],
    )

    ui_state = deepcopy(ui_state)
    ui_state["capability"] = deepcopy(capability_report)
    ui_state["route"]["route_state"] = capability_report.get("status")
    ui_state["route"]["selectable"] = bool(capability_report.get("selectable"))
    ui_state["route"]["capability_checked"] = bool(capability_report.get("discovery", {}).get("checked"))
    ui_state["route"]["capability_fingerprint"] = capability_report.get("capability_fingerprint")
    ui_state["validation"]["capability_ok"] = bool(capability_report.get("executable"))
    fingerprint_payload = deepcopy(ui_state)
    fingerprint_payload.pop("state_fingerprint", None)
    ui_state["state_fingerprint"] = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    controls = {
        "crop_padding": padding,
        "processing_size": {"width": process_width, "height": process_height},
        "resize_method": resize_method,
        "restore_resize_method": restore_method,
        "sampling_mask": {"expand": sample_expand, "blur": sample_blur},
        "stitch_mask": {"expand": stitch_expand, "blur": stitch_blur},
        "steps": steps, "cfg": cfg, "sampler": sampler_name, "scheduler": scheduler,
        "denoise": denoise, "thinking_steps": thinking_steps, "prompt_mode": prompt_mode,
        "aura_shift": aura_shift,
        "family_variant": str(family_variant.get("id") or "standard"),
        "stability_profile": str(stability_policy.get("profile_id") or ""),
    }
    actual_params = {
        **params,
        "inpaint_engine": "lanpaint", "workflow_type": WORKFLOW_TYPE,
        "seed": seed, "actual_seed": seed, "requested_seed": requested_seed,
        "source_image_name": source_name, "mask_image_name": mask_name,
        "diffusion_model": model_name if loader == "diffusion_model" else "",
        "gguf_model": model_name if loader == "gguf" else "",
        "text_encoder_1": text_encoder, "vae": vae, "clip_type": clip_type,
        "qwen_mmproj": mmproj,
        "steps": steps, "cfg": cfg, "denoise": denoise, "sampler": sampler_name,
        "scheduler": scheduler, "aura_shift": aura_shift,
        "prompt_conditioning_mode": conditioning_mode, "clamp": conditioning_mode,
        "lanpaint_route": {
            "route_family_id": ROUTE_FAMILY_ID, "route_key": route_key,
            "engine": "lanpaint", "family": family, "loader": loader,
            "variant": str(adapter_identity.get("variant") or "crop_stitch_aura_v1"),
            "policy_id": adapter_policy.get("policy_id"),
            "compiler_id": COMPILER_ID, "graph_state": PHASE20_STATE if is_z_image_phase20 else (PHASE18_STATE if is_qwen_edit else PHASE14_STATE),
        },
        "lanpaint_controls": controls,
        "lanpaint_ui_state": ui_state,
        "lanpaint_ui_state_fingerprint": ui_state.get("state_fingerprint"),
        "_neo_lanpaint_phase7_ui_state": PHASE7_STATE,
        "_neo_lanpaint_phase8_capability_state": PHASE8_STATE,
        "_neo_lanpaint_phase10_state": PHASE10_STATE,
        "_neo_lanpaint_phase11_state": PHASE11_STATE,
        "lanpaint_capability_report": capability_report,
        "lanpaint_capability_fingerprint": capability_report.get("capability_fingerprint"),
        "lanpaint_contract_fingerprint": route_contract.get("contract_fingerprint"),
        "lanpaint_compile_plan_fingerprint": plan.get("plan_fingerprint"),
        "lanpaint_family_adapter": adapter_snapshot(adapter),
        "lanpaint_family_adapter_id": _mapping(adapter.get("identity")).get("adapter_id"),
        "lanpaint_family_adapter_fingerprint": adapter.get("adapter_fingerprint"),
        "_neo_lanpaint_phase13_state": PHASE13_STATE,
        "_neo_lanpaint_phase14_state": PHASE14_STATE,
        "_neo_lanpaint_phase18_state": PHASE18_STATE if is_qwen_edit else None,
        "_neo_lanpaint_phase20_state": PHASE20_STATE if is_z_image_phase20 else None,
        "z_image_lanpaint_family_variant": deepcopy(family_variant) if is_z_image_phase20 else None,
        "z_image_lanpaint_stability_policy": deepcopy(stability_policy) if is_z_image_phase20 else None,
        "qwen_edit_lanpaint_source_policy": ({"canvas_source": "image1", "max_sources": 1, "additional_sources": "preserved_not_conditioned"} if is_qwen_edit else None),
        "lanpaint_loader_parity_group": str(adapter_stabilization.get("loader_parity_group") or f"{family}:inpaint:lanpaint"),
        "lanpaint_stabilization": deepcopy(adapter_stabilization),
        "lanpaint_node_roles": node_roles,
        "lanpaint_selected_assets": selected_assets,
        "lanpaint_phase10_signature_mismatches": signature_mismatches,
        "lanpaint_mask_target": "not_masked_area" if invert_mask else "masked_area",
        "_neo_sampler_node_id": str(sampler_id or ""),
        "_neo_lanpaint_phase10_graph": bool(validation.ok),
        "_neo_lanpaint_phase20_graph": bool(validation.ok and is_z_image_phase20),
        "_neo_lora_patch_profile": lora_patch_profile,
        "lanpaint_lora_route": lora_route,
        "lanpaint_lora_mode": "model_and_clip",
        "lanpaint_lora_requested_rows": deepcopy(active_loras),
        "lanpaint_lora_base_graph_rows": deepcopy(base_graph_loras),
        "lanpaint_lora_deferred_rows": [deepcopy(row) for row in active_loras if row not in base_graph_loras],
    }

    actual_params = refresh_lanpaint_replay_contract(
        actual_params,
        provider_id=provider_id,
        workflow_prompt=workflow,
    )

    return CompiledJob(
        provider_id=provider_id,
        compile_status="compiled" if validation.ok else "mock_compiled",
        backend_payload={
            "provider_id": provider_id, "backend": "comfyui", "base_url": base_url,
            "validation": model_to_dict(validation), "prompt": workflow,
            "client_id": f"neo-studio-v2-{uuid4().hex[:8]}",
            "actual_params": actual_params,
            "runtime_progress_source": "comfyui.websocket_and_history",
            "compile_route": route.as_dict(), "capabilities": capabilities,
            "backend_capabilities": backend, "lanpaint_compile_plan": plan,
            "lanpaint_route_capabilities": capability_report,
            "prompt_conditioning": conditioning,
            "phase_notes": [
                (f"Phase 18 onboards {display_name} {loader} as a versioned source-aware LanPaint overlay." if is_qwen_edit else (f"Phase 20 completes {display_name} {loader} as a dedicated LanPaint inpainting overlay." if is_z_image_phase20 else f"Phase 14 parity-stabilizes {display_name} {loader} as an experimental LanPaint inpaint overlay.")),
                "The reusable crop/mask/latent/restore/stitch stages remain provider-owned.",
                "ModelSamplingAuraFlow is the family transform; Krea DifferentialDiffusionAdvanced is not used.",
                f"Conditioning uses clip type {clip_type}; negative handling follows the complete family policy.",
                "LoRA Stack uses the existing model+CLIP consumer-rewire path before AuraFlow and conditioning.",
                ("Qwen Image Edit LanPaint conditions only Image 1; additional normal-edit reference lanes remain preserved but excluded from masked latent generation." if is_qwen_edit else ("The canonical z_image route is Z-Image Base; z_image_turbo remains an independent distilled route, and the duplicate z_image_base alias stays blocked." if is_z_image_phase20 else "No unrelated family is activated.")),
            ],
        },
    )


__all__ = [
    "PHASE10_STATE",
    "PHASE14_STATE",
    "PHASE20_STATE",
    "PHASE21_STATE",
    "PHASE20_Z_IMAGE_FAMILIES",
    "SUPPORTED_PHASE10_FAMILIES",
    "SUPPORTED_PHASE10_LOADERS",
    "compile_lanpaint_family_inpaint",
]
