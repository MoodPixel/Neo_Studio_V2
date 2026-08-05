from __future__ import annotations

import hashlib
import json
import time
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.lanpaint_family_adapter import PHASE22_STATE, adapter_asset_candidates, adapter_snapshot, get_lanpaint_family_adapter
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_app.image.lanpaint_replay import refresh_lanpaint_replay_contract, validate_lanpaint_replay_request
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile

from .lanpaint import _active_lora_rows, _base_graph_lora_rows, _mapping, _mask_image_name, _param, _source_image_name, _verify_selected_asset

SUPPORTED_PHASE22_ROUTES={("anima","diffusion_model"),("anima","gguf"),("ideogram4","diffusion_model"),("ideogram4","gguf")}


def _node_map(backend: Mapping[str,Any]) -> dict[str,Any]:
    return dict(backend.get("object_info_node_inputs")) if isinstance(backend.get("object_info_node_inputs"),Mapping) else {}


def _require_nodes(validation: ProviderValidationResult, backend: Mapping[str,Any], names:list[str]) -> None:
    nodes=_node_map(backend)
    if not nodes:
        validation.errors.append("Phase 22 LanPaint requires live Comfy object_info discovery."); validation.ok=False; return
    for name in names:
        if name not in nodes:
            validation.errors.append(f"Phase 22 LanPaint requires Comfy node {name}."); validation.ok=False


def _model_loader(loader:str,backend:Mapping[str,Any])->str:
    if loader=="diffusion_model": return "UNETLoader"
    nodes=_node_map(backend)
    return "UnetLoaderGGUF" if "UnetLoaderGGUF" in nodes else "LoaderGGUF"


def _model_inputs(node:str,name:str)->dict[str,Any]:
    if node=="LoaderGGUF": return {"gguf_name":name}
    out={"unet_name":name}
    if node=="UNETLoader": out["weight_dtype"]="default"
    return out


def _selected(adapter:Mapping[str,Any], params:Mapping[str,Any], job_model:Any, validation:ProviderValidationResult, family:str)->dict[str,str]:
    labels={"model":f"{family} main diffusion model","text_encoder":f"{family} text encoder","vae":f"{family} VAE"}
    result={}
    for slot,values in adapter_asset_candidates(adapter,params,job_model=job_model).items():
        if slot in {"model","text_encoder","vae"}:
            result[slot]=str(require_explicit_asset_selection(validation,labels[slot],*values))
    if family=="ideogram4":
        result["unconditional_model"]=str(require_explicit_asset_selection(validation,"Ideogram 4 unconditional diffusion model",_param(params,"ideogram4_unconditional_model","unconditional_model","negative_model")))
    return result


def _asset_proof(validation:ProviderValidationResult,backend:Mapping[str,Any],adapter:Mapping[str,Any],loader:str,selected:Mapping[str,str],family:str)->list[dict[str,Any]]:
    slots=_mapping(_mapping(adapter.get("assets")).get("slots")); result=[]
    for slot in ("model","text_encoder","vae"):
        role=str(_mapping(slots.get(slot)).get("role_id") or "")
        result.append(_verify_selected_asset(validation,backend,loader_id=loader,role_id=role,label=slot,selected=str(selected.get(slot) or "")))
    if family=="ideogram4":
        role="ideogram4_unconditional_model" if loader=="diffusion_model" else "ideogram4_unconditional_model_gguf"
        result.append(_verify_selected_asset(validation,backend,loader_id=loader,role_id=role,label="unconditional_model",selected=str(selected.get("unconditional_model") or "")))
    return result


def _spatial(params:Mapping[str,Any], defaults:Mapping[str,Any])->dict[str,Any]:
    crop=_mapping(defaults.get("crop_policy")); mask=_mapping(defaults.get("mask_policy")); sampler=_mapping(defaults.get("sampler_policy")); stitch=_mapping(defaults.get("stitch_policy"))
    return {
      "padding":int(_param(params,"lanpaint_crop_padding","crop_padding",default=crop.get("padding_px",112)) or 112),
      "width":int(_param(params,"lanpaint_processing_width",default=_mapping(crop.get("processing_size")).get("width",1024)) or 1024),
      "height":int(_param(params,"lanpaint_processing_height",default=_mapping(crop.get("processing_size")).get("height",1024)) or 1024),
      "sample_expand":int(_param(params,"lanpaint_sampling_mask_expand",default=_mapping(mask.get("sampling")).get("expand_px",32)) or 32),
      "sample_blur":float(_param(params,"lanpaint_sampling_mask_blur",default=_mapping(mask.get("sampling")).get("blur_radius",24.0)) or 24.0),
      "stitch_expand":int(_param(params,"lanpaint_stitch_mask_expand",default=_mapping(mask.get("stitch")).get("expand_px",40)) or 40),
      "stitch_blur":float(_param(params,"lanpaint_stitch_mask_blur",default=_mapping(mask.get("stitch")).get("blur_radius",8.0)) or 8.0),
      "steps":int(_param(params,"lanpaint_steps","steps",default=sampler.get("steps",30)) or 30),
      "cfg":float(_param(params,"lanpaint_cfg","cfg",default=sampler.get("cfg",4.0)) or 4.0),
      "thinking":int(_param(params,"lanpaint_thinking_steps",default=sampler.get("lanpaint_thinking_steps",5)) or 5),
      "sampler":str(_param(params,"lanpaint_sampler","sampler",default=sampler.get("sampler_name","euler")) or "euler"),
      "scheduler":str(_param(params,"lanpaint_scheduler","scheduler",default=sampler.get("scheduler","simple")) or "simple"),
      "denoise":float(_param(params,"lanpaint_denoise","denoise",default=sampler.get("denoise",1.0)) or 1.0),
      "prompt_mode":"Prompt First" if str(_param(params,"lanpaint_prompt_mode",default=sampler.get("prompt_mode","image_first"))).lower().replace("_"," ")=="prompt first" else "Image First",
      "restore_method":str(_param(params,"lanpaint_stitch_resize_method",default=stitch.get("resize_method","lanczos")) or "lanczos"),
      "sampler_contract":str(sampler.get("sampler_contract") or "basic"),
      "lanpaint_lambda":float(_param(params,"lanpaint_lambda",default=sampler.get("lanpaint_lambda",16.0)) or 16.0),
      "lanpaint_step_size":float(_param(params,"lanpaint_step_size",default=sampler.get("lanpaint_step_size",0.2)) or 0.2),
      "lanpaint_beta":float(_param(params,"lanpaint_beta",default=sampler.get("lanpaint_beta",1.0)) or 1.0),
      "lanpaint_friction":float(_param(params,"lanpaint_friction",default=sampler.get("lanpaint_friction",15.0)) or 15.0),
      "lanpaint_early_stop":int(_param(params,"lanpaint_early_stop",default=sampler.get("lanpaint_early_stop",1)) or 1),
      "lanpaint_inner_threshold":float(_param(params,"lanpaint_inner_threshold",default=0.0) or 0.0),
      "lanpaint_inner_patience":int(_param(params,"lanpaint_inner_patience",default=1) or 1),
    }


def _base_graph(source:str,mask:str,s:Mapping[str,Any])->tuple[dict[str,Any],int]:
    g={
      "1":{"class_type":"LoadImage","inputs":{"image":source}},
      "2":{"class_type":"LoadImage","inputs":{"image":mask}},
      "3":{"class_type":"ImageToMask","inputs":{"image":["2",0],"channel":"red"}},
      "4":{"class_type":"CropByMask","inputs":{"image":["1",0],"mask":["3",0],"padding":s["padding"]}},
      "5":{"class_type":"ImageResizeKJv2","inputs":{"image":["4",0],"mask":["4",1],"width":s["width"],"height":s["height"],"upscale_method":"lanczos","keep_proportion":"stretch","pad_color":"0, 0, 0","crop_position":"center","divisible_by":16,"device":"cpu"}},
      "6":{"class_type":"GrowMaskWithBlur","inputs":{"mask":["5",3],"expand":s["sample_expand"],"incremental_expandrate":0.0,"tapered_corners":True,"flip_input":False,"blur_radius":s["sample_blur"],"lerp_alpha":1.0,"decay_factor":1.0,"fill_holes":False}},
    }
    return g,7


def _finish(g:dict[str,Any],next_id:int,decode_ref:list[Any],vae_ref:list[Any],source_ref:list[Any],crop_ref:list[Any],mask_ref:list[Any],s:Mapping[str,Any])->None:
    g[str(next_id)]={"class_type":"VAEDecode","inputs":{"samples":decode_ref,"vae":vae_ref}}; decoded=str(next_id); next_id+=1
    g[str(next_id)]={"class_type":"ImageResizeKJv2","inputs":{"image":[decoded,0],"mask":mask_ref,"width":[crop_ref[0],4],"height":[crop_ref[0],5],"upscale_method":s["restore_method"],"keep_proportion":"stretch","pad_color":"0, 0, 0","crop_position":"center","divisible_by":2,"device":"cpu"}}; restored=str(next_id); next_id+=1
    g[str(next_id)]={"class_type":"GrowMaskWithBlur","inputs":{"mask":[restored,3],"expand":s["stitch_expand"],"incremental_expandrate":0.0,"tapered_corners":True,"flip_input":False,"blur_radius":s["stitch_blur"],"lerp_alpha":1.0,"decay_factor":1.0,"fill_holes":False}}; stitch=str(next_id); next_id+=1
    g[str(next_id)]={"class_type":"ImageCompositeMasked","inputs":{"destination":source_ref,"source":[restored,0],"x":[crop_ref[0],2],"y":[crop_ref[0],3],"resize_source":False,"mask":[stitch,0]}}; comp=str(next_id); next_id+=1
    g[str(next_id)]={"class_type":"PreviewImage","inputs":{"images":[comp,0]}}


def compile_lanpaint_phase22_inpaint(*,provider_id:str,base_url:str,job:NeoJob,validation:ProviderValidationResult,route:CompileRoute,capabilities:dict[str,Any],backend_capabilities:dict[str,Any]|None=None)->CompiledJob:
    family=str(route.family or job.family or ""); loader=str(route.loader or job.loader or "diffusion_model"); params=dict(job.params or {}); backend=dict(backend_capabilities or {})
    if (family,loader) not in SUPPORTED_PHASE22_ROUTES:
        validation.errors.append(f"Phase 22 has no LanPaint route for {family}+{loader}."); validation.ok=False
    source,mask=_source_image_name(params),_mask_image_name(params)
    if not source: validation.errors.append(f"{family} LanPaint requires a source image."); validation.ok=False
    if not mask: validation.errors.append(f"{family} LanPaint requires a mask image."); validation.ok=False
    for error in validate_lanpaint_replay_request(params,provider_id=provider_id,family=family,loader=loader,mode="inpaint",engine="lanpaint"):
        validation.errors.append(error); validation.ok=False
    adapter=get_lanpaint_family_adapter(family,loader=loader,provider_id=provider_id)
    if not adapter or not _mapping(adapter.get("binding")).get("selectable"):
        validation.errors.append(f"Phase 22 requires a complete bound {family} LanPaint adapter."); validation.ok=False
    policy=get_lanpaint_family_policy(family, loader=loader, provider_id=provider_id) or {}
    defaults=_mapping(policy.get("route_defaults")); s=_spatial(params,defaults)
    selected=_selected(adapter,params,job.model,validation,family)
    model_loader=_model_loader(loader,backend)
    common=["LoadImage","ImageToMask","CropByMask","ImageResizeKJv2","GrowMaskWithBlur",model_loader,"CLIPLoader","VAELoader","CLIPTextEncode","VAEEncode","SetLatentNoiseMask","VAEDecode","ImageCompositeMasked","PreviewImage"]
    if family=="anima": common += ["LanPaint_KSampler"]
    else: common += ["ConditioningZeroOut","RandomNoise","KSamplerSelect","Ideogram4Scheduler","DualModelGuider","LanPaint_SamplerCustomAdvanced"]
    _require_nodes(validation,backend,common)
    proof=_asset_proof(validation,backend,adapter,loader,selected,family) if selected else []
    requested=int(_param(params,"requested_seed","seed",default=-1) or -1); seed=int(_param(params,"actual_seed","seed",default=requested) or requested)
    if seed<0: seed=int(time.time()*1000)%2147483647
    conditioning_mode=normalize_prompt_conditioning_mode(_param(params,"prompt_conditioning_mode","clamp",default="raw")); conditioning=condition_prompt_pair(job.prompt or "",job.negative_prompt or "",conditioning_mode)
    g,next_id=_base_graph(source,mask,s) if validation.ok else ({},7)
    model_id=clip_id=vae_id=sampler_id=""
    if validation.ok:
        model_id=str(next_id); g[model_id]={"class_type":model_loader,"inputs":_model_inputs(model_loader,selected["model"])}; next_id+=1
        if family=="ideogram4":
            uncond_id=str(next_id); g[uncond_id]={"class_type":model_loader,"inputs":_model_inputs(model_loader,selected["unconditional_model"])}; next_id+=1
        clip_id=str(next_id); g[clip_id]={"class_type":"CLIPLoader","inputs":{"clip_name":selected["text_encoder"],"type":"stable_diffusion" if family=="anima" else "ideogram4","device":"default"}}; next_id+=1
        vae_id=str(next_id); g[vae_id]={"class_type":"VAELoader","inputs":{"vae_name":selected["vae"]}}; next_id+=1
        pos_id=str(next_id); g[pos_id]={"class_type":"CLIPTextEncode","inputs":{"clip":[clip_id,0],"text":conditioning.get("effective_positive") or job.prompt or ""}}; next_id+=1
        if family=="anima":
            neg_id=str(next_id); g[neg_id]={"class_type":"CLIPTextEncode","inputs":{"clip":[clip_id,0],"text":conditioning.get("effective_negative") or job.negative_prompt or ""}}; next_id+=1
        else:
            neg_id=str(next_id); g[neg_id]={"class_type":"ConditioningZeroOut","inputs":{"conditioning":[pos_id,0]}}; next_id+=1
        enc_id=str(next_id); g[enc_id]={"class_type":"VAEEncode","inputs":{"pixels":["5",0],"vae":[vae_id,0]}}; next_id+=1
        noise_mask_id=str(next_id); g[noise_mask_id]={"class_type":"SetLatentNoiseMask","inputs":{"samples":[enc_id,0],"mask":["6",0]}}; next_id+=1
        if family=="anima":
            sampler_id=str(next_id); g[sampler_id]={"class_type":"LanPaint_KSampler","inputs":{"model":[model_id,0],"positive":[pos_id,0],"negative":[neg_id,0],"latent_image":[noise_mask_id,0],"seed":seed,"steps":s["steps"],"cfg":s["cfg"],"sampler_name":s["sampler"],"scheduler":s["scheduler"],"denoise":s["denoise"],"LanPaint_NumSteps":s["thinking"],"LanPaint_PromptMode":s["prompt_mode"],"LanPaint_Info":"LanPaint KSampler.","Inpainting_mode":"🖼️ Image Inpainting"}}; next_id+=1
            sample_ref=[sampler_id,0]
        else:
            noise_id=str(next_id); g[noise_id]={"class_type":"RandomNoise","inputs":{"noise_seed":seed}}; next_id+=1
            sampler_select=str(next_id); g[sampler_select]={"class_type":"KSamplerSelect","inputs":{"sampler_name":s["sampler"]}}; next_id+=1
            sigma_id=str(next_id); g[sigma_id]={"class_type":"Ideogram4Scheduler","inputs":{"steps":s["steps"],"width":s["width"],"height":s["height"],"mu":float(_param(params,"ideogram4_mu",default=0.5) or 0.5),"std":float(_param(params,"ideogram4_std",default=1.75) or 1.75)}}; next_id+=1
            guider_id=str(next_id); g[guider_id]={"class_type":"DualModelGuider","inputs":{"model":[model_id,0],"positive":[pos_id,0],"model_negative":[uncond_id,0],"negative":[neg_id,0],"cfg":s["cfg"]}}; next_id+=1
            sampler_id=str(next_id); g[sampler_id]={"class_type":"LanPaint_SamplerCustomAdvanced","inputs":{"noise":[noise_id,0],"guider":[guider_id,0],"sampler":[sampler_select,0],"sigmas":[sigma_id,0],"latent_image":[noise_mask_id,0],"LanPaint_NumSteps":s["thinking"],"LanPaint_Lambda":s["lanpaint_lambda"],"LanPaint_StepSize":s["lanpaint_step_size"],"LanPaint_Beta":s["lanpaint_beta"],"LanPaint_Friction":s["lanpaint_friction"],"LanPaint_PromptMode":s["prompt_mode"],"LanPaint_EarlyStop":s["lanpaint_early_stop"],"LanPaint_Info":"LanPaint Custom Sampler Adv.","LanPaint_InnerThreshold":s["lanpaint_inner_threshold"],"LanPaint_InnerPatience":s["lanpaint_inner_patience"]}}; next_id+=1
            sample_ref=[sampler_id,0]
        _finish(g,next_id,sample_ref,[vae_id,0],["1",0],["4",0],["6",0],s)
    compatibility=f"{family}:{loader}:inpaint"; workflow_key=f"{compatibility}:lanpaint"
    lora_route={"backend":provider_id,"provider_id":provider_id,"family":family,"loader":loader,"workflow_mode":"inpaint","mode":"inpaint","engine":"lanpaint","route_key":compatibility,"compatibility_route_key":compatibility,"workflow_route_key":workflow_key,"route_state":"experimental_available"}
    if family=="anima":
        lora_profile=build_lora_patch_profile(route=lora_route,model_ref=[model_id,0] if validation.ok else None,clip_ref=[clip_id,0] if validation.ok else None,sampler_node_id=sampler_id,sampler_model_input="model",loader_node_class="LoraLoaderModelOnly",requires_model=True,requires_clip=False,source="neo_app.providers.comfy_workflows.lanpaint_phase22.anima",strategy="lora_loader_model_only_consumer_rewire",patch_model_consumers=True,patch_clip_consumers=False,validated=False,notes=["Phase 22 Anima LoRA is model-only and engine-independent."])
    else:
        lora_profile=build_lora_patch_profile(route=lora_route,model_ref=[model_id,0] if validation.ok else None,clip_ref=[clip_id,0] if validation.ok else None,sampler_node_id=sampler_id,sampler_model_input="guider",loader_node_class="LoraLoaderModelOnly",requires_model=True,requires_clip=False,source="neo_app.providers.comfy_workflows.lanpaint_phase22.ideogram4",strategy="none",validated=False,notes=["Ideogram 4 dual-model LoRA patching is not proven; explicit LoRA requests fail closed."])
    active=_active_lora_rows(job.extensions,params); base=_base_graph_lora_rows(active)
    actual={**params,"family":family,"loader":loader,"inpaint_engine":"lanpaint","seed":seed,"actual_seed":seed,"requested_seed":requested,"source_image_name":source,"mask_image_name":mask,"diffusion_model":selected.get("model","") if loader=="diffusion_model" else "","gguf_model":selected.get("model","") if loader=="gguf" else "","text_encoder_1":selected.get("text_encoder",""),"vae":selected.get("vae",""),"ideogram4_unconditional_model":selected.get("unconditional_model","") if family=="ideogram4" else "","steps":s["steps"],"cfg":s["cfg"],"lanpaint_route":{"route_key":workflow_key,"family":family,"loader":loader,"engine":"lanpaint","variant":_mapping(adapter.get("binding")).get("graph_profile"),"compiler_id":"comfy.lanpaint.family_aware.v1"},"lanpaint_controls":s,"lanpaint_family_adapter":adapter_snapshot(adapter),"lanpaint_family_adapter_id":_mapping(adapter.get("identity")).get("adapter_id"),"lanpaint_family_adapter_fingerprint":adapter.get("adapter_fingerprint"),"lanpaint_selected_assets":proof,"_neo_sampler_node_id":sampler_id,"_neo_lora_patch_profile":lora_profile,"lanpaint_lora_route":lora_route,"lanpaint_lora_mode":"model_only" if family=="anima" else "blocked_dual_model","lanpaint_lora_requested_rows":deepcopy(active),"lanpaint_lora_base_graph_rows":deepcopy(base),"_neo_lanpaint_phase22_state":PHASE22_STATE,"_neo_lanpaint_phase22_graph":bool(validation.ok)}
    actual=refresh_lanpaint_replay_contract(actual,provider_id=provider_id,workflow_prompt=g)
    return CompiledJob(provider_id=provider_id,compile_status="compiled" if validation.ok else "mock_compiled",backend_payload={"provider_id":provider_id,"backend":"comfyui","base_url":base_url,"validation":model_to_dict(validation),"prompt":g,"client_id":f"neo-studio-v2-{uuid4().hex[:8]}","actual_params":actual,"runtime_progress_source":"comfyui.websocket_and_history","compile_route":route.as_dict(),"capabilities":capabilities,"backend_capabilities":backend,"phase_notes":["Phase 22 onboards Anima and Ideogram 4 as separate image families before workflow activation.","Anima uses basic LanPaint KSampler and model-only LoRA.","Ideogram 4 uses paired models and LanPaint_SamplerCustomAdvanced; img2img and LoRA remain fail-closed where unproven."]})


__all__=["PHASE22_STATE","SUPPORTED_PHASE22_ROUTES","compile_lanpaint_phase22_inpaint"]
