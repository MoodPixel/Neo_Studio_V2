from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXT_ROOT = ROOT / "neo_extensions/built_in/image.lora_stack"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(EXT_ROOT) not in sys.path:
    sys.path.insert(0, str(EXT_ROOT))

from backend.patch_profile import PATCH_PROFILE_SCHEMA_VERSION, build_lora_patch_profile, normalize_lora_patch_profile
from backend.support_matrix import route_support
from tests.test_lanpaint_route_family_phase5_krea2_turbo_gguf import _job as phase5_job, _manifest
from tests.test_lanpaint_route_family_phase6_lora_stack import OfflinePhase6Provider, _extensions, _lora_nodes

SCHEMA_ID = "neo.validation.lora_stack_phase12_global_engine_decoupling.v1"
DATE = "2026-08-05"


def _check(check_id: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def _row() -> dict[str, Any]:
    return {"uid": "phase12-audit", "enabled": True, "name": "detail.safetensors", "strength": 0.7, "target": "both", "apply_to": "global"}


def _compile(engine: str | None, enabled: bool = True):
    extensions = _extensions([_row()], enabled=enabled)
    return OfflinePhase6Provider(_manifest()).compile_job(phase5_job(engine=engine, extensions=extensions))


def build_report() -> dict[str, Any]:
    manifest = json.loads((EXT_ROOT / "extension_manifest.json").read_text(encoding="utf-8"))
    native = route_support("comfyui", "krea2_turbo", "gguf", "inpaint", "native")
    lanpaint = route_support("comfyui", "krea2_turbo", "gguf", "inpaint", "lanpaint")
    qwen_native = route_support("comfyui", "qwen_image", "gguf", "inpaint", "native")
    qwen_lanpaint = route_support("comfyui", "qwen_image", "gguf", "inpaint", "lanpaint")
    z_native = route_support("comfyui", "z_image", "diffusion_model", "inpaint", "native")
    z_lanpaint = route_support("comfyui", "z_image", "diffusion_model", "inpaint", "lanpaint")

    native_compiled = _compile(None)
    lanpaint_compiled = _compile("lanpaint")
    native_patch = next(item for item in native_compiled.backend_payload["actual_params"]["extension_workflow_patches"] if item["extension_id"] == "lora_stack")
    lanpaint_patch = next(item for item in lanpaint_compiled.backend_payload["actual_params"]["extension_workflow_patches"] if item["extension_id"] == "lora_stack")
    native_off = _compile(None, enabled=False)
    lanpaint_off = _compile("lanpaint", enabled=False)

    legacy = normalize_lora_patch_profile({
        "schema_version": "neo.image.lora_stack.patch_profile.v1",
        "source": "phase12-audit-legacy",
        "route": {"backend": "comfyui", "family": "krea2_turbo", "loader": "gguf", "workflow_mode": "inpaint", "engine": "lanpaint", "route_key": "krea2_turbo:gguf:inpaint:lanpaint"},
        "strategy": "lora_loader_model_only_consumer_rewire",
        "loader_node_class": "LoraLoaderModelOnly",
        "requires_model": True,
        "requires_clip": False,
        "model_ref": ["10", 0],
        "clip_ref": ["11", 0],
        "sampler_node_id": "15",
        "sampler_model_input": "model",
    }, route={"backend": "comfyui", "family": "krea2_turbo", "loader": "gguf", "workflow_mode": "inpaint", "engine": "lanpaint"})

    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    public_sources = [
        EXT_ROOT / "backend/support_matrix.py",
        EXT_ROOT / "backend/patch_profile.py",
        EXT_ROOT / "backend/workflow_patch.py",
        ROOT / "neo_app/providers/comfy_provider.py",
        ROOT / "neo_app/static/js/neo.js",
    ]
    path_pattern = re.compile(r"(?:/(?:home|Users|mnt)/|[A-Za-z]:\\(?:Users|Documents and Settings)\\)")
    path_hits = [path.relative_to(ROOT).as_posix() for path in public_sources if path_pattern.search(path.read_text(encoding="utf-8"))]

    checks = [
        _check("compatibility_key_excludes_engine", native["route_key"] == lanpaint["route_key"] == "krea2_turbo:gguf:inpaint", "Native Inpaint and LanPaint share one Krea2 compatibility key."),
        _check("workflow_route_retains_engine", native["workflow_route_key"] == "krea2_turbo:gguf:inpaint" and lanpaint["workflow_route_key"] == "krea2_turbo:gguf:inpaint:lanpaint", "Graph route lineage still identifies the active inpaint engine."),
        _check("krea_model_only_parity", native["state"] == lanpaint["state"] == "experimental_available" and native["requires_clip"] is False and lanpaint["loader_node_class"] == "LoraLoaderModelOnly", "Krea2 keeps one model-only policy across both engines."),
        _check("qwen_model_clip_parity", qwen_native["route_key"] == qwen_lanpaint["route_key"] and qwen_native["requires_clip"] is True and qwen_native["state"] == qwen_lanpaint["state"], "Qwen inpaint compatibility no longer depends on engine selection."),
        _check("zimage_model_clip_parity", z_native["route_key"] == z_lanpaint["route_key"] and z_native["requires_clip"] is True and z_native["state"] == z_lanpaint["state"], "Z-Image inpaint compatibility no longer depends on engine selection."),
        _check("manifest_has_no_engine_keys", not any(":lanpaint" in key or ":native" in key for key in manifest.get("route_states", {})), "Generated manifest route states contain no engine-specific LoRA keys."),
        _check("manifest_declares_engine_independence", manifest.get("compatibility_dimensions", {}).get("engine") is False and bool(manifest.get("route_policies")), "Manifest publishes engine-independent compatibility dimensions and route policies."),
        _check("patch_profile_v2_separates_keys", native_patch["patch_profile"]["schema_version"] == PATCH_PROFILE_SCHEMA_VERSION and native_patch["patch_profile"]["route_key"] == lanpaint_patch["patch_profile"]["route_key"] and native_patch["patch_profile"]["workflow_route_key"] != lanpaint_patch["patch_profile"]["workflow_route_key"], "Patch profiles share compatibility but preserve compiler-specific workflow lineage."),
        _check("native_and_lanpaint_anchor_separation", native_patch["patched_model_consumer_nodes"] != lanpaint_patch["patched_model_consumer_nodes"], "Native and LanPaint compilers supply different model-consumer anchors."),
        _check("optional_switch_off_is_graph_neutral", not _lora_nodes(native_off.backend_payload.get("prompt") or {}) and not _lora_nodes(lanpaint_off.backend_payload.get("prompt") or {}), "LoRA-off workflows run on either engine without LoRA nodes."),
        _check("legacy_profile_migrates_safely", legacy.get("valid") is True and legacy.get("profile", {}).get("legacy_schema_migrated") is True and legacy.get("profile", {}).get("route_key") == "krea2_turbo:gguf:inpaint", "Legacy engine-specific profiles normalize to v2 without losing graph-engine metadata."),
        _check("frontend_policy_is_engine_independent", "manifest.route_policies" in js and "lora_mode: route.engine ===" not in js[js.index("function loraStackActiveRoute"):js.index("function loraStackRouteVisible")], "Frontend derives LoRA mode from family/loader policy rather than inpaint engine."),
        _check("explicit_execution_intent_preserved", "Apply LoRA Stack (optional)" in js and "execution_requested: loraStackExecutionRequested()" in js and "Plain LanPaint runs without any LoRA." in js, "Saved rows remain independent from explicit LoRA execution."),
        _check("cache_revision_advanced", "phase12=global_lora_engine_decoupling_20260805" in index and any(marker in js for marker in ("global_lora_engine_decoupling_20260805", "lanpaint_family_adapter_v2_20260805", "lanpaint_route_parity_phase14_20260805", "lanpaint_sd_family_phase15_20260805", "lanpaint_flux1_family_phase16_20260805", "lanpaint_flux2_family_phase17_20260805")), "Browser assets are advanced to the Phase 12 revision."),
        _check("no_personal_paths", not path_hits, "Phase 12 source files contain no personal absolute paths."),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": 1,
        "date": DATE,
        "status": "passed" if not failed else "failed",
        "compatibility": {"native": native, "lanpaint": lanpaint, "qwen_native": qwen_native, "qwen_lanpaint": qwen_lanpaint},
        "graph_lineage": {"native_patch": native_patch, "lanpaint_patch": lanpaint_patch},
        "legacy_migration": legacy,
        "path_hits": path_hits,
        "physical_validation": {
            "status": "not_run",
            "reason": "The packaging environment does not host the user's live ComfyUI model and LoRA catalogs.",
            "required_next": "Run Native Inpaint and LanPaint with LoRA off/on for each activated family and loader before release promotion.",
        },
        "checks": checks,
        "summary": {"passed": len(checks) - len(failed), "failed": len(failed), "total": len(checks), "failed_ids": [item["id"] for item in failed]},
    }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Audit Phase 12 global LoRA engine decoupling.")
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
