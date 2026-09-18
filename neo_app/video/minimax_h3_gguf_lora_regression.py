from __future__ import annotations

import json
from typing import Any, Callable

from neo_app.video import minimax_h3_lora_integration as integration
from neo_app.video.minimax_h3_lora_regression import (
    MODES,
    SPEED_LORA,
    STANDARD_LORA,
    _build_with_rows,
    _request,
    synthetic_h3_object_info,
)

SCHEMA_VERSION = "neo.video.minimax_h3.gguf_lora_regression.v1"


def synthetic_h3_gguf_object_info() -> dict[str, Any]:
    info = synthetic_h3_object_info()
    info["UnetLoaderGGUF"] = {
        "input": {"required": {"unet_name": [["minimax_h3_fl2va-Q4_K_M.gguf", "minimax_h3_ref2va-Q4_K_M.gguf"], {}]}},
        "output": ["MODEL"],
        "output_name": ["MODEL"],
    }
    return info


def _expect_error(fn: Callable[[], Any], text: str) -> str:
    try:
        fn()
    except ValueError as exc:
        if text.casefold() not in str(exc).casefold():
            raise AssertionError(f"Expected {text!r}, got {str(exc)!r}") from exc
        return str(exc)
    raise AssertionError(f"Expected ValueError containing {text!r}")


def run_minimax_h3_gguf_lora_regression() -> dict[str, Any]:
    integration.install_minimax_h3_lora_integration()
    info = synthetic_h3_gguf_object_info()
    cases: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
        except Exception as exc:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            cases.append({"name": name, "ok": True, "details": details})

    def active(mode: str, role: str) -> dict[str, Any]:
        name = SPEED_LORA if role == "speed" else STANDARD_LORA
        compiled = _build_with_rows(
            _request(mode, loader="gguf"),
            [{"name": name, "strength_model": 1.0, "role": role, "target": "all"}],
            info,
        )
        runtime = compiled.get("video_lora_stack") or {}
        profile = compiled.get("lora_patch_profile") or {}
        topology = runtime.get("gguf_topology") or {}
        assert compiled.get("route_id") == f"minimax_h3.gguf.{mode}"
        assert profile.get("validated") is True
        assert runtime.get("active") is True and runtime.get("applied_count") == 1
        assert topology.get("validated") is True and topology.get("evidence") == "live_object_info_schema"
        assert topology.get("model_loader") == "UnetLoaderGGUF"
        workflow = compiled.get("workflow") or {}
        loras = [node for node in workflow.values() if node.get("class_type") == "LoraLoaderModelOnly"]
        assert len(loras) == 1 and loras[0]["inputs"]["lora_name"] == name
        sigma = [node for node in workflow.values() if node.get("class_type") == "MiniMaxH3SigmaShift"]
        assert len(sigma) == 1 and sigma[0]["inputs"]["model"] == runtime.get("final_model_ref")
        return {"mode": mode, "role": role, "final_model_ref": runtime.get("final_model_ref")}

    for mode in MODES:
        record(f"{mode}: GGUF standard LoRA", lambda mode=mode: active(mode, "standard"))
        record(f"{mode}: GGUF speed/Turbo LoRA", lambda mode=mode: active(mode, "speed"))

    # Use the compiler module directly; this keeps the legacy bridge on the same canonical path.
    from neo_app.video import minimax_h3_compiler as h3
    record("img2vid: GGUF legacy Turbo bridge", lambda: _legacy_turbo(h3, info))

    no_gguf = synthetic_h3_object_info()
    record(
        "GGUF fallback loader name is rejected",
        lambda: _expect_error(lambda: _build_with_rows(_request("img2vid", loader="gguf"), [{"name": STANDARD_LORA}], no_gguf), "live supported GGUF model loader"),
    )
    wrong_output = synthetic_h3_gguf_object_info()
    wrong_output["UnetLoaderGGUF"]["output"] = ["LATENT"]
    record(
        "GGUF loader without MODEL output is rejected",
        lambda: _expect_error(lambda: _build_with_rows(_request("img2vid", loader="gguf"), [{"name": STANDARD_LORA}], wrong_output), "does not advertise a MODEL output"),
    )

    failed = [case for case in cases if not case["ok"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": "phase_13",
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "gpu_inference_proven": False,
        "cases": cases,
    }


def _legacy_turbo(h3: Any, info: dict[str, Any]) -> dict[str, Any]:
    compiled = h3.build_minimax_h3_workflow(_request("img2vid", loader="gguf", turbo=True, turbo_name=SPEED_LORA), object_info=info)
    runtime = compiled.get("video_lora_stack") or {}
    assert runtime.get("speed_count") == 1
    assert (runtime.get("legacy_turbo_bridge") or {}).get("bridged") is True
    return {"speed_count": 1, "topology": runtime.get("gguf_topology")}


def main() -> int:
    report = run_minimax_h3_gguf_lora_regression()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
