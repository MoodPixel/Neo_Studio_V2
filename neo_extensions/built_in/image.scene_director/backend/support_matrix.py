from __future__ import annotations

from typing import Any

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
BLOCKED_FAMILIES = {
    "flux",
    "flux1",
    "flux2_klein",
    "flux_2_klein",
    "qwen",
    "qwen_image",
    "qwen_image_edit",
    "qwen2",
    "qwen2.5",
    "z_image",
    "z_image_turbo",
    "zimage",
    "zimage_turbo",
    "hidream",
    "wan",
    "wan_image",
    "hunyuan",
    "hunyuan_image",
}
SUPPORTED_LOADERS = {"checkpoint", "ckpt", "safetensors"}
BLOCKED_LOADERS = {"gguf", "ggml", "unet", "diffusion_model", "native", "provider"}
SUPPORTED_MODES = {"generate", "txt2img", "img2img", "inpaint"}
GENERATE_MODE_ALIASES = {"txt2img", "text2image", "text_to_image", "generate", "generation"}
IMAGE_WORKSPACE = "image"
GENERATION_WORKSPACE_ALIASES = {"generations", "generation", "generate", "txt2img", "image.generations"}
BLOCKED_WORKSPACES = {"assets", "reference", "finish", "results"}


def normalize_mode(mode: Any) -> str:
    text = str(mode or "generate").strip().lower().replace("-", "_")
    if text in GENERATE_MODE_ALIASES:
        return "generate"
    return text


def normalize_backend(backend: Any) -> str:
    text = str(backend or "comfyui").strip().lower()
    return COMFY_BACKEND_ALIASES.get(text, text)


def normalize_family(family: Any) -> str:
    text = str(family or "sdxl").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"sd_15", "sd_1.5", "stable_diffusion_15"}:
        return "sd15"
    if text in {"qwenimage", "qwen_image_edit"}:
        return "qwen_image"
    if text in {"zimage_turbo", "z_image_turbo", "z_image_turbo"}:
        return "z_image_turbo"
    if text in {"zimage", "z_image"}:
        return "z_image"
    return text


def normalize_loader(loader: Any) -> str:
    text = str(loader or "checkpoint").strip().lower().replace("-", "_").replace(" ", "_")
    if text in {"ckpt", "safetensors", "checkpoint_loader", "checkpointloader"}:
        return "checkpoint"
    if text in {"gguf_loader", "ggufloader"}:
        return "gguf"
    return text


def normalize_workspace(value: Any) -> str:
    return str(value or IMAGE_WORKSPACE).strip().lower().replace("-", "_")


def normalize_subtab(value: Any) -> str:
    text = str(value or "generations").strip().lower().replace("-", "_")
    return "generations" if text in GENERATION_WORKSPACE_ALIASES else text


def normalize_route(route: dict[str, Any] | None = None, **overrides: Any) -> dict[str, str]:
    """Normalize route keys used by V2 providers, UI subtabs, and older tests.

    Missing workspace/subtab values default to Image → Generations so legacy unit
    tests and server-side calls without a UI context keep the V1-compatible route.
    """
    route = dict(route or {})
    route.update({k: v for k, v in overrides.items() if v is not None})
    return {
        "backend": normalize_backend(route.get("backend") or route.get("provider") or route.get("provider_id")),
        "family": normalize_family(route.get("family") or route.get("model_family")),
        "loader": normalize_loader(route.get("loader") or route.get("model_loader") or route.get("loader_type")),
        "mode": normalize_mode(route.get("workflow_mode") or route.get("mode") or route.get("subtab")),
        "workspace": normalize_workspace(route.get("workspace") or route.get("surface") or "image"),
        "subtab": normalize_subtab(route.get("workspace_app") or route.get("workspace_subtab") or route.get("subtab") or "generations"),
    }


def _base_state(route: dict[str, str]) -> tuple[str, str]:
    backend = route["backend"]
    family = route["family"]
    loader = route["loader"]
    mode = route["mode"]
    workspace = route["workspace"]
    subtab = route["subtab"]

    if workspace != IMAGE_WORKSPACE:
        return "unsupported", "Scene Director is image-generation specific and does not mount outside the Image workspace."
    if subtab in BLOCKED_WORKSPACES or subtab not in GENERATION_WORKSPACE_ALIASES:
        return "unsupported", "Scene Director only mounts in Image → Generations; asset/reference/finish/results surfaces are not generation compilers."
    if backend not in {"comfyui"}:
        return "provider_gated", "Scene Director workflow patching is Comfy-node based and this backend is not validated."
    if mode == "outpaint":
        return "planned_gated", "Outpaint is intentionally skipped by V1 Scene Director and remains planned-gated until a dedicated canvas/mask policy exists."
    if family in BLOCKED_FAMILIES:
        return "unsupported", f"{family} uses a non-SD checkpoint conditioning graph and must not fallback to Scene Director V054."
    if loader in BLOCKED_LOADERS or loader != "checkpoint":
        return "unsupported", "Scene Director V054 regional conditioning is checkpoint-only; GGUF/native/provider loaders must not consume checkpoint-only fields."
    if family not in SUPPORTED_FAMILIES:
        return "unsupported", "Scene Director has no validated V1 parity route for this family."
    if mode not in {"generate", "img2img", "inpaint"}:
        return "unsupported", "Scene Director only supports generate/img2img/inpaint; this workflow mode is not validated."
    if family in SD15_FAMILIES:
        return "experimental_available", "V1 supports SD/SD1.5 checkpoint routes; V2 keeps them experimental until expanded graph regression tests exist."
    return "available", "V1-supported SDXL checkpoint generate/img2img/inpaint route."


def _detect_node_available(node_status: Any = None, object_info: Any = None) -> bool | None:
    source = node_status if node_status is not None else object_info
    if source is None:
        return None
    if isinstance(source, dict):
        if "available" in source:
            return bool(source.get("available"))
        names = set(map(str, source.keys()))
        names.update(str(v.get("class_type")) for v in source.values() if isinstance(v, dict) and v.get("class_type"))
        return bool({"NeoSceneDirectorV054"} & names)
    if isinstance(source, (set, list, tuple)):
        return bool({"NeoSceneDirectorV054"} & {str(x) for x in source})
    return None


def get_scene_director_support(route: dict[str, Any] | None = None, *, node_status: Any = None, object_info: Any = None, require_node: bool = False, **overrides: Any) -> dict[str, Any]:
    normalized = normalize_route(route, **overrides)
    state, reason = _base_state(normalized)
    route_compatible_state = state
    node_available = _detect_node_available(node_status=node_status, object_info=object_info)

    # Support matrix is route-first. Node availability overlays workflow patch
    # readiness only when explicitly requested by validation/workflow layers.
    if require_node and state in ACTIVE_STATES and node_available is False:
        state = "provider_gated"
        reason = "Scene Director route is compatible, but NeoSceneDirectorV054 was not detected. V052/V053 active fallback is retired."

    workflow_patch_allowed = state in ACTIVE_STATES and (node_available is not False if require_node else True)
    if state not in ACTIVE_STATES:
        workflow_patch_allowed = False

    provider_capabilities = resolve_provider_capabilities_v054(normalized, object_info=object_info, node_status=node_status)

    return {
        "extension_id": EXTENSION_ID,
        "state": state,
        "route_state": route_compatible_state,
        "workflow_patch_allowed": workflow_patch_allowed,
        "reason": reason,
        "route": normalized,
        "requires_node": True,
        "node_required_for_patch": True,
        "node_available": node_available,
        "provider_capabilities": provider_capabilities,
        "allowed_states": sorted(SUPPORT_STATES),
    }


def route_state(*, backend: Any = None, family: Any = None, loader: Any = None, workflow_mode: Any = None, mode: Any = None, object_info: Any = None, workspace: Any = None, workspace_app: Any = None, subtab: Any = None, require_node: bool = False, node_status: Any = None) -> str:
    support = get_scene_director_support(
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
    )
    return str(support["state"])


def route_reason(*, backend: Any = None, family: Any = None, loader: Any = None, workflow_mode: Any = None, mode: Any = None, object_info: Any = None, workspace: Any = None, workspace_app: Any = None, subtab: Any = None, require_node: bool = False, node_status: Any = None) -> str:
    support = get_scene_director_support(
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
    )
    return str(support["reason"])
