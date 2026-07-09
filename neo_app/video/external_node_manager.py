from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Final

from neo_app.video.backend_probe import _get_json, video_backend_profile_payload, DEFAULT_COMFY_URL

SCHEMA_VERSION: Final[str] = "neo.video.external_node_manager.v11"
PHASE: Final[str] = "V11"


@dataclass(frozen=True)
class VideoExternalNodePack:
    pack_id: str
    display_name: str
    category: str
    purpose: str
    repo_url: str
    aliases: tuple[str, ...]
    phase_target: str
    install_role: str = "optional"
    risk: str = "medium"
    route_lanes: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["aliases"] = list(self.aliases)
        data["route_lanes"] = list(self.route_lanes)
        data["notes"] = list(self.notes)
        return data


VIDEO_EXTERNAL_NODE_PACKS: Final[tuple[VideoExternalNodePack, ...]] = (
    VideoExternalNodePack(
        pack_id="video_helper_suite",
        display_name="ComfyUI VideoHelperSuite",
        category="video_io",
        purpose="Load/combine/save video, frame sequences, and audio/video outputs.",
        repo_url="https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite",
        aliases=("VideoCombine", "VHS_VideoCombine", "LoadVideo", "VHS_LoadVideo", "LoadVideoUpload", "VHS_LoadImages", "VHS_PruneOutputs"),
        phase_target="V11/V12+",
        install_role="recommended",
        risk="low",
        route_lanes=("txt2vid", "img2vid", "finish", "results"),
        notes=("Recommended video I/O pack for reliable combine/export lanes.",),
    ),
    VideoExternalNodePack(
        pack_id="frame_interpolation",
        display_name="ComfyUI Frame Interpolation",
        category="interpolation",
        purpose="Post-generation FPS increase and smoother motion.",
        repo_url="https://github.com/Fannovel16/ComfyUI-Frame-Interpolation",
        aliases=("RIFE VFI", "RIFE_VFI", "FILM VFI", "FILM_VFI", "AMT VFI", "FrameInterpolation", "VFI"),
        phase_target="V12",
        install_role="optional",
        risk="medium",
        route_lanes=("finish", "interpolate"),
        notes=("Finish-lane only. It should never block base generation.",),
    ),
    VideoExternalNodePack(
        pack_id="rife_tensorrt",
        display_name="RIFE TensorRT",
        category="interpolation_experimental",
        purpose="Experimental NVIDIA TensorRT acceleration path for RIFE interpolation.",
        repo_url="https://github.com/yuvraj108c/ComfyUI-Rife-Tensorrt",
        aliases=("RIFE_Tensorrt", "RIFE TensorRT", "RIFETensorrt", "RIFE_TRT", "TensorRT_RIFE", "TRT_RIFE"),
        phase_target="V24.x",
        install_role="experimental",
        risk="high",
        route_lanes=("finish", "interpolate", "experimental"),
        notes=("Diagnostics only in V24.5. Do not promote to stable runtime until CUDA/TensorRT compatibility is validated locally.",),
    ),
    VideoExternalNodePack(
        pack_id="gimm_vfi",
        display_name="GIMM-VFI",
        category="interpolation_experimental",
        purpose="Experimental generative interpolation method for specialized quality tests.",
        repo_url="https://github.com/kijai/ComfyUI-GIMM-VFI",
        aliases=("GIMM_VFI", "GIMM VFI", "GIMM-VFI", "GIMMModelLoader", "GIMMInterpolation"),
        phase_target="V24.x",
        install_role="experimental",
        risk="high",
        route_lanes=("finish", "interpolate", "experimental"),
        notes=("Diagnostics only in V24.5. Keep hidden from stable method defaults.",),
    ),
    VideoExternalNodePack(
        pack_id="vsrfi_stream",
        display_name="VSRFI Streaming",
        category="interpolation_experimental",
        purpose="Experimental chunk/stream-oriented video super-resolution + interpolation lane.",
        repo_url="https://github.com/neilthefrobot/VSRFI-ComfyUI",
        aliases=("VSRFI", "VSRFI_VFI", "VSRFI Stream", "VSRFIStreaming", "VSRFIUpscale", "VSRFIInterpolate"),
        phase_target="V24.x",
        install_role="experimental",
        risk="high",
        route_lanes=("finish", "interpolate", "experimental"),
        notes=("Diagnostics only in V24.5. This is not the stable first-pass interpolation lane.",),
    ),
    VideoExternalNodePack(
        pack_id="seedvr2_upscaler",
        display_name="SeedVR2 Video Upscaler",
        category="upscale",
        purpose="Video upscaling as a non-destructive Finish child output.",
        repo_url="https://github.com/numz/ComfyUI-SeedVR2_VideoUpscaler",
        aliases=("SeedVR2", "SeedVR2LoadDiTModel", "SeedVR2LoadVAEModel", "SeedVR2VideoUpscaler", "SeedVR2TorchCompileSettings", "SeedVR2Upscaler", "SeedVR2_VideoUpscaler", "VideoUpscale", "UpscaleVideo"),
        phase_target="V25.9.19 Phase 6",
        install_role="optional",
        risk="high",
        route_lanes=("finish", "upscale"),
        notes=("Heavy post-process lane. Phase 6 requires native SeedVR2 DiT/VAE/upscaler nodes plus video components/create/save nodes.",),
    ),
    VideoExternalNodePack(
        pack_id="mtb",
        display_name="comfy_mtb",
        category="utility",
        purpose="Utility nodes that can help video/frame workflows and file operations.",
        repo_url="https://github.com/melMass/comfy_mtb",
        aliases=("MTB", "Load Images From Directory", "Save Image Grid", "Image Compare", "QrCode", "Concat Images"),
        phase_target="V11+",
        install_role="optional",
        risk="medium",
        route_lanes=("assets", "finish"),
        notes=("Utility pack. Treat as optional because node names vary across versions.",),
    ),
    VideoExternalNodePack(
        pack_id="animatediff_evolved",
        display_name="AnimateDiff Evolved",
        category="motion_legacy",
        purpose="Legacy/alternative animation workflows and motion modules.",
        repo_url="https://github.com/Kosinkadink/ComfyUI-AnimateDiff-Evolved",
        aliases=("AnimateDiffLoader", "ADE_AnimateDiffLoaderGen1", "ADE_AnimateDiffLoaderGen2", "ADE_ApplyAnimateDiffModel", "AnimateDiffCombine"),
        phase_target="future",
        install_role="experimental",
        risk="high",
        route_lanes=("motion", "experimental"),
        notes=("Not part of WAN/LTX compiler foundation. Keep isolated from base routes.",),
    ),
    VideoExternalNodePack(
        pack_id="fizznodes",
        display_name="FizzNodes",
        category="motion_schedule",
        purpose="Prompt/keyframe scheduling, float/int schedules, and animation control helpers.",
        repo_url="https://github.com/FizzleDorf/ComfyUI_FizzNodes",
        aliases=("FizzNodes", "PromptSchedule", "BatchPromptSchedule", "StringSchedule", "ValueSchedule", "WaveGenerator"),
        phase_target="V20",
        install_role="optional",
        risk="medium",
        route_lanes=("motion", "schedule"),
        notes=("Useful once Neo has a timeline/keyframe UI.",),
    ),
    VideoExternalNodePack(
        pack_id="depthcrafter",
        display_name="DepthCrafter Nodes",
        category="depth",
        purpose="Consistent depth maps for video/source clips.",
        repo_url="https://github.com/akatz-ai/ComfyUI-DepthCrafter-Nodes",
        aliases=("DepthCrafter", "DepthCrafterNodes", "DepthCrafterPreprocessor", "DepthCrafterModelLoader"),
        phase_target="V19",
        install_role="optional",
        risk="high",
        route_lanes=("depth", "motion_control"),
        notes=("Depth-control lane only; do not block basic generation.",),
    ),
    VideoExternalNodePack(
        pack_id="depthanything_v2",
        display_name="DepthAnythingV2",
        category="depth",
        purpose="Monocular image depth preprocessing for control/reference lanes.",
        repo_url="https://github.com/kijai/ComfyUI-DepthAnythingV2",
        aliases=("DepthAnythingV2", "DepthAnythingPreprocessor", "DepthAnythingV2Preprocessor", "DepthAnythingV2ModelLoader"),
        phase_target="V19",
        install_role="optional",
        risk="medium",
        route_lanes=("depth", "reference"),
        notes=("Pairs well with source-image/reference workflows.",),
    ),
    VideoExternalNodePack(
        pack_id="controlnet_aux",
        display_name="ControlNet Aux",
        category="control",
        purpose="Preprocessors for control hints such as depth, edges, pose, and lineart.",
        repo_url="https://github.com/Fannovel16/comfyui_controlnet_aux",
        aliases=("AIO_Preprocessor", "CannyEdgePreprocessor", "LineArtPreprocessor", "OpenposePreprocessor", "MiDaS-DepthMapPreprocessor", "Zoe-DepthMapPreprocessor"),
        phase_target="V19",
        install_role="optional",
        risk="medium",
        route_lanes=("control", "depth_motion"),
        notes=("Control preprocessors are optional and must be route-gated.",),
    ),
    VideoExternalNodePack(
        pack_id="qwenvl",
        display_name="ComfyUI QwenVL",
        category="captioning",
        purpose="Image/video understanding helper for reference analysis and prompt assist.",
        repo_url="https://github.com/1038lab/ComfyUI-QwenVL",
        aliases=("QwenVL", "Qwen2VL", "Qwen2_5VL", "QwenVLLoader", "QwenVLChat", "Qwen2VLImageCaption"),
        phase_target="assist/future",
        install_role="optional",
        risk="medium",
        route_lanes=("assist", "reference", "captioning"),
        notes=("Assist/analyze lane only. Generation should not depend on it.",),
    ),
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _available_nodes(object_info: dict[str, Any] | None) -> set[str]:
    return {str(key) for key in (object_info or {}).keys()}


def _match_aliases(nodes: set[str], aliases: tuple[str, ...]) -> list[str]:
    node_fold = {item.casefold(): item for item in nodes}
    matched: list[str] = []
    for alias in aliases:
        exact = node_fold.get(alias.casefold())
        if exact:
            matched.append(exact)
            continue
        needle = alias.casefold().replace(" ", "").replace("_", "")
        fuzzy = next((node for node in nodes if needle and needle in node.casefold().replace(" ", "").replace("_", "")), None)
        if fuzzy:
            matched.append(fuzzy)
    return list(dict.fromkeys(matched))


FRAME_INTERPOLATION_READINESS_SCHEMA: Final[str] = "neo.video.finish.frame_interpolation.readiness.v1"
FRAME_INTERPOLATION_READINESS_PHASE: Final[str] = "V24.5"
SEEDVR2_UPSCALE_ADMIN_READINESS_SCHEMA: Final[str] = "neo.video.finish.seedvr2_upscale.admin_readiness.v25_9_19_phase_8"
SEEDVR2_UPSCALE_ADMIN_READINESS_PHASE: Final[str] = "V25.9.19 Phase 8"

VIDEO_IO_LOAD_ALIASES: Final[tuple[str, ...]] = ("VHS_LoadVideoPath", "LoadVideoPath", "LoadVideoFromPath", "VHS_LoadVideo", "LoadVideo", "LoadVideoUpload")
VIDEO_IO_COMPONENT_ALIASES: Final[tuple[str, ...]] = ("GetVideoComponents", "VHS_GetVideoComponents", "VideoComponents", "Get Video Components")
VIDEO_IO_CREATE_ALIASES: Final[tuple[str, ...]] = ("CreateVideo", "VHS_CreateVideo", "VideoCreate")
VIDEO_IO_SAVE_ALIASES: Final[tuple[str, ...]] = ("VHS_VideoCombine", "VideoCombine", "SaveWEBM", "SaveAnimatedWEBP", "SaveVideo", "VHS_SaveVideo", "VideoSave")
SEEDVR2_DIT_LOADER_ALIASES: Final[tuple[str, ...]] = ("SeedVR2LoadDiTModel", "SeedVR2LoadDITModel", "LoadSeedVR2DiTModel", "SeedVR2 DiT Loader")
SEEDVR2_VAE_LOADER_ALIASES: Final[tuple[str, ...]] = ("SeedVR2LoadVAEModel", "LoadSeedVR2VAEModel", "SeedVR2 VAE Loader")
SEEDVR2_UPSCALER_ALIASES: Final[tuple[str, ...]] = ("SeedVR2VideoUpscaler", "SeedVR2Upscaler", "SeedVR2 Video Upscaler", "SeedVR2")
SEEDVR2_TORCH_COMPILE_ALIASES: Final[tuple[str, ...]] = ("SeedVR2TorchCompileSettings", "SeedVR2 Torch Compile Settings")
SEEDVR2_LOW_VRAM_DIT_RECOMMENDATIONS: Final[tuple[str, ...]] = (
    "seedvr2_ema_3b_fp8_e4m3fn.safetensors",
    "seedvr2_ema_3b_fp8_scaled.safetensors",
)
SEEDVR2_VAE_RECOMMENDATIONS: Final[tuple[str, ...]] = ("ema_vae_fp16.safetensors",)
FRAME_INTERPOLATION_METHOD_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "rife": ("RIFE_VFI", "RIFE VFI", "RIFE", "RifeVFI"),
    "film": ("FILM_VFI", "FILM VFI", "FILM", "FilmVFI"),
    "amt": ("AMT_VFI", "AMT VFI", "AMT", "AMTVFI"),
}
FRAME_INTERPOLATION_EXPERIMENTAL_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "rife_tensorrt": ("RIFE_Tensorrt", "RIFE TensorRT", "RIFETensorrt", "RIFE_TRT", "TensorRT_RIFE", "TRT_RIFE"),
    "gimm_vfi": ("GIMM_VFI", "GIMM VFI", "GIMM-VFI", "GIMMModelLoader", "GIMMInterpolation"),
    "vsrfi_stream": ("VSRFI", "VSRFI_VFI", "VSRFI Stream", "VSRFIStreaming", "VSRFIUpscale", "VSRFIInterpolate"),
}


def _pack_by_id(packs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("pack_id") or ""): item for item in packs if isinstance(item, dict)}


def _method_status(method_id: str, aliases: tuple[str, ...], nodes: set[str], *, stable: bool, default: bool = False) -> dict[str, Any]:
    matched = _match_aliases(nodes, aliases)
    return {
        "method_id": method_id,
        "label": {
            "rife": "RIFE VFI",
            "film": "FILM VFI",
            "amt": "AMT VFI",
            "rife_tensorrt": "RIFE TensorRT",
            "gimm_vfi": "GIMM-VFI",
            "vsrfi_stream": "VSRFI Streaming",
        }.get(method_id, method_id),
        "status": "available" if matched else "missing",
        "available": bool(matched),
        "stable": stable,
        "default_candidate": default,
        "matched_nodes": matched,
        "aliases_checked": list(aliases),
    }


def frame_interpolation_admin_readiness(
    object_info: dict[str, Any] | None,
    packs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return V24.5 Admin readiness diagnostics for Video > Finish > Frame Interpolation.

    This is intentionally diagnostic and Finish-lane scoped. Missing interpolation
    dependencies must never mark base WAN/LTX generation as unavailable.
    """
    nodes = _available_nodes(object_info)
    pack_records = packs if packs is not None else evaluate_video_external_node_packs(object_info or {})
    by_id = _pack_by_id(pack_records)

    load_nodes = _match_aliases(nodes, VIDEO_IO_LOAD_ALIASES)
    save_nodes = _match_aliases(nodes, VIDEO_IO_SAVE_ALIASES)
    video_io_available = bool(by_id.get("video_helper_suite", {}).get("installed") or (load_nodes and save_nodes))

    stable_methods = [
        _method_status(method, aliases, nodes, stable=True, default=(method == "rife"))
        for method, aliases in FRAME_INTERPOLATION_METHOD_ALIASES.items()
    ]
    experimental_methods = [
        _method_status(method, aliases, nodes, stable=False, default=False)
        for method, aliases in FRAME_INTERPOLATION_EXPERIMENTAL_ALIASES.items()
    ]
    stable_by_id = {item["method_id"]: item for item in stable_methods}
    experimental_by_id = {item["method_id"]: item for item in experimental_methods}
    stable_method_available = any(item["available"] for item in stable_methods)
    rife_available = bool(stable_by_id.get("rife", {}).get("available"))
    vfi_pack_available = bool(by_id.get("frame_interpolation", {}).get("installed") or stable_method_available)

    missing_required: list[str] = []
    if not video_io_available:
        missing_required.append("video_helper_suite")
    if not vfi_pack_available:
        missing_required.append("frame_interpolation")
    if vfi_pack_available and not rife_available:
        missing_required.append("rife_vfi_default")

    stable_lane_ready = video_io_available and vfi_pack_available and rife_available
    optional_available = [item["method_id"] for item in stable_methods if item["available"] and item["method_id"] != "rife"]
    experimental_available = [item["method_id"] for item in experimental_methods if item["available"]]

    profile_status = {
        "low": {
            "ready": stable_lane_ready,
            "default_method": "rife",
            "allowed_methods": ["auto", "rife"] if stable_lane_ready else [],
            "allowed_multipliers": [2] if stable_lane_ready else [],
        },
        "medium": {
            "ready": stable_lane_ready,
            "default_method": "rife",
            "allowed_methods": ["auto", "rife", *optional_available] if stable_lane_ready else [],
            "allowed_multipliers": [2, 4] if stable_lane_ready else [],
            "heavy_multiplier_requires_warning": 4,
        },
        "high": {
            "ready": stable_lane_ready,
            "default_method": "rife",
            "allowed_methods": ["auto", "rife", *optional_available] if stable_lane_ready else [],
            "experimental_methods_diagnostic_only": experimental_available,
            "allowed_multipliers": [2, 4] if stable_lane_ready else [],
        },
        "custom": {
            "ready": stable_lane_ready,
            "inherits_from": "medium",
            "requires_expert_mode": True,
            "must_preserve_dependency_gates": True,
        },
    }

    if stable_lane_ready:
        status = "ready"
        summary = "Frame Interpolation stable lane ready: VideoHelperSuite + RIFE VFI detected."
    elif video_io_available or vfi_pack_available:
        status = "partial"
        summary = "Frame Interpolation partially detected; Finish interpolation stays disabled until required stable lane nodes are available."
    else:
        status = "missing"
        summary = "Frame Interpolation dependencies are missing or not checked; base Video Generation is unaffected."

    return {
        "schema_version": FRAME_INTERPOLATION_READINESS_SCHEMA,
        "phase": FRAME_INTERPOLATION_READINESS_PHASE,
        "surface": "video",
        "workspace_app": "finish",
        "extension_id": "video.finish_interpolation",
        "mount_slot": "video.finish.finish_interpolation",
        "lane_slot": "video.finish.interpolate",
        "status": status,
        "ready": stable_lane_ready,
        "finish_interpolation_enabled": stable_lane_ready,
        "base_generation_blocked": False,
        "generation_mount_allowed": False,
        "stable_lane_ready": stable_lane_ready,
        "summary": summary,
        "required_packs": ["video_helper_suite", "frame_interpolation"],
        "missing_required": missing_required,
        "detected": {
            "video_io_load_nodes": load_nodes,
            "video_io_save_nodes": save_nodes,
            "video_helper_suite": bool(by_id.get("video_helper_suite", {}).get("installed")),
            "frame_interpolation_pack": bool(by_id.get("frame_interpolation", {}).get("installed")),
        },
        "methods": {
            "stable": stable_methods,
            "experimental": experimental_methods,
            "available_stable": [item["method_id"] for item in stable_methods if item["available"]],
            "available_experimental": experimental_available,
            "default_method": "rife",
            "default_method_available": rife_available,
            "optional_methods_visible_when_available": ["film", "amt"],
            "experimental_methods_diagnostic_only": list(FRAME_INTERPOLATION_EXPERIMENTAL_ALIASES.keys()),
        },
        "profiles": profile_status,
        "packs": {
            key: by_id.get(key, {})
            for key in ("video_helper_suite", "frame_interpolation", "rife_tensorrt", "gimm_vfi", "vsrfi_stream")
        },
        "rules": [
            "V24.5 readiness is Admin diagnostics for Video > Finish > Frame Interpolation only.",
            "RIFE VFI is the stable default method; FILM and AMT are optional after detection.",
            "RIFE TensorRT, GIMM-VFI, and VSRFI remain diagnostic/experimental only.",
            "Missing Frame Interpolation dependencies must disable only the Finish interpolation action, never base WAN/LTX generation.",
        ],
    }




def _first_matched(nodes: set[str], aliases: tuple[str, ...]) -> str:
    matches = _match_aliases(nodes, aliases)
    return matches[0] if matches else ""


def _object_info_inputs(object_info: dict[str, Any], class_type: str) -> dict[str, Any]:
    if not class_type:
        return {}
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


def _combo_values_from_spec(spec: Any) -> list[str]:
    values: list[str] = []
    if isinstance(spec, (list, tuple)) and spec:
        first = spec[0]
        second = spec[1] if len(spec) > 1 else None
        if isinstance(first, (list, tuple)):
            values.extend(str(item) for item in first if item not in (None, ""))
        elif isinstance(first, str) and first.upper() not in {"COMBO", "STRING", "INT", "FLOAT", "BOOLEAN"}:
            values.append(first)
        if isinstance(second, dict):
            for key in ("values", "options", "choices"):
                choice_values = second.get(key)
                if isinstance(choice_values, (list, tuple)):
                    values.extend(str(item) for item in choice_values if item not in (None, ""))
            default = second.get("default")
            if isinstance(default, str) and default:
                values.append(default)
    elif isinstance(spec, dict):
        for key in ("values", "options", "choices"):
            choice_values = spec.get(key)
            if isinstance(choice_values, (list, tuple)):
                values.extend(str(item) for item in choice_values if item not in (None, ""))
        default = spec.get("default")
        if isinstance(default, str) and default:
            values.append(default)
    return list(dict.fromkeys(values))


def _model_catalog(object_info: dict[str, Any], class_type: str, field_candidates: tuple[str, ...] = ("model", "model_name", "ckpt_name")) -> dict[str, Any]:
    inputs = _object_info_inputs(object_info, class_type)
    folded = {str(key).casefold(): key for key in inputs.keys()}
    field = ""
    values: list[str] = []
    for candidate in field_candidates:
        key = folded.get(candidate.casefold())
        if not key:
            continue
        candidate_values = _combo_values_from_spec(inputs.get(key))
        if candidate_values:
            field = str(key)
            values = candidate_values
            break
        if not field:
            field = str(key)
    return {
        "field": field,
        "values": values,
        "count": len(values),
        "detected": bool(values),
        "catalog_checked": bool(field),
    }


def _has_named_model(catalog: dict[str, Any], recommendations: tuple[str, ...]) -> bool:
    values = [str(item).casefold() for item in catalog.get("values", [])]
    if not values:
        return False
    if any(any(rec.casefold() == value for value in values) for rec in recommendations):
        return True
    # GGUF/quantized SeedVR2 names are acceptable low-VRAM alternatives when present.
    return any("seedvr2" in value and any(token in value for token in ("fp8", "q4", "q5", "q8", "gguf")) for value in values)


def seedvr2_upscale_admin_readiness(
    object_info: dict[str, Any] | None,
    packs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Return Phase 8 Admin readiness diagnostics for Video > Finish > Upscale.

    This contract is Admin-owned and Finish-lane scoped. Missing SeedVR2/video I/O
    dependencies disable only the Upscale action; they never block WAN/LTX generation.
    """
    info = object_info or {}
    nodes = _available_nodes(info)
    pack_records = packs if packs is not None else evaluate_video_external_node_packs(info)
    by_id = _pack_by_id(pack_records)

    load_nodes = _match_aliases(nodes, VIDEO_IO_LOAD_ALIASES)
    component_nodes = _match_aliases(nodes, VIDEO_IO_COMPONENT_ALIASES)
    create_nodes = _match_aliases(nodes, VIDEO_IO_CREATE_ALIASES)
    save_nodes = _match_aliases(nodes, VIDEO_IO_SAVE_ALIASES)
    dit_loader_nodes = _match_aliases(nodes, SEEDVR2_DIT_LOADER_ALIASES)
    vae_loader_nodes = _match_aliases(nodes, SEEDVR2_VAE_LOADER_ALIASES)
    upscaler_nodes = _match_aliases(nodes, SEEDVR2_UPSCALER_ALIASES)
    torch_compile_nodes = _match_aliases(nodes, SEEDVR2_TORCH_COMPILE_ALIASES)

    selected_dit_loader = dit_loader_nodes[0] if dit_loader_nodes else ""
    selected_vae_loader = vae_loader_nodes[0] if vae_loader_nodes else ""
    dit_catalog = _model_catalog(info, selected_dit_loader)
    vae_catalog = _model_catalog(info, selected_vae_loader)

    video_io_ready = bool(load_nodes and component_nodes and create_nodes and save_nodes)
    seedvr2_nodes_ready = bool(dit_loader_nodes and vae_loader_nodes and upscaler_nodes)
    dit_model_detected = bool(dit_catalog["detected"])
    vae_model_detected = bool(vae_catalog["detected"])
    model_catalogs_ready = bool(dit_model_detected and vae_model_detected)
    low_vram_recommended_detected = _has_named_model(dit_catalog, SEEDVR2_LOW_VRAM_DIT_RECOMMENDATIONS)
    recommended_vae_detected = _has_named_model(vae_catalog, SEEDVR2_VAE_RECOMMENDATIONS)

    missing_required: list[str] = []
    if not load_nodes:
        missing_required.append("video_io_load")
    if not component_nodes:
        missing_required.append("video_io_components")
    if not create_nodes:
        missing_required.append("video_io_create")
    if not save_nodes:
        missing_required.append("video_io_save")
    if not dit_loader_nodes:
        missing_required.append("seedvr2_dit_loader")
    if not vae_loader_nodes:
        missing_required.append("seedvr2_vae_loader")
    if not upscaler_nodes:
        missing_required.append("seedvr2_video_upscaler")
    if seedvr2_nodes_ready and not dit_model_detected:
        missing_required.append("seedvr2_dit_model_catalog")
    if seedvr2_nodes_ready and not vae_model_detected:
        missing_required.append("seedvr2_vae_model_catalog")

    ready = video_io_ready and seedvr2_nodes_ready and model_catalogs_ready
    if ready:
        status = "ready"
        summary = "SeedVR2 Upscale ready: video I/O nodes, native SeedVR2 nodes, and DiT/VAE model catalogs detected."
    elif video_io_ready or seedvr2_nodes_ready:
        status = "partial"
        summary = "SeedVR2 Upscale partially detected; Finish Upscale stays disabled until required nodes and model catalogs are available."
    else:
        status = "missing"
        summary = "SeedVR2 Upscale dependencies are missing or not checked; base Video Generation is unaffected."

    required_node_groups = {
        "video_io_load": {"label": "Video loader", "aliases": list(VIDEO_IO_LOAD_ALIASES), "matched_nodes": load_nodes, "ready": bool(load_nodes)},
        "video_io_components": {"label": "Frame/audio/FPS extractor", "aliases": list(VIDEO_IO_COMPONENT_ALIASES), "matched_nodes": component_nodes, "ready": bool(component_nodes)},
        "video_io_create": {"label": "Video creator", "aliases": list(VIDEO_IO_CREATE_ALIASES), "matched_nodes": create_nodes, "ready": bool(create_nodes)},
        "video_io_save": {"label": "Video saver", "aliases": list(VIDEO_IO_SAVE_ALIASES), "matched_nodes": save_nodes, "ready": bool(save_nodes)},
        "seedvr2_dit_loader": {"label": "SeedVR2 DiT loader", "aliases": list(SEEDVR2_DIT_LOADER_ALIASES), "matched_nodes": dit_loader_nodes, "ready": bool(dit_loader_nodes)},
        "seedvr2_vae_loader": {"label": "SeedVR2 VAE loader", "aliases": list(SEEDVR2_VAE_LOADER_ALIASES), "matched_nodes": vae_loader_nodes, "ready": bool(vae_loader_nodes)},
        "seedvr2_video_upscaler": {"label": "SeedVR2 video upscaler", "aliases": list(SEEDVR2_UPSCALER_ALIASES), "matched_nodes": upscaler_nodes, "ready": bool(upscaler_nodes)},
    }
    bindings = {
        "classes": {
            "load_video": load_nodes[0] if load_nodes else "LoadVideo",
            "get_components": component_nodes[0] if component_nodes else "GetVideoComponents",
            "dit_loader": selected_dit_loader or "SeedVR2LoadDiTModel",
            "vae_loader": selected_vae_loader or "SeedVR2LoadVAEModel",
            "seedvr2_upscaler": upscaler_nodes[0] if upscaler_nodes else "SeedVR2VideoUpscaler",
            "upscaler": upscaler_nodes[0] if upscaler_nodes else "SeedVR2VideoUpscaler",
            "create_video": create_nodes[0] if create_nodes else "CreateVideo",
            "save_video": save_nodes[0] if save_nodes else "SaveVideo",
            "saver": save_nodes[0] if save_nodes else "SaveVideo",
            "torch_compile": torch_compile_nodes[0] if torch_compile_nodes else "SeedVR2TorchCompileSettings",
        },
        "available": {
            "load_video": bool(load_nodes),
            "get_components": bool(component_nodes),
            "dit_loader": bool(dit_loader_nodes),
            "vae_loader": bool(vae_loader_nodes),
            "seedvr2_upscaler": bool(upscaler_nodes),
            "create_video": bool(create_nodes),
            "save_video": bool(save_nodes),
            "torch_compile": bool(torch_compile_nodes),
            "all_required": ready,
        },
        "engine": "seedvr2",
        "graph_policy": {
            "uses_native_seedvr2_loaders": True,
            "uses_get_video_components": True,
            "preserves_audio": True,
            "preserves_source_fps_by_default": True,
            "interpolation_branch_allowed": False,
            "basic_fallback_allowed": False,
        },
    }

    return {
        "schema_version": SEEDVR2_UPSCALE_ADMIN_READINESS_SCHEMA,
        "workflow_schema_version": "neo.video.finish.seedvr2_upscale.workflow.v25_9_19_phase_7",
        "phase": SEEDVR2_UPSCALE_ADMIN_READINESS_PHASE,
        "surface": "video",
        "workspace_app": "finish",
        "extension_id": "video.finish_upscale",
        "mount_slot": "video.finish.finish_upscale",
        "lane_slot": "video.finish.upscale",
        "status": status,
        "ready": ready,
        "stable_lane_ready": ready,
        "finish_upscale_enabled": ready,
        "base_generation_blocked": False,
        "generation_mount_allowed": False,
        "summary": summary,
        "required_packs": ["video_helper_suite", "seedvr2_upscaler"],
        "required": list(required_node_groups.keys()),
        "required_nodes": {key: value["label"] for key, value in required_node_groups.items()},
        "required_node_groups": required_node_groups,
        "missing_required": missing_required,
        "detected": {
            "video_io_load_nodes": load_nodes,
            "video_io_component_nodes": component_nodes,
            "video_io_create_nodes": create_nodes,
            "video_io_save_nodes": save_nodes,
            "seedvr2_dit_loader_nodes": dit_loader_nodes,
            "seedvr2_vae_loader_nodes": vae_loader_nodes,
            "seedvr2_upscaler_nodes": upscaler_nodes,
            "seedvr2_torch_compile_nodes": torch_compile_nodes,
            "video_helper_suite": bool(by_id.get("video_helper_suite", {}).get("installed")) or video_io_ready,
            "seedvr2_upscaler_pack": bool(by_id.get("seedvr2_upscaler", {}).get("installed")) or seedvr2_nodes_ready,
        },
        "video_io": {
            "ready": video_io_ready,
            "load": load_nodes,
            "components": component_nodes,
            "create": create_nodes,
            "save": save_nodes,
        },
        "seedvr2_nodes": {
            "ready": seedvr2_nodes_ready,
            "dit_loader": dit_loader_nodes,
            "vae_loader": vae_loader_nodes,
            "upscaler": upscaler_nodes,
        },
        "models": {
            "ready": model_catalogs_ready,
            "dit": dit_catalog,
            "vae": vae_catalog,
            "recommended_low_vram_dit_models": list(SEEDVR2_LOW_VRAM_DIT_RECOMMENDATIONS),
            "recommended_vae_models": list(SEEDVR2_VAE_RECOMMENDATIONS),
            "recommended_low_vram_dit_detected": low_vram_recommended_detected,
            "recommended_vae_detected": recommended_vae_detected,
            "low_vram_model_hint": "Install seedvr2_ema_3b_fp8_e4m3fn.safetensors or a SeedVR2 GGUF/Q4-Q8 variant for low-to-mid VRAM runs.",
        },
        "profiles": {
            "low": {"ready": ready, "recommended_model_detected": low_vram_recommended_detected, "target": "safe_720"},
            "medium": {"ready": ready, "recommended_model_detected": low_vram_recommended_detected, "target": "hd_1080"},
            "high": {"ready": ready, "requires_more_vram": True, "target": "hd_1080_plus"},
            "custom": {"ready": ready, "requires_expert_mode": True, "must_preserve_dependency_gates": True},
        },
        "bindings": bindings,
        "packs": {"video_helper_suite": by_id.get("video_helper_suite", {}), "seedvr2_upscaler": by_id.get("seedvr2_upscaler", {})},
        "optional": {"torch_compile": bool(torch_compile_nodes), "torch_compile_nodes": torch_compile_nodes},
        "fallback_basic_available": bool(_match_aliases(nodes, ("ImageScaleBy", "ImageScale", "ImageUpscaleWithModel", "UpscaleImageBy"))),
        "fallback_basic_ui_visible": False,
        "fallback_basic_request_allowed": False,
        "interpolation_nodes_ignored": True,
        "rules": [
            "Phase 8 readiness is Admin diagnostics for Video > Finish > Upscale only.",
            "Missing SeedVR2 dependencies disable only the Finish Upscale action, never WAN/LTX generation.",
            "Video UI stays clean; detailed node/model diagnostics belong in Admin > Extensions.",
            "Torch compile is optional and should not block readiness.",
            "At least one SeedVR2 DiT model and the VAE model catalog must be detected before runtime queue is marked ready.",
        ],
    }

def evaluate_video_external_node_packs(object_info: dict[str, Any] | None) -> list[dict[str, Any]]:
    nodes = _available_nodes(object_info)
    records: list[dict[str, Any]] = []
    for pack in VIDEO_EXTERNAL_NODE_PACKS:
        matched = _match_aliases(nodes, pack.aliases)
        status = "installed" if matched else "missing"
        if pack.install_role == "experimental" and matched:
            status = "installed_experimental"
        record = pack.payload()
        record.update({
            "status": status,
            "installed": bool(matched),
            "matched_nodes": matched,
            "missing_aliases": [alias for alias in pack.aliases if alias not in matched],
            "manager_action": "available" if matched else "install_in_comfyui_manager",
            "neo_runtime_enabled": False,
            "guard_reason": "V11 detects and classifies external node packs; runtime use is enabled only by later route phases.",
        })
        records.append(record)
    return records


def video_external_node_manager_payload(
    profile_id: str | None = None,
    timeout: float = 2.0,
    object_info_override: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = video_backend_profile_payload(profile_id)
    base_url = profile["connection"].get("base_url") or DEFAULT_COMFY_URL
    reachable = False
    object_info: dict[str, Any] = {}
    errors: list[str] = []
    warnings: list[str] = []

    if object_info_override is not None:
        object_info = object_info_override
        reachable = True
    else:
        try:
            object_info = _get_json(base_url, "/object_info", timeout)
            reachable = True
        except Exception as exc:  # best-effort local probe
            errors.append(f"ComfyUI /object_info discovery failed: {exc}")

    packs = evaluate_video_external_node_packs(object_info)
    frame_interpolation_readiness = frame_interpolation_admin_readiness(object_info, packs=packs)
    seedvr2_upscale_readiness = seedvr2_upscale_admin_readiness(object_info, packs=packs)
    installed = [item for item in packs if item["installed"]]
    recommended_missing = [item for item in packs if item["install_role"] == "recommended" and not item["installed"]]
    if recommended_missing:
        warnings.append("Recommended video I/O pack missing: " + ", ".join(item["display_name"] for item in recommended_missing))
    category_counts: dict[str, int] = {}
    for item in packs:
        if item["installed"]:
            category_counts[item["category"]] = category_counts.get(item["category"], 0) + 1

    return {
        "ok": not errors,
        "schema_version": SCHEMA_VERSION,
        "phase": PHASE,
        "surface": "video",
        "checked_at": _now(),
        "backend": {"profile": profile, "base_url": base_url, "reachable": reachable},
        "packs": packs,
        "frame_interpolation_readiness": frame_interpolation_readiness,
        "seedvr2_upscale_readiness": seedvr2_upscale_readiness,
        "finish_readiness": {"frame_interpolation": frame_interpolation_readiness, "upscale": seedvr2_upscale_readiness},
        "summary": {
            "total": len(packs),
            "installed": len(installed),
            "missing": len(packs) - len(installed),
            "recommended_missing": len(recommended_missing),
            "category_counts": category_counts,
        },
        "rules": [
            "V11 detects external video node packs through ComfyUI /object_info only.",
            "Neo does not install, update, or remove ComfyUI custom nodes from the Video workspace.",
            "Installed external packs remain runtime-guarded until a later Video phase explicitly enables their lane.",
            "Base WAN/LTX Txt2Vid and Img2Vid generation must not depend on optional Finish/Depth/Assist packs.",
            "V24.5 exposes Frame Interpolation method/profile readiness without enabling experimental methods or blocking base generation.",
            "V25.9.19 Phase 8 exposes SeedVR2 Upscale node/model readiness in Admin without cluttering the Video Finish UI or blocking base generation.",
        ],
        "errors": errors,
        "warnings": warnings,
    }
