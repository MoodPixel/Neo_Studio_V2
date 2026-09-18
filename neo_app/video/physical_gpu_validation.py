from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import argparse
import hashlib
import importlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Final
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen

SCHEMA_VERSION: Final[str] = "neo.video.physical_gpu_validation.v1"
PLAN_SCHEMA: Final[str] = "neo.video.physical_gpu_validation_plan.v1"

ROUTE_GENERATORS: Final[dict[str, tuple[str, str]]] = {
    "minimax_h3.unet.txt2vid": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.unet.img2vid": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.unet.first_last_frame": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.unet.reference_to_video": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.unet.vid2vid": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.gguf.txt2vid": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.gguf.img2vid": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.gguf.first_last_frame": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.gguf.reference_to_video": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "minimax_h3.gguf.vid2vid": ("neo_app.video.minimax_h3_compiler", "video_minimax_h3_generate_payload"),
    "ltx23.unet.txt2vid": ("neo_app.video.ltx_txt2vid_compiler", "video_ltx23_txt2vid_generate_payload"),
    "ltx23.gguf.txt2vid": ("neo_app.video.ltx_txt2vid_compiler", "video_ltx23_txt2vid_generate_payload"),
    "ltx23.unet.img2vid": ("neo_app.video.ltx_img2vid_compiler", "video_ltx23_img2vid_generate_payload"),
    "ltx23.gguf.img2vid": ("neo_app.video.ltx_img2vid_compiler", "video_ltx23_img2vid_generate_payload"),
    "ltx23.unet.first_last_frame": ("neo_app.video.first_last_frame_compiler", "video_ltx23_first_last_frame_generate_payload"),
    "ltx23.gguf.first_last_frame": ("neo_app.video.first_last_frame_compiler", "video_ltx23_first_last_frame_generate_payload"),
    "ltx23.unet.multiscene": ("neo_app.video.multiscene_compiler", "video_ltx23_multiscene_generate_payload"),
    "ltx23.gguf.multiscene": ("neo_app.video.multiscene_compiler", "video_ltx23_multiscene_generate_payload"),
    "ltx23.unet.extend": ("neo_app.video.extend_compiler", "video_ltx23_extend_generate_payload"),
    "ltx23.gguf.extend": ("neo_app.video.extend_compiler", "video_ltx23_extend_generate_payload"),
    "ltx23.unet.vid2vid": ("neo_app.video.vid2vid_compiler", "video_ltx23_vid2vid_generate_payload"),
    "ltx23.gguf.vid2vid": ("neo_app.video.vid2vid_compiler", "video_ltx23_vid2vid_generate_payload"),
    "ltx23.unet.depth_motion": ("neo_app.video.depth_motion_compiler", "video_ltx23_depth_motion_generate_payload"),
    "ltx23.gguf.depth_motion": ("neo_app.video.depth_motion_compiler", "video_ltx23_depth_motion_generate_payload"),
    "ltx23.unet.prompt_schedule": ("neo_app.video.schedule_compiler", "video_ltx23_schedule_generate_payload"),
    "ltx23.gguf.prompt_schedule": ("neo_app.video.schedule_compiler", "video_ltx23_schedule_generate_payload"),
    "ltx23.unet.audio_video": ("neo_app.video.audio_video_compiler", "video_ltx23_audio_video_generate_payload"),
    "ltx23.gguf.audio_video": ("neo_app.video.audio_video_compiler", "video_ltx23_audio_video_generate_payload"),
    "wan22.unet.txt2vid": ("neo_app.video.wan_txt2vid_compiler", "video_wan22_txt2vid_generate_payload"),
    "wan22.unet.img2vid": ("neo_app.video.wan_txt2vid_compiler", "video_wan22_img2vid_generate_payload"),
    "wan22.gguf.img2vid_14b_dual_noise": ("neo_app.video.wan_gguf_i2v14_compiler", "video_wan22_gguf_i2v14_generate_payload"),
    "wan22.rapid_aio_gguf.txt2vid": ("neo_app.video.wan_rapid_aio_gguf_compiler", "video_wan22_rapid_aio_gguf_generate_payload"),
    "wan22.rapid_aio_gguf.img2vid": ("neo_app.video.wan_rapid_aio_gguf_compiler", "video_wan22_rapid_aio_gguf_generate_payload"),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_plan(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict): raise ValueError("GPU validation plan must be a JSON object.")
    return value


def validate_plan(plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if plan.get("schema_version") != PLAN_SCHEMA: errors.append("unsupported plan schema")
    cases = plan.get("cases") if isinstance(plan.get("cases"), list) else []
    if not cases: errors.append("plan contains no cases")
    ids: set[str] = set()
    for index, case in enumerate(cases):
        if not isinstance(case, dict): errors.append(f"case {index} is not an object"); continue
        case_id = str(case.get("id") or "")
        route_id = str(case.get("route_id") or "")
        if not case_id: errors.append(f"case {index} has no id")
        elif case_id in ids: errors.append(f"duplicate case id {case_id}")
        ids.add(case_id)
        if route_id not in ROUTE_GENERATORS: errors.append(f"case {case_id or index} has no registered generator for {route_id}")
        payload = case.get("payload") if isinstance(case.get("payload"), dict) else {}
        if payload.get("dry_run") is not False: errors.append(f"case {case_id or index} must explicitly set payload.dry_run=false")
        expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
        if expected.get("output_required") is not True: errors.append(f"case {case_id or index} must require physical output")
    if errors: raise ValueError("Invalid GPU validation plan: " + "; ".join(errors))
    return {"valid": True, "case_count": len(cases), "case_ids": sorted(ids)}


def _get_json(base_url: str, endpoint: str, timeout: float) -> dict[str, Any]:
    request = Request(urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/")), headers={"Accept": "application/json", "User-Agent": "NeoStudioGPUValidation/1.0"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - user-configured Comfy backend.
        raw = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw) if raw else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _history_entry(history: dict[str, Any], prompt_id: str) -> dict[str, Any]:
    direct = history.get(prompt_id)
    if isinstance(direct, dict): return direct
    return history if isinstance(history.get("outputs"), dict) else {}


def output_candidates(entry: dict[str, Any]) -> list[dict[str, str]]:
    found: list[dict[str, str]] = []
    outputs = entry.get("outputs") if isinstance(entry.get("outputs"), dict) else {}
    for node_id, payload in outputs.items():
        if not isinstance(payload, dict): continue
        for bucket in ("videos", "gifs", "images"):
            items = payload.get(bucket) if isinstance(payload.get(bucket), list) else []
            for item in items:
                if isinstance(item, dict) and item.get("filename"):
                    found.append({"node_id": str(node_id), "bucket": bucket, "filename": str(item["filename"]), "subfolder": str(item.get("subfolder") or ""), "type": str(item.get("type") or "output")})
    return found


def verify_remote_output(base_url: str, candidate: dict[str, str], *, timeout: float, sample_bytes: int = 1048576) -> dict[str, Any]:
    query = urlencode({"filename": candidate["filename"], "subfolder": candidate.get("subfolder", ""), "type": candidate.get("type", "output")})
    request = Request(urljoin(base_url.rstrip("/") + "/", "view?" + query), headers={"User-Agent": "NeoStudioGPUValidation/1.0"})
    digest = hashlib.sha256(); size = 0
    with urlopen(request, timeout=timeout) as response:  # noqa: S310
        while size < sample_bytes:
            chunk = response.read(min(65536, sample_bytes - size))
            if not chunk: break
            digest.update(chunk); size += len(chunk)
        content_type = str(response.headers.get("Content-Type") or "")
        content_length = str(response.headers.get("Content-Length") or "")
    return {**candidate, "readable": size > 0, "sample_size_bytes": size, "sample_sha256": digest.hexdigest(), "content_type": content_type, "reported_content_length": content_length}


def poll_history(base_url: str, prompt_id: str, *, timeout_seconds: float, interval_seconds: float, request_timeout: float, clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep, getter: Callable[[str, str, float], dict[str, Any]] = _get_json) -> dict[str, Any]:
    started = clock(); attempts = 0
    while clock() - started <= timeout_seconds:
        attempts += 1
        history = getter(base_url, f"/history/{prompt_id}", request_timeout)
        entry = _history_entry(history, prompt_id)
        candidates = output_candidates(entry)
        status = entry.get("status") if isinstance(entry.get("status"), dict) else {}
        status_text = str(status.get("status_str") or "").casefold()
        messages = status.get("messages") if isinstance(status.get("messages"), list) else []
        if candidates: return {"state": "completed", "attempts": attempts, "elapsed_seconds": round(clock()-started, 3), "entry": entry, "outputs": candidates}
        if status_text in {"error", "failed"} or any(isinstance(item, list) and item and item[0] == "execution_error" for item in messages):
            return {"state": "failed", "attempts": attempts, "elapsed_seconds": round(clock()-started, 3), "entry": entry, "outputs": []}
        sleeper(interval_seconds)
    return {"state": "timeout", "attempts": attempts, "elapsed_seconds": round(clock()-started, 3), "entry": {}, "outputs": []}


def resolve_generator(route_id: str) -> Callable[..., dict[str, Any]]:
    module_name, function_name = ROUTE_GENERATORS[route_id]
    return getattr(importlib.import_module(module_name), function_name)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def run_case(case: dict[str, Any], *, evidence_dir: Path, generator: Callable[..., dict[str, Any]] | None = None, history_poller: Callable[..., dict[str, Any]] = poll_history, output_verifier: Callable[..., dict[str, Any]] = verify_remote_output) -> dict[str, Any]:
    case_id = str(case["id"]); route_id = str(case["route_id"]); payload = deepcopy(case["payload"])
    payload["dry_run"] = False
    started = utc_now()
    try:
        result = (generator or resolve_generator(route_id))(payload)
        prompt_id = str(result.get("prompt_id") or (result.get("queue_response") or {}).get("prompt_id") or "")
        base_url = str((result.get("backend") or {}).get("base_url") or case.get("base_url") or "")
        if not result.get("ok") or not result.get("queued") or not prompt_id or not base_url:
            raise RuntimeError(f"Generation did not produce queue proof: ok={result.get('ok')} queued={result.get('queued')} prompt_id={prompt_id!r} base_url={bool(base_url)}")
        polling = case.get("polling") if isinstance(case.get("polling"), dict) else {}
        history = history_poller(base_url, prompt_id, timeout_seconds=float(polling.get("timeout_seconds", 7200)), interval_seconds=float(polling.get("interval_seconds", 5)), request_timeout=float(polling.get("request_timeout", 15)))
        verified = [output_verifier(base_url, item, timeout=float(polling.get("download_timeout", 120))) for item in history.get("outputs", [])]
        applied = result.get("video_lora_stack") if isinstance(result.get("video_lora_stack"), dict) else {}
        expected = case.get("expected") if isinstance(case.get("expected"), dict) else {}
        errors: list[str] = []
        if history.get("state") != "completed": errors.append(f"history ended as {history.get('state')}")
        if not any(item.get("readable") for item in verified): errors.append("no readable Comfy output artifact")
        if "applied_lora_count" in expected and applied.get("applied_count") != expected["applied_lora_count"]: errors.append("applied LoRA count does not match expectation")
        evidence = {"schema_version": SCHEMA_VERSION, "case_id": case_id, "route_id": route_id, "started_at": started, "completed_at": utc_now(), "status": "passed" if not errors else "failed", "physical_inference_proven": not errors, "prompt_id": prompt_id, "request": payload, "compile_queue": {"ok": result.get("ok"), "queued": result.get("queued"), "route_id": result.get("route_id"), "video_lora_stack": applied, "lora_patch_profile": result.get("lora_patch_profile")}, "history": history, "verified_outputs": verified, "errors": errors}
    except Exception as exc:  # noqa: BLE001 - evidence must persist every operational failure.
        evidence = {"schema_version": SCHEMA_VERSION, "case_id": case_id, "route_id": route_id, "started_at": started, "completed_at": utc_now(), "status": "failed", "physical_inference_proven": False, "request": payload, "errors": [f"{type(exc).__name__}: {exc}"]}
    _write_json(evidence_dir / "cases" / f"{case_id}.json", evidence)
    return evidence


def run_plan(plan: dict[str, Any], *, evidence_dir: Path, resume: bool = False, only: set[str] | None = None, runner: Callable[..., dict[str, Any]] = run_case) -> dict[str, Any]:
    validate_plan(plan); evidence_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for case in plan["cases"]:
        case_id = str(case["id"])
        if only and case_id not in only: continue
        path = evidence_dir / "cases" / f"{case_id}.json"
        if resume and path.is_file():
            previous = json.loads(path.read_text(encoding="utf-8"))
            if previous.get("status") == "passed": results.append(previous); continue
        results.append(runner(case, evidence_dir=evidence_dir))
    passed = sum(item.get("physical_inference_proven") is True for item in results)
    report = {"schema_version": SCHEMA_VERSION, "plan_id": plan.get("plan_id"), "generated_at": utc_now(), "case_count": len(results), "passed": passed, "failed": len(results)-passed, "gate": "pass" if results and passed == len(results) else "fail", "physical_inference_proven": bool(results) and passed == len(results), "cases": [{"case_id": item.get("case_id"), "route_id": item.get("route_id"), "status": item.get("status"), "physical_inference_proven": item.get("physical_inference_proven"), "errors": item.get("errors", [])} for item in results]}
    _write_json(evidence_dir / "report.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run explicit physical ComfyUI GPU inference validation cases.")
    parser.add_argument("--plan", required=True, type=Path); parser.add_argument("--evidence-dir", type=Path, default=Path("gpu_validation_evidence"))
    parser.add_argument("--run", action="store_true"); parser.add_argument("--resume", action="store_true"); parser.add_argument("--case", action="append", default=[]); parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args(); plan = load_plan(args.plan); validate_plan(plan)
    if args.list_cases:
        for case in plan["cases"]: print(f"{case['id']}\t{case['route_id']}")
        return 0
    if not args.run:
        print(json.dumps({"ok": True, "validated_only": True, "physical_inference_proven": False, "case_count": len(plan["cases"]), "message": "Plan validated. Add --run to queue GPU jobs."}, indent=2)); return 0
    report = run_plan(plan, evidence_dir=args.evidence_dir, resume=args.resume, only=set(args.case) or None)
    print(json.dumps(report, indent=2)); return 0 if report["gate"] == "pass" else 1


if __name__ == "__main__": raise SystemExit(main())
