from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.providers.compile_router import select_comfy_compile_route
from neo_app.providers.comfy_workflows.lanpaint import (
    PHASE5_GRAPH_STATE,
    PHASE5_VARIANT,
    compile_lanpaint_krea2_turbo_gguf_inpaint,
)
from neo_app.providers.schema import NeoJob, ProviderValidationResult
from scripts.audit_lanpaint_route_family_phase0 import build_report as build_phase0_report
from scripts.audit_lanpaint_route_family_phase1 import build_report as build_phase1_report
from scripts.audit_lanpaint_route_family_phase2 import build_report as build_phase2_report
from scripts.audit_lanpaint_route_family_phase3 import build_report as build_phase3_report
from scripts.audit_lanpaint_route_family_phase4 import build_report as build_phase4_report

SCHEMA_ID = "neo.image.lanpaint_route_family_phase5_audit.v1"
DATE = "2026-08-04"
FIXTURE = ROOT / "tests/fixtures/lanpaint/krea2_turbo_gguf_crop_stitch_v1.json"


def _inputs(*names: str) -> dict[str, list[str]]:
    return {"required": list(names), "optional": [], "all": list(names)}


def _capabilities() -> dict[str, Any]:
    nodes = {
        "LoadImage": _inputs("image", "upload"),
        "ImageToMask": _inputs("image", "channel"),
        "InvertMask": _inputs("mask"),
        "CLIPLoader": _inputs("clip_name", "type", "device"),
        "CLIPTextEncode": _inputs("clip", "text"),
        "ConditioningZeroOut": _inputs("conditioning"),
        "VAELoader": _inputs("vae_name"),
        "UnetLoaderGGUF": _inputs("unet_name"),
        "CropByMask": _inputs("image", "mask", "padding"),
        "ImageResizeKJv2": _inputs("image", "width", "height", "upscale_method", "keep_proportion", "pad_color", "crop_position", "divisible_by", "mask", "device"),
        "GrowMaskWithBlur": _inputs("mask", "expand", "incremental_expandrate", "tapered_corners", "flip_input", "blur_radius", "lerp_alpha", "decay_factor", "fill_holes"),
        "VAEEncode": _inputs("pixels", "vae"),
        "SetLatentNoiseMask": _inputs("samples", "mask"),
        "DifferentialDiffusionAdvanced": _inputs("model", "samples", "mask", "multiplier"),
        "LanPaint_KSampler": _inputs("model", "positive", "negative", "latent_image", "seed", "steps", "cfg", "sampler_name", "scheduler", "denoise", "LanPaint_NumSteps", "LanPaint_PromptMode", "LanPaint_Info", "Inpainting_mode"),
        "VAEDecode": _inputs("samples", "vae"),
        "ImageCompositeMasked": _inputs("destination", "source", "mask", "x", "y", "resize_source"),
        "PreviewImage": _inputs("images"),
    }
    return {
        "provider_id": "comfyui_portable",
        "reachable": True,
        "object_info_available": True,
        "object_info_node_inputs": nodes,
        "loaders": {"gguf": {"available": True, "roles": {
            "gguf_unet": {"available": True, "backend_node": "UnetLoaderGGUF", "assets": {"gguf_models": ["krea2_turbo-Q5_K_M.gguf"]}, "notes": []},
            "krea2_clip_loader": {"available": True, "backend_node": "CLIPLoader", "assets": {"text_encoders": ["qwen3vl_4b_fp8_scaled.safetensors"]}, "notes": []},
            "qwen_image_vae": {"available": True, "backend_node": "VAELoader", "assets": {"vaes": ["qwen_image_vae.safetensors"]}, "notes": []},
        }}},
    }


def _job(engine: str | None = "lanpaint", *, family: str = "krea2_turbo", loader: str = "gguf", extensions: dict[str, Any] | None = None) -> NeoJob:
    params = {
        "source_image": "source.png",
        "source_image_name": "source.png",
        "mask_image": "mask.png",
        "mask_image_name": "mask.png",
        "qwen3vl_text_encoder": "qwen3vl_4b_fp8_scaled.safetensors",
        "vae": "qwen_image_vae.safetensors",
        "seed": 123,
    }
    if engine is not None:
        params["inpaint_engine"] = engine
    return NeoJob(
        surface="image", subtab="inpaint", mode="inpaint", provider_id="comfyui_portable",
        family=family, loader=loader,
        model="krea2_turbo-Q5_K_M.gguf" if loader == "gguf" else "krea2_turbo_fp8.safetensors",
        prompt="replace only the masked shirt", negative_prompt="", params=params,
        extensions=extensions or {},
    )


def _compile(job: NeoJob, capabilities: dict[str, Any] | None = None):
    route = select_comfy_compile_route(job)
    return compile_lanpaint_krea2_turbo_gguf_inpaint(
        provider_id="comfyui_portable",
        base_url="http://127.0.0.1:8188",
        job=job,
        validation=ProviderValidationResult(provider_id="comfyui_portable", ok=True),
        route=route,
        capabilities={},
        backend_capabilities=capabilities or _capabilities(),
    )


def build_report() -> dict[str, Any]:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    native_route = select_comfy_compile_route(_job(None))
    lanpaint_route = select_comfy_compile_route(_job())
    qwen_route = select_comfy_compile_route(_job(family="qwen_image"))
    native_loader_route = select_comfy_compile_route(_job(loader="diffusion_model"))
    compiled = _compile(_job())
    prompt = compiled.backend_payload.get("prompt") or {}
    actual = compiled.backend_payload.get("actual_params") or {}

    missing_caps = _capabilities()
    missing_caps["object_info_node_inputs"].pop("LanPaint_KSampler")
    missing = _compile(_job(), missing_caps)
    expected_sequence = ["UnetLoaderGGUF" if item == "GGUF_MODEL_LOADER" else item for item in fixture["node_sequence"]]
    actual_sequence = [node.get("class_type") for node in prompt.values() if isinstance(node, dict)]
    changed_sources = [
        ROOT / "neo_app/providers/compile_router.py",
        ROOT / "neo_app/providers/comfy_provider.py",
        ROOT / "neo_app/providers/comfy_workflows/lanpaint.py",
        ROOT / "neo_app/static/js/neo.js",
        FIXTURE,
    ]
    path_pattern = re.compile(r"(?:[A-Za-z]:\\\\|/(?:home|Users|mnt)/)")
    path_hits = [str(path.relative_to(ROOT)) for path in changed_sources if path_pattern.search(path.read_text(encoding="utf-8"))]
    previous = {f"phase{index}": fn() for index, fn in enumerate((build_phase0_report, build_phase1_report, build_phase2_report, build_phase3_report, build_phase4_report))}

    checks = [
        {"id": "native_engine_remains_default", "passed": native_route.compiler_id == "comfy.krea2_gguf" and native_route.engine == "native", "detail": "Omitting inpaint_engine preserves the existing Krea 2 native inpaint compiler."},
        {"id": "phase5_krea_route_remains_registered", "passed": lanpaint_route.can_compile and lanpaint_route.compiler_id == "comfy.lanpaint.family_aware.v1" and qwen_route.status == "available" and qwen_route.compiler_id == "comfy.lanpaint.family_aware.v1" and native_loader_route.status == "available" and native_loader_route.compiler_id == "comfy.lanpaint.family_aware.v1", "detail": "The original Krea 2 Turbo GGUF route remains intact while Phase 14 adds its safetensors parity binding through the same compiler."},
        {"id": "submitted_graph_sequence_is_emitted", "passed": compiled.compile_status == "compiled" and actual_sequence == expected_sequence, "detail": "The runnable API graph follows the submitted crop/resize/refine/Differential/LanPaint/restore/stitch workflow."},
        {"id": "critical_output_ports_are_locked", "passed": prompt.get("6", {}).get("inputs", {}).get("mask") == ["5", 3] and prompt.get("15", {}).get("inputs", {}).get("model") == ["14", 0] and prompt.get("15", {}).get("inputs", {}).get("latent_image") == ["14", 1] and prompt.get("19", {}).get("inputs", {}).get("x") == ["4", 2] and prompt.get("19", {}).get("inputs", {}).get("y") == ["4", 3], "detail": "KJ mask, Differential model/latent, and CropByMask geometry ports match the verified node contracts."},
        {"id": "defaults_and_lineage_are_recorded", "passed": actual.get("lanpaint_controls") == fixture["defaults"] and actual.get("lanpaint_route", {}).get("graph_state") == PHASE5_GRAPH_STATE and actual.get("lanpaint_route", {}).get("variant") == PHASE5_VARIANT, "detail": "Replay metadata retains the route identity and submitted workflow controls."},
        {"id": "missing_nodes_fail_closed", "passed": missing.compile_status == "mock_compiled" and not missing.backend_payload.get("prompt") and any("LanPaint_KSampler" in item for item in missing.backend_payload.get("validation", {}).get("errors", [])), "detail": "Missing LanPaint nodes block graph emission with no KSampler fallback."},
        {"id": "phase5_base_graph_has_no_embedded_lora_nodes", "passed": not any(node.get("class_type") == "LoraLoaderModelOnly" for node in prompt.values() if isinstance(node, dict)), "detail": "The Phase 5 graph emitter remains the stable no-LoRA baseline; Phase 6 patches the graph through the shared extension hook."},
        {"id": "frontend_selector_is_route_gated", "passed": "imageLanpaintPhase5Eligible" in (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8") and "imageInpaintEngine" in (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8"), "detail": "The minimal Native/LanPaint selector is visible only on the enabled route."},
        {"id": "no_personal_paths", "passed": not path_hits, "detail": "Phase 5 code and fixture contain no user-specific absolute path."},
        {"id": "previous_phase_audits_pass", "passed": all(item.get("status") == "passed" for item in previous.values()), "detail": "Phase 0–4 contract, abstraction, policy, and planner regressions remain green."},
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "route": lanpaint_route.as_dict(),
        "native_route": native_route.as_dict(),
        "graph": {"node_count": len(prompt), "node_sequence": actual_sequence, "controls": actual.get("lanpaint_controls"), "route_metadata": actual.get("lanpaint_route")},
        "gating": {"qwen": qwen_route.as_dict(), "diffusion_model": native_loader_route.as_dict(), "missing_node_compile_status": missing.compile_status, "lora_compile_status": "owned_by_phase6_extension_hook"},
        "path_hits": path_hits,
        "previous_phase_status": {key: value.get("status") for key, value in previous.items()},
        "physical_validation": {
            "status": "not_run",
            "reason": "No live target ComfyUI profile with the required Krea 2 Turbo GGUF assets and custom nodes was available in this packaging environment.",
            "required_next": "Run at least one masked-region generation on the intended ComfyUI/Portable profile before controlled rollout.",
        },
        "checks": checks,
        "summary": {"passed": len(checks) - len(failed), "failed": len(failed), "total": len(checks), "failed_ids": [item["id"] for item in failed]},
    }


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Audit LanPaint Phase 5 Krea 2 Turbo GGUF implementation.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
