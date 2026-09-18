from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Callable

from neo_app.video import minimax_h3_lora_integration as integration
from neo_app.video.minimax_h3_lora_regression import MODES, SPEED_LORA, _build_with_rows, _request, synthetic_h3_object_info
from neo_app.video.minimax_h3_gguf_lora_regression import synthetic_h3_gguf_object_info

SCHEMA_VERSION = "neo.video.minimax_h3.sigma_shift_regression.v1"
CUSTOM_VIDEO_SHIFT = 14.5
CUSTOM_AUDIO_SHIFT = 5.0


def _sigma_node(compiled: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    matches = [(str(node_id), node) for node_id, node in (compiled.get("workflow") or {}).items() if isinstance(node, dict) and node.get("class_type") == "MiniMaxH3SigmaShift"]
    if len(matches) != 1:
        raise AssertionError(f"Expected exactly one MiniMaxH3SigmaShift node, found {len(matches)}.")
    return matches[0]


def _assert_shift(compiled: dict[str, Any], *, video: float, audio: float, expect_lora: bool) -> dict[str, Any]:
    node_id, sigma = _sigma_node(compiled)
    inputs = sigma.get("inputs") or {}
    if inputs.get("shift_video") != video: raise AssertionError(f"shift_video expected {video}, got {inputs.get('shift_video')}")
    if inputs.get("shift_audio") != audio: raise AssertionError(f"shift_audio expected {audio}, got {inputs.get('shift_audio')}")
    model_ref = inputs.get("model")
    if not isinstance(model_ref, list) or len(model_ref) != 2: raise AssertionError("Sigma node model input is not connected.")
    workflow = compiled.get("workflow") or {}
    loras = [(str(key), value) for key, value in workflow.items() if isinstance(value, dict) and value.get("class_type") == "LoraLoaderModelOnly"]
    if expect_lora:
        if len(loras) != 1: raise AssertionError(f"Expected one canonical LoRA node, found {len(loras)}.")
        if model_ref != [loras[0][0], 0]: raise AssertionError("Sigma node is not fed by the canonical LoRA output.")
    elif loras:
        raise AssertionError("Unexpected LoRA node in the no-LoRA sigma case.")
    return {"route_id": compiled.get("route_id"), "sigma_node_id": node_id, "shift_video": inputs["shift_video"], "shift_audio": inputs["shift_audio"], "model_ref": model_ref, "lora_active": expect_lora}


def run_h3_sigma_shift_regression() -> dict[str, Any]:
    integration.install_minimax_h3_lora_integration()
    cases: list[dict[str, Any]] = []
    def record(name: str, fn: Callable[[], Any]) -> None:
        try: cases.append({"name": name, "ok": True, "details": fn()})
        except Exception as exc: cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    def compile_case(mode: str, loader: str, with_lora: bool) -> dict[str, Any]:
        request = replace(_request(mode, loader=loader), h3_shift_video=CUSTOM_VIDEO_SHIFT, h3_shift_audio=CUSTOM_AUDIO_SHIFT)
        info = synthetic_h3_gguf_object_info() if loader == "gguf" else synthetic_h3_object_info()
        rows = [{"uid":"sigma_speed","enabled":True,"name":SPEED_LORA,"strength_model":1.0,"role":"speed","target":"all"}] if with_lora else []
        return _assert_shift(_build_with_rows(request, rows, info), video=CUSTOM_VIDEO_SHIFT, audio=CUSTOM_AUDIO_SHIFT, expect_lora=with_lora)

    for loader in ("unet", "gguf"):
        for mode in MODES:
            record(f"{loader} {mode}: custom shifts without LoRA", lambda mode=mode, loader=loader: compile_case(mode, loader, False))
            record(f"{loader} {mode}: custom shifts survive canonical Turbo LoRA", lambda mode=mode, loader=loader: compile_case(mode, loader, True))

    for loader in ("unet", "gguf"):
        record(f"{loader} img2vid: defaults remain 12/3", lambda loader=loader: _default_case(loader))
    record("request payload round-trip preserves custom values", _payload_round_trip)
    failed=sum(not case["ok"] for case in cases)
    return {"schema_version":SCHEMA_VERSION,"phase":"phase_20_1","ok":failed == 0,"gate":"pass" if failed == 0 else "fail","case_count":len(cases),"passed":len(cases)-failed,"failed":failed,"gpu_inference_proven":False,"cases":cases}


def _default_case(loader: str) -> dict[str, Any]:
    info=synthetic_h3_gguf_object_info() if loader == "gguf" else synthetic_h3_object_info()
    return _assert_shift(_build_with_rows(_request("img2vid",loader=loader),[],info),video=12.0,audio=3.0,expect_lora=False)


def _payload_round_trip() -> dict[str, Any]:
    request=replace(_request("img2vid",loader="unet"),h3_shift_video=CUSTOM_VIDEO_SHIFT,h3_shift_audio=CUSTOM_AUDIO_SHIFT)
    payload=request.payload()
    if payload.get("h3_shift_video") != CUSTOM_VIDEO_SHIFT or payload.get("h3_shift_audio") != CUSTOM_AUDIO_SHIFT: raise AssertionError("Request payload lost custom sigma values.")
    return {"h3_shift_video":payload["h3_shift_video"],"h3_shift_audio":payload["h3_shift_audio"]}


if __name__ == "__main__":
    report=run_h3_sigma_shift_regression(); print(json.dumps(report,indent=2)); raise SystemExit(0 if report["ok"] else 1)
