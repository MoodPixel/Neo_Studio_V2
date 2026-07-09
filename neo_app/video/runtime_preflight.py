from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Final

from neo_app.video.backend_probe import video_backend_probe_payload
from neo_app.video.performance_profiles import video_performance_preflight_payload
from neo_app.video.route_matrix import find_video_route, normalize_video_family, normalize_video_generation_type, normalize_video_loader
from neo_app.video.vram_engine import apply_video_vram_engine

WAN22_GGUF_ROUTE_ID: Final[str] = "wan22.gguf.img2vid_14b_dual_noise"
RUNTIME_PREFLIGHT_SCHEMA_VERSION: Final[str] = "neo.video.runtime_preflight.vg13"
RUNTIME_PREFLIGHT_PHASE: Final[str] = "V-G13"

WAN22_GGUF_FIRST_TEST_PRESET: Final[dict[str, Any]] = {
    "family": "wan22",
    "loader": "gguf",
    "generation_type": "img2vid",
    "vram_profile": "low",
    "width": 512,
    "height": 288,
    "frames": 25,
    "fps": 12,
    "steps": 4,
    "guidance": 1.0,
    "split_step": 2,
    "sampler": "euler",
    "scheduler": "simple",
    "decode_mode": "tiled",
    "tile_size": 384,
    "temporal_tile_size": 4096,
    "batch_count": 1,
    "output_format": "webm",
    "filename_prefix": "Neo_Video_WAN22_GGUF_FirstTest",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _is_wan22_gguf_i2v(payload: dict[str, Any] | None) -> bool:
    data = payload if isinstance(payload, dict) else {}
    nf = normalize_video_family(data.get("family"))
    nl = normalize_video_loader(data.get("loader"))
    nt = normalize_video_generation_type(data.get("generation_type") or data.get("mode"))
    route = find_video_route(nf, nl, nt, include_planned=True)
    return bool(route and route.route_id == WAN22_GGUF_ROUTE_ID)


def wan22_gguf_first_test_preset_payload(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a queue-safe first-test payload for 12GB WAN 2.2 GGUF I2V validation.

    The preset intentionally preserves user-selected source/model/prompt fields while lowering
    generation load. It is a payload transform, not a hidden global preference.
    """
    incoming = deepcopy(payload if isinstance(payload, dict) else {})
    preserved_keys = {
        "prompt",
        "positive_prompt",
        "negative_prompt",
        "source_image",
        "source_image_name",
        "source_image_comfy_name",
        "comfy_source_image_name",
        "image",
        "image_name",
        "profile_id",
        "high_noise_model",
        "low_noise_model",
        "wan_high_noise_gguf",
        "wan_low_noise_gguf",
        "clip_name",
        "text_encoder",
        "vae_name",
        "enable_lightx2v",
        "enable_video_lora",
        "video_lora_mode",
        "video_lora_model",
        "video_lora_strength",
        "video_lora_target",
        "high_noise_lora",
        "low_noise_lora",
        "high_noise_lora_strength",
        "low_noise_lora_strength",
        "dry_run",
        "queue_preflight",
        "allow_manual_danger",
        "performance_profile",
        "enable_sage_attention",
        "sage_attention_mode",
        "sage_attention_target",
        "enable_teacache",
        "teacache_profile",
        "teacache_target",
        "enable_cpu_offload",
        "enable_vae_offload",
        "enable_block_swap",
        "block_swap_target",
        "block_swap_blocks",
        "enable_torch_compile",
    }
    preserved = {key: incoming[key] for key in preserved_keys if key in incoming and incoming[key] not in (None, "")}
    effective = {**WAN22_GGUF_FIRST_TEST_PRESET, **preserved, "first_test_mode": True, "queue_preflight": True}
    if not effective.get("prompt") and effective.get("positive_prompt"):
        effective["prompt"] = effective.get("positive_prompt")
    return {
        "ok": True,
        "schema_version": "neo.video.wan22_gguf_first_test_preset.vg7",
        "phase": RUNTIME_PREFLIGHT_PHASE,
        "surface": "video",
        "route_id": WAN22_GGUF_ROUTE_ID,
        "preset_id": "wan22_gguf_i2v14_12gb_first_test",
        "preset": dict(WAN22_GGUF_FIRST_TEST_PRESET),
        "payload": effective,
        "rules": [
            "The first-test preset is for queue validation on 12GB GPUs, not final quality.",
            "It preserves selected source image, high/low GGUF models, CLIP, VAE, LoRA, and prompt fields.",
            "It uses 512x288, 25 frames, 12 fps, 4 steps, CFG 1.0, split step 2, batch 1, and tiled decode.",
        ],
    }


def apply_wan22_gguf_first_test_preset(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = deepcopy(payload if isinstance(payload, dict) else {})
    if _as_bool(data.get("first_test_mode")) or _as_bool(data.get("safe_test")) or _as_bool(data.get("queue_test")):
        return wan22_gguf_first_test_preset_payload(data)["payload"]
    return data


def video_runtime_preflight_payload(
    payload: dict[str, Any] | None = None,
    *,
    object_info_override: dict[str, Any] | None = None,
    system_stats_override: dict[str, Any] | None = None,
    compile_payload: dict[str, Any] | None = None,
    timeout: float = 2.5,
) -> dict[str, Any]:
    """Run a no-queue safety gate before sending WAN 2.2 GGUF prompts to ComfyUI."""
    raw = deepcopy(payload if isinstance(payload, dict) else {})
    effective = apply_wan22_gguf_first_test_preset(raw)
    nf = normalize_video_family(effective.get("family"))
    nl = normalize_video_loader(effective.get("loader"))
    nt = normalize_video_generation_type(effective.get("generation_type") or effective.get("mode"))
    route = find_video_route(nf, nl, nt, include_planned=True)
    route_id = route.route_id if route else ""

    errors: list[str] = []
    warnings: list[str] = []
    action_items: list[str] = []

    if route_id != WAN22_GGUF_ROUTE_ID:
        errors.append(f"V-G7 runtime preflight currently guards {WAN22_GGUF_ROUTE_ID}; selected route is {route_id or 'unknown'}.")
    if not (effective.get("source_image") or effective.get("image")):
        errors.append("WAN 2.2 GGUF Img2Vid requires a source image before queueing.")
        action_items.append("Upload/select a Video source image, then run preflight again.")

    vram = apply_video_vram_engine(effective, family=nf, loader=nl, generation_type=nt, vram_profile=effective.get("vram_profile"))
    normalized_parameters = dict(vram.get("normalized_parameters") or {})
    risk = dict(vram.get("risk") or {})
    risk_tier = str(risk.get("tier") or "unknown")
    if risk_tier == "danger" and not _as_bool(effective.get("allow_manual_danger")):
        errors.append("Selected settings are in the danger tier for queueing; apply the WAN GGUF first-test preset or reduce frames/resolution/steps.")
        action_items.append("Use the Safe WAN GGUF test preset before the first local run.")
    elif risk_tier == "heavy":
        warnings.append("Selected settings are heavy for a first local GGUF test; first run should use the safe test preset.")

    performance_preflight = video_performance_preflight_payload(effective)
    if not performance_preflight.get("queue_ready", True):
        errors.append("Video Performance adapter blocked queueing because one or more planned optimizers are enabled before their graph adapter phase.")
        for item in performance_preflight.get("errors", []):
            if item not in errors:
                errors.append(str(item))
        action_items.extend(str(item) for item in performance_preflight.get("action_items", []) if item)
    for item in performance_preflight.get("warnings", []):
        if item not in warnings:
            warnings.append(str(item))

    # Backend/model readiness: prefer compile payload if supplied, but still ask V-G6 probe when possible.
    probe: dict[str, Any] = {}
    if compile_payload and isinstance(compile_payload, dict):
        route_readiness = compile_payload.get("route_readiness") if isinstance(compile_payload.get("route_readiness"), dict) else {}
        selected_models = compile_payload.get("selected_models") if isinstance(compile_payload.get("selected_models"), dict) else {}
        dual_noise = compile_payload.get("dual_noise_mapping") if isinstance(compile_payload.get("dual_noise_mapping"), dict) else {}
        backend = compile_payload.get("backend") if isinstance(compile_payload.get("backend"), dict) else {}
        probe = {
            "backend": {"reachable": bool(backend.get("base_url")), "status": "compiled_sidecar"},
            "route_readiness": route_readiness,
            "gguf_model_probe": {
                "ready": bool(selected_models.get("dual_noise_ready", dual_noise.get("ready", True))),
                "dual_noise_ready": bool(selected_models.get("dual_noise_ready", dual_noise.get("ready", True))),
                "selected_pair_visible": bool(selected_models.get("dual_noise_ready", dual_noise.get("ready", True))),
                "models": selected_models,
            },
            "warnings": compile_payload.get("warnings", []) if isinstance(compile_payload.get("warnings"), list) else [],
            "errors": compile_payload.get("errors", []) if isinstance(compile_payload.get("errors"), list) else [],
        }
    else:
        probe = video_backend_probe_payload(
            family=nf,
            loader=nl,
            generation_type=nt,
            profile_id=effective.get("profile_id"),
            timeout=timeout,
            object_info_override=object_info_override,
            system_stats_override=system_stats_override,
            high_noise_model=effective.get("high_noise_model") or effective.get("wan_high_noise_gguf"),
            low_noise_model=effective.get("low_noise_model") or effective.get("wan_low_noise_gguf"),
            clip_name=effective.get("clip_name") or effective.get("text_encoder"),
            vae_name=effective.get("vae_name"),
            enable_lightx2v=_as_bool(effective.get("enable_lightx2v")) or str(effective.get("video_lora_mode") or "").lower().replace("-", "_") == "lightx2v_4step",
            enable_video_lora=_as_bool(effective.get("enable_video_lora")),
            video_lora_mode=effective.get("video_lora_mode"),
            video_lora_model=effective.get("video_lora_model"),
            video_lora_target=effective.get("video_lora_target"),
            high_noise_lora=effective.get("high_noise_lora"),
            low_noise_lora=effective.get("low_noise_lora"),
            performance_profile=effective.get("performance_profile"),
            enable_sage_attention=_as_bool(effective.get("enable_sage_attention")),
            sage_attention_mode=effective.get("sage_attention_mode"),
            sage_attention_target=effective.get("sage_attention_target"),
            enable_teacache=_as_bool(effective.get("enable_teacache")),
            teacache_profile=effective.get("teacache_profile"),
            enable_cpu_offload=_as_bool(effective.get("enable_cpu_offload")),
            enable_vae_offload=_as_bool(effective.get("enable_vae_offload")),
            enable_block_swap=_as_bool(effective.get("enable_block_swap")),
            enable_torch_compile=_as_bool(effective.get("enable_torch_compile")),
        )

    backend_status = str((probe.get("backend") or {}).get("status") or "unknown")
    backend_reachable = bool((probe.get("backend") or {}).get("reachable"))
    if not backend_reachable and object_info_override is None and not compile_payload:
        errors.append("ComfyUI backend is not reachable; queue blocked before /prompt.")
        action_items.append("Start/connect ComfyUI Portable, then refresh the Video backend probe.")
    if backend_status not in {"ready", "ready_with_warnings", "compiled_sidecar"} and backend_reachable:
        errors.append(f"Backend probe is not queue-ready: {backend_status}.")

    route_ready = bool((probe.get("route_readiness") or {}).get("ready", False))
    if not route_ready:
        errors.append("Selected route is missing required ComfyUI nodes; queue blocked before /prompt.")
    gguf_probe = probe.get("gguf_model_probe") if isinstance(probe.get("gguf_model_probe"), dict) else {}
    if gguf_probe and not gguf_probe.get("ready"):
        errors.append("WAN GGUF model readiness failed; high/low model pair or visible model catalog is not ready.")
    if gguf_probe and not gguf_probe.get("dual_noise_ready", True):
        errors.append("WAN GGUF dual-noise pair is not ready; choose separate high-noise and low-noise GGUF models.")

    clip_guard = compile_payload.get("clip_loader_guard") if isinstance(compile_payload, dict) and isinstance(compile_payload.get("clip_loader_guard"), dict) else {}
    if clip_guard and not clip_guard.get("ok", True):
        errors.append("WAN text encoder guard failed; CLIPLoader node must use type='wan' before queueing.")
        action_items.extend(str(item) for item in clip_guard.get("action_items", []) if item)
        for item in clip_guard.get("errors", []) if isinstance(clip_guard.get("errors"), list) else []:
            if item and item not in errors:
                errors.append(str(item))

    for item in (probe.get("warnings") if isinstance(probe.get("warnings"), list) else []):
        if item and item not in warnings:
            warnings.append(str(item))
    for item in (probe.get("errors") if isinstance(probe.get("errors"), list) else []):
        if item and item not in errors:
            errors.append(str(item))
    for item in (probe.get("action_items") if isinstance(probe.get("action_items"), list) else []):
        if item and item not in action_items:
            action_items.append(str(item))

    queue_allowed = not errors
    return {
        "ok": True,
        "schema_version": RUNTIME_PREFLIGHT_SCHEMA_VERSION,
        "phase": RUNTIME_PREFLIGHT_PHASE,
        "surface": "video",
        "checked_at": _now(),
        "route_id": route_id or WAN22_GGUF_ROUTE_ID,
        "queue_allowed": queue_allowed,
        "first_test_mode": _as_bool(effective.get("first_test_mode")),
        "backend_status": backend_status,
        "vram": {
            "profile": normalized_parameters.get("vram_profile") or effective.get("vram_profile") or "low",
            "risk": risk,
            "normalized_parameters": normalized_parameters,
            "changes": vram.get("changes", []),
            "warnings": vram.get("warnings", []),
        },
        "probe_summary": {
            "backend_reachable": backend_reachable,
            "route_ready": route_ready,
            "gguf_ready": bool(gguf_probe.get("ready", False)) if gguf_probe else False,
            "dual_noise_ready": bool(gguf_probe.get("dual_noise_ready", False)) if gguf_probe else False,
            "selected_pair_visible": bool(gguf_probe.get("selected_pair_visible", False)) if gguf_probe else False,
        },
        "performance_preflight": performance_preflight,
        "effective_payload": effective,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys([*warnings, *(str(item) for item in vram.get("warnings", []) if item)])),
        "action_items": list(dict.fromkeys(action_items)),
        "rules": [
            "V-G13 runtime preflight never queues prompts; it gates /prompt submission.",
            "V-G13 performance preflight allows active Sage Attention, WAN TeaCache, and WAN low-VRAM adapters while blocking unavailable optimizer nodes.",
            "WAN GGUF queueing requires source image, reachable backend, required nodes, visible high/low GGUF models, ready dual-noise mapping, and non-danger VRAM risk.",
            "First local testing should use the 12GB first-test preset before raising duration, resolution, or steps.",
            "WAN text encoding is guarded so node 129:84 must stay CLIPLoader type='wan' instead of SD1/default CLIP.",
        ],
    }
