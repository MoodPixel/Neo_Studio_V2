from __future__ import annotations

from typing import Any

from .execution_strategy import (
    ENGINE_CLASSIC_V054,
    ENGINE_LIGHTWEIGHT_REGIONAL,
    MODERN_FAMILY_LOADERS,
    MODERN_LIGHTWEIGHT_FAMILIES,
    normalize_scene_director_family,
    normalize_scene_director_loader,
    normalize_scene_director_mode,
    resolve_scene_director_execution_strategy,
)
from .provider_capabilities import resolve_provider_capabilities_v054

EXTENSION_ID = "image.scene_director"

SUPPORT_STATES = {
    "available",
    "experimental_available",
    "planned_gated",
    "provider_gated",
    "unsupported",
}
ACTIVE_STATES = {"available", "experimental_available"}

SUPPORTED_BACKENDS = {"comfy", "comfyui", "comfyui_portable"}
COMFY_BACKEND_ALIASES = {
    "comfy": "comfyui",
    "comfyui": "comfyui",
    "comfyui_portable": "comfyui",
    "comfy_portable": "comfyui",
}

SDXL_FAMILIES = {"sdxl", "sdxl_sd"}
SD15_FAMILIES = {"sd", "sd15", "sd1.5", "sd_1_5", "sd1_5", "stable_diffusion_1_5"}
SUPPORTED_FAMILIES = SDXL_FAMILIES | SD15_FAMILIES
RECOGNIZED_MODERN_FAMILIES = set(MODERN_LIGHTWEIGHT_FAMILIES)
BLOCKED_FAMILIES = {
    "flux",
    "flux1",
    "qwen",
    "qwen_image",
    "qwen_image_edit",
    "qwen2",
    "qwen2.5",
    "hidream",
    "wan",
    "wan_image",
    "hunyuan",
    "hunyuan_image",
}
SUPPORTED_LOADERS = {"checkpoint", "ckpt", "safetensors"}
RECOGNIZED_MODERN_LOADERS = {loader for loaders in MODERN_FAMILY_LOADERS.values() for loader in loaders}
BLOCKED_LOADERS = {"ggml", "provider"}
SUPPORTED_MODES = {"generate", "txt2img", "img2img", "inpaint"}
GENERATE_MODE_ALIASES = {"txt2img", "text2image", "text_to_image", "generate", "generation"}
IMAGE_WORKSPACE = "image"
GENERATION_WORKSPACE_ALIASES = {"generations", "generation", "generate", "txt2img", "image.generations"}
BLOCKED_WORKSPACES = {"assets", "reference", "finish", "results"}


def normalize_mode(mode: Any) -> str:
    return normalize_scene_director_mode(mode)


def normalize_backend(backend: Any) -> str:
    text = str(backend or "comfyui").strip().lower()
    return COMFY_BACKEND_ALIASES.get(text, text)


def normalize_family(family: Any) -> str:
    return normalize_scene_director_family(family)


def normalize_loader(loader: Any, family: Any = None) -> str:
    return normalize_scene_director_loader(loader, family)


def normalize_workspace(value: Any) -> str:
    return str(value or IMAGE_WORKSPACE).strip().lower().replace("-", "_")


def normalize_subtab(value: Any) -> str:
    text = str(value or "generations").strip().lower().replace("-", "_")
    return "generations" if text in GENERATION_WORKSPACE_ALIASES else text


def normalize_route(route: dict[str, Any] | None = None, **overrides: Any) -> dict[str, str]:
    """Normalize route keys used by providers, workspace UI, and older tests."""
    route = dict(route or {})
    route.update({k: v for k, v in overrides.items() if v is not None})
    family = normalize_family(route.get("family") or route.get("model_family"))
    return {
        "backend": normalize_backend(route.get("backend") or route.get("provider") or route.get("provider_id")),
        "family": family,
        "loader": normalize_loader(route.get("loader") or route.get("model_loader") or route.get("loader_type"), family),
        "mode": normalize_mode(route.get("workflow_mode") or route.get("mode") or route.get("subtab")),
        "workspace": normalize_workspace(route.get("workspace") or route.get("surface") or "image"),
        "subtab": normalize_subtab(route.get("workspace_app") or route.get("workspace_subtab") or route.get("subtab") or "generations"),
    }


def _state_from_strategy(route: dict[str, str]) -> tuple[str, str]:
    strategy = resolve_scene_director_execution_strategy(route)
    engine = str(strategy.get("engine") or "unsupported")
    status = str(strategy.get("status") or "unsupported")
    if engine == ENGINE_CLASSIC_V054:
        if status == "active":
            return "available", str(strategy.get("reason") or "Classic V054 route is available.")
        if status == "experimental":
            return "experimental_available", str(strategy.get("reason") or "Classic V054 route is experimental.")
    if engine == ENGINE_LIGHTWEIGHT_REGIONAL:
        if status == "active" and strategy.get("execution_enabled"):
            return "available", str(strategy.get("reason") or "Lightweight regional route is available.")
        if status == "experimental" and strategy.get("execution_enabled"):
            return "experimental_available", str(strategy.get("reason") or "Lightweight regional prompt routing is experimental.")
        if status == "planned_gated":
            return "planned_gated", str(strategy.get("reason") or "Lightweight regional route is planned-gated.")
    if status == "provider_gated":
        return "provider_gated", str(strategy.get("reason") or "Scene Director provider is gated.")
    if status == "planned_gated":
        return "planned_gated", str(strategy.get("reason") or "Scene Director route is planned-gated.")
    return "unsupported", str(strategy.get("reason") or "Scene Director route is unsupported.")


def _base_state(route: dict[str, str]) -> tuple[str, str]:
    workspace = route["workspace"]
    subtab = route["subtab"]
    backend = route["backend"]
    family = route["family"]

    if workspace != IMAGE_WORKSPACE:
        return "unsupported", "Scene Director is image-generation specific and does not mount outside the Image workspace."
    mode = route["mode"]
    reference_mode = mode in {"img2img", "inpaint", "outpaint"}
    allowed_subtabs = set(GENERATION_WORKSPACE_ALIASES) | ({"reference"} if reference_mode else set())
    if subtab in {"assets", "finish", "results"} or subtab not in allowed_subtabs:
        return "unsupported", "Scene Director route context does not match the active Image generation/reference workflow."
    if backend != "comfyui":
        return "provider_gated", "Scene Director graph execution is currently validated only on ComfyUI backends."
    if family in BLOCKED_FAMILIES:
        return "unsupported", f"{family} has no Scene Director engine and must not fallback to V054."
    return _state_from_strategy(route)


def _detect_v054_node_available(node_status: Any = None, object_info: Any = None) -> bool | None:
    source = node_status if node_status is not None else object_info
    if source is None:
        return None
    if isinstance(source, dict):
        if "available" in source and "NeoSceneDirectorV054" not in source:
            return bool(source.get("available"))
        names = set(map(str, source.keys()))
        names.update(str(v.get("class_type")) for v in source.values() if isinstance(v, dict) and v.get("class_type"))
        return "NeoSceneDirectorV054" in names
    if isinstance(source, (set, list, tuple)):
        return "NeoSceneDirectorV054" in {str(x) for x in source}
    return None


def get_scene_director_support(
    route: dict[str, Any] | None = None,
    *,
    node_status: Any = None,
    object_info: Any = None,
    require_node: bool = False,
    **overrides: Any,
) -> dict[str, Any]:
    normalized = normalize_route(route, **overrides)
    state, reason = _base_state(normalized)
    route_compatible_state = state
    execution_strategy = resolve_scene_director_execution_strategy(normalized)
    engine = str(execution_strategy.get("engine") or "unsupported")
    custom_node_required = bool(execution_strategy.get("custom_scene_director_node_required"))
    node_available = _detect_v054_node_available(node_status=node_status, object_info=object_info) if custom_node_required else None

    if require_node and custom_node_required and state in ACTIVE_STATES and node_available is False:
        state = "provider_gated"
        reason = "Scene Director classic V054 route is compatible, but NeoSceneDirectorV054 was not detected. V052/V053 active fallback is retired."

    workflow_patch_allowed = state in ACTIVE_STATES
    if require_node and custom_node_required and node_available is False:
        workflow_patch_allowed = False

    provider_capabilities = resolve_provider_capabilities_v054(normalized, object_info=object_info, node_status=node_status)
    if isinstance(provider_capabilities, dict):
        provider_capabilities = dict(provider_capabilities)
        provider_capabilities["execution_strategy"] = execution_strategy
        if engine == ENGINE_LIGHTWEIGHT_REGIONAL:
            provider_capabilities["lightweight_regional"] = {
                "phase": "SD-28.7",
                "regional_prompt": bool((execution_strategy.get("regional_prompt") or {}).get("supported")),
                "regional_lora": bool((execution_strategy.get("regional_lora") or {}).get("supported")),
                "single_sampler_required": True,
                "custom_scene_director_node_required": False,
                "required_comfy_nodes": list(execution_strategy.get("required_comfy_nodes") or []),
            }

    return {
        "extension_id": EXTENSION_ID,
        "state": state,
        "route_state": route_compatible_state,
        "workflow_patch_allowed": workflow_patch_allowed,
        "reason": reason,
        "route": normalized,
        "requires_node": custom_node_required,
        "node_required_for_patch": custom_node_required,
        "node_available": node_available,
        "execution_engine": engine,
        "execution_strategy": execution_strategy,
        "provider_capabilities": provider_capabilities,
        "allowed_states": sorted(SUPPORT_STATES),
        "release_lock": {
            "phase": "SD-28.7",
            "state": "preflight",
            "enforced_at": "workflow_dispatch_post_compile",
            "fail_closed": True,
            "gpu_proof_is_separate": True,
        },
    }


def route_state(
    *,
    backend: Any = None,
    family: Any = None,
    loader: Any = None,
    workflow_mode: Any = None,
    mode: Any = None,
    object_info: Any = None,
    workspace: Any = None,
    workspace_app: Any = None,
    subtab: Any = None,
    require_node: bool = False,
    node_status: Any = None,
) -> str:
    return str(get_scene_director_support(
        backend=backend,
        family=family,
        loader=loader,
        workflow_mode=workflow_mode,
        mode=mode,
        object_info=object_info,
        workspace=workspace,
        workspace_app=workspace_app,
        subtab=subtab,
        require_node=require_node,
        node_status=node_status,
    )["state"])


def route_reason(
    *,
    backend: Any = None,
    family: Any = None,
    loader: Any = None,
    workflow_mode: Any = None,
    mode: Any = None,
    object_info: Any = None,
    workspace: Any = None,
    workspace_app: Any = None,
    subtab: Any = None,
    require_node: bool = False,
    node_status: Any = None,
) -> str:
    return str(get_scene_director_support(
        backend=backend,
        family=family,
        loader=loader,
        workflow_mode=workflow_mode,
        mode=mode,
        object_info=object_info,
        workspace=workspace,
        workspace_app=workspace_app,
        subtab=subtab,
        require_node=require_node,
        node_status=node_status,
    )["reason"])
