from __future__ import annotations

import time
from copy import deepcopy
from typing import Any, Mapping
from uuid import uuid4

from neo_app.core.pydantic_compat import model_to_dict
from neo_app.image.prompt_conditioning import condition_prompt_pair, normalize_prompt_conditioning_mode
from neo_app.models.asset_selection import require_explicit_asset_selection
from neo_app.providers.compile_router import CompileRoute
from neo_app.providers.schema import CompiledJob, NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.patch_profile import build_lora_patch_profile

PHASE22_STATE = "anima_ideogram4_family_onboarding"


def _param(params: Mapping[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        value = params.get(name)
        if value not in (None, ""):
            return value
    return default


def _image_name(value: Any) -> str:
    if isinstance(value, Mapping):
        value = value.get("name") or value.get("filename") or value.get("path") or value.get("file")
    return str(value or "").strip().split("/")[-1].split("\\")[-1]


def _node_map(backend: Mapping[str, Any]) -> Mapping[str, Any]:
    return backend.get("object_info_node_inputs") if isinstance(backend.get("object_info_node_inputs"), Mapping) else {}


def _require_nodes(validation: ProviderValidationResult, backend: Mapping[str, Any], names: list[str]) -> None:
    available = _node_map(backend)
    if not available:
        validation.errors.append("Phase 22 requires live Comfy object_info before compiling Anima or Ideogram 4.")
        validation.ok = False
        return
    for name in names:
        if name not in available:
            validation.errors.append(f"Phase 22 requires Comfy node {name}.")
            validation.ok = False


def _model_loader(loader: str, backend: Mapping[str, Any]) -> str:
    if loader == "diffusion_model":
        return "UNETLoader"
    available = _node_map(backend)
    return "UnetLoaderGGUF" if "UnetLoaderGGUF" in available else "LoaderGGUF"


def _model_inputs(node: str, name: str) -> dict[str, Any]:
    if node == "LoaderGGUF":
        return {"gguf_name": name}
    result: dict[str, Any] = {"unet_name": name}
    if node == "UNETLoader":
        result["weight_dtype"] = "default"
    return result


def _seed(params: Mapping[str, Any]) -> tuple[int, int]:
    requested = int(_param(params, "requested_seed", "seed", default=-1))
    actual = int(_param(params, "actual_seed", "seed", default=requested))
    if actual < 0:
        actual = int(time.time() * 1000) % 2147483647
    return requested, actual


def _compiled(*, provider_id: str, base_url: str, route: CompileRoute, validation: ProviderValidationResult, workflow: dict[str, Any], actual: dict[str, Any], capabilities: dict[str, Any], backend: dict[str, Any], notes: list[str]) -> CompiledJob:
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
            "actual_params": actual,
            "runtime_progress_source": "comfyui.websocket_and_history",
            "compile_route": route.as_dict(),
            "capabilities": capabilities,
            "backend_capabilities": backend,
            "phase_notes": notes,
        },
    )


def compile_anima_image(*, provider_id: str, base_url: str, job: NeoJob, validation: ProviderValidationResult, route: CompileRoute, capabilities: dict[str, Any], backend_capabilities: dict[str, Any] | None = None) -> CompiledJob:
    params = dict(job.params or {})
    backend = dict(backend_capabilities or {})
    loader = str(route.loader or job.loader or "diffusion_model")
    mode = str(route.mode or job.mode or "txt2img")
    if mode not in {"txt2img", "img2img"}:
        validation.errors.append("Anima native compiler supports txt2img and img2img only; inpaint must use the LanPaint engine.")
        validation.ok = False
    model_name = str(require_explicit_asset_selection(validation, "Anima Base v1 diffusion model", job.model, _param(params, "diffusion_model", "gguf_model", "model")))
    clip_name = str(require_explicit_asset_selection(validation, "Anima Qwen3 0.6B text encoder", _param(params, "anima_text_encoder", "qwen3_06b_text_encoder", "text_encoder_1", "clip_name")))
    vae_name = str(require_explicit_asset_selection(validation, "Anima Qwen Image VAE", _param(params, "anima_vae", "qwen_image_vae", "vae")))
    source_name = _image_name(_param(params, "comfy_source_image_name", "source_image_name", "source_image", "image"))
    if mode == "img2img" and not source_name:
        validation.errors.append("Anima img2img requires Image 1 / source image.")
        validation.ok = False
    model_loader = _model_loader(loader, backend)
    required = [model_loader, "CLIPLoader", "VAELoader", "CLIPTextEncode", "KSampler", "VAEDecode", "PreviewImage"]
    required.append("EmptyLatentImage" if mode == "txt2img" else "LoadImage")
    if mode == "img2img": required.append("VAEEncode")
    _require_nodes(validation, backend, required)
    conditioning_mode = normalize_prompt_conditioning_mode(_param(params, "prompt_conditioning_mode", "clamp", default="raw"))
    conditioning = condition_prompt_pair(job.prompt or "", job.negative_prompt or "", conditioning_mode)
    requested_seed, seed = _seed(params)
    steps = int(_param(params, "steps", default=30))
    cfg = float(_param(params, "cfg", default=4.0))
    denoise = float(_param(params, "denoise", default=0.75 if mode == "img2img" else 1.0))
    width = int(_param(params, "width", default=1024))
    height = int(_param(params, "height", default=1024))
    batch_count = int(_param(params, "batch_count", "batch_size", default=1))
    workflow: dict[str, Any] = {}
    if validation.ok:
        workflow["1"] = {"class_type": model_loader, "inputs": _model_inputs(model_loader, model_name)}
        workflow["2"] = {"class_type": "CLIPLoader", "inputs": {"clip_name": clip_name, "type": "stable_diffusion", "device": "default"}}
        workflow["3"] = {"class_type": "VAELoader", "inputs": {"vae_name": vae_name}}
        workflow["4"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": conditioning.get("effective_positive") or job.prompt or ""}}
        workflow["5"] = {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": conditioning.get("effective_negative") or job.negative_prompt or ""}}
        if mode == "txt2img":
            workflow["6"] = {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": batch_count}}
            latent_ref = ["6", 0]
            sampler_id = "7"
        else:
            workflow["6"] = {"class_type": "LoadImage", "inputs": {"image": source_name}}
            workflow["7"] = {"class_type": "VAEEncode", "inputs": {"pixels": ["6", 0], "vae": ["3", 0]}}
            latent_ref = ["7", 0]
            sampler_id = "8"
            if batch_count > 1:
                workflow["8"] = {"class_type": "RepeatLatentBatch", "inputs": {"samples": list(latent_ref), "amount": batch_count}}
                latent_ref = ["8", 0]
                sampler_id = "9"
        workflow[sampler_id] = {"class_type": "KSampler", "inputs": {"model": ["1", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": latent_ref, "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": str(_param(params, "sampler", default="euler")), "scheduler": str(_param(params, "scheduler", default="simple")), "denoise": denoise}}
        decode_id = str(int(sampler_id)+1); preview_id = str(int(sampler_id)+2)
        workflow[decode_id] = {"class_type": "VAEDecode", "inputs": {"samples": [sampler_id, 0], "vae": ["3", 0]}}
        workflow[preview_id] = {"class_type": "PreviewImage", "inputs": {"images": [decode_id, 0]}}
    lora_route={"backend":provider_id,"provider_id":provider_id,"family":"anima","loader":loader,"workflow_mode":mode,"mode":mode,"engine":"native","route_key":f"anima:{loader}:{mode}","compatibility_route_key":f"anima:{loader}:{mode}","workflow_route_key":f"anima:{loader}:{mode}:native","route_state":"experimental_available"}
    lora_profile=build_lora_patch_profile(route=lora_route, model_ref=["1",0] if validation.ok else None, clip_ref=["2",0] if validation.ok else None, sampler_node_id=sampler_id if validation.ok else "", sampler_model_input="model", loader_node_class="LoraLoaderModelOnly", requires_model=True, requires_clip=False, source="neo_app.providers.comfy_workflows.phase22_families.anima", strategy="lora_loader_model_only_consumer_rewire", patch_model_consumers=True, patch_clip_consumers=False, validated=False, notes=["Anima official Turbo LoRA is model-only; Phase 22 keeps engine-independent model-only patch anchors."])
    actual={**params,"family":"anima","loader":loader,"mode":mode,"seed":seed,"actual_seed":seed,"requested_seed":requested_seed,"diffusion_model":model_name if loader=="diffusion_model" else "","gguf_model":model_name if loader=="gguf" else "","anima_text_encoder":clip_name,"anima_vae":vae_name,"source_image_name":source_name,"steps":steps,"cfg":cfg,"denoise":denoise,"_neo_sampler_node_id":sampler_id if validation.ok else "","_neo_lora_patch_profile":lora_profile,"_neo_phase22_state":PHASE22_STATE,"phase22_route_proof":{"family":"anima","graph":"standard_ksampler","img2img_supported":True,"gguf_scope":"diffusion_model_only"}}
    return _compiled(provider_id=provider_id,base_url=base_url,route=route,validation=validation,workflow=workflow,actual=actual,capabilities=capabilities,backend=backend,notes=["Phase 22 adds Anima Base v1 txt2img and img2img for safetensors and GGUF model loaders.","Anima uses Qwen3 0.6B conditioning, Qwen Image VAE and model-only LoRA patching."])


def compile_ideogram4_txt2img(*, provider_id: str, base_url: str, job: NeoJob, validation: ProviderValidationResult, route: CompileRoute, capabilities: dict[str, Any], backend_capabilities: dict[str, Any] | None = None) -> CompiledJob:
    params=dict(job.params or {}); backend=dict(backend_capabilities or {}); loader=str(route.loader or job.loader or "diffusion_model"); mode=str(route.mode or job.mode or "txt2img")
    if mode != "txt2img":
        validation.errors.append("Ideogram 4 Phase 22 native compiler supports txt2img only. Img2img remains held; inpaint must use LanPaint custom advanced."); validation.ok=False
    main=str(require_explicit_asset_selection(validation,"Ideogram 4 main diffusion model",job.model,_param(params,"ideogram4_main_model","diffusion_model","gguf_model","model")))
    uncond=str(require_explicit_asset_selection(validation,"Ideogram 4 unconditional diffusion model",_param(params,"ideogram4_unconditional_model","unconditional_model","negative_model")))
    clip=str(require_explicit_asset_selection(validation,"Ideogram 4 Qwen3-VL text encoder",_param(params,"ideogram4_text_encoder","qwen3_vl_text_encoder","text_encoder_1","clip_name")))
    vae=str(require_explicit_asset_selection(validation,"Ideogram 4 Flux 2 VAE",_param(params,"ideogram4_vae","flux2_vae","vae")))
    model_loader=_model_loader(loader,backend)
    required=[model_loader,"CLIPLoader","VAELoader","CLIPTextEncode","ConditioningZeroOut","EmptyFlux2LatentImage","RandomNoise","KSamplerSelect","Ideogram4Scheduler","DualModelGuider","SamplerCustomAdvanced","VAEDecode","PreviewImage"]
    _require_nodes(validation,backend,required)
    requested_seed,seed=_seed(params); width=int(_param(params,"width",default=1024)); height=int(_param(params,"height",default=1024)); steps=int(_param(params,"steps",default=20)); cfg=float(_param(params,"cfg",default=4.0)); batch_count=int(_param(params,"batch_count","batch_size",default=1))
    conditioning_mode=normalize_prompt_conditioning_mode(_param(params,"prompt_conditioning_mode","clamp",default="raw")); conditioning=condition_prompt_pair(job.prompt or "",job.negative_prompt or "",conditioning_mode)
    workflow:dict[str,Any]={}
    if validation.ok:
        workflow={
          "1":{"class_type":model_loader,"inputs":_model_inputs(model_loader,main)},
          "2":{"class_type":model_loader,"inputs":_model_inputs(model_loader,uncond)},
          "3":{"class_type":"CLIPLoader","inputs":{"clip_name":clip,"type":"ideogram4","device":"default"}},
          "4":{"class_type":"VAELoader","inputs":{"vae_name":vae}},
          "5":{"class_type":"CLIPTextEncode","inputs":{"clip":["3",0],"text":conditioning.get("effective_positive") or job.prompt or ""}},
          "6":{"class_type":"ConditioningZeroOut","inputs":{"conditioning":["5",0]}},
          "7":{"class_type":"EmptyFlux2LatentImage","inputs":{"width":width,"height":height,"batch_size":batch_count}},
          "8":{"class_type":"RandomNoise","inputs":{"noise_seed":seed}},
          "9":{"class_type":"KSamplerSelect","inputs":{"sampler_name":str(_param(params,"sampler",default="euler"))}},
          "10":{"class_type":"Ideogram4Scheduler","inputs":{"steps":steps,"width":width,"height":height,"mu":float(_param(params,"ideogram4_mu",default=0.5)),"std":float(_param(params,"ideogram4_std",default=1.75))}},
          "11":{"class_type":"DualModelGuider","inputs":{"model":["1",0],"positive":["5",0],"model_negative":["2",0],"negative":["6",0],"cfg":cfg}},
          "12":{"class_type":"SamplerCustomAdvanced","inputs":{"noise":["8",0],"guider":["11",0],"sampler":["9",0],"sigmas":["10",0],"latent_image":["7",0]}},
          "13":{"class_type":"VAEDecode","inputs":{"samples":["12",0],"vae":["4",0]}},
          "14":{"class_type":"PreviewImage","inputs":{"images":["13",0]}},
        }
    lora_route={"backend":provider_id,"provider_id":provider_id,"family":"ideogram4","loader":loader,"workflow_mode":"generate","mode":"txt2img","engine":"native","route_key":f"ideogram4:{loader}:txt2img","compatibility_route_key":f"ideogram4:{loader}:txt2img","workflow_route_key":f"ideogram4:{loader}:txt2img:native","route_state":"experimental_available"}
    lora_profile=build_lora_patch_profile(route=lora_route,model_ref=["1",0] if validation.ok else None,clip_ref=["3",0] if validation.ok else None,sampler_node_id="11" if validation.ok else "",sampler_model_input="model",loader_node_class="LoraLoaderModelOnly",requires_model=True,requires_clip=False,source="neo_app.providers.comfy_workflows.phase22_families.ideogram4",strategy="none",validated=False,notes=["Ideogram 4 uses paired main/unconditional models. Phase 22 blocks LoRA until both branches have a proven patch policy."])
    actual={**params,"family":"ideogram4","loader":loader,"mode":"txt2img","seed":seed,"actual_seed":seed,"requested_seed":requested_seed,"ideogram4_main_model":main,"ideogram4_unconditional_model":uncond,"ideogram4_text_encoder":clip,"ideogram4_vae":vae,"steps":steps,"cfg":cfg,"_neo_sampler_node_id":"11" if validation.ok else "","_neo_lora_patch_profile":lora_profile,"_neo_phase22_state":PHASE22_STATE,"phase22_route_proof":{"family":"ideogram4","graph":"dual_model_custom_advanced","img2img_supported":False,"lora_state":"blocked_unproven_dual_model_patch"}}
    return _compiled(provider_id=provider_id,base_url=base_url,route=route,validation=validation,workflow=workflow,actual=actual,capabilities=capabilities,backend=backend,notes=["Phase 22 adds Ideogram 4 txt2img for paired safetensors and paired GGUF diffusion models.","Img2img remains held because no verified official local workflow was found.","LoRA remains fail-closed until both model branches have an explicit patch contract."])


__all__=["PHASE22_STATE","compile_anima_image","compile_ideogram4_txt2img"]
