from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.lanpaint_capabilities import PHASE8_STATE, evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE13_STATE, PHASE21_STATE, adapter_asset_candidates, adapter_snapshot, resolve_lanpaint_family_adapter
from neo_app.image.lanpaint_replay import PHASE11_STATE, refresh_lanpaint_replay_contract, validate_lanpaint_replay_request
from neo_app.image.lanpaint_route_contract import ROUTE_FAMILY_ID, normalize_lanpaint_route_contract
from neo_app.image.lanpaint_ui_state import PHASE7_STATE, normalize_lanpaint_ui_state
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile

from .lanpaint import (
    COMPILER_ID, WORKFLOW_TYPE, _active_lora_rows, _base_graph_lora_rows, _mapping,
    _mask_image_name, _optional_input, _param, _phase5_blocker_messages,
    _source_image_name, _verify_selected_asset, build_lanpaint_comfy_compile_plan,
)
from .lanpaint_family import _select_loader_node, _signature_gate

SUPPORTED_HIDREAM_ROUTES = {("hidream", "diffusion_model"), ("hidream", "gguf")}
HIDREAM_I1_PROFILES = {
    "full": {"steps": 50, "cfg": 5.0, "shift": 3.0, "sampler": "lcm", "scheduler": "normal"},
    "dev": {"steps": 28, "cfg": 1.0, "shift": 6.0, "sampler": "lcm", "scheduler": "normal"},
    "fast": {"steps": 16, "cfg": 1.0, "shift": 3.0, "sampler": "lcm", "scheduler": "normal"},
}


def _route_request(provider_id: str, loader: str, params: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "identity": {"provider_id": provider_id, "family": "hidream", "loader": loader, "mode": "inpaint", "engine": "lanpaint", "variant": "hidream_i1_quad_clip_crop_stitch_v1"},
        "crop_policy": {"padding_px": _param(params, "lanpaint_crop_padding", "crop_padding"), "processing_size": {"width": _param(params, "lanpaint_processing_width"), "height": _param(params, "lanpaint_processing_height")}, "resize_method": _param(params, "lanpaint_resize_method")},
        "mask_policy": {"sampling": {"expand_px": _param(params, "lanpaint_sampling_mask_expand"), "blur_radius": _param(params, "lanpaint_sampling_mask_blur")}, "stitch": {"expand_px": _param(params, "lanpaint_stitch_mask_expand"), "blur_radius": _param(params, "lanpaint_stitch_mask_blur")}},
        "sampler_policy": {"steps": _param(params, "lanpaint_steps"), "cfg": _param(params, "lanpaint_cfg"), "sampler_name": _param(params, "lanpaint_sampler"), "scheduler": _param(params, "lanpaint_scheduler"), "denoise": _param(params, "lanpaint_denoise"), "lanpaint_thinking_steps": _param(params, "lanpaint_thinking_steps"), "prompt_mode": _param(params, "lanpaint_prompt_mode")},
        "stitch_policy": {"resize_method": _param(params, "lanpaint_stitch_resize_method")},
    }


def _normalize_i1_variant(params: Mapping[str, Any], model_name: Any, validation: ProviderValidationResult) -> str:
    variant = str(_param(params, "hidream_variant", "variant", default="HiDream-I1") or "HiDream-I1").strip().lower().replace("_", "-")
    if variant not in {"hidream-i1", "i1", "hidream i1"}:
        validation.errors.append("Phase 21 HiDream LanPaint supports HiDream-I1 only. HiDream-E1/E1.1 and HiDream-O1 remain separate variant-gated architectures.")
        validation.ok = False
    explicit = str(_param(params, "hidream_i1_profile", "hidream_profile", default="") or "").strip().lower()
    model = str(model_name or "").lower()
    inferred = "fast" if "fast" in model else ("full" if "full" in model else ("dev" if "dev" in model else ""))
    if explicit and explicit not in HIDREAM_I1_PROFILES:
        validation.errors.append(f"Unsupported HiDream-I1 profile '{explicit}'. Select full, dev, or fast.")
        validation.ok = False
        explicit = "dev"
    if explicit and inferred and explicit != inferred:
        validation.errors.append(f"HiDream-I1 profile mismatch: selected {explicit}, but the model filename indicates {inferred}.")
        validation.ok = False
    if not explicit and not inferred:
        validation.warnings.append("HiDream-I1 model filename did not identify full/dev/fast; Phase 21 uses the conservative dev defaults. Select hidream_i1_profile explicitly for deterministic replay.")
    return explicit or inferred or "dev"


def _selected_assets(adapter: Mapping[str, Any], params: Mapping[str, Any], job_model: Any, validation: ProviderValidationResult) -> dict[str, str]:
    labels = {
        "model": "HiDream-I1 diffusion model", "text_encoder": "HiDream CLIP-L encoder",
        "text_encoder_2": "HiDream CLIP-G encoder", "text_encoder_3": "HiDream T5XXL encoder",
        "text_encoder_4": "HiDream Llama 3.1 8B encoder", "vae": "HiDream AE / VAE",
    }
    selected: dict[str, str] = {}
    for slot_id, values in adapter_asset_candidates(adapter, params, job_model=job_model).items():
        slot = _mapping(_mapping(_mapping(adapter.get("assets")).get("slots")).get(slot_id))
        if slot.get("required", True):
            selected[slot_id] = str(require_explicit_asset_selection(validation, labels.get(slot_id, slot_id), *values))
    return selected


def _model_inputs(node_class: str, model_name: str) -> dict[str, Any]:
    if node_class == "LoaderGGUF":
        return {"gguf_name": model_name}
    values: dict[str, Any] = {"unet_name": model_name}
    if node_class == "UNETLoader":
        values["weight_dtype"] = "default"
    return values


def compile_lanpaint_hidream_i1_inpaint(*, provider_id: str, base_url: str, job: NeoJob, validation: ProviderValidationResult, route: CompileRoute, capabilities: dict[str, Any], backend_capabilities: dict[str, Any] | None = None) -> CompiledJob:
    family = str(route.family or job.family or "").strip()
    loader = str(route.loader or job.loader or "").strip()
    params = dict(job.params or {})
    requested_params = deepcopy(params)
    backend = dict(backend_capabilities or {})
    if (family, loader) not in SUPPORTED_HIDREAM_ROUTES:
        validation.errors.append(f"Phase 21 has no HiDream-I1 LanPaint binding for {family}+{loader}+inpaint.")
        validation.ok = False
    source_name, mask_name = _source_image_name(params), _mask_image_name(params)
    if not source_name:
        validation.errors.append("HiDream-I1 LanPaint requires a source image after provider handoff."); validation.ok = False
    if not mask_name:
        validation.errors.append("HiDream-I1 LanPaint requires a mask image after provider handoff."); validation.ok = False
    for error in validate_lanpaint_replay_request(params, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint"):
        validation.errors.append(error); validation.ok = False

    ui_state = normalize_lanpaint_ui_state(params, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint")
    params.update({k: v for k, v in dict(ui_state.get("flat_params") or {}).items() if v not in (None, "")})
    route_contract, contract_issues = normalize_lanpaint_route_contract(_route_request(provider_id, loader, params))
    for issue in contract_issues:
        (validation.errors if issue.get("level") == "error" else validation.warnings).append(str(issue.get("message") or "LanPaint route contract validation failed."))
        if issue.get("level") == "error": validation.ok = False
    adapter = resolve_lanpaint_family_adapter(route_contract)
    identity, binding, policy = _mapping(adapter.get("identity")), _mapping(adapter.get("binding")), _mapping(adapter.get("policy"))
    if not policy.get("complete") or not binding.get("selectable"):
        validation.errors.append("Phase 21 requires a complete, bound HiDream-I1 family adapter."); validation.ok = False

    active_loras = _active_lora_rows(job.extensions, params); base_loras = _base_graph_lora_rows(active_loras)
    plan = build_lanpaint_comfy_compile_plan(route_contract, backend, lora_stack_enabled=bool(base_loras))
    for message in _phase5_blocker_messages(plan):
        if message not in validation.errors: validation.errors.append(message); validation.ok = False

    selected = _selected_assets(adapter, params, job.model, validation)
    model_name = selected.get("model", "")
    profile_id = _normalize_i1_variant(params, model_name, validation)
    variant_defaults = HIDREAM_I1_PROFILES[profile_id]
    model_loader = _select_loader_node(plan, "family_model")
    clip_loader = _select_loader_node(plan, "text_encoder")
    vae_loader = _select_loader_node(plan, "vae") or "VAELoader"
    if not model_loader or not clip_loader or not vae_loader:
        validation.errors.append("HiDream-I1 LanPaint could not resolve model, quadruple text-encoder and VAE loaders from live capabilities."); validation.ok = False
    required_signatures = {
        model_loader: (("gguf_name",) if model_loader == "LoaderGGUF" else ("unet_name",)),
        clip_loader: ("clip_name1", "clip_name2", "clip_name3", "clip_name4"),
        vae_loader: ("vae_name",),
        "ModelSamplingSD3": ("model", "shift"),
        "CLIPTextEncode": ("clip", "text"),
    }
    signature_mismatches = _signature_gate(validation, backend, required_signatures)

    selection_target = str(params.get("inpaint_selection_target") or params.get("inpaint_mask_target") or "masked_area").lower()
    invert_mask = selection_target in {"not_masked", "not_masked_area", "inverse", "unmasked", "outside_mask"}
    capability_report = evaluate_lanpaint_route_capabilities(
        backend, provider_id=provider_id, family=family, loader=loader, mode="inpaint", engine="lanpaint",
        selected_assets=selected, require_invert_mask=invert_mask, require_model_clip_lora=bool(base_loras),
    )
    for blocker in capability_report.get("blockers", []):
        validation.errors.append(f"LanPaint capability gate [{blocker.get('code') or 'blocked'}]: {blocker.get('message') or 'Route unavailable.'}"); validation.ok = False
    for warning in capability_report.get("warnings", []):
        validation.warnings.append(f"LanPaint capability notice [{warning.get('code') or 'notice'}]: {warning.get('message') or ''}".strip())

    adapter_spatial = _mapping(adapter.get("spatial")); crop = _mapping(adapter_spatial.get("crop")); processing = _mapping(crop.get("processing_size")); masks = _mapping(adapter_spatial.get("mask")); sample_mask = _mapping(masks.get("sampling")); stitch_mask = _mapping(masks.get("stitch")); stitch_policy = _mapping(adapter_spatial.get("stitch")); sampler_defaults = _mapping(_mapping(adapter.get("sampler")).get("defaults")); latent = _mapping(adapter.get("latent")); lora_policy = _mapping(adapter.get("lora"))
    padding = int(crop.get("padding_px") or 128); width = int(processing.get("width") or 1024); height = int(processing.get("height") or 1024); resize_method = str(crop.get("resize_method") or "lanczos"); restore_method = str(stitch_policy.get("resize_method") or resize_method)
    sample_expand = int(sample_mask.get("expand_px") or 40); sample_blur = float(sample_mask.get("blur_radius") or 28.0); stitch_expand = int(stitch_mask.get("expand_px") or 48); stitch_blur = float(stitch_mask.get("blur_radius") or 9.0)
    # The selected I1 profile owns its official defaults. UI fallback fields are
    # injected during normalization, so only values present in the original job
    # may override full/dev/fast profile semantics.
    steps = int(_param(requested_params, "steps", "lanpaint_steps", default=variant_defaults["steps"])); cfg = float(_param(requested_params, "cfg", "lanpaint_cfg", default=variant_defaults["cfg"])); shift = float(_param(requested_params, "hidream_sd3_shift", "sd3_shift", default=variant_defaults["shift"])); sampler_name = str(_param(requested_params, "sampler", "lanpaint_sampler", default=variant_defaults["sampler"])); scheduler = str(_param(requested_params, "scheduler", "lanpaint_scheduler", default=variant_defaults["scheduler"])); denoise = float(_param(requested_params, "denoise", "lanpaint_denoise", default=sampler_defaults.get("denoise") if sampler_defaults.get("denoise") is not None else 1.0)); batch_count = int(_param(requested_params, "batch_count", "batch_size", default=1)); thinking = int(_param(requested_params, "lanpaint_thinking_steps", default=sampler_defaults.get("lanpaint_thinking_steps") if sampler_defaults.get("lanpaint_thinking_steps") is not None else 5)); prompt_mode = str(_param(requested_params, "lanpaint_prompt_mode", default=sampler_defaults.get("prompt_mode") or "image_first"))
    requested_seed = int(_param(params, "requested_seed", "seed", default=-1)); seed = int(_param(params, "actual_seed", "seed", default=requested_seed)); seed = int(time.time()*1000)%2147483647 if seed < 0 else seed
    conditioning_mode = normalize_prompt_conditioning_mode(params.get("prompt_conditioning_mode", params.get("clamp", "raw"))); conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode); positive_text = conditioning.get("effective_positive") or job.prompt or ""; negative_text = conditioning.get("effective_negative") or job.negative_prompt or ""

    workflow: dict[str, Any] = {}; node_roles: dict[str, str] = {}
    def add(node_id: int, cls: str, inputs: dict[str, Any], role: str):
        workflow[str(node_id)] = {"class_type": cls, "inputs": inputs}; node_roles[str(node_id)] = role
    sampler_id = None; model_id = None; clip_id = None
    if validation.ok:
        add(1, "LoadImage", {"image": source_name, "upload": "image"}, "source_image"); add(2, "LoadImage", {"image": mask_name, "upload": "image"}, "mask_image"); add(3, "ImageToMask", {"image": ["2",0], "channel": "red"}, "mask_convert")
        next_id=4; mask_ref=["3",0]
        if invert_mask: add(next_id,"InvertMask",{"mask":mask_ref},"mask_invert"); mask_ref=[str(next_id),0]; next_id+=1
        crop_id=next_id; add(crop_id,"CropByMask",{"image":["1",0],"mask":mask_ref,"padding":padding},"crop_by_mask"); next_id+=1
        resize_inputs={"image":[str(crop_id),0],"mask":[str(crop_id),1],"width":width,"height":height,"upscale_method":resize_method,"keep_proportion":"stretch","pad_color":"0, 0, 0","crop_position":"center","divisible_by":16}; _optional_input(resize_inputs,backend,"ImageResizeKJv2","device","cpu")
        resize_id=next_id; add(resize_id,"ImageResizeKJv2",resize_inputs,"processing_resize"); next_id+=1
        grow_inputs={"mask":[str(resize_id),3],"expand":sample_expand,"incremental_expandrate":0.0,"tapered_corners":True,"flip_input":False,"blur_radius":sample_blur,"lerp_alpha":1.0,"decay_factor":1.0}; _optional_input(grow_inputs,backend,"GrowMaskWithBlur","fill_holes",False)
        sample_id=next_id; add(sample_id,"GrowMaskWithBlur",grow_inputs,"sampling_mask_refine"); next_id+=1
        vae_id=next_id; add(vae_id,vae_loader,{"vae_name":selected["vae"]},"hidream_vae_loader"); next_id+=1
        encode_id=next_id; add(encode_id,"VAEEncode",{"pixels":[str(resize_id),0],"vae":[str(vae_id),0]},"latent_encode"); next_id+=1
        noise_id=next_id; add(noise_id,"SetLatentNoiseMask",{"samples":[str(encode_id),0],"mask":[str(sample_id),0]},"latent_noise_mask"); next_id+=1
        latent_ref=[str(noise_id),0]
        if batch_count>1:
            repeat_id=next_id; add(repeat_id,"RepeatLatentBatch",{"samples":list(latent_ref),"amount":batch_count},"latent_batch_repeat"); latent_ref=[str(repeat_id),0]; next_id+=1
        model_id=next_id; add(model_id,model_loader,_model_inputs(model_loader,model_name),"hidream_model_loader"); next_id+=1
        clip_id=next_id; add(clip_id,clip_loader,{"clip_name1":selected["text_encoder"],"clip_name2":selected["text_encoder_2"],"clip_name3":selected["text_encoder_3"],"clip_name4":selected["text_encoder_4"]},"hidream_quadruple_text_encoder"); next_id+=1
        positive_id=next_id; add(positive_id,"CLIPTextEncode",{"clip":[str(clip_id),0],"text":positive_text},"positive_conditioning"); next_id+=1
        negative_id=next_id; add(negative_id,"CLIPTextEncode",{"clip":[str(clip_id),0],"text":negative_text},"negative_conditioning"); next_id+=1
        transform_id=next_id; add(transform_id,"ModelSamplingSD3",{"model":[str(model_id),0],"shift":shift},"family_model_transform"); next_id+=1
        sampler_id=next_id; add(sampler_id,"LanPaint_KSampler",{"model":[str(transform_id),0],"positive":[str(positive_id),0],"negative":[str(negative_id),0],"latent_image":latent_ref,"seed":seed,"steps":steps,"cfg":cfg,"sampler_name":sampler_name,"scheduler":scheduler,"denoise":denoise,"LanPaint_NumSteps":thinking,"LanPaint_PromptMode":prompt_mode,"LanPaint_Info":"LanPaint KSampler.","Inpainting_mode":"🖼️ Image Inpainting"},"lanpaint_sample"); next_id+=1
        decode_id=next_id; add(decode_id,"VAEDecode",{"samples":[str(sampler_id),0],"vae":[str(vae_id),0]},"latent_decode"); next_id+=1
        restore_inputs={"image":[str(decode_id),0],"mask":[str(sample_id),0],"width":[str(crop_id),4],"height":[str(crop_id),5],"upscale_method":restore_method,"keep_proportion":"stretch","pad_color":"0, 0, 0","crop_position":"center","divisible_by":2}; _optional_input(restore_inputs,backend,"ImageResizeKJv2","device","cpu")
        restore_id=next_id; add(restore_id,"ImageResizeKJv2",restore_inputs,"restore_crop_size"); next_id+=1
        stitch_inputs={"mask":[str(restore_id),3],"expand":stitch_expand,"incremental_expandrate":0.0,"tapered_corners":True,"flip_input":False,"blur_radius":stitch_blur,"lerp_alpha":1.0,"decay_factor":1.0}; _optional_input(stitch_inputs,backend,"GrowMaskWithBlur","fill_holes",False)
        stitch_id=next_id; add(stitch_id,"GrowMaskWithBlur",stitch_inputs,"stitch_mask_refine"); next_id+=1
        composite_id=next_id; add(composite_id,"ImageCompositeMasked",{"destination":["1",0],"source":[str(restore_id),0],"x":[str(crop_id),2],"y":[str(crop_id),3],"resize_source":False,"mask":[str(stitch_id),0]},"stitch_composite"); next_id+=1
        add(next_id,"PreviewImage",{"images":[str(composite_id),0]},"output_handoff")

    compatibility_key=f"hidream:{loader}:inpaint"; workflow_route_key=f"hidream:{loader}:inpaint:lanpaint"
    lora_route={"backend":provider_id,"provider_id":provider_id,"family":"hidream","loader":loader,"workflow_mode":"inpaint","mode":"inpaint","engine":"lanpaint","route_key":compatibility_key,"compatibility_route_key":compatibility_key,"workflow_route_key":workflow_route_key,"route_state":"experimental_available"}
    lora_profile=build_lora_patch_profile(route=lora_route,model_ref=[str(model_id),0] if validation.ok else None,clip_ref=[str(clip_id),0] if validation.ok else None,sampler_node_id=str(sampler_id or ""),sampler_model_input="model",loader_node_class=str(lora_policy.get("loader_node_class") or "LoraLoader"),requires_model=True,requires_clip=True,source="neo_app.providers.comfy_workflows.lanpaint_hidream.phase21",strategy="lora_loader_model_clip_consumer_rewire",patch_model_consumers=True,patch_clip_consumers=True,validated=False,notes=["Phase 21 HiDream-I1 LoRA compatibility is engine-independent; the compiler owns ModelSamplingSD3 and four-encoder graph anchors.","HunyuanVideo remains outside Image."])

    verified=[]
    slots=_mapping(_mapping(adapter.get("assets")).get("slots"))
    for slot_id,value in selected.items():
        verified.append(_verify_selected_asset(validation,backend,loader_id=loader,role_id=str(_mapping(slots.get(slot_id)).get("role_id") or ""),label=slot_id,selected=value))
    ui_state=deepcopy(ui_state); ui_state["capability"]=deepcopy(capability_report); ui_state["route"]["route_state"]=capability_report.get("status"); ui_state["route"]["selectable"]=bool(capability_report.get("selectable")); ui_state["route"]["capability_checked"]=bool(capability_report.get("discovery",{}).get("checked")); ui_state["route"]["capability_fingerprint"]=capability_report.get("capability_fingerprint"); ui_state["validation"]["capability_ok"]=bool(capability_report.get("executable")); fp=deepcopy(ui_state); fp.pop("state_fingerprint",None); ui_state["state_fingerprint"]=hashlib.sha256(json.dumps(fp,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()

    actual={**params,"inpaint_engine":"lanpaint","workflow_type":WORKFLOW_TYPE,"seed":seed,"actual_seed":seed,"requested_seed":requested_seed,"batch_count":batch_count,"source_image_name":source_name,"mask_image_name":mask_name,"diffusion_model":model_name if loader=="diffusion_model" else "","gguf_model":model_name if loader=="gguf" else "","hidream_variant":"HiDream-I1","hidream_i1_profile":profile_id,"hidream_clip_l":selected.get("text_encoder",""),"hidream_clip_g":selected.get("text_encoder_2",""),"hidream_t5xxl":selected.get("text_encoder_3",""),"hidream_llama_3_1_8b":selected.get("text_encoder_4",""),"vae":selected.get("vae",""),"steps":steps,"cfg":cfg,"denoise":denoise,"sampler":sampler_name,"scheduler":scheduler,"hidream_sd3_shift":shift,"prompt_conditioning_mode":conditioning_mode,"clamp":conditioning_mode,"lanpaint_route":{"route_family_id":ROUTE_FAMILY_ID,"route_key":workflow_route_key,"engine":"lanpaint","family":"hidream","loader":loader,"variant":"hidream_i1_quad_clip_crop_stitch_v1","family_variant":"HiDream-I1","profile":profile_id,"policy_id":policy.get("policy_id"),"compiler_id":COMPILER_ID,"graph_state":PHASE21_STATE},"lanpaint_controls":{"crop_padding":padding,"processing_size":{"width":width,"height":height},"resize_method":resize_method,"restore_resize_method":restore_method,"sampling_mask":{"expand":sample_expand,"blur":sample_blur},"stitch_mask":{"expand":stitch_expand,"blur":stitch_blur},"steps":steps,"cfg":cfg,"sampler":sampler_name,"scheduler":scheduler,"denoise":denoise,"thinking_steps":thinking,"prompt_mode":prompt_mode,"sd3_shift":shift},"lanpaint_ui_state":ui_state,"lanpaint_ui_state_fingerprint":ui_state.get("state_fingerprint"),"lanpaint_capability_report":capability_report,"lanpaint_capability_fingerprint":capability_report.get("capability_fingerprint"),"lanpaint_contract_fingerprint":route_contract.get("contract_fingerprint"),"lanpaint_compile_plan_fingerprint":plan.get("plan_fingerprint"),"lanpaint_family_adapter":adapter_snapshot(adapter),"lanpaint_family_adapter_id":identity.get("adapter_id"),"lanpaint_family_adapter_fingerprint":adapter.get("adapter_fingerprint"),"lanpaint_node_roles":node_roles,"lanpaint_selected_assets":verified,"lanpaint_phase21_signature_mismatches":signature_mismatches,"lanpaint_mask_target":"not_masked_area" if invert_mask else "masked_area","_neo_sampler_node_id":str(sampler_id or ""),"_neo_lora_patch_profile":lora_profile,"lanpaint_lora_route":lora_route,"lanpaint_lora_mode":"model_and_clip","lanpaint_lora_requested_rows":deepcopy(active_loras),"lanpaint_lora_base_graph_rows":deepcopy(base_loras),"lanpaint_lora_deferred_rows":[deepcopy(r) for r in active_loras if r not in base_loras],"_neo_lanpaint_phase7_ui_state":PHASE7_STATE,"_neo_lanpaint_phase8_capability_state":PHASE8_STATE,"_neo_lanpaint_phase11_state":PHASE11_STATE,"_neo_lanpaint_phase13_state":PHASE13_STATE,"_neo_lanpaint_phase21_state":PHASE21_STATE,"_neo_lanpaint_phase21_graph":bool(validation.ok),"hunyuan_image_route_state":"held_for_separate_verified_image_workflow","hunyuan_video_route_state":"held_for_video_workspace"}
    actual=refresh_lanpaint_replay_contract(actual,provider_id=provider_id,workflow_prompt=workflow)
    return CompiledJob(provider_id=provider_id,compile_status="compiled" if validation.ok else "mock_compiled",backend_payload={"provider_id":provider_id,"backend":"comfyui","base_url":base_url,"validation":model_to_dict(validation),"prompt":workflow,"client_id":f"neo-studio-v2-{uuid4().hex[:8]}","actual_params":actual,"runtime_progress_source":"comfyui.websocket_and_history","compile_route":route.as_dict(),"capabilities":capabilities,"backend_capabilities":backend,"lanpaint_compile_plan":plan,"lanpaint_route_capabilities":capability_report,"prompt_conditioning":conditioning,"phase_notes":["Phase 21 onboards HiDream-I1 full/dev/fast profiles through exact four-encoder safetensors and GGUF LanPaint adapters.","ModelSamplingSD3 shift and steps/CFG follow the selected I1 profile; E1/E1.1 and O1 are not reinterpreted as I1.","HunyuanVideo/T2V remains held for the Video workspace; the separate HunyuanImage family remains blocked pending a proven image LanPaint graph."]})


__all__=["HIDREAM_I1_PROFILES","PHASE21_STATE","SUPPORTED_HIDREAM_ROUTES","compile_lanpaint_hidream_i1_inpaint"]
