from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Final

from neo_app.video.performance_profiles import PERFORMANCE_PROFILES, normalize_video_performance_profile
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader


@dataclass(frozen=True)
class VideoParameterField:
    field_id: str
    label: str
    section: str
    value_type: str
    default: Any
    minimum: float | int | None = None
    maximum: float | int | None = None
    step: float | int | None = None
    unit: str = ""
    editable: bool = True
    route_scope: tuple[str, ...] = ("all",)
    description: str = ""

    def payload(self) -> dict:
        return asdict(self)


VIDEO_VRAM_PROFILES: Final[dict[str, dict[str, Any]]] = {
    "low": {
        "id": "low",
        "label": "Low VRAM Draft",
        "target": "8-12GB",
        "intent": "fastest safe proof pass; short clips before quality work",
        "constraints": {"max_width": 832, "max_height": 480, "max_short_side": 480, "max_long_side": 832, "max_frames": 49, "max_steps": 20, "batch_count": 1, "tiled_decode": True, "temporal_tiling": True},
        "notes": ["Prefer WAN 2.2 5B for the first usable low-VRAM compiler.", "Keep outputs short and upscale/interpolate later instead of starting huge."],
    },
    "balanced": {
        "id": "balanced",
        "label": "Balanced",
        "target": "12GB",
        "intent": "default working mode for WAN/LTX tests",
        "constraints": {"max_width": 960, "max_height": 544, "max_short_side": 544, "max_long_side": 960, "max_frames": 97, "max_steps": 30, "batch_count": 1, "tiled_decode": True, "temporal_tiling": True},
        "notes": ["Good default for your current build style: useful previews without reckless VRAM usage."],
    },
    "quality": {
        "id": "quality",
        "label": "Quality",
        "target": "16GB+",
        "intent": "slower clips with more frames or higher resolution",
        "constraints": {"max_width": 1280, "max_height": 704, "max_short_side": 704, "max_long_side": 1280, "max_frames": 121, "max_steps": 40, "batch_count": 1, "tiled_decode": True, "temporal_tiling": True},
        "notes": ["Still guarded. Long videos should be segmented, not forced into one giant run."],
    },
    "manual": {
        "id": "manual",
        "label": "Manual / Experimental",
        "target": "advanced",
        "intent": "expert overrides for later compiler testing",
        "constraints": {"max_width": 2048, "max_height": 1152, "max_short_side": 1152, "max_long_side": 2048, "max_frames": 241, "max_steps": 80, "batch_count": 1, "tiled_decode": True, "temporal_tiling": True},
        "notes": ["Neo should still warn before dangerous jobs; manual does not mean unguarded."],
    },
}

BASE_FIELDS: Final[tuple[VideoParameterField, ...]] = (
    VideoParameterField("vram_profile", "VRAM Profile", "profile", "select", "balanced", description="Safe performance profile that suggests defaults and risk notes. User-entered generation values stay editable and are guarded at queue time."),
    VideoParameterField("performance_profile", "Performance Profile", "performance", "select", "safe_12gb", description="Shared Video optimizer profile for WAN/LTX low-VRAM strategy."),
    VideoParameterField("enable_sage_attention", "Sage Attention", "performance", "boolean", False, route_scope=("wan22", "ltx23"), description="Enable the V-G11 Sage Attention model patch where the selected route supports it."),
    VideoParameterField("sage_attention_mode", "Sage Mode", "performance", "select", "auto", route_scope=("wan22", "ltx23"), description="Sage Attention mode from live Comfy/KJNodes /object_info discovery."),
    VideoParameterField("sage_attention_target", "Sage Target", "performance", "select", "both", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Apply Sage Attention to the high-noise branch, low-noise branch, or both."),
    VideoParameterField("enable_teacache", "TeaCache", "performance", "boolean", False, route_scope=("wan22", "ltx23"), description="Enable the V-G12 TeaCache model cache patch where the selected route supports it."),
    VideoParameterField("teacache_profile", "TeaCache Profile", "performance", "select", "conservative", route_scope=("wan22", "ltx23"), description="Conservative, balanced, or aggressive cache profile for the active V-G12 TeaCache adapter."),
    VideoParameterField("teacache_target", "TeaCache Target", "performance", "select", "both", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Apply TeaCache to the high-noise branch, low-noise branch, or both."),
    VideoParameterField("enable_cpu_offload", "CPU Offload", "performance", "boolean", False, route_scope=("wan22", "ltx23"), description="Enable V-G13 CPU/offload stability path where the selected route exposes compatible nodes."),
    VideoParameterField("enable_vae_offload", "VAE Offload", "performance", "boolean", False, route_scope=("wan22", "ltx23"), description="Enable V-G13 tiled/offload VAE decode where supported."),
    VideoParameterField("enable_block_swap", "Block Swap", "performance", "boolean", False, route_scope=("wan22", "ltx23"), description="Enable V-G13 route-specific block swap where supported."),
    VideoParameterField("block_swap_target", "Block Swap Target", "performance", "select", "both", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Apply block swap/offload to high-noise branch, low-noise branch, or both."),
    VideoParameterField("block_swap_blocks", "Blocks to Swap", "performance", "int", 12, 0, 99, 1, description="Number of model blocks to swap/offload when the active node supports it."),
    VideoParameterField("enable_torch_compile", "Torch Compile", "performance", "boolean", False, route_scope=("wan22", "ltx23"), description="Advanced experimental speed intent; disabled until validated per route."),
    VideoParameterField("auto_resolve_vace_dimensions", "Auto Resolve VACE Size", "performance", "boolean", True, route_scope=("wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"), description="Automatically snap invalid Rapid AIO VACE width/height down to safe multiples of 16 and report why Neo changed them."),
    VideoParameterField("output_format", "Output Format", "output", "select", "webm", description="Final format request. WEBM is the first safe output; MP4/frames are later lanes."),
    VideoParameterField("width", "Width", "size", "int", 832, 256, 2048, 16, "px", description="Route-safe generation width."),
    VideoParameterField("height", "Height", "size", "int", 480, 256, 1152, 16, "px", description="Route-safe generation height."),
    VideoParameterField("frames", "Frames", "timing", "int", 41, 1, 241, 1, "frames", description="Total frames generated before interpolation/upscale."),
    VideoParameterField("fps", "FPS", "timing", "float", 16, 1, 60, 1, "fps", description="User-facing frame rate. LTX routes also mirror this into int/float backend nodes."),
    VideoParameterField("steps", "Steps", "sampling", "int", 20, 1, 80, 1, description="Sampling step count constrained by VRAM profile."),
    VideoParameterField("guidance", "Guidance / CFG", "sampling", "float", 5, 0, 20, 0.1, description="Guidance/CFG value used by the selected route."),
    VideoParameterField("split_step", "Split Step", "sampling", "int", 6, 1, 80, 1, route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="WAN dual-noise high/low handoff step. LightX2V recommends 2 for 4-step runs; user edits are preserved."),
    VideoParameterField("seed", "Seed", "sampling", "int", -1, -1, 999999999999999, 1, description="-1 means randomize later; fixed seeds become replayable metadata."),
    VideoParameterField("sampler", "Sampler", "sampling", "select", "uni_pc", description="Route-derived sampler."),
    VideoParameterField("scheduler", "Scheduler", "sampling", "select", "simple", description="Route-derived scheduler/sigma policy."),
    VideoParameterField("batch_count", "Batch Count", "output", "int", 1, 1, 1, 1, description="Locked to 1 for video until queue batching is explicitly built."),
    VideoParameterField("high_noise_model", "High-Noise GGUF", "models", "backend_select", "", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="WAN 2.2 high-noise GGUF from live ComfyUI /object_info discovery."),
    VideoParameterField("low_noise_model", "Low-Noise GGUF", "models", "backend_select", "", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="WAN 2.2 low-noise GGUF from live ComfyUI /object_info discovery."),
    VideoParameterField("rapid_aio_model", "Rapid AIO GGUF Model", "models", "backend_select", "", route_scope=("wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"), description="WAN 2.2 Rapid AIO GGUF model from live ComfyUI /object_info discovery. No hardcoded filenames."),
    VideoParameterField("rapid_aio_text_encoder", "Text Encoder", "models", "backend_select", "provider_default", route_scope=("wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"), description="Auto or detected WAN text encoder from live ComfyUI/Admin catalogs."),
    VideoParameterField("rapid_aio_vae", "VAE", "models", "backend_select", "provider_default", route_scope=("wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"), description="Auto or detected WAN VAE from live ComfyUI/Admin catalogs."),
    VideoParameterField("enable_video_lora", "Enable Video LoRA", "lora", "boolean", False, route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Enable a model-only video LoRA branch for WAN high/low/both model paths."),
    VideoParameterField("video_lora_mode", "Video LoRA Mode", "lora", "select", "off", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Off, normal video LoRA, or LightX2V 4-step mode."),
    VideoParameterField("video_lora_model", "Video LoRA", "lora", "backend_select", "", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Normal Video LoRA from live ComfyUI LoRA catalog."),
    VideoParameterField("video_lora_strength", "Video LoRA Strength", "lora", "float", 0.8, -2, 2, 0.05, route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Model strength for normal Video LoRA."),
    VideoParameterField("video_lora_target", "Video LoRA Target", "lora", "select", "both", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Apply normal LoRA to high branch, low branch, or both."),
    VideoParameterField("enable_lightx2v", "LightX2V 4-Step", "lora", "boolean", False, route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Enables paired high/low LightX2V LoRAs. 4 steps / CFG 1.0 / split 2 are recommendations, not forced when user edits are present."),
    VideoParameterField("high_noise_lora", "High-Noise LightX2V LoRA", "lora", "backend_select", "", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="High-noise LightX2V LoRA from live ComfyUI LoRA catalog."),
    VideoParameterField("low_noise_lora", "Low-Noise LightX2V LoRA", "lora", "backend_select", "", route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Low-noise LightX2V LoRA from live ComfyUI LoRA catalog."),
    VideoParameterField("high_noise_lora_strength", "High LightX2V Strength", "lora", "float", 1.0, -2, 2, 0.05, route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Model strength for high-noise LightX2V LoRA."),
    VideoParameterField("low_noise_lora_strength", "Low LightX2V Strength", "lora", "float", 1.0, -2, 2, 0.05, route_scope=("wan22.gguf.img2vid_14b_dual_noise",), description="Model strength for low-noise LightX2V LoRA."),
    VideoParameterField("decode_mode", "Decode Mode", "decode", "select", "tiled", description="Decode strategy; tiled stays default for low/mid VRAM."),
    VideoParameterField("tile_size", "Tile Size", "decode", "int", 512, 128, 1024, 64, "px", description="Spatial tile size for tiled decode where supported."),
    VideoParameterField("temporal_tile_size", "Temporal Tile Size", "decode", "int", 4096, 1, 4096, 1, description="Temporal decode chunk size where supported."),
)

IMG2VID_FIELDS: Final[tuple[VideoParameterField, ...]] = (
    VideoParameterField("source_image", "Source Image", "source", "file", "", route_scope=("img2vid",), description="Required for Img2Vid routes; upload wiring comes in Phase V6."),
    VideoParameterField("image_strength", "Image Strength", "source", "float", 0.7, 0, 1, 0.05, route_scope=("img2vid",), description="How strongly the source image guides the video route."),
    VideoParameterField("resize_mode", "Resize Mode", "source", "select", "fit_crop", route_scope=("img2vid",), description="How Neo will adapt source images to route dimensions."),
)

LTX_FIELDS: Final[tuple[VideoParameterField, ...]] = (
    VideoParameterField("fps_sync", "LTX FPS Sync", "ltx", "boolean", True, route_scope=("ltx23",), description="One UI FPS value patches both int and float LTX frame-rate nodes."),
    VideoParameterField("audio_latent", "Audio Latent", "ltx", "boolean", False, route_scope=("ltx23",), description="Reserved for LTX audio-capable workflows; silent video remains first."),
    VideoParameterField("spatial_upscaler", "Spatial Upscaler", "ltx", "boolean", True, route_scope=("ltx23",), description="LTX latent spatial upscaler toggle where the workflow supports it."),
    VideoParameterField("chunk_feed_forward", "Chunk Feed-Forward", "ltx", "int", 2, 1, 8, 1, route_scope=("ltx23",), description="Low-VRAM LTX chunk helper."),
    VideoParameterField("tiled_vae_decode", "Tiled VAE Decode", "ltx", "boolean", True, route_scope=("ltx23",), description="Keeps LTX decode safer on mid/low VRAM."),
)

FIELD_ORDER: Final[tuple[str, ...]] = ("profile", "performance", "models", "lora", "size", "timing", "sampling", "source", "decode", "ltx", "output")

# Phase 10y: Rapid AIO uses the same production speed/VRAM controls as the working WAN route.
# Hide only true dual-branch controls: high/low model selection, branch targets, split-step,
# and dual-noise LoRA/LightX2V fields.
WAN_DUAL_NOISE_ONLY_FIELD_IDS: Final[frozenset[str]] = frozenset({
    "sage_attention_target",
    "teacache_target",
    "block_swap_target",
    "split_step",
    "high_noise_model",
    "low_noise_model",
    "enable_video_lora",
    "video_lora_mode",
    "video_lora_model",
    "video_lora_strength",
    "video_lora_target",
    "enable_lightx2v",
    "high_noise_lora",
    "low_noise_lora",
    "high_noise_lora_strength",
    "low_noise_lora_strength",
})

RAPID_AIO_ROUTE_IDS: Final[frozenset[str]] = frozenset({
    "wan22.rapid_aio_gguf.txt2vid",
    "wan22.rapid_aio_gguf.img2vid",
})

ROUTE_DEFAULTS: Final[dict[str, dict[str, Any]]] = {
    "wan22.unet.txt2vid": {"width": 832, "height": 480, "frames": 41, "fps": 16, "steps": 20, "guidance": 5, "sampler": "uni_pc", "scheduler": "simple", "decode_mode": "standard", "tile_size": 512},
    "wan22.unet.img2vid": {"width": 832, "height": 480, "frames": 41, "fps": 16, "steps": 20, "guidance": 5, "sampler": "uni_pc", "scheduler": "simple", "source_image_required": True, "image_strength": 0.7, "decode_mode": "standard"},
    "wan22.gguf.img2vid_14b_dual_noise": {"width": 640, "height": 360, "frames": 49, "fps": 16, "steps": 12, "guidance": 3.5, "split_step": 6, "first_test_preset": {"width": 512, "height": 288, "frames": 25, "fps": 12, "steps": 4, "guidance": 1.0, "split_step": 2}, "sampler": "euler", "scheduler": "simple", "source_image_required": True, "image_strength": 0.7, "resize_mode": "fit_crop", "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "dual_noise_models_required": True, "high_noise_model": "", "low_noise_model": "", "dual_noise_mapping": "explicit_high_low_pair", "template": "wan22_i2v14_dual_noise_native", "enable_video_lora": False, "video_lora_mode": "off", "video_lora_strength": 0.8, "video_lora_target": "both", "enable_lightx2v": False, "high_noise_lora_strength": 1.0, "low_noise_lora_strength": 1.0},
    "wan22.rapid_aio_gguf.txt2vid": {"width": 480, "height": 848, "frames": 121, "fps": 16, "steps": 4, "guidance": 1.0, "sampler": "euler", "scheduler": "simple", "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "rapid_aio_model": "", "rapid_aio_text_encoder": "provider_default", "rapid_aio_vae": "provider_default", "performance_profile": "safe_12gb", "auto_resolve_vace_dimensions": True, "enable_sage_attention": False, "sage_attention_mode": "auto", "enable_teacache": False, "teacache_profile": "conservative", "enable_cpu_offload": False, "enable_vae_offload": False, "enable_block_swap": False, "block_swap_blocks": 12},
    "wan22.rapid_aio_gguf.img2vid": {"width": 480, "height": 848, "frames": 121, "fps": 16, "steps": 4, "guidance": 1.0, "sampler": "euler", "scheduler": "simple", "source_image_required": True, "image_strength": 0.7, "resize_mode": "fit_crop", "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "rapid_aio_model": "", "rapid_aio_text_encoder": "provider_default", "rapid_aio_vae": "provider_default", "performance_profile": "safe_12gb", "auto_resolve_vace_dimensions": True, "enable_sage_attention": False, "sage_attention_mode": "auto", "enable_teacache": False, "teacache_profile": "conservative", "enable_cpu_offload": False, "enable_vae_offload": False, "enable_block_swap": False, "block_swap_blocks": 12},
    "ltx23.gguf.txt2vid": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.img2vid": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_image_required": True, "image_strength": 0.7, "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.txt2vid": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.img2vid": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_image_required": True, "image_strength": 0.7, "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.first_last_frame": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "first_image_required": True, "last_image_required": True, "transition_strength": 0.75, "first_strength": 0.75, "last_strength": 0.75, "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.first_last_frame": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "first_image_required": True, "last_image_required": True, "transition_strength": 0.75, "first_strength": 0.75, "last_strength": 0.75, "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.multiscene": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "segment_images_required": True, "segment_count_min": 2, "segment_count_max": 4, "image_strength": 0.7, "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.multiscene": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "segment_images_required": True, "segment_count_min": 2, "segment_count_max": 4, "image_strength": 0.7, "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.extend": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_video_required": True, "continuation_strength": 0.75, "extraction_mode": "last_frame", "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.extend": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_video_required": True, "continuation_strength": 0.75, "extraction_mode": "last_frame", "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.vid2vid": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_video_required": True, "denoise_strength": 0.45, "motion_strength": 0.85, "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.vid2vid": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_video_required": True, "denoise_strength": 0.45, "motion_strength": 0.85, "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.prompt_schedule": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "prompt_events_required": True, "motion_events_supported": True, "schedule_mode": "metadata_guided", "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.prompt_schedule": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "prompt_events_required": True, "motion_events_supported": True, "schedule_mode": "metadata_guided", "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.depth_motion": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_video_required": True, "control_type": "depth", "control_strength": 0.65, "motion_strength": 0.8, "depth_engine": "auto", "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.depth_motion": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "source_video_required": True, "control_type": "depth", "control_strength": 0.65, "motion_strength": 0.8, "depth_engine": "auto", "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.gguf.audio_video": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "audio_prompt_required": True, "audio_latent": True, "audio_mode": "prompted", "audio_strength": 0.75, "sync_strength": 0.6, "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
    "ltx23.unet.audio_video": {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "audio_prompt_required": True, "audio_latent": True, "audio_mode": "prompted", "audio_strength": 0.75, "sync_strength": 0.6, "decode_mode": "tiled", "tile_size": 512, "temporal_tile_size": 4096, "tiled_vae_decode": True, "chunk_feed_forward": 2},
}

VRAM_OVERRIDES: Final[dict[str, dict[str, Any]]] = {
    "low": {"width": 832, "height": 480, "frames": 41, "fps": 12, "steps": 16, "batch_count": 1, "decode_mode": "tiled", "tile_size": 384, "temporal_tile_size": 4096},
    "balanced": {"batch_count": 1, "decode_mode": "tiled"},
    "quality": {"frames": 97, "fps": 24, "steps": 30, "batch_count": 1, "decode_mode": "tiled"},
    "manual": {"batch_count": 1},
}


def normalize_video_vram_profile(value: str | None) -> str:
    key = str(value or "balanced").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {"low_vram": "low", "draft": "low", "balanced_vram": "balanced", "default": "balanced", "quality_vram": "quality", "high": "quality", "experimental": "manual"}
    return aliases.get(key, key if key in VIDEO_VRAM_PROFILES else "balanced")


def _field_in_scope(field: VideoParameterField, family: str, generation_type: str, route_id: str = "") -> bool:
    if route_id in RAPID_AIO_ROUTE_IDS and field.field_id in WAN_DUAL_NOISE_ONLY_FIELD_IDS:
        return False
    scope = set(field.route_scope)
    return "all" in scope or family in scope or generation_type in scope or route_id in scope


def _clamp(defaults: dict[str, Any], profile: dict[str, Any]) -> dict[str, Any]:
    values = deepcopy(defaults)
    constraints = profile.get("constraints", {})
    if "max_width" in constraints and values.get("width", 0) > constraints["max_width"]:
        values["width"] = constraints["max_width"]
    if "max_height" in constraints and values.get("height", 0) > constraints["max_height"]:
        values["height"] = constraints["max_height"]
    if "max_frames" in constraints and values.get("frames", 0) > constraints["max_frames"]:
        values["frames"] = constraints["max_frames"]
    if "max_steps" in constraints and values.get("steps", 0) > constraints["max_steps"]:
        values["steps"] = constraints["max_steps"]
    values["batch_count"] = min(int(values.get("batch_count", 1) or 1), int(constraints.get("batch_count", 1) or 1))
    if constraints.get("tiled_decode"):
        values.setdefault("decode_mode", "tiled")
    if constraints.get("temporal_tiling"):
        values.setdefault("temporal_tile_size", 4096)
    return values


def video_parameter_profile_payload(family: str | None = None, loader: str | None = None, generation_type: str | None = None, vram_profile: str | None = None) -> dict:
    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    nt = normalize_video_generation_type(generation_type)
    vp_id = normalize_video_vram_profile(vram_profile)
    profile = VIDEO_VRAM_PROFILES[vp_id]
    route = find_video_route(nf, nl, nt, include_planned=True)
    route_id = route.route_id if route else "wan22.unet.txt2vid"

    defaults: dict[str, Any] = {field.field_id: field.default for field in BASE_FIELDS}
    defaults.update(ROUTE_DEFAULTS.get(route_id, {}))
    perf_id = normalize_video_performance_profile(defaults.get("performance_profile"))
    perf_defaults = dict(PERFORMANCE_PROFILES[perf_id].defaults)
    defaults.update(perf_defaults)
    defaults.update(VRAM_OVERRIDES.get(vp_id, {}))
    defaults["performance_profile"] = perf_id
    # WAN 2.2 14B GGUF is registered for 12GB testing; keep its route-specific draft
    # budget below the generic WAN low-VRAM ceiling until the native compiler lands.
    if route_id == "wan22.gguf.img2vid_14b_dual_noise" and vp_id == "low":
        defaults.update(ROUTE_DEFAULTS.get(route_id, {}))
    defaults["vram_profile"] = vp_id
    defaults["output_format"] = "webm"
    defaults = _clamp(defaults, profile)

    fields = [field.payload() for field in (*BASE_FIELDS, *IMG2VID_FIELDS, *LTX_FIELDS) if _field_in_scope(field, nf, nt, route_id)]
    for field in fields:
        fid = field["field_id"]
        if fid in defaults:
            field["default"] = defaults[fid]
        if fid == "batch_count" and "batch_count" in profile["constraints"]:
            field["maximum"] = profile["constraints"]["batch_count"]
        if fid in {"width", "height", "frames", "steps"}:
            field["profile_recommended_maximum"] = profile["constraints"].get("max_long_side" if fid in {"width", "height"} else {"frames": "max_frames", "steps": "max_steps"}[fid])
            field["profile_limit_policy"] = "soft_warning_only_user_editable"
        if fid in {"sampler", "scheduler"}:
            field["editable"] = False

    sections: dict[str, list[str]] = {section: [] for section in FIELD_ORDER}
    for field in fields:
        sections.setdefault(field["section"], []).append(field["field_id"])

    warnings: list[str] = []
    if not route:
        warnings.append("No compatible route exists; parameter defaults fall back to WAN 2.2 UNET Txt2Vid.")
    if nt == "img2vid":
        warnings.append("Img2Vid source upload is active for WAN/LTX image-guided routes.")
    if route_id == "wan22.gguf.img2vid_14b_dual_noise":
        warnings.append("V-G13 Performance Layer can insert Sage Attention, TeaCache, and low-VRAM offload/block-swap for WAN GGUF.")
    if route_id in {"wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"}:
        warnings.append("V25.9.19-10y Rapid AIO GGUF uses a production route with true single-model I2V output and single-model Sage/TeaCache/low-VRAM speed controls.")
    if nt == "first_last_frame":
        warnings.append("First/Last Frame requires two Video source images and is active for LTX 2.3 routes in V15.")
    if nt == "multiscene":
        warnings.append("MultiScene requires 2-4 Video source images and is active for LTX 2.3 routes in V16.")
    if nt == "extend":
        warnings.append("Extend requires a Neo-owned source video and is active for LTX 2.3 routes in V17.")
    if nt == "vid2vid":
        warnings.append("Video-to-Video requires a Neo-owned source video and is active for LTX 2.3 routes in V18.")
    if nt == "prompt_schedule":
        warnings.append("Prompt/Motion Schedule requires at least one prompt or motion beat and is active for LTX 2.3 routes in V20.")
    if nt == "depth_motion":
        warnings.append("Depth / Motion Control requires a Neo-owned source video and external depth/motion nodes; it is active for LTX 2.3 routes in V19.")
    if nt == "audio_video":
        warnings.append("Audio-Video requires at least one audio prompt, dialogue prompt, or soundscape prompt and is active for LTX 2.3 routes in V21.")
    if nf == "ltx23":
        warnings.append("LTX routes mirror FPS into int/float backend frame-rate nodes and default to tiled decode.")
    if vp_id == "manual":
        warnings.append("Manual profile is for expert testing only; compiler phases should still preflight VRAM risk.")

    return {
        "schema_version": "neo.video.parameter_profile.v3",
        "surface": "video",
        "phase": "V3",
        "route": route.payload() if route else None,
        "request": {"family": nf, "loader": nl, "generation_type": nt, "vram_profile": vp_id},
        "vram_profile": profile,
        "sections": sections,
        "fields": fields,
        "defaults": defaults,
        "warnings": warnings,
        "rules": [
            "Parameter values are route-derived before they become compiler inputs.",
            "VRAM profile constrains width, height, frames, steps, batch, and decode behavior.",
            "Img2Vid-only parameters appear only for image-to-video routes.",
            "LTX-only parameters appear only for LTX 2.3 routes.",
            "Phase V10 VRAM Profile Engine constrains compiler inputs before queueing.",
            "Phase V-G13 Performance Adapter Layer exposes shared optimizer intent, active Sage Attention, WAN TeaCache, and WAN low-VRAM mutation across WAN/LTX-capable routes.",
            "Phase V15 First/Last Frame uses two image sources for LTX transition generation.",
            "Phase V16 MultiScene uses 2-4 image segment sources for LTX sequence generation.",
            "Phase V17 Extend uses a Neo-owned source video and extracts a continuation frame for LTX generation.",
            "Phase V21 Audio-Video records audio prompt, dialogue, soundscape, and sync metadata for LTX generation.",
        ],
    }
