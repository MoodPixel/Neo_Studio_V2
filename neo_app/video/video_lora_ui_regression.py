from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from neo_app.video.video_lora_ui import (
    MODEL_ONLY_LOADER,
    is_speed_candidate,
    support_for_route_id,
    validate_model_only_loader,
    video_lora_catalog_from_object_info,
)

SCHEMA_VERSION = "neo.video.lora_stack.ui_regression.v1"
PHASE = "phase_10"
ROOT = Path(__file__).resolve().parents[2]
NORMAL_LORA = "cinematic_character.safetensors"
H3_SPEED = "MiniMax-LightX2V-4steps.safetensors"
H3_LIGHTNING = "hailuo_lightning_8steps.safetensors"
WAN_SPEED = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _object_info(loras: list[str] | None = None, *, include_loader: bool = True, omit_strength: bool = False) -> dict[str, Any]:
    info: dict[str, Any] = {}
    if include_loader:
        required: dict[str, Any] = {
            "model": ["MODEL", {}],
            "lora_name": [list(loras if loras is not None else [NORMAL_LORA, H3_SPEED, H3_LIGHTNING, WAN_SPEED]), {}],
        }
        if not omit_strength:
            required["strength_model"] = ["FLOAT", {"default": 1.0}]
        info[MODEL_ONLY_LOADER] = {"input": {"required": required}}
    return info


def _catalog(family: str, loader: str, mode: str, loras: list[str] | None = None) -> dict[str, Any]:
    return video_lora_catalog_from_object_info(
        family=family,
        loader=loader,
        generation_type=mode,
        profile={"profile_id": "video.comfyui_portable", "provider_id": "comfyui_portable", "display_name": "Test Comfy"},
        object_info=_object_info(loras),
    )


def run_video_lora_ui_regression() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
        except Exception as exc:  # noqa: BLE001 - deterministic gate records full failure details.
            cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            cases.append({"name": name, "ok": True, "details": details})

    def support_case(route_id: str, *, state: str, standard: bool, speed: bool, targets: list[str]) -> dict[str, Any]:
        support = support_for_route_id(route_id, "comfyui")
        _assert(support.get("state") == state, f"{route_id} state mismatch: {support}")
        _assert(bool(support.get("supports_standard_lora")) == standard, f"{route_id} standard support mismatch")
        _assert(bool(support.get("supports_speed_lora")) == speed, f"{route_id} speed support mismatch")
        _assert(list(support.get("allowed_targets") or []) == targets, f"{route_id} target mismatch")
        return support

    record("H3 UNET Txt2Vid is standard+speed/all", lambda: support_case("minimax_h3.unet.txt2vid", state="supported", standard=True, speed=True, targets=["all"]))
    record("H3 UNET Img2Vid is standard+speed/all", lambda: support_case("minimax_h3.unet.img2vid", state="supported", standard=True, speed=True, targets=["all"]))
    record("H3 GGUF remains provisional", lambda: support_case("minimax_h3.gguf.img2vid", state="provisional", standard=False, speed=False, targets=["all"]))
    record("LTX UNET Txt2Vid is standard-only", lambda: support_case("ltx23.unet.txt2vid", state="supported", standard=True, speed=False, targets=["all"]))
    record("LTX UNET Img2Vid is standard-only", lambda: support_case("ltx23.unet.img2vid", state="supported", standard=True, speed=False, targets=["all"]))
    record("LTX extended route remains provisional", lambda: support_case("ltx23.unet.vid2vid", state="provisional", standard=False, speed=False, targets=["all"]))
    record("WAN UNET Txt2Vid is standard-only", lambda: support_case("wan22.unet.txt2vid", state="supported", standard=True, speed=False, targets=["all"]))
    record("WAN UNET Img2Vid is standard-only", lambda: support_case("wan22.unet.img2vid", state="supported", standard=True, speed=False, targets=["all"]))
    record("WAN dual-noise supports branch speed", lambda: support_case("wan22.gguf.img2vid_14b_dual_noise", state="supported", standard=True, speed=True, targets=["all", "high", "low"]))
    record("WAN Rapid AIO remains blocked", lambda: support_case("wan22.rapid_aio_gguf.img2vid", state="blocked", standard=False, speed=False, targets=["all"]))

    def valid_loader() -> dict[str, Any]:
        probe = validate_model_only_loader(_object_info())
        _assert(probe.get("safe") and probe.get("available"), f"Valid ModelOnly loader rejected: {probe}")
        _assert(NORMAL_LORA in probe.get("catalog", []), "Normal LoRA missing from live catalog")
        return probe
    record("ModelOnly loader signature accepted", valid_loader)
    record("missing ModelOnly loader fails closed", lambda: (_assert(not validate_model_only_loader({}).get("safe"), "Missing loader was accepted"), validate_model_only_loader({}))[1])
    record("incomplete ModelOnly signature fails closed", lambda: (_assert(not validate_model_only_loader(_object_info(omit_strength=True)).get("safe"), "Incomplete signature was accepted"), validate_model_only_loader(_object_info(omit_strength=True)))[1])

    record("H3 MiniMax LightX2V classifier", lambda: _assert(is_speed_candidate(H3_SPEED, "minimax_h3"), "MiniMax LightX2V not classified as speed"))
    record("H3 Hailuo Lightning classifier", lambda: _assert(is_speed_candidate(H3_LIGHTNING, "minimax_h3"), "Hailuo Lightning not classified as speed"))
    record("H3 normal manual LoRA is not classifier-gated", lambda: _assert(not is_speed_candidate(NORMAL_LORA, "minimax_h3"), "Normal LoRA misclassified"))
    record("WAN LightX2V classifier", lambda: _assert(is_speed_candidate(WAN_SPEED, "wan22"), "WAN LightX2V not classified as speed"))

    def h3_catalog() -> dict[str, Any]:
        payload = _catalog("minimax_h3", "unet", "img2vid")
        _assert(payload.get("ready"), f"H3 catalog not ready: {payload}")
        _assert(H3_SPEED in payload.get("speed_candidates", []), "H3 speed candidate missing")
        _assert(NORMAL_LORA in payload.get("catalog", []), "Normal manual LoRA missing")
        _assert(payload.get("manual_selection_classifier_gate") is False, "Manual selection classifier gate was enabled")
        return payload
    record("H3 live catalog is ready and advisory", h3_catalog)

    def ltx_catalog() -> dict[str, Any]:
        payload = _catalog("ltx23", "unet", "txt2vid")
        _assert(payload.get("ready"), f"LTX catalog not ready: {payload}")
        _assert(payload.get("support", {}).get("supports_speed_lora") is False, "LTX speed support widened")
        return payload
    record("LTX catalog stays standard-only", ltx_catalog)

    def wan_catalog() -> dict[str, Any]:
        payload = _catalog("wan22", "gguf", "img2vid")
        _assert(payload.get("route", {}).get("route_id") == "wan22.gguf.img2vid_14b_dual_noise", f"WAN dual route resolution failed: {payload.get('route')}")
        _assert(payload.get("ready"), f"WAN catalog not ready: {payload}")
        _assert(payload.get("support", {}).get("allowed_targets") == ["all", "high", "low"], "WAN branch targets missing")
        return payload
    record("WAN dual-noise catalog resolves semantic targets", wan_catalog)

    def empty_catalog() -> dict[str, Any]:
        payload = _catalog("minimax_h3", "unet", "img2vid", [])
        _assert(not payload.get("ready") and payload.get("fail_closed"), "Empty catalog did not fail closed")
        return payload
    record("empty live LoRA catalog fails closed", empty_catalog)

    def unsupported_provider() -> dict[str, Any]:
        payload = video_lora_catalog_from_object_info(
            family="wan22", loader="unet", generation_type="txt2vid",
            profile={"profile_id": "video.xai", "provider_id": "xai", "display_name": "xAI"},
            object_info=_object_info(),
        )
        _assert(not payload.get("ready") and payload.get("support", {}).get("state") == "blocked", "Unsupported provider was accepted")
        return payload
    record("non-Comfy provider fails closed", unsupported_provider)

    manifest = json.loads((ROOT / "neo_extensions/built_in/video.lora_stack/extension_manifest.json").read_text(encoding="utf-8"))
    surface_manifest = json.loads((ROOT / "neo_app/surfaces/surface_manifest.json").read_text(encoding="utf-8"))
    neo_js = (ROOT / "neo_app/static/js/neo.js").read_text(encoding="utf-8")
    video_js = (ROOT / "neo_app/static/js/surfaces/video.js").read_text(encoding="utf-8")
    main_py = (ROOT / "neo_app/main.py").read_text(encoding="utf-8")

    record("extension manifest has route-state UI matrix", lambda: _assert(bool(manifest.get("route_states")) and manifest.get("ui_schema", {}).get("phase") == "phase_10", "Phase-10 route-state manifest missing"))
    record("H3 Img2Vid manifest route is available", lambda: _assert(manifest.get("route_states", {}).get("comfyui:minimax_h3:unet:img2vid") == "available", "H3 Img2Vid UI route missing"))
    record("LTX extended manifest route is gated", lambda: _assert(manifest.get("route_states", {}).get("comfyui:ltx23:unet:vid2vid") == "planned_gated", "LTX extended UI route widened"))
    record("WAN Rapid AIO manifest route is provider-gated", lambda: _assert(manifest.get("route_states", {}).get("comfyui:wan22:rapid_aio_gguf:img2vid") == "provider_gated", "WAN Rapid AIO UI route widened"))

    def surface_slot() -> None:
        video = next(item for item in surface_manifest.get("surfaces", []) if item.get("surface_id") == "video")
        assets = next(item for item in video.get("subtabs", []) if item.get("subtab_id") == "assets")
        _assert("video.assets.lora_stack" in assets.get("slots", []), "Video LoRA mount slot missing from surface manifest")
    record("Video Assets declares video.assets.lora_stack", surface_slot)

    record("canonical Video LoRA payload is emitted", lambda: _assert("payload.extensions" in neo_js and "VIDEO_LORA_STACK_EXTENSION_ID" in neo_js and "videoLoraStackPayloadBlock" in neo_js, "Canonical Video LoRA payload wiring missing"))
    record("legacy WAN LoRA parameter line is hidden", lambda: _assert("renderLine('Video LoRA / LightX2V'" not in neo_js, "Legacy WAN LoRA controls remain visible"))
    record("legacy H3 Turbo fields are hidden from H3 parameter line", lambda: _assert("renderLine('MiniMax H3', ['h3_keyframe_role', 'h3_ref_image_size', 'h3_shift_video', 'h3_shift_audio', 'h3_turbo_enabled'" not in neo_js, "Legacy H3 Turbo fields remain visible"))
    record("Video Assets panel contains final catalog and retirement controls", lambda: _assert("videoLoraRefreshCatalogBtn" in neo_js and "videoLoraPickerSelect" in neo_js and "retireLegacyVideoLoraDraft" in neo_js and "videoLoraMigrateLegacyBtn" not in neo_js, "Final Video LoRA Assets UI controls missing or legacy UI remains"))
    record("Video surface diagnostics expose LoRA catalog endpoint", lambda: _assert("videoLoraCatalogEndpoint: '/api/video/lora-catalog'" in video_js, "Video diagnostics missing LoRA endpoint"))
    record("FastAPI exposes Video LoRA catalog endpoint", lambda: _assert('@app.get("/api/video/lora-catalog")' in main_py and "video_lora_catalog_payload" in main_py, "Video LoRA API endpoint missing"))

    failed = [case for case in cases if not case.get("ok")]
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "cases": cases,
        "previous_regression_case_count": 111,
        "combined_case_count": 111 + len(cases),
        "next_phase_allowed": not failed,
    }


def main() -> int:
    report = run_video_lora_ui_regression()
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
