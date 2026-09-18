from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable

from neo_app.video.native_workflow_lora_contract import apply_native_workflow_lora_stack, build_native_workflow_lora_contract, canonical_graph_digest, validate_native_workflow_lora_contract

NORMAL = "cinematic_style.safetensors"
SPEED = "wan_lightx2v_4steps.safetensors"


def graph() -> dict[str, Any]:
    return {"alpha": {"class_type": "UnetLoaderGGUF", "inputs": {"unet_name": "model.gguf"}}, "sampler": {"class_type": "ModelSamplingSD3", "inputs": {"model": ["alpha", 0], "shift": 5.0}}}


def info() -> dict[str, Any]:
    return {"LoraLoaderModelOnly": {"input": {"required": {"model": ["MODEL", {}], "lora_name": [[NORMAL, SPEED]], "strength_model": ["FLOAT", {}]}}, "output": ["MODEL"]}}


def contract(route: str, value: dict[str, Any] | None = None) -> dict[str, Any]:
    value = value or graph()
    return build_native_workflow_lora_contract(value, route_id=route, model_ref=["alpha", 0], model_consumers=[{"node_id": "sampler", "input": "model"}])


def expect_error(fn: Callable[[], Any], contains: str) -> None:
    try: fn()
    except ValueError as exc:
        assert contains.casefold() in str(exc).casefold(), exc; return
    raise AssertionError("Expected ValueError")


def run_phase17_gate() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    def run(name: str, fn: Callable[[], Any]) -> None:
        try: fn(); cases.append({"name": name, "ok": True})
        except Exception as exc: cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})
    routes = ["wan22.native_workflow.txt2vid", "wan22.native_workflow.img2vid", "ltx23.native_workflow.txt2vid", "ltx23.native_workflow.img2vid"]
    for route in routes:
        run(f"{route}: contract validates", lambda route=route: validate_native_workflow_lora_contract(graph(), contract(route), info()))
        run(f"{route}: standard applies", lambda route=route: assert_chain(apply_native_workflow_lora_stack(graph(), contract(route), [{"name": NORMAL, "role": "standard", "target": "all"}], info()), [NORMAL]))
    run("WAN speed applies", lambda: assert_chain(apply_native_workflow_lora_stack(graph(), contract("wan22.native_workflow.img2vid"), [{"name": SPEED, "role": "speed"}], info()), [SPEED]))
    run("LTX speed blocked", lambda: expect_error(lambda: apply_native_workflow_lora_stack(graph(), contract("ltx23.native_workflow.img2vid"), [{"name": SPEED, "role": "speed"}], info()), "role blocked"))
    run("graph tamper rejected", lambda: (lambda g: expect_error(lambda: validate_native_workflow_lora_contract(g, contract("wan22.native_workflow.txt2vid"), info()), "digest"))({**graph(), "extra": {"class_type": "PreviewImage", "inputs": {}}}))
    run("bad consumer rejected", lambda: (lambda c: (c["anchors"][0].update({"model_consumers": [{"node_id": "missing", "input": "model"}]}), expect_error(lambda: validate_native_workflow_lora_contract(graph(), c, info()), "consumer")))(contract("wan22.native_workflow.txt2vid")))
    run("preexisting LoRA authority rejected", lambda: (lambda g: expect_error(lambda: validate_native_workflow_lora_contract(g, build_native_workflow_lora_contract(g, route_id="wan22.native_workflow.txt2vid", model_ref=["alpha", 0], model_consumers=[{"node_id": "sampler", "input": "model"}]), info()), "already contains"))({**graph(), "old_lora": {"class_type": "LoraLoaderModelOnly", "inputs": {}}}))
    run("missing live loader rejected", lambda: expect_error(lambda: validate_native_workflow_lora_contract(graph(), contract("wan22.native_workflow.txt2vid"), {}), "live LoraLoaderModelOnly"))
    run("missing catalog file rejected", lambda: expect_error(lambda: apply_native_workflow_lora_stack(graph(), contract("wan22.native_workflow.txt2vid"), [{"name": "missing.safetensors"}], info()), "missing from"))
    run("canonical digest deterministic", lambda: (_ for _ in ()).throw(AssertionError()) if canonical_graph_digest(graph()) != canonical_graph_digest(deepcopy(graph())) else None)
    failed = sum(not item["ok"] for item in cases)
    return {"schema_version": "neo.video.native_workflow_lora_contract_regression.v1", "phase": "phase_17", "ok": failed == 0, "gate": "pass" if failed == 0 else "fail", "case_count": len(cases), "passed": len(cases)-failed, "failed": failed, "routes": routes, "runtime_routes_promoted": 0, "importer_integration_present": False, "gpu_inference_proven": False, "cases": cases}


def assert_chain(value: tuple[dict[str, Any], dict[str, Any]], names: list[str]) -> None:
    workflow, runtime = value
    assert [row["name"] for row in runtime["applied"]] == names
    assert workflow["sampler"]["inputs"]["model"] == runtime["final_model_ref"]


if __name__ == "__main__":
    import json
    result = run_phase17_gate(); print(json.dumps(result, indent=2)); raise SystemExit(0 if result["ok"] else 1)
