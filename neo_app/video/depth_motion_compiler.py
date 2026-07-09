from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.extend_compiler import _class_exists, _field, _load_video_inputs, _relative_to_root, _safe_neo_video_path
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
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import video_output_file_path
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.depth_motion.compiler.v19"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.depth_motion"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.depth_motion"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)


@dataclass(frozen=True)
class LtxDepthMotionCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "depth_motion"
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
    filename_prefix: str = "Neo_Video_LTX23_DepthMotion"
    source_result_id: str | None = None
    source_file_id: str | None = None
    source_video_path: str | None = None
    control_type: str = "depth"
    control_strength: float | None = None
    motion_strength: float | None = None
    frame_load_cap: int | None = None
    depth_engine: str = "auto"
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
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxDepthMotionCompileRequest":
        data = payload or {}
        def _int(value: Any) -> int | None:
            try:
                if value is None or value == "": return None
                return int(float(value))
            except (TypeError, ValueError):
                return None
        def _float(value: Any) -> float | None:
            try:
                if value is None or value == "": return None
                return float(value)
            except (TypeError, ValueError):
                return None
        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"), loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "depth_motion")) or "depth_motion"),
            prompt=str(data.get("prompt", data.get("positive_prompt", "")) or ""), negative_prompt=str(data.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT),
            vram_profile=str(data.get("vram_profile", "balanced") or "balanced"), width=_int(data.get("width")), height=_int(data.get("height")), frames=_int(data.get("frames")), fps=_float(data.get("fps")),
            steps=_int(data.get("steps")), guidance=_float(data.get("guidance", data.get("cfg"))), seed=_int(data.get("seed")), sampler=str(data.get("sampler") or "") or None, scheduler=str(data.get("scheduler") or "") or None,
            output_format=str(data.get("output_format", "webm") or "webm"), filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_DepthMotion") or "Neo_Video_LTX23_DepthMotion"),
            source_result_id=str(data.get("source_result_id", data.get("parent_result_id", "")) or "") or None, source_file_id=str(data.get("source_file_id", data.get("file_id", "")) or "") or None,
            source_video_path=str(data.get("source_video_path", data.get("video_path", "")) or "") or None,
            control_type=str(data.get("control_type", "depth") or "depth"), control_strength=_float(data.get("control_strength", data.get("depth_strength"))), motion_strength=_float(data.get("motion_strength")), frame_load_cap=_int(data.get("frame_load_cap")),
            depth_engine=str(data.get("depth_engine", "auto") or "auto"), model_name=str(data.get("model_name", "") or "") or None, unet_name=str(data.get("unet_name", "") or "") or None, gguf_name=str(data.get("gguf_name", "") or "") or None,
            clip_name1=str(data.get("clip_name1", data.get("clip_name", "")) or "") or None, clip_name2=str(data.get("clip_name2", data.get("text_projection", "")) or "") or None, vae_name=str(data.get("vae_name", "") or "") or None,
            chunk_feed_forward=_int(data.get("chunk_feed_forward")), tile_size=_int(data.get("tile_size")), temporal_tile_size=_int(data.get("temporal_tile_size")), tiled_vae_decode=bool(data.get("tiled_vae_decode", True)), profile_id=str(data.get("profile_id", "") or "") or None, dry_run=bool(data.get("dry_run", True)),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def resolve_depth_motion_source(req: LtxDepthMotionCompileRequest) -> dict[str, Any]:
    source_path: Path | None = None
    source_kind = "none"
    if req.source_result_id:
        source_path = video_output_file_path(req.source_result_id, req.source_file_id or "video_1")
        source_kind = "ledger_result"
    if source_path is None and req.source_video_path:
        source_path = _safe_neo_video_path(req.source_video_path)
        source_kind = "neo_path"
    if source_path is None:
        return {"ok": False, "error": "Depth / Motion Control requires a Neo-owned source video under neo_data/outputs/video.", "source_result_id": req.source_result_id or "", "source_file_id": req.source_file_id or "", "source_video_path": req.source_video_path or ""}
    return {"ok": True, "kind": source_kind, "path": str(source_path), "filename": source_path.name, "relative_path": _relative_to_root(source_path), "source_result_id": req.source_result_id or "", "source_file_id": req.source_file_id or ""}


def discover_depth_motion_bindings(loader: str, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    base = discover_ltx23_img2vid_bindings(loader, info)
    classes = dict(base["classes"])
    classes["load_video"] = _class_exists(info, "VHS_LoadVideo", "VHS_LoadVideoPath", "LoadVideo", "LoadVideoPath") or "VHS_LoadVideo"
    classes["frame_picker"] = _class_exists(info, "GetImageFromBatch", "ImageFromBatch", "VHS_SelectEveryNthImage") or "GetImageFromBatch"
    classes["depth_node"] = _class_exists(info, "DepthAnythingV2", "DepthAnythingV2Preprocessor", "DepthAnything", "DepthCrafter", "DepthCrafterPreprocessor", "MiDaS-DepthMapPreprocessor") or "DepthAnythingV2"
    classes["guide_node"] = _class_exists(info, "LTXVAddGuide", "LTXVAddGuideMulti") or "LTXVAddGuide"
    available = dict(base.get("available", {}))
    available.update({key: classes[key] in info for key in ("load_video", "frame_picker", "depth_node", "guide_node")})
    return {**base, "classes": classes, "available": available, "phase": "V19"}


def _frame_picker_inputs(required: dict[str, Any]) -> dict[str, Any]:
    image_key = _field(required, ("images", "image", "frames"), "images")
    inputs: dict[str, Any] = {image_key: ["0", 0]}
    _set_if_supported(inputs, required, "batch_index", 0)
    _set_if_supported(inputs, required, "index", 0)
    return inputs


def _depth_inputs(required: dict[str, Any], req: LtxDepthMotionCompileRequest) -> dict[str, Any]:
    image_key = _field(required, ("image", "images", "input", "frames"), "image")
    inputs: dict[str, Any] = {image_key: ["1", 0]}
    _set_if_supported(inputs, required, "model_name", "depth_anything_v2_vitl.pth")
    _set_if_supported(inputs, required, "device", "auto")
    _set_if_supported(inputs, required, "resolution", int(req.height or 512))
    return inputs


def _guide_inputs(required: dict[str, Any], strength: float) -> dict[str, Any]:
    inputs: dict[str, Any] = {"positive": ["8", 0], "negative": ["8", 1], "vae": ["5", 0], "latent": ["8", 2]}
    image_key = _field(required, ("image", "guide", "control_image", "image_1", "num_guides.image_1"), "image")
    inputs[image_key] = ["2", 0]
    strength_key = _field(required, ("strength", "guide_strength", "num_guides.strength_1"), "strength")
    inputs[strength_key] = strength
    if "frame_idx" in required:
        inputs["frame_idx"] = 0
    if "num_guides.frame_idx_1" in required:
        inputs["num_guides.frame_idx_1"] = 0
    return inputs


def build_ltx23_depth_motion_workflow(req: LtxDepthMotionCompileRequest, source: dict[str, Any], object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    loader = normalize_video_loader(req.loader)
    bindings = discover_depth_motion_bindings(loader, info)
    classes = bindings["classes"]
    params = _txt2vid_vram_safety(req)  # type: ignore[arg-type]
    control_strength = _clamp01(req.control_strength, 0.65)
    motion_strength = _clamp01(req.motion_strength, 0.8)
    prompt = req.prompt.strip() or "Generate a video guided by the extracted depth and motion structure from the source video."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    prefix = sanitize_path_part(req.filename_prefix, "Neo_Video_LTX23_DepthMotion")
    load_video_required = _required_inputs(info, classes["load_video"])
    model_required = _required_inputs(info, classes["model_loader"])
    clip_required = _required_inputs(info, classes["clip_loader"])
    vae_required = _required_inputs(info, classes["vae_loader"])
    frame_required = _required_inputs(info, classes["frame_picker"])
    depth_required = _required_inputs(info, classes["depth_node"])
    guide_required = _required_inputs(info, classes["guide_node"])
    decode_required = _required_inputs(info, classes["decode_node"])
    scheduler_inputs = {"latent": ["9", 0], "steps": int(params["steps"]), "max_shift": 2.05, "base_shift": 0.95, "stretch": True, "terminal": 0.1}
    sampler_select_inputs = {"sampler_name": req.sampler or params["sampler"]}
    latent_inputs = {"width": int(params["width"]), "height": int(params["height"]), "length": int(params["frames"]), "batch_size": 1}
    conditioning_inputs = {"positive": ["6", 0], "negative": ["7", 0], "frame_rate": float(params["fps"])}
    decode_inputs = {"samples": ["13", 0], "vae": ["5", 0]}
    if classes["decode_node"].lower().endswith("tiled") or "tile_size" in decode_required:
        _set_if_supported(decode_inputs, decode_required, "tile_size", int(req.tile_size or params.get("tile_size", 512)))
        _set_if_supported(decode_inputs, decode_required, "overlap", 64)
        _set_if_supported(decode_inputs, decode_required, "temporal_size", int(req.temporal_tile_size or params.get("temporal_tile_size", 4096)))
        _set_if_supported(decode_inputs, decode_required, "temporal_overlap", 8)
    saver_inputs = _saver_inputs(classes["saver"], prefix, float(params["fps"]))
    if "images" in saver_inputs:
        saver_inputs["images"] = ["18", 0]
    workflow: dict[str, Any] = {
        "0": {"class_type": classes["load_video"], "inputs": _load_video_inputs(load_video_required, source)},
        "1": {"class_type": classes["frame_picker"], "inputs": _frame_picker_inputs(frame_required)},
        "2": {"class_type": classes["depth_node"], "inputs": _depth_inputs(depth_required, req)},
        "3": {"class_type": classes["model_loader"], "inputs": _model_loader_inputs(req, bindings, info)},
        "4": {"class_type": classes["clip_loader"], "inputs": _clip_loader_inputs(req, bindings, info)},
        "5": {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(req, bindings, info)},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": prompt}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 0], "text": negative}},
        "8": {"class_type": "LTXVConditioning", "inputs": conditioning_inputs},
        "9": {"class_type": "EmptyLTXVLatentVideo", "inputs": latent_inputs},
        "10": {"class_type": "LTXVCropGuides", "inputs": {"positive": ["8", 0], "negative": ["8", 1], "latent": ["9", 0]}},
        "11": {"class_type": classes["guide_node"], "inputs": _guide_inputs(guide_required, control_strength)},
        "12": {"class_type": classes["chunk_node"], "inputs": {"model": ["3", 0], "chunks": int(req.chunk_feed_forward or params.get("chunk_feed_forward", 2)), "dim_threshold": 4096}},
        "13": {"class_type": classes["sampler_node"], "inputs": {"noise": ["15", 0], "guider": ["14", 0], "sampler": ["16", 0], "sigmas": ["17", 0], "latent_image": ["11", 2]}},
        "14": {"class_type": "CFGGuider", "inputs": {"model": ["12", 0], "positive": ["11", 0], "negative": ["11", 1], "cfg": float(params["guidance"])}},
        "15": {"class_type": "RandomNoise", "inputs": {"noise_seed": int(params["seed"])}},
        "16": {"class_type": "KSamplerSelect", "inputs": sampler_select_inputs},
        "17": {"class_type": "LTXVScheduler", "inputs": scheduler_inputs},
        "18": {"class_type": classes["decode_node"], "inputs": decode_inputs},
        "19": {"class_type": classes["saver"], "inputs": saver_inputs},
    }
    return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V19", "route_id": f"ltx23.{loader}.depth_motion", "compiled_at": _now(), "parameters": {**{k: v for k, v in params.items() if k != "profile"}, "control_type": req.control_type, "control_strength": control_strength, "motion_strength": motion_strength, "depth_engine": req.depth_engine, "frame_load_cap": req.frame_load_cap or 0}, "profile": params["profile"], "vram_engine": params.get("vram_engine", {}), "bindings": bindings, "workflow": workflow, "prompt_api_payload": {"prompt": workflow}, "source": {**source, "required": True, "control_type": req.control_type, "control_strength": control_strength}, "rules": ["V19 Depth / Motion Control extracts guidance from a Neo-owned source video.", "The source video is never modified; output is a child result under neo_data/outputs/video/depth_motion.", "This first lane targets LTX 2.3 GGUF and UNET/Diffusion routes only.", "Depth and motion control nodes are detected from external packs; missing packs keep the route in ready-with-warnings/missing-nodes state."]}


def video_ltx23_depth_motion_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxDepthMotionCompileRequest.from_payload(payload)
    nf, nl, nt = normalize_video_family(req.family), normalize_video_loader(req.loader), normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V19", "ok": False, "queued": False, "error": f"V19 Depth / Motion Control compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.", "request": {"family": nf, "loader": nl, "generation_type": nt}, "route": route.payload() if route else None}
    source = resolve_depth_motion_source(req)
    if not source.get("ok"):
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V19", "ok": False, "queued": False, "error": source.get("error") or "Depth / Motion Control source video is missing.", "request": req.payload(), "source": source, "route": route.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX Depth / Motion bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_ltx23_depth_motion_workflow(req, source, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("depth_motion", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_depth_motion')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_depth_motion_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxDepthMotionCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_depth_motion_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok") or req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    if object_info_override is not None and not compile_payload.get("route_readiness", {}).get("ready"):
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 Depth / Motion Control route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {**compile_payload, "ok": True, "queued": True, "dry_run": False, "queue_response": queue_response, "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or ""}
    return _attach_video_output_record(response_payload, req.payload())
