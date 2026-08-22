from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import random
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen
from uuid import uuid4

from neo_app.video.backend_probe import _get_json, route_node_readiness, video_backend_profile_payload
from neo_app.video.comfy_input_handoff import prepare_comfy_input_file_handoff
from neo_app.video.model_discovery import video_model_discovery_from_object_info
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import register_video_generation_result
from neo_app.video.parameter_profiles import video_parameter_profile_payload
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader

SCHEMA_VERSION: Final[str] = "neo.video.minimax_h3.compiler.v2"
PHASE: Final[str] = "H3-R2"
H3_FAMILY: Final[str] = "minimax_h3"
SUPPORTED_LOADERS: Final[set[str]] = {"unet", "gguf"}
SUPPORTED_TYPES: Final[set[str]] = {"txt2vid", "img2vid", "first_last_frame", "reference_to_video", "vid2vid"}
H3_FPS: Final[int] = 24
H3_FRAME_MODULUS: Final[int] = 17
H3_FRAME_REMAINDER: Final[int] = 5
H3_NATIVE_CLIP_LOADER_CANDIDATES: Final[tuple[str, ...]] = ("CLIPLoader",)
H3_GGUF_CLIP_LOADER_CANDIDATES: Final[tuple[str, ...]] = (
    "H3ClipLoaderAny",
    "VideoCLIPLoaderGGUF",
    "CLIPLoaderGGUFAdvanced",
    "CLIPLoaderGGUF",
)

FALLBACK_MODELS: Final[dict[str, dict[str, str]]] = {
    "unet": {
        "fl2va": "minimax_h3_fl2va_pruned_int8_convrot.safetensors",
        "ref2va": "minimax_h3_ref2va_pruned_int8_convrot.safetensors",
        "clip": "qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors",
    },
    "gguf": {
        "fl2va": "minimax_h3_fl2va-Q4_K_M.gguf",
        "ref2va": "minimax_h3_ref2va-Q4_K_M.gguf",
        "clip": "qwen3vl-32B-MiniMax-H3-Q4_K_M.gguf",
    },
    "shared": {
        "video_vae": "minimax_h3_video_vae_fp16.safetensors",
        "audio_vae": "minimax_h3_audio_vae_fp32.safetensors",
    },
}


@dataclass(frozen=True)
class H3ReferenceMedia:
    path: str = ""
    comfy_name: str = ""
    name: str = ""
    include_audio: bool = True

    @classmethod
    def from_value(cls, value: Any) -> "H3ReferenceMedia":
        if isinstance(value, str):
            return cls(path=value, name=Path(value).name)
        data = value if isinstance(value, dict) else {}
        return cls(
            path=str(data.get("path") or data.get("source") or data.get("file") or ""),
            comfy_name=str(data.get("comfy_name") or data.get("comfy_file_name") or data.get("comfy_image_name") or ""),
            name=str(data.get("name") or data.get("filename") or ""),
            include_audio=bool(data.get("include_audio", True)),
        )

    def payload(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MiniMaxH3CompileRequest:
    family: str = H3_FAMILY
    loader: str = "unet"
    generation_type: str = "txt2vid"
    prompt: str = ""
    negative_prompt: str = ""
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
    model_name: str | None = None
    unet_name: str | None = None
    gguf_name: str | None = None
    clip_name: str | None = None
    vae_name: str | None = None
    audio_vae_name: str | None = None
    source_image: str = ""
    source_image_name: str = ""
    source_image_comfy_name: str = ""
    first_image: str = ""
    first_image_name: str = ""
    first_image_comfy_name: str = ""
    last_image: str = ""
    last_image_name: str = ""
    last_image_comfy_name: str = ""
    source_video_path: str = ""
    source_video_name: str = ""
    source_video_comfy_name: str = ""
    source_result_id: str = ""
    source_file_id: str = ""
    preserve_audio: bool = True
    h3_keyframe_role: str = "first"
    h3_ref_image_size: str = "match"
    h3_shift_video: float = 12.0
    h3_shift_audio: float = 3.0
    h3_turbo_enabled: bool = False
    h3_turbo_lora: str = ""
    h3_turbo_strength: float = 1.0
    h3_acceleration_mode: str = "off"
    h3_spectrum_blend: float = 0.5
    h3_block_cache_threshold: float = 0.12
    enable_sage_attention: bool = False
    sage_attention_mode: str = "auto"
    h3_reference_images: tuple[H3ReferenceMedia, ...] = field(default_factory=tuple)
    h3_reference_videos: tuple[H3ReferenceMedia, ...] = field(default_factory=tuple)
    h3_reference_audios: tuple[H3ReferenceMedia, ...] = field(default_factory=tuple)
    output_format: str = "auto"
    filename_prefix: str = "Neo_Video_MiniMax_H3"
    profile_id: str | None = None
    dry_run: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "MiniMaxH3CompileRequest":
        data = payload or {}
        refs_i = tuple(H3ReferenceMedia.from_value(item) for item in (data.get("h3_reference_images") or data.get("reference_images") or []))
        refs_v = tuple(H3ReferenceMedia.from_value(item) for item in (data.get("h3_reference_videos") or data.get("reference_videos") or []))
        refs_a = tuple(H3ReferenceMedia.from_value(item) for item in (data.get("h3_reference_audios") or data.get("reference_audios") or []))
        return cls(
            family=str(data.get("family") or H3_FAMILY),
            loader=str(data.get("loader") or "unet"),
            generation_type=str(data.get("generation_type") or data.get("mode") or "txt2vid"),
            prompt=str(data.get("prompt") or data.get("positive_prompt") or ""),
            negative_prompt=str(data.get("negative_prompt") or ""),
            vram_profile=str(data.get("vram_profile") or "balanced"),
            width=_int_or_none(data.get("width")), height=_int_or_none(data.get("height")), frames=_int_or_none(data.get("frames")),
            fps=_float_or_none(data.get("fps")), steps=_int_or_none(data.get("steps")), guidance=_float_or_none(data.get("guidance", data.get("cfg"))),
            seed=_int_or_none(data.get("seed")), sampler=str(data.get("sampler") or "") or None, scheduler=str(data.get("scheduler") or "") or None,
            model_name=str(data.get("model_name") or "") or None, unet_name=str(data.get("unet_name") or "") or None, gguf_name=str(data.get("gguf_name") or "") or None,
            clip_name=str(data.get("clip_name") or data.get("text_encoder") or "") or None,
            vae_name=str(data.get("vae_name") or "") or None, audio_vae_name=str(data.get("audio_vae_name") or "") or None,
            source_image=str(data.get("source_image") or data.get("source_image_path") or ""), source_image_name=str(data.get("source_image_name") or ""), source_image_comfy_name=str(data.get("source_image_comfy_name") or data.get("comfy_source_image_name") or ""),
            first_image=str(data.get("first_image") or data.get("first_image_path") or ""), first_image_name=str(data.get("first_image_name") or ""), first_image_comfy_name=str(data.get("first_image_comfy_name") or ""),
            last_image=str(data.get("last_image") or data.get("last_image_path") or ""), last_image_name=str(data.get("last_image_name") or ""), last_image_comfy_name=str(data.get("last_image_comfy_name") or ""),
            source_video_path=str(data.get("source_video_path") or data.get("source_video") or ""), source_video_name=str(data.get("source_video_name") or ""), source_video_comfy_name=str(data.get("source_video_comfy_name") or ""),
            source_result_id=str(data.get("source_result_id") or ""), source_file_id=str(data.get("source_file_id") or ""), preserve_audio=_bool(data.get("preserve_audio", True)),
            h3_keyframe_role=str(data.get("h3_keyframe_role") or "first"), h3_ref_image_size=str(data.get("h3_ref_image_size") or "match"),
            h3_shift_video=float(data.get("h3_shift_video", 12.0) or 12.0), h3_shift_audio=float(data.get("h3_shift_audio", 3.0) or 3.0),
            h3_turbo_enabled=_bool(data.get("h3_turbo_enabled", False)), h3_turbo_lora=str(data.get("h3_turbo_lora") or ""), h3_turbo_strength=float(data.get("h3_turbo_strength", 1.0) or 1.0),
            h3_acceleration_mode=_normalize_acceleration(data.get("h3_acceleration_mode")), h3_spectrum_blend=float(data.get("h3_spectrum_blend", 0.5) or 0.5), h3_block_cache_threshold=float(data.get("h3_block_cache_threshold", 0.12) or 0.12),
            enable_sage_attention=_bool(data.get("enable_sage_attention", False)), sage_attention_mode=str(data.get("sage_attention_mode") or "auto"),
            h3_reference_images=refs_i, h3_reference_videos=refs_v, h3_reference_audios=refs_a,
            output_format=str(data.get("output_format") or "auto"), filename_prefix=str(data.get("filename_prefix") or "Neo_Video_MiniMax_H3"),
            profile_id=str(data.get("profile_id") or data.get("backend_profile_id") or "") or None, dry_run=_bool(data.get("dry_run", True)),
        )

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["h3_reference_images"] = [item.payload() for item in self.h3_reference_images]
        data["h3_reference_videos"] = [item.payload() for item in self.h3_reference_videos]
        data["h3_reference_audios"] = [item.payload() for item in self.h3_reference_audios]
        return data


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value in (None, "") else int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value in (None, "") else float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"1", "true", "yes", "on"}
    return bool(value)


def _normalize_acceleration(value: Any) -> str:
    key = str(value or "off").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {"none": "off", "disabled": "off", "spectrum_h3": "spectrum", "blockcache": "block_cache", "block_cache_t8": "block_cache", "t8": "block_cache"}
    key = aliases.get(key, key)
    return key if key in {"off", "spectrum", "block_cache"} else "off"


def _seed(value: int | None) -> int:
    if value is None or value < 0:
        return random.randint(0, 2_147_483_647)
    return max(0, min(int(value), 9_999_999_999_999))


def align_h3_frames(value: int) -> int:
    n = max(5, int(value))
    while n % H3_FRAME_MODULUS != H3_FRAME_REMAINDER:
        n += 1
    return n


def align_h3_canvas(value: int) -> int:
    return max(32, int(round(max(32, value) / 32.0)) * 32)


def _class_exists(object_info: dict[str, Any], *candidates: str) -> str | None:
    folded = {str(key).casefold(): str(key) for key in (object_info or {}).keys()}
    for candidate in candidates:
        hit = folded.get(candidate.casefold())
        if hit:
            return hit
    return None


def _input_groups(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    result: dict[str, Any] = {}
    for group_name in ("required", "optional"):
        group = inputs.get(group_name, {}) if isinstance(inputs, dict) else {}
        if isinstance(group, dict):
            result.update(group)
    return result


def _combo_values(object_info: dict[str, Any], class_type: str, *fields: str) -> list[str]:
    inputs = _input_groups(object_info, class_type)
    folded = {str(key).casefold(): str(key) for key in inputs}
    for field in fields:
        actual = folded.get(field.casefold())
        if not actual:
            continue
        spec = inputs.get(actual)
        if isinstance(spec, list) and spec and isinstance(spec[0], list):
            return [str(item) for item in spec[0]]
    return []


def _field_name(object_info: dict[str, Any], class_type: str, candidates: tuple[str, ...], fallback: str) -> str:
    inputs = _input_groups(object_info, class_type)
    folded = {str(key).casefold(): str(key) for key in inputs}
    for candidate in candidates:
        hit = folded.get(candidate.casefold())
        if hit:
            return hit
    return fallback


def _set_if_supported(inputs: dict[str, Any], object_info: dict[str, Any], class_type: str, key: str, value: Any, *, fallback: bool = False) -> None:
    available = _input_groups(object_info, class_type)
    if key in available or fallback or not available:
        inputs[key] = value


def _first_matching(values: list[str], needles: tuple[str, ...], fallback: str) -> str:
    if not values:
        return fallback
    lowered = [(value, value.casefold().replace("-", "_")) for value in values]
    for needle in needles:
        n = needle.casefold().replace("-", "_")
        hit = next((value for value, low in lowered if n in low), None)
        if hit:
            return hit
    return values[0]


def _clip_catalogs_by_class(info: dict[str, Any], candidates: tuple[str, ...]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    folded = {str(key).casefold(): str(key) for key in (info or {})}
    for candidate in candidates:
        actual = folded.get(candidate.casefold())
        if not actual:
            continue
        values = _combo_values(info, actual, "clip_name", "text_encoder_name")
        if values:
            result[actual] = values
    return result


def _select_h3_clip_loader(req: MiniMaxH3CompileRequest, info: dict[str, Any], main_loader: str) -> dict[str, Any]:
    native_catalogs = _clip_catalogs_by_class(info, H3_NATIVE_CLIP_LOADER_CANDIDATES)
    gguf_catalogs = _clip_catalogs_by_class(info, H3_GGUF_CLIP_LOADER_CANDIDATES)
    native_values = list(dict.fromkeys(value for rows in native_catalogs.values() for value in rows))
    gguf_values = list(dict.fromkeys(value for rows in gguf_catalogs.values() for value in rows))
    requested = str(req.clip_name or "").strip()

    if requested:
        requested_format = "gguf" if requested.casefold().endswith(".gguf") else "safetensors"
    elif main_loader == "gguf" and gguf_values:
        requested_format = "gguf"
    elif native_values:
        requested_format = "safetensors"
    elif gguf_values:
        requested_format = "gguf"
    else:
        requested_format = "gguf" if main_loader == "gguf" else "safetensors"

    if requested_format == "gguf":
        candidates = gguf_catalogs
        fallback_class = _class_exists(info, *H3_GGUF_CLIP_LOADER_CANDIDATES) or "CLIPLoaderGGUF"
        fallback_model = FALLBACK_MODELS["gguf"]["clip"]
        pool = gguf_values
    else:
        candidates = native_catalogs
        fallback_class = _class_exists(info, *H3_NATIVE_CLIP_LOADER_CANDIDATES) or "CLIPLoader"
        fallback_model = FALLBACK_MODELS["unet"]["clip"]
        pool = native_values

    selected_class = ""
    if requested:
        for class_type, values in candidates.items():
            if requested in values:
                selected_class = class_type
                break
    if not selected_class and candidates:
        selected_class = next(iter(candidates))
    selected_class = selected_class or fallback_class

    selected_model = requested or _first_matching(pool, ("minimax_h3", "qwen3vl", "qwen3_vl"), fallback_model)
    return {
        "class_type": selected_class,
        "selected_model": selected_model,
        "format": requested_format,
        "mixed_format_supported": True,
        "native_catalog": native_values,
        "gguf_catalog": gguf_values,
        "native_loader_classes": list(native_catalogs),
        "gguf_loader_classes": list(gguf_catalogs),
    }


def discover_minimax_h3_bindings(req: MiniMaxH3CompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    loader = normalize_video_loader(req.loader)
    if loader == "gguf":
        model_loader = _class_exists(info, "UnetLoaderGGUF", "UNETLoaderGGUF", "UnetLoaderGGUFAdvanced") or "UnetLoaderGGUF"
    else:
        model_loader = _class_exists(info, "UNETLoader", "DiffusionModelLoader") or "UNETLoader"
    clip_topology = _select_h3_clip_loader(req, info, loader)
    clip_loader = clip_topology["class_type"]
    classes = {
        "model_loader": model_loader,
        "clip_loader": clip_loader,
        "vae_loader": _class_exists(info, "VAELoader", "VAELoaderKJ") or "VAELoader",
        "condition_fl2va": _class_exists(info, "MiniMaxH3ImageToVideo") or "MiniMaxH3ImageToVideo",
        "condition_ref2va": _class_exists(info, "MiniMaxH3ReferenceToVideo") or "MiniMaxH3ReferenceToVideo",
        "sigma_shift": _class_exists(info, "MiniMaxH3SigmaShift") or "MiniMaxH3SigmaShift",
        "noise": _class_exists(info, "RandomNoise") or "RandomNoise",
        "sampler_select": _class_exists(info, "KSamplerSelect") or "KSamplerSelect",
        "scheduler": _class_exists(info, "BasicScheduler") or "BasicScheduler",
        "guider": _class_exists(info, "BasicGuider") or "BasicGuider",
        "sampler": _class_exists(info, "SamplerCustomAdvanced") or "SamplerCustomAdvanced",
        "decode_video": _class_exists(info, "VAEDecode") or "VAEDecode",
        "decode_audio": _class_exists(info, "VAEDecodeAudio") or "VAEDecodeAudio",
        "create_video": _class_exists(info, "CreateVideo") or "CreateVideo",
        "save_video": _class_exists(info, "SaveVideo") or "SaveVideo",
        "load_image": _class_exists(info, "LoadImage") or "LoadImage",
        "load_video": _class_exists(info, "LoadVideo") or "LoadVideo",
        "video_components": _class_exists(info, "GetVideoComponents") or "GetVideoComponents",
        "load_audio": _class_exists(info, "LoadAudio") or "LoadAudio",
        "lora": _class_exists(info, "LoraLoaderModelOnly", "LoraLoader"),
        "sage_h3": _class_exists(info, "MiniMaxH3MemoryEfficientSageAttentionPatch"),
        "sage_general": _class_exists(info, "PathchSageAttentionKJ", "PatchSageAttentionKJ", "SageAttentionKJ"),
        "spectrum": _class_exists(info, "SpectrumApplyMiniMaxH3"),
        "block_cache": _class_exists(info, "MiniMaxH3BlockCacheT8", "MiniMax H3 Block Cache (T8)"),
    }
    model_values = _combo_values(info, model_loader, "unet_name", "model_name")
    clip_values = _combo_values(info, clip_loader, "clip_name", "text_encoder_name")
    vae_values = _combo_values(info, classes["vae_loader"], "vae_name", "ckpt_name")
    lora_values = _combo_values(info, classes["lora"], "lora_name") if classes["lora"] else []
    is_ref = normalize_video_generation_type(req.generation_type) in {"reference_to_video", "vid2vid"}
    route_key = "ref2va" if is_ref else "fl2va"
    model_needles = ("minimax_h3_ref2va", "h3_ref2va", "ref2va") if is_ref else ("minimax_h3_fl2va", "h3_fl2va", "fl2va")
    clip_needles = ("minimax_h3", "qwen3vl", "qwen3_vl")
    video_vae_values = [v for v in vae_values if "minimax" in v.casefold() and "video" in v.casefold()]
    audio_vae_values = [v for v in vae_values if "minimax" in v.casefold() and "audio" in v.casefold()]
    turbo_loras = [v for v in lora_values if "h3" in v.casefold() and any(k in v.casefold() for k in ("turbo", "lightx2v", "4step", "4steps", "8step", "8steps"))]
    fallback = FALLBACK_MODELS[loader]
    return {
        "classes": classes,
        "models": {
            "model_name": _first_matching(model_values, model_needles, FALLBACK_MODELS[loader][route_key]),
            "clip_name": req.clip_name or clip_topology["selected_model"] or _first_matching(clip_values, clip_needles, fallback["clip"]),
            "video_vae": _first_matching(video_vae_values or vae_values, ("minimax_h3_video", "h3_video", "video"), FALLBACK_MODELS["shared"]["video_vae"]),
            "audio_vae": _first_matching(audio_vae_values or vae_values, ("minimax_h3_audio", "h3_audio", "audio"), FALLBACK_MODELS["shared"]["audio_vae"]),
            "turbo_lora": _first_matching(turbo_loras, ("4step", "turbo", "lightx2v"), "") if turbo_loras else "",
        },
        "catalogs": {
            "models": model_values,
            "text_encoders": list(dict.fromkeys([*clip_topology["native_catalog"], *clip_topology["gguf_catalog"]])) or clip_values,
            "native_text_encoders": clip_topology["native_catalog"],
            "gguf_text_encoders": clip_topology["gguf_catalog"],
            "vaes": vae_values, "loras": lora_values, "turbo_loras": turbo_loras,
        },
        "text_encoder_topology": clip_topology,
    }


def _validate_request(req: MiniMaxH3CompileRequest) -> list[str]:
    errors: list[str] = []
    family = normalize_video_family(req.family)
    loader = normalize_video_loader(req.loader)
    mode = normalize_video_generation_type(req.generation_type)
    if family != H3_FAMILY:
        errors.append("MiniMax H3 compiler requires family=minimax_h3.")
    if loader not in SUPPORTED_LOADERS:
        errors.append("MiniMax H3 supports UNET/Diffusion and experimental GGUF loaders only.")
    if mode not in SUPPORTED_TYPES:
        errors.append(f"MiniMax H3 compiler does not support generation type '{mode}'.")
    if not req.prompt.strip():
        errors.append("MiniMax H3 requires a prompt.")
    if mode == "img2vid" and not (req.source_image or req.source_image_name or req.source_image_comfy_name):
        errors.append("MiniMax H3 Img2Vid requires one source keyframe image.")
    if mode == "first_last_frame":
        if not (req.first_image or req.first_image_name or req.first_image_comfy_name):
            errors.append("MiniMax H3 First/Last Frame requires a first image.")
        if not (req.last_image or req.last_image_name or req.last_image_comfy_name):
            errors.append("MiniMax H3 First/Last Frame requires a last image.")
    if mode in {"reference_to_video", "vid2vid"}:
        ni, nv, na = len(req.h3_reference_images), len(req.h3_reference_videos), len(req.h3_reference_audios)
        source_video_count = 1 if mode == "vid2vid" else 0
        if mode == "vid2vid" and not (req.source_video_path or req.source_video_name or req.source_video_comfy_name):
            errors.append("MiniMax H3 Video-to-Video requires one source video.")
        if ni > 9:
            errors.append("MiniMax H3 Ref2VA accepts at most 9 reference images.")
        if nv + source_video_count > 3:
            errors.append("MiniMax H3 Ref2VA accepts at most 3 reference videos including the Video-to-Video source.")
        if na > 3:
            errors.append("MiniMax H3 Ref2VA accepts at most 3 standalone reference audio files.")
        if ni + nv + na + source_video_count > 12:
            errors.append("MiniMax H3 Ref2VA accepts at most 12 combined reference files including the Video-to-Video source.")
        if mode == "reference_to_video" and na and not (ni or nv):
            errors.append("MiniMax H3 reference audio cannot be the only reference type; add at least one image or video reference.")
        if mode == "reference_to_video" and not (ni or nv or na):
            errors.append("MiniMax H3 Omni Reference requires at least one reference image or video.")
    if req.h3_acceleration_mode not in {"off", "spectrum", "block_cache"}:
        errors.append("H3 acceleration must be off, spectrum, or block_cache.")
    return errors


def _normalized_parameters(req: MiniMaxH3CompileRequest) -> tuple[dict[str, Any], list[str]]:
    profile = video_parameter_profile_payload(H3_FAMILY, req.loader, req.generation_type, req.vram_profile)
    defaults = profile.get("defaults", {}) if isinstance(profile, dict) else {}
    requested_w = int(req.width or defaults.get("width") or 1344)
    requested_h = int(req.height or defaults.get("height") or 768)
    requested_f = int(req.frames or defaults.get("frames") or 124)
    width, height, frames = align_h3_canvas(requested_w), align_h3_canvas(requested_h), align_h3_frames(requested_f)
    notes: list[str] = []
    if width != requested_w or height != requested_h:
        notes.append(f"H3 canvas snapped from {requested_w}x{requested_h} to {width}x{height} so both axes are multiples of 32.")
    if frames != requested_f:
        notes.append(f"H3 frame count snapped from {requested_f} to {frames} on the 17k+5 temporal grid.")
    if req.fps not in (None, H3_FPS, float(H3_FPS)):
        notes.append(f"H3 native output is fixed at {H3_FPS} FPS; requested {req.fps} FPS was replaced.")
    if frames < 101:
        notes.append("This short H3 clip is a low-VRAM/smoke-test concession; the released model is normally used for roughly 4-15 second output.")
    if req.h3_turbo_enabled:
        turbo_steps = int(req.steps or defaults.get("steps") or 20)
        turbo_sampler = str(req.sampler or defaults.get("sampler") or "res_multistep")
        turbo_scheduler = str(req.scheduler or defaults.get("scheduler") or "simple")
        if turbo_steps > 8:
            notes.append("H3 Turbo LoRA is enabled with more than 8 steps. Neo preserves the user step value; current pruned ComfyUI Turbo conversions are normally tested around 6-8 steps.")
        if turbo_sampler.casefold() != "euler" or turbo_scheduler.casefold() != "beta":
            notes.append("H3 Turbo is enabled outside the common Euler + Beta few-step recipe. Neo preserves the requested sampler/scheduler for deliberate experiments.")
        if not (4.0 <= float(req.h3_shift_audio) <= 6.0):
            notes.append("H3 Turbo audio shift is outside the commonly tested 4-6 range; distorted or unstable audio can result with incompatible few-step scheduling.")
    if req.h3_acceleration_mode in {"spectrum", "block_cache"}:
        notes.append("The selected H3 accelerator is approximate and can change motion, anatomy, audio, timing, or synchronization even with the same seed.")
    if normalize_video_generation_type(req.generation_type) in {"reference_to_video", "vid2vid"} and (req.h3_reference_videos or req.source_video_path or req.source_video_name or req.source_video_comfy_name):
        notes.append("H3 reference-video frame batches are interpreted at 24 FPS. Use 24 FPS reference clips when exact reference timing matters.")
    return {
        "width": width, "height": height, "frames": frames, "fps": H3_FPS,
        "steps": max(1, int(req.steps or defaults.get("steps") or 20)), "guidance": 1.0,
        "seed": _seed(req.seed), "sampler": req.sampler or str(defaults.get("sampler") or "res_multistep"),
        "scheduler": req.scheduler or str(defaults.get("scheduler") or "simple"),
        "h3_shift_video": max(0.01, float(req.h3_shift_video)), "h3_shift_audio": max(0.01, float(req.h3_shift_audio)),
        "profile": profile,
    }, notes


def _media_input_name(media: H3ReferenceMedia) -> str:
    return str(media.comfy_name or media.name or Path(media.path).name).strip()


def _model_loader_inputs(req: MiniMaxH3CompileRequest, bindings: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    cls = bindings["classes"]["model_loader"]
    requested = req.model_name or req.gguf_name or req.unet_name or bindings["models"]["model_name"]
    key = _field_name(info, cls, ("unet_name", "model_name", "ckpt_name"), "unet_name")
    inputs = {key: requested}
    if normalize_video_loader(req.loader) != "gguf":
        _set_if_supported(inputs, info, cls, "weight_dtype", "default")
    return inputs


def _clip_loader_inputs(req: MiniMaxH3CompileRequest, bindings: dict[str, Any], info: dict[str, Any]) -> dict[str, Any]:
    cls = bindings["classes"]["clip_loader"]
    key = _field_name(info, cls, ("clip_name", "text_encoder_name"), "clip_name")
    inputs = {key: req.clip_name or bindings["models"]["clip_name"]}
    _set_if_supported(inputs, info, cls, "type", "minimax", fallback=True)
    _set_if_supported(inputs, info, cls, "device", "default")
    return inputs


def _vae_loader_inputs(class_type: str, model_name: str, info: dict[str, Any]) -> dict[str, Any]:
    key = _field_name(info, class_type, ("vae_name", "ckpt_name"), "vae_name")
    return {key: model_name}


def _append_model_patch(workflow: dict[str, Any], next_id: int, class_type: str, model_ref: list[Any], inputs: dict[str, Any]) -> tuple[list[Any], int]:
    node_id = str(next_id)
    workflow[node_id] = {"class_type": class_type, "inputs": {"model": model_ref, **inputs}}
    return [node_id, 0], next_id + 1


def build_minimax_h3_workflow(req: MiniMaxH3CompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    mode = normalize_video_generation_type(req.generation_type)
    loader = normalize_video_loader(req.loader)
    bindings = discover_minimax_h3_bindings(req, info)
    classes = bindings["classes"]
    params, notes = _normalized_parameters(req)
    clip_topology = bindings.get("text_encoder_topology") or {}
    if clip_topology.get("format") == "gguf":
        notes.append(
            f"H3 text encoder is GGUF and is loaded independently through {clip_topology.get('class_type') or 'a GGUF CLIP loader'}; "
            "the main diffusion model may remain safetensors/UNET. Any conversion-specific MMProj pairing is owned by the selected Comfy loader/node pack."
        )
    workflow: dict[str, Any] = {}
    next_id = 1

    # Model and optional quality/speed patches.
    workflow[str(next_id)] = {"class_type": classes["model_loader"], "inputs": _model_loader_inputs(req, bindings, info)}
    model_ref: list[Any] = [str(next_id), 0]
    next_id += 1

    turbo_name = req.h3_turbo_lora or bindings["models"].get("turbo_lora") or ""
    if req.h3_turbo_enabled:
        if not classes.get("lora"):
            raise ValueError("H3 Turbo is enabled but no model-only LoRA loader is installed in ComfyUI.")
        if not turbo_name:
            raise ValueError("H3 Turbo is enabled but no MiniMax H3 Turbo/LightX2V LoRA was discovered or selected.")
        model_ref, next_id = _append_model_patch(workflow, next_id, classes["lora"], model_ref, {"lora_name": turbo_name, "strength_model": float(req.h3_turbo_strength)})

    # Native sigma shift always owns the H3 audio/video schedule.
    model_ref, next_id = _append_model_patch(workflow, next_id, classes["sigma_shift"], model_ref, {"shift_video": params["h3_shift_video"], "shift_audio": params["h3_shift_audio"]})

    if req.enable_sage_attention:
        sage_cls = classes.get("sage_h3") or classes.get("sage_general")
        if sage_cls:
            sage_inputs: dict[str, Any] = {}
            if sage_cls == classes.get("sage_general"):
                _set_if_supported(sage_inputs, info, sage_cls, "sage_attention", req.sage_attention_mode or "auto", fallback=True)
                _set_if_supported(sage_inputs, info, sage_cls, "sage_attn_mode", req.sage_attention_mode or "auto")
                _set_if_supported(sage_inputs, info, sage_cls, "enabled", True)
            model_ref, next_id = _append_model_patch(workflow, next_id, sage_cls, model_ref, sage_inputs)
        else:
            notes.append("Sage Attention was requested but no compatible H3/KJNodes Sage patch is visible; compile continued without Sage.")

    if req.h3_acceleration_mode == "spectrum":
        spectrum_cls = classes.get("spectrum")
        if spectrum_cls:
            spectrum_inputs = {
                "enabled": True, "blend_weight": max(0.0, min(1.0, req.h3_spectrum_blend)), "degree": 1,
                "ridge_lambda": 0.10, "window_size": 2.0, "flex_window": 0.75, "warmup_steps": 1,
                "tail_actual_steps": 1, "max_history": 8, "debug": False, "history_storage": "system_ram",
                "bootstrap_first_forecast": True, "offline_smoothing_replay": True, "audio_blend_weight": 0.0,
                "offline_archive_storage": "system_ram", "model_aware_mode": "off",
            }
            # Drop optional names absent from older Spectrum versions when object_info is known.
            available = _input_groups(info, spectrum_cls)
            if available:
                spectrum_inputs = {k: v for k, v in spectrum_inputs.items() if k in available}
            model_ref, next_id = _append_model_patch(workflow, next_id, spectrum_cls, model_ref, spectrum_inputs)
        else:
            raise ValueError("Spectrum acceleration was selected but ComfyUI-Spectrum-MiniMax-H3 is not installed.")
    elif req.h3_acceleration_mode == "block_cache":
        block_cls = classes.get("block_cache")
        if block_cls:
            block_inputs = {
                "residual_diff_threshold": max(0.0, min(1.0, req.h3_block_cache_threshold)), "start_percent": 0.08,
                "end_percent": 0.95, "max_consecutive_hits": 2, "cache_device": "cpu", "metric_stride": 8, "debug": False,
            }
            available = _input_groups(info, block_cls)
            if available:
                block_inputs = {k: v for k, v in block_inputs.items() if k in available}
            model_ref, next_id = _append_model_patch(workflow, next_id, block_cls, model_ref, block_inputs)
        else:
            raise ValueError("T8 BlockCache was selected but the MiniMax H3 Block Cache node is not installed.")

    # Text encoder + two VAEs.
    clip_id = str(next_id); workflow[clip_id] = {"class_type": classes["clip_loader"], "inputs": _clip_loader_inputs(req, bindings, info)}; next_id += 1
    video_vae_id = str(next_id); workflow[video_vae_id] = {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(classes["vae_loader"], req.vae_name or bindings["models"]["video_vae"], info)}; next_id += 1
    audio_vae_id = str(next_id); workflow[audio_vae_id] = {"class_type": classes["vae_loader"], "inputs": _vae_loader_inputs(classes["vae_loader"], req.audio_vae_name or bindings["models"]["audio_vae"], info)}; next_id += 1

    # Source/reference loader nodes.
    first_ref: list[Any] | None = None
    last_ref: list[Any] | None = None
    if mode == "img2vid":
        source_name = req.source_image_comfy_name or req.source_image_name or Path(req.source_image).name
        load_id = str(next_id); workflow[load_id] = {"class_type": classes["load_image"], "inputs": {"image": source_name}}; next_id += 1
        if req.h3_keyframe_role.strip().casefold() == "last": last_ref = [load_id, 0]
        else: first_ref = [load_id, 0]
    elif mode == "first_last_frame":
        first_name = req.first_image_comfy_name or req.first_image_name or Path(req.first_image).name
        last_name = req.last_image_comfy_name or req.last_image_name or Path(req.last_image).name
        first_id = str(next_id); workflow[first_id] = {"class_type": classes["load_image"], "inputs": {"image": first_name}}; next_id += 1
        last_id = str(next_id); workflow[last_id] = {"class_type": classes["load_image"], "inputs": {"image": last_name}}; next_id += 1
        first_ref, last_ref = [first_id, 0], [last_id, 0]

    condition_id = str(next_id)
    if mode in {"reference_to_video", "vid2vid"}:
        cond_inputs: dict[str, Any] = {
            "clip": [clip_id, 0], "vae": [video_vae_id, 0], "audio_vae": [audio_vae_id, 0],
            "prompt": req.prompt.strip(), "width": params["width"], "height": params["height"], "length": params["frames"],
            "ref_image_size": req.h3_ref_image_size if req.h3_ref_image_size in {"match", "max"} else "match",
        }
        for index, media in enumerate(req.h3_reference_images[:9]):
            name = _media_input_name(media)
            node_id = str(next_id + 1); workflow[node_id] = {"class_type": classes["load_image"], "inputs": {"image": name}}
            cond_inputs[f"ref_images.ref_image_{index}"] = [node_id, 0]
            next_id += 1
        video_index_offset = 0
        if mode == "vid2vid":
            source_name = req.source_video_comfy_name or req.source_video_name or Path(req.source_video_path).name
            load_id = str(next_id + 1); workflow[load_id] = {"class_type": classes["load_video"], "inputs": {"file": source_name}}
            comp_id = str(next_id + 2); workflow[comp_id] = {"class_type": classes["video_components"], "inputs": {"video": [load_id, 0]}}
            cond_inputs["ref_videos.ref_video_0"] = [comp_id, 0]
            if req.preserve_audio:
                cond_inputs["ref_video_audios.ref_video_audio_0"] = [comp_id, 1]
            next_id += 2
            video_index_offset = 1
        for index, media in enumerate(req.h3_reference_videos[: 3 - video_index_offset]):
            name = _media_input_name(media)
            load_id = str(next_id + 1); workflow[load_id] = {"class_type": classes["load_video"], "inputs": {"file": name}}
            comp_id = str(next_id + 2); workflow[comp_id] = {"class_type": classes["video_components"], "inputs": {"video": [load_id, 0]}}
            target_index = index + video_index_offset
            cond_inputs[f"ref_videos.ref_video_{target_index}"] = [comp_id, 0]
            if media.include_audio:
                cond_inputs[f"ref_video_audios.ref_video_audio_{target_index}"] = [comp_id, 1]
            next_id += 2
        for index, media in enumerate(req.h3_reference_audios[:3]):
            name = _media_input_name(media)
            load_id = str(next_id + 1); workflow[load_id] = {"class_type": classes["load_audio"], "inputs": {"audio": name}}
            cond_inputs[f"ref_audios.ref_audio_{index}"] = [load_id, 0]
            next_id += 1
        workflow[condition_id] = {"class_type": classes["condition_ref2va"], "inputs": cond_inputs}
    else:
        cond_inputs = {"clip": [clip_id, 0], "vae": [video_vae_id, 0], "prompt": req.prompt.strip(), "width": params["width"], "height": params["height"], "length": params["frames"]}
        if first_ref is not None: cond_inputs["first_frame"] = first_ref
        if last_ref is not None: cond_inputs["last_frame"] = last_ref
        workflow[condition_id] = {"class_type": classes["condition_fl2va"], "inputs": cond_inputs}
    next_id += 1

    noise_id = str(next_id); workflow[noise_id] = {"class_type": classes["noise"], "inputs": {"noise_seed": params["seed"]}}; next_id += 1
    sampler_select_id = str(next_id); workflow[sampler_select_id] = {"class_type": classes["sampler_select"], "inputs": {"sampler_name": params["sampler"]}}; next_id += 1
    scheduler_id = str(next_id); workflow[scheduler_id] = {"class_type": classes["scheduler"], "inputs": {"model": model_ref, "scheduler": params["scheduler"], "steps": params["steps"], "denoise": 1.0}}; next_id += 1
    guider_id = str(next_id); workflow[guider_id] = {"class_type": classes["guider"], "inputs": {"model": model_ref, "conditioning": [condition_id, 0]}}; next_id += 1
    sampler_id = str(next_id); workflow[sampler_id] = {"class_type": classes["sampler"], "inputs": {"noise": [noise_id, 0], "guider": [guider_id, 0], "sampler": [sampler_select_id, 0], "sigmas": [scheduler_id, 0], "latent_image": [condition_id, 1]}}; next_id += 1
    decode_video_id = str(next_id); workflow[decode_video_id] = {"class_type": classes["decode_video"], "inputs": {"samples": [sampler_id, 0], "vae": [video_vae_id, 0]}}; next_id += 1
    decode_audio_id = str(next_id); workflow[decode_audio_id] = {"class_type": classes["decode_audio"], "inputs": {"samples": [sampler_id, 0], "vae": [audio_vae_id, 0]}}; next_id += 1
    create_id = str(next_id); workflow[create_id] = {"class_type": classes["create_video"], "inputs": {"images": [decode_video_id, 0], "audio": [decode_audio_id, 0], "fps": H3_FPS, "bit_depth": 8}}; next_id += 1
    prefix = sanitize_path_part(req.filename_prefix or f"Neo_Video_MiniMax_H3_{mode}", fallback="Neo_Video_MiniMax_H3")
    save_formats = _combo_values(info, classes["save_video"], "format")
    requested_format = req.output_format if req.output_format in {"auto", "mp4", "webm", "mkv"} else "auto"
    save_format = requested_format if requested_format in save_formats else "auto"
    save_id = str(next_id); workflow[save_id] = {"class_type": classes["save_video"], "inputs": {"video": [create_id, 0], "filename_prefix": f"video/{prefix}", "format": save_format, "codec": "auto"}}

    client_id = f"neo-video-h3-{uuid4().hex[:10]}"
    return {
        "schema_version": SCHEMA_VERSION, "surface": "video", "phase": PHASE,
        "route_id": f"minimax_h3.{loader}.{mode}", "compiled_at": _now(),
        "parameters": {key: value for key, value in params.items() if key != "profile"}, "profile": params["profile"],
        "bindings": bindings, "workflow": workflow, "prompt_api_payload": {"prompt": workflow, "client_id": client_id}, "client_id": client_id, "normalization_notes": notes,
        "h3": {
            "native_audio": True, "audio_channels": "stereo", "fps": H3_FPS, "conditioning": "ref2va" if mode in {"reference_to_video", "vid2vid"} else "fl2va",
            "keyframe_role": req.h3_keyframe_role, "source_video": bool(req.source_video_path or req.source_video_name or req.source_video_comfy_name), "source_video_audio": bool(req.preserve_audio) if mode == "vid2vid" else None, "reference_counts": {"images": len(req.h3_reference_images), "videos": len(req.h3_reference_videos) + (1 if mode == "vid2vid" else 0), "audios": len(req.h3_reference_audios)},
            "acceleration": req.h3_acceleration_mode, "turbo": req.h3_turbo_enabled,
        },
        "rules": [
            "MiniMax H3 native ComfyUI nodes own T2VA/FL2VA/Ref2VA conditioning and packed audio-video latent creation.",
            "Neo keeps Spectrum and T8 BlockCache mutually exclusive because both are approximate transformer-skipping accelerators.",
            "Turbo LoRA is optional and separate from the base quality path.",
            "H3 output is created at 24 FPS with decoded native audio attached before SaveVideo.",
            "Reference video inputs are split into frames/audio through core LoadVideo + GetVideoComponents.",
        ],
    }


def _post_json(base_url: str, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NeoStudioMiniMaxH3Compiler/1.0"}, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - user-configured local Comfy URL.
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data) if data else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _attach_video_output_record(result: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        ledger = register_video_generation_result(result, request=request_payload or {})
    except Exception as exc:  # noqa: BLE001
        return {**result, "neo_persisted": {"ok": False, "error": f"Video output ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def video_minimax_h3_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = MiniMaxH3CompileRequest.from_payload(payload)
    errors = _validate_request(req)
    nf, nl, nt = normalize_video_family(req.family), normalize_video_loader(req.loader), normalize_video_generation_type(req.generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    if not route or route.route_id not in {f"minimax_h3.{loader}.{mode}" for loader in SUPPORTED_LOADERS for mode in SUPPORTED_TYPES}:
        errors.append("No selectable MiniMax H3 route matches the requested family/loader/generation type.")
    if errors:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": PHASE, "ok": False, "queued": False, "errors": errors, "error": errors[0], "request": req.payload(), "route": route.payload() if route else None}

    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    object_info = object_info_override or {}
    warnings: list[str] = []
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 3.0)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback H3 bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}

    bindings = discover_minimax_h3_bindings(req, object_info)
    clip_topology = bindings.get("text_encoder_topology") or {}
    selected_clip_loader = str(clip_topology.get("class_type") or "")
    if object_info and selected_clip_loader and selected_clip_loader not in object_info:
        selected_clip = str(req.clip_name or clip_topology.get("selected_model") or "")
        return {
            "schema_version": SCHEMA_VERSION, "surface": "video", "phase": PHASE,
            "ok": False, "queued": False,
            "error": f"MiniMax H3 text encoder '{selected_clip}' requires loader '{selected_clip_loader}', but that loader is not visible in ComfyUI /object_info.",
            "errors": [f"Missing H3 text-encoder loader: {selected_clip_loader}"],
            "request": req.payload(), "route": route.payload(),
            "text_encoder_topology": clip_topology,
            "backend": {"profile": profile, "base_url": base_url},
        }
    selected_model = req.model_name or req.gguf_name or req.unet_name or bindings["models"]["model_name"]
    low_model = selected_model.casefold()
    if nt in {"reference_to_video", "vid2vid"} and "fl2va" in low_model:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": PHASE, "ok": False, "queued": False, "error": "H3 Ref2VA/Video-to-Video requires a Ref2VA model, but the selected model looks like FL2VA.", "request": req.payload(), "route": route.payload()}
    if nt not in {"reference_to_video", "vid2vid"} and "ref2va" in low_model:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": PHASE, "ok": False, "queued": False, "error": "H3 text/keyframe routes require an FL2VA model, but the selected model looks like Ref2VA.", "request": req.payload(), "route": route.payload()}

    readiness = route_node_readiness(route.route_id, object_info) if object_info else {"ready": False, "missing_required": [], "missing_recommended": []}
    try:
        compiled = build_minimax_h3_workflow(req, object_info=object_info)
    except ValueError as exc:
        return {
            "schema_version": SCHEMA_VERSION, "surface": "video", "phase": PHASE,
            "ok": False, "queued": False, "error": str(exc), "errors": [str(exc)],
            "request": req.payload(), "route": route.payload(), "route_readiness": readiness,
            "backend": {"profile": profile, "base_url": base_url},
        }
    discovery = video_model_discovery_from_object_info(object_info, family=nf, loader=nl, generation_type=nt, high_noise_model=selected_model, clip_name=req.clip_name, vae_name=req.vae_name, high_noise_lora=req.h3_turbo_lora) if object_info else None
    output_paths = get_video_output_paths(nt, create=True)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'minimax_h3')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": [*warnings, *compiled.get("normalization_notes", [])], "route_readiness": readiness, "model_discovery": discovery}
    sidecar_path.write_text(json.dumps(sidecar_payload, indent=2), encoding="utf-8")
    response = {**sidecar_payload, "ok": True, "queued": False, "dry_run": True, "backend": {"profile": profile, "base_url": base_url}, "neo_output": {"category": output_paths.category, "root": output_paths.relative_output_dir, "metadata_sidecar": str(sidecar_path)}}
    return _attach_video_output_record(response, req.payload())


def _handoff_single(data: dict[str, Any], path_key: str, name_key: str, comfy_key: str, base_url: str, prefix: str, timeout: float) -> tuple[dict[str, Any], dict[str, Any]]:
    value = str(data.get(path_key) or data.get(name_key) or "")
    existing = str(data.get(comfy_key) or "")
    result = prepare_comfy_input_file_handoff(value, base_url, timeout=timeout, prefix=prefix, existing_comfy_name=existing)
    if result.get("ok"):
        data[name_key] = result.get("comfy_name") or data.get(name_key) or ""
        data[comfy_key] = result.get("comfy_name") or ""
    return data, result


def _handoff_references(data: dict[str, Any], base_url: str, timeout: float) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    logs: list[dict[str, Any]] = []
    errors: list[str] = []
    for key, prefix in (("h3_reference_images", "neo_h3_ref_image"), ("h3_reference_videos", "neo_h3_ref_video"), ("h3_reference_audios", "neo_h3_ref_audio")):
        patched: list[dict[str, Any]] = []
        for index, item in enumerate(data.get(key) or []):
            row = item if isinstance(item, dict) else {"path": str(item)}
            result = prepare_comfy_input_file_handoff(str(row.get("path") or row.get("name") or ""), base_url, timeout=timeout, prefix=f"{prefix}_{index+1}", existing_comfy_name=str(row.get("comfy_name") or ""))
            logs.append({"kind": key, "index": index, **result})
            if not result.get("ok"):
                errors.append(f"{key}[{index + 1}]: {result.get('error') or 'Comfy input handoff failed'}")
            patched.append({**row, "comfy_name": result.get("comfy_name") or row.get("comfy_name") or ""})
        data[key] = patched
    return data, logs, errors


def video_minimax_h3_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 10.0) -> dict[str, Any]:
    initial = dict(payload or {})
    req = MiniMaxH3CompileRequest.from_payload(initial)
    if req.dry_run:
        return video_minimax_h3_compile_payload(initial, object_info_override=object_info_override)
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    mode = normalize_video_generation_type(req.generation_type)
    handoff_logs: list[dict[str, Any]] = []
    handoff_errors: list[str] = []
    data = dict(initial)
    if mode == "img2vid":
        data, result = _handoff_single(data, "source_image", "source_image_name", "source_image_comfy_name", base_url, "neo_h3_keyframe", timeout)
        handoff_logs.append({"kind": "source_image", **result})
        if not result.get("ok"): handoff_errors.append(result.get("error") or "H3 source image handoff failed")
    elif mode == "first_last_frame":
        data, first = _handoff_single(data, "first_image", "first_image_name", "first_image_comfy_name", base_url, "neo_h3_first", timeout)
        data, last = _handoff_single(data, "last_image", "last_image_name", "last_image_comfy_name", base_url, "neo_h3_last", timeout)
        handoff_logs.extend(({"kind": "first_image", **first}, {"kind": "last_image", **last}))
        if not first.get("ok"): handoff_errors.append(first.get("error") or "H3 first-frame handoff failed")
        if not last.get("ok"): handoff_errors.append(last.get("error") or "H3 last-frame handoff failed")
    elif mode == "reference_to_video":
        data, logs, errors = _handoff_references(data, base_url, timeout)
        handoff_logs.extend(logs); handoff_errors.extend(errors)
    elif mode == "vid2vid":
        data, source = _handoff_single(data, "source_video_path", "source_video_name", "source_video_comfy_name", base_url, "neo_h3_vid2vid_source", timeout)
        handoff_logs.append({"kind": "source_video", **source})
        if not source.get("ok"):
            handoff_errors.append(source.get("error") or "H3 Video-to-Video source handoff failed")
        data, logs, errors = _handoff_references(data, base_url, timeout)
        handoff_logs.extend(logs); handoff_errors.extend(errors)
    if handoff_errors:
        return {"schema_version": SCHEMA_VERSION, "surface": "video", "phase": PHASE, "ok": False, "queued": False, "error": handoff_errors[0], "errors": handoff_errors, "input_handoff": handoff_logs, "request": data}

    compile_payload = video_minimax_h3_compile_payload({**data, "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return {**compile_payload, "input_handoff": handoff_logs}
    readiness = compile_payload.get("route_readiness", {}) or {}
    if readiness.get("missing_required"):
        missing = ", ".join(str(item) for item in readiness.get("missing_required") or [])
        return {**compile_payload, "ok": False, "queued": False, "error": f"Selected MiniMax H3 route is missing required ComfyUI nodes: {missing}", "input_handoff": handoff_logs}
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI H3 queue failed: {exc}", "input_handoff": handoff_logs}
    response = {
        **compile_payload,
        "ok": True,
        "queued": True,
        "dry_run": False,
        "queue_response": queue_response,
        "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or "",
        "client_id": compile_payload.get("client_id") or (compile_payload.get("prompt_api_payload") or {}).get("client_id") or "",
        "input_handoff": handoff_logs,
    }
    return _attach_video_output_record(response, data)
