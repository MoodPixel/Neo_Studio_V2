from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.first_last_frame_compiler import _attach_video_output_record, _clamp01
from neo_app.video.ltx_img2vid_compiler import discover_ltx23_img2vid_bindings
from neo_app.video.ltx_txt2vid_compiler import (
    DEFAULT_NEGATIVE_PROMPT,
    _apply_vram_safety as _txt2vid_vram_safety,
    _clip_loader_inputs,
    _model_loader_inputs,
    _now,
    _post_json,
    _required_inputs,
    _saver_inputs,
    _set_if_supported,
    _vae_loader_inputs,
)
from neo_app.video.output_paths import ROOT_DIR, get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import video_output_file_path
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.extend.compiler.v17"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.extend"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.extend"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)
VIDEO_EXTENSIONS: Final[tuple[str, ...]] = (".webm", ".mp4", ".mov", ".mkv", ".gif")


@dataclass(frozen=True)
class LtxExtendCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "extend"
    prompt: str = ""
    negative_prompt: str = DEFAULT_NEGATIVE_PROMPT
    vram_profile: str = "balanced"
    width: int | None = None
    height: int | None = None
    frames: int | None = None
    fps: float | int | None = None
    steps: int | None = None
    guidance: float | None = None
    seed: int | None = None
    sampler: str | None = None
    scheduler: str | None = None
    output_format: str = "webm"
    filename_prefix: str = "Neo_Video_LTX23_Extend"
    source_result_id: str | None = None
    source_file_id: str | None = None
    source_video_path: str | None = None
    continuation_strength: float | None = None
    extraction_mode: str = "last_frame"
    stitch_output: bool = False
    model_name: str | None = None
    unet_name: str | None = None
    gguf_name: str | None = None
    clip_name1: str | None = None
    clip_name2: str | None = None
    vae_name: str | None = None
    chunk_feed_forward: int | None = None
    tile_size: int | None = None
    temporal_tile_size: int | None = None
    tiled_vae_decode: bool = True
    profile_id: str | None = None
    dry_run: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxExtendCompileRequest":
        data = payload or {}
        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "extend")) or "extend"),
            prompt=str(data.get("prompt", data.get("positive_prompt", "")) or ""),
            negative_prompt=str(data.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT),
            vram_profile=str(data.get("vram_profile", "balanced") or "balanced"),
            width=_int_or_none(data.get("width")),
            height=_int_or_none(data.get("height")),
            frames=_int_or_none(data.get("frames")),
            fps=_float_or_none(data.get("fps")),
            steps=_int_or_none(data.get("steps")),
            guidance=_float_or_none(data.get("guidance", data.get("cfg"))),
            seed=_int_or_none(data.get("seed")),
            sampler=str(data.get("sampler") or "") or None,
            scheduler=str(data.get("scheduler") or "") or None,
            output_format=str(data.get("output_format", "webm") or "webm"),
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_Extend") or "Neo_Video_LTX23_Extend"),
            source_result_id=str(data.get("source_result_id", data.get("parent_result_id", "")) or "") or None,
            source_file_id=str(data.get("source_file_id", data.get("file_id", "")) or "") or None,
            source_video_path=str(data.get("source_video_path", data.get("video_path", "")) or "") or None,
            continuation_strength=_float_or_none(data.get("continuation_strength", data.get("image_strength", data.get("strength")))),
            extraction_mode=str(data.get("extraction_mode", "last_frame") or "last_frame"),
            stitch_output=bool(data.get("stitch_output", False)),
            model_name=str(data.get("model_name", "") or "") or None,
            unet_name=str(data.get("unet_name", "") or "") or None,
            gguf_name=str(data.get("gguf_name", "") or "") or None,
            clip_name1=str(data.get("clip_name1", data.get("clip_name", "")) or "") or None,
            clip_name2=str(data.get("clip_name2", data.get("text_projection", "")) or "") or None,
            vae_name=str(data.get("vae_name", "") or "") or None,
            chunk_feed_forward=_int_or_none(data.get("chunk_feed_forward")),
            tile_size=_int_or_none(data.get("tile_size")),
            temporal_tile_size=_int_or_none(data.get("temporal_tile_size")),
            tiled_vae_decode=bool(data.get("tiled_vae_decode", True)),
            profile_id=str(data.get("profile_id", "") or "") or None,
            dry_run=bool(data.get("dry_run", True)),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _relative_to_root(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


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


def resolve_extend_source(req: LtxExtendCompileRequest) -> dict[str, Any]:
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
            "error": "Extend requires a Neo-owned source video. Select a Video result with an attached file or provide a path under neo_data/outputs/video.",
            "source_result_id": req.source_result_id or "",
            "source_file_id": req.source_file_id or "",
            "source_video_path": req.source_video_path or "",
        }
    return {
        "ok": True,
        "kind": source_kind,
        "path": str(source_path),
        "filename": source_path.name,
        "relative_path": _relative_to_root(source_path),
        "source_result_id": req.source_result_id or "",
        "source_file_id": req.source_file_id or "",
    }


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


def _field(required: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    folded = {str(key).casefold(): str(key) for key in required.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


def discover_extend_bindings(loader: str, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    base = discover_ltx23_img2vid_bindings(loader, info)
    classes = dict(base["classes"])
    classes["load_video"] = _class_exists(info, "VHS_LoadVideo", "VHS_LoadVideoPath", "LoadVideo", "LoadVideoPath", "LoadVideoUpload") or "VHS_LoadVideo"
    classes["frame_picker"] = _class_exists(info, "GetImageFromBatch", "ImageFromBatch", "ImageBatchGet", "VHS_GetLastFrame", "GetLastFrame", "VideoLastFrame", "SelectLastFrame") or "GetImageFromBatch"
    classes["guide_node"] = "LTXVAddGuide" if "LTXVAddGuide" in info else classes.get("guide_node") or "LTXVAddGuide"
    available = dict(base.get("available", {}))
    available.update({"load_video": classes["load_video"] in info, "frame_picker": classes["frame_picker"] in info})
    return {**base, "classes": classes, "available": available, "phase": "V17"}


def _load_video_inputs(required: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    filename = source.get("relative_path") or source.get("path") or source.get("filename") or ""
    key = _field(required, ("video", "video_path", "path", "file", "filename", "upload", "video_name"), "video")
    inputs = {key: filename}
    if "force_rate" in required:
        inputs["force_rate"] = 0
    if "frame_load_cap" in required:
        inputs["frame_load_cap"] = 0
    if "skip_first_frames" in required:
        inputs["skip_first_frames"] = 0
    if "select_every_nth" in required:
        inputs["select_every_nth"] = 1
    return inputs


def _frame_picker_inputs(required: dict[str, Any]) -> dict[str, Any]:
    image_key = _field(required, ("images", "image", "frames", "video"), "images")
    inputs = {image_key: ["0", 0]}
    # For common ImageFromBatch/GetImageFromBatch nodes, -1 means last item in many custom nodes. If the node expects clamped indexes, Comfy generally clamps or errors visibly; this stays behind compile preview.
    for key in ("index", "batch_index", "frame_index", "idx"):
        if key in required or not required:
            inputs[key] = -1
            break
    return inputs


def _guide_inputs(required: dict[str, Any], strength: float) -> dict[str, Any]:
    inputs: dict[str, Any] = {"positive": ["16", 0], "negative": ["16", 1], "vae": ["3", 0], "latent": ["16", 2]}
    if any(str(key).startswith("num_guides.") for key in required):
        inputs.update({"num_guides.image_1": ["18", 0], "num_guides.strength_1": strength})
        if "num_guides.frame_idx_1" in required:
            inputs["num_guides.frame_idx_1"] = 0
    else:
        inputs.update({"image": ["18", 0], "strength": strength})
    return inputs


def build_ltx23_extend_workflow(req: LtxExtendCompileRequest, source: dict[str, Any], object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    loader = normalize_video_loader(req.loader)
    bindings = discover_extend_bindings(loader, info)
    classes = bindings["classes"]
    params = _txt2vid_vram_safety(req)  # type: ignore[arg-type]
    strength = _clamp01(req.continuation_strength, 0.75)
    prompt = req.prompt.strip() or "Continue the source video smoothly with consistent motion, lighting, and camera direction."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    prefix = sanitize_path_part(req.filename_prefix or "Neo_Video_LTX23_Extend", fallback="Neo_Video_LTX23_Extend")

    latent_required = _required_inputs(info, classes["latent_node"])
    latent_inputs = {"width": params["width"], "height": params["height"], "length": params["frames"], "batch_size": 1}
    if "batch_size" not in latent_required and latent_required:
        latent_inputs.pop("batch_size", None)

    scheduler_required = _required_inputs(info, classes["scheduler_node"])
    scheduler_inputs = {"latent": ["17", 2]}
    _set_if_supported(scheduler_inputs, scheduler_required, "steps", params["steps"], fallback_ok=not bool(scheduler_required))
    _set_if_supported(scheduler_inputs, scheduler_required, "max_shift", 2.05, fallback_ok=not bool(scheduler_required))
    _set_if_supported(scheduler_inputs, scheduler_required, "base_shift", 0.95, fallback_ok=not bool(scheduler_required))
    _set_if_supported(scheduler_inputs, scheduler_required, "stretch", True, fallback_ok=not bool(scheduler_required))
    _set_if_supported(scheduler_inputs, scheduler_required, "terminal", 0.1, fallback_ok=not bool(scheduler_required))

    decode_required = _required_inputs(info, classes["decode_node"])
    decode_inputs = {"samples": ["11", 0], "vae": ["3", 0]}
    _set_if_supported(decode_inputs, decode_required, "tile_size", params["tile_size"])
    _set_if_supported(decode_inputs, decode_required, "overlap", 64)
    _set_if_supported(decode_inputs, decode_required, "temporal_size", params["temporal_tile_size"])
    _set_if_supported(decode_inputs, decode_required, "temporal_overlap", 8)

    load_video_required = _required_inputs(info, classes["load_video"])
    frame_picker_required = _required_inputs(info, classes["frame_picker"])
    guide_required = _required_inputs(info, classes["guide_node"])
    workflow = {
        "0": {"class_type": classes["load_video"], "inputs": _load_video_inputs(load_video_required, source)},
        "18": {"class_type": classes["frame_picker"], "inputs": _frame_picker_inputs(frame_picker_required)},
        "1": {"class_type": classes["model_loader"], "inputs": _model_loader_inputs(req, bindings, info)},
        "2": {"class_type": classes["clip_loader"], "inputs": _clip_loader_inputs(req, bindings, info)},
        "3": {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(req, bindings, info)},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": classes["condition_node"], "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(params["fps"])}},
        "7": {"class_type": classes["latent_node"], "inputs": latent_inputs},
        "8": {"class_type": classes["chunk_node"], "inputs": {"model": ["1", 0], "chunks": params["chunk_feed_forward"], "dim_threshold": 4096}},
        "16": {"class_type": classes["crop_node"], "inputs": {"positive": ["6", 0], "negative": ["6", 1], "latent": ["7", 0]}},
        "17": {"class_type": classes["guide_node"], "inputs": _guide_inputs(guide_required, strength)},
        "9": {"class_type": classes["guider_node"], "inputs": {"model": ["8", 0], "positive": ["17", 0], "negative": ["17", 1], "cfg": params["guidance"]}},
        "10": {"class_type": classes["sampler_select_node"], "inputs": {"sampler_name": params["sampler"]}},
        "11": {"class_type": classes["sampler_node"], "inputs": {"noise": ["13", 0], "guider": ["9", 0], "sampler": ["10", 0], "sigmas": ["14", 0], "latent_image": ["17", 2]}},
        "12": {"class_type": classes["decode_node"], "inputs": decode_inputs},
        "13": {"class_type": classes["noise_node"], "inputs": {"noise_seed": params["seed"]}},
        "14": {"class_type": classes["scheduler_node"], "inputs": scheduler_inputs},
        "15": {"class_type": classes["saver"], "inputs": _saver_inputs(classes["saver"], prefix, float(params["fps"]))},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": "V17",
        "route_id": f"ltx23.{loader}.extend",
        "compiled_at": _now(),
        "parameters": {**{key: value for key, value in params.items() if key != "profile"}, "continuation_strength": strength, "extraction_mode": req.extraction_mode, "stitch_output": req.stitch_output},
        "profile": params["profile"],
        "vram_engine": params.get("vram_engine", {}),
        "bindings": bindings,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "source": {**source, "required": True, "continuation_strength": strength, "extraction_mode": req.extraction_mode},
        "rules": [
            "V17 Extend uses a Neo-owned source video and extracts a continuation guide frame inside ComfyUI.",
            "The source video is never modified; the extension run creates a new child output under neo_data/outputs/video/extend.",
            "This lane is active for LTX 2.3 GGUF and UNET/Diffusion routes only; WAN stays guarded.",
            "Stitching is recorded as intent but remains guarded until a dedicated append/stitch pass is implemented.",
        ],
    }


def video_ltx23_extend_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxExtendCompileRequest.from_payload(payload)
    nf = normalize_video_family(req.family)
    nl = normalize_video_loader(req.loader)
    nt = normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V17", "ok": False, "queued": False, "error": f"V17 Extend compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.", "request": {"family": nf, "loader": nl, "generation_type": nt}, "route": route.payload() if route else None}
    source = resolve_extend_source(req)
    if not source.get("ok"):
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V17", "ok": False, "queued": False, "error": source.get("error") or "Extend source video is missing.", "request": req.payload(), "source": source, "route": route.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX Extend bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_ltx23_extend_workflow(req, source, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("extend", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_extend')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_extend_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxExtendCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_extend_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    route_ready = compile_payload.get("route_readiness", {}).get("ready")
    if object_info_override is not None and not route_ready:
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 Extend route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {**compile_payload, "ok": True, "queued": True, "dry_run": False, "queue_response": queue_response, "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or ""}
    return _attach_video_output_record(response_payload, req.payload())
