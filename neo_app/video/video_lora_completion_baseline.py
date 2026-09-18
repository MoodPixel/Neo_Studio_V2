from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

SCHEMA_VERSION = "neo.video.lora_stack.completion_baseline.v1"
PHASE = "phase_11"
ROOT = Path(__file__).resolve().parents[2]

EXPECTED_GATES: tuple[tuple[str, str, str, int], ...] = (
    ("phase_6_h3", "neo_app.video.minimax_h3_lora_regression", "run_minimax_h3_lora_regression", 43),
    ("phase_7_ltx", "neo_app.video.ltx_lora_regression", "run_regression", 17),
    ("phase_8_wan", "neo_app.video.wan_lora_regression", "run_phase8_gate", 30),
    ("phase_9_legacy", "neo_app.video.video_lora_legacy_compat_regression", "run_phase9_gate", 21),
    ("phase_10_ui", "neo_app.video.video_lora_ui_regression", "run_video_lora_ui_regression", 33),
)

EXPECTED_SUPPORTED: dict[str, dict[str, Any]] = {
    "minimax_h3.unet.txt2vid": {"standard": True, "speed": True, "targets": ["all"]},
    "minimax_h3.unet.img2vid": {"standard": True, "speed": True, "targets": ["all"]},
    "minimax_h3.unet.first_last_frame": {"standard": True, "speed": True, "targets": ["all"]},
    "minimax_h3.unet.reference_to_video": {"standard": True, "speed": True, "targets": ["all"]},
    "minimax_h3.unet.vid2vid": {"standard": True, "speed": True, "targets": ["all"]},
    "ltx23.unet.txt2vid": {"standard": True, "speed": False, "targets": ["all"]},
    "ltx23.unet.img2vid": {"standard": True, "speed": False, "targets": ["all"]},
    "wan22.unet.txt2vid": {"standard": True, "speed": False, "targets": ["all"]},
    "wan22.unet.img2vid": {"standard": True, "speed": False, "targets": ["all"]},
    "wan22.gguf.img2vid_14b_dual_noise": {"standard": True, "speed": True, "targets": ["all", "high", "low"]},
}

REQUIRED_UNPROMOTED_PREFIXES = (
    "minimax_h3.gguf.",
    "ltx23.gguf.",
    "ltx23.native_workflow.",
    "wan22.rapid_aio_gguf.",
    "wan22.native_workflow.",
)


def _git_head() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True, stderr=subprocess.DEVNULL
        ).strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _load_json(relative_path: str) -> dict[str, Any]:
    value = json.loads((ROOT / relative_path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{relative_path} must contain a JSON object")
    return value


def _support_index(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    index: dict[str, dict[str, Any]] = {}
    for group in matrix.get("groups", []):
        if not isinstance(group, dict):
            continue
        for route_id in group.get("route_ids", []) or []:
            route_id = str(route_id)
            if route_id in index:
                raise ValueError(f"Duplicate support-matrix route: {route_id}")
            index[route_id] = group
    return index


def _manifest_state(manifest: dict[str, Any], route_id: str) -> str | None:
    states = manifest.get("route_states") if isinstance(manifest.get("route_states"), dict) else {}
    return states.get(f"comfyui:{route_id.replace('.', ':')}") or states.get(route_id.replace(".", ":"))


def _run_gate(module_name: str, callable_name: str) -> dict[str, Any]:
    module = __import__(module_name, fromlist=[callable_name])
    runner: Callable[[], dict[str, Any]] = getattr(module, callable_name)
    result = runner()
    if not isinstance(result, dict):
        raise TypeError(f"{module_name}.{callable_name} returned a non-object report")
    return result


def run_video_lora_completion_baseline() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
        except Exception as exc:  # noqa: BLE001 - baseline reports every failed contract.
            cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            cases.append({"name": name, "ok": True, "details": details})

    matrix = _load_json("neo_extensions/built_in/video.lora_stack/backend/support_matrix_data.json")
    manifest = _load_json("neo_extensions/built_in/video.lora_stack/extension_manifest.json")
    support = _support_index(matrix)

    def validate_supported_routes() -> dict[str, Any]:
        for route_id, expected in EXPECTED_SUPPORTED.items():
            row = support.get(route_id)
            if not row:
                raise AssertionError(f"Supported route missing from matrix: {route_id}")
            actual = {
                "standard": bool(row.get("supports_standard_lora")),
                "speed": bool(row.get("supports_speed_lora")),
                "targets": list(row.get("allowed_targets") or []),
            }
            if row.get("state") != "supported" or actual != expected:
                raise AssertionError(f"Supported route drift for {route_id}: {row}")
            if not row.get("validated_topology") or not row.get("patch_profile_required"):
                raise AssertionError(f"Supported route lacks validated compiler-profile contract: {route_id}")
            if _manifest_state(manifest, route_id) != "available":
                raise AssertionError(f"Supported route is not available in extension manifest: {route_id}")
        return {"route_count": len(EXPECTED_SUPPORTED), "routes": sorted(EXPECTED_SUPPORTED)}

    def validate_fail_closed_boundaries() -> dict[str, Any]:
        checked: list[str] = []
        for route_id, row in support.items():
            if route_id in EXPECTED_SUPPORTED:
                continue
            if any(route_id.startswith(prefix) for prefix in REQUIRED_UNPROMOTED_PREFIXES) or (
                route_id.startswith("ltx23.unet.") and route_id not in EXPECTED_SUPPORTED
            ):
                checked.append(route_id)
                if row.get("state") == "supported":
                    raise AssertionError(f"Unvalidated Phase-11 route was widened: {route_id}")
                if row.get("supports_standard_lora") or row.get("supports_speed_lora"):
                    raise AssertionError(f"Unpromoted route advertises LoRA capability: {route_id}")
                if _manifest_state(manifest, route_id) == "available":
                    raise AssertionError(f"Unpromoted route is available in extension manifest: {route_id}")
        if not checked:
            raise AssertionError("No unpromoted route boundaries were discovered")
        return {"route_count": len(checked), "routes": sorted(checked)}

    def validate_matrix_policy() -> dict[str, Any]:
        if matrix.get("schema_version") != "neo.video.lora_stack.support_matrix.v1":
            raise AssertionError("Unexpected Video LoRA support-matrix schema")
        if not manifest.get("capabilities", {}).get("fail_closed"):
            raise AssertionError("Extension manifest no longer declares fail-closed behavior")
        return {
            "matrix_schema": matrix.get("schema_version"),
            "manifest_phase": manifest.get("capabilities", {}).get("phase"),
            "fail_closed": True,
        }

    record("supported route contract is unchanged", validate_supported_routes)
    record("unpromoted route families remain fail closed", validate_fail_closed_boundaries)
    record("matrix and manifest preserve exact-route policy", validate_matrix_policy)

    gate_reports: dict[str, dict[str, Any]] = {}
    for gate_id, module_name, callable_name, expected_count in EXPECTED_GATES:
        def execute(
            gate_id: str = gate_id,
            module_name: str = module_name,
            callable_name: str = callable_name,
            expected_count: int = expected_count,
        ) -> dict[str, Any]:
            result = _run_gate(module_name, callable_name)
            gate_reports[gate_id] = result
            if result.get("gate") != "pass" or int(result.get("failed") or 0) != 0:
                raise AssertionError(f"{gate_id} failed: {result}")
            if int(result.get("case_count") or 0) != expected_count:
                raise AssertionError(
                    f"{gate_id} count drift: expected {expected_count}, got {result.get('case_count')}"
                )
            return {
                "schema_version": result.get("schema_version"),
                "case_count": expected_count,
                "passed": result.get("passed"),
            }

        record(f"aggregate regression: {gate_id}", execute)

    regression_count = sum(int(report.get("case_count") or 0) for report in gate_reports.values())
    record(
        "aggregate regression count remains 144",
        lambda: (
            {"case_count": regression_count}
            if regression_count == 144
            else (_ for _ in ()).throw(AssertionError(f"Expected 144 regression cases, got {regression_count}"))
        ),
    )

    failed = [case for case in cases if not case.get("ok")]
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "source_tree": _git_head(),
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "audit_case_count": len(cases),
        "audit_passed": len(cases) - len(failed),
        "audit_failed": len(failed),
        "regression_case_count": regression_count,
        "expected_regression_case_count": 144,
        "supported_route_count": len(EXPECTED_SUPPORTED),
        "physical_gpu_inference_verified": False,
        "physical_gpu_inference_note": "Phase 11 is a deterministic current-tree baseline. Physical ComfyUI/GPU proof is a later release gate.",
        "next_phase": "phase_12_canonical_persistence_metadata_replay",
        "next_phase_allowed": not failed,
        "cases": cases,
    }


def main() -> int:
    report = run_video_lora_completion_baseline()
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())

