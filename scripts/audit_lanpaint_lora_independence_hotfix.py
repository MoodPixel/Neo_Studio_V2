from __future__ import annotations

import argparse
import json
from pathlib import Path

from neo_app.extensions.workflow_hooks import lora_stack_execution_requested
from tests.test_lanpaint_lora_independence_hotfix import (
    MissingLoraNodeProvider,
    _job_with_submit_state,
)
from tests.test_lanpaint_route_family_phase5_krea2_turbo_gguf import _manifest
from tests.test_lanpaint_route_family_phase6_lora_stack import _lora_nodes

ROOT = Path(__file__).resolve().parents[1]


def _check(check_id: str, passed: bool, detail: str) -> dict[str, object]:
    return {"id": check_id, "passed": bool(passed), "detail": detail}


def build_report() -> dict[str, object]:
    js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    index = (ROOT / "neo_app/static/index.html").read_text(encoding="utf-8")
    provider_source = (ROOT / "neo_app/providers/comfy_provider.py").read_text(encoding="utf-8")
    hooks_source = (ROOT / "neo_app/extensions/workflow_hooks.py").read_text(encoding="utf-8")
    compiler_source = (ROOT / "neo_app/providers/comfy_workflows/lanpaint.py").read_text(encoding="utf-8")
    family_source = (ROOT / "neo_app/providers/comfy_workflows/lanpaint_family.py").read_text(encoding="utf-8")

    disabled_job = _job_with_submit_state(enabled=False, execution_requested=False)
    disabled_compiled = MissingLoraNodeProvider(_manifest()).compile_job(disabled_job)
    enabled_job = _job_with_submit_state(enabled=True, execution_requested=True)
    enabled_compiled = MissingLoraNodeProvider(_manifest()).compile_job(enabled_job)

    disabled_route = {"actual_params": {"_neo_extension_state": disabled_job.params["_neo_extension_state"]}}
    enabled_route = {"actual_params": {"_neo_extension_state": enabled_job.params["_neo_extension_state"]}}
    legacy_ui_state = dict(enabled_job.params["_neo_extension_state"])
    legacy_ui_state["extensions"] = dict(legacy_ui_state.get("extensions") or {})
    legacy_ui_state["extensions"]["lora_stack"] = dict(legacy_ui_state["extensions"].get("lora_stack") or {})
    legacy_ui_state["extensions"]["lora_stack"].pop("execution_requested", None)
    legacy_ui_route = {"actual_params": {"_neo_extension_state": legacy_ui_state}}

    checks = [
        _check(
            "plain_lanpaint_has_no_lora_requirement",
            disabled_compiled.compile_status == "compiled" and not _lora_nodes(disabled_compiled.backend_payload.get("prompt") or {}),
            "Saved LoRA rows with the current master switch off do not change or block the LanPaint graph.",
        ),
        _check(
            "plain_lanpaint_ignores_missing_lora_loader",
            not any("LoraLoader" in str(item) for item in (disabled_compiled.backend_payload.get("validation") or {}).get("errors", [])),
            "A missing LoRA loader is irrelevant when LoRA execution was not requested.",
        ),
        _check(
            "explicit_lora_still_fails_closed",
            enabled_compiled.compile_status == "mock_compiled" and any("LoraLoaderModelOnly" in str(item) for item in (enabled_compiled.backend_payload.get("validation") or {}).get("errors", [])),
            "An explicitly enabled Krea model-only LoRA still requires its compatible loader and fails before queueing when unavailable.",
        ),
        _check(
            "submit_state_overrides_stale_payload",
            not lora_stack_execution_requested(disabled_job.extensions, disabled_route) and lora_stack_execution_requested(enabled_job.extensions, enabled_route),
            "The current Image submit-state snapshot is authoritative over stale or replay-preserved LoRA payloads.",
        ),
        _check(
            "legacy_api_explicit_payload_supported",
            lora_stack_execution_requested(enabled_job.extensions),
            "API callers without a UI snapshot retain explicit enabled-row behavior.",
        ),
        _check(
            "legacy_ui_snapshot_is_configuration_only",
            not lora_stack_execution_requested(enabled_job.extensions, legacy_ui_route),
            "A UI/replay snapshot without explicit v2 execution intent cannot activate LoRA.",
        ),
        _check(
            "compiler_filters_rows_before_capability_gate",
            "def _active_lora_rows(extensions: Any, params: Mapping[str, Any] | None = None)" in compiler_source
            and "execution_requested" in compiler_source
            and "_active_lora_rows(job.extensions, params)" in compiler_source
            and "_active_lora_rows(job.extensions, params)" in family_source,
            "Krea and Qwen/Z compilers exclude disabled LoRA intent before node/model capability requirements are calculated.",
        ),
        _check(
            "provider_fail_closed_is_optional_extension_only",
            "requires_patch = lora_stack_execution_requested(patch_extensions, route_payload)" in provider_source
            and "LanPaint itself never depends on LoRA" in provider_source,
            "Provider fail-closed handling runs only for a deliberate optional LoRA request, never for plain LanPaint.",
        ),
        _check(
            "shared_hook_is_request_aware",
            "if lora_stack_execution_requested(extensions, route):" in hooks_source
            and "or lora_stack_execution_requested(extensions)" in hooks_source,
            "The shared workflow hook does not mutate a graph merely because a LoRA payload block exists.",
        ),
        _check(
            "frontend_master_switch_is_versioned_and_explicit",
            "const LORA_STACK_EXECUTION_INTENT_VERSION = 2;" in js
            and "execution_enabled: false" in js
            and "execution_requested: loraStackExecutionRequested()" in js,
            "The frontend uses a versioned explicit LoRA execution switch; saved rows alone are not intent.",
        ),
        _check(
            "frontend_explains_independence",
            "Apply LoRA Stack (optional)" in js
            and "Plain LanPaint runs without any LoRA." in js
            and "lanpaint_and_lora_are_independent_optional_features" in js,
            "The UI states the optional/independent relationship and records it in the submit snapshot.",
        ),
        _check(
            "cache_revision_advanced",
            any(marker in js for marker in ("lanpaint_lora_independence_hotfix_20260804", "global_lora_engine_decoupling_20260805", "lanpaint_family_adapter_v2_20260805", "lanpaint_route_parity_phase14_20260805", "lanpaint_sd_family_phase15_20260805", "lanpaint_flux1_family_phase16_20260805", "lanpaint_flux2_family_phase17_20260805"))
            and "hotfix=lanpaint_lora_independence_20260804" in index,
            "HTML and JavaScript force clients off the previous cached behavior.",
        ),
        _check(
            "public_paths_only",
            all(token not in "\n".join((provider_source, hooks_source, compiler_source, family_source, js)) for token in ("/" + "home" + "/", "/" + "Users" + "/", "/" + "mnt" + "/" + "data", "C:" + "\\" + "Users" + "\\", "D:" + "\\" + "Users" + "\\")),
            "The hotfix contains no personal or machine-specific filesystem paths.",
        ),
    ]
    failed = [item["id"] for item in checks if not item["passed"]]
    return {
        "schema_id": "neo.validation.lanpaint_lora_independence_hotfix.v1",
        "title": "LanPaint and LoRA independence hotfix",
        "checks": checks,
        "summary": {"passed": len(checks) - len(failed), "failed": len(failed), "total": len(checks), "failed_ids": failed},
        "passed": not failed,
        "physical_validation": "not_run",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json-out", default="")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()
    report = build_report()
    text = json.dumps(report, indent=2 if args.pretty else None, sort_keys=True)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
