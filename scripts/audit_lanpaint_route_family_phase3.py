from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.image.lanpaint_family_policies import (
    AUTHORITY,
    COMPLETE_POLICY_STATE,
    PLACEHOLDER_POLICY_STATE,
    POLICY_STATE,
    REGISTRY_SCHEMA_ID,
    SCHEMA_ID,
    lanpaint_family_policy_fingerprint,
    lanpaint_family_policy_registry,
    resolve_lanpaint_family_policy,
    validate_lanpaint_family_policy,
)
from neo_app.image.lanpaint_route_contract import ROUTE_FAMILY_ID
from scripts.audit_lanpaint_route_family_phase0 import build_report as build_phase0_report
from scripts.audit_lanpaint_route_family_phase1 import build_report as build_phase1_report
from scripts.audit_lanpaint_route_family_phase2 import build_report as build_phase2_report

AUDIT_SCHEMA_ID = "neo.image.lanpaint_route_family_phase3_audit.v1"
DATE = "2026-08-04"

POLICY_ONLY_FILES = {
    "neo_app/image/lanpaint_route_contract.py",
    "neo_app/image/lanpaint_route_contract.schema.json",
    "neo_app/image/lanpaint_workflow_abstraction.py",
    "neo_app/image/lanpaint_workflow_abstraction.schema.json",
    "neo_app/image/lanpaint_family_policies.py",
    "neo_app/image/lanpaint_family_policy.schema.json",
    "neo_app/image/lanpaint_family_expansion.py",
    "neo_app/image/lanpaint_family_expansion.schema.json",
    "neo_app/image/lanpaint_family_expansion_profiles.json",
    "neo_app/image/lanpaint_ui_state.py",
    "neo_app/image/lanpaint_ui_state.schema.json",
    "neo_app/providers/comfy_workflows/lanpaint.py",
    "neo_app/providers/comfy_workflows/lanpaint_compiler.schema.json",
}


def _route_family_occurrences() -> dict[str, list[str]]:
    policy_only: list[str] = []
    production: list[str] = []
    files = list((ROOT / "neo_app").rglob("*.py"))
    files += list((ROOT / "neo_app").rglob("*.json"))
    files += list((ROOT / "neo_extensions" / "built_in").rglob("extension_manifest.json"))
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if ROUTE_FAMILY_ID not in text:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in POLICY_ONLY_FILES:
            policy_only.append(relative)
        else:
            production.append(relative)
    return {"policy_only": sorted(policy_only), "production": sorted(production)}


def _route_request(*, family: str, loader: str, provider: str = "comfyui_portable", overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "identity": {
            "provider_id": provider,
            "family": family,
            "loader": loader,
            "mode": "inpaint",
            "engine": "lanpaint",
            "variant": "default",
        }
    }
    if overrides:
        payload.update(overrides)
    return payload


def build_report() -> dict[str, Any]:
    registry = lanpaint_family_policy_registry()
    policies = registry["policies"]
    complete = [item for item in policies if item["identity"]["status"] == COMPLETE_POLICY_STATE]
    placeholders = [item for item in policies if item["identity"]["status"] == PLACEHOLDER_POLICY_STATE]
    krea = next((item for item in complete if item["identity"]["family"] == "krea2_turbo"), {})
    krea_identity = krea.get("identity", {})
    krea_defaults = krea.get("route_defaults", {})
    krea_sampler = krea_defaults.get("sampler_policy", {})
    krea_lora = krea.get("lora_policy", {})
    krea_nodes = set(krea.get("node_requirements", {}).get("required_node_classes", []))

    gguf_resolution = resolve_lanpaint_family_policy(_route_request(family="krea2_turbo", loader="gguf"))
    native_resolution = resolve_lanpaint_family_policy(_route_request(family="krea2_turbo", loader="diffusion_model", provider="comfyui"))
    explicit_resolution = resolve_lanpaint_family_policy(
        _route_request(
            family="krea2_turbo",
            loader="gguf",
            overrides={
                "crop_policy": {"padding_px": 224},
                "sampler_policy": {"lanpaint_thinking_steps": 12},
            },
        )
    )
    qwen_resolution = resolve_lanpaint_family_policy(_route_request(family="qwen_image", loader="gguf"))
    zimage_resolution = resolve_lanpaint_family_policy(_route_request(family="z_image", loader="diffusion_model"))
    occurrences = _route_family_occurrences()
    schema_path = ROOT / "neo_app" / "image" / "lanpaint_family_policy.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    previous = {
        "phase0": build_phase0_report(),
        "phase1": build_phase1_report(),
        "phase2": build_phase2_report(),
    }

    placeholder_defaults_empty = all(
        not any(bool(value) for value in item.get("route_defaults", {}).values())
        and item.get("placeholder", {}).get("inherits_defaults_from") is None
        for item in placeholders
    )
    placeholder_families = {item["identity"]["family"] for item in placeholders}

    checks = [
        {
            "id": "registry_identity_and_policy_only_state",
            "passed": registry["schema_id"] == REGISTRY_SCHEMA_ID and registry["authority"] == AUTHORITY and registry["policy_state"] == POLICY_STATE,
            "detail": "The registry publishes the locked Phase 3 schema, authority, and non-executable policy-only state.",
        },
        {
            "id": "phase10_complete_policies_are_additive",
            "passed": {item["identity"]["family"] for item in complete} == {"anima", "flux", "flux2_dev", "flux2_klein", "hidream", "ideogram4", "krea2_turbo", "qwen_image", "qwen_image_edit_2509", "qwen_image_edit_2511", "z_image", "z_image_turbo", "sdxl", "sd15", "sd35"} and krea_identity.get("family") == "krea2_turbo",
            "detail": "The Phase 3 Krea policy remains intact while later onboarding adds complete, family-owned Qwen, Z-Image, SD, Flux.1, Flux.2, HiDream-I1, Anima, and Ideogram 4 policies.",
        },
        {
            "id": "krea2_loader_branches_are_explicit",
            "passed": set(krea_identity.get("loader_ids", [])) == {"gguf", "diffusion_model"} and set(krea.get("loader_policies", {})) == {"gguf", "diffusion_model"},
            "detail": "Krea 2 Turbo declares separate GGUF and safetensors/component loader policies.",
        },
        {
            "id": "krea2_encoder_vae_and_conditioning_are_locked",
            "passed": (
                krea.get("text_encoder_policy", {}).get("required_clip_type") == "krea2"
                and "qwen3vl_4b_gguf" in krea.get("text_encoder_policy", {}).get("rejected_asset_classifications", [])
                and krea.get("vae_policy", {}).get("vae_role") == "qwen_image_vae"
                and krea.get("conditioning_policy", {}).get("negative", {}).get("negative_conditioning_policy") == "zero_out_positive_conditioning"
            ),
            "detail": "Krea 2 Turbo keeps Qwen3-VL-4B native, uses the Qwen Image VAE, and zeroes negative conditioning.",
        },
        {
            "id": "submitted_krea2_route_defaults_are_family_scoped",
            "passed": (
                krea_defaults.get("crop_policy", {}).get("padding_px") == 152
                and krea_defaults.get("crop_policy", {}).get("processing_size", {}).get("width") == 768
                and krea_defaults.get("mask_policy", {}).get("sampling", {}).get("expand_px") == 45
                and krea_defaults.get("mask_policy", {}).get("stitch", {}).get("blur_radius") == 9.1
                and krea_sampler.get("steps") == 8
                and krea_sampler.get("cfg") == 1.0
                and krea_sampler.get("lanpaint_thinking_steps") == 10
            ),
            "detail": "The submitted crop/stitch/sampler values are resolved only inside the Krea 2 Turbo policy.",
        },
        {
            "id": "krea2_lora_is_experimental_model_only",
            "passed": (
                krea_lora.get("lora_support_state") == "experimental"
                and krea_lora.get("lora_injection_strategy") == "model_only"
                and krea_lora.get("loader_node_class") == "LoraLoaderModelOnly"
                and krea_lora.get("allow_multiple") is True
            ),
            "detail": "The shared Neo LoRA Stack is declared as an experimental model-only transform for Krea 2 Turbo.",
        },
        {
            "id": "krea2_sample_role_nodes_are_declared",
            "passed": all(
                node in krea_nodes
                for node in {
                    "CropByMask",
                    "ImageResizeKJv2",
                    "GrowMaskWithBlur",
                    "SetLatentNoiseMask",
                    "DifferentialDiffusionAdvanced",
                    "LanPaint_KSampler",
                    "ImageCompositeMasked",
                }
            ),
            "detail": "Required reusable nodes from the submitted workflow are family-policy requirements, while authoring helpers stay excluded.",
        },
        {
            "id": "krea2_gguf_and_native_contracts_resolve_without_execution",
            "passed": (
                gguf_resolution["resolution_state"] == "resolved_policy_only"
                and native_resolution["resolution_state"] == "resolved_policy_only"
                and gguf_resolution["resolved_contract"]["family_policy"]["loader_policy"]["preferred_node_class"] == "UnetLoaderGGUF"
                and native_resolution["resolved_contract"]["family_policy"]["loader_policy"]["preferred_node_class"] == "UNETLoader"
                and gguf_resolution["resolved_contract"]["execution"]["enabled"] is False
                and native_resolution["resolved_contract"]["execution"]["enabled"] is False
            ),
            "detail": "Both Krea 2 Turbo loaders resolve family semantics but remain non-selectable and non-compiled.",
        },
        {
            "id": "explicit_route_values_override_family_defaults",
            "passed": (
                explicit_resolution["resolved_contract"]["crop_policy"]["padding_px"] == 224
                and explicit_resolution["resolved_contract"]["sampler_policy"]["lanpaint_thinking_steps"] == 12
                and explicit_resolution["resolved_contract"]["sampler_policy"]["steps"] == 8
            ),
            "detail": "Explicit route values survive policy resolution while missing values are filled from the family overlay.",
        },
        {
            "id": "phase10_families_do_not_inherit_krea",
            "passed": (
                qwen_resolution["resolution_state"] == "resolved_policy_only"
                and zimage_resolution["resolution_state"] == "resolved_policy_only"
                and qwen_resolution["resolved_contract"]["sampler_policy"]["steps"] == 20
                and qwen_resolution["resolved_contract"]["sampler_policy"]["cfg"] == 4.0
                and zimage_resolution["resolved_contract"]["sampler_policy"]["steps"] == 35
                and zimage_resolution["resolved_contract"]["latent_policy"]["aura_shift"] == 3.0
                and placeholder_defaults_empty
                and placeholder_families == {"hunyuan_image", "krea2", "qwen_image_edit", "z_image_base"}
            ),
            "detail": "Phase 10 Qwen and Z-Image policies own AuraFlow, conditioning and sampler values; unresolved aliases and the held HunyuanImage family remain empty placeholders.",
        },
        {
            "id": "all_policies_validate_and_fingerprint",
            "passed": all(not validate_lanpaint_family_policy(item) and item["policy_fingerprint"] == lanpaint_family_policy_fingerprint(item) for item in policies),
            "detail": "Every complete or placeholder policy validates and has a deterministic fingerprint.",
        },
        {
            "id": "public_schema_matches_authority",
            "passed": schema.get("$id") == SCHEMA_ID and schema.get("properties", {}).get("execution", {}).get("properties", {}).get("enabled", {}).get("const") is False,
            "detail": "The public JSON schema matches the Python policy authority and locks execution off.",
        },
        {
            "id": "previous_phase_audits_still_pass",
            "passed": all(report.get("status") == "passed" for report in previous.values()),
            "detail": "Phase 0, Phase 1, and Phase 2 baseline/contract/abstraction gates remain green.",
        },
        {
            "id": "later_production_activation_is_scoped",
            "passed": set(occurrences["production"]).issubset({
                "neo_app/image/lanpaint_family_adapter.py",
                "neo_app/models/route_matrix.py",
                "neo_app/providers/compile_router.py",
                "neo_app/providers/comfy_provider.py",
                "neo_app/providers/comfy_workflows/lanpaint_family.py",
            }),
            "detail": "The policy layer remains independent; existing activation is scoped through the Phase 13 adapter registry and the exact router/provider/compiler files.",
        },
    ]

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "policy_authority": {
            "module": AUTHORITY,
            "schema_id": SCHEMA_ID,
            "registry_schema_id": REGISTRY_SCHEMA_ID,
            "state": POLICY_STATE,
            "route_family_id": ROUTE_FAMILY_ID,
        },
        "registry": registry,
        "resolutions": {
            "krea2_turbo_gguf": gguf_resolution,
            "krea2_turbo_diffusion_model": native_resolution,
            "krea2_turbo_explicit_override": explicit_resolution,
            "qwen_image_phase10_policy": qwen_resolution,
            "z_image_phase10_policy": zimage_resolution,
        },
        "previous_phase_status": {key: value["status"] for key, value in previous.items()},
        "route_family_occurrences": occurrences,
        "checks": checks,
        "summary": {
            "passed": sum(1 for item in checks if item["passed"]),
            "failed": len(failed),
            "total": len(checks),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the Phase 3 LanPaint family-policy layer without enabling workflow compilation.")
    parser.add_argument("--json-out", type=Path, help="Optional output path for the machine-readable report.")
    args = parser.parse_args()

    report = build_report()
    output = json.dumps(report, indent=2, ensure_ascii=False)
    print(output)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(output + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
