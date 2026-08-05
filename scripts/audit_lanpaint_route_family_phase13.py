from __future__ import annotations

import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import (
    PHASE13_STATE,
    PHASE14_STATE,
    REGISTRY_SCHEMA_ID,
    SCHEMA_ID,
    get_lanpaint_family_adapter,
    lanpaint_family_adapter_fingerprint,
    lanpaint_family_adapter_registry,
)
from neo_app.image.lanpaint_replay import build_lanpaint_replay_contract, validate_lanpaint_replay_request
from neo_app.image.lanpaint_ui_state import normalize_lanpaint_ui_state
from neo_app.providers.compile_router import select_comfy_compile_route
from tests.test_lanpaint_route_family_phase5_krea2_turbo_gguf import _compile as compile_krea, _job as krea_job
from tests.test_lanpaint_route_family_phase10_qwen_zimage import _capabilities, _compile as compile_family, _job as family_job
from tests.test_lanpaint_route_family_phase11_replay_lineage import _input_assets

AUDIT_SCHEMA_ID = "neo.validation.lanpaint_route_family_phase13.v1"
DATE = "2026-08-05"
ACTIVE = {
    "krea2_turbo:diffusion_model:inpaint:lanpaint",
    "krea2_turbo:gguf:inpaint:lanpaint",
    "qwen_image:diffusion_model:inpaint:lanpaint",
    "qwen_image:gguf:inpaint:lanpaint",
    "z_image:diffusion_model:inpaint:lanpaint",
    "z_image:gguf:inpaint:lanpaint",
    "z_image_turbo:diffusion_model:inpaint:lanpaint",
    "z_image_turbo:gguf:inpaint:lanpaint",
}

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
    registry = lanpaint_family_adapter_registry("comfyui")
    adapters = registry.get("adapters") or []
    by_key = {(item["identity"]["family"], item["identity"]["loader"]): item for item in adapters}
    krea = by_key[("krea2_turbo", "gguf")]
    krea_safe = by_key[("krea2_turbo", "diffusion_model")]
    qwen = by_key[("qwen_image", "gguf")]
    zimage = by_key[("z_image", "diffusion_model")]
    turbo = by_key[("z_image_turbo", "diffusion_model")]
    placeholders = [by_key[("qwen_image_edit", "gguf")], by_key[("z_image_base", "gguf")]]

    compiled_krea = compile_krea(krea_job())
    compiled_qwen = compile_family("qwen_image", "gguf")
    krea_portable = get_lanpaint_family_adapter("krea2_turbo", loader="gguf", provider_id="comfyui_portable")
    qwen_portable = get_lanpaint_family_adapter("qwen_image", loader="gguf", provider_id="comfyui_portable")
    zimage_portable = get_lanpaint_family_adapter("z_image", loader="diffusion_model", provider_id="comfyui_portable")
    krea_actual = compiled_krea.backend_payload["actual_params"]
    qwen_actual = compiled_qwen.backend_payload["actual_params"]
    krea_classes = [node["class_type"] for node in compiled_krea.backend_payload["prompt"].values()]
    qwen_classes = [node["class_type"] for node in compiled_qwen.backend_payload["prompt"].values()]

    cap = evaluate_lanpaint_route_capabilities(
        _capabilities("z_image", "diffusion_model"), provider_id="comfyui_portable",
        family="z_image", loader="diffusion_model", mode="inpaint", engine="lanpaint",
    )
    ui = normalize_lanpaint_ui_state(
        {}, provider_id="comfyui_portable", family="z_image", loader="diffusion_model",
        mode="inpaint", engine="lanpaint",
    )

    replay = build_lanpaint_replay_contract(
        deepcopy(qwen_actual), provider_id="comfyui_portable", input_assets=_input_assets(),
        workflow_prompt=compiled_qwen.backend_payload["prompt"],
    )
    replay_params = {**deepcopy(qwen_actual), "lanpaint_replay": replay}
    replay_errors = validate_lanpaint_replay_request(
        replay_params, provider_id="comfyui_portable", family="qwen_image", loader="gguf"
    )
    tampered = deepcopy(replay_params)
    tampered["lanpaint_replay"]["family_adapter"]["adapter_fingerprint"] = "0" * 64
    drift_errors = validate_lanpaint_replay_request(
        tampered, provider_id="comfyui_portable", family="qwen_image", loader="gguf"
    )

    router_source = (ROOT / "neo_app/providers/compile_router.py").read_text(encoding="utf-8")
    provider_source = (ROOT / "neo_app/providers/comfy_provider.py").read_text(encoding="utf-8")
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    schema = json.loads((ROOT / "neo_app/image/lanpaint_family_adapter.schema.json").read_text(encoding="utf-8"))

    path_pattern = re.compile(r"(?:/(?:home|Users|mnt)/|[A-Za-z]:\\(?:Users|Documents and Settings|LLM)\\)")
    public_files = [
        ROOT / "neo_app/image/lanpaint_family_adapter.py",
        ROOT / "neo_app/image/lanpaint_family_adapter.schema.json",
        ROOT / "neo_app/providers/compile_router.py",
        ROOT / "neo_app/static/js/neo.js",
    ]
    path_hits = [path.relative_to(ROOT).as_posix() for path in public_files if path_pattern.search(path.read_text(encoding="utf-8"))]

    checks = [
        _check("adapter_schema_is_v2", schema.get("$id") == SCHEMA_ID and schema.get("properties", {}).get("schema_version", {}).get("const") == 2, "The public schema declares the universal LanPaint family adapter v2 contract."),
        _check("registry_is_deterministic", registry.get("schema_id") == REGISTRY_SCHEMA_ID and registry.get("registry_fingerprint") == lanpaint_family_adapter_registry("comfyui").get("registry_fingerprint"), "The adapter registry is deterministic for a provider."),
        _check("active_route_set_tracks_phase14_parity", set(registry.get("active_route_keys") or []) == ACTIVE | PHASE15_ACTIVE | PHASE16_ACTIVE | PHASE17_ACTIVE | PHASE18_ACTIVE and set(registry.get("new_routes_activated") or []) == ({"krea2_turbo:diffusion_model:inpaint:lanpaint"} | PHASE15_ACTIVE | PHASE16_ACTIVE | PHASE17_ACTIVE | PHASE18_ACTIVE), "Phase 13 remains the adapter authority while later phases add only explicit Krea, SD, Flux.1, Flux.2, and Qwen Edit compiler bindings."),
        _check("phase14_promotes_complete_krea_policy", krea_safe["binding"]["state"] == "compiler_bound" and krea_safe["binding"]["selectable"] and krea_safe.get("stabilization", {}).get("new_binding_activated"), "Krea2 Turbo safetensors is compiler-bound by Phase 14 without changing the adapter schema."),
        _check("placeholder_routes_stay_blocked", all(item["binding"]["state"] == "scaffold_only" and not item["binding"]["selectable"] for item in placeholders), "Qwen Edit and Z-Image Base placeholders remain non-selectable."),
        _check("adapter_fingerprints_are_immutable", all(item["adapter_fingerprint"] == lanpaint_family_adapter_fingerprint(item) for item in adapters), "Every adapter carries a deterministic architecture fingerprint independent of request control overrides."),
        _check("krea_contract_is_preserved", krea["binding"]["graph_profile"] == "krea2_differential_crop_stitch_v1" and krea["lora"]["mode"] == "model_only" and "DifferentialDiffusionAdvanced" in krea_classes and "ModelSamplingAuraFlow" not in krea_classes, "Krea2 keeps Differential Diffusion and model-only LoRA semantics."),
        _check("qwen_zimage_contracts_are_family_specific", qwen["loaders"]["text_encoder"]["clip_type"] == "qwen_image" and zimage["loaders"]["text_encoder"]["clip_type"] == "lumina2" and qwen["latent"]["aura_shift"] == 3.1 and zimage["latent"]["aura_shift"] == 3.0 and turbo["conditioning"]["negative"]["node_class"] == "ConditioningZeroOut", "Qwen and Z-Image keep distinct conditioning, encoder and AuraFlow policies."),
        _check("existing_graph_shapes_are_preserved", compiled_krea.compile_status == compiled_qwen.compile_status == "compiled" and "ModelSamplingAuraFlow" in qwen_classes and "DifferentialDiffusionAdvanced" not in qwen_classes, "Existing Krea and Aura graph topologies still compile without cross-family transforms."),
        _check("compiler_lineage_uses_adapter", krea_actual.get("_neo_lanpaint_phase13_state") == PHASE13_STATE and qwen_actual.get("_neo_lanpaint_phase13_state") == PHASE13_STATE and krea_actual.get("lanpaint_family_adapter_fingerprint") == krea_portable.get("adapter_fingerprint") and qwen_actual.get("lanpaint_family_adapter_fingerprint") == qwen_portable.get("adapter_fingerprint"), "Both graph compilers emit the exact adapter identity and fingerprint."),
        _check("capability_and_ui_share_adapter", cap.get("family_adapter", {}).get("adapter_fingerprint") == ui.get("family_adapter", {}).get("adapter_fingerprint") == zimage_portable.get("adapter_fingerprint"), "Capability gating and UI state consume one adapter identity."),
        _check("replay_records_and_validates_adapter", not replay_errors and replay.get("family_adapter", {}).get("adapter_fingerprint") == qwen_portable.get("adapter_fingerprint") and any("adapter" in item.lower() or "fingerprint" in item.lower() for item in drift_errors), "Replay preserves exact adapter lineage and fails closed on adapter drift."),
        _check("router_and_provider_use_registry", "get_lanpaint_family_adapter" in router_source and "lanpaint_routes =" not in router_source and "lanpaint_family_adapter_registry" in provider_source, "Router and discovery derive existing bindings from the adapter authority instead of duplicate route sets."),
        _check("frontend_uses_backend_adapter_registry", "imageLanpaintAdapterRegistry" in js and "imageLanpaintAdapterForRoute" in js and "adapter?.binding?.selectable" in js and "phase13=lanpaint_family_adapter_v2_20260805" in index and "phase14=lanpaint_route_parity_phase14_20260805" in index, "Frontend activation comes from the selected profile adapter registry; static data is fallback presentation only."),
        _check("no_personal_paths", not path_hits, "Phase 13 public source files contain no personal or machine-specific absolute paths."),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "adapter_authority": {
            "schema_id": SCHEMA_ID,
            "registry_schema_id": REGISTRY_SCHEMA_ID,
            "phase_state": PHASE13_STATE,
            "adapter_count": len(adapters),
            "active_route_keys": sorted(ACTIVE),
            "stabilization_state": PHASE14_STATE,
            "new_routes_activated": ["krea2_turbo:diffusion_model:inpaint:lanpaint"],
        },
        "path_hits": path_hits,
        "physical_validation": {
            "status": "not_run",
            "reason": "The packaging environment does not host the target ComfyUI profiles, model assets or custom-node installations.",
            "required_next": "Run both Krea2 Turbo loaders plus existing Qwen and Z-Image LanPaint routes and confirm physical parity before later family onboarding.",
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

    parser = argparse.ArgumentParser(description="Audit Phase 13 universal LanPaint family adapter v2.")
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
