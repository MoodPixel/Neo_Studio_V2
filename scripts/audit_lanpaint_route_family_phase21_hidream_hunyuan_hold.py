from __future__ import annotations

import json
import re
from pathlib import Path

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE21_STATE, PHASE22_STATE, get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_app.models.route_matrix import resolve_model_backend_route
from neo_app.providers.compile_router import select_comfy_compile_route
from neo_app.providers.comfy_workflows.lanpaint_family import compile_lanpaint_family_inpaint
from neo_app.providers.schema import NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (("hidream", "diffusion_model"), ("hidream", "gguf"))
ASSETS = {
    "diffusion_model": {
        "model": "hidream_i1_dev_fp8.safetensors",
        "clip_l": "clip_l_hidream.safetensors",
        "clip_g": "clip_g_hidream.safetensors",
        "t5": "t5xxl_fp16.safetensors",
        "llama": "llama_3.1_8b_instruct_fp8.safetensors",
        "vae": "ae.safetensors",
    },
    "gguf": {
        "model": "hidream_i1_dev_Q4_K_M.gguf",
        "clip_l": "clip_l_hidream.safetensors",
        "clip_g": "clip_g_hidream.safetensors",
        "t5": "t5xxl_fp16.safetensors",
        "llama": "llama_3.1_8b_instruct_Q4_K_M.gguf",
        "vae": "ae.safetensors",
    },
}


def _node_inputs(*names: str) -> dict[str, list[str]]:
    return {"required": list(names), "optional": [], "all": list(names)}


def _capabilities(loader: str, *, remove_node: str = "", remove_role: str = "") -> dict:
    model_node = "UNETLoader" if loader == "diffusion_model" else "UnetLoaderGGUF"
    clip_node = "QuadrupleCLIPLoader" if loader == "diffusion_model" else "QuadrupleCLIPLoaderGGUF"
    nodes = {
        "LoadImage": _node_inputs("image"),
        "ImageToMask": _node_inputs("image", "channel"),
        "InvertMask": _node_inputs("mask"),
        model_node: _node_inputs("unet_name", *(("weight_dtype",) if model_node == "UNETLoader" else ())),
        clip_node: _node_inputs("clip_name1", "clip_name2", "clip_name3", "clip_name4"),
        "VAELoader": _node_inputs("vae_name"),
        "CLIPTextEncode": _node_inputs("clip", "text"),
        "ModelSamplingSD3": _node_inputs("model", "shift"),
        "CropByMask": _node_inputs("image", "mask", "padding"),
        "ImageResizeKJv2": _node_inputs("image", "width", "height", "upscale_method", "keep_proportion", "pad_color", "crop_position", "divisible_by", "device"),
        "GrowMaskWithBlur": _node_inputs("mask", "expand", "incremental_expandrate", "tapered_corners", "flip_input", "blur_radius", "lerp_alpha", "decay_factor", "fill_holes"),
        "VAEEncode": _node_inputs("pixels", "vae"),
        "SetLatentNoiseMask": _node_inputs("samples", "mask"),
        "LanPaint_KSampler": _node_inputs("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps", "LanPaint_PromptMode", "LanPaint_Info", "Inpainting_mode"),
        "VAEDecode": _node_inputs("samples", "vae"),
        "ImageCompositeMasked": _node_inputs("destination", "source", "mask", "x", "y", "resize_source"),
        "PreviewImage": _node_inputs("images"),
        "LoraLoader": _node_inputs("model", "clip", "lora_name", "strength_model", "strength_clip"),
    }
    if remove_node:
        nodes.pop(remove_node, None)
    a = ASSETS[loader]
    model_role = "diffusion_model" if loader == "diffusion_model" else "gguf_unet"
    aggregate = "hidream_quadruple_clip_loader" if loader == "diffusion_model" else "hidream_quadruple_clip_loader_gguf"
    roles = {
        model_role: {"available": True, "backend_node": model_node, "assets": {"models": [a["model"]]}},
        aggregate: {"available": True, "backend_node": clip_node, "assets": {"text_encoders": [a["clip_l"], a["clip_g"], a["t5"], a["llama"]]}},
        "hidream_clip_l": {"available": True, "backend_node": clip_node, "assets": {"text_encoders": [a["clip_l"]]}},
        "hidream_clip_g": {"available": True, "backend_node": clip_node, "assets": {"text_encoders": [a["clip_g"]]}},
        "hidream_t5xxl": {"available": True, "backend_node": clip_node, "assets": {"text_encoders": [a["t5"]]}},
        "hidream_llama_3_1_8b": {"available": True, "backend_node": clip_node, "assets": {"text_encoders": [a["llama"]]}},
        "vae_or_ae": {"available": True, "backend_node": "VAELoader", "assets": {"vaes": [a["vae"]]}},
        "sampler": {"available": True, "backend_node": "LanPaint_KSampler", "assets": {}},
    }
    if remove_role:
        roles.pop(remove_role, None)
    return {
        "provider_id": "comfyui_portable",
        "reachable": True,
        "object_info_available": True,
        "discovery_status": "ready",
        "object_info_node_inputs": nodes,
        "loaders": {loader: {"available": True, "roles": roles}},
    }


def _selected(loader: str) -> dict[str, str]:
    a = ASSETS[loader]
    return {
        "model": a["model"],
        "text_encoder": a["clip_l"],
        "text_encoder_2": a["clip_g"],
        "text_encoder_3": a["t5"],
        "text_encoder_4": a["llama"],
        "vae": a["vae"],
    }


def _job(loader: str, profile: str = "dev", variant: str = "HiDream-I1") -> NeoJob:
    a = ASSETS[loader]
    params = {
        "source_image_name": "source.png",
        "mask_image_name": "mask.png",
        "source_image": "source.png",
        "mask_image": "mask.png",
        "inpaint_engine": "lanpaint",
        "hidream_variant": variant,
        "hidream_i1_profile": profile,
        "hidream_clip_l": a["clip_l"],
        "hidream_clip_g": a["clip_g"],
        "hidream_t5xxl": a["t5"],
        "hidream_llama_3_1_8b": a["llama"],
        "vae": a["vae"],
        "seed": 123,
        ("diffusion_model" if loader == "diffusion_model" else "gguf_model"): a["model"],
    }
    return NeoJob(
        surface="image",
        subtab="inpaint",
        mode="inpaint",
        provider_id="comfyui_portable",
        family="hidream",
        loader=loader,
        model=a["model"],
        prompt="replace only the masked jacket",
        negative_prompt="blurry",
        params=params,
        extensions={},
    )


def _compile(loader: str, profile: str = "dev", variant: str = "HiDream-I1"):
    job = _job(loader, profile=profile, variant=variant)
    return compile_lanpaint_family_inpaint(
        provider_id="comfyui_portable",
        base_url="http://127.0.0.1:8188",
        job=job,
        validation=ProviderValidationResult(provider_id="comfyui_portable", ok=True),
        route=select_comfy_compile_route(job),
        capabilities={},
        backend_capabilities=_capabilities(loader),
    )


def main() -> int:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    registry = lanpaint_family_adapter_registry("comfyui_portable")
    expected = {"hidream:diffusion_model:inpaint:lanpaint", "hidream:gguf:inpaint:lanpaint"}
    check("registry Phase 21 state", registry.get("onboarding_state") == PHASE22_STATE, str(registry.get("onboarding_state")))
    check("two HiDream Phase 21 routes onboarded", set(registry.get("phase21_onboarded_route_keys", [])) == expected)
    check("Hunyuan routes are not active", not any(key.startswith("hunyuan") for key in registry.get("active_route_keys", [])))

    profile_expectations = {
        "full": (50, 5.0, 3.0),
        "dev": (28, 1.0, 6.0),
        "fast": (16, 1.0, 3.0),
    }

    for _, loader in ROUTES:
        route_key = f"hidream:{loader}:inpaint:lanpaint"
        policy = get_lanpaint_family_policy("hidream", loader=loader, provider_id="comfyui_portable")
        adapter = get_lanpaint_family_adapter("hidream", loader=loader, provider_id="comfyui_portable")
        expansion = get_lanpaint_family_expansion_profile("hidream", loader=loader, provider_id="comfyui_portable")
        route = resolve_model_backend_route("hidream", loader, "inpaint", "comfyui")
        check(f"{route_key} policy complete", bool(policy and policy["validation"]["ok"] and policy["family_variant"]["id"] == "HiDream-I1"))
        check(f"{route_key} four encoder slots", len(policy["text_encoder_policy"]["asset_slots"]) == 4)
        check(f"{route_key} adapter bound", bool(adapter["binding"]["selectable"] and adapter["binding"]["graph_profile"] == "hidream_i1_quad_clip_crop_stitch_v1"))
        check(f"{route_key} expansion onboarded", bool(expansion and expansion["onboarding"]["state"] == "onboarded_phase21" and expansion["execution"]["state"] == "phase21_onboarded"))
        check(f"{route_key} route matrix available", bool(route.selectable and route.workflow_type == "image.inpaint.lanpaint"))

        native = route_support("comfyui_portable", "hidream", loader, "inpaint", engine="native")
        lanpaint = route_support("comfyui_portable", "hidream", loader, "inpaint", engine="lanpaint")
        check(f"{route_key} LoRA engine-independent", native["compatibility_route_key"] == lanpaint["compatibility_route_key"] == f"hidream:{loader}:inpaint")

        compiled = _compile(loader)
        classes = [node["class_type"] for node in compiled.backend_payload["prompt"].values()]
        actual = compiled.backend_payload["actual_params"]
        topology = (
            compiled.compile_status == "compiled"
            and classes[:4] == ["LoadImage", "LoadImage", "ImageToMask", "CropByMask"]
            and ("QuadrupleCLIPLoader" if loader == "diffusion_model" else "QuadrupleCLIPLoaderGGUF") in classes
            and "ModelSamplingSD3" in classes
            and "SetLatentNoiseMask" in classes
            and "LanPaint_KSampler" in classes
            and classes[-4:] == ["ImageResizeKJv2", "GrowMaskWithBlur", "ImageCompositeMasked", "PreviewImage"]
            and "ModelSamplingAuraFlow" not in classes
            and "DifferentialDiffusionAdvanced" not in classes
        )
        check(f"{route_key} full HiDream LanPaint graph", topology)
        replay = actual.get("lanpaint_replay", {})
        check(f"{route_key} replay family/profile", replay.get("route", {}).get("family_variant") == "HiDream-I1" and replay.get("route", {}).get("hidream_i1_profile") == "dev")

        for profile, expected_values in profile_expectations.items():
            profiled = _compile(loader, profile=profile).backend_payload["actual_params"]
            check(
                f"{route_key} {profile} defaults",
                (profiled.get("steps"), float(profiled.get("cfg")), float(profiled.get("hidream_sd3_shift"))) == expected_values,
            )

    missing_llama = evaluate_lanpaint_route_capabilities(
        _capabilities("gguf", remove_role="hidream_llama_3_1_8b"),
        provider_id="comfyui_portable",
        family="hidream",
        loader="gguf",
        selected_assets=_selected("gguf"),
    )
    check("missing fourth encoder fails closed", missing_llama.get("status") == "blocked_missing_models")

    blocked_variant = _compile("diffusion_model", variant="HiDream-E1")
    check("HiDream-E1 cannot inherit I1", blocked_variant.compile_status == "mock_compiled" and any("HiDream-I1" in str(e) for e in blocked_variant.backend_payload["validation"].get("errors", [])))

    h_image = resolve_model_backend_route("hunyuan_image", "diffusion_model", "inpaint", "comfyui")
    check("HunyuanImage stays provider gated", not h_image.selectable and h_image.state == "provider_gated" and "HunyuanVideo" in h_image.reason)

    manifest = json.loads((ROOT / "neo_app/models/model_family_manifest.json").read_text(encoding="utf-8"))
    manifest_text = json.dumps(manifest)
    check("manifest separates HiDream I1 variants", all(token in manifest_text for token in ("HiDream-I1", "HiDream-E1", "HiDream-O1")))
    check("manifest records Hunyuan hold", "held_unverified_image_workflow" in manifest_text and "HunyuanVideo" in manifest_text)

    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    check("frontend Phase 21 cache revision", "lanpaint_hidream_phase21_20260805" in js and "phase21=lanpaint_hidream_hunyuan_hold_20260805" in index)
    check("frontend HiDream label", "HiDream-I1" in js and "CLIP-L + CLIP-G + T5XXL + Llama 3.1 8B" in js)

    source_text = "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in (
            ROOT / "neo_app/providers/comfy_workflows/lanpaint_hidream.py",
            ROOT / "neo_app/image/lanpaint_family_policies.py",
            ROOT / "neo_app/image/lanpaint_family_adapter.py",
        )
    )
    windows_absolute = re.compile(r"(?<![A-Za-z0-9+.\-])\b[A-Za-z]:[\\/]")
    named_posix_home = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home)/[^/\s]+/")
    check("no personal absolute paths", not windows_absolute.search(source_text) and not named_posix_home.search(source_text))
    check("Phase 21 provider record", (ROOT / "neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE21_HIDREAM_I1_HUNYUAN_VIDEO_HOLD_20260805.md").exists())
    check("Phase 21 validation record", (ROOT / "neo_system_records/09_VALIDATION/LANPAINT_ROUTE_FAMILY_PHASE21_HIDREAM_I1_HUNYUAN_VIDEO_HOLD_20260805.md").exists())

    result = {
        "schema_id": "neo.validation.lanpaint_phase21_hidream_hunyuan_hold.v1",
        "phase_state": PHASE21_STATE,
        "checks": checks,
        "passed": sum(1 for item in checks if item["ok"]),
        "total": len(checks),
        "ok": all(item["ok"] for item in checks),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
