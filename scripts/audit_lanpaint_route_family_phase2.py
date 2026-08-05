from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from neo_app.image.lanpaint_route_contract import BASE_STAGE_ORDER, ROUTE_FAMILY_ID
from neo_app.image.lanpaint_workflow_abstraction import (
    ABSTRACTION_STATE,
    AUTHORITY,
    EXTERNAL_PORTS,
    SCHEMA_ID,
    SCHEMA_VERSION,
    lanpaint_workflow_abstraction_fingerprint,
    lanpaint_workflow_abstraction_template,
    stage_role_index,
    topological_stage_order,
    validate_lanpaint_workflow_abstraction,
)
from scripts.audit_lanpaint_route_family_phase0 import _sample_inventory

AUDIT_SCHEMA_ID = "neo.image.lanpaint_route_family_phase2_audit.v1"
DATE = "2026-08-03"

CONTRACT_ONLY_FILES = {
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

FORBIDDEN_CONCRETE_TOKENS = (
    "UNETLoader",
    "UnetLoaderGGUF",
    "CLIPLoader",
    "VAELoader",
    "LanPaint_KSampler",
    "DifferentialDiffusionAdvanced",
    "Krea 2",
    "krea2_turbo",
    "qwen_image",
    "z_image",
)


def _route_family_occurrences() -> dict[str, list[str]]:
    contract_only: list[str] = []
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
        if relative in CONTRACT_ONLY_FILES:
            contract_only.append(relative)
        else:
            production.append(relative)
    return {"contract_only": sorted(contract_only), "production": sorted(production)}


def _edge_keys(abstraction: dict[str, Any]) -> set[tuple[str, str, str, str]]:
    return {
        (
            str((edge.get("source") or {}).get("stage_id") or ""),
            str((edge.get("source") or {}).get("port_id") or ""),
            str((edge.get("target") or {}).get("stage_id") or ""),
            str((edge.get("target") or {}).get("port_id") or ""),
        )
        for edge in abstraction.get("edges", [])
        if isinstance(edge, dict)
    }


def build_report(workflow_path: Path | None = None) -> dict[str, Any]:
    abstraction = lanpaint_workflow_abstraction_template()
    issues = validate_lanpaint_workflow_abstraction(abstraction)
    roles = stage_role_index(abstraction)
    topo = topological_stage_order(abstraction)
    edges = _edge_keys(abstraction)
    serialized = json.dumps(abstraction, sort_keys=True)
    schema_path = ROOT / "neo_app" / "image" / "lanpaint_workflow_abstraction.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    occurrences = _route_family_occurrences()
    sample = _sample_inventory(workflow_path)

    external_ids = {str(item["port_id"]) for item in EXTERNAL_PORTS}
    binding_values = [
        value
        for stage in abstraction["stages"]
        for key, value in (stage.get("binding") or {}).items()
        if key != "state"
    ]

    checks = [
        {
            "id": "abstraction_schema_identity",
            "passed": abstraction["schema_id"] == SCHEMA_ID and abstraction["schema_version"] == SCHEMA_VERSION and abstraction["authority"] == AUTHORITY,
            "detail": "The base graph publishes the locked Phase 2 schema identity and authority.",
        },
        {
            "id": "route_family_and_stage_order_match_phase1",
            "passed": abstraction["route_family_id"] == ROUTE_FAMILY_ID and tuple(abstraction["stage_order"]) == BASE_STAGE_ORDER,
            "detail": "The workflow abstraction reuses the Phase 1 route-family and stage-order contract.",
        },
        {
            "id": "canonical_dag_is_complete_and_acyclic",
            "passed": not issues and topo == list(BASE_STAGE_ORDER) and abstraction["validation"]["ok"],
            "detail": "The logical graph is valid, acyclic, and topologically stable.",
        },
        {
            "id": "logical_roles_are_unique_and_unbound",
            "passed": set(roles) == set(BASE_STAGE_ORDER) and all((stage.get("binding") or {}).get("state") == "unbound" for stage in abstraction["stages"]),
            "detail": "Every base stage exposes one logical role and no concrete binding.",
        },
        {
            "id": "family_external_interface_is_explicit",
            "passed": external_ids == {"family_model", "positive_conditioning", "negative_conditioning", "vae", "sampler_settings"},
            "detail": "Model, conditioning, VAE, and sampler policy enter through explicit family/route ports.",
        },
        {
            "id": "pre_sampler_transform_is_bypassable",
            "passed": (
                roles["family_model_transform"]["required"] is False
                and roles["family_model_transform"]["bypass"] == {
                    "allowed": True,
                    "input_port": "model",
                    "output_port": "sample_model",
                    "preserves_type": True,
                }
                and abstraction["insertion_points"]["pre_sampler_model_transform"]["supports_lora_stack"] is True
            ),
            "detail": "LoRA and optional family transforms have one type-preserving pre-sampler insertion point.",
        },
        {
            "id": "source_space_crop_restore_stitch_flow_is_locked",
            "passed": all(
                edge in edges
                for edge in {
                    ("source_image", "image", "crop_context", "image"),
                    ("mask_image", "mask", "crop_context", "mask"),
                    ("crop_context", "crop_geometry", "restore_crop_size", "crop_geometry"),
                    ("source_image", "image", "stitch_composite", "destination"),
                    ("stitch_composite", "image", "output_handoff", "image"),
                }
            ),
            "detail": "The base route preserves original source geometry and composites the restored patch back into it.",
        },
        {
            "id": "latent_and_mask_flow_is_locked",
            "passed": all(
                edge in edges
                for edge in {
                    ("processing_resize", "process_image", "latent_encode", "pixels"),
                    ("sampling_mask_refine", "sampling_mask", "latent_noise_mask", "mask"),
                    ("latent_noise_mask", "masked_latent", "lanpaint_sample", "latent"),
                    ("lanpaint_sample", "sampled_latent", "latent_decode", "latent"),
                }
            ),
            "detail": "Image, mask, latent, sampling, and decode roles are connected without family-specific node assumptions.",
        },
        {
            "id": "no_concrete_family_or_node_bindings",
            "passed": all(value in (None, "") for value in binding_values) and not any(token in serialized for token in FORBIDDEN_CONCRETE_TOKENS),
            "detail": "The base abstraction contains no provider, family, loader, compiler, or concrete Comfy node bindings.",
        },
        {
            "id": "execution_remains_disabled",
            "passed": abstraction["abstraction_state"] == ABSTRACTION_STATE and abstraction["execution"]["enabled"] is False and abstraction["execution"]["selectable"] is False,
            "detail": "Phase 2 does not activate a compiler, route matrix entry, or UI route.",
        },
        {
            "id": "public_schema_and_fingerprint_match",
            "passed": schema.get("$id") == SCHEMA_ID and abstraction["abstraction_fingerprint"] == lanpaint_workflow_abstraction_fingerprint(abstraction),
            "detail": "The public schema and deterministic abstraction fingerprint match the Python authority.",
        },
        {
            "id": "later_production_activation_is_scoped",
            "passed": set(occurrences["production"]).issubset({"neo_app/image/lanpaint_family_adapter.py", "neo_app/models/route_matrix.py", "neo_app/providers/compile_router.py"}),
            "detail": "The Phase 2 abstraction remains provider-neutral; later execution activation is scoped to the Phase 13 adapter binding registry plus the compile router; UI state remains non-executing.",
        },
    ]

    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "abstraction_authority": {
            "module": AUTHORITY,
            "schema_id": SCHEMA_ID,
            "schema_version": SCHEMA_VERSION,
            "route_family_id": ROUTE_FAMILY_ID,
            "state": ABSTRACTION_STATE,
        },
        "workflow_abstraction": abstraction,
        "validation_issues": issues,
        "sample_workflow_inventory": sample,
        "route_family_occurrences": occurrences,
        "checks": checks,
        "summary": {
            "passed": sum(1 for item in checks if item["passed"]),
            "failed": len(failed),
            "total": len(checks),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit the family-neutral Phase 2 LanPaint workflow abstraction without enabling execution.")
    parser.add_argument("--workflow", type=Path, help="Optional submitted workflow JSON to inventory beside the abstraction.")
    parser.add_argument("--json-out", type=Path, help="Optional path for the machine-readable audit report.")
    args = parser.parse_args()

    report = build_report(args.workflow)
    output = json.dumps(report, indent=2, ensure_ascii=False)
    print(output)
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(output + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
