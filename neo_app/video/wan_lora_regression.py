from __future__ import annotations

import json
from typing import Any, Callable

from neo_app.video import wan_gguf_i2v14_compiler as dual
from neo_app.video import wan_lora_integration as integration
from neo_app.video import wan_txt2vid_compiler as single

SCHEMA_VERSION = "neo.video.wan22.lora_regression.v1"
PHASE = "phase_8"
STANDARD_LORA = "wan22_cinematic_motion.safetensors"
STANDARD_LORA_2 = "wan22_camera_detail.safetensors"
SPEED_LORA = "wan22_lightx2v_general_4step.safetensors"
LEGACY_HIGH = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
LEGACY_LOW = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
FIXED_SEED = 420824


def _combo(values: list[str]) -> list[Any]:
    return [values, {}]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _lora_loader(loras: list[str]) -> dict[str, Any]:
    return {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "lora_name": _combo(loras),
                "strength_model": ["FLOAT", {"default": 1.0}],
            }
        }
    }


def synthetic_single_object_info(*, model_only: bool = True, generic_lora: bool = False, loras: list[str] | None = None) -> dict[str, Any]:
    lora_values = list(loras if loras is not None else [STANDARD_LORA, STANDARD_LORA_2, SPEED_LORA])
    info: dict[str, Any] = {
        "UNETLoader": {"input": {"required": {"unet_name": _combo(["wan2.2_ti2v_5B_fp16.safetensors"]), "weight_dtype": _combo(["default"])}}},
        "CLIPLoader": {"input": {"required": {"clip_name": _combo(["umt5_xxl_fp8_e4m3fn_scaled.safetensors"]), "type": _combo(["wan"]), "device": _combo(["default"])}}},
        "VAELoader": {"input": {"required": {"vae_name": _combo(["wan2.2_vae.safetensors"])}}},
        "Wan22ImageToVideoLatent": {"input": {"required": {"vae": ["VAE", {}], "width": ["INT", {}], "height": ["INT", {}], "length": ["INT", {}], "batch_size": ["INT", {}]}}},
        "LoadImage": {"input": {"required": {"image": ["STRING", {}]}}},
        "ModelSamplingSD3": {"input": {"required": {"model": ["MODEL", {}], "shift": ["FLOAT", {"default": 8.0}]}}},
        "KSampler": {"input": {"required": {}}},
        "VAEDecode": {"input": {"required": {}}},
        "SaveWEBM": {"input": {"required": {}}},
    }
    if model_only:
        info[integration.WAN_MODEL_ONLY_LOADER] = _lora_loader(lora_values)
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


def synthetic_dual_object_info(*, model_only: bool = True, generic_lora: bool = False, loras: list[str] | None = None) -> dict[str, Any]:
    lora_values = list(loras if loras is not None else [STANDARD_LORA, STANDARD_LORA_2, SPEED_LORA, LEGACY_HIGH, LEGACY_LOW])
    info: dict[str, Any] = {
        "UnetLoaderGGUF": {
            "input": {
                "required": {
                    "unet_name": _combo(["wan2.2_i2v_high_noise_14B_Q4_K_M.gguf", "wan2.2_i2v_low_noise_14B_Q4_K_M.gguf"]),
                }
            }
        },
        "CLIPLoader": {"input": {"required": {"clip_name": _combo(["umt5_xxl_fp8_e4m3fn_scaled.safetensors"]), "type": _combo(["wan"])}}},
        "VAELoader": {"input": {"required": {"vae_name": _combo(["wan_2.1_vae.safetensors"])}}},
        "VAEDecodeTiled": {"input": {"required": {}}},
    }
    if model_only:
        info[integration.WAN_MODEL_ONLY_LOADER] = _lora_loader(lora_values)
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


def _extension_payload(base: dict[str, Any], rows: list[dict[str, Any]], *, enabled: bool = True) -> dict[str, Any]:
    payload = dict(base)
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


def _single_request(mode: str) -> single.VideoCompileRequest:
    return single.VideoCompileRequest(
        loader="unet",
        generation_type=mode,
        prompt="Phase 8 WAN single-model LoRA regression shot.",
        source_image="phase8_source.png" if mode == "img2vid" else None,
        source_image_name="phase8_source.png" if mode == "img2vid" else None,
        seed=FIXED_SEED,
        steps=12,
        guidance=4.0,
    )


def _dual_request(**kwargs: Any) -> dual.WanGgufI2V14CompileRequest:
    values: dict[str, Any] = {
        "source_image": "phase8_dual_source.png",
        "source_image_name": "phase8_dual_source.png",
        "seed": FIXED_SEED,
        "steps": 8,
        "guidance": 3.0,
        "split_step": 4,
        "enable_sage_attention": False,
        "enable_teacache": False,
        "enable_cpu_offload": False,
        "enable_vae_offload": False,
        "enable_block_swap": False,
        "preserve_user_overrides": True,
    }
    values.update(kwargs)
    return dual.WanGgufI2V14CompileRequest(**values)


def _build_single(mode: str, rows: list[dict[str, Any]], info: dict[str, Any], *, enabled: bool = True) -> dict[str, Any]:
    req = _single_request(mode)
    token = integration._PHASE8_PAYLOAD.set(_extension_payload(req.payload(), rows, enabled=enabled))
    try:
        return single.build_wan22_txt2vid_workflow(req, object_info=info)
    finally:
        integration._PHASE8_PAYLOAD.reset(token)


def _build_dual(rows: list[dict[str, Any]], info: dict[str, Any], *, enabled: bool = True, **legacy: Any) -> dict[str, Any]:
    req = _dual_request(**legacy)
    token = integration._PHASE8_PAYLOAD.set(_extension_payload(req.payload(), rows, enabled=enabled))
    try:
        return dual.build_wan22_gguf_i2v14_workflow(req, object_info=info)
    finally:
        integration._PHASE8_PAYLOAD.reset(token)


def _lora_nodes(compiled: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    return [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and str(node.get("class_type") or "") == integration.WAN_MODEL_ONLY_LOADER
    ]


def _expect_error(fn: Callable[[], Any], contains: str) -> str:
    try:
        fn()
    except ValueError as exc:
        text = str(exc)
        _assert(contains.casefold() in text.casefold(), f"Expected error containing {contains!r}, got {text!r}")
        return text
    raise AssertionError(f"Expected ValueError containing {contains!r}")


def _assert_single_chain(compiled: dict[str, Any], expected_names: list[str]) -> None:
    profile = compiled.get("lora_patch_profile") or {}
    runtime = compiled.get("video_lora_stack") or {}
    workflow = compiled.get("workflow") or {}
    _assert(profile.get("owner") == "compiler", "WAN single profile is not compiler-owned")
    _assert(profile.get("loader_type") == "model_only" and profile.get("targets") == ["all"], "WAN single profile contract mismatch")
    _assert(bool(profile.get("validated")), "WAN single profile is not validated")
    names = [str(node.get("inputs", {}).get("lora_name") or "") for _, node in _lora_nodes(compiled)]
    _assert(names == expected_names, f"WAN single LoRA order mismatch: {names}")
    _assert(runtime.get("applied_count") == len(expected_names), "WAN single applied_count mismatch")
    branch = profile["branches"][0]
    for consumer in branch["model_consumers"]:
        node = workflow[str(consumer["node_id"])]
        _assert(node["inputs"][str(consumer["input"])] == runtime.get("final_model_ref"), "WAN single final model ref was not rewired")
    _assert((compiled.get("prompt_api_payload") or {}).get("prompt") == workflow, "WAN single prompt graph is stale")
    json.dumps({"profile": profile, "runtime": runtime, "workflow": workflow})


def _assert_dual_chain(compiled: dict[str, Any], expected_rows: int, expected_nodes: int) -> None:
    profile = compiled.get("lora_patch_profile") or {}
    runtime = compiled.get("video_lora_stack") or {}
    workflow = compiled.get("workflow") or {}
    _assert(profile.get("owner") == "compiler", "WAN dual profile is not compiler-owned")
    _assert(profile.get("loader_type") == "model_only_multi_branch", "WAN dual profile loader type mismatch")
    _assert(profile.get("targets") == ["all", "high", "low"] and bool(profile.get("validated")), "WAN dual target/profile validation mismatch")
    _assert(runtime.get("applied_count") == expected_rows, "WAN dual row count mismatch")
    _assert(runtime.get("applied_node_count") == expected_nodes, "WAN dual node count mismatch")
    finals = runtime.get("final_model_refs") or {}
    for branch in profile.get("branches", []):
        target = str(branch.get("target") or "")
        for consumer in branch.get("model_consumers", []):
            node = workflow[str(consumer["node_id"])]
            _assert(node["inputs"][str(consumer["input"])] == finals[target], f"WAN {target} final model ref was not rewired")
    _assert(not any(node_id in workflow for node_id in ("129:101", "129:102", "9001", "9002")), "Legacy hardcoded WAN LoRA node ids survived migration")
    _assert((compiled.get("prompt_api_payload") or {}).get("prompt") == workflow, "WAN dual prompt graph is stale")
    json.dumps({"profile": profile, "runtime": runtime, "workflow": workflow})


def _case_single_noop(mode: str, info: dict[str, Any], *, disabled: bool = False) -> dict[str, Any]:
    req = _single_request(mode)
    original = single._neo_phase8_video_lora_original_build(req, object_info=info)
    rows = [{"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}] if disabled else []
    current = _build_single(mode, rows, info, enabled=not disabled)
    _assert(current.get("workflow") == original.get("workflow"), f"WAN {mode} no-op workflow changed")
    _assert(not _lora_nodes(current), f"WAN {mode} no-op unexpectedly contains LoRA nodes")
    return {"mode": mode, "disabled": disabled, "workflow_nodes": len(current.get("workflow") or {})}


def _case_dual_noop(info: dict[str, Any], *, disabled: bool = False) -> dict[str, Any]:
    req = _dual_request()
    original = dual._neo_phase8_video_lora_original_build(req, object_info=info)
    rows = [{"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}] if disabled else []
    current = _build_dual(rows, info, enabled=not disabled)
    _assert(current.get("workflow") == original.get("workflow"), "WAN dual no-op workflow changed")
    _assert(not _lora_nodes(current), "WAN dual no-op unexpectedly contains LoRA nodes")
    return {"disabled": disabled, "workflow_nodes": len(current.get("workflow") or {})}


def run_phase8_gate() -> dict[str, Any]:
    single_info = synthetic_single_object_info()
    dual_info = synthetic_dual_object_info()
    cases: list[dict[str, Any]] = []

    def run(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
            cases.append({"name": name, "ok": True, "details": details})
        except Exception as exc:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "error": f"{exc.__class__.__name__}: {exc}"})

    for mode in ("txt2vid", "img2vid"):
        run(f"{mode}: empty stack graph equivalence", lambda mode=mode: _case_single_noop(mode, single_info))
        run(f"{mode}: disabled populated stack graph equivalence", lambda mode=mode: _case_single_noop(mode, single_info, disabled=True))
        run(
            f"{mode}: standard LoRA",
            lambda mode=mode: (
                lambda compiled: (_assert_single_chain(compiled, [STANDARD_LORA]), {"mode": mode, "applied": [STANDARD_LORA]})[1]
            )(_build_single(mode, [{"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}], single_info)),
        )
        run(
            f"{mode}: multiple standard LoRAs",
            lambda mode=mode: (
                lambda compiled: (_assert_single_chain(compiled, [STANDARD_LORA, STANDARD_LORA_2]), {"mode": mode, "applied": [STANDARD_LORA, STANDARD_LORA_2]})[1]
            )(_build_single(mode, [
                {"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"},
                {"name": STANDARD_LORA_2, "strength_model": 0.6, "role": "standard", "target": "all"},
            ], single_info)),
        )

    run("WAN UNET speed role remains fail closed", lambda: _expect_error(lambda: _build_single("txt2vid", [{"name": SPEED_LORA, "role": "speed", "target": "all"}], single_info), "standard Video LoRAs only"))
    run("WAN UNET high target remains fail closed", lambda: _expect_error(lambda: _build_single("img2vid", [{"name": STANDARD_LORA, "role": "standard", "target": "high"}], single_info), "target='all'"))
    run("WAN UNET missing selected LoRA fails closed", lambda: _expect_error(lambda: _build_single("txt2vid", [{"name": "missing_wan_lora.safetensors", "target": "all"}], single_info), "not visible"))
    run("WAN UNET empty ModelOnly catalog fails closed", lambda: _expect_error(lambda: _build_single("txt2vid", [{"name": STANDARD_LORA, "target": "all"}], synthetic_single_object_info(loras=[])), "exposes no LoRA files"))
    run("WAN UNET generic LoraLoader alone is rejected", lambda: _expect_error(lambda: _build_single("txt2vid", [{"name": STANDARD_LORA, "target": "all"}], synthetic_single_object_info(model_only=False, generic_lora=True)), "does not expose LoraLoaderModelOnly"))

    run("dual: empty stack graph equivalence", lambda: _case_dual_noop(dual_info))
    run("dual: disabled populated stack graph equivalence", lambda: _case_dual_noop(dual_info, disabled=True))
    for target, nodes in (("all", 2), ("high", 1), ("low", 1)):
        run(
            f"dual: standard LoRA target={target}",
            lambda target=target, nodes=nodes: (
                lambda compiled: (_assert_dual_chain(compiled, 1, nodes), {"target": target, "nodes": nodes})[1]
            )(_build_dual([{"name": STANDARD_LORA, "strength_model": 0.75, "role": "standard", "target": target}], dual_info)),
        )
    run(
        "dual: mixed standard branch stack",
        lambda: (
            lambda compiled: (_assert_dual_chain(compiled, 2, 3), {"applied": ["all", "low"]})[1]
        )(_build_dual([
            {"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"},
            {"name": STANDARD_LORA_2, "strength_model": 0.5, "role": "standard", "target": "low"},
        ], dual_info)),
    )
    run(
        "dual: speed LoRA target=all",
        lambda: (
            lambda compiled: (_assert_dual_chain(compiled, 1, 2), {"speed_count": (compiled.get("video_lora_stack") or {}).get("speed_count")})[1]
        )(_build_dual([{"name": SPEED_LORA, "strength_model": 1.0, "role": "speed", "target": "all"}], dual_info)),
    )
    run(
        "dual: legacy normal Both bridges to universal all",
        lambda: (
            lambda compiled: (_assert_dual_chain(compiled, 1, 2), (compiled.get("video_lora_stack") or {}).get("legacy_bridge"))[1]
        )(_build_dual([], dual_info, enable_video_lora=True, video_lora_mode="normal", video_lora_model=STANDARD_LORA, video_lora_strength=0.7, video_lora_target="both")),
    )
    run(
        "dual: legacy normal High bridges to universal high",
        lambda: (
            lambda compiled: (_assert_dual_chain(compiled, 1, 1), (compiled.get("video_lora_stack") or {}).get("legacy_bridge"))[1]
        )(_build_dual([], dual_info, enable_video_lora=True, video_lora_mode="normal", video_lora_model=STANDARD_LORA, video_lora_strength=0.7, video_lora_target="high")),
    )
    run(
        "dual: legacy LightX2V bridges high/low speed rows",
        lambda: (
            lambda compiled: (
                _assert_dual_chain(compiled, 2, 2),
                _assert((compiled.get("video_lora_stack") or {}).get("speed_count") == 2, "Legacy LightX2V did not become two speed rows"),
                {"parameters": compiled.get("parameters"), "bridge": (compiled.get("video_lora_stack") or {}).get("legacy_bridge")},
            )[2]
        )(_build_dual([], dual_info, enable_lightx2v=True, high_noise_lora=LEGACY_HIGH, low_noise_lora=LEGACY_LOW)),
    )
    run(
        "dual: legacy normal duplicate is branch-deduped",
        lambda: (
            lambda compiled: (
                _assert_dual_chain(compiled, 1, 2),
                _assert((compiled.get("video_lora_stack") or {}).get("legacy_bridge", {}).get("duplicate_branch_suppressed") == 2, "Legacy Both duplicate was not suppressed on both branches"),
                (compiled.get("video_lora_stack") or {}).get("legacy_bridge"),
            )[2]
        )(_build_dual([{"name": STANDARD_LORA, "strength_model": 0.9, "role": "standard", "target": "all"}], dual_info, enable_video_lora=True, video_lora_mode="normal", video_lora_model=STANDARD_LORA, video_lora_target="both")),
    )
    run(
        "dual: legacy LightX2V promotes existing branch role without duplication",
        lambda: (
            lambda compiled: (
                _assert_dual_chain(compiled, 2, 2),
                _assert((compiled.get("video_lora_stack") or {}).get("legacy_bridge", {}).get("existing_role_promoted") == 1, "Existing high branch row was not promoted to speed"),
                (compiled.get("video_lora_stack") or {}).get("legacy_bridge"),
            )[2]
        )(_build_dual([{"name": LEGACY_HIGH, "strength_model": 0.9, "role": "standard", "target": "high"}], dual_info, enable_lightx2v=True, high_noise_lora=LEGACY_HIGH, low_noise_lora=LEGACY_LOW)),
    )
    run("WAN dual missing selected LoRA fails closed", lambda: _expect_error(lambda: _build_dual([{"name": "missing_dual_lora.safetensors", "target": "all"}], dual_info), "not visible"))
    run("WAN dual empty ModelOnly catalog fails closed", lambda: _expect_error(lambda: _build_dual([{"name": STANDARD_LORA, "target": "all"}], synthetic_dual_object_info(loras=[])), "exposes no LoRA files"))
    run("WAN dual generic LoraLoader alone is rejected", lambda: _expect_error(lambda: _build_dual([{"name": STANDARD_LORA, "target": "all"}], synthetic_dual_object_info(model_only=False, generic_lora=True)), "does not expose LoraLoaderModelOnly"))

    run(
        "WAN Txt2Vid Generate preserves outer universal payload",
        lambda: (
            lambda result: (
                _assert(result.get("ok") is True, f"WAN Txt2Vid dry-run Generate failed: {result.get('error')}"),
                _assert((result.get("video_lora_stack") or {}).get("applied_count") == 1, "WAN Txt2Vid Generate lost the outer LoRA extension payload"),
                {"applied_count": (result.get("video_lora_stack") or {}).get("applied_count")},
            )[2]
        )(single.video_wan22_txt2vid_generate_payload(_extension_payload({**_single_request("txt2vid").payload(), "dry_run": True}, [{"name": STANDARD_LORA, "target": "all"}]), object_info_override=single_info)),
    )
    run(
        "WAN dual Generate preserves outer universal payload",
        lambda: (
            lambda result: (
                _assert(result.get("ok") is True, f"WAN dual dry-run Generate failed: {result.get('error')}"),
                _assert((result.get("video_lora_stack") or {}).get("applied_count") == 1, "WAN dual Generate lost the outer LoRA extension payload"),
                {"applied_count": (result.get("video_lora_stack") or {}).get("applied_count")},
            )[2]
        )(dual.video_wan22_gguf_i2v14_generate_payload(_extension_payload({**_dual_request().payload(), "dry_run": True}, [{"name": STANDARD_LORA, "target": "all"}]), object_info_override=dual_info)),
    )

    passed = len([case for case in cases if case.get("ok")])
    failed = len(cases) - passed
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "family": "wan22",
        "routes": ["wan22.unet.txt2vid", "wan22.unet.img2vid", "wan22.gguf.img2vid_14b_dual_noise"],
        "ok": failed == 0,
        "gate": "pass" if failed == 0 else "fail",
        "case_count": len(cases),
        "passed": passed,
        "failed": failed,
        "cases": cases,
        "next_phase_allowed": failed == 0,
        "run_command": "python -m neo_app.video.wan_lora_regression",
    }


def main() -> int:
    result = run_phase8_gate()
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
