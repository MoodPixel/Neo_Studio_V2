from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import json
from typing import Any, Final
from urllib.error import HTTPError, URLError

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.ltx_txt2vid_compiler import (
    DEFAULT_NEGATIVE_PROMPT,
    FALLBACK_LTX_MODELS,
    _apply_vram_safety as _txt2vid_vram_safety,
    _class_exists,
    _clip_loader_inputs,
    _combo_values,
    _field_name,
    _first_matching,
    _model_loader_inputs,
    _now,
    _post_json,
    _required_inputs,
    _saver_inputs,
    _seed,
    _set_if_supported,
    _vae_loader_inputs,
)
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import register_video_generation_result
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.img2vid.compiler.v9"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.img2vid"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.img2vid"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)


@dataclass(frozen=True)
class LtxImg2VidCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "img2vid"
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
    filename_prefix: str = "Neo_Video_LTX23_I2V"
    source_image: str | None = None
    source_image_name: str | None = None
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
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxImg2VidCompileRequest":
        data = payload or {}
        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "img2vid")) or "img2vid"),
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
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_I2V") or "Neo_Video_LTX23_I2V"),
            source_image=str(data.get("source_image", data.get("image", "")) or "") or None,
            source_image_name=str(data.get("source_image_name", data.get("image_name", "")) or "") or None,
            image_strength=_float_or_none(data.get("image_strength", data.get("denoise"))),
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


def _comfy_image_name(path_or_name: str | None, display_name: str | None = None) -> str:
    raw = str(display_name or path_or_name or "").strip().replace("\\", "/")
    if not raw:
        return ""
    return raw.rsplit("/", 1)[-1]


def discover_ltx23_img2vid_bindings(loader: str = "gguf", object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    normalized_loader = normalize_video_loader(loader)
    if normalized_loader == "gguf":
        model_loader = _class_exists(info, "UnetLoaderGGUF", "UNETLoaderGGUF", "DiffusionModelLoaderKJ", "UNETLoader", "DiffusionModelLoader") or "UnetLoaderGGUF"
        clip_loader = _class_exists(info, "DualCLIPLoaderGGUF", "DualCLIPLoader") or "DualCLIPLoaderGGUF"
    else:
        model_loader = _class_exists(info, "UNETLoader", "DiffusionModelLoaderKJ", "DiffusionModelLoader") or "UNETLoader"
        clip_loader = _class_exists(info, "DualCLIPLoader", "CLIPLoader") or "DualCLIPLoader"
    vae_loader = _class_exists(info, "VAELoaderKJ", "VAELoader") or "VAELoaderKJ"
    return {
        "loader": normalized_loader,
        "classes": {
            "model_loader": model_loader,
            "clip_loader": clip_loader,
            "vae_loader": vae_loader,
            "load_image": _class_exists(info, "LoadImage") or "LoadImage",
            "guide_node": _class_exists(info, "LTXVAddGuide", "LTXVAddGuideMulti") or "LTXVAddGuide",
            "crop_node": _class_exists(info, "LTXVCropGuides") or "LTXVCropGuides",
            "chunk_node": _class_exists(info, "LTXVChunkFeedForward") or "LTXVChunkFeedForward",
            "latent_node": _class_exists(info, "EmptyLTXVLatentVideo") or "EmptyLTXVLatentVideo",
            "condition_node": _class_exists(info, "LTXVConditioning") or "LTXVConditioning",
            "scheduler_node": _class_exists(info, "LTXVScheduler") or "LTXVScheduler",
            "sampler_node": _class_exists(info, "SamplerCustomAdvanced") or "SamplerCustomAdvanced",
            "noise_node": _class_exists(info, "RandomNoise") or "RandomNoise",
            "guider_node": _class_exists(info, "CFGGuider") or "CFGGuider",
            "sampler_select_node": _class_exists(info, "KSamplerSelect") or "KSamplerSelect",
            "decode_node": _class_exists(info, "VAEDecodeTiled", "VAEDecode") or "VAEDecodeTiled",
            "saver": _class_exists(info, "SaveWEBM", "VHS_VideoCombine", "VideoCombine", "SaveAnimatedWEBP") or "SaveWEBM",
        },
        "models": _discover_models(normalized_loader, model_loader, clip_loader, vae_loader, info),
    }


def _discover_models(loader: str, model_loader: str, clip_loader: str, vae_loader: str, info: dict[str, Any]) -> dict[str, str]:
    model_values = _combo_values(info, model_loader, "unet_name") or _combo_values(info, model_loader, "model_name") or _combo_values(info, model_loader, "ckpt_name")
    clip1_values = _combo_values(info, clip_loader, "clip_name1") or _combo_values(info, clip_loader, "clip_name")
    clip2_values = _combo_values(info, clip_loader, "clip_name2") or _combo_values(info, clip_loader, "text_projection")
    vae_values = _combo_values(info, vae_loader, "vae_name") or _combo_values(info, vae_loader, "ckpt_name")
    return {
        "model_name": _first_matching(model_values, ("ltx-2.3", "ltx2.3", "ltx", "Q5", "gguf") if loader == "gguf" else ("ltx-2.3", "ltx2.3", "ltx", "fp8"), FALLBACK_LTX_MODELS["gguf_model" if loader == "gguf" else "unet_model"]),
        "clip_name1": _first_matching(clip1_values, ("gemma", "t5", "ltx"), FALLBACK_LTX_MODELS["clip_name1_gguf" if loader == "gguf" else "clip_name1_unet"]),
        "clip_name2": _first_matching(clip2_values, ("projection", "connector", "embedding", "ltx"), FALLBACK_LTX_MODELS["clip_name2"]),
        "vae_name": _first_matching(vae_values, ("ltx23", "ltx2.3", "ltx", "video"), FALLBACK_LTX_MODELS["vae"]),
    }


def _guide_inputs(guide_class: str, required: dict[str, Any], strength: float) -> dict[str, Any]:
    if guide_class == "LTXVAddGuideMulti" or any(str(key).startswith("num_guides.") for key in required):
        inputs: dict[str, Any] = {"positive": ["16", 0], "negative": ["16", 1], "vae": ["3", 0], "latent": ["16", 2]}
        inputs["num_guides.image_1"] = ["0", 0]
        inputs["num_guides.strength_1"] = strength
        if "num_guides.frame_idx_1" in required:
            inputs["num_guides.frame_idx_1"] = 0
        return inputs
    inputs = {"positive": ["16", 0], "negative": ["16", 1], "vae": ["3", 0], "latent": ["16", 2], "image": ["0", 0], "strength": strength}
    _set_if_supported(inputs, required, "frame_idx", 0)
    _set_if_supported(inputs, required, "frame_index", 0)
    return inputs


def build_ltx23_img2vid_workflow(req: LtxImg2VidCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    loader = normalize_video_loader(req.loader)
    bindings = discover_ltx23_img2vid_bindings(loader, info)
    classes = bindings["classes"]
    # Reuse V8 LTX safety defaults but force img2vid in the request object.
    params = _txt2vid_vram_safety(req)  # type: ignore[arg-type]
    image_strength = float(req.image_strength if req.image_strength is not None else params.get("image_strength", 0.7))
    image_strength = max(0.0, min(image_strength, 1.0))
    prompt = req.prompt.strip() or "Animate the source image with smooth cinematic motion."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    prefix = sanitize_path_part(req.filename_prefix or "Neo_Video_LTX23_I2V", fallback="Neo_Video_LTX23_I2V")

    latent_required = _required_inputs(info, classes["latent_node"])
    latent_inputs = {"width": params["width"], "height": params["height"], "length": params["frames"], "batch_size": 1}
    if "batch_size" not in latent_required and latent_required:
        latent_inputs.pop("batch_size", None)

    scheduler_required = _required_inputs(info, classes["scheduler_node"])
    scheduler_inputs = {"latent": ["17", 0]}
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
        "0": {"class_type": classes["load_image"], "inputs": {"image": _comfy_image_name(req.source_image, req.source_image_name)}},
        "1": {"class_type": classes["model_loader"], "inputs": _model_loader_inputs(req, bindings, info)},
        "2": {"class_type": classes["clip_loader"], "inputs": _clip_loader_inputs(req, bindings, info)},
        "3": {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(req, bindings, info)},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": classes["condition_node"], "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(params["fps"])}},
        "7": {"class_type": classes["latent_node"], "inputs": latent_inputs},
        "8": {"class_type": classes["chunk_node"], "inputs": {"model": ["1", 0], "chunks": params["chunk_feed_forward"], "dim_threshold": 4096}},
        "16": {"class_type": classes["crop_node"], "inputs": {"positive": ["6", 0], "negative": ["6", 1], "latent": ["7", 0]}},
        "17": {"class_type": classes["guide_node"], "inputs": _guide_inputs(classes["guide_node"], guide_required, image_strength)},
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
        "phase": "V9",
        "route_id": f"ltx23.{loader}.img2vid",
        "compiled_at": _now(),
        "parameters": {**{key: value for key, value in params.items() if key != "profile"}, "image_strength": image_strength},
        "profile": params["profile"],
        "vram_engine": params.get("vram_engine", {}),
        "bindings": bindings,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "source": {"required": True, "source_image": req.source_image or "", "source_image_name": req.source_image_name or "", "comfy_image_name": _comfy_image_name(req.source_image, req.source_image_name), "image_strength": image_strength, "resize_mode": req.resize_mode},
        "rules": [
            "LTX 2.3 Img2Vid is active for GGUF and UNET/Diffusion routes in V9.",
            "A Video source image is required before compile/queue.",
            "Source image guidance is applied through LTX guide/crop nodes when available.",
            "Batch count stays locked to 1 and tiled decode remains the default low/mid-VRAM safeguard.",
        ],
    }


def _attach_video_output_record(result: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        ledger = register_video_generation_result(result, request=request_payload or {})
    except Exception as exc:  # noqa: BLE001
        return {**result, "neo_persisted": {"ok": False, "error": f"Video output ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def video_ltx23_img2vid_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxImg2VidCompileRequest.from_payload(payload)
    nf = normalize_video_family(req.family)
    nl = normalize_video_loader(req.loader)
    nt = normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V9", "ok": False, "queued": False, "error": f"V9 LTX Img2Vid compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.", "request": {"family": nf, "loader": nl, "generation_type": nt}, "route": route.payload() if route else None}
    if not req.source_image:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V9", "ok": False, "queued": False, "error": "LTX Img2Vid requires a source_image from the Video source upload/select panel.", "request": req.payload(), "route": route.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX Img2Vid bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_ltx23_img2vid_workflow(req, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("img2vid", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_i2v')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_img2vid_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxImg2VidCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_img2vid_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    route_ready = compile_payload.get("route_readiness", {}).get("ready")
    if object_info_override is not None and not route_ready:
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 Img2Vid route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {**compile_payload, "ok": True, "queued": True, "dry_run": False, "queue_response": queue_response, "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or ""}
    return _attach_video_output_record(response_payload, req.payload())
