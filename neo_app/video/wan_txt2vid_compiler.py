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

SCHEMA_VERSION: Final[str] = "neo.video.wan22.compiler.v6"
SUPPORTED_ROUTE_ID: Final[str] = "wan22.unet.txt2vid"  # Backward-compatible V5 alias.
SUPPORTED_TXT2VID_ROUTE_ID: Final[str] = "wan22.unet.txt2vid"
SUPPORTED_IMG2VID_ROUTE_ID: Final[str] = "wan22.unet.img2vid"
SUPPORTED_ROUTE_IDS: Final[tuple[str, str]] = (SUPPORTED_TXT2VID_ROUTE_ID, SUPPORTED_IMG2VID_ROUTE_ID)
DEFAULT_NEGATIVE_PROMPT: Final[str] = (
    "flicker, jitter, unstable motion, warped anatomy, malformed hands, extra limbs, "
    "morphing face, broken eyes, blurry, low resolution, compression artifacts, text, logos, watermark"
)

FALLBACK_WAN_MODELS: Final[dict[str, str]] = {
    "unet": "wan2.2_ti2v_5B_fp16.safetensors",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan2.2_vae.safetensors",
}

@dataclass(frozen=True)
class VideoCompileRequest:
    family: str = "wan22"
    loader: str = "unet"
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
    filename_prefix: str = "Neo_Video_WAN22_T2V"
    source_image: str | None = None
    source_image_name: str | None = None
    image_strength: float | None = None
    resize_mode: str = "fit_crop"
    unet_name: str | None = None
    clip_name: str | None = None
    vae_name: str | None = None
    profile_id: str | None = None
    dry_run: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "VideoCompileRequest":
        data = payload or {}
        generation_type = data.get("generation_type", data.get("mode", "txt2vid"))
        return cls(
            family=str(data.get("family", "wan22")),
            loader=str(data.get("loader", "unet")),
            generation_type=str(generation_type or "txt2vid"),
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
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_WAN22_T2V") or "Neo_Video_WAN22_T2V"),
            source_image=str(data.get("source_image", data.get("image", "")) or "") or None,
            source_image_name=str(data.get("source_image_name", data.get("image_name", "")) or "") or None,
            image_strength=_float_or_none(data.get("image_strength", data.get("denoise"))),
            resize_mode=str(data.get("resize_mode", "fit_crop") or "fit_crop"),
            unet_name=str(data.get("unet_name", data.get("model_name", "")) or "") or None,
            clip_name=str(data.get("clip_name", data.get("text_encoder", "")) or "") or None,
            vae_name=str(data.get("vae_name", "") or "") or None,
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
    folded = {key.casefold(): key for key in required.keys()}
    for candidate in candidates:
        if candidate.casefold() in folded:
            return folded[candidate.casefold()]
    return fallback


def _literal_or_link(value: Any) -> Any:
    return value


def discover_wan22_txt2vid_bindings(object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    unet_loader = _class_exists(info, "UNETLoader", "DiffusionModelLoader") or "UNETLoader"
    clip_loader = _class_exists(info, "CLIPLoader") or "CLIPLoader"
    vae_loader = _class_exists(info, "VAELoader") or "VAELoader"
    latent_node = _class_exists(info, "Wan22ImageToVideoLatent", "WanImageToVideoLatent") or "Wan22ImageToVideoLatent"
    load_image = _class_exists(info, "LoadImage") or "LoadImage"
    sampler = _class_exists(info, "KSampler") or "KSampler"
    sampling_patch = _class_exists(info, "ModelSamplingSD3") or "ModelSamplingSD3"
    vae_decode = _class_exists(info, "VAEDecodeTiled", "VAEDecode") or "VAEDecode"
    saver = _class_exists(info, "SaveWEBM", "VHS_VideoCombine", "VideoCombine", "SaveAnimatedWEBP") or "SaveWEBM"

    unet_values = _combo_values(info, unet_loader, "unet_name") or _combo_values(info, unet_loader, "model_name")
    clip_values = _combo_values(info, clip_loader, "clip_name") or _combo_values(info, clip_loader, "text_encoder_name")
    vae_values = _combo_values(info, vae_loader, "vae_name")

    return {
        "classes": {
            "unet_loader": unet_loader,
            "clip_loader": clip_loader,
            "vae_loader": vae_loader,
            "latent_node": latent_node,
            "load_image": load_image,
            "sampling_patch": sampling_patch,
            "sampler": sampler,
            "vae_decode": vae_decode,
            "saver": saver,
        },
        "models": {
            "unet_name": _first_matching(unet_values, ("wan2.2", "wan22", "wan2.1", "wan"), FALLBACK_WAN_MODELS["unet"]),
            "clip_name": _first_matching(clip_values, ("umt5", "t5", "wan"), FALLBACK_WAN_MODELS["clip"]),
            "vae_name": _first_matching(vae_values, ("wan2.2", "wan_2.2", "wan2.1", "wan"), FALLBACK_WAN_MODELS["vae"]),
        },
        "available_model_counts": {"unet": len(unet_values), "clip": len(clip_values), "vae": len(vae_values)},
    }


def _apply_vram_safety(req: VideoCompileRequest) -> dict[str, Any]:
    engine = apply_video_vram_engine(
        req.payload(),
        family=req.family,
        loader=req.loader,
        generation_type=req.generation_type,
        vram_profile=req.vram_profile,
    )
    values = dict(engine.get("normalized_parameters") or {})
    return {
        "width": int(values.get("width") or 832),
        "height": int(values.get("height") or 480),
        "frames": int(values.get("frames") or 41),
        "fps": float(values.get("fps") or 16),
        "steps": max(1, int(values.get("steps") or 20)),
        "guidance": float(values.get("guidance") if values.get("guidance") is not None else 5),
        "seed": _seed(req.seed if req.seed is not None else values.get("seed")),
        "sampler": str(values.get("sampler") or "uni_pc"),
        "scheduler": str(values.get("scheduler") or "simple"),
        "batch_count": 1,
        "tile_size": int(values.get("tile_size") or 512),
        "temporal_tile_size": int(values.get("temporal_tile_size") or 4096),
        "decode_mode": str(values.get("decode_mode") or "tiled"),
        "profile": engine.get("parameter_profile"),
        "vram_engine": engine,
    }


def _comfy_image_name(source_image: str | None, source_image_name: str | None = None) -> str:
    candidate = Path(str(source_image_name or source_image or "")).name
    return sanitize_path_part(candidate, fallback="neo_video_source.png")


def build_wan22_txt2vid_workflow(req: VideoCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    bindings = discover_wan22_txt2vid_bindings(object_info)
    classes = bindings["classes"]
    models = dict(bindings["models"])
    if req.unet_name:
        models["unet_name"] = req.unet_name
    if req.clip_name:
        models["clip_name"] = req.clip_name
    if req.vae_name:
        models["vae_name"] = req.vae_name
    params = _apply_vram_safety(req)
    prompt = req.prompt.strip() or "A short cinematic video shot with smooth natural motion."
    negative = req.negative_prompt.strip() or DEFAULT_NEGATIVE_PROMPT
    is_img2vid = normalize_video_generation_type(req.generation_type) == "img2vid"
    route_id = SUPPORTED_IMG2VID_ROUTE_ID if is_img2vid else SUPPORTED_TXT2VID_ROUTE_ID
    prefix_fallback = "Neo_Video_WAN22_I2V" if is_img2vid else "Neo_Video_WAN22_T2V"
    prefix = sanitize_path_part(req.filename_prefix or prefix_fallback, fallback=prefix_fallback)

    unet_inputs: dict[str, Any] = {}
    unet_required = _required_inputs(object_info or {}, classes["unet_loader"])
    unet_name_field = _field_name(unet_required, ("unet_name", "model_name"), "unet_name")
    unet_inputs[unet_name_field] = models["unet_name"]
    if "weight_dtype" in unet_required:
        unet_inputs["weight_dtype"] = "default"

    clip_inputs: dict[str, Any] = {}
    clip_required = _required_inputs(object_info or {}, classes["clip_loader"])
    clip_name_field = _field_name(clip_required, ("clip_name", "text_encoder_name"), "clip_name")
    clip_inputs[clip_name_field] = models["clip_name"]
    if "type" in clip_required:
        clip_inputs["type"] = "wan"
    if "device" in clip_required:
        clip_inputs["device"] = "default"

    vae_inputs = {"vae_name": models["vae_name"]}

    latent_required = _required_inputs(object_info or {}, classes["latent_node"])
    latent_inputs: dict[str, Any] = {
        "vae": ["3", 0],
        "width": params["width"],
        "height": params["height"],
        "batch_size": 1,
    }
    length_field = "length" if "length" in latent_required else "frames" if "frames" in latent_required else "num_frames" if "num_frames" in latent_required else "length"
    latent_inputs[length_field] = params["frames"]
    if is_img2vid:
        latent_inputs["start_image"] = ["0", 0]

    saver_inputs: dict[str, Any]
    saver_class = classes["saver"]
    if saver_class == "SaveWEBM":
        saver_inputs = {"images": ["9", 0], "filename_prefix": prefix, "codec": "vp9", "fps": params["fps"], "crf": 20}
    elif saver_class == "SaveAnimatedWEBP":
        saver_inputs = {"images": ["9", 0], "filename_prefix": prefix, "fps": params["fps"], "lossless": False, "quality": 90, "method": "default"}
    else:
        saver_inputs = {"images": ["9", 0], "filename_prefix": prefix, "fps": params["fps"]}

    workflow: dict[str, Any] = {}
    if is_img2vid:
        workflow["0"] = {"class_type": classes["load_image"], "inputs": {"image": _comfy_image_name(req.source_image, req.source_image_name)}}
    workflow.update({
        "1": {"class_type": classes["unet_loader"], "inputs": unet_inputs},
        "2": {"class_type": classes["clip_loader"], "inputs": clip_inputs},
        "3": {"class_type": classes["vae_loader"], "inputs": vae_inputs},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": prompt}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["2", 0], "text": negative}},
        "6": {"class_type": classes["latent_node"], "inputs": latent_inputs},
        "7": {"class_type": classes["sampling_patch"], "inputs": {"model": ["1", 0], "shift": 8.0}},
        "8": {"class_type": classes["sampler"], "inputs": {"model": ["7", 0], "positive": ["4", 0], "negative": ["5", 0], "latent_image": ["6", 0], "seed": params["seed"], "steps": params["steps"], "cfg": params["guidance"], "sampler_name": params["sampler"], "scheduler": params["scheduler"], "denoise": 1.0}},
        "9": {"class_type": classes["vae_decode"], "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": saver_class, "inputs": saver_inputs},
    })
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": "V6",
        "route_id": route_id,
        "compiled_at": _now(),
        "parameters": {key: value for key, value in params.items() if key != "profile"},
        "profile": params["profile"],
        "vram_engine": params.get("vram_engine", {}),
        "bindings": bindings,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "source": {"required": is_img2vid, "source_image": req.source_image or "", "source_image_name": req.source_image_name or "", "comfy_image_name": _comfy_image_name(req.source_image, req.source_image_name) if is_img2vid else "", "image_strength": req.image_strength if req.image_strength is not None else params.get("image_strength", 0.7), "resize_mode": req.resize_mode},
        "rules": [
            "WAN 2.2 + UNET/Diffusion + Txt2Vid remains runnable from V5.",
            "WAN 2.2 + UNET/Diffusion + Img2Vid is runnable in V6 when a source image is provided.",
            "Batch count stays locked to 1.",
            "Phase V10 VRAM engine clamps resolution, frames, steps, decode tiling, and batch before compile.",
            "ComfyUI output is queued through /prompt; Neo-owned V7 output ledger records compile/queue events and attaches files when available.",
        ],
    }


def _attach_video_output_record(result: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        ledger = register_video_generation_result(result, request=request_payload or {})
    except Exception as exc:  # noqa: BLE001 - output ledger must not block compile/generate response.
        return {**result, "neo_persisted": {"ok": False, "error": f"Video output ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def _post_json(base_url: str, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NeoStudioVideoCompiler/1.0"}, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local user-configured Comfy URL.
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data) if data else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _requested_route_id(req: VideoCompileRequest) -> str:
    return f"{normalize_video_family(req.family)}.{normalize_video_loader(req.loader)}.{normalize_video_generation_type(req.generation_type)}"


def _video_wan22_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = VideoCompileRequest.from_payload(payload)
    nf = normalize_video_family(req.family)
    nl = normalize_video_loader(req.loader)
    nt = normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in SUPPORTED_ROUTE_IDS:
        return {
            "schema_version": SCHEMA_VERSION,
            "surface": "video",
            "phase": "V6",
            "ok": False,
            "queued": False,
            "error": f"V6 WAN compiler only supports {SUPPORTED_TXT2VID_ROUTE_ID} and {SUPPORTED_IMG2VID_ROUTE_ID}.",
            "request": {"family": nf, "loader": nl, "generation_type": nt},
            "route": route.payload() if route else None,
        }
    if route.route_id == SUPPORTED_IMG2VID_ROUTE_ID and not req.source_image:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": "V6", "ok": False, "queued": False, "error": "WAN Img2Vid requires a source_image from the Video source upload/select panel.", "request": req.payload(), "route": route.payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    compiled = build_wan22_txt2vid_workflow(req, object_info=object_info)
    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    output_paths = get_video_output_paths("img2vid" if route.route_id == SUPPORTED_IMG2VID_ROUTE_ID else "txt2vid", create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'wan22_video')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
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



def video_wan22_txt2vid_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    requested_type = normalize_video_generation_type(data.get("generation_type", data.get("mode", "txt2vid")))
    if requested_type != "txt2vid":
        route = find_video_route(data.get("family", "wan22"), data.get("loader", "unet"), requested_type, include_planned=True)
        return {
            "schema_version": SCHEMA_VERSION,
            "surface": "video",
            "phase": "V5",
            "ok": False,
            "queued": False,
            "error": f"V5 compiler only supports {SUPPORTED_TXT2VID_ROUTE_ID}.",
            "request": {"family": normalize_video_family(data.get("family")), "loader": normalize_video_loader(data.get("loader")), "generation_type": requested_type},
            "route": route.payload() if route else None,
        }
    data["family"] = "wan22"
    data["loader"] = "unet"
    data["generation_type"] = "txt2vid"
    if not data.get("filename_prefix"):
        data["filename_prefix"] = "Neo_Video_WAN22_T2V"
    return _video_wan22_compile_payload(data, object_info_override=object_info_override)

def video_wan22_img2vid_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    data = dict(payload or {})
    data["family"] = "wan22"
    data["loader"] = "unet"
    data["generation_type"] = "img2vid"
    if not data.get("filename_prefix"):
        data["filename_prefix"] = "Neo_Video_WAN22_I2V"
    return _video_wan22_compile_payload(data, object_info_override=object_info_override)


def _video_wan22_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = VideoCompileRequest.from_payload(payload)
    compile_payload = _video_wan22_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    route_ready = compile_payload.get("route_readiness", {}).get("ready")
    if object_info_override is not None and not route_ready:
        return {**compile_payload, "ok": False, "queued": False, "error": "Selected WAN 2.2 route is missing required ComfyUI nodes."}
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



def video_wan22_txt2vid_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    data = dict(payload or {})
    requested_type = normalize_video_generation_type(data.get("generation_type", data.get("mode", "txt2vid")))
    if requested_type != "txt2vid":
        return video_wan22_txt2vid_compile_payload(data, object_info_override=object_info_override)
    data["family"] = "wan22"
    data["loader"] = "unet"
    data["generation_type"] = "txt2vid"
    return _video_wan22_generate_payload(data, object_info_override=object_info_override, timeout=timeout)

def video_wan22_img2vid_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    data = dict(payload or {})
    data["family"] = "wan22"
    data["loader"] = "unet"
    data["generation_type"] = "img2vid"
    if not data.get("filename_prefix"):
        data["filename_prefix"] = "Neo_Video_WAN22_I2V"
    return _video_wan22_generate_payload(data, object_info_override=object_info_override, timeout=timeout)
