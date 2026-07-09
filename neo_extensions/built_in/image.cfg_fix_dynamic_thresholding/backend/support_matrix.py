from __future__ import annotations

EXTENSION_ID = "cfg_fix_dynamic_thresholding"
AVAILABLE = "available"
EXPERIMENTAL = "experimental_available"
PLANNED_GATED = "planned_gated"
PROVIDER_GATED = "provider_gated"
UNSUPPORTED = "unsupported"

SUPPORT_MATRIX: dict[tuple[str, str, str, str], str] = {
    ("comfyui", "sdxl", "checkpoint", "generate"): AVAILABLE,
    ("comfyui_portable", "sdxl", "checkpoint", "generate"): AVAILABLE,
    ("comfyui", "sd15", "checkpoint", "generate"): EXPERIMENTAL,
    ("comfyui_portable", "sd15", "checkpoint", "generate"): EXPERIMENTAL,
    ("comfyui", "flux", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui_portable", "flux", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui", "flux", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "flux", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "flux2_klein", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui_portable", "flux2_klein", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui", "flux2_klein", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "flux2_klein", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "qwen_image", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui_portable", "qwen_image", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui", "qwen_image", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "qwen_image", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "qwen_rapid_aio", "checkpoint_aio", "generate"): PLANNED_GATED,
    ("comfyui_portable", "qwen_rapid_aio", "checkpoint_aio", "generate"): PLANNED_GATED,
    ("comfyui", "qwen_rapid_aio", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "qwen_rapid_aio", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "qwen_image_edit_2509", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui_portable", "qwen_image_edit_2509", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui", "qwen_image_edit_2509", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "qwen_image_edit_2509", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "z_image", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui_portable", "z_image", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui", "z_image", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "z_image", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "z_image_turbo", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui_portable", "z_image_turbo", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui", "z_image_turbo", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "z_image_turbo", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "hidream", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui_portable", "hidream", "diffusion_model", "generate"): PLANNED_GATED,
    ("comfyui", "hidream", "gguf", "generate"): PLANNED_GATED,
    ("comfyui_portable", "hidream", "gguf", "generate"): PLANNED_GATED,
    ("comfyui", "wan_image", "diffusion_model", "generate"): PROVIDER_GATED,
    ("comfyui_portable", "wan_image", "diffusion_model", "generate"): PROVIDER_GATED,
    ("comfyui", "wan_image", "gguf", "generate"): PROVIDER_GATED,
    ("comfyui_portable", "wan_image", "gguf", "generate"): PROVIDER_GATED,
    ("comfyui", "hunyuan_image", "diffusion_model", "generate"): PROVIDER_GATED,
    ("comfyui_portable", "hunyuan_image", "diffusion_model", "generate"): PROVIDER_GATED,
    ("comfyui", "hunyuan_image", "gguf", "generate"): PROVIDER_GATED,
    ("comfyui_portable", "hunyuan_image", "gguf", "generate"): PROVIDER_GATED,
}

WORKSPACE_SUPPORT = {
    "generations": AVAILABLE,
    "assets": UNSUPPORTED,
    "reference": PLANNED_GATED,
    "finish": PLANNED_GATED,
    "results": UNSUPPORTED,
}

GATED_REASONS = {
    PLANNED_GATED: "Dynamic thresholding support for this route is declared for migration but not validated in Phase B.",
    PROVIDER_GATED: "The active provider/family route is gated before dynamic thresholding can safely patch a sampler model.",
    UNSUPPORTED: "This workspace or route has no sampler model graph for CFG Fix / Dynamic Thresholding to patch.",
}

def route_state(backend: str, family: str, loader: str, mode: str) -> str:
    return SUPPORT_MATRIX.get((backend, family, loader, mode), PLANNED_GATED)
