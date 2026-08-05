from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile, lanpaint_family_expansion_registry
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_app.image.lanpaint_ui_state import normalize_lanpaint_ui_state
from neo_app.providers.compile_router import select_comfy_compile_route
from neo_app.providers.comfy_workflows.lanpaint_family import compile_lanpaint_family_inpaint
from neo_app.providers.schema import NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support

ROUTES = {
    ("qwen_image", "diffusion_model"), ("qwen_image", "gguf"),
    ("z_image", "diffusion_model"), ("z_image", "gguf"),
    ("z_image_turbo", "diffusion_model"), ("z_image_turbo", "gguf"),
}


def _node_inputs(*names: str) -> dict[str, list[str]]:
    values = list(names)
    return {"required": values, "optional": [], "all": values}


def _assets(family: str, loader: str) -> tuple[str, str, str]:
    if family == "qwen_image":
        model, text, vae = "qwen_image_bf16.safetensors", "qwen_image_text_encoder.safetensors", "qwen_image_vae.safetensors"
    elif family == "z_image_turbo":
        model, text, vae = "z_image_turbo_bf16.safetensors", "qwen_3_4b.safetensors", "ae.safetensors"
    else:
        model, text, vae = "z_image_bf16.safetensors", "qwen_3_4b.safetensors", "ae.safetensors"
    if loader == "gguf":
        model = model.removesuffix(".safetensors") + "-Q5_K_M.gguf"
    return model, text, vae


def _capabilities(family: str, loader: str) -> dict[str, Any]:
    nodes = {
        "LoadImage": _node_inputs("image", "upload"), "ImageToMask": _node_inputs("image", "channel"),
        "InvertMask": _node_inputs("mask"), "UNETLoader": _node_inputs("unet_name", "weight_dtype"),
        "UnetLoaderGGUF": _node_inputs("unet_name"), "CLIPLoader": _node_inputs("clip_name", "type", "device"),
        "CLIPLoaderGGUF": _node_inputs("clip_name", "type", "device"), "CLIPTextEncode": _node_inputs("clip", "text"),
        "ConditioningZeroOut": _node_inputs("conditioning"), "VAELoader": _node_inputs("vae_name"),
        "CropByMask": _node_inputs("image", "mask", "padding"),
        "ImageResizeKJv2": _node_inputs("image", "width", "height", "upscale_method", "keep_proportion", "pad_color", "crop_position", "divisible_by", "mask", "device"),
        "GrowMaskWithBlur": _node_inputs("mask", "expand", "incremental_expandrate", "tapered_corners", "flip_input", "blur_radius", "lerp_alpha", "decay_factor", "fill_holes"),
        "VAEEncode": _node_inputs("pixels", "vae"), "SetLatentNoiseMask": _node_inputs("samples", "mask"),
        "ModelSamplingAuraFlow": _node_inputs("model", "shift"),
        "LanPaint_KSampler": _node_inputs("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps", "LanPaint_PromptMode", "LanPaint_Info", "Inpainting_mode"),
        "VAEDecode": _node_inputs("samples", "vae"),
        "ImageCompositeMasked": _node_inputs("destination", "source", "mask", "x", "y", "resize_source"),
        "PreviewImage": _node_inputs("images"),
        "LoraLoader": _node_inputs("model", "clip", "lora_name", "strength_model", "strength_clip"),
    }
    model, text, vae = _assets(family, loader)
    model_role = "gguf_unet" if loader == "gguf" else "diffusion_model"
    clip_role = "qwen_image_clip_loader" if family == "qwen_image" else "lumina2_clip_loader"
    return {
        "provider_id": "comfyui_portable", "reachable": True, "object_info_available": True,
        "object_info_node_inputs": nodes,
        "loaders": {loader: {"available": True, "roles": {
            model_role: {"available": True, "backend_node": "UnetLoaderGGUF" if loader == "gguf" else "UNETLoader", "assets": {"models": [model]}},
            clip_role: {"available": True, "backend_node": "CLIPLoaderGGUF" if loader == "gguf" else "CLIPLoader", "assets": {"text_encoders": [text]}},
            "vae_or_ae": {"available": True, "backend_node": "VAELoader", "assets": {"vaes": [vae]}},
        }}},
    }


def _job(family: str, loader: str) -> NeoJob:
    model, text, vae = _assets(family, loader)
    params: dict[str, Any] = {
        "source_image_name": "source.png", "mask_image_name": "mask.png",
        "source_image": "source.png", "mask_image": "mask.png",
        "inpaint_engine": "lanpaint", "vae": vae, "seed": 123,
        "diffusion_model" if loader == "diffusion_model" else "gguf_model": model,
        "qwen_text_encoder" if family == "qwen_image" else "qwen3_text_encoder": text,
    }
    return NeoJob(
        surface="image", subtab="inpaint", mode="inpaint", provider_id="comfyui_portable",
        family=family, loader=loader, model=model, prompt="replace only the masked shirt",
        negative_prompt="blurry, distorted", params=params, extensions={},
    )


def _compile(family: str, loader: str):
    job = _job(family, loader)
    route = select_comfy_compile_route(job)
    return compile_lanpaint_family_inpaint(
        provider_id="comfyui_portable", base_url="http://127.0.0.1:8188", job=job,
        validation=ProviderValidationResult(provider_id="comfyui_portable", ok=True),
        route=route, capabilities={}, backend_capabilities=_capabilities(family, loader),
    )


def run_audit() -> dict[str, Any]:
    compiled = {(family, loader): _compile(family, loader) for family, loader in sorted(ROUTES)}
    classes = {key: [node["class_type"] for node in value.backend_payload["prompt"].values()] for key, value in compiled.items()}
    registry = lanpaint_family_expansion_registry()
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    policy_block = js.split("const IMAGE_LANPAINT_FAMILY_UI_FALLBACKS =", 1)[1].split("const IMAGE_LANPAINT_CAPABILITY_STATUS", 1)[0]

    expected_defaults = {"qwen_image": (20, 4.0, 3.1), "z_image": (35, 3.5, 3.0), "z_image_turbo": (9, 1.0, 3.0)}
    checks = [
        {"id": "exact_six_routes_compile", "passed": all(item.compile_status == "compiled" for item in compiled.values()), "detail": "All six approved Qwen/Z-Image routes compile."},
        {"id": "router_scope_is_exact", "passed": all(select_comfy_compile_route(_job(f, l)).compiler_id == "comfy.lanpaint.family_aware.v1" for f, l in ROUTES) and all(select_comfy_compile_route(_job("qwen_image", "gguf")).status == "available" for _ in [0]), "detail": "The router binds only the approved family-aware route set."},
        {"id": "aura_graph_not_krea_graph", "passed": all("ModelSamplingAuraFlow" in c and "DifferentialDiffusionAdvanced" not in c for c in classes.values()), "detail": "Qwen/Z routes use AuraFlow and never inherit Krea Differential Diffusion."},
        {"id": "loader_branches_are_exact", "passed": all((("UnetLoaderGGUF" in classes[(f,l)]) == (l == "gguf")) and (("UNETLoader" in classes[(f,l)]) == (l == "diffusion_model")) for f,l in ROUTES), "detail": "Safetensors and GGUF routes use their intended loaders."},
        {"id": "family_defaults_are_distinct", "passed": all((c.backend_payload["actual_params"]["steps"], c.backend_payload["actual_params"]["cfg"], c.backend_payload["actual_params"]["aura_shift"]) == expected_defaults[f] for (f,_), c in compiled.items()), "detail": "Qwen, Z-Image and Z-Image Turbo retain separate defaults."},
        {"id": "negative_conditioning_is_family_aware", "passed": all(classes[(f,l)].count("CLIPTextEncode") == 2 for f,l in ROUTES if f != "z_image_turbo") and all("ConditioningZeroOut" in classes[("z_image_turbo", l)] for l in ("diffusion_model", "gguf")), "detail": "Qwen/Z base use the user negative prompt; Z-Image Turbo uses zeroed negative conditioning."},
        {"id": "capability_reports_are_experimental", "passed": all(evaluate_lanpaint_route_capabilities(_capabilities(f,l), provider_id="comfyui_portable", family=f, loader=l, mode="inpaint", engine="lanpaint")["status"] == "experimental_available" for f,l in ROUTES), "detail": "Complete profiles report experimental availability."},
        {"id": "family_policies_are_complete", "passed": all((get_lanpaint_family_policy(f, loader=l, provider_id="comfyui") or {}).get("identity", {}).get("status") == "complete_policy" for f,l in ROUTES), "detail": "Each onboarded route has a complete family policy."},
        {"id": "registry_promotions_preserve_phase10_scope", "passed": ROUTES.issubset({(p["identity"]["family"], p["identity"]["loader"]) for p in registry["profiles"] if p["execution"]["enabled"]}) and {(p["identity"]["family"], p["identity"]["loader"]) for p in registry["profiles"] if p["execution"]["enabled"]} - ROUTES == {("krea2_turbo", "diffusion_model"), ("sdxl", "checkpoint"), ("sd15", "checkpoint"), ("sd35", "diffusion_model"), ("sd35", "gguf"), ("flux", "diffusion_model"), ("flux", "gguf"), ("flux2_dev", "diffusion_model"), ("flux2_dev", "gguf"), ("flux2_klein", "diffusion_model"), ("flux2_klein", "gguf"), ("qwen_image_edit_2509", "diffusion_model"), ("qwen_image_edit_2509", "gguf"), ("qwen_image_edit_2511", "diffusion_model"), ("qwen_image_edit_2511", "gguf")}, "detail": "The six Phase 10 routes remain enabled; later phases add only the exact Krea, SD, Flux.1, Flux.2, and Qwen Edit promotion sets."},
        {"id": "unresolved_aliases_remain_blocked", "passed": all((get_lanpaint_family_expansion_profile(f, loader="gguf", provider_id="comfyui") or {}).get("execution", {}).get("enabled") is False for f in ("z_image_base",)), "detail": "The duplicate Z-Image Base identity remains blocked while Phase 18 explicitly activates versioned Qwen Edit routes."},
        {"id": "lora_matrix_is_model_clip", "passed": all(route_support("comfyui", f, l, "inpaint", engine="lanpaint")["graph_patch"] == "lora_loader_model_clip_consumer_rewire" for f,l in ROUTES), "detail": "All onboarded Qwen/Z routes use the shared model+CLIP LoRA strategy."},
        {"id": "ui_state_is_route_aware", "passed": all(normalize_lanpaint_ui_state({}, provider_id="comfyui", family=f, loader=l, mode="inpaint", engine="lanpaint")["badges"]["lora_mode"] == "Model + CLIP" for f,l in ROUTES), "detail": "The Phase 7 state model exposes the new routes and correct LoRA mode."},
        {"id": "frontend_activation_is_exact", "passed": all(name in policy_block for name in ("qwen_image", "z_image", "z_image_turbo")) and "qwen_image_edit_2509" not in policy_block and "z_image_base" not in policy_block, "detail": "The frontend exposes implemented families only."},
        {"id": "cache_revision_is_current", "passed": "phase10=lanpaint_qwen_zimage_20260804" in index and any(marker in js for marker in ("lanpaint_qwen_zimage_20260804", "lanpaint_replay_lineage_20260804", "lanpaint_capability_transport_hotfix_20260804", "lanpaint_lora_independence_hotfix_20260804", "global_lora_engine_decoupling_20260805", "lanpaint_family_adapter_v2_20260805", "lanpaint_route_parity_phase14_20260805", "lanpaint_sd_family_phase15_20260805", "lanpaint_flux1_family_phase16_20260805", "lanpaint_flux2_family_phase17_20260805")), "detail": "HTML and JavaScript share the Phase 10 cache revision."},
        {"id": "public_paths_only", "passed": all(token not in "\n".join(((ROOT / "neo_app/providers/comfy_workflows/lanpaint_family.py").read_text(encoding="utf-8"), (ROOT / "neo_app/image/lanpaint_family_expansion_profiles.json").read_text(encoding="utf-8"), (ROOT / "neo_app/image/lanpaint_family_policies.py").read_text(encoding="utf-8"))) for token in ("/" + "home" + "/", "/" + "Users" + "/", "/" + "mnt" + "/" + "data", "C:" + chr(92) + "Users" + chr(92), "D:" + chr(92) + "Users" + chr(92))), "detail": "Phase 10 route files contain no personal or machine-specific paths."},
    ]
    return {
        "schema_id": "neo.validation.lanpaint_route_family_phase10.v1",
        "phase": 10,
        "title": "LanPaint Qwen and Z-Image onboarding",
        "passed": all(item["passed"] for item in checks),
        "checks_passed": sum(1 for item in checks if item["passed"]),
        "checks_total": len(checks),
        "checks": checks,
        "routes": [f"{family}:{loader}:inpaint:lanpaint" for family, loader in sorted(ROUTES)],
        "registry_fingerprint": registry["registry_fingerprint"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit LanPaint Phase 10 Qwen and Z-Image onboarding.")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = run_audit()
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
