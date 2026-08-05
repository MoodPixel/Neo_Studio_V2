from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from neo_app.image.lanpaint_ui_state import PHASE7_STATE, SCHEMA_ID, normalize_lanpaint_ui_state
from scripts.audit_lanpaint_route_family_phase6 import build_report as build_phase6_report
from tests.test_lanpaint_route_family_phase5_krea2_turbo_gguf import _compile, _job

ROOT = Path(__file__).resolve().parents[1]
AUDIT_SCHEMA_ID = "neo.image.lanpaint_route_family_phase7_audit.v1"


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def build_report() -> dict[str, Any]:
    defaults = normalize_lanpaint_ui_state(
        {}, provider_id="comfyui_portable", family="krea2_turbo", loader="gguf", mode="inpaint", engine="lanpaint"
    )
    forge = normalize_lanpaint_ui_state(
        {"lanpaint_crop_padding": 224}, provider_id="forge", family="krea2_turbo", loader="gguf", mode="inpaint", engine="lanpaint"
    )
    job = _job()
    job.params["lanpaint_ui_state"] = {
        "controls": {
            "crop": {"padding_px": 208, "processing_width": 896, "processing_height": 704},
            "sampling_mask": {"expand_px": 33, "blur_radius": 12.5},
            "stitch_mask": {"expand_px": 44, "blur_radius": 7.5},
            "sampler": {"thinking_steps": 14, "prompt_mode": "prompt_first", "denoise": 0.82},
            "stitch": {"resize_method": "bicubic"},
        }
    }
    compiled = _compile(job)
    actual = compiled.backend_payload["actual_params"]
    prompt = compiled.backend_payload["prompt"]
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    css = (ROOT / "neo_app/static/css/neo.css").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    phase6 = build_phase6_report()

    checks = [
        _check("phase6_regression_remains_green", phase6.get("status") == "passed" or not phase6.get("summary", {}).get("failed"), "All Phase 6 architecture locks remain green."),
        _check("ui_state_schema_and_phase_are_locked", defaults.get("schema_id") == SCHEMA_ID and defaults.get("phase_state") == PHASE7_STATE, "The portable UI/replay state has a versioned authority."),
        _check("eligible_route_resolves_family_defaults", defaults["route"]["active"] and defaults["resolved_flat"]["lanpaint_crop_padding"] == 152 and defaults["resolved_flat"]["lanpaint_thinking_steps"] == 10, "Krea 2 Turbo GGUF defaults come from its family policy."),
        _check("route_badges_are_provider_family_loader_engine_aware", defaults["badges"] == {"provider": "ComfyUI Portable", "family": "Krea 2 Turbo", "loader": "GGUF", "engine": "LanPaint", "state": "Experimental", "lora_mode": "Model-only"}, "The state exposes the requested route badges without Krea-only UI ownership."),
        _check("unsupported_route_forces_native_without_destroying_controls", not forge["route"]["active"] and forge["route"]["engine"] == "native" and forge["resolved_flat"]["lanpaint_crop_padding"] == 224, "Forge cannot execute LanPaint, while saved controls remain portable."),
        _check("nested_state_drives_compiler", compiled.compile_status == "compiled" and prompt["4"]["inputs"]["padding"] == 208 and prompt["15"]["inputs"]["LanPaint_NumSteps"] == 14 and prompt["17"]["inputs"]["upscale_method"] == "bicubic", "The provider compiles normalized nested UI state rather than reading DOM-only values."),
        _check("replay_metadata_records_requested_and_resolved_values", actual.get("_neo_lanpaint_phase7_ui_state") == PHASE7_STATE and actual["lanpaint_ui_state"]["requested_flat"]["lanpaint_crop_padding"] == 208 and actual["lanpaint_ui_state_fingerprint"] == actual["lanpaint_ui_state"]["state_fingerprint"], "Requested and resolved values plus a fingerprint are retained."),
        _check("frontend_panel_is_route_aware", "function imageLanpaintRouteContext" in js and 'data-testid="image-lanpaint-panel"' in js and "params.lanpaint_ui_state = lanpaintState.nested" in js, "The visible panel and payload share one route-aware state contract."),
        _check("frontend_controls_cover_crop_mask_thinking_and_stitch", all(item in js for item in ["imageLanpaintCropPadding", "imageLanpaintProcessingWidth", "imageLanpaintSamplingMaskExpand", "imageLanpaintThinkingSteps", "imageLanpaintPromptMode", "imageLanpaintStitchMaskBlur"]), "Phase 7 exposes only LanPaint-owned control groups."),
        _check("lora_summary_is_family_aware_and_deferred_safe", "LoRA mode:" in js and "Model-only" in js and "Model + CLIP" in js and "Regional and finish-only rows stay deferred" in js, "The shared UI reports model-only Krea and model+CLIP Qwen/Z modes without claiming deferred row execution."),
        _check("responsive_css_is_present", all(item in css for item in [".neo-lanpaint-panel", ".neo-lanpaint-grid", ".neo-lanpaint-control-group"]), "The control card has a dedicated responsive layout."),
        _check("static_asset_revision_is_advanced", "phase7=lanpaint_ui_state_20260804" in index and any(marker in js for marker in ["window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_ui_state_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_capabilities_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_qwen_zimage_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_replay_lineage_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_capability_transport_hotfix_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_lora_independence_hotfix_20260804';", "window.__NEO_STATIC_ASSET_REVISION__ = 'global_lora_engine_decoupling_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_family_adapter_v2_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_route_parity_phase14_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_sd_family_phase15_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_flux1_family_phase16_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_flux2_family_phase17_20260805';", "window.__NEO_STATIC_ASSET_REVISION__ = 'lanpaint_qwen_edit_variants_phase18_20260805';"]), "The Phase 7 cache identity remains present and later LanPaint phases may advance the runtime revision."),
        _check("no_personal_paths_in_phase7_sources", all(token not in (js + css + (ROOT / "neo_app/image/lanpaint_ui_state.py").read_text(encoding="utf-8")) for token in ["/" + "home" + "/", "/" + "Users" + "/", "/" + "mnt" + "/"]), "Phase 7 source files contain no personal absolute paths."),
    ]
    return {
        "schema_id": AUDIT_SCHEMA_ID,
        "phase": "Phase 7 — Inpaint UI and state-model integration",
        "ok": all(item["passed"] for item in checks),
        "checks": checks,
        "summary": {"passed": sum(1 for item in checks if item["passed"]), "total": len(checks)},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit LanPaint Phase 7 route-aware UI and state integration.")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = build_report()
    print(json.dumps(report, indent=2 if args.pretty else None, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
