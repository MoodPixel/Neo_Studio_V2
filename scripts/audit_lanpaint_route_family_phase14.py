from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE14_STATE, get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.providers.compile_router import select_comfy_compile_route
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support
from tests.test_lanpaint_route_family_phase10_qwen_zimage import _compile as compile_aura
from tests.test_lanpaint_route_family_phase14_route_parity import (
    EXPECTED_ACTIVE,
    _compile_safetensors,
    _safetensors_capabilities,
)
from tests.test_lanpaint_route_family_phase5_krea2_turbo_gguf import _compile as compile_gguf, _job as krea_job

AUDIT_SCHEMA_ID = "neo.validation.lanpaint_route_family_phase14.v1"
DATE = "2026-08-05"

PHASE15_ACTIVE = {
    "sdxl:checkpoint:inpaint:lanpaint",
    "sd15:checkpoint:inpaint:lanpaint",
    "sd35:diffusion_model:inpaint:lanpaint",
    "sd35:gguf:inpaint:lanpaint",
}

PHASE16_ACTIVE = {
    "flux:diffusion_model:inpaint:lanpaint",
    "flux:gguf:inpaint:lanpaint",
}

PHASE17_ACTIVE = {
    "flux2_dev:diffusion_model:inpaint:lanpaint",
    "flux2_dev:gguf:inpaint:lanpaint",
    "flux2_klein:diffusion_model:inpaint:lanpaint",
    "flux2_klein:gguf:inpaint:lanpaint",
}
PHASE18_ACTIVE = {
    "qwen_image_edit_2509:diffusion_model:inpaint:lanpaint",
    "qwen_image_edit_2509:gguf:inpaint:lanpaint",
    "qwen_image_edit_2511:diffusion_model:inpaint:lanpaint",
    "qwen_image_edit_2511:gguf:inpaint:lanpaint",
}



def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def build_report() -> dict[str, Any]:
    registry = lanpaint_family_adapter_registry("comfyui_portable")
    expected_existing_fingerprints = {
        "krea2_turbo:gguf:inpaint:lanpaint": "3a776817ecec7c1b2f9bd2cf1ca498f728ecbda1fdecf39477b02d5224fb51a3",
        "qwen_image:diffusion_model:inpaint:lanpaint": "8973b4959ad180c2fec89283107e54dac716a0b6037fb65fb213ff2e0e4292cc",
        "qwen_image:gguf:inpaint:lanpaint": "6fc313f0e63fcb0ad75a49c151f408330a2ad3b260dde1ffe3282b2475c12335",
        "z_image:diffusion_model:inpaint:lanpaint": "fae299eddbe4e93a29a771403aafa46a365e6344740c1abd0472412bbf3703db",
        "z_image:gguf:inpaint:lanpaint": "a19000001da12db7be15c43262b5ad72c0693506d3b274898cab09faa103768e",
        "z_image_turbo:diffusion_model:inpaint:lanpaint": "db5aa5eb63546eafed429c43405876a260755632d99dba2e3247c9e897a0567b",
        "z_image_turbo:gguf:inpaint:lanpaint": "a669a8f058de6163d9877a8c02ac262a4b3331eea3b8575c2178abc8fc9a3c29",
    }
    registry_fingerprints = {item["identity"]["route_key"]: item["adapter_fingerprint"] for item in registry.get("adapters") or []}
    safe_adapter = get_lanpaint_family_adapter("krea2_turbo", loader="diffusion_model", provider_id="comfyui_portable")
    gguf_adapter = get_lanpaint_family_adapter("krea2_turbo", loader="gguf", provider_id="comfyui_portable")
    safe = _compile_safetensors()
    gguf = compile_gguf(krea_job())
    safe_prompt = safe.backend_payload["prompt"]
    gguf_prompt = gguf.backend_payload["prompt"]
    safe_actual = safe.backend_payload["actual_params"]
    safe_classes = [item["class_type"] for item in safe_prompt.values()]
    gguf_classes = [item["class_type"] for item in gguf_prompt.values()]
    cap = evaluate_lanpaint_route_capabilities(
        _safetensors_capabilities(),
        provider_id="comfyui_portable",
        family="krea2_turbo",
        loader="diffusion_model",
        selected_assets={
            "model": "krea2_turbo_fp8_scaled.safetensors",
            "text_encoder": "qwen3vl_4b_fp8_scaled.safetensors",
            "vae": "qwen_image_vae.safetensors",
        },
    )
    missing = evaluate_lanpaint_route_capabilities(
        _safetensors_capabilities(remove="UNETLoader"),
        provider_id="comfyui_portable",
        family="krea2_turbo",
        loader="diffusion_model",
    )
    expansion = get_lanpaint_family_expansion_profile("krea2_turbo", loader="diffusion_model", provider_id="comfyui_portable")
    blocked = [
        get_lanpaint_family_adapter("krea2", loader="gguf"),
        get_lanpaint_family_adapter("qwen_image_edit", loader="gguf"),
        get_lanpaint_family_adapter("z_image_base", loader="gguf"),
    ]
    aura = [compile_aura(family, loader) for family in ("qwen_image", "z_image", "z_image_turbo") for loader in ("diffusion_model", "gguf")]
    router_safe = select_comfy_compile_route(krea_job(loader="diffusion_model"))
    router_gguf = select_comfy_compile_route(krea_job(loader="gguf"))
    lora_safe = route_support("comfyui", "krea2_turbo", "diffusion_model", "inpaint", engine="lanpaint")
    lora_gguf = route_support("comfyui", "krea2_turbo", "gguf", "inpaint", engine="lanpaint")
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    path_pattern = re.compile(r"(?:/(?:home|Users|mnt)/|[A-Za-z]:\\(?:Users|Documents and Settings|LLM)\\)")
    public_files = [
        ROOT / "neo_app/image/lanpaint_family_adapter.py",
        ROOT / "neo_app/providers/comfy_workflows/lanpaint.py",
        ROOT / "neo_app/providers/comfy_workflows/lanpaint_family.py",
        ROOT / "neo_app/providers/compile_router.py",
        ROOT / "neo_app/static/js/neo.js",
    ]
    path_hits = [path.relative_to(ROOT).as_posix() for path in public_files if path_pattern.search(path.read_text(encoding="utf-8"))]

    checks = [
        _check("registry_stabilizes_eight_routes", registry.get("stabilization_state") == PHASE14_STATE and set(registry.get("stabilized_route_keys") or []) == EXPECTED_ACTIVE and all(registry_fingerprints.get(key) == value for key, value in expected_existing_fingerprints.items()), "Phase 14 marks the eight exact routes as stabilized while preserving all seven existing adapter fingerprints."),
        _check("only_krea_safetensors_is_new", set(registry.get("new_routes_activated") or []) == ({"krea2_turbo:diffusion_model:inpaint:lanpaint"} | PHASE15_ACTIVE | PHASE16_ACTIVE | PHASE17_ACTIVE | PHASE18_ACTIVE), "Phase 14 keeps Krea safetensors as its only binding while later phases add only the explicit SD, Flux.1, Flux.2, and Qwen Edit routes."),
        _check("krea_loader_policy_parity", safe_adapter["policy"]["policy_id"] == gguf_adapter["policy"]["policy_id"] and safe_adapter["spatial"] == gguf_adapter["spatial"] and safe_adapter["sampler"] == gguf_adapter["sampler"], "Krea safetensors and GGUF share one family, spatial and sampler policy."),
        _check("krea_loader_nodes_are_distinct", safe_adapter["loaders"]["model"]["preferred_node_class"] == "UNETLoader" and gguf_adapter["loaders"]["model"]["preferred_node_class"] == "UnetLoaderGGUF", "Each Krea loader keeps its own exact model-loader node."),
        _check("krea_graph_shapes_match", safe.compile_status == gguf.compile_status == "compiled" and safe_classes[:9] == gguf_classes[:9] and safe_classes[10:] == gguf_classes[10:] and safe_classes[9] == "UNETLoader" and gguf_classes[9] == "UnetLoaderGGUF", "The Krea graphs differ only at the model loader."),
        _check("krea_critical_ports_match", all(safe_prompt[node]["inputs"] == gguf_prompt[node]["inputs"] for node in ("14", "15", "17", "18", "19")), "Differential, sampler, restore and stitch ports are identical across Krea loaders."),
        _check("safetensors_lineage_is_exact", safe_actual.get("_neo_lanpaint_phase14_state") == PHASE14_STATE and safe_actual.get("lanpaint_route", {}).get("loader") == "diffusion_model" and safe_actual.get("diffusion_model") and not safe_actual.get("gguf_model"), "Krea safetensors records exact route and model lineage."),
        _check("lora_remains_optional", safe.compile_status == "compiled" and not any(item["class_type"].startswith("LoraLoader") for item in safe_prompt.values()), "Plain Krea safetensors LanPaint has no LoRA dependency or LoRA nodes."),
        _check("lora_policy_is_loader_specific_engine_independent", lora_safe.get("route_key") == "krea2_turbo:diffusion_model:inpaint" and lora_gguf.get("route_key") == "krea2_turbo:gguf:inpaint" and lora_safe.get("compatibility_engine_independent") and lora_gguf.get("compatibility_engine_independent"), "LoRA compatibility remains loader-specific and independent of the inpaint engine."),
        _check("capability_gate_is_loader_exact", cap.get("status") == "experimental_available" and missing.get("status") == "blocked_missing_nodes" and any("UNETLoader" in item.get("message", "") for item in missing.get("blockers", [])), "Safetensors requires UNETLoader and cannot fall through to a GGUF loader."),
        _check("router_parity_is_available", router_safe.status == router_gguf.status == "available" and router_safe.compiler_id == router_gguf.compiler_id == "comfy.lanpaint.family_aware.v1", "Both Krea loaders resolve through the shared family-aware compiler."),
        _check("expansion_profile_is_phase14_onboarded", expansion.get("onboarding", {}).get("state") == "onboarded_phase14" and expansion.get("execution", {}).get("state") == "phase14_stabilized" and expansion.get("test_status", {}).get("physical_validation") == "pending", "The Krea safetensors scaffold is promoted to automated Phase 14 stabilization only."),
        _check("unrelated_scaffolds_stay_blocked", all(not item.get("binding", {}).get("selectable") for item in blocked), "Krea RAW, Qwen Edit and duplicate Z-Image Base identities remain blocked."),
        _check("existing_aura_routes_are_unchanged", all(item.compile_status == "compiled" and "ModelSamplingAuraFlow" in [node["class_type"] for node in item.backend_payload["prompt"].values()] and "DifferentialDiffusionAdvanced" not in [node["class_type"] for node in item.backend_payload["prompt"].values()] for item in aura), "Existing Qwen and Z-Image routes keep their AuraFlow graph semantics."),
        _check("frontend_and_public_paths_are_clean", "IMAGE_LANPAINT_FAMILY_UI_FALLBACKS.krea2_turbo.diffusion_model" in js and "phase14=lanpaint_route_parity_phase14_20260805" in index and not path_hits, "Frontend fallback/cache markers are updated without personal paths."),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "phase_state": PHASE14_STATE,
        "active_route_keys": sorted(EXPECTED_ACTIVE),
        "new_routes_activated": registry.get("new_routes_activated") or [],
        "path_hits": path_hits,
        "physical_validation": {
            "status": "not_run",
            "reason": "The packaging environment does not host the target ComfyUI profile, custom nodes or model assets.",
            "required_next": "Run Krea 2 Turbo safetensors and GGUF with identical source, mask, prompt and seed; compare dimensions, mask behavior, stitch alignment and optional model-only LoRA influence.",
        },
        "checks": checks,
        "summary": {
            "passed": len(checks) - len(failed),
            "failed": len(failed),
            "total": len(checks),
            "failed_ids": [item["id"] for item in failed],
        },
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audit Phase 14 existing LanPaint route parity and stabilization.")
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
