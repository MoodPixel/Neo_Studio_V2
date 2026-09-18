from __future__ import annotations

from typing import Any, Callable

from neo_app.video.wan_rapid_aio_lora import apply_rapid_aio_lora_stack, validate_rapid_aio_lora_topology

STANDARD = "wan22_detail_style.safetensors"
SPEED = "wan22_lightx2v_4steps.safetensors"


def _info(*, model_output: bool = True, loader: bool = True, catalog: list[str] | None = None) -> dict[str, Any]:
    info: dict[str, Any] = {
        "WanVideoModelLoaderGGUFAdvanced": {"input": {"required": {"model_name": [["wan22_rapid_aio.gguf"]]}}, "output": ["MODEL"] if model_output else ["LATENT"]},
    }
    if loader:
        info["LoraLoaderModelOnly"] = {
            "input": {"required": {"model": ["MODEL", {}], "lora_name": [list(catalog if catalog is not None else [STANDARD, SPEED])], "strength_model": ["FLOAT", {}]}},
            "output": ["MODEL"],
        }
    return info


def _prompt() -> dict[str, Any]:
    return {
        "1": {"class_type": "WanVideoModelLoaderGGUFAdvanced", "inputs": {"model_name": "wan22_rapid_aio.gguf"}},
        "7": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["1", 0], "shift": 5.0}},
    }


def _expect_error(fn: Callable[[], Any], text: str) -> None:
    try:
        fn()
    except ValueError as exc:
        assert text.casefold() in str(exc).casefold(), exc
        return
    raise AssertionError("Expected ValueError")


def run_phase16_gate() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    def run(name: str, fn: Callable[[], Any]) -> None:
        try:
            fn(); cases.append({"name": name, "ok": True})
        except Exception as exc:  # noqa: BLE001
            cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    for mode in ("txt2vid", "img2vid"):
        route = f"wan22.rapid_aio_gguf.{mode}"
        run(f"{mode}: live topology", lambda route=route: validate_rapid_aio_lora_topology(route, _info(), "WanVideoModelLoaderGGUFAdvanced"))
        run(f"{mode}: standard LoRA", lambda route=route: (
            lambda value: (assert_chain(value, [STANDARD]), value)[1]
        )(apply_rapid_aio_lora_stack(_prompt(), ["1", 0], [{"name": STANDARD, "role": "standard", "target": "all"}], route_id=route, object_info=_info(), model_loader_class="WanVideoModelLoaderGGUFAdvanced")))
        run(f"{mode}: speed LoRA", lambda route=route: (
            lambda value: (assert_chain(value, [SPEED]), value)[1]
        )(apply_rapid_aio_lora_stack(_prompt(), ["1", 0], [{"name": SPEED, "role": "speed", "target": "all"}], route_id=route, object_info=_info(), model_loader_class="WanVideoModelLoaderGGUFAdvanced")))
    run("standard rows precede speed rows", lambda: assert_chain(apply_rapid_aio_lora_stack(_prompt(), ["1", 0], [{"name": SPEED, "role": "speed"}, {"name": STANDARD, "role": "standard"}], route_id="wan22.rapid_aio_gguf.img2vid", object_info=_info(), model_loader_class="WanVideoModelLoaderGGUFAdvanced"), [STANDARD, SPEED]))
    run("high target fails closed", lambda: _expect_error(lambda: apply_rapid_aio_lora_stack(_prompt(), ["1", 0], [{"name": STANDARD, "target": "high"}], route_id="wan22.rapid_aio_gguf.img2vid", object_info=_info(), model_loader_class="WanVideoModelLoaderGGUFAdvanced"), "target='all'"))
    run("missing MODEL output fails closed", lambda: _expect_error(lambda: validate_rapid_aio_lora_topology("wan22.rapid_aio_gguf.txt2vid", _info(model_output=False), "WanVideoModelLoaderGGUFAdvanced"), "MODEL output"))
    run("generic fallback fails closed", lambda: _expect_error(lambda: validate_rapid_aio_lora_topology("wan22.rapid_aio_gguf.txt2vid", _info(loader=False), "WanVideoModelLoaderGGUFAdvanced"), "requires LoraLoaderModelOnly"))
    run("missing file fails closed", lambda: _expect_error(lambda: apply_rapid_aio_lora_stack(_prompt(), ["1", 0], [{"name": "missing.safetensors"}], route_id="wan22.rapid_aio_gguf.img2vid", object_info=_info(), model_loader_class="WanVideoModelLoaderGGUFAdvanced"), "not visible"))
    failed = len([case for case in cases if not case["ok"]])
    return {"schema_version": "neo.video.wan22.rapid_aio_lora_regression.v1", "phase": "phase_16", "ok": failed == 0, "gate": "pass" if failed == 0 else "fail", "case_count": len(cases), "passed": len(cases) - failed, "failed": failed, "routes": sorted(["wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"]), "gpu_inference_proven": False, "cases": cases}


def assert_chain(value: tuple[dict[str, Any], dict[str, Any]], names: list[str]) -> None:
    prompt, runtime = value
    actual = [row["name"] for row in runtime["applied"]]
    assert actual == names, actual
    assert prompt["7"]["inputs"]["model"] == runtime["final_model_ref"]
    assert runtime["applied_count"] == len(names)


if __name__ == "__main__":
    import json
    report = run_phase16_gate()
    print(json.dumps(report, indent=2))
    raise SystemExit(0 if report["ok"] else 1)
