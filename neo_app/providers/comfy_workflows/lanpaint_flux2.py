from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.lanpaint_capabilities import PHASE8_STATE, evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE13_STATE, PHASE17_STATE, adapter_asset_candidates, adapter_snapshot, resolve_lanpaint_family_adapter
from neo_app.image.flux2_klein_contract import check_flux2_klein_compatibility, resolve_flux2_klein_variant
from neo_app.image.lanpaint_replay import PHASE11_STATE, refresh_lanpaint_replay_contract, validate_lanpaint_replay_request
from neo_app.image.lanpaint_route_contract import ROUTE_FAMILY_ID, normalize_lanpaint_route_contract
from neo_app.image.lanpaint_ui_state import PHASE7_STATE, normalize_lanpaint_ui_state
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile

from .lanpaint import (
    COMPILER_ID,
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
)
from .lanpaint_family import _select_loader_node, _signature_gate

SUPPORTED_FLUX2_ROUTES = {
    ("flux2_dev", "diffusion_model"), ("flux2_dev", "gguf"),
    ("flux2_klein", "diffusion_model"), ("flux2_klein", "gguf"),
}


def _normalize_variant(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


def _is_flux2_dev_model(value: Any) -> bool:
    text = str(value or "").strip().lower().replace("_", "-")
    return ("flux2" in text or "flux-2" in text or "flux.2" in text) and "klein" not in text


def _resolve_variant(family: str, params: Mapping[str, Any], model_name: Any, encoder_name: Any, validation: ProviderValidationResult) -> str:
    explicit = _normalize_variant(_param(params, "flux_variant", "variant", default=""))
    model_text = str(model_name or "").lower()
    if family == "flux2_dev":
        if "klein" in model_text or "klein" in explicit:
            validation.errors.append("Flux.2 Klein assets must use the dedicated flux2_klein LanPaint route; Flux.2 Dev cannot reinterpret them.")
            validation.ok = False
        if explicit and explicit not in {"dev", "flux2_dev", "flux_2_dev"}:
            validation.errors.append(f"Unsupported Flux.2 Dev variant '{explicit}'.")
            validation.ok = False
        if not _is_flux2_dev_model(model_name):
            validation.errors.append("Flux.2 Dev LanPaint requires model identity that explicitly indicates Flux.2 Dev and excludes Klein.")
            validation.ok = False
        enc = str(encoder_name or "").lower()
        if "qwen" in enc or (enc and "mistral" not in enc):
            validation.errors.append("Flux.2 Dev requires the Mistral 3 Small Flux2 text encoder; Qwen3/Klein encoders are incompatible.")
            validation.ok = False
        return "dev"

    resolved = resolve_flux2_klein_variant(explicit or "flux2_klein", model_name)
    if not resolved or resolved in {"klein", "flux2_klein", "flux_2_klein"}:
        validation.errors.append("Flux.2 Klein LanPaint requires an exact 4B/9B base or distilled variant, resolved from the selected model or explicit flux_variant.")
        validation.ok = False
        resolved = "klein_4b_distilled"
    compatibility = check_flux2_klein_compatibility(resolved, model_name, encoder_name)
    if compatibility.compatible is False:
        validation.errors.append(compatibility.message)
        validation.ok = False
    elif compatibility.compatible is None and compatibility.message:
        validation.warnings.append(compatibility.message)
    return resolved


def _route_request(provider_id: str, family: str, loader: str, variant: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": {
            "provider_id": provider_id,
            "family": family,
            "loader": loader,
            "mode": "inpaint",
            "engine": "lanpaint",
            "variant": f"{family}_{variant}_crop_stitch_v1",
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
            "cfg": 1.0,
            "sampler_name": _param(params, "lanpaint_sampler"),
            "scheduler": _param(params, "lanpaint_scheduler"),
            "denoise": _param(params, "lanpaint_denoise"),
            "lanpaint_thinking_steps": _param(params, "lanpaint_thinking_steps"),
            "prompt_mode": "image_first",
        },
        "stitch_policy": {"resize_method": _param(params, "lanpaint_stitch_resize_method")},
    }


def _selected_assets(adapter: Mapping[str, Any], params: Mapping[str, Any], job_model: Any, validation: ProviderValidationResult) -> dict[str, str]:
    candidates = adapter_asset_candidates(adapter, params, job_model=job_model)
    family = str(_mapping(adapter.get("identity")).get("family") or "flux2")
    selected: dict[str, str] = {}
    labels = {
        "model": f"{family} diffusion model",
        "text_encoder": "Flux.2 Dev Mistral3 text encoder" if family == "flux2_dev" else "Flux.2 Klein Qwen3 text encoder",
        "vae": "Flux.2 VAE",
    }
    for slot_id, values in candidates.items():
        slot = _mapping(_mapping(_mapping(adapter.get("assets")).get("slots")).get(slot_id))
        if not slot.get("required", True):
            continue
        selected[slot_id] = str(require_explicit_asset_selection(validation, labels.get(slot_id, slot_id), *values))
    return selected


def _model_loader_inputs(node_class: str, model_name: str) -> dict[str, Any]:
    if node_class == "LoaderGGUF":
        return {"gguf_name": model_name}
    inputs: dict[str, Any] = {"unet_name": model_name}
    if node_class == "UNETLoader":
        inputs["weight_dtype"] = "default"
    return inputs


def compile_lanpaint_flux2_inpaint(
    *,
    provider_id: str,
    base_url: str,
    job: NeoJob,
    validation: ProviderValidationResult,
    route: CompileRoute,
    capabilities: dict[str, Any],
    backend_capabilities: dict[str, Any] | None = None,
) -> CompiledJob:
    family = str(route.family or job.family or "").strip()
    loader = str(route.loader or job.loader or "").strip()
    params = dict(job.params or {})
    submitted_params = dict(params)
    backend = dict(backend_capabilities or {})
    if (family, loader) not in SUPPORTED_FLUX2_ROUTES:
        validation.errors.append(f"LanPaint Phase 17 has no Flux.2 compiler binding for {family}+{loader}+inpaint.")
        validation.ok = False

    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    if not source_name:
        validation.errors.append("Flux.2 LanPaint requires a Comfy source image name after provider handoff.")
        validation.ok = False
    if not mask_name:
        validation.errors.append("Flux.2 LanPaint requires a Comfy mask image name after provider handoff.")
        validation.ok = False

    for replay_error in validate_lanpaint_replay_request(params, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint"):
        validation.errors.append(replay_error)
        validation.ok = False

    ui_state = normalize_lanpaint_ui_state(params, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint")
    params.update({key: value for key, value in dict(ui_state.get("flat_params") or {}).items() if value not in (None, "")})

    preliminary_contract, _ = normalize_lanpaint_route_contract(_route_request(provider_id, family, loader, "dev" if family == "flux2_dev" else "klein_4b_distilled", params))
    adapter = resolve_lanpaint_family_adapter(preliminary_contract)
    adapter_policy = _mapping(adapter.get("policy")); binding = _mapping(adapter.get("binding")); identity = _mapping(adapter.get("identity"))
    if not adapter_policy.get("complete") or not binding.get("selectable"):
        validation.errors.append(f"LanPaint Phase 17 requires a complete, bound Flux.2 adapter for {family}+{loader}.")
        validation.ok = False

    selected = _selected_assets(adapter, params, job.model, validation)
    model_name = selected.get("model", "")
    encoder_name = selected.get("text_encoder", "")
    vae_name = selected.get("vae", "")
    variant = _resolve_variant(family, params, model_name, encoder_name, validation)
    params["flux_variant"] = variant

    route_contract, contract_issues = normalize_lanpaint_route_contract(_route_request(provider_id, family, loader, variant, params))
    for issue in contract_issues:
        message = str(issue.get("message") or "LanPaint route contract validation failed.")
        if issue.get("level") == "error":
            validation.errors.append(message); validation.ok = False
        else:
            validation.warnings.append(message)
    adapter = resolve_lanpaint_family_adapter(route_contract)
    adapter_policy = _mapping(adapter.get("policy")); binding = _mapping(adapter.get("binding")); identity = _mapping(adapter.get("identity"))

    active_loras = _active_lora_rows(job.extensions, params)
    base_graph_loras = _base_graph_lora_rows(active_loras)
    plan = build_lanpaint_comfy_compile_plan(route_contract, backend, lora_stack_enabled=bool(base_graph_loras))
    for message in _phase5_blocker_messages(plan):
        if message not in validation.errors:
            validation.errors.append(message); validation.ok = False

    selection_target = str(params.get("inpaint_selection_target") or params.get("inpaint_mask_target") or "masked_area").strip().lower()
    invert_mask = selection_target in {"not_masked", "not_masked_area", "inverse", "unmasked", "outside_mask"}
    capability_report = evaluate_lanpaint_route_capabilities(
        backend, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint",
        selected_assets={key: value for key, value in selected.items() if value}, require_invert_mask=invert_mask,
        require_model_clip_lora=bool(base_graph_loras),
    )
    for blocker in capability_report.get("blockers", []):
        message = f"LanPaint capability gate [{blocker.get('code') or 'blocked'}]: {blocker.get('message') or 'Route requirements unavailable.'}"
        if message not in validation.errors:
            validation.errors.append(message); validation.ok = False
    for warning in capability_report.get("warnings", []):
        message = f"LanPaint capability notice [{warning.get('code') or 'notice'}]: {warning.get('message') or ''}".strip()
        if message not in validation.warnings:
            validation.warnings.append(message)

    model_loader = _select_loader_node(plan, "family_model")
    clip_loader = _select_loader_node(plan, "text_encoder")
    vae_loader = _select_loader_node(plan, "vae")
    if not model_loader or not clip_loader or not vae_loader:
        validation.errors.append("LanPaint Phase 17 could not resolve the Flux.2 model, single-CLIP, and VAE loader contract from live capabilities.")
        validation.ok = False

    signatures: dict[str, tuple[str, ...]] = {
        "LoadImage": ("image",), "ImageToMask": ("image", "channel"),
        "CropByMask": ("image", "mask", "padding"),
        "ImageResizeKJv2": ("image", "width", "height", "upscale_method", "keep_proportion", "pad_color", "crop_position", "divisible_by"),
        "GrowMaskWithBlur": ("mask", "expand", "blur_radius"),
        "CLIPTextEncode": ("clip", "text"), "FluxGuidance": ("conditioning", "guidance"),
        "ConditioningZeroOut": ("conditioning",), "VAEEncode": ("pixels", "vae"),
        "SetLatentNoiseMask": ("samples", "mask"),
        "LanPaint_KSampler": ("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps", "LanPaint_PromptMode", "Inpainting_mode"),
        "VAEDecode": ("samples", "vae"), "ImageCompositeMasked": ("destination", "source", "mask", "x", "y", "resize_source"), "PreviewImage": ("images",),
    }
    signatures[model_loader] = (("gguf_name",) if model_loader == "LoaderGGUF" else (("unet_name",) if loader == "gguf" else ("unet_name", "weight_dtype")))
    signatures[clip_loader] = ("clip_name", "type")
    signatures[vae_loader] = ("vae_name",)
    if invert_mask:
        signatures["InvertMask"] = ("mask",)
    signature_mismatches = _signature_gate(validation, backend, signatures)

    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    positive_text = conditioning.get("effective_positive") or job.prompt or ""

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1) or -1)
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed) or requested_seed)
    if seed < 0:
        seed = int(time.time() * 1000) % 2147483647

    policy = __import__("neo_app.image.lanpaint_family_policies", fromlist=["get_lanpaint_family_policy"]).get_lanpaint_family_policy(family)
    variant_profiles = _mapping(_mapping(policy.get("route_defaults")).get("variant_profiles"))
    variant_defaults = _mapping(variant_profiles.get(variant))
    spatial = _mapping(adapter.get("spatial")); crop = _mapping(spatial.get("crop")); processing = _mapping(crop.get("processing_size")); masks = _mapping(spatial.get("mask")); sample_mask = _mapping(masks.get("sampling")); stitch_mask = _mapping(masks.get("stitch")); stitch_policy = _mapping(spatial.get("stitch")); latent_policy = _mapping(adapter.get("latent")); lora_policy = _mapping(adapter.get("lora")); sampler_defaults = _mapping(_mapping(adapter.get("sampler")).get("defaults"))

    padding = int(_param(params, "lanpaint_crop_padding", "crop_padding", default=crop.get("padding_px") or 128))
    width = int(_param(params, "lanpaint_processing_width", default=processing.get("width") or 1024))
    height = int(_param(params, "lanpaint_processing_height", default=processing.get("height") or 1024))
    resize_method = str(_param(params, "lanpaint_resize_method", default=crop.get("resize_method") or "lanczos"))
    restore_method = str(_param(params, "lanpaint_stitch_resize_method", default=stitch_policy.get("resize_method") or resize_method))
    sample_expand = int(_param(params, "lanpaint_sampling_mask_expand", default=sample_mask.get("expand_px") or 40))
    sample_blur = float(_param(params, "lanpaint_sampling_mask_blur", default=sample_mask.get("blur_radius") or 28.0))
    stitch_expand = int(_param(params, "lanpaint_stitch_mask_expand", default=stitch_mask.get("expand_px") or 48))
    stitch_blur = float(_param(params, "lanpaint_stitch_mask_blur", default=stitch_mask.get("blur_radius") or 9.0))
    steps = int(_param(submitted_params, "lanpaint_steps", default=variant_defaults.get("steps") or sampler_defaults.get("steps") or 30))
    cfg = 1.0
    sampler_name = str(_param(params, "lanpaint_sampler", default=sampler_defaults.get("sampler_name") or "euler"))
    scheduler = str(_param(params, "lanpaint_scheduler", default=sampler_defaults.get("scheduler") or "simple"))
    denoise = float(_param(params, "lanpaint_denoise", default=sampler_defaults.get("denoise") if sampler_defaults.get("denoise") is not None else 1.0))
    thinking = int(_param(submitted_params, "lanpaint_thinking_steps", default=variant_defaults.get("lanpaint_thinking_steps") or sampler_defaults.get("lanpaint_thinking_steps") or 5))
    requested_guidance = float(_param(submitted_params, "flux_guidance", "lanpaint_flux_guidance", "guidance", default=variant_defaults.get("flux_guidance") if variant_defaults.get("flux_guidance") is not None else 1.5))
    guidance_range = list(variant_defaults.get("guidance_range") or ([1.0, 6.0] if family == "flux2_dev" or "distilled" not in variant else [0.0, 2.0]))
    guidance_min, guidance_max = float(guidance_range[0]), float(guidance_range[1])
    flux_guidance = min(max(requested_guidance, guidance_min), guidance_max)
    if flux_guidance != requested_guidance:
        validation.warnings.append(f"Flux.2 {variant} guidance was clamped from {requested_guidance:g} to {flux_guidance:g}; supported LanPaint range is {guidance_min:g}-{guidance_max:g}.")
    if str(_param(params, "lanpaint_prompt_mode", default="image_first")).strip().lower().replace(" ", "_") in {"prompt_first", "prompt"}:
        validation.warnings.append("Prompt First is disabled for Flux.2 LanPaint; Image First is enforced because Flux uses CFG 1.0 guidance conditioning.")
    prompt_mode = "Image First"

    workflow: dict[str, Any] = {}; node_roles: dict[str, str] = {}
    def add(node_id: int, class_type: str, inputs: dict[str, Any], role: str) -> None:
        workflow[str(node_id)] = {"class_type": class_type, "inputs": inputs}; node_roles[str(node_id)] = role

    sampler_id = 0; model_ref: list[Any] = []; clip_ref: list[Any] = []; vae_ref: list[Any] = []
    if validation.ok:
        add(1, "LoadImage", {"image": source_name}, "source_image"); add(2, "LoadImage", {"image": mask_name}, "mask_image"); add(3, "ImageToMask", {"image": ["2", 0], "channel": "red"}, "mask_image_to_mask")
        mask_ref: list[Any] = ["3", 0]; next_id = 4
        if invert_mask:
            add(next_id, "InvertMask", {"mask": mask_ref}, "invert_mask"); mask_ref = [str(next_id), 0]; next_id += 1
        crop_id = next_id; add(crop_id, "CropByMask", {"image": ["1", 0], "mask": mask_ref, "padding": padding}, "crop_context"); next_id += 1
        resize_inputs = {"image": [str(crop_id), 0], "mask": [str(crop_id), 1], "width": width, "height": height, "upscale_method": resize_method, "keep_proportion": "stretch", "pad_color": "0, 0, 0", "crop_position": "center", "divisible_by": 8}
        _optional_input(resize_inputs, backend, "ImageResizeKJv2", "device", "cpu")
        resize_id = next_id; add(resize_id, "ImageResizeKJv2", resize_inputs, "processing_resize"); next_id += 1
        sample_inputs = {"mask": [str(resize_id), 3], "expand": sample_expand, "incremental_expandrate": 0.0, "tapered_corners": True, "flip_input": False, "blur_radius": sample_blur, "lerp_alpha": 1.0, "decay_factor": 1.0}
        _optional_input(sample_inputs, backend, "GrowMaskWithBlur", "fill_holes", bool(sample_mask.get("fill_holes", False)))
        sample_mask_id = next_id; add(sample_mask_id, "GrowMaskWithBlur", sample_inputs, "sampling_mask_refine"); next_id += 1

        model_loader_id = next_id; add(model_loader_id, model_loader, _model_loader_inputs(model_loader, model_name), "flux2_model_loader"); next_id += 1
        clip_loader_id = next_id; add(clip_loader_id, clip_loader, {"clip_name": encoder_name, "type": "flux2"}, "flux2_text_encoder"); next_id += 1
        vae_loader_id = next_id; add(vae_loader_id, vae_loader, {"vae_name": vae_name}, "flux2_vae_loader"); next_id += 1
        model_ref = [str(model_loader_id), 0]; clip_ref = [str(clip_loader_id), 0]; vae_ref = [str(vae_loader_id), 0]

        positive_id = next_id; add(positive_id, "CLIPTextEncode", {"clip": clip_ref, "text": positive_text}, "positive_conditioning"); next_id += 1
        guidance_id = next_id; add(guidance_id, "FluxGuidance", {"conditioning": [str(positive_id), 0], "guidance": flux_guidance}, "flux_guidance"); next_id += 1
        negative_id = next_id; add(negative_id, "ConditioningZeroOut", {"conditioning": [str(positive_id), 0]}, "negative_conditioning"); next_id += 1
        encode_id = next_id; add(encode_id, "VAEEncode", {"pixels": [str(resize_id), 0], "vae": vae_ref}, "latent_encode"); next_id += 1
        noise_id = next_id; add(noise_id, "SetLatentNoiseMask", {"samples": [str(encode_id), 0], "mask": [str(sample_mask_id), 0]}, "latent_noise_mask"); next_id += 1
        sample_model_ref = model_ref
        sampler_id = next_id
        add(sampler_id, "LanPaint_KSampler", {"model": sample_model_ref, "positive": [str(guidance_id), 0], "negative": [str(negative_id), 0], "latent_image": [str(noise_id), 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler_name, "scheduler": scheduler, "denoise": denoise, "LanPaint_NumSteps": thinking, "LanPaint_PromptMode": prompt_mode, "LanPaint_Info": "LanPaint KSampler.", "Inpainting_mode": "🖼️ Image Inpainting"}, "lanpaint_sample"); next_id += 1
        decode_id = next_id; add(decode_id, "VAEDecode", {"samples": [str(sampler_id), 0], "vae": vae_ref}, "latent_decode"); next_id += 1
        restore_inputs = {"image": [str(decode_id), 0], "mask": [str(sample_mask_id), 0], "width": [str(crop_id), 4], "height": [str(crop_id), 5], "upscale_method": restore_method, "keep_proportion": "stretch", "pad_color": "0, 0, 0", "crop_position": "center", "divisible_by": 2}
        _optional_input(restore_inputs, backend, "ImageResizeKJv2", "device", "cpu")
        restore_id = next_id; add(restore_id, "ImageResizeKJv2", restore_inputs, "restore_crop_size"); next_id += 1
        stitch_inputs = {"mask": [str(restore_id), 3], "expand": stitch_expand, "incremental_expandrate": 0.0, "tapered_corners": True, "flip_input": False, "blur_radius": stitch_blur, "lerp_alpha": 1.0, "decay_factor": 1.0}
        _optional_input(stitch_inputs, backend, "GrowMaskWithBlur", "fill_holes", bool(stitch_mask.get("fill_holes", False)))
        stitch_id = next_id; add(stitch_id, "GrowMaskWithBlur", stitch_inputs, "stitch_mask_refine"); next_id += 1
        composite_id = next_id; add(composite_id, "ImageCompositeMasked", {"destination": ["1", 0], "source": [str(restore_id), 0], "mask": [str(stitch_id), 0], "x": [str(crop_id), 2], "y": [str(crop_id), 3], "resize_source": False}, "stitch_composite"); next_id += 1
        add(next_id, "PreviewImage", {"images": [str(composite_id), 0]}, "output_handoff")

    route_key = f"{family}:{loader}:inpaint:lanpaint"; compatibility_key = f"{family}:{loader}:inpaint"
    lora_route = {"backend": provider_id, "provider_id": provider_id, "family": family, "loader": loader, "workflow_mode": "inpaint", "mode": "inpaint", "engine": "lanpaint", "route_key": compatibility_key, "compatibility_route_key": compatibility_key, "workflow_route_key": route_key, "route_state": "experimental_available"}
    lora_profile = build_lora_patch_profile(
        route=lora_route, model_ref=model_ref if validation.ok else None, clip_ref=clip_ref if validation.ok else None,
        sampler_node_id=str(sampler_id or ""), sampler_model_input="model", loader_node_class=str(lora_policy.get("loader_node_class") or "LoraLoader"),
        requires_model=True, requires_clip=True, source="neo_app.providers.comfy_workflows.lanpaint_flux2.phase17",
        strategy="lora_loader_model_clip_consumer_rewire", patch_model_consumers=True, patch_clip_consumers=True, validated=False,
        notes=["Phase 17 Flux.2 LoRA compatibility is engine-independent; the compiler owns only the LanPaint graph anchors.", "Physical Comfy validation remains required before available promotion."],
    )

    ui_state = deepcopy(ui_state); ui_state["capability"] = deepcopy(capability_report); ui_state["route"]["route_state"] = capability_report.get("status"); ui_state["route"]["selectable"] = bool(capability_report.get("selectable")); ui_state["route"]["capability_checked"] = bool(capability_report.get("discovery", {}).get("checked")); ui_state["route"]["capability_fingerprint"] = capability_report.get("capability_fingerprint"); ui_state["validation"]["capability_ok"] = bool(capability_report.get("executable"))
    fp = deepcopy(ui_state); fp.pop("state_fingerprint", None); ui_state["state_fingerprint"] = hashlib.sha256(json.dumps(fp, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    verified_assets = []
    for slot_id, value in selected.items():
        role = str(_mapping(_mapping(_mapping(adapter.get("assets")).get("slots")).get(slot_id)).get("role_id") or "")
        verified_assets.append(_verify_selected_asset(validation, backend, loader_id=loader, role_id=role, label=slot_id, selected=value))

    actual_params = {
        **params, "inpaint_engine": "lanpaint", "workflow_type": WORKFLOW_TYPE, "seed": seed, "actual_seed": seed, "requested_seed": requested_seed,
        "source_image_name": source_name, "mask_image_name": mask_name, "diffusion_model": model_name if loader == "diffusion_model" else "", "gguf_model": model_name if loader == "gguf" else "",
        "text_encoder_1": encoder_name, "qwen3_text_encoder": encoder_name if family == "flux2_klein" else "", "mistral3_text_encoder": encoder_name if family == "flux2_dev" else "", "vae": vae_name, "flux_variant": variant, "flux_guidance": flux_guidance,
        "steps": steps, "cfg": cfg, "denoise": denoise, "sampler": sampler_name, "scheduler": scheduler,
        "prompt_conditioning_mode": conditioning_mode, "clamp": conditioning_mode,
        "lanpaint_route": {"route_family_id": ROUTE_FAMILY_ID, "route_key": route_key, "engine": "lanpaint", "family": family, "loader": loader, "variant": variant, "adapter_variant": identity.get("variant"), "policy_id": adapter_policy.get("policy_id"), "compiler_id": COMPILER_ID, "graph_state": PHASE17_STATE},
        "lanpaint_controls": {"crop_padding": padding, "processing_size": {"width": width, "height": height}, "resize_method": resize_method, "restore_resize_method": restore_method, "sampling_mask": {"expand": sample_expand, "blur": sample_blur}, "stitch_mask": {"expand": stitch_expand, "blur": stitch_blur}, "steps": steps, "cfg": cfg, "flux_guidance": flux_guidance, "sampler": sampler_name, "scheduler": scheduler, "denoise": denoise, "thinking_steps": thinking, "prompt_mode": prompt_mode, "flux2_encoder_architecture": "mistral3_small_flux2" if family == "flux2_dev" else "qwen3_flux2"},
        "lanpaint_ui_state": ui_state, "lanpaint_ui_state_fingerprint": ui_state.get("state_fingerprint"), "lanpaint_capability_report": capability_report, "lanpaint_capability_fingerprint": capability_report.get("capability_fingerprint"),
        "lanpaint_contract_fingerprint": route_contract.get("contract_fingerprint"), "lanpaint_compile_plan_fingerprint": plan.get("plan_fingerprint"), "lanpaint_family_adapter": adapter_snapshot(adapter), "lanpaint_family_adapter_id": identity.get("adapter_id"), "lanpaint_family_adapter_fingerprint": adapter.get("adapter_fingerprint"),
        "lanpaint_node_roles": node_roles, "lanpaint_selected_assets": verified_assets, "lanpaint_phase17_signature_mismatches": signature_mismatches, "lanpaint_mask_target": "not_masked_area" if invert_mask else "masked_area", "_neo_sampler_node_id": str(sampler_id or ""),
        "_neo_lora_patch_profile": lora_profile, "lanpaint_lora_route": lora_route, "lanpaint_lora_mode": "model_and_clip", "lanpaint_lora_requested_rows": deepcopy(active_loras), "lanpaint_lora_base_graph_rows": deepcopy(base_graph_loras), "lanpaint_lora_deferred_rows": [deepcopy(row) for row in active_loras if row not in base_graph_loras],
        "_neo_lanpaint_phase7_ui_state": PHASE7_STATE, "_neo_lanpaint_phase8_capability_state": PHASE8_STATE, "_neo_lanpaint_phase11_state": PHASE11_STATE, "_neo_lanpaint_phase13_state": PHASE13_STATE, "_neo_lanpaint_phase17_state": PHASE17_STATE,
    }
    actual_params = refresh_lanpaint_replay_contract(actual_params, provider_id=provider_id, workflow_prompt=workflow)
    return CompiledJob(provider_id=provider_id, compile_status="compiled" if validation.ok else "mock_compiled", backend_payload={
        "provider_id": provider_id, "backend": "comfyui", "base_url": base_url, "validation": model_to_dict(validation), "prompt": workflow,
        "client_id": f"neo-studio-v2-{uuid4().hex[:8]}", "actual_params": actual_params, "runtime_progress_source": "comfyui.websocket_and_history",
        "compile_route": route.as_dict(), "capabilities": capabilities, "backend_capabilities": backend, "lanpaint_compile_plan": plan, "lanpaint_route_capabilities": capability_report, "prompt_conditioning": conditioning,
        "phase_notes": ["Phase 17 onboards Flux.2 Dev and Klein through separate family adapters for safetensors/component and GGUF model routes.", "Flux.2 Dev uses the Mistral3 Small Flux2 encoder; Klein uses the existing Qwen3 size compatibility contract.", "Flux.2 uses CFG 1.0, FluxGuidance, zeroed negative conditioning, and Image First LanPaint semantics.", "Flux.2 editing, reference conditioning, ControlNet, Fill, Kontext, and outpaint remain outside Phase 17."],
    })


__all__ = ["PHASE17_STATE", "SUPPORTED_FLUX2_ROUTES", "compile_lanpaint_flux2_inpaint"]
