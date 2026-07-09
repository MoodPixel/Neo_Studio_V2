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
from neo_app.video.external_node_manager import evaluate_video_external_node_packs, frame_interpolation_admin_readiness
from neo_app.video.output_paths import ROOT_DIR, get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import load_video_output_record, register_video_generation_result, video_output_file_path

SCHEMA_VERSION: Final[str] = "neo.video.finish.interpolation.v12"
PHASE: Final[str] = "V12"
BACKEND_WIRING_PHASE: Final[str] = "V24.7"
METADATA_LINEAGE_PHASE: Final[str] = "V24.8"
ROUTE_ID: Final[str] = "finish.interpolate"

VIDEO_EXTENSIONS: Final[tuple[str, ...]] = (".webm", ".mp4", ".mov", ".mkv", ".gif")
NORMALIZATION_SCHEMA_VERSION: Final[str] = "neo.video.finish.frame_interpolation.backend_normalization.v24_7"
OUTPUT_METADATA_SCHEMA_VERSION: Final[str] = "neo.video.finish.frame_interpolation.output_metadata.v24_8"
MOTION_TIMING_SCHEMA_VERSION: Final[str] = "neo.video.finish.motion_timing_repair.v25_9_19_phase_10k"
PIPELINE_PRESET_SCHEMA_VERSION: Final[str] = "neo.video.finish.pipeline_presets.v25_9_19_phase_10k"
EXTENSION_ID: Final[str] = "video.finish_interpolation"
FINISH_OPERATION_ID: Final[str] = "frame_interpolation"
FPS_AUTO_POLICY: Final[str] = "derive_from_source_fps_times_multiplier"
FPS_MOTION_REPAIR_POLICY: Final[str] = "derive_from_source_fps_times_multiplier_and_motion_speed"
FPS_SLOW_MOTION_POLICY: Final[str] = "keep_source_fps_after_interpolation"
MOTION_TIMING_POLICIES: Final[tuple[str, ...]] = ("same_duration", "motion_speed_repair", "slow_motion")
FINISH_PIPELINE_PRESETS: Final[tuple[str, ...]] = ("custom", "fast_finish", "quality_finish", "smooth_only", "upscale_only")

VRAM_PROFILE_PRESETS: Final[dict[str, dict[str, Any]]] = {
    "low": {
        "label": "Low VRAM",
        "default_method": "rife",
        "allowed_methods": ("auto", "rife"),
        "default_multiplier": 2.0,
        "allowed_multipliers": (2.0,),
        "clear_cache_after_n_frames": 4,
        "frame_load_cap": 72,
        "skip_first_frames": 0,
        "select_every_nth": 1,
        "output_format": "webm",
    },
    "medium": {
        "label": "Medium VRAM",
        "default_method": "rife",
        "allowed_methods": ("auto", "rife", "film", "amt"),
        "default_multiplier": 2.0,
        "allowed_multipliers": (2.0, 4.0),
        "clear_cache_after_n_frames": 8,
        "frame_load_cap": 0,
        "skip_first_frames": 0,
        "select_every_nth": 1,
        "output_format": "webm",
    },
    "high": {
        "label": "High VRAM",
        "default_method": "rife",
        "allowed_methods": ("auto", "rife", "film", "amt"),
        "default_multiplier": 2.0,
        "allowed_multipliers": (2.0, 4.0),
        "clear_cache_after_n_frames": 16,
        "frame_load_cap": 0,
        "skip_first_frames": 0,
        "select_every_nth": 1,
        "output_format": "webm",
    },
    "custom": {
        "label": "Custom",
        "inherits_from": "medium",
        "default_method": "rife",
        "allowed_methods": ("auto", "rife", "film", "amt"),
        "default_multiplier": 2.0,
        "allowed_multipliers": (2.0, 4.0),
        "clear_cache_after_n_frames": 8,
        "frame_load_cap": 0,
        "skip_first_frames": 0,
        "select_every_nth": 1,
        "output_format": "webm",
    },
}


@dataclass(frozen=True)
class VideoInterpolationRequest:
    source_result_id: str | None = None
    source_file_id: str | None = None
    source_video_path: str | None = None
    method: str = "auto"
    fps_multiplier: float = 2.0
    output_fps: float | None = None
    output_format: str = "webm"
    output_fps_policy: str = FPS_AUTO_POLICY
    motion_timing_policy: str = "same_duration"
    motion_speed_multiplier: float = 1.0
    finish_pipeline_preset: str = "custom"
    vram_profile: str = "medium"
    clear_cache_after_n_frames: int | None = None
    frame_load_cap: int | None = None
    skip_first_frames: int = 0
    select_every_nth: int = 1
    source_fps: float | None = None
    pix_fmt: str = "yuv420p"
    crf: int = 20
    save_metadata: bool = True
    trim_to_audio: bool = False
    filename_prefix: str = "Neo_Video_Interpolated"
    profile_id: str | None = None
    dry_run: bool = True

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "VideoInterpolationRequest":
        data = payload or {}
        return cls(
            source_result_id=str(data.get("source_result_id", data.get("parent_result_id", "")) or "") or None,
            source_file_id=str(data.get("source_file_id", data.get("file_id", "")) or "") or None,
            source_video_path=str(data.get("source_video_path", data.get("video_path", "")) or "") or None,
            method=str(data.get("method", "auto") or "auto"),
            fps_multiplier=_float_value(data.get("fps_multiplier", data.get("multiplier")), 2.0),
            output_fps=_float_or_none(data.get("output_fps", data.get("fps"))),
            output_format=str(data.get("output_format", "webm") or "webm"),
            output_fps_policy=str(data.get("output_fps_policy", FPS_AUTO_POLICY) or FPS_AUTO_POLICY),
            motion_timing_policy=str(data.get("motion_timing_policy", "same_duration") or "same_duration"),
            motion_speed_multiplier=_float_value(data.get("motion_speed_multiplier", data.get("speed_multiplier")), 1.0),
            finish_pipeline_preset=str(data.get("finish_pipeline_preset", "custom") or "custom"),
            vram_profile=str(data.get("vram_profile", data.get("profile", "medium")) or "medium"),
            clear_cache_after_n_frames=_int_or_none(data.get("clear_cache_after_n_frames")),
            frame_load_cap=_int_or_none(data.get("frame_load_cap")),
            skip_first_frames=_int_value(data.get("skip_first_frames"), 0),
            select_every_nth=_int_value(data.get("select_every_nth"), 1),
            source_fps=_float_or_none(data.get("source_fps")),
            pix_fmt=str(data.get("pix_fmt", "yuv420p") or "yuv420p"),
            crf=_int_value(data.get("crf"), 20),
            save_metadata=bool(data.get("save_metadata", True)),
            trim_to_audio=bool(data.get("trim_to_audio", False)),
            filename_prefix=str(data.get("filename_prefix", "Neo_Video_Interpolated") or "Neo_Video_Interpolated"),
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


def _float_value(value: Any, fallback: float) -> float:
    parsed = _float_or_none(value)
    if parsed is None:
        return fallback
    return parsed


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _int_value(value: Any, fallback: int) -> int:
    parsed = _int_or_none(value)
    if parsed is None:
        return fallback
    return parsed


def _clamp_int(value: int | None, fallback: int, minimum: int, maximum: int) -> int:
    parsed = fallback if value is None else int(value)
    return max(minimum, min(parsed, maximum))


def _clamp_float_range(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    parsed = _float_or_none(value)
    if parsed is None:
        parsed = fallback
    return max(minimum, min(float(parsed), maximum))


def _motion_timing_policy(value: str | None) -> str:
    key = str(value or "same_duration").strip().lower().replace("-", "_")
    return key if key in MOTION_TIMING_POLICIES else "same_duration"


def _finish_pipeline_preset(value: str | None) -> str:
    key = str(value or "custom").strip().lower().replace("-", "_")
    return key if key in FINISH_PIPELINE_PRESETS else "custom"


def _profile_preset(profile_id: str | None) -> tuple[str, dict[str, Any]]:
    key = str(profile_id or "medium").strip().lower().replace(" ", "_")
    if key not in VRAM_PROFILE_PRESETS:
        return "medium", VRAM_PROFILE_PRESETS["medium"]
    return key, VRAM_PROFILE_PRESETS[key]


def _format_multiplier(value: float | int) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _source_fps_from_result(source_result_id: str | None) -> float | None:
    if not source_result_id:
        return None
    loaded = load_video_output_record(str(source_result_id))
    if not loaded.get("ok"):
        return None
    record = loaded.get("record") if isinstance(loaded.get("record"), dict) else {}
    candidates: list[Any] = []
    for bucket in (record.get("parameters"), record.get("replay_payload")):
        if isinstance(bucket, dict):
            candidates.extend([bucket.get("fps"), bucket.get("source_fps")])
    for value in candidates:
        parsed = _float_or_none(value)
        if parsed and parsed > 0:
            return parsed
    return None


def _available_stable_methods(readiness: dict[str, Any] | None) -> set[str]:
    admin = readiness.get("admin_readiness") if isinstance(readiness, dict) else {}
    methods = admin.get("methods") if isinstance(admin, dict) else {}
    available = methods.get("available_stable") if isinstance(methods, dict) else []
    if isinstance(available, list) and available:
        return {str(item).lower() for item in available}
    if isinstance(readiness, dict) and readiness.get("ready"):
        return {"rife"}
    return set()


def normalize_interpolation_request(
    req: VideoInterpolationRequest,
    *,
    readiness: dict[str, Any] | None = None,
    source: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Normalize browser/API interpolation params into the backend V24.7 contract.

    The browser may suggest values, but the backend owns profile enforcement,
    method availability, output FPS derivation, and memory-safety clamps.
    """
    warnings: list[str] = []
    profile_id, preset = _profile_preset(req.vram_profile)
    if profile_id != str(req.vram_profile or "medium").strip().lower().replace(" ", "_"):
        warnings.append(f"Unknown VRAM profile '{req.vram_profile}' was normalized to medium.")

    requested_method = str(req.method or "auto").strip().lower()
    if requested_method not in {"auto", "rife", "film", "amt"}:
        warnings.append(f"Unknown interpolation method '{req.method}' was normalized to auto.")
        requested_method = "auto"
    allowed_methods = {str(item).lower() for item in preset.get("allowed_methods", ("auto", "rife"))}
    available_methods = _available_stable_methods(readiness)

    method_effective = requested_method
    if requested_method == "auto":
        method_effective = "rife" if (not available_methods or "rife" in available_methods) else next(iter(sorted(available_methods)), "rife")
    elif requested_method not in allowed_methods:
        warnings.append(f"{requested_method.upper()} is not allowed for the {profile_id} VRAM profile; using RIFE.")
        method_effective = "rife"
    elif available_methods and requested_method not in available_methods:
        warnings.append(f"{requested_method.upper()} was requested but is not detected; using RIFE stable default.")
        method_effective = "rife"

    allowed_multipliers = [float(item) for item in preset.get("allowed_multipliers", (2.0,))]
    requested_multiplier = float(req.fps_multiplier or preset.get("default_multiplier", 2.0))
    fps_multiplier = requested_multiplier
    if fps_multiplier not in allowed_multipliers:
        fallback = float(preset.get("default_multiplier", allowed_multipliers[0]))
        warnings.append(f"{_format_multiplier(requested_multiplier)}x interpolation is not allowed for the {profile_id} VRAM profile; using {_format_multiplier(fallback)}x.")
        fps_multiplier = fallback

    clear_cache = _clamp_int(req.clear_cache_after_n_frames, int(preset.get("clear_cache_after_n_frames", 8)), 1, 120)
    frame_load_default = int(preset.get("frame_load_cap") or 0)
    frame_load_cap = _clamp_int(req.frame_load_cap, frame_load_default, 0, 100000)
    skip_first_frames = _clamp_int(req.skip_first_frames, int(preset.get("skip_first_frames", 0)), 0, 100000)
    select_every_nth = _clamp_int(req.select_every_nth, int(preset.get("select_every_nth", 1)), 1, 1000)

    output_format = str(req.output_format or preset.get("output_format", "webm")).strip().lower()
    if output_format not in {"webm", "mp4"}:
        warnings.append(f"Unsupported output format '{req.output_format}' was normalized to webm.")
        output_format = "webm"

    pix_fmt = str(req.pix_fmt or "yuv420p").strip().lower()
    if pix_fmt not in {"yuv420p", "yuv444p", "yuv420p10le"}:
        warnings.append(f"Unsupported pixel format '{req.pix_fmt}' was normalized to yuv420p.")
        pix_fmt = "yuv420p"
    crf = _clamp_int(req.crf, 20, 0, 51)
    save_metadata = bool(req.save_metadata)
    trim_to_audio = bool(req.trim_to_audio)

    source_fps = req.source_fps or _float_or_none((source or {}).get("source_fps")) or _source_fps_from_result(req.source_result_id)
    manual_output_fps = req.output_fps if req.output_fps and req.output_fps > 0 else None
    requested_motion_policy = _motion_timing_policy(req.motion_timing_policy)
    motion_speed_multiplier = _clamp_float_range(req.motion_speed_multiplier, 1.0, 0.25, 4.0)
    if requested_motion_policy != "motion_speed_repair":
        motion_speed_multiplier = 1.0
    finish_pipeline_preset = _finish_pipeline_preset(req.finish_pipeline_preset)
    output_fps_policy = str(req.output_fps_policy or FPS_AUTO_POLICY)
    output_fps_source = "manual_override"
    output_fps = manual_output_fps
    if output_fps is None:
        if source_fps and source_fps > 0:
            if requested_motion_policy == "motion_speed_repair":
                output_fps_policy = FPS_MOTION_REPAIR_POLICY
                output_fps = round(float(source_fps) * float(fps_multiplier) * motion_speed_multiplier, 3)
                output_fps_source = "source_fps_times_multiplier_times_motion_speed"
                warnings.append(f"Motion timing repair is active; saver FPS is multiplied by {motion_speed_multiplier:g}.")
            elif requested_motion_policy == "slow_motion":
                output_fps_policy = FPS_SLOW_MOTION_POLICY
                output_fps = round(float(source_fps), 3)
                output_fps_source = "source_fps_after_interpolation_slow_motion"
            else:
                output_fps_policy = FPS_AUTO_POLICY
                output_fps = round(float(source_fps) * float(fps_multiplier), 3)
                output_fps_source = "source_fps_times_multiplier"
        else:
            output_fps_policy = FPS_AUTO_POLICY
            output_fps = 24.0
            output_fps_source = "fallback_24_source_fps_unknown"
            warnings.append("Source FPS was unavailable; compiler uses 24 FPS fallback for the saver node.")

    return {
        "schema_version": NORMALIZATION_SCHEMA_VERSION,
        "phase": BACKEND_WIRING_PHASE,
        "vram_profile": profile_id,
        "profile_label": str(preset.get("label", profile_id)),
        "method_requested": requested_method,
        "method_effective": method_effective,
        "fps_multiplier": fps_multiplier,
        "fps_multiplier_display": _format_multiplier(fps_multiplier),
        "output_fps": output_fps,
        "output_fps_policy": output_fps_policy,
        "output_fps_source": output_fps_source,
        "source_fps": source_fps,
        "motion_timing_schema_version": MOTION_TIMING_SCHEMA_VERSION,
        "pipeline_preset_schema_version": PIPELINE_PRESET_SCHEMA_VERSION,
        "motion_timing_policy": requested_motion_policy,
        "motion_speed_multiplier": motion_speed_multiplier,
        "finish_pipeline_preset": finish_pipeline_preset,
        "output_format": output_format,
        "clear_cache_after_n_frames": clear_cache,
        "frame_load_cap": frame_load_cap,
        "skip_first_frames": skip_first_frames,
        "select_every_nth": select_every_nth,
        "pix_fmt": pix_fmt,
        "crf": crf,
        "save_metadata": save_metadata,
        "trim_to_audio": trim_to_audio,
        "available_stable_methods": sorted(available_methods),
        "allowed_methods": sorted(allowed_methods),
        "allowed_multipliers": [_format_multiplier(item) for item in allowed_multipliers],
        "warnings": warnings,
        "base_generation_blocked": False,
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


def _required_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    required = inputs.get("required", {}) if isinstance(inputs, dict) else {}
    return required if isinstance(required, dict) else {}


def _node_outputs(object_info: dict[str, Any], class_type: str) -> list[str]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    outputs = entry.get("output", []) if isinstance(entry, dict) else []
    if isinstance(outputs, (list, tuple)):
        return [str(item).upper() for item in outputs]
    if isinstance(outputs, dict):
        return [str(item).upper() for item in outputs.values()]
    return []


def _input_type(spec: Any) -> str:
    if isinstance(spec, (list, tuple)) and spec:
        return str(spec[0]).upper()
    if isinstance(spec, dict):
        return str(spec.get("type", spec.get("rawType", ""))).upper()
    return str(spec or "").upper()


def _has_image_input(required: dict[str, Any], names: tuple[str, ...] = ("frames", "images", "image")) -> bool:
    folded = {str(key).casefold(): value for key, value in required.items()}
    for name in names:
        value = folded.get(name.casefold())
        if value is None:
            continue
        input_type = _input_type(value)
        if not input_type or "IMAGE" in input_type:
            return True
    return False


def _find_interpolator_class(object_info: dict[str, Any], candidates: tuple[str, ...]) -> str | None:
    """Find an execution VFI node, not a model-loader node.

    Some ComfyUI Frame Interpolation installs expose similarly named model loader
    nodes whose first output is INTERP_MODEL. Those validate as the wrong type when
    linked into VHS_VideoCombine. The finish compiler must pick a node that accepts
    IMAGE frames/images and, when output metadata is available, returns IMAGE.
    """
    info = object_info or {}
    exact_candidates: list[str] = []
    fuzzy_candidates: list[str] = []
    folded = {str(key).casefold(): str(key) for key in info.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            exact_candidates.append(found)
    for candidate in candidates:
        needle = candidate.casefold().replace(" ", "").replace("_", "")
        for key in info.keys():
            compact = str(key).casefold().replace(" ", "").replace("_", "")
            if needle and needle in compact:
                fuzzy_candidates.append(str(key))

    seen: set[str] = set()
    ordered = [item for item in exact_candidates + fuzzy_candidates if not (item in seen or seen.add(item))]
    for class_type in ordered:
        lowered = class_type.casefold()
        if any(bad in lowered for bad in ("load", "loader", "model", "download")) and "vfi" not in lowered:
            continue
        required = _required_inputs(info, class_type)
        outputs = _node_outputs(info, class_type)
        if required and not _has_image_input(required):
            continue
        if outputs and not any(output in {"IMAGE", "IMAGES"} for output in outputs):
            continue
        return class_type
    return None


def _video_combine_format(output_format: str, required: dict[str, Any] | None = None) -> str:
    requested = str(output_format or "webm").strip().lower()
    mapped = "video/h264-mp4" if requested == "mp4" else "video/webm"
    spec = (required or {}).get("format")
    choices: list[str] = []
    if isinstance(spec, (list, tuple)) and len(spec) > 1 and isinstance(spec[1], dict):
        raw_choices = spec[1].get("choices") or spec[1].get("values")
        if isinstance(raw_choices, (list, tuple)):
            choices = [str(item) for item in raw_choices]
    if choices and mapped not in choices:
        fallback = "video/webm" if "video/webm" in choices else choices[0]
        return fallback
    return mapped


def _first_field(required: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    folded = {key.casefold(): key for key in required.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


def _is_path_video_loader(class_type: str) -> bool:
    lowered = str(class_type or "").casefold().replace("_", "").replace(" ", "")
    return "path" in lowered or "frompath" in lowered


def _is_upload_video_loader(class_type: str) -> bool:
    lowered = str(class_type or "").casefold().replace("_", "").replace(" ", "")
    return lowered in {"vhsloadvideo", "loadvideo", "loadvideoupload"} or "upload" in lowered


def _load_video_source_value(source: dict[str, Any], class_type: str) -> str:
    if _is_path_video_loader(class_type):
        return str(source.get("path") or source.get("relative_path") or "")
    # Upload/input-folder loaders validate against ComfyUI/input. Prefer a bare
    # filename if the source was already handed to Comfy; otherwise fall back to
    # the Neo relative path so compile sidecars remain inspectable. Runtime should
    # use a path loader whenever available.
    return str(source.get("comfy_video_name") or Path(str(source.get("path") or source.get("relative_path") or "")).name or source.get("relative_path") or "")


def _fill_load_video_required_inputs(load_inputs: dict[str, Any], required: dict[str, Any], normalized: dict[str, Any]) -> None:
    if "force_rate" in required:
        load_inputs["force_rate"] = float(normalized.get("source_fps") or 0)
    if "custom_width" in required:
        load_inputs["custom_width"] = 0
    if "custom_height" in required:
        load_inputs["custom_height"] = 0
    if "frame_load_cap" in required:
        load_inputs["frame_load_cap"] = int(normalized.get("frame_load_cap") or 0)
    if "skip_first_frames" in required:
        load_inputs["skip_first_frames"] = int(normalized.get("skip_first_frames") or 0)
    if "select_every_nth" in required:
        load_inputs["select_every_nth"] = int(normalized.get("select_every_nth") or 1)


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


def resolve_interpolation_source(req: VideoInterpolationRequest) -> dict[str, Any]:
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
            "error": "Interpolation requires a Neo-owned source video. Select a Video result with an attached file or provide a path under neo_data/outputs/video.",
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
        "source_fps": req.source_fps or _source_fps_from_result(req.source_result_id),
    }


def discover_interpolation_bindings(object_info: dict[str, Any] | None = None, method: str = "auto") -> dict[str, Any]:
    info = object_info or {}
    # Prefer path-based loaders for Finish sources. Neo stores staged source videos
    # under neo_data/outputs/video/source; VHS_LoadVideo is an upload/input-folder
    # loader and rejects those Neo paths as "Invalid video file". Path loaders can
    # read the actual Neo-owned local file directly.
    load_video = _class_exists(info, "VHS_LoadVideoPath", "LoadVideoPath", "LoadVideoFromPath", "VHS_LoadVideo", "LoadVideo", "LoadVideoUpload") or "VHS_LoadVideoPath"
    requested = str(method or "auto").lower()
    if requested == "film":
        vfi = _find_interpolator_class(info, ("FILM_VFI", "FILM VFI", "FrameInterpolation")) or "FILM_VFI"
    elif requested == "amt":
        vfi = _find_interpolator_class(info, ("AMT_VFI", "AMT VFI", "FrameInterpolation")) or "AMT_VFI"
    else:
        vfi = _find_interpolator_class(info, ("RIFE VFI", "RIFE_VFI", "VFI Generate", "FILM_VFI", "FILM VFI", "AMT_VFI", "FrameInterpolation", "VFI")) or "RIFE VFI"
    save_video = _class_exists(info, "VHS_VideoCombine", "VideoCombine", "SaveWEBM", "SaveAnimatedWEBP") or "VHS_VideoCombine"
    return {
        "classes": {"load_video": load_video, "interpolator": vfi, "saver": save_video},
        "available": {"load_video": load_video in info, "interpolator": vfi in info, "saver": save_video in info},
        "method": requested if requested in {"auto", "rife", "film", "amt"} else "auto",
    }


def interpolation_node_readiness(object_info: dict[str, Any] | None) -> dict[str, Any]:
    packs = evaluate_video_external_node_packs(object_info or {})
    by_id = {item["pack_id"]: item for item in packs}
    readiness = frame_interpolation_admin_readiness(object_info or {}, packs=packs)
    missing = []
    if "video_helper_suite" in readiness.get("missing_required", []):
        missing.append("ComfyUI VideoHelperSuite / video load-combine nodes")
    if "frame_interpolation" in readiness.get("missing_required", []):
        missing.append("ComfyUI Frame Interpolation nodes")
    if "rife_vfi_default" in readiness.get("missing_required", []):
        missing.append("RIFE VFI default interpolation node")
    return {
        "ready": bool(readiness.get("ready")),
        "required": ["video_helper_suite", "frame_interpolation", "rife_vfi_default"],
        "missing_required": missing,
        "packs": {
            "video_helper_suite": by_id.get("video_helper_suite", {}),
            "frame_interpolation": by_id.get("frame_interpolation", {}),
            "rife_tensorrt": by_id.get("rife_tensorrt", {}),
            "gimm_vfi": by_id.get("gimm_vfi", {}),
            "vsrfi_stream": by_id.get("vsrfi_stream", {}),
        },
        "admin_readiness": readiness,
        "base_generation_blocked": False,
    }


def build_interpolation_workflow(req: VideoInterpolationRequest, source: dict[str, Any], object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    readiness = interpolation_node_readiness(object_info or {})
    normalized = normalize_interpolation_request(req, readiness=readiness, source=source)
    bindings = discover_interpolation_bindings(object_info, method=normalized["method_effective"])
    classes = bindings["classes"]
    prefix = sanitize_path_part(req.filename_prefix, "Neo_Video_Interpolated")
    fps_multiplier = float(normalized["fps_multiplier"])
    output_fps = float(normalized["output_fps"]) if normalized.get("output_fps") else 24.0

    load_required = _required_inputs(object_info or {}, classes["load_video"])
    load_field_candidates = ("video_path", "path", "file", "filename", "video") if _is_path_video_loader(classes["load_video"]) else ("video", "file", "filename", "video_path", "path")
    video_field = _first_field(load_required, load_field_candidates, "video_path" if _is_path_video_loader(classes["load_video"]) else "video")
    load_inputs: dict[str, Any] = {video_field: _load_video_source_value(source, classes["load_video"])}
    _fill_load_video_required_inputs(load_inputs, load_required, normalized)

    interpolator_required = _required_inputs(object_info or {}, classes["interpolator"])
    frame_field = _first_field(interpolator_required, ("frames", "images", "image"), "frames")
    interp_inputs: dict[str, Any] = {frame_field: ["1", 0]}
    if "ckpt_name" in interpolator_required:
        interp_inputs["ckpt_name"] = "rife49.pth"
    if "multiplier" in interpolator_required:
        interp_inputs["multiplier"] = _format_multiplier(fps_multiplier)
    elif "interpolation" in interpolator_required:
        interp_inputs["interpolation"] = int(fps_multiplier)
    if "clear_cache_after_n_frames" in interpolator_required:
        interp_inputs["clear_cache_after_n_frames"] = int(normalized["clear_cache_after_n_frames"])
    if "fast_mode" in interpolator_required:
        interp_inputs["fast_mode"] = True
    if "ensemble" in interpolator_required:
        interp_inputs["ensemble"] = True
    if "scale_factor" in interpolator_required:
        interp_inputs["scale_factor"] = 1.0
    if "dtype" in interpolator_required:
        interp_inputs["dtype"] = "float16"
    if "torch_compile" in interpolator_required:
        interp_inputs["torch_compile"] = False
    if "batch_size" in interpolator_required:
        interp_inputs["batch_size"] = 1 if normalized["vram_profile"] == "low" else 4

    saver_class = classes["saver"]
    saver_required = _required_inputs(object_info or {}, saver_class)
    images_field = _first_field(saver_required, ("images", "frames"), "images")
    saver_inputs: dict[str, Any] = {images_field: ["2", 0], "filename_prefix": prefix}
    is_video_combine = saver_class in {"VHS_VideoCombine", "VideoCombine"}
    if "frame_rate" in saver_required or (is_video_combine and "fps" not in saver_required):
        saver_inputs["frame_rate"] = output_fps
    elif "fps" in saver_required or saver_class in {"SaveWEBM", "SaveAnimatedWEBP"}:
        saver_inputs["fps"] = output_fps
    if saver_class == "SaveWEBM":
        saver_inputs.update({"codec": "vp9", "crf": 20})
    if saver_class == "SaveAnimatedWEBP":
        saver_inputs.update({"lossless": False, "quality": 90, "method": "default"})
    if is_video_combine:
        if "loop_count" in saver_required or not saver_required:
            saver_inputs["loop_count"] = 0
        if "pingpong" in saver_required or not saver_required:
            saver_inputs["pingpong"] = False
        if "save_output" in saver_required or not saver_required:
            saver_inputs["save_output"] = True
        if "pix_fmt" in saver_required or not saver_required:
            saver_inputs["pix_fmt"] = normalized["pix_fmt"]
        if "crf" in saver_required or not saver_required:
            saver_inputs["crf"] = int(normalized["crf"])
        if "save_metadata" in saver_required or not saver_required:
            saver_inputs["save_metadata"] = bool(normalized["save_metadata"])
        if "trim_to_audio" in saver_required or not saver_required:
            saver_inputs["trim_to_audio"] = bool(normalized["trim_to_audio"])
    if "format" in saver_required or is_video_combine:
        saver_inputs["format"] = _video_combine_format(normalized["output_format"], saver_required) if is_video_combine else normalized["output_format"]

    workflow = {
        "1": {"class_type": classes["load_video"], "inputs": load_inputs},
        "2": {"class_type": classes["interpolator"], "inputs": interp_inputs},
        "3": {"class_type": classes["saver"], "inputs": saver_inputs},
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "surface": "video",
        "phase": PHASE,
        "route_id": ROUTE_ID,
        "compiled_at": _now(),
        "parameters": {
            "method": normalized["method_effective"],
            "method_requested": normalized["method_requested"],
            "vram_profile": normalized["vram_profile"],
            "fps_multiplier": _format_multiplier(fps_multiplier),
            "output_fps": output_fps,
            "output_fps_policy": normalized["output_fps_policy"],
            "output_fps_source": normalized["output_fps_source"],
            "source_fps": normalized["source_fps"],
            "output_format": normalized["output_format"],
            "clear_cache_after_n_frames": normalized["clear_cache_after_n_frames"],
            "frame_load_cap": normalized["frame_load_cap"],
            "skip_first_frames": normalized["skip_first_frames"],
            "select_every_nth": normalized["select_every_nth"],
            "pix_fmt": normalized["pix_fmt"],
            "crf": normalized["crf"],
            "save_metadata": normalized["save_metadata"],
            "trim_to_audio": normalized["trim_to_audio"],
        },
        "normalization": normalized,
        "bindings": bindings,
        "source": source,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "rules": [
            "V12 interpolation is a non-destructive Finish lane that creates a child result.",
            "The source video must be a Neo-owned output under neo_data/outputs/video.",
            "Interpolation depends on optional ComfyUI video I/O and frame interpolation node packs detected in V11/V24.5.",
            "V24.7 normalizes VRAM profile, method availability, multiplier, cache, frame cap, and FPS policy server-side before compiling.",
            "V24.8 stores normalized payloads, method requested/used, source FPS, and parent-child lineage in the child result metadata.",
            "The parent result remains untouched; output files attach to a new interpolate ledger record after refresh/import.",
        ],
    }


def build_interpolation_output_metadata(compiled: dict[str, Any], req: VideoInterpolationRequest) -> dict[str, Any]:
    """Build the V24.8 replay/memory-friendly metadata envelope for interpolation children.

    The child output record owns the lineage and extension metadata. The parent
    video remains untouched to preserve the non-destructive Finish contract.
    """
    parameters = compiled.get("parameters") if isinstance(compiled.get("parameters"), dict) else {}
    normalization = compiled.get("normalization") if isinstance(compiled.get("normalization"), dict) else {}
    source = compiled.get("source") if isinstance(compiled.get("source"), dict) else {}
    node_readiness = compiled.get("node_readiness") if isinstance(compiled.get("node_readiness"), dict) else {}
    bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
    warnings = compiled.get("warnings") if isinstance(compiled.get("warnings"), list) else []
    parent_result_id = str(req.source_result_id or source.get("source_result_id") or "")
    source_file_id = str(req.source_file_id or source.get("source_file_id") or "")
    method_effective = str(parameters.get("method") or normalization.get("method_effective") or "rife")
    method_requested = str(parameters.get("method_requested") or normalization.get("method_requested") or req.method or "auto")
    payload_params = {
        "method": method_effective,
        "method_requested": method_requested,
        "vram_profile": parameters.get("vram_profile") or normalization.get("vram_profile") or req.vram_profile,
        "fps_multiplier": parameters.get("fps_multiplier") or normalization.get("fps_multiplier"),
        "output_fps": parameters.get("output_fps") or normalization.get("output_fps"),
        "output_fps_policy": parameters.get("output_fps_policy") or normalization.get("output_fps_policy"),
        "output_fps_source": parameters.get("output_fps_source") or normalization.get("output_fps_source"),
        "source_fps": parameters.get("source_fps") or normalization.get("source_fps"),
        "motion_timing_policy": parameters.get("motion_timing_policy") or normalization.get("motion_timing_policy"),
        "motion_speed_multiplier": parameters.get("motion_speed_multiplier") or normalization.get("motion_speed_multiplier"),
        "finish_pipeline_preset": parameters.get("finish_pipeline_preset") or normalization.get("finish_pipeline_preset"),
        "output_format": parameters.get("output_format") or normalization.get("output_format"),
        "clear_cache_after_n_frames": parameters.get("clear_cache_after_n_frames") or normalization.get("clear_cache_after_n_frames"),
        "frame_load_cap": parameters.get("frame_load_cap") or normalization.get("frame_load_cap"),
        "skip_first_frames": parameters.get("skip_first_frames") or normalization.get("skip_first_frames"),
        "select_every_nth": parameters.get("select_every_nth") or normalization.get("select_every_nth"),
        "pix_fmt": parameters.get("pix_fmt") or normalization.get("pix_fmt"),
        "crf": parameters.get("crf") if parameters.get("crf") is not None else normalization.get("crf"),
        "save_metadata": parameters.get("save_metadata") if parameters.get("save_metadata") is not None else normalization.get("save_metadata"),
        "trim_to_audio": parameters.get("trim_to_audio") if parameters.get("trim_to_audio") is not None else normalization.get("trim_to_audio"),
    }
    payload_params = {key: value for key, value in payload_params.items() if value is not None}
    return {
        "schema_version": OUTPUT_METADATA_SCHEMA_VERSION,
        "phase": METADATA_LINEAGE_PHASE,
        "extension_id": EXTENSION_ID,
        "finish_operation": FINISH_OPERATION_ID,
        "category": "interpolate",
        "non_destructive": True,
        "parent_result_unchanged": True,
        "lineage": {
            "relationship": "child_of",
            "parent_result_id": parent_result_id,
            "source_result_id": parent_result_id,
            "source_file_id": source_file_id,
            "source_kind": source.get("kind") or "",
            "source_video_path": source.get("relative_path") or source.get("path") or "",
            "child_category": "interpolate",
            "child_operation": FINISH_OPERATION_ID,
        },
        "finish": {
            "operation": FINISH_OPERATION_ID,
            "extension_id": EXTENSION_ID,
            "method_requested": method_requested,
            "method_used": method_effective,
            "method_effective": method_effective,
            "vram_profile": payload_params.get("vram_profile"),
            "fps_multiplier": payload_params.get("fps_multiplier"),
            "source_fps": payload_params.get("source_fps"),
            "motion_timing_policy": payload_params.get("motion_timing_policy"),
            "motion_speed_multiplier": payload_params.get("motion_speed_multiplier"),
            "finish_pipeline_preset": payload_params.get("finish_pipeline_preset"),
            "motion_timing_policy": payload_params.get("motion_timing_policy"),
            "motion_speed_multiplier": payload_params.get("motion_speed_multiplier"),
            "finish_pipeline_preset": payload_params.get("finish_pipeline_preset"),
            "output_fps": payload_params.get("output_fps"),
            "output_fps_policy": payload_params.get("output_fps_policy"),
            "output_fps_source": payload_params.get("output_fps_source"),
            "output_format": payload_params.get("output_format"),
            "pix_fmt": payload_params.get("pix_fmt"),
            "crf": payload_params.get("crf"),
            "save_metadata": payload_params.get("save_metadata"),
            "trim_to_audio": payload_params.get("trim_to_audio"),
        },
        "extensions": {
            "used": [EXTENSION_ID],
            "payloads": {
                EXTENSION_ID: {
                    "schema_version": "neo.extension.payload.v1",
                    "enabled": True,
                    "version": METADATA_LINEAGE_PHASE,
                    "stage": "finish",
                    "surface": "video",
                    "mount_slot": "video.finish.finish_interpolation",
                    "operation": FINISH_OPERATION_ID,
                    "inputs": {
                        "source_result_id": parent_result_id,
                        "source_file_id": source_file_id,
                        "source_video_path": source.get("relative_path") or source.get("path") or "",
                    },
                    "params": payload_params,
                    "metadata": {
                        "normalization_schema": normalization.get("schema_version") or NORMALIZATION_SCHEMA_VERSION,
                        "output_metadata_schema": OUTPUT_METADATA_SCHEMA_VERSION,
                        "method_requested": method_requested,
                        "method_used": method_effective,
                        "warnings": list(warnings),
                    },
                }
            },
            "validation": {
                "stable_ready": bool(node_readiness.get("ready")),
                "readiness_schema": "neo.video.finish.frame_interpolation.readiness.v1",
                "missing_required": node_readiness.get("missing_required") if isinstance(node_readiness.get("missing_required"), list) else [],
                "base_generation_blocked": False,
            },
            "workflow_patches": {
                "provider_graph_mutation": False,
                "post_generation_child_output": True,
                "compiler_param_wiring_phase": BACKEND_WIRING_PHASE,
            },
        },
        "replay_context": {
            "source_fps": payload_params.get("source_fps"),
            "motion_timing_policy": payload_params.get("motion_timing_policy"),
            "motion_speed_multiplier": payload_params.get("motion_speed_multiplier"),
            "finish_pipeline_preset": payload_params.get("finish_pipeline_preset"),
            "output_fps": payload_params.get("output_fps"),
            "fps_multiplier": payload_params.get("fps_multiplier"),
            "method_requested": method_requested,
            "method_used": method_effective,
            "vram_profile": payload_params.get("vram_profile"),
            "normalization": normalization,
            "bindings": bindings,
        },
        "memory_event": {
            "event_type": "extension_workflow_used",
            "namespace": f"extension:{EXTENSION_ID}",
            "surface": "video",
            "route_id": ROUTE_ID,
            "operation": FINISH_OPERATION_ID,
            "parent_result_id": parent_result_id,
            "method_requested": method_requested,
            "method_used": method_effective,
            "vram_profile": payload_params.get("vram_profile"),
            "fps_multiplier": payload_params.get("fps_multiplier"),
        },
    }


def _post_json(base_url: str, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NeoStudioVideoInterpolation/1.0"}, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local user-configured Comfy URL.
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data) if data else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _persist_finish_record(result: dict[str, Any], req: VideoInterpolationRequest) -> dict[str, Any]:
    output_metadata = result.get("output_metadata") if isinstance(result.get("output_metadata"), dict) else build_interpolation_output_metadata(result, req)
    lineage = output_metadata.get("lineage") if isinstance(output_metadata.get("lineage"), dict) else {}
    request_payload = {
        **req.payload(),
        "family": "finish",
        "loader": "external_nodes",
        "generation_type": "interpolate",
        "prompt": "Video Finish: frame interpolation",
        "negative_prompt": "",
        "source_result_id": str(lineage.get("source_result_id") or req.source_result_id or ""),
        "source_file_id": str(lineage.get("source_file_id") or req.source_file_id or ""),
        "parent_result_id": str(lineage.get("parent_result_id") or req.source_result_id or ""),
        "extension_id": EXTENSION_ID,
        "finish_operation": FINISH_OPERATION_ID,
        "output_metadata_schema": OUTPUT_METADATA_SCHEMA_VERSION,
    }
    result = {
        **result,
        "output_metadata": output_metadata,
        "extensions": output_metadata.get("extensions", {}),
        "finish": output_metadata.get("finish", {}),
        "lineage": output_metadata.get("lineage", {}),
        "memory_event": output_metadata.get("memory_event", {}),
    }
    try:
        ledger = register_video_generation_result(result, request=request_payload)
    except Exception as exc:  # noqa: BLE001
        return {**result, "neo_persisted": {"ok": False, "error": f"Video interpolation ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def video_interpolation_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = VideoInterpolationRequest.from_payload(payload)
    source = resolve_interpolation_source(req)
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
            warnings.append(f"Compiled with fallback interpolation bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    readiness = interpolation_node_readiness(object_info)
    compiled = build_interpolation_workflow(req, source, object_info=object_info)
    normalization_warnings = compiled.get("normalization", {}).get("warnings") if isinstance(compiled.get("normalization"), dict) else []
    if isinstance(normalization_warnings, list):
        warnings.extend(str(item) for item in normalization_warnings if str(item))
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    output_paths = get_video_output_paths("interpolate", create=True)
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'video_interpolate')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "node_readiness": readiness}
    sidecar_payload["output_metadata"] = build_interpolation_output_metadata(sidecar_payload, req)
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


def video_interpolation_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = VideoInterpolationRequest.from_payload(payload)
    compile_payload = video_interpolation_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    if not compile_payload.get("node_readiness", {}).get("ready"):
        return {**compile_payload, "ok": False, "queued": False, "error": "Frame Interpolation is missing required ComfyUI video I/O or RIFE VFI nodes. Base Video Generation is unaffected."}
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI interpolation queue failed: {exc}"}
    response_payload = {
        **compile_payload,
        "ok": True,
        "queued": True,
        "dry_run": False,
        "queue_response": queue_response,
        "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or "",
    }
    return _persist_finish_record(response_payload, req)
