from __future__ import annotations

ACTIVE_STATES = {"available", "experimental_available"}
GATED_STATES = {"planned_gated", "provider_gated", "unsupported"}
SUPPORTED_BACKENDS = {"comfyui", "comfyui_portable"}
SUPPORTED_WORKFLOW_MODES = {"generate", "txt2img", "img2img", "inpaint", "outpaint"}

REASONS = {
    "available": "IP Adapter is available for validated Comfy checkpoint routes.",
    "experimental_available": "IP Adapter is available experimentally on this route; validate output identity strength before batch use.",
    "planned_gated": "This route has no validated V2 IP Adapter workflow patch yet.",
    "provider_gated": "This backend/provider route is not implemented for IP Adapter yet.",
    "unsupported": "The selected family/loader is not compatible with V1 IP Adapter behavior in this V2 build.",
}


def normalize_mode(mode: str | None) -> str:
    value = str(mode or "generate").strip().lower() or "generate"
    return "generate" if value == "txt2img" else value


def route_key(backend: str | None, family: str | None, loader: str | None, workflow_mode: str | None) -> str:
    return f"{str(backend or 'comfyui').strip().lower()}:{str(family or 'sdxl').strip().lower()}:{str(loader or 'checkpoint').strip().lower()}:{normalize_mode(workflow_mode)}"


def route_state(backend: str | None, family: str | None, loader: str | None, workflow_mode: str | None) -> str:
    backend = str(backend or "comfyui").strip().lower()
    family = str(family or "sdxl").strip().lower()
    loader = str(loader or "checkpoint").strip().lower()
    mode = normalize_mode(workflow_mode)
    if backend not in SUPPORTED_BACKENDS:
        return "provider_gated"
    if family == "sdxl" and loader == "checkpoint" and mode in {"generate", "img2img", "inpaint"}:
        return "available"
    if family == "sd15" and loader == "checkpoint" and mode in {"generate", "img2img", "inpaint"}:
        return "experimental_available"
    if family in {"sdxl", "sd15"} and loader == "checkpoint" and mode == "outpaint":
        return "planned_gated"
    if family in {"sdxl", "sd15"} and loader in {"diffusion_model", "unet", "gguf"}:
        return "planned_gated"
    if family in {"wan_image", "hunyuan_image"}:
        return "provider_gated"
    return "unsupported"


def route_reason(state: str) -> str:
    return REASONS.get(state, "Unknown IP Adapter route state.")


def support_matrix(backends: list[str] | None = None, families: list[str] | None = None, loaders: list[str] | None = None, modes: list[str] | None = None) -> dict[str, dict[str, str]]:
    backends = backends or ["comfyui", "comfyui_portable", "forge", "a1111"]
    families = families or ["sdxl", "sd15", "flux", "flux2_klein", "qwen_image", "z_image", "z_image_turbo", "hidream", "wan_image", "hunyuan_image"]
    loaders = loaders or ["checkpoint", "diffusion_model", "gguf", "native", "api_model"]
    modes = modes or ["generate", "img2img", "inpaint", "outpaint"]
    rows: dict[str, dict[str, str]] = {}
    for backend in backends:
        for family in families:
            for loader in loaders:
                for mode in modes:
                    state = route_state(backend, family, loader, mode)
                    rows[route_key(backend, family, loader, mode)] = {"state": state, "reason": route_reason(state)}
    return rows
