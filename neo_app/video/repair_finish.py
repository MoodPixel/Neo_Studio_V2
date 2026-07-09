from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from neo_app.video.backend_probe import _get_json, video_backend_profile_payload
from neo_app.video.external_node_manager import evaluate_video_external_node_packs
from neo_app.video.output_paths import ROOT_DIR, get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import register_video_generation_result, video_output_file_path

SCHEMA_VERSION: Final[str] = "neo.video.finish.repair.v14"
PHASE: Final[str] = "V14"
ROUTE_ID: Final[str] = "finish.repair"

VIDEO_EXTENSIONS: Final[tuple[str, ...]] = (".webm", ".mp4", ".mov", ".mkv", ".gif")


@dataclass(frozen=True)
class VideoRepairRequest:
    source_result_id: str | None = None
    source_file_id: str | None = None
    source_video_path: str | None = None
    mode: str = "auto"
    strength: float = 0.35
    temporal_radius: int = 3
    noise_reduction: float = 0.25
    sharpen_amount: float = 0.15
    output_fps: float | None = None
    output_format: str = "webm"
    preserve_audio: bool = True
    cpu_offload: bool = True
    filename_prefix: str = "Neo_Video_Repaired"
    profile_id: str | None = None
    dry_run: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "VideoRepairRequest":
        data = payload or {}
        return cls(
            source_result_id=str(data.get("source_result_id", data.get("parent_result_id", "")) or "") or None,
            source_file_id=str(data.get("source_file_id", data.get("file_id", "")) or "") or None,
            source_video_path=str(data.get("source_video_path", data.get("video_path", "")) or "") or None,
            mode=str(data.get("mode", data.get("repair_mode", "auto")) or "auto"),
            strength=_float_value(data.get("strength", data.get("repair_strength")), 0.35),
            temporal_radius=max(1, min(_int_value(data.get("temporal_radius"), 3), 15)),
            noise_reduction=max(0.0, min(_float_value(data.get("noise_reduction"), 0.25), 1.0)),
            sharpen_amount=max(0.0, min(_float_value(data.get("sharpen_amount"), 0.15), 2.0)),
            output_fps=_float_or_none(data.get("output_fps", data.get("fps"))),
            output_format=str(data.get("output_format", "webm") or "webm"),
            preserve_audio=bool(data.get("preserve_audio", True)),
            cpu_offload=bool(data.get("cpu_offload", True)),
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_Repaired") or "Neo_Video_Repaired"),
            profile_id=str(data.get("profile_id", "") or "") or None,
            dry_run=bool(data.get("dry_run", True)),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_value(value: Any, fallback: float) -> float:
    parsed = _float_or_none(value)
    return fallback if parsed is None else parsed


def _int_value(value: Any, fallback: int) -> int:
    parsed = _int_or_none(value)
    return fallback if parsed is None else parsed


def _class_exists(object_info: dict[str, Any], *candidates: str) -> str | None:
    folded = {str(key).casefold(): str(key) for key in object_info.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    for candidate in candidates:
        needle = candidate.casefold().replace(" ", "").replace("_", "")
        fuzzy = next((str(key) for key in object_info.keys() if needle and needle in str(key).casefold().replace(" ", "").replace("_", "")), None)
        if fuzzy:
            return fuzzy
    return None


def _required_inputs(object_info: dict[str, Any], class_type: str | None) -> dict[str, Any]:
    if not class_type:
        return {}
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    return required if isinstance(required, dict) else {}


def _first_field(required: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    folded = {key.casefold(): key for key in required.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


def _safe_neo_video_path(path_value: str | None) -> Path | None:
    if not path_value:
        return None
    path = Path(str(path_value))
    if not path.is_absolute():
        path = ROOT_DIR / path
    target = path.resolve()
    video_root = (ROOT_DIR / "neo_data" / "outputs" / "video").resolve()
    if target.suffix.lower() not in VIDEO_EXTENSIONS:
        return None
    if video_root not in target.parents and target != video_root:
        return None
    return target if target.exists() and target.is_file() else None


def resolve_repair_source(req: VideoRepairRequest) -> dict[str, Any]:
    source_path: Path | None = None
    source_kind = "none"
    if req.source_result_id:
        source_path = video_output_file_path(req.source_result_id, req.source_file_id or "video_1")
        source_kind = "ledger_result"
    if source_path is None and req.source_video_path:
        source_path = _safe_neo_video_path(req.source_video_path)
        source_kind = "neo_path"
    if source_path is None:
        return {
            "ok": False,
            "error": "Repair requires a Neo-owned source video. Select a Video result with an attached file or provide a path under neo_data/outputs/video.",
            "source_result_id": req.source_result_id or "",
            "source_file_id": req.source_file_id or "",
            "source_video_path": req.source_video_path or "",
        }
    return {
        "ok": True,
        "kind": source_kind,
        "path": str(source_path),
        "filename": source_path.name,
        "relative_path": source_path.resolve().relative_to(ROOT_DIR.resolve()).as_posix(),
        "source_result_id": req.source_result_id or "",
        "source_file_id": req.source_file_id or "",
    }


def _cleanup_candidates(mode: str) -> tuple[str, ...]:
    requested = str(mode or "auto").lower().replace("-", "_")
    if requested == "deflicker":
        return ("VHS_Deflicker", "VideoDeflicker", "Deflicker", "ImageDeflicker")
    if requested == "denoise":
        return ("ImageDenoise", "DenoiseImage", "NLMeansDenoise", "ImageMedianFilter", "MedianFilter")
    if requested == "sharpen":
        return ("ImageSharpen", "SharpenImage", "ImageFilterSharpen")
    if requested in {"color", "color_fix", "levels"}:
        return ("ColorCorrect", "ImageColorCorrection", "ImageLevelsAdjustment", "ImageContrast")
    return (
        "VHS_Deflicker",
        "VideoDeflicker",
        "ImageDenoise",
        "DenoiseImage",
        "NLMeansDenoise",
        "ImageMedianFilter",
        "ImageSharpen",
        "SharpenImage",
        "ColorCorrect",
        "ImageLevelsAdjustment",
        "ImageContrast",
    )


def discover_repair_bindings(object_info: dict[str, Any] | None = None, mode: str = "auto") -> dict[str, Any]:
    info = object_info or {}
    load_video = _class_exists(info, "VHS_LoadVideo", "LoadVideo", "LoadVideoUpload", "VHS_LoadVideoPath", "LoadVideoPath") or "VHS_LoadVideo"
    cleanup = _class_exists(info, *_cleanup_candidates(mode))
    saver = _class_exists(info, "VHS_VideoCombine", "VideoCombine", "SaveWEBM", "SaveAnimatedWEBP") or "VHS_VideoCombine"
    requested = str(mode or "auto").lower().replace("-", "_")
    if requested not in {"auto", "deflicker", "denoise", "sharpen", "color", "color_fix", "levels", "passthrough"}:
        requested = "auto"
    return {
        "classes": {"load_video": load_video, "cleanup": cleanup or "passthrough", "saver": saver},
        "available": {"load_video": load_video in info, "cleanup": bool(cleanup and cleanup in info), "saver": saver in info},
        "mode": requested,
        "fallback": "passthrough" if not cleanup else "cleanup_node",
    }


def repair_node_readiness(object_info: dict[str, Any] | None) -> dict[str, Any]:
    info = object_info or {}
    packs = evaluate_video_external_node_packs(info)
    by_id = {item["pack_id"]: item for item in packs}
    video_io = bool(by_id.get("video_helper_suite", {}).get("installed")) or bool(_class_exists(info, "VHS_LoadVideo", "LoadVideo") and _class_exists(info, "VHS_VideoCombine", "VideoCombine", "SaveWEBM"))
    cleanup = bool(_class_exists(info, *_cleanup_candidates("auto")))
    missing = []
    if not video_io:
        missing.append("ComfyUI VideoHelperSuite / video load-combine nodes")
    return {
        "ready": not missing,
        "ready_with_passthrough_fallback": not missing and not cleanup,
        "required": ["video_helper_suite_or_compatible_video_io"],
        "optional": ["deflicker_or_denoise_or_sharpen_or_color_cleanup_nodes"],
        "missing_required": missing,
        "cleanup_available": cleanup,
        "packs": {"video_helper_suite": by_id.get("video_helper_suite", {})},
    }


def _apply_cleanup_inputs(required: dict[str, Any], req: VideoRepairRequest) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for field in ("strength", "amount", "alpha", "blend", "mix"):
        if field in required:
            inputs[field] = req.strength
    for field in ("temporal_radius", "radius", "window", "frames"):
        if field in required:
            inputs[field] = req.temporal_radius
    for field in ("noise_reduction", "denoise", "sigma"):
        if field in required:
            inputs[field] = req.noise_reduction
    for field in ("sharpen", "sharpen_amount", "sharpening", "sharpen_strength"):
        if field in required:
            inputs[field] = req.sharpen_amount
    if "cpu_offload" in required:
        inputs["cpu_offload"] = req.cpu_offload
    return inputs


def build_repair_workflow(req: VideoRepairRequest, source: dict[str, Any], object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    bindings = discover_repair_bindings(object_info, mode=req.mode)
    classes = bindings["classes"]
    prefix = sanitize_path_part(req.filename_prefix, "Neo_Video_Repaired")
    strength = max(0.0, min(float(req.strength or 0.35), 1.0))

    load_required = _required_inputs(object_info or {}, classes["load_video"])
    video_field = _first_field(load_required, ("video", "video_path", "path", "file", "filename"), "video")
    load_inputs: dict[str, Any] = {video_field: source["relative_path"]}
    if "force_rate" in load_required and req.output_fps:
        load_inputs["force_rate"] = req.output_fps
    if "frame_load_cap" in load_required:
        load_inputs["frame_load_cap"] = 0
    if "skip_first_frames" in load_required:
        load_inputs["skip_first_frames"] = 0
    if "select_every_nth" in load_required:
        load_inputs["select_every_nth"] = 1

    workflow: dict[str, Any] = {"1": {"class_type": classes["load_video"], "inputs": load_inputs}}
    saver_input_node = "1"
    cleanup_class = classes.get("cleanup")
    if cleanup_class and cleanup_class != "passthrough":
        cleanup_required = _required_inputs(object_info or {}, cleanup_class)
        image_field = _first_field(cleanup_required, ("images", "image", "frames", "video", "input"), "image")
        cleanup_inputs = {image_field: ["1", 0], **_apply_cleanup_inputs(cleanup_required, req)}
        workflow["2"] = {"class_type": cleanup_class, "inputs": cleanup_inputs}
        saver_input_node = "2"

    saver_required = _required_inputs(object_info or {}, classes["saver"])
    images_field = _first_field(saver_required, ("images", "frames"), "images")
    saver_inputs: dict[str, Any] = {images_field: [saver_input_node, 0], "filename_prefix": prefix}
    if "fps" in saver_required or classes["saver"] in {"VHS_VideoCombine", "VideoCombine", "SaveWEBM", "SaveAnimatedWEBP"}:
        saver_inputs["fps"] = req.output_fps or 24
    if classes["saver"] == "SaveWEBM":
        saver_inputs.update({"codec": "vp9", "crf": 18})
    if classes["saver"] == "SaveAnimatedWEBP":
        saver_inputs.update({"lossless": False, "quality": 92, "method": "default"})
    if "format" in saver_required:
        saver_inputs["format"] = req.output_format
    if "audio" in saver_required and req.preserve_audio:
        # VideoHelperSuite loaders may expose audio as output slot 2 in some versions.
        saver_inputs["audio"] = ["1", 2]
    workflow[str(len(workflow) + 1)] = {"class_type": classes["saver"], "inputs": saver_inputs}

    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": PHASE,
        "route_id": ROUTE_ID,
        "compiled_at": _now(),
        "parameters": {
            "mode": bindings["mode"],
            "strength": strength,
            "temporal_radius": req.temporal_radius,
            "noise_reduction": req.noise_reduction,
            "sharpen_amount": req.sharpen_amount,
            "output_fps": req.output_fps,
            "output_format": req.output_format,
            "preserve_audio": req.preserve_audio,
            "cpu_offload": req.cpu_offload,
            "fallback": bindings["fallback"],
        },
        "bindings": bindings,
        "source": source,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "rules": [
            "V14 repair/cleanup is a non-destructive Finish lane that creates a child result.",
            "The source video must be a Neo-owned output under neo_data/outputs/video.",
            "Repair uses compatible cleanup nodes when detected and falls back to a passthrough child export if only video I/O is available.",
            "The parent result remains untouched; output files attach to a new repair ledger record after refresh/import.",
        ],
    }


def _post_json(base_url: str, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NeoStudioVideoRepair/1.0"}, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local user-configured Comfy URL.
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data) if data else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _persist_finish_record(result: dict[str, Any], req: VideoRepairRequest) -> dict[str, Any]:
    request_payload = {
        **req.payload(),
        "family": "finish",
        "loader": "external_nodes",
        "generation_type": "repair",
        "prompt": "Video Finish: repair / cleanup",
        "negative_prompt": "",
        "source_result_id": req.source_result_id or "",
        "parent_result_id": req.source_result_id or "",
    }
    try:
        ledger = register_video_generation_result(result, request=request_payload)
    except Exception as exc:  # noqa: BLE001
        return {**result, "neo_persisted": {"ok": False, "error": f"Video repair ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def video_repair_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = VideoRepairRequest.from_payload(payload)
    source = resolve_repair_source(req)
    if not source.get("ok"):
        return {"ok": False, "queued": False, "dry_run": True, "schema_version": SCHEMA_VERSION, "phase": PHASE, "surface": "video", "route_id": ROUTE_ID, "error": source.get("error"), "source": source, "request": req.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    warnings: list[str] = []
    object_info: dict[str, Any] = object_info_override or {}
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback repair bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    readiness = repair_node_readiness(object_info)
    compiled = build_repair_workflow(req, source, object_info=object_info)
    if readiness.get("ready_with_passthrough_fallback"):
        warnings.append("No dedicated repair/cleanup node detected; this compile uses a passthrough child export so the parent remains untouched.")
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    output_paths = get_video_output_paths("repair", create=True)
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'video_repair')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "node_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {
        **sidecar_payload,
        "ok": True,
        "queued": False,
        "dry_run": True,
        "backend": {"profile": profile, "base_url": base_url},
        "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)},
    }
    return _persist_finish_record(response_payload, req)


def video_repair_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = VideoRepairRequest.from_payload(payload)
    compile_payload = video_repair_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    if object_info_override is not None and not compile_payload.get("node_readiness", {}).get("ready"):
        return {**compile_payload, "ok": False, "queued": False, "error": "V14 repair is missing required ComfyUI video I/O nodes."}
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI repair queue failed: {exc}"}
    response_payload = {
        **compile_payload,
        "ok": True,
        "queued": True,
        "dry_run": False,
        "queue_response": queue_response,
        "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or "",
    }
    return _persist_finish_record(response_payload, req)
