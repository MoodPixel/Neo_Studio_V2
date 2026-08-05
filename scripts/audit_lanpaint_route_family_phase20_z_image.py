from __future__ import annotations

import json
from pathlib import Path

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE20_STATE, PHASE21_STATE, PHASE22_STATE, get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_app.providers.compile_router import select_comfy_compile_route
from neo_app.providers.comfy_workflows.lanpaint_family import compile_lanpaint_family_inpaint
from neo_app.providers.schema import NeoJob, ProviderValidationResult
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support

ROOT = Path(__file__).resolve().parents[1]
ROUTES = (
    ("z_image", "diffusion_model"),
    ("z_image", "gguf"),
    ("z_image_turbo", "diffusion_model"),
    ("z_image_turbo", "gguf"),
)


def _node_inputs(*names: str) -> dict[str, list[str]]:
    return {"required": list(names), "optional": [], "all": list(names)}


def _assets(family: str, loader: str) -> tuple[str, str, str]:
    model = "z_image_turbo_bf16.safetensors" if family == "z_image_turbo" else "z_image_bf16.safetensors"
    if loader == "gguf":
        model = model.removesuffix(".safetensors") + "-Q5_K_M.gguf"
    return model, "qwen_3_4b.safetensors", "ae.safetensors"


def _capabilities(family: str, loader: str, *, remove: str = "") -> dict:
    nodes = {
        "LoadImage": _node_inputs("image", "upload"),
        "ImageToMask": _node_inputs("image", "channel"),
        "InvertMask": _node_inputs("mask"),
        "UNETLoader": _node_inputs("unet_name", "weight_dtype"),
        "UnetLoaderGGUF": _node_inputs("unet_name"),
        "CLIPLoader": _node_inputs("clip_name", "type", "device"),
        "CLIPLoaderGGUF": _node_inputs("clip_name", "type", "device"),
        "CLIPTextEncode": _node_inputs("clip", "text"),
        "ConditioningZeroOut": _node_inputs("conditioning"),
        "VAELoader": _node_inputs("vae_name"),
        "CropByMask": _node_inputs("image", "mask", "padding"),
        "ImageResizeKJv2": _node_inputs("image", "width", "height", "upscale_method", "keep_proportion", "pad_color", "crop_position", "divisible_by", "mask", "device"),
        "GrowMaskWithBlur": _node_inputs("mask", "expand", "incremental_expandrate", "tapered_corners", "flip_input", "blur_radius", "lerp_alpha", "decay_factor", "fill_holes"),
        "VAEEncode": _node_inputs("pixels", "vae"),
        "SetLatentNoiseMask": _node_inputs("samples", "mask"),
        "ModelSamplingAuraFlow": _node_inputs("model", "shift"),
        "LanPaint_KSampler": _node_inputs("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps", "LanPaint_PromptMode", "LanPaint_Info", "Inpainting_mode"),
        "VAEDecode": _node_inputs("samples", "vae"),
        "ImageCompositeMasked": _node_inputs("destination", "source", "mask", "x", "y", "resize_source"),
        "PreviewImage": _node_inputs("images"),
        "LoraLoader": _node_inputs("model", "clip", "lora_name", "strength_model", "strength_clip"),
    }
    if remove:
        nodes.pop(remove, None)
    model, text, vae = _assets(family, loader)
    model_role = "gguf_unet" if loader == "gguf" else "diffusion_model"
    return {
        "provider_id": "comfyui_portable",
        "reachable": True,
        "object_info_available": True,
        "object_info_node_inputs": nodes,
        "loaders": {
            loader: {
                "available": True,
                "roles": {
                    model_role: {
                        "available": True,
                        "backend_node": "UnetLoaderGGUF" if loader == "gguf" else "UNETLoader",
                        "assets": {"models": [model]},
                    },
                    "lumina2_clip_loader": {
                        "available": True,
                        "backend_node": "CLIPLoaderGGUF" if loader == "gguf" else "CLIPLoader",
                        "assets": {"text_encoders": [text]},
                    },
                    "vae_or_ae": {
                        "available": True,
                        "backend_node": "VAELoader",
                        "assets": {"vaes": [vae]},
                    },
                },
            }
        },
    }


def _job(family: str, loader: str) -> NeoJob:
    model, text, vae = _assets(family, loader)
    params = {
        "source_image_name": "source.png",
        "mask_image_name": "mask.png",
        "source_image": "source.png",
        "mask_image": "mask.png",
        "inpaint_engine": "lanpaint",
        "vae": vae,
        "seed": 123,
        "qwen3_text_encoder": text,
        "diffusion_model" if loader == "diffusion_model" else "gguf_model": model,
    }
    return NeoJob(
        surface="image",
        subtab="inpaint",
        mode="inpaint",
        provider_id="comfyui_portable",
        family=family,
        loader=loader,
        model=model,
        prompt="replace only the masked shirt",
        negative_prompt="blurry, distorted",
        params=params,
        extensions={},
    )


def _compile(family: str, loader: str):
    job = _job(family, loader)
    return compile_lanpaint_family_inpaint(
        provider_id="comfyui_portable",
        base_url="http://127.0.0.1:8188",
        job=job,
        validation=ProviderValidationResult(provider_id="comfyui_portable", ok=True),
        route=select_comfy_compile_route(job),
        capabilities={},
        backend_capabilities=_capabilities(family, loader),
    )


def main() -> int:
    checks: list[dict[str, object]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    registry = lanpaint_family_adapter_registry("comfyui_portable")
    expected_routes = {f"{family}:{loader}:inpaint:lanpaint" for family, loader in ROUTES}
    check("registry Phase 20 state", registry["onboarding_state"] in {PHASE20_STATE, PHASE21_STATE, PHASE22_STATE}, str(registry["onboarding_state"]))
    check("four Phase 20 routes complete", set(registry.get("phase20_completed_route_keys", [])) == expected_routes)
    check("duplicate z_image_base alias remains blocked", not any(key.startswith("z_image_base:") for key in registry["active_route_keys"]))

    for family, loader in ROUTES:
        route_key = f"{family}:{loader}:inpaint:lanpaint"
        expected_variant = "base" if family == "z_image" else "turbo"
        expected_thinking = 3 if family == "z_image" else 5
        expected_graph = "z_image_lanpaint_base_crop_stitch_v2" if family == "z_image" else "z_image_turbo_lanpaint_crop_stitch_v2"

        policy = get_lanpaint_family_policy(family, loader=loader, provider_id="comfyui_portable")
        adapter = get_lanpaint_family_adapter(family, loader=loader, provider_id="comfyui_portable")
        profile = get_lanpaint_family_expansion_profile(family, loader=loader, provider_id="comfyui_portable")
        check(f"{route_key} policy complete", bool(policy and policy["validation"]["ok"] and policy["family_variant"]["id"] == expected_variant))
        check(f"{route_key} adapter Phase 20 bound", bool(adapter["binding"]["selectable"] and adapter["binding"]["graph_profile"] == expected_graph and adapter["stabilization"]["state"] == "phase20_z_image_onboarded"))
        check(f"{route_key} expansion onboarded", bool(profile and profile["onboarding"]["state"] == "onboarded_phase20" and profile["execution"]["state"] == "phase20_onboarded"))
        check(f"{route_key} independent defaults", adapter["sampler"]["defaults"]["lanpaint_thinking_steps"] == expected_thinking)

        native = route_support("comfyui_portable", family, loader, "inpaint", engine="native")
        lanpaint = route_support("comfyui_portable", family, loader, "inpaint", engine="lanpaint")
        check(f"{route_key} LoRA engine-independent", native["compatibility_route_key"] == lanpaint["compatibility_route_key"] == f"{family}:{loader}:inpaint")

        compiled = _compile(family, loader)
        classes = [node["class_type"] for node in compiled.backend_payload["prompt"].values()]
        actual = compiled.backend_payload["actual_params"]
        topology_ok = (
            compiled.compile_status == "compiled"
            and classes[:4] == ["LoadImage", "LoadImage", "ImageToMask", "CropByMask"]
            and "ModelSamplingAuraFlow" in classes
            and "LanPaint_KSampler" in classes
            and "SetLatentNoiseMask" in classes
            and classes[-4:] == ["ImageResizeKJv2", "GrowMaskWithBlur", "ImageCompositeMasked", "PreviewImage"]
        )
        check(f"{route_key} full LanPaint graph", topology_ok)
        check(f"{route_key} replay identity", actual["lanpaint_replay"]["route"]["family_variant"] == expected_variant and bool(actual["lanpaint_replay"]["route"]["stability_profile"]))

    base_assets = dict(zip(("model", "text_encoder", "vae"), _assets("z_image", "gguf")))
    wrong_family = evaluate_lanpaint_route_capabilities(
        _capabilities("z_image", "gguf"),
        provider_id="comfyui_portable",
        family="z_image_turbo",
        loader="gguf",
        selected_assets=base_assets,
    )
    check("Base model cannot satisfy Turbo capability contract", wrong_family["executable"] is False)

    missing_gguf = evaluate_lanpaint_route_capabilities(
        _capabilities("z_image", "gguf", remove="UnetLoaderGGUF"),
        provider_id="comfyui_portable",
        family="z_image",
        loader="gguf",
        selected_assets=base_assets,
    )
    check("missing GGUF loader fails closed", missing_gguf["status"] == "blocked_missing_nodes")

    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    check("frontend Phase 20 cache revision", "lanpaint_z_image_phase20_20260805" in js and "phase20=lanpaint_z_image_20260805" in index)
    check("frontend canonical Base label", "family_label: 'Z-Image Base'" in js)
    check("Phase 20 provider record", (ROOT / "neo_system_records/03_PROVIDER_SYSTEM/LANPAINT_ROUTE_FAMILY_PHASE20_Z_IMAGE_LANPAINT_INPAINTING_20260805.md").exists())
    check("Phase 20 validation record", (ROOT / "neo_system_records/09_VALIDATION/LANPAINT_ROUTE_FAMILY_PHASE20_Z_IMAGE_LANPAINT_INPAINTING_20260805.md").exists())

    result = {
        "schema_id": "neo.validation.lanpaint_phase20_z_image.v1",
        "phase_state": PHASE20_STATE,
        "checks": checks,
        "passed": sum(1 for item in checks if item["ok"]),
        "total": len(checks),
        "ok": all(item["ok"] for item in checks),
    }
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
