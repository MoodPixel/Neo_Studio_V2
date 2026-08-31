from __future__ import annotations

from dataclasses import replace
import json
from typing import Any, Callable

from neo_app.video import minimax_h3_compiler as h3
from neo_app.video import minimax_h3_lora_integration as integration
from neo_app.video.video_lora_runtime import H3_MODEL_ONLY_LOADER, is_h3_speed_lora_name

SCHEMA_VERSION = "neo.video.minimax_h3.lora_regression.v1"
PHASE = "phase_6"
MODES = ("txt2vid", "img2vid", "first_last_frame", "reference_to_video", "vid2vid")
STANDARD_LORA = "cinematic_motion_character.safetensors"
STANDARD_LORA_2 = "wardrobe_detail.safetensors"
SPEED_LORA = "MiniMax-LightX2V-4steps.safetensors"
LIGHTNING_LORA = "hailuo_lightning_8steps.safetensors"
FIXED_SEED = 246813579


def _combo(values: list[str]) -> list[Any]:
    return [values, {}]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def synthetic_h3_object_info(*, model_only: bool = True, generic_lora: bool = False, loras: list[str] | None = None) -> dict[str, Any]:
    lora_values = list(loras if loras is not None else [STANDARD_LORA, STANDARD_LORA_2, SPEED_LORA, LIGHTNING_LORA])
    info: dict[str, Any] = {
        "UNETLoader": {
            "input": {
                "required": {
                    "unet_name": _combo([
                        "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
                        "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
                    ]),
                    "weight_dtype": _combo(["default"]),
                }
            }
        },
        "CLIPLoader": {
            "input": {
                "required": {
                    "clip_name": _combo(["qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors"]),
                    "type": _combo(["minimax"]),
                }
            }
        },
        "VAELoader": {
            "input": {
                "required": {
                    "vae_name": _combo([
                        "minimax_h3_video_vae_fp16.safetensors",
                        "minimax_h3_audio_vae_fp32.safetensors",
                    ])
                }
            }
        },
        "SaveVideo": {"input": {"required": {"format": _combo(["auto", "mp4"])}}},
    }
    if model_only:
        info[H3_MODEL_ONLY_LOADER] = {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "lora_name": _combo(lora_values),
                    "strength_model": ["FLOAT", {"default": 1.0}],
                }
            }
        }
    if generic_lora:
        info["LoraLoader"] = {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "clip": ["CLIP", {}],
                    "lora_name": _combo(lora_values),
                    "strength_model": ["FLOAT", {"default": 1.0}],
                    "strength_clip": ["FLOAT", {"default": 1.0}],
                }
            }
        }
    return info


def _request(mode: str, *, loader: str = "unet", turbo: bool = False, turbo_name: str = "") -> h3.MiniMaxH3CompileRequest:
    kwargs: dict[str, Any] = {
        "loader": loader,
        "generation_type": mode,
        "prompt": "Phase 6 MiniMax H3 LoRA regression shot.",
        "frames": 22,
        "steps": 6,
        "sampler": "euler",
        "scheduler": "beta",
        "seed": FIXED_SEED,
        "h3_shift_audio": 5.0,
        "h3_turbo_enabled": turbo,
        "h3_turbo_lora": turbo_name,
        "h3_turbo_strength": 1.0,
    }
    if mode == "img2vid":
        kwargs["source_image_name"] = "phase6_source.png"
    elif mode == "first_last_frame":
        kwargs["first_image_name"] = "phase6_first.png"
        kwargs["last_image_name"] = "phase6_last.png"
    elif mode == "reference_to_video":
        kwargs["h3_reference_images"] = (h3.H3ReferenceMedia(name="phase6_reference.png"),)
    elif mode == "vid2vid":
        kwargs["source_video_name"] = "phase6_source.mp4"
    return h3.MiniMaxH3CompileRequest(**kwargs)


def _extension_payload(req: h3.MiniMaxH3CompileRequest, rows: list[dict[str, Any]], *, enabled: bool = True) -> dict[str, Any]:
    payload = req.payload()
    payload["extensions"] = {
        "video.lora_stack": {
            "enabled": enabled,
            "version": 1,
            "inputs": {},
            "params": {"loras": rows},
            "assets": {},
            "metadata": {},
        }
    }
    return payload


def _build_with_rows(req: h3.MiniMaxH3CompileRequest, rows: list[dict[str, Any]], object_info: dict[str, Any], *, enabled: bool = True) -> dict[str, Any]:
    token = integration._PHASE5_PAYLOAD.set(_extension_payload(req, rows, enabled=enabled))
    try:
        return h3.build_minimax_h3_workflow(req, object_info=object_info)
    finally:
        integration._PHASE5_PAYLOAD.reset(token)


def _lora_nodes(compiled: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    return [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and str(node.get("class_type") or "") == H3_MODEL_ONLY_LOADER
    ]


def _sigma_node(compiled: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    rows = [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and str(node.get("class_type") or "") == "MiniMaxH3SigmaShift"
    ]
    _assert(len(rows) == 1, f"Expected exactly one MiniMaxH3SigmaShift node, found {len(rows)}")
    return rows[0]


def _assert_profile_and_chain(compiled: dict[str, Any], expected_names: list[str], expected_roles: list[str]) -> None:
    runtime = compiled.get("video_lora_stack") or {}
    profile = compiled.get("lora_patch_profile") or {}
    workflow = compiled.get("workflow") or {}
    prompt_workflow = (compiled.get("prompt_api_payload") or {}).get("prompt")
    _assert(prompt_workflow == workflow, "prompt_api_payload.prompt does not match the patched workflow")
    _assert(profile.get("schema_version") == "neo.video.lora_patch_profile.v1", "Missing/invalid H3 LoRA patch-profile schema")
    _assert(profile.get("owner") == "compiler" and profile.get("loader_type") == "model_only", "H3 LoRA patch profile is not compiler-owned/model-only")
    _assert(profile.get("loader_node_class") == H3_MODEL_ONLY_LOADER, "H3 LoRA profile is not locked to LoraLoaderModelOnly")
    _assert(profile.get("targets") == ["all"], "H3 LoRA profile exposes an invalid target")
    _assert(bool(profile.get("validated")), "H3 UNET LoRA patch profile is not validated")

    nodes = _lora_nodes(compiled)
    names = [str(node.get("inputs", {}).get("lora_name") or "") for _, node in nodes]
    _assert(names == expected_names, f"LoRA node order mismatch: expected {expected_names}, got {names}")
    roles = [str(item.get("role") or "") for item in runtime.get("applied", [])]
    _assert(roles == expected_roles, f"LoRA runtime role order mismatch: expected {expected_roles}, got {roles}")
    _assert(runtime.get("applied_count") == len(expected_names), "LoRA applied_count does not match inserted nodes")
    _assert(bool(runtime.get("live_catalog_validated")), "Active H3 LoRA stack did not validate the live ModelOnly catalog")

    _, sigma = _sigma_node(compiled)
    final_ref = runtime.get("final_model_ref")
    _assert(sigma.get("inputs", {}).get("model") == final_ref, "MiniMaxH3SigmaShift does not consume the final Video LoRA model reference")

    json.dumps({"workflow": workflow, "profile": profile, "runtime": runtime})


def _original_build() -> Callable[..., dict[str, Any]]:
    originals = getattr(h3, "_neo_phase5_video_lora_originals", {})
    original = originals.get("build_minimax_h3_workflow")
    if not callable(original):
        raise AssertionError("Phase-5 original H3 compiler build function is unavailable")
    return original


def _case_no_lora_equivalence(mode: str, object_info: dict[str, Any], *, disabled_with_rows: bool = False) -> dict[str, Any]:
    req = _request(mode)
    original = _original_build()(replace(req, h3_turbo_enabled=False, h3_turbo_lora=""), object_info=object_info)
    rows = [{"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}] if disabled_with_rows else []
    current = _build_with_rows(req, rows, object_info, enabled=not disabled_with_rows)
    _assert(current.get("workflow") == original.get("workflow"), f"No-op LoRA workflow changed for {mode}")
    _assert(not _lora_nodes(current), f"No-op LoRA workflow unexpectedly contains LoRA nodes for {mode}")
    runtime = current.get("video_lora_stack") or {}
    _assert(not runtime.get("active") and runtime.get("applied_count") == 0, f"No-op LoRA runtime is not inert for {mode}")
    return {"mode": mode, "disabled_with_rows": disabled_with_rows, "workflow_nodes": len(current.get("workflow") or {})}


def _case_stack(mode: str, object_info: dict[str, Any], rows: list[dict[str, Any]], expected_names: list[str], expected_roles: list[str]) -> dict[str, Any]:
    compiled = _build_with_rows(_request(mode), rows, object_info)
    _assert_profile_and_chain(compiled, expected_names, expected_roles)
    return {
        "mode": mode,
        "applied": expected_names,
        "roles": expected_roles,
        "final_model_ref": (compiled.get("video_lora_stack") or {}).get("final_model_ref"),
    }


def _expect_error(fn: Callable[[], Any], contains: str) -> str:
    try:
        fn()
    except ValueError as exc:
        text = str(exc)
        _assert(contains.casefold() in text.casefold(), f"Expected error containing {contains!r}, got {text!r}")
        return text
    raise AssertionError(f"Expected ValueError containing {contains!r}")


def run_minimax_h3_lora_regression() -> dict[str, Any]:
    integration.install_minimax_h3_lora_integration()
    object_info = synthetic_h3_object_info()
    cases: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
        except Exception as exc:  # noqa: BLE001 - regression harness records the full gate.
            cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
        else:
            cases.append({"name": name, "ok": True, "details": details})

    record("speed classifier accepts MiniMax LightX2V", lambda: _assert(is_h3_speed_lora_name(SPEED_LORA), "MiniMax LightX2V was not classified as speed"))
    record("speed classifier accepts Hailuo Lightning", lambda: _assert(is_h3_speed_lora_name(LIGHTNING_LORA), "Hailuo Lightning was not classified as speed"))
    record("standard manual LoRA is not speed-classifier gated", lambda: _assert(not is_h3_speed_lora_name(STANDARD_LORA), "Standard LoRA was misclassified as speed"))

    standard = [{"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}]
    multiple_standard = [
        {"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"},
        {"name": STANDARD_LORA_2, "strength_model": 0.65, "role": "standard", "target": "all"},
    ]
    speed = [{"name": SPEED_LORA, "strength_model": 1.0, "role": "speed", "target": "all"}]
    mixed = [
        {"name": SPEED_LORA, "strength_model": 1.0, "role": "speed", "target": "all"},
        {"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"},
    ]

    for mode in MODES:
        record(f"{mode}: empty stack graph equivalence", lambda mode=mode: _case_no_lora_equivalence(mode, object_info))
        record(f"{mode}: disabled populated stack graph equivalence", lambda mode=mode: _case_no_lora_equivalence(mode, object_info, disabled_with_rows=True))
        record(f"{mode}: standard LoRA", lambda mode=mode: _case_stack(mode, object_info, standard, [STANDARD_LORA], ["standard"]))
        record(f"{mode}: multiple standard LoRAs", lambda mode=mode: _case_stack(mode, object_info, multiple_standard, [STANDARD_LORA, STANDARD_LORA_2], ["standard", "standard"]))
        record(f"{mode}: speed/Turbo LoRA", lambda mode=mode: _case_stack(mode, object_info, speed, [SPEED_LORA], ["speed"]))
        record(f"{mode}: standard + speed ordering", lambda mode=mode: _case_stack(mode, object_info, mixed, [STANDARD_LORA, SPEED_LORA], ["standard", "speed"]))

    def legacy_turbo_only() -> dict[str, Any]:
        req = _request("img2vid", turbo=True, turbo_name=SPEED_LORA)
        compiled = h3.build_minimax_h3_workflow(req, object_info=object_info)
        _assert_profile_and_chain(compiled, [SPEED_LORA], ["speed"])
        bridge = (compiled.get("video_lora_stack") or {}).get("legacy_turbo_bridge") or {}
        _assert(bool(bridge.get("bridged")), "Legacy Turbo was not bridged into the universal speed stack")
        return bridge

    def legacy_duplicate_speed() -> dict[str, Any]:
        req = _request("img2vid", turbo=True, turbo_name=SPEED_LORA)
        compiled = _build_with_rows(req, speed, object_info)
        _assert_profile_and_chain(compiled, [SPEED_LORA], ["speed"])
        bridge = (compiled.get("video_lora_stack") or {}).get("legacy_turbo_bridge") or {}
        _assert(bool(bridge.get("duplicate_suppressed")) and not bridge.get("bridged"), "Legacy duplicate Turbo was not suppressed")
        return bridge

    def legacy_duplicate_standard_promotes() -> dict[str, Any]:
        req = _request("img2vid", turbo=True, turbo_name=SPEED_LORA)
        same_file = [{"name": SPEED_LORA, "strength_model": 0.9, "role": "standard", "target": "high"}]
        compiled = _build_with_rows(req, same_file, object_info)
        _assert_profile_and_chain(compiled, [SPEED_LORA], ["speed"])
        bridge = (compiled.get("video_lora_stack") or {}).get("legacy_turbo_bridge") or {}
        _assert(bool(bridge.get("duplicate_suppressed")), "Legacy Turbo duplicate was not suppressed")
        _assert(bool(bridge.get("existing_role_promoted")), "Legacy Turbo duplicate did not promote the existing row to speed")
        _assert(bool(bridge.get("existing_target_normalized")), "Legacy Turbo duplicate did not normalize the existing H3 target to all")
        applied = (compiled.get("video_lora_stack") or {}).get("applied") or []
        _assert(bool(applied) and float(applied[0].get("strength_model")) == 0.9, "Universal duplicate row strength was not preserved")
        return bridge

    def legacy_auto_discovery() -> dict[str, Any]:
        req = _request("img2vid", turbo=True, turbo_name="")
        compiled = h3.build_minimax_h3_workflow(req, object_info=object_info)
        runtime = compiled.get("video_lora_stack") or {}
        _assert(runtime.get("speed_count") == 1, "Legacy Turbo auto-discovery did not create exactly one speed row")
        bridge = runtime.get("legacy_turbo_bridge") or {}
        _assert(bridge.get("selected_name") in {SPEED_LORA, LIGHTNING_LORA}, f"Unexpected auto-discovered H3 speed LoRA: {bridge.get('selected_name')}")
        return bridge

    record("img2vid: legacy Turbo uses universal stack", legacy_turbo_only)
    record("img2vid: legacy Turbo duplicate suppression", legacy_duplicate_speed)
    record("img2vid: legacy Turbo promotes/normalizes duplicate universal row", legacy_duplicate_standard_promotes)
    record("img2vid: legacy Turbo auto-discovery", legacy_auto_discovery)

    record(
        "missing selected LoRA fails closed",
        lambda: _expect_error(
            lambda: _build_with_rows(
                _request("img2vid"),
                [{"name": "missing_h3_lora.safetensors", "strength_model": 1.0, "role": "standard", "target": "all"}],
                object_info,
            ),
            "not visible in the live LoraLoaderModelOnly catalog",
        ),
    )
    record(
        "empty LoraLoaderModelOnly catalog fails closed",
        lambda: _expect_error(
            lambda: _build_with_rows(_request("img2vid"), standard, synthetic_h3_object_info(loras=[])),
            "exposes no LoRA files",
        ),
    )
    record(
        "generic LoraLoader alone is rejected",
        lambda: _expect_error(
            lambda: _build_with_rows(_request("img2vid"), standard, synthetic_h3_object_info(model_only=False, generic_lora=True)),
            "does not expose LoraLoaderModelOnly",
        ),
    )
    record(
        "H3 GGUF LoRA remains fail closed",
        lambda: _expect_error(
            lambda: _build_with_rows(_request("img2vid", loader="gguf"), standard, object_info),
            "GGUF Video LoRA/Turbo remains fail-closed",
        ),
    )
    record(
        "H3 high/low target is rejected",
        lambda: _expect_error(
            lambda: _build_with_rows(
                _request("img2vid"),
                [{"name": STANDARD_LORA, "strength_model": 1.0, "role": "standard", "target": "high"}],
                object_info,
            ),
            "supports only target='all'",
        ),
    )
    record(
        "legacy Turbo missing selected file fails closed",
        lambda: _expect_error(
            lambda: h3.build_minimax_h3_workflow(
                _request("img2vid", turbo=True, turbo_name="missing_turbo.safetensors"),
                object_info=object_info,
            ),
            "not visible in the live LoraLoaderModelOnly catalog",
        ),
    )

    failed = [case for case in cases if not case.get("ok")]
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "family": "minimax_h3",
        "modes": list(MODES),
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "cases": cases,
        "next_phase_allowed": not failed,
        "run_command": "python -m neo_app.video.minimax_h3_lora_regression",
    }


def main() -> int:
    report = run_minimax_h3_lora_regression()
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
