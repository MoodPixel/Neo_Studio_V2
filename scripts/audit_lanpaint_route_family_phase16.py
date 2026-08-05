from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE16_STATE, PHASE17_STATE, PHASE18_STATE, get_lanpaint_family_adapter, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support
from tests.test_lanpaint_route_family_phase16_flux1_onboarding import (
    PHASE16_ACTIVE,
    _assets,
    _capabilities,
    _classes,
    _compile,
)

SCHEMA_ID = "neo.validation.lanpaint_route_family_phase16.v1"
DATE = "2026-08-05"


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def build_report() -> dict[str, Any]:
    registry = lanpaint_family_adapter_registry("comfyui_portable")
    adapters = {item["identity"]["route_key"]: item for item in registry["adapters"]}
    prior_fingerprints = {
        "krea2_turbo:diffusion_model:inpaint:lanpaint": "a612a352cdd274ba92ff729258710c47ad54b67a7b351037715b079993f173f5",
        "krea2_turbo:gguf:inpaint:lanpaint": "3a776817ecec7c1b2f9bd2cf1ca498f728ecbda1fdecf39477b02d5224fb51a3",
        "qwen_image:diffusion_model:inpaint:lanpaint": "8973b4959ad180c2fec89283107e54dac716a0b6037fb65fb213ff2e0e4292cc",
        "qwen_image:gguf:inpaint:lanpaint": "6fc313f0e63fcb0ad75a49c151f408330a2ad3b260dde1ffe3282b2475c12335",
        "sd15:checkpoint:inpaint:lanpaint": "c209e553f338385a9c037e85052930f5fec9f4446e0a229467127f55acabd53e",
        "sd35:diffusion_model:inpaint:lanpaint": "ad8e1a5f9fbe49a71798af032b70691b19071caf048a2384b011057a1e63e725",
        "sd35:gguf:inpaint:lanpaint": "21ad67b4d251557b9dcda78c678af4656e3363432cabd161b7442bc4c44162cf",
        "sdxl:checkpoint:inpaint:lanpaint": "c601f2db11d134f9dac59f728b447bb0b2082c6aa4dee13328a4040329eb515b",
        "z_image:diffusion_model:inpaint:lanpaint": "fae299eddbe4e93a29a771403aafa46a365e6344740c1abd0472412bbf3703db",
        "z_image:gguf:inpaint:lanpaint": "a19000001da12db7be15c43262b5ad72c0693506d3b274898cab09faa103768e",
        "z_image_turbo:diffusion_model:inpaint:lanpaint": "db5aa5eb63546eafed429c43405876a260755632d99dba2e3247c9e897a0567b",
        "z_image_turbo:gguf:inpaint:lanpaint": "a669a8f058de6163d9877a8c02ac262a4b3331eea3b8575c2178abc8fc9a3c29",
    }

    native_dev = _compile("diffusion_model", variant="dev")
    native_schnell = _compile("diffusion_model", variant="schnell")
    gguf_dev = _compile("gguf", variant="dev")
    gguf_schnell = _compile("gguf", variant="schnell")
    compiled = [native_dev, native_schnell, gguf_dev, gguf_schnell]
    plain = _compile("gguf", capabilities=_capabilities("gguf", include_lora=False))
    explicit = _compile("gguf", explicit_lora=True)
    lora_profile = explicit.backend_payload["actual_params"]["_neo_lora_patch_profile"]
    blocked = evaluate_lanpaint_route_capabilities(
        _capabilities("gguf", remove="DualCLIPLoaderGGUF"), provider_id="comfyui_portable",
        family="flux", loader="gguf", selected_assets=_assets("gguf"),
    )
    policy = get_lanpaint_family_policy("flux")
    profiles = [get_lanpaint_family_expansion_profile("flux", loader=loader, provider_id="comfyui_portable") for loader in ("diffusion_model", "gguf")]
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    public_sources = [
        ROOT / "neo_app/image/lanpaint_family_adapter.py", ROOT / "neo_app/image/lanpaint_family_policies.py",
        ROOT / "neo_app/image/lanpaint_capabilities.py", ROOT / "neo_app/providers/comfy_workflows/lanpaint_flux.py",
        ROOT / "neo_app/static/js/neo.js",
    ]
    path_pattern = re.compile(r"(?:/(?:home|Users|mnt)/|[A-Za-z]:\\(?:Users|Documents and Settings)\\)")
    path_hits = [path.relative_to(ROOT).as_posix() for path in public_sources if path_pattern.search(path.read_text(encoding="utf-8"))]

    checks = [
        _check("exact_phase16_bindings", registry["onboarding_state"] == PHASE18_STATE and len(registry["adapters"]) == 28 and {item["identity"]["route_key"] for item in registry["adapters"] if item["binding"].get("new_route_activated_by_phase16")} == PHASE16_ACTIVE, "Exactly the two Flux.1 family/loader bindings are activated in Phase 16."),
        _check("prior_fingerprints_stable", all(adapters[key]["adapter_fingerprint"] == value for key, value in prior_fingerprints.items()), "All twelve Phase 15 active adapter fingerprints remain stable for replay compatibility."),
        _check("policy_complete", policy["validation"]["ok"] and set(policy["route_defaults"]["variant_profiles"]) == {"dev", "schnell"}, "Flux.1 policy is complete and owns explicit Dev/Schnell profiles."),
        _check("expansion_profiles_bound", all(item and item["onboarding"]["state"] == "onboarded_phase16" and item["execution"]["selectable"] for item in profiles), "Both Flux.1 expansion profiles are compiler-bound and experimental."),
        _check("all_variants_compile", all(item.compile_status == "compiled" for item in compiled), "Dev and Schnell compile on both safetensors/components and GGUF."),
        _check("variant_defaults_separate", native_dev.backend_payload["actual_params"]["steps"] == 30 and native_schnell.backend_payload["actual_params"]["steps"] == 4 and native_dev.backend_payload["actual_params"]["lanpaint_controls"]["thinking_steps"] == 5 and native_schnell.backend_payload["actual_params"]["lanpaint_controls"]["thinking_steps"] == 2, "Dev and Schnell retain separate sampling and thinking defaults."),
        _check("cfg_prompt_semantics_locked", all(item.backend_payload["actual_params"]["cfg"] == 1.0 for item in compiled) and all(next(node for node in item.backend_payload["prompt"].values() if node["class_type"] == "LanPaint_KSampler")["inputs"]["LanPaint_PromptMode"] == "Image First" for item in compiled), "Flux.1 enforces CFG 1.0 and Image First semantics."),
        _check("loader_parity", "UNETLoader" in _classes(native_dev) and "DualCLIPLoader" in _classes(native_dev) and "UnetLoaderGGUF" in _classes(gguf_dev) and "DualCLIPLoaderGGUF" in _classes(gguf_dev), "Safetensors and GGUF use their exact model and dual-encoder loaders."),
        _check("gguf_transform_isolated", "ModelSamplingFlux" not in _classes(native_dev) and "ModelSamplingFlux" in _classes(gguf_dev), "ModelSamplingFlux is emitted only for the GGUF branch."),
        _check("family_graph_semantics", all("FluxGuidance" in _classes(item) and "ConditioningZeroOut" in _classes(item) and "DifferentialDiffusionAdvanced" not in _classes(item) and "ModelSamplingAuraFlow" not in _classes(item) for item in compiled), "Flux.1 uses Flux guidance and zeroed negative conditioning without borrowing Krea or AuraFlow transforms."),
        _check("capability_gate_exact", blocked["status"] == "blocked_missing_nodes" and any("DualCLIPLoaderGGUF" in item["message"] for item in blocked["blockers"]), "Missing GGUF dual-encoder support fails closed."),
        _check("plain_lanpaint_no_lora_dependency", plain.compile_status == "compiled" and not any(node["class_type"].startswith("LoraLoader") for node in plain.backend_payload["prompt"].values()), "Plain Flux.1 LanPaint has no LoRA dependency."),
        _check("explicit_lora_engine_independent", lora_profile["compatibility_route_key"] == "flux:gguf:inpaint" and lora_profile["workflow_route_key"] == "flux:gguf:inpaint:lanpaint" and route_support("comfyui", "flux", "gguf", "inpaint", engine="native")["compatibility_route_key"] == route_support("comfyui", "flux", "gguf", "inpaint", engine="lanpaint")["compatibility_route_key"], "Explicit LoRA uses the engine-independent Flux family/loader compatibility key."),
        _check("replay_variant_lineage", all(item.backend_payload["actual_params"]["lanpaint_replay"]["route"]["family"] == "flux" and item.backend_payload["actual_params"]["lanpaint_replay"]["family_adapter"]["adapter_fingerprint"] == item.backend_payload["actual_params"]["lanpaint_family_adapter_fingerprint"] for item in compiled), "Replay remains bound to Flux.1 route and adapter lineage while actual params preserve Dev/Schnell."),
        _check("frontend_and_public_hygiene", all(marker in js for marker in ("lanpaint.flux1.v1", "FluxGuidance", "flux1_dev_schnell", "lanpaint_flux1_family_phase16_20260805")) and "phase16=lanpaint_flux1_family_20260805" in index and not path_hits, "Frontend/cache records Phase 16 and public sources contain no personal paths."),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID, "schema_version": 1, "date": DATE, "phase": 16,
        "title": "LanPaint Flux.1 family onboarding", "status": "passed" if not failed else "failed",
        "routes": sorted(PHASE16_ACTIVE), "adapter_registry_fingerprint": registry["registry_fingerprint"], "path_hits": path_hits,
        "physical_validation": {"status": "not_run", "reason": "The packaging environment does not host a live target ComfyUI profile with Flux.1 Dev/Schnell assets.", "required_next": "Run Dev and Schnell on both loader branches with LoRA off/on, inspect crop/stitch output, and replay persisted results before promotion."},
        "checks": checks,
        "summary": {"passed": len(checks) - len(failed), "failed": len(failed), "total": len(checks), "failed_ids": [item["id"] for item in failed]},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit Phase 16 LanPaint Flux.1 family onboarding.")
    parser.add_argument("--output", type=Path); parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args(); report = build_report()
    text = json.dumps(report, indent=2 if (args.pretty or args.output) else None, ensure_ascii=False, sort_keys=not (args.pretty or args.output)) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True); args.output.write_text(text, encoding="utf-8")
    print(text, end=""); return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
