from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.lanpaint_capabilities import PHASE8_STATE, evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE13_STATE, PHASE15_STATE, adapter_asset_candidates, adapter_snapshot, resolve_lanpaint_family_adapter
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
from .lanpaint_family import _node_inputs, _select_loader_node, _signature_gate

PHASE15_STATE = "sd_family_onboarding"
SUPPORTED_SD_ROUTES = {
    ("sdxl", "checkpoint"),
    ("sd15", "checkpoint"),
    ("sd35", "diffusion_model"),
    ("sd35", "gguf"),
}


def _route_request(provider_id: str, family: str, loader: str, params: Mapping[str, Any]) -> dict[str, Any]:
    variant = "checkpoint_crop_stitch_v1" if loader == "checkpoint" else "sd3_crop_stitch_v1"
    return {
        "identity": {"provider_id": provider_id, "family": family, "loader": loader, "mode": "inpaint", "engine": "lanpaint", "variant": variant},
        "crop_policy": {
            "padding_px": _param(params, "lanpaint_crop_padding", "crop_padding"),
            "processing_size": {"width": _param(params, "lanpaint_processing_width"), "height": _param(params, "lanpaint_processing_height")},
            "resize_method": _param(params, "lanpaint_resize_method"),
        },
        "mask_policy": {
            "sampling": {"expand_px": _param(params, "lanpaint_sampling_mask_expand"), "blur_radius": _param(params, "lanpaint_sampling_mask_blur")},
            "stitch": {"expand_px": _param(params, "lanpaint_stitch_mask_expand"), "blur_radius": _param(params, "lanpaint_stitch_mask_blur")},
        },
        "sampler_policy": {
            "steps": _param(params, "lanpaint_steps"), "cfg": _param(params, "lanpaint_cfg"),
            "sampler_name": _param(params, "lanpaint_sampler"), "scheduler": _param(params, "lanpaint_scheduler"),
            "denoise": _param(params, "lanpaint_denoise"), "lanpaint_thinking_steps": _param(params, "lanpaint_thinking_steps"),
            "prompt_mode": _param(params, "lanpaint_prompt_mode"),
        },
        "stitch_policy": {"resize_method": _param(params, "lanpaint_stitch_resize_method")},
    }


def _selected_assets(adapter: Mapping[str, Any], params: Mapping[str, Any], job_model: Any, validation: ProviderValidationResult) -> dict[str, str]:
    candidates = adapter_asset_candidates(adapter, params, job_model=job_model)
    family = str(_mapping(adapter.get("identity")).get("family") or "SD")
    selected: dict[str, str] = {}
    for slot_id, values in candidates.items():
        slot = _mapping(_mapping(_mapping(adapter.get("assets")).get("slots")).get(slot_id))
        if not slot.get("required", True):
            continue
        label = f"{family.replace('_', ' ').upper()} {slot_id.replace('_', ' ')}"
        selected[slot_id] = str(require_explicit_asset_selection(validation, label, *values))
    return selected


def compile_lanpaint_sd_inpaint(
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
    backend = dict(backend_capabilities or {})
    if (family, loader) not in SUPPORTED_SD_ROUTES:
        validation.errors.append(f"LanPaint Phase 15 has no SD compiler binding for {family}+{loader}+inpaint.")
        validation.ok = False

    source_name = _source_image_name(params)
    mask_name = _mask_image_name(params)
    if not source_name:
        validation.errors.append("LanPaint inpaint requires a Comfy source image name after provider handoff.")
        validation.ok = False
    if not mask_name:
        validation.errors.append("LanPaint inpaint requires a Comfy mask image name after provider handoff.")
        validation.ok = False

    for replay_error in validate_lanpaint_replay_request(params, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint"):
        validation.errors.append(replay_error)
        validation.ok = False

    ui_state = normalize_lanpaint_ui_state(params, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint")
    params.update({key: value for key, value in dict(ui_state.get("flat_params") or {}).items() if value not in (None, "")})
    route_contract, contract_issues = normalize_lanpaint_route_contract(_route_request(provider_id, family, loader, params))
    for issue in contract_issues:
        message = str(issue.get("message") or "LanPaint route contract validation failed.")
        if issue.get("level") == "error":
            validation.errors.append(message); validation.ok = False
        else:
            validation.warnings.append(message)

    adapter = resolve_lanpaint_family_adapter(route_contract)
    adapter_policy = _mapping(adapter.get("policy")); binding = _mapping(adapter.get("binding")); identity = _mapping(adapter.get("identity"))
    if not adapter_policy.get("complete") or not binding.get("selectable"):
        validation.errors.append(f"LanPaint Phase 15 requires a complete, bound SD adapter for {family}+{loader}.")
        validation.ok = False

    active_loras = _active_lora_rows(job.extensions, params)
    base_graph_loras = _base_graph_lora_rows(active_loras)
    plan = build_lanpaint_comfy_compile_plan(route_contract, backend, lora_stack_enabled=bool(base_graph_loras))
    for message in _phase5_blocker_messages(plan):
        if message not in validation.errors:
            validation.errors.append(message); validation.ok = False

    selected = _selected_assets(adapter, params, job.model, validation)
    checkpoint_name = selected.get("model", "") if loader == "checkpoint" else ""
    model_name = selected.get("model", "")
    clip1 = selected.get("text_encoder", "")
    clip2 = selected.get("text_encoder_2", "")
    clip3 = selected.get("text_encoder_3", "")
    vae_name = selected.get("vae", "")

    selection_target = str(params.get("inpaint_selection_target") or params.get("inpaint_mask_target") or "masked_area").strip().lower()
    invert_mask = selection_target in {"not_masked", "not_masked_area", "inverse", "unmasked", "outside_mask"}
    selected_for_capability = {key: value for key, value in selected.items() if value}
    capability_report = evaluate_lanpaint_route_capabilities(
        backend, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint",
        selected_assets=selected_for_capability, require_invert_mask=invert_mask,
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

    loaders = _mapping(adapter.get("loaders")); model_policy = _mapping(loaders.get("model")); text_policy = _mapping(loaders.get("text_encoder")); vae_policy = _mapping(loaders.get("vae"))
    model_loader = _select_loader_node(plan, "family_model")
    clip_loader = _select_loader_node(plan, "text_encoder")
    vae_loader = _select_loader_node(plan, "vae")
    if loader == "checkpoint":
        model_loader = clip_loader = vae_loader = "CheckpointLoaderSimple"
    if not model_loader or not clip_loader or not vae_loader:
        validation.errors.append("LanPaint Phase 15 could not resolve the SD model/text/VAE loader contract from live capabilities.")
        validation.ok = False

    signatures: dict[str, tuple[str, ...]] = {
        "LoadImage": ("image",), "ImageToMask": ("image", "channel"),
        "CropByMask": ("image", "mask", "padding"),
        "ImageResizeKJv2": ("image", "width", "height", "upscale_method", "keep_proportion", "pad_color", "crop_position", "divisible_by"),
        "GrowMaskWithBlur": ("mask", "expand", "blur_radius"),
        "CLIPTextEncode": ("clip", "text"), "VAEEncode": ("pixels", "vae"),
        "SetLatentNoiseMask": ("samples", "mask"),
        "LanPaint_KSampler": ("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps", "LanPaint_PromptMode", "Inpainting_mode"),
        "VAEDecode": ("samples", "vae"), "ImageCompositeMasked": ("destination", "source", "mask", "x", "y", "resize_source"), "PreviewImage": ("images",),
    }
    if loader == "checkpoint":
        signatures["CheckpointLoaderSimple"] = ("ckpt_name",)
    else:
        signatures[model_loader] = ("unet_name",) if loader == "gguf" else ("unet_name", "weight_dtype")
        signatures[clip_loader] = ("clip_name1", "clip_name2", "clip_name3")
        signatures[vae_loader] = ("vae_name",)
        signatures["ModelSamplingSD3"] = ("model", "shift")
    if invert_mask: signatures["InvertMask"] = ("mask",)
    signature_mismatches = _signature_gate(validation, backend, signatures)

    requested_seed = int(_param(params, "requested_seed", "seed", default=-1) or -1)
    seed = int(_param(params, "actual_seed", "seed", default=requested_seed) or requested_seed)
    if seed < 0: seed = int(time.time() * 1000) % 2147483647
    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw")))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    positive_text = conditioning.get("effective_positive") or job.prompt or ""
    negative_text = conditioning.get("effective_negative") or job.negative_prompt or ""

    spatial = _mapping(adapter.get("spatial")); crop = _mapping(spatial.get("crop")); processing = _mapping(crop.get("processing_size")); masks = _mapping(spatial.get("mask")); sample_mask = _mapping(masks.get("sampling")); stitch_mask = _mapping(masks.get("stitch")); stitch_policy = _mapping(spatial.get("stitch"))
    sampler_policy = _mapping(_mapping(adapter.get("sampler")).get("defaults")); latent_policy = _mapping(adapter.get("latent")); lora_policy = _mapping(adapter.get("lora"))
    padding = int(crop.get("padding_px") or 96); width = int(processing.get("width") or 1024); height = int(processing.get("height") or 1024)
    resize_method = str(crop.get("resize_method") or "lanczos"); restore_method = str(stitch_policy.get("resize_method") or resize_method)
    sample_expand = int(sample_mask.get("expand_px") or 0); sample_blur = float(sample_mask.get("blur_radius") or 0.0); stitch_expand = int(stitch_mask.get("expand_px") or 0); stitch_blur = float(stitch_mask.get("blur_radius") or 0.0)
    steps = int(sampler_policy.get("steps") or 25); cfg = float(sampler_policy.get("cfg") if sampler_policy.get("cfg") is not None else 7.0); sampler_name = str(sampler_policy.get("sampler_name") or "euler"); scheduler = str(sampler_policy.get("scheduler") or "normal"); denoise = float(sampler_policy.get("denoise") if sampler_policy.get("denoise") is not None else 1.0); thinking = int(sampler_policy.get("lanpaint_thinking_steps") or 5); prompt_mode = "Prompt First" if str(sampler_policy.get("prompt_mode") or "image_first") == "prompt_first" else "Image First"; sd3_shift = float(latent_policy.get("sd3_shift") or 3.0)

    workflow: dict[str, Any] = {}; node_roles: dict[str, str] = {}
    def add(node_id: int, class_type: str, inputs: dict[str, Any], role: str) -> None:
        workflow[str(node_id)] = {"class_type": class_type, "inputs": inputs}; node_roles[str(node_id)] = role

    sampler_id = model_loader_id = clip_loader_id = 0
    model_ref: list[Any] = []; clip_ref: list[Any] = []; vae_ref: list[Any] = []
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

        if loader == "checkpoint":
            model_loader_id = next_id; clip_loader_id = model_loader_id
            add(model_loader_id, "CheckpointLoaderSimple", {"ckpt_name": checkpoint_name}, "sd_checkpoint_loader"); next_id += 1
            model_ref = [str(model_loader_id), 0]; clip_ref = [str(model_loader_id), 1]; vae_ref = [str(model_loader_id), 2]
        else:
            model_loader_id = next_id
            model_inputs = {"unet_name": model_name}
            if model_loader == "UNETLoader": model_inputs["weight_dtype"] = "default"
            add(model_loader_id, model_loader, model_inputs, "sd35_model_loader"); next_id += 1
            clip_loader_id = next_id
            add(clip_loader_id, clip_loader, {"clip_name1": clip1, "clip_name2": clip2, "clip_name3": clip3}, "sd35_triple_text_encoder"); next_id += 1
            vae_loader_id = next_id; add(vae_loader_id, vae_loader, {"vae_name": vae_name}, "sd35_vae_loader"); next_id += 1
            model_ref = [str(model_loader_id), 0]; clip_ref = [str(clip_loader_id), 0]; vae_ref = [str(vae_loader_id), 0]

        positive_id = next_id; add(positive_id, "CLIPTextEncode", {"clip": clip_ref, "text": positive_text}, "positive_conditioning"); next_id += 1
        negative_id = next_id; add(negative_id, "CLIPTextEncode", {"clip": clip_ref, "text": negative_text}, "negative_conditioning"); next_id += 1
        encode_id = next_id; add(encode_id, "VAEEncode", {"pixels": [str(resize_id), 0], "vae": vae_ref}, "latent_encode"); next_id += 1
        noise_id = next_id; add(noise_id, "SetLatentNoiseMask", {"samples": [str(encode_id), 0], "mask": [str(sample_mask_id), 0]}, "latent_noise_mask"); next_id += 1
        sample_model_ref = model_ref
        if family == "sd35":
            transform_id = next_id; add(transform_id, "ModelSamplingSD3", {"model": model_ref, "shift": sd3_shift}, "family_model_transform"); next_id += 1; sample_model_ref = [str(transform_id), 0]
        sampler_id = next_id
        add(sampler_id, "LanPaint_KSampler", {"model": sample_model_ref, "positive": [str(positive_id), 0], "negative": [str(negative_id), 0], "latent_image": [str(noise_id), 0], "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": sampler_name, "scheduler": scheduler, "denoise": denoise, "LanPaint_NumSteps": thinking, "LanPaint_PromptMode": prompt_mode, "LanPaint_Info": "LanPaint KSampler.", "Inpainting_mode": "🖼️ Image Inpainting"}, "lanpaint_sample"); next_id += 1
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
        requires_model=True, requires_clip=True, source="neo_app.providers.comfy_workflows.lanpaint_sd.phase15",
        strategy="lora_loader_model_clip_consumer_rewire", patch_model_consumers=True, patch_clip_consumers=True, validated=False,
        notes=["Phase 15 SD LoRA compatibility is engine-independent; this profile owns only the LanPaint graph anchors.", "Physical Comfy validation remains required before available promotion."],
    )

    ui_state = deepcopy(ui_state); ui_state["capability"] = deepcopy(capability_report); ui_state["route"]["route_state"] = capability_report.get("status"); ui_state["route"]["selectable"] = bool(capability_report.get("selectable")); ui_state["route"]["capability_checked"] = bool(capability_report.get("discovery", {}).get("checked")); ui_state["route"]["capability_fingerprint"] = capability_report.get("capability_fingerprint"); ui_state["validation"]["capability_ok"] = bool(capability_report.get("executable"))
    fp = deepcopy(ui_state); fp.pop("state_fingerprint", None); ui_state["state_fingerprint"] = hashlib.sha256(json.dumps(fp, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()

    verified_assets = []
    for slot_id, value in selected.items():
        role = str(_mapping(_mapping(_mapping(adapter.get("assets")).get("slots")).get(slot_id)).get("role_id") or "")
        verified_assets.append(_verify_selected_asset(validation, backend, loader_id=loader, role_id=role, label=slot_id, selected=value))

    actual_params = {
        **params, "inpaint_engine": "lanpaint", "workflow_type": WORKFLOW_TYPE, "seed": seed, "actual_seed": seed, "requested_seed": requested_seed,
        "source_image_name": source_name, "mask_image_name": mask_name, "checkpoint": checkpoint_name,
        "diffusion_model": model_name if loader == "diffusion_model" else "", "gguf_model": model_name if loader == "gguf" else "",
        "text_encoder_1": clip1, "text_encoder_2": clip2, "text_encoder_3": clip3, "vae": vae_name,
        "steps": steps, "cfg": cfg, "denoise": denoise, "sampler": sampler_name, "scheduler": scheduler, "sd3_shift": sd3_shift if family == "sd35" else None,
        "prompt_conditioning_mode": conditioning_mode, "clamp": conditioning_mode,
        "lanpaint_route": {"route_family_id": ROUTE_FAMILY_ID, "route_key": route_key, "engine": "lanpaint", "family": family, "loader": loader, "variant": identity.get("variant"), "policy_id": adapter_policy.get("policy_id"), "compiler_id": COMPILER_ID, "graph_state": PHASE15_STATE},
        "lanpaint_controls": {"crop_padding": padding, "processing_size": {"width": width, "height": height}, "resize_method": resize_method, "restore_resize_method": restore_method, "sampling_mask": {"expand": sample_expand, "blur": sample_blur}, "stitch_mask": {"expand": stitch_expand, "blur": stitch_blur}, "steps": steps, "cfg": cfg, "sampler": sampler_name, "scheduler": scheduler, "denoise": denoise, "thinking_steps": thinking, "prompt_mode": prompt_mode, "sd3_shift": sd3_shift if family == "sd35" else None},
        "lanpaint_ui_state": ui_state, "lanpaint_ui_state_fingerprint": ui_state.get("state_fingerprint"),
        "lanpaint_capability_report": capability_report, "lanpaint_capability_fingerprint": capability_report.get("capability_fingerprint"),
        "lanpaint_contract_fingerprint": route_contract.get("contract_fingerprint"), "lanpaint_compile_plan_fingerprint": plan.get("plan_fingerprint"),
        "lanpaint_family_adapter": adapter_snapshot(adapter), "lanpaint_family_adapter_id": identity.get("adapter_id"), "lanpaint_family_adapter_fingerprint": adapter.get("adapter_fingerprint"),
        "lanpaint_node_roles": node_roles, "lanpaint_selected_assets": verified_assets, "lanpaint_phase15_signature_mismatches": signature_mismatches,
        "lanpaint_mask_target": "not_masked_area" if invert_mask else "masked_area", "_neo_sampler_node_id": str(sampler_id or ""),
        "_neo_lora_patch_profile": lora_profile, "lanpaint_lora_route": lora_route, "lanpaint_lora_mode": "model_and_clip", "lanpaint_lora_requested_rows": deepcopy(active_loras), "lanpaint_lora_base_graph_rows": deepcopy(base_graph_loras), "lanpaint_lora_deferred_rows": [deepcopy(row) for row in active_loras if row not in base_graph_loras],
        "_neo_lanpaint_phase7_ui_state": PHASE7_STATE, "_neo_lanpaint_phase8_capability_state": PHASE8_STATE, "_neo_lanpaint_phase11_state": PHASE11_STATE, "_neo_lanpaint_phase13_state": PHASE13_STATE, "_neo_lanpaint_phase15_state": PHASE15_STATE,
    }
    actual_params = refresh_lanpaint_replay_contract(actual_params, provider_id=provider_id, workflow_prompt=workflow)
    return CompiledJob(provider_id=provider_id, compile_status="compiled" if validation.ok else "mock_compiled", backend_payload={
        "provider_id": provider_id, "backend": "comfyui", "base_url": base_url, "validation": model_to_dict(validation), "prompt": workflow,
        "client_id": f"neo-studio-v2-{uuid4().hex[:8]}", "actual_params": actual_params, "runtime_progress_source": "comfyui.websocket_and_history",
        "compile_route": route.as_dict(), "capabilities": capabilities, "backend_capabilities": backend, "lanpaint_compile_plan": plan,
        "lanpaint_route_capabilities": capability_report, "prompt_conditioning": conditioning,
        "phase_notes": ["Phase 15 onboards the SD family through exact family/loader adapters.", "SDXL and SD 1.5 use a bundled checkpoint MODEL/CLIP/VAE graph.", "SD 3.5 uses triple text encoders plus ModelSamplingSD3.", "SD 1.5 GGUF remains blocked because no approved convolutional-UNet GGUF loader ecosystem is declared."],
    })


__all__ = ["PHASE15_STATE", "SUPPORTED_SD_ROUTES", "compile_lanpaint_sd_inpaint"]
