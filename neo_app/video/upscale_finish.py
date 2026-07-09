from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from neo_app.video.backend_probe import _get_json, video_backend_profile_payload
from neo_app.video.external_node_manager import SEEDVR2_UPSCALE_ADMIN_READINESS_SCHEMA, seedvr2_upscale_admin_readiness
from neo_app.video.output_paths import ROOT_DIR, get_video_output_paths, sanitize_path_part
from neo_app.video.output_records import load_video_output_record, register_video_generation_result, video_output_file_path

SCHEMA_VERSION: Final[str] = "neo.video.finish.upscale.v13"
REQUEST_SCHEMA_VERSION: Final[str] = "neo.video.finish.seedvr2_upscale.request.v25_9_19_phase_4"
VRAM_PROFILE_SCHEMA_VERSION: Final[str] = "neo.video.finish.seedvr2_upscale.vram_profiles.v25_9_19_phase_5"
UI_SCHEMA_VERSION: Final[str] = "neo.video.finish.seedvr2_upscale.ui.v25_9_19_phase_3"
MANIFEST_SCHEMA_VERSION: Final[str] = "neo.video.finish.seedvr2_upscale.manifest.v25_9_19_phase_10"
OUTPUT_METADATA_SCHEMA_VERSION: Final[str] = "neo.video.finish.seedvr2_upscale.output_metadata.v25_9_19_phase_10"
MOTION_TIMING_SCHEMA_VERSION: Final[str] = "neo.video.finish.motion_timing_repair.v25_9_19_phase_10k"
PIPELINE_PRESET_SCHEMA_VERSION: Final[str] = "neo.video.finish.pipeline_presets.v25_9_19_phase_10k"
PHASE: Final[str] = "V25.9.19 Phase 8"
LEGACY_PHASE: Final[str] = "V13"
ROUTE_ID: Final[str] = "finish.upscale"
WORKFLOW_SCHEMA_VERSION: Final[str] = "neo.video.finish.seedvr2_upscale.workflow.v25_9_19_phase_7"
STRIP_INTERPOLATION_LOCK_SCHEMA_VERSION: Final[str] = "neo.video.finish.seedvr2_upscale.no_interpolation_lock.v25_9_19_phase_7"
SEEDVR2_UPSCALE_COMPILER_ID: Final[str] = "seedvr2_native_no_interpolation_phase7"
EXTENSION_ID: Final[str] = "video.finish_upscale"
FINISH_OPERATION_ID: Final[str] = "upscale"
MOUNT_SLOT: Final[str] = "video.finish.finish_upscale"

VIDEO_EXTENSIONS: Final[tuple[str, ...]] = (".webm", ".mp4", ".mov", ".mkv", ".gif")
VRAM_PROFILES: Final[tuple[str, ...]] = ("low", "medium", "high", "custom")
TARGET_PRESETS: Final[tuple[str, ...]] = ("safe_720", "hd_1080", "custom")
OUTPUT_FORMATS: Final[tuple[str, ...]] = ("auto", "mp4", "webm")
FPS_POLICIES: Final[tuple[str, ...]] = ("preserve_source_fps", "manual_override", "motion_speed_repair")
MOTION_TIMING_POLICIES: Final[tuple[str, ...]] = ("same_duration", "motion_speed_repair", "slow_motion")
FINISH_PIPELINE_PRESETS: Final[tuple[str, ...]] = ("custom", "fast_finish", "quality_finish", "smooth_only", "upscale_only")
COLOR_CORRECTION_MODES: Final[tuple[str, ...]] = ("lab", "none")
OFFLOAD_DEVICES: Final[tuple[str, ...]] = ("cpu", "cuda:0", "auto", "none")
INTERPOLATION_FORBIDDEN_NODE_PATTERNS: Final[tuple[str, ...]] = (
    "rife",
    "vfi",
    "filmvfi",
    "amtvfi",
    "frameinterpolation",
    "comfymathexpression",
    "comfyswitchnode",
    "primitiveboolean",
    "primitiveint",
    "primitivefloat",
)
BASIC_FALLBACK_FORBIDDEN_NODE_PATTERNS: Final[tuple[str, ...]] = (
    "imagescaleby",
    "imagescale",
    "imageupscalewithmodel",
    "upscaleimageby",
)


SEEDVR2_VRAM_PROFILE_CONTRACTS: Final[dict[str, dict[str, Any]]] = {
    "low": {
        "id": "low",
        "label": "Low",
        "intent": "8-12GB VRAM / safest HD pass",
        "recommended_dit_model": "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
        "recommended_vae_model": "ema_vae_fp16.safetensors",
        "target_preset": "safe_720",
        "resolution": 720,
        "max_short_edge": 720,
        "batch_size": 5,
        "max_batch_size": 5,
        "blocks_to_swap": 32,
        "min_blocks_to_swap": 32,
        "max_blocks_to_swap": 64,
        "swap_io_components": True,
        "cpu_offload": True,
        "dit_offload_device": "cpu",
        "vae_offload_device": "cpu",
        "upscaler_offload_device": "cpu",
        "encode_tiled": True,
        "decode_tiled": True,
        "encode_tile_size": 768,
        "decode_tile_size": 512,
        "encode_tile_overlap": 128,
        "decode_tile_overlap": 128,
        "temporal_overlap": 3,
        "torch_compile": False,
    },
    "medium": {
        "id": "medium",
        "label": "Medium",
        "intent": "12-16GB VRAM / balanced 1080p pass",
        "recommended_dit_model": "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
        "recommended_vae_model": "ema_vae_fp16.safetensors",
        "target_preset": "hd_1080",
        "resolution": 1080,
        "max_short_edge": 1080,
        "batch_size": 9,
        "max_batch_size": 9,
        "blocks_to_swap": 24,
        "min_blocks_to_swap": 16,
        "max_blocks_to_swap": 32,
        "swap_io_components": False,
        "cpu_offload": True,
        "dit_offload_device": "cpu",
        "vae_offload_device": "cpu",
        "upscaler_offload_device": "cpu",
        "encode_tiled": True,
        "decode_tiled": True,
        "encode_tile_size": 1024,
        "decode_tile_size": 768,
        "encode_tile_overlap": 128,
        "decode_tile_overlap": 128,
        "temporal_overlap": 3,
        "torch_compile": False,
    },
    "high": {
        "id": "high",
        "label": "High",
        "intent": "24GB+ VRAM / higher throughput 1080p+ pass",
        "recommended_dit_model": "seedvr2_ema_3b_fp16.safetensors",
        "recommended_vae_model": "ema_vae_fp16.safetensors",
        "target_preset": "hd_1080",
        "resolution": 1080,
        "max_short_edge": 2160,
        "batch_size": 21,
        "max_batch_size": 33,
        "blocks_to_swap": 8,
        "min_blocks_to_swap": 0,
        "max_blocks_to_swap": 16,
        "swap_io_components": False,
        "cpu_offload": False,
        "dit_offload_device": "cpu",
        "vae_offload_device": "cpu",
        "upscaler_offload_device": "cpu",
        "encode_tiled": True,
        "decode_tiled": True,
        "encode_tile_size": 1024,
        "decode_tile_size": 1024,
        "encode_tile_overlap": 128,
        "decode_tile_overlap": 128,
        "temporal_overlap": 3,
        "torch_compile": False,
    },
    "custom": {
        "id": "custom",
        "label": "Custom",
        "intent": "Expert override / still safety-clamped",
        "recommended_dit_model": "auto",
        "recommended_vae_model": "auto",
        "target_preset": "hd_1080",
        "resolution": 1080,
        "max_short_edge": 4320,
        "batch_size": 9,
        "max_batch_size": 65,
        "blocks_to_swap": 24,
        "min_blocks_to_swap": 0,
        "max_blocks_to_swap": 64,
        "swap_io_components": False,
        "cpu_offload": True,
        "dit_offload_device": "cpu",
        "vae_offload_device": "cpu",
        "upscaler_offload_device": "cpu",
        "encode_tiled": True,
        "decode_tiled": True,
        "encode_tile_size": 1024,
        "decode_tile_size": 768,
        "encode_tile_overlap": 128,
        "decode_tile_overlap": 128,
        "temporal_overlap": 3,
        "torch_compile": False,
    },
}


def seedvr2_vram_profile_contract(profile_id: str | None = None) -> dict[str, Any]:
    profile = _choice(profile_id, VRAM_PROFILES, "medium")
    contract = dict(SEEDVR2_VRAM_PROFILE_CONTRACTS[profile])
    contract["schema_version"] = VRAM_PROFILE_SCHEMA_VERSION
    return contract


@dataclass(frozen=True)
class VideoSeedVR2UpscaleRequest:
    """Normalized V25.9.19 Phase 4 backend request for the SeedVR2 upscale lane.

    This request contract is stricter than the temporary V13 compiler. It preserves the
    existing runtime route while giving later phases a stable, validated SeedVR2 payload.
    """

    request_schema_version: str = REQUEST_SCHEMA_VERSION
    source_result_id: str | None = None
    source_file_id: str | None = None
    source_video_path: str | None = None
    engine: str = "seedvr2"
    vram_profile: str = "medium"
    target_preset: str = "hd_1080"
    resolution: int = 1080
    max_resolution: int = 0
    scale: float = 2.0
    target_width: int | None = None
    target_height: int | None = None
    output_fps_policy: str = "preserve_source_fps"
    manual_output_fps: float | None = None
    output_fps: float | None = None
    source_fps: float | None = None
    motion_timing_policy: str = "same_duration"
    motion_speed_multiplier: float = 1.0
    finish_pipeline_preset: str = "custom"
    output_format: str = "auto"
    seed: int = 42
    dit_model: str = "auto"
    vae_model: str = "auto"
    dit_device: str = "cuda:0"
    vae_device: str = "cuda:0"
    batch_size: int = 9
    uniform_batch_size: bool = True
    color_correction: str = "lab"
    temporal_overlap: int = 3
    prepend_frames: int = 0
    input_noise_scale: float = 0.0
    latent_noise_scale: float = 0.0
    blocks_to_swap: int = 24
    swap_io_components: bool = False
    dit_offload_device: str = "cpu"
    vae_offload_device: str = "cpu"
    upscaler_offload_device: str = "cpu"
    cpu_offload: bool = True
    tile_size: int = 512
    temporal_tile_size: int = 96
    encode_tiled: bool = True
    encode_tile_size: int = 1024
    encode_tile_overlap: int = 128
    decode_tiled: bool = True
    decode_tile_size: int = 768
    decode_tile_overlap: int = 128
    preserve_audio: bool = True
    filename_prefix: str = "Neo_Video_SeedVR2_Upscaled"
    profile_id: str | None = None
    dry_run: bool = True

    @property
    def block_swap(self) -> int:
        """Legacy compatibility alias used by the temporary V13 compiler/tests."""
        return self.blocks_to_swap

    @classmethod
    def from_payload(cls, payload: dict[str, Any] | None) -> "VideoSeedVR2UpscaleRequest":
        data = payload or {}
        vram_profile = _choice(data.get("vram_profile", data.get("upscale_vram_profile")), VRAM_PROFILES, "medium")
        profile_contract = seedvr2_vram_profile_contract(vram_profile)
        target_preset = _choice(data.get("target_preset", data.get("upscale_target_preset", profile_contract["target_preset"])), TARGET_PRESETS, profile_contract["target_preset"])
        raw_resolution = _target_resolution(data.get("resolution"), target_preset)
        resolution = min(raw_resolution, int(profile_contract["max_short_edge"]))
        manual_fps = _float_or_none(data.get("manual_output_fps"))
        raw_output_fps = _float_or_none(data.get("output_fps", data.get("fps")))
        if manual_fps is None and raw_output_fps is not None:
            manual_fps = raw_output_fps
        manual_fps = _clamp_float_or_none(manual_fps, 1.0, 240.0)
        requested_policy = _choice(data.get("output_fps_policy"), FPS_POLICIES, "preserve_source_fps")
        source_fps = _float_or_none(data.get("source_fps"))
        motion_timing_policy = _motion_timing_policy(data.get("motion_timing_policy"))
        motion_speed_multiplier = _clamp_float(data.get("motion_speed_multiplier", data.get("speed_multiplier")), 1.0, 0.25, 4.0)
        if motion_timing_policy != "motion_speed_repair":
            motion_speed_multiplier = 1.0
        finish_pipeline_preset = _finish_pipeline_preset(data.get("finish_pipeline_preset"))
        # Manual FPS is honored only as an explicit advanced override. No backend default-to-24.
        if manual_fps is not None and requested_policy == "manual_override":
            output_fps_policy = "manual_override"
            output_fps = manual_fps
        elif requested_policy == "motion_speed_repair" or motion_timing_policy == "motion_speed_repair":
            output_fps_policy = "motion_speed_repair"
            output_fps = round(source_fps * motion_speed_multiplier, 3) if source_fps and source_fps > 0 else None
        else:
            output_fps_policy = "preserve_source_fps"
            output_fps = None
        cpu_offload = _profile_bool_value(data, "cpu_offload", profile_contract, vram_profile)
        blocks_to_swap = _profile_clamped_int(
            data.get("blocks_to_swap", data.get("block_swap")),
            profile_contract,
            "blocks_to_swap",
            int(profile_contract["min_blocks_to_swap"]),
            int(profile_contract["max_blocks_to_swap"]),
        )
        batch_size = _profile_seedvr2_batch_size(data.get("batch_size"), profile_contract)
        dit_model = _profile_model_value(data.get("dit_model"), profile_contract["recommended_dit_model"])
        vae_model = _profile_model_value(data.get("vae_model"), profile_contract["recommended_vae_model"])
        return cls(
            request_schema_version=REQUEST_SCHEMA_VERSION,
            source_result_id=_nullable_string(data.get("source_result_id", data.get("parent_result_id"))),
            source_file_id=_nullable_string(data.get("source_file_id", data.get("file_id"))),
            source_video_path=_nullable_string(data.get("source_video_path", data.get("video_path"))),
            engine="seedvr2",
            vram_profile=vram_profile,
            target_preset=target_preset,
            resolution=resolution,
            max_resolution=_profile_max_resolution(data.get("max_resolution"), profile_contract, vram_profile),
            scale=_clamp_float(data.get("scale", data.get("upscale_scale")), 2.0, 1.0, 4.0),
            target_width=_profile_target_dimension(data.get("target_width", data.get("width")), profile_contract, target_preset),
            target_height=_profile_target_dimension(data.get("target_height", data.get("height")), profile_contract, target_preset),
            output_fps_policy=output_fps_policy,
            manual_output_fps=manual_fps,
            output_fps=output_fps,
            source_fps=source_fps,
            motion_timing_policy=motion_timing_policy,
            motion_speed_multiplier=motion_speed_multiplier,
            finish_pipeline_preset=finish_pipeline_preset,
            output_format=_choice(data.get("output_format"), OUTPUT_FORMATS, "auto"),
            seed=_int_value(data.get("seed"), 42),
            dit_model=dit_model,
            vae_model=vae_model,
            dit_device=_device_choice(data.get("dit_device", data.get("device")), "cuda:0"),
            vae_device=_device_choice(data.get("vae_device", data.get("device")), "cuda:0"),
            batch_size=batch_size,
            uniform_batch_size=_bool_value(data.get("uniform_batch_size"), True),
            color_correction=_choice(data.get("color_correction"), COLOR_CORRECTION_MODES, "lab"),
            temporal_overlap=_profile_clamped_int(data.get("temporal_overlap"), profile_contract, "temporal_overlap", 0, 16),
            prepend_frames=_clamp_int(data.get("prepend_frames"), 0, 0, 64),
            input_noise_scale=_clamp_float(data.get("input_noise_scale"), 0.0, 0.0, 1.0),
            latent_noise_scale=_clamp_float(data.get("latent_noise_scale"), 0.0, 0.0, 1.0),
            blocks_to_swap=blocks_to_swap,
            swap_io_components=_profile_bool_value(data, "swap_io_components", profile_contract, vram_profile),
            dit_offload_device=_profile_device_value(data, "dit_offload_device", profile_contract, vram_profile),
            vae_offload_device=_profile_device_value(data, "vae_offload_device", profile_contract, vram_profile),
            upscaler_offload_device=_profile_device_value(data, "upscaler_offload_device", profile_contract, vram_profile),
            cpu_offload=cpu_offload,
            tile_size=_clamp_int(data.get("tile_size"), 512, 128, 2048),
            temporal_tile_size=_clamp_int(data.get("temporal_tile_size", data.get("temporal_size")), 96, 1, 4096),
            encode_tiled=_profile_bool_value(data, "encode_tiled", profile_contract, vram_profile),
            encode_tile_size=_profile_clamped_int(data.get("encode_tile_size"), profile_contract, "encode_tile_size", 256, 2048),
            encode_tile_overlap=_profile_clamped_int(data.get("encode_tile_overlap"), profile_contract, "encode_tile_overlap", 0, 512),
            decode_tiled=_profile_bool_value(data, "decode_tiled", profile_contract, vram_profile),
            decode_tile_size=_profile_clamped_int(data.get("decode_tile_size"), profile_contract, "decode_tile_size", 256, 2048),
            decode_tile_overlap=_profile_clamped_int(data.get("decode_tile_overlap"), profile_contract, "decode_tile_overlap", 0, 512),
            preserve_audio=_bool_value(data.get("preserve_audio"), True),
            filename_prefix=_string_default(data.get("filename_prefix"), "Neo_Video_SeedVR2_Upscaled"),
            profile_id=_nullable_string(data.get("profile_id")),
            dry_run=_bool_value(data.get("dry_run"), True),
        )

    def payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["block_swap"] = self.blocks_to_swap
        payload["engine"] = "seedvr2"
        payload["upscale_engine"] = "seedvr2"
        payload["vram_profile_schema_version"] = VRAM_PROFILE_SCHEMA_VERSION
        payload["vram_profile_contract"] = seedvr2_vram_profile_contract(self.vram_profile)
        return payload

    def contract_payload(self) -> dict[str, Any]:
        return {
            "schema_version": REQUEST_SCHEMA_VERSION,
            "ui_schema_version": UI_SCHEMA_VERSION,
            "runtime_schema_preserved": SCHEMA_VERSION,
            "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
            "route_id": ROUTE_ID,
            "engine_policy": {
                "engine": "seedvr2",
                "engine_forced": True,
                "basic_fallback_allowed_in_request": False,
                "interpolation_request_allowed": False,
            },
            "source_policy": {
                "neo_owned_video_only": True,
                "creates_child_output": True,
                "generation_mount_allowed": False,
            },
            "fps_policy": {
                "default": "preserve_source_fps",
                "manual_override_requires_policy": "manual_override",
                "default_output_fps": None,
                "motion_speed_repair_policy": "motion_speed_repair",
                "motion_timing_schema_version": MOTION_TIMING_SCHEMA_VERSION,
            },
            "strip_interpolation_lock": upscale_interpolation_strip_lock_policy(),
            "vram_profile_policy": {
                "schema_version": VRAM_PROFILE_SCHEMA_VERSION,
                "active_profile": self.vram_profile,
                "custom_is_safety_clamped": True,
                "profiles": {profile_id: seedvr2_vram_profile_contract(profile_id) for profile_id in VRAM_PROFILES},
            },
            "normalized_request": self.payload(),
        }


# Compatibility name used by existing route/tests.
VideoUpscaleRequest = VideoSeedVR2UpscaleRequest


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _nullable_string(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _string_default(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    return text or fallback


def _choice(value: Any, allowed: tuple[str, ...], fallback: str) -> str:
    raw = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "720": "safe_720",
        "720p": "safe_720",
        "safe720": "safe_720",
        "safe_720p": "safe_720",
        "1080": "hd_1080",
        "1080p": "hd_1080",
        "hd1080": "hd_1080",
        "hd_1080p": "hd_1080",
        "manual": "custom",
        "preserve": "preserve_source_fps",
        "source": "preserve_source_fps",
        "manual": "manual_override" if "manual_override" in allowed else "custom",
        "manual_fps": "manual_override",
        "override": "manual_override",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in allowed else fallback


def _device_choice(value: Any, fallback: str) -> str:
    raw = str(value or "").strip().lower()
    if raw in OFFLOAD_DEVICES:
        return raw
    if raw in {"gpu", "cuda"}:
        return "cuda:0"
    if raw in {"false", "off", "disabled"}:
        return "none"
    return fallback



def _profile_model_value(value: Any, fallback: Any) -> str:
    text = str(value or "").strip()
    if not text or text.casefold() == "auto":
        return str(fallback or "auto")
    return text


def _profile_seedvr2_batch_size(value: Any, profile_contract: dict[str, Any]) -> int:
    parsed = _seedvr2_batch_size(value, int(profile_contract["batch_size"]))
    return max(1, min(parsed, int(profile_contract["max_batch_size"])))


def _profile_clamped_int(value: Any, profile_contract: dict[str, Any], key: str, low: int, high: int) -> int:
    fallback = int(profile_contract.get(key, 0))
    return _clamp_int(value, fallback, low, high)


def _profile_bool_value(data: dict[str, Any], key: str, profile_contract: dict[str, Any], profile_id: str) -> bool:
    if profile_id != "custom":
        return bool(profile_contract.get(key))
    return _bool_value(data.get(key), bool(profile_contract.get(key)))


def _profile_device_value(data: dict[str, Any], key: str, profile_contract: dict[str, Any], profile_id: str) -> str:
    fallback = str(profile_contract.get(key) or "cpu")
    if profile_id != "custom":
        return _device_choice(fallback, "cpu")
    return _device_choice(data.get(key, data.get("offload_device")), fallback)


def _profile_max_resolution(value: Any, profile_contract: dict[str, Any], profile_id: str) -> int:
    parsed = _clamp_int(value, 0, 0, 4320)
    if parsed <= 0:
        return 0
    return min(parsed, int(profile_contract["max_short_edge"])) if profile_id != "custom" else parsed


def _profile_target_dimension(value: Any, profile_contract: dict[str, Any], target_preset: str) -> int | None:
    if target_preset != "custom":
        return None
    parsed = _positive_int_or_none(value)
    if parsed is None:
        return None
    return min(parsed, int(profile_contract["max_short_edge"]))


def _bool_value(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value == "":
        return fallback
    if isinstance(value, (int, float)):
        return bool(value)
    raw = str(value).strip().lower()
    if raw in {"1", "true", "yes", "y", "on", "enabled"}:
        return True
    if raw in {"0", "false", "no", "n", "off", "disabled"}:
        return False
    return fallback


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_value(value: Any, fallback: float) -> float:
    parsed = _float_or_none(value)
    return fallback if parsed is None else parsed


def _int_value(value: Any, fallback: int) -> int:
    parsed = _int_or_none(value)
    return fallback if parsed is None else parsed


def _clamp_int(value: Any, fallback: int, low: int, high: int) -> int:
    return max(low, min(_int_value(value, fallback), high))


def _clamp_float(value: Any, fallback: float, low: float, high: float) -> float:
    return max(low, min(_float_value(value, fallback), high))


def _clamp_float_or_none(value: Any, low: float, high: float) -> float | None:
    parsed = _float_or_none(value)
    if parsed is None:
        return None
    return max(low, min(parsed, high))


def _motion_timing_policy(value: str | None) -> str:
    key = str(value or "same_duration").strip().lower().replace("-", "_")
    return key if key in MOTION_TIMING_POLICIES else "same_duration"


def _finish_pipeline_preset(value: str | None) -> str:
    key = str(value or "custom").strip().lower().replace("-", "_")
    return key if key in FINISH_PIPELINE_PRESETS else "custom"


def _positive_int_or_none(value: Any) -> int | None:
    parsed = _int_or_none(value)
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _target_resolution(value: Any, target_preset: str) -> int:
    parsed = _int_or_none(value)
    if parsed is not None and parsed > 0:
        return max(256, min(parsed, 4320))
    if target_preset == "safe_720":
        return 720
    return 1080


def _seedvr2_batch_size(value: Any, fallback: int) -> int:
    parsed = _clamp_int(value, fallback, 1, 65)
    if parsed <= 1:
        return 1
    # SeedVR2 temporal batching is safest on 4n+1 values: 1, 5, 9, 13...
    return max(1, min(65, 1 + (round((parsed - 1) / 4) * 4)))


def _normalize_node_name(value: Any) -> str:
    return str(value or "").casefold().replace(" ", "").replace("_", "").replace("-", "")


def upscale_interpolation_strip_lock_policy() -> dict[str, Any]:
    return {
        "schema_version": STRIP_INTERPOLATION_LOCK_SCHEMA_VERSION,
        "active": True,
        "scope": "video.finish.finish_upscale",
        "engine": "seedvr2",
        "interpolation_branch_allowed": False,
        "basic_fallback_branch_allowed": False,
        "denied_interpolation_patterns": list(INTERPOLATION_FORBIDDEN_NODE_PATTERNS),
        "denied_basic_fallback_patterns": list(BASIC_FALLBACK_FORBIDDEN_NODE_PATTERNS),
        "allowed_graph_nodes": [
            "LoadVideo",
            "GetVideoComponents",
            "SeedVR2LoadDiTModel",
            "SeedVR2LoadVAEModel",
            "SeedVR2VideoUpscaler",
            "CreateVideo",
            "SaveVideo",
        ],
    }


def scan_upscale_workflow_for_forbidden_nodes(workflow: dict[str, Any] | None) -> dict[str, Any]:
    forbidden: list[dict[str, str]] = []
    for node_id, node in (workflow or {}).items():
        if not isinstance(node, dict):
            continue
        class_type = str(node.get("class_type") or "")
        normalized = _normalize_node_name(class_type)
        matched = next((pattern for pattern in INTERPOLATION_FORBIDDEN_NODE_PATTERNS if pattern in normalized), "")
        matched_basic = next((pattern for pattern in BASIC_FALLBACK_FORBIDDEN_NODE_PATTERNS if pattern in normalized), "")
        if matched or matched_basic:
            forbidden.append({
                "node_id": str(node_id),
                "class_type": class_type,
                "matched_pattern": matched or matched_basic,
                "family": "interpolation" if matched else "basic_fallback",
            })
    return {
        "schema_version": STRIP_INTERPOLATION_LOCK_SCHEMA_VERSION,
        "active": True,
        "passed": not forbidden,
        "forbidden_nodes_found": forbidden,
        "interpolation_branch_included": any(item["family"] == "interpolation" for item in forbidden),
        "basic_fallback_included": any(item["family"] == "basic_fallback" for item in forbidden),
        "policy": upscale_interpolation_strip_lock_policy(),
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


def _available_node_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    inputs = entry.get("input", {}) if isinstance(entry, dict) else {}
    if not isinstance(inputs, dict):
        return {}
    merged: dict[str, Any] = {}
    for bucket_name in ("required", "optional"):
        bucket = inputs.get(bucket_name, {})
        if isinstance(bucket, dict):
            merged.update(bucket)
    return merged


def _node_outputs(object_info: dict[str, Any], class_type: str) -> list[str]:
    entry = object_info.get(class_type, {}) if isinstance(object_info, dict) else {}
    outputs = entry.get("output", []) if isinstance(entry, dict) else []
    if isinstance(outputs, (list, tuple)):
        return [str(item).upper() for item in outputs]
    if isinstance(outputs, dict):
        return [str(item).upper() for item in outputs.values()]
    return []


def _output_index(object_info: dict[str, Any], class_type: str, candidates: tuple[str, ...]) -> int | None:
    outputs = _node_outputs(object_info, class_type)
    wanted = tuple(str(item).upper() for item in candidates)
    for index, output_type in enumerate(outputs):
        if any(candidate in output_type for candidate in wanted):
            return index
    return None


def _output_ref(object_info: dict[str, Any], class_type: str, node_id: str, candidates: tuple[str, ...], fallback_index: int | None = None) -> list[Any] | None:
    index = _output_index(object_info, class_type, candidates)
    if index is None:
        index = fallback_index
    if index is None:
        return None
    return [node_id, index]


def _loader_emits_video_object(object_info: dict[str, Any], class_type: str) -> bool:
    outputs = _node_outputs(object_info, class_type)
    if not outputs:
        # Canonical Comfy core LoadVideo in the SeedVR2 sample emits a VIDEO object.
        # Path loaders from VideoHelperSuite commonly emit IMAGE frames instead.
        return not _is_path_video_loader(class_type)
    return any("VIDEO" in output and "IMAGE" not in output for output in outputs)


def _loader_emits_image_frames(object_info: dict[str, Any], class_type: str) -> bool:
    outputs = _node_outputs(object_info, class_type)
    if not outputs:
        return _is_path_video_loader(class_type)
    return any("IMAGE" in output for output in outputs)


def _source_is_direct_finish_upload(source: dict[str, Any]) -> bool:
    """Return true for lane-scoped uploaded source videos.

    Comfy installations are inconsistent here: some expose ``LoadVideo`` as a
    VIDEO-object loader, while VideoHelperSuite-style loaders may expose a
    same/similar class name that emits IMAGE frames. Direct Finish uploads live
    under ``neo_data/outputs/video/source`` and should not be forced through
    GetVideoComponents unless /object_info explicitly proves the loader emits a
    VIDEO object. This prevents IMAGE → GetVideoComponents.video validation
    failures.
    """
    candidates = (source.get("relative_path"), source.get("path"), source.get("source_video_path"))
    lane_upload_prefixes = ("upscale_", "interpolate_", "finish_")
    for value in candidates:
        raw = str(value or "")
        normalized = raw.replace("\\", "/").casefold()
        if "outputs/video/source/" not in normalized:
            continue
        filename = Path(raw.replace("\\", "/")).name.casefold()
        if filename.startswith(lane_upload_prefixes):
            return True
    return False


def _should_route_loader_direct_to_frames(object_info: dict[str, Any], class_type: str, source: dict[str, Any]) -> bool:
    outputs = _node_outputs(object_info, class_type)
    if any("IMAGE" in output for output in outputs):
        return True
    if any("VIDEO" in output and "IMAGE" not in output for output in outputs):
        return False
    # Ambiguous loader output. For direct Finish uploads, prefer the IMAGE-frame
    # path because this is the safer shape for VHS/path loaders and avoids
    # GetVideoComponents receiving IMAGE frames.
    return _source_is_direct_finish_upload(source)


def _source_fps_from_result(source_result_id: str | None) -> float | None:
    if not source_result_id:
        return None
    loaded = load_video_output_record(str(source_result_id))
    if not loaded.get("ok"):
        return None
    record = loaded.get("record") if isinstance(loaded.get("record"), dict) else {}
    candidates: list[Any] = []
    for bucket in (record.get("parameters"), record.get("replay_payload"), record.get("finish"), record.get("output_metadata", {}).get("finish") if isinstance(record.get("output_metadata"), dict) else None):
        if isinstance(bucket, dict):
            candidates.extend([bucket.get("fps"), bucket.get("source_fps"), bucket.get("output_fps")])
    for value in candidates:
        parsed = _float_or_none(value)
        if parsed and parsed > 0:
            return parsed
    return None




def _source_audio_from_result(source_result_id: str | None) -> bool | None:
    if not source_result_id:
        return None
    loaded = load_video_output_record(str(source_result_id))
    if not loaded.get("ok"):
        return None
    record = loaded.get("record") if isinstance(loaded.get("record"), dict) else {}
    candidates: list[Any] = []
    for bucket in (
        record.get("parameters"),
        record.get("replay_payload"),
        record.get("finish"),
        record.get("output_metadata", {}).get("finish") if isinstance(record.get("output_metadata"), dict) else None,
        record.get("media") if isinstance(record.get("media"), dict) else None,
        record.get("metadata") if isinstance(record.get("metadata"), dict) else None,
    ):
        if isinstance(bucket, dict):
            candidates.extend([
                bucket.get("has_audio"),
                bucket.get("contains_audio"),
                bucket.get("audio_present"),
                bucket.get("preserve_audio_effective"),
            ])
    for value in candidates:
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in {"true", "yes", "1", "audio"}:
            return True
        if isinstance(value, str) and value.strip().lower() in {"false", "no", "0", "none", "silent"}:
            return False
    return None


def _run_probe_command(args: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return None


def _video_file_has_audio_stream(path: Path | None) -> bool | None:
    """Return True/False when audio presence can be proven, otherwise None.

    SeedVR2/VHS can finish an upscale successfully and still fail during final save
    if Neo wires an AUDIO output from a silent video. We only preserve audio when
    the source is proven to contain at least one audio stream.
    """
    if path is None or not path.exists() or not path.is_file():
        return None
    ffprobe = shutil.which("ffprobe") or shutil.which("ffprobe.exe")
    if ffprobe:
        result = _run_probe_command([
            ffprobe,
            "-v", "error",
            "-select_streams", "a:0",
            "-show_entries", "stream=index,codec_type",
            "-of", "json",
            str(path),
        ])
        if result and result.returncode == 0:
            try:
                payload = json.loads(result.stdout or "{}")
                streams = payload.get("streams") if isinstance(payload, dict) else []
                if isinstance(streams, list):
                    return len(streams) > 0
            except json.JSONDecodeError:
                pass
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    if ffmpeg:
        result = _run_probe_command([ffmpeg, "-hide_banner", "-i", str(path)], timeout=8.0)
        if result:
            probe_text = f"{result.stdout or ''}\n{result.stderr or ''}".casefold()
            if "audio:" in probe_text or "stream #" in probe_text and " audio" in probe_text:
                return True
            if "video:" in probe_text and "audio:" not in probe_text:
                return False
    return None


def _effective_preserve_audio(req: VideoUpscaleRequest, source: dict[str, Any]) -> bool:
    if not req.preserve_audio:
        return False
    if source.get("has_audio") is False:
        return False
    # Unknown keeps legacy behavior for manually curated sources/tests, but real
    # Neo source uploads get probed in resolve_upscale_source and silent videos
    # are marked has_audio=False.
    return True

def _input_allowed(inputs: dict[str, Any], key: str) -> bool:
    # When /object_info is unavailable, Neo compiles against the canonical
    # SeedVR2 sample graph instead of stripping widget values.
    return not inputs or key in inputs


def _set_if_allowed(target: dict[str, Any], inputs: dict[str, Any], key: str, value: Any) -> None:
    if _input_allowed(inputs, key):
        target[key] = value


def _first_field(required: dict[str, Any], candidates: tuple[str, ...], fallback: str) -> str:
    folded = {key.casefold(): key for key in required.keys()}
    for candidate in candidates:
        found = folded.get(candidate.casefold())
        if found:
            return found
    return fallback


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


def resolve_upscale_source(req: VideoUpscaleRequest) -> dict[str, Any]:
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
            "error": "Upscale requires a Neo-owned source video. Select a Video result with an attached file or provide a path under neo_data/outputs/video.",
            "source_result_id": req.source_result_id or "",
            "source_file_id": req.source_file_id or "",
            "source_video_path": req.source_video_path or "",
        }
    metadata_audio = _source_audio_from_result(req.source_result_id)
    probed_audio = _video_file_has_audio_stream(source_path)
    has_audio = metadata_audio if metadata_audio is not None else probed_audio
    return {
        "ok": True,
        "kind": source_kind,
        "path": str(source_path),
        "filename": source_path.name,
        "relative_path": source_path.resolve().relative_to(ROOT_DIR.resolve()).as_posix(),
        "source_result_id": req.source_result_id or "",
        "source_file_id": req.source_file_id or "",
        "source_fps": req.output_fps or _source_fps_from_result(req.source_result_id),
        "has_audio": has_audio,
        "audio_probe": {
            "schema_version": "neo.video.finish.seedvr2_upscale.audio_probe.v25_9_19_phase_10l",
            "metadata_has_audio": metadata_audio,
            "file_has_audio": probed_audio,
            "effective_has_audio": has_audio,
        },
    }


def discover_upscale_bindings(object_info: dict[str, Any] | None = None, engine: str = "auto") -> dict[str, Any]:
    """Discover the concrete node names used by the SeedVR2 native compiler.

    Phase 7 keeps the runtime graph locked to SeedVR2-only upscale and strips
    optional interpolation/RIFE branches from the Finish Upscale compiler. Phase 6 moved the runtime graph away from the old V13 load→upscale→save
    placeholder and into the canonical SeedVR2 video path:
    LoadVideo → GetVideoComponents → SeedVR2 loaders → SeedVR2VideoUpscaler
    → CreateVideo → SaveVideo.
    """
    info = object_info or {}
    requested = str(engine or "auto").lower().replace("-", "_")
    requested_engine = requested if requested in {"auto", "seedvr2"} else "auto"
    load_video = _class_exists(info, "VHS_LoadVideoPath", "LoadVideoPath", "LoadVideoFromPath", "LoadVideo", "VHS_LoadVideo", "LoadVideoUpload") or "VHS_LoadVideoPath"
    get_components = _class_exists(info, "GetVideoComponents", "VHS_GetVideoComponents", "VideoComponents", "Get Video Components") or "GetVideoComponents"
    dit_loader = _class_exists(info, "SeedVR2LoadDiTModel", "SeedVR2LoadDITModel", "LoadSeedVR2DiTModel", "SeedVR2 DiT Loader") or "SeedVR2LoadDiTModel"
    vae_loader = _class_exists(info, "SeedVR2LoadVAEModel", "LoadSeedVR2VAEModel", "SeedVR2 VAE Loader") or "SeedVR2LoadVAEModel"
    seedvr2_upscaler = _class_exists(info, "SeedVR2VideoUpscaler", "SeedVR2Upscaler", "SeedVR2 Video Upscaler", "SeedVR2") or "SeedVR2VideoUpscaler"
    create_video = _class_exists(info, "CreateVideo", "VHS_CreateVideo", "VideoCreate") or "CreateVideo"
    save_video = _class_exists(info, "SaveVideo", "VHS_SaveVideo", "VideoSave", "VideoCombine", "VHS_VideoCombine") or "SaveVideo"
    torch_compile = _class_exists(info, "SeedVR2TorchCompileSettings", "SeedVR2 Torch Compile Settings")
    basic = _class_exists(info, "ImageScaleBy", "ImageScale", "ImageUpscaleWithModel", "UpscaleImageBy")
    classes = {
        "load_video": load_video,
        "get_components": get_components,
        "dit_loader": dit_loader,
        "vae_loader": vae_loader,
        "seedvr2_upscaler": seedvr2_upscaler,
        "upscaler": seedvr2_upscaler,  # compatibility alias for older tests/callers
        "create_video": create_video,
        "save_video": save_video,
        "saver": save_video,  # compatibility alias for older tests/callers
        "torch_compile": torch_compile or "SeedVR2TorchCompileSettings",
    }
    required_keys = ("load_video", "get_components", "dit_loader", "vae_loader", "seedvr2_upscaler", "create_video", "save_video")
    available = {key: classes[key] in info for key in required_keys}
    available["torch_compile"] = bool(torch_compile and torch_compile in info)
    available["all_required"] = all(bool(available.get(key)) for key in required_keys)
    return {
        "schema_version": WORKFLOW_SCHEMA_VERSION,
        "classes": classes,
        "required_class_keys": list(required_keys),
        "available": available,
        "engine": "seedvr2",
        "requested_engine": requested_engine,
        "blocked_requested_engine": requested if requested in {"basic", "image_scale", "interpolation", "rife", "vfi"} else "",
        "compiler": SEEDVR2_UPSCALE_COMPILER_ID,
        "graph_policy": {
            "uses_native_seedvr2_loaders": True,
            "uses_get_video_components": "conditional_video_object_only",
            "preserves_audio": True,
            "preserves_source_fps_by_default": True,
            "interpolation_branch_allowed": False,
            "basic_fallback_allowed": False,
            "interpolation_strip_lock_active": True,
        },
        "strip_interpolation_lock": upscale_interpolation_strip_lock_policy(),
        "basic_fallback_detected": bool(basic),
        "basic_fallback_allowed": False,
    }


def upscale_node_readiness(object_info: dict[str, Any] | None) -> dict[str, Any]:
    readiness = seedvr2_upscale_admin_readiness(object_info or {})
    bindings = discover_upscale_bindings(object_info or {})
    merged_bindings = dict(readiness.get("bindings") or {})
    merged_bindings.update(bindings)
    return {
        **readiness,
        "schema_version": SEEDVR2_UPSCALE_ADMIN_READINESS_SCHEMA,
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "bindings": merged_bindings,
        "strip_interpolation_lock": upscale_interpolation_strip_lock_policy(),
        "admin_readiness": readiness,
    }


def _is_path_video_loader(class_type: str) -> bool:
    lowered = str(class_type or "").casefold().replace("_", "").replace(" ", "")
    return "path" in lowered or "frompath" in lowered


def _seedvr2_video_source_value(source: dict[str, Any], class_type: str) -> str:
    if _is_path_video_loader(class_type):
        return str(source.get("path") or source.get("relative_path") or "")
    # Core/upload-style LoadVideo nodes validate against ComfyUI/input and reject
    # Neo ledger paths such as neo_data/outputs/video/source/*.mp4. Prefer a
    # Comfy-uploaded name when one exists; otherwise use the bare filename so the
    # validation error is honest and Admin can require a path loader for direct
    # Finish uploads. Runtime should choose a path loader whenever available.
    return str(source.get("comfy_video_name") or Path(str(source.get("path") or source.get("relative_path") or "")).name or source.get("relative_path") or "")


def _seedvr2_load_video_inputs(class_type: str, req: VideoUpscaleRequest, source: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    inputs = _available_node_inputs(object_info, class_type)
    if _is_path_video_loader(class_type):
        video_field = _first_field(inputs, ("video_path", "path", "file", "filename", "video"), "video_path")
    else:
        video_field = _first_field(inputs, ("file", "video", "filename", "video_path", "path"), "file")
    load_inputs: dict[str, Any] = {video_field: _seedvr2_video_source_value(source, class_type)}
    _set_if_allowed(load_inputs, inputs, "force_rate", req.output_fps or 0)
    _set_if_allowed(load_inputs, inputs, "custom_width", 0)
    _set_if_allowed(load_inputs, inputs, "custom_height", 0)
    _set_if_allowed(load_inputs, inputs, "frame_load_cap", 0)
    _set_if_allowed(load_inputs, inputs, "skip_first_frames", 0)
    _set_if_allowed(load_inputs, inputs, "select_every_nth", 1)
    return load_inputs


def _seedvr2_dit_loader_inputs(class_type: str, req: VideoUpscaleRequest, object_info: dict[str, Any]) -> dict[str, Any]:
    inputs = _available_node_inputs(object_info, class_type)
    data: dict[str, Any] = {}
    _set_if_allowed(data, inputs, "model", req.dit_model)
    _set_if_allowed(data, inputs, "device", req.dit_device)
    _set_if_allowed(data, inputs, "blocks_to_swap", req.blocks_to_swap)
    _set_if_allowed(data, inputs, "block_swap", req.blocks_to_swap)
    _set_if_allowed(data, inputs, "swap_io_components", req.swap_io_components)
    _set_if_allowed(data, inputs, "offload_device", req.dit_offload_device)
    _set_if_allowed(data, inputs, "cache_model", False)
    _set_if_allowed(data, inputs, "attention_mode", "sdpa")
    return data


def _seedvr2_vae_loader_inputs(class_type: str, req: VideoUpscaleRequest, object_info: dict[str, Any]) -> dict[str, Any]:
    inputs = _available_node_inputs(object_info, class_type)
    data: dict[str, Any] = {}
    _set_if_allowed(data, inputs, "model", req.vae_model)
    _set_if_allowed(data, inputs, "device", req.vae_device)
    _set_if_allowed(data, inputs, "encode_tiled", req.encode_tiled)
    _set_if_allowed(data, inputs, "encode_tile_size", req.encode_tile_size)
    _set_if_allowed(data, inputs, "encode_tile_overlap", req.encode_tile_overlap)
    _set_if_allowed(data, inputs, "decode_tiled", req.decode_tiled)
    _set_if_allowed(data, inputs, "decode_tile_size", req.decode_tile_size)
    _set_if_allowed(data, inputs, "decode_tile_overlap", req.decode_tile_overlap)
    _set_if_allowed(data, inputs, "tile_debug", "false")
    _set_if_allowed(data, inputs, "offload_device", req.vae_offload_device)
    _set_if_allowed(data, inputs, "cache_model", False)
    return data


def _seedvr2_upscaler_inputs(class_type: str, req: VideoUpscaleRequest, object_info: dict[str, Any], image_ref: list[Any]) -> dict[str, Any]:
    inputs = _available_node_inputs(object_info, class_type)
    image_field = _first_field(inputs, ("image", "images", "frames", "video", "input"), "image")
    data: dict[str, Any] = {image_field: image_ref}
    _set_if_allowed(data, inputs, "dit", ["3", 0])
    _set_if_allowed(data, inputs, "vae", ["4", 0])
    _set_if_allowed(data, inputs, "seed", req.seed)
    _set_if_allowed(data, inputs, "resolution", req.resolution)
    _set_if_allowed(data, inputs, "max_resolution", req.max_resolution)
    _set_if_allowed(data, inputs, "batch_size", req.batch_size)
    _set_if_allowed(data, inputs, "uniform_batch_size", req.uniform_batch_size)
    _set_if_allowed(data, inputs, "color_correction", req.color_correction)
    _set_if_allowed(data, inputs, "temporal_overlap", req.temporal_overlap)
    _set_if_allowed(data, inputs, "prepend_frames", req.prepend_frames)
    _set_if_allowed(data, inputs, "input_noise_scale", req.input_noise_scale)
    _set_if_allowed(data, inputs, "latent_noise_scale", req.latent_noise_scale)
    _set_if_allowed(data, inputs, "offload_device", req.upscaler_offload_device)
    _set_if_allowed(data, inputs, "enable_debug", False)
    return data



def _seedvr2_effective_fps(req: VideoUpscaleRequest, source: dict[str, Any] | None = None) -> float | None:
    if req.output_fps_policy == "manual_override" and req.output_fps:
        return req.output_fps
    if req.output_fps_policy == "motion_speed_repair":
        source_fps = req.source_fps or _float_or_none((source or {}).get("source_fps")) or _source_fps_from_result(req.source_result_id)
        if source_fps and source_fps > 0:
            return round(float(source_fps) * float(req.motion_speed_multiplier or 1.0), 3)
    return None

def _seedvr2_create_video_inputs(
    class_type: str,
    req: VideoUpscaleRequest,
    object_info: dict[str, Any],
    images_ref: list[Any],
    audio_ref: list[Any] | None,
    fps_ref: list[Any] | float | int | None,
) -> dict[str, Any]:
    inputs = _available_node_inputs(object_info, class_type)
    images_field = _first_field(inputs, ("images", "frames", "image"), "images")
    data: dict[str, Any] = {images_field: images_ref}
    if req.preserve_audio and audio_ref is not None:
        _set_if_allowed(data, inputs, "audio", audio_ref)
    if req.output_fps_policy == "manual_override" and req.output_fps:
        _set_if_allowed(data, inputs, "fps", req.output_fps)
    elif fps_ref is not None:
        _set_if_allowed(data, inputs, "fps", fps_ref)
    return data


def _seedvr2_save_video_inputs(class_type: str, req: VideoUpscaleRequest, prefix: str, object_info: dict[str, Any]) -> dict[str, Any]:
    inputs = _available_node_inputs(object_info, class_type)
    video_field = _first_field(inputs, ("video", "videos", "image", "images"), "video")
    data: dict[str, Any] = {video_field: ["6", 0]}
    _set_if_allowed(data, inputs, "filename_prefix", prefix)
    _set_if_allowed(data, inputs, "format", req.output_format if req.output_format != "auto" else "auto")
    _set_if_allowed(data, inputs, "codec", "auto")
    return data


def build_upscale_workflow(req: VideoUpscaleRequest, source: dict[str, Any], object_info: dict[str, Any] | None = None) -> dict[str, Any]:
    info = object_info or {}
    bindings = discover_upscale_bindings(info, engine=req.engine)
    classes = bindings["classes"]
    prefix = sanitize_path_part(req.filename_prefix, "Neo_Video_Upscaled")
    load_class = classes["load_video"]
    load_inputs = _seedvr2_load_video_inputs(load_class, req, source, info)
    force_direct_frames = _should_route_loader_direct_to_frames(info, load_class, source)
    loader_outputs_video = (not force_direct_frames) and _loader_emits_video_object(info, load_class)
    loader_outputs_image = force_direct_frames or _loader_emits_image_frames(info, load_class)
    motion_effective_fps = _seedvr2_effective_fps(req, source)
    preserve_audio_effective = _effective_preserve_audio(req, source)
    if loader_outputs_video:
        frames_ref: list[Any] = ["2", 0]
        audio_ref: list[Any] | None = ["2", 1] if preserve_audio_effective else None
        fps_ref: list[Any] | float | int | None = motion_effective_fps or ["2", 2]
        source_mode = "video_object_components"
        workflow = {
            "1": {"class_type": load_class, "inputs": load_inputs},
            "2": {"class_type": classes["get_components"], "inputs": {"video": ["1", 0]}},
            "3": {"class_type": classes["dit_loader"], "inputs": _seedvr2_dit_loader_inputs(classes["dit_loader"], req, info)},
            "4": {"class_type": classes["vae_loader"], "inputs": _seedvr2_vae_loader_inputs(classes["vae_loader"], req, info)},
            "5": {"class_type": classes["seedvr2_upscaler"], "inputs": _seedvr2_upscaler_inputs(classes["seedvr2_upscaler"], req, info, frames_ref)},
            "6": {"class_type": classes["create_video"], "inputs": _seedvr2_create_video_inputs(classes["create_video"], req, info, ["5", 0], audio_ref, fps_ref)},
            "7": {"class_type": classes["save_video"], "inputs": _seedvr2_save_video_inputs(classes["save_video"], req, prefix, info)},
        }
    else:
        frames_ref = _output_ref(info, load_class, "1", ("IMAGE", "IMAGES"), 0) or ["1", 0]
        audio_ref = _output_ref(info, load_class, "1", ("AUDIO",), None) if preserve_audio_effective else None
        fps_ref = _output_ref(info, load_class, "1", ("FLOAT", "FPS"), None)
        if fps_ref is None:
            fps_ref = motion_effective_fps or req.output_fps or source.get("source_fps") or 30
        source_mode = "image_frames_direct" if loader_outputs_image else "loader_direct_fallback"
        workflow = {
            "1": {"class_type": load_class, "inputs": load_inputs},
            "3": {"class_type": classes["dit_loader"], "inputs": _seedvr2_dit_loader_inputs(classes["dit_loader"], req, info)},
            "4": {"class_type": classes["vae_loader"], "inputs": _seedvr2_vae_loader_inputs(classes["vae_loader"], req, info)},
            "5": {"class_type": classes["seedvr2_upscaler"], "inputs": _seedvr2_upscaler_inputs(classes["seedvr2_upscaler"], req, info, frames_ref)},
            "6": {"class_type": classes["create_video"], "inputs": _seedvr2_create_video_inputs(classes["create_video"], req, info, ["5", 0], audio_ref, fps_ref)},
            "7": {"class_type": classes["save_video"], "inputs": _seedvr2_save_video_inputs(classes["save_video"], req, prefix, info)},
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "surface": "video",
        "phase": PHASE,
        "legacy_phase": LEGACY_PHASE,
        "route_id": ROUTE_ID,
        "compiled_at": _now(),
        "request_contract": req.contract_payload(),
        "compiler": {
            "id": SEEDVR2_UPSCALE_COMPILER_ID,
            "graph": "LoadVideo/PathFrames → conditional GetVideoComponents only for explicit VIDEO loaders → SeedVR2LoadDiTModel/SeedVR2LoadVAEModel → SeedVR2VideoUpscaler → CreateVideo → SaveVideo",
            "source_mode": source_mode,
            "forced_direct_frame_loader": bool(force_direct_frames),
            "interpolation_branch_included": False,
            "interpolation_strip_lock_active": True,
            "basic_fallback_included": False,
            "preserves_source_fps_by_default": req.output_fps_policy == "preserve_source_fps",
            "preserves_audio": preserve_audio_effective,
            "preserve_audio_requested": req.preserve_audio,
            "source_has_audio": source.get("has_audio"),
        },
        "parameters": {
            "engine": "seedvr2",
            "vram_profile": req.vram_profile,
            "vram_profile_schema_version": VRAM_PROFILE_SCHEMA_VERSION,
            "vram_profile_contract": seedvr2_vram_profile_contract(req.vram_profile),
            "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
            "target_preset": req.target_preset,
            "resolution": req.resolution,
            "max_resolution": req.max_resolution,
            "scale": req.scale,
            "target_width": req.target_width,
            "target_height": req.target_height,
            "output_fps_policy": req.output_fps_policy,
            "manual_output_fps": req.manual_output_fps,
            "output_fps": motion_effective_fps if req.output_fps_policy == "motion_speed_repair" else req.output_fps,
            "source_fps": req.source_fps or source.get("source_fps"),
            "motion_timing_schema_version": MOTION_TIMING_SCHEMA_VERSION,
            "pipeline_preset_schema_version": PIPELINE_PRESET_SCHEMA_VERSION,
            "motion_timing_policy": req.motion_timing_policy,
            "motion_speed_multiplier": req.motion_speed_multiplier,
            "finish_pipeline_preset": req.finish_pipeline_preset,
            "output_format": req.output_format,
            "seed": req.seed,
            "dit_model": req.dit_model,
            "vae_model": req.vae_model,
            "dit_device": req.dit_device,
            "vae_device": req.vae_device,
            "batch_size": req.batch_size,
            "uniform_batch_size": req.uniform_batch_size,
            "color_correction": req.color_correction,
            "temporal_overlap": req.temporal_overlap,
            "prepend_frames": req.prepend_frames,
            "input_noise_scale": req.input_noise_scale,
            "latent_noise_scale": req.latent_noise_scale,
            "blocks_to_swap": req.blocks_to_swap,
            "block_swap": req.blocks_to_swap,
            "swap_io_components": req.swap_io_components,
            "dit_offload_device": req.dit_offload_device,
            "vae_offload_device": req.vae_offload_device,
            "upscaler_offload_device": req.upscaler_offload_device,
            "cpu_offload": req.cpu_offload,
            "tile_size": req.tile_size,
            "temporal_tile_size": req.temporal_tile_size,
            "encode_tiled": req.encode_tiled,
            "encode_tile_size": req.encode_tile_size,
            "encode_tile_overlap": req.encode_tile_overlap,
            "decode_tiled": req.decode_tiled,
            "decode_tile_size": req.decode_tile_size,
            "decode_tile_overlap": req.decode_tile_overlap,
            "preserve_audio": req.preserve_audio,
            "preserve_audio_effective": preserve_audio_effective,
            "source_has_audio": source.get("has_audio"),
            "audio_probe": source.get("audio_probe"),
            "source_mode": source_mode,
        },
        "bindings": {**bindings, "source_mode": source_mode, "loader_outputs": _node_outputs(info, load_class)},
        "strip_interpolation_lock": scan_upscale_workflow_for_forbidden_nodes(workflow),
        "source": source,
        "workflow": workflow,
        "prompt_api_payload": {"prompt": workflow},
        "rules": [
            "V25.9.19 Phase 8 uses Admin-owned SeedVR2 Upscale readiness while preserving the Phase 7 no-interpolation compiler lock.",
            "The graph keeps interpolation out of the upscale lane and never includes RIFE/VFI nodes, math FPS nodes, primitive interpolation controls, or bypass switches.",
            "The source video must be a Neo-owned output under neo_data/outputs/video.",
            "SeedVR2 DiT and VAE loaders are separate graph nodes, fed into SeedVR2VideoUpscaler.",
            "Output FPS preserves the source FPS by linking GetVideoComponents.fps when the loader emits VIDEO, or using loader/source FPS when the loader emits IMAGE frames.",
            "Audio is preserved only when the selected loader exposes an AUDIO output, preserve_audio is true, and the source is proven to contain an audio stream.",
            "The parent result remains untouched; output files attach to a new upscale ledger record after refresh/import.",
        ],
    }


def build_seedvr2_upscale_output_metadata(compiled: dict[str, Any], req: VideoUpscaleRequest) -> dict[str, Any]:
    """Build the Phase 10 replay/memory metadata envelope for SeedVR2 upscale children.

    The upscale output is always a non-destructive child record. The parent video is
    used as source context only and is never overwritten or mutated.
    """
    parameters = compiled.get("parameters") if isinstance(compiled.get("parameters"), dict) else {}
    source = compiled.get("source") if isinstance(compiled.get("source"), dict) else {}
    node_readiness = compiled.get("node_readiness") if isinstance(compiled.get("node_readiness"), dict) else {}
    admin_readiness = node_readiness.get("admin_readiness") if isinstance(node_readiness.get("admin_readiness"), dict) else node_readiness
    bindings = compiled.get("bindings") if isinstance(compiled.get("bindings"), dict) else {}
    warnings = compiled.get("warnings") if isinstance(compiled.get("warnings"), list) else []
    strip_lock = compiled.get("strip_interpolation_lock") if isinstance(compiled.get("strip_interpolation_lock"), dict) else {}
    vram_contract = compiled.get("vram_profile_contract") if isinstance(compiled.get("vram_profile_contract"), dict) else seedvr2_vram_profile_contract(req.vram_profile)
    parent_result_id = str(req.source_result_id or source.get("source_result_id") or "")
    source_file_id = str(req.source_file_id or source.get("source_file_id") or "")
    source_video_path = str(source.get("relative_path") or source.get("path") or req.source_video_path or "")
    payload_params = {
        "engine": "seedvr2",
        "vram_profile": parameters.get("vram_profile") or req.vram_profile,
        "target_preset": parameters.get("target_preset") or req.target_preset,
        "resolution": parameters.get("resolution") if parameters.get("resolution") is not None else req.resolution,
        "max_resolution": parameters.get("max_resolution") if parameters.get("max_resolution") is not None else req.max_resolution,
        "scale": parameters.get("scale") if parameters.get("scale") is not None else req.scale,
        "target_width": parameters.get("target_width") if parameters.get("target_width") is not None else req.target_width,
        "target_height": parameters.get("target_height") if parameters.get("target_height") is not None else req.target_height,
        "output_fps_policy": parameters.get("output_fps_policy") or req.output_fps_policy,
        "manual_output_fps": parameters.get("manual_output_fps") if parameters.get("manual_output_fps") is not None else req.manual_output_fps,
        "output_fps": parameters.get("output_fps") if parameters.get("output_fps") is not None else req.output_fps,
        "source_fps": parameters.get("source_fps") if parameters.get("source_fps") is not None else req.source_fps,
        "motion_timing_policy": parameters.get("motion_timing_policy") or req.motion_timing_policy,
        "motion_speed_multiplier": parameters.get("motion_speed_multiplier") if parameters.get("motion_speed_multiplier") is not None else req.motion_speed_multiplier,
        "finish_pipeline_preset": parameters.get("finish_pipeline_preset") or req.finish_pipeline_preset,
        "preserve_source_fps": (parameters.get("output_fps_policy") or req.output_fps_policy) == "preserve_source_fps",
        "motion_speed_repair": (parameters.get("output_fps_policy") or req.output_fps_policy) == "motion_speed_repair",
        "output_format": parameters.get("output_format") or req.output_format,
        "seed": parameters.get("seed") if parameters.get("seed") is not None else req.seed,
        "dit_model": parameters.get("dit_model") or req.dit_model,
        "vae_model": parameters.get("vae_model") or req.vae_model,
        "batch_size": parameters.get("batch_size") if parameters.get("batch_size") is not None else req.batch_size,
        "uniform_batch_size": parameters.get("uniform_batch_size") if parameters.get("uniform_batch_size") is not None else req.uniform_batch_size,
        "color_correction": parameters.get("color_correction") or req.color_correction,
        "temporal_overlap": parameters.get("temporal_overlap") if parameters.get("temporal_overlap") is not None else req.temporal_overlap,
        "prepend_frames": parameters.get("prepend_frames") if parameters.get("prepend_frames") is not None else req.prepend_frames,
        "input_noise_scale": parameters.get("input_noise_scale") if parameters.get("input_noise_scale") is not None else req.input_noise_scale,
        "latent_noise_scale": parameters.get("latent_noise_scale") if parameters.get("latent_noise_scale") is not None else req.latent_noise_scale,
        "blocks_to_swap": parameters.get("blocks_to_swap") if parameters.get("blocks_to_swap") is not None else req.blocks_to_swap,
        "swap_io_components": parameters.get("swap_io_components") if parameters.get("swap_io_components") is not None else req.swap_io_components,
        "dit_offload_device": parameters.get("dit_offload_device") or req.dit_offload_device,
        "vae_offload_device": parameters.get("vae_offload_device") or req.vae_offload_device,
        "upscaler_offload_device": parameters.get("upscaler_offload_device") or req.upscaler_offload_device,
        "cpu_offload": parameters.get("cpu_offload") if parameters.get("cpu_offload") is not None else req.cpu_offload,
        "encode_tiled": parameters.get("encode_tiled") if parameters.get("encode_tiled") is not None else req.encode_tiled,
        "encode_tile_size": parameters.get("encode_tile_size") if parameters.get("encode_tile_size") is not None else req.encode_tile_size,
        "encode_tile_overlap": parameters.get("encode_tile_overlap") if parameters.get("encode_tile_overlap") is not None else req.encode_tile_overlap,
        "decode_tiled": parameters.get("decode_tiled") if parameters.get("decode_tiled") is not None else req.decode_tiled,
        "decode_tile_size": parameters.get("decode_tile_size") if parameters.get("decode_tile_size") is not None else req.decode_tile_size,
        "decode_tile_overlap": parameters.get("decode_tile_overlap") if parameters.get("decode_tile_overlap") is not None else req.decode_tile_overlap,
        "preserve_audio": parameters.get("preserve_audio") if parameters.get("preserve_audio") is not None else req.preserve_audio,
        "preserve_audio_effective": parameters.get("preserve_audio_effective"),
        "source_has_audio": parameters.get("source_has_audio"),
        "audio_probe": parameters.get("audio_probe"),
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "vram_profile_schema_version": VRAM_PROFILE_SCHEMA_VERSION,
    }
    payload_params = {key: value for key, value in payload_params.items() if value is not None}
    readiness_snapshot = {
        "schema_version": node_readiness.get("schema_version") or SEEDVR2_UPSCALE_ADMIN_READINESS_SCHEMA,
        "ready": bool(node_readiness.get("ready")),
        "admin_key": "finish_readiness.upscale",
        "top_level_key": "seedvr2_upscale_readiness",
        "missing_required": node_readiness.get("missing_required") if isinstance(node_readiness.get("missing_required"), list) else [],
        "missing_groups": admin_readiness.get("missing_groups") if isinstance(admin_readiness.get("missing_groups"), list) else [],
        "base_generation_blocked": False,
        "blocking_scope": "finish_upscale_only",
    }
    return {
        "schema_version": OUTPUT_METADATA_SCHEMA_VERSION,
        "phase": "V25.9.19 Phase 10",
        "extension_id": EXTENSION_ID,
        "finish_operation": FINISH_OPERATION_ID,
        "category": "upscale",
        "engine": "seedvr2",
        "non_destructive": True,
        "parent_result_unchanged": True,
        "child_result_only": True,
        "lineage": {
            "relationship": "child_of",
            "parent_result_id": parent_result_id,
            "source_result_id": parent_result_id,
            "source_file_id": source_file_id,
            "source_kind": source.get("kind") or "",
            "source_video_path": source_video_path,
            "child_category": "upscale",
            "child_operation": FINISH_OPERATION_ID,
            "parent_mutation_allowed": False,
        },
        "finish": {
            "operation": FINISH_OPERATION_ID,
            "extension_id": EXTENSION_ID,
            "engine": "seedvr2",
            "vram_profile": payload_params.get("vram_profile"),
            "target_preset": payload_params.get("target_preset"),
            "resolution": payload_params.get("resolution"),
            "max_resolution": payload_params.get("max_resolution"),
            "scale": payload_params.get("scale"),
            "seed": payload_params.get("seed"),
            "dit_model": payload_params.get("dit_model"),
            "vae_model": payload_params.get("vae_model"),
            "output_fps_policy": payload_params.get("output_fps_policy"),
            "manual_output_fps": payload_params.get("manual_output_fps"),
            "output_fps": payload_params.get("output_fps"),
            "source_fps": payload_params.get("source_fps"),
            "motion_timing_policy": payload_params.get("motion_timing_policy"),
            "motion_speed_multiplier": payload_params.get("motion_speed_multiplier"),
            "finish_pipeline_preset": payload_params.get("finish_pipeline_preset"),
            "preserve_source_fps": payload_params.get("preserve_source_fps"),
            "motion_speed_repair": payload_params.get("motion_speed_repair"),
            "preserve_audio": payload_params.get("preserve_audio"),
            "preserve_audio_effective": payload_params.get("preserve_audio_effective"),
            "source_has_audio": payload_params.get("source_has_audio"),
            "output_format": payload_params.get("output_format"),
            "batch_size": payload_params.get("batch_size"),
            "temporal_overlap": payload_params.get("temporal_overlap"),
            "color_correction": payload_params.get("color_correction"),
            "blocks_to_swap": payload_params.get("blocks_to_swap"),
            "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        },
        "extensions": {
            "used": [EXTENSION_ID],
            "payloads": {
                EXTENSION_ID: {
                    "schema_version": "neo.extension.payload.v1",
                    "enabled": True,
                    "version": "V25.9.19 Phase 10",
                    "stage": "finish",
                    "surface": "video",
                    "mount_slot": MOUNT_SLOT,
                    "operation": FINISH_OPERATION_ID,
                    "inputs": {
                        "source_result_id": parent_result_id,
                        "source_file_id": source_file_id,
                        "source_video_path": source_video_path,
                    },
                    "params": payload_params,
                    "metadata": {
                        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
                        "request_schema_version": REQUEST_SCHEMA_VERSION,
                        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
                        "vram_profile_schema_version": VRAM_PROFILE_SCHEMA_VERSION,
                        "output_metadata_schema": OUTPUT_METADATA_SCHEMA_VERSION,
                        "admin_readiness_schema_version": SEEDVR2_UPSCALE_ADMIN_READINESS_SCHEMA,
                        "strip_interpolation_lock_schema": STRIP_INTERPOLATION_LOCK_SCHEMA_VERSION,
                        "vram_profile_contract": vram_contract,
                        "warnings": list(warnings),
                    },
                }
            },
            "validation": {
                "seedvr2_ready": bool(node_readiness.get("ready")),
                "readiness_schema": node_readiness.get("schema_version") or SEEDVR2_UPSCALE_ADMIN_READINESS_SCHEMA,
                "readiness_snapshot": readiness_snapshot,
                "missing_required": readiness_snapshot["missing_required"],
                "missing_groups": readiness_snapshot["missing_groups"],
                "base_generation_blocked": False,
                "blocking_scope": "finish_upscale_only",
            },
            "workflow_patches": {
                "provider_graph_mutation": False,
                "post_generation_child_output": True,
                "compiler": SEEDVR2_UPSCALE_COMPILER_ID,
                "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
                "interpolation_branch_included": False,
                "basic_fallback_included": False,
            },
        },
        "replay_context": {
            "engine": "seedvr2",
            "vram_profile": payload_params.get("vram_profile"),
            "target_preset": payload_params.get("target_preset"),
            "resolution": payload_params.get("resolution"),
            "max_resolution": payload_params.get("max_resolution"),
            "dit_model": payload_params.get("dit_model"),
            "vae_model": payload_params.get("vae_model"),
            "batch_size": payload_params.get("batch_size"),
            "blocks_to_swap": payload_params.get("blocks_to_swap"),
            "output_fps_policy": payload_params.get("output_fps_policy"),
            "preserve_source_fps": payload_params.get("preserve_source_fps"),
            "preserve_audio": payload_params.get("preserve_audio"),
            "readiness_snapshot": readiness_snapshot,
            "strip_interpolation_lock": strip_lock,
            "bindings": bindings,
        },
        "memory_event": {
            "event_type": "extension_workflow_used",
            "namespace": f"extension:{EXTENSION_ID}",
            "surface": "video",
            "route_id": ROUTE_ID,
            "operation": FINISH_OPERATION_ID,
            "parent_result_id": parent_result_id,
            "source_result_id": parent_result_id,
            "source_file_id": source_file_id,
            "engine": "seedvr2",
            "vram_profile": payload_params.get("vram_profile"),
            "target_preset": payload_params.get("target_preset"),
            "resolution": payload_params.get("resolution"),
            "dit_model": payload_params.get("dit_model"),
            "vae_model": payload_params.get("vae_model"),
            "readiness_ready": bool(node_readiness.get("ready")),
        },
    }


def _post_json(base_url: str, endpoint: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    raw = json.dumps(payload).encode("utf-8")
    req = Request(url, data=raw, headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "NeoStudioVideoUpscale/1.0"}, method="POST")
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local user-configured Comfy URL.
        data = response.read().decode("utf-8", errors="replace")
    parsed = json.loads(data) if data else {}
    return parsed if isinstance(parsed, dict) else {"value": parsed}


def _persist_finish_record(result: dict[str, Any], req: VideoUpscaleRequest) -> dict[str, Any]:
    output_metadata = result.get("output_metadata") if isinstance(result.get("output_metadata"), dict) else build_seedvr2_upscale_output_metadata(result, req)
    lineage = output_metadata.get("lineage") if isinstance(output_metadata.get("lineage"), dict) else {}
    request_payload = {
        **req.payload(),
        "family": "finish",
        "loader": "external_nodes",
        "generation_type": "upscale",
        "prompt": "Video Finish: SeedVR2 upscale",
        "vram_profile_schema_version": VRAM_PROFILE_SCHEMA_VERSION,
        "vram_profile_contract": seedvr2_vram_profile_contract(req.vram_profile),
        "workflow_schema_version": WORKFLOW_SCHEMA_VERSION,
        "negative_prompt": "",
        "strip_interpolation_lock_schema_version": STRIP_INTERPOLATION_LOCK_SCHEMA_VERSION,
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
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
        return {**result, "neo_persisted": {"ok": False, "error": f"Video upscale ledger write failed: {exc}"}}
    return {**result, "result_id": ledger.get("result_id", ""), "neo_persisted": ledger}


def video_upscale_compile_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None) -> dict[str, Any]:
    req = VideoUpscaleRequest.from_payload(payload)
    source = resolve_upscale_source(req)
    if not source.get("ok"):
        return {"ok": False, "queued": False, "dry_run": True, "schema_version": SCHEMA_VERSION, "workflow_schema_version": WORKFLOW_SCHEMA_VERSION, "request_schema_version": REQUEST_SCHEMA_VERSION, "phase": PHASE, "legacy_phase": LEGACY_PHASE, "surface": "video", "route_id": ROUTE_ID, "error": source.get("error"), "source": source, "request": req.payload(), "request_contract": req.contract_payload()}
    profile = video_backend_profile_payload(req.profile_id)
    base_url = profile["connection"]["base_url"]
    warnings: list[str] = []
    object_info: dict[str, Any] = object_info_override or {}
    if object_info_override is None:
        try:
            object_info = _get_json(base_url, "/object_info", 2.5)
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            warnings.append(f"Compiled with fallback upscale bindings because ComfyUI /object_info was unavailable: {exc}")
            object_info = {}
    readiness = upscale_node_readiness(object_info)
    compiled = build_upscale_workflow(req, source, object_info=object_info)
    metadata_dir = get_video_output_paths("metadata", create=True).output_dir
    output_paths = get_video_output_paths("upscale", create=True)
    sidecar_name = f"{sanitize_path_part(req.filename_prefix, 'video_upscale')}_{datetime.now().strftime('%Y%m%d_%H%M%S')}_compile.json"
    sidecar_path = metadata_dir / sidecar_name
    strip_lock = scan_upscale_workflow_for_forbidden_nodes(compiled.get("workflow", {}))
    sidecar_payload = {**compiled, "request": req.payload(), "backend_profile": profile, "warnings": warnings, "node_readiness": readiness, "workflow_schema_version": WORKFLOW_SCHEMA_VERSION, "strip_interpolation_lock": strip_lock, "vram_profile_contract": seedvr2_vram_profile_contract(req.vram_profile)}
    sidecar_payload["output_metadata"] = build_seedvr2_upscale_output_metadata(sidecar_payload, req)
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


def video_upscale_generate_payload(payload: dict[str, Any] | None = None, object_info_override: dict[str, Any] | None = None, timeout: float = 5.0) -> dict[str, Any]:
    req = VideoUpscaleRequest.from_payload(payload)
    compile_payload = video_upscale_compile_payload({**req.payload(), "dry_run": True}, object_info_override=object_info_override)
    if not compile_payload.get("ok"):
        return compile_payload
    if req.dry_run:
        return compile_payload
    if object_info_override is not None and not compile_payload.get("node_readiness", {}).get("ready"):
        return {**compile_payload, "ok": False, "queued": False, "error": "V25.9.19 Phase 8 upscale is missing required ComfyUI video I/O, SeedVR2 native compiler nodes, or SeedVR2 model catalogs."}
    backend = compile_payload.get("backend") or {}
    base_url = backend.get("base_url") or video_backend_profile_payload(req.profile_id)["connection"]["base_url"]
    try:
        queue_response = _post_json(base_url, "/prompt", compile_payload["prompt_api_payload"], timeout)
    except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {**compile_payload, "ok": False, "queued": False, "error": f"ComfyUI upscale queue failed: {exc}"}
    response_payload = {
        **compile_payload,
        "ok": True,
        "queued": True,
        "dry_run": False,
        "queue_response": queue_response,
        "prompt_id": queue_response.get("prompt_id") or queue_response.get("node_id") or "",
    }
    return _persist_finish_record(response_payload, req)
