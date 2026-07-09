from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Final


@dataclass(frozen=True)
class VideoRouteOption:
    id: str
    label: str
    status: str = "active"
    description: str = ""
    aliases: tuple[str, ...] = ()

    def payload(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class VideoRoute:
    route_id: str
    family: str
    loader: str
    generation_type: str
    status: str
    compiler_phase: str
    recommended_first: bool = False
    notes: tuple[str, ...] = ()
    parameter_profile: dict = field(default_factory=dict)
    requires: tuple[str, ...] = ()

    def payload(self) -> dict:
        data = asdict(self)
        data["enabled"] = self.status == "enabled"
        return data


VIDEO_MODEL_FAMILIES: Final[tuple[VideoRouteOption, ...]] = (
    VideoRouteOption("wan22", "WAN 2.2", "active", "Primary low/mid-VRAM foundation route for the first video compiler.", ("wan", "wan2.2", "wan_2_2")),
    VideoRouteOption("ltx23", "LTX 2.3", "active", "Advanced cinematic route with GGUF/safetensors and future audio/multiscene support.", ("ltx", "ltx2.3", "ltx_2_3")),
    VideoRouteOption("hunyuan_video", "HunyuanVideo", "future", "Registry slot only; not selectable until a route contract exists."),
    VideoRouteOption("mochi", "Mochi", "future", "Registry slot only; not selectable until a route contract exists."),
    VideoRouteOption("cogvideox", "CogVideoX", "future", "Registry slot only; not selectable until a route contract exists."),
    VideoRouteOption("animatediff", "AnimateDiff", "future", "Motion/legacy animation lane; external-node route later."),
    VideoRouteOption("svd", "Stable Video Diffusion", "future", "Image-to-video registry slot only."),
)

VIDEO_LOADERS: Final[tuple[VideoRouteOption, ...]] = (
    VideoRouteOption("unet", "UNET / Diffusion", "active", "Transformer/UNET safetensors loaded through ComfyUI diffusion/UNET style loaders.", ("diffusion", "diffusion_model", "safetensors")),
    VideoRouteOption("gguf", "GGUF", "active", "Quantized model route for lower-VRAM LTX/WAN-compatible workflows where available."),
    VideoRouteOption("rapid_aio_gguf", "WAN Rapid AIO GGUF", "active", "Production route for Phr00t-style WAN 2.2 Rapid All-in-One GGUF checkpoints with dynamic Comfy catalogs.", ("gguf_rapid_aio", "rapid_gguf", "wan_rapid_aio", "wan22_rapid_aio_gguf")),
    VideoRouteOption("checkpoint", "Checkpoint", "future", "Only valid where a true checkpoint-style video loader exists."),
    VideoRouteOption("native_workflow", "Native Workflow", "active", "Imported ComfyUI workflow template with Neo field mapping."),
)

VIDEO_GENERATION_TYPES: Final[tuple[VideoRouteOption, ...]] = (
    VideoRouteOption("txt2vid", "Txt2Vid", "active", "Text prompt to video." , ("text_to_video", "t2v")),
    VideoRouteOption("img2vid", "Img2Vid", "active", "Source image plus prompt to video.", ("image_to_video", "i2v")),
    VideoRouteOption("first_last_frame", "First / Last Frame", "active", "Controlled transition between two images using LTX dual image guides.", ("first_last", "first_last_frame_video", "start_end", "start_end_frame")),
    VideoRouteOption("multiscene", "Multi-Image / MultiScene", "active", "Segmented LTX image-guided video route using 2-4 source images.", ("multi_scene", "multi_image", "multi_image_video")),
    VideoRouteOption("extend", "Extend", "active", "Continue an existing Neo-owned video output using the final frame as the continuation guide.", ("video_extend", "continue", "continue_video")),
    VideoRouteOption("vid2vid", "Video-to-Video", "active", "Source video restyle/cleanup route using a Neo-owned source video.", ("video_to_video", "v2v", "restyle_video")),
    VideoRouteOption("depth_motion", "Depth / Motion Control", "active", "Controlled video generation using depth or motion preprocessors.", ("depth_control", "motion_control", "control_video")),
    VideoRouteOption("prompt_schedule", "Prompt / Motion Schedule", "active", "Timeline-style prompt and motion beats for LTX scheduled generation.", ("prompt_scheduling", "motion_schedule", "schedule", "scheduled")),
    VideoRouteOption("audio_video", "Audio-Video", "active", "LTX audio-video generation with audio prompt, dialogue, soundscape, and sync metadata.", ("audio", "audio_visual", "audiovideo")),
)

# Enabled means visible/selectable in Neo Studio V2. Planned means known but guarded for a future compiler update.
VIDEO_ROUTES: Final[tuple[VideoRoute, ...]] = (
    VideoRoute(
        "wan22.unet.txt2vid",
        "wan22",
        "unet",
        "txt2vid",
        "enabled",
        "V5",
        True,
        ("Best first compiler target for low/mid VRAM.", "Uses UNET/Diffusion safetensors route and conservative default parameters."),
        {"width": 832, "height": 480, "frames": 41, "fps": 16, "steps": 20, "guidance": 5, "sampler": "uni_pc", "scheduler": "simple", "vram_profile": "balanced"},
        ("UNETLoader", "CLIPLoader", "VAELoader", "Wan22ImageToVideoLatent", "KSampler", "VAEDecode"),
    ),
    VideoRoute(
        "wan22.unet.img2vid",
        "wan22",
        "unet",
        "img2vid",
        "enabled",
        "V6",
        False,
        ("Same WAN route with a connected source image latent.", "Source image upload and LoadImage connection are active in V6."),
        {"width": 832, "height": 480, "frames": 41, "fps": 16, "steps": 20, "guidance": 5, "sampler": "uni_pc", "scheduler": "simple", "vram_profile": "balanced", "source_image_required": True},
        ("UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "Wan22ImageToVideoLatent", "KSampler", "VAEDecode"),
    ),
    VideoRoute(
        "wan22.rapid_aio_gguf.txt2vid",
        "wan22",
        "rapid_aio_gguf",
        "txt2vid",
        "experimental",
        "experimental",
        False,
        (
            "Production route for WAN 2.2 Rapid AIO GGUF dynamic model catalog discovery.",
            "No hardcoded model names; model, text encoder, and VAE choices come from live ComfyUI object_info.",
            "Uses the Rapid AIO MEGA native VACE route split with automatic dimension snap for invalid VACE sizes.",
        ),
        {
            "width": 480,
            "height": 848,
            "frames": 121,
            "fps": 16,
            "steps": 4,
            "guidance": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "vram_profile": "balanced",
            "rapid_aio_frame_mode": "text_only",
            "decode_mode": "tiled",
            "tile_size": 384,
            "temporal_tile_size": 4096,
            "rapid_aio_model": "",
            "rapid_aio_text_encoder": "provider_default",
            "rapid_aio_vae": "provider_default",
            "performance_profile": "safe_12gb",
            "auto_resolve_vace_dimensions": True,
            "enable_sage_attention": False,
            "sage_attention_mode": "auto",
            "enable_teacache": False,
            "teacache_profile": "conservative",
            "enable_cpu_offload": False,
            "enable_vae_offload": False,
            "enable_block_swap": False,
            "block_swap_blocks": 12,
        },
        ("UnetLoaderGGUF", "UNETLoaderGGUF", "GGUFLoader", "WanVideoModelLoaderGGUF", "CLIPLoader", "VAELoader"),
    ),
    VideoRoute(
        "wan22.rapid_aio_gguf.img2vid",
        "wan22",
        "rapid_aio_gguf",
        "img2vid",
        "experimental",
        "experimental",
        False,
        (
            "Production Img2Vid route for WAN 2.2 Rapid AIO GGUF MEGA.",
            "Image2Video Source Mode supports start frame, start + end frame, and end frame only.",
            "Native VACE dimensions are auto-snapped to safe multiples of 16 when Auto Resolve VACE Size is enabled.",
        ),
        {
            "width": 480,
            "height": 848,
            "frames": 121,
            "fps": 16,
            "steps": 4,
            "guidance": 1.0,
            "sampler": "euler",
            "scheduler": "simple",
            "vram_profile": "balanced",
            "source_image_required": True,
            "rapid_aio_frame_mode": "start_frame",
            "rapid_aio_frame_modes": ("start_frame", "start_end_frame", "end_frame"),
            "image_strength": 0.7,
            "resize_mode": "fit_crop",
            "decode_mode": "tiled",
            "tile_size": 384,
            "temporal_tile_size": 4096,
            "rapid_aio_model": "",
            "rapid_aio_text_encoder": "provider_default",
            "rapid_aio_vae": "provider_default",
            "performance_profile": "safe_12gb",
            "auto_resolve_vace_dimensions": True,
            "enable_sage_attention": False,
            "sage_attention_mode": "auto",
            "enable_teacache": False,
            "teacache_profile": "conservative",
            "enable_cpu_offload": False,
            "enable_vae_offload": False,
            "enable_block_swap": False,
            "block_swap_blocks": 12,
        },
        ("UnetLoaderGGUF", "UNETLoaderGGUF", "GGUFLoader", "WanVideoModelLoaderGGUF", "CLIPLoader", "VAELoader", "LoadImage", "WanImageToVideo", "ModelSamplingSD3", "KSamplerAdvanced", "VAEDecode", "CreateVideo", "SaveVideo"),
    ),
    VideoRoute(
        "wan22.gguf.img2vid_14b_dual_noise",
        "wan22",
        "gguf",
        "img2vid",
        "enabled",
        "V-G13",
        False,
        (
            "GGUF-first WAN 2.2 14B Img2Vid route for 12GB VRAM testing.",
            "Uses the uploaded dual-noise WanImageToVideo workflow shape with explicit high/low GGUF model mapping.",
            "V-G13 activates low-VRAM block swap / CPU offload and tiled VAE decode after Sage/TeaCache.",
        ),
        {
            "width": 640,
            "height": 360,
            "frames": 49,
            "fps": 16,
            "steps": 12,
            "guidance": 3.5,
            "split_step": 6,
            "sampler": "euler",
            "scheduler": "simple",
            "vram_profile": "low",
            "source_image_required": True,
            "dual_noise_models_required": True,
            "high_noise_model_required": True,
            "low_noise_model_required": True,
            "decode_mode": "tiled",
            "tile_size": 384,
            "template": "wan22_i2v14_dual_noise_native",
            "high_noise_model": "auto_high_noise",
            "low_noise_model": "auto_low_noise",
            "dual_noise_mapping": "explicit_high_low_pair",
            "video_lora_supported": True,
            "lightx2v_4step_supported": True,
            "video_lora_mode": "off",
            "video_lora_target": "both",
            "performance_profile": "safe_12gb",
            "enable_sage_attention": False,
            "sage_attention_mode": "auto",
            "sage_attention_target": "both",
            "enable_teacache": False,
            "teacache_profile": "conservative",
            "teacache_target": "both",
            "enable_cpu_offload": False,
            "enable_vae_offload": False,
            "enable_block_swap": False,
            "enable_torch_compile": False,
        },
        (
            "UnetLoaderGGUF", "UNETLoaderGGUF", "GGUFLoader", "CLIPLoader", "VAELoader", "LoadImage",
            "WanImageToVideo", "ModelSamplingSD3", "KSamplerAdvanced", "VAEDecode", "CreateVideo", "SaveVideo", "CLIPTextEncode",
        ),
    ),
    VideoRoute(
        "wan22.native_workflow.txt2vid",
        "wan22",
        "native_workflow",
        "txt2vid",
        "planned",
        "V5+",
        False,
        ("Native workflow template import is known but not the first compiler path.",),
    ),
    VideoRoute(
        "wan22.native_workflow.img2vid",
        "wan22",
        "native_workflow",
        "img2vid",
        "planned",
        "V6+",
        False,
        ("Native workflow template import is known but not the first compiler path.",),
    ),
    VideoRoute(
        "ltx23.gguf.txt2vid",
        "ltx23",
        "gguf",
        "txt2vid",
        "enabled",
        "V8",
        False,
        ("Advanced low-VRAM LTX route using GGUF/text-encoder connector patterns.", "Compiler remains guarded until this LTX route is ready."),
        {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "vram_profile": "balanced", "tiled_decode": True},
        ("DualCLIPLoaderGGUF", "LTXVConditioning", "EmptyLTXVLatentVideo", "LTXVScheduler", "VAEDecodeTiled"),
    ),
    VideoRoute(
        "ltx23.gguf.img2vid",
        "ltx23",
        "gguf",
        "img2vid",
        "enabled",
        "V9",
        False,
        ("Advanced LTX image-guided route.", "Needs source image controls, FPS sync, guide/crop handling, and tiled decode."),
        {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "sampler": "euler_ancestral", "scheduler": "ltxv", "vram_profile": "balanced", "source_image_required": True, "tiled_decode": True},
        ("DualCLIPLoaderGGUF", "LTXVAddGuide", "LTXVCropGuides", "VAEDecodeTiled"),
    ),
    VideoRoute("ltx23.unet.txt2vid", "ltx23", "unet", "txt2vid", "enabled", "V8", False, ("Safetensors diffusion/UNET LTX path. Useful where GGUF is not selected.",), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "tiled_decode": True}),
    VideoRoute("ltx23.unet.img2vid", "ltx23", "unet", "img2vid", "enabled", "V9", False, ("Safetensors diffusion/UNET LTX image-guided path.",), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "source_image_required": True, "tiled_decode": True}),
    VideoRoute("ltx23.native_workflow.txt2vid", "ltx23", "native_workflow", "txt2vid", "planned", "V8+", False, ("Native LTX workflow template mapping later.",)),
    VideoRoute("ltx23.native_workflow.img2vid", "ltx23", "native_workflow", "img2vid", "planned", "V9+", False, ("Native LTX workflow template mapping later.",)),
    VideoRoute("ltx23.gguf.first_last_frame", "ltx23", "gguf", "first_last_frame", "enabled", "V15", False, ("Controlled transition between two Video source images using LTXVAddGuideMulti.", "Requires first_image and last_image."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "first_image_required": True, "last_image_required": True, "tiled_decode": True}, ("DualCLIPLoaderGGUF", "LTXVAddGuideMulti", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.unet.first_last_frame", "ltx23", "unet", "first_last_frame", "enabled", "V15", False, ("Controlled transition between two Video source images using LTXVAddGuideMulti.", "Uses LTX UNET/Diffusion safetensors route."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "first_image_required": True, "last_image_required": True, "tiled_decode": True}, ("DualCLIPLoader", "LTXVAddGuideMulti", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.gguf.multiscene", "ltx23", "gguf", "multiscene", "enabled", "V16", False, ("Segment cards, image guides, frame math, and LTXVAddGuideMulti are active in V16.", "Requires at least two segment images."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "segment_images_required": True, "segment_count_min": 2, "segment_count_max": 4, "tiled_decode": True}, ("DualCLIPLoaderGGUF", "LTXVAddGuideMulti", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.unet.multiscene", "ltx23", "unet", "multiscene", "enabled", "V16", False, ("Safetensors LTX MultiScene path with 2-4 image guides.", "Requires at least two segment images."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "segment_images_required": True, "segment_count_min": 2, "segment_count_max": 4, "tiled_decode": True}, ("DualCLIPLoader", "LTXVAddGuideMulti", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.gguf.extend", "ltx23", "gguf", "extend", "enabled", "V17", False, ("Extends an existing Neo-owned video by extracting the last frame as an LTX continuation guide.", "Creates a child output under the extend category; parent video is untouched."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "source_video_required": True, "continuation_strength": 0.75, "extraction_mode": "last_frame", "tiled_decode": True}, ("VHS_LoadVideo", "GetImageFromBatch", "DualCLIPLoaderGGUF", "LTXVAddGuide", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.unet.extend", "ltx23", "unet", "extend", "enabled", "V17", False, ("Safetensors LTX Extend path using a source video final-frame guide.", "Creates a child output under the extend category; parent video is untouched."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "source_video_required": True, "continuation_strength": 0.75, "extraction_mode": "last_frame", "tiled_decode": True}, ("VHS_LoadVideo", "GetImageFromBatch", "DualCLIPLoader", "LTXVAddGuide", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.gguf.vid2vid", "ltx23", "gguf", "vid2vid", "enabled", "V18", False, ("Restyles or cleans up a Neo-owned source video using encoded source latents.", "Creates a non-destructive child output under the vid2vid category."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "source_video_required": True, "denoise_strength": 0.45, "motion_strength": 0.85, "tiled_decode": True}, ("VHS_LoadVideo", "VAEEncodeTiled", "DualCLIPLoaderGGUF", "LTXVConditioning", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.unet.vid2vid", "ltx23", "unet", "vid2vid", "enabled", "V18", False, ("Safetensors LTX Video-to-Video path using encoded source latents.", "Creates a non-destructive child output under the vid2vid category."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "source_video_required": True, "denoise_strength": 0.45, "motion_strength": 0.85, "tiled_decode": True}, ("VHS_LoadVideo", "VAEEncodeTiled", "DualCLIPLoader", "LTXVConditioning", "LTXVCropGuides", "VAEDecodeTiled")),

    VideoRoute("ltx23.gguf.depth_motion", "ltx23", "gguf", "depth_motion", "enabled", "V19", False, ("Depth / Motion Control route using external depth preprocessors and LTX guide attachment.", "Requires a Neo-owned source video and depth/motion nodes such as DepthAnythingV2 or DepthCrafter."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "source_video_required": True, "control_type": "depth", "control_strength": 0.65, "motion_strength": 0.8, "tiled_decode": True}, ("VHS_LoadVideo", "GetImageFromBatch", "DepthAnythingV2", "DepthCrafter", "LTXVAddGuide", "LTXVCropGuides", "VAEDecodeTiled")),
    VideoRoute("ltx23.unet.depth_motion", "ltx23", "unet", "depth_motion", "enabled", "V19", False, ("Safetensors LTX Depth / Motion Control route using selected result video as guidance source.", "Requires a Neo-owned source video and depth/motion nodes such as DepthAnythingV2 or DepthCrafter."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "source_video_required": True, "control_type": "depth", "control_strength": 0.65, "motion_strength": 0.8, "tiled_decode": True}, ("VHS_LoadVideo", "GetImageFromBatch", "DepthAnythingV2", "DepthCrafter", "LTXVAddGuide", "LTXVCropGuides", "VAEDecodeTiled")),

    VideoRoute("ltx23.gguf.prompt_schedule", "ltx23", "gguf", "prompt_schedule", "enabled", "V20", False, ("Timeline-style prompt and motion scheduling using LTX prompt-beat metadata.", "Requires at least one prompt or motion event; FizzNodes integration remains optional/detection-only."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "prompt_events_required": True, "motion_events_supported": True, "tiled_decode": True}, ("DualCLIPLoaderGGUF", "LTXVConditioning", "LTXVScheduler", "VAEDecodeTiled")),
    VideoRoute("ltx23.unet.prompt_schedule", "ltx23", "unet", "prompt_schedule", "enabled", "V20", False, ("Safetensors LTX Prompt/Motion Schedule path using prompt-beat metadata.", "Requires at least one prompt or motion event; FizzNodes integration remains optional/detection-only."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "prompt_events_required": True, "motion_events_supported": True, "tiled_decode": True}, ("DualCLIPLoader", "LTXVConditioning", "LTXVScheduler", "VAEDecodeTiled")),

    VideoRoute("ltx23.gguf.audio_video", "ltx23", "gguf", "audio_video", "enabled", "V21", False, ("Audio-Video route using LTX prompt-conditioned audio intent, dialogue, soundscape, and sync metadata.", "Audio nodes are detected but not hard-wired until local ComfyUI node signatures are stable."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "audio_prompt_required": True, "audio_latent": True, "audio_strength": 0.75, "sync_strength": 0.6, "tiled_decode": True}, ("DualCLIPLoaderGGUF", "LTXVConditioning", "LTXVScheduler", "VAEDecodeTiled")),
    VideoRoute("ltx23.unet.audio_video", "ltx23", "unet", "audio_video", "enabled", "V21", False, ("Safetensors LTX Audio-Video route with prompt-conditioned audio intent and replay metadata.", "Audio nodes are detected but not hard-wired until local ComfyUI node signatures are stable."), {"width": 768, "height": 512, "frames": 97, "fps": 24, "steps": 8, "guidance": 1, "vram_profile": "balanced", "audio_prompt_required": True, "audio_latent": True, "audio_strength": 0.75, "sync_strength": 0.6, "tiled_decode": True}, ("DualCLIPLoader", "LTXVConditioning", "LTXVScheduler", "VAEDecodeTiled")),
)

_FAMILY_ALIAS: Final[dict[str, str]] = {alias: option.id for option in VIDEO_MODEL_FAMILIES for alias in (option.id, *option.aliases)}
_LOADER_ALIAS: Final[dict[str, str]] = {alias: option.id for option in VIDEO_LOADERS for alias in (option.id, *option.aliases)}
_TYPE_ALIAS: Final[dict[str, str]] = {alias: option.id for option in VIDEO_GENERATION_TYPES for alias in (option.id, *option.aliases)}


def _normalize(value: str | None, aliases: dict[str, str], fallback: str) -> str:
    key = str(value or fallback).strip().lower().replace("-", "_").replace(" ", "_")
    return aliases.get(key, fallback)


def normalize_video_family(value: str | None) -> str:
    return _normalize(value, _FAMILY_ALIAS, "wan22")


def normalize_video_loader(value: str | None) -> str:
    return _normalize(value, _LOADER_ALIAS, "unet")


def normalize_video_generation_type(value: str | None) -> str:
    return _normalize(value, _TYPE_ALIAS, "txt2vid")


def active_route_records(*, include_planned: bool = True) -> list[VideoRoute]:
    return [route for route in VIDEO_ROUTES if include_planned or route.status == "enabled"]


def find_video_route(family: str | None, loader: str | None, generation_type: str | None, *, include_planned: bool = True) -> VideoRoute | None:
    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    nt = normalize_video_generation_type(generation_type)
    for route in active_route_records(include_planned=include_planned):
        if route.family == nf and route.loader == nl and route.generation_type == nt:
            return route
    return None


def loader_options_for_family(family: str | None) -> tuple[VideoRouteOption, ...]:
    nf = normalize_video_family(family)
    loader_ids = []
    for route in VIDEO_ROUTES:
        if route.family == nf and route.generation_type in {"txt2vid", "img2vid", "first_last_frame", "multiscene", "extend", "vid2vid", "depth_motion", "prompt_schedule", "audio_video"} and route.status in {"enabled", "planned"} and route.loader not in loader_ids:
            loader_ids.append(route.loader)
    return tuple(option for option in VIDEO_LOADERS if option.id in loader_ids)


def generation_options_for_route(family: str | None, loader: str | None) -> tuple[VideoRouteOption, ...]:
    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    type_ids = []
    for route in VIDEO_ROUTES:
        if route.family == nf and route.loader == nl and route.status in {"enabled", "planned"} and route.generation_type not in type_ids:
            type_ids.append(route.generation_type)
    return tuple(option for option in VIDEO_GENERATION_TYPES if option.id in type_ids)


def video_route_matrix_payload() -> dict:
    routes = [route.payload() for route in VIDEO_ROUTES]
    return {
        "schema_version": "neo.video.route_matrix.v2",
        "surface": "video",
        "release_stage": "public_preview",
        "policy": "Only compatible family + loader + generation type combinations are selectable. Future routes remain visible as guarded registry slots, not runnable compilers.",
        "families": [item.payload() for item in VIDEO_MODEL_FAMILIES],
        "loaders": [item.payload() for item in VIDEO_LOADERS],
        "generation_types": [item.payload() for item in VIDEO_GENERATION_TYPES],
        "routes": routes,
        "first_build_order": ["wan22.gguf.img2vid_14b_dual_noise", "wan22.unet.txt2vid", "wan22.unet.img2vid", "ltx23.gguf.txt2vid", "ltx23.gguf.img2vid", "ltx23.gguf.first_last_frame", "ltx23.gguf.multiscene", "ltx23.gguf.extend", "ltx23.gguf.vid2vid", "ltx23.gguf.depth_motion", "ltx23.gguf.prompt_schedule", "ltx23.gguf.audio_video"],
        "guards": [
            "WAN 2.2 + GGUF + Img2Vid 14B dual-noise is the GGUF-first development route and compiles through the native V-G7 live model-discovery + dual-noise mapping adapter + queue-safe runtime preflight.",
            "WAN 2.2 Rapid AIO GGUF is experimental: dynamic model catalogs are active, but workflow queueing stays blocked until node signatures are verified.",
            "WAN 2.2 + UNET/Diffusion + Txt2Vid remains the legacy/compiler-compatible route.",
            "WAN 2.2 + UNET/Diffusion + Img2Vid is active in V6 when a source image is provided.",
            "LTX 2.3 Txt2Vid/Img2Vid routes are active through V8/V9; First/Last Frame is active in V15, MultiScene is active in V16, Extend is active in V17, Video-to-Video is active in V18, and Depth / Motion Control is active in V19 and Prompt/Motion Scheduling is active in V20 and Audio-Video is active in V21 for LTX only.",
            "WAN First/Last Frame/MultiScene/Extend/Video-to-Video, stay guarded until their explicit phases.",
            "Checkpoint is not selectable until a true checkpoint-style video route exists.",
            "Rapid AIO GGUF must never hardcode model filenames; Auto resolves from live Comfy catalogs and user selection wins.",
        ],
    }


def video_route_validation_payload(family: str | None = None, loader: str | None = None, generation_type: str | None = None) -> dict:
    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    nt = normalize_video_generation_type(generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    enabled = bool(route and route.status == "enabled")
    compatible = bool(route)
    fallback = find_video_route(nf, nl, "txt2vid", include_planned=True) or find_video_route(nf, "unet", "txt2vid", include_planned=True) or find_video_route("wan22", "unet", "txt2vid", include_planned=True)
    return {
        "schema_version": "neo.video.route_validation.v2",
        "surface": "video",
        "release_stage": "public_preview",
        "request": {"family": nf, "loader": nl, "generation_type": nt},
        "compatible": compatible,
        "enabled": enabled,
        "route": route.payload() if route else None,
        "fallback_route": fallback.payload() if fallback else None,
        "message": (
            f"Route {route.route_id} is compatible and selectable; runtime compiler is available for the selected route." if enabled and route else
            f"Route {route.route_id} is known but still planned; keep Generate disabled." if route else
            "No compatible Video route exists for this family + loader + generation type. Use the fallback route."
        ),
    }
