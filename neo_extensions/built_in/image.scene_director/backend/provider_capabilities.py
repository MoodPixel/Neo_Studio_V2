from __future__ import annotations

from copy import deepcopy
from typing import Any

CAPABILITY_PHASE = "SD-V054-21"
CAPABILITY_SCHEMA = "neo.image.scene_director.provider_capabilities.v054.v1"

TRUE = True
FALSE = False
WORKFLOW_DEPENDENT = "workflow_dependent"
ADAPTER_REQUIRED = "adapter_required"
SEMANTIC_OR_MASK_ADAPTER = "semantic_or_mask_adapter"
KONTEXT_IF_AVAILABLE = "kontext_if_available"
COMPOSITE_OR_NATIVE = "composite_or_native"
PLANNED = "planned"

try:
    from .flux_adapter import build_flux_adapter_plan_v054
    from .qwen_adapter import build_qwen_adapter_plan_v054
except Exception:  # standalone fallback
    build_flux_adapter_plan_v054 = None
    build_qwen_adapter_plan_v054 = None


def _norm(value: Any, default: str = "") -> str:
    return str(value if value is not None else default).strip().lower().replace("-", "_").replace(" ", "_")


def normalize_provider_family_v054(value: Any) -> str:
    family = _norm(value, "sdxl")
    aliases = {
        "sd_xl": "sdxl",
        "stable_diffusion_xl": "sdxl",
        "sd_1_5": "sd15",
        "sd1_5": "sd15",
        "sd_15": "sd15",
        "stable_diffusion_1_5": "sd15",
        "qwenimage": "qwen_image_edit",
        "qwen_image": "qwen_image_edit",
        "qwen": "qwen_image_edit",
        "qwen2": "qwen_image_edit",
        "qwen2_5": "qwen_image_edit",
        "flux1": "flux",
        "flux_1": "flux",
    }
    return aliases.get(family, family)


def normalize_provider_loader_v054(value: Any) -> str:
    loader = _norm(value, "checkpoint")
    if loader in {"ckpt", "safetensors", "checkpoint_loader", "checkpointloader"}:
        return "checkpoint"
    if loader in {"gguf_loader", "ggufloader"}:
        return "gguf"
    return loader


def normalize_provider_mode_v054(value: Any) -> str:
    mode = _norm(value, "generate")
    if mode in {"txt2img", "text2image", "text_to_image", "generation"}:
        return "generate"
    return mode


BASE_FEATURES: dict[str, Any] = {
    "scene_graph_json": True,
    "scene_graph_planning": True,
    "v054_node": False,
    "regional_conditioning": False,
    "character_lock": False,
    "linked_detail_lanes": False,
    "relationship_compiler": False,
    "prompt_compiler_registry": True,
    "conflict_resolver": True,
    "complexity_meter": True,
    "mixed_background_regions": False,
    "regional_controlnet": False,
    "regional_detailer": False,
    "text_regions": True,
    "composite_text": True,
    "native_text_editing": False,
    "img2img_region_reuse": False,
    "inpaint_region_targeting": False,
    "output_inspector_source_stack": True,
    "source_image_reuse": True,
    "mask_reuse": "size_match_required",
    "latent_replay": False,
    "native_semantic_editing": False,
    "flux_adapter_plan": False,
    "qwen_adapter_plan": False,
}

SDXL_CHECKPOINT_FEATURES = {
    **BASE_FEATURES,
    "v054_node": True,
    "regional_conditioning": True,
    "character_lock": True,
    "linked_detail_lanes": True,
    "relationship_compiler": True,
    "mixed_background_regions": True,
    "regional_controlnet": True,
    "regional_detailer": True,
    "img2img_region_reuse": True,
    "inpaint_region_targeting": True,
    "latent_replay": True,
}

SD15_CHECKPOINT_FEATURES = {
    **SDXL_CHECKPOINT_FEATURES,
    "regional_controlnet": WORKFLOW_DEPENDENT,
    "regional_detailer": WORKFLOW_DEPENDENT,
}

FLUX_FEATURES = {
    **BASE_FEATURES,
    "v054_node": ADAPTER_REQUIRED,
    "regional_conditioning": WORKFLOW_DEPENDENT,
    "character_lock": "prompt_or_adapter_dependent",
    "linked_detail_lanes": "prompt_compiler_only",
    "relationship_compiler": "prompt_compiler_only",
    "mixed_background_regions": "prompt_or_mask_adapter",
    "regional_controlnet": WORKFLOW_DEPENDENT,
    "regional_detailer": True,
    "img2img_region_reuse": True,
    "inpaint_region_targeting": True,
    "native_text_editing": COMPOSITE_OR_NATIVE,
    "native_semantic_editing": KONTEXT_IF_AVAILABLE,
    "flux_adapter_plan": True,
    "latent_replay": False,
}


QWEN_FEATURES = {
    **BASE_FEATURES,
    "v054_node": False,
    "regional_conditioning": SEMANTIC_OR_MASK_ADAPTER,
    "character_lock": "semantic_instruction",
    "linked_detail_lanes": "semantic_instruction",
    "relationship_compiler": "semantic_instruction",
    "mixed_background_regions": "semantic_instruction_or_mask",
    "regional_detailer": True,
    "img2img_region_reuse": True,
    "inpaint_region_targeting": True,
    "native_text_editing": True,
    "native_semantic_editing": True,
    "qwen_adapter_plan": True,
    "latent_replay": False,
}


UNSUPPORTED_FEATURES = {**BASE_FEATURES, "scene_graph_planning": True}


def _profile_key(family: str, loader: str) -> str:
    if family == "sdxl" and loader == "checkpoint":
        return "sdxl_checkpoint"
    if family == "sd15" and loader == "checkpoint":
        return "sd15_checkpoint_experimental"
    if family == "flux":
        return "flux_adapter_planned"
    if family == "qwen_image_edit":
        return "qwen_semantic_edit_adapter"
    return "unsupported_provider"


def _features_for_profile(profile: str) -> dict[str, Any]:
    if profile == "sdxl_checkpoint":
        return deepcopy(SDXL_CHECKPOINT_FEATURES)
    if profile == "sd15_checkpoint_experimental":
        return deepcopy(SD15_CHECKPOINT_FEATURES)
    if profile == "flux_adapter_planned":
        return deepcopy(FLUX_FEATURES)
    if profile == "qwen_semantic_edit_adapter":
        return deepcopy(QWEN_FEATURES)
    return deepcopy(UNSUPPORTED_FEATURES)


def _notice(profile: str, family: str) -> list[dict[str, str]]:
    if profile == "sdxl_checkpoint":
        return [{"level": "info", "code": "provider_sdxl_full_v054_locked", "message": "SDXL checkpoint route is the locked full V054 implementation target."}]
    if profile == "sd15_checkpoint_experimental":
        return [{"level": "warning", "code": "provider_sd15_experimental", "message": "SD1.5 checkpoint route can use V054 planning, but regional ControlNet/detailer behavior remains workflow-dependent."}]
    if profile == "flux_adapter_planned":
        return [{"level": "warning", "code": "provider_flux_adapter_required", "message": "Flux can preserve the V054 scene graph, but requires a Flux adapter route for regional conditioning; SDXL latent replay is disabled."}]
    if profile == "qwen_semantic_edit_adapter":
        return [{"level": "warning", "code": "provider_qwen_semantic_adapter", "message": "Qwen image/edit routes should use semantic/mask-adapter instructions, not the SDXL V054 node path; SDXL latent replay is disabled."}]
    return [{"level": "warning", "code": "provider_unsupported_v054", "message": f"{family or 'This provider'} has no validated V054 execution route; scene graph planning can be saved but generation controls must be gated."}]



SDXL_FULL_LOCK_REQUIRED_FEATURES = (
    "scene_graph_json",
    "v054_node",
    "regional_conditioning",
    "character_lock",
    "linked_detail_lanes",
    "relationship_compiler",
    "prompt_compiler_registry",
    "conflict_resolver",
    "complexity_meter",
    "mixed_background_regions",
    "regional_controlnet",
    "regional_detailer",
    "text_regions",
    "composite_text",
    "img2img_region_reuse",
    "inpaint_region_targeting",
    "output_inspector_source_stack",
    "source_image_reuse",
    "latent_replay",
)


def sdxl_full_implementation_lock_v054(capabilities: dict[str, Any]) -> dict[str, Any]:
    profile = str((capabilities or {}).get("provider_profile") or "")
    features = dict((capabilities or {}).get("features") or {})
    node_state = (capabilities or {}).get("v054_node_installed")
    missing = [name for name in SDXL_FULL_LOCK_REQUIRED_FEATURES if features.get(name) is not True]
    degraded = [name for name, value in features.items() if isinstance(value, str) and value not in {"size_match_required"}]
    locked = profile == "sdxl_checkpoint"
    ready = bool(locked and not missing and node_state is not False)
    return {
        "schema": "neo.image.scene_director.sdxl_full_lock.v054.v1",
        "phase": "SD-V054-18",
        "provider_profile": profile,
        "locked_provider": "sdxl_checkpoint",
        "locked": locked,
        "ready": ready,
        "node_install_state": "installed" if node_state is True else "missing" if node_state is False else "unknown",
        "required_features": list(SDXL_FULL_LOCK_REQUIRED_FEATURES),
        "missing_features": missing,
        "degraded_features": degraded,
        "stress_test": {
            "name": "sdxl_v054_two_subject_portal_stress",
            "requires": [
                "two character regions",
                "hair_detail attached to Person 1",
                "held_prop attached to Person 2",
                "left/right background zones",
                "transition_effect seam",
                "optional regional ControlNet",
                "optional regional detailer",
                "Output Inspector source stack",
            ],
        },
        "message": "SDXL checkpoint is the first fully locked V054 implementation route." if locked else "This provider is not the SDXL full-lock route; use the provider adapter plan instead.",
    }

def resolve_provider_capabilities_v054(route: dict[str, Any] | None = None, *, object_info: Any = None, node_status: Any = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    route = dict(route or {})
    metadata = dict(metadata or {})
    family = normalize_provider_family_v054(route.get("family") or route.get("model_family") or metadata.get("family") or metadata.get("model_family") or "sdxl")
    loader = normalize_provider_loader_v054(route.get("loader") or route.get("model_loader") or metadata.get("loader") or metadata.get("model_loader") or "checkpoint")
    mode = normalize_provider_mode_v054(route.get("workflow_mode") or route.get("mode") or metadata.get("mode") or "generate")
    backend = _norm(route.get("backend") or route.get("provider") or metadata.get("backend") or "comfyui", "comfyui")
    profile = _profile_key(family, loader)
    features = _features_for_profile(profile)

    # Object-info detection only changes install readiness; it must not claim Flux/Qwen use the SDXL node.
    node_names: set[str] = set()
    source = node_status if node_status is not None else object_info
    if isinstance(source, dict):
        node_names.update(str(k) for k in source.keys())
        for value in source.values():
            if isinstance(value, dict):
                name = value.get("class_type") or value.get("name") or value.get("display_name")
                if name:
                    node_names.add(str(name))
    elif isinstance(source, (list, tuple, set)):
        node_names.update(str(x) for x in source)
    v054_node_installed = "NeoSceneDirectorV054" in node_names if source is not None else None

    route_kind = {
        "sdxl_checkpoint": "v054_node_workflow",
        "sd15_checkpoint_experimental": "v054_node_workflow_experimental",
        "flux_adapter_planned": "flux_adapter_required",
        "qwen_semantic_edit_adapter": "semantic_edit_adapter",
    }.get(profile, "planning_only")

    disabled = [key for key, value in features.items() if value is False]
    adapter_required = [key for key, value in features.items() if value in {ADAPTER_REQUIRED, WORKFLOW_DEPENDENT, SEMANTIC_OR_MASK_ADAPTER, KONTEXT_IF_AVAILABLE, COMPOSITE_OR_NATIVE}]
    if profile in {"sdxl_checkpoint", "sd15_checkpoint_experimental"} and v054_node_installed is False:
        adapter_required.append("NeoSceneDirectorV054_install")

    result = {
        "schema": CAPABILITY_SCHEMA,
        "phase": CAPABILITY_PHASE,
        "provider_profile": profile,
        "route_kind": route_kind,
        "backend": backend,
        "family": family,
        "loader": loader,
        "mode": mode,
        "features": features,
        "v054_node_installed": v054_node_installed,
        "disabled_features": sorted(set(disabled)),
        "adapter_required_features": sorted(set(adapter_required)),
        "latent_policy": "compatible_route_only" if bool(features.get("latent_replay")) else "disabled_for_provider",
        "source_image_policy": "reusable_across_providers",
        "mask_policy": "requires_size_match",
        "notices": _notice(profile, family),
    }
    scene_graph = metadata.get("scene_graph_json") if isinstance(metadata.get("scene_graph_json"), dict) else metadata.get("scene_graph") if isinstance(metadata.get("scene_graph"), dict) else None
    if profile == "flux_adapter_planned" and build_flux_adapter_plan_v054 is not None:
        result["flux_adapter_plan"] = build_flux_adapter_plan_v054(scene_graph or {"version": "v054", "regions": []}, route=route)
    else:
        result["flux_adapter_plan"] = None
    if profile == "qwen_semantic_edit_adapter" and build_qwen_adapter_plan_v054 is not None:
        result["qwen_adapter_plan"] = build_qwen_adapter_plan_v054(scene_graph or {"version": "v054", "regions": []}, route=route)
    else:
        result["qwen_adapter_plan"] = None
    result["sdxl_full_implementation_lock"] = sdxl_full_implementation_lock_v054(result)
    return result


__all__ = [
    "CAPABILITY_PHASE",
    "CAPABILITY_SCHEMA",
    "SDXL_FULL_LOCK_REQUIRED_FEATURES",
    "sdxl_full_implementation_lock_v054",
    "resolve_provider_capabilities_v054",
    "build_flux_adapter_plan_v054",
    "build_qwen_adapter_plan_v054",
    "normalize_provider_family_v054",
    "normalize_provider_loader_v054",
    "normalize_provider_mode_v054",
]

# Phase 20 compatibility anchor: SD-V054-20 Qwen Adapter Planning
# Phase 21 compatibility anchor: SD-V054-21 Retire V052/V053 Active Path
