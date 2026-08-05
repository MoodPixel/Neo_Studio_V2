from __future__ import annotations

import argparse
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from neo_app.image.lanpaint_capabilities import (
    BLOCKED_MODEL_STATUS,
    BLOCKED_NODE_STATUS,
    PHASE8_STATE,
    READY_STATUS,
    SCHEMA_ID,
    evaluate_lanpaint_route_capabilities,
)
from scripts.audit_lanpaint_route_family_phase7 import build_report as build_phase7_report
from tests.test_lanpaint_route_family_phase5_krea2_turbo_gguf import _capabilities, _compile, _job

ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCHEMA_ID = "neo.image.lanpaint_route_family_phase8_audit.v1"


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def _report(capabilities: dict[str, Any], *, selected: dict[str, str] | None = None, lora: bool = False) -> dict[str, Any]:
    return evaluate_lanpaint_route_capabilities(
        capabilities,
        provider_id="comfyui_portable",
        family="krea2_turbo",
        loader="gguf",
        mode="inpaint",
        engine="lanpaint",
        selected_assets=selected or {
            "model": "krea2_turbo-Q5_K_M.gguf",
            "text_encoder": "qwen3vl_4b_fp8_scaled.safetensors",
            "vae": "qwen_image_vae.safetensors",
        },
        require_model_only_lora=lora,
    )


def build_report() -> dict[str, Any]:
    phase7 = build_phase7_report()
    ready = _report(_capabilities())
    missing_nodes_caps = _capabilities(remove="LanPaint_KSampler")
    missing_nodes_caps["object_info_node_inputs"].pop("CropByMask", None)
    missing_nodes = _report(missing_nodes_caps)
    missing_models_caps = deepcopy(_capabilities())
    for role_id in ("gguf_unet", "krea2_clip_loader", "qwen_image_vae"):
        missing_models_caps["loaders"]["gguf"]["roles"][role_id]["assets"] = {}
    missing_models = _report(missing_models_caps, selected={})
    base_without_lora = _report(_capabilities(), lora=False)
    lora_required = _report(_capabilities(), lora=True)
    compiled = _compile(_job())
    blocked_compiled = _compile(_job(), _capabilities(remove="LanPaint_KSampler"))
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    css = (ROOT / "neo_app/static/css/neo.css").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    schema = json.loads((ROOT / "neo_app/image/lanpaint_capabilities.schema.json").read_text(encoding="utf-8"))

    checks = [
        _check("phase7_regression_remains_green", phase7.get("ok") is True, "All Phase 7 architecture locks remain green."),
        _check("capability_schema_and_authority_are_locked", ready.get("schema_id") == SCHEMA_ID and ready.get("phase_state") == PHASE8_STATE and schema.get("$id") == SCHEMA_ID, "Capability status and diagnostics have one versioned backend-neutral authority."),
        _check("ready_route_remains_experimental_until_physical_validation", ready.get("status") == READY_STATUS and ready.get("selectable") is True and ready.get("executable") is True, "A complete live route is selectable but is not promoted beyond experimental without physical evidence."),
        _check("missing_nodes_are_fail_closed_and_pack_grouped", missing_nodes.get("status") == BLOCKED_NODE_STATUS and not missing_nodes.get("selectable") and {item.get("pack_id") for item in missing_nodes.get("checks", {}).get("nodes", {}).get("missing_by_pack", [])} >= {"LanPaint", "ComfyUI-InpaintEasy"}, "Missing custom nodes are grouped by installable pack and block selection before queueing."),
        _check("missing_models_have_distinct_status", missing_models.get("status") == BLOCKED_MODEL_STATUS and missing_models.get("checks", {}).get("nodes", {}).get("ok") is True, "Model/encoder/VAE catalog failures are distinct from node-pack failures."),
        _check("krea_clip_loader_option_is_required", ready.get("checks", {}).get("loaders", {}).get("items", {}).get("krea2_clip_loader", {}).get("available") is True, "CLIPLoader existence alone is insufficient; type=krea2 is part of readiness."),
        _check("base_route_does_not_require_lora_loader", base_without_lora.get("status") == READY_STATUS and base_without_lora.get("lora", {}).get("supported") is False, "Missing model-only LoRA support does not block a no-LoRA LanPaint graph."),
        _check("active_base_lora_requires_model_only_loader", lora_required.get("status") == BLOCKED_NODE_STATUS and any("LoraLoaderModelOnly" in item.get("message", "") for item in lora_required.get("blockers", [])), "The same missing loader becomes a blocker only when an active base/global LoRA requires it."),
        _check("compiler_records_authoritative_capability_lineage", compiled.compile_status == "compiled" and compiled.backend_payload.get("lanpaint_route_capabilities", {}).get("status") == READY_STATUS and compiled.backend_payload.get("actual_params", {}).get("_neo_lanpaint_phase8_capability_state") == PHASE8_STATE, "Compiled jobs retain the exact capability report and fingerprint used at execution."),
        _check("blocked_compiler_emits_no_prompt", blocked_compiled.compile_status == "mock_compiled" and blocked_compiled.backend_payload.get("prompt") == {} and blocked_compiled.backend_payload.get("lanpaint_route_capabilities", {}).get("status") == BLOCKED_NODE_STATUS, "Capability blockers stop graph emission rather than falling back to another engine."),
        _check("frontend_uses_live_capability_snapshot", all(item in js for item in ["function imageLanpaintCapabilitySnapshot", "function imageLanpaintCapabilityEvaluation", "const engineVisible = route.contract_eligible;", "disabled: !route.selectable"]), "The UI shows the engine contract but disables LanPaint until the selected profile is actually ready."),
        _check("frontend_diagnostics_are_actionable", 'data-testid="image-lanpaint-capability-diagnostics"' in js and "How to fix" in js and ".neo-lanpaint-diagnostic-list" in css, "Blocked routes show concrete issues, missing packs, and remediation steps."),
        _check("static_assets_are_cache_advanced", "phase7=lanpaint_ui_state_20260804" in index and "phase8=lanpaint_capabilities_20260804" in index and any(marker in js for marker in ["window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_capabilities_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_qwen_zimage_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_replay_lineage_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_capability_transport_hotfix_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_lora_independence_hotfix_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'global_lora_engine_decoupling_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_family_adapter_v2_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_route_parity_phase14_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_sd_family_phase15_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_flux1_family_phase16_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_flux2_family_phase17_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_qwen_edit_variants_phase18_20260805';"]), "Phase 7 cache identity is preserved while Phase 8 advances the active asset revision."),
        _check("no_personal_paths_in_phase8_sources", all(token not in "".join((ROOT / path).read_text(encoding="utf-8") for path in ["neo_app/image/lanpaint_capabilities.py", "neo_app/providers/comfy_workflows/lanpaint.py", "neo_app/providers/comfy_provider.py", "neo_app/static/js/neo.js", "neo_app/static/css/neo.css"]) for token in ["/" + "home" + "/", "/" + "Users" + "/", "/" + "mnt" + "/"]), "Phase 8 source files contain no personal absolute paths."),
    ]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "phase": "Phase 8 — Capability detection, gating, and diagnostics",
        "ok": all(item["passed"] for item in checks),
        "checks": checks,
        "summary": {"passed": sum(1 for item in checks if item["passed"]), "total": len(checks)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit LanPaint Phase 8 capability gating and diagnostics.")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = build_report()
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
