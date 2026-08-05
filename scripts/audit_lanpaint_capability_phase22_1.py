from __future__ import annotations

import json
from pathlib import Path

from neo_app.image.lanpaint_capability_discovery import (
    PHASE_STATE,
    build_lanpaint_capability_snapshot_metadata,
    build_lanpaint_discovery_contract,
)
from neo_app.image.lanpaint_family_adapter import lanpaint_family_adapter_registry
from neo_app.providers.comfy_workflows.lanpaint import (
    LANPAINT_BASE_OBJECT_INFO_NODE_CLASSES,
    LANPAINT_OBJECT_INFO_NODE_CLASSES,
)

ROOT = Path(__file__).resolve().parents[1]


def _check(check_id: str, passed: bool, detail: str) -> dict:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def run_audit() -> dict:
    registry = lanpaint_family_adapter_registry("comfyui")
    contract = build_lanpaint_discovery_contract(
        registry,
        base_node_classes=LANPAINT_BASE_OBJECT_INFO_NODE_CLASSES,
    )
    metadata = build_lanpaint_capability_snapshot_metadata(
        contract,
        discovered_node_classes=contract["required_node_classes"],
        object_info_available=True,
    )
    scope = set(contract["required_node_classes"])
    selectable = [item for item in registry["adapters"] if item["binding"]["selectable"]]
    provider = (ROOT / "neo_app/providers/comfy_provider.py").read_text(encoding="utf-8")
    capabilities = (ROOT / "neo_app/image/lanpaint_capabilities.py").read_text(encoding="utf-8")
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")

    checks = [
        _check("phase_state", PHASE_STATE == "phase22_1_registry_driven_discovery_and_cache_repair", "Phase 22.1 owns discovery and cache repair."),
        _check("registry_has_active_routes", len(selectable) == len(contract["active_route_keys"]), "Every selectable adapter contributes one discovery route."),
        _check("all_active_groups_collected", all(set(group.get("aliases") or ()).issubset(scope) for adapter in selectable for group in adapter["capabilities"]["node_groups"]), "Every required adapter alias is in the object_info scope."),
        _check("all_conditional_groups_collected", all(set(group.get("aliases") or ()).issubset(scope) for adapter in selectable for group in adapter["capabilities"]["conditional_node_groups"].values()), "Conditional LoRA/invert aliases are also discoverable."),
        _check("checkpoint_loader_discovered", "CheckpointLoaderSimple" in scope, "SD checkpoint routes no longer depend on a missing static whitelist entry."),
        _check("dual_clip_discovered", {"DualCLIPLoader", "DualCLIPLoaderGGUF"}.issubset(scope), "Flux dual-encoder loaders are discoverable."),
        _check("triple_clip_discovered", {"TripleCLIPLoader", "TripleCLIPLoaderGGUF"}.issubset(scope), "SD 3.5 triple-encoder loaders are discoverable."),
        _check("quad_clip_discovered", {"QuadrupleCLIPLoader", "QuadrupleCLIPLoaderGGUF"}.issubset(scope), "HiDream quadruple-encoder loaders are discoverable."),
        _check("flux_nodes_discovered", {"FluxGuidance", "ModelSamplingFlux"}.issubset(scope), "Flux guidance and model sampling nodes are discoverable."),
        _check("sd3_sampling_discovered", "ModelSamplingSD3" in scope, "SD3/HiDream sampling transform is discoverable."),
        _check("qwen_edit_aliases_discovered", {"TextEncodeQwenImageEditPlus", "TextEncodeQwenImageEditPlus_lrzjason", "TextEncodeQwenImageEditPlusAdvance_lrzjason", "TextEncodeQwenImageEditPlusPro_lrzjason"}.issubset(scope), "Qwen Edit encoder aliases are discoverable."),
        _check("basic_and_advanced_lanpaint_discovered", {"LanPaint_KSampler", "LanPaint_SamplerCustomAdvanced"}.issubset(scope), "Basic and Ideogram custom-advanced LanPaint samplers are distinct."),
        _check("legacy_public_constant_is_generated_union", set(LANPAINT_OBJECT_INFO_NODE_CLASSES) == scope, "Legacy imports expose the registry-generated union."),
        _check("snapshot_binds_registry_fingerprint", metadata["adapter_registry_fingerprint"] == registry["registry_fingerprint"], "Snapshots are tied to the adapter registry revision."),
        _check("snapshot_binds_active_routes", set(metadata["active_route_keys"]) == set(registry["active_route_keys"]), "Snapshots publish the exact active route set."),
        _check("provider_uses_dynamic_scope", "build_lanpaint_discovery_contract" in provider and "*object_info_scope" in provider and 'payload["lanpaint_capability_snapshot"]' in provider, "Comfy discovery transports registry-derived object_info and snapshot metadata."),
        _check("backend_has_stale_state", "blocked_stale_capability_snapshot" in capabilities and "stale_capability_snapshot" in capabilities, "Backend diagnostics separate stale snapshots from missing nodes."),
        _check("frontend_has_stale_state", "blocked_stale_capability_snapshot" in js and "route_missing_from_capability_matrix" in js and "adapter_registry_fingerprint_mismatch" in js, "Frontend refuses stale route matrices with a refresh-specific state."),
        _check("cache_revision_advanced", "phase22_1=lanpaint_capability_discovery_cache_repair_20260805" in index, "Browser cache revision forces the repaired capability logic to load."),
    ]
    return {
        "schema_id": "neo.validation.lanpaint_capability_phase22_1.v1",
        "phase_state": PHASE_STATE,
        "status": "passed" if all(item["passed"] for item in checks) else "failed",
        "passed": sum(1 for item in checks if item["passed"]),
        "total": len(checks),
        "checks": checks,
        "physical_validation": "not_run",
    }


def main() -> int:
    report = run_audit()
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
