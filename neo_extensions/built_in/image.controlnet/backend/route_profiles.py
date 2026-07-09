from __future__ import annotations

from copy import deepcopy
from typing import Any

AVAILABLE = "available"
EXPERIMENTAL = "experimental_available"
IMPLEMENTATION_TARGET = "implementation_target"
PLANNED_GATED = "planned_gated"
PROVIDER_GATED = "provider_gated"
UNSUPPORTED = "unsupported"
ACTIVE_STATES = {AVAILABLE, EXPERIMENTAL}
KNOWN_STATES = {AVAILABLE, EXPERIMENTAL, IMPLEMENTATION_TARGET, PLANNED_GATED, PROVIDER_GATED, UNSUPPORTED}

TASK_MAP_CONTROL = "map_control"
TASK_INPAINT_CONTROL = "inpaint_control"
TASK_OUTPAINT_CONTROL = "outpaint_control"
CONTROLNET_TASKS = (TASK_MAP_CONTROL, TASK_INPAINT_CONTROL, TASK_OUTPAINT_CONTROL)

BACKEND_ALIASES = {
    "comfyui_portable": "comfyui",
    "comfy": "comfyui",
    "automatic1111": "a1111",
    "sd_webui": "a1111",
}
MODE_ALIASES = {
    "txt2img": "generate",
    "text_to_image": "generate",
    "image_to_image": "img2img",
}

# P9.0 route-profile contract:
# - preprocessors/map creation can be treated separately from the model apply path
# - actual ControlNet graph mutation is profile/task-adapter driven
# - base routes that now exist but lack an explicit ControlNet adapter are
#   implementation targets, not fake global/global-SD fallbacks.
DEFAULT_PROFILE: dict[str, Any] = {
    "profile_id": "generic_controlnet_implementation_target",
    "family_group": "generic",
    "rollout_state": IMPLEMENTATION_TARGET,
    "map_adapter": "implementation_target",
    "inpaint_adapter": "implementation_target",
    "outpaint_adapter": "implementation_target",
    "model_patch_policy": "route_specific_required",
    "model_dir": "controlnet_or_model_patches_by_profile",
    "cfg_policy": "preserve_route_default",
    "negative_prompt_policy": "preserve_if_route_supports_it",
    "preprocessor_policy": "global_map_stage_allowed_when_nodes_or_local_fallback_exist",
    "allowed_controls": ["canny", "depth", "openpose", "lineart", "lineart_anime", "softedge", "scribble", "normalbae", "tile"],
    "task_states": {
        TASK_MAP_CONTROL: IMPLEMENTATION_TARGET,
        TASK_INPAINT_CONTROL: IMPLEMENTATION_TARGET,
        TASK_OUTPAINT_CONTROL: IMPLEMENTATION_TARGET,
    },
    "notes": [
        "P9.0 route-profile contract exists, but this family/loader still needs a dedicated ControlNet enablement pass.",
        "Control map preprocessing may be available, but workflow patching must not fall back to SD-style ControlNetApply unless the route profile says it is safe.",
    ],
}

_ROUTE_PROFILES: dict[tuple[str, str], dict[str, Any]] = {
    ("sdxl", "checkpoint"): {
        **DEFAULT_PROFILE,
        "profile_id": "sdxl_checkpoint_controlnet",
        "family_group": "sd",
        "rollout_state": AVAILABLE,
        "map_adapter": "sd_controlnet_apply",
        "inpaint_adapter": "sd_checkpoint_mask_canvas_control",
        "outpaint_adapter": "sd_checkpoint_mask_canvas_control",
        "model_dir": "controlnet",
        "cfg_policy": "sd_checkpoint_cfg",
        "negative_prompt_policy": "preserve",
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "inpaint": AVAILABLE, "outpaint": IMPLEMENTATION_TARGET},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": ["SDXL checkpoint ControlNet uses the standard ControlNetLoader/ControlNetApply route plus SD mask/canvas adapters for inpaint/outpaint."],
    },
    ("sd15", "checkpoint"): {
        **DEFAULT_PROFILE,
        "profile_id": "sd15_checkpoint_controlnet",
        "family_group": "sd",
        "rollout_state": EXPERIMENTAL,
        "map_adapter": "sd_controlnet_apply",
        "inpaint_adapter": "sd_checkpoint_mask_canvas_control",
        "outpaint_adapter": "sd_checkpoint_mask_canvas_control",
        "model_dir": "controlnet",
        "cfg_policy": "sd_checkpoint_cfg",
        "negative_prompt_policy": "preserve",
        "task_states": {
            TASK_MAP_CONTROL: {"generate": EXPERIMENTAL, "img2img": EXPERIMENTAL, "inpaint": EXPERIMENTAL, "outpaint": IMPLEMENTATION_TARGET},
            TASK_INPAINT_CONTROL: {"inpaint": EXPERIMENTAL},
            TASK_OUTPAINT_CONTROL: {"outpaint": EXPERIMENTAL},
        },
        "notes": ["SD 1.5 checkpoint ControlNet is enabled as experimental parity support."],
    },
    ("flux", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux_components_controlnet",
        "family_group": "flux",
        "rollout_state": AVAILABLE,
        "map_adapter": "flux_controlnet_components",
        "inpaint_adapter": "flux_alimama_inpaint",
        "outpaint_adapter": "flux_alimama_canvas",
        "model_dir": "controlnet",
        "cfg_policy": "flux_guidance_safe",
        "negative_prompt_policy": "zero_or_hide",
        "allowed_controls": ["canny", "depth", "openpose", "lineart", "softedge", "scribble", "normalbae", "tile"],
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": [
            "P9.1 promotes Flux 1 component/safetensors ControlNet routes. Map control uses Flux-compatible ControlNet loader/apply nodes; inpaint/outpaint use the Alimama Flux inpaint/canvas adapter path.",
            "Flux routes must not fall back to SD checkpoint ControlNet defaults or SD-style CFG assumptions.",
        ],
    },
    ("flux", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux_gguf_controlnet",
        "family_group": "flux",
        "rollout_state": AVAILABLE,
        "map_adapter": "flux_controlnet_gguf",
        "inpaint_adapter": "flux_alimama_inpaint",
        "outpaint_adapter": "flux_alimama_canvas",
        "model_dir": "controlnet",
        "cfg_policy": "flux_guidance_safe",
        "negative_prompt_policy": "zero_or_hide",
        "allowed_controls": ["canny", "depth", "openpose", "lineart", "softedge", "scribble", "normalbae", "tile"],
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": [
            "P9.1 aligns Flux 1 GGUF ControlNet with Flux component routes under the route-profile contract.",
            "Inpaint/outpaint use the same Flux Alimama mask/canvas adapter path; map control stays Flux-compatible and does not borrow SD route behavior.",
        ],
    },
    ("flux2_klein", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux2_klein_components_controlnet",
        "family_group": "flux2_klein",
        "rollout_state": AVAILABLE,
        "map_adapter": "flux2_fun_union_components",
        "inpaint_adapter": "flux2_fun_union_inpaint",
        "outpaint_adapter": "flux2_fun_union_canvas",
        "model_dir": "controlnet_or_model_patches",
        "cfg_policy": "flux2_fun_union_safe",
        "negative_prompt_policy": "zero_or_hide",
        "allowed_controls": ["canny", "depth", "openpose", "lineart", "softedge", "scribble", "normalbae", "tile", "mlsd", "gray"],
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": [
            "P9.3 promotes Flux Klein component/safetensors ControlNet through the Flux2/Klein Fun Union adapter lane.",
            "Flux Klein must not fall back to Flux 1 Alimama, SD ControlNet, Qwen, or ZImage adapters.",
        ],
    },
    ("flux2_klein", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "flux2_klein_gguf_controlnet",
        "family_group": "flux2_klein",
        "rollout_state": AVAILABLE,
        "map_adapter": "flux2_fun_union_gguf",
        "inpaint_adapter": "flux2_fun_union_inpaint",
        "outpaint_adapter": "flux2_fun_union_canvas",
        "model_dir": "controlnet_or_model_patches",
        "cfg_policy": "flux2_fun_union_safe",
        "negative_prompt_policy": "zero_or_hide",
        "allowed_controls": ["canny", "depth", "openpose", "lineart", "softedge", "scribble", "normalbae", "tile", "mlsd", "gray"],
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": [
            "P9.3 promotes Flux Klein GGUF ControlNet through the same Flux2/Klein Fun Union adapter policy as components.",
            "GGUF changes only the model loader lane; ControlNet adapter selection remains Flux2/Klein-specific.",
        ],
    },
    ("qwen_image", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_image_components_controlnet",
        "family_group": "qwen",
        "map_adapter": "qwen_instantx_or_standard_map_control",
        "inpaint_adapter": "qwen_diffsynth_inpaint_patch",
        "outpaint_adapter": "qwen_diffsynth_canvas_patch",
        "model_dir": "model_patches",
        "cfg_policy": "qwen_safe_low_cfg",
        "negative_prompt_policy": "route_native",
        "rollout_state": AVAILABLE,
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": ["P9.2 promotes Qwen Image Edit component/safetensors ControlNet routes. Map control uses Qwen InstantX/native ControlNet or standard ControlNet nodes; inpaint/outpaint use the Qwen DiffSynth/InstantX adapter path and do not borrow SD/Flux behavior."],
    },
    ("qwen_image", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_image_gguf_controlnet",
        "family_group": "qwen",
        "rollout_state": AVAILABLE,
        "map_adapter": "qwen_instantx_or_standard_map_control_gguf",
        "inpaint_adapter": "qwen_diffsynth_inpaint_patch",
        "outpaint_adapter": "qwen_diffsynth_canvas_patch",
        "model_dir": "model_patches",
        "cfg_policy": "qwen_safe_low_cfg",
        "negative_prompt_policy": "route_native",
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": ["P9.2 promotes Qwen Image Edit GGUF ControlNet routes and keeps DiffSynth/InstantX inpaint/outpaint adapter support active."],
    },
    ("qwen_rapid_aio", "checkpoint_aio"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_rapid_aio_checkpoint_controlnet",
        "family_group": "qwen",
        "rollout_state": AVAILABLE,
        "map_adapter": "qwen_rapid_aio_instantx_or_standard_map_control",
        "inpaint_adapter": "qwen_diffsynth_inpaint_patch",
        "outpaint_adapter": "qwen_diffsynth_canvas_patch",
        "model_dir": "model_patches",
        "cfg_policy": "qwen_rapid_aio_low_cfg",
        "negative_prompt_policy": "route_native",
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": ["P9.2 promotes Qwen Rapid AIO checkpoint ControlNet routes while preserving bundled checkpoint cleanup and hiding external encoder requirements."],
    },
    ("qwen_rapid_aio", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_rapid_aio_gguf_controlnet",
        "family_group": "qwen",
        "rollout_state": AVAILABLE,
        "map_adapter": "qwen_rapid_aio_gguf_instantx_or_standard_map_control",
        "inpaint_adapter": "qwen_diffsynth_inpaint_patch",
        "outpaint_adapter": "qwen_diffsynth_canvas_patch",
        "model_dir": "model_patches",
        "cfg_policy": "qwen_safe_low_cfg",
        "negative_prompt_policy": "route_native",
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": ["P9.2 promotes Qwen Rapid AIO GGUF ControlNet routes with the same Qwen-safe adapter policy as the checkpoint AIO lane."],
    },
    ("qwen_image_edit_2509", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_2509_components_controlnet",
        "family_group": "qwen",
        "map_adapter": "qwen_2509_instantx_or_native_map_control",
        "inpaint_adapter": "qwen_2509_diffsynth_inpaint_patch",
        "outpaint_adapter": "qwen_2509_diffsynth_canvas_patch",
        "model_dir": "model_patches",
        "cfg_policy": "qwen_safe_low_cfg",
        "negative_prompt_policy": "route_native",
        "rollout_state": AVAILABLE,
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": ["P9.2 promotes Qwen Image Edit 2509 component ControlNet routes while preserving its Image 1-3 policy only where the base edit route supports it."],
    },
    ("qwen_image_edit_2509", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "qwen_2509_gguf_controlnet",
        "family_group": "qwen",
        "rollout_state": AVAILABLE,
        "map_adapter": "qwen_2509_gguf_instantx_or_native_map_control",
        "inpaint_adapter": "qwen_2509_diffsynth_inpaint_patch",
        "outpaint_adapter": "qwen_2509_diffsynth_canvas_patch",
        "model_dir": "model_patches",
        "cfg_policy": "qwen_safe_low_cfg",
        "negative_prompt_policy": "route_native",
        "task_states": {
            TASK_MAP_CONTROL: {"generate": AVAILABLE, "img2img": AVAILABLE, "edit": AVAILABLE, "inpaint": AVAILABLE, "outpaint": AVAILABLE},
            TASK_INPAINT_CONTROL: {"inpaint": AVAILABLE},
            TASK_OUTPAINT_CONTROL: {"outpaint": AVAILABLE},
        },
        "notes": ["P9.2 promotes Qwen Image Edit 2509 GGUF ControlNet routes with Qwen-safe DiffSynth/InstantX adapters."],
    },
    ("z_image", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_components_controlnet_target",
        "family_group": "z_image",
        "map_adapter": "z_image_fun_union_or_patch_target",
        "inpaint_adapter": "z_image_fun_union_inpaint_target",
        "outpaint_adapter": "z_image_fun_union_canvas_target",
        "model_dir": "model_patches",
        "cfg_policy": "z_image_aura_safe",
        "negative_prompt_policy": "zero_or_hide",
        "notes": ["ZImage components need a native model-patch/Fun Union ControlNet adapter. P9.4 owns promotion."],
    },
    ("z_image", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_gguf_controlnet_target",
        "family_group": "z_image",
        "map_adapter": "z_image_fun_union_or_patch_gguf_target",
        "inpaint_adapter": "z_image_fun_union_inpaint_gguf_target",
        "outpaint_adapter": "z_image_fun_union_canvas_gguf_target",
        "model_dir": "model_patches",
        "cfg_policy": "z_image_aura_safe",
        "negative_prompt_policy": "zero_or_hide",
        "notes": ["ZImage GGUF ControlNet stays an implementation target until the ZImage patch adapter is wired in P9.4."],
    },
    ("z_image_turbo", "diffusion_model"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_turbo_components_controlnet_target",
        "family_group": "z_image_turbo",
        "map_adapter": "z_image_turbo_fun_union_patch_target",
        "inpaint_adapter": "z_image_turbo_fun_union_inpaint_target",
        "outpaint_adapter": "z_image_turbo_fun_union_canvas_target",
        "model_dir": "model_patches",
        "cfg_policy": "z_image_turbo_low_cfg",
        "negative_prompt_policy": "zero_or_hide",
        "notes": ["ZImage Turbo needs Z-Image-Turbo-Fun-Controlnet-Union.safetensors in models/model_patches; P9.5 owns promotion."],
    },
    ("z_image_turbo", "gguf"): {
        **DEFAULT_PROFILE,
        "profile_id": "z_image_turbo_gguf_controlnet_target",
        "family_group": "z_image_turbo",
        "map_adapter": "z_image_turbo_fun_union_patch_gguf_target",
        "inpaint_adapter": "z_image_turbo_fun_union_inpaint_gguf_target",
        "outpaint_adapter": "z_image_turbo_fun_union_canvas_gguf_target",
        "model_dir": "model_patches",
        "cfg_policy": "z_image_turbo_low_cfg",
        "negative_prompt_policy": "zero_or_hide",
        "notes": ["ZImage Turbo GGUF ControlNet stays an implementation target until the Turbo Fun Union adapter is wired in P9.5."],
    },
}


def normalize_backend_id(value: Any) -> str:
    raw = str(value or "comfyui").strip() or "comfyui"
    return BACKEND_ALIASES.get(raw, raw)


def normalize_mode_id(value: Any) -> str:
    raw = str(value or "generate").strip() or "generate"
    return MODE_ALIASES.get(raw, raw)


def normalized_route(route: dict[str, Any] | None = None) -> dict[str, str]:
    route = route if isinstance(route, dict) else {}
    return {
        "backend": normalize_backend_id(route.get("backend") or route.get("provider") or route.get("provider_id") or "comfyui"),
        "family": str(route.get("family") or "").strip(),
        "loader": str(route.get("loader") or "").strip(),
        "mode": normalize_mode_id(route.get("mode") or route.get("workflow_mode") or "generate"),
    }


def route_profile_key(route: dict[str, Any] | None = None) -> tuple[str, str]:
    normalized = normalized_route(route)
    return normalized["family"], normalized["loader"]


def get_route_profile(route: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = deepcopy(_ROUTE_PROFILES.get(route_profile_key(route), DEFAULT_PROFILE))
    normalized = normalized_route(route)
    profile["backend"] = normalized["backend"]
    profile["family"] = normalized["family"]
    profile["loader"] = normalized["loader"]
    profile["mode"] = normalized["mode"]
    return profile


def _base_route_state_from_matrix(route: dict[str, Any]) -> str:
    normalized = normalized_route(route)
    if normalized["backend"] not in {"comfyui", "forge", "a1111"}:
        return PROVIDER_GATED
    try:
        from neo_app.models.route_matrix import resolve_model_backend_route
        entry = resolve_model_backend_route(
            normalized["family"],
            normalized["loader"],
            "txt2img" if normalized["mode"] == "generate" else normalized["mode"],
            normalized["backend"],
        )
        state = str(entry.state or PROVIDER_GATED)
    except Exception:
        state = str(route.get("route_state") or route.get("base_route_state") or route.get("state") or PROVIDER_GATED)
    return state if state in KNOWN_STATES else PROVIDER_GATED


def base_route_state(route: dict[str, Any] | None = None) -> str:
    route = route if isinstance(route, dict) else {}
    explicit = str(route.get("base_route_state") or "").strip()
    if explicit in KNOWN_STATES:
        return explicit
    # Do not treat extension route_state as base_state if a task route has already
    # overwritten it; this function should describe the underlying image route.
    return _base_route_state_from_matrix(route)


def _task_state_from_profile(profile: dict[str, Any], task: str, mode: str) -> str:
    task_states = profile.get("task_states") if isinstance(profile.get("task_states"), dict) else {}
    task_state = task_states.get(task)
    if isinstance(task_state, dict):
        value = str(task_state.get(mode) or task_state.get("*") or IMPLEMENTATION_TARGET)
    else:
        value = str(task_state or profile.get("rollout_state") or IMPLEMENTATION_TARGET)
    return value if value in KNOWN_STATES else IMPLEMENTATION_TARGET


def controlnet_state_for_route(route: dict[str, Any] | None = None, *, task: str = TASK_MAP_CONTROL) -> str:
    normalized = normalized_route(route)
    if normalized["backend"] not in {"comfyui"}:
        return PROVIDER_GATED if normalized["backend"] not in {"forge", "a1111"} else PROVIDER_GATED
    base_state = base_route_state(normalized | {"base_route_state": (route or {}).get("base_route_state") if isinstance(route, dict) else None})
    if base_state == UNSUPPORTED:
        return UNSUPPORTED
    if base_state in {PROVIDER_GATED, PLANNED_GATED, IMPLEMENTATION_TARGET}:
        return base_state
    profile = get_route_profile(normalized)
    state = _task_state_from_profile(profile, task, normalized["mode"])
    return state


def route_profile_summary(route: dict[str, Any] | None = None, *, task: str = TASK_MAP_CONTROL) -> dict[str, Any]:
    normalized = normalized_route(route)
    profile = get_route_profile(normalized)
    base_state = base_route_state({**normalized, "base_route_state": (route or {}).get("base_route_state") if isinstance(route, dict) else None})
    extension_state = controlnet_state_for_route({**normalized, "base_route_state": base_state}, task=task)
    return {
        **profile,
        "backend": normalized["backend"],
        "family": normalized["family"],
        "loader": normalized["loader"],
        "mode": normalized["mode"],
        "controlnet_task": task,
        "route_profile_id": profile.get("profile_id"),
        "base_route_state": base_state,
        "controlnet_state": extension_state,
        "active": extension_state in ACTIVE_STATES,
    }


def all_route_profiles() -> dict[str, dict[str, Any]]:
    return {f"{family}:{loader}": deepcopy(profile) for (family, loader), profile in sorted(_ROUTE_PROFILES.items())}
