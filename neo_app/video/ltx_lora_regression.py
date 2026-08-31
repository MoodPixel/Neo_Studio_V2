from __future__ import annotations

import json
from typing import Any, Callable

from neo_app.video import ltx_img2vid_compiler as img2vid
from neo_app.video import ltx_lora_integration as integration
from neo_app.video import ltx_txt2vid_compiler as txt2vid

SCHEMA_VERSION = "neo.video.ltx23.lora_regression.v1"
PHASE = "phase_7"
MODES = ("txt2vid", "img2vid")
STANDARD_LORA = "ltx23_cinematic_motion.safetensors"
STANDARD_LORA_2 = "ltx23_camera_detail.safetensors"
SPEED_LORA = "ltx23_lightning_4steps.safetensors"
FIXED_SEED = 975318642


def _combo(values: list[str]) -> list[Any]:
    return [values, {}]


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def synthetic_ltx_object_info(
    *,
    model_only: bool = True,
    generic_lora: bool = False,
    loras: list[str] | None = None,
) -> dict[str, Any]:
    lora_values = list(loras if loras is not None else [STANDARD_LORA, STANDARD_LORA_2, SPEED_LORA])
    info: dict[str, Any] = {
        "UNETLoader": {
            "input": {
                "required": {
                    "unet_name": _combo(["ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors"]),
                    "weight_dtype": _combo(["default"]),
                }
            }
        },
        "UnetLoaderGGUF": {
            "input": {
                "required": {
                    "unet_name": _combo(["ltx-2.3-22b-distilled-1.1-Q5_K_M.gguf"]),
                }
            }
        },
        "DualCLIPLoader": {
            "input": {
                "required": {
                    "clip_name1": _combo(["gemma_3_12B_it_fp8_e4m3fn.safetensors"]),
                    "clip_name2": _combo(["ltx-2.3_text_projection_bf16.safetensors"]),
                    "type": _combo(["ltxv"]),
                }
            }
        },
        "DualCLIPLoaderGGUF": {
            "input": {
                "required": {
                    "clip_name1": _combo(["gemma-3-12b-it-IQ4_XS.gguf"]),
                    "clip_name2": _combo(["ltx-2.3_text_projection_bf16.safetensors"]),
                    "type": _combo(["ltxv"]),
                }
            }
        },
        "VAELoader": {
            "input": {
                "required": {
                    "vae_name": _combo(["LTX23_video_vae_bf16.safetensors"]),
                }
            }
        },
        "LTXVChunkFeedForward": {
            "input": {
                "required": {
                    "model": ["MODEL", {}],
                    "chunks": ["INT", {"default": 2}],
                    "dim_threshold": ["INT", {"default": 4096}],
                }
            }
        },
        "EmptyLTXVLatentVideo": {
            "input": {
                "required": {
                    "width": ["INT", {}],
                    "height": ["INT", {}],
                    "length": ["INT", {}],
                    "batch_size": ["INT", {"default": 1}],
                }
            }
        },
        "LTXVConditioning": {"input": {"required": {}}},
        "LTXVScheduler": {"input": {"required": {}}},
        "SamplerCustomAdvanced": {"input": {"required": {}}},
        "RandomNoise": {"input": {"required": {}}},
        "CFGGuider": {"input": {"required": {}}},
        "KSamplerSelect": {"input": {"required": {}}},
        "VAEDecodeTiled": {"input": {"required": {}}},
        "SaveWEBM": {"input": {"required": {}}},
        "LoadImage": {"input": {"required": {"image": _combo(["phase7_source.png"])}}},
        "LTXVAddGuide": {"input": {"required": {}}},
        "LTXVCropGuides": {"input": {"required": {}}},
    }
    if model_only:
        info[integration.LTX_MODEL_ONLY_LOADER] = {
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


def _request(mode: str, *, loader: str = "unet") -> Any:
    if mode == "txt2vid":
        return txt2vid.LtxVideoCompileRequest(
            loader=loader,
            generation_type="txt2vid",
            prompt="Phase 7 LTX Video LoRA regression shot.",
            width=768,
            height=512,
            frames=25,
            fps=24,
            steps=8,
            guidance=1.0,
            seed=FIXED_SEED,
        )
    if mode == "img2vid":
        return img2vid.LtxImg2VidCompileRequest(
            loader=loader,
            generation_type="img2vid",
            prompt="Animate the Phase 7 LTX source image with smooth motion.",
            source_image="phase7_source.png",
            source_image_name="phase7_source.png",
            image_strength=0.7,
            width=768,
            height=512,
            frames=25,
            fps=24,
            steps=8,
            guidance=1.0,
            seed=FIXED_SEED,
        )
    raise ValueError(f"Unsupported Phase 7 regression mode: {mode}")


def _extension_payload(req: Any, rows: list[dict[str, Any]], *, enabled: bool = True) -> dict[str, Any]:
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


def _build(
    mode: str,
    rows: list[dict[str, Any]],
    object_info: dict[str, Any],
    *,
    enabled: bool = True,
    loader: str = "unet",
) -> dict[str, Any]:
    req = _request(mode, loader=loader)
    token = integration._PHASE7_PAYLOAD.set(_extension_payload(req, rows, enabled=enabled))
    try:
        if mode == "txt2vid":
            return txt2vid.build_ltx23_txt2vid_workflow(req, object_info=object_info)
        return img2vid.build_ltx23_img2vid_workflow(req, object_info=object_info)
    finally:
        integration._PHASE7_PAYLOAD.reset(token)


def _baseline(mode: str, object_info: dict[str, Any], *, loader: str = "unet") -> dict[str, Any]:
    req = _request(mode, loader=loader)
    if mode == "txt2vid":
        return txt2vid.build_ltx23_txt2vid_workflow(req, object_info=object_info)
    return img2vid.build_ltx23_img2vid_workflow(req, object_info=object_info)


def _lora_nodes(compiled: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    return [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and str(node.get("class_type") or "") == integration.LTX_MODEL_ONLY_LOADER
    ]


def _chunk_node(compiled: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    workflow = compiled.get("workflow") if isinstance(compiled.get("workflow"), dict) else {}
    bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
    classes = bindings.get("classes") if isinstance(bindings.get("classes"), dict) else {}
    chunk_class = str(classes.get("chunk_node") or "")
    rows = [
        (str(node_id), node)
        for node_id, node in workflow.items()
        if isinstance(node, dict) and str(node.get("class_type") or "") == chunk_class
    ]
    _assert(len(rows) == 1, f"Expected exactly one LTX chunk model node, found {len(rows)}")
    return rows[0]


def _assert_profile_and_chain(compiled: dict[str, Any], expected_names: list[str]) -> None:
    runtime = compiled.get("video_lora_stack") or {}
    profile = compiled.get("lora_patch_profile") or {}
    workflow = compiled.get("workflow") or {}
    prompt_workflow = (compiled.get("prompt_api_payload") or {}).get("prompt")
    _assert(prompt_workflow == workflow, "prompt_api_payload.prompt does not match the patched LTX workflow")
    _assert(profile.get("schema_version") == "neo.video.lora_patch_profile.v1", "Missing/invalid LTX LoRA patch-profile schema")
    _assert(profile.get("owner") == "compiler", "LTX LoRA patch profile is not compiler-owned")
    _assert(profile.get("loader_type") == "model_only", "LTX LoRA patch profile is not model_only")
    _assert(profile.get("loader_node_class") == integration.LTX_MODEL_ONLY_LOADER, "LTX LoRA profile is not locked to LoraLoaderModelOnly")
    _assert(profile.get("targets") == ["all"], "LTX LoRA profile exposes an invalid target")
    _assert(bool(profile.get("validated")), "LTX UNET LoRA patch profile is not validated")

    nodes = _lora_nodes(compiled)
    names = [str(node.get("inputs", {}).get("lora_name") or "") for _, node in nodes]
    _assert(names == expected_names, f"LTX LoRA node order mismatch: expected {expected_names}, got {names}")
    roles = [str(item.get("role") or "") for item in runtime.get("applied", [])]
    _assert(roles == ["standard"] * len(expected_names), f"LTX runtime roles are not standard-only: {roles}")
    _assert(runtime.get("applied_count") == len(expected_names), "LTX applied_count does not match inserted nodes")
    _assert(runtime.get("standard_count") == len(expected_names) and runtime.get("speed_count") == 0, "LTX role counts are incorrect")
    _assert(bool(runtime.get("live_catalog_validated")), "Active LTX LoRA stack did not validate the live ModelOnly catalog")

    _, chunk = _chunk_node(compiled)
    final_ref = runtime.get("final_model_ref")
    _assert(chunk.get("inputs", {}).get("model") == final_ref, "LTXVChunkFeedForward does not consume the final Video LoRA model reference")

    json.dumps({"workflow": workflow, "profile": profile, "runtime": runtime})


def _case_no_lora_equivalence(mode: str, object_info: dict[str, Any], *, disabled_with_rows: bool = False) -> dict[str, Any]:
    baseline = _baseline(mode, object_info)
    rows = [{"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"}] if disabled_with_rows else []
    current = _build(mode, rows, object_info, enabled=not disabled_with_rows)
    _assert(current.get("workflow") == baseline.get("workflow"), f"No-op LTX LoRA workflow changed for {mode}")
    _assert(not _lora_nodes(current), f"No-op LTX workflow unexpectedly contains LoRA nodes for {mode}")
    runtime = current.get("video_lora_stack") or {}
    _assert(not runtime.get("active") and runtime.get("applied_count") == 0, f"No-op LTX runtime is not inert for {mode}")
    return {"mode": mode, "disabled_with_rows": disabled_with_rows, "workflow_nodes": len(current.get("workflow") or {})}


def _case_stack(mode: str, object_info: dict[str, Any], rows: list[dict[str, Any]], expected_names: list[str]) -> dict[str, Any]:
    compiled = _build(mode, rows, object_info)
    _assert_profile_and_chain(compiled, expected_names)
    return {
        "mode": mode,
        "applied": expected_names,
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


def run_regression() -> dict[str, Any]:
    object_info = synthetic_ltx_object_info()
    cases: list[dict[str, Any]] = []

    def record(name: str, fn: Callable[[], Any]) -> None:
        try:
            details = fn()
            cases.append({"name": name, "ok": True, "details": details})
        except Exception as exc:  # noqa: BLE001 - regression output must retain all failures.
            cases.append({"name": name, "ok": False, "details": f"{type(exc).__name__}: {exc}"})

    for mode in MODES:
        record(f"{mode}: empty stack graph equivalence", lambda mode=mode: _case_no_lora_equivalence(mode, object_info))
        record(
            f"{mode}: disabled populated stack graph equivalence",
            lambda mode=mode: _case_no_lora_equivalence(mode, object_info, disabled_with_rows=True),
        )
        record(
            f"{mode}: standard LoRA",
            lambda mode=mode: _case_stack(
                mode,
                object_info,
                [{"name": STANDARD_LORA, "strength_model": 0.85, "strength_clip": 0.5, "role": "standard", "target": "all"}],
                [STANDARD_LORA],
            ),
        )
        record(
            f"{mode}: multiple standard LoRAs",
            lambda mode=mode: _case_stack(
                mode,
                object_info,
                [
                    {"name": STANDARD_LORA, "strength_model": 0.8, "role": "standard", "target": "all"},
                    {"name": STANDARD_LORA_2, "strength_model": 0.65, "role": "standard", "target": "all"},
                ],
                [STANDARD_LORA, STANDARD_LORA_2],
            ),
        )

    for mode in MODES:
        record(
            f"{mode}: speed role remains fail closed",
            lambda mode=mode: _expect_error(
                lambda: _build(
                    mode,
                    [{"name": SPEED_LORA, "strength_model": 1.0, "role": "speed", "target": "all"}],
                    object_info,
                ),
                "standard Video LoRAs only",
            ),
        )
        record(
            f"{mode}: high target remains fail closed",
            lambda mode=mode: _expect_error(
                lambda: _build(
                    mode,
                    [{"name": STANDARD_LORA, "strength_model": 1.0, "role": "standard", "target": "high"}],
                    object_info,
                ),
                "target='all'",
            ),
        )

    record(
        "missing selected LoRA fails closed",
        lambda: _expect_error(
            lambda: _build(
                "txt2vid",
                [{"name": "missing_ltx_lora.safetensors", "strength_model": 1.0, "role": "standard", "target": "all"}],
                object_info,
            ),
            "not visible in the live LoraLoaderModelOnly catalog",
        ),
    )
    record(
        "empty LoraLoaderModelOnly catalog fails closed",
        lambda: _expect_error(
            lambda: _build(
                "txt2vid",
                [{"name": STANDARD_LORA, "strength_model": 1.0, "role": "standard", "target": "all"}],
                synthetic_ltx_object_info(loras=[]),
            ),
            "exposes no LoRA files",
        ),
    )
    record(
        "generic LoraLoader alone is rejected",
        lambda: _expect_error(
            lambda: _build(
                "txt2vid",
                [{"name": STANDARD_LORA, "strength_model": 1.0, "role": "standard", "target": "all"}],
                synthetic_ltx_object_info(model_only=False, generic_lora=True),
            ),
            "does not expose LoraLoaderModelOnly",
        ),
    )
    for mode in MODES:
        record(
            f"{mode}: LTX GGUF LoRA remains fail closed",
            lambda mode=mode: _expect_error(
                lambda: _build(
                    mode,
                    [{"name": STANDARD_LORA, "strength_model": 1.0, "role": "standard", "target": "all"}],
                    object_info,
                    loader="gguf",
                ),
                "GGUF Video LoRA remains fail-closed",
            ),
        )

    failed = [case for case in cases if not case["ok"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "family": "ltx23",
        "modes": list(MODES),
        "ok": not failed,
        "gate": "pass" if not failed else "fail",
        "case_count": len(cases),
        "passed": len(cases) - len(failed),
        "failed": len(failed),
        "cases": cases,
        "next_phase_allowed": not failed,
        "run_command": "python -m neo_app.video.ltx_lora_regression",
    }


def main() -> int:
    report = run_regression()
    print(json.dumps(report, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
