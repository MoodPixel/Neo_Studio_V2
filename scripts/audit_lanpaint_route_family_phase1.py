from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.image.lanpaint_route_contract import (
    BASE_REQUIRED_STAGE_ROLES,
    BASE_STAGE_ORDER,
    ENGINE_ID,
    EXECUTION_STATE,
    MODE_ID,
    ROUTE_FAMILY_ID,
    SCHEMA_ID,
    SCHEMA_VERSION,
    build_lanpaint_route_key,
    lanpaint_contract_fingerprint,
    lanpaint_route_contract_template,
    normalize_lanpaint_route_contract,
)

AUDIT_SCHEMA_ID = "neo.image.lanpaint_route_family_phase1_audit.v1"
DATE = "2026-08-03"


def _sample_request() -> dict[str, Any]:
    return {
        "identity": {
            "provider_id": "ComfyUI Portable",
            "family": "Krea 2 Turbo",
            "loader": "GGUF",
            "mode": "inpainting",
            "engine": "Lan Paint",
            "variant": "sample_v1",
        },
        "assets": {
            "source_image": {"kind": "neo_asset_id", "ref": "source_asset"},
            "mask_image": {"kind": "neo_asset_id", "ref": "mask_asset"},
        },
        "crop_policy": {
            "padding_px": 152,
            "processing_size": {"width": 768, "height": 768},
            "resize_method": "lanczos",
        },
        "mask_policy": {
            "sampling": {"expand_px": 45, "blur_radius": 31},
            "stitch": {"expand_px": 50, "blur_radius": 9.1},
        },
        "sampler_policy": {
            "steps": 8,
            "cfg": 1,
            "sampler_name": "euler",
            "scheduler": "simple",
            "denoise": 1,
            "lanpaint_thinking_steps": 10,
            "prompt_mode": "Image First",
        },
    }


def _product_route_occurrences() -> dict[str, list[str]]:
    contract_files = {
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
    contract_only: list[str] = []
    production: list[str] = []
    files = list((ROOT / "neo_app").rglob("*.py"))
    files += list((ROOT / "neo_app").rglob("*.json"))
    files += list((ROOT / "neo_extensions" / "built_in").rglob("extension_manifest.json"))
    for path in files:
        try:
            if ROUTE_FAMILY_ID not in path.read_text(encoding="utf-8"):
                continue
        except UnicodeDecodeError:
            continue
        relative = path.relative_to(ROOT).as_posix()
        if relative in contract_files:
            contract_only.append(relative)
        else:
            production.append(relative)
    return {"contract_only": sorted(contract_only), "production": sorted(production)}


def build_report() -> dict[str, Any]:
    template = lanpaint_route_contract_template(
        provider_id="comfyui_portable",
        family="krea2_turbo",
        loader="gguf",
    )
    sample, sample_issues = normalize_lanpaint_route_contract(_sample_request())
    qwen, qwen_issues = normalize_lanpaint_route_contract(
        {
            "identity": {
                "provider_id": "comfyui",
                "family": "qwen_image",
                "loader": "gguf",
                "mode": "inpaint",
                "engine": "lanpaint",
                "variant": "future_policy",
            }
        }
    )
    invalid, invalid_issues = normalize_lanpaint_route_contract(
        {
            "identity": {
                "provider_id": "comfyui_portable",
                "family": "krea2_turbo",
                "loader": "gguf",
                "mode": "outpaint",
                "engine": "native",
            },
            "assets": {
                "source_image": {"kind": "portable_name", "ref": "C:" + "/private/source.png"},
            },
        }
    )
    occurrences = _product_route_occurrences()
    schema_path = ROOT / "neo_app" / "image" / "lanpaint_route_contract.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    checks = [
        {
            "id": "canonical_schema_identity",
            "passed": template["schema_id"] == SCHEMA_ID and template["schema_version"] == SCHEMA_VERSION,
            "detail": "The Phase 1 contract publishes the locked schema id and version.",
        },
        {
            "id": "canonical_route_dimensions",
            "passed": template["identity"]["route_key"] == "comfyui_portable:krea2_turbo:gguf:inpaint:lanpaint:default",
            "detail": "Route keys are deterministic across provider, family, loader, mode, engine, and variant.",
        },
        {
            "id": "execution_remains_disabled",
            "passed": template["execution"]["state"] == EXECUTION_STATE and template["execution"]["enabled"] is False and template["execution"]["selectable"] is False,
            "detail": "Phase 1 does not expose or compile a LanPaint route.",
        },
        {
            "id": "base_stage_order_locked",
            "passed": tuple(template["stage_order"]) == BASE_STAGE_ORDER and all(role in template["capability_requirements"]["required_stage_roles"] for role in BASE_REQUIRED_STAGE_ROLES),
            "detail": "The reusable logical pipeline owns stable stage order and role identities.",
        },
        {
            "id": "sample_values_normalize_without_becoming_global_defaults",
            "passed": (
                sample["validation"]["ok"]
                and sample["crop_policy"]["padding_px"] == 152
                and sample["crop_policy"]["processing_size"] == {"width": 768, "height": 768, "multiple_of": 8}
                and sample["sampler_policy"]["lanpaint_thinking_steps"] == 10
                and template["crop_policy"]["padding_px"] is None
                and template["sampler_policy"]["steps"] is None
            ),
            "detail": "Submitted Krea 2 values serialize when explicit but are not promoted to universal LanPaint defaults.",
        },
        {
            "id": "family_policy_boundary_preserved",
            "passed": (
                template["family_policy"]["resolution_state"] == "unresolved"
                and template["lora_policy"]["support_state"] == "family_policy"
                and template["lora_policy"]["injection_strategy"] == "family_policy"
                and bool(template["validation"]["unresolved_family_policy_fields"])
            ),
            "detail": "Family-owned loader, conditioning, sampler, LoRA, node, and model decisions remain unresolved in Phase 1.",
        },
        {
            "id": "future_family_contract_is_generic_not_enabled",
            "passed": qwen["validation"]["ok"] and not qwen_issues and qwen["identity"]["family"] == "qwen_image" and qwen["execution"]["enabled"] is False,
            "detail": "The contract can represent a future Qwen overlay without capturing its current production route.",
        },
        {
            "id": "invalid_mode_engine_and_private_path_rejected",
            "passed": (
                invalid["validation"]["ok"] is False
                and len([item for item in invalid_issues if item.get("level") == "error"]) >= 3
                and invalid["assets"]["source_image"] is None
                and invalid["identity"]["mode"] == MODE_ID
                and invalid["identity"]["engine"] == ENGINE_ID
            ),
            "detail": "The normalizer fails closed on non-inpaint engines and absolute asset paths.",
        },
        {
            "id": "fingerprint_is_deterministic",
            "passed": sample["contract_fingerprint"] == lanpaint_contract_fingerprint(sample),
            "detail": "Canonical serialization produces a stable SHA-256 contract fingerprint.",
        },
        {
            "id": "json_schema_matches_contract_identity",
            "passed": schema.get("$id") == SCHEMA_ID and schema.get("properties", {}).get("execution", {}).get("properties", {}).get("enabled", {}).get("const") is False,
            "detail": "The public JSON schema mirrors the contract-only execution lock.",
        },
        {
            "id": "production_activation_uses_the_canonical_contract",
            "passed": set(occurrences["production"]).issubset({"neo_app/image/lanpaint_family_adapter.py", "neo_app/models/route_matrix.py", "neo_app/providers/compile_router.py"}),
            "detail": "Later execution activation remains confined to the adapter registry, route matrix, and compile router; UI/state modules consume metadata without compiling a graph.",
        },
    ]

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "contract_authority": {
            "module": "neo_app.image.lanpaint_route_contract",
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "route_family_id": ROUTE_FAMILY_ID,
            "mode": MODE_ID,
            "engine": ENGINE_ID,
            "execution_state": EXECUTION_STATE,
        },
        "route_key_example": build_lanpaint_route_key(
            provider_id="comfyui_portable",
            family="krea2_turbo",
            loader="gguf",
            mode="inpaint",
            engine="lanpaint",
            variant="default",
        ),
        "template_contract": template,
        "submitted_sample_contract": sample,
        "submitted_sample_issues": sample_issues,
        "future_qwen_contract": qwen,
        "invalid_contract": invalid,
        "invalid_contract_issues": invalid_issues,
        "route_family_occurrences": occurrences,
        "checks": checks,
        "summary": {
            "passed": sum(1 for item in checks if item["passed"]),
            "failed": len(failed),
            "total": len(checks),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the canonical Phase 1 LanPaint route-family contract without enabling execution.")
    parser.add_argument("--json-out", type=Path, help="Optional path for the machine-readable report.")
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
