from __future__ import annotations

EXTENSION_ID = "image.high_res_lab"
EXTENSION_NAME = "Image · High-Res Lab"
EXTENSION_TYPE = "built_in"
WORKSPACE_APP = "finish"
MOUNT_SLOT = "image.finish.high_res_lab"
VERSION = 1
PHASE = "P8.5"
SKELETON_ONLY = False
PAYLOAD_RUNTIME_READY = True
WORKFLOW_PATCH_READY = True
VALIDATION_RUNTIME_READY = True

ACTIVE_ROUTE_STATES = {"available", "experimental_available"}
GATED_ROUTE_STATES = {"implementation_target", "planned_gated", "provider_gated"}
UNSUPPORTED_ROUTE_STATES = {"unsupported"}

REQUIRED_BASE_NODES = ["KSampler", "LatentUpscale", "ImageScale", "VAEEncode", "VAEDecode"]
MODE_REQUIRED_NODES = {
    "latent": ["KSampler", "LatentUpscale", "VAEEncode", "VAEDecode"],
    "image_upscale": ["KSampler", "ImageScale", "VAEEncode", "VAEDecode"],
}
ALLOWED_MODES = {"latent", "image_upscale"}
ALLOWED_STRATEGIES = {"standard", "forge_pixel_refine", "ultimate_sd_upscale", "upscale_only", "qwen_reedit"}
ALLOWED_RESIZE_METHODS = {"lanczos", "bicubic", "bilinear", "area", "nearest-exact"}
PROFILE_PRESETS = {
    "gentle_polish": {"mode": "image_upscale", "strategy": "forge_pixel_refine", "resize_method": "lanczos", "scale": 1.35, "steps": 12, "denoise": 0.22, "cfg": 5.0, "sampler": "", "scheduler": "", "tiled_vae": True, "tile_size": 512, "tile_overlap": 64, "upscaler": ""},
    "balanced_finish": {"mode": "image_upscale", "strategy": "forge_pixel_refine", "resize_method": "lanczos", "scale": 1.5, "steps": 16, "denoise": 0.28, "cfg": 5.0, "sampler": "", "scheduler": "", "tiled_vae": True, "tile_size": 512, "tile_overlap": 64, "upscaler": ""},
    "detail_push": {"mode": "image_upscale", "strategy": "forge_pixel_refine", "resize_method": "lanczos", "scale": 1.75, "steps": 22, "denoise": 0.36, "cfg": 4.8, "sampler": "", "scheduler": "", "tiled_vae": True, "tile_size": 512, "tile_overlap": 64, "upscaler": ""},
    "bigger_finish": {"mode": "image_upscale", "strategy": "forge_pixel_refine", "resize_method": "lanczos", "scale": 2.0, "steps": 24, "denoise": 0.34, "cfg": 4.8, "sampler": "", "scheduler": "", "tiled_vae": True, "tile_size": 640, "tile_overlap": 64, "upscaler": ""},
    "latent_rebuild": {"mode": "latent", "strategy": "standard", "resize_method": "lanczos", "scale": 1.5, "steps": 22, "denoise": 0.5, "cfg": 5.2, "sampler": "", "scheduler": "", "tiled_vae": True, "tile_size": 512, "tile_overlap": 64, "upscaler": ""},
    "upscale_only": {"mode": "image_upscale", "strategy": "upscale_only", "resize_method": "lanczos", "scale": 2.0, "steps": 4, "denoise": 0.05, "cfg": 1.0, "sampler": "", "scheduler": "", "tiled_vae": False, "tile_size": 512, "tile_overlap": 64, "upscaler": ""},
}
DEFAULT_PARAMS = {"profile": "balanced_finish", "strategy": "standard", **PROFILE_PRESETS["balanced_finish"]}
OPTIONAL_NODES = [
    "VAEDecodeTiled",
    "UpscaleModelLoader",
    "ImageUpscaleWithModel",
    "UltimateSDUpscale",
    "ControlNetLoader",
    "ControlNetApply",
]
OPTIONAL_NODE_GROUPS = {
    "tiled_vae_decode": ["VAEDecodeTiled"],
    "model_upscale": ["UpscaleModelLoader", "ImageUpscaleWithModel"],
    "ultimate_sd_upscale": ["UltimateSDUpscale"],
    "controlnet_preserve_hint": ["ControlNetLoader", "ControlNetApply"],
}
OPTIONAL_FEATURE_CONTROLS = {
    "tiled_vae_decode": ["tiled_vae", "tile_size", "tile_overlap"],
    "model_upscale": ["upscaler"],
    "ultimate_sd_upscale": ["strategy", "tile_size", "tile_overlap"],
    "controlnet_preserve_hint": [],
}

