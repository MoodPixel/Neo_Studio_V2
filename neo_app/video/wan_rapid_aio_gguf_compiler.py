from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import uuid4
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from neo_app.video.backend_probe import _get_json, video_backend_profile_payload
from neo_app.video.comfy_input_handoff import prepare_video_source_image_handoff, resolve_video_source_image_path
from neo_app.video.output_records import register_video_generation_result
from neo_app.video.model_discovery import RAPID_AIO_NONE_TEST_ID, video_model_discovery_from_object_info
from neo_app.video.output_paths import get_video_output_paths, sanitize_path_part
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader
from neo_app.video.sage_attention_adapter import build_sage_attention_node_inputs, build_wan22_sage_attention_plan
from neo_app.video.teacache_adapter import build_teacache_node_inputs, build_wan22_teacache_plan
from neo_app.video.low_vram_adapter import build_vae_decode_inputs, build_wan_block_swap_node_inputs, build_wan22_low_vram_plan

SCHEMA_VERSION: Final[str] = "neo.video.wan22_rapid_aio_gguf.production_route.v25_9_19_phase10ab"
PHASE: Final[str] = "V25.9.19-10ab"
RAPID_ROUTE_IDS: Final[set[str]] = {"wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"}
MATRIX_ALL_ID: Final[str] = "matrix_all"
PRODUCTION_VARIANT_ID: Final[str] = "auto_encoder_auto_vae"
VACE_DIMENSION_MULTIPLE: Final[int] = 16
VACE_VAE_STRIDE: Final[int] = 8

TEMPLATE_DIR: Final[Path] = Path(__file__).resolve().parent / "workflows"
I2V_TEMPLATE: Final[Path] = TEMPLATE_DIR / "wan22_i2v14_dual_noise_native.json"
SAVE_VIDEO_NODE_PRIORITY: Final[tuple[str, ...]] = ("SaveVideo", "VHS_SaveVideo", "VideoSave")
IMAGE_VIDEO_OUTPUT_NODE_PRIORITY: Final[tuple[str, ...]] = ("VHS_VideoCombine", "VideoCombine", "SaveWEBM", "SaveAnimatedWEBP")
DEBUG_OUTPUT_NODE_PRIORITY: Final[tuple[str, ...]] = ("PreviewImage", "SaveImage")
RAPID_AIO_FRAME_MODES: Final[set[str]] = {"text_only", "start_frame", "start_end_frame", "end_frame"}
VACE_TO_VIDEO_CANDIDATES: Final[tuple[str, ...]] = ("WanVaceToVideo", "WanVACEToVideo", "WanVideoVaceToVideo", "WanVideoVACEToVideo")
VACE_FIRST_LAST_CANDIDATES: Final[tuple[str, ...]] = ("VACEFirstToLastFrame", "WanVACEFirstToLastFrame", "WanFirstToLastFrame", "WanVideoFirstToLastFrame")
OUTPUT_NODE_CLASSES: Final[set[str]] = set(SAVE_VIDEO_NODE_PRIORITY + IMAGE_VIDEO_OUTPUT_NODE_PRIORITY + DEBUG_OUTPUT_NODE_PRIORITY)
OBJECT_INFO_AUDIT_CLASSES: Final[tuple[str, ...]] = (
    "WanVideoModelLoaderGGUFAdvanced",
    "WanVideoModelLoaderGGUF",
    "UNETLoaderGGUF",
    "UnetLoaderGGUF",
    "GGUFModelLoader",
    "GGUFLoader",
    "CLIPLoader",
    "CLIPLoaderGGUF",
    "DualCLIPLoaderGGUF",
    "DualCLIPLoader",
    "UMT5Loader",
    "T5Loader",
    "VAELoader",
    "VAELoaderGGUF",
    "WanVAELoader",
    "WanImageToVideo",
    *VACE_TO_VIDEO_CANDIDATES,
    *VACE_FIRST_LAST_CANDIDATES,
    "ModelSamplingSD3",
    "KSamplerAdvanced",
    "VAEDecode",
    "CreateVideo",
    *SAVE_VIDEO_NODE_PRIORITY,
    *IMAGE_VIDEO_OUTPUT_NODE_PRIORITY,
    *DEBUG_OUTPUT_NODE_PRIORITY,
)

MATRIX_VARIANTS: Final[tuple[dict[str, Any], ...]] = (
    {"id": PRODUCTION_VARIANT_ID, "label": "Auto encoder + Auto VAE", "external_text_encoder": True, "external_vae": True},
)

MODEL_LOADER_CANDIDATES: Final[tuple[str, ...]] = (
    "WanVideoModelLoaderGGUFAdvanced",
    "WanVideoModelLoaderGGUF",
    "UNETLoaderGGUF",
    "UnetLoaderGGUF",
    "GGUFModelLoader",
    "GGUFLoader",
)
TEXT_ENCODER_CANDIDATES: Final[tuple[str, ...]] = (
    "CLIPLoader",
    "CLIPLoaderGGUF",
    "DualCLIPLoaderGGUF",
    "DualCLIPLoader",
    "UMT5Loader",
    "T5Loader",
)
VAE_CANDIDATES: Final[tuple[str, ...]] = (
    "VAELoader",
    "VAELoaderGGUF",
    "WanVAELoader",
)
KNOWN_MODEL_INPUTS: Final[tuple[str, ...]] = ("unet_name", "model_name", "gguf_name", "ckpt_name", "diffusion_model_name")
KNOWN_TEXT_INPUTS: Final[tuple[str, ...]] = ("clip_name", "text_encoder_name", "t5_name", "encoder_name", "name")
KNOWN_VAE_INPUTS: Final[tuple[str, ...]] = ("vae_name", "model_name", "name")


@dataclass(frozen=True)
class WanRapidAioGgufCompileRequest:
    family: str = "wan22"
    loader: str = "rapid_aio_gguf"
    generation_type: str = "img2vid"
    rapid_aio_frame_mode: str = "start_frame"
    prompt: str = ""
    negative_prompt: str = ""
    source_image: str | None = None
    source_image_name: str | None = None
    first_image: str | None = None
    first_image_name: str | None = None
    last_image: str | None = None
    last_image_name: str | None = None
    width: int = 480
    height: int = 848
    frames: int = 121
    fps: float = 16.0
    steps: int = 4
    guidance: float = 1.0
    seed: int = -1
    sampler: str = "euler"
    scheduler: str = "simple"
    decode_mode: str = "tiled"
    tile_size: int = 384
    temporal_tile_size: int = 4096
    rapid_aio_model: str | None = None
    rapid_aio_text_encoder: str | None = None
    rapid_aio_vae: str | None = None
    rapid_aio_test_variant: str = PRODUCTION_VARIANT_ID
    rapid_aio_queue_test: bool = False
    allow_conventional_fallback_queue_test: bool = False
    performance_profile: str = "safe_12gb"
    enable_sage_attention: bool = False
    sage_attention_mode: str = "auto"
    enable_teacache: bool = False
    teacache_profile: str = "conservative"
    enable_cpu_offload: bool = False
    enable_vae_offload: bool = False
    enable_block_swap: bool = False
    block_swap_blocks: int | None = None
    enable_torch_compile: bool = False
    auto_resolve_vace_dimensions: bool = True
    profile_id: str | None = None
    dry_run: bool = True
    filename_prefix: str = "Neo_Video_WAN22_Rapid_AIO_GGUF"

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "WanRapidAioGgufCompileRequest":
        data = dict(payload or {})
        text_encoder = _clean_selection(data.get("rapid_aio_text_encoder", data.get("text_encoder", data.get("clip_name"))))
        vae = _clean_selection(data.get("rapid_aio_vae", data.get("vae_name")))
        return cls(
            family=normalize_video_family(data.get("family", "wan22")),
            loader=normalize_video_loader(data.get("loader", "rapid_aio_gguf")),
            generation_type=normalize_video_generation_type(data.get("generation_type", data.get("mode", "img2vid"))),
            rapid_aio_frame_mode=_normalize_rapid_aio_frame_mode(data.get("rapid_aio_frame_mode"), normalize_video_generation_type(data.get("generation_type", data.get("mode", "img2vid")))),
            prompt=str(data.get("prompt") or data.get("positive_prompt") or ""),
            negative_prompt=str(data.get("negative_prompt") or data.get("negative") or ""),
            source_image=_clean_selection(data.get("source_image") or data.get("first_image") or data.get("image")),
            source_image_name=_clean_selection(data.get("source_image_comfy_name") or data.get("comfy_source_image_name") or data.get("source_image_name") or data.get("first_image_name") or data.get("image_name")),
            first_image=_clean_selection(data.get("first_image") or data.get("source_image") or data.get("image")),
            first_image_name=_clean_selection(data.get("first_image_comfy_name") or data.get("first_image_name") or data.get("source_image_comfy_name") or data.get("source_image_name") or data.get("image_name")),
            last_image=_clean_selection(data.get("last_image") or data.get("end_image")),
            last_image_name=_clean_selection(data.get("last_image_comfy_name") or data.get("last_image_name") or data.get("end_image_name")),
            width=_int(data.get("width"), 480, 64, 4096),
            height=_int(data.get("height"), 848, 64, 4096),
            frames=_int(data.get("frames"), 121, 1, 4096),
            fps=_float(data.get("fps"), 16.0, 1.0, 240.0),
            steps=_int(data.get("steps"), 4, 1, 200),
            guidance=_float(data.get("guidance", data.get("cfg")), 1.0, 0.0, 30.0),
            seed=_int(data.get("seed"), -1, -1, 999999999999999),
            sampler=str(data.get("sampler") or "euler"),
            scheduler=str(data.get("scheduler") or "simple"),
            decode_mode=str(data.get("decode_mode") or "tiled"),
            tile_size=_int(data.get("tile_size"), 384, 64, 4096),
            temporal_tile_size=_int(data.get("temporal_tile_size"), 4096, 1, 65536),
            rapid_aio_model=_clean_selection(data.get("rapid_aio_model", data.get("model_name", data.get("gguf_model")))),
            rapid_aio_text_encoder=text_encoder,
            rapid_aio_vae=vae,
            rapid_aio_test_variant=str(data.get("rapid_aio_test_variant") or data.get("test_variant") or PRODUCTION_VARIANT_ID),
            rapid_aio_queue_test=_bool(data.get("rapid_aio_queue_test", data.get("queue_test", False))),
            allow_conventional_fallback_queue_test=_bool(data.get("allow_conventional_fallback_queue_test", False)),
            performance_profile=str(data.get("performance_profile") or "safe_12gb"),
            enable_sage_attention=_bool(data.get("enable_sage_attention", False)),
            sage_attention_mode=str(data.get("sage_attention_mode") or "auto"),
            enable_teacache=_bool(data.get("enable_teacache", False)),
            teacache_profile=str(data.get("teacache_profile") or "conservative"),
            enable_cpu_offload=_bool(data.get("enable_cpu_offload", False)),
            enable_vae_offload=_bool(data.get("enable_vae_offload", False)),
            enable_block_swap=_bool(data.get("enable_block_swap", False)),
            block_swap_blocks=_int(data.get("block_swap_blocks"), 12, 0, 99),
            enable_torch_compile=_bool(data.get("enable_torch_compile", False)),
            auto_resolve_vace_dimensions=_bool(data.get("auto_resolve_vace_dimensions", True)),
            profile_id=_clean_selection(data.get("profile_id") or data.get("backend_profile_id")),
            dry_run=_bool(data.get("dry_run", True)),
            filename_prefix=str(data.get("filename_prefix") or "Neo_Video_WAN22_Rapid_AIO_GGUF"),
        )

    def payload(self) -> dict[str, Any]:
        return _production_request_payload(self)


def _clean_selection(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or text in {"provider_default", "automatic", "auto"} or text.startswith("select_"):
        return None
    return text


def _normalize_rapid_aio_frame_mode(value: Any, generation_type: str | None = None) -> str:
    gen = normalize_video_generation_type(generation_type or "img2vid")
    if gen in {"txt2vid", "text_to_video", "t2v"}:
        return "text_only"
    key = str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "start_frame",
        "i2v": "start_frame",
        "img2vid": "start_frame",
        "image_to_video": "start_frame",
        "start": "start_frame",
        "first": "start_frame",
        "first_frame": "start_frame",
        "source": "start_frame",
        "source_image": "start_frame",
        "start_only": "start_frame",
        "start_frame_only": "start_frame",
        "start_end": "start_end_frame",
        "start_end_frame": "start_end_frame",
        "first_last": "start_end_frame",
        "first_to_last": "start_end_frame",
        "first_last_frame": "start_end_frame",
        "end": "end_frame",
        "last": "end_frame",
        "last_frame": "end_frame",
        "end_only": "end_frame",
        "end_frame_only": "end_frame",
        "text": "text_only",
        "text_only": "text_only",
        "t2v": "text_only",
        "txt2vid": "text_only",
    }
    mode = aliases.get(key, key)
    return mode if mode in RAPID_AIO_FRAME_MODES else "start_frame"


def _bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().casefold()
    return text in {"1", "true", "yes", "y", "on", "enabled"}


def _int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _production_request_payload(req: WanRapidAioGgufCompileRequest) -> dict[str, Any]:
    payload = asdict(req)
    # Phase 10z: legacy test/matrix controls must not leak into route logs,
    # sidecars, output metadata, or user-facing diagnostics.
    for key in ("rapid_aio_test_variant", "rapid_aio_queue_test", "allow_conventional_fallback_queue_test"):
        payload.pop(key, None)
    mode = _normalize_rapid_aio_frame_mode(req.rapid_aio_frame_mode, req.generation_type)
    payload["rapid_aio_frame_mode"] = mode
    if mode == "text_only":
        for key in ("source_image", "source_image_name", "first_image", "first_image_name", "last_image", "last_image_name"):
            payload[key] = None
    elif mode == "start_frame":
        payload["first_image"] = payload.get("first_image") or payload.get("source_image")
        payload["first_image_name"] = payload.get("first_image_name") or payload.get("source_image_name")
        payload["last_image"] = None
        payload["last_image_name"] = None
    elif mode == "end_frame":
        payload["source_image"] = None
        payload["source_image_name"] = None
        payload["first_image"] = None
        payload["first_image_name"] = None
    else:
        payload["first_image"] = payload.get("first_image") or payload.get("source_image")
        payload["first_image_name"] = payload.get("first_image_name") or payload.get("source_image_name")
    return payload


def _sanitize_rapid_aio_payload(payload: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(payload or {})
    for key in ("rapid_aio_test_variant", "test_variant", "rapid_aio_queue_test", "queue_test", "allow_conventional_fallback_queue_test"):
        cleaned.pop(key, None)
    generation_type = normalize_video_generation_type(cleaned.get("generation_type", cleaned.get("mode", "img2vid")))
    frame_mode = _normalize_rapid_aio_frame_mode(cleaned.get("rapid_aio_frame_mode"), generation_type)
    cleaned["rapid_aio_frame_mode"] = frame_mode
    if frame_mode == "text_only":
        for key in ("source_image", "source_image_name", "source_image_comfy_name", "comfy_source_image_name", "first_image", "first_image_name", "first_image_comfy_name", "last_image", "last_image_name", "last_image_comfy_name"):
            cleaned.pop(key, None)
    elif frame_mode == "start_frame":
        for key in ("last_image", "last_image_name", "last_image_comfy_name"):
            cleaned.pop(key, None)
    elif frame_mode == "end_frame":
        for key in ("source_image", "source_image_name", "source_image_comfy_name", "comfy_source_image_name", "first_image", "first_image_name", "first_image_comfy_name"):
            cleaned.pop(key, None)
    return cleaned


RAPID_AIO_FORBIDDEN_DUAL_NODE_IDS: Final[set[str]] = {"129:86", "129:103"}
RAPID_AIO_FORBIDDEN_DUAL_CLASS_HINTS: Final[tuple[str, ...]] = ("high_noise", "low_noise", "dual_noise")


def _rapid_aio_single_model_assertion(prompt: dict[str, Any]) -> dict[str, Any]:
    sampler_nodes = [node_id for node_id, node in prompt.items() if isinstance(node, dict) and node.get("class_type") == "KSamplerAdvanced"]
    forbidden_ids = sorted(node_id for node_id in RAPID_AIO_FORBIDDEN_DUAL_NODE_IDS if node_id in prompt)
    forbidden_hints: list[str] = []
    for node_id, node in prompt.items():
        if not isinstance(node, dict):
            continue
        blob = json.dumps(node, sort_keys=True, default=str).casefold()
        if any(hint in blob for hint in RAPID_AIO_FORBIDDEN_DUAL_CLASS_HINTS):
            forbidden_hints.append(str(node_id))
    errors: list[str] = []
    if len(sampler_nodes) != 1:
        errors.append(f"Rapid AIO single-model graph must have exactly one KSamplerAdvanced node; found {len(sampler_nodes)}.")
    if forbidden_ids:
        errors.append(f"Rapid AIO single-model graph still contains dual-noise node ids: {', '.join(forbidden_ids)}.")
    if forbidden_hints:
        errors.append(f"Rapid AIO single-model graph still contains high/low dual-noise hints in nodes: {', '.join(sorted(set(forbidden_hints)))}.")
    return {
        "ok": not errors,
        "sampler_node_ids": sampler_nodes,
        "sampler_count": len(sampler_nodes),
        "forbidden_dual_node_ids": forbidden_ids,
        "forbidden_dual_hint_nodes": sorted(set(forbidden_hints)),
        "errors": errors,
    }


def _input_groups(entry: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entry, dict):
        return {}
    raw = entry.get("input", {})
    if not isinstance(raw, dict):
        return {}
    merged: dict[str, Any] = {}
    for group_name in ("required", "optional"):
        group = raw.get(group_name)
        if isinstance(group, dict):
            merged.update(group)
    return merged


def _enum_values(spec: Any) -> list[str]:
    if isinstance(spec, list) and spec and isinstance(spec[0], list):
        return [str(item) for item in spec[0]]
    if isinstance(spec, dict):
        for key in ("values", "options", "choices"):
            if isinstance(spec.get(key), list):
                return [str(item) for item in spec[key]]
    return []


def _load_i2v_template() -> dict[str, Any]:
    return json.loads(I2V_TEMPLATE.read_text(encoding="utf-8"))


def _output_types(entry: dict[str, Any] | None) -> list[str]:
    if not isinstance(entry, dict):
        return []
    raw = entry.get("output") or entry.get("outputs") or []
    if isinstance(raw, list):
        return [str(item) for item in raw]
    return []


def _first_output_index(object_info: dict[str, Any], class_type: str, wanted: tuple[str, ...], fallback: int | None = None) -> int | None:
    outputs = _output_types(object_info.get(class_type))
    wanted_folded = {item.casefold() for item in wanted}
    for idx, value in enumerate(outputs):
        if str(value).casefold() in wanted_folded:
            return idx
    return fallback


def _has_output_type(object_info: dict[str, Any], class_type: str, wanted: tuple[str, ...]) -> bool:
    return _first_output_index(object_info, class_type, wanted, None) is not None


def _comfy_image_name(source_image: str | None, source_image_name: str | None = None) -> str:
    candidate = Path(str(source_image_name or source_image or "")).name
    return sanitize_path_part(candidate, fallback="neo_video_source.png")


def _default_for_input_spec(spec: Any) -> Any:
    values = _enum_values(spec)
    if values:
        for preferred in ("default", "auto", "enable", "disable", "cpu", "cuda:0"):
            for value in values:
                if str(value).casefold() == preferred:
                    return value
        return values[0]
    if isinstance(spec, list) and len(spec) > 1 and isinstance(spec[1], dict):
        meta = spec[1]
        if "default" in meta:
            return meta.get("default")
        type_name = str(spec[0] if spec else "").casefold()
        if "float" in type_name:
            return 0.0
        if "int" in type_name:
            return 0
        if "bool" in type_name:
            return False
    if isinstance(spec, dict) and "default" in spec:
        return spec.get("default")
    return None


def _node_inputs_with_defaults(entry: dict[str, Any] | None, selected_field: str, selected_value: str) -> dict[str, Any]:
    inputs = {selected_field: selected_value}
    groups = _input_groups(entry)
    for field, spec in groups.items():
        if field in inputs:
            continue
        # Fill only simple required-ish fields. Link fields and rich tensors are intentionally not guessed.
        default = _default_for_input_spec(spec)
        if default is not None and str(field).casefold() not in {"model", "clip", "vae", "image", "samples", "latent_image", "positive", "negative"}:
            inputs[str(field)] = default
    return inputs


def _rapid_aio_log_dir() -> Path:
    log_dir = Path(__file__).resolve().parents[2] / "neo_data" / "logs" / "video"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def _write_latest_rapid_aio_log(filename: str, payload: dict[str, Any]) -> str:
    try:
        path = _rapid_aio_log_dir() / filename
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(path)
    except Exception:  # noqa: BLE001 - diagnostics must never break compile/generate.
        return ""


def _input_names(entry: dict[str, Any] | None) -> set[str]:
    return {str(name) for name in _input_groups(entry).keys()}


def _is_output_node(entry: dict[str, Any] | None) -> bool:
    if not isinstance(entry, dict):
        return False
    value = entry.get("output_node", False)
    if isinstance(value, bool):
        return value
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def _object_info_signature(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    entry = object_info.get(class_type) if isinstance(object_info, dict) else None
    required = []
    optional = []
    if isinstance(entry, dict):
        raw = entry.get("input", {}) if isinstance(entry.get("input"), dict) else {}
        required = sorted(str(item) for item in (raw.get("required") or {}).keys()) if isinstance(raw.get("required"), dict) else []
        optional = sorted(str(item) for item in (raw.get("optional") or {}).keys()) if isinstance(raw.get("optional"), dict) else []
    return {
        "class_type": class_type,
        "present": isinstance(entry, dict),
        "required_inputs": required,
        "optional_inputs": optional,
        "outputs": _output_types(entry),
        "output_node": _is_output_node(entry),
    }


def rapid_aio_object_info_summary(object_info: dict[str, Any] | None, prompt: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info if isinstance(object_info, dict) else {}
    classes = []
    seen: set[str] = set()
    for class_type in OBJECT_INFO_AUDIT_CLASSES:
        if class_type not in seen:
            classes.append(_object_info_signature(info, class_type))
            seen.add(class_type)
    prompt_output_nodes = []
    if isinstance(prompt, dict):
        for node_id, node in prompt.items():
            if not isinstance(node, dict):
                continue
            class_type = str(node.get("class_type") or "")
            if class_type in OUTPUT_NODE_CLASSES:
                signature = _object_info_signature(info, class_type)
                prompt_output_nodes.append({"node_id": str(node_id), **signature})
    return {
        "schema_version": "neo.video.wan22_rapid_aio_gguf.object_info_summary.v25_9_19_phase10aa",
        "phase": PHASE,
        "class_count": len(info),
        "classes": classes,
        "prompt_output_nodes": prompt_output_nodes,
        "verified_video_output_nodes": [
            item["class_type"]
            for item in classes
            if item["output_node"] and item["class_type"] in set(SAVE_VIDEO_NODE_PRIORITY + IMAGE_VIDEO_OUTPUT_NODE_PRIORITY)
        ],
        "debug_output_nodes": [
            item["class_type"]
            for item in classes
            if item["output_node"] and item["class_type"] in set(DEBUG_OUTPUT_NODE_PRIORITY)
        ],
    }


def _prompt_has_output(prompt: dict[str, Any]) -> bool:
    """Structural check only. Used for sidecar readability, not Comfy queue permission."""
    for node in prompt.values():
        if isinstance(node, dict) and str(node.get("class_type") or "") in OUTPUT_NODE_CLASSES:
            return True
    return False


def _prompt_has_verified_output(prompt: dict[str, Any], object_info: dict[str, Any]) -> bool:
    """Production output check for the Rapid AIO single-model route.

    Comfy validates output-node status internally at /prompt. Some custom node packs do
    not expose a reliable output_node=true flag through /object_info even though the
    same SaveVideo node queues correctly in the dual WAN route. For Rapid AIO, require
    a real output-class node in the prompt and, when object_info is available, require
    that class to be present locally. Do not block only because output_node is false or
    missing.
    """
    info_available = isinstance(object_info, dict) and bool(object_info)
    for node in prompt.values():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        if class_type in OUTPUT_NODE_CLASSES and ((not info_available) or isinstance(object_info.get(class_type), dict)):
            return True
    return False


def _preferred_enum_value(spec: Any, preferred: tuple[str, ...]) -> Any:
    values = _enum_values(spec)
    if not values:
        return None
    folded = {item.casefold(): item for item in preferred}
    for value in values:
        key = str(value).casefold()
        if key in folded:
            return value
    for wanted in preferred:
        wanted_low = wanted.casefold()
        for value in values:
            if wanted_low in str(value).casefold():
                return value
    return values[0]


def _output_filename_prefix(req: WanRapidAioGgufCompileRequest) -> str:
    return f"video/{sanitize_path_part(req.filename_prefix, 'Neo_Video_WAN22_Rapid_AIO_GGUF')}"


def _resolve_video_output_node(object_info: dict[str, Any]) -> dict[str, Any]:
    info = object_info if isinstance(object_info, dict) else {}
    candidates: list[dict[str, Any]] = []
    for class_type in SAVE_VIDEO_NODE_PRIORITY:
        entry = info.get(class_type)
        if not isinstance(entry, dict):
            continue
        names = _input_names(entry)
        candidates.append({
            "class_type": class_type,
            "present": True,
            "output_node": _is_output_node(entry),
            "mode": "video_save",
            "accepts_video": ("video" in names) or not names,
            "accepts_images": False,
            "debug_only": False,
        })
    for class_type in IMAGE_VIDEO_OUTPUT_NODE_PRIORITY:
        entry = info.get(class_type)
        if not isinstance(entry, dict):
            continue
        names = _input_names(entry)
        candidates.append({
            "class_type": class_type,
            "present": True,
            "output_node": _is_output_node(entry),
            "mode": "images_to_video_output",
            "accepts_video": False,
            "accepts_images": ("images" in names) or ("image" in names) or not names,
            "debug_only": False,
        })
    debug_candidates = []
    for class_type in DEBUG_OUTPUT_NODE_PRIORITY:
        entry = info.get(class_type)
        if isinstance(entry, dict):
            debug_candidates.append({
                "class_type": class_type,
                "present": True,
                "output_node": _is_output_node(entry),
                "mode": "debug_image_output",
                "accepts_video": False,
                "accepts_images": True,
                "debug_only": True,
            })

    if not info:
        return {
            "ok": True,
            "selected": {"class_type": "SaveVideo", "present": False, "output_node": None, "mode": "video_save", "accepts_video": True, "accepts_images": False, "debug_only": False, "source": "template_fallback"},
            "candidates": candidates,
            "debug_candidates": debug_candidates,
            "reason": "Comfy /object_info unavailable; keeping the Rapid AIO SaveVideo output path and letting Comfy validate it at /prompt.",
        }
    for candidate in candidates:
        if candidate["accepts_video"] or candidate["accepts_images"]:
            return {
                "ok": True,
                "selected": candidate,
                "candidates": candidates,
                "debug_candidates": debug_candidates,
                "reason": "Local Comfy video output node class is present; using Rapid AIO production output without relying only on the output_node flag.",
            }
    return {
        "ok": False,
        "selected": None,
        "candidates": candidates,
        "debug_candidates": debug_candidates,
        "reason": "No SaveVideo/VHS/VideoCombine class was found in local /object_info. PreviewImage/SaveImage are debug-only and are not used for Rapid AIO video generation.",
    }


def _output_inputs_with_defaults(entry: dict[str, Any] | None) -> dict[str, Any]:
    inputs: dict[str, Any] = {}
    for field, spec in _input_groups(entry).items():
        default = _default_for_input_spec(spec)
        if default is not None:
            inputs[str(field)] = default
    return inputs


def _apply_verified_video_output_node(
    prompt: dict[str, Any],
    req: WanRapidAioGgufCompileRequest,
    object_info: dict[str, Any],
) -> dict[str, Any]:
    resolution = _resolve_video_output_node(object_info)
    selected = resolution.get("selected") if isinstance(resolution.get("selected"), dict) else None
    if not selected:
        prompt.pop("108", None)
        return resolution
    class_type = str(selected.get("class_type") or "")
    entry = object_info.get(class_type) if isinstance(object_info, dict) else None
    inputs = _output_inputs_with_defaults(entry)
    inputs["filename_prefix"] = _output_filename_prefix(req)
    if selected.get("mode") == "video_save":
        prompt["129:94"]["inputs"].update({"fps": req.fps, "bit_depth": 8, "images": ["129:87", 0]})
        inputs["video"] = ["129:94", 0]
        if "format" in _input_groups(entry) and not inputs.get("format"):
            inputs["format"] = _preferred_enum_value(_input_groups(entry).get("format"), ("auto", "mp4", "video/h264-mp4")) or "auto"
        if "codec" in _input_groups(entry) and not inputs.get("codec"):
            inputs["codec"] = _preferred_enum_value(_input_groups(entry).get("codec"), ("auto", "h264", "h.264")) or "auto"
        prompt["108"] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": "Rapid AIO Verified Video Output"}}
    else:
        prompt.pop("129:94", None)
        image_field = "images" if "images" in _input_names(entry) or "image" not in _input_names(entry) else "image"
        inputs[image_field] = ["129:87", 0]
        for fps_field in ("frame_rate", "fps"):
            if fps_field in _input_groups(entry):
                inputs[fps_field] = req.fps
                break
        if "format" in _input_groups(entry):
            inputs["format"] = _preferred_enum_value(_input_groups(entry).get("format"), ("video/h264-mp4", "mp4", "auto", "video/webm")) or inputs.get("format") or "video/h264-mp4"
        if "save_output" in _input_groups(entry):
            inputs["save_output"] = True
        if "pingpong" in _input_groups(entry):
            inputs["pingpong"] = False
        if "loop_count" in _input_groups(entry):
            inputs["loop_count"] = 0
        prompt["108"] = {"class_type": class_type, "inputs": inputs, "_meta": {"title": "Rapid AIO Verified Video Output"}}
    resolution["applied_node_id"] = "108"
    resolution["verified_output_node"] = _prompt_has_verified_output(prompt, object_info)
    return resolution


def _attach_video_output_record(result: dict[str, Any], request_payload: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        ledger = register_video_generation_result(result, request=request_payload or {})
    except Exception as exc:  # noqa: BLE001
        return {**result, "neo_persisted": {"ok": False, "error": f"Video output ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def _field_for_value(entry: dict[str, Any] | None, selected: str | None, known_fields: tuple[str, ...], fallback_predicate) -> str | None:
    inputs = _input_groups(entry)
    if selected:
        low_selected = selected.casefold()
        for field, spec in inputs.items():
            if any(value.casefold() == low_selected for value in _enum_values(spec)):
                return str(field)
    for field in known_fields:
        if field in inputs:
            return field
    for field, spec in inputs.items():
        values = _enum_values(spec)
        if values and any(fallback_predicate(str(value)) for value in values):
            return str(field)
    return next(iter(inputs.keys()), None) if inputs else None


def _pick_node(object_info: dict[str, Any], candidates: tuple[str, ...], selected: str | None, known_fields: tuple[str, ...], fallback_predicate) -> dict[str, Any]:
    info = object_info or {}
    for class_type in candidates:
        if class_type not in info:
            continue
        field = _field_for_value(info.get(class_type), selected, known_fields, fallback_predicate)
        if field:
            return {"class_type": class_type, "input_name": field, "source": "object_info", "present": True}
    # Fall back to a conventional signature so the sidecar still shows the intended graph shape.
    fallback_class = candidates[0]
    fallback_input = known_fields[0]
    return {"class_type": fallback_class, "input_name": fallback_input, "source": "conventional_fallback", "present": False}


def _first_catalog(discovery: dict[str, Any], key: str) -> str | None:
    catalogs = discovery.get("catalogs", {}) if isinstance(discovery.get("catalogs"), dict) else {}
    values = catalogs.get(key, []) if isinstance(catalogs.get(key), list) else []
    for value in values:
        text = str(value or "").strip()
        if text and text != RAPID_AIO_NONE_TEST_ID:
            return text
    return None


def _requested_variants(req: WanRapidAioGgufCompileRequest) -> list[dict[str, Any]]:
    # Phase 10y removes matrix/test selection from the Rapid AIO production path.
    # Keep the single external encoder + external VAE variant until a packed-loader
    # route is proven by object_info and implemented as its own production contract.
    return [MATRIX_VARIANTS[0]]


def _has_conventional_fallback(nodes: dict[str, Any]) -> bool:
    return any(isinstance(node, dict) and node.get("source") == "conventional_fallback" for node in nodes.values())


def _apply_rapid_aio_performance_adapters(
    prompt: dict[str, Any],
    req: WanRapidAioGgufCompileRequest,
    object_info: dict[str, Any],
    model_link: list[Any],
) -> tuple[list[Any], dict[str, Any], list[str], list[str]]:
    """Apply speed/VRAM model-patch adapters as a Rapid AIO single-model chain.

    Rapid AIO has one model branch. Internally it may reuse adapter helpers that expect
    high/low route arguments, but exported diagnostics are sanitized to rapid_aio/single_model.
    """
    warnings: list[str] = []
    errors: list[str] = []
    active_model_link = list(model_link)

    sage_plan = build_wan22_sage_attention_plan(
        object_info=object_info,
        enable_sage_attention=req.enable_sage_attention,
        sage_attention_mode=req.sage_attention_mode,
        sage_attention_target="high",
        high_model_link=active_model_link,
        low_model_link=active_model_link,
    )
    sage_payload = sage_plan.payload()
    warnings.extend(sage_payload.get("warnings", []) if isinstance(sage_payload.get("warnings"), list) else [])
    errors.extend(sage_payload.get("errors", []) if isinstance(sage_payload.get("errors"), list) else [])
    for branch in sage_plan.branches[:1]:
        prompt[branch.node_id] = {
            "class_type": sage_plan.class_type,
            "inputs": build_sage_attention_node_inputs(
                object_info,
                sage_plan.class_type,
                model_link=branch.source_model_link,
                mode=sage_plan.mode,
                model_field=sage_plan.model_field,
                mode_field=sage_plan.mode_field,
            ),
            "_meta": {"title": "Rapid AIO Sage Attention"},
        }
        active_model_link = branch.output_model_link

    teacache_plan = build_wan22_teacache_plan(
        object_info=object_info,
        enable_teacache=req.enable_teacache,
        teacache_profile=req.teacache_profile,
        teacache_target="high",
        high_model_link=active_model_link,
        low_model_link=active_model_link,
    )
    teacache_payload = teacache_plan.payload()
    warnings.extend(teacache_payload.get("warnings", []) if isinstance(teacache_payload.get("warnings"), list) else [])
    errors.extend(teacache_payload.get("errors", []) if isinstance(teacache_payload.get("errors"), list) else [])
    for branch in teacache_plan.branches[:1]:
        prompt[branch.node_id] = {
            "class_type": teacache_plan.class_type,
            "inputs": build_teacache_node_inputs(
                object_info,
                teacache_plan.class_type,
                model_link=branch.source_model_link,
                profile=teacache_plan.profile,
                model_field=teacache_plan.model_field,
            ),
            "_meta": {"title": "Rapid AIO TeaCache"},
        }
        active_model_link = branch.output_model_link

    low_vram_plan = build_wan22_low_vram_plan(
        object_info=object_info,
        enable_cpu_offload=req.enable_cpu_offload,
        enable_vae_offload=req.enable_vae_offload,
        enable_block_swap=req.enable_block_swap,
        block_swap_target="high",
        block_swap_blocks=req.block_swap_blocks,
        high_model_link=active_model_link,
        low_model_link=active_model_link,
    )
    low_vram_payload = low_vram_plan.payload()
    warnings.extend(low_vram_payload.get("warnings", []) if isinstance(low_vram_payload.get("warnings"), list) else [])
    errors.extend(low_vram_payload.get("errors", []) if isinstance(low_vram_payload.get("errors"), list) else [])
    for branch in low_vram_plan.branches[:1]:
        prompt[branch.node_id] = {
            "class_type": low_vram_plan.class_type,
            "inputs": build_wan_block_swap_node_inputs(
                object_info,
                low_vram_plan.class_type,
                model_link=branch.source_model_link,
                model_field=low_vram_plan.model_field,
                blocks_to_swap=low_vram_plan.blocks_to_swap,
                cpu_offload=low_vram_plan.cpu_offload_enabled or low_vram_plan.block_swap_enabled,
            ),
            "_meta": {"title": "Rapid AIO Low-VRAM Block Swap"},
        }
        active_model_link = branch.output_model_link

    return active_model_link, _sanitize_single_model_adapter_payloads({
        "sage_attention_adapter": sage_payload,
        "teacache_adapter": teacache_payload,
        "low_vram_adapter": low_vram_payload,
    }), list(dict.fromkeys(str(item) for item in warnings if item)), list(dict.fromkeys(str(item) for item in errors if item))


def _sanitize_single_model_adapter_payloads(payloads: dict[str, Any]) -> dict[str, Any]:
    """Remove WAN high/low branch language from Rapid AIO adapter diagnostics.

    The adapter builders are shared with the dual WAN route, so they use high/low
    branch names internally to produce one patch node. Rapid AIO is a single-model
    route; exported route diagnostics must not imply a hidden high/low stem.
    """
    cleaned = deepcopy(payloads)
    for payload in cleaned.values():
        if not isinstance(payload, dict):
            continue
        if payload.get("target") in {"high", "low", "both"}:
            payload["target"] = "single_model"
        for key in ("block_swap_target", "teacache_target", "sage_attention_target"):
            if key in payload:
                payload[key] = "single_model"
        for branch in payload.get("branches", []) if isinstance(payload.get("branches"), list) else []:
            if isinstance(branch, dict):
                branch["role"] = "rapid_aio"
        for mutation in payload.get("graph_mutations", []) if isinstance(payload.get("graph_mutations"), list) else []:
            if isinstance(mutation, dict):
                if mutation.get("role") in {"high_noise", "low_noise", "high", "low"}:
                    mutation["role"] = "rapid_aio"
        if "rules" in payload:
            payload["rules"] = [
                "Rapid AIO applies this adapter once to the single model chain before ModelSamplingSD3.",
                "Dual-noise branch targets are not part of the Rapid AIO production graph.",
            ]
    return cleaned


def _rapid_aio_frame_requirements(req: WanRapidAioGgufCompileRequest) -> dict[str, Any]:
    mode = _normalize_rapid_aio_frame_mode(req.rapid_aio_frame_mode, req.generation_type)
    return {
        "mode": mode,
        "requires_start_image": mode in {"start_frame", "start_end_frame"},
        "requires_end_image": mode in {"start_end_frame", "end_frame"},
        "uses_vace_frame_path": mode in {"start_end_frame", "end_frame"},
    }


def _first_present_class(object_info: dict[str, Any], candidates: tuple[str, ...]) -> str | None:
    for class_type in candidates:
        if isinstance(object_info.get(class_type), dict):
            return class_type
    return None


def _rapid_aio_vace_capability(object_info: dict[str, Any], frame_mode: str) -> dict[str, Any]:
    vace_to_video = _first_present_class(object_info, VACE_TO_VIDEO_CANDIDATES)
    first_last = _first_present_class(object_info, VACE_FIRST_LAST_CANDIDATES)
    uses_native_vace = frame_mode in {"text_only", "start_frame", "start_end_frame", "end_frame"}
    requires_first_last = frame_mode in {"start_end_frame", "end_frame"}
    missing: list[str] = []
    if uses_native_vace and not vace_to_video:
        missing.append("WanVaceToVideo")
    if requires_first_last and not first_last:
        missing.append("VACEFirstToLastFrame")
    queue_ready = uses_native_vace and not missing
    return {
        "required": uses_native_vace,
        "frame_mode": frame_mode,
        "vace_to_video_class": vace_to_video,
        "first_last_class": first_last,
        "candidate_vace_to_video_classes": list(VACE_TO_VIDEO_CANDIDATES),
        "candidate_first_last_classes": list(VACE_FIRST_LAST_CANDIDATES),
        "missing": missing,
        "mapped": queue_ready,
        "native_vace_graph": bool(vace_to_video),
        "first_last_native_helper_required": requires_first_last,
        "queue_ready": queue_ready,
        "notes": [
            "Phase 10aa maps Text2Video and Start-frame Img2Video through native WanVaceToVideo instead of the plain WanImageToVideo path.",
            "Start+End and End-frame-only modes require a local first/last helper node so the end frame is treated as an endpoint, not just a generic reference image.",
        ],
    }


def _rapid_aio_vace_strength(frame_mode: str) -> float:
    # The Rapid AIO MEGA model card says T2V bypasses frames and sets WanVaceToVideo strength to 0.
    # Image-conditioned native VACE modes keep strength enabled.
    return 0.0 if frame_mode == "text_only" else 1.0


def _snap_dimension_down(value: int, multiple: int = VACE_DIMENSION_MULTIPLE) -> int:
    parsed = max(64, int(value or 64))
    snapped = (parsed // multiple) * multiple
    return max(multiple, snapped)


def _rapid_aio_vace_dimension_guard(req: WanRapidAioGgufCompileRequest) -> dict[str, Any]:
    frame_mode = _normalize_rapid_aio_frame_mode(req.rapid_aio_frame_mode, req.generation_type)
    # Phase 10aa maps all Rapid AIO MEGA modes through WanVaceToVideo. That node reshapes
    # generated masks by the WAN VAE stride; non-divisible dimensions can crash inside Comfy
    # with invalid mask.view(...) shapes. Neo snaps to a conservative multiple of 16, which
    # covers the observed stride-8 requirement while avoiding surprise VRAM increases.
    required = frame_mode in RAPID_AIO_FRAME_MODES
    original = {"width": int(req.width), "height": int(req.height)}
    valid = (req.width % VACE_DIMENSION_MULTIPLE == 0) and (req.height % VACE_DIMENSION_MULTIPLE == 0)
    resolved = dict(original)
    auto_resolved = False
    errors: list[str] = []
    warnings: list[str] = []
    message = ""

    if required and not valid:
        if req.auto_resolve_vace_dimensions:
            resolved = {
                "width": _snap_dimension_down(req.width),
                "height": _snap_dimension_down(req.height),
            }
            auto_resolved = resolved != original
            message = (
                f"Neo auto-resolved Rapid AIO VACE size from {original['width']}x{original['height']} "
                f"to {resolved['width']}x{resolved['height']} because WanVaceToVideo reshapes masks by "
                f"VAE stride {VACE_VAE_STRIDE}; Neo uses multiples of {VACE_DIMENSION_MULTIPLE} to avoid Comfy mask shape errors."
            )
            warnings.append(message)
        else:
            message = (
                f"Rapid AIO VACE dimension guard blocked {original['width']}x{original['height']}. "
                f"Enable Auto Resolve VACE Size or use dimensions divisible by {VACE_DIMENSION_MULTIPLE} "
                f"so WanVaceToVideo can reshape stride-{VACE_VAE_STRIDE} masks safely."
            )
            errors.append(message)

    return {
        "ok": not errors,
        "required": required,
        "auto_resolve_enabled": bool(req.auto_resolve_vace_dimensions),
        "auto_resolved": auto_resolved,
        "multiple": VACE_DIMENSION_MULTIPLE,
        "vae_stride": VACE_VAE_STRIDE,
        "original": original,
        "resolved": resolved,
        "message": message,
        "warnings": warnings,
        "errors": errors,
    }


def _apply_rapid_aio_vace_dimension_guard(req: WanRapidAioGgufCompileRequest) -> tuple[WanRapidAioGgufCompileRequest, dict[str, Any]]:
    guard = _rapid_aio_vace_dimension_guard(req)
    resolved = guard.get("resolved") if isinstance(guard.get("resolved"), dict) else {}
    if guard.get("auto_resolved"):
        req = replace(req, width=int(resolved.get("width", req.width)), height=int(resolved.get("height", req.height)))
    return req, guard


def _first_input_name(entry: dict[str, Any] | None, candidates: tuple[str, ...]) -> str | None:
    names = _input_names(entry)
    folded = {name.casefold(): name for name in names}
    for candidate in candidates:
        if candidate.casefold() in folded:
            return folded[candidate.casefold()]
    for candidate in candidates:
        needle = candidate.casefold()
        for name in names:
            if needle in name.casefold():
                return name
    return None


def _native_vace_inputs(
    req: WanRapidAioGgufCompileRequest,
    object_info: dict[str, Any],
    vace_class: str,
    frame_mode: str,
    positive_link: list[Any],
    negative_link: list[Any],
    vae_link: list[Any],
) -> dict[str, Any]:
    entry = object_info.get(vace_class) if isinstance(object_info, dict) else None
    inputs = _output_inputs_with_defaults(entry)
    inputs.update({
        "width": req.width,
        "height": req.height,
        "length": req.frames,
        "batch_size": 1,
        "positive": positive_link,
        "negative": negative_link,
        "vae": vae_link,
        "strength": _rapid_aio_vace_strength(frame_mode),
    })
    return inputs


def _wire_first_last_helper(
    prompt: dict[str, Any],
    req: WanRapidAioGgufCompileRequest,
    object_info: dict[str, Any],
    first_last_class: str,
    frame_mode: str,
) -> tuple[dict[str, Any], list[str]]:
    """Try to map a local first/last helper without guessing endpoint semantics.

    If the helper class is present but does not expose recognizable start/end image
    fields, queueing must stay blocked instead of silently treating the end frame as
    a generic reference image.
    """
    helper_entry = object_info.get(first_last_class) if isinstance(object_info, dict) else None
    helper_inputs = _output_inputs_with_defaults(helper_entry)
    errors: list[str] = []

    start_field = _first_input_name(helper_entry, ("start_image", "first_image", "start_frame", "first_frame", "image_start", "image1"))
    end_field = _first_input_name(helper_entry, ("end_image", "last_image", "end_frame", "last_frame", "image_end", "image2"))
    if frame_mode == "start_end_frame" and not start_field:
        errors.append(f"{first_last_class} is present but Neo could not identify a start/first image input.")
    if not end_field:
        errors.append(f"{first_last_class} is present but Neo could not identify an end/last image input.")
    if errors:
        return {}, errors

    if frame_mode == "start_end_frame":
        helper_inputs[start_field] = ["97", 0]
    if end_field:
        helper_inputs[end_field] = ["98", 0]
    for field, value in (("width", req.width), ("height", req.height), ("length", req.frames), ("batch_size", max(1, req.frames - 2))):
        if field in _input_groups(helper_entry):
            helper_inputs[field] = value
    prompt["129:170"] = {
        "class_type": first_last_class,
        "inputs": helper_inputs,
        "_meta": {"title": "Rapid AIO MEGA First/Last Frame Helper"},
    }
    return {"node_id": "129:170", "class_type": first_last_class}, []


def _build_mega_generation_prompt(
    req: WanRapidAioGgufCompileRequest,
    object_info: dict[str, Any],
    nodes: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    """Build the Rapid AIO MEGA native VACE route graph.

    Phase 10aa routes MEGA Text2Video and Start-frame Img2Video through
    WanVaceToVideo so the selected source image is used as the native VACE
    reference frame, not as a weak plain WanImageToVideo start_image. Start+End
    and End-frame-only are only queued when a recognizable first/last helper is
    present, because mapping an end frame as a generic reference image would be
    misleading.
    """
    errors: list[str] = []
    warnings: list[str] = []
    req, vace_dimension_guard = _apply_rapid_aio_vace_dimension_guard(req)
    requirements = _rapid_aio_frame_requirements(req)
    frame_mode = str(requirements["mode"])
    warnings.extend(vace_dimension_guard.get("warnings", []) if isinstance(vace_dimension_guard.get("warnings"), list) else [])
    errors.extend(vace_dimension_guard.get("errors", []) if isinstance(vace_dimension_guard.get("errors"), list) else [])

    if req.generation_type not in {"txt2vid", "text_to_video", "t2v", "img2vid", "image_to_video", "i2v"}:
        return {
            "ok": False,
            "prompt": {},
            "queue_payload": {"prompt": {}},
            "errors": [f"Rapid AIO MEGA does not support generation_type={req.generation_type!r} in this route split."],
            "warnings": [],
            "graph_mode": "unsupported_generation_type",
            "frame_mode": frame_mode,
        }

    if requirements["requires_start_image"] and not (req.source_image or req.source_image_name or req.first_image or req.first_image_name):
        errors.append("Rapid AIO start-frame Img2Vid requires a start/source image from the Video source panel.")
    if requirements["requires_end_image"] and not (req.last_image or req.last_image_name):
        errors.append("Rapid AIO MEGA end-frame mode requires an end/last frame image from the Video source panel.")

    model_loader = nodes.get("model_loader", {}) if isinstance(nodes.get("model_loader"), dict) else {}
    model_class = str(model_loader.get("class_type") or "")
    model_field = str(model_loader.get("input_name") or "")
    model_name = str(model_loader.get("selected") or "")
    if not (model_class and model_field and model_name):
        errors.append("Rapid AIO model loader is not mapped; select a detected GGUF model first.")

    vace_capability = _rapid_aio_vace_capability(object_info, frame_mode)
    vace_class = str(vace_capability.get("vace_to_video_class") or "")
    first_last_class = str(vace_capability.get("first_last_class") or "")
    if not vace_class:
        errors.append("Rapid AIO MEGA native VACE route requires WanVaceToVideo in local Comfy /object_info.")

    prompt = _load_i2v_template()
    # Strip optional dual-noise/LightX2V helper nodes so the graph is single-model and deterministic.
    for node_id in (
        "129:86", "129:96", "129:101", "129:102", "129:103", "129:116", "129:117", "129:118", "129:119", "129:120", "129:122",
        "129:124", "129:125", "129:126", "129:127", "129:128", "129:131", "129:161", "129:162", "129:163",
    ):
        prompt.pop(node_id, None)

    if frame_mode == "text_only":
        prompt.pop("97", None)

    model_entry = object_info.get(model_class) if model_class else None
    if model_class and model_field and model_name:
        prompt["129:95"] = {
            "class_type": model_class,
            "inputs": _node_inputs_with_defaults(model_entry, model_field, model_name),
            "_meta": {"title": "Rapid AIO MEGA GGUF Model Loader"},
        }
    model_output_idx = _first_output_index(object_info, model_class, ("MODEL",), 0) if model_class else 0
    model_link = ["129:95", model_output_idx or 0]
    model_link, performance_adapters, performance_warnings, performance_errors = _apply_rapid_aio_performance_adapters(prompt, req, object_info, model_link)
    warnings.extend(performance_warnings)
    errors.extend(performance_errors)

    # Resolve CLIP/text conditioning link.
    text_loader = nodes.get("text_encoder_loader", {}) if isinstance(nodes.get("text_encoder_loader"), dict) else {}
    if text_loader.get("omitted"):
        clip_idx = _first_output_index(object_info, model_class, ("CLIP", "CONDITIONING"), None) if model_class else None
        if clip_idx is None:
            errors.append("This packed-component encoder variant has no CLIP/CONDITIONING output from the selected Rapid AIO loader. External text encoder is required.")
            clip_link = ["129:95", 1]
        else:
            clip_link = ["129:95", clip_idx]
            warnings.append("Using CLIP/CONDITIONING output from Rapid AIO loader because external text encoder is omitted internally.")
        prompt.pop("129:84", None)
    else:
        text_class = str(text_loader.get("class_type") or "")
        text_field = str(text_loader.get("input_name") or "")
        text_name = str(text_loader.get("selected") or "")
        if not (text_class and text_field and text_name):
            errors.append("External text encoder is enabled but not mapped.")
        else:
            text_inputs = _node_inputs_with_defaults(object_info.get(text_class), text_field, text_name)
            if "type" in _input_groups(object_info.get(text_class)):
                text_inputs["type"] = "wan"
            if "device" in _input_groups(object_info.get(text_class)) and "device" not in text_inputs:
                text_inputs["device"] = "default"
            prompt["129:84"] = {"class_type": text_class, "inputs": text_inputs, "_meta": {"title": "Rapid AIO External Text Encoder"}}
        clip_link = ["129:84", _first_output_index(object_info, str(text_loader.get("class_type") or ""), ("CLIP",), 0) or 0]

    # Resolve VAE link.
    vae_loader = nodes.get("vae_loader", {}) if isinstance(nodes.get("vae_loader"), dict) else {}
    if vae_loader.get("omitted"):
        vae_idx = _first_output_index(object_info, model_class, ("VAE",), None) if model_class else None
        if vae_idx is None:
            errors.append("This packed-component VAE variant has no VAE output from the selected Rapid AIO loader. External VAE is required.")
            vae_link = ["129:95", 2]
        else:
            vae_link = ["129:95", vae_idx]
            warnings.append("Using VAE output from Rapid AIO loader because external VAE is omitted internally.")
        prompt.pop("129:90", None)
    else:
        vae_class = str(vae_loader.get("class_type") or "")
        vae_field = str(vae_loader.get("input_name") or "")
        vae_name = str(vae_loader.get("selected") or "")
        if not (vae_class and vae_field and vae_name):
            errors.append("External VAE is enabled but not mapped.")
        else:
            prompt["129:90"] = {
                "class_type": vae_class,
                "inputs": _node_inputs_with_defaults(object_info.get(vae_class), vae_field, vae_name),
                "_meta": {"title": "Rapid AIO External VAE"},
            }
        vae_link = ["129:90", _first_output_index(object_info, str(vae_loader.get("class_type") or ""), ("VAE",), 0) or 0]

    if frame_mode in {"start_frame", "start_end_frame"}:
        prompt["97"]["inputs"]["image"] = _comfy_image_name(req.source_image or req.first_image, req.source_image_name or req.first_image_name)
    elif frame_mode in {"text_only", "end_frame"}:
        prompt.pop("97", None)

    if frame_mode in {"start_end_frame", "end_frame"}:
        prompt["98"] = {
            "class_type": "LoadImage",
            "inputs": {"image": _comfy_image_name(req.last_image, req.last_image_name)},
            "_meta": {"title": "Rapid AIO MEGA End Frame"},
        }

    prompt["129:93"]["inputs"].update({"text": req.prompt, "clip": clip_link})
    prompt["129:89"]["inputs"].update({"text": req.negative_prompt, "clip": clip_link})

    vace_inputs = _native_vace_inputs(
        req,
        object_info,
        vace_class or "WanVaceToVideo",
        frame_mode,
        positive_link=["129:93", 0],
        negative_link=["129:89", 0],
        vae_link=vae_link,
    )
    vace_entry = object_info.get(vace_class) if vace_class else None
    vace_input_names = _input_names(vace_entry)

    first_last_helper: dict[str, Any] | None = None
    if frame_mode == "start_frame":
        if "reference_image" in vace_input_names:
            vace_inputs["reference_image"] = ["97", 0]
        else:
            errors.append(f"{vace_class or 'WanVaceToVideo'} does not expose reference_image for Rapid AIO start-frame Img2Vid.")
    elif frame_mode in {"start_end_frame", "end_frame"}:
        if not first_last_class:
            errors.append("Rapid AIO MEGA end-frame modes require a local VACEFirstToLastFrame-compatible helper node; otherwise the end image would be treated as a generic reference instead of an endpoint.")
        else:
            first_last_helper, helper_errors = _wire_first_last_helper(prompt, req, object_info, first_last_class, frame_mode)
            errors.extend(helper_errors)
            if first_last_helper and "control_video" in vace_input_names:
                control_idx = _first_output_index(object_info, first_last_class, ("IMAGE", "VIDEO"), 0) or 0
                vace_inputs["control_video"] = [first_last_helper["node_id"], control_idx]
            if first_last_helper and "control_masks" in vace_input_names:
                mask_idx = _first_output_index(object_info, first_last_class, ("MASK",), None)
                if mask_idx is not None:
                    vace_inputs["control_masks"] = [first_last_helper["node_id"], mask_idx]
            if "reference_image" in vace_input_names:
                vace_inputs["reference_image"] = ["97", 0] if frame_mode == "start_end_frame" else ["98", 0]

    prompt["129:98"] = {
        "class_type": vace_class or "WanVaceToVideo",
        "inputs": vace_inputs,
        "_meta": {"title": "Rapid AIO MEGA Native VACE Conditioning"},
    }
    prompt["129:104"]["inputs"].update({"model": model_link, "shift": 5.0})
    prompt["129:85"]["inputs"].update({
        "add_noise": "enable",
        "noise_seed": req.seed if req.seed >= 0 else 42,
        "steps": req.steps,
        "cfg": req.guidance,
        "sampler_name": req.sampler,
        "scheduler": req.scheduler,
        "start_at_step": 0,
        "end_at_step": req.steps,
        "return_with_leftover_noise": "disable",
        "model": ["129:104", 0],
        "positive": ["129:98", 0],
        "negative": ["129:98", 1],
        "latent_image": ["129:98", 2],
    })
    low_vram_payload = performance_adapters.get("low_vram_adapter", {}) if isinstance(performance_adapters, dict) else {}
    vae_decode = low_vram_payload.get("vae_decode", {}) if isinstance(low_vram_payload.get("vae_decode"), dict) else {}
    if vae_decode.get("tiled") and vae_decode.get("class_type"):
        prompt["129:87"] = {
            "class_type": str(vae_decode.get("class_type")),
            "inputs": build_vae_decode_inputs(
                object_info,
                str(vae_decode.get("class_type")),
                samples_link=["129:85", 0],
                vae_link=vae_link,
                tile_size=req.tile_size,
                temporal_tile_size=req.temporal_tile_size,
            ),
            "_meta": {"title": "Rapid AIO VAE Decode · tiled / low-VRAM"},
        }
    else:
        prompt["129:87"]["inputs"].update({"samples": ["129:85", 0], "vae": vae_link})
    single_model_assertion = _rapid_aio_single_model_assertion(prompt)
    errors.extend(single_model_assertion.get("errors", []))
    output_resolution = _apply_verified_video_output_node(prompt, req, object_info)
    if not output_resolution.get("ok"):
        errors.append("Rapid AIO graph blocked before Comfy: no valid local video output node found. Install/enable VideoHelperSuite or select a supported video output node.")
    elif not _prompt_has_verified_output(prompt, object_info):
        errors.append("Rapid AIO graph blocked before Comfy: selected video output node class is not present in local Comfy /object_info.")
    native_graph_modes = {
        "text_only": "rapid_aio_mega_vace_t2v_native",
        "start_frame": "rapid_aio_mega_vace_i2v_start_frame_native",
        "start_end_frame": "rapid_aio_mega_vace_start_end_frame_native",
        "end_frame": "rapid_aio_mega_vace_end_frame_native",
    }
    graph_mode = native_graph_modes.get(frame_mode, "rapid_aio_mega_vace_native")
    if errors and frame_mode in {"start_end_frame", "end_frame"}:
        graph_mode = f"{graph_mode}_guarded"
    return {
        "ok": not errors,
        "prompt": prompt if not errors else {},
        "queue_payload": {"prompt": prompt, "client_id": f"neo-video-rapid-aio-{uuid4().hex[:10]}"} if not errors else {"prompt": {}},
        "errors": errors,
        "warnings": warnings,
        "graph_mode": graph_mode,
        "frame_mode": frame_mode,
        "conditioning_node": vace_class or "WanVaceToVideo",
        "mega_workflow_native": True,
        "source_image_role": {
            "text_only": "none",
            "start_frame": "start_frame_reference_image",
            "start_end_frame": "start_and_end_frame_vace_helper",
            "end_frame": "end_frame_vace_helper",
        }.get(frame_mode, "unknown"),
        "first_last_helper": first_last_helper if 'first_last_helper' in locals() else None,
        "vace_capability": vace_capability,
        "vace_dimension_guard": vace_dimension_guard,
        "resolved_parameters": {"width": req.width, "height": req.height, "frames": req.frames, "fps": req.fps},
        "model_link": model_link,
        "clip_link": clip_link if 'clip_link' in locals() else None,
        "vae_link": vae_link if 'vae_link' in locals() else None,
        "output_resolution": output_resolution if 'output_resolution' in locals() else _resolve_video_output_node(object_info),
        "performance_adapters": performance_adapters if 'performance_adapters' in locals() else {},
        "single_model_assertion": single_model_assertion if 'single_model_assertion' in locals() else {},
        "object_info_summary": rapid_aio_object_info_summary(object_info, prompt),
    }


def _build_i2v_generation_prompt(
    req: WanRapidAioGgufCompileRequest,
    object_info: dict[str, Any],
    nodes: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    # Backwards-compatible symbol for older tests/tools. The implementation is now MEGA mode-aware.
    return _build_mega_generation_prompt(req, object_info, nodes, variant)


def _build_prompt_api_variant(
    req: WanRapidAioGgufCompileRequest,
    discovery: dict[str, Any],
    object_info: dict[str, Any],
    variant: dict[str, Any],
) -> dict[str, Any]:
    model_name = req.rapid_aio_model or _first_catalog(discovery, "rapid_aio_gguf_models")
    text_encoder_name = req.rapid_aio_text_encoder or _first_catalog(discovery, "rapid_aio_text_encoders")
    vae_name = req.rapid_aio_vae or _first_catalog(discovery, "rapid_aio_vaes")
    use_text_encoder = bool(variant.get("external_text_encoder")) and req.rapid_aio_text_encoder != RAPID_AIO_NONE_TEST_ID
    use_vae = bool(variant.get("external_vae")) and req.rapid_aio_vae != RAPID_AIO_NONE_TEST_ID

    model_node = _pick_node(object_info, MODEL_LOADER_CANDIDATES, model_name, KNOWN_MODEL_INPUTS, lambda value: value.casefold().endswith(".gguf") or "gguf" in value.casefold())
    text_node = _pick_node(object_info, TEXT_ENCODER_CANDIDATES, text_encoder_name, KNOWN_TEXT_INPUTS, lambda value: any(marker in value.casefold() for marker in ("clip", "t5", "umt5", "encoder"))) if use_text_encoder else None
    vae_node = _pick_node(object_info, VAE_CANDIDATES, vae_name, KNOWN_VAE_INPUTS, lambda value: "vae" in value.casefold()) if use_vae else None

    prompt: dict[str, Any] = {}
    nodes: dict[str, Any] = {}
    node_id = 1
    errors: list[str] = []
    warnings: list[str] = []
    if not model_name:
        errors.append("No Rapid AIO GGUF model is selected or visible in ComfyUI catalog.")
    if model_name:
        prompt[str(node_id)] = {"class_type": model_node["class_type"], "inputs": {model_node["input_name"]: model_name}, "_meta": {"title": "Rapid AIO GGUF Model Loader"}}
        nodes["model_loader"] = {**model_node, "node_id": str(node_id), "selected": model_name}
        node_id += 1
    if use_text_encoder:
        if not text_encoder_name:
            errors.append("External text encoder is enabled for this variant, but no encoder is selected or visible.")
        else:
            prompt[str(node_id)] = {"class_type": text_node["class_type"], "inputs": {text_node["input_name"]: text_encoder_name}, "_meta": {"title": "Rapid AIO External Text Encoder"}}
            nodes["text_encoder_loader"] = {**text_node, "node_id": str(node_id), "selected": text_encoder_name}
            node_id += 1
    else:
        warnings.append("External text encoder loader omitted for internal packed-component variant.")
        nodes["text_encoder_loader"] = {"omitted": True, "selected": RAPID_AIO_NONE_TEST_ID, "reason": "internal packed-component variant"}
    if use_vae:
        if not vae_name:
            errors.append("External VAE is enabled for this variant, but no VAE is selected or visible.")
        else:
            prompt[str(node_id)] = {"class_type": vae_node["class_type"], "inputs": {vae_node["input_name"]: vae_name}, "_meta": {"title": "Rapid AIO External VAE"}}
            nodes["vae_loader"] = {**vae_node, "node_id": str(node_id), "selected": vae_name}
            node_id += 1
    else:
        warnings.append("External VAE loader omitted for internal packed-component variant.")
        nodes["vae_loader"] = {"omitted": True, "selected": RAPID_AIO_NONE_TEST_ID, "reason": "internal packed-component variant"}

    has_fallback = _has_conventional_fallback(nodes)
    if has_fallback:
        warnings.append("One or more node signatures were not present in object_info; workflow uses conventional fallback node/input names and must be verified in Comfy before queueing.")

    loader_probe_ready = (not errors) and bool(prompt)
    generation_graph = _build_mega_generation_prompt(req, object_info, nodes, variant)
    for warning in generation_graph.get("warnings", []) if isinstance(generation_graph.get("warnings"), list) else []:
        if warning not in warnings:
            warnings.append(str(warning))
    generation_errors = [str(item) for item in generation_graph.get("errors", []) if item] if isinstance(generation_graph.get("errors"), list) else []
    promotion_ready = loader_probe_ready and (not has_fallback) and bool(generation_graph.get("ok")) and _prompt_has_verified_output(generation_graph.get("prompt", {}), object_info)

    return {
        "id": variant["id"],
        "label": variant["label"],
        "compile_ready": not errors,
        "loader_probe_ready": loader_probe_ready,
        "queue_allowed": promotion_ready,
        "promotion_ready": promotion_ready,
                "external_text_encoder": use_text_encoder,
        "external_vae": use_vae,
        "selected_models": {
            "rapid_aio_model": model_name,
            "rapid_aio_text_encoder": text_encoder_name if use_text_encoder else RAPID_AIO_NONE_TEST_ID,
            "rapid_aio_vae": vae_name if use_vae else RAPID_AIO_NONE_TEST_ID,
        },
        "nodes": nodes,
        "loader_probe_prompt_api_payload": prompt,
        "generation_graph": {
            "ok": bool(generation_graph.get("ok")),
            "graph_mode": generation_graph.get("graph_mode"),
            "frame_mode": generation_graph.get("frame_mode"),
            "conditioning_node": generation_graph.get("conditioning_node"),
            "mega_workflow_native": generation_graph.get("mega_workflow_native"),
            "source_image_role": generation_graph.get("source_image_role"),
            "first_last_helper": generation_graph.get("first_last_helper"),
            "vace_capability": generation_graph.get("vace_capability", {}),
            "vace_dimension_guard": generation_graph.get("vace_dimension_guard", {}),
            "resolved_parameters": generation_graph.get("resolved_parameters", {}),
            "errors": generation_errors,
            "warnings": generation_graph.get("warnings", []),
            "model_link": generation_graph.get("model_link"),
            "clip_link": generation_graph.get("clip_link"),
            "vae_link": generation_graph.get("vae_link"),
            "has_output_node": _prompt_has_output(generation_graph.get("prompt", {})),
            "verified_output_node": _prompt_has_verified_output(generation_graph.get("prompt", {}), object_info),
            "output_resolution": generation_graph.get("output_resolution"),
            "performance_adapters": generation_graph.get("performance_adapters", {}),
            "single_model_assertion": generation_graph.get("single_model_assertion", {}),
        },
        "prompt_api_payload": generation_graph.get("prompt", {}) if promotion_ready else prompt,
        "queue_prompt_payload": generation_graph.get("queue_payload", {"prompt": {}}) if promotion_ready else {"prompt": {}},
        "errors": [*errors, *generation_errors],
        "warnings": warnings,
        "parameters": {
            "width": int((generation_graph.get("resolved_parameters") or {}).get("width", req.width)) if isinstance(generation_graph.get("resolved_parameters"), dict) else req.width,
            "height": int((generation_graph.get("resolved_parameters") or {}).get("height", req.height)) if isinstance(generation_graph.get("resolved_parameters"), dict) else req.height,
            "frames": int((generation_graph.get("resolved_parameters") or {}).get("frames", req.frames)) if isinstance(generation_graph.get("resolved_parameters"), dict) else req.frames,
            "fps": float((generation_graph.get("resolved_parameters") or {}).get("fps", req.fps)) if isinstance(generation_graph.get("resolved_parameters"), dict) else req.fps,
            "steps": req.steps,
            "guidance": req.guidance,
            "seed": req.seed,
            "sampler": req.sampler,
            "scheduler": req.scheduler,
            "decode_mode": req.decode_mode,
            "tile_size": req.tile_size,
            "temporal_tile_size": req.temporal_tile_size,
            "rapid_aio_frame_mode": req.rapid_aio_frame_mode,
            "auto_resolve_vace_dimensions": req.auto_resolve_vace_dimensions,
        },
    }


def build_wan22_rapid_aio_gguf_production_route(req: WanRapidAioGgufCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info if isinstance(object_info, dict) else {}
    discovery = video_model_discovery_from_object_info(
        info,
        family=req.family,
        loader=req.loader,
        generation_type=req.generation_type,
        fallback_models={},
        rapid_aio_model=req.rapid_aio_model,
        clip_name=None if req.rapid_aio_text_encoder == RAPID_AIO_NONE_TEST_ID else req.rapid_aio_text_encoder,
        vae_name=None if req.rapid_aio_vae == RAPID_AIO_NONE_TEST_ID else req.rapid_aio_vae,
    )
    variants = [_build_prompt_api_variant(req, discovery, info, variant) for variant in _requested_variants(req)]
    ready_variants = [variant["id"] for variant in variants if variant.get("compile_ready")]
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "surface": "video",
        "compiler_id": "wan22_rapid_aio_gguf_production",
        "compiler_mode": "production_route",
        "queue_allowed": any(bool(variant.get("promotion_ready")) for variant in variants),
        "hardcoded_model_names": False,
        "user_selection_wins": True,
                "model_discovery": discovery,
        "variants": variants,
        "ready_variant_ids": ready_variants,
        "selected_variant_id": PRODUCTION_VARIANT_ID,
        "matrix_ids": [variant["id"] for variant in MATRIX_VARIANTS],
        "promotable_variant_ids": [variant["id"] for variant in variants if variant.get("promotion_ready")],
        "production_route_contract": {
            "uses_single_production_variant": True,
            "default_variant_id": PRODUCTION_VARIANT_ID,
            "blocks_conventional_fallback_signatures": True,
            "uses_mega_mode_route_split": True,
            "supports_modes": ["txt2vid", "img2vid"],
            "supports_rapid_aio_frame_modes": ["text_only", "start_frame", "start_end_frame", "end_frame"],
            "uses_native_vace_conditioning": True,
            "native_vace_modes": ["text_only", "start_frame"],
            "first_last_modes_require_helper": ["start_end_frame", "end_frame"],
            "vace_frame_modes_guarded_by_object_info": True,
            "auto_resolves_invalid_vace_dimensions": True,
            "vace_dimension_multiple": VACE_DIMENSION_MULTIPLE,
            "intent": "Send Rapid AIO MEGA Text2Video and start-frame Img2Video through native WanVaceToVideo; auto-snap invalid VACE dimensions; queue first/last frame modes only when an endpoint helper is present and mappable."
        },
    }


def build_wan22_rapid_aio_gguf_test_matrix(req: WanRapidAioGgufCompileRequest, object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    # Backwards-compatible import alias for older phase tests/tools. The visible UI and runtime now use the production route contract.
    return build_wan22_rapid_aio_gguf_production_route(req, object_info)


def _load_object_info(req: WanRapidAioGgufCompileRequest, object_info_override: dict[str, Any] | None, timeout: float = 2.5) -> tuple[dict[str, Any], list[str], dict[str, Any]]:
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    if object_info_override is not None:
        return object_info_override, [], profile
    try:
        return _get_json(base_url, "/object_info", timeout), [], profile
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {}, [f"Compiled with fallback bindings because ComfyUI /object_info was unavailable: {exc}"], profile


def _post_json(base_url: str, path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(urljoin(base_url.rstrip("/") + "/", path.lstrip("/")), data=body, headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _comfy_queue_error(exc: BaseException) -> dict[str, Any]:
    body = ""
    status_code = None
    if isinstance(exc, HTTPError):
        status_code = getattr(exc, "code", None)
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except Exception:
            body = ""
    text = str(exc)
    preview = body[:4000] if body else ""
    lowered = f"{text} {preview}".casefold()
    hint = ""
    if "not in list" in lowered or "value not in list" in lowered:
        hint = "Refresh model discovery and reselect the Rapid AIO model, encoder, or VAE from the live Comfy dropdown."
    elif "missing" in lowered and "input" in lowered:
        hint = "The local Rapid AIO loader signature needs another required input. Check the prompt sidecar and add the input mapping before real generation."
    elif "return type mismatch" in lowered:
        hint = "The selected Rapid AIO production graph links a component with an incompatible return type; reselect model, text encoder, or VAE from the live catalog."
    elif "prompt_no_outputs" in lowered or "prompt has no outputs" in lowered:
        hint = "The queued Rapid AIO graph had no valid video output node. Recompile and inspect the generated Rapid AIO single-model SaveVideo/CreateVideo path."
    elif "prompt_outputs_failed_validation" in lowered or "invalid prompt" in lowered:
        hint = "Comfy rejected the Rapid AIO generation graph. Inspect generation_graph errors and try Auto encoder + Auto VAE first."
    return {"type": exc.__class__.__name__, "message": text, "status_code": status_code, "body_preview": preview, "hint": hint}


def _selected_variant_for_queue(matrix: dict[str, Any], req: WanRapidAioGgufCompileRequest) -> dict[str, Any] | None:
    # Rapid AIO is now a single production route. Legacy matrix/test selector
    # payload keys are ignored so stale UI/client state cannot route generation.
    for variant in matrix.get("variants", []) if isinstance(matrix.get("variants"), list) else []:
        if isinstance(variant, dict) and variant.get("id") == PRODUCTION_VARIANT_ID:
            return variant
    return None


def video_wan22_rapid_aio_gguf_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = _sanitize_rapid_aio_payload(dict(payload or {}))
    req = WanRapidAioGgufCompileRequest.from_payload(payload)
    vace_dimension_guard = _rapid_aio_vace_dimension_guard(req)
    route = find_video_route(req.family, req.loader, req.generation_type, include_planned=True)
    object_info, object_info_warnings, profile = _load_object_info(req, object_info_override)
    matrix = build_wan22_rapid_aio_gguf_production_route(req, object_info)
    request_payload = req.payload()
    if vace_dimension_guard.get("auto_resolved") and isinstance(vace_dimension_guard.get("resolved"), dict):
        request_payload.update({
            "width": int(vace_dimension_guard["resolved"].get("width", req.width)),
            "height": int(vace_dimension_guard["resolved"].get("height", req.height)),
        })
    object_info_summary = rapid_aio_object_info_summary(object_info)
    output_paths = get_video_output_paths("metadata", create=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    sidecar = output_paths.output_dir / f"{sanitize_path_part(req.filename_prefix, 'wan22_rapid_aio')}_{stamp}_compile.json"
    payload_out = {
        **matrix,
        "ok": True,
        "queued": False,
        "dry_run": True,
        "route_id": route.route_id if route else "",
        "route": route.payload() if route else None,
        "request": {**request_payload, "vace_dimension_guard": vace_dimension_guard},
        "vace_dimension_guard": vace_dimension_guard,
        "warnings": [
            *object_info_warnings,
            *(vace_dimension_guard.get("warnings", []) if isinstance(vace_dimension_guard.get("warnings"), list) else []),
            "WAN 2.2 Rapid AIO GGUF uses native WanVaceToVideo conditioning for Txt2Vid and start-frame Img2Vid.",
            "Auto selects from live Comfy/Admin catalogs. The production path uses external text encoder and VAE unless a future packed-loader route proves CLIP/VAE outputs locally.",
            "Video LoRA/LightX2V branch controls remain dual-noise-only; Sage/TeaCache/low-VRAM speed controls are available for Rapid AIO as a single-model chain.",
        ],
        "backend": {"profile": profile, "base_url": profile["connection"]["base_url"]},
        "object_info_summary": object_info_summary,
        "neo_output": {"category": "metadata", "metadata_sidecar": str(sidecar)},
    }
    latest_compile = _write_latest_rapid_aio_log("latest_rapid_aio_compile_route.json", payload_out)
    latest_object_info = _write_latest_rapid_aio_log("latest_rapid_aio_object_info_summary.json", object_info_summary)
    payload_out["diagnostics"] = {
        "latest_compile_route": latest_compile,
        "latest_object_info_summary": latest_object_info,
    }
    sidecar.write_text(json.dumps(payload_out, indent=2), encoding="utf-8")
    if latest_compile:
        _write_latest_rapid_aio_log("latest_rapid_aio_compile_route.json", payload_out)
    return payload_out


def video_wan22_rapid_aio_gguf_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    effective_payload = _sanitize_rapid_aio_payload(dict(payload or {}))
    if "dry_run" not in effective_payload:
        effective_payload["dry_run"] = False
    req = WanRapidAioGgufCompileRequest.from_payload(effective_payload)

    # Rapid AIO MEGA image modes need Comfy input handoff for the selected frame slots.
    # Text-only mode intentionally clears source fields before compile/queue.
    source_handoff: dict[str, Any] | None = None
    if not req.dry_run and req.generation_type in {"img2vid", "image_to_video", "i2v"}:
        requirements = _rapid_aio_frame_requirements(req)
        profile = video_backend_profile_payload(req.profile_id)
        base_url_for_handoff = profile["connection"]["base_url"]

        def _handoff_source_slot(slot_payload: dict[str, Any], label: str) -> dict[str, Any]:
            local_source_exists = resolve_video_source_image_path(slot_payload) is not None
            if object_info_override is not None and not local_source_exists and not (slot_payload.get("source_image_comfy_name") or slot_payload.get("comfy_source_image_name")):
                comfy_name = slot_payload.get("source_image_name") or Path(str(slot_payload.get("source_image") or f"neo_video_{label}.png")).name
                bypass_payload = {**slot_payload, "source_image_name": comfy_name, "source_image_comfy_name": comfy_name, "comfy_source_image_name": comfy_name}
                return {
                    "ok": True,
                    "uploaded": False,
                    "verified": False,
                    "bypassed_for_object_info_override": True,
                    "slot": label,
                    "comfy_image_name": comfy_name,
                    "payload": bypass_payload,
                    "source_path": "",
                }
            result = prepare_video_source_image_handoff(slot_payload, base_url_for_handoff, timeout=max(timeout, 10.0))
            if isinstance(result, dict):
                result["slot"] = label
            return result

        handoffs: dict[str, Any] = {}
        if requirements["requires_start_image"]:
            start_payload = {
                **effective_payload,
                "source_image": req.source_image or req.first_image,
                "source_image_name": req.source_image_name or req.first_image_name,
                "source_image_comfy_name": req.source_image_name or req.first_image_name,
            }
            source_handoff = _handoff_source_slot(start_payload, "start")
            handoffs["start"] = source_handoff
            if not source_handoff.get("ok"):
                return {
                    "schema_version": SCHEMA_VERSION,
                    "surface": "video",
                    "phase": PHASE,
                    "ok": False,
                    "queued": False,
                    "dry_run": False,
                    "error": f"Rapid AIO start frame handoff to Comfy failed: {source_handoff.get('error') or 'unknown error'}",
                    "source_handoff": source_handoff,
                    "request": req.payload(),
                }
            effective_payload = source_handoff.get("payload") if isinstance(source_handoff.get("payload"), dict) else effective_payload

        if requirements["requires_end_image"]:
            end_payload = {
                **effective_payload,
                "source_image": req.last_image,
                "source_image_name": req.last_image_name,
                "source_image_comfy_name": req.last_image_name,
            }
            end_handoff = _handoff_source_slot(end_payload, "end")
            handoffs["end"] = end_handoff
            if not end_handoff.get("ok"):
                return {
                    "schema_version": SCHEMA_VERSION,
                    "surface": "video",
                    "phase": PHASE,
                    "ok": False,
                    "queued": False,
                    "dry_run": False,
                    "error": f"Rapid AIO end frame handoff to Comfy failed: {end_handoff.get('error') or 'unknown error'}",
                    "source_handoff": end_handoff,
                    "request": req.payload(),
                }
            comfy_name = end_handoff.get("comfy_image_name") or end_handoff.get("payload", {}).get("source_image_comfy_name")
            if comfy_name:
                effective_payload = {**effective_payload, "last_image_name": comfy_name, "last_image_comfy_name": comfy_name}

        if handoffs:
            source_handoff = {"ok": True, "mode": requirements["mode"], "slots": handoffs}
        req = WanRapidAioGgufCompileRequest.from_payload(effective_payload)

    compiled = video_wan22_rapid_aio_gguf_compile_payload(effective_payload, object_info_override=object_info_override)
    if isinstance(compiled.get("request"), dict):
        req = WanRapidAioGgufCompileRequest.from_payload(compiled["request"])
    if source_handoff is not None:
        compiled["source_handoff"] = source_handoff
    if req.dry_run:
        return compiled
    selected = _selected_variant_for_queue(compiled, req)
    if selected is None:
        return {**compiled, "ok": False, "queued": False, "dry_run": False, "error": "Rapid AIO GGUF production route could not resolve the selected compile variant."}
    if not selected.get("compile_ready"):
        return {**compiled, "ok": False, "queued": False, "dry_run": False, "promoted_variant": selected, "error": "Selected Rapid AIO GGUF variant is not compile-ready; fix model/encoder/VAE selections first."}
    if not selected.get("promotion_ready"):
        generation_graph = selected.get("generation_graph") if isinstance(selected.get("generation_graph"), dict) else {}
        output_resolution = generation_graph.get("output_resolution") if isinstance(generation_graph.get("output_resolution"), dict) else {}
        if output_resolution.get("ok") is not True:
            return {**compiled, "ok": False, "queued": False, "dry_run": False, "promoted_variant": selected, "error": "Rapid AIO graph blocked before Comfy: no valid local video output node found. Install/enable VideoHelperSuite or select a supported video output node."}
        return {**compiled, "ok": False, "queued": False, "dry_run": False, "promoted_variant": selected, "error": "Selected Rapid AIO GGUF variant is not generation-ready. Check generation_graph errors and external text encoder/VAE selections."}
    backend = compiled.get("backend", {}) if isinstance(compiled.get("backend"), dict) else {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    queue_payload = selected.get("queue_prompt_payload") if isinstance(selected.get("queue_prompt_payload"), dict) else {"prompt": selected.get("prompt_api_payload", {})}
    latest_queue_payload = _write_latest_rapid_aio_log("latest_rapid_aio_queue_payload.json", {
        "schema_version": "neo.video.wan22_rapid_aio_gguf.queue_payload_trace.v25_9_19_phase10aa",
        "phase": PHASE,
        "variant_id": selected.get("id"),
        "queue_payload": queue_payload,
        "generation_graph": selected.get("generation_graph"),
    })
    prompt = queue_payload.get("prompt") if isinstance(queue_payload, dict) else None
    generation_graph = selected.get("generation_graph") if isinstance(selected.get("generation_graph"), dict) else {}
    single_model_assertion = generation_graph.get("single_model_assertion") if isinstance(generation_graph.get("single_model_assertion"), dict) else {}
    if (
        not isinstance(prompt, dict)
        or not prompt
        or not _prompt_has_output(prompt)
        or generation_graph.get("verified_output_node") is not True
        or single_model_assertion.get("ok") is False
    ):
        return {
            **compiled,
            "ok": False,
            "queued": False,
            "dry_run": False,
            "promoted_variant": selected,
            "production_route": {"enabled": True, "variant_id": selected.get("id"), "queue_payload": queue_payload, "latest_queue_payload": latest_queue_payload},
            "error": "Rapid AIO graph blocked before Comfy: no valid local video output node found. Install/enable VideoHelperSuite or select a supported video output node.",
        }
    try:
        queue_response = _post_json(base_url, "/prompt", queue_payload, timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        queue_error = _comfy_queue_error(exc)
        return {
            **compiled,
            "ok": False,
            "queued": False,
            "dry_run": False,
            "promoted_variant": selected,
            "production_route": {"enabled": True, "variant_id": selected.get("id"), "queue_payload": queue_payload, "latest_queue_payload": latest_queue_payload},
            "queue_error": queue_error,
            "error": f"Rapid AIO GGUF Comfy queue failed: {queue_error['message']}",
        }
    response_payload = {
        **compiled,
        "ok": True,
        "queued": True,
        "dry_run": False,
        "promoted_variant": selected,
        "production_route": {"enabled": True, "variant_id": selected.get("id"), "queue_payload": queue_payload, "latest_queue_payload": latest_queue_payload},
        "queue_response": queue_response,
        "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or "",
        "client_id": queue_payload.get("client_id") or "",
    }
    return _attach_video_output_record(response_payload, _production_request_payload(req))
