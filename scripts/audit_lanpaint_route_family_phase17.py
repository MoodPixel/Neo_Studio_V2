from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

from neo_app.image.lanpaint_capabilities import evaluate_lanpaint_route_capabilities
from neo_app.image.lanpaint_family_adapter import PHASE17_STATE, PHASE18_STATE, lanpaint_family_adapter_registry
from neo_app.image.lanpaint_family_expansion import get_lanpaint_family_expansion_profile
from neo_app.image.lanpaint_family_policies import get_lanpaint_family_policy
from neo_extensions.built_in.lora_stack.backend.support_matrix import route_support
from tests.test_lanpaint_route_family_phase17_flux2_onboarding import (
    PHASE16_FINGERPRINTS,
    PHASE17_ACTIVE,
    _assets,
    _capabilities,
    _classes,
    _compile,
)

SCHEMA_ID = "neo.validation.lanpaint_route_family_phase17.v1"
DATE = "2026-08-05"


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def build_report() -> dict[str, Any]:
    registry = lanpaint_family_adapter_registry("comfyui_portable")
    adapters = {item["identity"]["route_key"]: item for item in registry["adapters"]}

    dev_native = _compile("flux2_dev", "diffusion_model", "dev")
    dev_gguf = _compile("flux2_dev", "gguf", "dev")
    klein_native = _compile("flux2_klein", "diffusion_model", "klein_4b")
    klein_gguf = _compile("flux2_klein", "gguf", "klein_9b_distilled")
    compiled = [dev_native, dev_gguf, klein_native, klein_gguf]

    klein_profiles = [
        _compile("flux2_klein", loader, variant)
        for loader in ("diffusion_model", "gguf")
        for variant in ("klein_4b", "klein_4b_distilled", "klein_9b", "klein_9b_distilled")
    ]
    plain = _compile(
        "flux2_dev",
        "diffusion_model",
        "dev",
        capabilities=_capabilities("flux2_dev", "diffusion_model", "dev", include_lora=False),
    )
    explicit = _compile("flux2_dev", "diffusion_model", "dev", explicit_lora=True)
    lora_profile = explicit.backend_payload["actual_params"]["_neo_lora_patch_profile"]

    blocked_caps = _capabilities("flux2_dev", "gguf", "dev")
    blocked_caps["loaders"]["gguf"]["roles"]["flux2_mistral3_text_encoder"]["available"] = False
    blocked = evaluate_lanpaint_route_capabilities(
        blocked_caps,
        provider_id="comfyui_portable",
        family="flux2_dev",
        loader="gguf",
        selected_assets=_assets("flux2_dev", "gguf", "dev"),
    )

    wrong_family = _compile(
        "flux2_dev", "diffusion_model", "dev", model_override="flux-2-klein-4b.safetensors"
    )
    wrong_encoder = _compile(
        "flux2_dev", "diffusion_model", "dev", encoder_override="qwen_3_4b.safetensors"
    )
    klein_mismatch = _compile(
        "flux2_klein", "diffusion_model", "klein_9b", encoder_override="qwen_3_4b.safetensors"
    )

    policies = {family: get_lanpaint_family_policy(family) for family in ("flux2_dev", "flux2_klein")}
    profiles = [
        get_lanpaint_family_expansion_profile(family, loader=loader, provider_id="comfyui_portable")
        for family in ("flux2_dev", "flux2_klein")
        for loader in ("diffusion_model", "gguf")
    ]

    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    public_sources = [
        ROOT / "neo_app/image/lanpaint_family_adapter.py",
        ROOT / "neo_app/image/lanpaint_family_policies.py",
        ROOT / "neo_app/image/lanpaint_capabilities.py",
        ROOT / "neo_app/providers/comfy_workflows/lanpaint_flux2.py",
        ROOT / "neo_app/static/js/neo.js",
    ]
    path_pattern = re.compile(r"(?:/(?:home|Users|mnt)/|[A-Za-z]:\\(?:Users|Documents and Settings)\\)")
    path_hits = [
        path.relative_to(ROOT).as_posix()
        for path in public_sources
        if path_pattern.search(path.read_text(encoding="utf-8"))
    ]

    dev_actual = dev_native.backend_payload["actual_params"]
    dev_gguf_actual = dev_gguf.backend_payload["actual_params"]
    klein_actual = klein_gguf.backend_payload["actual_params"]
    replay = klein_actual["lanpaint_replay"]
    checks = [
        _check(
            "exact_phase17_bindings",
            registry["onboarding_state"] == PHASE18_STATE
            and len(registry["adapters"]) == 28
            and {
                item["identity"]["route_key"]
                for item in registry["adapters"]
                if item["binding"].get("new_route_activated_by_phase17")
            }
            == PHASE17_ACTIVE,
            "Exactly the four Flux.2 Dev/Klein family-loader bindings are activated in Phase 17.",
        ),
        _check(
            "phase16_fingerprints_stable",
            all(adapters[key]["adapter_fingerprint"] == value for key, value in PHASE16_FINGERPRINTS.items()),
            "All fourteen Phase 16 active adapter fingerprints remain stable for replay compatibility.",
        ),
        _check(
            "family_policies_complete",
            all(policy and policy["validation"]["ok"] for policy in policies.values())
            and policies["flux2_dev"]["text_encoder_policy"]["required_clip_type"] == "flux2"
            and policies["flux2_klein"]["text_encoder_policy"]["required_clip_type"] == "flux2",
            "Flux.2 Dev and Klein have separate complete policies with the Flux2 conditioning contract.",
        ),
        _check(
            "expansion_profiles_bound",
            all(
                profile
                and profile["onboarding"]["state"] == "onboarded_phase17"
                and profile["execution"]["selectable"]
                and profile["execution"]["executable"]
                for profile in profiles
            ),
            "All four Phase 17 expansion profiles are compiler-bound and experimental.",
        ),
        _check(
            "all_loader_families_compile",
            all(item.compile_status == "compiled" for item in compiled),
            "Flux.2 Dev and Klein compile on both safetensors/components and GGUF transformer branches.",
        ),
        _check(
            "dev_encoder_boundary",
            "CLIPLoader" in _classes(dev_native)
            and "CLIPLoaderGGUF" not in _classes(dev_gguf)
            and dev_actual["mistral3_text_encoder"] == "mistral_3_small_flux2_bf16.safetensors"
            and dev_gguf_actual["mistral3_text_encoder"] == "mistral_3_small_flux2_bf16.safetensors",
            "Flux.2 Dev uses the native Mistral 3 Flux2 encoder on both transformer loader branches and does not claim GGUF Mistral support.",
        ),
        _check(
            "klein_variant_matrix",
            all(item.compile_status == "compiled" for item in klein_profiles)
            and {item.backend_payload["actual_params"]["flux_variant"] for item in klein_profiles}
            == {"klein_4b", "klein_4b_distilled", "klein_9b", "klein_9b_distilled"},
            "Klein preserves explicit 4B/9B and base/distilled variant identities on both loaders.",
        ),
        _check(
            "klein_defaults_separate",
            klein_native.backend_payload["actual_params"]["steps"] == 50
            and klein_actual["steps"] == 4
            and klein_native.backend_payload["actual_params"]["lanpaint_controls"]["thinking_steps"] == 3
            and klein_actual["lanpaint_controls"]["thinking_steps"] == 2,
            "Klein base and distilled routes retain separate step, guidance, and thinking defaults.",
        ),
        _check(
            "flux2_graph_semantics",
            all(
                "FluxGuidance" in _classes(item)
                and "ConditioningZeroOut" in _classes(item)
                and "ModelSamplingFlux" not in _classes(item)
                and "ModelSamplingAuraFlow" not in _classes(item)
                and "DifferentialDiffusionAdvanced" not in _classes(item)
                for item in compiled
            ),
            "Flux.2 uses Flux2 conditioning without inheriting Flux.1 GGUF transforms, AuraFlow, or Krea Differential Diffusion.",
        ),
        _check(
            "family_mismatch_fail_closed",
            wrong_family.compile_status == "mock_compiled"
            and wrong_encoder.compile_status == "mock_compiled"
            and klein_mismatch.compile_status == "mock_compiled"
            and any("dedicated flux2_klein" in item for item in wrong_family.backend_payload["validation"]["errors"])
            and any("Mistral 3" in item for item in wrong_encoder.backend_payload["validation"]["errors"])
            and any("Qwen3-8B" in item for item in klein_mismatch.backend_payload["validation"]["errors"]),
            "Dev/Klein family, encoder architecture, and Klein scale mismatches fail closed.",
        ),
        _check(
            "capability_gate_exact",
            blocked["status"] == "blocked_missing_nodes"
            and blocked["selectable"] is False
            and any(item["code"] == "text_encoder_unavailable" for item in blocked["blockers"]),
            "Missing Dev Mistral encoder evidence blocks only the exact selected route.",
        ),
        _check(
            "plain_lanpaint_no_lora_dependency",
            plain.compile_status == "compiled"
            and not any(node["class_type"].startswith("LoraLoader") for node in plain.backend_payload["prompt"].values()),
            "Plain Flux.2 LanPaint has no LoRA loader dependency.",
        ),
        _check(
            "explicit_lora_engine_independent",
            lora_profile["route"]["compatibility_route_key"] == "flux2_dev:diffusion_model:inpaint"
            and lora_profile["route"]["workflow_route_key"] == "flux2_dev:diffusion_model:inpaint:lanpaint"
            and route_support("comfyui_portable", "flux2_dev", "diffusion_model", "inpaint", engine="native")["compatibility_route_key"]
            == route_support("comfyui_portable", "flux2_dev", "diffusion_model", "inpaint", engine="lanpaint")["compatibility_route_key"],
            "Explicit LoRA uses the engine-independent Flux.2 family/loader compatibility key while the compiler owns LanPaint anchors.",
        ),
        _check(
            "replay_exact_lineage",
            replay["route"]["family"] == "flux2_klein"
            and replay["route"]["loader"] == "gguf"
            and klein_actual["lanpaint_route"]["variant"] == "klein_9b_distilled"
            and replay["family_adapter"]["adapter_fingerprint"] == klein_actual["lanpaint_family_adapter_fingerprint"],
            "Replay preserves exact Flux.2 family, loader, Klein variant, encoder, and adapter lineage.",
        ),
        _check(
            "frontend_and_public_hygiene",
            all(
                marker in js
                for marker in (
                    "lanpaint.flux2_dev.v1",
                    "lanpaint.flux2_klein.v1",
                    "lanpaint.flux2_klein.v1",
                    "lanpaint_flux2_family_phase17_20260805",
                )
            )
            and "phase17=lanpaint_flux2_family_20260805" in index
            and not path_hits,
            "Frontend/cache records Phase 17 and public sources contain no personal paths.",
        ),
    ]

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "phase": 17,
        "title": "LanPaint Flux.2 Dev and Klein onboarding",
        "status": "passed" if not failed else "failed",
        "routes": sorted(PHASE17_ACTIVE),
        "adapter_registry_fingerprint": registry["registry_fingerprint"],
        "path_hits": path_hits,
        "physical_validation": {
            "status": "not_run",
            "reason": "The packaging environment does not host a live target ComfyUI profile with Flux.2 Dev/Klein models and encoders.",
            "required_next": "Run Dev and every supported Klein variant on both loader branches with LoRA off/on, inspect crop/stitch output, and replay persisted results before promotion.",
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
    parser = argparse.ArgumentParser(description="Audit Phase 17 LanPaint Flux.2 Dev and Klein onboarding.")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(
        report,
        indent=2 if (args.pretty or args.output) else None,
        ensure_ascii=False,
        sort_keys=not (args.pretty or args.output),
    ) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
