from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_expansion import lanpaint_family_expansion_registry


def run_audit() -> dict[str, Any]:
    registry = lanpaint_family_expansion_registry()
    profiles = registry["profiles"]
    matrix = registry["compatibility_matrix"]
    ready = [item for item in profiles if item["onboarding"]["state"] == "ready_for_phase10"]
    phase14 = [item for item in profiles if item["onboarding"]["state"] == "onboarded_phase14"]
    phase15 = [item for item in profiles if item["onboarding"]["state"] == "onboarded_phase15"]
    phase17 = [item for item in profiles if item["onboarding"]["state"] == "onboarded_phase17"]
    phase18 = [item for item in profiles if item["onboarding"]["state"] == "onboarded_phase18"]
    qwen_report = evaluate_lanpaint_route_capabilities(
        {}, provider_id="comfyui", family="qwen_image", loader="gguf", mode="inpaint", engine="lanpaint"
    )
    source = {
        "router": (ROOT / "neo_app/providers/compile_router.py").read_text(encoding="utf-8"),
        "provider": (ROOT / "neo_app/providers/comfy_provider.py").read_text(encoding="utf-8"),
        "frontend": (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8"),
        "data": (ROOT / "neo_app/image/lanpaint_family_expansion_profiles.json").read_text(encoding="utf-8"),
    }
    policy_block = source["frontend"].split("const IMAGE_LANPAINT_FAMILY_UI_FALLBACKS =", 1)[1].split("const IMAGE_LANPAINT_CAPABILITY_STATUS", 1)[0]
    checks = [
        {"id": "registry_valid", "passed": registry["validation"]["ok"] and not registry["validation"]["issues"], "detail": "Expansion registry validates without errors."},
        {"id": "twenty_six_unique_profiles", "passed": len(profiles) == 26 and len({(p['identity']['family'], p['identity']['loader']) for p in profiles}) == 26, "detail": "One profile exists per family/loader candidate, including the Phase 15 SD, Phase 16 Flux.1, Phase 17 Flux.2, and Phase 18 Qwen Edit matrices."},
        {"id": "krea_safetensors_promoted_by_phase14", "passed": not ready and len(phase14) == 1 and phase14[0]["identity"]["family"] == "krea2_turbo" and phase14[0]["identity"]["loader"] == "diffusion_model" and phase14[0]["execution"]["state"] == "phase14_stabilized", "detail": "Krea 2 Turbo safetensors is no longer merely ready; Phase 14 binds it while other scaffolds remain blocked."},
        {"id": "phase10_promotions_are_preserved", "passed": {(p['identity']['family'], p['identity']['loader']) for p in profiles if p['onboarding']['state'] == 'onboarded_phase10'} == {('qwen_image','diffusion_model'),('qwen_image','gguf'),('z_image','diffusion_model'),('z_image','gguf'),('z_image_turbo','diffusion_model'),('z_image_turbo','gguf')}, "detail": "The six approved Qwen/Z profiles remain the exact Phase 10 promotion set."},
        {"id": "phase15_promotions_are_exact", "passed": {(p['identity']['family'], p['identity']['loader']) for p in phase15} == {('sdxl','checkpoint'),('sd15','checkpoint'),('sd35','diffusion_model'),('sd35','gguf')} and all(p['execution']['state'] == 'phase15_onboarded' for p in phase15), "detail": "Phase 15 promotes only SDXL checkpoint, SD 1.5 checkpoint, and SD 3.5 safetensors/GGUF."},
        {"id": "phase17_promotions_are_exact", "passed": {(p['identity']['family'], p['identity']['loader']) for p in phase17} == {('flux2_dev','diffusion_model'),('flux2_dev','gguf'),('flux2_klein','diffusion_model'),('flux2_klein','gguf')} and all(p['execution']['state'] == 'phase17_onboarded' for p in phase17), "detail": "Phase 17 promotes exactly Flux.2 Dev and Klein across both transformer loader branches."},
        {"id": "phase18_promotions_are_exact", "passed": {(p['identity']['family'], p['identity']['loader']) for p in phase18} == {('qwen_image_edit_2509','diffusion_model'),('qwen_image_edit_2509','gguf'),('qwen_image_edit_2511','diffusion_model'),('qwen_image_edit_2511','gguf')} and all(p['execution']['state'] == 'phase18_onboarded' for p in phase18), "detail": "Phase 18 promotes exactly Qwen Image Edit 2509 and 2511 across safetensors and GGUF."},
        {"id": "sd15_gguf_gap_is_explicit", "passed": any(p['identity']['family'] == 'sd15' and p['identity']['loader'] == 'gguf' and p['onboarding']['state'] == 'blocked_loader_ecosystem' and p['execution']['route_status'] == 'unsupported' for p in profiles), "detail": "SD 1.5 GGUF remains an explicit loader-ecosystem blocker rather than a fake executable route."},
        {"id": "matrix_separates_onboarded_and_scaffolded", "passed": all((row['route_status'] == 'experimental_available' and row['selectable'] and row['executable']) if (row['family'], row['loader']) in {('krea2_turbo','diffusion_model'),('qwen_image','diffusion_model'),('qwen_image','gguf'),('qwen_image_edit_2509','diffusion_model'),('qwen_image_edit_2509','gguf'),('qwen_image_edit_2511','diffusion_model'),('qwen_image_edit_2511','gguf'),('z_image','diffusion_model'),('z_image','gguf'),('z_image_turbo','diffusion_model'),('z_image_turbo','gguf'),('sdxl','checkpoint'),('sd15','checkpoint'),('sd35','diffusion_model'),('sd35','gguf'),('flux','diffusion_model'),('flux','gguf'),('flux2_dev','diffusion_model'),('flux2_dev','gguf'),('flux2_klein','diffusion_model'),('flux2_klein','gguf')} else (row['route_status'] == 'unsupported' and not row['selectable'] and not row['executable']) for row in matrix), "detail": "The matrix distinguishes Phase 10, Phase 14, Phase 15, Phase 16, Phase 17, and Phase 18 onboarded routes from remaining scaffolds."},
        {"id": "family_defaults_are_isolated", "passed": all((p.get('route_defaults', {}).get('lanpaint_defaults_state') in {'complete_family_policy','unresolved'}) or (p['family_policy'].get('policy_status') == 'complete_policy') or (p['onboarding']['state'] == 'blocked_loader_ecosystem') for p in profiles), "detail": "Each onboarded family owns a complete policy; unresolved and loader-blocked scaffolds cannot inherit Krea Turbo defaults."},
        {"id": "family_specific_conditioning", "passed": any(p['identity']['family'] == 'qwen_image' and p['conditioning_policy'].get('clip_type') == 'qwen_image' for p in profiles) and any(p['identity']['family'] == 'z_image' and p['conditioning_policy'].get('clip_type') == 'lumina2' for p in profiles), "detail": "Qwen and Z-Image keep their existing family conditioning identities."},
        {"id": "family_specific_lora", "passed": any(p['identity']['family'] == 'krea2' and p['lora_policy'].get('strategy') == 'model_only' for p in profiles) and any(p['identity']['family'] == 'qwen_image' and p['lora_policy'].get('strategy') == 'model_and_clip' for p in profiles), "detail": "LoRA strategy remains family-specific."},
        {"id": "qwen_edit_policy_gap_explicit", "passed": all(p['family_policy']['resolution_state'] == 'matched_registry_policy' and p['conditioning_policy'].get('strategy') == 'qwen_image_edit_plus_single_canvas' for p in profiles if p['identity']['family'] in {'qwen_image_edit_2509', 'qwen_image_edit_2511'}), "detail": "Qwen Image Edit 2509/2511 own edit-specific policies and do not borrow plain Qwen conditioning."},
        {"id": "z_image_base_identity_gap_explicit", "passed": all(p['onboarding']['state'] == 'blocked_variant_identity' for p in profiles if p['identity']['family'] == 'z_image_base'), "detail": "Z-Image Base public family identity must be resolved before activation."},
        {"id": "onboarded_diagnostics_fail_closed_without_backend", "passed": qwen_report['status'] == 'blocked_missing_nodes' and any(item['code'] == 'backend_capability_snapshot_unavailable' for item in qwen_report['blockers']) and qwen_report['expansion_scaffold'].get('onboarding_state') == 'onboarded_phase10', "detail": "Onboarded routes still fail closed when no live backend capability snapshot exists."},
        {"id": "provider_publishes_summary", "passed": source['provider'].count('payload["lanpaint_family_expansion"] = lanpaint_family_expansion_summary()') == 2, "detail": "Online and offline profiles publish the compact matrix."},
        {"id": "runtime_dispatch_is_family_aware", "passed": source['provider'].count('compile_lanpaint_family_inpaint(') == 1, "detail": "Provider dispatch uses one exact family-aware LanPaint compiler entry."},
        {"id": "frontend_activation_is_exact", "passed": all(name in policy_block for name in ('krea2_turbo','qwen_image','z_image','z_image_turbo','sdxl','sd15','sd35','flux')) and 'qwen_image_edit_2509' not in policy_block and 'z_image_base' not in policy_block, "detail": "Frontend activation includes only implemented families and excludes unresolved aliases and loader ecosystems."},
        {"id": "portable_paths_only", "passed": all(token not in source['data'] for token in ('/' + 'home/', '/' + 'mnt/' + 'data', 'C:' + '\\\\', 'D:' + '\\\\')), "detail": "Expansion data contains no personal or machine-specific paths."},
    ]
    return {
        "schema_id": "neo.validation.lanpaint_route_family_phase9.v1",
        "phase": 9,
        "title": "LanPaint family expansion scaffolding",
        "passed": all(item["passed"] for item in checks),
        "checks_passed": sum(1 for item in checks if item["passed"]),
        "checks_total": len(checks),
        "checks": checks,
        "registry_fingerprint": registry["registry_fingerprint"],
        "compatibility_matrix": matrix,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit LanPaint Phase 9 family expansion scaffolding.")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    result = run_audit()
    print(json.dumps(result, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
