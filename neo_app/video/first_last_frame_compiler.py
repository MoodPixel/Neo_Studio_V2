from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any, Final
from urllib.error import HTTPError, URLError

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.ltx_img2vid_compiler import (
    _comfy_image_name,
    _discover_models,
    discover_ltx23_img2vid_bindings,
)
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
from neo_app.video.output_records import register_video_generation_result
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.first_last_frame.compiler.v15"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.first_last_frame"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.first_last_frame"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)


@dataclass(frozen=True)
class LtxFirstLastFrameCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "first_last_frame"
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
    filename_prefix: str = "Neo_Video_LTX23_FLF"
    first_image: str | None = None
    first_image_name: str | None = None
    last_image: str | None = None
    last_image_name: str | None = None
    first_strength: float | None = None
    last_strength: float | None = None
    transition_strength: float | None = None
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
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxFirstLastFrameCompileRequest":
        data = payload or {}
        first_image = data.get("first_image") or data.get("start_image") or data.get("source_image") or data.get("image") or ""
        last_image = data.get("last_image") or data.get("end_image") or data.get("target_image") or ""
        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "first_last_frame")) or "first_last_frame"),
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
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_FLF") or "Neo_Video_LTX23_FLF"),
            first_image=str(first_image or "") or None,
            first_image_name=str(data.get("first_image_name", data.get("start_image_name", data.get("source_image_name", data.get("image_name", "")))) or "") or None,
            last_image=str(last_image or "") or None,
            last_image_name=str(data.get("last_image_name", data.get("end_image_name", "")) or "") or None,
            first_strength=_float_or_none(data.get("first_strength")),
            last_strength=_float_or_none(data.get("last_strength")),
            transition_strength=_float_or_none(data.get("transition_strength", data.get("image_strength", data.get("denoise")))),
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


def _clamp01(value: float | None, fallback: float) -> float:
    raw = fallback if value is None else float(value)
    return max(0.0, min(raw, 1.0))


def _guide_multi_inputs(required: dict[str, Any], params: dict[str, Any], first_strength: float, last_strength: float) -> dict[str, Any]:
    last_frame_idx = max(0, int(params["frames"]) - 1)
    inputs: dict[str, Any] = {"positive": ["16", 0], "negative": ["16", 1], "vae": ["3", 0], "latent": ["16", 2]}
    # LTXVAddGuideMulti style keys from KJNodes/LTX workflows.
    if any(str(key).startswith("num_guides.") for key in required) or not required:
        inputs.update({
            "num_guides.image_1": ["0", 0],
            "num_guides.strength_1": first_strength,
            "num_guides.image_2": ["18", 0],
            "num_guides.frame_idx_2": last_frame_idx,
            "num_guides.strength_2": last_strength,
        })
        if "num_guides.frame_idx_1" in required:
            inputs["num_guides.frame_idx_1"] = 0
        return inputs
    # Fallback for two-guide nodes with simpler names.
    inputs.update({"image": ["0", 0], "strength": first_strength})
    for key in ("end_image", "last_image", "image_2"):
        if key in required:
            inputs[key] = ["18", 0]
    for key in ("end_strength", "last_strength", "strength_2"):
        if key in required:
            inputs[key] = last_strength
    for key in ("end_frame_idx", "last_frame_idx", "frame_idx_2"):
        if key in required:
            inputs[key] = last_frame_idx
    return inputs


def build_ltx23_first_last_frame_workflow(req: LtxFirstLastFrameCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    loader = normalize_video_loader(req.loader)
    bindings = discover_ltx23_img2vid_bindings(loader, info)
    classes = dict(bindings["classes"])
    classes["guide_node"] = ("LTXVAddGuideMulti" if "LTXVAddGuideMulti" in info else classes.get("guide_node") or "LTXVAddGuideMulti")
    if classes["guide_node"] == "LTXVAddGuide":
        classes["guide_node"] = "LTXVAddGuideMulti"
    params = _txt2vid_vram_safety(req)  # type: ignore[arg-type]
    transition_strength = _clamp01(req.transition_strength, 0.75)
    first_strength = _clamp01(req.first_strength, transition_strength)
    last_strength = _clamp01(req.last_strength, transition_strength)
    prompt = req.prompt.strip() or "Create a smooth cinematic transition from the first frame into the last frame."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    prefix = sanitize_path_part(req.filename_prefix or "Neo_Video_LTX23_FLF", fallback="Neo_Video_LTX23_FLF")

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
    workflow = {
        "0": {"class_type": classes["load_image"], "inputs": {"image": _comfy_image_name(req.first_image, req.first_image_name)}},
        "18": {"class_type": classes["load_image"], "inputs": {"image": _comfy_image_name(req.last_image, req.last_image_name)}},
        "1": {"class_type": classes["model_loader"], "inputs": _model_loader_inputs(req, bindings, info)},
        "2": {"class_type": classes["clip_loader"], "inputs": _clip_loader_inputs(req, bindings, info)},
        "3": {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(req, bindings, info)},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": classes["condition_node"], "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(params["fps"])}},
        "7": {"class_type": classes["latent_node"], "inputs": latent_inputs},
        "8": {"class_type": classes["chunk_node"], "inputs": {"model": ["1", 0], "chunks": params["chunk_feed_forward"], "dim_threshold": 4096}},
        "16": {"class_type": classes["crop_node"], "inputs": {"positive": ["6", 0], "negative": ["6", 1], "latent": ["7", 0]}},
        "17": {"class_type": classes["guide_node"], "inputs": _guide_multi_inputs(guide_required, params, first_strength, last_strength)},
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
        "phase": "V15",
        "route_id": f"ltx23.{loader}.first_last_frame",
        "compiled_at": _now(),
        "parameters": {**{key: value for key, value in params.items() if key != "profile"}, "first_strength": first_strength, "last_strength": last_strength, "transition_strength": transition_strength},
        "profile": params["profile"],
        "vram_engine": params.get("vram_engine", {}),
        "bindings": bindings,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "source": {
            "required": True,
            "first_image": req.first_image or "",
            "first_image_name": req.first_image_name or "",
            "last_image": req.last_image or "",
            "last_image_name": req.last_image_name or "",
            "first_comfy_image_name": _comfy_image_name(req.first_image, req.first_image_name),
            "last_comfy_image_name": _comfy_image_name(req.last_image, req.last_image_name),
            "resize_mode": req.resize_mode,
        },
        "rules": [
            "V15 First/Last Frame uses two Video source images and creates a controlled transition.",
            "The first image is guided at frame 0 and the last image is guided at the final frame index.",
            "This lane is active for LTX 2.3 GGUF and UNET/Diffusion routes only; WAN stays guarded because the current WAN route only has a start-image latent.",
            "Batch count stays locked to 1 and tiled decode remains the default low/mid-VRAM safeguard.",
        ],
    }


def _attach_video_output_record(result: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        ledger = register_video_generation_result(result, request=request_payload or {})
    except Exception as exc:  # noqa: BLE001
        return {**result, "neo_persisted": {"ok": False, "error": f"Video output ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def video_ltx23_first_last_frame_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxFirstLastFrameCompileRequest.from_payload(payload)
    nf = normalize_video_family(req.family)
    nl = normalize_video_loader(req.loader)
    nt = normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V15", "ok": False, "queued": False, "error": f"V15 First/Last Frame compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.", "request": {"family": nf, "loader": nl, "generation_type": nt}, "route": route.payload() if route else None}
    if not req.first_image or not req.last_image:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V15", "ok": False, "queued": False, "error": "First/Last Frame requires first_image and last_image from the Video source upload/select panel.", "request": req.payload(), "route": route.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX First/Last Frame bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_ltx23_first_last_frame_workflow(req, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("first_last_frame", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_flf')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_first_last_frame_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxFirstLastFrameCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_first_last_frame_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    route_ready = compile_payload.get("route_readiness", {}).get("ready")
    if object_info_override is not None and not route_ready:
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 First/Last Frame route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {**compile_payload, "ok": True, "queued": True, "dry_run": False, "queue_response": queue_response, "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or ""}
    return _attach_video_output_record(response_payload, req.payload())
