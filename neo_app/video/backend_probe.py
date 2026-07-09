from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Final
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from neo_app.providers.profiles import get_backend_profile_payload
from neo_app.video.gguf_loader_adapter import build_wan22_gguf_loader_plan
from neo_app.video.model_discovery import video_model_discovery_from_object_info
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader
from neo_app.video.video_performance_probe import video_performance_probe_payload

ROOT_DIR: Final[Path] = Path(__file__).resolve().parents[2]
DEFAULT_COMFY_URL: Final[str] = "http://127.0.0.1:8188"
WAN22_GGUF_DUAL_NOISE_ROUTE_ID: Final[str] = "wan22.gguf.img2vid_14b_dual_noise"
WAN22_RAPID_AIO_GGUF_ROUTE_IDS: Final[set[str]] = {"wan22.rapid_aio_gguf.txt2vid", "wan22.rapid_aio_gguf.img2vid"}
BACKEND_PROBE_SCHEMA_VERSION: Final[str] = "neo.video.backend_probe.vg13"
BACKEND_PROBE_PHASE: Final[str] = "V-G13"
WAN22_GGUF_FALLBACK_MODELS: Final[dict[str, str]] = {
    "high_noise": "wan2.2_i2v_high_noise_14B_Q4_K_M.gguf",
    "low_noise": "wan2.2_i2v_low_noise_14B_Q4_K_M.gguf",
    "clip": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
    "vae": "wan_2.1_vae.safetensors",
    "high_noise_lora": "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
    "low_noise_lora": "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
}

NODE_CATEGORY_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "core": (
        "KSampler", "SamplerCustomAdvanced", "RandomNoise", "CFGGuider", "CLIPTextEncode",
        "VAEDecode", "VAEDecodeTiled", "VAELoader", "UNETLoader", "DiffusionModelLoader", "ModelSamplingSD3",
    ),
    "wan": (
        "Wan22ImageToVideoLatent", "WanImageToVideoLatent", "WanImageToVideo", "WanVideoToVideoLatent",
        "WanFirstLastFrameToVideo", "WanFunControlToVideo",
    ),
    "ltx": (
        "LTXVConditioning", "EmptyLTXVLatentVideo", "LTXVScheduler", "LTXVCropGuides",
        "LTXVConcatAVLatent", "LTXVSeparateAVLatent", "LTXVLatentUpsampler",
        "LTXVAddGuideMulti", "LTXVAudioVAELoader", "LTXVAudioVAEDecode", "LTXVEmptyLatentAudio",
        "LTXVChunkFeedForward", "LTX2SamplingPreviewOverride", "LTX2AttentionTunerPatch",
        "LTX2MemoryEfficientSageAttentionPatch",
    ),
    "gguf": (
        "UnetLoaderGGUF", "UNETLoaderGGUF", "DualCLIPLoaderGGUF", "CLIPLoaderGGUF",
        "VAELoaderGGUF", "GGUFLoader", "GGUFModelLoader", "WanVideoModelLoaderGGUF", "WanVideoModelLoaderGGUFAdvanced",
    ),
    "video_io": (
        "CreateVideo", "VideoCombine", "LoadVideo", "LoadVideoUpload", "SaveVideo", "SaveWEBM", "SaveAnimatedWEBP",
        "VHS_LoadVideo", "VHS_VideoCombine", "VHS_LoadImages", "VHS_PruneOutputs",
    ),
    "interpolation": (
        "RIFE VFI", "RIFE_VFI", "FILM VFI", "FILM_VFI", "FrameInterpolation", "VFI", "AMT VFI",
    ),
    "upscale": (
        "SeedVR2", "SeedVR2Upscaler", "SeedVR2_VideoUpscaler", "VideoUpscale", "UpscaleVideo",
        "LatentUpscaleModelLoader", "UpscaleModelLoader", "ImageUpscaleWithModel",
    ),
    "depth": (
        "DepthAnythingV2", "DepthAnythingPreprocessor", "DepthCrafter", "DepthCrafterNodes",
        "MiDaS-DepthMapPreprocessor", "Zoe-DepthMapPreprocessor",
    ),
    "captioning": (
        "QwenVL", "Qwen2VL", "Qwen2_5VL", "Florence2", "JoyCaption", "Moondream",
    ),
    "workflow_utilities": (
        "ComfySwitchNode", "ComfyMathExpression", "PrimitiveBoolean", "PrimitiveInt", "PrimitiveFloat",
    ),
    "low_vram": (
        "WanVideoBlockSwap", "WanVideoBlockSwapAdvanced", "WanVideoBlockSwapKJ",
        "LTXVModelLoaderLowVRAM", "LTXVModelLoaderAdvanced", "LTXVModelLoaderKJ",
        "VAEDecodeTiled", "VAEDecodeTiledKJ", "VAELoaderKJ",
    ),
}

ROUTE_REQUIRED_NODE_SETS: Final[dict[str, dict[str, tuple[tuple[str, ...], ...]]]] = {
    "wan22.unet.txt2vid": {
        "required": (("UNETLoader", "DiffusionModelLoader"), ("CLIPLoader",), ("VAELoader",), ("Wan22ImageToVideoLatent", "WanImageToVideoLatent"), ("KSampler",), ("VAEDecode", "VAEDecodeTiled")),
        "recommended": (("SaveWEBM", "VideoCombine", "VHS_VideoCombine", "SaveAnimatedWEBP"),),
    },
    "wan22.unet.img2vid": {
        "required": (("UNETLoader", "DiffusionModelLoader"), ("CLIPLoader",), ("VAELoader",), ("Wan22ImageToVideoLatent", "WanImageToVideoLatent"), ("KSampler",), ("VAEDecode", "VAEDecodeTiled"), ("LoadImage",)),
        "recommended": (("SaveWEBM", "VideoCombine", "VHS_VideoCombine", "SaveAnimatedWEBP"),),
    },
    "wan22.gguf.img2vid_14b_dual_noise": {
        "required": (
            ("UnetLoaderGGUF", "UNETLoaderGGUF", "GGUFLoader", "GGUFModelLoader", "WanVideoModelLoaderGGUF", "WanVideoModelLoaderGGUFAdvanced"),
            ("CLIPLoader", "CLIPLoaderGGUF"),
            ("VAELoader", "VAELoaderGGUF"),
            ("LoadImage",),
            ("WanImageToVideo", "Wan22ImageToVideoLatent", "WanImageToVideoLatent"),
            ("ModelSamplingSD3",),
            ("KSamplerAdvanced",),
            ("VAEDecode", "VAEDecodeTiled"),
            ("CreateVideo", "VideoCombine", "VHS_VideoCombine"),
            ("SaveVideo", "SaveWEBM", "VHS_VideoCombine", "SaveAnimatedWEBP"),
            ("CLIPTextEncode",),
        ),
        "recommended": (("ComfySwitchNode",), ("ComfyMathExpression",), ("LoraLoaderModelOnly",)),
    },
    "ltx23.gguf.txt2vid": {
        "required": (("UnetLoaderGGUF", "UNETLoaderGGUF", "UNETLoader", "DiffusionModelLoader"), ("DualCLIPLoaderGGUF", "DualCLIPLoader"), ("VAELoader", "VAELoaderKJ"), ("LTXVConditioning",), ("EmptyLTXVLatentVideo",), ("LTXVScheduler", "ManualSigmas"), ("SamplerCustomAdvanced",), ("VAEDecodeTiled", "VAEDecode")),
        "recommended": (("LTXVChunkFeedForward",), ("LTXVLatentUpsampler",), ("SaveWEBM", "VideoCombine", "VHS_VideoCombine", "SaveAnimatedWEBP")),
    },
    "ltx23.gguf.img2vid": {
        "required": (("UnetLoaderGGUF", "UNETLoaderGGUF", "UNETLoader", "DiffusionModelLoader"), ("DualCLIPLoaderGGUF", "DualCLIPLoader"), ("VAELoader", "VAELoaderKJ"), ("LTXVConditioning",), ("EmptyLTXVLatentVideo",), ("LTXVScheduler", "ManualSigmas"), ("SamplerCustomAdvanced",), ("VAEDecodeTiled", "VAEDecode"), ("LoadImage",)),
        "recommended": (("LTXVCropGuides",), ("LTXVChunkFeedForward",), ("LTXVLatentUpsampler",), ("SaveWEBM", "VideoCombine", "VHS_VideoCombine", "SaveAnimatedWEBP")),
    },
    "ltx23.unet.txt2vid": {
        "required": (("UNETLoader", "DiffusionModelLoader"), ("DualCLIPLoader", "CLIPLoader"), ("VAELoader", "VAELoaderKJ"), ("LTXVConditioning",), ("EmptyLTXVLatentVideo",), ("LTXVScheduler", "ManualSigmas"), ("SamplerCustomAdvanced",), ("VAEDecodeTiled", "VAEDecode")),
        "recommended": (("LTXVChunkFeedForward",), ("LTXVLatentUpsampler",), ("SaveWEBM", "VideoCombine", "VHS_VideoCombine", "SaveAnimatedWEBP")),
    },
    "ltx23.unet.img2vid": {
        "required": (("UNETLoader", "DiffusionModelLoader"), ("DualCLIPLoader", "CLIPLoader"), ("VAELoader", "VAELoaderKJ"), ("LTXVConditioning",), ("EmptyLTXVLatentVideo",), ("LTXVScheduler", "ManualSigmas"), ("SamplerCustomAdvanced",), ("VAEDecodeTiled", "VAEDecode"), ("LoadImage",)),
        "recommended": (("LTXVCropGuides",), ("LTXVChunkFeedForward",), ("LTXVLatentUpsampler",), ("SaveWEBM", "VideoCombine", "VHS_VideoCombine", "SaveAnimatedWEBP")),
    },
}

@dataclass(frozen=True)
class NodeGate:
    label: str
    options: tuple[str, ...]
    present: bool
    matched: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_backend_profiles() -> dict[str, Any]:
    try:
        return get_backend_profile_payload()
    except Exception:
        return {"defaults": {}, "profiles": []}


def video_backend_profile_payload(profile_id: str | None = None) -> dict[str, Any]:
    data = _read_backend_profiles()
    defaults = data.get("defaults") if isinstance(data.get("defaults"), dict) else {}
    profiles = [item for item in data.get("profiles", []) if isinstance(item, dict)]
    target_id = profile_id or defaults.get("video") or "video.comfyui_portable"
    profile = next((item for item in profiles if item.get("profile_id") == target_id), None)
    if profile is None:
        profile = next((item for item in profiles if item.get("surface") == "video" and item.get("is_default")), None)
    if profile is None:
        profile = next((item for item in profiles if item.get("surface") == "video"), None)
    connection = profile.get("connection", {}) if isinstance(profile, dict) else {}
    base_url = str(connection.get("base_url") or profile.get("runtime", {}).get("base_url") or DEFAULT_COMFY_URL).strip() if isinstance(profile, dict) else DEFAULT_COMFY_URL
    return {
        "profile_id": profile.get("profile_id") if isinstance(profile, dict) else "video.comfyui_portable",
        "display_name": profile.get("display_name") if isinstance(profile, dict) else "Video · ComfyUI Portable",
        "provider_id": profile.get("provider_id") if isinstance(profile, dict) else "comfyui_portable",
        "surface": "video",
        "connection": {
            "kind": connection.get("kind", "url") if isinstance(connection, dict) else "url",
            "base_url": base_url or DEFAULT_COMFY_URL,
            "portable_path": connection.get("portable_path", "") if isinstance(connection, dict) else "",
            "launch_command": connection.get("launch_command", "") if isinstance(connection, dict) else "",
        },
    }


def _get_json(base_url: str, endpoint: str, timeout: float) -> dict[str, Any]:
    url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    req = Request(url, headers={"Accept": "application/json", "User-Agent": "NeoStudioVideoProbe/1.0"})
    with urlopen(req, timeout=timeout) as response:  # noqa: S310 - local user-configured Comfy URL probe.
        raw = response.read()
    decoded = raw.decode("utf-8", errors="replace")
    data = json.loads(decoded) if decoded else {}
    return data if isinstance(data, dict) else {"value": data}


def _available_nodes(object_info: dict[str, Any] | None) -> set[str]:
    return {str(key) for key in (object_info or {}).keys()}


def _matches(nodes: set[str], options: tuple[str, ...]) -> tuple[str, ...]:
    node_fold = {item.casefold(): item for item in nodes}
    found: list[str] = []
    for option in options:
        exact = node_fold.get(option.casefold())
        if exact:
            found.append(exact)
            continue
        needle = option.casefold().replace(" ", "").replace("_", "")
        fuzzy = next((node for node in nodes if needle and needle in node.casefold().replace(" ", "").replace("_", "")), None)
        if fuzzy:
            found.append(fuzzy)
    return tuple(dict.fromkeys(found))


def classify_video_nodes(object_info: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    nodes = _available_nodes(object_info)
    payload: dict[str, dict[str, Any]] = {}
    for category, aliases in NODE_CATEGORY_ALIASES.items():
        matched = _matches(nodes, aliases)
        payload[category] = {
            "category": category,
            "available": bool(matched),
            "matched": list(matched),
            "expected_any": list(aliases),
            "count": len(matched),
        }
    return payload


def route_node_readiness(route_id: str, object_info: dict[str, Any] | None) -> dict[str, Any]:
    nodes = _available_nodes(object_info)
    spec = ROUTE_REQUIRED_NODE_SETS.get(route_id, {"required": (), "recommended": ()})
    required_gates = [NodeGate("required", tuple(options), bool(_matches(nodes, tuple(options))), _matches(nodes, tuple(options))).payload() for options in spec.get("required", ())]
    recommended_gates = [NodeGate("recommended", tuple(options), bool(_matches(nodes, tuple(options))), _matches(nodes, tuple(options))).payload() for options in spec.get("recommended", ())]
    missing_required = [gate for gate in required_gates if not gate["present"]]
    missing_recommended = [gate for gate in recommended_gates if not gate["present"]]
    return {
        "route_id": route_id,
        "required": required_gates,
        "recommended": recommended_gates,
        "missing_required": missing_required,
        "missing_recommended": missing_recommended,
        "ready": not missing_required,
        "warning_count": len(missing_recommended),
    }


def _model_visible(model_name: str, available: list[str]) -> bool:
    return bool(model_name and available and str(model_name) in {str(item) for item in available})


def wan22_gguf_backend_model_probe(
    object_info: dict[str, Any] | None,
    *,
    high_noise_model: str | None = None,
    low_noise_model: str | None = None,
    rapid_aio_model: str | None = None,
    rapid_aio_text_encoder: str | None = None,
    rapid_aio_vae: str | None = None,
    clip_name: str | None = None,
    vae_name: str | None = None,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
    enable_lightx2v: bool = False,
    enable_video_lora: bool = False,
    video_lora_mode: str | None = None,
    video_lora_model: str | None = None,
    video_lora_target: str | None = None,
) -> dict[str, Any]:
    """Validate the WAN 2.2 GGUF dual-noise loader/model catalog exposed by ComfyUI /object_info."""
    info = object_info or {}
    nodes = _available_nodes(info)
    plan = build_wan22_gguf_loader_plan(
        info,
        fallback_models=WAN22_GGUF_FALLBACK_MODELS,
        high_noise_model=high_noise_model,
        low_noise_model=low_noise_model,
        clip_name=clip_name,
        vae_name=vae_name,
        high_noise_lora=high_noise_lora,
        low_noise_lora=low_noise_lora,
    )
    adapter = plan.payload()
    classes = adapter.get("classes", {})
    models = adapter.get("models", {})
    available_models = adapter.get("available_models", {})
    counts = adapter.get("available_model_counts", {})
    dual_noise_mapping = adapter.get("adapter", {}).get("dual_noise_mapping", {})

    gguf_values = [str(item) for item in available_models.get("gguf", [])]
    clip_values = [str(item) for item in available_models.get("clip", [])]
    vae_values = [str(item) for item in available_models.get("vae", [])]
    lora_values = [str(item) for item in available_models.get("lora", [])]

    gguf_loader = str(classes.get("gguf_loader") or "")
    clip_loader = str(classes.get("clip_loader") or "")
    vae_loader = str(classes.get("vae_loader") or "")
    lora_loader = str(classes.get("lora_loader") or "")
    high_model = str(models.get("high_noise_model") or "")
    low_model = str(models.get("low_noise_model") or "")
    clip_model = str(models.get("clip_name") or "")
    vae_model = str(models.get("vae_name") or "")
    high_lora = str(models.get("high_noise_lora") or "")
    low_lora = str(models.get("low_noise_lora") or "")

    errors: list[str] = []
    warnings: list[str] = []
    action_items: list[str] = []

    if gguf_loader not in nodes:
        errors.append("WAN GGUF loader node is not installed or not visible in ComfyUI /object_info.")
        action_items.append("Install/enable the ComfyUI GGUF loader node pack, restart ComfyUI, then run the Video backend probe again.")
    if not gguf_values:
        errors.append("WAN GGUF loader was detected/fallback-selected, but /object_info did not expose any GGUF model dropdown values.")
        action_items.append("Put the WAN 2.2 high/low GGUF model files where your GGUF loader expects them, restart ComfyUI, then confirm the loader dropdown is populated.")
    if gguf_values and not _model_visible(high_model, gguf_values):
        errors.append(f"Selected high-noise GGUF model is not visible to ComfyUI: {high_model}")
    if gguf_values and not _model_visible(low_model, gguf_values):
        errors.append(f"Selected low-noise GGUF model is not visible to ComfyUI: {low_model}")
    if dual_noise_mapping and not dual_noise_mapping.get("ready", False):
        errors.append("WAN GGUF dual-noise model mapping is not ready; choose separate high-noise and low-noise GGUF files.")

    candidate_counts = dual_noise_mapping.get("candidate_counts", {}) if isinstance(dual_noise_mapping, dict) else {}
    if gguf_values and int(candidate_counts.get("high_noise") or 0) < 1:
        errors.append("No visible GGUF model has a clear high-noise filename token.")
    if gguf_values and int(candidate_counts.get("low_noise") or 0) < 1:
        errors.append("No visible GGUF model has a clear low-noise filename token.")

    if clip_loader and clip_loader not in nodes:
        warnings.append(f"Selected CLIP loader class is not visible: {clip_loader}")
    if vae_loader and vae_loader not in nodes:
        warnings.append(f"Selected VAE loader class is not visible: {vae_loader}")
    if clip_values and clip_model not in clip_values:
        warnings.append(f"Selected WAN text encoder is not in the ComfyUI CLIP dropdown: {clip_model}")
    if vae_values and vae_model not in vae_values:
        warnings.append(f"Selected WAN VAE is not in the ComfyUI VAE dropdown: {vae_model}")
    if enable_lightx2v:
        if lora_loader and lora_loader not in nodes:
            errors.append(f"LightX2V is enabled, but the LoRA loader class is not visible: {lora_loader}")
        if lora_values and high_lora not in lora_values:
            errors.append(f"Selected high-noise LightX2V LoRA is not visible to ComfyUI: {high_lora}")
        if lora_values and low_lora not in lora_values:
            errors.append(f"Selected low-noise LightX2V LoRA is not visible to ComfyUI: {low_lora}")
    normal_lora_enabled = bool(enable_video_lora or str(video_lora_mode or "").strip().lower().replace("-", "_") == "normal")
    if normal_lora_enabled:
        if lora_loader and lora_loader not in nodes:
            errors.append(f"Video LoRA is enabled, but the LoRA loader class is not visible: {lora_loader}")
        if not video_lora_model:
            errors.append("Video LoRA is enabled, but no normal video_lora_model was selected.")
        elif lora_values and str(video_lora_model) not in lora_values:
            errors.append(f"Selected Video LoRA is not visible to ComfyUI: {video_lora_model}")

    diagnostics = [str(item) for item in adapter.get("adapter", {}).get("diagnostics", []) if item]
    for item in diagnostics:
        if item not in warnings and item not in errors:
            warnings.append(item)

    high_visible = _model_visible(high_model, gguf_values)
    low_visible = _model_visible(low_model, gguf_values)
    ready = not errors and high_visible and low_visible and bool(dual_noise_mapping.get("ready", False))

    return {
        "schema_version": "neo.video.wan22_gguf_backend_model_probe.vg6",
        "phase": BACKEND_PROBE_PHASE,
        "route_id": WAN22_GGUF_DUAL_NOISE_ROUTE_ID,
        "ready": ready,
        "loader_ready": gguf_loader in nodes,
        "model_catalog_ready": bool(gguf_values),
        "selected_pair_visible": high_visible and low_visible,
        "dual_noise_ready": bool(dual_noise_mapping.get("ready", False)),
        "classes": classes,
        "fields": adapter.get("fields", {}),
        "models": models,
        "available_model_counts": counts,
        "available_models_preview": {
            "gguf": gguf_values[:20],
            "clip": clip_values[:20],
            "vae": vae_values[:20],
            "lora": lora_values[:20],
        },
        "dual_noise_mapping": dual_noise_mapping,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "action_items": list(dict.fromkeys(action_items)),
        "adapter": adapter.get("adapter", {}),
    }


def _status_from_probe(reachable: bool, route_ready: bool, warnings: list[str], errors: list[str], *, model_ready: bool | None = None) -> str:
    if not reachable:
        return "backend_offline"
    if not route_ready:
        return "missing_nodes"
    if model_ready is False:
        return "missing_models"
    if errors:
        return "probe_errors"
    if warnings:
        return "ready_with_warnings"
    return "ready"


def video_backend_probe_payload(
    family: str | None = None,
    loader: str | None = None,
    generation_type: str | None = None,
    profile_id: str | None = None,
    timeout: float = 2.0,
    object_info_override: dict[str, Any] | None = None,
    system_stats_override: dict[str, Any] | None = None,
    high_noise_model: str | None = None,
    low_noise_model: str | None = None,
    rapid_aio_model: str | None = None,
    rapid_aio_text_encoder: str | None = None,
    rapid_aio_vae: str | None = None,
    clip_name: str | None = None,
    vae_name: str | None = None,
    enable_lightx2v: bool = False,
    enable_video_lora: bool = False,
    video_lora_mode: str | None = None,
    video_lora_model: str | None = None,
    video_lora_target: str | None = None,
    high_noise_lora: str | None = None,
    low_noise_lora: str | None = None,
    performance_profile: str | None = None,
    enable_sage_attention: bool = False,
    sage_attention_mode: str | None = None,
    sage_attention_target: str | None = None,
    enable_teacache: bool = False,
    teacache_profile: str | None = None,
    teacache_target: str | None = None,
    enable_cpu_offload: bool = False,
    enable_vae_offload: bool = False,
    enable_block_swap: bool = False,
    block_swap_target: str | None = None,
    block_swap_blocks: int | None = None,
    enable_torch_compile: bool = False,
) -> dict[str, Any]:
    nf = normalize_video_family(family)
    nl = normalize_video_loader(loader)
    nt = normalize_video_generation_type(generation_type)
    route = find_video_route(nf, nl, nt, include_planned=True)
    route_id = route.route_id if route else "wan22.unet.txt2vid"
    profile = video_backend_profile_payload(profile_id)
    base_url = profile["connection"]["base_url"] or DEFAULT_COMFY_URL

    reachable = False
    errors: list[str] = []
    warnings: list[str] = []
    system_stats: dict[str, Any] = {}
    object_info: dict[str, Any] = {}

    if object_info_override is not None:
        object_info = object_info_override
        system_stats = system_stats_override or {}
        reachable = True
    else:
        try:
            system_stats = _get_json(base_url, "/system_stats", timeout)
            reachable = True
        except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            errors.append(f"ComfyUI not reachable at {base_url}: {exc}")
        if reachable:
            try:
                object_info = _get_json(base_url, "/object_info", timeout)
            except (HTTPError, URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
                errors.append(f"ComfyUI /object_info discovery failed: {exc}")

    node_categories = classify_video_nodes(object_info)
    route_readiness = route_node_readiness(route_id, object_info)
    if reachable and route_readiness["missing_required"]:
        missing = [" or ".join(gate["options"]) for gate in route_readiness["missing_required"]]
        errors.append("Missing required route nodes: " + "; ".join(missing))
    if reachable and route_readiness["missing_recommended"]:
        missing = [" or ".join(gate["options"]) for gate in route_readiness["missing_recommended"]]
        warnings.append("Missing recommended helper/output nodes: " + "; ".join(missing))
    if nl == "gguf" and not node_categories["gguf"]["available"]:
        warnings.append("GGUF route selected but GGUF loader nodes were not detected.")
    if nt == "img2vid" and reachable and "LoadImage" not in _available_nodes(object_info):
        errors.append("Img2Vid route requires ComfyUI LoadImage support.")

    gguf_model_probe: dict[str, Any] | None = None
    model_discovery: dict[str, Any] | None = None
    action_items: list[str] = []
    model_ready: bool | None = None
    performance_values = {
        "performance_profile": performance_profile or "safe_12gb",
        "enable_sage_attention": bool(enable_sage_attention),
        "sage_attention_mode": sage_attention_mode or "auto",
        "sage_attention_target": sage_attention_target or "both",
        "enable_teacache": bool(enable_teacache),
        "teacache_profile": teacache_profile or "conservative",
        "teacache_target": teacache_target or "both",
        "enable_cpu_offload": bool(enable_cpu_offload),
        "enable_vae_offload": bool(enable_vae_offload),
        "enable_block_swap": bool(enable_block_swap),
        "block_swap_target": block_swap_target or "both",
        "block_swap_blocks": block_swap_blocks if block_swap_blocks is not None else 12,
        "enable_torch_compile": bool(enable_torch_compile),
    }
    performance_probe = video_performance_probe_payload(
        object_info,
        family=nf,
        loader=nl,
        generation_type=nt,
        performance_profile=performance_profile,
        values=performance_values,
    ) if reachable or object_info_override is not None else None
    if performance_probe and not performance_probe.get("queue_ready", True):
        for item in performance_probe.get("errors", []):
            if item not in errors:
                errors.append(str(item))
        for item in performance_probe.get("warnings", []):
            if item not in warnings:
                warnings.append(str(item))
        action_items.extend(str(item) for item in performance_probe.get("action_items", []) if item)

    if reachable and route_id in ({WAN22_GGUF_DUAL_NOISE_ROUTE_ID, *WAN22_RAPID_AIO_GGUF_ROUTE_IDS}):
        model_discovery = video_model_discovery_from_object_info(
            object_info,
            family=nf,
            loader=nl,
            generation_type=nt,
            fallback_models=WAN22_GGUF_FALLBACK_MODELS if route_id == WAN22_GGUF_DUAL_NOISE_ROUTE_ID else {},
            high_noise_model=high_noise_model,
            low_noise_model=low_noise_model,
            rapid_aio_model=rapid_aio_model,
            clip_name=rapid_aio_text_encoder or clip_name,
            vae_name=rapid_aio_vae or vae_name,
            high_noise_lora=high_noise_lora,
            low_noise_lora=low_noise_lora,
        )
        if route_id == WAN22_GGUF_DUAL_NOISE_ROUTE_ID:
            gguf_model_probe = wan22_gguf_backend_model_probe(
                object_info,
                high_noise_model=high_noise_model,
                low_noise_model=low_noise_model,
                clip_name=clip_name,
                vae_name=vae_name,
                high_noise_lora=high_noise_lora,
                low_noise_lora=low_noise_lora,
                enable_lightx2v=enable_lightx2v,
                enable_video_lora=enable_video_lora,
                video_lora_mode=video_lora_mode,
                video_lora_model=video_lora_model,
                video_lora_target=video_lora_target,
            )
            model_ready = bool(gguf_model_probe.get("ready"))
            for item in gguf_model_probe.get("errors", []):
                if item not in errors:
                    errors.append(str(item))
            for item in gguf_model_probe.get("warnings", []):
                if item not in warnings:
                    warnings.append(str(item))
            action_items.extend(str(item) for item in gguf_model_probe.get("action_items", []) if item)
        else:
            model_ready = bool((model_discovery or {}).get("catalog_ready"))
            action_items.append("Rapid AIO GGUF is catalog/audit-only in Phase 10n; do not queue until the compiler pass verifies node signatures.")
        for item in (model_discovery or {}).get("errors", []):
            if item not in errors:
                errors.append(str(item))
        for item in (model_discovery or {}).get("warnings", []):
            if item not in warnings:
                warnings.append(str(item))

    status = _status_from_probe(reachable, route_readiness["ready"], warnings, errors, model_ready=model_ready)
    return {
        "schema_version": BACKEND_PROBE_SCHEMA_VERSION,
        "legacy_schema_version": "neo.video.backend_probe.v6",
        "surface": "video",
        "phase": BACKEND_PROBE_PHASE,
        "checked_at": _now(),
        "backend": {
            "profile": profile,
            "base_url": base_url,
            "reachable": reachable,
            "status": status,
        },
        "request": {
            "family": nf,
            "loader": nl,
            "generation_type": nt,
            "profile_id": profile.get("profile_id"),
            "high_noise_model": high_noise_model or "",
            "low_noise_model": low_noise_model or "",
            "clip_name": clip_name or "",
            "vae_name": vae_name or "",
            "enable_lightx2v": bool(enable_lightx2v),
            "enable_video_lora": bool(enable_video_lora),
            "video_lora_mode": video_lora_mode or "",
            "video_lora_model": video_lora_model or "",
            "video_lora_target": video_lora_target or "",
            "performance_profile": performance_profile or "safe_12gb",
            "enable_sage_attention": bool(enable_sage_attention),
            "sage_attention_mode": sage_attention_mode or "auto",
            "sage_attention_target": sage_attention_target or "both",
            "enable_teacache": bool(enable_teacache),
            "teacache_profile": teacache_profile or "conservative",
            "teacache_target": teacache_target or "both",
            "enable_cpu_offload": bool(enable_cpu_offload),
            "enable_vae_offload": bool(enable_vae_offload),
            "enable_block_swap": bool(enable_block_swap),
            "block_swap_target": block_swap_target or "both",
            "block_swap_blocks": block_swap_blocks if block_swap_blocks is not None else 12,
            "enable_torch_compile": bool(enable_torch_compile),
        },
        "route": route.payload() if route else None,
        "route_readiness": route_readiness,
        "gguf_model_probe": gguf_model_probe,
        "model_discovery": model_discovery,
        "performance_probe": performance_probe,
        "node_categories": node_categories,
        "system_stats": system_stats if reachable else {},
        "object_info_node_count": len(object_info) if isinstance(object_info, dict) else 0,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "action_items": list(dict.fromkeys(action_items)),
        "rules": [
            "V-G13 probes ComfyUI /system_stats and /object_info only; it does not queue prompts.",
            "V-G13 performance probe validates active Sage Attention, TeaCache, and low-VRAM offload/block-swap selections.",
            "Route readiness checks node availability for the selected Video model family, loader, and generation type.",
            "WAN 2.2 GGUF readiness validates loader schema, live /object_info model catalogs, high/low dropdown visibility, and dual-noise pairing.",
            "ComfyUI output folders are source references; Neo-owned video outputs stay under neo_data/outputs/video.",
        ],
    }
