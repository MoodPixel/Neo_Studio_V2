from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any, Final
from urllib.error import HTTPError, URLError

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.first_last_frame_compiler import _attach_video_output_record, _clamp01
from neo_app.video.ltx_img2vid_compiler import _comfy_image_name, discover_ltx23_img2vid_bindings
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
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.multiscene.compiler.v16"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.multiscene"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.multiscene"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)
MAX_SEGMENTS: Final[int] = 4


@dataclass(frozen=True)
class MultiSceneSegment:
    image: str = ""
    image_name: str = ""
    prompt: str = ""
    frames: int = 24
    strength: float = 0.7

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None, index: int) -> "MultiSceneSegment":
        data = payload or {}
        return cls(
            image=str(data.get("image") or data.get("source_image") or ""),
            image_name=str(data.get("image_name") or data.get("source_image_name") or ""),
            prompt=str(data.get("prompt") or data.get("positive_prompt") or ""),
            frames=max(1, _int_or_none(data.get("frames")) or 24),
            strength=_clamp01(_float_or_none(data.get("strength")), 0.7),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LtxMultiSceneCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "multiscene"
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
    filename_prefix: str = "Neo_Video_LTX23_MultiScene"
    segments: tuple[MultiSceneSegment, ...] = ()
    image_strength: float | None = None
    resize_mode: str = "fit_crop"
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
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxMultiSceneCompileRequest":
        data = payload or {}
        raw_segments = data.get("segments") if isinstance(data.get("segments"), list) else []
        segments = [MultiSceneSegment.from_payload(item if isinstance(item, dict) else {}, idx) for idx, item in enumerate(raw_segments[:MAX_SEGMENTS], start=1)]
        # Backward-compatible flat source fields for quick tests/UI fallbacks.
        if not segments:
            for idx in range(1, MAX_SEGMENTS + 1):
                image = data.get(f"segment_{idx}_image") or data.get(f"image_seg{idx}") or data.get(f"source_image_{idx}")
                if image:
                    segments.append(MultiSceneSegment.from_payload({
                        "image": image,
                        "image_name": data.get(f"segment_{idx}_image_name") or data.get(f"image_seg{idx}_name") or data.get(f"source_image_{idx}_name") or "",
                        "prompt": data.get(f"segment_{idx}_prompt") or "",
                        "frames": data.get(f"segment_{idx}_frames") or data.get("segment_frames") or 24,
                        "strength": data.get(f"segment_{idx}_strength") or data.get("image_strength") or 0.7,
                    }, idx))
        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "multiscene")) or "multiscene"),
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
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_MultiScene") or "Neo_Video_LTX23_MultiScene"),
            segments=tuple(segments),
            image_strength=_float_or_none(data.get("image_strength")),
            resize_mode=str(data.get("resize_mode", "fit_crop") or "fit_crop"),
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
        data = asdict(self)
        data["segments"] = [segment.payload() if hasattr(segment, "payload") else dict(segment) for segment in self.segments]
        return data


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


def _segment_frame_indexes(segments: tuple[MultiSceneSegment, ...], total_frames: int) -> list[int]:
    indexes: list[int] = []
    cursor = 0
    for index, segment in enumerate(segments):
        if index == 0:
            indexes.append(0)
        else:
            indexes.append(min(max(cursor, 0), max(total_frames - 1, 0)))
        cursor += max(1, int(segment.frames or 1))
    if indexes:
        indexes[-1] = min(indexes[-1], max(total_frames - 1, 0))
    return indexes


def _guide_multi_inputs(required: dict[str, Any], params: dict[str, Any], segments: tuple[MultiSceneSegment, ...]) -> dict[str, Any]:
    inputs: dict[str, Any] = {"positive": ["16", 0], "negative": ["16", 1], "vae": ["3", 0], "latent": ["16", 2]}
    frame_indexes = _segment_frame_indexes(segments, int(params["frames"]))
    if any(str(key).startswith("num_guides.") for key in required) or not required:
        for idx, segment in enumerate(segments, start=1):
            node_id = str(30 + idx)
            inputs[f"num_guides.image_{idx}"] = [node_id, 0]
            inputs[f"num_guides.strength_{idx}"] = _clamp01(segment.strength, _clamp01(params.get("image_strength"), 0.7))
            if idx > 1 or f"num_guides.frame_idx_{idx}" in required:
                inputs[f"num_guides.frame_idx_{idx}"] = frame_indexes[idx - 1]
        return inputs
    # Conservative fallback: first image only where custom nodes expose a simpler LTXVAddGuide contract.
    inputs.update({"image": ["31", 0], "strength": _clamp01(segments[0].strength if segments else None, 0.7)})
    return inputs


def build_ltx23_multiscene_workflow(req: LtxMultiSceneCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    loader = normalize_video_loader(req.loader)
    bindings = discover_ltx23_img2vid_bindings(loader, info)
    classes = dict(bindings["classes"])
    classes["guide_node"] = "LTXVAddGuideMulti" if "LTXVAddGuideMulti" in info else classes.get("guide_node") or "LTXVAddGuideMulti"
    if classes["guide_node"] == "LTXVAddGuide":
        classes["guide_node"] = "LTXVAddGuideMulti"
    total_segment_frames = sum(max(1, int(segment.frames or 1)) for segment in req.segments)
    req_for_safety = LtxMultiSceneCompileRequest(**{**req.payload(), "frames": req.frames or total_segment_frames})  # type: ignore[arg-type]
    params = _txt2vid_vram_safety(req_for_safety)  # type: ignore[arg-type]
    params["frames"] = min(int(params.get("frames") or total_segment_frames), max(1, total_segment_frames)) if req.frames is None else int(params["frames"])
    params["image_strength"] = _clamp01(req.image_strength, 0.7)
    prompt_parts = [req.prompt.strip()] if req.prompt.strip() else []
    prompt_parts.extend([seg.prompt.strip() for seg in req.segments if seg.prompt.strip()])
    prompt = "\n".join(prompt_parts).strip() or "Create a coherent cinematic multi-scene video using the supplied image guides in sequence."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    prefix = sanitize_path_part(req.filename_prefix or "Neo_Video_LTX23_MultiScene", fallback="Neo_Video_LTX23_MultiScene")

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

    guide_required = _required_inputs(info, classes["guide_node"])
    workflow: dict[str, Any] = {
        "1": {"class_type": classes["model_loader"], "inputs": _model_loader_inputs(req, bindings, info)},
        "2": {"class_type": classes["clip_loader"], "inputs": _clip_loader_inputs(req, bindings, info)},
        "3": {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(req, bindings, info)},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": classes["condition_node"], "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(params["fps"])}},
        "7": {"class_type": classes["latent_node"], "inputs": latent_inputs},
        "8": {"class_type": classes["chunk_node"], "inputs": {"model": ["1", 0], "chunks": params["chunk_feed_forward"], "dim_threshold": 4096}},
        "16": {"class_type": classes["crop_node"], "inputs": {"positive": ["6", 0], "negative": ["6", 1], "latent": ["7", 0]}},
        "17": {"class_type": classes["guide_node"], "inputs": _guide_multi_inputs(guide_required, params, req.segments)},
        "9": {"class_type": classes["guider_node"], "inputs": {"model": ["8", 0], "positive": ["17", 0], "negative": ["17", 1], "cfg": params["guidance"]}},
        "10": {"class_type": classes["sampler_select_node"], "inputs": {"sampler_name": params["sampler"]}},
        "11": {"class_type": classes["sampler_node"], "inputs": {"noise": ["13", 0], "guider": ["9", 0], "sampler": ["10", 0], "sigmas": ["14", 0], "latent_image": ["17", 2]}},
        "12": {"class_type": classes["decode_node"], "inputs": decode_inputs},
        "13": {"class_type": classes["noise_node"], "inputs": {"noise_seed": params["seed"]}},
        "14": {"class_type": classes["scheduler_node"], "inputs": scheduler_inputs},
        "15": {"class_type": classes["saver"], "inputs": _saver_inputs(classes["saver"], prefix, float(params["fps"]))},
    }
    for idx, segment in enumerate(req.segments, start=1):
        workflow[str(30 + idx)] = {"class_type": classes["load_image"], "inputs": {"image": _comfy_image_name(segment.image, segment.image_name)}}

    frame_indexes = _segment_frame_indexes(req.segments, int(params["frames"]))
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": "V16",
        "route_id": f"ltx23.{loader}.multiscene",
        "compiled_at": _now(),
        "parameters": {**{key: value for key, value in params.items() if key != "profile"}, "segment_count": len(req.segments), "segment_frame_indexes": frame_indexes},
        "profile": params["profile"],
        "vram_engine": params.get("vram_engine", {}),
        "bindings": bindings,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "source": {"required": True, "segments": [segment.payload() | {"comfy_image_name": _comfy_image_name(segment.image, segment.image_name), "frame_index": frame_indexes[idx]} for idx, segment in enumerate(req.segments)], "resize_mode": req.resize_mode},
        "rules": [
            "V16 MultiScene uses 2-4 Video source images as sequential LTXVAddGuideMulti guides.",
            "Segment frame counts are summed into the route duration unless a total frames override is supplied.",
            "This lane is active for LTX 2.3 GGUF and UNET/Diffusion routes only; WAN stays guarded.",
            "Batch count stays locked to 1 and tiled decode remains the default low/mid-VRAM safeguard.",
        ],
    }


def video_ltx23_multiscene_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxMultiSceneCompileRequest.from_payload(payload)
    nf = normalize_video_family(req.family)
    nl = normalize_video_loader(req.loader)
    nt = normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V16", "ok": False, "queued": False, "error": f"V16 MultiScene compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.", "request": {"family": nf, "loader": nl, "generation_type": nt}, "route": route.payload() if route else None}
    if len(req.segments) < 2:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V16", "ok": False, "queued": False, "error": "MultiScene requires at least two segment images from the Video source panel.", "request": req.payload(), "route": route.payload()}
    missing = [idx for idx, segment in enumerate(req.segments, start=1) if not segment.image]
    if missing:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V16", "ok": False, "queued": False, "error": f"MultiScene segment images missing for segment(s): {', '.join(map(str, missing))}.", "request": req.payload(), "route": route.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX MultiScene bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_ltx23_multiscene_workflow(req, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("multiscene", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_multiscene')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_multiscene_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxMultiSceneCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_multiscene_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    route_ready = compile_payload.get("route_readiness", {}).get("ready")
    if object_info_override is not None and not route_ready:
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 MultiScene route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {**compile_payload, "ok": True, "queued": True, "dry_run": False, "queue_response": queue_response, "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or ""}
    return _attach_video_output_record(response_payload, req.payload())
