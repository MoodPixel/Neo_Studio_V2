from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import random
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import register_video_generation_result
from neo_app.video.parameter_profiles import video_parameter_profile_payload
from neo_app.video.vram_engine import apply_video_vram_engine
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.ltx23.compiler.v8"
SUPPORTED_GGUF_ROUTE_ID: Final[str] = "ltx23.gguf.txt2vid"
SUPPORTED_UNET_ROUTE_ID: Final[str] = "ltx23.unet.txt2vid"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_GGUF_ROUTE_ID, SUPPORTED_UNET_ROUTE_ID)
DEFAULT_NEGATIVE_PROMPT: Final[str] = (
    "text, logos, brand names, signage, watermarks, distorted text, jittery motion, chaotic motion, "
    "inconsistent lighting, morphing limbs, blurred face, low resolution, compression artifacts"
)

FALLBACK_LTX_MODELS: Final[dict[str, str]] = {
    "gguf_model": "ltx-2.3-22b-distilled-1.1-Q5_K_M.gguf",
    "unet_model": "ltx-2.3-22b-distilled-1.1_transformer_only_fp8_scaled.safetensors",
    "clip_name1_gguf": "gemma-3-12b-it-IQ4_XS.gguf",
    "clip_name1_unet": "gemma_3_12B_it_fp8_e4m3fn.safetensors",
    "clip_name2": "ltx-2.3_text_projection_bf16.safetensors",
    "vae": "LTX23_video_vae_bf16.safetensors",
}


@dataclass(frozen=True)
class LtxVideoCompileRequest:
    family: str = "ltx23"
    loader: str = "gguf"
    generation_type: str = "txt2vid"
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
    filename_prefix: str = "Neo_Video_LTX23_T2V"
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
    def from_payload(cls, payload: dict[str, Any] | None) -> "LtxVideoCompileRequest":
        data = payload or {}
        return cls(
            family=str(data.get("family", "ltx23") or "ltx23"),
            loader=str(data.get("loader", "gguf") or "gguf"),
            generation_type=str(data.get("generation_type", data.get("mode", "txt2vid")) or "txt2vid"),
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
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_LTX23_T2V") or "Neo_Video_LTX23_T2V"),
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _seed(value: int | None) -> int:
    if value is None or value < 0:
        return random.randint(0, 2_147_483_647)
    return max(0, min(int(value), 9_999_999_999_999))


def _class_exists(object_info: dict[str, Any], *candidates: str) -> str | None:
    folded = {str(key).casefold(): str(key) for key in object_info.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return None


def _required_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    return required if isinstance(required, dict) else {}


def _combo_values(object_info: dict[str, Any], class_type: str, input_name: str) -> list[str]:
    spec = _required_inputs(object_info, class_type).get(input_name)
    if not isinstance(spec, list) or not spec:
        return []
    values = spec[0]
    if isinstance(values, list):
        return [str(item) for item in values]
    return []


def _first_matching(values: list[str], needles: tuple[str, ...], fallback: str) -> str:
    if not values:
        return fallback
    lowered = [(value, value.casefold()) for value in values]
    for needle in needles:
        hit = next((value for value, low in lowered if needle.casefold() in low), None)
        if hit:
            return hit
    return values[0]


def _field_name(required: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    folded = {str(key).casefold(): str(key) for key in required.keys()}
    for candidate in candidates:
        hit = folded.get(candidate.casefold())
        if hit:
            return hit
    return fallback


def _set_if_supported(inputs: dict[str, Any], required: dict[str, Any], key: str, value: Any, *, fallback_ok: bool = False) -> None:
    if fallback_ok or key in required:
        inputs[key] = value


def discover_ltx23_txt2vid_bindings(loader: str = "gguf", object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    normalized_loader = normalize_video_loader(loader)
    if normalized_loader == "gguf":
        model_loader = _class_exists(info, "UnetLoaderGGUF", "UNETLoaderGGUF", "DiffusionModelLoaderKJ", "UNETLoader", "DiffusionModelLoader") or "UnetLoaderGGUF"
        clip_loader = _class_exists(info, "DualCLIPLoaderGGUF", "DualCLIPLoader") or "DualCLIPLoaderGGUF"
    else:
        model_loader = _class_exists(info, "UNETLoader", "DiffusionModelLoaderKJ", "DiffusionModelLoader") or "UNETLoader"
        clip_loader = _class_exists(info, "DualCLIPLoader", "CLIPLoader") or "DualCLIPLoader"
    vae_loader = _class_exists(info, "VAELoaderKJ", "VAELoader") or "VAELoaderKJ"
    chunk_node = _class_exists(info, "LTXVChunkFeedForward") or "LTXVChunkFeedForward"
    latent_node = _class_exists(info, "EmptyLTXVLatentVideo") or "EmptyLTXVLatentVideo"
    condition_node = _class_exists(info, "LTXVConditioning") or "LTXVConditioning"
    scheduler_node = _class_exists(info, "LTXVScheduler") or "LTXVScheduler"
    sampler_node = _class_exists(info, "SamplerCustomAdvanced") or "SamplerCustomAdvanced"
    noise_node = _class_exists(info, "RandomNoise") or "RandomNoise"
    guider_node = _class_exists(info, "CFGGuider") or "CFGGuider"
    sampler_select_node = _class_exists(info, "KSamplerSelect") or "KSamplerSelect"
    decode_node = _class_exists(info, "VAEDecodeTiled", "VAEDecode") or "VAEDecodeTiled"
    saver_node = _class_exists(info, "SaveWEBM", "VHS_VideoCombine", "VideoCombine", "SaveAnimatedWEBP") or "SaveWEBM"

    model_values = (
        _combo_values(info, model_loader, "unet_name")
        or _combo_values(info, model_loader, "model_name")
        or _combo_values(info, model_loader, "ckpt_name")
    )
    clip1_values = _combo_values(info, clip_loader, "clip_name1") or _combo_values(info, clip_loader, "clip_name")
    clip2_values = _combo_values(info, clip_loader, "clip_name2") or _combo_values(info, clip_loader, "text_projection")
    vae_values = _combo_values(info, vae_loader, "vae_name") or _combo_values(info, vae_loader, "ckpt_name")

    return {
        "loader": normalized_loader,
        "classes": {
            "model_loader": model_loader,
            "clip_loader": clip_loader,
            "vae_loader": vae_loader,
            "chunk_node": chunk_node,
            "latent_node": latent_node,
            "condition_node": condition_node,
            "scheduler_node": scheduler_node,
            "sampler_node": sampler_node,
            "noise_node": noise_node,
            "guider_node": guider_node,
            "sampler_select_node": sampler_select_node,
            "decode_node": decode_node,
            "saver": saver_node,
        },
        "models": {
            "model_name": _first_matching(model_values, ("ltx-2.3", "ltx2.3", "ltx", "Q5", "gguf") if normalized_loader == "gguf" else ("ltx-2.3", "ltx2.3", "ltx", "fp8"), FALLBACK_LTX_MODELS["gguf_model" if normalized_loader == "gguf" else "unet_model"]),
            "clip_name1": _first_matching(clip1_values, ("gemma", "t5", "ltx"), FALLBACK_LTX_MODELS["clip_name1_gguf" if normalized_loader == "gguf" else "clip_name1_unet"]),
            "clip_name2": _first_matching(clip2_values, ("projection", "connector", "embedding", "ltx"), FALLBACK_LTX_MODELS["clip_name2"]),
            "vae_name": _first_matching(vae_values, ("ltx23", "ltx2.3", "ltx", "video"), FALLBACK_LTX_MODELS["vae"]),
        },
        "available_model_counts": {"model": len(model_values), "clip1": len(clip1_values), "clip2": len(clip2_values), "vae": len(vae_values)},
    }


def _apply_vram_safety(req: LtxVideoCompileRequest) -> dict[str, Any]:
    engine = apply_video_vram_engine(
        req.payload(),
        family="ltx23",
        loader=req.loader,
        generation_type="txt2vid",
        vram_profile=req.vram_profile,
    )
    values = dict(engine.get("normalized_parameters") or {})
    return {
        "width": max(256, int(values.get("width") or 768)),
        "height": max(256, int(values.get("height") or 512)),
        "frames": max(1, int(values.get("frames") or 97)),
        "fps": float(values.get("fps") or 24),
        "steps": max(1, int(values.get("steps") or 8)),
        "guidance": float(values.get("guidance") if values.get("guidance") is not None else 1),
        "seed": _seed(req.seed if req.seed is not None else values.get("seed")),
        "sampler": str(values.get("sampler") or "euler_ancestral"),
        "scheduler": str(values.get("scheduler") or "ltxv"),
        "batch_count": 1,
        "chunk_feed_forward": max(1, min(int(values.get("chunk_feed_forward") or 2), 8)),
        "tile_size": max(128, int(values.get("tile_size") or 384)),
        "temporal_tile_size": max(1, int(values.get("temporal_tile_size") or 4096)),
        "decode_mode": str(values.get("decode_mode") or "tiled"),
        "tiled_vae_decode": bool(values.get("tiled_vae_decode", True)),
        "profile": engine.get("parameter_profile"),
        "vram_engine": engine,
    }


def _model_loader_inputs(req: LtxVideoCompileRequest, bindings: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    classes = bindings["classes"]
    models = dict(bindings["models"])
    model_name = req.model_name or req.gguf_name or req.unet_name or models["model_name"]
    required = _required_inputs(object_info, classes["model_loader"])
    name_field = _field_name(required, ("unet_name", "model_name", "ckpt_name"), "unet_name" if req.loader == "gguf" else "model_name")
    inputs: dict[str, Any] = {name_field: model_name}
    _set_if_supported(inputs, required, "weight_dtype", "default")
    _set_if_supported(inputs, required, "compute_dtype", "default")
    _set_if_supported(inputs, required, "patch_cublaslinear", False)
    _set_if_supported(inputs, required, "sage_attention", "disabled")
    _set_if_supported(inputs, required, "enable_fp16_accumulation", False)
    return inputs


def _clip_loader_inputs(req: LtxVideoCompileRequest, bindings: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    clip_class = bindings["classes"]["clip_loader"]
    models = dict(bindings["models"])
    required = _required_inputs(object_info, clip_class)
    clip1 = req.clip_name1 or models["clip_name1"]
    clip2 = req.clip_name2 or models["clip_name2"]
    if "clip_name1" in required or "clip_name2" in required or clip_class.casefold().startswith("dual"):
        inputs = {
            _field_name(required, ("clip_name1",), "clip_name1"): clip1,
            _field_name(required, ("clip_name2",), "clip_name2"): clip2,
        }
    else:
        inputs = {_field_name(required, ("clip_name", "text_encoder_name"), "clip_name"): clip1}
    _set_if_supported(inputs, required, "type", "ltxv", fallback_ok=("DualCLIP" in clip_class))
    _set_if_supported(inputs, required, "device", "default")
    return inputs


def _vae_loader_inputs(req: LtxVideoCompileRequest, bindings: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    vae_class = bindings["classes"]["vae_loader"]
    required = _required_inputs(object_info, vae_class)
    inputs = {_field_name(required, ("vae_name", "ckpt_name"), "vae_name"): req.vae_name or bindings["models"]["vae_name"]}
    _set_if_supported(inputs, required, "device", "main_device")
    _set_if_supported(inputs, required, "weight_dtype", "bf16")
    return inputs


def _saver_inputs(saver_class: str, prefix: str, fps: float) -> dict[str, Any]:
    if saver_class == "SaveWEBM":
        return {"images": ["12", 0], "filename_prefix": prefix, "codec": "vp9", "fps": fps, "crf": 20}
    if saver_class == "SaveAnimatedWEBP":
        return {"images": ["12", 0], "filename_prefix": prefix, "fps": fps, "lossless": False, "quality": 90, "method": "default"}
    if saver_class in {"VHS_VideoCombine", "VideoCombine"}:
        return {"images": ["12", 0], "frame_rate": fps, "loop_count": 0, "filename_prefix": prefix, "format": "video/webm", "pix_fmt": "yuv420p", "crf": 20, "save_metadata": True, "pingpong": False, "save_output": True}
    return {"images": ["12", 0], "filename_prefix": prefix, "fps": fps}


def build_ltx23_txt2vid_workflow(req: LtxVideoCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    loader = normalize_video_loader(req.loader)
    bindings = discover_ltx23_txt2vid_bindings(loader, info)
    classes = bindings["classes"]
    params = _apply_vram_safety(req)
    prompt = req.prompt.strip() or "A short cinematic video with smooth natural motion."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    prefix = sanitize_path_part(req.filename_prefix or "Neo_Video_LTX23_T2V", fallback="Neo_Video_LTX23_T2V")

    latent_required = _required_inputs(info, classes["latent_node"])
    latent_inputs = {"width": params["width"], "height": params["height"], "length": params["frames"], "batch_size": 1}
    if "batch_size" not in latent_required and latent_required:
        latent_inputs.pop("batch_size", None)

    scheduler_required = _required_inputs(info, classes["scheduler_node"])
    scheduler_inputs = {"latent": ["7", 0]}
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

    workflow = {
        "1": {"class_type": classes["model_loader"], "inputs": _model_loader_inputs(req, bindings, info)},
        "2": {"class_type": classes["clip_loader"], "inputs": _clip_loader_inputs(req, bindings, info)},
        "3": {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(req, bindings, info)},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": classes["condition_node"], "inputs": {"positive": ["4", 0], "negative": ["5", 0], "frame_rate": float(params["fps"])}},
        "7": {"class_type": classes["latent_node"], "inputs": latent_inputs},
        "8": {"class_type": classes["chunk_node"], "inputs": {"model": ["1", 0], "chunks": params["chunk_feed_forward"], "dim_threshold": 4096}},
        "9": {"class_type": classes["guider_node"], "inputs": {"model": ["8", 0], "positive": ["6", 0], "negative": ["6", 1], "cfg": params["guidance"]}},
        "10": {"class_type": classes["sampler_select_node"], "inputs": {"sampler_name": params["sampler"]}},
        "11": {"class_type": classes["sampler_node"], "inputs": {"noise": ["13", 0], "guider": ["9", 0], "sampler": ["10", 0], "sigmas": ["14", 0], "latent_image": ["7", 0]}},
        "12": {"class_type": classes["decode_node"], "inputs": decode_inputs},
        "13": {"class_type": classes["noise_node"], "inputs": {"noise_seed": params["seed"]}},
        "14": {"class_type": classes["scheduler_node"], "inputs": scheduler_inputs},
        "15": {"class_type": classes["saver"], "inputs": _saver_inputs(classes["saver"], prefix, float(params["fps"]))},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": "V8",
        "route_id": f"ltx23.{loader}.txt2vid",
        "compiled_at": _now(),
        "parameters": {key: value for key, value in params.items() if key != "profile"},
        "profile": params["profile"],
        "vram_engine": params.get("vram_engine", {}),
        "bindings": bindings,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "source": {"required": False, "source_image": "", "source_image_name": ""},
        "rules": [
            "LTX 2.3 Txt2Vid is active for GGUF and UNET/Diffusion routes in V8.",
            "Batch count stays locked to 1.",
            "FPS sync is represented by one UI FPS value and patched into LTXVConditioning.",
            "Tiled VAE decode and chunk feed-forward remain default low/mid-VRAM safeguards.",
            "Phase V10 VRAM engine clamps LTX resolution, frame count, step count, tiling, and chunk feed-forward before compile.",
            "LTX Img2Vid, MultiScene, Extend, and audio-video stay guarded for later phases.",
        ],
    }


def _attach_video_output_record(result: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        ledger = register_video_generation_result(result, request=request_payload or {})
    except Exception as exc:  # noqa: BLE001
        return {**result, "neo_persisted": {"ok": False, "error": f"Video output ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def _post_json(base_url: str, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NeoStudioVideoLTXCompiler/1.0"}, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data) if data else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def video_ltx23_txt2vid_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = LtxVideoCompileRequest.from_payload(payload)
    nf = normalize_video_family(req.family)
    nl = normalize_video_loader(req.loader)
    nt = normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {
            "schema_version": SCHEMA_VERSION,
            "surface": "video",
            "phase": "V8",
            "ok": False,
            "queued": False,
            "error": f"V8 LTX compiler only supports {SUPPORTED_GGUF_ROUTE_ID} and {SUPPORTED_UNET_ROUTE_ID}.",
            "request": {"family": nf, "loader": nl, "generation_type": nt},
            "route": route.payload() if route else None,
        }
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback LTX bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_ltx23_txt2vid_workflow(req, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("txt2vid", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'ltx23_video')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "route_readiness": readiness}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response_payload = {
        **sidecar_payload,
        "ok": True,
        "queued": False,
        "dry_run": True,
        "backend": {"profile": profile, "base_url": base_url},
        "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)},
    }
    return _attach_video_output_record(response_payload, req.payload())


def video_ltx23_txt2vid_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = LtxVideoCompileRequest.from_payload(payload)
    compile_payload = video_ltx23_txt2vid_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    route_ready = compile_payload.get("route_readiness", {}).get("ready")
    if object_info_override is not None and not route_ready:
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected LTX 2.3 route is missing required ComfyUI nodes."}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI queue failed: {exc}"}
    response_payload = {
        **compile_payload,
        "ok": True,
        "queued": True,
        "dry_run": False,
        "queue_response": queue_response,
        "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or "",
    }
    return _attach_video_output_record(response_payload, req.payload())
