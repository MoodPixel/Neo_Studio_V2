from __future__ import annotations

from copy import deepcopy
import tempfile
from pathlib import Path
from typing import Any, Callable

from neo_app.video.physical_gpu_validation import PLAN_SCHEMA, output_candidates, poll_history, run_case, run_plan, validate_plan


def plan() -> dict[str, Any]:
    return {"schema_version": PLAN_SCHEMA, "plan_id": "test", "cases": [{"id": "wan_no_lora", "route_id": "wan22.unet.txt2vid", "payload": {"dry_run": False}, "expected": {"output_required": True, "applied_lora_count": 0}}]}


def expect_error(fn: Callable[[], Any], contains: str) -> None:
    try: fn()
    except ValueError as exc:
        assert contains.casefold() in str(exc).casefold(), exc; return
    raise AssertionError("Expected ValueError")


def run_phase18_gate() -> dict[str, Any]:
    cases: list[dict[str, Any]] = []
    def run(name: str, fn: Callable[[], Any]) -> None:
        try: fn(); cases.append({"name": name, "ok": True})
        except Exception as exc: cases.append({"name": name, "ok": False, "error": f"{type(exc).__name__}: {exc}"})

    run("valid explicit physical plan", lambda: validate_plan(plan()))
    run("dry-run plan rejected", lambda: (lambda p: (p["cases"][0]["payload"].update({"dry_run": True}), expect_error(lambda: validate_plan(p), "dry_run=false")))(deepcopy(plan())))
    run("unknown route rejected", lambda: (lambda p: (p["cases"][0].update({"route_id": "unknown.route"}), expect_error(lambda: validate_plan(p), "no registered generator")))(deepcopy(plan())))
    run("duplicate case id rejected", lambda: (lambda p: (p["cases"].append(deepcopy(p["cases"][0])), expect_error(lambda: validate_plan(p), "duplicate")))(deepcopy(plan())))
    run("history output extraction", lambda: (_ for _ in ()).throw(AssertionError()) if len(output_candidates({"outputs": {"9": {"videos": [{"filename": "out.mp4", "subfolder": "video"}]}}})) != 1 else None)

    ticks = iter([0.0, 0.0, 1.0])
    complete_getter = lambda base, endpoint, timeout: {"pid": {"outputs": {"9": {"videos": [{"filename": "out.mp4"}]}}, "status": {"status_str": "success"}}}
    run("history completed proof", lambda: (_ for _ in ()).throw(AssertionError()) if poll_history("http://local", "pid", timeout_seconds=10, interval_seconds=0, request_timeout=1, clock=lambda: next(ticks), sleeper=lambda _: None, getter=complete_getter)["state"] != "completed" else None)

    fail_ticks = iter([0.0, 0.0, 1.0])
    fail_getter = lambda base, endpoint, timeout: {"pid": {"outputs": {}, "status": {"status_str": "error", "messages": [["execution_error", {}]]}}}
    run("history execution failure", lambda: (_ for _ in ()).throw(AssertionError()) if poll_history("http://local", "pid", timeout_seconds=10, interval_seconds=0, request_timeout=1, clock=lambda: next(fail_ticks), sleeper=lambda _: None, getter=fail_getter)["state"] != "failed" else None)

    def good_generator(payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "queued": True, "prompt_id": "pid", "backend": {"base_url": "http://local"}, "video_lora_stack": {"applied_count": 0}}
    good_history = lambda *args, **kwargs: {"state": "completed", "outputs": [{"filename": "out.mp4", "subfolder": "", "type": "output"}]}
    good_verify = lambda base, item, timeout: {**item, "readable": True, "sample_size_bytes": 100, "sample_sha256": "abc"}
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        run("physical case pass requires readable output", lambda: (_ for _ in ()).throw(AssertionError()) if not run_case(plan()["cases"][0], evidence_dir=root, generator=good_generator, history_poller=good_history, output_verifier=good_verify)["physical_inference_proven"] else None)
        bad_verify = lambda base, item, timeout: {**item, "readable": False, "sample_size_bytes": 0}
        run("unreadable output fails proof", lambda: (_ for _ in ()).throw(AssertionError()) if run_case(plan()["cases"][0], evidence_dir=root, generator=good_generator, history_poller=good_history, output_verifier=bad_verify)["physical_inference_proven"] else None)
        run("queue failure persists evidence", lambda: (_ for _ in ()).throw(AssertionError()) if run_case(plan()["cases"][0], evidence_dir=root, generator=lambda payload: {"ok": False, "queued": False}, history_poller=good_history, output_verifier=good_verify)["status"] != "failed" else None)
        def fake_runner(case: dict[str, Any], *, evidence_dir: Path) -> dict[str, Any]:
            return {"case_id": case["id"], "route_id": case["route_id"], "status": "passed", "physical_inference_proven": True, "errors": []}
        run("aggregate gate passes only completed cases", lambda: (_ for _ in ()).throw(AssertionError()) if run_plan(plan(), evidence_dir=root, runner=fake_runner)["gate"] != "pass" else None)
    failed = sum(not item["ok"] for item in cases)
    return {"schema_version": "neo.video.physical_gpu_validation_regression.v1", "phase": "phase_18", "ok": failed == 0, "gate": "pass" if failed == 0 else "fail", "case_count": len(cases), "passed": len(cases)-failed, "failed": failed, "framework_verified": failed == 0, "physical_gpu_inference_executed": False, "cases": cases}


if __name__ == "__main__":
    import json
    result=run_phase18_gate(); print(json.dumps(result, indent=2)); raise SystemExit(0 if result["ok"] else 1)
